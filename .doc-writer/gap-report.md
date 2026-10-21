# Gap Report

**Generated:** 2026-10-21
**Source root:** src/
**Language:** Python
**Scope:** release after PR #36 (fast path builds instances without per-row `model_construct`; benchmark now times `validate=True`)
**Total undocumented symbols:** 0
**Potentially stale pages:** 6

## Undocumented Symbols

None. The public API (`ArrowModel`, `ArrowModelConverter`, `model_convert`, `model_iter`) is unchanged. The new `_construct_plan` helper is private (underscore-prefixed, internal to the Rust bridge) and is documented by its docstring and MAINTAINER.md, not the user docs.

## Potentially Stale Pages

These pages describe how the fast path builds instances or quote performance, both of which changed in PR #36 (`rust/src/construct.rs`, `src/arrowmodel/__init__.py::_construct_plan`):

- `docs/explanation/fast-vs-validated.rst` (refreshed in PR #36; verify)
- `docs/index.rst` (refreshed in PR #36; verify)
- `docs/reference/api.rst` (says fast path "uses model_construct" in 5 places)
- `docs/how-to/convert-nested-models.rst` (line 52: nested models "built with model_construct")
- `docs/how-to/use-validated-mode.rst` (line 9: "arrowmodel uses Pydantic's model_construct")
- `docs/tutorials/getting-started.rst` (line 126: "assembled ... via model_construct")
