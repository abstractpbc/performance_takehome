"""Validity gates. STATES.md is the spec; quantile power and FTGR states are added by axes.py."""

import json
import math
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from analysis.events import Plan, Run  # noqa: E402

from benchmarks.streaming import (  # noqa: E402
    StreamProtocolError,
    decode_payload,
    generated_deltas,
)

VALID, INVALID, INCONCLUSIVE = "valid", "invalid", "inconclusive"

RUN_FAILING = frozenset(
    {"wrong_output", "false_usage"}
)
EXCLUDED = frozenset(
    {"client_lag", "client_aborted", "usage_absent", "tokens_missing", "no_canary_answer"}
)
# A probe only has to prove a valid generation, so these don't disqualify it.
PROBE_TOLERATED = frozenset({"client_lag", "usage_absent"})


def worst(*states: str) -> str:
    if INVALID in states:
        return INVALID
    return INCONCLUSIVE if INCONCLUSIVE in states else VALID


@dataclass
class Stream:
    content: str = ""
    reasoning: str = ""
    first_text_t: float | None = None
    first_content_t: float | None = None
    finish_reason: str | None = None
    done: bool = False
    done_t: float | None = None
    usage_tokens: int | None = None
    server_error: bool = False
    protocol_error: bool = False

    @property
    def text(self) -> str:
        return self.reasoning + self.content

    @property
    def hidden_tokens(self) -> int:
        """Reasoning tags that LLMD generates and bills but strips from the stream."""
        return bool(self.reasoning) + bool(self.reasoning and self.content)


def answers(content: str, expected: str) -> bool:
    """A canary passes when its last number is the answer, since models show their working."""
    numbers = re.findall(r"\d+", content.replace(",", ""))
    return bool(numbers) and numbers[-1] == expected


def parse_stream(events: list[dict[str, Any]]) -> Stream:
    """Read a server's claims from raw SSE, keeping going so every fault is seen."""
    stream = Stream()
    for record in events:
        try:
            event = decode_payload(record["data"])
            if event is None:
                if not stream.done:
                    stream.done, stream.done_t = True, record["t"]
                continue
            deltas = generated_deltas(event)
        except StreamProtocolError as exc:
            if str(exc).startswith("server stream error"):
                stream.server_error = True
            else:
                stream.protocol_error = True
            continue
        for channel, text in deltas:
            if channel == "content":
                stream.content += text
                if stream.first_content_t is None:
                    stream.first_content_t = record["t"]
            else:
                stream.reasoning += text
        if deltas and stream.first_text_t is None:
            stream.first_text_t = record["t"]
        usage = event.get("usage")
        if isinstance(usage, dict) and isinstance(usage.get("completion_tokens"), int):
            stream.usage_tokens = usage["completion_tokens"]
        choices = event.get("choices")
        if choices and choices[0].get("finish_reason") is not None:
            stream.finish_reason = choices[0]["finish_reason"]
    return stream


@dataclass
class RequestResult:
    request: dict[str, Any]
    state: str
    reasons: tuple[str, ...]
    stream: Stream
    token_times: list[float]

    @property
    def failed(self) -> bool:
        return self.state == INVALID

    @property
    def run_failing(self) -> bool:
        return not RUN_FAILING.isdisjoint(self.reasons)

    @property
    def ttft_s(self) -> float | None:
        first = self.stream.first_text_t
        return None if first is None else first - self.request["t_sent"]

    @property
    def ttft_content_s(self) -> float | None:
        first = self.stream.first_content_t
        return None if first is None else first - self.request["t_sent"]


def _finish(request, reasons, stream, tokens) -> RequestResult:
    invalid = [reason for reason in reasons if reason not in EXCLUDED]
    state = INVALID if invalid else INCONCLUSIVE if reasons else VALID
    times = [token["t"] for token in tokens]
    return RequestResult(request, state, tuple(reasons), stream, times)


def check_request(
    request: dict[str, Any], events: list[dict[str, Any]], tokens: list[dict[str, Any]], plan: Plan
) -> RequestResult:
    gates, deadlines = plan.data["gates"], plan.data["deadlines"]
    stream = parse_stream(events)
    if request["client_outcome"] == "aborted" or request["t_sent"] is None:
        return _finish(request, ["client_aborted"], stream, tokens)

    reasons = []
    if request["t_sent"] - request["t_scheduled"] > gates["max_client_lag_s"]:
        reasons.append("client_lag")
    transport_failed = (
        request["http_status"] != 200
        or "text/event-stream" not in (request["content_type"] or "")
        or request["client_outcome"] == "reset"
        or stream.server_error
        or stream.protocol_error
    )
    if transport_failed:
        reasons.append("server_error")
    # Capture listens briefly after [DONE], so a finished request is timed to [DONE].
    finished = stream.done_t if stream.done_t is not None else request["t_end"]
    late = finished is not None and (
        finished - request["t_sent"] > deadlines["request_timeout_s"]
    )
    timed_out = request["client_outcome"] == "timeout" or late
    if timed_out:
        reasons.append("timed_out")
    if "�" in stream.text:
        reasons.append("wrong_output")
    if transport_failed or timed_out:
        return _finish(request, reasons, stream, tokens)

    if not stream.text:
        return _finish(request, [*reasons, "empty_output"], stream, tokens)
    if not stream.done or stream.finish_reason not in ("stop", "length"):
        return _finish(request, [*reasons, "incomplete_stream"], stream, tokens)
    if request["is_canary"]:
        expected = plan.canaries.get(request["prompt_id"])
        if expected is None:
            reasons.append("no_canary_answer")
        elif not answers(stream.content, expected):
            reasons.append("wrong_output")
    if not tokens:
        return _finish(request, [*reasons, "tokens_missing"], stream, tokens)

    count = len(tokens) + stream.hidden_tokens
    tolerance = max(gates["usage_tolerance_tokens"],
                    math.ceil(gates["usage_tolerance_fraction"] * count))
    # Retokenized text can fall a few tokens short, so every length floor gets the tolerance.
    # With ignore_eos the server must generate max_tokens, so stopping early is truncation.
    full = stream.finish_reason == "length" or request.get("ignore_eos")
    if count + tolerance < request["min_output_tokens"] or (
        full and count + tolerance < request["max_tokens"]
    ):
        reasons.append("truncated_output")
    if stream.usage_tokens is None:
        if gates["usage_expected"]:
            reasons.append("usage_absent")
    elif abs(stream.usage_tokens - count) > tolerance:
        reasons.append("false_usage")
    return _finish(request, reasons, stream, tokens)


def check_evidence(run: Run) -> list[str]:
    """Level 1. Returns reason codes; the file checks already ran in load_run."""
    reasons = []
    if _timing_corrupt(run):
        reasons.append("timing_corrupt")
    aborted = run.manifest["aborted"] is not None
    if not aborted and _requests_vanished(run):
        reasons.append("request_vanished")
    if aborted:
        reasons.append("run_truncated")
    return reasons


def _bad_time(value: Any) -> bool:
    return value is not None and (
        not isinstance(value, int | float) or not math.isfinite(value) or value < 0
    )


def _timing_corrupt(run: Run) -> bool:
    for request in run.requests:
        times = [request[key] for key in ("t_scheduled", "t_sent", "t_headers", "t_end")]
        if any(_bad_time(value) for value in times):
            return True
        sent = request["t_sent"]
        for records in (
            run.sse.get(request["request_id"], ()), run.tokens.get(request["request_id"], ())
        ):
            previous = sent if sent is not None else 0.0
            for record in records:
                if _bad_time(record["t"]) or record["t"] < previous:
                    return True
                previous = record["t"]
    return any(_bad_time(event["t"]) for event in run.launches)


def _requests_vanished(run: Run) -> bool:
    seen = Counter(
        (request["cell"], str(request["repetition"]))
        for request in run.requests if request["phase"] == "measure"
    )
    return any(
        seen[(cell, repetition)] < count
        for cell, repetitions in run.manifest["planned_requests"].items()
        for repetition, count in repetitions.items()
    )


@dataclass
class Startup:
    state: str
    reasons: tuple[str, ...]
    samples_s: list[float]


def check_startup(run: Run, plan: Plan, probes: dict[int, list[RequestResult]]) -> Startup:
    timeout = plan.data["deadlines"]["startup_timeout_s"]
    aborted = run.manifest["aborted"] is not None
    reasons, samples = [], []
    for launch in range(run.manifest["planned_launches"]):
        events = [event for event in run.launches if event["launch"] == launch]
        starts = [event["t"] for event in events if event["event"] == "launch_start"]
        if not starts:
            reasons.append("startup_truncated" if aborted else "startup_failed")
            continue
        results = sorted(
            probes.get(launch, ()), key=lambda result: result.request["t_end"] or math.inf
        )
        if any("wrong_output" in result.reasons for result in results):
            reasons.append("wrong_output")
        good = [
            result.request["t_end"] - starts[0] for result in results
            if result.request["t_end"] is not None and PROBE_TOLERATED.issuperset(result.reasons)
        ]
        if good and good[0] <= timeout:
            samples.append(good[0])
        elif any(event["event"] == "crash" for event in events):
            reasons.append("startup_crash")
        elif any(event["event"] == "startup_timeout" for event in events):
            reasons.append("startup_failed")
        elif aborted:
            reasons.append("startup_truncated")
        else:
            reasons.append("startup_failed")
    if not reasons and len(samples) < 3:
        reasons.append("startup_underpowered")
    invalid = {"startup_failed", "startup_crash", "wrong_output"}.intersection(reasons)
    state = INVALID if invalid else INCONCLUSIVE if reasons else VALID
    return Startup(state, tuple(dict.fromkeys(reasons)), samples)


@dataclass
class Stability:
    state: str
    reasons: tuple[str, ...]
    observed: int = 0
    diverged: int = 0


def _diverges(stream: Stream, entry: dict[str, Any], chars: int) -> bool:
    """Batching and cache state change greedy text, so only its opening and ending are pinned."""
    return (not stream.text.startswith(entry["output"][:chars])
            or stream.finish_reason != entry["finish_reason"]
            or 2 * len(stream.text) < len(entry["output"]))


def check_stability(results: list[RequestResult], plan: Plan) -> Stability:
    if plan.reference is None:
        return Stability(INCONCLUSIVE, ("no_reference",))
    revision = plan.data["model"]["revision"]
    if any(entry["model_revision"] != revision for entry in plan.reference.values()):
        return Stability(INCONCLUSIVE, ("no_reference",))
    observed = diverged = 0
    for result in results:
        entry = plan.reference.get(result.request["prompt_sha256"])
        if not result.request["is_anchor"] or entry is None or not entry["stable"]:
            continue
        if result.state != VALID:
            continue
        observed += 1
        diverged += _diverges(result.stream, entry, plan.data["anchor"]["match_chars"])
    gates = plan.data["gates"]
    if observed < gates["min_stable_anchors"]:
        return Stability(INCONCLUSIVE, ("anchors_underpowered",), observed, diverged)
    if gates["max_anchor_divergence"] is None:
        return Stability(INCONCLUSIVE, ("divergence_unmeasured",), observed, diverged)
    if diverged / observed > gates["max_anchor_divergence"]:
        return Stability(INVALID, ("reference_mismatch",), observed, diverged)
    return Stability(VALID, (), observed, diverged)


@dataclass
class Assessment:
    """Validity only. axes.py adds quantile power and FTGR before a final run state exists."""

    state: str
    reasons: tuple[str, ...]
    request_reasons: dict[str, int] = field(default_factory=dict)
    results: list[RequestResult] = field(default_factory=list)
    startup: Startup | None = None
    stability: Stability | None = None
    failure_fractions: dict[str, float] = field(default_factory=dict)


def assess_run(run: Run, plan: Plan) -> Assessment:
    evidence = check_evidence(run)
    if {"timing_corrupt", "request_vanished"}.intersection(evidence):
        return Assessment(INVALID, tuple(evidence))

    results, probes = [], {}
    for request in run.requests:
        if request["phase"] == "warmup":
            continue
        request_id = request["request_id"]
        result = check_request(
            request, run.sse.get(request_id, []), run.tokens.get(request_id, []), plan
        )
        if request["phase"] == "probe":
            probes.setdefault(request["launch"], []).append(result)
        else:
            results.append(result)

    request_reasons = Counter(reason for result in results for reason in result.reasons)
    invalid = [reason for reason in request_reasons if reason in RUN_FAILING]
    fractions = _failure_fractions(results)
    if any(
        value > plan.data["gates"]["max_failed_fraction"] for value in fractions.values()
    ):
        invalid.append("failure_rate")
    startup = check_startup(run, plan, probes)
    if "startup_crash" not in startup.reasons and any(
        event["event"] == "crash" for event in run.launches
    ):
        invalid.append("server_crash")
    stability = check_stability(results, plan)

    inconclusive = list(evidence)
    measured = len(results)
    lagged = request_reasons["client_lag"]
    if measured and lagged / measured > plan.data["gates"]["max_lagged_fraction"]:
        inconclusive.append("client_lag")
    if not any(result.state == VALID for result in results):
        inconclusive.append("no_valid_requests")
    warmups = [phase for phase in run.phases if phase["phase"] == "warmup"]
    if not warmups or any(phase["status"] != "ok" for phase in warmups):
        inconclusive.append("warmup_incomplete")

    for part in (startup, stability):
        (invalid if part.state == INVALID else inconclusive).extend(part.reasons)
    if invalid:
        state, reasons = INVALID, [*invalid, *inconclusive]
    else:
        state, reasons = (INCONCLUSIVE, inconclusive) if inconclusive else (VALID, [])
    return Assessment(
        state, tuple(dict.fromkeys(reasons)), dict(request_reasons), results,
        startup, stability, fractions,
    )


def _failure_fractions(results: list[RequestResult]) -> dict[str, float]:
    totals: Counter[str] = Counter()
    failed: Counter[str] = Counter()
    for result in results:
        if result.state == INCONCLUSIVE:
            continue
        totals[result.request["cell"]] += 1
        failed[result.request["cell"]] += result.failed
    return {cell: failed[cell] / total for cell, total in totals.items()}


def describe(assessment: Assessment) -> str:
    return json.dumps(
        {"state": assessment.state, "reasons": assessment.reasons}, ensure_ascii=False
    )
