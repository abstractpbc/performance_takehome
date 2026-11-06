"""Write the targeted plan for the three underpowered cells and print its exact schedule.

Same rates, reference and gates as the full run; a `fill` mode with 2 repetitions in one launch,
whose windows lengthen until each holds window_minimum counted requests.
"""
import json
import sys
from pathlib import Path

SUITE = Path(sys.argv[1])
sys.path.insert(0, str(SUITE))
from analysis.events import load_plan  # noqa: E402
from collect.capture import Prompts, build_window, layout, window_minimum  # noqa: E402

full, out = Path(sys.argv[2]), Path(sys.argv[3])
data = json.loads(full.read_text())
data["modes"]["fill"] = {
    "cells": ["long_prompt", "long_output", "prefix_hit"], "repetitions": 2, "duration_s": 120,
    "launches": 3, "repetition_layout": "across_launches", "quantiles": [0.5, 0.9],
    "filesystem_cache": "warm_fs", "max_wall_s": 1800,
}
out.write_text(json.dumps(data, indent=2) + "\n")
plan = load_plan(out)
mode = data["modes"]["fill"]
prompts = Prompts(data, out.parent, plan.reference)
need = window_minimum(data)
total = 0.0
print("minimum counted per window:", need)
for launch, dealt in enumerate(layout(data, mode)):
    for cell, rep in dealt:
        specs = build_window(data, prompts, cell, rep, mode["duration_s"], need)
        counted = sum(not (s.is_canary or s.is_anchor) for s in specs)
        total += specs[-1].offset_s
        print(f"  launch {launch} {cell:12} rep {rep}: {len(specs):3} requests, {counted} counted, "
              f"window {specs[-1].offset_s:6.1f} s")
print(f"request time {total:.0f} s = {total / 60:.1f} min")
