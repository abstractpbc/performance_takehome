"""Independent SSE timing/linkage audit; imports no runner, evaluator or metric code.

Reconstructs content, TTFT and startup elapsed time directly from raw JSON payloads.
It is an independent consistency check, not a substitute for correctness gates or
an assertion of hidden model token timing.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from bench.records import Evidence, EvidenceError, load_events

SCOPE = "independent raw SSE TTFT/text/completion and startup-linkage consistency"
LIMITATIONS = "SSE delta timing, not internal token IDs; output correctness remains evaluator-owned"


def audit(evidence: Evidence) -> dict[str, Any]:
    failures = list(evidence.schema_errors)
    if failures:
        return {
            "schema_version": "1.0.0",
            "status": "FAIL",
            "scope": SCOPE,
            "limitations": LIMITATIONS,
            "requests": [],
            "startup": [],
            "failures": failures,
        }
    rows = []
    by_id = {}
    for wrapped in evidence.requests:
        record = wrapped.raw
        rid = record.get("request_id")
        if rid in by_id:
            failures.append(f"duplicate request identity: {rid}")
        by_id[rid] = record
        times = record.get("timestamps_mono_s", {})
        send, end = times.get("send"), times.get("end")
        if (
            not all(
                isinstance(x, int | float) and not isinstance(x, bool) and math.isfinite(x)
                for x in (send, end)
            )
            or end < send
        ):
            failures.append(f"{rid}: invalid request interval")
            continue
        content, reasoning = [], []
        first = None
        previous = send
        done = False
        finish = None
        protocol_errors = 0
        for event in record.get("events", []):
            t = event.get("t")
            if (
                not isinstance(t, int | float)
                or isinstance(t, bool)
                or not math.isfinite(t)
                or not previous <= t <= end
            ):
                failures.append(f"{rid}: raw event outside monotonic request interval")
                continue
            previous = t
            if event.get("raw") == "[DONE]":
                done = True
                continue
            try:
                payload = json.loads(event["raw"])
                if not isinstance(payload, dict):
                    raise ValueError("payload is not an object")
                if "error" in payload:
                    raise ValueError("server error envelope")
                choices = payload.get("choices", [])
                if not isinstance(choices, list) or len(choices) > 1:
                    raise ValueError("expected at most one streaming choice")
                if not choices:
                    continue
                choice = choices[0]
                if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                    raise ValueError("invalid streaming choice object or index")
                delta = choice.get("delta", {})
                if not isinstance(delta, dict):
                    raise ValueError("delta is not an object")
                # Validate the whole chunk before consuming any channel. The
                # recorder discards all generated text from a malformed chunk.
                if any(
                    delta.get(key) is not None and not isinstance(delta[key], str)
                    for key in ("content", "reasoning_content")
                ):
                    raise ValueError("non-text delta")
                for key, target in (("content", content), ("reasoning_content", reasoning)):
                    text = delta.get(key)
                    if text:
                        target.append(text)
                        if key == "content" and first is None:
                            first = t
                if choice.get("finish_reason"):
                    finish = str(choice["finish_reason"])
            except (AttributeError, KeyError, TypeError, ValueError, RecursionError):
                # A malformed server payload is retained as a protocol fault, not
                # converted into a fabricated content or timing observation.
                protocol_errors += 1
        text = record.get("text", {})
        if text.get("content") != "".join(content) or text.get("reasoning_content") != "".join(
            reasoning
        ):
            failures.append(f"{rid}: convenience text disagrees with raw SSE")
        if record.get("done_received") != done or record.get("finish_reason") != finish:
            failures.append(f"{rid}: completion fields disagree with raw SSE")
        first_byte = times.get("first_byte")
        if first is not None and (first_byte is None or first < first_byte):
            failures.append(f"{rid}: first content precedes recorded first byte")
        rows.append(
            {
                "request_id": rid,
                "ttft_s": first - send if first is not None else None,
                "duration_s": end - send,
                "protocol_faults": protocol_errors,
            }
        )
    launches = []
    for launch in evidence.run.startup:
        if launch.get("status") != "valid":
            launches.append(
                {"launch_index": launch.get("launch_index"), "status": launch.get("status")}
            )
            continue
        request = by_id.get(launch.get("first_valid_request_id"))
        if request is None or request.get("phase") != "startup_probe":
            failures.append("startup result has no linked probe")
            continue
        actual = request.get("timestamps_mono_s", {}).get("end")
        if actual != launch.get("first_valid_generation_at"):
            failures.append("startup ends at a value other than the linked completed probe")
            continue
        start = launch.get("launch_mono_s")
        if not isinstance(start, int | float) or not math.isfinite(start) or actual < start:
            failures.append("startup clock interval is invalid")
            continue
        launches.append(
            {
                "launch_index": launch.get("launch_index"),
                "completed_probe_elapsed_s": actual - start,
            }
        )
    return {
        "schema_version": "1.0.0",
        "status": "PASS" if not failures else "FAIL",
        "scope": SCOPE,
        "limitations": LIMITATIONS,
        "requests": rows,
        "startup": launches,
        "failures": failures,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = audit(load_events(args.events))
        with args.out.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, indent=2, allow_nan=False)
            handle.write("\n")
    except (EvidenceError, OSError, ValueError) as exc:
        print(json.dumps({"status": "FAIL", "reason": str(exc)}))
        return 2
    print(json.dumps({"status": result["status"], "out": str(args.out)}))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
