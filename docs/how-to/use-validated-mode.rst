.. meta::
   :description: Enable full Pydantic validation when converting Arrow data to models, and understand the performance trade-off.

.. _how-to-use-validated-mode:

How to Use Validated Mode
=========================

By default, arrowmodel builds model instances the way Pydantic's
``model_construct`` does, without running validation. This is the fast path -- it trusts that
the Arrow data matches the model's schema. When you need Pydantic's full
validation pipeline (type coercion, custom validators, field constraints),
pass ``validate=True``.

Enable validated mode
---------------------

Every API style accepts a ``validate`` keyword argument:

.. code-block:: python

   import pyarrow as pa
   from pydantic import BaseModel
   from arrowmodel import ArrowModel, ArrowModelConverter, model_convert


   class User(BaseModel):
       id: int
       name: str
       score: float


   batch = pa.record_batch(
       {
           "id": [1, 2, 3],
           "name": ["Alice", "Bob", "Carol"],
           "score": [9.5, 8.0, 7.3],
       }
   )

   # Convenience function
   users = model_convert(User, batch, validate=True)

   # Converter object
   converter = ArrowModelConverter(User, validate=True)
   users = converter.convert(batch)


   # ArrowModel base class
   class ValidatedUser(ArrowModel):
       id: int
       name: str
       score: float


   users = ValidatedUser.convert(batch, validate=True)

All three produce the same result. The difference is that each row is
serialised to JSON in Rust and then passed through Pydantic's
``model_validate_json`` -- the same pipeline that runs when you call
``User.model_validate_json(json_bytes)`` in plain Pydantic.

What validation catches
-----------------------

The validated path runs Pydantic's full validation, including:

- **Type coercion** -- string ``"42"`` in an int field becomes ``42`` (or
  raises, depending on your ``model_config``).
- **Custom validators** -- ``@field_validator`` and ``@model_validator``
  decorators fire as expected.
- **Field constraints** -- ``Field(ge=0, le=100)`` bounds are enforced.
- **Strict mode** -- if the model uses ``model_config = ConfigDict(strict=True)``,
  no coercion is applied and type mismatches raise ``ValidationError``.

.. note::

   The fast path (``validate=False``) skips all of this. Values are placed
   directly into model fields as extracted from Arrow buffers. If a column
   contains unexpected data, you will not get a ``ValidationError`` -- you will
   get a model instance with the wrong value. See
   :ref:`explanation-fast-vs-validated` for a deeper discussion.

Performance trade-off
---------------------

Validation adds overhead because each row is serialised to JSON bytes and then
parsed by Pydantic. Expect conversion to be roughly 2-3x slower than the fast
path for flat schemas. See
:ref:`explanation-fast-vs-validated-performance` for the benchmark numbers.

The rule of thumb:

- **Fast path** (``validate=False``) -- use when the data source is trusted
  (your own database, an internal service, a file you wrote).
- **Validated path** (``validate=True``) -- use when the data source is
  untrusted or when you need custom validators to run (user uploads, third-party
  APIs, data you did not produce).

Handle validation errors
------------------------

When validation fails, Pydantic raises its standard ``ValidationError``. Catch
it the same way you would with ``model_validate_json``:

.. code-block:: python

   import pyarrow as pa
   from pydantic import BaseModel, ValidationError
   from arrowmodel import model_convert


   class StrictAge(BaseModel):
       age: int


   # Arrow column has strings where ints are expected
   bad_batch = pa.record_batch({"age": pa.array(["not_a_number"])})

   try:
       model_convert(StrictAge, bad_batch, validate=True)
   except ValidationError as exc:
       print(exc)
       # 1 validation error for StrictAge
       # age
       #   Input should be a valid integer, unable to parse string as an integer
       #   [type=int_parsing, input_value='not_a_number', input_type=str]

The error surfaces on the first row that fails validation. The remaining rows
are not processed.

.. warning::

   ``ValidationError`` is only raised in validated mode. The fast path does
   not validate and will not raise ``ValidationError`` regardless of the data.
