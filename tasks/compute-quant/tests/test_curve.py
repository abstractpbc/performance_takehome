"""Curve labels, intervals, the evidence-removal invariant and scenario behaviour."""

import copy
import unittest

from research.pipeline import load_config

from .support import CaseBuilder, node, run_demo, width

FORWARDS = ("q04", "q05", "q06")


class LabelTests(unittest.TestCase):
    def setUp(self):
        self.results = run_demo()

    def test_demo_labels(self):
        statuses = {n["tenor_months"]: n["status"] for n in self.results["curve"]["nodes"]}
        self.assertEqual(statuses, {1: "observed", 3: "observed", 6: "modeled", 12: "forecast"})

    def test_required_tenors_and_delivery_months(self):
        rows = [
            (n["tenor_months"], n["delivery_month"], n["hours"])
            for n in self.results["curve"]["nodes"]
        ]
        self.assertEqual(
            rows,
            [(1, "2026-11", 720), (3, "2027-01", 744), (6, "2027-04", 720), (12, "2027-10", 744)],
        )

    def test_modeled_nodes_lie_between_cheap_and_scarcity(self):
        for tenor in (6, 12):
            n = node(self.results, tenor)
            s = n["scenarios"]
            self.assertLess(s["cheap_compute"], s["base"])
            self.assertLess(s["base"], s["scarcity"])
            self.assertLessEqual(n["usd_per_gpu_hour_low"], s["cheap_compute"])
            self.assertGreaterEqual(n["usd_per_gpu_hour_high"], s["scarcity"])

    def test_interval_widens_with_horizon(self):
        self.assertLess(width(node(self.results, 3)), width(node(self.results, 6)))
        self.assertLess(width(node(self.results, 6)), width(node(self.results, 12)))

    def test_forecast_label_beyond_configured_horizon(self):
        config = copy.deepcopy(load_config(None))
        config["curve"]["model_horizon_months_before_forecast"] = 12
        results = CaseBuilder(self).run(config)
        self.assertEqual(node(results, 12)["status"], "modeled")

    def test_sparse_12_month_is_never_observed(self):
        self.assertNotEqual(node(self.results, 12)["status"], "observed")
        self.assertIsNone(node(self.results, 12)["bid"])
        self.assertIsNone(node(self.results, 12)["ask"])

    def test_kappa_fitted_from_observed_forward_nodes(self):
        rev = self.results["curve"]["reversion"]
        self.assertEqual(rev["source"], "fitted-least-squares-log-deviation")
        self.assertEqual(rev["observed_forward_tenors"], [1, 3])
        self.assertGreater(rev["kappa_per_month"], 0)


class RemovalInvariantTests(unittest.TestCase):
    """Removing evidence must widen (or leave unchanged) the interval at affected tenors."""

    def setUp(self):
        self.base = run_demo()

    def assert_not_narrower(self, results, tenors=(1, 3, 6, 12)):
        for tenor in tenors:
            before, after = node(self.base, tenor), node(results, tenor)
            self.assertGreaterEqual(width(after) + 1e-9, width(before), f"tenor {tenor} narrowed")

    def test_remove_jan_bid(self):
        results = run_demo(exclude_quote_ids=("q06",))
        self.assertEqual(node(results, 3)["status"], "observed")
        self.assertIsNone(node(results, 3)["bid"])
        self.assert_not_narrower(results)
        self.assertGreater(width(node(results, 3)), width(node(self.base, 3)))

    def test_remove_jan_ask(self):
        results = run_demo(exclude_quote_ids=("q05",))
        self.assertEqual(node(results, 3)["bid"], 4.0)
        self.assert_not_narrower(results)

    def test_remove_nov_ask(self):
        results = run_demo(exclude_quote_ids=("q04",))
        self.assertEqual(node(results, 1)["status"], "implied")
        self.assert_not_narrower(results)
        self.assertGreater(width(node(results, 1)), width(node(self.base, 1)))

    def test_remove_each_forward_individually(self):
        for qid in FORWARDS:
            with self.subTest(removed=qid):
                self.assert_not_narrower(run_demo(exclude_quote_ids=(qid,)))

    def test_remove_all_forwards_leaves_only_current_month_anchor(self):
        results = run_demo(exclude_quote_ids=FORWARDS)
        self.assert_not_narrower(results)
        statuses = {n["tenor_months"]: n["status"] for n in results["curve"]["nodes"]}
        self.assertNotIn("observed", statuses.values())
        self.assertEqual(results["curve"]["reversion"]["source"], "config-default")
        self.assertTrue(any("no observed forward node" in w for w in results["curve"]["warnings"]))

    def test_remove_everything_comparable_is_unidentified(self):
        results = run_demo(exclude_quote_ids=FORWARDS + ("q07",))
        for n in results["curve"]["nodes"]:
            self.assertEqual(n["status"], "unidentified")
            self.assertIsNone(n["usd_per_gpu_hour_mid"])

    def test_adding_a_bid_narrows_not_widens(self):
        results = (
            CaseBuilder(self)
            .copy_quote(
                "q06",
                "qbid",
                delivery_start="2026-11-01T00:00:00Z",
                delivery_end="2026-12-01T00:00:00Z",
                price="4.60",
            )
            .run()
        )
        n = node(results, 1)
        self.assertEqual(n["bid"], 4.6)
        self.assertEqual(n["uncertainty_type"], "bid-ask")
        self.assertLess(width(n), width(node(self.base, 1)))


class ModelBehaviourTests(unittest.TestCase):
    def test_higher_utilization_lowers_anchor_and_modeled_nodes(self):
        low_util = CaseBuilder(self)
        low_util.case["economics"]["operator_revenue_utilization_fraction"] = 0.5
        high_util = CaseBuilder(self)
        high_util.case["economics"]["operator_revenue_utilization_fraction"] = 0.8
        lo, hi = low_util.run(), high_util.run()
        self.assertGreater(
            lo["curve"]["scenario_anchor_levels"]["base"],
            hi["curve"]["scenario_anchor_levels"]["base"],
        )
        for tenor in (6, 12):
            self.assertGreater(
                node(lo, tenor)["usd_per_gpu_hour_mid"], node(hi, tenor)["usd_per_gpu_hour_mid"]
            )
        for tenor in (1, 3):
            self.assertEqual(
                node(lo, tenor)["usd_per_gpu_hour_mid"], node(hi, tenor)["usd_per_gpu_hour_mid"]
            )

    def test_higher_financing_raises_anchor(self):
        b = CaseBuilder(self)
        b.case["economics"]["operator_financing_annual_rate"] = 0.20
        self.assertGreater(
            b.run()["curve"]["scenario_anchor_levels"]["base"],
            run_demo()["curve"]["scenario_anchor_levels"]["base"],
        )

    def test_contango_shape_does_not_break_fit(self):
        results = CaseBuilder(self).set("q04", price="4.00").run()
        self.assertIn(
            results["curve"]["reversion"]["source"],
            {"fitted-clamped-to-zero", "config-default", "fitted-least-squares-log-deviation"},
        )
        self.assertIsNotNone(node(results, 12)["usd_per_gpu_hour_mid"])

    def test_nodes_straddling_anchor_fall_back_to_default_kappa(self):
        results = CaseBuilder(self).set("q05", price="2.60").set("q06", price="2.40").run()
        self.assertEqual(results["curve"]["reversion"]["source"], "config-default")

    def test_strip_quote_used_only_without_single_month_quote(self):
        results = (
            CaseBuilder(self)
            .copy_quote(
                "q05",
                "qstrip",
                delivery_start="2027-04-01T00:00:00Z",
                delivery_end="2027-07-01T00:00:00Z",
                price="3.90",
            )
            .run()
        )
        n = node(results, 6)
        self.assertEqual(n["status"], "implied")
        self.assertEqual(n["ask"], 3.9)

    def test_crossed_market_is_flagged(self):
        results = CaseBuilder(self).set("q06", price="4.70").run()
        self.assertTrue(any("crossed" in w for w in results["curve"]["warnings"]))
        n = node(results, 3)
        self.assertLessEqual(n["usd_per_gpu_hour_low"], n["usd_per_gpu_hour_high"])

    def test_wide_observed_spread_warns_about_fallback_fraction(self):
        results = CaseBuilder(self).set("q06", price="3.00").run()
        self.assertTrue(any("one_sided_spread_fraction" in w for w in results["curve"]["warnings"]))

    def test_monthly_base_curve_is_monotone_toward_anchor_after_last_observed(self):
        monthly = run_demo()["curve"]["monthly_base_mid"]
        values = [monthly[str(t)] for t in range(3, 13)]
        self.assertEqual(values, sorted(values, reverse=True))
