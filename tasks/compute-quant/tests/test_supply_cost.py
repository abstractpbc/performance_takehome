"""Supply-cost view: component arithmetic and declared monotonic behaviour."""

import unittest

from research.loader import load_inputs
from research.supply_cost import breakeven, sensitivity_grid

from .support import DEMO_CASE


class SupplyCostTests(unittest.TestCase):
    def setUp(self):
        self.econ = load_inputs(DEMO_CASE).case.economics

    def test_components_sum(self):
        cost = breakeven(self.econ)
        self.assertAlmostEqual(
            cost.breakeven_usd_per_gpu_hour,
            cost.capital_per_revenue_hour_usd
            + cost.energy_per_hour_usd
            + cost.non_energy_opex_per_hour_usd,
        )
        self.assertAlmostEqual(cost.energy_per_hour_usd, (1.4 + 0.2) * 1.25 * 0.08)
        self.assertAlmostEqual(cost.short_run_marginal_cost_usd, 0.41)
        self.assertAlmostEqual(
            cost.capital_per_revenue_hour_usd, cost.capital_per_calendar_hour_usd / 0.65
        )

    def test_capital_recovery_annuity(self):
        cost = breakeven(self.econ)
        pv_residual = 4000 / 1.12**3
        monthly = 1.12 ** (1 / 12) - 1
        annuity = (1 - (1 + monthly) ** -36) / monthly
        self.assertAlmostEqual(
            cost.capital_per_month_usd, (40000 - pv_residual) / annuity, places=6
        )

    def test_zero_financing_is_straight_line(self):
        cost = breakeven(self.econ, financing=0.0)
        self.assertAlmostEqual(cost.capital_per_month_usd, (40000 - 4000) / 36)

    def test_monotonic_in_utilization_and_financing(self):
        utils = [0.4, 0.5, 0.65, 0.8, 1.0]
        costs = [breakeven(self.econ, u).breakeven_usd_per_gpu_hour for u in utils]
        self.assertEqual(costs, sorted(costs, reverse=True))
        fins = [0.06, 0.09, 0.12, 0.15]
        costs = [breakeven(self.econ, financing=f).breakeven_usd_per_gpu_hour for f in fins]
        self.assertEqual(costs, sorted(costs))

    def test_grid_shape(self):
        grid = sensitivity_grid(self.econ, [0.5, 1.0], [0.1, 0.2])
        self.assertEqual(len(grid), 4)
        self.assertEqual(grid[0]["utilization"], 0.5)

    def test_invalid_utilization_rejected(self):
        with self.assertRaises(ValueError):
            breakeven(self.econ, 0.0)
        with self.assertRaises(ValueError):
            breakeven(self.econ, 1.5)
