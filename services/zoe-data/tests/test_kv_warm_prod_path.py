"""The wake KV warm vs the PRODUCTION turn path (run_flue_brain_streaming), not a synthetic envelope.

The manner block is chosen by the language of the utterance, and the sidecar renders a cached user-model card, so
the warm must replay the lines the member's last real turn sent and the key must carry the card version.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import zoe_flue_client as zfc

pytestmark = pytest.mark.ci_safe

SPANISH = "¿Cómo estás esta noche? Estoy muy cansado después de trabajar todo el día."


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def prod_turn(monkeypatch):
    import manner_block

    monkeypatch.setattr(manner_block, "enabled", lambda: True)

    async def _eligible(uid, db=None):
        return uid == "zed_uid"

    monkeypatch.setattr(manner_block, "eligible", _eligible)

    async def _persona(uid):
        return ""

    async def _card(uid):
        return "cardv1"

    monkeypatch.setattr(zfc, "_persona_context_block", _persona)
    monkeypatch.setattr(zfc, "_card_version", _card)
    monkeypatch.setattr(zfc, "_LAST_TURN", {})
    monkeypatch.setattr(zfc, "_SESSION_UID", {})
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")
    monkeypatch.setenv("ZOE_IDENTITY_BLOCK", "0")
    for k in ("ZOE_FLUE_WIRE", "ZOE_FLUE_STREAM_ENABLED", "ZOE_SEAM_RECALL_INJECT", "ZOE_VERIFY_ON_CHALLENGE",
              "ZOE_TRIVIA_HEDGE"):
        monkeypatch.delenv(k, raising=False)

    def turn(message, uid="zed_uid"):
        cap = []

        async def fake_wire2(session_id, payload, **kw):
            cap.append(json.loads(payload)["body"])
            yield "ok"

        monkeypatch.setattr(zfc, "_run_turn_aggregated_wire2", fake_wire2)

        async def go():
            return [c async for c in zfc.run_flue_brain_streaming(message, "voice-panel-t-1", uid)]

        asyncio.run(go())
        return cap[0]

    return turn


def _manner_line(wire: str) -> str:
    return next((ln for ln in wire.split("\n") if ln.startswith(" zoe-manner:")), "")


def test_prod_path_english_warm_matches_the_turn(prod_turn):
    wire = prod_turn("How are you going tonight?")
    assert _manner_line(wire)
    msg, key = _run(zfc._kv_warm_outbound("zed_uid"))
    assert _manner_line(msg) == _manner_line(wire)
    assert key == zfc.prefix_key(wire, "cardv1")


def test_prod_path_non_english_warm_matches_the_turn(prod_turn):
    """The manner block follows the utterance's language: the warm must replay the block the turn rendered."""
    english = _manner_line(prod_turn("How are you going tonight?"))
    wire = prod_turn(SPANISH)
    assert _manner_line(wire) and _manner_line(wire) != english, "fixture: Spanish must select the Spanish block"
    msg, key = _run(zfc._kv_warm_outbound("zed_uid"))
    assert _manner_line(msg) == _manner_line(wire)
    assert key == zfc.prefix_key(wire, "cardv1")


def test_negative_control_without_the_turn_memo_the_warm_is_english(prod_turn):
    """What the previous head did (blocks recomputed from the warm instruction): not the Spanish turn's block."""
    wire = prod_turn(SPANISH)
    zfc._LAST_TURN.clear()
    msg, _ = _run(zfc._kv_warm_outbound("zed_uid"))
    assert _manner_line(msg) != _manner_line(wire)


def test_key_changes_with_the_card_version(prod_turn):
    wire = prod_turn("How are you going tonight?")
    assert zfc.prefix_key(wire, "cardv1") != zfc.prefix_key(wire, "cardv2")


def test_warm_repeats_once_when_the_card_was_not_rendered(monkeypatch, prod_turn):
    """Cold/stale card cache: the first warm renders a smaller system prompt than the member's last turn."""
    import httpx

    import brain_slot_guard

    prod_turn("How are you going tonight?")
    zfc._LAST_TURN["zed_uid"]["system"] = 2942
    systems = iter([2380, 2942])
    posts = []

    async def handler(request):
        posts.append(1)
        body = '"ready"\n{"done": true, "context_budget": {"system": %d}, "prompt_cache": [{"prompt_n": 5, "cache_n": 9}]}\n'
        return httpx.Response(200, headers={"content-type": "application/x-ndjson"},
                              content=(body % next(systems)).encode())

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))
    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://sidecar.test")
    monkeypatch.setenv("ZOE_BRAIN_TOKEN", "t")
    monkeypatch.delenv("ZOE_BRAIN_KV_WARM_ON_WAKE", raising=False)
    brain_slot_guard.reset()
    monkeypatch.setattr(zfc, "_kv_warm_inflight", False)
    monkeypatch.setattr(zfc, "_kv_warm_session", {"id": "", "uses": 0, "seq": 0})
    assert _run(zfc.warm_kv_prefix("zed_uid")) == "warmed"
    assert len(posts) == 2
    brain_slot_guard.reset()
