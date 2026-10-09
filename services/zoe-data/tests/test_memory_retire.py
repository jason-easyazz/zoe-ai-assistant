"""Quote-backed retirement (``memory_retire``, ZOE_QUOTE_RETIRE): a one-word change of state retires the right fact, with the
owner's own words attached (Samantha bar S10; docs/research/mempalace-deep-dive-2026-10-06.md section 6.3).

The judge (the brain's ``memory_retire`` tool on the chat lane, the per-turn digest on the voice lane) only ever supplies a NUMBER;
every wall is server-side. Each wall has its break-the-wall control: patch that one check out and the scenario it guards must
RETIRE the row it protects (a test that stays green with its wall removed measures nothing). Shapes mirror the ZMB S10x cells
(scripts/perf/zmb/scenarios/retirement.json), over the real ``MemoryService`` with the lab's in-memory collection.

Synthetic data only; no network, no model (the voice judge is scripted), no Postgres.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

import memory_retire as mr  # noqa: E402
from zmb import lab_driver  # noqa: E402

pytestmark = pytest.mark.ci_safe

U = "demo_bar_0a1b2c3d"
CELLO = "User plays the cello in a community orchestra on Tuesday evenings."
SISTER = "User's sister Odette plays the cello in a community orchestra on Tuesday evenings."
OAT = "User likes oat milk."
CAR = "User drives a blue Corolla."
SAID = "I gave up the cello."


@pytest.fixture
def lab(monkeypatch):
    monkeypatch.setenv("ZOE_QUOTE_RETIRE", "enforce")
    monkeypatch.delenv("ZOE_MEMORY_AUTHORITY", raising=False)
    svc = lab_driver.load_service()
    ls = lab_driver.LabService(svc, tag="retire")
    mr.reset_state()
    yield ls
    ls.close()
    mr.reset_state()


def run(coro):
    return asyncio.run(coro)


def seed(lab, *texts):
    for t in texts:
        run(lab.service.ingest(t, user_id=U, source="voice_fact", confidence=0.9))


def row(lab, fragment):
    hits = [(i, d, m) for i, (d, m) in lab.col.rows.items() if fragment in d]
    assert len(hits) == 1, (fragment, [h[1] for h in hits])
    return hits[0]


def status(lab, fragment):
    return row(lab, fragment)[2].get("status")


def attempt(lab, text, *, lane="chat", verified=None, brain=None, mode="enforce"):
    """One candidate state change through prepare -> a scripted judge -> decide. ``brain``: {"pick": n} | {"row_id": id}
    | {"top1": True} (a hostile judge that always says yes)."""
    brain = brain or {"top1": True}

    async def go():
        prep = await mr.prepare(lab.service, U, text, lane=lane, speaker_verified=verified, mode_override=mode)
        if prep.decision is not None:
            return prep.decision, []
        kw = {"pick": 1} if brain.get("top1") else dict(brain)
        return await mr.decide(lab.service, U, prep, mode_override=mode, **kw), [r.id for r in prep.candidates]
    return run(go())


# ── the mode ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,want", [(None, "shadow"), ("", "shadow"), ("shadow", "shadow"), ("enforce", "enforce"),
                                      ("1", "enforce"), ("true", "enforce"), ("off", "off"), ("0", "off"),
                                      ("sahdow", "shadow"), ("maybe", "shadow")])
def test_the_mode_defaults_to_shadow_and_a_typo_never_applies(monkeypatch, raw, want):
    if raw is None:
        monkeypatch.delenv("ZOE_QUOTE_RETIRE", raising=False)
    else:
        monkeypatch.setenv("ZOE_QUOTE_RETIRE", raw)
    assert mr.mode() == want


# ── the prefilter (deterministic: no cue, no candidates, no model call) ───────────────────────────

@pytest.mark.parametrize("sentence", [
    "I gave up the cello.", "I stopped running.", "I sold the Corolla.", "The goldfish died.", "I no longer go swimming.",
    "I cycle to work now.", "We bought a house.", "I got rid of the standing desk.", "I switched to tea.",
    "I stepped down as netball coach.", "I haven't painted in months, I gave it up.", "I quit smoking.",
    "I do not play any more."])
def test_a_plain_statement_of_change_opens_the_door(sentence):
    assert mr.pick_quote(sentence) is not None


@pytest.mark.parametrize("sentence", [
    "I saw a cello today.", "I walked to the shops.", "What is the capital of Portugal?", "Did I give up the cello?",
    "Should I stop running?", "I will sell the Corolla next year.", "I almost gave up the cello but kept going.",
    "I am not giving up running.", "I haven't quit smoking.", "If I quit I would miss it.", "Maybe I'll give it up.",
    "Tea instead of coffee this morning.", "", "Quentin says: the goldfish died.", "I heard they sold the Corolla.",
    "The doctor said I should stop taking the tablets.", "My sister thinks I should quit the choir."])
def test_a_mention_a_question_a_plan_a_hedge_or_a_negated_change_opens_no_door(sentence):
    assert mr.pick_quote(sentence) is None


def test_the_quote_is_the_owners_verbatim_sentence_not_the_whole_turn():
    q = mr.pick_quote("The weather was awful. I gave up the cello. Anyway, dinner?")
    assert q == ("I gave up the cello.", "gave up")
    assert q[0] in "The weather was awful. I gave up the cello. Anyway, dinner?"


def test_the_quote_is_capped():
    long = "I gave up the cello " + "and everything else " * 40 + "."
    assert len(mr.pick_quote(long)[0]) <= mr.QUOTE_MAX


# ── effect: the S10 shape ─────────────────────────────────────────────────────────────────────────

def test_s10_the_owners_row_is_retired_with_their_sentence_and_nothing_else_changes(lab):
    seed(lab, CELLO, SISTER, OAT, CAR)
    d, offered = attempt(lab, SAID, brain={"pick": 1})
    assert d.action == "retired" and d.cue == "gave up" and d.lane == "chat"
    rid, doc, meta = next((i, dd, m) for i, (dd, m) in lab.col.rows.items() if dd == CELLO)
    assert meta["status"] == "superseded" and meta["retire_quote"] == SAID and meta["retired_by"] == "quote_retire"
    assert meta["retire_lane"] == "chat" and meta["retire_cue"] == "gave up" and meta["retire_turn_ref"].startswith("qr-")
    assert meta["invalid_at"] and meta["expired_at"] and doc == CELLO         # the two timelines; the text is never touched
    assert [status(lab, f) for f in ("sister Odette", "oat milk", "Corolla")] == ["approved"] * 3
    assert rid in lab.col.rows                                                   # never deleted


def test_the_retired_row_is_history_not_current(lab):
    seed(lab, CELLO, OAT)
    attempt(lab, SAID, brain={"pick": 1})
    now = [r.text for r in run(lab.service.search("do I play the cello", user_id=U, limit=5))]
    assert CELLO not in now
    _rid, _doc, meta = next((i, d, m) for i, (d, m) in lab.col.rows.items() if d == CELLO)
    mid = (float(meta["valid_from"]) + float(meta["invalid_at"])) / 2
    then = [r.text for r in run(lab.service.search("do I play the cello", user_id=U, limit=5, as_of=mid))]
    assert CELLO in then                                                         # as_of a moment before the change: still true


def test_a_retired_row_is_never_offered_again(lab):
    seed(lab, CELLO, OAT)
    assert attempt(lab, SAID, brain={"pick": 1})[0].action == "retired"
    cello_id = next(i for i, (d, _m) in lab.col.rows.items() if d == CELLO)
    _d, offered = attempt(lab, SAID, brain={"pick": 0})
    assert cello_id not in offered and status(lab, "plays the cello") == "superseded"


# ── a mention, a question and the judge's "none" retire nothing ───────────────────────────────────

def test_a_mention_retires_nothing_even_with_a_judge_that_always_says_yes(lab):
    seed(lab, CELLO, OAT)
    for text in ("I saw a cello today.", "I played the cello last night.", "Did I give up the cello?"):
        d, offered = attempt(lab, text, brain={"top1": True})
        assert d.action == "nothing_to_offer" and offered == [] and d.reason == mr.R_NO_CUE
    assert status(lab, "plays the cello") == "approved"


def test_a_cue_that_ends_nothing_reaches_the_judge_which_says_none(lab):
    seed(lab, CELLO, OAT)
    d, offered = attempt(lab, "I bought a new cello strap.", brain={"pick": 0})
    assert d.action == "none" and len(offered) >= 1 and status(lab, "plays the cello") == "approved"


def test_break_the_prefilter_and_the_hostile_judge_retires_the_mention(lab, monkeypatch):
    seed(lab, CELLO, OAT)
    monkeypatch.setattr(mr, "_is_change_sentence", lambda s: "any" if s.strip() else None)
    d, _ = attempt(lab, "I saw a cello today.", brain={"top1": True})
    assert d.action == "retired"        # RED control: with the cue gate gone the naive rule retires the mentioned row


# ── (c) the speaker is the verified owner, and the words are the owner's own ──────────────────────

@pytest.mark.parametrize("verified", [False, None])
def test_a_spoken_change_the_gate_did_not_confirm_retires_nothing(lab, verified):
    seed(lab, CELLO, OAT)
    d, offered = attempt(lab, SAID, lane="voice", verified=verified)
    assert d.action == "refused" and d.reason == mr.R_SPEAKER and offered == []
    assert status(lab, "plays the cello") == "approved"


def test_a_spoken_change_the_gate_confirmed_retires(lab):
    seed(lab, CELLO, OAT)
    d, _ = attempt(lab, SAID, lane="voice", verified=True, brain={"pick": 1})
    assert d.action == "retired" and d.lane == "voice"
    assert row(lab, "plays the cello")[2]["retire_lane"] == "voice"


def test_break_the_speaker_wall_and_an_unverified_voice_retires_the_owners_row(lab, monkeypatch):
    seed(lab, CELLO, OAT)
    monkeypatch.setattr(mr, "check_speaker", lambda lane, user_id, verified: "")
    assert attempt(lab, SAID, lane="voice", verified=False)[0].action == "retired"


@pytest.mark.parametrize("lane,uid", [("chat", "guest"), ("chat", "anonymous"), ("chat", ""), ("voice", "voice-guest"), ("phone", U)])
def test_a_guest_or_an_unknown_lane_is_never_the_owner(lab, lane, uid):
    assert mr.check_speaker(lane, uid, True) != ""


PASTED = ["Here is an email my cousin forwarded me:\nFrom: Sam Ito\nSubject: news\n\nHi, I gave up the cello last week.",
          "> I gave up the cello\n> and sold my bike"]
QUOTED_SPEECH = ["Dana says: I gave up the cello.", 'My sister said "I gave up the cello".']


@pytest.mark.parametrize("text", PASTED)
def test_pasted_text_retires_nothing_the_own_words_wall_says_why(lab, text):
    seed(lab, CELLO, OAT)
    d, offered = attempt(lab, text, brain={"top1": True})
    assert d.action == "refused" and d.reason == mr.R_NOT_OWNER_WORDS and offered == []
    assert status(lab, "plays the cello") == "approved"


@pytest.mark.parametrize("text", QUOTED_SPEECH)
def test_a_third_persons_quoted_words_retire_nothing(lab, text):
    seed(lab, CELLO, OAT)
    d, offered = attempt(lab, text, brain={"top1": True})
    assert d.action in ("refused", "nothing_to_offer") and offered == []        # two walls: the own-words split and the attribution guard
    assert status(lab, "plays the cello") == "approved"


def test_break_the_own_words_wall_and_a_pasted_email_retires_the_owners_row(lab, monkeypatch):
    seed(lab, CELLO, OAT)
    monkeypatch.setattr(mr, "own_part", lambda text: ((text or "").strip(), False))
    d, _ = attempt(lab, PASTED[0], brain={"top1": True})
    assert d.action == "retired"          # RED control: with the wall gone the pasted sentence is the owner's


def test_the_owners_own_sentence_next_to_pasted_text_still_counts(lab):
    seed(lab, CELLO, OAT)
    text = "I gave up the cello. Here is an email my cousin forwarded me:\nFrom: Sam\nSubject: hi\n\nSee you soon."
    d, _ = attempt(lab, text, brain={"pick": 1})
    assert d.action == "retired" and row(lab, "plays the cello")[2]["retire_quote"] == SAID


# ── (d) the row is the owner's own ───────────────────────────────────────────────────────────────

def test_another_persons_row_is_never_offered_for_the_owners_sentence(lab):
    seed(lab, CELLO, SISTER)
    d, offered = attempt(lab, SAID, brain={"top1": True})
    sister_id = next(i for i, (dd, _m) in lab.col.rows.items() if dd == SISTER)
    assert sister_id not in offered and d.action == "retired" and status(lab, "sister Odette") == "approved"


def test_the_sisters_row_is_offered_when_the_sentence_names_her(lab):
    seed(lab, CELLO, SISTER)
    d, offered = attempt(lab, "Odette gave up the cello.", brain={"top1": True})
    assert any(lab.col.rows[i][0] == SISTER for i in offered)


def test_break_the_owner_row_wall_and_the_copy_is_offered_and_retired(lab, monkeypatch):
    seed(lab, SISTER)
    monkeypatch.setattr(mr, "subject_ok", lambda quote, row_text: True)
    d, _ = attempt(lab, SAID, brain={"top1": True})
    assert d.action == "retired" and status(lab, "sister Odette") == "superseded"


def test_a_row_of_another_users_is_never_a_candidate(lab):
    run(lab.service.ingest(CELLO, user_id="demo_bar_0f0f0f0f", source="voice_fact", confidence=0.9))
    seed(lab, OAT)
    d, offered = attempt(lab, SAID, brain={"top1": True})
    assert all(lab.col.rows[i][1].get("user_id") == U for i in offered)
    assert [m["status"] for dd, m in lab.col.rows.values() if dd == CELLO] == ["approved"]


@pytest.mark.parametrize("meta,ok", [
    ({"user_id": U, "status": "approved", "memory_type": "fact"}, True),
    ({"user_id": U, "status": "pending", "memory_type": "fact"}, False),
    ({"user_id": U, "status": "superseded", "memory_type": "fact"}, False),
    ({"user_id": U, "status": "approved", "memory_type": "emotional_moment"}, False),
    ({"user_id": U, "status": "approved", "memory_type": "state_change", "tags": "state_change"}, False),
    ({"user_id": "someone-else", "status": "approved", "memory_type": "fact"}, False),
    ({"user_id": U, "status": "approved", "memory_type": "fact", "authority_class": "operator"}, False),
])
def test_eligibility(meta, ok):
    assert mr.eligible(meta, CELLO, SAID, U) is ok


# ── (a) the judge's choice is one of the rows it was shown ──────────────────────────────────────────

def _four_cello_rows_and_a_colour(lab):
    seed(lab, CELLO, "User has a cello bow made of carbon.", "User bought a new cello string last week.",
         "User's favourite colour is green.")


def test_a_row_outside_the_three_offered_is_refused_server_side(lab):
    _four_cello_rows_and_a_colour(lab)
    colour_id = next(i for i, (dd, _m) in lab.col.rows.items() if "colour" in dd)
    d, offered = attempt(lab, SAID, brain={"row_id": colour_id})
    assert colour_id not in offered and d.action == "refused" and d.reason == mr.R_NOT_OFFERED
    assert status(lab, "colour") == "approved"


@pytest.mark.parametrize("pick", [4, 9, -1, 100, "x", True])
def test_a_number_outside_the_offered_range_is_refused(lab, pick):
    seed(lab, CELLO, OAT)
    d, _ = attempt(lab, SAID, brain={"pick": pick})
    assert d.action == "refused" and status(lab, "plays the cello") == "approved"


def test_a_made_up_row_id_is_refused(lab):
    seed(lab, CELLO, OAT)
    d, _ = attempt(lab, SAID, brain={"row_id": "zoe_demo_bar_0a1b2c3d_does_not_exist"})
    assert d.action == "refused" and status(lab, "plays the cello") == "approved"


def test_break_the_offered_rows_check_and_any_row_can_be_named(lab, monkeypatch):
    _four_cello_rows_and_a_colour(lab)
    colour_id = next(i for i, (dd, _m) in lab.col.rows.items() if "colour" in dd)
    monkeypatch.setattr(mr, "check_offered", lambda row_id, offered: bool(row_id))
    d, _ = attempt(lab, SAID, brain={"row_id": colour_id})
    assert d.action == "retired" and status(lab, "colour") == "superseded"


# ── shadow and off ───────────────────────────────────────────────────────────────────────────────

def test_shadow_logs_the_decision_and_applies_nothing(lab, caplog):
    seed(lab, CELLO, OAT)
    caplog.set_level(logging.INFO, logger="memory_retire")
    d, _ = attempt(lab, SAID, brain={"pick": 1}, mode="shadow")
    assert d.action == "shadow" and d.reason == "would_retire" and d.row_id
    meta = row(lab, "plays the cello")[2]
    assert meta["status"] == "approved" and "retire_quote" not in meta and "invalid_at" not in meta
    line = next(r.getMessage() for r in caplog.records if "QUOTE_RETIRE" in r.getMessage() and "action=shadow" in r.getMessage())
    assert "mode=shadow" in line and "rank=1" in line and "cue=gave up" in line
    assert "cello" not in line and "orchestra" not in line               # ids, ranks and counts only: never the household's text


def test_shadow_and_enforce_reach_the_same_decision_on_the_same_turn(lab, caplog):
    """S10, the operator's flip: the shadow log must name EXACTLY the row, rank and cue that enforce then retires - or shadow is no evidence
    for turning enforce on. Same store, same turn, same scripted judge; only the mode differs."""
    seed(lab, CELLO, OAT)
    caplog.set_level(logging.INFO, logger="memory_retire")
    shadow, _ = attempt(lab, SAID, brain={"pick": 1}, mode="shadow")
    assert status(lab, "plays the cello") == "approved"
    enforced, _ = attempt(lab, SAID, brain={"pick": 1}, mode="enforce")
    assert (shadow.action, enforced.action) == ("shadow", "retired")
    assert shadow.row_id and shadow.row_id == enforced.row_id
    assert status(lab, "plays the cello") == "superseded" and status(lab, "oat milk") == "approved"
    lines = {m: next(r.getMessage() for r in caplog.records if "QUOTE_RETIRE" in r.getMessage() and f"mode={m}" in r.getMessage())
             for m in ("shadow", "enforce")}

    def keyed(line, drop):
        return {k: v for k, v in (t.split("=", 1) for t in line.split() if "=" in t) if k not in drop}
    assert keyed(lines["shadow"], {"mode", "action", "reason"}) == keyed(lines["enforce"], {"mode", "action", "reason"})


def test_off_does_nothing_and_logs_nothing(lab, caplog):
    seed(lab, CELLO, OAT)
    caplog.set_level(logging.INFO, logger="memory_retire")
    d, _ = attempt(lab, SAID, brain={"pick": 1}, mode="off")
    assert d.action == "off" and status(lab, "plays the cello") == "approved"
    assert not [r for r in caplog.records if "QUOTE_RETIRE" in r.getMessage()]


def test_break_shadow_and_the_row_is_written(lab, monkeypatch):
    seed(lab, CELLO, OAT)
    real = mr.decide

    async def applying(svc, user_id, prep, **kw):
        kw["mode_override"] = "enforce"
        return await real(svc, user_id, prep, **kw)
    monkeypatch.setattr(mr, "decide", applying)
    attempt(lab, SAID, brain={"pick": 1}, mode="shadow")
    assert status(lab, "plays the cello") == "superseded"      # RED control: a shadow that writes is not a shadow


# ── forgetting ───────────────────────────────────────────────────────────────────────────────────

def test_a_sentence_naming_a_forgotten_entity_retires_nothing(lab):
    seed(lab, CELLO, OAT)
    import memory_tombstones
    memory_tombstones.add(U, "cello")
    try:
        d, offered = attempt(lab, SAID, brain={"pick": 1})
    finally:
        memory_tombstones.clear_all(U)
    assert d.action == "refused" and d.reason == mr.R_FORGOTTEN and offered == []


# ── the judge's answer ────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,n,want", [
    ('{"pick": 2}', 3, 2), ('{"pick":0}', 3, 0), ('Sure: {"pick": 1} done', 3, 1), ("2", 3, 2), ('{"pick": 4}', 3, 0),
    ('{"pick": -1}', 3, 0), ('{"pick": "2"}', 3, 0), ('{"pick": 1.5}', 3, 0), ('{"pick": true}', 3, 0), ("", 3, 0),
    ("I think the first one", 3, 0), ("[1, 2]", 3, 0), ('{"choice": 2}', 3, 0), ("12", 3, 0)])
def test_a_muddled_judge_answer_retires_nothing(raw, n, want):
    assert mr.parse_pick(raw, n) == want


def test_the_judge_prompt_shows_the_sentence_and_numbered_notes_only(lab):
    seed(lab, CELLO, OAT)
    prep = run(mr.prepare(lab.service, U, SAID, lane="chat"))
    p = mr.judge_prompt(prep.quote, prep.candidates)
    assert SAID in p and "1. " in p and "2. " in p and "JSON" in p
    assert U not in p                                                       # no id of any kind reaches the model


# ── the VOICE lane's judge: off the turn, deterministic checks first ────────────────────────────────

def _scripted(pick, calls):
    async def judge(quote, rows):
        calls.append((quote, [r.text for r in rows]))
        return pick(rows) if callable(pick) else pick
    return judge


def test_the_digest_judge_retires_with_a_verified_voice(lab):
    seed(lab, CELLO, OAT)
    calls = []
    d = run(mr.distill_turn(U, SAID, speaker_verified=True, svc=lab.service,
                            judge=_scripted(lambda rows: 1 + next(i for i, r in enumerate(rows) if r.text == CELLO), calls)))
    assert d.action == "retired" and len(calls) == 1 and calls[0][0] == SAID
    assert status(lab, "plays the cello") == "superseded"


@pytest.mark.parametrize("text,verified", [("I saw a cello today.", True), (SAID, False), (SAID, None),
                                           ("Dana says: I gave up the cello.", True)])
def test_the_digest_judge_costs_no_model_call_unless_every_deterministic_check_passed(lab, text, verified):
    seed(lab, CELLO, OAT)
    calls = []
    d = run(mr.distill_turn(U, text, speaker_verified=verified, svc=lab.service, judge=_scripted(1, calls)))
    assert calls == [] and d.action in ("nothing_to_offer", "refused")
    assert status(lab, "plays the cello") == "approved"


def test_the_digest_judge_says_none_or_fails_and_nothing_is_retired(lab):
    seed(lab, CELLO, OAT)
    assert run(mr.distill_turn(U, SAID, speaker_verified=True, svc=lab.service, judge=_scripted(0, []))).action == "none"
    d = run(mr.distill_turn(U, SAID, speaker_verified=True, svc=lab.service, judge=_scripted(None, [])))
    assert d.action == "refused" and d.reason == mr.R_JUDGE_FAILED and status(lab, "plays the cello") == "approved"


def test_the_digest_judge_never_raises(lab):
    async def boom(quote, rows):
        raise RuntimeError("model down")
    seed(lab, CELLO, OAT)
    assert run(mr.distill_turn(U, SAID, speaker_verified=True, svc=lab.service, judge=boom)).action == "refused"


def test_in_shadow_the_digest_judge_runs_but_writes_nothing(lab, monkeypatch):
    seed(lab, CELLO, OAT)
    monkeypatch.setenv("ZOE_QUOTE_RETIRE", "shadow")
    calls = []
    d = run(mr.distill_turn(U, SAID, speaker_verified=True, svc=lab.service, judge=_scripted(1, calls)))
    assert d.action == "shadow" and len(calls) == 1 and status(lab, "plays the cello") == "approved"


async def _inner(user_id, user_message, assistant_response="", *, session_id=None, source="turn_digest", speaker_verified=None):
    return {"new": 0}


def test_the_per_turn_digest_hands_a_spoken_turn_to_the_judge_and_a_typed_one_to_nobody(monkeypatch):
    import memory_digest
    seen = []

    async def distill(user_id, user_message, *, speaker_verified, **kw):
        seen.append((user_id, user_message, speaker_verified))
        return mr.Decision("shadow", "would_retire", lane="voice")
    monkeypatch.setattr(mr, "distill_turn", distill)
    digest = memory_digest._then_quote_retire(_inner)
    out = run(digest(U, SAID, "ok", source="voice_turn_digest", speaker_verified=True))
    assert seen == [(U, SAID, True)] and out == {"new": 0, "quote_retire": "shadow"}
    seen.clear()
    out = run(digest(U, SAID, "ok", source="turn_digest"))
    assert seen == [] and "quote_retire" not in out                       # the chat lane's judge is the brain, not the digest
    out = run(digest(U, SAID, source="voice_turn_digest"))                # the lane's real call shape (positional text, keyword source)
    assert seen == [(U, SAID, None)]


def test_the_per_turn_digest_result_survives_a_failing_judge(monkeypatch):
    import memory_digest

    async def inner(*a, **k):
        return {"new": 2}

    async def boom(*a, **k):
        raise RuntimeError("x")
    monkeypatch.setattr(mr, "distill_turn", boom)
    digest = memory_digest._then_quote_retire(inner)
    assert run(digest(U, SAID, source="voice_turn_digest", speaker_verified=True)) == {"new": 2}


def test_the_per_turn_digest_is_the_same_function_with_one_more_step_after_it():
    """``run_turn_digest`` keeps its own source, name and signature (the decorator wraps it; ``inspect`` follows ``__wrapped__``), so every
    reader of the digest - and the voice lane's call shape - is untouched."""
    import inspect
    import memory_digest
    fn = memory_digest.run_turn_digest
    assert fn.__name__ == "run_turn_digest" and fn.__wrapped__.__name__ == "run_turn_digest"
    assert "memory_disputes.queue_questions" in inspect.getsource(fn)
    assert list(inspect.signature(fn).parameters) == ["user_id", "user_message", "assistant_response", "session_id", "source", "speaker_verified"]


# ── the CHAT lane: the brain's tool, two calls ──────────────────────────────────────────────────────

def test_the_brain_tool_lists_the_notes_then_a_number_retires(lab):
    seed(lab, CELLO, OAT)
    mr.note_turn(U, SAID)
    listing = run(mr.handle(U, {}, svc=lab.service))
    assert "1) " in listing and CELLO in listing and "pick=0" in listing and SAID not in listing
    n = 1 + next(i for i, t in enumerate(_listed(listing)) if t == CELLO)
    out = run(mr.handle(U, {"pick": n}, svc=lab.service))
    assert "no longer true" in out and status(lab, "plays the cello") == "superseded"
    assert row(lab, "plays the cello")[2]["retire_quote"] == SAID


def _listed(listing):
    import re
    body = listing.split("? ", 1)[1].split(" -- ", 1)[0]
    return [p.strip() for p in re.split(r"\s*\d\) ", body) if p.strip()]


def test_the_brain_tool_pick_zero_changes_nothing(lab):
    seed(lab, CELLO, OAT)
    mr.note_turn(U, "I bought a new cello strap.")
    run(mr.handle(U, {}, svc=lab.service))
    assert run(mr.handle(U, {"pick": 0}, svc=lab.service)) == "Noted."
    assert status(lab, "plays the cello") == "approved"


def test_the_brain_tool_is_about_the_noted_turn_never_about_anything_the_brain_says(lab):
    seed(lab, CELLO, OAT)
    mr.note_turn(U, "I saw a cello today.")                  # the owner's actual sentence has no cue
    out = run(mr.handle(U, {"pick": 1, "quote": SAID, "text": SAID, "row_id": "x"}, svc=lab.service))
    assert out == "Noted." and status(lab, "plays the cello") == "approved"


def test_a_brain_tool_call_with_no_noted_turn_does_nothing(lab):
    seed(lab, CELLO, OAT)
    assert run(mr.handle(U, {"pick": 1}, svc=lab.service)) == "Noted." and status(lab, "plays the cello") == "approved"


def test_a_spoken_turn_is_never_retired_by_the_brain_tool(lab):
    seed(lab, CELLO, OAT)
    mr.note_turn(U, SAID, voice=True)
    assert run(mr.handle(U, {}, svc=lab.service)) == "Noted."
    assert run(mr.handle(U, {"pick": 1}, svc=lab.service)) == "Noted."
    assert status(lab, "plays the cello") == "approved"


def test_a_stale_noted_turn_is_ignored(lab, monkeypatch):
    seed(lab, CELLO, OAT)
    mr.note_turn(U, SAID)
    t = mr.time.monotonic()
    monkeypatch.setattr(mr.time, "monotonic", lambda: t + mr._TURN_TTL_S + 1)
    assert run(mr.handle(U, {"pick": 1}, svc=lab.service)) == "Noted." and status(lab, "plays the cello") == "approved"


def test_the_brain_tool_in_shadow_never_claims_a_change(lab, monkeypatch):
    seed(lab, CELLO, OAT)
    monkeypatch.setenv("ZOE_QUOTE_RETIRE", "shadow")
    mr.note_turn(U, SAID)
    run(mr.handle(U, {}, svc=lab.service))
    out = run(mr.handle(U, {"pick": 1}, svc=lab.service))
    assert "do not say you changed" in out and "no longer true" not in out and status(lab, "plays the cello") == "approved"


def test_the_brain_tool_off_is_inert(lab, monkeypatch):
    seed(lab, CELLO, OAT)
    monkeypatch.setenv("ZOE_QUOTE_RETIRE", "off")
    mr.note_turn(U, SAID)
    assert run(mr.handle(U, {"pick": 1}, svc=lab.service)) == "Noted." and status(lab, "plays the cello") == "approved"


def test_note_turn_is_a_noop_when_off_and_bounded_otherwise(monkeypatch):
    monkeypatch.setenv("ZOE_QUOTE_RETIRE", "off")
    mr.reset_state()
    mr.note_turn(U, SAID)
    assert mr._turns == {}
    monkeypatch.setenv("ZOE_QUOTE_RETIRE", "shadow")
    for i in range(mr._TURN_MAX + 10):
        mr.note_turn(f"user{i}", SAID)
    assert len(mr._turns) == mr._TURN_MAX
    mr.note_turn("", SAID)
    assert "" not in mr._turns
    mr.reset_state()


def test_the_intent_router_routes_memory_retire_and_the_dispatch_allowlist_has_it(lab, monkeypatch):
    import intent_router
    import memory_service
    from routers import system
    assert "memory_retire" in system._DISPATCHABLE_INTENTS
    seed(lab, CELLO, OAT)
    # (the `lab` fixture already points memory_service.get_memory_service at lab.service and UNDOES it on close; a second
    #  monkeypatch.setattr of the same name here restored the lab lambda AFTER that - leaving it in place for every later module)
    mr.note_turn(U, SAID)
    listing = run(intent_router.execute_intent(intent_router.Intent("memory_retire", {}), U))
    assert "1) " in listing
    n = 1 + next(i for i, t in enumerate(_listed(listing)) if t == CELLO)
    out = run(intent_router.execute_intent(intent_router.Intent("memory_retire", {"pick": n}), U))
    assert "no longer true" in out and status(lab, "plays the cello") == "superseded"


# ── the service's own door ─────────────────────────────────────────────────────────────────────────

def _cello_id(lab):
    return next(i for i, (d, _m) in lab.col.rows.items() if d == CELLO)


def test_the_service_refuses_what_it_should_even_when_called_directly(lab):
    seed(lab, CELLO, OAT)
    rid = _cello_id(lab)
    svc = lab.service
    assert not run(svc.retire_with_quote(U, rid, quote="", turn_ref="t", lane="chat"))             # no sentence, no retirement
    assert not run(svc.retire_with_quote(U, rid, quote=SAID, turn_ref="t", lane="telegram"))        # an unknown lane
    assert not run(svc.retire_with_quote("demo_bar_0f0f0f0f", rid, quote=SAID, turn_ref="t", lane="chat"))   # not their row
    assert not run(svc.retire_with_quote(U, "no-such-row", quote=SAID, turn_ref="t", lane="chat"))
    assert not run(svc.retire_with_quote(U, rid, quote="my card is 4111 1111 1111 1111", turn_ref="t", lane="chat"))   # a hard PII reject drops the evidence AND the retirement
    assert status(lab, "plays the cello") == "approved"
    assert run(svc.retire_with_quote(U, rid, quote=SAID, turn_ref="t", lane="chat", cue="gave up"))
    assert not run(svc.retire_with_quote(U, rid, quote=SAID, turn_ref="t", lane="chat"))           # already retired: a second call changes nothing


def test_the_service_never_retires_a_tombstone_a_candidate_or_an_operators_row(lab):
    svc = lab.service
    ids = {}
    for key, kw in (("tomb", dict(memory_type="state_change", tags=["state_change"])), ("op", dict(source="operator"))):
        ref = run(svc.ingest(f"User gave up a thing {key}.", user_id=U, source=kw.pop("source", "voice_fact"), confidence=0.9, **kw))
        ids[key] = ref.id
    for key, rid in ids.items():
        assert not run(svc.retire_with_quote(U, rid, quote=SAID, turn_ref="t", lane="chat")), key


def test_the_audit_row_names_the_retirement_and_never_the_sentence(lab):
    seed(lab, CELLO, OAT)
    audits = []

    async def keep(**kw):
        audits.append(kw)
    lab.service._append_audit = keep
    run(lab.service.retire_with_quote(U, _cello_id(lab), quote=SAID, turn_ref="t", lane="chat"))
    assert len(audits) == 1 and audits[0]["action"] == "supersede" and audits[0]["actor"] == "quote_retire"
    assert "cello" not in repr(audits[0]).lower()


# ── forgetting erases the cited sentence ──────────────────────────────────────────────────────────

def test_forgetting_an_entity_the_cited_sentence_names_leaves_no_retained_row(lab, monkeypatch):
    """The retired row's own text never names the cello; the sentence cited on it does. Forgetting 'cello' must take the row."""
    import intent_router
    # (the `lab` fixture already points memory_service.get_memory_service at lab.service and UNDOES it on close; a second
    #  monkeypatch.setattr of the same name here restored the lab lambda AFTER that - leaving it in place for every later module)
    seed(lab, "User plays in a community orchestra on Tuesday evenings.", OAT)
    orchestra = next(i for i, (d, _m) in lab.col.rows.items() if "community orchestra" in d)
    assert attempt(lab, SAID, brain={"row_id": orchestra})[0].action == "retired"
    assert status(lab, "community orchestra") == "superseded"
    run(intent_router.execute_intent(intent_router.Intent("memory_forget_entity", {"name": "cello"}), U))
    assert status(lab, "community orchestra") == "archived" and status(lab, "oat milk") == "approved"
