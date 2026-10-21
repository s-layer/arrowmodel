use arrow_array::{
    cast::{
        as_boolean_array, as_fixed_size_list_array, as_large_list_array, as_largestring_array,
        as_list_array, as_map_array, as_primitive_array, as_string_array, as_struct_array,
        as_union_array,
    },
    types::{
        Date32Type, Date64Type, DurationMicrosecondType, DurationMillisecondType,
        DurationNanosecondType, DurationSecondType, Float16Type, Float32Type, Float64Type,
        Int16Type, Int32Type, Int64Type, Int8Type, IntervalDayTimeType, IntervalMonthDayNanoType,
        IntervalYearMonthType, Time32MillisecondType, Time32SecondType, Time64MicrosecondType,
        Time64NanosecondType, TimestampMicrosecondType, TimestampMillisecondType,
        TimestampNanosecondType, TimestampSecondType, UInt16Type, UInt32Type, UInt64Type,
        UInt8Type,
    },
    Array, BinaryArray, BinaryViewArray, BooleanArray, FixedSizeBinaryArray, FixedSizeListArray,
    LargeBinaryArray, LargeListArray, LargeStringArray, ListArray, MapArray, StringArray,
    StringViewArray, StructArray, UnionArray,
};
use arrow_schema::{DataType, IntervalUnit, TimeUnit};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList, PyString, PyTime, PyTuple};
use serde_json::{Map, Number, Value};
use std::sync::Arc;

use crate::construct::{ModelConstructor, SharedConstructor};

type PyObject = Py<PyAny>;

/// Column extractor enum -- one variant per supported Arrow type.
/// Each variant holds a reference to the pre-downcast typed array,
/// avoiding dynamic dispatch inside the row loop.
pub enum ColumnExtractor<'a> {
    Int8(&'a arrow_array::Int8Array),
    Int16(&'a arrow_array::Int16Array),
    Int32(&'a arrow_array::Int32Array),
    Int64(&'a arrow_array::Int64Array),
    UInt8(&'a arrow_array::UInt8Array),
    UInt16(&'a arrow_array::UInt16Array),
    UInt32(&'a arrow_array::UInt32Array),
    UInt64(&'a arrow_array::UInt64Array),
    Float32(&'a arrow_array::Float32Array),
    Float64(&'a arrow_array::Float64Array),
    Boolean(&'a BooleanArray),
    Utf8(&'a StringArray),
    LargeUtf8(&'a LargeStringArray),
    // Temporal types
    Date32(&'a arrow_array::Date32Array),
    TimestampNaive(&'a dyn Array, TimeUnit),
    TimestampAware(&'a dyn Array, TimeUnit, PyObject),
    Duration(&'a dyn Array, TimeUnit),
    // Complex types
    /// List element extraction: stores child DataType to create temporary
    /// extractors per row's sub-array (ListArray.value(i) returns new ArrayRef).
    /// The `ModelPlan` is the element's pre-resolved nested-model plan (built
    /// once per column), threaded to the per-row child extractor so a
    /// `list[NestedModel]` (Arrow `List(Struct)`) can build model instances
    /// without any per-row Python introspection. `ModelPlan::None` for lists of
    /// scalars; nested for nested lists (`list[list[..]]`).
    List(&'a ListArray, DataType, ModelPlan),
    /// LargeList: identical to List but uses i64 offsets.
    LargeList(&'a LargeListArray, DataType, ModelPlan),
    /// Struct: child extractors (pre-built), interned field names, nested model class.
    Struct(
        &'a StructArray,
        Vec<(Py<PyString>, ColumnExtractor<'a>)>,
        SharedConstructor, // builds the nested Pydantic model instances
    ),
    // Extended scalar types
    Float16(&'a arrow_array::Float16Array),
    // Decimal variants cache the Python `decimal.Decimal` class (looked up once
    // in prepare_extractor) so the row loop avoids a per-row module import.
    Decimal128(&'a arrow_array::Decimal128Array, PyObject),
    Decimal256(&'a arrow_array::Decimal256Array, PyObject),
    Decimal32(&'a arrow_array::Decimal32Array, PyObject),
    Decimal64(&'a arrow_array::Decimal64Array, PyObject),
    // Extended temporal types
    Date64(&'a arrow_array::Date64Array),
    Time32(&'a dyn Array, TimeUnit),
    Time64(&'a dyn Array, TimeUnit),
    // Binary types
    Binary(&'a BinaryArray),
    LargeBinary(&'a LargeBinaryArray),
    FixedSizeBinary(&'a FixedSizeBinaryArray),
    // View types
    Utf8View(&'a StringViewArray),
    BinaryView(&'a BinaryViewArray),
    // Interval types
    IntervalYearMonth(&'a arrow_array::IntervalYearMonthArray),
    IntervalDayTime(&'a arrow_array::IntervalDayTimeArray),
    IntervalMonthDayNano(&'a arrow_array::IntervalMonthDayNanoArray),
    // Container types
    // The trailing `ModelPlan`(s) are the child elements' pre-resolved
    // nested-model plans (built once per column), threaded to the per-row child
    // extractor (as for List). Map carries a separate key-plan and value-plan.
    FixedSizeList(&'a FixedSizeListArray, DataType, ModelPlan),
    Map(&'a MapArray, DataType, DataType, ModelPlan, ModelPlan),
    // Union type
    Union(&'a UnionArray, Vec<(i8, DataType)>),
    // Null type -- always returns None
    Null,
}

/// Look up the `decimal.Decimal` class once, to be cached in a decimal
/// extractor variant and reused across all rows of the column.
fn import_decimal_cls(py: Python<'_>) -> PyResult<PyObject> {
    Ok(py.import("decimal")?.getattr("Decimal")?.unbind())
}

/// Pre-resolved nested Pydantic model plan for a column subtree.
///
/// Built once per column by [`build_model_plan`] and threaded (by reference)
/// through the extractor construction. This mirrors the Arrow `DataType` tree so
/// the per-row rebuild of container child extractors (List/Map over a Struct)
/// does **no** Python introspection: the model class for each Struct and the
/// per-field child plans are resolved a single time up front, not once per row.
///
/// Cloning is cheap (no Python calls): each Struct's constructor is shared by
/// `Arc`. Used when an owned child plan must be stored inside a per-row-built
/// container extractor.
#[derive(Clone)]
pub enum ModelPlan {
    /// No nested model anywhere in this subtree (scalar columns, unions, etc.).
    None,
    /// A Struct: its model constructor plus a per-field child plan, in Arrow
    /// field order (aligned positionally with `DataType::Struct(fields)`).
    Struct {
        constructor: SharedConstructor,
        children: Vec<ModelPlan>,
    },
    /// A single-child container (List / LargeList / FixedSizeList): the element
    /// plan.
    List(Box<ModelPlan>),
    /// A Map: the key plan and the value plan.
    Map(Box<ModelPlan>, Box<ModelPlan>),
}

/// Build the [`ModelPlan`] for a column subtree once, resolving the nested
/// Pydantic model class for every Struct via a single walk. This is the only
/// place that calls into Python (`arrowmodel._get_nested_model`); the row loop
/// then reuses the result.
///
/// `nested_model` is the model threaded in from the enclosing annotation (the
/// leaf model for `list[Model]`, `Some(Model)`; `None` for scalar columns).
fn build_model_plan(
    py: Python<'_>,
    data_type: &DataType,
    nested_model: Option<&PyObject>,
) -> PyResult<ModelPlan> {
    match data_type {
        DataType::Struct(fields) => {
            let model_cls = nested_model
                .ok_or_else(|| struct_no_model_err(fields))?
                .clone_ref(py);

            // Introspect the nested model class once to resolve each field's
            // child model (import + attribute access happen a single time here,
            // not per row).
            let arrowmodel = py.import("arrowmodel")?;
            let get_nested_model_fn = arrowmodel.getattr("_get_nested_model")?;
            let model_fields = model_cls.bind(py).getattr("model_fields")?;

            let mut children: Vec<ModelPlan> = Vec::with_capacity(fields.len());
            for field in fields.iter() {
                let child_nested_model: Option<PyObject> =
                    if let Ok(field_info) = model_fields.get_item(field.name()) {
                        let annotation = field_info.getattr("annotation")?;
                        let result = get_nested_model_fn.call1((annotation,))?;
                        if result.is_none() {
                            None
                        } else {
                            Some(result.unbind())
                        }
                    } else {
                        None
                    };
                children.push(build_model_plan(
                    py,
                    field.data_type(),
                    child_nested_model.as_ref(),
                )?);
            }
            // Resolve how to build instances from this Struct's fields once.
            let keys: Vec<&str> = fields.iter().map(|f| f.name().as_str()).collect();
            let constructor = Arc::new(ModelConstructor::new(py, model_cls.bind(py), &keys)?);
            Ok(ModelPlan::Struct {
                constructor,
                children,
            })
        }
        DataType::List(field) | DataType::LargeList(field) | DataType::FixedSizeList(field, _) => {
            // The leaf model threads through container layers unchanged; it is
            // only consumed where a child resolves to a Struct.
            Ok(ModelPlan::List(Box::new(build_model_plan(
                py,
                field.data_type(),
                nested_model,
            )?)))
        }
        DataType::Map(entries_field, _sorted) => {
            if let DataType::Struct(fields) = entries_field.data_type() {
                let key_plan = build_model_plan(py, fields[0].data_type(), nested_model)?;
                let val_plan = build_model_plan(py, fields[1].data_type(), nested_model)?;
                Ok(ModelPlan::Map(Box::new(key_plan), Box::new(val_plan)))
            } else {
                // The malformed-Map error is raised by prepare_extractor itself;
                // there is no model to resolve here.
                Ok(ModelPlan::None)
            }
        }
        // Scalars, temporal, binary, union, null, etc. carry no nested model.
        _ => Ok(ModelPlan::None),
    }
}

/// Owned element plan for a single-child container (List/LargeList/
/// FixedSizeList). `ModelPlan::None` when the plan carries no nested model.
fn list_element_plan(plan: &ModelPlan) -> ModelPlan {
    match plan {
        ModelPlan::List(inner) => inner.as_ref().clone(),
        _ => ModelPlan::None,
    }
}

/// Owned (key, value) plans for a Map. `(None, None)` when the plan carries no
/// nested model.
fn map_kv_plans(plan: &ModelPlan) -> (ModelPlan, ModelPlan) {
    match plan {
        ModelPlan::Map(k, v) => (k.as_ref().clone(), v.as_ref().clone()),
        _ => (ModelPlan::None, ModelPlan::None),
    }
}

/// The error raised when an Arrow Struct column has no matching Pydantic model.
fn struct_no_model_err(fields: &arrow_schema::Fields) -> PyErr {
    let field_names: Vec<&str> = fields.iter().map(|f| f.name().as_str()).collect();
    PyErr::new::<pyo3::exceptions::PyTypeError, _>(format!(
        "Arrow Struct column (fields: {field_names:?}) has no matching \
         Pydantic model. Annotate the field with a BaseModel subclass, \
         or a container of one (e.g. `MyModel`, `MyModel | None`, \
         `list[MyModel]`), so its fields can be constructed."
    ))
}

/// Downcast an Arrow column to a concrete typed array once, before the row loop.
/// Returns a ColumnExtractor variant for efficient per-row value extraction.
///
/// Dictionary columns should be pre-unpacked before calling this function.
/// See `unpack_dictionary_columns` in lib.rs.
///
/// `nested_model` is `Some(model_cls)` when the column is a Struct that should
/// produce a nested Pydantic model instance. `None` for all other column types.
///
/// This is the public entry point used per column. It resolves the nested-model
/// plan once (the only Python introspection) and delegates to
/// [`prepare_extractor_with_plan`]; per-row container child extractors reuse the
/// pre-built plan and never re-enter Python.
pub fn prepare_extractor<'a>(
    py: Python<'_>,
    col: &'a dyn Array,
    data_type: &DataType,
    nested_model: Option<&PyObject>,
) -> PyResult<ColumnExtractor<'a>> {
    let plan = build_model_plan(py, data_type, nested_model)?;
    prepare_extractor_with_plan(py, col, data_type, &plan)
}

/// Build a [`ColumnExtractor`] using a pre-resolved [`ModelPlan`]. Called
/// recursively (Struct children) and per row (container children) without any
/// Python introspection — all model lookups are read from `plan`.
fn prepare_extractor_with_plan<'a>(
    py: Python<'_>,
    col: &'a dyn Array,
    data_type: &DataType,
    plan: &ModelPlan,
) -> PyResult<ColumnExtractor<'a>> {
    match data_type {
        DataType::Int8 => Ok(ColumnExtractor::Int8(as_primitive_array::<Int8Type>(col))),
        DataType::Int16 => Ok(ColumnExtractor::Int16(as_primitive_array::<Int16Type>(col))),
        DataType::Int32 => Ok(ColumnExtractor::Int32(as_primitive_array::<Int32Type>(col))),
        DataType::Int64 => Ok(ColumnExtractor::Int64(as_primitive_array::<Int64Type>(col))),
        DataType::UInt8 => Ok(ColumnExtractor::UInt8(as_primitive_array::<UInt8Type>(col))),
        DataType::UInt16 => Ok(ColumnExtractor::UInt16(as_primitive_array::<UInt16Type>(
            col,
        ))),
        DataType::UInt32 => Ok(ColumnExtractor::UInt32(as_primitive_array::<UInt32Type>(
            col,
        ))),
        DataType::UInt64 => Ok(ColumnExtractor::UInt64(as_primitive_array::<UInt64Type>(
            col,
        ))),
        DataType::Float32 => Ok(ColumnExtractor::Float32(as_primitive_array::<Float32Type>(
            col,
        ))),
        DataType::Float64 => Ok(ColumnExtractor::Float64(as_primitive_array::<Float64Type>(
            col,
        ))),
        DataType::Boolean => Ok(ColumnExtractor::Boolean(as_boolean_array(col))),
        DataType::Utf8 => Ok(ColumnExtractor::Utf8(as_string_array(col))),
        DataType::LargeUtf8 => Ok(ColumnExtractor::LargeUtf8(as_largestring_array(col))),
        DataType::Date32 => Ok(ColumnExtractor::Date32(as_primitive_array::<Date32Type>(
            col,
        ))),
        DataType::Timestamp(unit, None) => Ok(ColumnExtractor::TimestampNaive(col, *unit)),
        DataType::Timestamp(unit, Some(tz_str)) => {
            let zoneinfo = py.import("zoneinfo")?;
            let zi_cls = zoneinfo.getattr("ZoneInfo")?;
            let tz_obj: PyObject = zi_cls.call1((tz_str.as_ref(),))?.unbind();
            Ok(ColumnExtractor::TimestampAware(col, *unit, tz_obj))
        }
        DataType::Duration(unit) => Ok(ColumnExtractor::Duration(col, *unit)),
        DataType::List(field) => {
            let arr = as_list_array(col);
            Ok(ColumnExtractor::List(
                arr,
                field.data_type().clone(),
                list_element_plan(plan),
            ))
        }
        DataType::LargeList(field) => {
            let arr = as_large_list_array(col);
            Ok(ColumnExtractor::LargeList(
                arr,
                field.data_type().clone(),
                list_element_plan(plan),
            ))
        }
        DataType::Struct(fields) => {
            // The model and each field's child plan were resolved once by
            // build_model_plan; read them here without touching Python.
            let (constructor, child_plans) = match plan {
                ModelPlan::Struct {
                    constructor,
                    children,
                } => (Arc::clone(constructor), children),
                _ => return Err(struct_no_model_err(fields)),
            };
            let struct_arr = as_struct_array(col);
            let mut children: Vec<(Py<PyString>, ColumnExtractor<'a>)> =
                Vec::with_capacity(fields.len());

            for (i, field) in fields.iter().enumerate() {
                let child_col = struct_arr.column(i);
                // child_plans is positionally aligned with fields (both walk the
                // Struct fields in order). Fall back to None defensively.
                let child_plan = child_plans.get(i).unwrap_or(&ModelPlan::None);
                let child_ext = prepare_extractor_with_plan(
                    py,
                    child_col.as_ref(),
                    field.data_type(),
                    child_plan,
                )?;
                let field_name = PyString::intern(py, field.name()).unbind();
                children.push((field_name, child_ext));
            }
            Ok(ColumnExtractor::Struct(struct_arr, children, constructor))
        }
        // Extended scalar types
        DataType::Float16 => Ok(ColumnExtractor::Float16(as_primitive_array::<Float16Type>(
            col,
        ))),
        DataType::Decimal128(_, _) => Ok(ColumnExtractor::Decimal128(
            col.as_any()
                .downcast_ref::<arrow_array::Decimal128Array>()
                .expect("Decimal128Array"),
            import_decimal_cls(py)?,
        )),
        DataType::Decimal256(_, _) => Ok(ColumnExtractor::Decimal256(
            col.as_any()
                .downcast_ref::<arrow_array::Decimal256Array>()
                .expect("Decimal256Array"),
            import_decimal_cls(py)?,
        )),
        DataType::Decimal32(_, _) => Ok(ColumnExtractor::Decimal32(
            col.as_any()
                .downcast_ref::<arrow_array::Decimal32Array>()
                .expect("Decimal32Array"),
            import_decimal_cls(py)?,
        )),
        DataType::Decimal64(_, _) => Ok(ColumnExtractor::Decimal64(
            col.as_any()
                .downcast_ref::<arrow_array::Decimal64Array>()
                .expect("Decimal64Array"),
            import_decimal_cls(py)?,
        )),
        // Extended temporal types
        DataType::Date64 => Ok(ColumnExtractor::Date64(as_primitive_array::<Date64Type>(
            col,
        ))),
        DataType::Time32(unit) => Ok(ColumnExtractor::Time32(col, *unit)),
        DataType::Time64(unit) => Ok(ColumnExtractor::Time64(col, *unit)),
        // Binary types
        DataType::Binary => Ok(ColumnExtractor::Binary(
            col.as_any()
                .downcast_ref::<BinaryArray>()
                .expect("BinaryArray"),
        )),
        DataType::LargeBinary => Ok(ColumnExtractor::LargeBinary(
            col.as_any()
                .downcast_ref::<LargeBinaryArray>()
                .expect("LargeBinaryArray"),
        )),
        DataType::FixedSizeBinary(_) => Ok(ColumnExtractor::FixedSizeBinary(
            col.as_any()
                .downcast_ref::<FixedSizeBinaryArray>()
                .expect("FixedSizeBinaryArray"),
        )),
        // View types
        DataType::Utf8View => Ok(ColumnExtractor::Utf8View(
            col.as_any()
                .downcast_ref::<StringViewArray>()
                .expect("StringViewArray"),
        )),
        DataType::BinaryView => Ok(ColumnExtractor::BinaryView(
            col.as_any()
                .downcast_ref::<BinaryViewArray>()
                .expect("BinaryViewArray"),
        )),
        DataType::Null => Ok(ColumnExtractor::Null),
        // Interval types
        DataType::Interval(IntervalUnit::YearMonth) => {
            Ok(ColumnExtractor::IntervalYearMonth(as_primitive_array::<
                IntervalYearMonthType,
            >(col)))
        }
        DataType::Interval(IntervalUnit::DayTime) => {
            Ok(ColumnExtractor::IntervalDayTime(as_primitive_array::<
                IntervalDayTimeType,
            >(col)))
        }
        DataType::Interval(IntervalUnit::MonthDayNano) => {
            Ok(ColumnExtractor::IntervalMonthDayNano(as_primitive_array::<
                IntervalMonthDayNanoType,
            >(col)))
        }
        // Container types
        DataType::FixedSizeList(field, _size) => {
            let arr = as_fixed_size_list_array(col);
            Ok(ColumnExtractor::FixedSizeList(
                arr,
                field.data_type().clone(),
                list_element_plan(plan),
            ))
        }
        DataType::Map(entries_field, _sorted) => {
            let arr = as_map_array(col);
            if let DataType::Struct(fields) = entries_field.data_type() {
                let key_dt = fields[0].data_type().clone();
                let val_dt = fields[1].data_type().clone();
                let (key_plan, val_plan) = map_kv_plans(plan);
                Ok(ColumnExtractor::Map(
                    arr, key_dt, val_dt, key_plan, val_plan,
                ))
            } else {
                Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
                    "Map entries field is not a Struct",
                ))
            }
        }
        // Union type
        DataType::Union(union_fields, _mode) => {
            let arr = as_union_array(col);
            let type_map: Vec<(i8, DataType)> = union_fields
                .iter()
                .map(|(tid, field)| (tid, field.data_type().clone()))
                .collect();
            Ok(ColumnExtractor::Union(arr, type_map))
        }
        _ => Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(format!(
            "Unsupported Arrow type: {data_type:?}"
        ))),
    }
}

impl<'a> ColumnExtractor<'a> {
    /// Extract the value at `row` as a PyObject.
    /// Checks is_null(row) BEFORE accessing value(row) per NULL-01, NULL-03.
    /// Returns py.None() for null values per NULL-02.
    pub fn extract_value(&self, py: Python<'_>, row: usize) -> PyResult<PyObject> {
        match self {
            ColumnExtractor::Int8(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(arr.value(row).into_pyobject(py)?.into_any().unbind())
                }
            }
            ColumnExtractor::Int16(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(arr.value(row).into_pyobject(py)?.into_any().unbind())
                }
            }
            ColumnExtractor::Int32(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(arr.value(row).into_pyobject(py)?.into_any().unbind())
                }
            }
            ColumnExtractor::Int64(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(arr.value(row).into_pyobject(py)?.into_any().unbind())
                }
            }
            ColumnExtractor::UInt8(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(arr.value(row).into_pyobject(py)?.into_any().unbind())
                }
            }
            ColumnExtractor::UInt16(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(arr.value(row).into_pyobject(py)?.into_any().unbind())
                }
            }
            ColumnExtractor::UInt32(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(arr.value(row).into_pyobject(py)?.into_any().unbind())
                }
            }
            ColumnExtractor::UInt64(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(arr.value(row).into_pyobject(py)?.into_any().unbind())
                }
            }
            ColumnExtractor::Float32(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(arr.value(row).into_pyobject(py)?.into_any().unbind())
                }
            }
            ColumnExtractor::Float64(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(arr.value(row).into_pyobject(py)?.into_any().unbind())
                }
            }
            ColumnExtractor::Boolean(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    let val = arr.value(row);
                    Ok(val.into_pyobject(py)?.to_owned().into_any().unbind())
                }
            }
            ColumnExtractor::Utf8(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(PyString::new(py, arr.value(row)).into_any().unbind())
                }
            }
            ColumnExtractor::LargeUtf8(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(PyString::new(py, arr.value(row)).into_any().unbind())
                }
            }
            // --- Temporal types ---
            ColumnExtractor::Date32(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    match arr.value_as_date(row) {
                        Some(date) => Ok(date.into_pyobject(py)?.into_any().unbind()),
                        None => Ok(py.None()),
                    }
                }
            }
            ColumnExtractor::TimestampNaive(arr, unit) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    extract_naive_datetime(py, *arr, row, *unit)
                }
            }
            ColumnExtractor::TimestampAware(arr, unit, tz_obj) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    extract_aware_datetime(py, *arr, row, *unit, tz_obj)
                }
            }
            ColumnExtractor::Duration(arr, unit) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    extract_duration(py, *arr, row, *unit)
                }
            }
            // --- Complex types ---
            ColumnExtractor::List(arr, child_dt, child_plan) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    let child_array = arr.value(row); // ArrayRef for this row's list
                    let len = child_array.len();
                    let child_ext = prepare_extractor_with_plan(
                        py,
                        child_array.as_ref(),
                        child_dt,
                        child_plan,
                    )?;
                    let mut items: Vec<PyObject> = Vec::with_capacity(len);
                    for j in 0..len {
                        items.push(child_ext.extract_value(py, j)?);
                    }
                    Ok(PyList::new(py, &items)?.into_any().unbind())
                }
            }
            ColumnExtractor::LargeList(arr, child_dt, child_plan) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    let child_array = arr.value(row);
                    let len = child_array.len();
                    let child_ext = prepare_extractor_with_plan(
                        py,
                        child_array.as_ref(),
                        child_dt,
                        child_plan,
                    )?;
                    let mut items: Vec<PyObject> = Vec::with_capacity(len);
                    for j in 0..len {
                        items.push(child_ext.extract_value(py, j)?);
                    }
                    Ok(PyList::new(py, &items)?.into_any().unbind())
                }
            }
            ColumnExtractor::Struct(arr, children, constructor) => {
                if arr.is_null(row) {
                    Ok(py.None()) // null struct -> None for entire nested model (Pitfall 4)
                } else {
                    let values = children
                        .iter()
                        .map(|(_, extractor)| extractor.extract_value(py, row))
                        .collect::<PyResult<Vec<_>>>()?;
                    constructor.build(py, values)
                }
            }
            // --- Extended scalar types ---
            ColumnExtractor::Float16(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(arr
                        .value(row)
                        .to_f32()
                        .into_pyobject(py)?
                        .into_any()
                        .unbind())
                }
            }
            ColumnExtractor::Decimal128(arr, decimal_cls) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    let s = arr.value_as_string(row);
                    Ok(decimal_cls.bind(py).call1((s,))?.unbind())
                }
            }
            ColumnExtractor::Decimal256(arr, decimal_cls) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    let s = arr.value_as_string(row);
                    Ok(decimal_cls.bind(py).call1((s,))?.unbind())
                }
            }
            ColumnExtractor::Decimal32(arr, decimal_cls) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    let s = arr.value_as_string(row);
                    Ok(decimal_cls.bind(py).call1((s,))?.unbind())
                }
            }
            ColumnExtractor::Decimal64(arr, decimal_cls) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    let s = arr.value_as_string(row);
                    Ok(decimal_cls.bind(py).call1((s,))?.unbind())
                }
            }
            // --- Extended temporal types ---
            ColumnExtractor::Date64(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    let ms = arr.value(row);
                    let secs = ms.div_euclid(1000);
                    let nsec = (ms.rem_euclid(1000) as u32) * 1_000_000;
                    match chrono::DateTime::from_timestamp(secs, nsec) {
                        Some(utc) => Ok(utc.naive_utc().into_pyobject(py)?.into_any().unbind()),
                        None => Ok(py.None()),
                    }
                }
            }
            ColumnExtractor::Time32(arr, unit) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    match unit {
                        TimeUnit::Second => {
                            let total_secs =
                                as_primitive_array::<Time32SecondType>(*arr).value(row);
                            let h = (total_secs / 3600) as u8;
                            let m = ((total_secs % 3600) / 60) as u8;
                            let s = (total_secs % 60) as u8;
                            Ok(PyTime::new(py, h, m, s, 0, None)?.into_any().unbind())
                        }
                        TimeUnit::Millisecond => {
                            let total_ms =
                                as_primitive_array::<Time32MillisecondType>(*arr).value(row);
                            let total_secs = total_ms / 1000;
                            let us = ((total_ms % 1000) * 1000) as u32;
                            let h = (total_secs / 3600) as u8;
                            let m = ((total_secs % 3600) / 60) as u8;
                            let s = (total_secs % 60) as u8;
                            Ok(PyTime::new(py, h, m, s, us, None)?.into_any().unbind())
                        }
                        _ => Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
                            "Time32 only supports Second and Millisecond units",
                        )),
                    }
                }
            }
            ColumnExtractor::Time64(arr, unit) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    match unit {
                        TimeUnit::Microsecond => {
                            let total_us =
                                as_primitive_array::<Time64MicrosecondType>(*arr).value(row);
                            let total_secs = (total_us / 1_000_000) as i32;
                            let us = (total_us % 1_000_000) as u32;
                            let h = (total_secs / 3600) as u8;
                            let m = ((total_secs % 3600) / 60) as u8;
                            let s = (total_secs % 60) as u8;
                            Ok(PyTime::new(py, h, m, s, us, None)?.into_any().unbind())
                        }
                        TimeUnit::Nanosecond => {
                            let total_ns =
                                as_primitive_array::<Time64NanosecondType>(*arr).value(row);
                            let total_us = total_ns / 1000; // truncate to microsecond (Pitfall 4)
                            let total_secs = (total_us / 1_000_000) as i32;
                            let us = (total_us % 1_000_000) as u32;
                            let h = (total_secs / 3600) as u8;
                            let m = ((total_secs % 3600) / 60) as u8;
                            let s = (total_secs % 60) as u8;
                            Ok(PyTime::new(py, h, m, s, us, None)?.into_any().unbind())
                        }
                        _ => Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
                            "Time64 only supports Microsecond and Nanosecond units",
                        )),
                    }
                }
            }
            // --- Binary types ---
            ColumnExtractor::Binary(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(PyBytes::new(py, arr.value(row)).into_any().unbind())
                }
            }
            ColumnExtractor::LargeBinary(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(PyBytes::new(py, arr.value(row)).into_any().unbind())
                }
            }
            ColumnExtractor::FixedSizeBinary(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(PyBytes::new(py, arr.value(row)).into_any().unbind())
                }
            }
            // --- View types ---
            ColumnExtractor::Utf8View(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(PyString::new(py, arr.value(row)).into_any().unbind())
                }
            }
            ColumnExtractor::BinaryView(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    Ok(PyBytes::new(py, arr.value(row)).into_any().unbind())
                }
            }
            // --- Interval types ---
            ColumnExtractor::IntervalYearMonth(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    let months = arr.value(row) as i64;
                    let tuple = PyTuple::new(
                        py,
                        &[
                            months.into_pyobject(py)?.into_any().unbind(),
                            0i64.into_pyobject(py)?.into_any().unbind(),
                            0i64.into_pyobject(py)?.into_any().unbind(),
                        ],
                    )?;
                    Ok(tuple.into_any().unbind())
                }
            }
            ColumnExtractor::IntervalDayTime(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    let val = arr.value(row);
                    let (days, ms) = IntervalDayTimeType::to_parts(val);
                    let nanos = (ms as i64) * 1_000_000;
                    let tuple = PyTuple::new(
                        py,
                        &[
                            0i64.into_pyobject(py)?.into_any().unbind(),
                            (days as i64).into_pyobject(py)?.into_any().unbind(),
                            nanos.into_pyobject(py)?.into_any().unbind(),
                        ],
                    )?;
                    Ok(tuple.into_any().unbind())
                }
            }
            ColumnExtractor::IntervalMonthDayNano(arr) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    let val = arr.value(row);
                    let months = val.months as i64;
                    let days = val.days as i64;
                    let nanos = val.nanoseconds;
                    let tuple = PyTuple::new(
                        py,
                        &[
                            months.into_pyobject(py)?.into_any().unbind(),
                            days.into_pyobject(py)?.into_any().unbind(),
                            nanos.into_pyobject(py)?.into_any().unbind(),
                        ],
                    )?;
                    Ok(tuple.into_any().unbind())
                }
            }
            // --- Container types ---
            ColumnExtractor::FixedSizeList(arr, child_dt, child_plan) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    let child_array = arr.value(row);
                    let len = child_array.len();
                    let child_ext = prepare_extractor_with_plan(
                        py,
                        child_array.as_ref(),
                        child_dt,
                        child_plan,
                    )?;
                    let mut items: Vec<PyObject> = Vec::with_capacity(len);
                    for j in 0..len {
                        items.push(child_ext.extract_value(py, j)?);
                    }
                    Ok(PyList::new(py, &items)?.into_any().unbind())
                }
            }
            ColumnExtractor::Map(arr, key_dt, val_dt, key_plan, val_plan) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    let entries = arr.value(row);
                    let keys_arr = entries.column(0);
                    let vals_arr = entries.column(1);
                    // Each side uses its own pre-resolved plan (key_plan /
                    // val_plan); the plan is non-None wherever the side is a
                    // Struct, empty otherwise. No per-row Python introspection.
                    let key_ext =
                        prepare_extractor_with_plan(py, keys_arr.as_ref(), key_dt, key_plan)?;
                    let val_ext =
                        prepare_extractor_with_plan(py, vals_arr.as_ref(), val_dt, val_plan)?;
                    let len = entries.len();
                    let mut items: Vec<PyObject> = Vec::with_capacity(len);
                    for j in 0..len {
                        let k = key_ext.extract_value(py, j)?;
                        let v = val_ext.extract_value(py, j)?;
                        let pair = PyTuple::new(py, &[k, v])?;
                        items.push(pair.into_any().unbind());
                    }
                    Ok(PyList::new(py, &items)?.into_any().unbind())
                }
            }
            // --- Union type ---
            ColumnExtractor::Union(arr, type_map) => {
                if arr.is_null(row) {
                    Ok(py.None())
                } else {
                    let tid = arr.type_id(row);
                    let child = arr.child(tid);
                    let child_idx = match arr.offsets() {
                        Some(_) => arr.value_offset(row) as usize, // Dense
                        None => row,                               // Sparse
                    };
                    let child_dt = type_map
                        .iter()
                        .find(|(id, _)| *id == tid)
                        .map(|(_, dt)| dt)
                        .ok_or_else(|| {
                            PyErr::new::<pyo3::exceptions::PyTypeError, _>(format!(
                                "Unknown union type_id: {tid}"
                            ))
                        })?;
                    let child_ext = prepare_extractor_with_plan(
                        py,
                        child.as_ref(),
                        child_dt,
                        &ModelPlan::None,
                    )?;
                    child_ext.extract_value(py, child_idx)
                }
            }
            // Null type -- always returns None unconditionally.
            // Do NOT check is_null() -- NullArray has no physical null buffer
            // so is_null() returns false (Pitfall 1 from research).
            ColumnExtractor::Null => Ok(py.None()),
        }
    }

    /// Extract the value at `row` as a serde_json::Value for JSON serialization.
    /// Used by the validated path (validate=True) to build JSON bytes per row.
    /// Checks is_null(row) BEFORE accessing value(row).
    /// Returns Value::Null for null values (key included, not omitted -- Pitfall 7).
    pub fn extract_json_value(&self, py: Python<'_>, row: usize) -> Result<Value, PyErr> {
        match self {
            ColumnExtractor::Int8(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::Number(Number::from(arr.value(row))))
                }
            }
            ColumnExtractor::Int16(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::Number(Number::from(arr.value(row))))
                }
            }
            ColumnExtractor::Int32(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::Number(Number::from(arr.value(row))))
                }
            }
            ColumnExtractor::Int64(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::Number(Number::from(arr.value(row))))
                }
            }
            ColumnExtractor::UInt8(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::Number(Number::from(arr.value(row))))
                }
            }
            ColumnExtractor::UInt16(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::Number(Number::from(arr.value(row))))
                }
            }
            ColumnExtractor::UInt32(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::Number(Number::from(arr.value(row))))
                }
            }
            ColumnExtractor::UInt64(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::Number(Number::from(arr.value(row))))
                }
            }
            // Pitfall 5: Float NaN/Infinity -> Value::Null (not serde_json error)
            ColumnExtractor::Float32(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    let v = arr.value(row);
                    if v.is_nan() || v.is_infinite() {
                        Ok(Value::Null)
                    } else {
                        Ok(Number::from_f64(v as f64)
                            .map(Value::Number)
                            .unwrap_or(Value::Null))
                    }
                }
            }
            ColumnExtractor::Float64(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    let v = arr.value(row);
                    if v.is_nan() || v.is_infinite() {
                        Ok(Value::Null)
                    } else {
                        Ok(Number::from_f64(v)
                            .map(Value::Number)
                            .unwrap_or(Value::Null))
                    }
                }
            }
            ColumnExtractor::Boolean(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::Bool(arr.value(row)))
                }
            }
            ColumnExtractor::Utf8(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::String(arr.value(row).to_owned()))
                }
            }
            ColumnExtractor::LargeUtf8(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::String(arr.value(row).to_owned()))
                }
            }
            // --- Temporal types: format as ISO 8601 strings for Pydantic ---
            ColumnExtractor::Date32(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    match arr.value_as_date(row) {
                        Some(d) => Ok(Value::String(d.format("%Y-%m-%d").to_string())),
                        None => Ok(Value::Null),
                    }
                }
            }
            ColumnExtractor::TimestampNaive(arr, unit) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    match extract_naive_dt_value(*arr, row, *unit) {
                        Some(dt) => {
                            Ok(Value::String(dt.format("%Y-%m-%dT%H:%M:%S%.f").to_string()))
                        }
                        None => Ok(Value::Null),
                    }
                }
            }
            ColumnExtractor::TimestampAware(arr, unit, _tz_obj) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    // Arrow timestamps with tz are stored in UTC.
                    // Append +00:00 so Pydantic produces tz-aware datetime.
                    match extract_naive_dt_value(*arr, row, *unit) {
                        Some(dt) => {
                            let s = dt.format("%Y-%m-%dT%H:%M:%S%.f").to_string();
                            Ok(Value::String(format!("{s}+00:00")))
                        }
                        None => Ok(Value::Null),
                    }
                }
            }
            ColumnExtractor::Duration(arr, unit) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    match extract_duration_value(*arr, row, *unit) {
                        Some(td) => Ok(Value::String(timedelta_to_iso8601(&td))),
                        None => Ok(Value::Null),
                    }
                }
            }
            // --- Complex types ---
            ColumnExtractor::List(arr, child_dt, child_plan) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    let child_array = arr.value(row);
                    let len = child_array.len();
                    let child_ext = prepare_extractor_with_plan(
                        py,
                        child_array.as_ref(),
                        child_dt,
                        child_plan,
                    )?;
                    let mut items: Vec<Value> = Vec::with_capacity(len);
                    for j in 0..len {
                        items.push(child_ext.extract_json_value(py, j)?);
                    }
                    Ok(Value::Array(items))
                }
            }
            ColumnExtractor::LargeList(arr, child_dt, child_plan) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    let child_array = arr.value(row);
                    let len = child_array.len();
                    let child_ext = prepare_extractor_with_plan(
                        py,
                        child_array.as_ref(),
                        child_dt,
                        child_plan,
                    )?;
                    let mut items: Vec<Value> = Vec::with_capacity(len);
                    for j in 0..len {
                        items.push(child_ext.extract_json_value(py, j)?);
                    }
                    Ok(Value::Array(items))
                }
            }
            ColumnExtractor::Struct(_arr, children, _constructor) => {
                if _arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    let mut map = Map::new();
                    for (field_name, extractor) in children.iter() {
                        let key = field_name.bind(py).to_str()?.to_owned();
                        let value = extractor.extract_json_value(py, row)?;
                        map.insert(key, value);
                    }
                    Ok(Value::Object(map))
                }
            }
            // --- Extended scalar types ---
            ColumnExtractor::Float16(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    let v = arr.value(row).to_f64();
                    if v.is_nan() || v.is_infinite() {
                        Ok(Value::Null)
                    } else {
                        Ok(Number::from_f64(v)
                            .map(Value::Number)
                            .unwrap_or(Value::Null))
                    }
                }
            }
            ColumnExtractor::Decimal128(arr, _) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::String(arr.value_as_string(row)))
                }
            }
            ColumnExtractor::Decimal256(arr, _) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::String(arr.value_as_string(row)))
                }
            }
            ColumnExtractor::Decimal32(arr, _) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::String(arr.value_as_string(row)))
                }
            }
            ColumnExtractor::Decimal64(arr, _) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::String(arr.value_as_string(row)))
                }
            }
            // --- Extended temporal types ---
            ColumnExtractor::Date64(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    let ms = arr.value(row);
                    let secs = ms.div_euclid(1000);
                    let nsec = (ms.rem_euclid(1000) as u32) * 1_000_000;
                    match chrono::DateTime::from_timestamp(secs, nsec) {
                        Some(utc) => Ok(Value::String(
                            utc.naive_utc().format("%Y-%m-%dT%H:%M:%S%.f").to_string(),
                        )),
                        None => Ok(Value::Null),
                    }
                }
            }
            ColumnExtractor::Time32(arr, unit) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    let (h, m, s, us) = match unit {
                        TimeUnit::Second => {
                            let total_secs =
                                as_primitive_array::<Time32SecondType>(*arr).value(row);
                            (
                                (total_secs / 3600) as u8,
                                ((total_secs % 3600) / 60) as u8,
                                (total_secs % 60) as u8,
                                0u32,
                            )
                        }
                        TimeUnit::Millisecond => {
                            let total_ms =
                                as_primitive_array::<Time32MillisecondType>(*arr).value(row);
                            let total_secs = total_ms / 1000;
                            (
                                (total_secs / 3600) as u8,
                                ((total_secs % 3600) / 60) as u8,
                                (total_secs % 60) as u8,
                                ((total_ms % 1000) * 1000) as u32,
                            )
                        }
                        _ => {
                            return Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
                                "Time32 only supports Second and Millisecond units",
                            ))
                        }
                    };
                    if us > 0 {
                        Ok(Value::String(format!(
                            "{:02}:{:02}:{:02}.{:06}",
                            h, m, s, us
                        )))
                    } else {
                        Ok(Value::String(format!("{:02}:{:02}:{:02}", h, m, s)))
                    }
                }
            }
            ColumnExtractor::Time64(arr, unit) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    let (h, m, s, us) = match unit {
                        TimeUnit::Microsecond => {
                            let total_us =
                                as_primitive_array::<Time64MicrosecondType>(*arr).value(row);
                            let total_secs = (total_us / 1_000_000) as i32;
                            (
                                (total_secs / 3600) as u8,
                                ((total_secs % 3600) / 60) as u8,
                                (total_secs % 60) as u8,
                                (total_us % 1_000_000) as u32,
                            )
                        }
                        TimeUnit::Nanosecond => {
                            let total_ns =
                                as_primitive_array::<Time64NanosecondType>(*arr).value(row);
                            let total_us = total_ns / 1000; // truncate ns to us
                            let total_secs = (total_us / 1_000_000) as i32;
                            (
                                (total_secs / 3600) as u8,
                                ((total_secs % 3600) / 60) as u8,
                                (total_secs % 60) as u8,
                                (total_us % 1_000_000) as u32,
                            )
                        }
                        _ => {
                            return Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
                                "Time64 only supports Microsecond and Nanosecond units",
                            ))
                        }
                    };
                    if us > 0 {
                        Ok(Value::String(format!(
                            "{:02}:{:02}:{:02}.{:06}",
                            h, m, s, us
                        )))
                    } else {
                        Ok(Value::String(format!("{:02}:{:02}:{:02}", h, m, s)))
                    }
                }
            }
            // --- Binary types ---
            // Binary is emitted as a base64 string: raw bytes are not valid
            // UTF-8 and cannot round-trip through a JSON string. NOTE: Pydantic's
            // default decodes a JSON string into `bytes` as UTF-8, so a plain
            // `bytes` field receives the base64 *text*, not the original bytes.
            // To recover the original bytes on the validated path, the field must
            // opt into base64 decoding (e.g. `pydantic.Base64Bytes`, or model
            // config `val_json_bytes="base64"`). The fast path returns raw bytes
            // directly and needs no such annotation.
            ColumnExtractor::Binary(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    use base64::Engine;
                    let encoded = base64::engine::general_purpose::STANDARD.encode(arr.value(row));
                    Ok(Value::String(encoded))
                }
            }
            ColumnExtractor::LargeBinary(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    use base64::Engine;
                    let encoded = base64::engine::general_purpose::STANDARD.encode(arr.value(row));
                    Ok(Value::String(encoded))
                }
            }
            ColumnExtractor::FixedSizeBinary(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    use base64::Engine;
                    let encoded = base64::engine::general_purpose::STANDARD.encode(arr.value(row));
                    Ok(Value::String(encoded))
                }
            }
            // --- View types ---
            ColumnExtractor::Utf8View(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    Ok(Value::String(arr.value(row).to_owned()))
                }
            }
            ColumnExtractor::BinaryView(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    use base64::Engine;
                    let encoded = base64::engine::general_purpose::STANDARD.encode(arr.value(row));
                    Ok(Value::String(encoded))
                }
            }
            // --- Interval types ---
            ColumnExtractor::IntervalYearMonth(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    let months = arr.value(row) as i64;
                    Ok(Value::Array(vec![
                        Value::Number(Number::from(months)),
                        Value::Number(Number::from(0i64)),
                        Value::Number(Number::from(0i64)),
                    ]))
                }
            }
            ColumnExtractor::IntervalDayTime(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    let val = arr.value(row);
                    let (days, ms) = IntervalDayTimeType::to_parts(val);
                    let nanos = (ms as i64) * 1_000_000;
                    Ok(Value::Array(vec![
                        Value::Number(Number::from(0i64)),
                        Value::Number(Number::from(days as i64)),
                        Value::Number(Number::from(nanos)),
                    ]))
                }
            }
            ColumnExtractor::IntervalMonthDayNano(arr) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    let val = arr.value(row);
                    let months = val.months as i64;
                    let days = val.days as i64;
                    let nanos = val.nanoseconds;
                    Ok(Value::Array(vec![
                        Value::Number(Number::from(months)),
                        Value::Number(Number::from(days)),
                        Value::Number(Number::from(nanos)),
                    ]))
                }
            }
            // --- Container types ---
            ColumnExtractor::FixedSizeList(arr, child_dt, child_plan) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    let child_array = arr.value(row);
                    let len = child_array.len();
                    let child_ext = prepare_extractor_with_plan(
                        py,
                        child_array.as_ref(),
                        child_dt,
                        child_plan,
                    )?;
                    let mut items: Vec<Value> = Vec::with_capacity(len);
                    for j in 0..len {
                        items.push(child_ext.extract_json_value(py, j)?);
                    }
                    Ok(Value::Array(items))
                }
            }
            ColumnExtractor::Map(arr, key_dt, val_dt, key_plan, val_plan) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    let entries = arr.value(row);
                    let keys_arr = entries.column(0);
                    let vals_arr = entries.column(1);
                    let key_ext =
                        prepare_extractor_with_plan(py, keys_arr.as_ref(), key_dt, key_plan)?;
                    let val_ext =
                        prepare_extractor_with_plan(py, vals_arr.as_ref(), val_dt, val_plan)?;
                    let len = entries.len();
                    let mut items: Vec<Value> = Vec::with_capacity(len);
                    for j in 0..len {
                        let k = key_ext.extract_json_value(py, j)?;
                        let v = val_ext.extract_json_value(py, j)?;
                        items.push(Value::Array(vec![k, v]));
                    }
                    Ok(Value::Array(items))
                }
            }
            // --- Union type ---
            ColumnExtractor::Union(arr, type_map) => {
                if arr.is_null(row) {
                    Ok(Value::Null)
                } else {
                    let tid = arr.type_id(row);
                    let child = arr.child(tid);
                    let child_idx = match arr.offsets() {
                        Some(_) => arr.value_offset(row) as usize, // Dense
                        None => row,                               // Sparse
                    };
                    let child_dt = type_map
                        .iter()
                        .find(|(id, _)| *id == tid)
                        .map(|(_, dt)| dt)
                        .ok_or_else(|| {
                            PyErr::new::<pyo3::exceptions::PyTypeError, _>(format!(
                                "Unknown union type_id: {tid}"
                            ))
                        })?;
                    let child_ext = prepare_extractor_with_plan(
                        py,
                        child.as_ref(),
                        child_dt,
                        &ModelPlan::None,
                    )?;
                    child_ext.extract_json_value(py, child_idx)
                }
            }
            ColumnExtractor::Null => Ok(Value::Null),
        }
    }
}

/// Extract a naive datetime from a timestamp column at the given row.
/// Handles all TimeUnit variants (Second, Millisecond, Microsecond, Nanosecond).
/// Nanosecond precision truncates to microsecond automatically via chrono.
fn extract_naive_datetime(
    py: Python<'_>,
    arr: &dyn Array,
    row: usize,
    unit: TimeUnit,
) -> PyResult<PyObject> {
    let naive_dt = match unit {
        TimeUnit::Second => as_primitive_array::<TimestampSecondType>(arr).value_as_datetime(row),
        TimeUnit::Millisecond => {
            as_primitive_array::<TimestampMillisecondType>(arr).value_as_datetime(row)
        }
        TimeUnit::Microsecond => {
            as_primitive_array::<TimestampMicrosecondType>(arr).value_as_datetime(row)
        }
        TimeUnit::Nanosecond => {
            as_primitive_array::<TimestampNanosecondType>(arr).value_as_datetime(row)
        }
    };
    match naive_dt {
        Some(dt) => Ok(dt.into_pyobject(py)?.into_any().unbind()),
        None => Ok(py.None()),
    }
}

/// Extract a timezone-aware datetime from a timestamp column at the given row.
///
/// Arrow stores tz-aware timestamps as a UTC instant; the timezone is display
/// metadata. `value_as_datetime` returns that instant as a UTC wall-clock, so we
/// build a UTC-aware Python datetime first and then `astimezone(tz)` to shift it
/// into the target zone. Attaching the ZoneInfo directly to the UTC wall-clock
/// would re-interpret the numbers as local time and move the absolute instant.
fn extract_aware_datetime(
    py: Python<'_>,
    arr: &dyn Array,
    row: usize,
    unit: TimeUnit,
    tz_obj: &PyObject,
) -> PyResult<PyObject> {
    let naive_dt = match unit {
        TimeUnit::Second => as_primitive_array::<TimestampSecondType>(arr).value_as_datetime(row),
        TimeUnit::Millisecond => {
            as_primitive_array::<TimestampMillisecondType>(arr).value_as_datetime(row)
        }
        TimeUnit::Microsecond => {
            as_primitive_array::<TimestampMicrosecondType>(arr).value_as_datetime(row)
        }
        TimeUnit::Nanosecond => {
            as_primitive_array::<TimestampNanosecondType>(arr).value_as_datetime(row)
        }
    };
    match naive_dt {
        Some(dt) => {
            // dt is the UTC instant. Convert to a UTC-aware Python datetime
            // (chrono truncates ns -> us per TEMP-05), then localize.
            let py_utc = dt.and_utc().into_pyobject(py)?;
            let localized = py_utc.call_method1("astimezone", (tz_obj.bind(py),))?;
            Ok(localized.into_any().unbind())
        }
        None => Ok(py.None()),
    }
}

/// Extract a duration from a duration column at the given row.
/// Handles all TimeUnit variants via chrono::TimeDelta.
fn extract_duration(
    py: Python<'_>,
    arr: &dyn Array,
    row: usize,
    unit: TimeUnit,
) -> PyResult<PyObject> {
    let td = match unit {
        TimeUnit::Second => as_primitive_array::<DurationSecondType>(arr).value_as_duration(row),
        TimeUnit::Millisecond => {
            as_primitive_array::<DurationMillisecondType>(arr).value_as_duration(row)
        }
        TimeUnit::Microsecond => {
            as_primitive_array::<DurationMicrosecondType>(arr).value_as_duration(row)
        }
        TimeUnit::Nanosecond => {
            as_primitive_array::<DurationNanosecondType>(arr).value_as_duration(row)
        }
    };
    match td {
        Some(delta) => Ok(delta.into_pyobject(py)?.into_any().unbind()),
        None => Ok(py.None()),
    }
}

/// Convert a chrono::TimeDelta to ISO 8601 duration string (PxDTxHxMxS).
/// Pydantic's model_validate_json expects this format for timedelta fields.
fn timedelta_to_iso8601(td: &chrono::TimeDelta) -> String {
    // Derive the sign from the whole delta, not from num_seconds() -- for a
    // sub-second negative duration (e.g. -500ms) num_seconds() truncates to 0
    // and would drop the sign. Work with the absolute magnitude thereafter.
    let is_negative = *td < chrono::TimeDelta::zero();
    let td = td.abs();
    let total_secs = td.num_seconds().unsigned_abs();

    let days = total_secs / 86400;
    let remaining = total_secs % 86400;
    let hours = remaining / 3600;
    let remaining = remaining % 3600;
    let minutes = remaining / 60;
    let seconds = remaining % 60;

    // Include subsecond microseconds from the TimeDelta
    let subsec_nanos = td.subsec_nanos().unsigned_abs();
    let micros = subsec_nanos / 1000;

    let mut result = String::new();
    if is_negative {
        result.push('-');
    }
    result.push('P');
    if days > 0 {
        result.push_str(&format!("{days}D"));
    }
    // Always include T section if there are time components
    if hours > 0 || minutes > 0 || seconds > 0 || micros > 0 || days == 0 {
        result.push('T');
        if hours > 0 {
            result.push_str(&format!("{hours}H"));
        }
        if minutes > 0 {
            result.push_str(&format!("{minutes}M"));
        }
        if micros > 0 {
            result.push_str(&format!("{seconds}.{micros:06}S"));
        } else if seconds > 0 || (hours == 0 && minutes == 0 && days == 0) {
            result.push_str(&format!("{seconds}S"));
        }
    }
    result
}

/// Extract the naive datetime for a given timestamp row, returning a chrono::NaiveDateTime.
fn extract_naive_dt_value(
    arr: &dyn Array,
    row: usize,
    unit: TimeUnit,
) -> Option<chrono::NaiveDateTime> {
    match unit {
        TimeUnit::Second => as_primitive_array::<TimestampSecondType>(arr).value_as_datetime(row),
        TimeUnit::Millisecond => {
            as_primitive_array::<TimestampMillisecondType>(arr).value_as_datetime(row)
        }
        TimeUnit::Microsecond => {
            as_primitive_array::<TimestampMicrosecondType>(arr).value_as_datetime(row)
        }
        TimeUnit::Nanosecond => {
            as_primitive_array::<TimestampNanosecondType>(arr).value_as_datetime(row)
        }
    }
}

/// Extract a duration value as chrono::TimeDelta for a given row.
fn extract_duration_value(
    arr: &dyn Array,
    row: usize,
    unit: TimeUnit,
) -> Option<chrono::TimeDelta> {
    match unit {
        TimeUnit::Second => as_primitive_array::<DurationSecondType>(arr).value_as_duration(row),
        TimeUnit::Millisecond => {
            as_primitive_array::<DurationMillisecondType>(arr).value_as_duration(row)
        }
        TimeUnit::Microsecond => {
            as_primitive_array::<DurationMicrosecondType>(arr).value_as_duration(row)
        }
        TimeUnit::Nanosecond => {
            as_primitive_array::<DurationNanosecondType>(arr).value_as_duration(row)
        }
    }
}
