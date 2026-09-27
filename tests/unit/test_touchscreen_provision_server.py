"""The Pi's first-boot provision proxy never hands the poll secret to a LAN caller.

The proxy (scripts/setup/touchscreen/provision-server.py) keeps the attempt's
``poll_secret`` and attaches it to status polls, so it IS the pairing device:
anyone who could reach it with the on-screen code would collect the kiosk
device token (Greptile, #1741). Polls are loopback-only, and the server refuses
a non-loopback ``--host`` unless ``--allow-lan`` is explicit.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

_PATH = Path(__file__).resolve().parents[2] / "scripts/setup/touchscreen/provision-server.py"
_spec = importlib.util.spec_from_file_location("provision_server", _PATH)
ps = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ps)


def _handler(client_ip: str):
    h = ps.ProvisionHandler.__new__(ps.ProvisionHandler)
    h.path = "/proxy/provision/ABC234"
    h.client_address = (client_ip, 50000)
    h.sent, h.proxied = [], []
    h._send_json = lambda obj, status=200: h.sent.append((status, obj))
    h._proxy_get = lambda api_path, headers=None: h.proxied.append((api_path, headers))
    return h


@pytest.fixture(autouse=True)
def _secret(monkeypatch):
    monkeypatch.setattr(ps, "_POLL_SECRETS", {"ABC234": "the-secret"})


@pytest.mark.parametrize("ip", ["192.168.1.50", "10.0.0.7", "fe80::1"])
def test_lan_caller_cannot_poll_through_the_proxy(ip):
    h = _handler(ip)
    h.do_GET()
    assert h.proxied == [] and h.sent[0][0] == 403


@pytest.mark.parametrize("ip", ["127.0.0.1", "::1"])
def test_the_local_kiosk_page_polls_with_the_secret(ip):
    h = _handler(ip)
    h.do_GET()
    assert h.proxied == [("/api/panels/provision/ABC234", {"X-Provision-Secret": "the-secret"})]


def test_non_loopback_bind_needs_explicit_allow_lan():
    assert ps._provision_bind_allowed("127.0.0.1", False)
    assert ps._provision_bind_allowed("localhost", False)
    assert not ps._provision_bind_allowed("0.0.0.0", False)
    assert not ps._provision_bind_allowed("192.168.1.20", False)
    assert ps._provision_bind_allowed("0.0.0.0", True)


def test_main_refuses_a_lan_host_without_the_flag(monkeypatch):
    monkeypatch.setattr("sys.argv", ["provision-server.py", "--mode", "provision", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit):
        ps.main()


def test_poll_secret_is_stripped_from_the_page_response(monkeypatch):
    monkeypatch.setattr(ps, "_POLL_SECRETS", {})
    out = ps._keep_poll_secret(b'{"code": "xyz789", "poll_secret": "s", "expires_in": 300}')
    assert b"poll_secret" not in out and ps._POLL_SECRETS == {"XYZ789": "s"}
