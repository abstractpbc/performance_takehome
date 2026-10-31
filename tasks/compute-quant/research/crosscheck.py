"""Context and cross-checks that are *not* curve inputs.

* different-hardware quotes translated through the synthetic performance ratio, with the
  measurement uncertainty propagated (weak cross-check only; memory/software differ);
* usage commitments (a discount on current usage, not a forward);
* spot/preemptible prices (0% SLA: a different product);
* the flexibility premium between cancellable and reserved current-month capacity;
* expired same-provider quotes as the only historical hint for price variability.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from decimal import Decimal
from statistics import median

from .comparability import (
    AS_OF_EXPIRED,
    AS_OF_VALID,
    CLASS_COMMITMENT,
    CLASS_FLEXIBLE,
    CLASS_RESERVED,
    CLASS_SPOT,
    TIER_COMPARABLE,
)
from .normalize import NormalizedQuote
from .schema import Case, PerformanceRow


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _valid_comparable(items: list[NormalizedQuote]) -> list[NormalizedQuote]:
    return [
        item
        for item in items
        if item.usable
        and item.classification.as_of_status == AS_OF_VALID
        and item.classification.tier == TIER_COMPARABLE
    ]


EXAMPLE_LIMIT = 10


def _summary(values: list[float]) -> dict[str, object]:
    """Count/min/median/max of a price list (never the list itself)."""
    if not values:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(values),
        "min": min(values),
        "median": median(values),
        "max": max(values),
    }


def _bounded(entries: list[dict[str, object]], limit: int = EXAMPLE_LIMIT) -> dict[str, object]:
    """Bounded serialisation of a per-quote list: count + first ``limit`` entries."""
    return {
        "count": len(entries),
        "examples": entries[:limit],
        "example_limit": limit,
        "truncated": max(0, len(entries) - limit),
    }


def performance_cross_check(
    items: list[NormalizedQuote],
    performance: tuple[PerformanceRow, ...],
    case: Case,
    comparable: list[NormalizedQuote] | None = None,
) -> dict[str, object]:
    """Translate valid different-hardware quotes into reference-GPU equivalents.

    Returns a bounded structure: the number of translated quotes, up to ``EXAMPLE_LIMIT``
    example entries, and the same-class comparable price distribution as summary stats
    computed once per (contract_class, side).
    """
    comparable = _valid_comparable(items) if comparable is None else comparable
    ref_rows = [row for row in performance if row.gpu == case.reference_product.gpu]
    perf_by_gpu: dict[str, PerformanceRow] = {}
    for row in performance:
        perf_by_gpu.setdefault(row.gpu, row)
    same_class: dict[tuple[str, str], list[float]] = {}
    for peer in comparable:
        same_class.setdefault((peer.classification.contract_class, peer.quote.side), []).append(
            float(peer.single_gpu_equivalent or 0)
        )
    class_summary = {
        f"{cls}/{side}": _summary(values) for (cls, side), values in sorted(same_class.items())
    }
    entries: list[dict[str, object]] = []
    translated = 0
    for item in items:
        c, q = item.classification, item.quote
        if not item.usable or c.as_of_status != AS_OF_VALID or q.gpu == case.reference_product.gpu:
            continue
        other = perf_by_gpu.get(q.gpu)
        entry: dict[str, object] = {
            "quote_id": q.id,
            "gpu": q.gpu,
            "contract_class": c.contract_class,
            "normalized_usd_per_gpu_hour": _f(item.single_gpu_equivalent),
            "status": "cross-check-only",
        }
        ref = ref_rows[0] if ref_rows else None
        if ref is None or other is None:
            entry["note"] = "no matching performance measurement for both GPUs; no translation"
            entry["equivalent_usd_per_reference_gpu_hour"] = None
        elif ref.workload != other.workload:
            entry["note"] = "performance rows measure different workloads; not translatable"
            entry["equivalent_usd_per_reference_gpu_hour"] = None
        else:
            ratio = float(ref.output_tokens_per_s) / float(other.output_tokens_per_s)
            rel_unc = math.sqrt(
                float(ref.relative_uncertainty) ** 2 + float(other.relative_uncertainty) ** 2
            )
            price = float(item.single_gpu_equivalent or 0)
            translated += 1
            entry.update(
                {
                    "performance_ids": [ref.id, other.id],
                    "workload": ref.workload,
                    "throughput_ratio_reference_over_quote_gpu": ratio,
                    "ratio_relative_uncertainty": rel_unc,
                    "equivalent_usd_per_reference_gpu_hour": price * ratio,
                    "equivalent_low": price * ratio * (1 - rel_unc),
                    "equivalent_high": price * ratio * (1 + rel_unc),
                    "comparable_same_class_summary_key": f"{c.contract_class}/{q.side}",
                    "note": (
                        "throughput parity on one synthetic dense-decode workload; ignores "
                        f"memory ({q.memory_gib} vs {case.reference_product.memory_gib} GiB), "
                        "software and demand mix"
                    ),
                }
            )
        entries.append(entry)
    out = _bounded(entries)
    out["translated"] = translated
    out["comparable_same_class_summary"] = class_summary
    return out


def commitment_context(
    items: list[NormalizedQuote], case: Case, comparable: list[NormalizedQuote] | None = None
) -> dict[str, object]:
    """Usage commitments on the same hardware: discount on current usage, never a forward."""
    comparable = _valid_comparable(items) if comparable is None else comparable
    minimum = case.quote_common_terms.get("usage_commitment_minimum_monthly_gpu_hours")
    out = []
    for item in comparable:
        if item.classification.contract_class != CLASS_COMMITMENT:
            continue
        q = item.quote
        out.append(
            {
                "quote_id": q.id,
                "normalized_usd_per_gpu_hour": _f(item.single_gpu_equivalent),
                "commitment_months": q.commitment_months,
                "capacity_guaranteed": q.capacity_guaranteed,
                "delivery_window": None,
                "minimum_monthly_gpu_hours_per_common_terms": minimum,
                "treatment": (
                    "NOT a forward: no delivery window, no guaranteed capacity, postpaid spend "
                    "commitment; shown as the provider's "
                    "willingness to discount predictable current "
                    "usage, and as a soft long-run cross-check only"
                ),
            }
        )
    return _bounded(out)


def spot_context(
    items: list[NormalizedQuote], case: Case, comparable: list[NormalizedQuote] | None = None
) -> dict[str, object]:
    """Preemptible/spot quotes for the same hardware: a different (0% SLA) product."""
    comparable = _valid_comparable(items) if comparable is None else comparable
    flexible_asks = [
        float(peer.usd_per_gpu_hour or 0)
        for peer in comparable
        if peer.classification.contract_class == CLASS_FLEXIBLE and peer.quote.side == "ask"
    ]
    cheapest = min(flexible_asks) if flexible_asks else None
    out = []
    for item in items:
        c = item.classification
        if not item.usable or c.as_of_status != AS_OF_VALID or c.contract_class != CLASS_SPOT:
            continue
        if item.quote.gpu != case.reference_product.gpu:
            continue
        price = float(item.single_gpu_equivalent or 0)
        out.append(
            {
                "quote_id": item.quote.id,
                "normalized_usd_per_gpu_hour": price,
                "sla_pct": _f(item.quote.sla_pct),
                "discount_vs_cheapest_flexible_on_demand_ask_per_gpu_hour": (
                    None if not cheapest or cheapest <= 0 else 1 - price / cheapest
                ),
                "treatment": "context only: preemptible with "
                "no SLA is not comparable to reserved capacity",
            }
        )
    result = _bounded(out)
    result["cheapest_flexible_on_demand_ask_usd_per_gpu_hour"] = cheapest
    return result


def flexibility_premium(
    items: list[NormalizedQuote], case: Case, comparable: list[NormalizedQuote] | None = None
) -> dict[str, object] | None:
    """Cancellable vs reserved current-month asks at the same gpu_count (payment-normalized)."""
    base = month_start_tenor_zero(items, case, comparable)
    if base is None:
        return None
    reserved, flexible = base
    if float(reserved.usd_per_gpu_hour) <= 0:
        return None
    return {
        "reserved_quote_id": reserved.quote.id,
        "flexible_quote_id": flexible.quote.id,
        "reserved_usd_per_gpu_hour": _f(reserved.usd_per_gpu_hour),
        "flexible_usd_per_gpu_hour": _f(flexible.usd_per_gpu_hour),
        "flexible_over_reserved": float(flexible.usd_per_gpu_hour)
        / float(reserved.usd_per_gpu_hour),
        "note": (
            "contextual as-of price ratio of cancel-anytime hourly access to a non-cancellable "
            "reserved month at the same quantity; different rights and continuity assumptions "
            "prevent interpreting it as a pure option price. There is no future price bound "
            "without assumptions about future hourly prices and capacity availability"
        ),
    }


def month_start_tenor_zero(
    items: list[NormalizedQuote], case: Case, comparable: list[NormalizedQuote] | None = None
) -> tuple[NormalizedQuote, NormalizedQuote] | None:
    """First (reserved tenor-0 ask, flexible ask) pair at the same gpu_count, in input order."""
    comparable = _valid_comparable(items) if comparable is None else comparable
    flexible_by_count: dict[int, NormalizedQuote] = {}
    for flexible in comparable:
        if (
            flexible.classification.contract_class == CLASS_FLEXIBLE
            and flexible.quote.side == "ask"
            and flexible.quote.unit != "node_month"
        ):
            flexible_by_count.setdefault(flexible.quote.gpu_count, flexible)
    for reserved in comparable:
        if (
            reserved.classification.contract_class != CLASS_RESERVED
            or reserved.quote.side != "ask"
            or 0 not in reserved.classification.tenor_months
        ):
            continue
        flexible = flexible_by_count.get(reserved.quote.gpu_count)
        if flexible is not None:
            return reserved, flexible
    return None


HISTORY_EXAMPLE_LIMIT = 5


def historical_variability(
    items: list[NormalizedQuote], case: Case, comparable: list[NormalizedQuote] | None = None
) -> dict[str, object]:
    """Expired quotes paired with the nearest-dated valid quote of the same provider/kind/side/size.

    Each expired quote is matched to at most one valid quote: same provider, kind, side,
    gpu_count and unit, choosing the one whose ``observed_at`` is closest in time (before or
    after; ties go to the later one). The work is O(E log V) and the output is a bounded
    summary plus a few example pairs rather than the expired x valid cross product.
    """

    def key(q: NormalizedQuote) -> tuple:
        return (q.quote.provider, q.quote.kind, q.quote.side, q.quote.gpu_count, q.quote.unit)

    comparable = _valid_comparable(items) if comparable is None else comparable
    current: dict[tuple, list[NormalizedQuote]] = {}
    for item in comparable:
        current.setdefault(key(item), []).append(item)
    for group in current.values():
        group.sort(key=lambda q: q.quote.observed_at)
    stamps = {k: [q.quote.observed_at for q in g] for k, g in current.items()}
    vols: list[float] = []
    log_moves: list[float] = []
    examples: list[dict[str, object]] = []
    pairs = 0
    unmatched = 0
    for old in items:
        c = old.classification
        if not old.usable or c.as_of_status != AS_OF_EXPIRED or c.tier != TIER_COMPARABLE:
            continue
        group = current.get(key(old))
        if not group:
            unmatched += 1
            continue
        pos = bisect_left(stamps[key(old)], old.quote.observed_at)
        candidates = [group[i] for i in (pos - 1, pos) if 0 <= i < len(group)]
        new = min(
            candidates,
            key=lambda q: (
                abs((q.quote.observed_at - old.quote.observed_at).total_seconds()),
                -q.quote.observed_at.timestamp(),
            ),
        )
        months = abs(new.quote.observed_at - old.quote.observed_at).total_seconds() / (
            30.4375 * 86400
        )
        old_p, new_p = float(old.usd_per_gpu_hour), float(new.usd_per_gpu_hour)
        if months <= 0 or old_p <= 0 or new_p <= 0:
            unmatched += 1
            continue
        log_move = math.log(new_p / old_p)
        vol = abs(log_move) / math.sqrt(months)
        pairs += 1
        vols.append(vol)
        log_moves.append(log_move)
        if len(examples) < HISTORY_EXAMPLE_LIMIT:
            examples.append(
                {
                    "expired_quote_id": old.quote.id,
                    "current_quote_id": new.quote.id,
                    "months_apart": months,
                    "expired_usd_per_gpu_hour": old_p,
                    "current_usd_per_gpu_hour": new_p,
                    "log_move": log_move,
                    "implied_vol_per_sqrt_month": vol,
                }
            )
    return {
        "pairs": pairs,
        "unmatched_expired_quotes": unmatched,
        "implied_vol_per_sqrt_month": {
            "median": median(vols) if vols else None,
            "min": min(vols) if vols else None,
            "max": max(vols) if vols else None,
        },
        "log_move": {
            "median": median(log_moves) if log_moves else None,
            "min": min(log_moves) if log_moves else None,
            "max": max(log_moves) if log_moves else None,
        },
        "examples": examples,
        "example_limit": HISTORY_EXAMPLE_LIMIT,
        "treatment": (
            "expired prices are never current levels; each expired quote is paired with the "
            "nearest-dated valid quote of the same provider/kind/side/size only to inform the "
            "declared horizon-widening parameter; a handful of pairs cannot estimate "
            "volatility reliably"
        ),
    }
