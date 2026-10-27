"""Unit tests for inference load, storage and idle capacity in sim.site
(example-based; Properties 14 and 17 live in test_site_inference.py and
test_site_storage.py)."""

import math
from datetime import datetime, timezone

import pytest

from sim import config as c
from sim.site import StorageSpec, StorageState, idle_capacity_kw, inference_it_kw
from sim.timeutil import slot_times

UTC = timezone.utc
SLOT_H = c.SLOT_MINUTES / 60


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# --- Inference profile (Req 10.4, 10.5)

def test_inference_peaks_at_3500_kw_at_1800_denver():
    # 2026-07-15 18:00 MDT == 2026-07-16 00:00 UTC
    assert inference_it_kw([utc(2026, 7, 16, 0, 0)]) == [3500.0]


def test_inference_floor_is_60_percent_at_0600_denver():
    # 06:00 MDT == 12:00 UTC
    (kw,) = inference_it_kw([utc(2026, 7, 15, 12, 0)])
    assert math.isclose(kw, 2100.0, abs_tol=1e-9)


def test_inference_two_days_bounds_peak_hour_and_period():
    times = slot_times(utc(2026, 7, 15, 6, 0), 2 * 96)  # 00:00 MDT, 48 h
    kw = inference_it_kw(times)
    assert max(kw) == 3500.0
    assert all(2100.0 - 1e-9 <= v <= 3500.0 for v in kw)
    # Daily maximum at 18:00 local on both days, and the profile repeats every 24 h.
    peaks = [i for i, v in enumerate(kw) if v == 3500.0]
    assert peaks == [18 * 4, 96 + 18 * 4]
    assert kw[:96] == kw[96:]


def test_inference_follows_denver_time_in_winter():
    # 18:00 MST == 01:00 UTC next day
    assert inference_it_kw([utc(2026, 1, 15, 1, 0)]) == [3500.0]


def test_inference_rejects_naive_datetime():
    with pytest.raises(ValueError):
        inference_it_kw([datetime(2026, 7, 15, 18, 0)])


# --- Storage (Req 10.11 - 10.14)

BATTERY = StorageSpec.from_config("battery", c.BATTERY)
FLYWHEEL = StorageSpec.from_config("flywheel", c.FLYWHEEL)


def test_spec_from_config_and_eta():
    assert BATTERY.power_kw == 4000 and BATTERY.capacity_kwh == 16000
    assert math.isclose(BATTERY.eta ** 2, 0.90)
    assert math.isclose(FLYWHEEL.eta ** 2, 0.85)
    assert BATTERY.initial_state().soc_pct(BATTERY) == c.BATTERY["soc_init_pct"]
    assert FLYWHEEL.initial_state().soc_pct(FLYWHEEL) == c.FLYWHEEL["soc_init_pct"]


@pytest.mark.parametrize("spec", [BATTERY, FLYWHEEL], ids=["battery", "flywheel"])
def test_full_cycle_returns_rte_of_energy_drawn(spec):
    state = StorageState(spec.soc_min_kwh)
    drawn = 0.0
    while (kw := state.max_charge_kw(spec, SLOT_H)) > 0:
        state = state.step(spec, kw, 0, SLOT_H)
        drawn += kw * SLOT_H
    assert math.isclose(state.soc_kwh, spec.soc_max_kwh)
    delivered = 0.0
    while (kw := state.max_discharge_kw(spec, SLOT_H)) > 0:
        state = state.step(spec, 0, kw, SLOT_H)
        delivered += kw * SLOT_H
    assert math.isclose(state.soc_kwh, spec.soc_min_kwh)
    assert math.isclose(delivered, spec.rte * drawn, rel_tol=1e-9)


def test_max_rates_capped_by_power_rating():
    state = BATTERY.initial_state()  # 60 % of 16,000 kWh: far from both bounds
    assert BATTERY.power_kw == state.max_discharge_kw(BATTERY, SLOT_H)
    assert BATTERY.power_kw == state.max_charge_kw(BATTERY, SLOT_H)


def test_max_rates_honour_floor_and_ceiling_arguments():
    state = StorageState(2000.0)  # battery floor is 1,600 kWh, ceiling 15,200 kWh
    # Custom floor 1,900 kWh: 100 kWh of stored energy usable, delivered at eta.
    assert math.isclose(state.max_discharge_kw(BATTERY, 1.0, floor_kwh=1900.0), 100 * BATTERY.eta)
    # A floor below the 10 % SOC floor is clamped up to it.
    flywheel = StorageState(FLYWHEEL.soc_min_kwh + 10)
    assert math.isclose(flywheel.max_discharge_kw(FLYWHEEL, 1.0, floor_kwh=0.0), 10 * FLYWHEEL.eta)
    # Custom ceiling 2,100 kWh: 100 kWh stored needs 100 / eta drawn.
    assert math.isclose(state.max_charge_kw(BATTERY, 1.0, ceil_kwh=2100.0), 100 / BATTERY.eta)
    # At the bound, nothing is available.
    assert StorageState(FLYWHEEL.soc_min_kwh).max_discharge_kw(FLYWHEEL, SLOT_H) == 0.0
    assert StorageState(FLYWHEEL.soc_max_kwh).max_charge_kw(FLYWHEEL, SLOT_H) == 0.0


def test_step_rejects_simultaneous_charge_and_discharge():
    with pytest.raises(ValueError, match="same step"):
        BATTERY.initial_state().step(BATTERY, 100, 100, SLOT_H)


def test_step_idle_keeps_soc():
    state = BATTERY.initial_state()
    assert state.step(BATTERY, 0, 0, SLOT_H) == state


@pytest.mark.parametrize(
    ("soc_kwh", "charge_kw", "discharge_kw"),
    [
        (FLYWHEEL.soc_min_kwh + 1, 0, 2000),   # discharge below 10 %
        (FLYWHEEL.soc_max_kwh - 1, 2000, 0),   # charge above 95 %
        (8000.0, 4001, 0),                     # above the power rating
        (8000.0, -1, 0),                       # negative power
    ],
)
def test_step_rejects_out_of_bounds(soc_kwh, charge_kw, discharge_kw):
    spec = FLYWHEEL if soc_kwh < 1000 else BATTERY
    with pytest.raises(ValueError):
        StorageState(soc_kwh).step(spec, charge_kw, discharge_kw, SLOT_H)


# --- Idle capacity (Req 12.4)

def test_idle_capacity_shape_range_and_integer_percent():
    idle = idle_capacity_kw(48, c.SEED, "peak")
    assert list(idle) == list(c.ALT_REGIONS)
    lo, hi = c.IDLE_CAPACITY_PCT
    for values in idle.values():
        assert len(values) == 48
        for kw in values:
            pct = kw * 100 / c.IT_PEAK_KW
            assert pct == int(pct) and lo <= pct <= hi


def test_idle_capacity_is_deterministic_per_seed_and_scenario():
    a = idle_capacity_kw(48, c.SEED, "peak")
    assert a == idle_capacity_kw(48, c.SEED, "peak")
    assert a != idle_capacity_kw(48, c.SEED, "sun")
    assert a != idle_capacity_kw(48, c.SEED + 1, "peak")
    assert a["BPAT"] != a["CISO"]


def test_idle_capacity_zero_hours():
    assert idle_capacity_kw(0, c.SEED, "peak") == {r: [] for r in c.ALT_REGIONS}
