"""
arrowmodel against its reference: ``to_pylist()`` + Pydantic, on generated Arrow tables.

arrowmodel exists to be a faster way of writing

- ``[Model.model_construct(**row) for row in table.to_pylist()]`` (``validate=False``), and
- ``[Model.model_validate(row) for row in table.to_pylist()]`` (``validate=True``).

These properties generate Arrow schemas and tables (integers, floats, decimals, strings,
binaries, dates, times, timestamps with and without zones, durations, dictionaries, lists,
nested lists and structs, with nulls) and assert arrowmodel gives exactly what the reference
gives. The documented differences are applied to the reference, not hidden in the comparison:
a struct becomes a nested model built the same way, and in validated mode a NaN or infinite
float becomes ``None`` (JSON has no NaN), so validated tables draw finite floats only.
"""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal
from typing import Any, Literal, cast

import pyarrow as pa
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict, create_model

from arrowmodel import model_convert

# --------------------------------------------------------------------------------------
# Column kinds: an Arrow type, a strategy for its Python values, and the field annotation
# --------------------------------------------------------------------------------------


class Kind:
    def __init__(self, arrow: pa.DataType, values: st.SearchStrategy[Any], annotation: Any) -> None:
        self.arrow = arrow
        self.values = values
        self.annotation = annotation


def _floats(width: Literal[32, 64], finite: bool) -> st.SearchStrategy[float]:
    return st.floats(width=width, allow_nan=not finite, allow_infinity=not finite)


def _trunc(unit: str) -> Any:
    """Drop the sub-unit part a coarser Arrow unit cannot hold."""

    def cut(value: Any) -> Any:
        if unit == "s":
            return value.replace(microsecond=0)
        if unit == "ms":
            return value.replace(microsecond=value.microsecond // 1000 * 1000)
        return value

    return cut


def _timedelta(unit: str) -> st.SearchStrategy[dt.timedelta]:
    scale = {"s": 1_000_000, "ms": 1000, "us": 1}[unit]
    return st.integers(-(10**15) // scale, 10**15 // scale).map(
        lambda n: dt.timedelta(microseconds=n * scale)
    )


# One range for every timestamp unit and zone: inside datetime's range after any offset.
_DATETIMES = st.datetimes(min_value=dt.datetime(1900, 1, 2), max_value=dt.datetime(2200, 1, 1))  # noqa: DTZ001


def _zoned(unit: str, zone: str) -> st.SearchStrategy[dt.datetime]:
    return _DATETIMES.map(_trunc(unit)).map(lambda d: d.replace(tzinfo=dt.UTC))


def primitive_kinds(finite: bool) -> list[Kind]:
    kinds = [
        Kind(pa.bool_(), st.booleans(), bool),
        Kind(pa.int8(), st.integers(-(2**7), 2**7 - 1), int),
        Kind(pa.int32(), st.integers(-(2**31), 2**31 - 1), int),
        Kind(pa.int64(), st.integers(-(2**63), 2**63 - 1), int),
        Kind(pa.uint8(), st.integers(0, 2**8 - 1), int),
        Kind(pa.uint64(), st.integers(0, 2**64 - 1), int),
        Kind(pa.float32(), _floats(32, finite), float),
        Kind(pa.float64(), _floats(64, finite), float),
        Kind(
            pa.decimal128(20, 5),
            st.decimals(min_value=-(10**14), max_value=10**14, places=5),
            Decimal,
        ),
        Kind(
            pa.decimal256(50, 10),
            st.decimals(min_value=-(10**39), max_value=10**39, places=10),
            Decimal,
        ),
        Kind(pa.string(), st.text(max_size=8), str),
        Kind(pa.large_string(), st.text(max_size=8), str),
        Kind(pa.string_view(), st.text(max_size=20), str),
        Kind(pa.binary(), st.binary(max_size=8), bytes),
        Kind(pa.large_binary(), st.binary(max_size=8), bytes),
        Kind(pa.binary(3), st.binary(min_size=3, max_size=3), bytes),
        Kind(pa.date32(), st.dates(), dt.date),
        Kind(pa.time32("s"), st.times().map(_trunc("s")), dt.time),
        Kind(pa.time32("ms"), st.times().map(_trunc("ms")), dt.time),
        Kind(pa.time64("us"), st.times(), dt.time),
        Kind(pa.duration("s"), _timedelta("s"), dt.timedelta),
        Kind(pa.duration("ms"), _timedelta("ms"), dt.timedelta),
        Kind(pa.duration("us"), _timedelta("us"), dt.timedelta),
    ]
    for unit in ("s", "ms", "us"):
        kinds.append(Kind(pa.timestamp(unit), _DATETIMES.map(_trunc(unit)), dt.datetime))
        for zone in ("UTC", "America/New_York", "Asia/Kolkata"):
            kinds.append(Kind(pa.timestamp(unit, tz=zone), _zoned(unit, zone), dt.datetime))
    return kinds


_struct_counter = 0

CONFIG = ConfigDict(val_json_bytes="base64")
"""Validated mode sends binary as base64 text; this opts the models into decoding it.

Without it a ``bytes`` field gets the base64 text itself (see
``test_binary_validated_base64bytes``), which ``model_validate`` on the row would not.
"""


def _list_kind(inner: Kind, large: bool) -> Kind:
    arrow = pa.large_list(inner.arrow) if large else pa.list_(inner.arrow)
    return Kind(
        arrow, st.lists(st.none() | inner.values, max_size=3), list[inner.annotation | None]
    )


def _struct_kind(members: list[Kind]) -> Kind:
    global _struct_counter  # noqa: PLW0603 - unique class names only
    _struct_counter += 1
    names = [f"m{index}" for index in range(len(members))]
    nested = create_model(  # pyright: ignore[reportCallIssue, reportUnknownVariableType]
        f"Nested{_struct_counter}",
        __config__=CONFIG,
        **{name: (kind.annotation | None, None) for name, kind in zip(names, members, strict=True)},  # pyright: ignore[reportArgumentType]
    )
    arrow = pa.struct([(name, kind.arrow) for name, kind in zip(names, members, strict=True)])
    values = st.fixed_dictionaries(
        {name: st.none() | kind.values for name, kind in zip(names, members, strict=True)}
    )
    return Kind(arrow, values, nested)


DICTIONARY = Kind(pa.dictionary(pa.int32(), pa.string()), st.sampled_from(["a", "bb", "ccc"]), str)


def kinds(finite: bool) -> st.SearchStrategy[Kind]:
    prims = st.sampled_from([*primitive_kinds(finite), DICTIONARY])
    lists = st.builds(_list_kind, prims, st.booleans())
    nested_lists = st.builds(_list_kind, lists, st.booleans())
    structs = st.builds(_struct_kind, st.lists(prims | lists, min_size=1, max_size=3))
    return prims | lists | nested_lists | structs


@st.composite
def models_and_tables(draw: st.DrawFn, finite: bool) -> tuple[type[BaseModel], pa.Table]:
    columns = draw(st.lists(kinds(finite), min_size=1, max_size=5))
    rows = draw(st.integers(0, 6))
    names = [f"c{index}" for index in range(len(columns))]
    model = create_model(  # pyright: ignore[reportCallIssue, reportUnknownVariableType]
        "Row",
        __config__=CONFIG,
        **{name: (kind.annotation | None, None) for name, kind in zip(names, columns, strict=True)},  # pyright: ignore[reportArgumentType]
    )
    arrays = [
        pa.array(draw(st.lists(st.none() | kind.values, min_size=rows, max_size=rows)), kind.arrow)
        for kind in columns
    ]
    table = pa.Table.from_arrays(arrays, names=names)
    # pyarrow 24 builds invalid dictionary indices under a null struct row (index 0 into an
    # empty dictionary); that is a pyarrow bug, not input arrowmodel promises to handle.
    assume(_is_valid(table))
    if rows > 1 and draw(st.booleans()):
        cut = draw(st.integers(1, rows - 1))
        table = pa.concat_tables([table.slice(0, cut), table.slice(cut)])  # two batches
    return cast("type[BaseModel]", model), table


def _is_valid(table: pa.Table) -> bool:
    try:
        table.validate(full=True)
    except pa.ArrowInvalid:
        return False
    return True


# --------------------------------------------------------------------------------------
# The reference, and an exact comparison
# --------------------------------------------------------------------------------------


def _construct(model: type[BaseModel], row: dict[str, Any]) -> BaseModel:
    """``model_construct`` all the way down: a struct becomes the nested model, built alike."""
    values: dict[str, Any] = {}
    for name, value in row.items():
        values[name] = _construct_value(model.model_fields[name].annotation, value)
    return model.model_construct(**values)


def _construct_value(annotation: Any, value: Any) -> Any:
    if value is None:
        return None
    nested = _nested_model(annotation)
    if nested is not None and isinstance(value, dict):
        return _construct(nested, cast("dict[str, Any]", value))
    if isinstance(value, list):
        inner = _list_item(annotation)
        return [_construct_value(inner, item) for item in cast("list[Any]", value)]
    return value


def _nested_model(annotation: Any) -> type[BaseModel] | None:
    for candidate in (annotation, *getattr(annotation, "__args__", ())):
        if isinstance(candidate, type) and issubclass(candidate, BaseModel):
            return candidate
    return None


def _list_item(annotation: Any) -> Any:
    for candidate in (annotation, *getattr(annotation, "__args__", ())):
        if getattr(candidate, "__origin__", None) is list:
            return candidate.__args__[0]
    return None


def same(value: Any) -> Any:
    """A comparison key that is exact: type, NaN, -0.0, Decimal exponent and UTC offset count."""
    if isinstance(value, BaseModel):
        return (
            type(value).__name__,
            tuple(sorted(value.model_fields_set)),
            tuple((name, same(getattr(value, name))) for name in type(value).model_fields),
        )
    if isinstance(value, list):
        return ("list", tuple(same(item) for item in cast("list[Any]", value)))
    if isinstance(value, float):
        return ("float", "nan" if math.isnan(value) else value.hex())
    if isinstance(value, dt.datetime):
        return ("datetime", value.isoformat(), value.utcoffset())
    if isinstance(value, Decimal):
        return ("Decimal", value.as_tuple())
    return (type(value).__name__, value)


def _minute_offsets(value: Any) -> Any:
    """
    Apply the documented validated-mode difference for offsets with seconds.

    Validated mode sends an aware timestamp whose zone offset has seconds (historic local mean
    time, e.g. +05:21:10) as UTC, because Pydantic cannot parse it.
    """
    if isinstance(value, dict):
        return {k: _minute_offsets(v) for k, v in cast("dict[str, Any]", value).items()}
    if isinstance(value, list):
        return [_minute_offsets(v) for v in cast("list[Any]", value)]
    if (
        isinstance(value, dt.datetime)
        and (offset := value.utcoffset()) is not None
        and offset.total_seconds() % 60
    ):
        return value.astimezone(dt.UTC)
    return value


SETTINGS = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@SETTINGS
@given(models_and_tables(finite=False))
def test_fast_path_equals_to_pylist_and_model_construct(
    case: tuple[type[BaseModel], pa.Table],
) -> None:
    model, table = case
    expected = [_construct(model, row) for row in table.to_pylist()]
    assert [same(m) for m in model_convert(model, table)] == [same(m) for m in expected]


@SETTINGS
@given(models_and_tables(finite=True))
def test_validated_path_equals_to_pylist_and_model_validate(
    case: tuple[type[BaseModel], pa.Table],
) -> None:
    model, table = case
    expected = [model.model_validate(_minute_offsets(row)) for row in table.to_pylist()]
    actual = model_convert(model, table, validate=True)
    assert [same(m) for m in actual] == [same(m) for m in expected]
