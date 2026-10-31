"""Image/runtime contract: pinned base, preserved entrypoint, no egress, read-only model mount."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from abstract_harness.engines import LlmdEngine, StandinEngine

from . import _paths

LOCK = json.loads((_paths.REPO_ROOT / "baseline" / "lock.json").read_text("utf-8"))
LLMD_DOCKERFILE = _paths.TASK_DIR / "Dockerfile"
STANDIN_DOCKERFILE = _paths.TASK_DIR / "standin" / "Dockerfile"


def _docker_image_present(image: str) -> bool:
    if not shutil.which("docker"):
        return False
    r = subprocess.run(["docker", "image", "inspect", image], capture_output=True, timeout=60)
    return r.returncode == 0


class RunCommandContractTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.model_dir = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _check_common(self, cmd: list[str]) -> None:
        self.assertEqual(cmd[:2], ["docker", "run"])
        self.assertIn("--network", cmd)
        self.assertEqual(cmd[cmd.index("--network") + 1], "harness-net")
        self.assertNotIn("--publish", cmd)
        self.assertNotIn("-p", cmd)
        self.assertIn("no-new-privileges", cmd)
        mounts = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--mount"]
        model_mounts = [m for m in mounts if "dst=/models/model" in m]
        self.assertEqual(len(model_mounts), 1, mounts)
        self.assertIn("readonly", model_mounts[0])
        self.assertIn(f"src={self.model_dir}", model_mounts[0])

    def test_standin_run_command(self) -> None:
        engine = StandinEngine.create(_paths.REPO_ROOT, self.model_dir)
        cmd = engine.run_command(
            "img:tag", "c1", "harness-net", runtime_args=engine.full_runtime_args([])
        )
        self._check_common(cmd)
        self.assertIn("--model", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "/models/model/model.gguf")

    def test_llmd_run_command_matches_baseline_semantics(self) -> None:
        engine = LlmdEngine.from_lock(_paths.REPO_ROOT, self.model_dir)
        cmd = engine.run_command(
            "img:tag", "c2", "harness-net", runtime_args=engine.full_runtime_args(["--io-impl=zio"])
        )
        self._check_common(cmd)
        self.assertIn("--model=/models/model", cmd)
        joined = " ".join(cmd)
        for needle in (
            "--device /dev/kfd",
            "--device /dev/dri/renderD",
            "--env ROCR_VISIBLE_DEVICES=0",
            "--shm-size 16g",
            "--cpus 16",
            "--memory 128g",
        ):
            self.assertIn(needle, joined)
        self.assertEqual(cmd[-2:], ["--io-impl=zio", "--model=/models/model"])
        self.assertEqual(engine.spec.base_image, LOCK["image"])

    def test_conformance_probe_uses_network_none(self) -> None:
        engine = LlmdEngine.from_lock(_paths.REPO_ROOT, self.model_dir)
        cmd = engine.conformance_probe_command("img", "--help")
        self.assertEqual(cmd[cmd.index("--network") + 1], "none")
        self.assertEqual(cmd[-1], "--help")


class DockerfileContractTest(unittest.TestCase):
    def test_llmd_wrapper_pins_base_and_preserves_entrypoint(self) -> None:
        text = LLMD_DOCKERFILE.read_text("utf-8")
        self.assertIn(LOCK["image"], text)
        self.assertRegex(text, r'(?m)^ENTRYPOINT \["/llmd/llmd"\]')
        self.assertRegex(text, r'(?m)^CMD \["--io-impl=zio"')
        self.assertNotRegex(text, r"(?m)^RUN ", "pinned LLMD image has no /bin/sh; RUN cannot work")
        self.assertNotIn("--model=", text.split("CMD")[-1], "runner supplies --model=/models/model")

    def test_standin_dockerfile_pins_sources_by_digest(self) -> None:
        text = STANDIN_DOCKERFILE.read_text("utf-8")
        self.assertTrue(
            re.search(r"(?m)^ARG BASE_IMAGE=\S+@sha256:[0-9a-f]{64}$", text),
            "base image pinned by digest",
        )
        self.assertIn("ARG LLAMA_CPP_COMMIT=620d2225cc436ab0b001e3200e590e3b286ef9ab", text)
        self.assertRegex(text, r"ADD --checksum=sha256:[0-9a-f]{64}")
        self.assertIn("CANDIDATE-BUILT STAND-IN", text)

    def test_lock_platform_matches_engine(self) -> None:
        self.assertEqual(LOCK["platform"], "linux/amd64")
        self.assertEqual(LOCK["entrypoint"], "/llmd/llmd")


@unittest.skipUnless(_docker_image_present(LOCK["image"]), "pinned LLMD image not present locally")
class LlmdImageLiveTest(unittest.TestCase):
    """Runs only when the pinned image is already pulled; never pulls (offline-safe)."""

    def test_help_runs_with_network_none_and_entrypoint_preserved(self) -> None:
        engine = LlmdEngine.from_lock(_paths.REPO_ROOT, Path("/nonexistent"))
        r = subprocess.run(
            engine.conformance_probe_command(LOCK["image"], "--help"),
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        self.assertIn("--model=<path>", r.stdout + r.stderr)
        cfg = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{json .Config.Entrypoint}}",
                LOCK["image"],
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(json.loads(cfg.stdout), ["/llmd/llmd"])


if __name__ == "__main__":
    unittest.main()
