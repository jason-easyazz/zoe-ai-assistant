"""The hearing note on the REAL voice lane (``routers.voice_tts.voice_command`` through the lane-parity rig, the real brain dispatch
and the real Flue seam; only the transport under the seam is a stub): a short first turn after a wake that nothing claims reaches
the sidecar with ONE hearing note after the words; a clear line, a member's own session and the kill switch reach it unchanged.

NOT in the ci_safe lane: the rig imports the whole voice router, the memory service and the embedding router (Jetson catch-all,
like ``test_device_confirm_voice_lane.py``). Every phrasing is invented (public repo).

Negative control (run by hand when written, went red, then restored): with ``_hearing_note_kwargs`` in ``routers/voice_tts.py``
returning ``{}`` unconditionally the first test fails (the lane's decision never reaches the seam).
"""
from __future__ import annotations

from lane_parity_rig import MiniPatch, Rig


def _rig(user: str, *, flag: str = "1", panel: bool = True) -> tuple[Rig, MiniPatch]:
    import device_confirm

    mp = MiniPatch()
    rig = Rig(user=user).install(mp, flags={"ZOE_UNCLEAR_TURN_NOTE": flag, "ZOE_DEVICE_CONFIRM_GUARD": "1"})
    rig.route_as("chat", None, conf=0.4)                 # the live guess for a garble: chat, not sure
    if panel:
        mp.setattr(device_confirm, "at_the_panel", lambda _caller: True)   # the rig is a session caller; the note is the panel's
    device_confirm.reset()
    return rig, mp


def _sent(rig: Rig) -> str:
    return str(rig.brain_calls[-1]["message"])


async def test_a_short_unclaimed_first_turn_reaches_the_brain_with_one_hearing_note():
    import unclear_turn

    rig, mp = _rig("demo_unclear_first_turn")
    try:
        out = await rig.voice("Gribble the womble.")
        assert out.brain_reached and not out.pending_confirmation
        sent = _sent(rig)
        assert sent.count(unclear_turn.BLOCK) == 1 and sent.rstrip().endswith("[END MEMORY CONTEXT]")
        assert sent.index("Gribble the womble.") < sent.index("hearing note")       # after the words, never before
    finally:
        mp.undo()


async def test_a_clear_line_reaches_the_brain_unchanged():
    rig, mp = _rig("demo_unclear_clear_line")
    try:
        out = await rig.voice("I had a long day at the marble works.")
        assert out.brain_reached and "hearing note" not in _sent(rig)
    finally:
        mp.undo()


async def test_a_members_own_session_never_gets_the_note():
    rig, mp = _rig("demo_unclear_session", panel=False)
    try:
        out = await rig.voice("Gribble the womble.")
        assert out.brain_reached and "hearing note" not in _sent(rig)
    finally:
        mp.undo()


async def test_the_kill_switch_restores_todays_turn():
    """Flag off: the lane reads the flag FIRST and nothing else of the module runs on the turn (each entry point is stubbed to raise;
    control by hand: the ``enabled()`` check removed from ``_hearing_note_kwargs`` -> the stubbed ``noted`` is reached, its error is
    swallowed, and with the stub removed the note would ride - the first test's inverse)."""
    import unclear_turn

    rig, mp = _rig("demo_unclear_off", flag="0")
    reached = []
    for name in ("noted", "shape", "block"):
        mp.setattr(unclear_turn, name, lambda *a, _n=name, **k: reached.append(_n))
    try:
        out = await rig.voice("Gribble the womble.")
        assert out.brain_reached and "hearing note" not in _sent(rig)
        assert reached == [], reached
    finally:
        mp.undo()


async def test_a_distress_phrase_at_the_panel_is_still_the_fixed_hand_off():
    import distress_handoff as dh

    rig, mp = _rig("demo_unclear_distress")
    try:
        dh._NOTIFIED.clear()
        out = await rig.voice("I want to die")
        assert not out.brain_reached and "glad you told me" in out.reply and "catch" not in out.reply.lower()
    finally:
        mp.undo()
