# Gap Report

**Generated:** 2026-10-21
**Source root:** src/
**Language:** Python
**Scope:** release 1.1.0 (`git diff cf7facb`): `convert()` / `iter()` accept any Arrow stream of record batches; unsupported input raises `TypeError`
**Total undocumented symbols:** 4 (in the hand-written reference; autoapi covers them from docstrings)
**Potentially stale pages:** 8 (+ README)

## What changed for users

1. **Stream input.** `ArrowModel.convert` / `.iter`, `ArrowModelConverter.convert` / `.iter`, `model_convert` and `model_iter` take `data: ArrowData`, which means either:
   - anything exporting `__arrow_c_array__` that is a struct of columns: a pyarrow `RecordBatch`, a pyarrow `StructArray`, a nanoarrow struct array; or
   - anything exporting `__arrow_c_stream__` of record batches: a pyarrow `Table`, a pyarrow `RecordBatchReader`, a Polars `DataFrame`, a nanoarrow array stream, a struct-typed pyarrow `ChunkedArray`, an ADBC cursor's `fetch_record_batch()` reader.
   (Checked against the installed 1.1.0 build: `StructArray` and a struct `ChunkedArray` both convert. `rust/src/stream.rs::ArrowInput::new`.)
2. **Lazy streams.** `iter()` pulls one batch from the stream at a time (`convert_next`), so a `RecordBatchReader` is read lazily and memory stays bounded by one batch (`tests/test_stream_input.py::test_iter_reads_one_batch_at_a_time`). `convert()` reads every batch into one list.
3. **One-shot readers.** A `RecordBatchReader` is consumed. Converting it a second time returns `[]`, as `reader.read_all()` would (`test_reader_is_consumed`). A Polars `DataFrame` or pyarrow `Table` exports a fresh stream each time, so it can be converted again (`test_dataframe_can_be_converted_twice`).
4. **Schema checked first.** The missing-required-column `ValueError` is raised from the stream's schema before any batch is read (`test_missing_column_raises_before_reading_batches`). The error now lists the available columns for any source, Polars included (`test_missing_column_error_lists_polars_columns`).
5. **Map guard works for all sources.** The `dict`/`Mapping`-over-Map `TypeError` used to be best-effort (pyarrow types only). It now reads the Arrow C schema, so it fires for Polars, nanoarrow and readers too. The current docs already state the guard with no source caveat, so they are now accurate everywhere. No page change needed.
6. **Unsupported input → `TypeError`.** A plain `Array`, a non-struct `ChunkedArray`, a list of dicts, a Polars `Series`, a Polars `LazyFrame`, or a pyarrow `Schema` raises `TypeError`. Message: `arrowmodel converts a RecordBatch or Table (or any Arrow stream of record batches, such as a RecordBatchReader or a Polars DataFrame); got <fully.qualified.TypeName>.` (`tests/test_convert.py::TestUnsupportedInput`, `tests/test_stream_input.py::TestNonRecordBatchStreams`; `LazyFrame` and `Schema` checked by hand against the build.)
7. **Generators raise lazily.** `iter()` / `model_iter()` / `ArrowModel.iter()` are generators. The `TypeError` and the missing-column `ValueError` are raised on the first `next()`, not when `iter(...)` is called. This was already true in 1.0.x but was never documented. It is more visible now that the input `TypeError` exists.

## Undocumented Symbols

New public names in `__all__` (`src/arrowmodel/__init__.py`), each with a docstring that autoapi renders:

| Symbol | Kind | In hand-written `docs/reference/api.rst`? |
|---|---|---|
| `ArrowData` | type alias, `ArrowArrayExportable \| ArrowStreamExportable` | No |
| `ArrowArrayExportable` | `Protocol` (`__arrow_c_array__`) | No |
| `ArrowStreamExportable` | `Protocol` (`__arrow_c_stream__`) | No |
| `ArrowSchemaExportable` | `Protocol` (`__arrow_c_schema__`) | No |

`ArrowSchemaExportable` is exported but is **not** an accepted `data` input. Passing a `pa.Schema` raises `TypeError`. It types an internal schema argument. Document it as a typing helper only, or ask the maintainer whether it should be public at all.

## Potentially Stale Pages

| Page | What is wrong |
|---|---|
| `docs/how-to/iterate-large-datasets.rst` | **Wrong.** "Stream from a RecordBatchReader" says a reader is not accepted and raises `ValueError`, and tells users to loop over batches by hand. The intro, "How batch-at-a-time works", the note ("lazy advantage only appears with multi-batch Tables") and the meta description talk only about Tables. |
| `docs/reference/api.rst` | **Wrong types / missing raise.** Six signatures and six parameter rows say `pa.RecordBatch \| pa.Table`. No Raises list includes the unsupported-input `TypeError`. `ArrowModelConverter.iter` says it is lazy only "For Table input". The new type aliases are not described. |
| `README.md` | **Incomplete / slightly wrong.** "Input Compatibility" lists nanoarrow as "arrays and record batches" (a plain non-struct array is rejected). It leaves out `RecordBatchReader` and nanoarrow streams and says nothing about `TypeError`. "Lazy iteration" feature bullet does not mention streams. Benchmark links must point to the v1.1.0 results file (parent agent handles this). |
| `docs/index.rst` | **Incomplete.** "Any Arrow Source" card does not mention streams / `RecordBatchReader`. |
| `docs/how-to/integrate-fastapi.rst` | **Incomplete.** "Stream large results" still loads the whole result with `fetch_arrow_table()`. Now you can pass `cur.fetch_record_batch()` straight to `iter()`, but only while the cursor/connection is still open. The current example closes the connection before `StreamingResponse` runs the generator, so a naive swap would break. |
| `docs/how-to/use-with-poldantic.rst` | **Minor.** "Why this works without pyarrow" is accurate. A tip is worth adding: `LazyFrame` and `Series` raise `TypeError`, so call `.collect()` / use a `DataFrame`. |
| `docs/tutorials/getting-started.rst` | **Minor.** "What you learned" says `model_convert` converts "a `RecordBatch` or `Table`". Still true, but it should mention streams. |
| `docs/explanation/fast-vs-validated.rst` | Benchmark link must point to the v1.1.0 results file (parent agent handles this). No other change from this release. |

Checked and not stale: `use-with-pandera.rst` (Polars tip is accurate), `type-mappings.rst` (Map guard and missing-column wording are now true for every source), `convert-nested-models.rst`, `choose-api-style.rst`, `use-validated-mode.rst`, `work-with-aliases.rst`, section index pages.

## Outside docs pages (for the maintainer, not part of this pass)

- `MAINTAINER.md` "Data flow" steps 3-4 name `_core.convert_record_batch()` / `_core.convert_record_batch_validated()`, which no longer exist. It is now `_core.ArrowInput(data)` → `.fields()` → `.convert_all()` / `.convert_next()`. The directory tree leaves out `rust/src/stream.rs` and `tests/test_stream_input.py`. The `__init__.py` comment leaves out the new type aliases.
- Source docstrings rendered by autoapi still say "RecordBatch or Table": `ArrowModel.convert` / `ArrowModel.iter` (summary + `data:` arg), and the `ArrowModelConverter` class docstring ("Convert Arrow RecordBatch data"). `ArrowModelConverter.convert` / `.iter` docstrings were updated.
- `.doc-writer/context.md` still says "roughly 2x speedup" and describes input as `RecordBatch`/`Table` only. It feeds future passes and is safe to leave until a `--refresh-context` run.
