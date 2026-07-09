"""Phase 2b.2 of contacts-from-known-people (ADR-contacts-from-known-people.md).

Covers the broadened backfill sources + the LLM extraction pass added to
`contact_backfill`:

- the synthesized `user_portraits` prose and `fact`/`relationship` memories are
  now read (the user's `people` table was empty; family lived ONLY in narrative);
- LLM-extracted `{name, relationship}` people become `person_create` proposals;
- LLM results merge with the deterministic regex results (dedup by name);
- dedup against an existing contact still holds;
- pronoun / junk names are still rejected by the precision guard;
- flag-off is a byte-for-byte no-op;
- a malformed / raising LLM response falls back to the regex results, no crash.

The LLM is ALWAYS mocked — no network. Slim-dep: in-memory SQLite `people` +
`user_portraits` tables, a fake memory source, and a recording `store_suggestions`.
"""
import re

import aiosqlite
import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep → GitHub -m ci_safe lane

import contact_backfill as cb

USER = "demo_backfill_user"  # a DEMO user — never a real person

PORTRAIT = (
    "Jason is a devoted family man. He often speaks about his parents, Janice "
    "and Niel, and his sisters, Karen and Julie, who live nearby."
)


class _Ref:
    def __init__(self, text, memory_type="fact", entity_type="", tags=""):
        self.text = text
        self.metadata = {
            "memory_type": memory_type,
            "entity_type": entity_type,
            "tags": tags,
        }


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    async def fetchone(self):
        return self._rows[0] if self._rows else None

    async def fetchall(self):
        return self._rows


class _DB:
    """asyncpg-ish shim over aiosqlite: $N→? rewrite, execute() returns a cursor."""

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


async def _open(existing=(), portrait=None):
    db = await aiosqlite.connect(":memory:")
    await db.execute(
        """CREATE TABLE people (
            id TEXT PRIMARY KEY, user_id TEXT, name TEXT, relationship TEXT,
            circle TEXT, context TEXT, visibility TEXT,
            is_partial INTEGER DEFAULT 0, deleted INTEGER DEFAULT 0)"""
    )
    await db.execute(
        "CREATE TABLE user_portraits (user_id TEXT PRIMARY KEY, portrait_text TEXT)"
    )
    for i, nm in enumerate(existing):
        await db.execute(
            "INSERT INTO people (id, user_id, name, deleted) VALUES (?,?,?,0)",
            (f"p{i}", USER, nm),
        )
    if portrait is not None:
        await db.execute(
            "INSERT INTO user_portraits (user_id, portrait_text) VALUES (?,?)",
            (USER, portrait),
        )
    await db.commit()
    return db


def _fake_memory_source(monkeypatch, refs):
    class _Svc:
        async def load_for_prompt(self, user_id, *, limit=200):
            return list(refs)

    import memory_service

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _Svc())
    monkeypatch.setattr(memory_service, "is_guest_memory_user", lambda u: not u)


def _capture_store(monkeypatch):
    stored: list[dict] = []

    async def _store(user_id, session_id, suggestions):
        stored.extend(suggestions)
        return len(suggestions)

    import pending_suggestions

    monkeypatch.setattr(pending_suggestions, "store_suggestions", _store)
    return stored


def _use_db(monkeypatch, db):
    import person_extractor

    async def _ensure(db_arg):
        return (db_arg if db_arg is not None else _DB(db)), False

    monkeypatch.setattr(person_extractor, "_ensure_db", _ensure)


def _mock_llm(monkeypatch, pairs, *, capture=None):
    """Replace the LLM extraction pass with a stub returning `pairs`.

    If `capture` (a list) is given, records the combined text the pass received
    — lets a test assert the portrait / fact text actually reached the LLM.
    """

    async def _extract(text):
        if capture is not None:
            capture.append(text)
        return list(pairs)

    monkeypatch.setattr(cb, "_llm_extract_people", _extract)


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_portrait_and_llm_extraction_emit_proposals(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_BACKFILL_ENABLED", "1")
    db = await _open(portrait=PORTRAIT)
    try:
        # A fact memory (regex-extractable) + the portrait (only the LLM parses
        # the third-person prose). LLM returns the family it read from both.
        _fake_memory_source(monkeypatch, [_Ref("Julie loves gardening.")])
        _use_db(monkeypatch, db)
        seen: list[str] = []
        _mock_llm(
            monkeypatch,
            [("Janice", "parent"), ("Karen", "sister")],
            capture=seen,
        )
        stored = _capture_store(monkeypatch)

        res = await cb.backfill_contacts(USER)

        # The portrait prose AND the fact memory both reached the LLM pass —
        # proof both sources are now read.
        assert seen and "his parents, Janice and Niel" in seen[0]
        assert "Julie loves gardening." in seen[0]

        names = {s["pre_filled_slots"]["name"] for s in stored}
        # Janice + Karen from the LLM; Julie from the deterministic regex over
        # the fact memory. Merged into one proposal set.
        assert {"Janice", "Karen", "Julie"} <= names
        assert all(s["action_type"] == "person_create" for s in stored)
        rel = {s["pre_filled_slots"]["name"]: s["pre_filled_slots"].get("relationship")
               for s in stored}
        assert rel["Janice"] == "parent"
        assert rel["Karen"] == "sister"
        assert res["proposed"] == len(stored)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_llm_result_dedups_against_existing_contact(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_BACKFILL_ENABLED", "1")
    db = await _open(existing=["Janice"])
    try:
        _fake_memory_source(monkeypatch, [])
        _use_db(monkeypatch, db)
        _mock_llm(monkeypatch, [("Janice", "parent"), ("Karen", "sister")])
        stored = _capture_store(monkeypatch)

        res = await cb.backfill_contacts(USER)

        names = {s["pre_filled_slots"]["name"] for s in stored}
        assert names == {"Karen"}          # Janice already a contact → skipped
        assert res["skipped_existing"] == 1
        assert res["proposed"] == 1
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_llm_pronoun_and_junk_rejected(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_BACKFILL_ENABLED", "1")
    db = await _open()
    try:
        _fake_memory_source(monkeypatch, [])
        _use_db(monkeypatch, db)
        # LLM hallucinates pronouns + an empty token alongside a real name.
        _mock_llm(
            monkeypatch,
            [("She", "sister"), ("They", None), ("", "friend"), ("Karen", "sister")],
        )
        stored = _capture_store(monkeypatch)

        await cb.backfill_contacts(USER)

        names = {s["pre_filled_slots"]["name"] for s in stored}
        assert names == {"Karen"}   # precision guard drops "She" / "They" / ""
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_flag_off_is_noop(monkeypatch):
    monkeypatch.delenv("ZOE_CONTACT_BACKFILL_ENABLED", raising=False)
    # Nothing patched: proves it never reads memory / portrait / LLM when off.
    res = await cb.backfill_contacts(USER)
    assert res == {"enabled": False, "proposed": 0, "skipped_existing": 0, "candidates": 0}


@pytest.mark.asyncio
async def test_malformed_llm_falls_back_to_regex(monkeypatch):
    monkeypatch.setenv("ZOE_CONTACT_BACKFILL_ENABLED", "1")
    db = await _open()
    try:
        # A regex-extractable fact memory is the safety net.
        _fake_memory_source(monkeypatch, [_Ref("Julie loves gardening.")])
        _use_db(monkeypatch, db)

        async def _boom(_text):
            raise RuntimeError("LLM exploded / returned garbage")

        monkeypatch.setattr(cb, "_llm_extract_people", _boom)
        stored = _capture_store(monkeypatch)

        res = await cb.backfill_contacts(USER)  # must NOT raise

        names = {s["pre_filled_slots"]["name"] for s in stored}
        assert names == {"Julie"}   # regex result survives the LLM failure
        assert res["proposed"] == 1
    finally:
        await db.close()


def test_parse_llm_people_tolerates_malformed():
    # Prose-wrapped array with mixed-quality items.
    raw = (
        'Sure! Here you go: [{"name": "Janice", "relationship": "mother"}, '
        '{"name": "Karen", "relationship": ""}, {"relationship": "no name"}, '
        '"not-an-object", {"name": "  "}]'
    )
    out = dict(cb._parse_llm_people(raw))
    assert out == {"Janice": "mother", "Karen": None}
    # Non-JSON / empty bodies → [] (caller falls back to regex).
    assert cb._parse_llm_people("no json here") == []
    assert cb._parse_llm_people("") == []
    assert cb._parse_llm_people("{}") == []
