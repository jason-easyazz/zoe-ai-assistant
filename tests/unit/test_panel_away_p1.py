"""Panel away P1, the panel side: the kiosk status gate, the launcher that starts on it, and the inbound firewall.

docs/architecture/panel-remote-access.md sections 4.2, 4.4 and 4.6; record: docs/knowledge/panel-away-p1.md. Everything here is
offline: the gate's probes and HTTP client are injected, the launcher runs against stub binaries, and the firewall is only
RENDERED (the installer refuses to touch a real ruleset without root). Addresses are TEST-NET / the shared tailnet range.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
import stat
import subprocess
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
TOUCH = ROOT / "scripts" / "setup" / "touchscreen"
FW = TOUCH / "firewall"
KIOSK_URL = "https://192.0.2.10/touch/home.html?panel_id=kitchen-panel&kiosk=1"
TAILNET_URL = "https://zoe-host.example-tailnet.ts.net/touch/home.html?panel_id=kitchen-panel&kiosk=1"


def _load_gate():
    spec = importlib.util.spec_from_file_location("zoe_kiosk_gate", TOUCH / "zoe-kiosk-gate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


gate_mod = _load_gate()


class FakeHttp:
    def __init__(self, health=200, ticket=(200, json.dumps(dict(ticket="T1", enter_path="/api/panels/kiosk-enter")).encode())):
        self.health, self.ticket, self.calls = health, ticket, []

    def __call__(self, url, *, method="GET", headers=None, verify=False, timeout=4.0):
        self.calls.append((method, url, dict(headers or dict())))
        if url.endswith("/health"):
            return self.health, b""
        return self.ticket


def _gate(url=KIOSK_URL, wifi="up", link="up", http=None, **env):
    environ = dict(ZOE_KIOSK_URL=url)
    environ.update(env)
    return gate_mod.Gate(environ, wifi=lambda: dict(state=wifi), link=lambda: dict(state=link), request=http or FakeHttp())


# ---- the status screen says what is wrong ----------------------------------------------------------------------------------


@pytest.mark.parametrize("wifi,link,health,reason", [
    ("up", "up", 200, "ready"),
    ("down", "down", 0, "wifi_down"),
    ("portal", "up", 0, "wifi_portal"),
    ("up", "login", 0, "link_login"),
    ("up", "down", 0, "link_down"),
    ("up", "up", 503, "zoe_down"),
])
def test_status_names_the_first_thing_that_is_wrong(wifi, link, health, reason):
    snap = _gate(TAILNET_URL, wifi=wifi, link=link, http=FakeHttp(health=health)).refresh()
    assert snap["reason"] == reason and snap["message"] == gate_mod.SENTENCES[reason]
    assert snap["ready"] is (reason == "ready")


def test_on_the_home_lan_the_link_light_is_not_used_and_never_probed():
    calls = []
    g = gate_mod.Gate(dict(ZOE_KIOSK_URL=KIOSK_URL), wifi=lambda: dict(state="up"),
                      link=lambda: calls.append(1) or dict(state="down"), request=FakeHttp(health=0))
    snap = g.refresh()
    assert snap["link"]["state"] == "not-used" and calls == [] and snap["reason"] == "zoe_down"


def test_tailnet_hosts_are_recognised():
    assert gate_mod.is_tailnet_host("zoe-host.example-tailnet.ts.net")
    assert gate_mod.is_tailnet_host("100.101.102.103")
    assert not gate_mod.is_tailnet_host("192.0.2.10") and not gate_mod.is_tailnet_host("zoe.local")


def test_the_retry_never_gives_up():
    g = _gate(http=FakeHttp(health=0))
    for _ in range(50):
        snap = g.refresh()
    assert snap["attempts"] == 50 and snap["ready"] is False   # no attempt cap, no exit: the page keeps trying


# ---- the hand-off ---------------------------------------------------------------------------------------------------------------


def test_without_the_credential_the_gate_opens_the_kiosk_url_exactly_as_today():
    http = FakeHttp()
    status, headers, _ = _gate(http=http, DEVICE_TOKEN="tok").handoff()
    assert (status, headers["Location"]) == (303, KIOSK_URL)
    assert not [c for c in http.calls if c[0] == "POST"]          # flag off: the token is never used


def test_with_the_credential_the_browser_gets_a_ticket_form_and_never_the_token():
    http = FakeHttp(ticket=(200, json.dumps(dict(ticket='T"><script>x</script>', enter_path="/api/panels/kiosk-enter")).encode()))
    status, headers, body = _gate(http=http, DEVICE_TOKEN="device-secret", ZOE_KIOSK_CREDENTIAL="1").handoff()
    page = body.decode()
    assert status == 200
    post = [c for c in http.calls if c[0] == "POST"]
    assert len(post) == 1 and post[0][1] == "https://192.0.2.10/api/panels/kitchen-panel/kiosk-ticket"
    assert post[0][2] == dict([("X-Device-Token", "device-secret")])
    assert 'action="https://192.0.2.10/api/panels/kiosk-enter"' in page
    assert 'name="next" value="/touch/home.html?panel_id=kitchen-panel&amp;kiosk=1"' in page
    assert "<script>x</script>" not in page and "T&quot;&gt;&lt;script&gt;" in page   # the ticket is escaped
    assert "device-secret" not in page                                                 # the token never reaches the browser
    assert headers["Content-Security-Policy"].endswith("form-action https://192.0.2.10")


@pytest.mark.parametrize("reply", [(404, b""), (403, b""), (0, b""), (200, b"not json"), (200, b"[1]")])
def test_a_refused_or_failed_ticket_falls_back_to_the_plain_kiosk(reply):
    status, headers, _ = _gate(http=FakeHttp(ticket=reply), DEVICE_TOKEN="t", ZOE_KIOSK_CREDENTIAL="1").handoff()
    assert (status, headers["Location"]) == (303, KIOSK_URL)


def test_the_kiosk_url_comes_from_the_kiosk_config(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(dict(url=KIOSK_URL, gate_url="http://127.0.0.1:7781/")))
    g = gate_mod.Gate(dict(ZOE_KIOSK_CONFIG=str(cfg)), wifi=lambda: dict(state="up"), link=lambda: dict(state="up"),
                      request=FakeHttp())
    assert (g.kiosk_url, g.origin, g.panel_id) == (KIOSK_URL, "https://192.0.2.10", "kitchen-panel")


# ---- the local server ----------------------------------------------------------------------------------------------------------


@pytest.fixture
def server():
    try:
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        probe.close()
    except OSError as exc:
        if os.environ.get("CI"):
            raise
        pytest.skip("no loopback in this sandbox: %s" % exc)
    http = FakeHttp(health=0)
    g = _gate(http=http)
    g.http = http
    srv = gate_mod.serve(g, 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield g, srv.server_address[1]
    srv.shutdown()
    srv.server_close()


def _get(port, path, host=None):
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path))
    if host:
        req.add_header("Host", host)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    try:
        with urllib.request.build_opener(NoRedirect).open(req, timeout=5) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), b""


def test_server_answers_only_as_itself(server):
    g, port = server
    status, headers, body = _get(port, "/")
    assert status == 200 and b"Connecting to Zoe" in body
    assert headers["Cache-Control"] == "no-store" and "default-src 'none'" in headers["Content-Security-Policy"]
    assert _get(port, "/", host="evil.example")[0] == 403              # DNS rebinding: refused
    assert _get(port, "/", host="127.0.0.1:%d" % (port + 1))[0] == 403
    assert _get(port, "/nope")[0] == 404


def test_server_go_waits_for_zoe_then_hands_off(server):
    g, port = server
    status, headers, _ = _get(port, "/go")
    assert (status, headers["Location"]) == (303, "/")              # Zoe down: back to the status page
    g.http.health = 200
    g.refresh()
    assert json.loads(_get(port, "/status")[2])["ready"] is True
    status, headers, _ = _get(port, "/go")
    assert (status, headers["Location"]) == (303, KIOSK_URL)


def test_server_go_never_hands_off_on_a_stale_ready(server):
    g, port = server
    g.http.health = 200
    g.refresh()
    g._snapshot["checked_at"] = 0                                   # "ready", but long ago...
    g.http.health = 0                                               # ...and Zoe has gone since
    status, headers, _ = _get(port, "/go")
    assert (status, headers["Location"]) == (303, "/")


# ---- the launcher ---------------------------------------------------------------------------------------------------------------


def _stub(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/sh\n" + body + "\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_launcher(tmp_path, config: dict):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls.log"
    for name in ("xset", "xrandr", "unclutter", "pkill", "sleep"):
        _stub(bin_dir, name, "exit 0")
    _stub(bin_dir, "ping", 'echo "ping $*" >> "%s"; exit 1' % log)
    _stub(bin_dir, "chromium-browser", 'echo "chromium $*" >> "%s"; exit 0' % log)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(config))
    env = dict(PATH="%s:%s" % (bin_dir, os.environ.get("PATH", "/usr/bin:/bin")), HOME=str(tmp_path),
               ZOE_KIOSK_CONFIG=str(cfg), XDG_RUNTIME_DIR=str(tmp_path))
    proc = subprocess.run(["bash", str(TOUCH / "start-kiosk.sh")], env=env, capture_output=True, text=True, timeout=120)
    return proc, (log.read_text() if log.exists() else "")


def test_with_a_gate_the_launcher_starts_the_browser_on_it_and_never_loops(tmp_path):
    gate_url = "http://127.0.0.1:9/"          # nothing listens: the bounded wait gives up and the browser still starts
    proc, calls = _run_launcher(tmp_path, dict(url=KIOSK_URL, gate_url=gate_url))
    assert proc.returncode == 0, proc.stderr
    assert "ping" not in calls                                    # no reachability loop
    chromium = [l for l in calls.splitlines() if l.startswith("chromium ")]
    assert len(chromium) == 1 and chromium[0].endswith(" " + gate_url)


def test_without_a_gate_the_legacy_launcher_is_unchanged(tmp_path):
    """Documents today's behaviour (the boot loop the gate replaces): ping, then exit 1 for systemd, no browser."""
    proc, calls = _run_launcher(tmp_path, dict(url=KIOSK_URL))
    assert proc.returncode == 1 and "unreachable after 60 attempts" in proc.stderr
    assert calls.count("ping ") == 60 and "chromium" not in calls


# ---- the firewall ---------------------------------------------------------------------------------------------------------------


INSTALLER = FW / "install_panel_firewall.sh"


def _render(tmp_path, subnet="192.0.2.0/24"):
    out = tmp_path / "ruleset.nft"
    proc = subprocess.run(["bash", str(INSTALLER), "--render-only", "--home-subnet", subnet, "--out", str(out)],
                          capture_output=True, text=True, timeout=60)
    return proc, (out.read_text() if out.exists() else "")


def _rules(text):
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]


def test_the_rendered_ruleset_admits_only_loopback_tailnet_home_and_replies(tmp_path):
    proc, text = _render(tmp_path)
    assert proc.returncode == 0, proc.stderr
    rules = _rules(text)
    assert "flush ruleset" not in text                              # never wipes tailscaled's or the distro's tables
    assert "type filter hook input priority filter; policy drop;" in rules
    for needed in ('iif "lo" accept', "ct state established,related accept", 'iifname "tailscale0" accept',
                   "ip saddr @home_v4 accept", "udp dport 41641 accept"):
        assert needed in rules, needed
    assert "elements = " + chr(123) + " 192.0.2.0/24 " + chr(125) in rules
    # nothing opens a panel service to the world: no bare dport accept for SSH, AirPlay, the voice daemon or the wake agent
    for port in ("22", "7000", "7777", "8765", "9222"):
        assert not [r for r in rules if ("dport " + port) in r and r.endswith("accept")], port
    assert "hook output" not in text and "hook forward" not in text  # outbound stays open


@pytest.mark.parametrize("subnet", ["100.100.0.0/16", "8.8.8.0/24", "10.0.0.0/8", "fd00::/64", "not-a-net"])
def test_the_installer_refuses_a_home_subnet_that_is_not_a_home_lan(tmp_path, subnet):
    proc, _ = _render(tmp_path, subnet)
    assert proc.returncode != 0


@pytest.mark.parametrize("mode", ["--dry-run", "--apply", "--confirm", "--rollback"])
def test_the_installer_never_touches_a_ruleset_without_root(mode):
    if os.geteuid() == 0:
        pytest.skip("running as root: this guard is about non-root callers")
    proc = subprocess.run(["bash", str(INSTALLER), mode, "--home-subnet", "192.0.2.0/24"], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode != 0 and "run as root" in proc.stderr


def test_the_rendered_ruleset_passes_nft_check(tmp_path):
    """``nft -c`` in an unprivileged user+net namespace. Some kernels (the Jetson's) lack the ct/counter expressions; those
    evaluation errors are tolerated ONLY on ct/counter lines - any syntax error fails."""
    nft = shutil.which("nft") or ("/usr/sbin/nft" if Path("/usr/sbin/nft").exists() else None)
    if not nft or not shutil.which("unshare"):
        pytest.skip("nft/unshare not installed here (the render tests above still ran)")
    _, text = _render(tmp_path)
    proc = subprocess.run(["unshare", "-rn", nft, "-c", "-f", str(tmp_path / "ruleset.nft")], capture_output=True,
                          text=True, timeout=60)
    if proc.returncode == 0:
        return
    if "Operation not permitted" in proc.stderr and "Error:" not in proc.stderr:
        pytest.skip("user namespaces are not available here")
    errors = [l for l in proc.stderr.splitlines() if "Error:" in l]
    assert errors and all("Could not process rule: No such file or directory" in e for e in errors), proc.stderr
    lines = text.splitlines()
    for err in errors:
        lineno = int(err.split(":")[1])
        assert lines[lineno - 1].strip().split()[0] in ("ct", "counter"), err
