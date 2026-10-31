"""Reject recursive LLMD snapshot configurations before creating run state."""

from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run
from abstract_harness.budget import BudgetConfig
from abstract_harness.engines import LlmdEngine
from abstract_harness.loop import Harness, LoopConfig
from abstract_harness.proposers import ScriptedProposer

from . import _paths


class StateLocationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.engine = LlmdEngine.from_lock(
            self.repo, self.root / "model", lock_path=_paths.REPO_ROOT / "baseline/lock.json"
        )

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, state):
        return LoopConfig(
            repo_root=self.repo,
            state_dir=state,
            results_path=state / "results.tsv",
            reports_dir=state / "reports",
            patches_dir=state / "patches",
            benchmark_path=_paths.HARNESS_DIR / "development-benchmark.json",
            program_path=_paths.HARNESS_DIR / "program.md",
            out_manifest=self.root / "out.json",
            source_commit="frozen",
            budget=BudgetConfig(0.0, 1.0, 0, 0.0, 10**12, 0.0),
        )

    def test_llmd_constructor_rejects_equal_nested_and_symlink_resolved_state_before_io(self):
        alias = self.root / "alias"
        alias.symlink_to(self.repo, target_is_directory=True)
        derived_context = self.root / "contexts"
        derived_context.mkdir()
        derived_engine = LlmdEngine.from_lock(
            derived_context, self.root / "model", lock_path=_paths.REPO_ROOT / "baseline/lock.json"
        )
        cases = [
            (self.engine, path)
            for path in (self.repo, self.repo / "new-state", alias / "new-state")
        ]
        cases.append((derived_engine, self.root))
        for engine, state in cases:
            with self.subTest(state=state):
                before = set(engine.spec.build_context.rglob("*"))
                with (
                    mock.patch("abstract_harness.loop.procs.acquire_lock") as lock,
                    mock.patch("abstract_harness.loop.docker.build_image") as build,
                ):
                    with self.assertRaisesRegex(ValueError, "state-dir must be outside"):
                        Harness(self.config(state), engine, ScriptedProposer([]))
                lock.assert_not_called()
                build.assert_not_called()
                self.assertEqual(set(engine.spec.build_context.rglob("*")), before)
                self.assertFalse((self.repo / "new-state").exists())

    def test_llmd_cli_rejects_nested_state_before_proposer_harness_or_disk_work(self):
        state = self.repo / "new-state"
        out = self.root / "out.json"
        args = run.build_parser().parse_args(
            [
                "run",
                "--engine",
                "llmd",
                "--model-dir",
                str(self.root / "model"),
                "--state-dir",
                str(state),
                "--out",
                str(out),
                "--budget-minutes",
                "10",
                "--api-spend-limit-usd",
                "0",
                "--proposer",
                "pi",
            ]
        )
        stderr = io.StringIO()
        with (
            mock.patch.object(run, "REPO_ROOT", self.repo),
            mock.patch.object(run, "_tracked_outputs", return_value=[]),
            mock.patch.object(run, "_git_describe", return_value=("frozen", False)),
            mock.patch.object(run, "make_engine", return_value=self.engine),
            mock.patch.object(run, "Harness") as harness,
            mock.patch.object(run, "PiProposer") as proposer,
            mock.patch("sys.stderr", stderr),
        ):
            self.assertEqual(run.cmd_run(args), 2)
        self.assertIn("state-dir must be outside", stderr.getvalue())
        self.assertFalse(state.exists())
        self.assertFalse(out.exists())
        harness.assert_not_called()
        proposer.assert_not_called()
