<!-- GSD:project-start source:PROJECT.md -->
## Project

**arrowmodel**

A Python library with a Rust core (via PyO3/maturin) that converts Apache Arrow `RecordBatch` and `Table` objects directly into lists of Pydantic v2 model instances. It eliminates the intermediate Python dict representation that arises from `to_pylist()` + Pydantic construction, replacing a two-step materialisation with a single tight Rust loop over Arrow buffers accessed via the Arrow C Data Interface.

**Core Value:** Dict-free, single-step conversion from Arrow buffers to Pydantic model instances — faster and with less allocation pressure than any Python-level approach.

### Constraints

- **Tech stack**: Rust (PyO3) + Python, built with maturin. No C/C++ extensions.
- **Python version**: >=3.11 (per cookiecutter template)
- **Dependencies (Rust)**: pyo3, arrow-rs, pyo3-arrow, serde_json, chrono
- **Dependencies (Python)**: pydantic >= 2.0 (required), pyarrow (optional — for type stubs and testing)
- **Build system**: maturin with pyproject.toml integration
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

## Recommended Stack
### Build System
| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| maturin | >=1.12,<2.0 | Rust/Python build backend | The standard build tool for PyO3 crates. Integrates with pyproject.toml, handles wheel generation, supports `maturin develop` for fast dev builds. v1.12.6 is latest stable. | HIGH |
| uv | (project tooling) | Python package/project management | Already in use (uv.lock present). Works with maturin as build backend via `[build-system] requires = ["maturin>=1.12,<2.0"]`. | HIGH |
| maturin-import-hook | >=0.1 | Auto-rebuild on import during dev | Automatically rebuilds the Rust extension when source changes are detected on import. Eliminates the `maturin develop` / `uv sync` cache staleness problem. Optional dev dependency. | MEDIUM |
### Rust Core Dependencies
| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| pyo3 | 0.28 | Rust-Python FFI bindings | Latest stable (0.28.2). Supports Python 3.11-3.14 including free-threaded 3.14t. MSRV Rust 1.83. Required features: `extension-module`, `chrono`. | HIGH |
| pyo3-arrow | 0.17 | Arrow C Data Interface for PyO3 | The purpose-built crate for this project's core need. Provides `PyRecordBatch`, `PyTable` etc. that accept any Arrow-PyCapsule-compatible input (pyarrow, polars, nanoarrow) via zero-copy FFI. Depends on pyo3 0.28 + arrow 58. | HIGH |
| arrow (arrow-rs) | 58 | Arrow in-memory format | Required by pyo3-arrow 0.17. Use individual sub-crates (`arrow-array`, `arrow-schema`, `arrow-data`, `arrow-buffer`, `arrow-cast`, `arrow-select`) to minimize compile times. v58.0.0 released Feb 2026. | HIGH |
| serde | 1 | Serialization framework | Required by serde_json. Derive macros for any Rust structs that need serialization. | HIGH |
| serde_json | 1 | JSON serialization | For the validated path: serialize Arrow rows to JSON bytes, then pass to Pydantic's `model_validate_json`. v1.0.149 is latest. | HIGH |
| chrono | 0.4 | Date/time types | Arrow date/timestamp types convert to chrono types. pyo3 has a `chrono` feature for automatic Python datetime conversion. v0.4.43 is latest. | HIGH |
| thiserror | 2 | Error type derivation | Ergonomic error types for the conversion layer. Use v2 (not v1) -- v2 is the current major. | MEDIUM |
### Python Dependencies
| Technology | Version | Purpose | Why | Confidence |
|------------|---------|---------|-----|------------|
| pydantic | >=2.11 | Data model validation | v2.11+ required (not just v2.0) because v2.11 introduced `validate_by_name`/`validate_by_alias` config which replaces the older `populate_by_name`. The project needs these for alias resolution. v2.12.5 is latest stable. | HIGH |
| pyarrow | (optional) | Arrow provider for testing | Not a hard dependency. Any Arrow-PyCapsule-compatible library works (pyarrow, polars, nanoarrow). Optional for type stubs and testing. | HIGH |
### Dev Dependencies (Python)
| Technology | Version | Purpose | Confidence |
|------------|---------|---------|------------|
| pytest | >=8.0 | Testing framework | HIGH |
| pytest-benchmark | >=5.0 | Performance benchmarks | MEDIUM |
| pyarrow | >=14.0 | Test fixture data creation | HIGH |
| maturin-import-hook | >=0.1 | Auto-rebuild during dev | MEDIUM |
| basedpyright | >=1.38 | Type checking (already in project) | HIGH |
| ruff | >=0.15 | Linting/formatting (already in project) | HIGH |
### Dev Dependencies (Rust)
| Technology | Version | Purpose | Confidence |
|------------|---------|---------|------------|
| criterion | 0.5 | Rust-side benchmarks (optional, for micro-benchmarks) | LOW |
## Key Version Compatibility Matrix
| pyo3-arrow | pyo3 | arrow-rs | Status |
|------------|------|----------|--------|
| 0.17 | 0.28 | 58 | **Current recommended** |
| 0.16 | 0.23 | 54 | Outdated, skip |
## Alternatives Considered
### Arrow FFI Approach: pyo3-arrow vs arrow-pyarrow
| Criterion | pyo3-arrow (recommended) | arrow-pyarrow |
|-----------|-------------------------|---------------|
| Input flexibility | Any PyCapsule-compatible lib (pyarrow, polars, nanoarrow) | pyarrow only |
| Extension types | Supported (stores Field + Array) | Dropped (loses field metadata) |
| ChunkedArray/Table | Full support via PyTable, PyChunkedArray | Limited (no ChunkedArray) |
| Output targets | arro3, nanoarrow, pyarrow | pyarrow only |
| Release coupling | Independent release cadence | Locked to arrow-rs releases (version skew risk) |
| Buffer protocol | Auto-converts numpy/memoryview to PyArray | Not supported |
| Maturity | Used in production by arro3, lonboard, geoarrow-rs | Official Apache project but narrower scope |
### Pydantic Version Floor: >=2.11 vs >=2.0
| Criterion | >=2.11 (recommended) | >=2.0 |
|-----------|----------------------|-------|
| Alias config | `validate_by_name`/`validate_by_alias` (cleaner API) | `populate_by_name` only |
| model_construct | Same API across all v2 | Same |
| model_validate_json | Same API across all v2 | Same |
| User base impact | Anyone on v2 should be on 2.11+ by now | Wider compat but adds alias handling complexity |
### Build Backend: maturin vs setuptools-rust
| Criterion | maturin (recommended) | setuptools-rust |
|-----------|----------------------|-----------------|
| Configuration | pyproject.toml only | setup.py + pyproject.toml |
| uv integration | First-class (cache-keys, build backend) | Partial |
| Community | Standard for new PyO3 projects | Legacy, fewer new adopters |
| Features | Editable installs, import hook, abi3 | Editable installs |
## pyo3-arrow API Patterns
### Receiving Arrow Data from Python
#[pyfunction]
### Receiving a Table (multiple batches)
#[pyfunction]
### Key Types
| pyo3-arrow Type | Arrow Type | Python Input |
|-----------------|------------|--------------|
| `PyRecordBatch` | `RecordBatch` | pyarrow.RecordBatch, polars chunk |
| `PyTable` | `Vec<RecordBatch>` + Schema | pyarrow.Table, polars DataFrame |
| `PySchema` | `Schema` | pyarrow.Schema |
| `PyField` | `Field` | pyarrow.Field |
| `PyArray` | `ArrayRef` | pyarrow.Array, numpy array |
## Pydantic Integration Patterns
### Fast Path: model_construct (no validation)
### Validated Path: serde_json + model_validate_json
### Alias Resolution (Python-side, at converter init)
# Introspect Pydantic model to build column-name -> field-name mapping
## Cargo.toml Template
- `crate-type = ["cdylib"]` is required for maturin to produce a Python extension module.
- Use individual `arrow-*` sub-crates instead of the umbrella `arrow` crate to reduce compile times. Only pull in what you actually use.
- The `extension-module` feature on pyo3 is required for building as a Python extension (prevents linking against libpython).
- The `chrono` feature on pyo3 enables automatic conversion between `chrono::NaiveDateTime`/`chrono::DateTime<Utc>` and Python `datetime`.
## pyproject.toml Changes Required
## Development Workflow
### Recommended: maturin develop + import hook
# One-time setup
# Development iteration
# OR use import hook for auto-rebuild:
### Alternative: uv sync (slower but integrated)
## Minimum Rust Version
## Python Version Support
## Sources
- PyO3 releases: https://github.com/pyo3/pyo3/releases (latest: 0.28.2, Feb 2025) -- HIGH confidence
- pyo3-arrow Cargo.toml: https://github.com/kylebarron/arro3/blob/main/pyo3-arrow/Cargo.toml (v0.17, pyo3 0.28, arrow 58) -- HIGH confidence
- pyo3-arrow docs: https://docs.rs/pyo3-arrow/latest/pyo3_arrow/ -- HIGH confidence
- arrow-rs releases: https://github.com/apache/arrow-rs/releases (latest: 58.0.0, Feb 2026) -- HIGH confidence
- maturin releases: https://github.com/pyo3/maturin/releases (latest: 1.12.6, Mar 2026) -- HIGH confidence
- Pydantic docs (model_construct): https://docs.pydantic.dev/latest/api/base_model/ -- HIGH confidence
- Pydantic docs (aliases): https://docs.pydantic.dev/latest/concepts/alias/ -- HIGH confidence
- Pydantic v2.11 release (validate_by_name): https://pydantic.dev/articles/pydantic-v2-11-release -- HIGH confidence
- Pydantic releases: https://github.com/pydantic/pydantic/releases (latest: 2.12.5, Nov 2025) -- HIGH confidence
- serde_json: https://crates.io/crates/serde_json (latest: 1.0.149) -- HIGH confidence
- chrono: https://crates.io/crates/chrono (latest: 0.4.43) -- HIGH confidence
- maturin + uv integration: https://github.com/PyO3/maturin/issues/2314 -- MEDIUM confidence
- maturin import hook: https://github.com/PyO3/maturin-import-hook -- MEDIUM confidence
- pyo3-arrow vs arrow-pyarrow comparison: https://docs.rs/pyo3-arrow/latest/pyo3_arrow/ and https://arrow.apache.org/rust/arrow_pyarrow/index.html -- MEDIUM confidence (synthesized from multiple sources)
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->

## Opening pull requests

When you open a pull request, ask the user whether it needs a review. If they say yes:

1. Mark the PR ready for review, since the review bot skips drafts.
2. Request a review from `anentropic-reviewbot`. Claude then posts inline comments and a summary on the PR.
3. Watch the PR and address the review comments and any CI failures as they come in, until CI is green and no blocking comments are left open.

To get another review after pushing fixes, request one from `anentropic-reviewbot` again.
