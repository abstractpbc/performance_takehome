"""Separate calibration, strict deadlines, owned cleanup and complete evidence export."""

import copy
import hashlib
import io
import json
import os
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import httpx
from audit_timing import audit
from bench.calibration import (
    CalibrationError,
    calibrate,
    fit_envelope,
    scoring_length_domain,
    write_frozen,
)
from bench.plan import estimate_mode, load_plan, plan_digest, resolve_mode
from bench.prompts import make_prompt
from bench.records import Evidence, RequestRecord, RunRecord
from bench.tokens import count_tokens
from capture import (
    CaptureError,
    bounded_command,
    effective_deadline,
    launch_arguments,
    owned_identity,
    pack_evidence,
    verify_export,
)
from evaluate import Evaluator
from runner.run import (
    MAX_STREAM_BYTES,
    MAX_STREAM_EVENTS,
    CaptureLimitError,
    RequestMeta,
    Runner,
    parse_args,
)

from tests.helpers import SUITE, make_record, make_run, plan
from tools.baseline import start_command


class FitTests(unittest.TestCase):
    def test_known_slope_and_observed_noise_envelope(self):
        points = [
            (x, 0.2 + 0.001 * x + noise)
            for x in (64, 256, 512, 1024, 4096)
            for noise in (0, 0.01, -0.01, 0, 0.01, -0.01)
        ]
        fit = fit_envelope(points)
        self.assertAlmostEqual(fit["slope_s_per_prompt_token"], 0.001)
        self.assertAlmostEqual(fit["intercept_s"], 0.21)
        self.assertTrue(fit["observed_envelope_covers_all_samples"])

    def test_rejects_sparse_nonfinite_or_degenerate_observations(self):
        for points in (
            [(1, 1.0)] * 30,
            [(x, 1.0) for x in range(5)],
            [(x, float("nan")) for x in range(30)],
            [(x, 1.0) for x in range(30)],
        ):
            with self.subTest(points=points[:2]), self.assertRaises(CalibrationError):
                fit_envelope(points)

    def test_negative_slope_cannot_produce_negative_deadlines(self):
        fit = fit_envelope([(x, 2 - x / 100) for x in range(1, 31)])
        self.assertEqual(fit["slope_s_per_prompt_token"], 0)
        self.assertGreater(fit["intercept_s"], 0)
        self.assertTrue(fit["observed_envelope_covers_all_samples"])


class CalibrationEvidenceTests(unittest.TestCase):
    def evidence(self, root):
        workload = copy.deepcopy(plan())
        workload["warmup"]["requests_per_cell"] = 0
        workload["cells"] = [
            cell for cell in workload["cells"] if cell["id"] != "prefill_calibration"
        ]
        workload["modes"]["calibration"] = {
            "startup_launches": 1,
            "repetitions": 1,
            "cells": ["prefill_calibration"],
            "planned_wall_minutes_max": 45,
        }
        workload["cells"].append(
            {
                "id": "prefill_calibration",
                "role": "calibration",
                "prompt_source": "chat_short",
                "arrival": {"type": "closed_loop", "concurrency": [1], "requests_per_level": [30]},
                "deadline_profile": "chat",
            }
        )
        path = root / "plan.json"
        path.write_text(json.dumps(workload))
        records = []
        for i in range(30):
            item = make_record(
                workload,
                request_id=f"prefill_calibration|conc=1|rep0/measure/{i}",
                cell_id="prefill_calibration",
                load_label="conc=1",
                role="calibration",
                index=i,
                ttft=0.1 + (i % 5) * 0.01,
            )
            item.update(arrival_type="closed_loop", load=1)
            item["prompt"]["messages"][0]["content"] = "x " * (5 + (i % 5) * 1100)
            item["prompt"]["approx_prompt_tokens"] = sum(
                count_tokens(message["content"]) + 4 for message in item["prompt"]["messages"]
            )
            records.append(item)
        run = make_run(records)
        run["mode"] = "calibration"
        run["planned_startup_launches"] = 1
        run["plan"]["digest"] = plan_digest(path)
        events = root / "events"
        events.mkdir()
        (events / "run.json").write_text(json.dumps(run))
        (events / "requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records))
        return path, events, run, records

    def test_freezes_separate_complete_baseline_with_new_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path, events, _, _ = self.evidence(root)
            fitted, receipt = calibrate(path, events, allow_mock=True)
            self.assertEqual(receipt["fit"]["samples"], 30)
            self.assertNotEqual(fitted["plan_id"], load_plan(path)["plan_id"])
            self.assertEqual(receipt["engine_kind"], "mock")
            write_frozen(fitted, receipt, root / "frozen.json", root / "receipt.json")
            saved = json.loads((root / "receipt.json").read_text())
            self.assertEqual(
                saved["output_plan_sha256"],
                hashlib.sha256((root / "frozen.json").read_bytes()).hexdigest(),
            )
            before = (root / "frozen.json").read_bytes()
            with self.assertRaises(CalibrationError):
                write_frozen(fitted, receipt, root / "frozen.json", root / "other.json")
            self.assertEqual((root / "frozen.json").read_bytes(), before)

    def test_refuses_mock_without_permission_and_missing_population(self):
        with tempfile.TemporaryDirectory() as temp:
            path, events, _, records = self.evidence(Path(temp))
            with self.assertRaises(CalibrationError):
                calibrate(path, events)
            (events / "requests.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in records[1:])
            )
            with self.assertRaises(CalibrationError):
                calibrate(path, events, allow_mock=True)

    def test_refuses_scoring_mode_and_forged_length(self):
        with tempfile.TemporaryDirectory() as temp:
            path, events, run, records = self.evidence(Path(temp))
            run["mode"] = "full"
            (events / "run.json").write_text(json.dumps(run))
            with self.assertRaises(CalibrationError):
                calibrate(path, events, allow_mock=True)
            run["mode"] = "calibration"
            (events / "run.json").write_text(json.dumps(run))
            records[0]["prompt"]["approx_prompt_tokens"] += 100
            (events / "requests.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in records)
            )
            with self.assertRaisesRegex(CalibrationError, "covariate"):
                calibrate(path, events, allow_mock=True)

    def test_refuses_a_curve_that_cannot_cover_planned_scoring_lengths(self):
        with tempfile.TemporaryDirectory() as temp:
            path, events, _, records = self.evidence(Path(temp))
            for index, item in enumerate(records):
                item["prompt"]["messages"][0]["content"] = "x " * (1000 + index % 5)
                item["prompt"]["approx_prompt_tokens"] = sum(
                    count_tokens(message["content"]) + 4 for message in item["prompt"]["messages"]
                )
            (events / "requests.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in records)
            )
            with self.assertRaisesRegex(CalibrationError, "cover all planned"):
                calibrate(path, events, allow_mock=True)


class CaptureBoundaryTests(unittest.TestCase):
    def test_setup_and_stricter_allocation_stop_are_counted(self):
        end, prior = effective_deadline(
            "1970-01-01T00:00:00Z",
            wall_now=100,
            mono_now=50,
            max_wall_s=200,
            hard_deadline_utc="1970-01-01T00:02:30Z",
        )
        self.assertEqual((end, prior), (100, 100))
        for kwargs in ({"max_wall_s": 10801}, {"max_wall_s": 50}):
            with self.assertRaises(CaptureError):
                effective_deadline("1970-01-01T00:00:00Z", wall_now=100, mono_now=50, **kwargs)

    def test_launch_preserves_protected_isolation_and_changes_lifecycle_only(self):
        lock = json.loads((SUITE.parents[2] / "baseline/lock.json").read_text())
        image = "sha256:" + "a" * 64
        original = start_command(
            lock, Path("/tmp/model"), "/dev/dri/renderD128", image, "owned", 8000, render_gid=42
        )
        original.remove("--detach")
        actual = launch_arguments(
            lock, Path("/tmp/model"), "/dev/dri/renderD128", image, "owned", 8000, 42, "nonce"
        )
        self.assertEqual(actual[:2] + actual[6:], original)
        self.assertEqual(
            actual[2:6], ["--rm", "--pull=never", "--label", "abstract.task1.capture=nonce"]
        )

    def test_name_alone_never_authorizes_container_removal(self):
        item = {
            "id": "a" * 64,
            "name": "/owned",
            "owner": "nonce",
            "image_ref": "sha256:" + "b" * 64,
        }
        self.assertEqual(
            owned_identity(item, owner="nonce", name="owned", image_id=item["image_ref"]), "a" * 64
        )
        for key in ("owner", "name", "image_ref", "id"):
            with self.subTest(key=key), self.assertRaises(CaptureError):
                owned_identity(
                    {**item, key: "foreign"},
                    owner="nonce",
                    name="owned",
                    image_id=item["image_ref"],
                )

    @unittest.skipUnless(os.name == "posix", "owned process groups require assigned Linux host")
    def test_deadline_stops_child_and_pipe_holding_descendant(self):
        with tempfile.TemporaryDirectory() as temp:
            started = time.monotonic()
            code = bounded_command(
                [
                    sys.executable,
                    "-c",
                    "import subprocess,sys; "
                    "subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
                    "print('done')",
                ],
                deadline=started + 5,
                log=Path(temp) / "exit.log",
            )
            self.assertEqual(code, 0)
            self.assertLess(time.monotonic() - started, 2)
            started = time.monotonic()
            with self.assertRaises(CaptureError):
                bounded_command(
                    [sys.executable, "-c", "import time;time.sleep(30)"],
                    deadline=started + 2.2,
                    log=Path(temp) / "timeout.log",
                )
            self.assertLess(time.monotonic() - started, 2)

    @unittest.skipUnless(os.name == "posix", "owned process groups require assigned Linux host")
    def test_child_output_is_byte_bounded(self):
        with tempfile.TemporaryDirectory() as temp, self.assertRaises(CaptureError):
            bounded_command(
                [sys.executable, "-c", "print('x'*10000)"],
                deadline=time.monotonic() + 5,
                log=Path(temp) / "cap.log",
                max_log_bytes=100,
            )


class ExportTests(unittest.TestCase):
    def test_roundtrip_keeps_only_declared_regular_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source"
            source.mkdir()
            (source / "run.json").write_text("not allowlisted at this path")
            (source / "report.json").write_text('{"status":"inconclusive"}')
            (source / "secret.txt").write_text("never exported")
            archive = root / "evidence.tar.gz"
            result = pack_evidence(source, archive, 1000)
            verified = verify_export(archive, 1000)
            self.assertEqual(set(verified["files"]), {"report.json"})
            self.assertEqual(result["files"], verified["files"])
            with self.assertRaises(CaptureError):
                verify_export(archive, 1)

    def test_symlink_and_corrupt_manifest_are_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                info = tarfile.TarInfo("../escape")
                info.size = 1
                handle.addfile(info, io.BytesIO(b"x"))
            with self.assertRaises(CaptureError):
                verify_export(archive, 100)
            source = root / "source"
            source.mkdir()
            (root / "report").write_text("outside")
            try:
                (source / "report.json").symlink_to(root / "report")
            except OSError:
                return
            with self.assertRaises(CaptureError):
                pack_evidence(source, root / "out.tar.gz", 1000)


class EfficientPlanTests(unittest.TestCase):
    def test_prunes_duplicate_work_without_removing_axes_or_repetitions(self):
        old = load_plan(SUITE / "workload.json")
        new = load_plan(SUITE / "workload.efficient.json")
        self.assertEqual(new["modes"]["full"]["repetitions"], 3)
        units = resolve_mode(new, "full")
        self.assertNotIn("prefill_calibration", {unit.cell_id for unit in units})
        self.assertTrue(
            {
                "chat_short_open",
                "rag_long_open",
                "generation_long_open",
                "mixed_open",
                "closed_loop_sweep",
                "aa_control_a",
                "aa_control_b",
            }
            <= {unit.cell_id for unit in units}
        )
        self.assertLess(len(units), len(resolve_mode(old, "full")))
        self.assertLess(
            estimate_mode(new, "full")["planned_wall_minutes"],
            estimate_mode(old, "full")["planned_wall_minutes"],
        )
        self.assertEqual({unit.role for unit in resolve_mode(new, "calibration")}, {"calibration"})
        self.assertEqual(
            {unit.load for unit in units if unit.cell_id == "chat_short_open"},
            {0.5, 1, 2, 4},
        )

    def test_separate_calibration_covers_actual_scoring_covariates(self):
        workload = load_plan(SUITE / "workload.efficient.json")
        lengths = []
        for unit in resolve_mode(workload, "calibration"):
            for index in range(unit.requests):
                grid = unit.prompt_token_grid
                lengths.append(
                    make_prompt(
                        workload,
                        unit.prompt_source,
                        f"{unit.unit_id}/measure",
                        unit.repetition,
                        index,
                        forced_prompt_tokens=grid[index % len(grid)] if grid else None,
                    ).approx_prompt_tokens
                )
        low, high = scoring_length_domain(workload)
        self.assertLessEqual(min(lengths), low)
        self.assertGreaterEqual(max(lengths), high)
        self.assertGreaterEqual(len(set(lengths)), 5)

    def test_leading_nonces_cover_every_source_and_preserve_historical_plan(self):
        workload = load_plan(SUITE / "workload.efficient.json")
        prefixes = set()
        for source in workload["prompt_sources"]:
            for index in range(3):
                spec = make_prompt(workload, source, "unique/measure", 0, index)
                first = spec.messages[0]["content"].splitlines()[0]
                self.assertRegex(first, r"^[A-P]{64}$")
                # Same identity through mixture may intentionally select that source;
                # distinct scheduled request identities must differ.
                other = make_prompt(workload, source, "other/measure", 0, index)
                self.assertNotEqual(first, other.messages[0]["content"].splitlines()[0])
                self.assertEqual(
                    spec.approx_prompt_tokens,
                    sum(count_tokens(m["content"]) + 4 for m in spec.messages),
                )
                prefixes.add(first)
        self.assertEqual(len(prefixes), 3)
        historical = load_plan(SUITE / "workload.json")
        self.assertNotRegex(
            make_prompt(historical, "rag_long", "unique/measure", 0, 0).messages[0]["content"],
            r"^[A-P]{64}\n",
        )


class IndependentTimingTests(unittest.TestCase):
    def evidence(self):
        records = [make_record(plan(), ttft=0.25, gap=0.01)]
        run = make_run(records)
        return Evidence(
            Path("."), RunRecord(run), [RequestRecord(row) for row in records], [], "native"
        )

    def test_first_content_and_completed_startup_are_independently_reconstructed(self):
        result = audit(self.evidence())
        self.assertEqual(result["status"], "PASS")
        self.assertAlmostEqual(result["requests"][0]["ttft_s"], 0.26)
        self.assertGreater(result["startup"][0]["completed_probe_elapsed_s"], 5)

    def test_forged_text_and_readiness_as_startup_end_are_detected(self):
        evidence = self.evidence()
        evidence.requests[0].raw["text"]["content"] = "invented"
        evidence.run.raw["startup"][0]["first_valid_generation_at"] = 92.0
        result = audit(evidence)
        self.assertEqual(result["status"], "FAIL")
        self.assertTrue(any("text disagrees" in why for why in result["failures"]))
        self.assertTrue(any("linked completed probe" in why for why in result["failures"]))

    def test_scoring_refuses_extrapolation_from_a_frozen_curve(self):
        workload = plan()
        workload["calibration"] = {"fit": {"length_domain": [1000, 2000]}}
        evaluator = Evaluator(workload, self.evidence())
        evaluator.run_gates()
        self.assertTrue(any("outside frozen calibration domain" in r for r in evaluator.reasons))

    def test_structurally_invalid_evidence_is_a_named_failure_without_metric_math(self):
        evidence = self.evidence()
        evidence.schema_errors.append("request timestamps.end must be a number")
        evidence.requests[0].raw["timestamps_mono_s"]["end"] = None
        evidence.run.raw["startup"] = None
        result = audit(evidence)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["requests"], [])
        self.assertEqual(result["failures"], evidence.schema_errors)


class ImmutableCaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_raw_capture_is_never_truncated(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            saved = root / "requests.jsonl"
            saved.write_bytes(b"old immutable evidence\n")
            args = parse_args(
                [
                    "--plan",
                    str(SUITE / "fixtures/workload.mock.json"),
                    "--out",
                    str(root),
                    "--assume-mock",
                ]
            )
            runner = Runner(args, plan())
            with self.assertRaises(FileExistsError):
                await runner.run()
            self.assertEqual(saved.read_bytes(), b"old immutable evidence\n")
            self.assertFalse((root / "run.json").exists())

    async def test_existing_compressed_capture_is_never_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            saved = root / "requests.jsonl.gz"
            saved.write_bytes(b"old compressed evidence")
            args = parse_args(
                [
                    "--plan",
                    str(SUITE / "fixtures/workload.mock.json"),
                    "--out",
                    str(root),
                    "--assume-mock",
                ]
            )
            runner = Runner(args, plan())
            with self.assertRaises(FileExistsError):
                await runner.run()
            self.assertEqual(saved.read_bytes(), b"old compressed evidence")
            self.assertFalse((root / "requests.jsonl").exists())


class RequestBudgetTests(unittest.IsolatedAsyncioTestCase):
    def test_cumulative_evidence_refuses_a_record_before_crossing_reader_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            args = parse_args(
                [
                    "--plan",
                    str(SUITE / "fixtures/workload.mock.json"),
                    "--out",
                    temp,
                    "--assume-mock",
                ]
            )
            runner = Runner(args, plan())
            runner._requests_fh = io.StringIO()
            with mock.patch("runner.run.MAX_EVIDENCE_BYTES", 100):
                runner._write_request({"small": 1})
                prefix = runner._requests_fh.getvalue()
                with self.assertRaises(CaptureLimitError):
                    runner._write_request({"large": "x" * 101})
            self.assertEqual(runner._requests_fh.getvalue(), prefix)

    async def oversized(self, *, error_body=False):
        class Bytes(httpx.AsyncByteStream):
            async def __aiter__(self):
                if error_body:
                    raise AssertionError("HTTP error body must not be materialized")
                yield b"x" * (MAX_STREAM_BYTES + 1)

        workload = plan()
        with tempfile.TemporaryDirectory() as temp:
            args = parse_args(
                [
                    "--plan",
                    str(SUITE / "fixtures/workload.mock.json"),
                    "--out",
                    temp,
                    "--assume-mock",
                ]
            )
            runner = Runner(args, workload)
            transport = httpx.MockTransport(
                lambda _request: httpx.Response(500 if error_body else 200, stream=Bytes())
            )
            async with httpx.AsyncClient(transport=transport) as client:
                return await runner.execute(
                    client,
                    make_prompt(workload, "chat_short", "x", 0, 0),
                    RequestMeta("x", None, "startup_probe", "startup", 0),
                )

    async def test_oversized_stream_is_preserved_as_named_failure(self):
        record = await self.oversized()
        self.assertEqual(record["error"]["kind"], "protocol")
        self.assertIn("byte cap", record["error"]["message"])
        self.assertFalse(record["events"])

    async def test_http_error_does_not_materialize_an_unbounded_error_body(self):
        record = await self.oversized(error_body=True)
        self.assertEqual(record["error"]["kind"], "http_error")
        self.assertFalse(record["events"])

    async def test_many_tiny_events_cannot_bypass_the_capture_memory_bound(self):
        class Events(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"data: [DONE]\n\n" * (MAX_STREAM_EVENTS + 1)

        workload = plan()
        with tempfile.TemporaryDirectory() as temp:
            args = parse_args(
                [
                    "--plan",
                    str(SUITE / "fixtures/workload.mock.json"),
                    "--out",
                    temp,
                    "--assume-mock",
                ]
            )
            runner = Runner(args, workload)
            transport = httpx.MockTransport(lambda _request: httpx.Response(200, stream=Events()))
            async with httpx.AsyncClient(transport=transport) as client:
                record = await runner.execute(
                    client,
                    make_prompt(workload, "chat_short", "x", 0, 0),
                    RequestMeta("x", None, "startup_probe", "startup", 0),
                )
        self.assertEqual(record["error"]["kind"], "protocol")
        self.assertIn("event cap", record["error"]["message"])
        self.assertLessEqual(len(record["events"]), MAX_STREAM_EVENTS)


if __name__ == "__main__":
    unittest.main()
