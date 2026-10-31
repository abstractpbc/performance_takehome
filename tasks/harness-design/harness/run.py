"""Entrypoint for the Task 2 candidate harness.

Example (CPU stand-in, scripted proposer, no API key needed):

    uv run --frozen python tasks/harness-design/harness/run.py run \
        --engine standin --model-dir /path/to/standin-model \
        --experiments tasks/harness-design/harness/experiments/standin-queue.json \
        --state-dir /tmp/harness-state --budget-minutes 120 --api-spend-limit-usd 0 \
        --out /tmp/harness-state/submission.json \
        --results tasks/harness-design/harness/results.tsv

Example (real target, MI355X + coding-model key):

    PI_BIN=/path/to/node_modules/.bin/pi ANTHROPIC_API_KEY=... \
    uv run --frozen python tasks/harness-design/harness/run.py run \
        --engine llmd --model-dir /models/Qwen3.6-27B --proposer pi \
        --api-key-env ANTHROPIC_API_KEY \
        --baseline baseline/lock.json --budget-minutes 360 --api-spend-limit-usd 200 \
        --gpu-hours 64 --out tasks/harness-design/submission.json \
        --results tasks/harness-design/harness/results.tsv

Re-running the same command with the same ``--state-dir`` resumes from disk.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from abstract_harness import budget as budget_mod  # noqa: E402
from abstract_harness import results as results_mod  # noqa: E402
from abstract_harness.engines import make_engine  # noqa: E402
from abstract_harness.loop import (  # noqa: E402
    Harness,
    LoopConfig,
    Timeouts,
    validate_state_directory,  # noqa: E402
)
from abstract_harness.procs import LockError  # noqa: E402
from abstract_harness.proposers import PiLaunchConfig, PiProposer, ScriptedProposer  # noqa: E402
from abstract_harness.state import StateError, load_state  # noqa: E402

STANDIN_MODEL = {
    "repo": "unsloth/SmolLM2-135M-Instruct-GGUF",
    "revision": "9e6855bc4be717fca1ef21360a1db4b29d5c559a",
    "filename": "SmolLM2-135M-Instruct-Q8_0.gguf",
    "sha256": "c4a3dd037301b6ecea31d6da37f5cd793ead920dd5ddfe6d589294628d6ce66a",
    "size": 144811072,
}


def _git_describe(repo_root: Path, ignore: tuple[str, ...] = ()) -> tuple[str, bool]:
    """``(HEAD sha, dirty)`` of the repository that contains the running harness code.

    ``ignore`` lists the repo-relative paths this run is configured to write (results,
    reports, patches, manifest); only those may be modified without counting as dirty.
    """
    try:
        head = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(repo_root), "status", "--porcelain", "--", "tasks/harness-design"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN", True
    dirty = any(
        line.strip() and not (ignore and line[3:].startswith(ignore))
        for line in status.splitlines()
    )
    return head, dirty


def _repo_relative(repo_root: Path, path: Path) -> str | None:
    """``path`` relative to ``repo_root`` (POSIX), or None when it lies outside the repo."""
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return None


def _tracked_outputs(repo_root: Path, paths: list[Path]) -> list[str]:
    """Git-tracked files under any of ``paths`` (committed evidence a run would overwrite)."""
    rel = [r for r in (_repo_relative(repo_root, p) for p in paths) if r]
    if not rel:
        return []
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "--", *rel],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [line for line in out.splitlines() if line.strip()]


HARNESS_TREE_FILES = (
    "harness/run.py",
    "harness/abstract_harness",
    "harness/development-benchmark.json",
    "harness/program.md",
    "harness/experiments",
    "Dockerfile",
    "standin/Dockerfile",
)


def harness_tree_sha256(task_dir: Path) -> str:
    """Content hash of the harness code + inputs that produce evidence rows."""
    digest = hashlib.sha256()
    for entry in HARNESS_TREE_FILES:
        base = task_dir / entry
        files = sorted(p for p in base.rglob("*") if p.is_file()) if base.is_dir() else [base]
        for path in files:
            if "__pycache__" in path.parts or not path.exists():
                continue
            digest.update(path.relative_to(task_dir).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


def build_parser() -> argparse.ArgumentParser:
    """CLI definition."""
    parser = argparse.ArgumentParser(
        prog="run.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run (or resume) the optimization loop")
    run.add_argument("--engine", choices=["llmd", "standin"], required=True)
    run.add_argument(
        "--model-dir", type=Path, required=True, help="directory mounted read-only at /models/model"
    )
    run.add_argument(
        "--baseline",
        type=Path,
        default=REPO_ROOT / "baseline" / "lock.json",
        help="pinned LLMD lock (llmd engine)",
    )
    run.add_argument(
        "--development-benchmark", type=Path, default=HERE / "development-benchmark.json"
    )
    run.add_argument("--program", type=Path, default=HERE / "program.md")
    run.add_argument(
        "--results",
        type=Path,
        default=None,
        help="results.tsv to append to (default: <state-dir>/results.tsv)",
    )
    run.add_argument("--out", type=Path, required=True, help="manifest path (submission.json)")
    run.add_argument(
        "--state-dir", type=Path, required=True, help="persistent state (resume) + scratch + logs"
    )
    run.add_argument("--reports-dir", type=Path, default=None, help="default: <state-dir>/reports")
    run.add_argument("--patches-dir", type=Path, default=None, help="default: <state-dir>/patches")
    run.add_argument(
        "--overwrite-evidence",
        action="store_true",
        help="allow --results/--reports-dir/--patches-dir/--out to point at git-tracked "
        "(committed) evidence files; refused otherwise",
    )
    run.add_argument(
        "--manifest-only",
        action="store_true",
        help="do not run attempts: load the state directory and rewrite the manifest, "
        "build-provenance and agent-provenance files from it",
    )
    run.add_argument("--proposer", choices=["scripted", "pi"], default="scripted")
    run.add_argument("--experiments", type=Path, help="scripted proposer queue (JSON)")
    run.add_argument(
        "--api-key-env",
        default="ANTHROPIC_API_KEY",
        help="env var holding the coding-model key (value never logged)",
    )
    run.add_argument("--pi-bin", type=Path, default=Path(os.environ.get("PI_BIN", "pi")))
    run.add_argument("--pi-model", default=None)
    run.add_argument("--pi-provider", default=None)
    run.add_argument(
        "--provider-response-dir",
        type=Path,
        help="external gateway WAL directory; metadata only, no capture or verification",
    )
    run.add_argument(
        "--pi-usd-per-call",
        type=float,
        default=0.0,
        help="ledger estimate per Pi call when Pi emits no cost",
    )
    run.add_argument("--budget-minutes", type=float, required=True, help="lease length from now")
    run.add_argument("--final-reserve-minutes", type=float, default=15.0)
    run.add_argument("--api-spend-limit-usd", type=float, required=True)
    run.add_argument(
        "--gpu-hours",
        type=float,
        default=64.0,
        help="GPU-hour allocation (allocation x elapsed accounting)",
    )
    run.add_argument("--gpus", type=int, default=1, help="GPUs allocated (0 for the CPU stand-in)")
    run.add_argument("--max-attempts", type=int, default=None)
    run.add_argument(
        "--delivery-policy",
        choices=["none", "llmd-config-v1"],
        default="none",
        help=(
            "optional pre-build LLMD configuration-only/one-knob delivery guard; "
            "persisted on resume"
        ),
    )
    run.add_argument("--estimated-attempt-minutes", type=float, default=10.0)
    run.add_argument("--build-timeout-s", type=float, default=1800.0)
    run.add_argument("--startup-timeout-s", type=float, default=300.0)
    run.add_argument("--measure-timeout-s", type=float, default=900.0)
    run.add_argument("--min-effect", type=float, default=0.03)
    run.add_argument("--min-samples", type=int, default=3)
    run.add_argument("--render-device", default="/dev/dri/renderD128")
    run.add_argument("--render-gid", default=None)
    run.add_argument("--standin-cpus", default="4")
    run.add_argument(
        "--no-cache",
        action="store_true",
        help="pass --no-cache to every docker build in this invocation (cold rebuild)",
    )
    run.add_argument("--keep-candidate-images", action="store_true")
    run.add_argument(
        "--allow-dirty",
        action="store_true",
        help="run even if tasks/harness-design has uncommitted code changes "
        "(source_commit is then suffixed with -dirty)",
    )
    run.add_argument(
        "--startup-regression-limit",
        type=float,
        default=1.5,
        help="hard gate: candidate startup slower than this multiple of last-good is regressed",
    )

    val = sub.add_parser("validate-results", help="validate a results.tsv against the contract")
    val.add_argument("--results", type=Path, default=HERE / "results.tsv")

    fetch = sub.add_parser(
        "fetch-standin-model", help="download + verify the pinned stand-in GGUF (network required)"
    )
    fetch.add_argument("--dest", type=Path, required=True)

    retag = sub.add_parser(
        "retag-last-good", help="tag the last-good image recorded in a state dir"
    )
    retag.add_argument("--state-dir", type=Path, required=True)
    retag.add_argument("--tag", required=True)
    return parser


def cmd_run(args: argparse.Namespace) -> int:
    """Build config objects and run the loop."""
    now = time.time()
    args.results = args.results or args.state_dir / "results.tsv"
    args.reports_dir = args.reports_dir or args.state_dir / "reports"
    args.patches_dir = args.patches_dir or args.state_dir / "patches"
    outputs = [args.results, args.reports_dir, args.patches_dir, args.out]
    tracked = _tracked_outputs(REPO_ROOT, outputs)
    if tracked and not args.overwrite_evidence:
        more = f" and {len(tracked) - 1} more" if len(tracked) > 1 else ""
        print(
            f"[run] refusing to write over committed evidence ({tracked[0]}{more}); "
            "point --results/--reports-dir/--patches-dir/--out elsewhere or pass "
            "--overwrite-evidence",
            file=sys.stderr,
        )
        return 2
    ignore = tuple(r for r in (_repo_relative(REPO_ROOT, p) for p in outputs) if r)
    head, dirty = _git_describe(REPO_ROOT, ignore)
    if dirty and not args.allow_dirty:
        print(
            "[run] tasks/harness-design has uncommitted code changes; commit them so every "
            "results.tsv row names the code that produced it, or pass --allow-dirty",
            file=sys.stderr,
        )
        return 2
    source_commit = f"{head}-dirty" if dirty else head
    tree_sha256 = harness_tree_sha256(HERE.parent)
    if args.manifest_only:
        # Rewriting the manifest must describe the code that produced the rows, not HEAD.
        prior = load_state(args.state_dir / "state.json", on_warning=print)
        if prior is None:
            print(f"[run] --manifest-only: no state in {args.state_dir}", file=sys.stderr)
            return 2
        source_commit, tree_sha256 = prior.source_commit, prior.harness_tree_sha256
    budget = budget_mod.BudgetConfig(
        api_usd_limit=args.api_spend_limit_usd,
        gpu_hours_limit=args.gpu_hours,
        gpus_allocated=args.gpus,
        lease_start_ts=now,
        lease_deadline_ts=now + args.budget_minutes * 60,
        final_reserve_s=args.final_reserve_minutes * 60,
    )
    engine_kwargs = (
        {
            "render_device": args.render_device,
            "render_gid": args.render_gid,
            "lock_path": args.baseline,
        }
        if args.engine == "llmd"
        else {"cpus": args.standin_cpus}
    )
    engine = make_engine(args.engine, REPO_ROOT, args.model_dir, **engine_kwargs)
    try:
        validate_state_directory(args.state_dir, engine.spec.build_context)
    except ValueError as exc:
        print(f"[run] {exc}", file=sys.stderr)
        return 2
    profile = json.loads(
        (REPO_ROOT / "agent-harness" / "profiles.json").read_text(encoding="utf-8")
    )["profiles"]["harness-design"]
    if args.proposer == "pi":
        pi_cfg = PiLaunchConfig(
            pi_bin=args.pi_bin,
            agent_dir=args.state_dir / "pi-agent-dir",
            api_key_env=args.api_key_env,
            model=args.pi_model,
            provider=args.pi_provider,
            usd_per_call_estimate=args.pi_usd_per_call,
            provider_response_dir=args.provider_response_dir,
        )
        proposer = PiProposer(pi_cfg, profile=profile)
        if not proposer.api_key_present():
            print(
                f"[run] {args.api_key_env} is not set; falling back to the scripted proposer",
                file=sys.stderr,
            )
            if not args.experiments:
                print("[run] no --experiments queue given for the fallback", file=sys.stderr)
                return 2
            proposer = ScriptedProposer.from_file(args.experiments)
        else:
            print(f"[run] pi distribution: {proposer.verify_distribution()}")
    else:
        if not args.experiments:
            print("[run] --experiments is required for the scripted proposer", file=sys.stderr)
            return 2
        proposer = ScriptedProposer.from_file(args.experiments)
    config = LoopConfig(
        repo_root=REPO_ROOT,
        state_dir=args.state_dir,
        results_path=args.results,
        reports_dir=args.reports_dir,
        patches_dir=args.patches_dir,
        benchmark_path=args.development_benchmark,
        baseline_path=args.baseline if args.engine == "llmd" else None,
        program_path=args.program,
        out_manifest=args.out,
        source_commit=source_commit,
        harness_tree_sha256=tree_sha256,
        startup_regression_limit=args.startup_regression_limit,
        budget=budget,
        timeouts=Timeouts(
            build_s=args.build_timeout_s,
            startup_s=args.startup_timeout_s,
            measure_s=args.measure_timeout_s,
        ),
        max_attempts=args.max_attempts,
        min_effect=args.min_effect,
        no_cache=args.no_cache,
        min_samples=args.min_samples,
        estimated_attempt_s=args.estimated_attempt_minutes * 60,
        keep_candidate_images=args.keep_candidate_images,
        delivery_policy=args.delivery_policy,
    )
    # Harness aligns the scripted queue after interruption recovery while holding its locks.
    try:
        harness = Harness(config, engine, proposer)
    except (StateError, LockError, ValueError) as exc:
        print(f"[run] {exc}", file=sys.stderr)
        return 4
    if args.manifest_only:
        try:
            manifest = harness.finalize(harness.state.finish_reason or "manifest-only")
        finally:
            harness._release_locks()
        print(f"[run] manifest rewritten from {args.state_dir}: image={manifest.get('image')}")
        return 0
    return harness.run()


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate ``results.tsv``; print a per-outcome summary."""
    rows = results_mod.validate_file(args.results)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.outcome] = counts.get(row.outcome, 0) + 1
    print(f"{args.results}: {len(rows)} rows OK; outcomes={json.dumps(counts, sort_keys=True)}")
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    """Download the pinned stand-in model with huggingface_hub and verify its SHA-256."""
    from huggingface_hub import hf_hub_download  # root dependency

    args.dest.mkdir(parents=True, exist_ok=True)
    path = hf_hub_download(
        STANDIN_MODEL["repo"],
        STANDIN_MODEL["filename"],
        revision=STANDIN_MODEL["revision"],
        local_dir=args.dest,
    )
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if digest != STANDIN_MODEL["sha256"]:
        print(f"[fetch] SHA-256 mismatch: {digest}", file=sys.stderr)
        return 1
    target = args.dest / "model.gguf"
    if Path(path).resolve() != target.resolve():
        Path(path).replace(target)
    print(f"[fetch] {target} sha256={digest} size={target.stat().st_size}")
    return 0


def cmd_retag(args: argparse.Namespace) -> int:
    """Retag the last-good image from an existing state file."""
    from abstract_harness import docker_runtime as docker
    from abstract_harness.state import load_state

    state = load_state(args.state_dir / "state.json")
    if state is None or state.last_good is None:
        print("[retag] no last-good image recorded", file=sys.stderr)
        return 1
    source = (
        state.last_good.tag
        if docker.image_exists(state.last_good.tag)
        else state.last_good.image_id or ""
    )
    if not source:
        print("[retag] last-good image is no longer present locally", file=sys.stderr)
        return 1
    docker.retag(source, args.tag)
    print(f"[retag] {source} -> {args.tag} ({state.last_good.digest})")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry."""
    args = build_parser().parse_args(argv)
    return {
        "run": cmd_run,
        "validate-results": cmd_validate,
        "fetch-standin-model": cmd_fetch,
        "retag-last-good": cmd_retag,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
