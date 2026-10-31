"""Mock server + runner integration (loopback only, scaled time): startup clock and oracles."""

import asyncio
import json
import tempfile
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

import httpx
from bench.plan import load_plan
from mock_server.faults import PROFILES, answer_for, mock_tokenize, plan_stream, profile_by_name
from mock_server.server import MockServer
from runner.run import Runner

from tests.helpers import MOCK_PLAN_PATH


def runner_args(**overrides) -> Namespace:
    base = dict(
        plan=str(MOCK_PLAN_PATH),
        mode="fixture",
        out=None,
        base_url=None,
        model=None,
        launch_cmd=None,
        stop_after_launch=False,
        cache_flush_url=None,
        request_timeout_s=30.0,
        time_scale=0.05,
        run_id="t",
        only_unit="nothing-matches",
        gpu_count=0,
        gpu_hours=0.0,
        gpu_kind="none (CPU)",
        assume_mock=True,
        dry_run=False,
    )
    base.update(overrides)
    return Namespace(**base)


class FaultPlanningTests(unittest.TestCase):
    def test_every_readme_family_has_a_profile(self) -> None:
        families = {p.family for p in PROFILES.values()}
        for family in (
            "control",
            "bursts",
            "stalls",
            "late_first_tokens",
            "reasoning_channels",
            "unicode_splits",
            "missing_completion",
            "errors_and_drops",
            "invalid_output",
            "false_usage",
            "misleading_readiness",
            "startup",
        ):
            self.assertIn(family, families)

    def test_plan_stream_is_deterministic_and_oracle_separate(self) -> None:
        import random

        body = {
            "messages": [
                {
                    "role": "user",
                    "content": "Begin your reply with the exact code CODE-ABCD-1234 on its own "
                    "line.",
                }
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        a = plan_stream("r", body, profile_by_name("false_usage"), random.Random(5), "m", 20)
        b = plan_stream("r", body, profile_by_name("false_usage"), random.Random(5), "m", 20)
        self.assertEqual([c.payload for c in a.chunks], [c.payload for c in b.chunks])
        self.assertEqual(a.oracle["usage_completion_tokens"], 3 * a.oracle["true_tokens"])
        self.assertTrue(a.oracle["answer_is_correct"])
        self.assertIn("CODE-ABCD-1234", answer_for(body["messages"]))
        self.assertNotEqual(mock_tokenize("latency matters"), ["latency", " matters"])
        wrong = plan_stream("r", body, profile_by_name("wrong_text"), random.Random(5), "m", 20)
        self.assertFalse(wrong.oracle["answer_is_correct"])
        missing = plan_stream("r", body, profile_by_name("missing_done"), random.Random(5), "m", 20)
        self.assertFalse(missing.oracle["done_emitted"])
        self.assertNotIn("[DONE]", [c.payload for c in missing.chunks])


class LoopbackTests(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def test_misleading_readiness_does_not_stop_startup_clock(self) -> None:
        async def scenario():
            server = MockServer(profile_by_name("misleading_readiness"), 3, time_scale=0.1)
            await server.start()
            try:
                with tempfile.TemporaryDirectory() as tmp:
                    fixture_plan = load_plan(MOCK_PLAN_PATH)
                    fixture_plan["startup"]["probe_interval_s"] = 2.0
                    runner = Runner(
                        # Set an explicit 2s retry gap and scale it to 0.4s so the
                        # >0.2s assertion follows the fixture, not incidental host lag.
                        runner_args(base_url=server.base_url, out=tmp, time_scale=0.2),
                        fixture_plan,
                    )
                    runner._requests_fh = (Path(tmp) / "requests.jsonl").open("w", encoding="utf-8")
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        # Reject the first generation deterministically. An elapsed-time
                        # fault window can expire during HTTP initialization on a busy host.
                        readiness = iter((False, True))
                        server.started_mono = time.monotonic()
                        with mock.patch.object(
                            server, "inference_ready", side_effect=lambda: next(readiness, True)
                        ):
                            await runner.startup(client, 0)
                    runner._requests_fh.close()
                    with (Path(tmp) / "requests.jsonl").open(encoding="utf-8") as captured:
                        probes = sum(1 for _ in captured)
                return runner.startups[0], probes
            finally:
                await server.close()

        launch, probes = self.run_async(scenario())
        self.assertEqual(launch["status"], "valid")
        self.assertIsNotNone(launch["readiness_at"])
        self.assertGreater(launch["first_valid_generation_at"] - launch["readiness_at"], 0.2)
        self.assertGreaterEqual(probes, 2)  # at least one 503 probe before the valid one
        self.assertTrue(
            any(not p["ok"] for p in launch["readiness_probes"]) is False
        )  # readiness was 200 throughout

    def test_stream_events_match_oracle(self) -> None:
        async def scenario():
            server = MockServer(profile_by_name("stall"), 9, time_scale=0.02)
            await server.start()
            try:
                body = {
                    "model": server.model_id,
                    "stream": True,
                    "max_tokens": 64,
                    "temperature": 0,
                    "stream_options": {"include_usage": True},
                    "messages": [
                        {
                            "role": "user",
                            "content": "List every integer from 1 to 12 inclusive, one per line.",
                        }
                    ],
                }
                async with httpx.AsyncClient(timeout=10.0) as client:
                    payloads = []
                    async with client.stream(
                        "POST",
                        server.base_url + "/v1/chat/completions",
                        json=body,
                        headers={"X-Bench-Request-Id": "q"},
                    ) as resp:
                        async for line in resp.aiter_lines():
                            if line.startswith("data:"):
                                payloads.append(line[5:].strip())
                    oracle = (await client.get(server.base_url + "/_mock/oracle")).json()["q"]
                return payloads, oracle
            finally:
                await server.close()

        payloads, oracle = self.run_async(scenario())
        text = "".join(
            json.loads(p)["choices"][0]["delta"].get("content", "")
            for p in payloads
            if p != "[DONE]" and json.loads(p)["choices"]
        )
        self.assertEqual(text, oracle["true_text"])
        self.assertEqual(payloads[-1], "[DONE]")
        self.assertEqual(oracle["stall_s"], 1.5)
        self.assertEqual(len(oracle["true_token_offsets_s"]), oracle["true_tokens"])


if __name__ == "__main__":
    unittest.main()
