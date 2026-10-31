"""Completed requests survive a worker killed during its next request."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from abstract_harness import measure


class RequestJournalTest(unittest.TestCase):
    def test_startup_completion_precedes_slow_persistence_callback(self):
        clock = [10.0]
        persisted = []
        trace = measure.RequestTrace("startup-probe", True, finish_reason="stop")

        def stream(*_args, **_kwargs):
            clock[0] = 12.0
            return trace

        def journal(value):
            persisted.append(value)
            clock[0] += 100.0

        benchmark = {"startup_probe": {"prompt": "hello", "max_tokens": 4}, "request_timeout_s": 30}
        with (
            mock.patch.object(measure.time, "monotonic", side_effect=lambda: clock[0]),
            mock.patch.object(measure, "wait_for_model_id", return_value="fixture"),
            mock.patch.object(measure, "stream_chat_completion", side_effect=stream),
        ):
            startup, model = measure.wait_for_first_valid_generation(
                "http://unused", 0.0, benchmark, timeout_s=60, on_probe=journal
            )
        self.assertEqual(persisted, [trace])
        self.assertEqual(clock[0], 112.0)
        self.assertEqual(startup, 12.0)
        self.assertEqual(model, "fixture")

    def test_callback_does_not_change_measurement_or_gates(self):
        benchmark = {
            "repetitions": 2,
            "request_timeout_s": 2,
            "max_tokens": 8,
            "prompts": [{"id": "p", "prompt": "p"}],
            "require_usage": True,
        }
        traces = [
            measure.RequestTrace(
                "p",
                True,
                text="a b",
                finish_reason="stop",
                ttft_s=0.1,
                arrival_times_s=[0.1, 0.2],
                streamed_chunks=2,
                usage_completion_tokens=2,
                usage_prompt_tokens=1,
                total_s=0.3,
            )
        ] * 2
        with mock.patch.object(measure, "stream_chat_completion", side_effect=traces):
            original = measure.run_benchmark(
                "http://unused", "m", benchmark, golden={"p": "a b"}, startup_s=0.1
            )
        recorded = []
        with mock.patch.object(measure, "stream_chat_completion", side_effect=traces):
            journaled = measure.run_benchmark(
                "http://unused",
                "m",
                benchmark,
                golden={"p": "a b"},
                startup_s=0.1,
                on_trace=recorded.append,
            )
        self.assertEqual(asdict(original), asdict(journaled))
        self.assertEqual(recorded, traces)

    @unittest.skipUnless(os.name == "posix", "native process interruption")
    def test_real_worker_killed_mid_next_request_retains_first_trace(self):
        entered_second = threading.Event()
        release_second = threading.Event()
        count = 0

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):  # noqa: N802
                nonlocal count
                self.rfile.read(int(self.headers["Content-Length"]))
                count += 1
                if count == 2:
                    entered_second.set()
                    release_second.wait(15)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                for token in ("a", " b"):
                    event = {
                        "choices": [
                            {"index": 0, "delta": {"content": token}, "finish_reason": None}
                        ]
                    }
                    self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                    self.wfile.flush()
                    time.sleep(0.01)
                end = {
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                }
                self.wfile.write(("data: " + json.dumps(end) + "\n\ndata: [DONE]\n\n").encode())
                self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        worker = Path(measure.__file__).with_name("measurement_worker.py")
        payload = {
            "operation": "benchmark",
            "base_url": f"http://127.0.0.1:{server.server_port}",
            "model_id": "fixture",
            "golden": None,
            "startup_s": 0.1,
            "benchmark": {
                "repetitions": 2,
                "request_timeout_s": 30,
                "max_tokens": 8,
                "prompts": [{"id": "p", "prompt": "p"}],
            },
        }
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                with (
                    (root / "stdout.json").open("w") as out,
                    (root / "stderr.jsonl").open("w") as err,
                ):
                    child = subprocess.Popen(
                        [sys.executable, str(worker)],
                        stdin=subprocess.PIPE,
                        stdout=out,
                        stderr=err,
                        text=True,
                    )
                    try:
                        child.stdin.write(json.dumps(payload))
                        child.stdin.close()
                        self.assertTrue(
                            entered_second.wait(10), "worker never reached next request"
                        )
                        child.kill()
                        child.wait(timeout=5)
                    finally:
                        if child.poll() is None:
                            child.kill()
                            child.wait(timeout=5)
                lines = (root / "stderr.jsonl").read_text().splitlines()
                records = [json.loads(line) for line in lines]
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["event"], "benchmark_request")
                self.assertEqual(records[0]["trace"]["text"], "a b")
                self.assertEqual(records[0]["trace"]["usage_completion_tokens"], 2)
                self.assertEqual((root / "stdout.json").read_bytes(), b"")
        finally:
            release_second.set()
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
