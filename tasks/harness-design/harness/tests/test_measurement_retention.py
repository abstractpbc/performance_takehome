"""Actual parent/HTTP-worker boundaries retain startup bytes on every exit path."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from abstract_harness import manifest, measure
from abstract_harness.engines import StandinEngine
from abstract_harness.loop import Interrupted

from . import _paths
from .test_recovery import make_harness


class _StartupEngine(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - HTTP API
        self._respond(200, json.dumps({"data": [{"id": "retention-model"}]}).encode())

    def do_POST(self) -> None:  # noqa: N802 - HTTP API
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        self.server.probes += 1
        number = self.server.probes
        if number == 3 and self.server.mode in {"timeout", "abort"}:
            self.server.third_started.set()
            self.server.release.wait(10)
            return
        if number == 3 and self.server.mode == "success":
            body = (
                b'data: {"choices":[{"delta":{"content":"Hello"},"finish_reason":"stop"}],'
                b'"usage":{"prompt_tokens":2,"completion_tokens":1}}\n\n'
                b"data: [DONE]\n\n"
            )
            self._respond(200, body)
        else:
            self._respond(500, f"failed-probe-{number}:".encode() + b"x" * 1800)

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MeasurementRetentionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.harness = make_harness(self.root)
        self.harness.engine = StandinEngine.create(_paths.REPO_ROOT, self.root, cpus="1")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _StartupEngine)
        self.server.daemon_threads = True
        self.server.mode = "failure"
        self.server.probes = 0
        self.server.third_started = threading.Event()
        self.server.release = threading.Event()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.release.set()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.harness._release_locks()
        self.tmp.cleanup()

    def _startup(self, timeout_s: float = 5.0):
        return self.harness._measurement_worker(
            "startup",
            {
                "base_url": f"http://127.0.0.1:{self.server.server_address[1]}",
                "started_at": time.monotonic(),
                "benchmark": self.harness.benchmark,
                "timeout_s": 90.0,
            },
            timeout_s,
        )

    def _logs(self, expected_probes: int):
        workers = list((self.harness.config.state_dir / "measurement-workers").glob("worker-*"))
        self.assertEqual(len(workers), 1)
        worker = workers[0]
        self.assertEqual(json.loads((worker / "input.json").read_text())["operation"], "startup")
        stderr = (worker / "stderr.log").read_text()
        events = [json.loads(line) for line in stderr.splitlines() if line.startswith('{"event":')]
        self.assertEqual(len(events), expected_probes)
        evidence = manifest.build_manifest(self.harness, integrity_output="", integrity_rc=0)[
            "evidence"
        ]
        self.assertEqual(
            evidence["measurement_workers"],
            [
                {
                    "input": str(worker / "input.json"),
                    "stdout": str(worker / "stdout.json"),
                    "stderr": str(worker / "stderr.log"),
                }
            ],
        )
        self.assertFalse(list(self.harness.config.reports_dir.glob("*.json")))
        return worker, events, stderr

    def test_success_keeps_every_probe_and_separate_parseable_result(self) -> None:
        self.server.mode = "success"
        elapsed, model_id = self._startup()
        self.assertEqual(model_id, "retention-model")
        self.assertGreater(elapsed, 0)
        worker, events, _ = self._logs(3)
        self.assertEqual([e["trace"]["status"] for e in events], [500, 500, 200])
        self.assertEqual(events[-1]["trace"]["text"], "Hello")
        self.assertEqual(json.loads((worker / "stdout.json").read_text()), [elapsed, model_id])

    def test_failure_keeps_full_probes_beyond_exception_tail(self) -> None:
        with self.assertRaisesRegex(measure.MeasurementError, "startup failed") as failure:
            self._startup()
        worker, events, stderr = self._logs(3)
        self.assertGreater(len(stderr), 5000)
        self.assertIn("3/3 probes", stderr)
        self.assertTrue(all(f"failed-probe-{i}" in stderr for i in (1, 2, 3)))
        self.assertNotIn("failed-probe-1", str(failure.exception))
        self.assertTrue(all(e["trace"]["status"] == 500 for e in events))
        self.assertEqual((worker / "stdout.json").read_text(), "")

    def test_timeout_keeps_completed_probes_before_stalled_request(self) -> None:
        self.server.mode = "timeout"
        started = time.monotonic()
        with self.assertRaisesRegex(measure.MeasurementError, "exceeded its time budget"):
            self._startup(timeout_s=2.5)
        self.assertLess(time.monotonic() - started, 8)
        self.assertTrue(self.server.third_started.is_set())
        worker, _, _ = self._logs(2)
        self.assertEqual((worker / "stdout.json").read_text(), "")

    def test_controller_abort_keeps_completed_probes_before_stalled_request(self) -> None:
        self.server.mode = "abort"

        def abort_after_third_request() -> None:
            if self.server.third_started.wait(5):
                self.harness._stop_requested = True

        aborter = threading.Thread(target=abort_after_third_request, daemon=True)
        aborter.start()
        try:
            with self.assertRaises(Interrupted):
                self._startup()
        finally:
            aborter.join(timeout=5)
        self.assertTrue(self.server.third_started.is_set())
        worker, _, _ = self._logs(2)
        self.assertEqual((worker / "stdout.json").read_text(), "")
