"""Stage 2: every synthetic family lands in its STATES.md state. CPU-only."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.events import load_plan, load_run  # noqa: E402
from analysis.gates import (  # noqa: E402
    INCONCLUSIVE,
    INVALID,
    VALID,
    answers,
    assess_run,
    check_request,
    parse_stream,
)
from matrix import EXPECTED, REQUEST_REASONS, actual  # noqa: E402
from synth import FAMILIES, build_model, generate, write  # noqa: E402


def assess(family, seed=1):
    with tempfile.TemporaryDirectory() as directory:
        run_path, workload = generate(family, seed, Path(directory))
        return assess_run(load_run(run_path), load_plan(workload))


class FamilyTests(unittest.TestCase):
    def test_every_family_has_an_expectation(self):
        self.assertEqual(set(FAMILIES), set(EXPECTED))

    def test_families_land_in_expected_state(self):
        for family, (state, reasons) in EXPECTED.items():
            with self.subTest(family=family), tempfile.TemporaryDirectory() as directory:
                got_state, got_reasons = actual(family, 1, Path(directory))
                self.assertEqual(got_state, state, got_reasons)
                self.assertLessEqual(set(reasons), set(got_reasons))
                if state == VALID:
                    self.assertEqual(got_reasons, ())

    def test_clean_controls_stay_valid_across_seeds(self):
        for family in ("clean", "noisy", "burst"):
            for seed in range(5):
                with self.subTest(family=family, seed=seed):
                    self.assertEqual(assess(family, seed).state, VALID)

    def test_family_name_never_reaches_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            run_path, _ = generate("placeholder_first_token", 1, Path(directory))
            for path in run_path.iterdir():
                self.assertNotIn("placeholder", path.read_text())

    def test_same_seed_is_reproducible(self):
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as two:
            first, _ = generate("noisy", 7, Path(one))
            second, _ = generate("noisy", 7, Path(two))
            for name in ("requests.jsonl", "sse.jsonl", "tokens.jsonl"):
                self.assertEqual((first / name).read_text(), (second / name).read_text())


class RuleTests(unittest.TestCase):
    def test_canary_answer_is_the_last_number(self):
        self.assertTrue(answers("\n\n17 + 25 = 42\n\nThe answer is **42**.", "42"))
        self.assertTrue(answers("356 + 844 = 1,200", "1200"))
        self.assertFalse(answers("356 + 844 = 1100", "1200"))
        self.assertFalse(answers("42 is wrong, it is 41", "42"))
        self.assertFalse(answers("no number", "42"))

    def test_hidden_reasoning_tags_are_counted(self):
        self.assertEqual(parse_stream([]).hidden_tokens, 0)
        self.assertEqual(assess("reasoning").state, VALID)

    def test_failures_within_limit_stay_visible(self):
        result = assess("errors_few")
        self.assertEqual(result.state, VALID)
        self.assertEqual(result.request_reasons["server_error"], 1)
        self.assertEqual(sum(item.failed for item in result.results), 1)

    def test_heartbeats_do_not_move_ttft(self):
        clean = assess("clean").results[5].ttft_s
        padded = assess("heartbeats").results[5].ttft_s
        self.assertGreater(clean, 0.2)
        self.assertAlmostEqual(clean, padded, places=2)

    def test_readiness_does_not_stop_startup_clock(self):
        result = assess("misleading_readiness")
        self.assertEqual(len(result.startup.samples_s), 3)
        self.assertTrue(all(sample > 90 for sample in result.startup.samples_s))

    def test_clean_startup_uses_first_valid_probe(self):
        samples = assess("clean").startup.samples_s
        self.assertTrue(all(31 < sample < 32 for sample in samples))

    def test_small_anchor_noise_is_tolerated_and_reported(self):
        stability = assess("anchor_noise").stability
        self.assertEqual(stability.state, VALID)
        self.assertEqual(stability.diverged, 3)

    def test_unmeasured_divergence_is_not_a_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            model = build_model(1)
            model.plan["gates"]["max_anchor_divergence"] = None
            run_path, workload = write(model, Path(directory))
            result = assess_run(load_run(run_path), load_plan(workload))
        self.assertEqual(result.state, INCONCLUSIVE)
        self.assertIn("divergence_unmeasured", result.reasons)

    def test_rate_failures_have_the_expected_request_reason(self):
        for family, reason in REQUEST_REASONS.items():
            with self.subTest(family=family):
                self.assertGreater(assess(family).request_reasons.get(reason, 0), 0)

    def test_every_reason_code_is_documented(self):
        documented = (Path(__file__).resolve().parents[1] / "STATES.md").read_text()
        reasons = {reason for _, codes in EXPECTED.values() for reason in codes}
        reasons |= set(REQUEST_REASONS.values())
        for reason in reasons:
            with self.subTest(reason=reason):
                self.assertIn(f"`{reason}`", documented)

    def test_invalid_beats_truncation(self):
        self.assertEqual(assess("fail_fast_stop").state, INVALID)

    def test_empty_run_is_never_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            model = build_model(1)
            model.specs = [s for s in model.specs if s.record["phase"] == "probe"]
            model.manifest["planned_requests"] = {}
            run_path, workload = write(model, Path(directory))
            result = assess_run(load_run(run_path), load_plan(workload))
        self.assertNotEqual(result.state, VALID)


class StreamTests(unittest.TestCase):
    @staticmethod
    def events(*payloads):
        return [
            {"seq": i, "t": float(i), "data": data if isinstance(data, str) else json.dumps(data)}
            for i, data in enumerate(payloads)
        ]

    def test_role_only_event_is_not_first_text(self):
        stream = parse_stream(self.events(
            {"choices": [{"delta": {"role": "assistant"}}]},
            {"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]},
            "[DONE]",
        ))
        self.assertEqual(stream.first_text_t, 1.0)
        self.assertEqual(stream.finish_reason, "stop")
        self.assertTrue(stream.done)

    def test_error_event_and_malformed_json_are_distinct(self):
        self.assertTrue(parse_stream(self.events({"error": "boom"})).server_error)
        self.assertTrue(parse_stream(self.events("{bad")).protocol_error)

    def test_reset_request_keeps_partial_ttft(self):
        model = build_model(1)
        spec = next(s for s in model.measured if s.is_plain)
        request = {**spec.record, "client_outcome": "reset"}
        events = [{"seq": 0, "t": spec.tokens[0]["t"],
                   "data": json.dumps({"choices": [{"delta": {"content": "hi"}}]})}]
        result = check_request(request, events, [], load_plan_for(model))
        self.assertEqual(result.reasons, ("server_error",))
        self.assertIsNotNone(result.ttft_s)

    def test_ignore_eos_length_floor(self):
        model = build_model(1)
        spec = next(s for s in model.measured if s.is_plain)
        plan = load_plan_for(model)
        sent = spec.record["t_sent"]

        def reasons(count, finish):
            events = [{"seq": 0, "t": sent + 1,
                       "data": json.dumps({"choices": [{"delta": {"content": "x" * count},
                                                        "finish_reason": finish}],
                                           "usage": {"completion_tokens": count}})},
                      {"seq": 1, "t": sent + 1, "data": "[DONE]"}]
            tokens = [{"t": sent + 1, "channel": "content"}] * count
            request = {**spec.record, "max_tokens": 1024, "min_output_tokens": 1024,
                       "ignore_eos": True}
            return check_request(request, events, tokens, plan).reasons

        # Retokenizing can fall 2% short of a full generation without truncating it.
        self.assertNotIn("truncated_output", reasons(1010, "length"))
        # A server that ignores ignore_eos and stops early is truncated.
        self.assertIn("truncated_output", reasons(300, "stop"))

    def test_listening_after_done_is_not_a_timeout(self):
        model = build_model(1)
        spec = next(s for s in model.measured if s.is_plain)
        plan = load_plan_for(model)
        limit = plan.data["deadlines"]["request_timeout_s"]
        sent = spec.record["t_sent"]
        events = [{"seq": 0, "t": sent + limit - 0.5,
                   "data": json.dumps({"choices": [{"delta": {"content": "hi"},
                                                    "finish_reason": "stop"}]})},
                  {"seq": 1, "t": sent + limit - 0.5, "data": "[DONE]"}]
        request = {**spec.record, "t_end": sent + limit + 0.5}
        self.assertNotIn("timed_out", check_request(request, events, [], plan).reasons)
        request = {**spec.record, "t_end": sent + limit + 0.5}
        self.assertIn("timed_out", check_request(request, events[:1], [], plan).reasons)


def load_plan_for(model):
    with tempfile.TemporaryDirectory() as directory:
        _, workload = write(model, Path(directory))
        return load_plan(workload)


if __name__ == "__main__":
    unittest.main()
