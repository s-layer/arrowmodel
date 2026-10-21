.. meta::
   :description: Full API reference for arrowmodel -- class signatures, method parameters, return types, and exceptions for every public symbol.

.. _reference-api:

API Reference
=============

.. contents:: On this page
   :local:
   :depth: 2

----

ArrowModel
----------

.. code-block:: python

   class ArrowModel(pydantic.BaseModel): ...

Pydantic ``BaseModel`` subclass with Arrow conversion classmethods. Subclassing
``ArrowModel`` instead of ``BaseModel`` gives your model
:py:meth:`~arrowmodel.ArrowModel.convert` and
:py:meth:`~arrowmodel.ArrowModel.iter` classmethods with no extra setup.

An :py:class:`~arrowmodel.ArrowModelConverter` is compiled automatically when
the subclass is defined (via ``__pydantic_init_subclass__``). The converter is
cached as a class variable and reused on every call, so the alias-aware field
mapping is built once, not on each conversion.

``ArrowModel`` itself has no fields and no converter -- only concrete subclasses
with at least one field get a converter.

.. code-block:: python

   import pyarrow as pa
   from arrowmodel import ArrowModel


   class User(ArrowModel):
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

   users = User.convert(batch)
   # [User(id=1, name='Alice', score=9.5), ...]

Everything you know about ``BaseModel`` -- validators, serializers,
``model_config``, ``Field()`` -- still works.

ArrowModel.convert
~~~~~~~~~~~~~~~~~~

.. code-block:: python

   @classmethod
   def convert(
       cls,
       data: pa.RecordBatch | pa.Table,
       *,
       validate: bool = False,
   ) -> list[Self]: ...

Convert an Arrow ``RecordBatch`` or ``Table`` to a list of model instances.

**Parameters:**

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Name
     - Type
     - Description
   * - ``data``
     - ``pa.RecordBatch | pa.Table``
     - Arrow data to convert. Accepts any object that exposes the
       `Arrow PyCapsule Interface <https://arrow.apache.org/docs/format/CDataInterface/PyCapsuleInterface.html>`_
       (pyarrow, Polars, nanoarrow).
   * - ``validate``
     - ``bool`` (keyword-only, default ``False``)
     - When ``False`` (the default), uses the **fast path**, which builds
       instances as ``model_construct`` would and runs no Pydantic
       validation. When ``True``, uses the **validated path** via
       ``model_validate_json``, with full Pydantic validation including
       custom validators.

**Returns:** ``list[Self]`` -- a list of model instances. The list type matches
the subclass: ``User.convert(batch)`` returns ``list[User]``.

**Raises:**

- ``ValueError`` -- Arrow schema is missing one or more required model fields.
  The error message lists the missing column names and the available columns.
- ``TypeError`` -- A field annotated as a ``dict`` or ``Mapping`` maps to an
  Arrow ``Map`` column. Map columns convert to ``list[tuple[K, V]]``; annotate
  the field that way instead.

.. code-block:: python

   # Fast path (default) -- no Pydantic validation
   users = User.convert(batch)

   # Validated path -- runs validators, custom types, etc.
   users = User.convert(batch, validate=True)

ArrowModel.iter
~~~~~~~~~~~~~~~

.. code-block:: python

   @classmethod
   def iter(
       cls,
       data: pa.RecordBatch | pa.Table,
       *,
       validate: bool = False,
   ) -> Iterator[Self]: ...

Lazily yield individual model instances from Arrow data.

For ``Table`` input with multiple ``RecordBatch`` chunks, only one batch's
worth of instances is materialised in memory at a time. Each instance is yielded
individually. For ``RecordBatch`` input, behaviour is equivalent to iterating
over :py:meth:`~arrowmodel.ArrowModel.convert` results.

**Parameters:**

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Name
     - Type
     - Description
   * - ``data``
     - ``pa.RecordBatch | pa.Table``
     - Arrow data to convert.
   * - ``validate``
     - ``bool`` (keyword-only, default ``False``)
     - When ``False``, uses the fast path (``model_construct`` semantics).
       When ``True``, uses the validated path (``model_validate_json``).

**Yields:** ``Self`` -- model instances one at a time.

**Raises:**

- ``ValueError`` -- Arrow schema is missing one or more required model fields.
- ``TypeError`` -- A field annotated as a ``dict`` or ``Mapping`` maps to an
  Arrow ``Map`` column. Map columns convert to ``list[tuple[K, V]]``; annotate
  the field that way instead.

.. code-block:: python

   for user in User.iter(table):
       print(user.name)

   # With validation
   for user in User.iter(table, validate=True):
       process(user)

----

ArrowModelConverter
-------------------

.. code-block:: python

   class ArrowModelConverter: ...

Stateful converter that maps Arrow data to Pydantic model instances. The
alias-aware field mapping is compiled once at construction time and reused
across all subsequent :py:meth:`~arrowmodel.ArrowModelConverter.convert` and
:py:meth:`~arrowmodel.ArrowModelConverter.iter` calls.

Use ``ArrowModelConverter`` directly when you cannot change the model's base
class, or when you need separate converters with different ``validate``
settings for the same model.

ArrowModelConverter.__init__
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def __init__(
       self,
       model_class: type[BaseModel],
       *,
       validate: bool = False,
   ) -> None: ...

Create a converter for the given Pydantic model class.

**Parameters:**

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Name
     - Type
     - Description
   * - ``model_class``
     - ``type[BaseModel]``
     - The Pydantic v2 model class to convert Arrow data into.
   * - ``validate``
     - ``bool`` (keyword-only, default ``False``)
     - When ``False``, the converter uses the fast path
       (``model_construct`` semantics) on every conversion call. When
       ``True``, it uses the validated path (``model_validate_json``).

**Raises:**

- ``NotImplementedError`` -- The model uses ``AliasPath`` or ``AliasChoices``
  as a ``validation_alias`` on any field. These alias types are not supported.
- ``NotImplementedError`` -- The model has an ``AliasGenerator`` set in
  ``model_config``. Use explicit per-field aliases instead.

.. code-block:: python

   from pydantic import BaseModel
   from arrowmodel import ArrowModelConverter


   class Order(BaseModel):
       order_id: int
       total: float
       status: str


   converter = ArrowModelConverter(Order)

   # With validation enabled
   validating_converter = ArrowModelConverter(Order, validate=True)

ArrowModelConverter.convert
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def convert(self, data: pa.RecordBatch | pa.Table) -> list[BaseModel]: ...

Convert Arrow data to a list of Pydantic model instances.

**Parameters:**

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Name
     - Type
     - Description
   * - ``data``
     - ``pa.RecordBatch | pa.Table``
     - Arrow data to convert. The schema is cross-referenced against the
       model's field mapping on each call (column indices may differ
       between batches with the same logical schema).

**Returns:** ``list[BaseModel]`` -- a list of model instances. Returns an empty
list for empty input.

**Raises:**

- ``ValueError`` -- Arrow schema is missing one or more required model fields.
  Optional fields with defaults are silently skipped when absent.
  Extra Arrow columns not present in the model are silently ignored.
- ``TypeError`` -- A field annotated as a ``dict`` or ``Mapping`` maps to an
  Arrow ``Map`` column. Map columns convert to ``list[tuple[K, V]]``; annotate
  the field that way instead.

.. code-block:: python

   import pyarrow as pa
   from pydantic import BaseModel
   from arrowmodel import ArrowModelConverter


   class Order(BaseModel):
       order_id: int
       total: float
       status: str


   converter = ArrowModelConverter(Order)
   batch = pa.record_batch(
       {
           "order_id": [101, 102],
           "total": [29.99, 59.50],
           "status": ["shipped", "pending"],
       }
   )

   orders = converter.convert(batch)
   # [Order(order_id=101, total=29.99, status='shipped'), ...]

   # Tables work too -- multiple batches are processed internally
   table = pa.Table.from_batches([batch, another_batch])
   all_orders = converter.convert(table)

ArrowModelConverter.iter
~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

   def iter(self, data: pa.RecordBatch | pa.Table) -> Iterator[BaseModel]: ...

Lazily yield individual model instances from Arrow data.

For ``Table`` input with multiple chunks, only one ``RecordBatch`` is
materialised at a time. For ``RecordBatch`` input, behaviour is equivalent to
iterating over :py:meth:`~arrowmodel.ArrowModelConverter.convert` results.

**Parameters:**

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Name
     - Type
     - Description
   * - ``data``
     - ``pa.RecordBatch | pa.Table``
     - Arrow data to convert.

**Yields:** ``BaseModel`` -- model instances one at a time.

**Raises:**

- ``ValueError`` -- Arrow schema is missing one or more required model fields.
- ``TypeError`` -- A field annotated as a ``dict`` or ``Mapping`` maps to an
  Arrow ``Map`` column. Map columns convert to ``list[tuple[K, V]]``; annotate
  the field that way instead.

.. code-block:: python

   # Using converter from above
   for order in converter.iter(table):
       print(f"Order {order.order_id}: {order.status}")

----

model_convert
-------------

.. code-block:: python

   def model_convert(
       model_class: type[BaseModel],
       data: pa.RecordBatch | pa.Table,
       *,
       validate: bool = False,
   ) -> list[BaseModel]: ...

One-shot conversion from Arrow data to Pydantic model instances. Creates a
temporary :py:class:`~arrowmodel.ArrowModelConverter` internally, calls
:py:meth:`~arrowmodel.ArrowModelConverter.convert`, and discards the converter.

**Parameters:**

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Name
     - Type
     - Description
   * - ``model_class``
     - ``type[BaseModel]``
     - The Pydantic v2 model class to convert into.
   * - ``data``
     - ``pa.RecordBatch | pa.Table``
     - Arrow data to convert.
   * - ``validate``
     - ``bool`` (keyword-only, default ``False``)
     - When ``False``, uses the fast path (``model_construct`` semantics).
       When ``True``, uses the validated path (``model_validate_json``).

**Returns:** ``list[BaseModel]`` -- a list of model instances.

**Raises:**

- ``ValueError`` -- Arrow schema is missing required model fields.
- ``TypeError`` -- A field annotated as a ``dict`` or ``Mapping`` maps to an
  Arrow ``Map`` column. Map columns convert to ``list[tuple[K, V]]``; annotate
  the field that way instead.
- ``NotImplementedError`` -- The model uses unsupported alias types
  (``AliasPath``, ``AliasChoices``, or ``AliasGenerator``).

.. warning::

   Every call creates a fresh converter. If you are converting multiple batches
   with the same model, create an :py:class:`~arrowmodel.ArrowModelConverter`
   or use :py:class:`~arrowmodel.ArrowModel` to avoid recompiling the field
   mapping each time.

.. code-block:: python

   from pydantic import BaseModel
   from arrowmodel import model_convert


   class User(BaseModel):
       id: int
       name: str


   batch = pa.record_batch({"id": [1, 2], "name": ["Alice", "Bob"]})
   users = model_convert(User, batch)
   # [User(id=1, name='Alice'), User(id=2, name='Bob')]

   # With validation
   users = model_convert(User, batch, validate=True)

----

model_iter
----------

.. code-block:: python

   def model_iter(
       model_class: type[BaseModel],
       data: pa.RecordBatch | pa.Table,
       *,
       validate: bool = False,
   ) -> Iterator[BaseModel]: ...

One-shot lazy iteration from Arrow data to Pydantic model instances. Creates a
temporary :py:class:`~arrowmodel.ArrowModelConverter` internally, calls
:py:meth:`~arrowmodel.ArrowModelConverter.iter`, and discards the converter.

**Parameters:**

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Name
     - Type
     - Description
   * - ``model_class``
     - ``type[BaseModel]``
     - The Pydantic v2 model class to convert into.
   * - ``data``
     - ``pa.RecordBatch | pa.Table``
     - Arrow data to convert.
   * - ``validate``
     - ``bool`` (keyword-only, default ``False``)
     - When ``False``, uses the fast path (``model_construct`` semantics).
       When ``True``, uses the validated path (``model_validate_json``).

**Yields:** ``BaseModel`` -- model instances one at a time.

**Raises:**

- ``ValueError`` -- Arrow schema is missing required model fields.
- ``TypeError`` -- A field annotated as a ``dict`` or ``Mapping`` maps to an
  Arrow ``Map`` column. Map columns convert to ``list[tuple[K, V]]``; annotate
  the field that way instead.
- ``NotImplementedError`` -- The model uses unsupported alias types
  (``AliasPath``, ``AliasChoices``, or ``AliasGenerator``).

.. warning::

   Every call creates a fresh converter. For repeated iteration with the same
   model, create an :py:class:`~arrowmodel.ArrowModelConverter` or use
   :py:class:`~arrowmodel.ArrowModel`.

.. code-block:: python

   from pydantic import BaseModel
   from arrowmodel import model_iter


   class User(BaseModel):
       id: int
       name: str


   table = pa.table({"id": [1, 2, 3], "name": ["Alice", "Bob", "Carol"]})
   for user in model_iter(User, table):
       print(user.name)

----

_build_field_map
----------------

.. code-block:: python

   def _build_field_map(model_class: type[BaseModel]) -> dict[str, str]: ...

Build the ``{arrow_column_name: pydantic_field_name}`` mapping for a Pydantic
model class. This is the alias resolution logic that
:py:class:`~arrowmodel.ArrowModelConverter` uses internally.

You rarely need to call this directly. It is exposed for introspection and
debugging -- for example, to inspect how arrowmodel will map your Arrow column
names before running a conversion.

**Alias resolution priority:**

1. ``validation_alias`` (if set and is a ``str``)
2. ``alias`` (if set)
3. Field name (fallback)

When ``populate_by_name=True`` or ``validate_by_name=True`` is set in
``model_config``, both the alias and the field name are accepted as Arrow
column names. The alias entry takes priority if both are present in the Arrow
schema.

**Parameters:**

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Name
     - Type
     - Description
   * - ``model_class``
     - ``type[BaseModel]``
     - The Pydantic v2 model class to build the field map for.

**Returns:** ``dict[str, str]`` -- a dictionary mapping Arrow column names
(lookup keys) to Pydantic field names (values).

**Raises:**

- ``NotImplementedError`` -- A field uses ``AliasPath`` or ``AliasChoices`` as
  its ``validation_alias``.
- ``NotImplementedError`` -- The model has an ``AliasGenerator`` set in
  ``model_config``.

.. code-block:: python

   from pydantic import BaseModel, Field
   from arrowmodel import _build_field_map


   class Event(BaseModel):
       event_id: int = Field(validation_alias="eventId")
       event_type: str = Field(alias="type")
       payload: str


   field_map = _build_field_map(Event)
   # {'eventId': 'event_id', 'type': 'event_type', 'payload': 'payload'}

.. code-block:: python

   from pydantic import BaseModel, ConfigDict, Field
   from arrowmodel import _build_field_map


   class FlexModel(BaseModel):
       model_config = ConfigDict(populate_by_name=True)
       user_id: int = Field(alias="userId")


   field_map = _build_field_map(FlexModel)
   # {'userId': 'user_id', 'user_id': 'user_id'}
   # Both 'userId' and 'user_id' Arrow columns will resolve to the user_id field.

----

_get_nested_model
-----------------

.. code-block:: python

   def _get_nested_model(annotation: Any) -> type[BaseModel] | None: ...

Extract a nested ``BaseModel`` subclass from a Pydantic field annotation.
Used internally by :py:class:`~arrowmodel.ArrowModelConverter` to detect
which fields correspond to Arrow ``Struct`` columns that should be converted
into nested model instances.

You rarely need to call this directly. It is exposed for introspection.

**Handles:**

- Direct ``BaseModel`` subclass annotations (``NestedModel``) -- returns the class.
- ``Optional[NestedModel]`` (``Union[NestedModel, None]``) -- returns the class.
- Non-model types (``int``, ``str``, ``list[int]``, etc.) -- returns ``None``.

**Parameters:**

.. list-table::
   :header-rows: 1
   :widths: 20 20 60

   * - Name
     - Type
     - Description
   * - ``annotation``
     - ``Any``
     - A type annotation from a Pydantic model field. Typically accessed via
       ``model_class.model_fields[field_name].annotation``.

**Returns:** ``type[BaseModel] | None`` -- the nested model class if the
annotation is or contains a ``BaseModel`` subclass, otherwise ``None``.

.. code-block:: python

   from pydantic import BaseModel
   from arrowmodel import _get_nested_model


   class Address(BaseModel):
       city: str
       zip_code: str


   class User(BaseModel):
       name: str
       address: Address
       email: str | None = None


   _get_nested_model(User.model_fields["address"].annotation)
   # <class 'Address'>

   _get_nested_model(User.model_fields["name"].annotation)
   # None

   _get_nested_model(User.model_fields["email"].annotation)
   # None
