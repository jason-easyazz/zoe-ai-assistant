"""Every MUSIC route (reads included) declares a principal dependency, or is on a justified allowlist.

Music READS used to be open to any device on the home network (owner decision 2026-10-18: reads follow the writes - a household
member or the panel). ``test_mutating_routes_declare_auth.py`` only covers POST/PUT/PATCH/DELETE, so a new ``GET`` under
``/api/music`` would slip past it. This is that guard for the music surface: a route must name ``require_member_or_panel``
(or the jukebox variant, for the two reads the phone jukebox page shows), or be allowlisted with the reason it needs none.
Static (``ast``): imports nothing, runs in the slim CI venv.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[1]
VERBS = {"get", "post", "put", "patch", "delete"}

# The ONLY dependencies that satisfy a music route. ``get_current_user`` is deliberately NOT here: it resolves a header-less
# LAN caller to ``guest`` and lets it through.
MUSIC_DEPENDENCIES = frozenset({"require_member_or_panel", "require_member_panel_or_jukebox"})

# (file, handler) -> why it needs no principal. Only token / single-use-handle capabilities belong here.
ALLOWLIST = {
    ("routers/music.py", "jukebox_qr"): "single-use unguessable handle minted by the panel (setup_qr.redeem), like the setup QR",
    ("routers/music_setup.py", "setup_qr_image"): "single-use handle (setup_qr.redeem) + a valid setup token",
    ("routers/music_setup.py", "setup_form"): "phone: the one-time signed setup token IS the credential",
    ("routers/music_setup.py", "setup_save"): "phone: one-time signed setup token in the body, consumed",
    ("routers/music_setup.py", "oauth_start"): "phone: one-time signed setup token in the body",
    ("routers/music_setup.py", "oauth_status"): "phone: one-time signed setup token header",
    ("routers/music_setup.py", "browser_start"): "phone: one-time signed setup token in the body",
    ("routers/music_setup.py", "browser_status"): "phone: one-time signed setup token header",
    ("routers/music_setup.py", "browser_cancel"): "phone: one-time signed setup token in the body",
    ("routers/music_ma_ui.py", "gate"): "nginx auth_request target: it IS the credential check (cookie, member or panel; 401 otherwise)",
    ("routers/music_ma_ui.py", "session"): "member-or-panel checked in the body via ma_ui_gate.principal_ok - deliberately NOT "
                                           "require_member_or_panel, whose ZOE_HA_CONTROL_SCOPE=open rollback must not open MA's UI",
}

# Router files that import ``music_service`` (the sole Music Assistant client) but serve no ``/api/music`` route of their own:
# each one's routes were reviewed for who may reach MA state through them. A NEW file importing it fails until reviewed.
REVIEWED_MUSIC_STATE_ROUTERS = {
    "music.py", "music_setup.py",
    "music_ma_ui.py",   # the gate in front of Music Assistant's own web UI (ma_ui_gate.py); serves no MA state itself
    "handoff.py",       # /api/handoff/*: token/nonce-bound app-connection handoffs
    "panel_config.py",  # /api/panels/*: member-or-panel (#2099); music_service only validates/gets the default speaker
}


def _dep_names(node: ast.AST) -> set[str]:
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id in ("Depends", "Security") and n.args:
            arg = n.args[0]
            if isinstance(arg, ast.Name):
                out.add(arg.id)
            elif isinstance(arg, ast.Attribute):
                out.add(arg.attr)
    return out


def scan(source: str) -> tuple[str, list[tuple[str, str, str, set[str]]]]:
    """``(router prefix, [(verb, handler, path, declared music dependencies)])`` for the routes of the one router in ``source``."""
    tree = ast.parse(source)
    prefix, router_deps, router_names = "", set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fn = node.value.func
            if getattr(fn, "id", getattr(fn, "attr", "")) == "APIRouter":
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        router_names.add(t.id)
                for kw in node.value.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        prefix = str(kw.value.value)
                    if kw.arg == "dependencies":
                        router_deps |= _dep_names(kw.value)
    routes = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in fn.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and isinstance(dec.func.value, ast.Name) and dec.func.value.id in router_names):
                continue
            verb = dec.func.attr
            if verb != "api_route" and verb not in VERBS:
                continue
            deps = set(router_deps) | _dep_names(fn.args)
            for kw in dec.keywords:
                if kw.arg == "dependencies":
                    deps |= _dep_names(kw.value)
            path = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else "?"
            routes.append((verb, fn.name, str(path), deps & MUSIC_DEPENDENCIES))
    return prefix, routes


def _music_routes() -> list[tuple[str, str, str, str, set[str]]]:
    out = []
    for f in sorted((ROOT / "routers").glob("*.py")):
        prefix, routes = scan(f.read_text())
        if prefix.startswith("/api/music"):
            out += [(f"routers/{f.name}", verb, name, prefix + path, deps) for verb, name, path, deps in routes]
    return out


def test_the_scan_sees_the_real_music_route_table():
    routes = _music_routes()
    assert len(routes) >= 40, len(routes)  # 41 on 2026-10-18: a scan that silently matches nothing must not pass
    assert any(r[1] == "get" and r[2] == "music_now_playing" for r in routes)  # reads are in scope, not only writes
    assert any(r[1] == "get" and r[2] == "setup_catalogue" for r in routes)    # and so is music_setup.py


def test_every_music_route_declares_a_principal_or_is_allowlisted():
    bare = [f"{rel}::{name} {verb.upper()} {path}" for rel, verb, name, path, deps in _music_routes()
            if not deps and (rel, name) not in ALLOWLIST]
    assert not bare, (
        "music route(s) with no principal dependency - add Depends(require_member_or_panel) (a member or the panel; the "
        "jukebox page's now-playing/search take require_member_panel_or_jukebox), or add a JUSTIFIED ALLOWLIST entry:\n  "
        + "\n  ".join(bare))


def test_only_the_jukebox_page_reads_take_the_jukebox_token():
    """The guest capability stays narrow: the token opens the plays and exactly the two reads its page shows."""
    wide = sorted(f"{verb.upper()} {path}" for _rel, verb, _name, path, deps in _music_routes()
                  if "require_member_panel_or_jukebox" in deps)
    assert wide == ["GET /api/music/now-playing", "GET /api/music/search",
                    "POST /api/music/play", "POST /api/music/play_media"], wide


def test_the_allowlist_does_not_rot():
    by_key = {(rel, name): deps for rel, _verb, name, _path, deps in _music_routes()}
    stale = [k for k in ALLOWLIST if k not in by_key or by_key[k]]
    assert not stale, f"allowlisted routes that no longer exist or now declare a principal (drop them): {stale}"


def test_no_new_router_file_reaches_music_assistant_unreviewed():
    importers = {f.name for f in (ROOT / "routers").glob("*.py")
                 if "music_service" in f.read_text() or "/api/music" in f.read_text()}
    assert importers <= REVIEWED_MUSIC_STATE_ROUTERS, (
        f"{sorted(importers - REVIEWED_MUSIC_STATE_ROUTERS)} touch Music Assistant state: review who may reach it "
        "(member or panel) and add the file to REVIEWED_MUSIC_STATE_ROUTERS")


# Negative controls: the scanner goes red on a bare read, and recognises each way a dependency is declared.
@pytest.mark.parametrize("router, decorator, params, expected", [
    ("APIRouter(prefix='/api/music')", "get('/x')", "", [("get", "x", "/x", set())]),                       # bare read: flagged
    ("APIRouter(prefix='/api/music')", "get('/x')", "u=Depends(get_current_user)", [("get", "x", "/x", set())]),  # guest passes it
    ("APIRouter(prefix='/api/music')", "get('/x', dependencies=[Depends(require_member_or_panel)])", "",
     [("get", "x", "/x", {"require_member_or_panel"})]),
    ("APIRouter(prefix='/api/music')", "get('/x')", "u=Depends(require_member_or_panel)",
     [("get", "x", "/x", {"require_member_or_panel"})]),
    ("APIRouter(prefix='/api/music', dependencies=[Depends(require_member_or_panel)])", "get('/x')", "",
     [("get", "x", "/x", {"require_member_or_panel"})]),
    ("APIRouter(prefix='/api/music')", "post('/x', dependencies=[Depends(require_member_panel_or_jukebox)])", "",
     [("post", "x", "/x", {"require_member_panel_or_jukebox"})]),
])
def test_control_the_scanner(router, decorator, params, expected):
    prefix, routes = scan(f"r = {router}\n@r.{decorator}\nasync def x({params}):\n    pass\n")
    assert prefix == "/api/music" and routes == expected
