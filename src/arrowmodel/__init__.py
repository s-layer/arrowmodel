"""Arrowmodel: dict-free conversion from Arrow buffers to Pydantic model instances."""

from __future__ import annotations

import types
import typing
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, Self, TypeAlias, cast

from pydantic import AliasChoices, AliasPath, BaseModel

from arrowmodel import _core as _core

if TYPE_CHECKING:
    from collections.abc import Iterator


class ArrowArrayExportable(Protocol):
    """Anything exporting one Arrow array via the PyCapsule Interface (e.g. a RecordBatch)."""

    def __arrow_c_array__(self, requested_schema: Any = None) -> tuple[Any, Any]: ...


class ArrowStreamExportable(Protocol):
    """
    Anything exporting an Arrow stream via the PyCapsule Interface.

    For example a pyarrow ``Table`` or ``RecordBatchReader``, a Polars
    ``DataFrame``, or a nanoarrow array stream.
    """

    def __arrow_c_stream__(self, requested_schema: Any = None) -> Any: ...


class ArrowSchemaExportable(Protocol):
    """Anything exporting an Arrow schema via the PyCapsule Interface (e.g. a pyarrow Schema)."""

    def __arrow_c_schema__(self) -> Any: ...


ArrowData: TypeAlias = ArrowArrayExportable | ArrowStreamExportable
"""Arrow record-batch data that arrowmodel converts: a RecordBatch, Table or stream."""

__all__ = [
    "ArrowArrayExportable",
    "ArrowData",
    "ArrowModel",
    "ArrowModelConverter",
    "ArrowSchemaExportable",
    "ArrowStreamExportable",
    "model_convert",
    "model_iter",
    "_build_field_map",
    "_construct_plan",
    "_get_nested_model",
    "_core",
]


def _get_nested_model(annotation: Any) -> type[BaseModel] | None:
    """
    Extract the nested BaseModel class from a Pydantic field annotation.

    Returns the first BaseModel found by walking the annotation's type
    arguments, which is what the Rust extractor threads into a Struct child.

    Handles:
    - Direct BaseModel subclass: ``NestedModel`` -> ``NestedModel``
    - Optional[NestedModel] (Union[NestedModel, None]) -> ``NestedModel``
    - Container of model: ``list[NestedModel]``, ``list[list[NestedModel]]``,
      ``dict[str, NestedModel]`` -> ``NestedModel`` (the leaf element model)
    - Non-model types: -> ``None``

    The returned model is only *used* when the corresponding Arrow column (or a
    child of it) is a Struct; for non-Struct columns the Rust side ignores it,
    so returning a leaf model for a container annotation is always safe.
    """
    if annotation is None:
        return None
    # Direct BaseModel subclass
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    # Recurse into type arguments: Optional[Model], list[Model],
    # list[list[Model]], dict[str, Model], etc. Returns the first model found.
    for arg in typing.get_args(annotation):
        nested = _get_nested_model(arg)
        if nested is not None:
            return nested
    return None


def _is_mapping_annotation(annotation: Any) -> bool:
    """
    True if the annotation is (or wraps via Optional/Union) a ``dict``/``Mapping``.

    Used to give an actionable error when a mapping-typed field targets an Arrow
    ``Map`` column, which arrowmodel materialises as ``list[tuple[K, V]]`` rather
    than a ``dict`` (see :func:`_get_nested_model` for the model-threading path).
    """
    if annotation is None:
        return False
    if isinstance(annotation, type):
        return issubclass(annotation, Mapping)
    origin = typing.get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        return any(_is_mapping_annotation(arg) for arg in typing.get_args(annotation))
    if isinstance(origin, type):
        return issubclass(origin, Mapping)
    return False


_BASE_MODEL_CONSTRUCT: Any = BaseModel.__dict__["model_construct"].__func__

_ConstructPlan = tuple[list[tuple[str, int, Any]], bool, bool]
"""``(slots, extra_allow, post_init)`` -- see :func:`_construct_plan`."""


def _construct_plan(model_class: type[BaseModel], keys: list[str]) -> _ConstructPlan | None:
    """
    Precompute what ``model_class.model_construct(**kwargs)`` does with these keys.

    ``BaseModel.model_construct`` is pure Python: for every row it walks every
    model field, checks its alias and validation aliases against the kwargs, and
    pops values into a new ``__dict__``. Which kwarg ends up in which field
    depends only on the kwarg *names*, and every row of a column set has the same
    names, so the matching can be resolved once here. The Rust row loop then
    builds each instance directly, without calling ``model_construct``.

    Returns ``(slots, extra_allow, post_init)``, where ``slots`` lists the model
    fields in declaration order (the order ``model_construct`` fills
    ``__dict__``) as ``(field_name, key_index, field_info)``:

    - ``key_index >= 0``: the value comes from ``keys[key_index]``; the field is
      in ``model_fields_set``.
    - ``key_index == -1``: no key matches and the field has a default;
      ``field_info.get_default(call_default_factory=True, validated_data=...)``
      supplies it per row, as ``model_construct`` does.

    Required fields with no matching key are left out, as ``model_construct``
    leaves them out of ``__dict__``.

    Returns ``None`` when the model needs the real ``model_construct``: it
    overrides ``model_construct`` (``RootModel`` does), a field's validation
    alias is an ``AliasPath`` that ``model_construct`` would evaluate (the match
    depends on the values, not just the names), or unmatched keys would land in
    ``__pydantic_extra__`` or private attributes (``extra='allow'``, or a
    ``model_post_init`` hook).
    """
    construct = getattr(model_class.model_construct, "__func__", None)
    if construct is not _BASE_MODEL_CONSTRUCT:
        return None

    remaining = {key: index for index, key in enumerate(keys)}
    slots: list[tuple[str, int, Any]] = []
    for name, field in model_class.__pydantic_fields__.items():
        index: int | None = None
        if field.alias is not None and field.alias in remaining:
            index = remaining.pop(field.alias)
        va = field.validation_alias
        if index is None and va is not None:
            choices = va.choices if isinstance(va, AliasChoices) else [va]
            for alias in choices:
                if isinstance(alias, str):
                    if alias in remaining:
                        index = remaining.pop(alias)
                        break
                else:
                    return None  # AliasPath: the match depends on the values
        if index is None and name in remaining:
            index = remaining.pop(name)
        if index is not None:
            slots.append((name, index, None))
        elif not field.is_required():
            slots.append((name, -1, field))

    extra_allow = model_class.model_config.get("extra") == "allow"
    post_init = bool(model_class.__pydantic_post_init__)
    if remaining and (extra_allow or post_init):
        return None
    return slots, extra_allow, post_init


def _build_field_map(model_class: type[BaseModel]) -> dict[str, str]:
    """
    Build ``{arrow_column_name: pydantic_field_name}`` mapping.

    Resolution priority (ALIAS-01): validation_alias > alias > field_name.
    Raises NotImplementedError for AliasPath, AliasChoices (ALIAS-03).
    Raises NotImplementedError for AliasGenerator on model config (ALIAS-03).
    When populate_by_name or validate_by_name is enabled (ALIAS-02),
    both alias and field name are accepted for column lookup.
    """
    config = model_class.model_config

    # ALIAS-03: Reject AliasGenerator on model config
    alias_gen = config.get("alias_generator")
    if alias_gen is not None:
        raise NotImplementedError(
            f"AliasGenerator on {model_class.__name__} is not supported. "
            "Use explicit per-field aliases instead."
        )

    field_map: dict[str, str] = {}

    for field_name, field_info in model_class.model_fields.items():
        va = field_info.validation_alias
        if va is not None:
            # ALIAS-03: Reject AliasPath / AliasChoices
            if isinstance(va, (AliasPath, AliasChoices)):
                raise NotImplementedError(
                    f"Field {field_name!r} uses {type(va).__name__} as "
                    "validation_alias, which is not supported."
                )
            lookup_name = va  # str
        elif field_info.alias is not None:
            lookup_name = field_info.alias
        else:
            lookup_name = field_name

        field_map[lookup_name] = field_name

    # ALIAS-02: populate_by_name / validate_by_name support
    accept_by_name = config.get("validate_by_name", False) or config.get("populate_by_name", False)
    if accept_by_name:
        for field_name in model_class.model_fields:
            # Only add if not already present (alias takes priority)
            if field_name not in field_map:
                field_map[field_name] = field_name

    return field_map


class ArrowModelConverter:
    """
    Convert Arrow RecordBatch data to Pydantic model instances.

    Cross-references Arrow schema against Pydantic model fields at
    construction time (alias-aware field map built once) and at convert()
    time (column indices resolved per-batch schema).  The default fast path
    builds instances with ``model_construct`` semantics (no validation).

    Per SCHEMA-01: Cross-references Arrow schema against Pydantic model fields.
    Per SCHEMA-02: Schema mapping compiled once at init, reused across batches.
    Per SCHEMA-03: ValueError raised at convert() for missing required fields.
    Per SCHEMA-04: Extra Arrow columns silently ignored.
    Per ALIAS-01: validation_alias > alias > field_name priority.
    Per ALIAS-02: populate_by_name / validate_by_name support.
    Per ALIAS-03: AliasPath, AliasChoices, AliasGenerator raise NotImplementedError.
    Per API-01: Constructor accepts model class and optional validate flag.
    """

    def __init__(
        self,
        model_class: type[BaseModel],
        *,
        validate: bool = False,
    ) -> None:
        self._model_class = model_class
        self._validate = validate
        # SCHEMA-01, SCHEMA-02, ALIAS-01: Build alias-aware field map once at init
        self._field_map: dict[str, str] = _build_field_map(model_class)

    def _resolve_columns(
        self, schema: ArrowSchemaExportable | Sequence[tuple[str, bool]]
    ) -> list[tuple[int, str, type[BaseModel] | None]]:
        """
        Resolve Arrow column indices from schema using the field map.

        ``schema`` is any object exporting ``__arrow_c_schema__`` (e.g. a
        pyarrow ``Schema``), or the ``(name, is_map)`` pairs of its columns.
        Column names that occur more than once are treated as absent, as
        pyarrow's ``Schema.get_field_index`` does.

        Returns ``field_specs``: list of ``(col_index, field_name, nested_model_cls)``
        for Rust. ``nested_model_cls`` is non-None when the Pydantic field's
        annotation is a BaseModel subclass (for Struct column conversion).

        Raises ValueError for missing required columns (SCHEMA-03).
        Extra Arrow columns are silently ignored (SCHEMA-04).

        When populate_by_name / validate_by_name is enabled, the field_map
        may contain multiple lookup names mapping to the same Pydantic field.
        A field is considered resolved if ANY of its lookup names match an
        Arrow column.
        """
        fields = (
            list(schema)
            if isinstance(schema, Sequence)
            else _core.arrow_schema_fields(cast("Any", schema))
        )
        names = [name for name, _ in fields]
        name_counts = Counter(names)
        index_of = {name: i for i, name in enumerate(names) if name_counts[name] == 1}

        field_specs: list[tuple[int, str, type[BaseModel] | None]] = []
        # Track which Pydantic fields have been resolved (handles multiple
        # lookup names mapping to the same field, e.g. populate_by_name)
        resolved_fields: set[str] = set()

        for lookup_name, field_name in self._field_map.items():
            if field_name in resolved_fields:
                continue
            col_idx = index_of.get(lookup_name)
            if col_idx is None:
                continue
            field_info = self._model_class.model_fields[field_name]
            # Guard: a dict/Mapping field cannot be fed from a Map column, which
            # is materialised as list[tuple[K, V]] (dict output is unsupported --
            # Map keys may be non-string or duplicated). Raise here rather than
            # silently produce a mistyped list (fast path) or a generic Pydantic
            # error (validated path).
            if _is_mapping_annotation(field_info.annotation) and fields[col_idx][1]:
                raise TypeError(
                    f"Field {field_name!r} is annotated as a mapping (dict/Mapping), "
                    f"but the Arrow column {lookup_name!r} is a Map, which arrowmodel "
                    f"materialises as a list of (key, value) pairs. Annotate the field "
                    f"as list[tuple[K, V]] instead; dict output from Map columns is not "
                    f"supported."
                )
            # Detect nested BaseModel for Struct columns
            nested_model = _get_nested_model(field_info.annotation)
            field_specs.append((col_idx, field_name, nested_model))
            resolved_fields.add(field_name)

        # Check for missing required fields
        missing: list[str] = []
        for field_name, field_info in self._model_class.model_fields.items():
            if field_name not in resolved_fields and field_info.is_required():
                # Find the lookup name(s) for this field for the error message
                lookup_names = [ln for ln, fn in self._field_map.items() if fn == field_name]
                missing.extend(lookup_names[:1])  # report primary lookup name

        if missing:
            raise ValueError(
                f"Arrow schema is missing required columns: {missing}. Available columns: {names}"
            )

        return field_specs

    def convert(self, data: ArrowData) -> list[BaseModel]:
        """
        Convert Arrow data to a list of Pydantic model instances.

        Per API-02: Returns list[Model].
        Per INPUT-01: Accepts a RecordBatch (anything exporting ``__arrow_c_array__``).
        Per INPUT-02: Accepts a Table, RecordBatchReader, Polars DataFrame or other
        Arrow stream (anything exporting ``__arrow_c_stream__``); a one-shot
        stream such as a RecordBatchReader is consumed.
        Per SCHEMA-03: Raises ValueError when required fields cannot be matched.
        Per SCHEMA-04: Extra Arrow columns silently ignored.
        Raises TypeError for anything that is not Arrow record-batch data.
        """
        source = _core.ArrowInput(data)
        field_specs = self._resolve_columns(source.fields())
        return source.convert_all(self._model_class, field_specs, self._validate)

    def iter(self, data: ArrowData) -> Iterator[BaseModel]:
        """
        Lazily yield individual model instances from Arrow data.

        Converts one record batch at a time, so only one batch's worth of
        instances is materialised at once; a RecordBatchReader or other stream
        is read batch by batch as you iterate. For a single RecordBatch,
        behaviour is equivalent to iterating over convert() results.

        Per API-04: Iterator/generator API for lazy model yielding.
        """
        source = _core.ArrowInput(data)
        field_specs = self._resolve_columns(source.fields())
        while (
            batch := source.convert_next(self._model_class, field_specs, self._validate)
        ) is not None:
            yield from batch


class ArrowModel(BaseModel):
    """
    Pydantic BaseModel subclass with Arrow conversion classmethods.

    Subclasses auto-generate an ``ArrowModelConverter`` at class definition
    time (via ``__pydantic_init_subclass__``), so users can call
    ``MyModel.convert(batch)`` without manually creating a converter.

    Example::

        class User(ArrowModel):
            id: int
            name: str

        users: list[User] = User.convert(batch)

    The fast path (``model_construct``, no validation) is used by default.
    Pass ``validate=True`` to use the validated path
    (``model_validate_json``).
    """

    _arrow_converter: ClassVar[ArrowModelConverter]
    _arrow_converter_validated: ClassVar[ArrowModelConverter]

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: Any) -> None:
        super().__pydantic_init_subclass__(**kwargs)
        # Always create the converter, even for field-less subclasses, so that
        # `.convert()` / `.iter()` return an empty/degenerate result instead of
        # raising AttributeError on a missing `_arrow_converter`.
        # __pydantic_init_subclass__ runs after Pydantic's metaclass has
        # populated model_fields, unlike __init_subclass__ which fires
        # before field resolution.
        cls._arrow_converter = ArrowModelConverter(cls, validate=False)

    @classmethod
    def convert(cls, data: ArrowData, *, validate: bool = False) -> list[Self]:
        """
        Convert Arrow RecordBatch or Table to a list of model instances.

        Args:
            data: Arrow RecordBatch or Table to convert.
            validate: If True, use the validated path (model_validate_json).
                Defaults to False (fast path via model_construct).

        Returns:
            List of model instances.
        """
        if validate:
            if not hasattr(cls, "_arrow_converter_validated"):
                cls._arrow_converter_validated = ArrowModelConverter(cls, validate=True)
            return cls._arrow_converter_validated.convert(data)  # type: ignore[return-value]
        return cls._arrow_converter.convert(data)  # type: ignore[return-value]

    @classmethod
    def iter(cls, data: ArrowData, *, validate: bool = False) -> Iterator[Self]:
        """
        Lazily yield individual model instances from Arrow data.

        Args:
            data: Arrow RecordBatch or Table to convert.
            validate: If True, use the validated path (model_validate_json).
                Defaults to False (fast path via model_construct).

        Yields:
            Model instances one at a time.
        """
        if validate:
            if not hasattr(cls, "_arrow_converter_validated"):
                cls._arrow_converter_validated = ArrowModelConverter(cls, validate=True)
            yield from cls._arrow_converter_validated.iter(data)  # type: ignore[misc]
        else:
            yield from cls._arrow_converter.iter(data)  # type: ignore[misc]


def model_convert(
    model_class: type[BaseModel],
    data: ArrowData,
    *,
    validate: bool = False,
) -> list[BaseModel]:
    """
    One-shot conversion from Arrow data to Pydantic model instances.

    Convenience function that creates a temporary ArrowModelConverter
    and calls convert(). For repeated conversions of the same model,
    prefer creating an ArrowModelConverter instance and reusing it.

    Per API-03: Convenience one-shot function.
    Per DEBT-04: Accepts validate parameter for API symmetry with model_iter.
    """
    converter = ArrowModelConverter(model_class, validate=validate)
    return converter.convert(data)


def model_iter(
    model_class: type[BaseModel],
    data: ArrowData,
    *,
    validate: bool = False,
) -> Iterator[BaseModel]:
    """
    Lazily yield individual model instances from Arrow data.

    Convenience function that creates a temporary ArrowModelConverter
    and calls iter(). For repeated conversions of the same model,
    prefer creating an ArrowModelConverter instance and reusing it.

    Per API-04: Iterator/generator API for lazy model yielding.
    """
    converter = ArrowModelConverter(model_class, validate=validate)
    yield from converter.iter(data)
