"""Lambda entry point for ``POST /api/whatif``, behind CloudFront.

Never returns 403 or 404. The distribution rewrites those to ``index.html`` with
status 200 for the single-page site, so they would reach the browser as HTML. The
web client treats a non-JSON reply as "not connected" for exactly this reason.

Logs carry status, timing and token counts. Questions are not logged.
"""

from __future__ import annotations

import base64
import json
import os
import time
from typing import Any

from analyst import llm as llm_module
from analyst import workflow
from analyst.limits import Limits, visitor_id
from analyst.llm import LlmError
from sim import config, whatif

_JSON_HEADERS = {"content-type": "application/json", "cache-control": "no-store"}
_UNAVAILABLE = "The analyst isn't available right now. Everything else on this page still works."
_DAILY = "The analyst has reached its daily limit. Everything else on this page still works."
_VISITOR = "You've reached the hourly limit for questions. Try again later."

_limits: Limits | None = None


def _reply(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status, "headers": _JSON_HEADERS, "body": json.dumps(body, ensure_ascii=False)}


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


def _parse(event: dict[str, Any]) -> tuple[str, str] | str:
    """The question and scenario, or a message saying what's wrong."""
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode("utf-8")
        except ValueError:
            return "The request body isn't valid."
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return "The request body isn't valid JSON."
    if not isinstance(body, dict):
        return "The request body must be a JSON object."
    question = body.get("question")
    scenario = body.get("scenario")
    if not isinstance(question, str) or not question.strip():
        return "Type a question first."
    if len(question) > config.ANALYST_QUESTION_MAX_CHARS:
        return f"Keep the question under {config.ANALYST_QUESTION_MAX_CHARS} characters."
    if scenario not in whatif.SCENARIOS:
        return "Unknown scenario."
    return question.strip(), scenario


def _log(**fields: Any) -> None:
    print(json.dumps({"event": "whatif", **fields}))


def handler(event: dict[str, Any], _context: Any) -> dict[str, Any]:
    started = time.monotonic()
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    if method != "POST":
        return _reply(405, {"status": "error", "message": "Use POST."})

    parsed = _parse(event)
    if isinstance(parsed, str):
        return _reply(400, {"status": "error", "message": parsed})
    question, scenario = parsed

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    visitor = visitor_id(headers.get("cloudfront-viewer-address", ""), os.environ.get("ANALYST_SALT", ""))
    try:
        stopped_by = _get_limits().take(visitor)
    except Exception as e:  # noqa: BLE001 - no counters means no cap, so refuse.
        _log(status="limits_failed", error=type(e).__name__)
        return _reply(503, {"status": "unavailable", "message": _UNAVAILABLE})
    if stopped_by is not None:
        _log(status="rate_limited", limit=stopped_by)
        return _reply(429, {"status": "rate_limited", "message": _DAILY if stopped_by == "daily" else _VISITOR})

    llm = llm_module.from_environment()
    try:
        result = workflow.answer(question, scenario, llm)
    except LlmError as e:
        _log(status="llm_failed", error=str(e), seconds=round(time.monotonic() - started, 2))
        return _reply(503, {"status": "unavailable", "message": _UNAVAILABLE})
    _log(status=result["status"], source=result.get("answer_source"), seconds=round(time.monotonic() - started, 2),
         model=llm.model, input_tokens=llm.input_tokens, output_tokens=llm.output_tokens)
    return _reply(200, result)
