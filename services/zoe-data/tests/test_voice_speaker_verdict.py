"""The speaker gate's verdict reaches memory provenance (the voice lane of memory_authority).

Before: the voice daemon's speaker-id result never reached the memory writers, so a self-fact
spoken at the panel by a voice the gate did not confirm (a guest, another member) was stored as
the OWNER's own statement. After: the daemon sends a small ``speaker`` block, zoe-data turns it into
``speaker_verified`` (True / False / None), and every write the turn causes carries it.

Pinned here, each with a break-the-fix control (the verdict dropped -> the test goes red):

* ``_speaker_verdict`` - the server decides ``True`` (the panel's word never does), a refused
  claim or the daemon's ``verified: false`` is ``False``, no block is ``None``;
* ``voice_command`` hands the verdict to ``_run_voice_memory_passes`` on every exit path it takes;
* the passes hand it to ``extract_and_ingest`` / ``run_turn_digest`` (and nothing extra when it
  is ``None``: the exact call the lane always made);
* the real writer: verified -> ``user_stated``, unverified -> ``user_unverified`` (never
  supersedes the owner), no verdict -> unchanged, and a later VERIFIED statement supersedes an
  unverified one;
* the recall packet says "someone at the panel said", never "you said", for an unverified row.

Synthetic names only; fake Chroma; no network (ci_safe).
"""
from __future__ import annotations

import asyncio
import sys

import pytest

import memory_authority as ma
import memory_extractor
import memory_service
import routers.voice_tts as v
from memory_service import MemoryService
from test_memory_authority import _Col
from test_voice_identity_persistence import PANEL_ID, SESSION_ID, UTTERANCE, _wire_voice_command_fakes

pytestmark = pytest.mark.ci_safe

DEVICE = {"source": "device", "panel_id": "zoe-touch-pi", "user_id": "voice-daemon"}
SESSION_CALLER = {"source": "session", "user_id": "casey"}
UID = "casey"


# ── 1. the verdict, from the wire ────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _threshold(monkeypatch):
    monkeypatch.setenv("ZOE_SPEAKER_ID_THRESHOLD", "0.82")


def _verdict(payload, caller=DEVICE, *, by_claim=False):
    return v._speaker_verdict(payload, caller, identified_by_claim=by_claim)


def test_a_claim_the_server_accepted_is_verified_and_nothing_else_is():
    assert _verdict({"voice_user_id": "casey", "voice_score": 0.9}, by_claim=True) is True
    # the panel's own `verified: true` is NOT evidence: acceptance is the server's call
    assert _verdict({"speaker": {"verified": True, "member": None, "score": None}}) is None
    assert _verdict({"speaker": {"verified": True, "member": "casey", "score": 0.4}}) is False  # below threshold


def test_a_refused_or_unmatched_claim_is_unverified():
    # the gate scored a member, the server's threshold refused it
    assert _verdict({"voice_user_id": "casey", "voice_score": 0.5}) is False
    assert _verdict({"speaker": {"verified": None, "member": "casey", "score": 0.5}}) is False
    # the gate ran and nobody matched
    assert _verdict({"speaker": {"verified": False, "member": None, "score": None}}) is False


def test_no_verdict_is_none_so_today_s_behaviour_stands():
    assert _verdict({}) is None
    assert _verdict({"text": "hi", "panel_id": "p"}) is None
    assert _verdict({"speaker": "not-a-dict"}) is None
    assert _verdict({"speaker": {}}) is None
    assert _verdict({"voice_user_id": "casey", "voice_score": "garbage"}) is None   # malformed = no evidence
    assert _verdict({"speaker": {"verified": None, "member": "casey", "score": None}}) is None


def test_only_a_device_token_can_report_a_verdict():
    for caller in (SESSION_CALLER, {}, None):
        assert _verdict({"speaker": {"verified": False}}, caller) is None
        assert _verdict({"voice_user_id": "casey", "voice_score": 0.1}, caller) is None


def test_the_speaker_block_alone_carries_a_claim_for_the_existing_gate():
    """A daemon that sends only `speaker` (no flat pair) is still gated by the server's threshold."""
    assert v._accept_panel_voice_claim({"speaker": {"member": "casey", "score": 0.9}}, DEVICE) == "casey"
    assert v._accept_panel_voice_claim({"speaker": {"member": "casey", "score": 0.5}}, DEVICE) is None
    # the flat pair wins when both are present (older daemons)
    assert v._accept_panel_voice_claim(
        {"voice_user_id": "dana", "voice_score": 0.9, "speaker": {"member": "casey", "score": 0.1}}, DEVICE) == "dana"


# ── 2. voice_command hands it to the memory passes ───────────────────────────

async def _turn(monkeypatch, payload, caller=None, *, consented=True):
    chat_calls, spawned = _wire_voice_command_fakes(monkeypatch, panel_user="casey")
    seen: list[dict] = []

    async def passes(text, reply, user, session, **kw):
        seen.append({"user": user, **kw})

    async def consent(_uid):
        return consented

    monkeypatch.setattr(v, "_run_voice_memory_passes", passes)
    monkeypatch.setattr(v, "_voice_claim_consented", consent)
    body = {"text": UTTERANCE, "panel_id": PANEL_ID, "session_id": SESSION_ID, **payload}
    resp = await v.voice_command(body, caller=caller or DEVICE, stream=False, db=object())
    if spawned:
        await asyncio.gather(*spawned)
    assert resp["ok"] is True
    assert len(seen) == 1, seen
    return seen[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload, expected", [
    ({"voice_user_id": "casey", "voice_score": 0.93,
      "speaker": {"verified": None, "member": "casey", "score": 0.93}}, True),
    ({"voice_user_id": "casey", "voice_score": 0.40,
      "speaker": {"verified": None, "member": "casey", "score": 0.40}}, False),
    ({"speaker": {"verified": False, "member": None, "score": None}}, False),
    ({}, None),                                                       # gate off / shadow: no block
    ({"speaker": {"verified": True, "member": None, "score": None}}, None),   # the panel's word is not a verdict
])
async def test_voice_command_carries_the_verdict_to_the_memory_passes(monkeypatch, payload, expected):
    got = await _turn(monkeypatch, payload)
    assert got["speaker_verified"] is expected


@pytest.mark.asyncio
async def test_a_claim_without_consent_is_unverified(monkeypatch):
    """The member's profile consent was revoked after the panel cached it: not identified, and said so."""
    got = await _turn(monkeypatch, {"voice_user_id": "casey", "voice_score": 0.95}, consented=False)
    assert got["speaker_verified"] is False


@pytest.mark.asyncio
async def test_a_turn_that_names_its_own_speaker_reports_no_verdict(monkeypatch):
    """`identified_user_id` (a legacy / bridge path) is the caller's assertion, not the gate's."""
    got = await _turn(monkeypatch, {"identified_user_id": "casey", "speaker": {"verified": False}})
    assert got["speaker_verified"] is None


@pytest.mark.asyncio
async def test_a_browser_session_cannot_report_a_verdict(monkeypatch):
    got = await _turn(monkeypatch, {"speaker": {"verified": False}}, caller={**SESSION_CALLER, "panel_id": PANEL_ID})
    assert got["speaker_verified"] is None


# ── 3. the passes hand it to the writers ─────────────────────────────────────

def _passes(monkeypatch, verdict, *, with_kw=True):
    calls: dict[str, dict] = {}

    async def extract(text, reply, **kw):
        calls["extract"] = kw
        return 0

    async def digest(uid, text, reply, **kw):
        calls["digest"] = kw
        return {}

    async def noop(*_a, **_k):
        return 0

    import latent_intent_detector, memory_digest, person_extractor, person_extractor_llm
    monkeypatch.setattr(memory_extractor, "extract_and_ingest", extract)
    monkeypatch.setattr(memory_digest, "run_turn_digest", digest)
    monkeypatch.setattr(person_extractor, "process_text", noop)
    monkeypatch.setattr(person_extractor_llm, "process_text_llm", noop)
    monkeypatch.setattr(latent_intent_detector, "detect_and_store", noop)
    kw = {"speaker_verified": verdict} if with_kw else {}
    asyncio.run(v._run_voice_memory_passes("I live in Perth", "ok", UID, "s1", **kw))
    return calls


@pytest.mark.parametrize("verdict", [True, False])
def test_both_writing_passes_receive_the_verdict(monkeypatch, verdict):
    calls = _passes(monkeypatch, verdict)
    assert calls["extract"]["speaker_verified"] is verdict
    assert calls["digest"]["speaker_verified"] is verdict


def test_no_verdict_is_the_exact_call_the_lane_always_made(monkeypatch):
    for calls in (_passes(monkeypatch, None), _passes(monkeypatch, None, with_kw=False)):
        assert "speaker_verified" not in calls["extract"] and "speaker_verified" not in calls["digest"]


# ── 4. the real writer: the class of the row ─────────────────────────────────

@pytest.fixture
def svc(monkeypatch):
    for k in ("ZOE_MEMORY_AUTHORITY", "ZOE_AFFECT_CONSENT_GATE"):
        monkeypatch.delenv(k, raising=False)
    s = MemoryService(data_dir="/nonexistent/zoe-test-voice-speaker-verdict")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: s)
    s._col = col
    return s


def _say(svc, text, verdict, *, session="s1"):
    kw = {} if verdict is None else {"speaker_verified": verdict}
    return asyncio.run(memory_extractor.extract_and_ingest(
        text, "", user_id=UID, session_id=session, source="voice_regex", prev_user_message="", **kw))


def _rows(svc, needle):
    return [(m.get("authority_class"), m.get("status")) for d, m in svc._col.rows.values() if needle in d]


def test_a_verified_voice_self_fact_is_the_owners_own_statement(svc):
    assert _say(svc, "I live in Perth", True) == 1
    assert _rows(svc, "Perth") == [("user_stated", "approved")]


def test_an_unverified_voice_self_fact_is_user_unverified(svc):
    assert _say(svc, "I live in Perth", False) == 1
    assert _rows(svc, "Perth") == [("user_unverified", "approved")]


def test_no_verdict_leaves_the_class_unchanged(svc):
    assert _say(svc, "I live in Perth", None) == 1
    assert _rows(svc, "Perth") == [("user_stated", "approved")]


def test_an_unverified_voice_cannot_replace_what_the_owner_said(svc):
    _say(svc, "I live in Perth", True)
    _say(svc, "I live in Hobart", False, session="s2")
    assert ("user_stated", "approved") in _rows(svc, "Perth")
    assert all(status != "approved" for _c, status in _rows(svc, "Hobart")), _rows(svc, "Hobart")


def _reconcile_into(monkeypatch, svc, needle):
    """The fake collection has no vector search, so say what reconcile_for_ingest would: UPDATE the row
    about ``needle`` (the real one finds it by similarity). The write path after it is the real one."""
    import memory_quality

    async def reconcile(_svc, _text, _uid, **_kw):
        ids = [i for i, (d, _m) in svc._col.rows.items() if needle in d]
        return ("update", ids[0]) if ids else ("add", None)

    monkeypatch.setattr(memory_quality, "reconcile_for_ingest", reconcile)


def test_a_later_verified_statement_supersedes_an_unverified_one(svc, monkeypatch):
    _say(svc, "I live in Hobart", False)
    assert _rows(svc, "Hobart") == [("user_unverified", "approved")]
    _reconcile_into(monkeypatch, svc, "Hobart")
    _say(svc, "I live in Perth", True, session="s2")
    assert ("user_stated", "approved") in _rows(svc, "Perth")
    assert [s for _c, s in _rows(svc, "Hobart")] == ["superseded"], _rows(svc, "Hobart")


def test_an_unverified_statement_cannot_supersede_a_verified_one_through_the_correction_path(svc, monkeypatch):
    _say(svc, "I live in Perth", True)
    _reconcile_into(monkeypatch, svc, "Perth")
    _say(svc, "I live in Hobart", False, session="s2")
    assert ("user_stated", "approved") in _rows(svc, "Perth")
    assert all(status != "approved" for _c, status in _rows(svc, "Hobart")), _rows(svc, "Hobart")


def test_expert_dispatch_carries_the_verdict_to_the_voice_fact_writer(svc, monkeypatch):
    """store_fact (the explicit-teach fast path) writes as ``voice_fact``: same rule."""
    import expert_dispatch

    async def store(text, verdict):
        return await expert_dispatch._ingest_or_supersede(
            svc, text, user_id=UID, source="voice_fact", session_id="s1", user_turn_id=None,
            memory_type="fact", confidence=0.85, tags=["voice", "self"], speaker_verified=verdict)

    asyncio.run(store("User's dog is named Biscuit.", False))
    assert _rows(svc, "Biscuit") == [("user_unverified", "approved")]
    asyncio.run(store("User's cat is named Pickle.", None))
    assert _rows(svc, "Pickle") == [("user_stated", "approved")]


def test_the_voice_extractors_own_work_template_is_a_conflict_the_wall_sees(svc):
    """`User works at/for X` (memory_extractor's template) used to slip past the work matcher, so an
    unconfirmed panel voice's "I work at ..." sat beside the owner's job instead of being held back."""
    assert ma.conflict_kind("User works at/for Beta Freight", "User works at/for Acme Ferries") == "work"
    _say(svc, "I work at Acme Ferries", True)
    _say(svc, "I work at Beta Freight", False, session="s2")
    assert ("user_stated", "approved") in _rows(svc, "Acme")
    assert [c for c, s in _rows(svc, "Beta") if s == "approved"] == []
    assert any(s == "disputed" for _c, s in _rows(svc, "Beta"))


# ── 5. the recall packet's label ─────────────────────────────────────────────

def test_recall_labels_an_unverified_row_and_never_says_you_said(svc):
    from routers import memories

    _say(svc, "I live in Hobart", False)
    _say(svc, "I work at Acme", True, session="s2")
    refs = svc._metadata_read(UID, 50)
    assert {r.text for r in refs} >= {"User lives in Hobart.", "User works at Acme."} or len(refs) == 2
    for evidence in (False, True):
        packet = memories._build_memory_prompt_packet(refs, [], evidence=evidence, quotes=evidence)["packet"]
        lines = [ln for ln in packet.splitlines() if ln.startswith("- ")]
        hobart = [ln for ln in lines if "Hobart" in ln]
        acme = [ln for ln in lines if "Acme" in ln]
        assert hobart and ma.UNVERIFIED_RECALL_LABEL in hobart[0]
        assert "you said" not in hobart[0]
        assert acme and ma.UNVERIFIED_RECALL_LABEL not in acme[0]


def test_an_unverified_row_is_never_quoted_as_you_said():
    import recall_evidence

    meta = {"source": "voice_regex", "authority_class": "user_unverified",
            "source_excerpt": "I'm Dev and I live in Perth and I work at the bakery downtown"}
    assert recall_evidence.quote_for(meta, "User lives in Perth.") == ""
    meta["authority_class"] = "user_stated"
    assert recall_evidence.quote_for(meta, "User lives in Perth.") != ""
