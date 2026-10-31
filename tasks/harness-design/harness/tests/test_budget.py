"""Budget guards: API USD, GPU-hours (allocation x elapsed), lease deadline, reserved window."""

from __future__ import annotations

import unittest

from abstract_harness.budget import (
    BudgetConfig,
    check_before_attempt,
    gpu_hours_used,
    monotone_now,
)

from . import _paths  # noqa: F401

T0 = 1_000_000.0


def _cfg(**over: float) -> BudgetConfig:
    base = dict(
        api_usd_limit=10.0,
        gpu_hours_limit=2.0,
        gpus_allocated=1,
        lease_start_ts=T0,
        lease_deadline_ts=T0 + 3600,
        final_reserve_s=600,
    )
    base.update(over)
    return BudgetConfig(**base)  # type: ignore[arg-type]


class BudgetGuardTest(unittest.TestCase):
    def test_gpu_hours_are_allocation_times_elapsed(self) -> None:
        self.assertAlmostEqual(gpu_hours_used(_cfg(gpus_allocated=4), T0 + 1800), 2.0)
        self.assertEqual(gpu_hours_used(_cfg(gpus_allocated=0), T0 + 1800), 0.0)

    def test_ok_when_everything_fits(self) -> None:
        v = check_before_attempt(_cfg(), api_usd_cumulative=1.0, now_ts=T0, estimated_attempt_s=600)
        self.assertTrue(v.ok, v.reason)
        self.assertAlmostEqual(v.api_usd_remaining, 9.0)
        self.assertAlmostEqual(v.usable_seconds_remaining, 3000)

    def test_api_spend_limit_blocks(self) -> None:
        v = check_before_attempt(
            _cfg(),
            api_usd_cumulative=9.5,
            now_ts=T0,
            estimated_attempt_s=60,
            estimated_attempt_usd=1.0,
        )
        self.assertFalse(v.ok)
        self.assertIn("API spend", v.reason)

    def test_zero_api_budget_with_zero_cost_attempts_is_ok(self) -> None:
        v = check_before_attempt(
            _cfg(api_usd_limit=0.0), api_usd_cumulative=0.0, now_ts=T0, estimated_attempt_s=60
        )
        self.assertTrue(v.ok, v.reason)

    def test_gpu_hours_limit_blocks(self) -> None:
        v = check_before_attempt(
            _cfg(gpus_allocated=8), api_usd_cumulative=0, now_ts=T0 + 800, estimated_attempt_s=300
        )
        self.assertFalse(v.ok)
        self.assertIn("GPU-hours", v.reason)

    def test_reserved_final_window_blocks_attempt_that_would_overrun(self) -> None:
        v = check_before_attempt(
            _cfg(), api_usd_cumulative=0, now_ts=T0 + 3600 - 600 - 100, estimated_attempt_s=300
        )
        self.assertFalse(v.ok)
        self.assertIn("reserve", v.reason.lower())

    def test_past_deadline_blocks(self) -> None:
        v = check_before_attempt(
            _cfg(), api_usd_cumulative=0, now_ts=T0 + 4000, estimated_attempt_s=1
        )
        self.assertFalse(v.ok)
        self.assertLessEqual(v.usable_seconds_remaining, 0)


if __name__ == "__main__":
    unittest.main()


class MonotoneClockTest(unittest.TestCase):
    def test_backwards_wall_clock_is_clamped(self) -> None:
        self.assertEqual(monotone_now(100.0, 90.0), (100.0, False))
        self.assertEqual(monotone_now(80.0, 90.0), (90.0, True))
        self.assertEqual(monotone_now(90.0, 90.0), (90.0, False))

    def test_gpu_hours_never_negative_when_clock_steps_back(self) -> None:
        self.assertEqual(gpu_hours_used(_cfg(gpus_allocated=1), T0 - 500.0), 0.0)
