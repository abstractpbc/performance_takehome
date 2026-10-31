"""Regressions for capture cancellation and measurement-boundary stalls."""

import asyncio
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from capture import CaptureError, bounded_command, cleanup_container
from runner.run import LagMonitor

SUITE = Path(__file__).resolve().parents[1]
OWNER = "d" * 32
NAME = "abstract-t1-" + OWNER
IMAGE = "sha256:" + "e" * 64
IDENTIFIER = "f" * 64


class MeasurementBoundaryLagTests(unittest.TestCase):
    def test_stall_starting_before_window_is_not_hidden_by_other_samples(self):
        monitor = LagMonitor()
        monitor.samples = [(9.995, 10.085, 0.08)] + [
            (10.1 + i * 0.01, 10.11 + i * 0.01, 0.0) for i in range(20)
        ]
        summary = monitor.summary([("measure", 10.0, 10.5)])
        self.assertAlmostEqual(summary["event_loop_lag_max_s"], 0.08)
        self.assertGreater(summary["event_loop_lag_max_s"], 0.05)
        self.assertEqual(summary["samples"], 21)

    def test_pre_window_delay_and_nominal_sleep_are_not_invented_as_stalls(self):
        monitor = LagMonitor()
        monitor.samples = [(-0.08, 0.015, 0.085), (0.02, 0.03, 0.0)]
        summary = monitor.summary([("measure", 0.0, 0.1)])
        self.assertAlmostEqual(summary["event_loop_lag_max_s"], 0.015)
        monitor.samples = [(-0.005, 0.005, 0.0)]
        self.assertEqual(monitor.summary([("measure", 0.0, 0.1)])["event_loop_lag_max_s"], 0)

    def test_window_end_clips_stall_and_overlapping_windows_do_not_double_count(self):
        monitor = LagMonitor()
        monitor.samples = [(0.095, 0.19, 0.085)]
        summary = monitor.summary([("first", 0, 0.11), ("overlap", 0.1, 0.11)])
        self.assertAlmostEqual(summary["event_loop_lag_max_s"], 0.005)
        self.assertEqual(summary["samples"], 1)


class ObservedBoundaryLagTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_stall_after_pre_window_sleep_exceeds_integrity_gate(self):
        monitor = LagMonitor()
        monitor.start()
        try:
            await asyncio.sleep(0)  # Start the monitor's sleep before the window.
            start = time.monotonic()
            time.sleep(0.08)  # A real client stall, not an invented lag sample.
            await asyncio.sleep(0.08)
            end = time.monotonic()
        finally:
            await monitor.stop()
        summary = monitor.summary([("measure", start, end)])
        self.assertGreater(summary["event_loop_lag_max_s"], 0.05)
        self.assertGreater(summary["samples"], 1)


@unittest.skipUnless(sys.platform == "linux", "Linux process groups and /proc required")
class FullCaptureShutdownTests(unittest.TestCase):
    def setup_fake_docker(self, root):
        docker = root / "docker"
        docker.write_text(
            "#!"
            + sys.executable
            + "\n"
            + r"""
import json,os,signal,sys,time
from pathlib import Path
r=Path(os.environ['FAKE_ROOT']);a=sys.argv[1:];state=r/'engine.json'
if a[0]=='run':
 (r/'client.pid').write_text(str(os.getpid()))
 (r/'supervisor.pid').write_text(str(os.getppid()))
 def stopped(*unused):sys.exit(0)
 signal.signal(signal.SIGTERM,stopped)
 (r/'ready').write_text('ready')
 while not (r/'allow-create').exists():time.sleep(.01)
 state.write_text(json.dumps({'Id':'f'*64,'Name':'/abstract-t1-'+'d'*32,
 'Image':'sha256:'+'e'*64,'Config':{'Image':'sha256:'+'e'*64,
 'Labels':{'abstract.task1.capture':'d'*32}}}))
 while True:time.sleep(.02)
elif a[0]=='inspect':
 time.sleep(1.2)
 if not state.exists():print('No such object',file=sys.stderr);sys.exit(1)
 print('['+state.read_text()+']')
elif a[:2]==['rm','--force']:
 if a[2]!='f'*64:sys.exit(8)
 state.unlink()
elif a[:3]==['container','ls','-aq']:
 if state.exists():print('f'*64)
elif a[:2]==['container','inspect']:
 v=json.loads(state.read_text());print(json.dumps({'id':v['Id'],'name':v['Name'],
 'image_ref':v['Config']['Image'],'owner':v['Config']['Labels']['abstract.task1.capture']}))
elif a[:3]==['container','rm','-f']:
 if a[3]!='f'*64:sys.exit(8)
 state.unlink()
else:sys.exit(7)
""",
            encoding="utf-8",
        )
        docker.chmod(0o700)
        env = {
            **os.environ,
            "PATH": str(root) + os.pathsep + os.environ["PATH"],
            "FAKE_ROOT": str(root),
        }
        launch = [
            sys.executable,
            str(SUITE / "owned_docker.py"),
            "docker",
            "run",
            "--rm",
            "--pull=never",
            "--label",
            "abstract.task1.capture=" + OWNER,
            "--name",
            NAME,
            IMAGE,
        ]
        argv = [
            sys.executable,
            "-B",
            "-m",
            "runner",
            "--plan",
            str(SUITE / "fixtures/workload.mock.json"),
            "--out",
            str(root / "events"),
            "--base-url",
            "http://127.0.0.1:1",
            "--assume-mock",
            "--launch-cmd",
            shlex.join(launch),
            "--stop-after-launch",
        ]
        return env, argv

    def alive(self, pid):
        try:
            return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
        except FileNotFoundError:
            return False

    def wait_ready(self, root, proc):
        end = time.monotonic() + 5
        while not (root / "ready").exists() and time.monotonic() < end:
            if proc.poll() is not None:
                self.fail("outer capture exited before the client launched")
            time.sleep(0.01)
        self.assertTrue((root / "ready").exists(), "fake client did not launch")

    def assert_reaped_before_absence(self, root, env):
        # Check absence before allowing the pending create to complete, exactly
        # the ordering that could falsely report success in the broken chain.
        result = cleanup_container(
            name=NAME, owner=OWNER, image_id=IMAGE, deadline=time.monotonic() + 2, env=env
        )
        self.assertTrue(result["owned_container_absent"])
        (root / "allow-create").touch()
        time.sleep(0.15)
        for name in ("supervisor", "client"):
            pid = int((root / (name + ".pid")).read_text())
            self.assertFalse(self.alive(pid), name + " survived outer capture termination")
        self.assertFalse((root / "engine.json").exists(), "engine appeared after absence check")
        record = json.loads((root / "events/run.json").read_text())
        self.assertEqual(record["status"], "aborted")

    def stop_test_processes(self, root):
        # Test-owned exact process groups only, including on an expected repro failure.
        for name in ("supervisor", "client"):
            path = root / (name + ".pid")
            if path.exists():
                try:
                    os.killpg(int(path.read_text()), signal.SIGTERM)
                except ProcessLookupError:
                    pass
        time.sleep(0.1)
        for name in ("supervisor", "client"):
            path = root / (name + ".pid")
            if path.exists():
                try:
                    os.killpg(int(path.read_text()), signal.SIGKILL)
                except ProcessLookupError:
                    pass

    def test_outer_deadline_reaps_separate_supervisor_before_absence_check(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env, argv = self.setup_fake_docker(root)
            try:
                with self.assertRaisesRegex(CaptureError, "stage deadline"):
                    bounded_command(
                        argv,
                        deadline=time.monotonic() + 13.7,
                        log=root / "runner.log",
                        env=env,
                        shutdown_grace_s=12,
                    )
                self.assertTrue((root / "ready").exists())
                self.assert_reaped_before_absence(root, env)
            finally:
                self.stop_test_processes(root)

    def test_outer_sigterm_reaps_separate_supervisor_before_absence_check(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env, argv = self.setup_fake_docker(root)
            # This is the capture CLI's real bounded-command and interrupt path,
            # with GPU/model preconditions excluded from this CPU-only regression.
            worker = root / "outer.py"
            worker.write_text(
                "import signal,sys,time\n"
                f"sys.path.insert(0,{str(SUITE)!r})\n"
                "from pathlib import Path\n"
                "from capture import CaptureError,bounded_command\n"
                "def interrupted(*args):raise CaptureError('cancelled')\n"
                "signal.signal(signal.SIGTERM,interrupted)\n"
                "try:\n"
                f" bounded_command({argv!r},deadline=time.monotonic()+30,"
                f"log=Path({str(root / 'runner.log')!r}),shutdown_grace_s=12)\n"
                "except CaptureError:pass\n",
                encoding="utf-8",
            )
            outer = subprocess.Popen([sys.executable, str(worker)], env=env)
            try:
                self.wait_ready(root, outer)
                outer.terminate()
                self.assertEqual(outer.wait(timeout=15), 0)
                self.assert_reaped_before_absence(root, env)
            finally:
                if outer.poll() is None:
                    outer.kill()
                    outer.wait()
                self.stop_test_processes(root)

    def test_outer_cleanup_refuses_foreign_state_without_removing_it(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            env, _ = self.setup_fake_docker(root)
            state = root / "engine.json"
            state.write_text(
                json.dumps(
                    {
                        "Id": IDENTIFIER,
                        "Name": "/" + NAME,
                        "Image": IMAGE,
                        "Config": {"Image": IMAGE, "Labels": {"abstract.task1.capture": "foreign"}},
                    }
                )
            )
            with self.assertRaisesRegex(CaptureError, "ownership mismatch"):
                cleanup_container(
                    name=NAME, owner=OWNER, image_id=IMAGE, deadline=time.monotonic() + 2, env=env
                )
            self.assertTrue(state.exists())


if __name__ == "__main__":
    unittest.main()
