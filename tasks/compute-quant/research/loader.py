"""Load the case JSON and its CSV evidence files, recording SHA-256 digests."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .schema import (
    Case,
    InputError,
    PerformanceRow,
    Quote,
    case_from_mapping,
    performance_from_row,
    quote_from_row,
)


@dataclass(frozen=True)
class InputFile:
    role: str
    path: str
    sha256: str
    bytes: int


@dataclass(frozen=True)
class LoadedInputs:
    case: Case
    quotes: tuple[Quote, ...]
    performance: tuple[PerformanceRow, ...]
    files: tuple[InputFile, ...]
    quote_columns: tuple[str, ...]

    @property
    def input_pack_digest(self) -> str:
        """Candidate-defined pack digest: SHA-256 over sorted ``"<role>:<sha256>\\n"`` lines.

        The organizer has not published a digest definition; this one is deterministic and
        documented in the run manifest so it can be replaced when the real one exists.
        """
        lines = sorted(f"{item.role}:{item.sha256}\n" for item in self.files)
        return hashlib.sha256("".join(lines).encode("ascii")).hexdigest()


def sha256_file(path: Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_csv(path: Path) -> tuple[list[dict[str, str]], tuple[str, ...]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise InputError(f"{path}: empty CSV")
        rows = [dict(row) for row in reader]
    for row in rows:
        if None in row:
            raise InputError(f"{path}: a row has more cells than the header")
    return rows, tuple(reader.fieldnames)


def load_inputs(case_path: Path) -> LoadedInputs:
    """Load and validate the case and every file it references (relative to the case)."""
    case_path = Path(case_path)
    try:
        raw = json.loads(case_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(f"{case_path}: invalid JSON ({exc})") from exc
    if not isinstance(raw, dict):
        raise InputError(f"{case_path}: case must be a JSON object")
    case = case_from_mapping(raw)
    files = [
        InputFile("case", case_path.as_posix(), sha256_file(case_path), case_path.stat().st_size)
    ]
    quotes_path = case_path.parent / case.quotes_path
    if not quotes_path.exists():
        raise InputError(f"quotes file {quotes_path} referenced by the case does not exist")
    rows, columns = _read_csv(quotes_path)
    if not rows:
        raise InputError(f"{quotes_path}: no quote rows (header only); nothing to price")
    quotes = tuple(quote_from_row(row) for row in rows)
    seen: set[str] = set()
    duplicates: set[str] = set()
    for quote in quotes:
        (duplicates if quote.id in seen else seen).add(quote.id)
    if duplicates:
        raise InputError(f"duplicate quote ids {sorted(duplicates)}")
    files.append(
        InputFile(
            "quotes", quotes_path.as_posix(), sha256_file(quotes_path), quotes_path.stat().st_size
        )
    )
    performance: tuple[PerformanceRow, ...] = ()
    if case.performance_path is not None:
        perf_path = case_path.parent / case.performance_path
        if not perf_path.exists():
            raise InputError(f"performance file {perf_path} referenced by the case does not exist")
        perf_rows, _ = _read_csv(perf_path)
        performance = tuple(performance_from_row(row) for row in perf_rows)
        files.append(
            InputFile(
                "performance",
                perf_path.as_posix(),
                sha256_file(perf_path),
                perf_path.stat().st_size,
            )
        )
    return LoadedInputs(case, quotes, performance, tuple(files), columns)
