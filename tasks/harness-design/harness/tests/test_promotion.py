"""A failed current control must never promote a candidate using stale history."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from abstract_harness import loop, measure, results
from abstract_harness.docker_runtime import BuildResult
from abstract_harness.proposers import Proposal
from abstract_harness.state import ImageRef, load_state

from .test_recovery import make_harness


class PromotionControlTest(unittest.TestCase):
    def _run_failed_control(self, *, image_present: bool) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = make_harness(root)
            self.addCleanup(harness._release_locks)
            context = root / "context"
            context.mkdir()
            (context / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            harness.engine = SimpleNamespace(
                name="standin", spec=SimpleNamespace(build_context=context)
            )
            prior = ImageRef("old:good", "sha256:" + "a" * 64)
            harness.state.last_good = prior
            harness._append_row(
                experiment="baseline", outcome="baseline", kept="1",
                image_digest=prior.digest, development_report="baseline.json",
            )
            harness.state.attempt_index = 1
            # This old control would have made a new fast candidate look like a win.
            harness.state.baseline_samples = {
                "ttft_s": {"a": [10.0] * 5}, "ftgr_tok_s": {"a": [1.0] * 5},
            }
            bad = measure.Measurement(
                "model", 1.0, 5, [], [], [],
                [measure.GateResult("golden_output_match", False, "different text")], False,
            )
            build = BuildResult(
                "new:tag", True, False, 0.1, "sha256:" + "b" * 64,
                "sha256:" + "b" * 64, root / "build.log",
            )
            with (
                mock.patch.object(loop, "apply_patch", return_value=(True, "")),
                mock.patch.object(harness, "build", return_value=build),
                mock.patch.object(harness, "measure_image", return_value=(bad, {})) as measurement,
                mock.patch.object(loop.docker, "image_exists", return_value=image_present),
                mock.patch.object(loop.docker, "remove_image"),
                mock.patch.object(loop.docker, "retag") as retag,
            ):
                outcome = harness.attempt(Proposal("x", "fast", "patch"))
            self.assertEqual(outcome, "error")
            self.assertEqual(measurement.call_count, int(image_present))
            retag.assert_not_called()
            self.assertEqual(harness.state.last_good, prior)
            rows = results.validate_file(harness.config.results_path)
            self.assertEqual(len(rows), 2)
            self.assertEqual((rows[1].outcome, rows[1].kept), ("error", "0"))
            self.assertIn("last-good control", rows[1].notes)
            report = json.loads(next(harness.config.reports_dir.glob("*.json")).read_text())
            self.assertNotIn("measurement", report)
            if image_present:
                self.assertFalse(report["baseline_measurement"]["gates_passed"])
            state = load_state(harness.state_path)
            self.assertIsNotNone(state)
            assert state is not None
            self.assertEqual(state.last_good, prior)
            self.assertIsNone(state.in_progress)

    def test_invalid_current_control_reverts_without_measuring_candidate(self) -> None:
        self._run_failed_control(image_present=True)

    def test_missing_current_control_reverts_without_stale_comparison(self) -> None:
        self._run_failed_control(image_present=False)
