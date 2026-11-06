"""Stage 4: seeded scheduling, early stop and token timing. No network or GPU."""

import importlib.util
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

SUITE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SUITE))

from collect.tokenize_events import token_times  # noqa: E402

HAS_HTTPX = importlib.util.find_spec("httpx") is not None


def plan_data():
    message = [{"role": "user", "content": "text"}]
    prompt = {"prompt_id": "x", "messages": message, "expected": "1"}
    cell = {"prompt_mix": {"short": 1.0}, "prefix_cache": "miss", "canary_fraction": 0.05,
            "anchor_fraction": 0.05, "arrival": {"process": "poisson", "rate_rps": 5.0},
            "output": {"max_tokens": 8, "min_output_tokens": 1, "ignore_eos": False}}
    data = {"seed": 7, "cells": {"a": cell, "b": cell},
            "canary": {"prompt_set": "canary", "max_tokens": 4, "min_output_tokens": 1},
            "anchor": {"prompt_set": "anchor", "max_tokens": 4, "min_output_tokens": 1}}
    prompts = SimpleNamespace(sets={"short": [prompt], "canary": [prompt], "anchor": [prompt]})
    return data, prompts


class TokenTimeTests(unittest.TestCase):
    def test_tokens_in_one_event_share_its_time(self):
        self.assertEqual(token_times([(1.0, "abcd"), (2.0, "ef")], [2, 4, 6]), [1.0, 1.0, 2.0])

    def test_token_split_across_events_gets_completing_time(self):
        self.assertEqual(token_times([(1.0, "ab"), (2.0, "cd")], [3, 4]), [2.0, 2.0])


@unittest.skipUnless(HAS_HTTPX, "capture needs httpx")
class ScheduleTests(unittest.TestCase):
    def test_windows_are_reproducible_and_differ_by_repetition(self):
        from collect.capture import build_window

        data, prompts = plan_data()
        first = build_window(data, prompts, "a", 0, 60)
        again = build_window(data, prompts, "a", 0, 60)
        other = build_window(data, prompts, "a", 1, 60)
        self.assertEqual(first, again)
        self.assertNotEqual([s.offset_s for s in first], [s.offset_s for s in other])

    def test_rate_and_fractions_are_roughly_honored(self):
        from collect.capture import build_window

        data, prompts = plan_data()
        specs = build_window(data, prompts, "a", 0, 600)
        self.assertAlmostEqual(len(specs) / 600, 5.0, delta=0.5)
        self.assertAlmostEqual(sum(s.is_canary for s in specs) / len(specs), 0.05, delta=0.02)
        self.assertAlmostEqual(sum(s.is_anchor for s in specs) / len(specs), 0.05, delta=0.02)

    def test_windows_lengthen_until_quantiles_are_powered(self):
        from collect.capture import build_window, window_minimum

        data, prompts = plan_data()
        data["gates"] = {"min_valid_per_quantile": 5}
        # The per-repetition p90 needs 50 counted requests, plus 5% spare.
        self.assertEqual(window_minimum(data), 53)
        short = build_window(data, prompts, "a", 0, 2)
        long = build_window(data, prompts, "a", 0, 2, 53)
        self.assertEqual(long[:len(short)], short)
        self.assertEqual(sum(not (s.is_canary or s.is_anchor) for s in long), 53)

    def test_only_plain_miss_prompts_get_a_unique_prefix(self):
        from collect.capture import build_window

        data, prompts = plan_data()
        specs = build_window(data, prompts, "a", 0, 60)
        for spec in specs:
            changed = spec.messages[0]["content"] != "text"
            self.assertEqual(changed, not (spec.is_canary or spec.is_anchor))

    def test_gamma_arrivals_keep_the_rate_but_clump(self):
        from collect.capture import build_window

        def gaps(arrival):
            data, prompts = plan_data()
            data["cells"]["a"]["arrival"] = {**arrival, "rate_rps": 5.0}
            offsets = [s.offset_s for s in build_window(data, prompts, "a", 0, 2000)]
            return [b - a for a, b in zip(offsets, offsets[1:])]

        def spread(values):
            mean = sum(values) / len(values)
            return (sum((v - mean) ** 2 for v in values) / len(values)) ** 0.5 / mean

        poisson = gaps({"process": "poisson"})
        bursty = gaps({"process": "gamma", "burstiness": 0.25})
        self.assertAlmostEqual(len(bursty) / 2000, 5.0, delta=0.5)
        # The gaps' coefficient of variation is 1/sqrt(shape): 1 for Poisson, 2 at shape 0.25.
        self.assertAlmostEqual(spread(poisson), 1.0, delta=0.1)
        self.assertAlmostEqual(spread(bursty), 2.0, delta=0.3)

    def test_missing_rate_refuses_to_guess(self):
        from collect.capture import build_window

        data, prompts = plan_data()
        data["cells"]["a"]["arrival"]["rate_rps"] = None
        with self.assertRaises(ValueError):
            build_window(data, prompts, "a", 0, 60)

    def test_layout_covers_every_window_evenly(self):
        from collect.capture import layout

        data, _ = plan_data()
        dealt = layout(data, {"cells": ["a", "b"], "repetitions": 8, "launches": 5})
        flat = [window for launch in dealt for window in launch]
        self.assertEqual(len(flat), len(set(flat)))
        self.assertEqual(len(flat), 16)
        self.assertLessEqual(max(map(len, dealt)) - min(map(len, dealt)), 1)

    def test_sweep_capacity_ignores_batch_waves(self):
        from collect.sweep import capacity

        # 16 fixed-length requests finish together every 10 s: the true rate is 1.6 rps.
        waves = [(t, t + 10.0, True) for t in (0.0, 10.0, 20.0) for _ in range(16)]
        # This window sees only one wave finish, so counting completions would say 16/14.
        self.assertAlmostEqual(capacity(waves, (5.0, 19.0))["capacity_rps"], 1.6)
        failed = [(0.0, 10.0, False)] * 16
        self.assertEqual(capacity(failed, (0.0, 10.0)), {"failed_share": 16.0, "capacity_rps": 0.0})

    def test_early_stop_triggers_only_on_decided_failures(self):
        from collect.capture import EarlyStop

        config = {"max_run_failing_requests": 3}

        def result(state, reasons=(), failing=False):
            return SimpleNamespace(request={"cell": "a"}, state=state, reasons=reasons,
                                   run_failing=failing)

        stop = EarlyStop(config, 0.01, {"a": 300})
        for _ in range(200):
            self.assertIsNone(stop.observe(result("valid")))
        stop = EarlyStop(config, 0.01, {"a": 300})
        self.assertIsNone(stop.observe(result("invalid", ("wrong_output",), True)))
        self.assertIsNone(stop.observe(result("invalid", ("wrong_output",), True)))
        self.assertEqual(stop.observe(result("invalid", ("wrong_output",), True)),
                         "early_stop:wrong_output")
        # 3 early failures in a 300-request cell can still end under 1%, so no stop.
        stop = EarlyStop(config, 0.01, {"a": 300})
        outcomes = [stop.observe(result("invalid", ("server_error",))) for _ in range(4)]
        self.assertEqual(outcomes[:3], [None, None, None])
        self.assertEqual(outcomes[3], "early_stop:failure_rate")
        # Excluded requests are never charged.
        stop = EarlyStop(config, 0.01, {"a": 300})
        for _ in range(10):
            self.assertIsNone(stop.observe(result("inconclusive", ("client_lag",))))

if __name__ == "__main__":
    unittest.main()
