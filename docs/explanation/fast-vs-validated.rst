.. meta::
   :description: How arrowmodel's two conversion paths work, what each one skips or runs, and when to pick one over the other.

.. _explanation-fast-vs-validated:

Understanding Fast Path vs Validated Path
==========================================

arrowmodel offers two ways to build Pydantic model instances from Arrow data.
Picking the right one is the most impactful performance decision you will make
with this library, so it is worth understanding what each path does under the
hood.

The two paths at a glance
--------------------------

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * -
     - Fast path (default)
     - Validated path
   * - Flag
     - ``validate=False``
     - ``validate=True``
   * - Pydantic method
     - ``model_construct`` semantics (built in Rust)
     - ``model_validate_json``
   * - Type coercion
     - None
     - Full Pydantic coercion
   * - Custom validators
     - Skipped
     - Run
   * - Field constraints
     - Skipped
     - Enforced
   * - Speed
     - About 3x faster than ``to_pylist()`` + ``model_construct`` for flat schemas
     - About 2.5x slower than the fast path (JSON round-trip + validation)

How the fast path works
------------------------

When ``validate=False`` (the default), arrowmodel:

1. Walks each Arrow column in Rust, extracting values directly from Arrow
   buffers.
2. For each row, collects the extracted values as Python objects.
3. Builds each instance exactly as ``model_construct(**row_kwargs)`` would.

``model_construct`` is Pydantic's "I know what I'm doing" constructor. It
bypasses all validation -- no ``@field_validator`` decorators fire, no
``Field(ge=0)`` constraints are checked, no type coercion is attempted. The
values go straight into the model's ``__dict__``.

``model_construct`` itself is plain Python, and it matches every keyword
argument against every field's aliases on every call. Every row of a batch has
the same columns, so arrowmodel works out that matching once per batch and then
builds each instance in Rust: the same ``__dict__`` (in the same order), the
same ``model_fields_set``, defaults and default factories applied the same way,
and ``model_post_init`` still runs.

Some models still get a real ``model_construct`` call per row:

- Models that override ``model_construct``, including ``RootModel``.
- Nested models (the target of a struct column) whose result would depend on
  the values rather than just the column names: an ``AliasPath`` validation
  alias, extra struct fields kept by ``extra="allow"``, or struct fields that
  match no model field when the model has a ``model_post_init`` hook or private
  attributes. On the top-level model an ``AliasPath`` is still rejected with
  ``NotImplementedError`` (see :ref:`how-to-work-with-aliases`).

These models produce the same instances either way; they just do not get the
speed-up.

This is fast because:

- No intermediate Python dicts from ``to_pylist()``.
- No JSON serialisation or parsing.
- No Pydantic validation overhead.
- No per-row alias matching in Python.
- The Rust loop over Arrow buffers is tight and cache-friendly.

The trade-off is trust. You are trusting that the Arrow data matches the model's
schema exactly. If a column contains a string where the model expects an int,
you get a model instance with a string in an int field -- no error, no coercion,
just a quietly wrong value. The same goes for nulls: a NULL in a column whose
field is not ``Optional`` becomes ``None`` on the fast path, where
``validate=True`` would raise ``ValidationError``.

How the validated path works
-----------------------------

When ``validate=True``, arrowmodel:

1. Walks each Arrow column in Rust, extracting values directly from Arrow
   buffers (same as the fast path so far).
2. For each row, serialises the extracted values to a JSON byte string in Rust.
3. Calls ``model_validate_json(json_bytes)`` on the model class.

``model_validate_json`` is Pydantic's full validation entry point. It parses
the JSON, runs type coercion, fires custom validators, and enforces field
constraints. If anything fails, it raises ``ValidationError``.

.. mermaid::

   flowchart LR
       A["Arrow buffers"] --> B["Rust extraction"]
       B --> C{validate?}
       C -->|"False"| D["direct construction<br/>(same result as model_construct)"]
       C -->|"True"| E["JSON bytes"]
       E --> F["model_validate_json"]
       D --> G["Model instance"]
       F --> G

The JSON round-trip adds overhead, but it also gives you everything Pydantic
provides: coercion, validation, and custom logic.

When to use which
------------------

**Use the fast path when:**

- The data comes from a source you control (your own database, an internal
  service, a file your pipeline produced).
- You already validated the data upstream (pandera, Great Expectations, a
  schema-enforced database).
- You need maximum conversion speed and are willing to trust the data.

**Use the validated path when:**

- The data comes from an external or untrusted source (user uploads, third-party
  APIs, federated queries).
- Your model has custom validators that enforce business rules beyond type
  checking.
- You need Pydantic's type coercion (e.g., string ``"42"`` to int ``42``).
- Correctness matters more than speed for this particular code path.

.. _explanation-fast-vs-validated-performance:

Performance characteristics
----------------------------

From the project's benchmarks (`bench_convert.py
<https://github.com/s-layer/arrowmodel/blob/main/benchmarks/bench_convert.py>`__, recorded in `the results file
<https://github.com/s-layer/arrowmodel/blob/main/benchmarks/results-v1.0.1-direct-construct-2026-10-21.txt>`__) on a flat schema
of seven primitive and temporal columns, ``RecordBatch`` input, best of five
rounds:

.. list-table::
   :header-rows: 1
   :widths: 20 20 20 20 20

   * - Rows
     - arrowmodel (fast)
     - arrowmodel (``validate=True``)
     - ``to_pylist()`` + ``model_construct``
     - Fast path vs ``to_pylist()``
   * - 100
     - 142 µs
     - 378 µs
     - 488 µs
     - 3.4x
   * - 1,000
     - 1.6 ms
     - 3.9 ms
     - 5.0 ms
     - 3.1x
   * - 38,000
     - 80 ms
     - 193 ms
     - 274 ms
     - 3.4x

For flat schemas, the fast path is roughly 3x faster than the pure-Python
approach and roughly 2.5x faster than the validated path. ``Table`` input (10
batches) shows the same ratios.

For deeply nested models (10 levels of struct-in-struct with lists), the gap
widens. ``model_construct`` does not recurse, so the pure-Python baseline for
this benchmark calls it at every level to build the nested models, as arrowmodel
does. At 1,000 rows the fast path takes 8.3 ms, the validated path 17.5 ms and
the baseline 51.5 ms: the fast path is about 5-6x faster than the baseline and
about 2x faster than the validated path.

The validated path adds the cost of JSON serialisation and Pydantic's validation
pipeline on top of the extraction. For flat schemas it is still a little faster
than ``to_pylist()`` + ``model_construct``, and for the nested benchmark it is
about 3x faster than building the nested models that way. Either way it gives
you the same guarantees as calling ``model_validate_json`` directly.

A note on float NaN and Infinity
---------------------------------

JSON does not have representations for ``NaN`` or ``Infinity``. In the validated
path, arrowmodel converts these to JSON ``null``, which Pydantic then maps to
``None`` (for ``Optional[float]`` fields) or raises ``ValidationError`` (for
required ``float`` fields).

In the fast path, ``NaN`` and ``Infinity`` are passed through as-is -- your
model field will contain ``float('nan')`` or ``float('inf')``.
