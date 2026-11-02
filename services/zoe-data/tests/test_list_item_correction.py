"""Correcting a list item (2026-11-02 usage audit): "change noodles to rice noodles [on the shopping list]", "that should be X,
not Y", and the "just added" fix ("spell it X", "I meant X", "no, X") within a couple of minutes of an add. Household rule: anyone AT
THE PANEL edits ITEMS on the family list; a personal list stays its owner's. All phrases and names are synthetic."""
from __future__ import annotations

import asyncio

import pytest

import intent_router
import list_service
import panel_principal
import skybridge_service
import write_receipts
from skybridge_service import classify_skybridge_intent, resolve_skybridge_request
from test_skybridge_service import FakeDb

pytestmark = pytest.mark.ci_safe


class EditDb(FakeDb):
    """FakeDb + the one statement a correction adds: ``UPDATE list_items SET text``."""

    async def execute(self, *args):
        sql = str(args[0])
        if "UPDATE list_items SET text" in sql:
            new, item_id, list_id = args[1], args[2], args[3]
            hit = 0
            for item in self.items_by_list.get(list_id, []):
                if item["id"] == item_id and not item.get("deleted"):
                    item["text"] = new
                    hit += 1
            self.executed.append(args)
            return f"UPDATE {hit}"
        return await super().execute(*args)


def _db() -> EditDb:
    return EditDb(
        lists=[
            {"id": "fam", "user_id": "jo", "name": "Shopping", "list_type": "shopping", "visibility": "family", "updated_at": 1},
            {"id": "priv", "user_id": "jo", "name": "Gift ideas", "list_type": "personal", "visibility": "personal", "updated_at": 2},
        ],
        items_by_list={
            "fam": [{"id": "i1", "list_id": "fam", "text": "noodles", "completed": False},
                    {"id": "i2", "list_id": "fam", "text": "milk", "completed": False}],
            "priv": [{"id": "p1", "list_id": "priv", "text": "surprise gift", "completed": False}],
        },
    )


@pytest.fixture(autouse=True)
def _clean():
    list_service.clear_recent_adds()
    write_receipts.reset()
    yield
    list_service.clear_recent_adds()
    write_receipts.reset()


async def _as_panel(message: str, db) -> dict:
    async def _go():
        panel_principal.mark_panel_turn()
        return await resolve_skybridge_request(message, "guest", db=db)

    return await asyncio.create_task(_go())      # its own context: the panel mark cannot leak into the next test


def _texts(db, list_id="fam"):
    return [i["text"] for i in db.items_by_list[list_id]]


# ── rename ──────────────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_change_x_to_y_renames_the_item_and_says_so():
    db = _db()
    result = await resolve_skybridge_request("change noodles to rice noodles", "jo", db=db)
    assert result["spoken_summary"] == "Changed noodles to rice noodles on the shopping list."
    assert _texts(db) == ["rice noodles", "milk"]
    assert result["intent"]["status"] == "renamed"
    assert write_receipts.has("jo", 0.0, "list")      # the receipt the write-claim guard reads


@pytest.mark.parametrize("phrase", [
    "change milk to oat milk on the shopping list",
    "rename milk to oat milk on my shopping list.",
    "correct milk to oat milk",
    "Please change Milk to oat milk in the shopping list",
    "that should be oat milk, not milk",
    "it should be oat milk not milk",
])
async def test_every_rename_phrasing(phrase):
    db = _db()
    intent = classify_skybridge_intent(phrase)
    assert intent is not None and intent.action == "rename_item", phrase
    result = await resolve_skybridge_request(phrase, "jo", db=db)
    assert _texts(db) == ["noodles", "oat milk"], phrase
    assert result["spoken_summary"] == "Changed milk to oat milk on the shopping list."


async def test_the_old_item_is_matched_by_item_key_case_and_plural_insensitive():
    db = _db()
    result = await resolve_skybridge_request("change Noodle to rice noodles", "jo", db=db)
    assert _texts(db) == ["rice noodles", "milk"]
    assert result["intent"]["status"] == "renamed"


async def test_dedupe_into_an_existing_item_retires_the_old_row_instead_of_duplicating():
    db = _db()
    db.items_by_list["fam"].append({"id": "i3", "list_id": "fam", "text": "oat milk", "completed": False})
    result = await resolve_skybridge_request("change milk to oat milk", "jo", db=db)
    assert _texts(db) == ["noodles", "oat milk"]                    # exactly one oat milk, milk gone
    assert result["intent"]["status"] == "merged"
    assert "already" in result["spoken_summary"] and "oat milk" in result["spoken_summary"]


async def test_old_item_not_found_says_so_and_changes_nothing():
    db = _db()
    result = await resolve_skybridge_request("change bread to rye bread on the shopping list", "jo", db=db)
    assert "couldn't find bread" in result["spoken_summary"]
    assert result["intent"]["status"] == "not_found"
    assert _texts(db) == ["noodles", "milk"]
    assert not any("UPDATE list_items" in str(c[0]) for c in db.executed)


async def test_a_rename_never_creates_a_list():
    db = EditDb(lists=[], items_by_list={})
    result = await resolve_skybridge_request("change noodles to rice noodles", "jo", db=db)
    assert "nothing changed" in result["spoken_summary"]
    assert not any("INSERT INTO lists" in str(c[0]) for c in db.executed)
    assert db.lists == []


# ── questions and other domains are never writes ────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "can I change noodles to rice noodles?",
    "could I rename milk to oat milk",
    "did you change milk to oat milk",
    "what did I change",
    "I was going to change milk to oat milk",
    "should I rename noodles to rice noodles",
    "do not change milk to oat milk",
])
def test_questions_and_statements_about_changing_are_not_rename_writes(phrase):
    intent = classify_skybridge_intent(phrase)
    assert intent is None or intent.action not in {"rename_item", "correct_last_item"}, phrase


@pytest.mark.parametrize("phrase", [
    "change the timer to 10 minutes",
    "change the light to blue",
    "change the volume to 30",
    "change my name to Sam",
    "change the music to jazz",
    "change pasta to 20 minutes",          # no noun gives it away, the VALUE does
    "change milk to 3 percent",
])
def test_changing_something_that_is_not_a_list_item_is_not_a_rename(phrase):
    intent = classify_skybridge_intent(phrase)
    assert intent is None or intent.action != "rename_item", phrase


def test_the_negative_control_a_real_rename_is_claimed():
    """If the guards above were simply deleting the matcher, this would fail."""
    assert classify_skybridge_intent("change noodles to rice noodles").action == "rename_item"


# ── permissions ─────────────────────────────────────────────────────────────────────────────────────────────────────────

async def test_a_member_cannot_rename_on_someone_elses_personal_list():
    db = _db()
    result = await resolve_skybridge_request("change surprise gift to flowers on my personal list", "sam", db=db)
    assert db.items_by_list["priv"][0]["text"] == "surprise gift"
    assert "nothing changed" in result["spoken_summary"]


async def test_the_service_refuses_a_personal_list_for_a_non_owner_and_for_the_panel():
    db = _db()
    for who in ("sam", panel_principal.HOUSEHOLD_VIEWER, "guest"):
        out = await list_service.rename_item_on_list(db, user_id=who, list_id="priv", old_text="surprise gift", new_text="flowers")
        assert out["status"] == "forbidden", who
    assert db.items_by_list["priv"][0]["text"] == "surprise gift"
    ok = await list_service.rename_item_on_list(db, user_id="jo", list_id="priv", old_text="surprise gift", new_text="flowers")
    assert ok["status"] == "renamed" and db.items_by_list["priv"][0]["text"] == "flowers"


async def test_a_panel_guest_may_edit_items_on_the_household_list():
    db = _db()
    result = await _as_panel("change noodles to rice noodles on the shopping list", db)
    assert result["handled"] and not result.get("auth_required")
    assert _texts(db) == ["rice noodles", "milk"]
    assert db.lists[0]["user_id"] == "jo" and len(db.lists) == 2


async def test_a_panel_guest_cannot_reach_a_personal_list_by_name():
    db = _db()
    result = await _as_panel("change surprise gift to flowers on my gift ideas list", db)
    assert db.items_by_list["priv"][0]["text"] == "surprise gift"
    assert "nothing changed" in result["spoken_summary"] or not result.get("handled")


async def test_a_guest_who_is_not_the_panel_still_gets_the_sign_in_answer():
    db = _db()
    result = await resolve_skybridge_request("change noodles to rice noodles on the shopping list", "guest", db=db)
    assert result.get("auth_required")
    assert _texts(db) == ["noodles", "milk"] and db.executed == []


# ── "just added" correction ─────────────────────────────────────────────────────────────────────────────────────────────

async def _add(db, who="jo", text="add noodels to the shopping list"):
    result = await resolve_skybridge_request(text, who, db=db)
    assert result["intent"]["action"] == "add_item"
    return result


@pytest.mark.parametrize("phrase", ["spell it rice noodles", "I meant rice noodles", "that should be rice noodles",
                                    "no, rice noodles", "no it's rice noodles", "nope, I meant rice noodles"])
async def test_correct_the_item_just_added(phrase):
    db = _db()
    await _add(db)
    assert "noodels" in _texts(db)
    result = await resolve_skybridge_request(phrase, "jo", db=db)
    assert _texts(db) == ["noodles", "milk", "rice noodles"], phrase
    assert result["spoken_summary"] == "Changed noodels to rice noodles on the shopping list."


async def test_without_a_recent_add_these_words_are_not_a_list_command():
    for phrase in ("spell it rice noodles", "I meant rice noodles", "no, rice noodles"):
        assert classify_skybridge_intent(phrase) is None or classify_skybridge_intent(phrase).action != "correct_last_item", phrase
    db = _db()
    result = await resolve_skybridge_request("I meant rice noodles", "jo", db=db)
    assert not result["handled"] and _texts(db) == ["noodles", "milk"]


async def test_the_window_expires(monkeypatch):
    db = _db()
    await _add(db)
    key = list_service._recent_key("jo")
    lid, iid, at = list_service._RECENT_ADDS[key]
    list_service._RECENT_ADDS[key] = (lid, iid, at - list_service.RECENT_ADD_WINDOW_S - 5)
    result = await resolve_skybridge_request("spell it rice noodles", "jo", db=db)
    assert not result["handled"]
    assert "noodels" in _texts(db) and "rice noodles" not in _texts(db)


async def test_a_bare_no_that_does_not_resemble_the_item_is_left_alone():
    db = _db()
    await _add(db)
    for phrase in ("no, bananas", "no thanks", "no"):
        result = await resolve_skybridge_request(phrase, "jo", db=db)
        assert not result["handled"], phrase
    other = classify_skybridge_intent("no, turn on the lights")      # another command keeps its own meaning
    assert other is None or other.action != "correct_last_item"
    assert "noodels" in _texts(db) and "bananas" not in _texts(db)


@pytest.mark.parametrize("phrase", ["I meant tomorrow", "that should be better", "I meant to say hello", "make it say goodbye"])
async def test_ordinary_speech_after_an_add_is_not_hijacked_into_a_rename(phrase):
    db = _db()
    await _add(db)
    result = await resolve_skybridge_request(phrase, "jo", db=db)
    assert not result["handled"], phrase
    assert "noodels" in _texts(db) and len(_texts(db)) == 3


async def test_spell_it_is_the_one_unguarded_form():
    db = _db()
    await _add(db, text="add oat drink to the shopping list")
    result = await resolve_skybridge_request("spell it kefir", "jo", db=db)      # nothing like "oat drink", but unmistakable
    assert result["intent"]["status"] == "renamed" and "kefir" in _texts(db)


async def test_change_x_to_y_has_no_time_window():
    """Only the 'just added' forms are windowed: a plain rename works on any item whenever it was added."""
    db = _db()
    list_service.clear_recent_adds()
    result = await resolve_skybridge_request("change milk to oat milk", "jo", db=db)
    assert result["intent"]["status"] == "renamed"


async def test_a_second_correction_after_a_merge_follows_the_surviving_row():
    db = _db()
    db.items_by_list["fam"].append({"id": "i3", "list_id": "fam", "text": "oat milk", "completed": False})
    await resolve_skybridge_request("change milk to oat milk", "jo", db=db)
    assert list_service.recent_add("jo") == ("fam", "i3")


async def test_visibility_is_decided_inside_the_transaction_with_the_row_locked():
    seen = []

    class TxDb(EditDb):
        def transaction(self):
            seen.append("txn")

            class _T:
                async def __aenter__(s):
                    return None

                async def __aexit__(s, *a):
                    return False

            return _T()

        async def fetch(self, *args):
            if "FROM lists" in str(args[0]):
                seen.append(("lists", "FOR SHARE" in str(args[0]), seen.count("txn") > 0))
            return await super().fetch(*args)

    db = TxDb(lists=_db().lists, items_by_list=_db().items_by_list)
    await list_service.rename_item_on_list(db, user_id="jo", list_id="fam", old_text="milk", new_text="oat milk")
    assert ("lists", True, True) in seen          # the list row is read FOR SHARE, after the transaction began


async def test_another_members_add_is_not_mine_to_correct():
    db = _db()
    await _add(db, who="jo")
    result = await resolve_skybridge_request("spell it rice noodles", "sam", db=db)
    assert not result["handled"] and "noodels" in _texts(db)


async def test_an_item_ticked_off_since_the_add_is_not_corrected():
    db = _db()
    await _add(db)
    for item in db.items_by_list["fam"]:
        if item["text"] == "noodels":
            db.items_by_list["fam"].remove(item)
            break
    result = await resolve_skybridge_request("spell it rice noodles", "jo", db=db)
    assert not result["handled"]


async def test_panel_correction_is_the_panels_own_add():
    db = _db()
    await _as_panel("add noodels to the shopping list", db)
    result = await _as_panel("spell it rice noodles", db)
    assert result["handled"] and "rice noodles" in _texts(db) and "noodels" not in _texts(db)


async def test_a_non_panel_guest_is_never_answered_for_a_correction():
    db = _db()
    await _as_panel("add noodels to the shopping list", db)
    result = await resolve_skybridge_request("spell it rice noodles", "guest", db=db)
    assert not result["handled"] and not result.get("auth_required")
    assert "noodels" in _texts(db)


# ── the keyword (intent_router) path ───────────────────────────────────────────────────────────────────────────────────────

def test_intent_router_detects_a_rename_and_not_a_question():
    intent = intent_router.detect_intent("change milk to oat milk on the shopping list")
    assert intent is not None and intent.name == "list_rename"
    assert intent.slots["item"] == "milk" and intent.slots["new_item"] == "oat milk" and intent.slots["list_type"] == "shopping"
    for phrase in ("can I change milk to oat milk on the shopping list", "change the timer to 10 minutes"):
        got = intent_router.detect_intent(phrase)
        assert got is None or got.name != "list_rename", phrase


async def test_the_keyword_executor_renames_through_the_shared_writer(monkeypatch):
    import contextlib

    import database

    db = _db()

    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    async def pick(_db, *, user_id, list_type, name):
        return "fam"

    monkeypatch.setattr(database, "get_db_ctx", ctx)
    monkeypatch.setattr(list_service, "find_list_id", pick)
    monkeypatch.setattr(intent_router, "_notify_lists_ui", lambda *a, **k: asyncio.sleep(0))
    intent = intent_router.Intent("list_rename", {"item": "milk", "new_item": "oat milk", "list_type": "shopping", "list_name": ""})
    reply = await intent_router.execute_intent(intent, "jo")
    assert reply == "Changed milk to oat milk on your shopping list."
    assert _texts(db) == ["noodles", "oat milk"]
    missing = await intent_router.execute_intent(
        intent_router.Intent("list_rename", {"item": "bread", "new_item": "rye", "list_type": "shopping", "list_name": ""}), "jo")
    assert "couldn't find bread" in missing and _texts(db) == ["noodles", "oat milk"]


def test_resemblance_helper():
    assert list_service.looks_like_correction_of("noodels", "rice noodles")
    assert list_service.looks_like_correction_of("mlk", "milk")
    assert not list_service.looks_like_correction_of("milk", "bananas")


# ── review round (Codex / Greptile on #2246) ────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("phrase", [
    "are you sure that should be oat milk, not milk",
    "she said that should be oat milk, not milk",
    "the recipe says it should be oat milk, not milk",
])
def test_a_should_be_clause_inside_a_question_or_statement_is_not_a_command(phrase):
    intent = classify_skybridge_intent(phrase)
    assert intent is None or intent.action != "rename_item", phrase


@pytest.mark.parametrize("phrase", ["that should be oat milk, not milk", "no, it should be oat milk not milk",
                                    "please, that should be oat milk, not milk"])
def test_the_anchored_should_be_form_still_works(phrase):
    intent = classify_skybridge_intent(phrase)
    assert intent is not None and intent.action == "rename_item" and intent.target_text == "oat milk", phrase


@pytest.mark.parametrize("action", ["rename_item", "correct_last_item"])
def test_corrections_are_writes_for_the_unsure_confidence_floor(action):
    from skybridge_service import SkybridgeIntent

    intent = SkybridgeIntent(domain="lists", action=action, item_text="milk", target_text="oat milk")
    assert skybridge_service._unsure_floor(intent, "change milk to oat milk") == 0.45
    assert skybridge_service._unsure_floor(SkybridgeIntent(domain="lists", action="show"), "show my lists") is None   # control


async def test_a_rename_whose_update_hits_no_row_is_not_claimed():
    db = _db()
    original = db.execute

    async def lost_race(*args):
        if "UPDATE list_items SET text" in str(args[0]):
            return "UPDATE 0"                       # the item was deleted between the read and the write
        return await original(*args)

    db.execute = lost_race
    result = await resolve_skybridge_request("change noodles to rice noodles", "jo", db=db)
    assert result["intent"]["status"] == "not_found"
    assert "Changed" not in result["spoken_summary"]
    assert not write_receipts.has("jo", 0.0, "list")


async def test_a_failed_correction_does_not_create_the_default_lists():
    db = _db()
    result = await resolve_skybridge_request("change bread to rye bread on the shopping list", "jo", db=db)
    assert result["intent"]["status"] == "not_found"
    assert not any("INSERT INTO lists" in str(c[0]) for c in db.executed)


async def test_an_item_ticked_off_since_the_add_is_not_corrected_completed_row():
    db = _db()
    await _add(db)
    for item in db.items_by_list["fam"]:
        if item["text"] == "noodels":
            item["completed"] = True
    result = await resolve_skybridge_request("spell it rice noodles", "jo", db=db)
    assert not result["handled"]
    assert "noodels" in _texts(db)


class _NamedListDb:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, sql, params=()):
        name = params[0].lower()
        owner = params[2]
        hits = [r for r in self.rows if r["name"].lower() in (name, f"{name} list")
                and (r["visibility"] == "family" or (owner and r["user_id"] == owner))]
        db = self

        class _Cur:
            async def fetchone(self_inner):
                return hits[0] if hits else None

        return _Cur()


async def test_find_named_list_id_is_that_list_or_none():
    db = _NamedListDb([
        {"id": "fam", "name": "Shopping", "user_id": "jo", "visibility": "family"},
        {"id": "camp", "name": "Camping", "user_id": "jo", "visibility": "family"},
        {"id": "mine", "name": "Gift ideas", "user_id": "jo", "visibility": "personal"},
    ])
    assert await list_service.find_named_list_id(db, user_id="jo", name="camping") == "camp"
    assert await list_service.find_named_list_id(db, user_id="jo", name="gift ideas") == "mine"
    assert await list_service.find_named_list_id(db, user_id="jo", name="chores") is None
    assert await list_service.find_named_list_id(db, user_id="guest", name="gift ideas") is None    # a guest owns nothing


async def test_the_keyword_executor_resolves_a_named_list_exactly_and_refuses_when_absent(monkeypatch):
    import contextlib

    import database

    db = _db()

    @contextlib.asynccontextmanager
    async def ctx():
        yield db

    async def household(_db, *, user_id, list_type, name):
        raise AssertionError("a NAMED list must never fall back to the household list of the default type")

    async def named(_db, *, user_id, name):
        return "fam" if name == "shopping" else None

    monkeypatch.setattr(database, "get_db_ctx", ctx)
    monkeypatch.setattr(list_service, "find_list_id", household)
    monkeypatch.setattr(list_service, "find_named_list_id", named)
    monkeypatch.setattr(intent_router, "_notify_lists_ui", lambda *a, **k: asyncio.sleep(0))
    slots = {"item": "milk", "new_item": "oat milk", "list_type": "shopping", "list_name": "camping"}
    reply = await intent_router.execute_intent(intent_router.Intent("list_rename", dict(slots)), "jo")
    assert reply == "I couldn't find your camping list, so nothing changed."
    assert _texts(db) == ["noodles", "milk"]
    reply = await intent_router.execute_intent(intent_router.Intent("list_rename", {**slots, "list_name": "shopping"}), "jo")
    assert reply.startswith("Changed milk to oat milk") and _texts(db) == ["noodles", "oat milk"]
