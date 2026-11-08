"""panel_speaker_health: a panel that cannot score voices raises an owner ALARM instead of a silent all-guest night (2026-11-07:
the panel's ResNet34 self-test was refused at boot and the owner was a guest for ~8 h with nothing saying so)."""
import asyncio
import importlib
import logging

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

T0 = 1_800_000_000.0
REFUSED = {"model": "wespeaker_resnet34", "state": "refused", "reason": "too slow: 357 ms (limit 330 ms)", "attempts": 1}


@pytest.fixture
def psh(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_PANEL_SPEAKER_HEALTH_PATH", str(tmp_path / "psh.json"))
    monkeypatch.setenv("ZOE_PANEL_SPEAKER_ALARM_MIN", "10")
    import panel_speaker_health
    importlib.reload(panel_speaker_health)
    panel_speaker_health._reset_for_tests()
    return panel_speaker_health


def test_a_refused_model_alarms_after_the_bar_once_and_is_on_health(psh, caplog):
    caplog.set_level(logging.WARNING, logger="panel_speaker_health")
    assert psh.note_turn("pi", REFUSED, now=T0) is None                          # first sight: a warning, not yet an alarm
    assert psh.note_turn("pi", REFUSED, now=T0 + 300) is None                    # 5 min: under the 10-min bar
    text = psh.note_turn("pi", REFUSED, now=T0 + 601)
    assert text and "GUEST" in text and "refused" in text
    assert any(r.levelno == logging.ERROR and r.getMessage().startswith("ALARM:") for r in caplog.records)
    assert psh.note_turn("pi", REFUSED, now=T0 + 900) is None                    # once per outage...
    assert psh.note_turn("pi", REFUSED, now=T0 + 601 + 3600) is not None         # ...re-raised hourly
    snap = psh.snapshot(now=T0 + 700)
    assert snap["alarm"] is True and snap["panels"][0]["state"] == "refused" and "pi" not in str(snap)


def test_the_panels_own_down_time_survives_a_zoe_data_restart(psh):
    # the night window restarts zoe-data: the first turn after it already reports an 8-hour outage, and alarms at once
    assert psh.note_turn("pi", dict(REFUSED, down_s=8 * 3600), now=T0) is not None
    assert "8.0 hours" in psh.brief_line(now=T0)


def test_ready_ends_the_outage_and_the_brief_still_names_it_for_a_day(psh):
    psh.note_turn("pi", REFUSED, now=T0)
    psh.note_turn("pi", REFUSED, now=T0 + 3 * 3600)
    assert psh.note_turn("pi", {"model": "wespeaker_resnet34", "state": "ready"}, now=T0 + 3 * 3600 + 60) is None
    assert psh.snapshot(now=T0 + 4 * 3600) == {"alarm": False, "panels": []}
    line = psh.brief_line(now=T0 + 6 * 3600)
    assert "was down for 3.0 hours" in line and "back now" in line
    assert psh.brief_line(now=T0 + 30 * 3600) == ""                              # older than a day: not news
    importlib.reload(psh)                                                        # a restart: the ended outage comes back from disk
    assert "was down" in psh.brief_line(now=T0 + 6 * 3600)


def test_negative_controls_ready_off_short_blips_and_old_daemons_never_alarm(psh):
    for i in range(20):
        assert psh.note_turn("pi", {"model": "m", "state": "ready"}, now=T0 + i * 600) is None
        assert psh.note_turn("pi2", {"model": "m", "state": "off"}, now=T0 + i * 600) is None
        assert psh.note_turn("pi3", None, now=T0 + i * 600) is None              # an older daemon sends no block: nothing changes
        assert psh.note_turn("pi4", {"state": "bogus"}, now=T0 + i * 600) is None
    assert psh.snapshot(now=T0 + 20 * 600)["panels"] == []
    psh.note_turn("pi", {"model": "m", "state": "loading"}, now=T0)              # a 2-minute load at boot is not an outage
    psh.note_turn("pi", {"model": "m", "state": "ready"}, now=T0 + 120)
    assert psh.brief_line(now=T0 + 200) == "" and psh.snapshot(now=T0 + 200)["alarm"] is False


def test_the_alarm_rings_one_bell_per_admin(psh, monkeypatch):
    rung = []

    async def ring(panel, down_s):
        rung.append((panel, round(down_s)))
        return 1

    monkeypatch.setattr(psh, "_ring_admins", ring)

    async def scenario():
        psh.note_turn("pi", REFUSED, now=T0)
        psh.note_turn("pi", REFUSED, now=T0 + 601)
        psh.note_turn("pi", REFUSED, now=T0 + 601 + 3600)                        # re-logged, but no second bell
        await asyncio.sleep(0)

    asyncio.run(scenario())
    assert rung == [("pi", 601)]


def test_both_turn_routes_note_the_state_and_only_for_device_callers():
    import inspect

    from routers import voice_tts

    for fn in (voice_tts.voice_turn, voice_tts.voice_turn_stream):
        assert "_note_panel_speaker_state(payload, caller, panel_id)" in inspect.getsource(fn)
    seen = []
    import panel_speaker_health as live

    orig = live.note_turn
    try:
        live.note_turn = lambda panel, block, **k: seen.append((panel, block))
        voice_tts._note_panel_speaker_state({"speaker_model_state": REFUSED}, {"source": "session"}, "pi")
        assert seen == []                                                        # a browser cannot report a panel's model
        voice_tts._note_panel_speaker_state({"speaker_model_state": REFUSED}, {"source": "device"}, "pi")
        assert seen == [("pi", REFUSED)]
    finally:
        live.note_turn = orig
