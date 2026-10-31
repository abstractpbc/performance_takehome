"""Actual loopback wire regression; does not claim a real engine supports this policy."""
from __future__ import annotations

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from abstract_harness import measure

from . import _paths  # noqa: F401


class _PolicyEngine(BaseHTTPRequestHandler):
    requests: list[dict] = []

    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        body = b'{"data":[{"id":"policy-fixture"}]}'
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        self.requests.append(request)
        if request.get("chat_template_kwargs") != {"enable_thinking": False}:
            self.send_response(422)
            self.end_headers()
            self.wfile.write(b'fixture requires the configured request policy')
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for text in ("Hello", " world."):
            event = {"choices": [{"delta": {"content": text}, "finish_reason": None}]}
            self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
            self.wfile.flush()
            time.sleep(0.01)
        event = {"choices": [{"delta": {}, "finish_reason": "stop"}],
                 "usage": {"prompt_tokens": 3, "completion_tokens": 2}}
        self.wfile.write(("data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n").encode())


class StartupPolicyTest(unittest.TestCase):
    def test_startup_and_measurement_send_the_same_frozen_request_policy(self) -> None:
        _PolicyEngine.requests = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), _PolicyEngine)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        benchmark = {
            "repetitions": 1,
            "max_tokens": 8,
            "request_timeout_s": 2,
            "startup_probe": {"prompt": "Say hello.", "max_tokens": 4},
            "prompts": [{"id": "hello", "prompt": "Say hello world.", "allow_length": False}],
            "extra_request_fields": {"chat_template_kwargs": {"enable_thinking": False}},
        }
        url = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            startup_s, model = measure.wait_for_first_valid_generation(
                url, time.monotonic(), benchmark, timeout_s=2.0
            )
            measured = measure.run_benchmark(
                url, model, benchmark, golden=None, startup_s=startup_s
            )
            self.assertTrue(measured.gates_passed)
            self.assertEqual(len(_PolicyEngine.requests), 2)
            self.assertEqual(
                [request["messages"] for request in _PolicyEngine.requests],
                [[{"role": "user", "content": "Say hello."}],
                 [{"role": "user", "content": "Say hello world."}]],
            )
            self.assertEqual([request["max_tokens"] for request in _PolicyEngine.requests], [4, 8])
            for request in _PolicyEngine.requests:
                self.assertEqual(request["model"], "policy-fixture")
                self.assertEqual(request["chat_template_kwargs"], {"enable_thinking": False})
                self.assertEqual(request["temperature"], 0.0)
                self.assertEqual(request["top_k"], 1)
                self.assertEqual(request["seed"], 0)
                self.assertTrue(request["stream"])
                self.assertEqual(request["stream_options"], {"include_usage": True})
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)
