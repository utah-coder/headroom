"""Claude on Amazon Bedrock, reduced to the one call the workflow needs.

Access differs by account. Model listings don't show it: a listed model can still
answer "not available for this account", and a model can work on one Bedrock
route and not the other. So the model, route and region are deploy settings, and
``scripts/bedrock-probe.sh`` finds a combination the account can actually call.

* ``mantle``: the Messages API endpoint, IDs like ``anthropic.claude-opus-5-5``.
* ``invoke``: the InvokeModel endpoint, inference profiles like
  ``us.anthropic.claude-haiku-4-5-20251001-v1:0``.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol

from sim.config import ANALYST_CALL_TIMEOUT_S

__all__ = ["LlmError", "LlmRefused", "Llm", "BedrockLlm", "from_environment"]


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
class BedrockLlm:
    """``Llm`` over the Anthropic SDK's Bedrock clients. Counts tokens for the logs."""

    model: str
    route: str
    region: str
    effort: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    _client: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if self.route not in ("mantle", "invoke"):
            raise ValueError(f"unknown Bedrock route: {self.route}")

    def _get_client(self) -> Any:
        if self._client is None:
            from anthropic import AnthropicBedrock, AnthropicBedrockMantle

            cls = AnthropicBedrockMantle if self.route == "mantle" else AnthropicBedrock
            # No SDK retries: the workflow owns its time budget.
            self._client = cls(aws_region=self.region, timeout=ANALYST_CALL_TIMEOUT_S, max_retries=0)
        return self._client

    def structured(self, system: str, user: str, schema: dict[str, Any], max_tokens: int,
                   timeout_s: float | None = None) -> dict[str, Any]:
        import anthropic

        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:  # Not every model accepts effort (Haiku 4.5 rejects it).
            output_config["effort"] = self.effort
        client = self._get_client()
        if timeout_s is not None:
            client = client.with_options(timeout=timeout_s)
        try:
            response = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
            )
        except anthropic.APIStatusError as e:
            raise LlmError(f"Bedrock HTTP {e.status_code}: {type(e).__name__}") from e
        except anthropic.APIConnectionError as e:
            raise LlmError(f"Bedrock connection failed: {type(e).__name__}") from e

        self.input_tokens += response.usage.input_tokens
        self.output_tokens += response.usage.output_tokens
        if response.stop_reason == "refusal":
            raise LlmRefused("The model declined this request.")
        if response.stop_reason == "max_tokens":
            raise LlmError("The model ran out of output tokens.")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise LlmError("The model returned no text.")
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            raise LlmError("The model returned invalid JSON.") from e
        if not isinstance(parsed, dict):
            raise LlmError("The model returned JSON that isn't an object.")
        return parsed


def from_environment() -> BedrockLlm:
    """Build from the settings the CDK stack writes to the Lambda environment."""
    region = os.environ.get("ANALYST_REGION") or os.environ.get("AWS_REGION")
    if not region:
        raise LlmError("ANALYST_REGION is not set.")
    return BedrockLlm(
        model=os.environ.get("ANALYST_MODEL", "anthropic.claude-opus-5-5"),
        route=os.environ.get("ANALYST_ROUTE", "mantle"),
        region=region,
        effort=os.environ.get("ANALYST_EFFORT") or None,
    )
