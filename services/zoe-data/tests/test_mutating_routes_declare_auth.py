"""Every MUTATING zoe-data route (POST / PUT / PATCH / DELETE) declares an auth dependency, or is on the justified allowlist.

A2 (architecture deep dive 2026-10-18): 26 mutating routes had no ``Depends`` at all, 17 of them the music writes. This makes
the next one fail CI. A route passes when its parameters, decorator ``dependencies=[...]`` or ``APIRouter(dependencies=...)``
name a dependency in ``AUTH_DEPENDENCIES``. That is the FLOOR, not the right rule (``get_current_user`` admits a guest; each
route's own tests pin who it needs). A route that authenticates in its body goes on ``ALLOWLIST`` with the reason; a stale
entry fails. Static (``ast``): imports nothing, runs in the slim CI venv.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[1]
MUTATING = {"post", "put", "patch", "delete"}

# Dependencies that establish WHO is calling (or refuse). A new auth helper is added here in the PR that introduces it.
AUTH_DEPENDENCIES = frozenset({
    "get_current_user", "require_admin", "require_member", "require_member_or_panel", "require_signed_in",
    "require_internal_token", "require_intent_dispatch_auth", "require_lab_operator", "resolve_acting_user",
    "get_a2a_caller", "_require_voice_auth", "_require_owner", "_require_human_admin", "_require_device", "_member",
    "_require_member", "_require_member_session", "_require_caller", "_require_livekit_media_auth", "_require_livekit_auth",
    "_validate_device_token", "_require_multica_webhook_secret", "require_member_panel_or_jukebox",
})

# (file, handler) -> why it authenticates without a declared dependency.
ALLOWLIST = {
    ("routers/music_setup.py", "setup_save"): "the phone holds a one-time signed setup token (in the body), validated + consumed",
    ("routers/music_setup.py", "oauth_start"): "one-time signed setup token in the body",
    ("routers/music_setup.py", "browser_start"): "one-time signed setup token in the body",
    ("routers/music_setup.py", "browser_cancel"): "one-time signed setup token in the body",
    ("routers/panel_auth.py", "renew_device_token"): "X-Device-Token is checked in the body; unknown/expired/revoked is 401",
    ("routers/panel_auth.py", "submit_pin"): "the PIN IS the credential, validated by zoe-auth against an open challenge",
    ("routers/panel_kiosk.py", "kiosk_ticket"): "X-Device-Token for THIS panel is checked in the body (403 otherwise)",
    ("routers/panel_kiosk.py", "kiosk_enter"): "spends a single-use ticket minted from a device token; a bad one sets nothing",
    ("routers/panel_provision.py", "provision_request"): "first-boot pairing: by design pre-auth, rate-limited, mints only a code",
    ("routers/proactive.py", "claim_pending_endpoint"): "the unguessable pending id IS the capability (push-notification tap)",
    ("routers/proactive.py", "run_selector_synthetic"): "X-Internal-Token checked in the body; synthetic harness ids only",
    ("routers/memories.py", "forget_synthetic_user"): "X-Internal-Token checked in the body; synthetic harness ids only",
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


def _methods(dec: ast.Call) -> set[str]:
    verb = dec.func.attr  # type: ignore[union-attr]
    if verb in MUTATING:
        return {verb}
    if verb == "api_route":
        for kw in dec.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
                return {str(e.value).lower() for e in kw.value.elts if isinstance(e, ast.Constant)} & MUTATING
    return set()


def scan(source: str) -> list[tuple[str, str, set[str]]]:
    """``(handler, path, declared auth dependencies)`` for every mutating route in ``source``."""
    tree = ast.parse(source)
    router_deps: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            fn = node.value.func
            if getattr(fn, "id", getattr(fn, "attr", "")) in ("APIRouter", "FastAPI"):
                for kw in node.value.keywords:
                    if kw.arg == "dependencies":
                        for target in node.targets:
                            if isinstance(target, ast.Name):
                                router_deps[target.id] = _dep_names(kw.value)
    routes = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in fn.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and isinstance(dec.func.value, ast.Name) and _methods(dec)):
                continue
            deps = set(router_deps.get(dec.func.value.id, set())) | _dep_names(fn.args)
            for kw in dec.keywords:
                if kw.arg == "dependencies":
                    deps |= _dep_names(kw.value)
            path = dec.args[0].value if dec.args and isinstance(dec.args[0], ast.Constant) else "?"
            routes.append((fn.name, str(path), deps & AUTH_DEPENDENCIES))
    return routes


def _all_routes() -> list[tuple[str, str, str, set[str]]]:
    files = [ROOT / "main.py", *sorted((ROOT / "routers").glob("*.py"))]
    return [(str(f.relative_to(ROOT)), *route) for f in files for route in scan(f.read_text())]


def test_the_scan_sees_the_real_route_table():
    routes = _all_routes()
    assert len(routes) >= 200, len(routes)  # 246 on 2026-10-18: a scan that silently matches nothing must not pass
    assert any(r[1] == "music_control" for r in routes)


def test_every_mutating_route_declares_auth_or_is_allowlisted():
    bare = [f"{rel}::{name} {path}" for rel, name, path, deps in _all_routes()
            if not deps and (rel, name) not in ALLOWLIST]
    assert not bare, (
        "mutating route(s) with no auth dependency - add Depends(<auth>) (panel_principal.require_member / "
        "require_member_or_panel / auth.require_admin ...), or add a JUSTIFIED entry to ALLOWLIST:\n  " + "\n  ".join(bare))


def test_no_imperative_route_registration_hides_from_the_scan():
    """``add_api_route`` / ``add_route`` calls are invisible to the decorator scan: none may exist until it covers them."""
    files = [ROOT / "main.py", *sorted((ROOT / "routers").glob("*.py"))]
    hits = [f"{f.name}:{n.lineno}" for f in files for n in ast.walk(ast.parse(f.read_text()))
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") in ("add_api_route", "add_route")]
    assert not hits, hits


def test_the_allowlist_does_not_rot():
    by_key = {(rel, name): deps for rel, name, _path, deps in _all_routes()}
    stale = [k for k in ALLOWLIST if k not in by_key or by_key[k]]
    assert not stale, f"allowlisted routes that no longer exist or now declare auth (drop them): {stale}"


# Negative controls: the scanner goes red on a bare write, and recognises each way a dependency is declared.
@pytest.mark.parametrize("router, decorator, params, expected", [
    ("APIRouter()", "post('/x')", "payload: dict", [("x", "/x", set())]),                       # bare: flagged
    ("APIRouter()", "delete('/x')", "db=Depends(get_db)", [("x", "/x", set())]),                # get_db is not auth
    ("APIRouter()", "post('/x')", "u=Depends(get_current_user)", [("x", "/x", {"get_current_user"})]),
    ("APIRouter()", "put('/x', dependencies=[Depends(require_member)])", "", [("x", "/x", {"require_member"})]),
    ("APIRouter(dependencies=[Depends(require_admin)])", "patch('/x')", "", [("x", "/x", {"require_admin"})]),
    ("APIRouter()", "api_route('/x', methods=['GET', 'POST'])", "", [("x", "/x", set())]),
    ("APIRouter()", "get('/x')", "", []),                                                       # reads are out of scope
])
def test_control_the_scanner(router, decorator, params, expected):
    assert scan(f"r = {router}\n@r.{decorator}\nasync def x({params}):\n    pass\n") == expected
