"""Load event format v1 (schemas/events.md). The only module that knows the file layout."""

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUEST_FIELDS = (
    "request_id", "phase", "launch", "cell", "repetition", "prompt_id", "prompt_sha256",
    "is_canary", "is_anchor", "max_tokens", "min_output_tokens", "t_scheduled", "t_sent",
    "t_headers", "t_end", "http_status", "content_type", "client_outcome",
)
MANIFEST_FIELDS = ("mode", "planned_requests", "planned_launches", "aborted")


class EvidenceError(ValueError):
    """Required evidence is missing or unparsable."""


@dataclass
class Run:
    path: Path
    manifest: dict[str, Any]
    phases: list[dict[str, Any]]
    launches: list[dict[str, Any]]
    requests: list[dict[str, Any]]
    sse: dict[str, list[dict[str, Any]]]
    tokens: dict[str, list[dict[str, Any]]]


@dataclass
class Plan:
    data: dict[str, Any]
    canaries: dict[str, str] = field(default_factory=dict)
    reference: dict[str, dict[str, Any]] | None = None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvidenceError(f"{path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise EvidenceError(f"{path.name}: expected an object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    try:
        records = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except (OSError, ValueError) as exc:
        raise EvidenceError(f"{path.name}: {exc}") from exc
    if not all(isinstance(record, dict) for record in records):
        raise EvidenceError(f"{path.name}: every line must be an object")
    return records


def _require(records: list[dict[str, Any]], fields: tuple[str, ...], name: str) -> None:
    for record in records:
        missing = [key for key in fields if key not in record]
        if missing:
            raise EvidenceError(f"{name}: missing {missing}")


def _by_request(records: list[dict[str, Any]], order: str) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[record["request_id"]].append(record)
    for items in grouped.values():
        items.sort(key=lambda item: item[order])
    return dict(grouped)


def load_run(path: Path) -> Run:
    """Load one run directory. metrics.jsonl is diagnostic, so it's never read here."""
    manifest = _read_json(path / "manifest.json")
    _require([manifest], MANIFEST_FIELDS, "manifest.json")
    requests = _read_jsonl(path / "requests.jsonl")
    _require(requests, REQUEST_FIELDS, "requests.jsonl")
    sse = _read_jsonl(path / "sse.jsonl")
    _require(sse, ("request_id", "seq", "t", "data"), "sse.jsonl")
    tokens = _read_jsonl(path / "tokens.jsonl")
    _require(tokens, ("request_id", "index", "t", "channel"), "tokens.jsonl")
    phases = _read_jsonl(path / "phases.jsonl")
    _require(phases, ("phase", "status"), "phases.jsonl")
    launches = _read_jsonl(path / "launches.jsonl")
    _require(launches, ("launch", "event", "t"), "launches.jsonl")
    return Run(
        path, manifest, phases, launches, requests,
        _by_request(sse, "seq"), _by_request(tokens, "index"),
    )


def load_plan(workload: Path) -> Plan:
    """Load the plan plus any canary answers and reference it names, if those files exist."""
    data = _read_json(workload)
    base = workload.parent
    plan = Plan(data)
    canary = data["prompt_sets"][data["canary"]["prompt_set"]]["path"]
    if (base / canary).is_file():
        plan.canaries = {
            entry["prompt_id"]: entry["expected"] for entry in _read_jsonl(base / canary)
        }
    reference = data["reference"]["path"]
    if reference and (base / reference).is_file():
        plan.reference = {
            entry["prompt_sha256"]: entry for entry in _read_jsonl(base / reference)
        }
    return plan
