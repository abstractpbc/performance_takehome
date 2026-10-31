"""Noise-aware keep/revert decision rule."""

from __future__ import annotations

import random
import unittest

from abstract_harness.decision import compare_metric, decide

from . import _paths  # noqa: F401


def _noisy(center: float, n: int, spread: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    return [center * (1 + rng.uniform(-spread, spread)) for _ in range(n)]


def _samples(ttft: float, ftgr: float, *, spread: float = 0.05, seed: int = 1, n: int = 6) -> dict:
    ttft_groups = [("a", 1.0), ("b", 3.0)]
    ftgr_groups = [("a", 1.0), ("b", 0.5)]
    return {
        "ttft_s": {
            p: _noisy(ttft * k, n, spread, seed + i) for i, (p, k) in enumerate(ttft_groups)
        },
        "ftgr_tok_s": {
            p: _noisy(ftgr * k, n, spread, seed + 10 + i) for i, (p, k) in enumerate(ftgr_groups)
        },
        "startup_s": {"startup": [1.0]},
    }


class DecisionRuleTest(unittest.TestCase):
    def test_clear_improvement_is_kept(self) -> None:
        d = decide(_samples(0.020, 100), _samples(0.014, 130, seed=7), gates_passed=True)
        self.assertEqual(d.outcome, "improved")
        self.assertTrue(d.kept)

    def test_clear_regression_is_reverted(self) -> None:
        d = decide(_samples(0.020, 100), _samples(0.030, 60, seed=7), gates_passed=True)
        self.assertEqual(d.outcome, "regressed")
        self.assertFalse(d.kept)

    def test_noise_only_is_no_change(self) -> None:
        base = _samples(0.020, 100, spread=0.3, n=30)
        cand = _samples(0.0205, 101, spread=0.3, seed=9, n=30)
        d = decide(base, cand, gates_passed=True)
        self.assertEqual(d.outcome, "no_change")
        self.assertFalse(d.kept)

    def test_small_effect_below_min_effect_is_no_change(self) -> None:
        d = decide(
            _samples(0.020, 100, spread=0.001),
            _samples(0.0198, 101, spread=0.001, seed=3),
            gates_passed=True,
            min_effect=0.03,
        )
        self.assertEqual(d.outcome, "no_change")

    def test_correctness_failure_never_kept_even_if_faster(self) -> None:
        d = decide(
            _samples(0.020, 100),
            _samples(0.005, 400, seed=7),
            gates_passed=False,
            gate_summary="golden mismatch",
        )
        self.assertEqual(d.outcome, "gate_failed")
        self.assertFalse(d.kept)
        self.assertIn("golden", d.reason)

    def test_insufficient_samples_is_no_change(self) -> None:
        d = decide(
            {"ttft_s": {"a": [0.02]}, "ftgr_tok_s": {"a": [100.0]}},
            {"ttft_s": {"a": [0.01]}, "ftgr_tok_s": {"a": [300.0]}},
            gates_passed=True,
        )
        self.assertEqual(d.outcome, "no_change")
        self.assertFalse(d.kept)

    def test_mixed_primary_regression_blocks_keep(self) -> None:
        d = decide(_samples(0.020, 100), _samples(0.010, 60, seed=7), gates_passed=True)
        self.assertEqual(d.outcome, "regressed")

    def test_startup_regression_blocks_keep(self) -> None:
        base = _samples(0.020, 100)
        cand = _samples(0.014, 130, seed=7)
        base["startup_s"] = {"startup": [1.0, 1.0, 1.0]}
        cand["startup_s"] = {"startup": [3.0, 3.0, 3.0]}
        d = decide(base, cand, gates_passed=True)
        self.assertFalse(d.kept)

    def test_bootstrap_is_deterministic(self) -> None:
        a = compare_metric(
            "ttft_s",
            _samples(0.02, 1)["ttft_s"],
            _samples(0.015, 1, seed=5)["ttft_s"],
            min_effect=0.03,
            min_samples=3,
        )
        b = compare_metric(
            "ttft_s",
            _samples(0.02, 1)["ttft_s"],
            _samples(0.015, 1, seed=5)["ttft_s"],
            min_effect=0.03,
            min_samples=3,
        )
        self.assertEqual((a.speedup, a.ci_low, a.ci_high), (b.speedup, b.ci_low, b.ci_high))

    def test_flat_lists_are_accepted(self) -> None:
        c = compare_metric("ftgr_tok_s", [100.0] * 6, [150.0] * 6, min_effect=0.03, min_samples=3)
        self.assertEqual(c.verdict, "improved")
        self.assertAlmostEqual(c.speedup or 0, 1.5)

    def test_missing_primary_metric_blocks_other_metric_win(self) -> None:
        base, cand = _samples(0.020, 100), _samples(0.010, 100)
        cand["ftgr_tok_s"] = {}
        d = decide(base, cand, gates_passed=True)
        self.assertEqual(d.outcome, "no_change")
        self.assertFalse(d.kept)
        self.assertIn("incomplete", d.reason)

    def test_unmatched_prompt_cannot_be_silently_dropped(self) -> None:
        # The candidate wins the easy prompt but has no samples for the hard one.
        base = {"easy": [1.0] * 5, "hard": [10.0] * 5}
        cand = {"easy": [0.5] * 5}
        for left, right in ((base, cand), (cand, base)):
            with self.subTest(left=left):
                c = compare_metric("ttft_s", left, right, min_effect=0.03, min_samples=3)
                self.assertEqual(c.verdict, "insufficient")
                self.assertIsNone(c.speedup)

    def test_pooled_count_cannot_hide_under_sampled_prompt(self) -> None:
        c = compare_metric(
            "ttft_s", {"a": [1.0] * 20, "b": [2.0]}, {"a": [0.5] * 20, "b": [1.0]},
            min_effect=0.03, min_samples=3,
        )
        self.assertEqual(c.verdict, "insufficient")

    def test_dropped_repetition_cannot_improve_point_estimate(self) -> None:
        c = compare_metric(
            "ttft_s", {"a": [1.0] * 5}, {"a": [0.5] * 4},
            min_effect=0.03, min_samples=3,
        )
        self.assertEqual(c.verdict, "insufficient")

    def test_corrupt_samples_are_not_filtered_into_a_win(self) -> None:
        for value in (float("nan"), float("inf"), -1.0, 0.0, True, "unknown"):
            with self.subTest(value=value):
                c = compare_metric(
                    "ttft_s", [1.0] * 6, [0.5] * 5 + [value],
                    min_effect=0.03, min_samples=3,
                )
                self.assertEqual(c.verdict, "insufficient")


if __name__ == "__main__":
    unittest.main()
