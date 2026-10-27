"""Grid series: Raw_Cache rows -> hourly series, gap fill and slot expansion.

``build_hourly(raw)`` turns the output of ``fetch_eia.load_all()`` (a dict keyed
``"<endpoint>:<respondent>"`` holding EIA rows) into one ``HourlySeries`` per
series over the whole Data_Range, one value per UTC hour, ``None`` = missing:

- ``"PACE.D"``, ``"PACE.DF"``, ``"PACE.NG"``, ``"PACE.TI"`` from region-data
  (``type`` field);
- ``"<REGION>.<FUEL>"`` (for example ``"PACE.SUN"``, ``"BPAT.WAT"``) from
  fuel-type-data (``fueltype`` field), for every fuel code present;
- ``"<REGION>.TOTAL"``: total net generation per hour, the sum over fuels of
  ``max(value, 0)`` for the fuels reported that hour, ``None`` when no fuel
  value is present that hour.

Gap filling is applied later, per Scenario window (Requirement 7.13).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Mapping, Optional, Sequence

from sim.config import DATA_END, DATA_START, HOME_REGION, REGION_TYPES, SLOTS_PER_HOUR
from sim.timeutil import parse_eia_period

__all__ = [
    "NoDataError",
    "HourlySeries",
    "TOTAL",
    "data_range",
    "coerce_value",
    "build_hourly",
    "fill_gaps",
    "expand_to_slots",
]

TOTAL = "TOTAL"  # suffix of the per-region total net generation series
_HOUR = timedelta(hours=1)


class NoDataError(RuntimeError):
    """A required series has no present values (Requirements 7.16, 9.7).

    The Scenario_Builder turns this into a non-zero exit and writes no
    Scenario_File. ``series`` and ``respondent`` are named in the message when
    known; the respondent defaults to the prefix of a ``"REGION.X"`` name.
    """

    exit_code = 1

    def __init__(self, series: Optional[str] = None, respondent: Optional[str] = None,
                 detail: str = "no present hourly values") -> None:
        if respondent is None and series and "." in series:
            respondent = series.split(".", 1)[0]
        self.series = series
        self.respondent = respondent
        self.detail = detail
        names = [f"{k}={v}" for k, v in (("series", series), ("respondent", respondent)) if v]
        super().__init__(f"{detail} ({', '.join(names)})" if names else detail)


@dataclass(frozen=True)
class HourlySeries:
    """One value per consecutive UTC hour starting at ``start``; ``None`` = missing."""

    name: str                       # e.g. "PACE.D", "PACE.SUN", "BPAT.TOTAL"
    start: datetime
    values: tuple[Optional[float], ...]

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.start.utcoffset() is None:
            raise ValueError(f"HourlySeries start must be timezone-aware: {self.start!r}")
        if (self.start.minute, self.start.second, self.start.microsecond) != (0, 0, 0):
            raise ValueError(f"HourlySeries start must be on the hour: {self.start!r}")
        if not isinstance(self.values, tuple):
            object.__setattr__(self, "values", tuple(self.values))

    def __len__(self) -> int:
        return len(self.values)

    @property
    def present_count(self) -> int:
        return sum(v is not None for v in self.values)


def data_range() -> tuple[datetime, int]:
    """UTC start of the Data_Range and its length in hours (inclusive end)."""
    start = parse_eia_period(DATA_START)
    end = parse_eia_period(DATA_END)
    return start, int((end - start) / _HOUR) + 1


def coerce_value(v: object) -> Optional[float]:
    """EIA ``value`` -> float, or ``None`` when missing.

    Numbers and numeric strings become floats; ``None``, empty/blank strings,
    unparseable strings and non-finite numbers become ``None``.
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        f = float(v)
    elif isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            f = float(s)
        except ValueError:
            return None
    else:
        return None
    return f if math.isfinite(f) else None


def _split_key(key: str) -> tuple[str, str]:
    endpoint, sep, respondent = key.partition(":")
    if not sep or not respondent:
        raise ValueError(f"raw key must be '<endpoint>:<respondent>': {key!r}")
    return endpoint, respondent


def _bucket(rows: Iterable[Mapping], respondent: str, field: str,
            start: datetime, n: int) -> dict[str, list[Optional[float]]]:
    """Group rows by ``row[field]`` into per-hour lists over the Data_Range.

    Rows outside the range or from another respondent are ignored. When an
    hour appears twice, a later present value wins and ``None`` never
    overwrites a present value.
    """
    out: dict[str, list[Optional[float]]] = {}
    for row in rows:
        resp = row.get("respondent")
        if resp is not None and resp != respondent:
            continue
        code = row.get(field)
        period = row.get("period")
        if not code or not period:
            continue
        idx = int((parse_eia_period(period) - start) / _HOUR)
        if not 0 <= idx < n:
            continue
        values = out.setdefault(str(code), [None] * n)
        v = coerce_value(row.get("value"))
        if v is not None:
            values[idx] = v
    return out


def _total(fuels: Mapping[str, Sequence[Optional[float]]], n: int) -> list[Optional[float]]:
    total: list[Optional[float]] = [None] * n
    for values in fuels.values():
        for i, v in enumerate(values):
            if v is not None:
                total[i] = (total[i] or 0.0) + max(v, 0.0)
    return total


def build_hourly(raw: Mapping[str, Sequence[Mapping]]) -> dict[str, HourlySeries]:
    """Hourly series over the Data_Range from ``load_all()`` output.

    Raises ``NoDataError`` when the cache has no PACE ``D`` values or no PACE
    Fuel_Mix_Data values within the Data_Range (Requirement 9.7).
    """
    start, n = data_range()
    series: dict[str, HourlySeries] = {}

    for key in sorted(raw):
        endpoint, respondent = _split_key(key)
        if endpoint == "region-data":
            by_type = _bucket(raw[key], respondent, "type", start, n)
            for t in REGION_TYPES:
                name = f"{respondent}.{t}"
                series[name] = HourlySeries(name, start, tuple(by_type.get(t, [None] * n)))
        elif endpoint == "fuel-type-data":
            by_fuel = _bucket(raw[key], respondent, "fueltype", start, n)
            for fuel in sorted(by_fuel):
                name = f"{respondent}.{fuel}"
                series[name] = HourlySeries(name, start, tuple(by_fuel[fuel]))
            name = f"{respondent}.{TOTAL}"
            series[name] = HourlySeries(name, start, tuple(_total(by_fuel, n)))

    demand = series.get(f"{HOME_REGION}.D")
    if demand is None or demand.present_count == 0:
        raise NoDataError(f"{HOME_REGION}.D", HOME_REGION,
                          "Raw_Cache has no PACE demand values in the Data_Range")
    total = series.get(f"{HOME_REGION}.{TOTAL}")
    if total is None or total.present_count == 0:
        raise NoDataError(f"{HOME_REGION}.fuel-mix", HOME_REGION,
                          "Raw_Cache has no PACE Fuel_Mix_Data values in the Data_Range")
    return series


def _missing(v: Optional[float]) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def fill_gaps(values: Sequence[Optional[float]], *,
              series: Optional[str] = None) -> tuple[list[float], int]:
    """Fill missing hours; return ``(filled, n_filled)`` (Requirement 7.13).

    Interior gaps are linearly interpolated between the nearest present values
    before and after; leading/trailing gaps take the nearest present value.
    Present values are returned unchanged. Raises ``NoDataError`` (naming
    ``series`` when given) if every value is missing (Requirement 7.16).
    """
    present = [i for i, v in enumerate(values) if not _missing(v)]
    if not present:
        raise NoDataError(series)
    out = [None if _missing(v) else float(v) for v in values]
    n_filled = len(out) - len(present)
    if n_filled == 0:
        return out, 0  # type: ignore[return-value]

    first, last = present[0], present[-1]
    for i in range(first):
        out[i] = out[first]
    for i in range(last + 1, len(out)):
        out[i] = out[last]
    for a, b in zip(present, present[1:]):
        if b - a > 1:
            va, vb = out[a], out[b]
            for i in range(a + 1, b):
                out[i] = va + (vb - va) * (i - a) / (b - a)
    return out, n_filled  # type: ignore[return-value]


def expand_to_slots(hourly: Sequence[float]) -> list[float]:
    """Repeat each hourly value unchanged for the hour's four slots (Requirement 7.12)."""
    out: list[float] = []
    for i, v in enumerate(hourly):
        if _missing(v):
            raise ValueError(f"expand_to_slots needs a gap-filled series; hour {i} is missing")
        out.extend([v] * SLOTS_PER_HOUR)
    return out
