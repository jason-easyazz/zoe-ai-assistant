#!/usr/bin/env python3
"""Host-header-rewriting proxy for the Serena code-intel bridge.

WHY THIS EXISTS (it replaced systemd-socket-proxyd, which could not do the job)
------------------------------------------------------------------------------
`zoe-omnigent` reaches the host's shared Serena (serena-mcp.service, bound
127.0.0.1:9121 and never anything else) through serena-bridge.socket on
172.28.0.1:9121 — the gateway of the one-member internal `zoe-codeintel`
network. The TCP path worked; every request still came back

    HTTP/1.1 421 Misdirected Request
    Invalid Host header

because Serena builds its `FastMCP(...)` (serena/mcp.py) WITHOUT passing
`transport_security=`, and the MCP SDK auto-enables DNS-rebinding protection
whenever the bind host is loopback (mcp/server/fastmcp/server.py, "Auto-enable
DNS rebinding protection for localhost"), with

    allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"]

`mcp/server/transport_security.py::_validate_host` then correctly rejects a
request whose `Host:` is `172.28.0.1:9121`. systemd-socket-proxyd is an L4 byte
shuffler and cannot touch headers, so the bridge needs one HTTP-aware hop.

The two obvious shortcuts are both wrong and are NOT to be reintroduced:
  * binding Serena non-loopback — breaks the loopback-only rule (this server
    can read the entire repo) AND turns the SDK's protection off entirely;
  * overriding `localhost` in the container's /etc/hosts — breaks
    container-local loopback for everything else in the container.

WHAT IT DOES
------------
Exactly one semantic change: the request's `Host:` header becomes the upstream
authority (`127.0.0.1:9121`). Request lines, every other header, and all bodies
are relayed byte-for-byte, and responses are relayed byte-for-byte.

MCP streamable-HTTP constraints that shape the implementation:
  * Responses are long-lived `text/event-stream`. Both directions are pumped by
    CONCURRENT tasks and every relayed chunk is written and drained
    immediately — a read-all-then-forward proxy would buffer an SSE stream to
    completion, i.e. forever.
  * Connections are KEPT ALIVE and carry many requests. So this parses request
    framing (Content-Length / chunked, including trailers) well enough to find
    where the next request head starts, and rewrites the Host header of EVERY
    request — not just the first. Rewriting only the first request would leave
    request #2 onward getting 421 on a reused connection.
  * Bodies are streamed as they arrive; nothing is accumulated except the
    request head (capped, see HEAD_LIMIT).

Socket activation, not self-binding: the listening socket is inherited from
systemd on fd 3 (sd_listen_fds). The SOCKET unit must keep owning the bind,
because `FreeBind=true` and — load-bearing — `IPAddressDeny=any` +
`IPAddressAllow=<omnigent's pinned IP>` live there. For a socket-activated
service it is the SOCKET unit's access list that covers the listening socket
(systemd.resource-control(5)). If this process bound for itself, that access
control would silently disappear. Hence: no --listen flag, and a refusal to
start without LISTEN_FDS.

Stdlib only (asyncio) on purpose: no new dependency on this box.

Install / operate: scripts/setup/systemd/README.md. Unit rationale:
scripts/setup/systemd/system/serena-bridge.socket.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import socket
import sys

# sd_listen_fds(3): systemd passes listening sockets starting at fd 3.
SD_LISTEN_FDS_START = 3

DEFAULT_UPSTREAM = "127.0.0.1:9121"

# Cap on a single request head. The unit runs under a small MemoryMax and this
# is the only thing the proxy accumulates, so it is a containment bound as much
# as a protocol one. MCP request heads are a few hundred bytes.
HEAD_LIMIT = 32768

# Relay chunk size. Only an upper bound: reads return as soon as ANY bytes are
# available, which is what keeps SSE incremental.
IO_CHUNK = 65536

DEFAULT_CONNECT_TIMEOUT = 5.0

_BAD_GATEWAY_BODY = b"serena bridge: upstream unreachable\n"
_BAD_GATEWAY = (
    b"HTTP/1.1 502 Bad Gateway\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"Content-Length: " + str(len(_BAD_GATEWAY_BODY)).encode() + b"\r\n"
    b"Connection: close\r\n"
    b"\r\n" + _BAD_GATEWAY_BODY
)

CHUNKED = "chunked"

log = logging.getLogger("serena-bridge-proxy")


class BadRequest(Exception):
    """The client sent something this proxy refuses to relay."""


# --------------------------------------------------------------------------
# Header handling
# --------------------------------------------------------------------------


def split_head(head: bytes) -> tuple[bytes, list[bytes]]:
    """Split a complete request head into its request line and header lines."""
    if not head.endswith(b"\r\n\r\n"):
        raise BadRequest("request head is not CRLF-terminated")
    lines = head[:-4].split(b"\r\n")
    return lines[0], lines[1:]


def rewrite_host(head: bytes, authority: bytes) -> bytes:
    """Return `head` with its Host header replaced by `authority`.

    Everything else — the request line, header order, header casing, spacing —
    is preserved byte-for-byte. A request with no Host header gets one (HTTP/1.1
    requires it and the MCP middleware rejects its absence); a request with more
    than one is refused rather than guessed at, because picking one of two Host
    headers is exactly how request-smuggling bugs are built.
    """
    request_line, header_lines = split_head(head)

    out: list[bytes] = []
    seen = 0
    for line in header_lines:
        if line[:1] in (b" ", b"\t"):
            # obs-fold (RFC 7230 3.2.4): deprecated, and a folded Host header
            # would slip past the check below.
            raise BadRequest("obsolete line-folded header")
        name, sep, _ = line.partition(b":")
        if not sep:
            raise BadRequest("header line without a colon")
        if not name or name != name.strip():
            raise BadRequest("malformed header name")
        if name.lower() == b"host":
            seen += 1
            if seen > 1:
                raise BadRequest("multiple Host headers")
            out.append(b"Host: " + authority)
        else:
            out.append(line)

    if seen == 0:
        out.insert(0, b"Host: " + authority)

    return b"\r\n".join([request_line, *out]) + b"\r\n\r\n"


def header_fields(header_lines: list[bytes]) -> dict[bytes, list[bytes]]:
    fields: dict[bytes, list[bytes]] = {}
    for line in header_lines:
        name, _, value = line.partition(b":")
        fields.setdefault(name.strip().lower(), []).append(value.strip())
    return fields


def body_framing(fields: dict[bytes, list[bytes]]) -> int | str:
    """How long the request body is: a byte count, or CHUNKED.

    Refuses the ambiguous combinations rather than preferring one framing over
    the other — that preference IS request smuggling.
    """
    te = fields.get(b"transfer-encoding")
    cl = fields.get(b"content-length")

    if te:
        if cl:
            raise BadRequest("both Transfer-Encoding and Content-Length")
        codings = b",".join(te).lower().split(b",")
        if codings[-1].strip() != b"chunked":
            raise BadRequest("unsupported Transfer-Encoding")
        return CHUNKED

    if cl:
        if len(set(cl)) != 1:
            raise BadRequest("conflicting Content-Length headers")
        try:
            length = int(cl[0])
        except ValueError as exc:
            raise BadRequest("malformed Content-Length") from exc
        if length < 0:
            raise BadRequest("negative Content-Length")
        return length

    return 0


# --------------------------------------------------------------------------
# The bridge
# --------------------------------------------------------------------------


class Bridge:
    """Relays one client connection to the upstream MCP server."""

    def __init__(
        self,
        upstream_host: str,
        upstream_port: int,
        *,
        authority: str | None = None,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
    ) -> None:
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        # What the rewritten Host header says. Defaults to the upstream
        # authority, which is what the MCP SDK's allowed_hosts accepts.
        self.authority = (authority or f"{upstream_host}:{upstream_port}").encode()
        self.connect_timeout = connect_timeout

    # -- public entry point (asyncio.start_server callback) -----------------

    async def handle(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        """Handle one accepted connection. Never raises: one bad connection
        must not take the service down."""
        peer = client_writer.get_extra_info("peername")
        upstream_writer: asyncio.StreamWriter | None = None
        try:
            head = await self._read_head(client_reader)
            if head is None:
                return  # client connected and went away without asking anything

            try:
                upstream_reader, upstream_writer = await asyncio.wait_for(
                    asyncio.open_connection(self.upstream_host, self.upstream_port, limit=HEAD_LIMIT),
                    self.connect_timeout,
                )
            except (OSError, asyncio.TimeoutError) as exc:
                log.warning("upstream %s unreachable: %s", self.authority.decode(), exc)
                client_writer.write(_BAD_GATEWAY)
                await client_writer.drain()
                return

            await self._relay(client_reader, client_writer, upstream_reader, upstream_writer, head)

        except BadRequest as exc:
            log.warning("refused request from %s: %s", peer, exc)
        except (OSError, asyncio.IncompleteReadError, asyncio.LimitOverrunError) as exc:
            log.info("connection from %s ended: %s", peer, exc)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - defensive; the service must survive
            log.exception("unexpected failure relaying connection from %s", peer)
        finally:
            _close(upstream_writer)
            _close(client_writer)

    # -- internals ----------------------------------------------------------

    async def _read_head(self, reader: asyncio.StreamReader) -> bytes | None:
        """Read one complete request head, or None if the peer closed cleanly."""
        try:
            return await reader.readuntil(b"\r\n\r\n")
        except asyncio.IncompleteReadError as exc:
            if exc.partial:
                raise BadRequest("connection closed mid-request-head") from exc
            return None
        except asyncio.LimitOverrunError as exc:
            raise BadRequest(f"request head exceeds {HEAD_LIMIT} bytes") from exc

    async def _relay(
        self,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        upstream_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
        first_head: bytes,
    ) -> None:
        """Pump both directions CONCURRENTLY until the exchange is over.

        Concurrency is not an optimisation here: an MCP streamable-HTTP response
        is an open-ended SSE stream, so the response direction must be relayed
        while the request direction is still open.
        """
        requests = asyncio.create_task(
            self._requests(client_reader, upstream_writer, first_head),
            name="serena-bridge-requests",
        )
        responses = asyncio.create_task(
            self._responses(upstream_reader, client_writer),
            name="serena-bridge-responses",
        )

        done, _ = await asyncio.wait({requests, responses}, return_when=asyncio.FIRST_EXCEPTION)

        if responses not in done:
            # The client half-closed (or sent something we refuse). If it merely
            # stopped talking, the response may still be streaming — let it
            # drain. If it was malformed, there is nothing left to relay.
            if requests.exception() is None:
                await asyncio.wait({responses})
            else:
                responses.cancel()

        for task in (requests, responses):
            if not task.done():
                task.cancel()

        results = await asyncio.gather(requests, responses, return_exceptions=True)
        for result in results:
            if isinstance(result, BadRequest):
                raise result
            if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                raise result

    async def _requests(
        self,
        client_reader: asyncio.StreamReader,
        upstream_writer: asyncio.StreamWriter,
        first_head: bytes,
    ) -> None:
        """Client -> upstream: rewrite Host on EVERY request, stream bodies."""
        head: bytes | None = first_head
        while head is not None:
            _, header_lines = split_head(head)
            upstream_writer.write(rewrite_host(head, self.authority))
            await upstream_writer.drain()
            await self._pump_body(client_reader, upstream_writer, body_framing(header_fields(header_lines)))
            head = await self._read_head(client_reader)

        # Client is done asking. Half-close so upstream sees EOF and can finish
        # its response instead of waiting for a request that will never come.
        if upstream_writer.can_write_eof():
            upstream_writer.write_eof()

    async def _pump_body(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        framing: int | str,
    ) -> None:
        if framing == CHUNKED:
            await self._pump_chunked(reader, writer)
            return
        remaining = int(framing)
        while remaining > 0:
            data = await reader.read(min(remaining, IO_CHUNK))
            if not data:
                raise BadRequest("connection closed mid-body")
            writer.write(data)
            await writer.drain()
            remaining -= len(data)

    async def _pump_chunked(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            try:
                line = await reader.readuntil(b"\r\n")
            except asyncio.IncompleteReadError as exc:
                raise BadRequest("connection closed mid-chunk-header") from exc
            except asyncio.LimitOverrunError as exc:
                raise BadRequest("oversized chunk header") from exc
            writer.write(line)
            await writer.drain()

            size_field = line[:-2].split(b";", 1)[0].strip()
            try:
                size = int(size_field, 16)
            except ValueError as exc:
                raise BadRequest("malformed chunk size") from exc
            if size < 0:
                raise BadRequest("negative chunk size")

            if size == 0:
                # Trailer section, terminated by a bare CRLF.
                while True:
                    try:
                        trailer = await reader.readuntil(b"\r\n")
                    except asyncio.IncompleteReadError as exc:
                        raise BadRequest("connection closed in trailers") from exc
                    writer.write(trailer)
                    await writer.drain()
                    if trailer == b"\r\n":
                        return

            remaining = size + 2  # chunk data plus its terminating CRLF
            while remaining > 0:
                data = await reader.read(min(remaining, IO_CHUNK))
                if not data:
                    raise BadRequest("connection closed mid-chunk")
                writer.write(data)
                await writer.drain()
                remaining -= len(data)

    async def _responses(self, upstream_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        """Upstream -> client: a pure byte pump, written through immediately.

        Do NOT be tempted to `await reader.read()` to EOF here and forward once:
        an MCP SSE response has no EOF until the session ends, so that turns
        every streamed reply into a hang.
        """
        while True:
            data = await upstream_reader.read(IO_CHUNK)
            if not data:
                break
            client_writer.write(data)
            await client_writer.drain()
        if client_writer.can_write_eof():
            client_writer.write_eof()


def _close(writer: asyncio.StreamWriter | None) -> None:
    if writer is None:
        return
    try:
        if not writer.is_closing():
            writer.close()
    except Exception:  # pragma: no cover - closing must never raise upward
        pass


# --------------------------------------------------------------------------
# Socket activation + entry point
# --------------------------------------------------------------------------


def inherited_sockets(env: dict[str, str] | None = None, pid: int | None = None) -> list[socket.socket]:
    """Return the listening sockets systemd passed in (sd_listen_fds).

    The socket unit owns the bind — with it, FreeBind and the IPAddressAllow
    list that is the bridge's ACTUAL access control. This proxy therefore never
    binds; it only ever adopts.
    """
    env = os.environ if env is None else env
    pid = os.getpid() if pid is None else pid

    listen_pid = env.get("LISTEN_PID")
    if listen_pid is not None and listen_pid.strip() and int(listen_pid) != pid:
        raise RuntimeError(f"LISTEN_PID={listen_pid} is not this process ({pid}); refusing to adopt another process's sockets")

    count = int(env.get("LISTEN_FDS", "0") or 0)
    socks: list[socket.socket] = []
    for offset in range(count):
        sock = socket.socket(fileno=SD_LISTEN_FDS_START + offset)
        sock.setblocking(False)
        socks.append(sock)

    # Do not leak the activation environment into anything we might spawn.
    for key in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        env.pop(key, None)

    return socks


def parse_upstream(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    if not sep or not host:
        raise ValueError(f"upstream must be host:port, got {value!r}")
    return host, int(port)


async def _amain(args: argparse.Namespace) -> int:
    socks = inherited_sockets()
    if not socks:
        log.error(
            "no socket passed by systemd (LISTEN_FDS unset). This proxy is "
            "socket-activated on purpose and never binds for itself — the "
            "socket unit owns FreeBind and the IPAddressAllow access list. "
            "Start serena-bridge.socket, not this service."
        )
        return 2

    host, port = parse_upstream(args.upstream)
    bridge = Bridge(host, port, connect_timeout=args.connect_timeout)

    servers = [await asyncio.start_server(bridge.handle, sock=sock, limit=HEAD_LIMIT) for sock in socks]
    log.info("serena bridge relaying %d inherited socket(s) to %s:%d", len(servers), host, port)

    async with contextlib.AsyncExitStack() as stack:
        for server in servers:
            await stack.enter_async_context(server)
        await asyncio.gather(*(server.serve_forever() for server in servers))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--upstream",
        default=DEFAULT_UPSTREAM,
        help=f"host:port of the shared Serena MCP server (default {DEFAULT_UPSTREAM})",
    )
    parser.add_argument("--connect-timeout", type=float, default=DEFAULT_CONNECT_TIMEOUT)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)s %(message)s", stream=sys.stderr)

    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:  # pragma: no cover
        return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
