"""Machine-checkable expectations for every fixture profile (CANDIDATE-BUILT).

Each profile names the *specific* gate, metric or startup field that must carry its detection.
``run_checks`` evaluates those predicates against an ``evaluate.py`` report (and the clean
control's report where a ratio is needed) and returns one observed line per check, so the
discrimination table can only say "detected" when the named signal actually fired.
"""

from __future__ import annotations

from typing import Any

KEY = "chat_short_open|rate=4rps"
CONTROL_AA = ["no_difference", "inconclusive"]

# Measured cross-matrix (tests/test_expectations.py::test_fault_predicates_are_specific): the
# other profiles whose public fixture ALSO satisfies a profile's predicates. Detection is
# family-level inside these groups; any undeclared overlap fails the test.
AMBIGUOUS_WITH: dict[str, list[str]] = {
    "stall": ["slow_decode", "unicode_split"],
    "slow_decode": ["stall", "unicode_split"],
    "late_first_token": ["reasoning_channel"],
    "reasoning_channel": ["late_first_token"],
    "missing_done": ["drop"],
    "missing_finish_reason": ["drop"],
    "drop": ["missing_done"],
    "wrong_text": ["garbled_text", "truncated_text"],
    "garbled_text": ["drop", "truncated_text", "wrong_text"],
    "truncated_text": ["drop", "garbled_text", "wrong_text"],
}
# Controls carry no detection predicate: their checks ("nothing rejected, A/A not confident")
# are satisfied by every well-behaved profile by design.
CONTROLS = ("clean", "noisy")

EXPECTED: dict[str, dict[str, Any]] = {
    "clean": {
        "status": "valid",
        "signal": "none (control): no rejected request, A/A not confident",
        "checks": [{"kind": "no_rejections"}, {"kind": "aa", "in": CONTROL_AA}],
    },
    "noisy": {
        "status": "valid",
        "signal": "none (control): jitter alone must not reject requests or flip A/A",
        "checks": [{"kind": "no_rejections"}, {"kind": "aa", "in": CONTROL_AA}],
    },
    "burst": {
        "status": "valid",
        "signal": "token_evidence.tokens_per_delta_mean > 4 (chunk != token); no request "
        "rejected; Etalon fluidity stays ~1.0 by design (8-token bursts at ~83 tok/s sit "
        "inside playback slack)",
        "checks": [
            {"kind": "token_evidence", "name": "tokens_per_delta_mean", "op": "gt", "value": 4.0},
            {"kind": "no_rejections"},
        ],
    },
    "stall": {
        "status": "valid",
        "signal": "strict fluidity index < 0.6 and min decode deadline p99 > 2x clean",
        "checks": [
            {"kind": "metric", "name": "fluidity_strict_mean", "op": "lt", "value": 0.6},
            {"kind": "metric", "name": "mdd_p99", "op": "ratio_gt", "value": 2.0},
        ],
    },
    "late_first_token": {
        "status": "valid",
        "signal": "TTFT p50 > 3x clean",
        "checks": [{"kind": "metric", "name": "ttft_p50", "op": "ratio_gt", "value": 3.0}],
    },
    "slow_decode": {
        "status": "valid",
        "signal": "strict fluidity index < 0.5 and min decode deadline p99 > 2x clean",
        "checks": [
            {"kind": "metric", "name": "fluidity_strict_mean", "op": "lt", "value": 0.5},
            {"kind": "metric", "name": "mdd_p99", "op": "ratio_gt", "value": 2.0},
        ],
    },
    "reasoning_channel": {
        "status": "valid",
        "signal": "no rejection (reasoning deltas are not content) and TTFT p50 > 2x clean "
        "because TTFT is the first *content* token",
        "checks": [
            {"kind": "no_rejections"},
            {"kind": "metric", "name": "ttft_p50", "op": "ratio_gt", "value": 2.0},
        ],
    },
    "unicode_split": {
        "status": "valid",
        "signal": "token_evidence.grapheme_boundary_splits > 0 (a content delta starts inside a "
        "grapheme cluster: combining mark / ZWJ / flag half) while no invalid_output gate fires: "
        "the split pieces are reassembled and counted once",
        "checks": [
            {"kind": "token_evidence", "name": "grapheme_boundary_splits", "op": "gt", "value": 0},
            {"kind": "gate", "name": "invalid_output", "max": 0},
            {"kind": "no_rejections"},
        ],
    },
    "missing_done": {
        "status": "valid",
        "signal": "gate missing_done -> unfinished",
        "checks": [
            {"kind": "gate", "name": "missing_done", "min": 1},
            {"kind": "total", "name": "unfinished", "min": 1},
        ],
    },
    "missing_finish_reason": {
        "status": "valid",
        "signal": "gate missing_finish_reason -> unfinished",
        "checks": [{"kind": "gate", "name": "missing_finish_reason", "min": 1}],
    },
    "http_error": {
        "status": "valid",
        "signal": "gate http_error -> failed",
        "checks": [
            {"kind": "gate", "name": "http_error", "min": 1},
            {"kind": "total", "name": "failed", "min": 1},
        ],
    },
    "drop": {
        "status": "valid",
        "signal": "gate missing_done -> unfinished (an EOF-terminated SSE stream is a premature "
        "end, not a transport error)",
        "checks": [
            {"kind": "gate", "name": "missing_done", "min": 1},
            {"kind": "total", "name": "unfinished", "min": 1},
        ],
    },
    "wrong_text": {
        "status": "valid",
        "signal": "gate invalid_output (nonce/sequence) -> failed",
        "checks": [
            {"kind": "gate", "name": "invalid_output", "min": 1},
            {"kind": "total", "name": "failed", "min": 1},
        ],
    },
    "garbled_text": {
        "status": "valid",
        "signal": "gate invalid_output (replacement/garbled) -> failed",
        "checks": [{"kind": "gate", "name": "invalid_output", "min": 1}],
    },
    "truncated_text": {
        "status": "valid",
        "signal": "gate invalid_output (too short / sequence) -> failed",
        "checks": [{"kind": "gate", "name": "invalid_output", "min": 1}],
    },
    "false_usage": {
        "status": "valid",
        "signal": "gate usage_discrepancy -> failed on chat cells; masked on integer lists where "
        "the regex-vs-mock tokenizer gap (2x) puts 3x usage inside the band (DESIGN.md 2)",
        "checks": [{"kind": "gate", "name": "usage_discrepancy", "min": 1}],
    },
    "misleading_readiness": {
        "status": "valid",
        "signal": "readiness probe is as fast as clean (< 2x) while >= 1 generation probe FAILS "
        "before the first valid one and readiness_lead_s > 1 s; distinguishes it from stall, "
        "whose probes complete slowly without failing",
        "checks": [
            {"kind": "startup", "name": "first_ready_probe_s", "op": "ratio_lt", "value": 2.0},
            {"kind": "startup", "name": "failed_probes", "op": "gt", "value": 0},
            {"kind": "startup", "name": "readiness_lead_s", "op": "gt", "value": 1.0},
        ],
    },
    "slow_startup": {
        "status": "valid",
        "signal": "first_ready_probe_s > 3x clean and startup_to_first_valid_generation_s > 2x "
        "clean (the readiness lead alone is a weak signal and is NOT relied on)",
        "checks": [
            {"kind": "startup", "name": "first_ready_probe_s", "op": "ratio_gt", "value": 3.0},
            {
                "kind": "startup",
                "name": "startup_to_first_valid_generation_s",
                "op": "ratio_gt",
                "value": 2.0,
            },
        ],
    },
    "timing_invalid": {
        "status": "invalid",
        "signal": "gate invalid_timing -> evidence_invalid -> run invalid",
        "checks": [
            {"kind": "reason", "contains": "untrustworthy evidence"},
            {"kind": "total", "name": "evidence_invalid", "min": 1},
        ],
    },
    "client_lag": {
        "status": "invalid",
        "signal": "run gate: event_loop_lag_max_s > max -> run invalid",
        "checks": [{"kind": "reason", "contains": "event-loop lag"}],
    },
    "underpowered": {
        "status": "inconclusive",
        "signal": "every measure cell below min_requests_per_cell -> inconclusive, never valid",
        "checks": [{"kind": "inconclusive_reason", "contains": "min_requests"}],
    },
}


def _cell_metric(report: dict[str, Any], name: str) -> float | None:
    cell = report.get("cells", {}).get(KEY)
    if not cell:
        return None
    if name == "ttft_p50":
        return cell["ttft_s"]["p50"]["point"]
    if name == "fluidity_strict_mean":
        return cell["fluidity"]["strict"]["index_mean"]["point"]
    if name == "mdd_p99":
        return cell["decode"]["min_decode_deadline_s"]["p99"]
    if name == "ftgr":
        return cell["decode"]["ftgr_tokens_per_s"]
    raise KeyError(name)


def _startup_field(report: dict[str, Any], name: str) -> float | None:
    launches = report.get("startup", {}).get("launches") or []
    if not launches:
        return None
    launch = launches[0]
    if name == "failed_probes":
        return float(sum(n for cat, n in launch["probe_categories"].items() if cat != "completed"))
    return launch.get(name)


def _compare(value: float | None, ref: float | None, op: str, target: float) -> tuple[bool, str]:
    if value is None:
        return False, "observed=None"
    if op == "lt":
        return value < target, f"observed={value:.4g} (need < {target})"
    if op == "gt":
        return value > target, f"observed={value:.4g} (need > {target})"
    if ref is None or ref == 0:
        return False, f"observed={value:.4g}, clean reference unavailable"
    ratio = value / ref
    if op == "ratio_gt":
        return (
            ratio > target,
            f"observed={value:.4g} vs clean {ref:.4g} (x{ratio:.2f}, need > x{target})",
        )
    if op == "ratio_lt":
        return (
            ratio < target,
            f"observed={value:.4g} vs clean {ref:.4g} (x{ratio:.2f}, need < x{target})",
        )
    raise ValueError(op)


def run_checks(
    report: dict[str, Any], clean: dict[str, Any] | None, checks: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Evaluate each check; returns [{check, passed, observed}] with nothing read but reports."""
    totals = report.get("population", {}).get("totals", {})
    gates: dict[str, int] = {}
    for cell in report.get("cells", {}).values():
        for k, n in cell.get("rejection_reasons", {}).items():
            gates[k] = gates.get(k, 0) + n
    results = []
    for check in checks:
        kind = check["kind"]
        if kind == "no_rejections":
            n = sum(totals.get(k, 0) for k in ("failed", "unfinished", "evidence_invalid"))
            passed, observed = n == 0, f"rejected={n}"
        elif kind == "gate":
            n = gates.get(check["name"], 0)
            passed = n >= check.get("min", 0) and n <= check.get("max", 10**9)
            observed = f"gate {check['name']} fired on {n} request(s)"
        elif kind == "total":
            n = totals.get(check["name"], 0)
            passed, observed = n >= check["min"], f"{check['name']}={n}"
        elif kind == "flag":
            n = sum(
                c.get("flags", {}).get(check["name"], 0) for c in report.get("cells", {}).values()
            )
            passed, observed = n >= check["min"], f"flag {check['name']} on {n} request(s)"
        elif kind == "metric":
            ref = _cell_metric(clean, check["name"]) if clean else None
            passed, observed = _compare(
                _cell_metric(report, check["name"]), ref, check["op"], check["value"]
            )
            observed = f"{check['name']} {observed}"
        elif kind == "startup":
            ref = _startup_field(clean, check["name"]) if clean else None
            passed, observed = _compare(
                _startup_field(report, check["name"]), ref, check["op"], check["value"]
            )
            observed = f"startup.{check['name']} {observed}"
        elif kind == "token_evidence":
            cell = report.get("cells", {}).get(KEY) or {}
            value = (cell.get("token_evidence") or {}).get(check["name"])
            passed, observed = _compare(value, None, check["op"], check["value"])
            observed = f"token_evidence.{check['name']} {observed}"
        elif kind == "aa":
            decision = report.get("aa_control", {}).get("decision")
            passed, observed = decision in check["in"], f"A/A decision={decision}"
        elif kind == "reason":
            hits = [r for r in report.get("reasons", []) if check["contains"] in r]
            passed, observed = (
                bool(hits),
                (hits[0] if hits else f"no reason contains {check['contains']!r}"),
            )
        elif kind == "inconclusive_reason":
            hits = [r for r in report.get("inconclusive_reasons", []) if check["contains"] in r]
            passed, observed = (
                bool(hits),
                (hits[0] if hits else f"no inconclusive reason contains {check['contains']!r}"),
            )
        else:
            raise ValueError(kind)
        results.append({"check": check, "passed": passed, "observed": observed})
    return results
