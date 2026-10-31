"""Independent adversarial checks for product evidence and signed recovery valuation."""

import unittest

from research.comparability import classify
from research.curve import build_curve_model
from research.loader import load_inputs
from research.normalize import apply_quantity_adjustment, estimate_quantity_factor, normalize_quote
from research.pipeline import load_config
from research.schema import InputError
from research.valuation import value_reservation

from .support import CaseBuilder, node, quote_row, run_demo


class ProductEvidenceTests(unittest.TestCase):
    def test_required_supporting_resources_need_common_term_evidence(self):
        for requirement, common_key, inadequate in (
            ("cpu_cores", "cpu_cores_per_gpu", 8),
            ("ram_gib", "ram_gib_per_gpu", 64),
            ("network_gbps", "network_gbps", 10),
        ):
            for supplied in (None, inadequate):
                builder = CaseBuilder(self)
                if supplied is None:
                    del builder.case["quote_common_terms"][common_key]
                else:
                    builder.case["quote_common_terms"][common_key] = supplied
                with self.subTest(requirement=requirement, supplied=supplied):
                    result = builder.run()
                    row = quote_row(result, "q04")
                    self.assertFalse(row["curve_eligible"])
                    self.assertIn(requirement, " ".join(row["reasons"]))
                    self.assertEqual(node(result, 1)["status"], "unidentified")
                    self.assertFalse(result["valuation"]["identified"])

    def test_sufficient_supporting_resources_preserve_original_curve(self):
        builder = CaseBuilder(self)
        builder.case["quote_common_terms"].update(
            cpu_cores_per_gpu=32, ram_gib_per_gpu=256, network_gbps=50)
        self.assertEqual(builder.run()["curve"]["nodes"], run_demo()["curve"]["nodes"])

    def test_malformed_supporting_resource_names_the_input(self):
        builder = CaseBuilder(self)
        builder.case["quote_common_terms"]["ram_gib_per_gpu"] = -1
        with self.assertRaisesRegex(InputError, r"quote_common_terms.ram_gib_per_gpu"):
            builder.run()

    def test_missing_required_memory_is_not_comparable(self):
        result = CaseBuilder(self).set("q04", memory_gib="").run()
        self.assertFalse(quote_row(result, "q04")["curve_eligible"])
        self.assertEqual(quote_row(result, "q04")["tier"], "different_product")
        self.assertNotEqual(node(result, 1)["status"], "observed")

    def test_unimplemented_contract_optionality_is_rejected(self):
        for part in ("reservation", "reference_product"):
            builder = CaseBuilder(self)
            builder.case[part]["cancellation"] = "anytime"
            with self.subTest(part=part), self.assertRaisesRegex(InputError, "cancellation"):
                builder.run()

    def test_unknown_credit_remedy_is_not_assumed_pro_rata(self):
        for policy in (None, "no-service-credits", "full-refund"):
            builder = CaseBuilder(self)
            builder.case["reservation"]["service_credit_policy"] = policy
            with self.subTest(policy=policy), self.assertRaisesRegex(
                InputError, "service_credit_policy"
            ):
                builder.run()

    def test_lost_quantity_pair_never_promotes_node_to_direct_evidence(self):
        for missing in ("q01", "q02"):
            result = run_demo(exclude_quote_ids=(missing,))
            row = quote_row(result, "q07")
            self.assertFalse(row["curve_eligible"])
            self.assertIn("quantity evidence", row["excluded_reason"])
            self.assertNotIn(0, [a["tenor_months"] for a in result["curve"]["anchors"]])

    def test_quantity_factor_never_extrapolates_to_an_unsupported_node_size(self):
        result = CaseBuilder(self).set("q07", gpu_count="16").run()
        row = quote_row(result, "q07")
        self.assertFalse(row["curve_eligible"])
        self.assertIn("this GPU count", row["excluded_reason"])
        self.assertNotIn(0, [a["tenor_months"] for a in result["curve"]["anchors"]])

    def test_mixed_calibration_sizes_are_not_pooled(self):
        result = CaseBuilder(self).copy_quote(
            "q02", "q02-sixteen", gpu_count="16", price="64").run()
        self.assertIsNone(result["quantity_adjustment"]["factor"])
        self.assertIn("mixes", result["quantity_adjustment"]["note"])
        self.assertFalse(quote_row(result, "q07")["curve_eligible"])

    def test_reference_quantity_factor_is_not_borrowed_by_other_hardware(self):
        result = CaseBuilder(self).set("q08", gpu_count="8", unit="node_hour").run()
        row = quote_row(result, "q08")
        self.assertEqual(row["single_gpu_equivalent_usd_per_gpu_hour"], row["usd_per_gpu_hour"])
        self.assertIn("not adjusted", row["steps"][-1]["note"])

    def test_advance_on_demand_asks_do_not_identify_an_arrears_quantity_ratio(self):
        result = CaseBuilder(self).set("q02", payment="advance").run()
        self.assertIsNone(result["quantity_adjustment"]["factor"])
        self.assertFalse(quote_row(result, "q07")["curve_eligible"])


class RecoveryValuationTests(unittest.TestCase):
    def model_and_case(self):
        builder = CaseBuilder(self)
        builder.case["economics"]["provider_default_probability_over_deal"] = 0.8
        builder.case["economics"]["default_recovery_fraction"] = 0.5
        loaded = load_inputs(builder.write())
        case = loaded.case
        items = [normalize_quote(q, classify(q, case), case) for q in loaded.quotes]
        factor, _ = estimate_quantity_factor(items, case)
        apply_quantity_adjustment(items, factor, case)
        return build_curve_model(items, case, load_config(None)), case

    def test_nonzero_recovery_fair_price_zeroes_mark(self):
        from dataclasses import replace
        from decimal import Decimal

        model, case = self.model_and_case()
        value = value_reservation(model, case)
        repriced = replace(case, reservation=replace(
            case.reservation, fixed_usd_per_gpu_hour=Decimal(str(value.fair_fixed_price))))
        self.assertAlmostEqual(value_reservation(model, repriced).mark_adjusted, 0.0, places=7)

    def test_scenarios_and_interval_apply_their_own_claim_sign(self):
        model, case = self.model_and_case()
        value = value_reservation(model, case)
        fixed = float(case.reservation.fixed_usd_per_gpu_hour)
        recovery = float(case.economics.default_recovery_fraction)
        for scenario, actual in value.scenario_marks.items():
            expected = 0.0
            for row in value.rows:
                level = model.node(row.tenor).scenarios[scenario]
                spread = level - fixed
                exposure = row.hours * row.discount_factor * row.availability
                expected += spread * exposure * (row.survival +
                    (1 - row.survival) * recovery * (spread > 0))
            self.assertAlmostEqual(actual, expected, places=8)
        for bound in ("low", "high"):
            expected = 0.0
            for row in value.rows:
                spread = getattr(row, "fv_" + bound) - fixed
                expected += spread * row.hours * row.discount_factor * row.availability * (
                    row.survival + (1 - row.survival) * recovery * (spread > 0))
            self.assertAlmostEqual(getattr(value, "mark_" + bound), expected, places=8)

    def test_scenario_fair_prices_do_not_depend_on_offered_contract_price(self):
        from dataclasses import replace
        from decimal import Decimal

        model, case = self.model_and_case()
        base = value_reservation(model, case)
        altered = replace(case, reservation=replace(case.reservation,
                                                   fixed_usd_per_gpu_hour=Decimal("3")))
        other = value_reservation(model, altered)
        self.assertEqual(base.scenario_fair_prices, other.scenario_fair_prices)
        self.assertAlmostEqual(base.fair_fixed_price, other.fair_fixed_price, places=9)

