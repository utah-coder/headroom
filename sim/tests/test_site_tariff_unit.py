"""Unit tests for the tariff in sim.site (example-based; Property 15 lives in
test_site_tariff.py)."""

from datetime import datetime, timezone

import pytest

from sim.config import TARIFF_OFFPEAK_PER_KWH, TARIFF_ONPEAK_PER_KWH
from sim.site import is_on_peak, tariff_per_kwh

UTC = timezone.utc


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_rates_match_requirement():
    assert TARIFF_ONPEAK_PER_KWH == 0.085
    assert TARIFF_OFFPEAK_PER_KWH == 0.045


# 2026-07-15 is a Wednesday; Denver is MDT (UTC-6) in July.
@pytest.mark.parametrize(
    ("t", "on_peak"),
    [
        (utc(2026, 7, 15, 20, 45), False),  # Wed 14:45 MDT
        (utc(2026, 7, 15, 21, 0), True),    # Wed 15:00 MDT, start inclusive
        (utc(2026, 7, 16, 1, 45), True),    # Wed 19:45 MDT, last on-peak slot
        (utc(2026, 7, 16, 2, 0), False),    # Wed 20:00 MDT, end exclusive
    ],
)
def test_weekday_boundaries_in_denver(t, on_peak):
    assert is_on_peak(t) is on_peak
    expected = TARIFF_ONPEAK_PER_KWH if on_peak else TARIFF_OFFPEAK_PER_KWH
    assert tariff_per_kwh(t) == expected


def test_winter_boundaries_use_mst():
    # 2026-01-14 is a Wednesday; Denver is MST (UTC-7).
    assert not is_on_peak(utc(2026, 1, 14, 21, 45))  # 14:45 MST
    assert is_on_peak(utc(2026, 1, 14, 22, 0))       # 15:00 MST


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
    assert tariff_per_kwh(t) == TARIFF_OFFPEAK_PER_KWH


def test_utc_saturday_that_is_friday_in_denver_is_on_peak():
    t = utc(2026, 7, 18, 1, 0)  # Saturday in UTC, Fri 19:00 MDT
    assert t.weekday() == 5
    assert is_on_peak(t)
    assert tariff_per_kwh(t) == TARIFF_ONPEAK_PER_KWH


def test_utc_monday_that_is_sunday_in_denver_is_off_peak():
    t = utc(2026, 7, 20, 1, 30)  # Monday in UTC, Sun 19:30 MDT
    assert t.weekday() == 0
    assert not is_on_peak(t)
    assert tariff_per_kwh(t) == TARIFF_OFFPEAK_PER_KWH


def test_naive_datetime_rejected():
    with pytest.raises(ValueError):
        tariff_per_kwh(datetime(2026, 7, 15, 21, 0))
