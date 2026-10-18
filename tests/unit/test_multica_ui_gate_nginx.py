"""The Multica board is behind Zoe's sign-in (nginx ``auth_request``) - text pins over the edge config and its launcher page.

Upstream Multica has no generic OIDC login, so ``/multica/``, ``/multica-api/`` and ``/_next/`` run an ``auth_request`` to
zoe-data's ``/api/multica-ui/gate`` and only a signed-in adult household member reaches the board at all. The gate's decisions
are pinned by services/zoe-data/tests/test_multica_ui_gate.py and the whole chain is proven against the real Multica images by
labs/multica-signin-gate/; THIS file is the always-on pin that nobody quietly removes an ``auth_request`` line, un-``internal``s
the gate, or adds a second, ungated route to the board.
"""
from __future__ import annotations

import re
import subprocess
import shutil
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "services" / "zoe-ui"
LOCATIONS = (UI / "nginx.d" / "locations.inc").read_text(encoding="utf-8")
CONF = (UI / "nginx.conf").read_text(encoding="utf-8")
BOARD_HTML = (UI / "dist" / "board.html").read_text(encoding="utf-8")
GATE = "/_zoe/multica-gate"
BOARD_PREFIXES = ("^~ /multica/", "^~ /multica-api/", "^~ /_next/")
# How a location can reach Multica: the host-gateway ports today, the container names after the loopback move (#2130).
_MULTICA_UPSTREAM = re.compile(r"proxy_pass\s+https?://[^/\s;]*(?::3000|:8080|multica)", re.I)  # the HOST part only


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


def _reaches_multica(body: str) -> bool:
    return any(_MULTICA_UPSTREAM.search(l) for l in _active(body))


def _ungated_board_routes(text: str) -> list[str]:
    """Every location that proxies to Multica (or owns a board prefix) without the board gate."""
    return [head for head, body in _blocks(text)
            if (head in BOARD_PREFIXES or _reaches_multica(body))
            and f"auth_request {GATE};" not in [re.sub(r"\s+", " ", l) for l in _active(body)]]


def test_every_route_to_the_board_runs_the_gate_first():
    heads = dict(_blocks(LOCATIONS))
    assert all(p in heads for p in BOARD_PREFIXES), "a board prefix vanished - update this pin deliberately"
    assert sum(_reaches_multica(b) for _, b in _blocks(LOCATIONS)) >= 3
    assert _ungated_board_routes(LOCATIONS) == []


def test_the_conf_itself_adds_no_second_way_to_the_board():
    active = "\n".join(_active(CONF))
    assert "multica" not in active.lower() and ":3000" not in active and ":8080" not in active


def test_the_gate_location_is_internal_forwards_credentials_but_not_the_body():
    gate = dict(_blocks(LOCATIONS))[f"= {GATE}"]
    lines = _active(gate)
    assert "internal;" in lines                                         # a client asking for it directly gets 404
    assert "proxy_pass http://host.docker.internal:8000/api/multica-ui/gate;" in lines
    assert "proxy_pass_request_body off;" in lines and 'proxy_set_header Content-Length "";' in lines
    assert "proxy_set_header X-Real-IP $remote_addr;" in lines
    assert 'proxy_set_header X-Internal-Token "";' in lines and 'proxy_set_header X-Zoe-User-Id "";' in lines
    assert 'proxy_set_header Upgrade "";' in lines                      # the WebSocket handshake must not reach the gate as one
    assert not any(l.startswith(("proxy_set_header Cookie", "proxy_set_header X-Session-ID", "proxy_hide_header"))
                   for l in lines)                                      # the cookie / session id must reach the gate


def test_only_the_board_page_redirects_to_the_launcher():
    """A refused document goes to /board.html; an API call or an asset gets the bare 401/403 (a 302 to HTML would confuse
    fetch and the WebSocket client)."""
    heads = dict(_blocks(LOCATIONS))
    page = [re.sub(r"\s+", " ", l) for l in _active(heads["^~ /multica/"])]
    assert "error_page 401 403 = @multica_signin;" in page
    for p in ("^~ /multica-api/", "^~ /_next/"):
        assert not any(l.startswith("error_page") for l in _active(heads[p])), p
    signin = _active(heads["@multica_signin"])
    assert signin == ["return 302 /board.html?next=$request_uri;"]
    # error_page only sees nginx's own (auth_request) errors: intercepting Multica's own 401s would bounce its API to HTML.
    assert not any("proxy_intercept_errors on" in l for _, b in _blocks(LOCATIONS) for l in _active(b))


def test_negative_control_the_pin_catches_a_conf_that_dropped_an_auth_request():
    for prefix in BOARD_PREFIXES:
        head_re = re.escape("location " + prefix + " {")
        broken = re.sub(r"(" + head_re + r"[^}]*?)^\s*auth_request\s+" + re.escape(GATE) + r";\s*$", r"\1",
                        LOCATIONS, count=1, flags=re.M | re.S)
        assert broken != LOCATIONS, prefix
        assert _ungated_board_routes(broken) == [prefix]


def test_negative_control_a_new_ungated_route_to_multica_is_caught():
    added = LOCATIONS + "\nlocation ^~ /board-raw/ {\n    proxy_pass http://zoe-multica-web:3000/;\n}\n"
    assert _ungated_board_routes(added) == ["^~ /board-raw/"]


def test_board_html_mints_the_cookie_and_only_follows_a_board_path():
    assert '<script src="/js/auth.js"></script>' in BOARD_HTML     # no session -> sign in, then back here
    assert "fetch('/api/multica-ui/session', { method: 'POST'" in BOARD_HTML
    assert "startsWith('/multica/')" in BOARD_HTML and "u.origin !== origin" in BOARD_HTML


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
@pytest.mark.parametrize("raw,want", [
    ("/multica/issues/1?x=1", "/multica/issues/1?x=1"),
    (None, "/multica/"),
    ("", "/multica/"),
    ("https://evil.example/multica/", "/multica/"),
    ("//evil.example/multica/", "/multica/"),
    ("/\\evil.example", "/multica/"),
    ("javascript:alert(1)", "/multica/"),
    ("/dashboard.html", "/multica/"),
    ("/multica-api/api/me", "/multica/"),
])
def test_board_html_redirect_target_is_same_origin_board_only(raw, want):
    fn = BOARD_HTML[BOARD_HTML.index("function boardTarget"):]
    fn = fn[:fn.index("\n    }\n") + 6]
    import json
    script = fn + f"\nprocess.stdout.write(boardTarget({json.dumps(raw)}, 'http://zoe.local'));"
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert out.returncode == 0, out.stderr
    assert out.stdout == want
