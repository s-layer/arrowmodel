.. meta::
   :description: Task-oriented guides for common arrowmodel workflows: API styles, validation, aliases, nested models, iteration, and integrations.

.. _how-to-guides:

How-To Guides
=============

Goal-oriented guides for specific tasks. Each one solves a particular problem
and assumes you already know the basics from the :ref:`getting-started` tutorial.

**Choosing your API**

- :ref:`how-to-choose-api-style` -- Pick between the ArrowModel base class, ArrowModelConverter, and convenience functions.

**Conversion features**

- :ref:`how-to-use-validated-mode` -- Enable full Pydantic validation during conversion and understand the performance trade-off.
- :ref:`how-to-work-with-aliases` -- Map Arrow column names to Pydantic field names using aliases, validation_alias, and populate_by_name.
- :ref:`how-to-convert-nested-models` -- Convert Arrow Struct columns into nested Pydantic models, including optional and deeply nested types.
- :ref:`how-to-iterate-large-datasets` -- Process large Tables and streams such as a RecordBatchReader lazily, without materialising every model instance in memory.

**Integrations**

- :ref:`how-to-integrate-fastapi` -- Serve Arrow query results as typed JSON responses through FastAPI endpoints.
- :ref:`how-to-use-with-poldantic` -- Fit arrowmodel into a poldantic workflow with Polars DataFrames.
- :ref:`how-to-use-with-pandera` -- Convert pandera-validated Arrow data into Pydantic model instances.

.. toctree::
   :hidden:

   choose-api-style
   use-validated-mode
   work-with-aliases
   convert-nested-models
   iterate-large-datasets
   integrate-fastapi
   use-with-poldantic
   use-with-pandera
