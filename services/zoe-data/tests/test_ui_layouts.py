"""ui_layouts — layout memory: family bucketing, storage helpers (mocked db),
compose_card prompt wiring, and never-break-a-turn failure paths."""
import json
from contextlib import asynccontextmanager

import pytest

import ui_compose
import ui_layouts
from ui_layouts import intent_family_for, layout_memory_enabled

pytestmark = pytest.mark.ci_safe


# ── intent_family_for: cheap deterministic bucketing ─────────────────────────

def test_family_stable_across_phrasings():
    a = intent_family_for("What's the weather in Geraldton?")
    b = intent_family_for("whats  the WEATHER   in geraldton!!")
    assert a == b == "weather geraldton"


def test_family_stopword_insensitive():
    assert (intent_family_for("please show me the weather for geraldton")
            == intent_family_for("weather geraldton"))


def test_family_digits_stripped():
    assert (intent_family_for("set a timer for 10 minutes")
            == intent_family_for("set a timer for 25 minutes"))


def test_family_caps_salient_tokens():
    msg = ("compare flight hotel car train ferry bus tram scooter prices "
           "across every provider")
    fam = intent_family_for(msg)
    assert len(fam.split()) == 6
    assert fam == "compare flight hotel car train ferry"


def test_family_empty_and_degenerate_fall_back_to_general():
    assert intent_family_for("") == "general"
    assert intent_family_for(None) == "general"
    assert intent_family_for("123 456 !!! ...") == "general"
    assert intent_family_for("the a an is") == "general"


# ── flag: ZOE_LAYOUT_MEMORY (default ON) ─────────────────────────────────────

def test_layout_memory_flag(monkeypatch):
    monkeypatch.delenv("ZOE_LAYOUT_MEMORY", raising=False)
    assert layout_memory_enabled() is True  # default ON
    monkeypatch.setenv("ZOE_LAYOUT_MEMORY", "0")
    assert layout_memory_enabled() is False
    monkeypatch.setenv("ZOE_LAYOUT_MEMORY", "off")
    assert layout_memory_enabled() is False
    monkeypatch.setenv("ZOE_LAYOUT_MEMORY", "1")
    assert layout_memory_enabled() is True


# ── storage helpers against a mocked db ctx ─────────────────────────────────

TREE = {"component": "Stack", "children": [
    {"component": "Text", "text": "19° and clear", "role": "title"},
]}


class _FakeDB:
    def __init__(self, row=None):
        self.row = row
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self.row

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return "OK"


def _fake_ctx(db):
    @asynccontextmanager
    async def ctx():
        yield db
    return ctx


def _broken_ctx():
    @asynccontextmanager
    async def ctx():
        raise RuntimeError("db down")
        yield  # pragma: no cover
    return ctx


@pytest.mark.asyncio
async def test_get_layout_hit(monkeypatch):
    db = _FakeDB(row={"tree": json.dumps(TREE)})
    monkeypatch.setattr(ui_layouts, "get_db_ctx", _fake_ctx(db))
    assert await ui_layouts.get_layout("jason", "weather geraldton") == TREE
    kind, sql, args = db.calls[0]
    assert kind == "fetchrow" and args == ("jason", "weather geraldton")


@pytest.mark.asyncio
async def test_get_layout_miss_and_garbage(monkeypatch):
    monkeypatch.setattr(ui_layouts, "get_db_ctx", _fake_ctx(_FakeDB(row=None)))
    assert await ui_layouts.get_layout("jason", "weather") is None
    monkeypatch.setattr(ui_layouts, "get_db_ctx",
                        _fake_ctx(_FakeDB(row={"tree": "not json {"})))
    assert await ui_layouts.get_layout("jason", "weather") is None
    assert await ui_layouts.get_layout("", "weather") is None
    assert await ui_layouts.get_layout("jason", "") is None


@pytest.mark.asyncio
async def test_save_layout_upserts(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(ui_layouts, "get_db_ctx", _fake_ctx(db))
    await ui_layouts.save_layout("jason", "weather geraldton", TREE)
    kind, sql, args = db.calls[0]
    assert kind == "execute"
    assert "ON CONFLICT (user_id, intent_family)" in sql
    assert "uses = ui_layouts.uses + 1" in sql
    assert args[1] == "jason" and args[2] == "weather geraldton"
    assert json.loads(args[3]) == TREE


@pytest.mark.asyncio
async def test_touch_bumps_uses(monkeypatch):
    db = _FakeDB()
    monkeypatch.setattr(ui_layouts, "get_db_ctx", _fake_ctx(db))
    await ui_layouts.touch("jason", "weather geraldton")
    kind, sql, args = db.calls[0]
    assert kind == "execute" and "uses = uses + 1" in sql
    assert args[0] == "jason" and args[1] == "weather geraldton"


@pytest.mark.asyncio
async def test_storage_failure_paths_never_raise(monkeypatch):
    monkeypatch.setattr(ui_layouts, "get_db_ctx", _broken_ctx())
    assert await ui_layouts.get_layout("jason", "weather") is None
    await ui_layouts.save_layout("jason", "weather", TREE)  # must not raise
    await ui_layouts.touch("jason", "weather")              # must not raise


# ── compose_card wiring: layout hint in the prompt + save-after-compose ─────

class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _CapturingClient:
    """Fake httpx.AsyncClient that records every POST body."""

    def __init__(self, payload, sent):
        self._payload = payload
        self._sent = sent

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self._sent.append(json)
        return _FakeResponse(self._payload)


def _llm_payload(tree):
    return {"choices": [{"message": {"content": json.dumps(tree)}}]}


def _patch_client(monkeypatch, sent, tree=TREE):
    monkeypatch.setattr(
        ui_compose.httpx, "AsyncClient",
        lambda **kw: _CapturingClient(_llm_payload(tree), sent))


@pytest.mark.asyncio
async def test_compose_injects_stored_layout_hint(monkeypatch):
    monkeypatch.delenv("ZOE_LAYOUT_MEMORY", raising=False)
    sent, saved = [], []

    async def fake_get(user_id, family):
        return TREE

    async def fake_save(user_id, family, tree):
        saved.append((user_id, family, tree))

    monkeypatch.setattr(ui_layouts, "get_layout", fake_get)
    monkeypatch.setattr(ui_layouts, "save_layout", fake_save)
    _patch_client(monkeypatch, sent)

    card = await ui_compose.compose_card(
        "what's the weather in geraldton", "19 and clear", user_id="jason")
    assert card and card["component"] == "compose"

    user_msg = sent[0]["messages"][1]["content"]
    assert "Previously, a good layout for a similar request was: " in user_msg
    assert "Prefer this structure, updated with the new content." in user_msg
    assert json.dumps(TREE, separators=(",", ":")) in user_msg
    # successful compose saved back under the same (user, family)
    assert saved == [("jason", "weather geraldton", TREE)]


@pytest.mark.asyncio
async def test_compose_no_hint_when_nothing_stored(monkeypatch):
    monkeypatch.delenv("ZOE_LAYOUT_MEMORY", raising=False)
    sent, saved = [], []

    async def fake_get(user_id, family):
        return None

    async def fake_save(user_id, family, tree):
        saved.append((user_id, family, tree))

    monkeypatch.setattr(ui_layouts, "get_layout", fake_get)
    monkeypatch.setattr(ui_layouts, "save_layout", fake_save)
    _patch_client(monkeypatch, sent)

    card = await ui_compose.compose_card("weather?", "19 and clear", user_id="jason")
    assert card is not None
    assert "Previously, a good layout" not in sent[0]["messages"][1]["content"]
    assert len(saved) == 1  # first compose still seeds the layout


@pytest.mark.asyncio
async def test_compose_skips_layout_memory_without_user(monkeypatch):
    monkeypatch.delenv("ZOE_LAYOUT_MEMORY", raising=False)
    sent, calls = [], []

    async def fake_get(user_id, family):
        calls.append("get")
        return TREE

    async def fake_save(user_id, family, tree):
        calls.append("save")

    monkeypatch.setattr(ui_layouts, "get_layout", fake_get)
    monkeypatch.setattr(ui_layouts, "save_layout", fake_save)
    _patch_client(monkeypatch, sent)

    card = await ui_compose.compose_card("weather?", "19 and clear")
    assert card is not None and calls == []


@pytest.mark.asyncio
async def test_compose_skips_layout_memory_when_flag_off(monkeypatch):
    monkeypatch.setenv("ZOE_LAYOUT_MEMORY", "0")
    sent, calls = [], []

    async def fake_get(user_id, family):
        calls.append("get")
        return TREE

    async def fake_save(user_id, family, tree):
        calls.append("save")

    monkeypatch.setattr(ui_layouts, "get_layout", fake_get)
    monkeypatch.setattr(ui_layouts, "save_layout", fake_save)
    _patch_client(monkeypatch, sent)

    card = await ui_compose.compose_card("weather?", "19 and clear", user_id="jason")
    assert card is not None and calls == []
    assert "Previously, a good layout" not in sent[0]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_compose_survives_layout_memory_blowing_up(monkeypatch):
    monkeypatch.delenv("ZOE_LAYOUT_MEMORY", raising=False)
    sent = []

    async def bad_get(user_id, family):
        raise RuntimeError("layout store exploded")

    async def bad_save(user_id, family, tree):
        raise RuntimeError("layout store exploded")

    monkeypatch.setattr(ui_layouts, "get_layout", bad_get)
    monkeypatch.setattr(ui_layouts, "save_layout", bad_save)
    _patch_client(monkeypatch, sent)

    card = await ui_compose.compose_card("weather?", "19 and clear", user_id="jason")
    assert card is not None and card["component"] == "compose"


@pytest.mark.asyncio
async def test_layout_hint_is_bounded(monkeypatch):
    monkeypatch.delenv("ZOE_LAYOUT_MEMORY", raising=False)
    sent = []
    huge = {"component": "Stack", "children": [
        {"component": "Text", "text": "x" * 200, "role": "body"} for _ in range(40)
    ]}

    async def fake_get(user_id, family):
        return huge

    async def fake_save(user_id, family, tree):
        pass

    monkeypatch.setattr(ui_layouts, "get_layout", fake_get)
    monkeypatch.setattr(ui_layouts, "save_layout", fake_save)
    _patch_client(monkeypatch, sent)

    await ui_compose.compose_card("weather?", "19 and clear", user_id="jason")
    user_msg = sent[0]["messages"][1]["content"]
    marker = "Previously, a good layout for a similar request was: "
    start = user_msg.index(marker) + len(marker)
    end = user_msg.index("\nPrefer this structure")
    assert (end - start) <= ui_compose._LAYOUT_HINT_MAX_CHARS
