"""ZOE_MEMORY_NOW_CARD: the per-member now card (now_card.py), its seam (zoe_flue_client) and the real sidecar (src/now-card.ts).

Pins, each with a control that turns it red: content rules (no inferred / derived / pending row, no private or off-the-wall class, no
muted topic, no held / replaced / leave quote, no forgotten name, a forget-ledger OUTAGE drops the line), the token budget, the same
inputs give the same bytes, off and shadow are byte-identical on the wire (shadow logs ids and sizes, never text), the card rides the
SYSTEM prompt after the user-model card on one envelope line (the wake warm sends the same line), a guest / unverified voice gets
nothing and nothing is read, a minor gets no night-mind thread, the forget paths erase the row. Synthetic names only, no network.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import shutil
import subprocess
from pathlib import Path

import pytest

import manner_block
import memory_forgotten
import night_mind
import night_store
import now_card as nc
import restraint
import traffic_class
import zoe_flue_client as zc
from memory_service import MemoryService

pytestmark = pytest.mark.ci_safe

UID = "zed_uid"
TODAY = dt.date(2026, 11, 4)
LAB = Path(__file__).resolve().parents[3] / "labs" / "flue-zoe-brain-2x"
WALK = "User walks their kelpie Juniper along the river every morning at 6am."
HEALTH = "User takes insulin for diabetes before dinner."
MONEY = "User's mortgage repayment is due Friday and money is tight."


def row(i, text, *, uid=UID, cls="user_stated", status="approved", ts=1_760_000_000.0, **meta):
    md = {"status": status, "user_id": uid, "memory_type": "fact", "added_ts": ts, "authority_class": cls, "source": "chat_regex"}
    md.update(meta)
    return (i, text, md)


class _Col:
    def __init__(self, svc):
        self.svc = svc

    def get(self, where=None, ids=None, include=None):
        rows = [r for r in self.svc.rows if ids is None or r[0] in ids]
        return {"ids": [r[0] for r in rows], "documents": [r[1] for r in rows], "metadatas": [dict(r[2]) for r in rows]}


class FakeSvc(MemoryService):
    """The REAL ``load_durable_for_hop`` / ``recheck_durable`` over an in-memory collection."""

    def __init__(self, rows):
        self.rows, self.reads, self.fail = list(rows), 0, False

    def _collection(self):
        return _Col(self)

    async def _run_sync(self, fn, *args):
        self.reads += 1
        if self.fail:
            raise RuntimeError("store down")
        return fn(*args)


class DictStore:
    def __init__(self):
        self.rows, self.reads = {}, 0

    async def read(self, uid):
        self.reads += 1
        return self.rows.get(uid)

    async def write(self, uid, card_json, text, ver, built_on, built_at):
        self.rows[uid] = (card_json, text, ver, built_on)

    async def remove(self, uid):
        return 1 if self.rows.pop(uid, None) else 0


DATES = [("p_mia", "Mia's birthday", 11, 8)]


@pytest.fixture(autouse=True)
def lab(monkeypatch):
    for k in ("ZOE_MEMORY_NOW_CARD", "ZOE_MEMORY_NOW_CARD_HARNESS", "ZOE_MANNER_BLOCK", "ZOE_PERSONA_LAYER", "ZOE_FLUE_WIRE",
              "ZOE_RECALL_EVERY_TURN", "ZOE_RISK_CUES", "ZOE_SEAM_RECALL_INJECT", "ZOE_TRAFFIC_CLASS", "ZOE_RESTRAINT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_NIGHT_MIND", "serve")
    monkeypatch.setenv("ZOE_SEAM_CONTINUITY_INJECT", "0")
    monkeypatch.setenv("ZOE_IDENTITY_BLOCK", "0")
    st = DictStore()
    nc.set_store(st)
    prev_backend = night_store._backend
    backend = night_store.MemoryBackend()
    night_store.set_backend(backend)
    night_mind._reset_state()

    async def no_mutes(_uid):
        return []

    async def not_forgotten(_uid, _text, **_k):
        return False

    async def adult(_uid, db=None):
        return True

    async def name(_uid):
        return "Zed"

    async def dates(_uid, _mutes=()):
        return tuple(DATES)

    async def strict_mutes(uid):                  # the serve path reads the strict list: route it through whatever list_mutes a test set
        return await restraint.list_mutes(uid)

    monkeypatch.setattr(restraint, "list_mutes", no_mutes)
    monkeypatch.setattr(restraint, "list_mutes_strict", strict_mutes)
    monkeypatch.setattr(memory_forgotten, "matches", not_forgotten)
    monkeypatch.setattr(manner_block, "is_adult", adult)
    monkeypatch.setattr(nc, "_name", name)
    monkeypatch.setattr(nc, "_dates", dates)
    # The suite's threads are dated 2026-11-03 and its TODAY is 2026-11-04: freeze the card's "now" there. Left on the wall clock, every
    # build/serve test went red once the real date passed THREAD_RECENT_DAYS from the seeds (2026-11-09, all PRs' validate at once).
    # A ``now`` the test passes (card_for(now=...)) is still honoured.
    real_local_today = nc._local_today
    frozen = dt.datetime(2026, 11, 4, 10, 0, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(nc, "_local_today", lambda now=None: real_local_today(now or frozen))
    yield st, backend
    asyncio.run(nc.drain())
    nc.set_store(nc.SqlStore())
    night_store.set_backend(prev_backend)
    night_mind._reset_state()


def seed_thread(backend, tid, quote, *, day="2026-11-03", status="open", state="current", feeling="none", kind="progress",
                raise_policy="wait", leave="", mentions=2, uid=UID):
    backend.thread_rows[tid] = night_store.thread_row(id=tid, user_id=uid, title=tid, status=status, first_day="2026-10-30",
                                                      last_day=day, mentions_n=mentions, weight_max=2, last_feeling=feeling,
                                                      raise_policy=raise_policy, leave_reason=leave, source_ref=f"night_threads:{tid}")
    oid = f"o_{tid}"
    backend.obs_rows[oid] = night_store.obs_row(id=oid, user_id=uid, thread_id=tid, turn_id=f"t_{tid}", quote=quote, kind=kind,
                                                feeling=feeling, day=day, said_at=1.0, state=state)
    return oid


def build(svc, today=TODAY, uid=UID):
    return asyncio.run(nc.gather(uid, today, svc))[0]


# ── composition (pure) ────────────────────────────────────────────────────────────────────────────────

FULL = nc.Inputs(name="Zed", threads=(("o1", "2026-11-03", "the shed roof is finally going up on Saturday"),
                                      ("o2", "2026-11-02", "I started the cello again after ten years")),
                 dates=tuple(DATES), facts=(("m1", WALK),))


def test_the_same_inputs_give_the_same_bytes():
    a = nc.render(TODAY.isoformat(), nc.compose(FULL, TODAY))
    b = nc.render(TODAY.isoformat(), nc.compose(nc.Inputs(**FULL.__dict__), TODAY))
    assert a == b and a and nc.version(a) == nc.version(b)
    assert "Speaking with: Zed" in a and "Coming up: Mia's birthday, Sun 8 Nov" in a and "They told you:" in a
    assert a.index("Speaking with") < a.index("they said") < a.index("Coming up") < a.index("They told you")


def test_the_card_fits_its_budget_and_nothing_is_padded():
    text = nc.render(TODAY.isoformat(), nc.compose(FULL, TODAY))
    assert 60 <= nc.est_tokens(text) <= nc.MAX_TOKENS
    huge = nc.Inputs(name="Zed " * 30, threads=tuple((f"o{i}", "2026-11-03", "word " * 80) for i in range(5)),
                     dates=tuple(DATES), facts=(("m1", "fact " * 80),))
    lines = nc.compose(huge, TODAY)
    assert nc.est_tokens(nc.render(TODAY.isoformat(), lines)) <= nc.MAX_TOKENS and len(lines) <= 5
    assert nc.compose(nc.Inputs(name="Zed"), TODAY) == []          # a speaker alone is no card


def test_negative_control_the_budget_is_what_binds(monkeypatch):
    # the per-line cuts hold a Latin card near 155 tokens; a CJK card (~0.8 token a character) is where the budget binds
    big = nc.Inputs(name="Zed", threads=(("o1", "2026-11-03", "棚" * 200), ("o2", "2026-11-02", "琴" * 200)),
                    dates=tuple(DATES), facts=(("m1", "魚" * 200),))
    bound = nc.compose(big, TODAY)
    assert nc.est_tokens(nc.render(TODAY.isoformat(), bound)) <= nc.MAX_TOKENS and len(bound) < 5
    monkeypatch.setattr(nc, "MAX_TOKENS", 10_000)
    assert nc.est_tokens(nc.render(TODAY.isoformat(), nc.compose(big, TODAY))) > 180


@pytest.mark.parametrize("today,month,day,want", [
    (TODAY, 11, 4, "2026-11-04"), (TODAY, 11, 11, "2026-11-11"), (TODAY, 11, 12, None), (TODAY, 11, 3, None),
    (dt.date(2026, 12, 30), 1, 2, "2027-01-02"), (dt.date(2027, 2, 26), 2, 29, None)])
def test_the_next_date_is_inside_seven_days(today, month, day, want):
    got = nc.next_date((("p", "X's birthday", month, day),), today)
    assert (got[0].isoformat() if got else None) == want


# ── content rules (the night) ─────────────────────────────────────────────────────────────────────────

def test_negative_control_a_stated_fact_and_an_open_thread_enter(lab):
    _st, backend = lab
    oid = seed_thread(backend, "shed", "the shed roof is finally going up on Saturday")
    inp = build(FakeSvc([row("m_walk", WALK)]))
    assert inp.facts == (("m_walk", WALK),) and inp.threads == ((oid, "2026-11-03", "the shed roof is finally going up on Saturday"),)


@pytest.mark.parametrize("meta", [dict(cls="", authority="inferred"), dict(cls="model_from_turn"), dict(cls="model_from_transcript"),
                                  dict(cls="user_stated_derived"), dict(status="pending"), dict(status="superseded")])
def test_no_inferred_derived_or_unapproved_fact(meta):
    assert build(FakeSvc([row("m_walk", WALK, **meta)])).facts == ()
    assert build(FakeSvc([row("m_walk", WALK, cls="user_confirmed")])).facts == (("m_walk", WALK),)      # control


@pytest.mark.parametrize("text", [HEALTH, MONEY, "User felt anxious and overwhelmed all week."])
def test_no_private_or_off_the_wall_fact(text):
    assert build(FakeSvc([row("m_x", text)])).facts == ()
    assert build(FakeSvc([row("m_x", text), row("m_walk", WALK, ts=1.0)])).facts == (("m_walk", WALK),)   # control


def test_a_muted_or_private_date_never_enters(monkeypatch):
    rows = [("p_mia", "Mia", "birthday", None, 11, 8), ("p_vet", "Rex", "other", "chemotherapy session", 11, 6)]

    async def db_rows(sql, _params):
        return [(pid, kind, label, m, d, name) for pid, name, kind, label, m, d in rows] if "person_important_dates" in sql else []

    monkeypatch.undo()                                                     # the real _dates, over a fake database
    monkeypatch.setattr(nc, "_db_rows", db_rows)
    got = asyncio.run(nc._dates(UID, []))
    assert [r[0] for r in got] == ["p_mia"]                                # a health label never enters
    assert asyncio.run(nc._dates(UID, [restraint.Mute(id="m", stems=frozenset(restraint.stems("Mia birthday")))])) == ()


def test_a_failed_age_lookup_means_no_night_thread(lab, monkeypatch):
    _st, backend = lab
    seed_thread(backend, "shed", "the shed roof is finally going up on Saturday")

    async def down(_uid, db=None):
        raise RuntimeError("auth db down")

    monkeypatch.setattr(manner_block, "is_adult", down)
    inp, errors = asyncio.run(nc.gather(UID, TODAY, FakeSvc([row("m_walk", WALK)])))
    assert inp.threads == () and inp.facts == (("m_walk", WALK),) and errors == ["adult:RuntimeError"]


def test_another_members_row_never_enters():
    assert build(FakeSvc([row("m_walk", WALK, uid="yan_uid")])).facts == ()


def test_a_muted_topic_never_enters(monkeypatch):
    async def mutes(_uid):
        return [restraint.Mute(id="mu1", stems=frozenset(restraint.stems("kelpie river")))]

    monkeypatch.setattr(restraint, "list_mutes", mutes)
    assert build(FakeSvc([row("m_walk", WALK)])).facts == ()


@pytest.mark.parametrize("kw", [dict(state="held"), dict(state="history"), dict(leave="health"), dict(raise_policy="leave"),
                                dict(status="resolved"), dict(feeling="worried"), dict(kind="health"), dict(day="2026-10-01")])
def test_no_held_replaced_leave_resolved_heavy_or_old_thread(lab, kw):
    _st, backend = lab
    seed_thread(backend, "shed", "the shed roof is finally going up on Saturday", **kw)
    assert build(FakeSvc([])).threads == ()


def test_threads_are_off_while_the_night_minds_readers_are_off(lab, monkeypatch):
    _st, backend = lab
    seed_thread(backend, "shed", "the shed roof is finally going up on Saturday")
    monkeypatch.setenv("ZOE_NIGHT_MIND", "off")
    assert build(FakeSvc([])).threads == ()


def test_a_minor_gets_no_night_mind_thread_d7(lab, monkeypatch):
    _st, backend = lab
    seed_thread(backend, "shed", "the shed roof is finally going up on Saturday")

    async def child(_uid, db=None):
        return False

    monkeypatch.setattr(manner_block, "is_adult", child)
    inp = build(FakeSvc([row("m_walk", WALK)]))
    assert inp.threads == () and inp.facts == (("m_walk", WALK),) and inp.dates == tuple(DATES)


def test_a_forgotten_name_never_enters_and_a_ledger_outage_drops_the_line(lab, monkeypatch):
    _st, backend = lab
    seed_thread(backend, "shed", "Juniper chewed the shed door again")

    async def forgot(_uid, text, **_k):
        return "Juniper" in text

    monkeypatch.setattr(memory_forgotten, "matches", forgot)
    inp = build(FakeSvc([row("m_walk", WALK), row("m_fish", "User is pescatarian.", ts=1.0)]))
    assert inp.threads == () and inp.facts == (("m_fish", "User is pescatarian."),)

    async def down(*_a, **_k):
        raise memory_forgotten.LedgerUnavailable("down")

    monkeypatch.setattr(memory_forgotten, "matches", down)
    inp = build(FakeSvc([row("m_fish", "User is pescatarian.")]))
    assert inp.facts == () and inp.threads == ()


def test_a_failing_source_contributes_nothing_and_the_rest_stands(lab, monkeypatch):
    svc = FakeSvc([row("m_walk", WALK)])
    svc.fail = True                                               # the palace swallows its own failure: no facts
    inp, _errors = asyncio.run(nc.gather(UID, TODAY, svc))
    assert inp.facts == () and inp.dates == tuple(DATES)

    async def broken(_uid, _mutes=()):
        raise RuntimeError("people table down")

    monkeypatch.setattr(nc, "_dates", broken)
    inp, errors = asyncio.run(nc.gather(UID, TODAY, FakeSvc([row("m_walk", WALK)])))
    assert inp.dates == () and inp.facts == (("m_walk", WALK),) and errors == ["dates:RuntimeError"]


def test_the_card_is_deterministic_over_the_store_order(lab):
    _st, backend = lab
    seed_thread(backend, "a", "the shed roof is finally going up on Saturday")
    seed_thread(backend, "b", "I started the cello again after ten years")
    rows = [row("m_walk", WALK, ts=5.0), row("m_fish", "User is pescatarian.", ts=5.0)]
    one = nc.compose(build(FakeSvc(rows)), TODAY)
    two = nc.compose(build(FakeSvc(list(reversed(rows)))), TODAY)
    assert one == two and one


# ── serving (the day) ─────────────────────────────────────────────────────────────────────────────────

def _built(lab, monkeypatch, svc=None, mode="on"):
    st, backend = lab
    seed_thread(backend, "th01", "the shed roof is finally going up on Saturday")
    svc = svc or FakeSvc([row("m01", WALK)])
    import memory_service

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: svc)
    monkeypatch.setenv("ZOE_MEMORY_NOW_CARD", mode)
    out = asyncio.run(nc.rebuild(UID))
    assert out["status"] == "ok" and out["threads"] == 1 and out["date"] == 1 and out["fact"] == 1
    st.reads = 0
    return st, svc


def serve(uid=UID, voice=False):
    async def go():
        out = await nc.block_for(uid, voice=voice)
        await nc.drain()
        return out
    return asyncio.run(go())


def test_off_builds_nothing_and_reads_nothing(lab):
    st, _b = lab
    assert asyncio.run(nc.rebuild(UID)) == {"status": "off"} and st.rows == {}
    st.rows[UID] = ("{}", "x", "v", TODAY.isoformat())
    assert serve() == "" and st.reads == 0


def test_on_serves_the_stored_card_byte_for_byte(lab, monkeypatch):
    st, _svc = _built(lab, monkeypatch)
    assert serve() == st.rows[UID][1] and st.reads == 1
    assert serve() == st.rows[UID][1] and st.reads == 1          # read by key, cached


@pytest.mark.parametrize("uid", ["guest", "guest-abc123", "voice-daemon", "", "demo_bar_00000001", "probe_x"])
def test_guests_and_synthetic_ids_get_nothing_and_nothing_is_read(lab, monkeypatch, uid):
    st, _svc = _built(lab, monkeypatch)
    assert serve(uid) == "" and st.reads == 0


def test_an_unverified_voice_gets_nothing_voice_is_a_password(lab, monkeypatch):
    st, _svc = _built(lab, monkeypatch)
    assert serve(voice=True) == "" and st.reads == 0                 # no verdict is not a yes
    tok = restraint._VERDICT.set(False)
    try:
        assert serve() == "" and serve(voice=True) == ""
    finally:
        restraint._VERDICT.reset(tok)
    tok = restraint._VERDICT.set(True)                               # control: a confirmed voice
    try:
        assert serve(voice=True) == st.rows[UID][1]
    finally:
        restraint._VERDICT.reset(tok)


@pytest.mark.parametrize("cls", [traffic_class.HARNESS, traffic_class.PROBE, traffic_class.CI])
def test_household_traffic_only(lab, monkeypatch, cls):
    st, _svc = _built(lab, monkeypatch)
    tok = traffic_class.bind(cls)
    try:
        assert serve() == "" and st.reads == 0
    finally:
        traffic_class.reset(tok)


def test_a_stale_card_is_not_served_and_yesterdays_serves_until_five(lab, monkeypatch):
    st, _svc = _built(lab, monkeypatch)
    tz = nc._local_today().tzinfo
    row_ = st.rows[UID]
    built = dt.date.fromisoformat(row_[3])
    early = dt.datetime.combine(built + dt.timedelta(days=1), dt.time(4, 30), tzinfo=tz)
    late = dt.datetime.combine(built + dt.timedelta(days=1), dt.time(5, 30), tzinfo=tz)
    assert asyncio.run(nc.card_for(UID, now=early))[0] == row_[1]
    assert asyncio.run(nc.card_for(UID, now=late)) == ("", [], "stale")


def test_a_forget_after_the_build_drops_the_line_on_the_next_turn(lab, monkeypatch):
    st, _svc = _built(lab, monkeypatch)

    async def forgot(_uid, text, **_k):
        return "Mia" in text

    monkeypatch.setattr(memory_forgotten, "matches", forgot)
    out = serve()
    assert out and "Mia" not in out and "Juniper" in out and out != st.rows[UID][1]


def test_a_ledger_outage_at_serve_time_fails_closed(lab, monkeypatch):
    _built(lab, monkeypatch)

    async def down(*_a, **_k):
        raise RuntimeError("ledger down")

    monkeypatch.setattr(memory_forgotten, "matches", down)
    assert serve() == ""


def test_a_fact_retired_after_the_build_drops_out(lab, monkeypatch):
    st, svc = _built(lab, monkeypatch)
    svc.rows[0] = row("m01", WALK, status="superseded")
    out = serve()
    assert out and "Juniper" not in out and "Mia" in out


def test_an_erased_observation_drops_out(lab, monkeypatch):
    st, _svc = _built(lab, monkeypatch)
    _st, backend = lab
    asyncio.run(night_mind.erase_entity(UID, "shed"))
    out = serve()
    assert out and "shed" not in out and "Mia" in out


def test_shadow_sends_nothing_and_logs_ids_and_sizes_only(lab, monkeypatch, caplog):
    _built(lab, monkeypatch, mode="shadow")
    with caplog.at_level(logging.INFO, logger="now_card"):
        assert serve() == ""
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("NOW_CARD ")]
    assert len(lines) == 1 and "mode=shadow served=0" in lines[0] and "lines=4" in lines[0] and "skipped=-" in lines[0]
    for word in ("Juniper", "shed", "Mia", "Zed", "kelpie"):
        assert word not in lines[0]


def test_the_forget_paths_erase_the_row(lab, monkeypatch):
    st, _svc = _built(lab, monkeypatch)
    assert asyncio.run(nc.erase_entity(UID, "Nobody")) == 0 and UID in st.rows      # control: a name the card does not carry
    assert asyncio.run(nc.erase_entity(UID, "Mia")) == 1 and UID not in st.rows
    _built(lab, monkeypatch)
    assert asyncio.run(nc.forget_user(UID)) == 1 and serve() == ""


def test_an_erase_that_cannot_reach_the_store_raises(lab):
    class Down(DictStore):
        async def read(self, uid):
            raise RuntimeError("db down")

    nc.set_store(Down())
    with pytest.raises(RuntimeError):
        asyncio.run(nc.erase_entity(UID, "Mia"))


# ── the seam: the wire, the warm, the real sidecar ────────────────────────────────────────────────────

@pytest.fixture
def sent(monkeypatch):
    async def nothing(*_a, **_k):
        return ""

    import flue_fastpath_context

    monkeypatch.setattr(zc, "_pending_offer_block", nothing)
    monkeypatch.setattr(flue_fastpath_context, "block_for", nothing)

    def turn(message, uid=UID, session="s1", **kwargs):
        cap = []

        async def fake_wire2(session_id, payload, **kw):
            cap.append(json.loads(payload)["body"])
            yield "ok"

        monkeypatch.setattr(zc, "_run_turn_aggregated_wire2", fake_wire2)

        async def go():
            [c async for c in zc.run_flue_brain_streaming(message, session, uid, **kwargs)]
            await nc.drain()

        asyncio.run(go())
        assert len(cap) == 1
        return cap[0]
    return turn


def test_off_and_shadow_are_todays_bytes_on_the_wire(lab, monkeypatch, sent):
    _built(lab, monkeypatch)
    monkeypatch.setenv("ZOE_MEMORY_NOW_CARD", "off")
    off = [sent(m, session=f"a{i}") for i, m in enumerate(("Good morning", "Set a timer for ten minutes"))]
    monkeypatch.setenv("ZOE_MEMORY_NOW_CARD", "shadow")
    assert [sent(m, session=f"b{i}") for i, m in enumerate(("Good morning", "Set a timer for ten minutes"))] == off
    assert all(zc._NOW_CARD_ENVELOPE_PREFIX not in m for m in off)


def test_on_adds_exactly_one_line_between_manner_and_identity(lab, monkeypatch, sent):
    st, _svc = _built(lab, monkeypatch)
    monkeypatch.setenv("ZOE_MEMORY_NOW_CARD", "off")
    off = sent("Good morning")
    monkeypatch.setenv("ZOE_MEMORY_NOW_CARD", "on")
    on = sent("Good morning")
    line = zc._NOW_CARD_ENVELOPE_PREFIX + json.dumps(st.rows[UID][1], ensure_ascii=True) + "\n"
    assert on == line + off                                              # identity innermost; nothing else moved
    assert zc.prefix_key(on) != zc.prefix_key(off) and zc.prefix_key(on.replace(line, "")) == zc.prefix_key(off)
    assert zc._NOW_CARD_ENVELOPE_PREFIX not in sent("Good morning", uid="guest")


def test_the_wake_warm_sends_the_card_the_next_turn_sends(lab, monkeypatch, sent):
    st, _svc = _built(lab, monkeypatch)
    monkeypatch.setenv("ZOE_MEMORY_NOW_CARD", "on")
    turn = sent("Good morning")
    warm, key = asyncio.run(zc._kv_warm_outbound(UID))
    card = zc._NOW_CARD_ENVELOPE_PREFIX + json.dumps(st.rows[UID][1], ensure_ascii=True) + "\n"
    assert card in warm and card in turn
    monkeypatch.setenv("ZOE_MEMORY_NOW_CARD", "shadow")
    assert zc._NOW_CARD_ENVELOPE_PREFIX not in asyncio.run(zc._kv_warm_outbound(UID))[0]


def test_the_seam_has_one_construction_site_and_the_sidecar_mirrors_the_prefix():
    src = Path(zc.__file__).read_text()
    assert src.count("_wrap_message_with_now_card(\n        outbound_message, await _now_card_context_block(") == 1
    ts = (LAB / "src" / "now-card.ts").read_text()
    assert "NOW_CARD_ENVELOPE_PREFIX = '%s'" % zc._NOW_CARD_ENVELOPE_PREFIX in ts
    assert nc.MAX_TOKENS * 4 <= 200 * 4 and "NOW_CARD_MAX_CHARS = NOW_CARD_MAX_TOKENS * 4" in ts
    cc = (LAB / "src" / "providers" / "capped-completions.ts").read_text()
    assert cc.count("withNowCardBlock(") == 1 and cc.count("forwardedNowCardFromMessages(") == 1 and cc.count("stripNowCardEnvelope(") == 4


_NODE = """
const chunks = [];
for await (const c of process.stdin) chunks.push(c);
const wire = JSON.parse(Buffer.concat(chunks).toString());
const base = new URL("file://" + process.argv[1] + "/src/");
const { ZOE_INSTRUCTIONS } = await import(new URL("agents/zoe.ts", base).href);
const { applyPolicies, bindIdentityForRound } = await import(new URL("providers/capped-completions.ts", base).href);
const { actingUserId } = await import(new URL("tools/zoe-tools.ts", base).href);
const msgs = wire.map((m) => ({ role: "user", content: m, timestamp: 0 }));
const ctx = { systemPrompt: ZOE_INSTRUCTIONS, messages: msgs, tools: [] };
const out = applyPolicies(ctx, "\\n\\nCARD");
const signal = new AbortController().signal;
bindIdentityForRound(ctx, signal);
console.log(JSON.stringify({ base: ZOE_INSTRUCTIONS, system: out.systemPrompt, texts: out.messages.map((m) => m.content), uid: actingUserId(signal) }));
"""

needs_sidecar = pytest.mark.skipif(shutil.which("node") is None or not (LAB / "node_modules").exists(),
                                   reason="node or the sidecar node_modules are not installed")


def _sidecar(wire_messages):
    res = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", _NODE, str(LAB)],
                         input=json.dumps(wire_messages), capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr[-600:]
    return json.loads(res.stdout)


@needs_sidecar
def test_the_real_sidecar_places_the_card_after_the_user_model_card_and_hides_the_envelope(lab, monkeypatch, sent):
    st, _svc = _built(lab, monkeypatch)
    monkeypatch.setenv("ZOE_MEMORY_NOW_CARD", "off")
    got = _sidecar([sent("Hello there")])
    assert got["system"] == got["base"] + "\n\nCARD" and got["texts"] == ["Hello there"] and got["uid"] == UID
    monkeypatch.setenv("ZOE_MEMORY_NOW_CARD", "on")
    got = _sidecar([sent("Hello there")])
    assert got["system"] == got["base"] + "\n\nCARD\n\n" + st.rows[UID][1]      # every byte up to the card is today's
    assert got["texts"] == ["Hello there"] and got["uid"] == UID                 # neither line reaches the model; identity binds


@needs_sidecar
def test_the_real_sidecar_reads_the_newest_turn_only(lab, monkeypatch, sent):
    st, _svc = _built(lab, monkeypatch)
    monkeypatch.setenv("ZOE_MEMORY_NOW_CARD", "on")
    older = sent("first")
    newer = sent("second", uid="guest")
    assert st.rows[UID][1] not in _sidecar([older, newer])["system"]
    assert _sidecar([newer, older])["system"].count(st.rows[UID][1]) == 1


def test_a_process_that_lost_its_pool_fails_closed_one_that_never_had_one_has_nothing(monkeypatch):
    import db_pool

    class NoPool:
        def __call__(self):
            raise RuntimeError("db_pool not initialised - call init_pool() first")

    nc.set_store(nc.SqlStore())
    monkeypatch.setattr(db_pool, "get_db_ctx", NoPool())
    monkeypatch.setattr(db_pool, "_ever_initialised", True)
    with pytest.raises(RuntimeError):
        asyncio.run(nc.erase_entity(UID, "Mia"))
    with pytest.raises(RuntimeError):
        asyncio.run(nc.forget_user(UID))
    monkeypatch.setattr(db_pool, "_ever_initialised", False)                  # control: a lab / a bare script
    assert asyncio.run(nc.erase_entity(UID, "Mia")) == 0 and asyncio.run(nc.forget_user(UID)) == 0


@pytest.mark.parametrize("uid,admitted", [("demo_bar_00000001", True), ("probe_x", False), ("guest", False)])
def test_the_harness_switch_builds_and_serves_harness_ids_only(lab, monkeypatch, uid, admitted):
    st, _b = lab
    import memory_service

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: FakeSvc([row("m01", WALK, uid=uid)]))
    monkeypatch.setenv("ZOE_MEMORY_NOW_CARD", "on")
    assert serve(uid) == "" and serve(uid) == ""                           # default: never, and nothing is built
    assert st.rows == {}
    monkeypatch.setenv("ZOE_MEMORY_NOW_CARD_HARNESS", "1")
    assert serve(uid) == ""                                                # the first turn starts the build, never waits for it
    assert (("Juniper" in serve(uid)) is admitted) and ((uid in st.rows) is admitted)


# ── Codex #2281: a mute named after the display cut; the night-mind CLI's pool lifetime ──────────────────

LONG = ("User restores old oak furniture in the back shed every weekend, sanding, staining and refinishing the chairs "
        "for the Saturday markets near Pinehaven.")


def test_a_mute_after_the_build_is_checked_against_the_whole_fact(lab, monkeypatch):
    assert len(LONG) > nc.FACT_CHARS and LONG.index("Pinehaven") > nc.FACT_CHARS
    st, _svc = _built(lab, monkeypatch, svc=FakeSvc([row("m01", LONG)]))
    stored = st.rows[UID][1]
    assert "oak furniture" in stored and "Pinehaven" not in stored          # the stored line is cut before the muted word
    mute = restraint.Mute(id="mu1", stems=frozenset(restraint.stems("Pinehaven")))
    line = next(t for k, t, _r in json.loads(st.rows[UID][0])["lines"] if k == "fact")
    assert not restraint.muted(line, "memory:m01", [mute])                   # control: the cut line alone never matches the mute
    assert restraint.muted(LONG, "memory:m01", [mute])

    async def mutes(_uid):
        return [mute]

    monkeypatch.setattr(restraint, "list_mutes", mutes)
    out = serve()
    assert out and "oak furniture" not in out and "Mia" in out


def test_the_night_mind_cli_rebuilds_the_cards_before_it_closes_its_pool(monkeypatch):
    """Codex #2281: run_members closed the pool in its finally, then the CLI rebuilt the cards - every SqlStore write had no pool."""
    import importlib.util
    import sys as _sys

    import db_pool

    spec = importlib.util.spec_from_file_location(
        "zoe_night_mind_cli_nowcard", Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "zoe-night-mind.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    pool = {"open": False, "events": []}

    async def init_pool():
        pool["open"] = True
        pool["events"].append("init")

    async def close_pool():
        pool["open"] = False
        pool["events"].append("close")

    async def probe(_cfg):
        return True, ""

    async def reflect(uid, *_a, **_k):
        return {"user_id": uid, "status": "ran"}

    async def rebuild_all(uids):
        pool["events"].append(("rebuild", pool["open"], tuple(uids)))
        return {u: "ok" for u in uids}

    import memory_service

    monkeypatch.setattr(db_pool, "init_pool", init_pool)
    monkeypatch.setattr(db_pool, "close_pool", close_pool)
    monkeypatch.setattr(memory_service, "get_memory_service", lambda: None)
    monkeypatch.setattr(night_mind, "probe_model", probe)
    monkeypatch.setattr(cli, "reflect_member", reflect)
    monkeypatch.setattr(nc, "rebuild_all", rebuild_all)
    monkeypatch.setitem(_sys.modules, "now_card", nc)
    code, _summary = asyncio.run(cli.amain(["--user", "zed_uid", "--model-url", "http://127.0.0.1:1/v1"]))
    assert ("rebuild", True, ("zed_uid",)) in pool["events"], pool["events"]
    assert pool["events"].index(("rebuild", True, ("zed_uid",))) < pool["events"].index("close")
    # control: a shadow (dry) pass never rebuilds
    pool["events"].clear()
    asyncio.run(cli.amain(["--user", "zed_uid", "--model-url", "http://127.0.0.1:1/v1", "--dry-run"]))
    assert not [e for e in pool["events"] if isinstance(e, tuple)]


def test_a_thread_muted_by_its_thread_ref_after_the_build_drops_out(lab, monkeypatch):
    """Codex #2281: a thread mute is stored against ``night_threads:<id>``; the card line points at the observation. Different
    wording, so only the observation -> thread mapping catches it."""
    st, _svc = _built(lab, monkeypatch)
    assert "shed roof" in st.rows[UID][1]
    mute = restraint.Mute(id="mu2", stems=frozenset(restraint.stems("renovation project")), thread_ref="night_threads:th01")
    obs_line = next(t for k, t, _r in json.loads(st.rows[UID][0])["lines"] if k == "thread")
    assert not restraint.muted(obs_line, "night_observations:o_th01", [mute])     # control: neither the words nor the obs ref match

    async def mutes(_uid):
        return [mute]

    monkeypatch.setattr(restraint, "list_mutes", mutes)
    out = serve()
    assert out and "shed roof" not in out and "Mia" in out


def test_an_unreadable_mute_list_serves_no_line(lab, monkeypatch):
    """Codex #2281: ``list_mutes`` reads a failed lookup as "nothing muted"; the serve path uses the strict read and withholds."""
    _built(lab, monkeypatch)
    assert serve()                                                              # control: readable mutes serve the card

    async def down(_uid):
        raise RuntimeError("mute table unreadable")

    monkeypatch.setattr(restraint, "list_mutes_strict", down)
    nc._ROWS.clear()
    assert serve() == ""


def test_a_member_marked_a_minor_after_the_build_stops_getting_thread_lines(lab, monkeypatch):
    """Codex #2281: the adult check runs at build time only; an admin's later PUT /api/persona/modes marks the member a minor, and the
    stored card must stop serving night-mind thread lines at once (a failed lookup drops them too). The non-thread lines stay."""
    st, _svc = _built(lab, monkeypatch)
    assert "shed roof" in serve()                                                # control: an adult is served the thread

    async def child(_uid, db=None):
        return False

    monkeypatch.setattr(manner_block, "is_adult", child)
    nc._ROWS.clear()
    out = serve()
    assert out and "shed roof" not in out and "Mia" in out

    async def down(_uid, db=None):
        raise RuntimeError("persona store unreadable")

    monkeypatch.setattr(manner_block, "is_adult", down)
    nc._ROWS.clear()
    out = serve()
    assert out and "shed roof" not in out and "Mia" in out


_REAL_LIST_MUTES, _REAL_LIST_MUTES_STRICT = restraint.list_mutes, restraint.list_mutes_strict     # before the lab fixture patches them


def test_the_strict_mute_read_raises_where_the_lenient_one_says_none(monkeypatch):
    import contextlib

    import db_compat

    monkeypatch.setattr(restraint, "list_mutes", _REAL_LIST_MUTES)
    monkeypatch.setattr(restraint, "list_mutes_strict", _REAL_LIST_MUTES_STRICT)

    @contextlib.asynccontextmanager
    async def broken():
        raise RuntimeError("db down")
        yield

    monkeypatch.setattr(db_compat, "get_compat_db", broken)
    restraint._mute_cache.pop("zed_strict", None)
    assert asyncio.run(restraint.list_mutes("zed_strict")) == []
    with pytest.raises(RuntimeError):
        asyncio.run(restraint.list_mutes_strict("zed_strict"))
    assert "zed_strict" not in restraint._mute_cache, "a failed read is never cached"

