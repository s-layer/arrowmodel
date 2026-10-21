.. meta::
   :description: Install arrowmodel and convert your first Arrow RecordBatch into Pydantic model instances in under 5 minutes.

.. _getting-started:

Getting Started
===============

In this tutorial you will install arrowmodel, define a Pydantic model, create
some Arrow data, and convert it into a list of typed model instances -- all in
under 5 minutes. By the end you will have a working feel for the library's core
loop: Arrow data in, Pydantic models out.

**Prerequisites**

- Python 3.11 or later
- Familiarity with Pydantic v2 ``BaseModel`` definitions
- Familiarity with pyarrow ``RecordBatch`` (or any Arrow-producing library)

Install arrowmodel
------------------

arrowmodel ships as a pre-built binary wheel for Linux (x86_64, aarch64), macOS on
Apple Silicon (arm64) and Windows (x64), so no Rust toolchain is needed on those
platforms. Anywhere else, including Intel Macs, the install builds from source and
needs Rust 1.83 or later.

.. tab-set::

   .. tab-item:: pip

      .. code-block:: bash

         pip install arrowmodel

   .. tab-item:: uv

      .. code-block:: bash

         uv add arrowmodel

You will also need pyarrow (or another Arrow-PyCapsule-compatible library such
as Polars) to create Arrow data. If you do not have it already:

.. tab-set::

   .. tab-item:: pip

      .. code-block:: bash

         pip install pyarrow

   .. tab-item:: uv

      .. code-block:: bash

         uv add pyarrow

Define a Pydantic model
-----------------------

Start by defining the shape of the data you expect. This is a standard Pydantic
v2 model -- nothing special yet.

.. code-block:: python

   from pydantic import BaseModel


   class User(BaseModel):
       id: int
       name: str
       email: str
       score: float

Each field name corresponds to a column name in the Arrow data you will
convert next.

Create some Arrow data
----------------------

Build a ``RecordBatch`` with columns that match your model fields.

.. code-block:: python

   import pyarrow as pa

   batch = pa.record_batch(
       {
           "id": [1, 2, 3],
           "name": ["Alice", "Bob", "Carol"],
           "email": ["alice@example.com", "bob@example.com", "carol@example.com"],
           "score": [9.5, 8.0, 7.3],
       }
   )

In a real application this data would come from a database query (ADBC, Flight
SQL), a Parquet file, or a Polars DataFrame. The ``RecordBatch`` is the common
hand-off point.

Convert to model instances
--------------------------

Now for the good part. Use :py:func:`~arrowmodel.model_convert` to turn the
batch into a list of ``User`` instances:

.. code-block:: python

   from arrowmodel import model_convert

   users = model_convert(User, batch)

   for user in users:
       print(f"{user.name} ({user.email}): {user.score}")

You should see:

.. code-block:: text

   Alice (alice@example.com): 9.5
   Bob (bob@example.com): 8.0
   Carol (carol@example.com): 7.3

That is the entire workflow. Arrow columns were mapped to model fields by name,
values were extracted directly from Arrow buffers in Rust, and each row became
a ``User`` instance with the same result as ``model_construct``. No
intermediate Python dicts were created.

.. tip::

   ``model_convert`` creates a fresh converter on every call. If you will
   convert many batches with the same model, look at
   :py:class:`~arrowmodel.ArrowModelConverter` or the
   :py:class:`~arrowmodel.ArrowModel` base class in the
   :ref:`how-to-choose-api-style` guide -- they compile the field mapping once
   and reuse it.

Try with a Table
~~~~~~~~~~~~~~~~

A ``Table`` is just multiple ``RecordBatch`` objects bundled together.
arrowmodel handles both transparently:

.. code-block:: python

   # Using the same User model and batch from above
   table = pa.Table.from_batches([batch, batch])
   users = model_convert(User, table)
   print(len(users))  # 6

What you learned
----------------

- arrowmodel installs as a binary wheel on the main platforms -- no Rust toolchain
  required there.
- You define a Pydantic ``BaseModel`` whose field names match Arrow column names.
- :py:func:`~arrowmodel.model_convert` converts a ``RecordBatch`` or ``Table``
  into a ``list`` of model instances in a single call.
- Conversion happens in Rust via the Arrow C Data Interface -- no intermediate
  Python dicts.

Next steps
----------

- :ref:`how-to-choose-api-style` -- pick between the convenience function,
  the converter class, or the ``ArrowModel`` base class.
- :ref:`how-to-use-validated-mode` -- enable full Pydantic validation when you
  need it.
- :ref:`explanation-fast-vs-validated` -- understand the performance trade-offs
  between the fast path and the validated path.
