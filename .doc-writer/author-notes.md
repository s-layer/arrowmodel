# Author Notes for Editor (1.1.0 stream-input refresh, 2026-10-21)

RST pages cannot carry HTML-comment notes, so notes for this pass live here.

## docs/how-to/iterate-large-datasets.rst (refresh, how-to)
- Rewrote "Stream from a RecordBatchReader"; updated meta, intro, "How batch-at-a-time works", note; added "Handle input errors" (mirrors "Handle validation errors" in use-validated-mode).
- Sources: src/arrowmodel/__init__.py (ArrowModelConverter.iter), tests/test_stream_input.py (test_iter_reads_one_batch_at_a_time, test_reader_is_consumed, test_missing_column_raises_before_reading_batches, test_dataframe_can_be_converted_twice).
- Verified against the build: reader example output, TypeError message for pa.array, closed-cursor read raises ValueError("Failed to read the next Arrow record batch: ...").
- ADBC fetch_record_batch() is not covered by tests; it returns a pyarrow RecordBatchReader (tested). Ran end to end with adbc-driver-sqlite.

## docs/how-to/integrate-fastapi.rst (refresh, how-to)
- Added reader-based StreamingResponse variant with connection/cursor opened inside the generator, plus a warning (naive variant fails; errors arrive after 200).
- Verified with FastAPI TestClient + adbc-driver-sqlite. KNOWN ISSUE: see report — ArrowInput is #[pyclass(unsendable)] (rust/src/stream.rs:57); Starlette advances sync generators on threadpool workers, so concurrent streaming requests panic. Affects both streaming examples on this page.

## docs/how-to/use-with-poldantic.rst (refresh)
- Folded the old tip into prose (re-convertible DataFrame) and added one warning on LazyFrame/Series -> TypeError, with .collect() example (verified).

## docs/reference/api.rst (refresh)
- data: ArrowData in six signatures/param rows; TypeError (unsupported input) in six Raises lists; ValueError "raised from the schema before any batch is read"; generator note on the three iter entries; reader example under ArrowModelConverter.convert; new "Input types" section (ArrowData, ArrowArrayExportable, ArrowStreamExportable only — _ArrowSchemaExportable is private).
- Links verified in built HTML: :py:data:`~arrowmodel.ArrowData`, :py:class:`~arrowmodel.ArrowArrayExportable`, :py:class:`~arrowmodel.ArrowStreamExportable`.
- StructArray / struct ChunkedArray listed per tests/test_stream_input.py::TestStructArrays.

## docs/index.rst, docs/tutorials/getting-started.rst, README.md
- Card text + :link: to how-to-iterate-large-datasets; tutorial "What you learned" + Next steps link; README Input Compatibility + Lazy iteration bullet (benchmark lines untouched).
