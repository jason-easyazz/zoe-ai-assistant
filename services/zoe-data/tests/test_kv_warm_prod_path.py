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
        zfc._CARD_SEEN[uid] = "cardv1"
        return "cardv1"

    monkeypatch.setattr(zfc, "_CARD_SEEN", {"zed_uid": "cardv1"})
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


def _sidecar(monkeypatch, systems, cur):
    """A fake sidecar: the first request 'refreshes' the card (cur moves to cardv2), each reports the next system size."""
    import httpx

    import brain_slot_guard

    posts = []
    sizes = iter(systems)

    async def handler(request):
        posts.append(json.loads(request.content)["body"])
        if len(posts) == 1:
            cur["v"] = "cardv2"
        body = '"ready"\n{"done": true, "context_budget": {"system": %d}, "prompt_cache": [{"prompt_n": 5, "cache_n": 9}]}\n'
        return httpx.Response(200, headers={"content-type": "application/x-ndjson"},
                              content=(body % next(sizes)).encode())

    async def _card(uid):
        zfc._CARD_SEEN[uid] = cur["v"]
        return cur["v"]

    real = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **k: real(*a, transport=httpx.MockTransport(handler), **k))
    monkeypatch.setattr(zfc, "_card_version", _card)
    monkeypatch.setenv("ZOE_FLUE_BRAIN_URL", "http://sidecar.test")
    monkeypatch.setenv("ZOE_BRAIN_TOKEN", "t")
    monkeypatch.delenv("ZOE_BRAIN_KV_WARM_ON_WAKE", raising=False)
    brain_slot_guard.reset()
    monkeypatch.setattr(zfc, "_kv_warm_inflight", False)
    monkeypatch.setattr(zfc, "_kv_warm_session", {"id": "", "uses": 0, "seq": 0})
    return posts


def test_warm_repeats_once_when_the_card_was_not_rendered(monkeypatch, prod_turn, caplog):
    """Cold/stale card cache: the first warm renders a smaller system prompt than the member's last turn."""
    prod_turn("How are you going tonight?")
    zfc._LAST_TURN["zed_uid"]["system"] = 2942
    zfc._LAST_TURN["zed_uid"]["card"] = "cardv2"      # unchanged version: only the size signal fires
    caplog.set_level("INFO", logger="zoe_flue_client")
    posts = _sidecar(monkeypatch, [2380, 2942], {"v": "cardv2"})
    assert _run(zfc.warm_kv_prefix("zed_uid")) == "warmed"
    assert len(posts) == 2
    line = [r.getMessage() for r in caplog.records if "FLUE_KV_WARM " in r.getMessage()][0]
    wire = prod_turn("How are you going tonight?")
    assert f"key={zfc.prefix_key(wire, 'cardv2')}" in line


def test_warm_repeats_when_only_the_card_version_moved_at_equal_size(monkeypatch, prod_turn, caplog):
    """A same-size replacement card defeats the token heuristic; the version signal still forces the repeat, and the
    FLUE_KV_WARM key names the refreshed version."""
    prod_turn("How are you going tonight?")           # the memo records card=cardv1 (fixture)
    zfc._LAST_TURN["zed_uid"]["system"] = 2942
    caplog.set_level("INFO", logger="zoe_flue_client")
    posts = _sidecar(monkeypatch, [2942, 2942], {"v": "cardv2"})   # zoe-data now serves a new card
    assert _run(zfc.warm_kv_prefix("zed_uid")) == "warmed"
    assert len(posts) == 2
    line = [r.getMessage() for r in caplog.records if "FLUE_KV_WARM " in r.getMessage()][0]
    wire = prod_turn("How are you going tonight?")
    assert f"key={zfc.prefix_key(wire, 'cardv2')}" in line
    assert f"key={zfc.prefix_key(wire, 'cardv1')}" not in line


def test_warm_does_not_repeat_when_nothing_moved(monkeypatch, prod_turn):
    prod_turn("How are you going tonight?")
    zfc._LAST_TURN["zed_uid"]["system"] = 2942
    zfc._LAST_TURN["zed_uid"]["card"] = "cardv2"
    posts = _sidecar(monkeypatch, [2942, 2942], {"v": "cardv2"})
    assert _run(zfc.warm_kv_prefix("zed_uid")) == "warmed"
    assert len(posts) == 1


def test_wake_fresh_pin_lookup_is_a_hard_bound(monkeypatch):
    """A lookup that ignores cancellation must not delay the warm past the 0.4 s cap."""
    import time

    import routers.voice_tts as v

    seen = []

    async def _kv(member=""):
        seen.append(member)
        return "warmed"

    async def _stubborn(panel_id, fallback_db=None):
        for _ in range(30):   # ~1.5 s, then it ends by itself so the loop can close
            try:
                await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                continue   # swallows cancellation: wait_for would block on this

    monkeypatch.setattr(v, "_brain_prewarm_on_wake_enabled", lambda: True)
    monkeypatch.setattr(v, "_get_or_create_voice_session", lambda p: "voice-panel-t-1")
    monkeypatch.setattr(v, "_VOICE_SESSIONS", {})
    monkeypatch.setattr(v, "_LAST_VOICE_SPEAKER", {})
    monkeypatch.setattr(v, "_resolve_fresh_panel_member", _stubborn)
    monkeypatch.setattr(zfc, "warm_kv_prefix", _kv)
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")

    async def go():
        t0 = time.monotonic()
        task = asyncio.ensure_future(v._prewarm_brain_for_panel("p1"))
        for _ in range(40):
            await asyncio.sleep(0.05)
            if seen:
                break
        elapsed = time.monotonic() - t0
        task.cancel()
        return elapsed

    elapsed = asyncio.run(go())
    assert seen == [""] and elapsed < 0.8


def test_warm_repeats_on_a_small_system_size_delta(monkeypatch, prod_turn):
    """Any system-size difference from the member's last turn means a different prompt was rendered."""
    prod_turn("How are you going tonight?")
    zfc._LAST_TURN["zed_uid"]["system"] = 2942
    zfc._LAST_TURN["zed_uid"]["card"] = "cardv2"
    posts = _sidecar(monkeypatch, [2939, 2942], {"v": "cardv2"})
    assert _run(zfc.warm_kv_prefix("zed_uid")) == "warmed"
    assert len(posts) == 2


def test_warm_repeats_when_the_sidecar_reports_no_system_size(monkeypatch, prod_turn):
    """No size to compare (0 on either side) cannot confirm the card was rendered: repeat."""
    prod_turn("How are you going tonight?")
    zfc._LAST_TURN["zed_uid"]["system"] = 2942
    zfc._LAST_TURN["zed_uid"]["card"] = "cardv2"
    posts = _sidecar(monkeypatch, [0, 0], {"v": "cardv2"})
    assert _run(zfc.warm_kv_prefix("zed_uid")) == "warmed"
    assert len(posts) == 2


def test_warm_repeats_when_the_members_last_turn_had_no_size(monkeypatch, prod_turn):
    prod_turn("How are you going tonight?")
    zfc._LAST_TURN["zed_uid"]["system"] = 0
    zfc._LAST_TURN["zed_uid"]["card"] = "cardv2"
    posts = _sidecar(monkeypatch, [2942, 2942], {"v": "cardv2"})
    assert _run(zfc.warm_kv_prefix("zed_uid")) == "warmed"
    assert len(posts) == 2


def test_turn_path_awaits_no_card_lookup(monkeypatch, prod_turn):
    """The real turn must not put a card/DB lookup on its critical path (it is only a diagnostic key)."""
    calls = []

    async def _boom(uid):
        calls.append(uid)
        raise AssertionError("the turn path awaited a card lookup")

    monkeypatch.setattr(zfc, "_card_version", _boom)
    wire = prod_turn("How are you going tonight?")
    assert wire and not calls
    assert zfc._LAST_TURN["zed_uid"]["card"] == "cardv1", "the key uses the in-memory last-requested version"


def test_warm_preparation_is_inside_the_timeout_and_before_the_slot_guard(monkeypatch, prod_turn):
    import brain_slot_guard

    started = []
    monkeypatch.setattr(brain_slot_guard, "turn_started", lambda *a, **k: started.append(1))

    async def _slow(uid):
        await asyncio.sleep(30)
        return "x"

    posts = _sidecar(monkeypatch, [2942], {"v": "cardv1"})
    monkeypatch.setattr(zfc, "_card_version", _slow)
    monkeypatch.setattr(zfc, "_KV_WARM_MAX_S", 0.2)
    zfc._LAST_TURN.clear()
    import time
    t0 = time.monotonic()
    assert _run(zfc.warm_kv_prefix("zed_uid")) == "failed:timeout"
    assert time.monotonic() - t0 < 2
    assert not started, "the slot guard must not be held while preparing"
    assert not posts


def test_warm_with_no_memo_repeats_once(monkeypatch, prod_turn):
    """After a restart there is no remembered turn: the card cannot be confirmed rendered, so repeat once."""
    zfc._LAST_TURN.clear()
    posts = _sidecar(monkeypatch, [2380, 2942], {"v": "cardv1"})
    assert _run(zfc.warm_kv_prefix("zed_uid")) == "warmed"
    assert len(posts) == 2


def test_guest_warm_never_repeats(monkeypatch, prod_turn):
    posts = _sidecar(monkeypatch, [2380, 2942], {"v": "cardv1"})
    assert _run(zfc.warm_kv_prefix("")) == "warmed"
    assert len(posts) == 1
