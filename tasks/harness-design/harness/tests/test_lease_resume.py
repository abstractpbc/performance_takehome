"""A resumed harness must consume the original allocation, not renew it."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from abstract_harness.budget import gpu_hours_used
from abstract_harness.engines import LlmdEngine
from abstract_harness.loop import Harness
from abstract_harness.state import HarnessState, save_state

from . import _paths
from .test_recovery import make_harness


class LeaseResumeTest(unittest.TestCase):
    def _resume(self, root: Path, *, deadline: float) -> Harness:
        template = make_harness(root / "template")
        config = replace(
            template.config,
            state_dir=root / "state",
            results_path=root / "results.tsv",
            budget=replace(
                template.config.budget,
                gpus_allocated=1,
                lease_start_ts=1900.0,
                lease_deadline_ts=5500.0,
            ),
        )
        engine, proposer = template.engine, template.proposer
        template._release_locks()
        save_state(
            root / "state" / "state.json",
            HarnessState(lease_start_ts=100.0, lease_deadline_ts=deadline),
        )
        return Harness(config, engine, proposer, log=lambda _m: None)

    def test_restart_preserves_start_deadline_and_elapsed_gpu_hours(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = self._resume(Path(tmp), deadline=3700.0)
            try:
                self.assertEqual(harness.config.budget.lease_start_ts, 100.0)
                self.assertEqual(harness.config.budget.lease_deadline_ts, 3700.0)
                self.assertEqual(harness.state.lease_deadline_ts, 3700.0)
                self.assertEqual(gpu_hours_used(harness.config.budget, 1900.0), 0.5)
            finally:
                harness._release_locks()

    def test_legacy_checkpoint_uses_original_start_with_requested_duration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = self._resume(Path(tmp), deadline=0.0)
            try:
                self.assertEqual(harness.config.budget.lease_deadline_ts, 3700.0)
                self.assertEqual(gpu_hours_used(harness.config.budget, 1900.0), 0.5)
            finally:
                harness._release_locks()


class LlmdBuildContextTest(unittest.TestCase):
    def test_nested_dockerfile_keeps_its_path_in_copied_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = make_harness(root)
            harness.engine = LlmdEngine.from_lock(_paths.REPO_ROOT, root / "model")
            scratch = root / "copied-context"
            try:
                with mock.patch("abstract_harness.loop.docker.build_image") as build:
                    harness.build(scratch, "test:llmd", root / "build.log")
                self.assertEqual(
                    build.call_args.kwargs["dockerfile"],
                    scratch / "tasks" / "harness-design" / "Dockerfile",
                )
                self.assertTrue(harness.engine.spec.dockerfile.is_file())
            finally:
                harness._release_locks()
