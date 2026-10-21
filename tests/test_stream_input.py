"""
Arrow stream input: RecordBatchReader, Polars DataFrame, nanoarrow streams.

Anything exporting ``__arrow_c_stream__`` converts like a pyarrow Table, with no
pyarrow API used on the input itself (INPUT-02).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import polars as pl
import pyarrow as pa
import pytest
from pydantic import BaseModel

from arrowmodel import ArrowModel, ArrowModelConverter, model_convert, model_iter

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator


class Row(BaseModel):
    id: int
    name: str


class Address(BaseModel):
    city: str


class Person(BaseModel):
    id: int
    address: Address | None


class RowModel(ArrowModel):
    id: int
    name: str


def _batches(n_batches: int = 3) -> list[pa.RecordBatch]:
    return [
        pa.record_batch({"id": [2 * i, 2 * i + 1], "name": [f"r{2 * i}", f"r{2 * i + 1}"]})
        for i in range(n_batches)
    ]


def _expected(n_batches: int = 3) -> list[tuple[int, str]]:
    return [(i, f"r{i}") for i in range(2 * n_batches)]


def _rows(models: Iterable[Any]) -> list[tuple[int, str]]:
    return [(m.id, m.name) for m in models]


class TestRecordBatchReader:
    def test_convert(self) -> None:
        batches = _batches()
        reader = pa.RecordBatchReader.from_batches(batches[0].schema, batches)
        assert _rows(ArrowModelConverter(Row).convert(reader)) == _expected()

    def test_convert_validated(self) -> None:
        batches = _batches()
        reader = pa.RecordBatchReader.from_batches(batches[0].schema, batches)
        assert _rows(ArrowModelConverter(Row, validate=True).convert(reader)) == _expected()

    def test_arrow_model_and_one_shot_helpers(self) -> None:
        batches = _batches()
        schema = batches[0].schema
        assert _rows(RowModel.convert(pa.RecordBatchReader.from_batches(schema, batches))) == (
            _expected()
        )
        reader = pa.RecordBatchReader.from_batches(schema, batches)
        assert _rows(model_convert(Row, reader)) == _expected()
        reader = pa.RecordBatchReader.from_batches(schema, batches)
        assert _rows(model_iter(Row, reader)) == _expected()

    def test_iter_reads_one_batch_at_a_time(self) -> None:
        """iter() pulls the next batch from the stream only when it needs it."""
        batches = _batches()
        pulled: list[int] = []

        def source() -> Iterator[pa.RecordBatch]:
            for i, batch in enumerate(batches):
                pulled.append(i)
                yield batch

        reader = pa.RecordBatchReader.from_batches(batches[0].schema, source())
        it = ArrowModelConverter(Row).iter(reader)
        first = next(it)
        assert (first.id, pulled) == (0, [0])
        next(it)
        assert pulled == [0]
        next(it)  # first row of the second batch
        assert pulled == [0, 1]
        assert len(list(it)) == 3
        assert pulled == [0, 1, 2]

    def test_reader_is_consumed(self) -> None:
        """A reader is one-shot: a second pass sees no rows, as reader.read_all() would."""
        batches = _batches()
        reader = pa.RecordBatchReader.from_batches(batches[0].schema, batches)
        converter = ArrowModelConverter(Row)
        assert len(converter.convert(reader)) == 6
        assert converter.convert(reader) == []
        assert reader.read_all().num_rows == 0

    def test_missing_column_raises_before_reading_batches(self) -> None:
        pulled: list[int] = []

        def source() -> Iterator[pa.RecordBatch]:
            pulled.append(0)
            yield pa.record_batch({"id": [1]})

        reader = pa.RecordBatchReader.from_batches(pa.schema([("id", pa.int64())]), source())
        with pytest.raises(ValueError, match="missing required columns"):
            ArrowModelConverter(Row).convert(reader)
        assert pulled == []

    def test_empty_stream(self) -> None:
        schema = pa.schema([("id", pa.int64()), ("name", pa.utf8())])
        reader = pa.RecordBatchReader.from_batches(schema, [])
        assert ArrowModelConverter(Row).convert(reader) == []
        reader = pa.RecordBatchReader.from_batches(schema, [])
        assert list(ArrowModelConverter(Row).iter(reader)) == []


class TestPolars:
    def test_dataframe(self) -> None:
        df = pl.DataFrame({"id": [1, 2], "name": ["a", "b"], "extra": [0.5, 1.5]})
        assert _rows(ArrowModelConverter(Row).convert(df)) == [(1, "a"), (2, "b")]
        assert _rows(ArrowModelConverter(Row, validate=True).convert(df)) == [(1, "a"), (2, "b")]
        assert _rows(ArrowModelConverter(Row).iter(df)) == [(1, "a"), (2, "b")]

    def test_dataframe_struct_column(self) -> None:
        df = pl.DataFrame({"id": [1, 2], "address": [{"city": "NYC"}, None]})
        people = ArrowModelConverter(Person).convert(df)
        assert isinstance(people[0].address, Address)
        assert people[0].address.city == "NYC"
        assert people[1].address is None

    def test_dataframe_can_be_converted_twice(self) -> None:
        """A DataFrame exports a fresh stream each time, unlike a reader."""
        df = pl.DataFrame({"id": [1], "name": ["a"]})
        converter = ArrowModelConverter(Row)
        assert converter.convert(df) == converter.convert(df)

    def test_missing_column_error_lists_polars_columns(self) -> None:
        df = pl.DataFrame({"id": [1]})
        with pytest.raises(ValueError, match=r"Available columns: \['id'\]"):
            ArrowModelConverter(Row).convert(df)

    def test_dict_field_over_map_column_raises(self) -> None:
        """The Map guard works on non-pyarrow input (it reads the Arrow schema)."""

        class Tagged(BaseModel):
            tags: dict[str, int]

        table = pa.table({"tags": pa.array([[("a", 1)]], type=pa.map_(pa.utf8(), pa.int64()))})
        stream = pa.RecordBatchReader.from_batches(table.schema, table.to_batches())
        with pytest.raises(TypeError, match="is a Map"):
            ArrowModelConverter(Tagged).convert(stream)


class TestNanoarrow:
    def test_array_stream(self) -> None:
        na = pytest.importorskip("nanoarrow")
        stream = na.c_array_stream(pa.Table.from_batches(_batches()))
        assert _rows(ArrowModelConverter(Row).convert(stream)) == _expected()

    def test_record_batch_array(self) -> None:
        """A nanoarrow struct array exports __arrow_c_array__, like a RecordBatch."""
        na = pytest.importorskip("nanoarrow")
        array = na.c_array(_batches(1)[0])
        assert _rows(ArrowModelConverter(Row).convert(array)) == [(0, "r0"), (1, "r1")]


class TestNonRecordBatchStreams:
    def test_chunked_array_of_ints_rejected(self) -> None:
        with pytest.raises(TypeError, match="RecordBatch or Table"):
            ArrowModelConverter(Row).convert(pa.chunked_array([[1, 2]]))

    def test_polars_series_rejected(self) -> None:
        with pytest.raises(TypeError, match="RecordBatch or Table"):
            ArrowModelConverter(Row).convert(pl.Series("id", [1, 2]))


class TestStructArrays:
    """A struct array has the same layout as a record batch and converts like one."""

    def test_struct_array(self) -> None:
        array = _batches(1)[0].to_struct_array()
        assert _rows(ArrowModelConverter(Row).convert(array)) == [(0, "r0"), (1, "r1")]

    def test_struct_chunked_array(self) -> None:
        chunked = pa.chunked_array([batch.to_struct_array() for batch in _batches()])
        assert _rows(ArrowModelConverter(Row).convert(chunked)) == _expected()
        assert _rows(ArrowModelConverter(Row).iter(chunked)) == _expected()

    def test_struct_array_missing_field(self) -> None:
        array = pa.array([{"id": 1}], type=pa.struct([("id", pa.int64())]))
        with pytest.raises(ValueError, match="name"):
            ArrowModelConverter(Row).convert(array)


class TestThreads:
    """iter() generators can be advanced from different threads (e.g. Starlette's threadpool)."""

    def test_iter_advanced_on_another_thread(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        batches = _batches()
        reader = pa.RecordBatchReader.from_batches(batches[0].schema, batches)
        gen = ArrowModelConverter(Row).iter(reader)
        first = [next(gen), next(gen)]  # opens the input and reads batch 1 here
        with ThreadPoolExecutor(max_workers=1) as pool:
            rest = pool.submit(list, gen).result()
        assert _rows(first + rest) == _expected()

    @pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning")
    def test_iter_dropped_on_another_thread(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        gen = ArrowModelConverter(Row).iter(pa.Table.from_batches(_batches()))
        next(gen)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(gen.close).result()
