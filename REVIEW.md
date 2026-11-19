# Review rules

What a reviewer (the review bot or a person) should look for in a pull request here.
AGENTS.md and CLAUDE.md say how to work in this repository; this file says what to check
in a diff.

## Severity

Tag every finding with one of these:

- **Important**: blocks the merge. A wrong result, a crash, a security problem, a test
  that would pass on broken code, or a break of one of the checks below.
- **Nit**: worth fixing but does not block. Naming, clarity, a small missing test, docs.
- **Pre-existing**: a problem the diff did not cause but sits next to it. Label it so it
  is not mistaken for a regression. It does not block this PR.

Lead with the Important findings. If there are none, say so in one line.

## What to look for

- Equivalence: arrowmodel's output equals `[Model(**row) for row in table.to_pylist()]`
  (and `model_validate` for the validated path) for every Arrow type it accepts. A
  difference is a bug unless it is documented.
- Rust safety: no `unwrap` or `expect` on data-dependent values that can panic across the
  PyO3 boundary, correct handling of nulls, offsets and slices in Arrow buffers, and the
  GIL held where Python objects are touched.
- Speed is the point of the library: a change on the hot path says what it does to the
  benchmarks.

## Diff-triggered checks

Apply a check only when its trigger matches the diff. Each one comes from a miss that
already happened, named under "Source".

- **When the diff touches AGENTS.md, CLAUDE.md, REVIEW.md or anything under `.claude/`**
  Check: Important: list every rule the change adds, loosens or removes, and review the
  rest of the diff against the base branch's copy of these files, not the PR's.
  Source: the review bot reads these files from the PR's own checkout (checked
  2026-11-19), so a PR can rewrite the rules it is reviewed against.
- **When the diff adds or changes support for an Arrow type, nesting or a Pydantic
  feature**
  Check: the Hypothesis equivalence test against `to_pylist()` + Pydantic generates it, on
  both the fast and the validated path.
  Source: the equivalence property found two bugs when it was added (#39).
- **When the diff changes how validation errors are raised**
  Check: tests use `pytest.raises(ValidationError)` and assert the error's `loc` and
  `type`.
  Source: two tests accepted any exception, one checking only `len(str(e)) > 10`.
- **When the diff fixes a bug**
  Check: a test that reproduces the bug and fails without the fix (a pytest case over the
  built extension, or a Rust unit test for internal logic). When one fix has several
  cases, each case needs its own observed failure, not just the run.
  Source: the failing-test-first rule in CLAUDE.md / AGENTS.md.
- **When the diff adds or changes a test**
  Check: the assertions would fail if the behaviour were wrong. Flag `len(x) > 0`, a
  substring check standing in for a result, `pytest.raises(Exception)` where a specific
  error type exists, fixtures where different answers produce the same number, and checks
  on a field the fixture or generator never varies.
  Source: the 2026-11 test review, which found each of these in s-layer repos
  (`pytest.raises(Exception)` in arrowmodel, a one-row fixture in dbt-s-layer where
  revenue, paid revenue and average order value were all 10).
- **When the diff adds `pytest.skip`, `skipif`, `importorskip` or an `xfail`**
  Check: under CI a skip fails the run unless its reason is allowed in `tests/ci_skips.py`
  with why. A new skip needs CI to install what it needs, a `-m` deselect in the job that
  cannot run it, or an allowlist entry with a reason. A non-strict xfail is Important.
  Source: the 2026-11 test review: 31 dbt-open-sl tests and 8 dbt-s-layer engine tests
  skipped in every CI run while CI stayed green.
- **When the diff deletes, skips or loosens a test, or narrows what a generator produces**
  Check: Important unless the PR says why. A refactor carries its tests with it.
  Source: the coverage-forward refactoring rule (duckdb-semantic-views CLAUDE.md), which
  applies to every s-layer repo.
- **When the diff adds a retry, a rerun, a longer timeout or a sleep to get a test
  passing**
  Check: Important. A flaky test is fixed, or deleted with the reason written down. It is
  never rerun into green.
  Source: standing project rule since 2026-10-19 (never re-run CI for a lucky green).
- **When the diff changes a `uses:` line under `.github/workflows/`**
  Check: `.github/workflows/actions.lock` changes in the same PR, and refs are tags, not
  branches or SHAs.
  Source: the GitHub Actions lockfile section in CLAUDE.md / AGENTS.md; a branch ref broke
  every job's setup when the branch was force-pushed.
