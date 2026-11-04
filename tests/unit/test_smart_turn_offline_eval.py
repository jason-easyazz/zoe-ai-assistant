"""Metrics of scripts/perf/smart_turn_offline_eval.py on synthetic signals.

The eval's verdict (Smart Turn vs the fixed tail) is only as good as its arithmetic:
where the true end of speech is, what counts as a pause, when each policy closes,
and how a close is scored. Every rule is pinned here on hand-built probability
sequences, plus one run of the REAL daemon ``_Endpointer`` through the scripted-VAD
shim (torch-free, the recipe of test_voice_daemon_dead_time.py) with negative controls.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.ci_safe

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "perf" / "smart_turn_offline_eval.py"


@pytest.fixture(scope="module")
def ev():
    spec = importlib.util.spec_from_file_location("_st_eval_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S, Q, DEEP, AMB = 0.9, 0.02, 0.02, 0.2   # speech, quiet, deep quiet, ambiguous quiet


def test_speech_end_is_end_of_last_voiced_hop(ev):
    probs = [Q, S, S, Q, S, Q, Q]
    assert ev.speech_end_sample(probs, 0.35, hop=512) == 5 * 512
    assert ev.speech_start_sample(probs, 0.35, hop=512) == 512
    assert ev.speech_end_sample([Q, Q], 0.35) is None
    # limit excludes hops beyond the clip (the appended room tone)
    assert ev.speech_end_sample(probs, 0.35, hop=512, limit=4 * 512) == 3 * 512
    # threshold is inclusive
    assert ev.speech_end_sample([0.35], 0.35, hop=512) == 512


def test_internal_pauses_exclude_leading_and_trailing_silence(ev):
    probs = [Q, Q, S, Q, Q, Q, S, S, Q, S, Q, Q]
    assert ev.internal_pauses(probs, 0.35, hop=10) == [(30, 60), (80, 90)]
    assert ev.internal_pauses([Q, S, S, Q], 0.35, hop=10) == []


def test_chunk_fold_matches_vadstream_feed(ev):
    # hop 512, chunk 1280: chunk 0 completes hops 0,1; chunk 1 completes hops 2,3 (end 2048 <= 2560);
    # chunk 2 completes hop 4 (end 2560 <= 3840... and hop 5 ends 3072, hop 6 ends 3584)
    hops = [0.1, 0.7, 0.2, 0.3, 0.9, 0.05, 0.04, 0.01]
    out = ev.chunk_probs_from_hops(hops, 3, chunk=1280, hop=512)
    assert out[0] == 0.7                   # max of hops 0-1
    assert out[1] == pytest.approx(0.9)    # hops 2,3,4 end at 1536, 2048, 2560 -> max 0.9
    assert out[2] == 0.05                  # hops 5,6 end at 3072, 3584 -> max 0.05
    # a chunk that completes no hop answers the last completed hop
    assert ev.chunk_probs_from_hops([0.6], 2, chunk=256, hop=512) == [0.0, 0.6]


def _policy(ev, first=160, thr=0.5, recheck=160, cap=800):
    return ev.TurnPolicy(first, thr, recheck, cap)


def test_smart_turn_closes_on_confident_score(ev):
    probs = [S] * 8 + [Q] * 20
    pts = ev.check_points(probs, 0.35, 160, 160, 800)
    assert pts == [9, 11, 13, 15]          # quiet 160, 320, 480, 640 ms; 800 is the cap
    j, why = ev.simulate_smart_turn(probs, {9: 0.9, 11: 0.9, 13: 0.9, 15: 0.9}, _policy(ev), 0.35)
    assert (j, why) == (9, "model")


def test_smart_turn_holds_low_score_then_caps(ev):
    probs = [S] * 8 + [Q] * 20
    scores = {j: 0.1 for j in (9, 11, 13, 15)}
    j, why = ev.simulate_smart_turn(probs, scores, _policy(ev), 0.35)
    assert (j, why) == (17, "cap")         # 10 quiet chunks = 800 ms
    # a later confident check ends it before the cap
    scores[13] = 0.8
    assert ev.simulate_smart_turn(probs, scores, _policy(ev), 0.35) == (13, "model")


def test_smart_turn_resumed_speech_resets_the_quiet_run(ev):
    probs = [S] * 4 + [Q] * 4 + [S] * 2 + [Q] * 20
    scores = {j: 0.1 for j in range(40)}
    scores[11] = 0.95                      # first check of the SECOND pause
    pol = _policy(ev)
    assert ev.simulate_smart_turn(probs, scores, pol, 0.35) == (11, "model")
    # negative control: threshold above the score -> no model close, cap instead
    assert ev.simulate_smart_turn(probs, scores, _policy(ev, thr=0.99), 0.35)[1] == "cap"


def test_smart_turn_never_closes_before_speech_or_inside_min_guard(ev):
    assert ev.simulate_smart_turn([Q] * 30, {}, _policy(ev), 0.35) == (None, "never")
    probs = [S] + [Q] * 20
    pts = ev.check_points(probs, 0.35, 160, 160, 800, min_chunks=6)
    assert pts and min(pts) >= 6


def test_outcome_scoring(ev):
    pauses = [(1000, 5000)]
    assert ev.outcome(None, 8000, pauses) == {"closed": False}
    cut = ev.outcome(3000, 8000, pauses)
    assert cut["cut"] and cut["cut_in_pause"]
    assert ev.outcome(6000, 8000, pauses)["cut_in_pause"] is False
    late = ev.outcome(8000 + 16 * 300, 8000, pauses)
    assert late["cut"] is False and late["latency_ms"] == pytest.approx(300.0)


def test_summarise_counts_and_percentiles(ev):
    outs = [{"closed": True, "cut": False, "latency_ms": v} for v in (100, 200, 300, 400, 1500)]
    outs += [{"closed": True, "cut": True, "cut_in_pause": False}, {"closed": False}]
    s = ev.summarise(outs, over_wait_ms=1000)
    assert (s["n"], s["cuts"], s["never_closed"], s["over_waits"]) == (7, 1, 1, 1)
    assert s["latency_p50_ms"] == 300 and s["latency_p90_ms"] == 1500
    assert s["latency_mean_ms"] == pytest.approx(500.0)
    assert s["correct_rate"] == pytest.approx(4 / 7, abs=1e-4)
    assert ev.summarise([])["cut_rate"] is None


def test_room_tone_tiles_own_tail_or_flags_zeros(ev):
    audio = np.concatenate([np.full(4000, 9000, np.int16), np.arange(1, 3001, dtype=np.int16)])
    tone, real = ev.tile_room_tone(audio, 4000, 7000, skip=0, min_tone=1600)
    assert real and len(tone) == 7000 and tone[3000] == 1     # wraps to the tail start
    assert not (tone == 9000).any()                          # speech is never looped
    tone, real = ev.tile_room_tone(audio, 6500, 500, skip=0, min_tone=1600)
    assert not real and not tone.any()


def test_mid_boundary_picks_the_pause_nearest_the_middle(ev):
    pauses = [(100, 2000), (4800, 5300), (9000, 9200)]
    assert ev.mid_boundary(pauses, 0, 10000, min_len=300) == 5050
    assert ev.mid_boundary([(10, 50)], 0, 100, min_len=1024) is None


@pytest.fixture(scope="module")
def daemon(ev):
    return ev.load_daemon()


def test_real_endpointer_live_config(ev, daemon):
    live = ev.LIVE_FIXED
    # 10 speech chunks, then deep quiet: the 640 ms deep tail closes on the 8th quiet chunk
    probs = [S] * 10 + [DEEP] * 20
    assert ev.fixed_close(daemon, probs, live) == 17
    # ambiguous quiet never takes the deep exit: 800 ms any-quiet = 10th quiet chunk
    probs = [S] * 10 + [AMB] * 20
    assert ev.fixed_close(daemon, probs, live) == 19
    # negative control: flag off -> deep quiet waits the full 800 ms too
    assert ev.fixed_close(daemon, [S] * 10 + [DEEP] * 20, {"VAD_ENDPOINT_SILENCE_S": 0.8, "ZOE_VAD_TAIL_MS": 0}) == 19
    # a mid-utterance pause shorter than the tail is held
    probs = [S] * 10 + [AMB] * 6 + [S] * 5 + [DEEP] * 20
    assert ev.fixed_close(daemon, probs, live) == 28


def test_real_endpointer_min_guard_counts_the_preroll(ev, daemon):
    # one speech chunk then deep quiet: inside the 0.5 s minimum-recording guard the close
    # waits for frame 7; with a 20-chunk pre-roll already in the recording it does not.
    fast = {"VAD_ENDPOINT_SILENCE_S": 0.8, "ZOE_VAD_TAIL_MS": 80}   # 1-chunk deep tail
    probs = [S] + [DEEP] * 20
    assert ev.fixed_close(daemon, probs, fast, frame_offset=0) == 6     # held to frame 7
    assert ev.fixed_close(daemon, probs, fast, frame_offset=20) == 1    # guard already passed
    probs = [S, DEEP, DEEP, S] + [DEEP] * 20
    # speech resumes at chunk 3: the deep run restarts, 8 deep chunks later -> 11
    assert ev.fixed_close(daemon, probs, ev.LIVE_FIXED, frame_offset=20) == 11
    # the policy arm's guard is the same frame rule
    pts = ev.check_points([S] + [Q] * 6, 0.35, 160, 160, 800, min_chunks=max(0, 6 - 20))
    assert pts[0] == 2


def test_quiet_lock_rechecks_the_box_after_taking_the_lock(ev, tmp_path):
    # free before the lock, busy once it is held (a night window began during the wait),
    # then free: release, wait and re-acquire - never run a batch on the stale check.
    answers = iter([None, "night window", None, None])
    calls = []

    def busy(_mem):
        calls.append(1)
        return next(answers)

    fh = ev.acquire_quiet_lock(tmp_path / "lane.lock", 1500, "t", busy=busy, wait_s=0)
    assert fh is not None and len(calls) == 4
    fh.close()


def test_guarded_creates_the_work_only_after_the_box_is_free_and_releases_the_lock(ev, tmp_path):
    # the model is loaded inside ``work``: it must not exist while the guard is still waiting
    answers = iter(["night window", "night window", None, None])
    seen = []

    def busy(_mem):
        a = next(answers)
        seen.append(a)
        return a

    def work():
        assert seen == ["night window", "night window", None, None]   # every wait done first
        return "scored"

    lock = tmp_path / "lane.lock"
    assert ev.guarded(lock, 1500, "t", work, busy=busy, wait_s=0) == "scored"
    import fcntl
    with open(lock, "a") as fh:                       # released: a second taker gets it at once
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)


def test_cached_batch_is_bound_to_its_exact_slice(ev, tmp_path):
    corpus = tmp_path / "c"
    corpus.mkdir()
    clips = []
    for n in range(4):
        p = corpus / f"clip{n}.wav"
        p.write_bytes(b"x" * (10 + n))
        clips.append(p)
    key = ev.batch_key(clips[:2], 0, 2, 2)
    part = tmp_path / "batch_00000.json"
    part.write_text('{"fingerprint": "F", "slice": "%s", "rows": []}' % key)
    assert ev.cached_batch(part, "F", key) is not None
    assert ev.cached_batch(part, "G", key) is None                         # another configuration
    assert ev.cached_batch(part, "F", ev.batch_key(clips[2:], 0, 2, 2)) is None   # another --limit / corpus
    assert ev.cached_batch(part, "F", ev.batch_key(clips[:1], 0, 1, 2)) is None   # another --batch-size
    assert ev.cached_batch(part, "F", ev.batch_key(clips[:2], 0, 2, 3)) is None   # another synthetic stride
    part.write_text('{"fingerprint": "F", "rows": []}')                   # a pre-binding batch: never reused
    assert ev.cached_batch(part, "F", key) is None
    part.write_text("{broken")
    assert ev.cached_batch(part, "F", key) is None
    assert "clip0" not in key
