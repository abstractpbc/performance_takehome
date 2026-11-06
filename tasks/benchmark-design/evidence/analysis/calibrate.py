"""Margin calibration from one MI355X run, using the suite's own interval code.

For each cell and axis: the spread between repetitions, the half-width two independent runs of that
spread would get (quick 5 per side, full 8 per side, at the mode's adjusted confidence), and the
smallest change that could be called a regression (margin + half-width). Then the launch effect:
each repetition's log-TTFT residual from its cell mean, grouped by launch.
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean, stdev

sys.path.insert(0, sys.argv[1])  # suite/
from analysis.compare import MARGINS, adjusted_confidence, t_critical  # noqa: E402

run, plan = Path(sys.argv[2]), json.loads(Path(sys.argv[3]).read_text())
report = json.loads((run / "report.json").read_text())
launch_of = {}
for line in (run / "out/full/phases.jsonl").read_text().splitlines():
    p = json.loads(line)
    if p["phase"] == "measure":
        launch_of[(p["cell"], str(p["repetition"]))] = p["launch"]

conf = {m: adjusted_confidence(plan["comparison"]["confidence"], 2 * len(plan["modes"][m]["cells"]) + 1)
        for m in ("quick", "full")}
sides = {"quick": plan["modes"]["quick"]["repetitions"], "full": plan["modes"]["full"]["repetitions"]}
margins = plan["comparison"]["margins"]


def width(sd, n, c):
    return t_critical(c, 2 * n - 2) * sd * math.sqrt(2 / n)


def change(axis, x):
    return f"{math.expm1(x) * 100:4.0f}%" if axis in ("ttft", "startup") else f"{x:.3f}"


print(f"{'cell':14} {'axis':9} {'sd/rep':>7} {'margin':>6} | {'quick hw':>8} {'detects':>7} {'equiv?':>6} "
      f"| {'full hw':>8} {'detects':>7} {'equiv?':>6}")
rows = [(c, a, axes[a]["repetitions"]) for c, axes in report["axes"]["cells"].items() for a in ("ttft", "fluidity")]
rows.append(("(launches)", "startup", report["axes"]["startup"]["repetitions"]))
for cell, axis, reps in rows:
    sd, margin = stdev(reps.values()), margins[MARGINS[axis]]
    out = f"{cell:14} {axis:9} {sd:7.3f} {margin:6.2f}"
    for m in ("quick", "full"):
        n = 3 if axis == "startup" and m == "quick" else 4 if axis == "startup" else sides[m]
        hw = width(sd, n, conf[m])
        out += f" | {hw:8.3f} {change(axis, margin + hw):>7} {'yes' if hw < margin else 'no':>6}"
    print(out)

residuals = defaultdict(list)
for cell, axes in report["axes"]["cells"].items():
    reps = axes["ttft"]["repetitions"]
    center = mean(reps.values())
    for rep, value in reps.items():
        residuals[launch_of[(cell, rep)]].append(value - center)
print("\nlog TTFT p90 residual by launch (all cells):")
for launch in sorted(residuals):
    r = residuals[launch]
    print(f"  launch {launch}: n={len(r):2}  mean {mean(r):+.3f}  se {stdev(r) / math.sqrt(len(r)):.3f}")
