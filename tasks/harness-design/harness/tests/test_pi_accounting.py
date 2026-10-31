"""Unknown model usage stops the loop and remains blocked after restart."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from abstract_harness.proposers import PiProposer, ProposerAccountingError
from abstract_harness.state import load_state

from .test_recovery import make_harness


def terminal(usage):
    return {"type": "message_end", "message": {"role": "assistant", "usage": usage}}


def usage(cost=0.01):
    return {"input": 100, "output": 20, "cacheRead": 0, "cacheWrite": 0,
            "cost": {"total": cost}}


class PiAccountingTest(unittest.TestCase):
    def parse(self, events):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "events.jsonl"
            transcript.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
            return PiProposer._cost_from_events(transcript)

    def test_only_terminal_assistant_cost_is_counted(self):
        self.assertAlmostEqual(self.parse([
            {"type": "message_update", "message": {"usage": usage(99)}},
            terminal(usage()),
            {"type": "message_end", "message": {"role": "toolResult", "usage": usage(99)}},
            terminal(usage(0.02)),
        ]), 0.03)

    def test_missing_usage_preserves_known_subtotal_but_rejects_run(self):
        with self.assertRaises(ProposerAccountingError) as ctx:
            self.parse([terminal(usage()), terminal(None)])
        self.assertAlmostEqual(ctx.exception.api_usd, 0.01)

    def test_zero_cost_is_unknown_even_when_token_counts_exist(self):
        with self.assertRaisesRegex(ProposerAccountingError, "positive price"):
            self.parse([terminal(usage(0))])

    def test_invalid_costs_are_rejected(self):
        for cost in (True, -1, float("nan"), float("inf"), "0.01", None):
            with self.subTest(cost=cost), self.assertRaises(ProposerAccountingError):
                self.parse([terminal(usage(cost))])

    def test_missing_invalid_or_zero_token_counts_are_rejected(self):
        for counts in ({}, {"input": True, "output": 1},
                       {"input": -1, "output": 1}, {"input": 0, "output": 0}):
            sample = {"cacheRead": 0, "cacheWrite": 0, "cost": {"total": 0.01}, **counts}
            with self.subTest(counts=counts), self.assertRaises(ProposerAccountingError):
                self.parse([terminal(sample)])

    def test_no_terminal_message_is_unknown_usage(self):
        with self.assertRaisesRegex(ProposerAccountingError, "no terminal"):
            self.parse([{"type": "agent_start"}])

    def test_partial_response_after_paid_turn_is_unknown_usage(self):
        with self.assertRaisesRegex(ProposerAccountingError, "partial assistant") as ctx:
            self.parse([terminal(usage()),
                        {"type": "message_start", "message": {"role": "assistant"}}])
        self.assertAlmostEqual(ctx.exception.api_usd, 0.01)

    def test_accounting_failure_stops_and_blocks_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            harness = make_harness(root)
            harness.proposer = mock.Mock()
            harness.proposer.estimated_usd_per_call.return_value = 0
            harness.proposer.propose.side_effect = ProposerAccountingError(
                "unknown final request", api_usd=0.002
            )
            with (mock.patch.object(harness, "install_signal_handlers"),
                  mock.patch.object(harness, "recover_from_previous_run"),
                  mock.patch.object(harness, "establish_baseline"),
                  mock.patch.object(harness, "_proposer_context"),
                  mock.patch.object(harness, "finalize")):
                self.assertEqual(harness.run(), 2)
            harness.proposer.propose.assert_called_once()
            state = load_state(root / "state/state.json")
            self.assertEqual(state.api_accounting_unresolved, "unknown final request")
            self.assertAlmostEqual(state.api_usd_cumulative, 0.002)
            resumed = make_harness(root)
            resumed.proposer = mock.Mock()
            with (mock.patch.object(resumed, "install_signal_handlers"),
                  mock.patch.object(resumed, "recover_from_previous_run"),
                  mock.patch.object(resumed, "establish_baseline") as baseline,
                  mock.patch.object(resumed, "finalize")):
                self.assertEqual(resumed.run(), 2)
            baseline.assert_not_called()
            resumed.proposer.propose.assert_not_called()
