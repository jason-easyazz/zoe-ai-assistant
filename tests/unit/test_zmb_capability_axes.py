"""Zoe Memory Bench: the CAPABILITY axes (j exact words, k reflection, l long-range associative recall, m memory protocol).

What Hindsight and MemPalace are built for, which the first nine axes (storage hygiene) never measured. Everything here is slim-lane safe: no service module,
no server, no model. The arms are the repo's own test doubles (``InMemoryVerbatimStore``, ``FakeDistilledTier``, ``FakeHindsight``) and a scripted arm whose
every behaviour is switchable, so each probe is proven RED-BEFORE-GREEN: the same cell goes green on an arm that has the feature and red on one that does not
(the lab's own controls over the real ``MemoryService`` are in ``services/zoe-data/tests/test_zmb_lab.py``).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts" / "perf"))

from zmb import artifact, cells as cellmod, life, runner, scorers, scorers_cap as cap, spec, world  # noqa: E402
from zmb.arms.base import Arm, IngestReport, Turn  # noqa: E402
from zmb.arms.fake_hindsight import FakeHindsight  # noqa: E402
from zmb.arms.hindsight import HindsightArm  # noqa: E402
from zmb.arms.hm import FakeDistilledTier, HMArm  # noqa: E402
from zmb.arms.hm_policy import Controls  # noqa: E402
from zmb.arms.mempalace_verbatim import InMemoryVerbatimStore, MemPalaceVerbatimArm  # noqa: E402

SEED = "zmb-v1"
CELLS = {c.id: c for c in spec.load_cells()}
W = world.make_world(SEED)


def run(cid, arm):
    return cellmod.run_cell(CELLS[cid].rendered(W), W, arm)


# ── the corpora ──────────────────────────────────────────────────────────────

def test_the_exact_corpus_is_twenty_unique_needles_in_three_styles_and_a_held_out_seed_differs():
    a, b = life.exact_corpus(SEED), life.exact_corpus("fresh-0001")
    assert len(a) == 20 and {x.style for x in a} == {"said", "readback", "choice"} and sorted(x.style for x in a).count("choice") == 6
    assert len({x.sentence for x in a}) == 20 and len({x.day_offset for x in a}) == 20 and all(1 <= x.day_offset <= 27 for x in a)
    assert [x.sentence for x in a] != [x.sentence for x in b] and life.exact_corpus(SEED) == a                  # seeded: reproducible, and held out differs
    assert all(x.decoy and x.decoy != x.sentence for x in a if x.style == "choice") and not any(x.decoy for x in a if x.style != "choice")
    turns = life.exact_turns(SEED)
    assert len(turns) == 26 and [t["day_offset"] for t in turns] == sorted((t["day_offset"] for t in turns), reverse=True)     # 20 sentences + 6 decoys, oldest first


def test_the_hop_corpus_needs_two_facts_said_weeks_apart():
    items = life.hop_corpus(SEED)
    assert len(items) == 20 and sum(1 for i in items if i.kind == "date") == 10 and sum(1 for i in items if i.kind == "join") == 10
    assert all(i.day_a >= 14 > 9 >= i.day_b for i in items)                                                      # the first fact is older than a fortnight, the second newer than ten days
    joins = [i for i in items if i.kind == "join"]
    assert all(i.a_need[1] == i.b_need[1] and i.b_need[1] not in i.question for i in joins)                     # the second fact is reachable only THROUGH the place the first names
    assert len({i.a_need for i in items}) == 20 and len({i.b_need for i in items}) == 20


def test_the_life_has_four_threads_two_changes_and_a_decoy_for_every_kind_of_mistake():
    lf = life.life(SEED)
    assert [t["day"] for t in lf.turns] == sorted(t["day"] for t in lf.turns) and max(t["day"] for t in lf.turns) <= 30
    assert {t.id for t in lf.threads} == {"job", "move", "knee", "concert"} and len(lf.stale) == 2
    assert {t["speaker"] for t in lf.turns} == {"typed", "taught"}
    for kind in ("true", "fabricated", "stale", "hedged", "presented_as_said"):
        assert getattr(lf, f"proposals_{kind}"), kind
    gold = life.gold_for_scoring(lf)
    assert gold["threads"] and gold["pairs"] and gold["foreign"] and not set(gold["foreign"]) & set(gold["entities"])


def test_the_protocol_corpus_and_the_three_policies_differ_where_the_protocols_differ():
    sentences, prompts = life.protocol_corpus(SEED)
    kinds = [p.kind for p in prompts]
    assert (kinds.count("needed"), kinds.count("unneeded"), kinds.count("silent")) == (14, 10, 8) and len(sentences) == 14
    rate = {n: {k: sum(1 for p in prompts if p.kind == k and pol(p.text)) for k in ("needed", "unneeded", "silent")} for n, (pol, _src) in life.POLICIES.items()}
    assert rate["zoe"] == {"needed": 14, "unneeded": 0, "silent": 8}                  # recalls on everything personal, never on a command or the world's weather
    assert rate["mempalace5"]["needed"] == 10 and rate["mempalace5"]["unneeded"] == 2    # misses "what is my favourite tea" (no name, no past event), fires on "Lisbon"
    assert rate["hindsight"] == {"needed": 14, "unneeded": 10, "silent": 8}           # recall on every turn
    assert all(src for _pol, src in life.POLICIES.values())


def test_no_corpus_string_reaches_a_spec_title_or_an_artifact():
    pool = world.pool_strings()
    assert set(life.POOL_STRINGS) <= set(pool)
    for c in CELLS.values():
        assert not scorers.found_needles(" ".join((c.id, c.title, c.note, c.skip_reason, c.lme_map)), life.POOL_STRINGS), c.id
    out = run("J1.exact_sentence_after_100_filler", HMArm(distilled=FakeDistilledTier(), verbatim=MemPalaceVerbatimArm(store=InMemoryVerbatimStore())))
    assert artifact.household_strings_in(out.evidence, pool) == [] and artifact.household_strings_in(out.evidence, [x.sentence for x in life.exact_corpus(SEED)]) == []


# ── the scorers: red before green ────────────────────────────────────────────

def test_exact_words_are_scored_by_exact_substring_of_the_normalised_sentence():
    s = "I told Dr Okafor I can only manage Tuesday afternoons for the dentist and not Thursday."
    assert cap.contains_span("You said: i TOLD Dr  Okafor I can only manage Tuesday afternoons for the dentist and not Thursday", s)       # case, spacing, closing punctuation
    assert not cap.contains_span("You told Dr Okafor you can only manage Tuesday afternoons for the dentist and not Thursday", s)              # a paraphrase is not the words
    assert not cap.contains_span("I can only manage Tuesday afternoons for the dentist", s) and not cap.contains_span("", s)
    ok = cap.score_exact([True] * 18 + [False] * 2, ["said"] * 10 + ["choice"] * 10, k=5, min_rate=0.9)
    assert ok.ok and ok.evidence["items"] == [18, 20] and ok.evidence["exact"]["by_style"]["choice"] == [8, 10]
    assert not cap.score_exact([True] * 17 + [False] * 3, ["said"] * 20, k=5, min_rate=0.9).ok and not cap.score_exact([], [], k=5, min_rate=0.9).ok


def test_when_did_i_say_it_is_within_half_a_day_and_an_arm_that_cannot_say_fails():
    assert cap.score_when([3, 10.2, 7], [3, 10, 7], min_rate=0.9).ok
    bad = cap.score_when([3, None, 8], [3, 10, 7], min_rate=0.9)
    assert not bad.ok and bad.evidence["when"]["cannot_say"] == 1 and bad.evidence["when"]["hits"] == 1


def test_the_hop_scorer_needs_both_facts_and_says_where_the_chain_broke():
    texts = ["Osric's birthday is on 10 October.", "User's gardener appointment is on 24 April."]
    assert cap.fact_in_rows(texts, ("Osric", "10 October")) and cap.fact_in_rows(texts, ("gardener", "24 April"))
    assert not cap.fact_in_rows(texts[:1], ("gardener", "24 April")) and not cap.fact_in_rows(["Osric lives here. 10 October is cold."], ("Osric", "10 October")) is False
    s = cap.score_hops([True] * 14 + [False] * 6, ["date"] * 10 + ["join"] * 10, a_only=6, b_only=0, k=8, min_rate=0.7)
    assert s.ok and s.evidence["hops"]["only_first"] == 6 and s.evidence["items"] == [14, 20]
    assert not cap.score_hops([True] * 13 + [False] * 7, ["join"] * 20, 7, 0, k=8, min_rate=0.7).ok


LF = life.life(SEED)
GOLD = life.gold_for_scoring(LF)
SIS, OLD_SIS, NEW_SIS = LF.stale[0]
F1, OLD_JOB, NEW_JOB = LF.stale[1]


def obs(*texts, by=""):
    return [{"text": t, "stated_by": by} for t in texts]


def test_an_observation_is_true_false_or_neutral_against_what_the_owner_said_together():
    true = LF.proposals_true[0]
    assert cap.classify_observation(true, GOLD) == (cap.TRUE, "supported")
    assert cap.classify_observation(LF.proposals_fabricated[0], GOLD) == (cap.FALSE, "link")                    # two things the owner never put in one sentence
    assert cap.classify_observation(LF.proposals_fabricated[2], GOLD) == (cap.FALSE, "link")
    assert cap.classify_observation(LF.proposals_stale[0], GOLD) == (cap.FALSE, "stale")                       # the invalidated town as current
    assert cap.classify_observation(f"{SIS} used to live in {OLD_SIS} before {NEW_SIS}.".title(), GOLD)[0] != cap.FALSE      # ... with a history marker it is fine
    assert cap.classify_observation(f"{F1} left {OLD_JOB} for {NEW_JOB}.", GOLD)[0] == cap.TRUE                 # "left X" is history, not a stale restatement
    assert cap.classify_observation("The knee is recovering after the physio.", GOLD)[0] == cap.NEUTRAL         # fewer than two entities: nothing to judge
    foreign = next(x for x in life.POOL_STRINGS if x.lower() not in GOLD["entities"])
    assert cap.classify_observation(f"{foreign} lives in {NEW_SIS}.", GOLD) == (cap.FALSE, "foreign")


def test_precision_is_hard_at_95_percent_over_at_least_three_decidable_observations():
    honest = obs(LF.proposals_true[0], LF.proposals_true[1], LF.proposals_true[3], f"{SIS} lives in {NEW_SIS}.")
    s = cap.score_observations(honest, GOLD, kind="true")
    assert s.ok and s.evidence["items"] == [4, 4] and s.evidence["observations_judged"]["false_by"] == {"foreign": 0, "stale": 0, "link": 0}
    liar = cap.score_observations(honest + obs(LF.proposals_fabricated[0]), GOLD, kind="true")
    assert not liar.ok and liar.stage == "write" and liar.evidence["observations_judged"]["false"] == 1                    # 4 of 5: below 95%
    assert not cap.score_observations(obs(LF.proposals_true[0]), GOLD, kind="true").ok                        # too few decidable: a layer that says nothing has not passed
    assert not cap.score_observations([], GOLD, kind="true").ok


def test_an_insufficient_k1_sample_publishes_no_items_so_it_cannot_enter_the_winner_pool():
    """Class: a cell that failed for want of a sample is not a measurement. Its counts stay in the evidence; its ``items`` must be [0, 0]."""
    for texts in ([], [LF.proposals_true[0]], [LF.proposals_true[0], LF.proposals_true[1]]):
        s = cap.score_observations(obs(*texts), GOLD, kind="true")
        assert not s.ok and s.evidence["items"] == [0, 0] and "too few decidable" in s.evidence["observations_judged"]["reason"]
    row = runner._row(CELLS["K1.observations_are_true"], cellmod.Outcome("FAIL", "read", {"probes": [cap.score_observations(obs(LF.proposals_true[0]), GOLD, kind="true").evidence]}))
    assert artifact.axis_stats([row], CELLS, True)["reflection"]["items"] == {"pass": 0, "n": 0}


def test_a_stale_restatement_and_an_empty_layer_both_fail_currency():
    ok = cap.score_observations(obs(*LF.proposals_true[:3]), GOLD, kind="current")
    assert ok.ok
    stale = cap.score_observations(obs(*LF.proposals_true[:3], *LF.proposals_stale), GOLD, kind="current")
    assert not stale.ok and stale.evidence["currency"]["stale_as_current"] == 2
    empty = cap.score_observations([], GOLD, kind="current")
    assert not empty.ok and empty.stage == "read"                                                              # nothing reflected is not "current", it is empty


def test_attribution_is_checked_in_both_directions():
    assert cap.score_observations(obs(*LF.proposals_true[:3]), GOLD, kind="attributed").ok
    hedged = cap.score_observations(obs(*LF.proposals_true[:3], LF.proposals_hedged[0]), GOLD, kind="attributed")
    assert not hedged.ok and hedged.evidence["attribution"]["violations"] == 1                                 # a stated fact re-stated as an inference
    labelled = cap.score_observations(obs(*LF.proposals_true[:2], f"{SIS} lives in {NEW_SIS}.", by="inferred"), GOLD, kind="attributed")
    assert not labelled.ok                                                                                    # ... or labelled inferred
    said = cap.score_observations(obs(*LF.proposals_true[:3], LF.proposals_presented_as_said[0]), GOLD, kind="attributed")
    assert not said.ok and said.evidence["attribution"]["violations"] == 1                                     # an invented link presented as "you told me"
    assert cap.score_observations([{"text": t, "stated_by": "user"} for t in LF.proposals_true[:3]], GOLD, kind="attributed").ok     # labelled user-stated AND restating a stated claim: fine


def test_thread_recall_and_usefulness_count_only_observations_that_are_not_false():
    covered = cap.covered_threads(obs(LF.proposals_true[0], LF.proposals_true[1], LF.proposals_true[2], LF.proposals_true[3]), GOLD)
    assert covered == {"job", "move", "knee", "concert"}
    assert cap.score_threads(obs(*LF.proposals_true), GOLD).ok
    assert not cap.score_threads(obs(*LF.proposals_true[:2]), GOLD).ok                                        # 2 of 4 threads: below 70%
    assert cap.covered_threads(obs(f"{F1} is a person.", LF.proposals_fabricated[1]), GOLD) == set()          # the fabricated one never counts, the bare one lacks the key token
    ask = [("what's been going on with X lately", ("job",)), ("how has my week been", ("knee", "concert", "job"))]
    got = [obs(LF.proposals_true[0]), obs(LF.proposals_true[2], LF.proposals_true[3])]
    assert cap.score_useful(got, [w for _q, w in ask], GOLD).ok
    assert not cap.score_useful([obs(), obs(LF.proposals_true[2])], [w for _q, w in ask], GOLD).ok            # an empty answer; one of three week threads is under half


def test_the_protocol_metrics_and_their_bars():
    DECLINE = life.DECLINE
    recs = ([{"kind": "needed", "fired": True, "gold": "g%d" % i, "answer": "it is g%d" % i} for i in range(14)]
            + [{"kind": "unneeded", "fired": False, "answer": DECLINE} for _ in range(10)]
            + [{"kind": "silent", "fired": True, "answer": DECLINE} for _ in range(8)])
    m = cap.protocol_metrics(recs)
    assert m["fire_when_needed"] == [14, 14] and m["quiet_when_not_needed"] == [10, 10] and m["cite_precision"] == [14, 14] and m["idk_when_silent"] == [8, 8]
    assert all(cap.score_protocol(recs, k).ok for k in cap.PROTOCOL_BARS)
    always = [{**r, "fired": True} for r in recs]
    assert not cap.score_protocol(always, "quiet_when_not_needed").ok                                         # recall on every turn: the unneeded packet is wasted slot
    never = [{**r, "fired": False, "answer": DECLINE} for r in recs]
    assert not cap.score_protocol(never, "fire_when_needed").ok and not cap.score_protocol(never, "cite_precision").ok       # silence has no precision
    sycophant = [{**r, "answer": "it is g0"} if r["kind"] == "silent" else r for r in recs]
    assert not cap.score_protocol(sycophant, "idk_when_silent").ok and not cap.score_protocol(sycophant, "cite_precision").ok
    with pytest.raises(ValueError):
        cap.score_protocol(recs, "vibes")


def test_the_anchored_reader_answers_only_from_a_row_that_names_what_was_asked():
    rows = [{"text": "User's friend Aldo lives in Bergvik."}]
    assert "Bergvik" in life.anchored_reader(rows, ("Aldo",))
    assert life.anchored_reader(rows, ("Brigid",)) == life.DECLINE and life.anchored_reader([], ("Aldo",)) == life.DECLINE
    assert "Bergvik" in life.anchored_reader(rows, ("Brigid",), sycophantic=True)                              # the control: the nearest row, whatever it says


# ── the cells over a scripted arm: every probe goes green and red ────────────

class ScriptArm(Arm):
    """An arm whose capability answers are scripted: ``verbatim`` (keeps the owner's sentences), ``days`` (says when), ``linked`` (finds the second fact),
    ``obs`` (a list of observation texts) and ``model`` ('own' | 'scripted')."""
    name = "script"

    def __init__(self, *, verbatim=True, days=True, linked=True, obs=None, model="own", caps=("exact_words", "multi_hop", "observations", "protocol", "idle_pass")):
        self.capabilities = frozenset(caps)
        self.verbatim, self.days, self.linked, self.obs, self.model = verbatim, days, linked, obs or [], model
        self.nightly_model = model                      # the arm's contract: whose model writes the nightly observations
        self.proposed: "list[str]" = []                 # every proposal the lab handed this arm
        self.reflections = 0
        self.resets = 0
        self.played: "list[Turn]" = []
        self.facts: "list[tuple[str, int]]" = []

    def reset(self, user_id, **_kw):
        self.resets += 1
        self.played, self.facts = [], []

    def ingest(self, turns):
        self.played += turns
        self.facts += [(t.text, t.day_offset) for t in turns]
        return IngestReport(turns=len(turns), written=len(turns))

    def run_idle_pass(self, transcript, proposes, *, judge=True):
        self.proposed += list(proposes)
        return {"retained": len(proposes)}

    def reflect_pass(self):
        self.reflections += 1
        return {"retained": 0}

    def _ranked(self, query):
        """The facts that share a word of more than three letters with the query, most shared first (ties: said first)."""
        qw = set(re.findall(r"[a-z']+", query.lower())) - {"what", "exactly", "about", "last", "week", "read", "back", "told", "that", "did", "tell"}
        qw = {w for w in qw if len(w) > 3}
        scored = [(len(qw & set(re.findall(r"[a-z']+", t.lower()))), i, t, d) for i, (t, d) in enumerate(self.facts)]
        return [x for x in sorted(scored, key=lambda x: (-x[0], x[1])) if x[0] > 0]

    def recall(self, query, k=10):
        return [{"text": t} for _n, _i, t, _d in self._ranked(query)][:k]

    def recall_exact(self, query, k=5):
        return [{"text": t if self.verbatim else "User said something about it.", "day_offset": d if self.days else None}
                for _n, _i, t, d in self._ranked(query)][:k]

    def recall_linked(self, query, k=8):
        return [{"text": t} for t, _d in self.facts] if self.linked else []

    def observations(self, query=""):
        return {"items": [{"text": t} for t in self.obs], "model": self.model}

    def forget(self, entity): return ""
    def as_of(self, query, ts): return []
    def stats(self): return {"rows": []}

    def protocol_answer(self, prompt, anchor, fired, k=5):
        return life.anchored_reader(self.recall(prompt, k), anchor) if fired else life.DECLINE


def exact_arm_cell_ids():
    return ("J1.exact_sentence_after_100_filler", "J2.when_did_i_say_it")


def test_the_exact_cells_go_green_on_an_arm_that_keeps_the_words_and_red_on_one_that_rewrites_them():
    # the scripted recall_exact is a crude matcher over the questions' longer words; what matters is verbatim vs rewritten and dated vs undated
    for cid in exact_arm_cell_ids():
        o = run(cid, ScriptArm())
        assert o.verdict == "PASS", (cid, o.evidence)
    assert run("J1.exact_sentence_after_100_filler", ScriptArm(verbatim=False)).verdict == "FAIL"             # a rewrite is not the words
    assert run("J2.when_did_i_say_it", ScriptArm(days=False)).verdict == "FAIL"                                # no date, no "when"
    o = run("J1.exact_sentence_after_100_filler", ScriptArm(verbatim=False))
    assert o.stage == "read" and o.evidence["probes"][0]["items"] == [0, 20]


def test_j1_and_j2_share_one_play_and_a_different_seed_replays():
    arm = ScriptArm()
    run("J1.exact_sentence_after_100_filler", arm)
    run("J2.when_did_i_say_it", arm)
    assert arm.resets == 1 and cellmod.demo_user(W, CELLS["J1.exact_sentence_after_100_filler"]) == cellmod.demo_user(W, CELLS["J2.when_did_i_say_it"])
    other = world.make_world("fresh-9")
    cellmod.run_cell(CELLS["J2.when_did_i_say_it"].rendered(other), other, arm)
    assert arm.resets == 2                                                                                     # another seed is another household: a fresh store
    run("L1.two_facts_after_100_filler", arm)
    assert arm.resets == 3                                                                                     # another group too


def test_the_hop_cell_needs_an_associative_arm():
    assert run("L1.two_facts_after_100_filler", ScriptArm(linked=True)).verdict in ("PASS", "FAIL")
    green = run("L1.two_facts_after_100_filler", ScriptArm(linked=True))
    assert green.verdict == "PASS" and green.evidence["probes"][0]["items"] == [20, 20]
    red = run("L1.two_facts_after_100_filler", ScriptArm(linked=False))
    assert red.verdict == "FAIL" and red.evidence["probes"][0]["items"] == [0, 20]


def test_the_reflection_cells_judge_what_the_arm_derived_and_skip_a_scripted_model():
    lf = LF
    honest = ScriptArm(obs=lf.proposals_true)
    for cid in ("K1.observations_are_true", "K2.thread_recall", "K4.invalidated_fact_not_restated", "K5.user_stated_is_never_restated_as_inference"):
        assert run(cid, honest).verdict == "PASS", cid
    liar = ScriptArm(obs=lf.proposals_true + lf.proposals_fabricated + lf.proposals_stale + lf.proposals_hedged + lf.proposals_presented_as_said)
    assert run("K1.observations_are_true", liar).verdict == "FAIL"
    assert run("K4.invalidated_fact_not_restated", liar).verdict == "FAIL"
    assert run("K5.user_stated_is_never_restated_as_inference", liar).verdict == "FAIL"
    scripted = run("K2.thread_recall", ScriptArm(obs=lf.proposals_true, model="scripted"))
    assert scripted.verdict == "SKIP" and "scripted" in scripted.reason                                       # what a scripted model says is the script's
    assert run("K3.useful_answers", ScriptArm(obs=lf.proposals_true, model="scripted")).verdict == "SKIP"
    assert run("K1.observations_are_true", ScriptArm(obs=lf.proposals_true, model="scripted")).verdict == "PASS"      # the store's keeping is measured either way


def test_an_own_model_arm_is_never_handed_the_labs_scripted_proposals_and_a_scripted_arm_is():
    """The scripted proposals are DELIBERATE lies (fabricated links, stale facts, mis-attributions). An arm with its own nightly model that were handed them
    would retain them as facts and consolidate its observations partly from them: K1-K3 would measure the harness, not the arm."""
    lf = LF
    own = ScriptArm(obs=lf.proposals_true, model="own")
    for cid in ("K1.observations_are_true", "K2.thread_recall", "K3.useful_answers", "K4.invalidated_fact_not_restated"):
        run(cid, own)
    assert own.proposed == [] and own.reflections >= 1                                  # it reflected over the life it ingested, with nothing injected
    assert own.resets == 1 and any(t.text for t in own.played)                          # ... and ONE play served all four K cells (the group), over a real life
    scripted = ScriptArm(obs=lf.proposals_true, model="scripted")
    run("K1.observations_are_true", scripted)
    lies = set(lf.proposals_fabricated + lf.proposals_stale + lf.proposals_hedged + lf.proposals_presented_as_said)
    assert lies and lies <= set(scripted.proposed) and scripted.reflections == 0         # the script reaches the arm whose model the lab scripts
    h2 = HindsightArm("H2", transport=FakeHindsight(), settle_poll_s=0)
    assert h2.nightly_model == "own" and HindsightArm.nightly_model == "own" and Arm.nightly_model == "scripted"
    h2.close()


def test_an_arm_without_the_capability_skips_with_the_reason_and_never_errors_or_passes():
    bare = ScriptArm(caps=())
    for cid in ("J1.exact_sentence_after_100_filler", "J2.when_did_i_say_it", "L1.two_facts_after_100_filler", "K1.observations_are_true", "K4.invalidated_fact_not_restated",
                "M1.answered_when_recall_fired", "M2.cites_only_the_right_fact", "M3.says_idk_when_the_store_is_silent"):
        o = run(cid, bare)
        assert o.verdict == "SKIP" and "lacks capability" in o.reason, (cid, o)
    assert "exact_words" in run("J1.exact_sentence_after_100_filler", bare).reason and "observations" in run("K1.observations_are_true", bare).reason
    assert "idle_pass" in run("K1.observations_are_true", ScriptArm(caps=("observations",))).reason            # the nightly pass is the other half of the K cells

    class NoExact(ScriptArm):
        def recall_exact(self, *a, **k):
            raise NotImplementedError("no exact lookup")
    assert run("J1.exact_sentence_after_100_filler", NoExact()).verdict == "SKIP"                              # declared but not implemented: still a SKIP, never an ERROR


def test_the_protocol_lab_cells_pass_on_an_honest_reader_and_fail_on_a_sycophantic_one():
    class Arm_(ScriptArm):
        sycophantic = False

        def protocol_answer(self, prompt, anchor, fired, k=5):
            return life.anchored_reader(self.recall(prompt, k), anchor, sycophantic=self.sycophantic) if fired else life.DECLINE
    for cid in ("M1.answered_when_recall_fired", "M2.cites_only_the_right_fact", "M3.says_idk_when_the_store_is_silent"):
        assert run(cid, Arm_()).verdict == "PASS", cid
    syc = Arm_()
    syc.sycophantic = True
    assert run("M3.says_idk_when_the_store_is_silent", syc).verdict == "FAIL" and run("M2.cites_only_the_right_fact", syc).verdict == "FAIL"
    o = run("M3.says_idk_when_the_store_is_silent", Arm_())
    assert set(o.evidence["probes"][0]["compared"]) == {"zoe", "mempalace5", "hindsight"}                         # every protocol's number rides in the evidence


def test_the_brain_tier_protocol_cells_are_declared_with_their_reason_and_never_run_in_the_lab():
    declared = [c for c in CELLS.values() if c.id.startswith("M4.")]
    assert len(declared) == 12 and {c.id.split(".")[2] for c in declared} == {"zoe", "mempalace5", "hindsight"}
    assert {c.id.split(".")[1] for c in declared} == set(cap.PROTOCOL_BARS) - {"answered_when_fired"}
    assert all(c.tier == "full" and "clone brain" in c.skip_reason and c.axis == "protocol" for c in declared)
    o = cellmod.run_cell(declared[0].rendered(W), W, ScriptArm())
    assert o.verdict == "SKIP" and o.brain_turns == 0


# ── the arms ─────────────────────────────────────────────────────────────────

def hm(controls=None):
    c = controls or Controls()
    return HMArm(distilled=FakeDistilledTier(), verbatim=MemPalaceVerbatimArm(store=InMemoryVerbatimStore(), controls=c), controls=c)


def test_hm_and_the_verbatim_tier_pass_exact_words_and_hm_without_its_exact_lookup_fails():
    for arm in (hm(), MemPalaceVerbatimArm(store=InMemoryVerbatimStore())):
        for cid in exact_arm_cell_ids():
            o = run(cid, arm)
            assert o.verdict == "PASS", (arm.name, cid, o.evidence)
        arm.close()
    off = hm(Controls().off("exact_lookup"))
    assert run("J1.exact_sentence_after_100_filler", off).verdict == "FAIL" and run("J2.when_did_i_say_it", off).verdict == "FAIL"     # the NEGATIVE CONTROL
    assert "exact_words" in HMArm.capabilities and "exact_words" in MemPalaceVerbatimArm.capabilities and "multi_hop" in HMArm.capabilities


def test_hindsight_arms_declare_the_capability_axes_and_h1_has_no_observation_layer():
    h1, h2 = HindsightArm("H1", transport=FakeHindsight(), settle_poll_s=0), HindsightArm("H2", transport=FakeHindsight(), settle_poll_s=0)
    assert {"exact_words", "multi_hop", "protocol"} <= h1.capabilities and "observations" not in h1.capabilities and "observations" in h2.capabilities
    o = run("K1.observations_are_true", h1)
    assert o.verdict == "SKIP" and "observations" in o.reason
    h1.close()
    h2.close()


def test_h2_exports_its_observation_layer_with_the_class_of_the_facts_it_came_from():
    arm = HindsightArm("H2", transport=FakeHindsight(), settle_poll_s=0)
    arm.reset("demo_bar_1a2b3c4d")
    arm.ingest([Turn("User's friend Aldo lives in Bergvik.", "owner_taught")])
    ex = arm.observations()
    assert ex["model"] == "own" and ex["items"] and all(i["stated_by"] == "user" for i in ex["items"])           # consolidated from a user-stated fact
    assert arm.observations("where does Aldo live")["items"]
    arm.close()


def test_mempalace_filing_time_runs_forward_from_the_days_ago_offsets():
    """Class: ``Turn.day_offset`` is DAYS AGO. A turn said 9 days ago is filed EARLIER than one said 2 days ago, and the age read back is the offset."""
    arm = hm()
    arm.reset("demo_bar_1a2b3c4d")
    arm.ingest([Turn("Osric booked the ferry for Friday morning.", "owner_typed", day_offset=9),
                Turn("Osric moved the ferry to Saturday evening.", "owner_typed", day_offset=2)])
    rows = {r["text"]: r for r in arm.verbatim.stats()["rows"]}
    old, new = rows["Osric booked the ferry for Friday morning."], rows["Osric moved the ferry to Saturday evening."]
    assert old["filed_ts"] < new["filed_ts"]
    got = {r["text"]: r["day_offset"] for r in arm.recall_exact("Osric ferry", 5)}
    assert got["Osric booked the ferry for Friday morning."] == 9 and got["Osric moved the ferry to Saturday evening."] == 2
    arm.close()


def test_the_linked_probes_ask_the_distilled_tier_for_the_high_budget_like_the_direct_hindsight_arm():
    """The HM packet's ordinary lookups stay at the low budget; ONLY the linked (L) probe asks for ``high`` (the link graph), as Hindsight's own ``recall_linked`` does."""
    arm = hm()
    arm.reset("demo_bar_1a2b3c4d")
    seen = []
    real = arm.distilled.recall
    arm.distilled.recall = lambda user, query, k, **kw: (seen.append(kw.get("budget", "low")), real(user, query, k, **kw))[1]
    arm.recall_linked("Osric ferry", 5)
    arm.recall("Osric ferry", 5)
    assert seen == ["high", "low"]
    arm.close()


def test_hindsight_rows_carry_the_day_they_were_said():
    arm = HindsightArm("H2", transport=FakeHindsight(), settle_poll_s=0)
    arm.reset("demo_bar_1a2b3c4d")
    arm.ingest([Turn("User's friend Aldo lives in Bergvik.", "owner_taught", day_offset=9)])
    got = arm.recall_exact("where does Aldo live", 3)
    assert got and got[0]["day_offset"] == 9
    arm.close()


# ── the spec and the artifact ────────────────────────────────────────────────

def test_the_protocol_cells_share_one_play_group_so_the_corpus_is_taught_once():
    """M1-M3 ingest the same 14 protocol facts: without a shared play_group each replays the corpus (3 x 14 retains, unbudgeted, ~43 s on H1)."""
    ms = [CELLS[i] for i in ("M1.answered_when_recall_fired", "M2.cites_only_the_right_fact", "M3.says_idk_when_the_store_is_silent")]
    assert {c.params.get("play_group") for c in ms} == {"M"} and all(c.events == ms[0].events for c in ms)
    # the class: any capability-axis store cells that teach the SAME event list must name ONE shared group (else each replays the whole corpus)
    for axis in ("exact_words", "reflection", "multi_hop", "protocol"):
        by_events: "dict[str, list]" = {}
        for c in CELLS.values():
            if c.axis == axis and c.tier == "store" and c.events:
                by_events.setdefault(json.dumps(c.events, sort_keys=True), []).append(c)
        for group in (g for g in by_events.values() if len(g) > 1):
            assert len({c.params.get("play_group") for c in group}) == 1 and group[0].params.get("play_group"), [c.id for c in group]
    arm = ScriptArm(caps=("protocol", "idle_pass"))
    runner.run_cells(ms, W, arm)
    assert arm.resets == 1 and len(arm.played) == 14                                      # ONE play of the 14 facts, read three times
    assert len(life.protocol_corpus(SEED)[0]) == 14 and __import__("zmb.bakeoff_measure", fromlist=["x"]).CAP_RETAINS["protocol"] == 14


def test_the_capability_axes_are_in_the_spec_with_the_cells_the_brief_names():
    assert {"j": "exact_words", "k": "reflection", "l": "multi_hop", "m": "protocol"}.items() <= spec.AXES.items()
    by_axis = {a: sorted(c.id for c in CELLS.values() if c.axis == a and c.tier == "store") for a in ("exact_words", "reflection", "multi_hop", "protocol")}
    assert by_axis["exact_words"] == ["J0.taught_sentence_is_returned_whole", "J1.exact_sentence_after_100_filler", "J2.when_did_i_say_it"]
    assert sorted(i.split(".")[0] for i in by_axis["reflection"]) == sorted(["K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9", "K9", "K10", "K11", "K12"])
    assert by_axis["multi_hop"] == ["L0.two_facts_one_question", "L1.two_facts_after_100_filler", "L2.two_facts_after_300_filler"]
    assert by_axis["protocol"] == ["M1.answered_when_recall_fired", "M2.cites_only_the_right_fact", "M3.says_idk_when_the_store_is_silent"]
    j1 = CELLS["J1.exact_sentence_after_100_filler"]
    assert [e.get("do") for e in j1.events] == ["exact_needles", "filler"] and j1.events[1]["turns"] == 100 and j1.probes[0]["min_rate"] == 0.9
    assert CELLS["L1.two_facts_after_100_filler"].events[1]["turns"] + CELLS["L1.two_facts_after_100_filler"].events[3]["turns"] == 100
    assert CELLS["L2.two_facts_after_300_filler"].events[1]["turns"] + CELLS["L2.two_facts_after_300_filler"].events[3]["turns"] == 300
    assert next(p for p in CELLS["K1.observations_are_true"].probes)["min_precision"] == 0.95                    # hard
    assert CELLS["K2.thread_recall"].probes[0]["min_recall"] == 0.7 and CELLS["K2.thread_recall"].expected == "PASS"


def test_every_cell_that_can_pass_on_z0_has_a_control_and_every_target_names_its_gap():
    for c in CELLS.values():
        if c.axis not in ("exact_words", "reflection", "multi_hop", "protocol") or c.tier != "store":
            continue
        if c.expected == "FAIL":
            assert not c.controls and "Z0" in c.note, c.id                                                      # a target is already red: no control, and it says whose gap it is
        elif not c.sanity:
            assert c.controls, c.id                                                                             # K2 / K3 and K6-K12 need an own model (Z0n): their controls are the night mind's faults
    # K4 is a two-layer defence since the observation gate: the authority wall holds a stale restatement as a disputed candidate AND the gate
    # holds a claim the owner's words do not carry; both off together turn it red
    assert CELLS["K4.invalidated_fact_not_restated"].controls == ("authority", "observation_gate")
    # the three gaps the first run of these axes measured on Z0 are fixed: graded cells, each with the switch that turns it red
    for cid, ctl in (("J1.exact_sentence_after_100_filler", "exact_index"), ("J2.when_did_i_say_it", "exact_index"),
                     ("K5.user_stated_is_never_restated_as_inference", "observation_gate"),
                     ("L1.two_facts_after_100_filler", "multi_hop"), ("L2.two_facts_after_300_filler", "multi_hop")):
        assert CELLS[cid].expected == "PASS" and CELLS[cid].controls == (ctl,), cid
    # K1 is two walls since the night mind: the observation gate (the digest's, and the pass's) and the pass's verbatim-quote / cited-turn check
    assert CELLS["K1.observations_are_true"].expected == "PASS" and CELLS["K1.observations_are_true"].controls == ("observation_gate", "night_citations")
    assert CELLS["J0.taught_sentence_is_returned_whole"].sanity and CELLS["L0.two_facts_one_question"].sanity


def test_axis_stats_pool_items_and_name_the_failing_cells():
    rows = [runner._row(CELLS["J1.exact_sentence_after_100_filler"], cellmod.Outcome("FAIL", "read", {"probes": [{"items": [3, 20]}]})),
            runner._row(CELLS["J2.when_did_i_say_it"], cellmod.Outcome("PASS", "", {"probes": [{"items": [19, 20]}]})),
            runner._row(CELLS["J0.taught_sentence_is_returned_whole"], cellmod.Outcome("PASS", "", {"probes": [{"items": [1, 1]}]}))]
    st = artifact.axis_stats(rows, CELLS, True)["exact_words"]
    assert st["items"] == {"pass": 22, "n": 40} and st["failing"] == ["J1.exact_sentence_after_100_filler"] and (st["pass"], st["n"]) == (1, 2)      # the sanity cell's items are not evidence
    assert artifact.axis_stats([], CELLS, True)["reflection"]["items"] == {"pass": 0, "n": 0}


def test_the_artifact_of_a_capability_run_carries_counts_and_never_a_corpus_string(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "revision", lambda: None)
    arm = hm()
    rows = runner.run_cells([CELLS[i] for i in ("J1.exact_sentence_after_100_filler", "L1.two_facts_after_100_filler", "M1.answered_when_recall_fired")], W, arm)
    blob = json.dumps(rows)
    assert artifact.household_strings_in(blob, world.pool_strings() + [x.sentence for x in life.exact_corpus(SEED)] + [h.fact_a for h in life.hop_corpus(SEED)]) == []
    arm.close()
