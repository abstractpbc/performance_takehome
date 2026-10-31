"""Deadline checks exercise real hung child processes and a deliberately stalled HTTP server."""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from abstract_harness import measure
from abstract_harness.engines import StandinEngine
from abstract_harness.proposers import PiLaunchConfig, PiProposer, ProposerError

from . import _paths
from .test_proposers import FAKE_PI, PROFILES, _context
from .test_recovery import make_harness


class _StalledServer(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        time.sleep(3)

    def do_GET(self) -> None:  # noqa: N802
        time.sleep(3)


class _FixtureServer(BaseHTTPRequestHandler):
    """Protocol-only fixture; no weights, local model, or inference engine."""

    def log_message(self, *_args: object) -> None:
        pass

    def do_GET(self) -> None:  # noqa: N802
        body = b'{"data":[{"id":"fixture-model"}]}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for text in ("hello", " world"):
            event = {"choices": [{"delta": {"content": text}, "finish_reason": None}]}
            self.wfile.write(f"data: {json.dumps(event)}\n\n".encode())
            self.wfile.flush()
            time.sleep(0.01)
        event = {
            "choices": [{"delta": {}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 2, "prompt_tokens": 1},
        }
        self.wfile.write(f"data: {json.dumps(event)}\n\ndata: [DONE]\n\n".encode())
        self.wfile.flush()


class HardDeadlineTest(unittest.TestCase):
    def test_successful_workers_roundtrip_startup_traces_gates_and_measurement(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureServer)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                harness = make_harness(root)
                harness.engine = StandinEngine.create(_paths.REPO_ROOT, root)
                harness.benchmark = {
                    **harness.benchmark,
                    "prompts": [{"id": "fixed", "prompt": "fixture", "max_tokens": 4}],
                    "repetitions": 3,
                    "request_timeout_s": 2,
                }
                try:
                    with (
                        mock.patch(
                            "abstract_harness.loop.CONTAINER_PORT", server.server_address[1]
                        ),
                        mock.patch("abstract_harness.loop.docker.ensure_internal_network"),
                        mock.patch("abstract_harness.loop.docker.image_cmd", return_value=[]),
                        mock.patch("abstract_harness.loop.docker.run_detached"),
                        mock.patch(
                            "abstract_harness.loop.docker.container_running", return_value=True
                        ),
                        mock.patch(
                            "abstract_harness.loop.docker.container_ip", return_value="127.0.0.1"
                        ),
                        mock.patch("abstract_harness.loop.docker.container_logs", return_value=""),
                        mock.patch("abstract_harness.loop.docker.stop_container"),
                        mock.patch(
                            "abstract_harness.loop.procs.wait_for_quiet_host",
                            return_value={"quiet": True, "idle": 1.0},
                        ),
                    ):
                        measured, _info = harness.measure_image(
                            "fixture-only", golden={"fixed": "hello world"}, label="roundtrip"
                        )
                    self.assertEqual(measured.engine_model_id, "fixture-model")
                    self.assertTrue(measured.gates_passed)
                    self.assertGreater(measured.startup_s, 0)
                    self.assertEqual(len(measured.traces), 3)
                    self.assertTrue(
                        all(isinstance(t, measure.RequestTrace) for t in measured.traces)
                    )
                    self.assertTrue(all(isinstance(g, measure.GateResult) for g in measured.gates))
                    self.assertEqual(measured.traces[0].text, "hello world")
                    self.assertEqual(len(measured.ttft_samples), 3)
                    self.assertEqual(len(measured.ftgr_samples), 3)
                finally:
                    harness._release_locks()
        finally:
            server.shutdown()
            server.server_close()

    def test_container_exit_aborts_blocked_startup_worker_and_stops_container(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _StalledServer)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                harness = make_harness(root)
                harness.engine = StandinEngine.create(_paths.REPO_ROOT, root)
                harness.config.timeouts.startup_s = 30.0
                before = time.monotonic()
                try:
                    with (
                        mock.patch(
                            "abstract_harness.loop.CONTAINER_PORT", server.server_address[1]
                        ),
                        mock.patch("abstract_harness.loop.docker.ensure_internal_network"),
                        mock.patch("abstract_harness.loop.docker.image_cmd", return_value=[]),
                        mock.patch("abstract_harness.loop.docker.run_detached"),
                        mock.patch(
                            "abstract_harness.loop.docker.container_ip", return_value="127.0.0.1"
                        ),
                        mock.patch(
                            "abstract_harness.loop.docker.container_running",
                            side_effect=lambda *_args, **_kwargs: time.monotonic() - before < 0.75,
                        ) as running,
                        mock.patch("abstract_harness.loop.docker.container_logs", return_value=""),
                        mock.patch("abstract_harness.loop.docker.stop_container") as stop,
                        mock.patch(
                            "abstract_harness.loop.procs.wait_for_quiet_host",
                            return_value={"quiet": True, "idle": 1.0},
                        ),
                    ):
                        with self.assertRaisesRegex(
                            measure.MeasurementError, "container stopped during startup"
                        ):
                            harness.measure_image("fixture-only", golden=None, label="dead")
                    self.assertLess(time.monotonic() - before, 2.5)
                    self.assertEqual(running.call_count, 2)
                    self.assertLessEqual(running.call_args.kwargs["timeout_s"], 5.0)
                    stop.assert_called_once()
                    self.assertIsNone(harness._active_container)
                finally:
                    harness._release_locks()
        finally:
            server.shutdown()
            server.server_close()

    def test_expired_stage_cannot_start_with_a_new_one_second_floor(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = make_harness(Path(tmp))
            try:
                harness._attempt_deadline = time.monotonic() - 1
                with self.assertRaises(TimeoutError):
                    harness._stage_timeout(900)
            finally:
                harness._release_locks()

    def test_gpu_allocation_caps_deadline_even_with_longer_lease(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = make_harness(Path(tmp))
            try:
                harness.config.budget = replace(harness.config.budget, gpus_allocated=1)
                before = time.monotonic()
                harness._set_allocation_deadline(3600, 1 / 3600)
                self.assertLessEqual(harness._attempt_deadline - before, 1.05)
            finally:
                harness._release_locks()

    def test_expired_lease_refuses_baseline_before_building_anything(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            harness = make_harness(Path(tmp))
            harness.clock = lambda: 100.0
            harness.config.budget = replace(
                harness.config.budget,
                lease_start_ts=0,
                lease_deadline_ts=90,
            )
            with (
                mock.patch.object(harness, "install_signal_handlers"),
                mock.patch.object(harness, "recover_from_previous_run"),
                mock.patch.object(harness, "establish_baseline") as baseline,
                mock.patch.object(harness, "finalize") as finalize,
                mock.patch.object(harness, "cleanup"),
            ):
                self.assertEqual(harness.run(), 0)
            baseline.assert_not_called()
            self.assertIn("before baseline", finalize.call_args.args[0])

    def test_blocked_http_measurement_is_killed_before_whole_workload_finishes(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), _StalledServer)
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                harness = make_harness(Path(tmp))
                benchmark = measure.load_benchmark(
                    _paths.HARNESS_DIR / "development-benchmark.json"
                )
                before = time.monotonic()
                alive_timeouts = []

                def alive(timeout_s: float) -> bool:
                    alive_timeouts.append(timeout_s)
                    return True

                try:
                    with self.assertRaisesRegex(measure.MeasurementError, "time budget"):
                        harness._measurement_worker(
                            "benchmark",
                            {
                                "base_url": f"http://127.0.0.1:{server.server_address[1]}",
                                "model_id": "fake-model-no-inference",
                                "benchmark": benchmark,
                                "golden": None,
                                "startup_s": 0.0,
                            },
                            0.25,
                            is_alive=alive,
                        )
                    self.assertLess(time.monotonic() - before, 2.5)
                    self.assertTrue(alive_timeouts)
                    self.assertLessEqual(max(alive_timeouts), 0.25)
                finally:
                    harness._release_locks()
        finally:
            server.shutdown()
            server.server_close()

    def test_pi_hang_obeys_remaining_allocation_instead_of_its_900_second_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(
            "os.environ", {"HARNESS_TEST_KEY": "not-a-real-key"}
        ):
            root = Path(tmp)
            context_dir = root / "context"
            context_dir.mkdir()
            (context_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            work = root / "work"
            work.mkdir()
            context = _context(context_dir, work)
            context.timeout_s = 0.25
            context.deadline_monotonic = time.monotonic() + 0.25
            proposer = PiProposer(
                PiLaunchConfig(
                    pi_bin=FAKE_PI,
                    agent_dir=root / "agent",
                    api_key_env="HARNESS_TEST_KEY",
                    extra_env={"FAKE_PI_MODE": "hang"},
                ),
                profile=PROFILES["profiles"]["harness-design"],
            )
            before = time.monotonic()
            with self.assertRaisesRegex(ProposerError, "timed out"):
                proposer.propose(context)
            self.assertLess(time.monotonic() - before, 2.5)
