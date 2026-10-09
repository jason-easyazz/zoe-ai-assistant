"""The recall relevance gate (``recall_gate.py``, ``ZOE_RECALL_GATE`` off | shadow | enforce) - blueprint 2.8 / register BM4.

Synthetic data only (``demo_bar_*`` ids, invented names). Groups: the pure rule (triggers, sticky, cooldown, delay, budget, stable
order, authority), the walls (guest, off-record, restraint, cross-user), language independence (es / fr / de / zh / ja), the write-time
stamp, and the two surfaces - the recall packet (``routers.memories.memory_for_prompt``) and the personalisation hop - including the
SHADOW IDENTITY proof: with the gate in ``shadow`` (the default) the packet and the hop are byte-identical to ``off`` over a seed set.

Every behaviour has a RED-WHEN-REMOVED twin: the same scenario with the rule neutered through ``Config`` (sticky 0, cooldown 0, delay 0,
an unlimited budget) must produce the OTHER result, so a green run can never mean "the check was never wired".
"""
from __future__ import annotations

import logging
import random
import re
from dataclasses import replace

import pytest

pytestmark = pytest.mark.ci_safe

import personalisation_hop as hop
import recall_gate as rg
import restraint
import routers.memories as memories
from memory_service import MemoryRef

UID = "demo_bar_00000001"
OTHER = "demo_bar_00000002"
T0 = 1_760_000_000.0
WALK = "User walks their kelpie Juniper along the river every morning at 6am."
NIGHT = "User works night shifts at the hospital pharmacy and sleeps during the day."
SISTER = "User's sister Marisol is flying in from Lisbon on Thursday."
FISH = "User is pescatarian and does not eat any meat."
DENTIST = "User's dentist is Dr Okafor and the next check-up is in March."
BOOKS = "User is reading a long novel about lighthouse keepers."
MIGRAINE = "User gets migraines most weeks."


def ref(i, text, *, cls="user_stated", ts=T0, status="approved", **meta):
    md = {"status": status, "user_id": UID, "memory_type": "fact", "added_ts": ts, "authority_class": cls}
    md.update(meta)
    return MemoryRef(id=i, text=text, metadata=md)


def cand(r, source="pool", rank=0):
    return rg._cand(r, source, rank)


class _Lines(logging.Handler):
    """Captures the gate's own log lines through a handler on its logger (caplog also sees whatever another module attached to the
    root logger, and a full-suite run showed every line twice)."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.lines = []

    def emit(self, record):
        if record.getMessage().startswith("RECALL_GATE"):
            self.lines.append(record.getMessage())

    def clear(self):
        self.lines.clear()


@pytest.fixture
def caplog():
    lg = logging.getLogger("recall_gate")
    h, old = _Lines(), lg.level
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    h.at_level = lambda *a, **k: __import__("contextlib").nullcontext()
    yield h
    lg.removeHandler(h)
    lg.setLevel(old)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for k in (rg.ENV, "ZOE_RECALL_EVIDENCE", "ZOE_EMOTIONAL_RECALL_ENABLED", "ZOE_MEMORY_COMPOSE_ENABLED",
              "ZOE_PERSON_SUGGEST_ENABLED", "ZOE_PERSONALISATION_HOP", "ZOE_RESTRAINT"):
        monkeypatch.delenv(k, raising=False)
    rg._reset_state()
    restraint._reset_state()

    async def no_mutes(_uid):
        return []

    monkeypatch.setattr(restraint, "list_mutes", no_mutes)
    monkeypatch.setattr(rg, "_off_record", lambda uid, text: False)
    yield
    rg._reset_state()
    restraint._reset_state()


def run(sess, turn, rows, message, *, cfg=rg.PACKET, source="pool", presented=(), commit=True, **kw):
    return rg.decide(sess, turn, [cand(r, source) for r in rows], message, cfg=cfg, presented=presented, commit=commit, **kw)


# ── the rule: triggers ──────────────────────────────────────────────────────────────────────

def test_a_name_the_owner_says_triggers_the_row_that_holds_it():
    rows = [ref("walk0001", WALK), ref("nigh0001", NIGHT), ref("sist0001", SISTER)]
    d = run(rg.Session(), 1, rows, "How is Juniper doing?")
    assert d.ids == ["walk0001"] and "name" in d.selected[0].reasons
    assert run(rg.Session(), 1, rows, "What is the capital of France?").ids == []      # nothing shares a key: abstain


def test_inflection_and_stop_words():
    rows = [ref("walk0001", WALK)]
    # "walking" and "walk" share a stem key; "the", "of", "and" never trigger anything
    assert run(rg.Session(), 1, rows + [ref("x0000001", "User likes the sea and the sun.")], "Is the walking route by the river still ok?").ids \
        == ["walk0001"]
    assert run(rg.Session(), 1, [ref("x0000001", "User likes the sea and the sun.")], "the and of the and of").ids == []


def test_a_clock_time_matches_across_notations():
    rows = [ref("walk0001", WALK), ref("nigh0001", NIGHT)]
    assert rg.derive("alarm at 06:00 please").clocks == rg.derive("see you at 6am").clocks == ("@06:00",)
    d = run(rg.Session(), 1, rows, "Can you wake me at 6:00 and also check 6am?")
    assert "walk0001" in d.ids and "time" in d.selected[0].reasons


def test_plan_cue_and_a_routine_row_is_one_signal_not_a_trigger():
    """S9b's structure without its topic words: "tomorrow" + a habit at a fixed time raises the row to 1.2 - below the threshold. The
    gate says so honestly (docs/knowledge/recall-gate.md: the language-independent signals alone do not recover S9b)."""
    d = run(rg.Session(), 1, [ref("walk0001", WALK)], "What should I wear tomorrow? It's meant to be really cold.", commit=False)
    assert d.ids == []
    f = rg.derive(WALK)
    assert f.routine and rg.derive("What should I wear tomorrow?").plan


# ── the rule: sticky / cooldown / delay ─────────────────────────────────────────────────────

def test_sticky_holds_a_triggered_row_for_n_further_turns():
    rows = [ref("walk0001", WALK), ref("sist0001", SISTER)]
    s = rg.Session()
    assert run(s, 1, rows, "Tell me about Juniper").ids == ["walk0001"]
    for t in (2, 3):                                              # sticky=2: turns 2 and 3 carry it with no trigger
        d = run(s, t, rows, "ok thanks")
        assert d.ids == ["walk0001"] and d.selected[0].held and d.selected[0].reasons == ("sticky",)
    assert run(s, 4, rows, "ok thanks").ids == []                 # the window closed


def test_sticky_twin_neutered_row_is_not_held():
    rows = [ref("walk0001", WALK)]
    cfg = replace(rg.PACKET, sticky=0)
    s = rg.Session()
    assert run(s, 1, rows, "Tell me about Juniper", cfg=cfg).ids == ["walk0001"]
    assert run(s, 2, rows, "ok thanks", cfg=cfg).ids == []


FILLER_CFG = replace(rg.PACKET, delay=0, filler_max=3, cooldown=2, sticky=0)


def test_cooldown_blocks_a_served_row_that_is_not_retriggered():
    rows = [ref("bks00001", BOOKS)]
    s = rg.Session()
    got = [run(s, t, rows, "ok", cfg=FILLER_CFG, source="fact").ids for t in range(1, 6)]
    assert got == [["bks00001"], [], [], ["bks00001"], []]        # served, 2 turns cooling, served, cooling


def test_cooldown_does_not_apply_to_a_row_the_owners_words_trigger_again():
    rows = [ref("walk0001", WALK)]
    s = rg.Session()
    cfg = replace(FILLER_CFG, sticky=0)
    assert run(s, 1, rows, "how is Juniper", cfg=cfg, source="fact").ids == ["walk0001"]
    assert run(s, 2, rows, "and Juniper again", cfg=cfg, source="fact").ids == ["walk0001"]     # re-triggered inside the cooldown


def test_cooldown_twin_neutered_row_is_reserved_at_once():
    rows = [ref("bks00001", BOOKS)]
    s = rg.Session()
    cfg = replace(FILLER_CFG, cooldown=0)
    assert [run(s, t, rows, "ok", cfg=cfg, source="fact").ids for t in (1, 2, 3)] == [["bks00001"]] * 3


def test_delay_keeps_filler_out_of_the_first_turns_but_not_a_triggered_row():
    rows = [ref("bks00001", BOOKS), ref("walk0001", WALK)]
    cfg = replace(rg.PACKET, delay=2)
    s = rg.Session()
    assert run(s, 1, rows, "tell me about Juniper", cfg=cfg, source="fact").ids == ["walk0001"]     # triggered: no delay
    s2 = rg.Session()
    assert run(s2, 1, rows, "hello", cfg=cfg, source="fact").ids == []
    assert run(s2, 2, rows, "hello", cfg=cfg, source="fact").ids == []
    assert set(run(s2, 3, rows, "hello", cfg=cfg, source="fact").ids) == {"bks00001", "walk0001"}


def test_delay_twin_neutered_filler_is_served_on_turn_one():
    rows = [ref("bks00001", BOOKS)]
    assert run(rg.Session(), 1, rows, "hello", cfg=replace(rg.PACKET, delay=0), source="fact").ids == ["bks00001"]


def test_filler_is_capped_and_never_beats_a_triggered_row():
    rows = [ref(f"fil{i:05d}", f"User has hobby number {i} that is quite different.") for i in range(6)] + [ref("walk0001", WALK)]
    d = run(rg.Session(), 5, rows, "how is Juniper", cfg=replace(rg.PACKET, delay=0), source="fact")
    assert d.ids[0] == "walk0001" or "walk0001" in d.ids
    assert sum("filler" in p.reasons for p in d.selected) == rg.PACKET.filler_max


# ── the rule: budget and a stable order ─────────────────────────────────────────────────────

def _six_triggered():
    return [ref(f"row{i:05d}", f"User met Zebulon{chr(97 + i)} at the {'x' * 150} market.") for i in range(6)]


def test_budget_caps_the_selection_and_keeps_the_best_scoring_rows():
    rows = _six_triggered()
    msg = "Tell me about Zebulona Zebulonb Zebulonc Zebulond Zebulone Zebulonf"
    big = run(rg.Session(), 1, rows, msg, cfg=replace(rg.PACKET, budget=10_000))
    small = run(rg.Session(), 1, rows, msg, cfg=replace(rg.PACKET, budget=2 * rows[0].text.__len__() // 4 + 20))
    assert len(big.selected) == 6
    assert 0 < len(small.selected) < 6 and small.budget_used <= small.budget


def test_the_order_is_stable_and_independent_of_input_order():
    rows = [ref("walk0001", WALK), ref("sist0001", SISTER), ref("dent0001", DENTIST)]
    msg = "Juniper, Marisol and Okafor"
    base = run(rg.Session(), 1, rows, msg).ids
    assert sorted(base) == ["dent0001", "sist0001", "walk0001"]
    for seed in range(5):
        shuffled = rows[:]
        random.Random(seed).shuffle(shuffled)
        assert run(rg.Session(), 1, shuffled, msg).ids == base


def test_a_new_row_appends_and_the_prefix_does_not_move():
    rows = [ref("walk0001", WALK), ref("sist0001", SISTER), ref("dent0001", DENTIST)]
    s = rg.Session()
    first = run(s, 1, rows, "Juniper and Marisol").ids
    second = run(s, 2, rows, "Okafor and Juniper and Marisol").ids          # one more is triggered; the others stay put
    assert second[: len(first)] == first and second[-1] == "dent0001" and len(second) == 3
    assert run(s, 3, rows, "Okafor and Juniper and Marisol").ids == second     # nothing changed: byte-stable


def test_would_add_and_would_drop_are_relative_to_what_the_floor_presented():
    rows = [ref("walk0001", WALK), ref("bks00001", BOOKS)]
    d = run(rg.Session(), 1, rows, "how is Juniper", presented=["bks00001"])
    assert d.added == ("walk0001",) and d.dropped == ("bks00001",)


# ── the similarity floor ────────────────────────────────────────────────────────────────────

def _hit(i, text, dist, rank, best=None):
    best = rank == 1 if best is None else best
    r = ref(i, text)
    c = cand(MemoryRef(id=r.id, text=r.text, metadata=r.metadata, score=dist), "hit", rank)
    c.best = best
    return c


def test_the_floors_search_returns_its_k_nearest_whatever_they_are_the_gate_keeps_the_near_ones():
    far = _hit("far00001", BOOKS, 1.04, 1)             # the only (and best) hit, but nothing like the question
    near = _hit("near0001", WALK, 0.52, 1)
    best = _hit("best0001", SISTER, 0.76, 1)           # rank 1 within SIM_BEST: the ask's best answer is kept
    second = _hit("sec00001", FISH, 0.76, 2, best=False)   # the same distance, but not the nearest of its ask
    nearest3 = _hit("nea00001", DENTIST, 0.77, 3, best=True)   # the floor's order blends hotness: the nearest hit need not be rank 1
    mid = _hit("mid00001", NIGHT, 0.85, 3)             # a supporting signal only
    d = lambda c, msg="hello": rg.decide(rg.Session(), 1, [c], msg, commit=False).ids       # noqa: E731
    assert d(far) == [] and d(near) == ["near0001"] and d(best) == ["best0001"] and d(second) == [] and d(mid) == [] \
        and d(nearest3) == ["nea00001"]
    # a mid-distance hit plus one rare key the owner said is enough: two weak signals
    assert d(mid, "Do I sleep during the day or the night?") == ["mid00001"]


# ── authority ───────────────────────────────────────────────────────────────────────────────

def test_authority_orders_ties_verbatim_then_derived_then_inferred():
    a = ref("aaa00001", "User likes Juniper.", cls="model_from_transcript")
    b = ref("bbb00001", "User adores Juniper.", cls="user_stated_derived")
    c = ref("ccc00001", "User loves Juniper.", cls="user_stated")
    rows = [a, b, c]
    s = rg.Session()
    d = rg.decide(s, 1, [cand(a, "hit", 1), cand(b, "hit", 1), cand(c, "hit", 1)], "Juniper", presented=())
    assert d.ids == ["ccc00001", "bbb00001", "aaa00001"]
    assert [p.auth for p in d.selected] == [4, 3, 0]
    del rows


def test_an_inferred_row_is_not_triggered_by_key_overlap_alone_but_is_by_similarity_or_a_name():
    inferred = ref("inf00001", "User seems to enjoy long swims at the harbour baths.", cls="model_from_transcript")
    msg = "Any harbour swims planned? Long baths or short?"
    assert run(rg.Session(), 1, [inferred], msg).ids == []                                   # keys only: refused
    assert rg.decide(rg.Session(), 1, [cand(inferred, "hit", 1)], msg).ids == ["inf00001"]   # the floor's search found it
    named = ref("inf00002", "User seems to know Wilhelmina from the baths.", cls="model_from_transcript")
    assert run(rg.Session(), 1, [named], "Say hi to Wilhelmina").ids == ["inf00002"]          # a name the owner said


def test_an_inferred_row_is_never_held_by_sticky():
    inferred = ref("inf00001", "User seems to know Wilhelmina from the baths.", cls="model_from_transcript")
    s = rg.Session()
    assert run(s, 1, [inferred], "Say hi to Wilhelmina").ids == ["inf00001"]
    assert run(s, 2, [inferred], "thanks").ids == []


def test_authority_twin_a_verbatim_row_of_the_same_text_is_held():
    verb = ref("ver00001", "User seems to know Wilhelmina from the baths.", cls="user_stated")
    s = rg.Session()
    run(s, 1, [verb], "Say hi to Wilhelmina")
    assert run(s, 2, [verb], "thanks").ids == ["ver00001"]


# ── the walls ───────────────────────────────────────────────────────────────────────────────

class Svc:
    """What ``memory_for_prompt`` and the gate read: the ranked rows, a search, the recency read, the durable pool."""

    def __init__(self, rows=(), hits=(), pool=None):
        self.rows, self.hits, self.pool = list(rows), list(hits), list(rows) if pool is None else list(pool)
        self.pool_calls = 0

    async def load_for_prompt(self, user_id, *, limit):
        return self.rows[:limit]

    async def load_recent_for_prompt(self, user_id, *, window_s, limit, emotional_first=False):
        return []

    async def search(self, query, *, user_id, limit=6, **_):
        return list(self.hits)[:limit]

    async def load_durable_for_hop(self, user_id):
        self.pool_calls += 1
        return list(self.pool)


async def gate(svc, message, *, facts=(), hits=(), presented=(), user=UID, now=T0):
    return await rg.evaluate_packet(svc, user, message, facts=list(facts), hits=list(hits), recent=None,
                                    presented=list(presented), mood=False, now=now)


async def test_a_guest_gets_no_gate_and_no_pool_read():
    svc = Svc([ref("walk0001", WALK)])
    for guest in ("guest", "anonymous", "voice-guest", ""):
        d = await gate(svc, "how is Juniper", user=guest)
        assert d.skipped == "guest" and not d.selected
    assert svc.pool_calls == 0


async def test_an_off_record_turn_abstains_and_arms_nothing(monkeypatch):
    svc = Svc([ref("walk0001", WALK)])
    monkeypatch.setattr(rg, "_off_record", lambda uid, text: "secret" in text)
    d = await gate(svc, "off the record, secret: Juniper", now=T0)
    assert d.skipped == "off_record" and not d.selected
    assert svc.pool_calls == 0 and not any(s.sel_by_turn or s.rows for s in rg._SESSIONS.values())
    after = await gate(svc, "ok thanks", now=T0 + 10)
    assert after.ids == []                                         # nothing was armed by the off-record turn


async def test_off_record_twin_an_ordinary_turn_does_arm_the_row(monkeypatch):
    svc = Svc([ref("walk0001", WALK)])
    assert (await gate(svc, "tell me about Juniper", now=T0)).ids == ["walk0001"]
    assert (await gate(svc, "ok thanks", now=T0 + 10)).ids == ["walk0001"]


INSULIN = "User has a dentist appointment at 6pm on Friday for a cracked molar."


async def test_the_gate_never_adds_a_sensitive_pool_row_restraint_would_withhold():
    ins = ref("ins00001", INSULIN)
    assert "health" in restraint.classify(ins.text)
    # the owner's words share a CLOCK TIME with the row (a gate trigger) but name no health topic: restraint withholds it, so the gate adds nothing
    d = await gate(Svc([ref("walk0001", WALK)], pool=[ins]), "Set an alarm for 6pm please")
    assert "ins00001" not in d.ids
    # the owner's words name the class: that is a pull, the row may enter
    d2 = await gate(Svc([], pool=[ins]), "Remind me about my dentist at 6pm", now=T0 + 400)
    assert "ins00001" in d2.ids


async def test_restraint_twin_the_same_row_in_a_non_sensitive_class_is_added():
    plain = ref("wak00001", "User has a book club meeting at 6pm on Friday at the library.")
    assert restraint.classify(plain.text) == ()
    d = await gate(Svc([], pool=[plain]), "Set an alarm for 6pm please")
    assert d.ids == ["wak00001"]


async def test_a_muted_topic_stays_muted(monkeypatch):
    ins = ref("ins00001", INSULIN)

    async def mutes(_uid):
        return [restraint.Mute("m1", restraint.stems("dentist appointment"))]

    monkeypatch.setattr(restraint, "list_mutes", mutes)
    d = await gate(Svc([], pool=[ins]), "Set an alarm for 6pm please")
    assert d.ids == []


async def test_an_unreadable_restraint_state_withholds_every_pool_row(monkeypatch):
    async def down(_uid):
        raise RuntimeError("db down")

    monkeypatch.setattr(restraint, "list_mutes", down)
    plain = ref("wak00001", "User has a book club meeting at 6pm on Friday at the library.")
    assert (await gate(Svc([], pool=[plain]), "Set an alarm for 6pm please")).ids == []


async def test_state_never_crosses_users():
    svc = Svc([ref("walk0001", WALK)])
    assert (await gate(svc, "tell me about Juniper", user=UID, now=T0)).ids == ["walk0001"]
    assert (await gate(svc, "ok thanks", user=OTHER, now=T0 + 5)).ids == []          # OTHER has no sticky
    assert (await gate(svc, "ok thanks", user=UID, now=T0 + 10)).ids == ["walk0001"]


async def test_the_tool_query_in_the_same_turn_is_judged_with_the_owners_words():
    svc = Svc([ref("walk0001", WALK), ref("sist0001", SISTER)])
    rg.note_turn(UID, "sess-1", "tell me about Marisol", now=T0)
    d = await gate(svc, "what did she say about Lisbon", now=T0 + 1)     # a different query inside the SAME turn
    assert "sist0001" in d.ids
    s = rg._SESSIONS[(UID, "sess-1")]
    assert s.turn == 1


def test_the_turn_counter_advances_once_per_distinct_message():
    rg.note_turn(UID, "s", "hello there", now=T0)
    rg.note_turn(UID, "s", "hello there", now=T0 + 5)         # the same turn noted twice
    rg.note_turn(UID, "s", "and another", now=T0 + 10)
    assert rg._SESSIONS[(UID, "s")].turn == 2


# ── language independence ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,message,other", [
    ("User camina a su perra Juniper cada mañana a las 6:30.", "¿Cómo está Juniper hoy?", "¿Qué tiempo hará hoy?"),
    ("L'utilisateur promène sa chienne Juniper chaque matin à 6h.", "Comment va Juniper ?", "Quel temps fait-il ?"),
    ("Der Nutzer geht jeden Morgen um 6 Uhr mit seiner Hündin Juniper spazieren.", "Wie geht es Juniper?", "Wie wird das Wetter?"),
    ("用户每天早上6点在河边遛他的狗Juniper。", "Juniper最近怎么样？", "今天天气怎么样？"),
    ("ユーザーは毎朝6時に犬のジュニパーを川沿いで散歩させる。", "ジュニパーは元気？", "今日の天気は？"),
])
def test_a_named_entity_triggers_in_every_language_and_an_unrelated_question_does_not(text, message, other):
    rows = [ref("walk0001", text), ref("nigh0001", NIGHT)]
    assert run(rg.Session(), 1, rows, message).ids == ["walk0001"]
    assert run(rg.Session(), 1, rows, other).ids == []


def test_routine_and_clock_are_recognised_from_each_languages_own_data():
    for text in ("User camina cada mañana a las 6:30.", "L'utilisateur sort chaque matin à 6h30.", "Jeden Morgen um 6:30 Uhr geht der Nutzer mit dem Hund.",
                 "每天早上6点半去散步。", "毎朝6時半に散歩する。", "User walks every morning at 6:30am."):
        f = rg.derive(text)
        assert f.routine, text
        assert f.clocks and f.clocks[0].startswith("@06:"), (text, f.clocks)


def test_a_language_with_no_data_fails_safe_to_key_overlap_only(monkeypatch):
    f = rg.derive("Kullanıcı her sabah köpeği Juniper ile yürür.", lang="xx")           # no lexicon: no cue words, no stop list
    assert not f.routine and not f.plan and "juni" in f.keys and f.names == ()
    row = ref("t0000001", "Kullanıcı her sabah köpeği Juniper ile yürür.")
    monkeypatch.setattr(rg, "_detect", lambda text: ("xx", 3))
    assert run(rg.Session(), 1, [row], "Juniper nasıl?").ids == []        # one rare key (1.5): below the threshold - fewer additions, never more
    assert run(rg.Session(), 1, [row], "Juniper köpeği nasıl, her sabah yürür mü?").ids == ["t0000001"]   # enough shared keys still trigger


def test_german_nouns_are_not_names_but_a_rare_noun_still_matches_by_key():
    f = rg.derive("Der Nutzer besucht jeden Sonntag den Leuchtturm Warnemünde.")
    assert f.names == ()
    d = run(rg.Session(), 1, [ref("de000001", "Der Nutzer besucht jeden Sonntag den Leuchtturm Warnemünde.")],
            "Wie hoch ist der Leuchtturm Warnemünde?")
    assert d.ids == ["de000001"]


# ── the write-time stamp ────────────────────────────────────────────────────────────────────

def test_stamp_is_read_back_and_invalidated_by_a_text_change(monkeypatch):
    md = {}
    rg.stamp(md, WALK)
    assert md["gate_v"] == rg.VERSION and md["gate_t"]
    monkeypatch.setattr(rg, "derive", lambda *a, **k: (_ for _ in ()).throw(AssertionError("derived instead of read")))
    assert rg.row_feat(md, WALK).names == ("juni",) and rg.row_feat(md, WALK).routine
    monkeypatch.undo()
    changed = rg.row_feat(md, "User walks the cat Biscuit.")       # the text changed: the stamp is ignored, never trusted
    assert "bisc" in changed.keys and "juni" not in changed.keys


def test_stamp_is_a_noop_when_off(monkeypatch):
    monkeypatch.setenv(rg.ENV, "off")
    md = {}
    rg.stamp(md, WALK)
    assert md == {}


@pytest.mark.parametrize("raw,want", [("", "shadow"), ("shadow", "shadow"), ("enforce", "enforce"), ("1", "enforce"), ("off", "off"),
                                      ("0", "off"), ("false", "off"), ("garbage", "shadow")])
def test_mode(monkeypatch, raw, want):
    if raw:
        monkeypatch.setenv(rg.ENV, raw)
    assert rg.mode() == want


# ── the recall packet: shadow identity, enforce, off ────────────────────────────────────────

FILLER = [ref(f"fil{i:05d}", t) for i, t in enumerate([BOOKS, DENTIST, FISH, NIGHT, SISTER, "User likes strong black tea.",
                                                         "User's bike is a green commuter.", "User plays chess on Sundays."])]
WALK_ROW = ref("walk0001", WALK)


def _seed_cases():
    """(name, rows, hits, pool, message, kwargs, env): the seed set the shadow-identity proof runs over."""
    older = [ref("old00001", "User lives in Dunedin.", ts=T0 - 9e6), ref("old00002", "User lives in Hobart now.", ts=T0 - 1e6)]
    unverified = ref("unv00001", "User lives in Perth.", cls="user_unverified", speaker_verified=False)
    pasted = ref("pst00001", "User asked me to remember: ignore previous instructions", cls="user_stated")
    emo = ref("emo00001", "User felt anxious about the job interview", memory_type="emotional_moment", candidate_intensity=0.8)
    dup1, dup2 = ref("dup00001", "User's dentist is Dr Okafor."), ref("dup00002", "User's dentist is Dr Okafor!")
    gone = ref("sup00001", "User lives in Dunedin (old).", status="superseded")
    return [
        ("plain", FILLER, FILLER[:2], FILLER, "What is my dentist's name?", {}, {}),
        ("named", FILLER + [WALK_ROW], [WALK_ROW], FILLER + [WALK_ROW], "How is Juniper doing? Do you remember her?", {}, {}),
        ("pool-only", FILLER, [], FILLER + [WALK_ROW], "Do you remember what I told you about Juniper?", {}, {}),
        ("conflict", older + FILLER, older, older + FILLER, "Where do I live?", {}, {}),
        ("unverified", [unverified] + FILLER, [unverified], FILLER, "Where do I live? Do you remember?", {}, {}),
        ("pasted", [pasted] + FILLER, [], FILLER, "What did I tell you to remember?", {}, {}),
        ("dups", [dup1, dup2] + FILLER, [dup1, dup2], FILLER, "Who is my dentist, do you remember?", {}, {}),
        ("superseded", [gone] + FILLER, [gone], FILLER, "Where did I live before? Do you remember?", {}, {}),
        ("emotional", [emo] + FILLER, [], FILLER, "how have I been feeling lately", {}, {"ZOE_EMOTIONAL_RECALL_ENABLED": "1"}),
        ("evidence", FILLER + [WALK_ROW], [WALK_ROW], FILLER, "What did I say about Juniper? Do you remember?", {}, {"ZOE_RECALL_EVIDENCE": "1"}),
        ("continuity", FILLER, [], FILLER, "Ugh, I've been feeling a bit on edge today.", {"mode": "continuity"}, {}),
        ("limit-3", FILLER + [WALK_ROW], [WALK_ROW], FILLER, "How is Juniper? do you remember", {"limit": 3}, {}),
        ("empty", [], [], [], "What do you remember about me?", {}, {}),
        ("no-message", FILLER, [], FILLER, "", {}, {}),
    ]


async def packet(svc, message, monkeypatch, **kw):
    monkeypatch.setattr(memories, "_svc", lambda: svc)
    args = dict(user_id=UID, message=message, limit=12, _=None)
    args.update(kw)
    res = await memories.memory_for_prompt(**args)
    await rg.drain()
    return res


@pytest.mark.parametrize("case", _seed_cases(), ids=lambda c: c[0])
async def test_shadow_packet_is_byte_identical_to_off(case, monkeypatch, caplog):
    name, rows, hits, pool, message, kw, env = case
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv(rg.ENV, "off")
    off = await packet(Svc(rows, hits, pool), message, monkeypatch, **kw)
    rg._reset_state()
    monkeypatch.setenv(rg.ENV, "shadow")
    with caplog.at_level(logging.INFO, logger="recall_gate"):
        shadow = await packet(Svc(rows, hits, pool), message, monkeypatch, **kw)
    lines = list(caplog.lines)
    monkeypatch.delenv(rg.ENV)                                    # the default IS shadow
    rg._reset_state()
    default = await packet(Svc(rows, hits, pool), message, monkeypatch, **kw)
    assert shadow == off == default                               # the whole result, packet bytes and refs included
    assert repr(shadow["packet"]).encode() == repr(off["packet"]).encode()
    if name in ("continuity", "no-message"):
        assert lines == []                                        # the gate abstains on these surfaces entirely
    else:
        assert len(lines) == 1 and re.fullmatch(
            r"RECALL_GATE user=\S+ served=\d+ would_add=\S+ would_drop=\S+ budget=\d+ surface=packet turn=\d+ mode=shadow .*", lines[0])


async def test_shadow_packet_survives_a_gate_that_raises(monkeypatch):
    rows = FILLER + [WALK_ROW]
    monkeypatch.setenv(rg.ENV, "off")
    off = await packet(Svc(rows, [WALK_ROW], rows), "How is Juniper? do you remember", monkeypatch)

    async def boom(*a, **k):
        raise RuntimeError("gate down")

    monkeypatch.setattr(rg, "evaluate_packet", boom)
    for m in ("shadow", "enforce"):
        monkeypatch.setenv(rg.ENV, m)
        assert await packet(Svc(rows, [WALK_ROW], rows), "How is Juniper? do you remember", monkeypatch) == off


async def test_shadow_logs_what_enforce_would_add_and_drop(monkeypatch, caplog):
    """The S9b class: the owner's row is in the durable pool, the floor's ranked 12 and one search did not carry it."""
    crowd = [ref(f"cr{i:06d}", f"User keeps hobby log entry {i} about topic{i}.") for i in range(14)]
    svc = Svc(crowd, hits=[], pool=crowd + [WALK_ROW])
    with caplog.at_level(logging.INFO, logger="recall_gate"):
        res = await packet(svc, "Do you remember how Juniper is doing on the river?", monkeypatch)
    assert "Juniper" not in res["packet"]                                    # the floor missed it
    line = caplog.lines[0]
    assert "would_add=walk0001" in line and "would_drop=" in line and "would_drop=-" not in line


async def test_enforce_replaces_the_floors_selection(monkeypatch, caplog):
    crowd = [ref(f"cr{i:06d}", f"User keeps hobby log entry {i} about topic{i}.") for i in range(14)]
    svc = Svc(crowd, hits=[], pool=crowd + [WALK_ROW])
    monkeypatch.setenv(rg.ENV, "enforce")
    with caplog.at_level(logging.INFO, logger="recall_gate"):
        res = await packet(svc, "Do you remember how Juniper is doing on the river?", monkeypatch)
    assert res["packet"].startswith("## What I know about you")
    assert "Juniper" in res["packet"] and res["refs"][0]["id"] == "walk0001"
    assert res["count"] == len(res["refs"]) <= 12 and "hobby log entry" not in res["packet"]     # the irrelevant filler is gone
    assert any("mode=enforce" in ln for ln in caplog.lines)


async def test_enforce_abstains_for_a_guest_and_a_continuity_turn(monkeypatch):
    rows = FILLER + [WALK_ROW]
    monkeypatch.setenv(rg.ENV, "off")
    off = await packet(Svc(rows, [], rows), "Ugh, I've been feeling a bit on edge today.", monkeypatch, mode="continuity")
    monkeypatch.setenv(rg.ENV, "enforce")
    assert await packet(Svc(rows, [], rows), "Ugh, I've been feeling a bit on edge today.", monkeypatch, mode="continuity") == off
    res = await memories.memory_for_prompt(user_id="guest", message="how is Juniper", limit=12, _=None)
    assert res["packet"] == "" and res["count"] == 0


async def test_enforce_abstains_when_nothing_is_relevant_unless_told_to_keep_the_floor(monkeypatch):
    rows = FILLER
    monkeypatch.setenv(rg.ENV, "off")
    off = await packet(Svc(rows, [], rows), "What do you remember about my travel plans?", monkeypatch)
    assert off["count"] > 0
    rg._reset_state()
    monkeypatch.setenv(rg.ENV, "enforce")
    on = await packet(Svc(rows, [], rows), "What do you remember about my travel plans?", monkeypatch)
    assert on["packet"] == "" and on["count"] == 0                   # turn 1: nothing triggered, filler is delayed: an empty packet
    monkeypatch.setattr(rg, "PACKET", replace(rg.PACKET, abstain=False))
    rg._reset_state()
    assert await packet(Svc(rows, [], rows), "What do you remember about my travel plans?", monkeypatch) == off      # the twin: floor stands


async def test_off_mode_runs_nothing(monkeypatch, caplog):
    monkeypatch.setenv(rg.ENV, "off")
    svc = Svc(FILLER, [], FILLER)
    with caplog.at_level(logging.INFO, logger="recall_gate"):
        await packet(svc, "What is my dentist's name?", monkeypatch)
    assert svc.pool_calls == 0 and not rg._SESSIONS and not caplog.lines


# ── the hop surface ─────────────────────────────────────────────────────────────────────────

class HopSvc:
    def __init__(self, rows):
        self.rows = rows

    async def load_durable_for_hop(self, user_id):
        return list(self.rows)


COLD = "What should I wear tomorrow? It's meant to be really cold."
HOP_ROWS = [ref("walk0001", WALK), ref("nigh0001", NIGHT), ref("fish0001", FISH), ref("sist0001", SISTER)]


async def build_hop(message, svc=None):
    h = await hop.build(UID, message, svc=svc or HopSvc(HOP_ROWS))
    await rg.drain()
    return h


async def test_shadow_hop_is_identical_to_off_and_logs(monkeypatch, caplog):
    for msg in (COLD, "Any tips for sleeping better?", "What should I cook tonight?", "What is the capital of France?"):
        monkeypatch.setenv(rg.ENV, "off")
        rg._reset_state()
        off = await build_hop(msg)
        monkeypatch.setenv(rg.ENV, "shadow")
        rg._reset_state()
        caplog.clear()
        with caplog.at_level(logging.INFO, logger="recall_gate"):
            shadow = await build_hop(msg)
        assert shadow == off and shadow.section() == off.section() and shadow.suffix_line() == off.suffix_line()
        lines = list(caplog.lines)
        assert len(lines) == (1 if off.facts else 0)                     # a hop turn is logged; a non-advice turn never reaches the hop
        assert all("surface=hop" in ln and "mode=shadow" in ln for ln in lines)


async def test_the_gate_agrees_with_the_hop_on_the_s9b_row_through_the_topic_signal():
    h = await hop.build(UID, COLD, svc=HopSvc(HOP_ROWS))
    assert [f.id for f in h.facts] == ["walk0001"]
    rg._reset_state()
    d = await rg.evaluate_hop(UID, COLD, HOP_ROWS, ["walk0001"], {"walk0001": "clothing"}, now=T0)
    assert d.ids == ["walk0001"] and d.added == () and d.dropped == () and "topic" in d.selected[0].reasons


async def test_enforce_hop_adds_a_row_the_word_list_missed(monkeypatch):
    """The owner stated a fixed early routine in words the hop's list does not know ("rowing at dawn"); they ask for clothing advice and
    name the thing: the hop's regex misses it, the gate's own trigger (a name the owner said) finds it."""
    row = ref("row00001", "User goes sculling on Lake Pedder with Ingrid at 5am, no matter the weather.")
    rows = HOP_ROWS + [row]
    msg = "What should I wear tomorrow for Ingrid's sculling session? It's meant to be really cold."
    monkeypatch.setenv(rg.ENV, "off")
    off = await build_hop(msg, HopSvc(rows))
    monkeypatch.setenv(rg.ENV, "enforce")
    rg._reset_state()
    on = await build_hop(msg, HopSvc(rows))
    assert "row00001" in [f.id for f in on.facts]
    assert on.section().startswith(hop.HEADING) and len(on.facts) <= hop.MAX_FACTS
    assert off.facts != on.facts


async def test_enforce_hop_fails_open_to_the_words_match(monkeypatch):
    async def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setenv(rg.ENV, "off")
    off = await build_hop(COLD)
    monkeypatch.setenv(rg.ENV, "enforce")
    monkeypatch.setattr(rg, "evaluate_hop", boom)
    assert await build_hop(COLD) == off


async def test_a_row_written_through_the_service_carries_its_triggers(monkeypatch):
    """The write-time stamp is wired into ``MemoryService._build_metadata`` (beside restraint's class)."""
    import memory_service
    from test_memory_authority import _Col

    s = memory_service.MemoryService(data_dir="/nonexistent/zoe-test-gate")
    col = _Col()
    s._collection = lambda: col

    async def no_audit(**_kw):
        return None

    async def opted_in(_uid):
        return False

    s._append_audit = no_audit
    monkeypatch.setattr(memory_service, "_user_opted_out", opted_in)
    row = await s.ingest(WALK, user_id=UID, source="voice_fact", status="approved")
    md = row.metadata
    assert md["gate_v"] == rg.VERSION and md["gate_h"] == rg._text_hash(row.text) and "juni" in md["gate_t"]
    monkeypatch.setenv(rg.ENV, "off")
    off_row = await s.ingest(SISTER, user_id=UID, source="voice_fact", status="approved")
    assert "gate_t" not in off_row.metadata
