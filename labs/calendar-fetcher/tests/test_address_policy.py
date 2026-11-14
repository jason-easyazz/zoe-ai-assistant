"""The fetcher's address and URL policy, its pin to the checked address, and its contract with the engine. No real network
(``fakenet``). Negative controls switch a guard off with ``monkeypatch`` and show the attack then gets through."""
from __future__ import annotations

import ast
import ipaddress
import logging

import pytest

from fakenet import *  # noqa: F401,F403 - the fake network, constants and the ``net`` fixture
from fakenet import cf, fetcher, get, net, redirect, respond, FakeTLS  # noqa: F401 - named for readers and linters

# -- the engine contract -------------------------------------------------------------------------------------------------------

def _engine_constants():
    src = (REPO / "services/zoe-data/calendar_feed.py").read_text()
    tree = ast.parse(src)
    statuses = fields = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "FETCH_STATUSES" for t in node.targets):
            statuses = ast.literal_eval(node.value)
        if isinstance(node, ast.ClassDef) and node.name == "FetchResult":
            fields = [s.target.id for s in node.body if isinstance(s, ast.AnnAssign)]
    return statuses, fields


def test_statuses_and_fields_match_the_engine():
    statuses, fields = _engine_constants()
    assert statuses == cf.STATUSES
    assert fields == [f for f in cf.FeedResponse.__dataclass_fields__ if f != "reason"]


def test_bounds_are_inside_the_engine_bound():
    assert cf.TOTAL_TIMEOUT_S < 45.0
    assert cf.DNS_TIMEOUT_S + cf.CONNECT_TIMEOUT_S + cf.HEADER_TIMEOUT_S <= cf.TOTAL_TIMEOUT_S
    with pytest.raises(ValueError):
        cf.CalendarFetcher(total_timeout_s=60)
    with pytest.raises(ValueError):
        cf.CalendarFetcher(max_redirects=4)


# -- refused address classes --------------------------------------------------------------------------------------------------

REFUSED = [
    ("127.0.0.1", "loopback"), ("127.5.5.5", "loopback"), ("10.1.2.3", "private"), ("172.16.0.1", "private"),
    ("172.31.255.254", "private"), ("192.168.1.1", "private"), ("100.64.0.1", "cgnat"), ("100.127.255.254", "cgnat"),
    ("169.254.169.254", "metadata"), ("169.254.170.2", "metadata"), ("100.100.100.200", "metadata"),
    ("192.0.0.192", "metadata"), ("169.254.10.10", "link_local"), ("224.0.0.1", "multicast"), ("239.255.255.250", "multicast"),
    ("240.0.0.1", "reserved"), ("255.255.255.255", "reserved"), ("0.0.0.0", "unspecified"), ("0.1.2.3", "unspecified"),
    ("192.0.2.1", "documentation"), ("198.51.100.7", "documentation"), ("203.0.113.9", "documentation"),
    ("198.18.0.1", "reserved"), ("192.88.99.1", "reserved"),
    ("::1", "loopback"), ("::", "unspecified"), ("fe80::1", "link_local"), ("fe80::1%2", "link_local"), ("fc00::1", "private"),
    ("fd12:3456::1", "private"), ("fd00:ec2::254", "metadata"), ("ff02::1", "multicast"), ("fec0::1", "reserved"),
    ("2001:db8::1", "documentation"), ("2001::1", "reserved"), ("100::1", "reserved"), ("::127.0.0.1", "reserved"),
    ("::ffff:127.0.0.1", "loopback"), ("::ffff:169.254.169.254", "metadata"), ("::ffff:10.0.0.1", "private"),
    ("::ffff:100.64.0.1", "cgnat"), ("2002:7f00:1::1", "loopback"), ("2002:a9fe:a9fe::1", "metadata"),
    ("2002:c0a8:101::1", "private"), ("64:ff9b::7f00:1", "loopback"), ("64:ff9b::a9fe:a9fe", "metadata"),
    ("64:ff9b::a00:1", "private"), ("64:ff9b:1::1", "private"),
]


@pytest.mark.parametrize("address,why", REFUSED)
def test_every_refused_class_is_refused_after_resolution(net, address, why, caplog):
    caplog.set_level(logging.DEBUG, logger="calendar_fetcher")
    net.dns[HOST] = [[address]]
    r = get(net)
    assert (r.status, r.reason) == ("blocked", why)
    assert net.dns_calls == [HOST] and net.connects == []           # resolved, checked, never connected
    assert address not in caplog.text and HOST not in caplog.text


@pytest.mark.parametrize("address", [PUBLIC_V4, PUBLIC_V6, "::ffff:93.184.215.14", "64:ff9b::5db8:d70e", "2002:5db8:d70e::1"])
def test_public_addresses_pass(net, address):
    net.dns[HOST] = [[address]]
    net.servers[(str(ipaddress.ip_address(address)), 443)] = respond(200, [("Content-Type", "text/calendar")], ICS)
    r = get(net)
    assert r.status == "ok" and r.body == ICS


def test_one_private_answer_among_public_ones_refuses_the_host(net):
    net.dns[HOST] = [[PUBLIC_V4, "10.0.0.5"]]
    r = get(net)
    assert (r.status, r.reason) == ("blocked", "private") and net.connects == []


def test_negative_control_without_the_address_check_the_private_address_is_reached(net, monkeypatch):
    monkeypatch.setattr(cf, "classify_address", lambda ip: None)
    net.dns[HOST] = [["10.0.0.5"]]
    net.servers[("10.0.0.5", 443)] = respond(200, [], ICS)
    r = get(net)
    assert r.status == "ok" and net.connects == [("10.0.0.5", 443)]


@pytest.mark.parametrize("url,why", [
    ("https://2130706433/f.ics", "numeric_host"), ("https://0x7f000001/f.ics", "numeric_host"),
    ("https://127.1/f.ics", "numeric_host"), ("https://017700000001/f.ics", "numeric_host"),
    ("https://0x7f.0x0.0x0.0x1/f.ics", "numeric_host"), ("https://localhost/f.ics", "bad_host"),
    ("https://cal.localhost/f.ics", "internal_name"), ("https://metadata.google.internal/f.ics", "internal_name"),
    ("https://nas.local/f.ics", "internal_name"), ("https://router.home.arpa/f.ics", "internal_name"),
    ("https://bad_host.example.test/f.ics", "bad_host"),
])
def test_odd_and_internal_host_spellings_never_reach_a_resolver(net, url, why):
    r = get(net, url)
    assert (r.status, r.reason) == ("blocked", why) and net.dns_calls == [] and net.connects == []


@pytest.mark.parametrize("url,why", [("https://127.0.0.1/f.ics", "loopback"), ("https://[::1]/f.ics", "loopback"),
                                     ("https://[::ffff:a9fe:a9fe]/f.ics", "metadata"), ("https://169.254.169.254/", "metadata")])
def test_address_literals_are_checked_without_a_lookup(net, url, why):
    r = get(net, url)
    assert (r.status, r.reason) == ("blocked", why) and net.dns_calls == [] and net.connects == []


# -- scheme, userinfo, port, lab flag ------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url,why", [
    (f"http://{HOST}/f.ics", "not_https"), (f"ftp://{HOST}/f.ics", "scheme"), (f"file:///etc/passwd", "scheme"),
    (f"gopher://{HOST}/", "scheme"), (f"https://user:pw@{HOST}/f.ics", "userinfo"), (f"https://{HOST}:8080/f.ics", "port"),
    (f"https://{HOST}/f.ics\r\nX-Evil: 1", "bad_url"), (f"https://{HOST}/a b", "bad_url"), ("", "bad_url"),
    (f"https://{HOST}:99999/", "bad_url"),
])
def test_url_policy(net, url, why):
    r = get(net, url)
    assert (r.status, r.reason) == ("blocked", why) and net.dns_calls == []


def test_webcal_is_read_as_https(net):
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = respond(200, [], ICS)
    tls = FakeTLS()
    r = get(net, f"webcal://{HOST}/f.ics", tls=tls)
    assert r.status == "ok" and tls.names == [HOST]


def test_lab_flag_allows_http_to_loopback_only(net):
    net.servers[("127.0.0.1", 8765)] = respond(200, [], ICS)
    assert get(net, "http://127.0.0.1:8765/f.ics", lab_loopback_http=True).status == "ok"
    net.dns[HOST] = [[PUBLIC_V4]]
    r = get(net, f"http://{HOST}/f.ics", lab_loopback_http=True)
    assert (r.status, r.reason) == ("blocked", "http_not_loopback")
    r = get(net, f"https://{HOST}:8443/f.ics", lab_loopback_http=True)
    assert (r.status, r.reason) == ("blocked", "port") and net.connects == [("127.0.0.1", 8765)]
    for addr, why in (("10.0.0.1", "private"), ("169.254.169.254", "metadata"), ("192.168.0.2", "private")):
        r = get(net, f"http://{addr}:8765/f.ics", lab_loopback_http=True)
        assert (r.status, r.reason) == ("blocked", why)
    assert get(net, "http://127.0.0.1:8765/f.ics").reason == "not_https"     # off by default


def test_the_lab_flag_is_not_read_from_the_environment():
    src = (HERE.parent / "calendar_fetcher.py").read_text()
    assert "os.environ" not in src and "getenv" not in src and "import os" not in src


# -- pinning and DNS rebinding -------------------------------------------------------------------------------------------------

def test_dns_rebinding_cannot_switch_the_connection(net):
    net.dns[HOST] = [[PUBLIC_V4], ["127.0.0.1"]]          # first answer public, any later one loopback
    net.servers[(PUBLIC_V4, 443)] = respond(200, [], ICS)
    tls = FakeTLS()
    r = get(net, tls=tls)
    assert r.status == "ok"
    assert net.dns_calls == [HOST]                         # resolved once
    assert net.connects == [(PUBLIC_V4, 443)]              # to the checked address
    assert tls.names == [HOST]                             # SNI / certificate name is the host, not the address
    assert f"\r\nHost: {HOST}\r\n" in net.requests[0]


def test_negative_control_the_rebinding_fake_really_rebinds(net):
    net.dns[HOST] = [[PUBLIC_V4], ["127.0.0.1"]]
    assert net.resolver(HOST, 443, 1) == [PUBLIC_V4]
    assert net.resolver(HOST, 443, 1) == ["127.0.0.1"]


def test_http_client_can_never_connect_by_itself():
    conn = cf._PinnedConnection(cf._Target("https", HOST, 443, "/", None), sock=None)
    with pytest.raises(cf._Stop):
        conn.connect()


def test_ipv6_literal_host_header(net):
    net.servers[(PUBLIC_V6, 443)] = respond(200, [], ICS)
    tls = FakeTLS()
    r = get(net, f"https://[{PUBLIC_V6}]/f.ics", tls=tls)
    assert r.status == "ok" and f"Host: [{PUBLIC_V6}]\r\n" in net.requests[0]

