"""The operator briefing: facts and evidence from code, a checked draft, and its endpoint.

No test calls Gemini. ``ScriptedLlm`` (from the analyst tests) plays back what a model
might write, including drafts the checks must stop.
"""

from __future__ import annotations

import json
import re

import pytest

from analyst import briefing
from analyst import handler as lambda_handler
from analyst.limits import Limits
from analyst.llm import LlmError
from sim.run_scenarios import SCENARIOS_DIR
from sim.tests.test_analyst import ScriptedLlm


def _good(evidence: str = "d1") -> dict:
    return {
        "headline": "The plan saves {{plan.usd_saved}} by keeping the site peak at {{plan.site_peak_kw}}.",
        "points": [
            {"text": "Jobs wait for cheaper hours and storage covers the busiest ones.", "evidence": [evidence]},
            {"text": "With a real carbon forecast it saves {{risk.forecast_usd_saved}}.", "evidence": ["test:forecast"]},
            {"text": "A battery failure during the event misses the cut by {{risk.battery_dr_shortfall_kw}}.",
             "evidence": ["test:battery_dr"]},
        ],
    }


@pytest.fixture(autouse=True)
def _fresh_cache():
    lambda_handler._briefings.clear()
    yield
    lambda_handler._briefings.clear()


# --- Facts and evidence -------------------------------------------------------------

@pytest.mark.parametrize("scenario", ["peak", "sun"])
def test_plan_facts_match_what_the_page_shows(scenario):
    doc = json.loads((SCENARIOS_DIR / f"{scenario}.json").read_text(encoding="utf-8"))
    facts = briefing.inputs(scenario).facts
    m = doc["metrics"]
    # The page rounds halves up (web/src/lib/format.ts); so must the briefing.
    assert facts["plan.usd_saved"] == briefing._usd(m["usd_saved"])
    assert facts["plan.site_peak_kw"] == briefing._kw(m["headroom_site_peak_kw"])
    assert facts["plan.deadlines_met_pct"] == "100%"


def test_formatting_rounds_halves_up_like_the_page():
    assert briefing._kw(1452.5) == "1,453 kW"
    assert briefing._usd(29333.5) == "$29,334"
    assert briefing._usd(-805.5) == "-$805"
    assert briefing._tonnes(19.95) == "20.0 t"
    assert briefing._pct(99.99) == "99%"


@pytest.mark.parametrize("scenario", ["peak", "sun"])
def test_stress_facts_come_from_the_committed_stress_file(scenario):
    stress = json.loads((SCENARIOS_DIR / "stress.json").read_text(encoding="utf-8"))
    families = {f["id"]: f for f in stress["scenarios"][scenario]["families"]}
    facts = briefing.inputs(scenario).facts
    assert facts["risk.forecast_usd_saved"] == briefing._usd(families["forecast"]["usd_saved"]["p50"])
    assert facts["risk.battery_dr_shortfall_kw"] == briefing._kw(families["battery_dr"]["dr_shortfall_max_kw"])
    assert facts["replay.windows"] == str(len(stress["replay"]["windows"]))


@pytest.mark.parametrize("scenario", ["peak", "sun"])
def test_decision_evidence_points_at_real_decisions(scenario):
    doc = json.loads((SCENARIOS_DIR / f"{scenario}.json").read_text(encoding="utf-8"))
    keys = {f"{d['t']}|{d['job']}" for d in doc["decisions"]}
    decisions = [e for e in briefing.inputs(scenario).evidence if e.kind == "decision"]
    assert 1 <= len(decisions) <= 6
    assert all(e.key in keys for e in decisions)
    assert [e.id for e in decisions] == [f"d{i}" for i in range(1, len(decisions) + 1)]


def test_decision_labels_say_route_cost_not_bill_dollars():
    for e in briefing.inputs("peak").evidence:
        if e.kind == "decision" and "$" in e.label:
            assert "route cost" in e.label


def test_event_decisions_are_offered_as_evidence():
    labels = [e.label for e in briefing.inputs("sun").evidence]
    assert any("forced by the demand-response event" in label for label in labels)


def test_every_test_is_offered_as_evidence():
    ids = {e.id for e in briefing.inputs("peak").evidence}
    assert {"test:forecast", "test:runtime", "test:battery_random", "test:battery_dr", "test:everything", "test:replay"} <= ids


def test_unknown_scenario_is_refused():
    with pytest.raises(ValueError):
        briefing.inputs("live")


# --- The draft and its checks --------------------------------------------------------

def test_a_clean_draft_is_shown_with_its_evidence():
    result = briefing.brief("peak", ScriptedLlm(_good()))
    assert result["status"] == "briefed"
    assert result["source"] == "model_verified"
    assert "{{" not in result["headline"] and "$29,334" in result["headline"]
    cited = {i for p in result["points"] for i in p["evidence"]}
    assert {e["id"] for e in result["evidence"]} == cited
    assert all(e["label"] and e["kind"] in ("decision", "test") for e in result["evidence"])


def test_every_figure_shown_is_a_fact():
    result = briefing.brief("peak", ScriptedLlm(_good()))
    facts = set(briefing.inputs("peak").facts.values())
    text = " ".join([result["headline"], *(p["text"] for p in result["points"])])
    for figure in re.findall(r"-?\$[\d,]+|[\d,]+ kW", text):
        assert any(figure in fact for fact in facts), figure


@pytest.mark.parametrize("bad", [
    {"headline": "Saves $29,334.", "points": _good()["points"]},
    {"headline": "Saves {{plan.usd_saved}}, about twice as much.", "points": _good()["points"]},
    {"headline": "Saves {{plan.made_up}}.", "points": _good()["points"]},
    {"headline": "Saves {{plan.usd_saved}}.", "points": _good()["points"][:2]},
    {"headline": "Saves {{plan.usd_saved}}.", "points": _good()["points"] * 2},
    {"headline": "Saves {{plan.usd_saved}}.", "points": [{**p, "evidence": []} for p in _good()["points"]]},
    {"headline": "Saves {{plan.usd_saved}}.", "points": [{**p, "evidence": ["d99"]} for p in _good()["points"]]},
    {"headline": "", "points": _good()["points"]},
    {"points": _good()["points"]},
])
def test_a_bad_draft_gets_one_rewrite(bad):
    llm = ScriptedLlm(bad, _good())
    result = briefing.brief("peak", llm)
    assert result["source"] == "model_verified"
    assert "rejected" in llm.calls[1]["user"]


def test_three_bad_drafts_fall_back_to_the_briefing_written_by_code():
    bad = {"headline": "Saves 29 thousand dollars.", "points": _good()["points"]}
    result = briefing.brief("peak", ScriptedLlm(bad, bad, bad))
    assert result["source"] == "engine_only"
    assert result["headline"].startswith("The plan saves $29,334")
    assert all("{{" not in p["text"] for p in result["points"])


def test_a_failed_model_call_still_gives_a_briefing():
    result = briefing.brief("sun", ScriptedLlm(LlmError("down")))
    assert result["source"] == "engine_only"
    assert result["points"] and result["evidence"]


def test_no_rewrite_when_time_is_nearly_up():
    ticks = iter([0.0, 0.0, 49.0])    # start, first call, before the rewrite
    llm = ScriptedLlm({"headline": "x", "points": []}, _good())
    result = briefing.brief("peak", llm, clock=lambda: next(ticks))
    assert len(llm.calls) == 1
    assert result["source"] == "engine_only"


def test_the_code_briefing_passes_its_own_checks():
    b = briefing.inputs("peak")
    for scenario in ("peak", "sun"):
        headline, points, fact_ids = briefing.engine_only(briefing.inputs(scenario))
        assert headline and 3 <= len(points) <= 5 and fact_ids
    assert {i for p in briefing.engine_only(b)[1] for i in p["evidence"]} <= {e.id for e in b.evidence}


def test_the_code_briefing_would_pass_the_model_checks():
    """It must follow the same rules it falls back from, so check it as a draft."""
    for scenario in ("peak", "sun"):
        b = briefing.inputs(scenario)
        first = next(e.id for e in b.evidence if e.kind == "decision")
        source = {
            "headline": "The plan saves {{plan.usd_saved}} against running every job on arrival.",
            "points": [
                {"text": "Most of it comes from holding the site peak at {{plan.site_peak_kw}} instead of "
                         "{{plan.baseline_site_peak_kw}}.", "evidence": [first]},
                {"text": "With a real carbon forecast instead of foresight, it saves {{risk.forecast_usd_saved}}.",
                 "evidence": ["test:forecast"]},
                {"text": "If the battery fails during the demand-response event, the cut is missed by "
                         "{{risk.battery_dr_shortfall_kw}}.", "evidence": ["test:battery_dr"]},
                {"text": "Across {{replay.windows}} replayed windows the median saving is {{replay.median_usd}}.",
                 "evidence": ["test:replay"]},
            ],
        }
        assert briefing._draft_problems(source, b) == []
        assert briefing.render(source["headline"], b.facts) == briefing.engine_only(b)[0]


def test_the_two_battery_tests_are_told_apart():
    draft = {**_good(), "points": [
        {"text": "The cut is missed in {{risk.battery_random_cut_missed_runs}} stress runs.",
         "evidence": ["test:battery_dr"]},
        *_good()["points"][1:],
    ]}
    found = briefing._draft_problems(draft, briefing.inputs("peak"))
    assert any("add test:battery_random" in p for p in found)


def test_meanings_hold_no_number_words():
    """A meaning the model copies must not bring a figure with it, as "eight hours" once did."""
    from analyst.verify import problems
    for scenario in ("peak", "sun"):
        b = briefing.inputs(scenario)
        for fact_id, meaning in b.meanings.items():
            assert not [p for p in problems("{{plan.usd_saved}} " + meaning, b.facts, max_chars=10_000)
                        if "number word" in p or "figure directly" in p], fact_id


def test_a_test_figure_must_cite_its_test():
    draft = {**_good(), "points": [
        {"text": "If jobs run long it saves {{risk.runtime_low_usd}} at worst.", "evidence": ["d1"]},
        *_good()["points"][1:],
    ]}
    found = briefing._draft_problems(draft, briefing.inputs("sun"))
    assert any("without citing it: add test:runtime" in p for p in found)


def test_stress_figures_stay_out_of_the_headline():
    draft = {**_good(), "headline": "Savings fall to {{risk.forecast_usd_saved}} with a forecast."}
    assert any("out of the headline" in p for p in briefing._draft_problems(draft, briefing.inputs("peak")))


def test_the_reply_lists_every_figure_it_quotes_with_its_meaning():
    result = briefing.brief("peak", ScriptedLlm(_good()))
    ids = [f["id"] for f in result["figures"]]
    assert ids == list(dict.fromkeys(["plan.usd_saved", "plan.site_peak_kw", "risk.forecast_usd_saved",
                                      "risk.battery_dr_shortfall_kw"]))
    assert all(f["text"] and f["means"] for f in result["figures"])


def test_schema_limits_evidence_to_the_offered_ids_and_has_no_nullable_enum():
    s = briefing.schema(["d1", "test:forecast"])
    items = s["properties"]["points"]["items"]["properties"]["evidence"]["items"]
    assert items == {"type": "string", "enum": ["d1", "test:forecast"]}
    assert "null" not in json.dumps(s)


def test_prompt_holds_the_rules_and_only_code_figures():
    llm = ScriptedLlm(_good())
    briefing.brief("peak", llm)
    system = llm.calls[0]["system"]
    assert "never write a number yourself" in system
    assert "Never call them probabilities" in system
    assert "puts no price on a missed demand-response cut" in system
    assert "never add them up" in system
    b = briefing.inputs("peak")
    for fact_id, text in b.facts.items():
        assert text in system and b.meanings[fact_id] in system


def test_every_fact_says_what_it_means():
    for scenario in ("peak", "sun"):
        b = briefing.inputs(scenario)
        assert set(b.meanings) == set(b.facts)
        assert all(len(m) > 10 for m in b.meanings.values())
    assert "decisions, not jobs" in briefing.inputs("peak").meanings["plan.move_decisions"]


@pytest.mark.parametrize("text", [
    "The plan moves one training run to another region.",
    "Only a single job misses its deadline.",
    "A few runs miss their deadlines.",
    "Several windows lose money.",
    "The event lasts one hour.",
])
def test_a_count_written_as_one_is_a_figure(text):
    draft = {**_good(), "points": [{"text": text, "evidence": ["d1"]}, *_good()["points"][1:]]}
    assert any("counts something itself" in p for p in briefing._draft_problems(draft, briefing.inputs("peak")))


def test_one_as_ordinary_english_is_allowed():
    draft = {**_good(), "points": [{"text": "One reason is the lower peak.", "evidence": ["d1"]},
                                   *_good()["points"][1:]]}
    assert briefing._draft_problems(draft, briefing.inputs("peak")) == []


def test_a_long_headline_is_rejected_by_words():
    long_headline = "The plan saves {{plan.usd_saved}} " + "by doing a careful thing " * 6
    draft = {**_good(), "headline": long_headline}
    assert any("at most" in p for p in briefing._draft_problems(draft, briefing.inputs("peak")))


# --- The endpoint ----------------------------------------------------------------------

class Counters:
    def __init__(self):
        self.calls = 0

    def update_item(self, **kwargs):
        self.calls += 1

    def get_item(self, **kwargs):
        return {}


def _event(body, path="/api/briefing", method="POST"):
    return {"requestContext": {"http": {"method": method}}, "rawPath": path, "headers": {},
            "body": json.dumps(body)}


@pytest.fixture
def endpoint(monkeypatch):
    counters = Counters()
    monkeypatch.setattr(lambda_handler, "_limits",
                        Limits(client=counters, table="t", daily_cap=100, hourly_per_visitor=100))
    llm = ScriptedLlm(*[_good()] * 6)
    llm.model, llm.input_tokens, llm.output_tokens = "scripted", 0, 0
    monkeypatch.setattr(lambda_handler.llm_module, "from_environment", lambda: llm)
    return llm, counters


def test_endpoint_returns_a_briefing(endpoint):
    reply = lambda_handler.handler(_event({"scenario": "peak"}), None)
    assert reply["statusCode"] == 200
    assert json.loads(reply["body"])["status"] == "briefed"


def test_a_repeat_is_served_from_cache_and_costs_nothing(endpoint):
    llm, counters = endpoint
    first = lambda_handler.handler(_event({"scenario": "peak"}), None)
    used = counters.calls
    second = lambda_handler.handler(_event({"scenario": "peak"}), None)
    assert second["body"] == first["body"]
    assert len(llm.calls) == 1
    assert counters.calls == used


def test_a_code_written_briefing_is_not_cached(endpoint, monkeypatch):
    llm, _ = endpoint
    llm.replies[:] = [LlmError("down"), _good()]
    first = json.loads(lambda_handler.handler(_event({"scenario": "sun"}), None)["body"])
    second = json.loads(lambda_handler.handler(_event({"scenario": "sun"}), None)["body"])
    assert first["source"] == "engine_only"
    assert second["source"] == "model_verified"


@pytest.mark.parametrize("body", [{}, {"scenario": "live"}, {"scenario": 3}])
def test_bad_requests_cost_nothing(endpoint, body):
    llm, counters = endpoint
    reply = lambda_handler.handler(_event(body), None)
    assert reply["statusCode"] == 400
    assert not llm.calls and counters.calls == 0


def test_the_limits_apply_to_a_new_briefing(monkeypatch, endpoint):
    llm, _ = endpoint

    class Full:
        def get_item(self, **kwargs):
            return {}

        def update_item(self, **kwargs):
            error = RuntimeError("limit")
            error.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
            raise error

    monkeypatch.setattr(lambda_handler, "_limits", Limits(client=Full(), table="t", daily_cap=1, hourly_per_visitor=1))
    reply = lambda_handler.handler(_event({"scenario": "peak"}), None)
    assert reply["statusCode"] == 429
    assert not llm.calls


def test_questions_still_reach_the_what_if_analyst(endpoint):
    reply = lambda_handler.handler(_event({"question": "", "scenario": "peak"}, path="/api/whatif"), None)
    assert reply["statusCode"] == 400
    assert "question" in json.loads(reply["body"])["message"].lower()
