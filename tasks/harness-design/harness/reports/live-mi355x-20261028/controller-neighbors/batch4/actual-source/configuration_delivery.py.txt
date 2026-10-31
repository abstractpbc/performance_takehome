"""Optional bounded LLMD configuration-delivery source policy.

This is source eligibility before a build, not proof of a real image, performance,
registry upload or layer identity. The final publisher still checks actual config
and all inherited layers. Default Harness operation does not enable this policy.
"""

import copy
import hashlib
import json
import os
import re
import shlex
import stat
from decimal import Decimal
from pathlib import Path

POLICY = "llmd-config-v1"
DOCKERFILE = Path("tasks/harness-design/Dockerfile")
TELEMETRY = ("LLMD_POSTHOG_TOKEN", "LLMD_USAGE_STATS_ENDPOINT")
MAX_ENTRIES = 10000
MAX_CONTEXT_BYTES = 256 << 20


def need(ok, reason):
    if not ok:
        raise ValueError("delivery policy: " + reason)


SOURCE = "docker.io/zmlai/llmd"
BASE_SHA = "0a2cd01a2e53c78934a8cfef66f8668da7666be4954a5d18b72df089c54ea34f"


def dockerfile_runtime(raw, config, prior_config):
    """Exact copied source must explain the actual raw runtime configuration."""
    need(0 < len(raw) <= 64 << 10, "bounded selected Dockerfile")
    instructions = {}
    env = {}
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        # Conservative pilot grammar: reserved directive-looking comments anywhere.
        need(
            re.match(r"#\s*(?:syntax|escape|check)\s*=", line, re.IGNORECASE) is None,
            "reserved Dockerfile parser directives forbidden",
        )
        if not line or line.startswith("#"):
            continue
        head, sep, body = line.partition(" ")
        head = head.upper()
        need(
            sep and head in {"ARG", "FROM", "ENTRYPOINT", "CMD", "ENV"},
            "only reviewed configuration-only Dockerfile instructions",
        )
        if head == "ENV":
            values = shlex.split(body)
            need(
                len(values) == 1 and "=" in values[0],
                "one exact empty telemetry ENV per instruction",
            )
            key, value = values[0].split("=", 1)
            need(
                key in {"LLMD_USAGE_STATS_ENDPOINT", "LLMD_POSTHOG_TOKEN"}
                and value == ""
                and key not in env,
                "only existing telemetry variables may be blanked",
            )
            env[key] = value
        else:
            need(head not in instructions, "one original ARG/FROM/ENTRYPOINT/CMD")
            instructions[head] = body
    need(
        set(instructions) == {"ARG", "FROM", "ENTRYPOINT", "CMD"}
        and instructions["ARG"] == "BASE_IMAGE=" + SOURCE + "@sha256:" + BASE_SHA
        and instructions["FROM"] == "${BASE_IMAGE}",
        "pinned original public FROM unchanged",
    )
    entry, cmd = (json.loads(instructions[key]) for key in ("ENTRYPOINT", "CMD"))
    need(
        entry == ["/llmd/llmd"] == config["config"]["Entrypoint"]
        and cmd == config["config"]["Cmd"],
        "copied selected source and exact config ENTRYPOINT/CMD agree",
    )

    # Per-attempt one-knob attribution belongs before each build. This final
    # image may contain multiple independently kept knobs. Accept only the
    # explicitly reviewed LLMD configuration family, never arbitrary argv.
    def documented_flags(values):
        need(
            isinstance(values, list)
            and 2 <= len(values) <= 11
            and all(isinstance(x, str) and 0 < len(x) <= 128 for x in values),
            "bounded documented LLMD flag vector",
        )
        integer = {
            "--batch-size",
            "--token-batch-size",
            "--prefill-chunk-size",
            "--page-chunk-size",
            "--speculative-draft-count",
        }
        fraction = {"--gpu-memory-fraction", "--cache-memory-fraction"}
        fixed = {"--io-impl": "zio", "--max-context-len": "128000"}
        allowed = integer | fraction | set(fixed) | {"--backend", "--speculative-drafter-model"}
        parsed = {}
        for argument in values:
            key, separator, value = argument.partition("=")
            need(
                separator and key in allowed and key not in parsed,
                "unique reviewed flag names only",
            )
            if key in fixed:
                need(value == fixed[key], "model/context/io contract unchanged")
            elif key in integer:
                need(
                    re.fullmatch(r"[1-9][0-9]{0,19}", value) is not None and int(value) < 2**64,
                    "positive bounded integer tuning value",
                )
            elif key in fraction:
                from decimal import Decimal

                need(
                    re.fullmatch(r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", value) is not None
                    and len(value) <= 64,
                    "finite numeric memory fraction",
                )
                amount = Decimal(value)
                need(amount.is_finite() and 0 < amount <= 1, "memory fraction must be in (0,1]")
            elif key == "--backend":
                need(value in {"auto", "triton"}, "only reviewed non-CUDA/non-TPU backend values")
            else:
                need(value == "native", "only built-in speculative drafter, no external model/path")
            parsed[key] = value
        need(
            all(parsed.get(key) == value for key, value in fixed.items()),
            "fixed io/context flags required",
        )
        return parsed

    documented_flags(prior_config["config"]["Cmd"])
    documented_flags(cmd)
    old = copy.deepcopy(prior_config["config"])
    new = copy.deepcopy(config["config"])
    old_env = old.pop("Env")
    new_env = new.pop("Env")
    old.pop("Cmd")
    new.pop("Cmd")
    old = {k: v for k, v in old.items() if v not in (None, "", [], {}, False)}
    new = {k: v for k, v in new.items() if v not in (None, "", [], {}, False)}
    need(old == new, "all other nonempty actual image runtime semantics unchanged")
    need(
        [x.split("=", 1)[0] for x in old_env] == [x.split("=", 1)[0] for x in new_env],
        "same original ordered ENV keys",
    )
    for old_value, new_value in zip(old_env, new_env):  # noqa: B905 - copied predicate AST
        key, prior = old_value.split("=", 1)
        actual_key, actual = new_value.split("=", 1)
        need(
            actual_key == key and actual == env.get(key, prior),
            "actual runtime ENV follows exact selected Dockerfile",
        )
    return {
        "entrypoint": entry,
        "cmd": cmd,
        "blanked_telemetry": sorted(env),
        "filesystem_layer_instructions": 0,
    }


def _source_view(raw):
    """Infer only declared runtime semantics; canonical predicate validates them."""
    need(isinstance(raw, bytes) and 0 < len(raw) <= 64 << 10, "bounded Dockerfile")
    command = entry = None
    environment = {}
    for line in raw.decode("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        head, separator, body = line.partition(" ")
        if head.upper() == "CMD":
            command = json.loads(body)
        elif head.upper() == "ENTRYPOINT":
            entry = json.loads(body)
        elif head.upper() == "ENV":
            values = shlex.split(body)
            need(len(values) == 1 and "=" in values[0], "one telemetry ENV value")
            key, value = values[0].split("=", 1)
            environment[key] = value
    baseline_env = [key + "=INHERITED_PUBLIC_BASE_VALUE" for key in TELEMETRY]
    prior = {"config": {"Entrypoint": ["/llmd/llmd"], "Cmd": command, "Env": baseline_env}}
    inferred = {
        "config": {
            "Entrypoint": entry,
            "Cmd": command,
            "Env": [
                key + "=" + environment.get(key, "INHERITED_PUBLIC_BASE_VALUE") for key in TELEMETRY
            ],
        }
    }
    try:
        dockerfile_runtime(raw, inferred, prior)
    except ArithmeticError as exc:
        raise ValueError("delivery policy: invalid finite numeric flag") from exc
    return command, environment


def validate_initial_source(context):
    """Fail an incompatible initial source before a baseline/model/build call."""
    context = Path(context)
    target = context / DOCKERFILE
    need(
        target.is_file() and not any(p.is_symlink() for p in (target, *target.parents)),
        "regular initial Dockerfile required",
    )
    need(0 < target.stat().st_size <= 64 << 10, "bounded initial Dockerfile")
    _source_view(target.read_bytes())


def _manifest(root, *, exclude_git, checkpoint):
    root = Path(root)
    need(root.is_dir() and not root.is_symlink(), "regular context directory")
    entries = {}
    total = 0
    for directory, subdirs, files in os.walk(root, followlinks=False):
        checkpoint()
        if exclude_git:
            subdirs[:] = [name for name in subdirs if name != ".git"]
            files = [name for name in files if name != ".git"]
        for name in sorted(subdirs + files):
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            metadata = path.lstat()
            need(not stat.S_ISLNK(metadata.st_mode), "no source/candidate symlinks")
            need(len(entries) < MAX_ENTRIES, "bounded context entry count")
            if stat.S_ISDIR(metadata.st_mode):
                entries[relative] = ("directory",)
                continue
            need(stat.S_ISREG(metadata.st_mode), "regular context files only")
            total += metadata.st_size
            need(total <= MAX_CONTEXT_BYTES, "bounded context bytes")
            digest = hashlib.sha256()
            size = 0
            with path.open("rb") as stream:
                while block := stream.read(64 << 10):
                    checkpoint()
                    size += len(block)
                    need(size <= metadata.st_size, "context file changed while reading")
                    digest.update(block)
            need(size == metadata.st_size, "complete context file")
            entries[relative] = ("file", stat.S_IMODE(metadata.st_mode), size, digest.hexdigest())
    return entries


def _semantic_flags(command):
    flags = {}
    for argument in command:
        key, _, value = argument.partition("=")
        if key.endswith("-fraction"):
            value = Decimal(value)
        elif key in {
            "--batch-size",
            "--token-batch-size",
            "--prefill-chunk-size",
            "--page-chunk-size",
            "--speculative-draft-count",
            "--max-context-len",
        }:
            value = int(value)
        flags[key] = value
    return flags


def validate_patch_context(original, candidate, *, checkpoint=lambda: None):
    """Check ACTUAL applied scratch tree, not the model's different worktree."""
    before = _manifest(original, exclude_git=True, checkpoint=checkpoint)
    after = _manifest(candidate, exclude_git=False, checkpoint=checkpoint)
    changed = sorted(
        name for name in set(before) | set(after) if before.get(name) != after.get(name)
    )
    need(changed == [DOCKERFILE.as_posix()], "only the Dockerfile may change")
    old_raw = (Path(original) / DOCKERFILE).read_bytes()
    new_raw = (Path(candidate) / DOCKERFILE).read_bytes()
    old_cmd, old_env = _source_view(old_raw)
    new_cmd, new_env = _source_view(new_raw)
    old_flags, new_flags = _semantic_flags(old_cmd), _semantic_flags(new_cmd)
    flags = sorted(
        key for key in set(old_flags) | set(new_flags) if old_flags.get(key) != new_flags.get(key)
    )
    envs = sorted(
        key
        for key in set(old_env) | set(new_env)
        if old_env.get(key, "INHERITED_PUBLIC_BASE_VALUE")
        != new_env.get(key, "INHERITED_PUBLIC_BASE_VALUE")
    )
    need(len(flags) + len(envs) == 1, "exactly one semantic flag or telemetry variable per attempt")
    checkpoint()
    return {
        "policy": POLICY,
        "status": "ELIGIBLE_BEFORE_BUILD",
        "source_only": True,
        "changed_paths": changed,
        "changed_knobs": flags + envs,
        "prior_Dockerfile_sha256": hashlib.sha256(old_raw).hexdigest(),
        "candidate_Dockerfile_sha256": hashlib.sha256(new_raw).hexdigest(),
        "image_or_layer_verification": False,
    }
