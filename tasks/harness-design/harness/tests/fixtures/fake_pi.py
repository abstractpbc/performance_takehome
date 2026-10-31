#!/usr/bin/env python3
"""CANDIDATE-BUILT test double for the pinned Pi 1.0.0 CLI in ``--print --mode json`` mode.

It emits the line protocol of ``@earendil-works/pi-coding-agent@1.0.0`` faithfully for
every field ``PiProposer`` consumes (``agent_end``/``turn_end``/``message_start`` payloads
are abbreviated):

* ``dist/cli/args.js`` L134 (``--print``), L224 (``@file`` prompt arguments),
  L47 (``--mode`` must be text|json|rpc);
* ``dist/modes/print-mode.js`` L97-101 write the ``SessionHeader`` as the first
  stdout line, then L85-87 write ``JSON.stringify(toJsonEvent(event))`` per
  session event;
* ``dist/modes/json-event.js`` rewrites ``message_update`` events to
  ``{type, usage, assistantMessageEvent}`` (the ``partial`` message is dropped);
* ``dist/core/session-manager.d.ts`` L5-12 define ``SessionHeader``;
* ``node_modules/@earendil-works/pi-agent-core/dist/types.d.ts`` L451-483 list
  the event types (agent_start, turn_start, message_start, message_update,
  message_end, tool_execution_start/update/end, turn_end, agent_end);
* ``node_modules/@earendil-works/pi-ai/dist/types.d.ts`` L289-309 define
  ``usage`` (input, output, cacheRead, cacheWrite, totalTokens, cost{input,
  output, cacheRead, cacheWrite, total}) and L310 ``StopReason`` / L388
  ``errorMessage`` on the assistant message.

Behaviour is selected with ``FAKE_PI_MODE``: ``ok`` (default), ``error``
(assistant message ends with stopReason=error, no patch, exit 1), ``malformed``
(ok plus two non-JSON stdout lines), ``exit`` (dies with exit 2 after the
header), ``hang`` (prints the header and sleeps), ``nopatch`` (ok events, no
edit). No network, no model call, no reading of API keys.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import sys
import time
from pathlib import Path

VERSION = "1.0.0"
REQUIRED_FLAGS = (
    "--print",
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--no-themes",
    "--no-context-files",
    "--no-approve",
)


def _usage(inp: int, out: int, total_cost: float) -> dict:
    return {
        "input": inp,
        "output": out,
        "cacheRead": 0,
        "cacheWrite": 0,
        "totalTokens": inp + out,
        "cost": {
            "input": round(total_cost * 0.75, 6),
            "output": round(total_cost * 0.25, 6),
            "cacheRead": 0.0,
            "cacheWrite": 0.0,
            "total": total_cost,
        },
    }


def _emit(obj: object) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _emit_raw(text: str) -> None:
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def main(argv: list[str]) -> int:
    if argv == ["--version"]:
        print(VERSION)
        return 0
    if "--mode" not in argv or argv[argv.index("--mode") + 1] != "json":
        sys.stderr.write("fake pi: expected --mode json\n")
        return 64
    missing = [flag for flag in REQUIRED_FLAGS if flag not in argv]
    if missing or argv[argv.index("--tools") + 1] != "read,bash,edit,write":
        sys.stderr.write(f"fake pi: unexpected invocation {argv!r}\n")
        return 64
    prompt_files = [a[1:] for a in argv if a.startswith("@")]
    if len(prompt_files) != 1:
        sys.stderr.write("fake pi: expected exactly one @prompt file\n")
        return 64
    prompt = Path(prompt_files[0]).read_text(encoding="utf-8")
    patch_out = re.search(r"applicable with `patch -p1`\) to `([^`]+)`", prompt)
    meta_out = re.search(r"Write JSON to `([^`]+)`", prompt)
    if not patch_out or not meta_out:
        sys.stderr.write("fake pi: prompt does not name the patch/meta outputs\n")
        return 65
    mode = os.environ.get("FAKE_PI_MODE", "ok")
    cwd = Path.cwd()

    _emit(
        {
            "type": "session",
            "version": 3,
            "id": "fake-session-0001",
            "timestamp": "2026-10-24T00:00:00.000Z",
            "cwd": str(cwd),
        }
    )
    if mode == "hang":
        time.sleep(600)
        return 0
    _emit({"type": "agent_start"})
    if mode == "exit":
        return 2
    _emit({"type": "turn_start"})
    _emit({"type": "message_start", "message": {"role": "assistant", "content": []}})
    if mode == "malformed":
        _emit_raw("not json at all {")
    _emit(
        {
            "type": "message_update",
            "usage": _usage(1200, 20, 0.0030),
            "assistantMessageEvent": {"type": "text_delta", "contentIndex": 0, "delta": "Plan: "},
        }
    )
    _emit(
        {
            "type": "message_update",
            "usage": _usage(1200, 60, 0.0048),
            "assistantMessageEvent": {
                "type": "text_delta",
                "contentIndex": 0,
                "delta": "flip GGML_LTO",
            },
        }
    )
    if mode == "malformed":
        _emit_raw('{"type": "message_update", "usage": {"cost": {"total": 99.0')
    if mode == "error":
        _emit(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [],
                    "stopReason": "error",
                    "errorMessage": "402 insufficient_quota: billing hard limit reached",
                    "usage": _usage(1200, 0, 0.0),
                },
            }
        )
        _emit({"type": "turn_end"})
        _emit({"type": "agent_end"})
        return 1
    _emit(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Plan: flip GGML_LTO"}],
                "stopReason": "toolUse",
                "usage": _usage(1200, 80, 0.0048),
            },
        }
    )
    _emit(
        {
            "type": "tool_execution_start",
            "toolCallId": "call_1",
            "toolName": "edit",
            "args": {"path": "Dockerfile"},
        }
    )
    if mode == "binary":
        (cwd / "blob.bin").write_bytes(b"\x00\x01binary")
    elif mode != "nopatch":
        _apply_edit(cwd, Path(patch_out.group(1)), Path(meta_out.group(1)))
    _emit(
        {
            "type": "tool_execution_end",
            "toolCallId": "call_1",
            "toolName": "edit",
            "result": {"content": [{"type": "text", "text": "ok"}]},
            "isError": False,
        }
    )
    _emit(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Done."}],
                "stopReason": "stop",
                "usage": _usage(1400, 30, 0.0075),
            },
        }
    )
    _emit({"type": "turn_end"})
    _emit({"type": "agent_end"})
    return 0


def _apply_edit(cwd: Path, patch_out: Path, meta_out: Path) -> None:
    target = cwd / "Dockerfile"
    before = target.read_text(encoding="utf-8").splitlines(keepends=True)
    after = []
    for line in before:
        if line.startswith("ARG GGML_LTO="):
            value = "OFF" if line.strip().endswith("ON") else "ON"
            line = f"ARG GGML_LTO={value}\n"
        after.append(line)
    if after == before:
        after.append("# fake-pi: no-op marker\n")
    target.write_text("".join(after), encoding="utf-8")
    diff = difflib.unified_diff(before, after, fromfile="a/Dockerfile", tofile="b/Dockerfile")
    patch_out.write_text("".join(diff), encoding="utf-8")
    meta_out.write_text(
        json.dumps(
            {
                "experiment": "fake-pi-lto-toggle",
                "hypothesis": "LTO lets the compiler inline across ggml units; FTGR up.",
                "notes": "emitted by tests/fixtures/fake_pi.py",
            }
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
