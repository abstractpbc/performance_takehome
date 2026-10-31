"""Custom-config and uncapped exclusion-trail regressions from the final review."""
import csv
import io
import unittest

from research.pipeline import load_config, render_normalization_csv
from research.report import render_report

from .support import CaseBuilder, run_demo


class FinalReviewTests(unittest.TestCase):
    def test_omitted_supply_anchor_settings_use_case_defaults(self):
        config = load_config(None)
        config["anchors"]["base"] = {"kind": "supply_cost_breakeven"}
        result = CaseBuilder(self).run(config)
        self.assertEqual(
            result["curve"]["scenario_anchor_levels"]["base"],
            run_demo()["curve"]["scenario_anchor_levels"]["base"],
        )

    def test_report_supports_configuration_without_cheap_compute_scenario(self):
        config = load_config(None)
        del config["scenarios"]["cheap_compute"]
        result = CaseBuilder(self).run(config)
        report = render_report(result)
        self.assertIn("Observed market mids:", report)
        self.assertNotIn("the cheap-compute anchor is", report)

    def test_uncapped_csv_preserves_reasons_when_json_examples_are_capped(self):
        config = load_config(None)
        config["output"] = {"max_quote_rows_in_valuation_json": 1}
        result = CaseBuilder(self).run(config)
        self.assertNotIn("q11", [row["id"] for row in result["quotes"]["rows"]])
        rows = {row["id"]: row for row in csv.DictReader(io.StringIO(
            render_normalization_csv(result)
        ))}
        self.assertIn("look-ahead", rows["q11"]["excluded_reason"])
        self.assertIn("product differs", rows["q08"]["excluded_reason"])
        self.assertIn("preemptible/spot", rows["q09"]["excluded_reason"])
        self.assertTrue(all(
            row["excluded_reason"] for row in rows.values() if row["curve_eligible"] == "False"
        ))
        self.assertEqual(rows["q04"]["excluded_reason"], "")
