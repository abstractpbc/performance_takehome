"""Re-measure the correctness rules on the MI355X evidence (Qwen3.6-27B)."""
import json, math, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "suite"))
from analysis.events import load_run, load_plan
from analysis.gates import assess_run, parse_stream

for run_dir, plan_path in [(sys.argv[1], sys.argv[2]), (sys.argv[3], sys.argv[4])]:
    run, plan = load_run(Path(run_dir)), load_plan(Path(plan_path))
    a = assess_run(run, plan)
    res = [r for r in a.results if r.request["phase"] == "measure"] or a.results
    can = [r for r in a.results if r.request["is_canary"]]
    anc = [r for r in a.results if r.request["is_anchor"]]
    diffs, hidden = [], 0
    for r in a.results:
        s = r.stream
        if s.usage_tokens is None or not r.token_times:
            continue
        count = len(r.token_times) + s.hidden_tokens
        diffs.append(s.usage_tokens - count)
        hidden += s.hidden_tokens > 0
    print(run_dir, a.state, a.reasons)
    print(" phases", Counter(r.request["phase"] for r in a.results))
    print(" canaries", len(can), Counter(r.state for r in can), Counter(x for r in can for x in r.reasons))
    print(" canary tokens max", max(len(r.token_times) for r in can) if can else None)
    print(" anchors", len(anc), "stability", a.stability)
    print(" usage-count diff", Counter(diffs).most_common(12), "min", min(diffs), "max", max(diffs), "n", len(diffs), "with hidden", hidden)
    print(" reasons", a.request_reasons)
ref = [json.loads(l) for l in open(sys.argv[5])]
print("reference entries", len(ref), "stable", sum(e["stable"] for e in ref))

# Exact-text anchor matches against the reference.
from analysis.gates import Stream  # noqa: E402
import hashlib  # noqa: E402
byhash = {e["prompt_sha256"]: e for e in ref}
for run_dir in (sys.argv[1], sys.argv[3]):
    run = load_run(Path(run_dir))
    a = assess_run(run, load_plan(Path(sys.argv[2])))
    anc = [r for r in a.results if r.request["is_anchor"]]
    exact = sum(byhash[r.request["prompt_sha256"]]["output"] == r.stream.text for r in anc)
    print(run_dir, "anchors exact", exact, "of", len(anc))
