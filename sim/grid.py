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

Also selects the Peak_Scenario and Sun_Scenario windows (Requirement 9), the
Stress_Slots (Requirement 15.1) and the DR_Event start slot (Requirement 14.2),
builds the 192 ``GridSlot``s of a Scenario window (``build_slots``) and writes
the hourly sanity series to ``data/processed/`` (``write_processed``).

``python -m sim.grid`` reads the Raw_Cache offline and prints both selected
windows, the peak hour, solar share and filled-hour counts.
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence

from sim.config import (
    ALT_REGIONS,
    DATA_END,
    DATA_START,
    DR_DURATION_SLOTS,
    ENABLE_SUN_SCENARIO,
    HOME_REGION,
    REGION_TYPES,
    ROUND,
    SLOTS_PER_HOUR,
    STRESS_TOP_FRACTION,
    WINDOW_HOURS,
)
from sim.fetch_eia import CacheMissingError, load_all
from sim.site import is_on_peak, tariff_per_kwh
from sim.timeutil import denver, iso_z, parse_eia_period, slot_times

__all__ = [
    "NoDataError",
    "HourlySeries",
    "TOTAL",
    "data_range",
    "coerce_value",
    "build_hourly",
    "fill_gaps",
    "expand_to_slots",
    "select_peak_window",
    "select_sun_window",
    "stress_count",
    "stress_flags",
    "select_dr_start",
    "GridSlot",
    "SlotGrid",
    "build_slots",
    "solar_share",
    "PROCESSED_DIR",
    "write_processed",
    "main",
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


# --- Window, stress and DR-start selection ----------------------------------
#
# The window selectors treat the extent of the series they are given as the
# Data_Range. ``build_hourly`` always returns series spanning exactly the
# Data_Range, so for real data this is 2026-06-01T00Z .. 2026-09-30T23Z.

_PEAK_OFFSET_HOURS = WINDOW_HOURS // 2  # peak hour is hour 25 (index 24) of the window


def _require_window_fits(series: HourlySeries) -> None:
    if len(series) < WINDOW_HOURS:
        raise ValueError(f"{series.name} has {len(series)} hours; a scenario window "
                         f"needs {WINDOW_HOURS}")


def _peak_index(d: HourlySeries) -> int:
    """Index of the earliest hour holding the highest present value of ``d``."""
    peak_idx: Optional[int] = None
    for i, v in enumerate(d.values):
        if not _missing(v) and (peak_idx is None or v > d.values[peak_idx]):
            peak_idx = i  # strict '>' keeps the earliest hour on ties
    if peak_idx is None:
        raise NoDataError(d.name)
    return peak_idx


def select_peak_window(d: HourlySeries) -> datetime:
    """UTC start of the Peak_Scenario window (Requirements 9.1, 9.2, 9.4).

    The peak hour is the earliest hour holding the highest present value of
    ``d``. The window starts 24 hours before it, so the peak is the window's
    25th hour; if that would leave the range, the start is shifted by the
    minimum number of whole hours to keep all 48 hours inside it.
    Raises ``NoDataError`` if ``d`` has no present values.
    """
    _require_window_fits(d)
    peak_idx = _peak_index(d)
    latest_start = len(d) - WINDOW_HOURS
    start_idx = min(max(peak_idx - _PEAK_OFFSET_HOURS, 0), latest_start)
    return d.start + start_idx * _HOUR


def select_sun_window(sun: HourlySeries, total: HourlySeries) -> datetime:
    """UTC start of the Sun_Scenario window (Requirements 9.3, 9.4, 9.6).

    Every 48-hour window fully inside the range is a candidate. Its
    Solar_Share is ``sum(max(SUN, 0)) / sum(TOTAL)`` over the hours where both
    values are present; candidates whose summed total is zero (or less) are
    excluded. The highest share wins, earliest start on ties.

    Uses prefix sums, kept as exact ``Fraction``s so equal shares compare
    equal and the earliest-start tie-break is not decided by rounding.
    Raises ``NoDataError`` if no candidate has a non-zero total.
    """
    if sun.start != total.start or len(sun) != len(total):
        raise ValueError(f"{sun.name} and {total.name} must cover the same hours")
    _require_window_fits(sun)

    num = [Fraction(0)]
    den = [Fraction(0)]
    for s, t in zip(sun.values, total.values):
        if _missing(s) or _missing(t):
            num.append(num[-1])
            den.append(den[-1])
        else:
            num.append(num[-1] + Fraction(max(s, 0.0)))
            den.append(den[-1] + Fraction(t))

    best_idx: Optional[int] = None
    best_share = Fraction(0)
    for i in range(len(sun) - WINDOW_HOURS + 1):
        j = i + WINDOW_HOURS
        t_sum = den[j] - den[i]
        if t_sum <= 0:
            continue
        share = (num[j] - num[i]) / t_sum
        if best_idx is None or share > best_share:
            best_idx, best_share = i, share
    if best_idx is None:
        raise NoDataError(sun.name, detail="no candidate sun window has non-zero "
                                           "total net generation")
    return sun.start + best_idx * _HOUR


def stress_count(n: int) -> int:
    """``k = ceil(STRESS_TOP_FRACTION × n)``, computed exactly (10 for n = 192)."""
    return math.ceil(n * Fraction(str(STRESS_TOP_FRACTION)))


def _check_present(values: Sequence[float], what: str) -> None:
    for i, v in enumerate(values):
        if _missing(v):
            raise ValueError(f"{what} needs a gap-filled series; slot {i} is missing")


def stress_flags(demand: Sequence[float]) -> list[bool]:
    """Stress_Slot flags for slot demand values (glossary, Requirement 15.1).

    The threshold is the ``stress_count(N)``-th largest demand value and a slot
    is a Stress_Slot iff its demand is at or above it. Ties at the threshold
    are included, so more than ``k`` slots can be flagged (DECISIONS.md,
    "Stress slots fall in whole hours").
    """
    _check_present(demand, "stress_flags")
    if not demand:
        return []
    threshold = sorted(demand, reverse=True)[stress_count(len(demand)) - 1]
    return [v >= threshold for v in demand]


def select_dr_start(demand: Sequence[float], span: int = DR_DURATION_SLOTS) -> int:
    """Slot index where the DR_Event starts (Requirement 14.2).

    Among indices ``i`` with ``i + span <= N``, returns the one with the
    highest demand, earliest on ties.
    """
    _check_present(demand, "select_dr_start")
    if span < 1:
        raise ValueError(f"span must be at least 1, got {span}")
    if len(demand) < span:
        raise ValueError(f"DR span of {span} slots does not fit in {len(demand)} slots")
    best = 0
    for i in range(1, len(demand) - span + 1):
        if demand[i] > demand[best]:
            best = i
    return best


# --- Scenario slots ----------------------------------------------------------

_CI = "CI"  # suffix of carbon.hourly_intensity's series, e.g. "PACE.CI"
_SLOTS = WINDOW_HOURS * SLOTS_PER_HOUR  # 192


@dataclass(frozen=True)
class GridSlot:
    """One 15-minute Slot of a Scenario window (design, Data Models)."""

    t: datetime                       # UTC slot start
    pace_demand_mw: float             # PACE D (actual)
    pace_forecast_mw: float           # PACE DF, the only demand signal for look-ahead
    carbon_g_per_kwh: float           # PACE average intensity
    alt_carbon: Mapping[str, float]   # {"BPAT": ..., "CISO": ...}
    price_per_kwh: float              # tariff for this slot
    on_peak: bool
    stress: bool


@dataclass(frozen=True)
class SlotGrid:
    """The 192 GridSlots of one Scenario window and its filled-hour counts.

    ``filled_hours`` maps series name (``"PACE.D"``, ``"PACE.DF"``,
    ``"<REGION>.CI"``) to the number of window hours filled by ``fill_gaps``;
    it goes to ``meta.params.filled_hours`` (Requirement 7.13).
    """

    start: datetime
    slots: tuple[GridSlot, ...]
    filled_hours: Mapping[str, int]

    def __len__(self) -> int:
        return len(self.slots)

    @property
    def end(self) -> datetime:
        """Window end: first slot start plus 48 hours (Requirement 9.5)."""
        return self.start + WINDOW_HOURS * _HOUR

    @property
    def window(self) -> tuple[str, str]:
        """``meta.window`` as two UTC ISO 8601 ``Z`` strings (Requirement 9.5)."""
        return iso_z(self.start), iso_z(self.end)

    @property
    def stress_threshold_mw(self) -> float:
        """Lowest PACE demand among the Stress_Slots (the k-th largest demand)."""
        return min(s.pace_demand_mw for s in self.slots if s.stress)


def _window_offset(series: HourlySeries, window_start: datetime) -> int:
    """Index of ``window_start`` in ``series``; the 48-hour window must fit inside."""
    if window_start.tzinfo is None or window_start.utcoffset() is None:
        raise ValueError(f"window_start must be timezone-aware: {window_start!r}")
    delta = window_start - series.start
    if delta % _HOUR:
        raise ValueError(f"window_start {window_start!r} is not on a whole hour of "
                         f"{series.name}")
    idx = delta // _HOUR
    if idx < 0 or idx + WINDOW_HOURS > len(series):
        raise ValueError(f"the {WINDOW_HOURS}-hour window starting {iso_z(window_start)} "
                         f"is outside {series.name}")
    return idx


def _window_values(series: HourlySeries, window_start: datetime) -> tuple[Optional[float], ...]:
    i = _window_offset(series, window_start)
    return series.values[i:i + WINDOW_HOURS]


def _fill_window(series: HourlySeries, name: str,
                 window_start: datetime) -> tuple[list[float], int]:
    try:
        return fill_gaps(_window_values(series, window_start), series=name)
    except NoDataError:
        raise NoDataError(name, detail="no present hourly values in the scenario window "
                                       f"starting {iso_z(window_start)}") from None


def _require(series: Mapping[str, HourlySeries], key: str, name: str) -> HourlySeries:
    s = series.get(key)
    if s is None:
        raise NoDataError(name, detail="required series is missing")
    return s


def build_slots(window_start: datetime, hourly: Mapping[str, HourlySeries],
                carbon: Mapping[str, HourlySeries]) -> SlotGrid:
    """The 192 GridSlots of the 48-hour window starting at ``window_start``.

    ``hourly`` is ``build_hourly`` output; ``carbon`` maps region to its
    ``carbon.hourly_intensity`` series and must hold PACE and every
    alternate region. The window's PACE D and DF and each region's
    intensity are gap-filled over the 48 hours (Requirement 7.13) and the
    filled hours counted per series; carbon.hourly_intensity has already
    filled (and logged) zero-generation hours, so the CI counts are normally 0.
    Each hourly value is repeated for its four slots (Requirement 7.12); the
    tariff, ``on_peak`` and ``stress`` flags are attached per slot.

    Raises ``NoDataError`` naming the series and respondent when a required
    series is absent or has no present value in the window (Requirement 7.16).
    """
    filled_hours: dict[str, int] = {}

    def window_slots(series: HourlySeries, name: str) -> list[float]:
        values, n_filled = _fill_window(series, name, window_start)
        filled_hours[name] = n_filled
        return expand_to_slots(values)

    d_name, df_name = f"{HOME_REGION}.D", f"{HOME_REGION}.DF"
    demand = window_slots(_require(hourly, d_name, d_name), d_name)
    forecast = window_slots(_require(hourly, df_name, df_name), df_name)

    ci: dict[str, list[float]] = {}
    for region in (HOME_REGION, *ALT_REGIONS):
        name = f"{region}.{_CI}"
        ci[region] = window_slots(_require(carbon, region, name), name)

    stress = stress_flags(demand)
    slots = tuple(
        GridSlot(
            t=t,
            pace_demand_mw=demand[i],
            pace_forecast_mw=forecast[i],
            carbon_g_per_kwh=ci[HOME_REGION][i],
            alt_carbon={r: ci[r][i] for r in ALT_REGIONS},
            price_per_kwh=tariff_per_kwh(t),
            on_peak=is_on_peak(t),
            stress=stress[i],
        )
        for i, t in enumerate(slot_times(window_start, _SLOTS))
    )
    return SlotGrid(window_start, slots, filled_hours)


def solar_share(sun: HourlySeries, total: HourlySeries,
                window_start: datetime) -> Optional[float]:
    """Solar_Share of one 48-hour window, as ``select_sun_window`` scores it.

    ``sum(max(SUN, 0)) / sum(TOTAL)`` over the hours where both are present;
    ``None`` when that total is zero or less.
    """
    num = den = 0.0
    for s, t in zip(_window_values(sun, window_start), _window_values(total, window_start)):
        if not (_missing(s) or _missing(t)):
            num += max(s, 0.0)
            den += t
    return num / den if den > 0 else None


# --- Processed sanity series --------------------------------------------------

PROCESSED_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

# Output key -> hourly series name (suffix after "PACE.") for pace_hourly.json.
_PACE_COLUMNS = (
    ("demand_mw", "D"),
    ("forecast_mw", "DF"),
    ("net_generation_mw", "NG"),
    ("interchange_mw", "TI"),
    ("solar_mw", "SUN"),
    ("total_generation_mw", TOTAL),
)


def _q(v: Optional[float], nd: int) -> Optional[float]:
    if _missing(v):
        return None
    r = round(float(v), nd)
    return 0.0 if r == 0 else r  # no -0.0 in the output


def _dumps(doc: Mapping) -> str:
    return json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"


def _write_all_atomic(files: Mapping[Path, str]) -> None:
    """Write every file to ``*.tmp`` first, then ``os.replace`` them all."""
    tmps: list[Path] = []
    try:
        for path, text in files.items():
            tmp = path.with_name(path.name + ".tmp")
            tmps.append(tmp)
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        for path, tmp in zip(files, tmps):
            os.replace(tmp, path)
    finally:
        for tmp in tmps:
            if tmp.exists():
                tmp.unlink()


def write_processed(hourly: Mapping[str, HourlySeries], carbon: Mapping[str, HourlySeries],
                    out_dir: Optional[Path] = None) -> list[Path]:
    """Write ``pace_hourly.json`` and ``carbon_hourly.json`` for the sanity plot.

    ``pace_hourly.json`` holds the PACE hourly series over the full range of
    ``hourly`` before any gap fill (``null`` = missing); ``carbon_hourly.json``
    holds each region's hourly carbon intensity. Values are rounded per
    ``ROUND``. Both files are written atomically. Returns the two paths.
    """
    base = _require(hourly, f"{HOME_REGION}.D", f"{HOME_REGION}.D")
    start, n = base.start, len(base)
    regions = [r for r in (HOME_REGION, *ALT_REGIONS) if r in carbon]
    regions += sorted(set(carbon) - set(regions))
    for s in [*hourly.values(), *(carbon[r] for r in regions)]:
        if s.start != start or len(s) != n:
            raise ValueError(f"{s.name} does not cover the same hours as {base.name}")

    times = [iso_z(start + i * _HOUR) for i in range(n)]
    pace_cols = [(key, hourly.get(f"{HOME_REGION}.{suffix}")) for key, suffix in _PACE_COLUMNS]
    pace_doc = {
        "source": f"REAL: EIA-930 {HOME_REGION}",
        "note": "hourly values before gap fill; null = missing",
        "start": times[0] if times else None,
        "hours": n,
        "rows": [
            {"t": t, **{key: (_q(s.values[i], ROUND["mw"]) if s else None)
                        for key, s in pace_cols}}
            for i, t in enumerate(times)
        ],
    }
    carbon_doc = {
        "source": "REAL: EIA-930 fuel mix with config emission factors",
        "units": "gCO2eq/kWh",
        "note": "average intensity, not marginal",
        "start": times[0] if times else None,
        "hours": n,
        "rows": [
            {"t": t, **{r: _q(carbon[r].values[i], ROUND["carbon"]) for r in regions}}
            for i, t in enumerate(times)
        ],
    }

    root = Path(out_dir) if out_dir is not None else PROCESSED_DIR
    root.mkdir(parents=True, exist_ok=True)
    pace_path, carbon_path = root / "pace_hourly.json", root / "carbon_hourly.json"
    _write_all_atomic({pace_path: _dumps(pace_doc), carbon_path: _dumps(carbon_doc)})
    return [pace_path, carbon_path]


# --- Sanity check: python -m sim.grid ----------------------------------------

def _fmt_t(t: datetime) -> str:
    return f"{iso_z(t)} ({denver(t).strftime('%a %Y-%m-%d %H:%M %Z')})"


def _fmt_share(share: Optional[float]) -> str:
    return "n/a (zero total generation)" if share is None else f"{share * 100:.1f}%"


def _print_window(label: str, grid: SlotGrid, share: Optional[float]) -> None:
    print(f"{label} window: {grid.window[0]} .. {grid.window[1]}")
    print(f"  starts {_fmt_t(grid.start)}")
    print(f"  solar share: {_fmt_share(share)}")
    print("  filled hours: " + ", ".join(f"{k}={v}" for k, v in grid.filled_hours.items()))
    n_stress = sum(s.stress for s in grid.slots)
    print(f"  stress slots: {n_stress} (threshold {grid.stress_threshold_mw:.1f} MW)")


def main(*, cache_dir: Optional[Path] = None, out_dir: Optional[Path] = None) -> int:
    """Offline sanity check of the Raw_Cache; writes ``data/processed/``.

    Exits 1 with the error message when a Raw_Cache file is missing or a
    required series has no data (Requirement 9.7).
    """
    from sim.carbon import hourly_intensity  # carbon imports this module

    try:
        raw = load_all(offline=True, cache_dir=cache_dir)
        hourly = build_hourly(raw)
        carbon, ci_filled = {}, {}
        for region in (HOME_REGION, *ALT_REGIONS):
            series, entries = hourly_intensity(raw.get(f"fuel-type-data:{region}", []), region)
            carbon[region] = series
            ci_filled[region] = sum(e.kind == "filled_hour" for e in entries)

        d = hourly[f"{HOME_REGION}.D"]
        sun = _require(hourly, f"{HOME_REGION}.SUN", f"{HOME_REGION}.SUN")
        total = hourly[f"{HOME_REGION}.{TOTAL}"]
        start, n = data_range()
        print(f"Data_Range: {iso_z(start)} .. {iso_z(start + (n - 1) * _HOUR)} ({n} h)")
        print("carbon hours filled over the Data_Range: "
              + ", ".join(f"{r}={k}" for r, k in ci_filled.items()))

        peak_i = _peak_index(d)
        peak_t = d.start + peak_i * _HOUR
        print(f"peak hour: {_fmt_t(peak_t)}, {HOME_REGION} D = {d.values[peak_i]:.1f} MW")
        peak_start = select_peak_window(d)
        _print_window("peak", build_slots(peak_start, hourly, carbon),
                      solar_share(sun, total, peak_start))

        if ENABLE_SUN_SCENARIO:
            sun_start = select_sun_window(sun, total)
            _print_window("sun", build_slots(sun_start, hourly, carbon),
                          solar_share(sun, total, sun_start))
        else:
            print("sun window: skipped (ENABLE_SUN_SCENARIO = False)")

        for path in write_processed(hourly, carbon, out_dir):
            print(f"wrote {path}")
    except (CacheMissingError, NoDataError) as e:
        print(f"error: {e}", file=sys.stderr)
        return e.exit_code
    return 0


if __name__ == "__main__":
    # Run the copy imported as sim.grid, so its exception classes are the ones
    # sim.carbon raises (``python -m`` executes this file as __main__).
    from sim.grid import main as _main
    sys.exit(_main())
