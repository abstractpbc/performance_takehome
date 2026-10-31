"""Thin, timeout-bounded wrapper around the Docker CLI (buildx build, run, inspect, kill)."""

from __future__ import annotations

import json
import math
import re
import shutil
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import procs

DEFAULT_PLATFORM = "linux/amd64"


class DockerError(RuntimeError):
    """A Docker operation failed, timed out, or produced unusable output."""


@dataclass(frozen=True)
class BuildResult:
    """Result of a fresh image build.

    ``digest`` is resolved from build metadata or Docker inspection. It can
    describe a config, image manifest or index, depending on the image store and
    build output. ``image_id`` records Docker's local .Id separately; neither
    value alone proves publication or an organizer-accessible registry reference.
    """

    tag: str
    ok: bool
    timed_out: bool
    duration_s: float
    image_id: str | None
    digest: str | None
    log_path: Path
    error: str | None = None


def docker_available(
    *,
    timeout_s: float = 20.0,
    should_abort: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
) -> bool:
    """True when a ``docker`` binary is on PATH and the daemon answers."""
    if shutil.which("docker") is None:
        return False
    return _docker(
        ["version", "--format", "{{.Server.Version}}"],
        timeout_s=timeout_s,
        should_abort=should_abort,
        deadline_monotonic=deadline_monotonic,
    ).ok


def _deadline(timeout_s: float, deadline_monotonic: float | None) -> float:
    if not math.isfinite(timeout_s) or timeout_s < 0:
        raise DockerError("Docker timeout must be finite and nonnegative")
    local_end = time.monotonic() + timeout_s
    if deadline_monotonic is None:
        return local_end
    if not math.isfinite(deadline_monotonic):
        raise DockerError("Docker deadline must be finite")
    return min(local_end, deadline_monotonic)


def _remaining(end: float, should_abort: Callable[[], bool] | None) -> float:
    if should_abort is not None and should_abort():
        raise DockerError("Docker operation aborted")
    remaining = end - time.monotonic()
    if remaining <= 0:
        raise DockerError("Docker operation deadline expired")
    return remaining


def _command(
    args: Sequence[str],
    timeout_s: float,
    *,
    should_abort: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
    **kw: Any,
) -> procs.RunResult:
    end = _deadline(timeout_s, deadline_monotonic)
    remaining = _remaining(end, should_abort)
    result = procs.run(
        args,
        timeout_s=remaining,
        should_abort=should_abort,
        **kw,
    )
    # A fast-exiting CLI can win communicate() before its polling callback runs.
    # Do not start another step or report success after the original operation ended.
    if result.aborted:
        raise DockerError("Docker operation aborted")
    if result.ok:
        _remaining(end, should_abort)
    return result


def _docker(args: Sequence[str], timeout_s: float, **kw: Any) -> procs.RunResult:
    return _command(["docker", *args], timeout_s=timeout_s, **kw)


def _inspect_first(text: str, description: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except (TypeError, json.JSONDecodeError) as exc:
        raise DockerError(f"{description} returned invalid JSON") from exc
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        raise DockerError(f"{description} returned no valid object")
    return data[0]


def build_image(
    context_dir: Path,
    tag: str,
    *,
    log_path: Path,
    timeout_s: float,
    platform: str = DEFAULT_PLATFORM,
    dockerfile: Path | None = None,
    build_args: Mapping[str, str] | None = None,
    network: str = "default",
    no_cache: bool = False,
    should_abort: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
) -> BuildResult:
    """Build ``context_dir`` into a fresh local image and resolve its identity.

    Uses ``docker buildx build --load --metadata-file`` so the image ID is
    available without pushing to a registry. ``--no-cache`` is off by default:
    layer caching only reuses byte-identical steps, so a patched
    Dockerfile still produces a genuinely new image, and digests are resolved
    from the result, never guessed.
    """
    started = time.monotonic()
    end = _deadline(timeout_s, deadline_monotonic)
    metadata = log_path.with_suffix(".meta.json")
    args = [
        "buildx",
        "build",
        "--platform",
        platform,
        "--load",
        "--progress",
        "plain",
        "--network",
        network,
        "--metadata-file",
        str(metadata),
        "-t",
        tag,
    ]
    if no_cache:
        args.append("--no-cache")
    if dockerfile is not None:
        args += ["-f", str(dockerfile)]
    for key, value in (build_args or {}).items():
        args += ["--build-arg", f"{key}={value}"]
    args.append(str(context_dir))
    try:
        result = _docker(
            args, timeout_s, log_path=log_path, should_abort=should_abort, deadline_monotonic=end
        )
    except DockerError as exc:
        return BuildResult(
            tag,
            False,
            time.monotonic() >= end,
            time.monotonic() - started,
            None,
            None,
            log_path,
            str(exc),
        )
    if result.timed_out:
        return BuildResult(
            tag, False, True, result.duration_s, None, None, log_path, "build timed out"
        )
    if not result.ok:
        tail = "\n".join(result.stdout.splitlines()[-15:])
        return BuildResult(
            tag,
            False,
            False,
            result.duration_s,
            None,
            None,
            log_path,
            f"build failed (rc={result.returncode}): {tail}",
        )
    digest = None
    if metadata.exists():
        try:
            digest = json.loads(metadata.read_text(encoding="utf-8")).get("containerimage.digest")
        except json.JSONDecodeError:
            digest = None
    try:
        config = image_config(
            tag, timeout_s=min(60.0, timeout_s), should_abort=should_abort, deadline_monotonic=end
        )
        if digest is None:
            digest = _config_digest(config)
        image_id = config.get("Id")
    except DockerError as exc:
        return BuildResult(
            tag,
            False,
            time.monotonic() >= end,
            time.monotonic() - started,
            None,
            None,
            log_path,
            str(exc),
        )
    if not digest:
        return BuildResult(
            tag,
            False,
            False,
            time.monotonic() - started,
            image_id,
            None,
            log_path,
            "image built but no digest could be resolved",
        )
    return BuildResult(tag, True, False, time.monotonic() - started, image_id, digest, log_path)


def image_config(
    image: str,
    *,
    timeout_s: float = 60.0,
    should_abort: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Return ``docker image inspect`` for ``image`` (flattened to the first entry)."""
    result = _docker(
        ["image", "inspect", image],
        timeout_s=timeout_s,
        should_abort=should_abort,
        deadline_monotonic=deadline_monotonic,
    )
    if not result.ok:
        raise DockerError(f"image inspect failed for {image}: {result.stderr.strip()}")
    return _inspect_first(result.stdout, f"image inspect {image}")


def image_exists(
    image: str,
    *,
    timeout_s: float = 60.0,
    should_abort: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
) -> bool:
    """True when ``image`` is present locally."""
    return _docker(
        ["image", "inspect", image],
        timeout_s=timeout_s,
        should_abort=should_abort,
        deadline_monotonic=deadline_monotonic,
    ).ok


def image_cmd(
    image: str,
    *,
    timeout_s: float = 60.0,
    should_abort: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
) -> list[str]:
    """The image's default ``CMD`` (the tunable runtime-flag surface)."""
    return list(
        image_config(
            image,
            timeout_s=timeout_s,
            should_abort=should_abort,
            deadline_monotonic=deadline_monotonic,
        )
        .get("Config", {})
        .get("Cmd")
        or []
    )


def image_entrypoint(
    image: str,
    *,
    timeout_s: float = 60.0,
    should_abort: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
) -> list[str]:
    """The image's ``ENTRYPOINT``."""
    return list(
        image_config(
            image,
            timeout_s=timeout_s,
            should_abort=should_abort,
            deadline_monotonic=deadline_monotonic,
        )
        .get("Config", {})
        .get("Entrypoint")
        or []
    )


def _config_digest(config: dict[str, Any]) -> str | None:
    for ref in config.get("RepoDigests") or []:
        if isinstance(ref, str) and "@sha256:" in ref:
            return ref.split("@", 1)[1]
    return None


def resolve_digest(
    image: str,
    *,
    timeout_s: float = 60.0,
    should_abort: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
) -> str | None:
    """Resolve the ``sha256:`` repo digest of a local image, if Docker recorded one."""
    end = _deadline(timeout_s, deadline_monotonic)
    try:
        config = image_config(
            image, timeout_s=timeout_s, should_abort=should_abort, deadline_monotonic=end
        )
    except DockerError:
        _remaining(end, should_abort)
        return None
    return _config_digest(config)


def retag(
    source: str,
    target: str,
    *,
    timeout_s: float = 60.0,
    should_abort: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
) -> None:
    """``docker tag source target`` (used to pin the last-good image)."""
    result = _docker(
        ["tag", source, target],
        timeout_s=timeout_s,
        should_abort=should_abort,
        deadline_monotonic=deadline_monotonic,
    )
    if not result.ok:
        raise DockerError(f"docker tag {source} {target} failed: {result.stderr.strip()}")


def remove_image(image: str) -> None:
    """Best-effort ``docker rmi`` (never raises)."""
    _docker(["rmi", "-f", image], timeout_s=120)


def ensure_internal_network(
    name: str,
    *,
    timeout_s: float = 120.0,
    should_abort: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
) -> str:
    """Create (if needed) a bridge network with ``--internal`` so containers have no egress.

    The host can still reach the container's address on that network, which is
    how the harness measures the server.
    """
    end = _deadline(timeout_s, deadline_monotonic)
    options = {"should_abort": should_abort, "deadline_monotonic": end}
    inspected = _docker(["network", "inspect", name], timeout_s=min(30.0, timeout_s), **options)
    if inspected.ok:
        if _inspect_first(inspected.stdout, "network inspect").get("Internal") is not True:
            raise DockerError(f"network {name} exists but is not Internal=true")
        return name
    result = _docker(
        ["network", "create", "--internal", name], timeout_s=min(60.0, timeout_s), **options
    )
    inspected = _docker(["network", "inspect", name], timeout_s=min(30.0, timeout_s), **options)
    if not inspected.ok:
        raise DockerError(f"network create {name} failed: {result.stderr.strip()}")
    if _inspect_first(inspected.stdout, "network inspect").get("Internal") is not True:
        raise DockerError(f"network {name} is not Internal=true after creation/reuse")
    return name


def remove_network(name: str) -> None:
    """Best-effort network removal."""
    _docker(["network", "rm", name], timeout_s=60)


def run_detached(
    command: Sequence[str],
    *,
    timeout_s: float = 120.0,
    should_abort: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
) -> str:
    """Run a prepared ``docker run --detach ...`` command; return the container id."""
    result = _command(
        command,
        timeout_s=timeout_s,
        should_abort=should_abort,
        deadline_monotonic=deadline_monotonic,
    )
    if not result.ok:
        raise DockerError(f"docker run failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout.strip()


def container_absent(name: str, *, timeout_s: float = 30.0) -> bool:
    """Prove exact-name absence from a successful complete daemon enumeration.

    A failed ``inspect`` cannot distinguish absence from an unavailable daemon.
    Recovery uses this separate, bounded probe and leaves unknown state blocked.
    """
    valid_name = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}")
    if not isinstance(name, str) or not valid_name.fullmatch(name):
        raise DockerError("invalid container name for absence check")
    try:
        result = _docker(["ps", "-a", "--format", "{{.Names}}"], timeout_s=timeout_s)
    except OSError as exc:
        raise DockerError("container absence could not be established") from exc
    if not result.ok:
        raise DockerError("container absence could not be established")
    names = result.stdout.splitlines()
    if any(not valid_name.fullmatch(value) for value in names):
        raise DockerError("container enumeration returned invalid names")
    return name not in names


def container_state(
    name: str,
    *,
    timeout_s: float = 60.0,
    should_abort: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
) -> dict[str, Any] | None:
    """``State`` block of ``docker inspect`` or ``None`` when the container is gone."""
    result = _docker(
        ["inspect", name],
        timeout_s=timeout_s,
        should_abort=should_abort,
        deadline_monotonic=deadline_monotonic,
    )
    if not result.ok:
        return None
    return _inspect_first(result.stdout, "container inspect").get("State")


def container_running(
    name: str,
    *,
    timeout_s: float = 60.0,
    should_abort: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
) -> bool:
    """True when the container exists and is running."""
    state = container_state(
        name, timeout_s=timeout_s, should_abort=should_abort, deadline_monotonic=deadline_monotonic
    )
    return bool(state and state.get("Running"))


def container_ip(
    name: str,
    network: str,
    *,
    timeout_s: float = 60.0,
    should_abort: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
) -> str | None:
    """IP address of ``name`` on ``network`` (None if not attached yet)."""
    result = _docker(
        ["inspect", name],
        timeout_s=timeout_s,
        should_abort=should_abort,
        deadline_monotonic=deadline_monotonic,
    )
    if not result.ok:
        return None
    config = _inspect_first(result.stdout, "container inspect")
    networks = config.get("NetworkSettings", {}).get("Networks", {})
    entry = networks.get(network)
    return entry.get("IPAddress") or None if entry else None


def container_logs(
    name: str,
    tail: int = 200,
    *,
    timeout_s: float = 60.0,
    should_abort: Callable[[], bool] | None = None,
    deadline_monotonic: float | None = None,
) -> str:
    """Last ``tail`` lines of the container's combined logs."""
    result = _docker(
        ["logs", "--tail", str(tail), name],
        timeout_s=timeout_s,
        should_abort=should_abort,
        deadline_monotonic=deadline_monotonic,
    )
    return (result.stdout or "") + (result.stderr or "")


def stop_container(name: str, *, grace_s: int = 10) -> None:
    """Graceful SIGTERM via ``docker stop`` then force removal. Never raises."""
    _docker(["stop", "-t", str(grace_s), name], timeout_s=grace_s + 30)
    _docker(["rm", "-f", name], timeout_s=60)


def kill_containers_with_prefix(prefix: str) -> list[str]:
    """Force-remove every container whose name starts with ``prefix``; returns names removed."""
    result = _docker(["ps", "-a", "--format", "{{.Names}}"], timeout_s=60)
    names = [n for n in result.stdout.split() if n.startswith(prefix)]
    for name in names:
        _docker(["rm", "-f", name], timeout_s=60)
    return names
