"""Example-based tests for sim.metrics with hand-computed expected values.

Property 34 (reference formulas over generated inputs) lives in test_metrics.py.
The fixtures are hand-built PolicyResults over a 4-slot window, so the
arithmetic below can be checked by hand. Tariff and penalty values come from
sim/config.py (pinned by test_config.py): demand charge $18/kW, move penalty
$75, Requested_Shed 4,000 kW, slot length 0.25 h.
"""

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from sim import config as c
from sim.grid import GridSlot
from sim.jobs import TrainingJob
from sim.metrics import METRIC_KEYS, Metrics, clamp_pct, compute, dr_seconds_to_full
from sim.scheduler import (
    JobOutcome,
    MoveRecord,
    PolicyResult,
    ScenarioInputs,
    SlotResult,
    StorageFlows,
)
from sim.site import ZoomEntry, build_event_zoom
from sim.timeutil import slot_times

START = datetime(2026, 7, 15, 20, 0, tzinfo=timezone.utc)
INFERENCE_IT_KW = 1000.0

# Per slot: (price $/kWh, PACE g/kWh, BPAT g/kWh, CISO g/kWh, stress)
SLOT_SPEC = [
    (0.045, 400.0, 100.0, 200.0, False),
    (0.085, 500.0, 120.0, 220.0, True),
    (0.085, 500.0, 120.0, 220.0, True),
    (0.045, 300.0, 90.0, 180.0, False),
]
BASE_GRID = [5000.0, 9000.0, 8000.0, 6000.0]


@dataclass(frozen=True)
class Dr:
    """The DR fields compute() reads (design DrResult subset)."""

    requested_shed_kw: float
    required_kw: float
    zoom: tuple[ZoomEntry, ...]


def make_inputs() -> ScenarioInputs:
    times = slot_times(START, len(SLOT_SPEC))
    slots = tuple(
        GridSlot(t=t, pace_demand_mw=5000.0, pace_forecast_mw=5000.0,
                 carbon_g_per_kwh=ci, alt_carbon={"BPAT": bpat, "CISO": ciso},
                 price_per_kwh=price, on_peak=price == c.TARIFF_ONPEAK_PER_KWH, stress=stress)
        for t, (price, ci, bpat, ciso, stress) in zip(times, SLOT_SPEC)
    )
    jobs = tuple(TrainingJob(id=str(4001 + k), arrival_slot=0, power_kw=300,
                             duration_min=60, slack_min=360) for k in range(3))
    return ScenarioInputs(
        name="test", slots=slots, jobs=jobs,
        inference_it_kw=(INFERENCE_IT_KW,) * len(slots),
        idle_capacity_kw={r: (0.0,) for r in c.ALT_REGIONS},
        dr_start=1, dr_slots=2, requested_shed_kw=c.DR_SHED_KW, cap_kw=5000.0, seed=c.SEED,
    )


def make_result(inp, grid, *, policy="headroom", inference=None, met=None, moves=()):
    idle = StorageFlows(charge_kw=0.0, discharge_kw=0.0)
    inference = list(inp.inference_fac) if inference is None else inference
    met = [True] * len(inp.jobs) if met is None else met
    slots = tuple(
        SlotResult(t=g.t, grid_kw=kw, inference_kw=inf, training_kw=kw - inf,
                   battery=idle, flywheel=idle, battery_soc_pct=60.0,
                   flywheel_soc_pct=90.0, moved_kw=0.0)
        for g, kw, inf in zip(inp.slots, grid, inference)
    )
    outcomes = {
        j.id: JobOutcome(job_id=j.id, finish_min=60, delivered_kwh=300.0, resumes=0,
                         completed_in_window=True, met_deadline=ok, late=not ok)
        for j, ok in zip(inp.jobs, met)
    }
    return PolicyResult(policy=policy, slots=slots, decisions=(), outcomes=outcomes,
                        moves=tuple(moves))


def make_dr(site_load_kw=7000.0, cap_kw=5500.0) -> Dr:
    zoom = tuple(build_event_zoom(site_load_kw, cap_kw))
    return Dr(requested_shed_kw=c.DR_SHED_KW, required_kw=max(0.0, site_load_kw - cap_kw),
              zoom=zoom)


@pytest.fixture
def inp():
    return make_inputs()


@pytest.fixture
def base(inp):
    return make_result(inp, BASE_GRID, policy="baseline")


def test_headroom_better_than_baseline(inp, base):
    move = MoveRecord(job_id="4002", region="BPAT", decided_slot=0, start_slot=2,
                      kwh_by_slot={2: 250.0, 3: 250.0})
    head = make_result(inp, [6000.0, 5000.0, 5500.0, 6000.0],
                       met=[True, True, False], moves=[move])
    m = compute(inp, base, head, make_dr())

    # Stress slots 1, 2: max(9000, 8000) − max(5000, 5500)
    assert m.headroom_kw == pytest.approx(3500.0)
    # Base: 0.25 × (225 + 765 + 680 + 270) + 9000 × 18 = 485 + 162000 = 162485
    # Head: 0.25 × (270 + 425 + 467.5 + 270) + 6000 × 18 + 75 = 108433.125
    assert m.usd_saved == pytest.approx(54051.875)
    # Base: 0.25 × (5000·400 + 9000·500 + 8000·500 + 6000·300) = 3,075,000 g
    # Head: 0.25 × (6000·400 + 5000·500 + 5500·500 + 6000·300) = 2,362,500 g
    #       + moved 250·120 + 250·90 = 52,500 g → 2,415,000 g
    assert m.co2_avoided_t == pytest.approx(0.66)
    assert m.deadlines_met_pct == pytest.approx(200 / 3)
    assert m.inference_sla_pct == 100.0
    # 100 × (9000 − max(5000, 5500)) / 4000
    assert m.dr_delivered_raw_pct == pytest.approx(87.5)
    assert m.dr_delivered_pct == 87.5
    # R = 7000 − 5500 = 1500 kW > 0: the battery reaches R at the end of its 20 s ramp
    assert m.dr_seconds_to_full == c.DR_BATTERY_RAMP_S


def test_negative_values_reported_unchanged(inp, base):
    head = make_result(inp, [5000.0, 9500.0, 9000.0, 6000.0])
    m = compute(inp, base, head, make_dr())

    assert m.headroom_kw == pytest.approx(-500.0)
    # Head: 0.25 × (225 + 807.5 + 765 + 270) + 9500 × 18 = 171516.875
    assert m.usd_saved == pytest.approx(-9031.875)
    # Head: 0.25 × (5000·400 + 9500·500 + 9000·500 + 6000·300) = 3,262,500 g
    assert m.co2_avoided_t == pytest.approx(-0.1875)
    # Raw DR is 100 × (9000 − 9500) / 4000 = −12.5; the reported value clamps to 0
    assert m.dr_delivered_raw_pct == pytest.approx(-12.5)
    assert m.dr_delivered_pct == 0.0


def test_dr_delivered_clamps_above_100(inp, base):
    head = make_result(inp, [6000.0, 4000.0, 4500.0, 6000.0])
    m = compute(inp, base, head, make_dr())
    assert m.dr_delivered_raw_pct == pytest.approx(112.5)
    assert m.dr_delivered_pct == 100.0


def test_dr_delivered_rounds_to_one_decimal(inp, base):
    head = make_result(inp, [6000.0, 7654.321, 5000.0, 6000.0])
    m = compute(inp, base, head, make_dr())
    # 100 × (9000 − 7654.321) / 4000 = 33.641975
    assert m.dr_delivered_raw_pct == pytest.approx(33.641975)
    assert m.dr_delivered_pct == 33.6


def test_inference_sla_counts_matching_slots(inp, base):
    inference = list(inp.inference_fac)
    inference[3] -= 1.0
    head = make_result(inp, BASE_GRID, inference=inference)
    assert compute(inp, base, head, make_dr()).inference_sla_pct == 75.0


def test_identical_policies_give_zero_savings(inp, base):
    head = make_result(inp, BASE_GRID)
    m = compute(inp, base, head, make_dr())
    assert (m.headroom_kw, m.usd_saved, m.co2_avoided_t) == (0.0, 0.0, 0.0)
    assert m.deadlines_met_pct == 100.0


def test_seconds_to_full_is_zero_without_required_storage():
    dr = make_dr(site_load_kw=5000.0, cap_kw=5500.0)
    assert dr_seconds_to_full(dr.zoom, dr.required_kw) == 0


def test_seconds_to_full_rejects_zoom_that_never_reaches_full():
    zoom = [ZoomEntry(s=s, grid_kw=5000.0, flywheel_kw=100.0, battery_kw=0.0) for s in range(3)]
    with pytest.raises(ValueError):
        dr_seconds_to_full(zoom, 100.0)


def test_clamp_pct_bounds():
    assert (clamp_pct(-0.04), clamp_pct(55.54), clamp_pct(100.04)) == (0.0, 55.5, 100.0)


def test_slot_count_mismatch_rejected(inp, base):
    head = make_result(inp, BASE_GRID[:3])
    with pytest.raises(ValueError):
        compute(inp, base, head, make_dr())


def test_to_dict_has_contract_keys_in_order(inp, base):
    m = compute(inp, base, make_result(inp, BASE_GRID), make_dr())
    assert isinstance(m, Metrics)
    assert tuple(m.to_dict()) == METRIC_KEYS
