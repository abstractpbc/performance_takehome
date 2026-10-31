"""Buyer's mark-to-model of the fixed-price capacity reservation.

Reserved hours = GPU count × actual calendar hours of each delivery month (the buyer pays
for all of them; expected usage never reduces the bill). For each month ``m``:

    value_m = (FV_m - K) × hours_m × DF(pay_m) × (1 - unavailability) × survival_m

where ``FV_m`` is the curve's fair value for that delivery month, ``K`` the contract price,
``DF`` the buyer's discount factor (annual-effective, ACT/365) to the month-end payment date,
unavailability is the SLA floor (pro-rata fee credit, no consequential damages), and
``survival_m`` assumes default timing conditional on default is uniform from as-of to delivery
end (on default the buyer neither pays nor receives the undelivered months; recovery applies
to any positive claim). The mark is the sum; the fair fixed price ``K*`` sets it to zero.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .curve import CurveModel
from .months import (
    add_months,
    format_utc,
    hours_in_month,
    month_label,
    months_between,
    year_fraction,
)
from .schema import Case, InputError


@dataclass
class MonthRow:
    month: datetime
    tenor: int
    hours: int
    status: str
    fv_mid: float
    fv_low: float
    fv_high: float
    fee_usd: float
    payment_date: datetime
    discount_factor: float
    survival: float
    availability: float
    value_undiscounted: float
    value_discounted: float
    value_adjusted: float
    value_low: float
    value_high: float


@dataclass
class Valuation:
    rows: list[MonthRow]
    identified: bool
    reserved_hours: int
    fees_total: float
    fees_pv: float
    mark_undiscounted: float
    mark_discounted: float
    mark_sla_adjusted: float
    mark_adjusted: float
    mark_low: float
    mark_high: float
    fair_fixed_price: float | None
    fair_fixed_low: float | None
    fair_fixed_high: float | None
    scenario_marks: dict[str, float]
    scenario_fair_prices: dict[str, float | None]
    decision: str
    decision_basis: str
    flip_conditions: list[str]
    wrong_usage_fees: dict[str, float | None]
    assumptions: list[str] = field(default_factory=list)


def discount_factor(rate: float, start: datetime, end: datetime) -> float:
    """Annual-effective ACT/365 discount factor from ``start`` to ``end``."""
    return (1 + rate) ** (-year_fraction(start, end))


def survival_probability(
    p_default: float, as_of: datetime, when: datetime, deal_end: datetime
) -> float:
    """Survival under a linear cumulative default probability over the stated window.

    Conditional on default, its time is uniform. This is not constant-hazard survival.
    """
    total = (deal_end - as_of).total_seconds()
    if total <= 0:
        return 1 - p_default
    frac = min(1.0, max(0.0, (when - as_of).total_seconds() / total))
    return 1 - p_default * frac


def _credit_mark(spread: float, weight: float, survival: float, recovery: float) -> float:
    """Value one signed claim; recovery applies only to a positive buyer claim."""
    return spread * weight * (survival + (1 - survival) * recovery * (spread > 0))


def _fair_fixed_price(levels: list[float], rows: list[MonthRow], recovery: float) -> float | None:
    """Solve the piecewise-linear zero-mark price, including sign-dependent recovery."""

    def mark(price: float) -> float:
        return sum(
            _credit_mark(
                level - price,
                row.hours * row.discount_factor * row.availability,
                row.survival,
                recovery,
            )
            for level, row in zip(levels, rows, strict=True)
        )

    if not any(row.availability * row.survival > 0 for row in rows):
        # The mark still exists, but zero surviving payment exposure does not
        # identify a unique zero-mark contract price (including under stress).
        return None
    low, high = min(levels), max(levels)
    for _ in range(80):
        mid = (low + high) / 2
        if mark(mid) > 0:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def value_reservation(
    model: CurveModel,
    case: Case,
    *,
    discount_rate: float | None = None,
    p_default: float | None = None,
    kappa: float | None = None,
    vol_multiplier: float = 1.0,
) -> Valuation:
    """Compute the full cash-flow walk and mark for the case reservation."""
    res, econ = case.reservation, case.economics
    rate = float(econ.buyer_discount_annual_rate) if discount_rate is None else discount_rate
    pd = float(econ.provider_default_probability_over_deal) if p_default is None else p_default
    recovery = float(econ.default_recovery_fraction)
    unavailable = max(0.0, 1 - float(res.sla_pct) / 100)
    k = float(res.fixed_usd_per_gpu_hour)
    rows: list[MonthRow] = []
    scenario_values = {name: 0.0 for name in model.config["scenarios"]}
    scenario_levels: dict[str, list[float]] = {name: [] for name in model.config["scenarios"]}
    try:
        months = months_between(res.delivery_start, res.delivery_end)
    except ValueError as exc:
        raise InputError(f"reservation {res.id}: {exc}") from exc
    if model.tenor_of(months[0]) < 0:
        raise InputError(
            f"reservation {res.id}: delivery starts {months[0]:%Y-%m}, before the as-of month "
            f"{case.as_of:%Y-%m}; a past window cannot be marked against a forward curve"
        )
    for month in months:
        tenor = model.tenor_of(month)
        node = model.node(tenor, kappa=kappa, vol_multiplier=vol_multiplier)
        if node.mid is None or node.low is None or node.high is None:
            return _unidentified(case, k, month_label(month))
        hours = hours_in_month(month) * res.gpu_count
        pay = add_months(month, 1)
        df = discount_factor(rate, case.as_of, pay)
        surv = survival_probability(pd, case.as_of, pay, res.delivery_end)
        avail = 1 - unavailable
        fee = k * hours
        v0 = (node.mid - k) * hours
        v1 = v0 * df
        exposure = hours * df * avail
        v_adj = _credit_mark(node.mid - k, exposure, surv, recovery)
        rows.append(
            MonthRow(
                month,
                tenor,
                hours,
                node.status,
                node.mid,
                node.low,
                node.high,
                fee,
                pay,
                df,
                surv,
                avail,
                v0,
                v1,
                v_adj,
                _credit_mark(node.low - k, exposure, surv, recovery),
                _credit_mark(node.high - k, exposure, surv, recovery),
            )
        )
        for name in scenario_values:
            level = node.scenarios[name]
            if level is not None:
                scenario_values[name] += _credit_mark(level - k, exposure, surv, recovery)
                scenario_levels[name].append(level)
    hours_total = sum(r.hours for r in rows)
    fair = _fair_fixed_price([r.fv_mid for r in rows], rows, recovery)
    fair_low = _fair_fixed_price([r.fv_low for r in rows], rows, recovery)
    fair_high = _fair_fixed_price([r.fv_high for r in rows], rows, recovery)
    mark_adj = sum(r.value_adjusted for r in rows)
    mark_low = sum(r.value_low for r in rows)
    mark_high = sum(r.value_high for r in rows)
    scenario_fair = {
        name: _fair_fixed_price(scenario_levels[name], rows, recovery) for name in scenario_values
    }
    if fair is None and mark_low == mark_high == 0:
        decision, basis = (
            "indifferent_unidentified_price",
            "zero mark under no surviving exposure; no unique fair fixed price is identified",
        )
    elif mark_low > 0:
        decision, basis = "sign", "mark positive across the whole interval"
    elif mark_high < 0:
        decision, basis = "do_not_sign", "mark negative across the whole interval"
    elif mark_adj >= 0:
        decision, basis = "sign_marginal", "mark positive at mid but the interval includes zero"
    else:
        decision, basis = (
            "do_not_sign_at_this_price",
            "mark negative at mid; the interval includes zero",
        )
    flips = (
        [
            f"sign at or below the fair fixed price {fair:.4f} USD/GPU-hour (mid curve); "
            f"interval {fair_low:.4f}..{fair_high:.4f}",
        ]
        if fair is not None
        else ["no surviving service/payment exposure identifies a unique fair fixed price"]
    )
    for name, price in scenario_fair.items():
        if price is None:
            flips.append(f"{name} scenario has no uniquely identified fair fixed price")
            continue
        verdict = "favourable" if price > k else "unfavourable"
        flips.append(
            f"{name} scenario fair fixed price {price:.4f} -> "
            f"contract at {k:.2f} is {verdict} to the buyer"
        )
    used = res.expected_buyer_used_fraction
    wrong = {
        "fees_owed_usd": k * hours_total,
        "expected_used_fraction": float(used) if used is not None else None,
        "WRONG_fees_scaled_by_usage_usd": k * hours_total * float(used)
        if used is not None
        else None,
        "effective_usd_per_used_gpu_hour": k / float(used) if used not in (None, 0) else None,
    }
    return Valuation(
        rows=rows,
        identified=True,
        reserved_hours=hours_total,
        fees_total=k * hours_total,
        fees_pv=sum(r.fee_usd * r.discount_factor for r in rows),
        mark_undiscounted=sum(r.value_undiscounted for r in rows),
        mark_discounted=sum(r.value_discounted for r in rows),
        mark_sla_adjusted=sum(r.value_discounted * r.availability for r in rows),
        mark_adjusted=mark_adj,
        mark_low=mark_low,
        mark_high=mark_high,
        fair_fixed_price=fair,
        fair_fixed_low=fair_low,
        fair_fixed_high=fair_high,
        scenario_marks=scenario_values,
        scenario_fair_prices=scenario_fair,
        decision=decision,
        decision_basis=basis,
        flip_conditions=flips,
        wrong_usage_fees=wrong,
        assumptions=[
            f"discount rate {rate:.4f} annual-effective, ACT/365, "
            f"from as-of {format_utc(case.as_of)} to each "
            "month-end payment date",
            f"SLA {float(res.sla_pct)}%: provider assumed to deliver "
            "exactly at the floor; unavailable hours earn a "
            "pro-rata fee credit and no consequential damages, so value scales by availability",
            f"provider default probability {pd:.4f} over the "
            "deal spread uniformly from as-of to delivery end; "
            f"recovery {recovery:.2f} applies only to a positive "
            "buyer claim; undelivered months are neither paid "
            "nor received",
            "cancellation none: no optionality value for either side is added",
            "egress and taxes excluded from the capacity price on both the contract and the curve",
        ],
    )


def _unidentified(case: Case, k: float, month: str) -> Valuation:
    res = case.reservation
    hours = sum(hours_in_month(m) for m in months_between(res.delivery_start, res.delivery_end))
    hours *= res.gpu_count
    nan = float("nan")
    return Valuation(
        rows=[],
        identified=False,
        reserved_hours=hours,
        fees_total=k * hours,
        fees_pv=nan,
        mark_undiscounted=nan,
        mark_discounted=nan,
        mark_sla_adjusted=nan,
        mark_adjusted=nan,
        mark_low=nan,
        mark_high=nan,
        fair_fixed_price=nan,
        fair_fixed_low=nan,
        fair_fixed_high=nan,
        scenario_marks={},
        scenario_fair_prices={},
        decision="unidentified",
        decision_basis=f"the curve is unidentified for delivery month {month}; no mark is produced",
        flip_conditions=[],
        wrong_usage_fees={"fees_owed_usd": k * hours},
        assumptions=[
            "reserved hours and fees are contractual facts; fair value needs comparable evidence"
        ],
    )


def valuation_as_dict(val: Valuation) -> dict[str, object]:
    """JSON-ready representation with fixed rounding."""

    def r(x: float | None, p: int = 6) -> float | None:
        return None if x is None else round(x, p)

    if not val.identified:
        return {
            "identified": False,
            "reserved_hours": val.reserved_hours,
            "fees_total_usd": r(val.fees_total, 2),
            "months": [],
            "buyer_mark": None,
            "fair_fixed_price_usd_per_gpu_hour": None,
            "fair_fixed_price_identified": False,
            "decision": val.decision,
            "decision_basis": val.decision_basis,
            "usage_is_informational_only": {k: r(v, 4) for k, v in val.wrong_usage_fees.items()},
            "assumptions": list(val.assumptions),
        }

    return {
        "identified": True,
        "reserved_hours": val.reserved_hours,
        "fees_total_usd": r(val.fees_total, 2),
        "fees_present_value_usd": r(val.fees_pv, 2),
        "months": [
            {
                "delivery_month": month_label(row.month),
                "tenor_months": row.tenor,
                "reserved_hours": row.hours,
                "curve_status": row.status,
                "fair_value_usd_per_gpu_hour": {
                    "mid": r(row.fv_mid),
                    "low": r(row.fv_low),
                    "high": r(row.fv_high),
                },
                "fee_usd": r(row.fee_usd, 2),
                "payment_date": format_utc(row.payment_date),
                "discount_factor": r(row.discount_factor, 8),
                "survival_probability": r(row.survival, 8),
                "availability": r(row.availability, 8),
                "value_undiscounted_usd": r(row.value_undiscounted, 2),
                "value_discounted_usd": r(row.value_discounted, 2),
                "value_adjusted_usd": r(row.value_adjusted, 2),
                "value_low_usd": r(row.value_low, 2),
                "value_high_usd": r(row.value_high, 2),
            }
            for row in val.rows
        ],
        "buyer_mark": {
            "undiscounted_usd": r(val.mark_undiscounted, 2),
            "discounted_usd": r(val.mark_discounted, 2),
            "sla_adjusted_usd": r(val.mark_sla_adjusted, 2),
            "default_and_sla_adjusted_usd": r(val.mark_adjusted, 2),
            "interval_usd": {"low": r(val.mark_low, 2), "high": r(val.mark_high, 2)},
            "per_reserved_gpu_hour_usd": r(val.mark_adjusted / val.reserved_hours),
            "per_reserved_gpu_hour_interval_usd": {
                "low": r(val.mark_low / val.reserved_hours),
                "high": r(val.mark_high / val.reserved_hours),
            },
        },
        "fair_fixed_price_usd_per_gpu_hour": {
            "mid": r(val.fair_fixed_price),
            "low": r(val.fair_fixed_low),
            "high": r(val.fair_fixed_high),
        },
        "fair_fixed_price_identified": val.fair_fixed_price is not None,
        "scenario_marks_usd": {k: r(v, 2) for k, v in val.scenario_marks.items()},
        "scenario_fair_fixed_price_usd_per_gpu_hour": {
            k: r(v) for k, v in val.scenario_fair_prices.items()
        },
        "decision": val.decision,
        "decision_basis": val.decision_basis,
        "flip_conditions": list(val.flip_conditions),
        "usage_is_informational_only": {k: r(v, 4) for k, v in val.wrong_usage_fees.items()},
        "assumptions": list(val.assumptions),
    }
