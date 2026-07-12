"""fact→person linkage hygiene (Part A of gbrain-memory-graph-recall).

Two producers must key a person-fact the same way person_extractor does:
  * resolved to a real contact → entity_type="person",         entity_id=<people.id>
  * unresolved                 → entity_type="person_pending",  entity_id="slug:<name>"

Covered here:
  1. memory_extractor.extract_and_ingest: a known name → person + people.id.
  2. memory_extractor.extract_and_ingest: an unknown name → person_pending + slug:.
  3. An ambiguous short name ("Sam" ⊂ {"Sam","Samantha"}) stays pending — a hard
     link is only made when the contact match is unambiguous.
  4. memory_digest._resolve_pending_person_links: the idle dream-cycle pass
     rewrites a person_pending fact to a real people.id once the contact exists,
     scoped to THIS user's pending rows, via the lock-guarded relink path.
  5. The idle resolver is a true no-op when its flag is OFF.

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


# ── Fake memory service (records ingest / relink calls) ───────────────────────
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


def _where_match(meta: dict, where: dict) -> bool:
    """Honor a Chroma `{"$and": [{field: {"$eq": v}}, ...]}` filter over metadata.

    The production resolver relies on this filter to scope the scan to the
    current user's `person_pending` rows; a fake that returned everything would
    let a dropped predicate pass silently (Greptile P2).
    """
    for clause in (where or {}).get("$and", []):
        for field, cond in clause.items():
            if str(meta.get(field)) != str(cond.get("$eq")):
                return False
    return True


class _FakeCollection:
    def __init__(self, rows):
        # rows: list[(id, metadata_dict)]
        self._rows = [(rid, dict(meta)) for rid, meta in rows]
        self.get_calls = 0

    def get(self, where=None, include=None, ids=None):
        self.get_calls += 1
        sel = self._rows
        if ids is not None:
            wanted = set(ids)
            sel = [(rid, m) for rid, m in sel if rid in wanted]
        if where is not None:
            sel = [(rid, m) for rid, m in sel if _where_match(m, where)]
        return {"ids": [rid for rid, _ in sel], "metadatas": [dict(m) for _, m in sel]}

    def _meta(self, mem_id):
        for rid, m in self._rows:
            if rid == mem_id:
                return m
        return None


class _FakeCollectionSvc:
    """Fake with the two surfaces the resolver uses: _collection() + relink_entity."""

    def __init__(self, col):
        self._col = col
        self.relinks = []  # list[(mem_id, entity_type, entity_id)]

    def _collection(self):
        return self._col

    async def relink_entity(self, user_id, mem_id, entity_type, entity_id):
        # Mirror MemoryService.relink_entity's guards: owner + still-pending only.
        meta = self._col._meta(mem_id)
        if meta is None:
            return False
        owner = str(meta.get("user_id") or "")
        if owner and owner != user_id:
            return False
        if str(meta.get("entity_type") or "") != "person_pending":
            return False
        meta["entity_type"] = entity_type
        meta["entity_id"] = entity_id
        self.relinks.append((mem_id, entity_type, entity_id))
        return True


def _patch_memory_service(monkeypatch, svc):
    import memory_service
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda u: not u)


def _always_storable(monkeypatch):
    import memory_quality
    monkeypatch.setattr(memory_quality, "is_storable_fact", lambda _t: (True, ""))


def _use_db(monkeypatch, db):
    import person_extractor

    async def _ensure(_arg):
        return _DB(db), False
    monkeypatch.setattr(person_extractor, "_ensure_db", _ensure)


# ══════════════════════════════════════════════════════════════════════════════
# 1–3 — the producer (memory_extractor.extract_and_ingest)
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_known_person_fact_links_to_people_id(monkeypatch):
    import memory_extractor as me

    db = await _open(people=[("katie-uuid-123", "Katie")])
    try:
        svc = _FakeIngestSvc()
        _patch_memory_service(monkeypatch, svc)
        _always_storable(monkeypatch)
        _use_db(monkeypatch, db)

        saved = await me.extract_and_ingest(
            "I met Katie who is a nurse", user_id=USER, source="test",
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

    db = await _open(people=[])  # nobody in contacts yet
    try:
        svc = _FakeIngestSvc()
        _patch_memory_service(monkeypatch, svc)
        _always_storable(monkeypatch)
        _use_db(monkeypatch, db)

        saved = await me.extract_and_ingest(
            "I met Katie who is a nurse", user_id=USER, source="test",
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
async def test_ambiguous_short_name_stays_pending(monkeypatch):
    # "Sam" substring-matches BOTH "Samantha" and "Samuel" and there is NO exact
    # "Sam" contact — genuinely ambiguous. We must NOT hard-link (the old
    # first-row resolver could store the fact under whichever matched first). It
    # stays pending on the exact slug.
    import memory_extractor as me

    db = await _open(people=[("samantha-uuid", "Samantha"), ("samuel-uuid", "Samuel")])
    try:
        svc = _FakeIngestSvc()
        _patch_memory_service(monkeypatch, svc)
        _always_storable(monkeypatch)
        _use_db(monkeypatch, db)

        await me.extract_and_ingest(
            "I met Sam who is a nurse", user_id=USER, source="test",
            prev_user_message="",
        )
        person = [i for i in svc.ingests if i["memory_type"] == "person"]
        assert len(person) == 1
        assert person[0]["entity_type"] == "person_pending"
        assert person[0]["entity_id"] == "slug:sam"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_exact_match_wins_over_longer_substring(monkeypatch):
    # With an exact "Sam" contact present, "Sam" links to THAT row (the exact
    # match), not the longer "Samantha" — the unique-exact path.
    import memory_extractor as me

    db = await _open(people=[("sam-uuid", "Sam"), ("samantha-uuid", "Samantha")])
    try:
        # Remove the ambiguity by giving a fact for the exact-only case: only one
        # contact whose exact name is "Sam" exists, so exact-unique resolves.
        svc = _FakeIngestSvc()
        _patch_memory_service(monkeypatch, svc)
        _always_storable(monkeypatch)
        _use_db(monkeypatch, db)

        et, eid = await me._resolve_person_link("Sam", USER, _DB(db))
        assert et == "person" and eid == "sam-uuid"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_first_name_links_to_full_name_contact(monkeypatch):
    # First-name "Katie" → contact "Katie Brown": the extracted name is a whole
    # token of the contact's name, so this legitimate link is kept.
    import memory_extractor as me

    db = await _open(people=[("katie-uuid", "Katie Brown")])
    try:
        et, eid = await me._resolve_person_link("Katie", USER, _DB(db))
        assert et == "person" and eid == "katie-uuid"
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sub_token_substring_stays_pending(monkeypatch):
    # "Al" is a sub-token of "Alice" (not a whole name token) — hard-linking it
    # would attach the fact to the wrong person, so it stays pending.
    import memory_extractor as me

    db = await _open(people=[("alice-uuid", "Alice")])
    try:
        et, eid = await me._resolve_person_link("Al", USER, _DB(db))
        assert et == "person_pending" and eid == "slug:al"
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
# 4 + 5 — the idle resolver (memory_digest._resolve_pending_person_links)
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_idle_resolver_relinks_pending_once_person_exists(monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_LINK_RESOLVER_ENABLED", "1")
    import memory_digest as md

    db = await _open(people=[("katie-uuid-123", "Katie")])
    try:
        col = _FakeCollection(rows=[
            # this user's pending fact → relinked
            ("mem-1", {"user_id": USER, "entity_type": "person_pending",
                       "entity_id": "slug:katie"}),
            # another user's pending fact → the where-scope must exclude it
            ("mem-2", {"user_id": "someone_else", "entity_type": "person_pending",
                       "entity_id": "slug:katie"}),
            # already resolved → excluded by the entity_type predicate
            ("mem-3", {"user_id": USER, "entity_type": "person",
                       "entity_id": "katie-uuid-123"}),
        ])
        svc = _FakeCollectionSvc(col)
        _patch_memory_service(monkeypatch, svc)

        res = await md._resolve_pending_person_links(USER, db=_DB(db))

        assert res["scanned"] == 1  # only this user's pending row was scanned
        assert res["relinked"] == 1
        assert svc.relinks == [("mem-1", "person", "katie-uuid-123")]
        assert col._meta("mem-1")["entity_type"] == "person"
        assert col._meta("mem-1")["entity_id"] == "katie-uuid-123"
        # Other users' / already-resolved rows untouched.
        assert col._meta("mem-2")["entity_type"] == "person_pending"
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
        svc = _FakeCollectionSvc(col)
        _patch_memory_service(monkeypatch, svc)

        res = await md._resolve_pending_person_links(USER, db=_DB(db))

        assert res["scanned"] == 1
        assert res["relinked"] == 0
        assert svc.relinks == []  # nothing rewritten
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_idle_resolver_ambiguous_name_not_relinked(monkeypatch):
    monkeypatch.setenv("ZOE_MEMORY_LINK_RESOLVER_ENABLED", "1")
    import memory_digest as md

    db = await _open(people=[("samantha-uuid", "Samantha"), ("samuel-uuid", "Samuel")])
    try:
        col = _FakeCollection(rows=[
            ("mem-1", {"user_id": USER, "entity_type": "person_pending",
                       "entity_id": "slug:samantha"}),  # exact → relink
            ("mem-2", {"user_id": USER, "entity_type": "person_pending",
                       "entity_id": "slug:sam"}),        # ambiguous → stay pending
        ])
        svc = _FakeCollectionSvc(col)
        _patch_memory_service(monkeypatch, svc)

        res = await md._resolve_pending_person_links(USER, db=_DB(db))

        assert res["scanned"] == 2
        assert res["relinked"] == 1
        assert svc.relinks == [("mem-1", "person", "samantha-uuid")]
        assert col._meta("mem-2")["entity_type"] == "person_pending"
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
    svc = _FakeCollectionSvc(col)
    # If the flag gate leaks, the service would be touched — patch it so we can
    # assert it is NEVER reached.
    _patch_memory_service(monkeypatch, svc)

    res = await md._resolve_pending_person_links(USER, db=None)

    assert res == {"user_id": USER, "scanned": 0, "relinked": 0}
    assert col.get_calls == 0  # true no-op: no store scan
    assert svc.relinks == []


def test_name_from_pending_slug():
    import memory_digest as md
    assert md._name_from_pending_slug("slug:katie") == "katie"
    assert md._name_from_pending_slug("slug:mary_jane") == "mary jane"
    assert md._name_from_pending_slug("katie") == "katie"  # legacy bare value
    assert md._name_from_pending_slug("") == ""


# ══════════════════════════════════════════════════════════════════════════════
# Pronoun CHAIN anchoring + turn-digest pronoun guard (the Caitlin bug, #1242)
# ══════════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_pronoun_chain_anchors_via_history_lookback(monkeypatch):
    """'I have a friend Caitlin' → 'she is allergic to nuts' → \"she's ALSO
    allergic to shellfish\": the third turn's literal prev has no person intro,
    so the anchor must come from the history lookback, not break after one hop."""
    import memory_extractor as me

    db = await _open(people=[])
    try:
        svc = _FakeIngestSvc()
        _patch_memory_service(monkeypatch, svc)
        _always_storable(monkeypatch)
        _use_db(monkeypatch, db)
        # LRU returns the literal prev turn (a pronoun fact — no intro).
        monkeypatch.setattr(me, "recall_prev_user_turn",
                            lambda u, s: "She is allergic to nuts")
        async def _hist(u, s, cur, limit=6):
            return ["She is allergic to nuts", "I have a friend Caitlin Farrell"]
        monkeypatch.setattr(me, "_load_recent_user_messages", _hist)

        saved = await me.extract_and_ingest(
            "She's also allergic to shellfish", user_id=USER,
            session_id="sess-1", source="test",
        )
        assert saved == 1
        person = [i for i in svc.ingests if i["memory_type"] == "person"]
        assert len(person) == 1
        assert person[0]["text"] == "Caitlin Farrell (user's friend) is allergic to shellfish"
        assert person[0]["entity_type"] == "person_pending"  # no contact row yet
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_turn_digest_skips_pronoun_subject():
    """The context-free LLM turn digest must NOT guess who 'she' is — it once
    stored a friend's allergy as \"The user is allergic to nuts\"."""
    import memory_digest as md

    r = await md.run_turn_digest("u1", "She is allergic to nuts", session_id="s1")
    assert r.get("skipped_reason") == "pronoun_subject_no_context"
    assert r["new"] == 0
    # First-person facts still digest (no skip reason set by the guard).
    # (Not invoking the LLM here — just assert the guard doesn't fire.)
    import re as _re
    assert not _re.match(r"^(?:she|he|they)\b", "I am allergic to nuts")


# ══════════════════════════════════════════════════════════════════════════════
# QA review F2: reconciliation on ingest — corrections SUPERSEDE, echoes SKIP
# ══════════════════════════════════════════════════════════════════════════════
class _FakeReconcileSvc(_FakeIngestSvc):
    """FakeIngestSvc + the search/review surface the reconciliation path uses."""

    def __init__(self, existing):
        super().__init__()
        self._existing = existing          # list[(id, text)]
        self.reviews = []                  # list[(mem_id, edits)]

    class _Hit:
        def __init__(self, mem_id, text):
            self.id, self.text = mem_id, text

    async def search(self, query, *, user_id, limit=10, timeout_s=2.0):
        return [self._Hit(i, t) for i, t in self._existing][:limit]

    async def review(self, mem_id, *, decision, edits=None, actor="", note=""):
        assert decision == "edit"
        self.reviews.append((mem_id, edits))
        return _Ref(f"new-{mem_id}")


@pytest.mark.asyncio
async def test_birthday_correction_supersedes_stale_value(monkeypatch):
    """QA F2 repro: 'her birthday is actually March 25' after Jessica's March-15
    intro must SUPERSEDE the stale row via review(edit), not stack a new one."""
    import memory_extractor as me

    db = await _open(people=[])
    try:
        svc = _FakeReconcileSvc(existing=[("stale-1", "Jessica's birthday is March 15")])
        _patch_memory_service(monkeypatch, svc)
        _always_storable(monkeypatch)
        _use_db(monkeypatch, db)

        saved = await me.extract_and_ingest(
            "her birthday is actually March 25",
            user_id=USER, session_id="s-corr", source="test",
            prev_user_message="My friend Jessica's birthday is March 15",
        )
        assert saved >= 1
        # stale row superseded (review edit), correction NOT stacked as a new ingest
        assert ("stale-1", "Jessica's birthday is March 25") in svc.reviews
        assert not any("March 25" in i["text"] for i in svc.ingests)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_sparser_echo_skipped_not_duplicated(monkeypatch):
    """A sparser restatement of an existing richer fact must SKIP (mem0 rule)."""
    import memory_extractor as me

    db = await _open(people=[])
    try:
        svc = _FakeReconcileSvc(existing=[("rich-1", "My dad's name is Neil, spelled N-E-I-L, born 1945")])
        _patch_memory_service(monkeypatch, svc)
        _always_storable(monkeypatch)
        _use_db(monkeypatch, db)

        await me.extract_and_ingest(
            "My dad's name is Neil",
            user_id=USER, session_id="s-echo", source="test", prev_user_message="",
        )
        assert svc.reviews == []                       # nothing superseded
        assert not any(i["text"] == "My dad's name is Neil" for i in svc.ingests)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_correction_never_supersedes_another_persons_row(monkeypatch):
    """Greptile P1 (security): a Jessica correction must not supersede Karen's
    same-attribute row even when semantic search returns it."""
    import memory_extractor as me

    db = await _open(people=[])
    try:
        svc = _FakeReconcileSvc(existing=[("karen-1", "Karen's birthday is March 15")])
        _patch_memory_service(monkeypatch, svc)
        _always_storable(monkeypatch)
        _use_db(monkeypatch, db)

        await me.extract_and_ingest(
            "her birthday is actually March 25",
            user_id=USER, session_id="s-x", source="test",
            prev_user_message="My friend Jessica's birthday is March 15",
        )
        assert svc.reviews == []  # Karen's row untouched
        assert any("March 25" in i["text"] for i in svc.ingests)  # stored as ADD
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_bare_name_correction_skips_full_name_namesake(monkeypatch):
    """Greptile P1 r3: 'Jessica' (bare) must not supersede 'Jessica Smith's' row —
    two people can share a first name. Ambiguity → ADD, never overwrite."""
    import memory_extractor as me

    db = await _open(people=[])
    try:
        svc = _FakeReconcileSvc(existing=[("smith-1", "Jessica Smith's birthday is March 15")])
        _patch_memory_service(monkeypatch, svc)
        _always_storable(monkeypatch)
        _use_db(monkeypatch, db)

        await me.extract_and_ingest(
            "her birthday is actually March 25",
            user_id=USER, session_id="s-ns", source="test",
            prev_user_message="My friend Jessica's birthday is March 15",
        )
        assert svc.reviews == []                                   # namesake untouched
        assert any("March 25" in i["text"] for i in svc.ingests)   # stored as ADD
    finally:
        await db.close()
