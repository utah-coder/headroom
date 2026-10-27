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

import numpy as np

from sim.config import (
    ALT_REGIONS,
    BATTERY,
    CARBON_SHADOW_USD_PER_T,
    DEADLINE_COMFORT_MIN,
    DEADLINE_RISK_USD_PER_KWH,
    DEFER_TARGET_STEP_SLOTS,
    DEMAND_CHARGE_PER_KW,
    FLYWHEEL,
    IT_PEAK_KW,
    LOOKAHEAD_SLOTS,
    MOVE_DELAY_SLOTS,
    MOVE_PENALTY_USD,
    PEAK_TARGET_SCALE,
    PUE,
    RESTART_PENALTY_MIN,
    SLOT_MINUTES,
    STORAGE_WEAR_USD_PER_KWH,
    TARIFF_OFFPEAK_PER_KWH,
    TRAINING_SHARE,
)
from sim.grid import GridSlot, stress_flags
from sim.jobs import TrainingJob
from sim.site import StorageSpec, StorageState
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
    "peak_target_kw",
    "unit_cost_per_kwh",
    "forecast_stress",
    "run_slots",
    "RouteCandidate",
    "JobTrace",
    "SlotTrace",
    "HeadroomTrace",
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
    trace: HeadroomTrace | None = None   # headroom only, with run_headroom(trace=True)


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


# --- Headroom policy (design, "Headroom policy (greedy, rolling 24 h look-ahead)")

_G_PER_T = 1_000_000          # grams per tonne
_SLOT_HOURS = SLOT_MINUTES / _MIN_PER_HOUR
_COST_DIGITS = 9              # route costs are compared after rounding to 1e-9 $


def peak_target_kw(inp: ScenarioInputs) -> float:
    """Initial planned peak ``P0`` in facility kW (DECISIONS.md).

    ``PEAK_TARGET_SCALE × PUE × (mean(inference IT kW) + IT_PEAK_KW ×
    TRAINING_SHARE)``, the site's average facility load. The Scenario_Builder
    writes it to ``meta.params.peak_target_kw``.
    """
    if not inp.inference_it_kw:
        raise ValueError(f"{inp.name}: no slots")
    mean_inference_it = sum(inp.inference_it_kw) / len(inp.inference_it_kw)
    return PEAK_TARGET_SCALE * PUE * (mean_inference_it + IT_PEAK_KW * TRAINING_SHARE)


def unit_cost_per_kwh(slot: GridSlot) -> float:
    """``price + carbon × CARBON_SHADOW_USD_PER_T / 1e6`` in $/kWh (design)."""
    return slot.price_per_kwh + slot.carbon_g_per_kwh * CARBON_SHADOW_USD_PER_T / _G_PER_T


def forecast_stress(slots: Sequence[GridSlot], horizon: range) -> list[bool]:
    """Forecast-stress flags for the slots of ``horizon``.

    A slot is forecast-stressed when its PACE ``DF`` is in the top
    ``STRESS_TOP_FRACTION`` of the ``DF`` values within the horizon, with the
    same inclusive tie rule as ``grid.stress_flags``. Reads only ``DF``
    (Requirement 11.4); the ``stress`` flag on ``GridSlot`` comes from actual
    demand and is not used for planning.
    """
    return stress_flags([slots[k].pace_forecast_mw for k in horizon])


def run_slots(start: int, minutes: int, n_slots: int) -> list[tuple[int, int]]:
    """``(slot, minutes used)`` for a job running continuously from ``start``
    for ``minutes`` minutes, truncated at the window end."""
    out: list[tuple[int, int]] = []
    k = start
    while minutes > 0 and k < n_slots:
        u = min(SLOT_MINUTES, minutes)
        out.append((k, u))
        minutes -= u
        k += 1
    return out


@dataclass(frozen=True)
class RouteCandidate:
    """One priced Route for one job in one slot (design, "Route cost")."""

    route: str
    cost: float                     # $ over the job's whole remaining run
    finish_min: int                 # projected finish, minutes from window start
    peak_excess_kw: float           # rise in the planned window peak caused by the run
    target_slot: int | None = None  # DEFER resume slot
    region: str | None = None       # MOVE_REGION target (task 8.8)


@dataclass(frozen=True)
class JobTrace:
    """How one job was routed in one slot (``run_headroom(trace=True)``)."""

    job: str
    deadline_blocked: bool                    # no DEFER target meets plan_deadline
    planned_peak_kw: float                    # max(P, planned[k ≥ s]) when priced
    storage_available_kw: float               # discharge kW left for STORAGE_NOW when priced
    grid_now: RouteCandidate                  # always priced (reason peak delta, fallback)
    candidates: tuple[RouteCandidate, ...]    # offered routes; DEFER is the best target
    defer_options: tuple[RouteCandidate, ...]  # every offered DEFER target, earliest first
    route: str
    target_slot: int | None
    cause: Cause
    decision: bool                            # a Decision was emitted


@dataclass(frozen=True)
class SlotTrace:
    slot: int
    horizon_end: int                 # look-ahead H = [slot, horizon_end)
    fstress: tuple[bool, ...]        # forecast stress over H
    peak_kw: float                   # P while routing this slot
    jobs: tuple[JobTrace, ...]       # in processing order
    load_kw: float                   # site load before storage
    storage_commit_kw: float         # facility kW of STORAGE_NOW jobs
    grid_kw: float
    peak_after_kw: float             # P after this slot


@dataclass(frozen=True)
class HeadroomTrace:
    peak_target_kw: float            # P0
    slots: tuple[SlotTrace, ...]


def _route_key(c: RouteCandidate) -> tuple[float, int]:
    """Lowest cost, ties broken by ROUTE_ORDER (Requirement 11.8)."""
    return round(c.cost, _COST_DIGITS), ROUTE_ORDER.index(c.route)  # type: ignore[arg-type]


def _first_defer_slot(s: int) -> int:
    """First on-the-hour slot after ``s`` (DEFER targets step by DEFER_TARGET_STEP_SLOTS)."""
    return (s // DEFER_TARGET_STEP_SLOTS + 1) * DEFER_TARGET_STEP_SLOTS


def _defer_deadline_ok(js: JobState, t: int) -> bool:
    """Resuming at ``t`` still meets plan_deadline with one Restart_Penalty (Requirement 11.6)."""
    return t * SLOT_MINUTES + js.remaining_min + RESTART_PENALTY_MIN <= js.plan_deadline


def _pending_restart_min(js: JobState) -> int:
    """Restart_Penalty a paused job pays when it next runs."""
    return RESTART_PENALTY_MIN if js.paused else 0


class _HeadroomPlanner:
    """Per-run state of the greedy Headroom_Policy.

    Planning inputs for slots after the current one are PACE ``DF``, carbon
    intensity and the tariff only (Requirement 11.4). PACE ``D`` is read in
    ``run_headroom`` solely for the reason-string demand percentile, a
    reporting value that Requirement 13.4 defines over all N slots; it never
    reaches the cost or candidate code below.

    Task 8.7 extends ``_grid_budget_kw``, ``_discharge_floor_kwh``,
    ``cause_for`` and ``dispatch_storage`` (DR budget, reserve floors, DR
    cause, peak and stress shaving, charging); task 8.8 fills in
    ``_move_candidate``.
    """

    def __init__(self, inp: ScenarioInputs) -> None:
        self.inp = inp
        self.n = inp.n_slots
        self.inference_fac = inp.inference_fac
        self.base = np.array(self.inference_fac, dtype=float)
        self.planned = self.base.copy()         # forecast on-site grid kW per slot
        self.unit_cost = np.array([unit_cost_per_kwh(g) for g in inp.slots], dtype=float)
        self.eff_cost = self.unit_cost.copy()
        self.peak_target = peak_target_kw(inp)
        self.P = self.peak_target
        self.dr = frozenset(inp.dr_range)
        self.bat_spec = StorageSpec.from_config("battery", BATTERY)
        self.fly_spec = StorageSpec.from_config("flywheel", FLYWHEEL)
        self.bat: StorageState = self.bat_spec.initial_state()
        self.fly: StorageState = self.fly_spec.initial_state()
        mean_ci = sum(g.carbon_g_per_kwh for g in inp.slots) / self.n
        # $/kWh of stored energy (energy + carbon); task 8.7 updates it on charge.
        self.storage_cost_basis = (TARIFF_OFFPEAK_PER_KWH
                                   + mean_ci * CARBON_SHADOW_USD_PER_T / _G_PER_T)
        self.s = 0
        self.h_end = 0
        self.fstress: tuple[bool, ...] = ()

    # --- per-slot setup

    def begin_slot(self, s: int) -> None:
        self.s = s
        self.h_end = min(self.n, s + LOOKAHEAD_SLOTS)            # Requirement 11.3
        self.fstress = tuple(forecast_stress(self.inp.slots, range(s, self.h_end)))
        # Release every reservation from slot s on. Only runnable jobs hold
        # reservations there, and each is re-added as it is routed below, so
        # resetting to inference is the release step without float drift.
        self.planned[s:] = self.base[s:]
        # Slots past H (possible only when H is a full 24 h) are priced with a
        # 24-hour persistence forecast from inside H, so every route is priced
        # over the same work without reading data beyond the look-ahead.
        idx = np.arange(s, self.n)
        self.eff_cost[s:] = self.unit_cost[s + (idx - s) % LOOKAHEAD_SLOTS]

    def is_fstress(self, t: int) -> bool:
        return self.fstress[t - self.s]

    # --- pricing

    def _profile(self, start: int, minutes: int) -> tuple[int, int, np.ndarray]:
        if minutes <= 0 or start >= self.n:
            return start, start, np.empty(0)
        count = -(-minutes // SLOT_MINUTES)
        end = min(self.n, start + count)
        u = np.full(end - start, float(SLOT_MINUTES))
        if start + count <= self.n:
            u[-1] = minutes - (count - 1) * SLOT_MINUTES
        return start, end, u

    def planned_peak_kw(self) -> float:
        """Window peak already planned: ``max(P, planned[k] for k ≥ s)``."""
        return max(self.P, float(np.max(self.planned[self.s:])))

    def price_run(self, kw_fac: float, start: int, minutes: int,
                  ref_peak: float) -> tuple[float, float]:
        """``(energy_cost $, peak increase kW)`` of running ``kw_fac`` from ``start``.

        The increase is measured against ``ref_peak`` (``planned_peak_kw``),
        so it is the rise in the window's planned peak caused by this run
        (glossary, Demand_Charge_Risk). While no reservation exceeds ``P`` this
        equals the design's ``max(0, max_k(planned[k] + kW_k) − P)``.
        """
        a, b, u = self._profile(start, minutes)
        if b <= a:
            return 0.0, 0.0
        energy = kw_fac / _MIN_PER_HOUR * float(np.dot(u, self.eff_cost[a:b]))
        peak = float(np.max(self.planned[a:b] + kw_fac * u / SLOT_MINUTES))
        return energy, max(0.0, peak - ref_peak)

    def reserve(self, kw_fac: float, start: int, minutes: int) -> None:
        a, b, u = self._profile(start, minutes)
        if b > a:
            self.planned[a:b] += kw_fac * u / SLOT_MINUTES

    def deadline_pen(self, js: JobState, finish_min: int) -> float:
        kwh_fac = facility_kw(js.job.power_kw) * js.remaining_min / _MIN_PER_HOUR
        slack = js.plan_deadline - finish_min
        return DEADLINE_RISK_USD_PER_KWH * kwh_fac * max(0.0, 1 - slack / DEADLINE_COMFORT_MIN)

    def deadline_blocked(self, js: JobState) -> bool:
        """No DEFER target can meet plan_deadline (later targets only finish later).

        Task 8.8 also requires MOVE_REGION to be deadline-infeasible.
        """
        return not _defer_deadline_ok(js, _first_defer_slot(self.s))

    # --- hooks extended by tasks 8.7 and 8.8

    def _grid_budget_kw(self) -> float:
        """GRID_NOW is offered while ``load + kWfac ≤ budget`` (DR budget: task 8.7)."""
        return math.inf

    def _discharge_floor_kwh(self, spec: StorageSpec) -> float | None:
        """SOC floor for discharge; ``None`` = the spec's 10 % floor (reserve floors: task 8.7)."""
        return None

    def _move_candidate(self, js: JobState) -> RouteCandidate | None:
        """MOVE_REGION is not offered yet (task 8.8)."""
        return None

    def cause_for(self, js: JobState, chosen: RouteCandidate,
                  offered: Sequence[RouteCandidate]) -> Cause:
        """Cause of a cost-based choice (DR attribution: task 8.7)."""
        return "COST"

    # --- storage

    def available_discharge_kw(self) -> float:
        """Battery + flywheel discharge kW for one slot, rating- and floor-limited."""
        return (self.bat.max_discharge_kw(self.bat_spec, _SLOT_HOURS,
                                          self._discharge_floor_kwh(self.bat_spec))
                + self.fly.max_discharge_kw(self.fly_spec, _SLOT_HOURS,
                                            self._discharge_floor_kwh(self.fly_spec)))

    def dispatch_storage(self, load_kw: float,
                         commit_kw: float) -> tuple[StorageFlows, StorageFlows]:
        """Serve the slot's committed STORAGE_NOW kW, battery first, then flywheel.

        The STORAGE_NOW filter keeps the commitment within the available
        discharge, so the flywheel share fits its rating and floor; ``step``
        enforces the SOC bounds. Task 8.7 adds DR, peak and stress shaving and
        charging here.
        """
        bat_avail = self.bat.max_discharge_kw(self.bat_spec, _SLOT_HOURS,
                                              self._discharge_floor_kwh(self.bat_spec))
        bat_d = min(commit_kw, bat_avail)
        fly_d = commit_kw - bat_d
        self.bat = self.bat.step(self.bat_spec, 0.0, bat_d, _SLOT_HOURS)
        self.fly = self.fly.step(self.fly_spec, 0.0, fly_d, _SLOT_HOURS)
        return StorageFlows(0.0, bat_d), StorageFlows(0.0, fly_d)

    # --- candidates (design, "Candidate filtering")

    def defer_options(self, js: JobState, kw_fac: float, ref_peak: float) -> list[RouteCandidate]:
        s = self.s
        rem = js.remaining_min + (RESTART_PENALTY_MIN if js.started else 0)
        restart_kwh = kw_fac * RESTART_PENALTY_MIN / _MIN_PER_HOUR if js.started else 0.0
        out: list[RouteCandidate] = []
        for t in range(_first_defer_slot(s), self.h_end, DEFER_TARGET_STEP_SLOTS):
            if not _defer_deadline_ok(js, t):
                break
            if t in self.dr or self.is_fstress(t):
                continue
            energy, excess = self.price_run(kw_fac, t, rem, ref_peak)
            finish = t * SLOT_MINUTES + rem
            cost = (energy + DEMAND_CHARGE_PER_KW * excess
                    + restart_kwh * self.unit_cost[t] + self.deadline_pen(js, finish))
            out.append(RouteCandidate(DEFER, cost, finish, excess, target_slot=t))
        return out

    def price_routes(self, js: JobState, load_kw: float, commit_kw: float
                     ) -> tuple[RouteCandidate, list[RouteCandidate], list[RouteCandidate],
                                float, float]:
        """``(GRID_NOW, offered, DEFER options, planned peak, storage kW left)`` for ``js``."""
        s = self.s
        kw_fac = facility_kw(js.job.power_kw)
        rem_now = js.remaining_min + _pending_restart_min(js)
        now_finish = s * SLOT_MINUTES + rem_now
        pen_now = self.deadline_pen(js, now_finish)
        ref_peak = self.planned_peak_kw()

        energy, excess = self.price_run(kw_fac, s, rem_now, ref_peak)
        grid = RouteCandidate(GRID_NOW, energy + DEMAND_CHARGE_PER_KW * excess + pen_now,
                              now_finish, excess)
        offered: list[RouteCandidate] = []
        if load_kw + kw_fac <= self._grid_budget_kw():
            offered.append(grid)

        # STORAGE_NOW (Requirement 11.16): outside DR, enough discharge left for
        # the job's full facility kW, and, like every non-grid route
        # (Requirement 11.9), not finishing after plan_deadline. Its finish
        # equals GRID_NOW's, so a job that cannot finish in the window even
        # running now stays on GRID_NOW every slot, like baseline (DECISIONS.md).
        storage_left = self.available_discharge_kw() - commit_kw
        if s not in self.dr and now_finish <= js.plan_deadline and storage_left >= kw_fac:
            u_s = slot_minutes_used(rem_now)
            first = (kw_fac * u_s / _MIN_PER_HOUR
                     * (self.storage_cost_basis / self.bat_spec.eta + STORAGE_WEAR_USD_PER_KWH))
            energy, excess = self.price_run(kw_fac, s + 1, rem_now - u_s, ref_peak)
            offered.append(RouteCandidate(
                STORAGE_NOW, first + energy + DEMAND_CHARGE_PER_KW * excess + pen_now,
                now_finish, excess))

        options = self.defer_options(js, kw_fac, ref_peak)
        if options:   # best target: lowest cost, earliest on ties
            offered.append(min(options, key=lambda c: (round(c.cost, _COST_DIGITS),
                                                       c.target_slot)))
        move = self._move_candidate(js)
        if move is not None:
            offered.append(move)
        return grid, offered, options, ref_peak, storage_left

    # --- applying a route

    def apply(self, js: JobState, c: RouteCandidate) -> float:
        """Reserve the job's future run and run or pause it; returns on-site facility kW."""
        s = self.s
        kw_fac = facility_kw(js.job.power_kw)
        if c.route in (GRID_NOW, STORAGE_NOW):
            js.defer_target = None
            rem_now = js.remaining_min + _pending_restart_min(js)
            if c.route == GRID_NOW:
                self.reserve(kw_fac, s, rem_now)
            else:   # storage serves slot s; the grid sees the job from s + 1
                self.reserve(kw_fac, s + 1, rem_now - slot_minutes_used(rem_now))
            return facility_kw(js.run_slot(s).it_kw)
        if c.route == DEFER:
            assert c.target_slot is not None
            js.pause()
            js.defer_target = c.target_slot
            self.reserve(kw_fac, c.target_slot, js.remaining_min + _pending_restart_min(js))
            return 0.0
        raise ValueError(f"route {c.route} is not supported yet")


def _headroom_decision(inp: ScenarioInputs, s: int, js: JobState, c: RouteCandidate,
                       cause: Cause, demand_pct: int, peak_delta_kw: float) -> Decision:
    slot = inp.slots[s]
    defer_to = inp.slots[c.target_slot].t if c.route == DEFER and c.target_slot is not None else None
    dr_cap = inp.cap_kw if cause == "DR" else None
    reason = reason_string(
        slot.t, js.job.id, c.route,
        demand_pct=demand_pct,
        carbon_g_per_kwh=slot.carbon_g_per_kwh,
        peak_delta_kw=peak_delta_kw,
        defer_to=defer_to,
        dr_cap_kw=dr_cap,
    )
    return Decision(t=slot.t, job=js.job.id, route=c.route,
                    target=decision_target(c.route, defer_to=defer_to),
                    reason=reason, cause=cause)


def run_headroom(inp: ScenarioInputs, *, trace: bool = False) -> PolicyResult:
    """Greedy Headroom_Policy with a rolling 24 h look-ahead (Requirements 11.3–11.12, 11.16, 11.17).

    Per slot ``s``: release reservations, order runnable jobs (deadline-blocked
    first, then earliest plan_deadline, then id), price GRID_NOW, STORAGE_NOW
    and DEFER(t*) over each job's whole remaining run, filter them, and pick
    the lowest cost (rounded to 1e-9, ties by ``ROUTE_ORDER``). A job with no
    non-grid candidate left because no DEFER target meets its plan_deadline
    gets ``GRID_NOW`` with cause ``DEADLINE``. Earlier slots are never revised.
    A Decision is emitted only when a job's route differs from its previous
    slot's (Requirements 13.1, 13.2, 13.7); late jobs are flagged at the end.

    Storage dispatch serves committed STORAGE_NOW load only (task 8.6); DR
    handling, shaving and charging come with task 8.7, MOVE_REGION with 8.8.

    With ``trace=True`` the result carries a ``HeadroomTrace`` of every
    slot's candidates, costs, pre-storage load and ``P``.
    """
    n = inp.n_slots
    planner = _HeadroomPlanner(inp)
    states = [JobState.new(j, n) for j in inp.jobs]
    demand = [g.pace_demand_mw for g in inp.slots]   # reason percentile only (Requirement 13.4)
    out_slots: list[SlotResult] = []
    decisions: list[Decision] = []
    slot_traces: list[SlotTrace] = []

    for s, slot in enumerate(inp.slots):
        planner.begin_slot(s)
        runnable = [js for js in states if js.runnable(s) and js.moved_region is None]
        blocked = {js.job.id: planner.deadline_blocked(js) for js in runnable}
        runnable.sort(key=lambda js: (not blocked[js.job.id], js.plan_deadline, int(js.job.id)))
        peak_routing = planner.P
        inference = planner.inference_fac[s]
        training = 0.0
        commit = 0.0
        demand_pct: int | None = None
        job_traces: list[JobTrace] = []

        for js in runnable:
            grid, offered, options, ref_peak, storage_left = planner.price_routes(
                js, inference + training, commit)
            if blocked[js.job.id] and not any(c.route != GRID_NOW for c in offered):
                chosen, cause = grid, "DEADLINE"     # Requirement 11.9
            else:
                chosen = min(offered, key=_route_key)
                cause = planner.cause_for(js, chosen, offered)
            kw = planner.apply(js, chosen)
            training += kw
            if chosen.route == STORAGE_NOW:
                commit += kw
            emit = js.prev_route != chosen.route
            if emit:
                if demand_pct is None:
                    demand_pct = demand_percentile(demand, s)
                decisions.append(_headroom_decision(inp, s, js, chosen, cause, demand_pct,
                                                    grid.peak_excess_kw))
            js.prev_route = chosen.route
            if trace:
                job_traces.append(JobTrace(
                    job=js.job.id, deadline_blocked=blocked[js.job.id],
                    planned_peak_kw=ref_peak, storage_available_kw=storage_left, grid_now=grid,
                    candidates=tuple(offered), defer_options=tuple(options),
                    route=chosen.route, target_slot=chosen.target_slot,
                    cause=cause, decision=emit,
                ))

        load = inference + training
        bat, fly = planner.dispatch_storage(load, commit)
        grid_kw = load - bat.discharge_kw - fly.discharge_kw + bat.charge_kw + fly.charge_kw
        if grid_kw < -1e-9:
            raise RuntimeError(f"{inp.name}: slot {s} grid draw {grid_kw} kW is negative")
        grid_kw = max(0.0, grid_kw)                                 # Requirement 10.15
        planner.P = max(planner.P, grid_kw)
        out_slots.append(SlotResult(
            t=slot.t,
            grid_kw=grid_kw,
            inference_kw=inference,                                 # Requirement 11.10
            training_kw=training,
            battery=bat,
            flywheel=fly,
            battery_soc_pct=planner.bat.soc_pct(planner.bat_spec),
            flywheel_soc_pct=planner.fly.soc_pct(planner.fly_spec),
            moved_kw=0.0,
        ))
        if trace:
            slot_traces.append(SlotTrace(
                slot=s, horizon_end=planner.h_end, fstress=planner.fstress,
                peak_kw=peak_routing, jobs=tuple(job_traces), load_kw=load,
                storage_commit_kw=commit, grid_kw=grid_kw, peak_after_kw=planner.P,
            ))

    job_outcomes = outcomes(states, n)
    return PolicyResult(
        policy="headroom",
        slots=tuple(out_slots),
        decisions=tuple(flag_late(decisions, job_outcomes)),
        outcomes=job_outcomes,
        moves=(),
        trace=HeadroomTrace(planner.peak_target, tuple(slot_traces)) if trace else None,
    )
