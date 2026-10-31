"""Production-shaped identity gate units and descriptive-only inference diagnostics.

Identity-only tokenizer stand-ins exercise predicates, not token counts or full
real-run validity. Historical calibration is read without modifying its evidence.
"""

import copy
import hashlib
import json
import tarfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import evaluate as ev_mod
from bench.model_tokens import model_spec
from bench.plan import load_plan, resolve_mode
from bench.records import Evidence, RequestRecord, RunRecord, validate_run_record
from bench.stats import bonferroni, bootstrap_diagnostics

from tests.helpers import SUITE, plan


class CalibrationIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        directory = SUITE.parent / "validation/live-calibration-20261026"
        cls.frozen = load_plan(directory / "frozen-plan.json")
        with tarfile.open(directory / "calibration.tar.gz") as archive:
            raw_run = archive.extractfile("events/run.json").read()
            raw_requests = archive.extractfile("events/requests.jsonl").read()
        expected = cls.frozen["calibration"]["raw_evidence_sha256"]
        assert hashlib.sha256(raw_run).hexdigest() == expected["run.json"]
        assert hashlib.sha256(raw_requests).hexdigest() == expected["requests.jsonl"]
        cls.original_run = json.loads(raw_run)
        cls.records = [RequestRecord(json.loads(line)) for line in raw_requests.splitlines()]

    def evaluator(self, role="target", workload=None):
        workload = copy.deepcopy(self.frozen if workload is None else workload)
        workload["target"]["model_role"] = role
        spec = model_spec(role)
        model = f"{spec['repository']}@{spec['revision']}"
        identity = copy.deepcopy(self.frozen["calibration"]["tokenizer_identity"])
        identity.update(
            model=model,
            identifier=f"hf-tokenizers:{model}:sha256:{spec['files']['tokenizer.json']['sha256']}",
            files={name: asset["sha256"] for name, asset in spec["files"].items()},
        )
        selected = SimpleNamespace(role=role, model=model, identity=identity)
        run = copy.deepcopy(self.original_run)
        digest = (
            "sha256:" + hashlib.sha256(json.dumps(workload, sort_keys=True).encode()).hexdigest()
        )
        run["plan"].update(plan_id=workload["plan_id"], digest=digest)
        run["engine"].update(kind="pinned-llmd", lock_model=model)
        run["tokenizer"] = copy.deepcopy(identity)
        self.assertEqual(validate_run_record(run), [])
        evidence = Evidence(Path("."), RunRecord(run), self.records, [], "native")
        evaluator = ev_mod.Evaluator(workload, evidence, digest, selected)
        evaluator.expected_units = {u.unit_id: u for u in resolve_mode(workload, "calibration")}
        return evaluator

    def gate(self, evaluator):
        evaluator.run_gates()
        return " ".join(evaluator.reasons)

    def test_matching_real_calibration_allows_image_and_digest_changes(self):
        evaluator = self.evaluator()
        evaluator.ev.run.raw["engine"].update(
            lock_image="candidate@sha256:" + "b" * 64, lock_digest="a" * 64
        )
        self.assertEqual(self.gate(evaluator), "")

    def test_protected_smoke_role_cannot_reuse_target_calibration(self):
        evaluator = self.evaluator("smoke")
        reasons = self.gate(evaluator)
        self.assertIn("model pin differs from frozen calibration", reasons)
        self.assertIn("tokenizer semantics differ from frozen calibration", reasons)

    def test_selected_run_model_control_still_rejects_mismatch(self):
        evaluator = self.evaluator()
        evaluator.ev.run.raw["engine"]["lock_model"] = "wrong/model@revision"
        self.assertIn("run model pin differs from selected tokenizer", self.gate(evaluator))

    def test_calibration_semantics_mismatch_or_incomplete_is_named(self):
        for field in ("files", "runtime", "unicode_database", "offset_mapping"):
            with self.subTest(field=field):
                evaluator = self.evaluator()
                del evaluator.plan["calibration"]["tokenizer_identity"][field]
                self.assertIn(
                    "calibration verified tokenizer identity is incomplete", self.gate(evaluator)
                )
        evaluator = self.evaluator()
        evaluator.plan["calibration"]["tokenizer_identity"]["runtime"] = "different-runtime"
        self.assertIn("semantics differ from frozen calibration", self.gate(evaluator))

    def test_extra_calibration_provenance_is_not_count_semantics(self):
        evaluator = self.evaluator()
        evaluator.plan["calibration"]["tokenizer_identity"]["capture_note"] = "extra provenance"
        self.assertEqual(self.gate(evaluator), "")

    def test_unfrozen_real_and_explicit_mock_fit_only_legacy_stay_supported(self):
        workload = copy.deepcopy(self.frozen)
        del workload["calibration"]
        self.assertEqual(self.gate(self.evaluator(workload=workload)), "")
        evaluator = self.evaluator()
        evaluator.plan["calibration"] = {"fit": {"length_domain": [44, 4117]}}
        evaluator.ev.run.raw["engine"]["kind"] = "mock"
        evaluator.ev.run.raw["tokenizer"] = None
        evaluator.tokenizer = None
        self.assertEqual(self.gate(evaluator), "")

    def test_real_run_cannot_use_explicit_mock_calibration(self):
        evaluator = self.evaluator()
        evaluator.plan["calibration"]["engine_kind"] = "mock"
        self.assertIn("real evaluation cannot use a mock calibration", self.gate(evaluator))

    def comparison(self, a, b):
        key = "unit|load"
        sample = {"ttft": [1.0] * 30, "fi_strict": [1.0] * 30, "mdd": [0.1] * 30}
        report = {"status": "valid", "cells": {key: {"role": "measure"}}}
        return ev_mod.compare_runs(a.plan, (report, a, {key: sample}), (report, b, {key: sample}))

    def test_compare_api_rejects_models_tokenizers_and_plans_but_allows_images(self):
        a, b = self.evaluator(), self.evaluator()
        b.ev.run.raw["engine"].update(lock_image="different-image", lock_digest="c" * 64)
        self.assertEqual(self.comparison(a, b)["decision"], "no_difference")
        wrong = self.evaluator("smoke")
        self.assertEqual(self.comparison(a, wrong)["decision"], "invalid")
        wrong = self.evaluator()
        wrong.ev.run.raw["tokenizer"]["runtime"] = "different-runtime"
        self.assertEqual(self.comparison(a, wrong)["decision"], "invalid")
        wrong = self.evaluator()
        wrong.plan["statistics"]["bootstrap_resamples"] += 1
        self.assertEqual(self.comparison(a, wrong)["decision"], "invalid")
        wrong = self.evaluator()
        wrong.ev.run.raw["plan"]["digest"] = "sha256:" + "f" * 64
        self.assertEqual(self.comparison(a, wrong)["decision"], "invalid")


class InferenceDiagnosticsTests(unittest.TestCase):
    def test_tail_and_p99_diagnostics_are_constant_work_and_do_not_resample(self):
        with mock.patch("bench.stats.random.Random", side_effect=AssertionError("no resampling")):
            diagnostics = bootstrap_diagnostics(
                resamples=2000,
                confidence=bonferroni(0.95, 48),
                family_size=48,
                n_a=90,
                n_b=90,
                p99=True,
            )
        self.assertAlmostEqual(diagnostics["expected_resamples_per_tail"], 1.0416666667)
        self.assertEqual(diagnostics["bootstrap_resamples"], 2000)
        self.assertTrue(diagnostics["diagnostic_only"])
        self.assertFalse(diagnostics["measured_power_established"])
        support = diagnostics["p99_data_support"]
        self.assertAlmostEqual(
            support["probability_sample_max_below_population_quantile_if_iid_continuous"]["a"],
            0.99**90,
        )

    def test_diagnostics_do_not_add_ci_calls_or_change_decision_policy(self):
        workload = plan()
        evaluator = SimpleNamespace(stats_cfg=workload["statistics"], seed=1)
        samples = {"ttft": [1.0] * 90, "fi_strict": [1.0] * 90, "mdd": [1.0] * 90}
        diff = {"point": 0.0, "low": 0.0, "high": 0.0, "n_a": 90, "n_b": 90}
        with mock.patch("evaluate.difference_ci", return_value=diff) as ci:
            result = ev_mod.compare_groups(
                evaluator, samples, evaluator, samples, label="unit", salt=0, comparisons=12
            )
        self.assertEqual(ci.call_count, 4)
        self.assertEqual(result["decision"], "no_difference")
        self.assertEqual(result["comparison_family_size"], 48)
        self.assertTrue(all(m["decision"] == "no_difference" for m in result["metrics"].values()))
        self.assertIn(
            "p99_data_support",
            result["metrics"]["min_decode_deadline_p99_s"]["inference_diagnostics"],
        )

    def test_unresolved_mdd_stays_inconclusive_with_diagnostics(self):
        evaluator = SimpleNamespace(stats_cfg=plan()["statistics"], seed=1)
        samples = {"ttft": [1.0] * 30, "fi_strict": [1.0] * 30, "mdd": [], "mdd_unresolved": 1}
        with mock.patch(
            "evaluate.difference_ci",
            return_value={"point": 0.0, "low": 0.0, "high": 0.0, "n_a": 30, "n_b": 30},
        ) as ci:
            result = ev_mod.compare_groups(
                evaluator, samples, evaluator, samples, label="unit", salt=0
            )
        self.assertEqual(ci.call_count, 3)
        self.assertEqual(result["decision"], "inconclusive")
        mdd = result["metrics"]["min_decode_deadline_p99_s"]
        self.assertIsNone(mdd["difference"])
        self.assertIn("p99_data_support", mdd["inference_diagnostics"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
