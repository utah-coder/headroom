"""Run the analyst on your machine, for development: ``python -m analyst.local``.

Serves the same handler on http://127.0.0.1:8787 with an in-memory stand-in for
DynamoDB. ``npm run dev`` proxies ``/api`` here. It calls the real Gemini API with
the key in ``GEMINI_API_KEY``, so each question costs a little.
"""

from __future__ import annotations

import argparse
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from analyst import handler as lambda_handler
from analyst.limits import Limits
from analyst.llm import DEFAULT_MODEL

_PORT = 8787


class _MemoryCounters:
    """Enough of the DynamoDB client for ``Limits``. Development only."""

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    def update_item(self, **kw: Any) -> None:
        key = kw["Key"]["pk"]["S"]
        if self.counts.get(key, 0) >= int(kw["ExpressionAttributeValues"][":limit"]["N"]):
            error = RuntimeError("limit")
            error.response = {"Error": {"Code": "ConditionalCheckFailedException"}}  # type: ignore[attr-defined]
            raise error
        self.counts[key] = self.counts.get(key, 0) + 1


class _Handler(BaseHTTPRequestHandler):
    def _forward(self, method: str) -> None:
        length = int(self.headers.get("content-length", 0))
        event = {
            "requestContext": {"http": {"method": method}},
            "rawPath": self.path,
            "headers": dict(self.headers.items()),
            "body": self.rfile.read(length).decode("utf-8") if length else "",
        }
        result = lambda_handler.handler(event, None)
        body = result["body"].encode("utf-8")
        self.send_response(result["statusCode"])
        for key, value in result["headers"].items():
            self.send_header(key, value)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        self._forward("POST")

    def do_GET(self) -> None:  # noqa: N802
        self._forward("GET")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=os.environ.get("ANALYST_MODEL", DEFAULT_MODEL))
    parser.add_argument("--effort", default=os.environ.get("ANALYST_EFFORT", "low"),
                        help="the model's thinking level; empty for the model's default")
    args = parser.parse_args()
    if not os.environ.get("GEMINI_API_KEY"):
        parser.error("Set GEMINI_API_KEY to a Gemini API key first.")
    os.environ.update(ANALYST_MODEL=args.model, ANALYST_EFFORT=args.effort)
    lambda_handler._limits = Limits(client=_MemoryCounters(), table="local", daily_cap=500, hourly_per_visitor=500)
    print(f"What-if analyst on http://127.0.0.1:{_PORT}/api/whatif, briefing on /api/briefing")
    # Threaded: an idle browser connection would otherwise block every other request.
    ThreadingHTTPServer(("127.0.0.1", _PORT), _Handler).serve_forever()


if __name__ == "__main__":
    main()
