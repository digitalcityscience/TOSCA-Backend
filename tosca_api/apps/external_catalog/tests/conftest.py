"""Shared test setup for the external catalog: no real network, fresh cache.

Saving category items triggers a best-effort check against the remote
service (ticket 03). Tests must never reach the network, so real HTTP fails
like an unreachable service unless a test routes ``requests.get`` itself,
and every host resolves to a public address.
"""

from __future__ import annotations

import socket

import pytest
import requests
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _fresh_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    def unreachable(*args, **kwargs):
        raise requests.ConnectionError("network disabled in tests")

    monkeypatch.setattr(requests, "get", unreachable)
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda host, port: [(socket.AF_INET, None, None, "", ("93.184.216.34", 0))]
    )
