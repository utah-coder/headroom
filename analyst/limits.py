"""Hard limits on a public endpoint that spends money per question.

Two counters in DynamoDB, each raised atomically and only while below its limit:
one per visitor per hour, one for the whole site per UTC day. The daily counter
is what bounds the bill. Visitors are stored as a salted hash, never as an IP, and
the items expire on their own. Any failure here refuses the question: without the
counters there is no cap, so the handler fails closed.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = ["Limits", "visitor_id"]

_HOUR = 3600
_DAY = 86400
_EXPIRE_AFTER_S = 2 * _DAY


def visitor_id(address: str, salt: str) -> str:
    """A stable, non-reversible id for one visitor, from ``CloudFront-Viewer-Address``."""
    ip = address.rsplit(":", 1)[0] if address else "unknown"
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()[:32]


@dataclass
class Limits:
    client: Any  # boto3 DynamoDB client.
    table: str
    daily_cap: int
    hourly_per_visitor: int
    clock: Callable[[], float] = time.time

    def _take(self, key: str, limit: int, now: int) -> bool:
        try:
            self.client.update_item(
                TableName=self.table,
                Key={"pk": {"S": key}},
                UpdateExpression="ADD #n :one SET #e = :expires",
                ConditionExpression="attribute_not_exists(#n) OR #n < :limit",
                ExpressionAttributeNames={"#n": "n", "#e": "expires_at"},
                ExpressionAttributeValues={
                    ":one": {"N": "1"},
                    ":limit": {"N": str(limit)},
                    ":expires": {"N": str(now + _EXPIRE_AFTER_S)},
                },
            )
        except Exception as e:
            code = getattr(e, "response", {}).get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def take(self, visitor: str) -> str | None:
        """Count one question. Returns which limit stopped it, or ``None`` if allowed.

        The visitor is checked first, so one visitor's refused questions don't
        use up the site's daily allowance.
        """
        now = int(self.clock())
        if not self._take(f"visitor#{visitor}#{now // _HOUR}", self.hourly_per_visitor, now):
            return "visitor"
        if not self._take(f"day#{now // _DAY}", self.daily_cap, now):
            return "daily"
        return None
