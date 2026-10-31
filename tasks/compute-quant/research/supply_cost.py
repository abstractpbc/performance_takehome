"""Transparent supply-cost view: what one GPU-hour costs an operator to provide.

Capital is recovered as a level monthly annuity at the operator's financing rate over the
economic life, net of the discounted residual value (so depreciation and financing are not
double counted). Energy and non-energy opex accrue per allocated GPU-hour. The breakeven
price per *revenue* hour divides the capital charge by the utilization fraction. None of
this is a market price: in oversupply prices can fall to the short-run marginal cost (sunk
capex earns nothing) and in scarcity they exceed full cost. The view is a long-run soft
anchor only.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .schema import Economics

HOURS_PER_MONTH_AVG = 365 * 24 / 12  # 730 h, consistent with ACT/365 annual rates.


@dataclass(frozen=True)
class SupplyCost:
    utilization: float
    financing_annual_rate: float
    monthly_rate: float
    pv_residual_usd: float
    capital_per_month_usd: float
    capital_per_calendar_hour_usd: float
    capital_per_revenue_hour_usd: float
    energy_per_hour_usd: float
    non_energy_opex_per_hour_usd: float
    short_run_marginal_cost_usd: float
    breakeven_usd_per_gpu_hour: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def breakeven(
    econ: Economics, utilization: float | None = None, financing: float | None = None
) -> SupplyCost:
    """Full-cost breakeven in USD per revenue GPU-hour for the given utilization/financing."""
    util = float(econ.operator_revenue_utilization_fraction if utilization is None else utilization)
    rate = float(econ.operator_financing_annual_rate if financing is None else financing)
    if not 0 < util <= 1:
        raise ValueError(f"utilization must be in (0, 1], got {util}")
    if rate < 0:
        raise ValueError(f"financing rate must be non-negative, got {rate}")
    capex = float(econ.capex_usd_per_gpu)
    life = econ.economic_life_months
    residual = capex * float(econ.residual_value_fraction)
    pv_residual = residual / (1 + rate) ** (life / 12)
    monthly = (1 + rate) ** (1 / 12) - 1
    annuity = life if monthly == 0 else (1 - (1 + monthly) ** (-life)) / monthly
    capital_per_month = (capex - pv_residual) / annuity
    capital_per_hour = capital_per_month / HOURS_PER_MONTH_AVG
    energy = (
        (float(econ.gpu_power_kw) + float(econ.supporting_power_kw_per_gpu))
        * float(econ.pue)
        * float(econ.electricity_usd_per_kwh)
    )
    opex = float(econ.non_energy_opex_usd_per_allocated_gpu_hour)
    srmc = energy + opex
    return SupplyCost(
        utilization=util,
        financing_annual_rate=rate,
        monthly_rate=monthly,
        pv_residual_usd=pv_residual,
        capital_per_month_usd=capital_per_month,
        capital_per_calendar_hour_usd=capital_per_hour,
        capital_per_revenue_hour_usd=capital_per_hour / util,
        energy_per_hour_usd=energy,
        non_energy_opex_per_hour_usd=opex,
        short_run_marginal_cost_usd=srmc,
        breakeven_usd_per_gpu_hour=capital_per_hour / util + srmc,
    )


def sensitivity_grid(
    econ: Economics, utilizations: list[float], financings: list[float]
) -> list[dict[str, float]]:
    """Breakeven for every (utilization, financing) pair, in declared order."""
    rows: list[dict[str, float]] = []
    for util in utilizations:
        for fin in financings:
            cost = breakeven(econ, util, fin)
            rows.append(
                {
                    "utilization": util,
                    "financing_annual_rate": fin,
                    "breakeven_usd_per_gpu_hour": cost.breakeven_usd_per_gpu_hour,
                }
            )
    return rows
