"""The forget-alias sweep (MemPalace take 1; bench HM-F5): "forget Marisol" also OFFERS "Marisal" - and never forgets it unasked.

Real code under test: the REAL ``memory_forget_entity`` handler over ``MemoryService`` + in-memory SQLite (people graph, derived
stores, a ``pending_suggestions`` table behind an asyncpg-style shim), the REAL confirm path (``execute_suggestion`` ->
``forget_confirmed`` -> the same handler) and the REAL nightly digest. Invented names only. Controls: the rule disabled -> nothing
is proposed; the confirm stubbed -> the alias survives; no confirmation -> nothing is erased.
"""
from __future__ import annotations

import contextlib
import json
import re
import sys
import types
from pathlib import Path

import aiosqlite
import pytest

import intent_router
import memory_digest as md
import memory_forget_alias as mfa
import memory_forgotten as mf
import memory_reject_ledger
import pending_suggestions as ps
from forgotten_support import (  # noqa: F401 - fixtures
    OTHER, USER, ledger_env, open_forgotten_db, svc, use_db,
)
from test_forget_durable_ledger import _TranscriptDb, _script_digest  # the REAL digest with only the model calls scripted

pytestmark = pytest.mark.ci_safe

NAME = "Marisol"                    # 7 letters: <= 2 edits
STT = ["Marisal", "Marysol", "Marizol", "Marisole", "Marissol", "Maricol", "Marisoul", "Marrisol"]   # the pilot's 8
SPLIT = ["Mari sol", "Mari-sol", "Maris ol"]                                                         # the pilot's 3


# ── the rule, pure ───────────────────────────────────────────────────────────

def _cands(name: str, text: str) -> "set[str]":
    return {key for _disp, key in mfa.find_in_text(name, text)}


@pytest.mark.parametrize("spelling", STT + SPLIT)
def test_every_pilot_spelling_is_proposed(spelling):
    key = " ".join(re.findall(r"\w+", spelling.lower()))
    assert key in _cands(NAME, f"so {spelling} is bringing the cake on Sunday")


def test_the_length_rule():
    assert [mfa.max_edits(n) for n in (3, 4, 5, 6, 7, 8, 12)] == [0, 0, 1, 1, 2, 2, 2]
    # <= 4 letters: nothing at all, split spellings and near neighbours included ("Dan" is not "Dana")
    assert mfa.find_in_text("Dana", "Dan Dane Dayna Dina Diana Danny Da na Dana W Dana-Whitfield") == []
    assert mfa.find_in_text("Leo", "Leon Leah Lee Lea") == []
    assert _cands("Priya", "Pria Priyah Prya and Priyaa") == {"pria", "priyah", "prya", "priyaa"}      # 5-6 letters: ONE edit
    assert _cands("Priya", "Pry Priyanka Prayer Pryor") == set()
    assert "marsl" in _cands(NAME, "Marsl") and _cands(NAME, "Marcel Marl") == set()                     # 7+: 2 edits in, 3 out


def test_whole_tokens_only_and_the_exact_name_is_not_an_alias():
    assert mfa.find_in_text(NAME, "Marisol's sister, Marisol W, Marisol-Reyes and MARISOL") == []
    assert _cands(NAME, "the Marisolino stew and a remarisoled floor Marisolution Marketing Maria Mariner") == set()
    words = ("market marine marina maritime marsh martial mask matter material mattress marathon margin miserable misery mailbox "
             "mansion maniac manual marble marriage marshal mushroom musical muscle moral morsel motion mirror missile mission")
    assert mfa.find_aliases(NAME, [("memory", words)]) == []     # ordinary words outside the radius are silent (Mario / Marisa are inside: proposals)


def test_a_split_spelling_must_join_close_and_start_with_the_same_letter():
    assert _cands(NAME, "Mar is sold") == set()                  # joins 2 edits away: split runs get one edit LESS
    assert "a risol" not in _cands(NAME, "a risol")              # wrong first letter (the lone token "risol" is a single-token proposal)
    assert _cands(NAME, "Mari s ol") == set()                    # a one-letter fragment is not a split spelling


def test_aliases_are_counted_by_where_they_were_found():
    found = mfa.find_aliases(NAME, [("memory", "Marisal rang"), ("memory", "then Marisal came"), ("contact", "Marisal Reyes"),
                                    ("memory", "Marysol is nice")])
    assert [(a.key, a.memories, a.contacts) for a in found] == [("marisal", 2, 1), ("marysol", 1, 0)]
    assert mfa.question_text(found[0]) == 'Did you also mean "Marisal"? I found it in 2 of your memories and in your contacts.'
    assert mfa.question_text(found[1]) == 'Did you also mean "Marysol"? I found it in one of your memories.'


@pytest.mark.parametrize("raw,want", [(None, "on"), ("", "on"), ("on", "on"), ("shadow", "shadow"), ("off", "off"), ("0", "off"),
                                      ("surprise", "on")])
def test_the_flag(monkeypatch, raw, want):
    if raw is None:
        monkeypatch.delenv(mfa.ENV, raising=False)
    else:
        monkeypatch.setenv(mfa.ENV, raw)
    assert mfa.mode() == want


# ── the live path: handler -> questions -> confirm -> same permanent path ────

class _Shim:
    """asyncpg-style ($N / fetch / fetchrow / execute / transaction / acquire) over aiosqlite: what ``pending_suggestions`` speaks."""

    def __init__(self, db):
        self._db = db

    async def fetch(self, sql, *args):
        async with self._db.execute(re.sub(r"\$(\d+)", "?", sql), args) as c:
            return await c.fetchall()

    async def fetchrow(self, sql, *args):
        return next(iter(await self.fetch(sql, *args)), None)

    async def execute(self, sql, *args):
        await self._db.execute(re.sub(r"\$(\d+)", "?", sql), args)
        await self._db.commit()

    @contextlib.asynccontextmanager
    async def transaction(self):
        yield

    @contextlib.asynccontextmanager
    async def acquire(self):
        yield self


_PENDING_DDL = """
CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT);
CREATE TABLE pending_suggestions (
    id TEXT PRIMARY KEY, user_id TEXT, session_id TEXT, action_type TEXT, description TEXT, list_type TEXT,
    when_hint TEXT, amount_hint TEXT, offer_phrase TEXT, pre_filled_slots TEXT, created_at TEXT,
    turns_elapsed INTEGER DEFAULT 0, expire_after_turns INTEGER DEFAULT 2, resolved INTEGER DEFAULT 0);
"""


@pytest.fixture
async def world(svc, monkeypatch):
    """The service + one in-memory DB behind both ``db_pool`` (people, derived stores) and ``pending_suggestions``."""
    db = await open_forgotten_db()
    await db.executescript(_PENDING_DDL)
    await db.commit()
    use_db(monkeypatch, db)

    @contextlib.asynccontextmanager
    async def ctx():
        yield _Shim(db)

    monkeypatch.setattr(ps, "get_db_ctx", ctx)
    monkeypatch.setattr(ps, "get_pool", lambda: _Shim(db))
    monkeypatch.delenv(mfa.ENV, raising=False)
    mfa.reset_state()
    erased: "list[str]" = []

    async def erase_rows(user_id, ids, *, actor="", reason=""):
        # the physical erase over the stand-in store (the byte scrub is test_memory_physical_erase's and the bench's F5/F6); here: WHICH rows go
        gone = [i for i in ids if i in svc._col.rows
                and (svc._col.rows[i][1].get("user_id") or svc._col.rows[i][1].get("wing")) == user_id]
        for i in gone:
            del svc._col.rows[i]
        erased.extend(gone)
        return {"rows_removed": len(gone), "physical": {"ok": True}}

    monkeypatch.setattr(svc, "erase_rows", erase_rows)
    w = types.SimpleNamespace(db=db, svc=svc, erased=erased)
    yield w
    mfa.reset_state()
    await db.close()


async def _say(svc, text, *, user=USER):
    return await svc.ingest(text, user_id=user, source="voice_fact", confidence=0.9)


async def _forget(name=NAME, user=USER):
    return await intent_router.execute_intent(intent_router.Intent("memory_forget_entity", {"name": name}), user)


def _naming(svc, word, user=USER):
    rx = re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)
    return [d for d, m in svc._col.rows.values() if (m.get("user_id") or m.get("wing")) == user and rx.search(d)]


async def _questions(db, user=USER, resolved=0):
    async with db.execute("SELECT id, offer_phrase, pre_filled_slots, description FROM pending_suggestions "
                          "WHERE user_id = ? AND action_type = 'forget_alias' AND resolved = ? ORDER BY created_at, id",
                          (user, resolved)) as c:
        return [dict(r) for r in await c.fetchall()]


async def _seed(w):
    """The forgotten friend; 'Marisal'; 'Marysol' only in an ARCHIVED row; a similar-but-different 'Marisa'; an innocent row; another
    member's 'Marisal' (row and contact); the owner's contact 'Marysol Reyes'."""
    s = w.svc
    await _say(s, "User's friend Marisol lives in Hobart.")
    await _say(s, "User's friend Marisal is bringing the cake on Sunday.")
    r = await _say(s, "User's neighbour Marysol waters the plants on Friday.")
    await s.review(r.id, decision="archive", actor=USER, note="seed an archived row")
    await _say(s, "User's neighbour Marisa waves every morning.")
    await _say(s, "User likes quiet mornings in the garden.")
    await _say(s, "User's friend Marisal lives in Perth.", user=OTHER)
    await w.db.execute("INSERT INTO people (id, user_id, name, deleted, visibility) VALUES ('p-marysol', ?, 'Marysol Reyes', 0, 'family')", (USER,))
    await w.db.execute("INSERT INTO people (id, user_id, name, deleted, visibility) VALUES ('p-other', ?, 'Marisal', 0, 'family')", (OTHER,))
    await w.db.commit()


@pytest.mark.asyncio
async def test_forget_proposes_the_near_spellings_and_erases_none_of_them_unasked(world):
    await _seed(world)
    reply = await _forget()
    assert 'Did you also mean "' in reply and "Say yes to forget it too, or no to leave it." in reply
    assert not _naming(world.svc, NAME)                                    # the exact forget did its job
    asked = {json.loads(q["pre_filled_slots"])["alias"].lower() for q in await _questions(world.db)}
    assert asked == {"marisal", "marysol", "marisa"}                       # rows (any status) AND the people graph
    # never automatic: the near spellings are still there, the ledger holds none, the contact is alive
    assert _naming(world.svc, "Marisal", USER) and _naming(world.svc, "Marisa")
    assert not await mf.matches(USER, "Marisal") and not await mf.matches(USER, "Marysol")
    assert await world.db.execute_fetchall("SELECT 1 FROM people WHERE id='p-marysol' AND deleted=0")
    assert not re.search(r"\bmarisol\b", json.dumps(await _questions(world.db)).lower())      # a question names the candidate, never the forgotten name


@pytest.mark.asyncio
async def test_another_users_spelling_is_never_proposed_or_touched(world):
    await _seed(world)
    await _forget()
    assert _naming(world.svc, "Marisal", OTHER)                            # their row is untouched
    assert await _questions(world.db, OTHER) == []                         # and nothing was asked about it
    # only the owner's own 'Marisal' (one row) is counted: the other user's row and contact are not
    q = next(q for q in await _questions(world.db) if json.loads(q["pre_filled_slots"])["alias"] == "Marisal")
    assert json.loads(q["pre_filled_slots"])["memories"] == 1 and json.loads(q["pre_filled_slots"])["contacts"] == 0
    # confirming the owner's alias leaves the other user's row, contact and ledger alone
    res = await ps.execute_suggestion(q["id"], USER)
    assert res["ok"], res
    assert _naming(world.svc, "Marisal", OTHER)
    assert await world.db.execute_fetchall("SELECT 1 FROM people WHERE id='p-other' AND deleted=0")
    assert not await mf.matches(OTHER, "Marisal")


@pytest.mark.asyncio
async def test_the_scan_reads_only_the_owners_rows_even_when_the_store_hands_back_others(world):
    """The ownership guard is the scan's own: a listing that returns another member's row must not have it read."""
    def ref(text, owner):
        return types.SimpleNamespace(id=text, text=text, metadata={"user_id": owner, "wing": owner})

    class _Leaky:
        async def list_by_status(self, *, user_id, status, limit, offset):
            return [ref("Marisal is mine", USER), ref("Marisal is theirs", OTHER)] if status == "approved" and offset == 0 else []

    texts = [t for src, t in await mfa.collect_texts(USER, _Leaky()) if src == "memory"]
    assert texts == ["Marisal is mine"]


@pytest.mark.asyncio
async def test_a_confirmed_alias_is_gone_from_the_store_the_ledger_the_contact_and_the_digest(world, monkeypatch, ledger_env):
    await _seed(world)
    await _forget()
    q = next(q for q in await _questions(world.db) if json.loads(q["pre_filled_slots"])["alias"] == "Marysol")
    res = await ps.execute_suggestion(q["id"], USER)
    assert res == {"ok": True, "action": "forget_alias", "result": {"forgotten": True}}
    # the same permanent path: rows (the archived one too) erased, ledger entry, contact soft-deleted
    assert not _naming(world.svc, "Marysol")
    assert await mf.matches(USER, "Marysol") and await mf.matches(USER, "the Marysol thing")
    assert await world.db.execute_fetchall("SELECT 1 FROM people WHERE id='p-marysol' AND deleted=1")
    ledger = list(ledger_env.rows.values())
    assert {r["scope"] for r in ledger} - {"near"} == {"entity", "alias"}                                                # the alias is labelled
    assert "marysol" not in json.dumps(ledger).lower() and "marisol" not in json.dumps(ledger).lower()      # hashes only
    kept, dropped = await mf.keep_unforgotten(USER, ["Marysol rang about the lift on Friday", "the dentist is on Elm Street"])
    assert dropped == 1 and kept == ["the dentist is on Elm Street"]
    # ...nor does the REAL nightly digest read its turn
    _script_digest(monkeypatch, ["User's neighbour Marysol is visiting at the weekend."])
    out = await md.run_memory_digest(USER, _TranscriptDb(["okay so Marysol rang about the weekend and we will see about the "
                                                          "shopping later on tonight and the plan after that"]))
    assert out.get("skipped_reason") == "insufficient_activity" and not _naming(world.svc, "Marysol")
    row = (await _questions(world.db, resolved=1))[0]
    assert row["offer_phrase"] == "" and row["pre_filled_slots"] == "{}" and row["description"] == ""


@pytest.mark.asyncio
async def test_an_unconfirmed_alias_stays_stored_but_the_digest_holds_its_turns_until_the_owner_answers(world, monkeypatch, tmp_path):
    monkeypatch.setenv("ZOE_MEMORY_REJECT_LEDGER", str(tmp_path / "reject.json"))      # its own counters: the next test counts declines
    memory_reject_ledger.reset_for_tests()
    await _seed(world)
    await _forget()
    assert _naming(world.svc, "Marysol")                                   # nothing stored is erased unasked
    assert await world.db.execute_fetchall("SELECT 1 FROM people WHERE id='p-marysol' AND deleted=0")
    assert not await mf.matches(USER, "Marysol")                           # reads stay exact: the row is still shown
    turn = "Marysol rang about the lift on Friday"
    kept, dropped = await mf.keep_unforgotten(USER, [turn])                # the loaders hold the near spelling out of re-mining...
    assert dropped == 1 and not kept
    assert len(await _questions(world.db)) == 3                            # ...and the question is already waiting (not asked twice)
    q = next(q for q in await _questions(world.db) if json.loads(q["pre_filled_slots"])["alias"] == "Marysol")
    assert await ps.mark_resolved(q["id"], USER)                           # "no, that's someone else"
    kept, dropped = await mf.keep_unforgotten(USER, [turn])
    assert dropped == 0 and kept == [turn]


@pytest.mark.asyncio
async def test_declining_leaves_everything_resolves_the_question_and_counts_the_reason(world, monkeypatch, tmp_path):
    memory_reject_ledger.reset_for_tests()
    await _seed(world)
    await _forget()
    q = next(q for q in await _questions(world.db) if json.loads(q["pre_filled_slots"])["alias"] == "Marisa")
    assert await ps.mark_resolved(q["id"], USER)
    assert _naming(world.svc, "Marisa") and not await mf.matches(USER, "Marisa")             # the neighbour stays
    assert all(json.loads(x["pre_filled_slots"])["alias"] != "Marisa" for x in await _questions(world.db))
    answered = [x for x in await _questions(world.db, resolved=1) if x["id"] == q["id"]][0]
    assert answered["offer_phrase"] == "" and answered["pre_filled_slots"] == "{}"           # the spelling is not kept
    counts = memory_reject_ledger.summary(24)
    assert counts["reasons"].get("owner_declined") == 1 and counts["sources"].get("forget_alias") == 1


@pytest.mark.asyncio
async def test_a_confirmed_alias_does_not_sweep_for_aliases_of_the_alias(world):
    await _say(world.svc, "User's friend Marisol lives in Hobart.")
    await _say(world.svc, "User's friend Marisal is bringing the cake.")
    await _say(world.svc, "User's friend Marisaal rang too.")              # 1 edit from Marisal, 2 from Marisol... and far from nothing
    await _forget()
    q = next(q for q in await _questions(world.db) if json.loads(q["pre_filled_slots"])["alias"] == "Marisal")
    before = {json.loads(x["pre_filled_slots"])["alias"] for x in await _questions(world.db)}
    assert (await ps.execute_suggestion(q["id"], USER))["ok"]
    after = {json.loads(x["pre_filled_slots"])["alias"] for x in await _questions(world.db)}
    assert after == before - {"Marisal"}                                    # nothing NEW was asked as a consequence


@pytest.mark.asyncio
async def test_the_spoken_path_a_bare_yes_answers_the_question_just_asked(world, monkeypatch):
    await _say(world.svc, "User's friend Marisol lives in Hobart.")
    await _say(world.svc, "User's friend Marisal is bringing the cake on Sunday.")
    reply = await _forget()
    assert 'Did you also mean "Marisal"?' in reply
    asked = {"msg": reply}

    async def previous(_uid):
        return asked["msg"]
    monkeypatch.setattr(intent_router, "_previous_assistant_message", previous)

    # an unrelated yes (the last assistant message was something else) is NOT bound
    asked["msg"] = "The dentist is on Thursday. Want a reminder?"
    other = await intent_router.detect_and_extract_intent("yes", USER)
    assert other is None or not other.slots.get("forget_alias")
    asked["msg"] = reply
    intent = await intent_router.detect_and_extract_intent("yes please", USER)
    assert intent.name == "pending_offer_accept" and intent.slots["forget_alias"] is True
    said = await intent_router.execute_intent(intent, USER)
    assert said.startswith("Done - I've forgotten that spelling too.")
    assert not _naming(world.svc, "Marisal") and await mf.matches(USER, "Marisal")


@pytest.mark.asyncio
async def test_the_spoken_no_leaves_it_and_asks_the_next_question(world, monkeypatch):
    await _say(world.svc, "User's friend Marisol lives in Hobart.")
    await _say(world.svc, "User's friend Marisal is bringing the cake on Sunday.")
    await _say(world.svc, "User's neighbour Marysol waters the plants.")
    reply = await _forget()

    async def previous(_uid):
        return reply
    monkeypatch.setattr(intent_router, "_previous_assistant_message", previous)
    intent = await intent_router.detect_and_extract_intent("no", USER)
    assert intent.name == "pending_offer_dismiss" and intent.slots["forget_alias"] is True
    said = await intent_router.execute_intent(intent, USER)
    assert said.startswith("Okay, I'll leave it.") and 'Did you also mean "' in said      # the next one is asked aloud
    assert _naming(world.svc, "Marisal") and _naming(world.svc, "Marysol")


@pytest.mark.asyncio
async def test_the_panel_card_offers_both_answers_on_the_existing_handlers(world):
    await _seed(world)
    await _forget()
    items = await ps.list_active(USER, "some-other-session")               # NOT the session it was stored under
    assert {i["action_type"] for i in items} == {"forget_alias"}
    cards = ps.ui_components_for_suggestions(items)
    assert all(c["type"] == "action_card" and c["title"].startswith("Did you also mean") for c in cards)
    assert [(a["label"], a["action"]) for a in cards[0]["actions"]] == [
        ("Yes, forget it too", "pending_suggestion_accept"), ("No, leave it", "pending_suggestion_dismiss")]
    assert await ps.list_active(OTHER, "some-other-session") == []


@pytest.mark.asyncio
async def test_a_confirm_that_does_not_land_leaves_the_question_open(world, monkeypatch):
    await _say(world.svc, "User's friend Marisol lives in Hobart.")
    await _say(world.svc, "User's friend Marisal is bringing the cake on Sunday.")
    await _forget()
    q = (await _questions(world.db))[0]

    async def lost(*_a, **_k):
        return False
    monkeypatch.setattr(mfa, "forget_confirmed", lost)
    res = await ps.execute_suggestion(q["id"], USER)
    assert res == {"ok": False, "error": "forget_alias_not_applied"}
    assert len(await _questions(world.db)) == 1 and json.loads((await _questions(world.db))[0]["pre_filled_slots"])["alias"]


@pytest.mark.asyncio
async def test_shadow_counts_and_asks_nothing_and_off_is_the_old_forget(world, monkeypatch, caplog):
    await _seed(world)
    monkeypatch.setenv(mfa.ENV, "shadow")
    with caplog.at_level("INFO", logger=mfa.logger.name):
        reply = await _forget()
    assert "Did you also mean" not in reply and await _questions(world.db) == []
    assert any("FORGET_ALIAS_SHADOW" in r.getMessage() and "marisal" not in r.getMessage().lower() for r in caplog.records)
    monkeypatch.setenv(mfa.ENV, "off")
    reply = await _forget()
    assert "Did you also mean" not in reply and await _questions(world.db) == []
    assert _naming(world.svc, "Marisal")


@pytest.mark.asyncio
async def test_control_with_the_rule_disabled_nothing_is_proposed(world, monkeypatch):
    """Negative control: the rule away (radius 0) and the proposal tests above go red."""
    await _seed(world)
    monkeypatch.setattr(mfa, "max_edits", lambda _n: 0)
    reply = await _forget()
    assert "Did you also mean" not in reply and await _questions(world.db) == []
    assert _naming(world.svc, "Marisal") and _naming(world.svc, "Marysol")        # and so they survive, as before


# ── the lab's port agrees with the service ───────────────────────────────────

def test_the_bench_lab_port_agrees_with_the_service_rule():
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts" / "perf"))
    from zmb.arms.hm_policy import alias_candidates

    texts = [f"so {s} rang about the weekend" for s in STT + SPLIT] + [
        "Marisa waves", "Mar is sold", "a risol", "Marisol's sister", "Marisol W rang", "Marketing and Maria", "Dan and Dayna"]
    for name in (NAME, "Priya", "Dana", "Leo", "Percival"):
        service = {d.casefold() for t in texts for d, _k in mfa.find_in_text(name, t)}
        lab = {c.casefold() for c in alias_candidates(name, texts)}
        assert service == lab, (name, service ^ lab)
