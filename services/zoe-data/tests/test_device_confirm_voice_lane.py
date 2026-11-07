"""The device confirm guard on the REAL voice lane (``routers.voice_tts.voice_command`` through the lane-parity rig): a garbled
short light command nothing claims is asked about once, a "yes" switches the light through Skybridge (with its receipt), a "no"
switches nothing, and the kill switch restores today's behaviour (the brain answers).

NOT in the ci_safe lane: the rig imports the whole voice router, the memory service and the embedding router (Jetson catch-all,
like ``test_stt_snap_voice_lane.py``). Every phrasing is invented (public repo).
"""
from __future__ import annotations

from lane_parity_rig import MiniPatch, Rig
from routers import voice_tts as _vt


def _rig(user: str, *, flag: str = "1") -> tuple[Rig, MiniPatch, list]:
    import device_confirm
    import skybridge_service as sky
    import smart_home_service as shs

    mp = MiniPatch()
    rig = Rig(user=user).install(mp, flags={"ZOE_DEVICE_CONFIRM_GUARD": flag})
    rig.route_as("lists", "show_list", conf=0.98)        # the live guess: a lists verdict for words that name no list
    mp.setattr(device_confirm, "at_the_panel", lambda _caller: True)   # the rig is a session caller; this guard is the panel's
    device_confirm.reset()
    devices = [shs._device_from_light({"entity_id": "light.demo_lamp", "state": "off",
                                       "attributes": {"friendly_name": "Demo Lamp"}})]
    switched: list = []

    async def list_devices():
        return [dict(d) for d in devices]

    async def list_scenes():
        return []

    async def set_device(entity_id, on, *, brightness_pct=None):
        switched.append((entity_id, on))
        return True

    async def room_eids(_ctx, _db):
        return set()

    mp.setattr(shs, "list_devices", list_devices)
    mp.setattr(shs, "list_scenes", list_scenes)
    mp.setattr(shs, "set_device", set_device)
    mp.setattr(sky, "_room_eids_for_context", room_eids)
    return rig, mp, switched


async def test_a_garble_is_asked_about_and_a_yes_switches_the_light():
    rig, mp, switched = _rig("demo_device_confirm_yes")
    try:
        asked = await rig.voice("Shone the lots.")
        assert asked.pending_confirmation and asked.reply == "Light on?", asked.reply
        assert not asked.brain_reached and not switched
        assert _vt._PENDING_CONFIRMATIONS[rig.panel]["stt_snap_text"] == "turn on the light"
        rig.route_as("smart_home", "home")                # what the router says about the command a yes runs
        done = await rig.voice("Yes.")
        assert switched == [("light.demo_lamp", True)], switched
        assert "turned" in done.reply.lower() and "on" in done.reply.lower(), done.reply
        assert rig.panel not in _vt._PENDING_CONFIRMATIONS
    finally:
        mp.undo()


async def test_a_no_switches_nothing():
    rig, mp, switched = _rig("demo_device_confirm_no")
    try:
        asked = await rig.voice("Two of the one.")
        assert asked.reply == "Turn the light off?", asked.reply
        out = await rig.voice("No.")
        assert "cancel" in out.reply.lower() and not switched, out.reply
    finally:
        mp.undo()


async def test_a_feelings_line_with_no_device_word_is_never_asked_about():
    rig, mp, switched = _rig("demo_device_confirm_feelings")
    try:
        out = await rig.voice("I've had a lot on.")
        assert not out.pending_confirmation and out.brain_reached and not switched
    finally:
        mp.undo()


async def test_the_kill_switch_restores_todays_turn():
    rig, mp, switched = _rig("demo_device_confirm_off", flag="0")
    try:
        out = await rig.voice("Shone the lots.")
        assert not out.pending_confirmation and out.brain_reached and not switched
    finally:
        mp.undo()
