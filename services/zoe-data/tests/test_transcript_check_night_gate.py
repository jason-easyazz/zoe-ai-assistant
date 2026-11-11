"""The transcript check's WRITE paths under the erase / forget / atomicity rules (follow-up to the Codex review of #2365). Synthetic names and text only.

  atomic   an answer's mutations (segment + transcript text, review reopen, item status) land together or not at all
  forget   a forgotten name is never stored in the queue; an unreadable ledger stores nothing (fail closed); negative controls
  who      the night pass's REAL ``who_items`` row shape (seg_id, t0, t1, mark, cands) reaches the queue as one ``who`` item
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import json

import omi_repass as rp
import transcript_check as tc
from test_omi_recordings import OWNER, _migrate, env, rows  # noqa: F401
from test_omi_repass import first_pass, renv, result_for, run, transcribe  # noqa: F401
from test_omi_repass_who import _ambiguous_night, _one_shot, _registry  # noqa: F401
from test_transcript_check import chunk, items, night, queued, tenv  # noqa: F401


# ── atomic answers ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_a_failure_after_the_text_is_corrected_leaves_the_text_and_the_item_untouched(queued, monkeypatch):
    env, rid = queued
    iid = items(env)[0]["id"]
    before = rows(env)[0]["transcript"]
    import omi_day_review as dr

    async def boom(*a, **k):
        raise RuntimeError("injected")
    monkeypatch.setattr(dr, "reopen_recording_in", boom)
    with pytest.raises(RuntimeError):
        run(tc.answer(OWNER, iid, alt=1))
    assert rows(env)[0]["transcript"] == before and "eleven" not in rows(env)[0]["transcript"]
    assert items(env)[0]["status"] == "open"                                              # not half-answered: it can be retried


def test_control_without_the_failure_the_same_answer_lands(queued):
    env, rid = queued
    run(tc.answer(OWNER, items(env)[0]["id"], alt=1))
    assert "eleven" in rows(env)[0]["transcript"] and items(env)[0]["status"] == "answered"


# ── forget: nothing forgotten reaches the queue ───────────────────────────────────────────────────────────────────────────────────────
def _forget(monkeypatch, word):
    import memory_forgotten as mf

    async def spans(uid, text, **kw):
        i = text.find(word)
        return [(i, i + len(word))] if i >= 0 else []
    monkeypatch.setattr(mf, "spans", spans)


def test_a_forgotten_name_is_never_stored_in_the_check_queue(tenv, monkeypatch):
    _forget(monkeypatch, "Marisol")
    night(tenv, monkeypatch, [chunk()])                                                    # the contested chunk's context names the forgotten person
    assert items(tenv) == []


def test_control_a_name_that_was_not_forgotten_is_queued(tenv, monkeypatch):
    _forget(monkeypatch, "Zebulon")
    night(tenv, monkeypatch, [chunk()])
    assert len(items(tenv)) == 1


def test_an_unreadable_forget_ledger_stores_nothing(tenv, monkeypatch):
    first_pass(tenv)
    rec = dict(rows(tenv)[0])
    import memory_forgotten as mf

    async def down(uid, text, **kw):
        raise mf.LedgerUnavailable("down")
    monkeypatch.setattr(mf, "spans", down)
    out = run(tc.enqueue(OWNER, rec, tc.candidates_from_row(chunk())))
    assert out["stored"] == 0 and items(tenv) == []


def test_control_a_readable_ledger_stores_the_same_candidates(tenv):
    first_pass(tenv)
    out = run(tc.enqueue(OWNER, dict(rows(tenv)[0]), tc.candidates_from_row(chunk())))
    assert out["stored"] == 1


# ── who: the real row shape ────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def wenv(renv, monkeypatch):
    _migrate(renv.engine, "0054_omi_day_review.py")
    _migrate(renv.engine, "0076_transcript_check.py")
    monkeypatch.setenv("ZOE_TRANSCRIPT_CHECK", "on")
    tc.reset()
    return renv


def test_the_night_passs_real_who_items_become_who_check_items(wenv, monkeypatch, tmp_path):
    c = _registry(wenv)
    import numpy as np
    from test_omi_repass_who import A_, B_, v
    for i, (pid, vec) in enumerate((("pa", A_), ("pb", B_), ("pc", v(0, 0, 0, 1)))):     # three registered people with panel prints, so there are people to offer
        c.execute("INSERT INTO learned_voiceprints VALUES (?, ?, ?, ?, 'panel', ?, 0, 1)", (f"w{i}", OWNER, pid, rp.MODEL_ID, np.asarray(vec, dtype=np.float32).tobytes()))
    c.commit()
    rid, res = _ambiguous_night(wenv, monkeypatch, tmp_path)
    out = run(rp.run_night(budget_s=600, runner=_one_shot(res, rid, [])))
    assert out["status"] == "ok" and out["replaced"] == 1
    import omi_repass_eval as eu
    real = eu.check_queue(tmp_path / "repass", OWNER, kind="who_said")                      # the rows exactly as the night wrote them
    assert real and set(real[0]) >= {"seg_id", "t0", "t1", "mark", "cands"}
    who = [r for r in items(wenv) if r["kind"] == "who"]
    assert len(who) == 1 and who[0]["status"] == "open"
    seg_text = wenv.db.rows("SELECT text FROM omi_segments WHERE id = ?", (real[0]["seg_id"],))[0]["text"]
    assert who[0]["span_text"] == " ".join(seg_text.split()) and who[0]["span_text"]          # the turn's text, resolved from its segment
    offered = {a["word"] for a in json.loads(who[0]["alts"])}
    assert offered and offered <= {"Alex Example", "Blake Example", "Casey Example"}         # candidates mapped to his own people by name ("you" is not a person)


def test_a_forgotten_person_is_not_offered_from_a_who_turn(wenv, monkeypatch, tmp_path):
    _registry(wenv)
    _forget(monkeypatch, "Example")                                                          # every candidate name holds the forgotten word
    rid, res = _ambiguous_night(wenv, monkeypatch, tmp_path)
    run(rp.run_night(budget_s=600, runner=_one_shot(res, rid, [])))
    who = [r for r in items(wenv) if r["kind"] == "who"]
    assert not any("Example" in a["word"] for r in who for a in json.loads(r["alts"]))      # the forgotten names are not offered (the turn itself may still ask "someone new")


# ── erasure reaches the owner's ANSWER; a correction clears the cleaned twin ────────────────────────────────────────────────────────────
def _forget_entity(name):
    import memory_forgotten
    import omi_voices
    rx = memory_forgotten.name_pattern(name)

    async def go():
        from db_compat import get_compat_db
        async with get_compat_db() as db:
            return await omi_voices.redact(db, OWNER, rx, "[forgotten]")
    return run(go())


def test_an_owner_supplied_name_that_is_later_forgotten_leaves_no_answered_row(queued):
    env, rid = queued
    iid = items(env)[0]["id"]
    run(tc.answer(OWNER, iid, word="Zebulon", via="voice"))                             # a name that was never in the original sentence
    assert items(env)[0]["answer"] == "Zebulon" and items(env)[0]["status"] == "answered"
    _forget_entity("Zebulon")
    assert items(env) == []                                                             # the answered row is gone, answer column included
    with pytest.raises(tc.CheckError) as e:                                             # and the state route has nothing to return
        run(tc.answer(OWNER, iid, word="Other"))
    assert e.value.status == 404


def test_control_forgetting_an_unrelated_name_keeps_the_answered_row(queued):
    env, rid = queued
    run(tc.answer(OWNER, items(env)[0]["id"], word="Zebulon", via="voice"))
    _forget_entity("Quillfeather")
    assert len(items(env)) == 1 and items(env)[0]["answer"] == "Zebulon"


def test_a_correction_clears_the_cleaned_twin_so_the_book_shows_the_corrected_verbatim(queued):
    env, rid = queued
    env.db.conn.execute("UPDATE omi_segments SET clean_text = 'Tell Marisol to meet me at seven.', clean_how = 'llm' WHERE recording_id = ?", (rid,))
    env.db.conn.commit()
    assert env.db.rows("SELECT clean_how FROM omi_segments WHERE recording_id = ?", (rid,))[0]["clean_how"] == "llm"
    run(tc.answer(OWNER, items(env)[0]["id"], alt=1))
    segs = env.db.rows("SELECT text, clean_text, clean_how FROM omi_segments WHERE recording_id = ?", (rid,))
    changed = [s for s in segs if "eleven" in s["text"]]
    assert changed and all(s["clean_text"] == "" and s["clean_how"] == "" for s in changed)   # the cleaner regenerates it from the corrected text
