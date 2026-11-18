.. meta::
   :description: Complete mapping of Arrow data types to Python types that arrowmodel produces, including temporal, binary, nested, and container types.

.. _explanation-type-mappings:

Arrow Type Mappings
====================

When arrowmodel extracts values from Arrow columns, each Arrow data type
maps to a specific Python type. This page is the definitive reference for
those mappings -- consult it when you need to know what Python type a given
Arrow column will produce in your Pydantic model.

Integer types
-------------

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Arrow Type
     - Python Type
     - Notes
   * - ``Int8``
     - ``int``
     -
   * - ``Int16``
     - ``int``
     -
   * - ``Int32``
     - ``int``
     -
   * - ``Int64``
     - ``int``
     -
   * - ``UInt8``
     - ``int``
     -
   * - ``UInt16``
     - ``int``
     -
   * - ``UInt32``
     - ``int``
     -
   * - ``UInt64``
     - ``int``
     -

All Arrow integer types produce Python ``int``. There is no overflow risk
since Python integers are arbitrary-precision.

Float types
-----------

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Arrow Type
     - Python Type
     - Notes
   * - ``Float16``
     - ``float``
     - Half-precision, widened to 64-bit
   * - ``Float32``
     - ``float``
     - Single-precision, widened to 64-bit
   * - ``Float64``
     - ``float``
     -

All float types produce Python ``float`` (64-bit). ``Float16`` and ``Float32``
values are widened, which may introduce small floating-point representation
differences.

Decimal types
-------------

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Arrow Type
     - Python Type
     - Notes
   * - ``Decimal32(precision, scale)``
     - ``decimal.Decimal``
     - Full precision preserved
   * - ``Decimal64(precision, scale)``
     - ``decimal.Decimal``
     - Full precision preserved
   * - ``Decimal128(precision, scale)``
     - ``decimal.Decimal``
     - Up to 38 digits
   * - ``Decimal256(precision, scale)``
     - ``decimal.Decimal``
     - Up to 76 digits

Decimal values are converted to ``decimal.Decimal`` with full precision -- no
intermediate float conversion that would lose significant digits.

Boolean and string types
------------------------

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Arrow Type
     - Python Type
     - Notes
   * - ``Boolean``
     - ``bool``
     - Exactly ``True`` or ``False``
   * - ``Utf8``
     - ``str``
     -
   * - ``LargeUtf8``
     - ``str``
     -
   * - ``Utf8View``
     - ``str``
     -

Date and time types
--------------------

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Arrow Type
     - Python Type
     - Notes
   * - ``Date32``
     - ``datetime.date``
     - Days since epoch
   * - ``Date64``
     - ``datetime.datetime``
     - Milliseconds since epoch
   * - ``Time32(second)``
     - ``datetime.time``
     -
   * - ``Time32(millisecond)``
     - ``datetime.time``
     - Milliseconds as microseconds
   * - ``Time64(microsecond)``
     - ``datetime.time``
     -
   * - ``Time64(nanosecond)``
     - ``datetime.time``
     - Nanoseconds truncated to microseconds

.. note::

   ``Date32`` produces ``datetime.date`` while ``Date64`` produces
   ``datetime.datetime``. This follows Arrow's own semantics: Date32 stores
   days, Date64 stores milliseconds (with time component).

Timestamp types
----------------

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Arrow Type
     - Python Type
     - Notes
   * - ``Timestamp(s, None)``
     - ``datetime.datetime`` (naive)
     -
   * - ``Timestamp(ms, None)``
     - ``datetime.datetime`` (naive)
     -
   * - ``Timestamp(us, None)``
     - ``datetime.datetime`` (naive)
     -
   * - ``Timestamp(ns, None)``
     - ``datetime.datetime`` (naive)
     - Nanoseconds truncated to microseconds
   * - ``Timestamp(*, tz)``
     - ``datetime.datetime`` (aware)
     - ``tzinfo`` set via ``ZoneInfo``

Timezone-aware timestamps use Python's ``ZoneInfo`` for the timezone. IANA
timezone strings (like ``"America/New_York"``) are preserved.

In validated mode the value reaches Pydantic as JSON, which carries an offset
but not a zone name. The model gets the same instant, wall clock and offset as
the fast path (``2024-01-15T05:30:00-05:00`` for a New York column), with a
fixed-offset ``tzinfo`` in place of the ``ZoneInfo``. One exception: Pydantic
cannot parse an offset with seconds, which some zones had before standard time
(Kolkata's local mean time was +05:53:20 until 1941). Those values arrive as
the same instant in UTC.

.. warning::

   Nanosecond-precision timestamps are truncated to microsecond precision
   because Python's ``datetime`` only supports microseconds. Nanoseconds are
   silently dropped, not rounded.

Duration type
-------------

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Arrow Type
     - Python Type
     - Notes
   * - ``Duration(s|ms|us|ns)``
     - ``datetime.timedelta``
     - All units converted to timedelta

Binary types
------------

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Arrow Type
     - Python Type
     - Notes
   * - ``Binary``
     - ``bytes``
     -
   * - ``LargeBinary``
     - ``bytes``
     -
   * - ``FixedSizeBinary(n)``
     - ``bytes``
     - Length ``n``
   * - ``BinaryView``
     - ``bytes``
     -

.. note::

   In validated mode (``validate=True``), binary data is sent as
   base64-encoded text in the JSON intermediate, because raw bytes are not
   valid JSON. Pydantic does not decode it unless you ask: a plain ``bytes``
   field receives the base64 text itself. To get the raw bytes back, set
   ``model_config = ConfigDict(val_json_bytes="base64")`` on the model, or
   annotate the field as ``pydantic.Base64Bytes``. The fast path always
   returns the raw bytes.

List and container types
------------------------

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Arrow Type
     - Python Type
     - Notes
   * - ``List(T)``
     - ``list``
     - Elements mapped recursively
   * - ``LargeList(T)``
     - ``list``
     - Same as ``List``
   * - ``FixedSizeList(T, n)``
     - ``list``
     - Always ``n`` elements
   * - ``Map(K, V)``
     - ``list[tuple[K, V]]``
     - List of key-value pairs

Nested list types (``List(List(Int64))``) produce nested Python lists
(``list[list[int]]``).

Container elements may themselves be nested models. A ``List(Struct)`` column
whose field is annotated ``list[MyModel]`` produces a list of ``MyModel``
instances; this threads recursively, so ``list[list[MyModel]]``,
``FixedSizeList(MyModel)``, and struct fields containing ``list[MyModel]`` all
work. See :ref:`how-to-convert-nested-models`.

.. note::

   ``Map`` columns are materialised as a **list of** ``(key, value)`` **pairs**,
   not a ``dict``. This is lossless for Arrow Maps, whose keys may be non-string
   or duplicated (neither of which a Python ``dict`` or JSON object can
   represent). Annotate a Map field as ``list[tuple[K, V]]``; a ``dict`` /
   ``Mapping`` annotation over a Map column raises ``TypeError`` at
   ``convert()`` time. Map values may be nested models
   (``list[tuple[str, MyModel]]``).

Struct type
-----------

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Arrow Type
     - Python Type
     - Notes
   * - ``Struct({fields})``
     - Nested ``BaseModel`` instance
     - See :ref:`how-to-convert-nested-models`

A ``Struct`` column is converted to a nested Pydantic model when the
corresponding field is annotated as a ``BaseModel`` subclass. A null struct
produces ``None``.

Dictionary-encoded columns
--------------------------

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Arrow Type
     - Python Type
     - Notes
   * - ``Dictionary(index, value)``
     - Same as ``value`` type
     - Transparent decoding

Dictionary-encoded columns are transparently decoded. A
``Dictionary(Int32, Utf8)`` column produces ``str`` values, the same as a
plain ``Utf8`` column. This holds at any depth: a dictionary-encoded list item,
struct member or map entry decodes the same way, so a Polars
``List(Categorical)`` column gives ``list[str]``. Run-end encoded data is
unpacked the same way.

Interval types
--------------

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Arrow Type
     - Python Type
     - Notes
   * - ``Interval(YearMonth)``
     - ``tuple[int, int, int]``
     - ``(months, 0, 0)``
   * - ``Interval(DayTime)``
     - ``tuple[int, int, int]``
     - ``(0, days, nanos)``
   * - ``Interval(MonthDayNano)``
     - ``tuple[int, int, int]``
     - ``(months, days, nanos)``

All interval types are normalised to a 3-tuple of ``(months, days, nanoseconds)``
for a consistent representation.

Union types
-----------

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Arrow Type
     - Python Type
     - Notes
   * - ``SparseUnion``
     - Value from active child
     - Type varies per row
   * - ``DenseUnion``
     - Value from active child
     - Type varies per row

Union columns produce the Python value from the active child array for each
row. Use a Pydantic ``Union`` annotation (e.g., ``int | str``) to accept the
varying types.

Run-end encoded columns
-----------------------

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Arrow Type
     - Python Type
     - Notes
   * - ``RunEndEncoded``
     - Same as value type
     - Transparent decoding

Run-end encoded columns are transparently unpacked before extraction. A
run-end encoded ``Utf8`` column produces ``str`` values.

Null type
---------

.. list-table::
   :header-rows: 1
   :widths: 40 40 20

   * - Arrow Type
     - Python Type
     - Notes
   * - ``Null``
     - ``None``
     - Every row is ``None``

Null handling
-------------

For any nullable column (regardless of type), a null value produces ``None``
in the model instance. The corresponding Pydantic field should be typed as
``Optional`` (e.g., ``str | None = None``) to accept the ``None`` value.

Fields with default values that are missing from the Arrow schema use their
Pydantic default. Required fields that are missing from the Arrow schema
raise ``ValueError`` at conversion time.
