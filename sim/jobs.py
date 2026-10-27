"""Seeded synthetic training jobs (SIMULATED).

Training jobs are synthetic power profiles only: power, duration, arrival and
deadline (Requirement 24.4). Arrivals follow a Poisson process whose rate is
derived from config so the expected aggregate training load equals
``IT_PEAK_KW * TRAINING_SHARE`` (Requirement 10.4).
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass

from sim import config as c

JOBS_PURPOSE = "jobs"


@dataclass(frozen=True)
class TrainingJob:
    id: str              # "4001", "4002", ...
    arrival_slot: int
    power_kw: int        # IT kW, on the JOB_POWER_KW stepped grid
    duration_min: int    # on the JOB_DURATION_MIN stepped grid
    slack_min: int       # on the JOB_SLACK_MIN stepped grid

    @property
    def arrival_min(self) -> int:
        return self.arrival_slot * c.SLOT_MINUTES

    @property
    def deadline_min(self) -> int:
        """Deadline in minutes from window start: arrival + duration + slack."""
        return self.arrival_min + self.duration_min + self.slack_min


def rng_for(seed: int, scenario: str, purpose: str) -> random.Random:
    """One independent, reproducible generator per (seed, scenario, purpose)."""
    digest = hashlib.sha256(f"{seed}:{scenario}:{purpose}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def arrival_rate_per_hour() -> float:
    """λ such that λ × mean power × mean duration = expected training IT kW."""
    mean_power_kw = sum(c.JOB_POWER_KW) / 2
    mean_duration_h = sum(c.JOB_DURATION_MIN) / 2 / 60
    return c.IT_PEAK_KW * c.TRAINING_SHARE / (mean_power_kw * mean_duration_h)


def _stepped(rng: random.Random, bounds: tuple[int, int], step: int) -> int:
    """Uniform draw from lo, lo+step, ..., hi (inclusive)."""
    lo, hi = bounds
    return rng.randrange(lo, hi + 1, step)


def generate(n_slots: int, seed: int, scenario: str) -> list[TrainingJob]:
    """Poisson training-job arrivals over ``[0, n_slots × SLOT_MINUTES)`` minutes."""
    rng = rng_for(seed, scenario, JOBS_PURPOSE)
    rate_per_min = arrival_rate_per_hour() / 60
    window_min = n_slots * c.SLOT_MINUTES
    jobs: list[TrainingJob] = []
    t = 0.0
    while True:
        t += rng.expovariate(rate_per_min)
        if t >= window_min:
            break
        jobs.append(TrainingJob(
            id=str(c.JOB_ID_BASE + len(jobs)),
            arrival_slot=int(t // c.SLOT_MINUTES),
            power_kw=_stepped(rng, c.JOB_POWER_KW, c.JOB_POWER_STEP_KW),
            duration_min=_stepped(rng, c.JOB_DURATION_MIN, c.JOB_DURATION_STEP_MIN),
            slack_min=_stepped(rng, c.JOB_SLACK_MIN, c.JOB_SLACK_STEP_MIN),
        ))
    return jobs
