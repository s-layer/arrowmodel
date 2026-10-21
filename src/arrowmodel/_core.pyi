"""Type stubs for the arrowmodel._core Rust extension module."""

from collections.abc import Sequence
from typing import Any

def record_batch_info(batch: Any, /) -> tuple[int, int]: ...
def arrow_schema_fields(schema: Any, /) -> list[tuple[str, bool]]: ...

class ArrowInput:
    """One pass over a RecordBatch or an Arrow stream of record batches."""

    def __init__(self, data: Any, /) -> None: ...
    def fields(self) -> list[tuple[str, bool]]: ...
    def convert_next(
        self,
        model_cls: type[Any],
        field_specs: Sequence[tuple[int, str, type[Any] | None]],
        validate: bool,
        /,
    ) -> list[Any] | None: ...
    def convert_all(
        self,
        model_cls: type[Any],
        field_specs: Sequence[tuple[int, str, type[Any] | None]],
        validate: bool,
        /,
    ) -> list[Any]: ...
