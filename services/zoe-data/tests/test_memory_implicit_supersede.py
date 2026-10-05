"""Gap #4: implicit corrections supersede the fact they replace (ZOE_MEMORY_IMPLICIT_SUPERSEDE).

The A/B round-2 evidence (docs/knowledge/user-model-ab.md "Run 2"): "Change of plan: I've
dropped the half-marathon. I'm doing a 10k in May instead." stored two new rows and left
"training for their first half-marathon in March" approved, so the card kept the stale
race. These tests drive the REAL MemoryService (fake Chroma collection, no model, no DB)
through run_turn_digest, the nightly pass and the card, each with a flag-off control.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import sys
import types

import pytest

memory_digest = pytest.importorskip("memory_digest")
import memory_service  # noqa: E402
import memory_supersede as ms  # noqa: E402
import user_model_card as umc  # noqa: E402
from memory_service import MemoryRef, MemoryService  # noqa: E402

pytestmark = pytest.mark.ci_safe  # fake collection + stubbed LLM; no DB, no network

UID = "member-a"
SAY = "Change of plan: I've dropped the half-marathon. I'm doing a 10k in May instead."
OLD_RACE = "User is training for their first half-marathon in March."
TOMBSTONE = "User dropped the half-marathon."
TEN_K = "User is doing a 10k in May instead."


# ── fakes ─────────────────────────────────────────────────────────────────────

def _match(meta: dict, where: dict | None) -> bool:
    if not where:
        return True
    if "$and" in where:
        return all(_match(meta, w) for w in where["$and"])
    if "$or" in where:
        return any(_match(meta, w) for w in where["$or"])
    for key, want in where.items():
        if isinstance(want, dict):
            if "$gte" in want and not (meta.get(key) is not None and meta[key] >= want["$gte"]):
                return False
        elif meta.get(key) != want:
            return False
    return True


class _Col:
    """Just enough of a Chroma collection for MemoryService's metadata paths."""

    def __init__(self):
        self.rows: dict[str, tuple[str, dict]] = {}

    def upsert(self, *, ids, documents, metadatas, **_kw):
        for i, d, m in zip(ids, documents, metadatas):
            self.rows[i] = (d, dict(m))

    def update(self, *, ids, metadatas, **_kw):
        for i, m in zip(ids, metadatas):
            self.rows[i] = (self.rows[i][0], dict(m))

    def get(self, *, ids=None, where=None, include=None, **_kw):
        keys = [i for i in ids if i in self.rows] if ids is not None else list(self.rows)
        keys = [i for i in keys if _match(self.rows[i][1], where)]
        return {"ids": keys, "documents": [self.rows[i][0] for i in keys],
                "metadatas": [dict(self.rows[i][1]) for i in keys]}


def _svc(monkeypatch) -> tuple[MemoryService, _Col]:
    svc = MemoryService(data_dir="/nonexistent/zoe-test-implicit-supersede")
    col = _Col()
    svc._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def no_hits(*_a, **_k):
        return []

    async def opted_in(_uid):
        return False

    async def consenting(_uid, *_a, **_k):
        # pinned to the explicit `optin` mode below: the seeded member has consented (a stored mode)
        import persona_layer

        return persona_layer.MemberMode(mode="companion", minor=False)

    import persona_layer

    monkeypatch.setattr(persona_layer, "load_member_mode", consenting)
    monkeypatch.setenv("ZOE_AFFECT_CONSENT_GATE", "optin")  # explicit mode, not the (household) default
    svc._append_audit = no_audit
    svc.search = no_hits  # reconcile_for_ingest → ADD, exactly as measured live
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    return svc, col


async def _seed(svc, col, text, *, days_ago=3, memory_type="event", user=UID):
    ref = await svc.ingest(text, user_id=user, source="turn_digest", memory_type=memory_type,
                           confidence=0.82, status="approved", tags=["turn_digest"])
    when = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)
    doc, meta = col.rows[ref.id]
    meta.update(added_at=when.isoformat().replace("+00:00", "Z"), added_ts=when.timestamp())
    return ref.id


def _patch_llm(monkeypatch, facts, blob=""):
    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": json.dumps(facts)}}]}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return _Resp()

    monkeypatch.setattr(memory_digest.httpx, "AsyncClient", _Client)
    stub = types.ModuleType("zoe_agent")

    async def _blob(*a, **k):
        return blob

    stub._mempalace_load_user_facts = _blob
    stub._invalidate_user_facts_cache = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "zoe_agent", stub)


def _by_text(col, text):
    return next((i, m) for i, (d, m) in col.rows.items() if d == text)


def _flag(monkeypatch, on: bool):
    if on:
        monkeypatch.setenv(ms.ENV, "1")
    else:
        monkeypatch.delenv(ms.ENV, raising=False)


RACE_FACTS = [{"type": "event", "fact": TOMBSTONE}, {"type": "event", "fact": TEN_K}]


# ── the A/B scenario, end to end at the service layer ─────────────────────────

def test_ab_scenario_supersedes_old_race_and_card_shows_10k(monkeypatch, caplog):
    _flag(monkeypatch, True)
    svc, col = _svc(monkeypatch)
    _patch_llm(monkeypatch, RACE_FACTS)

    async def go():
        old = await _seed(svc, col, OLD_RACE)
        home = await _seed(svc, col, "User lives in Dunedin.", memory_type="profile")
        cello = await _seed(svc, col, "User plays the cello in a community orchestra.",
                            memory_type="habit")
        before = umc.build_card("Ottilie", await svc.list_by_status(user_id=UID, status="approved"))
        with caplog.at_level(logging.INFO, logger="memory_supersede"):
            res = await memory_digest.run_turn_digest(UID, SAY, session_id="s1")
        return old, home, cello, before, res

    old, home, cello, card_before, res = asyncio.run(go())
    assert res["new"] == 2 and res["superseded"] == 1

    tomb_id, tomb = _by_text(col, TOMBSTONE)
    new_id, new = _by_text(col, TEN_K)
    old_meta = col.rows[old][1]
    assert old_meta["status"] == "superseded"                     # kept, never deleted
    assert old_meta["superseded_by_id"] == new_id                 # the replacement, not the tombstone
    assert isinstance(old_meta["invalid_at"], float)
    assert new["supersedes_id"] == old and new["valid_from"] == new["added_ts"]
    assert tomb["memory_type"] == "state_change" and "state_change" in tomb["tags"].split(",")
    assert new["memory_type"] == "event" and tomb["valid_from"] == tomb["added_ts"]
    assert col.rows[home][1]["status"] == col.rows[cello][1]["status"] == "approved"
    assert f"MEMORY_SUPERSEDE user={UID} cue=change of plan superseded=1 new={new_id[:8]}" in caplog.text

    # the card, rebuilt from the store: the 10k is Current, the half-marathon and the
    # tombstone are not there
    rows = asyncio.run(svc.list_by_status(user_id=UID, status="approved"))
    text = umc.render_card(umc.build_card("Ottilie", rows))
    assert "Current: doing a 10k in May instead" in text
    assert "half" not in text and "dropped" not in text
    # …and the card stored BEFORE the turn stops serving the stale race at the next fetch
    assert "half-marathon" in umc.render_card(card_before)
    ids = [it[2] for it in card_before["items"]]
    live = asyncio.run(umc._live_ids(UID, ids))
    assert "half" not in umc.render_card(card_before, live)


def test_ab_scenario_flag_off_is_the_measured_failure(monkeypatch):
    """Negative control: without the flag the old race stays approved and the card
    shows it, beside the tombstone recited as a Current fact."""
    _flag(monkeypatch, False)
    svc, col = _svc(monkeypatch)
    _patch_llm(monkeypatch, RACE_FACTS)

    async def go():
        old = await _seed(svc, col, OLD_RACE)
        res = await memory_digest.run_turn_digest(UID, SAY, session_id="s1")
        return old, res

    old, res = asyncio.run(go())
    assert "superseded" not in res
    assert col.rows[old][1]["status"] == "approved"
    assert all("valid_from" not in m and "invalid_at" not in m for _, m in col.rows.values())
    assert _by_text(col, TOMBSTONE)[1]["memory_type"] == "event"
    rows = asyncio.run(svc.list_by_status(user_id=UID, status="approved"))
    text = umc.render_card(umc.build_card("Ottilie", rows))
    assert "half-marathon in March" in text and "dropped the half-marathon" in text


def test_flag_off_turn_digest_calls_are_byte_identical(monkeypatch):
    """Pin: flag off, run_turn_digest makes the exact legacy ingest call per fact, never
    reads the store for supersession and never calls supersede_by."""
    _flag(monkeypatch, False)
    calls = []

    class _Rec:
        async def ingest(self, text, **kw):
            calls.append((text, kw))
            return MemoryRef(id=f"n{len(calls)}", text=text)

        async def search(self, *a, **k):
            return []

        async def list_by_status(self, **_kw):
            raise AssertionError("no supersession read with the flag off")

        async def supersede_by(self, *a, **k):
            raise AssertionError("no supersede with the flag off")

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _Rec())
    _patch_llm(monkeypatch, RACE_FACTS)
    asyncio.run(memory_digest.run_turn_digest(UID, SAY, session_id="s1"))
    assert [(t, kw["memory_type"], kw["tags"], kw["metadata"]) for t, kw in calls] == [
        (TOMBSTONE, "event", ["turn_digest", "auto_extract"], None),
        (TEN_K, "event", ["turn_digest", "auto_extract"], None),
    ]


def test_build_metadata_writes_no_validity_keys_when_off(monkeypatch):
    _flag(monkeypatch, False)
    md = MemoryService._build_metadata(
        user_id=UID, source="turn_digest", session_id=None, user_turn_id=None,
        memory_type="fact", confidence=0.8, status="approved", tags=[], entity_type=None,
        entity_id=None, expires_at=None)
    assert "valid_from" not in md
    _flag(monkeypatch, True)
    md = MemoryService._build_metadata(
        user_id=UID, source="turn_digest", session_id=None, user_turn_id=None,
        memory_type="fact", confidence=0.8, status="approved", tags=[], entity_type=None,
        entity_id=None, expires_at=None)
    assert md["valid_from"] == md["added_ts"]


@pytest.mark.parametrize("on", [True, False])
def test_review_edit_marks_invalid_at_only_under_flag(monkeypatch, on):
    _flag(monkeypatch, on)
    svc, col = _svc(monkeypatch)

    async def go():
        old = await _seed(svc, col, "User's dad's name is Neil.", memory_type="relationship")
        new = await svc.review(old, decision="edit", actor="turn_digest",
                               edits="User's dad's name is Kevin.")
        return old, new

    old, new = asyncio.run(go())
    assert col.rows[old][1]["status"] == "superseded"
    assert ("invalid_at" in col.rows[old][1]) is on
    assert ("valid_from" in new.metadata) is on


# ── cue table ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,cue", [
    (SAY, "change of plan"),
    ("I've dropped the half-marathon.", "dropped"),
    ("I no longer eat meat.", "no longer"),
    ("I'm not doing yoga anymore.", "not anymore"),
    ("I stopped drinking coffee.", "stopped"),
    ("I quit my job at the bank.", "quit"),
    ("I gave up the cello.", "gave up"),
    ("We cancelled the trip to Bali.", "cancelled"),
    ("I used to live in Dunedin.", "used to"),
    ("We moved from Dunedin to Hobart.", "moved from"),
    ("I've moved to Hobart.", "moved to"),
    ("I'm doing a 10k instead.", "instead"),
    ("I switched to oat milk.", "switched to"),
    ("I changed to the early shift.", "changed to"),
    ("I now take the train rather than the bus.", "rather than"),
])
def test_cue_positives(text, cue):
    assert ms.utterance_cue(text) == cue


@pytest.mark.parametrize("text", [
    "I dropped my keys.", "I dropped the kids off at school.", "I dropped by the shop.",
    "I stopped at the shops on the way home.", "I got used to the cold.",
    "I'm used to early mornings.", "I'm training for a half-marathon.",
    "I live in Dunedin.", "What's the weather like?",
])
def test_cue_negatives(text):
    assert ms.utterance_cue(text) is None


def test_fact_cue_kinds_and_one_off():
    assert ms.fact_cue(TOMBSTONE).kind == "end" and ms.is_tombstone(TOMBSTONE)
    assert ms.fact_cue(TEN_K).kind == "swap" and not ms.is_tombstone(TEN_K)
    assert ms.fact_cue("User had tea instead of coffee this morning.") is None  # one-off
    assert ms.fact_cue("User is training for a half-marathon.") is None
    assert ms.utterance_cue(SAY) and ms.fact_cue("Change of plan for the user.") is None


# ── same-topic matcher boundaries ─────────────────────────────────────────────

@pytest.mark.parametrize("new,old,same", [
    (TOMBSTONE, OLD_RACE, True),                                         # the A/B pair
    ("User used to live in Dunedin.", "User lives in Dunedin.", True),   # plural folded
    ("User stopped eating meat.", "User eats meat most days.", True),
    (TEN_K, OLD_RACE, False),                     # no shared topic: the tombstone bridges
    ("User's sister no longer lives in Perth.", "User lives in Perth.", False),  # subject
    ("Tom quit the band.", "User plays guitar in the band.", False),             # owner
    ("User dropped their keys.", "User keeps a spare key under the mat.", False),
    # one shared word may not retire a fact that is mostly about something else
    ("User gave up the cello.", "User plays the cello in a community orchestra.", False),
    ("User quit the community orchestra.", "User plays the cello in a community orchestra.", True),
])
def test_same_topic_boundaries(new, old, same):
    assert ms.same_topic(new, old) is same


def test_home_slot_is_exclusive_and_subject_bound():
    assert ms.exclusive_conflict("User moved to Hobart.", "User lives in Dunedin.")
    assert not ms.exclusive_conflict("User lives in Dunedin.", "User lives in Dunedin.")
    assert not ms.exclusive_conflict("User's sister lives in Perth.", "User lives in Dunedin.")
    assert ms.home_value("User no longer lives in Dunedin.") is None  # a past home is not one


# ── write-time rules on the real store ────────────────────────────────────────

def _turn(monkeypatch, say, facts, seeds, *, on=True, blob=""):
    _flag(monkeypatch, on)
    svc, col = _svc(monkeypatch)
    _patch_llm(monkeypatch, facts, blob=blob)

    async def go():
        ids = [await _seed(svc, col, t, memory_type=mt) for t, mt in seeds]
        res = await memory_digest.run_turn_digest(UID, say, session_id="s1")
        return ids, res

    ids, res = asyncio.run(go())
    return col, ids, res


def test_used_to_live_in_supersedes_the_home_row(monkeypatch):
    col, (home, cello), res = _turn(
        monkeypatch, "I used to live in Dunedin, it was lovely.",
        [{"type": "profile", "fact": "User used to live in Dunedin."}],
        [("User lives in Dunedin.", "profile"), ("User plays the cello.", "habit")])
    assert res["superseded"] == 1
    assert col.rows[home][1]["status"] == "superseded"
    assert col.rows[cello][1]["status"] == "approved"


def test_dropped_my_keys_supersedes_nothing(monkeypatch):
    col, ids, res = _turn(
        monkeypatch, "Ugh, I dropped my keys down the drain this morning.",
        [{"type": "event", "fact": "User dropped their keys down the drain."}],
        [("User keeps a spare key under the mat.", "fact"),
         ("User lost their car keys last week.", "event"), (OLD_RACE, "event")])
    assert "superseded" not in res
    assert all(col.rows[i][1]["status"] == "approved" for i in ids)
    assert _by_text(col, "User dropped their keys down the drain.")[1]["memory_type"] == "event"


def test_change_fact_skips_the_word_overlap_dedup_only_under_flag(monkeypatch):
    """"User no longer lives in Dunedin." scores 0.83 on the legacy word-overlap dedup
    against the fact it retires, so it was dropped as a duplicate (control: flag off)."""
    blob = "## What I know about you:\n- User lives in Dunedin.\n- User plays the cello."
    say = "I no longer live in Dunedin."
    facts = [{"type": "profile", "fact": "User no longer lives in Dunedin."}]
    col, (home,), res = _turn(monkeypatch, say, facts, [("User lives in Dunedin.", "profile")],
                              on=False, blob=blob.lower())
    assert res["skipped_duplicates"] == 1 and col.rows[home][1]["status"] == "approved"
    col, (home,), res = _turn(monkeypatch, say, facts, [("User lives in Dunedin.", "profile")],
                              on=True, blob=blob.lower())
    assert res["superseded"] == 1 and col.rows[home][1]["status"] == "superseded"


def test_positive_fact_without_its_own_cue_retires_nothing(monkeypatch):
    """The cue gate is per FACT: an unrelated fact from a cue turn cannot act."""
    col, (sister,), res = _turn(
        monkeypatch, "Change of plan: my sister is visiting in May.",
        [{"type": "relationship", "fact": "User's sister is visiting in May."}],
        [("User's sister lives in Perth.", "relationship")])
    assert "superseded" not in res and col.rows[sister][1]["status"] == "approved"


def test_emotional_and_tombstone_rows_are_never_targets(monkeypatch):
    col, (emo, tomb, race), res = _turn(
        monkeypatch, "I've dropped the half-marathon.",
        [{"type": "event", "fact": TOMBSTONE}],
        [("User is anxious about the half-marathon.", "emotional_moment"),
         ("User has dropped the half-marathon for now.", "state_change"), (OLD_RACE, "event")])
    assert res["superseded"] == 1 and col.rows[race][1]["status"] == "superseded"
    assert all(col.rows[i][1]["status"] == "approved" for i in (emo, tomb))


def test_supersede_by_guards(monkeypatch):
    svc, col = _svc(monkeypatch)

    async def go():
        a = await _seed(svc, col, "User lives in Dunedin.")
        b = await _seed(svc, col, "User lives in Hobart.", days_ago=1)
        other = await _seed(svc, col, "User lives in Perth.", user="member-b")
        return (a, b, other,
                await svc.supersede_by(UID, a, a, actor="t"),          # same row
                await svc.supersede_by(UID, a, other, actor="t"),      # other user's row
                await svc.supersede_by(UID, a, b, actor="t"),          # ok
                await svc.supersede_by(UID, a, b, actor="t"))          # idempotent

    a, b, other, same, cross, ok, again = asyncio.run(go())
    assert (same, cross, ok, again) == (False, False, True, False)
    assert col.rows[b][1]["supersedes_id"] == a


# ── nightly pass ──────────────────────────────────────────────────────────────

def _ref(i, text, days_ago, mtype="fact", status="approved"):
    when = (dt.datetime(2026, 9, 30, tzinfo=dt.timezone.utc) - dt.timedelta(days=days_ago))
    return MemoryRef(id=f"r{i}", text=text, metadata={
        "status": status, "memory_type": mtype, "added_at": when.isoformat()})


def test_conflict_pairs_newer_cue_or_home_only():
    rows = [_ref(1, OLD_RACE, 10), _ref(2, TOMBSTONE, 1, "state_change"),
            _ref(3, "User lives in Dunedin.", 20), _ref(4, "User lives in Hobart.", 2),
            _ref(5, "User plays the cello.", 30),
            # an OLDER cue row never retires a newer fact
            _ref(6, "User used to play the piano.", 40), _ref(7, "User plays the piano.", 5)]
    got = {(n.id, o.id, why) for n, o, why in ms.conflict_pairs(rows)}
    assert got == {("r2", "r1", "dropped"), ("r4", "r3", "home")}


def test_nightly_pass_cap_and_idempotence(monkeypatch, caplog):
    _flag(monkeypatch, True)
    svc, col = _svc(monkeypatch)
    places = ["Alba", "Bree", "Cleve", "Dover", "Elgin", "Fife", "Galt", "Hove", "Ilkley",
              "Jarrow", "Kendal", "Leeds"]

    async def go():
        for n, p in enumerate(places):
            await _seed(svc, col, f"User lives in {p}.", days_ago=20 + n, memory_type="profile")
        await _seed(svc, col, "User lives in Perth.", days_ago=1, memory_type="profile")
        with caplog.at_level(logging.INFO, logger="memory_supersede"):
            runs = [await memory_digest._implicit_conflict_pass(UID) for _ in range(3)]
        return runs

    runs = asyncio.run(go())
    assert runs == [{"pairs": 12, "superseded": 10}, {"pairs": 2, "superseded": 2},
                    {"pairs": 0, "superseded": 0}]
    assert f"MEMORY_CONFLICT_PASS user={UID} pairs=12 superseded=10" in caplog.text
    live = [d for d, m in col.rows.values() if m["status"] == "approved"]
    assert live == ["User lives in Perth."]


def test_nightly_pass_dry_run_writes_nothing(monkeypatch):
    svc, col = _svc(monkeypatch)

    async def go():
        a = await _seed(svc, col, "User lives in Dunedin.", days_ago=9)
        await _seed(svc, col, "User moved to Hobart.", days_ago=1)
        return a, await ms.nightly_conflict_pass(svc, UID, dry_run=True)

    a, out = asyncio.run(go())
    assert out == {"pairs": 1, "superseded": 0} and col.rows[a][1]["status"] == "approved"


def test_nightly_pass_flag_off_reads_nothing(monkeypatch):
    _flag(monkeypatch, False)

    def boom():
        raise AssertionError("no store access with the flag off")

    monkeypatch.setattr(memory_service, "get_memory_service", boom)
    assert asyncio.run(memory_digest._implicit_conflict_pass(UID)) is None


def test_nightly_pass_skips_opted_out_user(monkeypatch):
    _flag(monkeypatch, True)
    svc, _ = _svc(monkeypatch)

    async def opted_out(_uid):
        return True

    monkeypatch.setattr(memory_service, "_user_opted_out", opted_out)
    assert asyncio.run(memory_digest._implicit_conflict_pass(UID)) == {"skipped": "opt_out"}


# ── card + packet treat the tombstone as a change ─────────────────────────────

def test_card_never_lists_a_state_change_row():
    tomb = _ref(1, TOMBSTONE, 0, "state_change")
    tagged = MemoryRef(id="r2", text="User no longer runs.", metadata={
        "status": "approved", "memory_type": "fact", "tags": "turn_digest,state_change",
        "added_at": "2026-09-30T00:00:00Z"})
    assert umc.build_card("A", [tomb, tagged])["items"] == []
    assert umc.categorize("User is doing a 5k fun run on Sunday.", {}).key == "current"
    assert umc.categorize("User is training for a triathlon.", {}).key == "current"


def test_packet_marks_the_tombstone_as_a_change():
    from routers.memories import _build_memory_prompt_packet

    rows = [_ref(1, TEN_K, 0, "event"), _ref(2, TOMBSTONE, 0, "state_change")]
    packet = _build_memory_prompt_packet(rows, [])["packet"]
    assert "- (change) User dropped the half-marathon." in packet
    assert "- User is doing a 10k in May instead." in packet


@pytest.mark.parametrize("on", [True, False])
def test_dreaming_runs_the_pass_before_the_card(monkeypatch, on):
    _flag(monkeypatch, on)
    order = []

    async def nop(*a, **k):
        return {}

    async def fake_pass(uid):
        order.append("conflicts")
        return None if not on else {"pairs": 0, "superseded": 0}

    async def fake_card(uid, db=None):
        order.append("card")
        return {"status": "disabled"}

    for name in ("_rem_reinforce_pass", "_resolve_pending_person_links", "_extract_open_loops"):
        monkeypatch.setattr(memory_digest, name, nop)
    monkeypatch.setattr(memory_digest, "_implicit_conflict_pass", fake_pass)
    monkeypatch.setattr(umc, "rebuild_user_model_card", fake_card)
    # run_dreaming_cycle imports datetime locally: pin a Monday (no Sunday phases)
    real = dt.datetime

    class _Mon(real):
        @classmethod
        def utcnow(cls):
            return real(2026, 9, 28, 1, 0)

    monkeypatch.setattr(dt, "datetime", _Mon)
    res = asyncio.run(memory_digest.run_dreaming_cycle(UID, run_agent_sync_phase=False))
    assert order == ["conflicts", "card"]
    assert ("implicit_conflicts" in res) is on


# ── corrections: a change of state with no change verb (day-sim ask 3, 2026-10-04) ──
MUM_FIX = "Actually, I got that wrong earlier - my mum lives in Bendigo, not Ballarat."
MUM_OLD = "User's mum Ingrid lives in Ballarat."
MUM_NEW = "User's mum lives in Bendigo."


@pytest.mark.parametrize("text", [
    MUM_FIX,
    "Wait, I meant Tuesday, not Thursday.",
    "Sorry, that's wrong - her name is Kate.",
    "I was wrong about the time, it's at 3.",
    "No, I got it wrong, the dentist is Friday.",
    "Correction: the race is in September.",
])
def test_correction_cue_positives(text):
    assert ms.utterance_cue(text) == "correction"


@pytest.mark.parametrize("text", [
    "Actually, I'd love a coffee.",                 # discourse "actually", no correction
    "Actually I'm not sure what to cook tonight.",  # "not sure" is not a correction
    "No, Caitlin is allergic to shellfish.",        # ambiguous negation: the clarifier's job
    "It's not too bad today.",
    "Change of plan: I'm doing the 12k instead.",   # stays its own cue
])
def test_correction_cue_negatives(text):
    assert ms.utterance_cue(text) != "correction"


def test_changes_existing_matches_the_home_slot_and_same_topic_only():
    rows = [types.SimpleNamespace(text=MUM_OLD, metadata={"status": "approved", "memory_type": "relationship"}),
            types.SimpleNamespace(text="User plays the cello.", metadata={"status": "approved", "memory_type": "habit"})]
    assert ms.changes_existing(MUM_NEW, rows)                       # exclusive home slot, same subject
    assert not ms.changes_existing("User's sister is visiting in May.", rows)
    # a superseded / state-change row is never a target
    gone = [types.SimpleNamespace(text=MUM_OLD, metadata={"status": "superseded", "memory_type": "relationship"})]
    assert not ms.changes_existing(MUM_NEW, gone)


def test_correction_retires_the_old_home_row_only_under_flag(monkeypatch):
    """Measured 2026-10-04 (day-sim ask 3, both runs): the corrected fact scores 0.83
    on the word-overlap dedup against the row it replaces and was dropped — the
    reply then asserted Ballarat. Control: flag off keeps the measured failure."""
    blob = f"## What I know about you:\n- {MUM_OLD}\n- User plays the cello."
    facts = [{"type": "relationship", "fact": MUM_NEW}]
    seeds = [(MUM_OLD, "relationship"), ("User plays the cello.", "habit")]
    col, (old, cello), res = _turn(monkeypatch, MUM_FIX, facts, seeds, on=False, blob=blob.lower())
    assert res["skipped_duplicates"] == 1 and col.rows[old][1]["status"] == "approved"
    col, (old, cello), res = _turn(monkeypatch, MUM_FIX, facts, seeds, on=True, blob=blob.lower())
    assert res["superseded"] == 1
    assert col.rows[old][1]["status"] == "superseded"
    assert col.rows[cello][1]["status"] == "approved"
    new_id, new_meta = _by_text(col, MUM_NEW)
    assert new_meta["status"] == "approved"
