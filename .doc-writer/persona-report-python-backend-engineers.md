# Persona Report

**Generated:** 2026-10-21
**Audience:** Python backend engineers (intermediate-advanced)
**Scenarios tested:** 6 (S1-S5 reused from 2026-04-04; S6 added for this release)
**Results:** 5 PASS, 1 PARTIAL, 0 FAIL

## Summary

The release's message is consistent everywhere I looked: README, the Overview page, the tutorial, the how-tos and the reference all say "fast path = `model_construct` semantics, roughly 3x faster than `to_pylist()` + `model_construct` for flat schemas". The rewritten fast-vs-validated explanation is the strongest page in the set. It says exactly what the fast path keeps (same `__dict__` in the same order, `model_fields_set`, defaults and default factories, `model_post_init`), when a real `model_construct` call still happens (overridden `model_construct`, `RootModel`), and it gives a benchmark row at 38,000 rows that maps almost directly onto a 40k-row ADBC result. The one PARTIAL comes from that same page. It says nested models with an `AliasPath` validation alias are handled, which contradicts the aliases how-to and the reference, both of which say `AliasPath` raises `NotImplementedError`. It also leaves the nested-schema trade-off and the null-in-required-field case only partly answered. No FAILs, so nothing triggers a revision pass.

---

## Scenario S1: I want to install arrowmodel and convert an ADBC query result into Pydantic models for a FastAPI endpoint

**Verdict:** PASS

### Navigation Path

1. Started at: `docs/index.rst`
   - Found: overview, a runnable `ArrowModel` example, pip/uv install tabs, and a wheel-platform note (Linux x86_64/aarch64, Apple Silicon, Windows x64; other platforms build from source and need Rust 1.83+). This matches README.md word for word.
   - Followed: "Get Started" button to `getting-started`.
2. Navigated to: `docs/tutorials/getting-started.rst`
   - Found: install (same platform wording), model, batch, `model_convert`, expected output, and a Table variant. The line "each row became a `User` instance with the same result as `model_construct`" sets up the fast-path idea early without overloading a tutorial. Next steps link to the API-style, validated-mode and explanation pages.
   - Type-alignment: correct, a learning-oriented tutorial.
3. Navigated to: `docs/how-to/index.rst`, then `docs/how-to/integrate-fastapi.rst`
   - Found: an ADBC `fetch_arrow_table()` to `Product.convert(table)` to `response_model=list[Product]` example, converter reuse, a validated-mode section and streaming.
   - Minor friction (unchanged from last release): the `import_products(table: pa.Table)` endpoint is not a body type FastAPI can accept, and `pa` is never imported on that page. I can see what it is getting at, but I couldn't paste it.

Goal achieved. The install wording in the tutorial ("the install builds from source") is slightly vaguer than README/index ("pip builds from source"), which is harmless.

---

## Scenario S2: I want to understand which Arrow types map to which Python types for a schema with timestamps, decimals, and nested structs

**Verdict:** PASS

### Navigation Path

1. Started at: `docs/index.rst`, then the sidebar to Explanation, then `docs/explanation/type-mappings.rst`
   - Found: complete tables for integers, floats, decimals (32/64/128/256 to `Decimal`), naive vs tz-aware timestamps (`ZoneInfo`), nanosecond truncation warning, Struct to nested model, Map to `list[tuple[K, V]]`, and null handling.
   - Followed: the Struct row's link to `how-to-convert-nested-models`.
2. Navigated to: `docs/how-to/convert-nested-models.rst`
   - Found: struct, optional struct, null-child vs null-struct, deep nesting, `list[Model]`, and the Map note. The new sentence "built the same way as top-level models: with the same result as `model_construct` on the fast path, or through `model_validate_json` in validated mode" answers how nested instances are built.

Type-alignment note (carried over): the type-mappings page is reference material filed under Explanation. It works, but a reader looking in Reference won't find it there.

---

## Scenario S3: I want to handle aliases because my database returns camelCase columns but my models use snake_case

**Verdict:** PASS

### Navigation Path

1. `docs/index.rst`, then the How-To index, then `docs/how-to/work-with-aliases.rst`
   - Found: priority order, `validation_alias`, `alias`, mixed usage, `populate_by_name`/`validate_by_name`, and the unsupported `AliasPath`/`AliasChoices`/`AliasGenerator` list with the error message.

Goal achieved on this page alone. Cross-page note: `docs/explanation/fast-vs-validated.rst` now says nested models with an `AliasPath` validation alias get a real `model_construct` call. That reads as if `AliasPath` is supported there, which contradicts this page. See S6.

---

## Scenario S4: I want to look up the complete API for ArrowModelConverter

**Verdict:** PASS

### Navigation Path

1. `docs/index.rst`, then the sidebar to Reference, then `docs/reference/index.rst`, then `docs/reference/api.rst`
   - Found: `__init__(model_class, *, validate=False)`, `convert`, `iter`, signatures, parameter tables, returns/yields, raises, and examples. The `validate` descriptions now say "builds instances as `model_construct` would", which matches the explanation page.

Minor reference gaps (none block the goal):
- **Raises** for `convert`/`iter` lists only `ValueError`. The nested-models how-to and type-mappings say a `dict` annotation over a Map column raises `TypeError` at `convert()` time, and that is missing here.
- The `validate` parameter rows don't link to `explanation-fast-vs-validated`, which is where the guarantees and the `model_construct`-override behaviour are described.
- `_get_nested_model` "Handles" doesn't say what `list[Model]` returns. Since `list[Model]` is now a documented nested case, I can't tell whether `None` is expected.
- `_build_field_map` and `_get_nested_model` are underscore-prefixed but documented as exposed helpers, so their stability status isn't clear.

---

## Scenario S5: I want to stream large Arrow query results through FastAPI without loading all rows into memory

**Verdict:** PASS

### Navigation Path

1. `docs/index.rst`, then the How-To index, then `docs/how-to/integrate-fastapi.rst` > "Stream large results"
   - Found: `Product.iter(table)` + `StreamingResponse` NDJSON.
2. `docs/how-to/iterate-large-datasets.rst` > "How batch-at-a-time works"
   - Found: memory is proportional to the largest batch, and a `RecordBatch` input gives no lazy benefit.

Goal (as defined) achieved. Carry-over nuance: the example still loads the full Arrow `Table` via `fetch_arrow_table()`. Only the model instances are lazy. Neither page says whether a `RecordBatchReader` (ADBC `fetch_record_batch()`) is accepted, and that would be the truly streaming option.

---

## Scenario S6 (new): Decide fast path vs `validate=True` for a 40k-row ADBC result, and know what the fast path does and doesn't guarantee

**Verdict:** PARTIAL

### Navigation Path

1. Started at: `docs/index.rst`
   - Found: the "Validated Mode" card ("fast path (`model_construct` semantics, no validation)" vs "`model_validate_json`, full Pydantic checks") and "~3x Faster" for flat schemas. The cards are not clickable, so I used the sidebar.
2. Navigated to: `docs/how-to/use-validated-mode.rst`
   - Found: `validate=True` on all three API styles, what validation catches, "roughly 2-3x slower than the fast path for flat schemas", a link to `explanation-fast-vs-validated-performance`, error handling, and a warning that the fast path never raises `ValidationError`.
   - Followed: the performance link.
3. Navigated to: `docs/explanation/fast-vs-validated.rst`
   - **At a glance table:** answers coercion, validators, constraints and speed.
   - **How the fast path works:** exactly what I needed. Same `__dict__` (same order), same `model_fields_set`, defaults/default factories, `model_post_init` runs. An overridden `model_construct` (and `RootModel`) still gets a real per-row call, so my override is honoured.
   - **Performance table:** the 38,000-row row (77 ms fast / 199 ms validated / 268 ms baseline) is close enough to 40k. My answer is that `validate=True` costs about +120 ms per request and is still a little faster than the old `to_pylist()` path. The numbers agree with each other (about 2.5x) and with the how-to's "2-3x". The flat schema is described (7 primitive/temporal columns, `RecordBatch`, best of 5).
   - **Nested paragraph:** the fast path is about 2x faster than validated but slower than the non-recursive baseline, and validated is 3.4-4.7x slower than that baseline.
   - **NaN/Infinity:** clear for both paths.
   - Type-alignment: correct. I needed understanding plus numbers, and the page is an explanation with a reference-style table. The how-to links to it instead of repeating it.

### Gap Analysis

**Gap 1: AliasPath contradiction**
**Where:** `docs/explanation/fast-vs-validated.rst` > "How the fast path works" > the "Some models still get a real `model_construct` call per row" list
**What:** It lists "nested models ... an `AliasPath` validation alias" as a handled case. `docs/how-to/work-with-aliases.rst` > "Unsupported alias types" and `docs/reference/api.rst` > `ArrowModelConverter.__init__` **Raises** say any `AliasPath` `validation_alias` raises `NotImplementedError` at converter creation.
**Impact:** I can't tell whether `AliasPath` is now allowed on nested models only, or whether the explanation is wrong. This changes whether I can keep an existing model unchanged.
**Suggested Fix:** In `fast-vs-validated.rst`, section "How the fast path works": if `AliasPath` is supported on nested models only, say "(supported on nested models only; top-level `AliasPath` still raises `NotImplementedError`)", and update the "Unsupported alias types" section in `work-with-aliases.rst` and the `ArrowModelConverter.__init__` Raises entry in `api.rst` to match. If it isn't supported, remove it from the list.

**Gap 2: nested trade-off is not quantified for realistic depth**
**Where:** `docs/explanation/fast-vs-validated.rst` > "Performance characteristics", the nested paragraph
**What:** The only nested data point is an extreme 10-level struct-in-struct-with-lists benchmark, given as ratios without absolute times or row count. Nothing says how one or two levels of nesting behave (for example an `address` struct). Nothing says the per-row `model_construct` fallback (overridden `model_construct`, `RootModel`, `extra="allow"` structs) costs speed.
**Impact:** For a nested 40k-row result I can only say "fast is still about 2x faster than validated". I can't estimate milliseconds or tell whether my override pushes me off the fast loop.
**Suggested Fix:** In `fast-vs-validated.rst`, section "Performance characteristics": add a nested row (or small table) with absolute times at a comparable row count. If a shallow-nesting benchmark exists, add one line on it. Add one sentence after the per-row `model_construct` list saying those models give up the batched alias-matching saving and run at roughly `model_construct` speed.

**Gap 3: the most common fast-path hazard for DB results isn't called out**
**Where:** `docs/explanation/fast-vs-validated.rst` > "How the fast path works", trade-off paragraph
**What:** The "quietly wrong value" example is a string in an int field. With ADBC, the more likely surprise is a NULL in a nullable DB column mapped to a non-`Optional` field. From type-mappings ("a null value produces `None`") I infer the fast path silently puts `None` in an `int` field, but no page says so.
**Impact:** That is the guarantee I most need to know about before choosing the fast path for database results.
**Suggested Fix:** In `fast-vs-validated.rst`, section "How the fast path works": add "A null in a column mapped to a non-`Optional` field becomes `None` on the fast path (no error); `validate=True` raises `ValidationError`."

**Gap 4 (minor): benchmark input type vs ADBC output**
**Where:** `docs/explanation/fast-vs-validated.rst` > "Performance characteristics"
**What:** The benchmarks use `RecordBatch` input, while ADBC `fetch_arrow_table()` returns a multi-chunk `Table`. The page doesn't say whether the numbers carry over. It also cites `benchmarks/...txt` repository paths that a docs-site reader can't open.
**Suggested Fix:** Add one sentence saying whether `Table` input performs the same, and link the benchmark files to the repository instead of giving plain paths.

**Gap 5 (minor, optional): FastAPI `response_model` interaction**
**Where:** `docs/how-to/use-validated-mode.rst` > "Performance trade-off" (or `integrate-fastapi.rst` > "Use validated mode for untrusted data")
**What:** The docs don't say how the fast/validated choice interacts with FastAPI's own `response_model` handling of the returned list. A FastAPI user deciding on a mode will ask this.
**Suggested Fix:** Add a short note on what FastAPI does with fast-path instances under `response_model` and whether that changes the recommendation. Verify FastAPI's behaviour before writing it.

Also noticed: the `ValidationError` sample output in `use-validated-mode.rst` > "Handle validation errors" uses the Pydantic v1 style `(type=int_parsing)`. Pydantic v2 prints `[type=int_parsing, input_value=..., input_type=str]`.

---

## Revision Recommendations

### FAIL Issues (trigger revision)

None. No revision pass needed.

### PARTIAL Issues (for project author approval)

| Scenario | Page | Gap | Suggested Fix |
|----------|------|-----|---------------|
| S6 | `docs/explanation/fast-vs-validated.rst` > How the fast path works | `AliasPath` listed as handled for nested models; aliases how-to and reference say it raises `NotImplementedError` | Say "nested only; top-level still raises" and align `work-with-aliases.rst` + `api.rst` Raises, or remove it from the list |
| S6 | `docs/explanation/fast-vs-validated.rst` > Performance characteristics | Nested trade-off given only as ratios for a 10-level benchmark; per-row `model_construct` fallback cost not stated | Add absolute nested timings at a comparable row count; one sentence on fallback-model speed |
| S6 | `docs/explanation/fast-vs-validated.rst` > How the fast path works | NULL in a non-`Optional` field on the fast path not stated | Add: becomes `None` silently on the fast path; `validate=True` raises |
| S6 | `docs/explanation/fast-vs-validated.rst` > Performance characteristics | `RecordBatch`-only benchmark; repo-path citations not reachable | State whether `Table` input matches; link the benchmark files |
| S6 | `docs/how-to/use-validated-mode.rst` > Performance trade-off | FastAPI `response_model` interplay not covered | Short note (after verifying FastAPI behaviour) |
| S6 | `docs/how-to/use-validated-mode.rst` > Handle validation errors | v1-style error output sample | Update to the Pydantic v2 format |
| S4 | `docs/reference/api.rst` > convert/iter Raises | `TypeError` (dict annotation over a Map column) missing; `validate` rows don't link to the explanation | Add the `TypeError` entry; link `explanation-fast-vs-validated` from the `validate` rows |
| S1 | `docs/how-to/integrate-fastapi.rst` > Use validated mode for untrusted data | `table: pa.Table` is not a valid FastAPI body; `pa` not imported | Show reading an uploaded Parquet file into a Table inside the handler; add `import pyarrow as pa` |
| S5 | `docs/how-to/integrate-fastapi.rst` > Stream large results | Full Table still loaded; `RecordBatchReader` support unstated | State whether `RecordBatchReader`/`fetch_record_batch()` is accepted; if so, show it |
| S6 | `docs/index.rst` > feature cards | "Validated Mode" card is not clickable | Add `:link: explanation-fast-vs-validated` / `:link-type: ref` |
