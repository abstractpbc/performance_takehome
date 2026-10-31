"""source_commit must name the code that produced a row: HEAD + dirty flag + tree hash."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run

from . import _paths


def _git(repo: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
    }
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    ).stdout.strip()


class ProvenanceTest(unittest.TestCase):
    def test_tree_hash_is_deterministic_and_content_sensitive(self) -> None:
        task_dir = _paths.HARNESS_DIR.parent
        first = run.harness_tree_sha256(task_dir)
        self.assertEqual(first, run.harness_tree_sha256(task_dir))
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp)
            (fake / "harness").mkdir()
            (fake / "harness" / "run.py").write_text("a", encoding="utf-8")
            before = run.harness_tree_sha256(fake)
            (fake / "harness" / "run.py").write_text("b", encoding="utf-8")
            self.assertNotEqual(before, run.harness_tree_sha256(fake))

    def test_dirty_detection_ignores_evidence_outputs_but_not_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _git(repo, "init", "-q")
            code = repo / "tasks" / "harness-design" / "harness"
            code.mkdir(parents=True)
            (code / "run.py").write_text("x", encoding="utf-8")
            _git(repo, "add", ".")
            _git(repo, "commit", "-q", "-m", "init")
            head, dirty = run._git_describe(repo)
            self.assertEqual(head, _git(repo, "rev-parse", "HEAD"))
            self.assertFalse(dirty)
            (code / "results.tsv").write_text("ts\n", encoding="utf-8")
            (code / "reports").mkdir()
            (code / "reports" / "001.json").write_text("{}", encoding="utf-8")
            self.assertTrue(
                run._git_describe(repo)[1], "outputs count as dirty unless this run owns them"
            )
            owned = (
                "tasks/harness-design/harness/results.tsv",
                "tasks/harness-design/harness/reports/",
            )
            self.assertFalse(run._git_describe(repo, owned)[1], "configured outputs are ignored")
            (code / "run.py").write_text("changed", encoding="utf-8")
            self.assertTrue(run._git_describe(repo, owned)[1], "edited code is dirty")

    def test_tracked_evidence_is_detected_and_defaults_avoid_it(self) -> None:
        tracked = run._tracked_outputs(_paths.REPO_ROOT, [_paths.HARNESS_DIR / "results.tsv"])
        self.assertEqual(tracked, ["tasks/harness-design/harness/results.tsv"])
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(run._tracked_outputs(_paths.REPO_ROOT, [Path(tmp) / "r.tsv"]), [])
        args = run.build_parser().parse_args(
            [
                "run",
                "--engine",
                "standin",
                "--model-dir",
                "/m",
                "--out",
                "/tmp/o.json",
                "--state-dir",
                "/tmp/s",
                "--budget-minutes",
                "1",
                "--api-spend-limit-usd",
                "0",
            ]
        )
        self.assertIsNone(args.results)
        self.assertIsNone(args.reports_dir)
        self.assertIsNone(args.patches_dir)

    def test_run_refuses_to_overwrite_committed_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            code = run.cmd_run(
                run.build_parser().parse_args(
                    [
                        "run",
                        "--engine",
                        "standin",
                        "--model-dir",
                        tmp,
                        "--out",
                        f"{tmp}/o.json",
                        "--state-dir",
                        f"{tmp}/s",
                        "--budget-minutes",
                        "1",
                        "--api-spend-limit-usd",
                        "0",
                        "--experiments",
                        f"{tmp}/q.json",
                        "--results",
                        str(_paths.HARNESS_DIR / "results.tsv"),
                    ]
                )
            )
            self.assertEqual(code, 2)
            self.assertFalse((Path(tmp) / "s").exists(), "refused before touching disk")

    def test_historical_manifest_tree_hash_matches_its_source_commit(self) -> None:
        import io
        import json
        import tarfile

        task_dir = _paths.HARNESS_DIR.parent
        sub = json.loads(
            (task_dir / "submission.cpu-standin-legacy.json").read_text(encoding="utf-8")
        )
        source = sub["source"]
        # This immutable artifact keeps the label written by its historical source.
        # Newly generated manifests use a conservative store-independent label.
        self.assertEqual(source["image_identity_kind"], "docker-image-config-id")
        commit = source["commit"].removesuffix("-dirty")
        try:
            archive = subprocess.run(
                ["git", "-C", str(_paths.REPO_ROOT), "archive", commit, "tasks/harness-design"],
                check=True,
                capture_output=True,
                timeout=60,
            ).stdout
        except (OSError, subprocess.SubprocessError) as exc:
            self.skipTest(f"git archive {commit} unavailable: {exc}")
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
                tar.extractall(tmp, filter="data")
            expected = run.harness_tree_sha256(Path(tmp) / "tasks" / "harness-design")
        self.assertEqual(source["harness_tree_sha256"], expected)
        prov = json.loads((_paths.HARNESS_DIR / "reports" / "build-provenance.json").read_text())
        self.assertEqual(prov["harness_tree_sha256"], expected)
        self.assertEqual(prov["source_commit"], source["commit"])

    def test_committed_agent_provenance_carries_the_pi_pins(self) -> None:
        import json

        pins = json.loads((_paths.REPO_ROOT / "agent-harness" / "profiles.json").read_text())["pi"]
        for name in ("agent-provenance.json",):
            agent = json.loads((_paths.HARNESS_DIR / "reports" / name).read_text())
            self.assertEqual(agent["pi_integrity"], pins["artifact_integrity"])
            self.assertEqual(agent["pi_node_requirement"], pins["node_requirement"])
            self.assertEqual(agent["pi_version"], pins["version"])
        sub = json.loads(
            (_paths.HARNESS_DIR.parent / "submission.cpu-standin-legacy.json").read_text()
        )
        self.assertEqual(sub["agent_harness"]["base_version"], pins["version"])
        self.assertEqual(sub["agent_harness"]["base_package"], pins["package"])
        linked = _paths.REPO_ROOT / sub["agent_harness"]["provenance_path"]
        self.assertEqual(json.loads(linked.read_text())["pi_integrity"], pins["artifact_integrity"])

    def test_historical_live_manifest_binds_actual_baseline_bytes_source_and_image(self) -> None:
        import csv
        import hashlib
        import json

        task_dir = _paths.HARNESS_DIR.parent
        current = json.loads(
            (task_dir / "submission.live-mi355x-20261026.json").read_text(encoding="utf-8")
        )
        evidence = current["evidence"]
        report_path = _paths.REPO_ROOT / evidence["baseline_report"]
        report_bytes = report_path.read_bytes()
        self.assertEqual(
            hashlib.sha256(report_bytes).hexdigest(), evidence["baseline_report_sha256"]
        )
        report = json.loads(report_bytes)
        results_path = _paths.REPO_ROOT / evidence["baseline_results"]
        with results_path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream, delimiter="\t"))
        self.assertEqual(len(rows), 1)
        baseline = rows[0]
        self.assertEqual(baseline["source_commit"], current["source"]["tested_code_commit"])
        self.assertEqual(baseline["image_digest"], current["image"])
        self.assertEqual(report["build"]["image_id"], current["image"])
        self.assertTrue(report["measurement"]["gates_passed"])
        self.assertEqual(report["kind"], "baseline")
        self.assertEqual(current["image_identity_kind"], "image-index-digest")
        self.assertNotEqual(
            evidence["historical_cpu_evidence_manifest"], "tasks/harness-design/submission.json"
        )

    def test_run_refuses_dirty_tree_without_allow_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(run, "_git_describe", return_value=("abc", True)):
                code = run.cmd_run(
                    run.build_parser().parse_args(
                        [
                            "run",
                            "--engine",
                            "standin",
                            "--model-dir",
                            str(root),
                            "--experiments",
                            str(_paths.HARNESS_DIR / "experiments" / "standin-queue.json"),
                            "--state-dir",
                            str(root / "state"),
                            "--out",
                            str(root / "out.json"),
                            "--results",
                            str(root / "results.tsv"),
                            "--gpus",
                            "0",
                            "--budget-minutes",
                            "120",
                            "--api-spend-limit-usd",
                            "0",
                        ]
                    )
                )
            self.assertEqual(code, 2)
            self.assertFalse((root / "results.tsv").exists())
