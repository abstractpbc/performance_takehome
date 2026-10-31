"""Paid Pi output failures retain their charge before another budget admission."""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from abstract_harness import proposers, results
from abstract_harness.procs import RunResult
from abstract_harness.proposers import PiLaunchConfig, PiProposer, ProposerContext, ProposerError
from abstract_harness.state import load_state

from . import _paths
from .test_recovery import make_harness


class PaidOutputFailureTest(unittest.TestCase):
    def setup_pi(self, root):
        source = root / "source"
        source.mkdir()
        (source / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        work = root / "work"
        work.mkdir()
        profile = json.loads((_paths.REPO_ROOT / "agent-harness/profiles.json").read_text())[
            "profiles"
        ]["harness-design"]
        proposer = PiProposer(
            PiLaunchConfig(
                pi_bin=root / "fixture-pi", agent_dir=root / "agent",
                api_key_env="HARNESS_TEST_KEY", usd_per_call_estimate=0.02,
            ),
            profile=profile,
        )
        context = ProposerContext("# test", profile, [], source, work, "llmd")
        return proposer, context

    def paid_result(self, output):
        def run(command, **kwargs):
            scratch = kwargs["cwd"]
            if output == "binary":
                (scratch / "changed.bin").write_bytes(b"\x00new binary\xff")
            elif output == "metadata":
                (scratch.parent / "proposal.patch").write_text("fixture patch\n", encoding="utf-8")
                (scratch.parent / "proposal.json").write_text("[]\n", encoding="utf-8")
            else:
                (scratch.parent / "proposal.patch").write_bytes(b"\xffinvalid UTF-8")
            event = {
                "type": "message_end",
                "message": {"role": "assistant", "usage": {
                    "input": 10, "output": 2, "cacheRead": 0, "cacheWrite": 0,
                    "cost": {"total": 0.02},
                }},
            }
            kwargs["log_path"].write_text(json.dumps(event) + "\n", encoding="utf-8")
            return RunResult(tuple(command), 0, "", "", 0.01, False)
        return run

    def test_binary_fallback_preserves_paid_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            proposer, context = self.setup_pi(Path(tmp))
            with (
                mock.patch.dict("os.environ", {"HARNESS_TEST_KEY": "fixture-only"}),
                mock.patch.object(proposers.procs, "run", side_effect=self.paid_result("binary")),
                self.assertRaisesRegex(ProposerError, "binary changes") as failure,
            ):
                proposer.propose(context)
            self.assertEqual(failure.exception.api_usd, 0.02)
            self.assertEqual(proposer.estimated_usd_per_call(), 0.02)
            self.assertEqual((context.context_dir / "Dockerfile").read_text(), "FROM scratch\n")
            self.assertFalse((context.context_dir / "changed.bin").exists())

    def test_unreadable_patch_or_invalid_metadata_preserves_paid_cost(self):
        for output in ("utf8", "metadata"):
            with self.subTest(output=output), tempfile.TemporaryDirectory() as tmp:
                proposer, context = self.setup_pi(Path(tmp))
                with (
                    mock.patch.dict("os.environ", {"HARNESS_TEST_KEY": "fixture-only"}),
                    mock.patch.object(proposers.procs, "run", side_effect=self.paid_result(output)),
                    self.assertRaises(ProposerError) as failure,
                ):
                    proposer.propose(context)
                self.assertEqual(failure.exception.api_usd, 0.02)

    def test_failed_proposal_is_charged_before_next_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            proposer, context = self.setup_pi(root)
            harness = make_harness(root)
            harness.config.budget = replace(harness.config.budget, api_usd_limit=0.03)
            harness.config.max_attempts = 2
            harness.proposer = proposer
            with (
                mock.patch.dict("os.environ", {"HARNESS_TEST_KEY": "fixture-only"}),
                mock.patch.object(
                    proposers.procs, "run", side_effect=self.paid_result("binary")
                ) as paid,
                mock.patch.object(harness, "install_signal_handlers"),
                mock.patch.object(harness, "recover_from_previous_run"),
                mock.patch.object(harness, "establish_baseline"),
                mock.patch.object(harness, "_proposer_context", return_value=context),
                mock.patch.object(harness, "finalize") as finalize,
                mock.patch.object(harness, "cleanup"),
            ):
                self.assertEqual(harness.run(), 0)
            self.assertEqual(paid.call_count, 1)
            self.assertIn("API spend", finalize.call_args.args[0])
            state = load_state(root / "state/state.json")
            self.assertEqual(state.api_usd_cumulative, 0.02)
            self.assertFalse(state.api_accounting_unresolved)
            rows = results.read_rows(root / "results.tsv")
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].outcome, "error")
            self.assertEqual(float(rows[0].api_usd_cumulative), 0.02)

    def test_length_terminal_rejects_partial_artifacts_and_preserves_paid_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            proposer, context = self.setup_pi(Path(tmp))

            def truncated(command, **kwargs):
                result = self.paid_result("metadata")(command, **kwargs)
                (kwargs["cwd"].parent / "proposal.json").write_text(
                    '{"experiment":"partial","hypothesis":"unfinished"}\n', encoding="utf-8"
                )
                event = json.loads(kwargs["log_path"].read_text())
                event["message"]["stopReason"] = "length"
                kwargs["log_path"].write_text(json.dumps(event) + "\n", encoding="utf-8")
                return result

            with (
                mock.patch.dict("os.environ", {"HARNESS_TEST_KEY": "fixture-only"}),
                mock.patch.object(proposers.procs, "run", side_effect=truncated),
                self.assertRaisesRegex(ProposerError, "output limit.*partial proposal refused")
                as failure,
            ):
                proposer.propose(context)
            self.assertEqual(failure.exception.api_usd, 0.02)
            self.assertEqual(proposer.estimated_usd_per_call(), 0.02)
            self.assertEqual((context.context_dir / "Dockerfile").read_text(), "FROM scratch\n")
