"""JSON-lines record writer/reader for panel measurement runs.

Each panel measurement run produces one JSON-lines file. The first line is
always a literal schema-version marker:

    {"schema_version": "1"}

followed by one JSON object per panel request. The per-request record
carries the following fields (not runtime-enforced — the writer accepts any
dict, and downstream consumers rely on these field names by convention):

    request_id   — stable correlation id (str)
    model        — provider-qualified model identifier (str)
    prompt       — the prompt sent to the provider (str)
    selection    — the selected text parsed from the response (str)
    raw_response — the full provider response text (str)
    error        — provider-exhaustion reason, or null on success (str | null)
    attempts     — number of provider call attempts made (int)
    elapsed_ms   — wall-clock elapsed time for the request (int)
    timestamp    — ISO-8601 request-completion timestamp (str)

RecordWriter is a context manager that creates a timestamped `.jsonl` file
under a given run directory, writes the schema-version line on entry, and
exposes `.write(record)` for subsequent records. The contract is
single-writer-per-run: one RecordWriter instance per file, used from a
single thread. Multi-threaded writes against one instance are NOT supported
and concurrent .write() calls may interleave partial JSON on disk.

RecordReader opens an existing `.jsonl` file, validates the schema-version
line, and yields subsequent records as parsed dicts. A missing, malformed,
or wrong-version first line raises RecordFormatError.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType
from typing import IO, Iterator


SCHEMA_VERSION = "1"
_SCHEMA_LINE_OBJ = {"schema_version": SCHEMA_VERSION}


class RecordFormatError(Exception):
    """Raised when a JSON-lines record file fails schema-version validation."""


class RecordWriter:
    """Context-managed writer for a single panel-run JSON-lines file.

    Single-writer-per-run contract: one instance per file, one thread. Do
    not share a RecordWriter across threads; concurrent .write() calls are
    not supported and may produce interleaved partial JSON on disk.
    """

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = Path(run_dir)
        self.path: Path | None = None
        self._fh: IO[str] | None = None

    def __enter__(self) -> "RecordWriter":
        self.run_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        self.path = self.run_dir / f"{timestamp}.jsonl"
        self._fh = self.path.open("w", encoding="utf-8")
        self._fh.write(json.dumps(_SCHEMA_LINE_OBJ) + "\n")
        self._fh.flush()
        return self

    def write(self, record: dict) -> None:
        if self._fh is None:
            raise RuntimeError("RecordWriter.write() called outside context manager")
        self._fh.write(json.dumps(record) + "\n")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._fh is not None:
            self._fh.flush()
            self._fh.close()
            self._fh = None


class RecordReader:
    """Iterator over records in a panel-run JSON-lines file.

    Validates the schema-version marker on line 1 at open time. Raises
    RecordFormatError if the file is empty, the first line is not valid
    JSON, or the first line does not equal {"schema_version": "1"}.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def __iter__(self) -> Iterator[dict]:
        with self.path.open("r", encoding="utf-8") as fh:
            first = fh.readline()
            if not first:
                raise RecordFormatError(
                    f"record file {self.path} is empty; expected schema_version line"
                )
            try:
                header = json.loads(first)
            except json.JSONDecodeError as e:
                raise RecordFormatError(
                    f"record file {self.path} line 1 is not valid JSON: {e}"
                ) from e
            if header != _SCHEMA_LINE_OBJ:
                raise RecordFormatError(
                    f"record file {self.path} line 1 schema mismatch: "
                    f"expected {_SCHEMA_LINE_OBJ!r}, got {header!r}"
                )
            for line in fh:
                if not line.strip():
                    continue
                yield json.loads(line)
