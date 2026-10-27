"""Site model: tariff, inference profile, storage and idle capacity.

The tariff is ILLUSTRATIVE, not Rocky Mountain Power's actual rates
(Requirement 10.16). On-peak is Monday through Friday, local hours in
``ON_PEAK_HOURS_LOCAL`` = [start, end) America/Denver; every other slot pays
the off-peak rate. Rates and hours come from ``sim.config``.

Inference load follows a raised-cosine daily profile peaking at
``IT_PEAK_KW * INFERENCE_SHARE`` at ``INFERENCE_PEAK_HOUR_LOCAL`` Denver time,
with an overnight floor of ``INFERENCE_MIN_FRACTION`` of the peak
(Requirements 10.4, 10.5).

Storage splits round-trip efficiency evenly, ``eta = sqrt(rte)`` on charge
and on discharge, so a full charge then discharge returns exactly ``rte`` of
the energy drawn (Requirements 10.11, 10.12).

``build_event_zoom`` produces the 1-second Event_Zoom for the first
``DR_ZOOM_SECONDS`` of a DR_Event: the flywheel takes the full step at s = 0
while the battery ramps over ``DR_BATTERY_RAMP_S`` (Requirements 14.5–14.8).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from sim.config import (
    ALT_REGIONS,
    BATTERY,
    DR_BATTERY_RAMP_S,
    DR_ZOOM_SECONDS,
    FLYWHEEL,
    IDLE_CAPACITY_PCT,
    INFERENCE_MIN_FRACTION,
    INFERENCE_PEAK_HOUR_LOCAL,
    INFERENCE_SHARE,
    IT_PEAK_KW,
    ON_PEAK_HOURS_LOCAL,
    ROUND,
    TARIFF_OFFPEAK_PER_KWH,
    TARIFF_ONPEAK_PER_KWH,
)
from sim.jobs import rng_for
from sim.timeutil import denver

__all__ = [
    "is_on_peak",
    "tariff_per_kwh",
    "inference_it_kw",
    "StorageSpec",
    "StorageState",
    "idle_capacity_kw",
    "IDLE_PURPOSE_PREFIX",
    "ZoomEntry",
    "DrInfeasibleError",
    "build_event_zoom",
]

# Purpose string for rng_for; one independent stream per Alternate_Region.
IDLE_PURPOSE_PREFIX = "idle_capacity"

_HOURS_PER_DAY = 24
# Float tolerance for storage bound checks (kWh / kW). Values inside the
# tolerance are clamped onto the bound so rounding cannot drift SOC outward.
_EPS = 1e-6

_FRIDAY = 4  # datetime.weekday(): Monday=0 ... Sunday=6


def is_on_peak(t: datetime) -> bool:
    """True if the aware instant ``t`` falls in the On_Peak_Period in Denver local time."""
    local = denver(t)
    start, end = ON_PEAK_HOURS_LOCAL
    return local.weekday() <= _FRIDAY and start <= local.hour < end


def tariff_per_kwh(t: datetime) -> float:
    """Energy rate in $/kWh for a slot starting at the aware instant ``t``."""
    return TARIFF_ONPEAK_PER_KWH if is_on_peak(t) else TARIFF_OFFPEAK_PER_KWH


# --- Inference load (Requirements 10.4, 10.5)

def inference_it_kw(slot_times: Sequence[datetime]) -> list[float]:
    """Inference IT kW for each aware slot start, a daily raised cosine in Denver local time.

    ``peak × (m + (1 − m) × 0.5 × (1 + cos(2π (h − h_peak) / 24)))`` with
    ``h`` the Denver hour plus minute/60: exactly the peak at
    ``INFERENCE_PEAK_HOUR_LOCAL`` and ``m × peak`` twelve hours later.
    """
    peak_kw = IT_PEAK_KW * INFERENCE_SHARE
    m = INFERENCE_MIN_FRACTION
    out: list[float] = []
    for t in slot_times:
        local = denver(t)
        h = local.hour + local.minute / 60
        phase = 2 * math.pi * (h - INFERENCE_PEAK_HOUR_LOCAL) / _HOURS_PER_DAY
        out.append(peak_kw * (m + (1 - m) * 0.5 * (1 + math.cos(phase))))
    return out


# --- Storage (Requirements 10.11, 10.12, 10.13, 10.14)

@dataclass(frozen=True)
class StorageSpec:
    name: str
    power_kw: float
    capacity_kwh: float
    rte: float
    soc_min_pct: float
    soc_max_pct: float
    soc_init_pct: float

    @classmethod
    def from_config(cls, name: str, cfg: Mapping[str, float]) -> "StorageSpec":
        """Build from a config dict such as ``sim.config.BATTERY``."""
        return cls(
            name=name,
            power_kw=cfg["power_kw"],
            capacity_kwh=cfg["capacity_kwh"],
            rte=cfg["rte"],
            soc_min_pct=cfg["soc_min_pct"],
            soc_max_pct=cfg["soc_max_pct"],
            soc_init_pct=cfg["soc_init_pct"],
        )

    @property
    def eta(self) -> float:
        """One-way efficiency, applied on both charge and discharge."""
        return math.sqrt(self.rte)

    @property
    def soc_min_kwh(self) -> float:
        return self.capacity_kwh * self.soc_min_pct / 100

    @property
    def soc_max_kwh(self) -> float:
        return self.capacity_kwh * self.soc_max_pct / 100

    def initial_state(self) -> "StorageState":
        return StorageState(soc_kwh=self.capacity_kwh * self.soc_init_pct / 100)


@dataclass(frozen=True)
class StorageState:
    soc_kwh: float

    def soc_pct(self, spec: StorageSpec) -> float:
        return 100 * self.soc_kwh / spec.capacity_kwh

    def max_discharge_kw(
        self, spec: StorageSpec, hours: float, floor_kwh: float | None = None
    ) -> float:
        """Largest discharge (kW delivered) for ``hours`` that keeps SOC at or above ``floor_kwh``.

        ``floor_kwh`` defaults to the spec's SOC floor and may not go below it.
        """
        _require_positive_hours(hours)
        floor = spec.soc_min_kwh if floor_kwh is None else max(floor_kwh, spec.soc_min_kwh)
        return min(spec.power_kw, max(0.0, self.soc_kwh - floor) * spec.eta / hours)

    def max_charge_kw(
        self, spec: StorageSpec, hours: float, ceil_kwh: float | None = None
    ) -> float:
        """Largest charge (kW drawn) for ``hours`` that keeps SOC at or below ``ceil_kwh``.

        ``ceil_kwh`` defaults to the spec's SOC ceiling and may not go above it.
        """
        _require_positive_hours(hours)
        ceil = spec.soc_max_kwh if ceil_kwh is None else min(ceil_kwh, spec.soc_max_kwh)
        return min(spec.power_kw, max(0.0, ceil - self.soc_kwh) / (spec.eta * hours))

    def step(
        self, spec: StorageSpec, charge_kw: float, discharge_kw: float, hours: float
    ) -> "StorageState":
        """State after charging (kW drawn) or discharging (kW delivered) for ``hours``.

        Raises ``ValueError`` if both are non-zero (Requirement 10.14), if either
        is negative or above the power rating, or if the new SOC would leave
        [soc_min, soc_max] (Requirement 10.13).
        """
        _require_positive_hours(hours)
        if charge_kw != 0 and discharge_kw != 0:
            raise ValueError(
                f"{spec.name}: cannot charge ({charge_kw} kW) and discharge "
                f"({discharge_kw} kW) in the same step"
            )
        for label, kw in (("charge", charge_kw), ("discharge", discharge_kw)):
            if kw < 0 or kw > spec.power_kw + _EPS:
                raise ValueError(
                    f"{spec.name}: {label} {kw} kW outside [0, {spec.power_kw}] kW"
                )
        soc = self.soc_kwh + charge_kw * hours * spec.eta - discharge_kw * hours / spec.eta
        lo, hi = spec.soc_min_kwh, spec.soc_max_kwh
        if soc < lo - _EPS or soc > hi + _EPS:
            raise ValueError(
                f"{spec.name}: SOC {soc:.6f} kWh outside [{lo}, {hi}] kWh"
            )
        return StorageState(soc_kwh=min(hi, max(lo, soc)))


def _require_positive_hours(hours: float) -> None:
    if not hours > 0:
        raise ValueError(f"step duration must be positive hours: {hours}")


# --- Idle capacity in Alternate_Regions (Requirement 12.4)

def idle_capacity_kw(n_hours: int, seed: int, scenario: str) -> dict[str, list[float]]:
    """SIMULATED idle capacity per Alternate_Region per hour, in kW.

    Each value is an integer percentage drawn uniformly from
    ``IDLE_CAPACITY_PCT`` (inclusive) times ``IT_PEAK_KW``. Each region has its
    own ``rng_for`` stream, so results depend only on (seed, scenario, region).
    """
    if n_hours < 0:
        raise ValueError(f"hour count must be non-negative: {n_hours}")
    lo, hi = IDLE_CAPACITY_PCT
    out: dict[str, list[float]] = {}
    for region in ALT_REGIONS:
        rng = rng_for(seed, scenario, f"{IDLE_PURPOSE_PREFIX}:{region}")
        out[region] = [rng.randint(lo, hi) * IT_PEAK_KW / 100 for _ in range(n_hours)]
    return out


# --- Event zoom (Requirements 10.13, 10.14, 10.15, 14.5–14.8, 14.11)

_SECONDS_PER_HOUR = 3600
# Zoom values are integers in units of 10^-ROUND["kw"] kW (deci-kW), so the
# rounded grid, flywheel and battery values sum exactly to the rounded load.
_ZOOM_SCALE = 10 ** ROUND["kw"]


@dataclass(frozen=True)
class ZoomEntry:
    """One second of the Event_Zoom; becomes an entry of ``dr_event.zoom``."""

    s: int
    grid_kw: float
    flywheel_kw: float
    battery_kw: float


class DrInfeasibleError(RuntimeError):
    """The DR_Event cannot be served within storage ratings and SOC bounds (Requirement 14.11).

    ``second`` names the first violating Event_Zoom second; the scheduler sets
    ``slot`` instead for a violating DR slot. ``scenario`` is filled in by the
    Scenario_Builder when it reports the failure.
    """

    def __init__(
        self,
        second: int | None = None,
        *,
        slot: int | None = None,
        scenario: str | None = None,
        detail: str = "",
    ) -> None:
        self.second = second
        self.slot = slot
        self.scenario = scenario
        self.detail = detail
        where = []
        if scenario is not None:
            where.append(f"scenario {scenario!r}")
        if slot is not None:
            where.append(f"slot {slot}")
        if second is not None:
            where.append(f"second {second}")
        msg = "DR event infeasible"
        if where:
            msg += " at " + ", ".join(where)
        if detail:
            msg += f": {detail}"
        super().__init__(msg)


def build_event_zoom(
    site_load_kw: float,
    cap_kw: float,
    *,
    battery_state: StorageState | None = None,
    flywheel_state: StorageState | None = None,
) -> list[ZoomEntry]:
    """The ``DR_ZOOM_SECONDS``-entry, 1-second Event_Zoom from the DR_Event start.

    With ``R = max(0, L − cap)``, the battery ramps linearly from 0 at ``s = 0``
    to ``R`` at ``s = DR_BATTERY_RAMP_S`` and holds; the flywheel supplies
    ``R − battery``; the grid supplies ``L − R``. Arithmetic is in integer
    deci-kW: ``L`` is rounded to the output precision and, when ``R > 0``, the
    grid value is rounded down so ``grid_kw ≤ cap_kw`` holds exactly.

    SOC starts from ``battery_state`` / ``flywheel_state`` (default: each
    device's configured initial SOC) and is stepped once per second.

    Raises ``DrInfeasibleError`` naming the first violating second if ``cap_kw``
    is negative (grid draw cannot be both ≥ 0 and ≤ cap), if either device
    would exceed its power rating, or if either SOC would leave
    [soc_min, soc_max]. Raises ``ValueError`` for a negative or non-finite load.
    """
    if not (math.isfinite(site_load_kw) and site_load_kw >= 0):
        raise ValueError(f"site load must be finite and non-negative: {site_load_kw}")
    if not math.isfinite(cap_kw):
        raise ValueError(f"cap must be finite: {cap_kw}")
    if cap_kw < 0:
        raise DrInfeasibleError(
            0, detail=f"cap {cap_kw} kW is negative; grid draw must stay >= 0 kW"
        )

    load_d = round(site_load_kw * _ZOOM_SCALE)
    if site_load_kw <= cap_kw:
        grid_d = load_d
    else:
        grid_d = min(load_d, math.floor(cap_kw * _ZOOM_SCALE))
        if grid_d / _ZOOM_SCALE > cap_kw:  # float guard on the floor
            grid_d -= 1
    required_d = load_d - grid_d
    grid_kw = grid_d / _ZOOM_SCALE

    bat_spec = StorageSpec.from_config("battery", BATTERY)
    fly_spec = StorageSpec.from_config("flywheel", FLYWHEEL)
    bat = battery_state if battery_state is not None else bat_spec.initial_state()
    fly = flywheel_state if flywheel_state is not None else fly_spec.initial_state()
    hours = 1 / _SECONDS_PER_HOUR
    ramp = DR_BATTERY_RAMP_S

    entries: list[ZoomEntry] = []
    for s in range(DR_ZOOM_SECONDS):
        k = min(s, ramp)
        battery_d = (2 * required_d * k + ramp) // (2 * ramp)  # round half up
        flywheel_d = required_d - battery_d
        battery_kw = battery_d / _ZOOM_SCALE
        flywheel_kw = flywheel_d / _ZOOM_SCALE

        for spec, kw in ((fly_spec, flywheel_kw), (bat_spec, battery_kw)):
            if kw > spec.power_kw:
                raise DrInfeasibleError(
                    s,
                    detail=f"{spec.name} needs {kw} kW, above its {spec.power_kw} kW rating",
                )
        try:
            fly = fly.step(fly_spec, 0.0, flywheel_kw, hours)
            bat = bat.step(bat_spec, 0.0, battery_kw, hours)
        except ValueError as exc:
            raise DrInfeasibleError(s, detail=str(exc)) from exc

        entries.append(ZoomEntry(s=s, grid_kw=grid_kw, flywheel_kw=flywheel_kw, battery_kw=battery_kw))
    return entries
