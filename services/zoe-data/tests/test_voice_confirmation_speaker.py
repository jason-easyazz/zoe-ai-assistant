"""A pending voice confirmation answers only to the person who asked (2026-11-02).

``_PENDING_CONFIRMATIONS`` is keyed by panel, so anyone who said "yes" inside the 30 s window ran the waiting write
(a cancel, a transaction, a journal entry, a contact) as the member who asked - and the plain-yes branch skips the
PIN and guest gates the asking turn passed. Now the entry records the speaker the asking turn was attributed to and a
turn attributed to anyone else leaves it waiting and is treated as new words.

NOT in the ci_safe lane: the rig imports the whole voice router (Jetson catch-all, like test_stt_snap_voice_lane).
"""
from __future__ import annotations

from lane_parity_rig import MiniPatch, Rig
from routers import voice_tts as _vt


def _rig(user: str) -> tuple[Rig, MiniPatch]:
    import skybridge_service

    mp = MiniPatch()
    rig = Rig(user=user).install(mp, flags={"ZOE_STT_SNAP": "on"})
    rig.route_as("timers", "set_timer")
    mp.setattr(skybridge_service._timers, "_by_owner", {})
    return rig, mp


async def test_another_members_yes_leaves_the_question_waiting_and_the_askers_yes_answers_it():
    rig, mp = _rig("demo_confirm_asker")
    try:
        await rig.voice("Set a timer for 5 minutes.")
        asked = await rig.voice("Council the timer.")
        assert asked.pending_confirmation, asked.reply
        assert _vt._PENDING_CONFIRMATIONS[rig.panel]["speaker"] == "demo_confirm_asker"

        rig.user = "demo_confirm_bystander"          # same panel, a different session identity
        other = await rig.voice("Yes.")
        assert rig.panel in _vt._PENDING_CONFIRMATIONS, "the bystander's yes consumed the asker's question"
        assert "cancel" not in other.reply.lower(), other.reply

        rig.user = "demo_confirm_asker"
        done = await rig.voice("Yes.")
        assert "cancel" in done.reply.lower(), done.reply
        assert rig.panel not in _vt._PENDING_CONFIRMATIONS
    finally:
        mp.undo()


async def test_a_bystanders_no_does_not_drop_the_askers_question():
    rig, mp = _rig("demo_confirm_asker2")
    try:
        await rig.voice("Set a timer for 5 minutes.")
        asked = await rig.voice("Council the timer.")
        assert asked.pending_confirmation, asked.reply
        rig.user = "demo_confirm_bystander2"
        await rig.voice("No.")
        assert rig.panel in _vt._PENDING_CONFIRMATIONS
    finally:
        mp.undo()
