"""Forward curve construction with explicit evidence labels and intervals.

Every node is one of:

* ``observed`` – a firm, comparable, reserved-capacity quote for exactly that delivery month
  (two-sided → mid with the bid-ask as the interval; one-sided → the quoted side is one
  interval edge and the other edge is a spread estimate);
* ``implied`` – derived arithmetically from observed comparable quotes (quantity-adjusted or
  strip quotes, or linear interpolation between bracketing anchors);
* ``modeled`` – extrapolated beyond the last anchor with the declared mean-reversion model,
  within the configured horizon;
* ``forecast`` – the same model beyond that horizon (judgmental; scenario band dominates);
* ``unidentified`` – no comparable evidence to anchor on.

The model: ``F(t) = A + (F_L - A) * exp(-kappa * (t - t_L))`` where ``F_L`` is the last anchor,
``A`` the scenario's long-run anchor and ``kappa`` is fitted by least squares on the log
deviations of observed forward nodes from the base anchor (falling back to the declared
default when the shape cannot identify it). Intervals widen with the square root of the
horizon and are unioned with the scenario band. Bands are conditional on retained anchors
and scenarios; evidence removal can change those and has no universal width guarantee.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from .comparability import AS_OF_VALID, TIER_COMPARABLE
from .months import add_months, format_utc, hours_in_month, month_start
from .normalize import NormalizedQuote
from .schema import Case
from .supply_cost import breakeven

STATUS_OBSERVED = "observed"
STATUS_IMPLIED = "implied"
STATUS_MODELED = "modeled"
STATUS_FORECAST = "forecast"
STATUS_UNIDENTIFIED = "unidentified"


@dataclass
class Observation:
    quote_id: str
    tenor: int
    side: str
    price: float
    direct: bool
    note: str


@dataclass(frozen=True)
class Disputed:
    """A tenor whose best bid exceeds its best ask: evidence exists but is not a clean level."""

    tenor: int
    low: float
    high: float
    quote_ids: list[str]


@dataclass
class Anchor:
    tenor: int
    mid: float
    low: float
    high: float
    status: str
    bids: list[Observation] = field(default_factory=list)
    asks: list[Observation] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    uncertainty_type: str = ""

    @property
    def evidence_ids(self) -> list[str]:
        return sorted({o.quote_id for o in self.bids + self.asks})


@dataclass
class Node:
    tenor: int
    delivery_start: datetime
    delivery_end: datetime
    hours: int
    status: str
    mid: float | None
    low: float | None
    high: float | None
    bid: float | None
    ask: float | None
    evidence_ids: list[str]
    uncertainty_type: str
    scenarios: dict[str, float | None]
    notes: list[str]
    months_beyond_last_anchor: float | None

    def as_dict(self) -> dict[str, object]:
        return {
            "tenor_months": self.tenor,
            "delivery_month": self.delivery_start.strftime("%Y-%m"),
            "delivery_start": format_utc(self.delivery_start),
            "delivery_end": format_utc(self.delivery_end),
            "hours": self.hours,
            "status": self.status,
            "usd_per_gpu_hour_mid": _r(self.mid),
            "usd_per_gpu_hour_low": _r(self.low),
            "usd_per_gpu_hour_high": _r(self.high),
            "bid": _r(self.bid),
            "ask": _r(self.ask),
            "evidence_ids": list(self.evidence_ids),
            "uncertainty_type": self.uncertainty_type,
            "scenarios": {key: _r(value) for key, value in self.scenarios.items()},
            "months_beyond_last_anchor": self.months_beyond_last_anchor,
            "notes": list(self.notes),
        }


def _r(value: float | None, places: int = 6) -> float | None:
    return None if value is None else round(value, places)


@dataclass
class CurveModel:
    """Everything needed to price any delivery month under any scenario."""

    case: Case
    config: dict
    anchors: list[Anchor]
    scenario_anchor_levels: dict[str, float | None]
    scenario_anchor_notes: dict[str, str]
    kappa: float
    kappa_source: str
    kappa_detail: dict[str, object]
    reference_spread: float | None
    warnings: list[str] = field(default_factory=list)
    items: list[NormalizedQuote] = field(default_factory=list)
    quantity_factor: object = None
    disputed: dict[int, Disputed] = field(default_factory=dict)

    @property
    def observed_forward_anchors(self) -> list[Anchor]:
        return [a for a in self.anchors if a.status == STATUS_OBSERVED and a.tenor >= 1]

    @property
    def last_anchor(self) -> Anchor | None:
        return self.anchors[-1] if self.anchors else None

    def spread_estimate(self, price: float) -> float:
        frac = float(self.config["curve"]["one_sided_spread_fraction"])
        est = frac * price
        if self.reference_spread is not None:
            est = max(est, self.reference_spread)
        return est

    def tenor_of(self, month: datetime) -> int:
        base = month_start(self.case.as_of)
        return (month.year - base.year) * 12 + (month.month - base.month)

    def _scenario_level_at_anchor(self, anchor: Anchor, scenario: str) -> float:
        side = self.config["scenarios"][scenario]["observed_side"]
        return {"mid": anchor.mid, "low": anchor.low, "high": anchor.high}[side]

    def _extrapolate(
        self, start_level: float, long_run: float | None, dt: float, kappa: float
    ) -> float:
        if long_run is None:
            return start_level
        return long_run + (start_level - long_run) * math.exp(-kappa * dt)

    def price(
        self, tenor: int, scenario: str = "base", kappa: float | None = None
    ) -> tuple[float | None, str, dict]:
        """Scenario point price at a tenor with its status and bookkeeping details."""
        kappa = self.kappa if kappa is None else kappa
        if not self.anchors:
            return None, STATUS_UNIDENTIFIED, {"reason": "no comparable reserved-capacity evidence"}
        exact = [a for a in self.anchors if a.tenor == tenor]
        if exact:
            anchor = exact[0]
            return (
                self._scenario_level_at_anchor(anchor, scenario),
                anchor.status,
                {"anchor": anchor.tenor},
            )
        before = [a for a in self.anchors if a.tenor < tenor]
        after = [a for a in self.anchors if a.tenor > tenor]
        if before and after:
            lo, hi = before[-1], after[0]
            w = (tenor - lo.tenor) / (hi.tenor - lo.tenor)
            level = (1 - w) * self._scenario_level_at_anchor(
                lo, scenario
            ) + w * self._scenario_level_at_anchor(hi, scenario)
            return level, STATUS_IMPLIED, {"interpolated_between": [lo.tenor, hi.tenor]}
        if before:
            last = before[-1]
            dt = tenor - last.tenor
            level = self._extrapolate(
                self._scenario_level_at_anchor(last, scenario),
                self.scenario_anchor_levels.get(scenario),
                dt,
                kappa,
            )
            horizon = float(self.config["curve"]["model_horizon_months_before_forecast"])
            status = STATUS_MODELED if dt <= horizon else STATUS_FORECAST
            return level, status, {"from_anchor": last.tenor, "months_beyond": dt}
        first = after[0]
        return (
            self._scenario_level_at_anchor(first, scenario),
            STATUS_MODELED,
            {"flat_from_first_anchor": first.tenor, "months_before": first.tenor - tenor},
        )

    def _structural_band(self, last: Anchor, dt: float, vol: float) -> tuple[float, float]:
        """Band beyond the last anchor: scenarios x declared kappa range, plus a random walk.

        The union contains the flat extension of the anchor interval (kappa = 0) and the fast
        reversion to every scenario anchor (upper kappa), so it depends only on the anchor
        interval and declared parameters, never on the fitted kappa. Removing evidence can
        change those anchors or scenario levels, so interval width need not be monotone.
        """
        k_lo, k_hi = (float(k) for k in self.config["curve"]["reversion_band_per_month"])
        values: list[float] = []
        for scenario in self.config["scenarios"]:
            start_level = self._scenario_level_at_anchor(last, scenario)
            long_run = self.scenario_anchor_levels.get(scenario)
            for k in (k_lo, k_hi):
                values.append(self._extrapolate(start_level, long_run, dt, k))
        v = vol * math.sqrt(dt) * last.high
        return min(*values, last.low - v), max(*values, last.high + v)

    def node(self, tenor: int, kappa: float | None = None, vol_multiplier: float = 1.0) -> Node:
        """Full node (mid/low/high/status/scenarios) for a tenor."""
        base_start = month_start(self.case.as_of)
        start = add_months(base_start, tenor)
        end = add_months(start, 1)
        mid, status, detail = self.price(tenor, "base", kappa)
        scenarios = {name: self.price(tenor, name, kappa)[0] for name in self.config["scenarios"]}
        notes: list[str] = []
        beyond: float | None = None
        if mid is None:
            return Node(
                tenor,
                start,
                end,
                hours_in_month(start),
                STATUS_UNIDENTIFIED,
                None,
                None,
                None,
                None,
                None,
                [],
                "unidentified",
                scenarios,
                ["no comparable reserved-capacity evidence at any tenor"],
                None,
            )
        vol = float(self.config["curve"]["horizon_vol_per_sqrt_month"]) * vol_multiplier
        exact = [a for a in self.anchors if a.tenor == tenor]
        if exact:
            anchor = exact[0]
            low, high = anchor.low, anchor.high
            bid = max((o.price for o in anchor.bids), default=None)
            ask = min((o.price for o in anchor.asks), default=None)
            notes.extend(anchor.notes)
            return Node(
                tenor,
                start,
                end,
                hours_in_month(start),
                anchor.status,
                mid,
                low,
                high,
                bid,
                ask,
                anchor.evidence_ids,
                anchor.uncertainty_type,
                scenarios,
                notes,
                0.0,
            )
        distance = min(abs(a.tenor - tenor) for a in self.anchors)
        evidence: list[str] = []
        if status == STATUS_IMPLIED:
            lo_t, hi_t = detail["interpolated_between"]
            lo_a = next(a for a in self.anchors if a.tenor == lo_t)
            hi_a = next(a for a in self.anchors if a.tenor == hi_t)
            evidence = sorted(set(lo_a.evidence_ids) | set(hi_a.evidence_ids))
            w = (tenor - lo_t) / (hi_t - lo_t)
            v = vol * math.sqrt(distance) * max(lo_a.high, hi_a.high)
            low = (1 - w) * lo_a.low + w * hi_a.low - v
            high = (1 - w) * lo_a.high + w * hi_a.high + v
            notes.append(f"linear interpolation between anchors at tenors {lo_t} and {hi_t}")
            unc = "interpolation-between-anchors+horizon-widening"
        else:
            beyond = detail.get("months_beyond")
            if beyond is not None:
                last = next(a for a in self.anchors if a.tenor == detail["from_anchor"])
                evidence = list(last.evidence_ids)
                low, high = self._structural_band(last, beyond, vol)
                band = self.config["curve"]["reversion_band_per_month"]
                notes.append(
                    f"mid: mean reversion from the tenor-{last.tenor} anchor toward the base "
                    f"anchor, kappa={self.kappa:.4f}/month ({self.kappa_source}); band: union "
                    f"over scenarios and kappa in {band} plus a random-walk band of "
                    f"{vol:.3f}*sqrt({beyond:g}) around the anchor interval"
                )
            else:
                first = next(a for a in self.anchors if a.tenor == detail["flat_from_first_anchor"])
                v = vol * math.sqrt(distance) * first.high
                low, high = first.low - v, first.high + v
                evidence = list(first.evidence_ids)
                notes.append(f"flat extension from the first anchor at tenor {first.tenor}")
            unc = (
                "model-band+scenario-union"
                if status == STATUS_MODELED
                else "judgmental-band+scenario-union"
            )
        finite = [v for v in scenarios.values() if v is not None]
        low = max(0.0, min(low, mid, *finite))
        high = max(high, mid, *finite)
        dispute = self.disputed.get(tenor)
        if dispute is not None:
            low, high = min(low, dispute.low), max(high, dispute.high)
            evidence = sorted(set(evidence) | set(dispute.quote_ids))
            unc += "+crossed-market-union"
            notes.append(
                f"crossed market from {dispute.quote_ids} (bid above ask): not used as a level; "
                f"interval widened to cover {dispute.low:.4f}-{dispute.high:.4f}"
            )
        return Node(
            tenor,
            start,
            end,
            hours_in_month(start),
            status,
            mid,
            low,
            high,
            None,
            None,
            evidence,
            unc,
            scenarios,
            notes,
            beyond,
        )


def _collect_observations(items: list[NormalizedQuote]) -> list[Observation]:
    obs: list[Observation] = []
    for item in items:
        c = item.classification
        if not item.usable or not c.curve_eligible:
            continue
        price = float(item.single_gpu_equivalent)
        adjusted = item.quantity_adjusted_usd_per_gpu_hour is not None
        strip = len(c.tenor_months) > 1
        direct = not adjusted and not strip
        note_bits = []
        if adjusted:
            note_bits.append("quantity-adjusted node quote")
        if strip:
            note_bits.append(f"strip average over {len(c.tenor_months)} months")
        for tenor in c.tenor_months:
            obs.append(
                Observation(
                    item.quote.id, tenor, item.quote.side, price, direct, "; ".join(note_bits)
                )
            )
    return obs


def _build_anchors(
    observations: list[Observation], config: dict
) -> tuple[list[Anchor], float | None, list[str]]:
    warnings: list[str] = []
    tenors = sorted({o.tenor for o in observations})
    by_tenor: dict[int, list[Observation]] = {
        t: [o for o in observations if o.tenor == t] for t in tenors
    }
    # Prefer direct single-month quotes; use derived ones only when nothing direct exists.
    chosen: dict[int, list[Observation]] = {}
    for tenor, group in by_tenor.items():
        direct = [o for o in group if o.direct]
        chosen[tenor] = direct if direct else group
    two_sided_spreads: list[float] = []
    for group in chosen.values():
        bids = [o.price for o in group if o.side == "bid"]
        asks = [o.price for o in group if o.side == "ask"]
        if bids and asks and min(asks) >= max(bids):
            two_sided_spreads.append(min(asks) - max(bids))
    reference_spread = max(two_sided_spreads) if two_sided_spreads else None
    frac = float(config["curve"]["one_sided_spread_fraction"])
    anchors: list[Anchor] = []
    disputed: dict[int, Disputed] = {}
    for tenor, group in chosen.items():
        bids = [o for o in group if o.side == "bid"]
        asks = [o for o in group if o.side == "ask"]
        status = STATUS_OBSERVED if all(o.direct for o in group) else STATUS_IMPLIED
        notes = sorted({o.note for o in group if o.note})
        best_bid = max((o.price for o in bids), default=None)
        best_ask = min((o.price for o in asks), default=None)
        if best_bid is not None and best_ask is not None:
            if best_bid > best_ask:
                ids = sorted({o.quote_id for o in group})
                warnings.append(
                    f"tenor {tenor}: crossed market (bid {best_bid} > ask {best_ask}) from "
                    f"{ids}; not a clean observation - excluded from anchors and kappa fit, "
                    "node modeled with the interval widened to cover both prices"
                )
                disputed[tenor] = Disputed(tenor, best_ask, best_bid, ids)
                continue
            low, high = best_bid, best_ask
            unc = "bid-ask"
            mid = (best_bid + best_ask) / 2
            if frac * mid < (high - low):
                warnings.append(
                    f"tenor {tenor}: observed relative spread "
                    f"{(high - low) / mid:.3f} exceeds the one-sided "
                    f"fallback fraction {frac}; raise "
                    "curve.one_sided_spread_fraction to keep removal-widening"
                )
        elif best_ask is not None:
            est = max(frac * best_ask, reference_spread or 0.0)
            mid, low, high = best_ask - est / 2, best_ask - est, best_ask
            unc = "one-sided(ask)-spread-estimate"
            notes.append(f"ask only: implied bid = ask - spread estimate {est:.4f}")
        else:
            assert best_bid is not None
            est = max(frac * best_bid, reference_spread or 0.0)
            mid, low, high = best_bid + est / 2, best_bid, best_bid + est
            unc = "one-sided(bid)-spread-estimate"
            notes.append(f"bid only: implied ask = bid + spread estimate {est:.4f}")
        # Prices are schema-checked > 0 and non-positive normalized prices are excluded
        # upstream; only a one-sided spread estimate larger than a tiny price can push the
        # implied side below zero, and then the interval is clamped at zero and flagged.
        if mid <= 0 or low < 0:
            warnings.append(
                f"tenor {tenor}: spread estimate exceeds the quoted price; implied side and "
                "mid clamped at zero - treat this node as weakly identified"
            )
        anchors.append(
            Anchor(
                tenor, max(0.0, mid), max(0.0, low), max(0.0, high), status, bids, asks, notes, unc
            )
        )
    return anchors, reference_spread, warnings, disputed


def _fit_kappa(
    anchors: list[Anchor], long_run: float | None, config: dict
) -> tuple[float, str, dict[str, object]]:
    cfg = config["curve"]
    default = float(cfg["default_reversion_per_month"])
    cap = float(cfg["max_reversion_per_month"])
    usable = [
        a
        for a in anchors
        if a.status == STATUS_OBSERVED and a.tenor >= int(cfg["fit_min_tenor_months"])
    ]
    detail: dict[str, object] = {
        "observed_forward_tenors": [a.tenor for a in usable],
        "long_run_anchor": long_run,
    }
    if long_run is None or len(usable) < 2:
        detail["reason"] = "fewer than two observed forward nodes (or no anchor); default used"
        return default, "config-default", detail
    devs = [(a.tenor, a.mid - long_run) for a in usable]
    signs = {math.copysign(1, d) for _, d in devs if d != 0}
    if len(signs) != 1 or any(d == 0 for _, d in devs):
        detail["reason"] = (
            "observed nodes straddle the long-run anchor; reversion speed not identified"
        )
        return default, "config-default", detail
    xs = [t for t, _ in devs]
    ys = [math.log(abs(d)) for _, d in devs]
    xbar, ybar = sum(xs) / len(xs), sum(ys) / len(ys)
    sxx = sum((x - xbar) ** 2 for x in xs)
    sxy = sum((x - xbar) * (y - ybar) for x, y in zip(xs, ys, strict=True))
    slope = sxy / sxx
    kappa = -slope
    detail["least_squares_slope_of_log_deviation"] = slope
    if kappa < 0:
        detail["reason"] = "observed curve moves away from the anchor; reversion clamped to 0"
        return 0.0, "fitted-clamped-to-zero", detail
    if kappa > cap:
        detail["reason"] = f"fitted {kappa:.4f} exceeds cap {cap}; clamped"
        return cap, "fitted-clamped-to-cap", detail
    return kappa, "fitted-least-squares-log-deviation", detail


def _anchor_level(
    spec: dict, items: list[NormalizedQuote], case: Case, anchors: list[Anchor], config: dict
) -> tuple[float | None, str]:
    kind = spec["kind"]
    if kind == "supply_cost_breakeven":
        util = None if spec.get("utilization", "case") == "case" else float(spec["utilization"])
        fin = None if spec.get("financing", "case") == "case" else float(spec["financing"])
        cost = breakeven(case.economics, util, fin)
        return cost.breakeven_usd_per_gpu_hour, (
            f"supply-cost breakeven at utilization {cost.utilization:.2f} and financing "
            f"{cost.financing_annual_rate:.2%}: {cost.breakeven_usd_per_gpu_hour:.4f} USD/GPU-hour"
        )
    if kind == "max_valid_same_hardware_ask":
        asks = [
            float(item.single_gpu_equivalent)
            for item in items
            if item.usable
            and item.classification.as_of_status == AS_OF_VALID
            and item.classification.tier == TIER_COMPARABLE
            and item.quote.side == "ask"
        ]
        if asks:
            return max(asks), (
                "highest valid comparable-hardware ask at as-of "
                f"(any contract class): {max(asks):.4f} USD/GPU-hour"
            )
        if anchors:
            level = max(a.high for a in anchors) * (
                1 + float(config["curve"]["one_sided_spread_fraction"])
            )
            return (
                level,
                f"no flexible asks; highest anchor plus the one-sided spread fraction: {level:.4f}",
            )
        return None, "no evidence for a scarcity anchor"
    if kind == "fixed":
        return float(
            spec["usd_per_gpu_hour"]
        ), f"fixed anchor {float(spec['usd_per_gpu_hour']):.4f}"
    raise ValueError(f"unknown anchor kind {kind!r}")


def build_curve_model(items: list[NormalizedQuote], case: Case, config: dict) -> CurveModel:
    """Assemble anchors, scenario anchor levels and the reversion speed."""
    observations = _collect_observations(items)
    anchors, reference_spread, warnings, disputed = _build_anchors(observations, config)
    levels: dict[str, float | None] = {}
    notes: dict[str, str] = {}
    for scenario, spec in config["scenarios"].items():
        level, note = _anchor_level(config["anchors"][spec["anchor"]], items, case, anchors, config)
        levels[scenario] = level
        notes[scenario] = note
    kappa, source, detail = _fit_kappa(anchors, levels.get("base"), config)
    if not any(a.status == STATUS_OBSERVED and a.tenor >= 1 for a in anchors):
        warnings.append(
            "no observed forward node: every forward tenor "
            "is implied/modeled/forecast or unidentified"
        )
    return CurveModel(
        case,
        config,
        anchors,
        levels,
        notes,
        kappa,
        source,
        detail,
        reference_spread,
        warnings,
        disputed=disputed,
    )
