"""Interval arithmetic and the A/B decision. STATES.md level 5 is the spec."""

import math
from collections.abc import Sequence
from statistics import mean, variance
from typing import Any

MIN_REPETITIONS = 3
DIRECTIONS = {"ttft": 1, "fluidity": -1, "startup": 1}  # +1 means a higher value is worse.
MARGINS = {"ttft": "ttft_log", "fluidity": "fluidity_abs", "startup": "startup_log"}


def _central_probability(t: float, df: float) -> float:
    """P(|T| < t), by Simpson integration so fractional Welch df work with stdlib only."""
    scale = math.exp(math.lgamma((df + 1) / 2) - math.lgamma(df / 2)) / math.sqrt(df * math.pi)
    density = lambda x: scale * (1 + x * x / df) ** (-(df + 1) / 2)  # noqa: E731
    steps, width = 1000, t / 1000
    total = density(0.0) + density(t)
    for index in range(1, steps):
        total += density(index * width) * (4 if index % 2 else 2)
    return 2 * total * width / 3


def t_critical(confidence: float, df: float) -> float:
    low, high = 0.0, 1.0
    while _central_probability(high, df) < confidence:
        low, high = high, high * 2
    for _ in range(40):
        middle = (low + high) / 2
        if _central_probability(middle, df) < confidence:
            low = middle
        else:
            high = middle
    return high


def adjusted_confidence(confidence: float, comparisons: int) -> float:
    return 1 - (1 - confidence) / comparisons


def mean_interval(values: Sequence[float], confidence: float) -> tuple[float, float, float] | None:
    """Mean with a two-sided t-interval, or None with fewer than two values."""
    if len(values) < 2:
        return None
    center = mean(values)
    half = t_critical(confidence, len(values) - 1) * math.sqrt(variance(values) / len(values))
    return center, center - half, center + half


def welch_interval(
    current: Sequence[float], baseline: Sequence[float], confidence: float
) -> tuple[float, float, float]:
    """Interval on mean(current) - mean(baseline)."""
    delta = mean(current) - mean(baseline)
    left, right = variance(current) / len(current), variance(baseline) / len(baseline)
    error = left + right
    if error == 0:
        return delta, delta, delta
    df = error**2 / (left**2 / (len(current) - 1) + right**2 / (len(baseline) - 1))
    half = t_critical(confidence, df) * math.sqrt(error)
    return delta, delta - half, delta + half


def decide(worse_low: float, worse_high: float, margin: float) -> str:
    """Decide from an interval signed so that positive means worse."""
    if worse_low > margin:
        return "regression"
    if worse_high < -margin:
        return "improvement"
    if worse_low > -margin and worse_high < margin:
        return "equivalent"
    return "inconclusive"


def _identity(manifest: dict[str, Any]) -> tuple[Any, ...]:
    model = manifest.get("model") or {}
    return (
        model.get("revision"), manifest.get("plan_sha256"), manifest.get("mode"),
        manifest.get("filesystem_cache"),
    )


def _axis_decision(
    axis: str, cell: str | None, current: Any, baseline: Any, margin: float, confidence: float
) -> dict[str, Any]:
    entry: dict[str, Any] = {"axis": axis, "cell": cell, "margin": margin, "reasons": []}
    if current.state != "valid" or baseline.state != "valid":
        reasons = {*current.reasons, *baseline.reasons}
        return {**entry, "decision": "inconclusive", "reasons": sorted(reasons)}
    left, right = list(current.repetitions.values()), list(baseline.repetitions.values())
    if min(len(left), len(right)) < MIN_REPETITIONS:
        return {**entry, "decision": "inconclusive", "reasons": ["too_few_repetitions"]}
    delta, low, high = welch_interval(left, right, confidence)
    if DIRECTIONS[axis] > 0:
        decision = decide(low, high, margin)
    else:
        decision = decide(-high, -low, margin)
    return {**entry, "decision": decision, "delta": delta, "ci_low": low, "ci_high": high}


def compare(current: Any, baseline: Any, plan: dict[str, Any]) -> dict[str, Any]:
    """Compare two Analysis objects, one decision per axis and cell, with no blended score."""
    settings = plan["comparison"]
    mode = current.manifest.get("mode")
    cells = plan["modes"][mode]["cells"] if mode in plan["modes"] else []
    count = 2 * len(cells) + 1
    confidence = adjusted_confidence(settings["confidence"], count)
    rows = [(axis, cell) for cell in cells for axis in ("ttft", "fluidity")]
    rows.append(("startup", None))
    margins = settings["margins"]

    reason = None
    if current.state == "invalid" or baseline.state == "invalid":
        reason, decision = "run_invalid", "invalid"
    elif _identity(current.manifest) != _identity(baseline.manifest):
        reason, decision = "identity_mismatch", "invalid"
    elif current.state == "inconclusive" or baseline.state == "inconclusive":
        reason, decision = "run_inconclusive", "inconclusive"
    if reason is not None:
        decisions = [
            {"axis": axis, "cell": cell, "decision": decision, "reasons": [reason]}
            for axis, cell in rows
        ]
    else:
        decisions = [
            _axis_decision(
                axis, cell, current.axis(axis, cell), baseline.axis(axis, cell),
                margins[MARGINS[axis]], confidence,
            )
            for axis, cell in rows
        ]
    return {
        "confidence_per_decision": confidence,
        "comparisons": count,
        "decisions": decisions,
    }

