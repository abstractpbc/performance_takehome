"""Declared no-tools overlay tests; all providers here are deterministic fixtures."""

from __future__ import annotations

import copy
import json
import os
import struct
import sys
import threading
import unittest
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from abstract_harness import manifest
from abstract_harness import pi_artifact as artifact
from abstract_harness import pi_one_response as one
from abstract_harness.pi_preflight import stock_distribution
from abstract_harness.proposers import PiLaunchConfig, Proposal, ProposerError

from . import test_pi_artifact as artifact_tests


class Fixture:
    """Serialized SDK/WAL/Journal fixtures, never inference or hardware evidence."""

    def __init__(self, owner):
        self.base = artifact_tests.CompletedArtifactTest()
        self.base.setUp()
        owner.addCleanup(self.base.doCleanups)
        self.payload = {**self.base.meta, "patch": self.base.patch}
        self.text = json.dumps(self.payload)
        self.make_synthetic_completion()

    def make_synthetic_completion(self):
        b = self.base
        prompt = one.render_one_response_prompt(b.context_value())
        (b.attempt / "prompt.md").write_text(prompt)
        (b.attempt / "execution-policy.json").write_bytes(
            artifact.canonical(artifact.ONE_RESPONSE_POLICY)
        )
        (b.attempt / "final-response.txt").write_text(self.text)
        b.events = [
            {
                "type": "message_end",
                "message": {
                    **b.message,
                    "stopReason": "stop",
                    "content": [{"type": "text", "text": self.text}],
                },
            },
            {"type": "agent_end", "willRetry": False},
        ]
        b.write_events()
        b.status.update(
            mode="one-response-no-tools-v1",
            controller_written_files=True,
            prompt_sha256=artifact.digest(prompt.encode()),
            command=["official-fixture-cli", "--offline", "--no-tools"],
        )
        (b.attempt / "attempt-status.json").write_text(json.dumps(b.status))
        b.proposal = Proposal(**self.payload, api_usd=0.0001, transcript_path=str(b.transcript))
        self.financial_wire()

    def financial_wire(self):
        b = self.base
        self.body = (
            b"data: "
            + json.dumps(
                {
                    "id": "fixture-one",
                    "object": "chat.completion.chunk",
                    "created": 0,
                    "model": "glm-5.2",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": self.text},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                }
            ).encode()
            + b"\n\ndata: [DONE]\n\n"
        )
        raw = self.body
        chain = bytes.fromhex(artifact.digest(bytes(32) + raw))
        b.receipts = b.receipts[:1]
        receipt = b.receipts[0]
        receipt.with_name("upstream.wal").write_bytes(struct.pack(">I", len(raw)) + chain + raw)
        j = json.loads(receipt.read_bytes())
        j.update(
            body_bytes=len(raw),
            complete_wal_records=1,
            wal_chain_sha256=chain.hex(),
            finish_reason="stop",
            requested_output_cap=2048,
            timeout_policy={"response_seconds": 60},
        )
        receipt.write_text(json.dumps(j))
        b.payloads = b.payloads[:2]
        b.write_journal()

    def seal(self, **overrides):
        b = self.base
        kwargs = dict(
            proposal_context=b.context_value(),
            context_dir=b.context,
            compatibility=b.binding,
            provenance_files=b.provenance,
            provider_receipt_paths=b.receipts,
            journal_path=b.journal,
            request_scope=b.scope,
            output_dir=b.output,
        )
        kwargs.update(overrides)
        return one.seal_one_response_pi_proposal(b.proposal, **kwargs)


@unittest.skipUnless(sys.platform == "linux", "production patch/process contract")
class OneResponseArtifactTest(unittest.TestCase):
    def setUp(self):
        self.f = Fixture(self)
        self.b = self.f.base

    def test_exact_model_text_contract_seals_exact_fields_and_declared_overlay(self):
        pin = self.f.seal()
        manifest = artifact.verify_pi_artifact(self.b.output, pin, self.b.binding)
        self.assertEqual(manifest["execution_policy"], artifact.ONE_RESPONSE_POLICY)
        self.assertTrue(manifest["execution_policy"]["controller_written_files"])
        replay = self.b.adapter(pin)
        returned = replay.propose(self.b.context_value())
        self.assertEqual(returned.patch, self.b.patch)
        self.assertEqual(returned.hypothesis, self.b.meta["hypothesis"])
        self.assertEqual(returned.api_usd, 0.000228)
        self.assertIn("one-response-no-tools", replay.name)
        self.assertTrue(replay.exhausted())

    def test_four_tool_sealer_still_requires_actual_stock_metadata_write(self):
        with self.assertRaisesRegex(artifact.ArtifactError, "metadata write"):
            self.b.seal()

    def test_controller_cannot_repair_patch_metadata_or_final_text(self):
        for name in ("proposal.patch", "proposal.json", "final-response.txt"):
            path = self.b.attempt / name
            original = path.read_bytes()
            path.write_bytes(original + b"changed")
            with self.subTest(name=name), self.assertRaises((artifact.ArtifactError, ValueError)):
                self.f.seal()
            path.write_bytes(original)
        self.b.proposal = replace(self.b.proposal, patch=self.b.patch + "changed")
        with self.assertRaisesRegex(artifact.ArtifactError, "returned proposal altered"):
            self.f.seal()

    def test_provider_text_must_match_exact_actual_SDK_final_text(self):
        self.f.text += " "
        self.f.financial_wire()
        with self.assertRaisesRegex(artifact.ArtifactError, "SSE text differs"):
            self.f.seal()

    def test_extra_model_turn_or_tool_event_or_length_is_not_one_response(self):
        original = copy.deepcopy(self.b.events)
        for change in ("extra", "tool", "length"):
            self.b.events = copy.deepcopy(original)
            if change == "extra":
                self.b.events.insert(0, copy.deepcopy(original[0]))
            elif change == "tool":
                self.b.events.insert(0, {"type": "tool_execution_start"})
            else:
                self.b.events[0]["message"]["stopReason"] = "length"
            self.b.write_events()
            with self.subTest(change=change), self.assertRaises(artifact.ArtifactError):
                self.f.seal()

    def test_wrong_prompt_profile_or_actual_provider_cap_is_rejected(self):
        context = self.b.context_value()
        context.profile = {"different": "reference profile"}
        with self.assertRaises(artifact.ArtifactError):
            self.f.seal(proposal_context=context)
        receipt = self.b.receipts[0]
        j = json.loads(receipt.read_bytes())
        j["requested_output_cap"] = 8192
        receipt.write_text(json.dumps(j))
        with self.assertRaisesRegex(artifact.ArtifactError, "actual provider response/output"):
            self.f.seal()

    def test_structured_model_output_is_not_repaired(self):
        for text in (
            "```json\n" + self.f.text + "\n```",
            self.f.text + " commentary",
            '{"experiment":"a","experiment":"b"}',
            "{}",
        ):
            with self.subTest(text=text[:40]), self.assertRaises(ValueError):
                one.parse_response(text)

    def test_prelaunch_prompt_and_filesystem_failures_have_known_zero_cost(self):
        for error in ("prompt", "filesystem", "deadline"):
            with (
                self.subTest(error=error),
                mock.patch.dict(os.environ, {"FIXTURE_KEY": "local-only"}),
            ):
                proposer = one.OneResponsePiProposer(
                    PiLaunchConfig(
                        pi_bin=Path("unused-official-cli"),
                        agent_dir=self.b.root,
                        api_key_env="FIXTURE_KEY",
                        timeout_s=300,
                    ),
                    profile=self.b.profile,
                )
                context = self.b.context_value()
                if error == "prompt":
                    context.program_md = "oversized" * 10000
                if error == "deadline":
                    context.timeout_s = 60
                with mock.patch.object(one.procs, "run") as launch:
                    if error == "filesystem":
                        with mock.patch.object(
                            one.tempfile, "mkdtemp", side_effect=OSError("fixture")
                        ):
                            with self.assertRaises(ProposerError) as failure:
                                proposer.propose(context)
                    else:
                        with self.assertRaises(ProposerError) as failure:
                            proposer.propose(context)
                self.assertEqual(failure.exception.api_usd, 0)
                self.assertTrue(proposer.exhausted())
                launch.assert_not_called()

    def test_one_response_model_configuration_does_not_mutate_four_tool_configurations(self):
        before = one.model_config("http://127.0.0.1:1/v1", "glm-5.2", "FIXTURE_KEY")
        single = one.one_response_model_config("http://127.0.0.1:1/v1", "glm-5.2", "FIXTURE_KEY")
        after = one.model_config("http://127.0.0.1:1/v1", "glm-5.2", "FIXTURE_KEY")

        def get(config):
            return config["providers"]["abstract-preflight"]["models"][0]

        self.assertEqual(get(before)["samplingParams"]["tool_choice"], "auto")
        self.assertEqual(get(after)["samplingParams"]["tool_choice"], "auto")
        self.assertEqual(get(single)["samplingParams"]["tool_choice"], "none")
        get(single)["samplingParams"]["thinking"]["type"] = "fixture-mutation"
        self.assertEqual(get(before)["samplingParams"]["thinking"], {"type": "disabled"})
        self.assertEqual(get(after)["maxTokens"], 8192)

    def test_failed_one_response_receipt_and_mode_are_retained_by_manifest(self):
        harness = artifact_tests.make_harness(self.b.root / "manifest-harness")
        work = harness.config.state_dir / "proposer"
        work.mkdir()
        proposer = one.OneResponsePiProposer(
            PiLaunchConfig(
                pi_bin=Path("unused-official-cli"),
                agent_dir=self.b.root,
                api_key_env="FIXTURE_KEY",
                timeout_s=300,
            ),
            profile=self.b.profile,
        )
        harness.proposer = proposer
        harness.engine = SimpleNamespace(
            name="llmd",
            spec=SimpleNamespace(
                platform="linux/amd64",
                dockerfile=self.b.context / artifact.DOCKERFILE,
                build_context=self.b.context,
                model_args=[],
                base_image="sha256:" + "d" * 64,
            ),
        )
        context = self.b.context_value()
        context.work_dir = work
        context.program_md = "oversized" * 10000
        try:
            with mock.patch.dict(os.environ, {"FIXTURE_KEY": "local-only"}):
                with self.assertRaises(ProposerError):
                    proposer.propose(context)
            statuses = list(work.glob("pi-attempt-*/attempt-status.json"))
            self.assertEqual(len(statuses), 1)
            with mock.patch.object(manifest, "run_integrity_check", return_value=(0, "fixture")):
                saved = manifest.write_manifest(harness)
            self.assertEqual(
                saved["evidence"]["agent_attempt_statuses"], [harness.config.rel(statuses[0])]
            )
            provenance = json.loads(
                (harness.config.reports_dir / "agent-provenance.json").read_bytes()
            )
            self.assertEqual(provenance["execution_policy"], artifact.ONE_RESPONSE_POLICY)
            self.assertNotIn("ScriptedProposer", provenance["note"])
            self.assertFalse(provenance["deferred_Pi_proposal"])
        finally:
            harness._release_locks()

    def test_deferred_manifest_keeps_carried_cost_without_claiming_scripted_zero_or_live_Pi(self):
        pin = self.f.seal()
        adapter = self.b.adapter(pin)
        proposal = adapter.propose(self.b.context_value())
        harness = artifact_tests.make_harness(self.b.root / "deferred-manifest")
        harness.proposer = adapter
        harness.state.api_usd_cumulative = proposal.api_usd
        harness.engine = SimpleNamespace(
            name="llmd",
            spec=SimpleNamespace(
                platform="linux/amd64",
                dockerfile=self.b.context / artifact.DOCKERFILE,
                build_context=self.b.context,
                model_args=[],
                base_image="sha256:" + "d" * 64,
            ),
        )
        try:
            with mock.patch.object(manifest, "run_integrity_check", return_value=(0, "fixture")):
                saved = manifest.write_manifest(harness)
            provenance = json.loads(
                (harness.config.reports_dir / "agent-provenance.json").read_bytes()
            )
            self.assertGreater(saved["evidence"]["api_usd_cumulative"], 0)
            self.assertTrue(provenance["deferred_Pi_proposal"])
            self.assertFalse(provenance["pi_used_for_these_rows"])
            self.assertNotIn("ScriptedProposer", provenance["note"])
            self.assertEqual(provenance["execution_policy"], artifact.ONE_RESPONSE_POLICY)
        finally:
            harness._release_locks()


ASSETS = ("ABSTRACT_PI_SMOKE_NODE", "ABSTRACT_PI_SMOKE_CLI", "ABSTRACT_PI_SMOKE_PACKAGE")


@unittest.skipUnless(
    sys.platform == "linux" and all(os.environ.get(k) for k in ASSETS),
    "official stock-Pi assets absent; no inference fallback",
)
class OfficialCliOneResponseTest(unittest.TestCase):
    def test_actual_unmodified_CLI_no_tools_one_request_wire_and_artifact(self):
        f = Fixture(self)
        b = f.base

        class Server(ThreadingHTTPServer):
            requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.server.requests.append(body)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(f.body)
                self.wfile.flush()

        server = Server(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        node, cli, package = (Path(os.environ[k]).resolve() for k in ASSETS)
        self.assertEqual(stock_distribution(cli, package), 1243)
        agent = b.root / "one-response-agent"
        agent.mkdir()
        (agent / "models.json").write_text(
            json.dumps(
                one.one_response_model_config(
                    f"http://127.0.0.1:{server.server_port}/v1",
                    "glm-5.2",
                    "ABSTRACT_PROTOCOL_FIXTURE_KEY",
                )
            )
        )
        (agent / "settings.json").write_text(
            json.dumps(
                {
                    "retry": {"enabled": False, "maxRetries": 0},
                    "compaction": {"enabled": False},
                    "cacheWarming": "off",
                }
            )
        )
        proposer = one.OneResponsePiProposer(
            PiLaunchConfig(
                pi_bin=cli,
                agent_dir=agent,
                api_key_env="ABSTRACT_PROTOCOL_FIXTURE_KEY",
                model="glm-5.2",
                provider="abstract-preflight",
                timeout_s=300,
            ),
            profile=b.profile,
            node=node,
        )
        original = os.environ.get("ABSTRACT_PROTOCOL_FIXTURE_KEY")
        os.environ["ABSTRACT_PROTOCOL_FIXTURE_KEY"] = "synthetic-loopback-only-key"
        try:
            self.assertEqual(proposer.verify_distribution(), "1.0.0")
            b.proposal = proposer.propose(b.context_value())
            with self.assertRaisesRegex(ProposerError, "already used"):
                proposer.propose(b.context_value())
        finally:
            if original is None:
                os.environ.pop("ABSTRACT_PROTOCOL_FIXTURE_KEY", None)
            else:
                os.environ["ABSTRACT_PROTOCOL_FIXTURE_KEY"] = original
        self.assertEqual(len(server.requests), 1)
        wire = server.requests[0]
        self.assertFalse(wire.get("tools"))
        self.assertEqual(wire["tool_choice"], "none")
        self.assertEqual(wire["max_tokens"], 2048)
        self.assertEqual(wire["thinking"], {"type": "disabled"})
        self.assertEqual(wire["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(b.proposal.patch, b.patch)
        b.attempt = Path(b.proposal.transcript_path).parent
        b.transcript = Path(b.proposal.transcript_path)
        pin = f.seal(purpose="test-only-compatibility-fixture")
        sealed = artifact.verify_pi_artifact(b.output, pin, b.binding)
        self.assertEqual(sealed["financial"]["undiscounted_upper_USD"], "0.000228")
        with self.assertRaisesRegex(artifact.ReplayArtifactError, "test-only"):
            b.adapter(pin).propose(b.context_value())
        if os.environ.get("ABSTRACT_PI_ONE_RESPONSE_WIRE_OUT"):
            Path(os.environ["ABSTRACT_PI_ONE_RESPONSE_WIRE_OUT"]).write_bytes(
                artifact.canonical(wire)
            )
