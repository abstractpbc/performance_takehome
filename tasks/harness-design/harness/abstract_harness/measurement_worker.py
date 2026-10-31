"""Private CPU-only measurement subprocess, bounded and terminated by the parent loop."""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import asdict
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path[:0] = [str(HERE.parents[1]), str(HERE.parents[4])]

from abstract_harness import measure  # noqa: E402


def record_trace(event: str, trace: measure.RequestTrace) -> None:
    """Persist completed requests before the next request can start.

    The parent captures stderr in a regular file. A pipe remains usable for
    standalone invocations, where fsync is unsupported. Request timing has
    already ended before this journal write.
    """
    print(
        json.dumps({"event": event, "trace": asdict(trace)}), file=sys.stderr, flush=True
    )
    if stat.S_ISREG(os.fstat(sys.stderr.fileno()).st_mode):
        os.fsync(sys.stderr.fileno())


def record_probe(trace: measure.RequestTrace) -> None:
    record_trace("startup_probe", trace)


def record_benchmark_trace(trace: measure.RequestTrace) -> None:
    record_trace("benchmark_request", trace)


def main() -> int:
    payload = json.load(sys.stdin)
    operation = payload.pop("operation")
    if operation == "startup":
        result = measure.wait_for_first_valid_generation(**payload, on_probe=record_probe)
    elif operation == "benchmark":
        result = measure.run_benchmark(**payload, on_trace=record_benchmark_trace).to_json()
    else:
        raise ValueError(f"unknown measurement operation {operation!r}")
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
