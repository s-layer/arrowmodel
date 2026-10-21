# arrowmodel

[![PyPI](https://img.shields.io/pypi/v/arrowmodel)](https://pypi.org/project/arrowmodel/)
[![Python](https://img.shields.io/pypi/pyversions/arrowmodel)](https://pypi.org/project/arrowmodel/)
[![License](https://img.shields.io/github/license/s-layer/arrowmodel)](LICENSE)

Dict-free, single-step conversion from Apache Arrow buffers to Pydantic v2 model instances.

arrowmodel uses a Rust core (via PyO3) to walk Arrow `RecordBatch` and `Table` buffers directly, building Pydantic model instances in a tight loop with no intermediate Python dicts. It replaces the `to_pylist()` + `Model.model_construct(**row)` two-step with a single call that is roughly 3x faster for flat schemas ([benchmark results](https://github.com/s-layer/arrowmodel/blob/main/benchmarks/results-v1.1.0-2026-10-21.txt)).

[Full Documentation](https://s-layer.github.io/arrowmodel/)

## Quick Start

### Installation

```bash
pip install arrowmodel
```

Pre-built wheels are provided for Linux (x86_64, aarch64), macOS on Apple Silicon
(arm64) and Windows (x64), so no Rust toolchain is needed there. On any other
platform, including Intel Macs, pip builds from source and needs Rust 1.83 or later
(install it with [rustup](https://rustup.rs)).

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

- **Dict-free conversion**: Rust builds each model instance straight from the Arrow buffers, exactly as `model_construct` would, with no `to_pylist()` and no intermediate dicts.
- **~3x faster for flat schemas**: Roughly three times the throughput of the pure-Python approach, with less allocation pressure. See the [benchmark results](https://github.com/s-layer/arrowmodel/blob/main/benchmarks/results-v1.1.0-2026-10-21.txt) and the [benchmark script](https://github.com/s-layer/arrowmodel/blob/main/benchmarks/bench_convert.py).
- **Three API styles**: `ArrowModel` base class for concise models, `ArrowModelConverter` for reusable converters, and `model_convert`/`model_iter` for one-shot use.
- **Validated mode**: Pass `validate=True` to run full Pydantic validation (`model_validate_json`) when you need type coercion or custom validators.
- **Pydantic alias support**: Resolves `validation_alias`, `alias`, field name, and `populate_by_name` / `validate_by_name`.
- **Nested models**: Arrow `Struct` columns map to nested Pydantic models, including optional structs and deeply nested hierarchies.
- **Lazy iteration**: `iter()` yields one model at a time, materialising one `RecordBatch` worth of instances in memory, including from a `RecordBatchReader`, so a streaming query result never has to fit in memory.
- **Broad type coverage**: Integers, floats, decimals, booleans, strings, dates, timestamps, durations, times, binary, lists, structs, maps, dictionaries, unions, intervals, and more.

## Input Compatibility

arrowmodel accepts any Arrow record batch, or stream of record batches, via the Arrow PyCapsule Interface:

- **pyarrow** -- `RecordBatch`, `Table`, `RecordBatchReader` (read lazily by `iter()`), `StructArray`, and a `ChunkedArray` of structs
- **Polars** -- `DataFrame`
- **nanoarrow** -- struct arrays and array streams
- **Other sources** -- any object that exports `__arrow_c_array__` (a record batch) or `__arrow_c_stream__` (a stream of record batches), such as the reader from an ADBC cursor's `fetch_record_batch()`

Any other input, such as a plain `Array`, a Polars `Series` or `LazyFrame`, or a list of dicts, raises `TypeError`. A `RecordBatchReader` can be read only once; converting it again returns an empty list.

No pyarrow dependency is required at runtime. If your Arrow data comes from Polars or nanoarrow, it works without installing pyarrow.

## License

[Apache-2.0](LICENSE)
