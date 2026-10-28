"""Example-based tests for MOVE_REGION in sim.scheduler (task 8.8, Requirement 12).

The property test for MOVE_REGION is ``test_sched_move.py`` (task 8.20).
"""

import math
from datetime import datetime, timezone

import pytest

from sim import config as c
from sim.grid import GridSlot
from sim.jobs import TrainingJob
from sim.scheduler import (
    DEFER,
    GRID_NOW,
    MOVE_REGION,
    ROUTE_ORDER,
    STORAGE_NOW,
    MoveRecord,
    ScenarioInputs,
    facility_kw,
    run,
    run_headroom,
)
from sim.timeutil import slot_times

# Reuse the real-data peak scenario fixture (committed Raw_Cache, offline).
from sim.tests.test_scheduler_headroom_unit import real_inputs  # noqa: F401

UTC = timezone.utc
START = datetime(2026, 7, 15, 6, 0, tzinfo=UTC)   # 00:00 MDT
N = 24
N_HOURS = N // c.SLOTS_PER_HOUR
INFERENCE_IT_KW = 2000.0
ALT_CI = {"BPAT": 100.0, "CISO": 200.0}
SLOT_KWH = facility_kw(300) * c.SLOT_MINUTES / 60   # one full slot of a 300 kW job


def pricey_slots(n=N):
    """Grid at $1/kWh everywhere, so a cheap, clean Alternate_Region wins after its $75."""
    return tuple(
        GridSlot(t=t, pace_demand_mw=4000.0 + i, pace_forecast_mw=4000.0 + i,
                 carbon_g_per_kwh=500.0, alt_carbon=dict(ALT_CI),
                 price_per_kwh=1.0, on_peak=False, stress=False)
        for i, t in enumerate(slot_times(START, n))
    )


def idle(bpat=1000.0, ciso=1000.0):
    def hours(v):
        return tuple(v) if isinstance(v, (list, tuple)) else (float(v),) * N_HOURS
    return {"BPAT": hours(bpat), "CISO": hours(ciso)}


def make_inputs(jobs, idle_kw=None, slots=None):
    slots = pricey_slots() if slots is None else slots
    return ScenarioInputs(
        name="test", slots=slots, jobs=tuple(jobs),
        inference_it_kw=(INFERENCE_IT_KW,) * len(slots),
        idle_capacity_kw=idle() if idle_kw is None else idle_kw,
        dr_start=0, dr_slots=0, requested_shed_kw=c.DR_SHED_KW, cap_kw=None, seed=c.SEED,
    )


def job(id="4001", arrival_slot=1, power_kw=300, duration_min=60, slack_min=1440):
    return TrainingJob(id=id, arrival_slot=arrival_slot, power_kw=power_kw,
                       duration_min=duration_min, slack_min=slack_min)


def decision_tuples(res):
    return [(d.t, d.job, d.route, d.target, d.cause) for d in res.decisions]


def job_traces(res, job_id):
    return [(st.slot, jt) for st in res.trace.slots for jt in st.jobs if jt.job == job_id]


def best(cands):
    return min(cands, key=lambda x: (round(x.cost, 9), ROUTE_ORDER.index(x.route)))


# --- Move chosen (Req 12.1, 12.2, 12.3, 12.6, 12.8)

def test_move_chosen_when_alternate_region_is_cheaper_and_has_capacity():
    slots = pricey_slots()
    res = run_headroom(make_inputs([job()]), trace=True)

    assert decision_tuples(res) == [(slots[1].t, "4001", MOVE_REGION, "BPAT", "COST")]
    reason = res.decisions[0].reason
    assert "job#4001 → MOVE_REGION to BPAT ($75, +30 min, 100 g/kWh) — PACE demand " in reason
    assert len(reason) <= 200

    # Starts 2 slots after the decision and runs continuously there.
    assert res.moves == (MoveRecord(job_id="4001", region="BPAT", decided_slot=1, start_slot=3,
                                    kwh_by_slot={3: SLOT_KWH, 4: SLOT_KWH, 5: SLOT_KWH,
                                                 6: SLOT_KWH}),)
    for s, r in enumerate(res.slots):
        assert r.training_kw == 0.0
        assert math.isclose(r.moved_kw, facility_kw(300) if 3 <= s <= 6 else 0.0)
        # moved_kw is never part of grid draw.
        assert math.isclose(r.grid_kw, r.inference_kw + r.training_kw - r.battery_kw - r.flywheel_kw,
                            abs_tol=1e-6)
    o = res.outcomes["4001"]
    assert o.finish_min == 3 * c.SLOT_MINUTES + 60 and o.met_deadline and o.resumes == 0
    assert math.isclose(o.delivered_kwh, 300.0)

    (_, jt), = job_traces(res, "4001")
    moves = {m.region: m for m in jt.move_options}
    assert set(moves) == {"BPAT", "CISO"}
    kwh = SLOT_KWH * 4
    for region, ci in ALT_CI.items():   # $75 once + alternate price + carbon × shadow
        expected = c.MOVE_PENALTY_USD + kwh * (c.ALT_REGION_PRICE_PER_KWH
                                               + ci * c.CARBON_SHADOW_USD_PER_T / 1e6)
        assert math.isclose(moves[region].cost, expected)
        assert moves[region].target_slot == 3 and moves[region].peak_excess_kw == 0.0
    assert jt.route == MOVE_REGION and best(jt.candidates).region == "BPAT"


def test_penalty_does_not_scale_with_job_size():
    small = run_headroom(make_inputs([job(power_kw=100)]), trace=True)
    big = run_headroom(make_inputs([job(power_kw=800)]), trace=True)
    for res, kw in ((small, 100), (big, 800)):
        (_, jt), = job_traces(res, "4001")
        m = next(x for x in jt.move_options if x.region == "BPAT")
        energy = facility_kw(kw) * (c.ALT_REGION_PRICE_PER_KWH
                                    + ALT_CI["BPAT"] * c.CARBON_SHADOW_USD_PER_T / 1e6)
        assert math.isclose(m.cost - energy, c.MOVE_PENALTY_USD)


# --- Idle capacity (Req 12.5)

def test_move_rejected_in_region_short_of_capacity_in_any_hour():
    # The remote run covers slots 3-6, i.e. hours 0 and 1; BPAT is short in hour 1 only.
    res = run_headroom(make_inputs([job()], idle([1000.0, 200.0, 1000.0, 1000.0, 1000.0, 1000.0])),
                       trace=True)
    assert [(d.route, d.target) for d in res.decisions] == [(MOVE_REGION, "CISO")]
    (_, jt), = job_traces(res, "4001")
    assert [m.region for m in jt.move_options] == ["CISO"]


def test_capacity_equal_to_job_power_is_enough():
    res = run_headroom(make_inputs([job()], idle(bpat=300.0, ciso=0.0)))
    assert [(d.route, d.target) for d in res.decisions] == [(MOVE_REGION, "BPAT")]


def test_no_capacity_anywhere_picks_next_cheapest_route():
    res = run_headroom(make_inputs([job()], idle(bpat=299.0, ciso=0.0)), trace=True)
    assert all(d.route != MOVE_REGION for d in res.decisions) and res.moves == ()
    for _, jt in job_traces(res, "4001"):
        assert jt.move_options == ()
        assert {x.route for x in jt.candidates} <= {GRID_NOW, STORAGE_NOW, DEFER}
        assert jt.route == best(jt.candidates).route
    assert all(r.moved_kw == 0.0 for r in res.slots)


def test_alt_used_ledger_accumulates_across_moves():
    jobs = [job("4001"), job("4002"), job("4003", arrival_slot=9)]
    # BPAT fits one 300 kW job per hour; jobs 4001/4002 run there in hours 0-1, 4003 in 2-3.
    res = run_headroom(make_inputs(jobs, idle(bpat=500.0, ciso=500.0)))
    assert {m.job_id: m.region for m in res.moves} == {"4001": "BPAT", "4002": "CISO",
                                                       "4003": "BPAT"}
    # With no CISO capacity the second job cannot move at all.
    res = run_headroom(make_inputs(jobs[:2], idle(bpat=500.0, ciso=0.0)), trace=True)
    assert {m.job_id: m.region for m in res.moves} == {"4001": "BPAT"}
    assert all(jt.move_options == () for _, jt in job_traces(res, "4002"))


# --- Deadline (Req 12.7)

def test_move_accepted_when_it_finishes_exactly_at_the_deadline():
    # deadline = 15 + 60 + 30 = 105 = (1 + 2) × 15 + 60
    res = run_headroom(make_inputs([job(slack_min=30)]))
    assert [d.route for d in res.decisions] == [MOVE_REGION]
    assert res.outcomes["4001"].finish_min == 105 and res.outcomes["4001"].met_deadline


def test_move_rejected_when_it_would_miss_plan_deadline():
    # deadline 90 < 105: moving would finish late, so the next-cheapest route runs.
    res = run_headroom(make_inputs([job(slack_min=15)]), trace=True)
    assert res.moves == () and all(d.route != MOVE_REGION for d in res.decisions)
    (_, jt), *_ = job_traces(res, "4001")
    assert jt.move_options == () and jt.route == best(jt.candidates).route
    assert res.outcomes["4001"].met_deadline


def test_move_uses_plan_deadline_inside_the_window():
    # Nominal deadline is after the window, but the remote run would end after N × 15.
    res = run_headroom(make_inputs([job(arrival_slot=20)]), trace=True)
    assert res.moves == ()
    for _, jt in job_traces(res, "4001"):
        assert jt.move_options == ()


# --- A started job moves once and pays a Restart_Penalty

def test_started_job_moves_once_with_restart_penalty():
    # No idle capacity in hour 0, so at slot 1 the move is rejected and the job runs
    # on site; at slot 2 the remote run (slots 4-7) is all in hour 1 and fits.
    hours = [0.0, 1000.0, 1000.0, 1000.0, 1000.0, 1000.0]
    res = run_headroom(make_inputs([job()], idle(hours, hours)), trace=True)
    routes = [(d.route, d.target) for d in res.decisions]
    assert routes[-1] == (MOVE_REGION, "BPAT") and len(routes) == 2
    assert routes[0][0] in (GRID_NOW, STORAGE_NOW)
    assert res.decisions[-1].t == res.slots[2].t

    # 45 min left after one slot, + 5 min Restart_Penalty = 50 min over slots 4-7.
    (m,) = res.moves
    assert (m.decided_slot, m.start_slot) == (2, 4)
    assert m.kwh_by_slot == pytest.approx({4: SLOT_KWH, 5: SLOT_KWH, 6: SLOT_KWH,
                                           7: SLOT_KWH * 5 / c.SLOT_MINUTES})
    o = res.outcomes["4001"]
    assert o.resumes == 1 and o.finish_min == 7 * c.SLOT_MINUTES + 5 and o.met_deadline
    assert math.isclose(o.delivered_kwh, 300 * (60 + c.RESTART_PENALTY_MIN) / 60)
    # In transit during slots 2-3: the job runs nowhere.
    assert [r.training_kw > 0 for r in res.slots[:4]] == [False, True, False, False]
    assert all(r.moved_kw == 0.0 for r in res.slots[:4])
    fac_kwh = sum(r.training_kw + r.moved_kw for r in res.slots) * c.SLOT_MINUTES / 60
    assert math.isclose(fac_kwh, o.delivered_kwh * c.PUE)
    # After the move the job is never routed again (at most one move per job).
    assert [s for s, _ in job_traces(res, "4001")] == [1, 2]


# --- Flag off (Req 12.9, 24.8)

def test_flag_off_by_parameter_offers_no_move():
    res = run_headroom(make_inputs([job(), job("4002")]), trace=True, move_enabled=False)
    assert all(d.route != MOVE_REGION for d in res.decisions) and res.moves == ()
    assert all(r.moved_kw == 0.0 for r in res.slots)
    for st in res.trace.slots:
        for jt in st.jobs:
            assert jt.move_options == ()
            assert {x.route for x in jt.candidates} <= {GRID_NOW, STORAGE_NOW, DEFER}


def test_flag_off_in_config_is_read_at_call_time(monkeypatch):
    inp = make_inputs([job()])
    assert [d.route for d in run("headroom", inp).decisions] == [MOVE_REGION]
    monkeypatch.setattr(c, "ENABLE_MOVE_REGION", False)
    for res in (run("headroom", inp), run_headroom(inp)):
        assert all(d.route != MOVE_REGION for d in res.decisions) and res.moves == ()


# --- Real peak scenario

@pytest.fixture(scope="module")
def real_moves(real_inputs):  # noqa: F811
    return run_headroom(real_inputs, trace=True, move_enabled=True)


def test_real_scenario_moves_are_valid(real_inputs, real_moves):  # noqa: F811
    inp, res = real_inputs, real_moves
    assert res.moves, "expected at least one MOVE_REGION in the real peak scenario"
    jobs = {j.id: j for j in inp.jobs}
    assert len({m.job_id for m in res.moves}) == len(res.moves)          # one move per job
    move_decisions = [d for d in res.decisions if d.route == MOVE_REGION]
    assert [(d.job, d.target) for d in move_decisions] == [(m.job_id, m.region) for m in res.moves]
    used = {r: [0.0] * (inp.n_slots // c.SLOTS_PER_HOUR) for r in c.ALT_REGIONS}
    for m, d in zip(res.moves, move_decisions):
        assert m.region in c.ALT_REGIONS and d.t == inp.slots[m.decided_slot].t
        assert m.start_slot == m.decided_slot + c.MOVE_DELAY_SLOTS
        assert sorted(m.kwh_by_slot) == list(range(m.start_slot, m.start_slot + len(m.kwh_by_slot)))
        ci = round(inp.slots[m.start_slot].alt_carbon[m.region])
        assert f"→ MOVE_REGION to {m.region} ($75, +30 min, {ci} g/kWh) — " in d.reason
        assert len(d.reason) <= 200
        # A moved job is never routed again.
        assert not [x for x in res.decisions if x.job == m.job_id and x.t > d.t]
        o = res.outcomes[m.job_id]
        assert o.met_deadline and o.finish_min <= jobs[m.job_id].deadline_min
        for h in {s // c.SLOTS_PER_HOUR for s in m.kwh_by_slot}:
            used[m.region][h] += jobs[m.job_id].power_kw
    for r in c.ALT_REGIONS:
        for h, kw in enumerate(used[r]):
            assert kw <= inp.idle_capacity_kw[r][h] + 1e-6


def test_real_scenario_flag_off_has_zero_moves(real_inputs):  # noqa: F811
    res = run_headroom(real_inputs, move_enabled=False)
    assert res.moves == () and not any(d.route == MOVE_REGION for d in res.decisions)
    assert all(r.moved_kw == 0.0 for r in res.slots)
