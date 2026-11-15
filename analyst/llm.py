"""Gemini, reduced to the one call the workflow needs.

Calls the Gemini API's Interactions endpoint through Google's ``google-genai`` SDK,
with an API key. The key never goes in the Lambda's configuration: the function
reads it from an SSM SecureString parameter on first use. On your machine, set
``GEMINI_API_KEY`` instead.

The model is a deploy setting. ``scripts/gemini-probe.sh`` finds one the key can call.

Every model call here is exactly one HTTP attempt. The SDK's own retries are off
(by default it makes up to five attempts on 408, 409, 429 and 5xx), because a
retry is another billable request the workflow can't see. The workflows decide
whether to call again, and ``CallBudget`` caps how many calls one request makes.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from sim.config import ANALYST_CALL_TIMEOUT_S

__all__ = ["LlmError", "LlmRefused", "Llm", "GeminiLlm", "CallBudget", "from_environment",
           "client_options", "DEFAULT_MODEL", "DEFAULT_KEY_PARAMETER"]

DEFAULT_MODEL = "gemini-4-hydrogen"
DEFAULT_KEY_PARAMETER = "/headroom/gemini-api-key"
_THINKING_LEVELS = ("minimal", "low", "medium", "high", "xhigh")

_api_key: str | None = None  # Read once per Lambda container.


class LlmError(RuntimeError):
    """The model could not be reached or gave no usable output."""


class LlmRefused(LlmError):
    """The model declined the request."""


class Llm(Protocol):
    def structured(self, system: str, user: str, schema: dict[str, Any], max_tokens: int,
                   timeout_s: float | None = None) -> dict[str, Any]:
        """One model call, constrained to ``schema``. Raises ``LlmError``."""
        ...


@dataclass
class GeminiLlm:
    """``Llm`` over the Gemini Interactions API. Counts tokens for the logs."""

    model: str
    api_key: str | None = field(default=None, repr=False)  # None: read it on first use.
    effort: str | None = None  # Sent as the model's thinking level.
    input_tokens: int = 0
    output_tokens: int = 0
    _client: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.effort and self.effort not in _THINKING_LEVELS:
            raise ValueError(f"unknown thinking level: {self.effort}")

    def _get_client(self) -> Any:
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key or _key(), http_options=client_options())
        return self._client

    def structured(self, system: str, user: str, schema: dict[str, Any], max_tokens: int,
                   timeout_s: float | None = None) -> dict[str, Any]:
        generation_config: dict[str, Any] = {"max_output_tokens": max_tokens}
        if self.effort:
            generation_config["thinking_level"] = self.effort
        client = self._get_client()  # A missing key is an LlmError, like any other failure.
        try:
            interaction = client.interactions.create(
                model=self.model,
                system_instruction=system,
                input=user,
                response_format={"type": "text", "mime_type": "application/json", "schema": schema},
                generation_config=generation_config,
                store=False,  # Google keeps no copy of a visitor's question.
                # A wall-clock limit on the whole call: the workflow owns its time budget.
                timeout=float(timeout_s if timeout_s is not None else ANALYST_CALL_TIMEOUT_S),
            )
        except Exception as e:  # noqa: BLE001 - reported by type and status only, never by message.
            # The SDK raises more than one error family; an HTTP error carries one of these.
            status = next((v for v in (getattr(e, "status_code", None), getattr(e, "code", None))
                           if isinstance(v, int)), None)
            if status is not None:
                raise LlmError(f"Gemini HTTP {status}: {type(e).__name__}") from e
            raise LlmError(f"Gemini call failed: {type(e).__name__}") from e

        usage = interaction.usage
        if usage is not None:
            self.input_tokens += usage.total_input_tokens or 0
            self.output_tokens += (usage.total_output_tokens or 0) + (usage.total_thought_tokens or 0)
        if interaction.status == "incomplete":
            raise LlmError("The model ran out of output tokens.")
        if interaction.status != "completed":
            raise LlmError(f"The model call ended as {interaction.status}.")
        text = interaction.output_text
        if not text:
            raise LlmError("The model returned no text.")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise LlmError("The model returned invalid JSON.") from e
        if not isinstance(parsed, dict):
            raise LlmError("The model returned JSON that isn't an object.")
        return parsed


def client_options() -> Any:
    """SDK options: one attempt per call, no hidden retries."""
    from google.genai import types

    return types.HttpOptions(retry_options=types.HttpRetryOptions(attempts=1))


@dataclass
class CallBudget:
    """An ``Llm`` that refuses calls past a fixed number. One per request.

    The workflows already stop on their own (what-if: one call to understand, at most
    two to explain; briefing: at most three drafts). This is the backstop if that
    logic changes: a request can never make more than ``max_calls`` model calls.
    """

    llm: Any
    max_calls: int  # The caller passes config.ANALYST_MAX_MODEL_CALLS, read at call time.
    calls: int = 0

    def structured(self, system: str, user: str, schema: dict[str, Any], max_tokens: int,
                   timeout_s: float | None = None) -> dict[str, Any]:
        if self.calls >= self.max_calls:
            raise LlmError("This request has used all its model calls.")
        self.calls += 1
        return self.llm.structured(system, user, schema, max_tokens, timeout_s=timeout_s)


def _key() -> str:
    """The API key: ``GEMINI_API_KEY`` if set, else the SSM SecureString parameter."""
    global _api_key
    if _api_key is None:
        key = os.environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            name = os.environ.get("ANALYST_KEY_PARAMETER", DEFAULT_KEY_PARAMETER)
            try:
                import boto3

                reply = boto3.client("ssm").get_parameter(Name=name, WithDecryption=True)
                key = reply["Parameter"]["Value"].strip()
            except Exception as e:  # noqa: BLE001 - no key means no model; the page says so.
                raise LlmError(f"Could not read the Gemini API key: {type(e).__name__}") from e
        if not key:
            raise LlmError("The Gemini API key is empty.")
        _api_key = key
    return _api_key


def from_environment() -> GeminiLlm:
    """Build from the settings the CDK stack writes to the Lambda environment."""
    return GeminiLlm(
        model=os.environ.get("ANALYST_MODEL", DEFAULT_MODEL),
        effort=os.environ.get("ANALYST_EFFORT") or None,
    )
