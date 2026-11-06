"""Stand-in capture client, because the organizer's runner doesn't exist yet.

evaluate.py depends only on the event format, never on this file.
"""

import argparse
import asyncio
import hashlib
import json
import math
import random
import shlex
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from analysis.axes import PRIMARY_QUANTILE  # noqa: E402
from analysis.events import Plan, load_plan  # noqa: E402
from analysis.gates import (  # noqa: E402
    INVALID,
    PROBE_TOLERATED,
    answers,
    check_request,
    parse_stream,
)

CHAT_PATH = "/v1/chat/completions"
AFTER_DONE_GRACE_S = 1.0


def prompt_hash(messages: list[dict[str, str]]) -> str:
    text = json.dumps(messages, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass
class Spec:
    request_id: str
    offset_s: float
    prompt_id: str
    messages: list[dict[str, str]]
    max_tokens: int
    min_output_tokens: int
    ignore_eos: bool = False
    prefix_cache: str = "miss"
    is_canary: bool = False
    is_anchor: bool = False
    cell: str | None = None
    repetition: int | None = None


class Prompts:
    def __init__(self, plan: dict[str, Any], base: Path, reference: dict | None):
        self.sets = {}
        for name, entry in plan["prompt_sets"].items():
            lines = (base / entry["path"]).read_text(encoding="utf-8").splitlines()
            self.sets[name] = [json.loads(line) for line in lines if line.strip()]
        canary = plan["canary"]["prompt_set"]
        if reference is not None:
            self.sets[canary] = [
                prompt for prompt in self.sets[canary]
                if reference.get(prompt_hash(prompt["messages"]), {}).get("stable")
            ]
        if not self.sets[canary]:
            raise ValueError("no usable canaries")


def _unique(messages: list[dict[str, str]], key: str) -> list[dict[str, str]]:
    tag = hashlib.sha256(key.encode()).hexdigest()[:16]
    head, *rest = messages
    return [{**head, "content": f"Session {tag}.\n{head['content']}"}, *rest]


def _gap(rng: random.Random, arrival: dict[str, Any]) -> float:
    """One seeded inter-arrival gap with mean 1/rate_rps.

    Poisson gaps are exponential. Gamma gaps take vLLM's burstiness as the shape: below 1 requests
    clump into bursts separated by long quiet spells, at the same average rate.
    """
    rate = arrival["rate_rps"]
    if arrival["process"] == "gamma":
        shape = arrival["burstiness"]
        return rng.gammavariate(shape, 1 / (shape * rate))
    return rng.expovariate(rate)


def window_minimum(plan: dict[str, Any]) -> int:
    """Counted requests each window needs for its p90 to be powered.

    Rates follow capacity, so a slow cell on slow hardware fills a fixed window with too few
    requests. Comparisons take p90 per repetition, so each window needs min_valid/(1-0.9) of its
    own. The 5% spare covers a failed or excluded request.
    """
    minimum = plan["gates"]["min_valid_per_quantile"]
    return math.ceil(round(minimum / (1 - PRIMARY_QUANTILE) * 1.05, 9))


def build_window(
    plan: dict[str, Any], prompts: Prompts, cell: str, repetition: int, duration_s: float,
    min_counted: int = 0,
) -> list[Spec]:
    """One seeded window, lengthened past duration_s until it holds min_counted plain requests.

    Two runs of one plan produce identical requests.
    """
    config = plan["cells"][cell]
    key = f"{plan['seed']}/{cell}/{repetition}"
    rng = random.Random(key)
    if config["arrival"]["rate_rps"] is None:
        raise ValueError(f"{cell} has no rate_rps; set it before capture")
    names, weights = zip(*config["prompt_mix"].items(), strict=True)
    specs, clock, counted = [], _gap(rng, config["arrival"]), 0
    while clock < duration_s or counted < min_counted:
        index = len(specs)
        rid = f"r-{cell}-{repetition}-{index}"
        draw = rng.random()
        common: dict[str, Any] = {"request_id": rid, "offset_s": clock, "cell": cell,
                                  "repetition": repetition,
                                  "prefix_cache": config["prefix_cache"]}
        if draw < config["canary_fraction"]:
            block = plan["canary"]
            prompt = rng.choice(prompts.sets[block["prompt_set"]])
            spec = Spec(prompt_id=prompt["prompt_id"], messages=prompt["messages"],
                        max_tokens=block["max_tokens"],
                        min_output_tokens=block["min_output_tokens"], is_canary=True, **common)
        elif draw < config["canary_fraction"] + config["anchor_fraction"]:
            block = plan["anchor"]
            prompt = rng.choice(prompts.sets[block["prompt_set"]])
            spec = Spec(prompt_id=prompt["prompt_id"], messages=prompt["messages"],
                        max_tokens=block["max_tokens"],
                        min_output_tokens=block["min_output_tokens"], is_anchor=True, **common)
        else:
            name = rng.choices(names, weights)[0]
            prompt = rng.choice(prompts.sets[name])
            messages = prompt["messages"]
            if config["prefix_cache"] == "miss":
                messages = _unique(messages, f"{key}/{index}")
            spec = Spec(prompt_id=prompt["prompt_id"], messages=messages,
                        max_tokens=config["output"]["max_tokens"],
                        min_output_tokens=config["output"]["min_output_tokens"],
                        ignore_eos=config["output"]["ignore_eos"], **common)
            counted += 1
        specs.append(spec)
        clock += _gap(rng, config["arrival"])
    return specs


def layout(plan: dict[str, Any], mode: dict[str, Any]) -> list[list[tuple[str, int]]]:
    """Deal seeded-order windows evenly across launches, so launches repeat every cell."""
    windows = [(cell, rep) for cell in mode["cells"] for rep in range(mode["repetitions"])]
    random.Random(f"{plan['seed']}/layout").shuffle(windows)
    dealt: list[list[tuple[str, int]]] = [[] for _ in range(mode["launches"])]
    for index, window in enumerate(windows):
        dealt[index % mode["launches"]].append(window)
    return dealt


@dataclass
class EarlyStop:
    """Stops only on outcomes that already make the run invalid. See STATES.md."""

    config: dict[str, Any]
    max_failed_fraction: float
    planned: dict[str, int]
    run_failing: int = 0
    failed: Counter = field(default_factory=Counter)

    def observe(self, result: Any) -> str | None:
        cell = result.request["cell"]
        if result.run_failing:
            self.run_failing += 1
            if self.run_failing >= self.config["max_run_failing_requests"]:
                reason = next(r for r in result.reasons if r not in ("client_lag",))
                return f"early_stop:{reason}"
        self.failed[cell] += result.state == INVALID
        # The cell's judged population can't exceed its plan, so this already fails the gate.
        if self.failed[cell] > self.max_failed_fraction * self.planned[cell]:
            return "early_stop:failure_rate"
        return None


class Recorder:
    def __init__(self, out: Path):
        out.mkdir(parents=True, exist_ok=True)
        self.out = out
        self.origin = time.monotonic()
        self.files: dict[str, TextIO] = {
            name: (out / f"{name}.jsonl").open("w", encoding="utf-8")
            for name in ("phases", "launches", "requests", "sse", "metrics")
        }

    def now(self) -> float:
        return time.monotonic() - self.origin

    def write(self, name: str, record: dict[str, Any]) -> None:
        self.files[name].write(json.dumps(record, ensure_ascii=False) + "\n")

    def close(self) -> None:
        for handle in self.files.values():
            handle.close()


class Capture:
    def __init__(
        self, plan: Plan, recorder: Recorder, base_url: str, planned: dict[str, int] | None = None
    ):
        self.plan = plan
        self.data = plan.data
        self.rec = recorder
        self.url = base_url.rstrip("/")
        self.model_id: str | None = None
        self.client = httpx.AsyncClient(trust_env=False, timeout=None)
        self.stop = EarlyStop(
            self.data["early_stop"], self.data["gates"]["max_failed_fraction"], planned or {}
        )
        self.abort: str | None = None

    def body(self, spec: Spec) -> dict[str, Any]:
        body = {**self.data["request"], "model": self.model_id, "messages": spec.messages,
                "max_tokens": spec.max_tokens}
        if spec.ignore_eos:
            body["ignore_eos"] = True
        return body

    async def _stream(self, spec: Spec, record: dict[str, Any], events: list) -> None:
        timeout = self.data["deadlines"]["request_timeout_s"]
        loop = asyncio.get_running_loop()
        fields: list[str] = []
        seq = done = 0
        try:
            async with asyncio.timeout(timeout) as limit, self.client.stream(
                "POST", self.url + CHAT_PATH, json=self.body(spec)
            ) as response:
                record["t_headers"] = self.rec.now()
                record["http_status"] = response.status_code
                record["content_type"] = response.headers.get("content-type")
                async for line in response.aiter_lines():
                    line = line.rstrip("\r\n")
                    if line.startswith("data:"):
                        fields.append(line[5:].removeprefix(" "))
                    elif not line and fields:
                        data = "\n".join(fields)
                        fields = []
                        event = {"request_id": spec.request_id, "seq": seq,
                                 "t": self.rec.now(), "data": data}
                        seq += 1
                        events.append(event)
                        self.rec.write("sse", event)
                        if data == "[DONE]" and not done:
                            # Keep listening briefly, so text sent after [DONE] is recorded.
                            done = 1
                            limit.reschedule(loop.time() + AFTER_DONE_GRACE_S)
            record["client_outcome"] = "closed"
        except TimeoutError:
            record["client_outcome"] = "closed" if done else "timeout"
        except httpx.HTTPError as exc:
            record.update(client_outcome="reset", error=repr(exc))

    async def send(
        self, spec: Spec, phase: str, launch: int, scheduled: float
    ) -> dict[str, Any]:
        record: dict[str, Any] = {
            "request_id": spec.request_id, "phase": phase, "launch": launch, "cell": spec.cell,
            "repetition": spec.repetition, "prompt_id": spec.prompt_id,
            "prompt_sha256": prompt_hash(spec.messages), "is_canary": spec.is_canary,
            "is_anchor": spec.is_anchor, "max_tokens": spec.max_tokens,
            "min_output_tokens": spec.min_output_tokens, "ignore_eos": spec.ignore_eos,
            "prefix_cache": spec.prefix_cache, "t_scheduled": scheduled,
            "t_sent": self.rec.now(), "t_headers": None, "t_end": None, "http_status": None,
            "content_type": None, "client_outcome": "aborted", "error": None,
        }
        events: list[dict[str, Any]] = []
        try:
            await self._stream(spec, record, events)
        finally:
            record["t_end"] = self.rec.now()
            self.rec.write("requests", record)
        if phase == "measure" and self.abort is None:
            result = check_request(record, events, [], self.plan)
            self.abort = self.stop.observe(result)
        return {"record": record, "events": events}

    async def scrape(self) -> None:
        config = self.data["server_metrics"]
        while True:
            await asyncio.sleep(config["poll_interval_s"])
            t = self.rec.now()
            try:
                response = await self.client.get(self.url + config["path"], timeout=5)
                self.rec.write("metrics", {"t": t, "status": response.status_code,
                                           "body": response.text})
            except httpx.HTTPError:
                self.rec.write("metrics", {"t": t, "status": None, "body": None})

    async def wait_ready(self, launch: int, started: float, alive) -> bool:
        limit = self.data["deadlines"]["startup_timeout_s"]
        poll = self.data["startup"]["poll_interval_s"]
        while self.rec.now() - started < limit:
            if not alive():
                return False
            try:
                response = await self.client.get(self.url + "/v1/models", timeout=5)
                models = response.json().get("data", [])
                if response.status_code == 200 and models:
                    self.model_id = models[0]["id"]
                    self.rec.write("launches", {"launch": launch, "event": "readiness_ok",
                                                "t": self.rec.now(), "model_id": self.model_id})
                    return True
            except (httpx.HTTPError, ValueError, KeyError):
                pass
            await asyncio.sleep(poll)
        return False

    async def probe(self, launch: int, started: float, prompts: Prompts, alive) -> bool:
        """Probe until one generation passes. A readiness response never counts."""
        config = self.data["canary"]
        limit = self.data["deadlines"]["startup_timeout_s"]
        rng = random.Random(f"{self.data['seed']}/probe/{launch}")
        index = 0
        while self.rec.now() - started < limit and alive():
            prompt = rng.choice(prompts.sets[config["prompt_set"]])
            rid = f"p-{launch}-{index}"
            index += 1
            self.rec.write("launches", {"launch": launch, "event": "probe",
                                        "t": self.rec.now(), "request_id": rid})
            spec = Spec(rid, 0.0, prompt["prompt_id"], prompt["messages"], config["max_tokens"],
                        config["min_output_tokens"], is_canary=True)
            sent = await self.send(spec, "probe", launch, self.rec.now())
            result = check_request(sent["record"], sent["events"], [], self.plan)
            # Tokens are counted after capture, so match evaluate.py's probe rule without them.
            if (PROBE_TOLERATED | {"tokens_missing"}).issuperset(result.reasons):
                return True
            await asyncio.sleep(self.data["startup"]["probe_interval_s"])
        return False

    async def warmup(self, launch: int, prompts: Prompts) -> None:
        config = self.data["warmup"]
        start = self.rec.now()
        status = "ok"
        for index in range(config["requests"]):
            if self.rec.now() - start > config["max_duration_s"]:
                status = "timeout"
                break
            prompt = prompts.sets[config["prompt_set"]][index % 10]
            spec = Spec(f"w-{launch}-{index}", 0.0, prompt["prompt_id"],
                        _unique(prompt["messages"], f"warmup/{launch}/{index}"), 64, 1)
            await self.send(spec, "warmup", launch, self.rec.now())
        self.rec.write("phases", {"phase": "warmup", "launch": launch, "t_start": start,
                                  "t_end": self.rec.now(), "status": status})

    async def window(self, launch: int, cell: str, rep: int, specs: list[Spec]) -> None:
        start = self.rec.now()
        tasks = []
        for spec in specs:
            scheduled = start + spec.offset_s
            await asyncio.sleep(max(0.0, scheduled - self.rec.now()))
            if self.abort is not None:
                break
            tasks.append(asyncio.create_task(self.send(spec, "measure", launch, scheduled)))
        end = self.rec.now()
        status = "aborted" if self.abort else "ok"
        self.rec.write("phases", {"phase": "measure", "launch": launch, "cell": cell,
                                  "repetition": rep, "t_start": start, "t_end": end,
                                  "status": status})
        finished, pending = await asyncio.wait(
            tasks, timeout=self.data["deadlines"]["drain_timeout_s"]
        ) if tasks else (set(), set())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self.rec.write("phases", {"phase": "drain", "launch": launch, "cell": cell,
                                  "repetition": rep, "t_start": end, "t_end": self.rec.now(),
                                  "status": "timeout" if pending else "ok"})

    async def launch(
        self, launch: int, windows: list, specs: dict, prompts: Prompts, server: "Server"
    ) -> None:
        started = self.rec.now()
        self.rec.write("launches", {"launch": launch, "event": "launch_start", "t": started})
        server.start()
        ready = await self.wait_ready(launch, started, server.alive)
        good = ready and await self.probe(launch, started, prompts, server.alive)
        self.rec.write("phases", {"phase": "launch", "launch": launch, "t_start": started,
                                  "t_end": self.rec.now(), "status": "ok" if good else "timeout"})
        scraper = asyncio.create_task(self.scrape())
        try:
            if not server.alive():
                self.rec.write("launches", {"launch": launch, "event": "crash",
                                            "t": self.rec.now()})
                self.abort = "early_stop:server_crash"
            elif not good:
                self.rec.write("launches", {"launch": launch, "event": "startup_timeout",
                                            "t": self.rec.now()})
                self.abort = "early_stop:startup_failed"
            else:
                await self.warmup(launch, prompts)
                for cell, rep in windows:
                    await self.window(launch, cell, rep, specs[cell, rep])
                    if not server.alive():
                        self.rec.write("launches", {"launch": launch, "event": "crash",
                                                    "t": self.rec.now()})
                        self.abort = self.abort or "early_stop:server_crash"
                    if self.abort:
                        break
        finally:
            scraper.cancel()
            await asyncio.gather(scraper, return_exceptions=True)
            cleanup = self.rec.now()
            exit_code = server.stop()
            self.rec.write("launches", {"launch": launch, "event": "launch_stop",
                                        "t": self.rec.now(), "exit_code": exit_code})
            self.rec.write("phases", {"phase": "cleanup", "launch": launch, "t_start": cleanup,
                                      "t_end": self.rec.now(), "status": "ok"})


class Server:
    """Runs the server in the foreground, so its exit is visible as a crash."""

    def __init__(self, start_cmd: str, stop_cmd: str | None):
        self.start_cmd, self.stop_cmd = start_cmd, stop_cmd
        self.process: subprocess.Popen | None = None

    def start(self) -> None:
        self.process = subprocess.Popen(shlex.split(self.start_cmd))

    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> int | None:
        if self.process is None:
            return None
        if self.stop_cmd:
            subprocess.run(shlex.split(self.stop_cmd), check=False)
        else:
            self.process.terminate()
        try:
            return self.process.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self.process.kill()
            return self.process.wait()


def _git_commit() -> str | None:
    try:
        done = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True,
                              check=False, cwd=Path(__file__).resolve().parent)
    except OSError:  # no git on the capture host
        return None
    return done.stdout.strip() or None


def _manifest(args, plan: Plan, mode: dict, planned: dict) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "capture": {"tool": "capture.py", "stand_in": True},
        "suite_commit": _git_commit(),
        "plan_sha256": hashlib.sha256(args.workload.read_bytes()).hexdigest(),
        "mode": args.mode, "seed": plan.data["seed"], "image_digest": args.image_digest,
        "model": {"repository": args.model_repository, "revision": plan.data["model"]["revision"]},
        "hardware": {"gpu": args.gpu, "gpus": args.gpus},
        "filesystem_cache": mode["filesystem_cache"],
        "planned_requests": planned, "planned_launches": mode["launches"], "aborted": None,
    }


async def run_capture(args) -> int:
    plan = load_plan(args.workload)
    if args.mode not in plan.data["modes"]:
        raise SystemExit(f"mode {args.mode!r} is not in {args.workload}")
    mode = plan.data["modes"][args.mode]
    prompts = Prompts(plan.data, args.workload.parent, plan.reference)
    windows = layout(plan.data, mode)
    specs = {
        (cell, rep): build_window(plan.data, prompts, cell, rep, mode["duration_s"],
                                  window_minimum(plan.data))
        for dealt in windows for cell, rep in dealt
    }
    planned: dict[str, dict[str, int]] = defaultdict(dict)
    for (cell, rep), items in specs.items():
        planned[cell][str(rep)] = len(items)
    manifest = _manifest(args, plan, mode, dict(planned))
    recorder = Recorder(args.out)
    totals = {cell: sum(counts.values()) for cell, counts in planned.items()}
    capture = Capture(plan, recorder, args.base_url, totals)
    server = Server(args.start_cmd, args.stop_cmd)
    try:
        for launch, dealt in enumerate(windows):
            await capture.launch(launch, dealt, specs, prompts, server)
            if capture.abort:
                break
    finally:
        manifest["aborted"] = capture.abort
        (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2))
        await capture.client.aclose()
        recorder.close()
    print(json.dumps({"events": str(args.out), "aborted": capture.abort}))
    return 0


async def run_reference(args) -> int:
    """Generate the reference from a running baseline, one request at a time."""
    plan = load_plan(args.workload)
    data = plan.data
    prompts = Prompts(data, args.workload.parent, None)
    recorder = Recorder(args.out.parent / ".reference-capture")
    capture = Capture(plan, recorder, args.base_url)
    capture.data = {**data, "gates": {**data["gates"], "usage_expected": False}}
    await capture.wait_ready(0, recorder.now(), lambda: True)
    entries = []

    async def generate(prompt: dict[str, Any], block: dict[str, Any], label: str) -> Any:
        spec = Spec(label, 0.0, prompt["prompt_id"], prompt["messages"], block["max_tokens"],
                    block["min_output_tokens"])
        sent = await capture.send(spec, "reference", 0, recorder.now())
        return parse_stream(sent["events"])

    for prompt in prompts.sets[data["anchor"]["prompt_set"]]:
        block = data["anchor"]
        outputs = [
            await generate(prompt, block, f"a-{prompt['prompt_id']}-{index}")
            for index in range(block["passes"])
        ]
        first = outputs[0]
        entries.append({
            "model_revision": data["model"]["revision"],
            "prompt_sha256": prompt_hash(prompt["messages"]), "output": first.text,
            "output_sha256": hashlib.sha256(first.text.encode()).hexdigest(),
            "tokens": first.usage_tokens, "finish_reason": first.finish_reason,
            "stable": bool(first.text) and all(o.text == first.text for o in outputs),
        })
    # A canary is stable when it answers correctly, not when its text repeats, so unlike anchors
    # it gains nothing from running alone; thinking models make it long enough to matter.
    slots = asyncio.Semaphore(16)

    async def canary(prompt: dict[str, Any]) -> Any:
        async with slots:
            return await generate(prompt, data["canary"], f"c-{prompt['prompt_id']}")

    canaries = prompts.sets[data["canary"]["prompt_set"]]
    outputs = await asyncio.gather(*(canary(prompt) for prompt in canaries))
    for prompt, output in zip(canaries, outputs, strict=True):
        entries.append({
            "model_revision": data["model"]["revision"],
            "prompt_sha256": prompt_hash(prompt["messages"]), "output": output.text,
            "output_sha256": hashlib.sha256(output.text.encode()).hexdigest(),
            "tokens": output.usage_tokens, "finish_reason": output.finish_reason,
            "stable": answers(output.content, prompt["expected"]),
        })
    await capture.client.aclose()
    recorder.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries))
    print(json.dumps({"entries": len(entries), "stable": sum(e["stable"] for e in entries)}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run")
    run.add_argument("--workload", type=Path, required=True)
    run.add_argument("--mode", required=True, help="a mode in the plan: quick, full or a targeted one")
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--start-cmd", required=True, help="foreground server command")
    run.add_argument("--stop-cmd")
    run.add_argument("--base-url", default="http://127.0.0.1:8000")
    run.add_argument("--image-digest", default="unknown")
    run.add_argument("--model-repository", default="unknown")
    run.add_argument("--gpu", default="unknown")
    run.add_argument("--gpus", type=int, default=1)
    reference = commands.add_parser("reference")
    reference.add_argument("--workload", type=Path, required=True)
    reference.add_argument("--out", type=Path, required=True)
    reference.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    return asyncio.run(run_capture(args) if args.command == "run" else run_reference(args))


if __name__ == "__main__":
    raise SystemExit(main())
