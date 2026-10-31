"""Normalize every quote to USD per GPU-hour of capacity paid monthly in arrears.

Each step is logged so the report can show, per quote, exactly what was done and why:

1. currency (USD passthrough, documented FX conversion, or explicit exclusion);
2. unit (``gpu_hour`` as is; ``node_hour`` ÷ gpu_count; ``node_month`` ÷ gpu_count ÷ actual
   hours of the stated delivery month – never a flat 720);
3. fees (optional per-hour fee added; optional one-time fee amortized over the window);
4. payment timing (advance payments compounded to the month-end arrears convention at the
   buyer's discount rate; arrears unchanged);
5. quantity (node-sized quotes expressed as single-GPU equivalents using a factor estimated
   from paired on-demand evidence, when such evidence exists; otherwise flagged, not adjusted).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from statistics import median

from .comparability import AS_OF_VALID, CLASS_FLEXIBLE, TIER_COMPARABLE, Classification
from .months import add_months, hours_between, hours_in_month, is_month_start, month_start
from .schema import Case, InputError, Quote

PRICE_PLACES = Decimal("0.000001")
HOURS_PER_YEAR = Decimal(365 * 24)


@dataclass(frozen=True)
class Step:
    name: str
    before: Decimal | None
    after: Decimal | None
    note: str


@dataclass
class NormalizedQuote:
    quote: Quote
    classification: Classification
    usd_per_gpu_hour: Decimal | None
    steps: list[Step] = field(default_factory=list)
    excluded_reason: str | None = None
    quantity_adjusted_usd_per_gpu_hour: Decimal | None = None

    @property
    def usable(self) -> bool:
        return self.usd_per_gpu_hour is not None and self.excluded_reason is None

    @property
    def single_gpu_equivalent(self) -> Decimal | None:
        """Price after the quantity adjustment when one was possible, else the normalized price."""
        if self.quantity_adjusted_usd_per_gpu_hour is not None:
            return self.quantity_adjusted_usd_per_gpu_hour
        return self.usd_per_gpu_hour


def quantize(value: Decimal) -> Decimal:
    return value.quantize(PRICE_PLACES, rounding=ROUND_HALF_EVEN)


def _window_hours(quote: Quote, case: Case) -> tuple[int, str]:
    if quote.delivery_start is not None and quote.delivery_end is not None:
        return hours_between(quote.delivery_start, quote.delivery_end), "stated delivery window"
    start = month_start(case.as_of)
    return hours_in_month(start), f"no delivery window; assumed the as-of month {start:%Y-%m}"


def normalize_quote(quote: Quote, classification: Classification, case: Case) -> NormalizedQuote:
    """Run the normalization ladder for one quote."""
    result = NormalizedQuote(quote=quote, classification=classification, usd_per_gpu_hour=None)
    ref_payment = case.reference_product.payment.lower()
    if "arrears" not in ref_payment:
        raise InputError(
            f"reference_product.payment {case.reference_product.payment!r} unsupported; "
            "the pipeline normalizes to monthly payment in arrears"
        )
    price = quote.price
    # 1. currency
    if quote.currency == "USD":
        result.steps.append(Step("currency", price, price, "USD; no conversion"))
    elif quote.currency in case.fx_rates_to_usd:
        rate = case.fx_rates_to_usd[quote.currency]
        converted = price * rate
        result.steps.append(
            Step(
                "currency",
                price,
                converted,
                f"converted {quote.currency}->USD at case fx rate {rate} USD per unit",
            )
        )
        price = converted
    else:
        result.excluded_reason = (
            f"currency {quote.currency!r} has no fx_rates_to_usd entry in the case; "
            "excluded explicitly rather than converted silently"
        )
        result.steps.append(Step("currency", price, None, result.excluded_reason))
        return result
    # 2. unit
    if quote.unit == "gpu_hour":
        result.steps.append(Step("unit", price, price, "already per GPU-hour"))
    elif quote.unit == "node_hour":
        per_gpu = price / Decimal(quote.gpu_count)
        result.steps.append(Step("unit", price, per_gpu, f"node_hour / {quote.gpu_count} GPUs"))
        price = per_gpu
    elif quote.unit == "node_month":
        if quote.delivery_start is None or quote.delivery_end is None:
            result.excluded_reason = (
                "node_month price without a stated delivery month cannot be normalized"
            )
            result.steps.append(Step("unit", price, None, result.excluded_reason))
            return result
        if not (
            is_month_start(quote.delivery_start)
            and quote.delivery_end == add_months(quote.delivery_start, 1)
        ):
            result.excluded_reason = (
                "node_month requires exactly one UTC calendar month; a longer window "
                "needs an explicit monthly billing schedule"
            )
            result.steps.append(Step("unit", price, None, result.excluded_reason))
            return result
        hours = hours_between(quote.delivery_start, quote.delivery_end)
        per_gpu = price / (Decimal(quote.gpu_count) * Decimal(hours))
        result.steps.append(
            Step(
                "unit",
                price,
                per_gpu,
                f"node_month / ({quote.gpu_count} GPUs x {hours} actual hours "
                f"{quote.delivery_start:%Y-%m-%d}..{quote.delivery_end:%Y-%m-%d})",
            )
        )
        price = per_gpu
    else:  # pragma: no cover - schema validation rejects other units
        raise InputError(f"quote {quote.id}: unit {quote.unit!r} unsupported")
    # 3. fees
    fee_total = Decimal(0)
    fee_notes: list[str] = []
    if quote.fee_usd_per_gpu_hour is not None:
        fee_total += quote.fee_usd_per_gpu_hour
        fee_notes.append(f"+{quote.fee_usd_per_gpu_hour}/GPU-hour recurring fee")
    if quote.one_time_fee_usd is not None and quote.one_time_fee_usd != 0:
        if quote.delivery_start is None or quote.delivery_end is None:
            result.excluded_reason = (
                "one_time_fee_usd given without a delivery window; cannot amortize"
            )
            result.steps.append(Step("fees", price, None, result.excluded_reason))
            return result
        hours = hours_between(quote.delivery_start, quote.delivery_end)
        amortized = quote.one_time_fee_usd / (Decimal(quote.gpu_count) * Decimal(hours))
        fee_total += amortized
        fee_notes.append(
            f"+{quote.one_time_fee_usd} one-time / ({quote.gpu_count} GPUs x {hours} h)"
        )
    if fee_notes:
        with_fees = price + fee_total
        result.steps.append(Step("fees", price, with_fees, "; ".join(fee_notes)))
        price = with_fees
    else:
        result.steps.append(
            Step(
                "fees",
                price,
                price,
                "no fee evidence in the quote; taxes/egress excluded per common terms",
            )
        )
    # 4. payment timing
    if quote.payment == "arrears":
        result.steps.append(
            Step("payment_timing", price, price, "arrears matches the reference convention")
        )
    else:
        if (
            quote.delivery_start is not None
            and quote.delivery_end is not None
            and quote.delivery_end > add_months(month_start(quote.delivery_start), 1)
        ):
            result.excluded_reason = (
                "advance-paid multi-month strip needs an explicit monthly payment schedule; "
                "compounding the whole strip to its final date overstates earlier payments"
            )
            result.steps.append(Step("payment_timing", price, None, result.excluded_reason))
            return result
        hours, basis = _window_hours(quote, case)
        rate = Decimal(1) + case.economics.buyer_discount_annual_rate
        factor = rate ** (Decimal(hours) / HOURS_PER_YEAR)
        timed = price * factor
        result.steps.append(
            Step(
                "payment_timing",
                price,
                timed,
                f"advance payment compounded {hours} h at buyer rate "
                f"{case.economics.buyer_discount_annual_rate} ({basis}); factor {factor:.6f}",
            )
        )
        price = timed
    result.usd_per_gpu_hour = quantize(price)
    return result


def _quantity_evidence(normalized: list[NormalizedQuote]) -> list[NormalizedQuote]:
    """Valid comparable on-demand asks with the same unadjusted payment convention."""
    return [
        item
        for item in normalized
        if item.usable
        and item.classification.as_of_status == AS_OF_VALID
        and item.classification.tier == TIER_COMPARABLE
        and item.classification.contract_class == CLASS_FLEXIBLE
        and item.quote.cancellation.strip().lower() == "anytime"
        and item.quote.side == "ask"
        and item.quote.unit != "node_month"
        and item.quote.kind == "current_on_demand"
        and item.quote.payment == "arrears"
    ]


def estimate_quantity_factor(
    normalized: list[NormalizedQuote], case: Case
) -> tuple[Decimal | None, str]:
    """Per-GPU price ratio (multi-GPU node ÷ single GPU) from paired flexible on-demand asks.

    Only valid, comparable, cancellable hourly asks with the reference payment timing are
    paired, so the ratio isolates granularity/quantity as far as the evidence allows. The
    provider confound is disclosed in the returned note.
    """
    ref_count = case.reference_product.gpu_count
    singles: list[Decimal] = []
    multis: list[Decimal] = []
    used: list[str] = []
    evidence = _quantity_evidence(normalized)
    counts = {item.quote.gpu_count for item in evidence if item.quote.gpu_count != ref_count}
    if len(counts) > 1:
        return None, (
            f"on-demand evidence mixes non-reference GPU counts {sorted(counts)}; "
            "one pooled quantity factor cannot identify a size-specific adjustment"
        )
    for item in evidence:
        q = item.quote
        assert item.usd_per_gpu_hour is not None
        used.append(q.id)
        (singles if q.gpu_count == ref_count else multis).append(item.usd_per_gpu_hour)
    if not singles or not multis:
        return (
            None,
            "no paired single-GPU vs multi-GPU flexible on-demand asks; quantity not adjusted",
        )
    factor = quantize(Decimal(median(multis)) / Decimal(median(singles)))
    return factor, (
        f"median per-GPU price of multi-GPU on-demand asks / median single-GPU asks = {factor} "
        f"(from {', '.join(sorted(used))}; calibrated GPU counts {ref_count} and "
        f"{next(iter(counts))}; providers differ, so this also carries provider effects)"
    )


def apply_quantity_adjustment(
    normalized: list[NormalizedQuote], factor: Decimal | None, case: Case
) -> None:
    """Express node-sized quotes as single-GPU equivalents in place (when a factor exists)."""
    ref_count = case.reference_product.gpu_count
    supported_counts = {
        item.quote.gpu_count
        for item in _quantity_evidence(normalized)
        if item.quote.gpu_count != ref_count
    }
    for item in normalized:
        if not item.usable or item.quote.gpu_count == ref_count:
            continue
        assert item.usd_per_gpu_hour is not None
        supported = (
            len(supported_counts) == 1
            and item.quote.gpu_count in supported_counts
            and item.classification.tier == TIER_COMPARABLE
        )
        if factor is None or factor == 0 or not supported:
            item.steps.append(
                Step(
                    "quantity",
                    item.usd_per_gpu_hour,
                    item.usd_per_gpu_hour,
                    f"gpu_count {item.quote.gpu_count} != reference "
                    f"{ref_count}; no paired evidence for this product and GPU count, not adjusted",
                )
            )
            if item.classification.curve_eligible:
                item.excluded_reason = (
                    f"gpu_count {item.quote.gpu_count} differs from reference {ref_count}; "
                    "no paired quantity evidence for this GPU count supports a comparable "
                    "capacity price"
                )
            continue
        adjusted = quantize(item.usd_per_gpu_hour / factor)
        item.quantity_adjusted_usd_per_gpu_hour = adjusted
        item.steps.append(
            Step(
                "quantity",
                item.usd_per_gpu_hour,
                adjusted,
                f"gpu_count {item.quote.gpu_count} != reference "
                f"{ref_count}; divided by quantity factor {factor}",
            )
        )
