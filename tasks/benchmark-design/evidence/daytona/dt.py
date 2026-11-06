"""Tiny Daytona driver for the MI355X validation run. State lives next to this file."""

import json
import os
import sys
import time
from pathlib import Path

from daytona import (CreateSandboxFromImageParams, CreateSandboxFromSnapshotParams, CreateSnapshotParams, Daytona, GpuType,
                     Image, Resources)

HERE = Path(__file__).resolve().parent
STATE = HERE / os.environ.get("DT_STATE", "state.json")
SNAPSHOT = os.environ.get("DT_SNAPSHOT", "llmd-suite-mi355x-v3")
LLMD = "docker.io/zmlai/llmd@sha256:0a2cd01a2e53c78934a8cfef66f8668da7666be4954a5d18b72df089c54ea34f"
RATE_USD_H = 6.50 + 16 * 0.0504 + 96 * 0.0162 + 150 * 0.000108  # on-demand GPU, padded over the unofficial $5.99
TTL_MIN = int(os.environ.get("DT_TTL_MIN", 165))
RESOURCES = Resources(cpu=16, memory=96, disk=150, gpu=1, gpu_type=GpuType.MI355X)


def state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(**fields) -> None:
    STATE.write_text(json.dumps({**state(), **fields}, indent=2))


def sandbox(d: Daytona):
    return d.get(state()["sandbox"])


def cost() -> str:
    s = state()
    if "created" not in s:
        return "no sandbox"
    end = s.get("deleted", time.time())
    minutes = (end - s["created"]) / 60
    return f"{minutes:.1f} min, ~${minutes / 60 * RATE_USD_H:.2f} (rate ${RATE_USD_H:.2f}/h, ttl {TTL_MIN} min)"


def main() -> int:
    command, *rest = sys.argv[1:]
    d = Daytona()
    if command == "snapshot":
        image = Image.base("python:3.12-slim-bookworm").dockerfile_commands([
            f"COPY --from={LLMD} /llmd /llmd",
            "RUN pip install --no-cache-dir httpx==0.28.1 huggingface-hub==1.33.0 "
            "tokenizers==0.23.3 hf_xet",
            "RUN mkdir -p /work /models",
        ])
        snap = d.snapshot.create(
            CreateSnapshotParams(name=SNAPSHOT, image=image, resources=RESOURCES,
                                 entrypoint=["sleep", "infinity"]),
            on_logs=lambda line: print(line, flush=True), timeout=3600)
        print(snap.name, snap.state)
    elif command == "up":
        if state().get("sandbox") and "deleted" not in state():
            raise SystemExit("a sandbox already exists: " + state()["sandbox"])
        started = time.time()
        box = d.create(CreateSandboxFromSnapshotParams(
            snapshot=SNAPSHOT, name=os.environ.get("DT_NAME", "llmd-mi355x-validation"), auto_stop_interval=0,
            ttl_minutes=TTL_MIN, spot=False, ephemeral=True, env_vars={"LLMD_NO_USAGE_STATS": "1",
            "LLMD_USAGE_STATS_ENDPOINT": "http://127.0.0.1:9/", "HF_XET_HIGH_PERFORMANCE": "1", "HF_XET_CHUNK_CACHE_SIZE_BYTES": "0"}),
            timeout=1800)
        STATE.write_text(json.dumps({"sandbox": box.id, "created": started}, indent=2))
        print(box.id, box.state, f"created in {time.time() - started:.0f}s")
    elif command == "cpu-up":
        # Same base image as the GPU snapshot, to rehearse the control path for pennies.
        started = time.time()
        box = d.create(CreateSandboxFromImageParams(
            image="python:3.12-slim-bookworm", name="llmd-control-rehearsal",
            resources=Resources(cpu=1, memory=1, disk=3), auto_stop_interval=0,
            ttl_minutes=30, ephemeral=True), timeout=900)
        STATE.write_text(json.dumps({"sandbox": box.id, "created": started}, indent=2))
        print(box.id, box.state, f"created in {time.time() - started:.0f}s")
    elif command == "sh":
        out = sandbox(d).process.exec(rest[0], timeout=int(rest[1]) if len(rest) > 1 else 600)
        print(out.result)
        print(f"[exit {out.exit_code}] {cost()}", file=sys.stderr)
        return out.exit_code
    elif command == "bg":
        # Detached, so it survives the exec call and our disconnects.
        name, script = rest
        line = f"setsid nohup bash -c {json.dumps(script)} > /work/{name}.log 2>&1 < /dev/null &"
        print(sandbox(d).process.exec(line, timeout=60).exit_code)
    elif command == "push":
        sandbox(d).fs.upload_file(rest[0], rest[1])
    elif command == "pull":
        sandbox(d).fs.download_file(rest[0], rest[1])
    elif command == "cost":
        print(cost())
    elif command == "down":
        box = sandbox(d)
        d.delete(box)
        save(deleted=time.time())
        print("deleted", box.id, cost())
    else:
        raise SystemExit(f"unknown command {command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
