"""Example-based unit tests for ``sim.contract`` (task 9.3).

A Scenario doc is built through ``build_doc`` from hand-built scheduler,
grid and site objects, checked to pass ``validate``, and then mutated one
rule at a time to check that each mutation is reported with its JSON path.

Requirements: 9.5, 16.2–16.11, 16.13, 16.14, 16.15.
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from sim import contract
from sim.contract import build_doc, dumps, q, validate, write_atomic
from sim.grid import GridSlot, stress_flags
from sim.jobs import TrainingJob
from sim.scheduler import (
    ROUTE_ORDER,
    Decision,
    OptionCost,
    PolicyResult,
    ScenarioInputs,
    SlotResult,
    StorageFlows,
)
from sim.site import ZoomEntry, is_on_peak, tariff_per_kwh
from sim.timeutil import iso_z, slot_times

T0 = datetime(2026, 7, 14, 18, tzinfo=timezone.utc)
SLOT = timedelta(minutes=15)


@dataclass(frozen=True)
class FakeDr:
    """Stand-in with the fields of the design's ``DrResult`` that build_doc reads."""

    start: datetime
    end: datetime
    requested_shed_kw: float
    cap_kw: float
    zoom: tuple[ZoomEntry, ...]


def _slot_result(t, grid, inf, trn, *, charge=0.0, discharge=0.0, soc=60.0, moved=0.0):
    return SlotResult(
        t=t, grid_kw=grid, inference_kw=inf, training_kw=trn,
        battery=StorageFlows(charge_kw=charge, discharge_kw=discharge),
        flywheel=StorageFlows(charge_kw=0.0, discharge_kw=0.0),
        battery_soc_pct=soc, flywheel_soc_pct=90.0, moved_kw=moved,
    )


def _opt(route, target, offered, *, energy=0.0, carbon=0.0, peak=0.0, deadline=0.0,
         storage=0.0, fee=0.0):
    total = energy + carbon + peak + deadline + storage + fee
    return OptionCost(route, target, offered, total, energy, carbon, peak, deadline, storage, fee)


def make_doc(n: int = 192) -> dict:
    times = slot_times(T0, n)
    hourly = [5000 + round(800 * math.sin(2 * math.pi * h / 24)) for h in range(n // 4)]
    demand = [float(hourly[i // 4]) for i in range(n)]
    flags = stress_flags(demand)
    slots = tuple(
        GridSlot(
            t=t, pace_demand_mw=demand[i], pace_forecast_mw=demand[i] - 50.04,
            carbon_g_per_kwh=512.345, alt_carbon={"BPAT": 90.0, "CISO": 210.0},
            price_per_kwh=tariff_per_kwh(t), on_peak=is_on_peak(t), stress=flags[i],
        )
        for i, t in enumerate(times)
    )
    inp = ScenarioInputs(
        name="peak", slots=slots,
        jobs=(TrainingJob("4001", 4, 500, 120, 360), TrainingJob("4002", 10, 300, 60, 360)),
        inference_it_kw=(2500.0,) * n,
        idle_capacity_kw={"BPAT": (0.0,) * (n // 4), "CISO": (0.0,) * (n // 4)},
        dr_start=20, dr_slots=8, requested_shed_kw=4000.0, cap_kw=6000.0, seed=42,
    )
    base = PolicyResult(
        policy="baseline",
        slots=tuple(_slot_result(t, 9750.0, 3250.0, 6500.0) for t in times),
        decisions=(), outcomes={}, moves=(),
    )
    head_slots = [_slot_result(t, 9750.0, 3250.0, 6500.0) for t in times]
    head_slots[1] = _slot_result(times[1], 10250.0, 3250.0, 6500.0, charge=500.0, soc=60.04)
    head_slots[2] = _slot_result(times[2], 8515.44, 3250.0, 6500.0, discharge=1234.56, soc=59.96)
    head_slots[3] = _slot_result(times[3], 9100.0, 3250.0, 5850.0, moved=650.0)
    decisions = (  # deliberately out of order; build_doc sorts by t
        Decision(times[12], "4002", "STORAGE_NOW", None, "storage reason", "COST", options=(
            _opt("GRID_NOW", None, True, energy=8.123, carbon=3.0, peak=36.0),
            _opt("STORAGE_NOW", None, True, energy=4.0, carbon=1.5, storage=6.25),
        )),
        Decision(times[4], "4001", "DEFER", iso_z(times[8]), "defer reason", "COST", options=(
            _opt("GRID_NOW", None, True, energy=20.0, carbon=9.0, peak=18.0),
            _opt("DEFER", iso_z(times[8]), True, energy=15.0, carbon=6.0, deadline=1.234),
        )),
        Decision(times[10], "4002", "MOVE_REGION", "BPAT", "move reason", "DR", options=(
            _opt("GRID_NOW", None, False, energy=10.0, carbon=4.0),
            _opt("MOVE_REGION", "BPAT", True, energy=5.0, carbon=0.5, fee=75.0),
        )),
        Decision(times[8], "4001", "GRID_NOW", None, "grid reason", "DEADLINE", options=(
            _opt("GRID_NOW", None, True, energy=20.0, carbon=9.0),
        )),
    )
    head = PolicyResult(policy="headroom", slots=tuple(head_slots), decisions=decisions,
                        outcomes={}, moves=())
    zoom = tuple(ZoomEntry(s, 6000.0, max(0.0, 3750.0 - 187.5 * s), min(3750.0, 187.5 * s))
                 for s in range(600))
    dr = FakeDr(times[20], times[20] + 8 * SLOT, 4000.0, 6000.0, zoom)
    metrics = {
        "headroom_kw": -12.345, "usd_saved": 1234.5678, "co2_avoided_t": 1.23456,
        "deadlines_met_pct": 95.0, "inference_sla_pct": 100.0, "dr_delivered_pct": 100.0,
        "dr_seconds_to_full": 20, "dr_delivered_raw_pct": 112.345,
    }
    params = {"filled_hours": {"PACE.D": 0, "PACE.DF": 2}, "peak_target_kw": 9100.04}
    return build_doc("peak", inp, base, head, dr, metrics, "2026-10-27T19:05:12Z", params)


@pytest.fixture(scope="module")
def valid_doc() -> dict:
    return make_doc()


@pytest.fixture
def doc(valid_doc) -> dict:
    return copy.deepcopy(valid_doc)


# --- Valid document ------------------------------------------------------------

def test_valid_doc_passes(valid_doc):
    assert validate(valid_doc) == []
    assert validate(valid_doc, scenario="peak") == []


def test_build_doc_key_order_and_values(valid_doc):
    d = valid_doc
    assert tuple(d) == contract.TOP_KEYS
    assert tuple(d["meta"]) == contract.META_KEYS
    assert tuple(d["meta"]["params"]) == contract.PARAMS_KEYS
    assert tuple(d["grid"][0]) == contract.GRID_KEYS
    assert tuple(d["baseline"][0]) == contract.BASELINE_KEYS
    assert tuple(d["headroom"][0]) == contract.HEADROOM_KEYS
    assert tuple(d["decisions"][0]) == contract.DECISION_KEYS
    assert tuple(d["decisions"][0]["options"][0]) == contract.OPTION_KEYS
    assert tuple(d["dr_event"]) == contract.DR_EVENT_KEYS
    assert tuple(d["dr_event"]["zoom"][0]) == contract.ZOOM_KEYS
    assert tuple(d["metrics"]) == contract.METRICS_KEYS

    assert d["meta"]["window"] == ["2026-07-14T18:00:00Z", "2026-07-16T18:00:00Z"]
    assert d["meta"]["sources"] == dict(contract.SOURCES)
    params = d["meta"]["params"]
    assert params["filled_hours"] == {"PACE.D": 0, "PACE.DF": 2}
    assert params["peak_target_kw"] == 9100.0
    assert params["job_count"] == 2
    assert params["dr_delivered_raw_pct"] == 112.3          # raw, unclamped
    assert params["stress_threshold_mw"] == min(g["pace_demand_mw"] for g in d["grid"] if g["stress"])
    assert params["units"] == "facility kW (IT × PUE 1.3)"

    assert [x["t"] for x in d["decisions"]] == sorted(x["t"] for x in d["decisions"])
    assert [x["route"] for x in d["decisions"]] == ["DEFER", "GRID_NOW", "MOVE_REGION", "STORAGE_NOW"]
    defer_opts = d["decisions"][0]["options"]
    assert [o["route"] for o in defer_opts] == ["GRID_NOW", "DEFER"]
    assert defer_opts[1] == {
        "route": "DEFER", "target": "2026-07-14T20:00:00Z", "offered": True, "total_usd": 22.23,
        "energy_usd": 15.0, "carbon_usd": 6.0, "peak_usd": 0.0, "deadline_usd": 1.23,
        "storage_usd": 0.0, "fee_usd": 0.0,
    }
    assert d["decisions"][2]["options"][0]["offered"] is False
    assert d["headroom"][1]["battery_kw"] == -500.0         # charging is negative
    assert d["headroom"][2]["battery_kw"] == 1234.6
    assert d["headroom"][3]["moved_kw"] == 650.0
    assert d["headroom"][1]["soc_pct"] == 60.0
    assert d["grid"][0]["carbon_g_per_kwh"] == 512.3
    assert d["metrics"]["headroom_kw"] == -12.3              # negatives reported raw
    assert d["metrics"]["usd_saved"] == 1234.57
    assert d["metrics"]["co2_avoided_t"] == 1.235
    assert d["metrics"]["dr_seconds_to_full"] == 20
    assert isinstance(d["metrics"]["dr_seconds_to_full"], int)


def test_dumps_round_trip_is_exact(valid_doc):
    text = dumps(valid_doc)
    loaded = json.loads(text)
    assert loaded == valid_doc
    assert validate(loaded) == []
    assert dumps(loaded) == text


def _inputs_stub() -> ScenarioInputs:
    slot = GridSlot(T0, 1.0, 1.0, 1.0, {"BPAT": 1.0, "CISO": 1.0}, 0.045, False, True)
    return ScenarioInputs("peak", (slot,), (), (1.0,), {}, 0, 0, 4000.0, None, 42)


@pytest.mark.parametrize("missing", ["filled_hours", "peak_target_kw", "dr_delivered_raw_pct"])
def test_build_params_requires_caller_values(missing):
    params = {"filled_hours": {}, "peak_target_kw": 1.0, "dr_delivered_raw_pct": 0.0}
    del params[missing]
    with pytest.raises(ValueError, match=missing):
        contract.build_params(_inputs_stub(), {}, params)


def test_build_params_raw_pct_from_metrics_and_extras_kept():
    out = contract.build_params(
        _inputs_stub(), {"dr_delivered_raw_pct": 101.26},
        {"filled_hours": {"PACE.D": 1}, "peak_target_kw": 5.0, "note": "x"},
    )
    assert tuple(out) == contract.PARAMS_KEYS + ("note",)
    assert out["dr_delivered_raw_pct"] == 101.3
    assert out["stress_threshold_mw"] == 1.0


def test_shorter_window_with_n_slots_none():
    short = make_doc(48)
    assert validate(short, n_slots=None) == []
    assert short["meta"]["window"][1] == iso_z(T0 + 48 * SLOT)
    assert any("expected 192 entries" in e for e in validate(short))


# --- Single mutations each fail with a specific error -------------------------

def _set(path, value):
    def mutate(d):
        *head, last = path
        obj = d
        for k in head:
            obj = obj[k]
        obj[last] = value
    return mutate


def _delete(path):
    def mutate(d):
        *head, last = path
        obj = d
        for k in head:
            obj = obj[k]
        del obj[last]
    return mutate


def _swap_decisions(d):
    d["decisions"][0], d["decisions"][1] = d["decisions"][1], d["decisions"][0]


def _clear_stress(d):
    for g in d["grid"]:
        g["stress"] = False


def _stress_on_lowest(d):
    low = min(range(len(d["grid"])), key=lambda i: d["grid"][i]["pace_demand_mw"])
    d["grid"][low]["stress"] = True


def _shift_grid_t(d):
    d["grid"][5]["t"] = "2026-07-14T19:30:00Z"


MUTATIONS = [
    # 16.2 top level
    ("missing top key", _delete(["metrics"]), "$: missing key 'metrics'"),
    ("extra top key", _set(["extra"], 1), "$: unexpected key 'extra'"),
    # 16.3 / 9.5 meta
    ("bad scenario", _set(["meta", "scenario"], "winter"), "meta.scenario"),
    ("window end not +48h", _set(["meta", "window", 1], "2026-07-16T19:00:00Z"), "meta.window"),
    ("window start malformed", _set(["meta", "window", 0], "2026-07-14 18:00:00"), "meta.window[0]"),
    ("window wrong length", _set(["meta", "window"], ["2026-07-14T18:00:00Z"]), "meta.window"),
    ("seed is string", _set(["meta", "seed"], "42"), "meta.seed"),
    ("seed is bool", _set(["meta", "seed"], True), "meta.seed"),
    ("built_at offset", _set(["meta", "built_at"], "2026-10-27T19:05:12+00:00"), "meta.built_at"),
    ("missing meta key", _delete(["meta", "params"]), "meta: missing key 'params'"),
    ("params not object", _set(["meta", "params"], []), "meta.params"),
    # 16.4 sources
    ("wrong source label", _set(["meta", "sources", "grid"], "SIMULATED"), "meta.sources.grid"),
    ("extra source", _set(["meta", "sources", "weather"], "REAL"), "meta.sources: unexpected key"),
    # 16.5 grid
    ("grid too short", lambda d: d["grid"].pop(), "grid: expected 192 entries"),
    ("grid t misaligned", _shift_grid_t, "grid[5].t"),
    ("grid t no Z", _set(["grid", 0, "t"], "2026-07-14T18:00:00"), "grid[0].t"),
    ("grid number is string", _set(["grid", 0, "pace_demand_mw"], "5000"), "grid[0].pace_demand_mw"),
    ("grid stress not bool", _set(["grid", 3, "stress"], 1), "grid[3].stress"),
    ("grid extra key", _set(["grid", 2, "pace_ng_mw"], 1.0), "grid[2]: unexpected key"),
    ("no stress slots", _clear_stress, "stress slots"),
    ("stress on low demand", _stress_on_lowest, "stress flags disagree"),
    # 16.6 baseline
    ("baseline t misaligned", _set(["baseline", 7, "t"], "2026-07-14T18:00:00Z"), "baseline[7].t"),
    ("baseline missing key", _delete(["baseline", 0, "training_kw"]), "baseline[0]: missing key"),
    ("baseline too long", lambda d: d["baseline"].append(dict(d["baseline"][-1])), "baseline: expected 192"),
    # 16.7 headroom
    ("soc above 100", _set(["headroom", 0, "soc_pct"], 100.1), "headroom[0].soc_pct"),
    ("soc below 0", _set(["headroom", 0, "soc_pct"], -0.1), "headroom[0].soc_pct"),
    ("moved_kw null", _set(["headroom", 1, "moved_kw"], None), "headroom[1].moved_kw"),
    ("battery_kw NaN", _set(["headroom", 2, "battery_kw"], float("nan")), "headroom[2].battery_kw"),
    ("headroom not array", _set(["headroom"], {}), "headroom: expected an array"),
    # 16.8 decisions
    ("unknown route", _set(["decisions", 0, "route"], "WAIT"), "decisions[0].route"),
    ("DEFER target null", _set(["decisions", 0, "target"], None), "decisions[0].target"),
    ("DEFER target not later", _set(["decisions", 0, "target"], "2026-07-14T19:00:00Z"),
     "decisions[0].target"),
    ("MOVE target not alt region", _set(["decisions", 2, "target"], "PACE"), "decisions[2].target"),
    ("GRID_NOW target set", _set(["decisions", 1, "target"], "BPAT"), "decisions[1].target"),
    ("decisions out of order", _swap_decisions, "earlier than the previous decision"),
    ("decision outside window", _set(["decisions", 3, "t"], "2026-07-16T18:00:00Z"), "decisions[3].t"),
    ("decision t off slot grid", _set(["decisions", 3, "t"], "2026-07-14T21:07:00Z"), "decisions[3].t"),
    ("empty reason", _set(["decisions", 0, "reason"], ""), "decisions[0].reason"),
    ("job id is int", _set(["decisions", 0, "job"], 4001), "decisions[0].job"),
    ("decision extra key", _set(["decisions", 0, "cause"], "COST"), "decisions[0]: unexpected key"),
    # decisions[].options (DECISIONS.md, "Explainable decisions")
    ("options missing", _delete(["decisions", 0, "options"]), "decisions[0]: missing key 'options'"),
    ("options not array", _set(["decisions", 0, "options"], {}), "decisions[0].options: expected an array"),
    ("option extra key", _set(["decisions", 0, "options", 0, "co2_t"], 1.0),
     "decisions[0].options[0]: unexpected key"),
    ("option unknown route", _set(["decisions", 0, "options", 0, "route"], "WAIT"),
     "decisions[0].options[0].route"),
    ("option route twice", _set(["decisions", 0, "options", 0, "route"], "DEFER"),
     "appears more than once"),
    ("option offered not bool", _set(["decisions", 0, "options", 0, "offered"], 1),
     "decisions[0].options[0].offered"),
    ("option part negative", _set(["decisions", 0, "options", 1, "energy_usd"], -15.0),
     "decisions[0].options[1].energy_usd"),
    ("option parts do not add up", _set(["decisions", 0, "options", 1, "total_usd"], 25.0),
     "cost parts add up to"),
    ("option DEFER target null", _set(["decisions", 0, "options", 1, "target"], None),
     "decisions[0].options[1].target"),
    ("chosen route not priced", _delete(["decisions", 0, "options", 1]), "chosen route DEFER once"),
    ("chosen target differs", _set(["decisions", 0, "options", 1, "target"], "2026-07-14T21:00:00Z"),
     "decisions[0].options: chosen DEFER option has target"),
    ("chosen not offered", _set(["decisions", 0, "options", 1, "offered"], False),
     "chosen route DEFER was not offered"),
    ("chosen not cheapest", lambda d: d["decisions"][0]["options"][0].update(
        total_usd=20.0, energy_usd=20.0, carbon_usd=0.0, peak_usd=0.0), "GRID_NOW cost less"),
    # 16.9 dr_event
    ("dr end before start", _set(["dr_event", "end"], "2026-07-14T22:00:00Z"), "must be earlier"),
    ("dr end after window", _set(["dr_event", "end"], "2026-07-16T18:15:00Z"), "dr_event.end"),
    ("dr start malformed", _set(["dr_event", "start"], "2026-07-14T23:00Z"), "dr_event.start"),
    ("shed not number", _set(["dr_event", "requested_shed_kw"], "4000"), "dr_event.requested_shed_kw"),
    ("zoom too short", lambda d: d["dr_event"]["zoom"].pop(), "dr_event.zoom: expected 600"),
    ("zoom s wrong", _set(["dr_event", "zoom", 3, "s"], 4), "dr_event.zoom[3].s"),
    ("zoom s float", _set(["dr_event", "zoom", 0, "s"], 0.5), "dr_event.zoom[0].s"),
    ("zoom extra key", _set(["dr_event", "zoom", 1, "soc"], 1.0), "dr_event.zoom[1]: unexpected key"),
    # 16.10 metrics
    ("pct above 100", _set(["metrics", "deadlines_met_pct"], 100.1), "metrics.deadlines_met_pct"),
    ("pct below 0", _set(["metrics", "dr_delivered_pct"], -0.1), "metrics.dr_delivered_pct"),
    ("seconds above 600", _set(["metrics", "dr_seconds_to_full"], 601), "metrics.dr_seconds_to_full"),
    ("metric is bool", _set(["metrics", "usd_saved"], True), "metrics.usd_saved"),
    ("metric missing", _delete(["metrics", "co2_avoided_t"]), "metrics: missing key 'co2_avoided_t'"),
]


@pytest.mark.parametrize("name, mutate, expected", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_single_mutation_fails(doc, name, mutate, expected):
    mutate(doc)
    errors = validate(doc)
    assert errors, f"{name}: mutation passed validation"
    assert any(expected in e for e in errors), errors


def test_scenario_must_match_file_name(valid_doc):
    errors = validate(valid_doc, scenario="sun")
    assert any(e.startswith("meta.scenario") for e in errors)


@pytest.mark.parametrize("not_a_doc", [None, [], "peak", 3])
def test_non_object_doc_fails(not_a_doc):
    assert validate(not_a_doc) == [f"$: expected an object, got {contract._type_name(not_a_doc)}"]


def test_key_constants_match_scheduler_and_types():
    assert contract.ROUTES == ROUTE_ORDER
    assert contract.MOVE_TARGETS == ("BPAT", "CISO")
    assert contract.INTERFACE_KEYS["HeadroomPoint"] == contract.HEADROOM_KEYS
    assert set(contract.INTERFACE_KEYS) == {
        "Scenario", "Meta", "Sources", "GridPoint", "BaselinePoint", "HeadroomPoint",
        "Decision", "OptionCost", "DrEvent", "ZoomPoint", "Metrics",
    }


def test_deadline_fallback_grid_now_not_offered_passes(doc):
    # GRID_NOW over the DR budget with nothing else offered: the job runs anyway.
    d = next(x for x in doc["decisions"] if x["route"] == "GRID_NOW")
    d["options"][0]["offered"] = False
    assert validate(doc) == []
    d["options"].append({**d["options"][0], "route": "STORAGE_NOW", "offered": True,
                         "total_usd": 50.0, "energy_usd": 50.0, "carbon_usd": 0.0})
    assert any("chosen route GRID_NOW was not offered" in e for e in validate(doc))


def test_rounded_tie_between_options_passes(doc):
    # Totals are rounded to cents, so a cent-level tie is not a tie-break error.
    opts = doc["decisions"][0]["options"]
    opts[0].update(total_usd=opts[1]["total_usd"], energy_usd=opts[1]["total_usd"],
                   carbon_usd=0.0, peak_usd=0.0)
    assert validate(doc) == []


# --- q() -----------------------------------------------------------------------

def test_q_rounds_and_returns_float():
    assert q(1234.5678, 2) == 1234.57
    assert q(5, 1) == 5.0 and isinstance(q(5, 1), float)
    assert q(-12.345, 1) == -12.3


@pytest.mark.parametrize("x", [-0.0, -0.04, -1e-12])
def test_q_never_returns_negative_zero(x):
    r = q(x, 1)
    assert r == 0.0
    assert math.copysign(1.0, r) == 1.0
    assert json.dumps(r) == "0.0"


@pytest.mark.parametrize("x", [float("nan"), float("inf"), float("-inf")])
def test_q_rejects_non_finite(x):
    with pytest.raises(ValueError, match="non-finite"):
        q(x, 1)


# --- dumps() -------------------------------------------------------------------

def test_dumps_format():
    assert dumps({"b": [1, 2.5], "a": "IT × PUE", "c": None}) == '{"b":[1,2.5],"a":"IT × PUE","c":null}\n'


def test_dumps_rejects_nan():
    with pytest.raises(ValueError):
        dumps({"x": float("nan")})


# --- write_atomic() ------------------------------------------------------------

def test_write_atomic_writes_and_replaces(tmp_path):
    target = tmp_path / "peak.json"
    target.write_text("old\n", encoding="utf-8")
    text = dumps({"meta": {"units": "IT × PUE"}})
    write_atomic(target, text)
    assert target.read_bytes() == text.encode("utf-8")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["peak.json"]


def test_write_atomic_failed_write_leaves_existing_file(tmp_path):
    target = tmp_path / "peak.json"
    target.write_bytes(b'{"old":true}\n')
    with pytest.raises(UnicodeEncodeError):
        write_atomic(target, '{"bad":"\ud800"}\n')  # lone surrogate cannot be UTF-8 encoded
    assert target.read_bytes() == b'{"old":true}\n'
    assert not (tmp_path / "peak.json.tmp").exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["peak.json"]


def test_write_atomic_failed_replace_removes_temp(tmp_path):
    target = tmp_path / "sun.json"
    target.mkdir()  # os.replace cannot put a file over a directory
    (target / "keep.txt").write_text("x", encoding="utf-8")
    with pytest.raises(OSError):
        write_atomic(target, "{}\n")
    assert target.is_dir() and (target / "keep.txt").read_text(encoding="utf-8") == "x"
    assert not (tmp_path / "sun.json.tmp").exists()
