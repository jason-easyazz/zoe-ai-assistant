"""The fake network for the calendar fetcher tests: the helpers and the ``net`` fixture. NO real network: a fake resolver, and
a connector that hands back one end of an in-process ``socket.socketpair()`` served by a scripted thread; TLS is a fake context
that records the SNI name and passes the socket through. Synthetic hosts (``*.example.test``) and a sentinel token only.
"""
from __future__ import annotations

import ast
import ipaddress
import logging
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import calendar_fetcher as cf  # noqa: E402

REPO = HERE.parents[2]
HOST = "cal.example.test"
PUBLIC_V4 = "93.184.215.14"
PUBLIC_V6 = "2001:4860:4860::8888"
TOKEN = "SENTINELTOKEN7f3a9c"
ICS = b"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//lab//EN\r\nEND:VCALENDAR\r\n"


# -- the fake network ---------------------------------------------------------------------------------------------------------

class CountingSock:
    """The client end the fetcher sees; counts every byte it receives."""

    def __init__(self, sock):
        self._s = sock
        self.received = 0

    def recv_into(self, buf, *a):
        n = self._s.recv_into(buf, *a)
        self.received += n
        return n

    def __getattr__(self, name):
        return getattr(self._s, name)


class FakeTLS:
    def __init__(self, fail: Exception | None = None):
        self.names = []
        self.fail = fail

    def wrap_socket(self, sock, server_hostname=None):
        self.names.append(server_hostname)
        if self.fail:
            raise self.fail
        return sock


def respond(status=200, headers=(), body=b"", *, chunked=False):
    """A handler that sends one scripted response."""
    def handler(conn, request):
        head = [f"HTTP/1.1 {status} X"] + [f"{k}: {v}" for k, v in headers]
        if chunked:
            head.append("Transfer-Encoding: chunked")
        elif not any(k.lower() == "content-length" for k, _ in headers):
            head.append(f"Content-Length: {len(body)}")
        head.append("Connection: close")
        out = ("\r\n".join(head) + "\r\n\r\n").encode()
        if chunked:
            out += f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
        else:
            out += body
        conn.sendall(out)
    return handler


def redirect(location, code=302):
    return respond(code, [("Location", location)])


class FakeNet:
    """``dns``: host -> list of answers (one per lookup; the last repeats). ``servers``: (address, port) -> handler, or a list of
    handlers used one per connection."""

    def __init__(self, dns=None, servers=None, connect_error: Exception | None = None):
        self.dns = dns or {}
        self.servers = servers or {}
        self.dns_calls = []
        self.connects = []
        self.requests = []
        self.socks = []
        self.served = []
        self.threads = []
        self.connect_error = connect_error
        self.stop = threading.Event()

    def resolver(self, host, port, timeout):
        self.dns_calls.append(host)
        answers = self.dns[host]
        return list(answers[min(len(self.dns_calls_for(host)) - 1, len(answers) - 1)])

    def dns_calls_for(self, host):
        return [h for h in self.dns_calls if h == host]

    def connector(self, address, port, timeout):
        self.connects.append((address, port))
        if self.connect_error:
            raise self.connect_error
        handler = self.servers[(address, port)]
        if isinstance(handler, list):
            handler = handler.pop(0)
        client, server = socket.socketpair()
        counted = CountingSock(client)
        self.socks.append(counted)
        sent = {"n": 0}
        self.served.append(sent)

        def serve():
            try:
                server.settimeout(5)
                data = b""
                while b"\r\n\r\n" not in data:
                    part = server.recv(4096)
                    if not part:
                        return
                    data += part
                self.requests.append(data.decode("latin-1"))
                handler(_Counted(server, sent, self.stop), data)
            except OSError:
                pass
            finally:
                server.close()

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        self.threads.append(t)
        return counted

    def close(self):
        self.stop.set()
        for t in self.threads:
            t.join(5)


class _Counted:
    def __init__(self, sock, sent, stop):
        self._s, self._sent, self.stop = sock, sent, stop

    def sendall(self, data):
        self._s.sendall(data)
        self._sent["n"] += len(data)


@pytest.fixture
def net():
    n = FakeNet()
    yield n
    n.close()


def fetcher(net, tls=None, **kw):
    return cf.CalendarFetcher(resolver=net.resolver, connector=net.connector, tls_context=tls or FakeTLS(), **kw)


def get(net, url=f"https://{HOST}/feed.ics", tls=None, max_bytes=1_000_000, **kw):
    return fetcher(net, tls, **kw).fetch(url, max_bytes=max_bytes)


