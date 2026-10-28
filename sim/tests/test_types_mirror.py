"""Contract mirror test (task 11.2, Requirement 16.12).

``web/src/types.ts`` declares the Scenario_File shape the web app reads, and
``sim/contract.py`` holds the key sets ``validate`` enforces, by interface name,
in ``INTERFACE_KEYS``. This test keeps the two in step from both sides:

* every ``export interface`` in ``types.ts`` has exactly the fields named in
  ``INTERFACE_KEYS`` for that interface, and there are no extra or missing
  interfaces;
* ``validate`` really enforces each key set: on a committed Scenario_File,
  deleting any listed key or adding an unlisted one is reported.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from sim.contract import INTERFACE_KEYS, validate

ROOT = Path(__file__).resolve().parents[2]
TYPES_TS = ROOT / "web" / "src" / "types.ts"
SCENARIO_FILE = ROOT / "web" / "public" / "scenarios" / "peak.json"

_INTERFACE_RE = re.compile(r"^export interface (\w+)([^{]*)\{(.*?)^\}", re.MULTILINE | re.DOTALL)
_FIELD_RE = re.compile(r"^\s*(\w+)\??\s*:", re.MULTILINE)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")

# Where one instance of each interface sits in a Scenario_File.
_PATHS: dict[str, tuple[Any, ...]] = {
    "Scenario": (),
    "Meta": ("meta",),
    "Sources": ("meta", "sources"),
    "GridPoint": ("grid", 0),
    "BaselinePoint": ("baseline", 0),
    "HeadroomPoint": ("headroom", 0),
    "Decision": ("decisions", 0),
    "OptionCost": ("decisions", 0, "options", 0),
    "DrEvent": ("dr_event",),
    "ZoomPoint": ("dr_event", "zoom", 0),
    "Metrics": ("metrics",),
    "NaiveMetrics": ("naive_metrics",),
}
_EXTRA_KEY = "zz_not_in_contract"


def _ts_interfaces() -> dict[str, list[str]]:
    """Field names of every ``export interface`` block in types.ts, in order."""
    out: dict[str, list[str]] = {}
    for name, heritage, body in _INTERFACE_RE.findall(TYPES_TS.read_text(encoding="utf-8")):
        # Inherited fields would be invisible to this parser, so refuse them.
        assert "extends" not in heritage, (
            f"types.ts: interface {name} uses 'extends'; list its fields inline "
            f"or teach test_types_mirror.py to resolve it"
        )
        out[name] = _FIELD_RE.findall(_LINE_COMMENT_RE.sub("", body))
    return out


def _load_doc() -> dict[str, Any]:
    return json.loads(SCENARIO_FILE.read_text(encoding="utf-8"))


def _at(doc: Any, path: tuple[Any, ...]) -> Any:
    for step in path:
        doc = doc[step]
    return doc


def test_types_ts_declares_exactly_the_contract_interfaces():
    assert set(_ts_interfaces()) == set(INTERFACE_KEYS)


@pytest.mark.parametrize("name", sorted(INTERFACE_KEYS))
def test_interface_fields_match_contract_keys(name):
    fields = _ts_interfaces()[name]
    expected = INTERFACE_KEYS[name]
    assert len(fields) == len(set(fields)), f"{name}: duplicate field in types.ts"
    missing = sorted(set(expected) - set(fields))
    extra = sorted(set(fields) - set(expected))
    assert not missing and not extra, (
        f"{name}: types.ts is missing {missing} and has extra {extra} "
        f"compared with sim/contract.py INTERFACE_KEYS"
    )


def test_every_interface_has_a_validate_path():
    assert set(_PATHS) == set(INTERFACE_KEYS)


def test_committed_scenario_file_is_valid():
    assert validate(_load_doc(), scenario="peak") == []


@pytest.mark.parametrize(
    ("name", "key"),
    [(name, key) for name in sorted(INTERFACE_KEYS) for key in INTERFACE_KEYS[name]],
)
def test_validate_reports_each_missing_key(name, key):
    doc = _load_doc()
    del _at(doc, _PATHS[name])[key]
    errors = validate(doc, scenario="peak")
    assert any(f"missing key '{key}'" in e for e in errors), f"{name}.{key}: {errors[:3]}"


@pytest.mark.parametrize("name", sorted(INTERFACE_KEYS))
def test_validate_reports_an_unexpected_key(name):
    doc = _load_doc()
    _at(doc, _PATHS[name])[_EXTRA_KEY] = 0
    errors = validate(doc, scenario="peak")
    assert any("unexpected key" in e and _EXTRA_KEY in e for e in errors), f"{name}: {errors[:3]}"
