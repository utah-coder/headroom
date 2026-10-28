"""Example-based tests for the headroom routing in sim.scheduler (task 8.6).

Property tests for the scheduler live in ``test_sched_*.py``.
"""

import dataclasses
import math
from datetime import datetime, timezone

import pytest

from sim import config as c
from sim.grid import GridSlot
from sim.jobs import TrainingJob
from sim.scheduler import (
    DEFER,
    GRID_NOW,
    ROUTE_ORDER,
    STORAGE_NOW,
    ScenarioInputs,
    facility_kw,
    peak_target_kw,
    run_baseline,
    run_headroom,
    run_slots,
    unit_cost_per_kwh,
)
from sim.site import StorageSpec
from sim.timeutil import iso_z, slot_times

UTC = timezone.utc
START = datetime(2026, 7, 15, 6, 0, tzinfo=UTC)   # 00:00 MDT, a Wednesday
N = 24
INFERENCE_IT_KW = 2000.0
HIGH_DF, LOW_DF = 6000.0, 4000.0


def make_slots(price, carbon, df, demand=None):
    demand = df if demand is None else demand
    return tuple(
        GridSlot(t=t, pace_demand_mw=demand[i], pace_forecast_mw=df[i],
                 carbon_g_per_kwh=carbon[i], alt_carbon={"BPAT": 100.0, "CISO": 200.0},
                 price_per_kwh=price[i], on_peak=False, stress=False)
        for i, t in enumerate(slot_times(START, len(price)))
    )


def two_phase_slots(n=N, pricey_until=8):
    """Slots [0, pricey_until) are on-peak, dirty and forecast-stressed; the rest cheap."""
    pricey = range(pricey_until)
    price = [c.TARIFF_ONPEAK_PER_KWH if i in pricey else c.TARIFF_OFFPEAK_PER_KWH for i in range(n)]
    carbon = [800.0 if i in pricey else 100.0 for i in range(n)]
    df = [HIGH_DF if i in pricey else LOW_DF for i in range(n)]
    return make_slots(price, carbon, df)


def make_inputs(jobs, slots, dr_start=0, dr_slots=0, cap_kw=None):
    n = len(slots)
    return ScenarioInputs(
        name="test", slots=slots, jobs=tuple(jobs),
        inference_it_kw=tuple([INFERENCE_IT_KW] * n),
        idle_capacity_kw={r: tuple([0.0] * max(1, n // c.SLOTS_PER_HOUR)) for r in c.ALT_REGIONS},
        dr_start=dr_start, dr_slots=dr_slots, requested_shed_kw=c.DR_SHED_KW,
        cap_kw=cap_kw, seed=c.SEED,
    )


def job(id="4001", arrival_slot=1, power_kw=300, duration_min=60, slack_min=1440):
    return TrainingJob(id=id, arrival_slot=arrival_slot, power_kw=power_kw,
                       duration_min=duration_min, slack_min=slack_min)


def decision_tuples(res):
    return [(d.t, d.job, d.route, d.target, d.cause) for d in res.decisions]


def job_traces(res, job_id):
    return [(st.slot, jt) for st in res.trace.slots for jt in st.jobs if jt.job == job_id]


# --- Helpers

def test_peak_target_is_average_facility_load():
    inp = make_inputs([], two_phase_slots())
    expected = c.PEAK_TARGET_SCALE * c.PUE * (INFERENCE_IT_KW + c.IT_PEAK_KW * c.TRAINING_SHARE)
    assert math.isclose(peak_target_kw(inp), expected)
    assert run_headroom(inp, trace=True).trace.peak_target_kw == peak_target_kw(inp)


def test_unit_cost_and_run_slots():
    slot = two_phase_slots()[0]
    assert math.isclose(unit_cost_per_kwh(slot),
                        c.TARIFF_ONPEAK_PER_KWH + 800.0 * c.CARBON_SHADOW_USD_PER_T / 1e6)
    assert run_slots(3, 40, 24) == [(3, 15), (4, 15), (5, 10)]
    assert run_slots(22, 60, 24) == [(22, 15), (23, 15)]


# --- Deferral (Req 11.4, 11.6, 11.7)

def test_job_defers_out_of_stressed_expensive_slots_to_cheap_hour():
    slots = two_phase_slots()
    inp = make_inputs([job(arrival_slot=1)], slots)
    res = run_headroom(inp, trace=True)

    assert decision_tuples(res) == [
        (slots[1].t, "4001", DEFER, iso_z(slots[8].t), "COST"),
        (slots[8].t, "4001", GRID_NOW, None, "COST"),
    ]
    assert " → DEFER to 02:00 — " in res.decisions[0].reason   # slot 8 = 02:00 MDT
    training = [r.training_kw for r in res.slots]
    assert all(kw == 0.0 for kw in training[:8] + training[12:])
    assert all(math.isclose(kw, facility_kw(300)) for kw in training[8:12])
    o = res.outcomes["4001"]
    assert o.met_deadline and o.resumes == 0 and o.finish_min == 8 * c.SLOT_MINUTES + 60

    first = res.trace.slots[1]
    assert first.fstress[:7] == (True,) * 7 and not any(first.fstress[7:])
    jt = first.jobs[0]
    # Slot 4 is on the hour but forecast-stressed; slot 20 would miss plan_deadline.
    assert [o.target_slot for o in jt.defer_options] == [8, 12, 16]
    costs = {cand.route: cand.cost for cand in jt.candidates}
    assert set(costs) == {GRID_NOW, STORAGE_NOW, DEFER}
    assert costs[DEFER] < min(costs[GRID_NOW], costs[STORAGE_NOW])
    assert jt.route == DEFER and jt.target_slot == 8


def test_deferred_job_reservation_moves_to_its_target():
    slots = two_phase_slots()
    res = run_headroom(make_inputs([job("4001", 1), job("4002", 1, power_kw=500)], slots),
                       trace=True)
    # Both jobs defer to the cheap hour; the second sees the first's reservation there.
    routes = {d.job: (d.route, d.target) for d in res.decisions if d.t == slots[1].t}
    assert routes == {"4001": (DEFER, iso_z(slots[8].t)), "4002": (DEFER, iso_z(slots[8].t))}
    assert all(r.training_kw == 0.0 for r in res.slots[:8])


# --- Deadline-risk fallback (Req 11.9, 11.11, DECISIONS.md)

def test_job_that_cannot_finish_in_window_runs_like_baseline_with_deadline_cause():
    slots = two_phase_slots()
    j = job(arrival_slot=20, duration_min=120, slack_min=360)
    inp = make_inputs([j], slots)
    head, base = run_headroom(inp, trace=True), run_baseline(inp)

    assert decision_tuples(head) == [(slots[20].t, "4001", GRID_NOW, None, "DEADLINE")]
    assert [r.training_kw for r in head.slots] == [r.training_kw for r in base.slots]
    assert head.outcomes["4001"].met_deadline
    for _, jt in job_traces(head, "4001"):
        assert jt.deadline_blocked and jt.route == GRID_NOW and jt.cause == "DEADLINE"
        assert [cand.route for cand in jt.candidates] == [GRID_NOW]


def test_tight_job_in_dr_slot_falls_back_to_grid_now():
    slots = two_phase_slots()
    inp = make_inputs([job(arrival_slot=2, slack_min=0)], slots, dr_start=0, dr_slots=c.DR_DURATION_SLOTS)
    res = run_headroom(inp, trace=True)
    # No DEFER target meets the deadline and STORAGE_NOW is not offered in DR slots.
    assert decision_tuples(res) == [(slots[2].t, "4001", GRID_NOW, None, "DEADLINE")]
    assert [r.training_kw for r in res.slots] == [r.training_kw for r in run_baseline(inp).slots]
    assert res.outcomes["4001"].met_deadline


def test_tight_job_outside_dr_can_use_storage_and_storage_serves_it():
    slots = two_phase_slots()
    res = run_headroom(make_inputs([job(arrival_slot=2, slack_min=0)], slots), trace=True)
    assert decision_tuples(res) == [(slots[2].t, "4001", STORAGE_NOW, None, "COST")]
    kw = facility_kw(300)
    for s in range(2, 6):
        r, d = res.slots[s], res.trace.slots[s].dispatch
        # The battery serves the job first; the forecast-stress shave comes on top.
        assert d.commit_kw == kw and d.peak_shave_kw == 0.0
        assert math.isclose(r.battery.discharge_kw, kw + d.stress_shave_kw)
        assert r.flywheel.discharge_kw == 0.0 and r.battery.charge_kw == 0.0
        assert math.isclose(r.grid_kw, r.inference_kw - d.stress_shave_kw)
    assert res.outcomes["4001"].met_deadline


# --- Tie-break by ROUTE_ORDER (Req 11.8)

def _flat_slots(first_price=None):
    """Zero carbon and a grid price equal to the STORAGE_NOW unit cost, so routes tie."""
    eta = StorageSpec.from_config("battery", c.BATTERY).eta
    u = c.TARIFF_OFFPEAK_PER_KWH / eta + c.STORAGE_WEAR_USD_PER_KWH
    price = [u] * N
    if first_price is not None:
        price[0] = first_price
    df = [HIGH_DF if i < 4 else LOW_DF for i in range(N)]
    return make_slots(price, [0.0] * N, df)


def _first_trace(res):
    return res.trace.slots[0].jobs[0]


def _rounded(jt):
    return {cand.route: round(cand.cost, 9) for cand in jt.candidates}


def test_three_way_tie_picks_grid_now():
    res = run_headroom(make_inputs([job(arrival_slot=0)], _flat_slots()), trace=True)
    jt = _first_trace(res)
    costs = _rounded(jt)
    assert set(costs) == {GRID_NOW, STORAGE_NOW, DEFER} and len(set(costs.values())) == 1
    assert jt.route == GRID_NOW


def test_storage_beats_defer_on_a_tie():
    res = run_headroom(make_inputs([job(arrival_slot=0)], _flat_slots(first_price=0.2)), trace=True)
    jt = _first_trace(res)
    costs = _rounded(jt)
    assert costs[STORAGE_NOW] == costs[DEFER] < costs[GRID_NOW]
    assert jt.route == STORAGE_NOW


def test_grid_beats_defer_on_a_tie_when_storage_is_excluded():
    inp = make_inputs([job(arrival_slot=0)], _flat_slots(), dr_start=0, dr_slots=2)
    jt = _first_trace(run_headroom(inp, trace=True))
    costs = _rounded(jt)
    assert set(costs) == {GRID_NOW, DEFER} and costs[GRID_NOW] == costs[DEFER]
    assert jt.route == GRID_NOW and jt.cause == "COST"


# --- Real-data-shaped scenario built from the committed Raw_Cache

@pytest.fixture(scope="module")
def real_inputs():
    from sim.carbon import hourly_intensity
    from sim.fetch_eia import load_all
    from sim.grid import build_hourly, build_slots, select_dr_start, select_peak_window
    from sim.jobs import generate
    from sim.site import idle_capacity_kw, inference_it_kw

    raw = load_all(offline=True)
    hourly = build_hourly(raw)
    carbon = {r: hourly_intensity(raw.get(f"fuel-type-data:{r}", []), r)[0]
              for r in (c.HOME_REGION, *c.ALT_REGIONS)}
    grid = build_slots(select_peak_window(hourly[f"{c.HOME_REGION}.D"]), hourly, carbon)
    slots = grid.slots
    n = len(slots)
    inp = ScenarioInputs(
        name="peak", slots=slots, jobs=tuple(generate(n, c.SEED, "peak")),
        inference_it_kw=tuple(inference_it_kw([g.t for g in slots])),
        idle_capacity_kw={r: tuple(v) for r, v in
                          idle_capacity_kw(n // c.SLOTS_PER_HOUR, c.SEED, "peak").items()},
        dr_start=select_dr_start([g.pace_demand_mw for g in slots]),
        dr_slots=c.DR_DURATION_SLOTS, requested_shed_kw=c.DR_SHED_KW, cap_kw=None, seed=c.SEED,
    )
    cap = run_baseline(inp).slots[inp.dr_start].grid_kw - c.DR_SHED_KW
    return dataclasses.replace(inp, cap_kw=cap)


@pytest.fixture(scope="module")
def real_result(real_inputs):
    return run_headroom(real_inputs, trace=True)


def test_real_scenario_accounts_for_every_job(real_inputs, real_result):
    inp, res = real_inputs, real_result
    assert len(res.slots) == inp.n_slots == 192
    assert set(res.outcomes) == {j.id for j in inp.jobs}
    first = {}
    for d in res.decisions:
        first.setdefault(d.job, d)
    assert set(first) == set(res.outcomes)
    for j in inp.jobs:
        assert first[j.id].t == inp.slots[j.arrival_slot].t
    assert all(o.met_deadline for o in res.outcomes.values())          # Req 11.11
    assert not any(d.late for d in res.decisions)
    assert any(d.route == DEFER for d in res.decisions)
    assert all(len(d.reason) <= 200 for d in res.decisions)


def test_real_scenario_inference_and_power_balance(real_inputs, real_result):
    inf = real_inputs.inference_fac
    for s, r in enumerate(real_result.slots):
        assert r.inference_kw == inf[s]                                  # Req 11.10 (exact)
        assert r.grid_kw >= 0 and r.moved_kw == 0.0
        assert math.isclose(r.grid_kw, r.inference_kw + r.training_kw - r.battery_kw - r.flywheel_kw,
                            abs_tol=1e-6)
        d = real_result.trace.slots[s].dispatch
        assert math.isclose(r.battery.discharge_kw + r.flywheel.discharge_kw,
                            d.dr_kw + d.commit_kw + d.peak_shave_kw + d.stress_shave_kw,
                            abs_tol=1e-6)
        assert math.isclose(r.battery.charge_kw + r.flywheel.charge_kw, d.charge_kw, abs_tol=1e-6)
        for soc in (r.battery_soc_pct, r.flywheel_soc_pct):
            assert c.BATTERY["soc_min_pct"] - 1e-6 <= soc <= c.BATTERY["soc_max_pct"] + 1e-6


def test_real_scenario_energy_conservation(real_inputs, real_result):
    delivered = 0.0
    for j in real_inputs.jobs:
        o = real_result.outcomes[j.id]
        delivered += o.delivered_kwh
        if o.completed_in_window:
            assert math.isclose(o.delivered_kwh,
                                j.power_kw * (j.duration_min + c.RESTART_PENALTY_MIN * o.resumes) / 60)
    training_fac_kwh = sum(r.training_kw for r in real_result.slots) * c.SLOT_MINUTES / 60
    assert math.isclose(training_fac_kwh, delivered * c.PUE)


def test_real_scenario_decisions_only_on_route_change(real_result):
    prev: dict[str, str] = {}
    emitted = []
    for st in real_result.trace.slots:
        for jt in st.jobs:
            assert jt.decision == (prev.get(jt.job) != jt.route)
            prev[jt.job] = jt.route
            if jt.decision:
                emitted.append((st.slot, jt.job, jt.route))
    t_index = {r.t: s for s, r in enumerate(real_result.slots)}
    got = [(t_index[d.t], d.job, d.route) for d in real_result.decisions]
    assert got == emitted


def test_real_scenario_routes_are_filtered_argmin(real_inputs, real_result):
    inp, tr = real_inputs, real_result.trace
    states_by_id = {j.id: j for j in inp.jobs}
    p = tr.peak_target_kw
    assert p == peak_target_kw(inp)
    for st in tr.slots:
        assert st.peak_kw == p
        p = st.peak_after_kw
        assert p == max(st.peak_kw, st.grid_kw)
        assert st.horizon_end == min(inp.n_slots, st.slot + c.LOOKAHEAD_SLOTS)
        order = [(not jt.deadline_blocked, jt.job) for jt in st.jobs]
        assert [b for b, _ in order] == sorted(b for b, _ in order)   # blocked jobs first
        for jt in st.jobs:
            routes = [cand.route for cand in jt.candidates]
            if jt.cause == "DEADLINE":
                # Blocked with only GRID_NOW left, or nothing offered (DR budget spent).
                assert jt.route == GRID_NOW and routes in ([GRID_NOW], [])
                assert jt.deadline_blocked or not routes
            else:
                best = min(jt.candidates,
                           key=lambda x: (round(x.cost, 9), ROUTE_ORDER.index(x.route)))
                assert (jt.route, jt.target_slot) == (best.route, best.target_slot)
            kw_fac = facility_kw(states_by_id[jt.job].power_kw)
            assert jt.planned_peak_kw >= st.peak_kw
            # Demand_Charge_Risk is the rise the job causes, never more than its own kW.
            for cand in (jt.grid_now, *jt.candidates):
                assert 0.0 <= cand.peak_excess_kw <= kw_fac + 1e-6
            if STORAGE_NOW in routes:
                assert st.slot not in inp.dr_range and jt.storage_available_kw >= kw_fac
            for o in jt.defer_options:
                t = o.target_slot
                assert st.slot < t < st.horizon_end and t % c.DEFER_TARGET_STEP_SLOTS == 0
                assert t not in inp.dr_range and not st.fstress[t - st.slot]


def test_planning_never_reads_actual_demand_for_future_slots(real_inputs, real_result):
    s0 = 60
    nan_slots = tuple(
        g if k <= s0 else dataclasses.replace(g, pace_demand_mw=math.nan, stress=not g.stress)
        for k, g in enumerate(real_inputs.slots)
    )
    res = run_headroom(dataclasses.replace(real_inputs, slots=nan_slots))
    ref = real_result
    assert res.slots == ref.slots
    assert [(d.t, d.job, d.route, d.target, d.cause) for d in res.decisions] == \
           [(d.t, d.job, d.route, d.target, d.cause) for d in ref.decisions]
    assert res.outcomes == ref.outcomes
