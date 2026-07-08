"""Compact per-person dossier line in the memory compose packet (flag-gated).

Proves: (1) OFF is a byte-for-byte no-op — the thin `Name (rel) — notes` line;
(2) ON folds relationship · circle · score · recent likes · notes · contact into
one clipped, cited line; (3) the extra facts read is skipped when OFF. Slim: pure
`_build_lines`/`_dossier_line` + `_fetch_relational` over in-memory SQLite.
"""
import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep → GitHub -m ci_safe lane

import aiosqlite

import zoe_memory_compose as zc

USER = "demo_dossier_user"


def _person(**kw):
    base = {"id": "p1", "name": "Jason Bertelsen", "relationship": "brother",
            "circle": "family", "context": None, "notes": None, "email": None,
            "phone": None, "birthday": None, "preferences": None, "health_score": 0.82}
    base.update(kw)
    return base


def test_off_is_thin_line(monkeypatch):
    monkeypatch.delenv("ZOE_PERSON_DOSSIER_ENABLED", raising=False)
    data = {"people": [_person(notes="met at work")], "relationships": [], "dates": [], "facts": {}}
    lines, _ = zc._build_lines(data, "")
    assert lines == ["- Jason Bertelsen (brother) — met at work [people]"]


def test_on_builds_dossier(monkeypatch):
    monkeypatch.setenv("ZOE_PERSON_DOSSIER_ENABLED", "1")
    data = {
        "people": [_person(email="j@x.io", phone="555-1", birthday="Aug 4")],
        "relationships": [], "dates": [],
        "facts": {"p1": ["Jason likes chocolate", "Jason likes fruit loops", "Jason enjoys travelling"]},
    }
    lines, _ = zc._build_lines(data, "")
    line = lines[0]
    assert line.startswith("- Jason Bertelsen (brother · family, score 82) —")
    # likes are folded in, own-name prefix stripped, compact
    assert "likes chocolate" in line and "likes fruit loops" in line and "enjoys travelling" in line
    assert "Jason likes chocolate" not in line  # name prefix stripped
    # contact folded in
    assert "j@x.io" in line and "555-1" in line and "b.Aug 4" in line
    assert line.endswith("[people]")


def test_dossier_line_drops_missing_segments(monkeypatch):
    # only a name + score → no em-dash body, no crash
    line = zc._dossier_line({"id": "p1", "name": "Solo", "health_score": None}, [])
    assert line == "Solo"


def test_score_formatting():
    assert zc._fmt_score(0.82) == "score 82"
    assert zc._fmt_score(1.0) == "score 100"
    assert zc._fmt_score(None) is None
    assert zc._fmt_score("nope") is None


async def _open_db():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(
        """CREATE TABLE people (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
            relationship TEXT, circle TEXT, context TEXT, notes TEXT, email TEXT,
            phone TEXT, birthday TEXT, preferences TEXT, health_score REAL DEFAULT 0.5,
            visibility TEXT DEFAULT 'family', deleted INTEGER DEFAULT 0,
            is_partial INTEGER DEFAULT 0, last_contacted_at TEXT)"""
    )
    await db.execute(
        """CREATE TABLE person_relationships (
            id TEXT PRIMARY KEY, user_id TEXT, person_a_id TEXT, person_b_id TEXT,
            rel_a_to_b TEXT, notes TEXT, updated_at TEXT, valid_to TEXT)"""
    )
    await db.execute(
        """CREATE TABLE person_important_dates (
            id TEXT PRIMARY KEY, user_id TEXT, person_id TEXT, label TEXT,
            date_type TEXT, month INTEGER, day INTEGER, year INTEGER)"""
    )
    await db.execute(
        """CREATE TABLE person_activities (
            id TEXT PRIMARY KEY, user_id TEXT, person_id TEXT, activity_type TEXT,
            description TEXT, created_at TEXT)"""
    )
    await db.execute(
        "INSERT INTO people (id, user_id, name, relationship, circle, health_score, visibility)"
        " VALUES ('p1', ?, 'Jason', 'brother', 'family', 0.9, 'family')", (USER,))
    for i, d in enumerate(["Jason likes chocolate", "Jason likes fruit loops"]):
        await db.execute(
            "INSERT INTO person_activities (id, user_id, person_id, activity_type, description, created_at)"
            " VALUES (?,?,?,?,?,?)", (f"a{i}", USER, "p1", "fact", d, f"2026-07-0{i+1}"))
    await db.commit()
    return db


@pytest.mark.asyncio
async def test_fetch_reads_facts_only_when_enabled(monkeypatch):
    db = await _open_db()
    try:
        monkeypatch.delenv("ZOE_PERSON_DOSSIER_ENABLED", raising=False)
        off = await zc._fetch_relational(db, USER)
        assert off["facts"] == {}, "facts read skipped when flag OFF"

        monkeypatch.setenv("ZOE_PERSON_DOSSIER_ENABLED", "1")
        on = await zc._fetch_relational(db, USER)
        assert "p1" in on["facts"] and len(on["facts"]["p1"]) == 2
    finally:
        await db.close()
