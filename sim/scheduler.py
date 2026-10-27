"""Scheduler: baseline and Headroom policies behind one interface.

``run(policy, inp)`` takes the same ``ScenarioInputs`` for both policies and
returns the same ``PolicyResult`` structure: one ``SlotResult`` per Slot and a
list of ``Decision``s (Requirement 11.1).

Units: every kW in ``SlotResult`` is facility kW (IT kW × PUE, Requirement
10.3). ``JobState.delivered_kwh`` and ``JobOutcome.delivered_kwh`` are IT kWh.
Times inside the scheduler are slot indices or minutes from window start;
``Decision.t`` and ``SlotResult.t`` are the UTC slot start.

Shared job mechanics (design, "Shared job mechanics"):

* A running job uses ``u = min(SLOT_MINUTES, remaining_min)`` minutes of a
  slot. Its IT draw averaged over the slot is ``power_kw × u / SLOT_MINUTES``
  and it delivers ``power_kw × u / 60`` IT kWh.
* Routes are assigned per slot, so a job pauses and resumes only at slot
  boundaries (Requirement 10.9). A job paused after it has run, then resumed,
  gets ``remaining_min += RESTART_PENALTY_MIN`` and ``resumes += 1``
  (Requirement 10.10). Pausing before the first run costs nothing.
* Deadline accounting with carry-over (DECISIONS.md): a job completed in the
  window met its deadline iff ``finish_min ≤ deadline_min``; a job unfinished
  at the window end met it iff ``N × SLOT_MINUTES + remaining_min ≤
  deadline_min`` (plus one Restart_Penalty if it ends the window paused,
  because it must resume to finish). Any other job is late (Requirement 10.20).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sim.config import BATTERY, FLYWHEEL, PUE, RESTART_PENALTY_MIN, SLOT_MINUTES
from sim.grid import GridSlot
from sim.jobs import TrainingJob
from sim.site import StorageSpec
from sim.timeutil import hhmm_denver

__all__ = [
    "Route",
    "Cause",
    "ROUTE_ORDER",
    "GRID_NOW",
    "STORAGE_NOW",
    "DEFER",
    "MOVE_REGION",
    "ScenarioInputs",
    "JobState",
    "SlotRun",
    "StorageFlows",
    "SlotResult",
    "Decision",
    "JobOutcome",
    "MoveRecord",
    "PolicyResult",
    "slot_minutes_used",
    "facility_kw",
    "it_kwh",
    "window_minutes",
    "met_deadline",
    "job_outcome",
    "outcomes",
    "flag_late",
    "run",
    "run_baseline",
    "run_headroom",
]

Route = Literal["GRID_NOW", "STORAGE_NOW", "DEFER", "MOVE_REGION"]
Cause = Literal["COST", "DEADLINE", "DR"]

GRID_NOW: Route = "GRID_NOW"
STORAGE_NOW: Route = "STORAGE_NOW"
DEFER: Route = "DEFER"
MOVE_REGION: Route = "MOVE_REGION"
# Tie-break order for equal Route_Costs (Requirement 11.8).
ROUTE_ORDER: tuple[Route, ...] = (GRID_NOW, STORAGE_NOW, DEFER, MOVE_REGION)

_MIN_PER_HOUR = 60


# --- Data models (design, "Python (in-memory)") ------------------------------

@dataclass(frozen=True)
class ScenarioInputs:
    """Everything one policy run needs; identical for both policies."""

    name: str                                         # "peak" | "sun"
    slots: tuple[GridSlot, ...]                       # length N
    jobs: tuple[TrainingJob, ...]
    inference_it_kw: tuple[float, ...]                # IT kW per slot, length N
    idle_capacity_kw: Mapping[str, tuple[float, ...]]  # per Alternate_Region per hour
    dr_start: int                                     # first DR slot index
    dr_slots: int                                     # DR length in slots
    requested_shed_kw: float
    cap_kw: float | None                              # Grid_Import_Cap, set after the baseline run
    seed: int

    def __post_init__(self) -> None:
        n = len(self.slots)
        if len(self.inference_it_kw) != n:
            raise ValueError(
                f"{self.name}: {len(self.inference_it_kw)} inference values for {n} slots"
            )
        for j in self.jobs:
            if not 0 <= j.arrival_slot < n:
                raise ValueError(
                    f"{self.name}: job {j.id} arrives in slot {j.arrival_slot}, outside [0, {n})"
                )
        if self.dr_slots < 0 or (self.dr_slots and not 0 <= self.dr_start <= n - self.dr_slots):
            raise ValueError(
                f"{self.name}: DR slots [{self.dr_start}, {self.dr_start + self.dr_slots}) "
                f"outside [0, {n})"
            )

    @property
    def n_slots(self) -> int:
        return len(self.slots)

    @property
    def inference_fac(self) -> tuple[float, ...]:
        """Inference facility kW per slot (IT × PUE, Requirement 10.3)."""
        return tuple(kw * PUE for kw in self.inference_it_kw)

    @property
    def dr_range(self) -> range:
        """Slot indices of the DR_Event."""
        return range(self.dr_start, self.dr_start + self.dr_slots)


@dataclass(frozen=True)
class SlotRun:
    """What one job did in one slot."""

    minutes: int        # u = min(SLOT_MINUTES, remaining_min) before the run
    it_kw: float        # slot-average IT kW
    it_kwh: float       # delivered IT energy


@dataclass
class JobState:
    """Mutable per-policy-run state of one Training_Job."""

    job: TrainingJob
    remaining_min: int
    run_min: int = 0
    resumes: int = 0
    started: bool = False
    paused: bool = False
    moved_region: str | None = None
    moved_start_slot: int | None = None
    delivered_kwh: float = 0.0     # IT kWh
    finish_min: int | None = None  # minutes from window start
    prev_route: str | None = None
    defer_target: int | None = None
    plan_deadline: int = 0

    @classmethod
    def new(cls, job: TrainingJob, n_slots: int) -> "JobState":
        """Fresh state; ``plan_deadline = min(deadline_min, N × SLOT_MINUTES)`` (DECISIONS.md)."""
        return cls(
            job=job,
            remaining_min=job.duration_min,
            plan_deadline=min(job.deadline_min, window_minutes(n_slots)),
        )

    def runnable(self, s: int) -> bool:
        """Arrived at or before slot ``s`` and not finished (Requirement 11.5)."""
        return self.job.arrival_slot <= s and self.remaining_min > 0

    def pause(self) -> None:
        """Stop running at the next slot boundary.

        Only a job that has already run is marked paused, so a job deferred
        before its first slot resumes without a Restart_Penalty.
        """
        if self.started and self.remaining_min > 0:
            self.paused = True

    def run_slot(self, s: int) -> SlotRun:
        """Run this job for slot ``s``, resuming it first if it was paused."""
        if s < self.job.arrival_slot:
            raise ValueError(f"job {self.job.id} cannot run in slot {s} before arrival "
                             f"slot {self.job.arrival_slot}")
        if self.remaining_min <= 0:
            raise ValueError(f"job {self.job.id} has no remaining run time in slot {s}")
        if self.paused:
            self.remaining_min += RESTART_PENALTY_MIN   # Requirement 10.10
            self.resumes += 1
            self.paused = False
        u = slot_minutes_used(self.remaining_min)
        self.started = True
        self.remaining_min -= u
        self.run_min += u
        kwh = it_kwh(self.job.power_kw, u)
        self.delivered_kwh += kwh
        if self.remaining_min == 0:
            self.finish_min = s * SLOT_MINUTES + u
        return SlotRun(minutes=u, it_kw=self.job.power_kw * u / SLOT_MINUTES, it_kwh=kwh)


@dataclass(frozen=True)
class StorageFlows:
    """One device's flows in one slot (kW); never both above zero (Requirement 10.14)."""

    charge_kw: float
    discharge_kw: float


@dataclass(frozen=True)
class SlotResult:
    t: datetime                 # UTC slot start
    grid_kw: float              # facility kW (× PUE)
    inference_kw: float
    training_kw: float
    battery: StorageFlows
    flywheel: StorageFlows
    battery_soc_pct: float
    flywheel_soc_pct: float
    moved_kw: float             # facility-equivalent kW running off-site, not in grid_kw

    @property
    def battery_kw(self) -> float:
        """Signed battery kW: positive discharge, negative charge."""
        return self.battery.discharge_kw - self.battery.charge_kw

    @property
    def flywheel_kw(self) -> float:
        """Signed flywheel kW: positive discharge, negative charge."""
        return self.flywheel.discharge_kw - self.flywheel.charge_kw


@dataclass(frozen=True)
class Decision:
    t: datetime                 # UTC slot start (Requirement 13.8)
    job: str
    route: str
    target: str | None          # ISO Z slot time for DEFER, region for MOVE_REGION, else None
    reason: str
    cause: Cause
    late: bool = False


@dataclass(frozen=True)
class JobOutcome:
    job_id: str
    finish_min: int | None
    delivered_kwh: float        # IT kWh
    resumes: int
    completed_in_window: bool
    met_deadline: bool
    late: bool


@dataclass(frozen=True)
class MoveRecord:
    job_id: str
    region: str
    decided_slot: int
    start_slot: int
    kwh_by_slot: Mapping[int, float]


@dataclass(frozen=True)
class PolicyResult:
    policy: str
    slots: tuple[SlotResult, ...]
    decisions: tuple[Decision, ...]
    outcomes: Mapping[str, JobOutcome]
    moves: tuple[MoveRecord, ...]


# --- Shared mechanics --------------------------------------------------------

def slot_minutes_used(remaining_min: int) -> int:
    """Minutes a running job uses in one slot: ``min(SLOT_MINUTES, remaining_min)``."""
    if remaining_min <= 0:
        raise ValueError(f"remaining run time must be positive: {remaining_min}")
    return min(SLOT_MINUTES, remaining_min)


def facility_kw(it_kw: float) -> float:
    """Facility kW for an IT kW draw (Requirement 10.3)."""
    return it_kw * PUE


def it_kwh(power_kw: float, minutes: int) -> float:
    """IT energy delivered by ``power_kw`` running for ``minutes``."""
    return power_kw * minutes / _MIN_PER_HOUR


def window_minutes(n_slots: int) -> int:
    """Length of an ``n_slots`` window in minutes."""
    return n_slots * SLOT_MINUTES


def met_deadline(js: JobState, n_slots: int) -> bool:
    """Deadline accounting with carry-over (Requirement 10.20, DECISIONS.md)."""
    deadline = js.job.deadline_min
    if js.finish_min is not None:
        return js.finish_min <= deadline
    carry = js.remaining_min + (RESTART_PENALTY_MIN if js.paused else 0)
    return window_minutes(n_slots) + carry <= deadline


def job_outcome(js: JobState, n_slots: int) -> JobOutcome:
    met = met_deadline(js, n_slots)
    return JobOutcome(
        job_id=js.job.id,
        finish_min=js.finish_min,
        delivered_kwh=js.delivered_kwh,
        resumes=js.resumes,
        completed_in_window=js.finish_min is not None,
        met_deadline=met,
        late=not met,
    )


def outcomes(states: Iterable[JobState], n_slots: int) -> dict[str, JobOutcome]:
    return {js.job.id: job_outcome(js, n_slots) for js in states}


def flag_late(decisions: Sequence[Decision],
              job_outcomes: Mapping[str, JobOutcome]) -> list[Decision]:
    """Mark the last Decision of every late job with ``late=True`` (Requirement 11.12)."""
    last: dict[str, int] = {}
    for i, d in enumerate(decisions):
        last[d.job] = i
    out = list(decisions)
    for job_id, i in last.items():
        o = job_outcomes.get(job_id)
        if o is not None and o.late:
            out[i] = dataclasses.replace(out[i], late=True)
    return out


def _initial_soc_pct() -> tuple[float, float]:
    bat = StorageSpec.from_config("battery", BATTERY)
    fly = StorageSpec.from_config("flywheel", FLYWHEEL)
    return bat.initial_state().soc_pct(bat), fly.initial_state().soc_pct(fly)


_IDLE = StorageFlows(charge_kw=0.0, discharge_kw=0.0)


def _interim_reason(t: datetime, job_id: str, route: str) -> str:
    # Placeholder until the full Reason_String helpers land (task 8.2).
    return f"{hhmm_denver(t)} job#{job_id} → {route}"


# --- Policies ----------------------------------------------------------------

def run(policy: Literal["baseline", "headroom"], inp: ScenarioInputs) -> PolicyResult:
    """Run one policy over the scenario (Requirement 11.1)."""
    if policy == "baseline":
        return run_baseline(inp)
    if policy == "headroom":
        return run_headroom(inp)
    raise ValueError(f"unknown policy: {policy!r}")


def run_baseline(inp: ScenarioInputs) -> PolicyResult:
    """Every job runs on grid power continuously from arrival (Requirements 11.2, 13.2).

    No pauses, deferral or moves; Battery and Flywheel stay idle at their
    initial SOC; each job gets one ``GRID_NOW`` Decision at its arrival slot.
    """
    n = inp.n_slots
    states = [JobState.new(j, n) for j in inp.jobs]
    inference_fac = inp.inference_fac
    bat_soc, fly_soc = _initial_soc_pct()
    out_slots: list[SlotResult] = []
    decisions: list[Decision] = []

    for s, slot in enumerate(inp.slots):
        training_it = 0.0
        for js in states:
            if not js.runnable(s):
                continue
            if js.prev_route is None:
                decisions.append(Decision(
                    t=slot.t, job=js.job.id, route=GRID_NOW, target=None,
                    reason=_interim_reason(slot.t, js.job.id, GRID_NOW), cause="COST",
                ))
                js.prev_route = GRID_NOW
            training_it += js.run_slot(s).it_kw
        training_fac = facility_kw(training_it)
        out_slots.append(SlotResult(
            t=slot.t,
            grid_kw=inference_fac[s] + training_fac,
            inference_kw=inference_fac[s],
            training_kw=training_fac,
            battery=_IDLE,
            flywheel=_IDLE,
            battery_soc_pct=bat_soc,
            flywheel_soc_pct=fly_soc,
            moved_kw=0.0,
        ))

    job_outcomes = outcomes(states, n)
    return PolicyResult(
        policy="baseline",
        slots=tuple(out_slots),
        decisions=tuple(flag_late(decisions, job_outcomes)),
        outcomes=job_outcomes,
        moves=(),
    )


def run_headroom(inp: ScenarioInputs) -> PolicyResult:
    """Greedy Headroom_Policy (tasks 8.6–8.8)."""
    raise NotImplementedError("headroom policy is not implemented yet")
