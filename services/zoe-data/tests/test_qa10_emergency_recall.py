"""QA round 10a, emergency memory: a RECALL QUESTION about the past is answered from memory (E1), and a present emergency the converter
declines is not filed as a standing fact (E4). The spoken floor for a real emergency is unchanged: every present-relevant turn still
gets it, and where in doubt the floor fires. Synthetic phrasings only; negative control per claim (ci_safe)."""
from __future__ import annotations

import asyncio
import logging

import pytest

import medical_safety
from test_qa9_memory import UID, _rows, mem  # noqa: F401 - the MemoryService over the where-honouring Chroma stand-in

pytestmark = pytest.mark.ci_safe


def run(coro):
    return asyncio.run(coro)


# ── E1: both sides of the floor ───────────────────────────────────────────────────────────────────────────────────────────────
#: QA round 10a recorded these three (shape kept, names generic): each got "Ring 000 now" instead of an answer from memory
QA10A_RECALL = ["did i have chest pain recently", "has my partner had chest pain", "have i ever had chest pain"]
RECALL_QUESTIONS = QA10A_RECALL + [
    "did i have chest pain?", "did i have chest pain last month?", "have i ever had chest pain?", "has dad ever had chest pain",
    "did my wife have chest pain last week", "had i had chest pain before", "did my son have trouble breathing last year",
    "hey zoe, did i have chest pain in march?", "did grandma have chest pain at christmas", "have i had chest pain before?",
    "did i ever have crushing chest pain", "did my husband have chest pain when he was younger", "um did i have chest pain last year",
    "has my mum ever had chest pain?", "ok, did my brother have chest pain in 2019?",
]
#: present relevance anywhere = the floor (QA round 10a's own present turns and its two past-tense mentions included)
FLOOR = [
    "i have crushing chest pain right now", "i had chest pain earlier today but its gone now", "have i had chest pain today?",
    "ive had chest pain since lunch", "my chest hurts, did i have a heart attack?", "did i just have chest pain",
    "i have chest pain. have i had this before?", "im having chest pain, have i had this before", "have i had chest pain again",
    "did you know i have chest pain", "do i have chest pain", "does my wife have chest pain", "did i have chest pain and i cant breathe",
    "has my wife had chest pain this morning", "my wife has chest pain, did she have it last week", "have i had chest pain all day",
    "is my chest pain still there", "she has chest pain, has she had it before", "did i have chest pain tonight", "chest pain",
    "have i had chest pain at the moment", "did i have chest pain and shortness of breath", "hey zoe, i have chest pain",
    "my partner has chest pain and is sweating a lot",
]
FALL_FLOOR = ["i fell over in the shower and cant get up", "my brother fell and cant get up", "she fell over and cant get up"]


@pytest.mark.parametrize("text", RECALL_QUESTIONS)
def test_a_recall_question_about_the_past_does_not_get_the_floor(text):
    assert medical_safety.detect(text).cls == ""


@pytest.mark.parametrize("text", RECALL_QUESTIONS)
def test_control_without_the_recall_gate_every_one_of_them_fires(text):
    assert medical_safety._detect(text, recall_gate=False).cls == "red_call"        # the round-10a over-trigger, reproduced


@pytest.mark.parametrize("text", FLOOR + FALL_FLOOR)
def test_any_present_relevance_keeps_the_floor(text):
    assert medical_safety.detect(text).cls == "red_call"


def _quiet_floor(monkeypatch):
    import distress_handoff

    async def no(*_a, **_k):
        return False
    monkeypatch.setattr(distress_handoff, "_is_minor", no)
    monkeypatch.setattr(medical_safety, "_intercom_reachable", no)
    monkeypatch.delenv("ZOE_MEDICAL_SAFETY", raising=False)


def test_the_withheld_floor_logs_a_fixed_code_and_never_the_words(monkeypatch, caplog):
    _quiet_floor(monkeypatch)
    with caplog.at_level(logging.INFO, logger="medical_safety"):
        assert run(medical_safety.handle(QA10A_RECALL[1], UID)) is None
    assert "MEDICAL_SAFETY_RECALL_QUESTION" in caplog.text and "cls=red_call key=heart" in caplog.text
    assert "partner" not in caplog.text and "chest" not in caplog.text


def test_the_spoken_floor_still_answers_a_present_turn(monkeypatch):
    _quiet_floor(monkeypatch)
    for text in (FLOOR[0], FLOOR[2], FALL_FLOOR[0]):
        assert run(medical_safety.handle(text, UID))                                   # the fixed reply, not the brain


# ── E4: a present emergency the converter declines is not a standing fact ────────────────────────────────────────────────────
BYSTANDER = [
    ("Ida fell over in the garden and she cannot get up", "ida fell over in the garden and she cant get up"),
    ("User's wife has chest pain and is sweating a lot", "my wife has chest pain and is sweating a lot"),
    ("User's brother fell and cannot get up", "my brother fell and cant get up"),
]


@pytest.mark.parametrize("fact, said", BYSTANDER)
def test_a_declined_present_emergency_from_an_automatic_writer_is_not_filed(fact, said, mem, caplog):  # noqa: F811
    assert medical_safety.detect(said).cls == "red_call"                                 # the spoken reply is unchanged
    with caplog.at_level(logging.INFO, logger="memory_service"):
        run(mem.ingest(fact, user_id=UID, source="turn_digest", source_excerpt=said, anchor_text=said))
    assert _rows(mem) == []
    assert "MEDICAL_EVENT_UNFILED" in caplog.text and "garden" not in caplog.text and "wife" not in caplog.text


@pytest.mark.parametrize("fact, said", BYSTANDER)
def test_control_without_the_check_it_is_stored_as_a_standing_fact(fact, said, mem, monkeypatch):  # noqa: F811
    monkeypatch.setattr(medical_safety, "unfiled_emergency", lambda *_a, **_k: False)
    run(mem.ingest(fact, user_id=UID, source="turn_digest", source_excerpt=said, anchor_text=said))
    assert [r.text for r in _rows(mem)] == [fact]                                       # the round-10a defect


def test_the_members_own_emergency_is_still_one_dated_event_and_ordinary_facts_land(mem):  # noqa: F811
    run(mem.ingest("User fell over in the shower and cannot get up", user_id=UID, source="turn_digest",
                   source_excerpt=FALL_FLOOR[0], anchor_text=FALL_FLOOR[0]))
    run(mem.ingest("User had chest pain in 2020", user_id=UID, source="turn_digest",
                   source_excerpt="i had crushing chest pain in 2020", anchor_text="i had crushing chest pain in 2020"))
    texts = [r.text for r in _rows(mem)]
    assert "User had chest pain in 2020" in texts                                       # history keeps its own date, as mined
    events = [t for t in texts if t.startswith("User had a fall on ") and t.endswith("(a medical emergency).")]
    assert len(events) == 1 and len(texts) == 2


@pytest.mark.parametrize("fact, said", [("User's wife has chest pain", "my wife has chest pain"),
                                        ("User's brother fell and cannot get up", "my brother fell and cant get up")])
def test_pure_rule_is_the_converter_declining_a_present_emergency(fact, said):
    assert medical_safety.emergency_event(fact, said) is None and medical_safety.unfiled_emergency(fact, said)
    assert not medical_safety.unfiled_emergency("User fell over and cannot get up", "i fell over and cant get up")   # converted
    assert not medical_safety.unfiled_emergency("User had chest pain in 2020", "i had chest pain in 2020")          # history
    assert not medical_safety.unfiled_emergency("User's wife is a nurse", "my wife is a nurse")                     # no emergency
    assert not medical_safety.unfiled_emergency("User asked what to do if someone has chest pain",
                                                "what should i do if someone has chest pain")                       # hypothetical


# ── E3: two different emergencies on one day are two records; a true repeat is still one ────────────────────────────────────────
CHEST_NOW = "i have crushing chest pain right now"


def _file_both(mem):  # noqa: F811
    run(mem.ingest("User fell over in the shower and cannot get up", user_id=UID, source="turn_digest",
                   source_excerpt=FALL_FLOOR[0], anchor_text=FALL_FLOOR[0]))
    run(mem.ingest("User has crushing chest pain", user_id=UID, source="turn_digest", source_excerpt=CHEST_NOW, anchor_text=CHEST_NOW))
    return sorted(medical_safety.event_key(r.text)[1] for r in _rows(mem) if medical_safety.event_key(r.text))


def test_a_fall_and_chest_pain_on_one_day_both_file(mem):  # noqa: F811
    assert _file_both(mem) == ["a fall", "chest pain"]


def test_control_word_overlap_alone_drops_the_second_emergency(mem, monkeypatch):  # noqa: F811
    monkeypatch.setattr(medical_safety, "event_key", lambda _t: None)
    _file_both(mem)                                                                     # (it reads back by event_key: count rows)
    texts = [r.text for r in _rows(mem)]
    assert len(texts) == 1 and "a fall" in texts[0]                                    # the round-10a defect: chest pain dropped


def test_a_true_repeat_from_a_second_writer_still_dedups(mem):  # noqa: F811
    for source in ("turn_digest", "chat_regex"):
        run(mem.ingest("User fell over in the shower and cannot get up", user_id=UID, source=source,
                       source_excerpt=FALL_FLOOR[0], anchor_text=FALL_FLOOR[0]))
    assert len(_rows(mem)) == 1


def test_event_key_reads_only_the_fixed_template():
    assert medical_safety.event_key("User had a fall on 14 November 2026 (a medical emergency).") == ("user", "a fall", "14 November 2026")
    assert medical_safety.event_key("User's son Max had chest pain on 3 May 2026 (a medical emergency).")[0] == "user's son max"
    assert medical_safety.event_key("User had a fall on the stairs") is None and medical_safety.event_key("") is None
