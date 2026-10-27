"""Example-based tests for sim.scheduler data models, shared job mechanics and
the baseline policy (Property 23 lives in test_sched_baseline.py)."""

import math
from datetime import datetime, timezone

import pytest

from sim import config as c
from sim.grid import GridSlot
from sim.jobs import TrainingJob, generate
from sim.scheduler import (
    GRID_NOW,
    Decision,
    JobState,
    ScenarioInputs,
    flag_late,
    job_outcome,
    run,
    run_baseline,
)
from sim.timeutil import slot_times

UTC = timezone.utc
START = datetime(2026, 7, 15, 6, 0, tzinfo=UTC)   # 00:00 MDT
INFERENCE_IT_KW = 2000.0


def make_inputs(jobs, n_slots=24, inference_it_kw=INFERENCE_IT_KW):
    times = slot_times(START, n_slots)
    slots = tuple(
        GridSlot(t=t, pace_demand_mw=5000.0, pace_forecast_mw=5000.0,
                 carbon_g_per_kwh=500.0, alt_carbon={"BPAT": 100.0, "CISO": 200.0},
                 price_per_kwh=c.TARIFF_OFFPEAK_PER_KWH, on_peak=False, stress=False)
        for t in times
    )
    n_hours = max(1, n_slots // c.SLOTS_PER_HOUR)
    return ScenarioInputs(
        name="test", slots=slots, jobs=tuple(jobs),
        inference_it_kw=tuple([inference_it_kw] * n_slots),
        idle_capacity_kw={r: tuple([0.0] * n_hours) for r in c.ALT_REGIONS},
        dr_start=0, dr_slots=c.DR_DURATION_SLOTS, requested_shed_kw=c.DR_SHED_KW,
        cap_kw=None, seed=c.SEED,
    )


def job(id="4001", arrival_slot=2, power_kw=300, duration_min=60, slack_min=360):
    return TrainingJob(id=id, arrival_slot=arrival_slot, power_kw=power_kw,
                       duration_min=duration_min, slack_min=slack_min)


# --- Baseline policy (Req 11.1, 11.2, 13.2)

def test_run_dispatches_and_returns_one_entry_per_slot():
    inp = make_inputs([job()])
    res = run("baseline", inp)
    assert res.policy == "baseline"
    assert len(res.slots) == inp.n_slots
    assert [r.t for r in res.slots] == [s.t for s in inp.slots]
    assert res.moves == ()


def test_job_runs_continuously_from_arrival():
    res = run_baseline(make_inputs([job(arrival_slot=2, power_kw=300, duration_min=60)]))
    training = [r.training_kw for r in res.slots]
    full = 300 * c.PUE
    assert training[:2] == [0.0, 0.0]
    assert all(math.isclose(kw, full) for kw in training[2:6])
    assert all(kw == 0.0 for kw in training[6:])


def test_partial_last_slot_uses_remaining_minutes():
    res = run_baseline(make_inputs([job(arrival_slot=1, power_kw=600, duration_min=65)]))
    training = [r.training_kw for r in res.slots]
    # 65 min = four full slots (1..4) then 5 minutes in slot 5
    assert math.isclose(training[5], 600 * c.PUE * 5 / c.SLOT_MINUTES)
    assert training[6] == 0.0
    o = res.outcomes["4001"]
    assert o.finish_min == 1 * c.SLOT_MINUTES + 65
    assert o.completed_in_window and o.met_deadline and not o.late


def test_delivered_energy_is_power_times_duration():
    res = run_baseline(make_inputs([job(power_kw=450, duration_min=95)]))
    o = res.outcomes["4001"]
    assert math.isclose(o.delivered_kwh, 450 * 95 / 60)
    assert o.resumes == 0
    total_fac_kwh = sum(r.training_kw * c.SLOT_MINUTES / 60 for r in res.slots)
    assert math.isclose(total_fac_kwh, 450 * 95 / 60 * c.PUE)


def test_facility_kw_is_it_times_pue_and_grid_is_the_sum():
    jobs = [job("4001", 0, 200, 120), job("4002", 3, 550, 75)]
    res = run_baseline(make_inputs(jobs))
    for s, r in enumerate(res.slots):
        it_training = sum(j.power_kw for j in jobs
                          if j.arrival_slot <= s < j.arrival_slot + math.ceil(j.duration_min / 15))
        assert math.isclose(r.inference_kw, INFERENCE_IT_KW * c.PUE)
        assert math.isclose(r.training_kw, it_training * c.PUE)
        assert math.isclose(r.grid_kw, r.inference_kw + r.training_kw)
        assert r.moved_kw == 0.0


def test_one_grid_now_decision_per_job_at_arrival():
    jobs = [job("4001", 0), job("4002", 5), job("4003", 5)]
    inp = make_inputs(jobs)
    res = run_baseline(inp)
    assert [(d.job, d.t, d.route, d.target) for d in res.decisions] == [
        ("4001", inp.slots[0].t, GRID_NOW, None),
        ("4002", inp.slots[5].t, GRID_NOW, None),
        ("4003", inp.slots[5].t, GRID_NOW, None),
    ]
    assert not any(d.late for d in res.decisions)


def test_storage_is_idle_and_soc_stays_at_initial_values():
    res = run_baseline(make_inputs([job()]))
    for r in res.slots:
        assert (r.battery.charge_kw, r.battery.discharge_kw) == (0.0, 0.0)
        assert (r.flywheel.charge_kw, r.flywheel.discharge_kw) == (0.0, 0.0)
        assert r.battery_kw == 0.0 and r.flywheel_kw == 0.0
        assert r.battery_soc_pct == c.BATTERY["soc_init_pct"]
        assert r.flywheel_soc_pct == c.FLYWHEEL["soc_init_pct"]


def test_unfinished_job_is_met_by_carry_over():
    n = 24   # 360 minutes
    j = job(arrival_slot=20, duration_min=120, slack_min=0)
    res = run_baseline(make_inputs([j], n_slots=n))
    o = res.outcomes["4001"]
    assert o.finish_min is None and not o.completed_in_window
    # Ran slots 20..23 (60 min), 60 min carried over: 360 + 60 == deadline
    assert math.isclose(o.delivered_kwh, j.power_kw * 60 / 60)
    assert n * c.SLOT_MINUTES + 60 == j.deadline_min
    assert o.met_deadline and not o.late


def test_generated_jobs_all_meet_deadlines_and_conserve_energy():
    n = c.WINDOW_HOURS * c.SLOTS_PER_HOUR
    jobs = generate(n, c.SEED, "peak")
    res = run_baseline(make_inputs(jobs, n_slots=n))
    assert len(res.decisions) == len(jobs) == len(res.outcomes)
    for j in jobs:
        o = res.outcomes[j.id]
        assert o.met_deadline and not o.late and o.resumes == 0
        ran_min = j.duration_min if o.completed_in_window else n * c.SLOT_MINUTES - j.arrival_min
        assert math.isclose(o.delivered_kwh, j.power_kw * min(ran_min, j.duration_min) / 60)


def test_run_dispatches_headroom_and_rejects_unknown_policy():
    inp = make_inputs([job()])
    res = run("headroom", inp)
    assert res.policy == "headroom" and len(res.slots) == inp.n_slots
    with pytest.raises(ValueError):
        run("bogus", inp)  # type: ignore[arg-type]


def test_inputs_reject_mismatched_lengths_and_out_of_window_jobs():
    inp = make_inputs([job()])
    with pytest.raises(ValueError):
        ScenarioInputs(**{**inp.__dict__, "inference_it_kw": (1.0,)})
    with pytest.raises(ValueError):
        ScenarioInputs(**{**inp.__dict__, "jobs": (job(arrival_slot=inp.n_slots),)})


# --- Shared job mechanics (Req 10.9, 10.10, 10.20)

def test_resume_after_pause_adds_restart_penalty():
    js = JobState.new(job(arrival_slot=0, duration_min=30), n_slots=24)
    js.run_slot(0)
    js.pause()
    assert js.paused and js.remaining_min == 15
    run_ = js.run_slot(2)          # resumes: 15 + 5 = 20 remaining, uses 15
    assert js.resumes == 1 and not js.paused
    assert run_.minutes == 15 and js.remaining_min == 5
    js.run_slot(3)
    assert js.finish_min == 3 * c.SLOT_MINUTES + 5
    assert math.isclose(js.delivered_kwh, 300 * (30 + c.RESTART_PENALTY_MIN) / 60)


def test_pause_before_first_run_has_no_penalty():
    js = JobState.new(job(arrival_slot=0, duration_min=15), n_slots=24)
    js.pause()
    assert not js.paused
    js.run_slot(4)
    assert js.resumes == 0 and js.finish_min == 4 * c.SLOT_MINUTES + 15


def test_run_slot_rejects_before_arrival_and_after_finish():
    js = JobState.new(job(arrival_slot=3, duration_min=15), n_slots=24)
    with pytest.raises(ValueError):
        js.run_slot(2)
    js.run_slot(3)
    with pytest.raises(ValueError):
        js.run_slot(4)


def test_plan_deadline_is_clamped_to_window_end():
    js = JobState.new(job(arrival_slot=20, duration_min=120, slack_min=360), n_slots=24)
    assert js.plan_deadline == 24 * c.SLOT_MINUTES
    js = JobState.new(job(arrival_slot=0, duration_min=60, slack_min=0), n_slots=24)
    assert js.plan_deadline == 60


def test_job_finishing_after_deadline_is_late():
    js = JobState.new(job(arrival_slot=0, duration_min=30, slack_min=0), n_slots=24)
    js.run_slot(0)
    js.pause()
    js.run_slot(2)
    js.run_slot(3)                 # finishes at 50 min > deadline 30
    o = job_outcome(js, n_slots=24)
    assert o.completed_in_window and o.finish_min == 50
    assert not o.met_deadline and o.late


def test_paused_job_at_window_end_counts_restart_penalty_in_carry_over():
    # Ran 15 of 60 min in slot 0, paused through the end of a 4-slot window.
    js = JobState.new(job(arrival_slot=0, duration_min=60, slack_min=4), n_slots=4)
    js.run_slot(0)
    js.pause()
    # 60 (window) + 45 remaining + 5 restart = 110 > deadline 64
    assert job_outcome(js, n_slots=4).late
    # Without the pause the same remaining work would need 60 + 45 = 105.
    js2 = JobState.new(job(arrival_slot=0, duration_min=60, slack_min=45), n_slots=4)
    js2.run_slot(0)
    js2.pause()
    assert job_outcome(js2, n_slots=4).late          # 110 > 105
    js3 = JobState.new(job(arrival_slot=0, duration_min=60, slack_min=50), n_slots=4)
    js3.run_slot(0)
    js3.pause()
    assert not job_outcome(js3, n_slots=4).late      # 110 <= 110


def test_flag_late_marks_only_the_last_decision_of_late_jobs():
    t0, t1 = slot_times(START, 2)
    decisions = [
        Decision(t=t0, job="4001", route="DEFER", target=None, reason="a", cause="COST"),
        Decision(t=t1, job="4001", route=GRID_NOW, target=None, reason="b", cause="DEADLINE"),
        Decision(t=t0, job="4002", route=GRID_NOW, target=None, reason="c", cause="COST"),
    ]
    late_js = JobState.new(job("4001", 0, duration_min=30, slack_min=0), n_slots=24)
    late_js.run_slot(0); late_js.pause(); late_js.run_slot(2); late_js.run_slot(3)
    ok_js = JobState.new(job("4002", 0, duration_min=15), n_slots=24)
    ok_js.run_slot(0)
    flagged = flag_late(decisions, {"4001": job_outcome(late_js, 24),
                                    "4002": job_outcome(ok_js, 24)})
    assert [d.late for d in flagged] == [False, True, False]
