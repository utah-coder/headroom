"""The analyst workflow, verifier, limits and handler, with a scripted model.

No test calls Bedrock. ``ScriptedLlm`` returns what a model might, including the
replies a real model must not get past the verifier.
"""

import json

import pytest

from analyst import handler as lambda_handler
from analyst.limits import Limits, visitor_id
from analyst.llm import BedrockLlm, LlmError, LlmRefused
from analyst.verify import problems, render
from analyst.workflow import CAVEAT, OUT_OF_SCOPE, answer, understand_schema
from sim import whatif

FACTS = {"whatif.usd_saved": "$12,345", "change.usd_saved": "$678", "current.usd_saved": "$13,023"}
MAX = 1200


def no_changes() -> dict:
    return {k.key: None for k in whatif.KNOBS} | {whatif.MOVE_SWITCH: None}


def understood(scenario="peak", in_scope=True, **changes) -> dict:
    return {"in_scope": in_scope, "scenario": scenario, "changes": no_changes() | changes}


class ScriptedLlm:
    """Plays back replies in order. An Exception in the script is raised."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def structured(self, system, user, schema, max_tokens, timeout_s=None):
        self.calls.append({"system": system, "user": user, "schema": schema, "timeout_s": timeout_s})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


GOOD = {"answer": "In this run the site saves {{whatif.usd_saved}}, against {{current.usd_saved}} today."}


class TestVerifier:
    def test_passes_a_draft_that_quotes_only_placeholders(self):
        assert problems("Savings reach {{whatif.usd_saved}} in this run.", FACTS, max_chars=MAX) == []

    def test_renders_exact_engine_text(self):
        assert render("Saves {{ whatif.usd_saved }} now.", FACTS) == "Saves $12,345 now."

    @pytest.mark.parametrize("draft", [
        "Savings reach $12,345.",
        "Savings reach {{whatif.usd_saved}}, up 5%.",
        "About 3 jobs moved, saving {{whatif.usd_saved}}.",
        "Saves {{whatif.usd_saved}} by 2030.",
        "Saves {{whatif.usd_saved}} or ١٢ dollars.",  # Arabic-Indic digits.
        "Saves {{whatif.usd_saved}} or ５ dollars.",  # Fullwidth digit.
        "Saves {{whatif.usd_saved}} in ½ the time.",  # Vulgar fraction.
        "Saves {{whatif.usd_saved}}, Ⅳ times more.",  # Roman numeral character.
    ])
    def test_rejects_a_figure_written_directly(self, draft):
        assert any("writes a figure" in p for p in problems(draft, FACTS, max_chars=MAX))

    @pytest.mark.parametrize("draft", [
        "Savings {{whatif.usd_saved}}, about twice as much.",
        "Savings {{whatif.usd_saved}}, roughly double.",
        "Savings {{whatif.usd_saved}}, doubling today's.",
        "Savings {{whatif.usd_saved}}, half of today's.",
        "Savings {{whatif.usd_saved}}, thirty percent more.",
        "Savings {{whatif.usd_saved}}, three thousand more.",
        "Savings {{whatif.usd_saved}}, a Quarter more.",
        "Savings {{whatif.usd_saved}} and zero misses.",
    ])
    def test_rejects_a_number_written_as_a_word(self, draft):
        assert any("number word" in p for p in problems(draft, FACTS, max_chars=MAX))

    def test_rejects_an_invented_fact_id(self):
        found = problems("Saves {{whatif.imaginary}}.", FACTS, max_chars=MAX)
        assert any("do not exist" in p for p in found)

    def test_rejects_prose_with_no_figures(self):
        assert any("no figures" in p for p in problems("It saves a lot.", FACTS, max_chars=MAX))

    @pytest.mark.parametrize("draft", ["Saves {{whatif.usd_saved}} {{bad id}}.", "Saves {{whatif.usd_saved}} {oops}."])
    def test_rejects_a_malformed_placeholder(self, draft):
        assert any("malformed" in p for p in problems(draft, FACTS, max_chars=MAX))

    def test_rejects_empty_and_overlong_drafts(self):
        assert problems("  ", FACTS, max_chars=MAX)
        assert any("longer" in p for p in problems("{{whatif.usd_saved}} " + "x" * MAX, FACTS, max_chars=MAX))

    @pytest.mark.parametrize("gas", ["CO2", "CO₂"])
    def test_the_gas_name_is_allowed(self, gas):
        assert problems(f"{gas} avoided is {{{{whatif.usd_saved}}}}.", FACTS, max_chars=MAX) == []

    def test_ordinary_words_are_not_number_words(self):
        draft = "One reason: the often-cited weight of neighbours, someone halfway, {{whatif.usd_saved}}."
        assert problems(draft, FACTS, max_chars=MAX) == []


class TestWorkflow:
    def test_a_clean_run(self):
        llm = ScriptedLlm(understood(demand_charge_usd_per_kw=36.0), GOOD)
        out = answer("What if the demand charge doubled?", "peak", llm)
        assert out["status"] == "answered"
        assert out["answer_source"] == "model_verified"
        assert out["caveat"] == CAVEAT
        assert out["inputs"][0]["whatif"] == "$36.00/kW"
        assert "{{" not in out["answer"]
        assert len(out["series"]["whatif_grid_kw"]) == 192

    def test_every_figure_in_the_answer_is_an_engine_figure(self):
        llm = ScriptedLlm(understood(demand_charge_usd_per_kw=36.0), GOOD)
        out = answer("q", "peak", llm)
        saved = next(f for f in out["figures"] if f["id"] == "usd_saved")
        assert out["answer"] == (f"In this run the site saves {saved['whatif']}, against {saved['current']} today.")

    def test_engine_runs_the_real_change(self):
        llm = ScriptedLlm(understood(demand_charge_usd_per_kw=36.0), GOOD)
        out = answer("q", "peak", llm)
        direct = whatif.run("peak", {"demand_charge_usd_per_kw": 36.0})
        assert out["series"]["whatif_grid_kw"] == list(direct.grid_kw)

    def test_a_draft_with_a_made_up_number_gets_one_rewrite(self):
        llm = ScriptedLlm(understood(), {"answer": "It saves $99,999."}, GOOD)
        out = answer("How does it do?", "peak", llm)
        assert out["answer_source"] == "model_verified"
        assert "$99,999" not in out["answer"]
        assert "rejected" in llm.calls[2]["user"]

    def test_two_bad_drafts_fall_back_to_engine_text(self):
        llm = ScriptedLlm(understood(move_fee_usd=500.0), {"answer": "Saves $99,999."}, {"answer": "Saves 88,888."})
        out = answer("q", "peak", llm)
        assert out["answer_source"] == "engine_only"
        assert "99,999" not in out["answer"] and "88,888" not in out["answer"]
        assert out["answer"].startswith("In this run:")
        assert llm.replies == []

    def test_no_model_text_is_shown_when_explain_fails(self):
        llm = ScriptedLlm(understood(move_fee_usd=500.0), LlmError("down"))
        out = answer("q", "peak", llm)
        assert out["status"] == "answered" and out["answer_source"] == "engine_only"

    def test_a_refused_explanation_still_shows_the_result(self):
        llm = ScriptedLlm(understood(move_fee_usd=500.0), LlmRefused("no"))
        assert answer("q", "peak", llm)["answer_source"] == "engine_only"

    def test_a_non_string_answer_counts_as_bad(self):
        llm = ScriptedLlm(understood(), {"answer": 5}, {"answer": ["x"]})
        assert answer("q", "peak", llm)["answer_source"] == "engine_only"

    def test_off_topic_questions_get_fixed_text_and_skip_the_engine(self, monkeypatch):
        monkeypatch.setattr(whatif, "run", lambda *a, **k: pytest.fail("engine must not run"))
        llm = ScriptedLlm(understood(in_scope=False))
        assert answer("Write me a poem", "peak", llm) == {"status": "out_of_scope", "message": OUT_OF_SCOPE}
        assert len(llm.calls) == 1

    def test_a_refusal_gets_the_same_fixed_text(self):
        out = answer("q", "peak", ScriptedLlm(LlmRefused("no")))
        assert out == {"status": "out_of_scope", "message": OUT_OF_SCOPE}

    def test_out_of_range_never_reaches_the_engine(self, monkeypatch):
        monkeypatch.setattr(whatif, "run", lambda *a, **k: pytest.fail("engine must not run"))
        out = answer("q", "peak", ScriptedLlm(understood(demand_charge_usd_per_kw=1e9)))
        assert out["status"] == "invalid_input" and "between" in out["message"]

    def test_an_unknown_input_from_the_model_never_reaches_the_engine(self, monkeypatch):
        monkeypatch.setattr(whatif, "run", lambda *a, **k: pytest.fail("engine must not run"))
        reply = understood()
        reply["changes"]["PUE"] = 1.0
        assert answer("q", "peak", ScriptedLlm(reply))["status"] == "invalid_input"

    def test_a_missing_changes_object_is_a_model_failure(self):
        with pytest.raises(LlmError):
            answer("q", "peak", ScriptedLlm({"in_scope": True, "scenario": None}))

    def test_an_infeasible_plan_is_reported_not_hidden(self):
        out = answer("q", "peak", ScriptedLlm(understood(battery_power_kw=500.0)))
        assert out["status"] == "engine_refused"
        assert "demand-response" in out["message"]
        assert out["inputs"][0]["whatif"] == "500 kW"

    def test_scenario_comes_from_the_question_then_the_page(self):
        assert answer("q", "peak", ScriptedLlm(understood(scenario="sun"), GOOD))["scenario"] == "sun"
        assert answer("q", "sun", ScriptedLlm(understood(scenario=None), GOOD))["scenario"] == "sun"
        assert answer("q", "peak", ScriptedLlm(understood(scenario="moon"), GOOD))["scenario"] == "peak"

    def test_no_rewrite_when_time_is_nearly_up(self):
        ticks = iter([0.0, 1.0, 45.0, 45.0, 45.0, 45.0])
        llm = ScriptedLlm(understood(), {"answer": "Saves $5."}, GOOD)
        out = answer("q", "peak", llm, clock=lambda: next(ticks))
        assert out["answer_source"] == "engine_only"
        assert len(llm.calls) == 2

    def test_explain_prompt_lists_only_engine_facts_and_the_hard_rule(self):
        llm = ScriptedLlm(understood(demand_charge_usd_per_kw=36.0), GOOD)
        answer("q", "peak", llm)
        system = llm.calls[1]["system"]
        assert "$36.00/kW" in system and "never write a number" in system

    def test_understand_schema_has_every_knob_and_is_closed(self):
        schema = understand_schema()
        assert schema["additionalProperties"] is False
        changes = schema["properties"]["changes"]
        assert changes["additionalProperties"] is False
        assert set(changes["required"]) == {k.key for k in whatif.KNOBS} | {whatif.MOVE_SWITCH}

    def test_understand_schema_has_no_null_enum(self):
        # Bedrock rejects an enum that doesn't match its declared type, seen live:
        # "Enum value 'peak' does not match declared type '['string', 'null']'".
        scenario = understand_schema()["properties"]["scenario"]
        assert scenario["type"] == "string"
        assert all(isinstance(v, str) for v in scenario["enum"])


def test_the_page_and_the_handler_agree_on_question_length():
    from pathlib import Path

    from sim import config

    source = (Path(__file__).resolve().parents[2] / "web" / "src" / "config.ts").read_text("utf-8")
    assert f"export const WHATIF_MAX_CHARS = {config.ANALYST_QUESTION_MAX_CHARS};" in source


class FakeDynamo:
    def __init__(self):
        self.counts = {}

    def update_item(self, **kw):
        key = kw["Key"]["pk"]["S"]
        if self.counts.get(key, 0) >= int(kw["ExpressionAttributeValues"][":limit"]["N"]):
            err = RuntimeError("cond")
            err.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
            raise err
        self.counts[key] = self.counts.get(key, 0) + 1


class TestLimits:
    def limits(self, daily=3, hourly=2, now=1_000_000.0):
        return Limits(client=FakeDynamo(), table="t", daily_cap=daily, hourly_per_visitor=hourly, clock=lambda: now)

    def test_stops_a_visitor_at_the_hourly_limit(self):
        limits = self.limits()
        assert [limits.take("a") for _ in range(3)] == [None, None, "visitor"]

    def test_stops_everyone_at_the_daily_cap(self):
        limits = self.limits(daily=2, hourly=5)
        assert limits.take("a") is None
        assert limits.take("b") is None
        assert limits.take("c") == "daily"

    def test_a_limited_visitor_does_not_use_the_daily_allowance(self):
        limits = self.limits(daily=3, hourly=1)
        limits.take("a")
        for _ in range(5):
            assert limits.take("a") == "visitor"
        assert limits.take("b") is None

    def test_the_hour_rolls_over_for_visitors_but_not_the_day(self):
        now = [86400.0 * 100]
        limits = Limits(client=FakeDynamo(), table="t", daily_cap=3, hourly_per_visitor=1, clock=lambda: now[0])
        assert limits.take("a") is None and limits.take("a") == "visitor"
        now[0] += 3600
        assert limits.take("a") is None

    def test_other_errors_propagate(self):
        class Broken:
            def update_item(self, **_):
                raise ConnectionError("down")

        limits = Limits(client=Broken(), table="t", daily_cap=1, hourly_per_visitor=1)
        with pytest.raises(ConnectionError):
            limits.take("a")

    def test_visitor_id_hides_the_address_and_ignores_the_port(self):
        first = visitor_id("203.0.113.9:1234", "salt")
        assert first == visitor_id("203.0.113.9:9999", "salt")
        assert "203" not in first
        assert first != visitor_id("203.0.113.9:1234", "other")
        assert first != visitor_id("203.0.113.10:1234", "salt")


def post(body, **extra) -> dict:
    return {"requestContext": {"http": {"method": "POST"}},
            "headers": {"CloudFront-Viewer-Address": "203.0.113.9:443"},
            "body": json.dumps(body) if not isinstance(body, str) else body, **extra}


class TestHandler:
    @pytest.fixture(autouse=True)
    def wired(self, monkeypatch):
        self.llm = ScriptedLlm()
        self.llm.model, self.llm.input_tokens, self.llm.output_tokens = "test", 0, 0
        monkeypatch.setattr(lambda_handler.llm_module, "from_environment", lambda: self.llm)
        monkeypatch.setattr(lambda_handler, "_limits",
                            Limits(client=FakeDynamo(), table="t", daily_cap=2, hourly_per_visitor=2))

    def send(self, event):
        reply = lambda_handler.handler(event, None)
        return reply["statusCode"], json.loads(reply["body"]), reply

    def test_answers_a_question(self):
        self.llm.replies = [understood(move_fee_usd=500.0), GOOD]
        status, body, reply = self.send(post({"question": "q", "scenario": "peak"}))
        assert status == 200 and body["status"] == "answered"
        assert reply["headers"]["cache-control"] == "no-store"

    def test_reads_a_base64_body(self):
        import base64

        self.llm.replies = [understood(in_scope=False)]
        raw = base64.b64encode(json.dumps({"question": "q", "scenario": "peak"}).encode()).decode()
        status, body, _ = self.send(post(raw, isBase64Encoded=True))
        assert status == 200 and body["status"] == "out_of_scope"

    @pytest.mark.parametrize("body", [
        "not json", "[1]", {"scenario": "peak"}, {"question": "", "scenario": "peak"},
        {"question": "   ", "scenario": "peak"}, {"question": 5, "scenario": "peak"},
        {"question": "q", "scenario": "moon"}, {"question": "x" * 401, "scenario": "peak"},
    ])
    def test_bad_requests_cost_nothing(self, body):
        status, reply_body, _ = self.send(post(body))
        assert status == 400 and reply_body["status"] == "error"
        assert self.llm.calls == []
        assert lambda_handler._limits.client.counts == {}

    def test_only_post(self):
        status, _, _ = self.send({"requestContext": {"http": {"method": "GET"}}})
        assert status == 405

    def test_the_cap_stops_the_model_call(self):
        self.llm.replies = [understood(in_scope=False), understood(in_scope=False)]
        for _ in range(2):
            assert self.send(post({"question": "q", "scenario": "peak"}))[0] == 200
        status, body, _ = self.send(post({"question": "q", "scenario": "peak"}))
        assert status == 429 and body["status"] == "rate_limited"
        assert len(self.llm.calls) == 2

    def test_fails_closed_when_the_counters_are_down(self, monkeypatch):
        class Broken:
            def update_item(self, **_):
                raise ConnectionError("down")

        monkeypatch.setattr(lambda_handler, "_limits",
                            Limits(client=Broken(), table="t", daily_cap=2, hourly_per_visitor=2))
        status, body, _ = self.send(post({"question": "q", "scenario": "peak"}))
        assert status == 503 and body["status"] == "unavailable"
        assert self.llm.calls == []

    def test_a_blocked_model_is_503_with_no_details(self):
        self.llm.replies = [LlmError("Bedrock HTTP 403: PermissionDeniedError secret-detail")]
        status, body, _ = self.send(post({"question": "q", "scenario": "peak"}))
        assert status == 503
        assert "secret-detail" not in json.dumps(body)

    def test_never_returns_a_status_cloudfront_would_rewrite(self):
        self.llm.replies = [LlmError("x"), understood(in_scope=False)]
        statuses = [
            self.send(post({"question": "q", "scenario": "peak"}))[0],
            self.send(post("bad"))[0],
            self.send({"requestContext": {"http": {"method": "PUT"}}})[0],
        ]
        assert not {403, 404} & set(statuses)

    def test_the_question_is_not_logged(self, capsys):
        self.llm.replies = [understood(in_scope=False)]
        self.send(post({"question": "my private question", "scenario": "peak"}))
        assert "my private question" not in capsys.readouterr().out


class TestBedrockLlm:
    class FakeClient:
        def __init__(self, response):
            self.response = response
            self.kwargs = None

        def with_options(self, **_):
            return self

        @property
        def messages(self):
            return self

        def create(self, **kwargs):
            self.kwargs = kwargs
            return self.response

    @staticmethod
    def response(text='{"a": 1}', stop="end_turn"):
        block = type("B", (), {"type": "text", "text": text})()
        usage = type("U", (), {"input_tokens": 10, "output_tokens": 5})()
        return type("R", (), {"content": [block], "stop_reason": stop, "usage": usage})()

    def llm(self, response, effort=None):
        return BedrockLlm(model="m", route="invoke", region="us-west-2", effort=effort,
                          _client=self.FakeClient(response))

    def test_rejects_an_unknown_route(self):
        with pytest.raises(ValueError):
            BedrockLlm(model="m", route="carrier-pigeon", region="r")

    def test_returns_parsed_json_and_counts_tokens(self):
        llm = self.llm(self.response())
        assert llm.structured("s", "u", {"type": "object"}, 100) == {"a": 1}
        assert (llm.input_tokens, llm.output_tokens) == (10, 5)
        sent = llm._client.kwargs
        assert sent["output_config"]["format"]["type"] == "json_schema"
        assert sent["system"] == "s" and sent["max_tokens"] == 100

    def test_effort_is_sent_only_when_set(self):
        # Haiku 4.5 rejects effort, so an unset value must not reach the API.
        unset = self.llm(self.response())
        unset.structured("s", "u", {}, 100)
        assert "effort" not in unset._client.kwargs["output_config"]
        high = self.llm(self.response(), effort="high")
        high.structured("s", "u", {}, 100)
        assert high._client.kwargs["output_config"]["effort"] == "high"

    def test_a_refusal_is_not_read_as_an_answer(self):
        with pytest.raises(LlmRefused):
            self.llm(self.response(stop="refusal")).structured("s", "u", {}, 100)

    def test_a_cut_off_reply_is_an_error(self):
        with pytest.raises(LlmError, match="tokens"):
            self.llm(self.response(stop="max_tokens")).structured("s", "u", {}, 100)

    @pytest.mark.parametrize("text", ["not json", "[1, 2]"])
    def test_unusable_text_is_an_error(self, text):
        with pytest.raises(LlmError):
            self.llm(self.response(text=text)).structured("s", "u", {}, 100)

    def test_api_failures_become_llm_errors_without_details(self):
        import anthropic
        import httpx2

        class Failing(self.FakeClient):
            def create(self, **kwargs):
                request = httpx2.Request("POST", "https://example.test")
                raise anthropic.PermissionDeniedError(
                    "not available", response=httpx2.Response(403, request=request), body=None)

        llm = BedrockLlm(model="m", route="invoke", region="r", _client=Failing(None))
        with pytest.raises(LlmError, match="HTTP 403") as caught:
            llm.structured("s", "u", {}, 100)
        assert "not available" not in str(caught.value)
