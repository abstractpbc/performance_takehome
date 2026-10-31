"""Named profile validation and actual common-path serialized wire regressions."""

import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace

import httpx
from bench.plan import ASSISTANT_PREFILL_PROFILE, PlanError, load_plan, resolve_mode, validate_plan
from bench.prompts import make_prompt
from runner.run import RequestMeta, Runner, build_body

from tests.helpers import SUITE, chunk
from tests.test_mock_server import runner_args


def workload():
    return load_plan(SUITE / "workload.efficient.json")


def typed_specs(plan):
    """Same four public generators/caps as the root's live prefreeze probe."""
    return [
        (
            "startup_probe",
            "startup",
            replace(make_prompt(plan, "calibration_short", "profile-startup", 0, 0), max_tokens=16),
        ),
        (
            "measure",
            "calibration",
            make_prompt(plan, "rag_long", "profile-rag", 0, 0, forced_prompt_tokens=4096),
        ),
        ("warmup", "calibration", make_prompt(plan, "calibration_short", "profile-short", 0, 0)),
        ("measure", "measure", make_prompt(plan, "generation_long", "profile-generation", 0, 0)),
    ]


class AssistantPrefillProfileTests(unittest.TestCase):
    def test_only_named_fixed_profile_and_no_conflicting_body_overrides(self):
        plan = workload()
        self.assertEqual(plan["output_policy"]["request_profile"], "llmd_closed_thinking_v1")
        self.assertEqual(ASSISTANT_PREFILL_PROFILE, "llmd_closed_thinking_v1")
        spec = typed_specs(plan)[0][2]
        for profile in (None, False, 0, {}, [], "unknown", "llmd_closed_thinking_v2"):
            changed = copy.deepcopy(plan)
            changed["output_policy"]["request_profile"] = profile
            with self.subTest(profile=profile), self.assertRaises(PlanError):
                validate_plan(changed)
            with self.subTest(body_profile=profile), self.assertRaises(PlanError):
                build_body(changed, spec, "actual-model")
        for key, value in (
            ("messages", []),
            ("model", "other"),
            ("max_tokens", 1024),
            ("continue_final_message", True),
            ("add_generation_prompt", False),
        ):
            changed = copy.deepcopy(plan)
            changed["output_policy"][key] = value
            with self.subTest(key=key), self.assertRaises(PlanError):
                validate_plan(changed)
        for options in (
            {"enable_thinking": False},
            {"chat_template_kwargs": {"enable_thinking": False}},
            {"continue_final_message": True},
        ):
            changed = copy.deepcopy(plan)
            changed["output_policy"]["request_options"] = options
            with self.subTest(options=options), self.assertRaises(PlanError):
                validate_plan(changed)

    def test_typed_body_matches_exact_successful_diagnostic_shape_without_mutation(self):
        plan = workload()
        before = copy.deepcopy(plan)
        for _, _, spec in typed_specs(plan):
            original = copy.deepcopy(spec.messages)
            expected = {
                "model": "actual-model",
                "messages": [*original, {"role": "assistant", "content": "</think>\n\n"}],
                "max_tokens": spec.max_tokens,
                "temperature": 0,
                "stream": True,
                "stream_options": {"include_usage": True},
                "continue_final_message": True,
                "add_generation_prompt": False,
            }
            body = build_body(plan, spec, "actual-model")
            self.assertEqual(body, expected)
            self.assertEqual(spec.messages, original)
            body["messages"][-1]["content"] = "not retained"
            body["messages"][0]["content"] = "not retained"
            self.assertEqual(build_body(plan, spec, "actual-model"), expected)
        self.assertEqual(plan, before)

    def test_generation_caps_and_full_design_coverage_remain_explicit(self):
        plan = workload()
        self.assertEqual(plan["startup"]["probe_max_tokens"], 16)
        self.assertEqual(
            {
                name: cfg["max_tokens"]
                for name, cfg in plan["prompt_sources"].items()
                if "max_tokens" in cfg
            },
            {"chat_short": 256, "rag_long": 128, "generation_long": 1280, "calibration_short": 16},
        )
        full = resolve_mode(plan, "full")
        self.assertEqual(len(full), 42)
        self.assertEqual(sum(unit.requests for unit in full), 1455)
        self.assertEqual(plan["modes"]["full"]["repetitions"], 3)
        self.assertEqual(plan["modes"]["full"]["startup_launches"], 2)
        self.assertTrue(all(unit.requests >= 30 for unit in full))
        high = [unit for unit in full if unit.cell_id == "closed_loop_sweep" and unit.load == 32]
        self.assertEqual(len(high), 3)
        self.assertTrue(all(unit.requests == 48 for unit in high))


class AssistantPrefillWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_warmup_calibration_and_scoring_use_identical_profile_and_actual_hash(
        self,
    ):
        plan = workload()
        sent = []
        wire = (
            "data: "
            + chunk("synthetic wire response")
            + "\n\n"
            + "data: "
            + chunk(finish="stop")
            + "\n\n"
            + "data: [DONE]\n\n"
        ).encode()

        def respond(request):
            sent.append(request.content)
            return httpx.Response(200, content=wire, headers={"content-type": "text/event-stream"})

        with tempfile.TemporaryDirectory() as temp:
            runner = Runner(runner_args(out=temp, mode="calibration"), plan)
            runner.model_id = "actual-model"
            unit = resolve_mode(plan, "calibration")[0]
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                for phase, role, spec in typed_specs(plan):
                    record = await runner.execute(
                        client,
                        spec,
                        RequestMeta(
                            "profile/" + spec.prompt_id,
                            None if phase == "startup_probe" else unit,
                            phase,
                            role,
                            0,
                        ),
                    )
                    expected = json.dumps(
                        build_body(plan, spec, "actual-model"), ensure_ascii=False, sort_keys=True
                    ).encode()
                    self.assertEqual(sent[-1], expected)
                    self.assertEqual(
                        record["request_body_sha256"], hashlib.sha256(sent[-1]).hexdigest()
                    )
                    self.assertEqual(record["phase"], phase)
                    self.assertIsNone(record["error"])
                    body = json.loads(sent[-1])
                    self.assertEqual(
                        body["messages"][-1], {"role": "assistant", "content": "</think>\n\n"}
                    )
                    self.assertEqual(body["max_tokens"], spec.max_tokens)
            self.assertEqual(len(sent), 4)


if __name__ == "__main__":
    unittest.main()
