"""Orchestration: load → classify → normalize → curve → cost → valuation → outputs."""

from __future__ import annotations

import contextlib
import csv
import hashlib
import io
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from . import __version__
from .comparability import AS_OF_VALID, classify
from .crosscheck import (
    _valid_comparable,
    commitment_context,
    flexibility_premium,
    historical_variability,
    performance_cross_check,
    spot_context,
)
from .curve import CurveModel, build_curve_model
from .loader import LoadedInputs, load_inputs
from .normalize import (
    NormalizedQuote,
    apply_quantity_adjustment,
    estimate_quantity_factor,
    normalize_quote,
)
from .schema import Case, InputError
from .supply_cost import breakeven, sensitivity_grid
from .valuation import valuation_as_dict, value_reservation

OUTPUT_SCHEMA_VERSION = 1
CURVE_COLUMNS = (
    "tenor_months",
    "delivery_month",
    "delivery_start",
    "delivery_end",
    "hours",
    "unit",
    "status",
    "usd_per_gpu_hour_mid",
    "usd_per_gpu_hour_low",
    "usd_per_gpu_hour_high",
    "bid",
    "ask",
    "uncertainty_type",
    "evidence_ids",
    "scenario_base",
    "scenario_cheap_compute",
    "scenario_scarcity",
    "months_beyond_last_anchor",
    "notes",
)
PACKAGE_DIR = Path(__file__).resolve().parent


def load_config(path: Path | None) -> dict:
    """Load the model configuration JSON (default: ``research/config/default.json``)."""
    target = Path(path) if path else PACKAGE_DIR / "config" / "default.json"
    try:
        config = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise InputError(f"{target}: invalid JSON ({exc})") from exc
    validate_config(config, str(target))
    return config


CONFIG_CURVE_KEYS = {
    "one_sided_spread_fraction": (float, 0.0, 5.0),
    "fit_min_tenor_months": (int, 0, 120),
    "default_reversion_per_month": (float, 0.0, 10.0),
    "max_reversion_per_month": (float, 0.0, 10.0),
    "horizon_vol_per_sqrt_month": (float, 0.0, 5.0),
    "model_horizon_months_before_forecast": (float, 0.0, 240.0),
}
CONFIG_SENSITIVITY_KEYS = (
    "utilizations",
    "financing_rates",
    "reversion_multipliers",
    "discount_rate_bumps",
    "default_probabilities",
    "horizon_vol_multipliers",
    "quantity_factor_multipliers",
)
ANCHOR_KINDS = {"supply_cost_breakeven", "max_valid_same_hardware_ask", "fixed"}


SUPPORTED_VALUATION = {
    "day_count": {"ACT/365"},
    "compounding": {"annual-effective"},
    "default_timing": {"uniform-default-time-from-as-of-to-delivery-end"},
    "sla_downtime_assumption": {"provider-delivers-exactly-at-sla-floor"},
}
KNOWN_CONFIG_KEYS = {
    "config_version",
    "name",
    "description",
    "curve",
    "anchors",
    "scenarios",
    "valuation",
    "sensitivities",
    "output",
}


def _check_finite(value: object, path: str) -> None:
    """Reject NaN/Infinity anywhere in the configuration (JSON parsers accept them)."""
    if isinstance(value, bool):
        return
    if isinstance(value, float) and not math.isfinite(value):
        raise InputError(f"{path}: non-finite number {value!r}")
    if isinstance(value, dict):
        for k, v in value.items():
            _check_finite(v, f"{path}.{k}")
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _check_finite(v, f"{path}[{i}]")


DOC_KEYS = {"note", "description"}
KNOWN_ANCHOR_KEYS = {"kind", "utilization", "financing", "usd_per_gpu_hour"} | DOC_KEYS
KNOWN_SCENARIO_KEYS = {"anchor", "observed_side"} | DOC_KEYS
KNOWN_OUTPUT_KEYS = {"max_quote_rows_in_valuation_json"} | DOC_KEYS


def unknown_config_keys(config: dict) -> list[str]:
    """Configuration keys, at every level, that are not part of config_version 1."""
    unknown = [k for k in config if k not in KNOWN_CONFIG_KEYS]
    curve_known = set(CONFIG_CURVE_KEYS) | {"reversion_band_per_month"} | DOC_KEYS
    unknown += [f"curve.{k}" for k in config.get("curve", {}) if k not in curve_known]
    for name, spec in config.get("anchors", {}).items():
        unknown += [f"anchors.{name}.{k}" for k in spec if k not in KNOWN_ANCHOR_KEYS]
    for name, spec in config.get("scenarios", {}).items():
        unknown += [f"scenarios.{name}.{k}" for k in spec if k not in KNOWN_SCENARIO_KEYS]
    unknown += [
        f"sensitivities.{k}"
        for k in config.get("sensitivities", {})
        if k not in set(CONFIG_SENSITIVITY_KEYS) | DOC_KEYS
    ]
    unknown += [f"output.{k}" for k in config.get("output", {}) if k not in KNOWN_OUTPUT_KEYS]
    return sorted(unknown)


def validate_config(config: object, where: str = "config") -> None:
    """Check the model configuration shape, types, finiteness and ranges; name the key on error.

    Every string-valued valuation assumption must be one the code implements
    (``SUPPORTED_VALUATION``); unsupported values are rejected rather than echoed.
    """
    if not isinstance(config, dict):
        raise InputError(f"{where}: configuration must be a JSON object")
    if config.get("config_version") != 1:
        raise InputError(f"{where}: unsupported config_version {config.get('config_version')!r}")
    _check_finite(config, where)
    for section in ("curve", "anchors", "scenarios", "valuation", "sensitivities"):
        if not isinstance(config.get(section), dict):
            raise InputError(f"{where}: missing or non-object section {section!r}")
    for key, allowed in SUPPORTED_VALUATION.items():
        value = config["valuation"].get(key)
        if value not in allowed:
            raise InputError(
                f"{where}: valuation.{key}={value!r} is not implemented; "
                f"supported: {sorted(allowed)}"
            )
    for key in config["valuation"]:
        if key not in SUPPORTED_VALUATION:
            raise InputError(f"{where}: valuation.{key} is not a recognised assumption")
    output = config.get("output", {})
    if not isinstance(output, dict):
        raise InputError(f"{where}: output must be an object")
    limit = output.get("max_quote_rows_in_valuation_json", 200)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise InputError(
            f"{where}: output.max_quote_rows_in_valuation_json must be an integer >= 1"
        )
    curve = config["curve"]
    for key, (kind, low, high) in CONFIG_CURVE_KEYS.items():
        if key not in curve:
            raise InputError(f"{where}: curve.{key} is required")
        value = curve[key]
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise InputError(f"{where}: curve.{key} must be a number")
        if kind is int and int(value) != value:
            raise InputError(f"{where}: curve.{key} must be an integer")
        if not low <= value <= high:
            raise InputError(f"{where}: curve.{key}={value} outside [{low}, {high}]")
    band = curve.get("reversion_band_per_month")
    if (
        not isinstance(band, list)
        or len(band) != 2
        or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in band)
        or not 0 <= band[0] <= band[1] <= 10
    ):
        raise InputError(
            f"{where}: curve.reversion_band_per_month must be [low, high] with 0<=low<=high"
        )
    for name, spec in config["anchors"].items():
        if not isinstance(spec, dict) or spec.get("kind") not in ANCHOR_KINDS:
            raise InputError(f"{where}: anchors.{name}.kind must be one of {sorted(ANCHOR_KINDS)}")
        if spec["kind"] == "supply_cost_breakeven":
            for field in ("utilization", "financing"):
                value = spec.get(field, "case")
                if value != "case" and not (isinstance(value, int | float) and value >= 0):
                    raise InputError(
                        f"{where}: anchors.{name}.{field} must be 'case' or a number >= 0"
                    )
            util = spec.get("utilization", "case")
            if util != "case" and not 0 < util <= 1:
                raise InputError(f"{where}: anchors.{name}.utilization must be in (0, 1]")
        if spec["kind"] == "fixed" and not isinstance(spec.get("usd_per_gpu_hour"), int | float):
            raise InputError(f"{where}: anchors.{name}.usd_per_gpu_hour must be a number")
    scenarios = config["scenarios"]
    if "base" not in scenarios:
        raise InputError(f"{where}: scenarios.base is required")
    for name, spec in scenarios.items():
        if not isinstance(spec, dict) or spec.get("anchor") not in config["anchors"]:
            raise InputError(f"{where}: scenarios.{name}.anchor must name an entry in anchors")
        if spec.get("observed_side") not in {"mid", "low", "high"}:
            raise InputError(f"{where}: scenarios.{name}.observed_side must be mid, low or high")
    sens = config["sensitivities"]
    for key in CONFIG_SENSITIVITY_KEYS:
        values = sens.get(key)
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(v, int | float) and not isinstance(v, bool) for v in values)
        ):
            raise InputError(f"{where}: sensitivities.{key} must be a non-empty list of numbers")
    for key in (
        "utilizations",
        "reversion_multipliers",
        "horizon_vol_multipliers",
        "quantity_factor_multipliers",
    ):
        if any(v <= 0 for v in sens[key]):
            raise InputError(f"{where}: sensitivities.{key} must be strictly positive")
    if any(v > 1 for v in sens["utilizations"]):
        raise InputError(f"{where}: sensitivities.utilizations must be <= 1")
    if any(v < 0 for v in sens["financing_rates"]):
        raise InputError(f"{where}: sensitivities.financing_rates must be >= 0")
    if any(not 0 <= v <= 1 for v in sens["default_probabilities"]):
        raise InputError(f"{where}: sensitivities.default_probabilities must be within [0, 1]")


def code_digest() -> str:
    """SHA-256 over the research package's source files (sorted by relative path)."""
    digest = hashlib.sha256()
    for path in sorted(PACKAGE_DIR.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".json"} and "__pycache__" not in path.parts:
            digest.update(path.relative_to(PACKAGE_DIR).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def git_tree(repo: Path, path: str) -> str | None:
    """Git tree hash of ``path`` at HEAD (stable across commits that do not touch it)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", f"HEAD:{path}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def git_commit(repo: Path) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


def research_working_tree_dirty(repo: Path) -> bool | None:
    """Whether the actual model differs from its recorded HEAD tree; unknown without Git."""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "status", "--porcelain", "--", "tasks/compute-quant/research"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return bool(out.stdout.strip()) if out.returncode == 0 else None


def _fmt(value: float | None) -> str:
    return "" if value is None else f"{value:.6f}"


def run_pipeline(
    case_path: Path,
    config: dict,
    *,
    exclude_quote_ids: tuple[str, ...] = (),
    as_of_override: str | None = None,
) -> dict[str, object]:
    """Run everything and return the JSON-ready results mapping."""
    loaded: LoadedInputs = load_inputs(case_path)
    case: Case = loaded.case
    if as_of_override:
        from dataclasses import replace

        from .months import parse_utc

        case = replace(case, as_of=parse_utc(as_of_override))
    quotes = [q for q in loaded.quotes if q.id not in set(exclude_quote_ids)]
    missing = sorted(set(exclude_quote_ids) - {q.id for q in loaded.quotes})
    if missing:
        raise InputError(f"--exclude-quote ids not present in the quotes file: {missing}")
    normalized: list[NormalizedQuote] = [
        normalize_quote(q, classify(q, case), case) for q in quotes
    ]
    for item in normalized:
        if item.usable and item.usd_per_gpu_hour is not None and item.usd_per_gpu_hour <= 0:
            item.excluded_reason = (
                f"normalized price {item.usd_per_gpu_hour} USD/GPU-hour is not positive; "
                "excluded from every view"
            )
    factor, factor_note = estimate_quantity_factor(normalized, case)
    apply_quantity_adjustment(normalized, factor, case)
    model: CurveModel = build_curve_model(normalized, case, config)
    model.items = normalized
    model.quantity_factor = factor
    nodes = [model.node(t) for t in case.tenor_months]
    base_cost = breakeven(case.economics)
    valuation = value_reservation(model, case)
    sensitivities = _sensitivities(model, case, config["sensitivities"], valuation.identified)
    comparable = _valid_comparable(normalized)
    quote_table = []
    for item in normalized:
        q, c = item.quote, item.classification
        quote_table.append(
            {
                "id": q.id,
                "provider": q.provider,
                "kind": q.kind,
                "side": q.side,
                "observed_at": q.observed_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "valid_until": None
                if q.valid_until is None
                else q.valid_until.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "raw": {
                    "price": float(q.price),
                    "currency": q.currency,
                    "unit": q.unit,
                    "gpu_count": q.gpu_count,
                },
                "as_of_status": c.as_of_status,
                "tier": c.tier,
                "contract_class": c.contract_class,
                "delivery_months": [m.strftime("%Y-%m") for m in c.delivery_months],
                "tenor_months": list(c.tenor_months),
                "curve_eligible": item.usable and c.curve_eligible,
                "usd_per_gpu_hour": None
                if item.usd_per_gpu_hour is None
                else float(item.usd_per_gpu_hour),
                "single_gpu_equivalent_usd_per_gpu_hour": (
                    None
                    if item.single_gpu_equivalent is None
                    else float(item.single_gpu_equivalent)
                ),
                "excluded_reason": item.excluded_reason,
                "steps": [
                    {
                        "step": s.name,
                        "before": None if s.before is None else float(s.before),
                        "after": None if s.after is None else float(s.after),
                        "note": s.note,
                    }
                    for s in item.steps
                ],
                "reasons": list(c.reasons),
                "unknown_columns": dict(q.unknown_columns),
            }
        )
    exclusions = [
        {
            "id": row["id"],
            "reason": quote_exclusion_reason(row),
        }
        for row in quote_table
        if not row["curve_eligible"]
    ]
    limit = int(config.get("output", {}).get("max_quote_rows_in_valuation_json", 200))
    reason_counts: dict[str, int] = {}
    for row in exclusions:
        reason_counts[row["reason"]] = reason_counts.get(row["reason"], 0) + 1
    quotes_out = {
        "count": len(quote_table),
        "rows": quote_table[:limit],
        "row_limit": limit,
        "truncated": max(0, len(quote_table) - limit),
        "full_table": "normalization.csv (one row per quote, written next to valuation.json)",
    }
    exclusions_out = {
        "count": len(exclusions),
        "by_reason": dict(sorted(reason_counts.items())),
        "rows": exclusions[:limit],
        "row_limit": limit,
        "truncated": max(0, len(exclusions) - limit),
    }
    results: dict[str, object] = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "task": "compute-quant",
        "synthetic_inputs": case.synthetic,
        "disclaimer": (
            "All inputs are the repository's synthetic demo data; nothing here is a real market "
            "view, provider offer, or executable price."
        ),
        "as_of": case.as_of.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reference_product": {
            "gpu": case.reference_product.gpu,
            "memory_gib": case.reference_product.memory_gib,
            "gpu_count": case.reference_product.gpu_count,
            "region": case.reference_product.region,
            "isolation": case.reference_product.isolation,
            "preemptible": case.reference_product.preemptible,
            "sla_pct": float(case.reference_product.sla_pct),
            "payment": case.reference_product.payment,
            "cancellation": case.reference_product.cancellation,
        },
        "inputs": {
            "files": [dict(f.__dict__, path=_display_path(f.path)) for f in loaded.files],
            "input_pack_digest": loaded.input_pack_digest,
            "input_pack_digest_definition": "sha256 over sorted "
            "'<role>:<sha256>\\n' lines (candidate-defined)",
            "quote_columns": list(loaded.quote_columns),
            "excluded_quote_ids_by_flag": list(exclude_quote_ids),
            "unknown_case_keys": list(case.unknown_keys),
            "unknown_nested_case_keys": list(case.unknown_nested_keys),
            "unknown_config_keys": unknown_config_keys(config),
        },
        "quotes": quotes_out,
        "exclusions": exclusions_out,
        "_quote_table": quote_table,
        "quantity_adjustment": {
            "factor": None if factor is None else float(factor),
            "note": factor_note,
        },
        "curve": {
            "unit": "USD per GPU-hour of reserved capacity, paid monthly in arrears",
            "tenor_definition": "delivery month starts this "
            "many calendar months after the as-of month start",
            "nodes": [n.as_dict() for n in nodes],
            "anchors": [
                {
                    "tenor_months": a.tenor,
                    "status": a.status,
                    "mid": round(a.mid, 6),
                    "low": round(a.low, 6),
                    "high": round(a.high, 6),
                    "bids": [o.quote_id for o in a.bids],
                    "asks": [o.quote_id for o in a.asks],
                    "uncertainty_type": a.uncertainty_type,
                    "notes": list(a.notes),
                }
                for a in model.anchors
            ],
            "reference_two_sided_spread": None
            if model.reference_spread is None
            else round(model.reference_spread, 6),
            "reversion": {
                "kappa_per_month": round(model.kappa, 6),
                "source": model.kappa_source,
                **model.kappa_detail,
            },
            "scenario_anchor_levels": {
                k: (None if v is None else round(v, 6))
                for k, v in model.scenario_anchor_levels.items()
            },
            "scenario_anchor_notes": dict(model.scenario_anchor_notes),
            "warnings": list(model.warnings),
            "monthly_base_mid": {
                str(t): (None if model.price(t)[0] is None else round(model.price(t)[0], 6))
                for t in range(0, max(case.tenor_months) + 1)
            },
        },
        "supply_cost": {
            "base": base_cost.as_dict(),
            "sensitivity_grid": sensitivities["supply_cost_breakeven_grid"],
        },
        "cross_checks": {
            "current_flexible_reference_ask_usd_per_gpu_hour": min(
                (
                    float(item.usd_per_gpu_hour)
                    for item in normalized
                    if item.usable
                    and item.classification.as_of_status == AS_OF_VALID
                    and item.classification.tier == "comparable"
                    and item.classification.contract_class == "flexible_on_demand"
                    and item.quote.kind == "current_on_demand"
                    and item.quote.side == "ask"
                    and item.quote.gpu_count == case.reference_product.gpu_count
                    and item.quote.cancellation.strip().lower() == "anytime"
                ),
                default=None,
            ),
            "performance_translation": performance_cross_check(
                normalized, loaded.performance, case, comparable
            ),
            "usage_commitments": commitment_context(normalized, case, comparable),
            "spot": spot_context(normalized, case, comparable),
            "flexibility_premium": flexibility_premium(normalized, case, comparable),
            "historical_variability": historical_variability(normalized, case, comparable),
        },
        "reservation": {
            "id": case.reservation.id,
            "gpu_count": case.reservation.gpu_count,
            "delivery_start": case.reservation.delivery_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "delivery_end": case.reservation.delivery_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "fixed_usd_per_gpu_hour": float(case.reservation.fixed_usd_per_gpu_hour),
            "payment": case.reservation.payment,
            "cancellation": case.reservation.cancellation,
            "sla_pct": float(case.reservation.sla_pct),
            "expected_buyer_used_fraction": (
                None
                if case.reservation.expected_buyer_used_fraction is None
                else float(case.reservation.expected_buyer_used_fraction)
            ),
        },
        "valuation": valuation_as_dict(valuation),
        "sensitivities": {
            k: v for k, v in sensitivities.items() if k != "supply_cost_breakeven_grid"
        },
        "config": config,
        "valid_quote_count": sum(1 for row in quote_table if row["as_of_status"] == AS_OF_VALID),
    }
    return _round_floats(results)


def _sensitivities(
    model: CurveModel, case: Case, sens: dict, identified: bool
) -> dict[str, object]:
    """Sensitivity tables; valuation-based ones are empty when the curve is unidentified."""
    out: dict[str, object] = {
        "supply_cost_breakeven_grid": sensitivity_grid(
            case.economics, sens["utilizations"], sens["financing_rates"]
        ),
        "reversion_multiplier": [
            {
                "multiplier": m,
                "kappa": model.kappa * m,
                "curve_mid_by_tenor": {
                    str(t): model.price(t, "base", model.kappa * m)[0] for t in case.tenor_months
                },
            }
            for m in sens["reversion_multipliers"]
        ],
        "quantity_factor_multiplier": _quantity_factor_sensitivity(model, case, sens),
        "discount_rate_bump": [],
        "default_probability": [],
        "horizon_vol_multiplier": [],
    }
    if not identified:
        return out
    base_rate = float(case.economics.buyer_discount_annual_rate)
    for row in out["reversion_multiplier"]:
        row["mark_adjusted_usd"] = value_reservation(model, case, kappa=row["kappa"]).mark_adjusted
    out["discount_rate_bump"] = [
        {
            "bump": b,
            "rate": base_rate + b,
            "mark_adjusted_usd": value_reservation(
                model, case, discount_rate=base_rate + b
            ).mark_adjusted,
        }
        for b in sens["discount_rate_bumps"]
    ]
    out["default_probability"] = []
    for p in sens["default_probabilities"]:
        stressed = value_reservation(model, case, p_default=p)
        out["default_probability"].append(
            {
                "p_default": p,
                "mark_adjusted_usd": stressed.mark_adjusted,
                "fair_fixed_price_identified": stressed.fair_fixed_price is not None,
                "fair_fixed_price_usd_per_gpu_hour": stressed.fair_fixed_price,
            }
        )
    out["horizon_vol_multiplier"] = [
        {
            "multiplier": m,
            "mark_interval_usd": {
                "low": value_reservation(model, case, vol_multiplier=m).mark_low,
                "high": value_reservation(model, case, vol_multiplier=m).mark_high,
            },
        }
        for m in sens["horizon_vol_multipliers"]
    ]
    return out


def _quantity_factor_sensitivity(model: CurveModel, case: Case, sens: dict) -> list[dict]:
    """Re-run the curve with the quantity factor scaled; reports node mids (and mark if marked)."""
    rows: list[dict] = []
    for m in sens["quantity_factor_multipliers"]:
        items = [
            replace(
                item,
                quantity_adjusted_usd_per_gpu_hour=None,
                steps=[step for step in item.steps if step.name != "quantity"],
            )
            for item in model.items
        ]
        factor = model.quantity_factor
        scaled = None if factor is None else factor * Decimal(str(m))
        apply_quantity_adjustment(items, scaled, case)
        alt = build_curve_model(items, case, model.config)
        row: dict = {
            "multiplier": m,
            "factor": None if scaled is None else float(scaled),
            "curve_mid_by_tenor": {str(t): alt.price(t)[0] for t in case.tenor_months},
            "anchor_mid_by_tenor": {str(a.tenor): a.mid for a in alt.anchors},
        }
        val = value_reservation(alt, case)
        row["mark_adjusted_usd"] = val.mark_adjusted if val.identified else None
        rows.append(row)
    return rows


def _display_path(path: str) -> str:
    """Repository-relative POSIX path when the file is inside the repo, else as given."""
    repo_root = PACKAGE_DIR.parents[2]
    try:
        return Path(path).resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def _round_floats(obj):
    if isinstance(obj, float):
        return round(obj, 8)
    if isinstance(obj, dict):
        return {k: _round_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_floats(v) for v in obj]
    return obj


def render_curve_csv(results: dict[str, object]) -> str:
    """Serialize the curve nodes as CSV text (LF line endings)."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CURVE_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for node in results["curve"]["nodes"]:
        writer.writerow(
            {
                "tenor_months": node["tenor_months"],
                "delivery_month": node["delivery_month"],
                "delivery_start": node["delivery_start"],
                "delivery_end": node["delivery_end"],
                "hours": node["hours"],
                "unit": "USD/GPU-hour reserved capacity, monthly arrears",
                "status": node["status"],
                "usd_per_gpu_hour_mid": _fmt(node["usd_per_gpu_hour_mid"]),
                "usd_per_gpu_hour_low": _fmt(node["usd_per_gpu_hour_low"]),
                "usd_per_gpu_hour_high": _fmt(node["usd_per_gpu_hour_high"]),
                "bid": _fmt(node["bid"]),
                "ask": _fmt(node["ask"]),
                "uncertainty_type": node["uncertainty_type"],
                "evidence_ids": ";".join(node["evidence_ids"]),
                "scenario_base": _fmt(node["scenarios"].get("base")),
                "scenario_cheap_compute": _fmt(node["scenarios"].get("cheap_compute")),
                "scenario_scarcity": _fmt(node["scenarios"].get("scarcity")),
                "months_beyond_last_anchor": ""
                if node["months_beyond_last_anchor"] is None
                else node["months_beyond_last_anchor"],
                "notes": " | ".join(node["notes"]),
            }
        )
    return buffer.getvalue()


def _public(obj: object) -> object:
    """Drop private (underscore-prefixed) keys before serialisation."""
    if isinstance(obj, dict):
        return {k: _public(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_public(v) for v in obj]
    return obj


def render_json(obj: dict[str, object]) -> str:
    return (
        json.dumps(_public(obj), indent=2, sort_keys=False, ensure_ascii=False, allow_nan=False)
        + "\n"
    )


def build_manifest(
    results: dict[str, object], case_path: Path, config_path: Path | None, argv: list[str]
) -> dict[str, object]:
    """Machine-readable provenance for a run."""
    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    when = datetime.fromtimestamp(int(epoch), tz=UTC) if epoch else datetime.now(tz=UTC)
    repo_root = PACKAGE_DIR.parents[2]
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "task": "compute-quant",
        "generated_at": when.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_at_source": "SOURCE_DATE_EPOCH" if epoch else "wall-clock",
        "code": {
            "package": "tasks/compute-quant/research",
            "version": __version__,
            "source_sha256": code_digest(),
            "git_commit": git_commit(repo_root),
            "git_commit_note": "HEAD when generated; git_tree_research is the committed HEAD "
            "tree only. If research_working_tree_dirty is true, source_sha256 identifies the "
            "actual uncommitted code and the HEAD tree is not its source identity.",
            "git_tree_research": git_tree(repo_root, "tasks/compute-quant/research"),
            "research_working_tree_dirty": research_working_tree_dirty(repo_root),
        },
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
        },
        "command": argv,
        "case": _display_path(str(case_path)),
        "config": (Path(config_path).as_posix() if config_path else "research/config/default.json"),
        "config_name": results["config"].get("name"),
        "inputs": results["inputs"],
        "as_of": results["as_of"],
        "parameters": results["config"],
        "network_access": "none",
        "outputs": list(OUTPUT_FILES),
    }


def quote_exclusion_reason(row: dict) -> str:
    """Use the same complete rejection explanation in capped JSON and uncapped CSV."""
    if row["curve_eligible"]:
        return ""
    return row["excluded_reason"] or "; ".join(
        reason for reason in row["reasons"] if not reason.startswith("observed on/before")
    )


def render_normalization_csv(results: dict[str, object]) -> str:
    """One row per quote with the normalization outcome (the full, uncapped table)."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(
        [
            "id",
            "provider",
            "kind",
            "side",
            "observed_at",
            "valid_until",
            "raw_price",
            "currency",
            "unit",
            "gpu_count",
            "as_of_status",
            "tier",
            "contract_class",
            "delivery_months",
            "tenor_months",
            "curve_eligible",
            "usd_per_gpu_hour",
            "single_gpu_equivalent_usd_per_gpu_hour",
            "excluded_reason",
            "steps",
        ]
    )
    for row in results["_quote_table"]:
        writer.writerow(
            [
                row["id"],
                row["provider"],
                row["kind"],
                row["side"],
                row["observed_at"],
                row["valid_until"] or "",
                row["raw"]["price"],
                row["raw"]["currency"],
                row["raw"]["unit"],
                row["raw"]["gpu_count"],
                row["as_of_status"],
                row["tier"],
                row["contract_class"],
                ";".join(row["delivery_months"]),
                ";".join(str(t) for t in row["tenor_months"]),
                row["curve_eligible"],
                "" if row["usd_per_gpu_hour"] is None else row["usd_per_gpu_hour"],
                ""
                if row["single_gpu_equivalent_usd_per_gpu_hour"] is None
                else row["single_gpu_equivalent_usd_per_gpu_hour"],
                quote_exclusion_reason(row),
                " | ".join(f"{s['step']}: {s['before']} -> {s['after']}" for s in row["steps"]),
            ]
        )
    return buf.getvalue()


OUTPUT_FILES = ("curve.csv", "valuation.json", "run-manifest.json", "normalization.csv")


def write_outputs(
    results: dict[str, object], out_dir: Path, manifest: dict[str, object]
) -> dict[str, Path]:
    """Render every artifact first, then write them atomically (temp files + rename).

    A failure while rendering (e.g. a non-finite number) therefore leaves no partial
    artifact on disk, and a reader never observes a half-written file.
    """
    rendered = {
        "curve.csv": render_curve_csv(results),
        "valuation.json": render_json(results),
        "run-manifest.json": render_json(manifest),
        "normalization.csv": render_normalization_csv(results),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {name: out_dir / name for name in OUTPUT_FILES}
    for name, text in rendered.items():
        fd, tmp_name = tempfile.mkstemp(prefix=f".{name}.", dir=out_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(text)
            os.replace(tmp_name, paths[name])
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
    return paths


def warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)
