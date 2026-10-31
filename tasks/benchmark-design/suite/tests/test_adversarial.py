"""Adversarial evidence, degenerate statistics and determinism (coordinator review items)."""

import copy
import gzip
import json
import tempfile
import unittest
from pathlib import Path

import evaluate as ev_mod
from bench.gates import judge
from bench.records import MAX_RECORD_BYTES, EvidenceError, load_events
from bench.stats import decide, difference_ci, percentile_ci

from tests.helpers import MOCK_PLAN_PATH, clean_cell, make_record, make_run, plan, write_events


def run_eval(the_plan, records, run=None, **kw):
    run = run or make_run(records, **kw)
    with tempfile.TemporaryDirectory() as tmp:
        write_events(Path(tmp), run, records)
        return ev_mod.evaluate(the_plan, Path(tmp), "native")[0]


class AdversarialRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = plan()
        self.rec = make_record(self.plan)

    def judge(self, rec):
        return judge(rec, self.plan, [])

    def test_non_finite_or_negative_timestamps_are_evidence_invalid(self) -> None:
        for mutate in (
            lambda r: r["timestamps_mono_s"].__setitem__("send", float("nan")),
            lambda r: r["timestamps_mono_s"].__setitem__("end", float("inf")),
            lambda r: r["events"][2].__setitem__("t", -1.0),
            lambda r: r["timestamps_mono_s"].__setitem__("send", -5.0),
        ):
            rec = copy.deepcopy(self.rec)
            mutate(rec)
            v = self.judge(rec)
            self.assertEqual(v.category, "evidence_invalid", v.reasons)
            self.assertTrue(any(r.startswith("invalid_timing") for r in v.reasons))

    def test_duplicate_timestamps_allowed_but_not_reordering(self) -> None:
        rec = copy.deepcopy(self.rec)
        rec["events"][3]["t"] = rec["events"][2]["t"]  # two deltas in one network chunk
        self.assertEqual(self.judge(rec).category, "completed")
        rec["events"][3]["t"] = rec["events"][2]["t"] - 1e-6
        self.assertEqual(self.judge(rec).category, "evidence_invalid")

    def test_usage_on_non_final_chunk_and_mixed_ids_are_protocol_errors(self) -> None:
        rec = copy.deepcopy(self.rec)
        mid = json.loads(rec["events"][2]["raw"])
        mid["usage"] = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        rec["events"][2]["raw"] = json.dumps(mid)
        v = self.judge(rec)
        self.assertEqual(v.category, "failed")
        self.assertTrue(any("after the usage chunk" in r for r in v.reasons))
        rec = copy.deepcopy(self.rec)
        other = json.loads(rec["events"][3]["raw"])
        other["id"] = "chatcmpl-someone-else"
        rec["events"][3]["raw"] = json.dumps(other)
        v = self.judge(rec)
        self.assertEqual(v.category, "failed")
        self.assertTrue(any("more than one completion id" in r for r in v.reasons))

    def test_non_sse_content_type_is_rejected(self) -> None:
        rec = copy.deepcopy(self.rec)
        rec["response_content_type"] = "application/json"
        v = self.judge(rec)
        self.assertEqual(v.category, "failed")
        rec["response_content_type"] = "text/event-stream; charset=utf-8"
        self.assertEqual(self.judge(rec).category, "completed")

    def test_server_timestamps_are_never_used(self) -> None:
        rec = copy.deepcopy(self.rec)
        for e in rec["events"]:
            if e["raw"] != "[DONE]":
                payload = json.loads(e["raw"])
                payload["created"] = 1  # absurd server clock
                e["raw"] = json.dumps(payload)
        self.assertEqual(self.judge(rec).ttft_s, self.judge(self.rec).ttft_s)

    def test_split_multibyte_delta_and_split_data_lines_reconstruct(self) -> None:
        # "日本" split across deltas character by character plus a combining sequence.
        nonce = self.rec["prompt"]["expected"]["nonce"]
        pieces = [f"{nonce}\n", "e", "\u0301", "\u65e5", "\u672c", " explanation " * 5]
        rec = make_record(self.plan, deltas=pieces)
        v = self.judge(rec)
        self.assertEqual(v.category, "completed", v.reasons)
        self.assertGreater(v.content_tokens, 5)
        # A payload altered after capture no longer matches the record's own text: the
        # record is inconsistent evidence, not a scorable request.
        rec2 = copy.deepcopy(self.rec)
        raw = rec2["events"][2]["raw"]
        rec2["events"][2]["raw"] = raw[:10] + "\n" + raw[10:]
        self.assertEqual(self.judge(rec2).category, "evidence_invalid")

    def test_schema_version_mismatch_and_unknown_adapter(self) -> None:
        records = clean_cell(self.plan, "chat_short_open", 12)
        records[0]["schema_version"] = "9.9.9"
        self.assertEqual(run_eval(self.plan, records)["status"], "invalid")
        with tempfile.TemporaryDirectory() as tmp:
            write_events(Path(tmp), make_run(records), records)
            with self.assertRaises(EvidenceError):
                load_events(Path(tmp), adapter="organizer-v1")

    def test_truncated_file_is_invalid_not_scored(self) -> None:
        records = clean_cell(self.plan, "chat_short_open", 12)
        with tempfile.TemporaryDirectory() as tmp:
            write_events(Path(tmp), make_run(records), records)
            f = Path(tmp) / "requests.jsonl"
            f.write_bytes(f.read_bytes()[:-50])
            report = ev_mod.evaluate(self.plan, Path(tmp), "native")[0]
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("unreadable" in r for r in report["reasons"]))

    def test_oversized_record_fails_fast(self) -> None:
        records = clean_cell(self.plan, "chat_short_open", 2)
        records[1]["text"]["content"] = "x" * (MAX_RECORD_BYTES + 10)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "run.json").write_text(json.dumps(make_run(records)), encoding="utf-8")
            with gzip.open(Path(tmp) / "requests.jsonl.gz", "wt", encoding="utf-8") as fh:
                for r in records:
                    fh.write(json.dumps(r) + "\n")
            with self.assertRaises(EvidenceError) as ctx:
                load_events(Path(tmp))
            self.assertIn("exceeds", str(ctx.exception))
            self.assertEqual(
                ev_mod.evaluate(self.plan, Path(tmp), "native")[0]["status"], "invalid"
            )

    def test_compare_refuses_different_plan_digest(self) -> None:
        records = clean_cell(self.plan, "chat_short_open", 12)
        run = make_run(records)
        run["plan"]["digest"] = "f" * 64
        with tempfile.TemporaryDirectory() as tmp:
            write_events(Path(tmp), run, records)
            report = ev_mod.evaluate(self.plan, Path(tmp), "native", "a" * 64)[0]
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("digest mismatch" in r for r in report["reasons"]))


class DegenerateStatisticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = plan()

    def test_bootstrap_with_zero_one_and_identical_samples(self) -> None:
        self.assertIsNone(
            percentile_ci([], lambda x: x[0], resamples=50, confidence=0.95, seed=1)["point"]
        )
        one = percentile_ci([0.5], lambda x: x[0], resamples=50, confidence=0.95, seed=1)
        self.assertEqual((one["low"], one["high"]), (0.5, 0.5))
        same = percentile_ci([0.2] * 20, lambda x: x[0], resamples=50, confidence=0.95, seed=1)
        self.assertEqual((same["low"], same["point"], same["high"]), (0.2, 0.2, 0.2))
        diff = difference_ci(
            [0.2] * 3, [0.2] * 3, lambda x: x[0], resamples=50, confidence=0.95, seed=1
        )
        self.assertEqual(diff["point"], 0.0)

    def test_degenerate_small_samples_never_decide_regression(self) -> None:
        a = clean_cell(self.plan, "chat_short_open", 1)
        b = [make_record(self.plan, ttft=0.5)]
        with tempfile.TemporaryDirectory() as ta, tempfile.TemporaryDirectory() as tb:
            write_events(Path(ta), make_run(a), a)
            write_events(Path(tb), make_run(b), b)
            base = ev_mod.evaluate(self.plan, Path(ta), "native")
            other = ev_mod.evaluate(self.plan, Path(tb), "native")
        self.assertEqual(ev_mod.compare_runs(self.plan, base, other)["decision"], "inconclusive")
        self.assertEqual(
            decide({"point": None}, None, 0.1, relative=True, higher_is_better=False)["decision"],
            "inconclusive",
        )
        self.assertEqual(
            decide(
                {"point": 1.0, "low": 1.0, "high": 1.0},
                0.0,
                0.1,
                relative=True,
                higher_is_better=False,
            )["decision"],
            "inconclusive",
        )

    def test_empty_cell_reports_nulls_not_scores(self) -> None:
        records = [
            make_record(self.plan, http_status=500, error={"kind": "http_error", "message": "x"})
            for _ in range(1)
        ]
        report = run_eval(self.plan, records)
        cell = report["cells"]["chat_short_open|rate=4rps"]
        self.assertIsNone(cell["ttft_s"]["p50"]["point"])
        self.assertIsNone(cell["decode"]["ftgr_tokens_per_s"])
        self.assertEqual(cell["fluidity"]["strict"]["index_mean"]["point"], 0.0)

    def test_identical_aa_arms_are_no_difference(self) -> None:
        a = clean_cell(self.plan, "aa_control_a", 12, role="aa_control")
        b = []
        for r in a:
            c = copy.deepcopy(r)
            c["cell_id"], c["unit_id"] = "aa_control_b", "aa_control_b|rate=4rps|rep0"
            c["request_id"] = r["request_id"].replace("aa_control_a", "aa_control_b")
            b.append(c)
        report = run_eval(self.plan, a + b)
        self.assertEqual(report["aa_control"]["decision"], "no_difference")


class DeterminismTests(unittest.TestCase):
    def test_evaluate_twice_is_byte_identical(self) -> None:
        the_plan = plan()
        fixture = Path(__file__).resolve().parent.parent / "fixtures" / "public" / "stall"
        if not fixture.is_dir():
            records = clean_cell(the_plan, "chat_short_open", 12)
            with tempfile.TemporaryDirectory() as tmp:
                d = write_events(Path(tmp), make_run(records), records)
                a = json.dumps(ev_mod.evaluate(the_plan, d, "native")[0], sort_keys=True)
                b = json.dumps(ev_mod.evaluate(the_plan, d, "native")[0], sort_keys=True)
        else:
            a = json.dumps(ev_mod.evaluate(the_plan, fixture, "native")[0], sort_keys=True)
            b = json.dumps(ev_mod.evaluate(the_plan, fixture, "native")[0], sort_keys=True)
        self.assertEqual(a, b)

    def test_cli_report_is_byte_identical_across_runs(self) -> None:
        the_plan = plan()
        records = clean_cell(the_plan, "chat_short_open", 12)
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp:
            d = write_events(Path(tmp) / "ev", make_run(records), records)
            outs = []
            for i in range(2):
                out = Path(tmp) / f"r{i}.json"
                with contextlib.redirect_stdout(io.StringIO()):
                    ev_mod.main(
                        ["--events", str(d), "--workload", str(MOCK_PLAN_PATH), "--out", str(out)]
                    )
                outs.append(out.read_bytes())
        self.assertEqual(outs[0], outs[1])


if __name__ == "__main__":
    unittest.main()
