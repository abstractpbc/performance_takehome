"""Subprocess helpers: every child runs in its own process group and is killed on timeout."""

from __future__ import annotations

import fcntl
import os
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunResult:
    """Outcome of :func:`run`. ``timed_out`` means the whole group was killed."""

    args: tuple[str, ...]
    returncode: int | None
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool
    aborted: bool = False

    @property
    def ok(self) -> bool:
        """True when the process exited 0 within the timeout."""
        return self.returncode == 0 and not self.timed_out


def kill_process_group(proc: subprocess.Popen[str], grace_s: float = 3.0) -> None:
    """Terminate a child session created with ``start_new_session=True``.

    Its group ID is the original child's PID even after that child exits. Polling
    only the direct child misses descendants that ignore SIGTERM or retain pipes.
    """
    pgid = proc.pid
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pgid, sig)
        except ProcessLookupError:
            return
        deadline = time.monotonic() + grace_s
        while time.monotonic() < deadline:
            proc.poll()  # Reap the direct child; its exit does not end the group.
            try:
                os.killpg(pgid, 0)
            except ProcessLookupError:
                return
            time.sleep(0.05)


def run(
    args: Sequence[str],
    *,
    timeout_s: float,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin_text: str | None = None,
    log_path: Path | None = None,
    stderr_path: Path | None = None,
    should_abort: Callable[[], bool] | None = None,
) -> RunResult:
    """Run ``args`` in a new session; kill the process group if ``timeout_s`` elapses.

    ``log_path`` receives combined stdout+stderr as the process runs so a hung
    build still leaves a partial log behind. With ``stderr_path``, stderr is
    retained separately so machine-readable stdout stays parseable.
    """
    start = time.monotonic()
    log_fh = None
    stderr_fh = None
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_fh = log_path.open("w", encoding="utf-8")
    if stderr_path is not None:
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_fh = stderr_path.open("w", encoding="utf-8")
    try:
        proc = subprocess.Popen(
            list(args),
            cwd=str(cwd) if cwd else None,
            env=dict(env) if env is not None else None,
            stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
            stdout=log_fh if log_fh else subprocess.PIPE,
            stderr=stderr_fh if stderr_fh else subprocess.STDOUT if log_fh else subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    except OSError as exc:
        if log_fh:
            log_fh.write(f"failed to start: {exc}\n")
            log_fh.close()
        if stderr_fh:
            stderr_fh.write(f"failed to start: {exc}\n")
            stderr_fh.close()
        return RunResult(tuple(args), None, "", str(exc), time.monotonic() - start, False)
    timed_out = False
    aborted = False
    group_cleaned = False
    try:
        if should_abort is None:
            stdout, stderr = proc.communicate(input=stdin_text, timeout=timeout_s)
        else:
            if stdin_text is not None and proc.stdin is not None:
                proc.stdin.write(stdin_text)
                proc.stdin.close()
                proc.stdin = None
            deadline = start + timeout_s
            while True:
                try:
                    stdout, stderr = proc.communicate(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    if should_abort():
                        aborted = True
                        raise
                    if time.monotonic() >= deadline:
                        raise
    except subprocess.TimeoutExpired:
        timed_out = not aborted
        kill_process_group(proc)
        group_cleaned = True
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
    except BaseException:
        # e.g. the harness's SIGTERM handler raised: never leave the child running
        kill_process_group(proc)
        group_cleaned = True
        raise
    finally:
        # A successful CLI may also leave background children behind (particularly
        # with log-file output, where inherited stdout does not delay communicate).
        if not group_cleaned:
            kill_process_group(proc)
        if log_fh:
            log_fh.close()
        if stderr_fh:
            stderr_fh.close()
    duration = time.monotonic() - start
    if log_fh is not None:
        stdout = log_path.read_text(encoding="utf-8", errors="replace") if log_path else ""
        stderr = ""
    if stderr_path is not None:
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
    return RunResult(
        tuple(args), proc.returncode, stdout or "", stderr or "", duration, timed_out, aborted
    )


class LockError(RuntimeError):
    """Another harness instance already owns the state directory."""


def results_lock_path(results_path: Path) -> Path:
    """Persistent host-wide lock file for ``results_path`` (never unlinked).

    Lives outside the repository so a run that appends to committed evidence does
    not leave an untracked ``.lock`` next to it; keyed by the resolved path.
    """
    import hashlib
    import tempfile

    key = hashlib.sha256(str(results_path.resolve()).encode("utf-8")).hexdigest()[:24]
    return Path(tempfile.gettempdir()) / "abstract-harness-locks" / f"{key}.lock"


def acquire_lock(path: Path) -> Any:
    """Take an exclusive, non-blocking ``flock`` on ``path`` and record our pid in it.

    The returned file object must stay referenced for as long as the lock is
    needed; closing it (or process death) releases the lock.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        fh.seek(0)
        owner = fh.read().strip() or "unknown pid"
        fh.close()
        raise LockError(f"{path} is held by another harness instance ({owner})") from exc
    fh.seek(0)
    fh.truncate()
    fh.write(f"{os.getpid()}\n")
    fh.flush()
    return fh


def cpu_idle_fraction(sample_s: float = 1.0) -> float | None:
    """Fraction of CPU time idle over ``sample_s`` from ``/proc/stat``; ``None`` if unavailable."""

    def _read() -> tuple[int, int] | None:
        try:
            with open("/proc/stat", encoding="utf-8") as fh:
                parts = fh.readline().split()
        except OSError:
            return None
        if not parts or parts[0] != "cpu":
            return None
        values = [int(v) for v in parts[1:]]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return idle, sum(values)

    first = _read()
    time.sleep(sample_s)
    second = _read()
    if first is None or second is None or second[1] == first[1]:
        return None
    return (second[0] - first[0]) / (second[1] - first[1])


def wait_for_quiet_host(
    *,
    min_idle: float = 0.85,
    stable_samples: int = 3,
    max_wait_s: float = 120.0,
    sample_s: float = 1.0,
    sampler: Callable[[float], float | None] = cpu_idle_fraction,
    should_abort: Callable[[], bool] | None = None,
) -> dict[str, float | int | bool | str | None]:
    """Block until the host CPU has been >= ``min_idle`` idle for ``stable_samples`` samples.

    Background work (BuildKit export/GC after a build, a previous container
    shutting down) would otherwise land on whichever measurement arm runs
    first and bias the A/B comparison. Bounded by ``max_wait_s``; the report
    records whether quiet was reached and the last idle reading.
    """
    start = time.monotonic()
    streak = 0
    last: float | None = None
    while True:
        last = sampler(sample_s)
        if last is None:
            return {
                "waited_s": round(time.monotonic() - start, 1),
                "idle": None,
                "quiet": None,
                "reason": "idle sampler unavailable (no readable /proc/stat)",
            }
        streak = streak + 1 if last >= min_idle else 0
        if streak >= stable_samples:
            return {"waited_s": round(time.monotonic() - start, 1), "idle": last, "quiet": True}
        if time.monotonic() - start >= max_wait_s or (should_abort and should_abort()):
            return {"waited_s": round(time.monotonic() - start, 1), "idle": last, "quiet": False}
