"""Unit tests for the tariff in sim.site: Rocky Mountain Power Utah Schedule 9.

Rates are Sheets 9.1 and 9.2 (effective 2026-08-10); hours and holidays are Sheet 9.3.
"""

from datetime import date, datetime, timezone

import pytest

from sim import config as c
from sim.site import (
    _tariff_holidays,
    is_on_peak,
    is_summer,
    is_tariff_holiday,
    power_charge_per_kw,
    tariff_per_kwh,
)

UTC = timezone.utc


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_rates_match_schedule_9():
    assert (c.TARIFF_ONPEAK_PER_KWH, c.TARIFF_OFFPEAK_PER_KWH) == (0.055841, 0.028383)
    assert (c.TARIFF_WINTER_ONPEAK_PER_KWH, c.TARIFF_WINTER_OFFPEAK_PER_KWH) == (0.049417, 0.025118)
    assert (c.DEMAND_CHARGE_PER_KW, c.WINTER_DEMAND_CHARGE_PER_KW) == (15.54, 13.75)
    assert c.FACILITIES_CHARGE_PER_KW == 2.47
    assert c.ON_PEAK_HOURS_LOCAL == ((15, 22),)
    assert c.WINTER_ON_PEAK_HOURS_LOCAL == ((6, 9), (18, 22))


# 2026-07-15 is a Wednesday; Denver is MDT (UTC-6) in July.
@pytest.mark.parametrize(
    ("t", "on_peak"),
    [
        (utc(2026, 7, 15, 20, 45), False),  # Wed 14:45 MDT
        (utc(2026, 7, 15, 21, 0), True),    # Wed 15:00 MDT, start inclusive
        (utc(2026, 7, 16, 3, 45), True),    # Wed 21:45 MDT, last on-peak slot
        (utc(2026, 7, 16, 4, 0), False),    # Wed 22:00 MDT, end exclusive
    ],
)
def test_summer_weekday_boundaries_in_denver(t, on_peak):
    assert is_on_peak(t) is on_peak
    expected = c.TARIFF_ONPEAK_PER_KWH if on_peak else c.TARIFF_OFFPEAK_PER_KWH
    assert tariff_per_kwh(t) == expected


# 2026-01-14 is a Wednesday; Denver is MST (UTC-7).
@pytest.mark.parametrize(
    ("t", "on_peak"),
    [
        (utc(2026, 1, 14, 12, 45), False),  # 05:45 MST
        (utc(2026, 1, 14, 13, 0), True),    # 06:00 MST, morning block starts
        (utc(2026, 1, 14, 15, 45), True),   # 08:45 MST
        (utc(2026, 1, 14, 16, 0), False),   # 09:00 MST, morning block ends
        (utc(2026, 1, 14, 22, 0), False),   # 15:00 MST, on-peak in summer only
        (utc(2026, 1, 15, 1, 0), True),     # 18:00 MST, evening block starts
        (utc(2026, 1, 15, 4, 45), True),    # 21:45 MST
        (utc(2026, 1, 15, 5, 0), False),    # 22:00 MST
    ],
)
def test_winter_has_two_blocks_in_mst(t, on_peak):
    assert is_on_peak(t) is on_peak
    expected = c.TARIFF_WINTER_ONPEAK_PER_KWH if on_peak else c.TARIFF_WINTER_OFFPEAK_PER_KWH
    assert tariff_per_kwh(t) == expected


def test_season_follows_the_denver_month():
    assert not is_summer(utc(2026, 6, 1, 5, 59))   # May 31 23:59 MDT
    assert is_summer(utc(2026, 6, 1, 6, 0))        # June 1 00:00 MDT
    assert is_summer(utc(2026, 10, 1, 5, 59))      # Sept 30 23:59 MDT
    assert not is_summer(utc(2026, 10, 1, 6, 0))   # Oct 1 00:00 MDT
    assert power_charge_per_kw(utc(2026, 7, 15, 21, 0)) == c.DEMAND_CHARGE_PER_KW
    assert power_charge_per_kw(utc(2026, 1, 14, 21, 0)) == c.WINTER_DEMAND_CHARGE_PER_KW


@pytest.mark.parametrize(
    "t",
    [
        utc(2026, 7, 18, 21, 0),   # Sat 15:00 MDT
        utc(2026, 7, 19, 0, 0),    # Sat 18:00 MDT
        utc(2026, 7, 20, 1, 0),    # Sun 19:00 MDT
    ],
)
def test_weekend_afternoons_are_off_peak(t):
    assert not is_on_peak(t)
    assert tariff_per_kwh(t) == c.TARIFF_OFFPEAK_PER_KWH


def test_utc_saturday_that_is_friday_in_denver_is_on_peak():
    t = utc(2026, 7, 18, 1, 0)  # Saturday in UTC, Fri 19:00 MDT
    assert t.weekday() == 5
    assert is_on_peak(t)
    assert tariff_per_kwh(t) == c.TARIFF_ONPEAK_PER_KWH


def test_utc_monday_that_is_sunday_in_denver_is_off_peak():
    t = utc(2026, 7, 20, 1, 30)  # Monday in UTC, Sun 19:30 MDT
    assert t.weekday() == 0
    assert not is_on_peak(t)
    assert tariff_per_kwh(t) == c.TARIFF_OFFPEAK_PER_KWH


def test_2026_holidays_with_the_weekend_rule():
    assert _tariff_holidays(2026) == {
        date(2026, 1, 1),    # New Year's Day, Thursday
        date(2026, 2, 16),   # Presidents Day, third Monday
        date(2026, 5, 25),   # Memorial Day, last Monday
        date(2026, 7, 3),    # Independence Day falls on a Saturday: the Friday before
        date(2026, 7, 24),   # Pioneer Day, Friday
        date(2026, 9, 7),    # Labor Day, first Monday
        date(2026, 11, 26),  # Thanksgiving, fourth Thursday
        date(2026, 12, 25),  # Christmas, Friday
    }


def test_sunday_holiday_moves_to_monday():
    assert date(2027, 7, 5) in _tariff_holidays(2027)    # July 4, 2027 is a Sunday
    assert date(2027, 7, 26) not in _tariff_holidays(2027)  # Pioneer Day 2027 is a Saturday...
    assert date(2027, 7, 23) in _tariff_holidays(2027)   # ...so the Friday before


def test_saturday_new_year_is_observed_the_friday_before():
    # January 1, 2028 is a Saturday, so Friday December 31, 2027 is off-peak all day.
    assert is_tariff_holiday(utc(2028, 1, 1, 2, 0))   # Fri Dec 31 2027, 19:00 MST
    assert not is_on_peak(utc(2028, 1, 1, 2, 0))


def test_holiday_afternoon_is_off_peak():
    t = utc(2026, 7, 24, 22, 0)  # Pioneer Day, Fri 16:00 MDT
    assert is_tariff_holiday(t)
    assert not is_on_peak(t)
    assert tariff_per_kwh(t) == c.TARIFF_OFFPEAK_PER_KWH
    assert is_on_peak(utc(2026, 7, 23, 22, 0))  # the Thursday before is on-peak


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        tariff_per_kwh(datetime(2026, 7, 15, 21, 0))
