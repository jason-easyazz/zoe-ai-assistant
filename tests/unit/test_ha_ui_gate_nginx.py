"""Home Assistant through nginx (``/ha/``) is behind a Zoe gate (``auth_request``) - text pins over the edge config.

``/ha/`` (HA's web UI, REST API, login flow and WebSocket) used to be proxied straight through. The gate's decisions are proven
by services/zoe-data/tests/test_ha_ui_gate.py and the wiring against a real nginx by
services/zoe-data/tests/test_ha_ui_gate_nginx_live.py (opt-in: docker). THIS file is the always-on pin that nobody quietly
removes the ``auth_request`` line, un-``internal``s the gate, or adds a second ungated route to Home Assistant.
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
HA_UPSTREAM = ":8123"


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


def _ungated_ha_routes(text: str) -> list[str]:
    """Every location that proxies to Home Assistant without an auth_request."""
    return [head for head, body in _blocks(text)
            if HA_UPSTREAM in "\n".join(_active(body)) and not any(re.fullmatch(r"auth_request\s+\S+;", l) for l in _active(body))]


def test_every_route_to_home_assistant_runs_the_gate_first():
    assert any(HA_UPSTREAM in body for _, body in _blocks(LOCATIONS)), "the HA proxy vanished - update this pin deliberately"
    assert _ungated_ha_routes(LOCATIONS) == []
    ha = _active(dict(_blocks(LOCATIONS))["^~ /ha/"])
    assert ha[0] == "auth_request /_zoe/ha-gate;"
    # HA's WebSocket (/ha/api/websocket) still upgrades behind the gate
    assert "proxy_set_header Upgrade $http_upgrade;" in ha and "proxy_set_header Connection $connection_upgrade;" in ha


def test_the_conf_itself_adds_no_second_way_to_home_assistant():
    assert HA_UPSTREAM not in CONF
    assert "/ha/" not in "\n".join(_active(CONF))


def test_the_gate_location_is_internal_forwards_credentials_but_not_the_body_and_pins_the_client_address():
    lines = _active(dict(_blocks(LOCATIONS))["= /_zoe/ha-gate"])
    assert "internal;" in lines
    assert "proxy_pass http://host.docker.internal:8000/api/ha-ui/gate;" in lines
    assert "proxy_pass_request_body off;" in lines and 'proxy_set_header Content-Length "";' in lines
    assert "proxy_set_header X-Real-IP $remote_addr;" in lines
    assert 'proxy_set_header X-Internal-Token "";' in lines and 'proxy_set_header X-Zoe-User-Id "";' in lines
    assert 'proxy_set_header Upgrade "";' in lines
    assert not any(l.startswith(("proxy_set_header Cookie", "proxy_hide_header")) for l in lines)


def test_negative_control_the_pin_catches_a_conf_that_dropped_the_auth_request():
    broken = re.sub(r"^\s*auth_request\s+/_zoe/ha-gate;\s*$", "", LOCATIONS, flags=re.M)
    assert broken != LOCATIONS
    assert _ungated_ha_routes(broken) == ["^~ /ha/"]


def test_negative_control_a_second_ungated_route_is_caught():
    extra = LOCATIONS + "\nlocation ^~ /hass/ {\n    proxy_pass http://host.docker.internal:8123;\n}\n"
    assert _ungated_ha_routes(extra) == ["^~ /hass/"]
