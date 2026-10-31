"""CPU-only stock-Pi proposal preflight, inactive unless explicitly executed."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .configuration_delivery import DOCKERFILE, validate_initial_source, validate_patch_context
from .proposers import (
    PI_STOCK_TOOLS,
    PiLaunchConfig,
    PiProposer,
    ProposerAccountingError,
    ProposerContext,
    ProposerError,
    apply_patch,
)

PACKAGE_INTEGRITY = (
    "/FtbxoSQU/mEv1QnichJjRjqteqaIaMWxmhB4G367+MwZfX7/DI5B9YAg5lqbN7nztFskBEtUSZ+FlmMBECtMw=="
)
TIMEOUT_SECONDS = 300
WIRE_OPTIONS = {
    "thinking": {"type": "disabled"},
    "chat_template_kwargs": {"enable_thinking": False},
    "tool_choice": "auto",
}


def stock_distribution(cli: Path, package: Path) -> int:
    """Check every installed official distribution file without modifying the SDK."""
    if (
        base64.b64encode(hashlib.sha512(package.read_bytes()).digest()).decode()
        != PACKAGE_INTEGRITY
    ):
        raise ValueError("Pi archive differs from the pinned official profiles.json integrity")
    root, count = cli.parents[2], 0
    if cli != root / "dist/bundle/cli.js":
        raise ValueError("expected official dist/bundle/cli.js")
    with tarfile.open(package, "r:gz") as packed:
        for member in packed:
            if not member.isfile():
                continue
            relative = Path(member.name.removeprefix("package/"))
            if (
                not member.name.startswith("package/")
                or relative.is_absolute()
                or ".." in relative.parts
            ):
                raise ValueError("unsafe official package path")
            target = root / relative
            if target.is_symlink() or target.resolve() != target or not target.is_file():
                raise ValueError("missing or redirected installed Pi file")
            if (
                hashlib.sha256(target.read_bytes()).digest()
                != hashlib.sha256(packed.extractfile(member).read()).digest()
            ):
                raise ValueError("installed Pi distribution differs: " + str(relative))
            count += 1
    if count != 1243:
        raise ValueError("expected all 1243 official Pi distribution files")
    return count


class ActualCliProposer(PiProposer):
    """Select Node explicitly; retain production stock-tools and isolation flags."""

    def __init__(self, node, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.node = node

    def build_command(self, prompt_path, session_dir):
        return [
            str(self.node),
            str(self.config.pi_bin),
            "--offline",
            *super().build_command(prompt_path, session_dir)[1:],
        ]


def model_config(url, model, key_env):
    """Official Pi1.0 samplingParams sends these fields on every model/tool turn."""
    return {
        "providers": {
            "abstract-preflight": {
                "baseUrl": url,
                "api": "openai-completions",
                "apiKey": "${" + key_env + "}",
                "models": [
                    {
                        "id": model,
                        "reasoning": False,
                        "input": ["text"],
                        "contextWindow": 65536,
                        "maxTokens": 8192,
                        "compat": {"maxTokensField": "max_tokens"},
                        "samplingParams": WIRE_OPTIONS,
                        "cost": {"input": 1.4, "output": 4.4, "cacheRead": 0.21, "cacheWrite": 2.8},
                    }
                ],
            }
        }
    }


class Fixture(ThreadingHTTPServer):
    """Deterministic responses exercise actual CLI tools, followups and recovery."""

    def __init__(self, mode="success"):
        super().__init__(("127.0.0.1", 0), FixtureHandler)
        self.mode, self.requests = mode, []
        self.stall_stop = threading.Event()


class FixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 2 << 20 or len(self.server.requests) >= 8:
            self.send_error(413, "bounded fixture request count/body exceeded")
            return
        body = json.loads(self.rfile.read(length))
        self.server.requests.append({"path": self.path, "body": body})
        stage = sum(m.get("role") == "tool" for m in body["messages"])
        if self.server.mode == "no-patch":
            delta, finish = {"content": "No change."}, "stop"
        elif self.server.mode == "length":
            delta, finish = {"content": "Incomplete response."}, "length"
        elif self.server.mode == "metadata-missing" and stage >= 2:
            delta, finish = {"content": "Finished without metadata."}, "stop"
        elif stage == 0:
            delta, finish = self.tool("read", {"path": DOCKERFILE.as_posix()}), "tool_calls"
        elif stage == 1:
            delta, finish = (
                self.tool(
                    "edit",
                    {
                        "path": DOCKERFILE.as_posix(),
                        "edits": [
                            {
                                "oldText": "--token-batch-size=1024",
                                "newText": "--token-batch-size=256",
                            }
                        ],
                    },
                ),
                "tool_calls",
            )
        elif stage == 2:
            prompt = "\n".join(str(m.get("content", "")) for m in body["messages"])
            metadata = re.search(r"Write JSON to `([^`]+)`", prompt)
            if metadata is None:
                self.send_error(400, "production prompt lacks proposal metadata path")
                return
            delta, finish = (
                self.tool(
                    "write",
                    {
                        "path": metadata[1],
                        "content": "not valid JSON"
                        if self.server.mode == "metadata-malformed"
                        else json.dumps(
                            {
                                "experiment": "cpu-preflight-token-batch-256",
                                "hypothesis": "Interface fixture only; no performance conclusion.",
                                "notes": "Actual stock tools against deterministic HTTP responses.",
                            }
                        ),
                    },
                ),
                "tool_calls",
            )
        else:
            delta, finish = {"content": "Preflight proposal finished."}, "stop"
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Connection", "close")
        self.end_headers()
        self.event(
            {
                "choices": [
                    {"index": 0, "delta": {"role": "assistant", **delta}, "finish_reason": None}
                ]
            }
        )
        if self.server.mode in {"partial", "stall"} and stage >= 3:
            # A real edit without terminal model usage must NOT become a proposal.
            self.wfile.flush()
            if self.server.mode == "stall":
                self.server.stall_stop.wait(120)
            self.close_connection = True
            return
        self.event({"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]})
        self.event(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            }
        )
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    @staticmethod
    def tool(name, arguments):
        return {
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_fixture_" + name,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(arguments),
                    },
                }
            ]
        }

    def event(self, fields):
        value = {
            "id": "fixture",
            "object": "chat.completion.chunk",
            "created": 0,
            "model": "protocol-fixture",
            **fields,
        }
        self.wfile.write(("data: " + json.dumps(value) + "\n\n").encode())


def failed_status(exc):
    if isinstance(exc, OSError | subprocess.SubprocessError):
        return "LAUNCH_FAILED"
    if isinstance(exc, ProposerAccountingError):
        return "ACCOUNTING_UNRESOLVED"
    for needle, status in (
        ("output limit", "OUTPUT_LIMIT"),
        ("timed out", "TIMEOUT"),
        ("no patch", "NO_PATCH"),
        ("metadata", "INVALID_METADATA"),
    ):
        if needle in str(exc):
            return status
    return "INVALID_PROPOSAL_OR_PROVIDER"


def proposal_preflight(
    *,
    node,
    cli,
    package,
    work,
    source,
    gateway_url=None,
    key_env="ABSTRACT_PILOT_GATEWAY_KEY",
    model="glm-5.2",
    fixture_mode="success",
    fixture_timeout_seconds=None,
):
    """Explicit executor. Live calls require an already approved budget gateway."""
    if sys.platform != "linux":
        raise ValueError("Linux required for production patch/process supervision")
    if gateway_url and fixture_timeout_seconds is not None:
        raise ValueError("live proposal deadline is300s; fixture timeout override is not live")
    timeout = TIMEOUT_SECONDS if fixture_timeout_seconds is None else fixture_timeout_seconds
    verified_files = stock_distribution(cli, package)
    if work.exists():
        raise ValueError("fresh private output directory required")
    if gateway_url:
        parsed = urlparse(gateway_url)
        if not (
            parsed.scheme == "http"
            and parsed.hostname == "127.0.0.1"
            and parsed.port
            and parsed.path == "/v1"
            and not parsed.query
            and not parsed.fragment
            and not parsed.username
            and not parsed.password
        ):
            raise ValueError("live URL must be the approved loopback budget gateway /v1")
        if key_env != "ABSTRACT_PILOT_GATEWAY_KEY" or not os.environ.get(key_env):
            raise ValueError("existing gateway client key required; never supply provider key")
    work.mkdir(mode=0o700, parents=True)
    original = work / "original"
    target = original / DOCKERFILE
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    validate_initial_source(original)
    original_sha = hashlib.sha256(target.read_bytes()).hexdigest()
    server, thread, live = None, None, gateway_url is not None
    old_key = os.environ.get("ABSTRACT_PROTOCOL_FIXTURE_KEY")
    if not live:
        server = Fixture(fixture_mode)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        gateway_url = f"http://127.0.0.1:{server.server_port}/v1"
        key_env, model = "ABSTRACT_PROTOCOL_FIXTURE_KEY", "protocol-fixture"
        os.environ[key_env] = "non-secret-protocol-fixture"
    agent = work / "agent"
    agent.mkdir()
    (agent / "models.json").write_text(json.dumps(model_config(gateway_url, model, key_env)))
    (agent / "settings.json").write_text(
        json.dumps(
            {
                "retry": {"enabled": False, "maxRetries": 0, "provider": {"maxRetries": 0}},
                "compaction": {"enabled": False},
                "cacheWarming": "off",
            }
        )
    )
    proposer = ActualCliProposer(
        node,
        PiLaunchConfig(
            pi_bin=cli,
            agent_dir=agent,
            api_key_env=key_env,
            model=model,
            provider="abstract-preflight",
            timeout_s=timeout,
        ),
        profile={"stock_tools": list(PI_STOCK_TOOLS)},
    )
    result = {
        "status": "PREPARED",
        "kind": "live provider proposal preflight"
        if live
        else "deterministic HTTP fixture; no inference",
        "GPU_or_Docker_actions": 0,
        "proposal_deadline_seconds": timeout,
        "verified_stock_Pi_files": verified_files,
        "official_grade": False,
    }
    known_api_usd = None
    try:
        version = subprocess.run(
            [str(node), str(cli), "--version"],
            env=proposer.build_env(),
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        ).stdout.strip()
        node_version = subprocess.run(
            [str(node), "--version"], capture_output=True, text=True, check=True, timeout=15
        ).stdout.strip()
        if version != "1.0.0" or tuple(map(int, node_version.lstrip("v").split("."))) < (22, 19, 0):
            raise ValueError("official Pi1.0.0 and Node>=22.19.0 required")
        result.update(pi_version=version, node_version=node_version)
        proposal = proposer.propose(
            ProposerContext(
                "CPU provider compatibility preflight, NOT an optimization experiment. "
                "Use read to inspect tasks/harness-design/Dockerfile, then use edit to change "
                "only --token-batch-size=1024 to --token-batch-size=256. Complete the requested "
                "proposal metadata using the stock write tool, then stop. "
                "Do not run a build, Docker, benchmark or server.",
                {},
                [],
                original,
                work,
                "llmd",
                "No GPU allocated. At most300s; write files early.",
            )
        )
        known_api_usd = proposal.api_usd
        transcript = Path(proposal.transcript_path)
        events = [
            json.loads(line) for line in transcript.read_text().splitlines() if line.startswith("{")
        ]
        starts = {e.get("toolCallId"): e for e in events if e.get("type") == "tool_execution_start"}
        successes, target_tools, metadata_written = [], [], False
        expected_target = transcript.parent / "context" / DOCKERFILE
        metadata_path = transcript.parent / "proposal.json"
        for event in events:
            if event.get("type") != "tool_execution_end" or event.get("isError") is not False:
                continue
            name = event["toolName"]
            successes.append(name)
            start = starts.get(event.get("toolCallId"), {})
            path = Path(start.get("args", {}).get("path", ""))
            resolved = path if path.is_absolute() else transcript.parent / "context" / path
            if resolved == expected_target:
                target_tools.append(name)
            if name == "write" and resolved == metadata_path:
                metadata_written = True
        if (
            "read" not in target_tools
            or "edit" not in target_tools
            or target_tools.index("read") > target_tools.index("edit")
        ):
            raise ProposerError("preflight requires successful actual read then edit tools")
        if not metadata_written:
            raise ProposerError("preflight requires successful write to expected proposal metadata")
        if (
            metadata_path.is_symlink()
            or not metadata_path.is_file()
            or metadata_path.stat().st_size > 16384
        ):
            raise ProposerError("preflight metadata must be a bounded regular file")
        try:
            metadata = json.loads(metadata_path.read_bytes())
        except (ValueError, UnicodeError) as exc:
            raise ProposerError("preflight metadata must be valid UTF8 JSON") from exc
        if not (
            isinstance(metadata, dict)
            and all(isinstance(metadata.get(k), str) for k in ("experiment", "hypothesis", "notes"))
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", metadata["experiment"])
            and metadata["hypothesis"].strip()
        ):
            raise ProposerError("preflight metadata needs experiment slug, hypothesis and notes")
        candidate = work / "applied-proposal"
        shutil.copytree(original, candidate)
        ok, message = apply_patch(proposal.patch, candidate)
        if not ok:
            raise ProposerError("recovered patch did not apply: " + message)
        eligibility = validate_patch_context(original, candidate)
        expected = target.read_bytes().replace(
            b"--token-batch-size=1024", b"--token-batch-size=256"
        )
        if (candidate / DOCKERFILE).read_bytes() != expected:
            raise ProposerError("preflight changed a different knob or additional bytes")
        if hashlib.sha256(target.read_bytes()).hexdigest() != original_sha:
            raise ProposerError("original context changed")
        (work / "recovered-proposal.patch").write_text(proposal.patch)
        result.update(
            status="PASS",
            successful_stock_tools=successes,
            proposal_recovered=True,
            proposal_metadata_validated=True,
            source_eligibility=eligibility,
            terminal_usage_reconciled=True,
            api_usd_known=proposal.api_usd,
            source_context_unchanged=True,
            transcript_sha256=hashlib.sha256(transcript.read_bytes()).hexdigest(),
        )
    except (ProposerError, ValueError, OSError, subprocess.SubprocessError) as exc:
        result.update(
            status=failed_status(exc),
            proposal_recovered=False,
            error=str(exc),
            api_usd_known=(
                getattr(exc, "api_usd", None)
                if getattr(exc, "api_usd", None) is not None
                else known_api_usd
            ),
            terminal_usage_reconciled=(
                not isinstance(exc, ProposerAccountingError)
                and (getattr(exc, "api_usd", None) is not None or known_api_usd is not None)
            ),
            process_timed_out="pi timed out" in str(exc),
        )
    finally:
        if server:
            server.stall_stop.set()
            server.shutdown()
            thread.join(3)
            server.server_close()
            (work / "fixture-wire-requests.json").write_text(json.dumps(server.requests, indent=2))
            result.update(
                fixture_http_requests=len(server.requests),
                actual_provider_spend_USD=0,
                fixture_wire_capture="fixture-wire-requests.json",
            )
            if old_key is None:
                os.environ.pop("ABSTRACT_PROTOCOL_FIXTURE_KEY", None)
            else:
                os.environ["ABSTRACT_PROTOCOL_FIXTURE_KEY"] = old_key
        (work / "verification.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--execute-fixture", action="store_true")
    mode.add_argument("--execute-live", action="store_true")
    for name in ("node", "pi-cli", "pi-package", "out"):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--gateway-url")
    parser.add_argument(
        "--fixture-mode", choices=("success", "length", "no-patch", "partial"), default="success"
    )
    args = parser.parse_args()
    if not (args.execute_fixture or args.execute_live):
        print(json.dumps({"inactive": True, "model_GPU_or_subprocess_actions": 0}))
        return 0
    if not all((args.node, args.pi_cli, args.pi_package, args.out)):
        parser.error("execution requires --node --pi-cli --pi-package --out")
    if bool(args.gateway_url) != args.execute_live:
        parser.error("only explicit --execute-live accepts/requires --gateway-url")
    here = Path(__file__).resolve().parents[1]
    result = proposal_preflight(
        node=args.node.resolve(strict=True),
        cli=args.pi_cli.resolve(strict=True),
        package=args.pi_package.resolve(strict=True),
        work=args.out.resolve(),
        source=here.parent / "Dockerfile",
        gateway_url=args.gateway_url,
        fixture_mode=args.fixture_mode,
    )
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "PASS" else 1
