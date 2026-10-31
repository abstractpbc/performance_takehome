"""Private handoff tests: synthetic evidence is never a model/GPU result."""

from __future__ import annotations

import copy
import difflib
import json
import shutil
import struct
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from abstract_harness import pi_artifact as artifact
from abstract_harness.proposers import PiProposer, Proposal, ProposerContext
from abstract_harness.state import ImageRef

from . import _paths
from .test_recovery import make_harness


@unittest.skipUnless(sys.platform == "linux", "production patch/process contract")
class CompletedArtifactTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="completed-pi-artifact-fixture-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.context = self.root / "original"
        dockerfile = self.context / artifact.DOCKERFILE
        dockerfile.parent.mkdir(parents=True)
        dockerfile.write_text(
            (_paths.REPO_ROOT / artifact.DOCKERFILE).read_text(encoding="utf-8"),
            encoding="utf-8",
            newline="\n",
        )
        original = dockerfile.read_text()
        changed = original.replace("--batch-size=16", "--batch-size=8")
        self.patch = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                changed.splitlines(keepends=True),
                fromfile="a/" + artifact.DOCKERFILE.as_posix(),
                tofile="b/" + artifact.DOCKERFILE.as_posix(),
            )
        )
        self.attempt = self.root / "pi-attempt-test"
        self.attempt.mkdir()
        shutil.copytree(self.context, self.attempt / "context")
        self.profile = {"stock_tools": ["read", "bash", "edit", "write"]}
        self.program = "Choose an evidence-based configuration change, write it, then finish.\n"
        self.runtime = {
            "engine": "llmd",
            "model_repository": "fixture/model",
            "model_revision": "pinned",
            "model_inventory_sha256": "c" * 64,
            "model_args": ["--model=/models/model"],
        }
        self.inputs = self.root / "inputs"
        self.inputs.mkdir()
        values = {
            "program": self.program.encode(),
            "profile": artifact.canonical(self.profile),
            "benchmark": b'{"fixture":true}',
            "baseline_lock": b'{"fixture":"lock"}',
            "model_runtime": artifact.canonical(self.runtime),
            "historical_runtime": b'{"host_model_dir":"/old/host/model"}',
            "baseline_report": b'{"historical_measurement":true,"not_new_GPU_evidence":true}',
        }
        self.provenance = {}
        for name, raw in values.items():
            path = self.inputs / name
            path.write_bytes(raw)
            self.provenance[name] = path
        self.binding = artifact.compatibility_binding(
            context_dir=self.context,
            source_commit="a" * 40,
            harness_tree_sha256="b" * 64,
            base_image_digest="sha256:" + "d" * 64,
            program_md=self.program,
            profile=self.profile,
            benchmark=values["benchmark"],
            baseline_lock=values["baseline_lock"],
            model_runtime=self.runtime,
        )
        self.meta = {
            "experiment": "fixture-choice",
            "hypothesis": "Fixture only",
            "notes": "Not inference",
        }
        metadata = json.dumps(self.meta)
        (self.attempt / "proposal.json").write_text(metadata)
        (self.attempt / "proposal.patch").write_text(self.patch)
        prompt = PiProposer.render_prompt(
            None,
            replace(self.context_value(), context_dir=self.attempt / "context"),
            self.attempt / "proposal.patch",
            self.attempt / "proposal.json",
        )
        (self.attempt / "prompt.md").write_text(prompt)
        (self.attempt / "sessions").mkdir()
        events = []
        for index, (name, content) in enumerate(
            (("proposal.patch", self.patch), ("proposal.json", metadata))
        ):
            events.extend(
                [
                    {
                        "type": "tool_execution_start",
                        "toolCallId": str(index),
                        "toolName": "write",
                        "args": {"path": str(self.attempt / name), "content": content},
                    },
                    {
                        "type": "tool_execution_end",
                        "toolCallId": str(index),
                        "toolName": "write",
                        "isError": False,
                    },
                ]
            )
        self.message = {
            "role": "assistant",
            "stopReason": "toolUse",
            "usage": {
                "input": 100,
                "output": 20,
                "cacheRead": 0,
                "cacheWrite": 0,
                "totalTokens": 120,
                "cost": {"total": 0.0001},
            },
        }
        events.insert(0, {"type": "message_end", "message": self.message})
        events.extend(
            [
                {"type": "message_end", "message": {**self.message, "stopReason": "stop"}},
                {"type": "agent_end", "willRetry": False},
            ]
        )
        self.events = events
        self.transcript = self.attempt / "pi-events.jsonl"
        self.write_events()
        self.status = {
            "status": "complete",
            "accounting_resolved": True,
            "returncode": 0,
            "timed_out": False,
            "aborted": False,
            "timeout_s": 300,
            "prompt_sha256": artifact.digest(prompt.encode()),
        }
        (self.attempt / "attempt-status.json").write_text(json.dumps(self.status))
        self.proposal = Proposal(
            **self.meta, patch=self.patch, api_usd=0.0002, transcript_path=str(self.transcript)
        )
        scope = self.root / "CPU-SCOPE.json"
        scope.write_bytes(b'{"kind":"TEST_ONLY_SYNTHETIC_SCOPE"}\n')
        self.scope = {
            "field": "external_sequential_optimization_CPU_scope",
            "value": {
                "path": str(scope),
                "sha256": artifact.digest(scope.read_bytes()),
            },
        }
        self.receipts, self.payloads = [], []
        for index in range(2):
            identifier = "api-" + str(index) * 32
            directory = self.root / "provider" / identifier
            directory.mkdir(parents=True)
            usage = {
                "prompt_tokens": 100,
                "completion_tokens": 20,
                "total_tokens": 120,
                "undiscounted_price_usd": "0.000228",
            }
            finish = "tool_calls" if index == 0 else "stop"
            body = (
                b"data: "
                + json.dumps({"usage": usage, "choices": [{"finish_reason": finish}]}).encode()
                + b"\n\ndata: [DONE]\n\n"
            )
            chain = bytes.fromhex(artifact.digest(bytes(32) + body))
            (directory / "upstream.wal").write_bytes(struct.pack(">I", len(body)) + chain + body)
            receipt = {
                "request_id": identifier,
                "status": "usage_settled",
                "usage_known": True,
                "terminal_done": True,
                "delivery": "complete",
                "body_bytes": len(body),
                "complete_wal_records": 1,
                "wal_chain_sha256": chain.hex(),
                "usage": usage,
                "finish_reason": finish,
            }
            path = directory / "receipt.json"
            path.write_text(json.dumps(receipt))
            self.receipts.append(path)
            self.payloads.extend(
                [
                    {
                        "kind": "request_reserved",
                        "id": identifier,
                        self.scope["field"]: self.scope["value"],
                    },
                    {
                        "kind": "request_usage_settled",
                        "id": identifier,
                        "usage": usage,
                        "charge_upper_usd": "0.000228",
                    },
                ]
            )
        self.journal = self.root / "journal.jsonl"
        self.write_journal()
        self.output = self.root / "sealed"

    def write_events(self):
        self.transcript.write_bytes(b"".join(artifact.canonical(e) + b"\n" for e in self.events))

    def write_journal(self):
        previous, lines = "0" * 64, []
        for seq, payload in enumerate(self.payloads):
            core = {"seq": seq, "previous": previous, "payload": payload}
            previous = artifact.digest(artifact.canonical(core))
            lines.append(artifact.canonical({**core, "sha256": previous}) + b"\n")
        self.journal.write_bytes(b"".join(lines))

    def seal(self, **kwargs):
        return artifact.seal_completed_pi_proposal(
            self.proposal,
            proposal_context=self.context_value(),
            context_dir=self.context,
            compatibility=self.binding,
            provenance_files=self.provenance,
            provider_receipt_paths=self.receipts,
            journal_path=self.journal,
            request_scope=self.scope,
            output_dir=self.output,
            **kwargs,
        )

    def context_value(self):
        return ProposerContext(self.program, self.profile, [], self.context, self.root, "llmd")

    def adapter(self, pin):
        return artifact.VerifiedCompletedPiProposal(
            self.output,
            manifest_sha256=pin,
            expected_compatibility=self.binding,
            claim_path=self.root / "consumer.claim",
            consumer_id="new-independent-GPU-run",
        )

    def test_exact_patch_metadata_replay_and_known_cost_once_without_any_process(self):
        original = (self.context / artifact.DOCKERFILE).read_bytes()
        pin = self.seal()
        with mock.patch("subprocess.Popen") as process:
            replay = self.adapter(pin).propose(self.context_value())
        process.assert_not_called()
        self.assertEqual(replay.patch, self.proposal.patch)
        self.assertEqual(
            (replay.experiment, replay.hypothesis, replay.notes),
            (self.proposal.experiment, self.proposal.hypothesis, self.proposal.notes),
        )
        self.assertEqual(replay.api_usd, 0.000456)
        manifest = json.loads((self.output / "manifest.json").read_bytes())
        self.assertEqual(manifest["proposal"]["api_usd"], 0.0002)
        self.assertFalse(manifest["replay_cost_policy"]["new_provider_charge_on_replay"])
        self.assertEqual((self.context / artifact.DOCKERFILE).read_bytes(), original)
        with self.assertRaisesRegex(artifact.ArtifactError, "already claimed"):
            self.adapter(pin).propose(self.context_value())

    def test_uncertain_consumer_claim_survives_new_instance_and_does_not_repeat_cost(self):
        pin = self.seal()
        self.adapter(pin).propose(self.context_value())
        original_claim = (self.root / "consumer.claim").read_bytes()
        with self.assertRaisesRegex(artifact.ArtifactError, "already claimed"):
            self.adapter(pin).propose(self.context_value())
        self.assertEqual((self.root / "consumer.claim").read_bytes(), original_claim)

    def test_harness_run_accounts_carried_upper_once_before_attempt(self):
        pin = self.seal()
        harness = make_harness(self.root / "harness")
        harness.config.budget = replace(harness.config.budget, api_usd_limit=1)
        harness.config.max_attempts = None
        harness.proposer = self.adapter(pin)
        harness.state.last_good = ImageRef("fixture-control", "sha256:" + "e" * 64)
        try:
            with (
                mock.patch.object(harness, "establish_baseline"),
                mock.patch.object(harness, "_proposer_context", return_value=self.context_value()),
                mock.patch.object(harness, "attempt", return_value="no_change") as attempt,
                mock.patch.object(harness, "finalize") as finalize,
                mock.patch.object(harness, "cleanup"),
            ):
                self.assertEqual(harness.run(), 0)
            self.assertEqual(harness.state.api_usd_cumulative, 0.000456)
            self.assertEqual(harness.state.api_accounting_unresolved, "")
            self.assertTrue(attempt.call_args.kwargs["api_cost_accounted"])
            self.assertEqual(attempt.call_args.args[0].patch, self.patch)
            attempt.assert_called_once()
            finalize.assert_called_once_with("proposer exhausted")
        finally:
            harness._release_locks()

    def test_each_compatibility_pin_rejects_before_claim(self):
        pin = self.seal()
        for key in self.binding:
            with self.subTest(key=key):
                bad = dict(self.binding)
                bad[key] = (
                    "f" * 40
                    if key == "source_commit"
                    else "sha256:" + "f" * 64
                    if key == "base_image_digest"
                    else "f" * 64
                )
                with self.assertRaises(artifact.ArtifactError):
                    artifact.verify_pi_artifact(self.output, pin, bad)
        self.assertFalse((self.root / "consumer.claim").exists())

    def test_current_context_program_profile_mismatch_rejected_before_claim(self):
        pin = self.seal()
        for key in ("program_md", "profile"):
            with self.subTest(key=key):
                ctx = self.context_value()
                setattr(ctx, key, "changed" if key == "program_md" else {"changed": True})
                with self.assertRaises(artifact.ArtifactError):
                    self.adapter(pin).propose(ctx)
        (self.context / artifact.DOCKERFILE).write_text("changed")
        with self.assertRaises(artifact.ArtifactError):
            self.adapter(pin).propose(self.context_value())
        self.assertFalse((self.root / "consumer.claim").exists())

    def test_changed_private_bytes_rejected_for_every_file(self):
        pin = self.seal()
        manifest = json.loads((self.output / "manifest.json").read_bytes())
        for name in manifest["files"]:
            path = self.output / name
            old = path.read_bytes()
            path.write_bytes(old + b"corrupt")
            with self.subTest(name=name), self.assertRaises(artifact.ArtifactError):
                artifact.verify_pi_artifact(self.output, pin, self.binding)
            path.write_bytes(old)

    def test_fresh_history_and_host_paths_are_not_compatibility_keys_but_cache_is(self):
        pin = self.seal()
        context = self.context_value()
        context.history = [{"ts": "new GPU baseline", "host_path": "/different/new/mount"}]
        context.budget_summary = "new independently timed GPU allocation"
        replay = self.adapter(pin).propose(context)
        self.assertEqual(replay.patch, self.patch)
        cache = self.context / "__pycache__/not-silently-ignored.pyc"
        cache.parent.mkdir()
        cache.write_bytes(b"new unbound file")
        self.assertNotEqual(
            artifact.tree_digest(self.context), self.binding["protected_context_sha256"]
        )

    def test_missing_metadata_write_is_not_a_completed_proposal(self):
        self.events = [e for e in self.events if e.get("toolCallId") != "1"]
        self.write_events()
        with self.assertRaisesRegex(artifact.ArtifactError, "metadata write"):
            self.seal()
        self.assertFalse(self.output.exists())

    def test_old_proposal_cannot_be_sealed_as_new_program_or_profile(self):
        for key in ("program", "profile"):
            with self.subTest(key=key):
                old_binding = dict(self.binding)
                if key == "program":
                    original = self.program
                    self.program = "New instructions Pi never received"
                    self.binding["program_sha256"] = artifact.digest(self.program.encode())
                    self.provenance[key].write_text(self.program)
                else:
                    original = self.profile
                    self.profile = {"different": "profile Pi never received"}
                    self.binding["profile_sha256"] = artifact.digest(
                        artifact.canonical(self.profile)
                    )
                    self.provenance[key].write_bytes(artifact.canonical(self.profile))
                with self.assertRaisesRegex(artifact.ArtifactError, "rendered Pi instructions"):
                    self.seal()
                if key == "program":
                    self.program = original
                    self.provenance[key].write_text(original)
                else:
                    self.profile = original
                    self.provenance[key].write_bytes(artifact.canonical(original))
                self.binding = old_binding
        self.assertFalse(self.output.exists())

    def test_modified_prompt_and_changed_historical_render_inputs_are_rejected(self):
        prompt = self.attempt / "prompt.md"
        original = prompt.read_bytes()
        prompt.write_bytes(original + b"tampered")
        with self.assertRaisesRegex(artifact.ArtifactError, "saved prompt hash"):
            self.seal()
        prompt.write_bytes(original)
        context = self.context_value()
        context.history = [{"experiment": "different actual input"}]
        with self.assertRaisesRegex(artifact.ArtifactError, "rendered Pi instructions"):
            artifact.seal_completed_pi_proposal(
                self.proposal,
                proposal_context=context,
                context_dir=self.context,
                compatibility=self.binding,
                provenance_files=self.provenance,
                provider_receipt_paths=self.receipts,
                journal_path=self.journal,
                request_scope=self.scope,
                output_dir=self.output,
            )

    def test_uncapped_new_consumer_refusal_is_recorded_once_then_finishes(self):
        pin = self.seal()
        self.adapter(pin).propose(self.context_value())
        harness = make_harness(self.root / "harness")
        harness.config.budget = replace(harness.config.budget, api_usd_limit=1)
        harness.config.max_attempts = None
        harness.proposer = self.adapter(pin)
        harness.state.last_good = ImageRef("fixture-control", "sha256:" + "e" * 64)
        try:
            with (
                mock.patch.object(harness, "establish_baseline"),
                mock.patch.object(harness, "_proposer_context", return_value=self.context_value()),
                mock.patch.object(harness, "attempt") as attempt,
                mock.patch.object(harness, "finalize") as finalize,
                mock.patch.object(harness, "cleanup"),
            ):
                self.assertEqual(harness.run(), 0)
            attempt.assert_not_called()
            self.assertEqual(harness.state.api_usd_cumulative, 0)
            self.assertEqual(harness.state.api_accounting_unresolved, "")
            self.assertEqual(len(harness.state.history), 1)
            finalize.assert_called_once_with("proposer exhausted")
        finally:
            harness._release_locks()

    def test_test_only_fixture_cannot_enter_GPU_loop_and_refusal_cost_is_known_zero(self):
        pin = self.seal(purpose="test-only-compatibility-fixture")
        with self.assertRaises(artifact.ReplayArtifactError) as failure:
            self.adapter(pin).propose(self.context_value())
        self.assertEqual(failure.exception.api_usd, 0)
        self.assertFalse((self.root / "consumer.claim").exists())

    def test_truncated_model_turn_and_output_limit_are_rejected(self):
        for reason in ("length", "toolUse", "error"):
            with self.subTest(reason=reason):
                self.events[-2]["message"]["stopReason"] = reason
                self.write_events()
                with self.assertRaises(artifact.ArtifactError):
                    self.seal()
        self.events[-2]["message"]["stopReason"] = "stop"
        self.write_events()
        self.transcript.write_bytes(self.transcript.read_bytes().rstrip(b"\n"))
        with self.assertRaisesRegex(artifact.ArtifactError, "truncated"):
            self.seal()

    def test_torn_or_corrupt_WAL_and_wrong_scope_and_unknown_are_rejected(self):
        wal = self.receipts[0].with_name("upstream.wal")
        original = wal.read_bytes()
        for bad in (original[:-1], original[:4] + bytes(32) + original[36:]):
            wal.write_bytes(bad)
            with self.assertRaises(artifact.ArtifactError):
                self.seal()
        wal.write_bytes(original)
        good = copy.deepcopy(self.scope)
        self.scope["value"]["sha256"] = "e" * 64
        with self.assertRaises(artifact.ArtifactError):
            self.seal()
        self.scope = good
        self.payloads.append({"kind": "request_unknown", "id": "api-" + "0" * 32})
        self.write_journal()
        with self.assertRaisesRegex(artifact.ArtifactError, "UNKNOWN"):
            self.seal()

    def test_changed_financial_chain_is_rejected_without_silent_recovery(self):
        raw = self.journal.read_bytes()
        self.journal.write_bytes(raw.replace(b"request_usage_settled", b"request_usage_altered", 1))
        with self.assertRaisesRegex(artifact.ArtifactError, "Journal chain"):
            self.seal()
