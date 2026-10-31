"""Regression tests for the independent review findings (M-1..M-4, m-1..m-5)."""

import copy
import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from research.pipeline import load_config, validate_config
from research.schema import InputError

from .support import REPO_ROOT, TASK_DIR, CaseBuilder, node, quote_row, run_cli, run_demo


class HistoricalVariabilityScaleTests(unittest.TestCase):
    """M-1: expired x valid pairing must stay linear and the output bounded."""

    def test_thousands_of_rows_bounded_output_and_runtime(self):
        b = CaseBuilder(self)
        for i in range(1500):
            b.copy_quote(
                "q10", f"old{i}", price=str(4.0 + (i % 7) / 10), observed_at="2026-07-01T00:00:00Z"
            )
            b.copy_quote("q01", f"new{i}", price=str(5.0 + (i % 5) / 10))
        start = time.perf_counter()
        results = b.run()
        elapsed = time.perf_counter() - start
        hist = results["cross_checks"]["historical_variability"]
        self.assertEqual(hist["pairs"], 1501)
        self.assertLessEqual(len(hist["examples"]), hist["example_limit"])
        self.assertLess(len(json.dumps(hist)), 5000)
        self.assertLess(elapsed, 30.0)
        self.assertIsNotNone(hist["implied_vol_per_sqrt_month"]["median"])


class ZeroInputTests(unittest.TestCase):
    """M-2: schema-valid zeros must be named errors, and the CLI must never leak a traceback."""

    def test_zero_throughput_rejected(self):
        b = CaseBuilder(self)
        perf = (b.dir / "demo-performance.csv").read_text().replace("32.0,0.10", "0,0.10")
        (b.dir / "demo-performance.csv").write_text(perf)
        with self.assertRaises(InputError) as ctx:
            b.run()
        self.assertIn("output_tokens_per_s", str(ctx.exception))

    def test_zero_price_rejected_via_cli_exit_2(self):
        b = CaseBuilder(self).set("q07", price="0")
        proc = run_cli("--case", str(b.write()), "--out", str(b.dir / "out"))
        self.assertEqual(proc.returncode, 2)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("q07.price", proc.stderr)

    def test_unexpected_exception_becomes_exit_2_message(self):
        broken = TASK_DIR / "tests" / "_broken_config.json"
        cfg = load_config(None)
        cfg["scenarios"]["base"]["observed_side"] = "mid"
        cfg["anchors"]["base"] = {"kind": "fixed", "usd_per_gpu_hour": float("nan")}
        broken.write_text(json.dumps(cfg))
        self.addCleanup(broken.unlink)
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(
                "--case",
                str(TASK_DIR / "data" / "demo-case.json"),
                "--out",
                tmp,
                "--config",
                str(broken),
            )
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertNotIn("Traceback", proc.stderr)
        self.assertIn("error:", proc.stderr)


class ConfigValidationTests(unittest.TestCase):
    """M-3: every config section and key is validated with a named error."""

    def test_missing_each_section(self):
        for section in ("curve", "anchors", "scenarios", "valuation", "sensitivities"):
            cfg = copy.deepcopy(load_config(None))
            del cfg[section]
            with self.subTest(section=section), self.assertRaises(InputError) as ctx:
                validate_config(cfg)
            self.assertIn(section, str(ctx.exception))

    def test_missing_curve_key_and_bad_range(self):
        cfg = copy.deepcopy(load_config(None))
        del cfg["curve"]["horizon_vol_per_sqrt_month"]
        with self.assertRaises(InputError) as ctx:
            validate_config(cfg)
        self.assertIn("horizon_vol_per_sqrt_month", str(ctx.exception))
        cfg = copy.deepcopy(load_config(None))
        cfg["curve"]["one_sided_spread_fraction"] = -1
        with self.assertRaises(InputError):
            validate_config(cfg)
        cfg = copy.deepcopy(load_config(None))
        cfg["curve"]["reversion_band_per_month"] = [0.5, 0.1]
        with self.assertRaises(InputError):
            validate_config(cfg)

    def test_scenario_must_reference_anchor(self):
        cfg = copy.deepcopy(load_config(None))
        cfg["scenarios"]["base"]["anchor"] = "nope"
        with self.assertRaises(InputError) as ctx:
            validate_config(cfg)
        self.assertIn("scenarios.base", str(ctx.exception))

    def test_invalid_config_file_exits_2(self):
        bad = TASK_DIR / "tests" / "_bad_config.json"
        bad.write_text('{"config_version": 1}')
        self.addCleanup(bad.unlink)
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli(
                "--case",
                str(TASK_DIR / "data" / "demo-case.json"),
                "--out",
                tmp,
                "--config",
                str(bad),
            )
        self.assertEqual(proc.returncode, 2)
        self.assertIn("curve", proc.stderr)


class ProvenanceTests(unittest.TestCase):
    """M-4: the committed manifest and submission must agree with the checked-out code tree."""

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    def test_manifest_tree_hash_matches_head_research_tree(self):
        manifest = json.loads((TASK_DIR / "run-manifest.json").read_text(encoding="utf-8"))
        try:
            head_tree = self.git("rev-parse", "HEAD:tasks/compute-quant/research")
            dirty = self.git("status", "--porcelain", "--", "tasks/compute-quant/research")
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("git metadata unavailable")
        if dirty:
            self.skipTest(
                "research/ has uncommitted changes; tree comparison is meaningful only when clean"
            )
        self.assertEqual(manifest["code"]["git_tree_research"], head_tree)

    def test_submission_commit_is_full_sha_and_matches_manifest_tree(self):
        sub = json.loads((TASK_DIR / "submission.json").read_text(encoding="utf-8"))
        commit = sub["source"]["commit"]
        self.assertRegex(commit, r"^[0-9a-f]{40}$")
        manifest = json.loads((TASK_DIR / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(sub["source"]["research_tree_sha"], manifest["code"]["git_tree_research"])
        try:
            tree_at_commit = self.git("rev-parse", f"{commit}:tasks/compute-quant/research")
        except (OSError, subprocess.CalledProcessError):
            self.skipTest("commit not available in this checkout")
        self.assertEqual(tree_at_commit, manifest["code"]["git_tree_research"])


class MinorFindingsTests(unittest.TestCase):
    def test_crossed_market_is_not_an_observation_and_does_not_flip_the_decision(self):
        r = CaseBuilder(self).set("q06", price="4.70").run()
        n = node(r, 3)
        self.assertNotEqual(n["status"], "observed")
        self.assertIn("crossed-market", n["uncertainty_type"])
        self.assertLessEqual(n["usd_per_gpu_hour_low"], 4.5)
        self.assertGreaterEqual(n["usd_per_gpu_hour_high"], 4.7)
        self.assertTrue(any("crossed" in w for w in r["curve"]["warnings"]))
        self.assertNotIn(3, r["curve"]["reversion"]["observed_forward_tenors"])
        wild = CaseBuilder(self).set("q06", price="9.00").run()
        self.assertEqual(wild["valuation"]["decision"], run_demo()["valuation"]["decision"])
        self.assertLess(wild["valuation"]["buyer_mark"]["default_and_sla_adjusted_usd"], 0)

    def test_sla_above_100_rejected_everywhere(self):
        with self.assertRaises(InputError):
            CaseBuilder(self).set("q04", sla_pct="150").run()
        b = CaseBuilder(self)
        b.case["reference_product"]["sla_pct"] = 101
        with self.assertRaises(InputError):
            b.run()
        b = CaseBuilder(self)
        b.case["reservation"]["sla_pct"] = 120
        with self.assertRaises(InputError):
            b.run()

    def test_unknown_case_keys_reported(self):
        b = CaseBuilder(self)
        b.case["reservaton"] = {"typo": True}
        r = b.run()
        self.assertEqual(r["inputs"]["unknown_case_keys"], ["reservaton"])
        self.assertEqual(run_demo()["inputs"]["unknown_case_keys"], [])

    def test_duplicate_detection_is_fast_at_scale(self):
        b = CaseBuilder(self)
        for i in range(20000):
            b.copy_quote("q11", f"x{i}")
        b.copy_quote("q04", "q04")
        start = time.perf_counter()
        with self.assertRaises(InputError) as ctx:
            b.run()
        self.assertIn("q04", str(ctx.exception))
        self.assertLess(time.perf_counter() - start, 20.0)

    def test_bom_prefixed_csv_is_read(self):
        b = CaseBuilder(self)
        path = b.write()
        quotes = b.dir / "demo-quotes.csv"
        quotes.write_bytes(b"\xef\xbb\xbf" + quotes.read_bytes())
        from research.pipeline import run_pipeline

        r = run_pipeline(path, load_config(None))
        self.assertEqual(quote_row(r, "q01")["usd_per_gpu_hour"], 5.2)

    def test_quantity_factor_sensitivity_present(self):
        rows = run_demo()["sensitivities"]["quantity_factor_multiplier"]
        self.assertEqual([row["multiplier"] for row in rows], [0.9, 1.0, 1.1])
        mids = [row["anchor_mid_by_tenor"]["0"] for row in rows]
        self.assertEqual(mids, sorted(mids, reverse=True))
        self.assertEqual(len({row["curve_mid_by_tenor"]["3"] for row in rows}), 1)


class RoundThreeTests(unittest.TestCase):
    """Regression tests for the second independent review (R-1..R-7 and nits)."""

    def test_zero_fx_rate_rejected(self):
        b = CaseBuilder(self).set("q04", currency="EUR")
        b.case["fx_rates_to_usd"] = {"EUR": 0}
        with self.assertRaises(InputError) as ctx:
            b.run()
        self.assertIn("fx_rates_to_usd.EUR", str(ctx.exception))

    def test_non_positive_normalized_price_is_excluded_never_observed(self):
        r = CaseBuilder(self).set("q04", price="0.0000001").run()
        row = quote_row(r, "q04")
        self.assertFalse(row["curve_eligible"])
        self.assertIn("not positive", row["excluded_reason"])
        n = node(r, 1)
        self.assertNotEqual(n["status"], "observed")
        for n in r["curve"]["nodes"]:
            if n["usd_per_gpu_hour_mid"] is not None:
                self.assertGreater(n["usd_per_gpu_hour_mid"], 0)

    def test_other_zero_bounds(self):
        b = CaseBuilder(self)
        b.case["reservation"]["fixed_usd_per_gpu_hour"] = 0
        with self.assertRaises(InputError):
            b.run()
        b = CaseBuilder(self)
        b.case["reservation"]["expected_buyer_used_fraction"] = 1.5
        with self.assertRaises(InputError):
            b.run()
        r = CaseBuilder(self).set("q04", fee_usd_per_gpu_hour="0").run()
        self.assertEqual(quote_row(r, "q04")["usd_per_gpu_hour"], 4.9)

    def test_full_pipeline_scale_envelope(self):
        import resource

        b = CaseBuilder(self)
        rows = []
        for i in range(20000):
            src = ("q01", "q04", "q05", "q06", "q08", "q09", "q10", "q03")[i % 8]
            rows.append((src, f"s{i}", {"price": str(3 + (i % 50) / 10)}))
        for src, qid, extra in rows:
            b.copy_quote(src, qid, **extra)
        path = b.write()
        start = time.perf_counter()
        proc = run_cli("--case", str(path), "--out", str(b.dir / "out"))
        elapsed = time.perf_counter() - start
        self.assertEqual(proc.returncode, 0, proc.stderr[-2000:])
        size = (b.dir / "out" / "valuation.json").stat().st_size
        self.assertLess(size, 2_000_000)
        self.assertLess(elapsed, 60.0)
        peak_children_kb = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
        self.assertLess(peak_children_kb, 1_000_000)
        data = json.loads((b.dir / "out" / "valuation.json").read_text())
        self.assertEqual(data["quotes"]["count"], 20011)
        self.assertEqual(len(data["quotes"]["rows"]), data["quotes"]["row_limit"])
        xc = data["cross_checks"]
        self.assertLessEqual(len(xc["performance_translation"]["examples"]), 10)
        self.assertLessEqual(len(xc["spot"]["examples"]), 10)
        self.assertLessEqual(len(xc["usage_commitments"]["examples"]), 10)
        self.assertNotIn("comparable_same_class_prices", json.dumps(xc))
        lines = (b.dir / "out" / "normalization.csv").read_text().splitlines()
        self.assertEqual(len(lines), 20012)

    def test_unsupported_valuation_assumption_rejected(self):
        cfg = copy.deepcopy(load_config(None))
        cfg["valuation"]["day_count"] = "30/360"
        with self.assertRaises(InputError) as ctx:
            validate_config(cfg)
        self.assertIn("valuation.day_count", str(ctx.exception))
        cfg = copy.deepcopy(load_config(None))
        cfg["valuation"]["extra_assumption"] = "x"
        with self.assertRaises(InputError):
            validate_config(cfg)

    def test_non_finite_config_named_and_no_partial_outputs(self):
        cfg = copy.deepcopy(load_config(None))
        cfg["anchors"]["base"] = {"kind": "fixed", "usd_per_gpu_hour": float("nan")}
        with self.assertRaises(InputError) as ctx:
            validate_config(cfg)
        self.assertIn("anchors.base.usd_per_gpu_hour", str(ctx.exception))
        bad = TASK_DIR / "tests" / "_nan_config.json"
        bad.write_text(json.dumps(cfg))
        self.addCleanup(bad.unlink)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            proc = run_cli(
                "--case",
                str(TASK_DIR / "data" / "demo-case.json"),
                "--out",
                str(out),
                "--config",
                str(bad),
            )
            self.assertEqual(proc.returncode, 2)
            self.assertFalse(out.exists() and any(out.iterdir()))

    def test_write_outputs_is_atomic(self):
        from unittest import mock

        from research import pipeline

        results = run_demo()
        manifest = {"x": 1}
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "o"
            with mock.patch.object(pipeline, "render_json", side_effect=ValueError("boom")):
                with self.assertRaises(ValueError):
                    pipeline.write_outputs(results, out, manifest)
            self.assertFalse(out.exists() and any(out.iterdir()))
            pipeline.write_outputs(results, out, manifest)
            self.assertEqual(
                sorted(p.name for p in out.iterdir()),
                ["curve.csv", "normalization.csv", "run-manifest.json", "valuation.json"],
            )

    def test_historical_pairing_uses_nearest_dated_quote(self):
        b = CaseBuilder(self)
        b.copy_quote("q01", "early", price="4.00", observed_at="2026-06-20T00:00:00Z")
        b.copy_quote("q01", "late", price="6.00", observed_at="2026-09-30T00:00:00Z")
        r = b.run()
        hist = r["cross_checks"]["historical_variability"]
        ex = {e["expired_quote_id"]: e for e in hist["examples"]}
        self.assertEqual(ex["q10"]["current_quote_id"], "early")
        self.assertGreater(ex["q10"]["months_apart"], 0)
        self.assertEqual(hist["unmatched_expired_quotes"], 0)

    def test_quantity_multipliers_must_be_positive(self):
        for bad in (0, -1):
            cfg = copy.deepcopy(load_config(None))
            cfg["sensitivities"]["quantity_factor_multipliers"] = [bad, 1.0]
            with self.assertRaises(InputError) as ctx:
                validate_config(cfg)
            self.assertIn("quantity_factor_multipliers", str(ctx.exception))

    def test_unknown_config_and_nested_case_keys_are_reported(self):
        cfg = copy.deepcopy(load_config(None))
        cfg["curv"] = {}
        from research.pipeline import run_pipeline

        b = CaseBuilder(self)
        b.case["economics"]["pue_typo"] = 1.3
        b.case["reservation"]["gpu_cnt"] = 2
        r = run_pipeline(b.write(), cfg)
        self.assertEqual(r["inputs"]["unknown_config_keys"], ["curv"])
        self.assertEqual(
            r["inputs"]["unknown_nested_case_keys"], ["economics.pue_typo", "reservation.gpu_cnt"]
        )

    def test_nested_unknown_keys_at_every_level(self):
        from research.pipeline import run_pipeline

        cfg = copy.deepcopy(load_config(None))
        cfg["curve"]["one_sided_spread_fractoin"] = 0.2
        cfg["anchors"]["base"]["utilisation"] = 0.5
        cfg["scenarios"]["base"]["side"] = "mid"
        b = CaseBuilder(self)
        b.case["reference_product"]["gpu_memory"] = 288
        b.case["quote_common_terms"]["egress"] = "free"
        r = run_pipeline(b.write(), cfg)
        self.assertEqual(
            r["inputs"]["unknown_config_keys"],
            ["anchors.base.utilisation", "curve.one_sided_spread_fractoin", "scenarios.base.side"],
        )
        self.assertEqual(
            r["inputs"]["unknown_nested_case_keys"],
            ["quote_common_terms.egress", "reference_product.gpu_memory"],
        )
        self.assertEqual(run_demo()["inputs"]["unknown_config_keys"], [])
        self.assertEqual(run_demo()["inputs"]["unknown_nested_case_keys"], [])

    def test_empty_header_only_and_whitespace_quote_files_exit_2(self):
        for label, content in (
            ("empty", ""),
            ("header-only", "id,provider\n"),
            ("whitespace", "   \n\n"),
        ):
            b = CaseBuilder(self)
            path = b.write()
            (b.dir / "demo-quotes.csv").write_text(content)
            proc = run_cli("--case", str(path), "--out", str(b.dir / "out"))
            with self.subTest(label=label):
                self.assertEqual(proc.returncode, 2, proc.stderr)
                self.assertNotIn("Traceback", proc.stderr)
                self.assertIn("quote", proc.stderr)

    def test_evidence_ids_list_only_influencing_quotes(self):
        r = run_demo()
        self.assertEqual(node(r, 6)["evidence_ids"], ["q05", "q06"])
        self.assertEqual(node(r, 12)["evidence_ids"], ["q05", "q06"])
        self.assertEqual(node(r, 1)["evidence_ids"], ["q04"])
