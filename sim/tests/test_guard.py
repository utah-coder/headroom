"""The no-network guard trips on every socket entry point (Requirement 23.10)."""

import os
import socket

import pytest
from hypothesis import settings

from sim.tests.conftest import NetworkBlockedError


def test_socket_connect_is_blocked():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        with pytest.raises(NetworkBlockedError):
            s.connect(("127.0.0.1", 80))


def test_create_connection_is_blocked():
    with pytest.raises(NetworkBlockedError):
        socket.create_connection(("api.eia.gov", 443), timeout=1)


def test_getaddrinfo_is_blocked():
    with pytest.raises(NetworkBlockedError):
        socket.getaddrinfo("api.eia.gov", 443)


@pytest.mark.skipif("HYPOTHESIS_PROFILE" in os.environ, reason="profile overridden")
def test_ci_profile_is_default():
    current = settings()
    assert current.max_examples == 100
    assert current.deadline is None
    assert current.derandomize is True
