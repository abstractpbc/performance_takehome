"""Seeded, deterministic prompt generation with independently checkable expected properties.

Every prompt is derived from ``(prompt_seed, cell_id, repetition, index)`` so the same plan
always produces the same prompts without any dataset download. Each prompt carries an
``expected`` block that ``evaluate.py`` checks against the *generated text*; the checks do
not depend on the server, the runner, or any fixture label.
"""

import hashlib
import random
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from bench.tokens import count_tokens

_WORDS = (
    "system latency throughput tensor kernel cache scheduler batch token stream decode "
    "prefill memory bandwidth compute request queue deadline budget replica cluster "
    "gradient matrix vector shard pipeline buffer socket packet header payload parser "
    "metric sample quantile interval window burst stall jitter drift clock monotonic "
    "valid invalid report gate control noise signal variance estimate confidence power"
).split()
_TOPICS = (
    "why streaming responses feel faster",
    "how to size a GPU cluster",
    "what a benchmark should never trust",
    "how caches change latency",
    "why tail latency matters more than mean",
    "how to explain jitter to a product manager",
)


@dataclass(frozen=True)
class PromptSpec:
    """One concrete request: the messages to send and the properties the answer must have."""

    prompt_id: str
    source: str
    kind: str
    messages: list[dict[str, str]]
    max_tokens: int
    approx_prompt_tokens: int
    expected: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        """Serialize for the event record (expected properties travel with the request)."""
        return asdict(self)


def _rng(prompt_seed: int, *parts: Any) -> random.Random:
    digest = hashlib.sha256(":".join([str(prompt_seed), *map(str, parts)]).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _nonce(rng: random.Random) -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    return (
        "CODE-" + "".join(rng.choice(alphabet) for _ in range(4)) + f"-{rng.randrange(1000, 9999)}"
    )


def _filler(rng: random.Random, target_tokens: int) -> str:
    words: list[str] = []
    tokens = 0
    while tokens < target_tokens:
        batch = [rng.choice(_WORDS) for _ in range(8)]
        if rng.random() < 0.2:
            batch[-1] += "."
        words.extend(batch)
        tokens += count_tokens(" " + " ".join(batch))
    return " ".join(words)


def _chat(
    rng: random.Random, source_cfg: dict[str, Any], prompt_id: str, source: str
) -> PromptSpec:
    nonce = _nonce(rng)
    length = rng.randint(source_cfg["prompt_tokens"]["min"], source_cfg["prompt_tokens"]["max"])
    topic = rng.choice(_TOPICS)
    body = (
        f"Begin your reply with the exact code {nonce} on its own line. Then explain "
        f"{topic} in a few short paragraphs for a technical reader. Context notes: "
        + _filler(rng, max(length - 48, 8))
    )
    messages = [{"role": "user", "content": body}]
    expected = dict(source_cfg["expected"])
    expected["nonce"] = nonce
    return PromptSpec(
        prompt_id,
        source,
        "synthetic_chat",
        messages,
        source_cfg["max_tokens"],
        _count_messages(messages),
        expected,
    )


def _rag(
    rng: random.Random,
    source_cfg: dict[str, Any],
    prompt_id: str,
    source: str,
    forced_tokens: int | None = None,
) -> PromptSpec:
    length = forced_tokens or rng.randint(
        source_cfg["prompt_tokens"]["min"], source_cfg["prompt_tokens"]["max"]
    )
    vault = rng.choice(("Aurora", "Basalt", "Cobalt", "Delta", "Ember"))
    code = str(rng.randrange(10000, 99999))
    paragraphs = []
    budget = max(length - 80, 16)
    per_para = max(budget // 6, 8)
    for _ in range(6):
        paragraphs.append(_filler(rng, per_para))
    insert_at = rng.randrange(1, 5)
    paragraphs.insert(insert_at, f"Operational note: the access code for vault {vault} is {code}.")
    document = "\n\n".join(paragraphs)
    body = (
        "You are given an internal document. Answer the question using only the document.\n\n"
        f"DOCUMENT:\n{document}\n\nQUESTION: What is the access code for vault {vault}? "
        "Reply with the code and one short sentence."
    )
    messages = [{"role": "user", "content": body}]
    expected = dict(source_cfg["expected"])
    expected["must_contain"] = code
    return PromptSpec(
        prompt_id,
        source,
        "synthetic_rag",
        messages,
        source_cfg["max_tokens"],
        _count_messages(messages),
        expected,
    )


def _sequence(
    rng: random.Random, source_cfg: dict[str, Any], prompt_id: str, source: str
) -> PromptSpec:
    end = rng.randint(source_cfg["sequence_end"]["min"], source_cfg["sequence_end"]["max"])
    pad = rng.randint(source_cfg["prompt_tokens"]["min"], source_cfg["prompt_tokens"]["max"])
    body = (
        f"List every integer from 1 to {end} inclusive, in increasing order, one per line, "
        "with no other text before, between, or after the numbers. "
        + _filler(rng, max(pad - 40, 4))
    )
    messages = [{"role": "user", "content": body}]
    expected = dict(source_cfg["expected"])
    expected["sequence_end"] = end
    return PromptSpec(
        prompt_id,
        source,
        "integer_sequence",
        messages,
        source_cfg["max_tokens"],
        _count_messages(messages),
        expected,
    )


def _count_messages(messages: list[dict[str, str]]) -> int:
    return sum(count_tokens(m["content"]) + 4 for m in messages)


def make_prompt(
    plan: dict[str, Any],
    source: str,
    cell_id: str,
    repetition: int,
    index: int,
    *,
    forced_prompt_tokens: int | None = None,
) -> PromptSpec:
    """Build the deterministic prompt for one slot of a cell."""
    sources = plan["prompt_sources"]
    cfg = sources[source]
    rng = _rng(plan["seeds"]["prompt_seed"], source, cell_id, repetition, index)
    prompt_id = f"{cell_id}:r{repetition}:{index:04d}"
    kind = cfg["kind"]
    if kind == "mixture":
        population = [name for name, _ in cfg["components"]]
        weights = [weight for _, weight in cfg["components"]]
        chosen = rng.choices(population, weights=weights, k=1)[0]
        return make_prompt(plan, chosen, cell_id, repetition, index)
    if kind == "synthetic_chat":
        spec = _chat(rng, cfg, prompt_id, source)
    elif kind == "synthetic_rag":
        spec = _rag(rng, cfg, prompt_id, source, forced_prompt_tokens)
    elif kind == "integer_sequence":
        spec = _sequence(rng, cfg, prompt_id, source)
    else:
        raise ValueError(f"unknown prompt source kind: {kind}")
    if plan.get("cache_state", {}).get("request") == "unique_leading_nonce_v1":
        # Start user content with request-specific letters, before shared instructions.
        # This prevents identical user prefixes; common chat-template token state is
        # still not flushed, and no claim about engine block-cache internals is made.
        digest = hashlib.sha256(
            f"{plan['seeds']['prompt_seed']}:{cell_id}:{repetition}:{index}".encode()
        ).digest()
        nonce = "".join(chr(65 + nibble) for byte in digest for nibble in (byte >> 4, byte & 15))
        messages = [dict(message) for message in spec.messages]
        messages[0]["content"] = nonce + "\n" + messages[0]["content"]
        spec = replace(spec, messages=messages, approx_prompt_tokens=_count_messages(messages))
    return spec


def check_expected(text: str, expected: dict[str, Any]) -> list[str]:
    """Return the list of violated expected properties (empty means the text is valid)."""
    failures: list[str] = []
    if expected.get("echo_nonce") and expected.get("nonce") and expected["nonce"] not in text:
        failures.append("nonce_missing")
    if expected.get("must_contain") and expected["must_contain"] not in text:
        failures.append("planted_fact_missing")
    minimum = expected.get("min_output_chars")
    if minimum is not None and len(text.strip()) < minimum:
        failures.append("output_too_short")
    if expected.get("integer_sequence") and expected.get("sequence_end"):
        if not _sequence_ok(text, int(expected["sequence_end"])):
            failures.append("integer_sequence_broken")
    if "\ufffd" in text:
        failures.append("replacement_character_present")
    if _looks_garbled(text):
        failures.append("garbled_text")
    return failures


def _sequence_ok(text: str, end: int) -> bool:
    # Keep the existing whitespace normalization: surrounding line whitespace
    # and blank lines are ignored. Every nonempty line must be an ASCII decimal
    # integer; prose and fences cannot disappear just because all numbers exist.
    # Normalize leading zeros without int(), avoiding huge conversions and the
    # disagreement between Unicode isdigit() and Python's integer parser.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return end > 0 and len(lines) == end and all(
        line.isascii() and line.isdecimal() and line.lstrip("0") == str(index)
        for index, line in enumerate(lines, start=1)
    )


def _looks_garbled(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 20:
        return False
    letters = sum(ch.isalnum() or ch.isspace() for ch in stripped)
    if letters / len(stripped) < 0.6:
        return True
    words = stripped.split()
    if len(words) >= 12 and len(set(words)) / len(words) < 0.15:
        return True
    return False
