"""Development measurement: streaming requests, TTFT, Etalon FTGR, startup, correctness gates.

Uses only the shared, protected ``benchmarks.metrics`` / ``benchmarks.streaming``
modules for metric arithmetic and SSE parsing. Nothing here is an official
grade; it is the harness's own development evidence.
"""

from __future__ import annotations

import http.client
import json
import math
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

from benchmarks import metrics, streaming

BENCHMARK_SCHEMA_VERSION = 1
LLMD_CLOSED_THINKING_PROFILE = "llmd_closed_thinking_v1"
_RESERVED_REQUEST_FIELDS = frozenset(
    {
        "model",
        "messages",
        "max_tokens",
        "temperature",
        "top_k",
        "seed",
        "stream",
        "stream_options",
        "continue_final_message",
        "add_generation_prompt",
        "request_profile",
    }
)


@dataclass
class RequestTrace:
    """One streamed chat completion with independently timed token arrivals."""

    prompt_id: str
    ok: bool
    text: str = ""
    finish_reason: str | None = None
    ttft_s: float | None = None
    arrival_times_s: list[float] = field(default_factory=list)
    streamed_chunks: int = 0
    usage_completion_tokens: int | None = None
    usage_prompt_tokens: int | None = None
    total_s: float | None = None
    error: str | None = None
    status: int | None = None


@dataclass(frozen=True)
class GateResult:
    """Outcome of one correctness gate."""

    name: str
    passed: bool
    detail: str


@dataclass
class Measurement:
    """Aggregated development measurement for one image."""

    engine_model_id: str | None
    startup_s: float | None
    repetitions: int
    ttft_samples: list[float]
    ftgr_samples: list[float]
    traces: list[RequestTrace]
    gates: list[GateResult]
    gates_passed: bool
    notes: list[str] = field(default_factory=list)
    ttft_by_prompt: dict[str, list[float]] = field(default_factory=dict)
    ftgr_by_prompt: dict[str, list[float]] = field(default_factory=dict)

    def samples(self) -> dict[str, dict[str, list[float]]]:
        """Metric name -> prompt id -> per-request samples (shape ``decision.decide`` expects)."""
        out: dict[str, dict[str, list[float]]] = {
            "ttft_s": {k: list(v) for k, v in self.ttft_by_prompt.items()},
            "ftgr_tok_s": {k: list(v) for k, v in self.ftgr_by_prompt.items()},
        }
        if self.startup_s is not None:
            out["startup_s"] = {"startup": [self.startup_s]}
        return out

    def to_json(self) -> dict[str, Any]:
        """Serializable form (traces included for replay)."""
        data = asdict(self)
        return data


class MeasurementError(RuntimeError):
    """Raised when a measurement cannot be completed at all (server unreachable)."""


class BenchmarkError(ValueError):
    """``development-benchmark.json`` is malformed."""


def _positive_int(data: Mapping[str, Any], key: str) -> None:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise BenchmarkError(f"{key} must be an integer >= 1, got {value!r}")


def _positive_number(data: Mapping[str, Any], key: str) -> None:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
        raise BenchmarkError(f"{key} must be a number > 0, got {value!r}")


def _validate_request_policy(
    request_profile: str | None, extra_fields: Mapping[str, Any] | None
) -> None:
    if request_profile is not None and request_profile != LLMD_CLOSED_THINKING_PROFILE:
        raise BenchmarkError(f"unknown request_profile {request_profile!r}")
    if extra_fields is not None and not isinstance(extra_fields, Mapping):
        raise BenchmarkError("extra_request_fields must be an object")
    fields = set(extra_fields or {})
    reserved = fields & _RESERVED_REQUEST_FIELDS
    if reserved:
        raise BenchmarkError(f"extra_request_fields cannot override {sorted(reserved)!r}")
    if request_profile is not None and fields & {
        "enable_thinking",
        "chat_template_kwargs",
        "reasoning_effort",
    }:
        raise BenchmarkError("reasoning options conflict with the selected request_profile")


def _benchmark_request_profile(benchmark: Mapping[str, Any]) -> str | None:
    profile = benchmark.get("request_profile")
    if "request_profile" in benchmark and profile != LLMD_CLOSED_THINKING_PROFILE:
        raise BenchmarkError(f"unknown request_profile {profile!r}")
    _validate_request_policy(profile, benchmark.get("extra_request_fields"))
    return profile


def build_chat_request(
    model: str,
    prompt: str,
    *,
    max_tokens: int,
    temperature: float = 0.0,
    seed: int = 0,
    extra_fields: Mapping[str, Any] | None = None,
    request_profile: str | None = None,
) -> dict[str, Any]:
    """Build the same explicit request policy for startup and every measured prompt.

    The optional LLMD profile continues a fixed closed-thinking assistant prefix.
    It changes neither the supplied user prompt nor the caller-selected model,
    token limit, greedy sampling or streaming/usage settings. The prefix contains
    no answer text. Omitted profile preserves the existing user-only body.
    """
    _validate_request_policy(request_profile, extra_fields)
    messages = [{"role": "user", "content": prompt}]
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_k": 1,
        "seed": seed,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if request_profile == LLMD_CLOSED_THINKING_PROFILE:
        messages.append({"role": "assistant", "content": "</think>\n\n"})
        body["continue_final_message"] = True
        body["add_generation_prompt"] = False
    body.update(dict(extra_fields or {}))
    return body


def load_benchmark(path: Any) -> dict[str, Any]:
    """Load and schema-check ``development-benchmark.json`` (raises :class:`BenchmarkError`)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BenchmarkError(f"cannot read development benchmark {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise BenchmarkError("development benchmark must be a JSON object")
    if data.get("schema_version") != BENCHMARK_SCHEMA_VERSION:
        raise BenchmarkError(
            f"unsupported development benchmark schema {data.get('schema_version')!r}"
        )
    prompts = data.get("prompts")
    if not isinstance(prompts, list) or not prompts:
        raise BenchmarkError("development benchmark needs a nonempty prompts list")
    ids: list[str] = []
    for n, prompt in enumerate(prompts):
        if not isinstance(prompt, dict):
            raise BenchmarkError(f"prompt {n} must be an object")
        for key in ("id", "prompt"):
            if not isinstance(prompt.get(key), str) or not prompt[key].strip():
                raise BenchmarkError(f"prompt {n} needs a nonempty string {key!r}")
        if "max_tokens" in prompt:
            _positive_int(prompt, "max_tokens")
        ids.append(prompt["id"])
    if len(set(ids)) != len(ids):
        raise BenchmarkError("duplicate prompt ids")
    for key in ("repetitions", "max_tokens"):
        _positive_int(data, key)
    for key in ("request_timeout_s", "prefill_deadline_s", "decode_deadline_s"):
        _positive_number(data, key)
    probe = data.get("startup_probe")
    if not isinstance(probe, dict) or not isinstance(probe.get("prompt"), str):
        raise BenchmarkError("startup_probe needs a string 'prompt'")
    _positive_int(probe, "max_tokens")
    _benchmark_request_profile(data)
    return data


def _open_connection(base_url: str, timeout_s: float) -> http.client.HTTPConnection:
    parsed = urllib.parse.urlsplit(base_url)
    if parsed.scheme != "http" or not parsed.hostname:
        raise ValueError(f"only plain http base URLs are supported: {base_url!r}")
    return http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=timeout_s)


def get_json(base_url: str, path: str, timeout_s: float) -> tuple[int, Any]:
    """GET ``path`` and decode JSON; returns ``(status, body_or_None)``."""
    conn = _open_connection(base_url, timeout_s)
    try:
        conn.request("GET", path, headers={"Accept": "application/json"})
        resp = conn.getresponse()
        raw = resp.read()
        try:
            return resp.status, json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            return resp.status, None
    finally:
        conn.close()


def _iter_sse_lines(resp: http.client.HTTPResponse, deadline: float) -> Any:
    while True:
        if time.monotonic() > deadline:
            raise TimeoutError("stream exceeded request deadline")
        line = resp.readline()
        if not line:
            return
        yield line.decode("utf-8", "replace").rstrip("\r\n")


def stream_chat_completion(
    base_url: str,
    model: str,
    prompt: str,
    *,
    prompt_id: str,
    extra_fields: Mapping[str, Any] | None = None,
    request_profile: str | None = None,
    max_tokens: int,
    timeout_s: float,
    temperature: float = 0.0,
    seed: int = 0,
) -> RequestTrace:
    """Stream one chat completion, timing every content delta on arrival.

    Arrival times are measured on the client when each SSE event containing a
    content delta is read (request-relative seconds). ``usage`` is requested via
    ``stream_options.include_usage`` for the independent token-count gate.
    """
    body = json.dumps(
        build_chat_request(
            model,
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            seed=seed,
            extra_fields=extra_fields,
            request_profile=request_profile,
        )
    ).encode("utf-8")
    trace = RequestTrace(prompt_id=prompt_id, ok=False)
    start = time.monotonic()
    deadline = start + timeout_s
    conn = _open_connection(base_url, timeout_s)
    try:
        conn.request(
            "POST",
            "/v1/chat/completions",
            body=body,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        )
        resp = conn.getresponse()
        trace.status = resp.status
        if resp.status != 200:
            trace.error = f"HTTP {resp.status}: {resp.read(2000).decode('utf-8', 'replace')}"
            return trace
        pieces: list[str] = []
        for payload in streaming.sse_data(_iter_sse_lines(resp, deadline)):
            now = time.monotonic() - start
            event = streaming.decode_payload(payload)
            if event is None:
                continue
            if "error" in event and not event.get("choices"):
                trace.error = f"server error event: {json.dumps(event)[:500]}"
                return trace
            deltas = streaming.generated_deltas(event)
            content = [text for channel, text in deltas if channel == "content" and text]
            if content:
                pieces.extend(content)
                trace.streamed_chunks += 1
                trace.arrival_times_s.append(now)
                if trace.ttft_s is None:
                    trace.ttft_s = now
            for choice in event.get("choices") or []:
                reason = choice.get("finish_reason")
                if reason:
                    trace.finish_reason = reason
            usage = event.get("usage")
            if isinstance(usage, dict):
                if isinstance(usage.get("completion_tokens"), int):
                    trace.usage_completion_tokens = usage["completion_tokens"]
                if isinstance(usage.get("prompt_tokens"), int):
                    trace.usage_prompt_tokens = usage["prompt_tokens"]
        trace.text = "".join(pieces)
        trace.total_s = time.monotonic() - start
        trace.ok = trace.error is None and bool(pieces)
        if not pieces and trace.error is None:
            trace.error = "stream ended without any content delta"
        return trace
    except (OSError, http.client.HTTPException, TimeoutError, ValueError) as exc:
        trace.error = f"{type(exc).__name__}: {exc}"
        trace.total_s = time.monotonic() - start
        return trace
    finally:
        conn.close()


def wait_for_model_id(
    base_url: str, *, timeout_s: float, poll_s: float = 0.5, is_alive: Any = None
) -> str:
    """Poll ``/v1/models`` until a model id is served; raise on timeout or dead server."""
    deadline = time.monotonic() + timeout_s
    last = "no response yet"
    while time.monotonic() < deadline:
        if is_alive is not None and not is_alive():
            raise MeasurementError(f"engine process exited before serving /v1/models ({last})")
        try:
            status, body = get_json(base_url, "/v1/models", timeout_s=min(5.0, poll_s * 4))
            if status == 200 and isinstance(body, dict):
                data = body.get("data") or []
                if data and isinstance(data[0], dict) and data[0].get("id"):
                    return str(data[0]["id"])
                models = body.get("models") or []
                if models and isinstance(models[0], dict) and models[0].get("name"):
                    return str(models[0]["name"])
                last = f"/v1/models returned no model entries: {str(body)[:200]}"
            else:
                last = f"/v1/models -> HTTP {status}"
        except (OSError, http.client.HTTPException) as exc:
            last = f"{type(exc).__name__}: {exc}"
        time.sleep(poll_s)
    raise MeasurementError(f"readiness timeout after {timeout_s:.0f}s: {last}")


def wait_for_first_valid_generation(
    base_url: str,
    started_at: float,
    benchmark: Mapping[str, Any],
    *,
    timeout_s: float,
    is_alive: Any = None,
    max_probe_attempts: int = 3,
    on_probe: Callable[[RequestTrace], None] | None = None,
) -> tuple[float, str]:
    """Startup metric: seconds from container start until the first valid, complete generation.

    Readiness of ``/v1/models`` alone is not accepted; the server must stream a
    complete greedy generation (finish_reason ``stop`` or ``length``) first.
    Completion probes are capped independently of the readiness/time deadline;
    repeated invalid model output is a failed compatibility check.
    """
    if type(max_probe_attempts) is not int or max_probe_attempts < 1:
        raise BenchmarkError("max_probe_attempts must be a positive integer")
    request_profile = _benchmark_request_profile(benchmark)
    remaining = lambda: timeout_s - (time.monotonic() - started_at)  # noqa: E731
    if remaining() <= 0:
        raise MeasurementError("startup deadline exhausted before readiness")
    model_id = wait_for_model_id(base_url, timeout_s=max(1.0, remaining()), is_alive=is_alive)
    probe = benchmark["startup_probe"]
    last_error = "not attempted"
    attempts = 0
    while remaining() > 0 and attempts < max_probe_attempts:
        if is_alive is not None and not is_alive():
            raise MeasurementError(f"engine process exited during startup probe ({last_error})")
        trace = stream_chat_completion(
            base_url,
            model_id,
            probe["prompt"],
            prompt_id="startup-probe",
            max_tokens=int(probe["max_tokens"]),
            timeout_s=max(1.0, min(remaining(), float(benchmark["request_timeout_s"]))),
            extra_fields=benchmark.get("extra_request_fields"),
            request_profile=request_profile,
        )
        attempts += 1
        if on_probe is not None:
            on_probe(trace)
        if trace.ok and trace.finish_reason in {"stop", "length"}:
            return time.monotonic() - started_at, model_id
        last_error = trace.error or f"finish_reason={trace.finish_reason!r}"
        if attempts < max_probe_attempts and remaining() > 0:
            time.sleep(min(0.5, max(0.0, remaining())))
    raise MeasurementError(
        f"no valid generation after {attempts}/{max_probe_attempts} probes "
        f"within {timeout_s:.0f}s of start: {last_error}"
    )


def correctness_gates(
    traces: Sequence[RequestTrace],
    benchmark: Mapping[str, Any],
    golden: Mapping[str, str] | None,
) -> list[GateResult]:
    """Run every correctness gate over the measured traces.

    Gates: every request succeeded; each has a valid finish reason; output is
    complete (``stop`` unless the prompt allows ``length`` at ``max_tokens``);
    independently counted streamed chunks match server-reported usage within
    tolerance; outputs are self-consistent across repetitions (greedy
    determinism); and, when a golden set exists, outputs equal the golden text.
    """
    gates: list[GateResult] = []
    failed = [t for t in traces if not t.ok]
    gates.append(
        GateResult(
            "all_requests_succeeded",
            not failed,
            f"{len(failed)}/{len(traces)} failed" + (f": {failed[0].error}" if failed else ""),
        )
    )
    bad_reason = [t for t in traces if t.ok and t.finish_reason not in {"stop", "length"}]
    gates.append(
        GateResult(
            "valid_finish_reason",
            not bad_reason,
            f"{len(bad_reason)} traces with finish_reason not in {{stop,length}}",
        )
    )
    prompts = {p["id"]: p for p in benchmark["prompts"]}
    incomplete = []
    for t in traces:
        if not t.ok:
            continue
        allow_length = bool(prompts.get(t.prompt_id, {}).get("allow_length", False))
        if t.finish_reason == "length" and not allow_length:
            incomplete.append(t.prompt_id)
    gates.append(
        GateResult(
            "complete_output",
            not incomplete,
            f"{len(incomplete)} truncated (finish_reason=length on prompts requiring stop): "
            f"{sorted(set(incomplete))[:5]}",
        )
    )
    tolerance = float(benchmark.get("token_count_tolerance", 0.0))
    mismatched = []
    missing_usage = []
    for t in traces:
        if not t.ok:
            continue
        if t.usage_completion_tokens is None:
            missing_usage.append(t.prompt_id)
            continue
        diff = abs(t.streamed_chunks - t.usage_completion_tokens)
        if diff > max(1, math.ceil(tolerance * max(t.usage_completion_tokens, 1))):
            mismatched.append(f"{t.prompt_id}:{t.streamed_chunks}!={t.usage_completion_tokens}")
    require_usage = bool(benchmark.get("require_usage", True))
    usage_ok = not mismatched and (not missing_usage or not require_usage)
    gates.append(
        GateResult(
            "token_count_matches_usage",
            usage_ok,
            f"mismatched={mismatched[:5]} missing_usage={sorted(set(missing_usage))[:5]} "
            f"tolerance={tolerance}",
        )
    )
    by_prompt: dict[str, set[str]] = {}
    for t in traces:
        if t.ok:
            by_prompt.setdefault(t.prompt_id, set()).add(t.text)
    inconsistent = sorted(pid for pid, texts in by_prompt.items() if len(texts) > 1)
    gates.append(
        GateResult(
            "greedy_self_consistency",
            not inconsistent,
            f"{len(inconsistent)} prompts produced differing greedy outputs across repetitions: "
            f"{inconsistent[:5]}",
        )
    )
    if golden:
        diffs = []
        for pid, texts in by_prompt.items():
            expected = golden.get(pid)
            if expected is None:
                diffs.append(f"{pid}:no-golden")
            elif texts != {expected}:
                diffs.append(pid)
        gates.append(
            GateResult(
                "golden_output_match",
                not diffs,
                f"{len(diffs)} prompts differ from the baseline golden outputs"
                + (
                    f" (showing 5): {sorted(diffs)[:5]}" if len(diffs) > 5 else f": {sorted(diffs)}"
                ),
            )
        )
    else:
        gates.append(
            GateResult(
                "golden_output_match",
                True,
                "no golden set yet (this measurement establishes it)",
            )
        )
    return gates


def ftgr_for_traces(
    traces: Sequence[RequestTrace], benchmark: Mapping[str, Any]
) -> tuple[float | None, int]:
    """Etalon fluid token generation rate over one repetition's successful traces.

    Returns ``(rate_tok_s_or_None, dropped)`` where ``dropped`` counts traces
    that had fewer than two token arrivals and therefore carry no rate.
    """
    usable = [t for t in traces if t.ok and len(t.arrival_times_s) >= 2]
    dropped = sum(1 for t in traces if t.ok) - len(usable)
    if not usable:
        return None, dropped
    try:
        # RequestTrace stores request-relative receive times; Etalon expects
        # TTFT followed by token-to-token intervals, not cumulative arrivals.
        intervals = [metrics.token_intervals(t.arrival_times_s) for t in usable]
        return metrics.fluid_token_generation_rate(
            intervals,
            max_miss_rate=float(benchmark.get("ftgr_max_miss_rate", 0.1)),
            request_quantile=float(benchmark.get("ftgr_request_quantile", 0.99)),
        ), dropped
    except ValueError:
        return None, dropped


def run_benchmark(
    base_url: str,
    model_id: str,
    benchmark: Mapping[str, Any],
    *,
    golden: Mapping[str, str] | None,
    startup_s: float | None,
    repetitions: int | None = None,
    is_alive: Any = None,
) -> Measurement:
    """Run every prompt ``repetitions`` times sequentially and collect per-request samples.

    Each successful request contributes one TTFT sample and one fluid-rate
    sample (the Etalon FTGR statistic evaluated on that single request, i.e.
    the reciprocal of its minimum decode deadline), so a benchmark of P prompts
    x R repetitions yields P*R samples per primary metric for the bootstrap.
    The per-repetition aggregate FTGR is kept in ``notes`` for the report.
    Correctness gates run over all traces.
    """
    request_profile = _benchmark_request_profile(benchmark)
    reps = int(repetitions if repetitions is not None else benchmark["repetitions"])
    timeout_s = float(benchmark["request_timeout_s"])
    traces: list[RequestTrace] = []
    ttft_samples: list[float] = []
    ftgr_samples: list[float] = []
    ftgr_dropped = 0
    ttft_by_prompt: dict[str, list[float]] = {}
    ftgr_by_prompt: dict[str, list[float]] = {}
    notes: list[str] = []
    for rep in range(reps):
        rep_traces: list[RequestTrace] = []
        for prompt in benchmark["prompts"]:
            if is_alive is not None and not is_alive():
                raise MeasurementError("engine container stopped during measurement")
            trace = stream_chat_completion(
                base_url,
                model_id,
                prompt["prompt"],
                prompt_id=prompt["id"],
                max_tokens=int(prompt.get("max_tokens", benchmark["max_tokens"])),
                timeout_s=timeout_s,
                extra_fields=benchmark.get("extra_request_fields"),
                request_profile=request_profile,
            )
            rep_traces.append(trace)
        traces.extend(rep_traces)
        for trace in rep_traces:
            if trace.ok and trace.ttft_s is not None:
                ttft_samples.append(trace.ttft_s)
                ttft_by_prompt.setdefault(trace.prompt_id, []).append(trace.ttft_s)
            rate, _ = ftgr_for_traces([trace], benchmark)
            if rate is not None:
                ftgr_samples.append(rate)
                ftgr_by_prompt.setdefault(trace.prompt_id, []).append(rate)
            elif trace.ok:
                ftgr_dropped += 1
        ftgr, _ = ftgr_for_traces(rep_traces, benchmark)
        if ftgr is not None:
            notes.append(f"repetition {rep}: aggregate FTGR {ftgr:.3f} tok/s")
        else:
            notes.append(
                f"repetition {rep}: FTGR unresolved (too few tokens or all-at-once decode)"
            )
    if ftgr_dropped:
        notes.append(
            f"{ftgr_dropped} successful request(s) produced < 2 token arrivals and carry no FTGR"
        )
    gates = correctness_gates(traces, benchmark, golden)
    return Measurement(
        engine_model_id=model_id,
        startup_s=startup_s,
        repetitions=reps,
        ttft_samples=ttft_samples,
        ftgr_samples=ftgr_samples,
        ttft_by_prompt=ttft_by_prompt,
        ftgr_by_prompt=ftgr_by_prompt,
        traces=traces,
        gates=gates,
        gates_passed=all(g.passed for g in gates),
        notes=notes,
    )


def golden_from(measurement: Measurement) -> dict[str, str]:
    """Derive the golden text per prompt from a passing baseline measurement."""
    golden: dict[str, str] = {}
    for t in measurement.traces:
        if t.ok and t.prompt_id not in golden:
            golden[t.prompt_id] = t.text
    return golden
