"""Every MUTATING zoe-data route (POST / PUT / PATCH / DELETE) declares an auth dependency, or is on the justified allowlist.

A2 (architecture deep dive 2026-10-18): 26 mutating routes had no ``Depends`` at all, 17 of them the music writes. This makes
the next one fail CI. A route passes when its parameters, decorator ``dependencies=[...]`` or ``APIRouter(dependencies=...)``
name a dependency in ``AUTH_DEPENDENCIES``. That is the FLOOR, not the right rule (``get_current_user`` admits a guest; each
route's own tests pin who it needs). A route that authenticates in its body goes on ``ALLOWLIST`` with the reason; a stale
entry fails. Static (``ast``): imports nothing, runs in the slim CI venv.

Second tier (2026-10-19): ``get_current_user`` alone ADMITS A GUEST, so a write that declares only it relies on whatever its
body checks - for the calendar that was the role matrix alone, which counted the header-less ``ZOE_UNAUTHENTICATED_ROLE``
promotion as an admin writer. ``GUEST_ADMITTING_BASELINE`` is the reviewed inventory of the writes that still declare ONLY a
guest-admitting dependency (guests may chat; the fridge-rule list items judge the panel in their body; ...). It is a RATCHET:
a NEW guest-admitting write fails until it declares a principal (``require_member`` / ``require_member_or_panel`` /
``require_admin`` ...) or is added here deliberately in review, and an entry that now declares one must be dropped.
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
    "require_phone_device", "require_pairing_code",
    "_require_card",  # routers/panel_review_cards: the X-Panel-Card token, honoured only for its member's open voice session or own session
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


# Dependencies that establish a caller but ADMIT A GUEST (a credential-less LAN request resolves to ``guest``).
GUEST_ADMITTING = frozenset({"get_current_user"})

# file -> handlers whose writes declare ONLY a guest-admitting dependency (reviewed 2026-10-19; each judges the caller in its
# body: the role matrix, an owner check, the fridge rule, or "guests may do this"). Shrink it; never grow it without review.
GUEST_ADMITTING_BASELINE = {
    "routers/auth.py": {"logout"},
    "routers/chat.py": {"cancel_latest_run", "create_background_task", "create_session", "delete_session", "resume_run", "save_message", "submit_feedback", "whatsapp_connect"},
    "routers/dashboard.py": {"add_widgets", "remove_widget", "save_layout"},
    "routers/ha_ui.py": {"session"},  # panel_principal.member_or_panel in the body: a member or the strictly registered panel, else 403
    "routers/journal.py": {"create_entry", "delete_entry", "update_entry"},
    "routers/lists.py": {"add_item", "create_list", "delete_item", "delete_list", "update_item", "update_list"},
    "routers/memories.py": {"create_memory_proposal", "forget_my_memory", "link_preview", "review_memory", "set_memory_opt_out"},
    "routers/music_ma_ui.py": {"session"},  # ma_ui_gate.principal_ok in the body: a member or the strictly registered panel, else 403
    "routers/notes.py": {"create_note", "delete_note", "patch_note", "update_note"},
    "routers/notifications.py": {"create_notification", "delete_notification", "mark_read", "track_interaction"},
    "routers/panel_auth.py": {"create_pin_challenge"},
    "routers/people.py": {"add_activity", "add_bucket_item", "add_gift_idea", "add_important_date", "add_relationship", "create_person", "delete_person", "delete_relationship", "mark_bucket_done", "mark_person_read", "merge_person_endpoint", "update_gift_status", "update_person", "update_relationship"},
    "routers/portrait.py": {"regenerate_my_portrait", "regenerate_portrait"},
    "routers/proactive.py": {"accept_pending_suggestion", "create_schedule", "delete_schedule", "dismiss_pending_suggestion", "trigger_morning_brief"},
    "routers/push.py": {"subscribe", "unsubscribe"},
    "routers/reminders.py": {"acknowledge_reminder", "create_reminder", "delete_reminder", "mark_notification_delivered", "snooze_reminder", "update_reminder"},
    "routers/skybridge.py": {"cancel_skybridge_timer", "resolve_skybridge_command"},
    "routers/stubs.py": {"create_project", "save_intelligence_settings"},
    "routers/system.py": {"board_review", "post_display_brightness", "post_display_volume", "put_display_preferences", "put_openclaw_preferences", "trigger_agent_sync", "trigger_memory_consolidation", "trigger_memory_digest", "trigger_run_digest"},
    "routers/transactions.py": {"create_transaction", "delete_transaction", "toggle_transaction_status", "update_transaction"},
    "routers/ui_actions.py": {"ack_ui_action", "bind_panel", "close_action_form", "confirm_action_form", "create_ui_action", "open_action_form", "requeue_stale_actions", "retry_ui_action", "sync_ui_state"},
    "routers/user_profile.py": {"analyze_profile", "create_telegram_link_token", "set_telegram_link"},
    "routers/voice_settings.py": {"preview_voice"},
    "routers/weather.py": {"update_preferences"},
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


def _guest_admitting_only(routes) -> list[tuple[str, str]]:
    return [(rel, name) for rel, name, _path, deps in routes if deps and deps <= GUEST_ADMITTING]


def test_no_new_write_declares_only_a_guest_admitting_dependency():
    new = sorted({(rel, name) for rel, name in _guest_admitting_only(_all_routes())
                  if name not in GUEST_ADMITTING_BASELINE.get(rel, set())})
    assert not new, (
        "write route(s) that declare only get_current_user (which admits a guest) - declare a principal "
        "(panel_principal.require_member / require_member_or_panel, auth.require_admin ...), or, if a guest or the "
        "panel genuinely may do this and the body judges it, add it to GUEST_ADMITTING_BASELINE in review:\n  "
        + "\n  ".join(f"{r}::{n}" for r, n in new))


def test_the_guest_admitting_baseline_does_not_rot():
    current = set(_guest_admitting_only(_all_routes()))
    stale = sorted((rel, n) for rel, names in GUEST_ADMITTING_BASELINE.items() for n in names if (rel, n) not in current)
    assert not stale, f"baseline entries that no longer exist or now declare a principal (drop them): {stale}"


def test_household_writes_are_never_guest_admitting():
    """The calendar is the household's shared record; music, rooms, panel config and home control drive the house."""
    for rel in ("routers/calendar.py", "routers/music.py", "routers/rooms.py", "routers/panel_config.py",
                "routers/ha_control.py"):
        assert rel not in GUEST_ADMITTING_BASELINE, rel
    routes = _all_routes()
    calendar = {name for rel, name, _p, _d in routes if rel == "routers/calendar.py"}
    assert {"create_event", "update_event", "delete_event"} <= calendar  # the scan still sees them


@pytest.mark.parametrize("params, flagged", [
    ("u=Depends(get_current_user)", True),                                   # the old calendar shape: flagged
    ("u=Depends(require_member_or_panel)", False),
    ("u=Depends(require_member), v=Depends(get_current_user)", False),
])
def test_control_the_guest_admitting_tier(params, flagged):
    routes = [("routers/calendar.py", *r) for r in scan(f"r = APIRouter()\n@r.post('/events')\nasync def create_event({params}):\n    pass\n")]
    assert bool(_guest_admitting_only(routes)) is flagged


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
