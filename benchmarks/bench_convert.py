"""
Benchmark: arrowmodel's fast and validated paths vs to_pylist() + model_construct.

Every path starts from the same pre-created data, and only the conversion is
measured (not batch/table creation). Each time is the best of ROUNDS rounds.

Columns:
    fast           ArrowModelConverter(model).convert(data)
    validate=True  ArrowModelConverter(model, validate=True).convert(data)
    to_pylist+mc   [model.model_construct(**row) for row in data.to_pylist()]
                   (nested benchmark: construct_recursive, which also builds the
                   nested models, since model_construct does not recurse)
    vs pylist      to_pylist+mc time / fast time (higher means fast is quicker)
    vs valid.      validate=True time / fast time

Iteration counts are normalised so that each benchmark processes
~TARGET_INSTANCES model instances regardless of row count, giving
comparable wall-clock times across sizes.

Usage:
    uv run python benchmarks/bench_convert.py  # defaults: 50, 100, 250, 500, 1000
    uv run python benchmarks/bench_convert.py 1000 10000 100000
    uv run python benchmarks/bench_convert.py 38000  # the size semolina reported on
"""

from __future__ import annotations

import datetime
import sys
import time
import typing
from typing import TYPE_CHECKING, Any, cast

import pyarrow as pa
from pydantic import BaseModel

from arrowmodel import ArrowModelConverter

if TYPE_CHECKING:
    from collections.abc import Callable

DEFAULT_SIZES = [50, 100, 250, 500, 1000]
TARGET_INSTANCES = 500_000
NESTING_DEPTH = 10
TABLE_BATCHES = 10
ROUNDS = 5


# ---------------------------------------------------------------------------
# Flat benchmark models and data
# ---------------------------------------------------------------------------


class BenchModel(BaseModel):
    id: int
    name: str
    score: float
    active: bool
    birthday: datetime.date
    created_at: datetime.datetime
    elapsed: datetime.timedelta


def make_batch(n: int) -> pa.RecordBatch:
    """Create a RecordBatch with n rows of mixed primitive + temporal types."""
    return pa.record_batch(
        {
            "id": list(range(n)),
            "name": [f"item_{i}" for i in range(n)],
            "score": [float(i) * 0.1 for i in range(n)],
            "active": [i % 2 == 0 for i in range(n)],
            "birthday": pa.array(
                [datetime.date(1990, 1, 1) + datetime.timedelta(days=i % 10000) for i in range(n)],
                type=pa.date32(),
            ),
            "created_at": pa.array(
                [datetime.datetime(2024, 1, 1, 12, 0, 0) for _ in range(n)],
                type=pa.timestamp("us"),
            ),
            "elapsed": pa.array(
                [datetime.timedelta(seconds=i) for i in range(n)],
                type=pa.duration("us"),
            ),
        }
    )


# ---------------------------------------------------------------------------
# Nested benchmark models (10-level deep structs with list fields)
# ---------------------------------------------------------------------------


class Level10(BaseModel):
    value: int
    label: str


class Level9(BaseModel):
    child: Level10 | None
    score: float


class Level8(BaseModel):
    child: Level9 | None
    tags: list[str] | None = None


class Level7(BaseModel):
    child: Level8 | None
    count: int


class Level6(BaseModel):
    child: Level7 | None
    active: bool


class Level5(BaseModel):
    child: Level6 | None
    name: str


class Level4(BaseModel):
    child: Level5 | None
    ids: list[int] | None = None


class Level3(BaseModel):
    child: Level4 | None
    ratio: float


class Level2(BaseModel):
    child: Level3 | None
    code: str


class Level1(BaseModel):
    child: Level2 | None
    timestamp: float


class NestedBenchModel(BaseModel):
    id: int
    name: str
    data: Level1 | None


def make_nested_batch(n: int) -> pa.RecordBatch:
    """Create a RecordBatch with n rows of 10-level nested structs + lists."""
    # Build struct arrays bottom-up
    # Level10: struct{value: int32, label: utf8}
    level10_arr = pa.StructArray.from_arrays(
        [
            pa.array([i for i in range(n)], type=pa.int32()),
            pa.array([f"val_{i}" for i in range(n)], type=pa.utf8()),
        ],
        names=["value", "label"],
    )

    # Level9: struct{child: Level10, score: float64}
    level9_arr = pa.StructArray.from_arrays(
        [
            level10_arr,
            pa.array([float(i) * 0.1 for i in range(n)], type=pa.float64()),
        ],
        names=["child", "score"],
    )

    # Level8: struct{child: Level9, tags: list(utf8)} -- 3 tags per row
    tags_arr = pa.array(
        [[f"t_{i}_0", f"t_{i}_1", f"t_{i}_2"] for i in range(n)],
        type=pa.list_(pa.utf8()),
    )
    level8_arr = pa.StructArray.from_arrays(
        [level9_arr, tags_arr],
        names=["child", "tags"],
    )

    # Level7: struct{child: Level8, count: int32}
    level7_arr = pa.StructArray.from_arrays(
        [
            level8_arr,
            pa.array([i * 10 for i in range(n)], type=pa.int32()),
        ],
        names=["child", "count"],
    )

    # Level6: struct{child: Level7, active: bool}
    level6_arr = pa.StructArray.from_arrays(
        [
            level7_arr,
            pa.array([i % 2 == 0 for i in range(n)], type=pa.bool_()),
        ],
        names=["child", "active"],
    )

    # Level5: struct{child: Level6, name: utf8}
    level5_arr = pa.StructArray.from_arrays(
        [
            level6_arr,
            pa.array([f"name_{i}" for i in range(n)], type=pa.utf8()),
        ],
        names=["child", "name"],
    )

    # Level4: struct{child: Level5, ids: list(int32)} -- 5 ids per row
    ids_arr = pa.array(
        [list(range(i, i + 5)) for i in range(n)],
        type=pa.list_(pa.int32()),
    )
    level4_arr = pa.StructArray.from_arrays(
        [level5_arr, ids_arr],
        names=["child", "ids"],
    )

    # Level3: struct{child: Level4, ratio: float64}
    level3_arr = pa.StructArray.from_arrays(
        [
            level4_arr,
            pa.array([float(i) * 1.5 for i in range(n)], type=pa.float64()),
        ],
        names=["child", "ratio"],
    )

    # Level2: struct{child: Level3, code: utf8}
    level2_arr = pa.StructArray.from_arrays(
        [
            level3_arr,
            pa.array([f"code_{i}" for i in range(n)], type=pa.utf8()),
        ],
        names=["child", "code"],
    )

    # Level1: struct{child: Level2, timestamp: float64}
    level1_arr = pa.StructArray.from_arrays(
        [
            level2_arr,
            pa.array([float(i) * 0.01 for i in range(n)], type=pa.float64()),
        ],
        names=["child", "timestamp"],
    )

    # Top level: int64 "id", utf8 "name", Level1 struct "data"
    return pa.record_batch(
        {
            "id": pa.array(list(range(n)), type=pa.int64()),
            "name": pa.array([f"item_{i}" for i in range(n)], type=pa.utf8()),
            "data": level1_arr,
        }
    )


# ---------------------------------------------------------------------------
# Benchmark runners
# ---------------------------------------------------------------------------


def _format_time(seconds: float) -> str:
    """Format a time duration as a human-readable string."""
    if seconds < 1e-3:
        return f"{seconds * 1e6:.0f} µs"
    return f"{seconds * 1e3:.1f} ms"


_COLUMNS = ("Rows", "Iters", "fast", "validate=True", "to_pylist+mc", "vs pylist", "vs valid.")
_WIDTHS = (8, 6, 12, 13, 12, 9, 9)


def _print_row(*cells: str) -> None:
    print(" | ".join(f"{cell:>{width}}" for cell, width in zip(cells, _WIDTHS, strict=True)))


def _print_header() -> None:
    _print_row(*_COLUMNS)
    print("-+-".join("-" * width for width in _WIDTHS))


def _best_of(fn: Callable[[], object], iters: int) -> float:
    """Best per-call time over ROUNDS rounds of ``iters`` calls (after one warmup call)."""
    fn()
    best = float("inf")
    for _ in range(ROUNDS):
        t0 = time.perf_counter()
        for _ in range(iters):
            fn()
        best = min(best, (time.perf_counter() - t0) / iters)
    return best


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    """The BaseModel in ``Model`` or ``Model | None``, else None."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for arg in typing.get_args(annotation):
        if isinstance(arg, type) and issubclass(arg, BaseModel):
            return arg
    return None


def construct_recursive(model: type[BaseModel], row: dict[str, Any]) -> BaseModel:
    """
    ``model_construct`` that also builds nested models from nested dicts.

    ``model_construct`` does not recurse, so ``to_pylist()`` + ``model_construct``
    alone leaves every nested level as a plain dict. This baseline does the same
    work as arrowmodel: one model instance per struct value.
    """
    kwargs: dict[str, Any] = {}
    for name, value in row.items():
        field = model.model_fields.get(name)
        nested = _nested_model(field.annotation) if field is not None else None
        if nested is not None and isinstance(value, dict):
            value = construct_recursive(nested, cast("dict[str, Any]", value))
        kwargs[name] = value
    return model.model_construct(**kwargs)


def _run(
    model: type[BaseModel],
    data: pa.RecordBatch | pa.Table,
    rows: int,
    iters: int,
    *,
    recursive_baseline: bool = False,
) -> None:
    fast = ArrowModelConverter(model)
    validated = ArrowModelConverter(model, validate=True)
    t_fast = _best_of(lambda: fast.convert(data), iters)
    t_valid = _best_of(lambda: validated.convert(data), iters)
    if recursive_baseline:
        t_base = _best_of(
            lambda: [construct_recursive(model, row) for row in data.to_pylist()], iters
        )
    else:
        t_base = _best_of(lambda: [model.model_construct(**row) for row in data.to_pylist()], iters)
    _print_row(
        str(rows),
        str(iters),
        _format_time(t_fast),
        _format_time(t_valid),
        _format_time(t_base),
        f"{t_base / t_fast:.2f}x",
        f"{t_valid / t_fast:.2f}x",
    )


def run_benchmark(sizes: list[int]) -> None:
    _print_header()
    for n in sizes:
        _run(BenchModel, make_batch(n), n, max(1, TARGET_INSTANCES // n))


def run_nested_benchmark(sizes: list[int]) -> None:
    # model_construct does not recurse into nested dicts, so the baseline here is
    # construct_recursive: it builds every nested model, as arrowmodel does.
    _print_header()
    for n in sizes:
        _run(
            NestedBenchModel,
            make_nested_batch(n),
            n,
            max(1, TARGET_INSTANCES // (n * NESTING_DEPTH)),
            recursive_baseline=True,
        )


def run_table_benchmark(sizes: list[int]) -> None:
    _print_header()
    for n in sizes:
        table = pa.Table.from_batches([make_batch(n) for _ in range(TABLE_BATCHES)])
        total_rows = n * TABLE_BATCHES
        _run(BenchModel, table, total_rows, max(1, TARGET_INSTANCES // total_rows))


if __name__ == "__main__":
    sizes = [int(arg) for arg in sys.argv[1:]] if len(sys.argv) > 1 else DEFAULT_SIZES
    print("=== Flat Primitives (RecordBatch) ===\n")
    run_benchmark(sizes)
    print("\n=== Nested (10-level struct + lists; baseline builds nested models too) ===\n")
    run_nested_benchmark(sizes)
    print(f"\n=== Flat Primitives (Table, {TABLE_BATCHES} batches) ===\n")
    run_table_benchmark(sizes)
