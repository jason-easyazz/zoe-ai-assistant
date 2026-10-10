"""P7.b (live person bench, 2026-10-10): "Tell me about Percival." drew "Percival is your brother. Would you like me to add
Marisol as a contact?" in 5 of 20 clear asks (the question is a cost; the answer needed none).

The offer came from a pending ``person_create`` row named just "Marisol": the latent-intent detector read a bare
"Marisol" in "What's Marisol's birthday?", asked ``_already_a_contact`` - an EXACT, case-insensitive full-name match -
found "Marisol Okafor" and "Marisol Vance" did not equal "Marisol", and stored an offer to add her. The seam then voiced
that offer on the next unrelated turn. A bare name that is the first or last word of a contact the owner already has is
not a new person. Fixed at the emitter (``_already_a_contact``), at the choke point every emitter passes
(``store_suggestions``) and at the surface (``surface_pending_contacts_for_prompt`` closes a stale one).

Synthetic names only. Slim: in-memory SQLite behind an asyncpg-shaped shim, no network.
"""
import contextlib
import json
import re
import sys
import types

import aiosqlite
import pytest

import latent_intent_detector as lid
import pending_suggestions as ps
from people_utils import name_covered_by_contacts

pytestmark = pytest.mark.ci_safe

USER = "demo_bar_0a1b2c3d"
OKAFOR, VANCE = "Marisol Okafor", "Marisol Vance"


# -- the pure rule ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name,existing,covered", [
    ("Marisol", [OKAFOR, VANCE], True),                      # a bare first name two contacts share
    ("marisol", [OKAFOR], True),                             # case
    ("Okafor", [OKAFOR], True),                              # a bare surname
    ("Marisol Okafor", [OKAFOR], True),                      # the full name
    ("  marisol   okafor ", [OKAFOR], True),                 # whitespace + case
    ("Zoë", ["Zoe Park"], True),                             # accents
    ("Marisol Quinn", [OKAFOR, VANCE], False),               # a DIFFERENT full name is still new
    ("Priya", [OKAFOR, VANCE], False),                       # nobody by that name
    ("Mari", [OKAFOR], False),                               # a prefix of a word is not a match
    ("", [OKAFOR], False),
    ("Marisol", [], False),
])
def test_the_rule(name, existing, covered):
    assert name_covered_by_contacts(name, existing) is covered


# -- the shim ---------------------------------------------------------------------------------------------------------

class _Shim:
    def __init__(self, conn):
        self._c = conn

    @staticmethod
    def _q(sql):
        return re.sub(r"\$\d+", "?", sql)

    async def execute(self, sql, *params):
        await self._c.execute(self._q(sql), params)
        await self._c.commit()

    async def fetch(self, sql, *params):
        async with self._c.execute(self._q(sql), params) as cur:
            return list(await cur.fetchall())

    async def fetchrow(self, sql, *params):
        async with self._c.execute(self._q(sql), params) as cur:
            return await cur.fetchone()


async def _open(names):
    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)")
    await db.execute("CREATE TABLE people (id TEXT PRIMARY KEY, user_id TEXT, name TEXT, deleted INTEGER DEFAULT 0)")
    await db.execute(
        "CREATE TABLE pending_suggestions (id TEXT PRIMARY KEY, user_id TEXT NOT NULL, session_id TEXT, action_type TEXT,"
        " description TEXT, list_type TEXT, when_hint TEXT, amount_hint TEXT, offer_phrase TEXT, pre_filled_slots TEXT,"
        " created_at TEXT, turns_elapsed INTEGER, expire_after_turns INTEGER, resolved INTEGER)")
    for i, n in enumerate(names):
        await db.execute("INSERT INTO people (id, user_id, name) VALUES (?, ?, ?)", (str(i), USER, n))
    await db.commit()
    return db


@contextlib.asynccontextmanager
async def _ctx_of(db):
    yield _Shim(db)


def _wire(monkeypatch, db):
    ctx = lambda: _ctx_of(db)  # noqa: E731
    monkeypatch.setattr(ps, "get_db_ctx", ctx)
    fake = types.ModuleType("db_pool")
    fake.get_db_ctx = ctx
    monkeypatch.setitem(sys.modules, "db_pool", fake)


def _offer(name):
    return {"action_type": "person_create", "description": f"Add {name} to contacts", "offer_phrase": f"Add {name}?",
            "pre_filled_slots": {"name": name}}


# -- the emitter ------------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_bare_name_two_contacts_share_is_not_proposed_as_new(monkeypatch):
    db = await _open([OKAFOR, VANCE, "Percival Dunmore"])
    try:
        _wire(monkeypatch, db)
        assert await lid._already_a_contact("Marisol", USER) is True            # the live P7.b failure: this was False
        assert await lid._already_a_contact("Percival", USER) is True           # the clear-name turn
        assert await lid._already_a_contact("Priya", USER) is False             # somebody new is still offered
        assert await lid._already_a_contact("Marisol Quinn", USER) is False
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_detector_does_not_offer_to_add_a_contact_the_owner_already_has(monkeypatch):
    """The whole emitter, over the reply the live LLM gave for "What's Marisol's birthday?"."""
    db = await _open([OKAFOR, VANCE])
    try:
        _wire(monkeypatch, db)
        monkeypatch.setattr(lid, "_person_enabled", lambda: True)
        mod = types.ModuleType("intent_router")
        mod.detect_intent = lambda text: None
        monkeypatch.setitem(sys.modules, "intent_router", mod)

        async def llm(prompt):
            return json.dumps([{"action_type": "person_create", "offer_phrase": "",
                                "pre_filled_slots": {"name": "Marisol", "relationship": ""}}])

        monkeypatch.setattr(lid, "_complete", llm)
        assert await lid.detect("What's Marisol's birthday?", user_id=USER, session_id="s") == []
    finally:
        await db.close()


# -- the choke point and the surface ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_choke_point_drops_a_covered_name_whichever_emitter_sent_it(monkeypatch):
    db = await _open([OKAFOR, VANCE])
    try:
        _wire(monkeypatch, db)
        assert await ps.store_suggestions(USER, "s", [_offer("Marisol")]) == 0
        assert await ps.store_suggestions(USER, "s", [_offer("Priya")]) == 1
        assert [p["name"] for p in await ps.list_pending_contacts(USER)] == ["Priya"]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_stale_offer_for_a_covered_name_is_closed_not_voiced(monkeypatch):
    """An offer stored before the fix (or before the contact existed) must not be asked on the next unrelated turn."""
    db = await _open([OKAFOR, VANCE])
    try:
        _wire(monkeypatch, db)
        for n in ("Marisol", "Priya"):
            await db.execute(
                "INSERT INTO pending_suggestions VALUES (?, ?, 's', 'person_create', 'd', NULL, NULL, NULL, 'o', ?, 'now', 0, 6, 0)",
                (f"id-{n}", USER, json.dumps({"name": n})))
        await db.commit()
        out = await ps.surface_pending_contacts_for_prompt(USER, limit=3)
        assert [o["name"] for o in out] == ["Priya"]
        async with db.execute("SELECT resolved FROM pending_suggestions WHERE id='id-Marisol'") as c:
            assert (await c.fetchone())[0] == 1
    finally:
        await db.close()
