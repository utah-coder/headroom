"""Cheapest-hour policy: the obvious fix, and why it fails (DECISIONS.md, "Cheapest hour").

Unit tests on a small synthetic window, then real-data checks: the gate table
from AGENT_BRIEF_EXPLAIN_AND_NAIVE.md (run 2026-10-28, seed 42) and the story
test. The story test must not be "fixed" by tuning the naive policy; if it
fails, stop and report (brief, Feature 2, Sim step 4).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sim.config import PUE, SLOT_MINUTES
from sim.grid import GridSlot
from sim.jobs import TrainingJob
from sim.metrics import compute_naive, policy_cost_usd, policy_emissions_g, site_peak_kw
from sim.run_scenarios import ScenarioBuild, build_scenario_full, load_grid_data
from sim.scheduler import (
    ScenarioInputs,
    naive_capped_missed,
    naive_starts,
    run_baseline,
    run_naive,
)
from sim.tests.conftest import block_network
from sim.timeutil import hhmm_denver

T0 = datetime(2026, 7, 14, 18, tzinfo=timezone.utc)
N = 16                       # 4 hours
INFERENCE_IT_KW = 1000.0


def make_inp(jobs, price=None, carbon=None) -> ScenarioInputs:
    price = price or [0.05] * N
    carbon = carbon or [400.0] * N
    slots = tuple(
        GridSlot(t=T0 + timedelta(minutes=SLOT_MINUTES * i), pace_demand_mw=5000.0 + i,
                 pace_forecast_mw=5000.0, carbon_g_per_kwh=carbon[i],
                 alt_carbon={"BPAT": 90.0, "CISO": 210.0}, price_per_kwh=price[i],
                 on_peak=False, stress=i == N - 1)
        for i in range(N)
    )
    return ScenarioInputs(
        name="peak", slots=slots, jobs=tuple(jobs), inference_it_kw=(INFERENCE_IT_KW,) * N,
        idle_capacity_kw={"BPAT": (0.0,) * 4, "CISO": (0.0,) * 4},
        dr_start=0, dr_slots=0, requested_shed_kw=1.0, cap_kw=None, seed=1,
    )


def dip(at: set[int], cheap=0.01, dear=0.10) -> list[float]:
    return [cheap if i in at else dear for i in range(N)]


# --- Unit: where each job starts -------------------------------------------------

def test_picks_its_cheapest_start_before_the_deadline():
    # 30-minute job, arrival 0, deadline 4 h; slots 6 and 7 are cheap.
    inp = make_inp([TrainingJob("4001", 0, 100, 30, 210)], price=dip({6, 7}))
    assert naive_starts(inp) == {"4001": 6}


def test_carbon_counts_at_the_shadow_price():
    # Same price everywhere; slot 9–10 are cleaner, so the job waits for them.
    carbon = [100.0 if i in (9, 10) else 800.0 for i in range(N)]
    inp = make_inp([TrainingJob("4001", 0, 100, 30, 210)], carbon=carbon)
    assert naive_starts(inp) == {"4001": 9}


def test_ties_go_to_the_earliest_start():
    inp = make_inp([TrainingJob("4001", 3, 100, 30, 120)])
    assert naive_starts(inp) == {"4001": 3}


def test_a_start_that_would_finish_after_the_deadline_is_not_used():
    # Cheap slots 12–13, but the deadline is 2 h (slot 8) after arrival 0.
    inp = make_inp([TrainingJob("4001", 0, 100, 30, 90)], price=dip({12, 13}))
    assert naive_starts(inp)["4001"] < 8


def test_a_job_that_cannot_finish_in_the_window_runs_on_arrival():
    # 2-hour job arriving 1 h before the window ends: no start finishes inside it.
    inp = make_inp([TrainingJob("4001", 12, 100, 120, 600)], price=dip({13, 14}))
    assert naive_starts(inp) == {"4001": 12}


def test_run_naive_runs_each_job_continuously_and_leaves_inference_alone():
    inp = make_inp([TrainingJob("4001", 0, 100, 30, 210)], price=dip({6, 7}))
    res = run_naive(inp)
    assert res.policy == "naive" and res.decisions == () and res.moves == ()
    training = [r.training_kw for r in res.slots]
    assert training == [100 * PUE if i in (6, 7) else 0.0 for i in range(N)]
    assert all(r.inference_kw == f for r, f in zip(res.slots, inp.inference_fac))
    assert all(r.battery_kw == 0 and r.flywheel_kw == 0 and r.moved_kw == 0 for r in res.slots)
    assert res.outcomes["4001"].met_deadline


def test_jobs_pile_into_the_same_cheap_slot():
    jobs = [TrainingJob(str(4001 + i), i, 100, 30, 210) for i in range(3)]
    res = run_naive(make_inp(jobs, price=dip({8, 9})))
    assert max(r.training_kw for r in res.slots) == pytest.approx(3 * 100 * PUE)


# --- Unit: held to a cap -------------------------------------------------------------

def test_capped_first_come_takes_the_cheap_slot_and_the_next_waits():
    cap = INFERENCE_IT_KW * PUE + 100 * PUE           # room for one job at a time
    jobs = [TrainingJob("4001", 0, 100, 30, 210), TrainingJob("4002", 1, 100, 30, 195)]
    assert naive_capped_missed(make_inp(jobs, price=dip({8, 9})), cap) == (0, 2)


def test_capped_job_with_no_on_time_room_is_late():
    cap = INFERENCE_IT_KW * PUE + 100 * PUE
    # Both must finish by slot 4 (60 min); one 60-minute run fills it.
    jobs = [TrainingJob("4001", 0, 100, 60, 0), TrainingJob("4002", 0, 100, 60, 0)]
    assert naive_capped_missed(make_inp(jobs), cap) == (1, 2)


def test_capped_job_that_never_fits_is_late():
    cap = INFERENCE_IT_KW * PUE + 50 * PUE            # no room for a 100 kW job anywhere
    assert naive_capped_missed(make_inp([TrainingJob("4001", 0, 100, 30, 210)]), cap) == (1, 1)


def test_capped_on_time_start_may_carry_past_the_window():
    # Deadline is past the window end, so a start whose run carries past the end is on time.
    cap = INFERENCE_IT_KW * PUE + 100 * PUE
    jobs = [TrainingJob("4001", 14, 100, 60, 600)]
    assert naive_capped_missed(make_inp(jobs), cap) == (0, 1)


def test_compute_naive_counts_and_cap():
    jobs = [TrainingJob(str(4001 + i), i, 100, 30, 210) for i in range(3)]
    inp = make_inp(jobs, price=dip({8, 9}))
    base = run_baseline(inp)
    m = compute_naive(inp, base, run_naive(inp))
    assert m.cap_kw == site_peak_kw(base)
    assert (m.peak_jobs_running, m.baseline_jobs_running, m.peak_hour_starts) == (3, 0, 3)
    assert m.deadlines_met_pct == 100.0
    assert m.usd_saved == pytest.approx(policy_cost_usd(inp, base, include_moves=False)
                                        - policy_cost_usd(inp, run_naive(inp), include_moves=False))


# --- Real data -------------------------------------------------------------------------

SCENARIOS = ("peak", "sun")

# The brief's gate table (2026-10-28, seed 42, current cache): site window peak,
# its Denver time, stress-hour peak, cost (energy + demand) and CO2 in t, all
# rounded as in the brief; and the capped deadline misses.
GATE = {
    "peak": dict(site=34_396, at="11:00", stress=4_703, cost=647_087, co2=275.7, missed=(29, 119)),
    "sun": dict(site=30_907, at="12:30", stress=17_511, cost=579_716, co2=146.4, missed=(20, 99)),
}


@pytest.fixture(scope="module")
def builds() -> dict[str, ScenarioBuild]:
    with pytest.MonkeyPatch.context() as mp:
        block_network(mp)
        data = load_grid_data()
        return {name: build_scenario_full(name, data, built_at="2026-10-28T00:00:00Z")
                for name in SCENARIOS}


@pytest.mark.parametrize("name", SCENARIOS)
def test_reproduces_the_gate_table(name, builds):
    b, want = builds[name], GATE[name]
    nm = b.naive_metrics
    grid = [r.grid_kw for r in b.naive.slots]
    assert round(nm.site_peak_kw) == want["site"]
    assert hhmm_denver(b.inp.slots[grid.index(max(grid))].t) == want["at"]
    assert round(nm.stress_peak_kw) == want["stress"]
    assert round(policy_cost_usd(b.inp, b.naive, include_moves=False)) == want["cost"]
    assert round(policy_emissions_g(b.inp, b.naive, include_moves=False) / 1e6, 1) == want["co2"]
    assert (nm.capped_missed, nm.capped_total) == want["missed"]


@pytest.mark.parametrize("name", SCENARIOS)
def test_story_naive_peak_above_baseline_above_headroom(name, builds):
    # Do not tune the naive policy to make this pass (brief, Feature 2, Sim step 4).
    m, nm = builds[name].metrics, builds[name].naive_metrics
    assert nm.site_peak_kw > m.baseline_site_peak_kw > m.headroom_site_peak_kw


@pytest.mark.parametrize("name", SCENARIOS)
def test_naive_keeps_inference_and_every_deadline(name, builds):
    b = builds[name]
    assert all(r.inference_kw == f for r, f in zip(b.naive.slots, b.inp.inference_fac))
    assert b.naive_metrics.deadlines_met_pct == 100.0
    assert b.naive_metrics.cap_kw == b.metrics.baseline_site_peak_kw
    assert b.naive_metrics.peak_jobs_running > b.naive_metrics.baseline_jobs_running


@pytest.mark.parametrize("name", SCENARIOS)
def test_written_naive_series_matches_the_run(name, builds):
    b = builds[name]
    assert [r["grid_kw"] for r in b.doc["naive"]] == [round(r.grid_kw, 1) for r in b.naive.slots]
    assert b.doc["naive_metrics"]["usd_saved"] < 0             # it costs more than baseline
