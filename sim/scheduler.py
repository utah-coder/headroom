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
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sim.config import (
    ALT_REGIONS,
    BATTERY,
    FLYWHEEL,
    MOVE_DELAY_SLOTS,
    MOVE_PENALTY_USD,
    PUE,
    RESTART_PENALTY_MIN,
    SLOT_MINUTES,
)
from sim.grid import GridSlot
from sim.jobs import TrainingJob
from sim.site import StorageSpec
from sim.timeutil import hhmm_denver, iso_z

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
    "late_minutes",
    "job_outcome",
    "outcomes",
    "flag_late",
    "REASON_MAX_CHARS",
    "ordinal",
    "demand_percentile",
    "decision_target",
    "late_suffix",
    "reason_string",
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
    late_min: int = 0           # minutes past the deadline (with carry-over); 0 when met


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


def _expected_finish_min(js: JobState, n_slots: int) -> int:
    """Finish time in minutes from window start, carrying unfinished work past the window."""
    if js.finish_min is not None:
        return js.finish_min
    carry = js.remaining_min + (RESTART_PENALTY_MIN if js.paused else 0)
    return window_minutes(n_slots) + carry


def met_deadline(js: JobState, n_slots: int) -> bool:
    """Deadline accounting with carry-over (Requirement 10.20, DECISIONS.md)."""
    return _expected_finish_min(js, n_slots) <= js.job.deadline_min


def late_minutes(js: JobState, n_slots: int) -> int:
    """Minutes past the deadline (0 when the deadline is met), with carry-over."""
    return max(0, _expected_finish_min(js, n_slots) - js.job.deadline_min)


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
        late_min=late_minutes(js, n_slots),
    )


def outcomes(states: Iterable[JobState], n_slots: int) -> dict[str, JobOutcome]:
    return {js.job.id: job_outcome(js, n_slots) for js in states}


def flag_late(decisions: Sequence[Decision],
              job_outcomes: Mapping[str, JobOutcome]) -> list[Decision]:
    """Mark the last Decision of every late job (Requirement 11.12).

    The Decision gets ``late=True`` and its reason gets `` [LATE +N min]``
    (N = ``JobOutcome.late_min``, at least 1 for a late job).
    """
    last: dict[str, int] = {}
    for i, d in enumerate(decisions):
        last[d.job] = i
    out = list(decisions)
    for job_id, i in last.items():
        o = job_outcomes.get(job_id)
        if o is not None and o.late and not out[i].late:
            reason = _checked_len(out[i].reason + late_suffix(max(1, o.late_min)))
            out[i] = dataclasses.replace(out[i], late=True, reason=reason)
    return out


def _initial_soc_pct() -> tuple[float, float]:
    bat = StorageSpec.from_config("battery", BATTERY)
    fly = StorageSpec.from_config("flywheel", FLYWHEEL)
    return bat.initial_state().soc_pct(bat), fly.initial_state().soc_pct(fly)


_IDLE = StorageFlows(charge_kw=0.0, discharge_kw=0.0)


# --- Reason strings (design, "Decisions and reason strings") ----------------

REASON_MAX_CHARS = 200   # Requirement 13.9


def ordinal(n: int) -> str:
    """English ordinal: 1st 2nd 3rd 4th … 11th 12th 13th … 21st 22nd … 101st."""
    if n < 0:
        raise ValueError(f"ordinal needs a non-negative integer: {n}")
    if n % 100 in (11, 12, 13):
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


def demand_percentile(demand: Sequence[float], i: int) -> int:
    """PACE demand percentile of slot ``i`` (Requirement 13.4).

    ``max(1, floor(100 × #(d ≤ d_i) / N))`` over all N slots of the scenario,
    so the result is an integer from 1 to 100.
    """
    n = len(demand)
    if not 0 <= i < n:
        raise IndexError(f"slot {i} outside [0, {n})")
    d_i = demand[i]
    count = sum(1 for d in demand if d <= d_i)
    return max(1, 100 * count // n)


def _round_half_up(x: float) -> int:
    return math.floor(x + 0.5)


def _move_delay_min() -> int:
    return MOVE_DELAY_SLOTS * SLOT_MINUTES


def decision_target(route: str, defer_to: datetime | None = None,
                    move_region: str | None = None) -> str | None:
    """``Decision.target`` for a route: ISO ``Z`` resume slot for DEFER, region
    for MOVE_REGION, else ``None`` (Requirement 13.8). Pass the same values
    given to ``reason_string`` so the two agree."""
    if route == DEFER:
        if defer_to is None:
            raise ValueError("DEFER needs a resume slot time")
        return iso_z(defer_to)
    if route == MOVE_REGION:
        if move_region is None:
            raise ValueError("MOVE_REGION needs a target region")
        return move_region
    return None


def late_suffix(late_min: int) -> str:
    """`` [LATE +N min]`` for a job that misses its deadline by ``late_min`` (Requirement 11.12)."""
    if late_min <= 0:
        raise ValueError(f"late minutes must be positive: {late_min}")
    return f" [LATE +{late_min} min]"


def _checked_len(reason: str) -> str:
    if len(reason) > REASON_MAX_CHARS:
        raise ValueError(f"reason string is {len(reason)} characters "
                         f"(max {REASON_MAX_CHARS}): {reason!r}")
    return reason


def reason_string(
    t: datetime,
    job_id: str,
    route: str,
    *,
    demand_pct: int,
    carbon_g_per_kwh: float,
    peak_delta_kw: float,
    defer_to: datetime | None = None,
    move_region: str | None = None,
    move_carbon_g_per_kwh: float | None = None,
    dr_cap_kw: float | None = None,
    late_min: int | None = None,
) -> str:
    """Build a Decision's Reason_String (Requirements 12.8, 13.3–13.6, 13.9).

    Format::

        {HH:MM} job#{id} → {ROUTE}[ to {target}] — PACE demand {pct} pct,
        {ci} g/kWh, would raise monthly peak by {dk} kW[ — DR event, cap {cap:,} kW]
        [ [LATE +N min]]

    * ``t``: slot start, shown as America/Denver ``HH:MM``.
    * ``defer_to``: resume slot start for ``DEFER`` (shown as Denver ``HH:MM``).
    * ``move_region`` / ``move_carbon_g_per_kwh``: target Alternate_Region and
      its carbon intensity for the start hour; ``MOVE_REGION`` uses the compact
      form ``to BPAT ($75, +30 min, 92 g/kWh)``.
    * ``demand_pct``: from ``demand_percentile`` (1–100).
    * ``carbon_g_per_kwh``: PACE intensity, rounded to the nearest integer.
    * ``peak_delta_kw``: kW by which running the job on the grid now would raise
      the planned peak; rounded and clamped to a non-negative integer.
    * ``dr_cap_kw``: set when the route changed because of the DR_Event.
    * ``late_min``: set (> 0) when the job misses its deadline.
    """
    if not 1 <= demand_pct <= 100:
        raise ValueError(f"demand percentile must be 1–100: {demand_pct}")
    if route not in ROUTE_ORDER:
        raise ValueError(f"unknown route: {route!r}")
    if route == DEFER:
        if defer_to is None:
            raise ValueError("DEFER needs a resume slot time")
        target = f" to {hhmm_denver(defer_to)}"
    elif route == MOVE_REGION:
        if move_region not in ALT_REGIONS:
            raise ValueError(f"MOVE_REGION target must be one of {ALT_REGIONS}: {move_region!r}")
        if move_carbon_g_per_kwh is None:
            raise ValueError("MOVE_REGION needs the target region's carbon intensity")
        target = (f" to {move_region} (${MOVE_PENALTY_USD:,.0f}, +{_move_delay_min()} min, "
                  f"{_round_half_up(move_carbon_g_per_kwh)} g/kWh)")
    else:
        target = ""
    if route != DEFER and defer_to is not None:
        raise ValueError(f"{route} takes no DEFER target")
    if route != MOVE_REGION and (move_region is not None or move_carbon_g_per_kwh is not None):
        raise ValueError(f"{route} takes no MOVE_REGION target")

    dk = max(0, _round_half_up(peak_delta_kw))
    reason = (
        f"{hhmm_denver(t)} job#{job_id} → {route}{target}"
        f" — PACE demand {ordinal(demand_pct)} pct, {_round_half_up(carbon_g_per_kwh)} g/kWh,"
        f" would raise monthly peak by {dk} kW"
    )
    if dr_cap_kw is not None:
        reason += f" — DR event, cap {_round_half_up(dr_cap_kw):,} kW"
    if late_min is not None:
        reason += late_suffix(late_min)
    return _checked_len(reason)


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
    Its reason's peak change is the kW by which that job lifts the slot's grid
    draw above the highest grid draw of earlier slots.
    """
    n = inp.n_slots
    states = [JobState.new(j, n) for j in inp.jobs]
    inference_fac = inp.inference_fac
    bat_soc, fly_soc = _initial_soc_pct()
    out_slots: list[SlotResult] = []
    decisions: list[Decision] = []
    demand = [slot.pace_demand_mw for slot in inp.slots]
    peak_so_far = 0.0   # highest grid draw in earlier slots

    for s, slot in enumerate(inp.slots):
        training_it = 0.0
        arrivals: list[tuple[JobState, float]] = []   # (job, facility kW this slot)
        for js in states:
            if not js.runnable(s):
                continue
            first = js.prev_route is None
            js.prev_route = GRID_NOW
            run_it = js.run_slot(s).it_kw
            training_it += run_it
            if first:
                arrivals.append((js, facility_kw(run_it)))
        training_fac = facility_kw(training_it)
        grid_kw = inference_fac[s] + training_fac
        if arrivals:
            pct = demand_percentile(demand, s)
            for js, job_fac in arrivals:
                # kW by which this job, run on the grid now, raises the peak so far.
                delta = grid_kw - max(peak_so_far, grid_kw - job_fac)
                decisions.append(Decision(
                    t=slot.t, job=js.job.id, route=GRID_NOW,
                    target=decision_target(GRID_NOW),
                    reason=reason_string(
                        slot.t, js.job.id, GRID_NOW,
                        demand_pct=pct,
                        carbon_g_per_kwh=slot.carbon_g_per_kwh,
                        peak_delta_kw=delta,
                    ),
                    cause="COST",
                ))
        peak_so_far = max(peak_so_far, grid_kw)
        out_slots.append(SlotResult(
            t=slot.t,
            grid_kw=grid_kw,
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
