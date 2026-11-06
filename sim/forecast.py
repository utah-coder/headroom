"""Forecasts that use only what was known at the time, and how accurate they were.

Carbon intensity has no forecast in the EIA data, so ``carbon_forecast`` makes one:
the trailing ``FORECAST_HISTORY_DAYS`` give a mean for each hour of the day, and the
latest hour's distance from that profile fades out by ``FORECAST_RESIDUAL_DECAY`` per
hour ahead. A backtest over Jun to Sep 2026 found a small ridge regression no better
on PACE and about 8% better on CISO, not enough to justify it (DECISIONS.md).

Demand already has a forecast: EIA's ``DF``, which is the balancing authority's own.
It isn't ours to improve here, so this module only measures it.

Confidence bands are built from past errors only: for a forecast issued at hour
``i``, the errors of earlier forecasts whose outcome was known by hour ``i``. Band
coverage is measured on the same rule, so it's what a site would have seen live.
Everything here is pure Python, summed with ``math.fsum``, so results don't depend on
the machine (DECISIONS.md, 2026-10-28 14:41 MT).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sim.config import (
    ALT_REGIONS,
    FORECAST_BAND_HISTORY_DAYS,
    FORECAST_BAND_MIN_SAMPLES,
    FORECAST_BAND_QUANTILES,
    FORECAST_HISTORY_DAYS,
    FORECAST_MAX_LEAD_HOURS,
    FORECAST_MIN_HISTORY_HOURS,
    FORECAST_RESIDUAL_DECAY,
    HOME_REGION,
    SLOTS_PER_HOUR,
)
from sim.grid import GridSlot

__all__ = [
    "quantile",
    "carbon_forecast",
    "ForecastErrors",
    "forecast_errors",
    "error_band",
    "CarbonAccuracy",
    "carbon_accuracy",
    "DemandAccuracy",
    "demand_accuracy",
    "demand_band",
    "CarbonView",
]

_HOURS_PER_DAY = 24


def quantile(values: Sequence[float], q: float) -> float:
    """Nearest-rank quantile: always one of the values, and the same on every machine."""
    if not values:
        raise ValueError("quantile of no values")
    ordered = sorted(values)
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def _mean(values: Sequence[float]) -> float:
    return math.fsum(values) / len(values)


def _hour_profile(y: Sequence[float], i: int) -> list[float]:
    """Mean by hour of day over the trailing history that ends at hour ``i``."""
    if i + 1 < FORECAST_MIN_HISTORY_HOURS:
        raise ValueError(f"forecast at hour {i} has under {FORECAST_MIN_HISTORY_HOURS} hours of history")
    start = max(0, i + 1 - _HOURS_PER_DAY * FORECAST_HISTORY_DAYS)
    by_hour: list[list[float]] = [[] for _ in range(_HOURS_PER_DAY)]
    for k in range(start, i + 1):
        by_hour[k % _HOURS_PER_DAY].append(y[k])
    return [_mean(b) for b in by_hour]


def carbon_forecast(y: Sequence[float], i: int, horizon: int) -> list[float]:
    """Forecast for hours ``i + 1 … i + horizon``, reading only ``y[:i + 1]``."""
    profile = _hour_profile(y, i)
    residual = y[i] - profile[i % _HOURS_PER_DAY]
    return [max(0.0, profile[(i + lead) % _HOURS_PER_DAY] + FORECAST_RESIDUAL_DECAY ** lead * residual)
            for lead in range(1, horizon + 1)]


@dataclass(frozen=True)
class ForecastErrors:
    """``by_lead[lead][i]`` is actual minus forecast, for a forecast issued at hour ``i``."""

    first_issue: int
    by_lead: Mapping[int, Sequence[float | None]]


def forecast_errors(y: Sequence[float], max_lead: int = FORECAST_MAX_LEAD_HOURS) -> ForecastErrors:
    """Every forecast this method would have made over ``y``, scored against what happened."""
    n = len(y)
    first = FORECAST_MIN_HISTORY_HOURS - 1
    by_lead: dict[int, list[float | None]] = {lead: [None] * n for lead in range(1, max_lead + 1)}
    for i in range(first, n - 1):
        horizon = min(max_lead, n - 1 - i)
        for lead, f in enumerate(carbon_forecast(y, i, horizon), start=1):
            by_lead[lead][i] = y[i + lead] - f
    return ForecastErrors(first, by_lead)


def error_band(errors: ForecastErrors, i: int, lead: int) -> tuple[float, float] | None:
    """``(low, high)`` offsets for a forecast issued at hour ``i``, from errors known by then.

    ``None`` when too few earlier forecasts of this lead have resolved.
    """
    newest = i - lead       # issued at newest, resolved at newest + lead == i
    oldest = max(errors.first_issue, newest + 1 - _HOURS_PER_DAY * FORECAST_BAND_HISTORY_DAYS)
    if newest < oldest:
        return None
    past = [e for e in errors.by_lead[lead][oldest:newest + 1] if e is not None]
    if len(past) < FORECAST_BAND_MIN_SAMPLES:
        return None
    low, high = FORECAST_BAND_QUANTILES
    return quantile(past, low), quantile(past, high)


@dataclass(frozen=True)
class CarbonAccuracy:
    region: str
    lead_hours: int
    forecasts: int
    mae_g_per_kwh: float         # mean absolute error of this forecast
    naive_mae_g_per_kwh: float   # the same, for "same hour yesterday", to compare against
    band_coverage_pct: float     # share of actuals inside the band; the target is 80


def _yesterday_error(y: Sequence[float], i: int, lead: int) -> float:
    k = i + lead
    while k > i:
        k -= _HOURS_PER_DAY
    return y[i + lead] - y[k]


def carbon_accuracy(y: Sequence[float], region: str, leads: Sequence[int],
                    errors: ForecastErrors | None = None) -> list[CarbonAccuracy]:
    """How the forecast did at each lead, over every forecast the data allows."""
    errors = forecast_errors(y) if errors is None else errors
    out: list[CarbonAccuracy] = []
    for lead in leads:
        line = errors.by_lead[lead]
        issued = [i for i, e in enumerate(line) if e is not None]
        absolute = [abs(line[i]) for i in issued]            # type: ignore[arg-type]
        naive = [abs(_yesterday_error(y, i, lead)) for i in issued]
        inside = banded = 0
        for i in issued:
            band = error_band(errors, i, lead)
            if band is not None:
                banded += 1
                inside += band[0] <= line[i] <= band[1]      # type: ignore[operator]
        out.append(CarbonAccuracy(region, lead, len(issued), _mean(absolute), _mean(naive),
                                  100.0 * inside / banded if banded else 0.0))
    return out


@dataclass(frozen=True)
class DemandAccuracy:
    hours: int
    mape_pct: float        # mean absolute error of DF, as a share of actual demand
    bias_pct: float        # positive: DF runs above actual demand
    band_coverage_pct: float


def _relative_errors(actual: Sequence[float | None],
                     forecast: Sequence[float | None]) -> list[float | None]:
    """(actual − DF) / DF per hour, ``None`` where either is missing."""
    return [None if a is None or f is None or f <= 0 else (a - f) / f
            for a, f in zip(actual, forecast)]


def demand_band(errors: Sequence[float | None], i: int) -> tuple[float, float] | None:
    """Relative band for DF at hours from ``i``, using outcomes known before hour ``i``."""
    oldest = max(0, i - _HOURS_PER_DAY * FORECAST_BAND_HISTORY_DAYS)
    past = [e for e in errors[oldest:i] if e is not None]
    if len(past) < FORECAST_BAND_MIN_SAMPLES:
        return None
    low, high = FORECAST_BAND_QUANTILES
    return quantile(past, low), quantile(past, high)


def demand_accuracy(actual: Sequence[float | None], forecast: Sequence[float | None]) -> DemandAccuracy:
    """How EIA's day-ahead ``DF`` did. The band for each hour stops a day before it."""
    errors = _relative_errors(actual, forecast)
    pairs = [(a, f) for a, f in zip(actual, forecast) if a is not None and f is not None and a > 0]
    inside = banded = 0
    for i, e in enumerate(errors):
        if e is None:
            continue
        band = demand_band(errors, max(0, i - _HOURS_PER_DAY + 1))
        if band is not None:
            banded += 1
            inside += band[0] <= e <= band[1]
    return DemandAccuracy(
        hours=len(pairs),
        mape_pct=100.0 * _mean([abs(f - a) / a for a, f in pairs]),
        bias_pct=100.0 * _mean([(f - a) / a for a, f in pairs]),
        band_coverage_pct=100.0 * inside / banded if banded else 0.0,
    )


class CarbonView:
    """What the planner believes about carbon, re-issued every hour (``PlanningGap.carbon_view``).

    The current hour is taken as known, as a site reading a live grid feed would see it,
    and later hours are forecast from the hours up to it. Issues are cached, so one view
    can serve every stress draw of a window.
    """

    def __init__(self, slots: Sequence[GridSlot], carbon: Mapping[str, Sequence[float]],
                 first_hour: int) -> None:
        """``carbon`` is hourly g/kWh per region over the data range; ``first_hour`` is the
        window's first hour in it."""
        self.n = len(slots)
        self.first_hour = first_hour
        self.carbon = carbon
        self.actual: dict[str, list[float]] = {
            HOME_REGION: [g.carbon_g_per_kwh for g in slots],
            **{r: [g.alt_carbon[r] for g in slots] for r in ALT_REGIONS},
        }
        self._issues: dict[int, dict[str, list[float]]] = {}

    def __call__(self, s: int) -> Mapping[str, Sequence[float]]:
        hour = s // SLOTS_PER_HOUR
        issue = self._issues.get(hour)
        if issue is None:
            issue = self._issues[hour] = {r: self._believed(r, hour) for r in self.actual}
        return issue

    def _believed(self, region: str, hour: int) -> list[float]:
        known = (hour + 1) * SLOTS_PER_HOUR
        hours_left = -(-(self.n - known) // SLOTS_PER_HOUR)
        out = self.actual[region][:known]
        if hours_left > 0:
            for value in carbon_forecast(self.carbon[region], self.first_hour + hour, hours_left):
                out.extend([value] * SLOTS_PER_HOUR)
        return out[:self.n]
