"""Memory opt-out preference: `GET`/`PUT /api/memories/opt-out` + the extractor guard.

Regression pin for a real 500: `routers/memories.py` imported
`from user_prefs import ...` — a module that did not exist anywhere on the
box — so both endpoints raised `ModuleNotFoundError` on every call. The
helper now lives in `services/zoe-data/user_prefs.py`, backed by the existing
`user_preferences.prefs` JSON store (alembic 0001); no new table.

Negative control: run this file against origin/main's `routers/memories.py`
(the version without the fix) — the two endpoint tests fail with HTTP 500.

Slim-dep: a fake in-memory `user_preferences` table stands in for asyncpg,
`get_current_user` + `get_db` are overridden, and `require_feature_access`
is stubbed, so no pool, auth service, or model is touched. `ci_safe`.
"""
from __future__ import annotations

import json
import sys
import types

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.ci_safe

memories = pytest.importorskip("routers.memories")
memory_extractor = pytest.importorskip("memory_extractor")

from auth import get_current_user  # noqa: E402
from database import get_db  # noqa: E402

USER = {"user_id": "u-optout", "role": "user", "display_name": "Opt Out"}


@pytest.fixture(autouse=True)
def _isolated_opt_out_cache():
    """user_prefs caches opt-out answers module-wide for 30 s. Without this, a
    test here that opts "u1" in/out leaks that verdict into every later test in
    the same process that ingests for "u1" (the ci_safe lane is ONE process)."""
    import user_prefs

    user_prefs.clear_pref_cache()
    yield
    user_prefs.clear_pref_cache()


class _Cursor:
    def __init__(self, row):
        self._row = row

    async def fetchone(self):
        return self._row


class _FakeDb:
    """Postgres stand-in for user_prefs, honouring the jsonb merge/delete semantics
    of the atomic statements (proven on the live DB in a rolled-back txn)."""

    def __init__(self):
        self.rows: dict[str, str] = {}
        self.commits = 0
        self.sql: list[str] = []

    async def execute(self, sql, params=()):
        norm = " ".join(sql.split())
        self.sql.append(norm)
        p = list(params or ())
        if norm.startswith("SELECT prefs FROM user_preferences"):
            raw = self.rows.get(p[0])
            return _Cursor({"prefs": raw} if raw is not None else None)
        if norm.startswith("INSERT INTO user_preferences"):
            uid, payload = p[0], p[1]
            if "::jsonb ||" in norm and uid in self.rows:   # atomic key merge
                merged = json.loads(self.rows[uid]); merged.update(json.loads(payload))
                self.rows[uid] = json.dumps(merged)
            else:                                            # plain full write
                self.rows[uid] = payload
            return _Cursor(None)
        if norm.startswith("UPDATE user_preferences SET prefs = (COALESCE(prefs, '{}')::jsonb -"):
            key, uid = p[0], p[1]
            if uid in self.rows:
                cur = json.loads(self.rows[uid])
                if len(p) == 2 or cur.get(p[2]) == p[3]:
                    cur.pop(key, None); self.rows[uid] = json.dumps(cur)
            return _Cursor(None)
        raise AssertionError(f"unexpected SQL: {norm}")

    async def commit(self):
        self.commits += 1


class _InMemoryCollection:
    """The two collection calls ingest/review make (upsert + get-by-id), in a dict.

    The real store is mempalace/chromadb, which the slim GitHub ``-m ci_safe``
    lane does not install — so these tests exercise the REAL MemoryService
    ingest/review logic against this fake rather than silently skipping."""

    def __init__(self):
        self.rows: dict[str, tuple[str, dict]] = {}

    def upsert(self, *, ids, documents, metadatas):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[i] = (d, dict(m))

    def get(self, *, ids=None, include=None, **_kw):
        hits = [i for i in (ids or []) if i in self.rows]
        return {"ids": hits,
                "documents": [self.rows[i][0] for i in hits],
                "metadatas": [self.rows[i][1] for i in hits]}


def _isolated_memory_service():
    """A real MemoryService whose store + audit lane never touch mempalace/chromadb."""
    from memory_service import MemoryService

    svc = MemoryService(data_dir="/nonexistent/zoe-test-memory-optout")
    col = _InMemoryCollection()
    svc._collection = lambda: col

    async def _no_audit(**_kw):
        return None

    svc._append_audit = _no_audit
    return svc, col


@pytest.fixture
def client(monkeypatch):
    db = _FakeDb()
    app = FastAPI()
    app.include_router(memories.router)

    async def fake_user():
        return USER

    async def fake_db():
        yield db

    async def allow(*_a, **_k):
        return None

    app.dependency_overrides[get_current_user] = fake_user
    app.dependency_overrides[get_db] = fake_db
    monkeypatch.setattr(memories, "require_feature_access", allow)
    # raise_server_exceptions=False so the negative control shows the real 500
    # a caller sees rather than the test harness re-raising the import error.
    return TestClient(app, raise_server_exceptions=False), db


def test_get_opt_out_defaults_false(client):
    tc, db = client
    resp = tc.get("/api/memories/opt-out")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"user_id": USER["user_id"], "memory_opt_out": False}
    assert db.rows == {}  # a read never creates a row


def test_put_then_get_round_trips_and_preserves_other_prefs(client):
    tc, db = client
    # Pre-existing, unrelated preference must survive the toggle.
    db.rows[USER["user_id"]] = json.dumps({"telegram_user_id": "12345"})

    resp = tc.put("/api/memories/opt-out", json={"memory_opt_out": True})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"user_id": USER["user_id"], "memory_opt_out": True}
    assert db.commits == 1
    stored = json.loads(db.rows[USER["user_id"]])
    assert stored == {"telegram_user_id": "12345", "memory_opt_out": True}

    assert tc.get("/api/memories/opt-out").json()["memory_opt_out"] is True

    # Flip back; a missing/None body clears it.
    resp = tc.put("/api/memories/opt-out", json={})
    assert resp.status_code == 200
    assert resp.json()["memory_opt_out"] is False
    assert tc.get("/api/memories/opt-out").json()["memory_opt_out"] is False


def test_put_coerces_truthy_values(client):
    tc, _db = client
    assert tc.put("/api/memories/opt-out", json={"memory_opt_out": "yes"}).json()["memory_opt_out"] is True
    assert tc.put("/api/memories/opt-out", json={"memory_opt_out": 0}).json()["memory_opt_out"] is False


# ── the capture path honours the flag ────────────────────────────────────────

class _FakeSvc:
    def __init__(self):
        self.ingested: list[dict] = []

    async def ingest(self, text, **kw):
        self.ingested.append({"text": text, **kw})
        return f"ref-{len(self.ingested)}"


@pytest.fixture
def fake_memory_service(monkeypatch):
    svc = _FakeSvc()
    mod = types.ModuleType("memory_service")
    mod.get_memory_service = lambda: svc
    monkeypatch.setitem(sys.modules, "memory_service", mod)
    monkeypatch.setattr(
        memory_extractor, "_prev_user_turns", type(memory_extractor._prev_user_turns)()
    )
    return svc


FACT = "My favourite colour is green."


@pytest.mark.asyncio
async def test_extractor_drops_everything_for_opted_out_user(fake_memory_service, monkeypatch):
    import user_prefs

    async def opted_out(user_id, *, db=None):
        return user_id == "u-optout"

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_out)

    n = await memory_extractor.extract_and_ingest(
        FACT, user_id="u-optout", session_id="s1", source="chat_regex"
    )
    assert n == 0
    assert fake_memory_service.ingested == []

    # Control: the same turn from an opted-in user IS captured.
    n = await memory_extractor.extract_and_ingest(
        FACT, user_id="u-other", session_id="s1", source="chat_regex"
    )
    assert n >= 1
    assert fake_memory_service.ingested


@pytest.mark.asyncio
async def test_extractor_fails_open_when_pref_lookup_breaks(fake_memory_service, monkeypatch):
    """No pool / DB blip must never turn into lost facts (fail-open)."""
    import user_prefs

    async def boom(user_id, *, db=None):
        raise RuntimeError("db_pool not initialised")

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", boom)
    n = await memory_extractor.extract_and_ingest(
        FACT, user_id="u-optout", session_id="s1", source="chat_regex"
    )
    assert n >= 1


# ── Greptile #1704 thread 2: concurrent updates must not lose preferences ────

@pytest.mark.asyncio
async def test_set_pref_is_one_atomic_statement_no_read_modify_write():
    import user_prefs

    db = _FakeDb()
    db.rows["u1"] = json.dumps({"telegram_id": "123"})
    await user_prefs.set_pref("u1", user_prefs.KEY_MEMORY_OPT_OUT, True, db=db)
    assert len(db.sql) == 1, db.sql          # no SELECT first — nothing to go stale
    assert "::jsonb ||" in db.sql[0]
    assert json.loads(db.rows["u1"]) == {"telegram_id": "123", "memory_opt_out": True}


@pytest.mark.asyncio
async def test_interleaved_writers_keep_both_keys():
    """Writer B starts before writer A commits. The old read→modify→write shape
    (kept here as the negative control) loses A's key; atomic per-key writes keep both."""
    import user_prefs

    # Negative control — the race class Greptile described, on a stale full copy.
    db = _FakeDb(); db.rows["u1"] = json.dumps({})
    stale = await user_prefs.read_prefs(db, "u1")                     # B reads
    await user_prefs.set_pref("u1", "memory_opt_out", True, db=db)   # A commits
    stale["telegram_id"] = "123"
    await user_prefs.write_prefs(db, "u1", stale)                     # B writes full copy
    assert "memory_opt_out" not in json.loads(db.rows["u1"])          # A's change LOST

    # Fixed shape: both writers merge one key each, in either order.
    db = _FakeDb(); db.rows["u1"] = json.dumps({})
    await user_prefs.set_pref("u1", "memory_opt_out", True, db=db)
    await user_prefs.set_pref("u1", "telegram_id", "123", db=db)
    assert json.loads(db.rows["u1"]) == {"memory_opt_out": True, "telegram_id": "123"}

    # delete_pref is atomic and (optionally) conditional — a stale claimant check
    # cannot clear a key someone else just re-pointed.
    await user_prefs.delete_pref("u1", "telegram_id", db=db, only_if="999")
    assert json.loads(db.rows["u1"])["telegram_id"] == "123"
    await user_prefs.delete_pref("u1", "telegram_id", db=db, only_if="123")
    assert json.loads(db.rows["u1"]) == {"memory_opt_out": True}


# ── Greptile #1704 thread 1: every AUTOMATIC writer honours the flag ─────────

@pytest.mark.asyncio
async def test_memory_service_chokepoint_drops_all_automatic_sources(monkeypatch):
    """chat/voice turns fan out to extract_and_ingest + run_turn_digest + both
    person extractors; the digest/consolidation lanes run later. Guarding one
    extractor is not enough — MemoryService.ingest is the single write chokepoint."""
    import user_prefs

    async def opted_out(user_id, *, db=None):
        return user_id == "u-optout"

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_out)
    svc, _col = _isolated_memory_service()
    written: list[tuple[str, str]] = []
    svc._write_row = lambda mem_id, text, metadata: written.append((metadata["source"], text))

    automatic = ["chat_regex", "turn_digest", "conversation", "digest", "consolidation", "synthesis"]
    for i, src in enumerate(automatic):
        ref = await svc.ingest(f"Auto fact number {i} about the user.", user_id="u-optout", source=src)
        assert ref is None, f"{src} wrote for an opted-out user"
    assert written == []

    # Explicit teach / proposal paths are untouched by the flag …
    for i, src in enumerate(["brain_tool", "voice_fact", "proposal"]):
        ref = await svc.ingest(f"Explicit fact number {i}.", user_id="u-optout", source=src)
        assert ref is not None, f"{src} must still store"
    # … and an opted-in user's automatic writers still work.
    ref = await svc.ingest("Opted-in user fact.", user_id="u-other", source="turn_digest")
    assert ref is not None
    assert {s for s, _ in written} == {"brain_tool", "voice_fact", "proposal", "turn_digest"}


# ── Greptile #1704 round 2 ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_opt_out_cache_never_restores_a_value_invalidated_mid_read(monkeypatch):
    """A read that started BEFORE an opt-out PUT must not park its stale False in
    the cache after the PUT invalidated it (that would drop memories for 30 s)."""
    import asyncio
    import user_prefs

    user_prefs.clear_pref_cache()
    gate = asyncio.Event()
    stored = {"flag": False}

    async def slow_get_pref(user_id, key, default=None, *, db=None):
        answer = stored["flag"]               # what the DB held when the read began
        await gate.wait()                     # parked mid-read
        return answer

    monkeypatch.setattr(user_prefs, "get_pref", slow_get_pref)

    in_flight = asyncio.create_task(user_prefs.is_memory_opted_out("u1"))
    await asyncio.sleep(0)                    # the read is now awaiting the DB
    stored["flag"] = True
    await user_prefs.set_pref("u1", user_prefs.KEY_MEMORY_OPT_OUT, True, db=_FakeDb())  # PUT lands
    gate.set()
    assert await in_flight is False           # its own (pre-PUT) answer is fine …
    assert await user_prefs.is_memory_opted_out("u1") is True   # … but it must not be cached


@pytest.mark.asyncio
async def test_review_edit_by_automatic_actor_honours_opt_out(monkeypatch):
    """reconcile_for_ingest's UPDATE path supersedes via review(decision="edit"),
    not ingest — an automatic actor must hit the same opt-out wall."""
    import user_prefs

    async def opted_out(user_id, *, db=None):
        return user_id == "u-optout"

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_out)
    svc, _col = _isolated_memory_service()
    ref = await svc.ingest("Jason's dentist is on Friday.", user_id="u-optout",
                           source="brain_tool", status="approved")   # explicit teach: stored
    assert ref is not None

    for actor in ("turn_digest", "conversation", "digest", "consolidation", "chat_regex"):
        out = await svc.review(ref.id, decision="edit", edits="Jason's dentist is on Saturday.", actor=actor)
        assert out is None, f"{actor} edited an opted-out user's memory"
    assert (await svc.get(ref.id)).text == "Jason's dentist is on Friday."

    # An explicit reviewer still edits.
    new_ref = await svc.review(ref.id, decision="edit", edits="Jason's dentist is on Saturday.", actor="review_ui")
    assert new_ref is not None and "Saturday" in new_ref.text


# ── Greptile #1704 round 3: consolidation must not count skipped edits ───────

class _SkippingReviewSvc:
    """Consolidation's svc surface; review() returns None like the opt-out wall."""

    def __init__(self, rows, review_result=None):
        self._rows = {r.id: r for r in rows}
        self._review_result = review_result
        self.review_calls = 0

    async def list_by_status(self, *, user_id, status, limit):
        return list(self._rows.values())

    async def get(self, mem_id):
        return self._rows.get(mem_id)

    async def review(self, mem_id, **_kw):
        self.review_calls += 1
        return self._review_result


def _approved(mem_id, text, added_at):
    from memory_service import MemoryRef

    return MemoryRef(id=mem_id, text=text, metadata={
        "status": "approved", "confidence": 0.9, "added_at": added_at})


@pytest.mark.asyncio
async def test_consolidation_does_not_count_edits_the_opt_out_wall_skipped(monkeypatch):
    """review() returns None for an opted-out user's automatic edit; the weekly
    summary must not report merges / resolved contradictions that never happened."""
    import memory_digest

    dupes = [_approved("a", "Jason loves Italian food a lot", "2026-09-01"),
             _approved("b", "Jason loves Italian food a lot really", "2026-09-02")]
    svc = _SkippingReviewSvc(dupes)
    assert await memory_digest._merge_near_duplicates(svc, "u-optout") == 0
    assert svc.review_calls == 1                      # the edit WAS attempted …

    async def always_contradicts(a, b):
        return True

    monkeypatch.setattr(memory_digest, "_is_contradiction", always_contradicts)
    pair = [_approved("n", "Jason's dentist is on Saturday", "2026-09-02"),
            _approved("o", "Jason's dentist is on Friday", "2026-09-01")]
    svc = _SkippingReviewSvc(pair)
    assert await memory_digest._resolve_contradictions(svc, "u-optout") == 0
    assert svc.review_calls == 1                      # … and skipped, not counted

    # A real edit still counts.
    svc = _SkippingReviewSvc(pair, review_result=object())
    assert await memory_digest._resolve_contradictions(svc, "u-optout") == 1
