"""PANEL ANCHORS (``omi_panel_anchor.py`` + the ``omi_voices`` seams): a pendant turn is Zoe ONLY if its sentences match something she said, aligned in time; a turn that
names her is never her; a turn that matches a panel utterance inherits the panel's PER-CLIP speaker verdict above the voice match; the night clock offset is
estimated, not assumed. Synthetic text and vectors throughout - no household data."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import asyncio
import datetime as dt
import json
import sqlite3
from types import SimpleNamespace

import numpy as np

import omi_panel_anchor as pa
import omi_repass as rp
import omi_repass_who as who
import omi_voices as ov

RULE = pa.AlignRule()          # the measured defaults, not whatever the environment says
REC = 1_000_000.0               # the recording's start, epoch s
OFFSET = 88.0                   # panel time = pendant time + 88 s


def zoe(t0, text, dur=6.0):
    return pa.Said("zoe", t0, t0 + dur, text)


def person(t0, text, speaker="member", key="p:G", dur=3.0):
    return pa.Said("person", t0, t0 + dur, text, speaker=speaker, person=key)


def seg(a_s, b_s, text):
    return {"start_ms": int(a_s * 1000), "end_ms": int(b_s * 1000), "text": text}


GREETING = "Good evening. How can I help you with the shopping list tonight?"
MIXED = GREETING + " Well, Zoe, I've got some issues with the car."


def ev(*said, offset=OFFSET):
    return pa.Evidence(said=list(said), offset_s=offset, offset_clips=3, covered=True)


# ── Zoe: what she SAID, sentence by sentence ────────────────────────────────────────────────────

def test_mixed_turn_only_zoes_own_sentences_are_zoe_and_the_line_naming_her_is_not():
    spans = pa.zoe_spans([seg(10, 20, MIXED)], ev(zoe(REC + 10 + OFFSET, GREETING)), REC, RULE)
    assert spans and all(b <= 20000 for _, b in spans)
    assert max(b for _, b in spans) < 20000 - 1                       # the tail ("Well, Zoe, ...") is outside every span
    assert all(a >= 10000 for a, _ in spans)


def test_negative_controls_nothing_said_other_words_naming_her_too_short_or_out_of_time_are_not_zoe():
    s = [seg(10, 14, GREETING)]
    at = REC + 10 + OFFSET
    assert pa.zoe_spans(s, ev(), REC, RULE) == []                                                    # she said nothing
    assert pa.zoe_spans(s, ev(zoe(at, "The weather tomorrow looks warm with a light breeze coming in")), REC, RULE) == []   # other words
    assert pa.zoe_spans([seg(10, 14, "Zoe, how can I help you with the shopping list tonight?")], ev(zoe(at, GREETING)), REC, RULE) == []   # names her
    assert pa.zoe_spans([seg(10, 11, "Good evening")], ev(zoe(at, "Good evening")), REC, RULE) == []  # under 6 words never decides
    assert pa.zoe_spans(s, ev(zoe(at + 300, GREETING)), REC, RULE) == []                              # same words, five minutes away
    assert pa.zoe_spans(s, ev(zoe(at, GREETING)), REC, RULE) != []                                     # the control: the matching case does match


def test_the_night_offset_moves_the_window_and_the_jitter_bounds_it():
    s = [seg(10, 14, GREETING)]
    say = zoe(REC + 10 + OFFSET, GREETING)
    assert pa.zoe_spans(s, ev(say, offset=OFFSET), REC, RULE) != []
    assert pa.zoe_spans(s, ev(say, offset=OFFSET - 200), REC, RULE) == []                              # a wrong offset finds nothing
    assert pa.zoe_spans(s, ev(say, offset=OFFSET + 25), REC, RULE) != []                               # inside the +-30 s jitter
    assert pa.zoe_spans(s, ev(say, offset=OFFSET + 90), REC, RULE) == []


def test_numbers_and_punctuation_normalise_and_cover_tolerates_stt_noise():
    assert pa.words_of("It's Twenty-One, ok.") == ["it's", "21", "ok"]
    assert pa.words_of("twenty twenty one") == ["2021"]
    share, n = pa.cover("add 21 apples to the list please", "Add twenty one apples to the list, please.")
    assert share == 1.0 and n == 7
    assert pa.cover("the car is broken again", "the cat is broken")[0] == pytest.approx(0.6)


# ── known speakers: the per-clip verdict above the voice match ─────────────────────────────────

def test_a_matching_panel_utterance_anchors_its_speaker_unknown_verdicts_and_poor_matches_do_not():
    t = REC + 30 + OFFSET
    said = [person(t, "I think the car needs a new battery before the trip")]
    s = [seg(30, 33, "I think the car needs a new battery before the trip")]
    assert pa.person_anchors(s, ev(*said), REC, RULE) == [{"start_ms": 30000, "end_ms": 33000, "key": "p:G", "speaker": "member"}]
    assert pa.person_anchors(s, ev(person(t, "I think the car needs a new battery before the trip", speaker="unknown", key=None)), REC, RULE) == []
    assert pa.person_anchors([seg(30, 33, "completely different words about gardening today")], ev(*said), REC, RULE) == []      # similarity < 0.6
    assert pa.person_anchors([seg(30, 31, "the car")], ev(*said), REC, RULE) == []                                                  # under 3 words
    assert pa.person_anchors([seg(300, 303, s[0]["text"])], ev(*said), REC, RULE) == []                                              # out of time


def test_the_best_text_match_wins_among_panel_turns_in_range():
    t = REC + 30 + OFFSET
    a, b = person(t, "put milk and eggs on the list", key="p:A"), person(t + 5, "I think the car needs a new battery", key="p:B")
    out = pa.person_anchors([seg(30, 33, "the car needs a new battery")], ev(a, b), REC, RULE)
    assert [x["key"] for x in out] == ["p:B"]


def test_join_trusts_the_per_clip_verdict_not_the_session():
    turn = SimpleNamespace(recorded_ts=dt.datetime.fromtimestamp(REC + 500), duration_s=3.0, claim_user="owner", clip="/x/a.wav")
    far = SimpleNamespace(recorded_ts=dt.datetime.fromtimestamp(REC + 900), duration_s=3.0, claim_user=None, clip=None)
    texts = [pa.Said("person", REC + 503, REC + 503, "the first clip"), pa.Said("person", REC + 9000, REC + 9000, "no panel turn near this one")]
    out = pa.join_verdicts(texts, [turn, far], lambda t: ("member", "you") if t.claim_user == "owner" else ("unknown", None))
    assert len(out) == 1 and (out[0].speaker, out[0].person, out[0].clip) == ("member", "you", "/x/a.wav")      # the guest's SESSION text carries the OWNER's clip verdict
    assert out[0].t0 == turn.recorded_ts.timestamp()                                                           # and the panel's own clock


# ── the night offset ───────────────────────────────────────────────────────────────────────────

def test_offset_is_a_robust_median_and_refuses_when_the_clips_disagree():
    assert pa.estimate_offset([86, 88, 90, 91], RULE) == (89.0, 4)
    assert pa.estimate_offset([86, 88, 90, 400], RULE) == (88.0, 3)                         # one wild clip is dropped
    assert pa.estimate_offset([88], RULE) is None                                           # one clip proves nothing
    assert pa.estimate_offset([10, 80, 160, 300], RULE) is None                             # no consensus: no anchors rather than wrong ones


def _speechlike(n, sr, seed):
    rng = np.random.default_rng(seed)
    t = np.arange(n) / sr
    env = np.abs(np.sin(2 * np.pi * rng.uniform(1.5, 3.5) * t + rng.uniform(0, 6))) ** 2 + 0.2 * np.abs(np.sin(2 * np.pi * 0.37 * t + seed))
    return rng.standard_normal(n) * env


def test_log_mel_correlation_finds_a_clip_in_the_pendant_audio_and_rejects_one_that_is_not_there():
    sr = 16000
    pend = 0.05 * np.random.default_rng(3).standard_normal(sr * 150)
    clip = _speechlike(sr * 6, sr, 11)
    at = 77.0
    pend[int(at * sr):int(at * sr) + len(clip)] += clip
    got = pa.correlate_shift(pend, clip, sr, predicted_s=60.0, search_s=40.0)
    assert got is not None and abs(got[0] - at) < 0.05
    other = _speechlike(sr * 6, sr, 99)
    assert pa.correlate_shift(pend, other, sr, predicted_s=60.0, search_s=40.0) is None   # the control: a clip that is not in the audio


# ── the readers ────────────────────────────────────────────────────────────────────────────────

def _flue(tmp_path, events):
    p = tmp_path / "zoe-brain.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE flue_conversation_stream_batches (path TEXT, seq INTEGER, data TEXT)")
    for i, e in enumerate(events):
        con.execute("INSERT INTO flue_conversation_stream_batches VALUES ('p', ?, ?)", (i, json.dumps([e])))
    con.commit()
    con.close()
    return str(p)


def _iso(t):
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).isoformat()


def test_flue_reader_joins_deltas_into_replies_and_reads_user_turns(tmp_path):
    ev_ = [{"type": "user_message", "timestamp": _iso(REC + 5), "content": "put milk on the list"},
           {"type": "assistant_text_delta", "timestamp": _iso(REC + 7), "messageId": "m", "blockId": "b", "sequence": 1, "delta": "Done, "},
           {"type": "assistant_text_delta", "timestamp": _iso(REC + 8), "messageId": "m", "blockId": "b", "sequence": 2, "delta": "milk is on."},
           {"type": "assistant_text_delta", "timestamp": _iso(REC + 9999), "messageId": "m2", "blockId": "b", "sequence": 1, "delta": "outside the window"}]
    got = pa.flue_said(REC, REC + 60, db_path=_flue(tmp_path, ev_))
    assert [(s.kind, s.text) for s in got] == [("person", "put milk on the list"), ("zoe", "Done, milk is on.")]
    assert pa.flue_said(REC, REC + 60, db_path=str(tmp_path / "missing.db")) is None            # an unreadable store is "no evidence", not []


def test_flue_reader_rebuilds_a_chunked_batch(tmp_path):
    evs = [{"type": "assistant_text_delta", "timestamp": _iso(REC + 7), "messageId": "m", "blockId": "b", "sequence": 1, "delta": "Chunked "},
           {"type": "assistant_text_delta", "timestamp": _iso(REC + 8), "messageId": "m", "blockId": "b", "sequence": 2, "delta": "reply."}]
    blob = json.dumps(evs)
    p = tmp_path / "zoe-brain.db"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE flue_conversation_stream_batches (path TEXT, seq INTEGER, data TEXT)")
    con.execute("CREATE TABLE flue_conversation_stream_batch_chunks (path TEXT, seq INTEGER, chunk_index INTEGER, data TEXT)")
    con.execute("INSERT INTO flue_conversation_stream_batches VALUES ('p', 1, ?)", (json.dumps({"$flueChunkCount": 2}),))
    half = len(blob) // 2
    con.execute("INSERT INTO flue_conversation_stream_batch_chunks VALUES ('p', 1, 1, ?)", (blob[half:],))      # out of order on purpose
    con.execute("INSERT INTO flue_conversation_stream_batch_chunks VALUES ('p', 1, 0, ?)", (blob[:half],))
    con.commit()
    con.close()
    got = pa.flue_said(REC, REC + 60, db_path=str(p))
    assert [(s.kind, s.text) for s in got] == [("zoe", "Chunked reply.")]


# ── the seams in omi_voices ────────────────────────────────────────────────────────────────────

class FakeDb:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, sql, params=()):
        rows = next(v for k, v in self.rows.items() if k in sql)

        class Cur:
            async def fetchall(self_):
                return rows
        return Cur()


def _db(quality="device"):
    started = dt.datetime.fromtimestamp(REC, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return FakeDb({"FROM omi_recordings WHERE": [(started, 120.0, quality)], "FROM omi_segments": []})


def _turns(claim="owner", score=0.9):
    return [SimpleNamespace(recorded_ts=dt.datetime.fromtimestamp(REC + 30 + OFFSET), duration_s=3.0, claim_user=claim, claim_score=score, clip=None)]


@pytest.fixture(autouse=True)
def _server_accepts_claims_at_or_above_half(monkeypatch):
    """The server's acceptance (routers/voice_tts._accepted_voice_claim: threshold + consent) stands in as 'score >= 0.5'."""
    from routers import voice_tts as vt

    async def accepted(payload, caller):
        return payload["voice_user_id"] if float(payload["voice_score"]) >= 0.5 and caller.get("source") == "device" else None
    monkeypatch.setattr(vt, "_accepted_voice_claim", accepted)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_the_seams_are_off_by_default_and_the_night_mode_leaves_the_first_pass_alone(monkeypatch):
    segs = [seg(10, 14, GREETING)]
    kw = dict(segs=segs, said=[zoe(REC + 10 + OFFSET, GREETING)], turns=_turns(), shifts=[88.0, 88.5])
    monkeypatch.delenv("ZOE_OMI_PANEL_ANCHORS", raising=False)
    assert run(ov.zoe_spoken_spans(_db(), "owner", "r", **kw)) == []                       # off: nothing reads the panel
    monkeypatch.setenv("ZOE_OMI_PANEL_ANCHORS", "night")
    assert run(ov.zoe_spoken_spans(_db(), "owner", "r", **kw)) == []                       # night mode: the first pass (night=False) does not
    assert run(ov.zoe_spoken_spans(_db(), "owner", "r", night=True, **kw)) != []
    monkeypatch.setenv("ZOE_OMI_PANEL_ANCHORS", "on")
    assert run(ov.zoe_spoken_spans(_db(), "owner", "r", **kw)) != []


def test_no_anchors_without_a_trustworthy_clock_or_an_offset(monkeypatch):
    monkeypatch.setenv("ZOE_OMI_PANEL_ANCHORS", "on")
    base = dict(segs=[seg(10, 14, GREETING)], said=[zoe(REC + 10 + OFFSET, GREETING)], turns=_turns())
    assert run(ov.panel_spans(_db("estimated"), "owner", "r", shifts=[88.0, 88.5], **base)) == ([], [], False)   # an estimated clock cannot be aligned
    assert run(ov.panel_spans(_db(), "owner", "r", shifts=[88.0], **base)) == ([], [], False)                    # one clip: no night offset
    assert run(ov.panel_spans(_db(), "owner", "r", shifts=[], **base)) == ([], [], False)
    assert run(ov.panel_spans(_db(), "owner", "r", shifts=[88.0, 88.5], **{**base, "turns": []})) == ([], [], False)   # panel log unreadable


def test_the_greeting_prefix_joins_the_first_reply_and_a_guest_session_keeps_the_owners_clip_verdict(monkeypatch):
    monkeypatch.setenv("ZOE_OMI_PANEL_ANCHORS", "on")
    first = zoe(REC + 10 + OFFSET, "How can I help you with the shopping list tonight?")
    segs = [seg(10, 14, GREETING), seg(40, 44, "I think the car needs a new battery before the trip")]
    said = [first, pa.Said("person", REC + 31 + OFFSET, REC + 31 + OFFSET, "I think the car needs a new battery before the trip")]
    spans, anchors, covered = run(ov.panel_spans(_db(), "owner", "r", segs=segs, said=said, turns=_turns("owner"), shifts=[88.0, 88.4], greeting="Good evening."))
    assert covered and spans and spans[0][0] >= 10000 and spans[-1][1] <= 14000
    assert [(a["key"], a["start_ms"]) for a in anchors] == [("you", 40000)]


def test_a_claim_the_server_rejects_gives_no_you_anchor(monkeypatch):
    """The daemon logs its best match even when its own decision was accept=False: a rejected claim must leave the speaker UNKNOWN."""
    monkeypatch.setenv("ZOE_OMI_PANEL_ANCHORS", "on")
    first = zoe(REC + 10 + OFFSET, "How can I help you with the shopping list tonight?")
    segs = [seg(10, 14, GREETING), seg(40, 44, "I think the car needs a new battery before the trip")]
    said = [first, pa.Said("person", REC + 31 + OFFSET, REC + 31 + OFFSET, "I think the car needs a new battery before the trip")]
    for turns, want in ((_turns("owner", 0.9), [("you", 40000)]), (_turns("owner", 0.1), [])):        # control: accepted -> anchor; rejected -> none
        spans, anchors, covered = run(ov.panel_spans(_db(), "owner", "r", segs=segs, said=said, turns=turns, shifts=[88.0, 88.4], greeting="Good evening."))
        assert [(a["key"], a["start_ms"]) for a in anchors] == want
    from routers import voice_tts as vt

    async def boom(payload, caller):
        raise RuntimeError("gate unavailable")
    monkeypatch.setattr(vt, "_accepted_voice_claim", boom)
    _s, anchors, _c = run(ov.panel_spans(_db(), "owner", "r", segs=segs, said=said, turns=_turns("owner", 0.9), shifts=[88.0, 88.4], greeting="Good evening."))
    assert anchors == []                                                                                 # acceptance not establishable -> unknown


# ── build_lines: the panel's record above the voice match; the print never makes Zoe ──────────────

def vec(*xs):
    return list(np.array(list(xs) + [0.0] * (8 - len(xs)), dtype=float))


A_, B_, Z_ = vec(1), vec(0, 1), vec(0, 0, 1)


def test_an_anchor_beats_the_voice_match_and_zoe_by_log_ignores_her_print():
    refs = who.Refs(you=A_, zoe=Z_, people=[{"person": "G", "v": B_}])
    words = [[" hello", 1.0, 1.4, 0.9], [" there", 1.5, 1.9, 0.9]]
    win = [[0.5, 2.5, A_, 0.2]]                                                              # the voice says the wearer
    assert [ln["key"] for ln in rp.build_lines(words, win, refs)] == ["you"]
    got = rp.build_lines(words, win, refs, anchors=[{"start_ms": 500, "end_ms": 2500, "key": "p:G"}])
    assert [ln["key"] for ln in got] == ["p:G"]                                              # the panel's record wins
    zwin = [[0.5, 2.5, Z_, 0.2]]
    assert [ln["key"] for ln in rp.build_lines(words, zwin, refs)] == ["zoe"]                # no panel log: the print fallback
    assert [ln["key"] for ln in rp.build_lines(words, zwin, refs, zoe_by_log=True)] != ["zoe"]   # the log covers it and says she did not speak
    assert [ln["key"] for ln in rp.build_lines(words, zwin, refs, zoe_by_log=True, zoe_spans=[(500, 2500)])] == ["zoe"]   # ... unless the log says she did


def test_a_line_that_names_zoe_is_never_zoe_even_when_her_print_matches():
    refs = who.Refs(you=A_, zoe=Z_)
    words = [[" Well,", 1.0, 1.3, 0.9], [" Zoe,", 1.4, 1.7, 0.9], [" I've", 1.8, 2.0, 0.9], [" got", 2.1, 2.3, 0.9], [" issues", 2.4, 2.8, 0.9]]
    lines = rp.build_lines(words, [[0.5, 3.0, Z_, 0.2]], refs)
    assert [ln["key"] for ln in lines] == ["?"]
    plain = [[" I've", 1.0, 1.3, 0.9], [" got", 1.4, 1.7, 0.9], [" issues", 1.8, 2.1, 0.9]]
    assert [ln["key"] for ln in rp.build_lines(plain, [[0.5, 3.0, Z_, 0.2]], refs)] == ["zoe"]        # the control: without her name the print still decides
