"""Engine abstraction: how to build, launch, and address an inference-engine container.

Two implementations:

* :class:`LlmdEngine` - the organizer's pinned LLMD image (``baseline/lock.json``)
  wrapped by ``tasks/harness-design/Dockerfile``. The launch command mirrors the
  device/mount/security/resource flags of ``tools.baseline.start_command`` but
  attaches the container to an ``--internal`` network (no egress, the contract
  requirement) instead of publishing a host port.
* :class:`StandinEngine` - the CANDIDATE-BUILT CPU stand-in in
  ``tasks/harness-design/standin/`` (llama.cpp ``llama-server``).

Both expose the same OpenAI-compatible port 8000 inside the container.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CONTAINER_PORT = 8000
MODEL_MOUNT = "/models/model"


@dataclass(frozen=True)
class EngineSpec:
    """Static description of one engine implementation."""

    name: str
    build_context: Path
    dockerfile: Path
    platform: str
    model_args: tuple[str, ...]
    readiness_path: str
    model_dir: Path
    base_image: str | None = None
    shm_size: str = "1g"
    cpus: str = "4"
    memory: str = "8g"
    devices: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    group_add: tuple[str, ...] = ()
    extra_flags: tuple[str, ...] = field(default_factory=tuple)


class Engine:
    """Base engine. Subclasses only differ in their :class:`EngineSpec`."""

    def __init__(self, spec: EngineSpec) -> None:
        self.spec = spec

    @property
    def name(self) -> str:
        """Engine identifier used in config, tags, and reports."""
        return self.spec.name

    def run_command(
        self,
        image: str,
        container_name: str,
        network: str,
        *,
        runtime_args: Sequence[str],
    ) -> list[str]:
        """Build the ``docker run --detach`` command for one engine container.

        ``runtime_args`` must be the image's CMD (tunable flags) - callers
        resolve it with :func:`docker_runtime.image_cmd` so experiments that
        edit the Dockerfile CMD take effect - followed by the model arguments.
        No ``--publish``: the container is only reachable on the internal network.
        """
        spec = self.spec
        model = spec.model_dir.resolve()
        if not model.exists():
            raise FileNotFoundError(f"model directory {model} does not exist")
        command = [
            "docker",
            "run",
            "--detach",
            "--name",
            container_name,
            "--platform",
            spec.platform,
            "--network",
            network,
            "--security-opt",
            "no-new-privileges",
            "--shm-size",
            spec.shm_size,
            "--cpus",
            spec.cpus,
            "--memory",
            spec.memory,
        ]
        for device in spec.devices:
            command += ["--device", device]
        for gid in spec.group_add:
            command += ["--group-add", gid]
        for key, value in spec.env:
            command += ["--env", f"{key}={value}"]
        command += spec.extra_flags
        command += ["--mount", f"type=bind,src={model},dst={MODEL_MOUNT},readonly"]
        command.append(image)
        command += list(runtime_args)
        return command

    def full_runtime_args(self, image_cmd: Sequence[str]) -> list[str]:
        """Image CMD followed by this engine's model argument(s)."""
        return [*image_cmd, *self.spec.model_args]

    def conformance_probe_command(self, image: str, *extra: str) -> list[str]:
        """A ``--network none`` one-shot run (e.g. ``--help``) that needs no GPU or model."""
        return [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--platform",
            self.spec.platform,
            "--security-opt",
            "no-new-privileges",
            image,
            *extra,
        ]


class LlmdEngine(Engine):
    """Pinned LLMD image on the assigned ROCm GPU."""

    @classmethod
    def from_lock(
        cls,
        repo_root: Path,
        model_dir: Path,
        *,
        render_device: str = "/dev/dri/renderD128",
        render_gid: str | None = None,
        lock_path: Path | None = None,
    ) -> LlmdEngine:
        """Create the engine from ``baseline/lock.json`` (protected, read-only)."""
        lock_path = lock_path or repo_root / "baseline" / "lock.json"
        lock: dict[str, Any] = json.loads(lock_path.read_text(encoding="utf-8"))
        spec = EngineSpec(
            name="llmd",
            build_context=repo_root,
            dockerfile=repo_root / "tasks" / "harness-design" / "Dockerfile",
            platform=lock["platform"],
            model_args=("--model=/models/model",),
            readiness_path="/v1/models",
            model_dir=model_dir,
            base_image=lock["image"],
            shm_size="16g",
            cpus="16",
            memory="128g",
            devices=("/dev/kfd", render_device),
            env=(("ROCR_VISIBLE_DEVICES", "0"),),
            group_add=(render_gid,) if render_gid else (),
        )
        return cls(spec)


class StandinEngine(Engine):
    """CANDIDATE-BUILT CPU stand-in (llama.cpp llama-server)."""

    @classmethod
    def create(
        cls, repo_root: Path, model_dir: Path, *, cpus: str = "4", memory: str = "8g"
    ) -> StandinEngine:
        """Create the stand-in engine rooted at ``tasks/harness-design/standin``."""
        context = repo_root / "tasks" / "harness-design" / "standin"
        spec = EngineSpec(
            name="standin",
            build_context=context,
            dockerfile=context / "Dockerfile",
            platform="linux/amd64",
            model_args=("--model", f"{MODEL_MOUNT}/model.gguf"),
            readiness_path="/v1/models",
            model_dir=model_dir,
            shm_size="1g",
            cpus=cpus,
            memory=memory,
        )
        return cls(spec)


def make_engine(name: str, repo_root: Path, model_dir: Path, **kwargs: Any) -> Engine:
    """Factory used by the CLI (``--engine llmd|standin``)."""
    if name == "llmd":
        return LlmdEngine.from_lock(repo_root, model_dir, **kwargs)
    if name == "standin":
        return StandinEngine.create(repo_root, model_dir, **kwargs)
    raise ValueError(f"unknown engine {name!r}; expected 'llmd' or 'standin'")
