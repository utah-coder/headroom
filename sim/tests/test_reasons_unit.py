"""Example-based tests for the Reason_String helpers in sim/scheduler.py.

Requirements 12.8, 13.3, 13.4, 13.5, 13.6, 13.8, 13.9.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from sim import config as c
from sim.grid import GridSlot
from sim.jobs import TrainingJob
from sim.scheduler import (
    DEFER,
    GRID_NOW,
    MOVE_REGION,
    REASON_MAX_CHARS,
    STORAGE_NOW,
    Decision,
    JobState,
    ScenarioInputs,
    decision_target,
    demand_percentile,
    flag_late,
    job_outcome,
    ordinal,
    reason_string,
    run_baseline,
)
from sim.timeutil import slot_times

UTC = timezone.utc
# 2026-07-15 20:15 UTC is 14:15 MDT; 07:00 UTC next day is 01:00 MDT.
T = datetime(2026, 7, 15, 20, 15, tzinfo=UTC)
RESUME = datetime(2026, 7, 16, 7, 0, tzinfo=UTC)
COMMON = dict(demand_pct=97, carbon_g_per_kwh=610.4, peak_delta_kw=380.2)


@pytest.mark.parametrize("n, expected", [
    (0, "0th"), (1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (10, "10th"),
    (11, "11th"), (12, "12th"), (13, "13th"), (21, "21st"), (22, "22nd"),
    (23, "23rd"), (97, "97th"), (100, "100th"), (101, "101st"), (111, "111th"),
    (112, "112th"), (113, "113th"),
])
def test_ordinal_suffixes(n, expected):
    assert ordinal(n) == expected


def test_demand_percentile_counts_slots_at_or_below():
    demand = [10.0, 20.0, 20.0, 40.0]
    assert [demand_percentile(demand, i) for i in range(4)] == [25, 75, 75, 100]


def test_demand_percentile_floors_and_never_drops_below_one():
    demand = list(range(192))
    assert demand_percentile(demand, 0) == 1        # floor(100/192) = 0 → 1
    assert demand_percentile(demand, 2) == 1        # floor(300/192) = 1
    assert demand_percentile(demand, 190) == 99     # floor(19100/192) = 99
    assert demand_percentile(demand, 191) == 100


def test_defer_reason_matches_documented_example():
    r = reason_string(T, "4412", DEFER, defer_to=RESUME, **COMMON)
    assert r == ("14:15 job#4412 → DEFER to 01:00 — PACE demand 97th pct, 610 g/kWh, "
                 "would raise monthly peak by 380 kW")
    assert decision_target(DEFER, defer_to=RESUME) == "2026-07-16T07:00:00Z"


@pytest.mark.parametrize("route", [GRID_NOW, STORAGE_NOW])
def test_grid_and_storage_reasons_have_no_target(route):
    r = reason_string(T, "4412", route, **COMMON)
    assert r == (f"14:15 job#4412 → {route} — PACE demand 97th pct, 610 g/kWh, "
                 "would raise monthly peak by 380 kW")
    assert decision_target(route) is None


def test_move_region_reason_uses_compact_form():
    r = reason_string(T, "4412", MOVE_REGION, move_region="BPAT",
                      move_carbon_g_per_kwh=91.6, **COMMON)
    assert r == ("14:15 job#4412 → MOVE_REGION to BPAT ($75, +30 min, 92 g/kWh) — "
                 "PACE demand 97th pct, 610 g/kWh, would raise monthly peak by 380 kW")
    assert decision_target(MOVE_REGION, move_region="BPAT") == "BPAT"


def test_move_region_penalty_and_delay_come_from_config():
    r = reason_string(T, "1", MOVE_REGION, move_region="CISO",
                      move_carbon_g_per_kwh=200, **COMMON)
    assert f"${c.MOVE_PENALTY_USD:,.0f}" in r
    assert f"+{c.MOVE_DELAY_SLOTS * c.SLOT_MINUTES} min" in r


def test_dr_suffix_states_cap_with_thousands_separator():
    r = reason_string(T, "4412", DEFER, defer_to=RESUME, dr_cap_kw=8123.4, **COMMON)
    assert r.endswith("would raise monthly peak by 380 kW — demand-response event, grid cap 8,123 kW")


def test_late_suffix_comes_last():
    r = reason_string(T, "4412", GRID_NOW, dr_cap_kw=8123, late_min=45, **COMMON)
    assert r.endswith(" — demand-response event, grid cap 8,123 kW [LATE +45 min]")


def test_negative_peak_delta_is_clamped_to_zero():
    r = reason_string(T, "4412", GRID_NOW, demand_pct=50, carbon_g_per_kwh=500,
                      peak_delta_kw=-250.0)
    assert r.endswith("would raise monthly peak by 0 kW")
    assert "-" not in r.split("peak by ")[1]


def test_longest_realistic_reason_fits_in_200_chars():
    r = reason_string(T, "9999", MOVE_REGION, move_region="CISO",
                      move_carbon_g_per_kwh=999.9, demand_pct=100,
                      carbon_g_per_kwh=9999.4, peak_delta_kw=99_999,
                      dr_cap_kw=99_999, late_min=9999)
    assert len(r) <= REASON_MAX_CHARS


def test_overlong_reason_is_rejected():
    with pytest.raises(ValueError):
        reason_string(T, "x" * 200, GRID_NOW, **COMMON)


@pytest.mark.parametrize("kwargs", [
    dict(route=DEFER),                                         # missing resume time
    dict(route=MOVE_REGION, move_region="PACE", move_carbon_g_per_kwh=1),
    dict(route=MOVE_REGION, move_region="BPAT"),               # missing target carbon
    dict(route=GRID_NOW, defer_to=RESUME),
    dict(route=STORAGE_NOW, move_region="BPAT"),
    dict(route="NAP"),
])
def test_inconsistent_targets_are_rejected(kwargs):
    route = kwargs.pop("route")
    with pytest.raises(ValueError):
        reason_string(T, "4412", route, **COMMON, **kwargs)


@pytest.mark.parametrize("pct", [0, 101])
def test_percentile_out_of_range_is_rejected(pct):
    with pytest.raises(ValueError):
        reason_string(T, "4412", GRID_NOW, demand_pct=pct, carbon_g_per_kwh=1,
                      peak_delta_kw=0)


def test_flag_late_appends_late_suffix_once():
    t0 = T
    js = JobState.new(TrainingJob(id="4001", arrival_slot=0, power_kw=500,
                                  duration_min=30, slack_min=0), n_slots=1)
    js.run_slot(0)
    js.pause()        # ends the 15-min window paused: 15 + 15 + 5 restart = 35 > 30
    out = job_outcome(js, n_slots=1)
    assert out.late and out.late_min == 5
    decisions = [Decision(t=t0, job="4001", route=DEFER, target=None,
                          reason="r", cause="COST")]
    flagged = flag_late(decisions, {"4001": out})
    assert flagged[0].late and flagged[0].reason == "r [LATE +5 min]"
    assert flag_late(flagged, {"4001": out})[0].reason == "r [LATE +5 min]"


def _baseline_inputs() -> ScenarioInputs:
    times = slot_times(T, 4)
    demand = [5000.0, 6000.0, 7000.0, 8000.0]
    slots = tuple(
        GridSlot(t=t, pace_demand_mw=d, pace_forecast_mw=d, carbon_g_per_kwh=600.4,
                 alt_carbon={"BPAT": 90.0, "CISO": 200.0},
                 price_per_kwh=c.TARIFF_OFFPEAK_PER_KWH, on_peak=False, stress=False)
        for t, d in zip(times, demand)
    )
    jobs = (
        TrainingJob(id="1001", arrival_slot=0, power_kw=1000, duration_min=60,
                    slack_min=600),
        TrainingJob(id="1002", arrival_slot=2, power_kw=100, duration_min=15,
                    slack_min=600),
    )
    return ScenarioInputs(
        name="peak", slots=slots, jobs=jobs, inference_it_kw=(2000.0,) * 4,
        idle_capacity_kw={"BPAT": (0.0,), "CISO": (0.0,)}, dr_start=0, dr_slots=0,
        requested_shed_kw=0.0, cap_kw=None, seed=1,
    )


def test_baseline_decisions_carry_full_reason_strings():
    res = run_baseline(_baseline_inputs())
    d1, d2 = res.decisions
    fac = 1000.0 * c.PUE
    # Slot 0: nothing earlier, so the job's whole facility draw raises the peak.
    assert d1.reason == (f"14:15 job#1001 → GRID_NOW — PACE demand 25th pct, 600 g/kWh, "
                         f"would raise monthly peak by {round(fac)} kW")
    # Slot 2: job 1001 is still running, so job 1002 lifts the peak by its own facility kW.
    assert d2.reason == (f"14:45 job#1002 → GRID_NOW — PACE demand 75th pct, 600 g/kWh, "
                         f"would raise monthly peak by {round(100.0 * c.PUE)} kW")
    assert d1.target is None and d2.target is None
    assert all(len(d.reason) <= REASON_MAX_CHARS for d in res.decisions)
