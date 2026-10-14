# arrowmodel

[![PyPI](https://img.shields.io/pypi/v/arrowmodel)](https://pypi.org/project/arrowmodel/)
[![Python](https://img.shields.io/pypi/pyversions/arrowmodel)](https://pypi.org/project/arrowmodel/)
[![License](https://img.shields.io/github/license/s-layer/arrowmodel)](LICENSE)

Dict-free, single-step conversion from Apache Arrow buffers to Pydantic v2 model instances.

arrowmodel uses a Rust core (via PyO3) to walk Arrow `RecordBatch` and `Table` buffers directly, building Pydantic model instances in a tight loop with no intermediate Python dicts. It replaces the `to_pylist()` + `Model(**row)` two-step with a single call that is roughly 2x faster for flat schemas.

[Full Documentation](https://s-layer.github.io/arrowmodel/)

## Quick Start

### Installation

```bash
pip install arrowmodel
```

No Rust toolchain needed -- pre-built wheels are provided.

### First Use

```python
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
```

## Key Features

- **Dict-free conversion**: Arrow buffers go straight to `model_construct` calls in Rust -- no `to_pylist()`, no intermediate dicts.
- **~2x faster for flat schemas**: Roughly twice the throughput of the pure-Python approach, with less allocation pressure.
- **Three API styles**: `ArrowModel` base class for concise models, `ArrowModelConverter` for reusable converters, and `model_convert`/`model_iter` for one-shot use.
- **Validated mode**: Pass `validate=True` to run full Pydantic validation (`model_validate_json`) when you need type coercion or custom validators.
- **Pydantic alias support**: Resolves `validation_alias`, `alias`, field name, and `populate_by_name` / `validate_by_name`.
- **Nested models**: Arrow `Struct` columns map to nested Pydantic models, including optional structs and deeply nested hierarchies.
- **Lazy iteration**: `iter()` yields one model at a time, materialising one `RecordBatch` worth of instances in memory.
- **Broad type coverage**: Integers, floats, decimals, booleans, strings, dates, timestamps, durations, times, binary, lists, structs, maps, dictionaries, unions, intervals, and more.

## Input Compatibility

arrowmodel accepts any Arrow-PyCapsule-compatible input via the Arrow C Data Interface:

- **pyarrow** -- `RecordBatch`, `Table`
- **Polars** -- `DataFrame` (exports via PyCapsule)
- **nanoarrow** -- arrays and record batches

No pyarrow dependency is required at runtime. If your Arrow data comes from Polars or nanoarrow, it works without installing pyarrow.

## License

[Apache-2.0](LICENSE)
