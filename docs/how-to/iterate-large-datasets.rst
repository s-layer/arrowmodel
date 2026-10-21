.. meta::
   :description: Use lazy iteration to process large Arrow Tables and streams one batch at a time without materialising every model in memory.

.. _how-to-iterate-large-datasets:

How to Iterate over Large Datasets
===================================

When your Arrow ``Table`` has millions of rows, or your query result arrives as
a stream, calling :py:meth:`~arrowmodel.ArrowModelConverter.convert` would
materialise every model instance at once and spike memory usage. The ``iter``
API yields instances lazily, one at a time, while only materialising one
``RecordBatch`` worth of models at a time.

Use ArrowModel.iter
-------------------

If you are using the :py:class:`~arrowmodel.ArrowModel` base class, call
the :py:meth:`~arrowmodel.ArrowModel.iter` classmethod:

.. code-block:: python

   import pyarrow as pa
   from arrowmodel import ArrowModel


   class User(ArrowModel):
       id: int
       name: str
       score: float


   # Imagine this Table came from a large Parquet file or ADBC query
   batch1 = pa.record_batch(
       {
           "id": [1, 2],
           "name": ["Alice", "Bob"],
           "score": [9.5, 8.0],
       }
   )
   batch2 = pa.record_batch(
       {
           "id": [3, 4],
           "name": ["Carol", "Dave"],
           "score": [7.3, 6.1],
       }
   )
   table = pa.Table.from_batches([batch1, batch2])

   for user in User.iter(table):
       print(f"{user.name}: {user.score}")

Use ArrowModelConverter.iter
----------------------------

When you are working with a converter instance:

.. code-block:: python

   from arrowmodel import ArrowModelConverter

   converter = ArrowModelConverter(User)

   for user in converter.iter(table):
       print(f"{user.name}: {user.score}")

Use model_iter
--------------

For one-off iteration in a REPL or script:

.. code-block:: python

   from arrowmodel import model_iter

   for user in model_iter(User, table):
       print(f"{user.name}: {user.score}")

How batch-at-a-time works
--------------------------

A ``Table`` holds one or more ``RecordBatch`` chunks, and an Arrow stream (a
``RecordBatchReader``, a Polars ``DataFrame``, a nanoarrow array stream)
hands out record batches one after another. When you call ``iter``,
arrowmodel processes one batch at a time:

1. Read the first ``RecordBatch`` and convert it into a list of models (in Rust).
2. Yield each model from that list.
3. When the list is exhausted, read and convert the next batch.
4. Repeat until all batches are consumed.

This means memory usage is proportional to the largest single batch, not the
total row count. If your Table or stream has 10 batches of 100,000 rows each,
only ~100,000 model instances exist in memory at any point.

.. note::

   For a ``RecordBatch`` input (a single batch), ``iter`` behaves identically
   to iterating over the result of ``convert``. The lazy advantage only appears
   with multi-batch Tables and streams.

.. _iterate-record-batch-reader:

Stream from a RecordBatchReader
-------------------------------

.. versionadded:: 1.1.0
   Arrow stream input, including ``RecordBatchReader`` and Polars ``DataFrame``.

Pass a ``pyarrow.RecordBatchReader`` straight to
:py:meth:`~arrowmodel.ArrowModel.iter`. arrowmodel pulls the next batch from
the reader only when it needs more rows, so memory stays bounded by one batch
even while the query result is still arriving:

.. code-block:: python

   import pyarrow as pa
   from arrowmodel import ArrowModel


   class User(ArrowModel):
       id: int
       name: str
       score: float


   batches = [
       pa.record_batch({"id": [1, 2], "name": ["Alice", "Bob"], "score": [9.5, 8.0]}),
       pa.record_batch({"id": [3, 4], "name": ["Carol", "Dave"], "score": [7.3, 6.1]}),
   ]
   reader = pa.RecordBatchReader.from_batches(batches[0].schema, batches)

   for user in User.iter(reader):
       print(f"{user.name}: {user.score}")
   # Alice: 9.5
   # Bob: 8.0
   # Carol: 7.3
   # Dave: 6.1

In practice the reader usually comes from a database driver. An ADBC cursor
gives you one with ``fetch_record_batch()``:

.. code-block:: python

   import adbc_driver_sqlite.dbapi as adbc_sqlite

   with adbc_sqlite.connect("users.db") as conn:
       with conn.cursor() as cur:
           cur.execute("SELECT id, name, score FROM users")
           for user in User.iter(cur.fetch_record_batch()):
               print(user)

Keep the cursor and connection open until you finish iterating. The reader
reads from the cursor, so closing it first makes the next read fail with
``ValueError``. To serve a reader from a web endpoint, see
:ref:`how-to-integrate-fastapi`.

:py:meth:`~arrowmodel.ArrowModel.convert` accepts a reader too, but it reads
every batch into one list, just as ``reader.read_all()`` would. Use ``iter``
when the point is to keep memory flat.

.. warning::

   A ``RecordBatchReader`` can be read only once. Converting it again returns
   an empty list, as ``reader.read_all()`` would. A ``Table`` or a Polars
   ``DataFrame`` hands out a fresh stream on every call, so you can convert
   those as many times as you like.

The schema is checked before any batch is read. If the reader is missing a
required column, ``iter`` raises ``ValueError`` on the first ``next()``,
without pulling a single batch from the source.

Polars ``DataFrame`` objects take the same route: ``User.iter(df)`` reads the
frame batch by batch, with no pyarrow involved.

Iterate with validation
-----------------------

Pass ``validate=True`` to run Pydantic validation on each row during iteration:

.. code-block:: python

   for user in User.iter(table, validate=True):
       print(user)

   # Or with the converter:
   validated_converter = ArrowModelConverter(User, validate=True)
   for user in validated_converter.iter(table):
       print(user)

Streaming pattern: write rows as you go
-----------------------------------------

Iteration pairs naturally with streaming writes. Process each model and discard
it before the next one arrives:

.. code-block:: python

   import json

   with open("users.jsonl", "w") as f:
       for user in User.iter(table):
           f.write(user.model_dump_json() + "\n")

Handle input errors
-------------------

``iter`` is a generator, so nothing runs until you ask for the first row.
Errors show up on the first ``next()`` (or the first pass of a ``for`` loop),
not on the line that calls ``iter``:

.. code-block:: python

   rows = User.iter(pa.array([1, 2, 3]))  # no error yet
   next(rows)
   # TypeError: arrowmodel converts a RecordBatch or Table (or any Arrow stream
   # of record batches, such as a RecordBatchReader or a Polars DataFrame);
   # got pyarrow.lib.Int64Array.

For some inputs, such as a ``ChunkedArray`` of numbers or a Polars ``Series``,
the message ends with a detail from the Arrow C Data Interface in parentheses.

- **TypeError naming your input type.** ``iter`` and ``convert`` take record
  batches: a ``RecordBatch``, a ``Table``, or a stream of record batches. A
  plain ``Array``, a non-struct ``ChunkedArray``, a Polars ``Series`` or
  ``LazyFrame``, or a list of dicts raises ``TypeError``. Call ``.collect()``
  on a ``LazyFrame`` first. A ``Series`` is one column, so it only converts
  (via ``.to_frame()``) for a model with one required field; otherwise use the
  ``DataFrame`` it came from.
- **ValueError about missing required columns.** The source's schema lacks a
  column for a required model field. The message lists the missing names and
  the columns that are available.
- **An empty result from a reader.** The reader was already read, by an
  earlier conversion or a ``for batch in reader`` loop. Get a new reader from
  the source.
