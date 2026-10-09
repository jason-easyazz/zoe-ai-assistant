"""The claim row, end to end through the REAL ``run_turn_digest`` + ``MemoryService`` (fake collection, stubbed model, no network).

What is pinned, mode by mode (``ZOE_STRUCTURAL_CLAIMS``):

* ``off``     the extractor prompt and token budget are the legacy ones, no claim is stored, nothing is logged;
* ``shadow``  (the default) the extraction is the LEGACY call, byte for byte (same prompt, same token budget, same stored rows as ``off``);
              the claim row is read afterwards by a SEPARATE bounded call (``structural_reader``) over the facts that were stored, the LEXICAL
              decision still decides, and the structural decision is only logged beside it: the two false promotions of the research
              ("My mum is moving to Bendigo next month") are visible as ``lexical=promote structural=anchor``;
* ``enforce`` the claim row decides: the false promotion is held, a Spanish sentence the lexical floor never reads is promoted, the contrast's
              "not Y" half retires Y's row BY KEY (never stored as a fact), an invented quote anchors nothing.

Break-the-fix controls sit in each test (a flag flip, or the check taken out). Synthetic users and names only.
"""
from __future__ import annotations

import asyncio
import json
import logging
import types

import pytest

import memory_authority as ma
import memory_digest
import structural_claims as sc
import structural_reader as sr
import structural_verifier as sv


def test_a_denial_the_owner_never_said_retires_nothing(monkeypatch):
    """A consumed denial has no row of its own: its quote must be in the owner's turn before it may retire anything."""
    import memory_supersede

    seen = []

    async def spy(svc, user_id, entries):
        seen.append(list(entries))
        return {"retired": 0, "would": 0}
    monkeypatch.setattr(memory_supersede, "retire_by_claims", spy)
    said = "I am a nurse at the clinic now"
    real = sc.Claim("user", "occupation", "doctor", "negate", "asserted", "current", "I am a nurse", "en")
    invented = sc.Claim("user", "occupation", "doctor", "negate", "asserted", "current", "I am not a doctor", "en")
    asyncio.run(memory_digest._structural_post(object(), "u1", said, [], [real, invented], {0, 1}, {}))
    (entries,) = seen
    assert [c.quote for _r, c in entries] == ["I am a nurse"]
    seen.clear()
    asyncio.run(memory_digest._structural_post(object(), "u1", "I am not a doctor, I am a nurse", [], [invented], {0}, {}))
    assert [c.quote for _r, c in seen[0]] == ["I am not a doctor"]                       # control: said aloud, it counts
from test_memory_implicit_supersede import UID, _by_text, _svc

pytestmark = pytest.mark.ci_safe

MOVING = "My mum is moving to Bendigo next month."
MOVING_FACT = "User's mum lives in Bendigo"


def claim(**kw):
    base = dict(subj="rel:mother", pred="residence", obj="Bendigo", pol="affirm", mod="asserted", tense="current",
                quote="My mum lives in Bendigo", lang="en")
    base.update(kw)
    return base


def _llm(monkeypatch, handler):
    """Stub the model: ``handler(payload) -> str`` (the content). Records every payload in ``calls``."""
    calls: list = []

    class _Resp:
        def __init__(self, text):
            self._t = text

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": self._t}}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **k):
            calls.append(json)
            return _Resp(handler(json))

    monkeypatch.setattr(memory_digest.httpx, "AsyncClient", _Client)
    stub = types.ModuleType("zoe_agent")

    async def _blob(*a, **k):
        return ""

    stub._mempalace_load_user_facts = _blob
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(__import__("sys").modules, "zoe_agent", stub)
    return calls


def _facts_handler(facts):
    """The stubbed model. The extraction call answers like the real prompt would: with the claim rows only when the prompt asked for them
    (``max_tokens`` 640 = the enforce prompt), else the plain legacy items. The post-turn reader answers with the claim row of each fact."""
    def handler(payload):
        if payload.get("grammar"):
            return "no"
        if payload["messages"][0]["content"] == sr.SYSTEM:
            return json.dumps([{"i": i, **f["claim"]} for i, f in enumerate(facts, 1) if isinstance(f.get("claim"), dict)],
                              ensure_ascii=False)
        if payload["max_tokens"] == 256:
            return json.dumps([{k: v for k, v in f.items() if k != "claim"} for f in facts], ensure_ascii=False)
        return json.dumps(facts, ensure_ascii=False)
    return handler


def _run(monkeypatch, facts, turn, *, mode=None, verifier="off", seed=None):
    if mode is None:
        monkeypatch.delenv(sc.ENV, raising=False)
    else:
        monkeypatch.setenv(sc.ENV, mode)
    monkeypatch.setenv(sv.ENV, verifier)
    sv.reset()
    sr.reset()
    svc, col = _svc(monkeypatch)
    calls = _llm(monkeypatch, _facts_handler(facts))

    async def go():
        for text, c in (seed or []):
            await svc.ingest(text, user_id=UID, source="turn_digest", memory_type="profile", confidence=0.82,
                             status="approved", tags=["turn_digest"], claim=c,
                             anchor_text="seed", source_excerpt="seed")
        return await memory_digest.run_turn_digest(UID, turn, session_id="s-1")

    res = asyncio.run(go())
    return svc, col, res, calls


def test_the_lexical_floor_really_does_promote_the_moving_sentence():
    """The premise of the research (a measured false promotion): without any claim row the lexical floor promotes it."""
    assert ma.entailing_span(MOVING_FACT, MOVING) is not None


# ── off ───────────────────────────────────────────────────────────────────────────────────────────────

def test_off_asks_for_nothing_stores_no_claim_and_logs_nothing(monkeypatch, caplog):
    facts = [{"type": "profile", "fact": MOVING_FACT, "claim": claim(quote=MOVING.rstrip("."), tense="future")}]
    with caplog.at_level(logging.INFO):
        svc, col, res, calls = _run(monkeypatch, facts, MOVING, mode="off")
    assert len(calls) == 1 and calls[0]["max_tokens"] == 256          # one call, the legacy one: the reader is not asked either
    assert "claim" not in calls[0]["messages"][1]["content"].lower().replace("claim row", "")  # the legacy prompt
    assert '"quote"' not in calls[0]["messages"][1]["content"]
    _, row = _by_text(col, MOVING_FACT)
    assert "claim" not in row and "claim_structural" not in row
    assert row["authority_basis"] == ma.VERBATIM_BASIS            # the legacy decision, untouched
    assert "STRUCTURAL_FLOOR" not in caplog.text and "STRUCTURAL_EXTRACT" not in caplog.text


# ── shadow (the default) ─────────────────────────────────────────────────────────────────────────────

def test_shadow_is_the_default_extracts_exactly_as_off_then_reads_the_claim_row_in_a_separate_call(monkeypatch, caplog):
    facts = [{"type": "profile", "fact": MOVING_FACT, "claim": claim(quote=MOVING.rstrip("."), tense="future")}]
    with caplog.at_level(logging.INFO):
        svc, col, res, calls = _run(monkeypatch, facts, MOVING, mode=None)
    extract, read = calls[0], calls[1]
    # call 1: the LEGACY extraction, byte for byte (the 2026-10-09 regression: the claim-row prompt changed which facts the 4B returned)
    assert extract["max_tokens"] == 256 and extract["messages"][1]["content"].startswith(memory_digest._TURN_EXTRACTION_PROMPT[:200])
    assert '"pol"' not in extract["messages"][1]["content"] and '"quote"' not in extract["messages"][1]["content"]
    assert "STRUCTURAL_EXTRACT" not in caplog.text
    # call 2: the separate post-turn reader, over the fact that was STORED
    assert read["messages"][0]["content"] == sr.SYSTEM and read["max_tokens"] == sr.MAX_TOKENS and MOVING_FACT in read["messages"][1]["content"]
    _, row = _by_text(col, MOVING_FACT)
    assert row["authority_basis"] == ma.VERBATIM_BASIS and row["authority_class"] == ma.USER_STATED_DERIVED  # lexical still decides
    assert not [k for k in row if k.startswith("claim")]              # the row is exactly the row `off` writes
    assert "STRUCTURAL_FLOOR floor=support lane=turn_digest_post mode=shadow lang=en lexical=promote structural=anchor agree=0 applied=0" in caplog.text
    assert "STRUCTURAL_READ lane=turn_digest facts=1 with_claim=1" in caplog.text
    assert MOVING_FACT not in "".join(r.getMessage() for r in caplog.records if r.getMessage().startswith("STRUCTURAL_FLOOR"))  # labels only


def test_shadow_reader_off_switch_and_no_stored_fact_mean_no_second_call(monkeypatch):
    facts = [{"type": "profile", "fact": MOVING_FACT, "claim": claim(quote=MOVING.rstrip("."), tense="future")}]
    monkeypatch.setenv(sr.ENV, "off")
    _, _, _, calls = _run(monkeypatch, facts, MOVING, mode=None)
    assert len(calls) == 1 and calls[0]["max_tokens"] == 256
    monkeypatch.delenv(sr.ENV, raising=False)
    _, col, _, calls = _run(monkeypatch, [], MOVING, mode=None)       # the extractor found nothing: nothing was stored, nothing to read
    assert len(calls) == 1 and not col.rows
    _, _, _, calls = _run(monkeypatch, facts, MOVING, mode="off")
    assert len(calls) == 1


def test_a_failed_reader_costs_the_digest_nothing(monkeypatch):
    facts = [{"type": "profile", "fact": "User lives in Perth", "claim": claim(subj="user", obj="Perth", quote="I live in Perth")}]
    base = _facts_handler(facts)

    def handler(payload):
        if payload["messages"][0]["content"] == sr.SYSTEM:
            raise RuntimeError("brain busy")
        return base(payload)
    monkeypatch.delenv(sc.ENV, raising=False)
    sr.reset()
    svc, col = _svc(monkeypatch)
    _llm(monkeypatch, handler)
    res = asyncio.run(memory_digest.run_turn_digest(UID, "I live in Perth.", session_id="s-1"))
    assert res["new"] == 1 and _by_text(col, "User lives in Perth")[1]["status"] == "approved"
    assert sr.STATS["error"] == 1


#: the seed set: the turns the 2026-10-09 acceptance regressed on (synthetic names), each with the legacy extractor's items
SEED_TURNS = [
    ("I live in Perth.", [{"type": "profile", "fact": "User lives in Perth",
                           "claim": claim(subj="user", obj="Perth", quote="I live in Perth")}]),
    ("Big news - I've moved. I live in Hobart now.", [{"type": "profile", "fact": "User has moved and now lives in Hobart",
                                                       "claim": claim(subj="user", obj="Hobart", quote="I live in Hobart now")}]),
    ("My friend Dana Whitfield has two kids, Mika and Biscuit.",
     [{"type": "relationship", "fact": "User's friend is named Dana Whitfield",
       "claim": claim(subj="person:Dana Whitfield", pred="name", obj="Dana Whitfield", quote="My friend Dana Whitfield")}]),
    ("Biscuit is their dog", [{"type": "pet", "fact": "User's dog is named Biscuit",
                               "claim": claim(subj="user", pred="pet_name", obj="Biscuit", quote="Biscuit is their dog")}]),
    (MOVING, [{"type": "profile", "fact": MOVING_FACT, "claim": claim(quote=MOVING.rstrip("."), tense="future")}]),
]


def _stored_rows(col):
    """Every stored row, minus the ids/timestamps that differ run to run: text + the whole metadata."""
    volatile = {"added_at", "added_ts", "created_at", "updated_at", "valid_from", "ts", "last_accessed", "id", "idem_key", "idempotency_key"}
    return sorted((d, tuple(sorted((k, repr(v)) for k, v in m.items() if k not in volatile))) for d, m in col.rows.values())


def test_shadow_and_off_store_byte_identical_rows_and_send_the_identical_extraction_prompt(monkeypatch):
    """THE class fix: `shadow` may add a measurement, never change what the extractor is asked or what gets stored.
    Over the seed set, the extraction payload and every stored row (text, type, class, basis, status, every metadata key) are equal."""
    for turn, facts in SEED_TURNS:
        outs = {}
        for mode in ("off", "shadow"):
            _, col, _, calls = _run(monkeypatch, facts, turn, mode=mode)
            outs[mode] = (calls[0], _stored_rows(col), len(calls))
        assert outs["off"][0] == outs["shadow"][0], turn                 # the extraction payload: model, messages, max_tokens, temperature
        assert outs["off"][1] == outs["shadow"][1] and outs["off"][1], turn
        assert outs["off"][2] == 1 and outs["shadow"][2] == 2            # the only difference: shadow's second, separate, reader call


def test_the_extraction_prompt_and_budget_do_not_depend_on_shadow_but_enforce_changes_them(monkeypatch):
    import hashlib

    for mode, want_tokens in (("off", 256), ("shadow", 256), ("enforce", 640)):
        monkeypatch.setenv(sc.ENV, mode)
        prompt, tokens = memory_digest._turn_prompt()
        assert tokens == want_tokens
        digest = hashlib.sha256(prompt.encode()).hexdigest()
        if mode == "enforce":
            assert digest[:16] == "cd0909aa4c6f61c3"                  # the enforce prompt is exactly the one #1943 shipped
            assert '"quote"' in prompt and "TWO items" in prompt
        else:
            assert digest == hashlib.sha256(memory_digest._TURN_EXTRACTION_PROMPT.encode()).hexdigest()
            assert '"quote"' not in prompt and "TWO items" not in prompt
    monkeypatch.delenv(sc.ENV, raising=False)                             # the default is shadow = the legacy prompt
    assert memory_digest._turn_prompt() == (memory_digest._TURN_EXTRACTION_PROMPT, 256)


def test_a_malformed_claim_is_just_no_claim(monkeypatch):
    for bad in ({"subj": "user", "pol": "maybe"}, "not json", None, {"pol": "affirm", "mod": "asserted", "tense": "current"}):
        facts = [{"type": "profile", "fact": "User lives in Perth", **({"claim": bad} if bad is not None else {})}]
        _, col, _, _ = _run(monkeypatch, facts, "I live in Perth.", mode="enforce")
        _, row = _by_text(col, "User lives in Perth")
        assert "claim" not in row and row["authority_basis"] == ma.VERBATIM_BASIS    # the lexical decision, unchanged


# ── enforce ──────────────────────────────────────────────────────────────────────────────────────────

def test_enforce_holds_the_false_promotion_the_lexical_floor_makes(monkeypatch, caplog):
    facts = [{"type": "profile", "fact": MOVING_FACT, "claim": claim(quote=MOVING.rstrip("."), tense="future")}]
    with caplog.at_level(logging.INFO):
        _, col, _, _ = _run(monkeypatch, facts, MOVING, mode="enforce")
    _, row = _by_text(col, MOVING_FACT)
    assert row["authority_basis"] == "anchored_user_turn"                       # NOT the verbatim promotion
    assert (row["claim_lexical"], row["claim_structural"], row["claim_applied"]) == ("promote", "anchor", True)
    assert "applied=1" in caplog.text
    # break the fix: the same row with the claim row honest about being plain/current is promoted (the check is what held it)
    facts[0]["claim"] = claim(quote=MOVING.rstrip("."), tense="current")
    _, col2, _, _ = _run(monkeypatch, facts, MOVING, mode="enforce")
    assert _by_text(col2, MOVING_FACT)[1]["authority_basis"] == ma.VERBATIM_BASIS


def test_enforce_promotes_a_spanish_sentence_the_lexical_floor_never_reads(monkeypatch, caplog):
    turn = "Mi madre vive en Bendigo."
    fact = "La madre del usuario vive en Bendigo"
    facts = [{"type": "profile", "fact": fact, "claim": claim(quote="Mi madre vive en Bendigo", lang="es")}]
    with caplog.at_level(logging.INFO):
        _, col, _, _ = _run(monkeypatch, facts, turn, mode="shadow")
    row = _by_text(col, fact)[1]
    assert row["authority_class"] == ma.MODEL_FROM_TURN and not [k for k in row if k.startswith("claim")]   # shadow: lexical = blind to Spanish
    assert "lane=turn_digest_post mode=shadow lang=es lexical=hold structural=promote" in caplog.text          # ...and the reader's row says promote
    _, col, _, _ = _run(monkeypatch, facts, turn, mode="enforce")
    row = _by_text(col, fact)[1]
    assert row["authority_class"] == ma.USER_STATED_DERIVED and row["authority_basis"] == ma.VERBATIM_BASIS


def test_enforce_an_invented_quote_anchors_nothing(monkeypatch):
    turn = "Mi madre vive en Bendigo."
    fact = "La madre del usuario vive en Bendigo"
    facts = [{"type": "profile", "fact": fact, "claim": claim(quote="La madre del usuario vive en Bendigo", lang="es")}]
    _, col, _, _ = _run(monkeypatch, facts, turn, mode="enforce")
    row = _by_text(col, fact)[1]
    assert row["authority_class"] == ma.MODEL_FROM_TURN and row["claim_structural"] == "hold"


def test_enforce_a_contrast_is_two_claims_the_denial_retires_the_old_row_by_key_and_is_never_stored(monkeypatch, caplog):
    turn = "Mi madre vive en Bendigo, no en Ballarat."
    new, old = "La madre del usuario vive en Bendigo", "La madre del usuario vive en Ballarat"
    old_claim = claim(obj="Ballarat", quote="Mi madre vive en Ballarat", lang="es")
    facts = [
        {"type": "profile", "fact": new, "claim": claim(quote="Mi madre vive en Bendigo", lang="es")},
        {"type": "profile", "fact": "La madre del usuario no vive en Ballarat",
         "claim": claim(obj="Ballarat", pol="negate", quote="no en Ballarat", lang="es")},
    ]
    seed = [(old, old_claim)]
    with caplog.at_level(logging.INFO):
        svc, col, res, _ = _run(monkeypatch, facts, turn, mode="enforce", seed=seed)
    texts = [d for d, _ in col.rows.values()]
    assert "La madre del usuario no vive en Ballarat" not in texts          # the "not Y" half is a retirement, not a fact
    old_id, old_row = _by_text(col, old)
    new_id, new_row = _by_text(col, new)
    assert old_row["status"] == "superseded" and old_row["superseded_by_id"] == new_id          # retired BY ID, kept
    assert isinstance(old_row["invalid_at"], float) and old_id in col.rows                       # invalidated, never deleted
    assert isinstance(old_row["expired_at"], float) and old_row["invalid_at"] <= old_row["expired_at"]   # both timelines kept
    assert "claim" in old_row and "superseded_by_id" in old_row                                   # the old claim row stays with the old row
    assert new_row["status"] == "approved" and new_row["authority_basis"] == ma.VERBATIM_BASIS
    assert "STRUCTURAL_RETIRE mode=enforce" in caplog.text and res["superseded"] == 1
    # shadow: the extraction is the legacy call (it returns the NEW fact only: the contrast rule is not in its prompt); the reader's row
    # for it logs what WOULD be retired and retires nothing
    caplog.clear()
    with caplog.at_level(logging.INFO):
        _, col2, res2, _ = _run(monkeypatch, facts[:1], turn, mode="shadow", seed=seed)
    assert _by_text(col2, old)[1]["status"] == "approved" and "would_retire=1" in caplog.text
    assert "La madre del usuario no vive en Ballarat" not in [d for d, _ in col2.rows.values()]
    # off: no claim rows, no retirement, and the (unclaimed) denial item is judged as the extractor wrote it
    _, col3, _, _ = _run(monkeypatch, facts, turn, mode="off", seed=seed)
    assert _by_text(col3, old)[1]["status"] == "approved"


def test_enforce_retires_a_legacy_row_with_no_claim_by_the_text_key(monkeypatch):
    """A row written before the claim row existed has none: the older text key (owner_key_match) still pairs it."""
    turn = "I no longer play squash."
    facts = [{"type": "profile", "fact": "User no longer plays squash",
              "claim": claim(subj="user", pred="activity", obj="squash", pol="ended", quote="I no longer play squash")}]
    seed = [("User plays squash every Tuesday", None)]
    _, col, res, _ = _run(monkeypatch, facts, turn, mode="enforce", seed=seed)
    assert _by_text(col, "User plays squash every Tuesday")[1]["status"] == "superseded"


def test_an_unverified_speaker_is_never_promoted_by_a_claim_row(monkeypatch):
    facts = [{"type": "profile", "fact": "User lives in Perth", "claim": claim(subj="user", obj="Perth", quote="I live in Perth")}]
    monkeypatch.setenv(sc.ENV, "enforce")
    d = sc.decide("User lives in Perth", sc.parse_claim(facts[0]["claim"])[0], "I live in Perth.", speaker_verified=False)
    assert d.anchored and not d.promoted and "speaker_not_verified" in d.reasons


# ── the off-path verifier ──────────────────────────────────────────────────────────────────────────

def test_verifier_logs_a_verdict_on_the_ambiguous_residue_and_changes_nothing(monkeypatch, caplog):
    facts = [{"type": "profile", "fact": MOVING_FACT, "claim": claim(quote=MOVING.rstrip("."), tense="future")}]
    with caplog.at_level(logging.INFO):
        _, col, _, calls = _run(monkeypatch, facts, MOVING, mode="shadow", verifier="shadow")
    judge = [c for c in calls if c.get("grammar")]
    assert len(judge) == 1 and judge[0]["grammar"] == 'root ::= "yes" | "no"' and judge[0]["max_tokens"] == 2
    assert judge[0]["temperature"] == 0
    assert "STRUCTURAL_VERIFY verdict=no lexical=promote structural=anchor" in caplog.text
    _, row = _by_text(col, MOVING_FACT)
    assert row["authority_basis"] == ma.VERBATIM_BASIS            # shadow-only: the verdict decides nothing
    # break the fix: verifier off -> no judge call at all
    _, _, _, calls2 = _run(monkeypatch, facts, MOVING, mode="shadow", verifier="off")
    assert not [c for c in calls2 if c.get("grammar")]
    # and with the whole feature off the verifier is not asked either
    _, _, _, calls3 = _run(monkeypatch, facts, MOVING, mode="off", verifier="shadow")
    assert not [c for c in calls3 if c.get("grammar")]


def test_no_verifier_call_when_the_two_floors_agree(monkeypatch):
    facts = [{"type": "profile", "fact": "User lives in Perth", "claim": claim(subj="user", obj="Perth", quote="I live in Perth")}]
    _, _, _, calls = _run(monkeypatch, facts, "I live in Perth.", mode="shadow", verifier="shadow")
    assert not [c for c in calls if c.get("grammar")]


def test_a_claim_whose_quote_carries_pii_is_not_stored(monkeypatch):
    """The claim row's quote is a slice of the owner's turn: it passes the same PII scrub as the evidence excerpt, else it is not stored."""
    turn = "My password is hunter2 and I live in Perth."
    facts = [{"type": "profile", "fact": "User lives in Perth",
              "claim": claim(subj="user", obj="Perth", quote="My password is hunter2 and I live in Perth")}]
    _, col, _, _ = _run(monkeypatch, facts, turn, mode="enforce")      # the claim row is STORED only where it is asked inline
    _, row = _by_text(col, "User lives in Perth")
    assert "claim" not in row and "hunter2" not in json.dumps(row)
    facts[0]["claim"] = claim(subj="user", obj="Perth", quote="I live in Perth")
    _, col, _, _ = _run(monkeypatch, facts, turn, mode="enforce")
    assert json.loads(_by_text(col, "User lives in Perth")[1]["claim"])["quote"] == "I live in Perth"


def test_a_reply_cut_off_by_max_tokens_keeps_every_complete_item_in_enforce_mode_only(monkeypatch):
    """The claim rows lengthen the reply (enforce only: shadow and off ask for the legacy short one). Cut mid-item, the complete items still land; the legacy prompt keeps its all-or-nothing parse."""
    whole = json.dumps([{"type": "profile", "fact": "User lives in Perth", "claim": claim(subj="user", obj="Perth", quote="I live in Perth")},
                        {"type": "profile", "fact": "User likes tea", "claim": claim(subj="user", obj="tea", pred="preference", quote="I like tea")}])
    cut = whole[: whole.index('{"type": "profile", "fact": "User likes tea"')] + '{"type": "profile", "fact": "User lik'
    for mode, want in (("enforce", ["User lives in Perth"]), ("shadow", []), ("off", [])):
        monkeypatch.setenv(sc.ENV, mode)
        monkeypatch.setenv(sv.ENV, "off")
        svc, col = _svc(monkeypatch)
        _llm(monkeypatch, lambda payload: cut)
        asyncio.run(memory_digest.run_turn_digest(UID, "I live in Perth. I like tea.", session_id="s-1"))
        assert sorted(d for d, _ in col.rows.values()) == want, mode


def test_a_place_fragment_the_extractor_returns_is_refused_but_the_full_clause_is_kept(monkeypatch):
    """Bar S24 (2026-10-09): a place fact must be a full clause. The model returning the bare fragment stores nothing; the same turn
    with a full clause stores it. (The three fragments are the acceptance run's.)"""
    for turn, fragment in (("Which city do I live in these days?", "User lives in these days"),
                           ("Big news - I've moved. I live in Hobart now.", "User lives in now"),
                           ("I live in Dunedin, by the way.", "User is from the")):
        _, col, res, _ = _run(monkeypatch, [{"type": "profile", "fact": fragment}], turn, mode="off")
        assert not col.rows, (turn, fragment)
    _, col, _, _ = _run(monkeypatch, [{"type": "profile", "fact": "User has moved and now lives in Hobart"}],
                        "Big news - I've moved. I live in Hobart now.", mode="off")
    assert [d for d, _ in col.rows.values()] == ["User has moved and now lives in Hobart"]
