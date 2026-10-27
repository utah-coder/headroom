"""Fuel mapping and average carbon intensity (Requirement 8).

``CI_h = sum_f gen_f * EF_f / sum_f gen_f`` with ``gen_f = max(value, 0)`` and
missing values counted as 0 (Requirements 8.3, 8.4). Emission factors and the
fuel-code map come from ``sim.config``. Codes outside ``FUEL_CODE_MAP`` (for
example ``GEO``, ``BAT``, ``PS``, ``UNK``) map to Other (Requirement 8.2; the
GEO choice is recorded in DECISIONS.md).

The result is average intensity, not marginal.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Mapping, Optional, Sequence

from sim.config import EMISSION_FACTORS, FUEL_CODE_MAP
from sim.grid import HourlySeries, NoDataError, _bucket, data_range, fill_gaps
from sim.timeutil import iso_z

__all__ = [
    "OTHER",
    "CI",
    "NoFuelMixError",
    "LogEntry",
    "map_fuel",
    "emission_factor",
    "intensity_hour",
    "hourly_intensity",
]

log = logging.getLogger(__name__)

OTHER = "Other"  # Emission_Factor_Table entry for unknown codes
CI = "CI"        # suffix of the per-region carbon intensity series, e.g. "PACE.CI"
_HOUR = timedelta(hours=1)


class NoFuelMixError(NoDataError):
    """A region has zero total generation in every hour (Requirement 8.7)."""

    def __init__(self, region: str) -> None:
        self.region = region
        super().__init__(f"{region}.fuel-mix", region,
                         f"{region} has no usable fuel mix data "
                         "(zero total generation in every hour)")


@dataclass(frozen=True)
class LogEntry:
    """One build-log line from the Carbon_Calculator.

    ``kind`` is ``"unknown_fuel"`` (``code`` and ``rows`` set) or
    ``"filled_hour"`` (``t`` set, the UTC start of the filled hour).
    """

    kind: str
    region: str
    message: str
    code: Optional[str] = None
    rows: Optional[int] = None
    t: Optional[datetime] = None


def map_fuel(code: str) -> str:
    """EIA fuel code -> Emission_Factor_Table name; unknown codes -> Other (Req 8.1, 8.2)."""
    return FUEL_CODE_MAP.get(code, OTHER)


def emission_factor(code: str) -> float:
    """gCO2eq/kWh for an EIA fuel code, read from the Config_Module."""
    return float(EMISSION_FACTORS[map_fuel(code)])


def intensity_hour(gen_by_fuel: Mapping[str, Optional[float]]) -> Optional[float]:
    """Generation-weighted intensity for one hour, keyed by EIA fuel code.

    Negative or missing (``None``) generation counts as 0 in both sums
    (Requirement 8.3). Returns ``None`` when the total is 0.
    """
    total = 0.0
    weighted = 0.0
    for code, value in gen_by_fuel.items():
        if value is None or not value > 0:  # None, negative, zero or NaN
            continue
        g = float(value)
        total += g
        weighted += g * emission_factor(code)
    if total <= 0.0:
        return None
    return weighted / total


def _unknown_codes(fuel_rows: Sequence[Mapping], region: str) -> Counter:
    """Row counts per (code, respondent) for codes outside FUEL_CODE_MAP."""
    counts: Counter = Counter()
    for row in fuel_rows:
        respondent = row.get("respondent") or region
        if respondent != region:
            continue
        code = row.get("fueltype")
        if code and str(code) not in FUEL_CODE_MAP:
            counts[(str(code), str(respondent))] += 1
    return counts


def hourly_intensity(fuel_rows: Sequence[Mapping],
                     region: str) -> tuple[HourlySeries, list[LogEntry]]:
    """Carbon intensity for every hour of the Data_Range for ``region``.

    ``fuel_rows`` are the region's EIA fuel-type-data rows (``period``,
    ``respondent``, ``fueltype``, ``value``). Hours with zero total generation
    (including hours with no rows) are filled through ``grid.fill_gaps``:
    interior hours by linear interpolation, edge hours by the nearest computed
    hour (Requirement 8.6). Returns the ``"<REGION>.CI"`` series and the log
    entries: one per distinct unknown (code, respondent) with its row count,
    then one per filled hour. Raises ``NoFuelMixError`` when every hour has
    zero total generation (Requirement 8.7).
    """
    start, n = data_range()
    entries: list[LogEntry] = []

    for (code, respondent), rows in sorted(_unknown_codes(fuel_rows, region).items()):
        msg = (f"{respondent}: unknown fuel code {code!r} mapped to {OTHER} "
               f"({rows} rows)")
        entries.append(LogEntry("unknown_fuel", respondent, msg, code=code, rows=rows))

    by_fuel = _bucket(fuel_rows, region, "fueltype", start, n)
    raw_ci = [intensity_hour({code: values[i] for code, values in by_fuel.items()})
              for i in range(n)]

    name = f"{region}.{CI}"
    try:
        filled, _ = fill_gaps(raw_ci, series=name)
    except NoDataError:
        raise NoFuelMixError(region) from None

    for i, v in enumerate(raw_ci):
        if v is None:
            t = start + i * _HOUR
            msg = (f"{region}: zero total generation at {iso_z(t)}; "
                   f"carbon intensity filled with {filled[i]:.1f} g/kWh")
            entries.append(LogEntry("filled_hour", region, msg, t=t))

    for e in entries:
        log.info(e.message)
    return HourlySeries(name, start, tuple(filled)), entries
