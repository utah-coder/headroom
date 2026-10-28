"""Property 37: Determinism (task 9.7; Requirements 10.19, 12.4, 16.13).

For any seed, generating jobs twice gives identical job lists, generating idle
capacity twice gives identical values, and building a Scenario_File twice
gives byte-identical ``dumps`` output once ``meta.built_at`` is fixed.

``test_acceptance.py`` already checks two in-process builds for the configured
seed. This file adds:

* the property over arbitrary seeds;
* builds in separate interpreters with different ``PYTHONHASHSEED`` values,
  which is where set or dict iteration order could differ and an in-process
  check cannot see it;
* the committed ``web/public/scenarios/*.json`` equal a fresh build from
  ``data/raw`` (``deploy.ps1 -SkipSim`` ships them without rebuilding).
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from sim.config import WINDOW_HOURS
from sim.contract import SCENARIO_NAMES, dumps
from sim.jobs import generate
from sim.run_scenarios import build_scenario, load_grid_data
from sim.site import idle_capacity_kw

ROOT = Path(__file__).resolve().parents[2]
SCENARIO_DIR = ROOT / "web" / "public" / "scenarios"
N_SLOTS = WINDOW_HOURS * 4
FIXED_BUILT_AT = "2000-01-01T00:00:00Z"
HASH_SEEDS = ("0", "4242")

seeds = st.integers(min_value=0, max_value=2**32 - 1)
scenarios = st.sampled_from(SCENARIO_NAMES)


@functools.cache
def _grid_data():
    # Read once, offline, from the committed Raw_Cache. Sharing it between
    # builds also checks that a build does not mutate its inputs.
    return load_grid_data(None)


def _build_outcome(name: str, seed: int) -> tuple[str, str]:
    """``dumps`` of the built document, or the error it raised."""
    try:
        doc = build_scenario(name, _grid_data(), seed, built_at=FIXED_BUILT_AT)
    except Exception as e:  # noqa: BLE001 - a deterministic failure is still deterministic
        return ("error", f"{type(e).__name__}: {e}")
    return ("ok", dumps(doc))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@given(n_slots=st.integers(min_value=0, max_value=N_SLOTS), seed=seeds, scenario=scenarios)
def test_jobs_are_deterministic(n_slots, seed, scenario):
    assert generate(n_slots, seed, scenario) == generate(n_slots, seed, scenario)


@given(n_hours=st.integers(min_value=0, max_value=WINDOW_HOURS), seed=seeds, scenario=scenarios)
def test_idle_capacity_is_deterministic(n_hours, seed, scenario):
    assert idle_capacity_kw(n_hours, seed, scenario) == idle_capacity_kw(n_hours, seed, scenario)


# A full build takes about 0.3 s, so this property runs fewer examples than the profile default.
@settings(max_examples=10)
@given(seed=seeds, scenario=scenarios)
def test_scenario_build_is_deterministic(seed, scenario):
    assert _build_outcome(scenario, seed) == _build_outcome(scenario, seed)


_SUBPROCESS_BUILD = f"""
import hashlib, json
from sim.contract import dumps
from sim.run_scenarios import build_scenario, load_grid_data
data = load_grid_data(None)
out = {{}}
for name in {SCENARIO_NAMES!r}:
    text = dumps(build_scenario(name, data, built_at={FIXED_BUILT_AT!r}))
    out[name] = hashlib.sha256(text.encode("utf-8")).hexdigest()
print(json.dumps(out))
"""


@pytest.fixture(scope="module")
def subprocess_digests() -> dict[str, dict[str, str]]:
    """Per PYTHONHASHSEED: scenario name -> sha256 of a fresh default-seed build."""
    out: dict[str, dict[str, str]] = {}
    for hash_seed in HASH_SEEDS:
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        proc = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_BUILD],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=300, check=False,
        )
        assert proc.returncode == 0, f"PYTHONHASHSEED={hash_seed} build failed:\n{proc.stderr[-2000:]}"
        out[hash_seed] = json.loads(proc.stdout.strip().splitlines()[-1])
    return out


def test_builds_match_across_hash_seeds(subprocess_digests):
    first, *rest = HASH_SEEDS
    for other in rest:
        assert subprocess_digests[other] == subprocess_digests[first], (
            f"PYTHONHASHSEED={first} and {other} built different scenario files"
        )


@pytest.mark.parametrize("name", SCENARIO_NAMES)
def test_committed_scenario_file_matches_fresh_build(name, subprocess_digests):
    path = SCENARIO_DIR / f"{name}.json"
    if not path.exists():
        pytest.skip(f"{path.name} is not committed (scenario cut)")
    committed: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    committed["meta"]["built_at"] = FIXED_BUILT_AT
    assert _digest(dumps(committed)) == subprocess_digests[HASH_SEEDS[0]][name], (
        f"web/public/scenarios/{name}.json differs from a fresh build of data/raw; "
        f"run `python -m sim.run_scenarios` and commit the result"
    )
