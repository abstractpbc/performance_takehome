"""Raw-event records: schema validation, loading, and the organizer-schema adapter point.

The native schema lives in ``suite/schema/*.schema.json``. A dependency-free subset
JSON-Schema validator (``validate``) enforces it so fixtures and live captures are checked
with the same document the organizer will read. ``load_events`` is the single adapter
seam: ``evaluate.py`` only ever sees ``RunRecord``/``RequestRecord``; a future organizer
schema is supported by adding an adapter that maps its files onto these two types.
"""

import gzip
import json
import math
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"
EVENT_SCHEMA_VERSION = "1.0.0"
RUN_FILE = "run.json"
REQUESTS_FILE = "requests.jsonl"
ADAPTERS = ("native",)
MAX_EVIDENCE_BYTES = 256 * 1024 * 1024  # decompressed bytes per requests file
MAX_RECORD_BYTES = 16 * 1024 * 1024  # one request line
MAX_RUN_BYTES = 16 * 1024 * 1024


class SchemaError(ValueError):
    """Raised when a record does not conform to the declared schema."""


class EvidenceError(ValueError):
    """Raised when an events directory is unreadable or structurally incomplete."""


def _load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _type_ok(value: Any, kind: str) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and (not isinstance(value, float) or math.isfinite(value))
        )
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "null":
        return value is None
    raise SchemaError(f"unsupported schema type {kind!r}")


def validate(instance: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate ``instance`` against a subset of JSON Schema 2020-12; return error strings.

    Supported keywords: type, const, enum, required, properties, items, minimum,
    minLength. Unknown keywords are ignored (documented limitation).
    """
    errors: list[str] = []
    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected const {schema['const']!r}, got {instance!r}")
    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: {instance!r} not in {schema['enum']!r}")
    if "type" in schema:
        kinds = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(instance, kind) for kind in kinds):
            errors.append(f"{path}: expected type {kinds}, got {type(instance).__name__}")
            return errors
    if isinstance(instance, dict):
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: missing required key {key!r}")
        for key, sub in schema.get("properties", {}).items():
            if key in instance:
                errors.extend(validate(instance[key], sub, f"{path}.{key}"))
    if isinstance(instance, list) and "items" in schema:
        for index, item in enumerate(instance):
            errors.extend(validate(item, schema["items"], f"{path}[{index}]"))
    if isinstance(instance, int | float) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} below minimum {schema['minimum']}")
    if isinstance(instance, str) and "minLength" in schema and len(instance) < schema["minLength"]:
        errors.append(f"{path}: string shorter than {schema['minLength']}")
    return errors


def validate_request_record(record: dict[str, Any]) -> list[str]:
    """Schema errors for one request record (empty list means valid)."""
    return validate(record, _load_schema("request.schema.json"))


def validate_run_record(record: dict[str, Any]) -> list[str]:
    """Schema errors for the run record (empty list means valid)."""
    return validate(record, _load_schema("run.schema.json"))


@dataclass(frozen=True)
class RunRecord:
    """Adapter-neutral view of ``run.json``."""

    raw: dict[str, Any]

    @property
    def run_id(self) -> str:
        return str(self.raw["run_id"])

    @property
    def mode(self) -> str:
        return str(self.raw["mode"])

    @property
    def units(self) -> list[dict[str, Any]]:
        return list(self.raw["units"])

    @property
    def startup(self) -> list[dict[str, Any]]:
        return list(self.raw["startup"])

    @property
    def client_integrity(self) -> dict[str, Any]:
        return dict(self.raw["client_integrity"])

    @property
    def phases(self) -> list[dict[str, Any]]:
        return list(self.raw["phases"])


@dataclass(frozen=True)
class RequestRecord:
    """Adapter-neutral view of one line of ``requests.jsonl``."""

    raw: dict[str, Any]

    @property
    def request_id(self) -> str:
        return str(self.raw.get("request_id", "<missing-request_id>"))

    @property
    def unit_id(self) -> str:
        return str(self.raw["unit_id"])

    @property
    def cell_id(self) -> str:
        return str(self.raw["cell_id"])

    @property
    def phase(self) -> str:
        return str(self.raw["phase"])

    @property
    def role(self) -> str:
        return str(self.raw["role"])

    @property
    def timestamps(self) -> dict[str, float | None]:
        return dict(self.raw["timestamps_mono_s"])

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self.raw["events"])

    @property
    def expected(self) -> dict[str, Any]:
        return dict(self.raw["prompt"].get("expected", {}))


@dataclass(frozen=True)
class Evidence:
    """Everything ``evaluate.py`` is allowed to see: one run record and its requests."""

    directory: Path
    run: RunRecord
    requests: list[RequestRecord]
    schema_errors: list[str]
    adapter: str


def _open_binary(path: Path) -> Any:
    if path.suffix == ".gz":
        return gzip.open(path, "rb")
    return path.open("rb")


def _strict_json(text: str) -> Any:
    """Reject non-JSON numbers, including finite-looking exponents that overflow float."""
    def reject_constant(value: str) -> Any:
        raise ValueError(f"non-finite JSON number {value}")

    def finite_float(value: str) -> float:
        result = float(value)
        if not math.isfinite(result):
            raise ValueError(f"non-finite JSON number {value}")
        return result

    return json.loads(text, parse_constant=reject_constant, parse_float=finite_float)


def _iter_bounded_lines(path: Path, chunk_bytes: int = 1 << 20) -> Iterator[tuple[int, bytes]]:
    """Yield (line_number, line) reading fixed-size chunks; never buffers more than one record.

    The bounds are enforced on the *decompressed* byte stream before a line is materialised, so
    a single-line gzip bomb aborts after ``MAX_RECORD_BYTES`` + one chunk, not after inflating.
    """
    total = 0
    number = 0
    buffer = bytearray()
    try:
        with _open_binary(path) as handle:
            while True:
                chunk = handle.read(chunk_bytes)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_EVIDENCE_BYTES:
                    raise EvidenceError(
                        f"{path}: evidence exceeds {MAX_EVIDENCE_BYTES} decompressed bytes"
                    )
                buffer += chunk
                while True:
                    cut = buffer.find(b"\n")
                    if cut < 0:
                        break
                    number += 1
                    line = bytes(buffer[:cut])
                    del buffer[: cut + 1]
                    if len(line) > MAX_RECORD_BYTES:
                        raise EvidenceError(
                            f"{path}:{number}: record exceeds {MAX_RECORD_BYTES} bytes"
                        )
                    yield number, line
                if len(buffer) > MAX_RECORD_BYTES:
                    raise EvidenceError(
                        f"{path}:{number + 1}: record exceeds {MAX_RECORD_BYTES} bytes"
                    )
    except (EOFError, gzip.BadGzipFile, OSError) as exc:
        raise EvidenceError(f"{path}: unreadable or corrupt evidence file ({exc})") from exc
    if buffer:
        yield number + 1, bytes(buffer)


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Stream records with hard byte bounds so a corrupt or hostile file fails fast, not OOM."""
    for number, raw_line in _iter_bounded_lines(path):
        try:
            line = raw_line.decode("utf-8").strip()
        except UnicodeDecodeError as exc:
            raise EvidenceError(f"{path}:{number}: not valid UTF-8 ({exc})") from exc
        if not line:
            continue
        try:
            record = _strict_json(line)
        except ValueError as exc:
            raise EvidenceError(f"{path}:{number}: invalid JSON ({exc})") from exc
        if not isinstance(record, dict):
            raise EvidenceError(f"{path}:{number}: record must be an object")
        yield record


def load_events(directory: Path, adapter: str = "native") -> Evidence:
    """Load an events directory through the named adapter.

    Only ``run.json`` and ``requests.jsonl`` (plain or gzip) are read. Any other file in the dir
    (labels, oracles, notes) is ignored by construction, which is what makes the
    fixture-identity independence test meaningful.
    """
    directory = Path(directory)
    if adapter not in ADAPTERS:
        raise EvidenceError(f"unknown adapter {adapter!r}; available: {ADAPTERS}")
    run_path, req_path = directory / RUN_FILE, directory / REQUESTS_FILE
    if not req_path.is_file() and (directory / (REQUESTS_FILE + ".gz")).is_file():
        req_path = directory / (REQUESTS_FILE + ".gz")
    if not run_path.is_file():
        raise EvidenceError(f"missing {RUN_FILE} in {directory}")
    if not req_path.is_file():
        raise EvidenceError(f"missing {REQUESTS_FILE} in {directory}")
    try:
        with run_path.open("rb") as handle:
            encoded = handle.read(MAX_RUN_BYTES + 1)
        if len(encoded) > MAX_RUN_BYTES:
            raise EvidenceError(f"{run_path}: run metadata exceeds {MAX_RUN_BYTES} bytes")
        run_raw = _strict_json(encoded.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise EvidenceError(f"{run_path}: unreadable JSON ({exc})") from exc
    if not isinstance(run_raw, dict):
        raise EvidenceError(f"{run_path}: run metadata must be an object")
    errors = [f"run: {e}" for e in validate_run_record(run_raw)]
    requests: list[RequestRecord] = []
    seen: set[str] = set()
    for record in _iter_jsonl(req_path):
        record_errors = validate_request_record(record)
        rid = str(record.get("request_id", "?"))
        errors.extend(f"request {rid}: {e}" for e in record_errors)
        if rid in seen:
            errors.append(f"request {rid}: duplicate request_id")
        seen.add(rid)
        requests.append(RequestRecord(record))
    if run_raw.get("run_id") and any(r.raw.get("run_id") != run_raw["run_id"] for r in requests):
        errors.append("requests reference a different run_id than run.json")
    return Evidence(directory, RunRecord(run_raw), requests, errors, adapter)
