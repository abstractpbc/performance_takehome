"""Seeded arrival schedules: open-loop Poisson and closed-loop concurrency levels."""

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Arrival:
    index: int
    offset_s: float


def poisson_schedule(rate_rps: float, count: int, seed: int) -> list[Arrival]:
    """Open-loop Poisson arrivals: i.i.d. exponential gaps with mean 1/rate, offsets from 0."""
    if rate_rps <= 0 or count <= 0:
        raise ValueError("poisson schedule needs positive rate and count")
    rng = random.Random(seed)
    offset = 0.0
    arrivals = []
    for index in range(count):
        arrivals.append(Arrival(index, offset))
        offset += rng.expovariate(rate_rps)
    return arrivals


def closed_loop_assignments(concurrency: int, count: int) -> list[list[int]]:
    """Round-robin request indices over ``concurrency`` workers that each run sequentially."""
    if concurrency <= 0 or count <= 0:
        raise ValueError("closed loop needs positive concurrency and count")
    return [list(range(worker, count, concurrency)) for worker in range(concurrency)]
