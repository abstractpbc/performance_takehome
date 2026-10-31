"""Completion labels must not turn aborted real-engine studies into success."""

import copy
import json
import runpy
import tarfile
import unittest
from pathlib import Path

PACKET = Path(__file__).resolve().parents[2] / "validation/engine-diagnostic-20261030"
REQUIRE_COMPLETE = runpy.run_path(str(PACKET / "study_driver.py"))["require_complete_study"]


class DiagnosticCompletionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tarfile.open(PACKET / "capture.tar.gz") as archive:
            cls.original = [
                json.loads(archive.extractfile(name).read())
                for name in (
                    "STUDY-RESULT.json", "study-summary.json", "report.json",
                    "independent-timing.json", "STUDY-PLAN.json",
                )
            ]

    def setUp(self):
        self.result, self.summary, self.report, self.timing, self.plan = copy.deepcopy(
            self.original
        )

    def validate(self):
        REQUIRE_COMPLETE(self.result, self.summary, self.report, self.timing, self.plan)

    def test_actual_complete_inconclusive_capture_keeps_intentional_faults(self):
        self.assertEqual(self.result["evaluate.py_exit_code"], 3)
        self.assertEqual(self.report["status"], "inconclusive")
        self.assertEqual(self.report["population"]["totals"]["failed"], 1)
        self.assertEqual(self.report["population"]["totals"]["unfinished"], 1)
        self.validate()

    def test_runner_abort_is_not_success(self):
        self.result["runner_exit_code"] = 1
        with self.assertRaises(ValueError):
            self.validate()

    def test_failed_timing_check_is_not_success(self):
        self.result["audit_timing.py_exit_code"] = 1
        with self.assertRaises(ValueError):
            self.validate()

    def test_invalid_evaluation_is_not_success(self):
        self.result["evaluate.py_exit_code"] = 2
        self.report["status"] = "invalid"
        with self.assertRaises(ValueError):
            self.validate()

    def test_startup_only_empty_measurements_are_not_success(self):
        self.summary["all_request_verdicts"] = [
            row for row in self.summary["all_request_verdicts"]
            if row["phase"] != "measure"
        ]
        self.report["population"]["totals"]["offered"] = 0
        with self.assertRaises(ValueError):
            self.validate()

    def test_partial_offering_is_not_success(self):
        self.report["population"]["totals"]["offered"] = 10
        self.report["population"]["totals"]["not_offered"] = 1
        with self.assertRaises(ValueError):
            self.validate()

    def test_repeated_cell_cannot_substitute_for_missing_arm(self):
        rows = self.summary["all_request_verdicts"]
        next(row for row in rows if row["cell_id"] == "control_b")["cell_id"] = "control_a"
        with self.assertRaises(ValueError):
            self.validate()

    def test_duplicate_request_does_not_count_twice(self):
        rows = [r for r in self.summary["all_request_verdicts"] if r["phase"] == "measure"]
        rows[1]["request_id"] = rows[0]["request_id"]
        with self.assertRaises(ValueError):
            self.validate()

    def test_missing_valid_startup_is_not_success(self):
        for probe in self.summary["original_recorder_startup"]:
            probe["status"] = "failed"
        with self.assertRaises(ValueError):
            self.validate()


if __name__ == "__main__":
    unittest.main()
