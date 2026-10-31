"""Load, validate, and resolve ``workload.json`` into concrete scheduled cells.

The plan is data, not code: the organizer's fixed runner would consume the same file. The
validator is deliberately strict (unknown mode, missing cell, non-positive rate, decode
deadline <= 0, etc. all raise ``PlanError``) because a silently-tolerated plan defect would
otherwise surface as a wrong benchmark result.
"""

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SUPPORTED_PLAN_SCHEMA = ("1.0.0",)
ASSISTANT_PREFILL_PROFILE = "llmd_closed_thinking_v1"


class PlanError(ValueError):
    """Raised when the workload plan is malformed or internally inconsistent."""


@dataclass(frozen=True)
class ScheduledCell:
    """One executable unit: a cell at one load point for one repetition."""

    cell_id: str
    repetition: int
    prompt_source: str
    arrival_type: str
    load: float
    requests: int
    warmup: int
    deadline_profile: str
    role: str
    arrival_seed: int
    prompt_token_grid: tuple[int, ...] | None = None

    @property
    def load_label(self) -> str:
        """Stable label for the load point (rate in rps or concurrency)."""
        if self.arrival_type == "poisson":
            return f"rate={self.load:g}rps"
        return f"conc={int(self.load)}"

    @property
    def unit_id(self) -> str:
        """Identifier that is unique within a run."""
        return f"{self.cell_id}|{self.load_label}|rep{self.repetition}"


def load_plan(path: Path) -> dict[str, Any]:
    """Read and validate a plan file."""
    try:
        plan = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot read plan {path}: {exc}") from exc
    validate_plan(plan)
    return plan


def plan_digest(path: Path) -> str:
    """SHA-256 of the plan file bytes, recorded in every run for provenance."""
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PlanError(message)


def _positive(value: Any, name: str) -> None:
    _require(
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0,
        f"{name} must be a finite positive number",
    )


def fixed_request_options(output_policy: dict[str, Any]) -> dict[str, Any]:
    """Validate and copy fixed optional thinking controls; never merge arbitrary fields.

    Servers expose either a top-level switch or a chat-template keyword. Supporting
    a representation does not establish that the selected server honors it: the
    frozen plan and real compatibility evidence must declare that separately.
    """
    _require(isinstance(output_policy, dict), "output_policy must be an object")
    _require(
        set(output_policy)
        <= {
            "temperature",
            "stream",
            "stream_options",
            "stop",
            "request_options",
            "request_profile",
        },
        "output_policy contains an unknown field or request override",
    )
    options = output_policy.get("request_options", {})
    _require(isinstance(options, dict), "output_policy.request_options must be an object")
    if "request_profile" in output_policy:
        _require(
            output_policy["request_profile"] == ASSISTANT_PREFILL_PROFILE,
            "unknown output_policy.request_profile",
        )
        _require(not options, "the fixed assistant-prefill profile cannot combine request_options")
        return {"continue_final_message": True, "add_generation_prompt": False}
    _require(
        set(options) <= {"enable_thinking", "chat_template_kwargs"},
        "request_options permits only fixed thinking controls, not request overrides",
    )
    result: dict[str, Any] = {}
    if "enable_thinking" in options:
        value = options["enable_thinking"]
        _require(type(value) is bool, "request_options.enable_thinking must be a boolean")
        result["enable_thinking"] = value
    if "chat_template_kwargs" in options:
        kwargs = options["chat_template_kwargs"]
        _require(
            isinstance(kwargs, dict) and set(kwargs) == {"enable_thinking"},
            "request_options.chat_template_kwargs must contain only enable_thinking",
        )
        value = kwargs["enable_thinking"]
        _require(type(value) is bool, "chat_template_kwargs.enable_thinking must be a boolean")
        if "enable_thinking" in result:
            _require(
                result["enable_thinking"] == value,
                "top-level and chat-template thinking controls must agree",
            )
        result["chat_template_kwargs"] = {"enable_thinking": value}
    return result


def validate_plan(plan: dict[str, Any]) -> None:
    """Raise ``PlanError`` unless the plan is structurally and semantically valid."""
    _require(isinstance(plan, dict), "plan must be a JSON object")
    _require(
        plan.get("schema_version") in SUPPORTED_PLAN_SCHEMA,
        f"unsupported plan schema_version {plan.get('schema_version')!r}",
    )
    for key in (
        "plan_id",
        "seeds",
        "output_policy",
        "deadlines",
        "prompt_sources",
        "cells",
        "modes",
        "planning_assumptions",
        "statistics",
        "startup",
        "warmup",
        "token_accounting",
        "client_integrity",
    ):
        _require(key in plan, f"plan is missing required key {key!r}")
    for seed in ("prompt_seed", "arrival_seed", "bootstrap_seed"):
        _require(isinstance(plan["seeds"].get(seed), int), f"seeds.{seed} must be an integer")
    fixed_request_options(plan["output_policy"])
    _require(plan["output_policy"].get("temperature") == 0, "output_policy.temperature must be 0")
    _require(plan["output_policy"].get("stream") is True, "output_policy.stream must be true")

    deadlines = plan["deadlines"]
    prefill = deadlines["prefill"]
    for key in ("intercept_s", "slope_s_per_prompt_token", "floor_s", "scheduling_slack_s"):
        _require(
            isinstance(prefill.get(key), int | float) and prefill[key] >= 0,
            f"deadlines.prefill.{key} must be a nonnegative number",
        )
    tiers = deadlines["decode_tiers"]
    _require(isinstance(tiers, dict) and tiers, "deadlines.decode_tiers must be a nonempty object")
    for name, tier in tiers.items():
        _positive(tier.get("deadline_s"), f"decode tier {name} deadline_s")
    slo = deadlines["fluidity_slo"]
    _require(0 <= slo["max_miss_rate"] < 1, "fluidity_slo.max_miss_rate must be in [0, 1)")
    _require(0 < slo["request_quantile"] <= 1, "fluidity_slo.request_quantile must be in (0, 1]")
    _require(
        isinstance(deadlines.get("ttft_slo_s"), dict), "deadlines.ttft_slo_s must be an object"
    )

    sources = plan["prompt_sources"]
    for name, cfg in sources.items():
        kind = cfg.get("kind")
        if kind == "mixture":
            comps = cfg.get("components")
            _require(isinstance(comps, list) and comps, f"mixture {name} needs components")
            for comp in comps:
                _require(
                    comp[0] in sources and sources[comp[0]]["kind"] != "mixture",
                    f"mixture {name} references unknown or nested source {comp[0]!r}",
                )
                _positive(comp[1], f"mixture {name} weight")
        else:
            _require(
                kind in ("synthetic_chat", "synthetic_rag", "integer_sequence"),
                f"prompt source {name} has unknown kind {kind!r}",
            )
            _positive(cfg.get("max_tokens"), f"prompt source {name} max_tokens")
            span = cfg.get("prompt_tokens", {})
            _require(
                0 < span.get("min", 0) <= span.get("max", 0),
                f"prompt source {name} prompt_tokens range invalid",
            )
            _require(isinstance(cfg.get("expected"), dict), f"prompt source {name} needs expected")
            _require(
                cfg["expected"].get("finish_reasons"),
                f"prompt source {name} expected.finish_reasons required",
            )

    cell_ids = set()
    for cell in plan["cells"]:
        cid = cell.get("id")
        _require(
            isinstance(cid, str) and cid and cid not in cell_ids, f"bad/duplicate cell id {cid!r}"
        )
        cell_ids.add(cid)
        _require(
            cell.get("prompt_source") in sources,
            f"cell {cid} references unknown prompt source {cell.get('prompt_source')!r}",
        )
        _require(
            cell.get("deadline_profile") in deadlines["ttft_slo_s"],
            f"cell {cid} deadline_profile has no ttft_slo_s entry",
        )
        arrival = cell.get("arrival", {})
        if arrival.get("type") == "poisson":
            rates, counts = arrival.get("rates_rps"), arrival.get("requests_per_rate")
            _require(
                isinstance(rates, list) and rates and len(rates) == len(counts or []),
                f"cell {cid} rates_rps/requests_per_rate must be equal-length lists",
            )
            for rate, count in zip(rates, counts, strict=True):
                _positive(rate, f"cell {cid} rate")
                _require(isinstance(count, int) and count > 0, f"cell {cid} request count")
        elif arrival.get("type") == "closed_loop":
            levels, counts = arrival.get("concurrency"), arrival.get("requests_per_level")
            _require(
                isinstance(levels, list) and levels and len(levels) == len(counts or []),
                f"cell {cid} concurrency/requests_per_level must be equal-length lists",
            )
            for level, count in zip(levels, counts, strict=True):
                _require(isinstance(level, int) and level > 0, f"cell {cid} concurrency level")
                _require(isinstance(count, int) and count > 0, f"cell {cid} request count")
        else:
            raise PlanError(f"cell {cid} arrival.type must be poisson or closed_loop")
        if cell.get("prompt_token_grid") is not None:
            grid = cell["prompt_token_grid"]
            _require(all(isinstance(g, int) and g > 0 for g in grid), f"cell {cid} bad token grid")

    for mode_name, mode in plan["modes"].items():
        _require(
            isinstance(mode.get("repetitions"), int) and mode["repetitions"] > 0,
            f"mode {mode_name} repetitions must be a positive integer",
        )
        _require(
            isinstance(mode.get("startup_launches"), int) and mode["startup_launches"] >= 0,
            f"mode {mode_name} startup_launches must be a nonnegative integer",
        )
        for cid in mode.get("cells", []):
            _require(cid in cell_ids, f"mode {mode_name} references unknown cell {cid!r}")
        _positive(
            mode.get("planned_wall_minutes_max"), f"mode {mode_name} planned_wall_minutes_max"
        )
        scale = mode.get("requests_scale", 1.0)
        _positive(scale, f"mode {mode_name} requests_scale")
    warm = plan["warmup"]
    _require(
        isinstance(warm.get("requests_per_cell"), int) and warm["requests_per_cell"] >= 0,
        "warmup.requests_per_cell must be a nonnegative integer",
    )
    stats = plan["statistics"]
    _require(
        isinstance(stats.get("bootstrap_resamples"), int) and stats["bootstrap_resamples"] > 0,
        "statistics.bootstrap_resamples must be positive",
    )
    _require(0 < stats.get("confidence", 0) < 1, "statistics.confidence must be in (0, 1)")
    _require(
        isinstance(stats.get("min_requests_per_cell"), int)
        and not isinstance(stats.get("min_requests_per_cell"), bool)
        and stats["min_requests_per_cell"] > 0,
        "statistics.min_requests_per_cell must be a positive integer",
    )
    _positive(plan["client_integrity"].get("max_event_loop_lag_s"), "max_event_loop_lag_s")
    _positive(plan["startup"].get("max_wait_s"), "startup.max_wait_s")
    band = plan["token_accounting"].get("usage_ratio_band")
    _require(
        isinstance(band, list) and len(band) == 2 and 0 < band[0] < 1 < band[1],
        "token_accounting.usage_ratio_band must bracket 1.0",
    )


def cell_by_id(plan: dict[str, Any], cell_id: str) -> dict[str, Any]:
    """Return the cell definition or raise ``PlanError``."""
    for cell in plan["cells"]:
        if cell["id"] == cell_id:
            return cell
    raise PlanError(f"unknown cell {cell_id!r}")


def resolve_mode(plan: dict[str, Any], mode_name: str) -> list[ScheduledCell]:
    """Expand a mode into the ordered list of scheduled cells (interleaved if requested)."""
    _require(mode_name in plan["modes"], f"unknown mode {mode_name!r}")
    mode = plan["modes"][mode_name]
    scale = float(mode.get("requests_scale", 1.0))
    # Scaling never takes a load point below the per-cell minimum the evaluator needs, so
    # every mode can in principle reach a *valid* verdict (round-2 review F7).
    floor = int(plan["statistics"]["min_requests_per_cell"])
    warmup = int(plan["warmup"]["requests_per_cell"])
    base_seed = int(plan["seeds"]["arrival_seed"])
    per_rep: list[list[ScheduledCell]] = [[] for _ in range(mode["repetitions"])]
    for cid in mode["cells"]:
        cell = cell_by_id(plan, cid)
        arrival = cell["arrival"]
        if arrival["type"] == "poisson":
            loads = list(zip(arrival["rates_rps"], arrival["requests_per_rate"], strict=True))
            subset = mode.get("rate_subset", {}).get(cid)
        else:
            loads = list(zip(arrival["concurrency"], arrival["requests_per_level"], strict=True))
            subset = mode.get("concurrency_subset", {}).get(cid)
        if subset is not None:
            loads = [(load, n) for load, n in loads if load in subset]
            _require(loads, f"mode {mode_name} subset for {cid} selects no load point")
        offset = int(arrival.get("arrival_seed_offset", 0))
        for repetition in range(mode["repetitions"]):
            for load_index, (load, count) in enumerate(loads):
                requests = max(floor, int(round(count * scale)))
                seed_material = f"{base_seed}:{cid}:{load}:{repetition}:{offset}"
                seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:12], 16)
                grid = cell.get("prompt_token_grid")
                per_rep[repetition].append(
                    ScheduledCell(
                        cell_id=cid,
                        repetition=repetition,
                        prompt_source=cell["prompt_source"],
                        arrival_type=arrival["type"],
                        load=float(load),
                        requests=requests,
                        warmup=warmup if load_index == 0 else 0,
                        deadline_profile=cell["deadline_profile"],
                        role=cell.get("role", "measure"),
                        arrival_seed=seed,
                        prompt_token_grid=tuple(grid) if grid else None,
                    )
                )
    if mode.get("interleave", False):
        return [unit for rep in per_rep for unit in rep]
    ordered: list[ScheduledCell] = []
    for cid in mode["cells"]:
        for rep in per_rep:
            ordered.extend(u for u in rep if u.cell_id == cid)
    return ordered


def prefill_deadline_s(plan: dict[str, Any], prompt_tokens: int) -> float:
    """Etalon-style prefill deadline: max(floor, intercept + slope * prompt_tokens) + slack."""
    cfg = plan["deadlines"]["prefill"]
    base = cfg["intercept_s"] + cfg["slope_s_per_prompt_token"] * max(prompt_tokens, 0)
    return max(cfg["floor_s"], base) + cfg["scheduling_slack_s"]


def estimate_mode(plan: dict[str, Any], mode_name: str) -> dict[str, Any]:
    """Planning arithmetic (NOT a measurement) for one mode's wall time and GPU-hours.

    Open-loop units take ``requests / rate`` of arrival span plus a drain equal to the
    slowest expected request. Closed-loop units take ``requests * service / concurrency``
    with per-stream decode throttled by the aggregate decode ceiling. Startup launches,
    warmup, per-cell setup, cache flushes, and teardown are added, then a safety margin.
    """
    assumptions = plan["planning_assumptions"]
    mode = plan["modes"][mode_name]
    units = resolve_mode(plan, mode_name)
    sources = plan["prompt_sources"]

    def expected_request_s(source: str, concurrency: float) -> tuple[float, float]:
        cfg = sources[source]
        if cfg["kind"] == "mixture":
            parts = [
                (expected_request_s(name, concurrency), weight)
                for name, weight in cfg["components"]
            ]
            total = sum(w for _, w in parts)
            typical = sum(t * w for (t, _), w in parts) / total
            worst = max(worst for (_, worst), _ in parts)
            return typical, worst
        prompt_mid = (cfg["prompt_tokens"]["min"] + cfg["prompt_tokens"]["max"]) / 2
        prompt_max = cfg["prompt_tokens"]["max"]
        out_typ = cfg["max_tokens"] * assumptions["expected_output_fraction_of_max_tokens"]
        per_stream = min(
            assumptions["decode_tokens_per_s_per_stream"],
            assumptions["aggregate_decode_tokens_per_s"] / max(concurrency, 1.0),
        )
        typical = prompt_mid / assumptions["prefill_tokens_per_s"] + out_typ / per_stream
        worst = prompt_max / assumptions["prefill_tokens_per_s"] + cfg["max_tokens"] / per_stream
        return typical, worst

    breakdown = []
    total_s = 0.0
    for unit in units:
        if unit.arrival_type == "poisson":
            typical, worst = expected_request_s(unit.prompt_source, 1.0)
            concurrency_guess = unit.load * typical
            typical, worst = expected_request_s(unit.prompt_source, max(1.0, concurrency_guess))
            arrival_span = unit.requests / unit.load
            drain = worst + assumptions["drain_grace_s"]
            busy = arrival_span + drain
        else:
            typical, worst = expected_request_s(unit.prompt_source, unit.load)
            busy = unit.requests * typical / unit.load + worst
        warm = unit.warmup * expected_request_s(unit.prompt_source, 1.0)[0]
        setup = assumptions["per_cell_setup_s"] + assumptions["cache_flush_s"]
        unit_total = busy + warm + setup
        total_s += unit_total
        breakdown.append(
            {
                "unit": unit.unit_id,
                "requests": unit.requests,
                "busy_s": round(busy, 1),
                "warmup_s": round(warm, 1),
                "setup_s": setup,
                "total_s": round(unit_total, 1),
            }
        )
    startup_s = mode["startup_launches"] * (
        assumptions.get("startup_launch_s", plan["startup"]["planning_cold_start_s"])
        + assumptions["teardown_s"]
    )
    raw_total = total_s + startup_s + assumptions["teardown_s"]
    margin = assumptions["safety_margin"]
    planned = raw_total * margin
    gpu_hours = planned / 3600 * assumptions["gpus_at_once"]
    cap_min = mode["planned_wall_minutes_max"]
    return {
        "status": "planning-arithmetic-unmeasured",
        "mode": mode_name,
        "units": len(units),
        "offered_requests": sum(u.requests for u in units),
        "warmup_requests": sum(u.warmup for u in units),
        "startup_launches": mode["startup_launches"],
        "arrival_and_drain_s": round(total_s, 1),
        "startup_s": round(startup_s, 1),
        "teardown_s": assumptions["teardown_s"],
        "raw_total_minutes": round(raw_total / 60, 1),
        "safety_margin": margin,
        "planned_wall_minutes": round(planned / 60, 1),
        "planned_gpu_hours": round(gpu_hours, 2),
        "planned_wall_minutes_max": cap_min,
        "fits_cap": planned / 60 <= cap_min,
        "suite_wall_budget_minutes": 180.0,
        "fits_suite_budget": planned / 60 <= 180.0,
        "assumptions": assumptions,
        "breakdown": breakdown,
    }
