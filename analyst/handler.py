"""Lambda entry point for ``POST /api/whatif`` and ``POST /api/briefing``, behind CloudFront.

Never returns 403 or 404. The distribution rewrites those to ``index.html`` with
status 200 for the single-page site, so they would reach the browser as HTML. The
web client treats a non-JSON reply as "not connected" for exactly this reason.

Order of checks, cheapest first, so a refused request costs as little as possible:
route and method, the deploy-time switch, body size, JSON shape, then (only for
work that calls the model) the operator's switch and the counters, and last the
model itself, capped at ``ANALYST_MAX_MODEL_CALLS`` calls.

Logs carry status, timing and token counts. Questions, IPs, keys and exception
messages are not logged; errors are logged by type.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import time
from typing import Any

from analyst import briefing, notice, workflow
from analyst import llm as llm_module
from analyst.limits import Limits, viewer_ip, visitor_id
from analyst.llm import CallBudget, LlmError
from sim import config, whatif

_JSON_HEADERS = {"content-type": "application/json", "cache-control": "no-store"}
_UNAVAILABLE = "The analyst isn't available right now. Everything else on this page still works."
_SWITCHED_OFF = "The analyst is switched off for now. Everything else on this page still works."
_DAILY = "The analyst has reached its daily limit. Everything else on this page still works."
_VISITOR = "You've reached the hourly limit for questions. Try again later."
_ROUTES = {"/api/whatif": "whatif", "/api/briefing": "briefing"}

_limits: Limits | None = None
# Verified briefings by scenario. Their inputs are fixed per deploy, so a repeat costs nothing.
_briefings: dict[str, dict[str, Any]] = {}


class _Refused(Exception):
    """A request refused before any paid work, with the status and message to send."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _reply(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status, "headers": _JSON_HEADERS, "body": json.dumps(body, ensure_ascii=False)}


def _error(status: int, message: str) -> dict[str, Any]:
    kind = {413: "error", 429: "rate_limited", 503: "unavailable"}.get(status, "error")
    return _reply(status, {"status": kind, "message": message})


def _enabled() -> bool:
    """The deploy-time switch, ``-c headroom:analystEnabled=false``. Anything but "false" is on."""
    return os.environ.get("ANALYST_ENABLED", "true").strip().lower() != "false"


def _get_limits() -> Limits:
    global _limits
    if _limits is None:
        import boto3

        _limits = Limits(
            client=boto3.client("dynamodb"),
            table=os.environ["ANALYST_TABLE"],
            daily_cap=int(os.environ.get("ANALYST_DAILY_CAP", config.ANALYST_DAILY_QUESTION_CAP)),
            hourly_per_visitor=int(os.environ.get("ANALYST_HOURLY_PER_VISITOR", config.ANALYST_HOURLY_PER_VISITOR)),
        )
    return _limits


def _body(event: dict[str, Any]) -> dict[str, Any]:
    """The JSON body. Raises ``_Refused`` for one that is too large or malformed.

    Size is checked on the raw text, before base64 decoding or JSON parsing.
    """
    limit = config.ANALYST_MAX_BODY_BYTES
    raw = event.get("body")
    if raw is None:
        raw = ""
    if not isinstance(raw, str):
        raise _Refused(400, "The request body isn't valid.")
    if event.get("isBase64Encoded"):
        # Base64 is 4 characters per 3 bytes; anything longer can't decode under the limit.
        if len(raw) > 4 * ((limit + 2) // 3):
            raise _Refused(413, "The request is too large.")
        try:
            data = base64.b64decode(raw, validate=True)
        except (binascii.Error, ValueError):
            raise _Refused(400, "The request body isn't valid.") from None
    else:
        if len(raw) > limit:  # Characters; each is at least one byte.
            raise _Refused(413, "The request is too large.")
        data = raw.encode("utf-8", "surrogatepass")
    if len(data) > limit:
        raise _Refused(413, "The request is too large.")
    try:
        body = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise _Refused(400, "The request body isn't valid JSON.") from None
    if not isinstance(body, dict):
        raise _Refused(400, "The request body must be a JSON object.")
    return body


def _scenario(body: dict[str, Any]) -> str:
    scenario = body.get("scenario")
    if not isinstance(scenario, str) or scenario not in whatif.SCENARIOS:
        raise _Refused(400, "Unknown scenario.")
    return scenario


def _parse(event: dict[str, Any]) -> tuple[str, str]:
    """The question and scenario. Raises ``_Refused`` saying what's wrong."""
    body = _body(event)
    question = body.get("question")
    if not isinstance(question, str) or not question.strip():
        raise _Refused(400, "Type a question first.")
    if len(question) > config.ANALYST_QUESTION_MAX_CHARS:
        raise _Refused(400, f"Keep the question under {config.ANALYST_QUESTION_MAX_CHARS} characters.")
    return question.strip(), _scenario(body)


def _log(**fields: Any) -> None:
    print(json.dumps({"event": "whatif", **fields}))


def _take_limit(event: dict[str, Any], kind: str) -> dict[str, Any] | None:
    """Clear the operator's switch and count one request. A reply means it was refused."""
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    address = headers.get("cloudfront-viewer-address")
    visitor = visitor_id(address, os.environ.get("ANALYST_SALT", ""))
    identified = viewer_ip(address) is not None
    try:
        limits = _get_limits()
        if limits.switched_off():
            _log(kind=kind, status="switched_off")
            return _error(503, _SWITCHED_OFF)
        stopped_by = limits.take(visitor)
    except Exception as e:  # noqa: BLE001 - no counters means no cap, so refuse.
        _log(kind=kind, status="limits_failed", error=type(e).__name__)
        return _error(503, _UNAVAILABLE)
    if stopped_by is not None:
        _log(kind=kind, status="rate_limited", limit=stopped_by, identified=identified)
        return _error(429, _DAILY if stopped_by == "daily" else _VISITOR)
    if not identified:
        # Shares one visitor bucket with every other request like it. Worth seeing in the logs:
        # through CloudFront the header is always set, so this means a misroute or a forgery.
        _log(kind=kind, status="unidentified_visitor")
    return None


def _model() -> tuple[Any, CallBudget]:
    llm = llm_module.from_environment()
    return llm, CallBudget(llm, max_calls=config.ANALYST_MAX_MODEL_CALLS)


def _with_notice(result: dict[str, Any], model: str | None) -> dict[str, Any]:
    return {**result, "notice": notice.NOTICE, "provenance": notice.provenance(model)}


def _briefing(event: dict[str, Any], started: float) -> dict[str, Any]:
    """``POST /api/briefing``. A cached briefing skips the limits: it costs nothing."""
    scenario = _scenario(_body(event))
    if scenario in _briefings:
        _log(kind="briefing", status="cached", seconds=round(time.monotonic() - started, 2))
        return _reply(200, _briefings[scenario])
    refused = _take_limit(event, "briefing")
    if refused is not None:
        return refused
    llm, budget = _model()
    result = _with_notice(briefing.brief(scenario, budget), llm.model)
    if result["source"] == "model_verified":
        _briefings[scenario] = result
    _log(kind="briefing", status=result["status"], source=result["source"],
         seconds=round(time.monotonic() - started, 2), model=llm.model, model_calls=budget.calls,
         input_tokens=llm.input_tokens, output_tokens=llm.output_tokens)
    return _reply(200, result)


def _whatif(event: dict[str, Any], started: float) -> dict[str, Any]:
    question, scenario = _parse(event)
    refused = _take_limit(event, "whatif")
    if refused is not None:
        return refused
    llm, budget = _model()
    try:
        result = workflow.answer(question, scenario, budget)
    except LlmError as e:
        _log(status="llm_failed", error=str(e), seconds=round(time.monotonic() - started, 2),
             model_calls=budget.calls)
        return _error(503, _UNAVAILABLE)
    _log(status=result["status"], source=result.get("answer_source"), seconds=round(time.monotonic() - started, 2),
         model=llm.model, model_calls=budget.calls, input_tokens=llm.input_tokens, output_tokens=llm.output_tokens)
    return _reply(200, _with_notice(result, llm.model))


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    started = time.monotonic()
    try:
        method = event.get("requestContext", {}).get("http", {}).get("method", "")
        if method != "POST":
            return _error(405, "Use POST.")
        route = _ROUTES.get(str(event.get("rawPath", "")).rstrip("/"))
        if route is None:
            return _error(400, "Unknown endpoint.")
        if not _enabled():
            return _error(503, _SWITCHED_OFF)
        return _briefing(event, started) if route == "briefing" else _whatif(event, started)
    except _Refused as r:
        return _error(r.status, r.message)
    except Exception as e:  # noqa: BLE001 - never a stack trace or a 502 to the browser.
        _log(status="failed", error=type(e).__name__, seconds=round(time.monotonic() - started, 2))
        return _error(503, _UNAVAILABLE)
