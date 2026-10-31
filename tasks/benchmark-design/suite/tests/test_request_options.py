"""Fixed request-policy validation and actual outgoing wire/hash regressions.

Synthetic HTTPX transport proves forwarding, not that a real server honors a knob.
"""

import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import httpx
from bench.plan import PlanError, load_plan, plan_digest, resolve_mode, validate_plan
from bench.prompts import PromptSpec
from runner.run import RequestMeta, Runner, build_body

from tests.helpers import SUITE, chunk
from tests.test_mock_server import runner_args


def workload():
    plan = load_plan(SUITE / "workload.efficient.json")
    # These regressions exercise the backward-compatible profile-absent path.
    plan["output_policy"].pop("request_profile", None)
    return plan


def prompt():
    return PromptSpec(
        "fixed",
        "chat_short",
        "synthetic_chat",
        [{"role": "user", "content": "Begin with CODE-ABCD-1234."}],
        32,
        12,
        {"echo_nonce": True, "nonce": "CODE-ABCD-1234"},
    )


def body_bytes(body):
    return json.dumps(body, ensure_ascii=False, sort_keys=True).encode()


class FixedRequestOptionsTests(unittest.TestCase):
    def test_absent_or_empty_options_preserve_the_original_body(self):
        plan = workload()
        expected = {
            "model": "target-model",
            "messages": prompt().messages,
            "max_tokens": 32,
            "temperature": 0,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        original = body_bytes(build_body(plan, prompt(), "target-model"))
        self.assertEqual(original, body_bytes(expected))
        plan["output_policy"]["request_options"] = {}
        validate_plan(plan)
        self.assertEqual(body_bytes(build_body(plan, prompt(), "target-model")), original)

    def test_supported_fixed_boolean_representations_are_forwarded(self):
        for value in (False, True):
            for options in (
                {"enable_thinking": value},
                {"chat_template_kwargs": {"enable_thinking": value}},
                {
                    "enable_thinking": value,
                    "chat_template_kwargs": {"enable_thinking": value},
                },
            ):
                with self.subTest(options=options):
                    plan = workload()
                    plan["output_policy"]["request_options"] = options
                    validate_plan(plan)
                    body = build_body(plan, prompt(), "target-model")
                    self.assertEqual({key: body[key] for key in options}, options)
                    self.assertEqual(body["model"], "target-model")
                    self.assertEqual(body["messages"], prompt().messages)
                    self.assertEqual(body["max_tokens"], 32)
                    self.assertIs(body["stream"], True)

    def test_request_overrides_and_unknown_nested_fields_are_rejected(self):
        for key in (
            "model",
            "messages",
            "max_tokens",
            "temperature",
            "stream",
            "stream_options",
            "stop",
            "unknown_server_option",
        ):
            plan = workload()
            plan["output_policy"]["request_options"] = {key: False}
            with self.subTest(key=key), self.assertRaises(PlanError):
                validate_plan(plan)
            with self.subTest(body_key=key), self.assertRaises(PlanError):
                build_body(plan, prompt(), "target-model")
        plan = workload()
        plan["output_policy"]["request_options"] = {
            "chat_template_kwargs": {"enable_thinking": False, "unknown": False}
        }
        with self.assertRaises(PlanError):
            validate_plan(plan)
        with self.assertRaises(PlanError):
            build_body(plan, prompt(), "target-model")

    def test_malformed_or_conflicting_controls_are_rejected(self):
        invalid = [None, [], "false", {"chat_template_kwargs": {}}, {"chat_template_kwargs": []}]
        for value in (None, 0, 1, 0.0, "false", [], {}):
            invalid.extend(
                [
                    {"enable_thinking": value},
                    {"chat_template_kwargs": {"enable_thinking": value}},
                ]
            )
        invalid.append(
            {"enable_thinking": True, "chat_template_kwargs": {"enable_thinking": False}}
        )
        for options in invalid:
            with self.subTest(options=options):
                plan = workload()
                plan["output_policy"]["request_options"] = options
                with self.assertRaises(PlanError):
                    validate_plan(plan)
                with self.assertRaises(PlanError):
                    build_body(plan, prompt(), "target-model")

    def test_body_options_do_not_mutate_the_frozen_plan(self):
        plan = workload()
        plan["output_policy"]["request_options"] = {
            "chat_template_kwargs": {"enable_thinking": False}
        }
        original = copy.deepcopy(plan)
        body = build_body(plan, prompt(), "target-model")
        body["chat_template_kwargs"]["enable_thinking"] = True
        self.assertEqual(plan, original)
        self.assertEqual(
            build_body(plan, prompt(), "target-model")["chat_template_kwargs"],
            {"enable_thinking": False},
        )

    def test_fixed_policy_changes_the_plan_and_request_hashes(self):
        plan = workload()
        with tempfile.TemporaryDirectory() as tmp:
            before = Path(tmp) / "before.json"
            after = Path(tmp) / "after.json"
            before.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
            first = hashlib.sha256(
                body_bytes(build_body(plan, prompt(), "target-model"))
            ).hexdigest()
            plan["output_policy"]["request_options"] = {"enable_thinking": False}
            after.write_text(json.dumps(plan, sort_keys=True), encoding="utf-8")
            second = hashlib.sha256(
                body_bytes(build_body(plan, prompt(), "target-model"))
            ).hexdigest()
            self.assertNotEqual(plan_digest(before), plan_digest(after))
            self.assertNotEqual(first, second)


class FixedRequestWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_request_phases_send_the_fixed_policy_and_record_actual_wire_hash(self):
        plan = workload()
        options = {"chat_template_kwargs": {"enable_thinking": False}}
        plan["output_policy"]["request_options"] = options
        captured = []
        wire = (
            "data: " + chunk("CODE-ABCD-1234\nsynthetic transport response") + "\n\n"
            "data: " + chunk(finish="stop") + "\n\n"
            "data: [DONE]\n\n"
        ).encode()

        def respond(request):
            captured.append(request.content)
            return httpx.Response(200, content=wire, headers={"content-type": "text/event-stream"})

        with tempfile.TemporaryDirectory() as tmp:
            runner = Runner(runner_args(out=tmp, mode="calibration"), plan)
            runner.model_id = "target-model"
            unit = resolve_mode(plan, "calibration")[0]
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                for phase, role, maximum in (
                    ("startup_probe", "startup", 32),
                    ("warmup", "calibration", 16),
                    ("measure", "calibration", 128),
                    ("measure", "measure", 1024),
                ):
                    with self.subTest(phase=phase, role=role):
                        spec = replace(prompt(), max_tokens=maximum)
                        record = await runner.execute(
                            client,
                            spec,
                            RequestMeta(
                                f"wire/{phase}/{role}",
                                None if phase == "startup_probe" else unit,
                                phase,
                                role,
                                0,
                            ),
                        )
                        sent = json.loads(captured[-1])
                        self.assertEqual(
                            sent["chat_template_kwargs"], options["chat_template_kwargs"]
                        )
                        self.assertEqual(sent["max_tokens"], maximum)
                        self.assertEqual(sent["messages"], spec.messages)
                        self.assertEqual(sent["model"], "target-model")
                        self.assertIs(sent["stream"], True)
                        self.assertEqual(
                            record["request_body_sha256"], hashlib.sha256(captured[-1]).hexdigest()
                        )
                        self.assertEqual(record["phase"], phase)
                        self.assertIsNone(record["error"])
            self.assertEqual(len(captured), 4)


if __name__ == "__main__":
    unittest.main()
