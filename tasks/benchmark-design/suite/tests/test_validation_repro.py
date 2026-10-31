"""The committed validation artifacts are reproduced by one documented command (round-2 F8/nit)."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TASK = Path(__file__).resolve().parents[2]
BUILD = TASK / "validation" / "build_validation.py"
COMPARED = ("discrimination.json",)


class ValidationReproducibilityTests(unittest.TestCase):
    def test_build_validation_reproduces_committed_tables(self) -> None:
        if not (TASK / "suite" / "fixtures" / "heldout").is_dir():
            self.skipTest("fixtures not generated")
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [sys.executable, str(BUILD), "--out", tmp],
                capture_output=True,
                text=True,
                timeout=600,
                env={
                    **(
                        {"SYSTEMROOT": os.environ["SYSTEMROOT"]}
                        if "SYSTEMROOT" in os.environ else {}
                    ),
                    "PATH": "",
                    "USERPROFILE": str(Path(tmp)),
                    "BENCHMARK_DESIGN_HELDOUT_KEY": str(Path(tmp) / "no-key"),
                },
            )
            self.assertIn(proc.returncode, (0, 1), proc.stderr[-800:])
            for name in COMPARED:
                with self.subTest(file=name):
                    self.assertTrue((Path(tmp) / name).is_file(), proc.stderr[-1500:])
                    fresh = json.loads((Path(tmp) / name).read_text(encoding="utf-8"))
                    committed = json.loads((TASK / "validation" / name).read_text(encoding="utf-8"))
                    self.assertEqual(fresh, committed)
            # Without the out-of-tree key the held-out controls cannot be identified, so only
            # the public control rows are compared here.
            fresh_ctl = json.loads((Path(tmp) / "controls.json").read_text(encoding="utf-8"))
            committed_ctl = json.loads(
                (TASK / "validation" / "controls.json").read_text(encoding="utf-8")
            )
            public = lambda rows: [r for r in rows if r["fixture"] in ("clean", "noisy")]  # noqa: E731
            self.assertEqual(public(fresh_ctl["controls"]), public(committed_ctl["controls"]))
            fresh_held = json.loads((Path(tmp) / "heldout_results.json").read_text())
            committed_held = json.loads((TASK / "validation" / "heldout_results.json").read_text())
            # Observed columns must match; the key-dependent column is absent without the key.
            strip = lambda rows: [  # noqa: E731
                {k: v for k, v in r.items() if k != "checks_passed"} for r in rows
            ]
            self.assertEqual(strip(fresh_held["rows"]), strip(committed_held["rows"]))


class ShippedPlanExecutedTests(unittest.TestCase):
    def test_quick_mode_of_the_shipped_plan_ran_end_to_end_against_the_mock(self) -> None:
        d = TASK / "validation" / "quick_mock_run"
        if not (d / "report.json").is_file():
            self.skipTest("quick_mock_run artefact not present")
        report = json.loads((d / "report.json").read_text(encoding="utf-8"))
        run = json.loads((d / "run.json").read_text(encoding="utf-8"))
        plan = json.loads((TASK / "suite" / "workload.json").read_text(encoding="utf-8"))
        self.assertEqual(run["plan"]["plan_id"], plan["plan_id"])
        self.assertEqual(run["mode"], "quick")
        self.assertEqual(report["status"], "valid", report["reasons"])
        totals = report["population"]["totals"]
        self.assertEqual(totals["offered"], totals["completed"])
        self.assertGreaterEqual(totals["offered"], 242)


if __name__ == "__main__":
    unittest.main()
