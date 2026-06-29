"""Security helpers shared by Zoe's agent tools.

Two concerns live here, both raised in the 2026-06-28 security audit
(``.polly/audit-findings.md``, security lane):

1. **Shell-injection hardening** for the Zoe Agent ``bash`` tool. The old code
   checked a prefix allowlist and then ran the *string* through
   ``create_subprocess_shell``, so ``echo ok; curl http://evil`` ran both
   halves. We now parse the command into an argv list with :func:`shlex.split`
   and the caller executes it with ``create_subprocess_exec`` — i.e. **no
   shell** — so ``;`` ``|`` ``&`` ``$()`` backticks and redirects can never
   chain a second command; they become inert literal arguments to the single
   allowlisted binary. As defence in depth we also *reject* those
   metacharacters outright for non-code-bearing commands.

2. **SSRF hardening** for outbound fetches. :func:`assert_public_url` blocks
   targets that resolve to private / loopback / link-local / reserved /
   multicast addresses (this includes the cloud metadata endpoint
   ``169.254.169.254``), for the web research + browser fetch tools.
   :func:`assert_panel_host` is the narrower policy for display-panel hosts: it
   permits LAN (private) panel addresses and an explicit operator allowlist,
   but still blocks loopback / link-local / metadata / public targets.

The module is intentionally **stdlib-only** (no ``httpx`` / ``cloakbrowser``
imports) so it loads in the slim CI environment and is cheap to unit-test.
"""

from __future__ import annotations

import http.client
import ipaddress
import os
import shlex
import socket
from typing import Iterable, List
from urllib.parse import urlsplit


class CommandRejected(ValueError):
    """The bash command failed the allowlist / injection policy."""


class SSRFBlocked(ValueError):
    """An outbound target is disallowed by the SSRF policy."""


# ── Shell-injection guard ─────────────────────────────────────────────────────

# Characters that introduce shell command chaining / substitution / redirection.
# Present here only as a defence-in-depth *rejection* signal — the real safety
# guarantee is that callers exec the returned argv WITHOUT a shell.
_SHELL_METACHARS = frozenset(";&|`$<>\n\r")

# Prefixes whose tail is an opaque *code* argument that may legitimately contain
# metacharacters (e.g. ``python3 -c "import os; print(os.getcwd())"``). For
# these the code payload is exempt from the raw-metacharacter scan; argv
# execution (no shell) is what keeps them safe.
_CODE_BEARING_PREFIXES = ("python3 -c", "python -c")


def split_command(command: str) -> List[str]:
    """Parse a command line into an argv list.

    Raises :class:`CommandRejected` on unbalanced quotes or an empty command.
    """
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise CommandRejected(f"could not parse command: {exc}") from exc
    if not argv:
        raise CommandRejected("empty command")
    return argv


def check_bash_command(command: str, allowed_prefixes: Iterable[str]) -> List[str]:
    """Validate ``command`` against the allowlist + injection policy.

    Returns the argv list to execute. Callers MUST run it via
    ``asyncio.create_subprocess_exec(*argv, ...)`` (no shell) so that any shell
    metacharacters that slipped through stay inert literal arguments.

    Raises :class:`CommandRejected` if the command is not allowlisted, cannot be
    parsed, or contains shell metacharacters in a non-code position.
    """
    cmd = command.strip()
    if not cmd:
        raise CommandRejected("empty command")

    allowed = tuple(allowed_prefixes)
    if not any(cmd.startswith(pfx) for pfx in allowed):
        raise CommandRejected(f"'{cmd[:40]}' is not in the allowed command list")

    argv = split_command(cmd)

    if any(cmd.startswith(pfx) for pfx in _CODE_BEARING_PREFIXES):
        # Layout: <python> <-c> <code> [extra...]. The code arg (index 2) may
        # legitimately contain metacharacters, so scan only the tokens AFTER it.
        scan_tokens = argv[3:]
    else:
        scan_tokens = argv

    for tok in scan_tokens:
        bad = _SHELL_METACHARS.intersection(tok)
        if bad:
            raise CommandRejected(
                "shell metacharacter(s) "
                f"{''.join(sorted(bad))!r} are not allowed (command chaining blocked)"
            )
    return argv


# ── SSRF guard ────────────────────────────────────────────────────────────────


def _normalize(ip: "ipaddress._BaseAddress") -> "ipaddress._BaseAddress":
    """Collapse IPv4-mapped IPv6 (``::ffff:127.0.0.1``) to its IPv4 form so the
    classification flags below see the real address."""
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def resolve_ips(host: str) -> List["ipaddress._BaseAddress"]:
    """Resolve ``host`` (IP literal or DNS name) to normalized IP objects.

    Returns an empty list if the host is empty or cannot be resolved.
    """
    h = (host or "").strip().strip("[]")
    if not h:
        return []
    try:
        return [_normalize(ipaddress.ip_address(h))]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(h, None, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    ips: List["ipaddress._BaseAddress"] = []
    for info in infos:
        try:
            ips.append(_normalize(ipaddress.ip_address(info[4][0])))
        except ValueError:
            continue
    return ips


def _is_dangerous_ip(ip: "ipaddress._BaseAddress") -> bool:
    """Loopback / link-local (incl. cloud metadata 169.254.169.254) / multicast /
    reserved / unspecified — never a valid outbound target for any policy."""
    return (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def is_public_ip(ip: "ipaddress._BaseAddress") -> bool:
    """True only for globally-routable public addresses."""
    ip = _normalize(ip)
    return ip.is_global and not _is_dangerous_ip(ip) and not ip.is_private


def assert_public_url(url: str) -> str:
    """SSRF gate for outbound *web* fetches (research + browser tools).

    Allows only ``http(s)`` URLs whose host resolves entirely to public,
    globally-routable addresses. Rejects private / loopback / link-local /
    metadata / reserved / multicast targets and unresolvable hosts.

    Returns the URL unchanged on success; raises :class:`SSRFBlocked` otherwise.
    """
    parts = urlsplit((url or "").strip())
    if parts.scheme not in ("http", "https"):
        raise SSRFBlocked(f"scheme {parts.scheme or '(none)'!r} not allowed (http/https only)")
    host = parts.hostname
    if not host:
        raise SSRFBlocked("URL has no host")
    ips = resolve_ips(host)
    if not ips:
        raise SSRFBlocked(f"host {host!r} could not be resolved")
    for ip in ips:
        if not is_public_ip(ip):
            raise SSRFBlocked(f"host {host!r} resolves to non-public address {ip}")
    return url


def is_public_url(url: str) -> bool:
    """Boolean form of :func:`assert_public_url` (no exception)."""
    try:
        assert_public_url(url)
        return True
    except SSRFBlocked:
        return False


def panel_allowlist() -> "set | None":
    """Optional operator allowlist of panel hostnames (``ZOE_PANEL_ALLOWED_HOSTS``,
    comma-separated).

    Returns ``None`` when unset (no name restriction — any LAN host is judged on
    its address alone). When set it only *narrows* what is permitted; it can
    NEVER widen the policy: an allowlisted host must still resolve to a private
    LAN address (the danger/public checks in :func:`is_allowed_panel_host` always
    apply). It is therefore not an escape hatch for loopback / metadata / public.
    """
    raw = os.environ.get("ZOE_PANEL_ALLOWED_HOSTS", "")
    hosts = {h.strip().lower() for h in raw.split(",") if h.strip()}
    return hosts or None


def is_allowed_panel_host(host: str) -> bool:
    """Policy for display-panel hosts.

    Allowed ONLY when the host resolves entirely to a **private LAN** address
    (RFC1918 ``10/8`` ``172.16/12`` ``192.168/16``, or the IPv6 ULA/private
    equivalents). Blocked: loopback, link-local / cloud-metadata
    (``169.254.169.254``), multicast, reserved, and **public** addresses — a
    panel host is never on the public internet (exfiltration target) nor on
    loopback (localhost-only services).

    The danger/private check applies to EVERY host, including any listed in
    :func:`panel_allowlist`; membership only narrows, it never bypasses.
    """
    h = (host or "").strip()
    if not h:
        return False
    allow = panel_allowlist()
    if allow is not None and h.lower() not in allow:
        return False
    ips = resolve_ips(h)
    if not ips:
        return False
    for ip in ips:
        if _is_dangerous_ip(ip):
            return False
        if ip.is_global:  # panels are LAN-only; a public target is never valid
            return False
        if not ip.is_private:
            return False
    return True


def assert_panel_host(host: str) -> str:
    """Raise :class:`SSRFBlocked` unless ``host`` is an allowed local panel target."""
    if not is_allowed_panel_host(host):
        raise SSRFBlocked(f"panel host {host!r} is not an allowed local panel target")
    return host


def resolve_validate_pin(host: str) -> str:
    """Resolve ``host``, require EVERY resolved address to be public, and return
    one validated IP literal to connect to.

    This is the anti-rebinding primitive: the caller connects to the *returned
    IP*, so the address that was validated is exactly the address that is
    connected — there is no second, unchecked DNS lookup at connect time.
    Raises :class:`SSRFBlocked` if the host is unresolvable or any address is
    non-public.
    """
    ips = resolve_ips(host)
    if not ips:
        raise SSRFBlocked(f"host {host!r} could not be resolved")
    for ip in ips:
        if not is_public_ip(ip):
            raise SSRFBlocked(f"host {host!r} resolves to non-public address {ip}")
    return str(ips[0])


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTPConnection that resolves+validates the host at connect time and pins
    the socket to the validated IP (defeats DNS-rebinding TOCTOU)."""

    def connect(self):
        ip = resolve_validate_pin(self.host)
        self.sock = socket.create_connection(
            (ip, self.port), self.timeout, self.source_address
        )
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection variant of :class:`_PinnedHTTPConnection`. Connects to the
    validated IP while keeping the original hostname for the ``Host`` header and
    TLS SNI."""

    def connect(self):
        ip = resolve_validate_pin(self.host)
        sock = socket.create_connection(
            (ip, self.port), self.timeout, self.source_address
        )
        server_hostname = self.host
        if self._tunnel_host:
            self.sock = sock
            self._tunnel()
            server_hostname = self._tunnel_host
        self.sock = self._context.wrap_socket(sock, server_hostname=server_hostname)


def guarded_urlopen(url: str, *, timeout: float, headers: dict | None = None):
    """``urllib`` open with SSRF protection:

    * the initial URL and every redirect hop are validated with
      :func:`assert_public_url`; and
    * the actual TCP connection is pinned to the validated IP
      (:class:`_PinnedHTTPConnection` / ``HTTPS``), so a DNS-rebinding host that
      reads as public at validation time but flips to a private address at
      connect time is still blocked.

    Returns the response object for the caller to read. Raises
    :class:`SSRFBlocked` if any hop targets a non-public address.
    """
    from urllib.request import (
        Request,
        HTTPRedirectHandler,
        HTTPHandler,
        HTTPSHandler,
        build_opener,
    )

    assert_public_url(url)

    class _GuardedRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            assert_public_url(newurl)  # cheap pre-check; pinned connect is authoritative
            return super().redirect_request(req, fp, code, msg, hdrs, newurl)

    class _GuardedHTTPHandler(HTTPHandler):
        def http_open(self, req):
            return self.do_open(_PinnedHTTPConnection, req)

    class _GuardedHTTPSHandler(HTTPSHandler):
        def https_open(self, req):
            return self.do_open(
                _PinnedHTTPSConnection, req, context=self._context
            )

    opener = build_opener(_GuardedHTTPHandler, _GuardedHTTPSHandler, _GuardedRedirect)
    req = Request(url, headers=headers or {})
    return opener.open(req, timeout=timeout)


async def guard_browser_page(page) -> None:
    """Install an SSRF route guard on a Playwright ``page``.

    Registers a ``page.route('**/*', ...)`` handler that runs
    :func:`assert_public_url` on EVERY request — the top-level navigation and
    every redirect hop — and **aborts** the route before the browser connects if
    the target is private / loopback / link-local / cloud-metadata. This blocks a
    ``public -> 169.254.169.254`` redirect pre-connect rather than after the
    request has already been sent.

    Residual risk: the validation resolves the hostname, while Chromium resolves
    again at connect time, so a DNS-rebinding host is not fully pinned for the
    browser path (unlike :func:`guarded_urlopen`, which pins the IP). The
    per-hop pre-connect abort is the mitigation available for Playwright here.
    """

    async def _handler(route):
        request = route.request
        try:
            assert_public_url(request.url)
        except SSRFBlocked:
            await route.abort()
            return
        await route.continue_()

    await page.route("**/*", _handler)
