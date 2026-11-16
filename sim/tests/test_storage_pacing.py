"""A bigger battery must never free less grid capacity in stress hours.

Before 2026-11-15, doubling the battery could halve ``headroom_kw``: storage spent
itself in the first slots of a high-value block (a larger power rating spent it
faster), and the forecast-stress threshold rose as the look-ahead shrank near the
window end, so the battery charged in hours that were really stressed
(DECISIONS.md, "Storage keeps energy for the whole block").
"""

from types import SimpleNamespace

import pytest

from sim import config as c
from sim import whatif
from sim.grid import stress_flags
from sim.scheduler import forecast_stress, run_headroom
from sim.tests.test_scheduler_storage_unit import dispatch, inputs, slots_for


def _df_slots(df):
    return [SimpleNamespace(pace_forecast_mw=v) for v in df]


def test_forecast_stress_does_not_drop_slots_as_the_horizon_shrinks():
    # 192 slots; the day-two evening has two high hours, 136-139 and a higher 140-143.
    n = 192
    df = [5000.0] * n
    for k in range(136, 140):
        df[k] = 9000.0
    for k in range(140, 144):
        df[k] = 9100.0
    slots = _df_slots(df)
    full_day = forecast_stress(slots, range(96, n))
    assert [96 + i for i, f in enumerate(full_day) if f] == list(range(136, 144))
    # 72 slots left: the top 5% of the horizon alone would be 140-143 only.
    late = forecast_stress(slots, range(120, n))
    assert [120 + i for i, f in enumerate(late) if f] == list(range(136, 144))


def test_forecast_stress_is_unchanged_while_the_horizon_is_a_full_day():
    df = [4000.0 + (i * 37) % 900 for i in range(192)]
    slots = _df_slots(df)
    for s in (0, 50, 96):
        h = range(s, s + c.LOOKAHEAD_SLOTS)
        assert forecast_stress(slots, h) == stress_flags([df[k] for k in h])


def test_battery_lasts_the_whole_stress_block():
    # An 8-hour forecast-stress block needs more energy than the battery holds at
    # the full stress shave, so the shave is spread evenly instead of stopping early.
    n, block = 48, range(8, 40)
    res = run_headroom(inputs([], slots_for(n, high_df=block)), trace=True)
    shave = [dispatch(res, s).stress_shave_kw for s in block]
    assert min(shave) > 0.0, "the battery ran out before the block ended"
    assert max(shave) - min(shave) < 1.0
    assert max(shave) < c.STRESS_DISCHARGE_SHARE * c.BATTERY["power_kw"]


@pytest.fixture(scope="module")
def data():
    from sim.run_scenarios import load_grid_data
    return load_grid_data()


@pytest.mark.parametrize("scenario", whatif.SCENARIOS)
@pytest.mark.parametrize("field", ["power_kw", "capacity_kwh"])
def test_doubling_the_battery_never_frees_less_capacity(scenario, field, data):
    base = whatif.current(scenario, data).values["headroom_kw"]
    key = f"battery_{field}"
    bigger = whatif.run(scenario, {key: 2 * c.BATTERY[field]}, data).values["headroom_kw"]
    assert bigger >= base
