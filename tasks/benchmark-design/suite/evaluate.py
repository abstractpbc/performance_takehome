"""Graded analysis for Task 1: turn raw events into a report with an explicit status.

    python tasks/benchmark-design/suite/evaluate.py --events DIR --workload PLAN --out REPORT \\
        [--compare OTHER_DIR] [--adapter native] [--summary SUMMARY.md]

The evaluator reads only ``run.json`` and ``requests.jsonl`` (via ``bench.records``); it
never looks at fixture names, labels or oracles. Missing evidence is never a pass: the
report's top-level ``status`` is ``valid``, ``invalid`` or ``inconclusive`` with reasons.
Fluidity, minimum decode deadline and FTGR come from ``benchmarks.metrics`` unchanged.
"""

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench import SUITE_VERSION  # noqa: E402
from bench.gates import Verdict, judge  # noqa: E402
from bench.identity import calibration_identity_errors, comparison_identity_errors  # noqa: E402
from bench.model_tokens import ModelTokenizer  # noqa: E402
from bench.plan import load_plan, plan_digest, prefill_deadline_s, resolve_mode  # noqa: E402
from bench.records import Evidence, EvidenceError, load_events  # noqa: E402
from bench.stats import (  # noqa: E402
    bonferroni,
    bootstrap_diagnostics,
    decide,
    difference_ci,
    percentile_ci,
)

from benchmarks.metrics import (  # noqa: E402
    fluid_token_generation_rate,
    fluidity,
    minimum_decode_deadline,
    quantile,
    token_intervals,
)

REPORT_SCHEMA_VERSION = "1.0.0"
CATEGORIES = ("completed", "failed", "unfinished", "evidence_invalid")


def _p(values: list[float], q: float) -> float | None:
    return quantile(values, q) if values else None


def _group_key(record: dict[str, Any]) -> str:
    return f"{record['cell_id']}|{record['load_label']}"


class Evaluator:
    """Evaluates one events directory against a plan."""

    def __init__(
        self,
        plan: dict[str, Any],
        evidence: Evidence,
        plan_digest_hex: str | None = None,
        tokenizer: ModelTokenizer | None = None,
    ) -> None:
        self.plan = plan
        self.plan_digest_hex = plan_digest_hex
        self.ev = evidence
        self.tokenizer = tokenizer
        self.stats_cfg = plan["statistics"]
        self.seed = plan["seeds"]["bootstrap_seed"]
        self.reasons: list[str] = []
        self.inconclusive: list[str] = []
        self.verdicts: dict[str, Verdict] = {}
        self.by_id = {str(r.raw.get("request_id")): r for r in evidence.requests}
        self.records = {r.request_id: r.raw for r in evidence.requests}
        # Only explicit fixture mode deliberately exercises partial cell sets.
        # Every other known mode, including calibration, must cover the actual
        # plan rather than trust a runner's omitted units or reduced totals.
        mode = evidence.run.mode
        self.expected_units = (
            {u.unit_id: u for u in resolve_mode(plan, mode)}
            if mode != "fixture" and mode in plan["modes"]
            else {}
        )

    def _ci(self, values: list[float], stat: Any, salt: int) -> dict[str, Any]:
        return percentile_ci(
            values,
            stat,
            resamples=self.stats_cfg["bootstrap_resamples"],
            confidence=self.stats_cfg["confidence"],
            seed=self.seed + salt,
        )

    def run_gates(self) -> None:
        """Run-level evidence checks (schema, run status, client lag, engine identity)."""
        run = self.ev.run.raw
        if self.ev.schema_errors:
            self.reasons.append(
                f"schema: {len(self.ev.schema_errors)} violation(s), "
                f"e.g. {self.ev.schema_errors[0]}"
            )
        if run.get("status") != "completed":
            self.reasons.append(f"run status is {run.get('status')!r}, not 'completed'")
        if run.get("clock", {}).get("source") != "time.monotonic":
            self.reasons.append("clock source is not time.monotonic")
        lag = run.get("client_integrity") or {}
        limit = self.plan["client_integrity"]["max_event_loop_lag_s"]
        if lag.get("event_loop_lag_max_s") is None or lag.get("samples", 0) == 0:
            if self.ev.requests:
                self.reasons.append(
                    "client event-loop lag was not measured (max or samples missing)"
                )
        elif lag["event_loop_lag_max_s"] > limit:
            self.reasons.append(
                f"client event-loop lag {lag['event_loop_lag_max_s']:.3f}s exceeds {limit}s: "
                "timings are client-owned"
            )
        engine = run.get("engine") or {}
        if engine.get("kind") != "mock" and self.tokenizer is None:
            self.reasons.append("real evaluation requires a verified model tokenizer snapshot")
        if self.tokenizer is not None:
            if self.tokenizer.role != self.plan["target"]["model_role"]:
                self.reasons.append("tokenizer model role differs from workload plan")
            if run.get("tokenizer") != self.tokenizer.identity:
                self.reasons.append(
                    "run tokenizer snapshot/runtime identity differs from evaluator"
                )
            if engine.get("lock_model") != self.tokenizer.model:
                self.reasons.append("run model pin differs from selected tokenizer")
        for key in ("model_id", "lock_digest"):
            if not engine.get(key) or engine.get(key) == "unknown":
                self.reasons.append(f"engine identity incomplete: {key} missing")
        self.reasons.extend(
            calibration_identity_errors(self.plan.get("calibration"), run, self.tokenizer)
        )
        recorded_digest = (run.get("plan") or {}).get("digest")
        if self.plan_digest_hex and recorded_digest != self.plan_digest_hex:
            self.reasons.append(
                "run was recorded under a different workload plan (digest mismatch); refusing"
            )
        plan_digest = (run.get("plan") or {}).get("plan_id")
        if plan_digest != self.plan["plan_id"]:
            self.reasons.append(
                f"run was recorded under plan {plan_digest!r}, "
                f"evaluated with {self.plan['plan_id']!r}"
            )
        if run["mode"] not in self.plan["modes"]:
            self.reasons.append(f"unknown run mode {run['mode']!r}")
        units = {u["unit_id"]: u for u in self.ev.run.units}
        if len(units) != len(self.ev.run.units):
            self.reasons.append("run contains duplicate unit_id metadata")
        for unit_id, unit in units.items():
            if self.expected_units and unit_id not in self.expected_units:
                self.reasons.append(f"unexpected scheduled unit {unit_id!r}")
                continue
            expected = self.expected_units.get(unit_id)
            if expected is not None:
                for key, value in (
                    ("planned_requests", expected.requests),
                    ("planned_warmup", expected.warmup),
                    ("cell_id", expected.cell_id),
                    ("load_label", expected.load_label),
                    ("repetition", expected.repetition),
                    ("arrival_type", expected.arrival_type),
                    ("load", expected.load),
                    ("role", expected.role),
                    ("prompt_source", expected.prompt_source),
                    ("deadline_profile", expected.deadline_profile),
                ):
                    if unit[key] != value:
                        self.reasons.append(f"unit {unit_id!r}: {key} differs from workload plan")
        for rec in self.ev.requests:
            raw = rec.raw
            calibration = self.plan.get("calibration")
            if calibration and raw["phase"] == "measure":
                low, high = calibration["fit"]["length_domain"]
                if not low <= raw["prompt"]["approx_prompt_tokens"] <= high:
                    self.reasons.append(
                        f"request {rec.request_id}: prompt length outside frozen calibration domain"
                    )
            if raw["phase"] == "startup_probe":
                continue
            meta = units.get(raw["unit_id"])
            if meta is None:
                self.reasons.append(f"request {rec.request_id}: undeclared unit_id")
                continue
            for key in ("cell_id", "load_label", "repetition", "arrival_type", "load", "role"):
                if raw[key] != meta[key]:
                    self.reasons.append(
                        f"request {rec.request_id}: {key} differs from unit metadata"
                    )
            source = self.plan["prompt_sources"].get(meta["prompt_source"], {})
            allowed_sources = (
                {c[0] for c in source["components"]}
                if source.get("kind") == "mixture"
                else {meta["prompt_source"]}
            )
            if raw["prompt"]["source"] not in allowed_sources:
                self.reasons.append(
                    f"request {rec.request_id}: prompt source differs from unit metadata"
                )

    REQUIRED_KEYS = (
        "request_id",
        "phase",
        "unit_id",
        "cell_id",
        "client_tokens",
        "timestamps_mono_s",
        "events",
    )

    def _malformed(self, raw: dict[str, Any]) -> bool:
        return any(key not in raw for key in self.REQUIRED_KEYS)

    def judge_all(self) -> None:
        errors_by_request: dict[str, list[str]] = defaultdict(list)
        for err in self.ev.schema_errors:
            if err.startswith("request "):
                rid = err.split(" ", 1)[1].split(":", 1)[0]
                errors_by_request[rid].append(err)
        for rec in self.ev.requests:
            if self._malformed(rec.raw):
                self.reasons.append(
                    f"record {rec.raw.get('request_id', '?')!r} is malformed "
                    "(required key missing); not scored"
                )
                continue
            self.verdicts[rec.request_id] = judge(
                rec.raw, self.plan, errors_by_request.get(rec.request_id, []), self.tokenizer
            )
        invalid = [v for v in self.verdicts.values() if v.category == "evidence_invalid"]
        if invalid:
            sample = invalid[0]
            self.reasons.append(
                f"{len(invalid)} request(s) with untrustworthy evidence, "
                f"e.g. {sample.request_id}: {sample.reasons[:2]}"
            )

    def population(self) -> dict[str, Any]:
        """planned / offered / completed / failed / unfinished / evidence_invalid per unit."""
        planned = {u["unit_id"]: u for u in self.ev.run.units}
        table: dict[str, dict[str, Any]] = {}
        for unit_id, meta in planned.items():
            table[unit_id] = {
                "planned": meta["planned_requests"],
                "offered": 0,
                "warmup_offered": 0,
                "warmup_planned": meta["planned_warmup"],
                **{c: 0 for c in CATEGORIES},
                "cell_id": meta["cell_id"],
                "load_label": meta["load_label"],
                "role": meta["role"],
            }
        for unit_id, unit in self.expected_units.items():
            table.setdefault(
                unit_id,
                {
                    "planned": unit.requests,
                    "offered": 0,
                    "warmup_offered": 0,
                    "warmup_planned": unit.warmup,
                    **{c: 0 for c in CATEGORIES},
                    "cell_id": unit.cell_id,
                    "load_label": unit.load_label,
                    "role": unit.role,
                },
            )
        for rec in self.ev.requests:
            raw = rec.raw
            if self._malformed(raw):
                continue
            if raw["phase"] == "startup_probe":
                continue
            row = table.setdefault(
                raw["unit_id"],
                {
                    "planned": 0,
                    "offered": 0,
                    "warmup_offered": 0,
                    "warmup_planned": 0,
                    **{c: 0 for c in CATEGORIES},
                    "cell_id": raw["cell_id"],
                    "load_label": raw["load_label"],
                    "role": raw["role"],
                },
            )
            if raw["phase"] == "warmup":
                row["warmup_offered"] += 1
                continue
            row["offered"] += 1
            row[self.verdicts[rec.request_id].category] += 1
        for unit_id, row in table.items():
            if row["warmup_offered"] < row["warmup_planned"]:
                self.inconclusive.append(
                    f"unit {unit_id} has incomplete warmup evidence: "
                    f"{row['warmup_offered']} offered < {row['warmup_planned']} planned"
                )
            row["not_offered"] = max(0, row["planned"] - row["offered"])
            if row["not_offered"]:
                self.inconclusive.append(
                    f"unit {unit_id} has incomplete population evidence: "
                    f"{row['offered']} offered < {row['planned']} planned"
                )
            if row["offered"] > row["planned"] > 0:
                self.reasons.append(
                    f"unit {unit_id} offered {row['offered']} > planned {row['planned']}"
                )
        totals = {
            k: sum(r[k] for r in table.values())
            for k in ("planned", "offered", "not_offered", *CATEGORIES)
        }
        return {"units": table, "totals": totals}

    def _measure_groups(self) -> dict[str, list[tuple[dict[str, Any], Verdict]]]:
        groups: dict[str, list[tuple[dict[str, Any], Verdict]]] = defaultdict(list)
        for rec in self.ev.requests:
            if self._malformed(rec.raw):
                continue
            if rec.raw["phase"] == "measure":
                groups[_group_key(rec.raw)].append((rec.raw, self.verdicts[rec.request_id]))
        return groups

    def _fluidity_for(self, raw: dict[str, Any], v: Verdict, tier_s: float) -> float | None:
        """Etalon fluidity index for one request (0 for failed/unfinished, None if unmeasurable)."""
        if v.category != "completed":
            return 0.0
        if len(v.token_times_s) < 1:
            return None
        intervals = token_intervals(v.token_times_s)
        deadline = prefill_deadline_s(self.plan, v.prompt_tokens)
        return fluidity(intervals, deadline, tier_s).index

    def cells(self) -> tuple[dict[str, Any], dict[str, Any]]:
        """Per cell x load: TTFT distribution, fluidity per tier, min decode deadline, FTGR."""
        tiers = self.plan["deadlines"]["decode_tiers"]
        slo = self.plan["deadlines"]["fluidity_slo"]
        min_n = self.stats_cfg["min_requests_per_cell"]
        out: dict[str, Any] = {}
        samples: dict[str, Any] = {}
        for salt, (key, items) in enumerate(sorted(self._measure_groups().items())):
            cell_id = items[0][0]["cell_id"]
            profile = items[0][0]["prompt"]["source"]
            cell_cfg = next((c for c in self.plan["cells"] if c["id"] == cell_id), {})
            ttft_slo = self.plan["deadlines"]["ttft_slo_s"].get(
                cell_cfg.get("deadline_profile", ""), None
            )
            completed = [(r, v) for r, v in items if v.category == "completed"]
            ttfts = sorted(v.ttft_s for _, v in completed if v.ttft_s is not None)
            first_bytes = sorted(v.first_byte_s for _, v in completed if v.first_byte_s is not None)
            prefill_hits = [
                v.ttft_s <= prefill_deadline_s(self.plan, v.prompt_tokens)
                for _, v in completed
                if v.ttft_s is not None
            ]
            row: dict[str, Any] = {
                "cell_id": cell_id,
                "load_label": items[0][0]["load_label"],
                "prompt_source": profile,
                "role": items[0][0]["role"],
                "offered": len(items),
                "completed": len(completed),
                "categories": dict(Counter(v.category for _, v in items)),
                "rejection_reasons": dict(
                    Counter(r.split(":", 1)[0] for _, v in items for r in v.reasons)
                ),
                "flags": dict(Counter(f for _, v in items for f in v.flags)),
                "enough_evidence": len(items)
                - sum(1 for _, v in items if v.category == "evidence_invalid")
                >= min_n,
                "latency_sample_sufficient": len(completed) >= min_n,
            }
            if not row["enough_evidence"]:
                self.inconclusive.append(
                    f"{key}: {row['offered'] - row['categories'].get('evidence_invalid', 0)} "
                    f"trustworthy requests < min_requests_per_cell={min_n}"
                )
            row["ttft_s"] = {
                "n": len(ttfts),
                "p50": self._ci(ttfts, lambda x: quantile(x, 0.5), salt * 10 + 1),
                "p90": self._ci(ttfts, lambda x: quantile(x, 0.9), salt * 10 + 2),
                "p99": _p(ttfts, 0.99),
                "max": max(ttfts) if ttfts else None,
                "first_byte_p50": _p(first_bytes, 0.5),
                "prefill_deadline_hit_rate": (sum(prefill_hits) / len(prefill_hits))
                if prefill_hits
                else None,
                "ttft_slo_s": ttft_slo,
                "ttft_slo_hit_rate": (sum(t <= ttft_slo for t in ttfts) / len(ttfts))
                if (ttfts and ttft_slo)
                else None,
            }
            tokens_per_event = sorted(
                v.content_tokens / v.content_events for _, v in completed if v.content_events
            )
            row["token_evidence"] = {
                "resolution": sorted({r["client_tokens"]["resolution"] for r, _ in items}),
                "tokens_per_delta_mean": statistics.fmean(tokens_per_event)
                if tokens_per_event
                else None,
                "tokens_per_delta_max": max(tokens_per_event) if tokens_per_event else None,
                "usage_reported": sum(1 for _, v in items if v.usage_completion_tokens is not None),
                "grapheme_boundary_splits": sum(v.grapheme_splits for _, v in items),
                "usage_discrepancies": sum(
                    1 for _, v in items if any(r.startswith("usage_discrepancy") for r in v.reasons)
                ),
            }
            row["fluidity"] = {}
            for tier_name, tier in tiers.items():
                indices = [self._fluidity_for(r, v, tier["deadline_s"]) for r, v in items]
                measurable = sorted(i for i in indices if i is not None)
                meets = [i >= slo["min_index"] for i in measurable]
                row["fluidity"][tier_name] = {
                    "deadline_s": tier["deadline_s"],
                    "n": len(measurable),
                    "index_mean": self._ci(measurable, statistics.fmean, salt * 10 + 3),
                    "index_p50": _p(measurable, 0.5),
                    "slo_fraction": self._ci(
                        [float(m) for m in meets], statistics.fmean, salt * 10 + 4
                    ),
                    "slo_met": (
                        quantile([float(m) for m in meets], 1 - slo["request_quantile"]) >= 1.0
                    )
                    if meets
                    else None,
                    "unmeasurable": len(indices) - len(measurable),
                }
            traces = [v.token_times_s for _, v in completed if len(v.token_times_s) >= 2]
            mdds = []
            unresolved = 0
            for trace in traces:
                mdd = minimum_decode_deadline(
                    token_intervals(trace), max_miss_rate=slo["max_miss_rate"]
                )
                if mdd is None:
                    unresolved += 1
                else:
                    mdds.append(mdd)
            mdds.sort()
            ftgr = None
            if traces and unresolved == 0:
                ftgr = fluid_token_generation_rate(
                    [token_intervals(t) for t in traces],
                    max_miss_rate=slo["max_miss_rate"],
                    request_quantile=slo["request_quantile"],
                )
            row["decode"] = {
                "min_decode_deadline_s": {
                    "n": len(mdds),
                    "p50": _p(mdds, 0.5),
                    "p99": _p(mdds, 0.99),
                    "unresolved_traces": unresolved,
                },
                "ftgr_tokens_per_s": ftgr,
                "ftgr_note": None
                if ftgr is not None
                else (
                    "unresolved: all-at-once delivery in some trace, "
                    "or no completed multi-token traces"
                ),
                "traces_excluded_single_token": len(completed) - len(traces),
            }
            out[key] = row
            samples[key] = {
                "ttft": ttfts,
                "fi_strict": sorted(
                    i
                    for i in (
                        self._fluidity_for(r, v, tiers["strict"]["deadline_s"]) for r, v in items
                    )
                    if i is not None
                ),
                "mdd": mdds,
                "mdd_unresolved": unresolved,
            }
        return out, samples

    def startup(self) -> dict[str, Any]:
        """Launch-to-first-valid-generation per launch; readiness alone never counts."""
        launches = []
        used_probe_ids: set[str] = set()
        for launch in self.ev.run.startup:
            t0 = launch["launch_mono_s"]
            probes = launch.get("readiness_probes") or []
            claimed_at = launch.get("first_valid_generation_at")
            probe_ids = launch.get("generation_probe_request_ids") or []
            probe_verdicts = [self.verdicts[p].category for p in probe_ids if p in self.verdicts]
            first_valid_request = launch.get("first_valid_request_id")
            # Startup is derived from the probes' own raw events: the earliest probe passing
            # every gate. run.json's summary is only cross-checked, never trusted.
            derived: tuple[float, str] | None = None
            for pid in probe_ids:
                v = self.verdicts.get(pid)
                rec = self.by_id.get(pid)
                if pid in used_probe_ids:
                    self.reasons.append(f"startup: probe {pid!r} is reused across launches")
                    continue
                used_probe_ids.add(pid)
                if rec is None:
                    self.reasons.append(f"startup: referenced probe {pid!r} is missing")
                    continue
                if rec.raw["phase"] != "startup_probe":
                    self.reasons.append(f"startup: referenced probe {pid!r} has wrong phase")
                    continue
                if t0 is None or rec.raw["timestamps_mono_s"]["send"] < t0:
                    self.reasons.append(f"startup: probe {pid!r} predates its launch")
                    continue
                if v is None or v.category != "completed":
                    continue
                # "First valid completed generation": validity is only known when the stream
                # ends, so the clock stops at the probe's own client-side ``end`` timestamp.
                at = float(rec.raw["timestamps_mono_s"]["end"])
                if derived is None or at < derived[0]:
                    derived = (at, pid)
            valid_at = derived[0] if derived else None
            label = f"startup launch {launch['launch_index']}"
            if (claimed_at is None) != (derived is None):
                self.reasons.append(
                    f"{label}: startup_self_report_mismatch (run.json claims "
                    f"{claimed_at!r}, probe evidence derives {valid_at!r})"
                )
            elif derived is not None and abs(float(claimed_at) - derived[0]) > 1e-3:
                self.reasons.append(
                    f"{label}: startup_self_report_mismatch (claimed {claimed_at:.4f}s, "
                    f"derived {derived[0]:.4f}s from probe {derived[1]!r})"
                )
            if derived is not None and first_valid_request not in (None, derived[1]):
                self.reasons.append(
                    f"{label}: startup_self_report_mismatch (claimed probe "
                    f"{first_valid_request!r}, derived {derived[1]!r})"
                )
            if (
                first_valid_request
                and first_valid_request in self.verdicts
                and self.verdicts[first_valid_request].category != "completed"
            ):
                self.reasons.append(f"{label}: claimed first valid probe fails the gates")
            if launch.get("status") == "valid" and derived is None:
                self.reasons.append(f"{label}: claimed valid but no probe passes the gates")
            if (
                valid_at is not None
                and launch.get("readiness_at")
                and launch["readiness_at"] > valid_at
            ):
                self.reasons.append(f"{label}: readiness recorded after the first valid generation")
            launches.append(
                {
                    "launch_index": launch["launch_index"],
                    "cold": launch.get("cold"),
                    "status": launch.get("status"),
                    "readiness_probes": len(probes),
                    "first_ready_probe_s": (launch["readiness_at"] - t0)
                    if launch.get("readiness_at") is not None
                    else None,
                    "generation_probes": len(probe_ids),
                    "probe_categories": dict(Counter(probe_verdicts)),
                    "startup_to_first_valid_generation_s": (valid_at - t0)
                    if valid_at is not None
                    else None,
                    "readiness_lead_s": (valid_at - launch["readiness_at"])
                    if (valid_at is not None and launch.get("readiness_at") is not None)
                    else None,
                }
            )
        valid = [
            launch["startup_to_first_valid_generation_s"]
            for launch in launches
            if launch["cold"]
            and launch["startup_to_first_valid_generation_s"] is not None
        ]
        planned = self.ev.run.raw.get("planned_startup_launches")
        if self.ev.run.mode in ("quick", "full", "calibration"):
            expected = self.plan["modes"][self.ev.run.mode]["startup_launches"]
            if planned != expected:
                self.reasons.append("startup: planned launch count differs from workload plan")
            planned = expected
        if planned is not None and len(launches) != planned:
            relation = "<" if len(launches) < planned else ">"
            self.inconclusive.append(
                f"startup: {len(launches)} launches recorded {relation} {planned} planned"
            )
        cold = [launch for launch in launches if launch["cold"]]
        if planned is not None and len(valid) != planned:
            relation = "<" if len(valid) < planned else ">"
            self.inconclusive.append(
                f"startup: {len(valid)} valid cold generations {relation} {planned} planned"
            )
        if not launches:
            self.inconclusive.append("startup: no launch recorded")
        elif not cold:
            self.inconclusive.append(
                "startup: runner attached to an already running server (no cold launch); "
                "clock measures attach-to-first-valid only"
            )
        elif not valid:
            self.inconclusive.append("startup: no cold launch reached a valid completed generation")
        return {
            "launches": launches,
            "planned_launches": planned,
            "cold_launches": len(cold),
            "startup_to_first_valid_generation_s": {
                "n": len(valid),
                "values": valid,
                "median": statistics.median(valid) if valid else None,
            },
        }

    def aa_control(self, cells: dict[str, Any], samples: dict[str, Any]) -> dict[str, Any] | None:
        """Within-run A/A comparison: control cells must not produce confident regressions."""
        a = [
            k for k, c in cells.items() if c["role"] == "aa_control" and c["cell_id"].endswith("_a")
        ]
        b = [
            k for k, c in cells.items() if c["role"] == "aa_control" and c["cell_id"].endswith("_b")
        ]
        if not a or not b:
            return None
        return compare_groups(
            self, samples[a[0]], self, samples[b[0]], label=f"{a[0]} vs {b[0]}", salt=777
        )


def compare_groups(
    ev_a: Evaluator,
    s_a: dict[str, Any],
    ev_b: Evaluator,
    s_b: dict[str, Any],
    *,
    label: str,
    salt: int,
    comparisons: int = 1,
) -> dict[str, Any]:
    """Compare two groups on TTFT p50/p90, strict FI mean and the FTGR proxy (MDD p99)."""
    cfg = ev_a.stats_cfg
    me = cfg["min_effect"]
    metrics = {
        "ttft_p50_s": ("ttft", lambda x: quantile(x, 0.5), me["ttft_p50_rel"], True, False),
        "ttft_p90_s": ("ttft", lambda x: quantile(x, 0.9), me["ttft_p90_rel"], True, False),
        "fluidity_strict_mean": (
            "fi_strict",
            statistics.fmean,
            me["fluidity_index_abs"],
            False,
            True,
        ),
        "min_decode_deadline_p99_s": (
            "mdd",
            lambda x: quantile(x, 0.99),
            me["ftgr_rel"],
            True,
            False,
        ),
    }
    # A cell declares regression if *any* metric moves; all tested metrics therefore
    # belong to the same family, including the single-cell within-run A/A control.
    family_size = comparisons * len(metrics)
    conf = bonferroni(cfg["confidence"], family_size)
    min_n = cfg["min_requests_per_cell"]
    out: dict[str, Any] = {
        "label": label,
        "confidence_adjusted": conf,
        "comparison_family_size": family_size,
        "inference_diagnostics": bootstrap_diagnostics(
            resamples=cfg["bootstrap_resamples"], confidence=conf, family_size=family_size
        ),
        "metrics": {},
    }
    for i, (name, (key, stat, min_effect, relative, higher_better)) in enumerate(metrics.items()):
        a, b = s_a.get(key, []), s_b.get(key, [])
        diagnostics = bootstrap_diagnostics(
            resamples=cfg["bootstrap_resamples"],
            confidence=conf,
            family_size=family_size,
            n_a=len(a),
            n_b=len(b),
            p99=key == "mdd",
        )
        if key == "mdd" and (s_a.get("mdd_unresolved", 0) or s_b.get("mdd_unresolved", 0)):
            out["metrics"][name] = {
                "a": None,
                "b": None,
                "difference": None,
                "inference_diagnostics": diagnostics,
                "decision": "inconclusive",
                "why": "unresolved decode traces: all-at-once delivery cannot identify FTGR",
            }
            continue
        diff = difference_ci(
            a,
            b,
            stat,
            resamples=cfg["bootstrap_resamples"],
            confidence=conf,
            seed=ev_a.seed + salt + i,
        )
        baseline = stat(a) if a else None
        if len(a) < min_n or len(b) < min_n:
            out["metrics"][name] = {
                "a": baseline,
                "b": stat(b) if b else None,
                "difference": diff,
                "inference_diagnostics": diagnostics,
                "decision": "inconclusive",
                "why": f"underpowered: n_a={len(a)}, n_b={len(b)} < min_requests_per_cell={min_n}",
            }
            continue
        out["metrics"][name] = {
            "a": baseline,
            "b": stat(b) if b else None,
            "difference": diff,
            "inference_diagnostics": diagnostics,
            **decide(diff, baseline, min_effect, relative=relative, higher_is_better=higher_better),
        }
    decisions = [m["decision"] for m in out["metrics"].values()]
    out["decision"] = (
        "regression"
        if "regression" in decisions
        else "inconclusive"
        if "inconclusive" in decisions
        else "improvement"
        if "improvement" in decisions
        else "no_difference"
    )
    return out


def evaluate(
    plan: dict[str, Any],
    events_dir: Path,
    adapter: str,
    plan_digest_hex: str | None = None,
    tokenizer: ModelTokenizer | None = None,
) -> tuple[dict[str, Any], Evaluator | None, dict[str, Any]]:
    """Evaluate one events directory; returns (report, evaluator, per-group samples)."""
    try:
        evidence = load_events(events_dir, adapter)
    except EvidenceError as exc:
        return (
            {
                "status": "invalid",
                "reasons": [f"evidence unreadable: {exc}"],
                "events_dir": str(events_dir),
            },
            None,
            {},
        )
    # Do not calculate on structurally invalid records: a type error must produce
    # a named invalid-evidence verdict, rather than crash or silently coerce data.
    if evidence.schema_errors:
        return (
            {
                "status": "invalid",
                "reasons": [f"schema: {e}" for e in evidence.schema_errors],
                "events_dir": str(events_dir),
            },
            None,
            {},
        )
    ev = Evaluator(plan, evidence, plan_digest_hex, tokenizer)
    ev.run_gates()
    ev.judge_all()
    population = ev.population()
    cells, samples = ev.cells()
    startup = ev.startup()
    aa = ev.aa_control(cells, samples)
    if aa and aa["decision"] in ("regression", "improvement"):
        ev.reasons.append(
            f"A/A control cells differ confidently ({aa['decision']}): "
            "the measurement is not stable"
        )
    if not any(c["enough_evidence"] for c in cells.values()):
        ev.inconclusive.append("no cell reached min_requests_per_cell completed requests")
    status = "invalid" if ev.reasons else "inconclusive" if ev.inconclusive else "valid"
    report = {
        "status": status,
        "reasons": ev.reasons,
        "inconclusive_reasons": ev.inconclusive,
        "run_id": evidence.run.run_id,
        "mode": evidence.run.mode,
        "adapter": adapter,
        "engine": evidence.run.raw.get("engine"),
        "tokenizer": evidence.run.raw.get("tokenizer"),
        "time_scale": evidence.run.raw.get("time_scale", 1.0),
        "population": population,
        "startup": startup,
        "cells": cells,
        "aa_control": aa,
        "request_verdicts": {
            rid: {"category": v.category, "reasons": v.reasons, "flags": v.flags}
            for rid, v in ev.verdicts.items()
            if v.category != "completed" or v.flags
        },
    }
    return report, ev, samples


def compare_runs(
    plan: dict[str, Any],
    base: tuple[dict[str, Any], Evaluator | None, dict[str, Any]],
    other: tuple[dict[str, Any], Evaluator | None, dict[str, Any]],
) -> dict[str, Any]:
    """Two-run decision: only valid runs are compared; everything else is inconclusive/invalid."""
    rep_a, ev_a, s_a = base
    rep_b, ev_b, s_b = other
    if rep_a["status"] == "invalid" or rep_b["status"] == "invalid":
        return {"decision": "invalid", "why": "at least one run is invalid; nothing to compare"}
    assert ev_a is not None and ev_b is not None
    identity_errors = comparison_identity_errors(plan, ev_a, ev_b)
    if identity_errors:
        return {
            "decision": "invalid",
            "why": "incompatible comparison identities; nothing compared",
            "identity_reasons": identity_errors,
        }
    common = sorted(
        k
        for k in s_a
        if k in s_b
        and rep_a["cells"][k]["role"] == "measure"
        and rep_b["cells"][k]["role"] == "measure"
    )
    if not common:
        return {"decision": "inconclusive", "why": "no common measurement cells"}
    per_cell = {
        k: compare_groups(
            ev_a, s_a[k], ev_b, s_b[k], label=k, salt=1000 + i, comparisons=len(common)
        )
        for i, k in enumerate(common)
    }
    decisions = Counter(c["decision"] for c in per_cell.values())
    overall = (
        "regression"
        if decisions["regression"]
        else "inconclusive"
        if decisions["inconclusive"]
        else "improvement"
        if decisions["improvement"]
        else "no_difference"
    )
    if rep_a["status"] == "inconclusive" or rep_b["status"] == "inconclusive":
        overall = "inconclusive"
    return {
        "decision": overall,
        "cells_compared": len(common),
        "inference_diagnostics": bootstrap_diagnostics(
            resamples=ev_a.stats_cfg["bootstrap_resamples"],
            confidence=bonferroni(ev_a.stats_cfg["confidence"], len(common) * 4),
            family_size=len(common) * 4,
        ),
        "per_cell": per_cell,
        "why": f"cell decisions: {dict(decisions)}; "
        f"run statuses {rep_a['status']}/{rep_b['status']}",
    }


def summarize(report: dict[str, Any]) -> str:
    """Human-readable Markdown summary of a report."""
    engine = report.get("engine") or {}
    kind = engine.get("kind", "unknown")
    banner = f"ENGINE: {engine.get('model_id', '?')} (kind={kind})"
    if kind == "mock":
        banner += " — CPU mock stand-in, NOT llm-d, NOT a GPU measurement"
    lines = [
        f"# Evaluation: {report.get('run_id', '?')} — status **{report['status'].upper()}**",
        "",
        f"> {banner}",
        "",
    ]
    for r in report.get("reasons", []):
        lines.append(f"- INVALID: {r}")
    for r in report.get("inconclusive_reasons", []):
        lines.append(f"- inconclusive: {r}")
    pop = report.get("population", {}).get("totals")
    if pop:
        lines += [
            "",
            "## Population",
            "| planned | offered | completed | failed | unfinished | evidence_invalid |",
            "| --- | --- | --- | --- | --- | --- |",
            f"| {pop['planned']} | {pop['offered']} | {pop['completed']} | {pop['failed']} "
            f"| {pop['unfinished']} | {pop['evidence_invalid']} |",
        ]
    st = report.get("startup", {}).get("startup_to_first_valid_generation_s")
    if st:
        lines += [
            "",
            "## Startup: launch -> first VALID generation: "
            f"median {st['median']} s over {st['n']} launch(es)",
        ]
    cells = report.get("cells", {})
    if cells:
        lines += [
            "",
            "## Cells",
            "| cell | load | completed/offered | TTFT p50 s [CI] | TTFT p90 s | FI strict mean "
            "| FI relaxed SLO | MDD p99 s | FTGR tok/s | rejections |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for c in cells.values():
            p50 = c["ttft_s"]["p50"]
            fi = c["fluidity"].get("strict", {}).get("index_mean", {})
            rel = c["fluidity"].get("relaxed", {})
            lines.append(
                f"| {c['cell_id']} | {c['load_label']} | {c['completed']}/{c['offered']} | "
                f"{_fmt(p50.get('point'))} [{_fmt(p50.get('low'))}, {_fmt(p50.get('high'))}] | "
                f"{_fmt(c['ttft_s']['p90'].get('point'))} | {_fmt(fi.get('point'))} | "
                f"{rel.get('slo_met')} | "
                f"{_fmt(c['decode']['min_decode_deadline_s']['p99'])} | "
                f"{_fmt(c['decode']['ftgr_tokens_per_s'])} | "
                f"{c['rejection_reasons'] or '-'} |"
            )
    aa = report.get("aa_control")
    if aa:
        lines += ["", f"## A/A control: {aa['decision']} ({aa['label']})"]
    cmp_ = report.get("comparison")
    if cmp_:
        lines += ["", f"## Comparison: **{cmp_['decision']}** — {cmp_.get('why', '')}"]
        diagnostics = cmp_.get("inference_diagnostics")
        if diagnostics:
            lines += [
                "",
                "Inference: independent-request percentile bootstrap, conditional on empirical "
                f"samples; joint family={diagnostics['comparison_family_size']}, "
                f"resamples={diagnostics['bootstrap_resamples']}, expected draws/tail="
                f"{diagnostics['expected_resamples_per_tail']:.3g}. "
                "IID requests/pooling are assumptions; sparse p99 data and Monte Carlo tails "
                "remain limitations. No measured power or finite-sample FWER guarantee.",
            ]
    return "\n".join(lines) + "\n"


def _fmt(value: Any) -> str:
    return "-" if value is None else f"{value:.4g}"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--workload", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--compare", type=Path, help="second events directory (B) compared against --events (A)"
    )
    parser.add_argument("--adapter", default="native")
    parser.add_argument(
        "--tokenizer-snapshot",
        type=Path,
        help="local pinned tokenizer assets; required for real-engine evidence",
    )
    parser.add_argument("--summary", type=Path, help="also write a Markdown summary here")
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entry point: always writes a report; an evaluator crash is an invalid verdict (rc 4)."""
    try:
        return _main(argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - a crash must surface as a verdict, not a traceback
        args, _ = _parser().parse_known_args(argv)
        report = {
            "report_schema_version": REPORT_SCHEMA_VERSION,
            "status": "invalid",
            "reasons": [f"evaluator error: {type(exc).__name__}: {exc}"],
            "inconclusive_reasons": [],
        }
        if getattr(args, "out", None) is not None:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
        print(f"# Evaluation: status **INVALID** - {report['reasons'][0]}")
        return 4


def _main(argv: list[str] | None) -> int:
    """Parse arguments, evaluate, write report and summary."""
    args = _parser().parse_args(argv)
    plan = load_plan(args.workload)
    digest = plan_digest(args.workload)
    tokenizer = (
        ModelTokenizer(args.tokenizer_snapshot, plan["target"]["model_role"])
        if args.tokenizer_snapshot
        else None
    )
    base = evaluate(plan, args.events, args.adapter, digest, tokenizer)
    report = base[0]
    if args.compare:
        other = evaluate(plan, args.compare, args.adapter, digest, tokenizer)
        report["comparison"] = {
            **compare_runs(plan, base, other),
            "other_status": other[0]["status"],
            "other_reasons": other[0].get("reasons", []),
            "other_events_dir": str(args.compare),
        }
    report = {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "suite_version": SUITE_VERSION,
        "plan_id": plan["plan_id"],
        "events_dir": str(args.events),
        **report,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    summary = summarize(report)
    if args.summary:
        args.summary.write_text(summary, encoding="utf-8")
    print(summary)
    return {"valid": 0, "inconclusive": 3, "invalid": 4}[report["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
