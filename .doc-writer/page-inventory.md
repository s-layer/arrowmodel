# Page Inventory

**Generated:** 2026-10-21
**Doc system:** sphinx-shibuya (RST)
**Navigation:** Sidebar only
**API Reference:** sphinx-autoapi (plus the hand-written `docs/reference/api.rst`)
**Scope:** content refresh after the fast-path direct-construction change (branch `claude/project-thread-m8qwj3`, commits `151b8b5`, `f1bf468`, `ff815f8` since `8e885a0`)

## Proposed Documentation

No new pages. The public API (`ArrowModel`, `ArrowModelConverter`, `model_convert`, `model_iter`) and its signatures did not change, so 0 symbols are undocumented. What changed is how the fast path works inside and how fast it is. This is a refresh-only pass.

## Content Refresh

What changed for users: with `validate=False`, arrowmodel no longer calls `model_construct` for every row. It builds each instance itself, with the same result `model_construct` would give: same `__dict__` order, same `model_fields_set`, defaults and default factories, `model_post_init`, and private attributes. A model that overrides `model_construct` (`RootModel`, or a user subclass) still gets its own `model_construct` called, so overrides are still honoured. Flat schemas are now about 3x faster than `to_pylist()` + `model_construct` and about 2.5x faster than `validate=True`.

Wording like "the fast path uses `model_construct` semantics / skips validation" is still true and is **not** flagged. The list below covers only text that is now wrong, or that tells the reader something is called per row when it no longer is.

| # | Type | Title | Key Sections | File Path |
|---|------|-------|--------------|-----------|
| 1 | (refresh) | Understanding Fast Path vs Validated Path | Mostly accurate (already refreshed). One wrong number: "Performance characteristics", last paragraph, says the validated path is "about 3.5x slower" than `to_pylist()` + `model_construct` on the nested benchmark. The committed results show 3.4x to 4.7x (50 rows 3.4x, 100 to 500 rows about 4.5x, 1,000 rows 4.1x, 38k rows 3.6x). Change it to "about 3.5-4.5x". Optional: the fallback sentence ("How the fast path works", paragraph 3) leaves out one case: nested structs with extra fields on a model that has a `model_post_init` hook (or private attributes) also fall back to a real `model_construct`. Users get the same result either way, so add this only if the list is meant to be complete. Every other figure matches `benchmarks/results-v1.0.1-direct-construct-2026-10-21.txt` (flat 100 rows: 152 µs / 403 µs / 512 µs, 3.4x; 1,000 rows: 1.7 / 4.3 / 4.8 ms, 2.9x; 38k rows: 77 / 199 / 268 ms, 3.5x; nested fast about 2x faster than validated; validated a little faster than the baseline on flat schemas). | docs/explanation/fast-vs-validated.rst |
| 2 | (refresh) | Overview (landing) | No changes needed. "Roughly 3x faster than `to_pylist()` + Pydantic construction for flat schemas" matches the benchmark. The "Dict-Free Conversion" card ("exactly as `model_construct` would") and the "Validated Mode" card ("fast path (`model_construct`, no validation)") describe the behaviour correctly. | docs/index.rst |
| 3 | (refresh) | API Reference (hand-written) | No changes needed. Five parameter descriptions say "fast path (`model_construct`)" / "via `model_construct`" (lines 93, 147, 212, 374, 445). This names what you get, and that is still accurate: same result, no validation, and overrides of `model_construct` are still called. Optional: change "uses the fast path via `model_construct`" (line 93) to "builds instances as `model_construct` would" to match the explanation page. | docs/reference/api.rst |
| 4 | (refresh) | How to Convert Nested Models | One sentence is now inaccurate. Line 52: "The nested `Address` instances are built with `model_construct` (or `model_validate_json` in validated mode)". Nested instances are no longer built by calling `model_construct`. Reword to "built the same way as top-level models: as `model_construct` would build them (or with `model_validate_json` in validated mode)". Nothing else on the page changed: null-struct vs null-child handling, list-of-struct, and Map-as-list-of-tuples are unaffected. | docs/how-to/convert-nested-models.rst |
| 5 | (refresh) | How to Use Validated Mode | No changes needed. Line 9 ("arrowmodel uses Pydantic's `model_construct` to build model instances without running validation") describes the behaviour and is still true for users. "Performance trade-off" says "Expect roughly 2-5x slower conversion compared to the fast path". The benchmark shows 1.9x to 4.0x (flat about 2.5x, nested about 2x), so the claim is not wrong, but it is a little wide. Optional: tighten to "roughly 2-3x slower" and link to the benchmark table in :ref:`explanation-fast-vs-validated`. | docs/how-to/use-validated-mode.rst |
| 6 | (refresh) | Getting Started | One sentence is now inaccurate. Line 125-126: "each row was assembled into a `User` instance via `model_construct`". Rows are no longer passed through `model_construct`. Reword to "each `User` instance was built directly in Rust, with the same result as `model_construct` (no validation)". The rest of the tutorial is accurate. | docs/tutorials/getting-started.rst |
| 7 | (readme) | README.md | Small fix. The intro says arrowmodel "replaces the `to_pylist()` + `Model(**row)` two-step with a single call that is roughly 3x faster for flat schemas". The 3x was measured against `to_pylist()` + `model_construct`. `Model(**row)` runs validation, so the claim compares the unvalidated fast path with a validating baseline. Change the baseline to `Model.model_construct(**row)`, or say "roughly 3x faster than `to_pylist()` + `model_construct`". Key Features ("exactly as `model_construct` would", "~3x faster for flat schemas") are accurate. | README.md |

## API Reference Status

**Detected:** sphinx-autoapi (`autoapi_root = "reference/api"`), plus the hand-written `docs/reference/api.rst`.

Two notes for autoapi output (not flagged pages):

- `_construct_plan` was added to `__all__` in `src/arrowmodel/__init__.py`. If autoapi follows `__all__`, it will now publish this private helper and its implementation-level docstring in the generated reference. The gap report treats it as private. Consider removing it from `__all__` (the Rust side gets it with `getattr` on the module) or excluding it from autoapi. `_build_field_map` and `_get_nested_model` are already in `__all__` and are documented on purpose in `api.rst` as introspection helpers. `_construct_plan` is not meant for users.
- Several docstrings that autoapi renders still say "Uses model_construct for zero-validation fast path" (`ArrowModelConverter` class docstring, line 228) and "fast path via model_construct" (lines 385, 412, 431). These are accurate as statements about semantics, like row 3 above. No change is required.

## Audience Targeting

All pages target the single configured persona: **Python backend engineers** (intermediate-advanced). All the refreshes are one-sentence or one-number edits, and none changes the tone or structure of its page.

## Coverage Gaps

- `.doc-writer/config.yaml` `use_cases[0].capability` still says "typically 2x faster than pure-Python approaches". It is not a docs page and `use_cases_section` is `false`, but it feeds context for future writing passes. Update it to "about 3x faster for flat schemas".
- Nested-schema performance: the fast path is slower than `to_pylist()` + `model_construct` on the 10-level nested benchmark (0.43x to 0.78x). The reason is that the baseline does not build nested models. The explanation page covers this, but no how-to page tells users with deeply nested models what to expect. This is acceptable for now. Revisit it if users report confusion.
- Unchanged pages not flagged (`choose-api-style`, `work-with-aliases`, `iterate-large-datasets`, `integrate-fastapi`, `use-with-pandera`, `use-with-poldantic`, `type-mappings`): a grep for `model_construct`, speed multipliers and "per row" found no hits that this change affects.
