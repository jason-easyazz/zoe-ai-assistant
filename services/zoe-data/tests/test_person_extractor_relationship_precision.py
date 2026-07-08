"""Precision guard for regex-extracted relationship edges.

The name regex (`_REL_RE`) captures any initial-capital token, so utterances like
"She is Tom's sister" or "He is Jason's brother" — perfectly natural in speech —
would otherwise silently mint a junk ``She``/``He`` person node **and** a
relationship edge. `_looks_like_person_name` rejects the pronoun/sentence-opener
tokens so those never reach the write path, while real (incl. multi-word) names
still extract. Runs slim: pure-function checks + `process_text` over an in-memory
SQLite DB (no model loads, relationship-only input never touches MemPalace).
"""
import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep → GitHub -m ci_safe lane (see tests/AGENTS.md)

import aiosqlite

import person_extractor as pe

USER = "demo_precision_user"  # a DEMO user — never a real person


@pytest.mark.parametrize("name", ["He", "She", "They", "We", "It", "There", "Here",
                                  "This", "That", "Who", "What", "Monday", "Friday", "the"])
def test_rejects_non_names(name):
    assert pe._looks_like_person_name(name) is False


@pytest.mark.parametrize("name", ["Sarah", "Tom", "Mary Jane", "April", "May",
                                  "Grace", "Will", "Bob Smith"])
def test_accepts_real_names(name):
    """Recall is preserved — real given names (incl. calendar-word collisions) pass."""
    assert pe._looks_like_person_name(name) is True


async def _open_db():
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute(
        """CREATE TABLE people (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT NOT NULL,
            relationship TEXT, how_we_met TEXT, first_met_date TEXT, notes TEXT,
            circle TEXT, context TEXT, visibility TEXT, deleted INTEGER NOT NULL DEFAULT 0,
            is_partial INTEGER NOT NULL DEFAULT 0, introduced_by_person_id TEXT,
            created_at TEXT, updated_at TEXT)"""
    )
    await db.execute(
        """CREATE TABLE person_relationships (
            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, person_a_id TEXT NOT NULL,
            person_b_id TEXT NOT NULL, rel_type TEXT NOT NULL, rel_a_to_b TEXT NOT NULL,
            rel_b_to_a TEXT NOT NULL, rel_group TEXT NOT NULL, notes TEXT,
            created_at TEXT, updated_at TEXT, valid_from TEXT, valid_to TEXT, superseded_by TEXT)"""
    )
    await db.execute(
        "CREATE UNIQUE INDEX person_relationships_pair_active "
        "ON person_relationships(user_id, person_a_id, person_b_id) WHERE valid_to IS NULL"
    )
    await db.commit()
    return db


async def _counts(db):
    async with db.execute("SELECT COUNT(*) FROM person_relationships WHERE user_id=?", (USER,)) as c:
        edges = (await c.fetchone())[0]
    async with db.execute("SELECT name FROM people WHERE user_id=?", (USER,)) as c:
        names = {r[0] for r in await c.fetchall()}
    return edges, names


@pytest.mark.asyncio
async def test_real_relationship_still_extracts():
    db = await _open_db()
    try:
        n = await pe.process_text("Sarah is Tom's sister", user_id=USER, db=db)
        edges, names = await _counts(db)
        assert n == 1 and edges == 1
        assert "Sarah" in names and "Tom" in names
    finally:
        await db.close()


@pytest.mark.parametrize("text", [
    "She is Tom's sister",
    "He is Jason's brother",
    "There is Something's friend",
])
@pytest.mark.asyncio
async def test_pronoun_relationship_is_dropped(text):
    db = await _open_db()
    try:
        n = await pe.process_text(text, user_id=USER, db=db)
        edges, names = await _counts(db)
        assert n == 0 and edges == 0, f"junk edge written for {text!r}"
        # no junk node minted for the pronoun (nor for the other side — whole edge skipped)
        assert not ({"He", "She", "They", "There"} & names), f"junk node minted for {text!r}: {names}"
    finally:
        await db.close()
