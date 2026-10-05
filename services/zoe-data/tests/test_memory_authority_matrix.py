"""The writer x target-class matrix of the memory-authority wall (fidelity audit P1.1 / F9).

docs/research/memory-fidelity-audit-2026-10-05.md section 2.2 lists the 14 retire-or-replace
paths; this runs each of them through the REAL ``MemoryService`` choke point against a target
row of every provenance class and pins who may overwrite whom:

    a write may supersede / archive a row iff its class is a USER class (rank >= 3) or its rank
    is >= the row's; anything else is held back (the row stays approved).

Paths covered here: W1 regex extractor, W2 turn digest, W4 LLM person extractor, W6a/W6b nightly
digest (contradiction + reconcile are the same ``review(edit)``), W8 idle consolidation, W9 weekly
merge (``archive_duplicate``), W10 weekly contradiction judge, W11/W12 implicit supersede
(``supersede_by``), W16 decay sweep, W20 voice teach. W3-edge (relationship graph) is
``test_memory_authority_people.py``. W22 (MCP edit-as-the-user) is NOT covered: an agent acting
under the owner's id is, by construction, indistinguishable from the owner - a follow-up.

``ZOE_MEMORY_AUTHORITY=off`` is the negative control on every row (the S1 signature: the old row
is superseded and the new row is stamped by the real writer); ``shadow`` changes nothing but logs
``AUTHORITY_WOULD_BLOCK``. Synthetic data only (ci_safe).
"""
from __future__ import annotations

import asyncio
import logging

import pytest

import memory_authority as ma
import memory_digest
import memory_service
from memory_service import MemoryService
from test_memory_authority import _Col  # the where-honouring Chroma stand-in

pytestmark = pytest.mark.ci_safe

UID = "member-a"
OLD = "User's dog is named Teddy."
NEW = "User's dog is named Rex."
SAID = "my dog is called Rex"          # the user's own words supporting NEW
CLASSES = list(ma.CLASSES)


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.delenv("ZOE_MEMORY_AUTHORITY", raising=False)
    s = MemoryService(data_dir="/nonexistent/zoe-test-memory-authority-matrix")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    s._col = col
    return s


def seed(svc, cls, text=OLD, tag="a"):
    md = MemoryService._build_metadata(
        user_id=UID, source="seed", session_id=None, user_turn_id=None, memory_type="fact",
        confidence=0.9, status="approved", tags=[], entity_type=None, entity_id=None,
        expires_at=None, text=text)
    md.update(ma.provenance("seed", ma.Resolved(cls, "seed")))
    mem_id = f"seed-{tag}-{cls}"
    svc._col.upsert(ids=[mem_id], documents=[text], metadatas=[md])
    return mem_id


def status(svc, mem_id):
    return svc._col.rows[mem_id][1]["status"]


def allowed(writer_rank, target_cls):
    return writer_rank >= ma.USER_RANK or writer_rank >= ma.RANK[target_cls]


# (label, path, actor, extra kwargs, resulting writer class)
EDIT_PATHS = [
    ("W1 regex extractor", "chat_regex", {"source_excerpt": SAID}, ma.USER_STATED),
    ("W2 turn digest, user's words support it", "turn_digest", {"source_excerpt": SAID}, ma.USER_STATED),
    ("W2 turn digest, nothing supports it", "turn_digest", {}, ma.MODEL_FROM_TURN),
    ("W4 LLM person extractor (own origin), supported", "conversation",
     {"origin": "person_extractor_llm", "source_excerpt": SAID}, ma.USER_STATED),
    ("W4 LLM person extractor (own origin), unsupported", "conversation",
     {"origin": "person_extractor_llm"}, ma.MODEL_FROM_TURN),
    ("W6a/b nightly digest, verbatim user quote", "digest", {"anchor_text": SAID}, ma.USER_STATED),
    ("W6a/b nightly digest, no user evidence", "digest", {}, ma.MODEL_FROM_TRANSCRIPT),
    ("W8 idle consolidation, no user evidence", "idle_consolidation", {}, ma.MODEL_FROM_TRANSCRIPT),
    ("W10 weekly contradiction judge", "consolidation", {}, ma.MODEL_FROM_TRANSCRIPT),
    ("W20 voice teach", "voice_fact", {"source_excerpt": SAID}, ma.USER_STATED),
    ("W24 the account editing its own row", UID, {}, ma.USER_CONFIRMED),
    ("operator tool", "identity_audit", {}, ma.OPERATOR),
    ("unknown writer (fail-closed)", "some_new_writer", {}, ma.MODEL_FROM_TRANSCRIPT),
]


@pytest.mark.parametrize("target", CLASSES)
@pytest.mark.parametrize("label,actor,kw,wcls", EDIT_PATHS, ids=[p[0] for p in EDIT_PATHS])
def test_edit_matrix(svc, label, actor, kw, wcls, target):
    old = seed(svc, target)
    got = asyncio.run(svc.review(old, decision="edit", edits=NEW, actor=actor, **kw))
    if allowed(ma.RANK[wcls], target):
        assert got is not None and status(svc, old) == "superseded", (label, target)
        nm = got.metadata
        assert nm["origin"] == (kw.get("origin") or actor) and nm["authority_class"] == wcls
        assert "session_id" not in nm and nm["supersedes_id"] == old
    else:
        assert got is None and status(svc, old) == "approved", (label, target)
        cands = asyncio.run(svc.list_by_status(user_id=UID, status="disputed"))
        assert [c.text for c in cands] == [NEW] and cands[0].metadata["contradicts_id"] == old
        assert cands[0].metadata["authority"] == ma.INFERRED


@pytest.mark.parametrize("target", CLASSES)
@pytest.mark.parametrize("label,actor,kw,wcls", EDIT_PATHS, ids=[p[0] for p in EDIT_PATHS])
def test_edit_matrix_negative_control_off(svc, monkeypatch, label, actor, kw, wcls, target):
    """``off`` is the pre-fix behaviour: EVERY path supersedes (the S1 signature)."""
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "off")
    old = seed(svc, target)
    got = asyncio.run(svc.review(old, decision="edit", edits=NEW, actor=actor, **kw))
    assert got is not None and status(svc, old) == "superseded"


INGEST_WRITERS = [
    ("voice_fact", ma.USER_STATED), ("person_updated", ma.USER_STATED), ("note_created", ma.USER_STATED),
    ("turn_digest", ma.MODEL_FROM_TURN), ("digest", ma.MODEL_FROM_TRANSCRIPT),
    ("brain_tool", ma.MODEL_FROM_TURN), ("mcp", ma.MODEL_FROM_TURN), ("zoe_agent", ma.MODEL_FROM_TURN),
    ("person_extractor_llm", ma.MODEL_FROM_TURN), ("synthesis", ma.MODEL_FROM_TRANSCRIPT),
    ("idle_consolidation", ma.MODEL_FROM_TRANSCRIPT), ("brand_new_lane", ma.MODEL_FROM_TRANSCRIPT),
]


@pytest.mark.parametrize("target", CLASSES)
@pytest.mark.parametrize("actor,wcls", INGEST_WRITERS, ids=[w[0] for w in INGEST_WRITERS])
def test_ingest_matrix(svc, monkeypatch, actor, wcls, target):
    """Every ingesting writer (an allow-list; the last is a lane nobody has heard of) against a
    row of every class, for a CONTRADICTING fact: ingest never retires anything, and a write
    below the row's rank is held back as a disputed candidate."""
    old = seed(svc, target)
    ref = asyncio.run(svc.ingest(NEW, user_id=UID, source=actor, status="approved"))
    assert ref.metadata["authority_class"] == wcls
    want_ok = allowed(ma.RANK[wcls], target)
    assert (ref.metadata["status"] == "approved") == want_ok, (actor, target)
    assert status(svc, old) == "approved"                        # ingest never retires anything
    if not want_ok:
        assert ref.metadata["status"] == "disputed" and ref.metadata["contradicts_id"] == old
    # negative control: off stores it approved beside the row it contradicts
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "off")
    other = asyncio.run(svc.ingest("User's dog is named Max.", user_id=UID, source=actor,
                                   status="approved", user_turn_id="ctl"))
    assert other.metadata["status"] == "approved"


@pytest.mark.parametrize("new_cls", CLASSES)
@pytest.mark.parametrize("target", CLASSES)
def test_supersede_by_matrix(svc, monkeypatch, new_cls, target):
    """W11 (write-time) and W12 (nightly) implicit supersede: the SUCCESSOR's class decides."""
    old = seed(svc, target, tag="old")
    new = seed(svc, new_cls, text="User's dog is named Rex.", tag="new")
    done = asyncio.run(svc.supersede_by(UID, old, new, actor="implicit_supersede"))
    assert done is allowed(ma.RANK[new_cls], target), (new_cls, target)
    assert status(svc, old) == ("superseded" if done else "approved")
    # negative control
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "off")
    other = seed(svc, target, tag="old2")
    assert asyncio.run(svc.supersede_by(UID, other, new, actor="implicit_supersede")) is True


@pytest.mark.parametrize("target", CLASSES)
@pytest.mark.parametrize("actor", ["decay_sweep", "consolidation", "janitor", "synthesis"])
def test_archive_matrix_decay_and_batch_passes(svc, monkeypatch, actor, target):
    """W16 decay + every batch actor archiving: below the user classes they may only retire
    rows of their own rank or lower (a never-recalled row the user said is never decayed)."""
    old = seed(svc, target)
    got = asyncio.run(svc.review(old, decision="archive", actor=actor))
    wrank = ma.RANK[ma.writer_class(actor)]
    assert (got is not None) is allowed(wrank, target), (actor, target)
    assert status(svc, old) == ("archived" if got is not None else "approved")
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "off")
    again = seed(svc, target, tag="b")
    assert asyncio.run(svc.review(again, decision="archive", actor=actor)) is not None


@pytest.mark.parametrize("target", CLASSES)
@pytest.mark.parametrize("keeper", CLASSES)
def test_weekly_merge_archives_only_identical_duplicates_of_equal_or_higher_class(svc, keeper, target):
    """W9: the merge never REWRITES; it archives an exact duplicate when the keeper says the
    same thing at least as authoritatively."""
    dup = seed(svc, target, tag="dup")
    keep = seed(svc, keeper, tag="keep")
    done = asyncio.run(svc.archive_duplicate(dup, keep, actor="consolidation"))
    assert done is (ma.RANK[keeper] >= ma.RANK[target])
    assert status(svc, dup) == ("archived" if done else "approved")
    assert status(svc, keep) == "approved"
    if done:
        assert svc._col.rows[dup][1]["duplicate_of"] == keep


def test_weekly_merge_pass_does_not_rewrite_and_keeps_the_stronger_row(svc):
    voice = seed(svc, ma.USER_STATED, text="User likes quiet mornings.", tag="v")
    model = seed(svc, ma.MODEL_FROM_TRANSCRIPT, text="User likes quiet mornings.", tag="m")
    differing = seed(svc, ma.MODEL_FROM_TRANSCRIPT, text="User likes quiet mornings a lot.", tag="d")
    n = asyncio.run(memory_digest._merge_near_duplicates(svc, UID))
    assert n == 1
    assert status(svc, voice) == "approved" and status(svc, model) == "archived"
    assert status(svc, differing) == "approved"                  # a different statement: untouched
    assert not any(m.get("supersedes_id") for _d, m in svc._col.rows.values())  # no rewrite rows
    assert len(svc._col.rows) == 3


def test_shadow_mode_logs_would_block_and_changes_nothing(svc, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=ma.logger.name)
    monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", "shadow")
    old = seed(svc, ma.USER_STATED)
    got = asyncio.run(svc.review(old, decision="edit", edits=NEW, actor="digest"))
    assert got is not None and status(svc, old) == "superseded"          # behaves like off ...
    assert "AUTHORITY_WOULD_BLOCK writer=digest kind=pet action=edit" in caplog.text  # ... but says so
    assert "AUTHORITY_BLOCKED" not in caplog.text
    assert asyncio.run(svc.list_by_status(user_id=UID, status="disputed")) == []
    # provenance is stamped in every mode
    assert got.metadata["origin"] == "digest" and got.metadata["authority_class"] == ma.MODEL_FROM_TRANSCRIPT
    # ingest in shadow: approved, logged
    seed(svc, ma.USER_STATED, text="User lives in Geraldton.", tag="h")
    ref = asyncio.run(svc.ingest("User lives in Perth.", user_id=UID, source="digest", status="approved"))
    assert ref.metadata["status"] == "approved"
    assert "AUTHORITY_WOULD_BLOCK writer=digest kind=home action=ingest" in caplog.text


def test_mode_parsing(monkeypatch):
    for raw, want in [(None, "enforce"), ("enforce", "enforce"), ("1", "enforce"), ("shadow", "shadow"),
                      ("SHADOW", "shadow"), ("off", "off"), ("0", "off"), ("", "off")]:
        if raw is None:
            monkeypatch.delenv("ZOE_MEMORY_AUTHORITY", raising=False)
        else:
            monkeypatch.setenv("ZOE_MEMORY_AUTHORITY", raw)
        assert ma.mode() == want


# ── P1.2: digest facts carry a verbatim quote from a USER turn ────────────────

USER_TURNS = "okay that was the plan\nBig news, I moved to Hobart last week\nuh Rex is coming over I think"


@pytest.mark.parametrize("item,want", [
    ({"fact": "User lives in Hobart.", "quote": "I moved to Hobart last week"}, "I moved to Hobart last week"),
    ({"fact": "User lives in Hobart.", "quote": "i  moved to hobart LAST week"}, "i  moved to hobart LAST week"),
    ({"fact": "User lives in Hobart.", "quote": "I moved to Darwin last week"}, None),   # not a span
    ({"fact": "User lives in Hobart.", "quote": ""}, None),
    ({"fact": "User lives in Hobart."}, USER_TURNS),                                    # no quote field
])
def test_fact_anchor(item, want):
    assert memory_digest.fact_anchor(item, USER_TURNS) == want


def test_a_hallucinated_quote_leaves_the_fact_inferred_but_a_verbatim_one_earns_user_stated(svc):
    seed(svc, ma.USER_STATED, text="User lives in Geraldton.", tag="h")
    bad = {"fact": "User lives in Hobart.", "quote": "I live in Hobart"}      # model made it up
    good = {"fact": "User lives in Hobart.", "quote": "I moved to Hobart last week"}
    r1 = asyncio.run(svc.ingest(bad["fact"], user_id=UID, source="digest", status="approved",
                                anchor_text=memory_digest.fact_anchor(bad, USER_TURNS) or ""))
    assert r1.metadata["status"] == "disputed"
    r2 = asyncio.run(svc.ingest(good["fact"], user_id=UID, source="digest", status="approved",
                                user_turn_id="x", anchor_text=memory_digest.fact_anchor(good, USER_TURNS) or ""))
    assert r2.metadata["status"] == "approved" and r2.metadata["authority_class"] == ma.USER_STATED


def test_the_extraction_prompt_asks_for_the_users_own_words():
    assert '"quote"' in memory_digest._EXTRACTION_PROMPT
    assert "first person" in memory_digest._EXTRACTION_PROMPT or "\"I\", \"my\"" in memory_digest._EXTRACTION_PROMPT


def test_idle_consolidation_sends_user_turns_only(monkeypatch, svc):
    """V5: the extractor's prompt says 'user turns only'; the transcript used to carry the
    assistant's lines too, so a fact the assistant SAID became a fact about the user."""
    import contextlib
    import types

    import memory_idle_consolidation as idle

    seen = {}

    async def extract(text):
        seen["text"] = text
        return [{"fact": "User's dog is named Rex.", "quote": "my dog is called Rex"}]

    rows = [
        {"role": "user", "content": "hello there, my dog is called Rex", "at": "t1", "metadata": "{}"},
        {"role": "assistant", "content": "Lovely - and your sister Casey lives in Perth, right?", "at": "t2",
         "metadata": "{}"},
        {"role": "user", "content": "yes she does", "at": "t3", "metadata": "{}"},
    ]

    async def fetch(conn, session_id, since):
        return rows

    async def watermark(*a, **k):
        return None

    @contextlib.asynccontextmanager
    async def ctx():
        yield types.SimpleNamespace()

    monkeypatch.setattr(idle, "_fetch_transcript_rows", fetch)
    monkeypatch.setattr(idle, "_write_watermark", watermark)
    monkeypatch.setattr(idle, "MIN_TURNS", 1)
    monkeypatch.setattr(idle, "_resolve_owner", lambda r, u: UID)
    monkeypatch.setattr(memory_digest, "_extract_facts_with_gemma", extract)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    import expert_dispatch

    stored = []

    async def ingest_or_supersede(_svc, text, **kw):
        stored.append((text, kw))
        return "stored"

    monkeypatch.setattr(expert_dispatch, "_ingest_or_supersede", ingest_or_supersede)
    asyncio.run(idle.consolidate_session("s1", UID, get_ctx=ctx))
    assert "Casey" not in seen["text"] and "assistant" not in seen["text"]
    assert "my dog is called Rex" in seen["text"]
    assert stored and stored[0][1]["anchor_text"] == "my dog is called Rex"
