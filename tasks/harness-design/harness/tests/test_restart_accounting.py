"""Crash-safe paid proposal admission and interrupted baseline retries."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from abstract_harness import loop, measure, results
from abstract_harness.docker_runtime import BuildResult
from abstract_harness.proposers import (
    Proposal,
    Proposer,
    ProposerAccountingError,
    ProposerError,
    ScriptedProposer,
)
from abstract_harness.state import InProgress, backup_path, load_state, save_state

from .test_recovery import make_harness


class _PaidProposer(Proposer):
    def __init__(self, callback):
        self.callback = callback

    def estimated_usd_per_call(self):
        return 0.02

    def propose(self, context):
        return self.callback(context)


class PendingPaidCallTest(unittest.TestCase):
    def prepare(self, root, callback):
        harness = make_harness(root)
        harness.proposer = _PaidProposer(callback)
        harness.config.budget = replace(harness.config.budget, api_usd_limit=1.0)
        harness.config.max_attempts = 1
        return harness

    def run_without_runtime(self, harness):
        with (
            mock.patch.object(harness, "install_signal_handlers"),
            mock.patch.object(harness, "establish_baseline"),
            mock.patch.object(harness, "_proposer_context", return_value=None),
            mock.patch.object(harness, "finalize"),
            mock.patch.object(harness, "cleanup"),
        ):
            return harness.run()

    def test_hard_interruption_leaves_durable_marker_and_blocks_next_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def crash(_context):
                saved = load_state(root / "state/state.json")
                self.assertIn("pending proposer call", saved.api_accounting_unresolved)
                raise KeyboardInterrupt("simulated process death before usage")

            harness = self.prepare(root, crash)
            with self.assertRaises(KeyboardInterrupt):
                self.run_without_runtime(harness)
            next_call = mock.Mock()
            restarted = self.prepare(root, next_call)
            self.assertEqual(self.run_without_runtime(restarted), 2)
            next_call.assert_not_called()
            self.assertIn(
                "pending proposer call", load_state(harness.state_path).api_accounting_unresolved
            )

    def test_known_cost_is_saved_before_attempt_artifact_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proposal = Proposal("paid", "h", "patch", api_usd=0.02)
            harness = self.prepare(root, lambda _context: proposal)

            def attempt(value, *, api_cost_accounted):
                saved = load_state(harness.state_path)
                self.assertIs(value, proposal)
                self.assertTrue(api_cost_accounted)
                self.assertEqual(saved.api_usd_cumulative, 0.02)
                self.assertFalse(saved.api_accounting_unresolved)
                raise OSError("simulated failure writing a patch")

            with mock.patch.object(harness, "attempt", side_effect=attempt) as attempted:
                self.assertEqual(self.run_without_runtime(harness), 2)
            attempted.assert_called_once()
            self.assertEqual(load_state(harness.state_path).api_usd_cumulative, 0.02)

    def test_corrupt_main_fallback_cannot_drop_pending_paid_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def crash(_context):
                saved_backup = load_state(backup_path(root / "state/state.json"))
                self.assertIn("pending proposer call", saved_backup.api_accounting_unresolved)
                raise KeyboardInterrupt("paid call interrupted before terminal usage")

            harness = self.prepare(root, crash)
            with self.assertRaises(KeyboardInterrupt):
                self.run_without_runtime(harness)
            harness.state_path.write_text("{truncated", encoding="utf-8")
            next_call = mock.Mock()
            restarted = self.prepare(root, next_call)
            self.assertEqual(self.run_without_runtime(restarted), 2)
            next_call.assert_not_called()

    def test_known_error_cost_settles_but_unknown_error_cost_stays_blocked(self):
        for error, unresolved in (
            (ProposerError("known", api_usd=0.02), False),
            (ProposerAccountingError("missing usage", api_usd=0.01), True),
            (ProposerError("unknown"), True),
        ):
            with self.subTest(error=str(error)), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)

                def fail(_context, error=error):
                    raise error

                harness = self.prepare(root, fail)
                self.assertEqual(self.run_without_runtime(harness), 2 if unresolved else 0)
                state = load_state(harness.state_path)
                self.assertEqual(bool(state.api_accounting_unresolved), unresolved)
                self.assertEqual(state.api_usd_cumulative, error.api_usd or 0.0)


class BaselineRetryTest(unittest.TestCase):
    def test_actual_run_retries_failed_baseline_without_resetting_state(self):
        for legacy_finished_flag in (False, True):
            with (
                self.subTest(legacy_finished_flag=legacy_finished_flag),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                context = root / "source"
                context.mkdir()
                (context / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
                engine = SimpleNamespace(
                    name="standin", spec=SimpleNamespace(build_context=context)
                )
                harness = make_harness(root)
                harness.engine = engine
                harness.config.max_attempts = 0

                def failed_build(_context, _tag, log_path):
                    log_path.parent.mkdir(parents=True, exist_ok=True)
                    log_path.write_bytes(b"transient baseline build failure")
                    raise RuntimeError("temporary Docker service failure")

                with (
                    mock.patch.object(harness, "install_signal_handlers"),
                    mock.patch.object(harness, "cleanup"),
                    mock.patch.object(harness, "build", side_effect=failed_build),
                    mock.patch("abstract_harness.manifest.write_manifest", return_value={}),
                ):
                    self.assertEqual(harness.run(), 2)
                failed_report = next((root / "reports").glob("*.json"))
                original_report = failed_report.read_bytes()
                failed_log = root / "state/logs/000-baseline.build.log"
                original_log = failed_log.read_bytes()
                if legacy_finished_flag:
                    # Reproduce the checkpoint written by the previous release.
                    state = load_state(harness.state_path)
                    state.finished = True
                    state.attempt_index = 0
                    save_state(harness.state_path, state)
                restarted = make_harness(root)
                restarted.engine = engine
                restarted.config.max_attempts = 0
                build = BuildResult(
                    "candidate:baseline",
                    True,
                    False,
                    0.1,
                    "sha256:" + "b" * 64,
                    "sha256:" + "b" * 64,
                    root / "build.log",
                )
                measurement = measure.Measurement("model", 1.0, 3, [], [], [], [], True)
                with (
                    mock.patch.object(restarted, "install_signal_handlers"),
                    mock.patch.object(restarted, "cleanup"),
                    mock.patch.object(restarted, "build", return_value=build) as rebuilt,
                    mock.patch.object(restarted, "measure_image", return_value=(measurement, {})),
                    mock.patch.object(loop.docker, "retag"),
                    mock.patch("abstract_harness.manifest.write_manifest", return_value={}),
                ):
                    self.assertEqual(restarted.run(), 0)
                rebuilt.assert_called_once()
                state = load_state(restarted.state_path)
                self.assertIsNotNone(state.last_good)
                self.assertFalse(state.finished)
                self.assertEqual(state.lease_start_ts, 0.0)
                self.assertEqual(state.api_usd_cumulative, 0.0)
                rows = results.validate_file(root / "results.tsv")
                self.assertEqual([row.outcome for row in rows], ["error", "baseline"])
                self.assertEqual(len({row.experiment for row in rows}), 2)
                self.assertEqual(failed_report.read_bytes(), original_report)
                self.assertEqual(failed_log.read_bytes(), original_log)

    def successful_retry(self, harness, root):
        context = root / "source"
        context.mkdir()
        (context / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        harness.engine = SimpleNamespace(
            name="standin", spec=SimpleNamespace(build_context=context)
        )
        build = BuildResult(
            "candidate:baseline",
            True,
            False,
            0.1,
            "sha256:" + "b" * 64,
            "sha256:" + "b" * 64,
            root / "build.log",
        )
        measurement = measure.Measurement("model", 1.0, 3, [], [], [], [], True)

        def build_retry(_context, _tag, log_path):
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_bytes(b"successful retry log")
            return build

        with (
            mock.patch.object(harness, "build", side_effect=build_retry),
            mock.patch.object(harness, "measure_image", return_value=(measurement, {})),
            mock.patch.object(loop.docker, "retag"),
        ):
            harness.establish_baseline()

    def test_gracefully_interrupted_baseline_retry_retains_both_rows_and_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = make_harness(root)
            context = root / "interrupted-source"
            context.mkdir()
            harness.engine = SimpleNamespace(
                name="standin", spec=SimpleNamespace(build_context=context)
            )
            failed_log = root / "state/logs/000-baseline.build.log"

            def interrupted_build(_context, _tag, log_path):
                self.assertEqual(log_path, failed_log)
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_bytes(b"failed baseline build evidence")
                raise loop.Interrupted("test")

            with mock.patch.object(harness, "build", side_effect=interrupted_build):
                with self.assertRaises(loop.Interrupted):
                    harness.establish_baseline()
            failed_report = next((root / "reports").glob("*.json"))
            failed_bytes = failed_report.read_bytes()
            harness._release_locks()
            restarted = make_harness(root)
            self.addCleanup(restarted._release_locks)
            restarted.recover_from_previous_run()
            self.successful_retry(restarted, root)
            rows = results.validate_file(root / "results.tsv")
            self.assertEqual([row.outcome for row in rows], ["interrupted", "baseline"])
            self.assertEqual(len({row.experiment for row in rows}), 2)
            self.assertEqual(failed_report.read_bytes(), failed_bytes)
            self.assertEqual(failed_log.read_bytes(), b"failed baseline build evidence")
            self.assertEqual(
                (root / "state/logs/001-baseline.build.log").read_bytes(),
                b"successful retry log",
            )
            self.assertEqual(rows[-1].experiment, restarted.state.last_good.experiment)

    def test_hard_killed_baseline_recovery_can_retry_successfully(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = make_harness(root)
            harness.state.in_progress = InProgress(
                "baseline", "baseline", 1.0, "", stage="building"
            )
            harness._save()
            harness._release_locks()
            restarted = make_harness(root)
            self.addCleanup(restarted._release_locks)
            with mock.patch.object(loop.docker, "kill_containers_with_prefix"):
                restarted.recover_from_previous_run()
            self.successful_retry(restarted, root)
            rows = results.validate_file(root / "results.tsv")
            self.assertEqual([row.outcome for row in rows], ["interrupted", "baseline"])
            self.assertEqual(len({row.experiment for row in rows}), 2)

    def test_baseline_retry_does_not_skip_first_scripted_queue_item_on_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = make_harness(root)
            harness.state.in_progress = InProgress(
                "baseline", "baseline", 1.0, "", stage="building"
            )
            harness._save()
            harness._release_locks()
            retried = make_harness(root)
            with mock.patch.object(loop.docker, "kill_containers_with_prefix"):
                retried.recover_from_previous_run()
            self.successful_retry(retried, root)
            retried._release_locks()
            restarted = make_harness(root)
            restarted.config.max_attempts = 1
            restarted.proposer = ScriptedProposer(
                [
                    {"experiment": "first", "patch": "first patch"},
                    {"experiment": "second", "patch": "second patch"},
                ]
            )
            with (
                mock.patch.object(restarted, "install_signal_handlers"),
                mock.patch.object(restarted, "establish_baseline"),
                mock.patch.object(restarted, "_proposer_context", return_value=None),
                mock.patch.object(restarted, "finalize"),
                mock.patch.object(restarted, "cleanup"),
                mock.patch.object(restarted, "attempt", return_value="error") as attempted,
            ):
                self.assertEqual(restarted.run(), 0)
            self.assertEqual(attempted.call_args.args[0].experiment, "first")

    def test_ordering_still_rejects_candidate_before_successful_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = make_harness(root)
            self.addCleanup(harness._release_locks)
            harness._append_row(
                experiment="000-baseline", outcome="interrupted", kept="0", image_digest=""
            )
            harness._append_row(
                experiment="001-candidate", outcome="error", kept="0", image_digest=""
            )
            with self.assertRaises(results.ResultsError):
                results.validate_file(root / "results.tsv")
