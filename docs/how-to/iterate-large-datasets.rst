.. meta::
   :description: Use lazy iteration to process large Arrow Tables one batch at a time without materialising every model in memory.

.. _how-to-iterate-large-datasets:

How to Iterate over Large Datasets
===================================

When your Arrow ``Table`` has millions of rows, calling
:py:meth:`~arrowmodel.ArrowModelConverter.convert` would materialise every
model instance at once and spike memory usage. The ``iter`` API yields
instances lazily, one at a time, while only materialising one ``RecordBatch``
worth of models at a time.

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

Arrow ``Table`` objects contain one or more ``RecordBatch`` chunks. When you
call ``iter``, arrowmodel processes one batch at a time:

1. Convert the first ``RecordBatch`` into a list of models (in Rust).
2. Yield each model from that list.
3. When the list is exhausted, convert the next batch.
4. Repeat until all batches are consumed.

This means memory usage is proportional to the largest single batch in the
Table, not the total row count. If your Table has 10 batches of 100,000 rows
each, only ~100,000 model instances exist in memory at any point.

.. note::

   For a ``RecordBatch`` input (a single batch, not a Table), ``iter`` behaves
   identically to iterating over the result of ``convert``. The lazy advantage
   only appears with multi-batch Tables.

Stream from a RecordBatchReader
-------------------------------

``convert`` and ``iter`` accept a ``RecordBatch`` or a ``Table``, not a
``pyarrow.RecordBatchReader``. Passing a reader raises ``ValueError``. To keep
memory bounded while a query streams in, loop over the reader yourself and
convert each batch as it arrives:

.. code-block:: python

   # ADBC cursors expose a reader via fetch_record_batch()
   reader = cur.fetch_record_batch()

   for batch in reader:
       for user in User.iter(batch):
           print(user)

Calling ``reader.read_all()`` instead gives you a ``Table`` you can pass
directly, at the cost of holding the whole result in memory.

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
