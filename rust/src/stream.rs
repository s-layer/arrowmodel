use std::sync::Mutex;

use arrow_array::{RecordBatch, RecordBatchIterator, RecordBatchReader};
use arrow_schema::{DataType, SchemaRef};
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyList;
use pyo3_arrow::{PyRecordBatch, PyRecordBatchReader, PySchema};

use crate::{convert_batch, FieldSpec};

type PyObject = Py<PyAny>;

/// The error for input that is not a record batch or a stream of record batches.
fn unsupported_input(data: &Bound<'_, PyAny>, detail: Option<String>) -> PyErr {
    let ty = data.get_type();
    let name = ty
        .fully_qualified_name()
        .map(|n| n.to_string())
        .unwrap_or_else(|_| "<unknown>".to_owned());
    let mut msg = format!(
        "arrowmodel converts a RecordBatch or Table (or any Arrow stream of record batches, \
         such as a RecordBatchReader or a Polars DataFrame); got {name}."
    );
    if let Some(detail) = detail {
        msg.push_str(&format!(" ({detail})"));
    }
    PyTypeError::new_err(msg)
}

/// `(name, is_map)` for each top-level field, in column order.
fn schema_fields(schema: &SchemaRef) -> Vec<(String, bool)> {
    schema
        .fields()
        .iter()
        .map(|f| {
            (
                f.name().clone(),
                matches!(f.data_type(), DataType::Map(_, _)),
            )
        })
        .collect()
}

/// `(name, is_map)` for each field of any object exporting `__arrow_c_schema__`
/// (a pyarrow Schema, or anything implementing the Arrow PyCapsule Interface).
#[pyfunction]
pub fn arrow_schema_fields(schema: PySchema) -> Vec<(String, bool)> {
    schema_fields(&schema.into_inner())
}

/// One pass over Arrow input: a single RecordBatch (`__arrow_c_array__`) or a
/// stream of them (`__arrow_c_stream__`: a pyarrow Table or RecordBatchReader,
/// a Polars DataFrame, a nanoarrow stream, ...).
///
/// The stream is opened once, so a one-shot source such as a RecordBatchReader
/// is read exactly once: its schema here, then its batches via `convert_next`
/// (lazily, for `iter()`) or `convert_all`.
///
/// Sendable, because an `iter()` generator holds one across `next()` calls,
/// and those can run on different threads (e.g. Starlette's threadpool for a
/// StreamingResponse). The reader is `Send` but not `Sync`, hence the Mutex;
/// it is only reached through `&mut self`, so it is never actually locked.
#[pyclass]
pub struct ArrowInput {
    reader: Mutex<Option<Box<dyn RecordBatchReader + Send>>>,
    schema: SchemaRef,
}

impl ArrowInput {
    fn next_batch(&mut self) -> PyResult<Option<RecordBatch>> {
        let reader = self
            .reader
            .get_mut()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        match reader.as_mut().and_then(|r| r.next()) {
            None => {
                *reader = None;
                Ok(None)
            }
            Some(Ok(batch)) => Ok(Some(batch)),
            Some(Err(e)) => Err(PyValueError::new_err(format!(
                "Failed to read the next Arrow record batch: {e}"
            ))),
        }
    }
}

#[pymethods]
impl ArrowInput {
    #[new]
    fn new(data: &Bound<'_, PyAny>) -> PyResult<Self> {
        let py = data.py();
        // A RecordBatch exports both protocols; read it as one batch. A plain
        // Array also has `__arrow_c_array__`, but it is not a struct of columns.
        if data.hasattr(pyo3::intern!(py, "__arrow_c_array__"))? {
            if let Ok(batch) = data.extract::<PyRecordBatch>() {
                let batch = batch.into_inner();
                let schema = batch.schema();
                let reader = RecordBatchIterator::new(vec![Ok(batch)], schema.clone());
                return Ok(ArrowInput {
                    reader: Mutex::new(Some(Box::new(reader))),
                    schema,
                });
            }
        }
        if data.hasattr(pyo3::intern!(py, "__arrow_c_stream__"))? {
            let reader = data
                .extract::<PyRecordBatchReader>()
                .map_err(|e| unsupported_input(data, Some(e.to_string())))?
                .into_reader()?;
            let schema = reader.schema();
            return Ok(ArrowInput {
                reader: Mutex::new(Some(reader)),
                schema,
            });
        }
        Err(unsupported_input(data, None))
    }

    /// `(name, is_map)` for each column, in column order.
    fn fields(&self) -> Vec<(String, bool)> {
        schema_fields(&self.schema)
    }

    /// Convert the next record batch, or return None when the input is exhausted.
    fn convert_next(
        &mut self,
        py: Python<'_>,
        model_cls: Bound<'_, PyAny>,
        field_specs: Vec<FieldSpec>,
        validate: bool,
    ) -> PyResult<Option<PyObject>> {
        let Some(batch) = self.next_batch()? else {
            return Ok(None);
        };
        let mut results = Vec::with_capacity(batch.num_rows());
        let constructor = crate::make_constructor(py, &model_cls, &field_specs, validate)?;
        convert_batch(
            py,
            &batch,
            &model_cls,
            &field_specs,
            constructor.as_ref(),
            &mut results,
        )?;
        Ok(Some(PyList::new(py, &results)?.into_any().unbind()))
    }

    /// Convert every remaining record batch into one list.
    fn convert_all(
        &mut self,
        py: Python<'_>,
        model_cls: Bound<'_, PyAny>,
        field_specs: Vec<FieldSpec>,
        validate: bool,
    ) -> PyResult<PyObject> {
        // Resolve the constructor once and reuse it across all batches.
        let constructor = crate::make_constructor(py, &model_cls, &field_specs, validate)?;
        let mut results = Vec::new();
        while let Some(batch) = self.next_batch()? {
            results.reserve(batch.num_rows());
            convert_batch(
                py,
                &batch,
                &model_cls,
                &field_specs,
                constructor.as_ref(),
                &mut results,
            )?;
        }
        Ok(PyList::new(py, &results)?.into_any().unbind())
    }
}
