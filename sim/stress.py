"""Stress test: how much of Headroom's result survives what the planner can't know.

``python -m sim.stress`` writes ``web/public/scenarios/stress.json``. It's a separate
file so the published scenarios keep their own meaning: those runs give the planner
actual carbon for its whole look-ahead and every job's exact run time, as the spec
allows. Here the planner gets a real forecast instead (``sim/forecast.py``), then jobs
run long, the battery fails and other regions stop taking jobs (``PlanningGap``).

**What's real:** the grid data and the forecast errors. **What's assumed:** the size of
each simulated shock (``STRESS_*`` in ``sim/config.py``), because nothing in this repo
measures a real site. So the spread across draws shows sensitivity to those
assumptions. It is not the probability of any outcome, and the page says so.

Each draw compares Headroom with baseline in the same world, so both see the same true
run times. The demand-response cap stays at its value from the undisturbed baseline:
the utility's request doesn't change because a job ran long.

The replay runs every 48-hour window from June to September, not just the two chosen
ones. Each window bills a full month's demand charge on its own 48-hour peak, as the
scenarios do, so window savings can be compared but must not be added up.

Read savings next to the other columns. A missed demand-response cut costs nothing on
this bill, so a battery that skips the event can show higher savings. Longer runs raise
baseline's bill too, so savings can rise while the bill does. ``python -m sim.stress
--check`` rebuilds and compares against the committed file; CI runs it.
"""

from __future__ import annotations

import dataclasses
import json
import math
import os
import random
import sys
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sim import config
from sim.config import (
    ALT_REGIONS,
    DATA_END,
    DATA_START,
    HOME_REGION,
    JOB_DURATION_STEP_MIN,
    REPLAY_HISTORY_DAYS,
    REPLAY_START_HOUR_UTC,
    ROUND,
    SLOT_MINUTES,
    SLOTS_PER_HOUR,
    STRESS_OUTAGE_HOURS,
    STRESS_RUNTIME_MEDIAN,
    STRESS_RUNTIME_SIGMA,
    STRESS_SAMPLES,
    WINDOW_HOURS,
)
from sim.forecast import (
    CarbonView,
    _relative_errors,
    carbon_accuracy,
    demand_accuracy,
    demand_band,
    error_band,
    forecast_errors,
    quantile,
)
from sim.jobs import rng_for
from sim.metrics import policy_cost_usd, policy_emissions_g, site_peak_kw, stress_indices
from sim.run_scenarios import (
    SCENARIOS_DIR,
    GridData,
    _utc_now,
    _window_start,
    _write_all,
    enabled_scenarios,
    grid_import_cap_kw,
    load_grid_data,
    scenario_inputs,
)
from sim.scheduler import PlanningGap, PolicyResult, ScenarioInputs, run_baseline, run_headroom
from sim.timeutil import iso_z

__all__ = [
    "STRESS_INTERFACE_KEYS",
    "FILE_NAME",
    "Outcome",
    "World",
    "make_world",
    "run_world",
    "runtime_draw",
    "outage_slots",
    "family_gap",
    "scenario_stress",
    "build_replay",
    "build",
    "validate",
    "dumps",
    "main",
]

FILE_NAME = "stress.json"
_G_PER_T = 1_000_000
_ACCURACY_LEADS = (1, 3, 6, 12, 24)
_RANGE_Q = (0.10, 0.50, 0.90)
_STABLE_DIGITS = 6  # Noise from BLAS and float sums stays below this (DECISIONS.md, 14:41 MT).

# The key sets the file must have, by the interface name in web/src/stressTypes.ts.
STRESS_INTERFACE_KEYS: Mapping[str, tuple[str, ...]] = {
    "StressFile": ("meta", "carbon_accuracy", "demand_accuracy", "scenarios", "replay"),
    "StressMeta": ("built_at", "seed", "samples", "runtime_median", "runtime_sigma",
                   "outage_hours_min", "outage_hours_max", "data_start", "data_end"),
    "CarbonAccuracyRow": ("region", "lead_hours", "forecasts", "mae_g_per_kwh",
                          "naive_mae_g_per_kwh", "band_coverage_pct"),
    "DemandAccuracyRow": ("hours", "mape_pct", "bias_pct", "band_coverage_pct"),
    "ScenarioStress": ("families", "carbon_band", "demand_band"),
    "StressFamily": ("id", "label", "samples", "usd_saved", "demand_charge_saved_usd", "bill_usd",
                     "baseline_bill_usd", "co2_avoided_t", "headroom_kw", "site_peak_kw",
                     "negative_runs", "deadlines_missed_p50", "deadlines_missed_max",
                     "baseline_deadlines_missed_max", "dr_missed_runs", "dr_shortfall_max_kw"),
    "StressRange": ("p10", "p50", "p90", "min", "max"),
    "BandPoint": ("t", "actual", "forecast", "low", "high"),
    "ReplaySummary": ("windows", "foresight_usd", "forecast_usd", "foresight_negative",
                      "forecast_negative", "change_median_usd", "change_worst_usd", "change_best_usd",
                      "windows_lower", "windows_higher", "foresight_dr_missed", "forecast_dr_missed",
                      "late_event_windows", "forecast_dr_missed_late"),
    "ReplayWindow": ("start", "foresight_usd", "forecast_usd", "forecast_co2_t",
                     "forecast_deadlines_missed", "dr_hour", "foresight_dr_shortfall_kw",
                     "forecast_dr_shortfall_kw"),
}
FAMILY_IDS = ("published", "forecast", "runtime", "battery", "battery_dr", "regions", "everything")
FAMILY_LABELS = {
    "published": "As published: planner sees actual carbon and exact run times",
    "forecast": "Real carbon forecast",
    "runtime": "Forecast, and jobs run longer or shorter than estimated",
    "battery": "Forecast, and the battery fails at a random time",
    "battery_dr": "Forecast, and the battery fails during the demand-response event",
    "regions": "Forecast, and other regions stop taking jobs at a random time",
    "everything": "All of it at once",
}
_SINGLE_RUN = frozenset({"published", "forecast", "battery_dr"})
_LATE_EVENT_HOUR = WINDOW_HOURS - 8     # an event starting here or later is in the window's last 8 hours
_SAME_USD = 100.0                       # a replay window within this much counts as unchanged
# The scenario files' own precision, so a figure both files hold reads the same in every panel.
_USD, _KW, _T = ROUND["usd"], ROUND["kw"], ROUND["t"]


@dataclass(frozen=True)
class Outcome:
    """One run of Headroom against baseline in the same world."""

    usd_saved: float
    demand_charge_saved_usd: float
    bill_usd: float
    baseline_bill_usd: float
    co2_avoided_t: float
    headroom_kw: float
    site_peak_kw: float
    deadlines_missed: int
    baseline_deadlines_missed: int
    dr_shortfall_kw: float     # most the grid draw went over the cap during the event


@dataclass(frozen=True)
class World:
    """One window, with the cap fixed from its undisturbed baseline."""

    inp: ScenarioInputs
    view: CarbonView


def _hour_index(data: GridData, t: datetime) -> int:
    return (t - data.carbon[HOME_REGION].start) // timedelta(hours=1)


def make_world(name: str, window_start: datetime, data: GridData, seed: int) -> World:
    inp, _ = scenario_inputs(name, window_start, data, seed)
    base = run_baseline(inp)
    inp = dataclasses.replace(inp, cap_kw=grid_import_cap_kw(base, inp.dr_start, inp.requested_shed_kw))
    carbon = {r: data.carbon[r].values for r in (HOME_REGION, *ALT_REGIONS)}
    return World(inp, CarbonView(inp.slots, carbon, _hour_index(data, window_start)))


def _missed(res: PolicyResult) -> int:
    return sum(1 for o in res.outcomes.values() if not o.met_deadline)


def _outcome(inp: ScenarioInputs, base: PolicyResult, head: PolicyResult) -> Outcome:
    assert inp.cap_kw is not None
    stress = stress_indices(inp)
    bill = policy_cost_usd(inp, head, include_moves=True)
    baseline_bill = policy_cost_usd(inp, base, include_moves=False)
    return Outcome(
        usd_saved=baseline_bill - bill,
        demand_charge_saved_usd=config.DEMAND_CHARGE_PER_KW * (site_peak_kw(base) - site_peak_kw(head)),
        bill_usd=bill,
        baseline_bill_usd=baseline_bill,
        co2_avoided_t=(policy_emissions_g(inp, base, include_moves=False)
                       - policy_emissions_g(inp, head, include_moves=True)) / _G_PER_T,
        headroom_kw=(max(base.slots[i].grid_kw for i in stress)
                     - max(head.slots[i].grid_kw for i in stress)),
        site_peak_kw=site_peak_kw(head),
        deadlines_missed=_missed(head),
        baseline_deadlines_missed=_missed(base),
        dr_shortfall_kw=max(0.0, max(head.slots[i].grid_kw for i in inp.dr_range) - inp.cap_kw),
    )


def run_world(world: World, gap: PlanningGap) -> Outcome:
    """Headroom against baseline, both with ``gap``. A missed cut is a result, not an error."""
    gap = dataclasses.replace(gap, soft_dr=True)
    inp = world.inp
    return _outcome(inp, run_baseline(inp, gap=gap),
                    run_headroom(inp, gap=gap, move_enabled=config.ENABLE_MOVE_REGION))


# --- The draws --------------------------------------------------------------

def runtime_draw(inp: ScenarioInputs, rng: random.Random) -> dict[str, int]:
    """True run time minus the estimate for every job, on the jobs' 5-minute grid."""
    out: dict[str, int] = {}
    for j in inp.jobs:
        ratio = rng.lognormvariate(math.log(STRESS_RUNTIME_MEDIAN), STRESS_RUNTIME_SIGMA)
        true_min = round(j.duration_min * ratio / JOB_DURATION_STEP_MIN) * JOB_DURATION_STEP_MIN
        out[j.id] = max(SLOT_MINUTES, true_min) - j.duration_min
    return out


def outage_slots(n_slots: int, rng: random.Random) -> frozenset[int]:
    """One unannounced outage: a random length in ``STRESS_OUTAGE_HOURS``, at a random time."""
    low, high = STRESS_OUTAGE_HOURS
    length = rng.randint(low, high) * SLOTS_PER_HOUR
    start = rng.randrange(0, n_slots - length + 1)
    return frozenset(range(start, start + length))


def family_gap(family: str, world: World, seed: int, draw: int) -> PlanningGap:
    """One run of one family. Each family and draw has its own random stream."""
    inp, view = world.inp, world.view
    n = inp.n_slots
    if family == "published":
        return PlanningGap()
    if family == "forecast":
        return PlanningGap(carbon_view=view)
    if family == "battery_dr":
        return PlanningGap(carbon_view=view, battery_out_slots=frozenset(inp.dr_range))
    rng = rng_for(seed, inp.name, f"stress:{family}:{draw}")
    if family == "runtime":
        return PlanningGap(carbon_view=view, hidden_run_min=runtime_draw(inp, rng))
    if family == "battery":
        return PlanningGap(carbon_view=view, battery_out_slots=outage_slots(n, rng))
    if family == "regions":
        return PlanningGap(carbon_view=view, move_blocked_slots=outage_slots(n, rng))
    if family == "everything":
        return PlanningGap(carbon_view=view, hidden_run_min=runtime_draw(inp, rng),
                           battery_out_slots=outage_slots(n, rng), move_blocked_slots=outage_slots(n, rng))
    raise ValueError(f"unknown family: {family}")


# --- Running many draws -------------------------------------------------------
#
# A run takes about 0.4 s and a build makes over 900, so they go to one process per
# CPU. Each worker keeps its own grid data and worlds. Results come back in task order,
# so the file doesn't depend on the number of workers.

_worker_data: GridData | None = None
_worker_worlds: dict[tuple[str, datetime, int], World] = {}


def _world_for(name: str, start: datetime, seed: int, data: GridData | None) -> World:
    global _worker_data
    key = (name, start, seed)
    if key not in _worker_worlds:
        if data is None:
            if _worker_data is None:
                _worker_data = load_grid_data()
            data = _worker_data
        _worker_worlds[key] = make_world(name, start, data, seed)
    return _worker_worlds[key]


@dataclass(frozen=True)
class _Task:
    name: str
    start: datetime
    seed: int
    family: str
    draw: int


def _run_task(task: _Task, data: GridData | None = None) -> Outcome:
    world = _world_for(task.name, task.start, task.seed, data)
    return run_world(world, family_gap(task.family, world, task.seed, task.draw))


def _run_all(tasks: Sequence[_Task], data: GridData, workers: int | None) -> list[Outcome]:
    """Run ``tasks`` in order. ``workers=1`` stays in this process, which tests use."""
    workers = (os.cpu_count() or 1) if workers is None else workers
    if workers <= 1:
        return [_run_task(t, data) for t in tasks]
    with ProcessPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_run_task, tasks, chunksize=8))


# --- Summaries ----------------------------------------------------------------

def _stable(value: float, digits: int) -> float:
    return round(round(value, _STABLE_DIGITS), digits)


def _range(values: Sequence[float], digits: int) -> dict[str, float]:
    low, mid, high = (_stable(quantile(values, q), digits) for q in _RANGE_Q)
    return {"p10": low, "p50": mid, "p90": high,
            "min": _stable(min(values), digits), "max": _stable(max(values), digits)}


def _family_row(family: str, runs: Sequence[Outcome]) -> dict[str, Any]:
    missed = [r.deadlines_missed for r in runs]
    return {
        "id": family,
        "label": FAMILY_LABELS[family],
        "samples": len(runs),
        "usd_saved": _range([r.usd_saved for r in runs], _USD),
        "demand_charge_saved_usd": _range([r.demand_charge_saved_usd for r in runs], _USD),
        "bill_usd": _range([r.bill_usd for r in runs], _USD),
        "baseline_bill_usd": _range([r.baseline_bill_usd for r in runs], _USD),
        "co2_avoided_t": _range([r.co2_avoided_t for r in runs], _T),
        "headroom_kw": _range([r.headroom_kw for r in runs], _KW),
        "site_peak_kw": _range([r.site_peak_kw for r in runs], _KW),
        "negative_runs": sum(1 for r in runs if _stable(r.usd_saved, _USD) < 0),
        "deadlines_missed_p50": quantile(missed, 0.5),
        "deadlines_missed_max": max(missed),
        "baseline_deadlines_missed_max": max(r.baseline_deadlines_missed for r in runs),
        "dr_missed_runs": sum(1 for r in runs if _stable(r.dr_shortfall_kw, _KW) > 0),
        "dr_shortfall_max_kw": _stable(max(r.dr_shortfall_kw for r in runs), _KW),
    }


def _band_points(times: Sequence[datetime], actual: Sequence[float], forecast: Sequence[float],
                 bands: Sequence[tuple[float, float] | None]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for t, a, f, b in zip(times, actual, forecast, bands):
        points.append({
            "t": iso_z(t), "actual": _stable(a, ROUND["carbon"]), "forecast": _stable(f, ROUND["carbon"]),
            "low": None if b is None else _stable(max(0.0, b[0]), ROUND["carbon"]),
            "high": None if b is None else _stable(b[1], ROUND["carbon"]),
        })
    return points


def _carbon_band(world: World, y: Sequence[float], errors: Any) -> list[dict[str, Any]]:
    """The forecast as issued in the window's first hour, with its band, against what happened."""
    i = world.view.first_hour
    believed = world.view(0)[HOME_REGION]
    start = world.inp.slots[0].t
    times, actual, forecast, bands = [], [], [], []
    for lead in range(1, world.inp.n_slots // SLOTS_PER_HOUR):
        f = believed[lead * SLOTS_PER_HOUR]
        band = error_band(errors, i, lead)
        times.append(start + timedelta(hours=lead))
        actual.append(y[i + lead])
        forecast.append(f)
        bands.append(None if band is None else (f + band[0], f + band[1]))
    return _band_points(times, actual, forecast, bands)


def _demand_band_points(world: World, data: GridData) -> list[dict[str, Any]]:
    """EIA's day-ahead forecast over the window, with a band from errors known before it."""
    hourly = data.hourly
    actual = hourly[f"{HOME_REGION}.D"].values
    df = hourly[f"{HOME_REGION}.DF"].values
    errors = _relative_errors(actual, df)
    band = demand_band(errors, world.view.first_hour)
    slots = world.inp.slots[::SLOTS_PER_HOUR]
    return _band_points(
        [g.t for g in slots], [g.pace_demand_mw for g in slots], [g.pace_forecast_mw for g in slots],
        [None if band is None else (g.pace_forecast_mw * (1 + band[0]), g.pace_forecast_mw * (1 + band[1]))
         for g in slots],
    )


def _replay_starts(data: GridData) -> list[datetime]:
    """Back-to-back windows from one week in to the end of the data."""
    series = data.carbon[HOME_REGION]
    first = REPLAY_HISTORY_DAYS * 24 + REPLAY_START_HOUR_UTC
    return [series.start + timedelta(hours=h)
            for h in range(first, len(series) - WINDOW_HOURS + 1, WINDOW_HOURS)]


def build_replay(data: GridData, seed: int, *, workers: int | None = None,
                 starts: Iterable[datetime] | None = None) -> dict[str, Any]:
    """Every window, with foresight and with the forecast. Each window draws its own jobs."""
    starts = _replay_starts(data) if starts is None else list(starts)
    tasks = [_Task(f"replay-{i}", start, seed, family, 0)
             for i, start in enumerate(starts) for family in ("published", "forecast")]
    runs = _run_all(tasks, data, workers)
    windows: list[dict[str, Any]] = []
    for i, start in enumerate(starts):
        known, guessed = runs[2 * i], runs[2 * i + 1]
        inp, _ = scenario_inputs(f"replay-{i}", start, data, seed)
        windows.append({
            "start": iso_z(start),
            "foresight_usd": _stable(known.usd_saved, _USD),
            "forecast_usd": _stable(guessed.usd_saved, _USD),
            "forecast_co2_t": _stable(guessed.co2_avoided_t, _T),
            "forecast_deadlines_missed": guessed.deadlines_missed,
            "dr_hour": inp.dr_start // SLOTS_PER_HOUR,
            "foresight_dr_shortfall_kw": _stable(known.dr_shortfall_kw, _KW),
            "forecast_dr_shortfall_kw": _stable(guessed.dr_shortfall_kw, _KW),
        })
    changes = [_stable(w["forecast_usd"] - w["foresight_usd"], _USD) for w in windows]
    late = [w for w in windows if w["dr_hour"] >= _LATE_EVENT_HOUR]
    return {
        "windows": windows,
        "foresight_usd": _range([r.usd_saved for r in runs[::2]], _USD),
        "forecast_usd": _range([r.usd_saved for r in runs[1::2]], _USD),
        "foresight_negative": sum(1 for w in windows if w["foresight_usd"] < 0),
        "forecast_negative": sum(1 for w in windows if w["forecast_usd"] < 0),
        "change_median_usd": quantile(changes, 0.5),
        "change_worst_usd": min(changes),
        "change_best_usd": max(changes),
        "windows_lower": sum(1 for c in changes if c < -_SAME_USD),
        "windows_higher": sum(1 for c in changes if c > _SAME_USD),
        "foresight_dr_missed": sum(1 for w in windows if w["foresight_dr_shortfall_kw"] > 0),
        "forecast_dr_missed": sum(1 for w in windows if w["forecast_dr_shortfall_kw"] > 0),
        "late_event_windows": len(late),
        "forecast_dr_missed_late": sum(1 for w in late if w["forecast_dr_shortfall_kw"] > 0),
    }


def scenario_stress(name: str, data: GridData, seed: int, samples: int, *,
                    workers: int | None = None) -> dict[str, Any]:
    """Every family for one published scenario, plus its forecast bands."""
    start = _window_start(name, data.hourly)
    tasks = [_Task(name, start, seed, f, draw) for f in FAMILY_IDS
             for draw in range(1 if f in _SINGLE_RUN else samples)]
    runs = _run_all(tasks, data, workers)
    families = [_family_row(f, [r for t, r in zip(tasks, runs) if t.family == f]) for f in FAMILY_IDS]
    world = _world_for(name, start, seed, data)
    return {
        "families": families,
        "carbon_band": _carbon_band(world, data.carbon[HOME_REGION].values,
                                    forecast_errors(data.carbon[HOME_REGION].values)),
        "demand_band": _demand_band_points(world, data),
    }


def build(data: GridData | None = None, *, seed: int | None = None, samples: int = STRESS_SAMPLES,
          built_at: str | None = None, workers: int | None = None) -> dict[str, Any]:
    """The whole file, validated."""
    data = load_grid_data() if data is None else data
    seed = config.SEED if seed is None else seed
    built_at = _utc_now() if built_at is None else built_at

    accuracy: list[dict[str, Any]] = []
    for region in (HOME_REGION, *ALT_REGIONS):
        for row in carbon_accuracy(data.carbon[region].values, region, _ACCURACY_LEADS):
            accuracy.append({**dataclasses.asdict(row),
                             "mae_g_per_kwh": _stable(row.mae_g_per_kwh, ROUND["carbon"]),
                             "naive_mae_g_per_kwh": _stable(row.naive_mae_g_per_kwh, ROUND["carbon"]),
                             "band_coverage_pct": _stable(row.band_coverage_pct, ROUND["pct"])})
    demand = demand_accuracy(data.hourly[f"{HOME_REGION}.D"].values, data.hourly[f"{HOME_REGION}.DF"].values)

    doc = {
        "meta": {
            "built_at": built_at, "seed": seed, "samples": samples,
            "runtime_median": STRESS_RUNTIME_MEDIAN, "runtime_sigma": STRESS_RUNTIME_SIGMA,
            "outage_hours_min": STRESS_OUTAGE_HOURS[0], "outage_hours_max": STRESS_OUTAGE_HOURS[1],
            "data_start": DATA_START, "data_end": DATA_END,
        },
        "carbon_accuracy": accuracy,
        "demand_accuracy": {
            "hours": demand.hours,
            "mape_pct": _stable(demand.mape_pct, ROUND["pct"]),
            "bias_pct": _stable(demand.bias_pct, ROUND["pct"]),
            "band_coverage_pct": _stable(demand.band_coverage_pct, ROUND["pct"]),
        },
        "scenarios": {name: scenario_stress(name, data, seed, samples, workers=workers)
                      for name in enabled_scenarios()},
        "replay": build_replay(data, seed, workers=workers),
    }
    problems = validate(doc)
    if problems:
        raise ValueError("stress file is invalid: " + "; ".join(problems[:10]))
    return doc


# --- Checks and output --------------------------------------------------------

def _check_keys(obj: Any, interface: str, where: str, out: list[str]) -> None:
    want = STRESS_INTERFACE_KEYS[interface]
    if not isinstance(obj, Mapping):
        out.append(f"{where}: not an object")
        return
    for key in want:
        if key not in obj:
            out.append(f"{where}: missing key {key!r}")
    for key in obj:
        if key not in want:
            out.append(f"{where}: unexpected key {key!r}")


def _check_range(obj: Any, where: str, out: list[str]) -> None:
    _check_keys(obj, "StressRange", where, out)
    if isinstance(obj, Mapping) and all(k in obj for k in STRESS_INTERFACE_KEYS["StressRange"]):
        if not obj["min"] <= obj["p10"] <= obj["p50"] <= obj["p90"] <= obj["max"]:
            out.append(f"{where}: range out of order")


def validate(doc: Any) -> list[str]:
    """Why this file can't ship. An empty list means it passes."""
    out: list[str] = []
    _check_keys(doc, "StressFile", "file", out)
    if out:
        return out
    _check_keys(doc["meta"], "StressMeta", "meta", out)
    _check_keys(doc["demand_accuracy"], "DemandAccuracyRow", "demand_accuracy", out)
    for i, row in enumerate(doc["carbon_accuracy"]):
        _check_keys(row, "CarbonAccuracyRow", f"carbon_accuracy[{i}]", out)
    for name, scenario in doc["scenarios"].items():
        _check_keys(scenario, "ScenarioStress", f"scenarios.{name}", out)
        if not isinstance(scenario, Mapping) or "families" not in scenario:
            continue
        ids = [f.get("id") for f in scenario["families"]]
        if tuple(ids) != FAMILY_IDS:
            out.append(f"scenarios.{name}: families are {ids}, expected {list(FAMILY_IDS)}")
        for f in scenario["families"]:
            where = f"scenarios.{name}.{f.get('id')}"
            _check_keys(f, "StressFamily", where, out)
            for key in ("usd_saved", "demand_charge_saved_usd", "bill_usd", "baseline_bill_usd",
                        "co2_avoided_t", "headroom_kw", "site_peak_kw"):
                if key in f:
                    _check_range(f[key], f"{where}.{key}", out)
            if f.get("id") in _SINGLE_RUN and f.get("samples") != 1:
                out.append(f"{where}: expected one run, has {f.get('samples')}")
        for key in ("carbon_band", "demand_band"):
            points = scenario.get(key, [])
            if not points:
                out.append(f"scenarios.{name}.{key}: empty")
            for i, point in enumerate(points):
                _check_keys(point, "BandPoint", f"scenarios.{name}.{key}[{i}]", out)
    replay = doc["replay"]
    _check_keys(replay, "ReplaySummary", "replay", out)
    if isinstance(replay, Mapping) and "windows" in replay:
        if not replay["windows"]:
            out.append("replay: no windows")
        for i, window in enumerate(replay["windows"]):
            _check_keys(window, "ReplayWindow", f"replay.windows[{i}]", out)
        for key in ("foresight_usd", "forecast_usd"):
            if key in replay:
                _check_range(replay[key], f"replay.{key}", out)
    return out


def dumps(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, separators=(",", ":"), ensure_ascii=False) + "\n"


def _differences(a: Any, b: Any, where: str = "") -> list[str]:
    """Paths where two documents differ, for a readable --check failure."""
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        out: list[str] = []
        for key in sorted(set(a) | set(b)):
            out += _differences(a.get(key), b.get(key), f"{where}.{key}" if where else str(key))
        return out
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in _differences(x, y, f"{where}[{i}]")]
    return [] if a == b else [f"{where}: {a!r} != {b!r}"]


def main(argv: Sequence[str] | None = None, *, out_dir: Path | str | None = None) -> int:
    """Write the file, or with ``--check`` compare a fresh build with the committed one."""
    argv = sys.argv[1:] if argv is None else argv
    path = (Path(out_dir) if out_dir is not None else SCENARIOS_DIR) / FILE_NAME
    if "--check" in argv:
        committed = json.loads(path.read_text(encoding="utf-8"))
        fresh = build(built_at=committed["meta"]["built_at"])
        differences = _differences(committed, fresh)
        if differences:
            print(f"{path.name} differs from a fresh build; run `python -m sim.stress` and commit it:",
                  file=sys.stderr)
            for line in differences[:20]:
                print(f"  {line}", file=sys.stderr)
            return 1
        print(f"{path.name} matches a fresh build")
        return 0
    _write_all({path: dumps(build())})
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    # Run the copy imported as sim.stress, as sim.run_scenarios does, so pool workers can find it.
    from sim.stress import main as _main
    sys.exit(_main())
