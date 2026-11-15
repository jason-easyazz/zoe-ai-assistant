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
CREATE TABLE chat_sessions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT 'New Chat', created_at TEXT,
    updated_at TEXT);
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
CREATE TABLE proactive_pending (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, message TEXT NOT NULL, trigger_type TEXT DEFAULT '',
    item_id TEXT DEFAULT '', trigger_context TEXT, expires_at TEXT DEFAULT '2999-01-01T00:00:00Z', claimed INTEGER DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
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
    await x("INSERT INTO chat_sessions (id, user_id, title) VALUES ('s-a', ?, 'about my plans')", (A,))
    await x("INSERT INTO chat_messages (id, session_id, role, content) VALUES ('m1', 's-a', 'user', 'my plans for the weekend')")
    await x("INSERT INTO chat_messages (id, session_id, role, content) VALUES ('m2', 's-a', 'assistant', 'sounds lovely')")
    await x("INSERT INTO chat_ag_ui_runs VALUES ('run-a', 's-a', 'r', '[]')")
    await x("INSERT INTO chat_sessions (id, user_id, title) VALUES ('s-kiosk', 'guest', 'kiosk')")
    await x("INSERT INTO chat_messages (id, session_id, role, content, metadata) VALUES ('m3', 's-kiosk', 'user', 'set a timer', ?)", (_meta(A),))
    await x("INSERT INTO chat_sessions (id, user_id, title) VALUES ('s-shared', ?, 'my private title')", (A,))
    await x("INSERT INTO chat_messages (id, session_id, role, content) VALUES ('m4', 's-shared', 'user', 'A speaks')")
    await x("INSERT INTO chat_messages (id, session_id, role, content, metadata) VALUES ('m5', 's-shared', 'user', 'B speaks', ?)", (_meta(B),))
    await x("INSERT INTO chat_ag_ui_runs VALUES ('run-shared', 's-shared', 'r', '[]')")
    await x("INSERT INTO chat_sessions (id, user_id, title) VALUES ('s-b', ?, 'B chat')", (B,))
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
    assert _q(store, "SELECT user_id, title FROM chat_sessions WHERE id = 's-shared'") == [(B, "New Chat")], \
        "the shared session loses A's title and is handed to B, who is still in it"
    assert _ids(store, "chat_ag_ui_runs") == set(), "the runs of an emptied session go, and a shared one's that nobody else started"
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
        await store.execute("UPDATE proactive_scheduled SET fired = 1 WHERE id = ?", (kw["pending_id"],))
        await store.commit()
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
        delete = asyncio.ensure_future(svc.delete_user(A, actor="test", reason="synthetic member delete"))
        await asyncio.sleep(0.4)                  # the delete has written its mark and now waits for the in-flight claim
        assert not delete.done(), "a delete cannot finish while a send of the member is mid-delivery"
        gate["release"].set()                     # the fire's check now runs AFTER the mark
        await asyncio.wait_for(fire, 10)
        await asyncio.wait_for(delete, 10)

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


# ── B1: the delete cannot finish between a fire's check and its delivery ─────────────────────────────────────────────────────

@pytest.mark.parametrize("wait", [True, False], ids=["delete-waits-for-the-claim", "control-no-wait"])
def test_a_send_that_passed_its_check_is_delivered_before_the_delete_finishes_never_after(env, store, monkeypatch, wait):
    """The fire claims its row and passes its erase check BEFORE the member delete starts; it is held between the check and the
    delivery. The delete must not be able to finish in that window (it waits for the claim), so no send reaches the member after
    delete_user returned. The control (no wait) finishes the delete in the window and the send lands after it - the old code."""
    from proactive.triggers import reminders

    svc, _col = env

    @contextlib.asynccontextmanager
    async def ctx():
        yield store
    monkeypatch.setattr(reminders, "_get_compat_db", ctx)
    events, gate = [], {}

    async def fire_notification(**kw):
        gate["checked"].set()                     # past the check, about to deliver
        await gate["release"].wait()
        events.append("delivered")
        await store.execute("UPDATE proactive_scheduled SET fired = 1 WHERE id = ?", (kw["pending_id"],))
        await store.commit()
    monkeypatch.setattr("proactive.engine.fire_notification", fire_notification)
    if not wait:
        async def no_wait(*_a, **_k):
            return None
        monkeypatch.setattr(mof, "_await_inflight_fires", no_wait)

    async def go():
        gate["checked"], gate["release"] = asyncio.Event(), asyncio.Event()
        fire = asyncio.ensure_future(reminders._fire_reminder("ps-" + A, A, "take the bins out"))
        await asyncio.wait_for(gate["checked"].wait(), 10)

        async def delete():
            await svc.delete_user(A, actor="test", reason="synthetic member delete")
            events.append("delete_done")
        d = asyncio.ensure_future(delete())
        await asyncio.sleep(0.4)
        gate["release"].set()
        await asyncio.wait_for(fire, 10)
        await asyncio.wait_for(d, 10)

    asyncio.run(go())
    assert events == (["delivered", "delete_done"] if wait else ["delete_done", "delivered"])


def test_a_delete_that_waits_too_long_for_a_send_fails_closed(env, store, monkeypatch):
    svc, _col = env
    monkeypatch.setattr(mof, "INFLIGHT_WAIT_S", 0.3)

    async def claim():
        await store.execute("UPDATE proactive_scheduled SET claimed_at = ? WHERE id = ?",
                            (__import__("proactive.triggers.reminders", fromlist=["x"])._utc_iso(
                                __import__("datetime").datetime.now(__import__("datetime").timezone.utc)), "ps-" + A))
        await store.commit()
    asyncio.run(claim())
    with pytest.raises(memory_service.MemoryServiceError, match="member-owned store erasure failed"):
        _delete(svc)


# ── B1: a queued notification claimed across the delete never seeds a session for the deleted member ───────────────────────

class _TxDb:
    """The compat connection's surface claim_pending uses, over the test store (``transaction`` commits on exit)."""

    def __init__(self, db):
        self._db = db

    def execute(self, sql, params=()):
        return self._db.execute(sql, params)

    async def commit(self):
        await self._db.commit()

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield
        await self._db.commit()


@pytest.mark.parametrize("post_check", [True, False], ids=["post-seed-check", "control-no-post-check"])
def test_a_notification_claimed_before_the_delete_and_seeded_after_it_leaves_nothing(env, store, monkeypatch, post_check):
    from proactive import session_utils

    svc, _col = env

    @contextlib.asynccontextmanager
    async def ctx():
        yield _TxDb(store)
    monkeypatch.setattr(session_utils, "_get_compat_db", ctx)
    real, calls = session_utils._erased_member, {"n": 0}

    async def erased(row, db, pid):
        calls["n"] += 1
        if calls["n"] == 1:
            out = await real(row, db, pid)          # the claim is won and checked BEFORE the delete ...
            await svc.delete_user(A, actor="test", reason="synthetic member delete")   # ... which then runs to the end
            return out
        return await real(row, db, pid) if post_check else False
    monkeypatch.setattr(session_utils, "_erased_member", erased)
    got = asyncio.run(session_utils.claim_pending("proactive_pending-" + A))
    sessions = _q(store, "SELECT id FROM chat_sessions WHERE user_id = ? AND title = 'Proactive notification'", (A,))
    if post_check:
        assert got is None and sessions == [], "the seed that landed after the transcript step is undone"
    else:
        assert got is not None and len(sessions) == 1, "control: the deleted member gets a fresh session with Zoe's words"


# ── B2: every member eraser in delete_user fails closed ──────────────────────────────────────────────────────────────────────

_ERASERS = [("reply_ledger", "forget_user", "reply-ledger"), ("commitments", "forget_user", "commitment"),
            ("restraint", "erase_user", "restraint"), ("proactive.outcome_edges", "erase_user", "outcome-edge"),
            ("contacts_store", "forget_user", "contact-model"), ("forget_redact", "clear_turn_caches", "in-process turn caches"),
            ("value_correction", "forget_user", "in-process turn caches"), ("memory_provenance", "reset", "reply provenance")]


@pytest.mark.parametrize("mod,fn,label", _ERASERS, ids=[e[0] for e in _ERASERS])
def test_an_eraser_that_fails_fails_the_member_delete(env, monkeypatch, mod, fn, label):
    import importlib

    svc, _col = env
    target = importlib.import_module(mod)
    is_async = asyncio.iscoroutinefunction(getattr(target, fn))

    if is_async:
        async def boom(*_a, **_k):
            raise ConnectionError("store down")
    else:
        def boom(*_a, **_k):
            raise ConnectionError("store down")
    monkeypatch.setattr(target, fn, boom)
    with pytest.raises(memory_service.MemoryServiceError, match=label):
        _delete(svc)


@pytest.mark.parametrize("mod,fn", [("reply_ledger", "forget_user"), ("commitments", "forget_user"), ("restraint", "erase_user"),
                                    ("proactive.outcome_edges", "erase_user")])
def test_an_eraser_with_no_store_to_clear_does_not_fail_the_delete(env, monkeypatch, mod, fn):
    """Control: a process that never had a database pool (a lab / bare run) holds nothing to clear."""
    import importlib

    svc, _col = env

    async def no_pool(*_a, **_k):
        raise RuntimeError("db_pool not initialised — call init_pool() first")
    monkeypatch.setattr(importlib.import_module(mod), fn, no_pool)
    _delete(svc)


def test_a_warm_speaker_memo_that_cannot_be_rewritten_fails_the_delete(env, monkeypatch):
    import warm_speaker

    svc, _col = env

    def failed(_uid, **_k):
        warm_speaker._save_failed = True
        return 0
    monkeypatch.setattr(warm_speaker, "forget_user", failed)
    with pytest.raises(memory_service.MemoryServiceError, match="warm-speaker"):
        _delete(svc)


def test_the_reply_ledger_and_commitments_erasers_raise_instead_of_returning_zero(monkeypatch):
    import db_compat
    import reply_ledger
    import commitments

    @contextlib.asynccontextmanager
    async def down():
        raise ConnectionError("store down")
        yield  # pragma: no cover
    monkeypatch.setattr(db_compat, "get_compat_db", down)
    for m in (reply_ledger, commitments):
        with pytest.raises(ConnectionError):
            asyncio.run(m.forget_user(A))


# ── a shared session the member owned: no surviving row names them; the other member's turns and runs stay ──────────────────────

def _names(db, uid, tables=("chat_sessions", "chat_messages", "chat_ag_ui_runs", "openclaw_run_state")) -> list:
    """Every (table, row) of ``tables`` with ``uid`` in any column."""
    out = []
    for t in tables:
        for row in _q(db, f"SELECT * FROM {t}"):
            if any(uid in str(v) for v in row if v is not None):
                out.append((t, row))
    return out


def test_a_shared_session_the_member_owned_names_them_nowhere_after_the_delete(env, store):
    """Runs: one A started (``openclaw_run_state`` names A), one B started, one nobody can be shown to have started. A's turns and the
    runs not shown to be B's go; B's turns and B's run stay; the session is B's. The old code kept A as the owner and every run."""
    svc, _col = env

    async def seed():
        x = store.execute
        await x("INSERT INTO openclaw_run_state VALUES ('rs-a', ?, 'A asks')", (A,))
        await x("INSERT INTO openclaw_run_state VALUES ('rs-b', ?, 'B asks')", (B,))
        await x("INSERT INTO chat_messages (id, session_id, role, content, created_at) VALUES ('m4b', 's-shared', 'assistant', "
                "'a reply to A', '2026-01-01 00:00:02')")
        await x("INSERT INTO chat_ag_ui_runs VALUES ('run-sh-a', 's-shared', 'rs-a', '[\"a reply to A\"]')")
        await x("INSERT INTO chat_ag_ui_runs VALUES ('run-sh-b', 's-shared', 'rs-b', '[\"a reply to B\"]')")
        await x("INSERT INTO chat_ag_ui_runs VALUES ('run-b-a', 's-b', 'rs-a', '[\"a reply to A at B''s panel\"]')")
        await store.commit()
    asyncio.run(seed())
    assert _names(store, A), "control: A is named before the delete"
    _delete(svc)
    assert _names(store, A) == [], "no surviving chat row names A"
    assert _q(store, "SELECT user_id FROM chat_sessions WHERE id = 's-shared'") == [(B,)]
    assert {r[0] for r in _q(store, "SELECT id FROM chat_messages WHERE session_id = 's-shared'")} == {"m5"}, "B's turn stays; A's go"
    assert _ids(store, "chat_ag_ui_runs") == {"run-sh-b"}, "A's runs and the unattributable run go, in A's session and in B's"


def test_a_shared_session_goes_to_the_first_household_member_who_spoke_and_with_nobody_left_it_goes(env, store):
    svc, _col = env

    async def seed():
        x = store.execute
        await x("CREATE TABLE users (id TEXT PRIMARY KEY)")
        for u in (A, B, "member-c", "guest"):
            await x("INSERT INTO users VALUES (?)", (u,))
        await x("INSERT INTO chat_sessions (id, user_id, title) VALUES ('s-three', ?, 'A title')", (A,))
        for mid, who, at in (("t1", "guest", "2026-01-01 00:00:01"), ("t2", "member-c", "2026-01-01 00:00:02"),
                             ("t3", B, "2026-01-01 00:00:03"), ("t4", "member-c", "2026-01-01 00:00:04")):
            await x("INSERT INTO chat_messages (id, session_id, role, content, metadata, created_at) VALUES (?, 's-three', 'user', 'hi', ?, ?)",
                    (mid, _meta(who), at))
        await x("INSERT INTO chat_sessions (id, user_id, title) VALUES ('s-gone', ?, 'A title')", (A,))
        await x("INSERT INTO chat_messages (id, session_id, role, content, metadata) VALUES ('t5', 's-gone', 'user', 'hi', ?)",
                (_meta("not-a-user"),))
        await store.commit()
    asyncio.run(seed())
    _delete(svc)
    assert _q(store, "SELECT user_id FROM chat_sessions WHERE id = 's-three'") == [("member-c",)], \
        "the first household member who spoke (a guest who spoke earlier does not take it)"
    assert not _q(store, "SELECT id FROM chat_sessions WHERE id = 's-gone'") and "t5" not in _ids(store, "chat_messages"), \
        "no remaining participant is a user: the session goes with its turns"


# ── a claim older than the stuck timeout is not presumed dead; the last step of a send re-checks the erase ─────────────────────

class _FakeWebPush:
    """``pywebpush`` stand-in: records what would have left the box."""

    class WebPushException(Exception):
        pass

    def __init__(self):
        self.sent = []

    def webpush(self, subscription_info, data, **_k):
        self.sent.append(subscription_info["endpoint"])


@pytest.fixture
def push_lane(store, monkeypatch):
    """The real ``fire_notification`` -> ``send_push_to_user`` path over the test store, with a fake transport."""
    import sys
    from proactive import engine
    from proactive.triggers import reminders
    from routers import push

    @contextlib.asynccontextmanager
    async def ctx():
        yield store

    async def get_db():
        yield store

    async def seed():
        await store.execute("CREATE TABLE push_subscriptions (user_id TEXT, endpoint TEXT, keys_p256dh TEXT, keys_auth TEXT)")
        for u in (A, B):
            await store.execute("INSERT INTO push_subscriptions VALUES (?, ?, 'k', 'a')", (u, "https://push.invalid/" + u))
        await store.commit()
    asyncio.run(seed())
    fake = _FakeWebPush()
    monkeypatch.setitem(sys.modules, "pywebpush", fake)
    monkeypatch.setattr(push, "_get_vapid_keys", lambda: {"private_key": "k"})
    monkeypatch.setattr(push, "get_db", get_db)
    for mod in (reminders, engine):
        monkeypatch.setattr(mod, "_get_compat_db", ctx)
    monkeypatch.setattr(engine, "_is_in_quiet_hours", lambda *_a: False)
    monkeypatch.setattr(engine, "_reminder_phone_mode", lambda: "off")
    return fake


@pytest.mark.parametrize("fires_here", [True, False], ids=["scheduler-process", "no-scheduler-here"])
def test_a_live_delivery_older_than_the_stuck_timeout_holds_the_delete_and_sends_nothing_after_it(env, store, monkeypatch, push_lane,
                                                                                                   fires_here):
    """The fire passes its early erase check, then stalls (here: making its pending row) past the stuck-claim timeout. The delete must
    not report success while that claim is unresolved, and the stalled delivery, reaching its send after the erase, sends nothing.
    The old code presumed the old claim crashed: the delete finished first and the push went out after it."""
    from proactive import engine
    from proactive.triggers import reminders

    svc, _col = env
    monkeypatch.setattr(reminders, "_fires_here", lambda: fires_here)
    real_pending, gate, events = engine.create_pending, {}, []

    async def held_pending(**kw):
        gate["held"].set()
        await gate["release"].wait()
        return await real_pending(**kw)
    monkeypatch.setattr(engine, "create_pending", held_pending)
    monkeypatch.setattr("proactive.session_utils._get_compat_db", lambda: _ctx(store))

    async def go():
        gate["held"], gate["release"] = asyncio.Event(), asyncio.Event()
        fire = asyncio.ensure_future(reminders._fire_reminder("ps-" + A, A, "take the bins out"))
        await asyncio.wait_for(gate["held"].wait(), 10)
        await store.execute("UPDATE proactive_scheduled SET claimed_at = '2000-01-01T00:00:00Z' WHERE id = ?", ("ps-" + A,))
        await store.commit()                      # the claim is now far older than the stuck-claim timeout

        async def delete():
            await svc.delete_user(A, actor="test", reason="synthetic member delete")
            events.append("delete_done")
        d = asyncio.ensure_future(delete())
        await asyncio.sleep(0.4)
        assert not d.done(), "the delete does not report success while the claim is unresolved"
        gate["release"].set()
        await asyncio.wait_for(fire, 10)
        await asyncio.wait_for(d, 10)

    asyncio.run(go())
    assert events == ["delete_done"] and push_lane.sent == [], "nothing reached the deleted member"
    assert not _q(store, "SELECT id FROM proactive_pending WHERE user_id = ?", (A,)), "the pending row made across the delete is withdrawn"


def _ctx(db):
    @contextlib.asynccontextmanager
    async def ctx():
        yield db
    return ctx()


def test_a_dead_claim_is_proven_only_in_the_scheduler_process_and_does_not_block_there(env, store, monkeypatch):
    from proactive.triggers import reminders

    svc, _col = env
    monkeypatch.setattr(mof, "INFLIGHT_WAIT_S", 0.3)

    async def stale():
        await store.execute("UPDATE proactive_scheduled SET claimed_at = '2000-01-01T00:00:00Z' WHERE id = ?", ("ps-" + A,))
        await store.commit()
    asyncio.run(stale())
    monkeypatch.setattr(reminders, "_fires_here", lambda: False)
    with pytest.raises(memory_service.MemoryServiceError, match="member-owned store erasure failed"):
        _delete(svc)                              # no proof possible here: wait, then fail closed (the caller retries)
    monkeypatch.setattr(reminders, "_fires_here", lambda: True)
    _delete(svc)                                  # the process that fires reminders is not delivering it: a dead claim
    assert _q(store, "SELECT user_id FROM proactive_scheduled") == [(B,)]


def test_a_push_that_reaches_its_last_step_after_the_erase_sends_nothing(env, store, push_lane):
    """``send_push_to_user`` re-checks right before each push leaves. A member deleted after the delivery began gets nothing; a live
    member, and a call that carries no ``erase_since`` (the old behaviour), still send."""
    import erase_watermark
    from routers import push

    async def go():
        began = erase_watermark._now()
        await asyncio.sleep(0.01)
        await erase_watermark.record(A)
        return (await push.send_push_to_user(A, "x", erase_since=began), await push.send_push_to_user(B, "x", erase_since=began))
    assert asyncio.run(go()) == (0, 1)
    assert push_lane.sent == ["https://push.invalid/" + B]


def test_a_spoken_line_queued_across_the_delete_is_never_claimed_but_the_forget_command_is(tmp_path):
    import erase_watermark
    import voice_announce

    async def go():
        db = await aiosqlite.connect(str(tmp_path / "va.db"))
        db.row_factory = aiosqlite.Row
        await db.execute("CREATE TABLE voice_announcements (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, panel_id TEXT, message TEXT NOT NULL, "
                         "trigger_type TEXT, created_at TEXT, expires_at TEXT, delivered_at TEXT, delivered_to TEXT, played_at TEXT, "
                         "expired INTEGER DEFAULT 0)")
        await voice_announce.enqueue_announcement(db, user_id=A, message="a line for A", panel_id="p1", trigger_type="reminder")
        await voice_announce.enqueue_announcement(db, user_id=B, message="a line for B", panel_id="p1", trigger_type="reminder")
        await erase_watermark.record(A)           # the delete's mark, at or after A's line was queued
        await voice_announce.enqueue_command(db, user_id=A, panel_id="p1", trigger_type="voice_profile_forget", command={})
        got = await voice_announce.claim_announcements(db, panel_id="p1")
        async with db.execute("SELECT expired FROM voice_announcements WHERE message = 'a line for A'") as cur:
            expired = (await cur.fetchone())[0]
        await db.close()
        return got, expired
    got, expired = asyncio.run(go())
    assert [g["text"] for g in got if g["text"]] == ["a line for B"] and expired == 1
    assert [g["command"]["kind"] for g in got if g.get("command")] == ["voice_profile_forget"], "the delete's own command still reaches the panel"


# ── a duplicate fire for the same row cannot clear the original's in-flight mark ─────────────────────────────────────────────

def test_a_duplicate_fire_that_loses_the_claim_leaves_the_original_delivery_live(env, store, monkeypatch, push_lane):
    """The original delivery holds its claim and stalls; a duplicate run for the same row (a catch-up job, the missed-job listener)
    loses the claim and returns. The original is still live: the delete waits for it, and nothing is sent after the erase. With a plain
    set, the duplicate's exit dropped the row id and ``claim_is_dead`` declared the live delivery dead (the reviewed code)."""
    from proactive import engine
    from proactive.triggers import reminders

    svc, _col = env
    monkeypatch.setattr(reminders, "_fires_here", lambda: True)
    real_pending, gate, events = engine.create_pending, {}, []

    async def held_pending(**kw):
        gate["held"].set()
        await gate["release"].wait()
        return await real_pending(**kw)
    monkeypatch.setattr(engine, "create_pending", held_pending)
    monkeypatch.setattr("proactive.session_utils._get_compat_db", lambda: _ctx(store))

    async def go():
        gate["held"], gate["release"] = asyncio.Event(), asyncio.Event()
        fire = asyncio.ensure_future(reminders._fire_reminder("ps-" + A, A, "take the bins out"))
        await asyncio.wait_for(gate["held"].wait(), 10)
        await asyncio.wait_for(reminders._fire_reminder("ps-" + A, A, "take the bins out"), 10)   # the duplicate: no claim, returns
        assert not reminders.claim_is_dead("ps-" + A), "the original still holds the claim"

        async def delete():
            await svc.delete_user(A, actor="test", reason="synthetic member delete")
            events.append("delete_done")
        d = asyncio.ensure_future(delete())
        await asyncio.sleep(0.4)
        assert not d.done(), "the delete does not report success while the original delivery runs"
        gate["release"].set()
        await asyncio.wait_for(fire, 10)
        await asyncio.wait_for(d, 10)

    asyncio.run(go())
    assert events == ["delete_done"] and push_lane.sent == [], "nothing reached the deleted member"
    assert not reminders._IN_FLIGHT, "every run lowered its own count"


# ── a voice session that cannot be closed fails the delete ───────────────────────────────────────────────────────────────────

def test_a_voice_session_that_cannot_be_closed_fails_the_delete_and_a_retry_completes_it(env, store, monkeypatch):
    """``revoke_member`` never raises, so a failed close used to read as done while the member's verified voice session still opened
    private reads. Now the delete fails (after still queueing the panels' voice forget), and the retry closes it."""
    import voice_session

    svc, _col = env

    async def panel():
        await store.execute("INSERT INTO device_tokens VALUES ('t1', 'panel-1', 0)")
        await store.commit()
    asyncio.run(panel())
    voice_session.reset()
    voice_session._open_or_renew("panel-1", A, 0.0, "synthetic")
    real_close = voice_session.close_member

    def broken(_member, _reason):
        raise RuntimeError("session store broken")
    monkeypatch.setattr(voice_session, "close_member", broken)
    try:
        with pytest.raises(memory_service.MemoryServiceError, match="member-owned store erasure failed"):
            _delete(svc)
        assert voice_session.member_panels(A) == ["panel-1"], "the session really is still open"
        assert _q(store, "SELECT panel_id FROM voice_announcements WHERE trigger_type = 'voice_profile_forget'") == [("panel-1",)], \
            "every reachable step still ran: the panel was told to drop the voice"
        monkeypatch.setattr(voice_session, "close_member", real_close)
        _delete(svc)                                   # the retry
        assert voice_session.member_panels(A) == []
    finally:
        voice_session.reset()


# ── device links: the member's push endpoints and panel sign-ins go; a later push finds no endpoint ──────────────────────────

def test_the_members_push_endpoints_and_panel_sign_ins_go_and_a_later_push_finds_nothing(env, store, push_lane):
    from routers import push

    svc, _col = env

    async def seed():
        await store.execute("CREATE TABLE ui_panel_sessions (panel_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, chat_session_id TEXT, "
                            "page TEXT, ui_context TEXT, is_foreground INTEGER DEFAULT 1, last_seen_at TEXT)")
        for pid, who in (("panel-kitchen", A), ("panel-hall", B), ("panel-kiosk", "guest")):
            await store.execute("INSERT INTO ui_panel_sessions (panel_id, user_id, ui_context) VALUES (?, ?, '{}')", (pid, who))
        await store.commit()
    asyncio.run(seed())
    _delete(svc)
    assert {r[0] for r in _q(store, "SELECT user_id FROM push_subscriptions")} == {B}, "only B's endpoint stays"
    assert _q(store, "SELECT panel_id, user_id FROM ui_panel_sessions ORDER BY panel_id") == [("panel-hall", B), ("panel-kiosk", "guest")]

    async def later():
        return await push.send_push_to_user(A, "x"), await push.send_push_to_user(B, "x")
    assert asyncio.run(later()) == (0, 1)
    assert push_lane.sent == ["https://push.invalid/" + B], "a push to the deleted member finds no endpoint; B's still sends"
