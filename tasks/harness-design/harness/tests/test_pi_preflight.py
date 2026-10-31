"""Actual stock Pi exercises provider wire payloads and complete proposal recovery.

Set ABSTRACT_PI_SMOKE_NODE/CLI/PACKAGE to independently installed official assets.
Without them only the default-inactive contract runs; integration tests explicitly skip.
The HTTP fixtures never call an inference model or allocate compute.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from abstract_harness import pi_preflight

from . import _paths

ASSET_ENV = ("ABSTRACT_PI_SMOKE_NODE", "ABSTRACT_PI_SMOKE_CLI", "ABSTRACT_PI_SMOKE_PACKAGE")


class InactivePreflightTest(unittest.TestCase):
    def test_default_does_not_launch_process_server_or_read_distribution(self):
        output = io.StringIO()
        with (
            mock.patch.object(sys, "argv", ["preflight"]),
            mock.patch.object(pi_preflight, "Fixture") as fixture,
            mock.patch.object(pi_preflight, "stock_distribution") as distribution,
            mock.patch.object(pi_preflight.subprocess, "run") as run,
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(pi_preflight.main(), 0)
        self.assertTrue(json.loads(output.getvalue())["inactive"])
        fixture.assert_not_called()
        distribution.assert_not_called()
        run.assert_not_called()


@unittest.skipUnless(sys.platform == "linux", "Linux process launcher contract")
class LaunchFailureReceiptTest(unittest.TestCase):
    def test_both_version_launch_failures_write_failed_receipts(self):
        errors = (
            subprocess.CalledProcessError(3, ["node", "cli", "--version"]),
            subprocess.TimeoutExpired(["node", "--version"], 15),
            OSError("executable unavailable"),
        )
        with tempfile.TemporaryDirectory() as temporary:
            for index, error in enumerate(errors):
                for command_index in (0, 1):
                    with self.subTest(error=type(error).__name__, command=command_index):
                        output = Path(temporary) / f"case-{index}-{command_index}"
                        effects = (
                            [subprocess.CompletedProcess([], 0, stdout="1.0.0\n")]
                            if command_index
                            else []
                        ) + [error]
                        with (
                            mock.patch.object(
                                pi_preflight, "stock_distribution", return_value=1243
                            ),
                            mock.patch.object(pi_preflight.subprocess, "run", side_effect=effects),
                        ):
                            result = pi_preflight.proposal_preflight(
                                node=Path("/fixture-node"),
                                cli=Path("/fixture-cli"),
                                package=Path("/fixture-package"),
                                work=output,
                                source=_paths.REPO_ROOT / "tasks/harness-design/Dockerfile",
                            )
                        self.assertEqual(result["status"], "LAUNCH_FAILED")
                        self.assertEqual(result["fixture_http_requests"], 0)
                        self.assertFalse(result["proposal_recovered"])
                        saved = json.loads((output / "verification.json").read_bytes())
                        self.assertEqual(saved["status"], "LAUNCH_FAILED")
                        self.assertTrue(saved["error"])


@unittest.skipUnless(
    sys.platform == "linux" and all(os.environ.get(k) for k in ASSET_ENV),
    "actual stock-Pi assets not supplied; no model/GPU fallback",
)
class ActualStockPiPreflightTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="actual-stock-pi-preflight-")
        self.work = Path(self.temporary.name) / "run"

    def tearDown(self):
        self.temporary.cleanup()

    def execute(self, mode):
        result = pi_preflight.proposal_preflight(
            node=Path(os.environ[ASSET_ENV[0]]).resolve(),
            cli=Path(os.environ[ASSET_ENV[1]]).resolve(),
            package=Path(os.environ[ASSET_ENV[2]]).resolve(),
            work=self.work,
            source=_paths.REPO_ROOT / "tasks/harness-design/Dockerfile",
            fixture_mode=mode,
        )
        self.assertEqual(result["actual_provider_spend_USD"], 0)
        self.assertEqual(result["verified_stock_Pi_files"], 1243)
        self.assertEqual(result["proposal_deadline_seconds"], 300)
        requests = json.loads((self.work / "fixture-wire-requests.json").read_bytes())
        # Independent wire expectations, including every actual tool followup.
        for request in requests:
            body = request["body"]
            self.assertEqual(body["thinking"], {"type": "disabled"})
            self.assertEqual(body["chat_template_kwargs"], {"enable_thinking": False})
            self.assertEqual(body["tool_choice"], "auto")
            self.assertEqual(body["max_tokens"], 8192)
            self.assertNotIn("max_completion_tokens", body)
            self.assertEqual(
                {v["function"]["name"] for v in body["tools"]}, {"read", "bash", "edit", "write"}
            )
        return result, requests

    def test_read_edit_completion_recovers_and_applies_valid_single_knob_patch(self):
        result, requests = self.execute("success")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(requests), 4)
        self.assertEqual(result["successful_stock_tools"], ["read", "edit", "write"])
        self.assertTrue(result["proposal_recovered"])
        self.assertTrue(result["proposal_metadata_validated"])
        self.assertTrue(result["terminal_usage_reconciled"])
        self.assertEqual(result["source_eligibility"]["changed_knobs"], ["--token-batch-size"])
        self.assertEqual(
            result["source_eligibility"]["changed_paths"], ["tasks/harness-design/Dockerfile"]
        )
        self.assertTrue(result["source_context_unchanged"])
        self.assertAlmostEqual(result["api_usd_known"], 4 * (100 * 1.4 + 20 * 4.4) / 1e6)
        # Later requests contain actual successful stock-tool results, not just
        # an assistant declaration that a read/edit happened.
        self.assertEqual(sum(m.get("role") == "tool" for m in requests[0]["body"]["messages"]), 0)
        self.assertEqual(sum(m.get("role") == "tool" for m in requests[1]["body"]["messages"]), 1)
        self.assertEqual(sum(m.get("role") == "tool" for m in requests[-1]["body"]["messages"]), 3)
        self.assertIn("+CMD", (self.work / "recovered-proposal.patch").read_text())

    def test_output_limit_is_not_a_completed_proposal(self):
        result, requests = self.execute("length")
        self.assertEqual(result["status"], "OUTPUT_LIMIT")
        self.assertEqual(len(requests), 1)
        self.assertFalse(result["proposal_recovered"])
        self.assertTrue(result["terminal_usage_reconciled"])

    def test_normal_text_without_patch_is_explicit_no_patch(self):
        result, requests = self.execute("no-patch")
        self.assertEqual(result["status"], "NO_PATCH")
        self.assertEqual(len(requests), 1)
        self.assertFalse(result["proposal_recovered"])

    def test_valid_edit_without_metadata_write_cannot_pass(self):
        result, requests = self.execute("metadata-missing")
        self.assertEqual(result["status"], "INVALID_METADATA")
        self.assertEqual(len(requests), 3)
        self.assertFalse(result["proposal_recovered"])
        self.assertGreater(result["api_usd_known"], 0)

    def test_successful_write_of_malformed_metadata_cannot_pass(self):
        result, requests = self.execute("metadata-malformed")
        self.assertEqual(result["status"], "INVALID_METADATA")
        self.assertEqual(len(requests), 4)
        self.assertFalse(result["proposal_recovered"])
        self.assertGreater(result["api_usd_known"], 0)

    def test_partial_terminal_after_real_edit_is_not_accepted_or_declared_free(self):
        result, requests = self.execute("partial")
        self.assertEqual(result["status"], "ACCOUNTING_UNRESOLVED")
        self.assertEqual(len(requests), 4)
        self.assertFalse(result["proposal_recovered"])
        self.assertFalse(result["terminal_usage_reconciled"])
        self.assertGreater(result["api_usd_known"], 0)
        self.assertFalse((self.work / "recovered-proposal.patch").exists())
        edited = next(self.work.glob("pi-attempt-*/context/tasks/harness-design/Dockerfile"))
        self.assertIn("--token-batch-size=256", edited.read_text())

    def test_stalled_provider_after_edit_times_out_with_known_subtotal_retained(self):
        result = pi_preflight.proposal_preflight(
            node=Path(os.environ[ASSET_ENV[0]]).resolve(),
            cli=Path(os.environ[ASSET_ENV[1]]).resolve(),
            package=Path(os.environ[ASSET_ENV[2]]).resolve(),
            work=self.work,
            source=_paths.REPO_ROOT / "tasks/harness-design/Dockerfile",
            fixture_mode="stall",
            fixture_timeout_seconds=30,
        )
        self.assertEqual(result["status"], "ACCOUNTING_UNRESOLVED")
        self.assertTrue(result["process_timed_out"])
        self.assertFalse(result["proposal_recovered"])
        self.assertFalse(result["terminal_usage_reconciled"])
        self.assertGreater(result["api_usd_known"], 0)
        self.assertFalse((self.work / "recovered-proposal.patch").exists())
