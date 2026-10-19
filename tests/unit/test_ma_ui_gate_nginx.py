"""Music Assistant's own web UI is behind a Zoe gate (nginx ``auth_request``) - text pins over the edge config and its one caller.

``/modules/music-assistant/`` (MA's UI + WebSocket) used to be proxied straight through, so anyone on the LAN had full Music
Assistant control. The behaviour is proven against a real nginx by services/zoe-data/tests/test_ma_ui_gate_nginx_live.py (opt-in:
needs docker) and the gate's decisions by services/zoe-data/tests/test_music_ma_ui_and_jukebox_lan.py; THIS file is the always-on
pin that nobody quietly removes the ``auth_request`` line, un-``internal``s the gate, or adds a second ungated route to MA.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "services" / "zoe-ui"
LOCATIONS = (UI / "nginx.d" / "locations.inc").read_text(encoding="utf-8")
CONF = (UI / "nginx.conf").read_text(encoding="utf-8")
MUSIC_HTML = (UI / "dist" / "music.html").read_text(encoding="utf-8")
MA_UPSTREAM = ":8095"


def _blocks(text: str) -> list[tuple[str, str]]:
    out = []
    for m in re.finditer(r"^location\s+([^{]+)\{", text, re.M):
        depth, i = 0, m.end() - 1
        while i < len(text):
            depth += (text[i] == "{") - (text[i] == "}")
            if depth == 0:
                break
            i += 1
        out.append((m.group(1).strip(), text[m.end():i]))
    return out


def _active(body: str) -> list[str]:
    return [l.strip() for l in body.splitlines() if l.strip() and not l.strip().startswith("#")]


def _ungated_ma_routes(text: str) -> list[str]:
    """Every location that proxies to Music Assistant without an auth_request."""
    return [head for head, body in _blocks(text)
            if MA_UPSTREAM in body and not any(re.fullmatch(r"auth_request\s+\S+;", l) for l in _active(body))]


def test_every_route_to_music_assistant_runs_the_gate_first():
    assert any(MA_UPSTREAM in body for _, body in _blocks(LOCATIONS)), "the MA proxy vanished - update this pin deliberately"
    assert _ungated_ma_routes(LOCATIONS) == []
    ma = dict(_blocks(LOCATIONS))["^~ /modules/music-assistant/"]
    assert re.search(r"^\s*auth_request\s+/_zoe/ma-gate;", ma, re.M)


def test_the_conf_itself_adds_no_second_way_to_music_assistant():
    assert MA_UPSTREAM not in CONF and "music-assistant" not in "\n".join(_active(CONF))


def test_the_gate_location_is_internal_forwards_credentials_but_not_the_body_and_pins_the_client_address():
    gate = dict(_blocks(LOCATIONS))["= /_zoe/ma-gate"]
    lines = _active(gate)
    assert "internal;" in lines                                         # a client asking for it directly gets 404
    assert "proxy_pass http://host.docker.internal:8000/api/music/ma-ui/gate;" in lines
    assert "proxy_pass_request_body off;" in lines and 'proxy_set_header Content-Length "";' in lines
    assert "proxy_set_header X-Real-IP $zoe_real_ip;" in lines         # a caller cannot forge a panel address (the TCP peer on the LAN)
    assert "include /etc/nginx/zoe/ingress-headers.inc;" in lines     # and cannot claim the relay ingress
    assert 'proxy_set_header X-Internal-Token "";' in lines and 'proxy_set_header X-Zoe-User-Id "";' in lines
    assert 'proxy_set_header Upgrade "";' in lines                      # the WebSocket handshake must not reach the gate as an Upgrade
    assert not any(l.startswith(("proxy_set_header Cookie", "proxy_hide_header")) for l in lines)  # the cookie must reach the gate


def test_negative_control_the_pin_catches_a_conf_that_dropped_the_auth_request():
    broken = re.sub(r"^\s*auth_request\s+/_zoe/ma-gate;\s*$", "", LOCATIONS, flags=re.M)
    assert broken != LOCATIONS
    assert _ungated_ma_routes(broken) == ["^~ /modules/music-assistant/"]


def test_music_html_trades_its_session_for_the_gate_cookie_before_it_opens_the_socket():
    assert "/api/music/ma-ui/session" in MUSIC_HTML
    connect = MUSIC_HTML[MUSIC_HTML.index("async function connectWS()"):]
    connect = connect[:connect.index("new WebSocket(MA_WS)")]
    assert "await maUiSession()" in connect, "the cookie must be set before the handshake"
    assert "setInterval(maUiSession" in MUSIC_HTML, "the cookie lasts 12 h; the open page must keep it fresh for the popup"
