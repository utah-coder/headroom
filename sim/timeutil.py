"""UTC and America/Denver time helpers, ISO 'Z' formatting and slot math.

All instants inside the simulation are timezone-aware UTC datetimes. Naive
datetimes are rejected so a missing tzinfo can never silently shift a series.

EIA `period` values (``YYYY-MM-DDTHH``) are parsed as the UTC hour that starts
at that label (Requirement 7.10; interpretation logged in DECISIONS.md).
Denver conversion goes through ``zoneinfo`` backed by the pinned ``tzdata``
package, because Windows has no system IANA database.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sim.config import SLOT_MINUTES

__all__ = [
    "DENVER_TZ",
    "parse_eia_period",
    "iso_z",
    "slot_times",
    "denver",
    "hhmm_denver",
    "hour_floor",
]

DENVER_TZ = ZoneInfo("America/Denver")

_EIA_PERIOD_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})T(\d{2})")


def _require_aware(t: datetime) -> None:
    if t.tzinfo is None or t.utcoffset() is None:
        raise ValueError(f"naive datetime not allowed: {t!r}")


def parse_eia_period(p: str) -> datetime:
    """Parse an EIA hourly period such as ``"2026-06-01T00"`` as a UTC datetime."""
    m = _EIA_PERIOD_RE.fullmatch(p)
    if m is None:
        raise ValueError(f"not an EIA hourly period (YYYY-MM-DDTHH): {p!r}")
    year, month, day, hour = (int(g) for g in m.groups())
    return datetime(year, month, day, hour, tzinfo=timezone.utc)


def iso_z(t: datetime) -> str:
    """Format an aware instant as UTC ISO 8601 with a ``Z`` suffix, e.g. ``"2026-07-15T21:00:00Z"``."""
    _require_aware(t)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def slot_times(start: datetime, n: int) -> list[datetime]:
    """Return the UTC start times of ``n`` consecutive slots beginning at ``start``."""
    _require_aware(start)
    if n < 0:
        raise ValueError(f"slot count must be non-negative: {n}")
    base = start.astimezone(timezone.utc)
    step = timedelta(minutes=SLOT_MINUTES)
    return [base + i * step for i in range(n)]


def denver(t: datetime) -> datetime:
    """Convert an aware instant to America/Denver local time."""
    _require_aware(t)
    return t.astimezone(DENVER_TZ)


def hhmm_denver(t: datetime) -> str:
    """24-hour ``HH:MM`` of the instant in America/Denver, e.g. ``"14:15"``."""
    return denver(t).strftime("%H:%M")


def hour_floor(t: datetime) -> datetime:
    """Truncate an aware instant to the start of its UTC hour."""
    _require_aware(t)
    return t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
