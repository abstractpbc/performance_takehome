"""Independently reconcile the supplied synthetic case, without importing research.

Checks calendar quantities, pricing arithmetic, costs, scenarios and artifact hashes.
This validates internal financial consistency, not market truth or forecast accuracy.
Run from a canonical LF checkout so file-byte provenance matches the declared inputs.
"""

from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import math
from datetime import datetime
from pathlib import Path


def iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def check_close(actual: float, expected: float, label: str, tolerance: float = 0.00501) -> None:
    if not math.isfinite(actual) or abs(actual - expected) > tolerance:
        raise ValueError(f"{label}: actual {actual}, independent expected {expected}")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reconcile(task: Path) -> dict:
    case_path = task / "data/demo-case.json"
    case = json.loads(case_path.read_text(encoding="utf-8"))
    result = json.loads((task / "valuation.json").read_text(encoding="utf-8"))
    run = json.loads((task / "run-manifest.json").read_text(encoding="utf-8"))
    sub = json.loads((task / "submission.json").read_text(encoding="utf-8"))
    econ, terms, val = case["economics"], case["reservation"], result["valuation"]
    if not case["synthetic"] or not result["synthetic_inputs"]:
        raise ValueError("This checker is scoped to the supplied synthetic case")
    if econ["default_recovery_fraction"] != 0:
        raise ValueError("Closed-form fair-price check requires this case's zero recovery")
    start, finish, as_of = map(iso, (terms["delivery_start"], terms["delivery_end"], case["as_of"]))
    current = start
    months = []
    while current < finish:
        hours = calendar.monthrange(current.year, current.month)[1] * 24 * terms["gpu_count"]
        payment = current.replace(
            year=current.year + (current.month == 12), month=current.month % 12 + 1
        )
        months.append((current, hours, payment))
        current = payment
    if current != finish or len(months) != len(val["months"]):
        raise ValueError("Reservation schedule does not reconcile")

    # Separate formula reconstruction of the explicitly declared supply-cost assumptions.
    monthly_rate = (1 + econ["operator_financing_annual_rate"]) ** (1 / 12) - 1
    life = econ["economic_life_months"]
    capex = econ["capex_usd_per_gpu_including_supporting_hardware"]
    residual_pv = capex * econ["residual_value_fraction"] / (1 + monthly_rate) ** life
    annuity = (capex - residual_pv) * monthly_rate / (1 - (1 + monthly_rate) ** -life)
    energy = (
        (econ["gpu_power_kw"] + econ["supporting_power_kw_per_gpu"])
        * econ["pue"]
        * econ["electricity_usd_per_kwh"]
    )
    marginal = energy + econ["non_energy_opex_usd_per_allocated_gpu_hour"]
    base_anchor = annuity / 730 / econ["operator_revenue_utilization_fraction"] + marginal
    cheap_anchor = annuity / 730 + marginal
    check_close(
        result["supply_cost"]["base"]["breakeven_usd_per_gpu_hour"],
        base_anchor,
        "full-cost anchor",
        1e-5,
    )

    # The supplied q04 ask and q05/q06 pair produce these stated anchor inputs.
    # Read quotes independently rather than accepting the model's point estimates.
    import csv

    with (task / "data/demo-quotes.csv").open(encoding="utf-8", newline="") as handle:
        quotes = {row["id"]: row for row in csv.DictReader(handle)}
    ask1, ask3, bid3 = (float(quotes[key]["price"]) for key in ("q04", "q05", "q06"))
    config = json.loads((task / "research/config/default.json").read_text(encoding="utf-8"))
    spread1 = max(ask1 * config["curve"]["one_sided_spread_fraction"], ask3 - bid3)
    first_mid, last_mid = ask1 - spread1 / 2, (ask3 + bid3) / 2
    kappa = math.log((first_mid - base_anchor) / (last_mid - base_anchor)) / 2
    scarcity_anchor = max(
        float(quotes[key]["price"]) / int(quotes[key]["gpu_count"]) for key in ("q01", "q02")
    )
    levels = {
        "base": lambda t: base_anchor + (last_mid - base_anchor) * math.exp(-kappa * (t - 3)),
        "cheap_compute": lambda t: (
            cheap_anchor + (bid3 - cheap_anchor) * math.exp(-kappa * (t - 3))
        ),
        "scarcity": lambda t: (
            scarcity_anchor + (ask3 - scarcity_anchor) * math.exp(-kappa * (t - 3))
        ),
    }
    with (task / "curve.csv").open(encoding="utf-8", newline="") as handle:
        curve_rows = list(csv.DictReader(handle))
    if [int(row["tenor_months"]) for row in curve_rows] != case["curve"]["tenor_months"]:
        raise ValueError("Curve tenors do not match the requested delivery months")
    curve_checks = []
    for row in curve_rows:
        tenor = int(row["tenor_months"])
        year, month0 = divmod(as_of.year * 12 + as_of.month - 1 + tenor, 12)
        month = as_of.replace(year=year, month=month0 + 1, day=1)
        hours = calendar.monthrange(month.year, month.month)[1] * 24
        if row["delivery_month"] != month.strftime("%Y-%m") or int(row["hours"]) != hours:
            raise ValueError("Curve delivery calendar mismatch")
        if tenor == 1:
            expected = {"base": first_mid, "cheap_compute": ask1 - spread1, "scarcity": ask1}
        elif tenor == 3:
            expected = {"base": last_mid, "cheap_compute": bid3, "scarcity": ask3}
        else:
            expected = {name: price(tenor) for name, price in levels.items()}
        check_close(float(row["usd_per_gpu_hour_mid"]), expected["base"], "curve CSV mid", 1e-6)
        low, high = float(row["usd_per_gpu_hour_low"]), float(row["usd_per_gpu_hour_high"])
        if not math.isfinite(low) or not math.isfinite(high) or low > high:
            raise ValueError("Curve range is invalid")
        for name, price in expected.items():
            check_close(float(row["scenario_" + name]), price, f"curve CSV {name}", 1e-6)
            if price < low - 1e-6 or price > high + 1e-6:
                raise ValueError("A declared scenario lies outside the reported range")
        curve_checks.append({"tenor_months": tenor, "hours": hours, "mid": expected["base"]})
    fee_total = 0.0
    fee_pv = 0.0
    independent_rows = []
    weights = []
    marks = dict.fromkeys(levels, 0.0)
    fixed = terms["fixed_usd_per_gpu_hour"]
    availability = terms["sla_pct"] / 100
    for (month, hours, payment), row in zip(months, val["months"], strict=True):
        tenor = (month.year - as_of.year) * 12 + month.month - as_of.month
        df = (1 + econ["buyer_discount_annual_rate"]) ** (
            -(payment - as_of).total_seconds() / (365 * 86400)
        )
        survival = (
            1
            - econ["provider_default_probability_over_deal"]
            * (payment - as_of).total_seconds()
            / (finish - as_of).total_seconds()
        )
        fee = hours * fixed
        if (
            row["delivery_month"] != month.strftime("%Y-%m")
            or row["reserved_hours"] != hours
            or iso(row["payment_date"]) != payment
        ):
            raise ValueError("Calendar, quantity or payment mismatch")
        check_close(row["fee_usd"], fee, "monthly fee")
        check_close(row["discount_factor"], df, "discount factor", 1e-8)
        check_close(row["survival_probability"], survival, "survival", 1e-8)
        check_close(
            row["fair_value_usd_per_gpu_hour"]["mid"],
            levels["base"](tenor),
            "monthly curve level",
            1e-6,
        )
        weight = hours * df * availability * survival
        value = (levels["base"](tenor) - fixed) * weight
        check_close(row["value_adjusted_usd"], value, "monthly adjusted mark")
        independent_rows.append(
            {
                "month": month.strftime("%Y-%m"),
                "hours": hours,
                "payment": payment.isoformat(),
                "fee_usd": fee,
                "adjusted_mark_usd": value,
            }
        )
        fee_total += fee
        fee_pv += fee * df
        weights.append((tenor, weight))
        for name, prices in levels.items():
            marks[name] += (prices(tenor) - fixed) * weight
    if val["reserved_hours"] != sum(row["hours"] for row in independent_rows):
        raise ValueError("Total reserved hours mismatch")
    check_close(val["fees_total_usd"], fee_total, "fee total")
    check_close(val["fees_present_value_usd"], fee_pv, "fee PV")
    check_close(val["buyer_mark"]["default_and_sla_adjusted_usd"], marks["base"], "buyer mark")
    scenario_fair = {}
    for name, prices in levels.items():
        fair = sum(prices(t) * w for t, w in weights) / sum(w for _, w in weights)
        scenario_fair[name] = fair
        check_close(val["scenario_marks_usd"][name], marks[name], f"{name} mark")
        check_close(
            val["scenario_fair_fixed_price_usd_per_gpu_hour"][name],
            fair,
            f"{name} zero-mark price",
            1e-6,
        )
    check_close(
        val["fair_fixed_price_usd_per_gpu_hour"]["mid"],
        scenario_fair["base"],
        "base zero-mark price",
        1e-6,
    )

    # Reconcile actual bytes, not merely the hash strings copied into two manifests.
    files = {
        "case": case_path,
        "quotes": task / "data/demo-quotes.csv",
        "performance": task / "data/demo-performance.csv",
    }
    digests = {role: sha(path) for role, path in files.items()}
    pack = hashlib.sha256(
        "".join(sorted(f"{role}:{digest}\n" for role, digest in digests.items())).encode("ascii")
    ).hexdigest()
    for manifest in (run, result):
        if manifest["inputs"]["input_pack_digest"] != pack:
            raise ValueError("Input-pack digest mismatch")
        if {item["role"]: item["sha256"] for item in manifest["inputs"]["files"]} != digests:
            raise ValueError("Input file hashes mismatch")
    if sub["inputs"]["input_pack_digest"] != pack:
        raise ValueError("Submission input identity mismatch")
    source_digest = hashlib.sha256()
    for path in sorted((task / "research").rglob("*")):
        if path.is_file() and path.suffix in {".py", ".json"} and "__pycache__" not in path.parts:
            source_digest.update(
                path.relative_to(task / "research").as_posix().encode()
                + b"\0"
                + path.read_bytes()
                + b"\0"
            )
    if (
        run["code"]["source_sha256"] != source_digest.hexdigest()
        or sub["source"]["source_sha256"] != source_digest.hexdigest()
    ):
        raise ValueError(
            "Research source digest mismatch; regenerate provenance from the committed source"
        )
    for key in ("report", "curve", "reservation_valuation", "tests"):
        if not (task.parents[1] / sub["evidence"][key]).exists():
            raise ValueError(f"Missing submitted artifact: {key}")
    return {
        "status": "PASS",
        "scope": (
            "Supplied synthetic case; independent arithmetic and byte-provenance checks, "
            "not forecast validation"
        ),
        "reserved_hours": val["reserved_hours"],
        "fees_usd": fee_total,
        "fees_pv_usd": fee_pv,
        "scenario_marks_usd": marks,
        "scenario_zero_mark_prices": scenario_fair,
        "curve_nodes_checked": curve_checks,
        "monthly_cashflows": independent_rows,
        "input_pack_digest": pack,
        "research_source_sha256": source_digest.hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-dir", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        result = reconcile(args.task_dir.resolve())
    except (OSError, KeyError, ValueError, TypeError, ZeroDivisionError) as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
