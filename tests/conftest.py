"""Shared pytest fixtures for arrowmodel test suite."""

from __future__ import annotations

import ctypes
import datetime

import pyarrow as pa
import pytest

# ---------------------------------------------------------------------------
# Arrow C Data Interface helpers for interval subtype construction
# ---------------------------------------------------------------------------
# pyarrow has no public API for constructing IntervalYearMonth or
# IntervalDayTime arrays.  The workaround: build an int32/int64 batch,
# export via the C Data Interface, flip the column format string to the
# correct Arrow interval type code, then re-import.
# ---------------------------------------------------------------------------


class _CSchema(ctypes.Structure):
    _fields_ = [
        ("format", ctypes.c_char_p),
        ("name", ctypes.c_char_p),
        ("metadata", ctypes.c_char_p),
        ("flags", ctypes.c_int64),
        ("n_children", ctypes.c_int64),
        ("children", ctypes.c_void_p),
        ("dictionary", ctypes.c_void_p),
        ("release", ctypes.c_void_p),
        ("private_data", ctypes.c_void_p),
    ]


class _CArray(ctypes.Structure):
    _fields_ = [
        ("length", ctypes.c_int64),
        ("null_count", ctypes.c_int64),
        ("offset", ctypes.c_int64),
        ("n_buffers", ctypes.c_int64),
        ("n_children", ctypes.c_int64),
        ("buffers", ctypes.c_void_p),
        ("children", ctypes.c_void_p),
        ("dictionary", ctypes.c_void_p),
        ("release", ctypes.c_void_p),
        ("private_data", ctypes.c_void_p),
    ]


def _reinterpret_column(batch: pa.RecordBatch, col_name: str, new_format: bytes) -> pa.RecordBatch:
    """
    Re-import *batch* via C Data Interface with one column's type changed.

    This is the only reliable way to create IntervalYearMonth / IntervalDayTime
    RecordBatches from pyarrow, which lacks native constructors for these types.
    """
    c_arr = _CArray()
    c_sch = _CSchema()
    batch._export_to_c(ctypes.addressof(c_arr), ctypes.addressof(c_sch))
    col_name_bytes = col_name.encode()
    for i in range(c_sch.n_children):
        child_ptr = ctypes.cast(c_sch.children, ctypes.POINTER(ctypes.c_void_p))[i]
        child = ctypes.cast(child_ptr, ctypes.POINTER(_CSchema)).contents
        if child.name == col_name_bytes:
            child.format = new_format
            break
    return pa.RecordBatch._import_from_c(ctypes.addressof(c_arr), ctypes.addressof(c_sch))  # type: ignore[arg-type]


@pytest.fixture
def sample_record_batch() -> pa.RecordBatch:
    """A simple RecordBatch with int and string columns."""
    return pa.record_batch({"x": [1, 2, 3], "y": ["a", "b", "c"]})


@pytest.fixture
def int_batch() -> pa.RecordBatch:
    """RecordBatch with all signed integer types."""
    return pa.record_batch(
        {
            "i8": pa.array([1, -1, 127], type=pa.int8()),
            "i16": pa.array([1, -1, 32767], type=pa.int16()),
            "i32": pa.array([1, -1, 2147483647], type=pa.int32()),
            "i64": pa.array([1, -1, 9223372036854775807], type=pa.int64()),
        }
    )


@pytest.fixture
def uint_batch() -> pa.RecordBatch:
    """RecordBatch with all unsigned integer types."""
    return pa.record_batch(
        {
            "u8": pa.array([0, 1, 255], type=pa.uint8()),
            "u16": pa.array([0, 1, 65535], type=pa.uint16()),
            "u32": pa.array([0, 1, 4294967295], type=pa.uint32()),
            "u64": pa.array([0, 1, 18446744073709551615], type=pa.uint64()),
        }
    )


@pytest.fixture
def float_batch() -> pa.RecordBatch:
    """RecordBatch with float types."""
    return pa.record_batch(
        {
            "f32": pa.array([1.5, -2.5, 0.0], type=pa.float32()),
            "f64": pa.array([1.5, -2.5, 0.0], type=pa.float64()),
        }
    )


@pytest.fixture
def bool_batch() -> pa.RecordBatch:
    """RecordBatch with boolean type."""
    return pa.record_batch(
        {
            "flag": pa.array([True, False, True]),
        }
    )


@pytest.fixture
def string_batch() -> pa.RecordBatch:
    """RecordBatch with utf8 string type."""
    return pa.record_batch(
        {
            "name": pa.array(["alice", "bob", "charlie"]),
        }
    )


@pytest.fixture
def mixed_batch() -> pa.RecordBatch:
    """RecordBatch with mixed primitive types."""
    return pa.record_batch(
        {
            "id": pa.array([1, 2, 3], type=pa.int64()),
            "name": pa.array(["alice", "bob", "charlie"]),
            "score": pa.array([9.5, 8.0, 7.5], type=pa.float64()),
            "active": pa.array([True, False, True]),
        }
    )


@pytest.fixture
def nullable_batch() -> pa.RecordBatch:
    """RecordBatch with null values in some columns."""
    return pa.record_batch(
        {
            "id": pa.array([1, 2, 3], type=pa.int64()),
            "name": pa.array(["alice", None, "charlie"]),
            "score": pa.array([9.5, None, 7.5], type=pa.float64()),
        }
    )


@pytest.fixture
def all_null_batch() -> pa.RecordBatch:
    """RecordBatch where nullable columns are entirely null."""
    return pa.record_batch(
        {
            "id": pa.array([1, 2], type=pa.int64()),
            "name": pa.array([None, None], type=pa.string()),
            "score": pa.array([None, None], type=pa.float64()),
        }
    )


# ---------------------------------------------------------------------------
# Phase 4: Temporal, dictionary, and null type fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def date32_batch() -> pa.RecordBatch:
    """RecordBatch with Date32 column including a null."""
    return pa.record_batch(
        {
            "event_date": pa.array(
                [datetime.date(2024, 1, 15), None, datetime.date(2020, 6, 30)],
                type=pa.date32(),
            ),
        }
    )


@pytest.fixture
def timestamp_us_batch() -> pa.RecordBatch:
    """RecordBatch with microsecond-precision naive timestamp."""
    return pa.record_batch(
        {
            "created_at": pa.array(
                [
                    datetime.datetime(2024, 1, 15, 10, 30, 0, 123456),
                    None,
                    datetime.datetime(2020, 6, 30, 23, 59, 59, 999999),
                ],
                type=pa.timestamp("us"),
            ),
        }
    )


@pytest.fixture
def timestamp_ns_batch() -> pa.RecordBatch:
    """Nanosecond timestamp for TEMP-05 truncation test."""
    return pa.record_batch(
        {
            "created_at": pa.array([1705312200123456789], type=pa.timestamp("ns")),
        }
    )


@pytest.fixture
def timestamp_tz_batch() -> pa.RecordBatch:
    """RecordBatch with timezone-aware timestamp (America/New_York)."""
    return pa.record_batch(
        {
            "created_at": pa.array(
                [datetime.datetime(2024, 1, 15, 10, 30, 0)],
                type=pa.timestamp("us", tz="America/New_York"),
            ),
        }
    )


@pytest.fixture
def duration_batch() -> pa.RecordBatch:
    """RecordBatch with microsecond-precision duration column."""
    return pa.record_batch(
        {
            "elapsed": pa.array([3600000000, None, 1000000], type=pa.duration("us")),
        }
    )


@pytest.fixture
def dict_string_batch() -> pa.RecordBatch:
    """RecordBatch with dictionary-encoded string column."""
    return pa.record_batch(
        {
            "category": pa.array(["a", "b", "a"]).dictionary_encode(),
        }
    )


@pytest.fixture
def dict_int_batch() -> pa.RecordBatch:
    """RecordBatch with dictionary-encoded int column."""
    indices = pa.array([0, 1, 0], type=pa.int8())
    dictionary = pa.array([100, 200], type=pa.int64())
    return pa.record_batch(
        {
            "code": pa.DictionaryArray.from_arrays(indices, dictionary),
        }
    )


# ---------------------------------------------------------------------------
# Phase 4 Plan 2: List, LargeList, and Struct type fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def list_int_batch() -> pa.RecordBatch:
    """RecordBatch with List(Int64) column."""
    return pa.record_batch(
        {
            "values": pa.array([[1, 2, 3], [4, 5], [6]], type=pa.list_(pa.int64())),
        }
    )


@pytest.fixture
def list_str_batch() -> pa.RecordBatch:
    """RecordBatch with List(Utf8) column."""
    return pa.record_batch(
        {
            "tags": pa.array([["a", "b"], ["c"]], type=pa.list_(pa.utf8())),
        }
    )


@pytest.fixture
def struct_batch() -> pa.RecordBatch:
    """RecordBatch with a name column and a Struct(city, zip_code) column."""
    return pa.record_batch(
        {
            "name": pa.array(["Alice", "Bob"]),
            "address": pa.StructArray.from_arrays(
                [
                    pa.array(["NYC", "LA"]),
                    pa.array([10001, 90001], type=pa.int32()),
                ],
                names=["city", "zip_code"],
            ),
        }
    )


# ---------------------------------------------------------------------------
# Phase 6: Extended type fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def float16_batch() -> pa.RecordBatch:
    """RecordBatch with Float16 column including a null."""
    return pa.record_batch(
        {
            "val": pa.array([1.5, None, -2.5], type=pa.float16()),
        }
    )


@pytest.fixture
def decimal128_batch() -> pa.RecordBatch:
    """RecordBatch with Decimal128 column including a null."""
    import decimal

    return pa.record_batch(
        {
            "amount": pa.array(
                [decimal.Decimal("12345.6789"), None, decimal.Decimal("-99999.9999")],
                type=pa.decimal128(10, 4),
            ),
        }
    )


@pytest.fixture
def decimal256_batch() -> pa.RecordBatch:
    """RecordBatch with Decimal256 column including a null."""
    import decimal

    return pa.record_batch(
        {
            "big_amount": pa.array(
                [
                    decimal.Decimal("123456789012345678901234567890.12345678"),
                    None,
                ],
                type=pa.decimal256(48, 8),
            ),
        }
    )


@pytest.fixture
def decimal32_batch() -> pa.RecordBatch:
    """RecordBatch with Decimal32 column including a null."""
    import decimal

    return pa.record_batch(
        {
            "amount": pa.array(
                [decimal.Decimal("123.45"), None, decimal.Decimal("-999.99")],
                type=pa.decimal32(7, 2),
            ),
        }
    )


@pytest.fixture
def decimal64_batch() -> pa.RecordBatch:
    """RecordBatch with Decimal64 column including a null."""
    import decimal

    return pa.record_batch(
        {
            "amount": pa.array(
                [decimal.Decimal("1234567.89"), None, decimal.Decimal("-9999999.99")],
                type=pa.decimal64(11, 2),
            ),
        }
    )


@pytest.fixture
def date64_batch() -> pa.RecordBatch:
    """RecordBatch with Date64 column including a null."""
    return pa.record_batch(
        {
            "ts": pa.array(
                [1705312200000, None, 0],  # 2024-01-15T09:50:00Z, null, epoch
                type=pa.date64(),
            ),
        }
    )


@pytest.fixture
def time32_second_batch() -> pa.RecordBatch:
    """RecordBatch with Time32(second) column."""
    return pa.record_batch(
        {
            "t": pa.array([37800, None, 0], type=pa.time32("s")),  # 10:30:00, null, 00:00:00
        }
    )


@pytest.fixture
def time32_ms_batch() -> pa.RecordBatch:
    """RecordBatch with Time32(millisecond) column."""
    return pa.record_batch(
        {
            "t": pa.array(
                [37800500, None, 0], type=pa.time32("ms")
            ),  # 10:30:00.500, null, 00:00:00
        }
    )


@pytest.fixture
def time64_us_batch() -> pa.RecordBatch:
    """RecordBatch with Time64(microsecond) column."""
    return pa.record_batch(
        {
            "t": pa.array([37800500123, None, 0], type=pa.time64("us")),  # 10:30:00.500123
        }
    )


@pytest.fixture
def time64_ns_batch() -> pa.RecordBatch:
    """RecordBatch with Time64(nanosecond) column."""
    return pa.record_batch(
        {
            "t": pa.array(
                [37800500123456, None, 0], type=pa.time64("ns")
            ),  # 10:30:00.500123456 -> truncate to .500123
        }
    )


@pytest.fixture
def binary_batch() -> pa.RecordBatch:
    """RecordBatch with Binary column including a null."""
    return pa.record_batch(
        {
            "data": pa.array([b"\x00\x01\x02", None, b"\xff"], type=pa.binary()),
        }
    )


@pytest.fixture
def large_binary_batch() -> pa.RecordBatch:
    """RecordBatch with LargeBinary column including a null."""
    return pa.record_batch(
        {
            "data": pa.array([b"hello", None, b"world"], type=pa.large_binary()),
        }
    )


@pytest.fixture
def fixed_size_binary_batch() -> pa.RecordBatch:
    """RecordBatch with FixedSizeBinary(4) column including a null."""
    return pa.record_batch(
        {
            "hash": pa.array(
                [b"\x01\x02\x03\x04", None, b"\xaa\xbb\xcc\xdd"],
                type=pa.binary(4),
            ),
        }
    )


@pytest.fixture
def utf8view_batch() -> pa.RecordBatch:
    """
    RecordBatch with Utf8View column including a null.

    Uses strings >12 bytes to avoid pyarrow C Data Interface segfault
    with inline StringView values (upstream pyarrow bug).
    """
    arr = pa.array(["hello_world_test", None, "world_hello_test"]).cast(pa.string_view())
    return pa.record_batch({"name": arr})


@pytest.fixture
def binaryview_batch() -> pa.RecordBatch:
    """
    RecordBatch with BinaryView column including a null.

    Uses values >12 bytes to avoid pyarrow C Data Interface segfault
    with inline BinaryView values (upstream pyarrow bug).
    """
    arr = pa.array([b"abc_data_padding!", None, b"xyz_data_padding!"]).cast(pa.binary_view())
    return pa.record_batch({"data": arr})


# ---------------------------------------------------------------------------
# Phase 6 Plan 02: Interval, container, REE, and union type fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def interval_mdn_batch() -> pa.RecordBatch:
    """IntervalMonthDayNano with varying components."""
    arr = pa.array(
        [
            (1, 2, 3000000000),  # 1 month, 2 days, 3 seconds in nanos
            None,
            (0, 0, 0),
        ],
        type=pa.month_day_nano_interval(),
    )
    return pa.record_batch({"interval": arr})


@pytest.fixture
def interval_ym_batch() -> pa.RecordBatch:
    """
    IntervalYearMonth with varying month values.

    pyarrow has no public constructor for IntervalYearMonth arrays.
    We build an int32 batch (the physical storage type for YearMonth)
    and re-import with the correct Arrow C format string ``tiM``.
    """
    src = pa.record_batch({"interval": pa.array([14, None, 0], type=pa.int32())})
    return _reinterpret_column(src, "interval", b"tiM")


@pytest.fixture
def interval_dt_batch() -> pa.RecordBatch:
    """
    IntervalDayTime with varying day/ms values.

    IntervalDayTime is physically stored as int64 where each value
    encodes ``(days, milliseconds)`` as a struct ``{i32, i32}``.
    On little-endian, the int64 representation is
    ``days | (ms << 32)`` (days in low 32 bits, ms in high 32 bits).

    Values:
      Row 0: 5 days, 3600000 ms (1 hour)
      Row 1: null
      Row 2: 0 days, 0 ms
    """
    val0 = 5 | (3600000 << 32)  # 5 days, 3600000 ms
    val2 = 0
    src = pa.record_batch({"interval": pa.array([val0, None, val2], type=pa.int64())})
    return _reinterpret_column(src, "interval", b"tiD")


@pytest.fixture
def fixed_size_list_batch() -> pa.RecordBatch:
    """RecordBatch with FixedSizeList(Int64, 3) column."""
    return pa.record_batch(
        {
            "values": pa.array(
                [[1, 2, 3], None, [4, 5, 6]],
                type=pa.list_(pa.int64(), 3),
            ),
        }
    )


@pytest.fixture
def map_batch() -> pa.RecordBatch:
    """RecordBatch with Map(Utf8, Int64) column."""
    return pa.record_batch(
        {
            "kv": pa.array(
                [[("a", 1), ("b", 2)], None, [("c", 3)]],
                type=pa.map_(pa.utf8(), pa.int64()),
            ),
        }
    )


@pytest.fixture
def ree_batch() -> pa.RecordBatch:
    """RunEndEncoded string column with 3 runs."""
    values = pa.array(["aaa", "bbb", "ccc"])
    run_ends = pa.array([2, 4, 5], type=pa.int32())
    ree_arr: pa.RunEndEncodedArray = pa.RunEndEncodedArray.from_arrays(run_ends, values)  # type: ignore[type-arg]
    return pa.record_batch({"name": ree_arr})


@pytest.fixture
def sparse_union_batch() -> pa.RecordBatch:
    """Sparse union with int and string children."""
    types = pa.array([0, 1, 0], type=pa.int8())
    children = [
        pa.array([1, 0, 2], type=pa.int64()),
        pa.array(["", "hello", ""], type=pa.utf8()),
    ]
    arr = pa.UnionArray.from_sparse(types, children)
    return pa.record_batch({"val": arr})


@pytest.fixture
def dense_union_batch() -> pa.RecordBatch:
    """Dense union with int and string children."""
    types = pa.array([0, 1, 0], type=pa.int8())
    offsets = pa.array([0, 0, 1], type=pa.int32())
    children = [
        pa.array([1, 2], type=pa.int64()),
        pa.array(["hello"], type=pa.utf8()),
    ]
    arr = pa.UnionArray.from_dense(types, offsets, children)
    return pa.record_batch({"val": arr})


# Under CI, a skipped test fails the run (tests/ci_skips.py).
pytest_plugins = ["tests.ci_skips"]
