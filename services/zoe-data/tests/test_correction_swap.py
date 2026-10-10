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


def row(rid, text, user=USER, status="approved"):
    return SimpleNamespace(id=rid, text=text, metadata={"user_id": user, "status": status})


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
