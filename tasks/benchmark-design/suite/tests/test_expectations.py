"""Machine-checkable fixture expectations and quick/full plan minimums (round-2 F5/F7)."""

import json
import unittest
from pathlib import Path

from bench.plan import load_plan, resolve_mode
from fixtures.expectations import AMBIGUOUS_WITH, CONTROLS, EXPECTED, run_checks
from mock_server.faults import PROFILES

from tests.helpers import MOCK_PLAN_PATH, plan

SUITE = Path(__file__).resolve().parent.parent


class ExpectationTableTests(unittest.TestCase):
    def test_every_mock_profile_and_mutation_has_specific_checks(self) -> None:
        for name in (*PROFILES, "timing_invalid", "client_lag", "underpowered"):
            with self.subTest(profile=name):
                exp = EXPECTED[name]
                self.assertIn(exp["status"], ("valid", "invalid", "inconclusive"))
                self.assertTrue(exp["checks"], "every profile needs at least one named check")
                for check in exp["checks"]:
                    self.assertIn("kind", check)

    def test_checks_fail_on_a_no_op_report(self) -> None:
        """A report with no rejections and clean-like metrics must NOT satisfy fault profiles."""
        clean = json.loads(
            (SUITE.parent / "validation" / "reports" / "public" / "clean.report.json").read_text()
        )
        for name, exp in EXPECTED.items():
            if exp["status"] != "valid" or name in CONTROLS:
                continue
            with self.subTest(profile=name):
                results = run_checks(clean, clean, exp["checks"])
                self.assertFalse(
                    all(r["passed"] for r in results), [r["observed"] for r in results]
                )

    def test_checks_pass_on_their_own_public_fixture_report(self) -> None:
        reports_dir = SUITE.parent / "validation" / "reports" / "public"
        clean = json.loads((reports_dir / "clean.report.json").read_text())
        for name, exp in EXPECTED.items():
            path = reports_dir / f"{name}.report.json"
            if not path.is_file():
                continue
            with self.subTest(profile=name):
                report = json.loads(path.read_text())
                results = run_checks(report, clean if name != "clean" else None, exp["checks"])
                self.assertTrue(all(r["passed"] for r in results), [r["observed"] for r in results])


class PredicateSpecificityTests(unittest.TestCase):
    def test_fault_predicates_are_specific(self) -> None:
        """Every other public fixture that satisfies a profile's predicates must be declared."""
        reports_dir = SUITE.parent / "validation" / "reports" / "public"
        if not reports_dir.is_dir():
            self.skipTest("validation reports not built")
        reports = {
            p.name[: -len(".report.json")]: json.loads(p.read_text(encoding="utf-8"))
            for p in reports_dir.glob("*.report.json")
        }
        clean = reports["clean"]
        for name, exp in EXPECTED.items():
            if name not in reports or name in CONTROLS:
                continue
            with self.subTest(profile=name):
                others = sorted(
                    other
                    for other, rep in reports.items()
                    if other != name
                    and rep["status"] == exp["status"]
                    and all(
                        c["passed"]
                        for c in run_checks(rep, clean if other != "clean" else None, exp["checks"])
                    )
                )
                self.assertEqual(others, sorted(AMBIGUOUS_WITH.get(name, [])))


class ModeMinimumTests(unittest.TestCase):
    def test_every_mode_meets_the_evaluators_per_cell_minimum(self) -> None:
        for path in (SUITE / "workload.json", MOCK_PLAN_PATH):
            the_plan = load_plan(path)
            floor = the_plan["statistics"]["min_requests_per_cell"]
            for mode in the_plan["modes"]:
                for unit in resolve_mode(the_plan, mode):
                    with self.subTest(plan=path.name, mode=mode, unit=unit.cell_id):
                        self.assertGreaterEqual(unit.requests, floor)

    def test_requests_scale_cannot_starve_a_cell(self) -> None:
        the_plan = plan()
        the_plan["modes"]["fixture"]["requests_scale"] = 0.01
        floor = the_plan["statistics"]["min_requests_per_cell"]
        self.assertTrue(all(u.requests >= floor for u in resolve_mode(the_plan, "fixture")))


if __name__ == "__main__":
    unittest.main()
