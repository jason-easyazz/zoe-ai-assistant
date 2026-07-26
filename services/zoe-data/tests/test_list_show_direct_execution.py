from contextlib import asynccontextmanager

import pytest

from intent_router import Intent, _execute_list_show_direct, detect_intent, execute_intent

pytestmark = pytest.mark.ci_safe


class _Cursor:
    def __init__(self, rows=None):
        self._rows = rows or []

    async def fetchall(self):
        return self._rows


class _FakeDB:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    async def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return _Cursor(self.rows)


def _install_fake_db(monkeypatch, db):
    @asynccontextmanager
    async def fake_get_db_ctx():
        yield db

    monkeypatch.setattr("database.get_db_ctx", fake_get_db_ctx)


def test_detect_intent_handles_spoken_what_is_list_show():
    intent = detect_intent("what is on my shopping list")

    assert intent.name == "list_show"
    assert intent.slots == {"list_type": "shopping"}


@pytest.mark.asyncio
async def test_list_show_direct_returns_empty_text_when_no_lists(monkeypatch):
    db = _FakeDB([])
    _install_fake_db(monkeypatch, db)

    result = await _execute_list_show_direct(
        Intent("list_show", {"list_type": "shopping"}),
        "pi-intent-lab",
    )

    assert result == "Your shopping list is empty."
    assert len(db.calls) == 1
    assert db.calls[0][1] == ("pi-intent-lab", "shopping")


@pytest.mark.asyncio
async def test_execute_list_show_returns_empty_text_for_pi_lab_when_no_lists(monkeypatch):
    db = _FakeDB([])
    _install_fake_db(monkeypatch, db)

    async def fail_mcporter(_cmd):
        raise AssertionError("list_show should use the read-only direct path")

    monkeypatch.setattr("intent_router._run_mcporter", fail_mcporter)

    result = await execute_intent(
        Intent("list_show", {"list_type": "shopping"}),
        "pi-intent-lab",
    )

    assert result == "Your shopping list is empty."
    assert len(db.calls) == 1
    assert db.calls[0][1] == ("pi-intent-lab", "shopping")


@pytest.mark.asyncio
async def test_execute_list_show_uses_read_only_direct_path_before_mcporter(monkeypatch):
    db = _FakeDB(
        [
            {
                "id": "shopping-1",
                "name": "Shopping",
                "item_id": "item-1",
                "text": "milk",
                "completed": False,
                "quantity": None,
                "category": None,
            }
        ]
    )
    _install_fake_db(monkeypatch, db)

    async def fail_mcporter(_cmd):
        raise AssertionError("list_show should use the read-only direct path")

    monkeypatch.setattr("intent_router._run_mcporter", fail_mcporter)

    result = await execute_intent(
        Intent("list_show", {"list_type": "shopping"}),
        "pi-intent-lab",
    )

    assert result == "Your shopping list:\n  - milk"
    assert len(db.calls) == 1


@pytest.mark.asyncio
async def test_list_show_direct_ignores_completed_items(monkeypatch):
    db = _FakeDB(
        [
            {
                "id": "shopping-1",
                "name": "Shopping",
                "item_id": "item-1",
                "text": "milk",
                "completed": True,
                "quantity": None,
                "category": None,
            }
        ]
    )
    _install_fake_db(monkeypatch, db)

    result = await _execute_list_show_direct(
        Intent("list_show", {"list_type": "shopping"}),
        "pi-intent-lab",
    )

    assert result == "Your shopping list is empty."


@pytest.mark.asyncio
async def test_list_show_direct_escapes_list_name_like_wildcards(monkeypatch):
    db = _FakeDB([])
    _install_fake_db(monkeypatch, db)

    result = await _execute_list_show_direct(
        Intent("list_show", {"list_type": "shopping", "list_name": "50%_off\\sale"}),
        "pi-intent-lab",
    )

    assert result == "Your shopping list is empty."
    sql, params = db.calls[0]
    assert "ESCAPE" in sql
    assert params == ("pi-intent-lab", "shopping", "%50\\%\\_off\\\\sale%")


@pytest.mark.asyncio
async def test_list_show_direct_renders_multiple_lists(monkeypatch):
    db = _FakeDB(
        [
            {
                "id": "pantry",
                "name": "Pantry",
                "item_id": "item-1",
                "text": "rice",
                "completed": False,
                "quantity": None,
                "category": None,
            },
            {
                "id": "shopping",
                "name": "Shopping",
                "item_id": "item-2",
                "text": "milk",
                "completed": False,
                "quantity": None,
                "category": None,
            },
        ]
    )
    _install_fake_db(monkeypatch, db)

    result = await _execute_list_show_direct(
        Intent("list_show", {"list_type": "shopping"}),
        "pi-intent-lab",
    )

    assert result == "Your shopping lists:\nPantry:\n  - rice\nShopping:\n  - milk"


@pytest.mark.asyncio
async def test_mutating_list_intents_still_use_mcporter(monkeypatch):
    calls = []

    async def fake_mcporter(cmd):
        calls.append(cmd)
        return "{}"

    monkeypatch.setattr("intent_router._run_mcporter", fake_mcporter)

    result = await execute_intent(
        Intent("list_add", {"list_type": "shopping", "item": "milk"}),
        "pi-intent-lab",
    )

    assert result == "Added milk to your shopping list."
    assert len(calls) == 1
    assert "zoe-data.list_add_item" in calls[0]
    assert 'text="milk"' in calls[0]
    assert "user_id=pi-intent-lab" in calls[0]
