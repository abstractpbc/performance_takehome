"""Optional same-GPU study. Inactive without --execute; no provider/model APIs."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import signal
import socket
import statistics
import subprocess
import sys
import tarfile
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def keep(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def verify_source(root):
    manifest = json.loads((HERE / "SOURCE-PINS.json").read_bytes())
    for name, row in manifest["files"].items():
        file = root / name
        if (
            not file.is_file()
            or file.is_symlink()
            or file.stat().st_size != row["bytes"]
            or sha(file) != row["sha256"]
        ):
            raise ValueError("actual96a9 source byte pin differs: " + name)
    return manifest


def summarize(source, out, tokenizer, launch_mono=None):
    suite = source / "tasks/benchmark-design/suite"
    sys.path[:0] = [str(suite), str(source)]
    from bench.gates import judge
    from bench.stats import difference_ci

    plan = json.loads((out / "workload.study.json").read_bytes())
    run = json.loads((out / "events/run.json").read_bytes())
    requests = [
        json.loads(line) for line in (out / "events/requests.jsonl").read_text().splitlines()
    ]
    rows, groups = [], {}
    for raw in requests:
        verdict = judge(raw, plan, [], tokenizer)
        row = {
            "request_id": verdict.request_id,
            "phase": raw["phase"],
            "cell_id": raw["cell_id"],
            "category": verdict.category,
            "reasons": verdict.reasons,
            "TTFT_s": verdict.ttft_s,
        }
        rows.append(row)
        if raw["phase"] == "measure" and verdict.category == "completed":
            groups.setdefault(raw["cell_id"], []).append(verdict.ttft_s)
    controls = groups.get("control_a", []) + groups.get("control_b", [])
    treatment = groups.get("delay100", [])
    difference = difference_ci(
        controls, treatment, statistics.median, resamples=2000, confidence=0.95, seed=2026102903
    )
    proxies = [json.loads(p.read_bytes()) for p in sorted((out / "proxy").glob("*/result.json"))]
    startup = next((x for x in run["startup"] if x["status"] == "valid"), None)
    result = {
        "kind": "SEPARATE_ENGINE_BACKED_POINT_DIAGNOSTIC",
        "official_grade": False,
        "power_or_promotion_claimed": False,
        "all_request_verdicts": rows,
        "groups_TTFT_samples_s": groups,
        "delay_minus_clean_median_descriptive_CI95": difference,
        "CI_qualification": "Tiny fixed n, independent-request percentile bootstrap only; "
        "no measured power, population coverage or regression grade.",
        "client_integrity": run["client_integrity"],
        "proxy_requests": proxies,
        "original_recorder_startup": run["startup"],
        "wrapper_owned_cold_first_valid_generation_s": startup["first_valid_generation_at"]
        - launch_mono
        if startup and launch_mono is not None
        else None,
        "cold_qualification": "Separate physical wrapper launch clock; recorder cold=false "
        "is retained unchanged. No host page-cache-cold claim.",
    }
    keep(out / "study-summary.json", result)
    return result


def export(out, destination):
    if destination.exists() or destination.is_symlink():
        raise ValueError("new archive required")
    allowed = {
        "STUDY-PLAN.json",
        "SOURCE-PINS.json",
        "workload.study.json",
        "ownership.json",
        "engine-command.json",
        "launch.json",
        "engine.log",
        "runner.log",
        "report.json",
        "summary.md",
        "evaluate.log",
        "independent-timing.json",
        "audit.log",
        "study-summary.json",
        "STUDY-RESULT.json",
        "events/run.json",
        "events/requests.jsonl",
    }
    files = []
    for path in out.rglob("*"):
        if path.is_symlink():
            raise ValueError("study evidence symlink refused")
        if not path.is_file():
            continue
        rel = path.relative_to(out).as_posix()
        proxy = (
            len(Path(rel).parts) == 3
            and rel.startswith("proxy/")
            and path.name in {"upstream.sse", "downstream.sse", "events.jsonl", "result.json"}
        )
        if rel not in allowed and not proxy:
            raise ValueError("unexpected study evidence file: " + rel)
        files.append((rel, path))
    if len(files) > 250 or sum(p.stat().st_size for _, p in files) > 64 << 20:
        raise ValueError("bounded small diagnostic evidence")
    manifest = {name: {"bytes": path.stat().st_size, "sha256": sha(path)} for name, path in files}
    with destination.open("xb") as raw, tarfile.open(fileobj=raw, mode="w:gz") as archive:
        for name, path in files:
            info = tarfile.TarInfo(name)
            info.size, info.mode = path.stat().st_size, 0o600
            with path.open("rb") as stream:
                archive.addfile(info, stream)
        body = (json.dumps(manifest, indent=2) + "\n").encode()
        info = tarfile.TarInfo("export-manifest.json")
        info.size, info.mode = len(body), 0o600
        archive.addfile(info, io.BytesIO(body))
    verified = verify_export(destination)
    if verified["files"] != manifest:
        raise ValueError("independent diagnostic archive readback differs")
    return {
        "archive": str(destination),
        "bytes": destination.stat().st_size,
        "sha256": sha(destination),
        **verified,
    }


def verify_export(archive_path):
    actual, declared, seen, total = {}, None, set(), 0
    with tarfile.open(archive_path, "r|gz") as archive:
        for member in archive:
            if (
                not member.isfile()
                or member.name in seen
                or member.name.startswith("/")
                or ".." in Path(member.name).parts
                or member.size < 0
            ):
                raise ValueError("unsafe diagnostic archive member")
            seen.add(member.name)
            total += member.size
            if total > (65 << 20):
                raise ValueError("bounded diagnostic archive")
            stream = archive.extractfile(member)
            if member.name == "export-manifest.json":
                if member.size > 1 << 20:
                    raise ValueError("bounded diagnostic manifest")
                declared = json.loads(stream.read())
            else:
                value, size = hashlib.sha256(), 0
                while block := stream.read(1 << 20):
                    value.update(block)
                    size += len(block)
                actual[member.name] = {"bytes": size, "sha256": value.hexdigest()}
    if not actual or declared != actual:
        raise ValueError("complete diagnostic archive body/manifest mismatch")
    return {"files": actual, "verified_complete_archive_manifest": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--verify-export", type=Path)
    parser.add_argument("--source-root", type=Path, default=HERE / "source")
    for name in ("model-dir", "tokenizer-snapshot", "python-site", "out", "export"):
        parser.add_argument("--" + name, type=Path)
    for name in ("image-id", "render-device", "hard-deadline-utc"):
        parser.add_argument("--" + name)
    parser.add_argument("--engine-port", type=int, default=19181)
    parser.add_argument("--proxy-port", type=int, default=19180)
    args = parser.parse_args()
    if args.verify_export:
        print(json.dumps(verify_export(args.verify_export)))
        return 0
    if not args.execute:
        print('{"inactive":true,"GPU_provider_or_model_actions":0}')
        return 0
    if sys.platform != "linux":
        raise ValueError("assigned native Linux host only")
    end_wall = datetime.fromisoformat(args.hard_deadline_utc.replace("Z", "+00:00")).timestamp()
    begin_wall, begin_mono = time.time(), time.monotonic()
    if end_wall - begin_wall < 480:
        raise ValueError("skip: less than8minutes remain before original GPU work stop")
    end = begin_mono + min(480, end_wall - begin_wall)
    source = args.source_root.resolve()
    verify_source(source)
    suite = source / "tasks/benchmark-design/suite"
    sys.path[:0] = [str(args.python_site), str(suite), str(source), str(HERE)]
    import capture
    from bench.model_tokens import ModelTokenizer
    from fault_proxy import make_server

    tokenizer = ModelTokenizer(args.tokenizer_snapshot or args.model_dir, "target")
    if (
        args.out.exists()
        or not args.out.is_absolute()
        or not args.export.is_absolute()
        or not args.export.parent.is_dir()
        or args.export.is_relative_to(args.out)
    ):
        raise ValueError("new absolute evidence/archive paths required")
    args.out.mkdir(mode=0o700)
    for name in ("STUDY-PLAN.json", "SOURCE-PINS.json", "workload.study.json"):
        (args.out / name).write_bytes((HERE / name).read_bytes())
    env = {
        "PATH": "/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "PYTHONPATH": os.pathsep.join(map(str, (args.python_site, suite, source))),
    }
    existing = subprocess.run(
        ["docker", "ps", "--format", "{{.ID}}"], env=env, capture_output=True, timeout=5
    )
    if existing.returncode or existing.stdout.strip():
        raise ValueError("Task2 must be finished; no parallel GPU containers")
    with socket.socket() as probe:
        probe.settimeout(0.5)
        if probe.connect_ex(("127.0.0.1", args.engine_port)) == 0:
            raise ValueError("pre-existing engine-port responder; no cold claim or launch")
    owner = uuid.uuid4().hex
    name = "abstract-t1-" + owner
    capture.persist_ownership(
        args.out, name=name, owner=owner, image_id=args.image_id, model_dir=args.model_dir
    )
    lock = json.loads((source / "baseline/lock.json").read_bytes())
    command = capture.launch_arguments(
        lock,
        args.model_dir,
        args.render_device,
        args.image_id,
        name,
        args.engine_port,
        Path(args.render_device).stat().st_gid,
        owner,
    )
    command = [sys.executable, "-B", str(suite / "owned_docker.py"), *command]
    keep(args.out / "engine-command.json", {"argv": command})
    server = make_server(
        f"http://127.0.0.1:{args.engine_port}", args.out / "proxy", port=args.proxy_port
    )
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    engine, log = None, None
    result = {
        "kind": "OPTIONAL_SAME_GPU_DIAGNOSTIC",
        "official_grade": False,
        "original_full_suite_or_financial_clock_changed": False,
        "started_utc": datetime.now(UTC).isoformat(),
        "status": "IN_PROGRESS",
    }

    def terminated(*_):
        raise KeyboardInterrupt("owned diagnostic stopped")

    old_term = signal.signal(signal.SIGTERM, terminated)
    try:
        capture.cleanup_container(
            name=name, owner=owner, image_id=args.image_id, deadline=end - 60, env=env
        )
        launch_mono = time.monotonic()
        keep(
            args.out / "launch.json",
            {
                "wrapper_engine_launch_mono_s": launch_mono,
                "UTC": datetime.now(UTC).isoformat(),
                "cold_scope": "new owned process/container, not host cache",
                "source_commit": "96a9fa5a716d54a9700d14627fe184dec4e7dedc",
            },
        )
        log = (args.out / "engine.log").open("xb")
        engine = subprocess.Popen(
            command, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True
        )
        argv = [
            sys.executable,
            "-B",
            "-m",
            "runner",
            "--plan",
            str(args.out / "workload.study.json"),
            "--mode",
            "diagnostic",
            "--out",
            str(args.out / "events"),
            "--base-url",
            f"http://127.0.0.1:{args.proxy_port}",
            "--tokenizer-snapshot",
            str(args.tokenizer_snapshot or args.model_dir),
            "--gpu-count",
            "1",
            "--gpu-kind",
            "MI355X",
            "--request-timeout-s",
            "30",
        ]
        result["runner_exit_code"] = capture.bounded_command(
            argv, deadline=end - 60, log=args.out / "runner.log", env=env
        )
        for script, extra, logname in (
            (
                "evaluate.py",
                [
                    "--workload",
                    str(args.out / "workload.study.json"),
                    "--tokenizer-snapshot",
                    str(args.tokenizer_snapshot or args.model_dir),
                    "--out",
                    str(args.out / "report.json"),
                    "--summary",
                    str(args.out / "summary.md"),
                ],
                "evaluate.log",
            ),
            ("audit_timing.py", ["--out", str(args.out / "independent-timing.json")], "audit.log"),
        ):
            result[script + "_exit_code"] = capture.bounded_command(
                [
                    sys.executable,
                    "-B",
                    str(suite / script),
                    "--events",
                    str(args.out / "events"),
                    *extra,
                ],
                deadline=end - 60,
                log=args.out / logname,
                env=env,
            )
        result["owned_engine_supervisor_alive_after_capture"] = engine.poll() is None
        summarize(
            source, args.out, tokenizer,
            launch_mono if result["owned_engine_supervisor_alive_after_capture"] else None,
        )
        result["status"] = "CAPTURED_DIAGNOSTIC_NOT_OFFICIAL_GRADE"
    except BaseException as exc:
        result.update(
            status="PARTIAL_PRESERVE_ALL_EVIDENCE", error_class=type(exc).__name__, error=str(exc)
        )
    finally:
        # Reap the actual owned supervisor/client before daemon absence checks.
        cleanup_errors = []
        if engine is not None:
            try:
                capture._stop(engine, grace_s=12)
            except BaseException as exc:
                cleanup_errors.append("owned supervisor: " + type(exc).__name__)
        if log is not None:
            log.close()
        try:
            result["cleanup"] = capture.cleanup_container(
                name=name, owner=owner, image_id=args.image_id, deadline=end - 30, env=env
            )
        except BaseException as exc:
            cleanup_errors.append("exact container: " + type(exc).__name__)
            result["cleanup"] = {"owned_container_absent": False}
        wait = min(time.monotonic() + 3, end - 30)
        while server.study_active and time.monotonic() < wait:
            time.sleep(0.05)
        server.shutdown()
        serving.join(2)
        server.server_close()
        signal.signal(signal.SIGTERM, old_term)
        if cleanup_errors:
            result.update(status="PARTIAL_CLEANUP_UNCONFIRMED", cleanup_errors=cleanup_errors)
        result["proxy_active_requests_at_stop"] = sorted(server.study_active)
        result["finished_utc"] = datetime.now(UTC).isoformat()
        keep(args.out / "STUDY-RESULT.json", result)
        exported = export(args.out, args.export)
        print(json.dumps({"result": result, "export": exported}), flush=True)
    return 0 if result["status"] == "CAPTURED_DIAGNOSTIC_NOT_OFFICIAL_GRADE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
