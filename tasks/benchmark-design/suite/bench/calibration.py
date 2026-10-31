"""Freeze an empirical prefill envelope from separate, complete baseline evidence.

The x axis is the recorder's approximate prompt-length covariate, not hidden engine
token IDs. The upper residual is an observed envelope, not a confidence bound.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import statistics
from pathlib import Path
from typing import Any

from bench.model_tokens import ModelTokenizer
from bench.plan import load_plan, plan_digest, resolve_mode, validate_plan
from bench.prompts import make_prompt
from bench.records import REQUESTS_FILE, RUN_FILE, EvidenceError, load_events
from bench.tokens import count_tokens


class CalibrationError(ValueError):
    """The supplied baseline cannot support a frozen calibration."""


def scoring_length_domain(plan: dict[str, Any]) -> list[int]:
    """Enumerate the actual deterministic quick/full covariates, not config targets."""
    lengths = []
    for mode in ("quick", "full"):
        if mode not in plan["modes"]:
            continue
        for unit in resolve_mode(plan, mode):
            for index in range(unit.requests):
                grid = unit.prompt_token_grid
                spec = make_prompt(
                    plan,
                    unit.prompt_source,
                    f"{unit.unit_id}/measure",
                    unit.repetition,
                    index,
                    forced_prompt_tokens=grid[index % len(grid)] if grid else None,
                )
                lengths.append(spec.approx_prompt_tokens)
    if not lengths:
        raise CalibrationError("no planned scoring population to calibrate")
    return [min(lengths), max(lengths)]


def fit_envelope(points: list[tuple[int, float]], *, min_samples: int = 30) -> dict[str, Any]:
    """Nonnegative least-squares slope plus the largest observed positive residual.

    All samples must be finite positive observations. Requiring at least five lengths
    avoids claiming a useful length curve from one cluster. No samples are discarded.
    """
    if len(points) < min_samples or len({x for x, _ in points}) < 5:
        raise CalibrationError("need the declared sample minimum and at least five lengths")
    if any(
        isinstance(x, bool)
        or not isinstance(x, int)
        or not 0 < x <= 1_048_576
        or not math.isfinite(y)
        or y <= 0
        for x, y in points
    ):
        raise CalibrationError("nonpositive or nonfinite calibration observation")
    mean_x = statistics.fmean(x for x, _ in points)
    mean_y = statistics.fmean(y for _, y in points)
    denom = sum((x - mean_x) ** 2 for x, _ in points)
    slope = max(0.0, sum((x - mean_x) * (y - mean_y) for x, y in points) / denom)
    intercept = max(0.0, mean_y - slope * mean_x)
    residual = max(0.0, max(y - intercept - slope * x for x, y in points))
    intercept += residual
    if not all(math.isfinite(v) for v in (slope, intercept)):
        raise CalibrationError("fit overflow")
    return {
        "method": "nonnegative-ols-plus-observed-upper-residual-v1",
        "samples": len(points),
        "distinct_lengths": len({x for x, _ in points}),
        "length_domain": [min(x for x, _ in points), max(x for x, _ in points)],
        "intercept_s": intercept,
        "slope_s_per_prompt_token": slope,
        "observed_max_positive_residual_s": residual,
        "observed_envelope_covers_all_samples": all(
            intercept + slope * x + 1e-12 >= y for x, y in points
        ),
    }


def calibrate(
    plan_path: Path,
    events: Path,
    *,
    tokenizer: ModelTokenizer | None = None,
    allow_mock: bool = False,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate an entire calibration capture, then return a new plan and receipt.

    A different output plan digest forces fresh captures: this function cannot make
    already-recorded evaluation data pass by changing its deadlines retrospectively.
    """
    # Local import avoids making the fit-only function depend on the CLI evaluator.
    from evaluate import Evaluator

    plan = load_plan(plan_path)
    evidence = load_events(events)
    if evidence.schema_errors:
        raise CalibrationError("calibration contains schema errors")
    run = evidence.run.raw
    if run.get("time_scale") != 1:
        raise CalibrationError("calibration requires unscaled measurement time")
    kind = run.get("engine", {}).get("kind")
    if kind == "mock" and not allow_mock:
        raise CalibrationError("mock calibration requires explicit development permission")
    if kind != "mock" and tokenizer is None:
        raise CalibrationError("real calibration requires verified tokenizer assets")
    mode = run.get("mode")
    if mode != "calibration" or mode not in plan["modes"]:
        raise CalibrationError("unknown calibration mode")
    scheduled = resolve_mode(plan, mode)
    if not scheduled or any(unit.role != "calibration" for unit in scheduled):
        raise CalibrationError("use a separate calibration-only mode, never scoring data")
    evaluator = Evaluator(plan, evidence, plan_digest(plan_path), tokenizer)
    evaluator.expected_units = {unit.unit_id: unit for unit in scheduled}
    evaluator.run_gates()
    evaluator.judge_all()
    evaluator.startup()
    evaluator.population()
    # A fit must not bless an incomplete baseline simply because its measurement
    # rows happen to be complete. Startup and warmup are part of the frozen plan.
    issues = evaluator.reasons + evaluator.inconclusive
    if issues:
        raise CalibrationError("; ".join(issues[:4]))
    by_unit: dict[str, list[Any]] = {}
    ids: set[str] = set()
    points = []
    for record in evidence.requests:
        raw = record.raw
        if raw["request_id"] in ids:
            raise CalibrationError("duplicate request identity")
        ids.add(raw["request_id"])
        if raw["phase"] != "measure":
            continue
        unit = evaluator.expected_units.get(raw["unit_id"])
        verdict = evaluator.verdicts[record.request_id]
        if unit is None or raw.get("role") != "calibration" or verdict.category != "completed":
            raise CalibrationError("every planned calibration observation must complete correctly")
        recomputed_length = sum(
            count_tokens(message["content"]) + 4 for message in raw["prompt"]["messages"]
        )
        if raw["prompt"]["approx_prompt_tokens"] != recomputed_length:
            raise CalibrationError("recorded prompt-length covariate disagrees with messages")
        by_unit.setdefault(unit.unit_id, []).append(record)
        points.append((raw["prompt"]["approx_prompt_tokens"], verdict.ttft_s))
    if {key: len(rows) for key, rows in by_unit.items()} != {
        unit.unit_id: unit.requests for unit in scheduled
    }:
        raise CalibrationError("missing or excess calibration population")
    fit = fit_envelope(points, min_samples=plan["statistics"]["min_requests_per_cell"])
    scoring_domain = scoring_length_domain(plan)
    if (
        not fit["length_domain"][0]
        <= scoring_domain[0]
        <= scoring_domain[1]
        <= fit["length_domain"][1]
    ):
        raise CalibrationError("calibration does not cover all planned quick/full prompt lengths")
    request_path = events / REQUESTS_FILE
    if not request_path.is_file():
        request_path = events / (REQUESTS_FILE + ".gz")
    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (events / RUN_FILE, request_path)
    }
    identity = {
        "schema_version": "1.0.0",
        "source_plan_digest": plan_digest(plan_path),
        "raw_evidence_sha256": hashes,
        "run_id": evidence.run.run_id,
        "engine_kind": kind,
        "engine_identity": {
            key: run.get("engine", {}).get(key)
            for key in ("model_id", "lock_digest", "lock_image", "lock_model")
        },
        "tokenizer_identity": run.get("tokenizer"),
        "fit": fit,
        "planned_scoring_length_domain": scoring_domain,
        "length_unit": "candidate approximate prompt-length covariate; not engine token IDs",
        "limitations": [
            "Observed upper envelope, not a calibrated confidence/prediction interval",
            "Length extrapolation and engine/tokenizer parity remain unvalidated",
            "Freeze this plan before fresh controls and comparisons; do not refit on scored runs",
            "Mock fits are software checks, not real-engine calibration",
        ],
    }
    encoded_identity = json.dumps(identity, sort_keys=True, allow_nan=False).encode()
    calibration_id = hashlib.sha256(encoded_identity).hexdigest()
    fitted = copy.deepcopy(plan)
    fitted["plan_id"] = plan["plan_id"] + "-cal-" + calibration_id[:12]
    fitted["status"] = "candidate-baseline-calibrated; fresh scoring captures required"
    fitted["deadlines"]["prefill"].update(
        intercept_s=fit["intercept_s"],
        slope_s_per_prompt_token=fit["slope_s_per_prompt_token"],
        note="Separate baseline empirical upper envelope; see calibration and its limitations",
    )
    fitted["calibration"] = {"id": calibration_id, **identity}
    validate_plan(fitted)
    return fitted, {"calibration_id": calibration_id, **identity}


def write_frozen(
    fitted: dict[str, Any], receipt: dict[str, Any], out_plan: Path, out_receipt: Path
) -> None:
    """Exclusive output only; refuse collisions before opening either file."""
    if out_plan.resolve() == out_receipt.resolve() or any(
        path.exists() or path.is_symlink() or not path.parent.is_dir()
        for path in (out_plan, out_receipt)
    ):
        raise CalibrationError("new, distinct output paths with existing parents required")
    encoded = (json.dumps(fitted, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    final = {**receipt, "output_plan_sha256": hashlib.sha256(encoded).hexdigest()}
    with out_plan.open("xb") as handle:
        handle.write(encoded)
    with out_receipt.open("x", encoding="utf-8") as handle:
        json.dump(final, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--out-plan", type=Path, required=True)
    parser.add_argument("--out-receipt", type=Path, required=True)
    parser.add_argument("--tokenizer-snapshot", type=Path)
    parser.add_argument("--allow-mock", action="store_true")
    args = parser.parse_args(argv)
    try:
        plan = load_plan(args.plan)
        tokenizer = (
            ModelTokenizer(args.tokenizer_snapshot, plan["target"]["model_role"])
            if args.tokenizer_snapshot
            else None
        )
        fitted, receipt = calibrate(
            args.plan, args.events, tokenizer=tokenizer, allow_mock=args.allow_mock
        )
        write_frozen(fitted, receipt, args.out_plan, args.out_receipt)
    except (CalibrationError, EvidenceError, OSError, ValueError) as exc:
        print(json.dumps({"status": "REFUSED", "reason": str(exc)}))
        return 2
    print(json.dumps({"status": "FROZEN", "plan": str(args.out_plan), "official_result": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
