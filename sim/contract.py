"""Output_Contract: build, validate, serialize and atomically write Scenario files.

``build_doc`` turns one scenario's in-memory results into the Scenario_File
object (Requirement 16), with every dict built in contract key order so the
written file reads naturally (no ``sort_keys``). Every float goes through
``q()`` (round, ``-0.0 → 0.0``, finite check), so ``json.loads(dumps(doc)) ==
doc`` holds exactly (Requirement 16.14).

``validate(doc)`` checks every rule of Requirements 16.2–16.11 and returns a
list of error strings, each prefixed with the JSON path it refers to; an empty
list means the document conforms. ``run_scenarios`` validates every scenario in
memory before anything is written (Requirement 16.15).

``write_atomic`` writes ``<name>.json.tmp`` and then ``os.replace``s it over the
target, removing the temp file if anything fails, so a Scenario_File is either
the old file or the complete new one, never a partial write.

The per-object key sets are module constants (``TOP_KEYS``, ``META_KEYS``, …,
collected in ``INTERFACE_KEYS`` by ``web/src/types.ts`` interface name) so the
TypeScript mirror test (Requirement 16.12) can compare against them directly.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from sim.config import (
    ALT_REGIONS,
    DR_ZOOM_SECONDS,
    ENABLE_MOVE_REGION,
    PUE,
    ROUND,
    SLOT_MINUTES,
    SLOTS_PER_HOUR,
    WINDOW_HOURS,
)
from sim.grid import stress_count
from sim.timeutil import iso_z

if TYPE_CHECKING:
    from sim.scheduler import PolicyResult, ScenarioInputs
    from sim.site import ZoomEntry

__all__ = [
    "SCENARIO_NAMES",
    "ROUTES",
    "MOVE_TARGETS",
    "N_SLOTS",
    "ZOOM_SECONDS",
    "UNITS",
    "TOP_KEYS",
    "META_KEYS",
    "SOURCES",
    "SOURCES_KEYS",
    "PARAMS_KEYS",
    "GRID_KEYS",
    "BASELINE_KEYS",
    "HEADROOM_KEYS",
    "DECISION_KEYS",
    "DR_EVENT_KEYS",
    "ZOOM_KEYS",
    "METRICS_KEYS",
    "PCT_METRICS",
    "INTERFACE_KEYS",
    "DrLike",
    "q",
    "build_params",
    "build_doc",
    "validate",
    "dumps",
    "write_atomic",
]

# --- Contract constants (Requirement 16) -------------------------------------

SCENARIO_NAMES: tuple[str, ...] = ("peak", "sun")
ROUTES: tuple[str, ...] = ("GRID_NOW", "STORAGE_NOW", "DEFER", "MOVE_REGION")
MOVE_TARGETS: tuple[str, ...] = tuple(ALT_REGIONS)          # ("BPAT", "CISO")
N_SLOTS: int = WINDOW_HOURS * SLOTS_PER_HOUR                 # 192
ZOOM_SECONDS: int = DR_ZOOM_SECONDS                          # 600
UNITS: str = f"facility kW (IT × PUE {PUE})"

TOP_KEYS: tuple[str, ...] = (
    "meta", "grid", "baseline", "headroom", "decisions", "dr_event", "metrics",
)
META_KEYS: tuple[str, ...] = ("scenario", "window", "seed", "built_at", "sources", "params")
SOURCES: Mapping[str, str] = MappingProxyType({
    "grid": "REAL: EIA-930 PACE",
    "site": "SIMULATED",
    "jobs": "SIMULATED",
    "storage": "SIMULATED",
    "tariff": "ILLUSTRATIVE",
})
SOURCES_KEYS: tuple[str, ...] = tuple(SOURCES)
# meta.params is free-form in the contract; build_doc always writes these first.
PARAMS_KEYS: tuple[str, ...] = (
    "filled_hours", "peak_target_kw", "job_count", "enable_move_region",
    "dr_delivered_raw_pct", "stress_threshold_mw", "units",
)
GRID_KEYS: tuple[str, ...] = (
    "t", "pace_demand_mw", "pace_forecast_mw", "carbon_g_per_kwh", "price_per_kwh", "stress",
)
BASELINE_KEYS: tuple[str, ...] = ("t", "grid_kw", "inference_kw", "training_kw")
HEADROOM_KEYS: tuple[str, ...] = BASELINE_KEYS + ("battery_kw", "flywheel_kw", "soc_pct", "moved_kw")
DECISION_KEYS: tuple[str, ...] = ("t", "job", "route", "target", "reason")
DR_EVENT_KEYS: tuple[str, ...] = ("start", "end", "requested_shed_kw", "cap_kw", "zoom")
ZOOM_KEYS: tuple[str, ...] = ("s", "grid_kw", "flywheel_kw", "battery_kw")
METRICS_KEYS: tuple[str, ...] = (
    "headroom_kw", "usd_saved", "co2_avoided_t", "deadlines_met_pct",
    "inference_sla_pct", "dr_delivered_pct", "dr_seconds_to_full",
)
PCT_METRICS: tuple[str, ...] = tuple(k for k in METRICS_KEYS if k.endswith("_pct"))

# Key sets by the interface name they have in web/src/types.ts (Requirement 16.12).
INTERFACE_KEYS: Mapping[str, tuple[str, ...]] = MappingProxyType({
    "Scenario": TOP_KEYS,
    "Meta": META_KEYS,
    "Sources": SOURCES_KEYS,
    "GridPoint": GRID_KEYS,
    "BaselinePoint": BASELINE_KEYS,
    "HeadroomPoint": HEADROOM_KEYS,
    "Decision": DECISION_KEYS,
    "DrEvent": DR_EVENT_KEYS,
    "ZoomPoint": ZOOM_KEYS,
    "Metrics": METRICS_KEYS,
})

_SLOT = timedelta(minutes=SLOT_MINUTES)
_ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")

# Rounding class (key of config.ROUND) per metric; dr_seconds_to_full is an int.
_METRIC_ROUND: Mapping[str, str] = MappingProxyType({
    "headroom_kw": "kw",
    "usd_saved": "usd",
    "co2_avoided_t": "t",
    "deadlines_met_pct": "pct",
    "inference_sla_pct": "pct",
    "dr_delivered_pct": "pct",
})


class DrLike(Protocol):
    """What ``build_doc`` reads from the DR result (design ``DrResult``)."""

    start: datetime
    end: datetime
    requested_shed_kw: float
    cap_kw: float
    zoom: Sequence["ZoomEntry"]


# --- Rounding ----------------------------------------------------------------

def q(x: float, nd: int) -> float:
    """``round(x, nd)`` as a float, with ``-0.0`` mapped to ``0.0``.

    Raises ``ValueError`` for NaN or ±inf, which JSON cannot carry
    (an explicit raise rather than ``assert`` so ``python -O`` keeps the check).
    """
    v = float(x)
    if not math.isfinite(v):
        raise ValueError(f"cannot write a non-finite number to a Scenario_File: {x!r}")
    r = round(v, nd)
    return 0.0 if r == 0 else r


def _kw(x: float) -> float:
    return q(x, ROUND["kw"])


# --- Building ----------------------------------------------------------------

def _get(obj: Any, key: str) -> Any:
    """``obj[key]`` for mappings, else ``obj.key`` (dataclass or plain object)."""
    if isinstance(obj, Mapping):
        return obj[key]
    return getattr(obj, key)


def _has(obj: Any, key: str) -> bool:
    if isinstance(obj, Mapping):
        return key in obj
    return hasattr(obj, key)


def _stress_threshold_mw(inp: "ScenarioInputs") -> float:
    """Lowest PACE demand among the Stress_Slots (the threshold that selected them)."""
    values = [s.pace_demand_mw for s in inp.slots if s.stress]
    if not values:
        raise ValueError(f"{inp.name}: no Stress_Slots, so no stress threshold to report")
    return min(values)


def build_params(inp: "ScenarioInputs", metrics: Any, params: Mapping[str, Any]) -> dict[str, Any]:
    """``meta.params`` in ``PARAMS_KEYS`` order, followed by any extra caller keys.

    ``params`` must supply ``filled_hours`` (series name → filled hour count,
    Requirement 7.13) and ``peak_target_kw`` (the scheduler's ``P0``).
    ``dr_delivered_raw_pct`` (the unclamped DR percentage, DECISIONS.md) comes
    from ``params`` or, failing that, from ``metrics``. ``job_count``,
    ``enable_move_region``, ``stress_threshold_mw`` and ``units`` default to
    values derived from ``inp`` and the Config_Module; a caller value overrides.
    """
    given = dict(params)
    if "dr_delivered_raw_pct" not in given and _has(metrics, "dr_delivered_raw_pct"):
        given["dr_delivered_raw_pct"] = _get(metrics, "dr_delivered_raw_pct")
    missing = [k for k in ("filled_hours", "peak_target_kw", "dr_delivered_raw_pct")
               if k not in given]
    if missing:
        raise ValueError(f"{inp.name}: meta.params is missing {', '.join(missing)}")

    out: dict[str, Any] = {
        "filled_hours": {str(k): int(v) for k, v in given["filled_hours"].items()},
        "peak_target_kw": _kw(given["peak_target_kw"]),
        "job_count": int(given.get("job_count", len(inp.jobs))),
        "enable_move_region": bool(given.get("enable_move_region", ENABLE_MOVE_REGION)),
        "dr_delivered_raw_pct": q(given["dr_delivered_raw_pct"], ROUND["pct"]),
        "stress_threshold_mw": q(
            given["stress_threshold_mw"] if "stress_threshold_mw" in given
            else _stress_threshold_mw(inp),
            ROUND["mw"],
        ),
        "units": str(given.get("units", UNITS)),
    }
    for k, v in given.items():
        if k not in out:
            out[k] = v
    return out


def _target(target: Any) -> Any:
    return iso_z(target) if isinstance(target, datetime) else target


def build_doc(
    name: str,
    inp: "ScenarioInputs",
    base: "PolicyResult",
    head: "PolicyResult",
    dr: DrLike,
    metrics: Any,
    built_at: str | datetime,
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """The Scenario_File object for one scenario, in contract key order.

    ``metrics`` is ``metrics.compute``'s result, as a mapping or an object with
    the ``METRICS_KEYS`` attributes. ``meta.window`` is the first slot start and
    the start plus N slots (48 hours for N = 192, Requirement 9.5). Decisions
    are sorted stably by ``t`` (Requirement 16.8). Nothing is clamped or
    checked here; ``validate`` reports any contract violation.
    """
    if not inp.slots:
        raise ValueError(f"{name}: no slots to build a Scenario_File from")
    t0 = inp.slots[0].t
    window = [iso_z(t0), iso_z(t0 + len(inp.slots) * _SLOT)]

    meta = {
        "scenario": name,
        "window": window,
        "seed": int(inp.seed),
        "built_at": iso_z(built_at) if isinstance(built_at, datetime) else built_at,
        "sources": dict(SOURCES),
        "params": build_params(inp, metrics, params),
    }
    grid = [
        {
            "t": iso_z(s.t),
            "pace_demand_mw": q(s.pace_demand_mw, ROUND["mw"]),
            "pace_forecast_mw": q(s.pace_forecast_mw, ROUND["mw"]),
            "carbon_g_per_kwh": q(s.carbon_g_per_kwh, ROUND["carbon"]),
            "price_per_kwh": q(s.price_per_kwh, ROUND["price"]),
            "stress": bool(s.stress),
        }
        for s in inp.slots
    ]
    baseline = [
        {
            "t": iso_z(r.t),
            "grid_kw": _kw(r.grid_kw),
            "inference_kw": _kw(r.inference_kw),
            "training_kw": _kw(r.training_kw),
        }
        for r in base.slots
    ]
    headroom = [
        {
            "t": iso_z(r.t),
            "grid_kw": _kw(r.grid_kw),
            "inference_kw": _kw(r.inference_kw),
            "training_kw": _kw(r.training_kw),
            "battery_kw": _kw(r.battery_kw),
            "flywheel_kw": _kw(r.flywheel_kw),
            "soc_pct": q(r.battery_soc_pct, ROUND["pct"]),
            "moved_kw": _kw(r.moved_kw),
        }
        for r in head.slots
    ]
    decisions = [
        {
            "t": iso_z(d.t),
            "job": d.job,
            "route": d.route,
            "target": _target(d.target),
            "reason": d.reason,
        }
        for d in sorted(head.decisions, key=lambda d: d.t)
    ]
    dr_event = {
        "start": iso_z(dr.start),
        "end": iso_z(dr.end),
        "requested_shed_kw": _kw(dr.requested_shed_kw),
        "cap_kw": _kw(dr.cap_kw),
        "zoom": [
            {
                "s": int(z.s),
                "grid_kw": _kw(z.grid_kw),
                "flywheel_kw": _kw(z.flywheel_kw),
                "battery_kw": _kw(z.battery_kw),
            }
            for z in dr.zoom
        ],
    }
    out_metrics: dict[str, Any] = {}
    for key in METRICS_KEYS:
        v = _get(metrics, key)
        out_metrics[key] = int(v) if key == "dr_seconds_to_full" else q(v, ROUND[_METRIC_ROUND[key]])

    return {
        "meta": meta,
        "grid": grid,
        "baseline": baseline,
        "headroom": headroom,
        "decisions": decisions,
        "dr_event": dr_event,
        "metrics": out_metrics,
    }


# --- Validation --------------------------------------------------------------

def _type_name(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "boolean"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _parse_iso(v: Any) -> datetime | None:
    """A ``YYYY-MM-DDTHH:MM:SSZ`` string as an aware UTC datetime, else ``None``."""
    if not isinstance(v, str) or _ISO_RE.fullmatch(v) is None:
        return None
    try:
        return datetime.strptime(v, _ISO_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


class _Checker:
    """Collects ``"<path>: <problem>"`` strings."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def err(self, path: str, msg: str) -> None:
        self.errors.append(f"{path}: {msg}")

    def obj(self, v: Any, path: str, keys: Sequence[str]) -> bool:
        """Exact key set (Requirements 16.2–16.10). False if ``v`` is not an object."""
        if not isinstance(v, dict):
            self.err(path, f"expected an object, got {_type_name(v)}")
            return False
        for k in keys:
            if k not in v:
                self.err(path, f"missing key {k!r}")
        for k in v:
            if k not in keys:
                self.err(path, f"unexpected key {k!r}")
        return True

    def array(self, v: Any, path: str, length: int | None = None) -> bool:
        if not isinstance(v, list):
            self.err(path, f"expected an array, got {_type_name(v)}")
            return False
        if length is not None and len(v) != length:
            self.err(path, f"expected {length} entries, got {len(v)}")
        return True

    def number(self, d: dict, key: str, path: str,
               lo: float | None = None, hi: float | None = None) -> float | None:
        if key not in d:
            return None
        v = d[key]
        if not _is_number(v):
            self.err(f"{path}.{key}", f"expected a finite number, got {_type_name(v)} {v!r}")
            return None
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            self.err(f"{path}.{key}", f"{v!r} outside [{lo}, {hi}]")
        return float(v)

    def ts(self, d: dict, key: str, path: str) -> datetime | None:
        """UTC ISO 8601 ``Z`` timestamp (Requirement 16.11)."""
        if key not in d:
            return None
        return self.ts_value(d[key], f"{path}.{key}")

    def ts_value(self, v: Any, path: str) -> datetime | None:
        t = _parse_iso(v)
        if t is None:
            self.err(path, f"expected a UTC timestamp YYYY-MM-DDTHH:MM:SSZ, got {v!r}")
        return t

    def slot_time(self, t: datetime | None, path: str, w0: datetime | None) -> None:
        if t is not None and w0 is not None and (t - w0) % _SLOT:
            self.err(path, f"{iso_z(t)} is not on the {SLOT_MINUTES}-minute slot grid")


def _check_meta(c: _Checker, meta: Any, n: int | None,
                scenario: str | None) -> tuple[datetime | None, datetime | None]:
    """Requirements 9.5, 16.3, 16.4. Returns the parsed window (or Nones)."""
    w0 = w1 = None
    if not c.obj(meta, "meta", META_KEYS):
        return w0, w1
    if "scenario" in meta:
        name = meta["scenario"]
        if name not in SCENARIO_NAMES:
            c.err("meta.scenario", f"expected one of {list(SCENARIO_NAMES)}, got {name!r}")
        elif scenario is not None and name != scenario:
            c.err("meta.scenario", f"expected {scenario!r} to match the file name, got {name!r}")
    if "window" in meta:
        window = meta["window"]
        if c.array(window, "meta.window", 2) and len(window) == 2:
            w0, w1 = (c.ts_value(window[j], f"meta.window[{j}]") for j in (0, 1))
            if w0 is not None and w1 is not None and n is not None and w1 - w0 != n * _SLOT:
                c.err("meta.window", f"end must be start + {n * SLOT_MINUTES // 60} h "
                                     f"({n} slots), got {window[0]} to {window[1]}")
    if "seed" in meta and (not isinstance(meta["seed"], int) or isinstance(meta["seed"], bool)):
        c.err("meta.seed", f"expected an integer, got {_type_name(meta['seed'])} {meta['seed']!r}")
    c.ts(meta, "built_at", "meta")
    if "sources" in meta and c.obj(meta["sources"], "meta.sources", SOURCES_KEYS):
        for k, want in SOURCES.items():
            if k in meta["sources"] and meta["sources"][k] != want:
                c.err(f"meta.sources.{k}", f"expected {want!r}, got {meta['sources'][k]!r}")
    if "params" in meta and not isinstance(meta["params"], dict):
        c.err("meta.params", f"expected an object, got {_type_name(meta['params'])}")
    return w0, w1


def _check_grid(c: _Checker, grid: Any, n: int | None,
                w0: datetime | None) -> list[str | None] | None:
    """Requirement 16.5. Returns the entries' ``t`` strings (None where invalid)."""
    if not c.array(grid, "grid", n):
        return None
    ts: list[str | None] = []
    parsed: list[datetime | None] = []
    demand: list[float | None] = []
    stress: list[bool | None] = []
    for i, e in enumerate(grid):
        path = f"grid[{i}]"
        if not c.obj(e, path, GRID_KEYS):
            ts.append(None); parsed.append(None); demand.append(None); stress.append(None)
            continue
        t = c.ts(e, "t", path)
        parsed.append(t)
        ts.append(e["t"] if t is not None else None)
        demand.append(c.number(e, "pace_demand_mw", path))
        for k in ("pace_forecast_mw", "carbon_g_per_kwh", "price_per_kwh"):
            c.number(e, k, path)
        flag = e.get("stress")
        if "stress" in e and not isinstance(flag, bool):
            c.err(f"{path}.stress", f"expected a boolean, got {_type_name(flag)}")
            flag = None
        stress.append(flag)

    if parsed and parsed[0] is not None and w0 is not None and parsed[0] != w0:
        c.err("grid[0].t", f"expected meta.window start {iso_z(w0)}, got {grid[0]['t']}")
    for i in range(1, len(parsed)):
        a, b = parsed[i - 1], parsed[i]
        if a is not None and b is not None and b - a != _SLOT:
            c.err(f"grid[{i}].t", f"expected {iso_z(a + _SLOT)} ({SLOT_MINUTES} minutes after "
                                  f"grid[{i - 1}].t), got {iso_z(b)}")

    # Stress_Slots are the slots at or above the k-th largest demand (ties
    # included). Rounding is monotone, so stress demands must still be >= every
    # non-stress demand, and there must be at least k of them.
    if grid and None not in demand and None not in stress:
        k = stress_count(len(grid))
        on = [d for d, s in zip(demand, stress) if s]
        off = [d for d, s in zip(demand, stress) if not s]
        if len(on) < k:
            c.err("grid", f"expected at least {k} stress slots, got {len(on)}")
        if on and off and max(off) > min(on):
            c.err("grid", f"stress flags disagree with demand: a non-stress slot has "
                          f"{max(off)} MW, above the stress slot minimum {min(on)} MW")
    return ts


def _check_series(c: _Checker, rows: Any, name: str, keys: Sequence[str],
                  grid_t: list[str | None] | None, n: int | None) -> None:
    """Requirements 16.6 (baseline) and 16.7 (headroom)."""
    if not c.array(rows, name, n):
        return
    for i, e in enumerate(rows):
        path = f"{name}[{i}]"
        if not c.obj(e, path, keys):
            continue
        t = c.ts(e, "t", path)
        if t is not None and grid_t is not None and i < len(grid_t) \
                and grid_t[i] is not None and e["t"] != grid_t[i]:
            c.err(f"{path}.t", f"expected grid[{i}].t {grid_t[i]}, got {e['t']}")
        if t is not None and grid_t is not None and i >= len(grid_t):
            c.err(f"{path}.t", "no grid entry at this index")
        for k in keys[1:]:
            if k == "soc_pct":
                c.number(e, k, path, 0, 100)
            else:
                c.number(e, k, path)


def _check_decisions(c: _Checker, decisions: Any,
                     w0: datetime | None, w1: datetime | None) -> None:
    """Requirement 16.8."""
    if not c.array(decisions, "decisions"):
        return
    prev: datetime | None = None
    for i, e in enumerate(decisions):
        path = f"decisions[{i}]"
        if not c.obj(e, path, DECISION_KEYS):
            continue
        t = c.ts(e, "t", path)
        if t is not None:
            if w0 is not None and w1 is not None and not w0 <= t < w1:
                c.err(f"{path}.t", f"{e['t']} is outside meta.window")
            c.slot_time(t, f"{path}.t", w0)
            if prev is not None and t < prev:
                c.err(f"{path}.t", f"{e['t']} is earlier than the previous decision")
            prev = t
        if "job" in e and (not isinstance(e["job"], str) or not e["job"]):
            c.err(f"{path}.job", f"expected a non-empty job id string, got {e['job']!r}")
        if "reason" in e and (not isinstance(e["reason"], str) or not e["reason"].strip()):
            c.err(f"{path}.reason", f"expected a non-empty string, got {e['reason']!r}")
        route = e.get("route")
        if "route" in e and route not in ROUTES:
            c.err(f"{path}.route", f"expected one of {list(ROUTES)}, got {route!r}")
            continue
        if "target" not in e or "route" not in e:
            continue
        target = e["target"]
        if route == "DEFER":
            tt = _parse_iso(target)
            if tt is None:
                c.err(f"{path}.target", f"DEFER needs a UTC timestamp YYYY-MM-DDTHH:MM:SSZ, "
                                        f"got {target!r}")
            else:
                if t is not None and tt <= t:
                    c.err(f"{path}.target", f"DEFER target {target} is not later than t {e['t']}")
                c.slot_time(tt, f"{path}.target", w0)
        elif route == "MOVE_REGION":
            if target not in MOVE_TARGETS:
                c.err(f"{path}.target", f"MOVE_REGION needs one of {list(MOVE_TARGETS)}, "
                                        f"got {target!r}")
        elif target is not None:
            c.err(f"{path}.target", f"{route} needs null, got {target!r}")


def _check_dr(c: _Checker, dr: Any, w0: datetime | None, w1: datetime | None) -> None:
    """Requirement 16.9."""
    if not c.obj(dr, "dr_event", DR_EVENT_KEYS):
        return
    start = c.ts(dr, "start", "dr_event")
    end = c.ts(dr, "end", "dr_event")
    if w0 is not None and w1 is not None:
        if start is not None and not w0 <= start < w1:
            c.err("dr_event.start", f"{dr['start']} is outside meta.window")
        if end is not None and not w0 < end <= w1:
            c.err("dr_event.end", f"{dr['end']} is outside meta.window")
    c.slot_time(start, "dr_event.start", w0)
    c.slot_time(end, "dr_event.end", w0)
    if start is not None and end is not None and not start < end:
        c.err("dr_event", f"start {dr['start']} must be earlier than end {dr['end']}")
    shed = c.number(dr, "requested_shed_kw", "dr_event")
    if shed is not None and shed <= 0:
        c.err("dr_event.requested_shed_kw", f"expected a positive kW value, got {shed}")
    c.number(dr, "cap_kw", "dr_event")
    if "zoom" not in dr or not c.array(dr["zoom"], "dr_event.zoom", ZOOM_SECONDS):
        return
    for i, e in enumerate(dr["zoom"]):
        path = f"dr_event.zoom[{i}]"
        if not c.obj(e, path, ZOOM_KEYS):
            continue
        if "s" in e:
            s = e["s"]
            if not isinstance(s, int) or isinstance(s, bool) or s != i:
                c.err(f"{path}.s", f"expected the integer {i}, got {s!r}")
        for k in ZOOM_KEYS[1:]:
            c.number(e, k, path)


def _check_metrics(c: _Checker, metrics: Any) -> None:
    """Requirement 16.10."""
    if not c.obj(metrics, "metrics", METRICS_KEYS):
        return
    for k in METRICS_KEYS:
        if k in PCT_METRICS:
            c.number(metrics, k, "metrics", 0, 100)
        elif k == "dr_seconds_to_full":
            c.number(metrics, k, "metrics", 0, ZOOM_SECONDS)
        else:
            c.number(metrics, k, "metrics")


def validate(doc: Any, *, n_slots: int | None = N_SLOTS,
             scenario: str | None = None) -> list[str]:
    """Every Output_Contract violation in ``doc`` (Requirements 9.5, 16.2–16.11).

    Returns an empty list when ``doc`` conforms. ``n_slots`` is the expected
    length of ``grid``, ``baseline`` and ``headroom`` (192 for a Scenario_File,
    so ``meta.window`` spans 48 hours); pass ``None`` to take it from ``grid``,
    for property tests over shorter windows. ``scenario``, when given, is the
    name ``meta.scenario`` must match (the Scenario_File name).
    """
    c = _Checker()
    if not c.obj(doc, "$", TOP_KEYS):
        return c.errors
    n = n_slots
    if n is None and isinstance(doc.get("grid"), list):
        n = len(doc["grid"])

    w0, w1 = _check_meta(c, doc["meta"], n, scenario) if "meta" in doc else (None, None)
    grid_t = _check_grid(c, doc["grid"], n, w0) if "grid" in doc else None
    if "baseline" in doc:
        _check_series(c, doc["baseline"], "baseline", BASELINE_KEYS, grid_t, n)
    if "headroom" in doc:
        _check_series(c, doc["headroom"], "headroom", HEADROOM_KEYS, grid_t, n)
    if "decisions" in doc:
        _check_decisions(c, doc["decisions"], w0, w1)
    if "dr_event" in doc:
        _check_dr(c, doc["dr_event"], w0, w1)
    if "metrics" in doc:
        _check_metrics(c, doc["metrics"])
    return c.errors


# --- Serialization -----------------------------------------------------------

def dumps(doc: Mapping[str, Any]) -> str:
    """Compact UTF-8 JSON plus a trailing newline; NaN/inf raise ``ValueError``."""
    return json.dumps(doc, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"


def write_atomic(path: Path | str, text: str) -> None:
    """Write ``text`` to ``path`` through ``<path>.json.tmp`` and ``os.replace``.

    On any failure the temp file is removed and the exception re-raised, so an
    existing file at ``path`` is left unchanged and no partial file remains
    (Requirement 16.15).
    """
    path = Path(path)
    tmp = path.with_suffix(".json.tmp")
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise
