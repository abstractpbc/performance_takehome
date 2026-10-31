"""Proposers: produce one experiment (hypothesis + unified-diff patch) per attempt.

* :class:`ScriptedProposer` - deterministic queue read from a JSON file; used
  for tests, for the stand-in evidence run, and whenever no coding-model key is
  present. Its patches are real unified diffs generated with ``difflib``.
* :class:`PiProposer` - launches the pinned Pi 1.0.0 coding agent
  (``@earendil-works/pi-coding-agent@1.0.0``) non-interactively (``--print``)
  in an isolated, task-scoped agent directory with exactly the tools and
  isolation flags from ``agent-harness/profiles.json`` / ``agent-harness/README.md``
  and extracts the single patch it writes.

API keys are only ever read from environment variables and are never written
to any log, report, or transcript produced here.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from . import procs

PI_PACKAGE = "@earendil-works/pi-coding-agent"
PI_VERSION = "1.0.0"
PI_STOCK_TOOLS: tuple[str, ...] = ("read", "bash", "edit", "write")
# Flags exactly as listed in agent-harness/README.md. Note that in Pi 1.0.0
# ``--no-approve`` means "ignore project-local files" (dist/cli/args.js L219,
# ``projectTrustOverride``), not "skip approval prompts"; print mode never asks.
PI_ISOLATION_FLAGS: tuple[str, ...] = (
    "--no-extensions",
    "--no-skills",
    "--no-prompt-templates",
    "--no-themes",
    "--no-context-files",
    "--no-approve",
)
SECRET_ENV_PATTERN = re.compile(r"(API_KEY|TOKEN|SECRET|PASSWORD)", re.IGNORECASE)


class ProposerError(RuntimeError):
    """A proposer could not produce a usable proposal.

    ``api_usd`` carries any model spend that happened before the failure so the
    loop can still account for it.
    """

    def __init__(self, message: str, *, api_usd: float | None = None) -> None:
        super().__init__(message)
        self.api_usd = api_usd


class ProposerAccountingError(ProposerError):
    """Model usage cannot be reconciled; stop instead of making another paid proposal.

    ``api_usd`` is only the known subtotal, not a claim that unknown usage was free.
    """


@dataclass(frozen=True)
class Proposal:
    """One experiment to try: a hypothesis and a unified diff against the build context."""

    experiment: str
    hypothesis: str
    patch: str
    notes: str = ""
    api_usd: float = 0.0
    transcript_path: str = ""


@dataclass
class ProposerContext:
    """What a proposer may read to decide on the next experiment."""

    program_md: str
    profile: Mapping[str, Any]
    history: Sequence[Mapping[str, Any]]
    context_dir: Path
    work_dir: Path
    engine: str
    budget_summary: str = ""
    timeout_s: float | None = None
    should_abort: Callable[[], bool] | None = None
    deadline_monotonic: float | None = None
    readonly_inputs: Mapping[str, str] = field(default_factory=dict)


class Proposer:
    """Base class."""

    name = "base"

    def exhausted(self) -> bool:
        """Whether this source has definitively finished; no further call is needed."""
        return False

    def propose(self, context: ProposerContext) -> Proposal | None:
        """Return the next proposal or ``None`` when the proposer has nothing left."""
        raise NotImplementedError

    def estimated_usd_per_call(self) -> float:
        """Upper estimate of model spend for one proposal (0 for offline proposers)."""
        return 0.0


def unified_diff_for_dirs(old_dir: Path, new_dir: Path) -> str:
    """Unified diff (``a/`` / ``b/`` prefixes, applies with ``patch -p1``) between two trees."""
    chunks: list[str] = []
    files = sorted(
        {
            p.relative_to(old_dir)
            for p in old_dir.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(old_dir).parts
        }
        | {
            p.relative_to(new_dir)
            for p in new_dir.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(new_dir).parts
        }
    )
    for rel in files:
        old_path, new_path = old_dir / rel, new_dir / rel
        old_bytes = old_path.read_bytes() if old_path.exists() else b""
        new_bytes = new_path.read_bytes() if new_path.exists() else b""
        if old_bytes == new_bytes:
            continue
        if b"\0" in old_bytes or b"\0" in new_bytes:
            raise ProposerError(f"binary changes are unsupported in a text patch: {rel}")
        try:
            old_lines = old_bytes.decode("utf-8").splitlines(keepends=True)
            new_lines = new_bytes.decode("utf-8").splitlines(keepends=True)
        except UnicodeDecodeError as exc:
            raise ProposerError(f"binary changes are unsupported in a text patch: {rel}") from exc
        diff = difflib.unified_diff(
            old_lines, new_lines, fromfile=f"a/{rel.as_posix()}", tofile=f"b/{rel.as_posix()}"
        )
        chunks.append("".join(diff))
    return "".join(chunks)


def contained_path(root: Path, relative: str) -> Path:
    """Resolve ``relative`` inside ``root`` or raise :class:`ProposerError`.

    Rejects absolute paths, ``..`` components, and anything that resolves (via
    symlinks) outside ``root``; the returned path is the *unresolved* child so
    callers can still refuse symlinks explicitly.
    """
    candidate = Path(relative)
    if candidate.is_absolute() or ".." in candidate.parts or not relative:
        raise ProposerError(f"path {relative!r} must be relative and inside the build context")
    target = root / candidate
    root_resolved = root.resolve()
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError) as exc:
        raise ProposerError(f"cannot resolve {relative!r}: {exc}") from exc
    if not resolved.is_relative_to(root_resolved):
        raise ProposerError(f"path {relative!r} escapes the build context")
    if target.is_symlink() or any(
        p.is_symlink() for p in target.parents if p != root and p.is_relative_to(root)
    ):
        raise ProposerError(f"path {relative!r} goes through a symlink")
    return target


def escaping_symlinks(root: Path) -> list[str]:
    """Symlinks under ``root`` that resolve outside it (or dangle); empty when clean."""
    root_resolved = root.resolve()
    found: list[str] = []
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        try:
            if not path.resolve(strict=True).is_relative_to(root_resolved):
                found.append(path.relative_to(root).as_posix())
        except (OSError, RuntimeError):
            found.append(path.relative_to(root).as_posix())
    return found


def apply_patch(patch_text: str, target_dir: Path, *, timeout_s: float = 60) -> tuple[bool, str]:
    """Apply a unified diff to ``target_dir`` with ``patch -p1`` (scratch copies only).

    Patches that try to escape the directory (``..`` or absolute paths) or that
    contain no hunks are rejected before anything is applied.
    """
    if not patch_text.strip():
        return False, "empty patch"
    for line in patch_text.splitlines():
        if line.startswith(("+++ ", "--- ")):
            path = line[4:].split("\t")[0].strip()
            if path == "/dev/null":
                continue
            stripped = path.split("/", 1)[1] if "/" in path else path
            if path.startswith("/") or ".." in Path(stripped).parts:
                return False, f"patch touches a path outside the build context: {path}"
    if shutil.which("patch") is None:
        return False, "`patch` binary not available"
    links_before = _symlinks(target_dir)
    result = procs.run(
        ["patch", "-p1", "--batch", "--forward", "--no-backup-if-mismatch", "-d", str(target_dir)],
        timeout_s=timeout_s,
        stdin_text=patch_text,
    )
    if result.ok:
        bad = escaping_symlinks(target_dir)
        if bad:
            return False, f"patch created symlinks escaping the build context: {bad}"
        created = sorted(_symlinks(target_dir) - links_before)
        if created:
            return False, f"patch created symlinks inside the build context: {created}"
    output = (result.stdout + result.stderr).strip()
    if result.timed_out:
        return False, "patch timed out"
    return result.ok, output


def _symlinks(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_symlink()}


def _slug(text: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "-", text).strip("-")[:64] or "experiment"


class ScriptedProposer(Proposer):
    """Deterministic queue of experiments loaded from a JSON file.

    Each entry: ``{"experiment": str, "hypothesis": str, "edits": [{"file": str,
    "find": str, "replace": str}], "notes": str}`` or a literal ``"patch"``.
    Edits are applied to a scratch copy of the current build context to render a
    real unified diff, so the stored patch is exactly what gets built.
    """

    name = "scripted"

    @staticmethod
    def validate_queue(queue: Sequence[Mapping[str, Any]]) -> None:
        """Schema-check queue entries up front so a typo fails before any build."""
        seen: set[str] = set()
        for n, entry in enumerate(queue):
            if not isinstance(entry, Mapping) or not isinstance(entry.get("experiment"), str):
                raise ProposerError(f"queue entry {n}: needs a string 'experiment'")
            if entry["experiment"] in seen:
                raise ProposerError(
                    f"queue entry {n}: duplicate experiment {entry['experiment']!r}"
                )
            seen.add(entry["experiment"])
            if "patch" in entry:
                if not isinstance(entry["patch"], str):
                    raise ProposerError(f"queue entry {n}: 'patch' must be a string")
                continue
            edits = entry.get("edits")
            if not isinstance(edits, list) or not edits:
                raise ProposerError(f"queue entry {n}: needs 'patch' or a nonempty 'edits' list")
            for m, edit in enumerate(edits):
                if not isinstance(edit, Mapping) or not all(
                    isinstance(edit.get(k), str) for k in ("file", "find", "replace")
                ):
                    raise ProposerError(
                        f"queue entry {n} edit {m}: needs string 'file', 'find' and 'replace'"
                    )

    def __init__(self, queue: Sequence[Mapping[str, Any]], *, skip: int = 0) -> None:
        self.validate_queue(queue)
        self._queue = list(queue)
        self._index = skip

    @classmethod
    def from_file(cls, path: Path, *, skip: int = 0) -> ScriptedProposer:
        """Load the queue from a JSON list (or ``{"experiments": [...]}``)."""
        data = json.loads(path.read_text(encoding="utf-8"))
        queue = data["experiments"] if isinstance(data, dict) else data
        return cls(queue, skip=skip)

    @property
    def remaining(self) -> int:
        """Experiments left in the queue."""
        return max(0, len(self._queue) - self._index)

    def resume_from_attempt_index(self, attempt_index: int) -> None:
        """Align only after recovery: attempt 1 consumes queue item 0 after baseline."""
        self._index = max(self._index, max(0, attempt_index - 1))

    def propose(self, context: ProposerContext) -> Proposal | None:
        """Pop the next experiment and render its patch against ``context.context_dir``."""
        if self._index >= len(self._queue):
            return None
        entry = self._queue[self._index]
        self._index += 1
        experiment = _slug(str(entry["experiment"]))
        hypothesis = str(entry.get("hypothesis", ""))
        notes = str(entry.get("notes", ""))
        if "patch" in entry:
            return Proposal(experiment, hypothesis, str(entry["patch"]), notes)
        edits = entry.get("edits") or []
        if not edits:
            raise ProposerError(f"experiment {experiment} has neither patch nor edits")
        bad_links = escaping_symlinks(context.context_dir)
        if bad_links:
            raise ProposerError(f"build context has symlinks escaping it: {bad_links}")
        with tempfile.TemporaryDirectory(prefix="scripted-", dir=context.work_dir) as tmp:
            new_dir = Path(tmp) / "new"
            shutil.copytree(
                context.context_dir, new_dir, symlinks=True, ignore=shutil.ignore_patterns(".git")
            )
            for edit in edits:
                try:
                    target = contained_path(new_dir, str(edit["file"]))
                except ProposerError as exc:
                    raise ProposerError(f"experiment {experiment}: {exc}") from exc
                if not target.is_file():
                    raise ProposerError(
                        f"experiment {experiment}: edit target {edit['file']!r} is not a file"
                    )
                text = target.read_text(encoding="utf-8")
                if edit.get("regex"):
                    pattern = re.compile(edit["find"])
                    found = pattern.search(text) is not None
                    patched = pattern.sub(edit["replace"].replace("\\", "\\\\"), text, count=1)
                else:
                    found = edit["find"] in text
                    patched = text.replace(edit["find"], edit["replace"], 1)
                if not found:
                    raise ProposerError(
                        f"experiment {experiment}: find-string not present in {edit['file']}: "
                        f"{edit['find']!r}"
                    )
                target.write_text(patched, encoding="utf-8")
            patch = unified_diff_for_dirs(context.context_dir, new_dir)
        if not patch.strip():
            raise ProposerError(f"experiment {experiment}: edits produced an empty patch")
        return Proposal(experiment, hypothesis, patch, notes)


@dataclass
class PiLaunchConfig:
    """How to launch the pinned Pi distribution."""

    pi_bin: Path
    agent_dir: Path
    api_key_env: str
    model: str | None = None
    provider: str | None = None
    extra_env: dict[str, str] = field(default_factory=dict)
    timeout_s: float = 900.0
    usd_per_call_estimate: float = 0.0
    provider_response_dir: Path | None = None


def pi_version(pi_bin: Path, *, timeout_s: float = 60) -> str:
    """Return ``pi --version`` output (stripped)."""
    result = procs.run([str(pi_bin), "--version"], timeout_s=timeout_s, env=scrubbed_env({}))
    if not result.ok:
        raise ProposerError(
            f"pi --version failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip()


def scrubbed_env(extra: Mapping[str, str], *, keep: Sequence[str] = ()) -> dict[str, str]:
    """Minimal child environment: PATH/HOME/LANG plus explicit extras; personal secrets dropped.

    Only variables named in ``keep`` survive the secret filter, so an API key
    reaches Pi only when the harness was configured to pass that exact name.
    """
    base = {
        k: v
        for k, v in os.environ.items()
        if k in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "TERM"}
    }
    for key in keep:
        if key in os.environ:
            base[key] = os.environ[key]
    for key, value in extra.items():
        base[key] = value
    return base


class PiProposer(Proposer):
    """Launch Pi 1.0.0 once per attempt and extract the single patch it writes."""

    name = "pi"

    def __init__(self, config: PiLaunchConfig, *, profile: Mapping[str, Any]) -> None:
        self.config = config
        self.profile = profile
        self._max_observed_usd = 0.0
        tools = tuple(profile.get("stock_tools", PI_STOCK_TOOLS))
        if tools != PI_STOCK_TOOLS:
            raise ProposerError(
                f"profile tools {tools} differ from the pinned stock tools {PI_STOCK_TOOLS}"
            )

    def estimated_usd_per_call(self) -> float:
        """Configured per-call estimate, raised to the largest spend observed so far."""
        return max(self.config.usd_per_call_estimate, self._max_observed_usd)

    def verify_distribution(self) -> str:
        """Check ``pi --version`` matches the pinned version; return the version string."""
        version = pi_version(self.config.pi_bin)
        if version.split()[-1] != PI_VERSION:
            raise ProposerError(f"pi reports version {version!r}, expected {PI_VERSION}")
        return version

    def build_command(self, prompt_path: Path, session_dir: Path) -> list[str]:
        """The exact Pi invocation: print mode, JSON events, stock tools, isolation flags."""
        cmd = [
            str(self.config.pi_bin),
            "--print",
            "--mode",
            "json",
            "--tools",
            ",".join(PI_STOCK_TOOLS),
            *PI_ISOLATION_FLAGS,
            "--session-dir",
            str(session_dir),
        ]
        if self.config.provider:
            cmd += ["--provider", self.config.provider]
        if self.config.model:
            cmd += ["--model", self.config.model]
        cmd += ["@" + str(prompt_path)]
        return cmd

    def build_env(self) -> dict[str, str]:
        """Isolated env: task-scoped agent dir, telemetry off, key passed through by name only."""
        env = scrubbed_env(
            {
                "PI_CODING_AGENT_DIR": str(self.config.agent_dir),
                "PI_SKIP_VERSION_CHECK": "1",
                "PI_TELEMETRY": "0",
                # Pi offline mode freezes startup catalog/extension network work;
                # it still permits requests to the configured coding-model provider.
                "PI_OFFLINE": "1",
                **self.config.extra_env,
            },
            keep=(self.config.api_key_env,),
        )
        return env

    def api_key_present(self) -> bool:
        """Whether the configured key variable is set (value is never read into reports)."""
        return bool(os.environ.get(self.config.api_key_env))

    def render_prompt(self, context: ProposerContext, patch_out: Path, meta_out: Path) -> str:
        """Compose the agent instructions from program.md, the profile, and history."""
        history_lines = [
            f"- {h.get('experiment')}: outcome={h.get('outcome')} kept={h.get('kept')} "
            f"ttft_speedup={h.get('local_ttft_speedup', '')} "
            f"ftgr_speedup={h.get('local_ftgr_speedup', '')} "
            f"hypothesis={h.get('hypothesis', '')!r} notes={h.get('notes', '')!r}"
            for h in context.history
        ] or ["- (no attempts yet)"]
        return "\n".join(
            [
                context.program_md.strip(),
                "",
                "## Pi profile (organizer contract)",
                "```json",
                json.dumps(dict(context.profile), indent=2, sort_keys=True),
                "```",
                "",
                "## Engine under optimization",
                f"`{context.engine}`. Build context (the ONLY files you may change): "
                f"`{context.context_dir}`",
                "",
                "## Current inputs supplied by the controller (read-only)",
                *[
                    f"### {name}\n```\n{content.rstrip()}\n```"
                    for name, content in context.readonly_inputs.items()
                ],
                "",
                "## Budget",
                context.budget_summary or "(not provided)",
                "",
                "## History of attempts so far (oldest first)",
                *history_lines,
                "",
                "## What you must output",
                "1. Pick exactly ONE experiment (one change), guided by the supplied workload "
                "and history.",
                f"2. Edit files inside `{context.context_dir}` only. Do not run docker, "
                "do not start servers, do not touch anything else.",
                "3. Write a unified diff of your change (as produced by "
                "`diff -ruN original patched`, "
                f"applicable with `patch -p1`) to `{patch_out}`.",
                f"4. Write JSON to `{meta_out}` with keys `experiment` (short slug), "
                "`hypothesis` (one sentence: what metric improves and why), `notes`.",
                "5. Then stop. Do not print the patch to stdout.",
            ]
        )

    def propose(self, context: ProposerContext) -> Proposal | None:
        """Run Pi once and return its proposal; raise :class:`ProposerError` on misuse."""
        if not self.api_key_present():
            raise ProposerError(
                f"environment variable {self.config.api_key_env} is not set; PiProposer cannot run"
            )
        bad_links = escaping_symlinks(context.context_dir)
        if bad_links:
            raise ProposerError(f"build context has symlinks escaping it: {bad_links}")
        attempt_dir = Path(tempfile.mkdtemp(prefix="pi-attempt-", dir=context.work_dir))
        scratch = attempt_dir / "context"
        shutil.copytree(
            context.context_dir, scratch, symlinks=True, ignore=shutil.ignore_patterns(".git")
        )
        patch_out = attempt_dir / "proposal.patch"
        meta_out = attempt_dir / "proposal.json"
        prompt_path = attempt_dir / "prompt.md"
        scratch_context = replace(context, context_dir=scratch)
        prompt_path.write_text(
            self.render_prompt(scratch_context, patch_out, meta_out), encoding="utf-8"
        )
        session_dir = attempt_dir / "sessions"
        session_dir.mkdir()
        status = {
            "status": "prepared",
            "prompt_sha256": hashlib.sha256(prompt_path.read_bytes()).hexdigest(),
            "transcript": "pi-events.jsonl",
            "stderr": "pi-stderr.log",
            "sessions": "sessions",
            "patch": "proposal.patch",
            "metadata": "proposal.json",
            "provider_response_dir": (
                str(self.config.provider_response_dir.resolve())
                if self.config.provider_response_dir is not None
                else None
            ),
            "provider_response_capture": "External gateway binding; bytes unverified here.",
        }
        self._write_status(attempt_dir, status)
        try:
            proposal = self._run_attempt(context, attempt_dir, scratch, prompt_path, status)
        except BaseException as exc:
            status.update(
                status="failed",
                error_type=type(exc).__name__,
                api_usd_known=getattr(exc, "api_usd", None),
                accounting_resolved=(
                    isinstance(exc, ProposerError)
                    and not isinstance(exc, ProposerAccountingError)
                    and exc.api_usd is not None
                ),
            )
            self._write_status(attempt_dir, status)
            raise
        status.update(status="complete", accounting_resolved=True, api_usd_known=proposal.api_usd)
        self._write_status(attempt_dir, status)
        return proposal

    @staticmethod
    def _write_status(attempt_dir: Path, status: Mapping[str, Any]) -> None:
        """Atomically retain attempt state; a killed process leaves its last known stage."""
        pending = attempt_dir / "attempt-status.json.tmp"
        with pending.open("w", encoding="utf-8") as fh:
            json.dump(dict(status), fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        pending.replace(attempt_dir / "attempt-status.json")

    def _run_attempt(
        self,
        context: ProposerContext,
        attempt_dir: Path,
        scratch: Path,
        prompt_path: Path,
        status: dict[str, Any],
    ) -> Proposal:
        patch_out = attempt_dir / "proposal.patch"
        meta_out = attempt_dir / "proposal.json"
        transcript = attempt_dir / "pi-events.jsonl"
        timeout_s = min(self.config.timeout_s, context.timeout_s or self.config.timeout_s)
        if context.deadline_monotonic is not None:
            timeout_s = min(timeout_s, context.deadline_monotonic - time.monotonic())
        if timeout_s <= 0:
            raise ProposerError("allocation deadline reached before Pi launch")
        status.update(status="running", timeout_s=timeout_s)
        self._write_status(attempt_dir, status)
        result = procs.run(
            self.build_command(prompt_path, attempt_dir / "sessions"),
            timeout_s=timeout_s,
            cwd=scratch,
            env=self.build_env(),
            log_path=transcript,
            stderr_path=attempt_dir / "pi-stderr.log",
            should_abort=context.should_abort,
        )
        status.update(
            status="process_exited",
            returncode=result.returncode,
            timed_out=result.timed_out,
            aborted=result.aborted,
            duration_s=result.duration_s,
        )
        self._write_status(attempt_dir, status)
        model_error = self._error_from_events(transcript)
        accounting_error = None
        try:
            api_usd = self._cost_from_events(transcript)
        except ProposerAccountingError as exc:
            api_usd = exc.api_usd
            accounting_error = str(exc)
        if api_usd is not None:
            self._max_observed_usd = max(self._max_observed_usd, api_usd)
        if result.timed_out or (not result.ok and model_error is None):
            accounting_error = (
                accounting_error or "Pi terminated without a reconciled terminal response"
            )
        if accounting_error:
            failure = (
                f"pi timed out after {timeout_s:.2f}s; "
                if result.timed_out
                else f"pi model call failed: {model_error}; "
                if model_error
                else f"pi exited {result.returncode}; "
                if not result.ok
                else ""
            )
            raise ProposerAccountingError(
                f"{failure}unresolved API accounting: {accounting_error} (transcript {transcript})",
                api_usd=api_usd,
            )
        if result.timed_out:
            raise ProposerError(
                f"pi timed out after {timeout_s:.2f}s (transcript {transcript})",
                api_usd=api_usd,
            )
        if model_error is not None:
            raise ProposerError(
                f"pi model call failed: {model_error} (transcript {transcript})",
                api_usd=api_usd,
            )
        if not result.ok:
            raise ProposerError(
                f"pi exited {result.returncode} (transcript {transcript})", api_usd=api_usd
            )
        # Usage is reconciled before recovering artifacts. A rejected binary diff
        # or unreadable output still belongs to this paid call, so its failure
        # must reach the loop with the same charge as a successful proposal.
        try:
            patch = (
                patch_out.read_text(encoding="utf-8")
                if patch_out.exists()
                else unified_diff_for_dirs(context.context_dir, scratch)
            )
            if not patch.strip():
                raise ProposerError(f"pi produced no patch (transcript {transcript})")
            meta: dict[str, Any] = {}
            if meta_out.exists():
                try:
                    meta = json.loads(meta_out.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    meta = {}
                if not isinstance(meta, dict):
                    raise ProposerError("pi proposal metadata must be a JSON object")
        except (ProposerError, OSError, UnicodeError) as exc:
            raise ProposerError(
                f"pi proposal recovery failed: {exc} (transcript {transcript})",
                api_usd=api_usd,
            ) from exc
        experiment = _slug(str(meta.get("experiment") or f"pi-{attempt_dir.name}"))
        return Proposal(
            experiment=experiment,
            hypothesis=str(meta.get("hypothesis", "")),
            patch=patch,
            notes=str(meta.get("notes", "")),
            api_usd=api_usd if api_usd is not None else self.config.usd_per_call_estimate,
            transcript_path=str(transcript),
        )

    @staticmethod
    def _cost_from_events(transcript: Path) -> float:
        """Sum complete terminal assistant usage; reject unknown or zero-priced usage.

        Pi's JSON mode (``dist/modes/json-event.js``) also attaches the running
        ``usage`` to every ``message_update`` delta, so only the terminal
        ``message_end`` event of each assistant message is counted.
        """
        total = 0.0
        seen = False
        problems = []
        assistant_in_progress = False
        for event in _iter_events(transcript):
            message = event.get("message")
            if (
                event.get("type") == "message_start"
                and isinstance(message, dict)
                and message.get("role") == "assistant"
            ):
                assistant_in_progress = True
            if event.get("type") != "message_end":
                continue
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            assistant_in_progress = False
            seen = True
            usage = message.get("usage")
            if not isinstance(usage, dict):
                problems.append("assistant response omitted usage")
                continue
            counts = [usage.get(k) for k in ("input", "output", "cacheRead", "cacheWrite")]
            cost = usage.get("cost")
            value = cost.get("total") if isinstance(cost, dict) else None
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                problems.append("assistant usage has no finite positive price")
                continue
            if not math.isfinite(total + value):
                problems.append("assistant usage subtotal overflowed")
                continue
            total += value
            if any(isinstance(n, bool) or not isinstance(n, int) or n < 0 for n in counts):
                problems.append("assistant usage has invalid or missing token counts")
            elif sum(counts) <= 0:
                problems.append("assistant usage has no token counts")
        if not seen:
            problems.append("no terminal assistant usage was captured")
        if assistant_in_progress:
            problems.append("a partial assistant response has no terminal usage")
        if problems:
            raise ProposerAccountingError("; ".join(dict.fromkeys(problems)), api_usd=total)
        return total

    @staticmethod
    def _error_from_events(transcript: Path) -> str | None:
        """Return terminal model failure, including output-limit partial responses."""
        for event in _iter_events(transcript):
            if event.get("type") != "message_end":
                continue
            message = event.get("message")
            if not isinstance(message, dict):
                continue
            if message.get("stopReason") == "length":
                return "response output limit reached (length); partial proposal refused"
            if message.get("stopReason") in {"error", "aborted"}:
                return str(message.get("errorMessage") or message.get("stopReason"))
        return None


def _iter_events(transcript: Path) -> Iterator[dict[str, Any]]:
    """Yield parseable JSON-object lines of a Pi JSON transcript, skipping garbage."""
    if not transcript.exists():
        return
    for line in transcript.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


def _find_costs(node: Any) -> list[float]:
    found: list[float] = []
    if isinstance(node, dict):
        cost = node.get("cost")
        if isinstance(cost, dict) and isinstance(cost.get("total"), (int, float)):
            found.append(float(cost["total"]))
        for value in node.values():
            found.extend(_find_costs(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_find_costs(value))
    return found
