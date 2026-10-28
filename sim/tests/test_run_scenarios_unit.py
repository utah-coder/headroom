"""Unit tests for the Scenario_Builder's DR wiring and two-phase file writes.

The real-data acceptance tests live in test_acceptance.py (task 9.8) and the
builder failure paths in test_run_scenarios.py (task 9.9).
"""

from datetime import datetime, timedelta, timezone

import pytest

from sim.config import BATTERY, DR_DURATION_SLOTS, DR_SHED_KW, FLYWHEEL, SLOT_MINUTES
from sim.run_scenarios import DrResult, _soc_before, _write_all, build_scenario, load_grid_data
from sim.scheduler import PolicyResult, SlotResult, StorageFlows
from sim.site import StorageSpec

_BUILT_AT = "2026-10-27T00:00:00Z"
_IDLE = StorageFlows(0.0, 0.0)


@pytest.fixture(scope="module")
def peak_doc():
    return build_scenario("peak", load_grid_data(), built_at=_BUILT_AT)


def _head(soc_pcts):
    t0 = datetime(2026, 7, 1, tzinfo=timezone.utc)
    slots = tuple(
        SlotResult(t=t0 + i * timedelta(minutes=SLOT_MINUTES), grid_kw=0.0, inference_kw=0.0,
                   training_kw=0.0, battery=_IDLE, flywheel=_IDLE, battery_soc_pct=b,
                   flywheel_soc_pct=f, moved_kw=0.0)
        for i, (b, f) in enumerate(soc_pcts)
    )
    return PolicyResult("headroom", slots, (), {}, ())


def test_soc_before_reads_previous_slot_end_or_initial():
    bat = StorageSpec.from_config("battery", BATTERY)
    fly = StorageSpec.from_config("flywheel", FLYWHEEL)
    head = _head([(40.0, 80.0), (35.0, 70.0)])
    assert _soc_before(head, 0, bat, battery=True) == bat.initial_state()
    assert _soc_before(head, 0, fly, battery=False) == fly.initial_state()
    assert _soc_before(head, 1, bat, battery=True).soc_kwh == pytest.approx(0.40 * 16000)
    assert _soc_before(head, 1, fly, battery=False).soc_kwh == pytest.approx(0.80 * 500)


def test_cap_is_baseline_draw_at_dr_start_minus_shed(peak_doc):
    dr = peak_doc["dr_event"]
    i = next(k for k, g in enumerate(peak_doc["grid"]) if g["t"] == dr["start"])
    assert dr["requested_shed_kw"] == DR_SHED_KW
    assert dr["cap_kw"] == pytest.approx(peak_doc["baseline"][i]["grid_kw"] - DR_SHED_KW, abs=0.11)


def test_dr_window_and_zoom_balance(peak_doc):
    dr = peak_doc["dr_event"]
    span = datetime.fromisoformat(dr["end"]) - datetime.fromisoformat(dr["start"])
    assert span == DR_DURATION_SLOTS * timedelta(minutes=SLOT_MINUTES)
    i = next(k for k, g in enumerate(peak_doc["headroom"]) if g["t"] == dr["start"])
    row = peak_doc["headroom"][i]
    load = row["inference_kw"] + row["training_kw"]
    for z in dr["zoom"]:
        assert z["grid_kw"] + z["flywheel_kw"] + z["battery_kw"] == pytest.approx(load, abs=0.25)
        assert z["grid_kw"] <= dr["cap_kw"]


def test_dr_result_carries_contract_and_metrics_fields():
    fields = set(DrResult.__dataclass_fields__)
    assert {"start", "end", "requested_shed_kw", "cap_kw", "zoom", "required_kw"} <= fields


def test_write_all_replaces_targets_and_removes_stale(tmp_path):
    a, b, stale = tmp_path / "peak.json", tmp_path / "sun.json", tmp_path / "old.json"
    a.write_text("old", encoding="utf-8")
    stale.write_text("x", encoding="utf-8")
    _write_all({a: "A\n", b: "B\n"}, remove=[stale])
    assert a.read_text(encoding="utf-8") == "A\n"
    assert b.read_text(encoding="utf-8") == "B\n"
    assert not stale.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_write_all_touches_nothing_when_a_temp_write_fails(tmp_path):
    a = tmp_path / "peak.json"
    a.write_text("old", encoding="utf-8")
    blocker = tmp_path / "blocker"
    blocker.write_text("", encoding="utf-8")       # a file, so blocker/sun.json cannot exist
    with pytest.raises(OSError):
        _write_all({a: "new", blocker / "sun.json": "new"})
    assert a.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob("*.tmp"))
