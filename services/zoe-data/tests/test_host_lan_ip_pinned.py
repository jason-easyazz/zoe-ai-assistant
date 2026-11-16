"""Importing ``main`` under test must not ask the host's routing table for its LAN address.

``main._self_lan_origins()`` runs at import and, with ``ZOE_HOST_LAN_IP`` unset, "connects" a UDP socket to a public
address to learn the outbound interface. Nothing is sent, but the result (and so the websocket origin allow-list the
tests run against) depended on the machine: a real address online, a warning and no entry offline. Measured
2026-11-14 with the network blocked: 17 ``ci_safe`` files did this. ``tests/conftest.py`` pins the variable.
"""
from __future__ import annotations

import os
import socket

import pytest

pytestmark = pytest.mark.ci_safe


def test_the_conftest_pins_the_lan_address():
    assert os.environ.get("ZOE_HOST_LAN_IP"), "tests/conftest.py must pin ZOE_HOST_LAN_IP before main is imported"


def test_self_lan_origins_opens_no_socket_when_pinned(monkeypatch):
    import main

    def refuse(*a, **k):
        raise AssertionError("_self_lan_origins() opened a socket although ZOE_HOST_LAN_IP is set")

    monkeypatch.setenv("ZOE_HOST_LAN_IP", "192.0.2.44")
    monkeypatch.setattr(socket, "socket", refuse)
    assert "https://192.0.2.44" in main._self_lan_origins()


def test_negative_control_unpinned_discovery_does_open_a_socket(monkeypatch):
    import main

    opened = []

    class _Probe:
        def __init__(self, *a, **k):
            opened.append(a)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def connect(self, addr):
            raise OSError("no route (test)")

    monkeypatch.delenv("ZOE_HOST_LAN_IP", raising=False)
    monkeypatch.setattr(socket, "socket", _Probe)
    main._self_lan_origins()
    assert opened, "the unpinned path no longer probes: this pin and its test can go"
