"""Every send to a person re-checks the member's erase AFTER its last await (member delete, owner decision 2026-11-15).

A structural check over the source, path-aware: for every call that delivers (a Telegram send, a web push, the panel toast's push,
the phone seam, the in-app broadcast, a queued scheduled send or pending row), every await that can be the LAST one executed before
it must be the erase check itself (``refused`` / ``withdrawn`` / ``send_refused`` / ``refuse_send``). Entering or leaving an
``async with`` counts as an await (a pool acquire / release can stall). A call whose delivered row is instead re-checked where it is
consumed is listed in ``ROW_CHECKED`` with the consumer that refuses it. The walker is pinned by negative controls below.
No database, no network: reads files only.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
CHECKS = {"refused", "withdrawn", "send_refused", "refuse_send"}

#: Telegram: every call of the send function (or of a sender a module injects) anywhere in the service
TELEGRAM_CALLS = {"telegram_notify.send", "c.sender", "_send_telegram", "_telegram_send"}
#: thin wrappers that only forward to the send; their single caller is checked instead
TELEGRAM_WRAPPERS = {
    ("reminder_delivery.py", "_send_telegram"): "wraps telegram_notify.send; its caller _channel checks right before calling it",
    ("change_notices.py", "telegram_sender"): "the default Ctx.sender; _send checks right before c.sender(...)",
}
#: the proactive engine's lanes and the phone seam's channels: (file, function) -> delivering calls
LANE_CALLS = {
    ("proactive/engine.py", "_dispatch"): {"schedule_reminder", "create_pending", "_deliver_to_phone", "_send_push",
                                           "_broadcaster.broadcast"},
    ("proactive/engine.py", "_speak_on_panel"): {"_ui_orchestrator.broadcast_ui_action"},
    ("reminder_delivery.py", "_channel"): {"_send_telegram", "_send_web_push"},
    ("routers/push.py", "send_push_to_user"): {"loop.run_in_executor"},
}
#: delivering calls whose ROW is re-checked where it is consumed (the call itself is the write, so no check can follow it)
ROW_CHECKED = {
    "_ui_orchestrator.enqueue_ui_action": "re-checked before its push (broadcast_ui_action) and refused at the panel's poll "
                                          "(routers/ui_actions.get_pending_ui_actions)",
    "_voice_announce.enqueue_announcement": "stamped with the delivery's start; voice_announce.claim_announcements refuses it",
    "_maybe_speak_notification": "a wrapper: its lanes are checked inside _speak_on_panel",
}


def _name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        inner = _name(node.value)
        return f"{inner}.{node.attr}" if inner else node.attr
    return ""


def _is_check(aw) -> bool:
    return isinstance(aw, ast.Await) and isinstance(aw.value, ast.Call) and _name(aw.value.func).split(".")[-1] in CHECKS


def _awaits(node) -> list:
    """Awaits inside ``node`` in evaluation order (inner before outer), not descending into nested functions."""
    out = []

    def visit(n):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            return
        for child in ast.iter_child_nodes(n):
            visit(child)
        if isinstance(n, ast.Await):
            out.append(n)
    visit(node)
    return out


def _contains(node, target) -> bool:
    return any(n is target for n in ast.walk(node))


_ENTER, _EXIT = "async-with enter", "async-with exit"


def _lasts(stmts, target, state):
    """Walk ``stmts`` from ``state`` (the set of awaits that can have been the last one). Returns ("found", lasts-at-target) or
    ("end", state-after; empty = the path ended in a return / raise)."""
    for s in stmts:
        if not state:
            return "end", set()
        if _contains(s, target):
            return _into(s, target, state)
        state = _after(s, state)
    return "end", state


def _after(s, state):
    if isinstance(s, ast.If):
        st = _expr_after(s.test, state)
        return _lasts(s.body, None, st)[1] | _lasts(s.orelse, None, st)[1] if s.orelse else _lasts(s.body, None, st)[1] | st
    if isinstance(s, (ast.For, ast.AsyncFor, ast.While)):
        st = _expr_after(s.iter if hasattr(s, "iter") else s.test, state)
        return st | _lasts(s.body, None, st)[1]
    if isinstance(s, ast.Try):
        body_end = _lasts(s.body, None, state)[1]
        reach = state | {a for a in _awaits(ast.Module(body=s.body, type_ignores=[]))}
        out = _lasts(s.orelse, None, body_end)[1] if s.orelse else body_end
        for h in s.handlers:
            out |= _lasts(h.body, None, reach)[1]
        return out
    if isinstance(s, (ast.With, ast.AsyncWith)):
        st = state
        for it in s.items:
            st = _expr_after(it.context_expr, st)
        if isinstance(s, ast.AsyncWith):
            st = {_ENTER}
        end = _lasts(s.body, None, st)[1]
        return {_EXIT} if isinstance(s, ast.AsyncWith) and end else end
    st = _expr_after(s, state)
    return set() if isinstance(s, (ast.Return, ast.Raise)) else st


def _expr_after(node, state):
    aws = _awaits(node)
    return {aws[-1]} if aws else state


def _into(s, target, state):
    if isinstance(s, ast.If):
        if _contains(s.test, target):
            return "found", _before_in(s.test, target, state)
        st = _expr_after(s.test, state)
        return _lasts(s.body if any(_contains(b, target) for b in s.body) else s.orelse, target, st)
    if isinstance(s, (ast.For, ast.AsyncFor, ast.While)):
        st = _expr_after(s.iter if hasattr(s, "iter") else s.test, state)
        loop_end = _lasts(s.body, None, st)[1]
        return _lasts(s.body, target, st | loop_end)
    if isinstance(s, ast.Try):
        for part in (s.body, s.orelse, s.finalbody):
            if any(_contains(b, target) for b in part):
                return _lasts(part, target, state)
        for h in s.handlers:
            if any(_contains(b, target) for b in h.body):
                return _lasts(h.body, target, state | set(_awaits(ast.Module(body=s.body, type_ignores=[]))))
    if isinstance(s, (ast.With, ast.AsyncWith)):
        st = state
        for it in s.items:
            if _contains(it.context_expr, target):
                return "found", _before_in(it.context_expr, target, st)
            st = _expr_after(it.context_expr, st)
        if isinstance(s, ast.AsyncWith):
            st = {_ENTER}
        return _lasts(s.body, target, st)
    return "found", _before_in(s, target, state)


def _before_in(node, target, state):
    aws = _awaits(node)
    i = next(i for i, a in enumerate(aws) if a is target)
    return {aws[i - 1]} if i else state


def last_awaits(fn, target) -> set:
    kind, lasts = _lasts(fn.body, target, {None})
    assert kind == "found"
    return lasts


def _functions(tree):
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _own_awaits(fn) -> list:
    return [a for a in _awaits(ast.Module(body=fn.body, type_ignores=[]))]


def _violations(fn, calls) -> tuple[list, list]:
    """(sites checked, problems) for the delivering ``calls`` awaited inside ``fn``."""
    sites, problems = [], []
    for aw in _own_awaits(fn):
        if not isinstance(aw.value, ast.Call):
            continue
        name = _name(aw.value.func)
        if name not in calls:
            continue
        if any(k.arg == "dry" and isinstance(k.value, ast.Constant) and k.value.value is True for k in aw.value.keywords):
            continue                                         # a dry run sends nothing
        sites.append(name)
        bad = [x for x in last_awaits(fn, aw) if not _is_check(x)]
        if bad:
            problems.append(f"{fn.name}:{aw.lineno} {name}: last await before it is "
                            + ", ".join(str(x) if isinstance(x, str) or x is None
                                        else f"line {x.lineno} {_name(x.value.func) if isinstance(x.value, ast.Call) else '?'}" for x in bad))
    return sites, problems


def _service_files():
    for p in sorted(SVC.rglob("*.py")):
        rel = p.relative_to(SVC)
        if "tests" in rel.parts or "alembic" in rel.parts or ".venv" in rel.parts:
            continue
        yield rel, p


def test_every_telegram_send_in_the_service_is_preceded_by_the_erase_check():
    checked, problems = [], []
    for rel, p in _service_files():
        text = p.read_text(encoding="utf-8", errors="replace")
        if "send" not in text:
            continue
        for fn in _functions(ast.parse(text)):
            if (str(rel), fn.name) in TELEGRAM_WRAPPERS:
                continue
            sites, bad = _violations(fn, TELEGRAM_CALLS)
            checked += [(str(rel), fn.name) for _ in sites]
            problems += [f"{rel} {b}" for b in bad]
    assert not problems, problems
    assert {("day_review_raise.py", "send_to_phone"), ("omi_review_digest.py", "send_for"), ("change_notices.py", "_send"),
            ("reminder_delivery.py", "_channel"), ("auth_handoff.py", "send_to_phone")} <= set(checked), checked
    for rel, fname in TELEGRAM_WRAPPERS:                     # a stale allow-list entry hides nothing and must go
        assert any(f.name == fname for f in _functions(ast.parse((SVC / rel).read_text(encoding="utf-8")))), (rel, fname)


#: what a delivering call is named like (a call of this shape that is neither proven checked nor ROW_CHECKED fails the test)
_DELIVERY_SHAPE = re.compile(r"send|push|broadcast|enqueue|deliver|announce|speak|notify|create_pending|schedule_reminder|run_in_executor")


def _lane_problems(fn, calls) -> tuple[list, list]:
    """(delivering sites proven checked, problems): every awaited call of a delivering SHAPE must be either one of ``calls`` with the
    erase check as its last await, or listed in ``ROW_CHECKED`` (its delivered row is refused where it is consumed)."""
    sites, problems = _violations(fn, calls)
    for aw in _own_awaits(fn):
        if not isinstance(aw.value, ast.Call):
            continue
        name = _name(aw.value.func)
        if name.split(".")[-1] in CHECKS or name in calls or name in ROW_CHECKED or not _DELIVERY_SHAPE.search(name):
            continue
        problems.append(f"{fn.name}:{aw.lineno} {name}: delivers but is neither checked nor listed in ROW_CHECKED")
    return sites, problems


def test_every_engine_lane_and_phone_channel_checks_after_its_last_await():
    problems, seen, awaited = [], [], set()
    for (rel, fname), calls in LANE_CALLS.items():
        tree = ast.parse((SVC / rel).read_text(encoding="utf-8"))
        fn = next(f for f in _functions(tree) if f.name == fname)
        sites, bad = _lane_problems(fn, calls)
        seen += sites
        problems += [f"{rel} {b}" for b in bad]
        awaited |= {_name(a.value.func) for a in _own_awaits(fn) if isinstance(a.value, ast.Call)}
    assert not problems, problems
    for (rel, fname), calls in LANE_CALLS.items():
        assert calls & set(seen), f"{rel}:{fname}: no delivering call found (renamed?)"
    assert set(ROW_CHECKED) <= awaited, f"stale ROW_CHECKED entries: {set(ROW_CHECKED) - awaited}"


def test_control_a_planted_unlisted_delivery_fails_the_lane_check():
    src = '''
async def lane(x):
    if await refused("l"):
        return
    await enqueue_something_new(x)
    if await refused("l"):
        return
    await send(x)
'''
    fn = next(f for f in _functions(ast.parse(src)) if f.name == "lane")
    _sites, problems = _lane_problems(fn, {"send"})
    assert problems and "enqueue_something_new" in problems[0], problems


_SRC = '''
async def ok(x):
    await a()
    if await refused("l"):
        return
    await send(x)

async def bad(x):
    if await refused("l"):
        return
    await slow()
    await send(x)

async def branch(x):
    if x:
        await slow()
    else:
        if await refused("l"):
            return
        await send(x)

async def merge(x):
    if await refused("l"):
        return
    if x:
        await slow()
    await send(x)

async def pool(x):
    if await refused("l"):
        return
    async with db() as c:
        await send(x)

async def loop(xs):
    for x in xs:
        if await send_refused(1, 2, "l"):
            break
        await send(x)
'''


@pytest.mark.parametrize("fname, ok", [("ok", True), ("bad", False), ("branch", True), ("merge", False), ("pool", False), ("loop", True)])
def test_the_walker_tells_a_checked_send_from_one_with_an_await_in_between(fname, ok):
    fn = next(f for f in _functions(ast.parse(_SRC)) if f.name == fname)
    _, problems = _violations(fn, {"send"})
    assert (not problems) is ok, problems


# ── behaviour: an erase landing in the LAST await of each lane delivers nothing ─────────────────────────────────────────────────
import asyncio  # noqa: E402
import contextlib  # noqa: E402
from datetime import timedelta  # noqa: E402

import aiosqlite  # noqa: E402

A = "member-erased"


class _RecDB:
    """A compat-DB double that records every statement and serves nothing."""

    def __init__(self):
        self.sql = []

    async def execute(self, sql, params=()):
        self.sql.append((" ".join(sql.split()), tuple(params)))

        class _C:
            rowcount = 1

            async def fetchone(self):
                return None

            async def fetchall(self):
                return []
        return _C()

    async def commit(self):
        pass


def _rec_ctx(db):
    @contextlib.asynccontextmanager
    async def ctx():
        yield db
    return ctx


def test_a_delete_during_the_toast_queue_write_withdraws_it_and_pushes_nothing(monkeypatch):
    import erase_watermark
    import proactive.engine as engine
    import ui_orchestrator

    events = []

    async def enqueue(db, **kw):
        await erase_watermark.record(A)                   # the delete lands while the toast is written
        msg = {"action_id": "act-1", "panel_id": kw.get("panel_id"), "user_id": kw["user_id"]}
        if kw.get("broadcast", True):
            events.append(("pushed", msg["action_id"]))
        return msg

    async def withdraw(db, action_id):
        events.append(("withdrawn", action_id))

    async def push(msg):
        events.append(("pushed", msg["action_id"]))
    monkeypatch.setattr(engine, "_get_compat_db", _rec_ctx(_RecDB()))
    monkeypatch.setattr(ui_orchestrator, "enqueue_ui_action", enqueue)
    monkeypatch.setattr(ui_orchestrator, "withdraw_ui_action", withdraw, raising=False)
    monkeypatch.setattr(ui_orchestrator, "broadcast_ui_action", push, raising=False)

    async def go():
        with erase_watermark.delivery(erase_watermark._now()):
            return await engine._speak_on_panel(user_id=A, panel_id="panel-1", message="hello", trigger_type="morning_checkin")
    panel, daemon, _ann = asyncio.run(go())
    assert events == [("withdrawn", "act-1")], events
    assert panel == "refused:erased" and daemon == "refused:erased"


def test_a_spoken_line_queued_after_a_delete_that_landed_mid_delivery_is_never_claimed(tmp_path, monkeypatch):
    """The delete lands between the sender's last check and the queue insert: the row is stamped with the DELIVERY's start, so the
    panel's claim refuses it (the insert time would be after the mark, and the old code served it). The insert is held 3 s after the
    mark (the stamp has whole-second resolution, so a same-second insert would be refused by rounding alone)."""
    import erase_watermark
    import voice_announce

    real_now = voice_announce._now
    monkeypatch.setattr(voice_announce, "_now", lambda: real_now() + timedelta(seconds=3))

    async def go():
        db = await aiosqlite.connect(str(tmp_path / "va.db"))
        db.row_factory = aiosqlite.Row
        await db.execute("CREATE TABLE voice_announcements (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, panel_id TEXT, message TEXT NOT NULL, "
                         "trigger_type TEXT, created_at TEXT, expires_at TEXT, delivered_at TEXT, delivered_to TEXT, played_at TEXT, "
                         "expired INTEGER DEFAULT 0)")
        with erase_watermark.delivery(erase_watermark._now() - timedelta(seconds=2)):
            await erase_watermark.record(A)               # after the delivery began, before the insert
            await voice_announce.enqueue_announcement(db, user_id=A, message="a line for the member", panel_id="p1", trigger_type="reminder")
        got = await voice_announce.claim_announcements(db, panel_id="p1")
        await db.close()
        return got
    assert asyncio.run(go()) == []


def test_a_scheduled_send_made_across_a_delete_is_removed_and_never_registered(monkeypatch):
    import erase_watermark
    from proactive.triggers import reminders

    db = _RecDB()
    registered = []

    async def generation_then_erase(item_id):
        await erase_watermark.record(A)                   # the delete lands while the row is being made
        return 0

    async def run_blocking(fn, **kw):
        registered.append(kw.get("job_id"))
    monkeypatch.setattr(reminders, "_get_compat_db", _rec_ctx(db))
    monkeypatch.setattr(reminders, "_reminder_generation", generation_then_erase)
    monkeypatch.setattr(reminders, "run_blocking", run_blocking)
    got = asyncio.run(reminders.schedule_reminder(user_id=A, message="bins", send_at=erase_watermark._now() + timedelta(hours=1),
                                                  item_id="rem-1"))
    assert got == "" and registered == []
    inserted = [p for s, p in db.sql if s.startswith("INSERT INTO proactive_scheduled")]
    assert inserted and any(s.startswith("DELETE FROM proactive_scheduled") for s, _ in db.sql)
    made = erase_watermark.instant(inserted[0][-1])
    assert made is not None and made <= asyncio.run(erase_watermark.watermark(A)), \
        "the row is stamped with when scheduling BEGAN, so the fire-time check refuses it too"


def test_an_unreadable_mark_after_the_in_app_insert_withdraws_the_row_and_a_retry_cannot_duplicate_it(monkeypatch):
    import erase_watermark
    import proactive.engine as engine
    import reminder_delivery

    db = _RecDB()
    monkeypatch.setattr(engine, "_get_compat_db", _rec_ctx(db))

    async def pending(**_):
        return "pend-1"

    async def nothing(**_):
        return None

    async def no_subscribers(**_):
        return 0
    monkeypatch.setattr(engine, "create_pending", pending)
    monkeypatch.setattr(engine, "_maybe_speak_notification", nothing)
    monkeypatch.setattr(engine, "_send_push", no_subscribers)
    monkeypatch.setattr(engine, "_is_in_quiet_hours", lambda *a: False)
    monkeypatch.setattr(reminder_delivery, "mode", lambda: "off")
    real = erase_watermark.send_refused

    async def unreadable_at_broadcast(uid, since, lane):
        if lane == "in_app_broadcast":
            raise erase_watermark.Unavailable("down")
        return await real(uid, since, lane)
    monkeypatch.setattr(erase_watermark, "send_refused", unreadable_at_broadcast)
    with pytest.raises(erase_watermark.Unavailable):
        asyncio.run(engine.fire_notification(user_id="member-live", message="Water the ferns", trigger_type="reminder",
                                             pending_id="sched-1", item_id="rem-1"))
    ins = [s for s, p in db.sql if s.startswith("INSERT INTO notifications")]
    assert ins and "ON CONFLICT (id) DO NOTHING" in ins[0], "one row per scheduled send: a retry re-inserts nothing"
    assert ("DELETE FROM notifications WHERE id = ?", ("reminder-sched-1",)) in db.sql, "the unbroadcast row is withdrawn"


def test_the_panels_poll_never_serves_an_action_queued_for_a_member_deleted_after_it(monkeypatch):
    import erase_watermark
    import routers.ui_actions as ui_actions

    mark = asyncio.run(erase_watermark.record(A))
    before = (mark - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    after = (mark + timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [{"id": "old", "panel_id": "p1", "chat_session_id": None, "action_type": "panel_announce", "payload": "{}", "status": "queued",
             "requires_confirmation": 0, "confirmation_token": None, "retry_count": 0, "max_retries": 3, "created_at": before,
             "updated_at": before},
            {"id": "new", "panel_id": "p1", "chat_session_id": None, "action_type": "panel_announce", "payload": "{}", "status": "queued",
             "requires_confirmation": 0, "confirmation_token": None, "retry_count": 0, "max_retries": 3, "created_at": after,
             "updated_at": after}]

    class _DB(_RecDB):
        async def execute(self, sql, params=()):
            await super().execute(sql, params)
            head = sql.strip().upper()

            class _C:
                async def fetchone(self_):
                    return {"user_id": A} if "FROM UI_PANEL_SESSIONS" in head else None

                async def fetchall(self_):
                    return rows if head.startswith("SELECT") and "FROM UI_ACTIONS" in head else []
            return _C()

    async def allow(*_a, **_k):
        return None
    monkeypatch.setattr(ui_actions, "require_feature_access", allow)
    monkeypatch.setattr(ui_actions, "_authorize_panel", allow)
    db = _DB()
    out = asyncio.run(ui_actions.get_pending_ui_actions(panel_id="p1", limit=20, user={"user_id": A, "role": "member"}, db=db))
    assert [a["id"] for a in out["actions"]] == ["new"]
    assert any(s.startswith("UPDATE ui_actions SET status = 'skipped', error_code = 'erased'") and p == ("old",) for s, p in db.sql)


def test_a_guest_kiosks_poll_never_serves_a_toast_queued_for_a_member_deleted_after_it(monkeypatch):
    """The kiosk's actions are stored as ``guest``; the member is the action's ORIGIN (its queued ledger event). The poll checks the
    origin's erase mark: the deleted member's toast is skipped, another member's toast on the same panel is served."""
    import json as _json

    import erase_watermark
    import routers.ui_actions as ui_actions

    mark = asyncio.run(erase_watermark.record(A))
    before = (mark - timedelta(seconds=5)).strftime("%Y-%m-%dT%H:%M:%SZ")

    def row(aid):
        return {"id": aid, "panel_id": "kiosk", "chat_session_id": None, "action_type": "panel_announce", "payload": "{}",
                "status": "queued", "requires_confirmation": 0, "confirmation_token": None, "retry_count": 0, "max_retries": 3,
                "created_at": before, "updated_at": before}
    rows = [row("for-erased"), row("for-other")]
    ledger = [{"action_id": "for-erased", "event_data": _json.dumps({"origin_user_id": A})},
              {"action_id": "for-other", "event_data": _json.dumps({"origin_user_id": "member-other"})}]

    class _DB(_RecDB):
        async def execute(self, sql, params=()):
            await super().execute(sql, params)
            head = sql.strip().upper()

            class _C:
                async def fetchone(self_):
                    return {"user_id": "guest"} if "FROM UI_PANEL_SESSIONS" in head else None

                async def fetchall(self_):
                    if head.startswith("SELECT") and "FROM UI_ACTION_LEDGER" in head:
                        return ledger
                    return rows if head.startswith("SELECT") and "FROM UI_ACTIONS" in head else []
            return _C()

    async def allow(*_a, **_k):
        return None
    monkeypatch.setattr(ui_actions, "require_feature_access", allow)
    monkeypatch.setattr(ui_actions, "_authorize_panel", allow)
    db = _DB()
    out = asyncio.run(ui_actions.get_pending_ui_actions(panel_id="kiosk", limit=20, user={"user_id": "guest", "role": "guest"}, db=db))
    assert [a["id"] for a in out["actions"]] == ["for-other"]
    assert any(s.startswith("UPDATE ui_actions SET status = 'skipped'") and p == ("for-erased",) for s, p in db.sql)
