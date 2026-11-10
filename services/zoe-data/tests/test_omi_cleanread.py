"""The CLEAN READ (``omi_repass_clean.py``) and the BOOK format (``omi_transcript_book.py``): a display twin that is only ever shown when a code guard proves it is the
verbatim minus fillers; every failure path falls back to the verbatim; nothing but the display reads it. Synthetic sentences only."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import datetime as dt
import importlib.util
import re
from pathlib import Path

import omi_repass_clean as oc
import omi_transcript_book as book
import omi_voices as ov
from test_omi_recordings import OWNER, env  # noqa: F401
from test_omi_repass import first_pass, renv, run, transcribe  # noqa: F401

V = "um so I I think we should go to the market on Saturday morning and then we can get some bread"


# ── the guard ──────────────────────────────────────────────────────────────────────────────────

def test_guard_accepts_the_verbatim_minus_fillers_and_ignores_case_punctuation_and_number_spelling():
    assert oc.guard(V, "So, I think we should go to the market on Saturday morning and then we can get some bread.").ok
    assert oc.guard("it's twenty one degrees today and clear", "It's 21 degrees today, and clear.").ok
    assert oc.guard(V, V).ok                                                                       # nothing dropped is fine


def test_guard_refuses_each_way_a_model_can_misbehave():
    assert oc.guard(V, "So we should visit the market on Saturday morning").reason == "not_subsequence"            # a word the speaker never said
    assert oc.guard(V, "go to the market so I think we should on Saturday morning").reason == "not_subsequence"    # reordered
    assert oc.guard(V, "We should go to the market").reason in ("too_many_dropped", "long_run_dropped")           # summarised
    long_clause = "we went there and then she said that it was far too late to bother so we left"        # 18 words
    assert oc.guard(long_clause, "we went there and left").reason == "too_many_dropped"                            # > 15%
    assert oc.guard(V, "").reason == "empty"
    assert oc.guard("", "invented").reason == "invented_words"


def test_guard_run_limit_is_independent_of_the_fraction():
    verbatim = " ".join(f"w{i}" for i in range(60))                    # 60 words: 15% is 9 words, so a 5-word hole passes the fraction and must fail the run
    hole = " ".join(f"w{i}" for i in list(range(0, 20)) + list(range(25, 60)))
    g = oc.guard(verbatim, hole)
    assert not g.ok and g.reason == "long_run_dropped" and g.dropped == 5 and g.frac < oc.MAX_DROP_FRAC
    scattered = " ".join(f"w{i}" for i in range(60) if i % 10 != 3)    # six single-word holes: under both limits
    assert oc.guard(verbatim, scattered).ok


# ── the deterministic cleaner ──────────────────────────────────────────────────────────────────

def test_deterministic_drops_fillers_and_repeats_and_keeps_everything_else():
    assert oc.deterministic("um I I think uh yes") == "I think yes"
    assert oc.deterministic("we we went to to the shop") == "we went to the shop"
    assert oc.deterministic("the the quick brown fox") == "the quick brown fox"
    assert oc.deterministic("it was like, really good") == "it was really good"            # "like," is a filler
    assert oc.deterministic("Like, I should go") == "I should go"                            # sentence-initial
    assert oc.deterministic("I like apples") == "I like apples"                              # the verb stays
    assert oc.deterministic("um okay") == "Okay"
    assert oc.det_ok(V, oc.deterministic(V)) and oc.guard(V, oc.deterministic(V)).ok


# ── one turn: the fallback chain ───────────────────────────────────────────────────────────────

def test_short_turns_never_reach_the_model():
    calls = []
    got = run(oc.clean_turn("um yes okay", lambda t: calls.append(t) or "x"))
    assert calls == [] and got["how"] == "det" and got["text"] == "Yes okay" and not got["violation"]


def test_a_good_answer_is_used_a_bad_one_falls_back_and_counts_a_violation():
    good = run(oc.clean_turn(V, lambda t: "So, I think we should go to the market on Saturday morning and then we can get some bread."))
    assert good["how"] == "llm" and good["text"].startswith("So, I think")
    bad = run(oc.clean_turn(V, lambda t: "Let's visit the market this weekend."))              # a paraphrase
    assert bad["how"] == "det_fb" and bad["violation"] and bad["reason"] == "not_subsequence" and oc.guard(V, bad["text"]).ok
    assert oc.marked(bad["how"]) and not oc.marked("llm") and not oc.marked("det")


@pytest.mark.parametrize("said,flipped", [
    ("I do not want to sell the house until next summer", "I do want to sell the house until next summer"),
    ("honestly I don't think we should go to the market on Saturday morning", "honestly I think we should go to the market on Saturday morning"),
    ("we are going to the shops with nobody else along on Saturday", "we are going to the shops with else along on Saturday"),
    ("I can go to the market and get bread but without any of the cheese today", "I can go to the market and get bread but any of the cheese today"),
])
def test_a_cleanup_that_deletes_a_negation_is_refused_and_falls_back(said, flipped):
    assert oc.guard(said, flipped).reason == "negation_dropped"
    r = run(oc.clean_turn(said, lambda t: flipped))
    assert r["how"] == "det_fb" and r["violation"] and r["reason"] == "negation_dropped" and oc.guard(said, r["text"]).ok
    assert run(oc.clean_turn(said, lambda t: said))["how"] == "llm"                            # control: the same sentence kept whole is accepted


def test_a_doubled_negation_counts_twice_and_a_contraction_is_one():
    said = "I will not not go to the market on Saturday morning with them"
    assert oc.guard(said, "I will not go to the market on Saturday morning with them").reason == "negation_dropped"
    r = run(oc.clean_turn(said, lambda t: "I will not go to the market on Saturday morning with them"))
    assert r["how"] != "llm" and r["violation"]
    assert oc.negations("I don't know, I can't say, never") == 3 and oc.negations("I know") == 0


def test_an_unreachable_model_degrades_without_counting_a_violation():
    def boom(t):
        raise ConnectionError("down")
    r = run(oc.clean_turn(V, boom))
    assert r["how"] == "det_fb" and not r["violation"] and r["reason"] == "ConnectionError"
    assert run(oc.clean_turn(V, None))["how"] == "det_fb"


def test_when_even_the_deterministic_twin_fails_the_guard_the_verbatim_is_shown_marked():
    everything_filler = "um um um um um um uh uh uh uh"          # the cleaner would delete the whole turn
    r = run(oc.clean_turn(everything_filler, lambda t: "something else entirely"))
    assert r["how"] == "raw_fb" and r["text"] == everything_filler and oc.marked(r["how"])


def test_the_request_is_one_deterministic_call_with_a_cap_that_fits_the_turn():
    req = oc.request(V, "m")
    assert req["temperature"] == 0 and req["messages"][-1]["content"] == V and req["max_tokens"] >= 2 * len(V.split())
    assert "must NOT change" in req["messages"][0]["content"]


def test_the_alert_needs_both_a_rate_above_20_percent_and_enough_turns_to_mean_it():
    def results(n_bad, n_good):
        return [{"how": "det_fb", "violation": True, "reason": "not_subsequence"}] * n_bad + [{"how": "llm", "violation": False, "reason": ""}] * n_good
    assert oc.summary(results(3, 12))["alert"] is False and oc.summary(results(3, 12))["violation_rate"] == 0.2     # exactly 20% is not above it
    assert oc.summary(results(4, 12))["alert"] is True and oc.summary(results(4, 12))["violation_rate"] == 0.25
    assert oc.summary(results(2, 10))["alert"] is False                        # 16.7%
    assert oc.summary(results(3, 2))["alert"] is False                         # too few turns to alert on
    unreachable = [{"how": "det_fb", "violation": False, "reason": "ConnectionError"}] * 30
    s = oc.summary(unreachable)
    assert s["alert"] is False and s["unreachable"] == 30 and s["violations"] == 0


# ── the store: display only, verbatim untouched ────────────────────────────────────────────────

def _segments(renv, monkeypatch, texts):
    first_pass(renv)
    rec = transcribe(renv, monkeypatch)
    segs = [{"start_ms": i * 3000, "end_ms": i * 3000 + 2500, "text": t, "speaker": "other", "score": None} for i, t in enumerate(texts)]
    run(ov.store_segments(renv.db, OWNER, rec["id"], segs, model_id="m", channel=None))
    return rec["id"]


def _clean_rows(renv):
    return renv.db.rows("SELECT text, clean_text, clean_how, not_person FROM omi_segments ORDER BY seq")


def test_run_writes_the_twin_beside_the_verbatim_and_never_touches_the_verbatim(renv, monkeypatch):
    _segments(renv, monkeypatch, [V, "um yes okay", "Hello there, how are you doing today"])
    seen = []

    async def llm(t):
        seen.append(t)
        return "So, I think we should go to the market on Saturday morning and then we can get some bread." if t == V else t
    out = run(oc.run(llm=llm))
    assert out["status"] == "ok" and out["turns"] == 3 and out["llm"] == 2 and out["short"] == 1 and out["violations"] == 0 and out["left"] == 0
    rows_ = _clean_rows(renv)
    assert [r["text"] for r in rows_] == [V, "um yes okay", "Hello there, how are you doing today"]                  # the verbatim is exactly as spoken
    assert [r["clean_how"] for r in rows_] == ["llm", "det", "llm"] and rows_[1]["clean_text"] == "Yes okay"
    assert seen == [V, "Hello there, how are you doing today"]                                                       # the 3-word turn never went to the model
    assert run(oc.run(llm=llm))["status"] == "nothing_to_do"                                             # idempotent: cleaned turns are not redone


def test_a_violation_is_stored_as_the_fallback_and_counted(renv, monkeypatch):
    _segments(renv, monkeypatch, [V])
    out = run(oc.run(llm=lambda t: "Totally different sentence about the weather outside"))
    r = _clean_rows(renv)[0]
    assert out["violations"] == 1 and r["clean_how"] == "det_fb" and r["text"] == V and oc.guard(V, r["clean_text"]).ok


def test_the_budget_stops_the_run_and_leaves_the_rest_for_tomorrow(renv, monkeypatch):
    _segments(renv, monkeypatch, [V, V + " again", V + " once more"])
    ticks = iter([0.0, 0.0, 99.0, 99.0, 99.0, 99.0, 99.0])
    out = run(oc.run(llm=lambda t: t, budget_s=10.0, now=lambda: next(ticks)))
    assert out["status"] == "no_time" and out["turns"] == 1 and out["left"] == 2
    assert [r["clean_how"] for r in _clean_rows(renv)].count("") == 2


def test_a_forgotten_name_is_redacted_in_the_twin_and_zoes_lines_never_reach_the_model(renv, monkeypatch):
    rid = _segments(renv, monkeypatch, ["um Zebulon said we should go to the market on Saturday morning", "Good evening, how can I help you with the list today"])
    renv.db.conn.execute("UPDATE omi_segments SET not_person = 'zoe' WHERE seq = 1")
    renv.db.conn.commit()
    sent = []

    async def llm(t):
        sent.append(t)
        return "Zebulon said we should go to the market on Saturday morning"
    import omi_recordings as omi

    async def redact(uid, text, **kw):
        return re.sub("Zebulon", "[forgotten]", text)
    monkeypatch.setattr(omi, "_redact_forgotten", redact)
    run(oc.run(llm=llm))
    rows_ = _clean_rows(renv)
    assert "Zebulon" not in rows_[0]["clean_text"] and "[forgotten]" in rows_[0]["clean_text"]
    assert len(sent) == 1 and rows_[1]["clean_how"] in ("det", "det_fb")                              # Zoe's own line: deterministic only
    assert rid


def test_an_entity_forget_redacts_the_twin_too(renv, monkeypatch):
    _segments(renv, monkeypatch, [V])
    renv.db.conn.execute("UPDATE omi_segments SET clean_text = 'we go to the market with Zebulon', clean_how = 'llm'")
    renv.db.conn.commit()
    run(ov.redact(renv.db, OWNER, re.compile("Zebulon"), "[forgotten]"))
    assert _clean_rows(renv)[0]["clean_text"] == "we go to the market with [forgotten]"


def test_the_readers_of_meaning_never_see_the_twin(renv):
    assert "clean_text" not in ov._SEG_COLS and "clean_how" not in ov._SEG_COLS             # public_segment / segments_for / the day review's loader
    root = Path(__file__).resolve().parents[1]
    for name in ("omi_day_review.py", "memory_digest.py", "night_mind.py", "omi_repass_eval.py", "omi_diarize.py"):
        assert "clean_text" not in (root / name).read_text(), name


# ── the book ───────────────────────────────────────────────────────────────────────────────────

def T(rec, a, b, name, text, clean="", how="", **kw):
    return {"rec": rec, "start_s": a, "end_s": b, "name": name, "text": text, "clean_text": clean, "clean_how": how, "is_zoe": kw.get("zoe", False), "lean": kw.get("lean")}


UTC = dt.timezone.utc
T0 = dt.datetime(2026, 11, 8, 2, 41, 0, tzinfo=UTC).timestamp()


def test_book_merges_same_speaker_turns_breaks_scenes_and_italicises_the_uncertain():
    turns = [T("r1", T0, T0 + 3, "Alex", "um hello there", "Hello there.", "det"),
             T("r1", T0 + 4, T0 + 6, "Alex", "how are you", "How are you?", "llm"),
             T("r1", T0 + 8, T0 + 10, None, "yeah okay", "Yeah, okay.", "det", lean="Sam"),
             T("r1", T0 + 11, T0 + 12, None, "hang on", "Hang on.", "det"),
             T("r1", T0 + 20, T0 + 22, None, "good evening", "Good evening.", "det", zoe=True),
             T("r1", T0 + 100, T0 + 103, "Alex", "much later", "Much later.", "det"),
             T("r2", T0 + 105, T0 + 107, "Alex", "new recording", "New recording.", "det")]
    md = book.render(turns, title="Sunday, 8 November 2026", tz=UTC)
    body = md.split("## Verbatim")[0]
    assert "**Alex:** Hello there. How are you?" in body                                                    # merged into one paragraph
    assert "**Someone (Sam?):** *Yeah, okay.*" in body and "**Someone:** *Hang on.*" in body                # uncertain: italics, no bracket tags
    assert "**Zoe:** Good evening." in body and "[" not in body.replace("[forgotten]", "")
    assert body.count("— ") == 3 and "— 2:41 am —" in body                                                   # recording start, the >60 s gap, and the next recording
    assert not re.search(r"\d\d:\d\d:\d\d", body)                                                           # no inline timestamps in the reading text
    verb = md.split("## Verbatim, with times")[1]
    assert "02:41:00 Alex: um hello there" in verb and "02:41:08 Someone (Sam?): yeah okay" in verb          # the appendix is the verbatim, with times


def test_book_marks_fallback_turns_and_never_merges_a_certain_with_an_uncertain_speaker():
    turns = [T("r", T0, T0 + 3, "Alex", "a b c d e f g", "a b c d e f g", "raw_fb"), T("r", T0 + 4, T0 + 6, None, "x y z", "x y z", "det", lean="Alex")]
    body = book.render(turns, tz=UTC).split("## Verbatim")[0]
    assert "a b c d e f g (lightly cleaned)" in body and "**Someone (Alex?):** *x y z*" in body and body.count("**") == 4
    # the control: a turn with no twin yet shows the verbatim, unmarked
    assert "plain words" in book.render([T("r", T0, T0 + 1, "Alex", "plain words")], tz=UTC).split("## Verbatim")[0]


def test_load_day_resolves_names_and_keeps_zoe_but_drops_media(renv, monkeypatch):
    rid = _segments(renv, monkeypatch, ["hello there my friend", "good evening", "tv noise here", "who is this"])
    c = renv.db.conn
    c.execute("CREATE TABLE IF NOT EXISTS people (id TEXT PRIMARY KEY, user_id TEXT, name TEXT)")
    c.execute("CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY, name TEXT)")
    c.execute("INSERT INTO people VALUES ('p1', ?, 'Sam')", (OWNER,))
    c.execute("INSERT INTO users VALUES (?, 'Alex')", (OWNER,))
    c.execute("UPDATE omi_segments SET speaker = 'you' WHERE seq = 0")
    c.execute("UPDATE omi_segments SET not_person = 'zoe' WHERE seq = 1")
    c.execute("UPDATE omi_segments SET not_person = 'tv' WHERE seq = 2")
    c.execute("UPDATE omi_segments SET suggested_person_id = 'p1', speaker = 'unsure' WHERE seq = 3")
    c.commit()
    day = dt.datetime.strptime(renv.db.rows("SELECT started_at FROM omi_recordings")[0][0], "%Y-%m-%dT%H:%M:%SZ").date()
    turns = run(book.load_day(renv.db, OWNER, day, UTC))
    assert [(t["name"], t["is_zoe"], t["lean"]) for t in turns] == [("Alex", False, None), (None, True, None), (None, False, "Sam")]
    assert "tv noise" not in book.render(turns, tz=UTC)
    assert rid


def test_guard_normalisation_matches_the_labs_measured_one_times_numbers_and_fillers_on_both_sides():
    assert oc.guard("it was nine twenty five a m and we left", "It was 9:25 a.m., and we left.").ok          # times and spoken numbers
    assert oc.guard("so you know we went to to the shops today after work", "So we went to the shops today after work.").ok   # "you know" and the stutter are free
    assert oc.request("some words here", "m")["chat_template_kwargs"] == {"enable_thinking": False}          # the lab's request shape
    assert oc.guard("we went to the shops", "we went to the shop").reason == "not_subsequence"             # a changed word is still refused
