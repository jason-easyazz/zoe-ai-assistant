"""Skybridge (the voice/typed fast path) and the household list: a guest AT THE PANEL adds to, reads, ticks off and removes from
the ONE household list; anyone else gets today's sign-in answer (2026-10-15). All data invented."""
import pytest

pytestmark = pytest.mark.ci_safe

import panel_principal
import skybridge_service
from skybridge_service import resolve_skybridge_request
from test_skybridge_service import FakeDb


def _household_db() -> FakeDb:
    return FakeDb(
        lists=[
            {"id": "fam", "user_id": "jo", "name": "Shopping", "list_type": "shopping", "visibility": "family", "updated_at": 1},
            {"id": "priv", "user_id": "jo", "name": "Gift ideas", "list_type": "personal", "visibility": "personal", "updated_at": 2},
        ],
        items_by_list={
            "fam": [{"id": "i1", "list_id": "fam", "text": "milk", "completed": False},
                    {"id": "i2", "list_id": "fam", "text": "eggs", "completed": False}],
            "priv": [{"id": "p1", "list_id": "priv", "text": "surprise gift", "completed": False}],
        },
    )


async def _as_panel(message: str, db: FakeDb) -> dict:
    import asyncio

    async def _go():
        panel_principal.mark_panel_turn()
        return await resolve_skybridge_request(message, "guest", db=db)

    return await asyncio.create_task(_go())      # its own context: the mark cannot leak into the next test


async def test_a_panel_guest_adds_to_the_household_list_and_no_list_is_created():
    db = _household_db()
    result = await _as_panel("add bread to the shopping list", db)
    assert result.get("handled") and not result.get("auth_required")
    assert [i["text"] for i in db.items_by_list["fam"]] == ["milk", "eggs", "bread"]
    assert not any("INSERT INTO lists" in str(c[0]) for c in db.executed)
    assert all(row["user_id"] == "jo" for row in db.lists)


async def test_a_panel_guest_ticks_off_and_removes_on_the_household_list():
    db = _household_db()
    ticked = await _as_panel("check off milk on the shopping list", db)
    assert ticked.get("handled") and not ticked.get("auth_required")
    assert [i for i in db.items_by_list["fam"] if i["id"] == "i1"][0]["completed"] is True
    removed = await _as_panel("take eggs off the shopping list", db)
    assert removed.get("handled") and not removed.get("auth_required")
    assert [i["text"] for i in db.items_by_list["fam"]] == ["milk"]


async def test_a_panel_guest_reads_the_household_list_and_the_card_never_carries_a_private_one():
    db = _household_db()
    result = await _as_panel("show my shopping list", db)
    assert result.get("handled") and not result.get("auth_required")
    assert "milk" in result["spoken_summary"]
    assert "surprise gift" not in str(result)


async def test_a_guest_who_is_not_the_panel_still_gets_the_sign_in_answer_and_no_write():
    db = _household_db()
    for message in ("add bread to the shopping list", "take eggs off the shopping list", "check off milk on the shopping list"):
        result = await resolve_skybridge_request(message, "guest", db=db)
        assert result.get("auth_required"), message
    assert [i["text"] for i in db.items_by_list["fam"]] == ["milk", "eggs"]
    assert db.executed == []


async def test_a_panel_guest_still_cannot_create_a_list_or_edit_an_item_text():
    db = _household_db()
    for message in ("create a work list called Projects", "edit milk on the shopping list"):
        result = await _as_panel(message, db)
        assert result.get("auth_required"), message
    assert not any("INSERT INTO lists" in str(c[0]) for c in db.executed)


async def test_a_panel_guest_add_with_no_household_list_starts_one_household_owned_family_list():
    db = FakeDb(lists=[], items_by_list={})
    result = await _as_panel("add rice to the shopping list", db)
    assert result.get("handled")
    created = [row for row in db.lists]
    assert len(created) == 1
    assert created[0]["visibility"] == "family" and created[0]["user_id"] == panel_principal.HOUSEHOLD_OWNER_ID


async def test_member_default_list_seeding_does_not_add_a_second_household_shopping_list():
    """The other duplicate source: every member who touched Lists got their own family 'Shopping' seeded."""
    db = _household_db()
    await skybridge_service._ensure_default_lists("casey", db)
    seeded = [str(c[3]).lower() for c in db.executed if "INSERT INTO lists" in str(c[0])]
    assert "shopping" not in seeded                      # the household already has one
    assert {"work", "personal"} <= set(seeded)           # private defaults are still per member


async def test_default_list_seeding_still_creates_the_household_list_when_there_is_none():
    db = FakeDb(lists=[], items_by_list={})
    await skybridge_service._ensure_default_lists("casey", db)
    seeded = [str(c[3]).lower() for c in db.executed if "INSERT INTO lists" in str(c[0])]
    assert "shopping" in seeded


async def test_a_members_type_only_add_lands_on_the_household_list_whatever_it_is_called():
    """Type first, name only a tie-break: casey's own empty personal 'Shopping' must not capture the add while the house
    keeps its list as 'Groceries'."""
    db = FakeDb(
        lists=[
            {"id": "mine", "user_id": "casey", "name": "Shopping", "list_type": "shopping", "visibility": "personal", "updated_at": 9},
            {"id": "groc", "user_id": "jo", "name": "Groceries", "list_type": "shopping", "visibility": "family", "updated_at": 1},
        ],
        items_by_list={"groc": [{"id": "g1", "list_id": "groc", "text": "bread", "completed": False}]},
    )
    result = await resolve_skybridge_request("add jam to the shopping list", "casey", db=db)
    assert result.get("handled")
    assert [i["text"] for i in db.items_by_list["groc"]] == ["bread", "jam"]
    assert not db.items_by_list.get("mine")
