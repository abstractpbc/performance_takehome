"""Actual loopback requests for the optional fixed LLMD assistant prefill."""

from __future__ import annotations

import copy
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from abstract_harness import measure

from . import _paths

PROFILE = "llmd_closed_thinking_v1"


class _PrefillEngine(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"data":[{"id":"prefill-fixture"}]}')

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(request)
        messages = request.get("messages", [])
        valid = (
            len(messages) == 2
            and messages[0].get("role") == "user"
            and messages[1] == {"role": "assistant", "content": "</think>\n\n"}
            and request.get("continue_final_message") is True
            and request.get("add_generation_prompt") is False
        )
        if not valid:
            self.send_response(422)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for text in ("Done", " now."):
            event = {"choices": [{"delta": {"content": text}, "finish_reason": None}]}
            self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
            self.wfile.flush()
            time.sleep(0.01)
        event = {
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }
        self.wfile.write(("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode())


class RequestProfileTest(unittest.TestCase):
    def test_startup_and_every_measured_prompt_use_one_exact_profile(self) -> None:
        _PrefillEngine.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _PrefillEngine)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        prompts = [
            {"id": "nonce", "prompt": "Return TEST-98731 and 7.", "max_tokens": 16},
            {
                "id": "explain",
                "prompt": "Explain why leaves are green.",
                "max_tokens": 48,
            },
            {"id": "unicode", "prompt": "Name two café drinks ☕.", "max_tokens": 24},
        ]
        benchmark = {
            "repetitions": 2,
            "max_tokens": 32,
            "request_timeout_s": 2,
            "startup_probe": {"prompt": "Say hello.", "max_tokens": 4},
            "prompts": prompts,
            "extra_request_fields": {},
            "request_profile": PROFILE,
        }
        frozen = copy.deepcopy(benchmark)
        url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            startup, model = measure.wait_for_first_valid_generation(
                url, time.monotonic(), benchmark, timeout_s=2
            )
            measured = measure.run_benchmark(url, model, benchmark, golden=None, startup_s=startup)
            self.assertTrue(measured.gates_passed)
            self.assertEqual(benchmark, frozen)
            expected = [benchmark["startup_probe"], *prompts, *prompts]
            self.assertEqual(len(_PrefillEngine.requests), len(expected))
            for body, original in zip(_PrefillEngine.requests, expected, strict=True):
                self.assertEqual(
                    body["messages"],
                    [
                        {"role": "user", "content": original["prompt"]},
                        {"role": "assistant", "content": "</think>\n\n"},
                    ],
                )
                self.assertEqual(body["max_tokens"], original["max_tokens"])
                self.assertEqual(body["model"], "prefill-fixture")
                self.assertIs(body["continue_final_message"], True)
                self.assertIs(body["add_generation_prompt"], False)
                self.assertEqual(body["temperature"], 0.0)
                self.assertEqual(body["top_k"], 1)
                self.assertEqual(body["seed"], 0)
                self.assertIs(body["stream"], True)
                self.assertEqual(body["stream_options"], {"include_usage": True})
                self.assertNotIn("request_profile", body)
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)

    def test_default_body_and_existing_extension_are_unchanged(self) -> None:
        self.assertEqual(
            measure.build_chat_request(
                "m",
                "A different question?",
                max_tokens=9,
                extra_fields={"cache_prompt": False},
            ),
            {
                "model": "m",
                "messages": [{"role": "user", "content": "A different question?"}],
                "max_tokens": 9,
                "temperature": 0.0,
                "top_k": 1,
                "seed": 0,
                "stream": True,
                "stream_options": {"include_usage": True},
                "cache_prompt": False,
            },
        )

    def test_reserved_extra_fields_cannot_replace_the_controlled_body(self) -> None:
        fields = [
            "model",
            "messages",
            "max_tokens",
            "temperature",
            "top_k",
            "seed",
            "stream",
            "stream_options",
            "continue_final_message",
            "add_generation_prompt",
            "request_profile",
        ]
        for profile in (None, PROFILE):
            for key in fields:
                with (
                    self.subTest(profile=profile, key=key),
                    self.assertRaises(measure.BenchmarkError),
                ):
                    measure.build_chat_request(
                        "m",
                        "q",
                        max_tokens=8,
                        extra_fields={key: "override"},
                        request_profile=profile,
                    )

    def test_unknown_profiles_and_conflicting_reasoning_options_are_rejected(
        self,
    ) -> None:
        for profile in ("", "llmd_closed_thinking_v2", True, 1, {}, []):
            with (
                self.subTest(profile=profile),
                self.assertRaises(measure.BenchmarkError),
            ):
                measure.build_chat_request("m", "q", max_tokens=8, request_profile=profile)
        for key, value in (
            ("enable_thinking", True),
            ("chat_template_kwargs", {"enable_thinking": False}),
            ("reasoning_effort", 10),
        ):
            with self.subTest(key=key), self.assertRaises(measure.BenchmarkError):
                measure.build_chat_request(
                    "m",
                    "q",
                    max_tokens=8,
                    request_profile=PROFILE,
                    extra_fields={key: value},
                )

    def test_policy_failure_precedes_startup_and_measurement_network_calls(
        self,
    ) -> None:
        bad_cases = [
            {"request_profile": "unknown"},
            {"request_profile": None},
            {"request_profile": PROFILE, "extra_request_fields": {"messages": []}},
        ]
        for benchmark in bad_cases:
            with (
                self.subTest(benchmark=benchmark),
                mock.patch.object(measure, "_open_connection") as opened,
            ):
                with self.assertRaises(measure.BenchmarkError):
                    measure.wait_for_first_valid_generation(
                        "http://127.0.0.1:1", time.monotonic(), benchmark, timeout_s=1
                    )
                with self.assertRaises(measure.BenchmarkError):
                    measure.run_benchmark(
                        "http://127.0.0.1:1",
                        "m",
                        benchmark,
                        golden=None,
                        startup_s=None,
                    )
                opened.assert_not_called()

    def test_load_benchmark_accepts_known_profile_and_rejects_null_or_unknown(
        self,
    ) -> None:
        source = _paths.HARNESS_DIR / "development-benchmark.json"
        original = source.read_bytes()
        for profile in (PROFILE, None, "unknown"):
            data = json.loads(original)
            data["request_profile"] = profile
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "benchmark.json"
                path.write_text(json.dumps(data), encoding="utf-8")
                if profile == PROFILE:
                    self.assertEqual(measure.load_benchmark(path)["request_profile"], PROFILE)
                else:
                    with self.assertRaises(measure.BenchmarkError):
                        measure.load_benchmark(path)
        self.assertEqual(source.read_bytes(), original)

    def test_extra_fields_must_be_a_mapping(self) -> None:
        for extra in ([], "body", 1, False):
            with self.subTest(extra=extra), self.assertRaises(measure.BenchmarkError):
                measure.build_chat_request("m", "q", max_tokens=8, extra_fields=extra)
