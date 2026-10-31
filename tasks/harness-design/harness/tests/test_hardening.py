"""Reviewer-driven hardening: path containment, startup hard gate, budget bounds, validation."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from abstract_harness import budget, measure, procs, results
from abstract_harness.decision import decide, startup_hard_gate
from abstract_harness.engines import StandinEngine
from abstract_harness.proposers import (
    ProposerError,
    ScriptedProposer,
    apply_patch,
    contained_path,
    escaping_symlinks,
)

from . import _paths
from .test_proposers import _context
from .test_recovery import make_harness

BENCHMARK = _paths.HARNESS_DIR / "development-benchmark.json"


class PathContainmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ctx = root / "ctx"
        self.ctx.mkdir()
        (self.ctx / "Dockerfile").write_text("FROM scratch\nARG X=1\n", encoding="utf-8")
        (self.ctx / "sub").mkdir()
        (self.ctx / "sub" / "inner.txt").write_text("MAGIC hello\n", encoding="utf-8")
        self.host_file = root / "host.txt"
        self.host_file.write_text("MAGIC hello\n", encoding="utf-8")
        self.work = root / "work"
        self.work.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _queue(self, file: str) -> ScriptedProposer:
        return ScriptedProposer(
            [{"experiment": "x", "edits": [{"file": file, "find": "MAGIC", "replace": "PWNED"}]}]
        )

    def test_absolute_path_is_rejected_and_host_file_untouched(self) -> None:
        with self.assertRaises(ProposerError):
            self._queue(str(self.host_file)).propose(_context(self.ctx, self.work))
        self.assertEqual(self.host_file.read_text("utf-8"), "MAGIC hello\n")

    def test_dotdot_variants_are_rejected(self) -> None:
        for rel in ("../host.txt", "sub/../../host.txt", "sub/../../../etc/passwd"):
            with self.assertRaises(ProposerError, msg=rel):
                self._queue(rel).propose(_context(self.ctx, self.work))
        self.assertEqual(self.host_file.read_text("utf-8"), "MAGIC hello\n")

    def test_symlink_pointing_outside_is_rejected(self) -> None:
        (self.ctx / "escape.txt").symlink_to(self.host_file)
        with self.assertRaises(ProposerError):
            self._queue("escape.txt").propose(_context(self.ctx, self.work))
        self.assertEqual(self.host_file.read_text("utf-8"), "MAGIC hello\n")

    def test_symlinked_directory_is_rejected(self) -> None:
        (self.ctx / "outdir").symlink_to(self.host_file.parent)
        with self.assertRaises(ProposerError):
            self._queue("outdir/host.txt").propose(_context(self.ctx, self.work))
        self.assertEqual(self.host_file.read_text("utf-8"), "MAGIC hello\n")

    def test_case_variant_of_existing_file_does_not_bypass(self) -> None:
        with self.assertRaises(ProposerError):
            self._queue("DOCKERFILE").propose(_context(self.ctx, self.work))

    def test_contained_relative_file_is_accepted(self) -> None:
        proposal = self._queue("sub/inner.txt").propose(_context(self.ctx, self.work))
        assert proposal is not None
        self.assertIn("+PWNED hello", proposal.patch)
        self.assertEqual(contained_path(self.ctx, "sub/inner.txt"), self.ctx / "sub" / "inner.txt")

    def test_apply_patch_rejects_symlink_creation_escaping_context(self) -> None:
        patch = (
            "diff --git a/lnk b/lnk\n"
            "new file mode 120000\n"
            "--- /dev/null\n"
            "+++ b/lnk\n"
            "@@ -0,0 +1 @@\n"
            "+/etc\n"
            "\\ No newline at end of file\n"
        )
        ok, out = apply_patch(patch, self.ctx)
        # Either `patch` refuses to materialise the link or our post-check catches it.
        if ok:
            self.fail(f"escaping symlink accepted: {out}")
        self.assertEqual(
            escaping_symlinks(self.ctx), [] if not (self.ctx / "lnk").is_symlink() else ["lnk"]
        )

    def test_apply_patch_rejects_symlink_creation_inside_context(self) -> None:
        patch = (
            "diff --git a/lnk2 b/lnk2\n"
            "new file mode 120000\n"
            "--- /dev/null\n"
            "+++ b/lnk2\n"
            "@@ -0,0 +1 @@\n"
            "+Dockerfile\n"
            "\\ No newline at end of file\n"
        )
        ok, out = apply_patch(patch, self.ctx)
        self.assertFalse(ok, out)
        if (self.ctx / "lnk2").is_symlink():
            self.assertIn("symlinks inside the build context", out)

    def test_escaping_symlinks_detects_dangling_and_outside_links(self) -> None:
        (self.ctx / "a").symlink_to("/nonexistent/path")
        (self.ctx / "b").symlink_to(self.host_file)
        (self.ctx / "c").symlink_to(self.ctx / "Dockerfile")
        self.assertEqual(sorted(escaping_symlinks(self.ctx)), ["a", "b"])

    def test_queue_schema_errors_are_named(self) -> None:
        for bad in (
            [{"edits": [{"file": "Dockerfile", "find": "a", "replace": "b"}]}],
            [{"experiment": "x"}],
            [{"experiment": "x", "edits": [{"file": "Dockerfile", "find": "a"}]}],
            [{"experiment": "x", "edits": []}, {"experiment": "x", "patch": "p"}],
        ):
            with self.assertRaises(ProposerError, msg=str(bad)):
                ScriptedProposer(bad)


def _samples(ttft: list[float], ftgr: list[float], startup: float) -> dict:
    return {
        "ttft_s": {"p": ttft},
        "ftgr_tok_s": {"p": ftgr},
        "startup_s": {"startup": [startup]},
    }


class StartupHardGateTest(unittest.TestCase):
    def test_single_sample_100x_startup_regression_is_rejected_despite_ttft_win(self) -> None:
        base = _samples([0.040] * 10, [10.0] * 10, 0.7)
        cand = _samples([0.020] * 10, [20.0] * 10, 70.0)
        decision = decide(base, cand, gates_passed=True)
        self.assertEqual(decision.outcome, "regressed")
        self.assertFalse(decision.kept)
        self.assertIn("startup hard gate", decision.reason)

    def test_startup_within_limit_does_not_block_a_real_improvement(self) -> None:
        base = _samples([0.040] * 10, [10.0] * 10, 0.70)
        cand = _samples([0.020] * 10, [20.0] * 10, 0.95)
        decision = decide(base, cand, gates_passed=True)
        self.assertEqual(decision.outcome, "improved")

    def test_gate_helper_handles_missing_and_bad_samples(self) -> None:
        self.assertIsNone(startup_hard_gate({}, {"s": [1.0]}, 1.5))
        self.assertIsNone(startup_hard_gate({"s": [float("nan")]}, {"s": [1.0]}, 1.5))
        self.assertIsNone(startup_hard_gate({"s": [1.0]}, {"s": [1.4]}, 1.5))
        self.assertIsNotNone(startup_hard_gate({"s": [1.0]}, {"s": [1.6]}, 1.5))


class BudgetBoundTest(unittest.TestCase):
    def test_worst_case_from_timeouts(self) -> None:
        worst = budget.worst_case_attempt_s(
            propose_s=900, build_s=1800, startup_s=300, measure_s=900, stop_grace_s=10
        )
        self.assertEqual(worst, 900 + 1800 + 2 * (300 + 900 + 10))

    def test_stage_timeout_is_clamped_to_deadline(self) -> None:
        import time

        deadline = time.monotonic() + 5.0
        self.assertLessEqual(budget.stage_timeout(1800.0, deadline), 5.0)
        self.assertGreaterEqual(budget.stage_timeout(1800.0, time.monotonic() - 100), 1.0)

    def test_attempt_estimate_uses_observed_p90(self) -> None:
        self.assertEqual(budget.attempt_estimate_s(600.0, []), 600.0)
        self.assertEqual(budget.attempt_estimate_s(600.0, [100.0, 200.0]), 600.0)
        self.assertEqual(budget.attempt_estimate_s(60.0, [100.0, 200.0, 900.0, 150.0]), 900.0)

    def test_guard_refuses_when_estimate_exceeds_usable_window(self) -> None:
        cfg = budget.BudgetConfig(
            api_usd_limit=10.0,
            gpu_hours_limit=100.0,
            gpus_allocated=1,
            lease_start_ts=0.0,
            lease_deadline_ts=3600.0,
            final_reserve_s=600.0,
        )
        ok = budget.check_before_attempt(
            cfg, api_usd_cumulative=0.0, now_ts=0.0, estimated_attempt_s=2000.0
        )
        self.assertTrue(ok.ok)
        refused = budget.check_before_attempt(
            cfg, api_usd_cumulative=0.0, now_ts=0.0, estimated_attempt_s=3100.0
        )
        self.assertFalse(refused.ok)
        refused_usd = budget.check_before_attempt(
            cfg,
            api_usd_cumulative=9.5,
            now_ts=0.0,
            estimated_attempt_s=100.0,
            estimated_attempt_usd=1.0,
        )
        self.assertFalse(refused_usd.ok)


class ResultsValidationTest(unittest.TestCase):
    def _row(self, **over: str) -> results.ResultRow:
        base = dict(
            ts="2026-01-01T00:00:01Z",
            experiment="001-x",
            source_commit="abc",
            image_digest="sha256:" + "a" * 64,
            kept="0",
            outcome="no_change",
            local_ttft_speedup="1.0",
            local_ftgr_speedup="1.0",
            api_usd_cumulative="0.0",
            gpu_hours_cumulative="",
        )
        base.update(over)
        return results.make_row(**base)

    def test_improved_row_needs_a_speedup_above_one(self) -> None:
        with self.assertRaises(results.ResultsError):
            self._row(
                outcome="improved", local_ttft_speedup="0.5", local_ftgr_speedup="0.9"
            ).validate()
        self._row(
            outcome="improved", local_ttft_speedup="1.3", local_ftgr_speedup="0.98"
        ).validate()

    def test_non_finite_and_non_numeric_costs_are_named_errors(self) -> None:
        for value in ("nan", "inf", "-inf", "abc"):
            with self.assertRaises(results.ResultsError, msg=value):
                self._row(api_usd_cumulative=value).validate()

    def test_digest_must_be_64_hex(self) -> None:
        with self.assertRaises(results.ResultsError):
            self._row(image_digest="sha256:XYZ").validate()
        with self.assertRaises(results.ResultsError):
            self._row(ts="yesterday").validate()

    def test_file_rejects_duplicates_and_time_travel(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.tsv"
            baseline = self._row(
                ts="2026-01-01T00:00:00Z",
                experiment="baseline",
                kept="1",
                outcome="baseline",
                local_ttft_speedup="",
                local_ftgr_speedup="",
                development_report="reports/000.json",
            )
            results.append_row(path, baseline)
            results.append_row(path, self._row())
            results.validate_file(path)
            # exact duplicate line
            with path.open("a", encoding="utf-8") as fh:
                fh.write(self._row().to_line())
            with self.assertRaises(results.ResultsError):
                results.validate_file(path)
            # rebuild: same id, different content
            path.write_text(results.HEADER_LINE + baseline.to_line() + self._row().to_line())
            with path.open("a", encoding="utf-8") as fh:
                fh.write(self._row(outcome="error", local_ttft_speedup="").to_line())
            with self.assertRaises(results.ResultsError):
                results.validate_file(path)
            # timestamp before the previous row
            path.write_text(results.HEADER_LINE + baseline.to_line() + self._row().to_line())
            with path.open("a", encoding="utf-8") as fh:
                fh.write(self._row(experiment="002-y", ts="2025-12-31T23:59:59Z").to_line())
            with self.assertRaises(results.ResultsError):
                results.validate_file(path)

    def test_complete_last_row_without_newline_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "r.tsv"
            baseline = self._row(
                ts="2026-01-01T00:00:00Z",
                experiment="baseline",
                kept="1",
                outcome="baseline",
                local_ttft_speedup="",
                local_ftgr_speedup="",
                development_report="reports/000.json",
            )
            results.append_row(path, baseline)
            path.write_text(path.read_text("utf-8") + self._row().to_line().rstrip("\n"))
            logs: list[str] = []
            self.assertIsNone(results.repair_torn_tail(path, log=logs.append))
            self.assertEqual(
                [r.experiment for r in results.validate_file(path)], ["baseline", "001-x"]
            )
            self.assertFalse(path.with_suffix(".tsv.torn").exists())
            self.assertTrue(logs and "missing newline" in logs[0])


class BenchmarkValidationTest(unittest.TestCase):
    def _mutated(self, mutate) -> Path:
        data = json.loads(BENCHMARK.read_text("utf-8"))
        mutate(data)
        path = Path(self.tmp.name) / "b.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_bad_numbers_and_prompts_are_named_errors(self) -> None:
        cases = [
            lambda d: d.update(repetitions=0),
            lambda d: d.update(repetitions=-3),
            lambda d: d.update(repetitions="5"),
            lambda d: d.update(max_tokens=0),
            lambda d: d.update(request_timeout_s=-1),
            lambda d: d["prompts"][0].pop("prompt"),
            lambda d: d["prompts"][0].pop("id"),
            lambda d: d.update(startup_probe={}),
        ]
        for n, mutate in enumerate(cases):
            with self.assertRaises(measure.BenchmarkError, msg=str(n)):
                measure.load_benchmark(self._mutated(mutate))


class ManifestPinsTest(unittest.TestCase):
    def test_pi_pins_come_from_profiles_json_and_are_nonempty(self) -> None:
        from abstract_harness import manifest as manifest_mod

        pins = manifest_mod._pi_pins(_paths.REPO_ROOT)
        self.assertTrue(all(pins.values()), pins)
        self.assertTrue(pins["pi_integrity"].startswith("sha512-"), pins["pi_integrity"])
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "agent-harness"
            fake.mkdir()
            (fake / "profiles.json").write_text(
                json.dumps({"pi": {"package": "p", "version": "1"}}), encoding="utf-8"
            )
            with self.assertRaises(RuntimeError):
                manifest_mod._pi_pins(Path(tmp))

    def test_manifest_records_tree_hash_and_image_identity_kind(self) -> None:
        from abstract_harness import manifest as manifest_mod

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = make_harness(root)
            harness.engine = StandinEngine.create(_paths.REPO_ROOT, root, cpus=1)
            try:
                harness.state.harness_tree_sha256 = "a" * 64
                failed = root / "state/proposer/pi-attempt-failed/attempt-status.json"
                failed.parent.mkdir(parents=True)
                failed.write_text('{"status":"failed","accounting_resolved":false}')
                manifest = manifest_mod.write_manifest(harness)
            finally:
                harness._release_locks()
            self.assertEqual(manifest["source"]["harness_tree_sha256"], "a" * 64)
            self.assertEqual(manifest["evidence"]["agent_attempt_statuses"], [str(failed)])
            self.assertEqual(
                manifest["source"]["image_identity_kind"], manifest_mod.IMAGE_IDENTITY_KIND
            )
            prov = json.loads((root / "reports" / "build-provenance.json").read_text("utf-8"))
            self.assertEqual(prov["harness_tree_sha256"], "a" * 64)
            self.assertEqual(prov["image_identity_kind"], manifest_mod.IMAGE_IDENTITY_KIND)
            agent = json.loads((root / "reports" / "agent-provenance.json").read_text("utf-8"))
            self.assertTrue(agent["pi_integrity"])


class StartupGateMissingSampleTest(unittest.TestCase):
    def test_missing_or_nan_candidate_startup_fails_the_gate(self) -> None:
        base = {"startup": [1.0]}
        self.assertIn("no finite startup", startup_hard_gate(base, {}, 1.5) or "")
        self.assertIn(
            "no finite startup", startup_hard_gate(base, {"startup": [float("nan")]}, 1.5) or ""
        )
        self.assertIsNone(startup_hard_gate({}, {"startup": [1.0]}, 1.5))


class CrlfResultsTest(unittest.TestCase):
    def test_crlf_results_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.tsv"
            text = (_paths.HARNESS_DIR / "results.tsv").read_text(encoding="utf-8")
            path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
            with self.assertRaises(results.ResultsError):
                results.validate_file(path)


class EarlyLockTest(unittest.TestCase):
    def test_lock_files_survive_release_and_block_another_process(self) -> None:
        import subprocess
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = procs.results_lock_path(root / "results.tsv")
            lock.parent.mkdir(parents=True, exist_ok=True)
            holder = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import fcntl,sys,time;fh=open(sys.argv[1],'a+');"
                    "fcntl.flock(fh,fcntl.LOCK_EX|fcntl.LOCK_NB);print('held',flush=True);"
                    "time.sleep(30)",
                    str(lock),
                ],
                stdout=subprocess.PIPE,
                text=True,
            )
            try:
                self.assertEqual(holder.stdout.readline().strip(), "held")
                with self.assertRaises(procs.LockError):
                    make_harness(root)
            finally:
                holder.kill()
                holder.wait()
            harness = make_harness(root)
            harness._release_locks()
            self.assertTrue(lock.exists(), "lock files are never unlinked (unlink races)")
            self.assertTrue((root / "state" / "harness.lock").exists())

    def test_second_harness_on_same_paths_fails_before_touching_disk(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = make_harness(root)
            try:
                with self.assertRaises(procs.LockError):
                    make_harness(root)
            finally:
                first._release_locks()
            second = make_harness(root)
            second._release_locks()

    def test_results_path_is_locked_independently_of_state_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first = make_harness(root)
            try:
                lock = procs.results_lock_path(root / "results.tsv")
                self.assertTrue(lock.exists())
                self.assertEqual(lock.read_text("utf-8").strip(), str(os.getpid()))
                with self.assertRaises(procs.LockError):
                    procs.acquire_lock(lock)
            finally:
                first._release_locks()
