# Editor Report

**Generated:** 2026-10-21
**Files reviewed:** 18 (17 pages under `docs/` plus `README.md`)
**Changes made:** 18
  - BLOCKING: 2
  - SUGGESTION: 11
  - NITPICK: 5

## Summary

The pages changed for the v1.0.1 direct-construction work are accurate against the benchmark run and the source, apart from one benchmark range and one sentence that still said the fast path calls `model_construct`. The rest of the changes bring the unchanged parts of those pages into line with "built as `model_construct` would", tighten a few sentences, and normalize two terminology variants.

Scope: all four passes ran on the changed regions of the seven listed pages, plus anything those changes made inconsistent. The terminology pass ran on every page in `docs/`. No `INTERNAL NOTES FOR EDITOR` blocks were present, so I checked the accuracy claims directly against `benchmarks/results-v1.0.1-direct-construct-2026-10-21.txt`, `benchmarks/bench_convert.py`, `src/arrowmodel/__init__.py` (`_construct_plan`) and `rust/src/construct.rs`.

Verified as accurate (no change):

- Flat `RecordBatch` table: 100 rows at 152 µs / 403 µs / 512 µs (3.4x), 1,000 rows at 1.7 ms / 4.3 ms / 4.8 ms (2.9x), 38,000 rows at 77 ms / 199 ms / 268 ms (3.5x). The schema has seven columns and each time is the best of five rounds (`BenchModel`, `ROUNDS = 5`).
- Fast path versus `to_pylist()` + `model_construct` is "roughly 3x" for flat schemas (2.5x to 5.1x for `RecordBatch`, 3.1x to 4.1x for `Table`). Fast path versus validated is "roughly 2.5x" (2.1x to 4.0x, and 2.6x at 38k rows).
- On nested data the fast path is about 2x faster than validated (1.9x to 2.8x) and slower than the baseline (0.43x to 0.78x). Validated is a little faster than the baseline on every flat row.
- The fallback cases match `_construct_plan`'s `None` returns: an overridden `model_construct` (`RootModel`); an `AliasPath` in the validation alias; leftover keys with `extra="allow"`; leftover keys with `__pydantic_post_init__`, which is set by a `model_post_init` hook or by private attributes. Top-level models never reach the `AliasPath` or extra-key cases, because `_build_field_map` rejects `AliasPath` and only mapped columns are passed in. That is why the doc correctly limits those cases to nested models.
- The Rust `Direct` builder sets `__dict__`, `__pydantic_fields_set__`, `__pydantic_extra__` and `__pydantic_private__`, applies defaults through `get_default(call_default_factory=True, ...)`, and calls `model_post_init(None)`. This matches the claims about field order, `model_fields_set`, defaults and `model_post_init`.

Notes for the author (not changed, not counted):

- `use-validated-mode.rst` says validated mode is "roughly 2-3x slower" for flat schemas. Most measurements fall in that range (median about 2.6x), but the 250-row and 500-row runs are 3.2x and 4.0x. I kept the sentence because it says "roughly"; change it to "2-4x" if you want it to cover every row.
- `fast-vs-validated.rst` cites `benchmarks/bench_convert.py` and the results file by path. These are repository files, not part of the installed package. I kept them because they let readers reproduce the numbers from a clone, and the benchmark script is not CI or test tooling. If you read Rule 1 strictly, cut them back to "the project's benchmarks".
- The `ArrowModelConverter` class docstring in `src/arrowmodel/__init__.py` still says "Uses model_construct for zero-validation fast path." It appears on the autoapi page `reference/api/arrowmodel/index`. Source docstrings are outside this edit, but this one is now stale.
- The unchanged prose still uses ` -- ` as a dash throughout (house style, which the previous editor run left alone). I replaced dashes only in the lines this branch changed.

---

## docs/explanation/fast-vs-validated.rst

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Performance characteristics | Accuracy mismatch: the doc said validated mode is "about 3.5-4.5x slower" than the baseline on the nested benchmark. The results file gives 3.4x (50 rows), 4.7x (100), 4.6x (250), 4.5x (500), 4.1x (1000) and 3.6x (38k). | Changed to "about 3.4-4.7x slower". |

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| The two paths at a glance | The "Pydantic method" row still listed plain `model_construct` for the fast path, which no longer matches the new "How the fast path works" text. | Changed to "`model_construct` semantics (built in Rust)". |
| Performance characteristics | Now that a `validate=True` column has been added, the "Fast path speedup" header was ambiguous (speedup versus which column?). The values are fast versus `to_pylist()`. | Renamed the header to "Fast path vs `to_pylist()`". |
| How the fast path works | The fallback cases were a single 70-word sentence nested two parentheses deep. | Split into a lead-in sentence and a two-item list (overridden `model_construct`; value-dependent nested models). The content is unchanged. |

### NITPICK

| Section | Description | Fix |
|---------|-------------|-----|
| Performance characteristics | Two lines ran to about 100 characters after the earlier edit. | Reflowed to the page's normal wrap width. |

---

## docs/how-to/use-validated-mode.rst

### BLOCKING

| Section | Description | Fix |
|---------|-------------|-----|
| Introduction | Accuracy mismatch: "arrowmodel uses Pydantic's `model_construct` to build model instances". The source now builds instances in Rust from a `_construct_plan` and calls `model_construct` only as a fallback. | Changed to "arrowmodel builds model instances the way Pydantic's `model_construct` does, without running validation." |

---

## docs/reference/api.rst

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| `ArrowModel.iter`, `ArrowModelConverter.__init__`, `model_convert`, `model_iter` (`validate` rows) | After the change to `ArrowModel.convert`, these four rows still read "fast path (`model_construct`)", which suggests a per-row `model_construct` call. | Changed to "fast path (`model_construct` semantics)" in all 4 rows. |

### NITPICK

| Section | Description | Fix |
|---------|-------------|-----|
| `ArrowModel.convert` (`validate` row) | Em dash: replaced 2 instances with "and runs no Pydantic validation" and ", with full Pydantic validation". | Rewrote the cell. |
| `validate` rows | Uneven wrapping ("run. When ``True``," on its own line, plus three lines over-long after the edit above). | Reflowed 5 cells. |

---

## docs/index.rst

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| Overview intro | "roughly 3x faster than `to_pylist()` + Pydantic construction" was vague. Validating construction (`Model(**row)`) was not benchmarked, and the 3x figure is against `model_construct`. | Changed to "`to_pylist()` + `model_construct`". |
| Validated Mode card | "the fast path (`model_construct`, no validation)" did not match the Dict-Free Conversion card above it. | Changed to "(`model_construct` semantics, no validation)". |

---

## docs/tutorials/getting-started.rst

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| Convert to model instances | Repetition: "directly from Arrow buffers in Rust ... directly in Rust". | Changed to "each row became a `User` instance with the same result as `model_construct`. No intermediate Python dicts were created." |

### NITPICK

| Section | Description | Fix |
|---------|-------------|-----|
| Convert to model instances | Em dash: replaced 1 instance with a sentence break. | See above. |

---

## docs/how-to/convert-nested-models.rst

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| Define nested models | Awkward wording: "built just like top-level models: as `model_construct` would build them (or via ... in validated mode)". | Changed to "built the same way as top-level models: with the same result as `model_construct` on the fast path, or through `model_validate_json` in validated mode." |

---

## docs/how-to/use-with-poldantic.rst

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| Why this works without pyarrow | Term normalized: "PyCapsule-compatible" to "Arrow-PyCapsule-compatible" (1 instance), matching the tip below it and the other pages. | Replaced. |

---

## README.md

### SUGGESTION

| Section | Description | Fix |
|---------|-------------|-----|
| Intro paragraph | `to_pylist()` appeared twice in one sentence ("replaces the `to_pylist()` + model-construction two-step ... roughly 3x faster than `to_pylist()` + `Model.model_construct(**row)`"). | Changed to "It replaces the `to_pylist()` + `Model.model_construct(**row)` two-step with a single call that is roughly 3x faster for flat schemas." |
| Input Compatibility | Term normalized: "exports via PyCapsule" to "exports via the Arrow PyCapsule Interface". | Replaced. |

### NITPICK

| Section | Description | Fix |
|---------|-------------|-----|
| Key Features | Em dash: replaced 1 instance ("would -- no `to_pylist()`") with ", with no `to_pylist()` and no intermediate dicts". | Replaced. |

---

## Pass results with no changes

- **Diataxis type integrity:** No blur found in the changed regions. The new fallback detail in the explanation page explains how the fast path works, which suits an explanation page. The how-to's performance section links to `explanation-fast-vs-validated-performance` instead of repeating the numbers. The tutorial change stays a one-sentence recap.
- **Cross-reference linking:** The changed regions contain no unlinked arrowmodel symbols. The `explanation-fast-vs-validated-performance` label exists and resolves. `README.md` is rendered by GitHub, so Sphinx roles do not apply there.
- **Internal notes:** None found, so nothing to strip.

---

## Terminology Changes

| Term | Before | After | Authority |
|------|--------|-------|-----------|
| Arrow PyCapsule Interface | "PyCapsule-compatible" (`use-with-poldantic.rst`), "via PyCapsule" (`README.md`) | "Arrow-PyCapsule-compatible", "via the Arrow PyCapsule Interface" | `terminology.yaml` `project_terms` |
| fast path description | "fast path (`model_construct`)" | "fast path (`model_construct` semantics)" | `_construct_plan` / `rust/src/construct.rs` (direct construction) |

`terminology.yaml` updates:

- Removed the `field_map` source term. `_field_map` is a private attribute, so normalizing prose to it would leak an internal detail (Rule 1). "Field mapping" is now a `project_terms` entry for prose.
- "Validated mode" is no longer listed as a variant of "validated path". It is used consistently in 9 places, including the how-to's title and headings, to name the `validate=True` setting. The new note on the entry records the split: "validated mode" names the setting, "validated path" describes what the conversion does.
- Added "`model_construct` semantics" as the way to describe the fast path.

Build check: `uv run --group docs sphinx-build -W -b html docs docs/_build/html` exited with RC=0.
