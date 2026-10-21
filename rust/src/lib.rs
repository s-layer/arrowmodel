use arrow_array::ArrayRef;
use arrow_array::RecordBatch;
use arrow_schema::DataType;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use pyo3_arrow::PyRecordBatch;

type PyObject = Py<PyAny>;

mod construct;
mod extract;
mod stream;

/// `(col_index, field_name, nested_model_cls)`, as built by
/// `ArrowModelConverter._resolve_columns`.
pub(crate) type FieldSpec = (usize, String, Option<PyObject>);

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

/// The fast-path constructor for these fields, resolved once per conversion;
/// `None` for the validated path, which goes through `model_validate_json`.
pub(crate) fn make_constructor(
    py: Python<'_>,
    model_cls: &Bound<'_, PyAny>,
    field_specs: &[FieldSpec],
    validate: bool,
) -> PyResult<Option<construct::ModelConstructor>> {
    if validate {
        return Ok(None);
    }
    let field_names: Vec<&str> = field_specs
        .iter()
        .map(|(_, name, _)| name.as_str())
        .collect();
    Ok(Some(construct::ModelConstructor::new(
        py,
        model_cls,
        &field_names,
    )?))
}

/// Convert one record batch, appending an instance per row to `results`.
///
/// With a `constructor` (fast path) each row is built as
/// `model_cls.model_construct(**kwargs)` would build it, without calling the
/// Python-level `model_construct` (see construct.rs). Without one (validated
/// path) each row is serialised to JSON with serde_json and passed to
/// `model_cls.model_validate_json` (VALID-02), so invalid data raises Pydantic's
/// ValidationError (VALID-03).
pub(crate) fn convert_batch(
    py: Python<'_>,
    rb: &RecordBatch,
    model_cls: &Bound<'_, PyAny>,
    field_specs: &[FieldSpec],
    constructor: Option<&construct::ModelConstructor>,
    results: &mut Vec<PyObject>,
) -> PyResult<()> {
    let col_indices: Vec<usize> = field_specs.iter().map(|(idx, _, _)| *idx).collect();

    // Pre-unpack dictionary / run-end encoded columns before building extractors
    let schema = rb.schema();
    let unpacked = unpack_columns(rb.columns(), &schema, &col_indices)?;

    // Downcast columns once before the row loop
    let extractors: Vec<extract::ColumnExtractor<'_>> = unpacked
        .iter()
        .zip(field_specs)
        .map(|(col, (orig_idx, _, nested_model))| {
            let dt = schema.field(*orig_idx).data_type();
            // For dictionary columns, use the value type for the extractor
            let effective_dt = match dt {
                DataType::Dictionary(_, value_type) => value_type.as_ref(),
                DataType::RunEndEncoded(_, value_field) => value_field.data_type(),
                other => other,
            };
            extract::prepare_extractor(py, col.as_ref(), effective_dt, nested_model.as_ref())
        })
        .collect::<Result<_, _>>()?;

    match constructor {
        Some(constructor) => {
            for row in 0..rb.num_rows() {
                let values = extractors
                    .iter()
                    .map(|extractor| extractor.extract_value(py, row))
                    .collect::<PyResult<Vec<_>>>()?;
                results.push(constructor.build(py, values)?);
            }
        }
        None => {
            let validate_json = model_cls.getattr(pyo3::intern!(py, "model_validate_json"))?;
            for row in 0..rb.num_rows() {
                let mut map = serde_json::Map::new();
                for (extractor, (_, field_name, _)) in extractors.iter().zip(field_specs) {
                    map.insert(field_name.clone(), extractor.extract_json_value(py, row)?);
                }
                let json_bytes = serde_json::to_vec(&map).map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                        "JSON serialization failed: {e}"
                    ))
                })?;
                let instance = validate_json.call1((PyBytes::new(py, &json_bytes),))?;
                results.push(instance.unbind());
            }
        }
    }
    Ok(())
}

#[pymodule(name = "_core")]
mod _core {
    use super::*;

    #[pymodule_export]
    use super::stream::{arrow_schema_fields, ArrowInput};

    /// Return (num_rows, num_columns) from an Arrow RecordBatch.
    /// Accepts any PyCapsule-compatible input (pyarrow, polars, nanoarrow).
    #[pyfunction]
    fn record_batch_info(batch: PyRecordBatch) -> PyResult<(usize, usize)> {
        let rb = batch.into_inner();
        Ok((rb.num_rows(), rb.num_columns()))
    }
}
