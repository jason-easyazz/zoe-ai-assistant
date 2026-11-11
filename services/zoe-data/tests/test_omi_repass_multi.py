"""Production voters + ROVER fusion + the contender evaluation (``omi_repass_eval.py``): owner policy 2026-11-09 - run every strong contender for a while.

Groups: rover (the voting rules) / ledger (what each model contributed, the verdict thresholds, nothing auto-prunes) / hyps store (forget-reached, pruned) / pipeline (order, budget, carry,
the sample, the check queue, rehearsal writes nothing). Synthetic text only; the workers are fake runners.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import omi_repass as rp
import omi_repass_eval as eu
import user_erase_gate as erase_gate
from test_omi_recordings import OWNER, env  # noqa: F401
from test_omi_repass import first_pass, renv, result_for, rows, run, transcribe, vec  # noqa: F401

PIV, PAR, DIS, WH, GR = "qwen3-asr", "parakeet-tdt", "whisper-gpu:distil-large-v3", "whisper-gpu:large-v3", "granite-speech"


# ── ROVER ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_majority_outvotes_the_pivot_and_the_pivot_breaks_ties():
    f = eu.rover({PIV: "the bat sat on the mat", PAR: "the cat sat on the mat", DIS: "the cat sat on the mat"})
    assert f["text"] == "the cat sat on the mat" and not f["low_conf"] and f["voters"] == [PIV, PAR, DIS]
    tie = eu.rover({PIV: "the bat sat", PAR: "the cat sat", DIS: "the hat sat"})                 # three different words: nobody has a majority, the pivot wins and the slot is contested
    assert tie["text"] == "the bat sat" and len(tie["slots"]) == 1 and tie["slots"][0]["votes"]["bat"] == 1 and tie["slots"][0]["who"]["cat"] == [PAR]


def test_an_inserted_word_needs_a_strict_majority_and_an_empty_hypothesis_abstains():
    one = eu.rover({PIV: "see you tomorrow", PAR: "see you tomorrow morning", DIS: "see you tomorrow"})
    assert one["text"] == "see you tomorrow"                                                       # 1 of 3 inserted it: not a majority
    two = eu.rover({PIV: "see you tomorrow", PAR: "see you tomorrow morning", DIS: "see you tomorrow morning"})
    assert two["text"] == "see you tomorrow morning"
    abstain = eu.rover({PIV: "hello there friend", PAR: "", DIS: "hello there friend", GR: "hello there friend"})
    assert abstain["text"] == "hello there friend" and PAR not in abstain["voters"]                # an empty hypothesis is an engine failure, not a vote for silence


def test_fewer_than_three_voters_leave_the_pivot_untouched_and_granites_zoe_is_ignored():
    assert eu.rover({PIV: "alpha beta", PAR: "alpha gamma"})["text"] == "alpha beta"
    g = eu.rover({PIV: "so we talked", PAR: "so we talked", DIS: "so we talked", GR: "so zoe talked"})
    assert g["text"] == "so we talked"
    both = eu.rover({PIV: "hey zoe we talked", PAR: "hey zoe we talked", DIS: "hey zoe we talked", GR: "hey zoe we talked"})
    assert "zoe" in both["text"]                                                                  # the pivot also says it: Granite's zoe counts as a vote


def test_an_empty_pivot_with_speech_from_the_other_voters_goes_to_review_not_silence():
    said = "we should book the table for friday"
    f = eu.rover({PIV: "", PAR: said, DIS: said})
    assert f["text"] == said and f["low_conf"] and f["words"] == said.split() and all(src == {PAR, DIS} for src in f["sources"])          # not silently empty: the chunk is flagged for the check queue
    assert eu.rover({PIV: "", PAR: "", DIS: ""}) == eu.rover({PIV: "", PAR: " "}) and eu.rover({PIV: "", PAR: "", DIS: ""})["text"] == ""
    assert not eu.rover({PIV: "", PAR: "", DIS: ""})["low_conf"]                         # all-empty stays empty and unflagged
    assert eu.rover({PIV: "", PAR: said})["text"] == ""                                  # one lone engine (likely a hallucination on silence) is not enough


def test_a_chunk_every_other_engine_disagrees_with_is_low_confidence_and_keeps_the_pivot_text():
    f = eu.rover({PIV: "completely different words here now", PAR: "zzz yyy xxx www vvv", DIS: "aaa bbb ccc ddd eee"})
    assert f["low_conf"] and f["text"] == "completely different words here now"
    ok = eu.rover({PIV: "one two three four", PAR: "one two three four", DIS: "xxx yyy zzz www"})   # one engine far away is just an outlier
    assert not ok["low_conf"]


def test_the_sources_say_which_models_voted_for_each_final_word():
    f = eu.rover({PIV: "the bat sat", PAR: "the cat sat", DIS: "the cat sat"})
    assert f["words"] == ["the", "cat", "sat"] and f["sources"][1] == {PAR, DIS} and f["sources"][0] == {PIV, PAR, DIS}


# ── ledger ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def _chunk(hyps, eval_models=()):
    prod = {k: v for k, v in hyps.items() if k not in eval_models}
    fz = eu.rover(prod)
    withs = {m: eu.rover({**prod, m: hyps[m]}) for m in eval_models if m in hyps}
    return {"hyps": hyps, "fused": fz["text"], "speech_s": 2.0, "sources": fz["sources"], "with": withs}


def test_sole_contributor_counts_only_words_the_vote_took_from_that_model_alone():
    c = eu.contributions([_chunk({PIV: "the bat sat", PAR: "the cat sat", DIS: "the cat sat"})], [PIV, PAR, DIS])
    assert c[PIV]["sole"] == 0 and c[PAR]["sole"] == 0 and c[DIS]["sole"] == 0                      # "cat" came from two models: nobody was the only source
    assert c[PIV]["invented"] == 1 and c[PAR]["invented"] == 0                                      # "bat" was only the pivot's and the fused text rejected it
    solo = eu.contributions([_chunk({PIV: "go home now", PAR: "go home", DIS: "go home now"})], [PIV, PAR, DIS])
    assert solo[PIV]["sole"] == 0                                                                    # "now" was in the pivot and distil: shared


def test_an_evaluation_contender_is_judged_on_what_the_vote_would_have_done_with_it_added():
    h = {PIV: "meet at five", PAR: "meet at five", DIS: "meet at fire", GR: "meet at five thirty", WH: "meat at five"}
    c = eu.contributions([_chunk(h, eval_models=(GR, WH))], [PIV, PAR, DIS])
    assert GR in c and WH in c and c[GR]["chunks"] == 1
    assert c[GR]["invented"] == 1 and c[GR]["sole"] == 0 and c[GR]["changed"] == 0                  # "thirty" is only Granite's and 1 of 4 voters does not insert it: rejected, nothing changes
    assert c[WH]["invented"] == 1 and c[WH]["sole"] == 0                                             # "meat" only Whisper's, outvoted
    solo = eu.contributions([_chunk({PIV: "meet at", PAR: "meet at", DIS: "meet at", WH: "meet at five", GR: "meet at five"}, eval_models=(GR, WH))], [PIV, PAR, DIS])
    assert solo[WH]["sole"] == 0 and solo[GR]["sole"] == 0                                           # "five" came from TWO contenders and still fails the strict majority (2 of 5): not sole, not chosen


def test_the_verdict_needs_fourteen_nights_and_hours_and_never_acts():
    base = {"night": "2026-11-01", "model": GR, "eval": True, "chunks": 100, "speech_s": 1800.0, "words": 5000, "sole": 10, "invented": 120, "agree_sum": 90.0, "agree_n": 100}
    few = [dict(base, night=f"2026-11-{d:02d}") for d in range(1, 8)]
    assert "not enough data yet" in eu.verdicts(few)[0]
    many = [dict(base, night=f"2026-11-{d:02d}") for d in range(1, 16)] + [dict(base, night=f"2026-12-{d:02d}") for d in range(1, 3)]
    v = eu.verdicts(many)[0]
    assert "CANDIDATE TO DROP (owner decides)" in v and "0.2%" in v                                    # 10 sole / 5000 words = 0.2 % < the 1 % bar, and it adds invented words
    keep = [dict(r, sole=400) for r in many]
    assert "keep" in eu.verdicts(keep)[0] and "DROP" not in eu.verdicts(keep)[0]
    harmless = [dict(r, invented=0) for r in many]
    assert "harmless" in eu.verdicts(harmless)[0]
    assert "granite" in eu.contender_line(many).lower() or "Granite" in eu.contender_line(many) or GR in eu.contender_line(many)


def test_owner_corrections_are_scored_as_ground_truth_hits_and_misses(tmp_path):
    home = tmp_path / "h"
    eu.store_hyps(home, "2026-11-09", [{"u": "m1", "rid": "r1", "i": 0, "t0": 0.0, "t1": 2.0, "hyps": {PIV: "book the tickets", PAR: "look the pickets", DIS: "book the tickets"}}])
    tally = eu.note_owner_correction(home, "m1", "r1", 0, 2000, "book the tickets", "2026-11-09")
    assert tally == {PIV: {"hit": 1, "miss": 0}, PAR: {"hit": 0, "miss": 1}, DIS: {"hit": 1, "miss": 0}}
    s = eu.summarise(eu.read_ledger(home))
    assert s[PAR]["owner_miss"] == 1 and s[PIV]["owner_hit"] == 1
    assert eu.note_owner_correction(home, "someone-else", "r1", 0, 2000, "x") == {}                  # another member's chunks are never scored


def test_a_speaker_check_row_overlapping_the_corrected_span_is_skipped_not_a_crash(tmp_path):
    """run_night() stores who_said items beside the transcript hypotheses; they have no 'hyps'. A correction over both must still score the transcript row."""
    home = tmp_path / "h"
    eu.store_hyps(home, "2026-11-09", [
        {"u": "m1", "rid": "r1", "i": 0, "t0": 0.0, "t1": 2.0, "hyps": {PIV: "book the tickets", PAR: "look the pickets"}},
        {"kind": "who_said", "u": "m1", "rid": "r1", "seg_id": "s1", "t0": 0.5, "t1": 1.5, "mark": "unknown", "cands": []}])
    tally = eu.note_owner_correction(home, "m1", "r1", 0, 2000, "book the tickets", "2026-11-09")
    assert tally == {PIV: {"hit": 1, "miss": 0}, PAR: {"hit": 0, "miss": 1}}                  # the transcript row is scored once; the speaker check is not
    only = tmp_path / "h2"
    eu.store_hyps(only, "2026-11-09", [{"kind": "who_said", "u": "m1", "rid": "r1", "seg_id": "s1", "t0": 0.5, "t1": 1.5, "mark": "lean", "cands": []}])
    assert eu.note_owner_correction(only, "m1", "r1", 0, 2000, "x") == {}


# ── hypothesis store ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_hypotheses_are_private_pruned_and_forget_reached(tmp_path):
    home = tmp_path / "h"
    fz = eu.rover({PIV: "call Dana now", PAR: "call Dan now", DIS: "call Dana now"})
    eu.store_hyps(home, "2026-11-09", [{"u": "m1", "rid": "r1", "i": 0, "t0": 0, "t1": 2, "hyps": {PIV: "call Dana now"}, "rover": "call Dana now",
                                        "slots": [{"pos": 1, "pick": "Dana", "votes": {"Dana": 1, "Dan": 1}, "who": {"Dana": [PIV], "Dan": [PAR]}, "n": 3}]},
                                       {"u": "m2", "rid": "r2", "i": 0, "t0": 0, "t1": 2, "hyps": {PIV: "call Dana"}}])
    eu.store_hyps(home, "2026-10-01", [{"u": "m1", "rid": "r0", "i": 0, "t0": 0, "t1": 2, "hyps": {PIV: "old"}}])
    eu.store_hyps(home, "2026-11-02", [{"u": "m1", "rid": "r0", "i": 0, "t0": 0, "t1": 2, "hyps": {PIV: "seven days old"}}])         # reached 7 days on the 9th: goes
    eu.store_hyps(home, "2026-11-03", [{"u": "m1", "rid": "r0", "i": 0, "t0": 0, "t1": 2, "hyps": {PIV: "six days old"}}])           # kept
    f = next((home / "hyps").glob("hyps-2026-11-09.jsonl"))
    assert oct(f.stat().st_mode & 0o777) == "0o600" and oct((home / "hyps").stat().st_mode & 0o777) == "0o700"
    assert eu.prune_hyps(home, date(2026, 11, 9)) == 2 and not (home / "hyps" / "hyps-2026-10-01.jsonl").exists() and not (home / "hyps" / "hyps-2026-11-02.jsonl").exists()
    assert (home / "hyps" / "hyps-2026-11-03.jsonl").exists()                                        # at most seven days of files remain
    assert eu.erase_entity(home, "m1", re.compile("Dana"), "[forgotten]") == 1
    text = f.read_text()
    assert "call [forgotten] now" in text and "call Dana" in text                                    # m2's line (another member) is untouched
    m1 = [json.loads(x) for x in text.splitlines() if '"m1"' in x][0]
    assert "Dana" not in json.dumps(m1) and m1["rover"] == "call [forgotten] now" and "[forgotten]" in m1["slots"][0]["votes"]       # the ROVER pick and the contested-slot picks / vote keys / who keys too
    assert eu.forget_user(home, "m1") == 2 and "m1" not in f.read_text() and "m2" in f.read_text()


def test_abandoned_work_files_are_swept_by_retention_and_by_erasure_but_a_live_run_is_left_alone(tmp_path):
    """A SIGKILL / reboot mid-run leaves work/<id>.out.jsonl (transcripts + embeddings): retention, an erasure and the next run clear it once stale."""
    import os
    import time
    home = tmp_path / "h"
    (home / "work").mkdir(parents=True)
    old, live = home / "work" / "dead.out.jsonl", home / "work" / "live.manifest.json"
    old.write_text("{}")
    live.write_text("{}")
    os.utime(old, (time.time() - 7 * 3600,) * 2)
    assert eu.sweep_work(home) == 1 and not old.exists() and live.exists()      # a running pass's files are younger than the stale age
    old.write_text("{}")
    os.utime(old, (time.time() - 7 * 3600,) * 2)
    eu.forget_user(home, "m1")
    assert not old.exists() and not live.exists()          # an erasure clears EVERY work file whatever its age (they name no member: none can be proven clean)
    live.write_text("{}")
    old.write_text("{}")
    os.utime(old, (time.time() - 7 * 3600,) * 2)
    eu.prune_hyps(home, date(2026, 11, 9))
    assert not old.exists() and live.exists()


def test_the_check_queue_lists_contested_and_low_confidence_chunks_with_alternatives_and_votes(tmp_path):
    home = tmp_path / "h"
    hy_low = {PIV: "completely different words here now", PAR: "zzz yyy xxx www vvv", DIS: "aaa bbb ccc ddd eee"}
    hy_tie = {PIV: "the bat sat", PAR: "the cat sat", DIS: "the hat sat"}
    hy_fine = {PIV: "all good here", PAR: "all good here", DIS: "all good here"}
    rows_ = []
    for i, hy in enumerate((hy_low, hy_tie, hy_fine)):
        fz = eu.rover(hy)
        rows_.append({"u": "m1", "rid": "r1", "i": i, "t0": i * 3.0, "t1": i * 3.0 + 2, "hyps": hy, "rover": fz["text"], "slots": fz["slots"], "low_conf": fz["low_conf"], "voters": fz["voters"]})
    eu.store_hyps(home, "2026-11-09", rows_)
    q = eu.check_queue(home, "m1")
    assert [x["i"] for x in q] == [0, 1] and q[0]["low_conf"] and q[0]["hyps"][PAR] == "zzz yyy xxx www vvv" and q[0]["rover"] == "completely different words here now"
    assert q[1]["slots"][0]["votes"] == {"bat": 1, "cat": 1, "hat": 1} and q[1]["slots"][0]["pick"] == "bat"
    assert eu.check_queue(home, "m2") == []                                                          # per member


def test_pick_sample_rotates_spreads_and_respects_the_speech_budget():
    chunks = [("r", i, i * 4.0, i * 4.0 + 2.0) for i in range(60)]                                   # 120 s of speech
    a, b = eu.pick_sample(chunks, 30.0, 100), eu.pick_sample(chunks, 30.0, 101)
    assert sum(c[3] - c[2] for c in a) <= 30.0 and a != b and a[0][1] != b[0][1]                      # a different night, different chunks
    assert a[-1][1] - a[0][1] > 30                                                                  # spread across the day, not the first 15
    assert eu.pick_sample(chunks, 0, 1) == [] and len(eu.pick_sample(chunks, 1000, 1)) == 60


# ── the pipeline ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def multi_runner(texts, calls, fail=(), before=None):
    """A worker double: the pivot returns its chunks; every later engine returns the chunks it was GIVEN with its own text for each."""
    def runner(manifest, out, timeout_s, mem_max="", threads=4):
        man = json.loads(Path(manifest).read_text())
        eng = man["asr"]
        calls.append({"asr": eng, "diar": man["diar"], "ct2": man.get("ct2_dir"), "chunks": [it.get("chunks") for it in man["items"]], "ids": [it["id"] for it in man["items"]], "mem_max": mem_max})
        if before:
            before(eng)
        with open(out, "a") as fh:
            if eng not in fail:
                for it in man["items"]:
                    if it.get("chunks") is None:
                        cks = [[0.0, 2.0, texts[eng][0]], [2.5, 4.5, texts[eng][1]]]
                        r = result_for(it["id"])
                        r["chunks"] = cks
                    else:
                        r = {"id": it["id"], "ok": True, "dur_s": 3.0, "speech_s": 2.0, "asr_s": 1, "emb_s": 0, "words": [], "windows": [],
                             "chunks": [[c[0], c[1], texts[eng][i]] for i, c in enumerate(it["chunks"])]}
                    fh.write(json.dumps(r) + "\n")
            fh.write(json.dumps({"done": True, "files": 1, "carried": 0, "failed": 0, "rtf": 0.4, "device": "cuda", "peak_rss_mb": 8000, "gpu_peak_mb": 4000}) + "\n")
        return 0
    return runner


TEXTS = {PIV: ["the bat sat", "on the mat"], PAR: ["the cat sat", "on the mat"], DIS: ["the cat sat", "on a mat"], WH: ["the cat sat", "on the mat"], GR: ["the cat sat zoe", "on the mat"]}


def on(monkeypatch, evals=f"{WH},{GR}"):
    monkeypatch.setenv("ZOE_OMI_NIGHT_ASR_SECOND", PAR)
    monkeypatch.setenv("ZOE_OMI_NIGHT_ASR_THIRD", DIS)
    monkeypatch.setenv("ZOE_OMI_NIGHT_ASR_EVAL", evals)


def test_production_runs_qwen_then_parakeet_then_the_third_voter_then_the_evaluation_sample_and_the_text_is_the_vote(renv, monkeypatch, tmp_path):
    on(monkeypatch)
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")
    calls: list = []
    out = run(rp.run_night(budget_s=3000, runner=multi_runner(TEXTS, calls)))
    assert [c["asr"] for c in calls] == [PIV, PAR, DIS, WH, GR]                                     # production first, value-per-minute order, evaluation last
    assert [c["diar"] for c in calls] == ["resnet34", "none", "none", "none", "none"]                    # the speaker model is loaded once, by the pivot
    assert calls[0]["chunks"] == [None] and calls[1]["chunks"][0] == [[0.0, 2.0], [2.5, 4.5]] and calls[3]["ct2"] and not calls[1]["ct2"]    # later engines get the PIVOT'S chunks; CT2 build only for whisper-gpu
    assert out["status"] == "ok" and out["engines"] == [PIV, PAR, DIS] and out["eval_engines"] == [WH, GR]
    r = rows(renv)[0]
    assert r["transcript"] == "the cat sat on the mat" and r["repass_model"] == f"{PIV}+{PAR}+{DIS}/rover+resnet34"       # the vote fixed the pivot's "bat"; provenance names the voters
    home = tmp_path / "repass"
    led = [json.loads(x) for x in (home / "ledger.jsonl").read_text().splitlines()]
    assert {x["model"] for x in led} == {PIV, PAR, DIS, WH, GR} and {x["model"] for x in led if x["eval"]} == {WH, GR}
    assert "hyps" in json.dumps(list((home / "hyps").glob("*"))[0].name) or (home / "hyps").exists()
    hy = [json.loads(x) for x in next((home / "hyps").glob("hyps-*.jsonl")).read_text().splitlines()]
    assert len(hy) == 2 and set(hy[0]["hyps"]) == {PIV, PAR, DIS, WH, GR} and hy[0]["rover"] == "the cat sat"
    assert "ledger" not in json.dumps(out) and out["contributions"][PIV]["invented"] >= 1             # the summary is counts; the pivot's "bat" is its invented word


def test_the_third_voter_is_whisper_large_v3_when_the_night_has_room_else_distil_else_none(renv, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_NIGHT_ASR_SECOND", PAR)
    monkeypatch.delenv("ZOE_OMI_NIGHT_ASR_THIRD")
    monkeypatch.setenv("ZOE_OMI_NIGHT_ASR_EVAL", "none")
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")
    for dur, budget, want in ((1500, 3000, WH), (1500, 2000, DIS), (1500, 1450, None)):
        renv.db.conn.execute("UPDATE omi_recordings SET duration_s = ?, repass_state = 'pending'", (dur,))
        renv.db.conn.commit()
        out = run(rp.run_night(budget_s=budget, runner=multi_runner(TEXTS, []), dry_run=True))
        assert out["engines"] == [PIV, PAR] + ([want] if want else []), (dur, budget, out["engines"])


def test_the_evaluation_samples_the_whisper_that_did_not_vote_when_the_third_voter_fell_back_to_distil(renv, monkeypatch):
    """Review #2: the eval set used to drop thirds[:1] (large-v3) up front, so a night whose third voter was distil evaluated neither whisper."""
    monkeypatch.setenv("ZOE_OMI_NIGHT_ASR_SECOND", PAR)
    monkeypatch.delenv("ZOE_OMI_NIGHT_ASR_THIRD")
    monkeypatch.setenv("ZOE_OMI_NIGHT_ASR_EVAL", f"{WH},{DIS},{GR}")
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")
    renv.db.conn.execute("UPDATE omi_recordings SET duration_s = 1500")
    renv.db.conn.commit()
    real = rp.plan_voters
    monkeypatch.setattr(rp, "plan_voters", lambda durs, rem, rt, second, thirds, **k: {**real(durs, rem, rt, second, [DIS], **k)})    # the budget picks distil
    out = run(rp.run_night(budget_s=3000, runner=multi_runner(TEXTS, [])))
    assert out["engines"] == [PIV, PAR, DIS], out["engines"]
    assert out["eval_engines"] == [WH, GR], out["eval_engines"]


def test_granite_never_votes_in_production_even_when_the_config_asks_for_it(renv, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_NIGHT_ASR_THIRD", GR)
    monkeypatch.setenv("ZOE_OMI_NIGHT_ASR_SECOND", GR)
    monkeypatch.delenv("ZOE_OMI_NIGHT_ASR_EVAL")
    assert rp.third_choices() == [] and rp.second_backend() == "" and GR in rp.eval_backends()          # measured: a fourth voter Granite HURTS (5.0-5.2 % against 4.5 %)
    monkeypatch.delenv("ZOE_OMI_NIGHT_ASR_THIRD")
    monkeypatch.delenv("ZOE_OMI_NIGHT_ASR_SECOND")
    assert rp.third_choices() == [WH, DIS] and rp.second_backend() == PAR


def test_an_over_budget_night_keeps_the_pivot_and_drops_the_other_voters_and_the_evaluation(renv, monkeypatch):
    on(monkeypatch)
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")
    renv.db.conn.execute("UPDATE omi_recordings SET duration_s = 2600")
    renv.db.conn.commit()
    calls: list = []
    t = [1000.0]
    inner = multi_runner(TEXTS, calls)

    def runner(*a, **k):
        rc = inner(*a, **k)
        t[0] += 1300.0                                                                               # the pivot's pass took its measured time (2600 s x 0.42 = 1092 s plus loads)
        return rc
    out = run(rp.run_night(budget_s=1500, runner=runner, clock=lambda: t[0]))
    assert [c["asr"] for c in calls] == [PIV] and out["engines"] == [PIV] and out["eval_engines"] == [] and out["model"] == PIV     # the pivot is never swapped out; no time was left for a voter
    assert out["qwen_only_min"] > 0 and out["fused3_min"] == 0 and out["carried_min"] == 0                # ... and the recording still got its Qwen3 text
    assert rows(renv)[0]["transcript"] == "the bat sat on the mat"                                    # the pivot alone


def test_a_missing_second_voter_means_no_vote_no_evaluation_and_the_pivot_text(renv, monkeypatch, tmp_path):
    on(monkeypatch)
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")
    calls: list = []
    out = run(rp.run_night(budget_s=3000, runner=multi_runner(TEXTS, calls, fail=(PAR,))))
    assert [c["asr"] for c in calls] == [PIV, PAR, DIS]                                              # no evaluation pass: production did not finish
    assert rows(renv)[0]["transcript"] == "the bat sat on the mat" and out["sample_speech_s"] == 0      # two voters (pivot + distil) cannot outvote the pivot
    led = [json.loads(x) for x in (tmp_path / "repass" / "ledger.jsonl").read_text().splitlines()]
    assert {x["model"] for x in led} == {PIV, DIS}


def test_a_member_erased_after_the_recording_was_applied_still_leaves_no_hypotheses(renv, monkeypatch, tmp_path):
    on(monkeypatch, evals="none")
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")
    real = eu.store_hyps

    def late(home, night, rows_):                                           # the erase lands between the filter and the write
        erase_gate._generation[OWNER] = erase_gate.generation(OWNER) + 1
        real(home, night, rows_)

    monkeypatch.setattr(eu, "store_hyps", late)
    run(rp.run_night(budget_s=3000, runner=multi_runner(TEXTS, [])))
    files = list((tmp_path / "repass" / "hyps").glob("hyps-*.jsonl"))
    assert all(OWNER not in f.read_text() for f in files)                    # swept again after the write


def test_an_erased_member_leaves_no_hypotheses_behind(renv, monkeypatch, tmp_path):
    on(monkeypatch, evals="none")
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")
    calls: list = []
    runner = multi_runner(TEXTS, calls, before=lambda eng: erase_gate._generation.__setitem__(OWNER, erase_gate.generation(OWNER) + 1) if eng == DIS else None)
    out = run(rp.run_night(budget_s=3000, runner=runner))
    assert out["erased"] == 1 and rows(renv)[0]["status"] == "deleted"
    assert not list((tmp_path / "repass" / "hyps").glob("hyps-*.jsonl")) or all(OWNER not in f.read_text() for f in (tmp_path / "repass" / "hyps").glob("hyps-*.jsonl"))


def test_the_rehearsal_runs_every_engine_on_one_recording_and_writes_nothing(renv, monkeypatch, tmp_path):
    on(monkeypatch)
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "off")
    monkeypatch.setenv("ZOE_OMI_KEEP_AUDIO", "1")
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")
    calls: list = []
    out = run(rp.run_night(budget_s=3000, runner=multi_runner(TEXTS, calls), rehearse=True))
    assert out["status"] == "rehearsal_ok" and [c["asr"] for c in calls] == [PIV, PAR, DIS, WH, GR] and out["engines"] == [PIV, PAR, DIS]
    assert out["contested_slots"] == 0 and out["low_conf_chunks"] == 0 and set(out["contributions"]) == {PIV, PAR, DIS, WH, GR}       # the fixture's disagreements are all outvoted 2-to-1
    assert rows(renv)[0]["transcript"] == "moonshine" and rows(renv)[0]["repass_state"] == ""
    assert not (tmp_path / "repass" / "ledger.jsonl").exists() and not (tmp_path / "repass" / "hyps").exists() and not (tmp_path / "repass" / "state.json").exists()


def test_the_weekly_contender_line_appears_on_mondays_only(renv, monkeypatch):
    on(monkeypatch, evals="none")
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")
    monday = datetime(2026, 11, 9, 3, 0, tzinfo=timezone.utc)
    calls: list = []
    assert "contenders" in run(rp.run_night(budget_s=3000, runner=multi_runner(TEXTS, calls), now=monday))
    renv.db.conn.execute("UPDATE omi_recordings SET repass_state = 'pending', status = 'done'")
    renv.db.conn.commit()
    tuesday = monday + timedelta(days=1)
    assert "contenders" not in run(rp.run_night(budget_s=3000, runner=multi_runner(TEXTS, calls), now=tuesday))


# ── capacity: Qwen3 on ALL the audio, the other voters on the IMPORTANT recordings first (owner, 2026-11-09) ──────────────────────────────────

def test_importance_scores_names_numbers_times_commitments_and_pending_candidates():
    names = ["Sam", "Alex"]
    plain = rp.importance("uh nothing much happening around here", names)
    assert plain == 0
    assert rp.importance("Sam is coming round", names) == 3                                       # a household name
    assert rp.importance("we got 12 of them", names) == 2                                         # a number
    assert rp.importance("remind me to call him tomorrow at 5:30", names) == 6                    # commitment + time + the digits as numbers
    assert rp.importance("Sam said call me tomorrow, I owe him 40 dollars", names) == 3 + 2 + 2 + 2
    assert rp.importance("nothing here", names, candidate=True) == 4                              # feeds a pending day-review candidate
    assert rp.importance("Samantha is here", names) == 0                                          # whole words only


def test_plan_voters_gives_the_important_recordings_all_three_voters_then_two_and_the_rest_keep_qwen3_only():
    rt = {PIV: 0.42, PAR: 0.08, WH: 0.74, DIS: 0.44}
    durs = [("a", 1000.0), ("b", 1000.0), ("c", 1000.0)]
    full = rp.plan_voters(durs, 10000, rt, PAR, [WH, DIS])
    assert full == {"second": ["a", "b", "c"], "third": ["a", "b", "c"], "third_engine": WH}               # room for everyone: the best third voter
    tight = rp.plan_voters(durs, 1500, rt, PAR, [WH, DIS])
    assert tight["third_engine"] == DIS and tight["third"] == ["a", "b"] and tight["second"] == ["a", "b", "c"]   # distil covers more recordings than large-v3 would
    none = rp.plan_voters(durs, 300, rt, PAR, [WH, DIS])
    assert none["third"] == [] and none["third_engine"] == "" and none["second"] == ["a", "b"]
    nothing = rp.plan_voters(durs, 30, rt, PAR, [WH, DIS])
    assert nothing == {"second": [], "third": [], "third_engine": ""}                                       # no time after the pivot: Qwen3 only
    assert rp.plan_voters(durs, 10000, rt, "", [WH]) == {"second": [], "third": [], "third_engine": ""}


def _two_recordings(renv, a_text, b_text, dur=1000):
    from test_omi_recordings import make_wav, upload
    upload(renv, make_wav(3.0, hz=220), key="r1.0-1", started="2026-10-10T01:00:00Z")
    upload(renv, make_wav(3.0, hz=330), key="r1.0-2", started="2026-10-10T01:30:00Z")
    ids = [r["id"] for r in rows(renv, "SELECT id FROM omi_recordings ORDER BY started_at")]
    for rid, text in zip(ids, (a_text, b_text)):
        renv.db.conn.execute("UPDATE omi_recordings SET status = 'done', transcript = ?, repass_state = 'pending', duration_s = ? WHERE id = ?", (text, dur, rid))
    renv.db.conn.commit()
    return ids


def test_the_pivot_reads_everything_important_first_and_the_voters_only_what_the_time_left_reaches(renv, monkeypatch):
    on(monkeypatch, evals="none")
    old_boring, new_important = _two_recordings(renv, "uh nothing much going on here", "remind me to call Sam tomorrow at 5:30")
    monkeypatch.setattr(rp, "household_names", lambda db, uid: _async(["Sam"]))
    calls: list = []
    t = [1000.0]
    inner = multi_runner(TEXTS, calls)

    def runner(*a, **k):
        rc = inner(*a, **k)
        t[0] += 840.0 if len(calls) == 1 else 0.0                                                          # the pivot used 840 s of the 1100 s night
        return rc
    out = run(rp.run_night(budget_s=1100, runner=runner, clock=lambda: t[0]))
    assert calls[0]["asr"] == PIV and calls[0]["ids"] == [new_important, old_boring]                      # Qwen3 reads ALL the audio, the important recording first
    assert [c["asr"] for c in calls] == [PIV, PAR] and calls[1]["ids"] == [new_important]                 # the time left reaches Parakeet on ONE recording: the important one
    assert out["done"] == 2 and out["carried"] == 0 and out["engines"] == [PIV, PAR]
    assert out["fused2_min"] > 0 and out["qwen_only_min"] > 0 and out["fused3_min"] == 0 and out["carried_min"] == 0
    assert abs(out["speech_min"] - (out["fused2_min"] + out["qwen_only_min"])) < 0.02
    assert all(r["transcript"] for r in rows(renv))                                                        # nobody is left without Qwen3 text


def test_a_recording_the_pivot_cannot_reach_carries_to_the_next_night_and_the_report_says_how_much(renv, monkeypatch):
    on(monkeypatch, evals="none")
    old_boring, new_important = _two_recordings(renv, "uh nothing much going on here", "remind me to call Sam tomorrow at 5:30")

    def runner(manifest, out, timeout_s, mem_max="", threads=4):
        man = json.loads(Path(manifest).read_text())
        with open(out, "a") as fh:
            for it in man["items"]:
                if it["id"] == new_important:
                    r = result_for(it["id"])
                    r["chunks"] = [[0.0, 2.0, "call sam tomorrow"], [2.5, 4.5, "at five thirty"]]
                    fh.write(json.dumps(r) + "\n")
                else:
                    fh.write(json.dumps({"id": it["id"], "ok": False, "why": "deadline"}) + "\n")      # the budget ran out before the boring one
            fh.write(json.dumps({"done": True, "files": 1, "carried": 1, "failed": 0, "rtf": 0.4, "device": "cuda"}) + "\n")
        return 0
    out = run(rp.run_night(budget_s=1100, runner=runner))
    assert out["carried"] == 1 and out["done"] == 1 and out["carried_min"] > 15.0                         # 1000 s = 16.7 min waiting for tomorrow
    left = {r["id"]: r for r in rows(renv)}
    assert left[old_boring]["repass_state"] == "pending" and left[new_important]["repass_state"] != "pending"       # the carried one is still owed (and its WAV is still held)


async def _async(v):
    return v


# ── review round: unicode erase, large numbers, recording delete, human decisions ─────────────────────────────────

def test_a_forgotten_accented_name_is_erased_from_every_word_field_and_a_year_survives_fusion(tmp_path):
    home = tmp_path / "h"
    eu.store_hyps(home, "2026-11-01", [{"u": "m1", "rid": "r1", "i": 0, "t0": 0, "t1": 5, "hyps": {PIV: "José came"}, "rover": "José came",
                                        "slots": [{"pick": "josé", "votes": {"josé": 2}, "who": {"josé": [PIV]}}]}])
    assert eu.erase_entity(home, "m1", re.compile(r"\bJosé\b", re.I), "[forgotten]") == 1
    blob = (home / "hyps" / "hyps-2026-11-01.jsonl").read_text(encoding="utf-8")
    assert "José" not in blob and "josé" not in blob.lower() and "[forgotten]" in blob
    assert eu.rover({PIV: "The year is 2026."})["text"] == "The year is 2026."


def test_deleting_a_recording_removes_only_its_hypotheses(tmp_path):
    home = tmp_path / "h"
    eu.store_hyps(home, "2026-11-01", [{"u": "m1", "rid": "r1", "i": 0, "t0": 0, "t1": 5, "hyps": {PIV: "a"}}, {"u": "m1", "rid": "r2", "i": 0, "t0": 0, "t1": 5, "hyps": {PIV: "b"}}])
    assert eu.forget_recording(home, "m1", "r1") == 1
    left = (home / "hyps" / "hyps-2026-11-01.jsonl").read_text()
    assert '"r2"' in left and '"r1"' not in left


def test_a_merged_line_keeps_the_owners_decision_and_a_human_person_clears_the_models_not_person():
    old = [{"start_ms": i * 10000, "end_ms": (i + 1) * 10000, "owner_label": "you", "tagged_person_id": None, "not_person": None} for i in range(3)]
    new = [{"start_ms": 0, "end_ms": 30000, "not_person": "zoe", "person": "p1"}]
    assert rp.carry_labels(new, old) == 1
    assert new[0]["carry"]["owner_label"] == "you" and new[0]["not_person"] is None
    tv = [{"start_ms": 0, "end_ms": 10000, "owner_label": None, "tagged_person_id": None, "not_person": "tv"}]
    n2 = [{"start_ms": 0, "end_ms": 10000, "person": "p1"}]
    assert rp.carry_labels(n2, tv) == 1 and n2[0]["not_person"] == "tv" and "person" not in n2[0]


def test_a_decomposed_spelling_of_an_accented_name_is_erased_too(tmp_path):
    home = tmp_path / "h"
    eu.store_hyps(home, "2026-11-01", [{"u": "m1", "rid": "r1", "i": 0, "t0": 0, "t1": 5, "hyps": {PIV: "José came"}, "rover": "José came", "slots": []}])
    assert eu.erase_entity(home, "m1", re.compile("José", re.I), "[forgotten]") == 1
    blob = (home / "hyps" / "hyps-2026-11-01.jsonl").read_text(encoding="utf-8")
    assert "came" in blob and "Jos" not in blob


def test_rover_and_clean_read_keys_keep_non_latin_letters_and_accents():
    """Review: ``[^a-z0-9]`` dropped every non-ASCII letter ("resume" with accents -> "r sum"; a CJK name -> nothing), so a changed non-Latin name passed the guard."""
    import omi_repass_clean as oc
    assert eu.norm_keep("\u738b\u660e said r\u00e9sum\u00e9, ok") == ["\u738b\u660e", "said", "r\u00e9sum\u00e9", "ok"]
    assert oc.keys("Anna \u738b\u660e called") == ["anna", "\u738b\u660e", "called"]
    assert not oc.guard("Anna \u738b\u660e called me", "Anna \u674e\u534e called me").ok


# ── a voter that cannot load is NAMED, never silent (2026-11-11: the whisper third voter lacked the CUDA CTranslate2 and the night passed on two) ──

def test_a_third_voter_that_cannot_load_is_named_with_its_reason_in_the_result(renv, monkeypatch):
    on(monkeypatch)
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")

    def no_load(manifest, out, timeout_s, mem_max="", threads=4):          # the worker's exit 3 with nothing written = "model unavailable"
        man = json.loads(Path(manifest).read_text())
        if man["asr"] == DIS:
            return 3
        return multi_runner(TEXTS, [])(manifest, out, timeout_s, mem_max, threads)
    out = run(rp.run_night(budget_s=3000, runner=no_load))
    assert out["engines"] == [PIV, PAR]
    assert out["missing_voters"] == {DIS: "model_unavailable"} and out["skipped"][DIS] == "model_unavailable"


def test_control_when_every_voter_loads_nothing_is_missing(renv, monkeypatch):
    on(monkeypatch)
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")
    out = run(rp.run_night(budget_s=3000, runner=multi_runner(TEXTS, [])))
    assert out["engines"] == [PIV, PAR, DIS] and out["missing_voters"] is None
