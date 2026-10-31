"""Bound a real Task 1 capture, owned-container cleanup and evidence export.

Run on the assigned Linux GPU host after the pinned image/model/dependencies have
been staged. Pass the controller's actual suite start so setup time is counted.
This manages a container, not the cloud allocation; off-host cost/deletion control
remains required. No weights, credentials or arbitrary files enter the export.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import re
import shlex
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SUITE = Path(__file__).resolve().parent
ROOT = SUITE.parents[2]
sys.path[:0] = [str(SUITE), str(ROOT)]

from bench.plan import load_plan, plan_digest  # noqa: E402

from tools.baseline import start_command  # noqa: E402

OWNER_LABEL = "abstract.task1.capture"
EXPORT_NAMES = (
    "events/run.json",
    "events/requests.jsonl",
    "report.json",
    "summary.md",
    "independent-timing.json",
    "audit.log",
    "runner.log",
    "evaluate.log",
    "capture-receipt.json",
    "ownership.json",
    "workload.json",
)


class CaptureError(RuntimeError):
    """A precondition, bounded stage or ownership check failed."""


def utc(value: str) -> float:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise CaptureError("suite/deadline timestamps must include a timezone")
    return parsed.timestamp()


def effective_deadline(
    suite_start_utc: str,
    *,
    wall_now: float,
    mono_now: float,
    max_wall_s: float,
    hard_deadline_utc: str | None = None,
) -> tuple[float, float]:
    """Bind a wall-clock receipt to one local monotonic deadline; never reset on resume."""
    if not math.isfinite(max_wall_s) or not 0 < max_wall_s <= 10_800:
        raise CaptureError("Task 1 suite wall ceiling must be positive and <=10800 seconds")
    start = utc(suite_start_utc)
    if start > wall_now + 1:
        raise CaptureError("suite start cannot be in the future")
    end = start + max_wall_s
    if hard_deadline_utc:
        end = min(end, utc(hard_deadline_utc))
    if end <= wall_now:
        raise CaptureError("suite/allocation deadline already exhausted")
    return mono_now + end - wall_now, max(0.0, wall_now - start)


def _stop(proc: subprocess.Popen, grace_s: float = 1.0) -> None:
    """Stop only this subprocess group, including descendants after its parent exits."""
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    elif proc.poll() is None:
        proc.terminate()
    try:
        proc.wait(timeout=grace_s)
    except subprocess.TimeoutExpired:
        pass
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    elif proc.poll() is None:
        proc.kill()
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired as exc:
        raise CaptureError("owned subprocess did not stop") from exc


def bounded_command(
    argv: list[str],
    *,
    deadline: float,
    log: Path,
    env: dict[str, str] | None = None,
    max_log_bytes: int = 16 << 20,
    shutdown_grace_s: float = 1.0,
) -> int:
    """Run without a shell; deadline includes termination and output flushing.

    A worker process owns blocking stage I/O. Output is drained incrementally with
    a bounded file and no inherited stderr/stdout pipe wait after the parent exits.
    """
    import selectors

    if os.name != "posix":
        raise CaptureError("bounded capture requires the assigned Linux host")
    if not math.isfinite(shutdown_grace_s) or shutdown_grace_s <= 0:
        raise CaptureError("positive finite subprocess shutdown grace required")
    shutdown_reserve_s = shutdown_grace_s + 1.0
    if deadline - time.monotonic() <= shutdown_reserve_s:
        raise CaptureError("insufficient time for a bounded stage and termination")
    if max_log_bytes <= 0:
        raise CaptureError("positive log cap required")
    with log.open("xb") as output:
        proc = subprocess.Popen(
            argv,
            cwd=SUITE,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        assert proc.stdout is not None
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        os.set_blocking(proc.stdout.fileno(), False)
        written = 0
        try:
            while True:
                if time.monotonic() >= deadline - shutdown_reserve_s:
                    raise CaptureError("stage deadline reached")
                for key, _ in selector.select(timeout=min(0.05, deadline - time.monotonic())):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                    else:
                        if written + len(data) > max_log_bytes:
                            raise CaptureError("stage log byte cap exceeded")
                        output.write(data)
                        written += len(data)
                if proc.poll() is not None:
                    # Do not wait forever for a background child holding a pipe.
                    while True:
                        try:
                            data = os.read(proc.stdout.fileno(), 65536)
                        except BlockingIOError:
                            break
                        if not data:
                            break
                        if written + len(data) > max_log_bytes:
                            raise CaptureError("stage log byte cap exceeded")
                        output.write(data)
                        written += len(data)
                    return proc.returncode
        finally:
            _stop(proc, grace_s=shutdown_grace_s)
            selector.close()
            proc.stdout.close()


def launch_arguments(
    lock: dict[str, Any],
    model_dir: Path,
    render_device: str,
    image_id: str,
    name: str,
    port: int,
    render_gid: int,
    owner: str,
) -> list[str]:
    """Preserve protected launch flags; change lifecycle metadata only."""
    command = start_command(
        lock, model_dir, render_device, image_id, name, port, render_gid=render_gid
    )
    command.remove("--detach")
    return command[:2] + ["--rm", "--pull=never", "--label", f"{OWNER_LABEL}={owner}"] + command[2:]


def owned_identity(value: Any, *, owner: str, name: str, image_id: str) -> str:
    """A name alone is never deletion authority."""
    if (
        not isinstance(value, dict)
        or not re.fullmatch(r"[0-9a-f]{64}", value.get("id", ""))
        or value.get("name") != "/" + name
        or value.get("owner") != owner
        or value.get("image_ref") != image_id
    ):
        raise CaptureError("container identity/ownership mismatch; refusing removal")
    return value["id"]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while data := handle.read(1 << 20):
            digest.update(data)
    return digest.hexdigest()


def persist_ownership(
    directory: Path, *, name: str, owner: str, image_id: str, model_dir: Path
) -> dict[str, Any]:
    """Persist recovery authority before launch, surviving a hard-killed wrapper."""
    if (
        not re.fullmatch(r"[0-9a-f]{32}", owner)
        or name != "abstract-t1-" + owner
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", image_id)
    ):
        raise CaptureError("invalid prelaunch container ownership")
    value = {
        "schema_version": "1.0.0",
        "container_name": name,
        "container_owner": owner,
        "owner_label": OWNER_LABEL,
        "expected_image_id": image_id,
        "model_dir": str(model_dir.resolve()),
    }
    raw = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    path = directory / "ownership.json"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    with os.fdopen(os.open(path, flags, 0o600), "wb") as handle:
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    if os.name == "posix":
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return {"path": path.name, "sha256": hashlib.sha256(raw).hexdigest()}


def pack_evidence(source: Path, destination: Path, max_bytes: int) -> dict[str, Any]:
    """Allowlisted, regular files only. No symlinks, recursion, weights or directory dumps."""
    if destination.exists() or destination.is_symlink():
        raise CaptureError("new export archive required")
    if source.is_symlink() or not source.is_dir() or max_bytes <= 0:
        raise CaptureError("regular source directory and positive byte cap required")
    members = []
    total = 0
    for relative in EXPORT_NAMES:
        path = source / relative
        if path.is_symlink() or any(
            parent.is_symlink()
            for parent in path.parents
            if parent == source or parent.is_relative_to(source)
        ):
            raise CaptureError("symlink evidence refused")
        if not path.exists():
            continue
        if not path.is_file():
            raise CaptureError("only regular evidence files may be exported")
        total += path.stat().st_size
        if total > max_bytes:
            raise CaptureError("evidence byte cap exceeded")
        members.append((relative, path))
    manifest = {
        relative: {"sha256": file_sha256(path), "bytes": path.stat().st_size}
        for relative, path in members
    }
    # This function runs in a separate bounded worker in the real CLI.
    with destination.open("xb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as archive:
        for relative, path in members:
            info = tarfile.TarInfo(relative)
            info.size = path.stat().st_size
            info.mode = 0o600
            with path.open("rb") as handle:
                archive.addfile(info, handle)
        payload = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
        info = tarfile.TarInfo("export-manifest.json")
        info.size = len(payload)
        info.mode = 0o600
        archive.addfile(info, io.BytesIO(payload))
    verified = verify_export(destination, max_bytes)
    return {
        **verified,
        "archive_bytes": destination.stat().st_size,
        "archive_sha256": file_sha256(destination),
    }


def verify_export(path: Path, max_bytes: int) -> dict[str, Any]:
    """Stream and hash the original archive on either sender or receiver; never extract."""
    if path.is_symlink() or not path.is_file() or max_bytes <= 0:
        raise CaptureError("regular export archive and positive cap required")
    actual = {}
    manifest = None
    total = 0
    seen = set()
    with tarfile.open(path, "r|gz") as archive:
        for member in archive:
            if (
                member.name in seen
                or member.name not in {*EXPORT_NAMES, "export-manifest.json"}
                or not member.isfile()
                or member.size < 0
            ):
                raise CaptureError("unsafe or duplicate archive member")
            seen.add(member.name)
            total += member.size
            if total > max_bytes + (1 << 20):
                raise CaptureError("decompressed archive exceeds evidence cap")
            handle = archive.extractfile(member)
            assert handle is not None
            if member.name == "export-manifest.json":
                if member.size > 1 << 20:
                    raise CaptureError("oversized export manifest")
                manifest = json.loads(handle.read())
            else:
                digest = hashlib.sha256()
                count = 0
                while data := handle.read(1 << 20):
                    digest.update(data)
                    count += len(data)
                actual[member.name] = {"sha256": digest.hexdigest(), "bytes": count}
    if (
        manifest != actual
        or not actual
        or sum(item["bytes"] for item in actual.values()) > max_bytes
    ):
        raise CaptureError("export payload does not match its complete manifest")
    return {"files": actual, "uncompressed_bytes": sum(item["bytes"] for item in actual.values())}


def _docker_json(argv: list[str], *, deadline: float, env: dict[str, str]) -> Any:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise CaptureError("no time remains for Docker identity check")
    proc = subprocess.run(
        argv, capture_output=True, timeout=min(5, remaining), env=env, check=False
    )
    if proc.returncode or len(proc.stdout) > 1 << 20:
        raise CaptureError("Docker identity check failed")
    return json.loads(proc.stdout)


def cleanup_container(
    *,
    name: str,
    owner: str,
    image_id: str,
    deadline: float,
    env: dict[str, str],
) -> dict[str, Any]:
    """Inspect exact identity before every destructive command; prove named absence."""

    def existing() -> list[str]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CaptureError("container cleanup deadline exhausted")
        proc = subprocess.run(
            ["docker", "container", "ls", "-aq", "--filter", f"name=^/{name}$"],
            capture_output=True,
            timeout=min(5, remaining),
            env=env,
            check=False,
        )
        if proc.returncode:
            raise CaptureError("container absence could not be established")
        return proc.stdout.decode("ascii").split()

    ids = existing()
    if ids:
        identity = _docker_json(
            [
                "docker",
                "container",
                "inspect",
                "--format",
                '{"id":{{json .Id}},"name":{{json .Name}},"image_ref":{{json .Config.Image}},'
                '"owner":{{json (index .Config.Labels "' + OWNER_LABEL + '")}}}',
                name,
            ],
            deadline=deadline,
            env=env,
        )
        container_id = owned_identity(identity, owner=owner, name=name, image_id=image_id)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CaptureError("container cleanup deadline exhausted")
        result = subprocess.run(
            ["docker", "container", "rm", "-f", container_id],
            capture_output=True,
            timeout=min(10, remaining),
            env=env,
            check=False,
        )
        if result.returncode:
            raise CaptureError("owned container removal failed")
    if existing():
        raise CaptureError("owned container still present after cleanup")
    return {"owned_container_absent": True, "container_name": name}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--mode", choices=("quick", "full", "calibration"), required=True)
    parser.add_argument("--model-dir", type=Path, required=True)
    parser.add_argument("--image", required=True, help="already-staged image reference")
    parser.add_argument(
        "--expected-image-id", required=True, help="independently recorded immutable local ID"
    )
    parser.add_argument("--tokenizer-snapshot", type=Path, required=True)
    parser.add_argument(
        "--python-site", type=Path, help="verified staged HTTPX/Tokenizers dependency directory"
    )
    parser.add_argument("--render-device", required=True)
    parser.add_argument("--suite-start-utc", required=True, help="controller receipt before setup")
    parser.add_argument("--hard-deadline-utc", help="stricter controller cost/allocation stop")
    parser.add_argument("--max-wall-seconds", type=float, default=10_800)
    parser.add_argument("--cleanup-reserve-seconds", type=float, default=30)
    parser.add_argument("--export-reserve-seconds", type=float, default=120)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--export", type=Path, required=True, help="archive on durable controller-mounted storage"
    )
    parser.add_argument("--max-evidence-bytes", type=int, default=512 << 20)
    parser.add_argument("--port", type=int, default=8000)
    return parser


def _run_capture(args: argparse.Namespace, env: dict[str, str]) -> int:
    if os.name != "posix" or sys.platform != "linux":
        raise CaptureError("real capture requires the assigned Linux GPU host")
    begin_mono, begin_wall = time.monotonic(), time.time()
    end, elapsed_setup = effective_deadline(
        args.suite_start_utc,
        wall_now=begin_wall,
        mono_now=begin_mono,
        max_wall_s=args.max_wall_seconds,
        hard_deadline_utc=args.hard_deadline_utc,
    )
    if (
        not 5 <= args.cleanup_reserve_seconds <= 300
        or not 5 <= args.export_reserve_seconds <= 900
        or end - begin_mono <= args.cleanup_reserve_seconds + args.export_reserve_seconds + 5
    ):
        raise CaptureError("insufficient cleanup/export reserve")
    out, export = args.out.resolve(), args.export.resolve()
    if out.exists() or out.is_symlink() or export.exists() or export.is_symlink():
        raise CaptureError("fresh evidence directory and archive required")
    if out.is_relative_to(ROOT) or export.is_relative_to(out):
        raise CaptureError(
            "evidence must be outside checkout and export outside evidence directory"
        )
    if not export.parent.is_dir() or args.max_evidence_bytes <= 0:
        raise CaptureError("existing export parent and positive evidence bound required")
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", args.expected_image_id):
        raise CaptureError("full immutable expected image ID required")
    plan = load_plan(args.plan)
    if args.mode not in plan["modes"]:
        raise CaptureError("mode absent from plan")
    lock = json.loads((ROOT / plan["target"]["engine_lock"]).read_text())
    role = plan["target"]["model_role"]
    marker = json.loads((args.model_dir / ".abstract-model.json").read_text())
    if marker.get("status") != "complete" or any(
        marker.get(key) != lock["models"][role][key] for key in ("repository", "revision")
    ):
        raise CaptureError("model snapshot marker does not match selected protected pin")
    for device in (Path("/dev/kfd"), Path(args.render_device)):
        if not stat.S_ISCHR(device.stat().st_mode):
            raise CaptureError("assigned GPU devices must be character devices")
    image = _docker_json(
        [
            "docker",
            "image",
            "inspect",
            "--format",
            '{"id":{{json .Id}},"os":{{json .Os}},"architecture":{{json .Architecture}}}',
            args.image,
        ],
        deadline=end,
        env=env,
    )
    if image != {"id": args.expected_image_id, "os": "linux", "architecture": "amd64"}:
        raise CaptureError("staged image identity/platform mismatch")
    owner = uuid.uuid4().hex
    name = "abstract-t1-" + owner
    out.mkdir(parents=True, exist_ok=False)
    (out / "workload.json").write_bytes(args.plan.read_bytes())
    command = launch_arguments(
        lock,
        args.model_dir,
        args.render_device,
        args.expected_image_id,
        name,
        args.port,
        Path(args.render_device).stat().st_gid,
        owner,
    )
    # The Docker daemon can keep an engine alive after its attached client exits.
    # Reap an exact-owned container before the recorder's next cold launch.
    command = [sys.executable, "-B", str(SUITE / "owned_docker.py"), *command]
    record: dict[str, Any] = {
        "schema_version": "1.0.0",
        "status": "incomplete",
        "official_result": False,
        "suite_start_utc": args.suite_start_utc,
        "wrapper_started_utc": datetime.fromtimestamp(begin_wall, UTC).isoformat(),
        "prior_setup_seconds": elapsed_setup,
        "max_wall_seconds": args.max_wall_seconds,
        "plan_digest": plan_digest(args.plan),
        "image": image,
        "model_marker": marker,
        "model_marker_scope": "bookkeeping only; independent snapshot validation remains required",
        "container_name": name,
        "container_owner": owner,
        "runtime_args": command,
        "errors": [],
    }
    work_end = end - args.cleanup_reserve_seconds - args.export_reserve_seconds
    cleanup_end = end - args.export_reserve_seconds
    # Failure here prevents any launch. An outer supervisor can retain this
    # immutable checkpoint and inspect the exact label/image before ID removal,
    # even when this wrapper cannot reach its finally block after a hard kill.
    record["ownership_checkpoint"] = persist_ownership(
        out, name=name, owner=owner, image_id=args.expected_image_id, model_dir=args.model_dir
    )
    try:
        # Also refuses collision with an unrelated pre-existing named container.
        record["prelaunch_absence"] = cleanup_container(
            name=name, owner=owner, image_id=args.expected_image_id, deadline=work_end, env=env
        )
        run_argv = [
            sys.executable,
            "-B",
            "-m",
            "runner",
            "--plan",
            str(args.plan.resolve()),
            "--mode",
            args.mode,
            "--out",
            str(out / "events"),
            "--base-url",
            f"http://127.0.0.1:{args.port}",
            "--launch-cmd",
            shlex.join(command),
            "--stop-after-launch",
            "--tokenizer-snapshot",
            str(args.tokenizer_snapshot.resolve()),
            "--gpu-count",
            "1",
            "--gpu-kind",
            "MI355X",
        ]
        record["runner_exit_code"] = bounded_command(
            run_argv,
            deadline=work_end,
            log=out / "runner.log",
            env=env,
            # The runner allows ten seconds for its separate-session supervisor
            # to stop/reap the Docker client and remove exact-owned daemon state.
            # Reserve that cleanup before this stage's deadline, not after it.
            shutdown_grace_s=12,
        )
        if record["runner_exit_code"] == 0:
            record["evaluation_exit_code"] = bounded_command(
                [
                    sys.executable,
                    "-B",
                    "evaluate.py",
                    "--events",
                    str(out / "events"),
                    "--workload",
                    str(args.plan.resolve()),
                    "--tokenizer-snapshot",
                    str(args.tokenizer_snapshot.resolve()),
                    "--out",
                    str(out / "report.json"),
                    "--summary",
                    str(out / "summary.md"),
                ],
                deadline=work_end,
                log=out / "evaluate.log",
                env=env,
            )
            record["status"] = {0: "valid", 3: "inconclusive", 4: "invalid"}.get(
                record["evaluation_exit_code"], "failed"
            )
            record["independent_audit_exit_code"] = bounded_command(
                [
                    sys.executable,
                    "-B",
                    "audit_timing.py",
                    "--events",
                    str(out / "events"),
                    "--out",
                    str(out / "independent-timing.json"),
                ],
                deadline=work_end,
                log=out / "audit.log",
                env=env,
            )
            if record["independent_audit_exit_code"] != 0:
                record["status"] = "invalid"
        else:
            record["status"] = "aborted"
    except (CaptureError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        record["errors"].append(str(exc))
        record["status"] = "failed"
    finally:
        try:
            record["cleanup"] = cleanup_container(
                name=name,
                owner=owner,
                image_id=args.expected_image_id,
                deadline=cleanup_end,
                env=env,
            )
        except (CaptureError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
            record["errors"].append("cleanup: " + str(exc))
            record["status"] = "failed"
        record["elapsed_before_export_s"] = elapsed_setup + time.monotonic() - begin_mono
        (out / "capture-receipt.json").write_text(json.dumps(record, indent=2) + "\n")
    partial = export.with_name(export.name + ".partial")
    if partial.exists() or partial.is_symlink():
        raise CaptureError("new partial export path required")
    export_code = bounded_command(
        [
            sys.executable,
            "-B",
            str(Path(__file__).resolve()),
            "_pack",
            str(out),
            str(partial),
            str(args.max_evidence_bytes),
        ],
        deadline=end,
        log=out / "export.log",
        env=env,
    )
    if export_code or not partial.is_file():
        raise CaptureError("bounded evidence export failed; partial output is not final")
    partial.replace(export)
    record["export"] = {"path": str(export), "bytes": export.stat().st_size}
    record["export_manifest"] = json.loads((out / "export.log").read_text())
    record["total_suite_seconds"] = elapsed_setup + time.monotonic() - begin_mono
    record["within_suite_deadline"] = time.monotonic() <= end
    (out / "completion.json").write_text(json.dumps(record, indent=2) + "\n")
    print(
        json.dumps(
            {
                "status": record["status"],
                "receipt": str(out / "completion.json"),
                "export": str(export),
            }
        )
    )
    return 0 if record["status"] == "valid" and record["within_suite_deadline"] else 1


def run_capture(args: argparse.Namespace) -> int:
    env = {
        key: value
        for key, value in os.environ.items()
        if key in {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR"}
    }
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if args.python_site:
        site = args.python_site.resolve()
        if not site.is_dir() or args.python_site.is_symlink():
            raise CaptureError("verified regular staged Python dependency directory required")
        env["PYTHONPATH"] = str(site)
    # Never inherit a user-selected remote Docker context or a registry credential.
    with tempfile.TemporaryDirectory(prefix="abstract-t1-docker-") as config:
        env["DOCKER_CONFIG"] = config
        return _run_capture(args, env)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "_pack":
        if len(argv) != 4:
            return 2
        print(json.dumps(pack_evidence(Path(argv[1]), Path(argv[2]), int(argv[3]))))
        return 0
    if argv and argv[0] == "verify-export":
        if len(argv) != 3:
            return 2
        print(json.dumps(verify_export(Path(argv[1]), int(argv[2]))))
        return 0
    args = _parser().parse_args(argv)

    def interrupted(signum, _frame):
        raise CaptureError(f"capture interrupted by signal {signum}")

    previous_handlers = {}
    if os.name == "posix":
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, interrupted)
    try:
        return run_capture(args)
    except (CaptureError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "REFUSED_OR_FAILED", "reason": str(exc)}))
        return 2
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main())
