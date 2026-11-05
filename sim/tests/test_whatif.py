"""The what-if layer: it must change inputs and nothing else."""

import json
from pathlib import Path

import pytest

from sim import config, metrics, run_scenarios, scheduler, site, whatif
from sim.contract import dumps
from sim.run_scenarios import build_scenario, load_grid_data

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def data():
    return load_grid_data()


@pytest.mark.parametrize("scenario", whatif.SCENARIOS)
def test_no_changes_reproduces_the_committed_scenario(scenario, data):
    run = whatif.run(scenario, {}, data)
    committed = json.loads((ROOT / "web" / "public" / "scenarios" / f"{scenario}.json").read_text("utf-8"))
    built = dict(run.doc, meta=dict(run.doc["meta"], built_at=committed["meta"]["built_at"]))
    assert json.loads(dumps(built)) == committed


@pytest.mark.parametrize("scenario", whatif.SCENARIOS)
def test_a_what_if_leaves_the_engine_as_it_found_it(scenario, data):
    before = dumps(build_scenario(scenario, data, built_at="2026-01-01T00:00:00Z"))
    whatif.run(scenario, {"demand_charge_usd_per_kw": 40.0, "battery_power_kw": 6000.0,
                          whatif.MOVE_SWITCH: False}, data)
    assert dumps(build_scenario(scenario, data, built_at="2026-01-01T00:00:00Z")) == before


def test_overlay_restores_values_even_when_the_run_fails():
    original = scheduler.DEMAND_CHARGE_PER_KW
    with pytest.raises(RuntimeError):
        with whatif.overlay({"demand_charge_usd_per_kw": 99.0}):
            assert scheduler.DEMAND_CHARGE_PER_KW == 99.0
            assert metrics.DEMAND_CHARGE_PER_KW == 99.0
            raise RuntimeError("boom")
    assert scheduler.DEMAND_CHARGE_PER_KW == original
    assert metrics.DEMAND_CHARGE_PER_KW == original


def test_overlay_reaches_every_module_that_holds_a_name():
    with whatif.overlay({"battery_power_kw": 7000.0, "dr_shed_kw": 1234.0}):
        for module in (config, scheduler, run_scenarios, site):
            assert module.BATTERY["power_kw"] == 7000.0
            assert module.BATTERY["capacity_kwh"] == 16000  # The other field is kept.
        assert run_scenarios.DR_SHED_KW == 1234.0
    assert config.BATTERY["power_kw"] == 4000


def test_a_changed_cut_moves_the_grid_cap(data):
    # grid_import_cap_kw's default argument is bound at import, so the build must pass the
    # overridden cut explicitly. Without that the cap silently stays at the 4,000 kW cut.
    base = whatif.current("sun", data).doc["dr_event"]
    changed = whatif.run("sun", {"dr_shed_kw": 3000.0}, data).doc["dr_event"]
    assert changed["requested_shed_kw"] == 3000
    assert changed["cap_kw"] == pytest.approx(base["cap_kw"] + 1000, abs=0.2)


def test_two_fields_of_one_device_combine():
    with whatif.overlay({"battery_power_kw": 7000.0, "battery_capacity_kwh": 30000.0}):
        assert (scheduler.BATTERY["power_kw"], scheduler.BATTERY["capacity_kwh"]) == (7000.0, 30000.0)


# One value per knob that the sweep showed moves the result in the peak scenario.
EFFECTIVE = {
    "demand_charge_usd_per_kw": 36.0,
    "onpeak_usd_per_kwh": 0.2,
    "offpeak_usd_per_kwh": 0.2,
    "carbon_usd_per_t": 200.0,
    "battery_power_kw": 8000.0,
    "battery_capacity_kwh": 40000.0,
    "flywheel_capacity_kwh": 2000.0,
    "dr_shed_kw": 2000.0,
    "move_fee_usd": 500.0,
    "alt_region_usd_per_kwh": 0.5,
}


def test_every_knob_is_listed_here_or_known_to_be_inert():
    # Flywheel power changes nothing above its current rating (seen in the bounds sweep);
    # its lower bound is covered by test_too_little_storage_is_a_refusal_not_a_crash.
    assert set(EFFECTIVE) | {"flywheel_power_kw"} == {k.key for k in whatif.KNOBS}


@pytest.mark.parametrize("key,value", sorted(EFFECTIVE.items()))
def test_each_knob_really_reaches_the_engine(key, value, data):
    base = whatif.current("peak", data)
    changed = whatif.run("peak", {key: value}, data)
    assert changed.values != base.values or changed.grid_kw != base.grid_kw


def test_the_move_switch_reaches_the_engine(data):
    assert whatif.run("peak", {whatif.MOVE_SWITCH: False}, data).values["move_decisions"] == 0
    assert whatif.current("peak", data).values["move_decisions"] > 0


def test_inference_never_moves_in_a_what_if(data):
    run = whatif.run("peak", {"demand_charge_usd_per_kw": 90.0, "carbon_usd_per_t": 400.0}, data)
    assert run.values["inference_sla_pct"] == 100


@pytest.mark.parametrize("change", [{"battery_power_kw": 500.0}, {"flywheel_power_kw": 500.0},
                                    {"battery_capacity_kwh": 1000.0}, {"dr_shed_kw": 10000.0}])
def test_too_little_storage_is_a_refusal_not_a_crash(change, data):
    with pytest.raises(whatif.EngineRefused, match="demand-response"):
        whatif.run("peak", change, data)


@pytest.mark.parametrize("scenario", whatif.SCENARIOS)
@pytest.mark.parametrize("knob", whatif.KNOBS, ids=lambda k: k.key)
def test_every_knob_at_both_bounds_gives_a_result_or_a_refusal(scenario, knob, data):
    for bound in knob.bounds:
        try:
            whatif.run(scenario, {knob.key: bound}, data)
        except whatif.EngineRefused:
            pass


class TestValidate:
    def test_accepts_values_at_the_bounds(self):
        low, high = whatif.KNOBS[0].bounds
        clean, problems = whatif.validate({whatif.KNOBS[0].key: low})
        assert clean and not problems
        clean, problems = whatif.validate({whatif.KNOBS[0].key: high})
        assert clean and not problems

    def test_null_means_no_change(self):
        assert whatif.validate({"demand_charge_usd_per_kw": None, whatif.MOVE_SWITCH: None}) == ({}, [])

    @pytest.mark.parametrize("value", [-1, 100.01, 1e308, float("inf"), float("-inf")])
    def test_rejects_out_of_range(self, value):
        clean, problems = whatif.validate({"demand_charge_usd_per_kw": value})
        assert not clean and "between" in problems[0]

    @pytest.mark.parametrize("value", ["36", True, [36], {"a": 1}, float("nan")])
    def test_rejects_non_numbers(self, value):
        clean, problems = whatif.validate({"demand_charge_usd_per_kw": value})
        assert not clean and "must be a number" in problems[0]

    def test_rejects_unknown_inputs(self):
        clean, problems = whatif.validate({"PUE": 1.0, "__class__": 1})
        assert not clean and len(problems) == 2

    def test_move_switch_must_be_boolean(self):
        clean, problems = whatif.validate({whatif.MOVE_SWITCH: 1})
        assert not clean and problems

    def test_every_knob_has_bounds_around_todays_value(self):
        for knob in whatif.KNOBS:
            low, high = knob.bounds
            assert low <= knob.read() <= high


class TestCompare:
    def test_no_change_shows_no_change(self, data):
        base = whatif.current("peak", data)
        cmp = whatif.compare(base, base)
        assert all(f.direction == "unchanged" and f.current == f.whatif for f in cmp.figures)
        assert cmp.inputs == ()

    def test_figures_match_the_sites_kpis(self, data):
        m = json.loads((ROOT / "web" / "public" / "scenarios" / "peak.json").read_text("utf-8"))["metrics"]
        base = whatif.current("peak", data)
        cmp = whatif.compare(base, base)
        assert cmp.facts["current.usd_saved"] == f"${m['usd_saved']:,.0f}"
        assert cmp.facts["current.headroom_kw"] == f"{m['headroom_kw']:,.0f} kW"
        assert cmp.facts["current.co2_avoided_t"] == f"{m['co2_avoided_t']:,.1f} t"

    def test_bill_matches_the_gate_table(self, data):
        # DECISIONS.md, cheapest-hour gate table (energy + demand). The site bill here
        # also counts the fee per moved job, so Headroom's figure is that total plus fees.
        run = whatif.current("peak", data)
        cmp = whatif.compare(run, run)
        assert cmp.facts["current.baseline_bill_usd"] == "$295,473"
        expected = run.values["baseline_bill_usd"] - run.values["usd_saved"]
        assert run.values["headroom_bill_usd"] == pytest.approx(expected, abs=0.01)

    def test_a_change_has_a_direction_and_an_unsigned_size(self, data):
        base = whatif.current("peak", data)
        cmp = whatif.compare(base, whatif.run("peak", {"move_fee_usd": 1000.0}, data))
        saved = next(f for f in cmp.figures if f.id == "usd_saved")
        assert saved.direction == "down"
        assert not saved.change.startswith("-") and not saved.change.startswith("+")
        assert saved.whatif.startswith("-$")

    def test_the_input_change_is_quotable(self, data):
        base = whatif.current("peak", data)
        cmp = whatif.compare(base, whatif.run("peak", {"demand_charge_usd_per_kw": 36.0}, data))
        assert cmp.facts["input_current.demand_charge_usd_per_kw"] == "$18.00/kW"
        assert cmp.facts["input_whatif.demand_charge_usd_per_kw"] == "$36.00/kW"

    def test_refuses_to_compare_across_scenarios(self, data):
        with pytest.raises(ValueError):
            whatif.compare(whatif.current("peak", data), whatif.current("sun", data))
