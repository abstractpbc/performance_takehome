"""On-disk state round-trips so a restarted process can resume."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from abstract_harness.state import (
    HarnessState,
    ImageRef,
    InProgress,
    StateError,
    backup_path,
    load_state,
    save_state,
)

from . import _paths  # noqa: F401


class StateTest(unittest.TestCase):
    def test_round_trip(self) -> None:
        state = HarnessState(
            engine="standin",
            source_commit="abc",
            lease_start_ts=1.0,
            attempt_index=3,
            api_usd_cumulative=0.25,
            last_good=ImageRef(tag="x:last-good", digest="sha256:" + "0" * 64, experiment="001-a"),
            baseline_samples={"ttft_s": {"p": [0.1, 0.2]}},
            golden={"p": "hello"},
            in_progress=InProgress(
                experiment="004-b", hypothesis="h", started_ts=2.0, patch_path="p", stage="building"
            ),
            history=[{"experiment": "001-a", "outcome": "improved"}],
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            save_state(path, state)
            loaded = load_state(path)
            self.assertEqual(loaded, state)
            self.assertEqual(json.loads(path.read_text())["schema_version"], 1)
            self.assertFalse(list(Path(tmp).glob("*.tmp*")), "atomic write leaves no temp files")

    def test_missing_state_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(load_state(Path(tmp) / "nope.json"))


if __name__ == "__main__":
    unittest.main()


class CorruptStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.json"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_truncated_file_falls_back_to_previous_checkpoint(self) -> None:
        save_state(self.path, HarnessState(engine="standin", attempt_index=3))
        save_state(self.path, HarnessState(engine="standin", attempt_index=4))
        self.assertTrue(backup_path(self.path).exists())
        text = self.path.read_text("utf-8")
        self.path.write_text(text[: len(text) // 2], encoding="utf-8")
        warnings: list[str] = []
        state = load_state(self.path, on_warning=warnings.append)
        assert state is not None
        self.assertEqual(state.attempt_index, 3)
        self.assertEqual(len(warnings), 1)
        self.assertIn("resumed from", warnings[0])

    def test_corrupt_file_without_backup_refuses_to_start_fresh(self) -> None:
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(StateError) as ctx:
            load_state(self.path)
        self.assertIn("move it aside", str(ctx.exception))

    def test_corrupt_file_and_corrupt_backup_raise(self) -> None:
        self.path.write_text("{", encoding="utf-8")
        backup_path(self.path).write_text('{"schema_version": 999}', encoding="utf-8")
        with self.assertRaises(StateError):
            load_state(self.path)

    def test_wrong_schema_is_rejected(self) -> None:
        self.path.write_text(json.dumps({"schema_version": 999}), encoding="utf-8")
        with self.assertRaises(StateError):
            load_state(self.path)
