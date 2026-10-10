"""correction_apply.maybe_swap: "that's wrong, my sister is Marisol not Marisa" changes the stored note and reads the fact back.

Evidence only: it acts when the owner's approved notes hold the old value as a whole word (and the relation word when one was said),
in at most three rows (one when no relation was said); anything else returns None and the brain answers as before.

Break-the-fix: drop the ownership filter in ``maybe_swap`` -> ``test_other_members_notes_are_never_touched`` goes red; let the swap
patterns match "tea not coffee" -> ``test_bare_values_are_not_a_correction`` goes red; skip the ``_rename_contact`` call ->
``test_the_contact_is_renamed_too`` goes red.
"""
import contextlib
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.ci_safe

import correction_apply as ca
from lane_parity_rig import FakeDB

USER = "demo_swap_user"
OTHER = "demo_swap_other"


class FakeSvc:
    def __init__(self, rows):
        self.rows = {r.id: r for r in rows}
        self.edits = []

    async def list_by_status(self, user_id=None, status="approved", limit=0):
        return list(self.rows.values())

    async def get(self, row_id):
        return self.rows.get(row_id)

    async def review(self, row_id, decision="edit", edits=None, **kw):
        assert decision == "edit"
        self.rows[row_id].text = edits
        self.edits.append((row_id, edits, kw.get("actor")))
        return self.rows[row_id]


def row(rid, text, user=USER, status="approved", turn="t1"):
    """``turn``: the saved turn the note came from (twin rows of one utterance share it)."""
    return SimpleNamespace(id=rid, text=text, metadata={"user_id": user, "status": status, "user_turn_id": turn})


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("ZOE_CORRECTION_APPLY", "1")
    db = FakeDB()
    db.people.append({"id": "p0", "name": "Marisa", "relationship": "sister", "deleted": 0})
    monkeypatch.setattr("lane_parity_rig.FakeDB.execute", _people_execute(db), raising=True)

    @contextlib.asynccontextmanager
    async def ctx(*_a, **_k):
        yield db

    monkeypatch.setattr("db_pool.get_db_ctx", ctx)

    async def no_mirror(*_a, **_k):
        return None

    monkeypatch.setattr("contacts_conversation.refresh_person_mirror", no_mirror)
    return db


def _people_execute(db):
    orig = FakeDB.execute

    async def execute(self, sql, params=()):
        low = " ".join(str(sql).split()).lower()
        params = tuple(params) if isinstance(params, (tuple, list)) else (params,)
        if low.startswith("select id, relationship from people"):
            from lane_parity_rig import _Cur
            return _Cur([{"id": p["id"], "relationship": p["relationship"]} for p in self.people
                         if p["name"].lower() == str(params[1]).lower() and not p.get("deleted")])
        if low.startswith("update people set name = ?, updated_at"):
            for p in self.people:
                if p["id"] == params[2]:
                    p["name"] = params[0]
            from lane_parity_rig import _Cur
            return _Cur()
        return await orig(self, sql, params)

    return execute


@pytest.mark.parametrize("text,expect", [
    ("That's wrong, my sister is Marisol not Marisa.", ("sister", "Marisol", "Marisa")),
    ("No, my sister is Marisol, not Marisa", ("sister", "Marisol", "Marisa")),
    ("my sister is not Marisa, she's Marisol", ("sister", "Marisol", "Marisa")),
    ("it's Thursday not Friday", ("", "Thursday", "Friday")),
    ("No, Marisol, not Marisa.", ("", "Marisol", "Marisa")),
    ("actually, not Marisa, Marisol", ("", "Marisol", "Marisa")),
    ("my brother is called Percy not Percival", ("brother", "Percy", "Percival")),
])
def test_swap_statement(text, expect):
    assert ca.swap_statement(text) == expect


@pytest.mark.parametrize("text", [
    "tea not coffee", "Marisol not Marisa", "that's wrong, it's the cat not the dog", "Biscuit is their dog",
    "I like tea, not coffee, and my sister likes neither of them at all", "my sister is Marisa not Marisa",
])
def test_bare_values_are_not_a_correction(text):
    assert ca.swap_statement(text) is None


async def test_swaps_the_one_note_and_reads_the_fact_back(_on):
    svc = FakeSvc([row("r1", "my sister is called Marisa"), row("r2", "User likes tea")])
    res = await ca.maybe_swap("That's wrong, my sister is Marisol not Marisa.", USER, "s", svc=svc)
    assert res is not None and res.kind == "swap"
    assert res.reply == "Got it, your sister is Marisol."
    assert svc.rows["r1"].text == "my sister is called Marisol" and svc.rows["r2"].text == "User likes tea"
    assert svc.edits == [("r1", "my sister is called Marisol", ca.SOURCE)]


async def test_the_contact_is_renamed_too(_on):
    svc = FakeSvc([row("r1", "my sister is called Marisa")])
    await ca.maybe_swap("That's wrong, my sister is Marisol not Marisa.", USER, "s", svc=svc)
    assert [p["name"] for p in _on.people] == ["Marisol"]


async def test_twin_rows_of_one_utterance_are_all_fixed(_on):
    svc = FakeSvc([row("r1", "my sister is called Marisa"), row("r2", "User's sister Marisa")])
    res = await ca.maybe_swap("No, my sister is Marisol, not Marisa", USER, "s", svc=svc)
    assert res is not None and len(res.changed) == 2


async def test_other_members_notes_are_never_touched(_on):
    svc = FakeSvc([row("r1", "my sister is called Marisa", user=OTHER)])
    assert await ca.maybe_swap("That's wrong, my sister is Marisol not Marisa.", USER, "s", svc=svc) is None
    assert svc.edits == []


async def test_other_members_notes_do_not_count_towards_the_guess_limit(_on):
    """Two of the owner's twin notes plus two of another member's: the owner's two are fixed, the other member's untouched.
    (Without the ownership filter the four hits exceed the limit and nothing is fixed.)"""
    svc = FakeSvc([row("r1", "my sister is called Marisa"), row("r2", "User's sister Marisa"),
                   row("o1", "my sister is called Marisa", user=OTHER), row("o2", "my sister Marisa", user=OTHER)])
    res = await ca.maybe_swap("That's wrong, my sister is Marisol not Marisa.", USER, "s", svc=svc)
    assert res is not None and sorted(e[0] for e in svc.edits) == ["r1", "r2"]
    assert svc.rows["o1"].text == "my sister is called Marisa" and svc.rows["o2"].text == "my sister Marisa"


async def test_no_stored_fact_to_change_means_the_brain_answers(_on):
    svc = FakeSvc([row("r1", "User likes tea")])
    assert await ca.maybe_swap("That's wrong, my sister is Marisol not Marisa.", USER, "s", svc=svc) is None


async def test_without_a_relation_more_than_one_note_is_a_guess(_on):
    svc = FakeSvc([row("r1", "Marisa is a coworker"), row("r2", "the dentist is Marisa")])
    assert await ca.maybe_swap("No, Marisol, not Marisa.", USER, "s", svc=svc) is None
    assert svc.edits == []


async def test_a_relation_keeps_a_coworker_of_the_same_name_out_of_it(_on):
    svc = FakeSvc([row("r1", "my sister is called Marisa"), row("r2", "Marisa is a coworker")])
    res = await ca.maybe_swap("That's wrong, my sister is Marisol not Marisa.", USER, "s", svc=svc)
    assert res is not None and svc.rows["r2"].text == "Marisa is a coworker"


@pytest.mark.parametrize("who", ["guest", "voice-daemon", ""])
async def test_guests_change_nothing(_on, who):
    svc = FakeSvc([row("r1", "my sister is called Marisa", user=who)])
    assert await ca.maybe_swap("That's wrong, my sister is Marisol not Marisa.", who, "s", svc=svc) is None


async def test_flag_off_is_inert(_on, monkeypatch):
    monkeypatch.setenv("ZOE_CORRECTION_APPLY", "0")
    svc = FakeSvc([row("r1", "my sister is called Marisa")])
    assert await ca.maybe_swap("That's wrong, my sister is Marisol not Marisa.", USER, "s", svc=svc) is None


# ── review round 1: the relation must be the SUBJECT of the note ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("note,expect", [
    ("my sister is called Marisa", True),
    ("My sister is Marisa", True),
    ("User's sister Marisa", True),
    ("the user's sister is named Marisa", True),
    ("sister: Marisa", True),
    ("Remember that my sister is called Marisa", True),
    ("My sister's friend is Marisa", False),               # possessive chain: the friend is Marisa, not the sister
    ("Dana's sister is Marisa", False),                    # somebody else's sister
    ("User's wife's sister is Marisa", False),             # a chain through another relative
    ("Marisa is a coworker", False),                       # no relation at all
    ("my sister works with Marisa", False),                # Marisa is not the sister
])
def test_the_relation_is_the_subject_of_the_note(note, expect):
    assert ca._is_note_about("sister", "Marisa", note) is expect


async def test_a_possessive_chain_note_is_never_rewritten(_on):
    svc = FakeSvc([row("r1", "My sister is Marisa"), row("r2", "My sister's friend is Marisa")])
    res = await ca.maybe_swap("My sister is Marisol not Marisa", USER, "s", svc=svc)
    assert res is not None
    assert svc.rows["r1"].text == "My sister is Marisol" and svc.rows["r2"].text == "My sister's friend is Marisa"


async def test_a_third_persons_sister_is_never_rewritten(_on):
    svc = FakeSvc([row("r1", "Dana's sister is Marisa")])
    assert await ca.maybe_swap("My sister is Marisol not Marisa", USER, "s", svc=svc) is None
    assert svc.edits == []


async def test_several_rows_must_be_twins_of_one_utterance(_on):
    def twin(rid, text, turn):
        return row(rid, text, turn=turn)

    same = FakeSvc([twin("r1", "my sister is called Marisa", "t1"), twin("r2", "User's sister Marisa", "t1")])
    assert await ca.maybe_swap("My sister is Marisol not Marisa", USER, "s", svc=same) is not None
    assert sorted(e[0] for e in same.edits) == ["r1", "r2"]
    apart = FakeSvc([twin("r1", "my sister is called Marisa", "t1"), twin("r2", "User's sister Marisa", "t2")])
    assert await ca.maybe_swap("My sister is Marisol not Marisa", USER, "s", svc=apart) is None   # two different saved turns: a guess
    assert apart.edits == []


# ── review round 1: a contact that did not follow the note is said, and logged ────────────────────────────────────────────────────
async def test_a_failed_contact_rename_is_not_claimed_and_is_logged(_on, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.WARNING)

    @contextlib.asynccontextmanager
    async def boom(*_a, **_k):
        raise RuntimeError("store down")
        yield  # pragma: no cover

    monkeypatch.setattr("db_pool.get_db_ctx", boom)
    svc = FakeSvc([row("r1", "my sister is called Marisa")])
    res = await ca.maybe_swap("That's wrong, my sister is Marisol not Marisa.", USER, "s", svc=svc)
    assert res is not None and res.reply.startswith("Got it, your sister is Marisol.")
    assert "couldn't update your contact" in res.reply
    warned = [r for r in caplog.records if r.levelno >= logging.WARNING and "contact rename FAILED" in r.getMessage()]
    assert warned and all("Marisa" not in r.getMessage() and "Marisol" not in r.getMessage() for r in warned)


async def test_a_failed_mirror_refresh_is_not_claimed_and_is_logged(_on, monkeypatch, caplog):
    import logging

    caplog.set_level(logging.WARNING)

    async def mirror_down(*_a, **_k):
        return False

    monkeypatch.setattr("contacts_conversation.refresh_person_mirror", mirror_down)
    svc = FakeSvc([row("r1", "my sister is called Marisa")])
    res = await ca.maybe_swap("That's wrong, my sister is Marisol not Marisa.", USER, "s", svc=svc)
    assert "couldn't update your contact" in res.reply
    assert any("mirror refresh FAILED" in r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


async def test_a_clean_rename_reads_back_without_a_caveat(_on):
    svc = FakeSvc([row("r1", "my sister is called Marisa")])
    res = await ca.maybe_swap("That's wrong, my sister is Marisol not Marisa.", USER, "s", svc=svc)
    assert res.reply == "Got it, your sister is Marisol."
