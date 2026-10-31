"""Synthetic evidence builders shared by the tests (no network, fully deterministic)."""

import json
import sys
from pathlib import Path
from typing import Any

SUITE = Path(__file__).resolve().parent.parent
if str(SUITE) not in sys.path:
    sys.path.insert(0, str(SUITE))

import bench  # noqa: E402,F401  (puts the repo root on sys.path)
from bench.plan import load_plan, plan_digest  # noqa: E402
from bench.prompts import make_prompt  # noqa: E402
from bench.records import EVENT_SCHEMA_VERSION  # noqa: E402
from bench.tokens import TIMING_RESOLUTION, TOKENIZER_ID, count_tokens  # noqa: E402

PLAN_PATH = SUITE / "workload.json"
MOCK_PLAN_PATH = SUITE / "fixtures" / "workload.mock.json"


def plan() -> dict[str, Any]:
    return load_plan(MOCK_PLAN_PATH)


def chunk(
    text: str | None = None,
    *,
    finish: str | None = None,
    reasoning: str | None = None,
    usage: dict[str, int] | None = None,
) -> str:
    delta: dict[str, Any] = {}
    if text is not None:
        delta["content"] = text
    if reasoning is not None:
        delta["reasoning_content"] = reasoning
    payload: dict[str, Any] = {
        "id": "chatcmpl-x",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "m",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    if usage is not None:
        payload["choices"] = []
        payload["usage"] = usage
    return json.dumps(payload, ensure_ascii=False)


def make_record(
    the_plan: dict[str, Any],
    *,
    request_id: str = "chat_short_open|rate=4rps|rep0/measure/0",
    cell_id: str = "chat_short_open",
    load_label: str = "rate=4rps",
    index: int = 0,
    send: float = 100.0,
    ttft: float = 0.05,
    gap: float = 0.01,
    deltas: list[str] | None = None,
    gaps: list[float] | None = None,
    text: str | None = None,
    finish: str | None = "stop",
    done: bool = True,
    usage_tokens: int | None = None,
    http_status: int = 200,
    error: dict[str, str] | None = None,
    planned_send: float | None = None,
    phase: str = "measure",
    role: str = "measure",
    reasoning: list[str] | None = None,
) -> dict[str, Any]:
    """Build a schema-conformant request record whose answer is correct unless ``text`` is set."""
    spec = make_prompt(the_plan, "chat_short", cell_id + "/measure", 0, index)
    answer = (
        text if text is not None else f"{spec.expected['nonce']}\n" + "tail latency matters " * 6
    )
    pieces = deltas if deltas is not None else answer.split(" ")
    if deltas is None:
        pieces = [p + (" " if i < len(pieces) - 1 else "") for i, p in enumerate(pieces)]
    events: list[dict[str, Any]] = []
    t = send + ttft
    events.append({"t": t, "raw": chunk("")})
    for r in reasoning or []:
        t += gap
        events.append({"t": t, "raw": chunk(None, reasoning=r)})
    for i, piece in enumerate(pieces):
        t += gaps[i] if gaps is not None else gap
        events.append({"t": t, "raw": chunk(piece)})
    t += gap
    events.append({"t": t, "raw": chunk("", finish=finish)})
    content = "".join(pieces)
    reasoning_text = "".join(reasoning or [])
    usage = None
    if usage_tokens is not None:
        usage = {
            "prompt_tokens": spec.approx_prompt_tokens,
            "completion_tokens": usage_tokens,
            "total_tokens": spec.approx_prompt_tokens + usage_tokens,
        }
        events.append({"t": t, "raw": chunk(None, usage=usage)})
    if done:
        events.append({"t": t, "raw": "[DONE]"})
    if http_status != 200:
        events = []
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "record_type": "request",
        "run_id": "run-test",
        "request_id": request_id,
        "unit_id": f"{cell_id}|{load_label}|rep0",
        "cell_id": cell_id,
        "repetition": 0,
        "load_label": load_label,
        "arrival_type": "poisson",
        "load": 4.0,
        "phase": phase,
        "role": role,
        "prompt": spec.to_json(),
        "request_body_sha256": "0" * 64,
        "timestamps_mono_s": {
            "planned_send": planned_send if planned_send is not None else send - 0.001,
            "send": send,
            "headers": send + 0.002,
            "first_byte": (send + ttft) if events else None,
            "end": t + 0.001,
        },
        "http_status": http_status,
        "events": events,
        "finish_reason": finish if events else None,
        "done_received": done and bool(events),
        "text": {
            "content": content if events else "",
            "reasoning_content": reasoning_text if events else "",
        },
        "server_usage": usage,
        "client_tokens": {
            "tokenizer": TOKENIZER_ID,
            "resolution": TIMING_RESOLUTION,
            "content": count_tokens(content) if events else 0,
            "reasoning_content": count_tokens(reasoning_text),
        },
        "error": error,
    }


def make_run(
    records: list[dict[str, Any]],
    *,
    lag_max: float = 0.002,
    status: str = "completed",
    launch: dict[str, Any] | None = None,
    planned: dict[str, int] | None = None,
) -> dict[str, Any]:
    units: dict[str, dict[str, Any]] = {}
    for r in records:
        if r["phase"] == "startup_probe":
            continue
        u = units.setdefault(
            r["unit_id"],
            {
                "unit_id": r["unit_id"],
                "cell_id": r["cell_id"],
                "repetition": 0,
                "load_label": r["load_label"],
                "arrival_type": r["arrival_type"],
                "load": r["load"],
                "planned_requests": 0,
                "planned_warmup": 0,
                "role": r["role"],
                "deadline_profile": "chat",
                "prompt_source": "chat_short",
            },
        )
        if r["phase"] == "measure":
            u["planned_requests"] += 1
    if planned:
        for uid, n in planned.items():
            units[uid]["planned_requests"] = n
    if launch is None and not any(r["phase"] == "startup_probe" for r in records):
        # A real probe record backs the claimed first valid generation (its own end timestamp).
        records.append(
            make_record(
                plan(),
                request_id="startup/0/probe/0",
                index=0,
                send=94.0,
                ttft=1.0,
                phase="startup_probe",
                role="startup",
            )
        )
    probe_ids = [r["request_id"] for r in records if r["phase"] == "startup_probe"]
    probe_ends = [r["timestamps_mono_s"]["end"] for r in records if r["phase"] == "startup_probe"]
    valid_at = min(probe_ends) if probe_ends else None
    sends = [r["timestamps_mono_s"]["send"] for r in records if r["phase"] == "measure"] or [100.0]
    ends = [r["timestamps_mono_s"]["end"] for r in records if r["phase"] == "measure"] or [101.0]
    startup = (
        [launch]
        if launch is not None
        else [
            {
                "launch_index": 0,
                "launch_mono_s": 90.0,
                "cold": True,
                "launch_command_sha256": "0" * 64,
                "readiness_probes": [
                    {"t": 91.0, "http_status": 503, "ok": False},
                    {"t": 92.0, "http_status": 200, "ok": True},
                ],
                "generation_probe_request_ids": probe_ids,
                "readiness_at": 92.0,
                "first_valid_generation_at": valid_at,
                "first_valid_request_id": probe_ids[0] if probe_ids else None,
                "status": "valid",
            }
        ]
    )
    return {
        "schema_version": EVENT_SCHEMA_VERSION,
        "record_type": "run",
        "run_id": "run-test",
        "created_utc": "2026-01-01T00:00:00+00:00",
        "suite_version": "1.0.0",
        "mode": "fixture",
        "status": status,
        "plan": {
            "plan_id": "abstract-t1-mock-cpu-v1",
            "digest": plan_digest(MOCK_PLAN_PATH),
            "schema_version": "1.0.0",
            "path": "x",
        },
        "engine": {
            "base_url": "http://127.0.0.1:1",
            "model_id": "m",
            "lock_digest": "1" * 64,
            "lock_image": "img",
            "server_header": None,
            "kind": "mock",
        },
        "host": {"platform": "test", "python": "3.11", "cpu_count": 1, "hostname_sha256": "2" * 64},
        "clock": {
            "source": "time.monotonic",
            "wall_utc_at_mono_zero_reference": {"mono_s": 0.0, "utc": "x"},
        },
        "gpu": {"count_allocated": 0, "hours_allocated": 0.0, "kind": "none (CPU)"},
        "phases": [
            {"name": "measure", "unit_id": u, "start": min(sends) - 0.01, "end": max(ends) + 0.01}
            for u in units
        ],
        "startup": startup,
        "client_integrity": {
            "event_loop_lag_max_s": lag_max,
            "event_loop_lag_p99_s": lag_max,
            "samples": 100,
            "measured_phases": list(units),
        },
        "units": list(units.values()),
        "adapter": {"name": "native", "version": EVENT_SCHEMA_VERSION},
        "time_scale": 1.0,
        "planned_startup_launches": 1,
    }


def write_events(directory: Path, run: dict[str, Any], records: list[dict[str, Any]]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "run.json").write_text(json.dumps(run), encoding="utf-8")
    with (directory / "requests.jsonl").open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return directory


def clean_cell(
    the_plan: dict[str, Any],
    cell_id: str,
    n: int,
    *,
    ttft: float = 0.05,
    gap: float = 0.01,
    role: str = "measure",
    seed_shift: float = 0.0,
) -> list[dict[str, Any]]:
    """``n`` valid requests in one cell with slightly varying TTFT so bootstrap has spread."""
    return [
        make_record(
            the_plan,
            request_id=f"{cell_id}|rate=4rps|rep0/measure/{i}",
            cell_id=cell_id,
            index=i,
            send=100.0 + i,
            ttft=ttft + 0.001 * ((i * 7) % 5) + seed_shift,
            gap=gap,
            role=role,
        )
        for i in range(n)
    ]
