"""Real LLMD on a small CUDA GPU: reference, A/A and a degraded batch size. Debug evidence only."""

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

SUITE = Path(__file__).resolve().parents[2]
TOKENIZE = SUITE / "collect" / "tokenize_events.py"
IMAGE = "zmlai/llmd:20260929.0-cuda"
NAME = "llmd-suite-debug"
PORT = 18000
BASE_URL = f"http://127.0.0.1:{PORT}"
REPOSITORY, REVISION = "Qwen/Qwen3-0.6B", "c1899de289a04d12100db370d81485cdf75e47ca"
SCENARIOS = {"clean_a": [], "clean_b": [], "batch_1": ["--batch-size=1"]}


def debug_plan(out: Path) -> Path:
    plan = json.loads((SUITE / "workload.json").read_text())
    plan["model"].update(role="smoke", revision=REVISION)
    for entry in plan["prompt_sets"].values():
        entry["path"] = str(SUITE / entry["path"])
    plan["reference"]["path"] = str(out / "reference.jsonl")
    plan["cells"]["chat_light"]["arrival"]["rate_rps"] = 2.0
    plan["cells"]["near_capacity"]["arrival"]["rate_rps"] = 1.5
    plan["modes"]["quick"].update(repetitions=3, duration_s=60, launches=3)
    plan["gates"].update(max_anchor_divergence=0.1, min_stable_anchors=10)
    plan["deadlines"]["startup_timeout_s"] = 120
    path = out / "workload.debug.json"
    path.write_text(json.dumps(plan))
    return path


def docker_cmd(model_dir: Path, *extra: str) -> list[str]:
    return ["docker", "run", "--rm", "--name", NAME, "--device", "nvidia.com/gpu=all",
            "--security-opt", "no-new-privileges", "--shm-size", "4g",
            "--env", "LLMD_NO_USAGE_STATS=1",
            "--env", "LLMD_USAGE_STATS_ENDPOINT=http://127.0.0.1:9/",
            "--publish", f"127.0.0.1:{PORT}:8000",
            "--mount", f"type=bind,src={model_dir},dst=/models/model,readonly",
            IMAGE, "--io-impl=zio", "--model=/models/model", *extra]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--only", nargs="*", default=list(SCENARIOS))
    args = parser.parse_args()
    # The debug plan stores these paths, and plan paths resolve against the plan's directory.
    args.out, args.model_dir = args.out.resolve(), args.model_dir.resolve()
    args.out.mkdir(parents=True, exist_ok=True)
    workload = debug_plan(args.out)
    capture = [sys.executable, str(SUITE / "collect" / "capture.py")]
    digest = subprocess.run(["docker", "inspect", IMAGE, "--format", "{{index .RepoDigests 0}}"],
                            check=True, capture_output=True, text=True).stdout.strip()
    if not (args.out / "reference.jsonl").exists():
        server = subprocess.Popen(docker_cmd(args.model_dir), stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
        try:
            subprocess.run([*capture, "reference", "--workload", str(workload), "--out",
                            str(args.out / "reference.jsonl"), "--base-url", BASE_URL], check=True)
        finally:
            subprocess.run(["docker", "stop", "--time", "10", NAME], check=False)
            server.wait()

    for name in args.only:
        events = args.out / name
        subprocess.run([*capture, "run", "--workload", str(workload), "--mode", "quick",
                        "--out", str(events), "--base-url", BASE_URL, "--gpu", "rtx3060",
                        "--image-digest", digest, "--model-repository", REPOSITORY,
                        "--start-cmd", shlex.join(docker_cmd(args.model_dir, *SCENARIOS[name])),
                        "--stop-cmd", f"docker stop --time 10 {NAME}"], check=True)
        subprocess.run([sys.executable, str(TOKENIZE), "--events", str(events),
                        "--repository", REPOSITORY, "--revision", REVISION], check=True,
                       stderr=subprocess.DEVNULL)
        command = [sys.executable, str(SUITE / "evaluate.py"), "--events", str(events),
                   "--workload", str(workload), "--out", str(args.out / f"{name}.report.json")]
        if name != "clean_a":
            command += ["--baseline", str(args.out / "clean_a")]
        print(f"\n===== {name}", flush=True)
        subprocess.run(command, check=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
