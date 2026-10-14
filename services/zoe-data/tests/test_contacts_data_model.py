"""The contacts data model (migration 0052, ``contacts_store.py``, ``contacts_vcard.py``, ``routers/contacts.py``).

Real SQLite built by running the REAL 0052 upgrade over the people-graph tables; no fakes for the storage engine. Every person, number
and address here is invented (``example.org`` / ``555``). Covers: migration up/down/rerun + one head; back-compat with the legacy
single-value columns; multi-value fields round-tripping through the store and vCard; the merge model (strong-identifier link,
name-only / several-candidate / identifier-conflict -> review item, never a silent merge or a duplicate); member-private visibility;
the forget cascade (fields, links, synced dates, reviews, tombstoned source card, learned voiceprint; re-import suppressed); the
member erase; person merge re-pointing the contact rows; the flag-dark router.

Negative controls (each run by hand, one at a time, when this was written): drop the owner filter in ``_live_people`` ->
``test_member_b_never_links_to_member_a_people`` (and both forget tests) go red; skip the name-only review (``if by_name`` ->
``if False``) -> ``test_name_only_match_is_a_review_never_a_merge`` goes red; skip the ``contacts`` step in the cascade ->
``test_forget_removes_contact_rows_links_and_the_voiceprint`` goes red; keep the card instead of tombstoning it -> the same test
goes red.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import re
import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

import contacts_store as cs
import contacts_vcard as vc
from db_pool import _Cursor, _ExecResult
from forgotten_support import ledger_env  # noqa: F401 - fixture

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
MIG = SVC / "alembic/versions/0052_contacts_data_model.py"
VCF = (SVC / "tests/fixtures/contacts/invented.vcf").read_text()
A, B = "demo_contacts_a", "demo_contacts_b"

BASE_DDL = """
CREATE TABLE people (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL, relationship TEXT, email TEXT, phone TEXT, birthday TEXT,
    notes TEXT, preferences TEXT, visibility TEXT NOT NULL DEFAULT 'family', deleted INTEGER DEFAULT 0,
    is_partial INTEGER NOT NULL DEFAULT 0, context TEXT, circle TEXT, how_we_met TEXT, first_met_date TEXT,
    introduced_by_person_id TEXT, created_at TEXT, updated_at TEXT);
CREATE TABLE person_relationships (
    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, person_a_id TEXT NOT NULL, person_b_id TEXT NOT NULL, rel_type TEXT NOT NULL,
    rel_a_to_b TEXT NOT NULL, rel_b_to_a TEXT NOT NULL, rel_group TEXT NOT NULL, notes TEXT, valid_from TEXT, valid_to TEXT,
    superseded_by TEXT, authority TEXT, origin TEXT, close_reason TEXT, turn_id TEXT, quote_span TEXT, speaker_rank INTEGER,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE UNIQUE INDEX person_relationships_pair_active ON person_relationships(user_id, person_a_id, person_b_id)
    WHERE valid_to IS NULL;
CREATE TABLE person_activities (id TEXT PRIMARY KEY, person_id TEXT NOT NULL, user_id TEXT NOT NULL);
CREATE TABLE person_gift_ideas (id TEXT PRIMARY KEY, person_id TEXT NOT NULL, user_id TEXT NOT NULL);
CREATE TABLE person_bucket_list (id TEXT PRIMARY KEY, person_id TEXT NOT NULL, user_id TEXT NOT NULL);
CREATE TABLE person_important_dates (
    id TEXT PRIMARY KEY, person_id TEXT NOT NULL, user_id TEXT NOT NULL, label TEXT NOT NULL,
    date_type TEXT NOT NULL DEFAULT 'birthday', month INTEGER, day INTEGER, year INTEGER,
    reminder_days_before INTEGER NOT NULL DEFAULT 7, mem_id TEXT, created_at TEXT);
"""


def _mod():
    spec = importlib.util.spec_from_file_location("mig_0052", MIG)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _migrate(engine, fn="upgrade"):
    mod = _mod()
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        getattr(mod, fn)()


class _Db:
    """The compat connection's shape over sqlite3 (await-able execute, ``rowcount``); ``fail_on`` injects a locked-table error."""

    def __init__(self, path, fail_on: str = ""):
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.fail_on = fail_on

    def execute(self, sql, params=()):
        async def _run():
            if self.fail_on and self.fail_on in sql:
                raise sqlite3.OperationalError("database table is locked")
            cur = self.conn.execute(sql, tuple(params))
            return _Cursor(cur.fetchall(), rowcount=cur.rowcount)
        return _ExecResult(_run())

    async def commit(self):
        self.conn.commit()

    def q(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "zoe.db")
    raw = sqlite3.connect(path)
    raw.executescript(BASE_DDL)
    raw.close()
    _migrate(sa.create_engine(f"sqlite:///{path}"))
    d = _Db(path)
    yield d
    d.conn.close()


def run(coro):
    return asyncio.run(coro)


def person(db, user, name, **cols):
    pid = cols.pop("id", None) or f"p-{user[-1]}-{re.sub(r'[^a-z]', '', name.lower())}"
    keys = ["id", "user_id", "name", "created_at", "updated_at"] + list(cols)
    db.conn.execute(f"INSERT INTO people ({', '.join(keys)}) VALUES ({','.join('?' * len(keys))})",
                    (pid, user, name, "2026-10-01T00:00:00Z", "2026-10-01T00:00:00Z", *cols.values()))
    db.conn.commit()
    return pid


def vcard(**kw) -> str:
    card = vc.empty_card()
    card.update(kw)
    return vc.to_vcard(card)


# -- migration --------------------------------------------------------------------------------------------------------------

def test_revision_chain_has_exactly_one_head():
    mod = _mod()
    revs, downs = set(), set()
    for f in (SVC / "alembic/versions").glob("*.py"):
        src = f.read_text()
        r = re.search(r'^revision = "(\w+)"', src, re.M)
        d = re.search(r'^down_revision = "(\w+)"', src, re.M)
        revs.add(r.group(1)) if r else None
        downs.add(d.group(1)) if d else None
    assert mod.down_revision in revs
    assert len(revs - downs) == 1, f"more than one head: {sorted(revs - downs)}"


def test_upgrade_is_rerun_safe_and_downgrade_removes_only_what_it_added(tmp_path):
    path = str(tmp_path / "m.db")
    raw = sqlite3.connect(path)
    raw.executescript(BASE_DDL)
    raw.execute("INSERT INTO person_important_dates (id, person_id, user_id, label) VALUES ('d1', 'p1', 'u', 'Birthday')")
    raw.commit()
    raw.close()
    engine = sa.create_engine(f"sqlite:///{path}")
    _migrate(engine)
    _migrate(engine)                                   # rerun: a no-op
    with engine.connect() as c:
        tables = {r[0] for r in c.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
        dcols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(person_important_dates)")}
        fcols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(contact_fields)")}
        legacy = c.exec_driver_sql("SELECT source, read_only FROM person_important_dates").fetchall()
    assert set(_mod().TABLES) <= tables
    assert {"source", "source_contact_id", "provenance", "read_only"} <= dcols
    assert {"kind", "value", "value_norm", "parts", "types", "pref", "source", "source_contact_id", "provenance",
            "provenance_ref", "read_only"} <= fcols
    assert legacy == [("local", 0)]                    # an existing date reads as the member's own, editable
    _migrate(engine, "downgrade")
    _migrate(engine, "downgrade")                      # already gone: a no-op
    with engine.connect() as c:
        tables = {r[0] for r in c.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
        dcols = {r[1] for r in c.exec_driver_sql("PRAGMA table_info(person_important_dates)")}
        kept = c.exec_driver_sql("SELECT id FROM person_important_dates").fetchall()
    assert not set(_mod().TABLES) & tables and {"people", "person_relationships", "person_important_dates"} <= tables
    assert not {"source", "source_contact_id", "provenance", "read_only"} & dcols and kept == [("d1",)]


def test_checks_refuse_a_bad_pref_kind_and_provider(db):
    pid = person(db, A, "Ines Ferreira")
    for bad in ("pref", "kind", "source"):
        vals = {"pref": 5, "kind": "email", "source": "local"}
        vals[bad] = {"pref": 0, "kind": "fax_machine", "source": "myspace"}[bad]
        with pytest.raises(sqlite3.IntegrityError):
            db.conn.execute("INSERT INTO contact_fields (id, user_id, person_id, kind, value, pref, source, provenance, created_at, "
                            "updated_at) VALUES ('x', ?, ?, ?, 'v', ?, ?, 'entered_by_member', 't', 't')",
                            (A, pid, vals["kind"], vals["pref"], vals["source"]))


def test_no_request_time_ddl_in_the_contacts_modules():
    for rel in ("contacts_store.py", "contacts_vcard.py", "routers/contacts.py"):
        src = (SVC / rel).read_text()
        assert "CREATE TABLE" not in src and "ALTER TABLE" not in src and "DROP TABLE" not in src, rel


# -- vCard codec ------------------------------------------------------------------------------------------------------------

def test_vcf_fixture_parses_every_mapped_field():
    marisol, tove, ines = vc.parse_vcards(VCF)
    assert marisol["full_name"] == "Marisol Okafor-Vance"
    assert marisol["name"] == {"surname": "Okafor-Vance", "given": "Marisol", "given2": "Ines", "title": "Dr.", "credential": "PhD",
                               "phonetic": {"given": "mah-ree-SOL", "surname": "oh-KAH-for"}, "say_as": "mah-ree-SOL oh-KAH-for"}
    assert [e["value"] for e in marisol["emails"]] == ["marisol@example.org", "m.okafor@example.net"]
    assert marisol["emails"][0]["pref"] == 1 and marisol["emails"][1]["types"] == ["work"]
    assert [p["value"] for p in marisol["phones"]] == ["+61 400 555 101", "(08) 5550 1202", "+61-8-5550-1303"]   # tel: URI unwrapped
    assert marisol["addresses"][0]["parts"]["street"] == "12 Example Lane"                                      # a folded line
    assert marisol["notes"][0]["value"] == "Allergic to peanuts, loves ferns; met at the seed swap.\nCall after 5."
    assert marisol["dates"] == [{"kind": "birthday", "year": 1986, "month": 4, "day": 12},
                                {"kind": "anniversary", "year": None, "month": 6, "day": 5}]
    assert marisol["groups"] == ["Garden club", "Book club"] and marisol["pronouns"][0]["value"] == "she/her"
    assert [r["name"] for r in marisol["related"]] == ["Tove Lindqvist", "Nobody Known"]
    assert tove["emails"][0] == {"value": "tove@example.com", "types": [], "pref": 1, "label": None}   # v3 TYPE=pref, INTERNET
    assert tove["photos"] == [] and tove["photos_inline_dropped"] == 1           # an inline photo is never kept
    assert tove["urls"][0]["value"] == "https://tove.example.com"                 # a group-prefixed line
    assert ines["unmapped"] == 1                                                  # GENDER: not mapped, counted


def test_vcf_round_trip_is_lossless_for_mapped_fields():
    for card in vc.parse_vcards(VCF):
        again = vc.parse_vcards(vc.to_vcard(card))[0]
        card = {k: v for k, v in card.items() if k not in ("unmapped", "photos_inline_dropped")}
        assert {k: again[k] for k in card} == card


def test_folding_never_splits_a_utf8_sequence_and_escapes_survive():
    text = vcard(full_name="Zoë Ångström-Øyen " * 6, notes=[{"value": "a,b;c\\d\nnext"}])
    for line in text.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75
        line.encode("utf-8").decode("utf-8")
    card = vc.parse_vcards(text)[0]
    assert card["full_name"] == ("Zoë Ångström-Øyen " * 6).strip() and card["notes"][0]["value"] == "a,b;c\\d\nnext"


def test_rfc9554_seven_component_n_and_phonetic_altid():
    text = ("BEGIN:VCARD\r\nVERSION:4.0\r\nFN:Ana Lucia Pereira Gomes Jr.\r\n"
            "N;ALTID=1:Pereira,Gomes;Ana;Lucia;;Jr.;Gomes;Jr.\r\n"
            "N;ALTID=1;PHONETIC=ipa;SCRIPT=Latn:pɛˈɾejɾɐ;ˈɐnɐ;;;;;\r\nEND:VCARD\r\n")
    card = vc.parse_vcards(text)[0]
    assert card["name"]["surname"] == "Pereira" and card["name"]["surname2"] == "Gomes" and card["name"]["generation"] == "Jr."
    assert "credential" not in card["name"]                    # the back-compat copy in suffixes is ignored
    assert card["name"]["phonetic"] == {"surname": "pɛˈɾejɾɐ", "given": "ˈɐnɐ"} and card["name"]["phonetic_system"] == "ipa"
    again = vc.parse_vcards(vc.to_vcard(card))[0]
    assert again["name"] == card["name"]


# -- import + merge model ---------------------------------------------------------------------------------------------------

def test_import_creates_private_people_with_provenanced_multi_value_fields(db):
    rep = run(cs.import_vcf(db, A, VCF))
    assert rep["cards"] == 3 and rep["created"] == 3 and rep["photos_inline_dropped"] == 1
    rows = db.q("SELECT name, visibility, user_id FROM people ORDER BY name")
    assert rows == [("Ines Ferreira", "personal", A), ("Marisol Okafor-Vance", "personal", A), ("Tove Lindqvist", "personal", A)]
    pid = db.q("SELECT id FROM people WHERE name = 'Marisol Okafor-Vance'")[0][0]
    card = run(cs.card_for_person(db, A, pid))
    assert [e["value"] for e in card["emails"]] == ["marisol@example.org", "m.okafor@example.net"]
    assert all(e["provenance"] == "file_import" and e["source"] == "phone" and e["provenance_ref"].startswith("source_contact:")
               and not e["read_only"] for e in card["emails"])
    assert len(card["phones"]) == 3 and card["addresses"][0]["parts"]["locality"] == "Springfield"
    assert card["name"]["say_as"] == "mah-ree-SOL oh-KAH-for" and card["groups"] == ["Garden club", "Book club"]
    assert {(d["kind"], d["month"], d["day"]) for d in card["dates"]} == {("birthday", 4, 12), ("anniversary", 6, 5)}
    assert db.q("SELECT source, provenance FROM person_important_dates WHERE person_id = ? AND date_type = 'birthday'", (pid,)) == \
        [("phone", "file_import")]
    # RELATED: Tove is in the same file -> an edge on the people graph (no second store); "Nobody Known" -> a relation review
    edges = db.q("SELECT rel_type, origin, turn_id FROM person_relationships WHERE user_id = ?", (A,))
    assert len(edges) == 1 and edges[0][0] == "friend" and edges[0][1] == "contact_sync" and edges[0][2].startswith("source_contact:")
    assert db.q("SELECT kind, reason FROM contact_merge_reviews WHERE user_id = ?", (A,)) == [("relation", "relation_unresolved")]
    assert [r["name"] for r in card["related"]] == ["Tove Lindqvist"]


def test_store_card_round_trips_through_vcard_export(db):
    run(cs.import_vcf(db, A, VCF))
    pid = db.q("SELECT id FROM people WHERE name = 'Marisol Okafor-Vance'")[0][0]
    exported = run(cs.export_vcard(db, A, pid))
    back = vc.parse_vcards(exported)[0]
    src = vc.parse_vcards(VCF)[0]
    assert back["uid"] == f"urn:uuid:{pid}"
    for key in ("emails", "phones", "addresses", "orgs", "titles", "urls", "socials", "ims", "photos", "notes", "nicknames",
                "pronouns"):
        assert [(x.get("value"), x.get("parts") or {}) for x in back[key]] == \
            [(x.get("value"), x.get("parts") or {}) for x in src[key]], key
    assert [x.get("pref") for x in back["emails"]] == [1, None]
    assert back["name"] == src["name"] and back["groups"] == src["groups"]
    assert sorted((d["kind"], d["month"], d["day"]) for d in back["dates"]) == sorted((d["kind"], d["month"], d["day"]) for d in src["dates"])
    assert back["related"][0]["name"] == "Tove Lindqvist" and back["related"][0]["types"] == ["friend"]
    # re-importing Zoe's own export links by the urn:uuid UID, never a second Marisol
    out = run(cs.import_card(db, A, run(cs.ensure_source(db, A, "carddav", source_account_id="dav1")), back, external_id="x.vcf"))
    assert out.action == "linked" and out.person_id == pid and out.reason == "uid"
    assert db.q("SELECT COUNT(*) FROM people WHERE name = 'Marisol Okafor-Vance'") == [(1,)]


def test_reimport_replaces_only_that_sources_fields_and_unchanged_is_a_noop(db):
    src = run(cs.ensure_source(db, A, "google", source_account_id="acct-1"))
    card = vc.parse_vcards(VCF)[2]
    first = run(cs.import_card(db, A, src, card, external_id="people/c1", etag="e1"))
    pid = first.person_id
    run(cs.add_field(db, A, pid, kind="email", value="ines.home@example.org", provenance="said_by_member"))
    assert run(cs.import_card(db, A, src, card, external_id="people/c1", etag="e1")).action == "unchanged"
    card2 = dict(card, phones=[{"value": "+61 400 555 999", "types": ["cell"], "pref": 1}])
    assert run(cs.import_card(db, A, src, card2, external_id="people/c1", etag="e2")).action == "updated"
    got = run(cs.card_for_person(db, A, pid))
    assert [p["value"] for p in got["phones"]] == ["+61 400 555 999"]
    assert got["phones"][0]["read_only"] is True and got["phones"][0]["provenance"] == "synced"     # google = externally owned
    assert [e["value"] for e in got["emails"]] == ["ines.home@example.org"]                            # the member's own survives
    with pytest.raises(cs.ContactsError) as exc:
        run(cs.remove_field(db, A, got["phones"][0]["id"]))
    assert exc.value.status == 409


def test_strong_identifier_links_to_the_existing_person(db):
    pid = person(db, A, "Tove", email="TOVE@example.com", visibility="personal")      # a legacy single-value email, first name only
    src = run(cs.ensure_source(db, A, "phone", source_account_id="file"))
    out = run(cs.import_card(db, A, src, vc.parse_vcards(VCF)[1], external_id="t1"))
    assert (out.action, out.person_id, out.reason) == ("linked", pid, "email")
    assert db.q("SELECT COUNT(*) FROM people WHERE user_id = ?", (A,)) == [(1,)]
    assert db.q("SELECT method, linked_by FROM person_contact_links WHERE person_id = ?", (pid,)) == [("email", "zoe")]


def test_name_only_match_is_a_review_never_a_merge(db):
    pid = person(db, A, "Ines Ferreira")
    src = run(cs.ensure_source(db, A, "phone", source_account_id="file"))
    out = run(cs.import_card(db, A, src, vc.parse_vcards(VCF)[2], external_id="i1"))
    assert out.action == "review" and out.reason == "name_only"
    assert db.q("SELECT COUNT(*) FROM people") == [(1,)]                                   # no duplicate person
    assert db.q("SELECT COUNT(*) FROM contact_fields") == [(0,)]                           # nothing merged in
    assert db.q("SELECT COUNT(*) FROM person_contact_links") == [(0,)]
    (rv,) = run(cs.list_reviews(db, A))
    assert rv["candidate_ids"] == [pid] and rv["incoming"]["full_name"] == "Ines Ferreira"
    # the same card again does not open a second review
    run(cs.import_card(db, A, src, vc.parse_vcards(VCF)[2], external_id="i1"))
    assert len(run(cs.list_reviews(db, A))) == 1
    # the member says it is the same person -> linked, fields applied, provenance says who decided
    assert run(cs.resolve_review(db, A, rv["id"], "link", person_id=pid))["status"] == "linked"
    assert db.q("SELECT method, linked_by FROM person_contact_links") == [("member_confirmed", A)]
    assert [p["value"] for p in run(cs.card_for_person(db, A, pid))["phones"]] == ["+61 400 555 303"]
    with pytest.raises(cs.ContactsError):
        run(cs.resolve_review(db, A, rv["id"], "dismiss"))                                 # resolved once


def test_several_candidates_and_identifier_conflict_are_reviews(db):
    person(db, A, "Ines Ferreira", phone="0400 555 303")
    person(db, A, "Ines F", id="p-dup", phone="+61400555303")
    src = run(cs.ensure_source(db, A, "phone", source_account_id="file"))
    out = run(cs.import_card(db, A, src, vc.parse_vcards(VCF)[2], external_id="i1"))
    assert (out.action, out.reason) == ("review", "several_candidates")
    # one shared household email, a different name: an identifier conflict, not a merge
    person(db, A, "Quinn Harrow", email="marisol@example.org")
    out = run(cs.import_card(db, A, src, vc.parse_vcards(VCF)[0], external_id="m1"))
    assert (out.action, out.reason) == ("review", "identifier_conflict")
    assert db.q("SELECT COUNT(*) FROM people WHERE name LIKE 'Marisol%'") == [(0,)]
    # "keep them separate" creates the new person and links it
    rid = out.review_id
    res = run(cs.resolve_review(db, A, rid, "new"))
    assert res["status"] == "kept_separate" and db.q("SELECT name, visibility FROM people WHERE id = ?", (res["person_id"],)) == \
        [("Marisol Okafor-Vance", "personal")]


# -- back-compat + member edits ---------------------------------------------------------------------------------------------

def test_legacy_columns_read_as_legacy_entries_and_are_never_written(db):
    pid = person(db, A, "Ines Ferreira", email="ines@example.org", phone="0400 555 303", birthday="1990-02-03",
                 notes="Neighbour.", visibility="family")
    before = db.q("SELECT * FROM people WHERE id = ?", (pid,))
    card = run(cs.card_for_person(db, A, pid))
    assert [(e["value"], e["provenance"]) for e in card["emails"]] == [("ines@example.org", "legacy")]
    assert card["dates"] == [{"id": None, "kind": "birthday", "year": 1990, "month": 2, "day": 3, "source": "local",
                              "provenance": "legacy", "read_only": False}]
    run(cs.add_field(db, A, pid, kind="phone", value="+61 400 555 303", types=["cell"], pref=1))
    card = run(cs.card_for_person(db, A, pid))
    assert [(p["value"], p["provenance"]) for p in card["phones"]] == [("+61 400 555 303", "entered_by_member")]   # same number: deduped
    src = run(cs.ensure_source(db, A, "google", source_account_id="g"))
    run(cs.import_card(db, A, src, vc.parse_vcards(VCF)[2], external_id="people/c9"))        # strong phone match -> linked
    assert db.q("SELECT * FROM people WHERE id = ?", (pid,)) == before                         # the shared row is never written


def test_member_field_validation(db):
    pid = person(db, A, "Ines Ferreira")
    for kw in ({"kind": "photo", "value": "data:image/png;base64,AAAA"}, {"kind": "email", "value": "not-an-email"},
               {"kind": "phone", "value": "1", "pref": 0}, {"kind": "fax", "value": "1"}, {"kind": "url", "value": "x", "types": ["Bad Type"]}):
        with pytest.raises(cs.ContactsError):
            run(cs.add_field(db, A, pid, **kw))
    ok = run(cs.add_field(db, A, pid, kind="photo", value="zoe-media:abc123"))
    assert ok["read_only"] is False and "zoe-media" not in run(cs.export_vcard(db, A, pid))    # a local photo never leaves the box


# -- member-private visibility ----------------------------------------------------------------------------------------------

def test_member_b_never_sees_member_a_synced_contacts(db):
    run(cs.import_vcf(db, A, VCF))
    pid = db.q("SELECT id FROM people WHERE name = 'Marisol Okafor-Vance'")[0][0]
    db.conn.execute("UPDATE people SET visibility = 'family' WHERE id = ?", (pid,))      # even a family-visible row
    db.conn.commit()
    assert run(cs.card_for_person(db, B, pid)) is None and run(cs.export_vcard(db, B, pid)) is None
    assert run(cs.list_reviews(db, B)) == []
    with pytest.raises(cs.ContactsError) as exc:
        run(cs.add_field(db, B, pid, kind="note", value="B's note"))
    assert exc.value.status == 404
    fid = run(cs.card_for_person(db, A, pid))["emails"][0]["id"]
    with pytest.raises(cs.ContactsError):
        run(cs.remove_field(db, B, fid))


def test_member_b_never_links_to_member_a_people(db):
    run(cs.import_vcf(db, A, VCF))
    rep = run(cs.import_vcf(db, B, VCF))                    # the same file: B gets B's own people, nothing of A's is touched
    assert rep["created"] == 3 and rep["linked"] == 0
    assert db.q("SELECT user_id, COUNT(*) FROM people GROUP BY user_id ORDER BY user_id") == [(A, 3), (B, 3)]
    assert db.q("SELECT COUNT(*) FROM person_contact_links l JOIN people p ON p.id = l.person_id WHERE l.user_id != p.user_id") == [(0,)]


# -- forget ------------------------------------------------------------------------------------------------------------------

def _voiceprint(db, owner, people_id, model="resnet34-int8"):
    db.conn.execute("INSERT INTO learned_voiceprints (id, owner_user_id, people_id, model_id, embedding, n_segments, n_recordings, "
                    "created_at, updated_at, expires_at) VALUES (?, ?, ?, ?, ?, 3, 2, 't', 't', '2027-01-01T00:00:00Z')",
                    (f"vp-{owner}-{people_id}-{model}", owner, people_id, model, b"\x00" * 16))
    db.conn.commit()


@pytest.mark.asyncio
async def test_forget_removes_contact_rows_links_and_the_voiceprint(db, ledger_env):
    import memory_forget_cascade as cascade
    await cs.import_vcf(db, A, VCF)
    await cs.import_vcf(db, B, VCF)
    ma = db.q("SELECT id FROM people WHERE user_id = ? AND name LIKE 'Marisol%'", (A,))[0][0]
    mb = db.q("SELECT id FROM people WHERE user_id = ? AND name LIKE 'Marisol%'", (B,))[0][0]
    tove = db.q("SELECT id FROM people WHERE user_id = ? AND name = 'Tove Lindqvist'", (A,))[0][0]
    await cs.add_field(db, A, tove, kind="note", value="Introduced me to Marisol at the market.")
    await cs.add_field(db, A, tove, kind="note", value="Plays the cello.")
    _voiceprint(db, A, ma)
    _voiceprint(db, B, mb)
    sc_before = db.q("SELECT id, external_id FROM source_contacts WHERE user_id = ? AND card_json LIKE '%Marisol Okafor%'", (A,))

    res = await cascade.cascade_forget(A, "Marisol", db=db)

    assert "contacts" not in res.failed and "voiceprints" not in res.failed
    assert res.contacts > 0 and res.voiceprints == 1
    assert db.q("SELECT COUNT(*) FROM contact_fields WHERE person_id = ?", (ma,)) == [(0,)]
    assert db.q("SELECT value FROM contact_fields WHERE person_id = ? AND kind = 'note'", (tove,)) == [("Plays the cello.",)]
    assert db.q("SELECT COUNT(*) FROM person_contact_links WHERE person_id = ?", (ma,)) == [(0,)]
    assert db.q("SELECT COUNT(*) FROM person_important_dates WHERE person_id = ? AND source_contact_id IS NOT NULL", (ma,)) == [(0,)]
    assert db.q("SELECT COUNT(*) FROM learned_voiceprints WHERE owner_user_id = ?", (A,)) == [(0,)]
    (sc_id, ext), = sc_before
    tomb = db.q("SELECT state, card_json, uid, external_id FROM source_contacts WHERE id = ?", (sc_id,))[0]
    assert tomb[:3] == ("forgotten", "{}", None) and tomb[3].startswith("forgotten:") and "marisol" not in tomb[3].lower()
    # no row the member owns still names her in the contact tables
    for table in ("contact_fields", "source_contacts", "contact_merge_reviews"):
        assert not [r for r in db.q(f"SELECT * FROM {table} WHERE user_id = ?", (A,)) if "marisol" in json.dumps(r).lower()], table
    # Tove's card (linked, not about Marisol) is redacted, not tombstoned
    tove_card = db.q("SELECT state, card_json FROM source_contacts s JOIN person_contact_links l ON l.source_contact_id = s.id "
                     "WHERE l.person_id = ?", (tove,))
    assert tove_card[0][0] == "active"
    # member B's Marisol, card and voiceprint are untouched
    assert db.q("SELECT COUNT(*) FROM contact_fields WHERE person_id = ?", (mb,))[0][0] > 0
    assert db.q("SELECT COUNT(*) FROM learned_voiceprints WHERE owner_user_id = ?", (B,)) == [(1,)]
    # the next sync of the same card is suppressed, not re-imported
    src = await cs.ensure_source(db, A, "phone", source_account_id="file")
    out = await cs.import_card(db, A, src, vc.parse_vcards(VCF)[0], external_id=ext)
    assert out.action == "forgotten"
    assert db.q("SELECT COUNT(*) FROM people WHERE user_id = ? AND name LIKE 'Marisol%' AND deleted = 0", (A,)) == [(0,)]


@pytest.mark.asyncio
async def test_a_sync_after_the_forget_never_brings_the_name_back(db, ledger_env):
    """The tombstone covers the SAME card; the forget ledger (salted hashes, no names) covers a re-created one and every other card
    that mentions the person. Control: drop the ledger check in ``import_card`` -> the re-created card imports and Tove's note
    carries the name again."""
    import memory_forgotten
    assert await memory_forgotten.add(A, "Marisol", actor=A)
    tove = vc.empty_card()
    tove.update(full_name="Tove Lindqvist", notes=[{"value": "Met Marisol at the seed swap."}, {"value": "Plays the cello."}],
                related=[{"name": "Marisol Okafor-Vance", "uri": None, "types": ["friend"], "zoe_rel": None, "zoe_role": None}])
    text = vc.to_vcard(vc.parse_vcards(VCF)[0]) + vc.to_vcard(tove)
    rep = await cs.import_vcf(db, A, text)
    assert rep["forgotten"] == 1 and rep["created"] == 1 and rep["relation_reviews"] == 0
    for table in ("contact_fields", "source_contacts", "contact_merge_reviews", "people"):
        assert not [r for r in db.q(f"SELECT * FROM {table} WHERE user_id = ?", (A,)) if "marisol" in json.dumps(r).lower()], table
    assert db.q("SELECT value FROM contact_fields WHERE kind = 'note'") == [("Plays the cello.",)]
    again = await cs.import_vcf(db, A, text)                       # the tombstone answers the second time without the ledger read
    assert again["forgotten"] == 1 and again["unchanged"] == 1
    assert (await cs.import_vcf(db, B, text))["created"] == 2      # B never forgot her: B's import is untouched


@pytest.mark.asyncio
async def test_forget_contacts_step_fails_loud(db, ledger_env, monkeypatch):
    """A locked contact table is a FAILED step (the forget is not confirmed), never a silent skip."""
    import memory_forget_cascade as cascade
    monkeypatch.setattr(cascade, "RETRY_DELAY_S", 0)
    await cs.import_vcf(db, A, VCF)
    flaky = _Db(db.conn.execute("PRAGMA database_list").fetchall()[0][2], fail_on="FROM learned_voiceprints")
    res = await cascade.cascade_forget(A, "Marisol", db=flaky)
    assert "voiceprints" in res.failed and not res.complete
    flaky.conn.close()


@pytest.mark.asyncio
async def test_forget_user_erases_every_contact_row_of_the_member_only(db):
    await cs.import_vcf(db, A, VCF)
    await cs.import_vcf(db, B, VCF)
    _voiceprint(db, A, db.q("SELECT id FROM people WHERE user_id = ? LIMIT 1", (A,))[0][0])
    n = await cs.forget_user(A, db=db)
    assert n > 0
    for table, col in cs.FORGET_USER_TABLES:
        assert db.q(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (A,)) == [(0,)], table
        assert table == "learned_voiceprints" or db.q(f"SELECT COUNT(*) FROM {table} WHERE {col} = ?", (B,))[0][0] > 0, table
    assert db.q("SELECT COUNT(*) FROM person_important_dates WHERE user_id = ? AND source_contact_id IS NOT NULL", (A,)) == [(0,)]


def test_the_cascade_owns_the_new_steps():
    import memory_forget_cascade as cascade
    assert {"contacts", "voiceprints"} <= cascade.DB_STEPS and {"contacts", "voiceprints"} <= set(cascade.ALL_LABELS)
    src = (SVC / "memory_service.py").read_text()
    assert "contacts_store.forget_user" in src        # the audited member erase reaches the contact model


# -- person merge ------------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_person_merge_repoints_contact_rows_and_never_averages_a_voiceprint(db):
    import person_merge
    stub = person(db, A, "Ines", is_partial=1)
    real = person(db, A, "Ines Ferreira")
    await cs.add_field(db, A, stub, kind="nickname", value="Nessie")
    _voiceprint(db, A, stub, model="m1")
    _voiceprint(db, A, stub, model="m2")
    _voiceprint(db, A, real, model="m1")
    await person_merge.merge_person(db, A, stub, real)
    assert db.q("SELECT person_id FROM contact_fields WHERE value = 'Nessie'") == [(real,)]
    assert sorted(db.q("SELECT id, people_id FROM learned_voiceprints")) == sorted([
        (f"vp-{A}-{real}-m1", real), (f"vp-{A}-{stub}-m2", real)])    # the survivor keeps its own m1; the stub's m2 moves


# -- router (flag-dark) ------------------------------------------------------------------------------------------------------

@pytest.fixture
def api(db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import auth
    from db_pool import get_db
    from routers import contacts as contacts_router

    who = {"user": {"user_id": A, "role": "member", "username": "a"}}

    async def _db():
        yield db

    async def _ok(*_a, **_k):
        return None

    monkeypatch.setattr(contacts_router, "require_feature_access", _ok)
    monkeypatch.setenv("ZOE_CONTACTS_API", "1")
    app = FastAPI()
    assert contacts_router.register(app) is True
    app.dependency_overrides[auth.require_signed_in] = lambda: who["user"]
    app.dependency_overrides[get_db] = _db
    return type("Api", (), {"client": TestClient(app), "who": who})


def test_flag_off_routes_are_absent(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from routers import contacts as contacts_router
    monkeypatch.delenv("ZOE_CONTACTS_API", raising=False)
    app = FastAPI()
    assert contacts_router.register(app) is False
    assert TestClient(app).get("/api/contacts/reviews").status_code == 404


def test_api_import_card_export_and_member_isolation(api, db):
    r = api.client.post("/api/contacts/import?provider=phone", content=VCF.encode(), headers={"Content-Type": "text/vcard"})
    assert r.status_code == 200 and r.json()["created"] == 3
    pid = db.q("SELECT id FROM people WHERE name = 'Ines Ferreira'")[0][0]
    card = api.client.get(f"/api/contacts/people/{pid}").json()
    assert card["phones"][0]["value"] == "+61 400 555 303"
    r = api.client.post(f"/api/contacts/people/{pid}/fields", json={"kind": "email", "value": "ines@example.org", "pref": 1})
    assert r.status_code == 200 and r.json()["provenance"] == "entered_by_member"
    v = api.client.get(f"/api/contacts/people/{pid}/vcard")
    assert v.status_code == 200 and v.headers["content-type"].startswith("text/vcard") and "EMAIL;PREF=1:ines@example.org" in v.text
    assert len(api.client.get("/api/contacts/reviews").json()["reviews"]) == 1          # the unresolved RELATED
    api.who["user"] = {"user_id": B, "role": "admin", "username": "b"}                    # admin never widens it
    assert api.client.get(f"/api/contacts/people/{pid}").status_code == 404
    assert api.client.get("/api/contacts/reviews").json()["reviews"] == []
    api.who["user"] = {"user_id": A, "role": "member", "username": "panel:kitchen", "device_token_role": "member"}
    assert api.client.get(f"/api/contacts/people/{pid}").status_code == 403              # a panel token is not a person
