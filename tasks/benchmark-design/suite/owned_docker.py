"""Keep an attached Docker client's lifetime tied to its exact owned container.

Stopping ``docker run`` can leave a daemon-managed engine serving requests. This
supervisor removes and verifies the owned container before a cold-launch wrapper
exits. It changes lifecycle only; Docker's engine and isolation arguments pass
through unchanged.
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import time

LABEL = "abstract.task1.capture"


def launch_identity(argv: list[str]) -> tuple[str, str, str]:
    if argv[:2] != ["docker", "run"] or "--rm" not in argv or "--pull=never" not in argv:
        raise ValueError("an owned foreground capture launch is required")
    names = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--name"]
    owners = [argv[i + 1].split("=", 1)[1] for i, arg in enumerate(argv[:-1])
              if arg == "--label" and argv[i + 1].startswith(LABEL + "=")]
    images = [arg for arg in argv if re.fullmatch(r"sha256:[0-9a-f]{64}", arg)]
    if (len(names) != 1 or len(owners) != 1 or len(images) != 1
            or not re.fullmatch(r"[0-9a-f]{32}", owners[0])
            or names[0] != "abstract-t1-" + owners[0]):
        raise ValueError("one exact capture name, owner and immutable image are required")
    return names[0], owners[0], images[0]


def exact_container(value: dict, name: str, owner: str, image: str) -> str:
    identifier = value.get("Id", "")
    config = value.get("Config", {})
    if (not re.fullmatch(r"[0-9a-f]{64}", identifier)
            or value.get("Name") != "/" + name
            or (config.get("Labels") or {}).get(LABEL) != owner
            or value.get("Image") != image or config.get("Image") != image):
        raise ValueError("container identity/ownership mismatch; refusing removal")
    return identifier


def remove_owned(name: str, owner: str, image: str, deadline: float) -> None:
    def docker(*args: str) -> subprocess.CompletedProcess:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("owned-container cleanup deadline reached")
        return subprocess.run(["docker", *args], stdin=subprocess.DEVNULL,
                              capture_output=True, text=True, timeout=min(2, remaining))

    def absent(result: subprocess.CompletedProcess) -> bool:
        return result.returncode != 0 and any(
            phrase in result.stderr.lower() for phrase in ("no such object", "no such container")
        )

    inspected = docker("inspect", name)
    if absent(inspected):
        return
    if inspected.returncode != 0:
        raise RuntimeError("container absence could not be established")
    values = json.loads(inspected.stdout)
    if not isinstance(values, list) or len(values) != 1:
        raise ValueError("exactly one inspected container required")
    identifier = exact_container(values[0], name, owner, image)
    if docker("rm", "--force", identifier).returncode != 0:
        raise RuntimeError("owned container removal failed")
    if not absent(docker("inspect", identifier)):
        raise RuntimeError("owned container remains after removal")
    print(json.dumps({"lifecycle_event": "exact_owned_container_removed_and_absent",
                      "container_id": identifier, "name": name, "owner": owner,
                      "image": image, "monotonic_s": time.monotonic()}), flush=True)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if os.name != "posix":
        raise RuntimeError("owned capture supervision requires the Linux GPU host")
    name, owner, image = launch_identity(args)
    stopping = False

    def stop(*unused: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    child = subprocess.Popen(args, stdin=subprocess.DEVNULL, start_new_session=True)
    cleanup_deadline = None
    try:
        while child.poll() is None and not stopping:
            time.sleep(0.05)
    finally:
        cleanup_deadline = time.monotonic() + 8
        # Reap the client first: an in-flight create can make a container appear
        # after an earlier absence check. Then inspect/remove daemon-owned state.
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=1)
        # Complete this before the recorder's next cold repetition. A refused
        # cleanup must never look successful.
        remove_owned(name, owner, image, cleanup_deadline)
    return 0 if stopping else child.returncode


if __name__ == "__main__":
    raise SystemExit(main())
