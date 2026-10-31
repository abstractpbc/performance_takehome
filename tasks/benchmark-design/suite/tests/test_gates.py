"""Per-request gates: rejection (not scoring) of invalid, incomplete and misleading evidence."""

import unittest

from bench.gates import judge, token_times
from bench.tokens import count_tokens

from benchmarks.metrics import fluidity, token_intervals
from tests.helpers import chunk, make_record, plan


class GateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = plan()

    def judge(self, **kwargs):
        return judge(make_record(self.plan, **kwargs), self.plan, [])

    def test_clean_record_completes(self) -> None:
        v = self.judge()
        self.assertEqual(v.category, "completed", v.reasons)
        self.assertAlmostEqual(
            v.ttft_s, 0.06, places=3
        )  # first non-empty content delta, not the role chunk
        self.assertTrue(v.done_received)

    def test_delayed_prefill_shows_in_ttft(self) -> None:
        self.assertGreater(self.judge(ttft=2.0).ttft_s, self.judge(ttft=0.05).ttft_s + 1.9)

    def test_missing_done_and_finish_reason_are_unfinished(self) -> None:
        self.assertEqual(self.judge(done=False).category, "unfinished")
        self.assertIn("missing_done", self.judge(done=False).reasons)
        v = self.judge(finish=None)
        self.assertEqual(v.category, "unfinished")
        self.assertIn("missing_finish_reason", v.reasons)

    def test_wrong_garbled_truncated_text_fail(self) -> None:
        wrong = self.judge(
            text="C0DE-WRONG-0000\nthis explanation is long enough to pass the length check twice"
        )
        self.assertEqual(wrong.category, "failed")
        self.assertTrue(any(r.startswith("invalid_output:nonce_missing") for r in wrong.reasons))
        garbled = make_record(self.plan)
        nonce = garbled["prompt"]["expected"]["nonce"]
        garbled = self.judge(
            text=f"{nonce}\n\ufffd\ufffd garbled text that is otherwise long enough to pass length"
        )
        self.assertEqual(garbled.category, "failed")
        short = self.judge(text=nonce)
        self.assertEqual(short.category, "failed")
        self.assertTrue(any("output_too_short" in r for r in short.reasons))

    def test_http_error_and_drop(self) -> None:
        self.assertEqual(
            self.judge(
                http_status=500, error={"kind": "http_error", "message": "HTTP 500"}
            ).category,
            "failed",
        )
        v = self.judge(done=False, finish=None, error={"kind": "drop", "message": "peer closed"})
        self.assertEqual(v.category, "unfinished")
        self.assertIn("drop", v.reasons)

    def test_misleading_usage_rejected_but_sane_usage_accepted(self) -> None:
        rec = make_record(self.plan)
        independent = rec["client_tokens"]["content"]
        self.assertEqual(self.judge(usage_tokens=independent).category, "completed")
        v = self.judge(usage_tokens=independent * 3)
        self.assertEqual(v.category, "failed")
        self.assertTrue(any(r.startswith("usage_discrepancy") for r in v.reasons))

    def test_invalid_timing_is_evidence_invalid(self) -> None:
        rec = make_record(self.plan)
        rec["events"][3]["t"], rec["events"][4]["t"] = rec["events"][4]["t"], rec["events"][3]["t"]
        v = judge(rec, self.plan, [])
        self.assertEqual(v.category, "evidence_invalid")
        self.assertTrue(any(r.startswith("invalid_timing") for r in v.reasons))
        rec = make_record(self.plan)
        rec["timestamps_mono_s"]["first_byte"] = None
        self.assertEqual(judge(rec, self.plan, []).category, "evidence_invalid")
        rec = make_record(self.plan)
        rec["timestamps_mono_s"]["send"] = None
        self.assertEqual(judge(rec, self.plan, []).category, "evidence_invalid")

    def test_client_send_lag_and_record_mismatch_are_evidence_invalid(self) -> None:
        v = self.judge(planned_send=99.0, send=100.0)
        self.assertEqual(v.category, "evidence_invalid")
        self.assertTrue(any(r.startswith("client_send_lag") for r in v.reasons))
        rec = make_record(self.plan)
        rec["text"]["content"] = "something the payloads never said"
        self.assertEqual(judge(rec, self.plan, []).category, "evidence_invalid")

    def test_schema_error_is_evidence_invalid(self) -> None:
        self.assertEqual(
            judge(make_record(self.plan), self.plan, ["$.x: bad"]).category, "evidence_invalid"
        )

    def test_empty_output_fails(self) -> None:
        rec = make_record(self.plan, deltas=[""])
        v = judge(rec, self.plan, [])
        self.assertEqual(v.category, "failed")
        self.assertIn("empty_output", v.reasons)


class TokenEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = plan()

    def test_chunks_are_not_tokens(self) -> None:
        nonce = make_record(self.plan)["prompt"]["expected"]["nonce"]
        burst = f"{nonce}\n" + "tail latency matters more than the mean for interactive users " * 3
        v = judge(make_record(self.plan, deltas=[burst]), self.plan, [])
        self.assertEqual(v.content_events, 1)
        self.assertEqual(v.content_tokens, count_tokens(burst))
        self.assertGreater(v.content_tokens, 10)
        self.assertIn("multi_token_deltas", v.flags)
        self.assertEqual(len(v.token_times_s), v.content_tokens)

    def test_split_tokens_counted_once(self) -> None:
        whole = [("latency", 1.0), (" matters", 1.1)]
        split = [("lat", 1.0), ("ency", 1.05), (" mat", 1.1), ("ters", 1.15)]
        times_whole, n_whole = token_times(whole, 0.0)
        times_split, n_split = token_times(split, 0.0)
        self.assertEqual(n_whole, n_split)
        self.assertEqual(times_split, [1.05, 1.15])
        self.assertEqual(times_whole, [1.0, 1.1])

    def test_unicode_graphemes_and_cjk(self) -> None:
        deltas = [("caf", 1.0), ("e", 1.1), ("\u0301", 1.2), (" \u65e5", 1.3), ("\u672c", 1.4)]
        times, n = token_times(deltas, 0.0)
        self.assertEqual(n, len(times))
        self.assertEqual(n, count_tokens("cafe\u0301 \u65e5\u672c"))
        self.assertEqual(times, sorted(times))

    def test_stall_produces_multiple_misses_and_burst_zero_intervals(self) -> None:
        nonce = make_record(self.plan)["prompt"]["expected"]["nonce"]
        words = [f"{nonce}\n"] + [f"token{i} " for i in range(20)]
        gaps = [0.01] * 10 + [1.5] + [0.01] * 10
        v = judge(make_record(self.plan, deltas=words, gaps=gaps), self.plan, [])
        self.assertEqual(v.category, "completed", v.reasons)
        fi = fluidity(token_intervals(v.token_times_s), 0.3, 0.025)
        self.assertGreater(fi.missed_slots, 10)
        bursty = judge(
            make_record(
                self.plan, deltas=["".join(words[:11]), "".join(words[11:])], gaps=[0.2, 0.2]
            ),
            self.plan,
            [],
        )
        intervals = token_intervals(bursty.token_times_s)
        self.assertEqual(intervals.count(0.0), bursty.content_tokens - 2)

    def test_reasoning_channel_separate_and_ttft_is_first_content(self) -> None:
        v = judge(make_record(self.plan, reasoning=["think ", "more "], gap=0.1), self.plan, [])
        self.assertEqual(v.category, "completed", v.reasons)
        self.assertEqual(v.reasoning_tokens, count_tokens("think more "))
        self.assertAlmostEqual(v.ttft_s, 0.05 + 0.3, places=3)

    def test_protocol_violation_detected(self) -> None:
        rec = make_record(self.plan)
        rec["events"].insert(2, {"t": rec["events"][2]["t"], "raw": "not json"})
        v = judge(rec, self.plan, [])
        self.assertEqual(v.category, "failed")
        self.assertTrue(any(r.startswith("protocol_error") for r in v.reasons))
        rec = make_record(self.plan)
        rec["events"].append({"t": rec["events"][-1]["t"], "raw": chunk("after done")})
        self.assertTrue(any("after [DONE]" in r for r in judge(rec, self.plan, []).reasons))


if __name__ == "__main__":
    unittest.main()
