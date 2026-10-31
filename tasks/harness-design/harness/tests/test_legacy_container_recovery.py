"""Exact saved servers from pre-namespace checkpoints must not survive resume."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from abstract_harness import loop as loop_mod
from abstract_harness import results
from abstract_harness.state import StateError, load_state, save_state

from .test_recovery import _seed_in_progress, make_harness


class LegacyContainerRecoveryTest(unittest.TestCase):
    def test_unknown_daemon_state_blocks_recovery_without_clearing_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            saved_name = self.seed(root)
            harness = make_harness(root)
            try:
                with (
                    mock.patch.object(loop_mod.docker, "stop_container") as stop,
                    mock.patch.object(
                        loop_mod.docker, "container_absent",
                        side_effect=loop_mod.docker.DockerError("absence unknown"),
                    ),
                    mock.patch.object(loop_mod.docker, "kill_containers_with_prefix") as kill,
                    self.assertRaisesRegex(loop_mod.docker.DockerError, "absence unknown"),
                ):
                    harness.recover_from_previous_run()
                stop.assert_called_once_with(
                    saved_name, grace_s=harness.config.timeouts.stop_grace_s
                )
                kill.assert_not_called()
                self.assertEqual(harness.state.in_progress.container, saved_name)
                self.assertEqual(harness.state.api_usd_cumulative, 1.25)
                self.assertEqual(harness.state.lease_start_ts, 0.0)
                persisted = load_state(root / "state/state.json")
                self.assertEqual(persisted.in_progress.container, saved_name)
                self.assertEqual(len(results.read_rows(root / "results.tsv")), 1)
            finally:
                harness._release_locks()

    def seed(self, root, container="abstract-harness-candidate-12345", *, row_written=False):
        _seed_in_progress(root, "measuring")
        path = root / "state/state.json"
        state = load_state(path)
        state.api_usd_cumulative = 1.25
        state.in_progress.container = container
        save_state(path, state)
        payload = json.loads(path.read_text())
        payload.pop("run_id")  # Shape written by the implementation before namespacing.
        path.write_text(json.dumps(payload))
        if row_written:
            results.append_row(root / "results.tsv", results.make_row(
                ts="2026-01-01T00:00:01Z", experiment="003-hung",
                source_commit=state.source_commit, image_digest="", kept="0", outcome="interrupted",
            ))
        return container

    def test_legacy_server_is_removed_by_exact_name_before_either_recovery_branch(self):
        for row_written in (False, True):
            with self.subTest(row_written=row_written), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                saved_name = self.seed(root, row_written=row_written)
                harness = make_harness(root)
                try:
                    self.assertNotEqual(harness.config.container_prefix, "abstract-harness")
                    with (
                        mock.patch.object(loop_mod.docker, "stop_container") as stop,
                        mock.patch.object(
                            loop_mod.docker, "container_absent", return_value=True
                        ) as inspect,
                        mock.patch.object(loop_mod.docker, "kill_containers_with_prefix") as kill,
                    ):
                        harness.recover_from_previous_run()
                    stop.assert_called_once_with(
                        saved_name, grace_s=harness.config.timeouts.stop_grace_s
                    )
                    inspect.assert_called_once_with(saved_name)
                    kill.assert_called_once_with(harness.config.container_prefix + "-")
                    self.assertIsNone(harness.state.in_progress)
                    self.assertEqual(harness.state.api_usd_cumulative, 1.25)
                    self.assertEqual(harness.state.lease_start_ts, 0.0)
                    self.assertEqual(len(results.read_rows(root / "results.tsv")), 2)
                finally:
                    harness._release_locks()

    def test_surviving_or_unowned_saved_server_blocks_recovery_without_clearing_state(self):
        for container, owned, error in (
            ("abstract-harness-candidate-12345", True, "still exists"),
            ("unrelated-server", False, "outside the run namespace"),
            ("-all", False, "outside the run namespace"),
        ):
            with self.subTest(container=container), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.seed(root, container)
                harness = make_harness(root)
                try:
                    with (
                        mock.patch.object(loop_mod.docker, "stop_container") as stop,
                        mock.patch.object(loop_mod.docker, "container_absent", return_value=False),
                        mock.patch.object(loop_mod.docker, "kill_containers_with_prefix") as kill,
                    ):
                        with self.assertRaisesRegex(StateError, error):
                            harness.recover_from_previous_run()
                    self.assertIsNotNone(harness.state.in_progress)
                    self.assertEqual(len(results.read_rows(root / "results.tsv")), 1)
                    kill.assert_not_called()
                    self.assertEqual(stop.call_count, int(owned))
                finally:
                    harness._release_locks()
