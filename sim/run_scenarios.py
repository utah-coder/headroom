"""Scenario_Builder: ``python -m sim.run_scenarios``.

Builds every enabled Scenario from the committed Raw_Cache (offline: a missing
cache file is an error, never a fetch, and no EIA_API_KEY is needed) and
writes ``web/public/scenarios/{peak,sun}.json`` plus the hourly sanity series
in ``data/processed/`` (Requirements 7.7, 16.1).

Flow per scenario (design, "sim/run_scenarios.py"): select window → slots →
jobs → inference → idle capacity → baseline → DR start and ``cap_kw`` from the
baseline draw → Headroom → Event_Zoom → metrics → doc → validate.

``cap_kw`` is the Grid_Import_Cap: the baseline Site_Grid_Draw in the DR start
slot minus the Requested_Shed (Requirement 14.3). The Event_Zoom starts from the
Headroom run's site load in the DR start slot before storage (``L`` =
inference + training kW) and from the Battery and Flywheel SOC at the start of
that slot; ``R = max(0, L − cap_kw)`` (Requirements 14.5–14.8).

Every enabled scenario is built, checked and serialized in memory before any
file is touched. If one fails (missing data, infeasible DR event,
``inference_sla_pct < 100``, or an Output_Contract violation) the builder prints
an error naming the scenario, writes nothing and exits 1, so the existing
Scenario_Files stay unchanged (Requirements 14.11, 15.10, 16.15). Otherwise all
files go to ``*.json.tmp`` first and are then moved into place with
``os.replace``. With ``ENABLE_SUN_SCENARIO = False`` only ``peak.json`` is
written and a stale ``sun.json`` is removed (Requirement 24.7).

``meta.built_at`` is the build's UTC start time to the second, the same for
every file of one run; it is the only field that differs between two runs with
the same seed and Raw_Cache (Requirement 16.13).
"""

from __future__ import annotations

import dataclasses
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sim import config
from sim.carbon import hourly_intensity
from sim.config import (
    ALT_REGIONS,
    BATTERY,
    DR_DURATION_SLOTS,
    DR_SHED_KW,
    FLYWHEEL,
    HOME_REGION,
    SLOT_MINUTES,
    WINDOW_HOURS,
)
from sim.contract import build_doc, dumps, validate
from sim.fetch_eia import CacheMissingError, load_all
from sim.grid import (
    TOTAL,
    HourlySeries,
    NoDataError,
    build_hourly,
    build_slots,
    select_dr_start,
    select_peak_window,
    select_sun_window,
    write_processed,
)
from sim.jobs import generate
from sim.metrics import Metrics, NaiveMetrics, compute, compute_naive
from sim.scheduler import (
    PolicyResult,
    ScenarioInputs,
    peak_target_kw,
    run_baseline,
    run_headroom,
    run_naive,
)
from sim.site import (
    DrInfeasibleError,
    StorageSpec,
    StorageState,
    ZoomEntry,
    build_event_zoom,
    idle_capacity_kw,
    inference_it_kw,
)

__all__ = [
    "SCENARIOS_DIR",
    "ScenarioError",
    "GridData",
    "DrResult",
    "ScenarioBuild",
    "load_grid_data",
    "enabled_scenarios",
    "grid_import_cap_kw",
    "build_dr",
    "build_scenario",
    "build_scenario_full",
    "main",
]

SCENARIOS_DIR = Path(__file__).resolve().parent.parent / "web" / "public" / "scenarios"

_SLOT = timedelta(minutes=SLOT_MINUTES)
_SLA_PCT = 100.0
_MAX_ERRORS_SHOWN = 10


class ScenarioError(RuntimeError):
    """A scenario failed a build check (inference SLA or Output_Contract)."""

    def __init__(self, scenario: str, reason: str) -> None:
        self.scenario = scenario
        self.reason = reason
        super().__init__(f"scenario {scenario!r}: {reason}")


@dataclass(frozen=True)
class GridData:
    """The Raw_Cache turned into hourly series, shared by every scenario."""

    hourly: Mapping[str, HourlySeries]   # grid.build_hourly output
    carbon: Mapping[str, HourlySeries]   # region -> "<REGION>.CI" series


@dataclass(frozen=True)
class DrResult:
    """The DR_Event of one scenario (design, Data Models).

    ``site_load_kw`` is the Headroom site load in the DR start slot before
    storage and ``required_kw = max(0, site_load_kw − cap_kw)`` the storage
    output the Event_Zoom delivers, taken from the zoom itself (battery +
    flywheel in its last entry) so it carries the zoom's deci-kW rounding:
    the load rounded to 0.1 kW and the cap floored to 0.1 kW (DECISIONS.md).
    """

    start: datetime
    end: datetime
    requested_shed_kw: float
    cap_kw: float
    site_load_kw: float
    required_kw: float
    zoom: tuple[ZoomEntry, ...]


def load_grid_data(raw: Optional[Mapping[str, Sequence[Mapping]]] = None, *,
                   cache_dir: Path | None = None) -> GridData:
    """Hourly grid and carbon series from ``raw`` (default: the Raw_Cache, offline).

    ``cache_dir`` overrides the Raw_Cache location (default ``data/raw``).
    Raises ``CacheMissingError`` naming a missing cache file (no fetch is
    attempted) and ``NoDataError`` / ``NoFuelMixError`` for unusable series.
    """
    if raw is None:
        raw = load_all(offline=True, cache_dir=cache_dir)
    hourly = build_hourly(raw)
    carbon: dict[str, HourlySeries] = {}
    for region in (HOME_REGION, *ALT_REGIONS):
        carbon[region], _ = hourly_intensity(raw.get(f"fuel-type-data:{region}", []), region)
    return GridData(hourly=hourly, carbon=carbon)


def enabled_scenarios() -> tuple[str, ...]:
    """Scenario names to build, reading ``ENABLE_SUN_SCENARIO`` at call time."""
    return ("peak", "sun") if config.ENABLE_SUN_SCENARIO else ("peak",)


def _series(hourly: Mapping[str, HourlySeries], name: str) -> HourlySeries:
    s = hourly.get(name)
    if s is None:
        raise NoDataError(name, detail="required series is missing")
    return s


def _window_start(name: str, hourly: Mapping[str, HourlySeries]) -> datetime:
    if name == "peak":
        return select_peak_window(_series(hourly, f"{HOME_REGION}.D"))
    if name == "sun":
        return select_sun_window(_series(hourly, f"{HOME_REGION}.SUN"),
                                 _series(hourly, f"{HOME_REGION}.{TOTAL}"))
    raise ValueError(f"unknown scenario: {name!r}")


def grid_import_cap_kw(base: PolicyResult, dr_start: int,
                       requested_shed_kw: float = DR_SHED_KW) -> float:
    """Baseline Site_Grid_Draw in the DR start slot minus the Requested_Shed (Req 14.3)."""
    return base.slots[dr_start].grid_kw - requested_shed_kw


def _soc_before(head: PolicyResult, s: int, spec: StorageSpec, *, battery: bool) -> StorageState:
    """Device SOC at the start of slot ``s`` (``SlotResult`` holds end-of-slot SOC)."""
    if s == 0:
        return spec.initial_state()
    prev = head.slots[s - 1]
    pct = prev.battery_soc_pct if battery else prev.flywheel_soc_pct
    return StorageState(soc_kwh=spec.capacity_kwh * pct / 100)


def build_dr(inp: ScenarioInputs, head: PolicyResult) -> DrResult:
    """The DR_Event and its Event_Zoom from the Headroom run (Requirements 14.2–14.8).

    Raises ``DrInfeasibleError`` (with ``scenario`` set) when the zoom would
    exceed a device rating or SOC bound (Requirement 14.11).
    """
    if inp.cap_kw is None:
        raise ValueError(f"{inp.name}: cap_kw must be set before building the DR event")
    s = inp.dr_start
    row = head.slots[s]
    load = row.inference_kw + row.training_kw
    bat_spec = StorageSpec.from_config("battery", BATTERY)
    fly_spec = StorageSpec.from_config("flywheel", FLYWHEEL)
    try:
        zoom = build_event_zoom(
            load, inp.cap_kw,
            battery_state=_soc_before(head, s, bat_spec, battery=True),
            flywheel_state=_soc_before(head, s, fly_spec, battery=False),
        )
    except DrInfeasibleError as e:
        raise DrInfeasibleError(e.second, slot=e.slot, scenario=inp.name,
                                detail=e.detail) from e
    start = inp.slots[s].t
    return DrResult(
        start=start,
        end=start + inp.dr_slots * _SLOT,
        requested_shed_kw=inp.requested_shed_kw,
        cap_kw=inp.cap_kw,
        site_load_kw=load,
        required_kw=zoom[-1].battery_kw + zoom[-1].flywheel_kw,
        zoom=tuple(zoom),
    )


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class ScenarioBuild:
    """One scenario's pipeline objects: the inputs, the three policy runs, the
    DR_Event, the metrics and the validated Scenario_File object."""

    inp: ScenarioInputs
    base: PolicyResult
    head: PolicyResult
    dr: DrResult
    metrics: Metrics
    doc: dict[str, Any]
    naive: PolicyResult
    naive_metrics: NaiveMetrics


def build_scenario(name: str, cache: GridData | Mapping[str, Sequence[Mapping]] | None = None,
                   seed: int | None = None, *, built_at: str | None = None) -> dict[str, Any]:
    """Build and validate one Scenario_File object in memory.

    ``cache`` is a ``GridData``, a raw ``load_all`` dict, or ``None`` to read
    the Raw_Cache offline. ``seed`` defaults to ``config.SEED`` and
    ``built_at`` to the current UTC time.

    Raises ``ScenarioError`` if ``inference_sla_pct < 100`` (Requirement 15.10)
    or the document breaks the Output_Contract (Requirement 16.15), and
    ``DrInfeasibleError`` / ``NoDataError`` / ``CacheMissingError`` from the
    stages that detect them.
    """
    return build_scenario_full(name, cache, seed, built_at=built_at).doc


def build_scenario_full(name: str,
                        cache: GridData | Mapping[str, Sequence[Mapping]] | None = None,
                        seed: int | None = None, *,
                        built_at: str | None = None) -> ScenarioBuild:
    """``build_scenario`` that also returns the intermediate objects.

    Same arguments, checks and exceptions as ``build_scenario``; the acceptance
    tests use the ``PolicyResult``s for SOC, energy and lateness checks.
    """
    data = cache if isinstance(cache, GridData) else load_grid_data(cache)
    seed = config.SEED if seed is None else seed
    built_at = _utc_now() if built_at is None else built_at

    grid = build_slots(_window_start(name, data.hourly), data.hourly, data.carbon)
    n = len(grid.slots)
    jobs = generate(n, seed, name)
    inference = inference_it_kw([g.t for g in grid.slots])
    idle = {r: tuple(v) for r, v in idle_capacity_kw(WINDOW_HOURS, seed, name).items()}
    dr_start = select_dr_start([g.pace_demand_mw for g in grid.slots], DR_DURATION_SLOTS)

    inp = ScenarioInputs(
        name=name, slots=grid.slots, jobs=tuple(jobs), inference_it_kw=tuple(inference),
        idle_capacity_kw=idle, dr_start=dr_start, dr_slots=DR_DURATION_SLOTS,
        requested_shed_kw=float(DR_SHED_KW), cap_kw=None, seed=seed,
    )
    base = run_baseline(inp)
    inp = dataclasses.replace(inp, cap_kw=grid_import_cap_kw(base, dr_start))

    try:
        head = run_headroom(inp, move_enabled=config.ENABLE_MOVE_REGION)
    except DrInfeasibleError as e:
        if e.scenario is None:
            raise DrInfeasibleError(e.second, slot=e.slot, scenario=name,
                                    detail=e.detail) from e
        raise
    dr = build_dr(inp, head)
    metrics = compute(inp, base, head, dr)
    naive = run_naive(inp)
    naive_metrics = compute_naive(inp, base, naive)
    if metrics.inference_sla_pct < _SLA_PCT:
        raise ScenarioError(name, f"inference_sla_pct is {metrics.inference_sla_pct}, "
                                  f"must be {_SLA_PCT:g}")

    doc = build_doc(name, inp, base, head, dr, metrics, built_at, {
        "filled_hours": grid.filled_hours,
        "peak_target_kw": peak_target_kw(inp),
        "enable_move_region": bool(config.ENABLE_MOVE_REGION),
    }, naive=naive, naive_metrics=naive_metrics)
    errors = validate(doc, scenario=name)
    if errors:
        shown = "; ".join(errors[:_MAX_ERRORS_SHOWN])
        more = f" (+{len(errors) - _MAX_ERRORS_SHOWN} more)" if len(errors) > _MAX_ERRORS_SHOWN else ""
        raise ScenarioError(name, f"Output_Contract violations: {shown}{more}")
    return ScenarioBuild(inp=inp, base=base, head=head, dr=dr, metrics=metrics, doc=doc,
                         naive=naive, naive_metrics=naive_metrics)


def _write_all(files: Mapping[Path, str], remove: Sequence[Path] = ()) -> None:
    """Write every file to ``<name>.json.tmp``, then ``os.replace`` each into place.

    No target is touched until every temp file is written; temp files are
    removed if anything fails. ``remove`` lists stale files deleted afterwards.
    """
    tmps: list[Path] = []
    try:
        for path, text in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".json.tmp")
            tmps.append(tmp)
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
        for path, tmp in zip(files, tmps):
            os.replace(tmp, path)
    finally:
        for tmp in tmps:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    for path in remove:
        path.unlink(missing_ok=True)


def _summary(doc: Mapping[str, Any]) -> str:
    m, dr, meta = doc["metrics"], doc["dr_event"], doc["meta"]
    return (f"{meta['scenario']}: window {meta['window'][0]} .. {meta['window'][1]}, "
            f"{meta['params']['job_count']} jobs, {len(doc['decisions'])} decisions, "
            f"DR {dr['start']} cap {dr['cap_kw']} kW\n"
            "  " + ", ".join(f"{k}={v}" for k, v in m.items())
            + f", dr_delivered_raw_pct={meta['params']['dr_delivered_raw_pct']}\n"
            "  cheapest hour: " + ", ".join(f"{k}={v}" for k, v in doc["naive_metrics"].items()))


def main(out_dir: Path | str | None = None, *, processed_dir: Path | str | None = None,
         raw: Optional[Mapping[str, Sequence[Mapping]]] = None,
         cache_dir: Path | str | None = None, built_at: str | None = None) -> int:
    """Build every enabled scenario, then write all files; 0 on success, 1 on failure.

    ``out_dir`` defaults to ``web/public/scenarios``, ``processed_dir`` to
    ``data/processed`` and ``cache_dir`` to ``data/raw``; ``raw`` supplies
    already-loaded rows instead of reading the cache.
    """
    out = Path(out_dir) if out_dir is not None else SCENARIOS_DIR
    built_at = _utc_now() if built_at is None else built_at
    names = enabled_scenarios()

    try:
        data = load_grid_data(raw, cache_dir=Path(cache_dir) if cache_dir is not None else None)
    except (CacheMissingError, NoDataError) as e:
        print(f"error: Raw_Cache: {e}; no Scenario_File written", file=sys.stderr)
        return 1

    texts: dict[Path, str] = {}
    docs: list[dict[str, Any]] = []
    for name in names:
        try:
            doc = build_scenario(name, data, config.SEED, built_at=built_at)
            texts[out / f"{name}.json"] = dumps(doc)
        except (DrInfeasibleError, ScenarioError) as e:   # messages already name the scenario
            print(f"error: {e}; no Scenario_File written", file=sys.stderr)
            return 1
        except (CacheMissingError, NoDataError, AssertionError, ValueError) as e:
            print(f"error: scenario {name!r}: {e}; no Scenario_File written", file=sys.stderr)
            return 1
        docs.append(doc)

    stale = [out / "sun.json"] if "sun" not in names else []
    _write_all(texts, remove=stale)
    for path in texts:
        print(f"wrote {path}")
    for path in stale:
        print(f"removed stale {path} (ENABLE_SUN_SCENARIO = False)")
    for path in write_processed(data.hourly, data.carbon, processed_dir):
        print(f"wrote {path}")
    for doc in docs:
        print(_summary(doc))
    return 0


if __name__ == "__main__":
    # Run the copy imported as sim.run_scenarios so exception classes match
    # the ones the other sim modules raise (``python -m`` runs this as __main__).
    from sim.run_scenarios import main as _main
    sys.exit(_main())
