use arrow_array::ArrayRef;
use arrow_schema::DataType;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList};
use pyo3_arrow::{PyRecordBatch, PyTable};

type PyObject = Py<PyAny>;

mod construct;
mod extract;

/// Unpack dictionary-encoded columns to their value type arrays.
/// Non-dictionary columns are returned as-is (cloned Arc reference).
/// This resolves the lifetime issue: owned unpacked arrays live in the
/// returned Vec, and extractors borrow from them.
fn unpack_columns(
    columns: &[ArrayRef],
    schema: &arrow_schema::SchemaRef,
    col_indices: &[usize],
) -> Result<Vec<ArrayRef>, PyErr> {
    col_indices
        .iter()
        .map(|&idx| {
            let col = &columns[idx];
            let dt = schema.field(idx).data_type();
            match dt {
                DataType::Dictionary(_, value_type) => {
                    arrow_cast::cast(col.as_ref(), value_type.as_ref()).map_err(|e| {
                        PyErr::new::<pyo3::exceptions::PyTypeError, _>(format!(
                            "Failed to unpack dictionary array: {e}"
                        ))
                    })
                }
                DataType::RunEndEncoded(_, value_field) => {
                    arrow_cast::cast(col.as_ref(), value_field.data_type()).map_err(|e| {
                        PyErr::new::<pyo3::exceptions::PyTypeError, _>(format!(
                            "Failed to unpack run-end encoded array: {e}"
                        ))
                    })
                }
                _ => Ok(col.clone()),
            }
        })
        .collect()
}

#[pymodule(name = "_core")]
mod _core {
    use super::*;

    /// Return (num_rows, num_columns) from an Arrow RecordBatch.
    /// Accepts any PyCapsule-compatible input (pyarrow, polars, nanoarrow).
    #[pyfunction]
    fn record_batch_info(batch: PyRecordBatch) -> PyResult<(usize, usize)> {
        let rb = batch.into_inner();
        Ok((rb.num_rows(), rb.num_columns()))
    }

    /// Convert an Arrow RecordBatch to a list of Pydantic model instances.
    ///
    /// For each row, builds the same instance as
    /// model_cls.model_construct(**kwargs) -- no Pydantic validation -- without
    /// calling the Python-level model_construct per row (see construct.rs).
    ///
    /// Arguments:
    ///   - batch: Arrow RecordBatch (via PyCapsule / C Data Interface)
    ///   - model_cls: Pydantic model class (e.g. MyModel)
    ///   - field_specs: list of (col_index, field_name, Optional[nested_model_cls])
    #[pyfunction]
    fn convert_record_batch(
        py: Python<'_>,
        batch: PyRecordBatch,
        model_cls: Bound<'_, PyAny>,
        field_specs: Vec<(usize, String, Option<PyObject>)>,
    ) -> PyResult<PyObject> {
        let rb = batch.into_inner();
        let num_rows = rb.num_rows();

        // Decompose field_specs into parallel vectors
        let col_indices: Vec<usize> = field_specs.iter().map(|(idx, _, _)| *idx).collect();
        let field_names: Vec<&str> = field_specs
            .iter()
            .map(|(_, name, _)| name.as_str())
            .collect();
        let nested_models: Vec<&Option<PyObject>> =
            field_specs.iter().map(|(_, _, model)| model).collect();

        // Resolve how to build instances from these fields once, not per row.
        let constructor = construct::ModelConstructor::new(py, &model_cls, &field_names)?;

        // Pre-unpack dictionary columns before building extractors
        let schema = rb.schema();
        let unpacked = unpack_columns(rb.columns(), &schema, &col_indices)?;

        // Pattern 2: Downcast columns once before the row loop
        // Use unpacked columns (dictionary columns are already decoded)
        let extractors: Vec<extract::ColumnExtractor<'_>> = unpacked
            .iter()
            .enumerate()
            .map(|(i, col)| {
                let orig_idx = col_indices[i];
                let dt = schema.field(orig_idx).data_type();
                // For dictionary columns, use the value type for the extractor
                let effective_dt = match dt {
                    DataType::Dictionary(_, value_type) => value_type.as_ref(),
                    DataType::RunEndEncoded(_, value_field) => value_field.data_type(),
                    other => other,
                };
                extract::prepare_extractor(
                    py,
                    col.as_ref(),
                    effective_dt,
                    nested_models[i].as_ref(),
                )
            })
            .collect::<Result<_, _>>()?;

        // Pitfall 5: Pre-allocate result Vec
        let mut results: Vec<PyObject> = Vec::with_capacity(num_rows);

        // Row loop: extract the row's values, build the instance
        for row in 0..num_rows {
            let values = extractors
                .iter()
                .map(|extractor| extractor.extract_value(py, row))
                .collect::<PyResult<Vec<_>>>()?;
            results.push(constructor.build(py, values)?);
        }

        // Convert Vec to PyList and return
        let py_list = PyList::new(py, &results)?;
        Ok(py_list.into_any().unbind())
    }

    /// Convert an Arrow Table to a list of Pydantic model instances.
    ///
    /// Iterates over all RecordBatches in the Table, using the shared
    /// Table schema to resolve column indices once. Field name strings
    /// are interned once and reused across all batches (FAST-02).
    #[pyfunction]
    fn convert_table(
        py: Python<'_>,
        table: PyTable,
        model_cls: Bound<'_, PyAny>,
        field_specs: Vec<(usize, String, Option<PyObject>)>,
    ) -> PyResult<PyObject> {
        let (batches, _schema) = table.into_inner();

        // Decompose field_specs into parallel vectors
        let col_indices: Vec<usize> = field_specs.iter().map(|(idx, _, _)| *idx).collect();
        let field_names: Vec<&str> = field_specs
            .iter()
            .map(|(_, name, _)| name.as_str())
            .collect();
        let nested_models: Vec<&Option<PyObject>> =
            field_specs.iter().map(|(_, _, model)| model).collect();

        // Resolve how to build instances from these fields once, reused across
        // ALL batches.
        let constructor = construct::ModelConstructor::new(py, &model_cls, &field_names)?;

        // Pre-allocate for total rows across all batches
        let total_rows: usize = batches.iter().map(|b| b.num_rows()).sum();
        let mut results: Vec<PyObject> = Vec::with_capacity(total_rows);

        for rb in &batches {
            let schema = rb.schema();

            // Pre-unpack dictionary columns for this batch
            let unpacked = unpack_columns(rb.columns(), &schema, &col_indices)?;

            let extractors: Vec<extract::ColumnExtractor<'_>> = unpacked
                .iter()
                .enumerate()
                .map(|(i, col)| {
                    let orig_idx = col_indices[i];
                    let dt = schema.field(orig_idx).data_type();
                    let effective_dt = match dt {
                        DataType::Dictionary(_, value_type) => value_type.as_ref(),
                        DataType::RunEndEncoded(_, value_field) => value_field.data_type(),
                        other => other,
                    };
                    extract::prepare_extractor(
                        py,
                        col.as_ref(),
                        effective_dt,
                        nested_models[i].as_ref(),
                    )
                })
                .collect::<Result<_, _>>()?;

            for row in 0..rb.num_rows() {
                let values = extractors
                    .iter()
                    .map(|extractor| extractor.extract_value(py, row))
                    .collect::<PyResult<Vec<_>>>()?;
                results.push(constructor.build(py, values)?);
            }
        }

        let py_list = PyList::new(py, &results)?;
        Ok(py_list.into_any().unbind())
    }

    /// Convert an Arrow RecordBatch to a list of validated Pydantic model instances.
    ///
    /// For each row, builds a serde_json::Map from extracted column values,
    /// serializes to JSON bytes, and calls model_cls.model_validate_json(bytes)
    /// for full Pydantic validation (type coercion, constraints, custom validators).
    ///
    /// VALID-02: serde_json row serialization -> model_validate_json
    /// VALID-03: Invalid data raises Pydantic ValidationError
    #[pyfunction]
    fn convert_record_batch_validated(
        py: Python<'_>,
        batch: PyRecordBatch,
        model_cls: Bound<'_, PyAny>,
        field_specs: Vec<(usize, String, Option<PyObject>)>,
    ) -> PyResult<PyObject> {
        let rb = batch.into_inner();
        let num_rows = rb.num_rows();

        // Decompose field_specs into parallel vectors
        let col_indices: Vec<usize> = field_specs.iter().map(|(idx, _, _)| *idx).collect();
        let field_names: Vec<&str> = field_specs
            .iter()
            .map(|(_, name, _)| name.as_str())
            .collect();
        let nested_models: Vec<&Option<PyObject>> =
            field_specs.iter().map(|(_, _, model)| model).collect();

        // Pre-unpack dictionary columns before building extractors
        let schema = rb.schema();
        let unpacked = unpack_columns(rb.columns(), &schema, &col_indices)?;

        // Downcast columns once before the row loop
        let extractors: Vec<extract::ColumnExtractor<'_>> = unpacked
            .iter()
            .enumerate()
            .map(|(i, col)| {
                let orig_idx = col_indices[i];
                let dt = schema.field(orig_idx).data_type();
                let effective_dt = match dt {
                    DataType::Dictionary(_, value_type) => value_type.as_ref(),
                    DataType::RunEndEncoded(_, value_field) => value_field.data_type(),
                    other => other,
                };
                extract::prepare_extractor(
                    py,
                    col.as_ref(),
                    effective_dt,
                    nested_models[i].as_ref(),
                )
            })
            .collect::<Result<_, _>>()?;

        let mut results: Vec<PyObject> = Vec::with_capacity(num_rows);

        for row in 0..num_rows {
            let mut map = serde_json::Map::new();
            for (extractor, field_name) in extractors.iter().zip(field_names.iter()) {
                let json_val = extractor.extract_json_value(py, row)?;
                map.insert(field_name.to_string(), json_val);
            }
            let json_bytes = serde_json::to_vec(&map).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                    "JSON serialization failed: {e}"
                ))
            })?;
            let py_bytes = PyBytes::new(py, &json_bytes);
            let instance = model_cls.call_method1("model_validate_json", (py_bytes,))?;
            results.push(instance.unbind());
        }

        let py_list = PyList::new(py, &results)?;
        Ok(py_list.into_any().unbind())
    }

    /// Convert an Arrow Table to a list of validated Pydantic model instances.
    ///
    /// Iterates over all RecordBatches in the Table, serializing each row to
    /// JSON bytes and calling model_validate_json for full Pydantic validation.
    #[pyfunction]
    fn convert_table_validated(
        py: Python<'_>,
        table: PyTable,
        model_cls: Bound<'_, PyAny>,
        field_specs: Vec<(usize, String, Option<PyObject>)>,
    ) -> PyResult<PyObject> {
        let (batches, _schema) = table.into_inner();

        // Decompose field_specs into parallel vectors
        let col_indices: Vec<usize> = field_specs.iter().map(|(idx, _, _)| *idx).collect();
        let field_names: Vec<&str> = field_specs
            .iter()
            .map(|(_, name, _)| name.as_str())
            .collect();
        let nested_models: Vec<&Option<PyObject>> =
            field_specs.iter().map(|(_, _, model)| model).collect();

        // Pre-allocate for total rows across all batches
        let total_rows: usize = batches.iter().map(|b| b.num_rows()).sum();
        let mut results: Vec<PyObject> = Vec::with_capacity(total_rows);

        for rb in &batches {
            let schema = rb.schema();

            // Pre-unpack dictionary columns for this batch
            let unpacked = unpack_columns(rb.columns(), &schema, &col_indices)?;

            let extractors: Vec<extract::ColumnExtractor<'_>> = unpacked
                .iter()
                .enumerate()
                .map(|(i, col)| {
                    let orig_idx = col_indices[i];
                    let dt = schema.field(orig_idx).data_type();
                    let effective_dt = match dt {
                        DataType::Dictionary(_, value_type) => value_type.as_ref(),
                        DataType::RunEndEncoded(_, value_field) => value_field.data_type(),
                        other => other,
                    };
                    extract::prepare_extractor(
                        py,
                        col.as_ref(),
                        effective_dt,
                        nested_models[i].as_ref(),
                    )
                })
                .collect::<Result<_, _>>()?;

            for row in 0..rb.num_rows() {
                let mut map = serde_json::Map::new();
                for (extractor, field_name) in extractors.iter().zip(field_names.iter()) {
                    let json_val = extractor.extract_json_value(py, row)?;
                    map.insert(field_name.to_string(), json_val);
                }
                let json_bytes = serde_json::to_vec(&map).map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                        "JSON serialization failed: {e}"
                    ))
                })?;
                let py_bytes = PyBytes::new(py, &json_bytes);
                let instance = model_cls.call_method1("model_validate_json", (py_bytes,))?;
                results.push(instance.unbind());
            }
        }

        let py_list = PyList::new(py, &results)?;
        Ok(py_list.into_any().unbind())
    }
}
