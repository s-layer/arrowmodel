use std::sync::Arc;

use pyo3::exceptions::PyRuntimeError;
use pyo3::ffi;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PySet, PyString, PyTuple, PyType};

type PyObject = Py<PyAny>;

/// Where one model field's value comes from, per row.
enum Source {
    /// Index into the row's values (aligned with the constructor's keys).
    Key(usize),
    /// No key matched: call `field_info.get_default(...)` per row.
    Default(PyObject),
}

struct Slot {
    name: Py<PyString>,
    source: Source,
}

/// The direct construction plan resolved by `arrowmodel._construct_plan`.
pub struct Plan {
    cls: Py<PyType>,
    new: ffi::newfunc,
    slots: Vec<Slot>,
    /// The names of the key-sourced fields, copied into a fresh set per row.
    fields_set: Py<PySet>,
    extra_allow: bool,
    post_init: bool,
}

/// Builds model instances from a row of values keyed by a fixed list of names,
/// with the same result as `model_cls.model_construct(**dict(zip(keys, values)))`.
///
/// `BaseModel.model_construct` is Python code that re-resolves aliases for every
/// row, so calling it per row cost more than the whole validated path. Where
/// `_construct_plan` can resolve the key matching once, `Direct` builds the
/// instance the way `model_construct` does (`cls.__new__(cls)`, then set
/// `__dict__`, `__pydantic_fields_set__`, `__pydantic_extra__` and
/// `__pydantic_private__`) without re-entering Python per field. Otherwise
/// `Fallback` calls `model_construct` itself.
pub enum ModelConstructor {
    Direct(Plan),
    Fallback {
        cls: PyObject,
        keys: Vec<Py<PyString>>,
    },
}

/// Shared so that per-row rebuilt container extractors (List/Map over a Struct)
/// reuse the constructor resolved once per column.
pub type SharedConstructor = Arc<ModelConstructor>;

impl ModelConstructor {
    /// Resolve the constructor for `model_cls` given the names its values will be
    /// keyed by (Pydantic field names at the top level, Arrow field names for a
    /// Struct). Calls into Python once.
    pub fn new(py: Python<'_>, model_cls: &Bound<'_, PyAny>, keys: &[&str]) -> PyResult<Self> {
        let key_names: Vec<Py<PyString>> = keys
            .iter()
            .map(|k| PyString::intern(py, k).unbind())
            .collect();
        let plan = py
            .import("arrowmodel")?
            .getattr("_construct_plan")?
            .call1((model_cls, keys.to_vec()))?;
        if plan.is_none() {
            return Ok(ModelConstructor::Fallback {
                cls: model_cls.clone().unbind(),
                keys: key_names,
            });
        }
        let (raw_slots, extra_allow, post_init): (Vec<(String, isize, PyObject)>, bool, bool) =
            plan.extract()?;

        let cls = model_cls.cast::<PyType>()?.clone();
        // SAFETY: `cls` is a live type object; Py_tp_new is a valid slot id.
        let new_ptr = unsafe { ffi::PyType_GetSlot(cls.as_type_ptr(), ffi::Py_tp_new) };
        if new_ptr.is_null() {
            return Err(PyRuntimeError::new_err(format!(
                "{} has no tp_new slot",
                cls.name()?
            )));
        }
        // SAFETY: the Py_tp_new slot always holds a `newfunc`.
        let new: ffi::newfunc = unsafe { std::mem::transmute(new_ptr) };

        let mut slots = Vec::with_capacity(raw_slots.len());
        let mut set_names = Vec::new();
        for (name, index, field_info) in raw_slots {
            let name = PyString::intern(py, &name).unbind();
            let source = if index >= 0 {
                set_names.push(name.clone_ref(py));
                Source::Key(index as usize)
            } else {
                Source::Default(field_info)
            };
            slots.push(Slot { name, source });
        }
        Ok(ModelConstructor::Direct(Plan {
            cls: cls.unbind(),
            new,
            slots,
            fields_set: PySet::new(py, &set_names)?.unbind(),
            extra_allow,
            post_init,
        }))
    }

    /// Build one instance from `values`, aligned with the keys given to `new`.
    pub fn build(&self, py: Python<'_>, values: Vec<PyObject>) -> PyResult<PyObject> {
        match self {
            ModelConstructor::Fallback { cls, keys } => {
                let kwargs = PyDict::new(py);
                for (key, value) in keys.iter().zip(values) {
                    kwargs.set_item(key.bind(py), value)?;
                }
                Ok(cls
                    .bind(py)
                    .call_method(pyo3::intern!(py, "model_construct"), (), Some(&kwargs))?
                    .unbind())
            }
            ModelConstructor::Direct(plan) => plan.build(py, values),
        }
    }
}

impl Plan {
    fn build(&self, py: Python<'_>, mut values: Vec<PyObject>) -> PyResult<PyObject> {
        let cls = self.cls.bind(py);
        let empty = PyTuple::empty(py);
        // SAFETY: calling the type's own tp_new with an empty args tuple and no
        // kwargs is what `cls.__new__(cls)` does; a NULL result sets an error.
        let instance = unsafe {
            Bound::from_owned_ptr_or_err(
                py,
                (self.new)(cls.as_type_ptr(), empty.as_ptr(), std::ptr::null_mut()),
            )?
        };

        let fields_values = PyDict::new(py);
        for slot in &self.slots {
            let name = slot.name.bind(py);
            match &slot.source {
                Source::Key(index) => {
                    let value = std::mem::replace(&mut values[*index], py.None());
                    fields_values.set_item(name, value)?;
                }
                Source::Default(field_info) => {
                    let kwargs = PyDict::new(py);
                    kwargs.set_item(pyo3::intern!(py, "call_default_factory"), true)?;
                    kwargs.set_item(pyo3::intern!(py, "validated_data"), &fields_values)?;
                    let value = field_info.bind(py).call_method(
                        pyo3::intern!(py, "get_default"),
                        (),
                        Some(&kwargs),
                    )?;
                    fields_values.set_item(name, value)?;
                }
            }
        }

        // SAFETY: `fields_set` is a live set; PySet_New copies it into a new set.
        let fields_set =
            unsafe { Bound::from_owned_ptr_or_err(py, ffi::PySet_New(self.fields_set.as_ptr()))? };
        let extra = if self.extra_allow {
            PyDict::new(py).into_any()
        } else {
            py.None().into_bound(py)
        };

        // `object.__setattr__`, as `model_construct` uses, bypassing the
        // model's own `__setattr__`.
        object_setattr(&instance, pyo3::intern!(py, "__dict__"), &fields_values)?;
        object_setattr(
            &instance,
            pyo3::intern!(py, "__pydantic_fields_set__"),
            &fields_set,
        )?;
        object_setattr(&instance, pyo3::intern!(py, "__pydantic_extra__"), &extra)?;
        object_setattr(
            &instance,
            pyo3::intern!(py, "__pydantic_private__"),
            py.None().bind(py),
        )?;

        if self.post_init {
            instance.call_method1(pyo3::intern!(py, "model_post_init"), (py.None(),))?;
        }
        Ok(instance.unbind())
    }
}

fn object_setattr(
    obj: &Bound<'_, PyAny>,
    name: &Bound<'_, PyString>,
    value: &Bound<'_, PyAny>,
) -> PyResult<()> {
    // SAFETY: all three are live objects for the duration of the call.
    let rc = unsafe { ffi::PyObject_GenericSetAttr(obj.as_ptr(), name.as_ptr(), value.as_ptr()) };
    if rc == 0 {
        Ok(())
    } else {
        Err(PyErr::fetch(obj.py()))
    }
}
