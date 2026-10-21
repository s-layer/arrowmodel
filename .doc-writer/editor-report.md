# Editor Report

**Generated:** 2026-10-21
**Scope:** uncommitted 1.1.0 stream-input changes (README.md, docs/index.rst, docs/tutorials/getting-started.rst, docs/how-to/iterate-large-datasets.rst, docs/how-to/integrate-fastapi.rst, docs/how-to/use-with-poldantic.rst, docs/reference/api.rst, benchmark numbers in docs/explanation/fast-vs-validated.rst)
**Files reviewed:** 8
**Changes made:** 9
  - BLOCKING: 3
  - SUGGESTION: 4
  - NITPICK: 2

## Summary

The stream-input refresh is accurate against `src/arrowmodel/__init__.py`, `rust/src/stream.rs` (as of 7fd4953) and `tests/test_stream_input.py`. Every user-visible claim was re-run: reader output, one-shot reader, TypeError message and the inputs that raise it, LazyFrame `.collect()`, `ArrowData` example, nanoarrow stream, and the closed-cursor `ValueError`. The fixes were the SQLite boolean example on the FastAPI page and two benchmark ratios in prose that no longer matched the new results. No page mentions the old `unsendable` threading limitation; 16 concurrent streaming requests through FastAPI's TestClient all completed.

`sphinx-build -W -b html docs docs/_build/html`: RC=0, build succeeded.

---

## docs/how-to/integrate-fastapi.rst

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Define your response model / Reuse converters | Pre-existing: `Product.in_stock: bool` with SQLite. adbc-driver-sqlite returns `in_stock` as `int64`; the fast path stores 1/0 in a `bool` field, the JSON response carries `1`/`0`, and Pydantic emitted 5,001 serialisation warnings for a 5,000-row export (reproduced with FastAPI TestClient + adbc-driver-sqlite). `CAST` cannot help: SQLite has no boolean type. | Changed both `Product` models to `in_stock: int` and added a paragraph after the first model: why it is `int` (SQLite), that the fast path does no coercion (link to type mappings), what `bool` would do, `bool` for databases with a real boolean (PostgreSQL), `validate=True` for 1/0 to True/False (link to validated mode). Re-ran: 0 warnings for list, export and concurrent export. The Parquet upload example (`validate=True`) still works: a bool Parquet column coerces to the `int` field. |

### SUGGESTION

None. Threading: the page makes no known-limitation claim, which matches 7fd4953. The reader-in-generator pattern, the "naive variant fails with `ValueError: Failed to read the next Arrow record batch`" warning, and the late-error note were all reproduced.

---

## docs/explanation/fast-vs-validated.rst

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Performance characteristics | Prose said the flat fast path is "roughly 3x faster" than `to_pylist()`. The new table shows 3.6x / 4.5x / 3.1x (results file: 3.62x, 4.52x, 3.14x). | "3x to 4.5x faster". |
| Performance characteristics | Nested at 1,000 rows: prose said "about 5-6x faster than the baseline". Results file: 5.13x (8.5 ms vs 43.7 ms). | "about 5x faster". |

Table checked cell by cell. Rows 100 and 1,000 match the first Flat Primitives (RecordBatch) section and 38,000 matches the "38k rows" section, rounded correctly (81.8 → 82 ms, 201.7 → 202 ms, 256.7 → 257 ms). Nested 8.5 / 17.3 / 43.7 ms match. "Roughly 2.5x faster than the validated path" (2.62x / 2.74x / 2.46x), "Table input shows the same ratios" (3.3x to 4.3x vs pylist, about 2.5x to 2.8x vs validated), "validated path still faster than to_pylist" (1.3x to 1.7x) and "about 2-3x" for nested validated vs baseline (2.5x at 1,000 rows, 2.0x at 38k) all still hold. The README and index "~3x" headlines are a conservative lower bound and were left unchanged.

---

## docs/how-to/use-with-poldantic.rst

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| Why this works without pyarrow | "arrowmodel accepts any Arrow-PyCapsule-compatible input" contradicted the new warning two paragraphs later: a Series and a LazyFrame are PyCapsule-compatible but raise `TypeError`. | "arrowmodel reads record batches through that protocol". |

---

## docs/how-to/iterate-large-datasets.rst

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| Stream from a RecordBatchReader | Rule 4: the first `iter` / `convert` mentions in the new section had no links. | Linked `:py:meth:~arrowmodel.ArrowModel.iter` and `:py:meth:~arrowmodel.ArrowModel.convert` (checked in the built HTML). Generic later uses of "``iter``" for the iter API across all three styles were left plain. |

All claims verified by running: reader example output, empty list when converting a reader again, `TypeError` text for `pa.array`, the check that runs before any batch is read (`test_missing_column_raises_before_reading_batches`), Polars DataFrame streaming, and the `ValueError` on a closed cursor.

---

## docs/reference/api.rst

### NITPICK

| Section | Description | Fix |
|---------|-------------|-----|
| ArrowModel.convert, ArrowModelConverter.convert | Three over-long source lines in `data` rows and the Raises list. | Reflowed (no wording change). |

The signatures (`data: ArrowData`), the TypeError/ValueError lists, the note that generator errors surface on the first `next()`, and the Input types section all match `__init__.py` and `stream.rs`. The LazyFrame, Series, list-of-dicts and int ChunkedArray inputs each raise `TypeError` (confirmed by running them). The struct array and struct ChunkedArray rows match `TestStructArrays`.

---

## README.md, docs/index.rst, docs/tutorials/getting-started.rst

No changes. The claims were checked: inputs listed, `TypeError` set, single-read reader, nanoarrow struct arrays and streams. The benchmark link points at the renamed results file. RST/Markdown `--` usage matches the surrounding text, and none of the changed prose has em dashes, curly quotes or flagged AI vocabulary.

### NITPICK

| Section | Description | Fix |
|---------|-------------|-----|
| (all scoped files) | Humanizer scan: no em dashes, chatbot artifacts, promotional words or filler phrases found in the changed text. | None needed. |

---

## Terminology Changes

None. "Arrow PyCapsule Interface", "fast path" / "validated path", "field mapping" and "model_construct semantics" are used consistently in the changed text. `terminology.yaml` is unchanged.
