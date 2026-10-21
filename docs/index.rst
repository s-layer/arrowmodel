.. meta::
   :description: arrowmodel converts Arrow RecordBatch and Table data directly to Pydantic v2 model instances, skipping the intermediate dict step.

.. _overview:

Overview
========

**arrowmodel** converts Apache Arrow ``RecordBatch`` and ``Table`` objects directly
into Pydantic v2 model instances -- no intermediate Python dicts, no two-step
materialisation. A tight Rust loop walks the Arrow buffers via the Arrow C Data
Interface and hands you back typed models, roughly 3x faster than
``to_pylist()`` + ``model_construct`` for flat schemas.

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

.. grid:: 1 2 3 3

   .. grid-item-card:: Dict-Free Conversion
      :text-align: center

      Skips ``to_pylist()`` entirely. A single Rust loop builds each model
      straight from the Arrow buffers, exactly as ``model_construct`` would.

   .. grid-item-card:: ~3x Faster
      :text-align: center

      Roughly three times as fast as the pure-Python approach for flat schemas,
      with less memory allocation pressure.

   .. grid-item-card:: Any Arrow Source
      :text-align: center

      Accepts any Arrow-PyCapsule-compatible input -- pyarrow, Polars,
      nanoarrow -- via the Arrow C Data Interface.

.. grid:: 1 2 3 3

   .. grid-item-card:: Pydantic Aliases
      :text-align: center

      Full alias resolution: ``validation_alias``, ``alias``, field name,
      and ``populate_by_name`` / ``validate_by_name``.

   .. grid-item-card:: Nested Models
      :text-align: center

      Arrow ``Struct`` columns map automatically to nested Pydantic models,
      including ``Optional`` structs and deeply nested hierarchies.

   .. grid-item-card:: Validated Mode
      :text-align: center

      Choose between the fast path (``model_construct`` semantics, no validation)
      and the validated path (``model_validate_json``, full Pydantic checks).

Getting Started
---------------

Install with pip or uv:

.. tab-set::

   .. tab-item:: pip

      .. code-block:: bash

         pip install arrowmodel

   .. tab-item:: uv

      .. code-block:: bash

         uv add arrowmodel

Pre-built wheels are provided for Linux (x86_64, aarch64), macOS on Apple Silicon
(arm64) and Windows (x64), so no Rust toolchain is needed there. On any other
platform, including Intel Macs, pip builds from source and needs Rust 1.83 or later
(install it with `rustup <https://rustup.rs>`_).

Then head to the :ref:`getting-started` tutorial to convert your first batch.

.. button-ref:: getting-started
   :color: primary
   :expand:

   Get Started

.. toctree::
   :hidden:

   tutorials/index
   how-to/index
   explanation/index
   reference/index
