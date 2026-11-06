"""Seeded synthetic event generator. Family names never reach the evidence it writes."""

import argparse
import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUITE = Path(__file__).resolve().parents[1]
REPETITIONS, REQUESTS_PER_WINDOW, LAUNCHES, ANCHOR_PROMPTS = 5, 60, 3, 40
MODEL_ID = "synth-model"
REVISION = "0" * 40


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _words(prompt_id: str, count: int) -> list[str]:
    return [_sha(f"{prompt_id}/{index}")[:5] + " " for index in range(count)]


@dataclass
class Spec:
    """One simulated request. Mutators edit this before it's turned into events."""

    record: dict[str, Any]
    tokens: list[dict[str, Any]]
    finish: str | None = "stop"
    done: bool = True
    usage: int | None = -1  # -1 means "report the true count".
    model: str = MODEL_ID
    extra: list[tuple[float, str]] = field(default_factory=list)
    leading: list[tuple[float, dict[str, Any]]] = field(default_factory=list)

    @property
    def is_plain(self) -> bool:
        return not self.record["is_canary"] and not self.record["is_anchor"]


@dataclass
class Model:
    plan: dict[str, Any]
    manifest: dict[str, Any]
    specs: list[Spec]
    launches: list[dict[str, Any]]
    phases: list[dict[str, Any]]
    rng: random.Random
    write_tokens: bool = True
    write_reference: bool = True
    stable_anchors: int = ANCHOR_PROMPTS
    reference_revision: str = REVISION

    @property
    def measured(self) -> list[Spec]:
        return [spec for spec in self.specs if spec.record["phase"] == "measure"]

    def pick(self, specs: list[Spec], count: int) -> list[Spec]:
        return self.rng.sample(specs, min(count, len(specs)))


def _prompt(cell: str, index: int) -> tuple[str, bool, bool, int]:
    if index % 20 == 0:
        return f"canary-{index % 7}", True, False, 3
    if index % 20 == 1:
        anchor = _sha(f"{cell}/{index}")
        return f"anchor-{int(anchor, 16) % ANCHOR_PROMPTS}", False, True, 32
    return f"{cell}-{index:04d}", False, False, 40


def _answer(prompt_id: str) -> str:
    return str(1000 + int(prompt_id.split("-")[1]))


def _request(rid, phase, launch, cell, rep, prompt, t_start, rng, ttft=0.3, gap=0.03) -> Spec:
    prompt_id, canary, anchor, count = prompt
    shared = canary or anchor
    sent = t_start + 0.001
    noise = rng.uniform(0.95, 1.05)
    first = sent + ttft * noise
    tokens = [
        {"t": round(first + index * gap * noise, 6), "channel": "content", "text": word}
        for index, word in enumerate(_words(prompt_id, count))
    ]
    if canary:
        tokens = [{**token, "text": text} for token, text in zip(
            tokens, ("ans", "wer: ", _answer(prompt_id)), strict=True
        )]
    record = {
        "request_id": rid, "phase": phase, "launch": launch, "cell": cell, "repetition": rep,
        "prompt_id": prompt_id, "prompt_sha256": _sha(prompt_id if shared else rid),
        "is_canary": canary, "is_anchor": anchor,
        "max_tokens": 64 if canary else 128 if anchor else 256,
        "min_output_tokens": 1 if shared else 32, "ignore_eos": False, "prefix_cache": "miss",
        "t_scheduled": t_start, "t_sent": sent, "t_headers": sent + 0.01,
        "t_end": tokens[-1]["t"] + 0.01, "http_status": 200,
        "content_type": "text/event-stream", "client_outcome": "closed", "error": None,
    }
    return Spec(record, tokens)


def build_plan() -> dict[str, Any]:
    plan = json.loads((SUITE / "workload.json").read_text())
    plan["model"].update(role="smoke", revision=REVISION)
    plan["prompt_sets"] = {
        "canary": {"path": "prompts/canary.jsonl", "sha256": None, "input_tokens": 8},
        "anchor": {"path": "prompts/anchor.jsonl", "sha256": None, "input_tokens": 8},
    }
    plan["reference"]["path"] = "references/reference.jsonl"
    plan["gates"].update(max_anchor_divergence=0.1)
    plan["deadlines"]["request_timeout_s"] = 30
    return plan


def build_model(seed: int) -> Model:
    rng = random.Random(seed)
    plan = build_plan()
    cells = plan["modes"]["quick"]["cells"]
    specs, launches, phases = [], [], []
    for launch in range(LAUNCHES):
        start = launch * 5000.0
        launches.append({"launch": launch, "event": "launch_start", "t": start})
        launches.append(
            {"launch": launch, "event": "readiness_ok", "t": start + 30, "model_id": MODEL_ID}
        )
        probe = _request(
            f"p-{launch}-0", "probe", launch, None, None, ("canary-0", True, False, 3),
            start + 31, rng,
        )
        specs.append(probe)
        launches.append(
            {"launch": launch, "event": "probe", "t": start + 31, "request_id": f"p-{launch}-0"}
        )
        launches.append({"launch": launch, "event": "launch_stop", "t": start + 4900})
        phases.append({"phase": "launch", "launch": launch, "t_start": start,
                       "t_end": start + 31, "status": "ok"})
        phases.append({"phase": "warmup", "launch": launch, "t_start": start + 40,
                       "t_end": start + 60, "status": "ok"})
        phases.append({"phase": "cleanup", "launch": launch, "t_start": start + 4890,
                       "t_end": start + 4900, "status": "ok"})
    planned: dict[str, dict[str, int]] = {cell: {} for cell in cells}
    for position, cell in enumerate(cells):
        for rep in range(REPETITIONS):
            planned[cell][str(rep)] = REQUESTS_PER_WINDOW
            launch = rep % LAUNCHES
            window = launch * 5000.0 + 100 + (rep * len(cells) + position) * 200
            phases.append({"phase": "measure", "launch": launch, "cell": cell, "repetition": rep,
                           "t_start": window, "t_end": window + 120, "status": "ok"})
            phases.append({"phase": "drain", "launch": launch, "cell": cell, "repetition": rep,
                           "t_start": window + 120, "t_end": window + 130, "status": "ok"})
            for index in range(REQUESTS_PER_WINDOW):
                t_start = window + index * 2 + rng.uniform(0, 0.5)
                specs.append(_request(
                    f"r-{cell}-{rep}-{index}", "measure", launch, cell, rep,
                    _prompt(f"{cell}/{rep}", index), t_start, rng,
                ))
    manifest = {
        "schema_version": 1, "started_at_utc": "2026-01-01T00:00:00Z",
        "capture": {"tool": "synth.py", "stand_in": True}, "suite_commit": "0" * 40,
        "plan_sha256": "0" * 64, "mode": "quick", "seed": seed,
        "image_digest": "sha256:" + "0" * 64,
        "model": {"repository": "synth", "revision": REVISION},
        "hardware": {"gpu": "none", "gpus": 0}, "filesystem_cache": "warm_fs",
        "planned_requests": planned, "planned_launches": LAUNCHES, "aborted": None,
    }
    return Model(plan, manifest, specs, launches, phases, rng)


def _emit(spec: Spec) -> list[tuple[float, str]]:
    """Turn a spec into raw SSE payloads, one event per distinct token time and channel."""
    base = {"model": spec.model, "object": "chat.completion.chunk"}
    first = spec.tokens[0]["t"] if spec.tokens else spec.record["t_end"]
    out = [(t, json.dumps(payload)) for t, payload in spec.leading]
    out.append((first, json.dumps({
        **base, "choices": [{"index": 0, "delta": {"role": "assistant"}}]
    })))
    groups: list[dict[str, Any]] = []
    for token in spec.tokens:
        if groups and (groups[-1]["t"], groups[-1]["channel"]) == (token["t"], token["channel"]):
            groups[-1]["text"] += token["text"]
        else:
            groups.append(dict(token))
    for group in groups:
        delta = {group["channel"]: group["text"]}
        out.append((group["t"], json.dumps(
            {**base, "choices": [{"index": 0, "delta": delta}]}, ensure_ascii=False
        )))
    last = spec.tokens[-1]["t"] if spec.tokens else first
    if spec.finish is not None:
        out.append((last, json.dumps(
            {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": spec.finish}]}
        )))
    usage = len(spec.tokens) if spec.usage == -1 else spec.usage
    if usage is not None:
        out.append((last, json.dumps(
            {**base, "choices": [], "usage": {"completion_tokens": usage}}
        )))
    out += spec.extra
    if spec.done:
        out.append((last, "[DONE]"))
    return out


def _dump(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in records))


def write(model: Model, out: Path) -> tuple[Path, Path]:
    """Write the plan and one run directory. Returns (run directory, workload path)."""
    run = out / "run"
    run.mkdir(parents=True, exist_ok=True)
    (run / "manifest.json").write_text(json.dumps(model.manifest))
    _dump(run / "phases.jsonl", model.phases)
    _dump(run / "launches.jsonl", model.launches)
    _dump(run / "requests.jsonl", [spec.record for spec in model.specs])
    sse, tokens = [], []
    for spec in model.specs:
        rid = spec.record["request_id"]
        for seq, (t, data) in enumerate(_emit(spec)):
            sse.append({"request_id": rid, "seq": seq, "t": t, "data": data})
        for index, token in enumerate(spec.tokens):
            tokens.append({"request_id": rid, "index": index, "t": token["t"],
                           "channel": token["channel"], "token_id": index})
    _dump(run / "sse.jsonl", sse)
    if model.write_tokens:
        _dump(run / "tokens.jsonl", tokens)

    (out / "workload.json").write_text(json.dumps(model.plan))
    prompts = {spec.record["prompt_id"] for spec in model.specs if spec.record["is_canary"]}
    _dump(out / "prompts/canary.jsonl", [
        {"prompt_id": prompt, "messages": [], "expected": _answer(prompt)}
        for prompt in sorted(prompts)
    ])
    if model.write_reference:
        entries = []
        for index in range(ANCHOR_PROMPTS):
            prompt = f"anchor-{index}"
            text = "".join(_words(prompt, 32))
            entries.append({
                "model_revision": model.reference_revision, "prompt_sha256": _sha(prompt),
                "output": text, "output_sha256": _sha(text), "tokens": 32,
                "finish_reason": "stop", "stable": index < model.stable_anchors,
            })
        _dump(out / "references/reference.jsonl", entries)
    return run, out / "workload.json"


# Mutators. Each describes server or capture behavior, and none writes a label.


def noisy(model: Model) -> None:
    for spec in model.measured:
        scale = model.rng.uniform(0.7, 1.4)
        start = spec.record["t_sent"]
        for token in spec.tokens:
            token["t"] = start + (token["t"] - start) * scale
        spec.record["t_end"] = spec.tokens[-1]["t"] + 0.01


def burst(model: Model) -> None:
    for spec in model.measured:
        if spec.is_plain:
            for index, token in enumerate(spec.tokens):
                token["t"] = spec.tokens[index - index % 5]["t"]


def _delay(spec: Spec, start: int, seconds: float) -> None:
    for token in spec.tokens[start:]:
        token["t"] += seconds
    spec.record["t_end"] += seconds


def stall(model: Model) -> None:
    for spec in model.pick([s for s in model.measured if s.is_plain], 120):
        _delay(spec, len(spec.tokens) // 2, 2.0)


def late_first_token(model: Model) -> None:
    for spec in model.measured:
        _delay(spec, 0, 3.0)


def reasoning(model: Model) -> None:
    for spec in model.measured:
        if not spec.record["is_canary"]:
            for token in spec.tokens[:5]:
                token["channel"] = "reasoning_content"
            spec.usage = len(spec.tokens) + 2


def unicode_boundary(model: Model) -> None:
    for spec in model.pick([s for s in model.measured if s.is_plain], 100):
        spec.tokens[3]["text"] = "\U0001f642"
        spec.tokens[4]["t"] = spec.tokens[3]["t"]


def heartbeats(model: Model) -> None:
    for spec in model.measured:
        early = spec.record["t_sent"] + 0.001
        spec.leading.append((early, {"choices": [{"index": 0, "delta": {"content": ""}}]}))


def stray_replacement(model: Model) -> None:
    model.pick([s for s in model.measured if s.is_plain], 1)[0].tokens[2]["text"] = "�"


def _reset(spec: Spec) -> None:
    spec.finish, spec.done = None, False
    spec.tokens = spec.tokens[:3]
    spec.record.update(client_outcome="reset", t_end=spec.tokens[-1]["t"] + 0.01)


def errors_few(model: Model) -> None:
    _reset(model.pick([s for s in model.measured if s.is_plain], 1)[0])


def errors_many(model: Model) -> None:
    for spec in model.pick([s for s in model.measured if s.is_plain], 60):
        _reset(spec)


def http_errors(model: Model) -> None:
    for spec in model.pick([s for s in model.measured if s.is_plain], 60):
        spec.tokens, spec.finish, spec.done = [], None, False
        spec.record.update(http_status=503, content_type="application/json")


def missing_done(model: Model) -> None:
    for spec in model.pick([s for s in model.measured if s.is_plain], 60):
        spec.done = False


def missing_finish(model: Model) -> None:
    for spec in model.pick([s for s in model.measured if s.is_plain], 60):
        spec.finish = None


def empty_output(model: Model) -> None:
    for spec in model.pick([s for s in model.measured if s.is_plain], 60):
        spec.tokens = []


def timed_out(model: Model) -> None:
    for spec in model.pick([s for s in model.measured if s.is_plain], 60):
        _delay(spec, 1, 40.0)


def vanished(model: Model) -> None:
    gone = {id(spec) for spec in model.pick(model.measured, 5)}
    model.specs = [spec for spec in model.specs if id(spec) not in gone]


def wrong_canary(model: Model) -> None:
    for spec in model.pick([s for s in model.measured if s.record["is_canary"]], 1):
        spec.tokens[-1]["text"] = "wrong"


def false_usage(model: Model) -> None:
    for spec in model.pick(model.measured, 3):
        spec.usage = len(spec.tokens) * 3


def usage_absent(model: Model) -> None:
    for spec in model.specs:
        spec.usage = None


def truncated_length(model: Model) -> None:
    for spec in model.pick([s for s in model.measured if s.is_plain], 60):
        spec.finish = "length"


def truncated_short(model: Model) -> None:
    for spec in model.pick([s for s in model.measured if s.is_plain], 60):
        spec.tokens = spec.tokens[:10]


def _anchors(model: Model) -> list[Spec]:
    return [spec for spec in model.measured if spec.record["is_anchor"]]


def placeholder_first_token(model: Model) -> None:
    for spec in model.measured:
        if spec.record["is_canary"]:
            continue
        first = dict(spec.tokens[0])
        first.update(t=spec.record["t_sent"] + 0.002, text="...")
        spec.tokens.insert(0, first)


def shortened_stop(model: Model) -> None:
    for spec in _anchors(model):
        spec.tokens = spec.tokens[:8]


def anchor_noise(model: Model) -> None:
    for spec in model.pick(_anchors(model), 3):
        spec.tokens[0]["text"] = "flip "


def malformed_sse(model: Model) -> None:
    for spec in model.pick([s for s in model.measured if s.is_plain], 60):
        spec.extra.append((spec.tokens[-1]["t"], "{not json"))


def two_choices(model: Model) -> None:
    choice = {"index": 0, "delta": {"content": "x"}}
    for spec in model.pick([s for s in model.measured if s.is_plain], 60):
        spec.extra.append((spec.tokens[-1]["t"], json.dumps({"choices": [choice, choice]})))


def timing_corrupt(model: Model) -> None:
    spec = model.pick([s for s in model.measured if s.is_plain], 1)[0]
    spec.tokens[5]["t"] = spec.tokens[4]["t"] - 0.5


def missing_tokens(model: Model) -> None:
    model.write_tokens = False


def no_reference(model: Model) -> None:
    model.write_reference = False


def other_revision_reference(model: Model) -> None:
    model.reference_revision = "1" * 40


def few_stable_anchors(model: Model) -> None:
    model.stable_anchors = 10


def client_lag_many(model: Model) -> None:
    for spec in model.pick([s for s in model.measured if s.is_plain], 18):
        spec.record["t_scheduled"] -= 0.5


def client_lag_few(model: Model) -> None:
    for spec in model.pick([s for s in model.measured if s.is_plain], 3):
        spec.record["t_scheduled"] -= 0.5


def warmup_unfinished(model: Model) -> None:
    next(p for p in model.phases if p["phase"] == "warmup")["status"] = "timeout"


def aborted_capture(model: Model) -> None:
    model.manifest["aborted"] = "operator"
    model.specs = [s for s in model.specs if s.record["repetition"] in (None, 0, 1)]


def fail_fast_stop(model: Model) -> None:
    wrong_canary(model)
    model.manifest["aborted"] = "early_stop:wrong_output"


def _probes(model: Model) -> list[Spec]:
    return [spec for spec in model.specs if spec.record["phase"] == "probe"]


def _failing_probe(spec: Spec) -> None:
    spec.tokens, spec.finish, spec.done = [], None, False
    spec.record.update(http_status=503, content_type="application/json")


def misleading_readiness(model: Model) -> None:
    """Readiness is early, and a generation only succeeds a minute later."""
    rng_state = model.rng
    for spec in _probes(model):
        launch = spec.record["launch"]
        late = _request(
            f"p-{launch}-1", "probe", launch, None, None, ("canary-0", True, False, 3),
            launch * 5000.0 + 91, rng_state,
        )
        model.specs.append(late)
        _failing_probe(spec)


def readiness_only(model: Model) -> None:
    for spec in _probes(model):
        _failing_probe(spec)


def startup_crash(model: Model) -> None:
    probe = _probes(model)[0]
    _failing_probe(probe)
    model.launches.append({"launch": 0, "event": "crash", "t": 40.0})


def startup_timeout(model: Model) -> None:
    for spec in _probes(model):
        _delay(spec, 0, 1000.0)


def wrong_probe(model: Model) -> None:
    _probes(model)[0].tokens[-1]["text"] = "wrong"


def startup_stopped(model: Model) -> None:
    """Fail-fast stop: the first launch never served, so later launches never ran."""
    _failing_probe(_probes(model)[0])
    model.launches.append({"launch": 0, "event": "startup_timeout", "t": 900.0})
    model.manifest["aborted"] = "early_stop:startup_failed"
    model.specs = [spec for spec in model.specs if spec.record["launch"] == 0]
    model.launches = [event for event in model.launches if event["launch"] == 0]


def mid_run_crash(model: Model) -> None:
    model.launches.append({"launch": 1, "event": "crash", "t": 5500.0})
    model.manifest["aborted"] = "early_stop:server_crash"
    model.specs = [spec for spec in model.specs if spec.record["launch"] < 2]
    model.launches = [event for event in model.launches if event["launch"] < 2]


def few_launches(model: Model) -> None:
    model.manifest["planned_launches"] = 2


def one_chunk(model: Model) -> None:
    """All generated text in one event, so FTGR can't be resolved."""
    for spec in model.measured:
        if spec.is_plain:
            for token in spec.tokens:
                token["t"] = spec.tokens[0]["t"]


def thin_run(model: Model) -> None:
    model.specs = [
        spec for spec in model.specs
        if spec.record["phase"] != "measure"
        or int(spec.record["request_id"].rsplit("-", 1)[1]) < 40
    ]
    for repetitions in model.manifest["planned_requests"].values():
        for repetition in repetitions:
            repetitions[repetition] = 40


# Severity scenarios for sensitivity runs. Each takes the fault size as a number.


def _rescale(spec: Spec, scale: float) -> None:
    start = spec.record["t_sent"]
    for token in spec.tokens:
        token["t"] = start + (token["t"] - start) * scale
    spec.record["t_end"] = spec.tokens[-1]["t"] + 0.01


def repetition_drift(model: Model, sigma: float) -> None:
    """Noise shared by a repetition, which request-level noise can't imitate."""
    scales: dict[tuple[str, int], float] = {}
    for spec in model.measured:
        key = (spec.record["cell"], spec.record["repetition"])
        scale = scales.setdefault(key, model.rng.lognormvariate(0, sigma))
        _rescale(spec, scale)
    for spec in _probes(model):
        _shift(spec, 31 * (model.rng.lognormvariate(0, sigma) - 1))


def _shift(spec: Spec, seconds: float) -> None:
    for key in ("t_scheduled", "t_sent", "t_headers", "t_end"):
        spec.record[key] += seconds
    for token in spec.tokens:
        token["t"] += seconds


def slower_ttft(model: Model, percent: float) -> None:
    for spec in model.measured:
        _delay(spec, 0, 0.3 * percent / 100)


def stalled_requests(model: Model, percent: float) -> None:
    plain = [spec for spec in model.measured if spec.is_plain]
    for spec in model.pick(plain, round(len(plain) * percent / 100)):
        _delay(spec, len(spec.tokens) // 2, 2.0)


def failed_requests(model: Model, percent: float) -> None:
    for spec in model.pick(model.measured, round(len(model.measured) * percent / 100)):
        _reset(spec)


def slower_startup(model: Model, percent: float) -> None:
    for spec in _probes(model):
        _shift(spec, 31 * percent / 100)


SEVERITIES = {
    "slower_ttft": (slower_ttft, (5, 10, 20, 50)),
    "stalled_requests": (stalled_requests, (2, 5, 10, 25)),
    "failed_requests": (failed_requests, (0.5, 1, 2, 5)),
    "slower_startup": (slower_startup, (5, 10, 25, 50)),
}


FAMILIES = {
    "clean": None, "noisy": noisy, "burst": burst, "stall": stall,
    "late_first_token": late_first_token, "reasoning": reasoning,
    "unicode_boundary": unicode_boundary, "heartbeats": heartbeats,
    "stray_replacement": stray_replacement, "errors_few": errors_few,
    "errors_many": errors_many, "http_errors": http_errors, "missing_done": missing_done,
    "missing_finish": missing_finish, "empty_output": empty_output, "timed_out": timed_out,
    "vanished": vanished, "wrong_canary": wrong_canary, "false_usage": false_usage,
    "usage_absent": usage_absent, "truncated_length": truncated_length,
    "truncated_short": truncated_short, "placeholder_first_token": placeholder_first_token,
    "shortened_stop": shortened_stop, "anchor_noise": anchor_noise,
    "malformed_sse": malformed_sse, "two_choices": two_choices,
    "timing_corrupt": timing_corrupt, "missing_tokens": missing_tokens,
    "no_reference": no_reference, "other_revision_reference": other_revision_reference,
    "few_stable_anchors": few_stable_anchors, "client_lag_many": client_lag_many,
    "client_lag_few": client_lag_few, "warmup_unfinished": warmup_unfinished,
    "aborted_capture": aborted_capture, "fail_fast_stop": fail_fast_stop,
    "misleading_readiness": misleading_readiness, "readiness_only": readiness_only,
    "startup_crash": startup_crash, "startup_timeout": startup_timeout,
    "wrong_probe": wrong_probe, "few_launches": few_launches, "one_chunk": one_chunk,
    "thin_run": thin_run, "startup_stopped": startup_stopped, "mid_run_crash": mid_run_crash,
}


def generate(family: str, seed: int, out: Path) -> tuple[Path, Path]:
    model = build_model(seed)
    mutator = FAMILIES[family]
    if mutator is not None:
        mutator(model)
    return write(model, out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--family", choices=sorted(FAMILIES), default="clean")
    parser.add_argument("--seed", type=int, default=1)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run, workload = generate(args.family, args.seed, args.out)
    print(json.dumps({"events": str(run), "workload": str(workload)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
