"""Analyze captured events into a report. Stdlib-only, plus the repo's benchmarks package."""

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from analysis.axes import Analysis, analyze  # noqa: E402
from analysis.compare import compare  # noqa: E402
from analysis.events import EvidenceError, Plan, Run, load_plan  # noqa: E402

COST_PHASES = {
    "launch": "launch", "warmup": "warmup", "measure": "requests", "drain": "drain",
    "cleanup": "cleanup",
}
EVIDENCE_FILES = (
    "manifest.json", "phases.jsonl", "launches.jsonl", "requests.jsonl", "sse.jsonl",
    "metrics.jsonl", "tokens.jsonl", "tokens.meta.json",
)


def evidence_identity(events: Path) -> dict[str, Any]:
    files = {}
    for name in EVIDENCE_FILES:
        path = events / name
        if path.is_file():
            data = path.read_bytes()
            files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
    return {"path": str(events), "files": files}


def _population_key(request: dict[str, Any]) -> tuple[str, str]:
    return request["cell"], str(request["repetition"])


def populations(analysis: Analysis) -> dict[str, Any]:
    """Counts per cell and repetition. planned = completed + failed + excluded + unfinished."""
    run = analysis.run
    if run is None:
        return {"cells": {}, "totals": None}
    results = analysis.assessment.results if analysis.assessment else []
    measured = [request for request in run.requests if request["phase"] == "measure"]
    offered = Counter(
        _population_key(request) for request in measured if request["t_sent"] is not None
    )
    tally: dict[tuple[str, str], Counter[str]] = {}
    for result in results:
        counts = tally.setdefault(_population_key(result.request), Counter())
        if "client_aborted" in result.reasons:
            continue
        counts[{"valid": "completed", "invalid": "failed"}.get(result.state, "excluded")] += 1
    cells: dict[str, dict[str, Any]] = {}
    totals: Counter[str] = Counter()
    for cell, repetitions in run.manifest["planned_requests"].items():
        cells[cell] = {}
        for repetition, planned in repetitions.items():
            key = (cell, repetition)
            counts = tally.get(key, Counter())
            row = {
                "planned": planned, "offered": offered[key],
                "completed": counts["completed"], "failed": counts["failed"],
                "excluded": counts["excluded"],
            }
            judged = row["completed"] + row["failed"] + row["excluded"]
            row["unfinished"] = max(planned - judged, 0)
            cells[cell][repetition] = row
            totals.update(row)
    return {"cells": cells, "totals": dict(totals), "judged": bool(results)}


def measured_cost(run: Run | None, plan: Plan) -> dict[str, Any] | None:
    if run is None:
        return None
    breakdown: Counter[str] = Counter()
    times = [event["t"] for event in run.launches]
    for phase in run.phases:
        if "t_start" in phase and "t_end" in phase:
            times += [phase["t_start"], phase["t_end"]]
            breakdown[COST_PHASES.get(phase["phase"], "other")] += (
                phase["t_end"] - phase["t_start"]
            )
    if not times:
        return None
    wall = max(times) - min(times)
    gpus = (run.manifest.get("hardware") or {}).get("gpus", 0)
    mode = plan.data["modes"].get(run.manifest["mode"], {})
    return {
        "wall_s": wall, "gpu_hours": wall * gpus / 3600, "gpus": gpus,
        "breakdown_s": dict(breakdown), "unaccounted_s": wall - sum(breakdown.values()),
        "max_wall_s": mode.get("max_wall_s"),
        "over_budget": wall > mode["max_wall_s"] if mode else None,
    }


def _axis_json(axis) -> dict[str, Any]:
    return {
        "state": axis.state, "reasons": list(axis.reasons),
        "repetitions": axis.repetitions, **axis.summary,
    }


def _format_value(axis_name: str, axis) -> str:
    if axis.summary.get("value") is None:
        return ",".join(axis.reasons) or "-"
    value = axis.summary["value"]
    if axis_name == "ftgr":
        return f"{value:.1f} tok/s"
    low, high = axis.summary.get("ci_low"), axis.summary.get("ci_high")
    if axis_name == "fluidity":
        return f"{value:.3f} ({max(low, 0.0):.3f}-{min(high, 1.0):.3f})"
    return f"{value * 1000:.0f} ms ({low * 1000:.0f}-{high * 1000:.0f})"


def _format_decision(decision: dict[str, Any] | None) -> str:
    if decision is None:
        return "-"
    if "delta" not in decision:
        return f"{decision['decision']} ({','.join(decision['reasons'])})"
    return (
        f"{decision['decision']} {decision['delta']:+.3f} "
        f"({decision['ci_low']:+.3f}..{decision['ci_high']:+.3f})"
    )


def summary(analysis: Analysis, comparison: dict[str, Any] | None) -> dict[str, Any]:
    decisions = {
        (item["axis"], item["cell"]): item for item in (comparison or {}).get("decisions", [])
    }
    rows = []
    for cell, axes in analysis.cells.items():
        for name in ("ttft", "fluidity", "ftgr"):
            rows.append({
                "cell": cell, "axis": name, "state": axes[name].state,
                "value": _format_value(name, axes[name]),
                "vs_baseline": _format_decision(decisions.get((name, cell))),
            })
    if analysis.startup is not None:
        rows.append({
            "cell": "(all)", "axis": "startup", "state": analysis.startup.state,
            "value": _format_value("startup", analysis.startup),
            "vs_baseline": _format_decision(decisions.get(("startup", None))),
        })
    counts = analysis.assessment.request_reasons if analysis.assessment else {}
    return {
        "state": analysis.state,
        "mode": analysis.manifest.get("mode"),
        "cache": analysis.manifest.get("filesystem_cache"),
        "reasons": [
            f"{reason}({counts[reason]})" if reason in counts else reason
            for reason in analysis.reasons
        ],
        "axes": rows,
        "diagnostics": analysis.diagnostics,
    }


def build_report(
    events: Path, plan: Plan, workload: Path, baseline: Path | None
) -> tuple[dict[str, Any], Analysis]:
    analysis = analyze(events, plan)
    comparison = None
    if baseline is not None:
        comparison = compare(analysis, analyze(baseline, plan), plan.data)
    assessment = analysis.assessment
    population = populations(analysis)
    cost = measured_cost(analysis.run, plan) if analysis.run else None
    overview = summary(analysis, comparison)
    overview.update(requests=population["totals"], cost=cost)
    report = {
        "summary": overview,
        "schema_version": 1,
        "identity": {
            "manifest": analysis.manifest,
            "plan": {
                "path": str(workload), "status": plan.data["status"],
                "sha256": hashlib.sha256(workload.read_bytes()).hexdigest(),
                "deadlines": plan.data["deadlines"],
            },
            "evidence": evidence_identity(events),
        },
        "populations": population,
        "validity": {
            "state": analysis.state,
            "reasons": list(analysis.reasons),
            "request_reasons": assessment.request_reasons if assessment else {},
            "failure_fractions": assessment.failure_fractions if assessment else {},
            "output_stability": None if assessment is None or assessment.stability is None else {
                "state": assessment.stability.state,
                "reasons": list(assessment.stability.reasons),
                "observed": assessment.stability.observed,
                "diverged": assessment.stability.diverged,
            },
        },
        "axes": {
            "confidence_per_interval": analysis.confidence,
            "cells": {
                cell: {name: _axis_json(axis) for name, axis in axes.items()}
                for cell, axes in analysis.cells.items()
            },
            "startup": _axis_json(analysis.startup) if analysis.startup else None,
        },
        "comparison": comparison,
        "cost": cost,
    }
    return report, analysis


def _diagnostic_line(cell: str, row: dict[str, Any]) -> str:
    def shown(value: float | None, scale: float, unit: str) -> str:
        return "-" if value is None else f"{value * scale:.1f}{unit}"

    tpot, rate = row["tpot_s"], row["request_tps"]
    return (f"{cell:16}{shown(tpot['p50'], 1000, ' ms'):12}{shown(tpot['p90'], 1000, ' ms'):12}"
            f"{shown(rate['p50'], 1, ''):14}{shown(row['system_tps'], 1, '')}")


def render(overview: dict[str, Any]) -> str:
    reasons = ", ".join(overview["reasons"]) or "none"
    lines = [
        f"RUN {overview['state']}  mode={overview['mode']}  cache={overview['cache']}",
        f"reasons: {reasons}", "",
        f"{'CELL':16}{'AXIS':10}{'STATE':14}{'VALUE':30}VS BASELINE",
    ]
    lines += [
        f"{row['cell']:16}{row['axis']:10}{row['state']:14}{row['value']:30}{row['vs_baseline']}"
        for row in overview["axes"]
    ]
    if overview["diagnostics"]:
        lines += ["", "diagnostics only, never gated or compared",
                  f"{'CELL':16}{'TPOT p50':12}{'TPOT p90':12}{'REQ TPS p50':14}SYSTEM TPS"]
        lines += [_diagnostic_line(cell, row) for cell, row in overview["diagnostics"].items()]
    requests, cost = overview["requests"], overview["cost"]
    if requests:
        lines += ["", "requests: " + ", ".join(f"{k} {v}" for k, v in requests.items())]
    if cost:
        parts = ", ".join(f"{k} {v / 3600:.2f}" for k, v in cost["breakdown_s"].items())
        lines.append(f"cost: {cost['wall_s'] / 3600:.2f} h wall, "
                     f"{cost['gpu_hours']:.2f} GPU-h ({parts})")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, help="events directory to compare against")
    args = parser.parse_args()
    try:
        plan = load_plan(args.workload)
    except (EvidenceError, KeyError, TypeError) as exc:
        print(f"unusable workload: {exc}", file=sys.stderr)
        return 2
    report, _ = build_report(args.events, plan, args.workload, args.baseline)
    args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(render(report["summary"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
