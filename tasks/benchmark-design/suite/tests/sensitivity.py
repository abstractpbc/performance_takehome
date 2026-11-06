"""A/A false-positive and per-severity detection rates on synthetic events."""

import argparse
import json
import random
import sys
import tempfile
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.axes import analyze  # noqa: E402
from analysis.compare import compare  # noqa: E402
from analysis.events import load_plan  # noqa: E402
from synth import SEVERITIES, build_model, repetition_drift, write  # noqa: E402

CONTROLS = {"quiet": 0.02, "noisy": 0.08}


def _pair(job: tuple[str | None, float, float, int]) -> dict:
    name, severity, sigma, seed = job
    with tempfile.TemporaryDirectory() as directory:
        analyses = []
        for side, side_seed in (("base", seed), ("test", seed + 100000)):
            model = build_model(side_seed)
            model.rng = random.Random(side_seed + 1)
            repetition_drift(model, sigma)
            if side == "test" and name is not None:
                SEVERITIES[name][0](model, severity)
            run_path, workload = write(model, Path(directory) / side)
            plan = load_plan(workload)
            analyses.append(analyze(run_path, plan))
    base, test = analyses
    result = compare(test, base, plan.data)
    return {
        "states": [base.state, test.state],
        "decisions": [(d["axis"], d["decision"]) for d in result["decisions"]],
    }


def run(jobs: list, workers: int) -> list[dict]:
    with ProcessPoolExecutor(workers) as pool:
        return list(pool.map(_pair, jobs))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pairs", type=int, default=30)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    report: dict = {"pairs": args.pairs, "controls": {}, "severities": {}}

    print(f"{'CONTROL':10}{'DECISIONS':11}{'FALSE CONFIDENT':17}OUTCOMES")
    for name, sigma in CONTROLS.items():
        results = run([(None, 0, sigma, seed) for seed in range(args.pairs)], args.workers)
        counts = Counter(decision for r in results for _, decision in r["decisions"])
        wrong = counts["regression"] + counts["improvement"]
        report["controls"][name] = {"sigma": sigma, "outcomes": dict(counts), "confident": wrong}
        print(f"{name:10}{sum(counts.values()):<11}{wrong:<17}{dict(counts)}")

    axis_for = {"slower_ttft": "ttft", "stalled_requests": "fluidity",
                "slower_startup": "startup"}
    print(f"\n{'FAULT':20}{'SIZE %':8}{'REGRESSION':12}{'RUN INVALID':13}OUTCOMES")
    for name, (_, sizes) in SEVERITIES.items():
        report["severities"][name] = {}
        for size in sizes:
            results = run([(name, size, 0.02, seed) for seed in range(args.pairs)], args.workers)
            invalid = sum(r["states"][1] == "invalid" for r in results)
            axis = axis_for.get(name)
            picked = [d for r in results for a, d in r["decisions"] if a == axis]
            counts = Counter(picked)
            share = counts["regression"] / len(picked) if picked else None
            report["severities"][name][str(size)] = {
                "regression_share": share, "run_invalid_share": invalid / len(results),
                "outcomes": dict(counts),
            }
            shown = "-" if share is None else f"{share:.0%}"
            print(f"{name:20}{size:<8}{shown:12}{invalid / len(results):<13.0%}{dict(counts)}")
    if args.out:
        args.out.write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
