"""The panel agent on :8765 (screen wake / dim / brightness) - who may call it (2026-10-19).

It answered every caller on the network with no auth, and its source lived only on the Pi. Now
``request_allowed`` admits: the kiosk on the panel itself (loopback peer AND loopback Host), the Zoe server for the three
calls zoe-data makes (``/wake``, ``/reload``, ``/brightness``), and ``GET /health`` for anyone. Every other caller is
refused - a negative control for each route below. Pure function, no aiohttp needed.
"""
from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
AGENT = ROOT / "scripts" / "setup" / "touchscreen" / "zoe-panel-agent.py"
SERVER = "192.168.1.218"
PEERS = frozenset({SERVER})


@pytest.fixture(scope="module")
def agent():
    spec = importlib.util.spec_from_file_location("zoe_panel_agent", AGENT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ALL_ROUTES = [("GET", "/info"), ("GET", "/health"), ("GET", "/state"),
              ("POST", "/wake"), ("POST", "/reload"), ("POST", "/brightness")]


@pytest.mark.parametrize("method,path", ALL_ROUTES)
def test_the_kiosk_on_the_panel_reaches_every_route(agent, method, path):
    for peer, host in (("127.0.0.1", "127.0.0.1:8765"), ("::1", "[::1]:8765"), ("::ffff:127.0.0.1", "localhost:8765")):
        assert agent.request_allowed(method, path, peer, host, PEERS) is True, (peer, host)


@pytest.mark.parametrize("method,path", [r for r in ALL_ROUTES if r != ("GET", "/health")])
def test_a_rebound_name_through_the_kiosk_browser_is_refused(agent, method, path):
    """DNS rebinding: a page the kiosk opened resolves its own name to 127.0.0.1 - the peer is loopback, the Host is not."""
    assert agent.request_allowed(method, path, "127.0.0.1", "evil.example:8765", PEERS) is False
    assert agent.request_allowed(method, path, "127.0.0.1", None, PEERS) is False


@pytest.mark.parametrize("method,path", [("POST", "/wake"), ("POST", "/reload"), ("POST", "/brightness"), ("GET", "/health")])
def test_the_zoe_server_reaches_exactly_the_calls_zoe_data_makes(agent, method, path):
    assert agent.request_allowed(method, path, SERVER, "192.168.1.61:8765", PEERS) is True
    assert agent.request_allowed(method, path, "::ffff:" + SERVER, "192.168.1.61:8765", PEERS) is True


@pytest.mark.parametrize("method,path", [("GET", "/state"), ("GET", "/info")])
def test_the_presence_bearing_reads_are_loopback_only_even_for_the_server(agent, method, path):
    assert agent.request_allowed(method, path, SERVER, "192.168.1.61:8765", PEERS) is False


@pytest.mark.parametrize("method,path", [r for r in ALL_ROUTES if r != ("GET", "/health")])
def test_THE_BUG_any_other_device_on_the_network_is_refused(agent, method, path):
    for peer in ("192.168.1.77", "100.64.0.9", "203.0.113.5", "", None):
        assert agent.request_allowed(method, path, peer, "192.168.1.61:8765", PEERS) is False, peer
        assert agent.request_allowed(method, path, peer, "127.0.0.1:8765", PEERS) is False, peer   # a forged Host is not enough


def test_health_stays_public(agent):
    assert agent.request_allowed("GET", "/health", "192.168.1.77", "x", PEERS) is True


def test_the_rollback_opens_everything_and_is_explicit(agent, monkeypatch):
    assert agent.request_allowed("POST", "/brightness", "192.168.1.77", "x", PEERS, guard=False) is True
    for value in ("", "on", "1", "yes", "anything"):
        monkeypatch.setenv("AGENT_GUARD", value)
        assert agent.guard_enabled() is True, value
    for value in ("off", "0", "false", "no", "OFF"):
        monkeypatch.setenv("AGENT_GUARD", value)
        assert agent.guard_enabled() is False, value


def test_the_server_peers_come_from_zoe_api_or_the_explicit_list_and_never_loopback(agent):
    assert agent.allowed_peers({"ZOE_API": "https://192.168.1.218"}) == frozenset({"192.168.1.218"})
    assert agent.allowed_peers({"ZOE_API": "https://zoe.local", "AGENT_ALLOWED_PEERS": "192.168.1.218, 10.0.0.2"}) == \
        frozenset({"192.168.1.218", "10.0.0.2"})
    fake = lambda host, port: [(None, None, None, None, ("192.168.1.218", 0))]        # noqa: E731
    assert agent.allowed_peers({"ZOE_API": "https://zoe.local"}, resolve=fake) == frozenset({"192.168.1.218"})
    assert agent.allowed_peers({"ZOE_API": "https://127.0.0.1"}) == frozenset()       # loopback is the kiosk's rule

    def broken(host, port):
        raise OSError("no dns")

    assert agent.allowed_peers({"ZOE_API": "https://zoe.local"}, resolve=broken) == frozenset()


def test_zoe_data_still_calls_only_routes_the_server_may_reach():
    """Drift guard: if zoe-data starts calling another agent route, this policy must be revisited in the same change."""
    data = ROOT / "services" / "zoe-data"
    called = set()
    for f in (data / "auth_handoff.py", data / "routers" / "system.py"):
        called |= set(re.findall(r'(?:_PANEL_AGENT_PORT\}|\{port\})/(\w+)', f.read_text()))
    assert called == {"wake", "reload", "brightness", "volume"}, called


def test_every_server_call_is_a_server_route(agent):
    for path in ("/wake", "/reload", "/brightness", "/volume"):
        assert ("POST", path) in agent.SERVER_ROUTES
