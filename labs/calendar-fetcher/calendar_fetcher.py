"""calendar_fetcher - the REAL fetcher behind ``calendar_feed``'s injected seam. Standalone, stdlib only, UNWIRED.

A member's calendar subscription address is a bearer credential: whoever holds it reads the calendar. By the owner's standing
security decision it is never readable by zoe-data, so this module is written to be HOSTED BY THE CREDENTIAL BROKER (zoe-vaultd)
once the box's privilege work (P0) lands. Until then it lives in this lab, nothing in zoe-data imports it, nothing stores a link,
and the only callers are its tests and the lab script (``lab_happy_path.py``). See ``docs/knowledge/connected-accounts.md``
("the calendar feed fetcher contract") for the contract this implements and what the broker integration still has to do.

What it guarantees (each is a test under ``tests/``):
  * https only. ``webcal://`` is read as https. ``http://`` is refused unless the CALLER passes ``lab_loopback_http=True`` (a
    Python argument, never an environment variable), and even then only to a loopback address.
  * the host is resolved HERE, once per hop, and EVERY resolved address must be public: loopback, private, CGNAT, link-local
    (cloud metadata), multicast, reserved, unspecified, documentation, benchmark and the known metadata addresses are refused,
    IPv4 and IPv6, including IPv4 embedded in v6 (v4-mapped, v4-compatible, 6to4, NAT64; Teredo is refused outright). Numeric
    host spellings a resolver might read as an address (``2130706433``, ``0x7f.1``) and internal names (``localhost``,
    ``*.internal``) are refused before any lookup.
  * the connection goes to the address that was checked (no second resolution, so DNS rebinding cannot switch it), with the
    original host in ``Host`` and in TLS SNI, and the certificate verified against that host.
  * at most ``MAX_REDIRECTS`` (3) redirects; every hop re-runs every check; https never downgrades to http.
  * the body is streamed and the fetch aborts once it would exceed ``max_bytes`` (a ``Content-Length`` over the cap is refused
    before the body is read); response headers are capped too.
  * every phase has a bound, and the whole fetch is bounded by ``TOTAL_TIMEOUT_S`` (30 s), shorter than the engine's 45 s.
  * validators: ``If-None-Match`` / ``If-Modified-Since`` are sent when given; a 304 is ``not_modified``.
  * the result is a small ``FeedResponse`` whose ``status`` is one of the engine's closed list (``STATUSES`` ==
    ``calendar_feed.FETCH_STATUSES``, pinned by a test) plus a fixed ``reason`` word for the broker's audit.
  * NO address, host, path, token or response text ever reaches an exception that leaves this module, a log line or a result
    field (other than the body and the sanitised validators). Logs carry the status, a fixed reason word, counts, and a
    12-hex-character host tag (a salted sha256 of the host name) at most. Nothing raises out of ``fetch`` for a network or
    provider problem.

Why the standard library and not httpx (which zoe-data already ships): the broker package is stdlib-only by contract
(``services/zoe-vaultd/AGENTS.md``, pinned by ``test_broker_is_stdlib_only``) so it stays reviewable whole; httpx has no public
hook to connect to a pre-checked address while keeping the original host for SNI and certificate checks (it would mean a
custom httpcore network backend, an internal surface); and httpx reads proxy settings from the environment by default
(``trust_env``), which would route around the address check. ``http.client`` never reads proxy variables, and its
``connect()`` is replaced here so it can never resolve or connect on its own.
"""
from __future__ import annotations

import hashlib
import http.client
import io
import ipaddress
import logging
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Callable, List, Optional, Sequence, Tuple
from urllib.parse import quote, urljoin, urlsplit

logger = logging.getLogger("calendar_fetcher")

#: the engine's closed status list (``calendar_feed.FETCH_STATUSES``); a test pins the two equal
STATUSES = ("ok", "not_modified", "unauthorized", "not_found", "rate_limited", "too_large", "network", "server_error",
            "blocked")

MAX_REDIRECTS = 3
DNS_TIMEOUT_S = 5.0          # one lookup
CONNECT_TIMEOUT_S = 5.0      # TCP connect plus the TLS handshake, per address tried
HEADER_TIMEOUT_S = 10.0      # request sent -> end of the response headers, per hop, however slowly they arrive
READ_TIMEOUT_S = 10.0        # the longest silence allowed while reading the body
TOTAL_TIMEOUT_S = 30.0       # the whole fetch, every hop and phase (the engine's own bound is 45 s)
MAX_HEADER_BYTES = 64 * 1024
MAX_ADDRESSES = 4            # resolved addresses tried per hop (all of them are checked)
CHUNK = 64 * 1024
MAX_VALIDATOR_LEN = 256
MAX_RETRY_AFTER_S = 24 * 3600
DEFAULT_PORTS = (443,)
USER_AGENT = "Zoe-calendar-fetcher/1"
_TAG_SALT = b"zoe-calendar-fetcher-host-tag-v1|"

_REDIRECTS = (301, 302, 303, 307, 308)
_VALIDATOR_RE = re.compile(r"^[\x21-\x7e][\x20-\x7e]*$")   # printable ASCII, no CR / LF / NUL; no leading space
_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TLD_RE = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")
#: names that are never a public calendar host, refused before any lookup (the address check would refuse most anyway)
_INTERNAL_SUFFIXES = (".localhost", ".local", ".internal", ".home.arpa", ".lan", ".intranet", ".corp", ".localdomain")
_INTERNAL_NAMES = {"localhost", "localhost.localdomain", "metadata", "instance-data"}


@dataclass(frozen=True)
class FeedResponse:
    """What a fetch returns. The first six fields are exactly ``calendar_feed.FetchResult``; ``reason`` is a fixed word for the
    broker's audit (never forwarded to zoe-data by ``engine_fetcher``)."""
    status: str
    body: bytes = b""
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    content_type: Optional[str] = None
    retry_after_s: Optional[int] = None
    reason: Optional[str] = None

    def __repr__(self) -> str:  # never print a body (provider text) in a traceback or a test failure
        return (f"FeedResponse(status={self.status!r}, reason={self.reason!r}, body_len={len(self.body)}, "
                f"etag_set={self.etag is not None}, last_modified_set={self.last_modified is not None}, "
                f"retry_after_s={self.retry_after_s!r})")


class _Stop(Exception):
    """Internal: end the fetch with ``status`` and a fixed ``reason`` word. Its args are ONLY those two fixed words."""

    def __init__(self, status: str, reason: str):
        super().__init__(status, reason)
        self.status = status
        self.reason = reason


# -- address policy -------------------------------------------------------------------------------------------------------------

def _nets(*cidrs: str) -> Tuple[Any, ...]:
    return tuple(ipaddress.ip_network(c) for c in cidrs)


#: explicit blocks, so the verdict does not depend on which Python's ``ipaddress`` tables are installed
_V4_BLOCKS = (
    ("unspecified", _nets("0.0.0.0/8")),
    ("loopback", _nets("127.0.0.0/8")),
    ("private", _nets("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")),
    ("cgnat", _nets("100.64.0.0/10")),
    ("link_local", _nets("169.254.0.0/16")),
    ("reserved", _nets("192.0.0.0/24", "192.88.99.0/24", "198.18.0.0/15", "240.0.0.0/4", "255.255.255.255/32")),
    ("documentation", _nets("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")),
    ("multicast", _nets("224.0.0.0/4")),
)
_V6_BLOCKS = (
    ("unspecified", _nets("::/128")),
    ("loopback", _nets("::1/128")),
    ("reserved", _nets("::/96", "100::/64", "2001::/23", "fec0::/10", "5f00::/16")),   # v4-compatible, discard, IETF/Teredo, site-local, SRv6
    ("documentation", _nets("2001:db8::/32", "3fff::/20")),
    ("private", _nets("fc00::/7", "64:ff9b:1::/48")),                                # ULA, local-use NAT64
    ("link_local", _nets("fe80::/10")),
    ("multicast", _nets("ff00::/8")),
)
#: cloud / platform metadata endpoints, named so the refusal reason says so (most also fall in a block above)
_METADATA = frozenset(ipaddress.ip_address(a) for a in (
    "169.254.169.254", "169.254.170.2", "169.254.169.123", "100.100.100.200", "192.0.0.192", "fd00:ec2::254"))
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIXTOFOUR = ipaddress.ip_network("2002::/16")


def classify_address(ip: Any) -> Optional[str]:
    """``None`` for a public unicast address, else the fixed word naming why it is refused. IPv4 carried inside IPv6
    (v4-mapped, NAT64, 6to4) is judged as the IPv4 address it carries."""
    if ip in _METADATA:
        return "metadata"
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.scope_id:
            return "link_local"
        inner = ip.ipv4_mapped
        if inner is None and ip in _NAT64:
            inner = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if inner is None and ip in _SIXTOFOUR:
            inner = ip.sixtofour
        if inner is not None:
            return classify_address(inner)
        blocks = _V6_BLOCKS
    else:
        blocks = _V4_BLOCKS
    for word, nets in blocks:
        if any(ip in n for n in nets):
            return word
    if not ip.is_global or ip.is_private or ip.is_multicast or ip.is_reserved or ip.is_loopback or ip.is_link_local:
        return "reserved"                      # anything else the standard library does not call global
    return None


def host_tag(host: str) -> str:
    """The most a log line may say about a host: 12 hex characters of a salted sha256 (host names are not the secret, but a
    raw one in a log is still a pointer at the member's provider)."""
    return hashlib.sha256(_TAG_SALT + host.encode("utf-8", "replace")).hexdigest()[:12]


def _check_hostname(host: str) -> Tuple[str, Optional[Any]]:
    """(normalised host, the address if the host IS an address literal). Raises ``_Stop`` for a host that is not a plain DNS
    name or address literal."""
    host = host.rstrip(".").lower()
    if not host:
        raise _Stop("blocked", "bad_url")
    try:
        return host, ipaddress.ip_address(host)
    except ValueError:
        pass
    try:
        ascii_host = host.encode("idna").decode("ascii")
    except (UnicodeError, ValueError):
        raise _Stop("blocked", "bad_host") from None
    labels = ascii_host.split(".")
    if len(ascii_host) > 253 or not all(_LABEL_RE.match(lb) for lb in labels):
        raise _Stop("blocked", "bad_host")
    if not _TLD_RE.match(labels[-1]):           # 2130706433, 0x7f000001, 127.1, 017700000001 ... never reach a resolver
        raise _Stop("blocked", "numeric_host")
    if len(labels) < 2:                         # a bare name is resolved through the search domains: never public
        raise _Stop("blocked", "bad_host")
    if ascii_host in _INTERNAL_NAMES or ascii_host.endswith(_INTERNAL_SUFFIXES):
        raise _Stop("blocked", "internal_name")
    return ascii_host, None


@dataclass(frozen=True)
class _Target:
    scheme: str
    host: str
    port: int
    path: str
    literal: Optional[Any]

    @property
    def url(self) -> str:
        h = f"[{self.host}]" if ":" in self.host else self.host
        return f"{self.scheme}://{h}:{self.port}{self.path}"


def _parse_target(url: str, *, previous_scheme: Optional[str], lab: bool, allowed_ports: Sequence[int]) -> _Target:
    if not isinstance(url, str) or not url or len(url) > 8192 or any(ord(c) < 0x21 or ord(c) == 0x7f for c in url):
        raise _Stop("blocked", "bad_url")
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme == "webcal":
            scheme = "https"
        port = parts.port
        raw_host = parts.hostname
        has_userinfo = parts.username is not None or parts.password is not None
    except ValueError:
        raise _Stop("blocked", "bad_url") from None
    if scheme == "http":
        if previous_scheme == "https":
            raise _Stop("blocked", "downgrade")
        if not lab:
            raise _Stop("blocked", "not_https")
    elif scheme != "https":
        raise _Stop("blocked", "scheme")
    if has_userinfo:
        raise _Stop("blocked", "userinfo")
    if not raw_host:
        raise _Stop("blocked", "bad_url")
    host, literal = _check_hostname(raw_host)
    port = port or (443 if scheme == "https" else 80)
    if not lab and port not in allowed_ports:
        raise _Stop("blocked", "port")
    path = "".join(c if ord(c) < 0x80 else quote(c, safe="") for c in (parts.path or "/"))
    if parts.query:
        path += "?" + "".join(c if ord(c) < 0x80 else quote(c, safe="") for c in parts.query)
    return _Target(scheme, host, port, path, literal)


# -- the network seam (injectable; the defaults are the real thing) -------------------------------------------------------------

Resolver = Callable[[str, int, float], List[str]]          # (host, port, timeout) -> address strings
Connector = Callable[[str, int, float], Any]              # (address, port, timeout) -> a connected socket


def system_resolver(host: str, port: int, timeout: float) -> List[str]:
    """``getaddrinfo`` bounded by ``timeout`` (it has no timeout of its own: it runs in a daemon thread that is abandoned at
    the bound; the C resolver's own retry limits end it). Raises ``_Stop`` with fixed words only."""
    box: dict = {}

    def run() -> None:
        try:
            box["infos"] = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
        except Exception:  # noqa: BLE001 - its text names the host; only the fact of failure is kept
            box["failed"] = True

    t = threading.Thread(target=run, name="calendar-fetch-dns", daemon=True)
    t.start()
    t.join(max(0.0, timeout))
    if t.is_alive():
        raise _Stop("network", "dns_timeout")
    if "infos" not in box:
        raise _Stop("network", "dns_failed")
    return [str(info[4][0]) for info in box["infos"]]


def system_connector(address: str, port: int, timeout: float) -> socket.socket:
    """A TCP connection to exactly ``address`` (already checked); never a name, so nothing resolves again."""
    ip = ipaddress.ip_address(address)
    family = socket.AF_INET6 if ip.version == 6 else socket.AF_INET
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        sock.settimeout(max(0.001, timeout))
        sock.connect((address, port))
    except BaseException:
        sock.close()
        raise
    return sock


def default_tls_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()          # CERT_REQUIRED + check_hostname
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return ctx


# -- deadline-bounded socket reading --------------------------------------------------------------------------------------------

class _Clock:
    """The fetch's bounds. ``limit()`` = seconds the next read may wait: the per-read silence, cut to the phase deadline and the
    total deadline. Raises ``_Stop`` once a deadline has passed."""

    def __init__(self, total: float):
        self.total_deadline = time.monotonic() + total
        self.phase_deadline = self.total_deadline
        self.silence = total
        self.header_cap: Optional[int] = None    # bytes allowed while reading headers
        self.header_seen = 0
        self.received = 0

    def remaining(self) -> float:
        return self.total_deadline - time.monotonic()

    def bounded(self, phase: float) -> float:
        left = min(phase, self.remaining())
        if left <= 0:
            raise _Stop("network", "timeout")
        return left

    def phase(self, *, deadline_s: float, silence_s: float, header_cap: Optional[int]) -> None:
        self.phase_deadline = min(self.total_deadline, time.monotonic() + deadline_s)
        self.silence = silence_s
        self.header_cap = header_cap
        self.header_seen = 0

    def limit(self) -> float:
        left = min(self.silence, self.phase_deadline - time.monotonic())
        if left <= 0:
            raise _Stop("network", "timeout")
        return left


class _BoundedRaw(io.RawIOBase):
    """The raw stream ``http.client`` reads from: every ``recv`` waits at most ``clock.limit()``."""

    def __init__(self, sock: Any, clock: _Clock):
        super().__init__()
        self._sock = sock
        self._clock = clock

    def readable(self) -> bool:
        return True

    def readinto(self, b) -> int:
        c = self._clock
        want = len(b)
        if c.header_cap is not None:
            room = c.header_cap - c.header_seen
            if room <= 0:
                raise _Stop("server_error", "headers_too_large")
            want = min(want, room)
        self._sock.settimeout(c.limit())
        try:
            n = self._sock.recv_into(memoryview(b)[:want])
        except socket.timeout:
            raise _Stop("network", "timeout") from None
        c.received += n
        if c.header_cap is not None:
            c.header_seen += n
        return n


class _BoundedSock:
    """What ``http.client`` holds as ``self.sock``: sends go to the real socket, reads go through ``_BoundedRaw``."""

    def __init__(self, sock: Any, clock: _Clock):
        self._sock = sock
        self._clock = clock

    def makefile(self, mode: str = "rb", *args: Any, **kwargs: Any) -> io.BufferedReader:
        return io.BufferedReader(_BoundedRaw(self._sock, self._clock), 8192)

    def sendall(self, data: bytes) -> None:
        self._sock.settimeout(self._clock.bounded(self._clock.silence))
        try:
            self._sock.sendall(data)
        except socket.timeout:
            raise _Stop("network", "timeout") from None

    def close(self) -> None:
        """A no-op: ``http.client`` closes ``conn.sock`` as soon as it sees ``Connection: close``, BEFORE the body is read
        (it "passes the connection to the response"). This module closes the socket itself with ``discard()``."""

    def discard(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class _PinnedConnection(http.client.HTTPConnection):
    """An ``HTTPConnection`` over a socket this module already connected to a checked address. ``connect()`` is disabled, so
    http.client can never resolve or connect on its own (a reconnect would be a second, unchecked resolution)."""

    def __init__(self, target: _Target, sock: _BoundedSock):
        super().__init__(target.host, target.port, timeout=None)
        self.default_port = 443 if target.scheme == "https" else 80   # Host header without a default port, as a browser sends
        self.sock = sock
        self.auto_open = 0

    def connect(self) -> None:  # pragma: no cover - only reached on a bug
        raise _Stop("network", "reconnect_refused")


# -- the fetcher ----------------------------------------------------------------------------------------------------------------

def _clean_validator(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    if not value or len(value) > MAX_VALIDATOR_LEN or not _VALIDATOR_RE.match(value):
        return None
    return value


def _retry_after(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return min(int(value), MAX_RETRY_AFTER_S)
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None or when.tzinfo is None:
        return None
    return max(0, min(int(when.timestamp() - time.time()), MAX_RETRY_AFTER_S))


def _status_for(code: int) -> Tuple[str, str]:
    if code in (401, 403):
        return "unauthorized", f"http_{code}"
    if code in (404, 410):
        return "not_found", f"http_{code}"
    if code == 429:
        return "rate_limited", "http_429"
    if 500 <= code <= 599:
        return "server_error", "http_5xx"
    return "server_error", "http_other"         # other 4xx / 1xx / unknown: the provider would not serve it


class CalendarFetcher:
    """Fetch one calendar feed under the policy in the module docstring. Thread-safe: no shared mutable state.

    ``resolver`` / ``connector`` / ``tls_context`` are the network seam (tests inject fakes; the defaults are the real
    system calls). ``lab_loopback_http`` is the ONLY relaxation and exists for the lab script: it allows ``http://`` and any
    port, to LOOPBACK addresses only; every other refusal still applies. It is a constructor argument on purpose: nothing
    in the environment can switch it on."""

    def __init__(self, *, resolver: Optional[Resolver] = None, connector: Optional[Connector] = None,
                 tls_context: Optional[Any] = None, lab_loopback_http: bool = False,
                 allowed_ports: Sequence[int] = DEFAULT_PORTS, max_redirects: int = MAX_REDIRECTS,
                 dns_timeout_s: float = DNS_TIMEOUT_S, connect_timeout_s: float = CONNECT_TIMEOUT_S,
                 header_timeout_s: float = HEADER_TIMEOUT_S, read_timeout_s: float = READ_TIMEOUT_S,
                 total_timeout_s: float = TOTAL_TIMEOUT_S):
        if total_timeout_s > TOTAL_TIMEOUT_S or max_redirects > MAX_REDIRECTS:
            raise ValueError("calendar_fetcher: bounds can only be tightened")
        self._resolve = resolver or system_resolver
        self._connect = connector or system_connector
        self._tls = tls_context if tls_context is not None else default_tls_context()
        self._lab = bool(lab_loopback_http)
        self._ports = tuple(allowed_ports)
        self._max_redirects = max_redirects
        self._dns_t, self._connect_t = dns_timeout_s, connect_timeout_s
        self._header_t, self._read_t, self._total_t = header_timeout_s, read_timeout_s, total_timeout_s

    def fetch(self, url: str, *, max_bytes: int, etag: Optional[str] = None,
              last_modified: Optional[str] = None) -> FeedResponse:
        """Never raises for a network, provider or policy problem: the answer is a ``FeedResponse``."""
        started = time.monotonic()
        clock = _Clock(self._total_t)
        hops = 0
        tag = "-"
        try:
            first = _parse_target(url, previous_scheme=None, lab=self._lab, allowed_ports=self._ports)
            tag = host_tag(first.host)
            res = self._run(first, clock, int(max_bytes), _clean_validator(etag), _clean_validator(last_modified))
            hops = res[1]
            out = res[0]
        except _Stop as stop:
            out = FeedResponse(stop.status, reason=stop.reason)
        except Exception as exc:  # noqa: BLE001 - its text may name the host or path; only the type name is kept
            logger.warning("CALENDAR_FETCH unexpected %s", type(exc).__name__)
            out = FeedResponse("network", reason="unexpected")
        logger.info("CALENDAR_FETCH status=%s reason=%s hops=%d bytes=%d received=%d ms=%d host=%s", out.status, out.reason,
                    hops, len(out.body), clock.received, int((time.monotonic() - started) * 1000), tag)
        return out

    # one hop at a time; ``_Stop`` ends the fetch from anywhere below
    def _run(self, target: _Target, clock: _Clock, max_bytes: int, etag: Optional[str],
             last_modified: Optional[str]) -> Tuple[FeedResponse, int]:
        redirects = 0
        while True:
            bsock, resp = self._request(target, clock, etag, last_modified)
            try:
                code = resp.status
                if code in _REDIRECTS:
                    location = resp.getheader("Location")
                    if not location:
                        raise _Stop("server_error", "redirect_without_location")
                    if redirects >= self._max_redirects:
                        raise _Stop("server_error", "too_many_redirects")
                    redirects += 1
                    nxt = urljoin(target.url, location.strip())
                    target = _parse_target(nxt, previous_scheme=target.scheme, lab=self._lab, allowed_ports=self._ports)
                    continue
                if code == 304:
                    return FeedResponse("not_modified", etag=_clean_validator(resp.getheader("ETag")) or etag,
                                        last_modified=_clean_validator(resp.getheader("Last-Modified")) or last_modified,
                                        reason="http_304"), redirects + 1
                if code != 200:
                    status, reason = _status_for(code)
                    retry = _retry_after(resp.getheader("Retry-After")) if status in ("rate_limited", "server_error") else None
                    return FeedResponse(status, retry_after_s=retry, reason=reason), redirects + 1
                body = self._read_body(resp, clock, max_bytes)
                return FeedResponse("ok", body=body, etag=_clean_validator(resp.getheader("ETag")),
                                    last_modified=_clean_validator(resp.getheader("Last-Modified")),
                                    content_type=_clean_validator(resp.getheader("Content-Type")),
                                    reason="http_200"), redirects + 1
            finally:
                try:
                    resp.close()
                except Exception:  # noqa: BLE001 - closing never decides the answer
                    pass
                bsock.discard()

    def _addresses(self, target: _Target, clock: _Clock) -> List[Any]:
        if target.literal is not None:
            found = [target.literal]
        else:
            raw = self._resolve(target.host, target.port, clock.bounded(self._dns_t))
            found = []
            for item in raw:
                try:
                    found.append(ipaddress.ip_address(str(item)))
                except ValueError:
                    raise _Stop("blocked", "bad_address") from None
        if not found:
            raise _Stop("network", "dns_empty")
        for ip in found:                         # EVERY answer must pass, not just the one we would use
            why = classify_address(ip)
            if why is None:
                if target.scheme == "http":      # lab only (parse refused it otherwise): http goes to loopback, nowhere else
                    raise _Stop("blocked", "http_not_loopback")
                if target.port not in self._ports:   # lab only: an odd port is for loopback, nowhere else
                    raise _Stop("blocked", "port")
                continue
            if why == "loopback" and self._lab:
                continue
            raise _Stop("blocked", why)
        unique: List[Any] = []
        for ip in found:
            if ip not in unique:
                unique.append(ip)
        return unique[:MAX_ADDRESSES]

    def _open(self, target: _Target, clock: _Clock) -> Any:
        for ip in self._addresses(target, clock):
            try:
                sock = self._connect(str(ip), target.port, clock.bounded(self._connect_t))
            except _Stop:
                raise
            except Exception:  # noqa: BLE001 - refused / unreachable / timed out: try the next checked address
                continue
            if target.scheme != "https":
                return sock
            try:
                sock.settimeout(clock.bounded(self._connect_t))
                return self._tls.wrap_socket(sock, server_hostname=target.host)
            except _Stop:
                sock.close()
                raise
            except Exception:  # noqa: BLE001 - a TLS failure (bad certificate, wrong host, timeout): its text names the host
                sock.close()
                raise _Stop("network", "tls_failed") from None
        raise _Stop("network", "connect_failed")

    def _request(self, target: _Target, clock: _Clock, etag: Optional[str],
                 last_modified: Optional[str]) -> Tuple[_BoundedSock, http.client.HTTPResponse]:
        bsock = _BoundedSock(self._open(target, clock), clock)
        conn = _PinnedConnection(target, bsock)
        try:
            clock.phase(deadline_s=self._header_t, silence_s=self._header_t, header_cap=MAX_HEADER_BYTES)
            conn.putrequest("GET", target.path, skip_accept_encoding=True)
            conn.putheader("User-Agent", USER_AGENT)
            conn.putheader("Accept", "text/calendar, */*;q=0.1")
            conn.putheader("Accept-Encoding", "identity")      # no compressed body, so no decompression bomb
            conn.putheader("Connection", "close")
            if etag:
                conn.putheader("If-None-Match", etag)
            if last_modified:
                conn.putheader("If-Modified-Since", last_modified)
            conn.endheaders()
            resp = conn.getresponse()
        except _Stop:
            bsock.discard()
            raise
        except Exception:  # noqa: BLE001 - malformed status line / headers, reset: never its text
            bsock.discard()
            raise _Stop("network", "bad_response") from None
        clock.phase(deadline_s=max(0.001, clock.remaining()), silence_s=self._read_t, header_cap=None)
        return bsock, resp

    def _read_body(self, resp: http.client.HTTPResponse, clock: _Clock, max_bytes: int) -> bytes:
        declared = resp.getheader("Content-Length")
        if declared and declared.strip().isdigit() and int(declared.strip()) > max_bytes:
            raise _Stop("too_large", "declared_too_large")
        buf = bytearray()
        try:
            while True:
                chunk = resp.read(min(CHUNK, max_bytes + 1 - len(buf)))
                if not chunk:
                    break
                buf += chunk
                if len(buf) > max_bytes:
                    raise _Stop("too_large", "body_too_large")
        except _Stop:
            raise
        except Exception:  # noqa: BLE001 - truncated / malformed chunking / reset
            raise _Stop("network", "body_failed") from None
        return bytes(buf)


def engine_fetcher(lookup: Callable[[str], Optional[str]], result_type: Any,
                   fetcher: Optional[CalendarFetcher] = None) -> Callable[[Any], Any]:
    """Adapt to ``calendar_feed``'s seam: ``Fetcher(FetchRequest) -> FetchResult``. ``lookup(account_id)`` returns the
    account's subscription address (in the broker: the decrypted secret; in the lab: a dict of a synthetic loopback URL). A
    missing link is ``unauthorized`` (the member reconnects). ``reason`` is dropped: zoe-data gets the status class only."""
    f = fetcher or CalendarFetcher()

    def fetch(req: Any) -> Any:
        url = lookup(req.account_id)
        if not url:
            r = FeedResponse("unauthorized", reason="no_link")
        else:
            r = f.fetch(url, max_bytes=req.max_bytes, etag=req.etag, last_modified=req.last_modified)
        return result_type(status=r.status, body=r.body, etag=r.etag, last_modified=r.last_modified,
                           content_type=r.content_type, retry_after_s=r.retry_after_s)

    return fetch
