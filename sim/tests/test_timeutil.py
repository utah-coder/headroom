"""Unit tests for sim.timeutil (example-based; properties live in test_period.py
and test_denver_time.py)."""

from datetime import datetime, timedelta, timezone

import pytest

from sim.config import SLOT_MINUTES
from sim.timeutil import (
    denver,
    hhmm_denver,
    hour_floor,
    iso_z,
    parse_eia_period,
    slot_times,
)

UTC = timezone.utc


def test_parse_eia_period_is_utc_hour_start():
    t = parse_eia_period("2026-06-01T00")
    assert t == datetime(2026, 6, 1, 0, tzinfo=UTC)
    assert t.utcoffset() == timedelta(0)


@pytest.mark.parametrize("bad", ["2026-6-01T00", "2026-06-01 00", "2026-06-01T00:00", "", "2026-06-01T24"])
def test_parse_eia_period_rejects_malformed(bad):
    with pytest.raises(ValueError):
        parse_eia_period(bad)


def test_iso_z_formats_utc_with_z():
    assert iso_z(datetime(2026, 7, 15, 21, tzinfo=UTC)) == "2026-07-15T21:00:00Z"
    # A Denver-local instant is converted back to UTC before formatting.
    assert iso_z(denver(datetime(2026, 7, 15, 21, tzinfo=UTC))) == "2026-07-15T21:00:00Z"


def test_naive_datetimes_rejected():
    naive = datetime(2026, 7, 15, 21)
    for fn in (iso_z, denver, hhmm_denver, hour_floor):
        with pytest.raises(ValueError):
            fn(naive)
    with pytest.raises(ValueError):
        slot_times(naive, 4)


def test_slot_times_steps_by_slot_length():
    start = datetime(2026, 7, 15, 0, tzinfo=UTC)
    ts = slot_times(start, 192)
    assert len(ts) == 192
    assert ts[0] == start
    assert all(b - a == timedelta(minutes=SLOT_MINUTES) for a, b in zip(ts, ts[1:]))
    assert ts[-1] == start + timedelta(minutes=SLOT_MINUTES * 191)
    assert slot_times(start, 0) == []


def test_hhmm_denver_is_utc_minus_6_in_summer():
    assert hhmm_denver(datetime(2026, 7, 15, 20, 15, tzinfo=UTC)) == "14:15"
    assert hhmm_denver(datetime(2026, 6, 1, 3, 0, tzinfo=UTC)) == "21:00"
    assert denver(datetime(2026, 9, 30, 23, tzinfo=UTC)).utcoffset() == timedelta(hours=-6)


def test_hour_floor_truncates_to_utc_hour():
    t = datetime(2026, 7, 15, 21, 45, 30, 123, tzinfo=UTC)
    assert hour_floor(t) == datetime(2026, 7, 15, 21, tzinfo=UTC)
    assert hour_floor(denver(t)) == datetime(2026, 7, 15, 21, tzinfo=UTC)
