use std::sync::Mutex;

use arrow_array::{Array, RecordBatch, StructArray};
use arrow_schema::{DataType, Schema, SchemaRef};
use pyo3::exceptions::{PyTypeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyList;
use pyo3_arrow::ffi::{ArrayIterator, ArrayReader};
use pyo3_arrow::{PyArray, PyArrayReader, PySchema};

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

/// One pass over Arrow input: a single RecordBatch or struct array
/// (`__arrow_c_array__`) or a stream of them (`__arrow_c_stream__`: a pyarrow
/// Table, RecordBatchReader or struct ChunkedArray, a Polars DataFrame, a
/// nanoarrow stream, ...).
///
/// The stream is opened once, so a one-shot source such as a RecordBatchReader
/// is read exactly once: its schema here, then its batches via `convert_next`
/// (lazily, for `iter()`) or `convert_all`.
///
/// Both protocols carry record batches as struct arrays. They are read as
/// arrays rather than through pyo3-arrow's RecordBatch import, which panics on
/// a struct array with null rows (or, for streams, drops the nulls), so that a
/// null row raises a ValueError instead.
///
/// Sendable, because an `iter()` generator holds one across `next()` calls,
/// and those can run on different threads (e.g. Starlette's threadpool for a
/// StreamingResponse). The reader is `Send` but not `Sync`, hence the Mutex;
/// it is only reached through `&mut self`, so it is never actually locked.
#[pyclass]
pub struct ArrowInput {
    reader: Mutex<Option<Box<dyn ArrayReader + Send>>>,
    schema: SchemaRef,
}

impl ArrowInput {
    fn from_reader(data: &Bound<'_, PyAny>, reader: Box<dyn ArrayReader + Send>) -> PyResult<Self> {
        let DataType::Struct(fields) = reader.field().data_type().clone() else {
            return Err(unsupported_input(data, None));
        };
        Ok(ArrowInput {
            reader: Mutex::new(Some(reader)),
            schema: SchemaRef::new(Schema::new(fields)),
        })
    }

    /// The next record batch. The read itself runs without the GIL: for a lazy
    /// reader (an ADBC cursor, a Flight stream) that is where the I/O happens.
    /// A reader backed by Python code takes the GIL again itself.
    fn next_batch(&mut self, py: Python<'_>) -> PyResult<Option<RecordBatch>> {
        let reader = self
            .reader
            .get_mut()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let next = match reader.as_mut() {
            Some(r) => py.detach(|| r.next()),
            None => None,
        };
        let array = match next {
            None => {
                *reader = None;
                return Ok(None);
            }
            Some(Ok(array)) => array,
            Some(Err(e)) => {
                return Err(PyValueError::new_err(format!(
                    "Failed to read the next Arrow record batch: {e}"
                )))
            }
        };
        let Some(struct_array) = array.as_any().downcast_ref::<StructArray>() else {
            return Err(PyValueError::new_err(
                "Arrow stream yielded a non-struct array; expected record batches",
            ));
        };
        if struct_array.null_count() > 0 {
            return Err(PyValueError::new_err(format!(
                "Cannot convert a struct array with null rows ({} of {} rows are null): \
                 a null row has no field values to build a model from. Filter them out first.",
                struct_array.null_count(),
                struct_array.len()
            )));
        }
        Ok(Some(RecordBatch::from(struct_array.clone())))
    }
}

#[pymethods]
impl ArrowInput {
    #[new]
    fn new(data: &Bound<'_, PyAny>) -> PyResult<Self> {
        let py = data.py();
        // A RecordBatch exports both protocols; read it as one batch. If the
        // array export fails (a multi-chunk nanoarrow Array refuses it), fall
        // through to the stream when there is one.
        let mut array_error = None;
        if data.hasattr(pyo3::intern!(py, "__arrow_c_array__"))? {
            match data.extract::<PyArray>() {
                Ok(array) => {
                    let (array, field) = array.into_inner();
                    let reader = ArrayIterator::new(vec![Ok(array)], field);
                    return Self::from_reader(data, Box::new(reader));
                }
                Err(e) => array_error = Some(e.to_string()),
            }
        }
        if data.hasattr(pyo3::intern!(py, "__arrow_c_stream__"))? {
            let reader = data
                .extract::<PyArrayReader>()
                .map_err(|e| unsupported_input(data, Some(e.to_string())))?
                .into_reader()?;
            return Self::from_reader(data, reader);
        }
        if array_error.is_some() {
            return Err(unsupported_input(data, array_error));
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
        let Some(batch) = self.next_batch(py)? else {
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
        while let Some(batch) = self.next_batch(py)? {
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
