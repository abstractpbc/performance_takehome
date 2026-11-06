"""Measure each cell's capacity against a running server and set rate_rps from it.

Capacity is closed-loop throughput: `--concurrency` requests always in flight, with the cell's
prompts and output policy, in valid completions per second after a warmup. Each cell's rate is
then load_fraction x capacity. Cells with the same prompts, output and cache policy share one
measurement, so bursty reuses chat_light's.

Fixed-length outputs make a batch finish together, so completions arrive in waves and counting
them in a short window is off by up to a whole batch. Each request is instead credited with the
share of its lifetime that falls inside the window, which is Little's law: concurrency over the
mean request time.
"""

import argparse
import asyncio
import json
import random
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.events import load_plan  # noqa: E402
from analysis.gates import check_request  # noqa: E402
from collect.capture import Capture, Prompts, Recorder, Spec, _unique  # noqa: E402


def shape(config: dict[str, Any]) -> str:
    return json.dumps([config["prompt_mix"], config["output"], config["prefix_cache"]],
                      sort_keys=True)


async def saturate(capture: Capture, plan: dict, prompts: Prompts, cell: str,
                   concurrency: int, warmup_s: float, measure_s: float) -> dict[str, Any]:
    config = plan["cells"][cell]
    names, weights = zip(*config["prompt_mix"].items(), strict=True)
    rng = random.Random(f"{plan['seed']}/sweep/{cell}")
    start = capture.rec.now()
    window = (start + warmup_s, start + warmup_s + measure_s)
    counter = 0
    finished: list[tuple[float, float, bool]] = []

    async def worker() -> None:
        nonlocal counter
        while capture.rec.now() < window[1]:
            index, counter = counter, counter + 1
            prompt = rng.choice(prompts.sets[rng.choices(names, weights)[0]])
            messages = prompt["messages"]
            if config["prefix_cache"] == "miss":
                messages = _unique(messages, f"{plan['seed']}/sweep/{cell}/{index}")
            spec = Spec(f"s-{cell}-{index}", 0.0, prompt["prompt_id"], messages,
                        config["output"]["max_tokens"], config["output"]["min_output_tokens"],
                        config["output"]["ignore_eos"], config["prefix_cache"], cell=cell)
            sent = await capture.send(spec, "sweep", 0, capture.rec.now())
            result = check_request(sent["record"], sent["events"], [], capture.plan)
            record = sent["record"]
            # Token counts exist only after tokenize_events, so that is the one reason allowed.
            ok = set(result.reasons) <= {"tokens_missing"}
            finished.append((record["t_sent"], record["t_end"], ok))

    await asyncio.gather(*(worker() for _ in range(concurrency)))
    return {"requests": len(finished), **capacity(finished, window)}


def capacity(finished: list[tuple[float, float, bool]], window: tuple[float, float]) -> dict:
    """Completions per second inside the window, each request credited by its overlap share."""
    credit = failed = 0.0
    for sent_at, end, ok in finished:
        overlap = min(end, window[1]) - max(sent_at, window[0])
        if overlap > 0:
            share = overlap / (end - sent_at)
            credit += share if ok else 0.0
            failed += 0.0 if ok else share
    return {"failed_share": round(failed, 2), "capacity_rps": credit / (window[1] - window[0])}


async def run(args) -> int:
    plan = load_plan(args.workload)
    data = plan.data
    prompts = Prompts(data, args.workload.parent, None)
    capture = Capture(plan, Recorder(args.out), args.base_url)
    capture.data = {**data, "gates": {**data["gates"], "usage_expected": False}}
    cells = args.cells or list(data["cells"])
    if not await capture.wait_ready(0, capture.rec.now(), lambda: True):
        raise SystemExit("server never became ready")
    measured: dict[str, dict[str, Any]] = {}
    results = {}
    try:
        for cell in cells:
            key = shape(data["cells"][cell])
            if key not in measured:
                measured[key] = await saturate(capture, data, prompts, cell, args.concurrency,
                                               args.warmup_s, args.measure_s)
                result = measured[key]
                total = result["capacity_rps"] * args.measure_s + result["failed_share"]
                if result["capacity_rps"] <= 0 or result["failed_share"] > 0.05 * total:
                    raise SystemExit(f"sweep of {cell} is unusable: {result}")
            fraction = data["cells"][cell]["arrival"]["load_fraction"]
            results[cell] = {**measured[key], "load_fraction": fraction,
                             "rate_rps": round(fraction * measured[key]["capacity_rps"], 3)}
            print(json.dumps({"cell": cell, **results[cell]}), flush=True)
    finally:
        await capture.client.aclose()
        capture.rec.close()
    (args.out / "sweep.json").write_text(json.dumps(results, indent=2))
    if args.write_plan:
        for cell, result in results.items():
            data["cells"][cell]["arrival"]["rate_rps"] = result["rate_rps"]
        args.write_plan.write_text(json.dumps(data, indent=2) + "\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cells", nargs="*")
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--warmup-s", type=float, default=20)
    parser.add_argument("--measure-s", type=float, default=60)
    parser.add_argument("--write-plan", type=Path, help="write the plan with rate_rps filled in")
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
