"""The fetcher's redirects, size caps, time bounds, status mapping, validators and its no-leak rule. No real network
(``fakenet``). Negative controls switch a guard off with ``monkeypatch`` and show the attack then gets through."""
from __future__ import annotations

import logging
import ssl
import threading
import time

import pytest

from fakenet import *  # noqa: F401,F403 - the fake network, constants and the ``net`` fixture
from fakenet import FakeNet, FakeTLS, cf, fetcher, get, net, redirect, respond  # noqa: F401

# -- redirects -----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("location,why", [
    ("https://127.0.0.1/x", "loopback"), ("https://169.254.169.254/latest/meta-data/", "metadata"),
    ("https://[::ffff:169.254.169.254]/", "metadata"), (f"http://{HOST}/f.ics", "downgrade"),
    ("http://127.0.0.1/x", "downgrade"), ("file:///etc/passwd", "scheme"), ("https://inner.example.test/f.ics", "private"),
    ("https://2130706433/", "numeric_host"),
])
def test_a_redirect_hop_is_checked_like_the_first(net, location, why):
    net.dns[HOST] = [[PUBLIC_V4]]
    net.dns["inner.example.test"] = [["192.168.1.20"]]
    net.servers[(PUBLIC_V4, 443)] = redirect(location)
    r = get(net)
    assert (r.status, r.reason) == ("blocked", why)
    assert net.connects == [(PUBLIC_V4, 443)]


def test_downgrade_is_refused_even_under_the_lab_flag(net):
    net.servers[("127.0.0.1", 443)] = redirect("http://127.0.0.1:8765/f.ics")
    r = get(net, "https://127.0.0.1/f.ics", lab_loopback_http=True)
    assert (r.status, r.reason) == ("blocked", "downgrade")


def test_a_fourth_redirect_across_hosts_is_never_followed(net):
    for i, host in enumerate(("a.example.test", "b.example.test", "c.example.test")):
        net.dns[host] = [[f"93.184.215.{20 + i}"]]
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = redirect("https://a.example.test/one")
    net.servers[("93.184.215.20", 443)] = redirect("https://b.example.test/two", 301)
    net.servers[("93.184.215.21", 443)] = [redirect("/three", 307), redirect("https://c.example.test/four", 308)]
    net.servers[("93.184.215.22", 443)] = respond(200, [], ICS)
    # chain: HOST -> a -> b(/two) -> b(/three) is the 3rd redirect -> would need a 4th to reach c
    r = get(net)
    assert (r.status, r.reason) == ("server_error", "too_many_redirects")
    assert len(net.connects) == 4


def test_a_chain_of_exactly_three_redirects_succeeds(net):
    net.dns.update({HOST: [[PUBLIC_V4]], "a.example.test": [["93.184.215.20"]], "b.example.test": [["93.184.215.21"]]})
    net.servers[(PUBLIC_V4, 443)] = redirect("https://a.example.test/one")
    net.servers[("93.184.215.20", 443)] = redirect("https://b.example.test/two")
    net.servers[("93.184.215.21", 443)] = [redirect("/three", 303), respond(200, [], ICS)]
    r = get(net)
    assert r.status == "ok" and r.body == ICS
    assert net.dns_calls == [HOST, "a.example.test", "b.example.test", "b.example.test"]   # every hop re-resolved
    assert "GET /three " in net.requests[-1]


def test_a_chain_of_four_redirects_is_refused(net):
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = [redirect(f"/r{i}") for i in range(4)] + [respond(200, [], ICS)]
    r = get(net)
    assert (r.status, r.reason) == ("server_error", "too_many_redirects")
    assert len(net.connects) == 4                      # the 5th request is never made


def test_negative_control_a_looser_redirect_limit_would_follow_the_fourth(net, monkeypatch):
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = [redirect(f"/r{i}") for i in range(4)] + [respond(200, [], ICS)]
    f = fetcher(net)
    monkeypatch.setattr(f, "_max_redirects", 4)
    assert f.fetch(f"https://{HOST}/f.ics", max_bytes=10_000).status == "ok"


def test_negative_control_without_the_downgrade_rule_http_would_be_followed(net, monkeypatch):
    real = cf._parse_target
    monkeypatch.setattr(cf, "_parse_target", lambda url, **kw: real(url, **dict(kw, previous_scheme=None)))
    net.servers[("127.0.0.1", 443)] = redirect("http://127.0.0.1:8765/f.ics")
    net.servers[("127.0.0.1", 8765)] = respond(200, [], ICS)
    assert get(net, "https://127.0.0.1/f.ics", lab_loopback_http=True).status == "ok"


# -- size cap ------------------------------------------------------------------------------------------------------------------

def _stream(total, chunk=64 * 1024):
    def handler(conn, request):
        conn.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n")       # close-delimited: no length announced
        sent = 0
        piece = b"X" * chunk
        while sent < total and not conn.stop.is_set():
            conn.sendall(piece)
            sent += chunk
    return handler


def test_body_over_the_cap_aborts_mid_stream(net):
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = _stream(8 * 1024 * 1024)
    cap = 100_000
    r = get(net, max_bytes=cap)
    assert (r.status, r.reason) == ("too_large", "body_too_large") and r.body == b""
    got = net.socks[0].received
    assert cap < got <= cap + 64 * 1024 + 8192 + 512          # cap, plus at most one read and one buffer, plus headers
    assert got < 8 * 1024 * 1024 // 10


def test_one_byte_over_the_cap_is_refused_and_exactly_the_cap_is_kept(net):
    net.dns[HOST] = [[PUBLIC_V4]]
    body = b"B" * 5000
    net.servers[(PUBLIC_V4, 443)] = [respond(200, [], body, chunked=True), respond(200, [], body, chunked=True)]
    assert get(net, max_bytes=4999).status == "too_large"
    r = get(net, max_bytes=5000)
    assert r.status == "ok" and r.body == body


def test_a_declared_length_over_the_cap_is_refused_before_reading(net):
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = _declared(10_000_000)
    r = get(net, max_bytes=1000)
    assert (r.status, r.reason) == ("too_large", "declared_too_large")
    assert net.socks[0].received < 8192 + 512


def _declared(n):
    def handler(conn, request):
        conn.sendall(f"HTTP/1.1 200 OK\r\nContent-Length: {n}\r\nConnection: close\r\n\r\n".encode() + b"Y" * 4096)
    return handler


def test_negative_control_without_the_streaming_cap_the_whole_body_is_read(net, monkeypatch):
    net.dns[HOST] = [[PUBLIC_V4]]
    total = 2 * 1024 * 1024
    net.servers[(PUBLIC_V4, 443)] = _stream(total)

    def read_all(self, resp, clock, max_bytes):
        data = resp.read()
        if len(data) > max_bytes:
            raise cf._Stop("too_large", "body_too_large")
        return data
    monkeypatch.setattr(cf.CalendarFetcher, "_read_body", read_all)
    get(net, max_bytes=100_000)
    assert net.socks[0].received >= total                  # the bounded-read assertion above would fail


def test_headers_are_capped(net):
    def handler(conn, request):
        conn.sendall(b"HTTP/1.1 200 OK\r\n")
        for i in range(60):                                 # under http.client's 100-header and 64 KiB-line limits
            if conn.stop.is_set():
                return
            conn.sendall(f"X-Pad-{i}: {'p' * 2000}\r\n".encode())
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = handler
    r = get(net)
    assert (r.status, r.reason) == ("server_error", "headers_too_large")
    assert net.socks[0].received <= cf.MAX_HEADER_BYTES


# -- timeouts ------------------------------------------------------------------------------------------------------------------

def _dribble_headers(conn, request):
    for b in b"HTTP/1.1 200 OK\r\nX-Slow: " + b"a" * 10_000:
        if conn.stop.is_set():
            return
        conn.sendall(bytes([b]))
        time.sleep(0.02)


def test_slow_loris_headers_hit_the_header_deadline(net):
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = _dribble_headers
    t0 = time.monotonic()
    r = get(net, header_timeout_s=0.5, read_timeout_s=0.5, total_timeout_s=5)
    took = time.monotonic() - t0
    assert (r.status, r.reason) == ("network", "timeout")
    assert 0.4 < took < 1.5                                # each byte arrives well inside the per-read wait: the phase bound ends it


def test_slow_body_hits_the_total_deadline(net):
    def handler(conn, request):
        conn.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n")
        while not conn.stop.is_set():
            conn.sendall(b"Z")
            time.sleep(0.02)
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = handler
    t0 = time.monotonic()
    r = get(net, read_timeout_s=0.5, total_timeout_s=1.0)
    took = time.monotonic() - t0
    assert (r.status, r.reason) == ("network", "timeout") and 0.9 < took < 2.0


def test_a_silent_server_hits_the_read_timeout(net):
    def handler(conn, request):
        conn.sendall(b"HTTP/1.1 200 OK\r\nConnection: close\r\n\r\npartial")
        conn.stop.wait(5)
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = handler
    t0 = time.monotonic()
    r = get(net, read_timeout_s=0.3, total_timeout_s=5)
    assert (r.status, r.reason) == ("network", "timeout") and time.monotonic() - t0 < 1.5


def test_negative_control_without_the_phase_deadline_slow_headers_outlast_it(net, monkeypatch):
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = _dribble_headers
    real = cf._Clock.phase
    monkeypatch.setattr(cf._Clock, "phase", lambda self, **kw: real(self, **dict(kw, deadline_s=1e9)))
    t0 = time.monotonic()
    get(net, header_timeout_s=0.5, read_timeout_s=0.5, total_timeout_s=2.0)
    assert time.monotonic() - t0 > 1.5                     # only the total bound stopped it


def test_a_resolver_that_hangs_is_bounded(monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(cf.socket, "getaddrinfo", lambda *a, **k: gate.wait(5) and [])
    t0 = time.monotonic()
    with pytest.raises(cf._Stop) as e:
        cf.system_resolver(HOST, 443, 0.3)
    gate.set()
    assert e.value.reason == "dns_timeout" and time.monotonic() - t0 < 1.0


# -- status mapping and validators ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("code,status", [(401, "unauthorized"), (403, "unauthorized"), (404, "not_found"), (410, "not_found"),
                                         (429, "rate_limited"), (500, "server_error"), (503, "server_error"),
                                         (400, "server_error"), (418, "server_error")])
def test_status_mapping(net, code, status):
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = respond(code, [("Retry-After", "120")], b"provider says no " + TOKEN.encode())
    r = get(net)
    assert r.status == status and r.body == b""
    assert r.retry_after_s == (120 if status in ("rate_limited", "server_error") else None)


def test_retry_after_as_a_date_and_garbage(net):
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = [respond(429, [("Retry-After", "Wed, 21 Oct 2099 07:28:00 GMT")]),
                                     respond(429, [("Retry-After", "soon")])]
    assert get(net).retry_after_s == cf.MAX_RETRY_AFTER_S
    assert get(net).retry_after_s is None


def test_validators_round_trip(net):
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = [
        respond(200, [("ETag", '"v1"'), ("Last-Modified", "Tue, 01 Dec 2026 00:00:00 GMT"), ("Content-Type", "text/calendar")],
                ICS),
        respond(304, [("ETag", '"v1"')]),
    ]
    f = fetcher(net)
    first = f.fetch(f"https://{HOST}/f.ics", max_bytes=10_000)
    assert first.status == "ok" and first.etag == '"v1"' and first.content_type == "text/calendar"
    assert "If-None-Match" not in net.requests[0]
    second = f.fetch(f"https://{HOST}/f.ics", max_bytes=10_000, etag=first.etag, last_modified=first.last_modified)
    assert second.status == "not_modified" and second.body == b""
    assert (second.etag, second.last_modified) == ('"v1"', "Tue, 01 Dec 2026 00:00:00 GMT")
    assert '\r\nIf-None-Match: "v1"\r\n' in net.requests[1]
    assert "\r\nIf-Modified-Since: Tue, 01 Dec 2026 00:00:00 GMT\r\n" in net.requests[1]


def test_unsafe_validators_are_never_sent_or_returned(net):
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = respond(200, [("ETag", '"' + "e" * 400 + '"')], ICS)
    r = fetcher(net).fetch(f"https://{HOST}/f.ics", max_bytes=10_000, etag='"x"\r\nX-Evil: 1')
    assert "X-Evil" not in net.requests[0] and "If-None-Match" not in net.requests[0]
    assert r.etag is None


def test_request_shape(net):
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = respond(200, [], ICS)
    get(net, f"https://{HOST}/feed/{TOKEN}.ics?k={TOKEN}#frag")
    req = net.requests[0]
    assert req.startswith(f"GET /feed/{TOKEN}.ics?k={TOKEN} HTTP/1.1\r\n")   # the token goes to the provider and nowhere else
    assert "\r\nAccept-Encoding: identity\r\n" in req and "frag" not in req and "Proxy" not in req


# -- failures and the no-leak rule ---------------------------------------------------------------------------------------------

def test_connect_failure_and_tls_failure_are_network(net, caplog):
    caplog.set_level(logging.DEBUG, logger="calendar_fetcher")
    net.dns[HOST] = [[PUBLIC_V4]]
    net.connect_error = ConnectionRefusedError(f"refused {HOST} {TOKEN}")
    assert (get(net).status, get(net).reason) == ("network", "connect_failed")
    net.connect_error = None
    net.servers[(PUBLIC_V4, 443)] = respond(200, [], ICS)
    tls = FakeTLS(fail=__import__("ssl").SSLCertVerificationError(f"certificate is not valid for '{HOST}' {TOKEN}"))
    r = get(net, tls=tls)
    assert (r.status, r.reason) == ("network", "tls_failed")
    assert TOKEN not in caplog.text and HOST not in caplog.text


def test_a_garbage_response_is_network(net):
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = lambda conn, req: conn.sendall(b"NOT HTTP AT ALL " + TOKEN.encode() + b"\r\n\r\n")
    r = get(net)
    assert (r.status, r.reason) == ("network", "bad_response")


def test_the_sentinel_token_never_leaks(net, caplog, monkeypatch):
    """Every outcome, with the token in the path and the query and in every provider / exception text it could ride in on."""
    caplog.set_level(logging.DEBUG)
    url = f"https://{HOST}/feed/{TOKEN}.ics?token={TOKEN}"
    results = []
    scenarios = [
        ([PUBLIC_V4], respond(200, [("ETag", '"e"')], ICS)),
        ([PUBLIC_V4], respond(404, [], f"no such feed {TOKEN} at {HOST}".encode())),
        ([PUBLIC_V4], redirect(f"https://127.0.0.1/{TOKEN}?again={TOKEN}")),
        ([PUBLIC_V4], redirect(f"http://{HOST}/{TOKEN}")),
        ([PUBLIC_V4], [redirect(f"/r{i}/{TOKEN}") for i in range(4)]),
        ([PUBLIC_V4], _stream(500_000)),
        ([PUBLIC_V4], lambda conn, req: conn.sendall(f"HTTP/1.1 999 {TOKEN}\r\n\r\n".encode())),
        (["10.9.9.9"], None),
    ]
    for answers, handler in scenarios:
        n = FakeNet({HOST: [answers]}, {(answers[0], 443): handler} if handler else {})
        results.append(cf.CalendarFetcher(resolver=n.resolver, connector=n.connector, tls_context=FakeTLS()).fetch(
            url, max_bytes=100_000))
        n.close()
    boom = FakeNet({HOST: [[PUBLIC_V4]]}, connect_error=RuntimeError(f"{url} exploded"))
    results.append(cf.CalendarFetcher(resolver=boom.resolver, connector=boom.connector, tls_context=FakeTLS()).fetch(
        url, max_bytes=100_000))
    monkeypatch.setattr(cf, "_parse_target", lambda *a, **k: (_ for _ in ()).throw(ValueError(url)))
    results.append(cf.CalendarFetcher().fetch(url, max_bytes=1))
    assert [r.status for r in results] == ["ok", "not_found", "blocked", "blocked", "server_error", "too_large", "server_error",
                                           "blocked", "network", "network"]
    text = "\n".join(r.getMessage() + repr(r.args) + str(r.exc_text) for r in caplog.records)
    for r in results:
        fields = (r.etag, r.last_modified, r.content_type, r.reason, repr(r), str(r.retry_after_s))
        assert not any(TOKEN in str(v) or HOST in str(v) for v in fields)
        assert TOKEN.encode() not in r.body
    assert TOKEN not in text and HOST not in text and "10.9.9.9" not in text and "127.0.0.1" not in text
    assert all(r.name == "calendar_fetcher" for r in caplog.records if "CALENDAR_FETCH" in r.getMessage())
    assert sum("host=" in r.getMessage() for r in caplog.records) >= len(results) - 1


def test_negative_control_the_leak_check_sees_a_leak(net, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    net.dns[HOST] = [["10.9.9.9"]]
    monkeypatch.setattr(cf, "host_tag", lambda host: host)                # a careless log of the raw host
    get(net, f"https://{HOST}/feed/{TOKEN}.ics")
    assert HOST in caplog.text


def test_repr_never_shows_the_body():
    r = cf.FeedResponse("ok", body=b"SECRET-EVENT-TITLE")
    assert "SECRET" not in repr(r) and "body_len=18" in repr(r)


# -- the engine adapter --------------------------------------------------------------------------------------------------------

class _Req:
    def __init__(self, account_id, etag=None, last_modified=None, max_bytes=10_000):
        self.account_id, self.etag, self.last_modified, self.max_bytes = account_id, etag, last_modified, max_bytes


class _Result:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_engine_adapter(net):
    net.dns[HOST] = [[PUBLIC_V4]]
    net.servers[(PUBLIC_V4, 443)] = respond(304, [("ETag", '"v2"')])
    links = {"acct_1": f"https://{HOST}/f.ics"}
    fetch = cf.engine_fetcher(links.get, _Result, fetcher(net))
    r = fetch(_Req("acct_1", etag='"v2"'))
    assert r.status == "not_modified" and r.etag == '"v2"' and not hasattr(r, "reason")
    assert fetch(_Req("acct_unknown")).status == "unauthorized"
    assert net.dns_calls == [HOST]
