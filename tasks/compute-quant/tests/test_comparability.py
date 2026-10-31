"""As-of handling, product comparability and quote-type semantics."""

import unittest

from .support import CaseBuilder, node, quote_row, run_demo


class AsOfTests(unittest.TestCase):
    def setUp(self):
        self.results = run_demo()

    def test_post_as_of_quote_excluded_as_look_ahead(self):
        row = quote_row(self.results, "q11")
        self.assertEqual(row["as_of_status"], "post_as_of")
        self.assertFalse(row["curve_eligible"])
        self.assertTrue(any("look-ahead" in r for r in row["reasons"]))
        for n in self.results["curve"]["nodes"]:
            self.assertNotIn("q11", n["evidence_ids"])

    def test_expired_quote_flagged_and_excluded(self):
        row = quote_row(self.results, "q10")
        self.assertEqual(row["as_of_status"], "expired")
        self.assertFalse(row["curve_eligible"])
        self.assertTrue(any("expired" in r for r in row["reasons"]))
        hist = self.results["cross_checks"]["historical_variability"]
        self.assertEqual(hist["pairs"], 1)
        self.assertEqual(hist["examples"][0]["expired_quote_id"], "q10")
        self.assertEqual(hist["examples"][0]["current_quote_id"], "q01")

    def test_valid_count(self):
        self.assertEqual(self.results["valid_quote_count"], 9)

    def test_as_of_override_moves_forward_quote_out_of_validity(self):
        results = CaseBuilder(self).run(as_of_override="2026-10-09T00:00:00Z")
        self.assertEqual(quote_row(results, "q06")["as_of_status"], "expired")
        self.assertEqual(quote_row(results, "q05")["as_of_status"], "valid")
        self.assertIsNone(node(results, 3)["bid"])
        self.assertEqual(node(results, 3)["ask"], 4.5)

    def test_forward_whose_window_already_started_is_not_a_forward_node(self):
        results = CaseBuilder(self).run(as_of_override="2026-12-15T00:00:00Z")
        row = quote_row(results, "q04")
        self.assertEqual(row["tenor_months"], [])
        self.assertFalse(row["curve_eligible"])


class ProductComparabilityTests(unittest.TestCase):
    def assert_not_comparable(self, results, qid, fragment):
        row = quote_row(results, qid)
        self.assertEqual(row["tier"], "different_product")
        self.assertFalse(row["curve_eligible"])
        self.assertTrue(any(fragment in r for r in row["reasons"]), row["reasons"])

    def test_baseline_forward_is_comparable_and_eligible(self):
        row = quote_row(run_demo(), "q04")
        self.assertEqual(row["tier"], "comparable")
        self.assertTrue(row["curve_eligible"])

    def test_hardware_change_removes_from_comparable_set(self):
        results = CaseBuilder(self).set("q04", gpu="H200").run()
        self.assert_not_comparable(results, "q04", "gpu")
        self.assertEqual(node(results, 1)["status"], "implied")

    def test_region_change(self):
        results = CaseBuilder(self).set("q04", region="R2").run()
        self.assert_not_comparable(results, "q04", "region")

    def test_preemptible_change(self):
        results = CaseBuilder(self).set("q04", preemptible="true").run()
        row = quote_row(results, "q04")
        self.assertEqual(row["contract_class"], "spot")
        self.assertFalse(row["curve_eligible"])

    def test_isolation_change(self):
        results = CaseBuilder(self).set("q04", isolation="shared").run()
        self.assert_not_comparable(results, "q04", "isolation")

    def test_lower_sla_change(self):
        results = CaseBuilder(self).set("q04", sla_pct="99.0").run()
        self.assert_not_comparable(results, "q04", "sla_pct")

    def test_higher_sla_still_comparable(self):
        results = CaseBuilder(self).set("q04", sla_pct="99.99").run()
        self.assertEqual(quote_row(results, "q04")["tier"], "comparable")

    def test_memory_change(self):
        results = CaseBuilder(self).set("q04", memory_gib="256").run()
        self.assert_not_comparable(results, "q04", "memory_gib")

    def test_cancellation_change_moves_bucket(self):
        results = CaseBuilder(self).set("q04", cancellation="anytime").run()
        row = quote_row(results, "q04")
        self.assertEqual(row["contract_class"], "flexible_on_demand")
        self.assertFalse(row["curve_eligible"])
        self.assertNotEqual(node(results, 1)["status"], "observed")

    def test_payment_change_changes_normalized_price(self):
        base = quote_row(run_demo(), "q04")["usd_per_gpu_hour"]
        changed = quote_row(CaseBuilder(self).set("q04", payment="advance").run(), "q04")
        self.assertGreater(changed["usd_per_gpu_hour"], base)

    def test_capacity_not_guaranteed_forward_is_not_reserved(self):
        results = CaseBuilder(self).set("q04", capacity_guaranteed="false").run()
        row = quote_row(results, "q04")
        self.assertEqual(row["contract_class"], "unclassified_rights")
        self.assertFalse(row["curve_eligible"])


class QuoteTypeSemanticsTests(unittest.TestCase):
    def setUp(self):
        self.results = run_demo()

    def test_usage_commitment_is_not_a_forward(self):
        row = quote_row(self.results, "q03")
        self.assertEqual(row["contract_class"], "usage_commitment")
        self.assertFalse(row["curve_eligible"])
        ctx = self.results["cross_checks"]["usage_commitments"]["examples"]
        self.assertEqual(ctx[0]["quote_id"], "q03")
        self.assertIn("NOT a forward", ctx[0]["treatment"])
        self.assertEqual(ctx[0]["minimum_monthly_gpu_hours_per_common_terms"], 600)

    def test_bid_ask_mid_at_jan_2027(self):
        n = node(self.results, 3)
        self.assertEqual(n["status"], "observed")
        self.assertEqual((n["bid"], n["ask"]), (4.0, 4.5))
        self.assertEqual(n["usd_per_gpu_hour_mid"], 4.25)
        self.assertEqual((n["usd_per_gpu_hour_low"], n["usd_per_gpu_hour_high"]), (4.0, 4.5))
        self.assertEqual(n["uncertainty_type"], "bid-ask")
        self.assertEqual(sorted(n["evidence_ids"]), ["q05", "q06"])

    def test_ask_only_node_keeps_ask_as_upper_edge(self):
        n = node(self.results, 1)
        self.assertEqual(n["status"], "observed")
        self.assertEqual(n["ask"], 4.9)
        self.assertIsNone(n["bid"])
        self.assertEqual(n["usd_per_gpu_hour_high"], 4.9)
        self.assertLess(n["usd_per_gpu_hour_mid"], 4.9)
        self.assertIn("one-sided", n["uncertainty_type"])

    def test_spot_is_context_not_comparable(self):
        row = quote_row(self.results, "q09")
        self.assertEqual(row["contract_class"], "spot")
        self.assertEqual(row["tier"], "different_product")
        spot = self.results["cross_checks"]["spot"]["examples"]
        self.assertEqual(spot[0]["quote_id"], "q09")
        self.assertAlmostEqual(
            spot[0]["discount_vs_cheapest_flexible_on_demand_ask_per_gpu_hour"], 1 - 1.8 / 4.5
        )

    def test_h200_only_cross_check_with_uncertainty(self):
        xc = self.results["cross_checks"]["performance_translation"]["examples"][0]
        self.assertEqual(xc["quote_id"], "q08")
        self.assertEqual(xc["status"], "cross-check-only")
        self.assertAlmostEqual(xc["throughput_ratio_reference_over_quote_gpu"], 1.25)
        self.assertAlmostEqual(xc["equivalent_usd_per_reference_gpu_hour"], 3.3 * 1.25)
        self.assertAlmostEqual(xc["ratio_relative_uncertainty"], (0.1**2 + 0.1**2) ** 0.5)
        self.assertLess(xc["equivalent_low"], xc["equivalent_usd_per_reference_gpu_hour"])
        self.assertGreater(xc["equivalent_high"], xc["equivalent_usd_per_reference_gpu_hour"])
        for n in self.results["curve"]["nodes"]:
            self.assertNotIn("q08", n["evidence_ids"])

    def test_h200_without_performance_row_cannot_be_translated(self):
        builder = CaseBuilder(self)
        builder.case["files"].pop("performance")
        xc = builder.run()["cross_checks"]["performance_translation"]["examples"][0]
        self.assertIsNone(xc["equivalent_usd_per_reference_gpu_hour"])
