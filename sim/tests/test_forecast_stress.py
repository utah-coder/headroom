"""Forecasts, the planner's belief/actual split, and the stress file.

The rule under test throughout: a forecast may read only what was known when it was
issued, and with no gap the planner behaves exactly as published.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import random
import re
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sim import config, stress
from sim.config import ALT_REGIONS, HOME_REGION, SLOT_MINUTES, SLOTS_PER_HOUR, STRESS_OUTAGE_HOURS
from sim.forecast import (
    CarbonView,
    carbon_accuracy,
    carbon_forecast,
    demand_accuracy,
    error_band,
    forecast_errors,
    quantile,
)
from sim.jobs import TrainingJob
from sim.run_scenarios import _window_start, build_scenario_full, load_grid_data
from sim.scheduler import (
    MOVE_REGION,
    JobState,
    PlanningGap,
    met_deadline,
    run_baseline,
    run_headroom,
)
from sim.site import DrInfeasibleError

ROOT = Path(__file__).resolve().parents[2]
STRESS_FILE = ROOT / "web" / "public" / "scenarios" / stress.FILE_NAME
TYPES_TS = ROOT / "web" / "src" / "stressTypes.ts"


@functools.cache
def _data():
    return load_grid_data()


@functools.cache
def _world(name: str = "peak"):
    return stress.make_world(name, _window_start(name, _data().hourly), _data(), config.SEED)


def _committed() -> dict:
    return json.loads(STRESS_FILE.read_text(encoding="utf-8"))


def _soft(**kwargs) -> PlanningGap:
    return PlanningGap(soft_dr=True, **kwargs)


# --- Forecast: nothing from the future ---------------------------------------

@given(
    history=st.lists(st.floats(0, 1000), min_size=30, max_size=60),
    future=st.lists(st.floats(0, 1000), min_size=5, max_size=5),
    other=st.lists(st.floats(0, 1000), min_size=5, max_size=5),
)
def test_forecast_reads_no_hour_after_it_was_issued(history, future, other):
    i = len(history) - 1
    assert carbon_forecast(history + future, i, 5) == carbon_forecast(history + other, i, 5)


def test_forecast_needs_history():
    with pytest.raises(ValueError, match="history"):
        carbon_forecast([1.0] * 10, 9, 3)


def test_a_flat_series_forecasts_flat():
    assert carbon_forecast([100.0] * 48, 47, 6) == pytest.approx([100.0] * 6)


def test_a_repeating_day_is_forecast_exactly():
    day = [100.0 + 10 * h for h in range(24)]
    series = day * 8
    i = 24 * 7 - 1
    assert carbon_forecast(series, i, 24) == pytest.approx(day)


def test_forecast_is_never_negative():
    y = [500.0] * 47 + [0.0]
    assert min(carbon_forecast(y, 47, 24)) >= 0.0


def test_band_uses_only_errors_already_resolved():
    y = [100.0 + (h % 24) + (h % 7) for h in range(200)]
    changed = y[:120] + [v + 500 for v in y[120:]]
    assert error_band(forecast_errors(y), 119, 3) == error_band(forecast_errors(changed), 119, 3)


def test_band_is_missing_without_enough_history():
    y = [100.0] * 60
    assert error_band(forecast_errors(y), 25, 3) is None


def test_quantile_is_nearest_rank():
    assert quantile([3.0, 1.0, 2.0, 4.0], 0.5) == 2.0
    assert quantile([3.0, 1.0, 2.0, 4.0], 0.9) == 4.0
    with pytest.raises(ValueError):
        quantile([], 0.5)


def test_real_forecast_beats_same_hour_yesterday_at_short_leads():
    rows = carbon_accuracy(_data().carbon[HOME_REGION].values, HOME_REGION, (1, 6))
    assert all(r.mae_g_per_kwh < r.naive_mae_g_per_kwh for r in rows)


def test_demand_accuracy_measures_bias_with_sign():
    high = demand_accuracy([100.0] * 60, [110.0] * 60)
    assert high.mape_pct == pytest.approx(10.0)
    assert high.bias_pct == pytest.approx(10.0)
    low = demand_accuracy([100.0] * 60, [90.0] * 60)
    assert low.bias_pct == pytest.approx(-10.0)


def test_demand_accuracy_skips_missing_hours():
    assert demand_accuracy([100.0, None, 100.0], [110.0, 110.0, None]).hours == 1


# --- CarbonView ---------------------------------------------------------------

def test_view_knows_the_current_hour_and_forecasts_the_rest():
    world = _world()
    view, slots = world.view, world.inp.slots
    s = 41                     # hour 10, slot 1 of that hour
    believed = view(s)[HOME_REGION]
    known = (s // SLOTS_PER_HOUR + 1) * SLOTS_PER_HOUR
    assert len(believed) == len(slots)
    assert believed[:known] == [g.carbon_g_per_kwh for g in slots[:known]]
    assert believed[known:] != [g.carbon_g_per_kwh for g in slots[known:]]


def test_view_covers_every_region():
    assert set(_world().view(0)) == {HOME_REGION, *ALT_REGIONS}


def test_view_ignores_changes_after_the_issue_hour():
    world = _world()
    start = world.view.first_hour
    hour = 10
    carbon = {r: list(world.view.carbon[r]) for r in world.view.carbon}
    changed = {r: v[:start + hour + 1] + [x + 500 for x in v[start + hour + 1:]] for r, v in carbon.items()}
    other = CarbonView(world.inp.slots, changed, start)
    s = hour * SLOTS_PER_HOUR
    assert other(s) == world.view(s)


# --- The planner's belief/actual split ------------------------------------------

@pytest.mark.parametrize("name", ["peak", "sun"])
def test_an_empty_gap_is_the_published_run(name):
    build = build_scenario_full(name, _data(), built_at="2000-01-01T00:00:00Z")
    same = run_headroom(build.inp, gap=PlanningGap(), move_enabled=config.ENABLE_MOVE_REGION)
    assert same.slots == build.head.slots
    assert same.decisions == build.head.decisions


def test_believing_actual_carbon_changes_no_route():
    world = _world()
    actual = world.view.actual
    believed = run_headroom(world.inp, gap=PlanningGap(carbon_view=lambda s: actual))
    published = run_headroom(world.inp)
    assert [(d.t, d.job, d.route) for d in believed.decisions] == [(d.t, d.job, d.route) for d in published.decisions]
    assert [r.grid_kw for r in believed.slots] == pytest.approx([r.grid_kw for r in published.slots])


def test_a_forecasting_planner_cannot_see_future_carbon():
    """Change actual carbon from slot k on: every route before k stays the same."""
    world = _world()
    inp = world.inp
    k = 100
    moved = tuple(
        dataclasses.replace(g, carbon_g_per_kwh=g.carbon_g_per_kwh + 700,
                            alt_carbon={r: v + 700 for r, v in g.alt_carbon.items()})
        if i >= k else g
        for i, g in enumerate(inp.slots)
    )
    gap = _soft(carbon_view=world.view)      # the same beliefs in both runs
    first = run_headroom(inp, gap=gap)
    second = run_headroom(dataclasses.replace(inp, slots=moved), gap=gap)
    routes = lambda res: [(d.t, d.job, d.route, d.target) for d in res.decisions if d.t < inp.slots[k].t]  # noqa: E731
    assert routes(first) == routes(second)


def test_published_planner_does_see_future_carbon():
    """The control for the test above: without a forecast, the same change does move routes."""
    inp = _world().inp
    k = 100
    moved = tuple(
        dataclasses.replace(g, carbon_g_per_kwh=g.carbon_g_per_kwh + 700,
                            alt_carbon={r: v + 700 for r, v in g.alt_carbon.items()})
        if i >= k else g
        for i, g in enumerate(inp.slots)
    )
    routes = lambda res: [(d.t, d.job, d.route, d.target) for d in res.decisions if d.t < inp.slots[k].t]  # noqa: E731
    assert routes(run_headroom(inp, gap=_soft())) != routes(
        run_headroom(dataclasses.replace(inp, slots=moved), gap=_soft()))


JOB = TrainingJob(id="1", arrival_slot=0, power_kw=100, duration_min=30, slack_min=600)


def _drain(js: JobState, limit: int = 40) -> list[int]:
    """Run a job until it finishes; returns the remaining time the planner saw after each slot."""
    seen = []
    for s in range(limit):
        if js.remaining_min == 0:
            break
        js.run_slot(s)
        seen.append(js.remaining_min)
    return seen


def test_a_job_that_runs_long_reveals_one_slot_at_a_time():
    js = JobState.new(JOB, 192, hidden_min=40)
    seen = _drain(js)
    assert seen[:2] == [15, SLOT_MINUTES]          # looks one slot from done, not 40 minutes away
    assert js.finish_min == 70
    assert js.delivered_kwh == pytest.approx(100 * 70 / 60)


def test_a_job_that_runs_short_finishes_early():
    js = JobState.new(JOB, 192, hidden_min=-10)
    _drain(js)
    assert js.finish_min == 20
    assert js.delivered_kwh == pytest.approx(100 * 20 / 60)


def test_an_exact_estimate_is_unchanged():
    js = JobState.new(JOB, 192)
    _drain(js)
    assert js.finish_min == 30


def test_carry_over_counts_time_the_planner_cant_see():
    job = TrainingJob(id="2", arrival_slot=0, power_kw=100, duration_min=30, slack_min=0)
    js = JobState.new(job, 1, hidden_min=60)          # a one-slot window, so it ends unfinished
    js.run_slot(0)
    assert js.finish_min is None
    assert not met_deadline(js, 1)


def test_baseline_bills_the_true_run_time():
    world = _world()
    inp = dataclasses.replace(world.inp, jobs=world.inp.jobs[:1])
    job = inp.jobs[0]
    energy = lambda res: sum(r.training_kw for r in res.slots)  # noqa: E731
    known = run_baseline(inp)
    longer = run_baseline(inp, gap=PlanningGap(hidden_run_min={job.id: 30}))
    assert energy(longer) > energy(known)
    assert longer.outcomes[job.id].delivered_kwh > known.outcomes[job.id].delivered_kwh


def test_every_job_still_completes_or_carries_over_when_runs_differ():
    world = _world()
    hidden = stress.runtime_draw(world.inp, random.Random(1))
    res = run_headroom(world.inp, gap=_soft(carbon_view=world.view, hidden_run_min=hidden))
    assert set(res.outcomes) == {j.id for j in world.inp.jobs}
    assert all(o.delivered_kwh > 0 for o in res.outcomes.values())


def test_a_failed_battery_does_nothing():
    world = _world()
    out = frozenset(range(20, 60))
    res = run_headroom(world.inp, gap=_soft(battery_out_slots=out))
    for s in out:
        assert res.slots[s].battery.discharge_kw == 0.0
        assert res.slots[s].battery.charge_kw == 0.0


def test_no_move_is_accepted_while_regions_are_blocked():
    world = _world()
    blocked = frozenset(range(world.inp.n_slots))
    res = run_headroom(world.inp, gap=_soft(move_blocked_slots=blocked))
    assert not res.moves
    assert not [d for d in res.decisions if d.route == MOVE_REGION]


def test_blocking_some_slots_only_stops_moves_decided_in_them():
    world = _world()
    blocked = frozenset(range(0, 50))
    res = run_headroom(world.inp, gap=_soft(move_blocked_slots=blocked))
    assert all(m.decided_slot not in blocked for m in res.moves)


def test_a_missed_cut_is_rejected_unless_soft():
    world = _world()
    out = frozenset(world.inp.dr_range)
    with pytest.raises(DrInfeasibleError):
        run_headroom(world.inp, gap=PlanningGap(battery_out_slots=out))
    res = run_headroom(world.inp, gap=_soft(battery_out_slots=out))
    assert max(res.slots[s].grid_kw for s in world.inp.dr_range) > world.inp.cap_kw


# --- Draws ----------------------------------------------------------------------

def test_runtime_draw_stays_on_the_grid_and_above_one_slot():
    world = _world()
    hidden = stress.runtime_draw(world.inp, random.Random(7))
    for j in world.inp.jobs:
        true_min = j.duration_min + hidden[j.id]
        assert true_min >= SLOT_MINUTES
        assert true_min % config.JOB_DURATION_STEP_MIN == 0


@given(seed=st.integers(0, 2**32 - 1))
def test_outage_fits_the_window_and_its_length_limits(seed):
    slots = stress.outage_slots(192, random.Random(seed))
    low, high = STRESS_OUTAGE_HOURS
    assert low * SLOTS_PER_HOUR <= len(slots) <= high * SLOTS_PER_HOUR
    assert min(slots) >= 0 and max(slots) < 192
    assert max(slots) - min(slots) + 1 == len(slots)


def test_draws_repeat_for_a_seed_and_differ_between_families_and_draws():
    world = _world()
    gap = lambda family, draw: stress.family_gap(family, world, config.SEED, draw)  # noqa: E731
    assert gap("battery", 0) == gap("battery", 0)
    assert gap("battery", 0).battery_out_slots != gap("battery", 1).battery_out_slots
    assert gap("battery", 0).battery_out_slots != gap("regions", 0).move_blocked_slots


def test_unknown_family_is_refused():
    with pytest.raises(ValueError, match="unknown family"):
        stress.family_gap("nope", _world(), config.SEED, 0)


def test_soft_dr_is_always_on_in_a_stress_run():
    world = _world()
    outcome = stress.run_world(world, PlanningGap(battery_out_slots=frozenset(world.inp.dr_range)))
    assert outcome.dr_shortfall_kw > 0


# --- The committed file -----------------------------------------------------------

def test_committed_file_is_valid():
    assert stress.validate(_committed()) == []


@pytest.mark.parametrize("name", ["peak", "sun"])
def test_published_row_equals_the_published_scenario(name):
    published = json.loads((ROOT / "web" / "public" / "scenarios" / f"{name}.json").read_text(encoding="utf-8"))
    row = _committed()["scenarios"][name]["families"][0]
    assert row["id"] == "published"
    m = published["metrics"]
    assert row["usd_saved"]["p50"] == m["usd_saved"]    # the same figure, so both panels show the same text
    assert row["headroom_kw"]["p50"] == m["headroom_kw"]
    assert row["site_peak_kw"]["p50"] == m["headroom_site_peak_kw"]
    assert row["co2_avoided_t"]["p50"] == m["co2_avoided_t"]
    assert row["dr_missed_runs"] == 0


@pytest.mark.parametrize("name", ["peak", "sun"])
@pytest.mark.parametrize("family", ["published", "forecast", "battery_dr"])
def test_committed_single_runs_match_a_fresh_run(name, family):
    """A quick staleness check. CI's `python -m sim.stress --check` compares the whole file."""
    world = _world(name)
    fresh = stress._family_row(family, [stress.run_world(world, stress.family_gap(family, world, config.SEED, 0))])
    committed = next(f for f in _committed()["scenarios"][name]["families"] if f["id"] == family)
    assert committed == fresh


def test_committed_replay_windows_match_a_fresh_run():
    starts = stress._replay_starts(_data())[:2]
    fresh = stress.build_replay(_data(), config.SEED, workers=1, starts=starts)
    assert _committed()["replay"]["windows"][:2] == fresh["windows"]


def test_replay_summary_agrees_with_its_windows():
    replay = _committed()["replay"]
    windows = replay["windows"]
    assert replay["forecast_negative"] == sum(1 for w in windows if w["forecast_usd"] < 0)
    assert replay["forecast_dr_missed"] == sum(1 for w in windows if w["forecast_dr_shortfall_kw"] > 0)
    assert replay["change_worst_usd"] == pytest.approx(min(w["forecast_usd"] - w["foresight_usd"] for w in windows))
    assert replay["change_best_usd"] == pytest.approx(max(w["forecast_usd"] - w["foresight_usd"] for w in windows))


def test_replay_windows_do_not_overlap():
    starts = stress._replay_starts(_data())
    gaps = {(b - a).total_seconds() / 3600 for a, b in zip(starts, starts[1:])}
    assert gaps == {config.WINDOW_HOURS}


def test_a_build_gives_the_same_result_twice_and_in_any_worker_count():
    one = stress.scenario_stress("sun", _data(), config.SEED, 2, workers=1)
    again = stress.scenario_stress("sun", _data(), config.SEED, 2, workers=1)
    pooled = stress.scenario_stress("sun", _data(), config.SEED, 2, workers=2)
    assert one == again == pooled


# --- validate and the TypeScript mirror ---------------------------------------------

def test_validate_reports_each_missing_key_at_the_top_level():
    for key in stress.STRESS_INTERFACE_KEYS["StressFile"]:
        doc = _committed()
        del doc[key]
        assert any(repr(key) in e for e in stress.validate(doc)), key


def test_validate_reports_an_unexpected_key_and_a_range_out_of_order():
    doc = _committed()
    doc["scenarios"]["peak"]["families"][0]["zz"] = 1
    doc["replay"]["forecast_usd"]["p10"] = 10**9
    errors = stress.validate(doc)
    assert any("unexpected key 'zz'" in e for e in errors)
    assert any("range out of order" in e for e in errors)


def test_validate_requires_every_family_in_order():
    doc = _committed()
    doc["scenarios"]["peak"]["families"].reverse()
    assert any("families are" in e for e in stress.validate(doc))


def _ts_interfaces() -> dict[str, list[str]]:
    text = TYPES_TS.read_text(encoding="utf-8")
    pattern = re.compile(r"^export interface (\w+)[^{]*\{(.*?)^\}", re.MULTILINE | re.DOTALL)
    return {name: re.findall(r"^\s*(\w+)\??\s*:", body, re.MULTILINE) for name, body in pattern.findall(text)}


def test_stress_types_mirror_the_python_keys():
    declared = _ts_interfaces()
    assert set(declared) == set(stress.STRESS_INTERFACE_KEYS)
    for name, keys in stress.STRESS_INTERFACE_KEYS.items():
        assert sorted(declared[name]) == sorted(keys), name


# --- Rounding that doesn't depend on the machine ----------------------------------

def test_stable_rounding_ignores_float_noise_at_a_half():
    assert stress._stable(1.005 + 1e-12, 2) == stress._stable(1.005 - 1e-12, 2)
