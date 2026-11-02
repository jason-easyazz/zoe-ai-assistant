"""A no-wake-word follow-up / conversation window turn carries its OWN speaker verdict (owner report 2026-11-02).

The panel's follow-up window keeps listening after a reply with no wake word. A television in the room was heard in that
window and answered as the member, because (a) the window turn cleared the same bar as a wake turn (FAR 1 %), so a few
background clips scored over it, and (b) a clip the gate did not confirm still ran the brain as a guest turn, which kept the
chain of follow-up windows alive. Now:

  * a window turn must clear the stricter ``ZOE_SPEAKER_WINDOW_TIER`` bar (default FAR 0.1 %);
  * a window turn the speaker gate RAN on and did not confirm is dropped as ``not_addressed`` (no reply, no brain, no
    memory) - the daemon closes the follow-up chain on a ``skipped`` frame;
  * the voice session never upgrades a non-matching utterance (it is closed by it instead);
  * a wake turn is unchanged; so is a window turn with no verdict at all (gate off / shadow).

Driven through the REAL voice_turn_stream with the real claim gate (only consent, STT, TTS and the brain are faked).
NEGATIVE CONTROLS at the bottom put each guard back to its old behaviour and the matching assertion goes red.
Synthetic ids and phrases only.
"""
from __future__ import annotations

import base64

import pytest

pytestmark = pytest.mark.ci_safe

from fastapi.testclient import TestClient

import speaker_models
import voice_session
import voice_utterance_shape as shape
import test_voice_stream_speaker_verdict as base

MEMBER = base.MEMBER
RESNET = speaker_models.RESNET34_MODEL_ID


def _block(score, speech_s=3.0, member=MEMBER, verified=None):
    return {"verified": verified, "member": member, "score": score, "model_id": RESNET, "speech_s": speech_s}


def _run(monkeypatch, *, window="", speaker=None, text="tell me about the cardiologist", raw=None):
    import routers.voice_tts as vt

    real_claim = vt._accepted_voice_claim
    seen: list = []
    app = base._app(monkeypatch, seen, claim_ok=True, filler=False)
    monkeypatch.setattr(vt, "_accepted_voice_claim", real_claim)       # the REAL threshold gate

    async def consented(_uid, _model=None):
        return True

    async def stt(_path, **_kw):
        # The real _transcribe_audio records the transcript as heard BEFORE the wake strip; ``raw`` stands in for that.
        vt._stt_raw_var.set({"raw": raw} if raw is not None else {})
        return text

    monkeypatch.setattr(vt, "_voice_claim_consented", consented)
    monkeypatch.setattr(vt, "_transcribe_audio", stt)
    payload = {"audio_base64": base64.b64encode(b"\x00\x01" * 400).decode(), "panel_id": "hall-panel"}
    if window:
        payload["window"] = window
    if speaker is not None:
        payload["speaker"] = speaker
    with TestClient(app) as client:
        r = client.post("/api/voice/turn_stream", json=payload)
        assert r.status_code == 200, r.text
        frames = [ln for ln in r.content.split(b"\n") if ln]
    turns = [x for x in seen if isinstance(x, dict)]
    return turns, frames


def _skipped(frames):
    return any(b'"skipped"' in f for f in frames)


# -- the bar --------------------------------------------------------------------------------------------------------

def test_window_bar_is_stricter_than_the_wake_bar_and_wake_is_unchanged():
    wake_long = speaker_models.claim_threshold(RESNET, 3.0)
    assert speaker_models.claim_threshold(RESNET, 3.0, "") == wake_long
    assert speaker_models.claim_threshold(RESNET, 3.0, "followup") > wake_long
    assert speaker_models.claim_threshold(RESNET, 3.0, "conversation") > wake_long
    assert speaker_models.claim_threshold(RESNET, 1.0, "followup") > speaker_models.claim_threshold(RESNET, 1.0)


@pytest.mark.parametrize("env", ["ZOE_SPEAKER_RESNET34_THRESHOLD_LONG", "ZOE_SPEAKER_RESNET34_THRESHOLD_SHORT"])
def test_a_wake_bar_override_does_not_erase_the_stricter_window_bar(monkeypatch, env):
    """Cross-review: an override of the wake bar was returned for EVERY tier, so the window bar collapsed to it (0.265 let a 0.30 pass)."""
    speech_s = 3.0 if env.endswith("LONG") else 1.0
    group = "long" if env.endswith("LONG") else "short"
    monkeypatch.setenv(env, "0.265")
    table = speaker_models.RESNET34_THRESHOLDS[speaker_models.window_tier()][group]
    assert speaker_models.claim_threshold(RESNET, speech_s, "") == 0.265                # the wake bar IS the override
    assert speaker_models.claim_threshold(RESNET, speech_s, "followup") == max(0.265, table) > 0.265
    assert speaker_models.claim_threshold(RESNET, speech_s, "conversation") == max(0.265, table)
    monkeypatch.setenv(env, "0.95")                                                      # a STRICTER wake bar still wins
    assert speaker_models.claim_threshold(RESNET, speech_s, "followup") == 0.95


def test_window_tier_env_can_loosen_to_the_wake_bar(monkeypatch):
    monkeypatch.setenv("ZOE_SPEAKER_WINDOW_TIER", "far1")
    assert speaker_models.claim_threshold(RESNET, 3.0, "followup") == speaker_models.claim_threshold(RESNET, 3.0)


# -- end to end through voice_turn_stream -------------------------------------------------------------------------------

def test_followup_with_a_matching_voice_is_the_member(monkeypatch):
    turns, frames = _run(monkeypatch, window="followup", speaker=_block(0.55))
    assert len(turns) == 1 and turns[0]["user_id"] == MEMBER and turns[0]["verdict"] is True, (turns, frames)
    assert not _skipped(frames)


def test_followup_with_a_low_match_is_ignored_not_answered(monkeypatch):
    # 0.30 clears the wake bar for a 3 s clip (long 0.265) but NOT the window bar (0.351): the television case.
    assert 0.30 >= speaker_models.claim_threshold(RESNET, 3.0)
    turns, frames = _run(monkeypatch, window="followup", speaker=_block(0.30))
    assert turns == [], "the brain ran for a window utterance that did not match the member"
    assert _skipped(frames), frames


def test_followup_the_daemon_scored_and_nobody_matched_is_ignored(monkeypatch):
    turns, frames = _run(monkeypatch, window="followup", speaker={"verified": False, "member": None, "score": None})
    assert turns == [] and _skipped(frames), (turns, frames)


def test_conversation_window_is_gated_the_same_way(monkeypatch):
    turns, frames = _run(monkeypatch, window="conversation", speaker=_block(0.30))
    assert turns == [] and _skipped(frames)


def test_wake_turn_with_the_same_low_score_is_unchanged_still_the_member(monkeypatch):
    turns, frames = _run(monkeypatch, window="", speaker=_block(0.30))
    assert len(turns) == 1 and turns[0]["user_id"] == MEMBER and turns[0]["verdict"] is True, (turns, frames)
    assert not _skipped(frames)


def test_followup_with_no_verdict_at_all_is_unchanged(monkeypatch):
    """Gate off / shadow mode: nothing to judge, today's behaviour (the turn is not the member either way - it is per utterance)."""
    turns, frames = _run(monkeypatch, window="followup", speaker=None)
    assert len(turns) == 1 and turns[0]["verdict"] is None and not _skipped(frames), (turns, frames)


def test_a_window_utterance_that_names_zoe_is_answered_as_a_guest_never_the_member(monkeypatch):
    turns, frames = _run(monkeypatch, window="followup", speaker=_block(0.30), text="Zoe tell me about the cardiologist")
    assert len(turns) == 1 and not _skipped(frames), (turns, frames)
    assert turns[0]["verdict"] is False and turns[0]["user_id"] != MEMBER, turns[0]
    assert turns[0]["allow_sensitive"] is False, "a non-matching voice must not read the member's sensitive rows"


def test_a_stripped_call_by_name_is_still_a_call_by_name(monkeypatch):
    """Cross-review: Moonshine's wake strip turns "Zoe, what time is it" into "what time is it", so the name check on the stripped text
    dropped an explicit call. The RAW transcript carries the name."""
    turns, frames = _run(monkeypatch, window="followup", speaker=_block(0.30), text="tell me about the cardiologist", raw="Zoe, tell me about the cardiologist")
    assert len(turns) == 1 and not _skipped(frames), (turns, frames)
    assert turns[0]["verdict"] is False and turns[0]["user_id"] != MEMBER, turns[0]


def test_negative_control_without_the_raw_name_the_stripped_call_is_dropped(monkeypatch):
    turns, frames = _run(monkeypatch, window="followup", speaker=_block(0.30), text="tell me about the cardiologist", raw="tell me about the cardiologist")
    assert turns == [] and _skipped(frames)


def test_a_skipped_window_turn_is_still_recorded_as_someone_in_the_room(monkeypatch):
    """Cross-review: dropping the turn bypassed voice_command's crowd note, so another person speaking into a follow-up stayed invisible
    to day_review_raise.others_present."""
    import day_review_raise

    notes = []
    monkeypatch.setattr(day_review_raise, "note_voice_turn", lambda *a, **k: notes.append((a, k)))
    turns, frames = _run(monkeypatch, window="followup", speaker=_block(0.30))
    assert turns == [] and _skipped(frames)                                   # still no brain, no memory
    (a, k), = notes
    assert a == ("", "hall-panel") and k == {"scope_user": None, "verdict": False, "claimed": None}


def test_a_window_turn_that_is_not_skipped_makes_no_extra_crowd_note(monkeypatch):
    import day_review_raise

    notes = []
    monkeypatch.setattr(day_review_raise, "note_voice_turn", lambda *a, **k: notes.append((a, k)))
    turns, frames = _run(monkeypatch, window="followup", speaker=_block(0.99))
    assert len(turns) == 1 and not _skipped(frames)
    assert all(a[0] != "" for a, _k in notes)                                  # voice_command's own note only (it has a session id)


def test_the_voice_session_never_upgrades_a_non_matching_utterance_and_is_closed_by_it(monkeypatch):
    monkeypatch.setenv("ZOE_VOICE_SESSION", "1")
    voice_session.reset()
    # the member opened a session on this panel with a verified wake turn
    voice_session.observe_turn(panel_id="hall-panel", caller_source="device", verdict=True, claimed=MEMBER, pin_user=None,
                               model_ok=True, model_reason="resnet34", text="")
    assert voice_session.active_member("hall-panel") == MEMBER
    turns, frames = _run(monkeypatch, window="followup", speaker=_block(0.30))
    assert turns == [] and _skipped(frames)
    assert voice_session.active_member("hall-panel") is None, "the open session survived a non-matching voice"
    voice_session.reset()


# -- the pure frame --------------------------------------------------------------------------------------------------

def test_window_speaker_frame_cases():
    f = shape.window_speaker_frame
    assert f({"window": "followup"}, "what time is it", False)["skipped"] == "not_addressed"
    assert f({"window": "followup"}, "what time is it", False)["conversation_end"] is True   # the daemon stops listening
    assert f({"window": "followup"}, "what time is it", True) is None
    assert f({"window": "followup"}, "what time is it", None) is None
    assert f({}, "what time is it", False) is None              # a wake turn
    assert f({"window": "followup"}, "hey zoe what time is it", False) is None


# -- negative controls ------------------------------------------------------------------------------------------------

def test_negative_control_wake_bar_for_windows_lets_the_low_score_in(monkeypatch):
    """Window tier back at the wake bar: the 0.30 clip is accepted as the member (the pre-fix behaviour) - the bar test goes red."""
    monkeypatch.setenv("ZOE_SPEAKER_WINDOW_TIER", "far1")
    # the stub-free path: with the bar loosened the clip is a CONFIRMED member and the brain runs as the member
    turns, _frames = _run(monkeypatch, window="followup", speaker=_block(0.30))
    assert len(turns) == 1 and turns[0]["user_id"] == MEMBER and turns[0]["verdict"] is True


def test_negative_control_without_the_skip_the_unconfirmed_window_turn_runs_the_brain(monkeypatch):
    monkeypatch.setattr(shape, "window_speaker_frame", lambda *_a, **_k: None)
    turns, frames = _run(monkeypatch, window="followup", speaker=_block(0.30))
    assert len(turns) == 1 and turns[0]["verdict"] is False and not _skipped(frames), (turns, frames)


def test_an_error_while_judging_a_window_turn_fails_closed(monkeypatch):
    """The claim backend blowing up must not let an unjudged window utterance run the brain."""
    import routers.voice_tts as vt

    real_app = base._app

    def boom_app(mp, seen, **kw):
        app = real_app(mp, seen, **kw)

        async def boom(_p, _c):
            raise RuntimeError("consent backend down")

        mp.setattr(vt, "_accepted_voice_claim", boom)
        return app

    monkeypatch.setattr(base, "_app", boom_app)
    # _run restores the real claim after _app, so patch the gate itself to raise instead
    monkeypatch.setattr(vt, "_speaker_verdict", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    turns, frames = _run(monkeypatch, window="followup", speaker=_block(0.9))
    assert turns == [] and _skipped(frames), (turns, frames)


# -- the blocking /turn (the stream's fallback) applies the same rule (greptile P1, #2242) -----------------------------

def _run_turn(monkeypatch, *, window="", speaker=None, text="tell me about the cardiologist"):
    import routers.voice_tts as vt

    real_claim = vt._accepted_voice_claim
    seen: list = []
    app = base._app(monkeypatch, seen, claim_ok=True, filler=False)
    monkeypatch.setattr(vt, "_accepted_voice_claim", real_claim)

    async def consented(_uid, _model=None):
        return True

    async def stt(_path, **_kw):
        vt._stt_raw_var.set({})
        return text

    monkeypatch.setattr(vt, "_voice_claim_consented", consented)
    monkeypatch.setattr(vt, "_transcribe_audio", stt)
    payload = {"audio_base64": base64.b64encode(b"\x00\x01" * 400).decode(), "panel_id": "hall-panel"}
    if window:
        payload["window"] = window
    if speaker is not None:
        payload["speaker"] = speaker
    with TestClient(app) as client:
        r = client.post("/api/voice/turn", json=payload)
        assert r.status_code == 200, r.text
        body = r.json()
    return [x for x in seen if isinstance(x, dict)], body


def test_blocking_turn_drops_a_low_scoring_window_clip(monkeypatch):
    # 0.30 clears the wake bar for a 3 s clip but not the window bar: the television case, arriving via the fallback.
    assert 0.30 >= speaker_models.claim_threshold(RESNET, 3.0)
    turns, body = _run_turn(monkeypatch, window="followup", speaker=_block(0.30))
    assert turns == [], "the brain ran for a window utterance that did not match the member"
    assert body["skipped"] == "not_addressed" and body["text"] == "" and not body.get("audio_base64"), body


def test_blocking_turn_with_a_matching_voice_or_a_wake_turn_is_unchanged(monkeypatch):
    turns, body = _run_turn(monkeypatch, window="followup", speaker=_block(0.55))
    assert len(turns) == 1 and turns[0]["user_id"] == MEMBER and "skipped" not in body, (turns, body)
    turns, body = _run_turn(monkeypatch, window="", speaker=_block(0.30))
    assert len(turns) == 1 and turns[0]["user_id"] == MEMBER and "skipped" not in body, (turns, body)


def test_blocking_turn_window_error_fails_closed_and_naming_zoe_stays_answerable(monkeypatch):
    import routers.voice_tts as vt

    turns, body = _run_turn(monkeypatch, window="followup", speaker=_block(0.30), text="Zoe tell me about the cardiologist")
    assert len(turns) == 1 and turns[0]["verdict"] is False and "skipped" not in body      # a guest turn, never the member
    monkeypatch.setattr(vt, "_speaker_verdict", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    turns, body = _run_turn(monkeypatch, window="followup", speaker=_block(0.9))
    assert turns == [] and body["skipped"] == "not_addressed", (turns, body)


def test_negative_control_without_the_blocking_check_the_low_clip_runs_the_brain(monkeypatch):
    monkeypatch.setattr(shape, "window_speaker_frame", lambda *_a, **_k: None)
    turns, body = _run_turn(monkeypatch, window="followup", speaker=_block(0.30))
    assert len(turns) == 1 and "skipped" not in body, (turns, body)
