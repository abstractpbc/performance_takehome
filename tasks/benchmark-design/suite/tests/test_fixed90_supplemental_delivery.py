"""Bind actual fixed90 source/raw evidence without upgrading its statistical scope."""

from __future__ import annotations

import copy
import hashlib
import json
import runpy
import statistics
import tarfile
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

TASK = Path(__file__).resolve().parents[2]
PACKET = TASK / "validation/engine-fixed90-supplemental-20261030"
HELPER = runpy.run_path(str(PACKET / "study_driver.capture.py.txt"))
STATS = runpy.run_path(str(PACKET / "stats.capture.py.txt"))


class Fixed90SupplementalDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (PACKET / "capture.tar.gz").read_bytes()
        with tarfile.open(PACKET / "capture.tar.gz") as archive:
            cls.files = {
                member.name: archive.extractfile(member).read()
                for member in archive
                if member.isfile()
            }
        cls.load = staticmethod(lambda name: json.loads(cls.files[name]))

    def test_original_archive_and_all382_payload_hashes_are_exact(self):
        self.assertEqual(len(self.raw), 383385)
        self.assertEqual(
            hashlib.sha256(self.raw).hexdigest(),
            "fab4f7d0ffb9d1bf5625664f01d32b79a20afee4f1afcbbd454e2bacbb398f23",
        )
        manifest = self.load("export-manifest.json")
        self.assertEqual(len(manifest), 382)
        for name, row in manifest.items():
            self.assertEqual(len(self.files[name]), row["bytes"])
            self.assertEqual(hashlib.sha256(self.files[name]).hexdigest(), row["sha256"])

    def test_actual_frozen_completion_and_raw90_stream_validators_pass(self):
        original = [
            self.load(name)
            for name in [
                "STUDY-RESULT.json",
                "study-summary.json",
                "report.json",
                "independent-timing.json",
                "STUDY-PLAN.json",
                "workload.study.json",
            ]
        ]
        HELPER["require_complete_study"](*original)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name, data in self.files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            value = HELPER["require_raw_fixed90"](root, original[1])
        self.assertEqual(value["measured_streams"], 90)
        self.assertEqual(value["actual100ms_delays"], 30)
        self.assertTrue(value["all_bytes_identical"])
        self.assertEqual(original[2]["status"], "inconclusive")
        self.assertEqual(original[0]["evaluate.py_exit_code"], 3)

    def test_recorded_descriptive_CIs_replay_from_original_actual_groups(self):
        summary = self.load("study-summary.json")
        groups = summary["groups_TTFT_samples_s"]
        ci = STATS["difference_ci"]
        delay = ci(
            groups["control_a"] + groups["control_b"],
            groups["delay100"],
            statistics.median,
            resamples=2000,
            confidence=0.95,
            seed=2026102903,
        )
        aa = ci(
            groups["control_a"],
            groups["control_b"],
            statistics.median,
            resamples=2000,
            confidence=0.95,
            seed=2026103004,
        )
        self.assertEqual(delay, summary["delay_minus_clean_median_descriptive_CI95"])
        self.assertEqual(aa, summary["healthy_b_minus_a_median_descriptive_CI95"])
        self.assertLess(aa["low"], 0)
        self.assertGreater(aa["high"], 0)

    def test_frozen50ms_gate_rejects_invalid_lag_without_relabelling_capture(self):
        original = [
            copy.deepcopy(self.load(name))
            for name in [
                "STUDY-RESULT.json",
                "study-summary.json",
                "report.json",
                "independent-timing.json",
                "STUDY-PLAN.json",
                "workload.study.json",
            ]
        ]
        self.assertEqual(original[5]["client_integrity"]["max_event_loop_lag_s"], 0.05)
        original[1]["client_integrity"]["event_loop_lag_max_s"] = 0.050001
        with self.assertRaises(ValueError):
            HELPER["require_complete_study"](*original)

    def test_primary_measurements_history_and_clock_are_not_rewritten(self):
        old = (TASK / "submission.pre-fixed90-20261030.json").read_bytes()
        self.assertEqual(
            hashlib.sha256(old).hexdigest(),
            "45e76a389cd696b24c992b398685b90374c83def272a0f1c54e354af38451744",
        )
        current = json.loads((TASK / "submission.json").read_bytes())
        history = json.loads(old)
        self.assertEqual(current["measurement"], history["measurement"])
        self.assertEqual(current["suite"], history["suite"])
        self.assertEqual(current["source"]["capture_commit"], history["source"]["capture_commit"])
        self.assertTrue(current["supplemental_fixed90"]["outside_original_primary3h"])
        self.assertFalse(current["supplemental_fixed90"]["primary_capture_files_or_clocks_changed"])
        self.assertFalse(
            current["supplemental_fixed90"][
                "power_official_grade_or_full_suite_revalidation_claimed"
            ]
        )

    def test_post_DELETE_poll_error_and_missing_body_do_not_replace_capture_verdict(self):
        closure = json.loads((PACKET / "resource-closure.json").read_bytes())
        completion = json.loads((PACKET / "completion.json").read_bytes())
        self.assertEqual(closure["controller_returncode"], 1)
        self.assertEqual(closure["watcher_returncode"], 0)
        self.assertFalse(closure["post_DELETE_GET200_body_retained"])
        self.assertTrue(closure["within_original_absence_bound"])
        self.assertLess(
            datetime.fromisoformat(closure["GET404_UTC"]),
            datetime.fromisoformat(closure["original_absence_by_UTC"]),
        )
        self.assertEqual(completion["status"], "COMPLETE_FIXED90_DESCRIPTIVE_NOT_OFFICIAL_GRADE")
        self.assertEqual(completion["original_evaluator_status"], "inconclusive")

    def test_public_packet_inventory_and_replayed_proof_bind_actual_archive(self):
        manifest = json.loads((PACKET / "manifest.json").read_bytes())["files"]
        for name, row in manifest.items():
            raw = (PACKET / name).read_bytes()
            self.assertEqual(len(raw), row["bytes"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), row["sha256"])
        replay = json.loads((PACKET / "independent-replay.json").read_bytes())
        self.assertEqual(replay["archive_sha256"], hashlib.sha256(self.raw).hexdigest())
        self.assertTrue(replay["actual90_requests_rejudged_with_pinned_tokenizer"])
        self.assertFalse(replay["official_grade_or_power_claimed"])


if __name__ == "__main__":
    unittest.main()
