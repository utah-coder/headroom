"""Metrics_Calculator: headline KPIs from the baseline and Headroom runs.

``compute(inp, base, head, dr)`` applies the design's formulas (design,
"sim/metrics.py"):

* ``headroom_kw``: max baseline ``grid_kw`` over Stress_Slots minus max
  Headroom ``grid_kw`` over the same slots (Requirement 15.1).
* ``usd_saved``: ``cost(base) − cost(head)``, where ``cost`` is grid energy at
  the slot tariff plus the window peak × demand charge plus, for Headroom only,
  the transfer penalty per successful move (Requirements 10.17, 15.2).
* ``co2_avoided_t``: ``(em(base) − em(head)) / 1e6``, where ``em`` is grid
  energy × PACE carbon plus, for Headroom only, moved energy × the target
  region's carbon (Requirements 12.6, 15.3).
* ``deadlines_met_pct``: Headroom jobs that met their deadline under the
  carry-over rule ÷ jobs generated (Requirement 15.4).
* ``inference_sla_pct``: slots whose Headroom inference kW equals the
  Inference_Load ÷ N (Requirement 15.5).
* ``dr_delivered_pct``: ``100 × (base.grid[dr_start] − max(head.grid[dr
  slots])) / Requested_Shed`` clamped to [0, 100] and rounded; the unclamped
  value is kept as ``dr_delivered_raw_pct`` for ``meta.params`` (Requirement
  14.9, DECISIONS.md).
* ``dr_seconds_to_full``: first Event_Zoom second with the flywheel at 0 kW and
  the battery at the required storage output (Requirement 14.10).
* ``baseline_site_peak_kw`` / ``headroom_site_peak_kw``: the highest grid draw
  of each policy over the whole window, the grid connection the site needs.

``compute_naive`` gives the cheapest-hour policy's ``NaiveMetrics`` against
baseline (DECISIONS.md, "Cheapest hour").

``headroom_kw``, ``usd_saved`` and ``co2_avoided_t`` are returned unrounded and
unclamped, including zero or negative values (Requirement 15.7); the output
contract rounds them when it writes the Scenario_File.

Units: ``grid_kw`` is facility kW, so slot energy is ``grid_kw × SLOT_MINUTES /
60`` facility kWh. ``MoveRecord.kwh_by_slot`` is read as the facility-equivalent
kWh the moved job draws in the Alternate_Region in each slot, matching
``SlotResult.moved_kw``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sim.config import DEMAND_CHARGE_PER_KW, MOVE_PENALTY_USD, ROUND, SLOT_MINUTES, SLOTS_PER_HOUR
from sim.scheduler import (
    MoveRecord,
    PolicyResult,
    ScenarioInputs,
    naive_capped_missed,
    naive_starts,
    run_slots,
)
from sim.site import ZoomEntry

__all__ = [
    "METRIC_KEYS",
    "NAIVE_METRIC_KEYS",
    "DrInputs",
    "Metrics",
    "NaiveMetrics",
    "stress_indices",
    "slot_hours",
    "policy_cost_usd",
    "policy_emissions_g",
    "dr_delivered_raw_pct",
    "clamp_pct",
    "dr_seconds_to_full",
    "compute",
    "site_peak_kw",
    "compute_naive",
]

# Key order of the output contract's ``metrics`` object (Requirement 16.10).
METRIC_KEYS: tuple[str, ...] = (
    "headroom_kw",
    "usd_saved",
    "co2_avoided_t",
    "deadlines_met_pct",
    "inference_sla_pct",
    "dr_delivered_pct",
    "dr_seconds_to_full",
    "baseline_site_peak_kw",
    "headroom_site_peak_kw",
)

# Key order of the output contract's ``naive_metrics`` object.
NAIVE_METRIC_KEYS: tuple[str, ...] = (
    "site_peak_kw",
    "stress_peak_kw",
    "usd_saved",
    "co2_avoided_t",
    "deadlines_met_pct",
    "peak_hour_starts",
    "peak_jobs_running",
    "baseline_jobs_running",
    "capped_missed",
    "capped_total",
    "cap_kw",
)

_MIN_PER_HOUR = 60
_G_PER_TONNE = 1_000_000
_PCT = 100.0


class DrInputs(Protocol):
    """The DR_Event fields the metrics read (a subset of the design's ``DrResult``)."""

    requested_shed_kw: float
    required_kw: float
    zoom: Sequence[ZoomEntry]


@dataclass(frozen=True)
class Metrics:
    headroom_kw: float
    usd_saved: float
    co2_avoided_t: float
    deadlines_met_pct: float
    inference_sla_pct: float
    dr_delivered_pct: float        # clamped to [0, 100], rounded to ROUND["pct"]
    dr_seconds_to_full: int
    dr_delivered_raw_pct: float    # unclamped, unrounded; goes to meta.params
    baseline_site_peak_kw: float
    headroom_site_peak_kw: float

    def to_dict(self) -> dict[str, float]:
        """The contract ``metrics`` keys in contract order."""
        return {k: getattr(self, k) for k in METRIC_KEYS}


@dataclass(frozen=True)
class NaiveMetrics:
    """The cheapest-hour policy against baseline (contract ``naive_metrics``)."""

    site_peak_kw: float            # highest grid draw over the window
    stress_peak_kw: float          # highest grid draw over the Stress_Slots
    usd_saved: float               # cost(base) − cost(naive); negative when it costs more
    co2_avoided_t: float           # no moves, so grid energy only
    deadlines_met_pct: float
    peak_hour_starts: int          # jobs starting in the clock hour of the naive peak
    peak_jobs_running: int         # jobs running in the naive peak slot
    baseline_jobs_running: int     # jobs running in that same slot under baseline
    capped_missed: int             # held to cap_kw: jobs that miss their deadline
    capped_total: int
    cap_kw: float                  # today's connection: the baseline window peak

    def to_dict(self) -> dict[str, float]:
        return {k: getattr(self, k) for k in NAIVE_METRIC_KEYS}


# --- Building blocks (also used as reference points by tests) ---------------

def slot_hours() -> float:
    """Length of one Slot in hours."""
    return SLOT_MINUTES / _MIN_PER_HOUR


def _check_slots(inp: ScenarioInputs, res: PolicyResult) -> None:
    if len(res.slots) != inp.n_slots:
        raise ValueError(
            f"{inp.name}: {res.policy} has {len(res.slots)} slots, expected {inp.n_slots}"
        )


def stress_indices(inp: ScenarioInputs) -> list[int]:
    """Indices of the Stress_Slots; raises if the scenario has none."""
    idx = [i for i, g in enumerate(inp.slots) if g.stress]
    if not idx:
        raise ValueError(f"{inp.name}: no stress slots")
    return idx


def _moves_for_metrics(inp: ScenarioInputs, moves: Sequence[MoveRecord]) -> list[tuple[int, str, float]]:
    out: list[tuple[int, str, float]] = []
    for m in moves:
        for s, kwh in m.kwh_by_slot.items():
            if not 0 <= s < inp.n_slots:
                raise ValueError(f"{inp.name}: move of job {m.job_id} has energy in slot {s}, "
                                 f"outside [0, {inp.n_slots})")
            out.append((s, m.region, kwh))
    return out


def policy_cost_usd(inp: ScenarioInputs, res: PolicyResult, *, include_moves: bool) -> float:
    """Energy cost + demand charge on the window peak (+ $ per successful move).

    Requirements 10.16, 10.17, 15.2. Only the Headroom policy's cost includes
    the transfer penalty, so callers pass ``include_moves`` explicitly.
    """
    _check_slots(inp, res)
    h = slot_hours()
    energy = sum(r.grid_kw * h * g.price_per_kwh for r, g in zip(res.slots, inp.slots))
    demand = max(r.grid_kw for r in res.slots) * DEMAND_CHARGE_PER_KW if res.slots else 0.0
    moves = MOVE_PENALTY_USD * len(res.moves) if include_moves else 0.0
    return energy + demand + moves


def policy_emissions_g(inp: ScenarioInputs, res: PolicyResult, *, include_moves: bool) -> float:
    """Grams CO₂eq: grid energy × PACE carbon (+ moved energy × target-region carbon).

    Requirements 12.6, 15.3. Each slot's carbon is the value for the hour
    containing it, as carried on ``GridSlot``.
    """
    _check_slots(inp, res)
    h = slot_hours()
    grams = sum(r.grid_kw * h * g.carbon_g_per_kwh for r, g in zip(res.slots, inp.slots))
    if include_moves:
        grams += sum(kwh * inp.slots[s].alt_carbon[region]
                     for s, region, kwh in _moves_for_metrics(inp, res.moves))
    return grams


def site_peak_kw(res: PolicyResult) -> float:
    """Highest grid draw over the whole window."""
    if not res.slots:
        raise ValueError(f"{res.policy}: no slots")
    return max(r.grid_kw for r in res.slots)


def _deadlines_met_pct(inp: ScenarioInputs, res: PolicyResult) -> float:
    if not inp.jobs:
        return _PCT
    met = sum(1 for j in inp.jobs if (o := res.outcomes.get(j.id)) is not None and o.met_deadline)
    return _PCT * met / len(inp.jobs)


def dr_delivered_raw_pct(inp: ScenarioInputs, base: PolicyResult, head: PolicyResult,
                         requested_shed_kw: float) -> float:
    """Unclamped, unrounded DR delivered percentage (Requirement 14.9)."""
    _check_slots(inp, base)
    _check_slots(inp, head)
    if inp.dr_slots <= 0:
        raise ValueError(f"{inp.name}: no DR slots")
    if requested_shed_kw <= 0:
        raise ValueError(f"{inp.name}: requested shed must be positive: {requested_shed_kw}")
    head_max = max(head.slots[i].grid_kw for i in inp.dr_range)
    return _PCT * (base.slots[inp.dr_start].grid_kw - head_max) / requested_shed_kw


def clamp_pct(value: float) -> float:
    """Clamp to [0, 100] and round to ``ROUND["pct"]`` places (DECISIONS.md)."""
    return round(min(_PCT, max(0.0, value)), ROUND["pct"])


def dr_seconds_to_full(zoom: Sequence[ZoomEntry], required_kw: float) -> int:
    """Smallest ``s`` with flywheel 0 kW and battery at ``required_kw`` (Requirement 14.10).

    Values are compared at the output precision (``ROUND["kw"]``), the
    precision the Event_Zoom is built in.
    """
    nd = ROUND["kw"]
    target = round(required_kw, nd)
    for e in zoom:
        if round(e.flywheel_kw, nd) == 0 and round(e.battery_kw, nd) == target:
            return e.s
    raise ValueError(f"event zoom never reaches full battery output of {required_kw} kW")


# --- Entry point ---------------------------------------------------------------

def compute(inp: ScenarioInputs, base: PolicyResult, head: PolicyResult, dr: DrInputs) -> Metrics:
    """All headline KPIs for one Scenario (Requirements 14.9, 14.10, 15.1–15.5, 15.7)."""
    _check_slots(inp, base)
    _check_slots(inp, head)
    n = inp.n_slots
    if n == 0:
        raise ValueError(f"{inp.name}: no slots")

    stress = stress_indices(inp)
    headroom_kw = (max(base.slots[i].grid_kw for i in stress)
                   - max(head.slots[i].grid_kw for i in stress))

    usd_saved = (policy_cost_usd(inp, base, include_moves=False)
                 - policy_cost_usd(inp, head, include_moves=True))

    co2_avoided_t = (policy_emissions_g(inp, base, include_moves=False)
                     - policy_emissions_g(inp, head, include_moves=True)) / _G_PER_TONNE

    deadlines_met_pct = _deadlines_met_pct(inp, head)

    inference = inp.inference_fac
    served = sum(1 for r, want in zip(head.slots, inference) if r.inference_kw == want)
    inference_sla_pct = _PCT * served / n

    raw = dr_delivered_raw_pct(inp, base, head, dr.requested_shed_kw)

    return Metrics(
        headroom_kw=headroom_kw,
        usd_saved=usd_saved,
        co2_avoided_t=co2_avoided_t,
        deadlines_met_pct=deadlines_met_pct,
        inference_sla_pct=inference_sla_pct,
        dr_delivered_pct=clamp_pct(raw),
        dr_seconds_to_full=dr_seconds_to_full(dr.zoom, dr.required_kw),
        dr_delivered_raw_pct=raw,
        baseline_site_peak_kw=site_peak_kw(base),
        headroom_site_peak_kw=site_peak_kw(head),
    )


def compute_naive(inp: ScenarioInputs, base: PolicyResult, naive: PolicyResult) -> NaiveMetrics:
    """``NaiveMetrics`` for ``naive = run_naive(inp)`` against ``base = run_baseline(inp)``.

    ``cap_kw`` is the baseline window peak, the connection the site has today.
    ``peak_hour_starts`` counts jobs whose start slot falls in the clock hour
    (UTC hour, which is also a Denver hour) of the first slot at the naive peak;
    ``peak_jobs_running`` and ``baseline_jobs_running`` count the jobs with work
    in that slot under each policy (owner request, DECISIONS.md).
    """
    _check_slots(inp, base)
    _check_slots(inp, naive)
    grid = [r.grid_kw for r in naive.slots]
    peak = max(grid)
    p = grid.index(peak)
    hour = p // SLOTS_PER_HOUR
    starts = naive_starts(inp)

    def running(start: dict[str, int]) -> int:
        return sum(1 for j in inp.jobs
                   if any(k == p for k, _ in run_slots(start[j.id], j.duration_min, inp.n_slots)))

    cap = site_peak_kw(base)
    missed, total = naive_capped_missed(inp, cap)
    return NaiveMetrics(
        site_peak_kw=peak,
        stress_peak_kw=max(grid[i] for i in stress_indices(inp)),
        usd_saved=(policy_cost_usd(inp, base, include_moves=False)
                   - policy_cost_usd(inp, naive, include_moves=False)),
        co2_avoided_t=(policy_emissions_g(inp, base, include_moves=False)
                       - policy_emissions_g(inp, naive, include_moves=False)) / _G_PER_TONNE,
        deadlines_met_pct=_deadlines_met_pct(inp, naive),
        peak_hour_starts=sum(1 for s in starts.values() if s // SLOTS_PER_HOUR == hour),
        peak_jobs_running=running(starts),
        baseline_jobs_running=running({j.id: j.arrival_slot for j in inp.jobs}),
        capped_missed=missed,
        capped_total=total,
        cap_kw=cap,
    )
