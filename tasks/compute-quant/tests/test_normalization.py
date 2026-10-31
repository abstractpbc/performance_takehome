"""Unit, calendar-hour, payment-timing, fee, currency and quantity normalization."""

import unittest
from decimal import Decimal

from research.months import add_months, hours_in_month, parse_utc
from research.schema import InputError

from .support import CaseBuilder, quote_row, run_demo


class CalendarHoursTests(unittest.TestCase):
    def test_actual_hours_never_720_by_default(self):
        self.assertEqual(hours_in_month(parse_utc("2026-10-01T00:00:00Z")), 744)
        self.assertEqual(hours_in_month(parse_utc("2026-11-01T00:00:00Z")), 720)
        self.assertEqual(hours_in_month(parse_utc("2027-02-01T00:00:00Z")), 672)
        self.assertEqual(hours_in_month(parse_utc("2028-02-01T00:00:00Z")), 696)

    def test_add_months_crosses_years(self):
        start = parse_utc("2026-10-01T00:00:00Z")
        self.assertEqual(add_months(start, 3).isoformat(), "2027-01-01T00:00:00+00:00")
        self.assertEqual(add_months(start, 12).isoformat(), "2027-10-01T00:00:00+00:00")

    def test_naive_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            parse_utc("2026-10-01T00:00:00")


class UnitNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.results = run_demo()

    def test_gpu_hour_passthrough(self):
        self.assertEqual(quote_row(self.results, "q01")["usd_per_gpu_hour"], 5.2)

    def test_node_hour_divided_by_gpu_count(self):
        self.assertEqual(quote_row(self.results, "q02")["usd_per_gpu_hour"], 36.0 / 8)

    def test_node_month_uses_actual_october_hours_and_advance_timing(self):
        row = quote_row(self.results, "q07")
        unit_step = next(s for s in row["steps"] if s["step"] == "unit")
        self.assertIn("744 actual hours", unit_step["note"])
        self.assertAlmostEqual(unit_step["after"], 24000 / (8 * 744), places=7)
        timing = next(s for s in row["steps"] if s["step"] == "payment_timing")
        factor = 1.05 ** (744 / 8760)
        self.assertAlmostEqual(timing["after"], unit_step["after"] * factor, places=6)
        self.assertGreater(timing["after"], timing["before"])
        self.assertNotAlmostEqual(unit_step["after"], 24000 / (8 * 720), places=3)

    def test_arrears_quote_has_no_timing_adjustment(self):
        row = quote_row(self.results, "q04")
        timing = next(s for s in row["steps"] if s["step"] == "payment_timing")
        self.assertEqual(timing["before"], timing["after"])

    def test_node_month_in_february_uses_672_hours(self):
        builder = CaseBuilder(self).copy_quote(
            "q07",
            "qfeb",
            delivery_start="2027-02-01T00:00:00Z",
            delivery_end="2027-03-01T00:00:00Z",
            payment="arrears",
        )
        results = builder.run()
        row = quote_row(results, "qfeb")
        self.assertAlmostEqual(row["usd_per_gpu_hour"], 24000 / (8 * 672), places=6)

    def test_node_month_without_window_is_excluded_explicitly(self):
        results = CaseBuilder(self).set("q07", delivery_start="", delivery_end="").run()
        row = quote_row(results, "q07")
        self.assertIsNone(row["usd_per_gpu_hour"])
        self.assertIn("node_month", row["excluded_reason"])

    def test_quantity_factor_from_paired_on_demand_asks(self):
        factor = self.results["quantity_adjustment"]["factor"]
        self.assertAlmostEqual(factor, 4.5 / 5.2, places=5)
        q07 = quote_row(self.results, "q07")
        self.assertAlmostEqual(
            q07["single_gpu_equivalent_usd_per_gpu_hour"],
            q07["usd_per_gpu_hour"] / factor,
            places=5,
        )
        self.assertEqual(
            quote_row(self.results, "q04")["single_gpu_equivalent_usd_per_gpu_hour"],
            quote_row(self.results, "q04")["usd_per_gpu_hour"],
        )

    def test_quantity_not_adjusted_without_pair(self):
        results = CaseBuilder(self).drop("q01").run()
        self.assertIsNone(results["quantity_adjustment"]["factor"])
        q07 = quote_row(results, "q07")
        self.assertEqual(q07["single_gpu_equivalent_usd_per_gpu_hour"], q07["usd_per_gpu_hour"])
        self.assertTrue(any("not adjusted" in s["note"] for s in q07["steps"]))


class FeeTests(unittest.TestCase):
    def test_recurring_fee_added_per_gpu_hour(self):
        results = CaseBuilder(self).set("q04", fee_usd_per_gpu_hour="0.10").run()
        self.assertAlmostEqual(quote_row(results, "q04")["usd_per_gpu_hour"], 5.0, places=9)

    def test_one_time_fee_amortized_over_window_hours_and_gpus(self):
        results = CaseBuilder(self).set("q05", one_time_fee_usd="74.40").run()
        self.assertAlmostEqual(
            quote_row(results, "q05")["usd_per_gpu_hour"], 4.5 + 74.4 / 744, places=9
        )

    def test_one_time_fee_without_window_excluded(self):
        results = CaseBuilder(self).set("q01", one_time_fee_usd="100").run()
        self.assertIn("cannot amortize", quote_row(results, "q01")["excluded_reason"])

    def test_no_fee_evidence_is_recorded_not_assumed_zero_silently(self):
        row = quote_row(run_demo(), "q01")
        fee_step = next(s for s in row["steps"] if s["step"] == "fees")
        self.assertIn("no fee evidence", fee_step["note"])


class CurrencyTests(unittest.TestCase):
    def test_unknown_currency_excluded_with_reason(self):
        results = CaseBuilder(self).set("q04", currency="EUR").run()
        row = quote_row(results, "q04")
        self.assertIsNone(row["usd_per_gpu_hour"])
        self.assertIn("EUR", row["excluded_reason"])
        self.assertIn("fx_rates_to_usd", row["excluded_reason"])
        self.assertIn("q04", {e["id"] for e in results["exclusions"]["rows"]})
        self.assertNotIn("q04", results["curve"]["nodes"][0]["evidence_ids"])

    def test_declared_fx_rate_converts_and_documents(self):
        builder = CaseBuilder(self).set("q04", currency="EUR", price="4.00")
        builder.case["fx_rates_to_usd"] = {"EUR": "1.10"}
        row = quote_row(builder.run(), "q04")
        self.assertAlmostEqual(row["usd_per_gpu_hour"], 4.4, places=9)
        self.assertIn("EUR->USD", row["steps"][0]["note"])

    def test_negative_fx_rate_rejected(self):
        builder = CaseBuilder(self)
        builder.case["fx_rates_to_usd"] = {"EUR": "-1"}
        with self.assertRaises(InputError):
            builder.run()


class DecimalExactnessTests(unittest.TestCase):
    def test_price_quantized_to_six_places(self):
        row = quote_row(run_demo(), "q07")
        self.assertEqual(Decimal(str(row["usd_per_gpu_hour"])).as_tuple().exponent >= -6, True)
