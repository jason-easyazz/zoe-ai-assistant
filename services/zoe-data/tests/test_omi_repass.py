"""The pendant NIGHT RE-PASS (``omi_repass.py``, migration 0075, flag ``ZOE_OMI_NIGHT_REPASS``). Spec: docs/knowledge/omi-night-repass.md.

Groups (each invariant has its negative control next to it):
  hold        audio is never deleted before the pass has seen it (flag on/shadow), the flag-off control still deletes, retention still bounds the hold
  replace     ``on`` swaps in the clean text with provenance, ``shadow`` replaces nothing and logs counts only
  forget      a forgotten span stays redacted after the pass; a member erased mid-job has nothing written
  labels      nobody is "me" without a CONFIRMED print; a human decision on a first-pass line survives the pass
  budget      the planner picks the cheaper backend / carries what does not fit; a carried recording is untouched
  seam        an unknown backend is refused loudly; the declared backends carry their budget facts

Synthetic data only. The DB is the real migrations 0045 / 0055 / 0075 on SQLite; the worker is a fake runner that writes the JSONL a real one would.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

import omi_recordings as omi
import omi_repass as rp
import user_erase_gate as erase_gate
from test_omi_recordings import OWNER, _migrate, env, make_wav, rows, upload  # noqa: F401 - the first pass's own fixture and helpers

import asyncio

UTC = timezone.utc
SVC_DIR = Path(__file__).resolve().parents[1]


def run(coro):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(coro)


@pytest.fixture
def renv(env, monkeypatch, tmp_path):
    _migrate(env.engine, "0055_omi_segments_voices.py")
    _migrate(env.engine, "0075_omi_night_repass.py")
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "on")
    monkeypatch.setenv("ZOE_OMI_REPASS_DIR", str(tmp_path / "repass"))
    monkeypatch.setenv("ZOE_OMI_REPASS_ZOE_CLIPS", str(tmp_path / "no-clips"))
    for k in ("ZOE_OMI_NIGHT_ASR_SECOND", "ZOE_OMI_NIGHT_ASR_THIRD", "ZOE_OMI_NIGHT_ASR_EVAL"):
        monkeypatch.setenv(k, "none")                       # the single-engine path by default; test_omi_repass_multi.py turns the other voters on
    import manner_block

    async def _adult(uid, db=None):                         # the pass retains voice data only for a positively-adult member; the tests' member is one (a child test overrides this)
        return True
    monkeypatch.setattr(manner_block, "eligible", _adult)
    return env


def first_pass(env, text="first words"):
    upload(env, make_wav(3.0))
    return rows(env)[0]


def transcribe(env, monkeypatch, text="first words"):
    async def stt(path):
        return text
    monkeypatch.setattr(omi, "_transcribe_file", stt)
    run(omi.transcribe_pending(limit=1, pause_s=0))
    return rows(env)[0]


def vec(i: int, dim: int = 8) -> list:
    v = np.zeros(dim)
    v[i % dim] = 1.0
    return [round(float(x), 4) for x in v]


def result_for(rid, text=("Hello", "there", "friend"), emb=0, dur=3.0):
    words = [[f" {w}", i * 0.5, i * 0.5 + 0.4, 0.95] for i, w in enumerate(text)]
    wins = [[0.0, 1.5, vec(emb), 0.2], [0.75, 2.25, vec(emb), 0.2], [1.5, 3.0, vec(emb), 0.2]]
    return {"id": rid, "ok": True, "dur_s": dur, "speech_s": 2.0, "asr_s": 1.0, "emb_s": 0.5, "words": words, "windows": wins}


def fake_runner(per_id=None, deadline_ids=(), rc=0, seen=None):
    """A worker double: writes the JSONL a real one would. ``per_id`` maps a recording id to its result; ``deadline_ids`` are carried."""
    def runner(manifest, out, timeout_s, mem_max="", threads=4):
        man = json.loads(Path(manifest).read_text())
        if seen is not None:
            seen.append(man)
        with open(out, "a") as fh:
            for it in man["items"]:
                if it["id"] in deadline_ids:
                    fh.write(json.dumps({"id": it["id"], "ok": False, "why": "deadline"}) + "\n")
                else:
                    fh.write(json.dumps((per_id or {}).get(it["id"]) or result_for(it["id"])) + "\n")
            fh.write(json.dumps({"done": True, "files": len(man["items"]), "carried": len(deadline_ids), "failed": 0, "rtf": 0.5, "device": "cpu", "peak_rss_mb": 2000}) + "\n")
        return rc
    return runner


# ── hold ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_audio_is_held_until_the_night_pass_has_run(renv, monkeypatch):
    r = transcribe(first_pass(renv) and renv, monkeypatch)
    assert r["status"] == "done" and r["repass_state"] == "pending" and r["audio_path"] and Path(r["audio_path"]).exists()
    run(omi.release_tagged_audio(OWNER))              # the hourly release must not take it either
    assert Path(rows(renv)[0]["audio_path"]).exists()


def test_control_flag_off_the_first_pass_still_deletes_the_wav(renv, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "off")
    path = first_pass(renv)["audio_path"]
    r = transcribe(renv, monkeypatch)
    assert r["audio_path"] == "" and not Path(path).exists() and r["repass_state"] == ""


def test_the_hold_is_bounded_by_the_retention_maximum(renv, monkeypatch):
    path = first_pass(renv)["audio_path"]
    transcribe(renv, monkeypatch)
    assert Path(path).exists()
    assert run(omi.purge_expired_audio(now=datetime.now(UTC) + timedelta(days=8))) == 1      # past retention it goes whether or not the pass ran
    assert not Path(path).exists()
    assert run(_pending(renv)) == []                                                        # and is no longer listed as owed


async def _pending(env):
    from db_compat import get_compat_db
    async with get_compat_db() as db:
        return await rp.pending_recordings(db)


# ── replace / shadow ─────────────────────────────────────────────────────────────────────────────────────────────────────

def test_on_replaces_the_text_with_provenance_and_then_releases_the_wav(renv, monkeypatch):
    r0 = transcribe(first_pass(renv) and renv, monkeypatch, "moonshine garble")
    out = run(rp.run_night(budget_s=600, runner=fake_runner()))
    assert out["status"] == "ok" and out["replaced"] == 1 and out["model"] == "qwen3-asr"
    r = rows(renv)[0]
    assert r["transcript"] == "Hello there friend" and r["repass_state"] == "done" and r["repass_model"] == "qwen3-asr+resnet34" and r["repass_at"]
    assert r["audio_path"] == "" and not Path(r0["audio_path"]).exists()                     # the pass has seen it: now the WAV goes
    segs = renv.db.rows("SELECT speaker, model_id, text FROM omi_segments")
    assert segs and all(s["model_id"] == rp.MODEL_ID for s in segs)


def test_a_failure_while_replacing_segments_leaves_the_original_recording_intact(renv, monkeypatch):
    """The segment swap is ONE transaction: a crash after the delete must not leave the owner's first-pass lines gone with nothing in their place."""
    import omi_voices
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine garble")
    rid = rows(renv)[0]["id"]
    renv.db.conn.execute("INSERT INTO omi_segments (id, recording_id, user_id, seq, start_ms, end_ms, text, speaker, created_at) "
                         "VALUES ('seg-first', ?, ?, 0, 0, 1000, 'first pass line', 'unverified', '2026-11-09T00:00:00Z')", (rid, OWNER))
    renv.db.conn.commit()
    before = renv.db.rows("SELECT id, text FROM omi_segments ORDER BY id")
    assert [b["id"] for b in before] == ["seg-first"]

    async def boom(*_a, **_k):
        raise RuntimeError("interrupted after the delete")
    real = omi_voices.store_segments

    async def store_then_boom(*a, **k):
        await real(*a, **k)
        await boom()
    monkeypatch.setattr(omi_voices, "store_segments", store_then_boom)
    out = run(rp.run_night(budget_s=600, runner=fake_runner()))
    assert out["replaced"] == 0
    assert renv.db.rows("SELECT id, text FROM omi_segments ORDER BY id") == before
    r = rows(renv)[0]
    assert r["transcript"] == "moonshine garble" and r["repass_state"] != "done"


def test_shadow_replaces_nothing_and_logs_counts_never_words(renv, monkeypatch, tmp_path):
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "shadow")
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine text")
    out = run(rp.run_night(budget_s=600, runner=fake_runner()))
    assert out["shadow"] == 1 and out["replaced"] == 0
    r = rows(renv)[0]
    assert r["transcript"] == "moonshine text" and r["repass_state"] == "shadow" and r["audio_path"] == ""
    log = (tmp_path / "repass" / "shadow.jsonl").read_text()
    assert "Hello" not in log and "moonshine" not in log and json.loads(log.splitlines()[0])["words"] == 3


def test_an_empty_answer_never_erases_the_first_pass_text(renv, monkeypatch):
    transcribe(first_pass(renv) and renv, monkeypatch, "kept words")
    rid = rows(renv)[0]["id"]
    run(rp.run_night(budget_s=600, runner=fake_runner({rid: {**result_for(rid), "words": [], "windows": []}})))
    assert rows(renv)[0]["transcript"] == "kept words" and rows(renv)[0]["repass_state"] == "shadow"


def test_off_does_nothing_at_all(renv, monkeypatch):
    transcribe(first_pass(renv) and renv, monkeypatch)
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "off")
    assert run(rp.run_night(budget_s=600, runner=fake_runner()))["status"] == "flag_off"


# ── forget ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_forgotten_span_stays_redacted_after_the_pass(renv, monkeypatch):
    transcribe(first_pass(renv) and renv, monkeypatch)
    import memory_forgotten as mf

    async def fake_spans(user_id, text, *, near=True, strict=False):
        i = text.find("Dana")
        return [(i, i + 4)] if i >= 0 and user_id == OWNER else []

    monkeypatch.setattr(mf, "spans", fake_spans)
    rid = rows(renv)[0]["id"]
    run(rp.run_night(budget_s=600, runner=fake_runner({rid: result_for(rid, ("Dana", "called", "today"))})))
    r = rows(renv)[0]
    assert "Dana" not in r["transcript"] and r["transcript"].startswith("[forgotten]")
    assert all("Dana" not in (s["text"] or "") for s in renv.db.rows("SELECT text FROM omi_segments"))


def _ledger_down(monkeypatch):
    import memory_forgotten as mf

    async def down(user_id, text, *, near=True, strict=False):
        raise mf.LedgerUnavailable("down")
    monkeypatch.setattr(mf, "spans", down)


def test_a_forget_ledger_outage_fails_closed_nothing_is_stored(renv, monkeypatch):
    """Forgotten means forever: when the ledger cannot be read the new text cannot be proven clean, so the first-pass transcript stays and nothing is replaced."""
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine text")
    _ledger_down(monkeypatch)
    rid = rows(renv)[0]["id"]
    before = rows(renv)[0]["repass_attempts"]
    out = run(rp.run_night(budget_s=600, runner=fake_runner({rid: result_for(rid, ("Dana", "called", "today"))})))
    r = rows(renv)[0]
    assert out["replaced"] == 0 and r["transcript"] == "moonshine text" and r["repass_state"] in ("pending", "")      # still owed: retried, not failed for good
    assert r["repass_attempts"] == before
    assert not any("Dana" in (x["text"] or "") for x in renv.db.rows("SELECT text FROM omi_segments"))


def test_household_names_withholds_a_name_it_cannot_prove_unforgotten(renv, monkeypatch):
    renv.db.conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)")
    renv.db.conn.execute("INSERT INTO users VALUES ('u1', 'Billie Example', 'member')")
    renv.db.conn.commit()
    import named_guests

    async def names():
        from db_compat import get_compat_db
        async with get_compat_db() as db:
            return await rp.household_names(db, OWNER)

    async def fine(owner, name):
        return False
    monkeypatch.setattr(named_guests, "_forgotten", fine)
    assert any("Billie" in n for n in run(names()))                             # negative control: with the ledger up the name is offered

    async def down(owner, name):
        raise RuntimeError("ledger down")
    monkeypatch.setattr(named_guests, "_forgotten", down)
    assert run(names()) == []                                                   # ledger unreadable: nothing is offered to the ASR


def test_control_without_a_ledger_hit_the_name_is_kept(renv, monkeypatch):
    transcribe(first_pass(renv) and renv, monkeypatch)
    rid = rows(renv)[0]["id"]
    run(rp.run_night(budget_s=600, runner=fake_runner({rid: result_for(rid, ("Dana", "called", "today"))})))
    assert rows(renv)[0]["transcript"].startswith("Dana")


def test_a_member_erased_while_the_job_ran_has_nothing_written(renv, monkeypatch):
    transcribe(first_pass(renv) and renv, monkeypatch)

    def erasing_runner(manifest, out, timeout_s, mem_max="", threads=4):
        erase_gate._generation[OWNER] = erase_gate.generation(OWNER) + 1          # the erase landed while the worker ran
        return fake_runner()(manifest, out, timeout_s, mem_max, threads)

    out = run(rp.run_night(budget_s=600, runner=erasing_runner))
    r = rows(renv)[0]
    assert out["erased"] == 1 and r["status"] == "deleted" and r["transcript"] == "" and r["audio_path"] == ""


# ── labels ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_nobody_is_me_without_any_print():
    refs = rp.Refs()
    words = [[" hi", 0.0, 0.3, 0.9], [" there", 0.4, 0.8, 0.9]]
    lines = rp.build_lines(words, [[0.0, 1.5, vec(0), 0.3]], refs)
    assert [ln["key"] for ln in lines] == ["unverified"]
    assert rp.label_window(vec(0), refs)["key"] == "unverified"


def test_a_panel_only_wearer_print_can_make_a_turn_at_most_you_maybe():
    refs = rp.Refs(you=vec(0), you_panel_only=True)
    v = rp.label_window(vec(0), refs, dur_s=3.0)
    assert v["key"] == "you?" and v["conf"] == "lean"                                             # the owner's PANEL profile alone never says "me" outright
    confirmed = rp.Refs(you=vec(0))
    assert rp.label_window(vec(0), confirmed, dur_s=3.0)["key"] == "you"


def test_propose_wearer_needs_a_real_cluster():
    assert rp.propose_wearer([[0, 1.5, vec(0), 0.3]] * 3) == []
    loud = [[i, i + 1.5, vec(0), 0.5] for i in range(10)] + [[i, i + 1.5, vec(1), 0.01] for i in range(10)]
    pick = rp.propose_wearer(loud)
    assert pick and all(loud[i][3] == 0.5 for i in pick)


# ── budget ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_planner_prefers_the_big_model_drops_to_the_fallback_and_says_when_nothing_fits():
    assert rp.plan_model(600, 1500)["model"] == "qwen3-asr" and rp.plan_model(600, 1500)["fits"]
    p = rp.plan_model(4000, 1500)                                             # 4000 s x 0.42 = 1680 s > 85 % of 1500 s: drop to parakeet (x 0.08 = 320 s)
    assert p["model"] == "parakeet-tdt" and p["fallback"] and p["fits"]
    q = rp.plan_model(20000, 1500)
    assert q["model"] == "parakeet-tdt" and not q["fits"]
    assert rp.plan_model(4000, 1500, {"qwen3-asr": 0.2})["model"] == "qwen3-asr"       # last night's measured speed wins


def test_a_missing_pivot_model_burns_no_recording_retries(renv, monkeypatch):
    """The worker exits 3 (model / package unavailable) with no results: the night reports model_missing and every recording keeps its retries."""
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")
    def no_model(manifest, out, timeout_s, mem_max="", threads=4):
        return 3
    out = run(rp.run_night(budget_s=600, runner=no_model))
    r = rows(renv)[0]
    assert out["status"] == "model_missing" and out["why"] == "model_unavailable"
    assert r["repass_state"] == "pending" and r["repass_attempts"] == 0 and r["transcript"] == "moonshine"


def test_a_recording_the_budget_could_not_reach_carries_to_the_next_night_untouched(renv, monkeypatch):
    transcribe(first_pass(renv) and renv, monkeypatch, "moonshine")
    rid = rows(renv)[0]["id"]
    out = run(rp.run_night(budget_s=600, runner=fake_runner(deadline_ids=(rid,))))
    r = rows(renv)[0]
    assert out["carried"] == 1 and r["repass_state"] == "pending" and r["repass_attempts"] == 0 and r["transcript"] == "moonshine"
    assert r["audio_path"] and Path(r["audio_path"]).exists()                                    # still held for tomorrow
    out2 = run(rp.run_night(budget_s=600, runner=fake_runner()))                                 # tomorrow it finishes
    assert out2["replaced"] == 1 and rows(renv)[0]["repass_state"] == "done"


def test_a_crashing_worker_is_counted_and_gives_up_after_three_nights_but_the_audio_is_kept_to_retention(renv, monkeypatch):
    path = transcribe(first_pass(renv) and renv, monkeypatch)["audio_path"]
    rid = rows(renv)[0]["id"]
    bad = {rid: {"id": rid, "ok": False, "why": "error:ValueError"}}
    for _ in range(rp.MAX_PASS_ATTEMPTS):
        run(rp.run_night(budget_s=600, runner=fake_runner(bad)))
    assert rows(renv)[0]["repass_state"] == "failed" and rows(renv)[0]["transcript"] == "first words"      # the Moonshine text stands
    run(omi.release_tagged_audio(OWNER))                                                        # a failed pass is NOT a pass that read it: the WAV stays
    assert Path(path).exists() and rows(renv)[0]["audio_path"]
    assert run(rp.requeue_failed(OWNER)) == 1 and rows(renv)[0]["repass_state"] == "pending"      # after a fix it can be re-run on the same audio
    assert run(omi.purge_expired_audio(now=datetime.now(UTC) + timedelta(days=8))) == 1          # and retention still bounds it
    assert not Path(path).exists()


def test_the_owner_clearing_keep_audio_cannot_drop_a_wav_the_pass_still_owes_a_look(renv, monkeypatch):
    path = transcribe(first_pass(renv) and renv, monkeypatch)["audio_path"]
    rid = rows(renv)[0]["id"]
    assert renv.client.patch(f"/api/omi/recordings/{rid}", json={"keep_audio": False}).status_code == 200
    assert Path(path).exists() and rows(renv)[0]["repass_state"] == "pending"                   # held for the pass
    run(rp.run_night(budget_s=600, runner=fake_runner()))
    assert rows(renv)[0]["audio_path"] == "" and not Path(path).exists()                        # and the pass released it afterwards


def test_control_with_the_flag_off_clearing_keep_audio_still_drops_the_wav_at_once(renv, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "off")
    monkeypatch.setenv("ZOE_OMI_KEEP_AUDIO", "1")
    path = transcribe(first_pass(renv) and renv, monkeypatch)["audio_path"]
    rid = rows(renv)[0]["id"]
    monkeypatch.delenv("ZOE_OMI_KEEP_AUDIO")
    assert renv.client.patch(f"/api/omi/recordings/{rid}", json={"keep_audio": False}).json()["has_audio"] is False
    assert not Path(path).exists()


def test_a_forget_that_lands_between_redaction_and_the_write_gate_still_redacts(renv, monkeypatch):
    """An entity forget does not move the erase generation (``erasing(bump=False)``): only the lock orders it against our write. The redaction therefore runs
    INSIDE the gate. Simulated: the ledger learns the name at the instant the gate is entered."""
    transcribe(first_pass(renv) and renv, monkeypatch)
    import contextlib
    import memory_forgotten as mf
    learned = {"on": False}

    async def fake_spans(user_id, text, *, near=True, strict=False):
        i = text.find("Dana")
        return [(i, i + 4)] if (learned["on"] and i >= 0) else []

    real = erase_gate.writing

    @contextlib.asynccontextmanager
    async def late_forget(uid, gen):
        learned["on"] = True                       # the forget lands just before we hold the lock
        async with real(uid, gen) as live:
            yield live

    monkeypatch.setattr(mf, "spans", fake_spans)
    monkeypatch.setattr(erase_gate, "writing", late_forget)
    rid = rows(renv)[0]["id"]
    run(rp.run_night(budget_s=600, runner=fake_runner({rid: result_for(rid, ("Dana", "called", "today"))})))
    assert "Dana" not in rows(renv)[0]["transcript"]
    assert all("Dana" not in (x["text"] or "") for x in renv.db.rows("SELECT text FROM omi_segments"))


def test_speaker_units_are_one_embedding_per_short_turn_and_voting_windows_for_a_long_one_and_a_tiny_turn_is_skipped():
    import importlib.util
    spec = importlib.util.spec_from_file_location("omi_repass_worker_units", Path(__file__).resolve().parents[3] / "scripts/night/jobs/omi_repass_worker.py")
    w = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(w)
    emb = w.ResNet34Units.__new__(w.ResNet34Units)
    emb.one = lambda seg: np.ones(4)
    x = np.ones(16000 * 30, dtype="float32")
    wins = emb.units(x, [(1.0, 1.3), (2.0, 6.0), (10.0, 20.0)])           # 0.3 s: skipped; 4 s: ONE unit; 10 s: 1.5 s windows, hop 0.75
    assert [(a, b) for a, b, _e, _r in wins[:1]] == [(2.0, 6.0)]
    longs = [(a, b) for a, b, _e, _r in wins[1:]]
    assert longs[0] == (10.0, 11.5) and longs[1][0] == 10.75 and len(longs) >= 12 and all(b - a <= 1.5 for a, b in longs)       # never a 20 s slab
    assert len([1 for a, b, _e, _r in wins if b - a > 8.0]) == 0


def test_the_name_hint_reaches_the_worker_and_forgotten_names_do_not(renv, monkeypatch):
    transcribe(first_pass(renv) and renv, monkeypatch)
    renv.db.conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)")
    renv.db.conn.execute("INSERT INTO users VALUES ('u1', 'Billie Example', 'member'), ('u2', 'Dana Example', 'member')")
    renv.db.conn.commit()
    import named_guests

    async def forgotten(owner, name):
        return name == "Dana"

    monkeypatch.setattr(named_guests, "_forgotten", forgotten)
    seen: list = []
    run(rp.run_night(budget_s=600, runner=fake_runner(seen=seen)))
    hint = seen[0]["items"][0]["hint"]
    assert "Billie" in hint and "Dana" not in hint
    assert rp.normalise_names("Billy said hi", ["Billie"]) == "Billie said hi" and rp.normalise_names("Billie said hi", ["Billie"]) == "Billie said hi"


# ── the day review waits for the clean text ──────────────────────────────────────────────────────────────────────────────

def test_the_day_review_waits_for_a_pending_recording_only_when_on(renv, monkeypatch):
    transcribe(first_pass(renv) and renv, monkeypatch)
    import importlib.util
    import sys as _sys
    spec = importlib.util.spec_from_file_location("omi_day_review_fresh", SVC_DIR / "omi_day_review.py")     # by path: another test lane may have put a different copy in sys.modules
    dr = importlib.util.module_from_spec(spec)
    _sys.modules["omi_day_review_fresh"] = dr                 # dataclasses look their module up here
    try:
        spec.loader.exec_module(dr)
    finally:
        _sys.modules.pop("omi_day_review_fresh", None)
    assert dr._repass_defers() is True
    sql = f"SELECT id FROM omi_recordings WHERE status = 'done' AND NOT {rp.PENDING_SQL}"
    assert renv.db.rows(sql) == []                                                              # pending + audio: not read yet
    run(rp.run_night(budget_s=600, runner=fake_runner()))
    assert len(renv.db.rows(sql)) == 1                                                          # passed: read, on the clean text
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "shadow")
    assert dr._repass_defers() is False


# ── the seam ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_an_unknown_backend_is_refused_loudly_and_touches_nothing(renv, monkeypatch):
    transcribe(first_pass(renv) and renv, monkeypatch)
    monkeypatch.setenv("ZOE_OMI_NIGHT_ASR", "some-future-model")
    out = run(rp.run_night(budget_s=600, runner=fake_runner()))
    assert out["status"] == "bad_backend" and rows(renv)[0]["repass_state"] == "pending"


def test_every_declared_backend_carries_its_budget_facts_and_the_worker_has_it():
    src = (Path(__file__).resolve().parents[3] / "scripts/night/jobs/omi_repass_worker.py").read_text()
    for name, b in rp.BACKENDS.items():
        assert {"rtf", "gpu_rtf", "peak_rss_mb", "fallback"} <= set(b) and b["rtf"] > 0 and b["peak_rss_mb"] > 0
        assert f'"{name}"' in src, name                                                        # declared here AND registered in the worker
        assert rp.scope_mem_max(name).endswith("M")
