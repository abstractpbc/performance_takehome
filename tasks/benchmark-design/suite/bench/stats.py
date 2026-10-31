"""Seeded bootstrap uncertainty and the two-run comparison decision rule."""

import random
from collections.abc import Callable, Sequence
from typing import Any

from benchmarks.metrics import quantile


def bootstrap_diagnostics(
    *,
    resamples: int,
    confidence: float,
    family_size: int,
    n_a: int | None = None,
    n_b: int | None = None,
    p99: bool = False,
) -> dict[str, Any]:
    """O(1) descriptive diagnostics; never resample, veto or change a decision."""
    tail = (1 - confidence) / 2
    result: dict[str, Any] = {
        "diagnostic_only": True,
        "method": "percentile-bootstrap-independent-requests",
        "confidence_adjusted": confidence,
        "comparison_family_size": family_size,
        "bootstrap_resamples": resamples,
        "tail_probability": tail,
        "expected_resamples_per_tail": resamples * tail,
        "zero_based_percentile_order_index": (resamples - 1) * tail,
        "conditional_on_empirical_samples": True,
        "iid_exchangeable_requests_assumed": True,
        "repetitions_pooled_within_cell_load": True,
        "cluster_or_block_correction": False,
        "finite_sample_familywise_control_established": False,
        "measured_power_established": False,
        "caveat": "Sparse Monte Carlo tails can be seed-sensitive; more resamples do not "
        "add observations or establish population coverage, independence or measured power.",
    }
    if n_a is not None and n_b is not None:
        result.update(n_a=n_a, n_b=n_b)
    if p99:
        result["p99_data_support"] = {
            "quantile": 0.99,
            "nominal_observations_above_population_quantile_if_iid_continuous": {
                "a": None if n_a is None else n_a * 0.01,
                "b": None if n_b is None else n_b * 0.01,
            },
            "probability_sample_max_below_population_quantile_if_iid_continuous": {
                "a": 0.99**n_a if n_a else None,
                "b": 0.99**n_b if n_b else None,
            },
            "caveat": "Conditional IID-continuous illustration, not measured failure/power. "
            "Finite-sample p99 support can be sparse; more bootstrap draws do not fix it.",
        }
    return result


def percentile_ci(
    values: Sequence[float],
    statistic: Callable[[Sequence[float]], float],
    *,
    resamples: int,
    confidence: float,
    seed: int,
) -> dict[str, Any]:
    """Seeded percentile-bootstrap CI of ``statistic(values)``."""
    if not values:
        return {"point": None, "low": None, "high": None, "n": 0}
    values = sorted(values)  # record order must not influence the resampling
    rng = random.Random(seed)
    n = len(values)
    boots = sorted(
        statistic([values[rng.randrange(n)] for _ in range(n)]) for _ in range(resamples)
    )
    alpha = (1 - confidence) / 2
    return {
        "point": statistic(values),
        "low": quantile(boots, alpha),
        "high": quantile(boots, 1 - alpha),
        "n": n,
        "resamples": resamples,
        "confidence": confidence,
    }


def difference_ci(
    a: Sequence[float],
    b: Sequence[float],
    statistic: Callable[[Sequence[float]], float],
    *,
    resamples: int,
    confidence: float,
    seed: int,
) -> dict[str, Any]:
    """Bootstrap CI of ``statistic(b) - statistic(a)`` with independent resampling."""
    if not a or not b:
        return {"point": None, "low": None, "high": None, "n_a": len(a), "n_b": len(b)}
    a, b = sorted(a), sorted(b)
    rng = random.Random(seed)
    boots = sorted(
        statistic([b[rng.randrange(len(b))] for _ in range(len(b))])
        - statistic([a[rng.randrange(len(a))] for _ in range(len(a))])
        for _ in range(resamples)
    )
    alpha = (1 - confidence) / 2
    return {
        "point": statistic(b) - statistic(a),
        "low": quantile(boots, alpha),
        "high": quantile(boots, 1 - alpha),
        "n_a": len(a),
        "n_b": len(b),
        "confidence": confidence,
    }


def decide(
    diff: dict[str, Any],
    baseline_point: float | None,
    min_effect: float,
    *,
    relative: bool,
    higher_is_better: bool,
) -> dict[str, Any]:
    """Classify a difference CI as regression / improvement / no_difference / inconclusive.

    regression:    CI excludes zero on the bad side AND |point| exceeds min_effect
    improvement:   CI excludes zero on the good side AND |point| exceeds min_effect
    no_difference: the whole CI lies within +/- min_effect
    inconclusive:  anything else (CI straddles the min_effect boundary: underpowered)
    """
    if diff.get("point") is None or baseline_point is None:
        return {"decision": "inconclusive", "why": "insufficient data on one side"}
    scale = abs(baseline_point) if relative else 1.0
    if relative and scale == 0:
        return {"decision": "inconclusive", "why": "baseline is zero; relative effect undefined"}
    low, high, point = diff["low"] / scale, diff["high"] / scale, diff["point"] / scale
    sign = -1.0 if higher_is_better else 1.0
    worse_low, worse_high = sorted((low * sign, high * sign))
    worse_point = point * sign
    if worse_low > 0 and worse_point > min_effect:
        return {
            "decision": "regression",
            "why": f"CI excludes 0 and effect {worse_point:+.3f} > {min_effect}",
        }
    if worse_high < 0 and -worse_point > min_effect:
        return {
            "decision": "improvement",
            "why": f"CI excludes 0 and effect {worse_point:+.3f} < -{min_effect}",
        }
    if -min_effect <= worse_low and worse_high <= min_effect:
        return {
            "decision": "no_difference",
            "why": f"CI [{worse_low:+.3f}, {worse_high:+.3f}] within +/-{min_effect}",
        }
    return {
        "decision": "inconclusive",
        "why": f"CI [{worse_low:+.3f}, {worse_high:+.3f}] straddles the {min_effect} "
        "boundary (underpowered)",
    }


def bonferroni(confidence: float, comparisons: int) -> float:
    """Family-wise adjusted confidence level for ``comparisons`` simultaneous tests."""
    if comparisons <= 1:
        return confidence
    return 1 - (1 - confidence) / comparisons
