"""STT snap on the REAL voice lane (``routers.voice_tts.voice_command`` through the lane-parity rig): a misheard command word
is recovered, a destructive snap asks first and runs on "yes", shadow changes nothing.

NOT in the ci_safe lane: the rig imports the whole voice router, the memory service and the embedding router (Jetson catch-all,
like ``test_voice_lane_parity.py``). Every phrasing is invented (public repo).
"""
from __future__ import annotations

from lane_parity_rig import MiniPatch, Rig
from routers import voice_tts as _vt


def _rig(mode: str, user: str) -> tuple[Rig, MiniPatch]:
    import skybridge_service

    mp = MiniPatch()
    rig = Rig(user=user).install(mp, flags={"ZOE_STT_SNAP": mode})
    rig.route_as("timers", "set_timer")          # no sidecar in the rig: pin the router so Skybridge's veto agrees
    mp.setattr(skybridge_service._timers, "_by_owner", {})    # a private timer store, restored on undo
    return rig, mp


def _clear_timers(_owner: str) -> None:
    """The rig's undo restores the module's own timer store; nothing else to clean."""


async def test_on_a_misheard_timer_word_runs_the_timer_and_says_it_back():
    rig, mp = _rig("on", "demo_stt_snap_act")
    try:
        out = await rig.voice("Set a time for 5 minutes.")
        assert not out.brain_reached, out.stage
        assert "timer" in out.reply.lower() and "5 minutes" in out.reply.lower(), out.reply
    finally:
        _clear_timers(rig.user)
        mp.undo()


async def test_shadow_changes_nothing():
    rig, mp = _rig("shadow", "demo_stt_snap_shadow")
    try:
        out = await rig.voice("Set a time for 5 minutes.")
        assert out.brain_reached, out.stage                       # today's behaviour: the brain answers
    finally:
        _clear_timers(rig.user)
        mp.undo()


async def test_on_a_destructive_snap_asks_and_runs_only_on_yes():
    rig, mp = _rig("on", "demo_stt_snap_confirm")
    try:
        await rig.voice("Set a timer for 5 minutes.")
        asked = await rig.voice("Council the timer.")
        assert asked.pending_confirmation and asked.reply == "Did you mean: cancel the timer?", asked.reply
        assert not asked.brain_reached
        assert _vt._PENDING_CONFIRMATIONS[rig.panel]["stt_snap_text"] == "cancel the timer"
        done = await rig.voice("Yes.")
        assert "cancel" in done.reply.lower(), done.reply
        assert rig.panel not in _vt._PENDING_CONFIRMATIONS
    finally:
        _clear_timers(rig.user)
        mp.undo()


async def test_on_a_destructive_snap_does_nothing_on_no():
    rig, mp = _rig("on", "demo_stt_snap_no")
    try:
        await rig.voice("Set a timer for 5 minutes.")
        await rig.voice("Council the timer.")
        out = await rig.voice("No.")
        assert "cancel" in out.reply.lower()                      # "Okay, cancelled." - the question, not the timer
        status = await rig.voice("How long is left on the timer?")
        assert "left" in status.reply.lower(), status.reply       # the timer is still running
    finally:
        _clear_timers(rig.user)
        mp.undo()


async def test_off_never_snaps():
    rig, mp = _rig("off", "demo_stt_snap_off")
    try:
        out = await rig.voice("Council the timer.")
        assert not out.pending_confirmation and out.brain_reached
    finally:
        _clear_timers(rig.user)
        mp.undo()
