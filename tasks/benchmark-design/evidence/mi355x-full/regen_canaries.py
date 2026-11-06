"""Regenerate the reference's canary entries at the new budget; keep the anchor entries as captured."""
import asyncio, hashlib, json, sys
from pathlib import Path
sys.path.insert(0, ".")
from analysis.events import load_plan
from collect.capture import Capture, Prompts, Recorder, Spec, answers, parse_stream, prompt_hash

PLAN, REF = Path("workload.mi355x.json"), Path("reference.mi355x.jsonl")


async def main():
    data = json.loads(PLAN.read_text())
    data["canary"]["max_tokens"] = 1024
    PLAN.write_text(json.dumps(data, indent=2) + "\n")
    plan = load_plan(PLAN)
    prompts = Prompts(plan.data, Path("."), None)
    rec = Recorder(Path("/work/out/.canary-reference"))
    cap = Capture(plan, rec, "http://127.0.0.1:8000")
    cap.data = {**plan.data, "gates": {**plan.data["gates"], "usage_expected": False}}
    assert await cap.wait_ready(0, rec.now(), lambda: True)
    block, slots = plan.data["canary"], asyncio.Semaphore(16)

    async def one(prompt):
        async with slots:
            spec = Spec(f"c-{prompt['prompt_id']}", 0.0, prompt["prompt_id"], prompt["messages"],
                        block["max_tokens"], block["min_output_tokens"])
            return parse_stream((await cap.send(spec, "reference", 0, rec.now()))["events"])

    canaries = prompts.sets[block["prompt_set"]]
    outputs = await asyncio.gather(*(one(p) for p in canaries))
    await cap.client.aclose(); rec.close()
    anchor_hashes = {prompt_hash(p["messages"]) for p in prompts.sets[plan.data["anchor"]["prompt_set"]]}
    entries = [e for e in map(json.loads, REF.read_text().splitlines()) if e["prompt_sha256"] in anchor_hashes]
    for prompt, out in zip(canaries, outputs, strict=True):
        entries.append({"model_revision": plan.data["model"]["revision"],
                        "prompt_sha256": prompt_hash(prompt["messages"]), "output": out.text,
                        "output_sha256": hashlib.sha256(out.text.encode()).hexdigest(),
                        "tokens": out.usage_tokens, "finish_reason": out.finish_reason,
                        "stable": answers(out.content, prompt["expected"])})
    REF.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))
    data["reference"]["sha256"] = hashlib.sha256(REF.read_bytes()).hexdigest()
    PLAN.write_text(json.dumps(data, indent=2) + "\n")
    stable = [o.usage_tokens for o, e in zip(outputs, entries[-len(outputs):]) if e["stable"]]
    print(json.dumps({"anchors": len(entries) - len(outputs), "canaries": len(outputs),
                      "stable_canaries": len(stable), "max_tokens_used": max(stable or [0]),
                      "finish": sorted({o.finish_reason for o in outputs})}))


asyncio.run(main())
