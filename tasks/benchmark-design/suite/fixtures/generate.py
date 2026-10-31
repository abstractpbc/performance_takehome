"""Generate the public (labeled) and held-out (opaque) event fixtures on the CPU mock.

    cd tasks/benchmark-design/suite && python fixtures/generate.py [--time-scale 0.25] [--only NAME]

For every mock profile the runner cold-starts a mock server via ``--launch-cmd`` (so the
startup axis has real launch evidence), runs ``workload.mock.json`` in ``fixture`` mode and
stores ``run.json`` + ``requests.jsonl.gz`` + the mock's ``oracle.json`` + ``label.json``.
Two families that no server can produce (invalid timing, client lag) are derived from the
clean capture by mutating the recorded evidence. Held-out fixtures reuse a subset of
profiles with fresh seeds under opaque directory names; the key is written to
``~/.benchmark-design/heldout_key.json`` (outside the tree; sha256 committed as
``fixtures/heldout/COMMITMENT.sha256``) and is never read by ``evaluate.py``.
"""

import argparse
import gzip
import hashlib
import json
import random
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

SUITE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SUITE))

from mock_server.faults import PROFILES  # noqa: E402

FIXTURE_DIR = SUITE / "fixtures"
PUBLIC_DIR = FIXTURE_DIR / "public"
HELDOUT_DIR = FIXTURE_DIR / "heldout"
# The held-out key lives OUTSIDE the public tree (revealed after review); only its sha256
# commitment is committed next to the fixtures.
DEFAULT_KEY_PATH = Path.home() / ".benchmark-design" / "heldout_key.json"
COMMITMENT_PATH = HELDOUT_DIR / "COMMITMENT.sha256"
HELDOUT_MUTATIONS = ("timing_invalid", "client_lag", "underpowered")
MOCK_PLAN = FIXTURE_DIR / "workload.mock.json"
MUTATIONS = {
    "timing_invalid": "events in 10% of measured requests re-ordered: non-monotonic timestamps",
    "client_lag": "run.json client_integrity reports a 0.4 s event-loop stall in a measure window",
}
HELDOUT_PROFILES = (
    "clean",
    "noisy",
    "stall",
    "burst",
    "missing_done",
    "wrong_text",
    "false_usage",
    "drop",
    "late_first_token",
    "truncated_text",
)

# What the evaluator is expected to conclude; checked by validation/build_validation.py and
# the tests. "signal" names the gate/metric that must carry the detection.
from fixtures.expectations import EXPECTED  # noqa: E402  (machine-checkable expectations)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_fixture(
    profile: str, seed: int, out: Path, time_scale: float, *, keep_oracle: bool
) -> dict[str, Any]:
    """Cold-start a mock server through the runner and capture one events directory."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    port = free_port()
    oracle_path = out / "oracle.json"
    launch = (
        f"{sys.executable} -m mock_server --profile {profile} --seed {seed} --port {port} "
        f"--time-scale {time_scale} --oracle-dump {oracle_path}"
    )
    cmd = [
        sys.executable,
        "-m",
        "runner",
        "--plan",
        str(MOCK_PLAN),
        "--mode",
        "fixture",
        "--out",
        str(out),
        "--base-url",
        f"http://127.0.0.1:{port}",
        "--time-scale",
        str(time_scale),
        "--assume-mock",
        "--launch-cmd",
        launch,
        "--stop-after-launch",
        "--run-id",
        f"fixture-{profile}-{seed}",
    ]
    started = time.monotonic()
    result = subprocess.run(cmd, cwd=SUITE, capture_output=True, text=True, check=False)
    wall = time.monotonic() - started
    if result.returncode not in (0, 1):
        raise RuntimeError(f"runner failed for {profile}: {result.stderr[-2000:]}")
    for _ in range(50):
        if oracle_path.exists():
            break
        time.sleep(0.1)
    raw = out / "requests.jsonl"
    with raw.open("rb") as src, gzip.open(out / "requests.jsonl.gz", "wb", compresslevel=9) as dst:
        shutil.copyfileobj(src, dst)
    raw.unlink()
    if oracle_path.exists():
        if keep_oracle:
            with (
                oracle_path.open("rb") as src,
                gzip.open(out / "oracle.json.gz", "wb", compresslevel=9) as dst,
            ):
                shutil.copyfileobj(src, dst)
        oracle_path.unlink()
    return {
        "profile": profile,
        "seed": seed,
        "runner_exit": result.returncode,
        "wall_s_cpu_mock": round(wall, 2),
    }


def mutate(clean_dir: Path, mutation: str, out: Path, seed: int) -> None:
    """Derive an evidence-corruption fixture from the clean capture."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    run = json.loads((clean_dir / "run.json").read_text(encoding="utf-8"))
    run["run_id"] = f"fixture-{mutation}-{seed}"
    with gzip.open(clean_dir / "requests.jsonl.gz", "rt", encoding="utf-8") as fh:
        records = [json.loads(line) for line in fh if line.strip()]
    rng = random.Random(seed)
    for record in records:
        record["run_id"] = run["run_id"]
    if mutation == "timing_invalid":
        measured = [r for r in records if r["phase"] == "measure" and len(r["events"]) > 4]
        for record in rng.sample(measured, max(1, len(measured) // 10)):
            i = rng.randrange(1, len(record["events"]) - 1)
            record["events"][i]["t"], record["events"][i + 1]["t"] = (
                record["events"][i + 1]["t"],
                record["events"][i]["t"],
            )
    elif mutation == "underpowered":
        # Keep only the first 5 measure requests of every unit: no cell can reach
        # min_requests_per_cell, so the only honest verdict is *inconclusive*.
        kept: dict[str, int] = {}
        survivors = []
        for record in records:
            if record["phase"] != "measure":
                survivors.append(record)
                continue
            kept[record["unit_id"]] = kept.get(record["unit_id"], 0) + 1
            if kept[record["unit_id"]] <= 5:
                survivors.append(record)
        records[:] = survivors
    elif mutation == "client_lag":
        window = next(p for p in run["phases"] if p["name"] == "measure")
        ci = run["client_integrity"]
        ci["event_loop_lag_max_s"] = 0.4
        ci["event_loop_lag_max_at_mono_s"] = (window["start"] + window["end"]) / 2
        ci.setdefault("event_loop_lag_spikes", []).append(
            {"t": ci["event_loop_lag_max_at_mono_s"], "lag_s": 0.4}
        )
    else:
        raise ValueError(mutation)
    (out / "run.json").write_text(json.dumps(run, indent=2, ensure_ascii=False), encoding="utf-8")
    with gzip.open(out / "requests.jsonl.gz", "wt", encoding="utf-8", compresslevel=9) as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def opaque_name(profile: str, seed: int, salt: str) -> str:
    return "hx-" + hashlib.sha256(f"{salt}|{profile}|{seed}".encode()).hexdigest()[:12]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--time-scale", type=float, default=1.0)
    parser.add_argument("--only", help="generate only this profile/mutation (public set)")
    parser.add_argument("--skip-heldout", action="store_true")
    parser.add_argument(
        "--heldout-salt",
        default=None,
        help="salt for opaque names; default: fresh random hex, stored only in the key file",
    )
    parser.add_argument(
        "--heldout-only", action="store_true", help="regenerate only the held-out set"
    )
    parser.add_argument(
        "--key-out",
        type=Path,
        default=DEFAULT_KEY_PATH,
        help="held-out key destination, outside the public tree (default ~/.benchmark-design/)",
    )
    args = parser.parse_args(argv)
    manifest_path = FIXTURE_DIR / "manifest.json"
    manifest: dict[str, Any] = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    )
    manifest.update(
        {
            "time_scale": args.time_scale,
            "public": manifest.get("public", {}),
            "heldout_count": manifest.get("heldout_count", 0),
            "note": "CPU mock fixtures; injected timings, not GPU measurements.",
        }
    )
    public_profiles = [] if args.heldout_only else list(PROFILES)
    for index, profile in enumerate(public_profiles):
        if args.only and args.only != profile:
            continue
        seed = 100 + index
        out = PUBLIC_DIR / profile
        info = run_fixture(profile, seed, out, args.time_scale, keep_oracle=True)
        label = {
            **info,
            "family": PROFILES[profile].family,
            "description": PROFILES[profile].description,
            "expected": EXPECTED[profile],
        }
        (out / "label.json").write_text(json.dumps(label, indent=2), encoding="utf-8")
        manifest["public"][profile] = info
        print(json.dumps(info), flush=True)
    clean_dir = PUBLIC_DIR / "clean"
    for index, (mutation, description) in enumerate(MUTATIONS.items()):
        if args.only and args.only != mutation:
            continue
        out = PUBLIC_DIR / mutation
        mutate(clean_dir, mutation, out, 900 + index)
        label = {
            "profile": mutation,
            "seed": 900 + index,
            "family": "evidence_corruption",
            "derived_from": "clean",
            "description": description,
            "expected": EXPECTED[mutation],
        }
        (out / "label.json").write_text(json.dumps(label, indent=2), encoding="utf-8")
        manifest["public"][mutation] = {"derived_from": "clean"}
        print(json.dumps({"mutation": mutation}), flush=True)
    if not args.skip_heldout and not args.only:
        if HELDOUT_DIR.exists():
            shutil.rmtree(HELDOUT_DIR)
        salt = args.heldout_salt or secrets.token_hex(16)
        key: dict[str, Any] = {"_salt": salt}
        for index, profile in enumerate(HELDOUT_PROFILES):
            seed = 500 + index
            name = opaque_name(profile, seed, salt)
            with tempfile.TemporaryDirectory() as tmp:
                info = run_fixture(
                    profile, seed, Path(tmp) / name, args.time_scale, keep_oracle=False
                )
                shutil.copytree(Path(tmp) / name, HELDOUT_DIR / name)
            key[name] = {**info, "family": PROFILES[profile].family, "expected": EXPECTED[profile]}
            print(
                json.dumps({"heldout": name, "wall_s_cpu_mock": info["wall_s_cpu_mock"]}),
                flush=True,
            )
        clean_name = next(n for n, v in key.items() if n != "_salt" and v["profile"] == "clean")
        for index, mutation in enumerate(HELDOUT_MUTATIONS):
            seed = 900 + index
            name = opaque_name(mutation, seed, salt)
            mutate(HELDOUT_DIR / clean_name, mutation, HELDOUT_DIR / name, seed)
            key[name] = {
                "profile": mutation,
                "seed": seed,
                "derived_from": clean_name,
                "family": "evidence_corruption" if mutation != "underpowered" else "power",
                "expected": EXPECTED[mutation],
            }
            print(json.dumps({"heldout": name, "mutation": True}), flush=True)
        key_text = json.dumps(key, indent=2, sort_keys=True)
        args.key_out.parent.mkdir(parents=True, exist_ok=True)
        args.key_out.write_text(key_text, encoding="utf-8")
        COMMITMENT_PATH.write_text(
            hashlib.sha256(key_text.encode("utf-8")).hexdigest() + "  heldout_key.json\n",
            encoding="utf-8",
        )
        manifest["heldout_count"] = len(key) - 1
        manifest["heldout_key_sha256"] = hashlib.sha256(key_text.encode("utf-8")).hexdigest()
        manifest["heldout_key_location"] = (
            "outside the public tree (revealed after review); "
            "see fixtures/heldout/COMMITMENT.sha256"
        )
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
