"""Example-based unit tests for sim.carbon (Requirement 8).

Property tests for weighted intensity, bounds and unknown codes live in
test_carbon_weighted.py, test_carbon_bounds.py and test_carbon_unknown.py.
"""

from datetime import datetime, timezone

import pytest

from sim.carbon import (
    LogEntry,
    NoFuelMixError,
    hourly_intensity,
    intensity_hour,
    map_fuel,
)
from sim.config import EMISSION_FACTORS
from sim.grid import NoDataError

UTC = timezone.utc
START = datetime(2026, 6, 1, 0, tzinfo=UTC)
N_HOURS = 122 * 24


def fuel_row(period, fuel, value, respondent="PACE"):
    return {"period": period, "respondent": respondent, "fueltype": fuel,
            "value": value, "value-units": "megawatthours"}


@pytest.mark.parametrize("code,name", [
    ("COL", "Coal"), ("NG", "Natural gas"), ("NUC", "Nuclear"), ("OIL", "Oil"),
    ("OTH", "Other"), ("SUN", "Solar"), ("WAT", "Hydro"), ("WND", "Wind"),
])
def test_map_fuel_known_codes(code, name):
    assert map_fuel(code) == name


@pytest.mark.parametrize("code", ["GEO", "BAT", "PS", "UNK", "", "col"])
def test_map_fuel_unknown_codes_map_to_other(code):
    assert map_fuel(code) == "Other"


def test_intensity_hour_weighted_average():
    ci = intensity_hour({"COL": 100.0, "WND": 300.0})
    expected = (100 * EMISSION_FACTORS["Coal"] + 300 * EMISSION_FACTORS["Wind"]) / 400
    assert ci == pytest.approx(expected)


def test_intensity_hour_negative_and_missing_count_as_zero():
    base = intensity_hour({"NG": 50.0, "SUN": 150.0})
    assert intensity_hour({"NG": 50.0, "SUN": 150.0, "WAT": -20.0, "COL": None}) == base


def test_intensity_hour_unknown_code_uses_other_factor():
    assert intensity_hour({"GEO": 10.0}) == pytest.approx(EMISSION_FACTORS["Other"])


@pytest.mark.parametrize("mix", [{}, {"COL": 0.0}, {"COL": -5.0, "NG": None}])
def test_intensity_hour_zero_total_returns_none(mix):
    assert intensity_hour(mix) is None


def test_hourly_intensity_full_range_and_name():
    rows = [fuel_row("2026-06-01T00", "COL", 100), fuel_row("2026-06-01T00", "SUN", "100")]
    series, entries = hourly_intensity(rows, "PACE")
    assert series.name == "PACE.CI"
    assert series.start == START
    assert len(series) == N_HOURS
    expected = (EMISSION_FACTORS["Coal"] + EMISSION_FACTORS["Solar"]) / 2
    assert series.values[0] == pytest.approx(expected)
    # every later hour has no rows -> filled with the nearest value (trailing edge)
    assert all(v == pytest.approx(expected) for v in series.values)
    assert sum(e.kind == "filled_hour" for e in entries) == N_HOURS - 1


def test_hourly_intensity_interpolates_zero_hour_and_logs_it():
    rows = [
        fuel_row("2026-06-01T00", "COL", 10),    # 820
        fuel_row("2026-06-01T01", "COL", 0),     # zero total -> filled
        fuel_row("2026-06-01T01", "WND", -3),
        fuel_row("2026-06-01T02", "NUC", 10),    # 12
    ]
    series, entries = hourly_intensity(rows, "PACE")
    coal, nuc = EMISSION_FACTORS["Coal"], EMISSION_FACTORS["Nuclear"]
    assert series.values[0] == pytest.approx(coal)
    assert series.values[1] == pytest.approx((coal + nuc) / 2)
    assert series.values[2] == pytest.approx(nuc)
    filled = [e for e in entries if e.kind == "filled_hour"]
    assert filled[0].t == datetime(2026, 6, 1, 1, tzinfo=UTC)
    assert filled[0].region == "PACE"
    assert "2026-06-01T01:00" in filled[0].message and "PACE" in filled[0].message


def test_hourly_intensity_leading_zero_hours_take_nearest_value():
    rows = [fuel_row("2026-06-01T00", "SUN", 0), fuel_row("2026-06-01T03", "WAT", 5)]
    series, _ = hourly_intensity(rows, "PACE")
    assert all(v == pytest.approx(EMISSION_FACTORS["Hydro"]) for v in series.values[:4])


def test_hourly_intensity_logs_each_unknown_code_once_with_row_count():
    rows = [
        fuel_row("2026-06-01T00", "GEO", 5, "CISO"),
        fuel_row("2026-06-01T01", "GEO", 6, "CISO"),
        fuel_row("2026-06-01T02", "GEO", None, "CISO"),
        fuel_row("2026-06-01T00", "BAT", -2, "CISO"),
        fuel_row("2026-06-01T00", "SUN", 100, "CISO"),
        fuel_row("2026-06-01T00", "PS", 7, "BPAT"),   # other respondent: ignored
    ]
    series, entries = hourly_intensity(rows, "CISO")
    unknown = [e for e in entries if e.kind == "unknown_fuel"]
    assert {(e.code, e.region, e.rows) for e in unknown} == {("GEO", "CISO", 3), ("BAT", "CISO", 1)}
    assert all(isinstance(e, LogEntry) for e in entries)
    expected = (5 * EMISSION_FACTORS["Other"] + 100 * EMISSION_FACTORS["Solar"]) / 105
    assert series.name == "CISO.CI"
    assert series.values[0] == pytest.approx(expected)


@pytest.mark.parametrize("rows", [
    [],
    [fuel_row("2026-06-01T00", "COL", 0), fuel_row("2026-06-01T05", "NG", -4),
     fuel_row("2026-06-01T06", "SUN", None)],
])
def test_hourly_intensity_all_zero_raises_no_fuel_mix(rows):
    with pytest.raises(NoFuelMixError) as exc:
        hourly_intensity(rows, "BPAT")
    assert exc.value.region == "BPAT"
    assert "BPAT" in str(exc.value)
    assert isinstance(exc.value, NoDataError)


def test_hourly_intensity_ignores_rows_outside_data_range():
    rows = [fuel_row("2026-05-31T23", "COL", 100), fuel_row("2026-06-01T00", "WND", 100)]
    series, _ = hourly_intensity(rows, "PACE")
    assert series.values[0] == pytest.approx(EMISSION_FACTORS["Wind"])
