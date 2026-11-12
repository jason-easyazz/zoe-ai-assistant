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


# ── the wearer print from STORED samples (synthetic 256-d unit vectors around invented centres) ────────────────────────────────────────

def _centre(seed: int) -> np.ndarray:
    v = np.random.default_rng(seed).standard_normal(256)
    return v / np.linalg.norm(v)


def _around(c: np.ndarray, seed: int, noise: float = 0.3) -> np.ndarray:
    n = np.random.default_rng(50_000 + seed).standard_normal(256)
    v = c + noise * n / np.linalg.norm(n)
    return (v / np.linalg.norm(v)).astype(np.float32)


_SEQ = {"n": 0}


def _held(env, centre, recs: int, per_rec: int = 1, *, tag: str = "v", uid: str = OWNER, speaker: str = "other", noise: float = 0.3, vec=None, **cols) -> list[str]:
    """``recs`` recordings, each with ``per_rec`` first-pass lines of this voice and their held embedding (the shape ``omi_voices.learn`` leaves). Returns the segment ids."""
    ids = []
    for r in range(recs):
        rid = f"{tag}-r{r}"
        env.db.conn.execute("INSERT OR IGNORE INTO omi_recordings (id, user_id, device_id, recording_key, started_at, status, created_at) VALUES (?, ?, 'dev', ?, ?, 'done', ?)",
                            (rid, uid, rid, "2026-11-10T01:00:00Z", "2026-11-10T01:00:00Z"))
        for k in range(per_rec):
            _SEQ["n"] += 1
            sid = f"{tag}-s{r}-{k}"
            seq = int(env.db.conn.execute("SELECT COUNT(*) FROM omi_segments WHERE recording_id = ?", (rid,)).fetchone()[0])
            env.db.conn.execute("INSERT INTO omi_segments (id, recording_id, user_id, seq, start_ms, end_ms, text, speaker, score, model_id, profile_channel, created_at) "
                                "VALUES (?, ?, ?, ?, ?, ?, 'words', ?, 0.1, ?, 'panel', ?)", (sid, rid, uid, seq, seq * 3000, seq * 3000 + 2500, speaker, rp.MODEL_ID,
                                                                                               f"2026-11-10T01:{_SEQ['n'] // 60:02d}:{_SEQ['n'] % 60:02d}Z"))
            for c, val in cols.items():
                env.db.conn.execute(f"UPDATE omi_segments SET {c} = ? WHERE id = ?", (val, sid))
            e = vec if vec is not None else _around(centre, _SEQ["n"], noise)
            env.db.conn.execute("INSERT INTO omi_segment_embeddings (segment_id, user_id, model_id, embedding, expires_at) VALUES (?, ?, ?, ?, '2999-01-01T00:00:00Z')",
                                (sid, uid, rp.MODEL_ID, np.asarray(e, dtype=np.float32).tobytes()))
            ids.append(sid)
    env.db.conn.commit()
    return ids


NOW = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)


def _proposals(env):
    return {r["proposal_id"]: [np.frombuffer(r["embedding"], dtype=np.float32) for r in env.db.rows(
        "SELECT embedding FROM omi_wearer_samples WHERE proposal_id = ? AND status = 'proposed'", (r["proposal_id"],))]
        for r in env.db.rows("SELECT DISTINCT proposal_id FROM omi_wearer_samples WHERE status = 'proposed'")}


def test_stored_voices_become_side_by_side_proposals_ranked_by_how_many_recordings_they_are_heard_in(renv):
    """Mutation: rank by segments (``len(v["m"])``) instead of distinct recordings -> B (12 recordings x 3 lines = 36) outranks A (15 recordings x 1); drop the ``[:STORED_MAX_VOICES]``
    cut -> D is offered too."""
    A, B, C, D = _centre(1), _centre(2), _centre(3), _centre(4)
    _held(renv, A, 15, tag="a")
    _held(renv, B, 12, 3, tag="b")
    _held(renv, C, 11, tag="c")
    _held(renv, D, 10, tag="d")
    out = run(rp.propose_wearer_from_stored(renv.db, OWNER, now=NOW))
    assert out["status"] == "proposed" and [p["recordings"] for p in out["proposals"]] == [15, 12, 11]
    props = _proposals(renv)
    assert sorted(props) == ["stored-2026-11-12-a", "stored-2026-11-12-b", "stored-2026-11-12-c"]
    for pid, centre in (("stored-2026-11-12-a", A), ("stored-2026-11-12-b", B), ("stored-2026-11-12-c", C)):
        assert props[pid] and all(float(e @ centre) > 0.9 for e in props[pid])                   # each candidate is ONE voice
    assert {r["source"] for r in renv.db.rows("SELECT source FROM omi_wearer_samples")} == {"heuristic"}
    assert run(rp.wearer_print(renv.db, OWNER)) is None             # a proposal is never used: nobody is "me" yet


def test_a_voice_heard_in_fewer_than_ten_recordings_is_not_proposed(renv):
    """Mutation: drop the ``STORED_MIN_RECORDINGS`` filter -> the 9-recording voice is offered. Control: at 10 it is."""
    _held(renv, _centre(5), 9, 4, tag="few")
    out = run(rp.propose_wearer_from_stored(renv.db, OWNER, now=NOW))
    assert out["status"] == "no_voice" and renv.db.rows("SELECT COUNT(*) AS n FROM omi_wearer_samples")[0]["n"] == 0
    _held(renv, _centre(5), 1, tag="tenth")
    assert run(rp.propose_wearer_from_stored(renv.db, OWNER, now=NOW))["status"] == "proposed"


def test_one_recording_gives_a_candidate_at_most_two_windows(renv):
    """One long conversation must not define a voice. Mutation: drop ``per_group`` in the ``_nearest`` call -> the recording whose eight lines sit nearest the centre fills the proposal."""
    c = _centre(6)
    _held(renv, c, 10, 2, tag="spread", noise=0.4)
    _held(renv, c, 1, 8, tag="close", noise=0.02)
    run(rp.propose_wearer_from_stored(renv.db, OWNER, now=NOW))
    per = renv.db.rows("SELECT recording_id, COUNT(*) AS n FROM omi_wearer_samples WHERE status = 'proposed' GROUP BY recording_id")
    assert per and max(r["n"] for r in per) <= rp.STORED_PER_RECORDING and len(per) >= 10 and "close-r0" in {r["recording_id"] for r in per}
    assert sum(r["n"] for r in per) <= rp.PROPOSAL_MAX


def test_no_new_proposal_with_a_confirmed_print_or_while_proposals_are_open(renv):
    """Mutation: drop either guard -> a second batch is written over a confirmed print / beside the open one. Control: with neither, it proposes."""
    _held(renv, _centre(7), 12, tag="w")
    renv.db.conn.execute("INSERT INTO omi_wearer_samples (id, user_id, recording_id, model_id, start_ms, end_ms, embedding, source, status, proposal_id, created_at) "
                         "VALUES ('open1', ?, 'x', ?, 0, 900, ?, 'heuristic', 'proposed', '2026-11-11', ?)", (OWNER, rp.MODEL_ID, _around(_centre(7), 1).tobytes(), rp._iso(NOW)))
    renv.db.conn.commit()
    assert run(rp.propose_wearer_from_stored(renv.db, OWNER, now=NOW))["status"] == "open_proposals"
    assert renv.db.rows("SELECT COUNT(*) AS n FROM omi_wearer_samples")[0]["n"] == 1
    renv.db.conn.execute("UPDATE omi_wearer_samples SET status = 'confirmed'")
    for i in range(rp.MIN_WEARER_WINDOWS):
        renv.db.conn.execute("INSERT INTO omi_wearer_samples (id, user_id, recording_id, model_id, start_ms, end_ms, embedding, source, status, created_at) "
                             "VALUES (?, ?, 'x', ?, ?, ?, ?, 'owner_label', 'confirmed', '2026-11-11T00:00:00Z')", (f"c{i}", OWNER, rp.MODEL_ID, 1000 + i * 1000, 1900 + i * 1000,
                                                                                                                _around(_centre(7), 100 + i).tobytes()))
    renv.db.conn.commit()
    assert run(rp.propose_wearer_from_stored(renv.db, OWNER, now=NOW))["status"] == "has_print"
    assert renv.db.rows("SELECT COUNT(*) AS n FROM omi_wearer_samples WHERE status = 'proposed'")[0]["n"] == 0
    renv.db.conn.execute("DELETE FROM omi_wearer_samples")
    renv.db.conn.commit()
    assert run(rp.propose_wearer_from_stored(renv.db, OWNER, now=NOW))["status"] == "proposed"


def test_a_child_or_unverifiable_members_pendant_gets_no_wearer_proposal(renv, monkeypatch):
    """D14, the first pass's own rule. Mutation: drop the ``manner_block.eligible`` check -> the child's voices are proposed. Control: an adult's are."""
    import manner_block
    _held(renv, _centre(8), 12, tag="k")

    async def child(uid, db=None):
        return False
    monkeypatch.setattr(manner_block, "eligible", child)
    assert run(rp.propose_wearer_from_stored(renv.db, OWNER, now=NOW))["status"] == "not_eligible"
    assert run(rp.relabel_stored_from_print(renv.db, OWNER))["status"] == "not_eligible"
    assert renv.db.rows("SELECT COUNT(*) AS n FROM omi_wearer_samples")[0]["n"] == 0

    async def adult(uid, db=None):
        return True
    monkeypatch.setattr(manner_block, "eligible", adult)
    assert run(rp.propose_wearer_from_stored(renv.db, OWNER, now=NOW))["status"] == "proposed"


def test_a_member_erased_while_the_proposal_was_read_has_nothing_written(renv, monkeypatch):
    """Mutation: write outside ``erase_gate.writing`` -> the erased member's voice is stored as proposals. Control: no erase -> proposed."""
    import manner_block
    _held(renv, _centre(9), 12, tag="e")

    async def erased_meanwhile(uid, db=None):
        async with erase_gate.erasing(uid):
            pass
        return True
    monkeypatch.setattr(manner_block, "eligible", erased_meanwhile)
    assert run(rp.propose_wearer_from_stored(renv.db, OWNER, now=NOW))["status"] == "erased"
    assert renv.db.rows("SELECT COUNT(*) AS n FROM omi_wearer_samples")[0]["n"] == 0
    renv.db.conn.execute("INSERT INTO omi_wearer_samples (id, user_id, recording_id, model_id, start_ms, end_ms, embedding, source, status, created_at) "
                         "SELECT 'p' || segment_id, user_id, 'x' || segment_id, model_id, 0, 1, embedding, 'heuristic', 'confirmed', '2026-11-11T00:00:00Z' FROM omi_segment_embeddings")
    renv.db.conn.commit()
    before = renv.db.rows("SELECT speaker FROM omi_segments")
    assert run(rp.relabel_stored_from_print(renv.db, OWNER))["status"] == "erased"
    assert renv.db.rows("SELECT speaker FROM omi_segments") == before
    renv.db.conn.execute("DELETE FROM omi_wearer_samples")
    renv.db.conn.commit()

    async def adult(uid, db=None):
        return True
    monkeypatch.setattr(manner_block, "eligible", adult)
    assert run(rp.propose_wearer_from_stored(renv.db, OWNER, now=NOW))["status"] == "proposed"


def test_confirming_one_stored_candidate_withdraws_its_siblings_only(renv):
    """Mutation: drop the sibling DELETE in ``confirm_wearer`` -> the other two voices stay wearer candidates. A night-heuristic proposal (another batch) is untouched."""
    for k in range(3):
        _held(renv, _centre(20 + k), 12 - k, tag=f"s{k}")
    run(rp.propose_wearer_from_stored(renv.db, OWNER, now=NOW))
    renv.db.conn.execute("INSERT INTO omi_wearer_samples (id, user_id, recording_id, model_id, start_ms, end_ms, embedding, source, status, proposal_id, created_at) "
                         "VALUES ('night', ?, 'x', ?, 0, 900, ?, 'heuristic', 'proposed', '2026-11-11', ?)", (OWNER, rp.MODEL_ID, _around(_centre(30), 1).tobytes(), rp._iso(NOW)))
    renv.db.conn.commit()
    assert run(rp.confirm_wearer(renv.db, OWNER, "stored-2026-11-12-b")) == 11
    left = renv.db.rows("SELECT proposal_id, status, COUNT(*) AS n FROM omi_wearer_samples GROUP BY proposal_id, status ORDER BY proposal_id")
    assert [(r["proposal_id"], r["status"]) for r in left] == [("2026-11-11", "proposed"), ("stored-2026-11-12-b", "confirmed")]
    you = run(rp.wearer_print(renv.db, OWNER))
    assert you is not None and float(you @ _centre(21)) > 0.9                                     # the confirmed voice IS the print
    assert run(rp.confirm_wearer(renv.db, OWNER, "stored-2026-11-12-a")) == 0                     # a withdrawn sibling cannot be confirmed afterwards


def test_relabel_marks_only_undecided_stored_lines_over_the_bar_as_me_and_is_idempotent(renv):
    """Mutations: drop the owner-label / tag / not-person guards -> those lines turn "me"; lower the bar to 0.4 -> the half-similar line turns "me"; label below the bar ``other`` ->
    the runner-up's "unsure" line changes. Nothing reads "me" before the print is confirmed."""
    W, P = _centre(40), _centre(41)
    mine = _held(renv, W, 12, tag="me")
    partner = _held(renv, P, 11, tag="pa", speaker="unsure")
    owner_other = _held(renv, W, 1, tag="lab", owner_label="other")
    tagged = _held(renv, W, 1, tag="tag", tagged_person_id="person-1")
    tv = _held(renv, W, 1, tag="tv", not_person="tv")
    orth = _centre(42) - float(_centre(42) @ W) * W
    half = 0.5 * W + np.sqrt(0.75) * orth / np.linalg.norm(orth)                                    # cos 0.5 to the voice: the tail, not the shoulder
    mid = _held(renv, None, 1, tag="mid", vec=half)
    assert run(rp.relabel_stored_from_print(renv.db, OWNER))["status"] == "no_print"               # before the owner confirms: nothing
    assert {r["speaker"] for r in renv.db.rows("SELECT speaker FROM omi_segments")} == {"other", "unsure"}
    run(rp.propose_wearer_from_stored(renv.db, OWNER, now=NOW))
    run(rp.confirm_wearer(renv.db, OWNER, "stored-2026-11-12-a"))
    out = run(rp.relabel_stored_from_print(renv.db, OWNER))
    sp = {r["id"]: (r["speaker"], r["profile_channel"]) for r in renv.db.rows("SELECT id, speaker, profile_channel FROM omi_segments")}
    assert out["status"] == "ok" and out["relabelled"] == len(mine)
    assert all(sp[s] == ("you", "pendant") for s in mine)
    assert all(sp[s] == ("unsure", "panel") for s in partner)                                      # never "other" from this, and the runner-up is left as it was
    assert all(sp[s] == ("other", "panel") for s in owner_other + tagged + tv + mid)
    held = {r["segment_id"] for r in renv.db.rows("SELECT segment_id FROM omi_segment_embeddings")}
    assert not held & set(mine) and set(partner + mid) <= held                                     # the wearer's own voice is not held as a bystander's
    assert run(rp.relabel_stored_from_print(renv.db, OWNER))["relabelled"] == 0                    # idempotent
    assert run(rp.relabel_stored_from_print(renv.db, OWNER, min_cos=0.4))["relabelled"] == 1       # control: the bar is what keeps the half-similar line out


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


def _ledger_down_strict_only(monkeypatch):
    import memory_forgotten as mf

    async def down(user_id, text, *, near=True, strict=False):
        if strict:
            raise mf.LedgerUnavailable("down")
        raise RuntimeError("ledger read failed")           # main's non-strict call swallows ANY failure
    monkeypatch.setattr(mf, "spans", down)


def test_first_pass_fails_closed_on_an_unreadable_ledger_only_while_the_repass_flag_is_on(renv, monkeypatch):
    """Review #1: flag ON - forgotten text must never be stored, so an unreadable ledger stores no transcript (left for a retry). Flag OFF - exactly main: fail open."""
    _ledger_down_strict_only(monkeypatch)
    first_pass(renv)
    r = transcribe(renv, monkeypatch, "Dana called")
    assert "Dana" not in (r["transcript"] or "") and r["status"] != "done"
    monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", "off")
    renv.db.conn.execute("UPDATE omi_recordings SET status = 'pending', attempts = 0")
    renv.db.conn.commit()
    r = transcribe(renv, monkeypatch, "Dana called")
    assert r["status"] == "done" and r["transcript"] == "Dana called"


def test_a_completed_member_forget_and_entity_erase_remove_work_files_of_any_age(renv, tmp_path):
    import re as _re
    import omi_repass_eval as eu
    home = tmp_path / "repass"
    (home / "work").mkdir(parents=True)
    for n in ("a", "b"):
        (home / "work" / f"{n}.manifest.json").write_text('{"items": [{"id": "r1", "hint": "Dana"}]}')
        (home / "work" / f"{n}.out.jsonl").write_text('{"id": "r1", "words": ["Dana"]}')
    eu.erase_entity(home, "m1", _re.compile("Dana"), "[forgotten]")
    assert list((home / "work").iterdir()) == []
    (home / "work" / "c.out.jsonl").write_text("{}")
    eu.forget_user(home, "m1")
    assert list((home / "work").iterdir()) == []


def test_flag_off_redaction_stays_fail_open_even_when_the_ledger_modules_cannot_be_imported(renv, monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "memory_forgotten", None)          # import raises ImportError
    assert run(omi._redact_forgotten("u1", "Dana called")) == "Dana called"
    with pytest.raises(Exception):
        run(omi._redact_forgotten("u1", "Dana called", strict=True))


def test_entity_erase_rewrites_hypotheses_inside_the_gate_so_a_write_in_flight_cannot_survive_it(renv, tmp_path, monkeypatch):
    """Review: the hypothesis rewrite used to run BEFORE the erase gate, so a writer holding ``writing`` could land forgotten text after it."""
    import re as _re
    home = Path(rp.home_dir())
    (home / "hyps").mkdir(parents=True, exist_ok=True)
    f = home / "hyps" / "hyps-2026-11-10.jsonl"
    f.write_text("")

    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()

        async def writer():
            async with erase_gate.writing("m1", erase_gate.generation("m1")):
                entered.set()
                await release.wait()
                f.write_text(json.dumps({"u": "m1", "rid": "r1", "hyps": {"e": "Dana called"}, "rover": "Dana called", "slots": []}) + "\n")

        w = asyncio.ensure_future(writer())
        await entered.wait()
        er = asyncio.ensure_future(omi.erase_entity("m1", _re.compile("Dana"), strict=True))
        await asyncio.sleep(0.05)
        assert not er.done()                          # the erase is queued behind the in-flight write, not racing past it
        release.set()
        await w
        await er
    run(scenario())
    assert "Dana" not in f.read_text() and "[forgotten]" in f.read_text()


def test_shadow_mode_stores_no_wearer_proposal_but_on_mode_does(renv, monkeypatch):
    """Review: shadow "stores nothing and logs counts", yet the heuristic wearer proposal (biometric windows) was written in shadow too. Negative control: ``on`` still proposes."""
    def eight(rid):
        r = result_for(rid)
        r["windows"] = [[i * 0.75, i * 0.75 + 1.5, vec(0), 0.2] for i in range(8)]
        return r
    counts = {}
    for mode in ("shadow", "on"):
        monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", mode)
        renv.db.conn.execute("DELETE FROM omi_wearer_samples")
        renv.db.conn.execute("DELETE FROM omi_recordings")
        renv.db.conn.commit()
        transcribe(first_pass(renv) and renv, monkeypatch, "moonshine text")
        rid = rows(renv)[0]["id"]
        run(rp.run_night(budget_s=600, runner=fake_runner({rid: eight(rid)})))
        counts[mode] = renv.db.rows("SELECT COUNT(*) AS n FROM omi_wearer_samples")[0]["n"]
    assert counts["shadow"] == 0 and counts["on"] > 0, counts


def test_shadow_mode_stores_no_channel_mean_but_on_mode_does(renv, monkeypatch):
    """Review: a long-enough session persisted the pendant channel mean (biometric) in shadow too. Negative control: ``on`` stores it."""
    def long_result(rid):
        r = result_for(rid)
        r["speech_s"] = 700.0
        return r
    counts = {}
    for mode in ("shadow", "on"):
        monkeypatch.setenv("ZOE_OMI_NIGHT_REPASS", mode)
        renv.db.conn.execute("DELETE FROM omi_channel_means")
        renv.db.conn.execute("DELETE FROM omi_recordings")
        renv.db.conn.commit()
        transcribe(first_pass(renv) and renv, monkeypatch, "moonshine text")
        rid = rows(renv)[0]["id"]
        run(rp.run_night(budget_s=600, runner=fake_runner({rid: long_result(rid)})))
        counts[mode] = renv.db.rows("SELECT COUNT(*) AS n FROM omi_channel_means")[0]["n"]
    assert counts["shadow"] == 0 and counts["on"] > 0, counts


def test_a_worker_load_failure_names_the_engine_and_a_redacted_reason(tmp_path, monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("omi_repass_worker_loud", Path(__file__).resolve().parents[3] / "scripts/night/jobs/omi_repass_worker.py")
    w = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(w)
    secret = "a" * 40

    def boom(t, g):
        raise RuntimeError(f"This CTranslate2 package was not compiled with CUDA support at {Path.home()}/x token={secret}")
    monkeypatch.setitem(w.ASR, "whisper-gpu:large-v3", boom)
    rc = w.run({"asr": "whisper-gpu:large-v3", "diar": "none", "items": []}, str(tmp_path / "out.jsonl"))
    err = capsys.readouterr().err
    assert rc == 3 and "whisper-gpu:large-v3" in err and "RuntimeError" in err and "compiled with CUDA" in err
    assert secret not in err and str(Path.home()) not in err


def test_worker_redaction_withholds_household_text_and_short_secrets_and_keeps_setup_reasons():
    import importlib.util
    spec = importlib.util.spec_from_file_location("omi_repass_worker_red", Path(__file__).resolve().parents[3] / "scripts/night/jobs/omi_repass_worker.py")
    w = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(w)
    assert w.redact_exc(RuntimeError("failed /srv/family/Alice-medical-notes password is hunter2")) == "(message withheld)"      # negative control: household text + a short secret
    assert w.redact_exc(RuntimeError("Alice said call Bob at the clinic")) == "(message withheld)"
    out = w.redact_exc(OSError("libcudart.so.12: cannot open shared object file /opt/x/lib https://u:pw@h/x"))
    assert "cannot open shared object" in out and "/opt" not in out and "pw@" not in out
    assert "compiled with CUDA" in w.redact_exc(RuntimeError("This CTranslate2 package was not compiled with CUDA support"))                # control: the real reason survives
