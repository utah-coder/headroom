"""Hard limits on a public endpoint that spends money per question.

Two counters in DynamoDB, each raised atomically and only while below its limit:
one per visitor per hour, one for the whole site per UTC day. The daily counter
bounds how many questions reach the model, and with the per-question call and
token limits in ``analyst/llm.py`` that bounds model spend. It does not bound
AWS charges (CloudFront, Lambda, DynamoDB, logs, WAF), which grow with traffic
whether or not a question is answered.

Visitors are stored as a salted hash, never as an IP, and the counter items
expire on their own. Any failure here refuses the question: without the counters
there is no cap, so the handler fails closed.

The same table holds one control item, the operator's switch. While it is set,
no question reaches the model. ``scripts/analyst-switch.sh`` sets and clears it.
"""

from __future__ import annotations

import hashlib
import ipaddress
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

__all__ = ["Limits", "visitor_id", "viewer_ip", "SWITCH_KEY", "UNIDENTIFIED"]

_HOUR = 3600
_DAY = 86400
_EXPIRE_AFTER_S = 2 * _DAY
# The switch item has no expires_at, so the table's TTL never removes it.
SWITCH_KEY = "control#analyst"
# Requests whose viewer address is missing or malformed all share this one visitor
# bucket, so a forged or duplicated header can't buy a fresh hourly allowance.
UNIDENTIFIED = "unidentified"
# One IPv6 subscriber usually holds a whole /64, so a visitor is the /64, not one address.
_IPV6_VISITOR_PREFIX = 64


def viewer_ip(header: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The viewer's IP from ``CloudFront-Viewer-Address``, or ``None`` if the header isn't exactly one.

    CloudFront generates this header as ``<ip>:<port>`` from the connection it accepted.
    This code doesn't depend on CloudFront discarding a copy the viewer sent: a function
    URL joins repeated headers with commas, so a comma means more than one value arrived.
    That, or anything else that isn't one address and port, gets ``None``.
    """
    if not header or not isinstance(header, str):
        return None
    value = header.strip()
    if "," in value or " " in value:
        return None
    host, sep, port = value.rpartition(":")
    if not sep or not port.isdigit() or not 0 < int(port) < 65536:
        return None
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None


def visitor_id(address: str | None, salt: str) -> str:
    """A stable, non-reversible id for one visitor, from ``CloudFront-Viewer-Address``."""
    ip = viewer_ip(address)
    if ip is None:
        who = UNIDENTIFIED
    elif ip.version == 6:
        who = str(ipaddress.ip_network(f"{ip}/{_IPV6_VISITOR_PREFIX}", strict=False))
    else:
        who = str(ip)
    return hashlib.sha256(f"{salt}:{who}".encode()).hexdigest()[:32]


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

    def switched_off(self) -> bool:
        """Whether the operator has switched the analyst off. Raises if the table can't be read."""
        reply = self.client.get_item(
            TableName=self.table,
            Key={"pk": {"S": SWITCH_KEY}},
            ConsistentRead=True,
            ProjectionExpression="#off",
            ExpressionAttributeNames={"#off": "off"},
        )
        return reply.get("Item", {}).get("off", {}).get("BOOL") is True

    def take(self, visitor: str) -> str | None:
        """Count one question. Returns which limit stopped it, or ``None`` if allowed.

        A zero or negative limit refuses everything. The visitor is checked first, so
        one visitor's refused questions don't use up the site's daily allowance.
        """
        if self.daily_cap <= 0:
            return "daily"
        if self.hourly_per_visitor <= 0:
            return "visitor"
        now = int(self.clock())
        if not self._take(f"visitor#{visitor}#{now // _HOUR}", self.hourly_per_visitor, now):
            return "visitor"
        if not self._take(f"day#{now // _DAY}", self.daily_cap, now):
            return "daily"
        return None
