"""Example tests for the Event_Zoom builder (Requirements 10.13–10.15, 14.5–14.8, 14.11)."""

import pytest

from sim.config import BATTERY, DR_BATTERY_RAMP_S, DR_ZOOM_SECONDS, FLYWHEEL
from sim.site import (
    DrInfeasibleError,
    StorageSpec,
    StorageState,
    ZoomEntry,
    build_event_zoom,
)

BAT = StorageSpec.from_config("battery", BATTERY)
FLY = StorageSpec.from_config("flywheel", FLYWHEEL)


def test_600_entries_with_consecutive_seconds():
    zoom = build_event_zoom(5000.0, 3500.0)
    assert len(zoom) == DR_ZOOM_SECONDS == 600
    assert [e.s for e in zoom] == list(range(600))
    assert all(isinstance(e, ZoomEntry) for e in zoom)


def test_battery_ramp_and_flywheel_step():
    # R = 1500 kW: battery 0 → 1500 over 20 s in 75 kW steps, flywheel takes the rest.
    zoom = build_event_zoom(5000.0, 3500.0)
    assert (zoom[0].battery_kw, zoom[0].flywheel_kw) == (0.0, 1500.0)
    for e in zoom[: DR_BATTERY_RAMP_S + 1]:
        assert e.battery_kw == pytest.approx(75.0 * e.s)
    assert zoom[10].flywheel_kw == pytest.approx(750.0)
    for e in zoom[DR_BATTERY_RAMP_S:]:
        assert (e.battery_kw, e.flywheel_kw) == (1500.0, 0.0)
    # dr_seconds_to_full is the first s with flywheel 0 and battery at R.
    first_full = next(e.s for e in zoom if e.flywheel_kw == 0 and e.battery_kw == 1500.0)
    assert first_full == DR_BATTERY_RAMP_S


def test_storage_sums_to_required_and_grid_at_cap():
    load, cap = 5000.0, 3500.0
    for e in build_event_zoom(load, cap):
        assert round(e.flywheel_kw + e.battery_kw, 1) == 1500.0
        assert e.grid_kw == cap
        assert round(e.grid_kw + e.flywheel_kw + e.battery_kw, 1) == load
        assert 0 <= e.flywheel_kw <= FLY.power_kw and 0 <= e.battery_kw <= BAT.power_kw


def test_grid_never_exceeds_unrounded_cap():
    # cap with more precision than the output: grid is rounded down to stay ≤ cap.
    load, cap = 5000.0, 3500.06
    zoom = build_event_zoom(load, cap)
    assert all(e.grid_kw <= cap for e in zoom)
    assert zoom[0].grid_kw == 3500.0
    assert all(round(e.grid_kw + e.flywheel_kw + e.battery_kw, 1) == load for e in zoom)


@pytest.mark.parametrize("load, cap", [(3000.0, 3500.0), (3500.0, 3500.0), (0.0, 0.0)])
def test_no_storage_needed_when_load_at_or_below_cap(load, cap):
    for e in build_event_zoom(load, cap):
        assert (e.grid_kw, e.flywheel_kw, e.battery_kw) == (load, 0.0, 0.0)


def test_flywheel_at_exact_rating_is_feasible():
    zoom = build_event_zoom(5500.0, 3500.0)  # R = 2000 kW = flywheel rating
    assert zoom[0].flywheel_kw == FLY.power_kw


@pytest.mark.parametrize("load", [5500.1, 8000.0])
def test_over_rating_raises_at_second_zero(load):
    with pytest.raises(DrInfeasibleError) as info:
        build_event_zoom(load, 3500.0)
    assert info.value.second == 0
    assert "flywheel" in str(info.value) and "second 0" in str(info.value)


def test_flywheel_soc_exit_names_the_second():
    # 1 kWh above the floor at R = 2000 kW: second 0 drains ~0.60 kWh, second 1 ~0.57 kWh.
    start = StorageState(soc_kwh=FLY.soc_min_kwh + 1.0)
    with pytest.raises(DrInfeasibleError) as info:
        build_event_zoom(5500.0, 3500.0, flywheel_state=start)
    assert info.value.second == 1
    assert "flywheel" in info.value.detail


def test_battery_soc_exit_names_the_second():
    # 1 kWh above the floor at R = 1500 kW: cumulative ramp drain first exceeds 1 kWh at s = 10.
    start = StorageState(soc_kwh=BAT.soc_min_kwh + 1.0)
    with pytest.raises(DrInfeasibleError) as info:
        build_event_zoom(5000.0, 3500.0, battery_state=start)
    assert info.value.second == 10
    assert "battery" in info.value.detail


def test_starting_soc_above_ceiling_raises():
    start = StorageState(soc_kwh=BAT.capacity_kwh * 0.96)
    with pytest.raises(DrInfeasibleError) as info:
        build_event_zoom(3000.0, 3500.0, battery_state=start)
    assert info.value.second == 0


def test_negative_cap_is_infeasible():
    with pytest.raises(DrInfeasibleError) as info:
        build_event_zoom(1000.0, -10.0)
    assert info.value.second == 0


def test_negative_load_is_rejected():
    with pytest.raises(ValueError):
        build_event_zoom(-1.0, 3500.0)


def test_error_message_carries_scenario_and_slot():
    err = DrInfeasibleError(slot=5, scenario="peak", detail="load too high")
    assert str(err) == "DR event infeasible at scenario 'peak', slot 5: load too high"
    assert err.second is None and err.slot == 5
