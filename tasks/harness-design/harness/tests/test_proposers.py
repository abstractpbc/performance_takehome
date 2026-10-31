"""Proposer contract: scripted queue, patch application, Pi isolation and offline behaviour."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from abstract_harness import proposers
from abstract_harness.proposers import (
    PI_ISOLATION_FLAGS,
    PI_PACKAGE,
    PI_STOCK_TOOLS,
    PI_VERSION,
    PiLaunchConfig,
    PiProposer,
    ProposerAccountingError,
    ProposerContext,
    ProposerError,
    ScriptedProposer,
    apply_patch,
)

from . import _paths

PROFILES = json.loads((_paths.REPO_ROOT / "agent-harness" / "profiles.json").read_text("utf-8"))


def _context(context_dir: Path, work_dir: Path) -> ProposerContext:
    return ProposerContext(
        program_md="# program",
        profile={"name": "test"},
        history=[],
        context_dir=context_dir,
        work_dir=work_dir,
        engine="standin",
    )


class ScriptedProposerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ctx_dir = self.root / "context"
        self.ctx_dir.mkdir()
        (self.ctx_dir / "Dockerfile").write_text("ARG THREADS=4\nFROM scratch\n", encoding="utf-8")
        self.work = self.root / "work"
        self.work.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_queue_yields_real_unified_diffs_in_order_then_none(self) -> None:
        queue = [
            {
                "experiment": "threads-8",
                "hypothesis": "more threads",
                "edits": [{"file": "Dockerfile", "find": "THREADS=4", "replace": "THREADS=8"}],
            },
            {
                "experiment": "threads-any",
                "hypothesis": "regex",
                "edits": [
                    {
                        "file": "Dockerfile",
                        "regex": True,
                        "find": r"THREADS=\d+",
                        "replace": "THREADS=1",
                    }
                ],
            },
        ]
        proposer = ScriptedProposer(queue)
        ctx = _context(self.ctx_dir, self.work)
        first = proposer.propose(ctx)
        assert first is not None
        self.assertIn("threads-8", first.experiment)
        self.assertIn("-ARG THREADS=4", first.patch)
        self.assertIn("+ARG THREADS=8", first.patch)
        ok, _ = apply_patch(first.patch, self.ctx_dir)
        self.assertTrue(ok)
        self.assertIn("THREADS=8", (self.ctx_dir / "Dockerfile").read_text())
        second = proposer.propose(ctx)
        assert second is not None
        self.assertIn("+ARG THREADS=1", second.patch)
        self.assertIsNone(proposer.propose(ctx))

    def test_edit_that_does_not_match_raises(self) -> None:
        proposer = ScriptedProposer(
            [
                {
                    "experiment": "x",
                    "hypothesis": "h",
                    "edits": [{"file": "Dockerfile", "find": "NOPE", "replace": "y"}],
                }
            ]
        )
        with self.assertRaises(ProposerError):
            proposer.propose(_context(self.ctx_dir, self.work))

    def test_edit_outside_context_is_rejected(self) -> None:
        proposer = ScriptedProposer(
            [
                {
                    "experiment": "x",
                    "hypothesis": "h",
                    "edits": [{"file": "../escape", "find": "a", "replace": "b"}],
                }
            ]
        )
        with self.assertRaises(ProposerError):
            proposer.propose(_context(self.ctx_dir, self.work))

    def test_skip_resumes_queue_position(self) -> None:
        queue = [
            {
                "experiment": f"e{i}",
                "hypothesis": "h",
                "edits": [{"file": "Dockerfile", "find": "FROM", "replace": "FROM"}],
            }
            for i in range(3)
        ]
        self.assertEqual(ScriptedProposer(queue, skip=2).remaining, 1)

    def test_apply_patch_rejects_garbage_and_traversal(self) -> None:
        ok, out = apply_patch("this is not a patch", self.ctx_dir)
        self.assertFalse(ok)
        traversal = "--- a/../evil\n+++ b/../evil\n@@ -0,0 +1 @@\n+pwned\n"
        ok, _ = apply_patch(traversal, self.ctx_dir)
        self.assertFalse(ok)
        self.assertFalse((self.root / "evil").exists())


class PiProposerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.cfg = PiLaunchConfig(
            pi_bin=root / "pi", agent_dir=root / "agent", api_key_env="HARNESS_TEST_KEY"
        )
        self.profile = PROFILES["profiles"]["harness-design"]
        self.proposer = PiProposer(self.cfg, profile=self.profile)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_pin_matches_profiles_json(self) -> None:
        self.assertEqual(PROFILES["pi"]["package"], PI_PACKAGE)
        self.assertEqual(PROFILES["pi"]["version"], PI_VERSION)
        self.assertEqual(tuple(self.profile["stock_tools"]), PI_STOCK_TOOLS)
        self.assertFalse(self.profile["inherit_personal_resources"])
        readme = (_paths.REPO_ROOT / "agent-harness" / "README.md").read_text("utf-8")
        for flag in PI_ISOLATION_FLAGS:
            self.assertIn(flag, readme, flag)

    def test_command_is_print_mode_with_exact_flags_and_tools(self) -> None:
        cmd = self.proposer.build_command(Path("/p/prompt.md"), Path("/p/session"))
        self.assertIn("--print", cmd)
        self.assertEqual(cmd[cmd.index("--tools") + 1], ",".join(PI_STOCK_TOOLS))
        for flag in PI_ISOLATION_FLAGS:
            self.assertEqual(cmd.count(flag), 1, flag)
        self.assertNotIn("--approve", cmd)
        self.assertEqual(cmd[-1], "@/p/prompt.md")

    def test_env_is_scrubbed_and_key_passed_only_by_configured_name(self) -> None:
        fake = {
            "PATH": "/usr/bin",
            "HOME": "/h",
            "HARNESS_TEST_KEY": "sk-test-not-real",
            "OTHER_API_KEY": "leak",
            "GITHUB_TOKEN": "leak",
            "AWS_SECRET_ACCESS_KEY": "leak",
            "PI_OFFLINE": "0",
        }
        with mock.patch.dict(os.environ, fake, clear=True):
            env = self.proposer.build_env()
        self.assertEqual(env.get("HARNESS_TEST_KEY"), "sk-test-not-real")
        for leaked in ("OTHER_API_KEY", "GITHUB_TOKEN", "AWS_SECRET_ACCESS_KEY"):
            self.assertNotIn(leaked, env)
        self.assertEqual(env["PI_CODING_AGENT_DIR"], str(self.cfg.agent_dir))
        self.assertTrue(env["PI_CODING_AGENT_DIR"].startswith(self.tmp.name))
        self.assertEqual(env["PI_OFFLINE"], "1")

    def test_propose_without_key_fails_fast_and_offline(self) -> None:
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin"}, clear=True):
            self.assertFalse(self.proposer.api_key_present())
            with mock.patch.object(proposers.procs, "run") as run:
                with self.assertRaises(ProposerError):
                    self.proposer.propose(_context(Path(self.tmp.name), Path(self.tmp.name)))
                run.assert_not_called()

    def test_prompt_never_contains_key_value(self) -> None:
        with mock.patch.dict(os.environ, {"HARNESS_TEST_KEY": "sk-secret-value"}, clear=False):
            root = Path(self.tmp.name)
            prompt = self.proposer.render_prompt(
                _context(root, root), root / "out.patch", root / "out.json"
            )
        self.assertNotIn("sk-secret-value", prompt)
        self.assertIn("program", prompt)

    def test_controller_supplies_current_context_and_selected_inputs(self) -> None:
        from abstract_harness.budget import BudgetVerdict
        from abstract_harness.engines import LlmdEngine
        from abstract_harness.state import ImageRef

        from .test_recovery import make_harness

        root = Path(self.tmp.name)
        harness = make_harness(root)
        try:
            harness.engine = LlmdEngine.from_lock(_paths.REPO_ROOT, root / "model")
            current = harness.last_good_context
            current.mkdir(parents=True)
            relative = Path("tasks/harness-design/Dockerfile")
            (current / relative).parent.mkdir(parents=True)
            (current / relative).write_text("FROM current-last-good\n")
            benchmark = root / "selected-benchmark.json"
            benchmark.write_text('{"selected": "external-benchmark"}')
            baseline = root / "selected-baseline.json"
            baseline.write_text('{"selected": "external-baseline"}')
            harness.config.benchmark_path = benchmark
            harness.config.baseline_path = baseline
            harness.state.last_good = ImageRef("last-good", "sha256:current")
            context = harness._proposer_context(BudgetVerdict(True, "ok", 0, 0, 1, 30, 30))
            prompt = self.proposer.render_prompt(context, root / "out.patch", root / "out.json")
            self.assertIn("FROM current-last-good", prompt)
            self.assertIn("external-benchmark", prompt)
            self.assertIn("external-baseline", prompt)
            self.assertIn("sha256:current", prompt)
            self.assertIn("--model=/models/model", prompt)
        finally:
            harness._release_locks()


if __name__ == "__main__":
    unittest.main()


FAKE_PI = Path(__file__).resolve().parent / "fixtures" / "fake_pi.py"


class FakePiEndToEndTest(unittest.TestCase):
    """PiProposer against a faithful ``pi --print --mode json`` test double (no model, no net)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.context_dir = root / "context"
        self.context_dir.mkdir()
        (self.context_dir / "Dockerfile").write_text(
            'FROM scratch\nARG GGML_LTO=OFF\nCMD ["x"]\n', encoding="utf-8"
        )
        self.work = root / "work"
        self.work.mkdir()
        self.agent_dir = root / "agent"
        self.env = mock.patch.dict(os.environ, {"HARNESS_TEST_KEY": "not-a-real-key"}, clear=False)
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def _proposer(self, mode: str, *, timeout_s: float = 20) -> PiProposer:
        # The scrubbed child env drops ambient variables, so the fixture's mode
        # switch has to travel through the one sanctioned channel: extra_env.
        cfg = PiLaunchConfig(
            pi_bin=FAKE_PI,
            agent_dir=self.agent_dir,
            api_key_env="HARNESS_TEST_KEY",
            timeout_s=timeout_s,
            provider_response_dir=self.work / "external-provider-wal",
            extra_env={"FAKE_PI_MODE": mode},
        )
        return PiProposer(cfg, profile=PROFILES["profiles"]["harness-design"])

    def _propose(self, mode: str, *, timeout_s: float = 20):
        return self._proposer(mode, timeout_s=timeout_s).propose(
            _context(self.context_dir, self.work)
        )

    def _transcript(self) -> Path:
        files = list(self.work.glob("pi-attempt-*/pi-events.jsonl"))
        self.assertEqual(len(files), 1)
        return files[0]

    def test_version_check_passes_against_pinned_string(self) -> None:
        self.assertEqual(self._proposer("ok").verify_distribution(), PI_VERSION)

    def test_ok_run_yields_one_applicable_patch_and_message_end_cost_only(self) -> None:
        proposal = self._propose("ok")
        self.assertEqual(proposal.experiment, "fake-pi-lto-toggle")
        self.assertIn("LTO", proposal.hypothesis)
        self.assertIn("+ARG GGML_LTO=ON", proposal.patch)
        self.assertAlmostEqual(proposal.api_usd, 0.0048 + 0.0075, places=9)
        fresh = Path(self.tmp.name) / "fresh"
        shutil.copytree(self.context_dir, fresh)
        ok, out = apply_patch(proposal.patch, fresh)
        self.assertTrue(ok, out)
        self.assertIn("ARG GGML_LTO=ON", (fresh / "Dockerfile").read_text("utf-8"))
        self.assertIn("ARG GGML_LTO=OFF", (self.context_dir / "Dockerfile").read_text("utf-8"))
        transcript = self._transcript().read_text("utf-8")
        self.assertEqual(json.loads(transcript.splitlines()[0])["type"], "session")
        self.assertNotIn("not-a-real-key", transcript)
        receipt = json.loads((self._transcript().parent / "attempt-status.json").read_text())
        self.assertEqual(receipt["status"], "complete")
        self.assertTrue(receipt["accounting_resolved"])
        self.assertEqual(receipt["returncode"], 0)
        prompt = self._transcript().parent / "prompt.md"
        self.assertEqual(receipt["prompt_sha256"], hashlib.sha256(prompt.read_bytes()).hexdigest())
        self.assertTrue((prompt.parent / receipt["stderr"]).is_file())
        self.assertEqual(
            receipt["provider_response_dir"], str((self.work / "external-provider-wal").resolve())
        )
        self.assertFalse((self.work / "external-provider-wal").exists())

    def test_malformed_lines_are_skipped_without_losing_cost(self) -> None:
        proposal = self._propose("malformed")
        self.assertAlmostEqual(proposal.api_usd, 0.0123, places=9)
        self.assertIn("not json at all", self._transcript().read_text("utf-8"))

    def test_model_error_is_reported_with_spend(self) -> None:
        with self.assertRaises(ProposerError) as ctx:
            self._propose("error")
        self.assertIn("insufficient_quota", str(ctx.exception))
        self.assertEqual(ctx.exception.api_usd, 0.0)
        self.assertIsInstance(ctx.exception, ProposerAccountingError)

    def test_binary_change_without_patch_file_keeps_spend(self) -> None:
        """The directory-diff fallback must keep the model spend when it rejects a binary edit."""
        spent = self._propose("ok").api_usd
        self.assertGreater(spent, 0.0)
        self.tearDown()
        self.setUp()
        with self.assertRaises(ProposerError) as ctx:
            self._propose("binary")
        self.assertIn("binary changes are unsupported", str(ctx.exception))
        self.assertIsNotNone(ctx.exception.api_usd)
        self.assertAlmostEqual(ctx.exception.api_usd, spent)

    def test_nonzero_exit_is_an_error(self) -> None:
        with self.assertRaises(ProposerError) as ctx:
            self._propose("exit")
        self.assertIn("exited 2", str(ctx.exception))
        self.assertEqual(ctx.exception.api_usd, 0.0)
        self.assertIsInstance(ctx.exception, ProposerAccountingError)

    def test_hang_is_killed_at_timeout(self) -> None:
        start = time.monotonic()
        with self.assertRaises(ProposerError) as ctx:
            self._propose("hang", timeout_s=2)
        self.assertLess(time.monotonic() - start, 15)
        self.assertIn("timed out", str(ctx.exception))
        receipt = json.loads((self._transcript().parent / "attempt-status.json").read_text())
        self.assertEqual(receipt["status"], "failed")
        self.assertTrue(receipt["timed_out"])
        self.assertFalse(receipt["accounting_resolved"])
        self.assertEqual(receipt["api_usd_known"], 0.0)

    def test_run_without_edits_is_rejected(self) -> None:
        with self.assertRaises(ProposerError) as ctx:
            self._propose("nopatch")
        self.assertIn("no patch", str(ctx.exception))
        self.assertAlmostEqual(ctx.exception.api_usd or 0.0, 0.0123, places=9)
