"""The unattended optimization loop: propose -> build -> measure -> decide -> keep/revert.

Resilience properties (each is covered by a test under ``harness/tests``):

* every stage has a timeout; child processes run in their own process group
  and are killed on timeout; containers are stopped/removed on every path;
* every attempt (including build failures, startup timeouts, gate failures and
  interruptions) is appended to ``results.tsv`` and gets a report file;
* the last-good image is never deleted and is retagged on each keep;
* state is persisted to disk after every transition so a killed run resumes;
* SIGTERM/SIGINT finalize the current attempt as ``interrupted``, emit the
  last-good manifest, and exit;
* budget guards run before every attempt and reserve a final window.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from . import budget as budget_mod
from . import configuration_delivery, procs
from . import docker_runtime as docker
from . import measure as measure_mod
from . import results as results_mod
from .decision import Decision, decide
from .engines import CONTAINER_PORT, Engine
from .proposers import (
    Proposal,
    Proposer,
    ProposerAccountingError,
    ProposerError,
    ScriptedProposer,
    apply_patch,
    escaping_symlinks,
)
from .state import HarnessState, ImageRef, InProgress, StateError, load_state, save_state, utc_ts


class Interrupted(Exception):
    """Raised inside the loop when a termination signal was received."""


@dataclass
class Timeouts:
    """Per-stage timeouts in seconds."""

    build_s: float = 1800.0
    startup_s: float = 300.0
    measure_s: float = 900.0
    propose_s: float = 900.0
    stop_grace_s: int = 10


@dataclass
class LoopConfig:
    """Everything the loop needs; built by ``run.py`` from CLI flags."""

    repo_root: Path
    state_dir: Path
    results_path: Path
    reports_dir: Path
    patches_dir: Path
    benchmark_path: Path
    program_path: Path
    out_manifest: Path
    source_commit: str
    budget: budget_mod.BudgetConfig
    timeouts: Timeouts = field(default_factory=Timeouts)
    max_attempts: int | None = None
    network_name: str = "abstract-harness-net"
    container_prefix: str = "abstract-harness"
    image_prefix: str = "abstract-harness"
    min_effect: float = 0.03
    no_cache: bool = False
    startup_regression_limit: float = 1.5
    harness_tree_sha256: str = ""
    min_samples: int = 3
    estimated_attempt_s: float = 600.0
    keep_candidate_images: bool = False
    repo_relative_base: Path | None = None
    baseline_path: Path | None = None
    delivery_policy: str = "none"

    def rel(self, path: Path) -> str:
        """Repository-root-relative POSIX path for TSV/report references."""
        base = self.repo_relative_base or self.repo_root
        try:
            return path.resolve().relative_to(base.resolve()).as_posix()
        except ValueError:
            return path.as_posix()


def validate_state_directory(state_dir: Path, build_context: Path) -> None:
    """Refuse recursive snapshots before creating state files or launching any work."""
    context = build_context.resolve()
    if any(path.resolve().is_relative_to(context) for path in (state_dir, state_dir / "contexts")):
        raise ValueError(
            "--state-dir must be outside the engine build context; "
            "use an external directory such as /var/tmp/abstract-harness-state"
        )


class Harness:
    """Owns one engine, one proposer, one state file, and drives the loop."""

    def __init__(
        self,
        config: LoopConfig,
        engine: Engine,
        proposer: Proposer,
        *,
        log: Callable[[str], None] = print,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if config.delivery_policy not in {"none", configuration_delivery.POLICY}:
            raise ValueError("unknown delivery policy")
        if config.delivery_policy != "none":
            if engine.name != "llmd":
                raise ValueError("llmd-config-v1 delivery policy requires the llmd engine")
            configuration_delivery.validate_initial_source(engine.spec.build_context)
        spec = getattr(engine, "spec", None)
        if spec is not None:
            validate_state_directory(config.state_dir, spec.build_context)
        self.config = config
        self.engine = engine
        self.proposer = proposer
        self.log = log
        self.clock = clock
        self.state_path = config.state_dir / "state.json"
        self._lock: Any = None
        self._results_lock: Any = None
        # Locks first: nothing below may touch state.json or results.tsv until we own both.
        self._lock = procs.acquire_lock(config.state_dir / "harness.lock")
        self._results_lock = procs.acquire_lock(procs.results_lock_path(config.results_path))
        self._attempt_deadline: float | None = None
        self.benchmark = measure_mod.load_benchmark(config.benchmark_path)
        prior_state = load_state(self.state_path, on_warning=log)
        self.state = prior_state or HarnessState(
            engine=engine.name,
            source_commit=config.source_commit,
            lease_start_ts=config.budget.lease_start_ts,
            lease_deadline_ts=config.budget.lease_deadline_ts,
        )
        if prior_state and prior_state.delivery_policy != config.delivery_policy:
            self._release_locks()
            raise StateError(
                "delivery policy differs from original state; use a fresh state directory"
            )
        self.state.delivery_policy = config.delivery_policy
        if prior_state and self.clock() < prior_state.last_clock_ts:
            self._release_locks()
            raise StateError("wall clock is behind saved accounting time; reconcile the lease")
        self._budget_wall_origin = max(self.clock(), self.state.last_clock_ts)
        self._budget_monotonic_origin = time.monotonic()
        if not self.state.run_id:
            self.state.run_id = hashlib.sha256(
                str(config.state_dir.resolve()).encode()
            ).hexdigest()[:12]
        if len(self.state.run_id) != 12 or any(
            c not in "0123456789abcdef" for c in self.state.run_id
        ):
            self._release_locks()
            raise StateError("invalid Docker run namespace")
        try:
            self._run_lock = procs.acquire_lock(
                Path(tempfile.gettempdir())
                / "abstract-harness-runs"
                / (self.state.run_id + ".lock")
            )
        except BaseException:
            self._release_locks()
            raise
        suffix = "-" + self.state.run_id
        self._original_container_prefix = config.container_prefix
        for key in ("container_prefix", "image_prefix", "network_name"):
            value = getattr(config, key)
            if not value.endswith(suffix):
                setattr(config, key, value + suffix)
        if prior_state is not None:
            # A restart resumes the same allocation; it must not grant fresh GPU-hours
            # or move the lease window forward. Older checkpoints did not carry the
            # deadline, so reconstruct it from their original start and the requested
            # duration. A shorter newly supplied deadline is still respected.
            prior_deadline = prior_state.lease_deadline_ts or (
                prior_state.lease_start_ts
                + config.budget.lease_deadline_ts
                - config.budget.lease_start_ts
            )
            config.budget = replace(
                config.budget,
                lease_start_ts=prior_state.lease_start_ts,
                lease_deadline_ts=min(prior_deadline, config.budget.lease_deadline_ts),
            )
            self.state.lease_deadline_ts = config.budget.lease_deadline_ts
        self._stop_requested = False
        self._stop_signal = ""
        self._active_container: str | None = None
        for d in (config.state_dir, config.reports_dir, config.patches_dir):
            d.mkdir(parents=True, exist_ok=True)
        results_mod.repair_torn_tail(config.results_path, log=log)
        results_mod.ensure_header(config.results_path)
        if self.state.harness_tree_sha256 and config.harness_tree_sha256:
            if self.state.harness_tree_sha256 != config.harness_tree_sha256:
                log(
                    "[harness] WARNING: harness code changed since this state directory was "
                    f"created ({self.state.harness_tree_sha256[:12]} -> "
                    f"{config.harness_tree_sha256[:12]}); rows will carry the new source_commit"
                )
        self.state.harness_tree_sha256 = (
            config.harness_tree_sha256 or self.state.harness_tree_sha256
        )
        if self.state.source_commit != config.source_commit:
            self.state.source_commit = config.source_commit

    def _release_locks(self) -> None:
        # Lock files are never unlinked: unlinking races a second holder onto a new inode.
        for name in ("_run_lock", "_results_lock", "_lock"):
            fh = getattr(self, name, None)
            if fh is not None:
                fh.close()
                setattr(self, name, None)

    # ------------------------------------------------------------------ signals
    def install_signal_handlers(self) -> None:
        """Handle SIGTERM/SIGINT by requesting a graceful stop at the next checkpoint."""
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._on_signal)

    def _on_signal(self, signum: int, _frame: Any) -> None:
        name = signal.Signals(signum).name
        if self._stop_requested:
            # Second signal: abort whatever is running (even finalize); a third one is fatal.
            self.log(f"[harness] received {name} again; aborting")
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, signal.SIG_DFL)
            raise Interrupted(name)
        self._stop_requested = True
        self._stop_signal = name
        self.log(f"[harness] received {name}; stopping at the next checkpoint and finalizing")

    def _should_abort(self) -> bool:
        return self._stop_requested

    def _checkpoint(self) -> None:
        if self._stop_requested:
            raise Interrupted(self._stop_signal or "signal")

    # ------------------------------------------------------------------ helpers
    def _save(self) -> None:
        ip = self.state.in_progress
        if ip is not None and not ip.attempt_index:
            ip.attempt_index = self.state.attempt_index
        save_state(self.state_path, self.state)

    def _stage_timeout(self, configured_s: float) -> float:
        if self._attempt_deadline is None:
            return configured_s
        remaining = budget_mod.stage_timeout(configured_s, self._attempt_deadline, floor_s=0.0)
        if remaining <= 0:
            raise TimeoutError("allocation deadline reached; no new stage may start")
        return remaining

    def _set_allocation_deadline(self, usable_seconds: float, gpu_hours_remaining: float) -> None:
        if self.config.budget.gpus_allocated:
            usable_seconds = min(
                usable_seconds,
                gpu_hours_remaining * 3600 / self.config.budget.gpus_allocated,
            )
        self._attempt_deadline = time.monotonic() + max(0.0, usable_seconds)

    def _measurement_worker(
        self,
        operation: str,
        payload: dict[str, Any],
        timeout_s: float,
        *,
        is_alive: Callable[[float], bool] | None = None,
    ) -> Any:
        """Run blocking HTTP measurement in a killable process with an absolute stage cap."""
        container_stopped = False
        next_alive_check = 0.0
        stage_deadline = time.monotonic() + self._stage_timeout(timeout_s)

        def should_abort() -> bool:
            nonlocal container_stopped, next_alive_check
            if self._should_abort():
                return True
            now = time.monotonic()
            remaining = stage_deadline - now
            if is_alive is not None and now >= next_alive_check and remaining > 0:
                next_alive_check = now + 1.0
                container_stopped = not is_alive(min(5.0, remaining))
            return container_stopped

        if should_abort():
            self._checkpoint()
            raise measure_mod.MeasurementError(f"engine container stopped during {operation}")
        remaining = stage_deadline - time.monotonic()
        if remaining <= 0:
            raise measure_mod.MeasurementError(f"{operation} exceeded its time budget")
        worker_root = self.config.state_dir / "measurement-workers"
        worker_root.mkdir(parents=True, exist_ok=True)
        worker_dir = Path(tempfile.mkdtemp(prefix=f"worker-{operation}-", dir=worker_root))
        worker_input = json.dumps({"operation": operation, **payload})
        (worker_dir / "input.json").write_text(worker_input + "\n", encoding="utf-8")
        self.log(f"[harness] {operation} worker logs: {self.config.rel(worker_dir)}")
        remaining = stage_deadline - time.monotonic()
        if remaining <= 0:
            raise measure_mod.MeasurementError(
                f"{operation} exceeded its time budget (worker logs: {worker_dir})"
            )
        result = procs.run(
            [sys.executable, str(Path(measure_mod.__file__).with_name("measurement_worker.py"))],
            stdin_text=worker_input,
            timeout_s=remaining,
            should_abort=should_abort,
            log_path=worker_dir / "stdout.json",
            stderr_path=worker_dir / "stderr.log",
        )
        self._checkpoint()
        if container_stopped:
            raise measure_mod.MeasurementError(
                f"engine container stopped during {operation} (worker logs: {worker_dir})"
            )
        if result.timed_out:
            raise measure_mod.MeasurementError(
                f"{operation} exceeded its time budget (worker logs: {worker_dir})"
            )
        if not result.ok:
            raise measure_mod.MeasurementError(
                f"{operation} failed: {(result.stderr or result.stdout)[-1500:]} "
                f"(worker logs: {worker_dir})"
            )
        return json.loads(result.stdout)

    def _gpu_hours(self) -> float:
        return budget_mod.gpu_hours_used(self.config.budget, self.now())

    def now(self) -> float:
        """Budget time advances by monotonic elapsed time even when wall time rolls back."""
        ts, _ = budget_mod.monotone_now(
            self.clock(),
            self.state.last_clock_ts,
            monotonic_floor_ts=self._budget_wall_origin
            + time.monotonic()
            - self._budget_monotonic_origin,
        )
        self.state.last_clock_ts = ts
        return ts

    def _report_path(self, experiment: str) -> Path:
        return self.config.reports_dir / f"{self.state.attempt_index:03d}-{experiment}.json"

    def _write_report(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
        )

    def _append_row(self, **fields: str) -> None:
        """Append one row unless a row for this experiment id already exists (idempotent)."""
        if results_mod.has_experiment(self.config.results_path, fields["experiment"]):
            self.log(
                f"[harness] results.tsv already has a row for {fields['experiment']}; not appending"
            )
            return
        gpu_hours = "" if self.config.budget.gpus_allocated == 0 else f"{self._gpu_hours():.4f}"
        row = results_mod.make_row(
            ts=utc_ts(self.now()),
            source_commit=self.state.source_commit,
            api_usd_cumulative=f"{self.state.api_usd_cumulative:.4f}",
            gpu_hours_cumulative=gpu_hours,
            **fields,
        )
        results_mod.append_row(self.config.results_path, row)
        self.state.history.append({k: getattr(row, k) for k in results_mod.COLUMNS})
        if self.state.in_progress is not None:
            self.state.in_progress.row_written = True

    def _is_alive(self, container: str) -> Callable[[float], bool]:
        return lambda timeout_s: docker.container_running(
            container, **self._docker_active_options(timeout_s)
        )

    def _docker_active_options(self, timeout_s: float) -> dict[str, Any]:
        return {
            "timeout_s": self._stage_timeout(timeout_s),
            "should_abort": self._should_abort,
            "deadline_monotonic": self._attempt_deadline,
        }

    def _stop_active_container(self) -> None:
        if self._active_container:
            docker.stop_container(self._active_container, grace_s=self.config.timeouts.stop_grace_s)
            self._active_container = None

    def cleanup(self) -> None:
        """Kill any container this harness instance may have left behind."""
        self._stop_active_container()
        docker.kill_containers_with_prefix(self.config.container_prefix + "-")

    # ------------------------------------------------------------------ stages
    @property
    def last_good_context(self) -> Path:
        """Build context that produced the last-good image (kept patches accumulate here)."""
        if self.state.last_good and self.state.last_good.context_path:
            return self.config.repo_root / self.state.last_good.context_path
        return self.config.state_dir / "last-good-context"

    def _promote_context(self, source: Path) -> Path:
        """Prepare an immutable snapshot; the authoritative state pointer is changed later."""
        validate_state_directory(self.config.state_dir, source)
        target = self.config.state_dir / "contexts" / uuid.uuid4().hex
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        shutil.copytree(source, tmp, symlinks=True, ignore=shutil.ignore_patterns(".git"))
        os.replace(tmp, target)
        return target

    def _commit_promotion(
        self,
        reference: ImageRef,
        samples: dict[str, Any],
        row: dict[str, str],
        *,
        golden: dict[str, str] | None = None,
    ) -> None:
        """Atomically checkpoint image + immutable context + replayable completed row."""
        if self.state.in_progress is None:
            raise RuntimeError("promotion requires an in-progress transaction")
        if (
            not reference.context_path
            or not (self.config.repo_root / reference.context_path).is_dir()
        ):
            raise RuntimeError("promotion context snapshot is missing")
        docker.retag(
            reference.tag,
            f"{self.config.image_prefix}-{self.engine.name}:last-good",
            **self._docker_active_options(60),
        )
        previous = (
            self.state.last_good,
            self.state.baseline_samples,
            self.state.golden,
            self.state.in_progress.stage,
            self.state.in_progress.completed_row,
        )
        self.state.last_good = reference
        self.state.baseline_samples = samples
        if golden is not None:
            self.state.golden = golden
        self.state.in_progress.stage = "promoted"
        self.state.in_progress.completed_row = row
        try:
            self._save()
        except BaseException:
            durable = load_state(self.state_path)
            if (
                durable
                and durable.last_good == reference
                and durable.in_progress
                and durable.in_progress.stage == "promoted"
                and durable.in_progress.completed_row == row
            ):
                # A wrapper/filesystem error may be raised after the atomic rename.
                # The durable checkpoint wins over a presumed in-memory rollback.
                self.state = durable
            else:
                (
                    self.state.last_good,
                    self.state.baseline_samples,
                    self.state.golden,
                    self.state.in_progress.stage,
                    self.state.in_progress.completed_row,
                ) = previous
            raise

    def build(self, context_dir: Path, tag: str, log_path: Path) -> docker.BuildResult:
        """Build a fresh image from ``context_dir`` (digest resolved from the result)."""
        return docker.build_image(
            context_dir,
            tag,
            log_path=log_path,
            timeout_s=self._stage_timeout(self.config.timeouts.build_s),
            platform=self.engine.spec.platform,
            dockerfile=context_dir
            / self.engine.spec.dockerfile.relative_to(self.engine.spec.build_context),
            no_cache=self.config.no_cache,
            should_abort=self._should_abort,
        )

    def measure_image(
        self, image: str, *, golden: dict[str, str] | None, label: str
    ) -> tuple[measure_mod.Measurement, dict[str, Any]]:
        """Start a container from ``image``, measure startup + benchmark, always stop it."""
        container = f"{self.config.container_prefix}-{label}-{int(self.clock()) % 100000}"
        quiet = procs.wait_for_quiet_host(
            should_abort=self._should_abort, max_wait_s=self._stage_timeout(120.0)
        )
        self._checkpoint()
        if quiet["quiet"] is None:
            self.log(f"[harness] host idle unknown before {label}: {quiet.get('reason')}")
        elif not quiet["quiet"]:
            self.log(f"[harness] host did not go quiet before {label} (idle={quiet['idle']})")
        docker.ensure_internal_network(self.config.network_name, **self._docker_active_options(60))
        runtime_args = self.engine.full_runtime_args(
            docker.image_cmd(image, **self._docker_active_options(60))
        )
        command = self.engine.run_command(
            image, container, self.config.network_name, runtime_args=runtime_args
        )
        started = time.monotonic()
        self._active_container = container
        if self.state.in_progress:
            self.state.in_progress.container = container
            self.state.in_progress.stage = "starting"
            self._save()
        info: dict[str, Any] = {
            "container": container,
            "command": command,
            "runtime_args": runtime_args,
        }
        try:
            docker.run_detached(command, **self._docker_active_options(120))
            ip = None
            deadline = time.monotonic() + self._stage_timeout(30.0)
            while ip is None and time.monotonic() < deadline:
                self._checkpoint()
                ip = docker.container_ip(
                    container,
                    self.config.network_name,
                    **self._docker_active_options(max(0, min(60, deadline - time.monotonic()))),
                )
                if ip is None:
                    if not docker.container_running(container, **self._docker_active_options(10)):
                        raise measure_mod.MeasurementError(
                            "container exited immediately: "
                            + docker.container_logs(
                                container, 40, **self._docker_active_options(5)
                            )[-1500:]
                        )
                    time.sleep(0.2)
            if ip is None:
                raise measure_mod.MeasurementError(
                    "container never obtained an address on the internal network"
                )
            base_url = f"http://{ip}:{CONTAINER_PORT}"
            info["base_url"] = base_url
            info["host_quiet_before_start"] = quiet
            startup_s, model_id = self._measurement_worker(
                "startup",
                {
                    "base_url": base_url,
                    "started_at": started,
                    "benchmark": self.benchmark,
                    "timeout_s": self._stage_timeout(self.config.timeouts.startup_s),
                },
                self.config.timeouts.startup_s,
                is_alive=self._is_alive(container),
            )
            self._checkpoint()
            if self.state.in_progress:
                self.state.in_progress.stage = "measuring"
                self._save()
            data = self._measurement_worker(
                "benchmark",
                {
                    "base_url": base_url,
                    "model_id": model_id,
                    "benchmark": self.benchmark,
                    "golden": golden,
                    "startup_s": startup_s,
                },
                self.config.timeouts.measure_s,
                is_alive=self._is_alive(container),
            )
            data["traces"] = [measure_mod.RequestTrace(**trace) for trace in data["traces"]]
            data["gates"] = [measure_mod.GateResult(**gate) for gate in data["gates"]]
            measurement = measure_mod.Measurement(**data)
            return measurement, info
        finally:
            info["container_log_tail"] = docker.container_logs(container, 60, timeout_s=5)[-4000:]
            self._stop_active_container()

    # ------------------------------------------------------------------ baseline
    def establish_baseline(self) -> None:
        """Build + measure the unpatched build context; it becomes last-good and the golden set."""
        if self.state.last_good is not None:
            self.log(
                f"[harness] baseline already established: {self.state.last_good.tag} "
                f"{self.state.last_good.digest}"
            )
            return
        # Keep failed baseline attempts as distinct chronological evidence. Reserve
        # the old canonical name when unused, then use numbered retry identities.
        experiment = "baseline"
        while results_mod.has_experiment(self.config.results_path, experiment):
            experiment = f"{self.state.attempt_index:03d}-baseline"
            if results_mod.has_experiment(self.config.results_path, experiment):
                self.state.attempt_index += 1
        tag = f"{self.config.image_prefix}-{self.engine.name}:baseline"
        report_path = self._report_path(experiment)
        self.state.in_progress = InProgress(
            experiment,
            "unpatched build context",
            self.clock(),
            "",
            stage="building",
            image_tag=tag,
            attempt_index=self.state.attempt_index,
            report_path=self.config.rel(report_path),
        )
        self._save()
        log_path = (
            self.config.state_dir / "logs" / f"{self.state.attempt_index:03d}-baseline.build.log"
        )
        report: dict[str, Any] = {
            "experiment": experiment,
            "engine": self.engine.name,
            "kind": "baseline",
            "started_ts": utc_ts(self.clock()),
        }
        try:
            self._checkpoint()
            build = self.build(self.engine.spec.build_context, tag, log_path)
            report["build"] = _build_summary(build, self.config)
            if not build.ok or not build.digest:
                raise RuntimeError(f"baseline build failed: {build.error}")
            self._checkpoint()
            measurement, info = self.measure_image(tag, golden=None, label="baseline")
            report["measurement"] = measurement.to_json()
            report["container"] = info
            if not measurement.gates_passed:
                raise RuntimeError(
                    "baseline failed its own correctness gates: "
                    + "; ".join(f"{g.name}: {g.detail}" for g in measurement.gates if not g.passed)
                )
            reference = ImageRef(
                tag, build.digest, build.image_id, experiment, self.config.rel(report_path)
            )
            reference.context_path = self.config.rel(
                self._promote_context(self.engine.spec.build_context)
            )
            report["decision"] = {"outcome": "baseline", "kept": True}
            report["promotion_prepared"] = asdict(reference)
            self._write_report(report_path, report)
            row = dict(
                experiment=experiment,
                image_digest=build.digest,
                kept="1",
                outcome="baseline",
                local_startup_s=results_mod.format_metric(measurement.startup_s, 3),
                hypothesis="unpatched build context establishes last-good image, "
                "golden outputs, and noise floor",
                notes=f"ttft_median_s={_median(measurement.ttft_samples)} "
                f"ftgr_median_tok_s={_median(measurement.ftgr_samples)} "
                f"reps={measurement.repetitions}",
                development_report=self.config.rel(report_path),
                patch_path="",
            )
            self._commit_promotion(
                reference, measurement.samples(), row, golden=measure_mod.golden_from(measurement)
            )
            self._append_row(**row)
            self.state.in_progress = None
            self.state.attempt_index += 1
            self._save()
        except Interrupted:
            if self.state.in_progress and self.state.in_progress.stage == "promoted":
                raise
            report["interrupted"] = True
            self._write_report(report_path, report)
            self._append_row(
                experiment=experiment,
                image_digest="",
                kept="0",
                outcome="interrupted",
                hypothesis="baseline",
                notes=f"interrupted by {self._stop_signal} during baseline",
                development_report=self.config.rel(report_path),
                patch_path="",
            )
            self.state.in_progress = None
            self.state.attempt_index += 1
            self._save()
            raise
        except Exception as exc:  # noqa: BLE001 - every failure must be logged, not crash the loop
            if self.state.in_progress and self.state.in_progress.stage == "promoted":
                raise  # Recovery must finish the committed keep, never erase its transaction.
            report["error"] = f"{type(exc).__name__}: {exc}"
            report["traceback"] = traceback.format_exc()
            self._write_report(report_path, report)
            self._append_row(
                experiment=experiment,
                image_digest="",
                kept="0",
                outcome="error",
                hypothesis="baseline",
                notes=report["error"][:500],
                development_report=self.config.rel(report_path),
                patch_path="",
            )
            self.state.in_progress = None
            # A transient build/startup/gate error ends this invocation, not the
            # saved run. A later invocation may retry under the original lease.
            self.state.attempt_index += 1
            self.state.finish_reason = "baseline failed"
            self._save()
            raise

    # ------------------------------------------------------------------ attempt
    def attempt(self, proposal: Proposal, *, api_cost_accounted: bool = False) -> str:
        """Run one experiment end to end; returns the outcome string written to results.tsv."""
        assert self.state.last_good is not None
        idx = self.state.attempt_index
        experiment = f"{idx:03d}-{proposal.experiment}"
        patch_path = self.config.patches_dir / f"{experiment}.patch"
        patch_path.write_text(proposal.patch, encoding="utf-8")
        report_path = self._report_path(proposal.experiment)
        tag = f"{self.config.image_prefix}-{self.engine.name}:{experiment}"
        if not api_cost_accounted:
            self.state.api_usd_cumulative += max(0.0, proposal.api_usd)
        self.state.in_progress = InProgress(
            experiment,
            proposal.hypothesis,
            self.clock(),
            self.config.rel(patch_path),
            stage="building",
            image_tag=tag,
            attempt_index=self.state.attempt_index,
            report_path=self.config.rel(report_path),
        )
        self._save()
        report: dict[str, Any] = {
            "experiment": experiment,
            "engine": self.engine.name,
            "kind": "attempt",
            "started_ts": utc_ts(self.clock()),
            "hypothesis": proposal.hypothesis,
            "proposer": self.proposer.name,
            "proposal_notes": proposal.notes,
            "patch_path": self.config.rel(patch_path),
            "transcript": proposal.transcript_path,
            "last_good_before": asdict(self.state.last_good),
        }
        scratch = self.config.state_dir / "scratch" / experiment
        attempt_start = time.monotonic()
        outcome = "error"
        digest = ""
        kept = "0"
        notes = ""
        ttft = ftgr = ""
        startup = ""
        try:
            self._checkpoint()
            if scratch.exists():
                shutil.rmtree(scratch)
            source_context = (
                self.last_good_context
                if self.last_good_context.exists()
                else self.engine.spec.build_context
            )
            bad_links = escaping_symlinks(source_context)
            if bad_links:
                raise RuntimeError(f"build context has symlinks escaping it: {bad_links}")
            shutil.copytree(
                source_context, scratch, symlinks=True, ignore=shutil.ignore_patterns(".git")
            )
            ok, output = apply_patch(proposal.patch, scratch)
            report["patch_apply"] = {"ok": ok, "output": output[-2000:]}
            if not ok:
                raise RuntimeError(f"patch did not apply: {output[-300:]}")
            if self.config.delivery_policy != "none":
                self._checkpoint()

                def policy_checkpoint():
                    self._checkpoint()
                    self._stage_timeout(self.config.timeouts.build_s)

                try:
                    report["delivery_eligibility"] = configuration_delivery.validate_patch_context(
                        source_context, scratch, checkpoint=policy_checkpoint
                    )
                except ValueError as exc:
                    report["delivery_eligibility"] = {
                        "policy": self.config.delivery_policy,
                        "status": "REJECTED_BEFORE_BUILD",
                        "reason": str(exc),
                        "image_or_layer_verification": False,
                    }
                    raise
            self._checkpoint()
            build = self.build(
                scratch, tag, self.config.state_dir / "logs" / f"{experiment}.build.log"
            )
            report["build"] = _build_summary(build, self.config)
            if not build.ok or not build.digest:
                raise RuntimeError(f"build failed: {build.error}")
            digest = build.digest
            self._checkpoint()
            # A/B: require a valid current control. Historical samples cannot replace
            # a failed/missing control without confounding the promotion decision.
            assert self.state.last_good is not None
            if not docker.image_exists(self.state.last_good.tag, **self._docker_active_options(60)):
                raise measure_mod.MeasurementError("last-good control image is unavailable")
            base_measurement, base_info = self.measure_image(
                self.state.last_good.tag, golden=self.state.golden, label=f"{idx:03d}-ab"
            )
            report["baseline_measurement"] = base_measurement.to_json()
            report["baseline_container"] = base_info
            if not base_measurement.gates_passed:
                failed = "; ".join(
                    f"{g.name}: {g.detail}" for g in base_measurement.gates if not g.passed
                )
                raise measure_mod.MeasurementError(f"last-good control failed gates: {failed}")
            baseline_samples = base_measurement.samples()
            self._checkpoint()
            measurement, info = self.measure_image(
                tag, golden=self.state.golden, label=f"{idx:03d}"
            )
            report["measurement"] = measurement.to_json()
            report["container"] = info
            decision = decide(
                baseline_samples,
                measurement.samples(),
                gates_passed=measurement.gates_passed,
                gate_summary="; ".join(
                    f"{g.name}: {g.detail}" for g in measurement.gates if not g.passed
                ),
                min_effect=self.config.min_effect,
                min_samples=self.config.min_samples,
                startup_regression_limit=self.config.startup_regression_limit,
                expected_samples={
                    p["id"]: self.benchmark["repetitions"] for p in self.benchmark["prompts"]
                },
            )
            report["decision"] = _decision_summary(decision)
            outcome = decision.outcome
            notes = decision.reason
            ttft = results_mod.format_metric(_speedup(decision, "ttft_s"))
            ftgr = results_mod.format_metric(_speedup(decision, "ftgr_tok_s"))
            startup = results_mod.format_metric(measurement.startup_s, 3)
            if decision.kept:
                reference = ImageRef(
                    tag,
                    build.digest,
                    build.image_id,
                    experiment,
                    self.config.rel(report_path),
                    self.config.rel(patch_path),
                )
                reference.context_path = self.config.rel(self._promote_context(scratch))
                report["promotion_prepared"] = asdict(reference)
                self._write_report(report_path, report)
                row = dict(
                    experiment=experiment,
                    image_digest=digest,
                    kept="1",
                    outcome=outcome,
                    local_ttft_speedup=ttft,
                    local_ftgr_speedup=ftgr,
                    local_startup_s=startup,
                    hypothesis=proposal.hypothesis,
                    notes=notes[:900],
                    development_report=self.config.rel(report_path),
                    patch_path=self.config.rel(patch_path),
                )
                self._commit_promotion(reference, measurement.samples(), row)
                kept = "1"
                self.log(f"[harness] KEPT {experiment} -> {build.digest}")
            else:
                self.log(f"[harness] reverted {experiment}: {outcome} ({notes})")
                if not self.config.keep_candidate_images:
                    docker.remove_image(tag)
        except Interrupted:
            if self.state.in_progress and self.state.in_progress.stage == "promoted":
                raise
            outcome = "interrupted"
            stage = self.state.in_progress.stage if self.state.in_progress else "?"
            notes = f"interrupted by {self._stop_signal} during stage {stage}"
            report["interrupted"] = notes
        except (
            RuntimeError,
            measure_mod.MeasurementError,
            docker.DockerError,
            OSError,
            ValueError,
        ) as exc:
            if self.state.in_progress and self.state.in_progress.stage == "promoted":
                raise
            if self._stop_requested:
                outcome = "interrupted"
                notes = f"interrupted by {self._stop_signal} ({type(exc).__name__}: {exc})"
                report["interrupted"] = notes
            else:
                outcome = "error"
                notes = f"{type(exc).__name__}: {exc}"
                report["error"] = notes
                report["traceback"] = traceback.format_exc()
            if (
                not self.config.keep_candidate_images
                and tag != (self.state.last_good.tag if self.state.last_good else "")
                and docker.image_exists(tag, timeout_s=5)
            ):
                docker.remove_image(tag)
        finally:
            self._stop_active_container()
            shutil.rmtree(scratch, ignore_errors=True)
            committed_row = (
                self.state.in_progress.completed_row
                if self.state.in_progress and self.state.in_progress.stage == "promoted"
                else None
            )
            if committed_row:
                # Local assignments may not have run after the durable keep checkpoint.
                # Never overwrite that keep with an error/interrupted local outcome.
                outcome = committed_row["outcome"]
                kept = committed_row["kept"]
            report["finished_ts"] = utc_ts(self.clock())
            report["outcome"] = outcome
            report["kept"] = kept == "1"
            report["last_good_after"] = asdict(self.state.last_good)
            self._write_report(report_path, report)
            if committed_row:
                self._append_row(**committed_row)
            else:
                self._append_row(
                    experiment=experiment,
                    image_digest=digest,
                    kept=kept,
                    outcome=outcome,
                    local_ttft_speedup=ttft,
                    local_ftgr_speedup=ftgr,
                    local_startup_s=startup,
                    hypothesis=proposal.hypothesis,
                    notes=notes[:900],
                    development_report=self.config.rel(report_path),
                    patch_path=self.config.rel(patch_path),
                )
            self.state.attempt_durations_s.append(round(time.monotonic() - attempt_start, 3))
            self.state.in_progress = None
            self.state.attempt_index += 1
            self._save()
        return outcome

    # ------------------------------------------------------------------ resume
    def recover_from_previous_run(self) -> None:
        """If a previous process died mid-attempt, log that attempt as ``interrupted``."""
        self._reconcile_attempt_index()
        ip = self.state.in_progress
        if ip is None:
            return
        if ip.container:
            # Older checkpoints predate per-run namespaces. Remove only the exact
            # saved server, never every container sharing the old global prefix.
            allowed_prefixes = (self._original_container_prefix, self.config.container_prefix)
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}", ip.container) or not any(
                ip.container.startswith(prefix + "-") for prefix in allowed_prefixes
            ):
                raise StateError("saved recovery container is outside the run namespace")
            docker.stop_container(ip.container, grace_s=self.config.timeouts.stop_grace_s)
            if not docker.container_absent(ip.container):
                raise StateError("saved recovery container still exists; recovery blocked")
        if ip.stage == "promoted":
            reference = self.state.last_good
            if (
                not reference
                or reference.experiment != ip.experiment
                or not reference.context_path
                or not (self.config.repo_root / reference.context_path).is_dir()
                or ip.completed_row.get("experiment") != ip.experiment
                or ip.completed_row.get("image_digest") != reference.digest
            ):
                raise RuntimeError(
                    "committed promotion cannot be reconciled with its image/context"
                )
            existing = [
                row
                for row in results_mod.read_rows(self.config.results_path)
                if row.experiment == ip.experiment
            ]
            if existing and any(
                getattr(existing[0], key) != value for key, value in ip.completed_row.items()
            ):
                raise RuntimeError("completed row conflicts with its durable promotion checkpoint")
            self._append_row(**ip.completed_row)
            self.state.in_progress = None
            self.state.attempt_index = max(self.state.attempt_index, ip.attempt_index + 1)
            self._save()
            return
        if results_mod.has_experiment(self.config.results_path, ip.experiment):
            row = next(
                r
                for r in results_mod.read_rows(self.config.results_path)
                if r.experiment == ip.experiment
            )
            if row.kept == "1" and (
                not self.state.last_good or row.image_digest != self.state.last_good.digest
            ):
                raise RuntimeError(
                    "completed keep differs from saved image/context; recovery blocked"
                )
            self.log(
                f"[harness] results.tsv already records {ip.experiment}; the previous process "
                "died after writing the row - clearing the in-progress marker only"
            )
            docker.kill_containers_with_prefix(self.config.container_prefix + "-")
            self.state.in_progress = None
            self.state.attempt_index = max(self.state.attempt_index, ip.attempt_index + 1)
            self._save()
            return
        self.log(
            f"[harness] previous run died during {ip.experiment} ({ip.stage}); "
            "logging as interrupted"
        )
        docker.kill_containers_with_prefix(self.config.container_prefix + "-")
        report_path = (
            self.config.repo_root / ip.report_path
            if ip.report_path
            else self._report_path(
                ip.experiment.split("-", 1)[-1] if ip.experiment != "baseline" else "baseline"
            )
        )
        if report_path.exists():
            # Never overwrite a report the previous process finished writing.
            report_path = report_path.with_suffix(".recovered.json")
        report = {
            "experiment": ip.experiment,
            "kind": "recovered",
            "stage": ip.stage,
            "hypothesis": ip.hypothesis,
            "started_ts": utc_ts(ip.started_ts),
            "note": "process was not running when the harness restarted; "
            "attempt logged as interrupted",
        }
        self._write_report(report_path, report)
        self._append_row(
            experiment=ip.experiment,
            image_digest="",
            kept="0",
            outcome="interrupted",
            hypothesis=ip.hypothesis,
            notes=f"recovered on restart: previous process died during stage {ip.stage}",
            development_report=self.config.rel(report_path),
            patch_path=ip.patch_path,
        )
        if ip.image_tag and ip.image_tag != (
            self.state.last_good.tag if self.state.last_good else ""
        ):
            docker.remove_image(ip.image_tag)
        self.state.in_progress = None
        self.state.attempt_index = max(self.state.attempt_index, ip.attempt_index) + 1
        self._save()

    def _reconcile_attempt_index(self) -> None:
        """Never reuse an attempt number results.tsv already contains (e.g. after a .bak rewind)."""
        try:
            rows = results_mod.read_rows(self.config.results_path)
        except results_mod.ResultsError as exc:
            raise RuntimeError(f"results file is not usable for resume: {exc}") from exc
        highest = 0
        for row in rows:
            prefix = row.experiment.split("-", 1)[0]
            if prefix.isdigit():
                highest = max(highest, int(prefix))
        self.state.history = [asdict(row) for row in rows]
        if rows and self.state.attempt_index <= highest:
            self.log(
                f"[harness] attempt index {self.state.attempt_index} is behind results.tsv "
                f"({highest}); advancing"
            )
            self.state.attempt_index = highest + 1

    # ------------------------------------------------------------------ main loop
    def run(self) -> int:
        """Drive the loop until the proposer, budget, attempt cap, or a signal stops it.

        Returns a process exit code: 0 normal, 3 interrupted, 2 baseline failure,
        4 when another instance holds the state-directory lock.
        """
        self.install_signal_handlers()
        try:
            self.recover_from_previous_run()
            if isinstance(self.proposer, ScriptedProposer):
                # Baseline retries consume global evidence IDs, not queue entries.
                failed_baselines = 0
                for row in self.state.history:
                    if row.get("outcome") == "baseline":
                        break
                    if row.get("outcome") in {"error", "interrupted"} and (
                        row.get("experiment") == "baseline"
                        or re.fullmatch(r"\d+-baseline", row.get("experiment", ""))
                    ):
                        failed_baselines += 1
                self.proposer.resume_from_attempt_index(
                    max(0, self.state.attempt_index - failed_baselines)
                )
            if self.state.api_accounting_unresolved:
                self.finalize(
                    "API accounting unresolved; reconcile provider billing before resuming"
                )
                return 2
            if (
                self.state.finished
                and self.state.last_good is None
                and self.state.in_progress is None
                and self.state.history
                and all(
                    row.get("outcome") in {"error", "interrupted"}
                    and (
                        row.get("experiment") == "baseline"
                        or re.fullmatch(r"\d+-baseline", row.get("experiment", ""))
                    )
                    for row in self.state.history
                )
            ):
                # Previous releases persisted a terminal flag on baseline
                # failure. Recover only that unambiguous checkpoint shape;
                # keep its evidence, costs and original lease unchanged.
                self.log("[harness] retrying previously failed baseline")
                self.state.finished = False
                self._save()
            if self.state.finished:
                self.log(
                    f"[harness] state marked finished ({self.state.finish_reason}); "
                    "writing manifest only"
                )
                self.finalize("already finished")
                return 0
            verdict = budget_mod.check_before_attempt(
                self.config.budget,
                api_usd_cumulative=self.state.api_usd_cumulative,
                now_ts=self.now(),
                estimated_attempt_s=0.0,
            )
            if not verdict.ok or verdict.usable_seconds_remaining <= 0:
                self.finalize(f"budget guard before baseline: {verdict.reason}")
                return 0
            self._set_allocation_deadline(
                verdict.usable_seconds_remaining, verdict.gpu_hours_remaining
            )
            self.establish_baseline()
            attempts = 0
            while True:
                self._checkpoint()
                if getattr(self.proposer, "exhausted", lambda: False)() is True:
                    self.finalize("proposer exhausted")
                    return 0
                if self.config.max_attempts is not None and attempts >= self.config.max_attempts:
                    self.finalize("attempt cap reached")
                    return 0
                t = self.config.timeouts
                worst_case_s = budget_mod.worst_case_attempt_s(
                    propose_s=t.propose_s,
                    build_s=t.build_s,
                    startup_s=t.startup_s,
                    measure_s=t.measure_s,
                    stop_grace_s=t.stop_grace_s,
                )
                estimate_s = budget_mod.attempt_estimate_s(
                    self.config.estimated_attempt_s, self.state.attempt_durations_s
                )
                verdict = budget_mod.check_before_attempt(
                    self.config.budget,
                    api_usd_cumulative=self.state.api_usd_cumulative,
                    now_ts=self.now(),
                    estimated_attempt_s=estimate_s,
                    estimated_attempt_usd=self.proposer.estimated_usd_per_call(),
                )
                # Hard bound: no stage may run past the lease's usable window, even if the
                # configured timeouts (worst case = worst_case_s) would allow it.
                self._set_allocation_deadline(
                    min(worst_case_s, verdict.usable_seconds_remaining),
                    verdict.gpu_hours_remaining,
                )
                self.log(
                    f"[harness] budget check: {verdict.reason} "
                    f"(api_usd_remaining={verdict.api_usd_remaining:.2f}, "
                    f"gpu_h_used={verdict.gpu_hours_used:.3f}, "
                    f"usable_s={verdict.usable_seconds_remaining:.0f})"
                )
                if not verdict.ok:
                    self.finalize(f"budget guard: {verdict.reason}")
                    return 0
                proposer_context = self._proposer_context(verdict)
                paid_proposer = not isinstance(self.proposer, ScriptedProposer)
                if paid_proposer:
                    # Commit before the subprocess can send its first paid request.
                    # A hard kill, signal, or unexpected exception leaves this marker
                    # blocking every future call until billing is reconciled.
                    self.state.api_accounting_unresolved = (
                        f"pending proposer call at attempt {self.state.attempt_index}; "
                        "reconcile provider billing before resuming"
                    )
                    self._save()
                    # Corrupt-main recovery loads the previous checkpoint. Make
                    # that backup fail closed too before a paid request can start.
                    self._save()
                try:
                    proposal = self.proposer.propose(proposer_context)
                except ProposerError as exc:
                    self.log(f"[harness] proposer error: {exc}")
                    self.state.api_usd_cumulative += exc.api_usd or 0.0
                    if isinstance(exc, ProposerAccountingError) or (
                        paid_proposer and exc.api_usd is None
                    ):
                        self.state.api_accounting_unresolved = str(exc)
                    else:
                        self.state.api_accounting_unresolved = ""
                    # The settled cost and cleared marker are one durable checkpoint,
                    # before any report/TSV write can fail or the process can die.
                    self._save()
                    self._append_row(
                        experiment=f"{self.state.attempt_index:03d}-proposer",
                        image_digest="",
                        kept="0",
                        outcome="error",
                        hypothesis="",
                        notes=f"proposer error: {exc}"[:900],
                        development_report="",
                        patch_path="",
                    )
                    self.state.attempt_index += 1
                    self._save()
                    attempts += 1
                    if self.state.api_accounting_unresolved:
                        self.finalize(
                            "API accounting unresolved; reconcile provider billing before resuming"
                        )
                        return 2
                    continue
                if proposal is None:
                    if paid_proposer:
                        self.finalize(
                            "API accounting unresolved; proposer returned no settled cost"
                        )
                        return 2
                    self.finalize("proposer exhausted")
                    return 0
                if paid_proposer:
                    self.state.api_usd_cumulative += max(0.0, proposal.api_usd)
                    self.state.api_accounting_unresolved = ""
                    self._save()
                self.attempt(proposal, api_cost_accounted=paid_proposer)
                attempts += 1
        except Interrupted:
            self.finalize(f"interrupted by {self._stop_signal}")
            return 3
        except Exception as exc:  # noqa: BLE001 - last line of defence: always finalize
            self.log(f"[harness] fatal {type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            self.finalize(f"fatal: {type(exc).__name__}: {exc}")
            return 2
        finally:
            try:
                self.cleanup()
            finally:
                self._release_locks()

    def _proposer_context(self, verdict: budget_mod.BudgetVerdict) -> Any:
        from .proposers import ProposerContext

        profile = json.loads(
            (self.config.repo_root / "agent-harness" / "profiles.json").read_text(encoding="utf-8")
        )["profiles"]["harness-design"]
        summary = (
            f"api_usd_remaining={verdict.api_usd_remaining:.2f}; "
            f"gpu_hours_used={verdict.gpu_hours_used:.3f}; "
            f"usable_seconds_remaining={verdict.usable_seconds_remaining:.0f}"
        )
        work = self.config.state_dir / "proposer"
        work.mkdir(parents=True, exist_ok=True)
        current_context = (
            self.last_good_context
            if self.last_good_context.exists()
            else self.engine.spec.build_context
        )
        dockerfile = self.engine.spec.dockerfile.relative_to(self.engine.spec.build_context)
        readonly_inputs = {
            f"Current build-context file {dockerfile.as_posix()}": (
                current_context / dockerfile
            ).read_text(encoding="utf-8"),
            f"Selected development benchmark {self.config.benchmark_path}": (
                self.config.benchmark_path.read_text(encoding="utf-8")
            ),
            "Current runtime binding and last-good image": json.dumps(
                {
                    "model_args": self.engine.spec.model_args,
                    "model_directory": str(self.engine.spec.model_dir),
                    "base_image": self.engine.spec.base_image,
                    "last_good_digest": (
                        self.state.last_good.digest if self.state.last_good else None
                    ),
                },
                indent=2,
            ),
        }
        if self.engine.name == "llmd":
            baseline = self.config.baseline_path or self.config.repo_root / "baseline/lock.json"
            readonly_inputs[f"Selected baseline image/model catalog {baseline}"] = (
                baseline.read_text(encoding="utf-8")
            )
        return ProposerContext(
            program_md=self.config.program_path.read_text(encoding="utf-8"),
            profile=profile,
            history=list(self.state.history),
            context_dir=current_context,
            work_dir=work,
            engine=self.engine.name,
            budget_summary=summary,
            timeout_s=self._stage_timeout(self.config.timeouts.propose_s),
            should_abort=self._should_abort,
            deadline_monotonic=self._attempt_deadline,
            readonly_inputs=readonly_inputs,
        )

    # ------------------------------------------------------------------ finalize
    def finalize(self, reason: str) -> dict[str, Any]:
        """Write the last-good manifest + integrity report and mark the state finished."""
        from .manifest import write_manifest

        self.state.finish_reason = reason
        self._save()
        manifest = write_manifest(self)
        self.log(f"[harness] finalized ({reason}); last-good={manifest.get('image')}")
        return manifest


def _median(values: list[float]) -> str:
    if not values:
        return ""
    ordered = sorted(values)
    mid = len(ordered) // 2
    value = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
    return f"{value:.4f}"


def _speedup(decision: Decision, metric: str) -> float | None:
    comp = decision.comparison(metric)
    return comp.speedup if comp else None


def _build_summary(build: docker.BuildResult, config: LoopConfig) -> dict[str, Any]:
    return {
        "ok": build.ok,
        "timed_out": build.timed_out,
        "duration_s": round(build.duration_s, 2),
        "no_cache": config.no_cache,
        "tag": build.tag,
        "image_id": build.image_id,
        "digest": build.digest,
        "log": config.rel(build.log_path),
        "error": build.error,
    }


def _decision_summary(decision: Decision) -> dict[str, Any]:
    return {
        "outcome": decision.outcome,
        "kept": decision.kept,
        "reason": decision.reason,
        "comparisons": [asdict(c) for c in decision.comparisons],
    }


__all__ = ["Harness", "LoopConfig", "Timeouts", "Interrupted", "subprocess"]
