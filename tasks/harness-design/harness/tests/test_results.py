"""results.tsv schema, outcome vocabulary and blank-vs-zero handling."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from abstract_harness import results

from . import _paths  # noqa: F401

EXPECTED_COLUMNS = (
    "ts", "experiment", "source_commit", "image_digest", "kept", "outcome",
    "local_ttft_speedup", "local_ftgr_speedup", "local_startup_s", "api_usd_cumulative",
    "gpu_hours_cumulative", "hypothesis", "notes", "development_report", "patch_path",
)  # fmt: skip
EXPECTED_OUTCOMES = {
    "baseline", "improved", "no_change", "regressed", "gate_failed", "error", "interrupted"
}  # fmt: skip


def _row(**overrides: str) -> results.ResultRow:
    base = {
        "ts": "2026-01-01T00:00:00Z",
        "experiment": "001-x",
        "source_commit": "81aead87d78ed468c4c374fb42921471b44c3f80",
        "image_digest": "sha256:" + "a" * 64,
        "kept": "0",
        "outcome": "no_change",
        "development_report": "reports/001-x.json",
    }
    base.update(overrides)
    return results.make_row(**base)


class ResultsSchemaTest(unittest.TestCase):
    def test_columns_match_contract_exactly(self) -> None:
        self.assertEqual(results.COLUMNS, EXPECTED_COLUMNS)
        self.assertEqual(results.HEADER_LINE, "\t".join(EXPECTED_COLUMNS) + "\n")

    def test_outcome_vocabulary_is_exact(self) -> None:
        self.assertEqual(set(results.OUTCOMES), EXPECTED_OUTCOMES)
        with self.assertRaises(results.ResultsError):
            _row(outcome="improved!").validate()
        with self.assertRaises(results.ResultsError):
            _row(outcome="Improved").validate()

    def test_starter_results_header_matches(self) -> None:
        header = (_paths.HARNESS_DIR / "results.tsv").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(header, "\t".join(EXPECTED_COLUMNS))

    def test_kept_only_zero_or_one_and_only_for_good_outcomes(self) -> None:
        with self.assertRaises(results.ResultsError):
            _row(kept="yes").validate()
        with self.assertRaises(results.ResultsError):
            _row(kept="1", outcome="regressed").validate()
        _row(
            kept="1", outcome="improved", local_ttft_speedup="1.21", local_ftgr_speedup="1.05"
        ).validate()

    def test_blank_metrics_are_preserved_not_zeroed(self) -> None:
        row = _row(outcome="error", image_digest="", development_report="reports/e.json")
        line = row.to_line()
        parsed = results.ResultRow.from_line(line)
        self.assertEqual(parsed.local_ttft_speedup, "")
        self.assertEqual(parsed.local_startup_s, "")
        self.assertEqual(parsed.api_usd_cumulative, "")
        self.assertEqual(parsed.image_digest, "")
        self.assertEqual(results.format_metric(None), "")
        self.assertEqual(results.format_metric(1.23456), "1.2346")

    def test_free_text_with_tabs_newlines_round_trips(self) -> None:
        row = _row(notes="line1\nline2\twith tab", hypothesis="a\\b")
        line = row.to_line()
        self.assertEqual(line.count("\n"), 1)
        self.assertEqual(len(line.rstrip("\n").split("\t")), len(EXPECTED_COLUMNS))
        parsed = results.ResultRow.from_line(line)
        self.assertEqual(results.unescape_field(parsed.notes), "line1\nline2\twith tab")
        self.assertEqual(results.unescape_field(parsed.hypothesis), "a\\b")
        with self.assertRaises(results.ResultsError):
            results.ResultRow(**{**parsed.__dict__, "notes": "raw\ttab"}).validate()

    def test_append_and_validate_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.tsv"
            results.ensure_header(path)
            results.append_row(path, _row(experiment="baseline", outcome="baseline", kept="1"))
            results.append_row(path, _row(outcome="gate_failed"))
            rows = results.validate_file(path)
            self.assertEqual([r.outcome for r in rows], ["baseline", "gate_failed"])
            path.write_text(path.read_text().replace("gate_failed", "bogus"))
            with self.assertRaises(results.ResultsError):
                results.validate_file(path)

    def test_wrong_header_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "results.tsv"
            path.write_text("ts\texperiment\n", encoding="utf-8")
            with self.assertRaises(results.ResultsError):
                results.ensure_header(path)


if __name__ == "__main__":
    unittest.main()


class TornTailTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "results.tsv"
        results.append_row(
            self.path,
            results.make_row(
                ts="2026-10-24T00:00:00Z",
                experiment="baseline",
                source_commit="abc",
                image_digest="sha256:" + "0" * 64,
                kept="1",
                outcome="baseline",
                local_startup_s="1.0",
                api_usd_cumulative="0.00",
                gpu_hours_cumulative="0.000",
                development_report="reports/000-baseline.json",
            ),
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _row(self, experiment: str) -> results.ResultRow:
        return results.make_row(
            ts="2026-10-24T00:01:00Z",
            experiment=experiment,
            source_commit="abc",
            image_digest="",
            kept="0",
            outcome="error",
            api_usd_cumulative="0.00",
            gpu_hours_cumulative="0.000",
            notes="x",
        )

    def test_partial_last_line_is_rejected_by_validate(self) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write("2026-10-24T00:00:30Z\t001-torn\tabc\tsha256:")
        with self.assertRaises(results.ResultsError) as ctx:
            results.validate_file(self.path)
        self.assertIn("truncated last line", str(ctx.exception))

    def test_append_repairs_partial_last_line_and_keeps_fragment(self) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write("2026-10-24T00:00:30Z\t001-torn\tabc\tsha256:")
        results.append_row(self.path, self._row("002-next"))
        rows = results.validate_file(self.path)
        self.assertEqual([r.experiment for r in rows], ["baseline", "002-next"])
        torn = self.path.with_suffix(".tsv.torn").read_text("utf-8")
        self.assertIn("001-torn", torn)

    def test_intact_file_is_left_alone(self) -> None:
        before = self.path.read_bytes()
        self.assertIsNone(results.repair_torn_tail(self.path))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse(self.path.with_suffix(".tsv.torn").exists())
