"""Voice lane == typed chat lane, cell by cell (register G11 / felt gap 7), plus the instrument's own proof.

The rig (``lane_parity_rig``) runs the REAL ``chat_stream_generator`` and the REAL ``voice_command`` over a fake store and a stubbed
model transport; ``lane_parity_cells`` holds the utterance scripts and the per-cell verdicts. This is NOT in the ci_safe lane: it
imports the whole voice router, the memory service and the embedding router (Jetson catch-all).

Instrument proof (the standing rule "break the fix, the test must go red"):
  * flag controls  - turn a tier's flag OFF and the cell that guards it MUST fail on both lanes;
  * code controls  - undo each voice-lane change in turn and its cell MUST fail on the voice lane only.
"""
import pytest
from _pytest.monkeypatch import MonkeyPatch

import lane_parity_cells as lpc
from lane_parity_cells import CELLS


@pytest.mark.parametrize("cell", CELLS, ids=[c.cid for c in CELLS])
async def test_cell_has_parity(cell):
    results = await lpc.run_cells(MonkeyPatch, only={cell.cid})
    [r] = results
    assert r.voice_ok, f"voice FAILED {cell.cid}: {r.voice_detail}"
    assert r.chat_ok in (None, True), f"chat FAILED {cell.cid}: {r.chat_detail}"


# ── the instrument can see a missing tier (flag controls) ──────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("flag,cells", [
    ({"ZOE_CORRECTION_APPLY": "0"}, {"correction"}),
    ({"ZOE_VERIFY_ON_CHALLENGE": "0"}, {"verify_on_challenge"}),
    ({"ZOE_CONVERSATION_FEEDBACK": "0"}, {"feedback_down", "feedback_up"}),
    ({"ZOE_MEMORY_PROVENANCE_ANSWERS": "0"}, {"provenance", "forget_it"}),
])
async def test_instrument_goes_red_when_a_tier_is_off(flag, cells):
    results = await lpc.run_cells(MonkeyPatch, only=cells, flags=flag)
    assert results and all(not r.parity for r in results), [(r.cid, r.chat_ok, r.voice_ok) for r in results]
    # a tier that is off is off on BOTH lanes: the chat column is red too (the rows are lane-neutral)
    assert all(r.chat_ok is False and r.voice_ok is False for r in results)


# ── ... and a lane-private regression (code controls) ──────────────────────────────────────────────────────────────────────────
async def test_instrument_goes_red_without_the_voice_contacts_read(monkeypatch):
    import fast_tiers

    monkeypatch.setattr(fast_tiers, "keyword_intent_allowed", lambda *a, **k: False)   # voice declines the contacts read
    results = await lpc.run_cells(MonkeyPatch, only={"contact_list", "contact_lookup"})
    assert all(r.chat_ok is False or r.voice_ok is False for r in results)
    assert all(not r.voice_ok for r in results)


async def test_instrument_goes_red_with_the_old_confirmation_test(monkeypatch):
    from routers import voice_tts

    def old(text):          # the pre-fix rule: any confirm keyword anywhere confirms
        lc = (text or "").lower().strip().rstrip(".!?")
        if voice_tts._contains_decision_keyword(lc, voice_tts._CONFIRM_KEYWORDS):
            return "yes"
        return "no" if voice_tts._contains_decision_keyword(lc, voice_tts._CANCEL_KEYWORDS) else None

    monkeypatch.setattr(voice_tts, "_pending_decision", old)
    results = await lpc.run_cells(MonkeyPatch, only={"pending_not_a_yes", "pending_not_correct"})
    assert results and all(not r.voice_ok for r in results)


async def test_instrument_goes_red_if_the_voice_lane_skips_the_conversation_tiers(monkeypatch):
    import fast_tiers

    real = fast_tiers.resolve

    async def skip_conversation(text, user_id, session_id, **kw):
        if kw.get("phase") == "conversation" and kw.get("channel") == "voice":
            return None
        return await real(text, user_id, session_id, **kw)

    monkeypatch.setattr(fast_tiers, "resolve", skip_conversation)
    results = await lpc.run_cells(MonkeyPatch, only={"feedback_down", "feedback_up", "correction"})
    voice_red = {r.cid for r in results if not r.voice_ok}
    # feedback_down survives on voice: the verdict is also recorded at the top of the turn (before a waiting write can swallow it)
    assert {"feedback_up", "correction"} <= voice_red
    assert all(r.chat_ok for r in results), [(r.cid, r.chat_detail) for r in results]


def test_the_table_renders():
    results = [lpc.CellResult("a", "t", True, True, "", ""), lpc.CellResult("b", "t", None, False, "", "", voice_only=True)]
    out = lpc.render(results)
    assert "parity 1/2" in out and "n/a" in out


# ── review round 1 ───────────────────────────────────────────────────────────────────────────────────────────────────────────────
from lane_parity_rig import MiniPatch, Rig  # noqa: E402
from routers import voice_tts as _vt  # noqa: E402


def _kinds(rig):
    return [(f["params"][3], f["params"][4]) for f in rig.db.feedback]


@pytest.mark.parametrize("lane", ["chat", "voice"])
async def test_a_verdict_is_recorded_even_when_an_explained_answer_takes_the_fix(lane):
    """"that's wrong, it's Marisol" right after "why did you say that?" is the provenance fix's to answer - and still leaves its
    `correction` row (the feedback write is a side effect that runs before the early return)."""
    mp = MiniPatch()
    try:
        rig = Rig(user=f"demo_lane_fb_explained_{lane}").install(mp)
        say = rig.chat if lane == "chat" else rig.voice
        await say("Remember that my sister is called Marisa.")
        rig.brain_reply, rig.serve_on_brain = "Your sister is called Marisa.", True
        await say("What is my sister called?")
        rig.serve_on_brain = False
        await say("Why did you say that?")
        out = await say("That's wrong, it's Marisol")
        assert out.stage.startswith("tier:provenance"), out.stage          # the fix's own reply is kept
        assert _kinds(rig) == [("correction", "Marisol")]
    finally:
        mp.undo()


async def test_a_waiting_spoken_write_does_not_swallow_the_verdict():
    mp = MiniPatch()
    try:
        rig = Rig(user="demo_lane_fb_pending").install(mp)
        rig.db.add_message(rig.voice_session, "assistant", "Stefan Edberg won it.", rig.user)
        first = await rig.voice("Add my brother Percival.")
        assert first.pending_confirmation
        out = await rig.voice("That was wrong.")
        assert "cancel" in out.reply.lower()                                # the existing reply stands
        assert _kinds(rig) == [("thumbs_down", None)]                      # exactly one row: the top-of-turn call and the tier dedupe
        assert rig.db.people == []
    finally:
        mp.undo()


async def test_contacts_answer_sends_the_panel_events():
    mp = MiniPatch()
    try:
        rig = Rig(user="demo_lane_people_events").install(mp)
        await rig.voice("Add my brother Percival.")
        await rig.voice("Yes.")
        rig.events.clear()
        out = await rig.voice("List my contacts.")
        assert out.stage == "voice:people_search" and "percival" in out.reply.lower()
        panel = [e for e in rig.events if e in ("voice:responding", "voice:done")]
        assert panel[-2:] == ["voice:responding", "voice:done"] and panel.count("voice:done") == 1
    finally:
        mp.undo()


async def test_the_rig_leaves_the_module_voice_state_as_it_found_it():
    _vt._PENDING_CONFIRMATIONS["sentinel-panel"] = {"intent_name": "x"}
    _vt._VOICE_SESSIONS["sentinel-panel"] = {"bound_user_id": "x"}
    pending, sessions = dict(_vt._PENDING_CONFIRMATIONS), dict(_vt._VOICE_SESSIONS)
    try:
        await lpc.run_cells(MonkeyPatch, only={"contact_add", "pending_not_a_yes"})
        assert _vt._PENDING_CONFIRMATIONS == pending and _vt._VOICE_SESSIONS == sessions
    finally:
        _vt._PENDING_CONFIRMATIONS.pop("sentinel-panel", None)
        _vt._VOICE_SESSIONS.pop("sentinel-panel", None)
