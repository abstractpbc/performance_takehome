"""Per-request evidence gates and token-time reconstruction.

Every request is re-derived from its verbatim SSE payloads with the shared strict parser;
the runner's convenience fields (``text``, ``finish_reason``, ``client_tokens``) are only
cross-checked. A request ends up in exactly one population category:

* ``completed``        passed every gate; the only category that feeds latency metrics;
* ``failed``           the server answered but wrongly (HTTP error, invalid text, false
                       usage, protocol error, empty output): scored as a failure (FI = 0);
* ``unfinished``       the stream never reached a verified end ([DONE] + finish_reason):
                       drops, timeouts, missing completion markers;
* ``evidence_invalid`` the *measurement* cannot be trusted (timing not monotonic, record
                       inconsistent with its own payloads, client send lag, schema error).
                       Any such request makes the whole run ``invalid``.
"""

import math
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from bench.model_tokens import ModelTokenizer, TokenizerError
from bench.prompts import check_expected
from bench.tokens import TOKENIZER_ID, count_tokens, token_spans
from benchmarks.streaming import StreamProtocolError, decode_payload, generated_deltas

FAILED_REASONS = frozenset(
    {
        "http_error",
        "protocol_error",
        "invalid_output",
        "usage_discrepancy",
        "empty_output",
        "unexpected_finish_reason",
    }
)
UNFINISHED_REASONS = frozenset(
    {"missing_done", "missing_finish_reason", "drop", "timeout", "transport_error"}
)
EVIDENCE_REASONS = frozenset(
    {"invalid_timing", "record_mismatch", "client_send_lag", "schema_error", "missing_timestamp"}
)


@dataclass
class Verdict:
    """Gate outcome for one request plus the reconstructed token timeline."""

    request_id: str
    category: str
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    ttft_s: float | None = None
    first_byte_s: float | None = None
    token_times_s: list[float] = field(default_factory=list)
    content_tokens: int = 0
    reasoning_tokens: int = 0
    content_events: int = 0
    usage_completion_tokens: int | None = None
    grapheme_splits: int = 0
    prompt_tokens: int = 0
    done_received: bool = False
    finish_reason: str | None = None


def _reparse(
    events: list[dict[str, Any]],
) -> tuple[list[tuple[str, str, float]], bool, str | None, dict[str, Any] | None, list[str]]:
    """Re-derive (channel, text, t) deltas, [DONE], finish_reason, usage and protocol errors.

    Parsing continues past a bad event (like the runner does) so the reconstructed text can
    be cross-checked against the record; every violation is still reported.
    """
    deltas: list[tuple[str, str, float]] = []
    done = False
    finish: str | None = None
    usage: dict[str, Any] | None = None
    errors: list[str] = []
    ids: set[str] = set()
    for event in events:
        try:
            decoded = decode_payload(str(event["raw"]))
        except (ValueError, RecursionError) as exc:
            errors.append(f"protocol_error:{exc}")
            continue
        if decoded is None:
            done = True
            continue
        if done:
            errors.append("protocol_error:data after [DONE]")
        if usage is not None:
            errors.append("protocol_error:data after the usage chunk")
        if decoded.get("id") is not None:
            ids.add(str(decoded["id"]))
            if len(ids) > 1:
                errors.append("protocol_error:chunks from more than one completion id")
                ids = {str(decoded["id"])}
        if isinstance(decoded.get("usage"), dict):
            usage = decoded["usage"]
        try:
            for channel, text in generated_deltas(decoded):
                deltas.append((channel, text, float(event["t"])))
        except StreamProtocolError as exc:
            errors.append(f"protocol_error:{exc}")
            continue
        choices = decoded.get("choices") or []
        if choices and isinstance(choices[0], dict) and choices[0].get("finish_reason"):
            finish = str(choices[0]["finish_reason"])
    return deltas, done, finish, usage, errors


def grapheme_boundary_splits(deltas: list[tuple[str, str, float]]) -> int:
    """Count content deltas that begin inside a grapheme cluster.

    A delta starts inside a cluster when its first code point is a combining mark, a
    zero-width joiner/non-joiner, a variation selector, a lone surrogate (half of a code
    point that the runner could only decode with ``surrogateescape``), or when the previous
    delta ended with a ZWJ or with an *unpaired* regional indicator (half a flag). Two
    complete adjacent flags (two RI pairs) are not a split.
    """

    def is_ri(ch: str) -> bool:
        return "\U0001f1e6" <= ch <= "\U0001f1ff"

    count = 0
    previous = ""
    for channel, text, _ in deltas:
        if channel != "content" or not text:
            continue
        first = text[0]
        trailing_ri = 0
        for ch in reversed(previous):
            if not is_ri(ch):
                break
            trailing_ri += 1
        starts_inside = (
            unicodedata.category(first) in ("Mn", "Mc", "Me", "Cs")
            or first in "\u200d\u200c"
            or "\ufe00" <= first <= "\ufe0f"
            or (is_ri(first) and trailing_ri % 2 == 1)
            or (previous[-1:] == "\u200d")
        )
        if starts_inside:
            count += 1
        previous = text
    return count


def token_times(
    deltas: list[tuple[str, float]],
    send_s: float,
    tokenizer: ModelTokenizer | None = None,
) -> tuple[list[float], int]:
    """Request-relative arrival time of each content token (delta resolution).

    The full text is tokenized once so tokens that straddle delta boundaries are counted
    once; each token takes the arrival time of the delta holding its last character.
    """
    text = "".join(t for t, _ in deltas)
    if not text:
        return [], 0
    bounds: list[tuple[int, float]] = []
    offset = 0
    for piece, t in deltas:
        offset += len(piece)
        bounds.append((offset, t))
    times: list[float] = []
    cursor = 0
    if tokenizer is not None:
        ends = tokenizer.end_offsets(text)
    else:
        ends = [end for _, end in token_spans(text)]
    for pos in ends:
        while cursor < len(bounds) - 1 and bounds[cursor][0] < pos:
            cursor += 1
        times.append(bounds[cursor][1] - send_s)
    return times, len(times)


def _timing_errors(ts: dict[str, Any], events: list[dict[str, Any]], http_ok: bool) -> list[str]:
    errors: list[str] = []
    for key in ("send", "end"):
        if not isinstance(ts.get(key), int | float):
            errors.append(f"missing_timestamp:{key}")
    if errors:
        return errors
    values = [v for v in ts.values() if isinstance(v, int | float)] + [
        e.get("t") for e in events if isinstance(e.get("t"), int | float)
    ]
    if any(not math.isfinite(v) or v < 0 for v in values):
        return ["invalid_timing:non-finite or negative timestamp"]
    order = [("send", ts["send"])]
    for key in ("headers", "first_byte"):
        value = ts.get(key)
        if value is None:
            if http_ok and events:
                errors.append(f"missing_timestamp:{key}")
        elif isinstance(value, int | float):
            order.append((key, value))
        else:
            errors.append(f"missing_timestamp:{key}")
    for index, event in enumerate(events):
        if not isinstance(event.get("t"), int | float):
            errors.append(f"missing_timestamp:event[{index}]")
            return errors
        order.append((f"event[{index}]", event["t"]))
    order.append(("end", ts["end"]))
    for (a_name, a), (b_name, b) in zip(order, order[1:], strict=False):
        if b < a:
            errors.append(f"invalid_timing:{b_name}<{a_name}")
            break
    planned = ts.get("planned_send")
    if isinstance(planned, int | float) and ts["send"] < planned - 1e-6:
        errors.append("invalid_timing:send<planned_send")
    return errors


def judge(
    record: dict[str, Any],
    plan: dict[str, Any],
    schema_errors: list[str],
    tokenizer: ModelTokenizer | None = None,
) -> Verdict:
    """Apply every gate to one request record."""
    verdict = Verdict(str(record.get("request_id", "?")), "completed")
    reasons = verdict.reasons
    if schema_errors:
        reasons.extend(f"schema_error:{e}" for e in schema_errors[:3])
    ts = record.get("timestamps_mono_s") or {}
    events = record.get("events") or []
    status = record.get("http_status")
    http_ok = status == 200
    timing = _timing_errors(ts, events, http_ok)
    reasons.extend(timing)
    send = ts.get("send") if isinstance(ts.get("send"), int | float) else None
    planned = ts.get("planned_send")
    if record.get("phase") == "measure" and (
        not isinstance(planned, int | float)
        or isinstance(planned, bool)
        or not math.isfinite(planned)
    ):
        reasons.append("missing_timestamp:planned_send for measurement")
    max_send_lag = plan["client_integrity"]["max_send_lag_s"]
    if send is not None and isinstance(planned, int | float) and send - planned > max_send_lag:
        reasons.append(f"client_send_lag:{send - planned:.3f}s")
    error = record.get("error")
    if status is None and error is None:
        reasons.append("missing_timestamp:http_status")
    elif status is not None and not http_ok:
        reasons.append(f"http_error:{status}")
    if isinstance(error, dict):
        kind = str(error.get("kind"))
        if kind in ("drop", "timeout"):
            reasons.append(kind)
        elif kind == "transport":
            reasons.append("transport_error")
        elif kind == "protocol":
            reasons.append(f"protocol_error:{error.get('message')}")
        elif kind != "http_error":
            reasons.append(f"transport_error:{kind}")
    deltas, done, finish, usage, proto = _reparse(events)
    reasons.extend(proto)
    verdict.done_received = done
    verdict.finish_reason = finish
    content = [(t, at) for ch, t, at in deltas if ch == "content"]
    reasoning_text = "".join(t for ch, t, _ in deltas if ch == "reasoning_content")
    content_text = "".join(t for t, _ in content)
    recorded = record.get("text") or {}
    if (
        recorded.get("content", content_text) != content_text
        or record.get("done_received", done) != done
    ):
        reasons.append("record_mismatch:text or [DONE] differs from verbatim payloads")
    verdict.content_events = sum(1 for t, _ in content if t)
    try:
        verdict.reasoning_tokens = (
            tokenizer.count(reasoning_text) if tokenizer else count_tokens(reasoning_text)
        )
        if send is not None:
            verdict.token_times_s, verdict.content_tokens = token_times(content, send, tokenizer)
    except TokenizerError as exc:
        reasons.append(f"record_mismatch:tokenizer failed: {exc}")
    client = record.get("client_tokens") or {}
    if client.get("count_error"):
        reasons.append(f"record_mismatch:capture tokenizer failed: {client['count_error']}")
    expected_tokenizer = tokenizer.identifier if tokenizer else TOKENIZER_ID
    if client.get("tokenizer") != expected_tokenizer or client.get("resolution") != "delta":
        reasons.append("record_mismatch:tokenizer identity or timing resolution differs")
    if (
        client.get("content") != verdict.content_tokens
        or client.get("reasoning_content") != verdict.reasoning_tokens
    ):
        reasons.append(
            "record_mismatch:client token counts differ from independently reencoded text"
        )
    verdict.prompt_tokens = int((record.get("prompt") or {}).get("approx_prompt_tokens") or 0)
    if send is not None:
        if isinstance(ts.get("first_byte"), int | float):
            verdict.first_byte_s = ts["first_byte"] - send
        first_text = next((at for t, at in content if t), None)
        if first_text is not None:
            verdict.ttft_s = first_text - send
    content_type = record.get("response_content_type")
    if http_ok and content_type is not None and "text/event-stream" not in str(content_type):
        reasons.append(f"protocol_error:content-type {content_type!r} is not text/event-stream")
    if http_ok and not error:
        if not done:
            reasons.append("missing_done")
        if finish is None:
            reasons.append("missing_finish_reason")
        expected = (record.get("prompt") or {}).get("expected") or {}
        allowed = expected.get("finish_reasons") or []
        if finish is not None and allowed and finish not in allowed:
            reasons.append(f"unexpected_finish_reason:{finish}")
        if not content_text:
            reasons.append("empty_output")
        reasons.extend(f"invalid_output:{f}" for f in check_expected(content_text, expected))
        if (
            usage
            and "completion_tokens" in usage
            and (
                isinstance(usage["completion_tokens"], bool)
                or not isinstance(usage["completion_tokens"], int)
            )
        ):
            reasons.append(
                "usage_discrepancy:completion_tokens is not an integer "
                f"({usage['completion_tokens']!r})"
            )
        elif usage and isinstance(usage.get("completion_tokens"), int):
            verdict.usage_completion_tokens = usage["completion_tokens"]
            independent = verdict.content_tokens + verdict.reasoning_tokens
            lo, hi = plan["token_accounting"]["usage_ratio_band"]
            ratio = usage["completion_tokens"] / independent if independent else None
            if ratio is None or not lo <= ratio <= hi:
                reasons.append(
                    f"usage_discrepancy:usage={usage['completion_tokens']} "
                    f"independent={independent}"
                )
        elif usage is None and plan["output_policy"]["stream_options"].get("include_usage"):
            verdict.flags.append("usage_not_reported")
        if verdict.content_tokens and verdict.content_events:
            if verdict.content_tokens / verdict.content_events > 1.5:
                verdict.flags.append("multi_token_deltas")
        verdict.grapheme_splits = grapheme_boundary_splits(deltas)
    keys = {r.split(":", 1)[0] for r in reasons}
    if keys & EVIDENCE_REASONS:
        verdict.category = "evidence_invalid"
    elif keys & UNFINISHED_REASONS:
        verdict.category = "unfinished"
    elif keys & FAILED_REASONS:
        verdict.category = "failed"
    return verdict
