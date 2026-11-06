"""Metric axes and the final run state. STATES.md levels 3 and 4 are the spec."""

import math
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, median
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from analysis.compare import adjusted_confidence, mean_interval  # noqa: E402
from analysis.events import EvidenceError, Plan, Run, load_run  # noqa: E402
from analysis.gates import (  # noqa: E402
    INCONCLUSIVE,
    INVALID,
    VALID,
    Assessment,
    RequestResult,
    assess_run,
    worst,
)

from benchmarks.metrics import (  # noqa: E402
    fluid_token_generation_rate,
    fluidity,
    quantile,
    token_intervals,
)

PRIMARY_QUANTILE = 0.9
MALFORMED = (EvidenceError, KeyError, TypeError, ValueError, AttributeError, IndexError)


@dataclass
class Axis:
    state: str
    reasons: tuple[str, ...] = ()
    repetitions: dict[str, float] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)


def _bad_axis() -> Axis:
    return Axis(INVALID, ("failure_rate",))


@dataclass
class Analysis:
    state: str
    reasons: tuple[str, ...]
    manifest: dict[str, Any]
    run: Run | None
    assessment: Assessment | None
    cells: dict[str, dict[str, Axis]] = field(default_factory=dict)
    startup: Axis | None = None
    confidence: float | None = None
    diagnostics: dict[str, dict[str, Any]] = field(default_factory=dict)

    def axis(self, name: str, cell: str | None) -> Axis:
        if name == "startup":
            return self.startup or Axis(INCONCLUSIVE, ("startup_underpowered",))
        return self.cells.get(cell, {}).get(name, Axis(INCONCLUSIVE, ("quantile_underpowered",)))


def counted(result: RequestResult) -> bool:
    """Canaries and anchors repeat bytes, and excluded requests have no verdict."""
    request = result.request
    if request["is_canary"] or request["is_anchor"]:
        return False
    return result.state in (VALID, INVALID)


def powered(count: int, q: float, minimum: int) -> bool:
    return round(count * (1 - q), 9) >= minimum


def censored_ttft(result: RequestResult, timeout: float) -> float:
    ttft = result.ttft_s
    return timeout if ttft is None else ttft


def fluidity_index(result: RequestResult, plan: Plan) -> float:
    if result.failed:
        return 0.0
    deadlines = plan.data["deadlines"]
    sent = result.request["t_sent"]
    intervals = token_intervals([t - sent for t in result.token_times])
    return fluidity(intervals, deadlines["ttft_prefill_s"], deadlines["decode_s"]).index


def _groups(results: list[RequestResult]) -> dict[str, list[RequestResult]]:
    grouped: dict[str, list[RequestResult]] = defaultdict(list)
    for result in results:
        grouped[str(result.request["repetition"])].append(result)
    return dict(grouped)


def _power_reasons(groups: dict[str, list[RequestResult]], plan: Plan) -> tuple[str, ...]:
    """Only the per-repetition p90 that comparisons use gates a cell.

    Other quantiles, such as p99, are reported when powered and left empty otherwise.
    """
    minimum = plan.data["gates"]["min_valid_per_quantile"]
    enough = len(groups) >= 2 and all(
        powered(len(group), PRIMARY_QUANTILE, minimum) for group in groups.values()
    )
    return () if enough else ("quantile_underpowered",)


def _log_summary(values: list[float], confidence: float) -> dict[str, Any]:
    interval = mean_interval(values, confidence)
    if interval is None:
        return {}
    center, low, high = interval
    return {"value": math.exp(center), "ci_low": math.exp(low), "ci_high": math.exp(high)}


def _quantiles(values: list[float], mode: dict[str, Any], minimum: int) -> dict[str, float | None]:
    return {
        f"p{round(q * 100)}": quantile(values, q) if powered(len(values), q, minimum) else None
        for q in mode["quantiles"]
    }


def _ttft_axis(groups, plan, mode, state, reasons, confidence) -> Axis:
    timeout = plan.data["deadlines"]["request_timeout_s"]
    minimum = plan.data["gates"]["min_valid_per_quantile"]
    repetitions = {
        rep: math.log(max(
            quantile([censored_ttft(result, timeout) for result in group], PRIMARY_QUANTILE),
            1e-9,
        ))
        for rep, group in groups.items()
    }
    pooled = [
        censored_ttft(result, timeout) for group in groups.values() for result in group
    ]
    first_content = [
        result.ttft_content_s for group in groups.values() for result in group
        if result.ttft_content_s is not None
    ]
    summary = {
        **_log_summary(list(repetitions.values()), confidence),
        "unit": "s", "statistic": "geometric mean of repetition p90s",
        "population": len(pooled), "quantiles": _quantiles(pooled, mode, minimum),
        "first_content_quantiles": _quantiles(first_content, mode, minimum),
        "censored": sum(result.ttft_s is None for g in groups.values() for result in g),
        "censored_at_s": timeout,
    }
    return Axis(state, reasons, repetitions, summary)


def _fluidity_axis(groups, plan, mode, state, reasons, confidence) -> Axis:
    repetitions = {
        rep: mean(fluidity_index(result, plan) for result in group)
        for rep, group in groups.items()
    }
    interval = mean_interval(list(repetitions.values()), confidence)
    summary: dict[str, Any] = {
        "unit": "index", "statistic": "mean of repetition mean fluidity index",
        "population": sum(len(group) for group in groups.values()),
        "failed_charged_zero": sum(result.failed for g in groups.values() for result in g),
        "prefill_deadline_s": plan.data["deadlines"]["ttft_prefill_s"],
        "decode_deadline_s": plan.data["deadlines"]["decode_s"],
    }
    if interval is not None:
        summary.update(value=interval[0], ci_low=interval[1], ci_high=interval[2])
    return Axis(state, reasons, repetitions, summary)


def _ftgr_axis(groups, state, invalid: bool) -> Axis:
    traces = [
        token_intervals([t - result.request["t_sent"] for t in result.token_times])
        for group in groups.values() for result in group
        if not result.failed and len(result.token_times) >= 2
    ]
    rate = fluid_token_generation_rate(traces) if traces else None
    summary = {"unit": "tokens/s", "value": rate, "eligible_traces": len(traces)}
    if invalid:
        return Axis(INVALID, ("failure_rate",), summary=summary)
    if rate is None:
        return Axis(INCONCLUSIVE, ("ftgr_unresolved",), summary=summary)
    return Axis(worst(state), summary=summary)


def cell_axes(
    cell: str, assessment: Assessment, plan: Plan, mode: dict[str, Any], confidence: float
) -> dict[str, Axis]:
    results = [
        result for result in assessment.results
        if result.request["cell"] == cell and counted(result)
    ]
    groups = _groups(results)
    limit = plan.data["gates"]["max_failed_fraction"]
    invalid = assessment.failure_fractions.get(cell, 0.0) > limit
    reasons = _power_reasons(groups, plan)
    state = INVALID if invalid else INCONCLUSIVE if reasons else VALID
    final = ("failure_rate",) if invalid else reasons
    if not groups:
        return {"ttft": _axis_or_empty(state, final), "fluidity": _axis_or_empty(state, final),
                "ftgr": _axis_or_empty(state, final)}
    return {
        "ttft": _ttft_axis(groups, plan, mode, state, final, confidence),
        "fluidity": _fluidity_axis(groups, plan, mode, state, final, confidence),
        "ftgr": _ftgr_axis(groups, VALID, invalid),
    }


def cell_diagnostics(
    cell: str, assessment: Assessment, plan: Plan, mode: dict[str, Any]
) -> dict[str, Any]:
    """TPOT and TPS for reading a report. These never affect a state or a decision."""
    served = [
        result for result in assessment.results
        if result.request["cell"] == cell and result.request["phase"] == "measure"
        and not result.failed and result.token_times
    ]
    tpots = []
    for result in filter(counted, served):
        span = result.token_times[-1] - result.token_times[0]
        if span > 0:
            tpots.append(span / (len(result.token_times) - 1))
    system = []
    for group in _groups(served).values():
        start = min(result.request["t_sent"] for result in group)
        end = max(result.token_times[-1] for result in group)
        if end > start:
            system.append(sum(len(result.token_times) for result in group) / (end - start))
    minimum = plan.data["gates"]["min_valid_per_quantile"]
    return {
        "tpot_s": _quantiles(tpots, mode, minimum),
        "request_tps": _quantiles([1 / tpot for tpot in tpots], mode, minimum),
        "system_tps": mean(system) if system else None,
        "population": len(tpots),
    }


def _axis_or_empty(state: str, reasons: tuple[str, ...]) -> Axis:
    return Axis(state, reasons)


def startup_axis(assessment: Assessment, confidence: float) -> Axis:
    part = assessment.startup
    samples = part.samples_s
    repetitions = {str(index): math.log(sample) for index, sample in enumerate(samples)}
    summary: dict[str, Any] = {"unit": "s", "statistic": "geometric mean of launches",
                               "population": len(samples)}
    if samples:
        summary.update(
            _log_summary(list(repetitions.values()), confidence),
            min=min(samples), median=median(samples), max=max(samples),
        )
    return Axis(part.state, part.reasons, repetitions, summary)


def analyze(events: Path, plan: Plan) -> Analysis:
    """Analyze one run. Unusable evidence fails closed instead of crashing or scoring."""
    try:
        run = load_run(events)
    except EvidenceError:
        return Analysis(INVALID, ("evidence_missing",), {}, None, None)
    try:
        return _analyze(run, plan)
    except MALFORMED:
        return Analysis(INVALID, ("evidence_missing",), run.manifest, run, None)


def _analyze(run: Run, plan: Plan) -> Analysis:
    assessment = assess_run(run, plan)
    if assessment.startup is None:
        return Analysis(assessment.state, assessment.reasons, run.manifest, run, assessment)
    mode = plan.data["modes"][run.manifest["mode"]]
    confidence = adjusted_confidence(
        plan.data["comparison"]["confidence"], 2 * len(mode["cells"]) + 1
    )
    cells = {cell: cell_axes(cell, assessment, plan, mode, confidence) for cell in mode["cells"]}
    startup = startup_axis(assessment, confidence)
    required = [axis for axes in cells.values() for axis in (axes["ttft"], axes["fluidity"])]
    state = worst(assessment.state, *(axis.state for axis in required))
    reasons = dict.fromkeys(assessment.reasons)
    for axis in required:
        reasons.update(dict.fromkeys(axis.reasons))
    diagnostics = {cell: cell_diagnostics(cell, assessment, plan, mode) for cell in mode["cells"]}
    return Analysis(state, tuple(reasons), run.manifest, run, assessment, cells, startup,
                    confidence, diagnostics)
