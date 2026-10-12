"""Five memory defects found reviewing a live voice session (2026-10-12), each pinned with a break-the-fix control.

  1  a user-stated fact about a parent's health was dropped by the relationship validator ("seeing dad in the hospital")
  2  an inferred, unanchored consolidation row retired an older approved row (equal rank passed the authority wall)
  3  the voice "what do you know about me" summary: "46 people, newest someone", a happening filed as a place, a plan filed
     as a preference, "the user" kept in the third person, ~24 s of speech
  4  a row nothing the owner said supports surfaced in that summary
  5  "Who's <unknown name>?" was answered "I don't have any information" with NO recall pass

Synthetic names and facts only (no household text). Fakes only (fake Chroma, fake HTTP): ``ci_safe``.
"""
from __future__ import annotations

import asyncio
import json
import sys
import types

import pytest

import expert_dispatch
import memory_authority as auth
import memory_digest
import memory_gate as mg
import memory_quality as mq
import memory_reject_ledger as led
import memory_service
import person_recall_floor as prf
import provenance_answers as pa
import zoe_flue_client as zc
# the BM5 suite's fixtures: the fake store, the autouse clock / flag reset, the seed helper
from test_provenance_answers import NOW, UID, _clean, put, run, svc  # noqa: F401

pytestmark = pytest.mark.ci_safe

PARENT_TURN = ("I've just driven back from Hobart straight from seeing dad in the hospital so I'm a little bit tired "
               "but other than that I'm feeling all right")
PARENT_FACT = "User drove back from Hobart after seeing dad in the hospital"


# ── 1. the relationship validator ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("fact,turn", [
    (PARENT_FACT, PARENT_TURN),                                             # the live defect
    ("User's dad is in the hospital", "dad is in the hospital again"),
    ("User visited their mum on Sunday", "I visited Mum on Sunday and we had lunch"),
    ("User's mother is unwell", "mum is not well"),                          # a synonym of the bare word
    ("User saw their nan today", "went to see Nan today"),
    ("User's parent is in hospital", "I'm going to see dad at the hospital"),  # a generic role, a specific word
])
def test_a_parent_the_speaker_names_aloud_is_the_speakers_parent(fact, turn):
    assert mq.user_relationship_claim_unsupported(fact, turn) is False


@pytest.mark.parametrize("fact,turn", [
    ("User saw their dad on Sunday", "I saw her dad on Sunday"),            # somebody else's parent
    ("User saw their mum on Sunday", "I saw Tom's mum on Sunday"),
    ("User saw their dad on Sunday", "I saw the dad at the school gate"),   # a common noun
    ("Emily is the user's wife", "Emily is the wife"),                      # the 2026-07-12 incident: a role is never a bare name
    ("Emily is the user's boss", "Emily is the boss"),
    ("User's sister is unwell", "dad is unwell"),                           # a different relative
])
def test_the_guessed_anchor_is_still_refused(fact, turn):
    assert mq.user_relationship_claim_unsupported(fact, turn) is True


def test_the_turn_digest_keeps_a_stated_parent_event_and_still_drops_a_guessed_one(monkeypatch, tmp_path):
    monkeypatch.setenv("ZOE_MEMORY_REJECT_LEDGER", str(tmp_path / "ledger.json"))
    led.reset_for_tests()

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps([{"fact": self.fact, "type": "fact"}])}}]}

    class _Client:
        def __init__(self, fact):
            _Resp.fact = fact

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **k):
            return _Resp()

    class _Svc:
        def __init__(self):
            self.ingested = []

        async def ingest(self, text, **kw):
            self.ingested.append(text)
            return None

        async def search(self, *a, **k):
            return []

    stub = types.ModuleType("zoe_agent")

    async def _blob(*a, **k):
        return ""

    stub._mempalace_load_user_facts = _blob
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)

    def digest(fact, turn):
        s = _Svc()
        monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
        monkeypatch.setattr(memory_digest.httpx, "AsyncClient", lambda *a, **k: _Client(fact))
        asyncio.run(memory_digest.run_turn_digest("demo-user", turn, session_id="s1"))
        return s.ingested

    assert digest(PARENT_FACT, PARENT_TURN) == [PARENT_FACT]
    # control: the same fact from a turn that never names the parent is the extractor guessing the anchor
    assert digest(PARENT_FACT, "I've just driven back from Hobart and I'm a little bit tired") == []
    led.reset_for_tests()


# ── 2. inference never supersedes ───────────────────────────────────────────────────────────

OLD = "User is seeing their dad who is in hospital."
NEW = "The user has a father who was in the hospital."
UNRELATED_TURN = "I drove home and then put the washing on"


def _legacy_row(svc, text=OLD):
    """A row written before provenance existed: an idle-consolidation write, no class stamped (it reads back as the lowest rank)."""
    mid = "zoe_demo_legacy_row"
    svc._col.rows[mid] = (text, {
        "user_id": UID, "wing": UID, "status": "approved", "source": "idle_consolidation", "added_by": "idle_consolidation",
        "memory_type": "fact", "added_ts": NOW - 86400 * 10, "added_at": "2026-09-28T13:02:10Z"})
    return mid


def _store(svc, anchor, monkeypatch, old_id):
    async def reconcile(*_a, **_k):
        return "update", old_id

    monkeypatch.setattr(mq, "reconcile_for_ingest", reconcile)
    return run(expert_dispatch._ingest_or_supersede(
        svc, NEW, user_id=UID, source="idle_consolidation", session_id="s9", user_turn_id="t9", memory_type="fact",
        confidence=0.8, tags=["idle"], anchor_text=anchor))


def _status(svc, mid):
    return svc._col.rows[mid][1].get("status")


def test_an_unanchored_consolidation_row_does_not_retire_an_older_row(svc, monkeypatch, caplog):
    old_id = _legacy_row(svc)
    caplog.set_level("INFO")
    assert _store(svc, UNRELATED_TURN, monkeypatch, old_id) == "stored"
    assert _status(svc, old_id) == "approved", "no word of the owner's supports the new row: it has no standing over any row"
    new = [m for i, (t, m) in svc._col.rows.items() if t == NEW]
    assert new and new[0]["authority_basis"] == "unanchored" and new[0]["status"] == "approved"      # kept beside it, not lost
    text = caplog.text
    assert "MEMORY_SUPERSEDE source=" not in text and "MEMORY_SUPERSEDE_REFUSED" in text
    assert "AUTHORITY_BLOCKED" in text and "action=supersede" in text


def test_an_anchored_row_still_retires_the_row_it_corrects(svc, monkeypatch):
    """Control: the wall stops the unsupported, not the supported. The owner's own sentence carries the new fact."""
    old_id = _legacy_row(svc)
    said = "my dad has been discharged, he was in the hospital but he is home now. I have a father who was in the hospital."
    assert _store(svc, said, monkeypatch, old_id) == "stored"
    new = [m for t, m in svc._col.rows.values() if t == NEW]
    assert new and new[0]["authority_basis"] != "unanchored"
    assert _status(svc, old_id) == "superseded"


def test_a_user_stated_row_is_still_protected_from_an_inferred_one(svc, monkeypatch):
    """The original wall (rank): a row the owner said is never retired by a model row, anchored or not."""
    stated = put(svc, OLD, source="voice_fact")
    assert stated is not None
    assert _store(svc, UNRELATED_TURN, monkeypatch, stated.id) == "stored"
    assert _status(svc, stated.id) == "approved"


def test_has_standing_is_about_the_basis_not_the_rank():
    assert auth.has_standing(auth.RANK[auth.USER_STATED], "unanchored") is True            # a direct statement
    assert auth.has_standing(auth.RANK[auth.USER_STATED_DERIVED], "anchored_user_turn") is True
    assert auth.has_standing(auth.RANK[auth.MODEL_FROM_TURN], "unanchored") is False
    assert auth.has_standing(auth.RANK[auth.MODEL_FROM_TRANSCRIPT], "no_user_evidence") is True     # no words offered: says nothing
    assert auth.has_standing(auth.RANK[auth.MODEL_FROM_TRANSCRIPT], "automatic_writer") is True   # unchanged: not this rule's business


def test_a_per_turn_edit_without_support_is_refused_not_applied(svc):
    """review(edit) is the digest's supersede path: the same standing rule, so an unsupported per-turn row cannot rewrite one."""
    old = put(svc, "User lives in Hobart", source="digest")
    assert old is not None
    got = run(svc.review(old.id, decision="edit", edits="User lives in Darwin", actor="turn_digest",
                         anchor_text="we talked about the weather for a bit"))
    assert got is None and _status(svc, old.id) == "approved"


# ── 3 + 4. the voice summary ────────────────────────────────────────────────────────────────

def _ep(svc, text, **kw):
    return put(svc, text, source="turn_digest", excerpt=kw.pop("excerpt", None) or text, **kw)


def test_a_happening_is_not_a_place_and_a_plan_is_not_a_preference():
    assert pa.group_of("User enjoys being back in their house") == "other"
    assert pa.group_of("The user plans to eat food and relax") == "other"
    assert pa.group_of("User is going to eat out tonight") == "other"
    assert pa.group_of("User drove back from Hobart after seeing a friend") != "places"
    # the standing forms stay where they were
    assert pa.group_of("User lives in Hobart") == "places"
    assert pa.group_of("User has a house in Fremantle") == "places"
    assert pa.group_of("User likes oat milk in coffee") == "preferences"
    assert pa.group_of("User walks the dog every morning at six") == "routines"


@pytest.mark.parametrize("text,want", [
    ("The user enjoys being back in their house.", "you enjoy being back in your house"),
    ("User plans to eat food and relax.", "you plan to eat food and relax"),
    ("User lives in Hobart", "you live in Hobart"),
    ("User's sister is named Marisol", "your sister is named Marisol"),
    ("The user has a father who was in the hospital.", "you have a father who was in the hospital"),
    ("User watches the footy on weekends", "you watch the footy on weekends"),
    ("User tries to run every morning", "you try to run every morning"),
    ("User drove back from Hobart", "you drove back from Hobart"),
    ("Dana and their kids came over", "Dana and their kids came over"),         # not about the owner: untouched
])
def test_second_person_throughout(text, want):
    assert pa.second_person(text) == want


def _known(svc):
    return run(pa.gather(UID, svc=svc))


def test_the_voice_summary_is_short_named_second_person_and_offers_more(svc):
    put(svc, "User's sister is named Marisol", source="chat_regex", excerpt="My sister Marisol is flying in.", when="2026-10-08T02:00:00Z")
    put(svc, "User lives in Hobart", excerpt="I live in Hobart now.", when="2026-10-02T02:00:00Z")
    put(svc, "User walks the dog every morning at six", excerpt="I walk the dog every morning at six.", when="2026-10-03T02:00:00Z")
    put(svc, "User likes oat milk in coffee", excerpt="I like oat milk in my coffee.", when="2026-10-04T02:00:00Z")
    # the shapes found live: an unnamed people-group row, a happening, a plan
    for i in range(30):
        put(svc, f"User's kids all love swimming lesson number {i}", excerpt=f"my kids all love swimming lesson number {i}", when="2026-10-05T02:00:00Z")
    put(svc, "The user enjoys being back in their house.", source="chat_regex", excerpt="it's good to be back in my house", when="2026-10-09T01:00:00Z")
    put(svc, "User plans to eat food and relax.", source="chat_regex", excerpt="I'm going to eat food and vegetate", when="2026-10-09T01:30:00Z")
    out = pa.render_summary(_known(svc), voice=True, now=NOW)
    low = out.lower()
    assert out.startswith("Here's the short version. I know ")
    assert "someone" not in low and "newest" not in low
    assert "marisol, your sister" in low                                  # the newest NAMED person, with the relation
    assert "the user" not in low and "user's" not in low and "their" not in low
    assert "you live in hobart" in low and "you walk the dog every morning at six" in low
    assert "you like oat milk in coffee" not in low                       # two examples are enough to hear
    assert "back in your house" not in low and "relax" not in low        # neither the happening nor the plan is a standing example
    assert pa.HANDOFF_VOICE in out
    assert len(out.split()) <= 56, f"{len(out.split())} words is ~{len(out.split()) / 2.5:.0f}s of speech"
    assert out.count(". ") + out.count("? ") <= 4                          # about three sentences, plus the offer to go on


def test_unnamed_people_group_rows_are_neither_counted_as_people_nor_named_as_the_newest(svc):
    put(svc, "User's sister is named Marisol", excerpt="My sister Marisol is flying in.", when="2026-10-08T02:00:00Z")
    for i in range(12):
        put(svc, f"User's kids all love swimming lesson number {i}", excerpt=f"my kids all love swimming lesson number {i}", when="2026-10-09T02:00:00Z")
    out = pa.render_summary(_known(svc), voice=True, now=NOW)
    assert "I know 1 person in your life, most recently Marisol, your sister." in out
    assert "someone" not in out.lower()


def test_an_unanchored_row_is_not_said_back(svc):
    put(svc, "User lives in Hobart", excerpt="I live in Hobart now.", when="2026-10-02T02:00:00Z")
    ghost = put(svc, "User keeps a boat at the marina", source="turn_digest", anchor_text="unrelated words about the weather",
                when="2026-10-09T02:00:00Z")
    assert ghost is not None and ghost.metadata["authority_basis"] == "unanchored"
    known = _known(svc)
    assert known.unconfirmed == 1 and all("boat" not in i.text for i in known.items)
    voice = pa.render_summary(known, voice=True, now=NOW)
    chat = pa.render_summary(known, voice=False, now=NOW)
    assert "boat" not in voice and "boat" not in chat and "marina" not in chat
    assert "couldn't tie to anything you said" in chat and "1 note" in chat   # said honestly, not said back


# ── 5. a named person always gets a recall pass ─────────────────────────────────────────────

@pytest.mark.parametrize("q,want", [
    ("Who's Owen?", ["Owen"]), ("Who is Owen", ["Owen"]), ("who is owen", ["Owen"]),
    ("Do you know Owen?", ["Owen"]), ("Tell me about Dana Whitfield", ["Dana Whitfield"]),
    ("What do you know about Owen?", ["Owen"]),
])
def test_an_identity_question_names_its_person(q, want):
    assert mg.person_identity_names(q) == want


@pytest.mark.parametrize("q", [
    "How are you?", "Who is he?", "Who are you?", "Who is the president?", "What's the weather in Perth?",
    "who is going to the party", "Who's my sister?", "Owen is here",
])
def test_not_an_identity_question_about_a_name(q):
    assert mg.person_identity_names(q) == []


@pytest.fixture
def known_people(monkeypatch):
    async def known(uid):
        return [("pid-dana", "Dana Whitfield")]

    async def pending(uid, cands):
        return []

    monkeypatch.setattr(prf, "_known_people", known)
    monkeypatch.setattr(prf, "_pending_entity_names", pending)
    monkeypatch.delenv(mg.PERSON_FLOOR_ENV, raising=False)


async def test_a_name_nothing_on_file_knows_still_runs_the_recall_pass(known_people, caplog):
    caplog.set_level("INFO")
    got = await prf.resolve_named_people("Who's Owen? How are you?", "demo-owner")
    assert got == [prf.NamedPerson("Owen", "", "unknown")]
    assert prf.focus_ids(got) == []
    line = next(r.getMessage() for r in caplog.records if "RECALL_FLOOR" in r.getMessage())
    assert "unknown=1" in line and "Owen" not in line                     # counts, never the name


async def test_a_known_person_is_still_linked_and_a_non_identity_question_is_unchanged(known_people):
    assert await prf.resolve_named_people("Who is Dana Whitfield?", "demo-owner") == [
        prf.NamedPerson("Dana Whitfield", "pid-dana", "people")]
    assert await prf.resolve_named_people("How are you?", "demo-owner") == []
    assert await prf.resolve_named_people("What's the weather in Perth?", "demo-owner") == []


async def test_the_seam_fetches_the_packet_for_an_unknown_name(known_people, monkeypatch):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    seen = []

    async def fake_fetch(user_id, msg, focus=None):
        seen.append((user_id, msg, [p.name for p in focus or ()]))
        return "## What I know about you\n- Owen is the user's neighbour. [mem:aaaa1111]"

    monkeypatch.setattr(zc, "_fetch_for_prompt_packet", fake_fetch)
    block = await zc._recall_context_block("Who's Owen? How are you?", "demo-owner")
    assert seen == [("demo-owner", "Who's Owen? How are you?", ["Owen"])]
    assert "Owen is the user's neighbour" in block
    seen.clear()
    assert await zc._recall_context_block("How are you?", "demo-owner") == "" and seen == []


async def test_the_floor_off_switch_still_reads_nothing(known_people, monkeypatch):
    monkeypatch.setenv(mg.PERSON_FLOOR_ENV, "off")
    assert await prf.resolve_named_people("Who's Owen?", "demo-owner") == []


# ── 6. a memory challenge always gets a recall pass, and an answer that stands on it ────────

CHALLENGES = [
    "I think you already know, we've already talked about it.",
    "Now we've talked already today about what's going on. Don't you remember?",
    "Do you remember what I said?", "I already told you about that", "I told you about it earlier",
    "as I said before", "Remember when I told you about the trip?", "We spoke about this yesterday",
    "You should already know that", "Didn't I tell you?",
    "no te acuerdas de lo que te dije?", "ya lo sabes",
]


@pytest.mark.parametrize("q", CHALLENGES)
def test_a_memory_challenge_is_recognised(q):
    assert mg.is_memory_challenge(q) is True
    # "Do you remember what I said?" is also a personal recall question: either way the recall floor claims it
    assert zc._recall_question_shape(q) in ("challenge", "personal")


@pytest.mark.parametrize("q", [
    "Tell me the meaning of life", "I told you to turn off the light", "You know, it's been a long week",
    "How are you going?", "Turn on the kitchen light", "", "What's the weather like?",
])
def test_not_a_memory_challenge(q):
    assert mg.is_memory_challenge(q) is False
    assert zc._recall_question_shape(q) != "challenge"


@pytest.fixture
def seam(monkeypatch):
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "1")
    calls = []
    state = {"packet": "## What I know about you\n- User drove back from Hobart. [mem:aaaa1111]"}

    async def fake_continuity(user_id, msg):
        calls.append((user_id, msg))
        return {"packet": state["packet"]}

    monkeypatch.setattr(zc, "_fetch_continuity_packet", fake_continuity)
    return calls, state


async def test_a_challenge_runs_a_recall_pass_and_carries_the_grounding_rule(seam):
    calls, _state = seam
    block = await zc._recall_context_block("Don't you remember? We've already talked about it.", "demo-owner")
    assert calls == [("demo-owner", "Don't you remember? We've already talked about it.")]
    assert "User drove back from Hobart" in block and zc._CHALLENGE_RULE in block
    assert block.rstrip().endswith(zc._RECALL_BLOCK_CLOSE)


async def test_a_challenge_with_nothing_stored_still_sends_the_rule_so_the_denial_is_grounded(seam):
    _calls, state = seam
    state["packet"] = ""
    block = await zc._recall_context_block("Don't you remember?", "demo-owner")
    assert zc._CHALLENGE_EMPTY in block and "Never describe something they said" in block


async def test_a_failing_recall_is_the_rule_alone_not_a_failed_turn(seam, monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("store down")

    monkeypatch.setattr(zc, "_fetch_continuity_packet", boom)
    block = await zc._recall_context_block("Don't you remember?", "demo-owner")
    assert zc._CHALLENGE_EMPTY in block and zc._CHALLENGE_RULE in block


async def test_the_challenge_floor_is_off_with_the_recall_flag_and_for_guests_and_ordinary_turns(seam, monkeypatch):
    calls, _ = seam
    assert await zc._recall_context_block("Tell me the meaning of life", "demo-owner") == ""
    assert await zc._recall_context_block("Don't you remember?", "") == ""
    monkeypatch.setenv("ZOE_SEAM_RECALL_INJECT", "0")
    assert await zc._recall_context_block("Don't you remember?", "demo-owner") == ""
    assert calls == []


# ── 10. no subjectless fact is stored ───────────────────────────────────────────────────────

TURN_A = "No, about how I'd just come back from Hobart after seeing my father in the hospital."
TURN_B = "It's been very stressful. His health went up and down. He's been in and out of ICU."


def test_a_pronoun_subject_is_settled_from_the_owners_own_recent_words():
    texts = [TURN_B, TURN_A]
    assert mq.subjectless_pronoun_fact("His health has gone up and down.")
    assert mq.resolve_subjectless_fact("His health has gone up and down.", texts) == "User's father's health has gone up and down."
    assert mq.resolve_subjectless_fact("He has been in and out of ICU.", texts) == "User's father has been in and out of ICU."
    assert mq.resolve_subjectless_fact("User lives in Hobart", texts) == "User lives in Hobart"      # not a pronoun fact: untouched


@pytest.mark.parametrize("fact,texts", [
    ("He has been in and out of ICU.", [TURN_B]),                                       # nobody was named
    ("She has been in and out of ICU.", [TURN_B, TURN_A]),                              # a father is not a "she"
    ("Her flight is late.", ["my sister and my mum are coming over"]),                  # two women: ambiguous, never guessed
    ("They are coming over.", [TURN_A]),                                                # "they" never resolves
    ("He is tired.", ["my dad and my brother came"]),                                   # two men
    ("His health went up and down.", []),
])
def test_an_unsettled_pronoun_fact_is_held(fact, texts):
    assert mq.resolve_subjectless_fact(fact, texts) is None


def _run_digest(monkeypatch, tmp_path, fact, turn, recent):
    monkeypatch.setenv("ZOE_MEMORY_REJECT_LEDGER", str(tmp_path / "ledger.json"))
    led.reset_for_tests()

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps([{"fact": fact, "type": "health"}])}}]}

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, *a, **k):
            return _Resp()

    class _Svc:
        def __init__(self):
            self.ingested = []

        async def ingest(self, text, **kw):
            self.ingested.append(text)
            return None

        async def search(self, *a, **k):
            return []

    stub = types.ModuleType("zoe_agent")

    async def _blob(*a, **k):
        return ""

    stub._mempalace_load_user_facts = _blob
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)

    async def recent_msgs(user_id, session_id, current, limit=6):
        return list(recent)

    import memory_extractor
    monkeypatch.setattr(memory_extractor, "_load_recent_user_messages", recent_msgs)
    s = _Svc()
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    monkeypatch.setattr(memory_digest.httpx, "AsyncClient", lambda *a, **k: _Client())
    asyncio.run(memory_digest.run_turn_digest("demo-user", turn, session_id="s1"))
    return s.ingested


def test_the_turn_digest_stores_the_pronoun_fact_about_the_person_not_about_he(monkeypatch, tmp_path):
    got = _run_digest(monkeypatch, tmp_path, "His health has gone up and down.", TURN_B, [TURN_A])
    assert got == ["User's father's health has gone up and down."]
    got = _run_digest(monkeypatch, tmp_path, "He has been in and out of ICU.", TURN_B, [TURN_A])
    assert got == ["User's father has been in and out of ICU."]


def test_the_turn_digest_holds_a_pronoun_fact_it_cannot_place_and_counts_it(monkeypatch, tmp_path):
    assert _run_digest(monkeypatch, tmp_path, "He has been in and out of ICU.", TURN_B, []) == []
    assert led.summary(24)["reasons"] == {"guard_subjectless_pronoun": 1}
    led.reset_for_tests()
