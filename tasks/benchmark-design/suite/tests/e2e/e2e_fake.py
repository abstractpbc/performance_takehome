"""End-to-end capture, tokenize and evaluate over real HTTP against the fake server."""

import argparse
import copy
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path

SUITE = Path(__file__).resolve().parents[2]
TOKENIZE = SUITE / "collect" / "tokenize_events.py"
FAKE = SUITE / "tests" / "e2e" / "fake_server.py"
PORT = 18765
BASE_URL = f"http://127.0.0.1:{PORT}"
REPOSITORY, REVISION = "Qwen/Qwen3-8B", "b968826d9c46dd6066d109eabc6255188de91218"
SCENARIOS = {
    "clean_a": [],
    "clean_b": [],
    "slow_ttft": ["--ttft", "0.15"],
    "false_usage": ["--fault", "false_usage"],
    "wrong": ["--fault", "wrong"],
    "crash": ["--fault", "crash"],
}


def debug_plan(out: Path) -> Path:
    plan = json.loads((SUITE / "workload.json").read_text())
    plan = copy.deepcopy(plan)
    plan["model"].update(role="smoke", revision=REVISION)
    for entry in plan["prompt_sets"].values():
        entry["path"] = str(SUITE / entry["path"])
    plan["reference"]["path"] = str(out / "reference.jsonl")
    for cell in plan["cells"].values():
        cell["arrival"]["rate_rps"] = 5.0
    plan["modes"]["quick"].update(repetitions=3, duration_s=15, launches=3)
    plan["gates"].update(max_anchor_divergence=0.1, min_stable_anchors=10)
    plan["warmup"]["requests"] = 5
    plan["deadlines"]["startup_timeout_s"] = 30
    path = out / "workload.debug.json"
    path.write_text(json.dumps(plan))
    return path


def server_cmd(*extra: str) -> list[str]:
    return [sys.executable, str(FAKE), "--port", str(PORT), *extra]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--only", nargs="*", default=list(SCENARIOS))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    workload = debug_plan(args.out)
    capture = [sys.executable, str(SUITE / "collect" / "capture.py")]

    server = subprocess.Popen(server_cmd("--ttft", "0", "--gap", "0", "--startup", "0"))
    try:
        time.sleep(0.5)
        subprocess.run([*capture, "reference", "--workload", str(workload), "--out",
                        str(args.out / "reference.jsonl"), "--base-url", BASE_URL], check=True)
    finally:
        server.terminate()
        server.wait()

    for name in args.only:
        started = time.monotonic()
        events = args.out / name
        subprocess.run([*capture, "run", "--workload", str(workload), "--mode", "quick",
                        "--out", str(events), "--base-url", BASE_URL, "--gpu", "none",
                        "--gpus", "0", "--start-cmd",
                        shlex.join(server_cmd(*SCENARIOS[name]))], check=True)
        subprocess.run([sys.executable, str(TOKENIZE), "--events", str(events),
                        "--repository", REPOSITORY, "--revision", REVISION], check=True,
                       stderr=subprocess.DEVNULL)
        command = [sys.executable, str(SUITE / "evaluate.py"), "--events", str(events),
                   "--workload", str(workload), "--out", str(args.out / f"{name}.report.json")]
        if name != "clean_a":
            command += ["--baseline", str(args.out / "clean_a")]
        print(f"\n===== {name} ({time.monotonic() - started:.0f} s)")
        subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
