"""The people graph's five invariants (people_graph.py, ADR-relationship-memory "Invariants"), on real in-memory SQLite
with the post-0043 schema and synthetic people. Every rule has a test that goes red when the rule is broken - the
instrument checks (``_old_*``) reproduce the legacy behaviour the rule replaces, so the test is known to bite."""
from __future__ import annotations

import ast
import random
import re
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
import pytest

import people_graph as pg
import person_extractor as pe
from test_named_relations import _close_dbs, svc  # noqa: F401 - fixtures (the fake memory service; closes the DBs)

pytestmark = pytest.mark.ci_safe
U = "demo-u"
SVC = Path(__file__).resolve().parents[1]


async def _db():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("""CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, relationship TEXT,
        circle TEXT, context TEXT, visibility TEXT, notes TEXT, deleted INTEGER NOT NULL DEFAULT 0, is_partial INTEGER NOT NULL DEFAULT 0,
        created_at TEXT, updated_at TEXT, last_contacted_at TEXT)""")
    await db.execute("""CREATE TABLE person_relationships (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, person_a_id TEXT NOT NULL,
        person_b_id TEXT NOT NULL, rel_type TEXT NOT NULL, rel_a_to_b TEXT NOT NULL, rel_b_to_a TEXT NOT NULL, rel_group TEXT NOT NULL,
        notes TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, valid_from TEXT, valid_to TEXT, superseded_by TEXT,
        authority TEXT, origin TEXT, close_reason TEXT, turn_id TEXT, quote_span TEXT, speaker_rank INTEGER)""")
    await db.execute("CREATE UNIQUE INDEX person_relationships_pair_active ON person_relationships(user_id, person_a_id, person_b_id) "
                     "WHERE valid_to IS NULL")
    await db.commit()
    return db


async def _person(db, pid, name, user=U, **cols):
    cols = {"id": pid, "user_id": user, "name": name, **cols}
    await db.execute(f"INSERT INTO people ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", tuple(cols.values()))
    await db.commit()


async def _edge(db, eid, a, b, rel="friend", user=U, valid_from="2020-01-01T00:00:00.000000Z", valid_to=None, **cols):
    cols = {"id": eid, "user_id": user, "person_a_id": a, "person_b_id": b, "rel_type": rel, "rel_a_to_b": rel, "rel_b_to_a": rel,
            "rel_group": "friend", "created_at": valid_from, "updated_at": valid_from, "valid_from": valid_from,
            "valid_to": valid_to, **cols}
    await db.execute(f"INSERT INTO person_relationships ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", tuple(cols.values()))
    await db.commit()


async def _rows(db, sql="SELECT * FROM person_relationships ORDER BY created_at, id"):
    async with db.execute(sql) as cur:
        return [dict(r) for r in await cur.fetchall()]


@pytest.fixture
async def db():
    d = await _db()
    yield d
    await d.close()


# ── 1. resolution ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("a,b", [("José", "jose"), ("O'Brien", "OBRIEN"), ("Mary-Jane", "mary jane"), ("Zoë", "ZOE"),
                                 ("Łukasz", "Łukasz"), ("田中 太郎", "田中太郎".replace("中", "中 "))])
def test_fold_equal(a, b):
    assert pg.fold_name(a) == pg.fold_name(b)


@pytest.mark.parametrize("a,b", [("か", "が"), ("田中", "田村"), ("Ann", "Anna"), ("हिन्दी", "हिंदी")])
def test_fold_keeps_letters_apart(a, b):
    assert pg.fold_name(a) != pg.fold_name(b)


async def test_ann_is_not_joanna(db):
    await _person(db, "p1", "Joanna Reyes")
    assert (await pg.resolve_person(db, U, "Ann")).status == "none"            # the legacy LIKE '%ann%' returned Joanna
    await _person(db, "p2", "Ann Reyes")
    res = await pg.resolve_person(db, U, "Ann")
    assert (res.status, res.tier, res.person_id) == ("unique", "token", "p2")


async def _old_like_resolver(db, name):                 # instrument: the resolver this replaces
    async with db.execute("SELECT id FROM people WHERE user_id=? AND deleted=0 AND lower(name) LIKE lower(?)", (U, f"%{name}%")) as c:
        return [r[0] for r in await c.fetchall()]


async def test_the_old_resolver_was_wrong_in_exactly_these_ways(db):
    await _person(db, "p1", "Joanna Reyes")
    await _person(db, "p2", "100% Joe")
    assert await _old_like_resolver(db, "Ann") == ["p1"] and await _old_like_resolver(db, "%") == ["p1", "p2"]


async def test_two_toms_are_ambiguous_with_a_stable_candidate_list(db):
    ids = ["t1", "t2", "t3"]
    random.Random(7).shuffle(ids)
    for i, pid in enumerate(ids):
        await _person(db, pid, "Tom", relationship=["colleague", "brother", ""][i], updated_at=f"2026-0{i + 1}-01T00:00:00Z")
    await _edge(db, "e", "t1", "t3")                                          # t1 has a current edge: first
    seen = set()
    for _ in range(50):
        res = await pg.resolve_person(db, U, "tom")
        assert (res.status, res.tier, res.person_id) == ("ambiguous", "exact", None)
        seen.add(tuple(m.person_id for m in res.matches))
    assert seen == {("t1", "t3", "t2")}          # current edge first, then newest evidence (t3 Mar > t2 Feb)
    payload = (await pg.resolve_person(db, U, "Tom")).ask_payload()
    assert payload["kind"] == "person" and payload["handle"] == "Tom" and len(payload["candidates"]) == 3
    from ask_when_ambiguous import Candidate                                   # the shape that tier reads
    Candidate(**{k: payload["candidates"][0][k] for k in ("person_id", "name", "relationship")})


async def test_tiers_exact_then_token_then_prefix(db):
    for pid, name in (("a", "Ann"), ("b", "Ann Reyes"), ("c", "Annabel")):
        await _person(db, pid, name)
    assert (await pg.resolve_person(db, U, "ann")).person_id == "a"            # exact beats token and prefix
    await db.execute("DELETE FROM people WHERE id='a'")
    assert (await pg.resolve_person(db, U, "Ann")).person_id == "b"            # token beats prefix
    await db.execute("DELETE FROM people WHERE id='b'")
    res = await pg.resolve_person(db, U, "Ann")
    assert (res.person_id, res.tier) == ("c", "prefix")
    assert (await pg.resolve_person(db, U, "Ann", allow_prefix=False)).status == "none"


async def test_wildcards_are_plain_characters(db):
    await _person(db, "p1", "Al_ex")
    await _person(db, "p2", "100% Joe")
    await _person(db, "p3", "Alex")
    for q in ("%", "_", "%%%", "A%"):
        assert (await pg.resolve_person(db, U, q)).status == "none", q          # a wildcard expands to nothing
    assert (await pg.resolve_person(db, U, "Al%")).person_id == "p1"            # punctuation separates; "al" is p1's token
    assert (await pg.resolve_person(db, U, "A_ex")).person_id == "p1"           # "_" is not "any one character": not p3
    assert (await pg.resolve_person(db, U, "Alex")).person_id == "p3"


async def test_cjk_and_accents(db):
    await _person(db, "p1", "田中太郎")
    await _person(db, "p2", "José García")
    assert (await pg.resolve_person(db, U, "田中")).person_id == "p1"
    assert (await pg.resolve_person(db, U, "jose garcia")).person_id == "p2"
    await _person(db, "p3", "田中花子")
    assert (await pg.resolve_person(db, U, "田中")).status == "ambiguous"


async def test_scope_deleted_and_other_users_never_candidates(db):
    await _person(db, "p1", "Tom", deleted=1)
    await _person(db, "p2", "Tom", user="other-user")
    assert (await pg.resolve_person(db, U, "Tom")).status == "none"
    await _person(db, "p3", "Tom")
    assert (await pg.resolve_person(db, U, "Tom")).person_id == "p3"


async def test_writers_never_attach_to_a_guess(db, monkeypatch):
    for pid in ("t1", "t2"):
        await _person(db, pid, "Tom")
    held = []

    class Svc:
        async def record_candidate(self, text, **kw):
            held.append((text, kw))

    import memory_service
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: Svc())
    assert await pe._resolve_person_uuid("Tom", U, db) is None
    await pe._write_relationship(U, "Tom", "Ann", "sibling", "family", db)
    await pe._write_relationship(U, "Zed", "Tom", "sibling", "family", db)                   # the ambiguous name is the second
    assert (await _rows(db)) == [] and len(await _rows(db, "SELECT id FROM people")) == 2   # no edge, no third Tom, no stubs
    (text, kw), _second = held
    assert text == "Tom is Ann's sibling." and kw["status"] == "pending" and kw["basis"] == "ambiguous_name"


# ── 2. atomic change ─────────────────────────────────────────────────────────

def _spec(rel="spouse", **kw):
    return pg.EdgeSpec(rel, rel, rel, "family", **kw)


async def _one_current(db):
    cur = [r for r in await _rows(db) if r["valid_to"] is None]
    assert len(cur) == 1, cur
    return cur[0]


async def test_supersede_closes_old_and_opens_new_in_one_step(db):
    await _edge(db, "old", "a", "b", "friend")
    ch = await pg.replace_current_edge(db, U, "a", "b", _spec(origin="conversation", authority="user_stated"), close_reason="superseded")
    old, new = await _rows(db, "SELECT * FROM person_relationships WHERE id='old'"), await _one_current(db)
    assert ch.status == "superseded" and new["id"] == ch.edge_id != "old" and new["rel_type"] == "spouse"
    assert old[0]["valid_to"] and old[0]["superseded_by"] == new["id"] and old[0]["close_reason"] == "superseded"


async def test_crash_after_the_close_leaves_the_old_edge_current(db, monkeypatch):
    await _edge(db, "old", "a", "b", "friend")

    async def boom(*a, **k):
        raise RuntimeError("crash between the close and the insert")

    monkeypatch.setattr(pg, "insert_edge", boom)
    with pytest.raises(RuntimeError):
        await pg.replace_current_edge(db, U, "a", "b", _spec())
    cur = await _one_current(db)
    assert (cur["id"], cur["rel_type"], cur["superseded_by"], cur["close_reason"]) == ("old", "friend", None, None)


async def test_an_insert_that_does_not_land_rolls_the_close_back(db, monkeypatch):
    await _edge(db, "old", "a", "b", "friend")

    async def noop(*a, **k):
        return False                                           # ON CONFLICT DO NOTHING swallowed it

    monkeypatch.setattr(pg, "insert_edge", noop)
    with pytest.raises(pg.EdgeWriteError):
        await pg.replace_current_edge(db, U, "a", "b", _spec())
    assert (await _one_current(db))["id"] == "old"


async def test_the_legacy_close_then_insert_loses_the_edge(db):        # instrument: the pattern this replaces
    await _edge(db, "old", "a", "b", "friend")
    await db.execute("UPDATE person_relationships SET valid_to='2026-01-01T00:00:00Z' WHERE id='old'")
    await db.commit()                                                    # the compat layer's autocommit
    assert [r for r in await _rows(db) if r["valid_to"] is None] == []   # ...and the insert crashed: no current edge


async def test_a_stale_decision_writes_nothing(db):
    await _edge(db, "old", "a", "b", "friend")
    assert (await pg.replace_current_edge(db, U, "a", "b", _spec(), expect_old_id="somebody-else")).status == "stale"
    assert (await pg.replace_current_edge(db, U, "a", "b", _spec("friend"))).status == "unchanged"
    assert (await _one_current(db))["id"] == "old" and len(await _rows(db)) == 1


# ── 3. invalidate, never delete ──────────────────────────────────────────────

class _Req:
    """Drive the REST handlers directly: access check, 404 lookup and push are stubbed, the SQL is real."""

    def __init__(self, monkeypatch):
        from routers import people

        async def allow(*a, **k):
            return None

        async def found(db, person_id, user_id):
            return {"id": person_id}

        async def quiet(*a, **k):
            return None

        monkeypatch.setattr(people, "require_feature_access", allow)
        monkeypatch.setattr(people, "_get_person_or_404", found)
        monkeypatch.setattr(people.broadcaster, "broadcast", quiet)
        self.people, self.user = people, {"user_id": U}


@pytest.fixture
def rest(monkeypatch):
    return _Req(monkeypatch)


async def test_rest_put_closes_the_old_edge_and_opens_a_new_one(db, rest):
    await _edge(db, "e1", "a", "b", "friend", notes="met at school")
    out = await rest.people.update_relationship("a", "e1", {"rel_type": "spouse"}, rest.user, db)
    old = (await _rows(db, "SELECT * FROM person_relationships WHERE id='e1'"))[0]
    new = await _one_current(db)
    assert out["rel_id"] == new["id"] != "e1" and (new["rel_type"], new["notes"], new["authority"], new["speaker_rank"]) == (
        "spouse", "met at school", "user_confirmed", 5)
    assert old["rel_type"] == "friend" and old["valid_to"] and old["close_reason"] == "user_edited"   # not overwritten


async def test_rest_put_notes_only_is_an_annotation_in_place(db, rest):
    await _edge(db, "e1", "a", "b", "friend")
    assert (await rest.people.update_relationship("a", "e1", {"notes": "x"}, rest.user, db))["rel_id"] == "e1"
    assert len(await _rows(db)) == 1 and (await _one_current(db))["notes"] == "x"


async def test_rest_delete_closes_with_a_reason_and_keeps_the_row(db, rest):
    await _edge(db, "e1", "a", "b", "friend")
    await rest.people.delete_relationship("a", "e1", rest.user, db)
    (row,) = await _rows(db)
    assert row["valid_to"] and row["close_reason"] == "user_removed"
    assert (await rest.people.list_relationships("a", None, None, rest.user, db))["relationships"] == {
        "love": [], "family": [], "friend": [], "work": []}


async def _merge_db():
    from test_person_merge import _open_db

    d = await _open_db()
    d.row_factory = aiosqlite.Row
    for col in ("close_reason TEXT", "authority TEXT", "origin TEXT", "turn_id TEXT", "quote_span TEXT", "speaker_rank INTEGER"):
        await d.execute(f"ALTER TABLE person_relationships ADD COLUMN {col}")
    return d


async def test_merge_closes_the_self_edge_and_the_duplicate_edge(rest):
    import person_merge
    from test_person_merge import _seed_edge, _seed_person

    d = await _merge_db()
    try:
        for p in ("src", "tgt", "carol"):
            await _seed_person(d, p)
        await _seed_edge(d, "self", "src", "tgt")
        await _seed_edge(d, "dup", "src", "carol", rel_type="friend")
        await _seed_edge(d, "keep", "tgt", "carol", rel_type="cousin")
        await person_merge.merge_person(d, "jason", "src", "tgt")
        rows = {r["id"]: r for r in await _rows(d, "SELECT * FROM person_relationships")}
        assert set(rows) == {"self", "dup", "keep"}                                   # nothing deleted
        assert (rows["self"]["close_reason"], rows["self"]["valid_to"] is not None) == ("merged_self_edge", True)
        assert (rows["dup"]["close_reason"], rows["dup"]["superseded_by"]) == ("merged_duplicate", "keep")
        assert rows["keep"]["valid_to"] is None
    finally:
        await d.close()


async def test_flag_off_keeps_the_new_belief_as_a_pending_candidate(db, monkeypatch):
    monkeypatch.delenv("ZOE_TEMPORAL_RELATIONSHIPS_ENABLED", raising=False)
    await _person(db, "pa", "Alice")
    await _person(db, "pb", "Bob")
    held = []

    class Svc:
        async def record_candidate(self, text, **kw):
            held.append((text, kw))

    import memory_service
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: Svc())
    await pe._write_relationship(U, "Alice", "Bob", "friend", "friend", db)
    await pe._write_relationship(U, "Alice", "Bob", "spouse", "love", db)
    cur = await _one_current(db)
    assert cur["rel_type"] == "friend" and len(await _rows(db)) == 1             # byte-for-byte the old graph...
    (text, kw), = held                                                           # ...but the belief is not lost
    assert text == "Alice is Bob's spouse." and kw["status"] == "pending" and kw["basis"] == "temporal_flag_off"
    assert kw["contradicts"] == "edge:" + cur["id"] and kw["extra"]["edge_new_rel"] == "spouse"


def test_no_runtime_module_deletes_an_edge():
    rx = re.compile(r"DELETE\s+FROM\s+person_relationships", re.IGNORECASE)
    assert rx.search("cur.execute('DELETE FROM person_relationships WHERE id=?')")          # the scan can see one
    hits = [str(p.relative_to(SVC)) for p in SVC.rglob("*.py")
            if not {"tests", "alembic", ".venv"} & set(p.relative_to(SVC).parts) and rx.search(p.read_text(errors="ignore"))]
    assert hits == []


# ── 4. temporal read ─────────────────────────────────────────────────────────

async def _history(db):
    await _edge(db, "spouse", "a", "b", "spouse", valid_from="2019-05-01T00:00:00.000000Z",
                valid_to="2024-03-01 09:30:00.123456+00", close_reason="superseded", superseded_by="ex")     # NOW()::text form
    await _edge(db, "ex", "a", "b", "ex", valid_from="2024-03-01T09:30:00Z")


def _t(s):
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


async def test_as_of_returns_the_edge_that_was_true_then(db):
    await _history(db)
    ids = lambda rows: [r["id"] for r in rows]                                   # noqa: E731
    assert ids(await pg.edges_for_person(db, U, "a")) == ["ex"]                  # default: current only
    assert ids(await pg.edges_for_person(db, U, "a", as_of=_t("2022-06-01"))) == ["spouse"]
    assert ids(await pg.edges_for_person(db, U, "a", as_of=_t("2025-01-01"))) == ["ex"]
    assert ids(await pg.edges_for_person(db, U, "a", as_of=_t("2018-01-01"))) == []
    assert ids(await pg.edges_for_person(db, U, "a", history=True)) == ["ex", "spouse"]


async def test_rest_get_current_by_default_history_on_request(db, rest):
    await _history(db)
    await _person(db, "b", "Bea")
    get = lambda **kw: rest.people.list_relationships("a", kw.get("as_of"), kw.get("include"), rest.user, db)   # noqa: E731
    flat = lambda out: [r["rel_type"] for g in out["relationships"].values() for r in g]                        # noqa: E731
    assert flat(await get()) == ["ex"]
    assert flat(await get(as_of="2022-06-01")) == ["spouse"]
    hist = await get(include="history")
    assert flat(hist) == ["ex", "spouse"] and hist["relationships"]["friend"][1]["close_reason"] == "superseded"
    with pytest.raises(rest.people.HTTPException):
        await get(as_of="last tuesday")


def test_timestamps_are_one_form_on_write_and_both_forms_read():
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}Z", pg.now_iso(datetime(2026, 1, 1, tzinfo=timezone.utc)))
    a, b = pg.parse_ts("2026-10-05T12:00:00Z"), pg.parse_ts("2026-10-05 12:00:00.000000+00")
    assert a == b == pg.parse_ts("2026-10-05T20:00:00+08:00") and pg.parse_ts("garbage") is None


# ── 5. evidence pointers ─────────────────────────────────────────────────────

async def test_a_chat_turn_writes_turn_quote_and_rank_on_the_edge(db):
    text = "Yesterday we talked. Sarah is Tom's sister, I think."
    await pe.process_text(text, user_id=U, source="conversation", db=db)
    (e,) = await _rows(db)
    assert e["turn_id"] == pg.content_turn_id(U, text) and e["speaker_rank"] == 4 and e["authority"] == "user_stated"
    start, end, digest = e["quote_span"].split(":")
    assert text[int(start):int(end)] == "Sarah is Tom's sister" and digest == pg.quote_span(text, text[int(start):int(end)]).split(":")[2]
    assert "Sarah" not in e["quote_span"]                                          # a pointer, never the words


async def test_named_relations_edges_carry_evidence(svc):
    from test_named_relations import SAID, USER, _open_db as open_named_db

    ndb = await open_named_db()
    for col in ("close_reason TEXT", "authority TEXT", "origin TEXT", "turn_id TEXT", "quote_span TEXT", "speaker_rank INTEGER"):
        await ndb.execute(f"ALTER TABLE person_relationships ADD COLUMN {col}")
    await pe.process_text(SAID, user_id=USER, source="conversation", db=ndb)
    async with ndb.execute("SELECT turn_id, quote_span, speaker_rank FROM person_relationships") as cur:
        edges = [tuple(r) for r in await cur.fetchall()]
    assert len(edges) == 2 and all(t == pg.content_turn_id(USER, SAID) and q and r == 4 for t, q, r in edges)


async def test_legacy_edges_have_null_evidence(db):
    await _edge(db, "old", "a", "b")
    (e,) = await _rows(db)
    assert (e["turn_id"], e["quote_span"], e["speaker_rank"]) == (None, None, None)
    assert (await pg.edges_for_person(db, U, "a"))[0]["turn_id"] is None


# ── the migration ────────────────────────────────────────────────────────────

def test_migration_0043_is_the_single_head_and_schema_only():
    revs = {}
    for f in (SVC / "alembic" / "versions").glob("*.py"):
        vals = {n.targets[0].id: n.value.value for n in ast.parse(f.read_text()).body
                if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Constant)}
        if "revision" in vals:
            revs[vals["revision"]] = vals.get("down_revision")
    assert set(revs) - {d for d in revs.values() if d} == {"0043"} and revs["0043"] == "0042"
    src = (SVC / "alembic" / "versions" / "0043_people_graph_invariants.py").read_text().split("def downgrade")[0]
    assert not re.search(r"\bUPDATE\s+\w+\s+SET\b|\bDELETE\s+FROM\b", src.split('"""', 2)[2])   # no data change


async def test_migration_0043_adds_the_columns_and_downgrades_cleanly():
    import importlib.util

    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import create_engine, text

    spec = importlib.util.spec_from_file_location("m0043", SVC / "alembic" / "versions" / "0043_people_graph_invariants.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    eng = create_engine("sqlite://")
    with eng.begin() as conn:
        conn.execute(text("CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, deleted INTEGER)"))
        conn.execute(text("CREATE TABLE person_relationships (id TEXT PRIMARY KEY, user_id TEXT, person_a_id TEXT, person_b_id TEXT, "
                          "valid_from TEXT, valid_to TEXT)"))
        with Operations.context(MigrationContext.configure(conn)):
            mod.upgrade()
            cols = {r[1] for r in conn.execute(text("PRAGMA table_info(person_relationships)"))}
            assert {"close_reason", "turn_id", "quote_span", "speaker_rank", "valid_from_ts", "valid_to_ts", "recorded_ts"} <= cols
            assert conn.execute(text("SELECT name FROM sqlite_master WHERE name='people_user_live_idx'")).fetchone()
            mod.downgrade()
            assert not {"close_reason", "valid_to_ts"} & {r[1] for r in conn.execute(text("PRAGMA table_info(person_relationships)"))}
