"""Explainable decisions: every Decision carries the routes it compared (DECISIONS.md,
2026-10-28 "Explainable decisions").

Real-data checks over both scenarios:

* each option's six parts add up to its unrounded total within 1e-6;
* each total is the ``RouteCandidate.cost`` the planner compared, read back
  from ``run_headroom(trace=True)``;
* the chosen route is the cheapest offered option, ties by ``ROUTE_ORDER``;
* adding the options changed nothing else: every pre-existing key of both
  Scenario_Files hashes to its value from the build before this feature.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import numpy as np
import pytest

from sim.contract import OPTION_COST_PARTS, dumps
from sim.run_scenarios import ScenarioBuild, build_scenario_full, load_grid_data
from sim.scheduler import GRID_NOW, ROUTE_ORDER, Decision, OptionCost, run_headroom
from sim.tests.conftest import block_network

SCENARIOS = ("peak", "sun")
_BUILT_AT = "2026-10-28T00:00:00Z"
_SPLIT_TOL_USD = 1e-6
_COST_DIGITS = 9   # scheduler._route_key compares costs rounded to 1e-9 $

# sha256 of each Scenario_File as built at b84a621 (before decisions[].options),
# without meta.built_at, restricted to the keys below. A change here means the
# decision path or another output moved; log it in DECISIONS.md before updating.
_PRE_FEATURE_SHA256 = {
    "peak": "59ebd1dc947225c831051ea9b60474c2bb405b464c2635292cfb11b56ab3ddb2",
    "sun": "202ef9ae18fd1d908f3b6153bdef210c6619d5805c2b6f2214a0df5dd51afa5b",
}
_PRE_FEATURE_KEYS: dict[str, tuple[str, ...]] = {
    "meta": ("scenario", "window", "seed", "sources", "params"),
    "params": ("filled_hours", "peak_target_kw", "job_count", "enable_move_region",
               "dr_delivered_raw_pct", "stress_threshold_mw", "units"),
    "decision": ("t", "job", "route", "target", "reason"),
    "metrics": ("headroom_kw", "usd_saved", "co2_avoided_t", "deadlines_met_pct",
                "inference_sla_pct", "dr_delivered_pct", "dr_seconds_to_full"),
}


@pytest.fixture(scope="module")
def builds() -> dict[str, ScenarioBuild]:
    with pytest.MonkeyPatch.context() as mp:
        block_network(mp)
        data = load_grid_data()
        return {name: build_scenario_full(name, data, built_at=_BUILT_AT) for name in SCENARIOS}


def _all_options(b: ScenarioBuild) -> list[tuple[Decision, OptionCost]]:
    return [(d, o) for d in b.head.decisions for o in d.options]


@pytest.mark.parametrize("name", SCENARIOS)
def test_every_decision_has_options_grid_now_first(name, builds):
    decisions = builds[name].head.decisions
    assert decisions
    for d in decisions:
        routes = [o.route for o in d.options]
        assert routes and routes[0] == GRID_NOW, d
        assert len(routes) == len(set(routes)) <= len(ROUTE_ORDER), d
        # Only GRID_NOW is priced when not offered; every other option passed filtering.
        assert all(o.offered for o in d.options[1:]), d


@pytest.mark.parametrize("name", SCENARIOS)
def test_option_parts_add_up_to_total(name, builds):
    pairs = _all_options(builds[name])
    assert pairs
    for d, o in pairs:
        parts = [getattr(o, k) for k in OPTION_COST_PARTS]
        assert all(p >= 0 for p in parts), (d.t, d.job, o)
        assert abs(sum(parts) - o.total_usd) <= _SPLIT_TOL_USD, (d.t, d.job, o)


@pytest.mark.parametrize("name", SCENARIOS)
def test_parts_follow_the_route(name, builds):
    for _, o in _all_options(builds[name]):
        assert (o.storage_usd > 0) == (o.route == "STORAGE_NOW"), o
        assert (o.fee_usd > 0) == (o.route == "MOVE_REGION"), o
        if o.route == "MOVE_REGION":
            assert o.peak_usd == 0.0, o           # the load leaves the site
    routes = {o.route for _, o in _all_options(builds[name])}
    assert routes == set(ROUTE_ORDER)             # every route is explained somewhere


@pytest.mark.parametrize("name", SCENARIOS)
def test_option_totals_are_the_costs_the_planner_compared(name, builds):
    b = builds[name]
    traced = run_headroom(b.inp, trace=True)
    assert traced.decisions == b.head.decisions
    jobs = {(st.slot, jt.job): jt for st in traced.trace.slots for jt in st.jobs if jt.decision}
    slot_of = {g.t: i for i, g in enumerate(b.inp.slots)}
    for d in b.head.decisions:
        jt = jobs[(slot_of[d.t], d.job)]
        offered = {c.route: c.cost for c in jt.candidates}
        assert d.options[0].total_usd == jt.grid_now.cost
        assert d.options[0].offered == (GRID_NOW in offered)
        assert {o.route: o.total_usd for o in d.options if o.offered} == offered


@pytest.mark.parametrize("name", SCENARIOS)
def test_chosen_is_the_cheapest_offered_option(name, builds):
    for d in builds[name].head.decisions:
        chosen = [o for o in d.options if o.route == d.route]
        assert len(chosen) == 1, d
        assert chosen[0].target == d.target, d
        offered = [o for o in d.options if o.offered]
        if not offered:                           # deadline fallback: runs anyway
            assert d.route == GRID_NOW and d.cause == "DEADLINE", d
            continue
        best = min(offered, key=lambda o: (round(o.total_usd, _COST_DIGITS),
                                           ROUTE_ORDER.index(o.route)))
        assert best.route == d.route, d


def test_option_cents_survive_blas_noise(builds, monkeypatch):
    # np.dot sums in a CPU-dependent order, so CI (Linux) and a Windows build
    # differ by a few ulps. Nudging every dot product by one ulp must not
    # change a single cent in either file.
    real_dot = np.dot
    monkeypatch.setattr(np, "dot", lambda a, b: np.nextafter(real_dot(a, b), np.inf))
    with pytest.MonkeyPatch.context() as mp:
        block_network(mp)
        data = load_grid_data()
        for name in SCENARIOS:
            noisy = build_scenario_full(name, data, built_at=_BUILT_AT).doc
            # Compare per-decision options, not the whole one-line file: a
            # failing string compare would make pytest diff ~300 kB.
            got = [d["options"] for d in noisy["decisions"]]
            want = [d["options"] for d in builds[name].doc["decisions"]]
            bad = [i for i, (g, w) in enumerate(zip(got, want)) if g != w]
            assert len(got) == len(want) and not bad, f"{name}: options differ in decisions {bad[:10]}"
            assert dumps(noisy) == dumps(builds[name].doc), f"{name}: file differs outside options"


def _pre_feature_view(doc: dict[str, Any]) -> dict[str, Any]:
    keys = _PRE_FEATURE_KEYS
    meta = {k: doc["meta"][k] for k in keys["meta"]}
    meta["params"] = {k: doc["meta"]["params"][k] for k in keys["params"]}
    return {
        "meta": meta,
        "grid": doc["grid"],
        "baseline": doc["baseline"],
        "headroom": doc["headroom"],
        "decisions": [{k: d[k] for k in keys["decision"]} for d in doc["decisions"]],
        "dr_event": doc["dr_event"],
        "metrics": {k: doc["metrics"][k] for k in keys["metrics"]},
    }


@pytest.mark.parametrize("name", SCENARIOS)
def test_everything_else_is_unchanged(name, builds):
    view = _pre_feature_view(json.loads(dumps(builds[name].doc)))
    text = json.dumps(view, ensure_ascii=False, separators=(",", ":"))
    assert hashlib.sha256(text.encode()).hexdigest() == _PRE_FEATURE_SHA256[name]
