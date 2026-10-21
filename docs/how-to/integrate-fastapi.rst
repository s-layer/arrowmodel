.. meta::
   :description: Wire arrowmodel into FastAPI endpoints to serve Arrow query results as typed JSON responses.

.. _how-to-integrate-fastapi:

How to Integrate with FastAPI
=============================

The classic arrowmodel use case: your database returns Arrow data and your API
needs to serve it as JSON. This guide shows how to wire arrowmodel into a
FastAPI endpoint so that Arrow query results go straight to typed Pydantic
response models with no intermediate dict layer.

**Prerequisites:** Working FastAPI project, a database that returns Arrow data
(ADBC, Flight SQL, or any Arrow-PyCapsule source).

Define your response model
--------------------------

Start with a Pydantic model that matches the columns your query returns. Using
:py:class:`~arrowmodel.ArrowModel` as the base class gives you the
``convert`` classmethod for free:

.. code-block:: python

   from arrowmodel import ArrowModel


   class Product(ArrowModel):
       id: int
       name: str
       price: float
       in_stock: int

``in_stock`` is an ``int`` because the examples on this page use SQLite, which
has no boolean type: the driver returns the column as ``int64`` holding ``1``
or ``0``. The fast path does no type coercion, so each field must have the
Python type its Arrow column converts to (see :ref:`explanation-type-mappings`).
Declared as ``bool``, ``in_stock`` would hold ``1`` and ``0``, reach the client
as ``1`` and ``0``, and make Pydantic log a serialisation warning for every row.
Against a database with a real boolean column, such as PostgreSQL, declare
``in_stock: bool``. To turn SQLite's ``1``/``0`` into ``True``/``False``, use
``validate=True`` (see :ref:`how-to-use-validated-mode`).

Build the endpoint
------------------

Query the database, convert the Arrow result to models, and return them.
FastAPI handles the JSON serialisation of the Pydantic model list.

.. code-block:: python

   import adbc_driver_sqlite.dbapi as adbc_sqlite
   from fastapi import FastAPI

   app = FastAPI()


   @app.get("/products", response_model=list[Product])
   def list_products():
       with adbc_sqlite.connect("products.db") as conn:
           with conn.cursor() as cur:
               cur.execute("SELECT id, name, price, in_stock FROM products")
               table = cur.fetch_arrow_table()

       return Product.convert(table)

That is the core pattern. ``fetch_arrow_table()`` returns a pyarrow ``Table``,
:py:meth:`~arrowmodel.ArrowModel.convert` turns it into a ``list[Product]``,
and FastAPI serialises that list to JSON.

Reuse converters across requests
---------------------------------

:py:class:`~arrowmodel.ArrowModel` caches the converter at class definition
time, so ``Product.convert(table)`` already reuses the compiled field mapping.
No extra setup needed.

If you are using :py:class:`~arrowmodel.ArrowModelConverter` directly (because
the model cannot subclass ``ArrowModel``), create the converter once at module
level:

.. code-block:: python

   from pydantic import BaseModel
   from arrowmodel import ArrowModelConverter


   class Product(BaseModel):
       id: int
       name: str
       price: float
       in_stock: int


   product_converter = ArrowModelConverter(Product)


   @app.get("/products", response_model=list[Product])
   def list_products():
       with adbc_sqlite.connect("products.db") as conn:
           with conn.cursor() as cur:
               cur.execute("SELECT id, name, price, in_stock FROM products")
               table = cur.fetch_arrow_table()

       return product_converter.convert(table)

Use validated mode for untrusted data
--------------------------------------

If the Arrow data comes from an external source -- a third-party Flight SQL
server, a user-uploaded Parquet file, or a federated query -- enable validation
to run Pydantic's full pipeline. FastAPI cannot take a pyarrow ``Table`` as a
request body, so accept the upload as a file and read it with pyarrow:

.. code-block:: python

   import pyarrow.parquet as pq
   from fastapi import UploadFile


   @app.post("/products/import", response_model=list[Product])
   def import_products(file: UploadFile):
       table = pq.read_table(file.file)
       # Untrusted data: run full Pydantic validation
       return Product.convert(table, validate=True)

See :ref:`how-to-use-validated-mode` for more on what the validated path catches
and the performance trade-off.

.. note::

   ``response_model`` does not re-validate what you return. FastAPI serialises
   model instances of the declared class as they are, so a wrong value from the
   fast path reaches the client as JSON, with at most a Pydantic serialisation
   warning in your logs. Use ``validate=True`` when the data needs checking;
   ``response_model`` will not do it for you.

Stream large results
--------------------

For endpoints that return large datasets, use
:py:meth:`~arrowmodel.ArrowModel.iter` with FastAPI's ``StreamingResponse``
to avoid materialising the entire list in memory:

.. code-block:: python

   from fastapi.responses import StreamingResponse


   @app.get("/products/export")
   def export_products():
       with adbc_sqlite.connect("products.db") as conn:
           with conn.cursor() as cur:
               cur.execute("SELECT id, name, price, in_stock FROM products")
               table = cur.fetch_arrow_table()

       def generate():
           for product in Product.iter(table):
               yield product.model_dump_json() + "\n"

       return StreamingResponse(generate(), media_type="application/x-ndjson")

This still loads the whole query result into a ``Table`` before the first row
goes out. To keep only one batch in memory, pass the cursor's
``fetch_record_batch()`` reader straight to ``Product.iter()``. The reader
reads from the open cursor, so open the connection and cursor *inside* the
generator. That way they stay open while ``StreamingResponse`` sends rows,
and close when the last row has gone:

.. code-block:: python

   @app.get("/products/export")
   def export_products():
       def generate():
           with adbc_sqlite.connect("products.db") as conn:
               with conn.cursor() as cur:
                   cur.execute("SELECT id, name, price, in_stock FROM products")
                   for product in Product.iter(cur.fetch_record_batch()):
                       yield product.model_dump_json() + "\n"

       return StreamingResponse(generate(), media_type="application/x-ndjson")

.. warning::

   Opening the connection in the endpoint and returning the generator, as the
   first example does with its ``Table``, does not work with a reader. The
   ``with`` blocks close the cursor before ``StreamingResponse`` starts
   pulling rows, and the first read fails with ``ValueError: Failed to read
   the next Arrow record batch``.

   Errors also arrive late with streaming. ``StreamingResponse`` sends the
   ``200`` status before it runs the generator, so a missing column
   (``ValueError``) or a failed query shows up as a cut-off response rather
   than an error status. Check the query against your model with the
   non-streaming endpoint first.

See :ref:`how-to-iterate-large-datasets` for how batch-at-a-time iteration
works and what else ``iter`` accepts.
