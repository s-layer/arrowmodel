"""
Fast-path construction tests.

The fast path builds each instance directly in Rust from a plan resolved once by
``_construct_plan``, instead of calling the Python-level
``BaseModel.model_construct`` per row. Its contract is that every instance is
indistinguishable from what ``model_construct(**kwargs)`` would have built.

The oracle is the ``model_construct`` path itself: patching ``_construct_plan``
to return ``None`` forces every model (top level and nested) through
``model_construct``, and the two results are compared on full internal state --
type, ``__dict__`` contents *and order*, ``model_fields_set``,
``__pydantic_extra__`` and ``__pydantic_private__``.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any, ClassVar, Self, cast

import pyarrow as pa
import pytest
from pydantic import AliasChoices, AliasPath, BaseModel, ConfigDict, Field, PrivateAttr

import arrowmodel
from arrowmodel import ArrowModelConverter, _construct_plan

if TYPE_CHECKING:
    from collections.abc import Sequence
    from types import FrameType

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class Plain(BaseModel):
    id: int
    name: str


class Reordered(BaseModel):
    """Declared in a different order from the Arrow columns."""

    b: str
    a: int


class WithDefaults(BaseModel):
    id: int
    tags: list[str] = []
    label: str = "none"
    doubled: int = Field(default_factory=lambda data: data["id"] * 2)
    made: list[int] = Field(default_factory=list)


class Aliased(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(alias="userId")
    full_name: str = Field(validation_alias="fullName")
    nick: str = Field(validation_alias="nickName")


class InnerChoices(BaseModel):
    """AliasChoices is only reachable through a Struct (top-level fields reject it)."""

    nick: str = Field(validation_alias=AliasChoices("nickName", "nick"))
    city: str = Field(alias="City")


class OuterChoices(BaseModel):
    id: int
    home: InnerChoices


class AliasCollision(BaseModel):
    """``a``'s alias is ``b``'s name: ``model_construct`` gives ``a`` the ``b`` kwarg."""

    model_config = ConfigDict(populate_by_name=True)

    a: int = Field(alias="b", default=-1)
    b: int = 0


class ExtraAllow(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: int


class WithPrivate(BaseModel):
    id: int
    _cache: dict[str, int] = PrivateAttr(default_factory=dict)
    _flag: bool = PrivateAttr(default=True)


class WithPostInit(BaseModel):
    id: int
    seen: ClassVar[list[int]] = []

    def model_post_init(self, context: Any, /) -> None:
        WithPostInit.seen.append(self.id)


class OverriddenConstruct(BaseModel):
    id: int
    calls: ClassVar[int] = 0

    @classmethod
    def model_construct(cls, _fields_set: set[str] | None = None, **values: Any) -> Self:
        OverriddenConstruct.calls += 1
        return super().model_construct(_fields_set, **values)


class Inner(BaseModel):
    city: str
    zip_code: int = 0
    note: str | None = None


class InnerExtra(BaseModel):
    model_config = ConfigDict(extra="allow")

    city: str


class InnerAliasPath(BaseModel):
    city: str = Field(validation_alias=AliasPath("city"))


class Outer(BaseModel):
    id: int
    home: Inner | None
    homes: list[Inner] | None = None


class OuterExtra(BaseModel):
    id: int
    home: InnerExtra


class OuterAliasPath(BaseModel):
    id: int
    home: InnerAliasPath


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


def _struct(
    rows: list[dict[str, Any] | None], fields: Sequence[tuple[str, pa.DataType]]
) -> pa.StructArray:
    return cast("pa.StructArray", pa.array(rows, type=pa.struct(fields)))


_HOME_FIELDS = [("city", pa.utf8()), ("zip_code", pa.int64()), ("ignored", pa.int64())]

CASES: dict[str, tuple[type[BaseModel], pa.RecordBatch]] = {
    "plain": (Plain, pa.record_batch({"id": [1, 2], "name": ["a", "b"]})),
    "reordered": (Reordered, pa.record_batch({"a": [1, 2], "b": ["x", "y"]})),
    "defaults": (WithDefaults, pa.record_batch({"id": [3, 4]})),
    "defaults_partly_given": (
        WithDefaults,
        pa.record_batch({"label": ["p", "q"], "id": [5, 6]}),
    ),
    "aliases": (
        Aliased,
        pa.record_batch({"fullName": ["Ann", "Bo"], "userId": [1, 2], "nickName": ["an", "b"]}),
    ),
    "nested_alias_choices": (
        OuterChoices,
        pa.record_batch(
            {
                "id": [1, 2],
                "home": _struct(
                    [{"nick": "n", "City": "c"}, {"nick": "m", "City": "d"}],
                    [("nick", pa.utf8()), ("City", pa.utf8())],
                ),
            }
        ),
    ),
    "alias_collision": (AliasCollision, pa.record_batch({"b": [7, 8]})),
    "extra_allow": (ExtraAllow, pa.record_batch({"id": [1, 2], "other": [9, 9]})),
    "private": (WithPrivate, pa.record_batch({"id": [1, 2]})),
    "post_init": (WithPostInit, pa.record_batch({"id": [1, 2]})),
    "nested": (
        Outer,
        pa.record_batch(
            {
                "id": [1, 2, 3],
                # "ignored" is not an Inner field; "note" is absent (default);
                # row 2 is a null struct.
                "home": _struct(
                    [{"city": "NYC", "zip_code": 1, "ignored": 5}, None, {"city": "LA"}],
                    _HOME_FIELDS,
                ),
                "homes": pa.array(
                    [[{"city": "SF", "zip_code": 2}], None, []],
                    type=pa.list_(pa.struct([("city", pa.utf8()), ("zip_code", pa.int64())])),
                ),
            }
        ),
    ),
    "nested_missing_required": (
        Outer,
        # Inner.city is required but absent: model_construct leaves it out.
        pa.record_batch(
            {"id": [1], "home": _struct([{"zip_code": 4}], [("zip_code", pa.int64())])}
        ),
    ),
    "nested_extra_allow": (
        OuterExtra,
        pa.record_batch(
            {
                "id": [1],
                "home": _struct(
                    [{"city": "NYC", "more": 2}], [("city", pa.utf8()), ("more", pa.int64())]
                ),
            }
        ),
    ),
    "nested_alias_path": (
        OuterAliasPath,
        pa.record_batch({"id": [1], "home": _struct([{"city": "NYC"}], [("city", pa.utf8())])}),
    ),
}


def _state(obj: Any) -> Any:
    """Full observable state of a converted value, recursing into models and lists."""
    if isinstance(obj, BaseModel):
        return (
            type(obj),
            [(k, _state(v)) for k, v in obj.__dict__.items()],
            obj.model_fields_set,
            obj.__pydantic_extra__,
            obj.__pydantic_private__,
        )
    if isinstance(obj, list):
        return [_state(v) for v in cast("list[Any]", obj)]
    return obj


def _no_plan(*_args: object) -> None:
    return None


def _via_model_construct(
    monkeypatch: pytest.MonkeyPatch, model: type[BaseModel], data: pa.RecordBatch | pa.Table
) -> list[BaseModel]:
    """Convert with every model forced through the real ``model_construct``."""
    with monkeypatch.context() as m:
        m.setattr(arrowmodel, "_construct_plan", _no_plan)
        return ArrowModelConverter(model).convert(data)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestParityWithModelConstruct:
    """Each instance matches ``model_construct(**kwargs)`` on full internal state."""

    @pytest.mark.parametrize("case", list(CASES))
    def test_record_batch(self, case: str, monkeypatch: pytest.MonkeyPatch) -> None:
        model, batch = CASES[case]
        expected = _via_model_construct(monkeypatch, model, batch)
        actual = ArrowModelConverter(model).convert(batch)
        assert _state(actual) == _state(expected)

    @pytest.mark.parametrize("case", list(CASES))
    def test_table(self, case: str, monkeypatch: pytest.MonkeyPatch) -> None:
        model, batch = CASES[case]
        table = pa.Table.from_batches([batch, batch])
        expected = _via_model_construct(monkeypatch, model, table)
        actual = ArrowModelConverter(model).convert(table)
        assert _state(actual) == _state(expected)

    def test_oracle_is_not_vacuous(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The comparison sees __dict__ order and fields_set, not just field values."""
        model, batch = CASES["reordered"]
        actual = ArrowModelConverter(model).convert(batch)
        assert list(actual[0].__dict__) == ["b", "a"]
        model, batch = CASES["defaults"]
        actual = ArrowModelConverter(model).convert(batch)
        assert actual[0].model_fields_set == {"id"}
        assert actual[0].doubled == 6


class TestDirectConstructionSemantics:
    """Behaviour that the parity oracle alone cannot pin down."""

    def test_mutable_defaults_are_not_shared(self) -> None:
        a, b = ArrowModelConverter(WithDefaults).convert(pa.record_batch({"id": [1, 2]}))
        assert a.tags == [] and a.made == []
        assert a.tags is not b.tags
        assert a.made is not b.made

    def test_fields_set_is_per_instance(self) -> None:
        a, b = ArrowModelConverter(Plain).convert(pa.record_batch({"id": [1, 2], "name": "xy"}))
        assert a.model_fields_set is not b.model_fields_set
        a.model_fields_set.add("extra")
        assert b.model_fields_set == {"id", "name"}

    def test_private_attributes_initialised(self) -> None:
        a, b = ArrowModelConverter(WithPrivate).convert(pa.record_batch({"id": [1, 2]}))
        assert a._flag is True
        assert a._cache == {} and a._cache is not b._cache

    def test_post_init_runs_per_instance(self) -> None:
        WithPostInit.seen.clear()
        ArrowModelConverter(WithPostInit).convert(pa.record_batch({"id": [4, 5]}))
        assert WithPostInit.seen == [4, 5]

    def test_overridden_model_construct_is_called(self) -> None:
        OverriddenConstruct.calls = 0
        results = ArrowModelConverter(OverriddenConstruct).convert(
            pa.record_batch({"id": [1, 2, 3]})
        )
        assert OverriddenConstruct.calls == 3
        assert [r.id for r in results] == [1, 2, 3]

    def test_extra_allow_keeps_unmatched_struct_fields(self) -> None:
        _, batch = CASES["nested_extra_allow"]
        (result,) = ArrowModelConverter(OuterExtra).convert(batch)
        assert result.home.__pydantic_extra__ == {"more": 2}

    def test_extra_allow_extra_dict_is_per_instance(self) -> None:
        a, b = ArrowModelConverter(ExtraAllow).convert(pa.record_batch({"id": [1, 2]}))
        assert a.__pydantic_extra__ == {}
        assert a.__pydantic_extra__ is not b.__pydantic_extra__

    @pytest.mark.parametrize("case", ["plain", "nested"])
    def test_does_not_call_python_model_construct(self, case: str) -> None:
        """
        The fast path builds instances without running ``BaseModel.model_construct``.

        Guards the performance fix: a regression back to a per-row
        ``model_construct`` call would pass every parity test.
        """
        model, batch = CASES[case]
        converter = ArrowModelConverter(model)
        target = BaseModel.model_construct.__func__.__code__
        calls = 0

        def profiler(frame: FrameType, event: str, _arg: Any) -> None:
            nonlocal calls
            if event == "call" and frame.f_code is target:
                calls += 1

        sys.setprofile(profiler)
        try:
            converter.convert(batch)
        finally:
            sys.setprofile(None)
        assert calls == 0

    def test_profiler_detects_model_construct(self) -> None:
        """The spy above is not vacuous: it does see a real model_construct call."""
        target = BaseModel.model_construct.__func__.__code__
        calls = 0

        def profiler(frame: FrameType, event: str, _arg: Any) -> None:
            nonlocal calls
            if event == "call" and frame.f_code is target:
                calls += 1

        sys.setprofile(profiler)
        try:
            Plain.model_construct(id=1, name="a")
        finally:
            sys.setprofile(None)
        assert calls == 1


class TestConstructPlan:
    """``_construct_plan`` resolves keys exactly as ``model_construct`` would."""

    def test_slots_follow_declaration_order(self) -> None:
        plan = _construct_plan(Reordered, ["a", "b"])
        assert plan is not None
        slots, extra_allow, post_init = plan
        assert [(name, index) for name, index, _ in slots] == [("b", 1), ("a", 0)]
        assert (extra_allow, post_init) == (False, False)

    def test_missing_fields(self) -> None:
        plan = _construct_plan(Inner, ["zip_code"])
        assert plan is not None
        slots = plan[0]
        # city is required and absent: omitted. note has a default: index -1.
        assert [(name, index) for name, index, _ in slots] == [("zip_code", 0), ("note", -1)]
        assert slots[1][2] is Inner.model_fields["note"]

    def test_alias_takes_key_before_field_name(self) -> None:
        plan = _construct_plan(AliasCollision, ["b"])
        assert plan is not None
        # a's alias "b" consumes the key; b falls back to its default.
        assert [(name, index) for name, index, _ in plan[0]] == [("a", 0), ("b", -1)]

    def test_alias_choices(self) -> None:
        plan = _construct_plan(InnerChoices, ["City", "nick"])
        assert plan is not None
        assert [(name, index) for name, index, _ in plan[0]] == [("nick", 1), ("city", 0)]

    def test_top_level_keys_are_field_names(self) -> None:
        # The converter keys top-level values by field name; with
        # populate_by_name, model_construct matches them by name.
        plan = _construct_plan(Aliased, ["full_name", "user_id", "nick"])
        assert plan is not None
        assert [(name, index) for name, index, _ in plan[0]] == [
            ("user_id", 1),
            ("full_name", 0),
            ("nick", 2),
        ]

    @pytest.mark.parametrize(
        ("model", "keys"),
        [
            (OverriddenConstruct, ["id"]),
            (InnerAliasPath, ["city"]),
            (InnerExtra, ["city", "more"]),
        ],
    )
    def test_falls_back(self, model: type[BaseModel], keys: list[str]) -> None:
        assert _construct_plan(model, keys) is None

    def test_extra_allow_without_leftovers_is_direct(self) -> None:
        assert _construct_plan(InnerExtra, ["city"]) == ([("city", 0, None)], True, False)

    def test_post_init_without_leftovers_is_direct(self) -> None:
        plan = _construct_plan(WithPrivate, ["id"])
        assert plan is not None
        assert plan[2] is True
        assert _construct_plan(WithPrivate, ["id", "_cache"]) is None

    def test_leftover_keys_ignored_by_default(self) -> None:
        assert _construct_plan(Plain, ["name", "junk", "id"]) == (
            [("id", 2, None), ("name", 0, None)],
            False,
            False,
        )
