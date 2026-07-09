"""fact→person linkage hygiene (Part A of gbrain-memory-graph-recall).

Two producers must key a person-fact the same way person_extractor does:
  * resolved to a real contact → entity_type="person",         entity_id=<people.id>
  * unresolved                 → entity_type="person_pending",  entity_id="slug:<name>"

Covered here:
  1. memory_extractor.extract_and_ingest: a known name → person + people.id.
  2. memory_extractor.extract_and_ingest: an unknown name → person_pending + slug:.
  3. memory_digest._resolve_pending_person_links: the idle dream-cycle pass
     rewrites a person_pending fact to a real people.id once the contact exists
     (metadata-only col.update — no re-embed).
  4. The idle resolver is a true no-op when its flag is OFF.

Slim-dep: an in-memory SQLite `people` table + fakes for the memory service.
No pool, no Chroma, no network. DEMO users only.
"""
import re

import aiosqlite
import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep → GitHub -m ci_safe lane

USER = "demo_linkage_user"  # a DEMO user — never a real person


# ── DB shim (asyncpg-ish over aiosqlite: $N→? rewrite) ────────────────────────
class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class _DB:
    def __init__(self, db):
        self._db = db

    @staticmethod
    def _tr(sql):
        return re.sub(r"\$(\d+)", "?", sql)

    async def execute(self, sql, *args):
        if len(args) == 1 and isinstance(args[0], (list, tuple)):
            args = tuple(args[0])
        cur = await self._db.execute(self._tr(sql), args)
        rows = await cur.fetchall()
        return _Cursor(rows)

    async def close(self):
        pass


async def _open(people=()):
    db = await aiosqlite.connect(":memory:")
    await db.execute(
        """CREATE TABLE people (
            id TEXT PRIMARY KEY, user_id TEXT, name TEXT,
            deleted INTEGER DEFAULT 0)"""
    )
    for pid, nm in people:
        await db.execute(
            "INSERT INTO people (id, user_id, name, deleted) VALUES (?,?,?,0)",
            (pid, USER, nm),
        )
    await db.commit()
    return db


# ── Fake memory service (records ingest / metadata updates) ───────────────────
class _Ref:
    def __init__(self, mem_id):
        self.id = mem_id


class _FakeIngestSvc:
    """Captures every ingest() call's entity linkage."""

    def __init__(self):
        self.ingests = []

    async def ingest(self, text, **kw):
        self.ingests.append({
            "text": text,
            "entity_type": kw.get("entity_type"),
            "entity_id": kw.get("entity_id"),
            "memory_type": kw.get("memory_type"),
        })
        return _Ref(f"mem-{len(self.ingests)}")


class _FakeCollection:
    def __init__(self, rows):
        # rows: list[(id, metadata_dict)]
        self._rows = rows
        self.get_calls = 0
        self.updates = []  # list[(ids, metadatas)]

    def get(self, where=None, include=None):
        self.get_calls += 1
        return {
            "ids": [r[0] for r in self._rows],
            "metadatas": [dict(r[1]) for r in self._rows],
        }

    def update(self, ids=None, metadatas=None, documents=None):
        # A metadata-only relink must NOT pass documents (would force a re-embed).
        assert documents is None, "relink must be metadata-only (no re-embed)"
        self.updates.append((list(ids or []), [dict(m) for m in (metadatas or [])]))


class _FakeCollectionSvc:
    def __init__(self, col):
        self._col = col

    def _collection(self):
        return self._col


def _patch_memory_service(monkeypatch, svc):
    import memory_service
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda u: not u)


def _always_storable(monkeypatch):
    import memory_quality
    monkeypatch.setattr(memory_quality, "is_storable_fact", lambda _t: (True, ""))


# ══════════════════════════════════════════════════════════════════════════════
# 1 + 2 — the producer (memory_extractor.extract_and_ingest)
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_known_person_fact_links_to_people_id(monkeypatch):
    import memory_extractor as me
    import person_extractor

    db = await _open(people=[("katie-uuid-123", "Katie")])
    try:
        svc = _FakeIngestSvc()
        _patch_memory_service(monkeypatch, svc)
        _always_storable(monkeypatch)

        async def _ensure(_arg):
            return _DB(db), False
        monkeypatch.setattr(person_extractor, "_ensure_db", _ensure)

        saved = await me.extract_and_ingest(
            "I met Katie who is a nurse",
            user_id=USER,
            source="test",
            prev_user_message="",
        )
        assert saved == 1
        person = [i for i in svc.ingests if i["memory_type"] == "person"]
        assert len(person) == 1
        # Resolved to a real contact → keyed by the people.id, NOT a bare slug.
        assert person[0]["entity_type"] == "person"
        assert person[0]["entity_id"] == "katie-uuid-123"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_unknown_person_fact_is_pending_with_slug(monkeypatch):
    import memory_extractor as me
    import person_extractor

    db = await _open(people=[])  # nobody in contacts yet
    try:
        svc = _FakeIngestSvc()
        _patch_memory_service(monkeypatch, svc)
        _always_storable(monkeypatch)

        async def _ensure(_arg):
            return _DB(db), False
        monkeypatch.setattr(person_extractor, "_ensure_db", _ensure)

        saved = await me.extract_and_ingest(
            "I met Katie who is a nurse",
            user_id=USER,
            source="test",
            prev_user_message="",
        )
        assert saved == 1
        person = [i for i in svc.ingests if i["memory_type"] == "person"]
        assert len(person) == 1
        # Unresolved → honest pending marker, never "person" + bare slug.
        assert person[0]["entity_type"] == "person_pending"
        assert person[0]["entity_id"] == "slug:katie"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_non_person_fact_linkage_untouched(monkeypatch):
    # A plain user fact carries no entity link and must not open a DB / resolve.
    import memory_extractor as me
    import person_extractor

    svc = _FakeIngestSvc()
    _patch_memory_service(monkeypatch, svc)
    _always_storable(monkeypatch)

    async def _boom(_arg):  # would fire only if a person-fact were present
        raise AssertionError("_ensure_db must not be called without a person fact")
    monkeypatch.setattr(person_extractor, "_ensure_db", _boom)

    saved = await me.extract_and_ingest(
        "I live in Perth", user_id=USER, source="test", prev_user_message=""
    )
    assert saved == 1
    assert all(i["entity_type"] in (None, "") for i in svc.ingests)


# ══════════════════════════════════════════════════════════════════════════════
# 3 + 4 — the idle resolver (memory_digest._resolve_pending_person_links)
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_idle_resolver_relinks_pending_once_person_exists(monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_LINK_RESOLVER_ENABLED", "1")
    import memory_digest as md

    db = await _open(people=[("katie-uuid-123", "Katie")])
    try:
        col = _FakeCollection(rows=[
            ("mem-1", {"user_id": USER, "entity_type": "person_pending",
                       "entity_id": "slug:katie"}),
        ])
        _patch_memory_service(monkeypatch, _FakeCollectionSvc(col))

        res = await md._resolve_pending_person_links(USER, db=_DB(db))

        assert res["scanned"] == 1
        assert res["relinked"] == 1
        assert len(col.updates) == 1
        ids, metas = col.updates[0]
        assert ids == ["mem-1"]
        assert metas[0]["entity_type"] == "person"
        assert metas[0]["entity_id"] == "katie-uuid-123"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_idle_resolver_leaves_pending_when_no_contact(monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_LINK_RESOLVER_ENABLED", "1")
    import memory_digest as md

    db = await _open(people=[])  # Katie still not a contact
    try:
        col = _FakeCollection(rows=[
            ("mem-1", {"user_id": USER, "entity_type": "person_pending",
                       "entity_id": "slug:katie"}),
        ])
        _patch_memory_service(monkeypatch, _FakeCollectionSvc(col))

        res = await md._resolve_pending_person_links(USER, db=_DB(db))

        assert res["scanned"] == 1
        assert res["relinked"] == 0
        assert col.updates == []  # nothing rewritten
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_idle_resolver_is_noop_when_flag_off(monkeypatch):
    monkeypatch.delenv("ZOE_MEMORY_LINK_RESOLVER_ENABLED", raising=False)
    import memory_digest as md

    col = _FakeCollection(rows=[
        ("mem-1", {"user_id": USER, "entity_type": "person_pending",
                   "entity_id": "slug:katie"}),
    ])
    # If the flag gate leaks, the service would be touched — patch it so we can
    # assert it is NEVER reached.
    _patch_memory_service(monkeypatch, _FakeCollectionSvc(col))

    res = await md._resolve_pending_person_links(USER, db=None)

    assert res == {"user_id": USER, "scanned": 0, "relinked": 0}
    assert col.get_calls == 0  # true no-op: no store scan
    assert col.updates == []


def test_name_from_pending_slug():
    import memory_digest as md
    assert md._name_from_pending_slug("slug:katie") == "katie"
    assert md._name_from_pending_slug("slug:mary_jane") == "mary jane"
    assert md._name_from_pending_slug("katie") == "katie"  # legacy bare value
    assert md._name_from_pending_slug("") == ""
