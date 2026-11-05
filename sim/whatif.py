"""What-if runs: the real engine, with validated changes to its inputs.

The analyst never does the maths. A question becomes a set of ``KNOBS`` values,
``validate`` checks them, and ``run`` builds the scenario through the same
``build_scenario_full`` the static site uses. The scheduler's logic is untouched.

The engine's modules bind their constants by name at import
(``from sim.config import DEMAND_CHARGE_PER_KW``), so ``overlay`` swaps the value
in every ``sim.*`` module that holds that name for the length of one run, then
restores it. With no changes the result is the committed scenario, to the byte
(``sim/tests/test_whatif.py``).

``compare`` turns two runs into the only figures an answer may quote. Each has an
id, and its text is produced here, so a model can refer to a figure but never
write one (``analyst/verify.py``).
"""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from sim import config
from sim.metrics import policy_cost_usd
from sim.run_scenarios import GridData, ScenarioError, build_scenario_full, load_grid_data
from sim.site import DrInfeasibleError

__all__ = [
    "KNOBS",
    "MOVE_SWITCH",
    "SCENARIOS",
    "EngineRefused",
    "Figure",
    "Run",
    "Comparison",
    "validate",
    "overlay",
    "run",
    "current",
    "compare",
]

SCENARIOS = ("peak", "sun")
MOVE_SWITCH = "move_region_enabled"

_FIXED_BUILT_AT = "2026-01-01T00:00:00Z"  # Not shown; only keeps runs comparable.


class EngineRefused(RuntimeError):
    """The engine could not produce a valid plan for these inputs."""


def _usd(v: float) -> str:
    return f"-${abs(v):,.0f}" if round(v) < 0 else f"${abs(v):,.0f}"


def _usd_rate(unit: str, digits: int) -> Callable[[float], str]:
    return lambda v: f"${v:,.{digits}f}/{unit}"


def _kw(v: float) -> str:
    return f"{v:,.0f} kW"


def _kwh(v: float) -> str:
    return f"{v:,.0f} kWh"


def _tonnes(v: float) -> str:
    return f"{v:,.1f} t"


def _pct(v: float) -> str:
    return f"{v:.1f}%"


def _count(v: float) -> str:
    return f"{v:,.0f}"


@dataclass(frozen=True)
class Knob:
    """One input a question may change."""

    key: str
    label: str
    config_name: str
    field: str | None  # Key inside a config dict (BATTERY, FLYWHEEL), else None.
    fmt: Callable[[float], str]

    @property
    def bounds(self) -> tuple[float, float]:
        return config.WHATIF_BOUNDS[self.key]

    def read(self) -> float:
        value = getattr(config, self.config_name)
        return float(value if self.field is None else value[self.field])


KNOBS: tuple[Knob, ...] = (
    Knob("demand_charge_usd_per_kw", "Demand charge", "DEMAND_CHARGE_PER_KW", None, _usd_rate("kW", 2)),
    Knob("onpeak_usd_per_kwh", "On-peak energy price", "TARIFF_ONPEAK_PER_KWH", None, _usd_rate("kWh", 3)),
    Knob("offpeak_usd_per_kwh", "Off-peak energy price", "TARIFF_OFFPEAK_PER_KWH", None, _usd_rate("kWh", 3)),
    Knob("carbon_usd_per_t", "Carbon price", "CARBON_SHADOW_USD_PER_T", None, _usd_rate("t", 2)),
    Knob("battery_power_kw", "Battery power", "BATTERY", "power_kw", _kw),
    Knob("battery_capacity_kwh", "Battery capacity", "BATTERY", "capacity_kwh", _kwh),
    Knob("flywheel_power_kw", "Flywheel power", "FLYWHEEL", "power_kw", _kw),
    Knob("flywheel_capacity_kwh", "Flywheel capacity", "FLYWHEEL", "capacity_kwh", _kwh),
    Knob("dr_shed_kw", "Demand-response cut requested", "DR_SHED_KW", None, _kw),
    Knob("move_fee_usd", "Fee per job moved to another region", "MOVE_PENALTY_USD", None, _usd),
    Knob("alt_region_usd_per_kwh", "Energy price in other regions", "ALT_REGION_PRICE_PER_KWH", None, _usd_rate("kWh", 3)),
)
_KNOB_BY_KEY = {k.key: k for k in KNOBS}


def validate(changes: Mapping[str, Any]) -> tuple[dict[str, float | bool], list[str]]:
    """Check requested changes. Returns the clean values and readable problems.

    Unknown keys, wrong types, non-finite numbers and values out of range are all
    problems: nothing unvalidated reaches the engine. ``None`` means "no change".
    """
    clean: dict[str, float | bool] = {}
    problems: list[str] = []
    for key, value in changes.items():
        if value is None:
            continue
        if key == MOVE_SWITCH:
            if isinstance(value, bool):
                clean[key] = value
            else:
                problems.append("Moving jobs to other regions can only be switched on or off.")
            continue
        knob = _KNOB_BY_KEY.get(key)
        if knob is None:
            problems.append(f"Unknown input: {key[:40]}.")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
            problems.append(f"{knob.label} must be a number.")
            continue
        low, high = knob.bounds
        if not low <= value <= high:
            problems.append(f"{knob.label} must be between {knob.fmt(low)} and {knob.fmt(high)}.")
            continue
        clean[key] = float(value)
    return clean, problems


def _config_patches(changes: Mapping[str, float | bool]) -> dict[str, Any]:
    patches: dict[str, Any] = {}
    for key, value in changes.items():
        if key == MOVE_SWITCH:
            patches["ENABLE_MOVE_REGION"] = bool(value)
            continue
        knob = _KNOB_BY_KEY[key]
        if knob.field is None:
            patches[knob.config_name] = value
        else:
            base = patches.get(knob.config_name, getattr(config, knob.config_name))
            patches[knob.config_name] = {**base, knob.field: value}
    return patches


_OVERLAY_LOCK = threading.Lock()


def _engine_modules() -> list[Any]:
    return [m for name, m in list(sys.modules.items())
            if m is not None and (name == "sim" or name.startswith("sim."))
            and not name.startswith("sim.tests")]


@contextmanager
def overlay(changes: Mapping[str, float | bool]) -> Iterator[None]:
    """Apply validated changes to every engine module, then restore them."""
    patches = _config_patches(changes)
    saved: list[tuple[Any, str, Any]] = []
    with _OVERLAY_LOCK:
        try:
            for module in _engine_modules():
                for name, value in patches.items():
                    if name in vars(module):
                        saved.append((module, name, vars(module)[name]))
                        setattr(module, name, value)
            yield
        finally:
            for module, name, value in reversed(saved):
                setattr(module, name, value)


@dataclass(frozen=True)
class Run:
    """The numbers from one engine run, rounded as the site shows them."""

    scenario: str
    changes: Mapping[str, float | bool]
    values: Mapping[str, float]
    times: tuple[str, ...]
    grid_kw: tuple[float, ...]
    doc: Mapping[str, Any]


_data_lock = threading.Lock()
_data: GridData | None = None


def _grid_data() -> GridData:
    global _data
    with _data_lock:
        if _data is None:
            _data = load_grid_data()
        return _data


def run(scenario: str, changes: Mapping[str, float | bool], data: GridData | None = None) -> Run:
    """Build one scenario with validated changes. Raises ``EngineRefused``."""
    if scenario not in SCENARIOS:
        raise ValueError(f"unknown scenario: {scenario}")
    data = _grid_data() if data is None else data
    try:
        with overlay(changes):
            build = build_scenario_full(scenario, data, built_at=_FIXED_BUILT_AT)
            # Inside the overlay: the bill reads the demand charge and move fee.
            head_bill = policy_cost_usd(build.inp, build.head, include_moves=True)
            base_bill = policy_cost_usd(build.inp, build.base, include_moves=False)
    except DrInfeasibleError as e:
        raise EngineRefused(
            "On-site storage can't deliver the demand-response cut with these inputs, "
            "so the engine rejected the plan.") from e
    except (ScenarioError, AssertionError) as e:
        raise EngineRefused(
            "These inputs break one of the engine's hard rules, so it produced no valid plan.") from e

    doc = build.doc
    m = doc["metrics"]
    routes = [d["route"] for d in doc["decisions"]]
    values = {
        "headroom_kw": m["headroom_kw"],
        "usd_saved": m["usd_saved"],
        "headroom_bill_usd": head_bill,
        "baseline_bill_usd": base_bill,
        "co2_avoided_t": m["co2_avoided_t"],
        "headroom_site_peak_kw": m["headroom_site_peak_kw"],
        "baseline_site_peak_kw": m["baseline_site_peak_kw"],
        "deadlines_met_pct": m["deadlines_met_pct"],
        "inference_sla_pct": m["inference_sla_pct"],
        "dr_delivered_pct": m["dr_delivered_pct"],
        "storage_decisions": float(routes.count("STORAGE_NOW")),
        "defer_decisions": float(routes.count("DEFER")),
        "move_decisions": float(routes.count("MOVE_REGION")),
    }
    return Run(
        scenario=scenario,
        changes=dict(changes),
        values=values,
        times=tuple(r["t"] for r in doc["headroom"]),
        grid_kw=tuple(r["grid_kw"] for r in doc["headroom"]),
        doc=doc,
    )


_current: dict[str, Run] = {}


def current(scenario: str, data: GridData | None = None) -> Run:
    """The unchanged scenario, built once per process."""
    if scenario not in _current:
        _current[scenario] = run(scenario, {}, data)
    return _current[scenario]


@dataclass(frozen=True)
class Figure:
    """One metric, before and after, as display text."""

    id: str
    label: str
    current: str
    whatif: str
    change: str  # Size of the change, unsigned, e.g. "$1,204".
    direction: str  # "up", "down" or "unchanged".


# (id, label, formatter, decimals used to compare and subtract).
_METRICS: tuple[tuple[str, str, Callable[[float], str], int], ...] = (
    ("usd_saved", "Saved against running every job on arrival", _usd, 0),
    ("headroom_bill_usd", "Site bill with Headroom", _usd, 0),
    ("baseline_bill_usd", "Site bill without Headroom", _usd, 0),
    ("headroom_kw", "Grid capacity freed in stress hours", _kw, 0),
    ("headroom_site_peak_kw", "Site peak with Headroom", _kw, 0),
    ("baseline_site_peak_kw", "Site peak without Headroom", _kw, 0),
    ("co2_avoided_t", "CO2 avoided (average intensity, not marginal)", _tonnes, 1),
    ("deadlines_met_pct", "Training deadlines met", _pct, 1),
    ("inference_sla_pct", "Inference SLA", _pct, 1),
    ("dr_delivered_pct", "Demand-response cut delivered", _pct, 1),
    ("storage_decisions", "Decisions to run from storage", _count, 0),
    ("defer_decisions", "Decisions to defer", _count, 0),
    ("move_decisions", "Decisions to move region", _count, 0),
)


@dataclass(frozen=True)
class Comparison:
    """Everything an answer may quote about one what-if."""

    scenario: str
    inputs: tuple[Figure, ...]
    figures: tuple[Figure, ...]
    facts: Mapping[str, str]  # Placeholder id -> exact display text.


def _direction(delta: float) -> str:
    return "up" if delta > 0 else "down" if delta < 0 else "unchanged"


def _input_figures(changes: Mapping[str, float | bool]) -> tuple[Figure, ...]:
    out: list[Figure] = []
    for key, value in changes.items():
        if key == MOVE_SWITCH:
            on = "allowed" if config.ENABLE_MOVE_REGION else "off"
            out.append(Figure(key, "Moving jobs to other regions", on, "allowed" if value else "off",
                              "", "unchanged"))
            continue
        knob = _KNOB_BY_KEY[key]
        was = knob.read()
        out.append(Figure(key, knob.label, knob.fmt(was), knob.fmt(float(value)),
                          knob.fmt(abs(float(value) - was)), _direction(float(value) - was)))
    return tuple(out)


def compare(base: Run, whatif: Run) -> Comparison:
    """Pair two runs of one scenario and produce every quotable figure."""
    if base.scenario != whatif.scenario:
        raise ValueError("compare needs two runs of the same scenario")
    figures: list[Figure] = []
    facts: dict[str, str] = {}
    for key, label, fmt, digits in _METRICS:
        before = round(base.values[key], digits)
        after = round(whatif.values[key], digits)
        delta = round(after - before, digits)
        figure = Figure(key, label, fmt(before), fmt(after), fmt(abs(delta)), _direction(delta))
        figures.append(figure)
        facts[f"current.{key}"] = figure.current
        facts[f"whatif.{key}"] = figure.whatif
        facts[f"change.{key}"] = figure.change
    inputs = _input_figures(whatif.changes)
    for figure in inputs:
        facts[f"input_current.{figure.id}"] = figure.current
        facts[f"input_whatif.{figure.id}"] = figure.whatif
    return Comparison(whatif.scenario, inputs, tuple(figures), facts)
