"""Shared test setup: the no-network guard and the Hypothesis profiles.

Requirement 23.10: the Test_Suite never touches the network. An autouse fixture
replaces the socket entry points so any connection attempt raises
NetworkBlockedError.

Hypothesis profile "ci" (reproducible) is selected by default. Set
HYPOTHESIS_PROFILE=dev for random seeds.
"""

import os
import socket

import pytest
from hypothesis import settings


class NetworkBlockedError(RuntimeError):
    """Raised when a test tries to open a network connection."""


settings.register_profile("ci", max_examples=100, deadline=None, derandomize=True)
settings.register_profile("dev", max_examples=100, deadline=None)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


def _blocked(*args, **kwargs):
    raise NetworkBlockedError("tests must not use the network")


def block_network(mp: pytest.MonkeyPatch) -> None:
    """Patch the socket entry points on ``mp`` so any connection attempt raises.

    Session-scoped fixtures run before the function-scoped ``no_network``
    fixture, so they call this inside ``pytest.MonkeyPatch.context()``.
    """
    mp.setattr(socket.socket, "connect", _blocked)
    mp.setattr(socket, "create_connection", _blocked)
    mp.setattr(socket, "getaddrinfo", _blocked)


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    block_network(monkeypatch)
