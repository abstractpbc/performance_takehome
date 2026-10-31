"""Cold-start summaries require the complete planned cold-generation population."""

import copy
import tempfile
import unittest
from pathlib import Path

import evaluate
from bench.plan import resolve_mode

from tests.helpers import clean_cell, make_record, make_run, plan, write_events


class ColdStartupSufficiencyTests(unittest.TestCase):
    def setUp(self):
        self.plan = plan()
        self.plan["modes"]["full"] = copy.deepcopy(self.plan["modes"]["fixture"])
        self.plan["modes"]["full"].update(cells=["chat_short_open"], startup_launches=2)

    def capture(self, settings):
        (unit,) = resolve_mode(self.plan, "full")
        records = clean_cell(self.plan, unit.cell_id, unit.requests)
        for index in range(unit.warmup):
            row = copy.deepcopy(records[0])
            row["phase"] = "warmup"
            row["request_id"] = f"{unit.unit_id}/warmup/{index}"
            records.append(row)
        launches = []
        for index, (cold, completed) in enumerate(settings):
            launch_time = 90.0 + index * 3
            request_id = f"startup/{index}/probe/0"
            probe = make_record(
                self.plan,
                request_id=request_id,
                send=launch_time + 1,
                ttft=0.1 if cold else 0.01,
                phase="startup_probe",
                role="startup",
                done=completed,
                finish="stop" if completed else None,
            )
            records.append(probe)
            launches.append(
                {
                    "launch_index": index,
                    "launch_mono_s": launch_time,
                    "cold": cold,
                    "launch_command_sha256": "0" * 64 if cold else None,
                    "readiness_probes": [
                        {"t": launch_time + 0.5, "http_status": 200, "ok": True}
                    ],
                    "generation_probe_request_ids": [request_id],
                    "readiness_at": launch_time + 0.5,
                    "first_valid_generation_at": (
                        probe["timestamps_mono_s"]["end"] if completed else None
                    ),
                    "first_valid_request_id": request_id if completed else None,
                    "status": "valid" if completed else "timeout",
                }
            )
        run = make_run(records, launch=launches[0])
        run.update(mode="full", planned_startup_launches=2, startup=launches)
        run["units"][0]["planned_warmup"] = unit.warmup
        with tempfile.TemporaryDirectory() as directory:
            write_events(Path(directory), run, records)
            return evaluate.evaluate(self.plan, Path(directory), "native")[0]

    def test_two_valid_cold_launches_preserve_a_valid_result(self):
        report = self.capture([(True, True), (True, True)])
        self.assertEqual(report["status"], "valid", report["reasons"])
        summary = report["startup"]["startup_to_first_valid_generation_s"]
        self.assertEqual(summary["n"], 2)
        self.assertEqual(len(summary["values"]), 2)

    def test_warm_attach_does_not_complete_or_enter_cold_start_summary(self):
        report = self.capture([(True, True), (False, True)])
        self.assertEqual(report["status"], "inconclusive")
        startup = report["startup"]
        self.assertEqual(startup["startup_to_first_valid_generation_s"]["n"], 1)
        self.assertEqual(
            startup["startup_to_first_valid_generation_s"]["values"],
            [startup["launches"][0]["startup_to_first_valid_generation_s"]],
        )
        self.assertFalse(startup["launches"][1]["cold"])
        self.assertIsNotNone(startup["launches"][1]["startup_to_first_valid_generation_s"])
        self.assertIn(
            "1 valid cold generations < 2 planned", " ".join(report["inconclusive_reasons"])
        )

    def test_failed_cold_start_cannot_be_replaced_by_a_successful_warm_attach(self):
        report = self.capture([(True, False), (False, True)])
        self.assertEqual(report["status"], "inconclusive")
        summary = report["startup"]["startup_to_first_valid_generation_s"]
        self.assertEqual(summary, {"n": 0, "values": [], "median": None})
        self.assertIsNone(report["startup"]["launches"][0]["startup_to_first_valid_generation_s"])
        self.assertIsNotNone(report["startup"]["launches"][1]["startup_to_first_valid_generation_s"])

    def test_two_warm_attaches_do_not_establish_cold_startup(self):
        report = self.capture([(False, True), (False, True)])
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["startup"]["startup_to_first_valid_generation_s"]["n"], 0)
        self.assertIn("no cold launch", " ".join(report["inconclusive_reasons"]))

    def test_failed_second_cold_generation_remains_an_incomplete_population(self):
        report = self.capture([(True, True), (True, False)])
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["startup"]["cold_launches"], 2)
        self.assertEqual(report["startup"]["startup_to_first_valid_generation_s"]["n"], 1)
        self.assertEqual(report["startup"]["launches"][1]["status"], "timeout")

    def test_extra_cold_retry_cannot_replace_a_failed_planned_launch(self):
        report = self.capture([(True, False), (True, True), (True, True)])
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["startup"]["startup_to_first_valid_generation_s"]["n"], 2)
        self.assertEqual(report["startup"]["launches"][0]["status"], "timeout")
        self.assertIn(
            "3 launches recorded > 2 planned", " ".join(report["inconclusive_reasons"])
        )

    def test_extra_successful_cold_launch_is_not_a_declared_population(self):
        report = self.capture([(True, True), (True, True), (True, True)])
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["startup"]["startup_to_first_valid_generation_s"]["n"], 3)
        self.assertIn(
            "3 valid cold generations > 2 planned", " ".join(report["inconclusive_reasons"])
        )

    def test_extra_warm_attach_does_not_preserve_a_valid_planned_population(self):
        report = self.capture([(True, True), (True, True), (False, True)])
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["startup"]["startup_to_first_valid_generation_s"]["n"], 2)
        self.assertEqual(len(report["startup"]["launches"]), 3)
        self.assertFalse(report["startup"]["launches"][2]["cold"])
        self.assertIn(
            "3 launches recorded > 2 planned", " ".join(report["inconclusive_reasons"])
        )


if __name__ == "__main__":
    unittest.main()
