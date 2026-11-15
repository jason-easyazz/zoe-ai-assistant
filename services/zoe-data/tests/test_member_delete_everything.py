"""A member delete removes everything the member owns, and nothing of anyone else's (owner decision 2026-11-15).

Real ``MemoryService.delete_user`` over a fake palace, with ``member_owned_forget`` on one SQLite file holding every store it reaches.
Members A and B both have rows in every store, and the household-SHARED objects (a family list, a family contact, a family event, a
chat session both spoke in) carry entries from both. After A is deleted: A's rows are gone, B's rows and every shared object are intact,
the shared objects lose only what A authored, A's scheduled sends cannot fire, and a later transcript miner finds nothing of A.
Synthetic placeholder people and text only.
"""
from __future__ import annotations

import asyncio
import contextlib
import json

import aiosqlite
import pytest

import member_owned_forget as mof
import memory_service
from test_erase_outruns_digest import _rows, env  # noqa: F401 - the real delete_user over a fake collection
from test_memory_implicit_supersede import UID as A

pytestmark = pytest.mark.ci_safe

B = "member-b"

_DDL = """
CREATE TABLE chat_sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT 'New Chat');
CREATE TABLE chat_messages (id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE, role TEXT NOT NULL,
    content TEXT NOT NULL, metadata TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE chat_ag_ui_runs (id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE, run_id TEXT, events TEXT);
CREATE TABLE chat_feedback (id TEXT PRIMARY KEY, interaction_id TEXT, user_id TEXT, corrected_response TEXT);
CREATE TABLE background_tasks (id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, task TEXT NOT NULL, result TEXT);
CREATE TABLE ui_actions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, payload TEXT);
CREATE TABLE memory_consolidation_state (session_id TEXT PRIMARY KEY, user_id TEXT, last_consolidated_at TEXT, turns_consolidated INTEGER);
CREATE TABLE notes (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, content TEXT, visibility TEXT NOT NULL DEFAULT 'personal',
    person_id TEXT REFERENCES people(id));
CREATE TABLE journal_entries (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, content TEXT, visibility TEXT NOT NULL DEFAULT 'personal',
    person_id TEXT REFERENCES people(id));
CREATE TABLE transactions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, description TEXT, visibility TEXT NOT NULL DEFAULT 'family');
CREATE TABLE reminders (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT, visibility TEXT NOT NULL DEFAULT 'personal');
CREATE TABLE proactive_pending (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, message TEXT NOT NULL);
CREATE TABLE proactive_scheduled (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, message TEXT NOT NULL, apscheduler_job_id TEXT,
    fired INTEGER DEFAULT 0, item_id TEXT DEFAULT '', claimed_at TEXT, schedule_generation INTEGER DEFAULT 0, attempts INTEGER DEFAULT 0,
    last_error TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE voice_announcements (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, panel_id TEXT, message TEXT NOT NULL, trigger_type TEXT,
    created_at TEXT, expires_at TEXT);
CREATE TABLE device_tokens (token TEXT PRIMARY KEY, panel_id TEXT, revoked INTEGER DEFAULT 0);
CREATE TABLE openclaw_approvals (id TEXT PRIMARY KEY, user_id TEXT, request_text TEXT);
CREATE TABLE openclaw_run_state (id TEXT PRIMARY KEY, user_id TEXT, request_text TEXT);
CREATE TABLE lists (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT, visibility TEXT NOT NULL DEFAULT 'family');
CREATE TABLE list_items (id TEXT PRIMARY KEY, list_id TEXT NOT NULL REFERENCES lists(id) ON DELETE CASCADE, text TEXT, assigned_to TEXT);
CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT, visibility TEXT NOT NULL DEFAULT 'family');
CREATE TABLE people_field_values (id TEXT PRIMARY KEY, person_id TEXT NOT NULL REFERENCES people(id) ON DELETE CASCADE, value_json TEXT);
CREATE TABLE person_activities (id TEXT PRIMARY KEY, person_id TEXT REFERENCES people(id) ON DELETE CASCADE, user_id TEXT, description TEXT);
CREATE TABLE person_bucket_list (id TEXT PRIMARY KEY, person_id TEXT REFERENCES people(id) ON DELETE CASCADE, user_id TEXT, description TEXT);
CREATE TABLE person_gift_ideas (id TEXT PRIMARY KEY, person_id TEXT REFERENCES people(id) ON DELETE CASCADE, user_id TEXT, description TEXT);
CREATE TABLE person_important_dates (id TEXT PRIMARY KEY, person_id TEXT REFERENCES people(id) ON DELETE CASCADE, user_id TEXT, label TEXT);
CREATE TABLE person_relationships (id TEXT PRIMARY KEY, user_id TEXT, person_a_id TEXT REFERENCES people(id) ON DELETE CASCADE,
    person_b_id TEXT REFERENCES people(id) ON DELETE CASCADE);
CREATE TABLE events (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT, visibility TEXT NOT NULL DEFAULT 'family');
CREATE TABLE event_attendees (id TEXT PRIMARY KEY, event_id TEXT REFERENCES events(id) ON DELETE CASCADE, user_id TEXT, display_name TEXT,
    person_id TEXT REFERENCES people(id) ON DELETE SET NULL);
CREATE TABLE ambient_memory (id INTEGER PRIMARY KEY, transcript TEXT NOT NULL, speaker_id TEXT, user_id TEXT);
CREATE TABLE speaker_profiles (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, embedding_blob BLOB, consent_at TEXT, model_id TEXT);
CREATE TABLE face_profiles (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, embedding_blob BLOB, active INTEGER DEFAULT 1);
CREATE TABLE panel_voice_clusters (id TEXT PRIMARY KEY, user_id TEXT);
CREATE TABLE panel_voice_samples (id TEXT PRIMARY KEY, cluster_id TEXT REFERENCES panel_voice_clusters(id) ON DELETE CASCADE);
"""

#: single-author tables: (table, column holding words)
OWNED = [("chat_feedback", "corrected_response"), ("background_tasks", "task"), ("ui_actions", "payload"), ("notes", "content"),
         ("journal_entries", "content"), ("transactions", "description"), ("reminders", "title"), ("proactive_pending", "message"),
         ("voice_announcements", "message"), ("openclaw_approvals", "request_text"), ("openclaw_run_state", "request_text")]


def _meta(user):
    return json.dumps({"user_id": user})


async def _seed(db):
    x = db.execute
    for u in (A, B):
        await x("INSERT INTO people VALUES (?, ?, 'Private Contact', 'personal')", (f"pp-{u}", u))
    for u in (A, B):
        for t, col in OWNED:
            if t == "background_tasks":
                await x(f"INSERT INTO {t} (user_id, {col}) VALUES (?, 'synthetic words')", (u,))
            else:
                await x(f"INSERT INTO {t} (id, user_id, {col}) VALUES (?, ?, 'synthetic words')", (f"{t}-{u}", u))
        await x("INSERT INTO proactive_scheduled (id, user_id, message, apscheduler_job_id) VALUES (?, ?, 'take the bins out', ?)",
                (f"ps-{u}", u, f"job-{u}"))
        await x("INSERT INTO memory_consolidation_state VALUES (?, ?, 'now', 1)", (f"state-{u}", u))
        await x("INSERT INTO ambient_memory (transcript, user_id) VALUES ('overheard words', ?)", (u,))
        await x("INSERT INTO speaker_profiles VALUES (?, ?, x'00', 'now', 'resemblyzer')", (f"spk-{u}", u))
        await x("INSERT INTO face_profiles VALUES (?, ?, x'00', 1)", (f"face-{u}", u))
        await x("INSERT INTO notes (id, user_id, content, person_id) VALUES (?, ?, 'about my contact', ?)", (f"note-p-{u}", u, f"pp-{u}"))
        await x("INSERT INTO journal_entries (id, user_id, content, person_id) VALUES (?, ?, 'saw my contact', ?)",
                (f"jr-p-{u}", u, f"pp-{u}"))
        await x("INSERT INTO ambient_memory (transcript, speaker_id) VALUES ('legacy words', ?)", (f"spk-{u}",))
        await x("INSERT INTO panel_voice_clusters VALUES (?, ?)", (f"pvc-{u}", u))
        await x("INSERT INTO panel_voice_samples VALUES (?, ?)", (f"pvs-{u}", f"pvc-{u}"))
        # private things of each member
        await x("INSERT INTO lists VALUES (?, ?, 'private list', 'personal')", (f"list-{u}", u))
        await x("INSERT INTO list_items VALUES (?, ?, 'private item', NULL)", (f"li-{u}", f"list-{u}"))
        await x("INSERT INTO people_field_values VALUES (?, ?, '1')", (f"pfv-{u}", f"pp-{u}"))
        await x("INSERT INTO person_gift_ideas VALUES (?, ?, ?, 'a kite')", (f"gift-priv-{u}", f"pp-{u}", u))
        await x("INSERT INTO events VALUES (?, ?, 'private appointment', 'personal')", (f"ev-{u}", u))
        await x("INSERT INTO event_attendees (id, event_id, user_id, display_name) VALUES (?, ?, ?, 'self')", (f"att-priv-{u}", f"ev-{u}", u))
    # ANOTHER member's note pointing at A's private contact (alembic 0006: a plain REFERENCES, no ON DELETE)
    await x("INSERT INTO notes (id, user_id, content, person_id) VALUES ('note-b-on-a', ?, 'B wrote this', ?)", (B, "pp-" + A))
    # household-SHARED objects created by A, with entries from both
    await x("INSERT INTO lists VALUES ('list-family', ?, 'household shopping', 'family')", (A,))
    await x("INSERT INTO list_items VALUES ('li-fam-1', 'list-family', 'milk', ?)", (A,))
    await x("INSERT INTO list_items VALUES ('li-fam-2', 'list-family', 'bread', ?)", (B,))
    await x("INSERT INTO people VALUES ('pp-family', ?, 'Family Friend', 'family')", (A,))
    await x("INSERT INTO people_field_values VALUES ('pfv-family', 'pp-family', '2')")
    await x("INSERT INTO person_gift_ideas VALUES ('gift-fam-a', 'pp-family', ?, 'a book')", (A,))
    await x("INSERT INTO person_gift_ideas VALUES ('gift-fam-b', 'pp-family', ?, 'a scarf')", (B,))
    await x("INSERT INTO person_relationships VALUES ('rel-a', ?, 'pp-family', 'pp-family')", (A,))
    await x("INSERT INTO person_relationships VALUES ('rel-b', ?, 'pp-family', 'pp-family')", (B,))
    await x("INSERT INTO events VALUES ('ev-family', ?, 'family picnic', 'family')", (A,))
    await x("INSERT INTO event_attendees (id, event_id, user_id, display_name) VALUES ('att-fam-a', 'ev-family', ?, 'A')", (A,))
    await x("INSERT INTO event_attendees (id, event_id, user_id, display_name) VALUES ('att-fam-b', 'ev-family', ?, 'B')", (B,))
    # chat: A's own session; a kiosk session only A spoke in; a session A owns that B also spoke in; B's session with an A turn
    await x("INSERT INTO chat_sessions VALUES ('s-a', ?, 'about my plans')", (A,))
    await x("INSERT INTO chat_messages (id, session_id, role, content) VALUES ('m1', 's-a', 'user', 'my plans for the weekend')")
    await x("INSERT INTO chat_messages (id, session_id, role, content) VALUES ('m2', 's-a', 'assistant', 'sounds lovely')")
    await x("INSERT INTO chat_ag_ui_runs VALUES ('run-a', 's-a', 'r', '[]')")
    await x("INSERT INTO chat_sessions VALUES ('s-kiosk', 'guest', 'kiosk')")
    await x("INSERT INTO chat_messages (id, session_id, role, content, metadata) VALUES ('m3', 's-kiosk', 'user', 'set a timer', ?)", (_meta(A),))
    await x("INSERT INTO chat_sessions VALUES ('s-shared', ?, 'my private title')", (A,))
    await x("INSERT INTO chat_messages (id, session_id, role, content) VALUES ('m4', 's-shared', 'user', 'A speaks')")
    await x("INSERT INTO chat_messages (id, session_id, role, content, metadata) VALUES ('m5', 's-shared', 'user', 'B speaks', ?)", (_meta(B),))
    await x("INSERT INTO chat_ag_ui_runs VALUES ('run-shared', 's-shared', 'r', '[]')")
    await x("INSERT INTO chat_sessions VALUES ('s-b', ?, 'B chat')", (B,))
    await x("INSERT INTO chat_messages (id, session_id, role, content) VALUES ('m6', 's-b', 'user', 'B again')")
    await x("INSERT INTO chat_messages (id, session_id, role, content, metadata) VALUES ('m7', 's-b', 'user', 'A at B''s panel', ?)", (_meta(A),))
    await db.commit()


@pytest.fixture
def store(monkeypatch, tmp_path):
    async def open_():
        db = await aiosqlite.connect(str(tmp_path / "zoe.db"))
        db.row_factory = aiosqlite.Row                          # rows by index AND by name, like the pool's records
        await db.execute("PRAGMA foreign_keys = ON")           # Postgres enforces them: so does this store
        await db.executescript(_DDL)
        await _seed(db)
        return db
    db = asyncio.run(open_())
    clips = tmp_path / "voice-samples"
    clips.mkdir()
    for u in (A, B):                                        # one labelled member clip each, plus a legacy clip with no label
        (clips / f"turn-{u}.wav").write_bytes(b"RIFF")
        (clips / f"turn-{u}.verdict.json").write_text(json.dumps({"v": 1, "kind": "member", "id": u, "cluster": "", "ts": 1}))
    (clips / "legacy.wav").write_bytes(b"RIFF")
    monkeypatch.setenv("ZOE_VOICE_SAMPLE_DIR", str(clips))
    db.clips = clips

    @contextlib.asynccontextmanager
    async def ctx():
        yield db
    monkeypatch.setattr(mof, "_db", ctx)
    cancelled = []
    import proactive.scheduler as sched

    def cancel_job(job_id):
        cancelled.append(job_id)
        return sched.CancelResult.REMOVED
    monkeypatch.setattr(sched, "cancel_job", cancel_job)
    db.cancelled = cancelled
    yield db
    asyncio.run(db.close())


def _q(db, sql, params=()):
    async def go():
        async with db.execute(sql, params) as cur:
            return [tuple(r) for r in await cur.fetchall()]
    return asyncio.run(go())


def _ids(db, table, col="id"):
    return {r[0] for r in _q(db, f"SELECT {col} FROM {table}")}


def _delete(svc):
    return asyncio.run(svc.delete_user(A, actor="test", reason="synthetic member delete"))


def test_a_member_delete_removes_every_single_author_row_of_the_member_and_none_of_another(env, store):
    svc, _col = env
    _delete(svc)
    for t, _c in OWNED + [("proactive_scheduled", ""), ("memory_consolidation_state", ""), ("panel_voice_clusters", "")]:
        assert {r[0] for r in _q(store, f"SELECT user_id FROM {t}")} == {B}, t
    assert _ids(store, "panel_voice_samples") == {"pvs-" + B}
    assert {r[0] for r in _q(store, "SELECT transcript FROM ambient_memory")} == {"overheard words", "legacy words"}
    assert len(_q(store, "SELECT id FROM ambient_memory")) == 2, "B's two recordings (by user_id and by B's speaker profile) stay"


def test_the_transcript_goes_with_no_orphans_and_shared_sessions_keep_the_other_members_turns(env, store):
    svc, _col = env
    _delete(svc)
    assert _ids(store, "chat_messages") == {"m5", "m6"}, "every turn A owned goes; B's turns stay, wherever they are"
    assert _ids(store, "chat_sessions") == {"s-shared", "s-b"}, "A's emptied session and the kiosk session A alone spoke in go"
    assert _q(store, "SELECT title FROM chat_sessions WHERE id = 's-shared'") == [("New Chat",)], "the shared session loses A's title"
    assert _ids(store, "chat_ag_ui_runs") == {"run-shared"}, "the runs of an emptied session go (a shared one's stay: authorship gap)"
    assert not _q(store, "SELECT m.id FROM chat_messages m LEFT JOIN chat_sessions s ON s.id = m.session_id WHERE s.id IS NULL")
    assert not _q(store, "SELECT s.id FROM chat_sessions s WHERE NOT EXISTS (SELECT 1 FROM chat_messages m WHERE m.session_id = s.id)")


def test_shared_objects_stay_and_lose_only_what_the_member_authored(env, store):
    svc, _col = env
    _delete(svc)
    assert _ids(store, "lists") == {"list-family", "list-" + B}, "A's private list goes; the family list stays"
    assert _ids(store, "list_items") == {"li-fam-1", "li-fam-2", "li-" + B}, "a family list's items record no author: they stay"
    assert _q(store, "SELECT assigned_to FROM list_items WHERE id = 'li-fam-1'") == [(None,)], "A's assignment is cleared"
    assert _ids(store, "people") == {"pp-family", "pp-" + B}
    assert _ids(store, "people_field_values") == {"pfv-family", "pfv-" + B}, "authorship gap: a shared contact's field values stay"
    assert _ids(store, "person_gift_ideas") == {"gift-fam-b", "gift-priv-" + B}, "A's entries go on every contact"
    assert _ids(store, "person_relationships") == {"rel-b"}
    assert _ids(store, "events") == {"ev-family", "ev-" + B}, "A's personal event goes; the family event stays"
    assert _ids(store, "event_attendees") == {"att-fam-b", "att-priv-" + B}


def test_the_members_scheduled_sends_are_consumed_and_cancelled_before_they_go(env, store):
    svc, _col = env
    _delete(svc)
    assert store.cancelled == ["job-" + A], "only A's job is cancelled"
    assert _q(store, "SELECT id, fired FROM proactive_scheduled") == [("ps-" + B, 0)], "B's reminder still fires"


def test_a_later_transcript_miner_finds_nothing_of_the_deleted_member(env, store, monkeypatch):
    """The idle sweep reads a session's turns: after the delete there are none of A's to read in any session."""
    import memory_idle_consolidation as mic

    svc, col = env
    _delete(svc)

    class _Conn:
        def __init__(self, sid):
            self.sid = sid

        async def fetch(self, *_a):
            async with store.execute("SELECT role, content, metadata, created_at FROM chat_messages WHERE session_id = ?",
                                     (self.sid,)) as cur:
                rows = await cur.fetchall()
            return [{"role": r, "content": c, "metadata": m, "at": at} for r, c, m, at in rows]

        async def execute(self, *_a):
            pass

    async def none(_text):
        raise AssertionError("no turn of A's is left to extract from")

    import memory_digest
    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", none)
    for sid in ("s-a", "s-kiosk"):
        @contextlib.asynccontextmanager
        async def ctx(sid=sid):
            yield _Conn(sid)
        assert asyncio.run(mic.consolidate_session(sid, A, get_ctx=ctx)) == 0
    assert _rows(col, A) == []


def test_a_store_that_cannot_be_cleared_fails_the_delete(env, monkeypatch):
    svc, col = env
    asyncio.run(svc.ingest("User keeps hens", user_id=A, source="digest", memory_type="profile", confidence=0.82, status="approved"))

    @contextlib.asynccontextmanager
    async def down():
        raise ConnectionError("store down")
        yield  # pragma: no cover

    monkeypatch.setattr(mof, "_db", down)
    with pytest.raises(memory_service.MemoryServiceError, match="member-owned store erasure failed"):
        _delete(svc)
    assert [d for d, _ in _rows(col, A)] == ["User keeps hens"], "nothing of the palace was erased: the caller retries"


def test_a_table_that_was_never_migrated_is_nothing_to_clear(env, monkeypatch, tmp_path):
    svc, _col = env

    async def open_():
        db = await aiosqlite.connect(str(tmp_path / "empty.db"))
        await db.execute("CREATE TABLE notes (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, content TEXT)")
        await db.execute("INSERT INTO notes VALUES ('n', ?, 'x')", (A,))
        await db.commit()
        return db
    db = asyncio.run(open_())

    @contextlib.asynccontextmanager
    async def ctx():
        yield db
    monkeypatch.setattr(mof, "_db", ctx)
    _delete(svc)
    assert _q(db, "SELECT COUNT(*) FROM notes") == [(0,)]
    asyncio.run(db.close())


# ── contacts with references from other rows (foreign keys enforced, as on Postgres) ─────────────────────────────────────────

def test_a_private_contact_goes_after_the_rows_that_point_at_it_and_another_members_note_keeps_its_row(env, store):
    svc, _col = env
    _delete(svc)                                    # PRAGMA foreign_keys = ON: a parent-first order raises here
    assert "pp-" + A not in _ids(store, "people")
    assert _q(store, "SELECT user_id, person_id FROM notes WHERE id = 'note-b-on-a'") == [(B, None)], "B's note stays, unlinked"
    assert _q(store, "SELECT person_id FROM notes WHERE id = ?", ("note-p-" + B,)) == [("pp-" + B,)], "B's own link is untouched"
    assert not _q(store, "SELECT id FROM journal_entries WHERE user_id = ?", (A,))


def _migration_fks():
    """(child, column, parent, on_delete) for every foreign key the migrations declare (CREATE TABLE bodies, ALTER TABLE ... REFERENCES).
    ``on_delete`` is CASCADE, SET NULL or "" (none: the parent delete fails while a child row points at it)."""
    import glob
    import re
    from pathlib import Path

    def rule(rest):
        u = rest.upper()
        return "CASCADE" if "ON DELETE CASCADE" in u else ("SET NULL" if "ON DELETE SET NULL" in u else "")

    out = set()
    for f in sorted(glob.glob(str(Path(memory_service.__file__).parent / "alembic" / "versions" / "*.py"))):
        t = Path(f).read_text(encoding="utf-8")
        for m in re.finditer(r"CREATE TABLE(?: IF NOT EXISTS)?\s+(\w+)\s*\((.*?)\n\s*\)", t, re.S):
            for line in m.group(2).split("\n"):
                l = line.strip().rstrip(",")
                fk = (re.match(r"FOREIGN KEY\s*\((\w+)\)\s*REFERENCES\s+(\w+)(.*)", l, re.I)
                      or re.match(r"(\w+)\s+\w+.*?REFERENCES\s+(\w+)(.*)", l, re.I))
                if fk:
                    out.add((m.group(1), fk.group(1), fk.group(2), rule(fk.group(3))))
        for fk in re.finditer(r"ALTER TABLE (?:IF EXISTS )?(\w+)\s+ADD COLUMN (?:IF NOT EXISTS )?(\w+)[^\n;\"]*?REFERENCES\s+(\w+)([^\n;\"]*)", t, re.I):
            out.add((fk.group(1), fk.group(2), fk.group(3), rule(fk.group(4))))
    return out


#: tables that reference a table this eraser deletes from, are not deleted from by it, and have no ON DELETE rule: none today. A new one
#: must be erased first, NULLed (``member_owned_forget.NULLED``) or listed here with the reason it can never point at a deleted row.
FK_ALLOWLIST: dict = {}


def _fk_violations(fks, order) -> list:
    """Every way a delete in ``order`` could break a foreign key: a parent deleted before its child, or a child this eraser never touches
    that points at a deleted table with no ON DELETE rule and is neither NULLed nor allowlisted."""
    deleted = set(order)
    nulled = {(t, "person_id") for t in mof.NULLED}
    bad = []
    for child, col, parent, on_delete in sorted(fks):
        if parent not in deleted or child == parent:
            continue
        if child in deleted:
            if max(i for i, t in enumerate(order) if t == child) > min(i for i, t in enumerate(order) if t == parent):
                bad.append(f"parent first: {child}.{col} -> {parent}")
        elif (child, col) in nulled or on_delete or child in FK_ALLOWLIST:
            continue
        else:
            bad.append(f"unhandled: {child}.{col} -> {parent}")
    return bad


def test_the_erase_order_deletes_every_child_before_its_parent_per_the_migrations(env, store):
    """Derived from the migration files, so a future table with a foreign key cannot reintroduce a parent-first delete or an unhandled
    reference."""
    svc, _col = env
    _delete(svc)
    order = list(mof.last_order)
    fks = _migration_fks()
    assert ("notes", "person_id", "people", "") in fks and ("journal_entries", "person_id", "people", "") in fks, "the parser sees 0006"
    assert ("list_items", "list_id", "lists", "CASCADE") in fks
    assert _fk_violations(fks, order) == []
    # the check is not vacuous: an edge the eraser does not handle fails it, and so does a parent-first order
    assert _fk_violations(fks | {("a_new_table", "person_id", "people", "")}, order) == ["unhandled: a_new_table.person_id -> people"]
    assert _fk_violations(fks, list(reversed(order)))


# ── biometrics: rows, labelled clips, the panels, a stale claim ──────────────────────────────────────────────────────────────

def test_the_members_biometrics_go_rows_and_files_and_another_members_stay(env, store):
    svc, _col = env
    _delete(svc)
    assert _ids(store, "speaker_profiles") == {"spk-" + B} and _ids(store, "face_profiles") == {"face-" + B}
    left = sorted(p.name for p in store.clips.iterdir())
    assert left == ["legacy.wav", f"turn-{B}.verdict.json", f"turn-{B}.wav"], "A's labelled clip and label go; B's and a legacy clip stay"


def test_a_clip_that_cannot_be_removed_fails_the_delete(env, store, monkeypatch):
    import voice_clips

    svc, _col = env
    monkeypatch.setattr(voice_clips, "_remove", lambda *_a: False)
    with pytest.raises(memory_service.MemoryServiceError, match="member-owned store erasure failed"):
        _delete(svc)


def test_a_stale_panel_claim_cannot_verify_the_deleted_member(env, store, monkeypatch):
    """The panel's cached copy may still match A until it drops it; the server's claim gate reads the store, so the claim is refused."""
    import db_compat
    from routers import voice_tts

    svc, _col = env

    @contextlib.asynccontextmanager
    async def ctx():
        yield store
    def claim(uid):
        with monkeypatch.context() as m:              # the claim gate reads this store; the erase's other erasers keep theirs
            m.setattr(db_compat, "get_compat_db", ctx)
            return asyncio.run(voice_tts._voice_claim_consented(uid))
    assert claim(A) is True, "control: A's consented profile verifies before the delete"
    _delete(svc)
    assert claim(A) is False and claim(B) is True


# ── blocker 3/4: transcript discovery is JSON-aware and portable ─────────────────────────────────────────────────────────────

def test_every_json_spelling_of_the_owner_is_found_and_an_id_inside_text_is_not_ownership(env, store):
    svc, _col = env

    async def seed():
        for mid, meta, content in (
                ("v1", '{"user_id":"%s"}' % A, "compact"),
                ("v2", '{ "user_id" : "%s" }' % A, "spaced"),
                ("v3", '{"panel": "p1",\n  "user_id":   "%s", "x": 2}' % A, "key order and newlines"),
                ("v4", json.dumps({"user_id": B, "note": "said to " + A}), "B's turn whose metadata merely mentions A"),
                ("v5", None, "a turn in B's session that says " + A + " in its text")):
            await store.execute("INSERT INTO chat_messages (id, session_id, role, content, metadata) VALUES (?, 's-b', 'user', ?, ?)",
                                (mid, content, meta))
        await store.commit()
    asyncio.run(seed())
    _delete(svc)
    left = _ids(store, "chat_messages")
    assert not {"v1", "v2", "v3"} & left, "every spelling of A's ownership is found and deleted"
    assert {"v4", "v5"} <= left, "an id inside text or inside another member's metadata is not ownership"


def test_the_transcript_query_renders_for_postgres(env):
    """No Postgres here: the SQL is rendered through the pool's own positional adapter and checked (the test_memory_digest_postgres_sql
    pattern). ``metadata`` is TEXT (alembic 0001); the CAST keeps the LIKE text on any column type, and every bind is a placeholder."""
    import db_pool

    params = (A, *mof._id_patterns(A))
    sql, args = db_pool._adapt_params(mof.TRANSCRIPT_SQL, params)
    assert "$1" in sql and "$2" in sql and "$3" in sql and "?" not in sql and list(args) == list(params)
    assert sql.count("CAST(cm.metadata AS TEXT) LIKE") == 2 and "cm.metadata LIKE" not in sql.replace("CAST(cm.metadata AS TEXT) LIKE", "")
    assert args[1] == f"%{A}%", "the prefilter is the bare id: it cannot miss any JSON spelling"


# ── blocker 1: the panel purge is queued or the delete fails ─────────────────────────────────────────────────────────────────

def test_a_forget_command_is_queued_for_every_panel_and_a_queue_failure_fails_the_delete(env, store, monkeypatch):
    import voice_announce

    svc, _col = env

    async def panel():
        await store.execute("INSERT INTO device_tokens VALUES ('t1', 'panel-1', 0)")
        await store.commit()
    asyncio.run(panel())
    real = voice_announce.enqueue_command

    async def down(*_a, **_k):
        raise ConnectionError("queue down")
    monkeypatch.setattr(voice_announce, "enqueue_command", down)
    with pytest.raises(memory_service.MemoryServiceError, match="member-owned store erasure failed"):
        _delete(svc)
    monkeypatch.setattr(voice_announce, "enqueue_command", real)
    _delete(svc)                                                     # the retry
    rows = _q(store, "SELECT user_id, panel_id, trigger_type FROM voice_announcements WHERE trigger_type = 'voice_profile_forget'")
    assert rows == [(A, "panel-1", "voice_profile_forget")]


# ── blocker 2: a reminder fire that claimed its row before the delete delivers nothing ───────────────────────────────────────

@pytest.mark.parametrize("guarded", [True, False], ids=["fire-time-guard", "control-guard-bypassed"])
def test_a_fire_that_claimed_before_the_delete_delivers_nothing_for_the_deleted_member(env, store, monkeypatch, guarded):
    from proactive.triggers import reminders

    svc, _col = env

    @contextlib.asynccontextmanager
    async def ctx():
        yield store
    monkeypatch.setattr(reminders, "_get_compat_db", ctx)
    delivered = []

    async def fire_notification(**kw):
        delivered.append(kw["user_id"])
    monkeypatch.setattr("proactive.engine.fire_notification", fire_notification)
    real_guard = reminders._member_erased_since_row
    gate = {}

    async def held(uid, sid):
        gate["claimed"].set()                     # the row is CLAIMED (claimed_at set) ...
        await gate["release"].wait()              # ... and the member delete runs to the end here
        return await real_guard(uid, sid) if guarded else False
    monkeypatch.setattr(reminders, "_member_erased_since_row", held)

    async def go():
        gate["claimed"], gate["release"] = asyncio.Event(), asyncio.Event()
        fire = asyncio.ensure_future(reminders._fire_reminder("ps-" + A, A, "take the bins out"))
        await asyncio.wait_for(gate["claimed"].wait(), 10)
        async with store.execute("SELECT claimed_at FROM proactive_scheduled WHERE id = ?", ("ps-" + A,)) as cur:
            assert (await cur.fetchone())[0], "the fire holds the claim"
        await svc.delete_user(A, actor="test", reason="synthetic member delete")
        gate["release"].set()
        await asyncio.wait_for(fire, 10)

    asyncio.run(go())
    assert delivered == ([] if guarded else [A])


def test_a_reminder_of_a_live_member_still_fires(env, store, monkeypatch):
    from proactive.triggers import reminders

    @contextlib.asynccontextmanager
    async def ctx():
        yield store
    monkeypatch.setattr(reminders, "_get_compat_db", ctx)
    delivered = []

    async def fire_notification(**kw):
        delivered.append(kw["user_id"])
    monkeypatch.setattr("proactive.engine.fire_notification", fire_notification)
    asyncio.run(reminders._fire_reminder("ps-" + B, B, "take the bins out"))
    assert delivered == [B]


# ── blocker 6: a stale panel face match vouches for nothing ──────────────────────────────────────────────────────────────────

def test_a_stale_face_match_no_longer_corroborates_the_deleted_member(env, store):
    import datetime as dt

    import speaker_adaptation as sa

    svc, _col = env
    cfg = sa.AdaptConfig()

    def vouches(uid):
        claims = {"face_user_id": uid, "face_score": 0.99}       # what a panel's cached copy still reports
        return asyncio.run(sa._verify_corroboration(store, panel_id="panel-1", user_id=uid, claims=claims,
                                                    now=dt.datetime.now(dt.timezone.utc), cfg=cfg))
    assert vouches(A) == ["face"], "control: A's face vouches before the delete"
    _delete(svc)
    assert vouches(A) == [] and vouches(B) == ["face"]


def test_a_panel_face_match_is_read_by_the_server_only_as_speaker_adaptation_corroboration():
    """Pinned from the source: ``face_user_id`` (the panel's face match) is consumed by ONE server module, and it is not an identity claim."""
    from pathlib import Path

    root = Path(memory_service.__file__).parent
    readers = sorted(str(p.relative_to(root)) for p in root.rglob("*.py")
                     if "tests" not in p.parts and "face_user_id" in p.read_text(encoding="utf-8", errors="ignore"))
    assert readers == ["speaker_adaptation.py"], readers


def test_a_scheduler_cancel_error_fails_the_delete(env, store, monkeypatch):
    """Only "this process runs no scheduler" is not an error (the consumed row cannot fire, and the fire-time guard refuses it);
    a jobstore failure fails the delete closed, so it is retried."""
    import proactive.scheduler as sched

    svc, _col = env

    def broken(_job):
        raise ConnectionError("jobstore down")
    monkeypatch.setattr(sched, "cancel_job", broken)
    with pytest.raises(memory_service.MemoryServiceError, match="member-owned store erasure failed"):
        _delete(svc)

    def absent(_job):
        raise RuntimeError("Proactive scheduler not started - call start_scheduler() first")
    monkeypatch.setattr(sched, "cancel_job", absent)
    _delete(svc)
    assert _q(store, "SELECT user_id FROM proactive_scheduled") == [(B,)]
