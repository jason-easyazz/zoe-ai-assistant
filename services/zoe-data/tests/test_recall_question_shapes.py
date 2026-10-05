"""A recall QUESTION without a question mark is never a memory write (ZMB E1b).

The panel's speech-to-text types no "?". "do you remember who my dentist is" matched the
extractor's unanchored ``remember ...`` template and was stored as "User asked me to remember:
who my dentist is"; the write-quality gate then ACCEPTED it, because a "remember" command is
accepted early and the wrapper ("User asked me ...") is not an interrogative opener. Two
layers, both pinned here (synthetic names only, no network, no model):

  1. memory_extractor._remember_is_a_question: the template does not fire for a question;
  2. memory_quality.is_storable_fact: a wrapped question payload is refused, and the shared
     ``is_recall_question`` knows the punctuation-free shapes.

Controls: "remember that my dentist is Dr Quill" still stores; "remind me to call the dentist"
is still a reminder; a name that looks like an auxiliary ("Will is my brother") is still a fact.
"""
import asyncio

import pytest

import expert_dispatch
import memory_extractor
import memory_quality
import memory_service
from memory_quality import is_recall_question, is_storable_fact

pytestmark = pytest.mark.ci_safe

# the three sentences ZMB E1b / its siblings use, exactly as the panel delivers them
QUESTIONS = [
    "do you remember who my dentist is",
    "what did I say about the dentist",
    "who is my dentist",
]
# more unpunctuated shapes of the same class
MORE_QUESTIONS = [
    "hey zoe do you remember what my dentist is called",
    "can you remember where my dentist is",
    "you remember who my dentist is",
    "do you remember my dentist's name",
    "tell me who my dentist is",
    "can you tell me what my dentist is called",
    "let me know when my dentist appointment is",
    "i was wondering if you know who my dentist is",
    "remember who my dentist is",
    "what time is my dentist appointment",
    "so when did I last see the dentist",
]


def _texts(msg):
    return [c.text for c in memory_extractor.extract_candidates(msg, "")]


@pytest.mark.parametrize("msg", QUESTIONS + MORE_QUESTIONS)
def test_an_unpunctuated_question_yields_no_candidate(msg):
    assert _texts(msg) == []


@pytest.mark.parametrize("msg", QUESTIONS + MORE_QUESTIONS)
def test_the_shared_helper_knows_the_shape_without_a_question_mark(msg):
    assert "?" not in msg and is_recall_question(msg)


@pytest.mark.parametrize("payload", ["who my dentist is", "what I said about the dentist",
                                     "when my dentist appointment is", "whether I have a dentist"])
def test_the_gate_refuses_a_question_wrapped_in_the_teach_shape(payload):
    """The exact stored text of the E1b failure, and its siblings."""
    assert is_storable_fact(f"User asked me to remember: {payload}") == (False, "recall_question_payload")
    assert is_storable_fact(f"Important note: {payload}") == (False, "recall_question_payload")


@pytest.mark.parametrize("msg", [
    "remember that my dentist is Dr Quill",
    "please remember my dentist is Dr Quill",
    "remember my dentist is Dr Quill",
    "can you remember that my dentist is Dr Quill",          # a request to STORE, "that" is the marker
    "remember that I asked who's coming",                     # a wh-word inside a teach is still a teach
])
def test_controls_a_teach_still_stores(msg):
    texts = _texts(msg)
    assert texts, msg
    assert all(is_storable_fact(t)[0] for t in texts), texts


def test_the_teach_keeps_its_payload():
    assert _texts("remember that my dentist is Dr Quill") == ["User asked me to remember: my dentist is Dr Quill"]


def test_controls_a_reminder_is_still_a_reminder_and_never_a_memory():
    import intent_router

    assert _texts("remind me to call the dentist") == []
    assert intent_router.detect_intent("remind me to call the dentist").name == "reminder_create"


@pytest.mark.parametrize("text", ["Will is my brother", "User asked me to remember: Will is my brother",
                                  "my dentist is Dr Quill", "I remember my dentist is Dr Quill"])
def test_a_statement_is_not_mistaken_for_a_question(text):
    # a name that reads as an auxiliary ("Will is ...") has no subject after it: not an inversion
    assert not is_recall_question(text)


@pytest.mark.parametrize("text", ["User asked me to remember: Will is my brother",
                                  "my dentist is Dr Quill", "I remember my dentist is Dr Quill"])
def test_a_statement_still_passes_the_gate(text):
    assert is_storable_fact(text)[0]


# -- the write paths: nothing reaches MemoryService.ingest ---------------------------------------

class _Svc:
    def __init__(self):
        self.ingested = []

    async def ingest(self, text, **kw):
        self.ingested.append(text)
        return type("Ref", (), {"id": "m1", "text": text})()

    async def search(self, *a, **k):
        return []


@pytest.fixture
def svc(monkeypatch):
    s = _Svc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    recalled = []

    async def run_expert(domain, text, user_id, session_id):
        recalled.append(text)
        return "RECALLED"

    async def not_opted_out(_uid):
        return False

    monkeypatch.setattr(expert_dispatch, "_run_expert", run_expert)
    monkeypatch.setattr(memory_extractor, "_memory_opted_out", not_opted_out)
    s.recalled = recalled
    return s


@pytest.mark.parametrize("msg", QUESTIONS + MORE_QUESTIONS)
def test_extract_and_ingest_writes_nothing_for_a_question(svc, msg):
    n = asyncio.run(memory_extractor.extract_and_ingest(
        msg, user_id="demo_recall_user", source="voice_regex", prev_user_message=""))
    assert n == 0 and svc.ingested == []


@pytest.mark.parametrize("msg", QUESTIONS + MORE_QUESTIONS)
def test_store_fact_routes_a_question_to_recall_never_to_a_write(svc, msg):
    out = asyncio.run(expert_dispatch.store_fact("memory", msg, "demo_recall_user"))
    assert svc.ingested == [], msg
    assert svc.recalled == [msg] and out == "RECALLED"


def test_controls_store_fact_still_stores_a_teach(svc):
    out = asyncio.run(expert_dispatch.store_fact("memory", "remember that my dentist is Dr Quill",
                                                 "demo_recall_user"))
    assert any("Dr Quill" in t for t in svc.ingested)
    assert out and out.startswith("Got it")


def test_break_the_fix_control_the_template_is_what_stopped_it(monkeypatch):
    """With the question check removed the wrapped question comes back (so the tests above
    measure the guard, not an accident of the sentence)."""
    monkeypatch.setattr(memory_extractor, "_remember_is_a_question", lambda *_a: False)
    assert _texts("do you remember who my dentist is") == ["User asked me to remember: who my dentist is"]
    # ... and the gate is the second wall: with the extractor guard off, the gate still refuses it
    assert not is_storable_fact("User asked me to remember: who my dentist is")[0]
