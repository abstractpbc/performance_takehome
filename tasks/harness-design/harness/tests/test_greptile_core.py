"""Crash consistency, complete populations, and per-run isolation regressions."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from abstract_harness.decision import decide
from abstract_harness.docker_runtime import BuildResult
from abstract_harness.engines import StandinEngine
from abstract_harness.loop import Interrupted
from abstract_harness.manifest import build_manifest
from abstract_harness.measure import Measurement
from abstract_harness.procs import LockError
from abstract_harness.proposers import (
    Proposal,
    ProposerError,
    ScriptedProposer,
    unified_diff_for_dirs,
)
from abstract_harness.state import ImageRef, InProgress, StateError, load_state, save_state

from . import _paths
from .test_recovery import make_harness


class ContextDiffTest(unittest.TestCase):
    def test_git_metadata_and_unchanged_binary_do_not_break_a_text_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            old, new = Path(tmp) / "old", Path(tmp) / "new"
            for root in (old, new):
                (root / ".git/objects").mkdir(parents=True)
                (root / ".git/objects/binary").write_bytes(b"\xff\x00")
                (root / "engine.bin").write_bytes(b"\xff\x01")
            (old / "Dockerfile").write_text("FROM scratch\nLABEL state=old\n")
            (new / "Dockerfile").write_text("FROM scratch\nLABEL state=new\n")
            (new / ".git/objects/binary").write_bytes(b"\xfe\x00")
            patch = unified_diff_for_dirs(old, new)
            self.assertIn("+LABEL state=new", patch)
            self.assertNotIn(".git", patch)
            self.assertNotIn("engine.bin", patch)
            (new / "engine.bin").write_bytes(b"\xfe\x01")
            with self.assertRaisesRegex(ProposerError, "binary changes"):
                unified_diff_for_dirs(old, new)
            (old / "engine.bin").write_bytes(b"\0old")
            (new / "engine.bin").write_bytes(b"\0new")
            with self.assertRaisesRegex(ProposerError, "binary changes"):
                unified_diff_for_dirs(old, new)


class CompletePopulationTest(unittest.TestCase):
    def test_matching_missing_prompt_on_both_arms_cannot_keep(self):
        baseline = {"ttft_s": {"a": [2.0] * 5}, "ftgr_tok_s": {"a": [10.0] * 5}}
        candidate = {"ttft_s": {"a": [1.0] * 5}, "ftgr_tok_s": {"a": [20.0] * 5}}
        result = decide(
            baseline, candidate, gates_passed=True, expected_samples={"a": 5, "missing": 5}
        )
        self.assertFalse(result.kept)
        self.assertIn("configured prompt/repetition", result.reason)

    def test_matching_missing_repetitions_on_both_arms_cannot_keep(self):
        baseline = {"ttft_s": {"a": [2.0] * 3}, "ftgr_tok_s": {"a": [10.0] * 3}}
        candidate = {"ttft_s": {"a": [1.0] * 3}, "ftgr_tok_s": {"a": [20.0] * 3}}
        self.assertFalse(
            decide(baseline, candidate, gates_passed=True, expected_samples={"a": 5}).kept
        )
        self.assertTrue(
            decide(baseline, candidate, gates_passed=True, expected_samples={"a": 3}).kept
        )


class RecoveryAndIsolationTest(unittest.TestCase):
    def test_copied_state_cannot_run_the_same_docker_namespace_concurrently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = make_harness(root / "a")
            try:
                save_state(root / "b/state/state.json", first.state)
                with self.assertRaises(LockError):
                    make_harness(root / "b")
            finally:
                first._release_locks()

    def test_scripted_queue_aligns_after_interrupted_attempt_recovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = make_harness(Path(tmp))
            harness.state.attempt_index = 7
            harness.state.in_progress = InProgress("007-q6", "h", 1.0, "", attempt_index=7)
            harness.proposer = ScriptedProposer(
                [{"experiment": f"q{i}", "patch": "patch"} for i in range(12)], skip=6
            )
            harness.config.max_attempts = 1
            with (
                mock.patch.object(harness, "install_signal_handlers"),
                mock.patch.object(harness, "establish_baseline"),
                mock.patch.object(harness, "_proposer_context", return_value=None),
                mock.patch.object(harness, "attempt") as attempt,
                mock.patch.object(harness, "finalize"),
                mock.patch.object(harness, "cleanup"),
                mock.patch("abstract_harness.loop.docker.kill_containers_with_prefix"),
            ):
                self.assertEqual(harness.run(), 0)
            self.assertEqual(attempt.call_args.args[0].experiment, "q7")

    def test_separate_runs_have_distinct_docker_names_and_cleanup_holds_locks(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = make_harness(Path(tmp) / "a")
            second = make_harness(Path(tmp) / "b")
            try:
                for key in ("container_prefix", "image_prefix", "network_name"):
                    self.assertNotEqual(getattr(first.config, key), getattr(second.config, key))

                def cleanup():
                    self.assertIsNotNone(first._lock)
                    self.assertFalse(first._lock.closed)

                with (
                    mock.patch.object(first, "install_signal_handlers"),
                    mock.patch.object(first, "recover_from_previous_run"),
                    mock.patch.object(first, "establish_baseline"),
                    mock.patch.object(first, "_proposer_context", return_value=None),
                    mock.patch.object(first, "finalize"),
                    mock.patch.object(first, "cleanup", side_effect=cleanup),
                ):
                    self.assertEqual(first.run(), 0)
                self.assertIsNone(first._lock)
            finally:
                first._release_locks()
                second._release_locks()

    def test_budget_clock_advances_when_wall_time_goes_backwards(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness = make_harness(Path(tmp))
            try:
                harness._budget_wall_origin = 100.0
                harness._budget_monotonic_origin = 10.0
                harness.state.last_clock_ts = 100.0
                harness.clock = lambda: 90.0
                with mock.patch("abstract_harness.loop.time.monotonic", return_value=25.0):
                    self.assertEqual(harness.now(), 115.0)
            finally:
                harness._release_locks()

    def test_restart_with_clock_behind_saved_accounting_time_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = make_harness(root)
            harness.state.last_clock_ts = 10**12
            harness._save()
            harness._release_locks()
            with self.assertRaisesRegex(StateError, "clock is behind"):
                make_harness(root)


class PromotionTransactionTest(unittest.TestCase):
    def prepare(self, root):
        harness = make_harness(root)
        old, new = root / "old-context", root / "new-context"
        old.mkdir()
        new.mkdir()
        (old / "Dockerfile").write_text("old image context")
        (new / "Dockerfile").write_text("new image context")
        harness.state.last_good = ImageRef(
            "old:image", "sha256:" + "a" * 64, experiment="baseline", context_path=str(old)
        )
        harness.state.attempt_index = 3
        harness.state.in_progress = InProgress("003-new", "h", 1.0, "new.patch", attempt_index=3)
        harness._save()
        reference = ImageRef(
            "new:image", "sha256:" + "b" * 64, experiment="003-new", patch="new.patch"
        )
        reference.context_path = str(harness._promote_context(new))
        row = dict(
            experiment="003-new",
            image_digest=reference.digest,
            kept="1",
            outcome="improved",
            local_ttft_speedup="1.2",
            local_ftgr_speedup="1.2",
            hypothesis="h",
            notes="passed",
            development_report="new.json",
            patch_path="new.patch",
        )
        return harness, reference, row, old

    def test_prepared_context_does_not_replace_saved_context_and_save_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness, reference, row, old = self.prepare(Path(tmp))
            try:
                self.assertEqual(harness.last_good_context, old)
                with (
                    mock.patch("abstract_harness.loop.docker.retag"),
                    mock.patch.object(harness, "_save", side_effect=OSError("disk failed")),
                ):
                    with self.assertRaises(OSError):
                        harness._commit_promotion(reference, {}, row)
                self.assertEqual(harness.state.last_good.tag, "old:image")
                self.assertEqual(harness.last_good_context, old)
                self.assertEqual((old / "Dockerfile").read_text(), "old image context")
                self.assertEqual(load_state(harness.state_path).last_good.tag, "old:image")
            finally:
                harness._release_locks()

    def test_crash_after_promotion_checkpoint_replays_keep_with_matching_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness, reference, row, old = self.prepare(root)
            with mock.patch("abstract_harness.loop.docker.retag"):
                harness._commit_promotion(reference, {"ttft_s": {"p": [1.0]}}, row)
            harness._release_locks()
            resumed = make_harness(root)
            try:
                resumed.recover_from_previous_run()
                self.assertEqual(resumed.state.last_good.tag, "new:image")
                self.assertEqual(
                    (resumed.last_good_context / "Dockerfile").read_text(), "new image context"
                )
                self.assertEqual((old / "Dockerfile").read_text(), "old image context")
                self.assertIsNone(resumed.state.in_progress)
                self.assertEqual(resumed.state.history[-1]["kept"], "1")
                self.assertEqual(resumed.state.attempt_index, 4)
            finally:
                resumed._release_locks()

    def test_successful_attempt_keeps_image_and_accumulated_context_together(self):
        self._attempt_fixture()

    def test_errors_immediately_after_durable_commit_cannot_write_a_reverted_row(self):
        for failure in (OSError, SystemExit, Interrupted):
            with self.subTest(failure=failure):
                self._attempt_fixture(failure_after_commit=failure)

    def test_recovery_blocks_a_row_that_contradicts_the_committed_keep(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness, reference, row, _ = self.prepare(root)
            with mock.patch("abstract_harness.loop.docker.retag"):
                harness._commit_promotion(reference, {}, row)
            harness._append_row(**{**row, "kept": "0", "outcome": "no_change"})
            harness._release_locks()
            resumed = make_harness(root)
            try:
                with self.assertRaisesRegex(RuntimeError, "row conflicts"):
                    resumed.recover_from_previous_run()
                self.assertEqual(resumed.state.last_good, reference)
                self.assertEqual(resumed.state.in_progress.stage, "promoted")
            finally:
                resumed._release_locks()

    def test_save_error_after_durable_replace_preserves_the_committed_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            harness, reference, row, _ = self.prepare(Path(tmp))
            original_save = harness._save

            def save_then_fail():
                original_save()
                raise OSError("error observed after atomic rename")

            try:
                with (
                    mock.patch("abstract_harness.loop.docker.retag"),
                    mock.patch.object(harness, "_save", side_effect=save_then_fail),
                ):
                    with self.assertRaises(OSError):
                        harness._commit_promotion(reference, {}, row)
                self.assertEqual(harness.state.last_good, reference)
                self.assertEqual(load_state(harness.state_path).last_good, reference)
                self.assertEqual(harness.state.in_progress.stage, "promoted")
            finally:
                harness._release_locks()

    def _attempt_fixture(self, failure_after_commit=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness, _, _, old = self.prepare(root)
            (old / "Dockerfile").write_text("FROM scratch\nLABEL variant=old\n")
            proposal = Proposal(
                "new",
                "faster",
                "--- a/Dockerfile\n+++ b/Dockerfile\n"
                "@@ -1,2 +1,2 @@\n FROM scratch\n-LABEL variant=old\n+LABEL variant=new\n",
            )
            groups = {
                p["id"]: [2.0] * harness.benchmark["repetitions"]
                for p in harness.benchmark["prompts"]
            }
            faster = {p: [1.0] * len(v) for p, v in groups.items()}
            rates = {p: [10.0] * len(v) for p, v in groups.items()}
            fast_rates = {p: [20.0] * len(v) for p, v in groups.items()}

            def measurement(ttft, ftgr):
                return Measurement(
                    "fixture",
                    1.0,
                    harness.benchmark["repetitions"],
                    [],
                    [],
                    [],
                    [],
                    True,
                    ttft_by_prompt=ttft,
                    ftgr_by_prompt=ftgr,
                )

            build = BuildResult(
                "new:image",
                True,
                False,
                0.1,
                "sha256:" + "b" * 64,
                "sha256:" + "b" * 64,
                root / "build.log",
            )
            original_commit = harness._commit_promotion

            def commit_then_fail(*args, **kwargs):
                original_commit(*args, **kwargs)
                if failure_after_commit:
                    raise failure_after_commit("error after durable keep")

            try:
                with (
                    mock.patch.object(harness, "build", return_value=build),
                    mock.patch.object(
                        harness,
                        "measure_image",
                        side_effect=[
                            (measurement(groups, rates), {}),
                            (measurement(faster, fast_rates), {}),
                        ],
                    ),
                    mock.patch("abstract_harness.loop.docker.image_exists", return_value=True),
                    mock.patch("abstract_harness.loop.docker.retag"),
                    mock.patch.object(harness, "_commit_promotion", side_effect=commit_then_fail),
                ):
                    if failure_after_commit:
                        with self.assertRaises(failure_after_commit):
                            harness.attempt(proposal)
                    else:
                        self.assertEqual(harness.attempt(proposal), "improved")
                self.assertIn(
                    "LABEL variant=new", (harness.last_good_context / "Dockerfile").read_text()
                )
                self.assertIn("LABEL variant=old", (old / "Dockerfile").read_text())
                saved = load_state(harness.state_path)
                self.assertEqual(saved.last_good.context_path, str(harness.last_good_context))
                self.assertEqual(saved.last_good.digest, build.digest)
                self.assertIsNone(saved.in_progress)
                self.assertEqual(saved.history[-1]["kept"], "1")
                report = json.loads((root / "reports/003-new.json").read_text())
                self.assertTrue(report["kept"])
                self.assertEqual(report["outcome"], "improved")
            finally:
                harness._release_locks()


class KeptPatchChainTest(unittest.TestCase):
    def test_manifest_keeps_ordered_patch_chain_and_ignores_rejected_and_future_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = make_harness(root)
            harness.engine = StandinEngine.create(_paths.REPO_ROOT, root, cpus=1)
            harness.state.last_good = ImageRef(
                "fixture:image",
                "sha256:" + "a" * 64,
                experiment="003-kept",
                patch="patches/003.patch",
            )
            harness.state.history = [
                {"experiment": "001-kept", "kept": "1", "patch_path": "patches/001.patch"},
                {"experiment": "002-rejected", "kept": "0", "patch_path": "patches/002.patch"},
                {"experiment": "003-kept", "kept": "1", "patch_path": "patches/003.patch"},
                {"experiment": "004-future", "kept": "1", "patch_path": "patches/004.patch"},
            ]
            try:
                with mock.patch("abstract_harness.docker_runtime.image_cmd", return_value=[]):
                    manifest = build_manifest(harness, integrity_output="PASS", integrity_rc=0)
                self.assertEqual(
                    manifest["source"]["patches"], ["patches/001.patch", "patches/003.patch"]
                )
                self.assertTrue(manifest["source"]["patch_chain_complete"])
                harness.state.history = harness.state.history[:2]
                harness.state.in_progress = InProgress(
                    "003-kept",
                    "h",
                    1.0,
                    "",
                    stage="promoted",
                    completed_row={
                        "experiment": "003-kept",
                        "kept": "1",
                        "patch_path": "patches/003.patch",
                    },
                )
                with mock.patch("abstract_harness.docker_runtime.image_cmd", return_value=[]):
                    pending = build_manifest(harness, integrity_output="PASS", integrity_rc=0)
                self.assertEqual(pending["source"]["patches"], manifest["source"]["patches"])
                self.assertTrue(pending["source"]["patch_chain_complete"])
            finally:
                harness._release_locks()
