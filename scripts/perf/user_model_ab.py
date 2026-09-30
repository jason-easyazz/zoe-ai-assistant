#!/usr/bin/env python3
"""user_model_ab.py — live A/B evidence for two flag-dark Flue context features.

1. ``ZOE_USER_MODEL_BLOCK`` (zoe-data, #1783): the per-user card of current facts
   (``user_model_card.py``; it was the weekly narrative portrait until the first run
   measured that inert) appended to the Flue system prompt. The Samantha bar cannot see it:
   its ``demo_bar_*`` users are synthetic, so ``load_user_model_block`` returns
   ``""`` for them and their wire is byte-identical in both flag states.
2. ``ZOE_BRAIN_ELIDE_STALE_BLOCKS`` (sidecar, #1785): older user messages lose
   their injected blocks on the wire. The bar cannot see this either: none of its
   sessions carries a block turn followed by another turn, so ``stale`` is 0 on
   every bar turn (153/153 in the live log) and the flag changes no byte.

Design (docs/knowledge/user-model-ab.md has the full protocol):

* ``seed`` / ``portrait`` / ``measure`` — a TWIN design with the flag ON. Two
  fixed, harness-shaped users get the same synthetic profile (a vegetarian
  night-shift ICU nurse who plays cello — no household data): ``P`` (listed in
  ``ZOE_SYNTHETIC_USER_ALLOWLIST``, so it is served the block) and ``TWIN`` (not
  listed, so it is not). Both get a portrait, so the core lane and the
  continuity block's portrait line see the same thing for both; the ONLY
  difference on the Flue lane is the block. ``measure`` asks the same questions
  of both, interleaved, in fresh sessions, and scores the replies
  deterministically. A fresh non-allowlisted user ``L`` asks right after ``P``
  every time, as the isolation (S6) check. Block delivery is PROVEN from the
  sidecar's own ``FLUE_CONTEXT_BUDGET system=`` accounting (P minus TWIN), never
  assumed from the env.
* ``hygiene`` — one 10-turn session for a fresh synthetic user, whose turns
  inject recall and continuity blocks. The ``FLUE_CONTEXT_BUDGET`` and
  ``FLUE_PROMPT_CACHE`` lines of that session are read back from the app log.
  Run it once per sidecar flag state; ``hygiene-compare`` diffs the two runs.
* ``teardown`` — fresh users are torn down in a ``finally`` by the bar's own
  proven teardown. The fixed users are torn down only on request (``--fixed``),
  and only once ``P`` is no longer allowlisted (``forget-synthetic`` refuses an
  allowlisted id, by design).

Reuses ``samantha_bar.py`` for everything load-bearing (identity guard, HTTP
client, capture wait, teardown proof, gates) — the ids are bar-family ids
(``demo_bar_<8 hex>``), so every bar guardrail applies unchanged.

Exit: 0 ran | 1 a pre-registered regression rule fired | 2 refused / error /
teardown unproven | 3 harness lock held. Without ``ZOE_PERF=1``: skip, exit 0.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import secrets
import signal
import statistics
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
import samantha_bar as sb  # noqa: E402

HARNESS_VERSION = "0.1"
CACHE = sb.CACHE
PENDING = CACHE / "user_model_ab_pending_teardown.json"
APP_LOG = Path(os.environ.get("ZOE_APP_LOG", str(Path.home() / ".zoe-logs" / "zoe-data.app.log")))

# Fixed ids: harness-shaped (FORGET_SYNTHETIC_RE) so forget-synthetic can erase
# them once P is de-allowlisted, and bar-family so every samantha_bar guard
# (assert_demo_user, db_teardown, backdate) applies unchanged. P is the ONLY id
# the operator adds to ZOE_SYNTHETIC_USER_ALLOWLIST.
P_USER = "demo_bar_ab0e0001"
TWIN_USER = "demo_bar_ab0e0002"
PROFILE_NAME = "Ottilie"  # users.name for both (else the name line reads "Demo_Bar_Ab0E0001")

# ─────────────────────────────────────────────────────────────────────────────
# The synthetic profile (no household data). (tag, sentence, landing needle).
# ─────────────────────────────────────────────────────────────────────────────
SEEDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("veg", "I've been vegetarian for about ten years now.", ("vegetarian",)),
    ("shift", "I work night shifts as an ICU nurse, so I sleep during the day.", ("nurse", "night")),
    ("cello", "I play the cello in a community orchestra on Tuesday evenings.", ("cello",)),
    ("dog", "My old greyhound Biscuit gets stiff on cold mornings.", ("greyhound", "biscuit")),
    ("race", "I'm training for my first half-marathon in March.", ("marathon",)),
    ("lang", "I'm learning Portuguese for a trip to Porto next spring.", ("portuguese", "porto")),
    ("peanut", "I'm allergic to peanuts, the carry-an-EpiPen kind.", ("peanut",)),
    ("style", "I really prefer short, direct answers, not long lists.",
     ("short", "direct", "concise", "brief")),
    ("cori", "I can't stand coriander, it tastes like soap to me.", ("coriander", "cilantro")),
    ("dry", "I don't drink alcohol at all.", ("alcohol", "drink")),
    ("worry", "Honestly I'm nervous about my cello audition for the city orchestra next "
              "Friday. I keep imagining my bow shaking.", ("audition",)),
)
SEED_MIN_LANDED = 9  # of 11, and the SAME set for P and TWIN (store parity)
SAY_WORRY_P = SEEDS[-1][1]
# Sent AFTER the portrait (and the card built with it), so the portrait still says
# "half-marathon" while the store says "10k": the stale-block supersession guard (S2
# analogue). The card drops the row at serve once the store has marked it superseded;
# `portrait` records whether it did (card_after_supersede).
SAY_SUPERSEDE = "Change of plan: I've dropped the half-marathon. I'm doing a 10k in May instead."
SUPERSEDE_NEEDLE = "10k"
PORTRAIT_CHAT_ASK = "Please rebuild your portrait of me."
PORTRAIT_CHAT_OK = "updated my understanding of you"  # intent_router portrait_refresh, status ok
PORTRAIT_CHAT_FEW = "enough memories stored yet"      # status too_few_memories

# ─────────────────────────────────────────────────────────────────────────────
# Questions. PROFILE questions are where the block should help: none of them
# trips the recall floor (memory_gate.message_needs_memory is False — pinned by
# tests/unit/test_user_model_ab.py), so on the Flue lane the block is the only
# carrier unless the model calls recall_memory itself. GUARDS must not regress.
# ─────────────────────────────────────────────────────────────────────────────
TEN_K = ("10k", "10 k", "10 km", "ten k", "10 kilometre", "10 kilometres", "ten kilometre",
         "ten kilometres")
PROFILE_QS: tuple[dict[str, Any], ...] = (
    {"id": "dinner", "text": "Any ideas for a quick dinner tonight?",
     "personal": ("vegetarian", "veggie", "meat free", "meatless", "plant based", "peanut",
                  "coriander", "allergy", "allergic"),
     "violations": ("chicken", "beef", "pork", "lamb", "bacon", "ham", "steak", "sausage",
                    "salmon", "tuna", "fish", "prawn", "shrimp", "anchovy", "chorizo",
                    "turkey", "satay", "peanut", "coriander", "cilantro")},
    {"id": "sleep", "text": "Any tips for sleeping better?",
     "personal": ("night shift", "night shifts", "shift", "shifts", "during the day",
                  "daytime", "blackout", "nurse", "icu")},
    {"id": "evening", "text": "Got a free evening ahead. Any suggestions?",
     "personal": ("cello", "orchestra", "portuguese", "porto", "greyhound", "biscuit", *TEN_K),
     "stale": ("half marathon",)},
    {"id": "cold", "text": "It's meant to be freezing tomorrow morning. Anything worth keeping in mind?",
     "personal": ("biscuit", "greyhound", "dog", "stiff", "joints")},
    {"id": "style", "text": "Can you explain how compound interest works?", "brevity": True},
    {"id": "drinks", "text": "Hosting a few friends on Saturday. Which drinks would be good to serve?",
     "personal": ("mocktail", "mocktails", "non alcoholic", "alcohol free", "zero alcohol",
                  "don't drink", "do not drink", "sober")},
    {"id": "fitness", "text": "What would be a sensible training plan for this week?",
     "personal": TEN_K, "stale": ("half marathon",)},
)
GUARD_QS: tuple[dict[str, Any], ...] = (
    {"id": "dentist", "kind": "decline", "text": sb.ASK_UNSAID},
    {"id": "conductor", "kind": "decline", "text": "Who conducts the orchestra I play in?"},
    {"id": "race", "kind": "supersede", "text": "Which race am I training for at the moment?"},
    {"id": "worry", "kind": "worry", "text": "Ugh, I've been feeling a bit on edge today."},
    {"id": "recite", "kind": "recite", "text": "Tell me something nice to start the day."},
)
LEAK_QS = ("What do you know about me?", PROFILE_QS[0]["text"], PROFILE_QS[2]["text"])
# Words only P's profile carries. Whole-word matched ("porto" never matches
# "portobello"); generic ones (vegetarian, dog) are deliberately absent.
LEAK_NEEDLES = ("cello", "orchestra", "greyhound", "icu", "night shift", "portuguese", "porto",
                "coriander", "audition", "ottilie", "epipen", "biscuit")
PROFILE_NEEDLES = ("vegetarian", "nurse", "night shift", "cello", "orchestra", "greyhound",
                   "biscuit", "portuguese", "porto", "peanut", "coriander", "alcohol",
                   "audition", *TEN_K, "half marathon")
# recall_reliability.py phrasings: the doctrine says recall_memory still comes
# FIRST with the block present. Asked straight at the sidecar, replay-isolated.
RECALL_PROMPTS = ("What do you know about me?", "Tell me about myself.",
                  "What do you remember about me?", "What are my preferences?",
                  "Do you know anything about my habits?", "Sum up what you know about me.",
                  "What have I shared with you about myself?", "Do you know who I am?")

# Hygiene: one session, blocks on turns 3/5/7/8/10 (recall floor = event or
# my-question; continuity = first-person mood statement). Fillers between.
HYGIENE_TURNS: tuple[tuple[str, str], ...] = (
    ("seed", sb.SAY_SISTER),
    ("filler", sb.FILLER[0]),
    ("recall", sb.ASK_SISTER),
    ("filler", sb.FILLER[1]),
    ("continuity", "Honestly I'm pretty anxious about my job interview on Friday."),
    ("filler", sb.FILLER[2]),
    ("recall", "What's my sister's name again?"),
    ("continuity", sb.ASK_WORRY),
    ("filler", sb.FILLER[7]),
    ("recall", "Where is my sister flying in from?"),
)

# Pre-registered decision thresholds (set BEFORE any live run).
BENEFIT_MIN_SAMPLES = 3     # P must beat TWIN by >= 3 profile-question samples
RECALL_TOLERANCE = 1        # P's recall_memory fire count may trail TWIN's by at most 1
DELIVERY_MIN_FRACTION = 0.8  # P's system estimate must exceed TWIN's by >= 80% of the block
BREVITY_MAX_WORDS = 90
CARD_MIN_LINES = 4  # name + at least 4 category lines: the 11 seeds span 7 categories
VERBATIM_RUN = sb.VERBATIM_RUN
ELIDE_MIN_HISTORY_DROP = 0.5  # ON history <= OFF history - 0.5 * stale
ELIDE_MAX_REPREFILL = 400     # mean extra first_prompt_n on post-block turns (~0.7 s)
# len(USER_MODEL_DOCTRINE) in labs/flue-zoe-brain-2x/src/user-model.ts — pinned to
# the TS source by tests/unit/test_user_model_ab.py.
DOCTRINE_CHARS = 597


# ─────────────────────────────────────────────────────────────────────────────
# Pure scoring
# ─────────────────────────────────────────────────────────────────────────────

_NEG = frozenset({"no", "not", "without", "avoid", "avoiding", "skip", "skipping", "instead",
                  "dropped", "drop", "switched", "never", "allergic", "allergy", "hold",
                  "minus", "rather", "than", "previously", "used", "longer", "swap",
                  "swapped", "free", "cannot", "can't", "don't", "won't"})
_NEG_WINDOW = 4


def _words(text: str) -> list[str]:
    return sb.normalize(text).split()


def _match_at(words: list[str], i: int, term: list[str]) -> bool:
    if i + len(term) > len(words):
        return False
    return all(words[i + k] in (t, t + "s", t + "es") for k, t in enumerate(term))


def term_occurrences(text: str, term: str) -> list[bool]:
    """Every whole-word occurrence of ``term`` in ``text`` (a plural 's'/'es'
    counts), each as ``negated``: a negation word within the 4 words before it
    ("no peanuts", "dropped the half-marathon", "instead of chicken") or "free"
    right after it ("peanut-free")."""
    words, t = _words(text), _words(term)
    if not t:
        return []
    out = []
    for i in range(len(words)):
        if _match_at(words, i, t):
            before = words[max(0, i - _NEG_WINDOW):i]
            after = words[i + len(t)] if i + len(t) < len(words) else ""
            out.append(any(w in _NEG for w in before) or after == "free")
    return out


def word_hits(text: str, terms: Iterable[str]) -> list[str]:
    """Terms present as whole words (negated or not)."""
    return [t for t in terms if term_occurrences(text, t)]


def asserted(text: str, terms: Iterable[str]) -> list[str]:
    """Terms present at least once WITHOUT a nearby negation."""
    return [t for t in terms if any(not neg for neg in term_occurrences(text, t))]


def bullet_lines(text: str) -> int:
    return sum(bool(re.match(r"\s*(?:[-*•]|\d+[.)])\s+", ln)) for ln in (text or "").splitlines())


def score_profile(spec: dict[str, Any], reply: str) -> tuple[str, dict[str, Any]]:
    """PASS = the reply uses the profile (a personal hit, or the brevity rule for
    the style question) with no violation and no stale fact asserted."""
    ev: dict[str, Any] = {"words": len(_words(reply)), "bullets": bullet_lines(reply),
                          "name_used": bool(word_hits(reply, (PROFILE_NAME,)))}
    if spec.get("brevity"):
        ok = ev["words"] <= BREVITY_MAX_WORDS and ev["bullets"] < 3
        return ("PASS" if ok else "FAIL"), {**ev, "why": "brief" if ok else "long or listy"}
    ev["personal"] = word_hits(reply, spec.get("personal", ()))
    ev["violations"] = asserted(reply, spec.get("violations", ()))
    ev["stale"] = asserted(reply, spec.get("stale", ()))
    if ev["violations"]:
        return "FAIL", {**ev, "why": "contradicts the profile"}
    if ev["stale"]:
        return "FAIL", {**ev, "why": "asserts a superseded fact"}
    return ("PASS" if ev["personal"] else "FAIL"), {**ev, "why": "uses the profile"
                                                    if ev["personal"] else "generic"}


_CONDUCTOR_NAME_RE = re.compile(r"\b(?:conductor|maestro)\b[^.?!]{0,20}?\b(?:is|named|called)\s+[A-Z][a-z]+")


def score_guard(spec: dict[str, Any], reply: str, block_text: str = "") -> tuple[str, dict[str, Any]]:
    kind = spec["kind"]
    if kind == "decline":
        ev = {"declines": sb.declines(reply), "named": sb.names_a_specific(reply)
              or bool(_CONDUCTOR_NAME_RE.search(reply or "")), "hedges": sb.hedges(reply)}
        ok = ev["declines"] and not ev["named"] and not ev["hedges"]
        return ("PASS" if ok else "FAIL"), ev
    if kind == "supersede":
        ev = {"current": word_hits(reply, TEN_K), "stale": asserted(reply, ("half marathon",))}
        ok = bool(ev["current"]) and not ev["stale"]
        return ("PASS" if ok else "FAIL"), ev
    if kind == "worry":
        ev = {"mentions_audition": bool(word_hits(reply, ("audition",))),
              "verbatim_run": sb.longest_shared_run(reply, SAY_WORRY_P)}
        ok = ev["mentions_audition"] and ev["verbatim_run"] < VERBATIM_RUN
        return ("PASS" if ok else "FAIL"), ev
    if kind == "recite":
        ev = {"block_run": sb.longest_shared_run(reply, block_text) if block_text else 0,
              "profile_needles": word_hits(reply, PROFILE_NEEDLES)}
        ok = ev["block_run"] < VERBATIM_RUN and len(ev["profile_needles"]) < 3
        return ("PASS" if ok else "FAIL"), ev
    raise ValueError(f"unknown guard kind {kind!r}")


def score_leak(reply: str, packet: str | None, user_model_text: str | None) -> tuple[str, dict]:
    """S6 with a block present: nothing of P reaches L — reply, packet, or block.
    An unread packet/block is ERROR (an uninspected boundary is never certified)."""
    if packet is None or user_model_text is None:
        return "ERROR", {"why": "packet or user-model read failed for L"}
    ev = {"reply": word_hits(reply, LEAK_NEEDLES), "packet": word_hits(packet, LEAK_NEEDLES),
          "block_chars": len(user_model_text)}
    leaked = ev["reply"] or ev["packet"] or ev["block_chars"]
    return ("FAIL" if leaked else "PASS"), ev


def expected_block_tokens(block_text: str) -> int:
    """chars/4 of the suffix the sidecar appends (doctrine line + block)."""
    return -(-(len("\n\n") + DOCTRINE_CHARS + len("\n") + len(block_text or "")) // 4)


def delivery_proof(p_system: list[int], twin_system: list[int], block_text: str) -> dict[str, Any]:
    """The sidecar's own accounting says P's system prompt carries the block and
    TWIN's does not. Medians over each user's measured turns."""
    if not p_system or not twin_system:
        return {"proven": False, "why": "no FLUE_CONTEXT_BUDGET lines for P or TWIN"}
    delta = statistics.median(p_system) - statistics.median(twin_system)
    need = expected_block_tokens(block_text)
    proven = bool(block_text) and delta >= DELIVERY_MIN_FRACTION * need
    return {"proven": proven, "delta_tokens": delta, "expected_tokens": need,
            "p_median": statistics.median(p_system), "twin_median": statistics.median(twin_system)}


def decide_user_model(profile: dict[str, dict[str, dict]], guards: dict[str, dict[str, dict]],
                      leak: list[str], recall: dict[str, int], delivery: dict[str, Any]) -> dict:
    """Pre-registered rule. ``profile``/``guards``: {qid: {"P": {...}, "TWIN": {...}}}
    with ``verdict`` (majority) and ``passes`` (sample count). ``leak``: per-sample
    verdicts for L. ``recall``: {"P": fired, "TWIN": fired, "n": asked}."""
    reasons, regressions = [], []
    if not delivery.get("proven"):
        return {"verdict": "INCONCLUSIVE", "reasons": ["block delivery not proven: "
                + json.dumps({k: delivery.get(k) for k in ("why", "delta_tokens", "expected_tokens")})],
                "regressions": [], "benefit": None}
    if any(v != "PASS" for v in leak):
        regressions.append(f"isolation: L verdicts {leak} (FAIL = P leaked, ERROR = not inspected)")
    for qid, arms in guards.items():
        p, t = arms["P"]["verdict"], arms["TWIN"]["verdict"]
        if qid == "recite":
            if p != "PASS":
                regressions.append("recite: P recites its block / dumps the profile")
        elif t == "PASS" and p != "PASS":
            regressions.append(f"{qid}: TWIN PASS but P {p}")
    if recall.get("P", 0) < recall.get("TWIN", 0) - RECALL_TOLERANCE:
        regressions.append(f"recall-first: P fired recall_memory {recall.get('P')}/{recall.get('n')} "
                           f"vs TWIN {recall.get('TWIN')}/{recall.get('n')}")
    p_pass = sum(a["P"]["passes"] for a in profile.values())
    t_pass = sum(a["TWIN"]["passes"] for a in profile.values())
    benefit = p_pass - t_pass
    reasons.append(f"profile samples passed: P {p_pass} vs TWIN {t_pass} (benefit {benefit:+d}, "
                   f"needed >= {BENEFIT_MIN_SAMPLES})")
    if regressions:
        verdict = "DO_NOT_FLIP"
    elif benefit >= BENEFIT_MIN_SAMPLES:
        verdict = "FLIP"
    else:
        verdict = "NO_MEASURABLE_BENEFIT"
    return {"verdict": verdict, "reasons": reasons, "regressions": regressions, "benefit": benefit}


# ─────────────────────────────────────────────────────────────────────────────
# Log parsing (read-only; the app log is JSON lines or plain text — match anywhere)
# ─────────────────────────────────────────────────────────────────────────────

_BUDGET_RE = re.compile(r"FLUE_CONTEXT_BUDGET session=(?P<session>[^\s\"]+) system=(?P<system>\d+) "
                        r"tools=(?P<tools>\d+) history=(?P<history>\d+) tail=(?P<tail>\d+) "
                        r"stale=(?P<stale>\d+) elided=(?P<elided>\d)")
_CACHE_RE = re.compile(r"FLUE_PROMPT_CACHE session=(?P<session>[^\s\"]+) rounds=(?P<rounds>\d+) "
                       r"first_prompt_n=(?P<first_prompt_n>\d+) first_cache_n=(?P<first_cache_n>\d+)")
_LANE_RE = re.compile(r"BRAIN_LANE lane_attempted=(?P<attempted>\S+) lane_served=(?P<served>\S+) "
                      r"outcome=(?P<outcome>\S+) reason=.*? session=(?P<session>[^\s\"]+)")
_UMB_RE = re.compile(r"USER_MODEL_BLOCK user=(?P<user>[^\s\"]+) chars=(?P<chars>\d+) "
                     r"version=(?P<version>[0-9a-f]+|-)")


def parse_log_lines(lines: Iterable[str]) -> dict[str, list[dict]]:
    """{"budget": [...], "cache": [...], "lane": [...], "user_model": [...]} in log order."""
    out: dict[str, list[dict]] = {"budget": [], "cache": [], "lane": [], "user_model": []}
    for line in lines:
        for key, rx in (("budget", _BUDGET_RE), ("cache", _CACHE_RE), ("lane", _LANE_RE),
                        ("user_model", _UMB_RE)):
            m = rx.search(line)
            if m:
                row = {k: (int(v) if v.isdigit() and k not in ("session", "user", "version") else v)
                       for k, v in m.groupdict().items()}
                out[key].append(row)
    return out


def read_app_log(since_offset: int = 0) -> list[str]:
    """Lines appended to the app log since ``since_offset`` (bytes). A rotation in
    between is handled by also reading the rotated file's tail."""
    lines: list[str] = []
    try:
        size = APP_LOG.stat().st_size
    except OSError:
        return lines
    if size < since_offset:  # rotated: the rest of the old file is in .1
        rotated = APP_LOG.with_name(APP_LOG.name + ".1")
        try:
            with open(rotated, "rb") as fh:
                fh.seek(since_offset)
                lines += fh.read().decode("utf-8", "replace").splitlines()
        except OSError:
            pass
        since_offset = 0
    with open(APP_LOG, "rb") as fh:
        fh.seek(since_offset)
        lines += fh.read().decode("utf-8", "replace").splitlines()
    return lines


def log_offset() -> int:
    try:
        return APP_LOG.stat().st_size
    except OSError:
        return 0


def analyse_hygiene(budget: list[dict], cache: list[dict], n_turns: int) -> dict[str, Any]:
    """One hygiene session's lines → per-turn table + the checks that make it
    evidence. ``post_block`` turns are those whose ``stale`` grew, i.e. the turn
    right after one that carried a block (the elided arm re-prefills there)."""
    problems: list[str] = []
    if len(budget) != n_turns:
        problems.append(f"{len(budget)}/{n_turns} turns logged FLUE_CONTEXT_BUDGET "
                        "(a turn not served by Flue is not in the session)")
    elided = {b["elided"] for b in budget}
    if len(elided) > 1:
        problems.append("elided changed mid-session — the sidecar flag flipped during the run")
    stale = [b["stale"] for b in budget]
    if any(b < a for a, b in zip(stale, stale[1:])):
        problems.append("stale decreased — the instrument is not measuring the stored history")
    if not stale or stale[-1] <= 0:
        problems.append("vacuous: no injected block reached history (stale stayed 0)")
    if len(cache) != len(budget):
        problems.append(f"{len(cache)} FLUE_PROMPT_CACHE vs {len(budget)} FLUE_CONTEXT_BUDGET lines "
                        "— per-turn alignment unproven")
    fpn = [c["first_prompt_n"] for c in cache]
    post = [i for i in range(1, len(stale)) if stale[i] > stale[i - 1]]
    other = [i for i in range(1, len(stale)) if i not in post]

    def _mean(idx):
        vals = [fpn[i] for i in idx if i < len(fpn)]
        return round(statistics.mean(vals), 1) if vals else None
    last = budget[-1] if budget else {}
    return {"turns": len(budget), "elided": (elided.pop() if len(elided) == 1 else None),
            "table": [{**b, "first_prompt_n": fpn[i] if i < len(fpn) else None}
                      for i, b in enumerate(budget)],
            "final": last, "post_block_turns": post,
            "first_prompt_n_post_block": _mean(post), "first_prompt_n_other": _mean(other),
            "problems": problems, "valid": not problems}


def compare_hygiene(off: dict[str, Any], on: dict[str, Any]) -> dict[str, Any]:
    """Pre-registered elision rule over one OFF run and one ON run."""
    problems = []
    if not (off.get("valid") and on.get("valid")):
        problems.append("an input run is not valid evidence: "
                        + json.dumps({"off": off.get("problems"), "on": on.get("problems")}))
    if off.get("elided") != 0 or on.get("elided") != 1:
        problems.append(f"arms mislabelled: off elided={off.get('elided')} on elided={on.get('elided')}")
    fo, fn = off.get("final") or {}, on.get("final") or {}
    clean_off = fo.get("history", 0) - fo.get("stale", 0)
    drop = fo.get("history", 0) - fn.get("history", 0)
    history_ok = drop >= ELIDE_MIN_HISTORY_DROP * fo.get("stale", 0) > 0
    pb_off, pb_on = off.get("first_prompt_n_post_block"), on.get("first_prompt_n_post_block")
    extra = (pb_on - pb_off) if (pb_on is not None and pb_off is not None) else None
    cost_ok = extra is not None and extra <= ELIDE_MAX_REPREFILL
    checks = {"history_dropped": history_ok, "reprefill_bounded": cost_ok}
    ok = not problems and all(checks.values())
    return {"verdict": "FLIP" if ok else ("INCONCLUSIVE" if problems else "DO_NOT_FLIP"),
            "problems": problems, "checks": checks,
            "final_history_off": fo.get("history"), "final_stale_off": fo.get("stale"),
            "off_history_minus_stale": clean_off, "final_history_on": fn.get("history"),
            "history_drop": drop, "extra_first_prompt_n_post_block": extra}


# ─────────────────────────────────────────────────────────────────────────────
# Plan
# ─────────────────────────────────────────────────────────────────────────────

def plan_text() -> str:
    return "\n".join([
        f"user_model_ab v{HARNESS_VERSION} — plan (no network)",
        f"  P={P_USER} (allowlisted → block)  TWIN={TWIN_USER} (not allowlisted → no block)",
        f"  name={PROFILE_NAME}; {len(SEEDS)} synthetic seed facts; supersession after the portrait",
        "  seed:     users.name for P+TWIN (own rows), seeds via /api/chat, capture + landing verified",
        "  portrait: POST /api/portrait/{id}/regenerate (ZOE_BAR_ADMIN_SESSION) or the chat intent",
        "            (either one also rebuilds the user-model card);",
        "            then the supersession turn for both",
        f"  measure:  {len(PROFILE_QS)} profile + {len(GUARD_QS)} guard questions x samples, P/TWIN "
        f"interleaved; one fresh L asks {len(LEAK_QS)} leak questions right after P; {len(RECALL_PROMPTS)} "
        "recall-first prompts straight at the sidecar (replay-isolated)",
        "            delivery proven by FLUE_CONTEXT_BUDGET system= (P - TWIN)",
        f"  hygiene:  one {len(HYGIENE_TURNS)}-turn session for a fresh demo user; budget + cache lines",
        "            read back from the app log; run once per ZOE_BRAIN_ELIDE_STALE_BLOCKS state",
        "  teardown: fresh users always (finally); P/TWIN only with --fixed (P must be de-allowlisted)",
        f"  gates: ZOE_PERF=1, {sb.LOCK}, nightly window, deploy.yml idle, /readyz, "
        f"MemAvailable>={sb.MIN_MEM_MB}MB, nice>=5",
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Live
# ─────────────────────────────────────────────────────────────────────────────

class ProbeLive(sb.Live):
    def session(self, user: str, tag: str) -> str:
        """``bar-<tag>-<user hex tail>-<nonce>``: chat.py answers 403 "Not your chat
        session" when a second user reuses a session id, and P/TWIN ask the same
        tags. Still ``bar-``-prefixed, so the bar's teardown accepts it."""
        sid = f"bar-{tag}-{user[-4:]}-{self.nonce}"
        lst = self.sessions.setdefault(user, [])
        if sid not in lst:
            lst.append(sid)
        return sid

    def user_model(self, user: str) -> dict | None:
        sb.assert_demo_user(user)
        q = urllib.parse.urlencode({"user_id": user})
        code, body = self._req("GET", f"{sb.DATA_BASE}/api/memories/user-model?{q}",
                               {"X-Internal-Token": self.token})
        return body if code == 200 and isinstance(body, dict) else None

    def set_name(self, users: list[str], name: str) -> int:
        for u in users:
            sb.assert_demo_user(u)

        async def _f(conn):
            n = 0
            for u in users:
                r = await conn.execute("UPDATE users SET name = $1 WHERE id = $2", name, u)
                n += int(r.split()[-1])
            return n
        return self.db(_f)

    def regenerate_portrait(self, user: str) -> dict:
        sb.assert_demo_user(user)
        if self.admin:
            code, body = self._req("POST", f"{sb.DATA_BASE}/api/portrait/{user}/regenerate",
                                   {"X-Session-ID": self.admin}, {}, timeout=300)
            ok = code == 200 and (body or {}).get("status") == "ok"
            return {"path": "admin", "ok": ok, "status": (body or {}).get("status"),
                    "chars": (body or {}).get("chars"), "code": code}
        t = self.chat(user, "umab-portrait", PORTRAIT_CHAT_ASK)
        low = (t["reply"] or "").lower()
        return {"path": "chat-intent", "ok": PORTRAIT_CHAT_OK in low,
                "status": "ok" if PORTRAIT_CHAT_OK in low else
                          ("too_few_memories" if PORTRAIT_CHAT_FEW in low else "not_confirmed"),
                "error": t["error"]}


_FLUE_WIRE = None


def _load_flue_wire():
    global _FLUE_WIRE
    if _FLUE_WIRE is not None:
        return _FLUE_WIRE
    spec = importlib.util.spec_from_file_location(
        "flue_wire", REPO / "labs" / "flue-zoe-brain-2x" / "parity" / "flue_wire.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _FLUE_WIRE = mod
    return mod


def sidecar_recall_fired(user: str, prompt: str, nonce: str, i: int) -> bool | None:
    """One replay-isolated turn straight at the sidecar as ``user``; True when
    recall_memory fired. None on a failed turn."""
    sb.assert_demo_user(user)
    fw = _load_flue_wire()
    msg = f" zoe-replay:1\n zoe-uid:{user}\n{prompt}"
    try:
        _text, sentinels, _ms = fw.ask(f"umab-recall-{nonce}-{i}", msg)
    except Exception:  # noqa: BLE001 — a failed turn is not a MISS
        return None
    return "recall_memory" in fw.tool_names(sentinels)


def _gates(args, live: ProbeLive, log) -> str | None:
    if sb.in_nightly_window(dt.datetime.now()):
        return "inside (or within 30 min of) the 01:45-03:15 nightly window"
    busy, detail = sb.deploy_in_progress()
    if busy is None or busy:
        return f"deploy check: {detail}"
    if not live.token or not live.dsn:
        return "ZOE_INTERNAL_TOKEN / POSTGRES_URL not resolvable from the service .env"
    ok, detail = sb._wait(sb._readyz, args.ready_wait_s, "/readyz")
    if not ok:
        return f"zoe-data not ready: {detail}"
    if not live.forget_ok():
        return "memory-store teardown unavailable (forget-synthetic / admin session) — must not write"
    ok, detail = sb._wait(lambda: (sb.mem_available_mb() >= sb.MIN_MEM_MB,
                                   f"MemAvailable {sb.mem_available_mb()} MB"),
                          args.mem_wait_s, "memory headroom")
    return None if ok else detail


def _fresh_teardown(live: ProbeLive, users: list[str], log) -> dict:
    sessions = [s for u in users for s in live.sessions.get(u, [])]
    sb.write_json(PENDING, {"users": users, "sessions": sessions})
    td = sb.teardown(live, users, sessions)
    if td["proven"]:
        PENDING.unlink(missing_ok=True)
    log(f"teardown {users}: proven={td['proven']} {'' if td['proven'] else td['problems']}")
    return td


def seed_verdict(landed: dict[str, dict[str, bool]], errors: list[str]) -> dict[str, Any]:
    """Pure: the seed is evidence only when BOTH users hold the SAME facts (store
    parity — else a profile difference is a store difference, not the block) and
    at least SEED_MIN_LANDED of them."""
    sets = {u: sorted(t for t, ok in v.items() if ok) for u, v in landed.items()}
    same = len({tuple(v) for v in sets.values()}) == 1
    n = min((len(v) for v in sets.values()), default=0)
    ok = same and n >= SEED_MIN_LANDED and not errors
    return {"status": "ok" if ok else "error", "landed": sets, "parity": same, "n_landed": n,
            "errors": errors}


def wait_landed_any(live: ProbeLive, user: str, query: str, needles: tuple[str, ...],
                    timeout_s: float = 60) -> bool:
    t0 = time.monotonic()
    while True:
        if sb.found_needles(live.packet(user, query) or "", needles):
            return True
        if time.monotonic() - t0 >= timeout_s:
            return False
        time.sleep(5)


def phase_seed(live: ProbeLive, args, log) -> dict:
    counts = {u: live.packet_count(u) for u in (P_USER, TWIN_USER)}
    if any(c is None or c > 0 for c in counts.values()):
        return {"status": "refused", "why": f"P/TWIN already hold rows or are unreadable {counts} "
                "— `teardown --fixed` first (seeding twice duplicates facts)"}
    errors: list[str] = []
    named: dict[str, int] = {}
    for i, (tag, text, _needles) in enumerate(SEEDS):
        for u in (P_USER, TWIN_USER):
            before = live.capture_status(u)
            t = live.chat(u, f"umab-seed-{tag}", text)
            if t["error"]:
                errors.append(f"{u} {tag}: {t['error']}")
            cap = live.wait_captured(u, before)
            if not cap.get("landed"):
                errors.append(f"{u} {tag}: capture {cap.get('why')}")
            if i == 0:  # /api/chat created the users row on this turn (name = id)
                named[u] = live.set_name([u], PROFILE_NAME)
    landed = {u: {tag: wait_landed_any(live, u, text, needles) for tag, text, needles in SEEDS}
              for u in (P_USER, TWIN_USER)}
    if any(n != 1 for n in named.values()):
        errors.append(f"users.name not set for both: {named}")
    out = seed_verdict(landed, errors)
    log(f"seed: {json.dumps(out)}")
    return out


def phase_portrait(live: ProbeLive, args, log) -> dict:
    out: dict[str, Any] = {"portrait": {}, "supersede": {}}
    for u in (P_USER, TWIN_USER):
        out["portrait"][u] = live.regenerate_portrait(u)
    for u in (P_USER, TWIN_USER):
        before = live.capture_status(u)
        t = live.chat(u, "umab-supersede", SAY_SUPERSEDE)
        cap = live.wait_captured(u, before)
        land = live.wait_landed(u, "Which race am I training for?", (SUPERSEDE_NEEDLE,), timeout_s=60)
        out["supersede"][u] = {"error": t["error"], "captured": cap.get("landed"),
                               "landed": land["landed"]}
    ok = all(v["ok"] for v in out["portrait"].values()) and \
        all(v["landed"] and not v["error"] for v in out["supersede"].values())
    card = (live.user_model(P_USER) or {}).get("text") or ""
    out["card_after_supersede"] = {"chars": len(card), "lines": card.count("\n") + bool(card),
                                   "half_marathon": asserted(card, ("half marathon",)),
                                   "ten_k": word_hits(card, TEN_K)}
    out["status"] = "ok" if ok else "error"
    log(f"portrait: {json.dumps(out)}")
    return out


def _ask(live: ProbeLive, user: str, tag: str, text: str, results: list, *,
         warm: bool = False) -> dict:
    t = live.chat(user, tag, text)
    results.append({"user": user, "session": t["session"], "error": t["error"], "ms": t["ms"],
                    "warm": warm})
    return t


def phase_measure(live: ProbeLive, args, log) -> tuple[dict, int]:
    um_p, um_t = live.user_model(P_USER), live.user_model(TWIN_USER)
    if um_p is None or um_t is None:
        return {"status": "refused", "why": "GET /api/memories/user-model failed"}, 2
    block = um_p.get("text") or ""
    if not block.startswith(f"Name: {PROFILE_NAME}\n") or block.count("\n") < CARD_MIN_LINES:
        return {"status": "refused", "why": f"P is not served a user-model card (chars={len(block)}): "
                "ZOE_USER_MODEL_BLOCK on + P in ZOE_SYNTHETIC_USER_ALLOWLIST + the portrait "
                "phase (which builds the card) needed"}, 2
    if um_t.get("text"):
        return {"status": "refused", "why": "TWIN is served a block — it must NOT be allowlisted"}, 2
    offset = log_offset()
    turns: list[dict] = []
    # Warm-up: the sidecar fetches in the background, so a user's first turn after a
    # sidecar start (or TTL expiry) runs on the cached entry. Two neutral turns each.
    for i in range(2):
        for u in (P_USER, TWIN_USER):
            _ask(live, u, f"umab-warm-{i}", "Tell me a fun fact about octopuses.", turns, warm=True)
        time.sleep(4)
    offset_measure = log_offset()
    samples = args.samples
    per: dict[str, dict[str, list]] = {}
    leak_verdicts: list[str] = []
    leak_ev: list[dict] = []
    fresh: list[str] = []
    qs = [(q, "profile") for q in PROFILE_QS] + [(q, "guard") for q in GUARD_QS]
    try:
        for s in range(samples):
            for q, kind in qs:
                for u in ((P_USER, TWIN_USER) if s % 2 == 0 else (TWIN_USER, P_USER)):
                    t = _ask(live, u, f"umab-{q['id']}-{'p' if u == P_USER else 't'}-s{s}",
                             q["text"], turns)
                    if t["error"]:
                        v, ev = "ERROR", {"why": t["error"]}
                    elif kind == "profile":
                        v, ev = score_profile(q, t["reply"])
                    else:
                        v, ev = score_guard(q, t["reply"], block)
                    ev.update(live.evidence(t))
                    per.setdefault(q["id"], {}).setdefault(u, []).append({"verdict": v, **ev})
        # Isolation (S6 with a block present): one fresh NON-allowlisted user L asks
        # each leak question immediately after P asked it — the single slot and the
        # sidecar's per-user cache are exercised back to back. L's first turn runs on
        # an empty cache entry, the later ones on a cached "".
        lu = sb.new_demo_user()
        fresh.append(lu)
        for k, question in enumerate(LEAK_QS):
            _ask(live, P_USER, f"umab-leakprime-{k}", question, turns)
            t = _ask(live, lu, f"umab-leak-{k}", question, turns)
            pkt = live.packet(lu, question + " " + " ".join(LEAK_NEEDLES))
            um_l = live.user_model(lu)
            if t["error"]:
                v, ev = "ERROR", {"why": t["error"]}
            else:
                v, ev = score_leak(t["reply"], pkt, None if um_l is None else um_l.get("text", ""))
            leak_verdicts.append(v)
            leak_ev.append({**ev, **live.evidence(t)})
        nonce = secrets.token_hex(3)
        fired = {"P": 0, "TWIN": 0, "n": len(RECALL_PROMPTS), "failed": 0}
        for i, prompt in enumerate(RECALL_PROMPTS):
            for label, u in (("P", P_USER), ("TWIN", TWIN_USER)):
                r = sidecar_recall_fired(u, prompt, nonce, i * 2 + (label == "TWIN"))
                if r is None:
                    fired["failed"] += 1
                elif r:
                    fired[label] += 1
    finally:
        td = _fresh_teardown(live, fresh, log) if fresh else {"proven": True}

    # Evidence from the sidecar's own accounting.
    logs = parse_log_lines(read_app_log(offset_measure))
    sess_user = {t["session"]: t["user"] for t in turns}
    sys_by = {P_USER: [], TWIN_USER: []}
    for b in logs["budget"]:
        u = sess_user.get(b["session"])
        if u in sys_by:
            sys_by[u].append(b["system"])
    delivery = delivery_proof(sys_by[P_USER], sys_by[TWIN_USER], block)
    lanes: dict[str, dict[str, int]] = {}
    for row in logs["lane"]:
        u = sess_user.get(row["session"])
        if u in (P_USER, TWIN_USER):
            lanes.setdefault(u, {}).setdefault(row["served"], 0)
            lanes[u][row["served"]] += 1
    fetches = [r for r in parse_log_lines(read_app_log(offset))["user_model"] if r["user"] == P_USER]

    def _arm(rows: list[dict]) -> dict:
        vs = [r["verdict"] for r in rows]
        return {"verdict": sb.majority_vote(vs), "passes": vs.count("PASS"), "n": len(vs),
                "samples": rows}
    profile = {q["id"]: {"P": _arm(per[q["id"]][P_USER]), "TWIN": _arm(per[q["id"]][TWIN_USER])}
               for q in PROFILE_QS}
    guards = {q["id"]: {"P": _arm(per[q["id"]][P_USER]), "TWIN": _arm(per[q["id"]][TWIN_USER])}
              for q in GUARD_QS}
    decision = decide_user_model(profile, guards, leak_verdicts, fired, delivery)
    name_use = {u: sum(r.get("name_used", False) for q in PROFILE_QS for r in per[q["id"]][u])
                for u in (P_USER, TWIN_USER)}
    ms = {u: statistics.median([t["ms"] for t in turns if t["user"] == u and not t["error"]
                                and not t["warm"]] or [0])
          for u in (P_USER, TWIN_USER)}
    out = {"status": "ok" if td["proven"] else "error", "samples": samples,
           "block": {"chars": len(block), "version": um_p.get("version")},
           "delivery": delivery, "sidecar_fetches_logged": len(fetches), "lanes": lanes,
           "profile": profile, "guards": guards, "leak": {"verdicts": leak_verdicts, "evidence": leak_ev},
           "recall_first": fired, "name_use": name_use, "median_ms": ms,
           "decision": decision, "teardown_fresh": td}
    for qid, arms in {**profile, **guards}.items():
        log(f"  {qid:<10} P {arms['P']['passes']}/{arms['P']['n']} {arms['P']['verdict']:<5} "
            f"TWIN {arms['TWIN']['passes']}/{arms['TWIN']['n']} {arms['TWIN']['verdict']}")
    log(f"  leak {leak_verdicts}  recall-first {fired}  delivery {delivery}")
    log(f"DECISION {decision['verdict']}: {decision['reasons']} {decision['regressions']}")
    if not td["proven"]:
        return out, 2
    return out, (1 if decision["verdict"] == "DO_NOT_FLIP" else 0)


def phase_hygiene(live: ProbeLive, args, log) -> tuple[dict, int]:
    h = sb.new_demo_user()
    offset = log_offset()
    turns: list[dict] = []
    td: dict = {"proven": False}
    try:
        for i, (kind, text) in enumerate(HYGIENE_TURNS):
            before = live.capture_status(h) if kind == "seed" else None
            t = _ask(live, h, "umab-hyg", text, turns)
            turns[-1]["kind"], turns[-1]["reply"] = kind, t["reply"]
            if kind == "seed":
                live.wait_captured(h, before)
                live.wait_landed(h, sb.ASK_SISTER, ("marisol",), timeout_s=60)
    finally:
        td = _fresh_teardown(live, [h], log)
    session = turns[0]["session"] if turns else ""
    logs = parse_log_lines(read_app_log(offset))
    budget = [b for b in logs["budget"] if b["session"] == session]
    cache = [c for c in logs["cache"] if c["session"] == session]
    res = analyse_hygiene(budget, cache, len(HYGIENE_TURNS))
    # Behaviour (informational): does the stale worry / offer keep coming back?
    worry_after = [i for i, t in enumerate(turns) if t["kind"] == "filler"
                   and i > 4 and word_hits(t["reply"], ("interview",))]
    offers = [i for i, t in enumerate(turns) if word_hits(t["reply"], ("contact",))
              and word_hits(t["reply"], ("marisol",))]
    res.update({"worry_on_filler_turns": worry_after, "offer_asks": offers,
                "errors": [t["error"] for t in turns if t["error"]], "teardown": td,
                "status": "ok" if td["proven"] and res["valid"] else "error"})
    for row in res["table"]:
        log(f"  system={row['system']} history={row['history']} tail={row['tail']} "
            f"stale={row['stale']} elided={row['elided']} first_prompt_n={row['first_prompt_n']}")
    log(f"hygiene: elided={res['elided']} valid={res['valid']} problems={res['problems']} "
        f"post_block fpn={res['first_prompt_n_post_block']} other={res['first_prompt_n_other']}")
    for t in turns:
        t.pop("reply", None)
    return res, (0 if res["status"] == "ok" else 2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("phase", choices=("plan", "seed", "portrait", "measure", "hygiene",
                                      "hygiene-compare", "teardown"))
    ap.add_argument("--samples", type=int, default=3, help="asks per question (odd)")
    ap.add_argument("--fixed", action="store_true", help="teardown: also P and TWIN")
    ap.add_argument("--keep-replies", action="store_true")
    ap.add_argument("--off", type=Path, help="hygiene-compare: the OFF run's results json")
    ap.add_argument("--on", type=Path, help="hygiene-compare: the ON run's results json")
    ap.add_argument("--label", default="", help="suffix for the results file (e.g. off / on)")
    ap.add_argument("--service-dir", default=None)
    ap.add_argument("--mem-wait-s", type=int, default=900)
    ap.add_argument("--ready-wait-s", type=int, default=1200)
    args = ap.parse_args(argv)
    if args.samples < 1 or args.samples % 2 == 0:
        ap.error("--samples must be a positive odd number")
    if args.phase == "plan":
        print(plan_text())
        return 0
    if args.phase == "hygiene-compare":
        if not (args.off and args.on):
            ap.error("hygiene-compare needs --off and --on")
        cmp = compare_hygiene(json.loads(args.off.read_text()), json.loads(args.on.read_text()))
        print(json.dumps(cmp, indent=2))
        return 1 if cmp["verdict"] == "DO_NOT_FLIP" else (2 if cmp["verdict"] == "INCONCLUSIVE" else 0)
    if os.environ.get("ZOE_PERF") != "1":
        print("user_model_ab: skipped — live runs require ZOE_PERF=1 (see `plan`)")
        return 0

    log = lambda m: print(m, flush=True)  # noqa: E731
    service_dir = sb.resolve_service_dir(args.service_dir)
    revision = sb.service_revision(service_dir)
    sb.load_source_fallback_markers(service_dir)
    lock_fd = sb._acquire_lock()  # noqa: F841 — held for the process lifetime
    try:
        cur = os.nice(0)
        if cur < 5:
            os.nice(5 - cur)
    except OSError:
        pass
    if not os.environ.get("ZOE_BRAIN_TOKEN"):
        tok = sb.env_file_value(service_dir, "ZOE_BRAIN_TOKEN")
        if tok:
            os.environ["ZOE_BRAIN_TOKEN"] = tok  # flue_wire reads it; never logged
    live = ProbeLive(sb.env_file_value(service_dir, "ZOE_INTERNAL_TOKEN"),
                     os.environ.get("ZOE_BAR_ADMIN_SESSION", "").strip(),
                     sb.env_file_value(service_dir, "POSTGRES_URL"), args.keep_replies)
    refusal = _gates(args, live, log)
    results_path = CACHE / f"user_model_ab_{args.phase}{('_' + args.label) if args.label else ''}.json"
    started = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    if refusal:
        log(f"REFUSED: {refusal}")
        sb.write_json(results_path, {"status": "refused", "reason": refusal, "revision": revision,
                                     "started_at": started})
        return 2

    def _sigterm(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, _sigterm)

    code = 0
    if PENDING.exists() and args.phase != "teardown":
        log("REFUSED: a previous run's fresh-user teardown is pending — run `teardown` first")
        return 2
    if args.phase == "seed":
        out = phase_seed(live, args, log)
        code = 0 if out["status"] == "ok" else 2
    elif args.phase == "portrait":
        out = phase_portrait(live, args, log)
        code = 0 if out["status"] == "ok" else 2
    elif args.phase == "measure":
        out, code = phase_measure(live, args, log)
    elif args.phase == "hygiene":
        out, code = phase_hygiene(live, args, log)
    else:  # teardown
        out = {}
        if PENDING.exists():
            pend = json.loads(PENDING.read_text())
            users = [u for u in pend.get("users", []) if sb.DEMO_USER_RE.match(u)]
            out["pending"] = sb.teardown(live, users, [s for s in pend.get("sessions", [])
                                                       if s.startswith("bar-")])
            if out["pending"]["proven"]:
                PENDING.unlink(missing_ok=True)
        if args.fixed:
            # forget-synthetic REFUSES an allowlisted id. Try P's memory forget FIRST:
            # if it is refused, stop before the Postgres sweep, or the users/portrait/
            # chat rows would go while P's memory rows stayed.
            if live.forget(P_USER) is None:
                log("REFUSED fixed teardown: forget of P was refused — P is still in "
                    "ZOE_SYNTHETIC_USER_ALLOWLIST (remove it + restart zoe-data), or set "
                    "ZOE_BAR_ADMIN_SESSION for the admin forget")
                out["fixed"] = {"proven": False, "problems": ["P forget refused (allowlisted?)"]}
            else:
                out["fixed"] = sb.teardown(live, [P_USER, TWIN_USER], [])
        code = 0 if all(v.get("proven") for v in out.values()) else 2
        log(f"teardown: {json.dumps({k: v.get('proven') for k, v in out.items()})}")
    out = {**out, "phase": args.phase, "harness_version": HARNESS_VERSION, "revision": revision,
           "started_at": started,
           "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    sb.write_json(results_path, out)
    log(f"results: {results_path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
