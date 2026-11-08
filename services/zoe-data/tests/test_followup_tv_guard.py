"""Follow-up listening vs a television: a tunable window bar + a flag-dark media-playing skip.

Live 2026-11-05: television dialogue cleared the follow-up (window) speaker bar by a hair (0.356 vs 0.351 long) while the owner's
lowest real follow-up score was ~0.43. ``ZOE_SPEAKER_WINDOW_THRESHOLD_LONG/_SHORT`` set the window bar numerically (unset = the
unchanged table). ``ZOE_FOLLOWUP_SKIP_WHILE_MEDIA`` (default OFF) drops a no-wake-word window turn while media plays unless it
names Zoe. Synthetic scores only (ci_safe)."""
import pytest

import speaker_models
import voice_utterance_shape as shape

pytestmark = pytest.mark.ci_safe

RESNET = speaker_models.RESNET34_MODEL_ID
LONG_ENV, SHORT_ENV = "ZOE_SPEAKER_WINDOW_THRESHOLD_LONG", "ZOE_SPEAKER_WINDOW_THRESHOLD_SHORT"


def test_unset_window_bar_is_the_unchanged_table(monkeypatch):
    monkeypatch.delenv(LONG_ENV, raising=False)
    monkeypatch.delenv(SHORT_ENV, raising=False)
    assert speaker_models.claim_threshold(RESNET, 3.0, "followup") == 0.351
    assert speaker_models.claim_threshold(RESNET, 1.0, "followup") == 0.309


def test_window_override_raises_the_window_bar_and_rejects_the_tv_score(monkeypatch):
    monkeypatch.setenv(LONG_ENV, "0.39")
    bar = speaker_models.claim_threshold(RESNET, 3.0, "followup")
    assert bar == 0.39
    assert not (0.356 >= bar)          # the television's score no longer clears it
    assert 0.425 >= bar                # a real, low owner score still does
    assert speaker_models.claim_threshold(RESNET, 3.0, "conversation") == 0.39


def test_window_override_never_touches_a_wake_turn(monkeypatch):
    # negative control: a wake turn keeps the wake bar
    monkeypatch.setenv(LONG_ENV, "0.39")
    assert speaker_models.claim_threshold(RESNET, 3.0, "") == speaker_models.resnet34_threshold("long")


def test_window_override_cannot_loosen_below_the_wake_bar_and_garbage_is_ignored(monkeypatch):
    monkeypatch.setenv(LONG_ENV, "0.1")
    assert speaker_models.claim_threshold(RESNET, 3.0, "followup") == speaker_models.resnet34_threshold("long")
    monkeypatch.setenv(LONG_ENV, "garbage")
    assert speaker_models.claim_threshold(RESNET, 3.0, "followup") == 0.351


def test_media_flag_default_off(monkeypatch):
    monkeypatch.delenv("ZOE_FOLLOWUP_SKIP_WHILE_MEDIA", raising=False)
    assert shape.skip_while_media_enabled() is False
    monkeypatch.setenv("ZOE_FOLLOWUP_SKIP_WHILE_MEDIA", "1")
    assert shape.skip_while_media_enabled() is True


def test_media_playing_drops_a_window_turn_but_not_a_wake_turn_or_a_named_one():
    win = {"window": "followup"}
    frame = shape.media_playing_frame(win, "and then he said we should go", True)
    assert frame and frame["skipped"] == "not_addressed" and frame["reason"] == "media_playing" and frame["done"] is True
    assert shape.media_playing_frame(win, "and then he said we should go", False) is None   # nothing playing
    assert shape.media_playing_frame({}, "and then he said we should go", True) is None      # a wake turn
    assert shape.media_playing_frame(win, "zoe what time is it", True) is None               # names Zoe
    assert shape.media_playing_frame(win, "whatever", True, named_zoe=True) is None          # raw transcript named Zoe
