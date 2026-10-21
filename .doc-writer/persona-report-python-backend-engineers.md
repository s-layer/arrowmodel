# Persona Report

**Generated:** 2026-10-21 (pass for 1.1.0 stream input, commit 68b8e39)
**Audience:** Python backend engineers (intermediate-advanced)
**Scenarios tested:** 7 (S1-S6 reused; S7 added for stream input)
**Results:** 7 PASS, 0 PARTIAL, 0 FAIL

## Summary

The 1.1.0 stream-input story holds up when I run it. Every snippet I tried in the changed pages ran as documented with arrowmodel 1.1.0, pyarrow 26, Polars, nanoarrow, poldantic, FastAPI and the ADBC SQLite driver. That covers reader iteration, read-once behaviour, the missing-column error raised before any batch is pulled, the `TypeError` for a plain `Array`, `LazyFrame`, `Series` or list of dicts, both FastAPI streaming variants, the broken "cursor closed first" variant (`ValueError: Failed to read the next Arrow record batch`), and the "200 then cut-off body" warning. All the previous pass's PARTIALs are resolved. What remains is low-severity friction. Nothing says the reader and stream input is new in 1.1.0. The `ArrowData` link lands on a garbled autoapi entry. The how-to index blurb doesn't mention streams. A couple of benchmark-prose ranges are looser than the linked results file.

Code verification (run with `uv run python ...` / `uv run --with fastapi --with adbc-driver-sqlite --with httpx --with polars --with poldantic --with nanoarrow ...`):

- `iterate-large-datasets.rst`: all blocks run and the printed output matches. A consumed reader returns `[]` from both `convert` and `iter`. A missing column raises `ValueError` on the first `next()` with 0 batches pulled. Closing the cursor before iterating raises `ValueError`. The `pa.array([1, 2, 3])` `TypeError` message matches the documented text exactly. A Polars `DataFrame` with several chunks iterates without importing pyarrow.
- `integrate-fastapi.rst`: `/products` returns 200, `in_stock` comes back as `int64` from SQLite, and there is no serializer warning. The `bool` claim is confirmed: the fast path gives `1`/`0` plus a `PydanticSerializationUnexpectedValue` warning, and `validate=True` gives `True`/`False`. The Table streaming endpoint and the reader-inside-generator endpoint each streamed 5000 NDJSON rows. Opening the reader in the endpoint gives 200 with an empty body, and the server raises the documented `ValueError`. A missing column also gives 200 with an empty body.
- `use-with-poldantic.rst`: model-first and schema-first both run. `LazyFrame` and `Series` raise `TypeError`, and `lazy.collect()` converts.
- `reference/api.rst`: the `load_users(data: ArrowData)` example runs. StructArray, struct ChunkedArray, nanoarrow `ArrayStream` and nanoarrow struct array all convert.
- `fast-vs-validated.rst`: the table numbers (100 / 1,000 / 38,000 rows) match `benchmarks/results-v1.1.0-2026-10-21.txt`.

---

## Scenario S1: Install arrowmodel and convert an ADBC query result into Pydantic models for a FastAPI endpoint

**Verdict:** PASS

### Navigation Path

1. Started at: `docs/index.rst`. I followed the Getting Started card to `tutorials/getting-started.rst` (install, first conversion, Next steps now link the iteration guide).
2. Navigated to: `how-to/integrate-fastapi.rst` (through the how-to index). The model, endpoint and converter-reuse examples ran against a real SQLite DB. The `in_stock: int` rationale is clear and correct.

---

## Scenario S2: Arrow to Python type mappings for timestamps, decimals and nested structs

**Verdict:** PASS (pages unchanged by this commit; previous verdict carried, navigation re-checked)

---

## Scenario S3: Aliases for camelCase columns and snake_case fields

**Verdict:** PASS (pages unchanged; the `AliasPath` wording is now consistent across the aliases how-to, the explanation and the reference)

---

## Scenario S4: Full API for ArrowModelConverter

**Verdict:** PASS

### Navigation Path

1. `docs/index.rst`, then Reference, then `reference/api.rst`. The signatures now use `data: ArrowData`, Raises lists the input `TypeError`, the schema `ValueError` and the Map `TypeError`, and a new "Input types" table explains `ArrowData` and both protocols, including the caveat that a plain `Array` or `Series` type-checks but fails at runtime.
2. I clicked `ArrowData` and landed on the autoapi page. The entry reads: "a RecordBatch, Table or stream. Type: Arrow record-batch data that arrowmodel converts". The sentence is split, and part of it is rendered as a "Type:" field (see problem 2). This is friction, not a blocker, because the hand-written table already answered the question.

---

## Scenario S5: Stream large Arrow query results through FastAPI without loading all rows

**Verdict:** PASS (up from the previous pass's PARTIAL note)

### Navigation Path

1. `how-to/integrate-fastapi.rst` > Stream large results. The Table variant is shown first, followed by an explicit statement that it still loads everything, and then the reader-inside-generator variant. The warning explains both the closed-cursor failure and the late-error (200 then cut-off) behaviour. I ran all three shapes and they behave as described.
2. Friction: both examples register `@app.get("/products/export")` (problem 5).

---

## Scenario S6: Fast path vs `validate=True` for a ~40k-row ADBC result

**Verdict:** PASS (up from PARTIAL; the previous gaps on AliasPath, NULLs, Table ratios, benchmark links, `response_model` and the clickable card are fixed)

### Navigation Path

1. `explanation/fast-vs-validated.rst` > Performance characteristics. The 38,000-row row (82 ms fast, 202 ms validated, 257 ms baseline) answers the 40k question directly, and the numbers match the linked results file.
2. Friction: the range prose and the nested-trade-off sentence are slightly off from the file (problem 4).

---

## Scenario S7 (new): Stream a large ADBC result from a RecordBatchReader into models

**Verdict:** PASS

### Navigation Path

1. Started at: `docs/index.rst`. The "Any Arrow Source" card names `RecordBatchReader` and links to `how-to/iterate-large-datasets.rst`.
2. `iterate-large-datasets.rst` > Stream from a RecordBatchReader. It covers passing the reader to `iter`, the ADBC `fetch_record_batch()` example, keeping the cursor open, `convert` reading everything, read-once behaviour, the schema check before the first batch, and Polars. Then Handle input errors covers lazy errors on the first `next()`, the exact `TypeError` text, what to do with a `LazyFrame` or `Series`, and why a reader can come back empty. Everything I ran matched.
3. I followed the link to `integrate-fastapi.rst` for the server-side variant, which works.
4. Friction: nothing says this needs 1.1.0 (problem 1). The how-to index blurb only says "large Tables" (problem 3). The `.to_frame()` advice is incomplete (problem 6).

---

## Revision Recommendations

### FAIL Issues (trigger revision)

None. No revision pass needed.

### PARTIAL Issues (for project author approval)

None at scenario level. Low-severity fixes, ranked:

| # | Sev | Page | Text at fault | Suggested fix |
|---|-----|------|---------------|---------------|
| 1 | Medium | `how-to/iterate-large-datasets.rst` (Stream from a RecordBatchReader, Handle input errors), `reference/api.rst` (Input types, `data` rows) | "Pass a ``pyarrow.RecordBatchReader`` straight to" (no version marker anywhere) | Add `.. versionadded:: 1.1.0` (stream input, `ArrowData`) / `.. versionchanged:: 1.1.0` (TypeError for non-record-batch input) so users on 1.0.x know why the snippet fails for them |
| 2 | Medium-low | autoapi entry for `arrowmodel.ArrowData` (from the src docstring; the target of every `:py:data:` link in `api.rst`) | Renders "a RecordBatch, Table or stream. Type: Arrow record-batch data that arrowmodel converts"; `sphinx-build -n` warns `py:class reference target not found: Arrow record-batch data that arrowmodel converts` | Reword the docstring so Napoleon doesn't parse "X: Y" as a type field, e.g. "Arrow record-batch data that arrowmodel converts -- a RecordBatch, Table or stream." |
| 3 | Low | `how-to/index.rst` | "Process large Tables lazily without materialising every model instance in memory." | Mention streams: "...large Tables and streaming query results (RecordBatchReader, Polars) lazily..." |
| 4 | Low | `explanation/fast-vs-validated.rst` > Performance characteristics | "For flat schemas, the fast path is 3x to 4.5x faster than the pure-Python approach" (the linked file shows 2.42x at 50 rows and 4.77x at 500 rows); "For deeply nested models ... the gap widens" (it widens against the baseline, but against `validate=True` it is about 2.0x at 1,000 rows vs 2.7x flat) | Say "about 2.4x-4.8x (3x-4.5x from 100 rows up)"; say "the gap to the pure-Python baseline widens"; optionally cite the 38,000-row nested row (633 ms / 2,015 ms / 4,091 ms) |
| 5 | Low | `how-to/integrate-fastapi.rst` > Stream large results | Both blocks use `@app.get("/products/export")` | Rename the second to `/products/export-stream`, or say "replace the endpoint above with" |
| 6 | Low | `how-to/iterate-large-datasets.rst` > Handle input errors; `how-to/use-with-poldantic.rst` warning | "or ``.to_frame()`` on a ``Series``, first" | Add "(a one-column frame converts only into a model with one required field; otherwise build a DataFrame with all the columns)". Verified: `.to_frame()` on `df["id"]` raises `ValueError: ... missing required columns: ['name', 'score']` |
| 7 | Low | `reference/api.rst` > ArrowModelConverter.convert example | `pa.Table.from_batches([batch, another_batch])` / `pa.RecordBatchReader.from_batches(batch.schema, [batch, another_batch])` | Define `another_batch`; `another_batch` is undefined, so the block fails if copied |
| 8 | Very low (src message) | `how-to/iterate-large-datasets.rst` > Handle input errors | Shows only the clean `Int64Array` message; for a `ChunkedArray` or `Series` the real message ends "(ValueError: C Data interface error: Unable to interpret C data struct as a Schema)" | Optional: note that the message may carry the underlying error, or trim it in the error text |
