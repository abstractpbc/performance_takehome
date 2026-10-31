"""Actual patch-to-build policy ordering; all Docker and measurement calls fake."""

from __future__ import annotations

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from abstract_harness import configuration_delivery as policy
from abstract_harness import loop, measure, results
from abstract_harness.decision import Decision, MetricComparison
from abstract_harness.docker_runtime import BuildResult
from abstract_harness.proposers import Proposal, unified_diff_for_dirs
from abstract_harness.state import ImageRef, StateError, load_state

from .test_recovery import make_harness

H = Path(__file__).resolve().parents[1]
BASE = (H.parent / "Dockerfile").read_text(encoding="utf-8")
DF = Path("tasks/harness-design/Dockerfile")


def put(root, text):
    path = root / DF
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


class PolicyCases(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="delivery-policy-")
        self.addCleanup(self.tempdir.cleanup)
        self.tmp = Path(self.tempdir.name)
        self.ctx = self.tmp / "context"
        put(self.ctx, BASE)
        (self.ctx / "README.md").write_text("Unchanged context input.\n", encoding="utf-8")
        self.h = make_harness(self.tmp)
        self.h._release_locks()
        self.h.config.delivery_policy = policy.POLICY
        self.h = loop.Harness(
            self.h.config,
            SimpleNamespace(
                name="llmd", spec=SimpleNamespace(build_context=self.ctx, dockerfile=self.ctx / DF)
            ),
            self.h.proposer,
            log=lambda _: None,
        )
        self.addCleanup(self.h._release_locks)
        prior = ImageRef("old:good", "sha256:" + "a" * 64)
        self.h.state.last_good = prior
        self.h.state.attempt_index = 1
        self.h._append_row(
            experiment="baseline",
            outcome="baseline",
            kept="1",
            image_digest=prior.digest,
            development_report="baseline.json",
        )
        traces = measure.Measurement("CLOSED-model", 0.1, 0.0, [], [], [], [], True)
        comparisons = (
            MetricComparison("ttft_s", 5, 5, 1.0, 0.5, 2.0, 2.0, 2.0, "improved"),
            MetricComparison("ftgr_tok_s", 5, 5, 1.0, 2.0, 2.0, 2.0, 2.0, "improved"),
        )
        self.build = mock.patch.object(
            self.h,
            "build",
            return_value=BuildResult(
                "CLOSED:candidate",
                True,
                False,
                0.001,
                "sha256:" + "b" * 64,
                "sha256:" + "b" * 64,
                self.tmp / "fake-build.log",
            ),
        ).start()
        self.measure = mock.patch.object(
            self.h, "measure_image", return_value=(traces, {"fixture": True})
        ).start()
        self.addCleanup(mock.patch.stopall)
        mock.patch.object(
            loop,
            "decide",
            return_value=Decision(
                "improved", True, "CLOSED stipulated gates and performance", comparisons
            ),
        ).start()
        mock.patch.object(loop.docker, "image_exists", return_value=False).start()
        mock.patch.object(loop.docker, "remove_image").start()
        mock.patch.object(loop.docker, "retag").start()

    def apply(self, transform, *, other=False):
        original = self.h.last_good_context if self.h.last_good_context.exists() else self.ctx
        scratch = self.tmp / ("proposal" + str(self.h.state.attempt_index))
        shutil.copytree(original, scratch)
        put(scratch, transform((scratch / DF).read_text(encoding="utf-8")))
        if other:
            (scratch / "README.md").write_text("Altered unrelated context.\n", encoding="utf-8")
        patch = unified_diff_for_dirs(original, scratch)
        index = self.h.state.attempt_index
        outcome = self.h.attempt(
            Proposal("case" + str(index), "CLOSED no model or performance", patch)
        )
        reports = sorted(self.h.config.reports_dir.glob("*.json"))
        report = json.loads(reports[-1].read_bytes())
        return (outcome, report)

    def reject(self, transform, *, other=False):
        old = self.h.state.last_good
        outcome, report = self.apply(transform, other=other)
        self.assertEqual(outcome, "error")
        self.build.assert_not_called()
        self.measure.assert_not_called()
        self.assertEqual(self.h.state.last_good, old)
        self.assertEqual(report["delivery_eligibility"]["status"], "REJECTED_BEFORE_BUILD")
        self.assertIn("error", report)
        self.assertTrue(list(Path(self.h.config.patches_dir).glob("*.patch")))
        row = results.validate_file(self.h.config.results_path)[-1]
        self.assertEqual((row.outcome, row.kept), ("error", "0"))

    def accept(self, transform):
        with mock.patch.object(loop.docker, "image_exists", return_value=True):
            outcome, report = self.apply(transform)
        self.assertEqual(outcome, "improved")
        self.assertEqual(report["delivery_eligibility"]["status"], "ELIGIBLE_BEFORE_BUILD")
        self.assertEqual(len(report["delivery_eligibility"]["changed_knobs"]), 1)
        return report

    def test_copy_rejected_before_any_build(self):
        self.reject(
            lambda t: t.replace("FROM ${BASE_IMAGE}", "FROM ${BASE_IMAGE}\nCOPY README.md /extra")
        )

    def test_run_rejected_before_any_build(self):
        self.reject(lambda t: t.replace("FROM ${BASE_IMAGE}", "FROM ${BASE_IMAGE}\nRUN false"))

    def test_other_context_edit_rejected(self):
        self.reject(lambda t: t.replace("--batch-size=16", "--batch-size=32"), other=True)

    def test_unknown_flag_rejected(self):
        self.reject(lambda t: t.replace("--batch-size=16", "--unknown=32"))

    def test_contract_change_rejected(self):
        self.reject(lambda t: t.replace("--max-context-len=128000", "--max-context-len=64000"))

    def test_malformed_flag_rejected(self):
        self.reject(lambda t: t.replace("--batch-size=16", "--batch-size=NaN"))

    def test_extreme_decimal_exponent_is_normal_rejection(self):
        self.reject(
            lambda t: t.replace(
                "--gpu-memory-fraction=0.9", "--gpu-memory-fraction=1e99999999999999999999999999"
            )
        )

    def test_external_drafter_rejected(self):
        self.reject(
            lambda t: t.replace(
                "--batch-size=16", "--speculative-drafter-model=https://outside.invalid/model"
            )
        )

    def test_two_simultaneous_knobs_rejected(self):
        self.reject(
            lambda t: t.replace("--batch-size=16", "--batch-size=32").replace(
                "--page-chunk-size=16", "--page-chunk-size=32"
            )
        )

    def test_comment_only_no_semantic_change_rejected(self):
        self.reject(lambda t: t + "\n# No tuning change.\n")

    def test_new_native_flag_accepted(self):
        self.accept(
            lambda t: t.replace(
                '--cache-memory-fraction=0.95"]',
                '--cache-memory-fraction=0.95", "--speculative-drafter-model=native"]',
            )
        )

    def test_two_sequential_single_knob_keeps_are_eligible(self):
        self.accept(lambda t: t.replace("--batch-size=16", "--batch-size=32"))
        self.accept(lambda t: t.replace("--page-chunk-size=16", "--page-chunk-size=32"))
        text = (self.h.last_good_context / DF).read_text(encoding="utf-8")
        self.assertIn("--batch-size=32", text)
        self.assertIn("--page-chunk-size=32", text)
        self.assertEqual(self.build.call_count, 2)

    def test_one_telemetry_variable_accepted(self):
        self.accept(lambda t: t.replace('# ENV LLMD_POSTHOG_TOKEN=""', 'ENV LLMD_POSTHOG_TOKEN=""'))

    def test_two_telemetry_variables_rejected(self):
        self.reject(
            lambda t: t.replace('# ENV LLMD_POSTHOG_TOKEN=""', 'ENV LLMD_POSTHOG_TOKEN=""').replace(
                '# ENV LLMD_USAGE_STATS_ENDPOINT=""', 'ENV LLMD_USAGE_STATS_ENDPOINT=""'
            )
        )

    def test_default_generic_mode_does_not_run_guard(self):
        self.h.config.delivery_policy = "none"
        with mock.patch.object(loop.docker, "image_exists", return_value=True):
            outcome, report = self.apply(
                lambda t: t.replace(
                    "FROM ${BASE_IMAGE}", "FROM ${BASE_IMAGE}\nCOPY README.md /extra"
                )
            )
        self.assertEqual(outcome, "improved")
        self.build.assert_called_once()
        self.assertNotIn("delivery_eligibility", report)

    def test_policy_persisted_and_cannot_be_disabled_on_resume(self):
        self.h._save()
        cfg = self.h.config
        proposer = self.h.proposer
        engine = self.h.engine
        self.h._release_locks()
        self.assertEqual(load_state(self.h.state_path).delivery_policy, policy.POLICY)
        cfg.delivery_policy = "none"
        with self.assertRaisesRegex(StateError, "delivery policy differs"):
            loop.Harness(cfg, engine, proposer, log=lambda _: None)

    def test_invalid_initial_source_refused_before_baseline(self):
        self.h._release_locks()
        put(
            self.ctx,
            BASE.replace("FROM ${BASE_IMAGE}", "FROM ${BASE_IMAGE}\nCOPY README.md /extra"),
        )
        with self.assertRaisesRegex(ValueError, "configuration-only"):
            loop.Harness(self.h.config, self.h.engine, self.h.proposer, log=lambda _: None)

    def test_cli_default_and_explicit_option(self):
        spec = importlib.util.spec_from_file_location("candidate_harness_cli", H / "run.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        args = [
            "run",
            "--engine",
            "llmd",
            "--model-dir",
            "/CLOSED/model",
            "--state-dir",
            "/CLOSED/state",
            "--out",
            "/CLOSED/out",
            "--budget-minutes",
            "60",
            "--api-spend-limit-usd",
            "0",
        ]
        self.assertEqual(m.build_parser().parse_args(args).delivery_policy, "none")
        self.assertEqual(
            m.build_parser()
            .parse_args(args + ["--delivery-policy", policy.POLICY])
            .delivery_policy,
            policy.POLICY,
        )

    def test_unknown_policy_refused_before_baseline(self):
        self.h._release_locks()
        self.h.config.delivery_policy = "unknown-mode"
        with self.assertRaisesRegex(ValueError, "unknown delivery policy"):
            loop.Harness(self.h.config, self.h.engine, self.h.proposer, log=lambda _: None)

    def test_reserved_parser_directives_rejected_before_any_build(self):
        for line in (
            "# syntax=unapproved.invalid/frontend:latest",
            "# escape=`",
            "# check=skip=all",
            "#SyNtAx = docker/dockerfile:1",
            "  # \t EsCaPe \t = \\",
            "# CHECK = error=true",
        ):
            with self.subTest(directive=line):
                self.reject(
                    lambda text, line=line: (
                        line + "\n" + text.replace("--batch-size=16", "--batch-size=32")
                    )
                )

    def test_late_reserved_directive_rejected_conservatively(self):
        self.reject(
            lambda text: (
                text.replace("--batch-size=16", "--batch-size=32")
                + "\n# syntax=unapproved.invalid/frontend:latest\n"
            )
        )

    def test_ordinary_comment_with_one_knob_is_eligible(self):
        self.accept(
            lambda text: (
                "# ordinary note; no parser directive\n"
                + text.replace("--batch-size=16", "--batch-size=32")
            )
        )
