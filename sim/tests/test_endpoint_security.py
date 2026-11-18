"""The public endpoint's defences: sizes, identity, limits under load, switches, model-call bounds.

No network and no AWS: DynamoDB and the model are fakes. The concurrency tests check
``Limits`` against a fake that applies DynamoDB's conditional update atomically; they
show the code relies only on that atomicity, not that DynamoDB provides it.
"""

from __future__ import annotations

import base64
import json
import re
import threading
from pathlib import Path

import pytest

from analyst import handler as lambda_handler
from analyst import notice
from analyst.limits import UNIDENTIFIED, Limits, viewer_ip, visitor_id
from analyst.llm import CallBudget, GeminiLlm, LlmError, client_options
from sim import config
from sim.tests.test_analyst import GOOD, ScriptedLlm, understood

ROOT = Path(__file__).resolve().parents[2]
VIEWER = "203.0.113.9:443"


class AtomicDynamo:
    """Conditional ADD under a lock, as DynamoDB applies it. Thread-safe."""

    def __init__(self, fail_on: str | None = None, fail_get: bool = False):
        self.counts: dict[str, int] = {}
        self.off = False
        self.lock = threading.Lock()
        self.fail_on, self.fail_get = fail_on, fail_get

    def get_item(self, **kw):
        if self.fail_get:
            raise ConnectionError("down")
        return {"Item": {"off": {"BOOL": True}}} if self.off else {}

    def update_item(self, **kw):
        key = kw["Key"]["pk"]["S"]
        if self.fail_on and key.startswith(self.fail_on):
            raise ConnectionError("down")
        limit = int(kw["ExpressionAttributeValues"][":limit"]["N"])
        with self.lock:
            if self.counts.get(key, 0) >= limit:
                err = RuntimeError("cond")
                err.response = {"Error": {"Code": "ConditionalCheckFailedException"}}
                raise err
            self.counts[key] = self.counts.get(key, 0) + 1


def event(body, path="/api/whatif", headers=None, **extra):
    raw = body if isinstance(body, str) else json.dumps(body)
    return {"requestContext": {"http": {"method": "POST"}}, "rawPath": path,
            "headers": {"cloudfront-viewer-address": VIEWER} if headers is None else headers,
            "body": raw, **extra}


QUESTION = {"question": "What if the demand charge doubled?", "scenario": "peak"}


@pytest.fixture
def wired(monkeypatch):
    dynamo = AtomicDynamo()
    monkeypatch.setattr(lambda_handler, "_limits", Limits(client=dynamo, table="t", daily_cap=50, hourly_per_visitor=10))
    llm = ScriptedLlm()
    llm.model, llm.input_tokens, llm.output_tokens = "test", 0, 0
    monkeypatch.setattr(lambda_handler.llm_module, "from_environment", lambda: llm)
    monkeypatch.delenv("ANALYST_ENABLED", raising=False)
    lambda_handler._briefings.clear()
    yield dynamo, llm
    lambda_handler._briefings.clear()


def send(evt):
    reply = lambda_handler.handler(evt, None)
    assert reply["headers"]["content-type"] == "application/json"
    return reply["statusCode"], json.loads(reply["body"])


# --- Oversized and malformed requests: refused before parsing, the counters or the model.

class TestRequestShape:
    @pytest.mark.parametrize("path", ["/api/whatif", "/api/briefing"])
    def test_an_oversized_body_is_413_and_costs_nothing(self, wired, path):
        dynamo, llm = wired
        big = json.dumps({"question": "q", "scenario": "peak", "pad": "x" * config.ANALYST_MAX_BODY_BYTES})
        status, body = send(event(big, path))
        assert status == 413 and body["status"] == "error"
        assert dynamo.counts == {} and llm.calls == []

    def test_an_oversized_base64_body_is_refused_before_decoding(self, wired, monkeypatch):
        dynamo, llm = wired
        decoded = []
        monkeypatch.setattr(lambda_handler.base64, "b64decode", lambda *a, **k: decoded.append(1) or b"{}")
        raw = base64.b64encode(b"{" + b" " * (config.ANALYST_MAX_BODY_BYTES + 10) + b"}").decode()
        status, _ = send(event(raw, isBase64Encoded=True))
        assert status == 413 and decoded == [] and llm.calls == []

    def test_multibyte_text_is_measured_in_bytes(self, wired):
        # Under the limit in characters, over it in UTF-8 bytes.
        chars = config.ANALYST_MAX_BODY_BYTES // 2
        status, _ = send(event('{"question": "' + "é" * chars + '", "scenario": "peak"}'))
        assert status == 413

    def test_a_body_at_the_largest_real_question_is_accepted(self, wired):
        _, llm = wired
        llm.replies = [understood(in_scope=False)]
        worst = {"question": "é" * config.ANALYST_QUESTION_MAX_CHARS, "scenario": "peak"}
        assert len(json.dumps(worst, ensure_ascii=False).encode()) < config.ANALYST_MAX_BODY_BYTES
        status, _ = send(event(json.dumps(worst, ensure_ascii=False)))
        assert status == 200

    @pytest.mark.parametrize("raw,b64", [
        ("[" * 2000 + "]" * 2000, False),            # Deep nesting under the size limit: no RecursionError escapes.
        ("not base64!!", True),
        (base64.b64encode(b"\xff\xfe\x00").decode(), True),  # Not UTF-8.
        ('{"question": "q", "scenario": ["peak"]}', False),   # Unhashable scenario.
        ('{"question": ["q"], "scenario": "peak"}', False),
        ("null", False),
    ])
    def test_malformed_bodies_are_400(self, wired, raw, b64):
        dynamo, llm = wired
        status, body = send(event(raw, isBase64Encoded=b64))
        assert status == 400 and body["status"] == "error"
        assert dynamo.counts == {} and llm.calls == []

    def test_a_non_string_body_is_400(self, wired):
        status, _ = send({**event("{}"), "body": {"question": "q"}})
        assert status == 400

    @pytest.mark.parametrize("path", ["/api/other", "/api/whatif/../briefing", "/", "", "/api/briefingx"])
    def test_unknown_paths_are_400_not_404(self, wired, path):
        dynamo, llm = wired
        status, _ = send(event(QUESTION, path))
        assert status == 400
        assert dynamo.counts == {} and llm.calls == []

    def test_an_unexpected_failure_is_a_503_with_no_details(self, wired, monkeypatch, capsys):
        _, llm = wired

        def boom(*_a, **_k):
            raise ValueError("secret internal detail")

        monkeypatch.setattr(lambda_handler.workflow, "answer", boom)
        status, body = send(event(QUESTION))
        assert status == 503 and "secret" not in json.dumps(body)
        logged = capsys.readouterr().out
        assert "ValueError" in logged and "secret internal detail" not in logged


# --- Visitor identity comes only from CloudFront's header, parsed strictly.

class TestVisitorIdentity:
    @pytest.mark.parametrize("header", [
        None, "", "203.0.113.9", "203.0.113.9:", "203.0.113.9:0", "203.0.113.9:70000",
        "not-an-ip:443", "203.0.113.9:443, 198.51.100.7:443", "198.51.100.7:443,203.0.113.9:443",
        "203.0.113.9 :443", "203.0.113.999:443",
    ])
    def test_malformed_or_repeated_headers_are_unidentified(self, header):
        assert viewer_ip(header) is None
        assert visitor_id(header, "s") == visitor_id(None, "s")

    @pytest.mark.parametrize("header", ["203.0.113.9:443", "2001:db8::1:443", "[2001:db8::1]:443"])
    def test_well_formed_headers_are_identified(self, header):
        assert viewer_ip(header) is not None

    def test_an_ipv6_visitor_is_its_slash_64(self):
        assert visitor_id("2001:db8:0:1::1:443", "s") == visitor_id("2001:db8:0:1:ffff::9:80", "s")
        assert visitor_id("2001:db8:0:1::1:443", "s") != visitor_id("2001:db8:0:2::1:443", "s")

    def test_unidentified_is_one_bucket_not_a_bypass(self):
        assert visitor_id("garbage-1", "s") == visitor_id("garbage-2", "s")
        assert UNIDENTIFIED not in visitor_id(None, "s")

    def test_forged_headers_share_one_hourly_allowance(self, wired):
        dynamo, llm = wired
        llm.replies = [understood(in_scope=False)] * 20
        statuses = []
        for i in range(15):
            forged = f"198.51.100.{i}:443, 203.0.113.9:443"  # A viewer copy joined with CloudFront's.
            statuses.append(send(event(QUESTION, headers={"cloudfront-viewer-address": forged}))[0])
        assert statuses.count(200) == 10 and statuses.count(429) == 5

    def test_other_client_headers_do_not_change_the_visitor(self, wired):
        _, llm = wired
        llm.replies = [understood(in_scope=False)] * 20
        statuses = []
        for i in range(12):
            headers = {"cloudfront-viewer-address": VIEWER, "x-forwarded-for": f"198.51.100.{i}",
                       "x-real-ip": f"198.51.100.{i}", "true-client-ip": f"198.51.100.{i}"}
            statuses.append(send(event(QUESTION, headers=headers))[0])
        assert statuses.count(200) == 10 and statuses.count(429) == 2

    def test_an_unidentified_request_is_logged_without_its_address(self, wired, capsys):
        _, llm = wired
        llm.replies = [understood(in_scope=False)]
        send(event(QUESTION, headers={"cloudfront-viewer-address": "198.51.100.77:1, 203.0.113.9:2"}))
        out = capsys.readouterr().out
        assert "unidentified_visitor" in out and "198.51.100.77" not in out


# --- Limits under concurrent requests.

class TestConcurrency:
    def run_threads(self, n, fn):
        results, barrier = [None] * n, threading.Barrier(n)

        def worker(i):
            barrier.wait()
            results[i] = fn(i)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def test_the_daily_cap_holds_under_a_burst(self):
        dynamo = AtomicDynamo()
        limits = Limits(client=dynamo, table="t", daily_cap=20, hourly_per_visitor=5, clock=lambda: 1e6)
        results = self.run_threads(64, lambda i: limits.take(f"v{i}"))
        assert results.count(None) == 20
        assert sum(v for k, v in dynamo.counts.items() if k.startswith("day#")) == 20

    def test_one_visitor_cannot_race_past_the_hourly_limit(self):
        limits = Limits(client=AtomicDynamo(), table="t", daily_cap=1000, hourly_per_visitor=10, clock=lambda: 1e6)
        results = self.run_threads(40, lambda i: limits.take("same"))
        assert results.count(None) == 10 and results.count("visitor") == 30

    def test_concurrent_handler_requests_never_call_the_model_past_the_cap(self, monkeypatch):
        dynamo = AtomicDynamo()
        monkeypatch.setattr(lambda_handler, "_limits",
                            Limits(client=dynamo, table="t", daily_cap=7, hourly_per_visitor=100))
        calls, lock = [], threading.Lock()

        class CountingLlm:
            model, input_tokens, output_tokens = "test", 0, 0

            def structured(self, *a, **k):
                with lock:
                    calls.append(1)
                return understood(in_scope=False)

        monkeypatch.setattr(lambda_handler.llm_module, "from_environment", CountingLlm)
        results = self.run_threads(30, lambda i: lambda_handler.handler(
            event(QUESTION, headers={"cloudfront-viewer-address": f"198.51.100.{i}:443"}), None)["statusCode"])
        assert results.count(200) == 7 and results.count(429) == 23
        assert len(calls) == 7


# --- Counter failures fail closed.

class TestCounterFailures:
    @pytest.mark.parametrize("dynamo", [
        AtomicDynamo(fail_get=True), AtomicDynamo(fail_on="visitor#"), AtomicDynamo(fail_on="day#"),
    ])
    def test_any_counter_failure_refuses_before_the_model(self, wired, monkeypatch, dynamo):
        _, llm = wired
        monkeypatch.setattr(lambda_handler, "_limits", Limits(client=dynamo, table="t", daily_cap=5, hourly_per_visitor=5))
        status, body = send(event(QUESTION))
        assert status == 503 and body["status"] == "unavailable"
        assert llm.calls == []

    def test_a_missing_table_setting_fails_closed(self, monkeypatch, wired):
        _, llm = wired
        monkeypatch.setattr(lambda_handler, "_limits", None)
        monkeypatch.delenv("ANALYST_TABLE", raising=False)
        status, _ = send(event(QUESTION))
        assert status == 503 and llm.calls == []

    @pytest.mark.parametrize("daily,hourly", [(0, 10), (10, 0), (-1, 10)])
    def test_a_zero_limit_refuses_everything(self, daily, hourly):
        dynamo = AtomicDynamo()
        assert Limits(client=dynamo, table="t", daily_cap=daily, hourly_per_visitor=hourly).take("v") is not None
        assert dynamo.counts == {}


# --- Emergency switches.

class TestSwitches:
    @pytest.mark.parametrize("path,body", [("/api/whatif", QUESTION), ("/api/briefing", {"scenario": "peak"})])
    def test_the_deploy_switch_stops_everything_before_any_cost(self, wired, monkeypatch, path, body):
        dynamo, llm = wired
        monkeypatch.setenv("ANALYST_ENABLED", "false")
        status, reply = send(event(body, path))
        assert status == 503 and "switched off" in reply["message"]
        assert dynamo.counts == {} and llm.calls == []

    def test_the_deploy_switch_also_hides_a_cached_briefing(self, wired, monkeypatch):
        monkeypatch.setenv("ANALYST_ENABLED", "false")
        lambda_handler._briefings["peak"] = {"status": "briefed"}
        assert send(event({"scenario": "peak"}, "/api/briefing"))[0] == 503

    @pytest.mark.parametrize("path,body", [("/api/whatif", QUESTION), ("/api/briefing", {"scenario": "peak"})])
    def test_the_operator_switch_stops_new_model_work(self, wired, path, body):
        dynamo, llm = wired
        dynamo.off = True
        status, reply = send(event(body, path))
        assert status == 503 and "switched off" in reply["message"]
        assert dynamo.counts == {} and llm.calls == []

    def test_switching_back_on_resumes(self, wired):
        dynamo, llm = wired
        dynamo.off = True
        assert send(event(QUESTION))[0] == 503
        dynamo.off = False
        llm.replies = [understood(in_scope=False)]
        assert send(event(QUESTION))[0] == 200

    def test_the_static_site_does_not_depend_on_the_switch(self):
        # The switch lives in the Lambda; the site is S3 behind CloudFront's default behavior.
        stack = (ROOT / "infra" / "lib" / "site-stack.ts").read_text("utf-8")
        assert "origins.S3BucketOrigin.withOriginAccessControl(bucket)" in stack


# --- Model calls and retries are bounded.

class TestModelBounds:
    def test_the_budget_refuses_past_its_limit(self):
        inner = ScriptedLlm(*[{"x": 1}] * 10)
        budget = CallBudget(inner, max_calls=3)
        for _ in range(3):
            budget.structured("s", "u", {}, 10)
        with pytest.raises(LlmError):
            budget.structured("s", "u", {}, 10)
        assert len(inner.calls) == 3

    def test_a_what_if_with_bad_drafts_makes_at_most_three_calls(self, wired):
        _, llm = wired
        llm.replies = [understood(move_fee_usd=500.0)] + [{"answer": "It saves 5 dollars."}] * 10
        status, body = send(event(QUESTION))
        assert status == 200 and body["answer_source"] == "engine_only"
        assert len(llm.calls) <= config.ANALYST_MAX_MODEL_CALLS

    def test_a_briefing_with_bad_drafts_makes_at_most_three_calls(self, wired):
        _, llm = wired
        llm.replies = [{"headline": "Saves 5 dollars.", "points": []}] * 10
        status, body = send(event({"scenario": "sun"}, "/api/briefing"))
        assert status == 200 and body["source"] == "engine_only"
        assert len(llm.calls) <= config.ANALYST_MAX_MODEL_CALLS

    def test_the_budget_stops_a_workflow_that_would_loop(self, wired, monkeypatch):
        _, llm = wired
        llm.replies = [GOOD] * 100

        def runaway(question, scenario, model):
            while True:
                model.structured("s", "u", {}, 10)

        monkeypatch.setattr(lambda_handler.workflow, "answer", runaway)
        status, _ = send(event(QUESTION))
        assert status == 503
        assert len(llm.calls) == config.ANALYST_MAX_MODEL_CALLS

    def test_the_sdk_makes_one_attempt_per_call(self):
        options = client_options()
        assert options.retry_options.attempts == 1
        from google.genai._gaos.google_genai import _translate_retry_config

        assert _translate_retry_config(options).max_retries == 0

    def test_the_client_is_built_with_retries_off(self, monkeypatch):
        from google import genai

        seen = {}

        class FakeClient:
            def __init__(self, **kw):
                seen.update(kw)

        monkeypatch.setattr(genai, "Client", FakeClient)
        GeminiLlm(model="m", api_key="k")._get_client()
        assert seen["http_options"].retry_options.attempts == 1

    def test_output_and_time_are_bounded_per_call(self):
        assert 0 < config.ANALYST_MAX_TOKENS <= 8000
        assert config.ANALYST_CALL_TIMEOUT_S < config.ANALYST_TIME_BUDGET_S < 58
        # The first call waits longest, but still leaves the explanation room to try.
        assert config.ANALYST_CALL_TIMEOUT_S <= config.ANALYST_UNDERSTAND_TIMEOUT_S
        assert config.ANALYST_TIME_BUDGET_S - config.ANALYST_UNDERSTAND_TIMEOUT_S >= 10
        lam = (ROOT / "infra" / "lib" / "analyst.ts").read_text("utf-8")
        assert "timeout: cdk.Duration.seconds(58)" in lam


# --- Every reply says what it is, and the page says the same.

class TestNotice:
    def test_a_what_if_reply_carries_the_notice_and_provenance(self, wired):
        _, llm = wired
        llm.replies = [understood(move_fee_usd=500.0), GOOD]
        _, body = send(event(QUESTION))
        assert body["notice"] == notice.NOTICE
        prov = body["provenance"]
        assert {"historical_grid_data", "simulated", "illustrative", "outcome", "build", "model"} <= set(prov)
        assert prov["model"] == "test" and config.DATA_START[:10] in prov["historical_grid_data"]

    def test_a_briefing_reply_carries_the_notice(self, wired):
        _, llm = wired
        llm.replies = [LlmError("down")]
        _, body = send(event({"scenario": "peak"}, "/api/briefing"))
        assert body["notice"] == notice.NOTICE and "provenance" in body

    def test_the_page_shows_the_same_notice(self):
        source = (ROOT / "web" / "src" / "config.ts").read_text("utf-8")
        # Slice out the declaration with plain string search, then join its quoted parts.
        # (No nested regex quantifiers: CodeQL flags those as exponential backtracking.)
        head = "export const RESPONSIBLE_USE ="
        start = source.find(head)
        assert start >= 0, "RESPONSIBLE_USE not found in web/src/config.ts"
        declaration = source[start + len(head):source.index(";", start)]
        assert "".join(re.findall(r"'([^']*)'", declaration)) == notice.NOTICE

    def test_the_build_id_is_unknown_outside_a_bundle(self):
        assert notice.build_id() == "unknown" or len(notice.build_id()) >= 7


# --- The edge's body limit matches the handler's.

def test_the_waf_body_limit_matches_the_handler():
    edge = (ROOT / "infra" / "lib" / "edge-waf.ts").read_text("utf-8")
    assert f"DEFAULT_MAX_BODY_BYTES = {config.ANALYST_MAX_BODY_BYTES};" in edge
