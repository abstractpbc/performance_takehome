"""Seeded fault families for the mock server, one profile per public fixture family.

Every profile is pure configuration; ``plan_stream`` turns a (profile, prompt, per-request
RNG) triple into a fully determined list of SSE chunks plus an oracle describing what was
truly generated, so fixtures carry ground truth separately from the observed events.
"""

import json
import re
import unicodedata
from dataclasses import asdict, dataclass, field, replace
from typing import Any

MOCK_TOKEN_PATTERN = re.compile(r"\s*\w{1,4}|\s*\W", re.UNICODE)
UNICODE_TAIL = (
    " caf\u00e9 \u65e5\u672c\u8a9e"
    " \U0001f468\u200d\U0001f469\u200d\U0001f467"
    " e\u0301t\u00e9 \U0001f1e8\U0001f1e6"
)


def mock_tokenize(text: str) -> list[str]:
    """The mock engine's own tokenizer (up to four word chars per token).

    Deliberately different from ``bench.tokens`` so that the suite's chunk-vs-token and
    usage-cross-check logic is exercised against a tokenizer it does not control.
    """
    return MOCK_TOKEN_PATTERN.findall(text)


@dataclass(frozen=True)
class FaultProfile:
    """Declarative description of one fixture family."""

    name: str
    family: str
    description: str
    ttft_base_s: float = 0.08
    ttft_per_prompt_token_s: float = 0.00001
    decode_interval_s: float = 0.012
    jitter_cv: float = 0.05
    severity: float = 1.0
    late_first_token_s: float = 0.0
    stall_s: float = 0.0
    stall_count: int = 0
    burst_size: int = 1
    reasoning_tokens: int = 0
    unicode_tail: bool = False
    split_graphemes: bool = False
    omit_done: bool = False
    omit_finish_reason: bool = False
    http_error_status: int | None = None
    drop_after_fraction: float | None = None
    wrong_text: bool = False
    garble_text: bool = False
    truncate_fraction: float | None = None
    usage_multiplier: float | None = None
    models_ready_after_s: float = 0.0
    inference_ready_after_s: float = 0.0
    clean_first_n: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serializable copy (for the oracle and fixture labels)."""
        return asdict(self)


_BASE = FaultProfile("clean", "control", "No injected faults; steady decode and tiny jitter.")

PROFILES: dict[str, FaultProfile] = {
    p.name: p
    for p in (
        _BASE,
        replace(
            _BASE,
            name="noisy",
            family="control",
            description="Heavy timing jitter but no structural faults (noise control).",
            jitter_cv=0.6,
        ),
        replace(
            _BASE,
            name="burst",
            family="bursts",
            description="Tokens withheld and released in bursts of 8 per SSE chunk.",
            burst_size=8,
        ),
        replace(
            _BASE,
            name="stall",
            family="stalls",
            description="Two 1.5 s mid-stream stalls; steady otherwise.",
            stall_s=1.5,
            stall_count=2,
        ),
        replace(
            _BASE,
            name="late_first_token",
            family="late_first_tokens",
            description="First token delayed by an extra 2.0 s.",
            late_first_token_s=2.0,
        ),
        replace(
            _BASE,
            name="slow_decode",
            family="stalls",
            description="Decode interval 4x slower than clean (deadline-miss control).",
            decode_interval_s=0.048,
        ),
        replace(
            _BASE,
            name="reasoning_channel",
            family="reasoning_channels",
            description="Emits 24 reasoning_content tokens before the answer.",
            reasoning_tokens=24,
        ),
        replace(
            _BASE,
            name="unicode_split",
            family="unicode_splits",
            description="Appends CJK/emoji/combining text and splits deltas mid-grapheme.",
            unicode_tail=True,
            split_graphemes=True,
        ),
        replace(
            _BASE,
            name="missing_done",
            family="missing_completion",
            description="finish_reason sent but the [DONE] sentinel is never emitted.",
            omit_done=True,
            clean_first_n=3,
        ),
        replace(
            _BASE,
            name="missing_finish_reason",
            family="missing_completion",
            description="[DONE] emitted but finish_reason stays null.",
            omit_finish_reason=True,
            clean_first_n=3,
        ),
        replace(
            _BASE,
            name="http_error",
            family="errors_and_drops",
            description="30% of requests answered with HTTP 500.",
            http_error_status=500,
            severity=0.3,
        ),
        replace(
            _BASE,
            name="drop",
            family="errors_and_drops",
            description="30% of streams closed after half the tokens, no finish.",
            drop_after_fraction=0.5,
            severity=0.3,
        ),
        replace(
            _BASE,
            name="wrong_text",
            family="invalid_output",
            description="Required codes/numbers altered; stream otherwise clean.",
            wrong_text=True,
            clean_first_n=3,
        ),
        replace(
            _BASE,
            name="garbled_text",
            family="invalid_output",
            description="Replacement characters and mojibake injected into the text.",
            garble_text=True,
            clean_first_n=3,
        ),
        replace(
            _BASE,
            name="truncated_text",
            family="invalid_output",
            description="Stops at 40% of the answer but reports finish_reason=stop.",
            truncate_fraction=0.4,
            clean_first_n=3,
        ),
        replace(
            _BASE,
            name="false_usage",
            family="false_usage",
            description="usage.completion_tokens reports 3x the true count.",
            usage_multiplier=3.0,
            clean_first_n=3,
        ),
        replace(
            _BASE,
            name="misleading_readiness",
            family="misleading_readiness",
            description="/v1/models is 200 immediately; inference 503s for 3 s.",
            inference_ready_after_s=3.0,
        ),
        replace(
            _BASE,
            name="slow_startup",
            family="startup",
            description="/v1/models unavailable for 2 s, inference ready at 2.5 s.",
            models_ready_after_s=2.0,
            inference_ready_after_s=2.5,
        ),
    )
}


def profile_by_name(name: str) -> FaultProfile:
    """Look up a profile or raise ``KeyError`` listing the known names."""
    try:
        return PROFILES[name]
    except KeyError:
        raise KeyError(f"unknown profile {name!r}; known: {sorted(PROFILES)}") from None


_CODE_RE = re.compile(r"exact code (CODE-[A-Z]{4}-\d{4})")
_VAULT_RE = re.compile(r"access code for vault (\w+)\?")
_SEQ_RE = re.compile(r"List every integer from 1 to (\d+) inclusive")


def answer_for(messages: list[dict[str, Any]]) -> str:
    """The mock model's deterministic 'correct' answer for a suite prompt."""
    text = "\n".join(str(m.get("content", "")) for m in messages)
    if m := _CODE_RE.search(text):
        return (
            f"{m.group(1)}\nTail latency matters because users experience the slowest "
            "requests, not the average. A mean hides the p99 that defines interactive "
            "quality.\n\nCapacity planning for the tail also exposes queueing and "
            "scheduling pathologies that averages smooth over."
        )
    if m := _VAULT_RE.search(text):
        vault = m.group(1)
        fact = re.search(rf"access code for vault {re.escape(vault)} is (\d+)", text)
        return f"The access code for vault {vault} is {fact.group(1) if fact else 'unknown'}."
    if m := _SEQ_RE.search(text):
        return "\n".join(str(i) for i in range(1, int(m.group(1)) + 1))
    return "I am a mock engine. " * 8


def corrupt_text(answer: str, profile: FaultProfile, rng: Any) -> str:
    """Apply wrong/garbled/truncated mutations to the correct answer."""
    text = answer
    if profile.wrong_text:
        text = re.sub(r"\d", lambda m: str((int(m.group()) + 1) % 10), text, count=6)
        text = text.replace("CODE-", "C0DE-", 1)
    if profile.garble_text:
        pieces = list(text)
        for _ in range(max(3, len(pieces) // 40)):
            pos = rng.randrange(len(pieces))
            pieces[pos] = rng.choice(("\ufffd", "\u00c3\u00a9", "\u00e2\u20ac\u2122"))
        text = "".join(pieces)
    if profile.truncate_fraction is not None:
        text = text[: max(1, int(len(text) * profile.truncate_fraction))]
    return text


def _graphemes(text: str) -> list[str]:
    clusters: list[str] = []
    for ch in text:
        if clusters and (
            unicodedata.combining(ch)
            or ch == "\u200d"
            or clusters[-1].endswith("\u200d")
            or (
                0x1F1E6 <= ord(ch) <= 0x1F1FF
                and len(clusters[-1]) == 1
                and 0x1F1E6 <= ord(clusters[-1]) <= 0x1F1FF
            )
        ):
            clusters[-1] += ch
        else:
            clusters.append(ch)
    return clusters


@dataclass
class Chunk:
    """One SSE event the mock will write, with the delay to wait before writing it."""

    delay_s: float
    payload: str
    kind: str


@dataclass
class StreamPlan:
    """Chunks to emit plus the oracle (ground truth) for one request."""

    chunks: list[Chunk]
    oracle: dict[str, Any]
    http_status: int = 200
    drop_after_chunk: int | None = None


def _jitter(rng: Any, base: float, cv: float) -> float:
    if cv <= 0:
        return base
    return max(0.0, rng.gauss(base, base * cv))


def plan_stream(
    request_id: str,
    body: dict[str, Any],
    profile: FaultProfile,
    rng: Any,
    model_id: str,
    prompt_tokens: int,
    request_index: int = 10**9,
) -> StreamPlan:
    """Deterministically plan the SSE stream and oracle for one chat completion.

    ``request_index`` is the server's 1-based request counter: the first ``clean_first_n``
    requests of a profile are served clean (a server that degrades after start-up), so
    startup probes can succeed and the fault shows up in the measured population.
    """
    affected = request_index > profile.clean_first_n and rng.random() < profile.severity
    answer = answer_for(body.get("messages", []))
    if profile.unicode_tail and "CODE-" in answer:
        # Only chat answers tolerate extra text; integer lists must stay exact.
        answer += UNICODE_TAIL
    text = corrupt_text(answer, profile, rng) if affected else answer
    max_tokens = body.get("max_tokens")
    content_tokens = mock_tokenize(text)
    finish_reason = "stop"
    if isinstance(max_tokens, int) and len(content_tokens) > max_tokens:
        content_tokens = content_tokens[:max_tokens]
        finish_reason = "length"
    reasoning_tokens = (
        mock_tokenize(" ".join(f"step{i}" for i in range(profile.reasoning_tokens)))
        if affected and profile.reasoning_tokens
        else []
    )
    if affected and profile.http_error_status:
        oracle = {
            "request_id": request_id,
            "http_status": profile.http_error_status,
            "true_text": "",
            "true_tokens": 0,
            "true_reasoning_tokens": 0,
            "finish_reason": None,
            "done_emitted": False,
            "fault_applied": True,
            "profile": profile.name,
            "family": profile.family,
        }
        return StreamPlan([], oracle, profile.http_error_status)

    def wrap(delta: dict[str, Any], finish: str | None) -> str:
        return json.dumps(
            {
                "id": f"chatcmpl-{request_id}",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model_id,
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
            },
            ensure_ascii=False,
        )

    chunks: list[Chunk] = []
    ttft = profile.ttft_base_s + profile.ttft_per_prompt_token_s * prompt_tokens
    if affected:
        ttft += profile.late_first_token_s
    chunks.append(
        Chunk(
            _jitter(rng, ttft, profile.jitter_cv),
            wrap({"role": "assistant", "content": ""}, None),
            "role",
        )
    )
    emitted_times: list[float] = []
    clock = chunks[0].delay_s
    interval = profile.decode_interval_s
    for tok in reasoning_tokens:
        d = _jitter(rng, interval, profile.jitter_cv)
        clock += d
        chunks.append(Chunk(d, wrap({"reasoning_content": tok}, None), "reasoning"))
    stall_positions: set[int] = set()
    if affected and profile.stall_count and len(content_tokens) > 4:
        step = len(content_tokens) // (profile.stall_count + 1)
        stall_positions = {step * (i + 1) for i in range(profile.stall_count)}
    pieces: list[str] = (
        _graphemes("".join(content_tokens))
        if (affected and profile.split_graphemes)
        else content_tokens
    )
    if affected and profile.split_graphemes:
        # Emit each grapheme's code points as separate deltas so clusters are split mid-way.
        pieces = [cp for cluster in pieces for cp in cluster]
    burst = profile.burst_size if affected else 1
    pending: list[str] = []
    pending_delay = 0.0
    for i, piece in enumerate(pieces):
        d = _jitter(rng, interval, profile.jitter_cv)
        if i in stall_positions:
            d += profile.stall_s
        clock += d
        pending_delay += d
        if not (affected and profile.split_graphemes) or piece.strip():
            emitted_times.append(clock)
        pending.append(piece)
        if len(pending) >= burst or i == len(pieces) - 1:
            # A burst chunk waits for the whole group's accumulated time, then delivers every
            # pending token at once; the oracle keeps the true per-token emission times.
            chunks.append(
                Chunk(pending_delay, wrap({"content": "".join(pending)}, None), "content")
            )
            pending, pending_delay = [], 0.0
    drop_after: int | None = None
    if affected and profile.drop_after_fraction is not None:
        content_chunks = [i for i, c in enumerate(chunks) if c.kind == "content"]
        if content_chunks:
            drop_after = content_chunks[
                max(0, int(len(content_chunks) * profile.drop_after_fraction) - 1)
            ]
    true_tokens = len(content_tokens)
    usage_tokens = true_tokens + len(reasoning_tokens)  # OpenAI usage counts reasoning too
    if affected and profile.usage_multiplier is not None:
        usage_tokens = int(round(true_tokens * profile.usage_multiplier))
    finish = None if (affected and profile.omit_finish_reason) else finish_reason
    chunks.append(Chunk(_jitter(rng, interval, profile.jitter_cv), wrap({}, finish), "finish"))
    include_usage = bool((body.get("stream_options") or {}).get("include_usage"))
    if include_usage:
        chunks.append(
            Chunk(
                0.0,
                json.dumps(
                    {
                        "id": f"chatcmpl-{request_id}",
                        "object": "chat.completion.chunk",
                        "created": 0,
                        "model": model_id,
                        "choices": [],
                        "usage": {
                            "prompt_tokens": prompt_tokens,
                            "completion_tokens": usage_tokens,
                            "total_tokens": prompt_tokens + usage_tokens,
                        },
                    }
                ),
                "usage",
            )
        )
    done_emitted = not (affected and profile.omit_done) and drop_after is None
    if done_emitted:
        chunks.append(Chunk(0.0, "[DONE]", "done"))
    oracle = {
        "request_id": request_id,
        "http_status": 200,
        "profile": profile.name,
        "family": profile.family,
        "fault_applied": affected,
        "true_text": "".join(content_tokens),
        "true_tokens": true_tokens,
        "true_reasoning_tokens": len(reasoning_tokens),
        "true_token_offsets_s": emitted_times,
        "usage_completion_tokens": usage_tokens,
        "finish_reason": finish if drop_after is None else None,
        "done_emitted": done_emitted,
        "dropped": drop_after is not None,
        "stall_s": profile.stall_s if stall_positions else 0.0,
        "answer_is_correct": not (
            affected
            and (profile.wrong_text or profile.garble_text or profile.truncate_fraction is not None)
        ),
    }
    return StreamPlan(chunks, oracle, 200, drop_after)
