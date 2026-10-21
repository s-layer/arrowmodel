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
       in_stock: bool

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
       in_stock: bool


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
