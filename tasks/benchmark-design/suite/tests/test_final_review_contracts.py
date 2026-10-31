"""Regressions for output format, calibration coverage and failed-start evidence."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx
from bench.gates import judge
from bench.plan import load_plan, resolve_mode
from bench.prompts import check_expected, make_prompt
from bench.records import load_events
from evaluate import Evaluator, evaluate
from runner.run import Runner

from tests import test_calibration_capture as calibration_fixtures
from tests.helpers import SUITE, make_record, plan
from tests.test_mock_server import runner_args


class NumbersOnlyFormatTests(unittest.TestCase):
    def test_decimal_lines_preserve_generic_whitespace_and_leading_zero_normalization(self):
        for text in (
            "1\n2\n3", "1\n2\n3\n", "1\r\n2\r\n3\r\n", "\n\n1\n2\n3",
            " 1\t\n\n 2 \n3\n\n", "001\n02\n3", "1\v2\v3", "1\r2\r3",
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    check_expected(text, {"integer_sequence": True, "sequence_end": 3}), []
                )

    def test_nonempty_prose_fences_and_nondecimal_numbers_fail(self):
        invalid = (
            "Here are the numbers:\n1\n2\n3\n",
            "1\n2\n3\nDone.",
            "```text\n1\n2\n3\n```",
            "1\nexplanation\n2\n3",
            "1\n2\n2\n3",
            "1\n3",
            "1\n3\n2",
            "+1\n2\n3",
            "1\n2\n3\nExtra line",
            "1\nemoji \U0001f600\n2\n3",
            "١\n٢\n٣",
            "１\n２\n３",
            "1\n2\n" + "9" * 5000,
        )
        for text in invalid:
            with self.subTest(text=text[:80]):
                self.assertIn(
                    "integer_sequence_broken",
                    check_expected(text, {"integer_sequence": True, "sequence_end": 3}),
                )

    def test_single_integer_startup_uses_the_same_numbers_only_gate(self):
        workload = load_plan(SUITE / "workload.efficient.json")
        prompt = make_prompt(workload, "calibration_short", "startup", 0, 1)
        self.assertEqual(prompt.expected["sequence_end"], 1)
        for text in ("1", "1\n", "1\r\n", "\n\n1", " \t1\n\n", "001"):
            self.assertEqual(check_expected(text, prompt.expected), [])
        for text in ("Answer:\n1", "```\n1\n```", "1\nThank you.", "¹", "1\nemoji \U0001f600"):
            with self.subTest(text=text):
                record = make_record(workload, text=text, phase="startup_probe", role="startup")
                record["prompt"] = prompt.to_json()
                verdict = judge(record, workload, [])
                self.assertEqual(verdict.category, "failed", verdict.reasons)
                self.assertIn("invalid_output:integer_sequence_broken", verdict.reasons)


class CalibrationPopulationCoverageTests(unittest.TestCase):
    def test_omitting_a_whole_calibration_unit_cannot_produce_a_valid_report(self):
        with tempfile.TemporaryDirectory() as temp:
            path, events, _, _ = calibration_fixtures.CalibrationEvidenceTests().evidence(
                Path(temp)
            )
            workload = json.loads(path.read_text())
            missing = copy.deepcopy(workload["cells"][-1])
            missing["id"] = "omitted_calibration"
            workload["cells"].append(missing)
            workload["modes"]["calibration"]["cells"].append(missing["id"])
            report, evaluator, _ = evaluate(workload, events, "native")
            self.assertEqual(report["status"], "inconclusive", report)
            missing_id = next(
                u.unit_id for u in resolve_mode(workload, "calibration")
                if u.cell_id == missing["id"]
            )
            row = report["population"]["units"][missing_id]
            self.assertEqual((row["planned"], row["offered"], row["not_offered"]), (30, 0, 30))
            self.assertTrue(any(missing_id in reason for reason in evaluator.inconclusive))

    def test_only_explicit_fixture_mode_exempts_partial_unit_sets(self):
        workload = plan()
        evidence = load_events(SUITE / "fixtures/public/clean")
        self.assertEqual(Evaluator(workload, evidence).expected_units, {})
        workload["modes"]["pilot"] = copy.deepcopy(workload["modes"]["quick"])
        evidence.run.raw["mode"] = "pilot"
        self.assertEqual(
            set(Evaluator(workload, evidence).expected_units),
            {u.unit_id for u in resolve_mode(workload, "pilot")},
        )


class FailedStartupEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_failed_starts_load_and_keep_startup_diagnostics(self):
        original_client = httpx.AsyncClient
        original_popen = subprocess.Popen
        for outcome in ("preexisting_server", "process_exited"):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as temp:
                workload = plan()
                runner = Runner(
                    runner_args(out=temp, launch_cmd="mock-owned-engine", stop_after_launch=True),
                    workload,
                )

                def respond(request, outcome=outcome):
                    if outcome == "preexisting_server":
                        return httpx.Response(200, json={"data": [{"id": "m"}]})
                    raise httpx.ConnectError("no responder before launch", request=request)

                child = mock.Mock()
                child.poll.return_value = 2

                def start(argv, *args, child=child, **kwargs):
                    if argv == ["mock-owned-engine"]:
                        return child
                    return original_popen(argv, *args, **kwargs)

                transport = httpx.MockTransport(respond)
                with (
                    mock.patch(
                        "runner.run.httpx.AsyncClient",
                        side_effect=lambda transport=transport, **kw: original_client(
                            transport=transport, **kw
                        ),
                    ),
                    mock.patch("runner.run.subprocess.Popen", side_effect=start),
                ):
                    await runner.run()
                evidence = load_events(Path(temp))
                self.assertEqual(evidence.schema_errors, [])
                self.assertEqual(evidence.run.raw["status"], "aborted")
                self.assertEqual(evidence.run.startup[0]["status"], outcome)
                report, evaluator, _ = evaluate(workload, Path(temp), "native")
                self.assertIsNotNone(evaluator)
                self.assertEqual(report["status"], "invalid")
                self.assertEqual(report["startup"]["launches"][0]["status"], outcome)
                self.assertIsNone(
                    report["startup"]["launches"][0]["startup_to_first_valid_generation_s"]
                )
                self.assertEqual(report["startup"]["startup_to_first_valid_generation_s"]["n"], 0)


if __name__ == "__main__":
    unittest.main()
