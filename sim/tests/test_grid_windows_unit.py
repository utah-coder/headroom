"""Unit tests for window, stress and DR-start selection in sim.grid
(example-based; properties live in test_grid_peak_window.py,
test_grid_sun_window.py and test_grid_stress_dr.py)."""

from datetime import datetime, timedelta, timezone

import pytest

from sim.config import WINDOW_HOURS
from sim.grid import (
    HourlySeries,
    NoDataError,
    data_range,
    select_dr_start,
    select_peak_window,
    select_sun_window,
    stress_count,
    stress_flags,
)

UTC = timezone.utc
START = datetime(2026, 6, 1, 0, tzinfo=UTC)
H = timedelta(hours=1)


def series(name, values, start=START):
    return HourlySeries(name, start, tuple(values))


# --- select_peak_window --------------------------------------------------------

def test_peak_hour_is_25th_hour_of_window():
    vals = [100.0] * 200
    vals[100] = 500.0
    start = select_peak_window(series("PACE.D", vals))
    assert start == START + 76 * H
    assert start + 24 * H == START + 100 * H


def test_peak_near_range_start_shifts_later_by_minimum():
    vals = [100.0] * 200
    vals[3] = 500.0
    assert select_peak_window(series("PACE.D", vals)) == START


def test_peak_near_range_end_shifts_earlier_by_minimum():
    vals = [100.0] * 200
    vals[195] = 500.0
    start = select_peak_window(series("PACE.D", vals))
    assert start == START + (200 - WINDOW_HOURS) * H
    assert start <= START + 195 * H < start + WINDOW_HOURS * H


def test_peak_tie_picks_earliest_hour_and_skips_missing():
    vals = [None] * 200
    vals[60] = 900.0
    vals[150] = 900.0
    vals[10] = 400.0
    assert select_peak_window(series("PACE.D", vals)) == START + 36 * H


def test_peak_window_on_full_data_range():
    start, n = data_range()
    vals = [1.0] * n
    vals[n - 1] = 2.0  # 2026-09-30T23Z
    got = select_peak_window(series("PACE.D", vals, start))
    assert got == datetime(2026, 9, 29, 0, tzinfo=UTC)


def test_peak_all_missing_raises_no_data():
    with pytest.raises(NoDataError, match="PACE.D"):
        select_peak_window(series("PACE.D", [None] * 60))


def test_peak_series_shorter_than_window_raises():
    with pytest.raises(ValueError):
        select_peak_window(series("PACE.D", [1.0] * (WINDOW_HOURS - 1)))


# --- select_sun_window ---------------------------------------------------------

def test_sun_picks_highest_share():
    n = 100
    sun = [0.0] * n
    total = [100.0] * n
    for i in range(30, 40):
        sun[i] = 80.0
    start = select_sun_window(series("PACE.SUN", sun), series("PACE.TOTAL", total))
    # Every window containing hours 30..39 ties; the earliest is the one starting at 0.
    assert start == START


def test_sun_negative_values_count_as_zero():
    n = 60
    total = [100.0] * n
    sun = [0.0] * n
    sun[0] = 50.0          # only in the window starting at 0
    for i in range(1, 13):
        sun[i] = -1000.0   # would sink window 0 if not clamped
    sun[59] = 40.0         # only in the last window (start 12)
    start = select_sun_window(series("PACE.SUN", sun), series("PACE.TOTAL", total))
    assert start == START


def test_sun_skips_hours_missing_either_value():
    n = 49  # two candidates: starts 0 and 1
    sun = [10.0] * n
    total = [100.0] * n
    sun[0] = None          # hour 0 excluded from window 0
    total[48] = 1.0        # hour 48 in window 1: share would be 10/1 ...
    sun[48] = None         # ... but SUN is missing so the hour is skipped
    total[47] = None       # hour 47 excluded from both windows
    sun[1] = 90.0          # in both windows
    # Window 0: hours 1..46 -> (90 + 45*10) / (46*100); window 1: same hours.
    # Equal shares, so the earliest start wins.
    start = select_sun_window(series("PACE.SUN", sun), series("PACE.TOTAL", total))
    assert start == START


def test_sun_excludes_zero_total_candidates():
    n = 50  # candidates 0, 1, 2
    sun = [0.0] * n
    total = [0.0] * n
    total[49] = 10.0
    sun[49] = 1.0
    # Only window 2 (hours 2..49) has a non-zero total.
    start = select_sun_window(series("PACE.SUN", sun), series("PACE.TOTAL", total))
    assert start == START + 2 * H


def test_sun_all_zero_total_raises_no_data():
    with pytest.raises(NoDataError):
        select_sun_window(series("PACE.SUN", [1.0] * 50), series("PACE.TOTAL", [0.0] * 50))


def test_sun_misaligned_series_raise():
    with pytest.raises(ValueError):
        select_sun_window(series("PACE.SUN", [1.0] * 50),
                          series("PACE.TOTAL", [1.0] * 50, START + H))


def test_sun_candidates_cover_full_data_range():
    start, n = data_range()
    sun = [0.0] * n
    total = [100.0] * n
    sun[n - 1] = 50.0
    got = select_sun_window(series("PACE.SUN", sun, start), series("PACE.TOTAL", total, start))
    assert got == datetime(2026, 9, 29, 0, tzinfo=UTC)


def test_sun_matches_brute_force_on_mixed_example():
    sun = [((i * 37) % 23) - 3.0 if i % 11 else None for i in range(120)]
    total = [float((i * 17) % 50) if i % 13 else None for i in range(120)]

    def brute():
        best, best_share = None, None
        for s in range(len(sun) - WINDOW_HOURS + 1):
            hrs = [h for h in range(s, s + WINDOW_HOURS)
                   if sun[h] is not None and total[h] is not None]
            t = sum(total[h] for h in hrs)
            if t == 0:
                continue
            share = sum(max(sun[h], 0.0) for h in hrs) / t
            if best is None or share > best_share:
                best, best_share = s, share
        return best

    got = select_sun_window(series("PACE.SUN", sun), series("PACE.TOTAL", total))
    assert got == START + brute() * H


# --- stress_flags --------------------------------------------------------------

def test_stress_count_is_exact_ceil():
    assert stress_count(192) == 10
    assert stress_count(20) == 1
    assert stress_count(21) == 2
    assert stress_count(60) == 3
    assert stress_count(1) == 1


def test_stress_flags_whole_hours_include_ties():
    hourly = [float(h) for h in range(48)]
    demand = [v for v in hourly for _ in range(4)]  # 192 slots
    flags = stress_flags(demand)
    # k = 10: the 10th largest is hour 45, so hours 45..47 (12 slots) are flagged.
    assert sum(flags) == 12
    assert flags == [d >= 45.0 for d in demand]


def test_stress_flags_distinct_values_flag_exactly_k():
    demand = [float((i * 7) % 192) for i in range(192)]
    flags = stress_flags(demand)
    assert sum(flags) == 10
    assert all(d >= 182 for d, f in zip(demand, flags) if f)


def test_stress_flags_empty_and_missing():
    assert stress_flags([]) == []
    with pytest.raises(ValueError):
        stress_flags([1.0, None])


# --- select_dr_start -----------------------------------------------------------

def test_dr_start_picks_earliest_maximum():
    demand = [1.0] * 40
    demand[10:14] = [5.0] * 4
    demand[20:24] = [5.0] * 4
    assert select_dr_start(demand, 8) == 10


def test_dr_start_ignores_slots_whose_span_overflows():
    demand = [1.0] * 40
    demand[35] = 9.0  # 35 + 8 > 40
    demand[32] = 3.0  # 32 + 8 == 40, allowed
    assert select_dr_start(demand) == 32


def test_dr_start_needs_room_for_span():
    assert select_dr_start([2.0] * 8) == 0
    with pytest.raises(ValueError):
        select_dr_start([2.0] * 7)
