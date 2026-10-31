"""Timeouts kill the whole process group (hung engine / build / proposer)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from abstract_harness import procs

from . import _paths  # noqa: F401


class ProcessGroupTest(unittest.TestCase):
    def _orphaned_descendant(self, *, log_output: bool) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pidfile = root / "pid"
            script = (
                "import os, signal, time\n"
                "from pathlib import Path\n"
                "pid = os.fork()\n"
                "if pid == 0:\n"
                "    signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                f"    Path({str(pidfile.with_suffix('.tmp'))!r}).write_text(str(os.getpid()))\n"
                f"    Path({str(pidfile.with_suffix('.tmp'))!r}).replace({str(pidfile)!r})\n"
                "    time.sleep(120)\n"
                "else:\n"
                f"    while not Path({str(pidfile)!r}).exists(): time.sleep(0.01)\n"
                "    os._exit(0)\n"
            )
            grandchild = None
            try:
                start = time.monotonic()
                result = procs.run(
                    [sys.executable, "-c", script],
                    timeout_s=3.0,
                    log_path=root / "child.log" if log_output else None,
                )
                self.assertLess(time.monotonic() - start, 10.0)
                grandchild = int(pidfile.read_text())
                stat = Path(f"/proc/{grandchild}/stat")
                # An adopted zombie may await init's reap, but cannot run or hold pipes.
                self.assertTrue(not stat.exists() or stat.read_text().split()[2] == "Z")
                if log_output:
                    self.assertTrue(result.ok)
                else:
                    self.assertTrue(result.timed_out)
            finally:
                if grandchild is None and pidfile.exists():
                    grandchild = int(pidfile.read_text())
                if grandchild is not None:
                    try:
                        os.kill(grandchild, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_exited_parent_with_inherited_pipes_does_not_leave_child_running(self) -> None:
        self._orphaned_descendant(log_output=False)

    def test_successful_logged_cli_does_not_leave_background_child_running(self) -> None:
        self._orphaned_descendant(log_output=True)

    def test_timeout_kills_child_and_grandchild(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pidfile = Path(tmp) / "pid"
            script = f"sleep 300 & echo $! > {pidfile}; wait"
            t0 = time.monotonic()
            result = procs.run(["bash", "-c", script], timeout_s=1.0)
            self.assertTrue(result.timed_out)
            self.assertFalse(result.ok)
            self.assertLess(time.monotonic() - t0, 10)
            grandchild = int(pidfile.read_text().strip())
            time.sleep(0.2)
            with self.assertRaises(ProcessLookupError):
                os.kill(grandchild, 0)

    def test_successful_run_captures_output_and_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "run.log"
            result = procs.run(
                ["bash", "-c", "echo out; echo err 1>&2"], timeout_s=10, log_path=log
            )
            self.assertTrue(result.ok)
            self.assertEqual(result.returncode, 0)
            self.assertIn("out", result.stdout + log.read_text())
            self.assertFalse(result.timed_out)

    def test_nonzero_exit_is_not_ok(self) -> None:
        result = procs.run(["bash", "-c", "exit 3"], timeout_s=10)
        self.assertEqual(result.returncode, 3)
        self.assertFalse(result.ok)

    def test_separate_stderr_retains_both_streams_after_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stdout, stderr = Path(tmp) / "stdout.jsonl", Path(tmp) / "stderr.log"
            result = procs.run(
                [
                    sys.executable,
                    "-u",
                    "-c",
                    'import sys,time; print("{} "); print("diagnostic", file=sys.stderr); '
                    "time.sleep(30)",
                ],
                timeout_s=0.2,
                log_path=stdout,
                stderr_path=stderr,
            )
            self.assertTrue(result.timed_out)
            self.assertEqual(stdout.read_text().strip(), "{}")
            self.assertEqual(stderr.read_text().strip(), "diagnostic")
            self.assertEqual(result.stderr.strip(), "diagnostic")

    def test_kill_process_group_on_already_dead_process_is_safe(self) -> None:
        proc = subprocess.Popen(["true"], start_new_session=True)
        proc.wait()
        procs.kill_process_group(proc)


if __name__ == "__main__":
    unittest.main()


class StateDirLockTest(unittest.TestCase):
    def test_second_instance_is_refused_until_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "harness.lock"
            first = procs.acquire_lock(lock)
            try:
                with self.assertRaises(procs.LockError) as ctx:
                    procs.acquire_lock(lock)
                self.assertIn(str(os.getpid()), str(ctx.exception))
            finally:
                first.close()
            second = procs.acquire_lock(lock)
            second.close()


class QuietHostTest(unittest.TestCase):
    def test_waits_for_stable_idle_then_returns(self) -> None:
        readings = iter([0.2, 0.9, 0.5, 0.9, 0.95, 0.99, 0.99])
        out = procs.wait_for_quiet_host(
            min_idle=0.85,
            stable_samples=3,
            max_wait_s=60,
            sample_s=0,
            sampler=lambda _s: next(readings),
        )
        self.assertTrue(out["quiet"])
        self.assertEqual(out["idle"], 0.99)

    def test_gives_up_at_max_wait_and_says_so(self) -> None:
        out = procs.wait_for_quiet_host(
            min_idle=0.85, stable_samples=3, max_wait_s=0.0, sample_s=0, sampler=lambda _s: 0.1
        )
        self.assertFalse(out["quiet"])

    def test_unavailable_sampler_does_not_block(self) -> None:
        out = procs.wait_for_quiet_host(sample_s=0, sampler=lambda _s: None)
        self.assertIsNone(out["quiet"], "unknown must not be recorded as quiet")
        self.assertIsNone(out["idle"])
        self.assertIn("unavailable", str(out["reason"]))

    def test_real_sampler_reads_proc_stat(self) -> None:
        idle = procs.cpu_idle_fraction(0.2)
        if idle is not None:
            self.assertGreaterEqual(idle, 0.0)
            self.assertLessEqual(idle, 1.0)
