"""Tests for the Serena code-intel bridge's Host-rewriting proxy.

THE BUG BEING PINNED: the bridge used to be `systemd-socket-proxyd`, an L4 byte
shuffler. The TCP path worked, but every request from the zoe-omnigent container
came back `421 Misdirected Request / Invalid Host header`, because Serena's
FastMCP inherits the MCP SDK's DNS-rebinding protection for loopback binds
(allowed_hosts = 127.0.0.1:* / localhost:* / [::1]:*) and the container's
requests carry `Host: 172.28.0.1:9121`. `scripts/maintenance/serena_bridge_proxy.py`
is the HTTP-aware replacement.

Four properties are load-bearing and each is asserted against a real socket, not
a mock:
  1. the Host header is rewritten to the upstream authority — on EVERY request
     on a kept-alive connection, not just the first;
  2. everything else is relayed byte-identically;
  3. a streamed (SSE) response is relayed INCREMENTALLY — a read-to-EOF proxy
     would hang forever on an MCP streamable-HTTP reply, so the test deadlocks
     (and times out) if buffering is reintroduced;
  4. an unreachable upstream, and a malformed request, are handled without the
     proxy dying.

No pytest-asyncio: each test drives its own `asyncio.run`, so this stays green
in the slim CI lane.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import socket
from pathlib import Path

import pytest

# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe

REPO_ROOT = Path(__file__).resolve().parents[2]
PROXY_PATH = REPO_ROOT / "scripts" / "maintenance" / "serena_bridge_proxy.py"


def _load_proxy():
    spec = importlib.util.spec_from_file_location("serena_bridge_proxy", PROXY_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


proxy = _load_proxy()

TIMEOUT = 10.0


# --------------------------------------------------------------------------
# Harness
# --------------------------------------------------------------------------


class StubUpstream:
    """A minimal HTTP server that records exactly what bytes it received."""

    def __init__(self, responder=None):
        self.requests: list[bytes] = []
        self.server: asyncio.AbstractServer | None = None
        self.port = 0
        self._responder = responder or self._default_responder

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                head = await reader.readuntil(b"\r\n\r\n")
                _, header_lines = proxy.split_head(head)
                fields = proxy.header_fields(header_lines)
                framing = proxy.body_framing(fields)
                body = b""
                if framing == proxy.CHUNKED:
                    while True:
                        line = await reader.readuntil(b"\r\n")
                        body += line
                        size = int(line[:-2].split(b";", 1)[0], 16)
                        if size == 0:
                            while True:
                                trailer = await reader.readuntil(b"\r\n")
                                body += trailer
                                if trailer == b"\r\n":
                                    break
                            break
                        body += await reader.readexactly(size + 2)
                elif framing:
                    body = await reader.readexactly(framing)
                self.requests.append(head + body)
                await self._responder(writer, head, body)
        except (asyncio.IncompleteReadError, ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            if not writer.is_closing():
                writer.close()

    @staticmethod
    async def _default_responder(writer: asyncio.StreamWriter, head: bytes, body: bytes) -> None:
        payload = b'{"ok":true}'
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
            + str(len(payload)).encode()
            + b"\r\n\r\n"
            + payload
        )
        await writer.drain()


async def _start_bridge(upstream_port: int, **kwargs):
    """Start the proxy handler on an ephemeral loopback port.

    In production the listening socket is inherited from systemd (see
    test_socket_activation_*); here we only exercise the connection handler.
    """
    bridge = proxy.Bridge("127.0.0.1", upstream_port, **kwargs)
    server = await asyncio.start_server(bridge.handle, "127.0.0.1", 0, limit=proxy.HEAD_LIMIT)
    return bridge, server, server.sockets[0].getsockname()[1]


async def _read_response_head(reader: asyncio.StreamReader) -> tuple[bytes, dict]:
    head = await reader.readuntil(b"\r\n\r\n")
    _, header_lines = proxy.split_head(head)
    return head, proxy.header_fields(header_lines)


def _request(host: str, path: str = "/mcp", body: bytes = b"", extra: bytes = b"") -> bytes:
    return (
        b"POST " + path.encode() + b" HTTP/1.1\r\n"
        b"Host: " + host.encode() + b"\r\n"
        b"Accept: application/json, text/event-stream\r\n"
        b"Content-Type: application/json\r\n"
        b"user-AGENT: probe/1.0\r\n"
        + extra
        + b"Content-Length: " + str(len(body)).encode() + b"\r\n"
        b"\r\n" + body
    )


# --------------------------------------------------------------------------
# 1. Host rewriting
# --------------------------------------------------------------------------


def test_host_header_is_rewritten_to_the_upstream_authority():
    """The whole point: the container's `Host: 172.28.0.1:9121` must reach
    Serena as `Host: 127.0.0.1:<port>`, or the MCP SDK answers 421."""

    async def scenario():
        upstream = StubUpstream()
        await upstream.start()
        _, server, port = await _start_bridge(upstream.port)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(_request("172.28.0.1:9121", body=b'{"jsonrpc":"2.0"}'))
            await writer.drain()
            head, _ = await _read_response_head(reader)
            assert head.startswith(b"HTTP/1.1 200")
            writer.close()
        finally:
            server.close()
            await server.wait_closed()
            await upstream.stop()
        return upstream.requests

    requests = asyncio.run(asyncio.wait_for(scenario(), TIMEOUT))
    assert len(requests) == 1
    seen = requests[0]
    assert b"Host: 172.28.0.1:9121\r\n" not in seen, "the bridge address reached Serena; it will answer 421"
    assert b"Host: 127.0.0.1:" in seen


def test_every_request_on_a_kept_alive_connection_is_rewritten():
    """MCP clients reuse connections. Rewriting only the first request head
    leaves request #2 onward getting 421 — the subtle half of this bug."""

    async def scenario():
        upstream = StubUpstream()
        await upstream.start()
        _, server, port = await _start_bridge(upstream.port)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            for i in range(3):
                writer.write(_request("172.28.0.1:9121", body=b'{"n":%d}' % i))
                await writer.drain()
                head, fields = await _read_response_head(reader)
                assert head.startswith(b"HTTP/1.1 200")
                await reader.readexactly(int(fields[b"content-length"][0]))
            writer.close()
        finally:
            server.close()
            await server.wait_closed()
            await upstream.stop()
        return upstream.requests

    requests = asyncio.run(asyncio.wait_for(scenario(), TIMEOUT))
    assert len(requests) == 3
    for i, seen in enumerate(requests):
        assert b"Host: 172.28.0.1:9121\r\n" not in seen, f"request #{i + 1} was not rewritten"
        assert b'{"n":%d}' % i in seen


# --------------------------------------------------------------------------
# 2. Everything else survives byte-identically
# --------------------------------------------------------------------------


def test_other_headers_and_body_survive_byte_identical():
    """Only the Host line may differ. Header order, casing, spacing and the
    body are relayed verbatim."""

    async def scenario():
        upstream = StubUpstream()
        await upstream.start()
        _, server, port = await _start_bridge(upstream.port)
        body = b'{"jsonrpc":"2.0","method":"initialize","params":{"unicode":"caf\xc3\xa9"}}'
        sent = _request("172.28.0.1:9121", body=body, extra=b"X-Odd_Header:   spaced   value  \r\nmcp-session-id: abc123\r\n")
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(sent)
            await writer.drain()
            await _read_response_head(reader)
            writer.close()
        finally:
            server.close()
            await server.wait_closed()
            await upstream.stop()
        return sent, body, upstream.requests[0]

    sent, body, seen = asyncio.run(asyncio.wait_for(scenario(), TIMEOUT))

    # The only difference is the Host line.
    def lines(raw: bytes) -> list[bytes]:
        head, _, _ = raw.partition(b"\r\n\r\n")
        return head.split(b"\r\n")

    sent_lines, seen_lines = lines(sent), lines(seen)
    assert len(sent_lines) == len(seen_lines)
    for a, b in zip(sent_lines, seen_lines):
        if a.lower().startswith(b"host:"):
            assert b.startswith(b"Host: 127.0.0.1:")
        else:
            assert a == b, f"header mutated: {a!r} -> {b!r}"

    assert seen.endswith(body), "request body was not relayed byte-identically"


def test_chunked_request_body_is_relayed_verbatim_including_trailers():
    async def scenario():
        upstream = StubUpstream()
        await upstream.start()
        _, server, port = await _start_bridge(upstream.port)
        chunked = (
            b"POST /mcp HTTP/1.1\r\n"
            b"Host: 172.28.0.1:9121\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"Trailer: X-Checksum\r\n"
            b"\r\n"
            b"5\r\nhello\r\n"
            b"6\r\n world\r\n"
            b"0\r\n"
            b"X-Checksum: deadbeef\r\n"
            b"\r\n"
        )
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(chunked)
            await writer.drain()
            head, _ = await _read_response_head(reader)
            assert head.startswith(b"HTTP/1.1 200")
            writer.close()
        finally:
            server.close()
            await server.wait_closed()
            await upstream.stop()
        return upstream.requests[0]

    seen = asyncio.run(asyncio.wait_for(scenario(), TIMEOUT))
    assert b"5\r\nhello\r\n6\r\n world\r\n0\r\nX-Checksum: deadbeef\r\n\r\n" in seen
    assert b"Transfer-Encoding: chunked\r\n" in seen
    assert b"Host: 127.0.0.1:" in seen


# --------------------------------------------------------------------------
# 3. Streaming (SSE) is relayed incrementally, not buffered
# --------------------------------------------------------------------------


def test_sse_response_is_relayed_incrementally_not_buffered():
    """MCP streamable-HTTP replies are open-ended `text/event-stream`.

    The stub sends event 1, then BLOCKS until the client confirms it arrived,
    then sends event 2 and only then ends the stream. A proxy that read the
    response to EOF before forwarding would never deliver event 1, the stub
    would never be released, and this test would time out. That deadlock IS the
    assertion.
    """
    got_first = asyncio.Event()

    async def responder(writer: asyncio.StreamWriter, head: bytes, body: bytes) -> None:
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: text/event-stream\r\n"
            b"Transfer-Encoding: chunked\r\n"
            b"\r\n"
        )
        await writer.drain()
        first = b"event: message\r\ndata: {\"id\":1}\r\n\r\n"
        writer.write(b"%x\r\n" % len(first) + first + b"\r\n")
        await writer.drain()

        # Not a sleep: the stream genuinely does not continue until the client
        # has the first event in hand.
        await got_first.wait()

        second = b"event: message\r\ndata: {\"id\":2}\r\n\r\n"
        writer.write(b"%x\r\n" % len(second) + second + b"\r\n")
        writer.write(b"0\r\n\r\n")
        await writer.drain()

    async def scenario():
        upstream = StubUpstream(responder=responder)
        await upstream.start()
        _, server, port = await _start_bridge(upstream.port)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(_request("172.28.0.1:9121", body=b"{}"))
            await writer.drain()

            head, fields = await _read_response_head(reader)
            assert head.startswith(b"HTTP/1.1 200")
            assert fields[b"content-type"] == [b"text/event-stream"]

            # First chunk must arrive while the upstream is still mid-stream.
            size_line = await reader.readuntil(b"\r\n")
            first = await reader.readexactly(int(size_line[:-2], 16) + 2)
            assert b'"id":1' in first
            got_first.set()

            size_line = await reader.readuntil(b"\r\n")
            second = await reader.readexactly(int(size_line[:-2], 16) + 2)
            assert b'"id":2' in second
            writer.close()
        finally:
            server.close()
            await server.wait_closed()
            await upstream.stop()

    asyncio.run(asyncio.wait_for(scenario(), TIMEOUT))


def test_response_head_arrives_before_the_body_is_complete():
    """A weaker but independent incrementality probe: the status line and
    headers must be readable while the body is still open."""
    release = asyncio.Event()

    async def responder(writer: asyncio.StreamWriter, head: bytes, body: bytes) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: 4\r\n\r\n")
        await writer.drain()
        await release.wait()
        writer.write(b"done")
        await writer.drain()

    async def scenario():
        upstream = StubUpstream(responder=responder)
        await upstream.start()
        _, server, port = await _start_bridge(upstream.port)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(_request("172.28.0.1:9121", body=b"{}"))
            await writer.drain()
            head, _ = await _read_response_head(reader)
            assert head.startswith(b"HTTP/1.1 200")
            release.set()
            assert await reader.readexactly(4) == b"done"
            writer.close()
        finally:
            server.close()
            await server.wait_closed()
            await upstream.stop()

    asyncio.run(asyncio.wait_for(scenario(), TIMEOUT))


# --------------------------------------------------------------------------
# 4. Failure handling — one bad connection must not kill the service
# --------------------------------------------------------------------------


def test_upstream_down_answers_502_and_the_proxy_keeps_serving():
    """Serena restarts (the reaper recycles it). The bridge must answer, stay
    up, and work again once upstream returns — on the SAME proxy server."""

    async def scenario():
        # Grab a port, then release it so nothing is listening there.
        probe = socket.socket()
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
        probe.close()

        _, server, port = await _start_bridge(dead_port, connect_timeout=2.0)
        try:
            statuses = []
            for _ in range(2):
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(_request("172.28.0.1:9121", body=b"{}"))
                await writer.drain()
                head, _ = await _read_response_head(reader)
                statuses.append(head.split(b"\r\n")[0])
                writer.close()

            # Upstream comes back on the same port; the proxy must recover.
            upstream = StubUpstream()
            upstream.server = await asyncio.start_server(upstream._handle, "127.0.0.1", dead_port)
            upstream.port = dead_port
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(_request("172.28.0.1:9121", body=b"{}"))
                await writer.drain()
                head, _ = await _read_response_head(reader)
                statuses.append(head.split(b"\r\n")[0])
                writer.close()
            finally:
                await upstream.stop()
            return statuses, upstream.requests
        finally:
            server.close()
            await server.wait_closed()

    statuses, requests = asyncio.run(asyncio.wait_for(scenario(), TIMEOUT))
    assert statuses[0].startswith(b"HTTP/1.1 502"), statuses
    assert statuses[1].startswith(b"HTTP/1.1 502"), "the proxy died after the first failed upstream"
    assert statuses[2].startswith(b"HTTP/1.1 200"), "the proxy did not recover when upstream returned"
    assert b"Host: 127.0.0.1:" in requests[0]


def test_malformed_request_is_refused_without_killing_the_proxy():
    async def scenario():
        upstream = StubUpstream()
        await upstream.start()
        _, server, port = await _start_bridge(upstream.port)
        try:
            # Two Host headers: refused rather than guessed at.
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(
                b"POST /mcp HTTP/1.1\r\nHost: 172.28.0.1:9121\r\nHost: evil\r\n"
                b"Content-Type: application/json\r\nContent-Length: 0\r\n\r\n"
            )
            await writer.drain()
            assert await reader.read() == b"", "a smuggling-shaped request was relayed"
            writer.close()

            # A client that vanishes mid-head must not take anything down.
            _, writer2 = await asyncio.open_connection("127.0.0.1", port)
            writer2.write(b"POST /mcp HTTP/1.1\r\nHost: 172.28.0.1:9121\r\n")
            await writer2.drain()
            writer2.close()

            # ... and the next well-formed request still works.
            reader3, writer3 = await asyncio.open_connection("127.0.0.1", port)
            writer3.write(_request("172.28.0.1:9121", body=b"{}"))
            await writer3.drain()
            head, _ = await _read_response_head(reader3)
            writer3.close()
            return head
        finally:
            server.close()
            await server.wait_closed()
            await upstream.stop()

    head = asyncio.run(asyncio.wait_for(scenario(), TIMEOUT))
    assert head.startswith(b"HTTP/1.1 200")


def test_client_disconnecting_mid_stream_does_not_kill_the_proxy():
    async def responder(writer: asyncio.StreamWriter, head: bytes, body: bytes) -> None:
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n")
        await writer.drain()
        for i in range(200):
            frame = b"data: %d\r\n\r\n" % i
            writer.write(b"%x\r\n" % len(frame) + frame + b"\r\n")
            try:
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                return
            await asyncio.sleep(0.001)

    async def scenario():
        upstream = StubUpstream(responder=responder)
        await upstream.start()
        _, server, port = await _start_bridge(upstream.port)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(_request("172.28.0.1:9121", body=b"{}"))
            await writer.drain()
            await _read_response_head(reader)
            writer.close()  # walk away mid-stream
            await asyncio.sleep(0.05)

            reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
            writer2.write(_request("172.28.0.1:9121", body=b"{}"))
            await writer2.drain()
            head, _ = await _read_response_head(reader2)
            writer2.close()
            return head
        finally:
            server.close()
            await server.wait_closed()
            await upstream.stop()

    head = asyncio.run(asyncio.wait_for(scenario(), TIMEOUT))
    assert head.startswith(b"HTTP/1.1 200")


# --------------------------------------------------------------------------
# Header-rewriting unit cases
# --------------------------------------------------------------------------


def test_rewrite_host_preserves_request_line_and_header_order():
    head = b"GET /mcp HTTP/1.1\r\nA: 1\r\nhost: 172.28.0.1:9121\r\nB: 2\r\n\r\n"
    out = proxy.rewrite_host(head, b"127.0.0.1:9121")
    assert out == b"GET /mcp HTTP/1.1\r\nA: 1\r\nHost: 127.0.0.1:9121\r\nB: 2\r\n\r\n"


def test_rewrite_host_inserts_a_missing_host_header():
    head = b"GET /mcp HTTP/1.1\r\nA: 1\r\n\r\n"
    out = proxy.rewrite_host(head, b"127.0.0.1:9121")
    assert out == b"GET /mcp HTTP/1.1\r\nHost: 127.0.0.1:9121\r\nA: 1\r\n\r\n"


@pytest.mark.parametrize(
    "head",
    [
        b"GET / HTTP/1.1\r\nHost: a\r\nHost: b\r\n\r\n",       # two Host headers
        b"GET / HTTP/1.1\r\nHost: a\r\n\tfolded\r\n\r\n",       # obs-fold
        b"GET / HTTP/1.1\r\nHost : a\r\n\r\n",                  # space before colon
        b"GET / HTTP/1.1\r\nnot-a-header\r\n\r\n",              # no colon
    ],
)
def test_rewrite_host_refuses_ambiguous_heads(head):
    with pytest.raises(proxy.BadRequest):
        proxy.rewrite_host(head, b"127.0.0.1:9121")


@pytest.mark.parametrize(
    "lines",
    [
        [b"Transfer-Encoding: chunked", b"Content-Length: 5"],  # smuggling pair
        [b"Content-Length: 5", b"Content-Length: 7"],           # conflicting
        [b"Content-Length: nope"],
        [b"Transfer-Encoding: gzip"],
    ],
)
def test_body_framing_refuses_ambiguous_framing(lines):
    with pytest.raises(proxy.BadRequest):
        proxy.body_framing(proxy.header_fields(lines))


# --------------------------------------------------------------------------
# Socket activation — the proxy must never bind for itself
# --------------------------------------------------------------------------


def test_socket_activation_adopts_the_inherited_fd():
    """The socket unit owns the bind, and with it FreeBind + the
    IPAddressAllow list that is the bridge's actual access control. If this
    process bound for itself that access control would silently vanish."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    expected = listener.getsockname()

    saved = os.dup(proxy.SD_LISTEN_FDS_START) if _fd_open(proxy.SD_LISTEN_FDS_START) else None
    try:
        os.dup2(listener.fileno(), proxy.SD_LISTEN_FDS_START)
        env = {"LISTEN_PID": str(os.getpid()), "LISTEN_FDS": "1"}
        socks = proxy.inherited_sockets(env=env, pid=os.getpid())
        assert len(socks) == 1
        assert socks[0].getsockname() == expected
        # The activation environment is consumed, not leaked to children.
        assert "LISTEN_FDS" not in env and "LISTEN_PID" not in env
        socks[0].detach()
    finally:
        os.close(proxy.SD_LISTEN_FDS_START)
        if saved is not None:
            os.dup2(saved, proxy.SD_LISTEN_FDS_START)
            os.close(saved)
        listener.close()


def _fd_open(fd: int) -> bool:
    try:
        os.fstat(fd)
        return True
    except OSError:
        return False


def test_inherited_sockets_refuses_another_processes_fds():
    with pytest.raises(RuntimeError):
        proxy.inherited_sockets(env={"LISTEN_PID": str(os.getpid() + 1), "LISTEN_FDS": "1"}, pid=os.getpid())


def test_main_refuses_to_run_without_socket_activation():
    """No --listen flag exists, and no LISTEN_FDS means no listening socket:
    the proxy must refuse rather than invent a bind."""
    env_backup = {k: os.environ.pop(k) for k in ("LISTEN_PID", "LISTEN_FDS") if k in os.environ}
    try:
        assert proxy.main(["--upstream", "127.0.0.1:9121"]) == 2
    finally:
        os.environ.update(env_backup)

    # There is no way to ask it to bind: no --listen option, and no bind() call.
    with pytest.raises(SystemExit):
        proxy.main(["--listen", "0.0.0.0:9121"])
    assert ".bind(" not in PROXY_PATH.read_text()


def test_default_upstream_is_the_loopback_shared_serena():
    assert proxy.DEFAULT_UPSTREAM == "127.0.0.1:9121"
    assert proxy.parse_upstream(proxy.DEFAULT_UPSTREAM) == ("127.0.0.1", 9121)
