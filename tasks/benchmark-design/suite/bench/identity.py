"""Candidate-owned calibration/comparison consistency; no engine-image equality rule."""

from typing import Any

TOKENIZER_SEMANTICS = (
    "identifier",
    "model",
    "files",
    "runtime",
    "timing_resolution",
    "count_semantics",
    "prompt_count_semantics",
    "unicode_database",
    "offset_mapping",
)


def stable_tokenizer_identity(value: Any) -> dict[str, Any] | None:
    """Keep verified asset/count/offset semantics; ignore added provenance metadata.

    Current real calibration declares every field. Missing semantic fields cannot
    silently become a real calibration identity; explicit mock/unfrozen legacy
    cases are handled separately below.
    """
    if not isinstance(value, dict) or any(key not in value for key in TOKENIZER_SEMANTICS):
        return None
    if any(
        not isinstance(value[key], str) or not value[key]
        for key in TOKENIZER_SEMANTICS
        if key != "files"
    ):
        return None
    files = value["files"]
    if (
        not isinstance(files, dict)
        or not files
        or any(
            not isinstance(name, str)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
            for name, digest in files.items()
        )
    ):
        return None
    return {key: value[key] for key in TOKENIZER_SEMANTICS}


def calibration_identity_errors(calibration: Any, run: dict[str, Any], tokenizer: Any) -> list[str]:
    """Bind a declared calibration to model/count semantics, permitting image changes."""
    if calibration is None:
        return []  # Unfrozen planning/legacy cases retain their existing gates.
    if not isinstance(calibration, dict):
        return ["calibration identity is malformed"]
    engine = run.get("engine") or {}
    declared_engine = calibration.get("engine_identity") or {}
    declared_model = (
        declared_engine.get("lock_model") if isinstance(declared_engine, dict) else None
    )
    declared_tokens = calibration.get("tokenizer_identity")
    mock = engine.get("kind") == "mock"
    legacy_mock = mock and calibration.get("engine_kind") in (None, "mock")
    errors = []
    if not mock and calibration.get("engine_kind") == "mock":
        errors.append("real evaluation cannot use a mock calibration")
    if declared_model:
        if engine.get("lock_model") != declared_model:
            errors.append("run model pin differs from frozen calibration")
        if tokenizer is not None and tokenizer.model != declared_model:
            errors.append("selected tokenizer model differs from frozen calibration")
    elif not legacy_mock:
        errors.append("frozen calibration model identity is incomplete")
    if legacy_mock:
        if declared_tokens is not None and run.get("tokenizer") != declared_tokens:
            errors.append("mock tokenizer identity differs from declared mock calibration")
        return errors
    expected = stable_tokenizer_identity(declared_tokens)
    if expected is None:
        errors.append("frozen calibration verified tokenizer identity is incomplete")
        return errors
    if stable_tokenizer_identity(run.get("tokenizer")) != expected:
        errors.append("run tokenizer semantics differ from frozen calibration")
    if tokenizer is None or stable_tokenizer_identity(tokenizer.identity) != expected:
        errors.append("selected verified tokenizer semantics differ from frozen calibration")
    return errors


def comparison_identity_errors(plan: dict[str, Any], a: Any, b: Any) -> list[str]:
    """Defend direct API callers; normal CLI already shares one verified tokenizer."""
    errors = []
    runs = [a.ev.run.raw, b.ev.run.raw]
    for label, ev, run in zip(("A", "B"), (a, b), runs, strict=True):
        if ev.plan != plan:
            errors.append(f"run {label} evaluator plan differs from comparison plan")
        recorded = run.get("plan") or {}
        if recorded.get("plan_id") != plan["plan_id"]:
            errors.append(f"run {label} plan_id differs from comparison plan")
        if ev.plan_digest_hex and recorded.get("digest") != ev.plan_digest_hex:
            errors.append(f"run {label} recorded plan digest differs from evaluator")
        errors.extend(
            f"run {label}: {why}"
            for why in calibration_identity_errors(plan.get("calibration"), run, ev.tokenizer)
        )
    if (runs[0].get("plan") or {}).get("digest") != (runs[1].get("plan") or {}).get("digest"):
        errors.append("compared runs have different recorded plan digests")
    engines = [run.get("engine") or {} for run in runs]
    if (engines[0].get("kind") == "mock") != (engines[1].get("kind") == "mock"):
        errors.append("mock and real runs cannot be compared")
    if engines[0].get("lock_model") != engines[1].get("lock_model"):
        errors.append("compared runs have different model pins")
    identities = [run.get("tokenizer") for run in runs]
    if all(engine.get("kind") == "mock" for engine in engines):
        if identities[0] != identities[1]:
            errors.append("compared mock runs have different tokenizer identities")
    else:
        stable = [stable_tokenizer_identity(identity) for identity in identities]
        if any(identity is None for identity in stable) or stable[0] != stable[1]:
            errors.append("compared real runs have different/incomplete tokenizer semantics")
    return errors
