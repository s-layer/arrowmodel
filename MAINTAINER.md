# Maintainer Guide

> This is a reference document for the project maintainer -- a "future you" guide.
> It is NOT an end-user document and NOT a contributor guide.

## Dev Setup

Clone the repository and install with uv (handles both Python and Rust build):

```bash
git clone https://github.com/s-layer/arrowmodel.git
cd arrowmodel
uv sync --dev
```

This installs all dev dependencies and builds the Rust extension via maturin.

Verify the setup:

```bash
uv run pytest -x
```

All tests should pass. If the Rust extension fails to build, ensure you have a Rust toolchain installed (`rustup` recommended, minimum Rust 1.83).

### Rebuilding after Rust changes

After changing Rust source files in `rust/src/`, rebuild:

```bash
uv sync
```

Alternatively, use `maturin develop` directly for faster iteration:

```bash
uv run maturin develop --release
```

Or install the `maturin-import-hook` for automatic rebuilds on import:

```bash
uv run python -c "import maturin_import_hook; maturin_import_hook.install()"
```

## Common Development Tasks

### Run tests

```bash
uv run pytest -v
```

### Run tests with coverage

```bash
uv run pytest --cov=arrowmodel --cov-report=term-missing
```

### Run benchmarks

```bash
uv run python benchmarks/bench_convert.py
```

### Lint and format

```bash
uv run ruff check src/ tests/
uv run ruff format src/ tests/
```

Rust:

```bash
cargo fmt --manifest-path rust/Cargo.toml
(cd rust && cargo clippy --all-targets -- -D warnings)
```

### Type check

```bash
uv run basedpyright
```

### Run all checks (via prek)

```bash
uv tool install prek
prek run --all-files
```

## Architecture Overview

### Directory structure

```
arrowmodel/
  rust/
    src/
      lib.rs          # PyO3 module definition; per-batch conversion (convert_batch)
      stream.rs       # ArrowInput: opens a RecordBatch or Arrow stream once, converts batch by batch
      construct.rs    # Fast-path instance construction (model_construct-equivalent)
      extract.rs      # Arrow column extraction logic (DataType -> Python value)
    Cargo.toml        # Rust dependencies (pyo3, arrow-rs, serde_json, chrono)
  src/
    arrowmodel/
      __init__.py     # Public Python API (ArrowModel, ArrowModelConverter, model_convert, model_iter)
      _core.pyi       # Type stubs for the Rust extension module
  tests/
    conftest.py       # Shared pytest fixtures for all Arrow data types
    test_convert.py   # Core conversion tests (schema, types, nulls, aliases, validation)
    test_extended_types.py  # Extended type tests (decimals, binary, intervals, unions)
    test_arrow_model_base.py  # ArrowModel base class tests
    test_construct.py # Fast-path construction parity with model_construct
    test_stream_input.py  # RecordBatchReader, Polars, nanoarrow and struct-array input
  benchmarks/
    bench_convert.py  # Benchmarks: fast path, validate=True, and to_pylist + model_construct
```

### Data flow

1. Python calls `ArrowModelConverter.convert(data)` or `ArrowModel.convert(data)` (or `iter`).
2. Python opens the input with `_core.ArrowInput(data)` (`stream.rs`). It accepts anything exporting `__arrow_c_array__` (a RecordBatch or struct array) or `__arrow_c_stream__` (a Table, RecordBatchReader, Polars DataFrame, nanoarrow stream) via pyo3-arrow's Arrow C Data Interface, and raises `TypeError` for anything else. Both are read as struct arrays (not through pyo3-arrow's RecordBatch import, which panics on, or for streams drops, null rows), so a null row raises `ValueError`. The stream is opened once, so a one-shot reader is read once, and each read runs with the GIL released.
3. Python resolves the Arrow schema (`ArrowInput.fields()`) to Pydantic field specs (column index, field name, nested model class) before any batch is read.
4. `convert` calls `ArrowInput.convert_all()`; `iter` calls `ArrowInput.convert_next()` once per batch, so a stream is read lazily.
5. Rust iterates rows, extracting values from each column based on Arrow DataType.
6. **Fast path:** `_construct_plan()` (Python) resolves once which value goes to which field, as `model_construct(**kwargs)` would; Rust then builds each instance directly (`construct.rs`). Models that override `model_construct`, or whose matching depends on values (`AliasPath`, unmatched keys with `extra="allow"` or a post-init hook), fall back to calling `model_construct` per row.
7. **Validated path:** Rust serialises each row to JSON bytes, calls `model_validate_json(json_bytes)` per row.
8. Results are collected into a Python list and returned.

### Key design decisions

- **Column extraction in Rust:** The inner loop over Arrow buffers is in Rust for speed. Python handles schema resolution and Pydantic API calls.
- **pyo3-arrow for FFI:** Uses pyo3-arrow (not arrow-pyarrow) for Arrow C Data Interface support, which accepts any PyCapsule-compatible input.
- **Field map compiled once:** `_build_field_map()` runs at converter init, producing a `{arrow_column: pydantic_field}` dict. This avoids per-batch alias resolution.
- **Nested model detection at init:** `_get_nested_model()` inspects field annotations once and passes nested model classes to Rust for recursive struct conversion.

## CI/CD Process

Pre-commit hooks are managed via prek:

```bash
prek install      # Install git hooks
prek run --all-files  # Run all checks manually
```

prek hooks: ruff (lint + format), `cargo fmt --check`, basedpyright (type checking), and the usual whitespace/TOML/YAML checks.

CI (`.github/workflows/quality.yml`) runs prek, then `cargo clippy --all-targets -- -D warnings` (CI only, since a cold run checks every dependency), then pytest.

## Release Process

1. Update version in `pyproject.toml`:
   ```toml
   [project]
   version = "X.Y.Z"
   ```

2. Commit the version bump and tag:
   ```bash
   git add pyproject.toml
   git commit -m "release: vX.Y.Z"
   git tag vX.Y.Z
   git push origin main --tags
   ```

3. Build wheels with maturin:
   ```bash
   maturin build --release
   ```

4. Upload to PyPI:
   ```bash
   maturin publish
   ```

5. Verify the release:
   ```bash
   pip install arrowmodel==X.Y.Z
   python -c "import arrowmodel; print('OK')"
   ```

## Decision Log

| Decision | Rationale | Date |
|----------|-----------|------|
| Use pyo3-arrow instead of arrow-pyarrow | Accepts any PyCapsule-compatible input (pyarrow, Polars, nanoarrow), not just pyarrow. Independent release cadence avoids arrow-rs version coupling. | 2024 |
| Pydantic >= 2.11 version floor | v2.11 introduced `validate_by_name` / `validate_by_alias` config, replacing the older `populate_by_name`. Cleaner API for alias resolution. | 2024 |
| model_construct for fast path | Bypasses all Pydantic validation for maximum speed. Users opt into validation explicitly with `validate=True`. | 2024 |
| serde_json for validated path | Serialise rows to JSON in Rust, then call `model_validate_json` on Python side. Avoids constructing Python dicts and uses Pydantic's fastest validation entry point. | 2024 |
| ArrowModel base class via __pydantic_init_subclass__ | Uses Pydantic's own subclass hook (not `__init_subclass__`) to ensure model_fields is populated before converter creation. | 2026-04 |
| Individual arrow-rs sub-crates | Uses `arrow-array`, `arrow-schema`, etc. instead of the umbrella `arrow` crate to reduce compile times. | 2024 |
