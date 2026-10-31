"""End-to-end CLI behaviour, determinism, schema perturbations and committed artifacts."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from research.pipeline import (
    load_config,
    render_curve_csv,
    render_json,
    render_normalization_csv,
    run_pipeline,
)
from research.report import render_report
from research.schema import InputError

from .support import (
    DEMO_CASE,
    REPO_ROOT,
    STANDINS,
    TASK_DIR,
    CaseBuilder,
    node,
    quote_row,
    run_cli,
    run_demo,
)


class CliTests(unittest.TestCase):
    def test_cli_writes_three_files_and_manifest_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli("--case", str(DEMO_CASE), "--out", tmp)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            names = sorted(p.name for p in Path(tmp).iterdir())
            self.assertEqual(
                names, ["curve.csv", "normalization.csv", "run-manifest.json", "valuation.json"]
            )
            manifest = json.loads((Path(tmp) / "run-manifest.json").read_text())
            self.assertEqual(manifest["python"]["version"].count("."), 2)
            self.assertEqual(len(manifest["code"]["source_sha256"]), 64)
            roles = {f["role"]: f["sha256"] for f in manifest["inputs"]["files"]}
            self.assertEqual(set(roles), {"case", "quotes", "performance"})
            self.assertTrue(all(len(v) == 64 for v in roles.values()))
            self.assertEqual(manifest["network_access"], "none")
            self.assertIn("parameters", manifest)
            self.assertTrue(manifest["generated_at"].endswith("Z"))

    def test_source_date_epoch_pins_manifest_timestamp(self):
        env = dict(os.environ, SOURCE_DATE_EPOCH="1790000000")
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli("--case", str(DEMO_CASE), "--out", tmp, env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            manifest = json.loads((Path(tmp) / "run-manifest.json").read_text())
            self.assertEqual(manifest["generated_at"], "2026-09-21T14:13:20Z")
            self.assertEqual(manifest["generated_at_source"], "SOURCE_DATE_EPOCH")

    def test_malformed_input_exits_2(self):
        builder = CaseBuilder(self).set("q04", price="abc")
        proc = run_cli("--case", str(builder.write()), "--out", str(builder.dir / "out"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("q04", proc.stderr)

    def test_unknown_exclude_id_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = run_cli("--case", str(DEMO_CASE), "--out", tmp, "--exclude-quote", "q99")
            self.assertEqual(proc.returncode, 2)


class DeterminismTests(unittest.TestCase):
    def test_rerun_is_byte_identical_to_committed_outputs(self):
        results = run_demo()
        self.assertEqual(
            render_curve_csv(results), (TASK_DIR / "curve.csv").read_text(encoding="utf-8")
        )
        self.assertEqual(
            render_json(results), (TASK_DIR / "valuation.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            render_normalization_csv(results),
            (TASK_DIR / "normalization.csv").read_text(encoding="utf-8"),
        )

    def test_two_runs_identical(self):
        self.assertEqual(render_json(run_demo()), render_json(run_demo()))

    def test_committed_report_matches_rendered_report(self):
        rendered = render_report(
            json.loads((TASK_DIR / "valuation.json").read_text(encoding="utf-8"))
        )
        self.assertEqual(rendered, (TASK_DIR / "REPORT.md").read_text(encoding="utf-8"))

    def test_committed_manifest_hashes_match_inputs(self):
        manifest = json.loads((TASK_DIR / "run-manifest.json").read_text(encoding="utf-8"))
        results = run_demo()
        self.assertEqual(manifest["inputs"]["files"], results["inputs"]["files"])
        self.assertEqual(
            manifest["inputs"]["input_pack_digest"], results["inputs"]["input_pack_digest"]
        )

    def test_submission_manifest_points_at_existing_artifacts(self):
        sub = json.loads((TASK_DIR / "submission.json").read_text(encoding="utf-8"))
        for key in ("report", "curve", "reservation_valuation", "tests"):
            self.assertTrue((REPO_ROOT / sub["evidence"][key]).exists(), sub["evidence"][key])
        self.assertTrue((REPO_ROOT / sub["model"]["entrypoint"]).exists())
        self.assertEqual(
            sub["inputs"]["input_pack_digest"], run_demo()["inputs"]["input_pack_digest"]
        )
        self.assertNotIn("REPLACE_", json.dumps(sub))


class SchemaPerturbationTests(unittest.TestCase):
    def test_extra_quote_rows_and_columns_are_tolerated(self):
        builder = CaseBuilder(self).copy_quote("q05", "q12", price="4.40", notes_extra="hello")
        results = builder.run()
        self.assertEqual(quote_row(results, "q12")["unknown_columns"], {"notes_extra": "hello"})
        self.assertEqual(node(results, 3)["ask"], 4.4)

    def test_missing_optional_fields_become_unknown(self):
        results = (
            CaseBuilder(self)
            .set("q04", valid_until="", provider="", memory_gib="", source_ref="")
            .run()
        )
        row = quote_row(results, "q04")
        self.assertIsNone(row["valid_until"])
        self.assertIsNone(row["provider"])
        self.assertEqual(row["as_of_status"], "valid")
        self.assertFalse(row["curve_eligible"])
        self.assertEqual(row["tier"], "different_product")

    def test_missing_required_field_is_explicit_error(self):
        with self.assertRaises(InputError) as ctx:
            CaseBuilder(self).set("q04", gpu="").run()
        self.assertIn("q04", str(ctx.exception))

    def test_unknown_kind_or_unit_rejected(self):
        with self.assertRaises(InputError):
            CaseBuilder(self).set("q04", unit="gpu_month").run()
        with self.assertRaises(InputError):
            CaseBuilder(self).set("q04", kind="rumour").run()

    def test_duplicate_ids_rejected(self):
        with self.assertRaises(InputError):
            CaseBuilder(self).copy_quote("q04", "q04").run()

    def test_unsupported_schema_version_rejected(self):
        builder = CaseBuilder(self)
        builder.case["schema_version"] = 2
        with self.assertRaises(InputError):
            builder.run()

    def test_window_not_month_aligned_is_not_a_node(self):
        results = CaseBuilder(self).set("q04", delivery_start="2026-11-05T00:00:00Z").run()
        row = quote_row(results, "q04")
        self.assertFalse(row["curve_eligible"])
        self.assertTrue(any("aligned" in r for r in row["reasons"]))

    def test_different_tenor_list_is_honoured(self):
        builder = CaseBuilder(self)
        builder.case["curve"]["tenor_months"] = [2, 4, 9]
        results = builder.run()
        self.assertEqual([n["tenor_months"] for n in results["curve"]["nodes"]], [2, 4, 9])
        self.assertEqual(node(results, 2)["status"], "implied")

    def test_standin_perturbation_fixtures_run(self):
        fixtures = sorted(STANDINS.glob("*/case.json"))
        self.assertTrue(fixtures, "no stand-in perturbation fixtures found")
        for case_path in fixtures:
            with self.subTest(fixture=case_path.parent.name):
                expected = json.loads(
                    (case_path.parent / "expected.json").read_text(encoding="utf-8")
                )
                results = run_pipeline(case_path, load_config(None))
                statuses = {str(n["tenor_months"]): n["status"] for n in results["curve"]["nodes"]}
                self.assertEqual(statuses, expected["statuses"])
                if "decision" in expected:
                    self.assertEqual(results["valuation"]["decision"], expected["decision"])
                if "excluded" in expected:
                    self.assertEqual(
                        sorted(e["id"] for e in results["exclusions"]["rows"]),
                        sorted(expected["excluded"]),
                    )
