"""Budget guards checked before every attempt: API spend, GPU-hours, lease deadline."""

from __future__ import annotations

import math
import time
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetConfig:
    """Hard limits for one harness run.

    ``gpu_hours`` are accounted as ``gpus_allocated x elapsed wall time`` since
    ``lease_start_ts`` (allocation accounting, not utilization), matching
    ``docs/BUDGETS.md``. ``final_reserve_s`` is the window kept free at the end
    of the lease for the final build/validation/manifest.
    """

    api_usd_limit: float
    gpu_hours_limit: float
    gpus_allocated: int
    lease_start_ts: float
    lease_deadline_ts: float
    final_reserve_s: float

    def __post_init__(self) -> None:
        if self.api_usd_limit < 0 or self.gpu_hours_limit < 0 or self.final_reserve_s < 0:
            raise ValueError("budget limits must be nonnegative")
        if self.gpus_allocated < 0:
            raise ValueError("gpus_allocated must be nonnegative")
        if self.lease_deadline_ts < self.lease_start_ts:
            raise ValueError("lease deadline precedes lease start")


@dataclass(frozen=True)
class BudgetVerdict:
    """Result of a pre-attempt guard check."""

    ok: bool
    reason: str
    api_usd_remaining: float
    gpu_hours_used: float
    gpu_hours_remaining: float
    lease_seconds_remaining: float
    usable_seconds_remaining: float


def gpu_hours_used(config: BudgetConfig, now_ts: float) -> float:
    """GPU-hours consumed so far under allocation accounting."""
    elapsed = max(0.0, now_ts - config.lease_start_ts)
    return config.gpus_allocated * elapsed / 3600.0


def check_before_attempt(
    config: BudgetConfig,
    *,
    api_usd_cumulative: float,
    now_ts: float,
    estimated_attempt_s: float,
    estimated_attempt_usd: float = 0.0,
) -> BudgetVerdict:
    """Decide whether one more attempt fits in every budget dimension.

    An attempt is refused when the API ledger would exceed its limit, when the
    GPU-hour allocation would be exhausted before it finishes, or when it would
    run into the reserved final window of the lease.
    """
    if estimated_attempt_s < 0 or estimated_attempt_usd < 0:
        raise ValueError("estimates must be nonnegative")
    used = gpu_hours_used(config, now_ts)
    lease_remaining = config.lease_deadline_ts - now_ts
    usable_remaining = lease_remaining - config.final_reserve_s
    api_remaining = config.api_usd_limit - api_usd_cumulative
    attempt_gpu_hours = config.gpus_allocated * estimated_attempt_s / 3600.0
    gpu_remaining = config.gpu_hours_limit - used

    reasons: list[str] = []
    if api_usd_cumulative + estimated_attempt_usd > config.api_usd_limit:
        reasons.append(
            f"API spend {api_usd_cumulative:.2f}+{estimated_attempt_usd:.2f} USD "
            f"would exceed limit {config.api_usd_limit:.2f}"
        )
    if used + attempt_gpu_hours > config.gpu_hours_limit:
        reasons.append(
            f"GPU-hours {used:.3f}+{attempt_gpu_hours:.3f} "
            f"would exceed allocation {config.gpu_hours_limit:.3f}"
        )
    if estimated_attempt_s > usable_remaining:
        reasons.append(
            f"attempt (~{estimated_attempt_s:.0f}s) does not fit before the reserved final window "
            f"({usable_remaining:.0f}s usable of {lease_remaining:.0f}s lease remaining)"
        )
    return BudgetVerdict(
        ok=not reasons,
        reason="; ".join(reasons) if reasons else "ok",
        api_usd_remaining=api_remaining,
        gpu_hours_used=used,
        gpu_hours_remaining=gpu_remaining,
        lease_seconds_remaining=lease_remaining,
        usable_seconds_remaining=usable_remaining,
    )


def monotone_now(
    now_ts: float, last_seen_ts: float, *, monotonic_floor_ts: float | None = None
) -> tuple[float, bool]:
    """Combine wall time, persisted accounting time, and a process-monotonic floor.

    Returns ``(timestamp, clock_went_back)``; the caller persists the larger
    value. A wall-clock clamp alone freezes charges during rollback, so running
    allocations must pass an advancing monotonic floor. A restart whose wall
    clock is behind the saved checkpoint needs separate lease reconciliation.
    """
    return max(
        now_ts, last_seen_ts, monotonic_floor_ts if monotonic_floor_ts is not None else last_seen_ts
    ), now_ts < last_seen_ts


def worst_case_attempt_s(
    *,
    propose_s: float,
    build_s: float,
    startup_s: float,
    measure_s: float,
    stop_grace_s: float,
    ab_remeasure: bool = True,
) -> float:
    """Upper bound on one attempt's wall time from the configured stage timeouts."""
    arms = 2 if ab_remeasure else 1
    return propose_s + build_s + arms * (startup_s + measure_s + stop_grace_s)


def stage_timeout(configured_s: float, deadline_monotonic: float, *, floor_s: float = 1.0) -> float:
    """Clamp a stage timeout so the stage cannot run past ``deadline_monotonic``."""
    remaining = deadline_monotonic - time.monotonic()
    return max(floor_s, min(configured_s, remaining))


def attempt_estimate_s(configured_s: float, observed_s: Sequence[float]) -> float:
    """Estimate for the next attempt: the configured value or the 90th percentile observed."""
    finite = sorted(v for v in observed_s if math.isfinite(v) and v >= 0)
    if not finite:
        return configured_s
    idx = min(len(finite) - 1, int(math.ceil(0.9 * len(finite)) - 1))
    return max(configured_s, finite[max(0, idx)])
