"""Independent review regressions for untrusted metadata and comparison families."""

import copy
import tempfile
import unittest
from pathlib import Path

import evaluate as ev_mod
from bench.plan import resolve_mode

from tests.helpers import clean_cell, make_run, plan, write_events


class EvidenceBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.plan = plan()
        self.records = clean_cell(self.plan, "chat_short_open", 12)
        self.run = make_run(self.records)

    def evaluate(self, run=None, records=None):
        with tempfile.TemporaryDirectory() as tmp:
            write_events(
                Path(tmp),
                self.run if run is None else run,
                self.records if records is None else records,
            )
            return ev_mod.evaluate(self.plan, Path(tmp), "native")

    def test_non_finite_metadata_and_overflow_json_are_rejected(self):
        for number in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(number=number):
                run = copy.deepcopy(self.run)
                run["client_integrity"]["event_loop_lag_max_s"] = number
                self.assertEqual(self.evaluate(run)[0]["status"], "invalid")
        with tempfile.TemporaryDirectory() as tmp:
            d = write_events(Path(tmp), self.run, self.records)
            path = d / "run.json"
            path.write_text(
                path.read_text().replace('"hours_allocated": 0.0', '"hours_allocated": 1e999')
            )
            report = ev_mod.evaluate(self.plan, d, "native")[0]
            self.assertEqual(report["status"], "invalid")
            self.assertIn("non-finite", report["reasons"][0])

    def test_bad_run_shape_and_nested_request_are_named_invalid_evidence(self):
        self.assertEqual(self.evaluate([])[0]["status"], "invalid")
        run = copy.deepcopy(self.run)
        del run["mode"]
        self.assertEqual(self.evaluate(run)[0]["status"], "invalid")
        records = copy.deepcopy(self.records)
        records[0]["events"] = {"unexpected": "object"}
        report = self.evaluate(records=records)[0]
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("schema" in why for why in report["reasons"]))
        self.assertFalse(any("evaluator error" in why for why in report["reasons"]))

    def test_missing_lag_max_or_samples_is_not_valid_evidence(self):
        for key, value in (("event_loop_lag_max_s", None), ("samples", 0)):
            run = copy.deepcopy(self.run)
            run["client_integrity"][key] = value
            self.assertEqual(self.evaluate(run)[0]["status"], "invalid")

    def test_measurement_cannot_be_relabelled_as_a_startup_probe(self):
        run = copy.deepcopy(self.run)
        record = self.records[0]
        launch = run["startup"][0]
        launch["generation_probe_request_ids"] = [record["request_id"]]
        launch["first_valid_request_id"] = record["request_id"]
        launch["first_valid_generation_at"] = record["timestamps_mono_s"]["end"]
        report = self.evaluate(run)[0]
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("wrong phase" in why for why in report["reasons"]))

    def test_startup_probe_must_follow_launch_and_cannot_be_reused(self):
        run = copy.deepcopy(self.run)
        run["startup"][0]["launch_mono_s"] = 999.0
        report = self.evaluate(run)[0]
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("predates" in why for why in report["reasons"]))
        run = copy.deepcopy(self.run)
        run["startup"].append(copy.deepcopy(run["startup"][0]))
        run["startup"][1]["launch_index"] = 1
        run["planned_startup_launches"] = 2
        report = self.evaluate(run)[0]
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("reused" in why for why in report["reasons"]))

    def test_missing_planned_startup_repetitions_is_inconclusive(self):
        run = copy.deepcopy(self.run)
        run["planned_startup_launches"] = 3
        report = self.evaluate(run)[0]
        self.assertEqual(report["status"], "inconclusive")
        self.assertIn("1 launches recorded < 3 planned", " ".join(report["inconclusive_reasons"]))

    def test_comparison_corrects_for_four_metrics_even_for_one_cell(self):
        base = self.evaluate()
        result = ev_mod.compare_runs(self.plan, base, base)
        cell = result["per_cell"]["chat_short_open|rate=4rps"]
        self.assertEqual(cell["comparison_family_size"], 4)
        self.assertAlmostEqual(cell["confidence_adjusted"], 1 - 0.05 / 4)
        self.assertIn(result["decision"], ("no_difference", "inconclusive"))

    def test_quick_population_cannot_reduce_or_omit_the_plans_schedule(self):
        self.plan["modes"]["quick"]["cells"] = ["chat_short_open"]
        unit = resolve_mode(self.plan, "quick")[0]
        records = clean_cell(self.plan, unit.cell_id, unit.requests)
        for index in range(unit.warmup):
            record = copy.deepcopy(records[0])
            record["phase"] = "warmup"
            record["request_id"] = f"{unit.unit_id}/warmup/{index}"
            records.append(record)
        run = make_run(records)
        run["mode"] = "quick"
        run["units"][0]["planned_warmup"] = unit.warmup
        self.assertEqual(self.evaluate(run, records)[0]["status"], "valid")
        relabelled = copy.deepcopy(run)
        relabelled["units"][0]["prompt_source"] = "rag_long"
        relabelled["units"][0]["deadline_profile"] = "rag"
        relabelled_records = copy.deepcopy(records)
        for record in relabelled_records:
            if record["phase"] != "startup_probe":
                record["prompt"]["source"] = "rag_long"
        report = self.evaluate(relabelled, relabelled_records)[0]
        self.assertEqual(report["status"], "invalid")
        self.assertIn("prompt_source differs from workload plan", " ".join(report["reasons"]))
        no_warmup = [r for r in records if r["phase"] != "warmup"]
        report = self.evaluate(run, no_warmup)[0]
        self.assertEqual(report["status"], "inconclusive")
        self.assertIn("incomplete warmup evidence", " ".join(report["inconclusive_reasons"]))
        run["units"][0]["planned_requests"] = 0
        report = self.evaluate(run, records)[0]
        self.assertEqual(report["status"], "invalid")
        self.assertIn("planned_requests differs", " ".join(report["reasons"]))
        run["units"] = []
        records = [r for r in records if r["phase"] == "startup_probe"]
        report = self.evaluate(run, records)[0]
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["population"]["units"][unit.unit_id]["not_offered"], unit.requests)

    def test_prompt_source_cannot_bypass_declared_workload_identity(self):
        records = copy.deepcopy(self.records)
        records[0]["prompt"]["source"] = "generation_long"
        report = self.evaluate(records=records)[0]
        self.assertEqual(report["status"], "invalid")
        self.assertIn("prompt source differs", " ".join(report["reasons"]))

    def test_request_identity_must_agree_with_its_declared_unit(self):
        records = copy.deepcopy(self.records)
        records[0]["cell_id"] = "generation_long_open"
        report = self.evaluate(records=records)[0]
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("cell_id differs" in why for why in report["reasons"]))


if __name__ == "__main__":
    unittest.main()
