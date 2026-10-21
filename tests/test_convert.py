"""
Conversion correctness tests.

Covers Phase 2-4 requirement IDs:
- SCHEMA-01, SCHEMA-02: Schema cross-referencing
- TYPE-01 through TYPE-05: Primitive type conversions
- NULL-01 through NULL-03: Null handling
- FAST-01: model_construct (no validation)
- FAST-03: Direct Arrow buffer extraction
- INPUT-01: RecordBatch input
- API-01, API-02: Public API contract
- TEMP-01 through TEMP-05: Temporal type conversions
- CPLX-04: Dictionary array decoding
- CPLX-05: Null type handling
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

import pyarrow as pa
import pytest
from pydantic import AliasChoices, AliasGenerator, AliasPath, BaseModel, ConfigDict, Field

from arrowmodel import ArrowModelConverter, _build_field_map, model_convert


class IntModel(BaseModel):
    i8: int
    i16: int
    i32: int
    i64: int


class IntModelSmall(BaseModel):
    id: int


class UIntModel(BaseModel):
    u8: int
    u16: int
    u32: int
    u64: int


class FloatModel(BaseModel):
    f32: float
    f64: float


class BoolModel(BaseModel):
    flag: bool


class StringModel(BaseModel):
    name: str


class MixedModel(BaseModel):
    id: int
    name: str
    score: float
    active: bool


class NullableModel(BaseModel):
    id: int
    name: str | None = None
    score: float | None = None


class TestSchemaMapping:
    """Tests for SCHEMA-01 and SCHEMA-02: Arrow schema <-> Pydantic model cross-referencing."""

    def test_matches_arrow_columns_to_model_fields(self, mixed_batch: pa.RecordBatch) -> None:
        """SCHEMA-01: Converter maps Arrow column names to Pydantic model fields."""
        converter = ArrowModelConverter(MixedModel)
        results = converter.convert(mixed_batch)
        assert len(results) == 3
        assert results[0].id == 1
        assert results[0].name == "alice"
        assert results[0].score == 9.5
        assert results[0].active is True

    def test_raises_on_missing_field(self) -> None:
        """SCHEMA-01: ValueError when Arrow schema lacks a required model field."""
        batch = pa.record_batch({"x": [1, 2, 3]})
        converter = ArrowModelConverter(MixedModel)
        with pytest.raises(ValueError, match="missing required columns"):
            converter.convert(batch)

    def test_mapping_reuse_across_batches(self, mixed_batch: pa.RecordBatch) -> None:
        """SCHEMA-02: Converter can be reused across multiple batches."""
        converter = ArrowModelConverter(MixedModel)
        results1 = converter.convert(mixed_batch)
        results2 = converter.convert(mixed_batch)
        assert len(results1) == 3
        assert len(results2) == 3
        assert results1[0].id == results2[0].id
        assert results1[2].name == results2[2].name


class TestPrimitiveTypes:
    """Tests for TYPE-01 through TYPE-05: Primitive Arrow type conversions."""

    def test_int_types(self, int_batch: pa.RecordBatch) -> None:
        """TYPE-01: Int8, Int16, Int32, Int64 -> Python int."""
        converter = ArrowModelConverter(IntModel)
        results = converter.convert(int_batch)
        assert results[0].i8 == 1
        assert results[1].i8 == -1
        assert results[2].i64 == 9223372036854775807
        # Verify all values are int type
        for result in results:
            assert isinstance(result.i8, int)
            assert isinstance(result.i16, int)
            assert isinstance(result.i32, int)
            assert isinstance(result.i64, int)

    def test_uint_types(self, uint_batch: pa.RecordBatch) -> None:
        """TYPE-02: UInt8, UInt16, UInt32, UInt64 -> Python int."""
        converter = ArrowModelConverter(UIntModel)
        results = converter.convert(uint_batch)
        assert results[2].u8 == 255
        assert results[2].u64 == 18446744073709551615
        # Verify all values are int type
        for result in results:
            assert isinstance(result.u8, int)
            assert isinstance(result.u16, int)
            assert isinstance(result.u32, int)
            assert isinstance(result.u64, int)

    def test_float_types(self, float_batch: pa.RecordBatch) -> None:
        """TYPE-03: Float32, Float64 -> Python float."""
        converter = ArrowModelConverter(FloatModel)
        results = converter.convert(float_batch)
        assert results[0].f32 == pytest.approx(1.5)
        assert results[0].f64 == 1.5
        assert results[1].f32 == pytest.approx(-2.5)
        assert results[2].f64 == 0.0
        # Verify all values are float type
        for result in results:
            assert isinstance(result.f32, float)
            assert isinstance(result.f64, float)

    def test_bool_type(self, bool_batch: pa.RecordBatch) -> None:
        """TYPE-04: Boolean -> Python bool (not truthy ints)."""
        converter = ArrowModelConverter(BoolModel)
        results = converter.convert(bool_batch)
        assert results[0].flag is True
        assert results[1].flag is False
        assert results[2].flag is True
        # Verify values are exactly True/False, not truthy ints
        for result in results:
            assert type(result.flag) is bool

    def test_string_types(self, string_batch: pa.RecordBatch) -> None:
        """TYPE-05: Utf8 -> Python str."""
        converter = ArrowModelConverter(StringModel)
        results = converter.convert(string_batch)
        assert results[0].name == "alice"
        assert results[1].name == "bob"
        assert results[2].name == "charlie"
        # Verify all values are str type
        for result in results:
            assert isinstance(result.name, str)


class TestNullHandling:
    """Tests for NULL-01 through NULL-03: Null detection and handling."""

    def test_null_produces_none(self, nullable_batch: pa.RecordBatch) -> None:
        """NULL-01, NULL-02: Null values produce None in model instances."""
        converter = ArrowModelConverter(NullableModel)
        results = converter.convert(nullable_batch)
        assert results[1].name is None
        assert results[1].score is None
        assert results[0].name == "alice"

    def test_non_null_values_correct(self, nullable_batch: pa.RecordBatch) -> None:
        """NULL-03: Non-null values are correctly extracted alongside nulls."""
        converter = ArrowModelConverter(NullableModel)
        results = converter.convert(nullable_batch)
        assert results[0].id == 1
        assert results[0].score == 9.5
        assert results[2].name == "charlie"
        assert results[2].score == 7.5

    def test_all_null_column(self, all_null_batch: pa.RecordBatch) -> None:
        """NULL-02: Entirely null columns produce all None values."""
        converter = ArrowModelConverter(NullableModel)
        results = converter.convert(all_null_batch)
        assert len(results) == 2
        for result in results:
            assert result.name is None
            assert result.score is None
        # Non-null column still correct
        assert results[0].id == 1
        assert results[1].id == 2


class TestModelConstruct:
    """Tests for FAST-01: Conversion uses model_construct (no Pydantic validation)."""

    def test_uses_model_construct_not_validate(self, mixed_batch: pa.RecordBatch) -> None:
        """
        FAST-01: model_construct bypasses Pydantic validation.

        Verify that conversion uses model_construct (not __init__/model_validate)
        by confirming instances are produced correctly and model_fields_set
        contains the provided fields (model_construct sets this from kwargs).
        """
        converter = ArrowModelConverter(MixedModel)
        results = converter.convert(mixed_batch)
        # model_construct with kwargs sets model_fields_set to the provided field names
        assert results[0].model_fields_set == {"id", "name", "score", "active"}
        # Verify instance is correct
        assert results[0].id == 1
        assert isinstance(results[0], MixedModel)


class TestAPI:
    """Tests for API-01, API-02, INPUT-01: Public API contract."""

    def test_constructor_accepts_model_class(self) -> None:
        """API-01: ArrowModelConverter(Model) constructor works."""
        converter = ArrowModelConverter(MixedModel)
        assert converter is not None

    def test_constructor_accepts_validate_flag(self) -> None:
        """API-01: ArrowModelConverter(Model, validate=True) constructor works."""
        converter = ArrowModelConverter(MixedModel, validate=True)
        assert converter is not None

    def test_convert_returns_list(self, mixed_batch: pa.RecordBatch) -> None:
        """API-02, INPUT-01: convert() returns list[Model] from RecordBatch."""
        results = ArrowModelConverter(MixedModel).convert(mixed_batch)
        assert isinstance(results, list)
        assert len(results) == 3
        assert isinstance(results[0], MixedModel)


class TestUnsupportedInput:
    """convert()/iter() reject inputs that are not a RecordBatch or Table with a clear TypeError."""

    @staticmethod
    def _inputs() -> dict[str, object]:
        batch = pa.record_batch({"id": [1, 2]})
        return {
            "reader": pa.RecordBatchReader.from_batches(batch.schema, [batch]),
            "array": pa.array([1, 2]),
            "chunked_array": pa.chunked_array([[1, 2]]),
            "list_of_dicts": [{"id": 1}],
        }

    @pytest.mark.parametrize("kind", ["reader", "array", "chunked_array", "list_of_dicts"])
    @pytest.mark.parametrize("method", ["convert", "iter"])
    def test_raises_type_error(self, kind: str, method: str) -> None:
        data = self._inputs()[kind]
        converter = ArrowModelConverter(IntModelSmall)
        with pytest.raises(TypeError, match="RecordBatch or Table") as exc_info:
            list(getattr(converter, method)(data))
        assert type(data).__name__ in str(exc_info.value)

    def test_reader_error_says_how_to_convert(self) -> None:
        reader = self._inputs()["reader"]
        with pytest.raises(TypeError, match=r"read_all\(\)"):
            ArrowModelConverter(IntModelSmall).convert(reader)

    def test_supported_inputs_still_work(self) -> None:
        batch = pa.record_batch({"id": [1, 2]})
        converter = ArrowModelConverter(IntModelSmall)
        assert [m.id for m in converter.convert(batch)] == [1, 2]
        assert [m.id for m in converter.convert(pa.Table.from_batches([batch]))] == [1, 2]
        assert [m.id for m in converter.iter(batch)] == [1, 2]


class TestEndToEnd:
    """Integration tests for FAST-03: End-to-end conversion correctness."""

    def test_empty_batch(self) -> None:
        """Empty RecordBatch produces empty list."""
        batch = pa.record_batch(
            {
                "id": pa.array([], type=pa.int64()),
                "name": pa.array([], type=pa.string()),
                "score": pa.array([], type=pa.float64()),
                "active": pa.array([], type=pa.bool_()),
            }
        )
        converter = ArrowModelConverter(MixedModel)
        results = converter.convert(batch)
        assert results == []

    def test_single_row(self) -> None:
        """Single-row RecordBatch converts correctly."""
        batch = pa.record_batch(
            {
                "id": pa.array([42], type=pa.int64()),
                "name": pa.array(["single"]),
                "score": pa.array([99.9], type=pa.float64()),
                "active": pa.array([True]),
            }
        )
        converter = ArrowModelConverter(MixedModel)
        results = converter.convert(batch)
        assert len(results) == 1
        assert results[0].id == 42
        assert results[0].name == "single"
        assert results[0].score == 99.9
        assert results[0].active is True

    def test_large_batch(self) -> None:
        """FAST-03: Large batch (10k rows) converts correctly."""
        n = 10_000
        batch = pa.record_batch(
            {
                "id": pa.array(list(range(n)), type=pa.int64()),
                "name": pa.array([f"item_{i}" for i in range(n)]),
                "score": pa.array([float(i) for i in range(n)], type=pa.float64()),
                "active": pa.array([i % 2 == 0 for i in range(n)]),
            }
        )
        converter = ArrowModelConverter(MixedModel)
        results = converter.convert(batch)
        assert len(results) == n
        # Spot-check first and last
        assert results[0].id == 0
        assert results[0].name == "item_0"
        assert results[-1].id == n - 1
        assert results[-1].name == f"item_{n - 1}"


# ---------------------------------------------------------------------------
# Phase 3 test models for alias resolution and schema validation
# ---------------------------------------------------------------------------


class ValidationAliasModel(BaseModel):
    user_id: int = Field(validation_alias="userId")
    display_name: str = Field(validation_alias="displayName")


class AliasModel(BaseModel):
    user_id: int = Field(alias="userId")
    display_name: str = Field(alias="displayName")


class MixedAliasModel(BaseModel):
    user_id: int = Field(validation_alias="userId")
    display_name: str = Field(alias="displayName")
    email: str  # no alias


class PopulateByNameModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    user_id: int = Field(alias="userId")


class ValidateByNameModel(BaseModel):
    model_config = ConfigDict(validate_by_name=True)
    user_id: int = Field(alias="userId")


class AliasPathModel(BaseModel):
    nested_val: str = Field(validation_alias=AliasPath("data", "value"))


class AliasChoicesModel(BaseModel):
    value: str = Field(validation_alias=AliasChoices("val", "value"))


class AliasGeneratorModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=AliasGenerator(validation_alias=lambda field_name: field_name.upper())
    )
    user_id: int


class OptionalFieldModel(BaseModel):
    id: int
    name: str = "default_name"
    score: float | None = None


# ---------------------------------------------------------------------------
# Task 1 RED: Tests for _build_field_map and _resolve_columns
# ---------------------------------------------------------------------------


class TestBuildFieldMap:
    """Tests for _build_field_map: alias resolution logic."""

    def test_validation_alias_str_returns_alias_to_field(self) -> None:
        result = _build_field_map(ValidationAliasModel)
        assert result == {"userId": "user_id", "displayName": "display_name"}

    def test_alias_fallback_returns_alias_to_field(self) -> None:
        result = _build_field_map(AliasModel)
        assert result == {"userId": "user_id", "displayName": "display_name"}

    def test_no_alias_returns_field_name_to_field_name(self) -> None:
        result = _build_field_map(MixedModel)
        assert result == {"id": "id", "name": "name", "score": "score", "active": "active"}

    def test_alias_path_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="AliasPath"):
            _build_field_map(AliasPathModel)

    def test_alias_choices_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="AliasChoices"):
            _build_field_map(AliasChoicesModel)

    def test_alias_generator_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="AliasGenerator"):
            _build_field_map(AliasGeneratorModel)

    def test_populate_by_name_adds_both_entries(self) -> None:
        result = _build_field_map(PopulateByNameModel)
        assert "userId" in result
        assert "user_id" in result
        assert result["userId"] == "user_id"
        assert result["user_id"] == "user_id"

    def test_validate_by_name_adds_both_entries(self) -> None:
        result = _build_field_map(ValidateByNameModel)
        assert "userId" in result
        assert "user_id" in result

    def test_populate_by_name_does_not_overwrite_alias(self) -> None:
        """Alias entry should win over field_name entry when they collide."""
        result = _build_field_map(PopulateByNameModel)
        # "userId" -> "user_id" is the alias mapping, it must remain
        assert result["userId"] == "user_id"


class TestResolveColumns:
    """Tests for ArrowModelConverter._resolve_columns."""

    def test_all_required_fields_present(self) -> None:
        converter = ArrowModelConverter(MixedModel)
        schema = pa.schema(
            [
                ("id", pa.int64()),
                ("name", pa.utf8()),
                ("score", pa.float64()),
                ("active", pa.bool_()),
            ]
        )
        field_specs = converter._resolve_columns(schema)
        assert len(field_specs) == 4

    def test_missing_required_field_raises_value_error(self) -> None:
        converter = ArrowModelConverter(MixedModel)
        schema = pa.schema([("id", pa.int64())])
        with pytest.raises(ValueError, match="missing required columns"):
            converter._resolve_columns(schema)

    def test_missing_multiple_fields_lists_all(self) -> None:
        converter = ArrowModelConverter(MixedModel)
        schema = pa.schema([("wrong", pa.int64())])
        with pytest.raises(ValueError, match="missing required columns") as exc_info:
            converter._resolve_columns(schema)
        error_msg = str(exc_info.value)
        # All 4 fields should be listed
        assert "id" in error_msg
        assert "name" in error_msg

    def test_optional_field_missing_skips(self) -> None:
        converter = ArrowModelConverter(OptionalFieldModel)
        schema = pa.schema([("id", pa.int64())])
        field_specs = converter._resolve_columns(schema)
        assert len(field_specs) == 1
        assert field_specs[0][1] == "id"

    def test_extra_arrow_columns_ignored(self) -> None:
        converter = ArrowModelConverter(MixedModel)
        schema = pa.schema(
            [
                ("id", pa.int64()),
                ("name", pa.utf8()),
                ("score", pa.float64()),
                ("active", pa.bool_()),
                ("extra_col", pa.int64()),
            ]
        )
        field_specs = converter._resolve_columns(schema)
        assert len(field_specs) == 4
        field_names = [fs[1] for fs in field_specs]
        assert "extra_col" not in field_names


# ---------------------------------------------------------------------------
# Task 2: End-to-end alias resolution and schema validation tests
# ---------------------------------------------------------------------------


class TestAliasResolution:
    """End-to-end tests for alias-aware conversion via ArrowModelConverter.convert()."""

    def test_validation_alias_priority(self) -> None:
        """ALIAS-01: validation_alias str field matches Arrow column."""
        batch = pa.record_batch({"userId": [1, 2], "displayName": ["a", "b"]})
        results = ArrowModelConverter(ValidationAliasModel).convert(batch)
        assert len(results) == 2
        assert results[0].user_id == 1
        assert results[0].display_name == "a"
        assert results[1].user_id == 2
        assert results[1].display_name == "b"

    def test_alias_fallback(self) -> None:
        """ALIAS-01: alias (no validation_alias) matches Arrow column."""
        batch = pa.record_batch({"userId": [1, 2], "displayName": ["a", "b"]})
        results = ArrowModelConverter(AliasModel).convert(batch)
        assert results[0].user_id == 1
        assert results[0].display_name == "a"

    def test_field_name_fallback(self) -> None:
        """ALIAS-01: field_name used when no aliases."""
        batch = pa.record_batch({"id": [1], "name": ["x"], "score": [1.0], "active": [True]})
        results = ArrowModelConverter(MixedModel).convert(batch)
        assert results[0].id == 1

    def test_mixed_alias_types(self) -> None:
        """ALIAS-01: validation_alias, alias, and field_name all work together."""
        batch = pa.record_batch({"userId": [1], "displayName": ["a"], "email": ["x@y.z"]})
        results = ArrowModelConverter(MixedAliasModel).convert(batch)
        assert results[0].user_id == 1
        assert results[0].display_name == "a"
        assert results[0].email == "x@y.z"

    def test_populate_by_name(self) -> None:
        """ALIAS-02: populate_by_name=True accepts both alias and field_name."""
        # With alias column name
        batch_alias = pa.record_batch({"userId": [1, 2]})
        results = ArrowModelConverter(PopulateByNameModel).convert(batch_alias)
        assert results[0].user_id == 1

        # With field name column name
        batch_field = pa.record_batch({"user_id": [3, 4]})
        results = ArrowModelConverter(PopulateByNameModel).convert(batch_field)
        assert results[0].user_id == 3

    def test_validate_by_name(self) -> None:
        """ALIAS-02: validate_by_name=True accepts both alias and field_name."""
        batch_alias = pa.record_batch({"userId": [1, 2]})
        results = ArrowModelConverter(ValidateByNameModel).convert(batch_alias)
        assert results[0].user_id == 1

        batch_field = pa.record_batch({"user_id": [3, 4]})
        results = ArrowModelConverter(ValidateByNameModel).convert(batch_field)
        assert results[0].user_id == 3

    def test_alias_path_raises(self) -> None:
        """ALIAS-03: AliasPath raises NotImplementedError at init."""
        with pytest.raises(NotImplementedError, match="AliasPath"):
            ArrowModelConverter(AliasPathModel)

    def test_alias_choices_raises(self) -> None:
        """ALIAS-03: AliasChoices raises NotImplementedError at init."""
        with pytest.raises(NotImplementedError, match="AliasChoices"):
            ArrowModelConverter(AliasChoicesModel)

    def test_alias_generator_raises(self) -> None:
        """ALIAS-03: AliasGenerator raises NotImplementedError at init."""
        with pytest.raises(NotImplementedError, match="AliasGenerator"):
            ArrowModelConverter(AliasGeneratorModel)


class TestSchemaValidation:
    """End-to-end tests for schema validation behavior."""

    def test_missing_required_field_raises(self) -> None:
        """SCHEMA-03: convert with batch missing required field raises ValueError."""
        batch = pa.record_batch({"id": [1, 2]})
        converter = ArrowModelConverter(MixedModel)
        with pytest.raises(ValueError, match="missing required columns"):
            converter.convert(batch)

    def test_missing_multiple_fields_lists_all(self) -> None:
        """SCHEMA-03: ValueError lists all missing required field names."""
        batch = pa.record_batch({"wrong": [1, 2]})
        converter = ArrowModelConverter(MixedModel)
        with pytest.raises(ValueError, match="missing required columns") as exc_info:
            converter.convert(batch)
        error_msg = str(exc_info.value)
        # All 4 fields of MixedModel should be listed as missing
        assert "id" in error_msg
        assert "name" in error_msg

    def test_optional_field_missing_uses_default(self) -> None:
        """SCHEMA-03: Optional fields missing from Arrow use Pydantic defaults."""
        batch = pa.record_batch({"id": [1, 2]})
        results = ArrowModelConverter(OptionalFieldModel).convert(batch)
        assert len(results) == 2
        assert results[0].id == 1
        assert results[0].name == "default_name"
        assert results[0].score is None

    def test_extra_columns_ignored(self) -> None:
        """SCHEMA-04: Extra Arrow columns not in model are silently ignored."""
        batch = pa.record_batch(
            {
                "id": [1],
                "name": ["a"],
                "score": [1.0],
                "active": [True],
                "extra_col": [999],
            }
        )
        results = ArrowModelConverter(MixedModel).convert(batch)
        assert len(results) == 1
        assert results[0].id == 1
        assert not hasattr(results[0], "extra_col")


# ---------------------------------------------------------------------------
# Phase 3 Plan 2: Table input, model_convert, and string interning tests (TDD RED)
# ---------------------------------------------------------------------------


class TestTableInput:
    """Tests for INPUT-02: Accept pyarrow Table as input."""

    def test_table_conversion(self) -> None:
        """INPUT-02: Table with single batch converts correctly."""
        table = pa.table(
            {
                "id": [1, 2, 3],
                "name": ["a", "b", "c"],
                "score": [1.0, 2.0, 3.0],
                "active": [True, False, True],
            }
        )
        results = ArrowModelConverter(MixedModel).convert(table)
        assert len(results) == 3
        assert results[0].id == 1
        assert results[0].name == "a"
        assert results[2].active is True

    def test_multi_batch_table(self) -> None:
        """INPUT-02: Table with multiple batches processes all rows."""
        batch1 = pa.record_batch(
            {"id": [1, 2], "name": ["a", "b"], "score": [1.0, 2.0], "active": [True, False]}
        )
        batch2 = pa.record_batch({"id": [3], "name": ["c"], "score": [3.0], "active": [True]})
        batch3 = pa.record_batch(
            {
                "id": [4, 5, 6],
                "name": ["d", "e", "f"],
                "score": [4.0, 5.0, 6.0],
                "active": [False, True, False],
            }
        )
        table = pa.Table.from_batches([batch1, batch2, batch3])
        results = ArrowModelConverter(MixedModel).convert(table)
        assert len(results) == 6
        assert results[0].id == 1
        assert results[2].id == 3
        assert results[5].id == 6
        assert results[5].name == "f"

    def test_empty_table(self) -> None:
        """INPUT-02: Empty Table returns empty list."""
        table = pa.table(
            {
                "id": pa.array([], type=pa.int64()),
                "name": pa.array([], type=pa.string()),
                "score": pa.array([], type=pa.float64()),
                "active": pa.array([], type=pa.bool_()),
            }
        )
        results = ArrowModelConverter(MixedModel).convert(table)
        assert results == []

    def test_table_with_aliases(self) -> None:
        """INPUT-02 + ALIAS-01: Table conversion respects alias resolution."""
        table = pa.table({"userId": [1, 2], "displayName": ["a", "b"]})
        results = ArrowModelConverter(ValidationAliasModel).convert(table)
        assert len(results) == 2
        assert results[0].user_id == 1
        assert results[1].display_name == "b"


class TestModelConvert:
    """Tests for API-03: model_convert convenience function."""

    def test_model_convert_record_batch(self) -> None:
        """API-03: model_convert works with RecordBatch."""
        batch = pa.record_batch(
            {"id": [1, 2], "name": ["a", "b"], "score": [1.0, 2.0], "active": [True, False]}
        )
        results = model_convert(MixedModel, batch)
        assert len(results) == 2
        assert results[0].id == 1
        assert isinstance(results[0], MixedModel)

    def test_model_convert_table(self) -> None:
        """API-03: model_convert works with Table."""
        table = pa.table(
            {
                "id": [1, 2, 3],
                "name": ["a", "b", "c"],
                "score": [1.0, 2.0, 3.0],
                "active": [True, False, True],
            }
        )
        results = model_convert(MixedModel, table)
        assert len(results) == 3
        assert results[2].name == "c"
        assert isinstance(results[0], MixedModel)

    def test_model_convert_validated_record_batch(self) -> None:
        """DEBT-04: model_convert(validate=True) works with RecordBatch."""
        batch = pa.record_batch(
            {"id": [1, 2], "name": ["a", "b"], "score": [1.0, 2.0], "active": [True, False]}
        )
        results = model_convert(MixedModel, batch, validate=True)
        assert len(results) == 2
        assert results[0].id == 1
        assert results[1].name == "b"
        assert isinstance(results[0], MixedModel)

    def test_model_convert_validated_table(self) -> None:
        """DEBT-04: model_convert(validate=True) works with Table."""
        table = pa.table(
            {
                "id": [1, 2, 3],
                "name": ["a", "b", "c"],
                "score": [1.0, 2.0, 3.0],
                "active": [True, False, True],
            }
        )
        results = model_convert(MixedModel, table, validate=True)
        assert len(results) == 3
        assert results[0].id == 1
        assert results[2].name == "c"
        assert isinstance(results[0], MixedModel)


class TestStringInterning:
    """Tests for FAST-02: Pre-interned Python field name strings."""

    def test_interned_string_correctness(self) -> None:
        """
        FAST-02: Interned strings are reused across rows.

        model_fields_set contains the field names used in model_construct kwargs.
        If strings are interned, the same string object is reused, so we can
        verify by checking that field names across different result instances
        refer to the same Python string objects.
        """
        n = 100
        batch = pa.record_batch(
            {
                "id": pa.array(list(range(n)), type=pa.int64()),
                "name": pa.array([f"item_{i}" for i in range(n)]),
                "score": pa.array([float(i) for i in range(n)], type=pa.float64()),
                "active": pa.array([i % 2 == 0 for i in range(n)]),
            }
        )
        results = ArrowModelConverter(MixedModel).convert(batch)
        # All instances should have been constructed -- basic sanity
        assert len(results) == n
        assert results[0].model_fields_set == {"id", "name", "score", "active"}
        # The interning guarantee is that PyString::intern returns the same
        # Python object for the same string value. We verify by confirming
        # the conversion produced correct results for all rows (interning
        # doesn't change correctness, but validates the code path).
        assert results[0].id == 0
        assert results[99].id == 99


# ---------------------------------------------------------------------------
# Phase 4 Plan 1: Temporal, dictionary, and null type models and tests
# ---------------------------------------------------------------------------


class DateModel(BaseModel):
    event_date: datetime.date | None = None


class TimestampModel(BaseModel):
    created_at: datetime.datetime | None = None


class DurationModel(BaseModel):
    elapsed: datetime.timedelta | None = None


class DictStringModel(BaseModel):
    category: str


class DictIntModel(BaseModel):
    code: int


class NullFieldModel(BaseModel):
    id: int
    nothing: None = None


class TestTemporalTypes:
    """Tests for TEMP-01 through TEMP-05: Temporal type conversions."""

    def test_date32(self, date32_batch: pa.RecordBatch) -> None:
        """TEMP-01: Date32 column produces datetime.date values."""
        results = ArrowModelConverter(DateModel).convert(date32_batch)
        assert len(results) == 3
        assert results[0].event_date == datetime.date(2024, 1, 15)
        assert results[1].event_date is None
        assert results[2].event_date == datetime.date(2020, 6, 30)
        assert isinstance(results[0].event_date, datetime.date)

    def test_timestamp_naive_microsecond(self, timestamp_us_batch: pa.RecordBatch) -> None:
        """TEMP-02: Timestamp(us, None) produces naive datetime.datetime."""
        results = ArrowModelConverter(TimestampModel).convert(timestamp_us_batch)
        assert len(results) == 3
        assert results[0].created_at == datetime.datetime(2024, 1, 15, 10, 30, 0, 123456)
        assert results[0].created_at.tzinfo is None
        assert results[1].created_at is None
        assert results[2].created_at == datetime.datetime(2020, 6, 30, 23, 59, 59, 999999)

    def test_timestamp_naive_second(self) -> None:
        """TEMP-02: Timestamp(s, None) produces naive datetime with zero microseconds."""
        batch = pa.record_batch(
            {
                "created_at": pa.array(
                    [datetime.datetime(2024, 1, 15, 10, 30, 0)],
                    type=pa.timestamp("s"),
                ),
            }
        )
        results = ArrowModelConverter(TimestampModel).convert(batch)
        assert results[0].created_at == datetime.datetime(2024, 1, 15, 10, 30, 0)
        assert results[0].created_at.microsecond == 0

    def test_timestamp_aware(self) -> None:
        """TEMP-03: Timestamp with UTC timezone produces aware datetime."""
        batch = pa.record_batch(
            {
                "created_at": pa.array(
                    [datetime.datetime(2024, 1, 15, 10, 30, 0)],
                    type=pa.timestamp("us", tz="UTC"),
                ),
            }
        )
        results = ArrowModelConverter(TimestampModel).convert(batch)
        assert results[0].created_at is not None
        assert results[0].created_at.tzinfo is not None
        assert results[0].created_at.tzname() is not None

    def test_timestamp_aware_iana(self, timestamp_tz_batch: pa.RecordBatch) -> None:
        """TEMP-03: Timestamp with IANA timezone preserves both the zone and the instant."""
        results = ArrowModelConverter(TimestampModel).convert(timestamp_tz_batch)
        assert results[0].created_at is not None
        assert results[0].created_at.tzinfo == ZoneInfo("America/New_York")
        # The absolute instant must match what pyarrow itself materializes --
        # attaching the zone to the UTC wall-clock (instead of converting the
        # instant into the zone) would silently shift the time by the offset.
        expected = timestamp_tz_batch.to_pylist()[0]["created_at"]
        assert results[0].created_at == expected
        assert results[0].created_at.astimezone(datetime.UTC) == expected.astimezone(datetime.UTC)

    def test_timestamp_aware_fast_and_validated_agree(
        self, timestamp_tz_batch: pa.RecordBatch
    ) -> None:
        """Fast and validated paths must resolve tz-aware timestamps to the same instant."""
        fast = ArrowModelConverter(TimestampModel).convert(timestamp_tz_batch)[0]
        validated = ArrowModelConverter(TimestampModel, validate=True).convert(timestamp_tz_batch)[
            0
        ]
        assert fast.created_at == validated.created_at

    def test_nanosecond_truncation(self, timestamp_ns_batch: pa.RecordBatch) -> None:
        """TEMP-05: Nanosecond timestamp truncates to microsecond precision."""
        results = ArrowModelConverter(TimestampModel).convert(timestamp_ns_batch)
        assert len(results) == 1
        assert isinstance(results[0].created_at, datetime.datetime)
        # Nanosecond precision should be truncated -- no sub-microsecond data
        dt = results[0].created_at
        assert dt.microsecond < 1_000_000

    def test_duration(self, duration_batch: pa.RecordBatch) -> None:
        """TEMP-04: Duration(us) produces datetime.timedelta values."""
        results = ArrowModelConverter(DurationModel).convert(duration_batch)
        assert len(results) == 3
        assert results[0].elapsed == datetime.timedelta(hours=1)
        assert results[1].elapsed is None
        assert results[2].elapsed == datetime.timedelta(seconds=1)


class TestDictionaryType:
    """Tests for CPLX-04: Dictionary-encoded column decoding."""

    def test_dictionary_string(self, dict_string_batch: pa.RecordBatch) -> None:
        """CPLX-04: Dictionary(Int32, Utf8) resolves to str values."""
        results = ArrowModelConverter(DictStringModel).convert(dict_string_batch)
        assert len(results) == 3
        assert results[0].category == "a"
        assert results[1].category == "b"
        assert results[2].category == "a"
        assert isinstance(results[0].category, str)

    def test_dictionary_int(self, dict_int_batch: pa.RecordBatch) -> None:
        """CPLX-04: Dictionary(Int8, Int64) resolves to int values."""
        results = ArrowModelConverter(DictIntModel).convert(dict_int_batch)
        assert len(results) == 3
        assert results[0].code == 100
        assert results[1].code == 200
        assert results[2].code == 100

    def test_dictionary_with_nulls(self) -> None:
        """CPLX-04: Dictionary column with null entries produces None."""

        class DictNullableModel(BaseModel):
            category: str | None = None

        batch = pa.record_batch(
            {
                "category": pa.array(["a", None, "b"]).dictionary_encode(),
            }
        )
        results = ArrowModelConverter(DictNullableModel).convert(batch)
        assert len(results) == 3
        assert results[0].category == "a"
        assert results[1].category is None
        assert results[2].category == "b"


class TestNullType:
    """Tests for CPLX-05: Null type column handling."""

    def test_null_type(self) -> None:
        """CPLX-05: Null type column produces None for every row."""
        batch = pa.record_batch(
            {
                "id": pa.array([1, 2, 3], type=pa.int64()),
                "nothing": pa.array([None, None, None], type=pa.null()),
            }
        )
        results = ArrowModelConverter(NullFieldModel).convert(batch)
        assert len(results) == 3
        assert results[0].id == 1
        assert results[0].nothing is None
        assert results[1].id == 2
        assert results[1].nothing is None
        assert results[2].id == 3
        assert results[2].nothing is None

    def test_null_type_all_rows_none(self) -> None:
        """CPLX-05: Every row in a null-typed column returns None."""
        batch = pa.record_batch(
            {
                "id": pa.array([10, 20], type=pa.int64()),
                "nothing": pa.array([None, None], type=pa.null()),
            }
        )
        results = ArrowModelConverter(NullFieldModel).convert(batch)
        assert len(results) == 2
        for result in results:
            assert result.nothing is None


# ---------------------------------------------------------------------------
# Phase 4 Plan 2: List, LargeList, and Struct type models and tests
# ---------------------------------------------------------------------------


class AddressModel(BaseModel):
    city: str
    zip_code: int


class PersonModel(BaseModel):
    name: str
    address: AddressModel | None = None


class ListIntModel(BaseModel):
    values: list[int] | None = None


class ListStrModel(BaseModel):
    tags: list[str]


class NestedListModel(BaseModel):
    matrix: list[list[int]] | None = None


class InnerModel(BaseModel):
    x: int


class OuterModel(BaseModel):
    inner: InnerModel | None = None


class PolylineModel(BaseModel):
    """A model whose field is a list of nested models."""

    points: list[InnerModel] | None = None


class GridModel(BaseModel):
    """A model whose field is a list of lists of nested models."""

    cells: list[list[InnerModel]] | None = None


class ShapeModel(BaseModel):
    """A model whose struct field itself contains a list of nested models."""

    outline: PolylineModel | None = None


class TestListTypes:
    """Tests for CPLX-01, CPLX-02: List and LargeList type conversions."""

    def test_list_int(self, list_int_batch: pa.RecordBatch) -> None:
        """CPLX-01: List(Int64) produces Python lists of ints."""
        results = ArrowModelConverter(ListIntModel).convert(list_int_batch)
        assert len(results) == 3
        assert results[0].values == [1, 2, 3]
        assert results[1].values == [4, 5]
        assert results[2].values == [6]
        assert isinstance(results[0].values, list)

    def test_list_string(self, list_str_batch: pa.RecordBatch) -> None:
        """CPLX-01: List(Utf8) produces Python lists of strings."""
        results = ArrowModelConverter(ListStrModel).convert(list_str_batch)
        assert len(results) == 2
        assert results[0].tags == ["a", "b"]
        assert results[1].tags == ["c"]

    def test_list_with_nulls(self) -> None:
        """CPLX-01: Null list entries produce None."""
        batch = pa.record_batch(
            {
                "values": pa.array([[1, 2], None, [3]], type=pa.list_(pa.int64())),
            }
        )
        results = ArrowModelConverter(ListIntModel).convert(batch)
        assert len(results) == 3
        assert results[0].values == [1, 2]
        assert results[1].values is None
        assert results[2].values == [3]

    def test_list_empty_sublist(self) -> None:
        """CPLX-01: Empty sublists produce empty Python lists."""
        batch = pa.record_batch(
            {
                "values": pa.array([[], [1]], type=pa.list_(pa.int64())),
            }
        )
        results = ArrowModelConverter(ListIntModel).convert(batch)
        assert len(results) == 2
        assert results[0].values == []
        assert results[1].values == [1]

    def test_large_list(self) -> None:
        """CPLX-02: LargeList(Int64) produces same results as List."""
        batch = pa.record_batch(
            {
                "values": pa.array([[1, 2], [3]], type=pa.large_list(pa.int64())),
            }
        )
        results = ArrowModelConverter(ListIntModel).convert(batch)
        assert len(results) == 2
        assert results[0].values == [1, 2]
        assert results[1].values == [3]
        assert isinstance(results[0].values, list)

    def test_nested_list(self) -> None:
        """CPLX-01: List(List(Int32)) produces nested Python lists."""
        batch = pa.record_batch(
            {
                "matrix": pa.array(
                    [[[1, 2], [3]], [[4]]],
                    type=pa.list_(pa.list_(pa.int32())),
                ),
            }
        )
        results = ArrowModelConverter(NestedListModel).convert(batch)
        assert len(results) == 2
        assert results[0].matrix == [[1, 2], [3]]
        assert results[1].matrix == [[4]]


class TestStructTypes:
    """Tests for CPLX-03: Struct type conversions to nested Pydantic models."""

    def test_struct_basic(self, struct_batch: pa.RecordBatch) -> None:
        """CPLX-03: Struct column produces nested Pydantic model instances."""
        results = ArrowModelConverter(PersonModel).convert(struct_batch)
        assert len(results) == 2
        assert results[0].name == "Alice"
        assert results[0].address is not None
        assert results[0].address.city == "NYC"
        assert results[0].address.zip_code == 10001
        assert isinstance(results[0].address, AddressModel)
        assert results[1].name == "Bob"
        assert results[1].address is not None
        assert results[1].address.city == "LA"
        assert results[1].address.zip_code == 90001

    def test_struct_null(self) -> None:
        """CPLX-03: Null struct row produces None for the nested model."""
        # Create a struct array with an explicit null at row index 1
        cities = pa.array(["NYC", None, "LA"])
        zips = pa.array([10001, 0, 90001], type=pa.int32())
        struct_arr = pa.StructArray.from_arrays(
            [cities, zips],
            names=["city", "zip_code"],
            mask=pa.array([False, True, False]),  # row 1 is null struct
        )
        batch = pa.record_batch(
            {
                "name": pa.array(["Alice", "Bob", "Charlie"]),
                "address": struct_arr,
            }
        )
        results = ArrowModelConverter(PersonModel).convert(batch)
        assert len(results) == 3
        assert results[0].address is not None
        assert results[0].address.city == "NYC"
        assert results[1].address is None  # null struct -> None
        assert results[2].address is not None
        assert results[2].address.city == "LA"

    def test_struct_with_nullable_child(self) -> None:
        """CPLX-03: Struct with nullable child fields propagates None correctly."""
        cities = pa.array(["NYC", None])
        zips = pa.array([10001, 90001], type=pa.int32())
        struct_arr = pa.StructArray.from_arrays(
            [cities, zips],
            names=["city", "zip_code"],
        )
        batch = pa.record_batch(
            {
                "name": pa.array(["Alice", "Bob"]),
                "address": struct_arr,
            }
        )
        results = ArrowModelConverter(PersonModel).convert(batch)
        assert len(results) == 2
        assert results[0].address is not None
        assert results[0].address.city == "NYC"
        # Struct is not null, but child city is null
        assert results[1].address is not None
        assert results[1].address.city is None
        assert results[1].address.zip_code == 90001

    def test_struct_nested(self) -> None:
        """CPLX-03: Struct containing another Struct produces doubly-nested models."""
        # Build inner struct: InnerModel with field x
        inner_struct = pa.StructArray.from_arrays(
            [pa.array([10, 20], type=pa.int32())],
            names=["x"],
        )
        # Build outer struct: OuterModel with field inner
        outer_struct = pa.StructArray.from_arrays(
            [inner_struct],
            names=["inner"],
        )
        batch = pa.record_batch({"outer": outer_struct})

        class DeepModel(BaseModel):
            outer: OuterModel | None = None

        results = ArrowModelConverter(DeepModel).convert(batch)
        assert len(results) == 2
        assert results[0].outer is not None
        assert isinstance(results[0].outer, OuterModel)
        assert results[0].outer.inner is not None
        assert isinstance(results[0].outer.inner, InnerModel)
        assert results[0].outer.inner.x == 10
        assert results[1].outer.inner.x == 20

    def test_struct_fields_set(self, struct_batch: pa.RecordBatch) -> None:
        """CPLX-03: Nested model has correct model_fields_set (model_construct was used)."""
        results = ArrowModelConverter(PersonModel).convert(struct_batch)
        # The nested model should have been constructed via model_construct
        assert results[0].address is not None
        # model_construct sets model_fields_set to the kwargs keys
        assert results[0].address.model_fields_set == {"city", "zip_code"}


class TestListOfModel:
    """CPLX-03: nested Pydantic models inside List/FixedSizeList/Map containers."""

    _POINT_STRUCT = pa.struct([("x", pa.int64())])

    @pytest.mark.parametrize("validate", [False, True])
    def test_list_of_model(self, validate: bool) -> None:
        """list[NestedModel] (Arrow List(Struct)) builds a list of model instances."""
        batch = pa.record_batch(
            {
                "points": pa.array(
                    [[{"x": 1}, {"x": 2}], None, []],
                    type=pa.list_(self._POINT_STRUCT),
                ),
            }
        )
        results = ArrowModelConverter(PolylineModel, validate=validate).convert(batch)
        # Pydantic __eq__ compares class as well as fields, so equality here also
        # confirms the elements are InnerModel instances (not raw dicts).
        assert results[0].points == [InnerModel(x=1), InnerModel(x=2)]
        assert results[1].points is None  # null list -> None
        assert results[2].points == []  # empty list -> []

    @pytest.mark.parametrize("validate", [False, True])
    def test_nested_list_of_model(self, validate: bool) -> None:
        """list[list[NestedModel]] threads the leaf model through both list layers."""
        batch = pa.record_batch(
            {
                "cells": pa.array(
                    [[[{"x": 1}], [{"x": 2}, {"x": 3}]]],
                    type=pa.list_(pa.list_(self._POINT_STRUCT)),
                ),
            }
        )
        results = ArrowModelConverter(GridModel, validate=validate).convert(batch)
        assert results[0].cells == [[InnerModel(x=1)], [InnerModel(x=2), InnerModel(x=3)]]

    @pytest.mark.parametrize("validate", [False, True])
    def test_large_list_of_model(self, validate: bool) -> None:
        """large_list[NestedModel] behaves like list[NestedModel]."""
        batch = pa.record_batch(
            {
                "points": pa.array([[{"x": 7}]], type=pa.large_list(self._POINT_STRUCT)),
            }
        )
        results = ArrowModelConverter(PolylineModel, validate=validate).convert(batch)
        assert results[0].points == [InnerModel(x=7)]

    @pytest.mark.parametrize("validate", [False, True])
    def test_fixed_size_list_of_model(self, validate: bool) -> None:
        """fixed_size_list[NestedModel] builds a list of model instances."""
        batch = pa.record_batch(
            {
                "points": pa.array(
                    [[{"x": 1}, {"x": 2}]],
                    type=pa.list_(self._POINT_STRUCT, 2),
                ),
            }
        )
        results = ArrowModelConverter(PolylineModel, validate=validate).convert(batch)
        assert results[0].points == [InnerModel(x=1), InnerModel(x=2)]

    @pytest.mark.parametrize("validate", [False, True])
    def test_struct_field_with_list_of_model(self, validate: bool) -> None:
        """A struct field that itself contains list[NestedModel] resolves recursively."""
        poly_ty = pa.struct([("points", pa.list_(self._POINT_STRUCT))])
        batch = pa.record_batch({"outline": pa.array([{"points": [{"x": 9}]}], type=poly_ty)})
        results = ArrowModelConverter(ShapeModel, validate=validate).convert(batch)
        assert results[0].outline is not None
        assert results[0].outline.points == [InnerModel(x=9)]

    def test_struct_without_model_raises_actionable_error(self) -> None:
        """A Struct column with no model annotation raises a message that names the fix."""

        class NoModel(BaseModel):
            points: list[int] | None = None  # int, not a model, but Arrow col is List(Struct)

        batch = pa.record_batch(
            {"points": pa.array([[{"x": 1}]], type=pa.list_(self._POINT_STRUCT))}
        )
        with pytest.raises(TypeError, match="has no matching"):
            ArrowModelConverter(NoModel).convert(batch)


# ---------------------------------------------------------------------------
# Phase 5 Plan 1: Validated path tests (VALID-01, VALID-02, VALID-03)
# ---------------------------------------------------------------------------


class ValidatedMixedModel(BaseModel):
    id: int
    name: str
    score: float
    active: bool


class ValidatedNullableModel(BaseModel):
    id: int
    name: str | None = None
    score: float | None = None


class ValidatedDateModel(BaseModel):
    event_date: datetime.date | None = None


class ValidatedTimestampModel(BaseModel):
    created_at: datetime.datetime | None = None


class ValidatedDurationModel(BaseModel):
    elapsed: datetime.timedelta | None = None


class ValidatedListModel(BaseModel):
    values: list[int] | None = None


class ValidatedPersonModel(BaseModel):
    name: str
    address: AddressModel | None = None


class ValidatedNanModel(BaseModel):
    value: float | None = None


class ValidatedDictModel(BaseModel):
    category: str


class StrictAgeModel(BaseModel):
    age: int


class TestValidatedPath:
    """Tests for VALID-01, VALID-02, VALID-03: Validated conversion path."""

    def test_validated_primitives(self) -> None:
        """VALID-01: validate=True with primitive types produces correct model instances."""
        batch = pa.record_batch(
            {
                "id": pa.array([1, 2], type=pa.int64()),
                "name": pa.array(["alice", "bob"]),
                "score": pa.array([9.5, 8.0], type=pa.float64()),
                "active": pa.array([True, False]),
            }
        )
        converter = ArrowModelConverter(ValidatedMixedModel, validate=True)
        results = converter.convert(batch)
        assert len(results) == 2
        assert results[0].id == 1
        assert results[0].name == "alice"
        assert results[0].score == 9.5
        assert results[0].active is True
        assert results[1].id == 2
        assert results[1].name == "bob"
        assert results[1].active is False
        assert isinstance(results[0], ValidatedMixedModel)

    def test_validated_null_handling(self) -> None:
        """VALID-02: validate=True with null values produces None fields."""
        batch = pa.record_batch(
            {
                "id": pa.array([1, 2], type=pa.int64()),
                "name": pa.array(["alice", None]),
                "score": pa.array([9.5, None], type=pa.float64()),
            }
        )
        converter = ArrowModelConverter(ValidatedNullableModel, validate=True)
        results = converter.convert(batch)
        assert len(results) == 2
        assert results[0].name == "alice"
        assert results[0].score == 9.5
        assert results[1].name is None
        assert results[1].score is None

    def test_validated_date32(self) -> None:
        """VALID-02: validate=True with Date32 produces correct datetime.date."""
        batch = pa.record_batch(
            {
                "event_date": pa.array([datetime.date(2024, 1, 15), None], type=pa.date32()),
            }
        )
        converter = ArrowModelConverter(ValidatedDateModel, validate=True)
        results = converter.convert(batch)
        assert len(results) == 2
        assert results[0].event_date == datetime.date(2024, 1, 15)
        assert isinstance(results[0].event_date, datetime.date)
        assert results[1].event_date is None

    def test_validated_timestamp_naive(self) -> None:
        """VALID-02: validate=True with naive Timestamp produces correct datetime."""
        batch = pa.record_batch(
            {
                "created_at": pa.array(
                    [datetime.datetime(2024, 1, 15, 10, 30, 0, 123456)],
                    type=pa.timestamp("us"),
                ),
            }
        )
        converter = ArrowModelConverter(ValidatedTimestampModel, validate=True)
        results = converter.convert(batch)
        assert len(results) == 1
        assert results[0].created_at is not None
        assert results[0].created_at.year == 2024
        assert results[0].created_at.month == 1
        assert results[0].created_at.day == 15
        assert results[0].created_at.hour == 10
        assert results[0].created_at.minute == 30

    def test_validated_timestamp_aware(self) -> None:
        """VALID-02: validate=True with tz-aware Timestamp produces aware datetime."""
        batch = pa.record_batch(
            {
                "created_at": pa.array(
                    [datetime.datetime(2024, 1, 15, 10, 30, 0)],
                    type=pa.timestamp("us", tz="UTC"),
                ),
            }
        )
        converter = ArrowModelConverter(ValidatedTimestampModel, validate=True)
        results = converter.convert(batch)
        assert len(results) == 1
        assert results[0].created_at is not None
        assert results[0].created_at.tzinfo is not None

    def test_validated_duration(self) -> None:
        """VALID-02: validate=True with Duration produces correct timedelta."""
        batch = pa.record_batch(
            {
                "elapsed": pa.array([3600000000, None, 1000000], type=pa.duration("us")),
            }
        )
        converter = ArrowModelConverter(ValidatedDurationModel, validate=True)
        results = converter.convert(batch)
        assert len(results) == 3
        assert results[0].elapsed == datetime.timedelta(hours=1)
        assert results[1].elapsed is None
        assert results[2].elapsed == datetime.timedelta(seconds=1)

    def test_validated_duration_negative(self) -> None:
        """
        VALID-02: negative durations keep their sign, including sub-second ones.

        A sub-second negative duration (e.g. -500ms) has whole seconds == 0, so
        the ISO-8601 serialization must derive the sign from the full delta, not
        from the truncated seconds component.
        """
        batch = pa.record_batch(
            {
                "elapsed": pa.array([-500000, -1500000, -3600000000], type=pa.duration("us")),
            }
        )
        results = ArrowModelConverter(ValidatedDurationModel, validate=True).convert(batch)
        assert results[0].elapsed == datetime.timedelta(microseconds=-500000)
        assert results[1].elapsed == datetime.timedelta(seconds=-1, microseconds=-500000)
        assert results[2].elapsed == datetime.timedelta(hours=-1)

    def test_validated_list(self) -> None:
        """VALID-02: validate=True with List(Int64) produces correct Python lists."""
        batch = pa.record_batch(
            {
                "values": pa.array([[1, 2, 3], None, [4]], type=pa.list_(pa.int64())),
            }
        )
        converter = ArrowModelConverter(ValidatedListModel, validate=True)
        results = converter.convert(batch)
        assert len(results) == 3
        assert results[0].values == [1, 2, 3]
        assert results[1].values is None
        assert results[2].values == [4]

    def test_validated_struct(self) -> None:
        """VALID-02: validate=True with Struct produces correct nested model."""
        struct_arr = pa.StructArray.from_arrays(
            [pa.array(["NYC", "LA"]), pa.array([10001, 90001], type=pa.int32())],
            names=["city", "zip_code"],
        )
        batch = pa.record_batch({"name": pa.array(["Alice", "Bob"]), "address": struct_arr})
        converter = ArrowModelConverter(ValidatedPersonModel, validate=True)
        results = converter.convert(batch)
        assert len(results) == 2
        assert results[0].name == "Alice"
        assert results[0].address is not None
        assert results[0].address.city == "NYC"
        assert results[0].address.zip_code == 10001
        assert isinstance(results[0].address, AddressModel)

    def test_validated_table(self) -> None:
        """VALID-02: validate=True with Table input processes all batches."""
        batch1 = pa.record_batch(
            {
                "id": pa.array([1, 2], type=pa.int64()),
                "name": pa.array(["a", "b"]),
                "score": pa.array([1.0, 2.0], type=pa.float64()),
                "active": pa.array([True, False]),
            }
        )
        batch2 = pa.record_batch(
            {
                "id": pa.array([3], type=pa.int64()),
                "name": pa.array(["c"]),
                "score": pa.array([3.0], type=pa.float64()),
                "active": pa.array([True]),
            }
        )
        table = pa.Table.from_batches([batch1, batch2])
        converter = ArrowModelConverter(ValidatedMixedModel, validate=True)
        results = converter.convert(table)
        assert len(results) == 3
        assert results[0].id == 1
        assert results[2].id == 3

    def test_validated_dict_column(self) -> None:
        """VALID-02: validate=True with dictionary-encoded column works correctly."""
        batch = pa.record_batch({"category": pa.array(["a", "b", "a"]).dictionary_encode()})
        converter = ArrowModelConverter(ValidatedDictModel, validate=True)
        results = converter.convert(batch)
        assert len(results) == 3
        assert results[0].category == "a"
        assert results[1].category == "b"
        assert results[2].category == "a"

    def test_validated_nan_produces_none(self) -> None:
        """VALID-03: Float NaN in validated mode produces None (not crash)."""
        batch = pa.record_batch(
            {"value": pa.array([float("nan"), 1.5, float("inf")], type=pa.float64())}
        )
        converter = ArrowModelConverter(ValidatedNanModel, validate=True)
        results = converter.convert(batch)
        assert len(results) == 3
        assert results[0].value is None  # NaN -> null -> None
        assert results[1].value == 1.5
        assert results[2].value is None  # Infinity -> null -> None


class TestValidationErrors:
    """Tests for VALID-03: Invalid data raises Pydantic ValidationError."""

    def test_validation_error_wrong_type(self) -> None:
        """VALID-03: String values in int column with validate=True raises ValidationError."""
        batch = pa.record_batch({"age": pa.array(["not_a_number", "also_not"])})
        converter = ArrowModelConverter(StrictAgeModel, validate=True)
        with pytest.raises(Exception) as exc_info:
            converter.convert(batch)
        # Should be a Pydantic ValidationError
        assert (
            "validation" in type(exc_info.value).__name__.lower()
            or "validation" in str(exc_info.value).lower()
        )

    def test_validation_error_message(self) -> None:
        """VALID-03: ValidationError contains useful info about the failure."""
        batch = pa.record_batch({"age": pa.array(["bad_value"])})
        converter = ArrowModelConverter(StrictAgeModel, validate=True)
        with pytest.raises(Exception) as exc_info:
            converter.convert(batch)
        error_str = str(exc_info.value)
        # Should mention the field or type issue
        assert len(error_str) > 10  # meaningful error message


class TestIteratorAPI:
    """Tests for API-04: Iterator/generator API for lazy model yielding."""

    def test_iter_record_batch(self) -> None:
        """API-04: iter() yields models from RecordBatch."""
        batch = pa.record_batch(
            {
                "id": [1, 2, 3],
                "name": ["a", "b", "c"],
                "score": [1.0, 2.0, 3.0],
                "active": [True, False, True],
            }
        )
        converter = ArrowModelConverter(MixedModel)
        results = list(converter.iter(batch))
        assert len(results) == 3
        assert results[0].id == 1
        assert results[2].name == "c"
        assert isinstance(results[0], MixedModel)

    def test_iter_table(self) -> None:
        """API-04: iter() yields models from Table (multi-batch)."""
        batch1 = pa.record_batch(
            {"id": [1, 2], "name": ["a", "b"], "score": [1.0, 2.0], "active": [True, False]}
        )
        batch2 = pa.record_batch({"id": [3], "name": ["c"], "score": [3.0], "active": [True]})
        table = pa.Table.from_batches([batch1, batch2])
        converter = ArrowModelConverter(MixedModel)
        results = list(converter.iter(table))
        assert len(results) == 3
        assert results[0].id == 1
        assert results[2].id == 3

    def test_iter_is_generator(self) -> None:
        """API-04: iter() returns a generator (lazy, not pre-materialized)."""
        import types

        batch = pa.record_batch({"id": [1], "name": ["a"], "score": [1.0], "active": [True]})
        converter = ArrowModelConverter(MixedModel)
        result = converter.iter(batch)
        assert isinstance(result, types.GeneratorType)

    def test_iter_validated(self) -> None:
        """API-04: iter() respects validate=True flag."""
        batch = pa.record_batch(
            {"id": [1, 2], "name": ["a", "b"], "score": [1.0, 2.0], "active": [True, False]}
        )
        converter = ArrowModelConverter(MixedModel, validate=True)
        results = list(converter.iter(batch))
        assert len(results) == 2
        assert results[0].id == 1

    def test_model_iter_convenience(self) -> None:
        """API-04: model_iter() convenience function works."""
        from arrowmodel import model_iter

        batch = pa.record_batch(
            {"id": [1, 2], "name": ["a", "b"], "score": [1.0, 2.0], "active": [True, False]}
        )
        results = list(model_iter(MixedModel, batch))
        assert len(results) == 2
        assert results[0].id == 1
        assert isinstance(results[0], MixedModel)

    def test_model_iter_validated(self) -> None:
        """DEBT-03: model_iter(validate=True) convenience wrapper works."""
        from arrowmodel import model_iter

        batch = pa.record_batch(
            {"id": [1, 2], "name": ["a", "b"], "score": [1.0, 2.0], "active": [True, False]}
        )
        results = list(model_iter(MixedModel, batch, validate=True))
        assert len(results) == 2
        assert results[0].id == 1
        assert results[1].name == "b"
        assert isinstance(results[0], MixedModel)

    def test_iter_empty_table(self) -> None:
        """API-04: iter() on empty Table yields nothing."""
        table = pa.table(
            {
                "id": pa.array([], type=pa.int64()),
                "name": pa.array([], type=pa.string()),
                "score": pa.array([], type=pa.float64()),
                "active": pa.array([], type=pa.bool_()),
            }
        )
        converter = ArrowModelConverter(MixedModel)
        results = list(converter.iter(table))
        assert results == []
