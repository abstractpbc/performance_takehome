"""Regressions for the independent Greptile review of the first audited PR head."""

import asyncio
import copy
import shlex
import socket
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import evaluate as ev_mod
import httpx
from bench.gates import judge, token_times
from bench.plan import resolve_mode
from bench.prompts import make_prompt
from mock_server.faults import profile_by_name
from mock_server.server import MockServer
from runner.run import RequestMeta, Runner

from tests.helpers import chunk, clean_cell, make_record, make_run, plan, write_events
from tests.test_mock_server import runner_args


class EvidenceRegressionTests(unittest.TestCase):
    def test_cjk_spaces_do_not_move_a_later_token_to_an_earlier_delta(self):
        self.assertEqual(token_times([(" 日", 1.0), ("本", 2.0)], 0.0), ([1.0, 2.0], 2))
        self.assertEqual(
            token_times([(" 日", 1.0), ("本 next", 2.0)], 0.0), ([1.0, 2.0, 2.0], 3)
        )

    def test_measurements_require_a_planned_send_but_startup_does_not(self):
        the_plan = plan()
        for phase in ("measure", "startup_probe", "warmup"):
            record = make_record(the_plan, phase=phase)
            record["timestamps_mono_s"]["planned_send"] = None
            verdict = judge(record, the_plan, [])
            self.assertEqual(
                verdict.category, "evidence_invalid" if phase == "measure" else "completed"
            )
            if phase == "measure":
                self.assertIn("missing_timestamp:planned_send for measurement", verdict.reasons)

    def test_prompt_is_distinct_across_load_points_but_stable_for_a_rerun(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = Runner(runner_args(out=tmp), plan())
            unit = resolve_mode(runner.plan, "quick")[0]
            other = replace(unit, load=unit.load * 2)
            first = runner._prompt(unit, "measure", 0)
            second = runner._prompt(other, "measure", 0)
            self.assertNotEqual(first.prompt_id, second.prompt_id)
            self.assertNotEqual(first.expected["nonce"], second.expected["nonce"])
            self.assertNotEqual(first.messages, second.messages)
            self.assertEqual(first, runner._prompt(unit, "measure", 0))

    def test_partial_decode_population_cannot_claim_a_comparison(self):
        the_plan = plan()
        records = clean_cell(the_plan, "chat_short_open", 12)
        # Keep enough ordinary traces for bootstrap while one valid request arrives
        # all at once. The same missing decode evidence must affect cell and comparison.
        unresolved = make_record(the_plan, index=12, request_id="extra/measure/12", send=150.0)
        unresolved = make_record(
            the_plan,
            index=12,
            request_id="extra/measure/12",
            send=150.0,
            deltas=[unresolved["text"]["content"]],
        )
        records.append(unresolved)
        with tempfile.TemporaryDirectory() as tmp:
            write_events(Path(tmp), make_run(records), records)
            result = ev_mod.evaluate(the_plan, Path(tmp), "native")
            self.assertEqual(result[0]["status"], "valid", result[0].get("reasons"))
            cell = result[0]["cells"]["chat_short_open|rate=4rps"]
            self.assertIsNone(cell["decode"]["ftgr_tokens_per_s"])
            self.assertEqual(cell["decode"]["min_decode_deadline_s"]["unresolved_traces"], 1)
            comparison = ev_mod.compare_runs(the_plan, result, result)
            metric = comparison["per_cell"]["chat_short_open|rate=4rps"]["metrics"][
                "min_decode_deadline_p99_s"
            ]
            self.assertEqual(metric["decision"], "inconclusive")
            self.assertIn("unresolved", metric["why"])


class CaptureRegressionTests(unittest.TestCase):
    def test_oversized_sse_at_boundary_or_eof_is_a_recorded_failure_and_next_request_runs(self):
        class Chunks(httpx.AsyncByteStream):
            def __init__(self, pieces):
                self.pieces = pieces

            async def __aiter__(self):
                for piece in self.pieces:
                    yield piece

        async def scenario(eof):
            with tempfile.TemporaryDirectory() as tmp:
                the_plan = plan()
                runner = Runner(runner_args(out=tmp), the_plan)
                spec = make_prompt(the_plan, "chat_short", "capture", 0, 0)
                good_text = spec.expected["nonce"] + "\n" + "tail latency matters " * 6
                prefix = ("data: " + chunk("prefix") + "\n\n").encode()
                oversized = b"data: " + b"x" * 1_048_577 + (b"" if eof else b"\n\n")
                calls = 0

                async def response(request):
                    nonlocal calls
                    calls += 1
                    body = (
                        [prefix, oversized]
                        if calls == 1
                        else [
                            (
                                "data: " + chunk(good_text, finish="stop") + "\n\ndata: [DONE]\n\n"
                            ).encode()
                        ]
                    )
                    return httpx.Response(
                        200, headers={"content-type": "text/event-stream"}, stream=Chunks(body)
                    )

                async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
                    failed = await runner.execute(
                        client, spec, RequestMeta("oversized", None, "startup_probe", "startup", 0)
                    )
                    next_record = await runner.execute(
                        client, spec, RequestMeta("next", None, "startup_probe", "startup", 1)
                    )
                self.assertEqual(failed["error"]["kind"], "protocol")
                self.assertIn("size limit", failed["error"]["message"])
                self.assertEqual(failed["text"]["content"], "prefix")
                self.assertIsNotNone(failed["timestamps_mono_s"]["end"])
                self.assertEqual(judge(failed, the_plan, []).category, "failed")
                self.assertEqual(judge(next_record, the_plan, []).category, "completed")

        for eof in (False, True):
            with self.subTest(eof=eof):
                asyncio.run(scenario(eof))

    def test_repeated_cold_launches_do_not_overlap_on_the_same_port(self):
        async def scenario():
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                port = listener.getsockname()[1]
            with tempfile.TemporaryDirectory() as tmp:
                the_plan = copy.deepcopy(plan())
                the_plan["modes"]["fixture"]["startup_launches"] = 2
                the_plan["startup"]["max_wait_s"] = 30
                the_plan["startup"]["readiness_poll_interval_s"] = 0.01
                child_script = Path(tmp) / "engine.py"
                suite = Path(__file__).resolve().parent.parent
                child_script.write_text(
                    "import asyncio, sys\n"
                    f"sys.path.insert(0, {str(suite)!r})\n"
                    "from mock_server.server import MockServer\n"
                    "from mock_server.faults import profile_by_name\n"
                    "async def serve():\n"
                    f"    server = MockServer(profile_by_name('clean'), 7, port={port}, "
                    "time_scale=0.005)\n"
                    "    await server.start()\n"
                    "    await asyncio.Event().wait()\n"
                    "asyncio.run(serve())\n",
                    encoding="utf-8",
                )
                command = f"{shlex.quote(sys.executable)} -B {shlex.quote(str(child_script))}"
                runner = Runner(
                    runner_args(
                        out=tmp,
                        base_url=f"http://127.0.0.1:{port}",
                        launch_cmd=command,
                        stop_after_launch=True,
                        time_scale=1.0,
                    ),
                    the_plan,
                )
                children = []
                overlap = []
                real_popen = subprocess.Popen

                def start(*args, **kwargs):
                    overlap.append(any(child.poll() is None for child in children))
                    child = real_popen(
                        *args, **kwargs, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    children.append(child)
                    return child

                try:
                    with mock.patch("runner.run.subprocess.Popen", side_effect=start):
                        await runner.run()
                    self.assertEqual(runner.status, "completed", runner.startups)
                    self.assertEqual([s["status"] for s in runner.startups], ["valid", "valid"])
                    self.assertEqual(overlap, [False, False])
                    self.assertTrue(all(child.poll() is not None for child in children))
                finally:
                    for child in children:
                        if child.poll() is None:
                            child.kill()
                            child.wait(timeout=5)

        asyncio.run(scenario())

    def test_dead_launch_cannot_claim_a_warm_servers_generation(self):
        async def scenario():
            server = MockServer(profile_by_name("clean"), 3, time_scale=0.02)
            await server.start()
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    runner = Runner(
                        runner_args(
                            out=tmp,
                            base_url=server.base_url,
                            launch_cmd=f"{shlex.quote(sys.executable)} -c 'raise SystemExit(2)'",
                            stop_after_launch=True,
                        ),
                        plan(),
                    )
                    await runner.run()
                    self.assertEqual(runner.status, "aborted")
                    self.assertEqual(runner.startups[0]["status"], "preexisting_server")
                    self.assertIsNone(runner.startups[0]["first_valid_generation_at"])
            finally:
                await server.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
