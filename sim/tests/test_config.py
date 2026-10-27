"""Unit checks that config values match the requirements they encode."""

import math

from sim import config as c


def test_facility_and_load_split():
    # Req 10.2, 10.4
    assert c.IT_PEAK_KW * c.PUE == 13_000
    assert c.IT_PEAK_KW * c.INFERENCE_SHARE == 3_500
    assert c.IT_PEAK_KW * c.TRAINING_SHARE == 6_500


def test_derived_job_arrival_rate():
    mean_power = sum(c.JOB_POWER_KW) / 2
    mean_duration_h = sum(c.JOB_DURATION_MIN) / 2 / 60
    lam = c.IT_PEAK_KW * c.TRAINING_SHARE / (mean_power * mean_duration_h)
    assert math.isclose(lam, 2.353, abs_tol=0.001)


def test_storage_specs():
    # Req 10.11, 10.12, 10.13
    assert (c.BATTERY["power_kw"], c.BATTERY["capacity_kwh"], c.BATTERY["rte"]) == (4000, 16000, 0.90)
    assert (c.FLYWHEEL["power_kw"], c.FLYWHEEL["capacity_kwh"], c.FLYWHEEL["rte"]) == (2000, 500, 0.85)
    for spec in (c.BATTERY, c.FLYWHEEL):
        assert spec["soc_min_pct"] == 10 and spec["soc_max_pct"] == 95
        assert 10 <= spec["soc_init_pct"] <= 95
    assert c.DR_STORAGE_SUPPORT_KW <= c.FLYWHEEL["power_kw"]


def test_tariff_dr_and_move():
    # Req 10.16, 10.17, 10.18, 12.2, 14.1
    assert (c.TARIFF_OFFPEAK_PER_KWH, c.TARIFF_ONPEAK_PER_KWH) == (0.045, 0.085)
    assert c.ON_PEAK_HOURS_LOCAL == (15, 20)
    assert c.DEMAND_CHARGE_PER_KW == 18.0
    assert c.CARBON_SHADOW_USD_PER_T == 50.0
    assert (c.DR_SHED_KW, c.DR_DURATION_SLOTS, c.DR_BATTERY_RAMP_S) == (4000, 8, 20)
    assert c.MOVE_PENALTY_USD == 75.0


def test_fuel_codes_map_to_emission_factors():
    # Req 8.1
    assert set(c.FUEL_CODE_MAP.values()) <= set(c.EMISSION_FACTORS)
    assert c.EMISSION_FACTORS["Other"] == 300
