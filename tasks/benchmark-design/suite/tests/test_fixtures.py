"""Generated fixtures: expected detections hold, and results never depend on fixture identity."""

import copy
import gzip
import hashlib
import json
import os
import random
import shutil
import tempfile
import unittest
from pathlib import Path

import evaluate as ev_mod
from bench.plan import load_plan, plan_digest
from fixtures.expectations import run_checks
from fixtures.generate import DEFAULT_KEY_PATH

from tests.helpers import MOCK_PLAN_PATH, SUITE

PUBLIC = SUITE / "fixtures" / "public"
HELDOUT = SUITE / "fixtures" / "heldout"


def strip_identity(report: dict) -> dict:
    report = copy.deepcopy(report)
    report.pop("events_dir", None)
    return report


@unittest.skipUnless(PUBLIC.is_dir() and any(PUBLIC.iterdir()), "fixtures not generated")
class PublicFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.plan = load_plan(MOCK_PLAN_PATH)
        cls.reports = {}
        for d in sorted(PUBLIC.iterdir()):
            if (d / "label.json").is_file():
                cls.reports[d.name] = ev_mod.evaluate(cls.plan, d, "native")[0]

    def label(self, name: str) -> dict:
        return json.loads((PUBLIC / name / "label.json").read_text(encoding="utf-8"))

    def test_expected_status_per_family(self) -> None:
        for name, report in self.reports.items():
            with self.subTest(fixture=name):
                self.assertEqual(
                    report["status"], self.label(name)["expected"]["status"], report.get("reasons")
                )

    def test_shipped_fixture_workload_bytes_match_recorded_digest(self) -> None:
        digest = plan_digest(MOCK_PLAN_PATH)
        for directory in sorted(PUBLIC.iterdir()):
            run_path = directory / "run.json"
            if run_path.is_file():
                with self.subTest(fixture=directory.name):
                    run = json.loads(run_path.read_text(encoding="utf-8"))
                    self.assertEqual(run["plan"]["digest"], digest)

    def test_controls_have_no_rejections_and_stable_aa(self) -> None:
        for name in ("clean", "noisy"):
            report = self.reports[name]
            totals = report["population"]["totals"]
            self.assertEqual(totals["completed"], totals["offered"], name)
            self.assertIn(report["aa_control"]["decision"], ("no_difference", "inconclusive"), name)

    def test_server_faults_are_rejected_not_scored(self) -> None:
        expectations = {
            "missing_done": ("unfinished", "missing_done"),
            "missing_finish_reason": ("unfinished", "missing_finish_reason"),
            "http_error": ("failed", "http_error"),
            "drop": ("unfinished", "missing_done"),
            "wrong_text": ("failed", "invalid_output"),
            "garbled_text": ("failed", "invalid_output"),
            "truncated_text": ("failed", "invalid_output"),
            "false_usage": ("failed", "usage_discrepancy"),
        }
        for name, (category, reason) in expectations.items():
            report = self.reports[name]
            with self.subTest(fixture=name):
                self.assertGreater(report["population"]["totals"][category], 0)
                reasons = {k for c in report["cells"].values() for k in c["rejection_reasons"]}
                self.assertIn(reason, reasons)

    def test_timing_families_move_the_right_metric(self) -> None:
        clean = self.reports["clean"]["cells"]
        key = "chat_short_open|rate=4rps"
        self.assertGreater(
            self.reports["late_first_token"]["cells"][key]["ttft_s"]["p50"]["point"],
            clean[key]["ttft_s"]["p50"]["point"] + 0.3,
        )
        for name in ("stall", "slow_decode"):
            with self.subTest(fixture=name):
                fi = self.reports[name]["cells"][key]["fluidity"]["strict"]["index_mean"]["point"]
                self.assertLess(fi, clean[key]["fluidity"]["strict"]["index_mean"]["point"])
        burst = self.reports["burst"]["cells"][key]
        self.assertIn("multi_token_deltas", burst["flags"])
        self.assertGreater(burst["token_evidence"]["tokens_per_delta_mean"], 4)
        self.assertEqual(burst["rejection_reasons"], {})  # bursts are evidence, not a fault
        self.assertGreater(
            self.reports["slow_decode"]["cells"][key]["decode"]["min_decode_deadline_s"]["p99"],
            clean[key]["decode"]["min_decode_deadline_s"]["p99"] * 2,
        )

    def test_readiness_families_show_in_startup_axis(self) -> None:
        for name in ("misleading_readiness", "slow_startup"):
            launch = self.reports[name]["startup"]["launches"][0]
            with self.subTest(fixture=name):
                self.assertEqual(launch["status"], "valid")
                self.assertGreater(launch["readiness_lead_s"], 0.1)
        self.assertGreater(
            self.reports["misleading_readiness"]["startup"]["launches"][0]["readiness_lead_s"],
            self.reports["clean"]["startup"]["launches"][0]["readiness_lead_s"],
        )

    def test_token_count_independent_of_split_and_reasoning(self) -> None:
        key = "chat_short_open|rate=4rps"
        for name in ("unicode_split", "reasoning_channel"):
            report = self.reports[name]
            with self.subTest(fixture=name):
                self.assertEqual(report["status"], "valid", report["reasons"])
                self.assertEqual(report["cells"][key]["rejection_reasons"], {})

    def test_oracle_agrees_with_evaluator_on_text_validity(self) -> None:
        for name in ("clean", "wrong_text", "false_usage"):
            with gzip.open(PUBLIC / name / "oracle.json.gz", "rt", encoding="utf-8") as fh:
                oracle = json.load(fh)["requests"]
            verdicts = self.reports[name]["request_verdicts"]
            for rid, truth in oracle.items():
                # Integer lists are excluded from the usage check: the regex tokenizer counts
                # them 2x the mock tokenizer, which puts a 3x usage inside the band.
                if not rid.startswith("chat_short_open") or "/measure/" not in rid:
                    continue
                failed = rid in verdicts and verdicts[rid]["category"] != "completed"
                expected_fail = (not truth["answer_is_correct"]) or truth[
                    "usage_completion_tokens"
                ] != truth["true_tokens"]
                self.assertEqual(failed, expected_fail, (name, rid))


@unittest.skipUnless(HELDOUT.is_dir() and any(HELDOUT.iterdir()), "held-out fixtures not generated")
class FixtureIdentityIndependenceTests(unittest.TestCase):
    def test_rename_and_permute_gives_identical_report(self) -> None:
        plan = load_plan(MOCK_PLAN_PATH)
        rng = random.Random(1234)
        for d in sorted(x for x in HELDOUT.iterdir() if x.is_dir())[:4]:
            original = strip_identity(ev_mod.evaluate(plan, d, "native")[0])
            with tempfile.TemporaryDirectory() as tmp:
                renamed = Path(tmp) / f"zz-{rng.randrange(10**9)}"
                shutil.copytree(d, renamed)
                with gzip.open(renamed / "requests.jsonl.gz", "rt", encoding="utf-8") as fh:
                    lines = [line for line in fh if line.strip()]
                rng.shuffle(lines)
                (renamed / "requests.jsonl").write_text("".join(lines), encoding="utf-8")
                (renamed / "requests.jsonl.gz").unlink()
                (renamed / "label.json").write_text(
                    '{"profile": "decoy", "expected": {"status": "invalid"}}'
                )
                permuted = strip_identity(ev_mod.evaluate(plan, renamed, "native")[0])
            self.assertEqual(
                json.dumps(original, sort_keys=True), json.dumps(permuted, sort_keys=True), d.name
            )

    def test_heldout_key_matches_evaluator(self) -> None:
        """Needs the out-of-tree key (~/.benchmark-design/ or $BENCHMARK_DESIGN_HELDOUT_KEY)."""
        key_path = Path(os.environ.get("BENCHMARK_DESIGN_HELDOUT_KEY", DEFAULT_KEY_PATH))
        if not key_path.is_file():
            self.skipTest("held-out key not available on this machine (kept outside the tree)")
        key_text = key_path.read_text(encoding="utf-8")
        committed = (HELDOUT / "COMMITMENT.sha256").read_text(encoding="utf-8").split()[0]
        self.assertEqual(hashlib.sha256(key_text.encode("utf-8")).hexdigest(), committed)
        key = {k: v for k, v in json.loads(key_text).items() if k != "_salt"}
        plan = load_plan(MOCK_PLAN_PATH)
        statuses = set()
        clean_name = next(n for n, v in key.items() if v["profile"] == "clean")
        clean = ev_mod.evaluate(plan, HELDOUT / clean_name, "native")[0]
        for name, truth in key.items():
            report = ev_mod.evaluate(plan, HELDOUT / name, "native")[0]
            statuses.add(truth["expected"]["status"])
            with self.subTest(fixture=name):
                self.assertEqual(
                    report["status"], truth["expected"]["status"], report.get("reasons")
                )
                results = run_checks(report, clean, truth["expected"]["checks"])
                self.assertTrue(all(r["passed"] for r in results), [r["observed"] for r in results])
        self.assertEqual(statuses, {"valid", "invalid", "inconclusive"})


if __name__ == "__main__":
    unittest.main()
