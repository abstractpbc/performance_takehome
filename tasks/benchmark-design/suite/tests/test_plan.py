"""Plan validation, deterministic scheduling and quick/full budget arithmetic."""

import copy
import json
import statistics
import unittest
from pathlib import Path

from bench.arrivals import closed_loop_assignments, poisson_schedule
from bench.plan import (
    PlanError,
    estimate_mode,
    load_plan,
    prefill_deadline_s,
    resolve_mode,
    validate_plan,
)

from tests.helpers import MOCK_PLAN_PATH, PLAN_PATH


class PlanValidationTests(unittest.TestCase):
    def test_shipped_plans_validate(self) -> None:
        for path in (PLAN_PATH, MOCK_PLAN_PATH):
            plan = load_plan(path)
            self.assertEqual(plan["schema_version"], "1.0.0")
            self.assertEqual(plan["output_policy"]["temperature"], 0)
            self.assertTrue(plan["output_policy"]["stream"])

    def test_malformed_plan_rejected(self) -> None:
        plan = load_plan(PLAN_PATH)
        broken = copy.deepcopy(plan)
        broken["cells"][0]["prompt_source"] = "does_not_exist"
        with self.assertRaises(PlanError):
            validate_plan(broken)
        broken = copy.deepcopy(plan)
        broken["deadlines"]["decode_tiers"]["strict"]["deadline_s"] = -1
        with self.assertRaises(PlanError):
            validate_plan(broken)
        broken = copy.deepcopy(plan)
        del broken["modes"]["full"]["planned_wall_minutes_max"]
        with self.assertRaises(PlanError):
            validate_plan(broken)

    def test_required_cell_kinds_present(self) -> None:
        plan = load_plan(PLAN_PATH)
        sources = {c["prompt_source"] for c in plan["cells"]}
        self.assertTrue({"chat_short", "rag_long", "generation_long", "mixed"} <= sources)
        arrivals = {c["arrival"]["type"] for c in plan["cells"]}
        self.assertEqual(arrivals, {"poisson", "closed_loop"})
        roles = {c.get("role", "measure") for c in plan["cells"]}
        self.assertIn("aa_control", roles)
        self.assertIn("calibration", roles)


class BudgetArithmeticTests(unittest.TestCase):
    def test_submission_costs_match_the_current_plan(self) -> None:
        submission = json.loads(
            (PLAN_PATH.parents[1] / "submission.json").read_text(encoding="utf-8")
        )
        plan = load_plan(PLAN_PATH)
        for mode in ("quick", "full"):
            with self.subTest(mode=mode):
                estimate = estimate_mode(plan, mode)
                self.assertEqual(
                    submission["suite"]["planned_wall_minutes"][mode],
                    estimate["planned_wall_minutes"],
                )
                self.assertEqual(
                    submission["suite"]["planned_gpu_hours"][mode],
                    estimate["planned_gpu_hours"],
                )

    def test_full_mode_fits_three_hours_and_quick_is_cheaper(self) -> None:
        plan = load_plan(PLAN_PATH)
        quick, full = estimate_mode(plan, "quick"), estimate_mode(plan, "full")
        self.assertEqual(full["status"], "planning-arithmetic-unmeasured")
        self.assertLessEqual(full["planned_wall_minutes"], 180.0)
        self.assertTrue(full["fits_cap"])
        self.assertTrue(full["fits_suite_budget"])
        self.assertTrue(quick["fits_cap"])
        self.assertLess(quick["planned_wall_minutes"], full["planned_wall_minutes"])
        self.assertLess(quick["offered_requests"], full["offered_requests"])
        for est in (quick, full):
            gpus = plan["planning_assumptions"]["gpus_at_once"]
            self.assertAlmostEqual(
                est["planned_gpu_hours"],
                round(est["planned_wall_minutes"] / 60 * gpus, 2),
                places=2,
            )
            self.assertAlmostEqual(
                est["planned_wall_minutes"],
                est["raw_total_minutes"] * est["safety_margin"],
                delta=0.11,
            )

    def test_estimate_scales_with_requests(self) -> None:
        plan = load_plan(PLAN_PATH)
        bigger = copy.deepcopy(plan)
        bigger["modes"]["quick"]["requests_scale"] = plan["modes"]["quick"]["requests_scale"] * 4
        self.assertGreater(
            estimate_mode(bigger, "quick")["offered_requests"],
            estimate_mode(plan, "quick")["offered_requests"],
        )
        self.assertGreater(
            estimate_mode(bigger, "quick")["planned_wall_minutes"],
            estimate_mode(plan, "quick")["planned_wall_minutes"],
        )

    def test_dry_run_matches_plan_json_claims(self) -> None:
        plan = load_plan(PLAN_PATH)
        for mode in ("quick", "full"):
            est = estimate_mode(plan, mode)
            self.assertEqual(
                est["planned_wall_minutes_max"], plan["modes"][mode]["planned_wall_minutes_max"]
            )


class SchedulingTests(unittest.TestCase):
    def test_resolve_mode_is_deterministic_and_covers_cells(self) -> None:
        plan = load_plan(PLAN_PATH)
        a, b = resolve_mode(plan, "full"), resolve_mode(plan, "full")
        self.assertEqual([u.unit_id for u in a], [u.unit_id for u in b])
        self.assertEqual({u.cell_id for u in a}, set(plan["modes"]["full"]["cells"]))
        reps = {u.repetition for u in a}
        self.assertEqual(reps, set(range(plan["modes"]["full"]["repetitions"])))
        self.assertEqual(len({u.unit_id for u in a}), len(a))

    def test_poisson_schedule_seeded_and_rate_consistent(self) -> None:
        a, b = poisson_schedule(2.0, 2000, 7), poisson_schedule(2.0, 2000, 7)
        self.assertEqual(a, b)
        self.assertNotEqual(a, poisson_schedule(2.0, 2000, 8))
        gaps = [y.offset_s - x.offset_s for x, y in zip(a, a[1:], strict=False)]
        self.assertAlmostEqual(statistics.fmean(gaps), 0.5, delta=0.05)
        self.assertEqual(a[0].offset_s, 0.0)
        with self.assertRaises(ValueError):
            poisson_schedule(0.0, 10, 1)

    def test_closed_loop_assignments_partition_indices(self) -> None:
        workers = closed_loop_assignments(4, 10)
        self.assertEqual(len(workers), 4)
        self.assertEqual(sorted(i for w in workers for i in w), list(range(10)))

    def test_prefill_deadline_grows_with_prompt_length(self) -> None:
        plan = load_plan(PLAN_PATH)
        short, long_ = prefill_deadline_s(plan, 128), prefill_deadline_s(plan, 4096)
        self.assertGreater(long_, short)
        self.assertGreaterEqual(short, plan["deadlines"]["prefill"]["floor_s"])
        tiers = plan["deadlines"]["decode_tiers"]
        self.assertLess(tiers["strict"]["deadline_s"], tiers["medium"]["deadline_s"])
        self.assertLess(tiers["medium"]["deadline_s"], tiers["relaxed"]["deadline_s"])

    def test_startup_policy_waits_for_valid_generation(self) -> None:
        plan = json.loads(Path(PLAN_PATH).read_text(encoding="utf-8"))
        procedure = " ".join(plan["startup"]["procedure"]).lower()
        self.assertIn("readiness never stops the clock", procedure)
        self.assertIn("valid completed generation", procedure)


if __name__ == "__main__":
    unittest.main()
