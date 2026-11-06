"""Stage 3: axes, comparison decisions and the report contract. CPU-only."""

import json
import statistics
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SUITE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SUITE))

from analysis.axes import Analysis, Axis, analyze  # noqa: E402
from analysis.compare import (  # noqa: E402
    compare,
    decide,
    mean_interval,
    t_critical,
    welch_interval,
)
from analysis.events import load_plan  # noqa: E402
from matrix import EXPECTED  # noqa: E402
from synth import build_model, generate, slower_startup, slower_ttft, write  # noqa: E402

EVALUATE = SUITE / "evaluate.py"


def analysis_of(family, seed=1, mutate=None):
    with tempfile.TemporaryDirectory() as directory:
        if mutate is None:
            run_path, workload = generate(family, seed, Path(directory))
        else:
            model = build_model(seed)
            mutate(model)
            run_path, workload = write(model, Path(directory))
        plan = load_plan(workload)
        return analyze(run_path, plan), plan


class StatisticsTests(unittest.TestCase):
    def test_t_critical_matches_published_values(self):
        for confidence, df, expected in (
            (0.95, 4, 2.776), (0.95, 10, 2.228), (0.99, 7, 3.499), (0.95, 1, 12.706),
        ):
            self.assertAlmostEqual(t_critical(confidence, df), expected, places=2)

    def test_welch_with_no_variance_is_a_point(self):
        self.assertEqual(welch_interval([1, 1, 1], [1, 1, 1], 0.95), (0, 0, 0))

    def test_mean_interval_needs_two_values(self):
        self.assertIsNone(mean_interval([1.0], 0.95))

    def test_decision_boundaries(self):
        self.assertEqual(decide(0.2, 0.3, 0.1), "regression")
        self.assertEqual(decide(-0.3, -0.2, 0.1), "improvement")
        self.assertEqual(decide(-0.05, 0.05, 0.1), "equivalent")
        self.assertEqual(decide(0.05, 0.3, 0.1), "inconclusive")
        self.assertEqual(decide(-0.3, 0.3, 0.1), "inconclusive")


class AxisTests(unittest.TestCase):
    def test_late_first_token_shows_in_ttft_not_hidden_by_decode(self):
        clean, _ = analysis_of("clean")
        late, _ = analysis_of("late_first_token")
        before = clean.cells["chat_light"]["ttft"].summary["value"]
        after = late.cells["chat_light"]["ttft"].summary["value"]
        self.assertGreater(after, before + 2.5)

    def test_stall_lowers_fluidity(self):
        clean, _ = analysis_of("clean")
        stall, _ = analysis_of("stall")
        self.assertLess(
            stall.cells["chat_light"]["fluidity"].summary["value"],
            clean.cells["chat_light"]["fluidity"].summary["value"] - 0.02,
        )

    def test_burst_with_slack_is_not_penalized(self):
        burst, _ = analysis_of("burst")
        self.assertGreater(burst.cells["chat_light"]["fluidity"].summary["value"], 0.98)

    def test_failures_are_charged_zero_and_censored(self):
        result, _ = analysis_of("errors_few")
        axes = [result.cells[cell]["fluidity"] for cell in result.cells]
        self.assertEqual(sum(axis.summary["failed_charged_zero"] for axis in axes), 1)

    def test_one_chunk_ftgr_is_unresolved_never_infinite(self):
        result, _ = analysis_of("one_chunk")
        ftgr = result.cells["chat_light"]["ftgr"]
        self.assertEqual(ftgr.state, "inconclusive")
        self.assertEqual(ftgr.reasons, ("ftgr_unresolved",))
        self.assertIsNone(ftgr.summary["value"])

    def test_diagnostics_are_reported_without_affecting_state(self):
        clean, _ = analysis_of("clean")
        row = clean.diagnostics["chat_light"]
        self.assertAlmostEqual(row["tpot_s"]["p50"], 0.03, delta=0.003)
        self.assertAlmostEqual(row["request_tps"]["p50"], 33.3, delta=4)
        self.assertGreater(row["system_tps"], 0)
        self.assertNotIn("tpot", clean.cells["chat_light"])
        self.assertEqual(clean.state, "valid")

    def test_one_chunk_tpot_is_absent_never_infinite(self):
        result, _ = analysis_of("one_chunk")
        self.assertIsNone(result.diagnostics["chat_light"]["tpot_s"]["p50"])

    def test_anchors_and_canaries_stay_out_of_timing(self):
        result, _ = analysis_of("clean")
        self.assertEqual(result.cells["chat_light"]["ttft"].summary["population"], 5 * 54)

    def test_quantile_reported_only_with_enough_samples(self):
        result, plan = analysis_of("thin_run")
        self.assertEqual(result.cells["chat_light"]["ttft"].state, "inconclusive")
        self.assertIn("quantile_underpowered", result.reasons)


class ComparisonTests(unittest.TestCase):
    @staticmethod
    def rows(left, right, plan):
        return {
            (item["axis"], item["cell"]): item
            for item in compare(left, right, plan.data)["decisions"]
        }

    def test_ttft_regression_detected_and_fluidity_kept_separate(self):
        base, plan = analysis_of("clean", 1)
        slow, _ = analysis_of("", 2, lambda model: slower_ttft(model, 50))
        rows = self.rows(slow, base, plan)
        self.assertEqual(rows["ttft", "chat_light"]["decision"], "regression")
        self.assertEqual(rows["startup", None]["decision"], "equivalent")

    def test_reversed_regression_is_an_improvement(self):
        base, plan = analysis_of("clean", 1)
        slow, _ = analysis_of("", 2, lambda model: slower_ttft(model, 50))
        self.assertEqual(
            self.rows(base, slow, plan)["ttft", "near_capacity"]["decision"], "improvement"
        )

    def test_startup_regression_detected(self):
        base, plan = analysis_of("clean", 1)
        slow, _ = analysis_of("", 2, lambda model: slower_startup(model, 50))
        self.assertEqual(self.rows(slow, base, plan)["startup", None]["decision"], "regression")

    def test_invalid_run_makes_every_decision_invalid(self):
        base, plan = analysis_of("clean", 1)
        bad, _ = analysis_of("wrong_canary", 2)
        self.assertTrue(all(
            item["decision"] == "invalid" for item in self.rows(bad, base, plan).values()
        ))

    def test_identity_mismatch_is_invalid(self):
        base, plan = analysis_of("clean", 1)
        other, _ = analysis_of("clean", 2)
        other.manifest = {**other.manifest, "mode": "full"}
        decisions = compare(other, base, plan.data)["decisions"]
        self.assertTrue(all(item["reasons"] == ["identity_mismatch"] for item in decisions))

    def test_different_image_digest_is_allowed(self):
        base, plan = analysis_of("clean", 1)
        other, _ = analysis_of("clean", 2)
        other.manifest = {**other.manifest, "image_digest": "sha256:" + "1" * 64}
        self.assertEqual(self.rows(other, base, plan)["ttft", "chat_light"]["decision"],
                         "equivalent")

    def test_inconclusive_run_cannot_decide(self):
        base, plan = analysis_of("clean", 1)
        weak, _ = analysis_of("warmup_unfinished", 2)
        self.assertEqual(self.rows(weak, base, plan)["ttft", "chat_light"]["reasons"],
                         ["run_inconclusive"])

    def test_too_few_repetitions_cannot_decide(self):
        axis = Axis("valid", repetitions={"0": 1.0, "1": 1.1})
        manifest = {"mode": "quick"}
        left = Analysis("valid", (), manifest, None, None, {"chat_light": {
            "ttft": axis, "fluidity": axis}, "near_capacity": {"ttft": axis, "fluidity": axis}},
            axis)
        plan = load_plan(SUITE / "workload.json")
        for item in compare(left, left, plan.data)["decisions"]:
            self.assertEqual(item["reasons"], ["too_few_repetitions"])



class ReportTests(unittest.TestCase):
    def run_cli(self, family, *extra, directory):
        run_path, workload = generate(family, 1, Path(directory) / family)
        out = Path(directory) / f"{family}.json"
        done = subprocess.run(
            [sys.executable, "-B", str(EVALUATE), "--events", str(run_path),
             "--workload", str(workload), "--out", str(out), *extra],
            capture_output=True, text=True, cwd="/",
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        return json.loads(out.read_text()), done.stdout, run_path

    def test_report_has_required_content(self):
        with tempfile.TemporaryDirectory() as directory:
            report, stdout, _ = self.run_cli("clean", directory=directory)
        self.assertEqual(next(iter(report)), "summary")
        self.assertEqual(report["summary"]["state"], "valid")
        self.assertIn("RUN valid", stdout)
        for key in ("identity", "populations", "validity", "axes", "cost"):
            self.assertIn(key, report)
        self.assertEqual(set(report["cost"]["breakdown_s"]),
                         {"launch", "warmup", "requests", "drain", "cleanup"})
        self.assertTrue(report["identity"]["evidence"]["files"]["sse.jsonl"]["sha256"])
        self.assertEqual(report["axes"]["cells"]["chat_light"]["ttft"]["censored_at_s"], 30)

    def test_populations_balance(self):
        with tempfile.TemporaryDirectory() as directory:
            report, _, _ = self.run_cli("errors_many", directory=directory)
        totals = report["populations"]["totals"]
        self.assertEqual(
            totals["planned"],
            totals["completed"] + totals["failed"] + totals["excluded"] + totals["unfinished"],
        )
        self.assertGreater(totals["failed"], 0)

    def test_baseline_flag_adds_decisions(self):
        with tempfile.TemporaryDirectory() as directory:
            baseline, _ = generate("clean", 2, Path(directory) / "base")
            report, stdout, _ = self.run_cli(
                "clean", "--baseline", str(baseline), directory=directory
            )
        self.assertEqual(len(report["comparison"]["decisions"]), 5)
        self.assertIn("equivalent", stdout)

    def test_missing_evidence_writes_an_invalid_report(self):
        with tempfile.TemporaryDirectory() as directory:
            report, _, _ = self.run_cli("missing_tokens", directory=directory)
        self.assertEqual(report["summary"]["state"], "invalid")
        self.assertEqual(report["summary"]["reasons"], ["evidence_missing"])

    def test_corrupt_fields_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path, workload = generate("clean", 1, Path(directory))
            lines = (run_path / "requests.jsonl").read_text().splitlines()
            record = json.loads(lines[10])
            record["repetition"] = {"bad": 1}
            record["max_tokens"] = "lots"
            lines[10] = json.dumps(record)
            (run_path / "requests.jsonl").write_text("\n".join(lines) + "\n")
            result = analyze(run_path, load_plan(workload))
        self.assertEqual(result.state, "invalid")

    def test_all_families_report_without_error(self):
        with tempfile.TemporaryDirectory() as directory:
            for family, (state, _) in EXPECTED.items():
                with self.subTest(family=family):
                    report, _, _ = self.run_cli(family, directory=directory)
                    self.assertEqual(report["summary"]["state"], state)

    def test_stats_agree_with_statistics_module(self):
        values = [1.0, 2.0, 3.0, 4.0]
        center, low, high = mean_interval(values, 0.95)
        self.assertEqual(center, statistics.mean(values))
        self.assertLess(low, center)
        self.assertGreater(high, center)


if __name__ == "__main__":
    unittest.main()
