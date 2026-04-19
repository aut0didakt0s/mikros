"""Unit tests for megalos_panel.record JSON-lines writer/reader.

Exercises the load-bearing contract: first line is literal
{"schema_version": "1"}, subsequent lines are one JSON record each, and
RecordReader refuses files that violate the schema-version marker. Also
exercises the runs_dir() env-var override and the single-writer-per-run
documented contract.
"""

import json
import os
from pathlib import Path

import pytest

from megalos_panel.config import runs_dir
from megalos_panel.record import (
    SCHEMA_VERSION,
    RecordFormatError,
    RecordReader,
    RecordWriter,
)


def test_writer_emits_schema_version_on_line_1(tmp_path: Path) -> None:
    with RecordWriter(tmp_path) as w:
        path = w.path
    assert path is not None
    assert path.exists()
    with path.open("r", encoding="utf-8") as fh:
        first = fh.readline()
    assert json.loads(first) == {"schema_version": "1"}


def test_schema_version_constant_is_string_one() -> None:
    # The literal value is part of the contract — guards against a silent
    # bump that would break S04 gate check #2.
    assert SCHEMA_VERSION == "1"


def test_writer_subsequent_lines_are_individual_json_records(tmp_path: Path) -> None:
    records = [
        {"request_id": "a", "model": "m", "selection": "A"},
        {"request_id": "b", "model": "m", "selection": "B"},
    ]
    with RecordWriter(tmp_path) as w:
        for r in records:
            w.write(r)
        path = w.path
    assert path is not None
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 + len(records)
    assert json.loads(lines[1]) == records[0]
    assert json.loads(lines[2]) == records[1]


def test_write_read_roundtrip(tmp_path: Path) -> None:
    records = [
        {
            "request_id": "r1",
            "model": "claude-opus-4-7",
            "prompt": "hello",
            "selection": "A",
            "raw_response": "raw-A",
            "error": None,
            "attempts": 1,
            "elapsed_ms": 123,
            "timestamp": "2026-04-19T12:00:00Z",
        },
        {
            "request_id": "r2",
            "model": "gpt-5",
            "prompt": "world",
            "selection": "B",
            "raw_response": "raw-B",
            "error": None,
            "attempts": 2,
            "elapsed_ms": 456,
            "timestamp": "2026-04-19T12:00:01Z",
        },
    ]
    with RecordWriter(tmp_path) as w:
        for r in records:
            w.write(r)
        path = w.path
    assert path is not None
    read_back = list(RecordReader(path))
    assert read_back == records


def test_reader_raises_on_empty_file(tmp_path: Path) -> None:
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(RecordFormatError):
        list(RecordReader(path))


def test_reader_raises_on_non_json_first_line(tmp_path: Path) -> None:
    path = tmp_path / "bad.jsonl"
    path.write_text("not-json\n{}\n", encoding="utf-8")
    with pytest.raises(RecordFormatError):
        list(RecordReader(path))


def test_reader_raises_on_wrong_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "v2.jsonl"
    path.write_text(
        json.dumps({"schema_version": "2"}) + "\n" + json.dumps({"x": 1}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(RecordFormatError):
        list(RecordReader(path))


def test_reader_raises_on_missing_schema_version_key(tmp_path: Path) -> None:
    path = tmp_path / "no-key.jsonl"
    path.write_text(json.dumps({"other": "field"}) + "\n", encoding="utf-8")
    with pytest.raises(RecordFormatError):
        list(RecordReader(path))


def test_writer_write_outside_context_raises(tmp_path: Path) -> None:
    w = RecordWriter(tmp_path)
    with pytest.raises(RuntimeError):
        w.write({"x": 1})


def test_writer_creates_missing_run_dir(tmp_path: Path) -> None:
    nested = tmp_path / "nested" / "run"
    assert not nested.exists()
    with RecordWriter(nested) as w:
        w.write({"x": 1})
    assert nested.is_dir()


def test_writer_single_thread_sequential_writes(tmp_path: Path) -> None:
    # Single-writer-per-run contract: sequential writes from one thread
    # land as N+1 well-formed lines (schema header + N records).
    records = [{"i": i} for i in range(10)]
    with RecordWriter(tmp_path) as w:
        for r in records:
            w.write(r)
        path = w.path
    assert path is not None
    read_back = list(RecordReader(path))
    assert read_back == records


def test_runs_dir_default() -> None:
    # Preserve any active override, then clear it to observe the default.
    saved = os.environ.pop("MEGALOS_PANEL_RUNS_DIR", None)
    try:
        assert runs_dir() == Path("./runs/")
    finally:
        if saved is not None:
            os.environ["MEGALOS_PANEL_RUNS_DIR"] = saved


def test_runs_dir_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEGALOS_PANEL_RUNS_DIR", str(tmp_path / "custom"))
    assert runs_dir() == Path(str(tmp_path / "custom"))
