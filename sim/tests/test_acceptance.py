"""Acceptance tests on the real `peak` and `sun` scenarios (Requirement 23, 15.6).

Both scenarios are built once per session from the committed Raw_Cache
(``data/raw``), offline and under the no-network guard (Requirement 23.10).
Every scenario test is parametrized by scenario name, so a failure report
names the test and the scenario it failed in (Requirement 23.12).

The builder is never pointed at the real ``web/public/scenarios`` or
``data/processed``: ``main()`` runs write into pytest temp directories.
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest

from sim.config import RESTART_PENALTY_MIN, ROUND, SLOT_MINUTES
from sim.contract import TOP_KEYS, dumps, q, validate
from sim.fetch_eia import RAW_DIR, CacheMissingError
from sim.run_scenarios import ScenarioBuild, build_scenario_full, load_grid_data, main
from sim.scheduler import PolicyResult
from sim.tests.conftest import block_network

SCENARIOS = ("peak", "sun")
N_SLOTS = 192
ZOOM_SECONDS = 600
SOC_MIN_PCT, SOC_MAX_PCT = 10.0, 95.0   # Requirement 23.2
SOC_EPS = 1e-9                          # float noise only
ENERGY_TOL_KWH = 0.01                   # Requirement 23.3
LATE_MARK = "[LATE +"

_BUILT_AT_1 = "2026-10-27T00:00:00Z"
_BUILT_AT_2 = "2026-10-28T12:34:56Z"
_BUILT_AT_RE = re.compile(rb'"built_at":"[^"]*"')


# --- Session fixtures ----------------------------------------------------------

@pytest.fixture(scope="session")
def builds() -> dict[str, ScenarioBuild]:
    """Both scenarios built in memory from the committed Raw_Cache, offline."""
    with pytest.MonkeyPatch.context() as mp:
        block_network(mp)
        data = load_grid_data()
        return {name: build_scenario_full(name, data, built_at=_BUILT_AT_1) for name in SCENARIOS}


@pytest.fixture(scope="session")
def written(tmp_path_factory) -> list[dict[str, bytes]]:
    """Two full ``main()`` runs (same seed and Raw_Cache, different ``built_at``),
    each into its own temp directory; returns the Scenario_File bytes per run."""
    runs: list[dict[str, bytes]] = []
    with pytest.MonkeyPatch.context() as mp:
        block_network(mp)
        for i, built_at in enumerate((_BUILT_AT_1, _BUILT_AT_2)):
            root = tmp_path_factory.mktemp(f"build{i}")
            out = root / "scenarios"
            rc = main(out_dir=out, processed_dir=root / "processed", built_at=built_at)
            assert rc == 0, f"builder run {i} exited {rc}"
            runs.append({name: (out / f"{name}.json").read_bytes() for name in SCENARIOS})
    return runs


def _policies(b: ScenarioBuild) -> tuple[PolicyResult, PolicyResult]:
    return b.base, b.head


# --- Requirement 23.1 ----------------------------------------------------------

@pytest.mark.parametrize("scenario", SCENARIOS)
def test_aligned_series_and_identical_inference(scenario, builds):
    doc = builds[scenario].doc
    grid_t = [g["t"] for g in doc["grid"]]
    assert len(doc["baseline"]) == N_SLOTS
    assert len(doc["headroom"]) == N_SLOTS
    assert [r["t"] for r in doc["baseline"]] == grid_t
    assert [r["t"] for r in doc["headroom"]] == grid_t
    bad = [(b["t"], b["inference_kw"], h["inference_kw"])
           for b, h in zip(doc["baseline"], doc["headroom"])
           if h["inference_kw"] != b["inference_kw"]]
    assert not bad, f"{scenario}: headroom inference_kw differs from baseline at {bad[:5]}"
    # Same check on the unrounded policy results.
    b, h = builds[scenario].base, builds[scenario].head
    assert [s.inference_kw for s in h.slots] == [s.inference_kw for s in b.slots]


# --- Requirement 23.2 ----------------------------------------------------------

@pytest.mark.parametrize("scenario", SCENARIOS)
def test_storage_soc_bounds_and_no_simultaneous_charge_discharge(scenario, builds):
    head = builds[scenario].head
    assert len(head.slots) == N_SLOTS
    problems: list[str] = []
    for i, s in enumerate(head.slots):
        for dev, soc, flows in (("battery", s.battery_soc_pct, s.battery),
                                ("flywheel", s.flywheel_soc_pct, s.flywheel)):
            if not SOC_MIN_PCT - SOC_EPS <= soc <= SOC_MAX_PCT + SOC_EPS:
                problems.append(f"slot {i} {dev} SOC {soc}%")
            if flows.charge_kw > 0 and flows.discharge_kw > 0:
                problems.append(f"slot {i} {dev} charge {flows.charge_kw} kW "
                                f"and discharge {flows.discharge_kw} kW")
    assert not problems, f"{scenario}: " + "; ".join(problems[:10])


# --- Requirement 23.3 ----------------------------------------------------------

@pytest.mark.parametrize("scenario", SCENARIOS)
def test_completed_job_energy_conservation(scenario, builds):
    b = builds[scenario]
    for policy in _policies(b):
        assert set(policy.outcomes) == {j.id for j in b.inp.jobs}
        completed = 0
        problems: list[str] = []
        for job in b.inp.jobs:
            o = policy.outcomes[job.id]
            if not o.completed_in_window:
                continue
            completed += 1
            requested = job.power_kw * job.duration_min / 60
            penalty = job.power_kw * RESTART_PENALTY_MIN / 60 * o.resumes
            if abs(o.delivered_kwh - (requested + penalty)) > ENERGY_TOL_KWH:
                problems.append(f"job {job.id}: delivered {o.delivered_kwh} kWh, expected "
                                f"{requested} + {penalty} ({o.resumes} resumes)")
        assert completed > 0, f"{scenario}/{policy.policy}: no completed jobs"
        assert not problems, f"{scenario}/{policy.policy}: " + "; ".join(problems[:10])


# --- Requirement 23.4 ----------------------------------------------------------

def _must_be_late(job, o, n_slots: int) -> bool | None:
    """Lateness implied by finish time vs deadline; ``None`` when the
    end-of-window state (paused or not) is needed to decide."""
    if o.completed_in_window:
        return o.finish_min > job.deadline_min
    # Unfinished: finish = window end + remaining work (+ one Restart_Penalty if paused).
    remaining = (job.duration_min + RESTART_PENALTY_MIN * o.resumes
                 - round(o.delivered_kwh * 60 / job.power_kw))
    earliest = n_slots * SLOT_MINUTES + remaining
    if earliest > job.deadline_min:
        return True
    if earliest + RESTART_PENALTY_MIN <= job.deadline_min:
        return False
    return None


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_late_flags_match_finish_times_and_deadlines_met_pct(scenario, builds):
    b = builds[scenario]
    n = b.inp.n_slots
    for policy in _policies(b):
        late_jobs = set()
        for job in b.inp.jobs:
            o = policy.outcomes[job.id]
            expected = _must_be_late(job, o, n)
            if expected is not None:
                assert o.late == expected, (
                    f"{scenario}/{policy.policy}: job {job.id} late={o.late}, finish "
                    f"{o.finish_min} min vs deadline {job.deadline_min} min")
            assert o.met_deadline == (not o.late)
            if o.late:
                late_jobs.add(job.id)
        flagged = {d.job for d in policy.decisions if d.late}
        assert flagged == late_jobs, (
            f"{scenario}/{policy.policy}: late but not flagged {sorted(late_jobs - flagged)}, "
            f"flagged but on time {sorted(flagged - late_jobs)}")
        assert all(LATE_MARK in d.reason for d in policy.decisions if d.late)
        assert not any(LATE_MARK in d.reason for d in policy.decisions if not d.late)

    # The Scenario_File carries the Headroom decisions and metrics.
    doc = b.doc
    doc_flagged = {d["job"] for d in doc["decisions"] if LATE_MARK in d["reason"]}
    head_late = {j for j, o in b.head.outcomes.items() if o.late}
    assert doc_flagged == head_late
    total = len(b.inp.jobs)
    expected_pct = q(100.0 * (total - len(head_late)) / total, ROUND["pct"]) if total else 100.0
    assert doc["metrics"]["deadlines_met_pct"] == expected_pct


# --- Requirement 23.5 ----------------------------------------------------------

@pytest.mark.parametrize("scenario", SCENARIOS)
def test_event_zoom_has_600_seconds_under_cap(scenario, builds):
    dr = builds[scenario].doc["dr_event"]
    zoom = dr["zoom"]
    assert len(zoom) == ZOOM_SECONDS
    assert [z["s"] for z in zoom] == list(range(ZOOM_SECONDS))
    over = [(z["s"], z["grid_kw"]) for z in zoom if not z["grid_kw"] <= dr["cap_kw"]]
    assert not over, f"{scenario}: grid_kw above cap_kw {dr['cap_kw']} at {over[:5]}"


# --- Requirement 23.6 ----------------------------------------------------------

@pytest.mark.parametrize("scenario", SCENARIOS)
def test_stress_hour_peak(scenario, builds):
    doc = builds[scenario].doc
    stress = [i for i, g in enumerate(doc["grid"]) if g["stress"] is True]
    assert stress, f"{scenario}: no stress slots"
    base_max = max(doc["baseline"][i]["grid_kw"] for i in stress)
    head_max = max(doc["headroom"][i]["grid_kw"] for i in stress)
    assert head_max < base_max, (
        f"{scenario}: headroom stress-slot peak {head_max} kW is not below "
        f"baseline {base_max} kW")


# --- Requirement 23.8 ----------------------------------------------------------

@pytest.mark.parametrize("scenario", SCENARIOS)
def test_two_builds_byte_identical_except_built_at(scenario, written):
    first, second = written[0][scenario], written[1][scenario]
    assert first != second, "built_at should differ between the two runs"
    masked = [_BUILT_AT_RE.subn(b'"built_at":""', x) for x in (first, second)]
    assert [count for _, count in masked] == [1, 1]
    assert masked[0][0] == masked[1][0], f"{scenario}: builds differ outside meta.built_at"


# --- Requirement 23.9 ----------------------------------------------------------

@pytest.mark.parametrize("scenario", SCENARIOS)
def test_scenario_file_conforms_to_output_contract(scenario, written, builds):
    doc = json.loads(written[0][scenario].decode("utf-8"))
    errors = validate(doc, scenario=scenario)
    assert not errors, f"{scenario}: " + "; ".join(errors[:10])
    assert tuple(doc) == tuple(TOP_KEYS)
    assert all(len(doc[k]) == N_SLOTS for k in ("grid", "baseline", "headroom"))
    # The written file is the in-memory document built from the same inputs.
    assert written[0][scenario] == dumps(builds[scenario].doc).encode("utf-8")


# --- Requirement 15.6 ----------------------------------------------------------

@pytest.mark.parametrize("scenario", SCENARIOS)
def test_inference_sla_is_100(scenario, builds):
    assert builds[scenario].doc["metrics"]["inference_sla_pct"] == 100
    assert builds[scenario].metrics.inference_sla_pct == 100


# --- Requirements 23.10, 23.11 -------------------------------------------------

@pytest.mark.parametrize("missing", [
    "region-data__PACE__D-DF-NG-TI__o000000.json",   # first page of an endpoint
    "fuel-type-data__BPAT__all__o005000.json",       # a later page
])
def test_missing_raw_cache_file_fails_naming_it_without_fetching(missing, tmp_path, capsys,
                                                                 monkeypatch):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    cache = tmp_path / "raw"
    shutil.copytree(RAW_DIR, cache)
    (cache / missing).unlink()

    with pytest.raises(CacheMissingError) as exc:
        load_grid_data(cache_dir=cache)
    assert exc.value.path.name == missing
    assert missing in str(exc.value)

    out, proc = tmp_path / "out", tmp_path / "proc"
    rc = main(out_dir=out, processed_dir=proc, cache_dir=cache, built_at=_BUILT_AT_1)
    assert rc != 0
    assert missing in capsys.readouterr().err
    assert not out.exists() and not proc.exists(), "nothing may be written"
