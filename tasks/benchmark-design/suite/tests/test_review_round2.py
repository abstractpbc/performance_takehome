"""Round-2 review items: malformed records, bounded reader under a memory cap, corrupt gzip."""

import contextlib
import gzip
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    import resource
except ImportError:  # Windows has no POSIX resource module.
    resource = None

import evaluate as ev_mod
from bench.records import MAX_RECORD_BYTES, EvidenceError, load_events

from tests.helpers import MOCK_PLAN_PATH, clean_cell, make_run, plan, write_events

SUITE = Path(__file__).resolve().parent.parent


class MalformedRecordFuzzTests(unittest.TestCase):
    def test_deleting_each_required_key_yields_rc4_and_a_report(self) -> None:
        the_plan = plan()
        for key in ev_mod.Evaluator.REQUIRED_KEYS:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as tmp:
                records = clean_cell(the_plan, "chat_short_open", 12)
                run = make_run(records)
                del records[3][key]
                d = write_events(Path(tmp) / "ev", run, records)
                out = Path(tmp) / "report.json"
                with contextlib.redirect_stdout(io.StringIO()):
                    rc = ev_mod.main(
                        ["--events", str(d), "--workload", str(MOCK_PLAN_PATH), "--out", str(out)]
                    )
                self.assertEqual(rc, 4)
                report = json.loads(out.read_text(encoding="utf-8"))
                self.assertEqual(report["status"], "invalid")
                self.assertTrue(
                    any("malformed" in r or "schema" in r for r in report["reasons"]),
                    report["reasons"],
                )

    def test_evaluator_crash_is_an_invalid_verdict_not_a_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.json"
            with contextlib.redirect_stdout(io.StringIO()):
                rc = ev_mod.main(
                    [
                        "--events",
                        tmp,
                        "--workload",
                        str(
                            Path(tmp) / "missing.json",
                        ),
                        "--out",
                        str(out),
                    ]
                )
            self.assertEqual(rc, 4)
            self.assertEqual(json.loads(out.read_text())["status"], "invalid")


class BoundedReaderTests(unittest.TestCase):
    def test_corrupt_gzip_is_a_named_evidence_error(self) -> None:
        the_plan = plan()
        records = clean_cell(the_plan, "chat_short_open", 2)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "run.json").write_text(json.dumps(make_run(records)), encoding="utf-8")
            good = gzip.compress(("\n".join(json.dumps(r) for r in records) + "\n").encode())
            (Path(tmp) / "requests.jsonl.gz").write_bytes(
                good[: len(good) // 2]
            )  # truncated member
            with self.assertRaises(EvidenceError) as ctx:
                load_events(Path(tmp))
            self.assertIn("corrupt", str(ctx.exception).lower() + "unreadable")
            (Path(tmp) / "requests.jsonl.gz").write_bytes(b"not gzip at all")
            with self.assertRaises(EvidenceError):
                load_events(Path(tmp))
            report = ev_mod.evaluate(the_plan, Path(tmp), "native")[0]
            self.assertEqual(report["status"], "invalid")

    @unittest.skipUnless(
        sys.platform.startswith("linux"), "requires Linux resource limits/RSS units"
    )
    def test_single_line_gzip_bomb_fails_fast_under_memory_cap(self) -> None:
        """A 600 MB single line (0.6 MB gzip) must raise with < 512 MB address space."""
        with tempfile.TemporaryDirectory() as tmp:
            bomb = Path(tmp) / "requests.jsonl.gz"
            with gzip.open(bomb, "wb", compresslevel=9) as fh:
                chunk = b" " * (1 << 20)
                for _ in range(600):
                    fh.write(chunk)
            (Path(tmp) / "run.json").write_text("{}", encoding="utf-8")
            code = "\n".join(
                [
                    "import resource, sys",
                    f"sys.path.insert(0, {str(SUITE)!r})",
                    "resource.setrlimit(resource.RLIMIT_AS, (512 << 20, 512 << 20))",
                    "from pathlib import Path",
                    "from bench.records import _iter_bounded_lines, EvidenceError",
                    f"p = Path({str(bomb)!r})",
                    "try:",
                    "    for _ in _iter_bounded_lines(p): pass",
                    "except EvidenceError as exc:",
                    "    print('EVIDENCE_ERROR', exc); sys.exit(0)",
                    "sys.exit(1)",
                ]
            )
            proc = subprocess.run(
                [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
            )
            self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
            self.assertIn("EVIDENCE_ERROR", proc.stdout)
            self.assertIn(str(MAX_RECORD_BYTES), proc.stdout)
            self.assertNotIn("MemoryError", proc.stderr)
            usage = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss  # KiB on Linux
            self.assertLess(usage, 400 << 10, f"child peak RSS {usage} KiB")


if __name__ == "__main__":
    unittest.main()
