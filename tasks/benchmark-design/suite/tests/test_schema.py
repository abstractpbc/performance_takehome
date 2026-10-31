"""Schema validation of request/run records and the evidence loader."""

import copy
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from bench.records import EvidenceError, load_events, validate_request_record, validate_run_record

from tests.helpers import make_record, make_run, plan, write_events


class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = plan()
        self.record = make_record(self.plan)
        self.run = make_run([self.record])

    def test_valid_records_pass(self) -> None:
        self.assertEqual(validate_request_record(self.record), [])
        self.assertEqual(validate_run_record(self.run), [])

    def test_missing_and_mistyped_fields_fail(self) -> None:
        broken = copy.deepcopy(self.record)
        del broken["events"]
        self.assertTrue(any("events" in e for e in validate_request_record(broken)))
        broken = copy.deepcopy(self.record)
        broken["timestamps_mono_s"]["send"] = "100"
        self.assertTrue(any("send" in e for e in validate_request_record(broken)))
        broken = copy.deepcopy(self.record)
        broken["schema_version"] = "0.9"
        self.assertTrue(any("schema_version" in e for e in validate_request_record(broken)))
        broken = copy.deepcopy(self.record)
        broken["events"][0] = {"t": 1.0}
        self.assertTrue(any("raw" in e for e in validate_request_record(broken)))
        broken_run = copy.deepcopy(self.run)
        broken_run["clock"]["source"] = "time.time"
        self.assertTrue(validate_run_record(broken_run))

    def test_loader_requires_both_files_and_unique_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            with self.assertRaises(EvidenceError):
                load_events(d)
            write_events(d, self.run, [self.record, self.record])
            ev = load_events(d)
            self.assertTrue(any("duplicate" in e for e in ev.schema_errors))
            with self.assertRaises(EvidenceError):
                load_events(d, adapter="organizer-v9")

    def test_loader_reads_gzip_and_ignores_other_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "run.json").write_text(json.dumps(self.run), encoding="utf-8")
            with gzip.open(d / "requests.jsonl.gz", "wt", encoding="utf-8") as fh:
                fh.write(json.dumps(self.record) + "\n")
            (d / "label.json").write_text('{"profile": "stall"}', encoding="utf-8")
            ev = load_events(d)
            self.assertEqual(len(ev.requests), 1)
            self.assertEqual(ev.schema_errors, [])


if __name__ == "__main__":
    unittest.main()
