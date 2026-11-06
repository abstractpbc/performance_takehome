"""Expected state per synthetic family. Run directly to print expected versus actual."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis.axes import analyze  # noqa: E402
from analysis.events import load_plan  # noqa: E402
from analysis.gates import INCONCLUSIVE, INVALID, VALID  # noqa: E402
from synth import FAMILIES, generate  # noqa: E402

EVIDENCE_MISSING = "evidence_missing"

EXPECTED = {
    "clean": (VALID, ()),
    "noisy": (VALID, ()),
    "burst": (VALID, ()),
    "stall": (VALID, ()),
    "late_first_token": (VALID, ()),
    "reasoning": (VALID, ()),
    "unicode_boundary": (VALID, ()),
    "heartbeats": (VALID, ()),
    "errors_few": (VALID, ()),
    "anchor_noise": (VALID, ()),
    "client_lag_few": (VALID, ()),
    "misleading_readiness": (VALID, ()),
    "one_chunk": (VALID, ()),
    "stray_replacement": (INVALID, ("wrong_output",)),
    "errors_many": (INVALID, ("failure_rate",)),
    "http_errors": (INVALID, ("failure_rate",)),
    "missing_done": (INVALID, ("failure_rate",)),
    "missing_finish": (INVALID, ("failure_rate",)),
    "empty_output": (INVALID, ("failure_rate",)),
    "timed_out": (INVALID, ("failure_rate",)),
    "truncated_length": (INVALID, ("failure_rate",)),
    "truncated_short": (INVALID, ("failure_rate",)),
    "vanished": (INVALID, ("request_vanished",)),
    "wrong_canary": (INVALID, ("wrong_output",)),
    "false_usage": (INVALID, ("false_usage",)),
    "placeholder_first_token": (INVALID, ("reference_mismatch",)),
    "shortened_stop": (INVALID, ("reference_mismatch",)),
    "malformed_sse": (INVALID, ("failure_rate",)),
    "two_choices": (INVALID, ("failure_rate",)),
    "timing_corrupt": (INVALID, ("timing_corrupt",)),
    "missing_tokens": (INVALID, (EVIDENCE_MISSING,)),
    "fail_fast_stop": (INVALID, ("wrong_output", "run_truncated")),
    "readiness_only": (INVALID, ("startup_failed",)),
    "startup_crash": (INVALID, ("startup_crash",)),
    "startup_timeout": (INVALID, ("startup_failed",)),
    "wrong_probe": (INVALID, ("wrong_output",)),
    "startup_stopped": (INVALID, ("startup_failed", "run_truncated")),
    "mid_run_crash": (INVALID, ("server_crash", "run_truncated")),
    "usage_absent": (INCONCLUSIVE, ("no_valid_requests",)),
    "no_reference": (INCONCLUSIVE, ("no_reference",)),
    "other_revision_reference": (INCONCLUSIVE, ("no_reference",)),
    "few_stable_anchors": (INCONCLUSIVE, ("anchors_underpowered",)),
    "client_lag_many": (INCONCLUSIVE, ("client_lag",)),
    "warmup_unfinished": (INCONCLUSIVE, ("warmup_incomplete",)),
    "aborted_capture": (INCONCLUSIVE, ("run_truncated",)),
    "few_launches": (INCONCLUSIVE, ("startup_underpowered",)),
    "thin_run": (INCONCLUSIVE, ("quantile_underpowered",)),
}

# failure_rate says how many failed, so these pin down why each family failed.
REQUEST_REASONS = {
    "errors_many": "server_error",
    "http_errors": "server_error",
    "missing_done": "incomplete_stream",
    "missing_finish": "incomplete_stream",
    "empty_output": "empty_output",
    "timed_out": "timed_out",
    "truncated_length": "truncated_output",
    "truncated_short": "truncated_output",
    "malformed_sse": "server_error",
    "two_choices": "server_error",
    "usage_absent": "usage_absent",
    "client_lag_many": "client_lag",
}


def actual(family: str, seed: int, out: Path) -> tuple[str, tuple[str, ...]]:
    run_path, workload = generate(family, seed, out)
    analysis = analyze(run_path, load_plan(workload))
    return analysis.state, analysis.reasons


def main() -> int:
    missing = set(FAMILIES) ^ set(EXPECTED)
    failures = len(missing)
    print(f"{'FAMILY':26}{'EXPECTED':14}{'ACTUAL':14}REASONS")
    for family, (state, reasons) in EXPECTED.items():
        with tempfile.TemporaryDirectory() as directory:
            got_state, got_reasons = actual(family, 1, Path(directory))
        ok = got_state == state and set(reasons) <= set(got_reasons)
        ok = ok and (state != VALID or not got_reasons)
        failures += not ok
        print(f"{family:26}{state:14}{got_state:14}{','.join(got_reasons) or '-'}"
              f"{'' if ok else '  <-- MISMATCH'}")
    print(f"\n{len(EXPECTED)} families, {failures} mismatches")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
