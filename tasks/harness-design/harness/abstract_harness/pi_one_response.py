"""Declared stock-Pi execution overlay: one JSON response, no active tools.

The official binary is unchanged. The organizer four-tool profile is retained as
reference, not claimed as this execution policy. Controller-created files contain
only the actual model response; no proposed patch or metadata is repaired.
"""

from __future__ import annotations

import copy
import json
import math
import re
import tempfile
from pathlib import Path
from typing import Any

from . import procs
from .pi_artifact import (
    DOCKERFILE,
    ONE_RESPONSE_POLICY,
    _compatibility,
    _events,
    _seal_verified_proposal,
    _wal,
    canonical,
    digest,
    need,
    regular,
    tree_digest,
)
from .pi_preflight import model_config
from .proposers import (
    PiLaunchConfig,
    PiProposer,
    Proposal,
    ProposerAccountingError,
    ProposerContext,
    ProposerError,
)


def one_response_model_config(url: str, model: str, key_env: str) -> dict[str, Any]:
    """Supported official SDK configuration; no tool-choice auto without tools."""
    result = copy.deepcopy(model_config(url, model, key_env))
    for provider in result["providers"].values():
        for selected in provider["models"]:
            selected["maxTokens"] = 2048
            selected["samplingParams"]["tool_choice"] = "none"
    return result


def render_one_response_prompt(context: ProposerContext) -> str:
    """Concise complete evidence, with the original playbook/profile as references."""
    payload = {
        "execution_policy": ONE_RESPONSE_POLICY,
        "instructions": (
            "This declared no-tools JSON overlay replaces the original playbook's "
            "tool/file-operation and output-format steps; its source, evidence, workload "
            "and correctness constraints still apply. "
            "Choose exactly one evidence-based inference configuration experiment. "
            "No tools are active; do not request tools or perform research. "
            "Return exactly one JSON object with experiment, hypothesis, notes, patch. "
            "experiment is a short slug; hypothesis states intended metric/mechanism/risk; "
            "notes acknowledge uncertainty. patch is a complete unified diff, using "
            "a/tasks/harness-design/Dockerfile and b/tasks/harness-design/Dockerfile. "
            "Change only that file, one flag or ENV knob; preserve all other bytes. "
            "Preserve parent image, entrypoint, model/tokenizer/weights, greedy outputs, "
            "port8000, --io-impl=zio and --max-context-len=128000. No benchmark, "
            "accounting, generation-profile or correctness changes. No unmeasured gain claim. "
            "Do not use Markdown fences, commentary outside JSON, placeholders or extra keys. "
            "The controller will apply your exact patch, build and measure it later."
        ),
        "baseline_profile_reference_not_active_tools": dict(context.profile),
        "original_program_reference_sha256": digest(context.program_md.encode()),
        "original_program_reference_text": context.program_md,
        "engine": context.engine,
        "current_Dockerfile": regular(context.context_dir / DOCKERFILE).decode(),
        "actual_readonly_evidence": dict(context.readonly_inputs),
        "actual_attempt_history": list(context.history),
        "budget": context.budget_summary,
    }
    text = canonical(payload).decode()
    need(
        len(text.encode()) <= 65536,
        "one-response prompt exceeds64KiB; compact evidence before launch",
    )
    return text


def parse_response(text: str) -> dict[str, str]:
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "duplicate model JSON key")
            result[key] = value
        return result

    value = json.loads(text, object_pairs_hook=pairs)
    need(
        isinstance(value, dict) and set(value) == {"experiment", "hypothesis", "notes", "patch"},
        "one model JSON object with exactly four proposal keys required",
    )
    need(all(isinstance(v, str) and v.strip() for v in value.values()), "empty/nontext model field")
    need(
        bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value["experiment"])),
        "invalid model slug",
    )
    need(
        len(value["hypothesis"]) <= 2048
        and len(value["notes"]) <= 4096
        and len(value["patch"].encode()) <= 32768,
        "oversized model proposal",
    )
    return value


def _single_final(transcript: Path) -> tuple[str, dict[str, str], list[dict[str, Any]], float]:
    events = _events(regular(transcript))
    cost = PiProposer._cost_from_events(transcript)
    need(
        PiProposer._error_from_events(transcript) is None,
        "model response failed or hit output limit",
    )
    need(not any(e.get("type", "").startswith("tool_execution_") for e in events), "tools executed")
    messages = [
        e["message"]
        for e in events
        if e.get("type") == "message_end" and e.get("message", {}).get("role") == "assistant"
    ]
    need(
        len(messages) == 1 and messages[0].get("stopReason") == "stop",
        "exactly one final SDK response",
    )
    need(messages[0]["usage"].get("output", 2049) <= 2048, "model output exceeded2048")
    contents = messages[0].get("content", [])
    need(contents and all(c.get("type") == "text" for c in contents), "nontext/tool model response")
    ends = [e for e in events if e.get("type") == "agent_end"]
    need(ends and ends[-1].get("willRetry") is False, "agent did not complete")
    text = "".join(c["text"] for c in contents)
    return text, parse_response(text), messages, cost


class OneResponsePiProposer(PiProposer):
    """One genuine model-selected proposal via unmodified Pi --no-tools."""

    name = "stock-pi-one-response-no-tools"
    execution_policy = ONE_RESPONSE_POLICY

    def __init__(self, config: PiLaunchConfig, *, profile, node: Path | None = None):
        need(config.timeout_s == 300, "ordinary overall Pi completion deadline must be300seconds")
        super().__init__(config, profile=profile)
        self.node = node
        self._called = False

    def exhausted(self) -> bool:
        return self._called

    def verify_distribution(self) -> str:
        if self.node is None:
            return super().verify_distribution()
        result = procs.run(
            [str(self.node), str(self.config.pi_bin), "--offline", "--version"],
            timeout_s=15,
            env=self.build_env(),
        )
        need(result.ok and result.stdout.strip() == "1.0.0", "official Pi1.0.0 version required")
        return "1.0.0"

    def build_command(self, prompt_path: Path, session_dir: Path) -> list[str]:
        command = super().build_command(prompt_path, session_dir)
        index = command.index("--tools")
        command[index : index + 2] = ["--no-tools"]
        command.insert(1, "--offline")
        return [str(self.node), *command] if self.node else command

    def propose(self, context: ProposerContext) -> Proposal:
        if self._called:
            raise ProposerError(
                "one-response proposer already used; no second model request", api_usd=0
            )
        self._called = True
        if not self.api_key_present():
            raise ProposerError("one-response gateway client key is not present", api_usd=0)
        attempt = None
        status = {"mode": ONE_RESPONSE_POLICY["mode"]}
        known = None
        launched = False
        try:
            attempt = Path(tempfile.mkdtemp(prefix="pi-attempt-", dir=context.work_dir))
            (attempt / "sessions").mkdir()
            prompt = render_one_response_prompt(context)
            (attempt / "prompt.md").write_text(prompt, encoding="utf-8")
            (attempt / "execution-policy.json").write_bytes(canonical(ONE_RESPONSE_POLICY) + b"\n")
            status = {
                "status": "prepared",
                "mode": ONE_RESPONSE_POLICY["mode"],
                "prompt_sha256": digest(prompt.encode()),
                "timeout_s": 300,
                "controller_written_files": True,
                "command": self.build_command(attempt / "prompt.md", attempt / "sessions"),
            }
            self._write_status(attempt, status)
            timeout = min(300, context.timeout_s or 300)
            if context.deadline_monotonic is not None:
                import time

                timeout = min(timeout, context.deadline_monotonic - time.monotonic())
            if timeout < 75:
                raise ProposerError(
                    "CPU deadline leaves less than75s for provider60s plus completion", api_usd=0
                )
            status["timeout_s"] = timeout
            self._write_status(attempt, status)
            launched = True
            result = procs.run(
                status["command"],
                timeout_s=timeout,
                cwd=context.context_dir,
                env=self.build_env(),
                log_path=attempt / "pi-events.jsonl",
                stderr_path=attempt / "pi-stderr.log",
                should_abort=context.should_abort,
            )
            status.update(
                returncode=result.returncode,
                timed_out=result.timed_out,
                aborted=result.aborted,
                duration_s=result.duration_s,
            )
            self._write_status(attempt, status)
            known = PiProposer._cost_from_events(attempt / "pi-events.jsonl")
            if not result.ok or result.timed_out or result.aborted:
                raise ProposerAccountingError(
                    "one-response process did not complete normally", api_usd=known
                )
            text, value, _, known = _single_final(attempt / "pi-events.jsonl")
            # These files are written by the controller from exact model text,
            # never attributed to stock write/edit tools.
            (attempt / "final-response.txt").write_text(text, encoding="utf-8")
            (attempt / "proposal.patch").write_text(value["patch"], encoding="utf-8")
            (attempt / "proposal.json").write_bytes(
                canonical({k: value[k] for k in ("experiment", "hypothesis", "notes")}) + b"\n"
            )
            proposal = Proposal(
                **value, api_usd=known, transcript_path=str(attempt / "pi-events.jsonl")
            )
        except BaseException as exc:
            known = getattr(exc, "api_usd", known) if launched else 0.0
            status.update(
                status="failed",
                error_type=type(exc).__name__,
                api_usd_known=known,
                accounting_resolved=known is not None
                and not isinstance(exc, ProposerAccountingError),
            )
            if attempt is not None and attempt.is_dir():
                try:
                    self._write_status(attempt, status)
                except OSError:
                    pass  # Keep the original error/cost if the disk cannot retain a receipt.
            if isinstance(exc, ProposerError):
                raise
            if isinstance(exc, (ValueError, OSError, KeyError, TypeError)):
                raise ProposerError(
                    f"one-response proposal rejected: {exc}", api_usd=known
                ) from exc
            raise
        status.update(status="complete", accounting_resolved=True, api_usd_known=known)
        try:
            self._write_status(attempt, status)
        except OSError as exc:
            raise ProposerError(
                "completed model proposal could not be persisted", api_usd=known
            ) from exc
        return proposal


def seal_one_response_pi_proposal(
    proposal: Proposal,
    *,
    proposal_context: ProposerContext,
    context_dir: Path,
    compatibility: dict[str, str],
    provenance_files: dict[str, Path],
    provider_receipt_paths: list[Path],
    journal_path: Path,
    request_scope: dict[str, Any],
    output_dir: Path,
    purpose: str = "optimization",
) -> str:
    """Verify one actual no-tools JSON exchange; preserve the four-tool sealer."""
    _compatibility(compatibility)
    need(
        purpose in {"optimization", "test-only-compatibility-fixture"}, "explicit artifact purpose"
    )
    need(
        not output_dir.exists() and not output_dir.resolve().is_relative_to(context_dir.resolve()),
        "new external artifact directory required",
    )
    need(
        tree_digest(context_dir) == compatibility["protected_context_sha256"],
        "source context differs",
    )
    need(
        {
            "baseline_report",
            "historical_runtime",
            "model_runtime",
            "program",
            "profile",
            "benchmark",
            "baseline_lock",
        }
        <= set(provenance_files),
        "complete provenance required",
    )
    need(
        proposal_context.context_dir.resolve() == context_dir.resolve(), "proposal context differs"
    )
    need(
        digest(proposal_context.program_md.encode()) == compatibility["program_sha256"]
        and digest(canonical(proposal_context.profile)) == compatibility["profile_sha256"],
        "actual baseline program/profile reference differs",
    )
    attempt = Path(proposal.transcript_path).parent
    status = json.loads(regular(attempt / "attempt-status.json"))
    need(
        status.get("status") == "complete"
        and status.get("accounting_resolved") is True
        and status.get("returncode") == 0
        and not status.get("timed_out")
        and not status.get("aborted"),
        "one-response process not completed",
    )
    need(
        status.get("mode") == ONE_RESPONSE_POLICY["mode"]
        and status.get("controller_written_files") is True
        and "--no-tools" in status.get("command", [])
        and "--tools" not in status.get("command", []),
        "undeclared/incorrect execution overlay",
    )
    prompt = regular(attempt / "prompt.md")
    need(
        digest(prompt) == status.get("prompt_sha256")
        and prompt == render_one_response_prompt(proposal_context).encode(),
        "actual one-response prompt differs",
    )
    need(
        json.loads(regular(attempt / "execution-policy.json")) == ONE_RESPONSE_POLICY,
        "execution policy differs",
    )
    text, parsed, messages, cost = _single_final(attempt / "pi-events.jsonl")
    need(math.isclose(cost, proposal.api_usd, rel_tol=1e-12, abs_tol=1e-12), "SDK cost differs")
    need(
        all(getattr(proposal, key) == value for key, value in parsed.items()),
        "returned proposal altered",
    )
    need(regular(attempt / "final-response.txt").decode() == text, "model final text altered")
    need(
        regular(attempt / "proposal.patch").decode() == parsed["patch"], "controller patch altered"
    )
    need(
        json.loads(regular(attempt / "proposal.json"))
        == {k: parsed[k] for k in ("experiment", "hypothesis", "notes")},
        "controller metadata altered",
    )
    need(len(provider_receipt_paths) == 1, "exactly one remote provider request required")
    receipt = provider_receipt_paths[0]
    received = json.loads(regular(receipt))
    need(
        received.get("requested_output_cap") == 2048
        and received.get("timeout_policy", {}).get("response_seconds") == 60,
        "actual provider response/output bounds differ",
    )
    body, _, _ = _wal(regular(receipt.with_name("upstream.wal")))
    frames = [
        json.loads(line[5:].strip())
        for line in body.splitlines()
        if line.startswith(b"data:") and line[5:].strip() != b"[DONE]"
    ]
    content = []
    for frame in frames:
        for choice in frame.get("choices", []):
            delta = choice.get("delta", {})
            need(
                not delta.get("tool_calls")
                and not delta.get("function_call")
                and not delta.get("reasoning_content"),
                "provider returned tools/reasoning",
            )
            if delta.get("content") is not None:
                need(isinstance(delta["content"], str), "nontext provider content")
                content.append(delta["content"])
    need("".join(content) == text, "provider SSE text differs from actual SDK final")
    return _seal_verified_proposal(
        proposal,
        proposal_context=proposal_context,
        context_dir=context_dir,
        compatibility=compatibility,
        provenance_files=provenance_files,
        provider_receipt_paths=provider_receipt_paths,
        journal_path=journal_path,
        request_scope=request_scope,
        output_dir=output_dir,
        purpose=purpose,
        messages=messages,
        prompt=prompt,
        execution_policy=copy.deepcopy(ONE_RESPONSE_POLICY),
        extra_files=("final-response.txt", "execution-policy.json"),
    )
