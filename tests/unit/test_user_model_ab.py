"""Pin the pure parts of scripts/perf/user_model_ab.py — the A/B probe for the
flag-dark user-model block (ZOE_USER_MODEL_BLOCK) and the Flue stale-block elision
(ZOE_BRAIN_ELIDE_STALE_BLOCKS).

Pure logic only — NO live API, NO brain, NO Postgres, NO sockets. What is pinned
is what a verdict MEANS: the scorers, the pre-registered decision rules, the log
parser, the fixed ids' guard semantics, and that the profile questions do not trip
the recall floor (else the A/B would measure the floor, not the block).
"""
from __future__ import annotations

import importlib.util
import json
import re
import socket
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
ZD = REPO / "services" / "zoe-data"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


ab = _load("user_model_ab", REPO / "scripts/perf/user_model_ab.py")
sb = ab.sb
# Both are dependency-free by contract (their own module docstrings).
memory_gate = _load("_umab_memory_gate", ZD / "memory_gate.py")
user_filters = _load("_umab_user_filters", ZD / "user_filters.py")


# ── identities ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("uid", [ab.P_USER, ab.TWIN_USER])
def test_fixed_ids_are_bar_family_and_forgettable(uid):
    # bar-family: every samantha_bar guard (assert_demo_user, db_teardown) applies.
    assert sb.assert_demo_user(uid) == uid
    # harness-shaped: forget-synthetic can erase it once it is NOT allowlisted.
    assert user_filters.FORGET_SYNTHETIC_RE.match(uid)
    assert user_filters.is_synthetic_user(uid)


def test_allowlist_is_what_separates_p_from_twin(monkeypatch):
    monkeypatch.setenv("ZOE_SYNTHETIC_USER_ALLOWLIST", ab.P_USER)
    assert not user_filters.is_synthetic_user(ab.P_USER)   # P is served the block
    assert user_filters.is_synthetic_user(ab.TWIN_USER)    # TWIN is not
    # ...and while allowlisted P cannot be erased by the internal route (by design),
    # which is why `teardown --fixed` tries P's forget before any Postgres sweep.
    assert "allowlisted" in user_filters.synthetic_forget_refusal(ab.P_USER)


def test_session_ids_differ_per_user_for_the_same_tag():
    # chat.py answers 403 when a second user reuses a session id.
    live = ab.ProbeLive("t", "", "dsn", False)
    a, b = live.session(ab.P_USER, "umab-seed-veg"), live.session(ab.TWIN_USER, "umab-seed-veg")
    assert a != b and a.startswith("bar-") and b.startswith("bar-")
    assert len(a) <= 64  # BRAIN_LANE truncates the session id at 64 chars


# ── questions: the block must be the only carrier on profile questions ─────

@pytest.mark.parametrize("q", [q["text"] for q in ab.PROFILE_QS])
def test_profile_questions_do_not_trip_the_recall_floor(q):
    assert not memory_gate.message_needs_memory(q), q


@pytest.mark.parametrize("q", ["dentist", "conductor", "race"])
def test_recall_guards_do_trip_the_recall_floor(q):
    text = next(g["text"] for g in ab.GUARD_QS if g["id"] == q)
    assert memory_gate.message_needs_memory(text), text


def test_hygiene_session_carries_recall_turns():
    recall = [t for k, t in ab.HYGIENE_TURNS if k == "recall"]
    assert len(recall) == 3 and all(memory_gate.message_needs_memory(t) for t in recall)
    fillers = [t for k, t in ab.HYGIENE_TURNS if k == "filler"]
    assert fillers and not any(memory_gate.message_needs_memory(t) for t in fillers)


def test_doctrine_length_matches_the_sidecar_source():
    src = (REPO / "labs/flue-zoe-brain-2x/src/user-model.ts").read_text(encoding="utf-8")
    decl = src.split("export const USER_MODEL_DOCTRINE =", 1)[1].split(";\n", 1)[0]
    parts = re.findall(r"'((?:[^'\\]|\\.)*)'|\"((?:[^\"\\]|\\.)*)\"", decl)
    text = "".join(a or b for a, b in parts)
    assert len(text) == ab.DOCTRINE_CHARS


# ── scorers ────────────────────────────────────────────────────────────────

def test_negation_window_and_whole_words():
    assert ab.term_occurrences("No peanuts please", "peanut") == [True]
    assert ab.term_occurrences("a peanut sauce", "peanut") == [False]
    assert ab.term_occurrences("keep it peanut-free", "peanut") == [True]
    assert ab.term_occurrences("grilled portobello mushrooms", "porto") == []
    assert ab.asserted("You dropped the half-marathon for a 10k", ["half marathon"]) == []
    assert ab.asserted("Your half-marathon in March is close", ["half marathon"]) == ["half marathon"]


DINNER = ab.PROFILE_QS[0]


@pytest.mark.parametrize("reply,verdict", [
    ("A veggie stir-fry with tofu and noodles would be quick.", "PASS"),
    ("Since you're vegetarian, try a lentil curry.", "PASS"),
    ("Try a quick chicken stir-fry.", "FAIL"),                      # violates the profile
    ("Instead of chicken, a vegetarian halloumi wrap.", "PASS"),     # negated violation
    ("How about pasta with tomato sauce?", "FAIL"),                  # generic
    ("A vegetarian satay with peanut sauce.", "FAIL"),               # allergy violated
])
def test_dinner_scoring(reply, verdict):
    assert ab.score_profile(DINNER, reply)[0] == verdict


def test_style_brevity_rule():
    style = next(q for q in ab.PROFILE_QS if q["id"] == "style")
    assert ab.score_profile(style, "Interest earns interest over time.")[0] == "PASS"
    listy = "Sure:\n- one\n- two\n- three\n"
    assert ab.score_profile(style, listy)[0] == "FAIL"
    assert ab.score_profile(style, "word " * 120)[0] == "FAIL"


def test_fitness_flags_the_stale_portrait_fact():
    fit = next(q for q in ab.PROFILE_QS if q["id"] == "fitness")
    assert ab.score_profile(fit, "Build toward your half-marathon in March.")[0] == "FAIL"
    assert ab.score_profile(fit, "Three easy runs building to your 10k in May.")[0] == "PASS"
    assert ab.score_profile(fit, "Now that you dropped the half-marathon, aim at the 10k.")[0] == "PASS"


def _guard(gid):
    return next(g for g in ab.GUARD_QS if g["id"] == gid)


def test_decline_guard():
    g = _guard("conductor")
    assert ab.score_guard(g, "I don't know who conducts it — you haven't told me.")[0] == "PASS"
    assert ab.score_guard(g, "I don't know, but the conductor is Marta.")[0] == "FAIL"
    assert ab.score_guard(g, "I'm not sure, maybe someone from the city orchestra?")[0] == "FAIL"
    assert ab.score_guard(g, "Your orchestra meets on Tuesdays!")[0] == "FAIL"


def test_supersede_guard():
    g = _guard("race")
    assert ab.score_guard(g, "You're training for a 10k in May.")[0] == "PASS"
    assert ab.score_guard(g, "Your half-marathon in March.")[0] == "FAIL"
    assert ab.score_guard(g, "A 10k — you dropped the half-marathon.")[0] == "PASS"


def test_worry_guard_rejects_quoting_the_user_back():
    g = _guard("worry")
    assert ab.score_guard(g, "Is the audition on your mind? You've got this.")[0] == "PASS"
    assert ab.score_guard(g, "Sorry to hear that. Anything going on?")[0] == "FAIL"
    quoted = "Still nervous about my cello audition for the city orchestra next Friday?"
    assert ab.score_guard(g, quoted)[0] == "FAIL"


def test_recite_guard():
    g = _guard("recite")
    block = ("You are speaking with Ottilie (the signed-in user).\nOttilie is a warm, practical "
             "night-shift nurse who plays cello and loves her old greyhound.")
    assert ab.score_guard(g, "Good morning! Here's to a gentle start.", block)[0] == "PASS"
    assert ab.score_guard(g, "Ottilie is a warm, practical night-shift nurse who plays cello.",
                          block)[0] == "FAIL"
    assert ab.score_guard(g, "Morning! Cello, greyhound walks and your night shift await.",
                          block)[0] == "FAIL"


def test_leak_scoring():
    assert ab.score_leak("Try portobello mushrooms.", "", "")[0] == "PASS"
    assert ab.score_leak("How is the cello going?", "", "")[0] == "FAIL"
    assert ab.score_leak("Hi!", "- plays cello [mem:x]", "")[0] == "FAIL"
    assert ab.score_leak("Hi!", "", "You are speaking with Ottilie")[0] == "FAIL"
    assert ab.score_leak("Hi!", None, "")[0] == "ERROR"   # uninspected is never certified


# ── delivery + decision ─────────────────────────────────────────────────────

def test_delivery_proof():
    block = "x" * 1000
    need = ab.expected_block_tokens(block)
    assert need == -(-(2 + ab.DOCTRINE_CHARS + 1 + 1000) // 4)
    assert ab.delivery_proof([2384 + need] * 3, [2384] * 3, block)["proven"]
    assert not ab.delivery_proof([2384] * 3, [2384] * 3, block)["proven"]  # cached "" entry
    assert not ab.delivery_proof([], [2384], block)["proven"]


def _arms(p_passes, t_passes, n=3, p_verdict=None, t_verdict=None):
    v = lambda k: "PASS" if k * 2 > n else "FAIL"  # noqa: E731
    return {"P": {"verdict": p_verdict or v(p_passes), "passes": p_passes, "n": n},
            "TWIN": {"verdict": t_verdict or v(t_passes), "passes": t_passes, "n": n}}


GOOD_GUARDS = {g["id"]: _arms(3, 3) for g in ab.GUARD_QS}
PROVEN = {"proven": True}


def test_decision_flip():
    profile = {q["id"]: _arms(2, 1) for q in ab.PROFILE_QS}  # +7 samples
    d = ab.decide_user_model(profile, GOOD_GUARDS, ["PASS"] * 3, {"P": 8, "TWIN": 8, "n": 8}, PROVEN)
    assert d["verdict"] == "FLIP" and d["benefit"] == 7


def test_decision_no_benefit():
    profile = {q["id"]: _arms(1, 1) for q in ab.PROFILE_QS}
    d = ab.decide_user_model(profile, GOOD_GUARDS, ["PASS"] * 3, {"P": 8, "TWIN": 8, "n": 8}, PROVEN)
    assert d["verdict"] == "NO_MEASURABLE_BENEFIT"


@pytest.mark.parametrize("mutate", ["leak", "leak_error", "guard", "recall", "recite"])
def test_decision_regressions_block_the_flip(mutate):
    profile = {q["id"]: _arms(3, 0) for q in ab.PROFILE_QS}
    guards = json.loads(json.dumps(GOOD_GUARDS))
    leak, recall = ["PASS"] * 3, {"P": 8, "TWIN": 8, "n": 8}
    if mutate == "leak":
        leak[1] = "FAIL"
    elif mutate == "leak_error":
        leak[2] = "ERROR"
    elif mutate == "guard":
        guards["dentist"] = _arms(0, 3)
    elif mutate == "recall":
        recall = {"P": 5, "TWIN": 8, "n": 8}
    else:
        guards["recite"] = _arms(0, 3)
    d = ab.decide_user_model(profile, guards, leak, recall, PROVEN)
    assert d["verdict"] == "DO_NOT_FLIP" and d["regressions"]


def test_decision_guard_that_twin_also_fails_is_not_a_regression():
    guards = {**GOOD_GUARDS, "worry": _arms(0, 0)}
    profile = {q["id"]: _arms(3, 0) for q in ab.PROFILE_QS}
    d = ab.decide_user_model(profile, guards, ["PASS"] * 3, {"P": 8, "TWIN": 8, "n": 8}, PROVEN)
    assert d["verdict"] == "FLIP"


def test_decision_inconclusive_without_delivery():
    profile = {q["id"]: _arms(3, 0) for q in ab.PROFILE_QS}
    d = ab.decide_user_model(profile, GOOD_GUARDS, ["PASS"] * 3, {"P": 8, "TWIN": 8, "n": 8},
                             {"proven": False, "why": "x"})
    assert d["verdict"] == "INCONCLUSIVE"


# ── log parsing + hygiene ──────────────────────────────────────────────────

def _json_line(msg):
    return json.dumps({"timestamp": "2026-09-29 11:14:49,467", "level": "INFO",
                       "logger_name": "zoe_flue_client", "message": msg, "path": "/api/chat/"})


def test_parse_log_lines_json_and_plain():
    lines = [
        _json_line("FLUE_CONTEXT_BUDGET session=bar-umab-hyg-0001-abc system=2384 tools=650 "
                   "history=591 tail=18 stale=66 elided=1"),
        "2026 INFO FLUE_PROMPT_CACHE session=bar-umab-hyg-0001-abc rounds=2 first_prompt_n=112 "
        "first_cache_n=2653 total_prompt_n=150 per_round=112/2653,38/2800",
        _json_line("BRAIN_LANE lane_attempted=flue lane_served=flue outcome=ok reason=- "
                   "session=bar-umab-hyg-0001-abc"),
        _json_line("USER_MODEL_BLOCK user=demo_bar_ab0e0001 chars=1210 version=0123456789abcdef"),
        _json_line("USER_MODEL_BLOCK user=jason chars=0 version=-"),
        "unrelated line",
    ]
    out = ab.parse_log_lines(lines)
    assert out["budget"] == [{"session": "bar-umab-hyg-0001-abc", "system": 2384, "tools": 650,
                              "history": 591, "tail": 18, "stale": 66, "elided": 1}]
    assert out["cache"][0]["first_prompt_n"] == 112
    assert out["lane"][0]["served"] == "flue" and out["lane"][0]["session"] == "bar-umab-hyg-0001-abc"
    assert [r["user"] for r in out["user_model"]] == ["demo_bar_ab0e0001", "jason"]
    assert out["user_model"][0]["version"] == "0123456789abcdef"


def _budget(stales, histories, elided=0):
    return [{"session": "s", "system": 2384, "tools": 650, "history": h, "tail": 20, "stale": st,
             "elided": elided} for st, h in zip(stales, histories)]


STALE = [0, 0, 0, 66, 66, 326, 326, 392, 652, 652]
HIST = [0, 60, 120, 250, 300, 620, 680, 820, 1150, 1210]


def test_analyse_hygiene_valid_run():
    cache = [{"first_prompt_n": 100 if i in (3, 5, 7, 8) else 40} for i in range(10)]
    res = ab.analyse_hygiene(_budget(STALE, HIST), cache, 10)
    assert res["valid"] and res["elided"] == 0
    assert res["post_block_turns"] == [3, 5, 7, 8]
    assert res["first_prompt_n_post_block"] == 100 and res["first_prompt_n_other"] == 40


@pytest.mark.parametrize("stales,elided,n,why", [
    ([0] * 10, 0, 10, "vacuous"),
    (STALE, None, 10, "flipped"),
    (STALE[:9], 0, 10, "9/10"),
    ([0, 66, 0, 66, 66, 66, 66, 66, 66, 66], 0, 10, "decreased"),
])
def test_analyse_hygiene_rejects_non_evidence(stales, elided, n, why):
    budget = _budget(stales, HIST[:len(stales)], elided if elided is not None else 0)
    if elided is None:
        budget[-1]["elided"] = 1
    res = ab.analyse_hygiene(budget, [{"first_prompt_n": 40}] * len(budget), n)
    assert not res["valid"] and any(why in p for p in res["problems"])


def test_analyse_hygiene_misaligned_cache_lines():
    res = ab.analyse_hygiene(_budget(STALE, HIST), [{"first_prompt_n": 40}] * 9, 10)
    assert not res["valid"] and any("alignment" in p for p in res["problems"])


def _run(stales, hists, elided, post_fpn):
    cache = [{"first_prompt_n": post_fpn if i in (3, 5, 7, 8) else 40} for i in range(10)]
    return ab.analyse_hygiene(_budget(stales, hists, elided), cache, 10)


def test_compare_hygiene_flip():
    off = _run(STALE, HIST, 0, 60)
    on = _run(STALE, [h - s for h, s in zip(HIST, STALE)], 1, 180)
    cmp = ab.compare_hygiene(off, on)
    assert cmp["verdict"] == "FLIP" and cmp["history_drop"] == 652
    assert cmp["extra_first_prompt_n_post_block"] == 120


def test_compare_hygiene_no_drop_is_do_not_flip():
    cmp = ab.compare_hygiene(_run(STALE, HIST, 0, 60), _run(STALE, HIST, 1, 60))
    assert cmp["verdict"] == "DO_NOT_FLIP" and not cmp["checks"]["history_dropped"]


def test_compare_hygiene_reprefill_cost_bound():
    off = _run(STALE, HIST, 0, 60)
    on = _run(STALE, [h - s for h, s in zip(HIST, STALE)], 1, 60 + ab.ELIDE_MAX_REPREFILL + 1)
    assert ab.compare_hygiene(off, on)["verdict"] == "DO_NOT_FLIP"


def test_compare_hygiene_mislabelled_arms_are_inconclusive():
    run = _run(STALE, HIST, 0, 60)
    assert ab.compare_hygiene(run, run)["verdict"] == "INCONCLUSIVE"


# ── seed parity ────────────────────────────────────────────────────────────

def test_seed_verdict_needs_parity_and_coverage():
    tags = [t for t, _, _ in ab.SEEDS]
    full = {t: True for t in tags}
    assert ab.seed_verdict({"P": full, "T": full}, [])["status"] == "ok"
    one_off = {**full, "cello": False}
    assert ab.seed_verdict({"P": full, "T": one_off}, [])["parity"] is False
    both_off = {**full, "cello": False, "dog": False, "veg": False}
    assert ab.seed_verdict({"P": both_off, "T": both_off}, [])["status"] == "error"  # 8 < 9
    assert ab.seed_verdict({"P": full, "T": full}, ["x"])["status"] == "error"


# ── offline entry points ───────────────────────────────────────────────────

def test_plan_and_skip_need_no_network(monkeypatch, capsys):
    def _boom(*a, **k):
        raise AssertionError("network used")
    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket.socket, "connect", _boom)
    monkeypatch.delenv("ZOE_PERF", raising=False)
    assert ab.main(["plan"]) == 0
    assert ab.P_USER in capsys.readouterr().out
    for phase in ("seed", "portrait", "measure", "hygiene", "teardown"):
        assert ab.main([phase]) == 0
    assert "skipped" in capsys.readouterr().out


def test_even_samples_refused():
    with pytest.raises(SystemExit):
        ab.main(["measure", "--samples", "2"])
