"""QA round 8 (2026-11-12): three memory faults a controlled chat test proved, each reproduced here with fakes.

1. A stated correction did not retire the old value: "sorry the dentist appointment is the 27th of november not the 20th" was read
   by no correction path (a date / ordinal / amount was never a correction VALUE), so "... on the 20th of November" and "... on the
   twentieth of November" stayed approved beside the new date and the brain answered the old one. A minute later the idle
   consolidation of the EARLIER session filed the old date again.
2. A stored fact was not used: "how old is eli" (typed without capitals) named nobody the recall floor could look up, so the stored
   "Eli: 9" never reached the brain ("I don't have any information about someone named Eli").
3. A stated amount was never stored: "ive got 13 and a half thousand saved for the caravan now" was split at the "and" inside the
   number, the amount was filed as someone else's and dropped - and the quote retirement retired the old amount, so no amount was
   left. The rows a turn writes are now never candidates for that turn's retirement.

Synthetic names only (Noor, Eli, Tam).
"""
from __future__ import annotations

import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import fact_subject  # noqa: E402
import memory_gate as mg  # noqa: E402
import memory_retire as mr  # noqa: E402
import person_recall_floor as prf  # noqa: E402
import value_correction as vc  # noqa: E402

pytestmark = pytest.mark.ci_safe

UID = "demo_qa8_00000001"
DENTIST_FIX = "sorry the dentist appointment is the 27th of november not the 20th"


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _fresh():
    vc.reset_state()
    yield
    vc.reset_state()


# -- 1. a correction of a date / amount / age reaches every stored phrasing ----------------------------------------------------------

def test_a_date_correction_is_read_and_rewrites_every_phrasing_of_the_old_date():
    c = vc.statement(DENTIST_FIX)
    assert c is not None and c.subject == "the dentist appointment"
    # the two phrasings two different writers left behind in the round
    assert vc.rewrite("User has a dentist appointment on the twentieth of November", c) == \
        "User has a dentist appointment on the 27th of November"
    assert vc.rewrite("User (Noor): has a dentist appointment on the 20th of November", c) == \
        "User (Noor): has a dentist appointment on the 27th of November"
    assert vc.rewrite("User has a dentist appointment on 20 November", c) == "User has a dentist appointment on 27 November"


@pytest.mark.parametrize("row", [
    "User has a dentist appointment on 27 November",           # already the new value
    "User has a dentist appointment on the 20th of December",  # another month: another appointment
    "User has a hair appointment on the 20th of November",     # another subject
    "User has a dentist appointment at 10:20",                 # a time is not a day
    "User has a dentist appointment on the 20th and a check-up on the 20th",   # two candidates: never guessed
])
def test_negative_controls_a_date_correction_leaves_other_rows_alone(row):
    assert vc.rewrite(row, vc.statement(DENTIST_FIX)) is None


@pytest.mark.parametrize("text,new,old", [
    ("actually eli is 9 not 8, he had his birthday last month", "9", "8"),     # a trailing clause after the pair
    ("my exam is on the 11th not the 4th", "on the 11th", "the 4th"),
    ("my flight is on the 3rd of december, not the 20th of november", "on the 3rd of december", "the 20th of november"),
    ("no the caravan fund is 13 and a half thousand not 12", "13 and a half thousand", "12"),
])
def test_quantity_pairs_are_corrections(text, new, old):
    c = vc.statement(text)
    assert c is not None and (c.new, c.old) == (new, old)


def test_ages_amounts_and_months_are_compared_by_value():
    eli = vc.statement("actually eli is 9 not 8, he had his birthday last month")
    assert vc.rewrite("Eli: 8", eli) == "Eli: 9"
    assert vc.rewrite("User's son Eli is 8", eli) == "User's son Eli is 9"
    assert vc.rewrite("User's son Eli is 8 years old", eli) == "User's son Eli is 9 years old"     # the live re-run's row
    fund = vc.statement("no the caravan fund is 13 and a half thousand not 12")
    # the old value said without its scale takes the new one's; a subject that comes last still matches a one-number row
    assert vc.rewrite("User has saved twelve thousand dollars for the caravan fund", fund) == \
        "User has saved 13 and a half thousand dollars for the caravan fund"
    assert vc.rewrite("Tam has 3 kids and saved 12 thousand for the caravan fund", fund) is None    # two numbers before the subject
    flight = vc.statement("my flight is on the 3rd of december, not the 20th of november")
    assert vc.rewrite("User has a flight on the 20th of November", flight) == "User has a flight on the 3rd of December"


@pytest.mark.parametrize("text", [
    "the party is at 7 not 8 i think",        # a clause in the value
    "the meeting is tomorrow not today",      # no quantity, no cue: a description
    "i was there at 7 not 8",
    "page 4 not 5",
])
def test_negative_controls_not_a_quantity_correction(text):
    assert vc.statement(text) is None


def test_the_old_shapes_still_read_the_same():
    assert vc.statement("actualy my exam is on friday not thursday")[1:3] == ("friday", "thursday")
    assert vc.statement("also kais 21 not 22")[1:3] == ("21", "22")


class _Row:
    def __init__(self, i, text):
        self.id, self.text, self.metadata = i, text, {"user_id": UID}


class _Svc:
    def __init__(self, *texts):
        self.rows = [_Row(f"r{i}", t) for i, t in enumerate(texts)]
        self.edits = []

    async def list_by_status(self, **_k):
        return list(self.rows)

    async def review(self, rid, decision=None, edits=None, actor=None, **_k):
        self.edits.append((rid, edits, actor))
        return object()


def _apply(svc, text, monkeypatch):
    import correction_apply
    import user_prefs

    async def opted_in(_u):
        return False

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_in)
    monkeypatch.setattr(correction_apply, "_is_unidentified", lambda _u: False)
    return run(vc.apply(text, UID, None, svc=svc))


def test_the_round_8_store_one_correction_supersedes_both_old_rows_as_the_users_words(monkeypatch):
    svc = _Svc("User has a dentist appointment on the twentieth of November",
               "User has a dentist appointment on the 20th of November",
               "User has a dentist appointment on 27 November",           # the digest's copy of the new value: untouched
               "User's son is named Eli")
    assert _apply(svc, DENTIST_FIX, monkeypatch) == 2
    assert [(rid, actor) for rid, _t, actor in svc.edits] == [("r0", "conversation_correction"), ("r1", "conversation_correction")]
    assert all("27th of November" in t for _r, t, _a in svc.edits)


def test_a_transcript_written_before_the_correction_does_not_file_the_old_value_again(monkeypatch):
    svc = _Svc("User has a dentist appointment on the 20th of November")
    stale = "User (Noor): has a dentist appointment on the 20th of November"
    assert vc.superseded_by_correction(UID, stale) is False          # nothing corrected yet: the fact stands
    assert _apply(svc, DENTIST_FIX, monkeypatch) == 1
    assert vc.superseded_by_correction(UID, stale) is True
    assert vc.superseded_by_correction(UID, "User has a dentist appointment on the 27th of November") is False
    assert vc.superseded_by_correction(UID, "User's son is named Eli") is False
    assert vc.superseded_by_correction("demo_qa8_other", stale) is False      # another member's correction is not this one's


def test_a_correction_that_arrives_before_the_old_fact_is_filed_still_blocks_it(monkeypatch):
    # stated 15 s before the correction, filed minutes after it (QA round 8 pace): nothing to rewrite yet, but the later pass must not store it
    svc = _Svc()
    assert _apply(svc, "sorry the dentist appointment is the 27th of november not the 20th", monkeypatch) == 0
    assert vc.superseded_by_correction(UID, "User has a dentist appointment on the 20th of November")
    assert not vc.superseded_by_correction(UID, "User has a dentist appointment on 27 November")
    assert not vc.superseded_by_correction("someone-else", "User has a dentist appointment on the 20th of November")


def test_idle_consolidation_drops_the_stale_value_and_keeps_the_rest(monkeypatch):
    import expert_dispatch
    import memory_digest
    import memory_idle_consolidation as mic
    import memory_quality
    import memory_service

    vc._remember(UID, vc.statement(DENTIST_FIX))

    class _Conn:
        async def fetch(self, q, *a):
            return [dict(role="user", content="my dentist appointment is on the 20th of november", at="2026-11-12T10:00:00+00:00"),
                    dict(role="assistant", content="Noted.", at="2026-11-12T10:00:02+00:00"),
                    dict(role="user", content="and my son eli is 8", at="2026-11-12T10:00:09+00:00")]

        async def execute(self, q, *a):
            return None

    class _Ctx:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, *a):
            return False

    async def extract(_text):
        return [{"fact": "User has a dentist appointment on the 20th of November"}, {"fact": "User's son is named Eli"}]

    stored = []

    async def ingest(_svc, text, **_kw):
        stored.append(text)
        return "stored"

    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", extract)
    monkeypatch.setattr(memory_quality, "is_storable_fact", lambda t: (True, ""))
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: object())
    monkeypatch.setattr(expert_dispatch, "_ingest_or_supersede", ingest)
    run(mic.consolidate_session("sess-old", UID, get_ctx=lambda: _Ctx()))
    assert stored == ["User's son is named Eli"]


# -- 2. a caseless question about a person Zoe holds facts on reaches the record -----------------------------------------------------

def test_a_caseless_question_offers_its_words_as_name_candidates():
    assert mg.person_candidate_names("how old is eli") == []              # the gap: no capitals, no candidate
    assert mg.person_caseless_candidates("how old is eli") == ["Eli"]
    assert mg.person_caseless_candidates("How old is eli?") == ["Eli"]
    assert "Dana Whitfield" in mg.person_caseless_candidates("when is dana whitfield coming")
    assert mg.person_caseless_candidates("How old is Eli?") == []          # capitals: the capitalised-run rule owns it


def test_the_floor_resolves_a_person_fact_entity_named_without_capitals(monkeypatch):
    seen = []

    async def known(_uid):
        return []                                  # Eli is no contact row: only "Eli: 9" (slug:eli)

    async def pending(uid, cands):
        seen.append(list(cands))
        return ["Eli"] if "Eli" in cands else []

    monkeypatch.setattr(prf, "_known_people", known)
    monkeypatch.setattr(prf, "_pending_entity_names", pending)
    monkeypatch.delenv(mg.PERSON_FLOOR_ENV, raising=False)
    assert run(prf.resolve_named_people("how old is eli", UID)) == [prf.NamedPerson("Eli", "", "memory")]
    assert seen == [["Eli"]]


def test_negative_control_a_caseless_question_naming_nobody_known_forces_nothing(monkeypatch):
    async def known(_uid):
        return []

    async def pending(uid, cands):
        return []

    monkeypatch.setattr(prf, "_known_people", known)
    monkeypatch.setattr(prf, "_pending_entity_names", pending)
    monkeypatch.delenv(mg.PERSON_FLOOR_ENV, raising=False)
    assert run(prf.resolve_named_people("how old is the house", UID)) == []


# -- 3. an amount with "and" in it stays the speaker's, and the new row is never the one retired -------------------------------------

def test_a_number_with_and_in_it_keeps_the_speakers_i():
    turn = "ive got 13 and a half thousand saved for the caravan now"
    fact = "User has saved thirteen and a half thousand for the caravan"
    assert fact_subject.attribute(fact, turn) == ("keep", fact)
    assert fact_subject.attribute("User has two hundred and fifty dollars saved", "we have two hundred and fifty dollars saved")[0] == "keep"


def test_negative_controls_a_real_clause_break_still_finds_the_other_person():
    assert fact_subject.attribute("User works nights", "my sister is a doctor and works nights") == \
        ("rewrite", "User's sister works nights")
    assert fact_subject._clauses("i bought 3 and 4 is hers") == ["i bought 3", "4 is hers"]


def test_quote_retire_never_offers_the_row_the_same_turn_wrote(monkeypatch):
    class _R:
        def __init__(self, i, t):
            self.id, self.text = i, t
            self.metadata = {"user_id": UID, "status": "approved", "memory_type": "profile", "authority_class": "user_stated"}

    new = _R("new", "User has saved thirteen and a half thousand for the caravan")
    old = _R("old", "User has saved about twelve thousand dollars for a caravan")

    class _S:
        async def search(self, *_a, **_k):
            return [new, old]

    monkeypatch.setattr(mr, "eligible", lambda *_a: True)
    assert [r.id for r in run(mr.candidates(_S(), UID, "ive got 13 and a half thousand saved for the caravan now"))] == ["new", "old"]
    assert [r.id for r in run(mr.candidates(_S(), UID, "ive got 13 and a half thousand saved for the caravan now",
                                            exclude_ids=("new",)))] == ["old"]


def test_the_digest_hands_its_written_rows_to_the_retirement_judge(monkeypatch):
    import memory_digest
    import user_prefs

    seen = {}

    async def distill(user_id, user_message, **kw):
        seen.update(kw)
        return mr.Decision("shadow", "would_retire", lane="chat")

    async def opted_in(_u):
        return False

    monkeypatch.setattr(mr, "distill_turn", distill)
    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_in)
    run(memory_digest._quote_retire_pass({"new": 1, "written_ids": ["row-new"]}, UID, "ive got 13 thousand now", "turn_digest", None))
    assert seen.get("exclude_ids") == ("row-new",)


@pytest.mark.parametrize("text,new,old", [("actually my savings is 13.5 thousand not 12 thousand", "13.5 thousand", "12 thousand"),
                                          ("my savings is $13,500 not $12,000", "$13,500", "$12,000"),
                                          ("my savings is $13,500 not $12,000, i checked the app", "$13,500", "$12,000")])
def test_a_money_correction_with_a_point_or_a_comma_is_a_correction(text, new, old):
    corr = vc.statement(text)
    assert corr is not None and (corr.new, corr.old) == (new, old)


def test_correcting_back_keeps_the_latest_value(monkeypatch):
    """the 27th not the 20th, then the 20th not the 27th: only the LATEST correction of a subject stands, so the 20th is not stale."""
    svc = _Svc("User has a dentist appointment on the 20th of November")
    _apply(svc, DENTIST_FIX, monkeypatch)
    assert vc.superseded_by_correction(UID, "User has a dentist appointment on the 20th of November") is True     # control
    _apply(svc, "sorry the dentist appointment is the 20th of november not the 27th", monkeypatch)
    assert vc.superseded_by_correction(UID, "User has a dentist appointment on the 20th of November") is False
    assert vc.superseded_by_correction(UID, "User has a dentist appointment on the 27th of November") is True


def test_an_age_correction_with_years_old_never_rewrites_another_number_of_that_person():
    for text in ("eli is 9 years old not 8 years old, he had his birthday", "eli is 9 years old not 8"):
        corr = vc.statement(text)
        assert corr is not None and (corr.new, corr.old) == ("9", "8")
        assert vc.rewrite("Eli has 8 dogs", corr) is None                    # a count is not his age
        assert vc.rewrite("Eli is 8 years old", corr) == "Eli is 9 years old"


def test_a_chain_of_corrections_keeps_every_old_value_stale(monkeypatch):
    """20th -> 27th -> 28th: a transcript written before either correction must not refile the 20th OR the 27th."""
    svc = _Svc("User has a dentist appointment on the 20th of November")
    _apply(svc, DENTIST_FIX, monkeypatch)
    _apply(svc, "sorry the dentist appointment is the 28th of november not the 27th", monkeypatch)
    assert vc.superseded_by_correction(UID, "User has a dentist appointment on the 20th of November") is True
    assert vc.superseded_by_correction(UID, "User has a dentist appointment on the 27th of November") is True
    assert vc.superseded_by_correction(UID, "User has a dentist appointment on the 28th of November") is False


def test_a_date_correction_rewrites_the_date_and_never_another_number_of_that_subject():
    corr = vc.statement(DENTIST_FIX)
    assert vc.rewrite("User has a dentist appointment that costs 20 dollars", corr) is None
    assert vc.rewrite("User's dentist appointment is at 20 Example Street", corr) is None
    assert vc.rewrite("User has a dentist appointment on the 20th of November", corr) == "User has a dentist appointment on the 27th of November"
    assert vc.rewrite("User has a dentist appointment on 20 November", corr) == "User has a dentist appointment on 27 November"
    assert vc.rewrite("User has a dentist appointment on November 20", corr) == "User has a dentist appointment on November 27"
    money = vc.statement("actually my savings is 13.5 thousand not 12 thousand")
    assert vc.rewrite("User checked their savings on the 12th of May", money) is None        # an amount never rewrites a day
    assert vc.rewrite("User's savings are 12 thousand dollars", money) == "User's savings are 13.5 thousand dollars"


def test_a_matching_number_of_another_kind_does_not_make_the_row_up_to_date():
    """27 dollars is not the 27th: the row still holds the stale DATE and is rewritten; and the mirror for an amount."""
    corr = vc.statement(DENTIST_FIX)
    assert vc.rewrite("User has a dentist appointment on the 20th that costs 27 dollars", corr) == \
        "User has a dentist appointment on the 27th of November that costs 27 dollars"     # the month SAID is kept (QA round 9)
    assert vc.rewrite("User has a dentist appointment on the 20th of November that costs 27 dollars", corr) == \
        "User has a dentist appointment on the 27th of November that costs 27 dollars"
    assert vc.rewrite("User has a dentist appointment on the 27th of November", corr) is None      # really up to date
    money = vc.statement("actually the gas bill is 27 dollars not 20 dollars")
    assert vc.rewrite("User's gas bill is 20 dollars, due on the 27th", money) == "User's gas bill is 27 dollars, due on the 27th"
    assert vc.rewrite("User's gas bill is 27 dollars", money) is None                              # really up to date


def test_only_a_number_of_the_corrections_unit_counts():
    """27 dollars not 20 dollars: a street number, a count and an age of 27 neither make the row current nor get rewritten."""
    money = vc.statement("actually the gas bill is 27 dollars not 20 dollars")
    row = "User's gas bill is 20 dollars, paid at 27 Example Street for 27 units, and Tam turned 27"
    assert vc.rewrite(row, money) == "User's gas bill is 27 dollars, paid at 27 Example Street for 27 units, and Tam turned 27"
    assert vc.rewrite("User's gas bill is 20 dollars for 20 units", money) == "User's gas bill is 27 dollars for 20 units"
    assert vc.rewrite("User's gas bill is 20, sent to 27 Example Street", money) == \
        "User's gas bill is 27, sent to 27 Example Street"                     # a unitless 20 is still the single candidate
    assert vc.rewrite("User's gas bill is 27 dollars at 20 Example Street", money) is None      # really up to date
    assert vc.rewrite("User's gas bill is 20 dollars after a 27 km drive", money) == "User's gas bill is 27 dollars after a 27 km drive"
    # the reverse: an age correction is not made current by a dollar amount, and never rewrites one
    age = vc.statement("actually eli is 9 years old not 8 years old")
    assert vc.rewrite("Eli is 8 years old and has 9 dollars", age) == "Eli is 9 years old and has 9 dollars"
    assert vc.rewrite("Eli has 8 dollars and is 9 years old", age) is None


def test_a_value_restated_after_the_correction_is_a_new_fact_not_the_stale_one(monkeypatch):
    """12 thousand, corrected to 13.5 thousand; LATER the member says it is 12 thousand again. Words said after the correction
    are never discarded by it; words said before it still are."""
    import time

    svc = _Svc("User's savings are 12 thousand dollars")
    before = time.time() - 60
    _apply(svc, "actually my savings is 13.5 thousand not 12 thousand", monkeypatch)
    later = time.time() + 60
    fact = "User's savings are 12 thousand dollars"
    assert vc.superseded_by_correction(UID, fact, said_at=before) is True       # mined from words older than the correction
    assert vc.superseded_by_correction(UID, fact, said_at=later) is False       # said again afterwards: the latest fact
    assert vc.superseded_by_correction(UID, fact) is True                       # no time known: unchanged behaviour


def _session_around_a_correction(monkeypatch, after_text="ok thanks, what is on tomorrow"):
    """[states the 20th] -> [corrects to the 27th, recorded as a correction at that moment] -> [chats on]. Invented data."""
    import datetime as dt
    import time

    svc = _Svc("User has a dentist appointment on the 20th of November")
    now = time.time()
    _apply(svc, DENTIST_FIX, monkeypatch)                 # the correction is recorded NOW
    at = lambda off: dt.datetime.fromtimestamp(now + off, tz=dt.timezone.utc)  # noqa: E731
    return [
        {"role": "user", "content": "my dentist appointment is on the 20th of november", "at": at(-120)},
        {"role": "assistant", "content": "Noted.", "at": at(-110)},
        {"role": "user", "content": DENTIST_FIX, "at": at(0)},
        {"role": "user", "content": after_text, "at": at(60)},
    ]


def test_a_fact_mined_from_words_before_a_mid_session_correction_is_stale(monkeypatch):
    """The session chats on after the correction: the session's LAST turn is newer than the correction, but the words the fact
    came from are older, so the old date is not filed again."""
    import memory_idle_consolidation as mic

    rows = _session_around_a_correction(monkeypatch)
    fact = "User has a dentist appointment on the 20th of November"
    said = mic._fact_said_at({"fact": fact}, fact, rows)
    assert said == rows[0]["at"].timestamp()                                           # the supporting turn, not the last one
    assert vc.superseded_by_correction(UID, fact, said_at=said) is True
    assert vc.superseded_by_correction(UID, fact, said_at=rows[-1]["at"].timestamp()) is False   # control: the old rule kept it


def test_a_fact_restated_after_the_correction_is_kept(monkeypatch):
    import memory_idle_consolidation as mic

    rows = _session_around_a_correction(monkeypatch, after_text="it is the 20th again")
    fact = "User has a dentist appointment on the 20th of November"
    said = mic._fact_said_at({"fact": fact, "quote": "it is the 20th again"}, fact, rows)
    assert said == rows[-1]["at"].timestamp()
    assert vc.superseded_by_correction(UID, fact, said_at=said) is False
    rows = _session_around_a_correction(monkeypatch, after_text="my dentist appointment is on the 20th of november")
    said = mic._fact_said_at({"fact": fact}, fact, rows)                               # no quote: the entailing turn
    assert said == rows[-1]["at"].timestamp()                                          # the NEWEST supporting turn wins


@pytest.mark.parametrize("row,want", [
    # (a)/(d) the subject's own amount already holds the new value: the 20 dollar fee is never rewritten
    ("User's gas bill is 27, with a 20 dollar late fee", None),
    ("User's gas bill is 27 dollars, with a 20 dollar late fee", None),
    # (b)/(c) the subject's own amount is the stale one: it is rewritten, the fee left alone
    ("User's gas bill is 20, with a 27 dollar late fee", "User's gas bill is 27, with a 27 dollar late fee"),
    ("User's gas bill is 20 dollars, with a 27 dollar late fee", "User's gas bill is 27 dollars, with a 27 dollar late fee"),
    # the subject's amount is neither value: another number holding the old one is not the subject's
    ("User's gas bill is 30 dollars, with a 20 dollar late fee", None),
    # a number of another unit is not the subject's value and is skipped
    ("User's gas bill due on the 3rd is 20 dollars", "User's gas bill due on the 3rd is 27 dollars"),
    # undecidable: a unitless number before the amount may be the subject's - left unchanged
    ("User's gas bill for flat 4 is 20 dollars", None),
])
def test_only_the_subjects_own_number_is_judged_and_rewritten(row, want):
    money = vc.statement("actually the gas bill is 27 dollars not 20 dollars")
    assert vc.rewrite(row, money) == want


def test_a_newer_entailing_restatement_beats_an_older_quoted_turn(monkeypatch):
    """The model quoted the EARLY turn, but the member restated the value after the correction: the fact is as new as the
    restatement, so it is kept. Control: with no restatement the quoted turn's time stands and the fact is stale."""
    import memory_idle_consolidation as mic

    fact = "User has a dentist appointment on the 20th of November"
    item = {"fact": fact, "quote": "my dentist appointment is on the 20th of november"}
    rows = _session_around_a_correction(monkeypatch, after_text="i have a dentist appointment on the 20th of November")
    said = mic._fact_said_at(item, fact, rows)
    assert said == rows[-1]["at"].timestamp()
    assert vc.superseded_by_correction(UID, fact, said_at=said) is False
    vc.reset_state()
    rows = _session_around_a_correction(monkeypatch)
    said = mic._fact_said_at(item, fact, rows)
    assert said == rows[0]["at"].timestamp()
    assert vc.superseded_by_correction(UID, fact, said_at=said) is True


def test_no_supporting_turn_falls_back_to_the_first_turn(monkeypatch):
    import memory_idle_consolidation as mic

    rows = _session_around_a_correction(monkeypatch)
    fact = "User has a dentist appointment on the 20th of November"
    said = mic._fact_said_at({"fact": fact, "quote": "words nobody said"}, fact, rows)
    assert said == rows[0]["at"].timestamp()
    assert vc.superseded_by_correction(UID, fact, said_at=said) is True
    assert mic._first_turn_epoch([]) is None and mic._first_turn_epoch([{"at": "not a time"}]) is None