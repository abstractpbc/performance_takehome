"""Private, verified completed-Pi handoff between CPU and GPU phases.

No model/provider process starts here. An artifact contains private transcripts;
its local integrity seal is not a provider signature or a performance result.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import struct
from collections import Counter
from dataclasses import asdict, replace
from decimal import Decimal
from pathlib import Path
from typing import Any

from .configuration_delivery import validate_patch_context
from .proposers import (
    PiProposer,
    Proposal,
    Proposer,
    ProposerContext,
    ProposerError,
    apply_patch,
    unified_diff_for_dirs,
)

DOCKERFILE = Path("tasks/harness-design/Dockerfile")
MAX_BYTES = 64 << 20
COMPATIBILITY_KEYS = {
    "source_commit",
    "harness_tree_sha256",
    "protected_context_sha256",
    "base_image_digest",
    "program_sha256",
    "profile_sha256",
    "benchmark_sha256",
    "baseline_lock_sha256",
    "model_runtime_sha256",
}
ONE_RESPONSE_POLICY = {
    "mode": "one-response-no-tools-v1",
    "stock_Pi": "1.0.0",
    "tools": [],
    "max_remote_calls": 1,
    "output_cap": 2048,
    "response_seconds": 60,
    "model_authored": "final-text-JSON",
    "controller_written_files": True,
}


class ArtifactError(ValueError):
    """A handoff cannot be verified; no GPU or proposal should be started."""


class ReplayArtifactError(ProposerError, ArtifactError):
    """Replay made no request; the loop can retain a known zero new-call cost."""

    def __init__(self, message: str):
        ProposerError.__init__(self, message, api_usd=0.0)


def need(ok: Any, message: str) -> None:
    if not ok:
        raise ArtifactError(message)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def regular(path: Path, maximum: int = MAX_BYTES) -> bytes:
    need(not any(p.is_symlink() for p in (path, *path.parents)), "symlink evidence refused")
    need(path.is_file() and path.stat().st_size <= maximum, "missing/oversized evidence")
    return path.read_bytes()


def tree_digest(root: Path) -> str:
    """Hash exact protected context bytes, excluding Git's host-local database."""
    rows, total = [], 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if ".git" in relative.parts:
            continue
        need(not path.is_symlink(), "context symlink refused")
        if path.is_file():
            raw = regular(path)
            total += len(raw)
            need(total <= 256 << 20, "context exceeds256MiB")
            rows.append([relative.as_posix(), len(raw), digest(raw)])
    return digest(canonical(rows))


def compatibility_binding(
    *,
    context_dir: Path,
    source_commit: str,
    harness_tree_sha256: str,
    base_image_digest: str,
    program_md: str,
    profile: dict[str, Any],
    benchmark: bytes,
    baseline_lock: bytes,
    model_runtime: dict[str, Any],
) -> dict[str, str]:
    """Explicit stable replay contract; historical reports/host paths are separate.

    model_runtime contains engine, model repository/revision/inventory hash and
    container model arguments. It deliberately contains no host mount directory,
    measurement timestamps, fresh outputs or historical decision fields.
    """
    need(
        set(model_runtime)
        == {
            "engine",
            "model_repository",
            "model_revision",
            "model_inventory_sha256",
            "model_args",
        },
        "explicit model identity/arguments contract required",
    )
    result = {
        "source_commit": source_commit,
        "harness_tree_sha256": harness_tree_sha256,
        "protected_context_sha256": tree_digest(context_dir),
        "base_image_digest": base_image_digest,
        "program_sha256": digest(program_md.encode()),
        "profile_sha256": digest(canonical(profile)),
        "benchmark_sha256": digest(benchmark),
        "baseline_lock_sha256": digest(baseline_lock),
        "model_runtime_sha256": digest(canonical(model_runtime)),
    }
    _compatibility(result)
    return result


def _compatibility(value: dict[str, str]) -> None:
    need(set(value) == COMPATIBILITY_KEYS, "complete compatibility contract required")
    need(bool(re.fullmatch(r"[0-9a-f]{40}", value["source_commit"])), "source pin required")
    need(
        bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value["base_image_digest"])),
        "base digest required",
    )
    for key in COMPATIBILITY_KEYS - {"source_commit", "base_image_digest"}:
        need(bool(re.fullmatch(r"[0-9a-f]{64}", value[key])), "invalid compatibility hash")


def _events(raw: bytes) -> list[dict[str, Any]]:
    need(raw.endswith(b"\n"), "truncated transcript")
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    need(all(isinstance(event, dict) for event in events), "invalid transcript record")
    return events


def _completed(attempt: Path, proposal: Proposal) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    status = json.loads(regular(attempt / "attempt-status.json"))
    need(
        status.get("status") == "complete" and status.get("accounting_resolved") is True,
        "Pi proposal was not completed/accounted",
    )
    need(
        status.get("returncode") == 0 and not status.get("timed_out") and not status.get("aborted"),
        "Pi process did not complete normally",
    )
    transcript = attempt / "pi-events.jsonl"
    events = _events(regular(transcript))
    need(PiProposer._error_from_events(transcript) is None, "model error/length response")
    cost = PiProposer._cost_from_events(transcript)
    need(
        math.isclose(cost, proposal.api_usd, rel_tol=1e-12, abs_tol=1e-12), "returned cost mismatch"
    )
    messages = [
        e["message"]
        for e in events
        if e.get("type") == "message_end" and e.get("message", {}).get("role") == "assistant"
    ]
    need(messages and messages[-1].get("stopReason") == "stop", "no successful final assistant")
    ends = [e for e in events if e.get("type") == "agent_end"]
    need(ends and ends[-1].get("willRetry") is False, "agent did not settle")
    starts = {e.get("toolCallId"): e for e in events if e.get("type") == "tool_execution_start"}
    writes = {}
    edited = False
    for event in events:
        if event.get("type") != "tool_execution_end" or event.get("isError") is not False:
            continue
        start = starts.get(event.get("toolCallId"), {})
        args = start.get("args", {})
        target = Path(args.get("path", ""))
        target = target if target.is_absolute() else attempt / "context" / target
        if event.get("toolName") == "write":
            writes[str(target)] = args.get("content")
        if (
            event.get("toolName") in {"edit", "write"}
            and target == attempt / "context" / DOCKERFILE
        ):
            edited = True
    metadata = regular(attempt / "proposal.json", 16384)
    need(
        writes.get(str(attempt / "proposal.json")) == metadata.decode(),
        "actual metadata write missing",
    )
    meta = json.loads(metadata)
    need(
        isinstance(meta, dict)
        and all(
            isinstance(meta.get(k), str) and meta[k].strip()
            for k in ("experiment", "hypothesis", "notes")
        ),
        "invalid proposal metadata",
    )
    need(
        bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", meta["experiment"])),
        "invalid experiment",
    )
    need(
        all(getattr(proposal, k) == meta[k] for k in ("experiment", "hypothesis", "notes")),
        "returned metadata differs from actual Pi write",
    )
    patch_written = writes.get(str(attempt / "proposal.patch")) == proposal.patch
    if (attempt / "proposal.patch").exists():
        need(
            regular(attempt / "proposal.patch").decode() == proposal.patch, "returned patch differs"
        )
        need(
            patch_written or edited,
            "patch has no successful stock write/edit provenance",
        )
    else:
        need(edited, "recovered patch has no successful stock edit")
    return messages, {"status": status, "patch_written_by_stock": patch_written}


def _wal(raw: bytes) -> tuple[bytes, int, str]:
    position, chain, chunks = 0, bytes(32), []
    while position < len(raw):
        need(len(raw) - position >= 36, "truncated private WAL header")
        size = struct.unpack(">I", raw[position : position + 4])[0]
        need(size <= 65536 and len(raw) - position >= 36 + size, "truncated private WAL body")
        claimed = raw[position + 4 : position + 36]
        chunk = raw[position + 36 : position + 36 + size]
        need(hashlib.sha256(chain + chunk).digest() == claimed, "private WAL chain mismatch")
        chain = claimed
        chunks.append(chunk)
        position += 36 + size
    return b"".join(chunks), len(chunks), chain.hex()


def _journal(raw: bytes) -> list[dict[str, Any]]:
    need(raw.endswith(b"\n"), "truncated financial Journal")
    previous, payloads = "0" * 64, []
    for seq, line in enumerate(raw.splitlines()):
        row = json.loads(line)
        need(
            isinstance(row, dict)
            and type(row.get("seq")) is int
            and isinstance(row.get("payload"), dict),
            "financial Journal schema mismatch",
        )
        core = {"seq": seq, "previous": previous, "payload": row["payload"]}
        need(row == {**core, "sha256": digest(canonical(core))}, "financial Journal chain mismatch")
        previous = row["sha256"]
        payloads.append(row["payload"])
    return payloads


def _settlements(
    receipts: list[Path], journal: Path, scope: dict[str, Any], messages: list[dict[str, Any]]
):
    need(
        set(scope) == {"field", "value"} and isinstance(scope["field"], str),
        "request scope required",
    )
    payloads = _journal(regular(journal))
    need(
        set(scope["value"]) == {"path", "sha256"}
        and digest(regular(Path(scope["value"]["path"]))) == scope["value"]["sha256"],
        "actual CPU scope bytes differ",
    )
    tokens, total, request_ids = [], Decimal(0), []
    for path in receipts:
        receipt = json.loads(regular(path))
        identifier = receipt["request_id"]
        need(bool(re.fullmatch(r"api-[0-9a-f]{32}", identifier)), "invalid request ID")
        need(
            path.parent.name == identifier and identifier not in request_ids,
            "duplicate/mismatched request",
        )
        request_ids.append(identifier)
        need(
            receipt.get("status") == "usage_settled"
            and receipt.get("usage_known") is True
            and receipt.get("terminal_done") is True
            and receipt.get("delivery") == "complete",
            "unsettled/partial provider response",
        )
        body, count, chain = _wal(regular(path.with_name("upstream.wal")))
        need(
            receipt["complete_wal_records"] == count
            and receipt["body_bytes"] == len(body)
            and receipt["wal_chain_sha256"] == chain,
            "WAL receipt mismatch",
        )
        data = [line[5:].strip() for line in body.splitlines() if line.startswith(b"data:")]
        need(data and data[-1] == b"[DONE]", "missing terminal SSE")
        frames = [json.loads(line) for line in data[:-1]]
        usages = [frame["usage"] for frame in frames if frame.get("usage")]
        need(usages, "missing provider usage")
        finishes = [
            choice["finish_reason"]
            for frame in frames
            for choice in frame.get("choices", [])
            if choice.get("finish_reason")
        ]
        need(
            finishes and finishes[-1] == receipt["finish_reason"], "provider finish reason differs"
        )
        usage = receipt["usage"]
        need(
            all(
                usages[-1][k] == usage[k]
                for k in ("prompt_tokens", "completion_tokens", "total_tokens")
            ),
            "terminal usage mismatch",
        )
        own = [p for p in payloads if p.get("id") == identifier]
        need(
            Counter(p["kind"] for p in own) == {"request_reserved": 1, "request_usage_settled": 1},
            "request UNKNOWN/duplicate/unsettled Journal event",
        )
        reserved = next(p for p in own if p["kind"] == "request_reserved")
        settled = next(p for p in own if p["kind"] == "request_usage_settled")
        need(reserved.get(scope["field"]) == scope["value"], "wrong CPU request scope")
        need(
            settled["usage"] == usage
            and settled["charge_upper_usd"] == usage["undiscounted_price_usd"],
            "Journal usage differs",
        )
        price = (
            Decimal(usage["prompt_tokens"]) * Decimal("1.40")
            + Decimal(usage["completion_tokens"]) * Decimal("4.40")
        ) / 1000000
        need(price == Decimal(usage["undiscounted_price_usd"]) and price > 0, "invalid usage price")
        total += price
        tokens.append(
            (
                usage["prompt_tokens"],
                usage["completion_tokens"],
                usage["total_tokens"],
                receipt["finish_reason"],
            )
        )
    expected = [
        (
            m["usage"]["input"] + m["usage"]["cacheRead"] + m["usage"]["cacheWrite"],
            m["usage"]["output"],
            m["usage"]["totalTokens"],
            {"toolUse": "tool_calls", "stop": "stop"}.get(m.get("stopReason")),
        )
        for m in messages
    ]
    need(tokens and Counter(tokens) == Counter(expected), "provider/SDK request usage mismatch")
    scoped_ids = {
        p["id"]
        for p in payloads
        if p.get("kind") == "request_reserved" and p.get(scope["field"]) == scope["value"]
    }
    need(scoped_ids == set(request_ids), "unverified additional request in this CPU scope")
    return {"request_ids": request_ids, "undiscounted_upper_USD": str(total), "all_settled": True}


def _write(path: Path, raw: bytes) -> None:
    need(not any(p.is_symlink() for p in (path, *path.parents)), "symlink output refused")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open("xb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if os.name == "posix":
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def seal_completed_pi_proposal(
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
    """Seal a real completed optimization proposal; never infer/build/allocate.

    The caller supplies genuinely observed source/identity pins. The returned
    manifest hash must be retained independently, outside this private artifact.
    Historical report/runtime files are evidence, not fresh-GPU equality keys.
    """
    _compatibility(compatibility)
    need(
        purpose in {"optimization", "test-only-compatibility-fixture"}, "explicit artifact purpose"
    )
    need(
        not output_dir.exists() and not output_dir.resolve().is_relative_to(context_dir.resolve()),
        "new external private artifact directory required",
    )
    need(
        tree_digest(context_dir) == compatibility["protected_context_sha256"],
        "wrong source context",
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
        "complete historical/input provenance required",
    )
    attempt = Path(proposal.transcript_path).parent
    messages, completed = _completed(attempt, proposal)
    need(proposal_context.context_dir.resolve() == context_dir.resolve(), "proposal source differs")
    need(
        digest(proposal_context.program_md.encode()) == compatibility["program_sha256"],
        "actual proposal program differs",
    )
    need(
        digest(canonical(proposal_context.profile)) == compatibility["profile_sha256"],
        "actual proposal profile differs",
    )
    prompt = regular(attempt / "prompt.md")
    need(digest(prompt) == completed["status"].get("prompt_sha256"), "saved prompt hash differs")
    rendered = PiProposer.render_prompt(
        None,
        replace(proposal_context, context_dir=attempt / "context"),
        attempt / "proposal.patch",
        attempt / "proposal.json",
    ).encode()
    need(prompt == rendered, "actual rendered Pi instructions/context differ")
    if not completed["patch_written_by_stock"]:
        need(
            unified_diff_for_dirs(context_dir, attempt / "context") == proposal.patch,
            "recovered diff differs from actual stock edit",
        )
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
    )


def _seal_verified_proposal(
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
    purpose: str,
    messages: list[dict[str, Any]],
    prompt: bytes,
    execution_policy: dict[str, Any] | None = None,
    extra_files: tuple[str, ...] = (),
) -> str:
    """Common immutable output after a mode-specific completed-exchange verifier."""
    attempt = Path(proposal.transcript_path).parent
    financial = _settlements(provider_receipt_paths, journal_path, request_scope, messages)
    profile = json.loads(regular(provenance_files["profile"]))
    if "profiles" in profile:
        profile = profile["profiles"]["harness-design"]
    need(digest(canonical(profile)) == compatibility["profile_sha256"], "profile evidence differs")
    need(
        digest(canonical(json.loads(regular(provenance_files["model_runtime"]))))
        == compatibility["model_runtime_sha256"],
        "stable model runtime evidence differs",
    )
    for key in ("program", "benchmark", "baseline_lock"):
        need(
            digest(regular(provenance_files[key])) == compatibility[key + "_sha256"],
            "input evidence differs: " + key,
        )
    output_dir.mkdir(parents=True, mode=0o700)
    scratch = output_dir / "applied-context"
    shutil.copytree(context_dir, scratch, ignore=shutil.ignore_patterns(".git"))
    try:
        ok, message = apply_patch(proposal.patch, scratch)
        need(ok, "actual Pi patch does not apply: " + message[-300:])
        eligibility = validate_patch_context(context_dir, scratch)
        candidate = regular(scratch / DOCKERFILE)
    finally:
        shutil.rmtree(scratch)
    need(
        tree_digest(context_dir) == compatibility["protected_context_sha256"],
        "original context changed",
    )
    for name in ("attempt-status.json", "pi-events.jsonl", "proposal.json", "prompt.md"):
        _write(output_dir / "attempt" / name, regular(attempt / name))
    for name in extra_files:
        _write(output_dir / "attempt" / name, regular(attempt / name))
    for path in sorted((attempt / "sessions").rglob("*")):
        if path.is_file():
            _write(
                output_dir / "attempt/sessions" / path.relative_to(attempt / "sessions"),
                regular(path),
            )
    _write(output_dir / "proposal.patch", proposal.patch.encode())
    _write(output_dir / "original.Dockerfile", regular(context_dir / DOCKERFILE))
    _write(output_dir / "candidate.Dockerfile", candidate)
    _write(output_dir / "journal.jsonl", regular(journal_path))
    _write(output_dir / "CPU-SCOPE.json", regular(Path(request_scope["value"]["path"])))
    _write(
        output_dir / "proposal-context.json",
        canonical(
            {
                "program_md": proposal_context.program_md,
                "profile": dict(proposal_context.profile),
                "history": list(proposal_context.history),
                "engine": proposal_context.engine,
                "budget_summary": proposal_context.budget_summary,
                "readonly_inputs": dict(proposal_context.readonly_inputs),
                "original_context_dir": str(proposal_context.context_dir),
                "prompt_sha256": digest(prompt),
            }
        )
        + b"\n",
    )
    for name, path in provenance_files.items():
        need(bool(re.fullmatch(r"[a-z][a-z_]{0,63}", name)), "unsafe provenance name")
        _write(output_dir / "inputs" / name, regular(path))
    for receipt in provider_receipt_paths:
        for name in ("receipt.json", "upstream.wal"):
            _write(
                output_dir / "provider" / receipt.parent.name / name,
                regular(receipt.with_name(name)),
            )
    files = {
        p.relative_to(output_dir).as_posix(): {
            "sha256": digest(regular(p)),
            "bytes": p.stat().st_size,
        }
        for p in sorted(output_dir.rglob("*"))
        if p.is_file()
    }
    need(sum(row["bytes"] for row in files.values()) <= MAX_BYTES, "artifact exceeds64MiB")
    manifest = {
        "schema": "completed-stock-pi-artifact-v1",
        "private_transcripts": True,
        "kind": "deferred CPU stock-Pi proposal; no GPU performance claim",
        "purpose": purpose,
        "compatibility": compatibility,
        "proposal": asdict(proposal),
        "original_attempt_path": str(attempt),
        "financial": financial,
        "replay_cost_policy": {
            "original_SDK_estimate_USD": proposal.api_usd,
            "GPU_report_carried_provider_upper_USD": financial["undiscounted_upper_USD"],
            "new_provider_charge_on_replay": False,
            "not_additive_cost_views": True,
        },
        "request_scope": request_scope,
        "eligibility": eligibility,
        "files": files,
    }
    if execution_policy is not None:
        manifest["execution_policy"] = execution_policy
    raw = canonical(manifest) + b"\n"
    _write(output_dir / "manifest.json", raw)
    return digest(raw)


def verify_pi_artifact(root: Path, manifest_sha256: str, expected_compatibility: dict[str, str]):
    """Read-only replay validation. Expected pins come from the current GPU source."""
    raw = regular(root / "manifest.json")
    need(digest(raw) == manifest_sha256, "artifact seal differs")
    manifest = json.loads(raw)
    need(manifest.get("schema") == "completed-stock-pi-artifact-v1", "unknown artifact schema")
    if "execution_policy" in manifest:
        need(manifest["execution_policy"] == ONE_RESPONSE_POLICY, "unknown execution overlay")
    _compatibility(expected_compatibility)
    need(
        manifest["compatibility"] == expected_compatibility, "artifact replay compatibility differs"
    )
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    need(actual == set(manifest["files"]) | {"manifest.json"}, "artifact file inventory differs")
    for name, row in manifest["files"].items():
        relative = Path(name)
        need(not relative.is_absolute() and ".." not in relative.parts, "unsafe artifact path")
        data = regular(root / relative)
        need(len(data) == row["bytes"] and digest(data) == row["sha256"], "artifact bytes differ")
    need(
        regular(root / "proposal.patch").decode() == manifest["proposal"]["patch"],
        "artifact patch differs",
    )
    return manifest


class VerifiedCompletedPiProposal(Proposer):
    """Single-use deferred real-Pi handoff; no model call and no scripted attribution.

    A durable claim is written before returning. An uncertain/interrupted consumer
    must reconcile its existing Harness evidence; it cannot silently replay again.
    The original model cost is returned once, not charged again at the provider.
    """

    name = "verified-completed-stock-pi-deferred"

    def __init__(
        self,
        root: Path,
        *,
        manifest_sha256: str,
        expected_compatibility: dict[str, str],
        claim_path: Path,
        consumer_id: str,
    ):
        need(
            bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", consumer_id)),
            "consumer ID required",
        )
        self.root, self.pin, self.expected = root, manifest_sha256, expected_compatibility
        self.claim, self.consumer = claim_path, consumer_id
        self._exhausted = False
        need(
            not claim_path.resolve().is_relative_to(root.resolve()),
            "claim must be outside sealed artifact",
        )

    def propose(self, context: ProposerContext) -> Proposal:
        try:
            return self._offer(context)
        except (ArtifactError, OSError, ValueError, KeyError, TypeError) as exc:
            self._exhausted = True
            raise ReplayArtifactError(f"deferred Pi artifact refused: {exc}") from exc

    def exhausted(self) -> bool:
        return self._exhausted

    def _offer(self, context: ProposerContext) -> Proposal:
        manifest = verify_pi_artifact(self.root, self.pin, self.expected)
        need(manifest.get("purpose") == "optimization", "test-only artifact cannot enter GPU loop")
        if manifest.get("execution_policy") == ONE_RESPONSE_POLICY:
            self.name = "verified-completed-stock-pi-one-response-no-tools-deferred"
            self.execution_policy = manifest["execution_policy"]
        need(
            tree_digest(context.context_dir) == self.expected["protected_context_sha256"],
            "current context differs",
        )
        need(
            digest(context.program_md.encode()) == self.expected["program_sha256"],
            "current program differs",
        )
        need(
            digest(canonical(context.profile)) == self.expected["profile_sha256"],
            "current profile differs",
        )
        need(
            not self.claim.exists(),
            "artifact already claimed; reconcile previous consumer, do not replay",
        )
        _write(
            self.claim,
            canonical(
                {
                    "artifact_sha256": self.pin,
                    "consumer_id": self.consumer,
                    "status": "offered-once; reconcile Harness before any retry",
                }
            )
            + b"\n",
        )
        fields = dict(manifest["proposal"])
        # Preserve the raw original SDK estimate in the immutable manifest;
        # report the independently settled undiscounted upper once in this run.
        # No gateway reservation, request or Journal mutation occurs on replay.
        fields["api_usd"] = float(manifest["financial"]["undiscounted_upper_USD"])
        fields["transcript_path"] = str(self.root / "attempt/pi-events.jsonl")
        self._exhausted = True
        return Proposal(**fields)
