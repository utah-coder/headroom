"""Unit tests for the hourly helpers in sim.grid (example-based; properties for
slot expansion and gap filling live in test_grid_expand.py and test_grid_fill.py)."""

from datetime import datetime, timezone

import pytest

from sim.config import SLOTS_PER_HOUR
from sim.grid import (
    HourlySeries,
    NoDataError,
    build_hourly,
    coerce_value,
    data_range,
    expand_to_slots,
    fill_gaps,
)

UTC = timezone.utc
START = datetime(2026, 6, 1, 0, tzinfo=UTC)
N_HOURS = 122 * 24  # June 1 .. Sept 30 inclusive


def region_row(period, type_, value, respondent="PACE"):
    return {"period": period, "respondent": respondent, "type": type_,
            "value": value, "value-units": "megawatthours"}


def fuel_row(period, fuel, value, respondent="PACE"):
    return {"period": period, "respondent": respondent, "fueltype": fuel,
            "value": value, "value-units": "megawatthours"}


def minimal_raw():
    return {
        "region-data:PACE": [
            region_row("2026-06-01T00", "D", "8000"),
            region_row("2026-06-01T01", "D", 8100),
            region_row("2026-06-01T00", "DF", None),
            region_row("2026-06-01T00", "NG", "7900.5"),
            region_row("2026-06-01T00", "TI", "-150"),
        ],
        "fuel-type-data:PACE": [
            fuel_row("2026-06-01T00", "SUN", "-5"),
            fuel_row("2026-06-01T00", "COL", "4000"),
            fuel_row("2026-06-01T01", "SUN", ""),
            fuel_row("2026-06-01T01", "COL", "4100"),
        ],
        "fuel-type-data:BPAT": [
            fuel_row("2026-09-30T23", "WAT", "9000", "BPAT"),
        ],
    }


def test_data_range_covers_june_through_september():
    assert data_range() == (START, N_HOURS)


@pytest.mark.parametrize("raw,expected", [
    ("123.5", 123.5), (" 42 ", 42.0), (7, 7.0), (-3.25, -3.25),
    (None, None), ("", None), ("  ", None), ("n/a", None), ("nan", None), (True, None),
])
def test_coerce_value(raw, expected):
    assert coerce_value(raw) == expected


def test_build_hourly_region_series_and_coercion():
    s = build_hourly(minimal_raw())
    for name in ("PACE.D", "PACE.DF", "PACE.NG", "PACE.TI"):
        assert s[name].name == name
        assert s[name].start == START
        assert len(s[name]) == N_HOURS
    assert s["PACE.D"].values[:3] == (8000.0, 8100.0, None)
    assert s["PACE.DF"].present_count == 0
    assert s["PACE.NG"].values[0] == 7900.5
    assert s["PACE.TI"].values[0] == -150.0


def test_build_hourly_fuel_series_and_totals():
    s = build_hourly(minimal_raw())
    assert s["PACE.SUN"].values[:2] == (-5.0, None)  # raw values kept; '' -> None
    assert s["PACE.COL"].values[:2] == (4000.0, 4100.0)
    # Total clamps negatives to 0 and is None only where no fuel is present.
    assert s["PACE.TOTAL"].values[:3] == (4000.0, 4100.0, None)
    assert s["BPAT.WAT"].values[-1] == 9000.0
    assert s["BPAT.TOTAL"].values[-1] == 9000.0
    assert s["BPAT.TOTAL"].present_count == 1


def test_build_hourly_ignores_out_of_range_and_foreign_rows():
    raw = minimal_raw()
    raw["region-data:PACE"] += [
        region_row("2026-05-31T23", "D", "1"),
        region_row("2026-10-01T00", "D", "1"),
        region_row("2026-06-01T02", "D", "1", respondent="CISO"),
        region_row("2026-06-01T03", "XX", "1"),
    ]
    s = build_hourly(raw)
    assert s["PACE.D"].present_count == 2
    assert "PACE.XX" not in s


def test_build_hourly_duplicate_none_does_not_erase_value():
    raw = minimal_raw()
    raw["region-data:PACE"].append(region_row("2026-06-01T00", "D", None))
    assert build_hourly(raw)["PACE.D"].values[0] == 8000.0


def test_build_hourly_requires_pace_demand():
    raw = minimal_raw()
    raw["region-data:PACE"] = [r for r in raw["region-data:PACE"] if r["type"] != "D"]
    with pytest.raises(NoDataError) as e:
        build_hourly(raw)
    assert e.value.series == "PACE.D" and e.value.respondent == "PACE"
    assert "PACE.D" in str(e.value)


def test_build_hourly_requires_pace_fuel_mix():
    raw = minimal_raw()
    raw["fuel-type-data:PACE"] = [fuel_row("2026-06-01T00", "SUN", None)]
    with pytest.raises(NoDataError) as e:
        build_hourly(raw)
    assert e.value.respondent == "PACE"
    del raw["fuel-type-data:PACE"]
    with pytest.raises(NoDataError):
        build_hourly(raw)


def test_hourly_series_rejects_naive_or_off_hour_start():
    with pytest.raises(ValueError):
        HourlySeries("X", datetime(2026, 6, 1), (1.0,))
    with pytest.raises(ValueError):
        HourlySeries("X", datetime(2026, 6, 1, 0, 30, tzinfo=UTC), (1.0,))


def test_fill_gaps_interior_linear_and_edges_nearest():
    filled, n = fill_gaps([None, None, 10.0, None, None, 40.0, None])
    assert filled == [10.0, 10.0, 10.0, 20.0, 30.0, 40.0, 40.0]
    assert n == 5


def test_fill_gaps_no_gaps_returns_values_unchanged():
    values = [1.0, 2.5, -3.0]
    filled, n = fill_gaps(values)
    assert filled == values and n == 0


def test_fill_gaps_single_present_value():
    assert fill_gaps([None, 5.0, None]) == ([5.0, 5.0, 5.0], 2)


def test_fill_gaps_all_missing_raises_naming_series():
    with pytest.raises(NoDataError):
        fill_gaps([None, None])
    with pytest.raises(NoDataError):
        fill_gaps([])
    with pytest.raises(NoDataError) as e:
        fill_gaps([None], series="BPAT.D")
    assert e.value.series == "BPAT.D" and e.value.respondent == "BPAT"


def test_expand_to_slots_repeats_each_hour_four_times():
    assert SLOTS_PER_HOUR == 4
    assert expand_to_slots([1.0, 2.0]) == [1.0] * 4 + [2.0] * 4
    assert len(expand_to_slots([0.0] * 48)) == 192
    assert expand_to_slots([]) == []


def test_expand_to_slots_rejects_missing_values():
    with pytest.raises(ValueError):
        expand_to_slots([1.0, None])
