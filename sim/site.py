"""Site model: tariff (inference profile, storage and event zoom follow).

The tariff is ILLUSTRATIVE, not Rocky Mountain Power's actual rates
(Requirement 10.16). On-peak is Monday through Friday, local hours in
``ON_PEAK_HOURS_LOCAL`` = [start, end) America/Denver; every other slot pays
the off-peak rate. Rates and hours come from ``sim.config``.
"""

from __future__ import annotations

from datetime import datetime

from sim.config import (
    ON_PEAK_HOURS_LOCAL,
    TARIFF_OFFPEAK_PER_KWH,
    TARIFF_ONPEAK_PER_KWH,
)
from sim.timeutil import denver

__all__ = ["is_on_peak", "tariff_per_kwh"]

_FRIDAY = 4  # datetime.weekday(): Monday=0 ... Sunday=6


def is_on_peak(t: datetime) -> bool:
    """True if the aware instant ``t`` falls in the On_Peak_Period in Denver local time."""
    local = denver(t)
    start, end = ON_PEAK_HOURS_LOCAL
    return local.weekday() <= _FRIDAY and start <= local.hour < end


def tariff_per_kwh(t: datetime) -> float:
    """Energy rate in $/kWh for a slot starting at the aware instant ``t``."""
    return TARIFF_ONPEAK_PER_KWH if is_on_peak(t) else TARIFF_OFFPEAK_PER_KWH
