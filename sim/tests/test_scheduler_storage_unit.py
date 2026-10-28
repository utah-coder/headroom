"""Example-based tests for headroom storage dispatch and DR handling (task 8.7).

Property tests for the scheduler live in ``test_sched_*.py``.
"""

import dataclasses
import math

import pytest

from sim import config as c
from sim.jobs import TrainingJob
from sim.scheduler import (
    DEFER,
    GRID_NOW,
    STORAGE_NOW,
    ScenarioInputs,
    facility_kw,
    peak_target_kw,
    run_headroom,
)
from sim.site import DrInfeasibleError, StorageSpec
from sim.tests.test_scheduler_headroom_unit import make_slots, real_inputs  # noqa: F401
from sim.timeutil import iso_z

BAT = StorageSpec.from_config("battery", c.BATTERY)
FLY = StorageSpec.from_config("flywheel", c.FLYWHEEL)
INFERENCE_IT_KW = 2000.0
INFERENCE_FAC = INFERENCE_IT_KW * c.PUE
CARBON = 400.0
HIGH_DF = 9000.0
TOL = 1e-6
# Battery DR reserve from the design formula; flywheel reserve from config.
BAT_RESERVE_KWH = (BAT.soc_min_kwh + c.DR_STORAGE_SUPPORT_KW
                   * c.DR_DURATION_SLOTS * c.SLOT_MINUTES / 60 / BAT.eta * c.DR_RESERVE_MARGIN)
FLY_RESERVE_KWH = FLY.capacity_kwh * c.DR_FLYWHEEL_RESERVE_PCT / 100


def slots_for(n, *, price=None, high_df=()):
    """Flat carbon; DF strictly increasing (forecast stress only at the window end)
    except ``high_df`` slots, which share the top DF value and so are forecast-stressed."""
    price = [c.TARIFF_OFFPEAK_PER_KWH] * n if price is None else price
    df = [HIGH_DF if i in high_df else 4000.0 + i for i in range(n)]
    return make_slots(price, [CARBON] * n, df)


def inputs(jobs, slots, *, dr_start=0, dr_slots=0, cap_kw=None):
    n = len(slots)
    return ScenarioInputs(
        name="test", slots=slots, jobs=tuple(jobs),
        inference_it_kw=tuple([INFERENCE_IT_KW] * n),
        idle_capacity_kw={r: tuple([0.0] * max(1, n // c.SLOTS_PER_HOUR)) for r in c.ALT_REGIONS},
        dr_start=dr_start, dr_slots=dr_slots, requested_shed_kw=c.DR_SHED_KW,
        cap_kw=cap_kw, seed=c.SEED,
    )


def jobs(count, power_kw, duration_min, slack_min, arrival_slot=0):
    return [TrainingJob(id=str(c.JOB_ID_BASE + i), arrival_slot=arrival_slot, power_kw=power_kw,
                        duration_min=duration_min, slack_min=slack_min) for i in range(count)]


def dispatch(res, s):
    return res.trace.slots[s].dispatch


# --- Step 1: DR hold (Req 11.15, 14.4, 14.11)

DR_START = 8


def _dr_run(cap_offset_kw):
    """Four deadline-blocked 500 kW jobs run through a DR event at slots 8–15."""
    n = 32
    load = INFERENCE_FAC + 4 * facility_kw(500)
    inp = inputs(jobs(4, 500, n * c.SLOT_MINUTES, 0), slots_for(n),
                 dr_start=DR_START, dr_slots=c.DR_DURATION_SLOTS, cap_kw=load - cap_offset_kw)
    return inp, load


def test_dr_cap_holds_battery_carries_shed_and_flywheel_covers_start():
    inp, load = _dr_run(1000.0)
    res = run_headroom(inp, trace=True)
    r_kw = 1000.0
    fly_start = r_kw * c.DR_BATTERY_RAMP_S / 2 / (c.SLOT_MINUTES * 60)
    for s in inp.dr_range:
        r, d = res.slots[s], dispatch(res, s)
        assert r.inference_kw + r.training_kw == pytest.approx(load)
        assert r.grid_kw <= inp.cap_kw and r.grid_kw == pytest.approx(inp.cap_kw)
        assert d.dr_kw == pytest.approx(r_kw) and d.charge_kw == 0.0
        assert r.battery.charge_kw == 0.0 and r.flywheel.charge_kw == 0.0
        expected_fly = fly_start if s == DR_START else 0.0
        assert r.flywheel.discharge_kw == pytest.approx(expected_fly)
        assert r.battery.discharge_kw == pytest.approx(r_kw - expected_fly)
    # Every job stays on GRID_NOW inside the budget, so the event emits no Decisions.
    assert not any(d.t in {inp.slots[s].t for s in inp.dr_range} for d in res.decisions)


def test_dr_load_above_storage_limit_raises_naming_the_slot():
    # R = 2,100 kW is above the flywheel's 2,000 kW rating, which must take the
    # full step at the event start.
    inp, _ = _dr_run(FLY.power_kw + 100.0)
    with pytest.raises(DrInfeasibleError) as info:
        run_headroom(inp)
    assert info.value.slot == DR_START and info.value.scenario == "test"


def test_without_a_cap_dr_slots_get_no_dr_dispatch():
    inp, _ = _dr_run(1000.0)
    res = run_headroom(dataclasses.replace(inp, cap_kw=None), trace=True)
    for s in inp.dr_range:
        assert dispatch(res, s).dr_kw == 0.0 and math.isinf(dispatch(res, s).grid_budget_kw)
        assert res.slots[s].battery.charge_kw == 0.0      # still no charging in DR slots


# --- DR routing budget and DR cause (Req 13.6, design "DR behaviour in routing")

def test_dr_budget_defers_excess_jobs_with_dr_cause():
    n, cap = 48, 3100.0
    inp = inputs(jobs(6, 500, 240, 1440), slots_for(n),
                 dr_start=DR_START, dr_slots=c.DR_DURATION_SLOTS, cap_kw=cap)
    res = run_headroom(inp, trace=True)
    kw = facility_kw(500)
    budget = cap + c.DR_STORAGE_SUPPORT_KW

    at_start = [d for d in res.decisions if d.t == inp.slots[DR_START].t]
    # Three jobs fit the budget (2,600 + 3 × 650 ≤ 4,600 kW); the other three defer.
    assert [(d.job, d.route, d.cause) for d in at_start] == [
        ("4004", DEFER, "DR"), ("4005", DEFER, "DR"), ("4006", DEFER, "DR")]
    slot_of = {iso_z(g.t): k for k, g in enumerate(inp.slots)}
    for d in at_start:
        assert d.reason.endswith(f" — DR event, cap {cap:,.0f} kW")
        assert slot_of[d.target] >= DR_START + c.DR_DURATION_SLOTS   # resumes after the event
    assert all(d.cause != "DR" for d in res.decisions if d not in at_start)

    for s in inp.dr_range:
        st = res.trace.slots[s]
        assert st.dispatch.grid_budget_kw == budget
        assert res.slots[s].grid_kw <= cap
        for jt in st.jobs:
            routes = [x.route for x in jt.candidates]
            assert STORAGE_NOW not in routes
            assert (GRID_NOW in routes) == (jt.load_kw + kw <= budget)
    assert math.isinf(res.trace.slots[DR_START - 1].dispatch.grid_budget_kw)
    assert all(o.met_deadline for o in res.outcomes.values())


# --- Step 3: peak shave above P (Req 11.14)

def test_peak_shave_battery_first_then_flywheel_down_to_p():
    n = 16
    p = peak_target_kw(inputs([], slots_for(n)))
    # Two must-run jobs sized so the load sits about 5,000 kW above P: more than
    # the battery's 4,000 kW rating, less than battery + flywheel, for any P0.
    power = math.ceil((p + BAT.power_kw + FLY.power_kw / 2 - INFERENCE_FAC) / 2 / c.PUE)
    inp = inputs(jobs(2, power, 60, 0), slots_for(n))
    res = run_headroom(inp, trace=True)
    assert peak_target_kw(inp) == p
    load = INFERENCE_FAC + 2 * facility_kw(power)
    assert BAT.power_kw < load - p < BAT.power_kw + FLY.power_kw
    r, d = res.slots[0], dispatch(res, 0)
    assert res.trace.slots[0].storage_commit_kw == 0.0
    assert d.peak_shave_kw == pytest.approx(load - p) and d.stress_shave_kw == 0.0
    assert r.battery.discharge_kw == BAT.power_kw
    assert r.flywheel.discharge_kw == pytest.approx(load - p - BAT.power_kw)
    assert r.grid_kw == pytest.approx(p)


# --- Step 4: forecast-stress shave (design step 4)

def test_stress_shave_is_battery_only_and_at_most_stress_discharge_kw():
    n = 24
    res = run_headroom(inputs([], slots_for(n, high_df=range(4, 8))), trace=True)
    for s in range(n):
        r, d = res.slots[s], dispatch(res, s)
        assert d.stress_shave_kw <= c.STRESS_DISCHARGE_KW
        if s in range(4, 8):
            assert res.trace.slots[s].fstress[0]
            assert d.stress_shave_kw == c.STRESS_DISCHARGE_KW
            assert r.battery.discharge_kw == c.STRESS_DISCHARGE_KW and r.flywheel.discharge_kw == 0.0
            assert r.grid_kw == pytest.approx(INFERENCE_FAC - c.STRESS_DISCHARGE_KW)


# --- Step 5: charging (Req 11.13)

def test_charging_only_in_cheap_non_dr_non_stress_slots():
    n = 32
    low, high = 0.03, c.TARIFF_ONPEAK_PER_KWH
    price = [low if 12 <= i < 20 else high for i in range(n)]
    inp = inputs([], slots_for(n, price=price, high_df=(14, 15)),
                 dr_start=24, dr_slots=c.DR_DURATION_SLOTS, cap_kw=INFERENCE_FAC - 500)
    res = run_headroom(inp, trace=True)
    charge = [r.battery.charge_kw + r.flywheel.charge_kw for r in res.slots]

    assert all(kw == 0.0 for kw in charge[:12])          # expensive: outside the cheapest 25 %
    assert charge[14] == charge[15] == 0.0               # forecast stress
    assert all(kw == 0.0 for kw in charge[24:])          # DR event
    assert charge[12] == charge[13] == BAT.power_kw      # battery at rating; flywheel at init SOC
    for s, kw in enumerate(charge):
        if kw > 0:
            st, r = res.trace.slots[s], res.slots[s]
            assert st.dispatch.cheap_slot and not st.fstress[0] and s not in inp.dr_range
            assert r.battery.discharge_kw == 0.0 and r.flywheel.discharge_kw == 0.0
            assert r.grid_kw <= st.peak_kw + TOL

    # storage_cost_basis: weighted average of stored energy and the energy just drawn.
    b0 = c.TARIFF_OFFPEAK_PER_KWH + CARBON * c.CARBON_SHADOW_USD_PER_T / 1e6
    assert dispatch(res, 11).storage_cost_basis == pytest.approx(b0)
    stock = (res.slots[11].battery_soc_pct / 100 * BAT.capacity_kwh / BAT.eta
             + res.slots[11].flywheel_soc_pct / 100 * FLY.capacity_kwh / FLY.eta)
    drawn = charge[12] * c.SLOT_MINUTES / 60
    unit = low + CARBON * c.CARBON_SHADOW_USD_PER_T / 1e6
    assert dispatch(res, 12).storage_cost_basis == pytest.approx(
        (b0 * stock + unit * drawn) / (stock + drawn))


def test_charge_is_limited_so_grid_stays_at_or_below_p():
    inp = inputs(jobs(1, 5000, 120, 0), slots_for(16))
    res = run_headroom(inp, trace=True)
    p = peak_target_kw(inp)
    load = INFERENCE_FAC + facility_kw(5000)
    assert res.slots[0].battery.charge_kw == pytest.approx(p - load)
    assert res.slots[0].grid_kw == pytest.approx(p)


# --- SOC floors (design "Floors", Req 10.13)

def test_dr_reserve_floor_holds_until_the_event():
    n, start = 24, 12
    inp = inputs([], slots_for(n, high_df=range(start)),
                 dr_start=start, dr_slots=c.DR_DURATION_SLOTS, cap_kw=INFERENCE_FAC - 1000)
    res = run_headroom(inp, trace=True)
    for s in range(n):
        d = dispatch(res, s)
        if s < start:
            assert d.battery_floor_kwh == pytest.approx(BAT_RESERVE_KWH)
            assert d.flywheel_floor_kwh == pytest.approx(FLY_RESERVE_KWH)
            assert res.slots[s].battery_soc_pct / 100 * BAT.capacity_kwh >= BAT_RESERVE_KWH - TOL
        else:
            assert d.battery_floor_kwh == BAT.soc_min_kwh and d.flywheel_floor_kwh == FLY.soc_min_kwh
    # Twelve 2,000 kW stress shaves would drain the battery to about 20 %; the reserve stops it.
    assert any(dispatch(res, s).stress_shave_kw < c.STRESS_DISCHARGE_KW for s in range(start))
    assert res.slots[start - 1].battery_soc_pct / 100 * BAT.capacity_kwh == pytest.approx(BAT_RESERVE_KWH)
    assert all(res.slots[s].grid_kw <= inp.cap_kw for s in inp.dr_range)


def test_reserve_floor_matches_design_value():
    assert 100 * BAT_RESERVE_KWH / BAT.capacity_kwh == pytest.approx(31.7, abs=0.1)


# --- Real peak scenario (from the committed Raw_Cache)

@pytest.fixture(scope="module", params=[False, True], ids=["move_off", "move_on"])
def real_run(request, real_inputs):  # noqa: F811
    return real_inputs, run_headroom(real_inputs, trace=True, move_enabled=request.param)


def test_real_scenario_dr_cap_and_dr_causes(real_run):
    inp, res = real_run
    for s in inp.dr_range:
        d = dispatch(res, s)
        assert res.slots[s].grid_kw <= inp.cap_kw
        assert d.dr_kw <= FLY.power_kw and d.charge_kw == 0.0
        for jt in res.trace.slots[s].jobs:
            assert STORAGE_NOW not in [x.route for x in jt.candidates]
    dr_times = {inp.slots[s].t for s in inp.dr_range}
    dr_decisions = [d for d in res.decisions if d.cause == "DR"]
    if not res.moves:
        # With MOVE_REGION on, jobs moved earlier can leave the DR load under
        # the cap, so the budget never binds; without it the budget must bind.
        assert dr_decisions
    for d in dr_decisions:
        assert d.t in dr_times and d.reason.endswith(f" — DR event, cap {round(inp.cap_kw):,} kW")


def test_real_scenario_storage_invariants(real_run):
    inp, res = real_run
    for s, r in enumerate(res.slots):
        st = res.trace.slots[s]
        for flows in (r.battery, r.flywheel):                      # Req 10.14
            assert flows.charge_kw == 0.0 or flows.discharge_kw == 0.0
        for spec, soc in ((BAT, r.battery_soc_pct), (FLY, r.flywheel_soc_pct)):   # Req 10.13
            assert spec.soc_min_pct - TOL <= soc <= spec.soc_max_pct + TOL
        assert r.grid_kw >= 0.0                                    # Req 10.15
        assert st.dispatch.stress_shave_kw <= c.STRESS_DISCHARGE_KW
        if st.dispatch.charge_kw > 0:                              # Req 11.13
            assert st.dispatch.cheap_slot and not st.fstress[0] and s not in inp.dr_range
            assert r.grid_kw <= st.peak_kw + TOL
        if s < inp.dr_start:
            assert r.battery_soc_pct / 100 * BAT.capacity_kwh >= BAT_RESERVE_KWH - TOL
            assert r.flywheel_soc_pct / 100 * FLY.capacity_kwh >= FLY_RESERVE_KWH - TOL
