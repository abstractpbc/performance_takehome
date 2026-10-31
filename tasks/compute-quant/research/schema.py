"""Typed records for the Compute Quant input contract (``schema_version`` 1).

The organizer's ``data/README.md`` defines the fields. Everything here is parsing and
validation only; no economics. Unknown columns are tolerated and recorded, missing
optional fields become ``None`` (unknown, never zero), and malformed required fields
raise :class:`InputError` with the offending row id.

Numeric bounds (decided per field; 0 is only accepted where it is meaningful):

======================================  ==========  ==================================
field                                   bound       why
======================================  ==========  ==================================
quote.price                             > 0         a zero price is not a quote
quote.gpu_count, performance.gpu_count  >= 1        counts
quote.sla_pct, reference/reservation    0..100      0% SLA = spot, 100% = perfect
quote.commitment_months                 >= 0        0 = no term
quote.fee_usd_per_gpu_hour, one_time    >= 0        0 = no fee
performance.output_tokens_per_s         > 0         ratio denominator
performance.relative_uncertainty        0..1        relative error
fx_rates_to_usd.<CCY>                   > 0         conversion factor
reservation.fixed_usd_per_gpu_hour      > 0         a free reservation is not priced
reservation.expected_buyer_used_fraction 0..1       informational share
economics.* money/power/rates           >= 0        0 = free/none (life, PUE >= 1-ish are
                                                    checked where they divide)
curve.tenor_months                      >= 0        0 = as-of month
======================================  ==========  ==================================
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation

from .months import parse_utc

SUPPORTED_SCHEMA_VERSIONS = (1,)
QUOTE_KINDS = ("current_on_demand", "usage_commitment", "firm_forward", "spot_current")
QUOTE_SIDES = ("ask", "bid")
QUOTE_UNITS = ("gpu_hour", "node_hour", "node_month")
PAYMENT_TERMS = ("arrears", "advance")

REQUIRED_QUOTE_COLUMNS = (
    "id",
    "observed_at",
    "kind",
    "side",
    "gpu",
    "gpu_count",
    "region",
    "isolation",
    "preemptible",
    "sla_pct",
    "currency",
    "price",
    "unit",
    "payment",
    "cancellation",
)
OPTIONAL_QUOTE_COLUMNS = (
    "synthetic",
    "source_ref",
    "provider",
    "valid_until",
    "memory_gib",
    "delivery_start",
    "delivery_end",
    "commitment_months",
    "capacity_guaranteed",
    # Candidate-defined optional fee columns (absent in the demo pack → unknown/none).
    "fee_usd_per_gpu_hour",
    "one_time_fee_usd",
)


class InputError(ValueError):
    """Raised for malformed or unsupported inputs; message names the row and field."""


@dataclass(frozen=True)
class ReferenceProduct:
    gpu: str
    memory_gib: int | None
    gpu_count: int
    region: str
    isolation: str
    preemptible: bool
    sla_pct: Decimal
    payment: str
    cancellation: str
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class Economics:
    capex_usd_per_gpu: Decimal
    economic_life_months: int
    residual_value_fraction: Decimal
    gpu_power_kw: Decimal
    supporting_power_kw_per_gpu: Decimal
    pue: Decimal
    electricity_usd_per_kwh: Decimal
    non_energy_opex_usd_per_allocated_gpu_hour: Decimal
    operator_revenue_utilization_fraction: Decimal
    operator_financing_annual_rate: Decimal
    buyer_discount_annual_rate: Decimal
    provider_default_probability_over_deal: Decimal
    default_recovery_fraction: Decimal


@dataclass(frozen=True)
class Reservation:
    id: str
    gpu_count: int
    delivery_start: datetime
    delivery_end: datetime
    fixed_usd_per_gpu_hour: Decimal
    payment: str
    cancellation: str
    expected_buyer_used_fraction: Decimal | None
    sla_pct: Decimal
    service_credit_policy: str | None


KNOWN_ECONOMICS_KEYS = frozenset(
    {
        "capex_usd_per_gpu_including_supporting_hardware",
        "economic_life_months",
        "residual_value_fraction",
        "gpu_power_kw",
        "supporting_power_kw_per_gpu",
        "pue",
        "electricity_usd_per_kwh",
        "non_energy_opex_usd_per_allocated_gpu_hour",
        "operator_revenue_utilization_fraction",
        "operator_financing_annual_rate",
        "buyer_discount_annual_rate",
        "provider_default_probability_over_deal",
        "default_recovery_fraction",
        "note",
        "source_ref",
        "synthetic",
    }
)
KNOWN_RESERVATION_KEYS = frozenset(
    {
        "id",
        "gpu_count",
        "delivery_start",
        "delivery_end",
        "delivery_end_exclusive",
        "fixed_usd_per_gpu_hour",
        "payment",
        "cancellation",
        "expected_buyer_used_fraction",
        "sla_pct",
        "service_credit_policy",
        "egress_and_taxes",
    }
)
KNOWN_REFERENCE_PRODUCT_KEYS = frozenset(
    {
        "gpu",
        "memory_gib",
        "gpu_count",
        "region",
        "isolation",
        "preemptible",
        "sla_pct",
        "payment",
        "cancellation",
        "cpu_cores",
        "ram_gib",
        "network_gbps",
        "delivery",
        "quote_unit",
        "included_egress_gib",
        "network_egress_usd_per_gib",
        "note",
        "source_ref",
    }
)
KNOWN_QUOTE_COMMON_TERMS_KEYS = frozenset(
    {
        "usage_commitment_minimum_monthly_gpu_hours",
        "cpu_cores_per_gpu",
        "ram_gib_per_gpu",
        "network_gbps",
        "node_interconnect",
        "includes_energy",
        "egress_pricing",
        "taxes_included",
        "note",
    }
)
KNOWN_CASE_KEYS = frozenset(
    {
        "schema_version",
        "task",
        "kind",
        "synthetic",
        "as_of",
        "files",
        "reference_product",
        "curve",
        "quote_common_terms",
        "economics",
        "reservation",
        "fx_rates_to_usd",
    }
)


@dataclass(frozen=True)
class Case:
    schema_version: int
    unknown_keys: tuple[str, ...]
    unknown_nested_keys: tuple[str, ...]
    task: str
    synthetic: bool
    as_of: datetime
    quotes_path: str
    performance_path: str | None
    reference_product: ReferenceProduct
    tenor_months: tuple[int, ...]
    economics: Economics
    reservation: Reservation
    quote_common_terms: dict[str, object]
    fx_rates_to_usd: dict[str, Decimal]
    raw: dict[str, object]


@dataclass(frozen=True)
class Quote:
    id: str
    synthetic: bool | None
    source_ref: str | None
    provider: str | None
    observed_at: datetime
    valid_until: datetime | None
    kind: str
    side: str
    gpu: str
    memory_gib: int | None
    gpu_count: int
    region: str
    isolation: str
    preemptible: bool
    sla_pct: Decimal
    currency: str
    price: Decimal
    unit: str
    delivery_start: datetime | None
    delivery_end: datetime | None
    commitment_months: int | None
    capacity_guaranteed: bool | None
    payment: str
    cancellation: str
    fee_usd_per_gpu_hour: Decimal | None
    one_time_fee_usd: Decimal | None
    unknown_columns: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PerformanceRow:
    id: str
    gpu: str
    gpu_count: int
    workload: str
    output_tokens_per_s: Decimal
    relative_uncertainty: Decimal
    extra: dict[str, str] = field(default_factory=dict)


def _blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def parse_bool(value: object, *, where: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    raise InputError(f"{where}: expected a boolean, got {value!r}")


def parse_decimal(
    value: object,
    *,
    where: str,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
    exclusive_minimum: bool = False,
) -> Decimal:
    try:
        number = Decimal(str(value).strip())
    except (InvalidOperation, ValueError) as exc:
        raise InputError(f"{where}: expected a number, got {value!r}") from exc
    if not number.is_finite():
        raise InputError(f"{where}: expected a finite number, got {value!r}")
    if minimum is not None and number < minimum:
        raise InputError(f"{where}: {number} is below the minimum {minimum}")
    if minimum is not None and exclusive_minimum and number == minimum:
        raise InputError(f"{where}: {number} must be strictly greater than {minimum}")
    if maximum is not None and number > maximum:
        raise InputError(f"{where}: {number} is above the maximum {maximum}")
    return number


def parse_int(value: object, *, where: str, minimum: int | None = None) -> int:
    number = parse_decimal(value, where=where)
    if number != number.to_integral_value():
        raise InputError(f"{where}: expected an integer, got {value!r}")
    result = int(number)
    if minimum is not None and result < minimum:
        raise InputError(f"{where}: {result} is below the minimum {minimum}")
    return result


def parse_timestamp(value: object, *, where: str) -> datetime:
    try:
        return parse_utc(str(value))
    except ValueError as exc:
        raise InputError(f"{where}: {exc}") from exc


def _optional(value: object, parser, **kwargs):
    if _blank(value):
        return None
    return parser(value, **kwargs)


def _choice(value: object, choices: tuple[str, ...], *, where: str) -> str:
    text = str(value).strip()
    if text not in choices:
        raise InputError(f"{where}: {text!r} is not one of {list(choices)}")
    return text


def quote_from_row(row: dict[str, str]) -> Quote:
    """Build a :class:`Quote` from one CSV row, validating required fields."""
    qid = str(row.get("id", "")).strip()
    if not qid:
        raise InputError("quote row without an id")
    missing = [column for column in REQUIRED_QUOTE_COLUMNS if _blank(row.get(column))]
    if missing:
        raise InputError(f"quote {qid}: missing required fields {missing}")
    where = f"quote {qid}"
    known = set(REQUIRED_QUOTE_COLUMNS) | set(OPTIONAL_QUOTE_COLUMNS)
    unknown = {key: value for key, value in row.items() if key not in known and key is not None}
    delivery_start = _optional(
        row.get("delivery_start"), parse_timestamp, where=f"{where}.delivery_start"
    )
    delivery_end = _optional(
        row.get("delivery_end"), parse_timestamp, where=f"{where}.delivery_end"
    )
    if (delivery_start is None) != (delivery_end is None):
        raise InputError(f"{where}: delivery_start and delivery_end must be given together")
    if delivery_start is not None and delivery_end is not None and delivery_end <= delivery_start:
        raise InputError(f"{where}: delivery_end must be after delivery_start")
    return Quote(
        id=qid,
        synthetic=_optional(row.get("synthetic"), parse_bool, where=f"{where}.synthetic"),
        source_ref=None if _blank(row.get("source_ref")) else row["source_ref"].strip(),
        provider=None if _blank(row.get("provider")) else row["provider"].strip(),
        observed_at=parse_timestamp(row["observed_at"], where=f"{where}.observed_at"),
        valid_until=_optional(
            row.get("valid_until"), parse_timestamp, where=f"{where}.valid_until"
        ),
        kind=_choice(row["kind"], QUOTE_KINDS, where=f"{where}.kind"),
        side=_choice(row["side"], QUOTE_SIDES, where=f"{where}.side"),
        gpu=row["gpu"].strip(),
        memory_gib=_optional(
            row.get("memory_gib"), parse_int, where=f"{where}.memory_gib", minimum=1
        ),
        gpu_count=parse_int(row["gpu_count"], where=f"{where}.gpu_count", minimum=1),
        region=row["region"].strip(),
        isolation=row["isolation"].strip(),
        preemptible=parse_bool(row["preemptible"], where=f"{where}.preemptible"),
        sla_pct=parse_decimal(
            row["sla_pct"], where=f"{where}.sla_pct", minimum=Decimal(0), maximum=Decimal(100)
        ),
        currency=row["currency"].strip().upper(),
        price=parse_decimal(
            row["price"], where=f"{where}.price", minimum=Decimal(0), exclusive_minimum=True
        ),
        unit=_choice(row["unit"], QUOTE_UNITS, where=f"{where}.unit"),
        delivery_start=delivery_start,
        delivery_end=delivery_end,
        commitment_months=_optional(
            row.get("commitment_months"), parse_int, where=f"{where}.commitment_months", minimum=0
        ),
        capacity_guaranteed=_optional(
            row.get("capacity_guaranteed"), parse_bool, where=f"{where}.capacity_guaranteed"
        ),
        payment=_choice(row["payment"], PAYMENT_TERMS, where=f"{where}.payment"),
        cancellation=row["cancellation"].strip(),
        fee_usd_per_gpu_hour=_optional(
            row.get("fee_usd_per_gpu_hour"),
            parse_decimal,
            where=f"{where}.fee_usd_per_gpu_hour",
            minimum=Decimal(0),
        ),
        one_time_fee_usd=_optional(
            row.get("one_time_fee_usd"),
            parse_decimal,
            where=f"{where}.one_time_fee_usd",
            minimum=Decimal(0),
        ),
        unknown_columns=unknown,
    )


def performance_from_row(row: dict[str, str]) -> PerformanceRow:
    """Build a :class:`PerformanceRow` from one CSV row."""
    pid = str(row.get("id", "")).strip()
    if not pid:
        raise InputError("performance row without an id")
    where = f"performance {pid}"
    for column in ("gpu", "output_tokens_per_s", "relative_uncertainty"):
        if _blank(row.get(column)):
            raise InputError(f"{where}: missing required field {column!r}")
    known = {"id", "gpu", "gpu_count", "workload", "output_tokens_per_s", "relative_uncertainty"}
    return PerformanceRow(
        id=pid,
        gpu=row["gpu"].strip(),
        gpu_count=parse_int(row.get("gpu_count") or "1", where=f"{where}.gpu_count", minimum=1),
        workload=(row.get("workload") or "unspecified").strip(),
        output_tokens_per_s=parse_decimal(
            row["output_tokens_per_s"],
            where=f"{where}.output_tokens_per_s",
            minimum=Decimal(0),
            exclusive_minimum=True,
        ),
        relative_uncertainty=parse_decimal(
            row["relative_uncertainty"],
            where=f"{where}.relative_uncertainty",
            minimum=Decimal(0),
            maximum=Decimal(1),
        ),
        extra={key: value for key, value in row.items() if key not in known and key is not None},
    )


def _require(mapping: dict[str, object], key: str, *, where: str) -> object:
    if key not in mapping or _blank(mapping[key]):
        raise InputError(f"{where}: missing required field {key!r}")
    return mapping[key]


def case_from_mapping(raw: dict[str, object]) -> Case:
    """Validate the case JSON mapping and return a :class:`Case`."""
    schema_version = parse_int(
        _require(raw, "schema_version", where="case"), where="case.schema_version"
    )
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise InputError(
            f"case.schema_version {schema_version} unsupported; "
            f"supported {SUPPORTED_SCHEMA_VERSIONS}"
        )
    files = _require(raw, "files", where="case")
    if not isinstance(files, dict) or _blank(files.get("quotes")):
        raise InputError("case.files.quotes is required")
    product_raw = _require(raw, "reference_product", where="case")
    if not isinstance(product_raw, dict):
        raise InputError("case.reference_product must be an object")
    core_keys = {
        "gpu",
        "memory_gib",
        "gpu_count",
        "region",
        "isolation",
        "preemptible",
        "sla_pct",
        "payment",
        "cancellation",
    }
    product = ReferenceProduct(
        gpu=str(_require(product_raw, "gpu", where="reference_product")).strip(),
        memory_gib=_optional(
            product_raw.get("memory_gib"), parse_int, where="reference_product.memory_gib"
        ),
        gpu_count=parse_int(
            product_raw.get("gpu_count", 1), where="reference_product.gpu_count", minimum=1
        ),
        region=str(_require(product_raw, "region", where="reference_product")).strip(),
        isolation=str(_require(product_raw, "isolation", where="reference_product")).strip(),
        preemptible=parse_bool(
            _require(product_raw, "preemptible", where="reference_product"),
            where="reference_product.preemptible",
        ),
        sla_pct=parse_decimal(
            _require(product_raw, "sla_pct", where="reference_product"),
            where="reference_product.sla_pct",
            minimum=Decimal(0),
            maximum=Decimal(100),
        ),
        payment=str(_require(product_raw, "payment", where="reference_product")).strip(),
        cancellation=str(_require(product_raw, "cancellation", where="reference_product")).strip(),
        extra={key: value for key, value in product_raw.items() if key not in core_keys},
    )
    curve_raw = raw.get("curve") or {}
    tenors_raw = curve_raw.get("tenor_months") if isinstance(curve_raw, dict) else None
    if not tenors_raw:
        raise InputError("case.curve.tenor_months is required")
    tenors = tuple(parse_int(item, where="curve.tenor_months", minimum=0) for item in tenors_raw)
    if len(set(tenors)) != len(tenors):
        raise InputError("case.curve.tenor_months must not repeat")
    econ_raw = _require(raw, "economics", where="case")
    if not isinstance(econ_raw, dict):
        raise InputError("case.economics must be an object")

    def econ(key: str, minimum: Decimal | None = Decimal(0)) -> Decimal:
        return parse_decimal(
            _require(econ_raw, key, where="economics"), where=f"economics.{key}", minimum=minimum
        )

    economics = Economics(
        capex_usd_per_gpu=econ("capex_usd_per_gpu_including_supporting_hardware"),
        economic_life_months=parse_int(
            _require(econ_raw, "economic_life_months", where="economics"),
            where="economics.economic_life_months",
            minimum=1,
        ),
        residual_value_fraction=econ("residual_value_fraction"),
        gpu_power_kw=econ("gpu_power_kw"),
        supporting_power_kw_per_gpu=econ("supporting_power_kw_per_gpu"),
        pue=econ("pue", minimum=Decimal(1)),
        electricity_usd_per_kwh=econ("electricity_usd_per_kwh"),
        non_energy_opex_usd_per_allocated_gpu_hour=econ(
            "non_energy_opex_usd_per_allocated_gpu_hour"
        ),
        operator_revenue_utilization_fraction=econ("operator_revenue_utilization_fraction"),
        operator_financing_annual_rate=econ("operator_financing_annual_rate"),
        buyer_discount_annual_rate=econ("buyer_discount_annual_rate"),
        provider_default_probability_over_deal=econ("provider_default_probability_over_deal"),
        default_recovery_fraction=econ("default_recovery_fraction"),
    )
    for name, value in (
        ("residual_value_fraction", economics.residual_value_fraction),
        ("operator_revenue_utilization_fraction", economics.operator_revenue_utilization_fraction),
        (
            "provider_default_probability_over_deal",
            economics.provider_default_probability_over_deal,
        ),
        ("default_recovery_fraction", economics.default_recovery_fraction),
    ):
        if value > 1:
            raise InputError(f"economics.{name} must be a fraction in [0, 1], got {value}")
    if economics.operator_revenue_utilization_fraction == 0:
        raise InputError("economics.operator_revenue_utilization_fraction must be positive")
    res_raw = _require(raw, "reservation", where="case")
    if not isinstance(res_raw, dict):
        raise InputError("case.reservation must be an object")
    start = parse_timestamp(
        _require(res_raw, "delivery_start", where="reservation"), where="reservation.delivery_start"
    )
    end = parse_timestamp(
        _require(res_raw, "delivery_end", where="reservation"), where="reservation.delivery_end"
    )
    if end <= start:
        raise InputError("reservation.delivery_end must be after delivery_start")
    if res_raw.get("delivery_end_exclusive") is not None and not parse_bool(
        res_raw["delivery_end_exclusive"], where="reservation.delivery_end_exclusive"
    ):
        raise InputError(
            "reservation.delivery_end_exclusive=false is not supported; windows are [start, end)"
        )
    reservation = Reservation(
        id=str(res_raw.get("id") or "reservation"),
        gpu_count=parse_int(
            _require(res_raw, "gpu_count", where="reservation"),
            where="reservation.gpu_count",
            minimum=1,
        ),
        delivery_start=start,
        delivery_end=end,
        fixed_usd_per_gpu_hour=parse_decimal(
            _require(res_raw, "fixed_usd_per_gpu_hour", where="reservation"),
            where="reservation.fixed_usd_per_gpu_hour",
            minimum=Decimal(0),
            exclusive_minimum=True,
        ),
        payment=str(_require(res_raw, "payment", where="reservation")).strip(),
        cancellation=str(res_raw.get("cancellation") or "unspecified").strip(),
        expected_buyer_used_fraction=_optional(
            res_raw.get("expected_buyer_used_fraction"),
            parse_decimal,
            where="reservation.expected_buyer_used_fraction",
            minimum=Decimal(0),
            maximum=Decimal(1),
        ),
        sla_pct=parse_decimal(
            res_raw.get("sla_pct", product.sla_pct),
            where="reservation.sla_pct",
            minimum=Decimal(0),
            maximum=Decimal(100),
        ),
        service_credit_policy=None
        if _blank(res_raw.get("service_credit_policy"))
        else str(res_raw["service_credit_policy"]),
    )
    if reservation.payment != "monthly-in-arrears-at-delivery-month-end":
        raise InputError(
            f"reservation.payment {reservation.payment!r} unsupported; "
            "only monthly arrears at month end is modeled"
        )
    if product.payment != "monthly-in-arrears-at-delivery-month-end":
        raise InputError("reference_product.payment: only monthly arrears at month end is modeled")
    for label, cancellation in (
        ("reference_product", product.cancellation), ("reservation", reservation.cancellation)
    ):
        if cancellation.strip().lower() not in {"none", "no", "non-cancellable", "noncancellable"}:
            raise InputError(
                f"{label}.cancellation {cancellation!r} unsupported; optionality is not modeled"
            )
    supported_credit_policy = (
        "pro-rata-fee-credit-for-unavailable-hours-no-additional-consequential-damages"
    )
    if reservation.service_credit_policy != supported_credit_policy:
        raise InputError(
            "reservation.service_credit_policy must specify the supported pro-rata fee credit; "
            "unknown or different remedies cannot be valued as identical"
        )
    fx_raw = raw.get("fx_rates_to_usd") or {}
    if not isinstance(fx_raw, dict):
        raise InputError("case.fx_rates_to_usd must be an object mapping currency -> USD per unit")
    fx = {
        str(key).upper(): parse_decimal(
            value, where=f"fx_rates_to_usd.{key}", minimum=Decimal(0), exclusive_minimum=True
        )
        for key, value in fx_raw.items()
    }
    common = raw.get("quote_common_terms") or {}
    return Case(
        schema_version=schema_version,
        unknown_keys=tuple(sorted(k for k in raw if k not in KNOWN_CASE_KEYS)),
        unknown_nested_keys=tuple(
            sorted(
                [
                    f"reference_product.{k}"
                    for k in product_raw
                    if k not in KNOWN_REFERENCE_PRODUCT_KEYS
                ]
                + [
                    f"quote_common_terms.{k}"
                    for k in common
                    if k not in KNOWN_QUOTE_COMMON_TERMS_KEYS
                ]
                + [f"reservation.{k}" for k in res_raw if k not in KNOWN_RESERVATION_KEYS]
                + [f"economics.{k}" for k in econ_raw if k not in KNOWN_ECONOMICS_KEYS]
                + [f"curve.{k}" for k in curve_raw if k not in {"tenor_months", "tenor_definition"}]
            )
        ),
        task=str(raw.get("task") or ""),
        synthetic=parse_bool(raw.get("synthetic", False), where="case.synthetic"),
        as_of=parse_timestamp(_require(raw, "as_of", where="case"), where="case.as_of"),
        quotes_path=str(files["quotes"]),
        performance_path=None if _blank(files.get("performance")) else str(files["performance"]),
        reference_product=product,
        tenor_months=tenors,
        economics=economics,
        reservation=reservation,
        quote_common_terms=dict(common) if isinstance(common, dict) else {},
        fx_rates_to_usd=fx,
        raw=raw,
    )
