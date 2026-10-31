"""As-of filtering and product comparability (what may enter the curve, and as what).

Three independent classifications are produced for every quote:

* **as-of status** – ``valid`` (observed on/before the cutoff and not expired), ``post_as_of``
  (observed after the cutoff: look-ahead, never used in the primary view) or ``expired``
  (``valid_until`` on/before the cutoff: historical context only).
* **product tier** – ``comparable`` (same hardware/region/isolation/preemption and SLA at
  least the reference), ``different_product`` otherwise. Hardware, region, preemption,
  isolation and SLA are compared exactly, so changing any of them moves the quote out of
  the comparable set.
* **contract class** – ``reserved_capacity`` (guaranteed capacity for a stated delivery
  window with no cancellation: the reference product's contract), ``flexible_on_demand``
  (cancellable hourly usage), ``usage_commitment`` (spend commitment without guaranteed
  future capacity: *not* a forward) or ``spot`` (preemptible).

Only ``valid`` + ``comparable`` + ``reserved_capacity`` quotes become curve nodes. Everything
else is context, a cross-check, or excluded with a recorded reason.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from .months import is_month_start, month_start, months_between
from .schema import Case, Quote, parse_decimal

AS_OF_VALID = "valid"
AS_OF_POST = "post_as_of"
AS_OF_EXPIRED = "expired"

TIER_COMPARABLE = "comparable"
TIER_DIFFERENT = "different_product"

CLASS_RESERVED = "reserved_capacity"
CLASS_FLEXIBLE = "flexible_on_demand"
CLASS_COMMITMENT = "usage_commitment"
CLASS_SPOT = "spot"
CLASS_UNCLASSIFIED = "unclassified_rights"

NO_CANCELLATION_TERMS = {"none", "no", "non-cancellable", "noncancellable"}


@dataclass(frozen=True)
class Classification:
    quote_id: str
    as_of_status: str
    tier: str
    contract_class: str
    delivery_months: tuple[datetime, ...]
    tenor_months: tuple[int, ...]
    reasons: tuple[str, ...] = field(default_factory=tuple)
    product_differences: tuple[str, ...] = field(default_factory=tuple)

    @property
    def curve_eligible(self) -> bool:
        return (
            self.as_of_status == AS_OF_VALID
            and self.tier == TIER_COMPARABLE
            and self.contract_class == CLASS_RESERVED
            and bool(self.delivery_months)
        )


def as_of_status(quote: Quote, as_of: datetime) -> tuple[str, str]:
    """Return ``(status, reason)`` for a quote relative to the cutoff."""
    if quote.observed_at > as_of:
        return AS_OF_POST, (
            f"observed_at {quote.observed_at.isoformat()} is after as-of {as_of.isoformat()} "
            "(look-ahead; excluded from the primary view)"
        )
    if quote.valid_until is not None and quote.valid_until <= as_of:
        return AS_OF_EXPIRED, (
            f"valid_until {quote.valid_until.isoformat()} is on/before as-of "
            f"{as_of.isoformat()} (expired; historical context only)"
        )
    return AS_OF_VALID, "observed on/before as-of and not expired"


def product_differences(quote: Quote, case: Case) -> tuple[str, ...]:
    """List reference-product attributes the quote does not match (empty → comparable)."""
    ref = case.reference_product
    diffs: list[str] = []
    if quote.gpu != ref.gpu:
        diffs.append(f"gpu {quote.gpu!r} != {ref.gpu!r}")
    if ref.memory_gib is not None and quote.memory_gib != ref.memory_gib:
        diffs.append(
            "memory_gib unknown; missing evidence is not proof of the same product"
            if quote.memory_gib is None
            else f"memory_gib {quote.memory_gib} != {ref.memory_gib}"
        )
    if quote.region != ref.region:
        diffs.append(f"region {quote.region!r} != {ref.region!r}")
    if quote.isolation != ref.isolation:
        diffs.append(f"isolation {quote.isolation!r} != {ref.isolation!r}")
    if quote.preemptible != ref.preemptible:
        diffs.append(f"preemptible {quote.preemptible} != {ref.preemptible}")
    if quote.sla_pct < ref.sla_pct:
        diffs.append(f"sla_pct {quote.sla_pct} below reference {ref.sla_pct}")
    # These resources are supplied once for all quotes, rather than repeated in the CSV.
    # A missing common term cannot prove that an explicit reference requirement is met.
    for resource, common_key, per_gpu in (
        ("cpu_cores", "cpu_cores_per_gpu", True),
        ("ram_gib", "ram_gib_per_gpu", True),
        ("network_gbps", "network_gbps", False),
    ):
        required = ref.extra.get(resource)
        if required is None:
            continue
        floor = parse_decimal(required, where=f"reference_product.{resource}", minimum=Decimal(0))
        supplied = case.quote_common_terms.get(common_key)
        if supplied is None:
            diffs.append(f"{resource} unknown; quote_common_terms.{common_key} is missing")
            continue
        available = parse_decimal(
            supplied, where=f"quote_common_terms.{common_key}", minimum=Decimal(0)
        )
        if per_gpu:
            available *= ref.gpu_count
        if available < floor:
            diffs.append(f"{resource} {available} below reference {floor}")
    return tuple(diffs)


def contract_class(quote: Quote) -> tuple[str, str]:
    """Classify the economic rights a quote conveys; returns ``(class, reason)``."""
    if quote.preemptible or quote.kind == "spot_current":
        return CLASS_SPOT, "preemptible/spot capacity: provider can withdraw it"
    if quote.kind == "usage_commitment":
        return CLASS_COMMITMENT, (
            "spend commitment on current usage without a delivery window or guaranteed "
            "capacity: a discount on today's usage, not a forward"
        )
    no_cancel = quote.cancellation.strip().lower() in NO_CANCELLATION_TERMS
    has_window = quote.delivery_start is not None and quote.delivery_end is not None
    if quote.capacity_guaranteed and has_window and no_cancel:
        return CLASS_RESERVED, "guaranteed capacity for a stated window, not cancellable"
    if quote.cancellation.strip().lower() != "anytime":
        return CLASS_UNCLASSIFIED, (
            f"cancellation {quote.cancellation!r} does not establish supported cancel-anytime "
            "rights; neither guaranteed reserved capacity nor flexible hourly access is proven"
        )
    if quote.kind == "firm_forward":
        missing = []
        if not quote.capacity_guaranteed:
            missing.append("capacity not guaranteed")
        if not has_window:
            missing.append("no delivery window")
        if not no_cancel:
            missing.append(f"cancellation {quote.cancellation!r}")
        return CLASS_FLEXIBLE, "labelled firm_forward but " + ", ".join(
            missing
        ) + ": treated as flexible"
    return (
        CLASS_FLEXIBLE,
        f"cancellable ({quote.cancellation!r}) hourly usage without guaranteed future capacity",
    )


def delivery_months_of(quote: Quote) -> tuple[datetime, ...]:
    """Calendar months a delivery window covers; empty when there is no aligned window."""
    if quote.delivery_start is None or quote.delivery_end is None:
        return ()
    if not (is_month_start(quote.delivery_start) and is_month_start(quote.delivery_end)):
        return ()
    return tuple(months_between(quote.delivery_start, quote.delivery_end))


def classify(quote: Quote, case: Case) -> Classification:
    """Full classification of one quote against the case."""
    status, status_reason = as_of_status(quote, case.as_of)
    diffs = product_differences(quote, case)
    tier = TIER_COMPARABLE if not diffs else TIER_DIFFERENT
    klass, class_reason = contract_class(quote)
    months = delivery_months_of(quote)
    reasons = [status_reason, class_reason]
    if diffs:
        reasons.append("product differs: " + "; ".join(diffs))
    if quote.delivery_start is not None and not months:
        reasons.append(
            "delivery window is not aligned to UTC calendar months; not usable as a monthly node"
        )
    base = month_start(case.as_of)
    tenors = tuple((m.year - base.year) * 12 + (m.month - base.month) for m in months)
    if months and min(tenors) < 0:
        reasons.append("delivery window starts before the as-of month; not a forward node")
        months, tenors = (), ()
    return Classification(
        quote_id=quote.id,
        as_of_status=status,
        tier=tier,
        contract_class=klass,
        delivery_months=months,
        tenor_months=tenors,
        reasons=tuple(reasons),
        product_differences=diffs,
    )
