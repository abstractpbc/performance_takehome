"""Execute a workload plan against an OpenAI-compatible streaming server and record events.

Evidence rules (enforced here, checked again by ``evaluate.py``):

* every timestamp is ``time.monotonic()`` taken by this process; an unobserved time is
  ``None`` and is never back-filled;
* each SSE event's ``raw`` payload is stored verbatim (as yielded by the shared strict
  parser ``benchmarks.streaming.sse_data``) with the arrival time of the network chunk that
  completed it, so a token-level view can be rebuilt later;
* token counts come from ``bench.tokens`` (resolution ``delta``: all tokens in a delta get
  the delta's arrival time) and are stored next to, never instead of, server ``usage``;
* readiness probes are recorded but the startup clock stops only at the first *valid*
  completed generation.
"""

import argparse
import asyncio
import codecs
import gc
import hashlib
import json
import os
import platform
import shlex
import signal
import socket
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from bench import REPO_ROOT, SUITE_VERSION  # noqa: E402
from bench.arrivals import closed_loop_assignments, poisson_schedule  # noqa: E402
from bench.gates import judge  # noqa: E402
from bench.model_tokens import ModelTokenizer, TokenizerError  # noqa: E402
from bench.plan import (  # noqa: E402
    ASSISTANT_PREFILL_PROFILE,
    ScheduledCell,
    estimate_mode,
    fixed_request_options,
    load_plan,
    plan_digest,
    resolve_mode,
)
from bench.prompts import PromptSpec, make_prompt  # noqa: E402
from bench.records import (  # noqa: E402
    EVENT_SCHEMA_VERSION,
    MAX_EVIDENCE_BYTES,
    REQUESTS_FILE,
    RUN_FILE,
)
from bench.tokens import TIMING_RESOLUTION, TOKENIZER_ID, count_tokens  # noqa: E402

from benchmarks.streaming import (  # noqa: E402
    StreamProtocolError,
    decode_payload,
    generated_deltas,
    sse_data,
)

RUNNER_VERSION = "1.1.1"
ADAPTER = {"name": "native", "version": EVENT_SCHEMA_VERSION}
MAX_STREAM_BYTES = 2 << 20
MAX_STREAM_EVENTS = 8192


class CaptureLimitError(RuntimeError):
    """The immutable raw evidence reached its explicit storage/reader bound."""


@dataclass
class RequestMeta:
    """Static identity of one request inside the run."""

    request_id: str
    unit: ScheduledCell | None
    phase: str
    role: str
    index: int
    planned_send: float | None = None

    def unit_fields(self) -> dict[str, Any]:
        if self.unit is None:
            return {
                "unit_id": "startup",
                "cell_id": "startup",
                "repetition": 0,
                "load_label": "probe",
                "arrival_type": "probe",
                "load": 0,
            }
        return {
            "unit_id": self.unit.unit_id,
            "cell_id": self.unit.cell_id,
            "repetition": self.unit.repetition,
            "load_label": self.unit.load_label,
            "arrival_type": self.unit.arrival_type,
            "load": self.unit.load,
        }


class LagMonitor:
    """Samples event-loop lag so client stalls can be told apart from server stalls."""

    def __init__(self, interval_s: float = 0.01, spike_threshold_s: float = 0.02) -> None:
        self.interval_s = interval_s
        self.spike_threshold_s = spike_threshold_s
        self.samples: list[tuple[float, float, float]] = []
        self._task: asyncio.Task[None] | None = None

    async def _loop(self) -> None:
        while True:
            before = time.monotonic()
            await asyncio.sleep(self.interval_s)
            now = time.monotonic()
            self.samples.append((before, now, max(0.0, now - before - self.interval_s)))

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def summary(self, windows: list[tuple[str, float, float]]) -> dict[str, Any]:
        """Lag statistics restricted to the measurement windows ``(unit_id, start, end)``."""
        # The overdue part starts at the scheduled wakeup, not at sleep's start.
        # Clip it to the union of measurement windows: dropping boundary-crossing
        # samples hides stalls, while counting the full lag can blame pre-window work.
        merged: list[tuple[float, float]] = []
        for a, b in sorted((a, b) for _, a, b in windows if a < b):
            if merged and a <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], b))
            else:
                merged.append((a, b))
        inside = []
        for before, now, lag in self.samples:
            if any(before < b and a < now for a, b in merged):
                overlap = sum(
                    max(0.0, min(now, b) - max(before + self.interval_s, a)) for a, b in merged
                )
                # Never report more delay than was actually observed, including
                # floating-point rounding at the scheduled wakeup boundary.
                inside.append((now, min(lag, overlap)))
        result: dict[str, Any] = {
            "event_loop_lag_max_s": None,
            "event_loop_lag_p99_s": None,
            "samples": len(inside),
            "measured_phases": [unit_id for unit_id, _, _ in windows],
            "sample_interval_s": self.interval_s,
            "gc_disabled_in_measure_windows": True,
        }
        if not inside:
            return result
        lags = sorted(lag for _, lag in inside)
        max_t, max_lag = max(inside, key=lambda item: item[1])
        result.update(
            event_loop_lag_spikes=[
                {"t": t, "lag_s": lag} for t, lag in inside if lag > self.spike_threshold_s
            ][:200],
            event_loop_lag_max_s=max_lag,
            event_loop_lag_max_at_mono_s=max_t,
            event_loop_lag_p99_s=lags[min(len(lags) - 1, int(0.99 * (len(lags) - 1)))],
            event_loop_lag_mean_s=statistics.fmean(lags),
        )
        return result


def _repo_relative(path: Path) -> str:
    """Path relative to the repository root when inside it (keeps host paths out of evidence)."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return path.name


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _lock_info(plan: dict[str, Any]) -> dict[str, Any]:
    lock_path = REPO_ROOT / plan["target"]["engine_lock"]
    lock_bytes = lock_path.read_bytes()
    lock = json.loads(lock_bytes)
    role = plan["target"]["model_role"]
    model = (lock.get("models") or {}).get(role, {})
    return {
        "lock_digest": _sha256(lock_bytes),
        "lock_image": str(lock.get("image", "unknown")),
        "lock_model": f"{model.get('repository')}@{model.get('revision')}",
    }


def build_body(plan: dict[str, Any], spec: PromptSpec, model_id: str) -> dict[str, Any]:
    """Chat-completions request body from the plan's output policy and the prompt spec."""
    policy = plan["output_policy"]
    options = fixed_request_options(policy)
    messages = [dict(message) for message in spec.messages]
    if policy.get("request_profile") == ASSISTANT_PREFILL_PROFILE:
        messages.append({"role": "assistant", "content": "</think>\n\n"})
    body: dict[str, Any] = {
        "model": model_id,
        "messages": messages,
        "max_tokens": spec.max_tokens,
        "temperature": policy["temperature"],
        "stream": True,
        "stream_options": dict(policy["stream_options"]),
    }
    if policy.get("stop") is not None:
        body["stop"] = policy["stop"]
    body.update(options)
    return body


class Runner:
    """Executes one run (startup launches + scheduled units) and writes the evidence."""

    def __init__(self, args: argparse.Namespace, plan: dict[str, Any]) -> None:
        self.args = args
        self.plan = plan
        snapshot = getattr(args, "tokenizer_snapshot", None)
        self.tokenizer = (
            ModelTokenizer(Path(snapshot), plan["target"]["model_role"]) if snapshot else None
        )
        if not args.assume_mock and self.tokenizer is None:
            raise TokenizerError("real capture requires --tokenizer-snapshot; regex is mock-only")
        if self.tokenizer and self.tokenizer.model != _lock_info(plan)["lock_model"]:
            raise TokenizerError("selected tokenizer differs from the workload model pin")
        self.out = Path(args.out)
        self.base_url = args.base_url or plan["target"]["base_url"]
        self.model_id = args.model or "unknown"
        self.run_id = (
            args.run_id or f"run-{int(time.time())}-{_sha256(str(time.monotonic()).encode())[:6]}"
        )
        self.scale = args.time_scale
        self.lag = LagMonitor()
        self.phases: list[dict[str, Any]] = []
        self.startups: list[dict[str, Any]] = []
        self.units_meta: list[dict[str, Any]] = []
        self.measure_windows: list[tuple[str, float, float]] = []
        self.engine: dict[str, Any] = {}
        self.counter = 0
        self.launched: list[subprocess.Popen[bytes]] = []
        self.status = "in_progress"
        self.scheduled: list[ScheduledCell] = [
            unit
            for unit in resolve_mode(plan, args.mode)
            if not args.only_unit or args.only_unit in unit.unit_id
        ]
        self._requests_fh = None
        self._request_bytes = 0
        self.abort_reason: str | None = None

    def _write_request(self, record: dict[str, Any]) -> None:
        assert self._requests_fh is not None
        # Escapes also preserve surrogateescape bytes from invalid UTF-8 evidence;
        # writing them directly to a UTF-8 text stream would lose the entire record.
        encoded = json.dumps(record, ensure_ascii=True) + "\n"
        if self._request_bytes + len(encoded) > MAX_EVIDENCE_BYTES:
            raise CaptureLimitError("request evidence file byte cap reached; capture is incomplete")
        self._requests_fh.write(encoded)
        self._request_bytes += len(encoded)
        self._requests_fh.flush()

    async def execute(
        self, client: httpx.AsyncClient, spec: PromptSpec, meta: RequestMeta
    ) -> dict[str, Any]:
        """Send one streaming request and return its raw-event record."""
        body = build_body(self.plan, spec, self.model_id)
        body_bytes = json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
        events: list[dict[str, Any]] = []
        ts: dict[str, float | None] = {
            "planned_send": meta.planned_send,
            "send": None,
            "headers": None,
            "first_byte": None,
            "end": None,
        }
        http_status: int | None = None
        content_type: str | None = None
        error: dict[str, str] | None = None
        done_received = False
        finish_reason: str | None = None
        usage: dict[str, Any] | None = None
        content: list[str] = []
        reasoning: list[str] = []
        try:
            ts["send"] = time.monotonic()
            async with client.stream(
                "POST",
                self.base_url + self.plan["target"]["endpoint"],
                content=body_bytes,
                headers={"Content-Type": "application/json", "X-Bench-Request-Id": meta.request_id},
            ) as response:
                ts["headers"] = time.monotonic()
                http_status = response.status_code
                content_type = response.headers.get("content-type")
                if http_status != 200:
                    error = {"kind": "http_error", "message": f"HTTP {http_status}"}
                else:
                    pending_lines: list[str] = []
                    partial = ""
                    decoder = codecs.getincrementaldecoder("utf-8")(errors="surrogateescape")
                    stream_bytes = 0
                    async for chunk in response.aiter_bytes():
                        stream_bytes += len(chunk)
                        if stream_bytes > MAX_STREAM_BYTES:
                            raise StreamProtocolError("raw SSE capture byte cap exceeded")
                        now = time.monotonic()
                        if ts["first_byte"] is None:
                            ts["first_byte"] = now
                        partial += decoder.decode(chunk)
                        lines = partial.split("\n")
                        partial = lines.pop()
                        for line in lines:
                            pending_lines.append(line)
                            if not line.rstrip("\r"):
                                for payload in sse_data(pending_lines):
                                    if len(events) >= MAX_STREAM_EVENTS:
                                        raise StreamProtocolError(
                                            "raw SSE capture event cap exceeded"
                                        )
                                    events.append({"t": now, "raw": payload})
                                pending_lines = []
                    partial += decoder.decode(b"", final=True)
                    if pending_lines or partial:
                        now = time.monotonic()
                        for payload in sse_data([*pending_lines, partial]):
                            if len(events) >= MAX_STREAM_EVENTS:
                                raise StreamProtocolError("raw SSE capture event cap exceeded")
                            events.append({"t": now, "raw": payload, "eof_terminated": True})
        except httpx.TimeoutException as exc:
            error = {"kind": "timeout", "message": repr(exc)}
        except (httpx.RemoteProtocolError, httpx.ReadError) as exc:
            error = {"kind": "drop", "message": repr(exc)}
        except httpx.HTTPError as exc:
            error = {"kind": "transport", "message": repr(exc)}
        except StreamProtocolError as exc:
            # Parser limits apply both at blank-line boundaries and at EOF. Preserve
            # prior complete events and return a failed request instead of losing the run.
            error = {"kind": "protocol", "message": str(exc)}
        finally:
            ts["end"] = time.monotonic()
        for event in events:
            try:
                decoded = decode_payload(event["raw"])
            except (ValueError, RecursionError) as exc:
                # JSON's bounded integer conversion and nesting limits can fail
                # before the shared codec wraps a JSONDecodeError. Keep the raw
                # event and classify the server request without aborting the run.
                error = error or {"kind": "protocol", "message": str(exc)}
                continue
            if decoded is None:
                done_received = True
                continue
            if isinstance(decoded.get("usage"), dict):
                usage = decoded["usage"]
            try:
                for channel, text in generated_deltas(decoded):
                    (reasoning if channel == "reasoning_content" else content).append(text)
            except StreamProtocolError as exc:
                error = error or {"kind": "protocol", "message": str(exc)}
                continue
            choices = decoded.get("choices") or []
            if choices and isinstance(choices[0], dict) and choices[0].get("finish_reason"):
                finish_reason = str(choices[0]["finish_reason"])
        content_text, reasoning_text = "".join(content), "".join(reasoning)
        count_error = None
        try:
            content_count = (
                self.tokenizer.count(content_text) if self.tokenizer else count_tokens(content_text)
            )
            reasoning_count = (
                self.tokenizer.count(reasoning_text)
                if self.tokenizer
                else count_tokens(reasoning_text)
            )
        except TokenizerError as exc:
            # Preserve raw SSE/text for invalid-evidence analysis, never regex fallback.
            content_count = reasoning_count = 0
            count_error = str(exc)
        return {
            "schema_version": EVENT_SCHEMA_VERSION,
            "record_type": "request",
            "run_id": self.run_id,
            "request_id": meta.request_id,
            **meta.unit_fields(),
            "phase": meta.phase,
            "role": meta.role,
            "prompt": spec.to_json(),
            "request_body_sha256": _sha256(body_bytes),
            "timestamps_mono_s": ts,
            "http_status": http_status,
            "response_content_type": content_type,
            "events": events,
            "finish_reason": finish_reason,
            "done_received": done_received,
            "text": {"content": content_text, "reasoning_content": reasoning_text},
            "server_usage": usage,
            "client_tokens": {
                "tokenizer": self.tokenizer.identifier if self.tokenizer else TOKENIZER_ID,
                "resolution": TIMING_RESOLUTION,
                "content": content_count,
                "reasoning_content": reasoning_count,
                **({"count_error": count_error} if count_error else {}),
            },
            "error": error,
        }

    def _is_valid_generation(self, record: dict[str, Any]) -> bool:
        """A probe is valid only if it passes every evaluator gate (single source of truth)."""
        return judge(record, self.plan, [], self.tokenizer).category == "completed"

    @staticmethod
    def _stop_process(proc: subprocess.Popen[bytes]) -> None:
        """Stop only a foreground engine/session created by this runner, then reap it."""
        if proc.poll() is not None:
            return
        if os.name == "posix":
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            if os.name == "posix":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:
                proc.kill()
            proc.wait(timeout=5)

    async def startup(self, client: httpx.AsyncClient, launch_index: int) -> None:
        """Cold start (optional launch), poll readiness, probe until the first VALID generation."""
        cfg = self.plan["startup"]
        # Repetitions share a server address. Leaving the preceding engine alive can
        # turn a failed bind into apparently successful probes of the warm engine.
        for previous in self.launched:
            self._stop_process(previous)
        self.launched.clear()
        launch = {
            "launch_index": launch_index,
            "launch_mono_s": time.monotonic(),
            "cold": bool(self.args.launch_cmd),
            "launch_command_sha256": _sha256(self.args.launch_cmd.encode())
            if self.args.launch_cmd
            else None,
            "readiness_probes": [],
            "generation_probe_request_ids": [],
            "readiness_at": None,
            "first_valid_generation_at": None,
            "first_valid_request_id": None,
            "status": "in_progress",
        }
        proc: subprocess.Popen[bytes] | None = None
        readiness_url = self.base_url + self.plan["target"]["readiness_endpoint"]
        if self.args.launch_cmd:
            # Refuse a pre-existing responder even if a failed child has not exited
            # yet: fast probes could otherwise finish before its bind error appears.
            try:
                await client.get(readiness_url, timeout=min(1.0, cfg["max_wait_s"] * self.scale))
            except httpx.HTTPError:
                pass
            else:
                launch["status"] = "preexisting_server"
                self.startups.append(launch)
                return
            launch["launch_mono_s"] = time.monotonic()
            proc = subprocess.Popen(
                shlex.split(self.args.launch_cmd), start_new_session=os.name == "posix"
            )
            self.launched.append(proc)
        self.startups.append(launch)
        deadline = launch["launch_mono_s"] + cfg["max_wait_s"] * self.scale
        next_probe = launch["launch_mono_s"]
        probe_index = 0
        while time.monotonic() < deadline:
            if proc is not None and proc.poll() is not None:
                launch["status"] = "process_exited"
                break
            t = time.monotonic()
            status, ok = None, False
            try:
                resp = await client.get(readiness_url, timeout=5.0)
                status = resp.status_code
                if status == 200:
                    data = resp.json().get("data") or []
                    ok = bool(data)
                    if ok and not self.engine:
                        self.engine = {
                            "server_header": resp.headers.get("server"),
                            "kind": "mock"
                            if "x-mock-profile" in resp.headers or self.args.assume_mock
                            else "unknown",
                        }
                        if self.model_id == "unknown" and isinstance(data[0], dict):
                            self.model_id = str(data[0].get("id", "unknown"))
            except httpx.HTTPError as exc:
                status = None
                launch["readiness_probes"].append(
                    {"t": t, "http_status": None, "ok": False, "error": repr(exc)}
                )
            else:
                launch["readiness_probes"].append({"t": t, "http_status": status, "ok": ok})
            if ok and launch["readiness_at"] is None:
                launch["readiness_at"] = t
            if ok and time.monotonic() >= next_probe:
                if proc is not None and proc.poll() is not None:
                    launch["status"] = "process_exited"
                    break
                probe_index += 1
                spec = make_prompt(
                    self.plan, cfg["probe_prompt_source"], "startup", launch_index, probe_index
                )
                spec = PromptSpec(
                    spec.prompt_id,
                    spec.source,
                    spec.kind,
                    spec.messages,
                    min(spec.max_tokens, cfg["probe_max_tokens"]),
                    spec.approx_prompt_tokens,
                    {**spec.expected, "min_output_chars": 1},
                )
                meta = RequestMeta(
                    f"startup{launch_index}/probe/{probe_index}",
                    None,
                    "startup_probe",
                    "startup",
                    probe_index,
                )
                record = await self.execute(client, spec, meta)
                self._write_request(record)
                launch["generation_probe_request_ids"].append(meta.request_id)
                if proc is not None and proc.poll() is not None:
                    launch["status"] = "process_exited"
                    break
                if self._is_valid_generation(record):
                    launch["first_valid_generation_at"] = record["timestamps_mono_s"]["end"]
                    launch["first_valid_request_id"] = meta.request_id
                    launch["status"] = "valid"
                    break
                next_probe = time.monotonic() + cfg["probe_interval_s"] * self.scale
            await asyncio.sleep(cfg["readiness_poll_interval_s"] * self.scale)
        else:
            launch["status"] = "timeout"

    async def _run_unit(self, client: httpx.AsyncClient, unit: ScheduledCell) -> None:
        self.units_meta.append(
            {
                "unit_id": unit.unit_id,
                "cell_id": unit.cell_id,
                "repetition": unit.repetition,
                "load_label": unit.load_label,
                "arrival_type": unit.arrival_type,
                "load": unit.load,
                "planned_requests": unit.requests,
                "planned_warmup": unit.warmup,
                "role": unit.role,
                "deadline_profile": unit.deadline_profile,
                "prompt_source": unit.prompt_source,
            }
        )
        cache_state = await self._cache_flush(client)
        warm_start = time.monotonic()
        for index in range(unit.warmup):
            spec = self._prompt(unit, "warmup", index)
            record = await self.execute(
                client,
                spec,
                RequestMeta(f"{unit.unit_id}/warmup/{index}", unit, "warmup", unit.role, index),
            )
            self._write_request(record)
        self.phases.append(
            {
                "name": "warmup",
                "unit_id": unit.unit_id,
                "start": warm_start,
                "end": time.monotonic(),
                "cache_state": cache_state,
            }
        )
        # Prompts are deterministic, so they are built before the clock starts: generation
        # cost must never show up as client lag inside a measurement window.
        specs = [self._prompt(unit, "measure", index) for index in range(unit.requests)]
        # The cyclic GC is paused inside the window (collected before and after it) so
        # collector pauses cannot be mistaken for server stalls; recorded in run.json.
        gc.collect()
        gc.disable()
        start = time.monotonic()
        if unit.arrival_type == "poisson":
            schedule = poisson_schedule(unit.load / self.scale, unit.requests, unit.arrival_seed)

            async def fire(index: int, offset: float) -> None:
                planned = start + offset
                await asyncio.sleep(max(0.0, planned - time.monotonic()))
                spec = specs[index]
                meta = RequestMeta(
                    f"{unit.unit_id}/measure/{index}", unit, "measure", unit.role, index, planned
                )
                self._write_request(await self.execute(client, spec, meta))

            await asyncio.gather(*(fire(a.index, a.offset_s) for a in schedule))
        else:

            async def worker(indices: list[int]) -> None:
                for index in indices:
                    spec = specs[index]
                    meta = RequestMeta(
                        f"{unit.unit_id}/measure/{index}",
                        unit,
                        "measure",
                        unit.role,
                        index,
                        time.monotonic(),
                    )
                    self._write_request(await self.execute(client, spec, meta))

            await asyncio.gather(
                *(worker(ix) for ix in closed_loop_assignments(int(unit.load), unit.requests))
            )
        end = time.monotonic()
        gc.enable()
        gc.collect()
        self.phases.append({"name": "measure", "unit_id": unit.unit_id, "start": start, "end": end})
        self.measure_windows.append((unit.unit_id, start, end))

    def _prompt(self, unit: ScheduledCell, phase: str, index: int) -> PromptSpec:
        forced = None
        if unit.prompt_token_grid:
            forced = unit.prompt_token_grid[index % len(unit.prompt_token_grid)]
        return make_prompt(
            self.plan,
            unit.prompt_source,
            f"{unit.unit_id}/{phase}",
            unit.repetition,
            index,
            forced_prompt_tokens=forced,
        )

    async def _cache_flush(self, client: httpx.AsyncClient) -> str:
        if not self.args.cache_flush_url:
            return (
                "unique_leading_nonce_v1; shared chat-template cache not flushed"
                if self.plan.get("cache_state", {}).get("request") == "unique_leading_nonce_v1"
                else "uncontrolled_prefix_cache_no_flush"
            )
        try:
            resp = await client.post(self.args.cache_flush_url, timeout=30.0)
            return "flushed" if resp.status_code < 300 else f"failed:HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            return f"failed:{type(exc).__name__}"

    def run_record(self) -> dict[str, Any]:
        """Assemble ``run.json``."""
        mode = self.plan["modes"][self.args.mode]
        return {
            "schema_version": EVENT_SCHEMA_VERSION,
            "record_type": "run",
            "run_id": self.run_id,
            "created_utc": datetime.now(UTC).isoformat(),
            "suite_version": SUITE_VERSION,
            "runner_version": RUNNER_VERSION,
            "capture_limits": {
                "raw_sse_bytes_per_request": MAX_STREAM_BYTES,
                "sse_events_per_request": MAX_STREAM_EVENTS,
            },
            "mode": self.args.mode,
            "status": self.status,
            "abort_reason": self.abort_reason,
            "plan": {
                "plan_id": self.plan["plan_id"],
                "digest": plan_digest(Path(self.args.plan)),
                "schema_version": self.plan["schema_version"],
                "path": _repo_relative(Path(self.args.plan)),
            },
            "engine": {
                "base_url": self.base_url,
                "model_id": self.model_id,
                **_lock_info(self.plan),
                "server_header": self.engine.get("server_header"),
                "kind": self.engine.get("kind", "unknown"),
            },
            "tokenizer": self.tokenizer.identity if self.tokenizer else None,
            "host": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "cpu_count": __import__("os").cpu_count(),
                "hostname_sha256": _sha256(socket.gethostname().encode()),
            },
            "clock": {
                "source": "time.monotonic",
                "wall_utc_at_mono_zero_reference": {
                    "mono_s": time.monotonic(),
                    "utc": datetime.now(UTC).isoformat(),
                },
            },
            "gpu": {
                "count_allocated": self.args.gpu_count,
                "hours_allocated": self.args.gpu_hours,
                "kind": self.args.gpu_kind,
            },
            "phases": self.phases,
            "startup": self.startups,
            "client_integrity": self.lag.summary(self.measure_windows),
            "units": self.units_meta,
            "adapter": ADAPTER,
            "time_scale": self.scale,
            "planned_startup_launches": mode["startup_launches"],
        }

    async def run(self) -> None:
        """Execute the whole run; ``run.json`` is written even on abort."""
        self.out.mkdir(parents=True, exist_ok=True)
        if any(
            (self.out / name).exists() for name in (RUN_FILE, REQUESTS_FILE, REQUESTS_FILE + ".gz")
        ):
            raise FileExistsError("fresh capture output required; existing evidence is immutable")
        self._requests_fh = (self.out / REQUESTS_FILE).open("x", encoding="utf-8", newline="\n")
        self.lag.start()
        timeout = httpx.Timeout(
            connect=10.0, read=self.args.request_timeout_s, write=10.0, pool=None
        )
        try:
            async with httpx.AsyncClient(
                timeout=timeout, limits=httpx.Limits(max_connections=None)
            ) as client:
                launches = (
                    self.plan["modes"][self.args.mode]["startup_launches"]
                    if self.args.launch_cmd
                    else 1
                )
                for launch_index in range(launches):
                    await self.startup(client, launch_index)
                    if self.startups[-1]["status"] != "valid":
                        self.status = "aborted"
                        return
                for unit in self.scheduled:
                    await self._run_unit(client, unit)
            self.status = "completed"
        except (KeyboardInterrupt, asyncio.CancelledError):
            self.status = "aborted"
            self.abort_reason = "interrupted or cancelled"
        except CaptureLimitError as exc:
            self.status = "aborted"
            self.abort_reason = str(exc)
        finally:
            if self.args.stop_after_launch:
                for proc in self.launched:
                    self._stop_process(proc)
            await self.lag.stop()
            self._requests_fh.close()
            with (self.out / RUN_FILE).open("x", encoding="utf-8") as handle:
                handle.write(json.dumps(self.run_record(), indent=2, ensure_ascii=False))


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """CLI definition."""
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--plan", required=True, help="path to workload.json")
    p.add_argument("--mode", default="quick", help="mode name from the plan (quick|full)")
    p.add_argument("--out", help="events directory to write (run.json + requests.jsonl)")
    p.add_argument("--base-url", help="override target.base_url from the plan")
    p.add_argument("--model", help="model id to request (default: first id from /v1/models)")
    p.add_argument(
        "--launch-cmd",
        help="command that cold-starts the engine; the startup clock starts at launch",
    )
    p.add_argument(
        "--stop-after-launch",
        action="store_true",
        help="terminate every process started via --launch-cmd when the run ends",
    )
    p.add_argument(
        "--cache-flush-url", help="POST here before each unit; otherwise rely on nonce isolation"
    )
    p.add_argument("--request-timeout-s", type=float, default=300.0)
    p.add_argument(
        "--time-scale",
        type=float,
        default=1.0,
        help="scale arrival gaps and poll intervals (<1 compresses mock runs; recorded)",
    )
    p.add_argument("--run-id")
    p.add_argument("--only-unit", help="run only units whose unit_id contains this substring")
    p.add_argument("--gpu-count", type=int, default=0)
    p.add_argument("--gpu-hours", type=float, default=0.0)
    p.add_argument("--gpu-kind", default="none (CPU)")
    p.add_argument(
        "--tokenizer-snapshot",
        type=Path,
        help="verified local tokenizer assets for plan.target.model_role (required for real runs)",
    )
    p.add_argument(
        "--assume-mock",
        action="store_true",
        help="label engine.kind=mock even without the mock header",
    )
    p.add_argument(
        "--dry-run", action="store_true", help="print the plan's cost estimate for --mode and exit"
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Entry point."""
    args = parse_args(argv)
    plan = load_plan(Path(args.plan))
    if args.dry_run:
        estimate = estimate_mode(plan, args.mode)
        estimate["scheduled_units"] = [u.unit_id for u in resolve_mode(plan, args.mode)]
        print(json.dumps(estimate, indent=2))
        return 0 if estimate["fits_cap"] else 2
    if not args.out:
        print("--out is required unless --dry-run", file=sys.stderr)
        return 2
    try:
        runner = Runner(args, plan)
    except TokenizerError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    try:
        asyncio.run(_run_until_stopped(runner))
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "run_id": runner.run_id,
                "status": runner.status,
                "out": str(runner.out),
                "requests": sum(1 for _ in (runner.out / REQUESTS_FILE).open(encoding="utf-8")),
            }
        )
    )
    return 0 if runner.status == "completed" else 1


async def _run_until_stopped(runner: Runner) -> None:
    """Give SIGTERM the same cleanup path as an asyncio cancellation.

    The outer capture stops this runner's group, but launched engine supervisors
    have separate sessions. They must be stopped and reaped by Runner.run's finally
    block before the outer capture can check daemon-owned container absence.
    """
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    previous = signal.getsignal(signal.SIGTERM)
    if os.name == "posix":
        assert task is not None
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        await runner.run()
    finally:
        if os.name == "posix":
            loop.remove_signal_handler(signal.SIGTERM)
            signal.signal(signal.SIGTERM, previous)


if __name__ == "__main__":
    raise SystemExit(main())
