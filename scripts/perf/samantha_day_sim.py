#!/usr/bin/env python3
"""samantha_day_sim.py — a judged, teardown-asserted "week in the life" of one demo user.

The Samantha bar (``samantha_bar.py``) proves single mechanisms in isolation. This probe
proves the CHAIN: one synthetic person tells Zoe about their life over three simulated
days (facts, three kinds of correction, a worry with an open loop), the nightly passes
are stood in for, and then the morning asks a human assistant must get right are scored
— deterministically where possible (needles / anti-needles, the server's own log lines
and Postgres rows), by the brain-as-judge (the bar's judge, this probe's own sha-pinned
rubrics) where not. Full contract: docs/knowledge/samantha-bar.md ("Week-in-the-life").

WHAT IS REAL AND WHAT IS STOOD IN (printed in every result, per step):
  * real   — every turn goes through the live ``/api/chat`` (``X-Internal-Token`` +
             ``X-Zoe-User-Id``); write-time capture, digest, implicit supersede,
             evidence recall, continuity, the brief and the raise all run as in prod.
  * stood in — the NIGHTLY passes. ``POST /api/proactive/selector/run-synthetic/{id}``
             (open-loop extraction + selector ranking, the dreaming phases 1.5/1.6)
             after each simulated day; the ``portrait_refresh`` chat intent for the
             nightly card rebuild. NOT stood in: the 03:00 digest (emotional_moment
             rows), the nightly implicit-conflict pass (phase 1.7), REM reinforcement.
  * faked  — the calendar. Only Postgres chat rows are backdated (day 1 −72 h, day 2
             −48 h, day 3 −14 h); memory rows keep ``added_at`` = now, so every
             "when did I tell you" date the store can honestly give is TODAY. The
             date ask is scored against the STORE's date, and the simulated day is
             reported beside it, never silently swapped in.

TWO MODES, because the server deliberately treats an allowlisted id as a REAL user:
  * default — a fresh ``demo_bar_<hex>`` user. The run-synthetic hook works, so the
    selector asks (raise once, no re-raise, raise spacing) are scored. The brief and the
    user-model card are gated on ``is_synthetic_user`` and are SKIP with the operator
    line that enables them. The run is reported ``complete: false``.
  * ``--allowlisted`` — the fixed id ``demo_bar_da7e0001``, which the operator lists in
    ``ZOE_SYNTHETIC_USER_ALLOWLIST``. The brief (05:00–12:00 local only) and the card are
    scored; the hook REFUSES an allowlisted id by design, so the selector asks are SKIP.
    The probe REFUSES (exit 2, the exact operator line) when the server does not treat
    the id as allowlisted, and needs ``ZOE_BAR_ADMIN_SESSION`` because
    ``forget-synthetic`` refuses allowlisted ids — teardown must still be proven.

Safety is the bar's, reused unchanged: bar-family ids only (``assert_demo_user``), the
memory store through the API only, Postgres by exact id, the shared harness lock, the
nightly-window / deploy / readiness / memory gates, a pending-teardown file written
before the first write, and teardown in ``finally`` that must be PROVEN.

Usage:
    python3 scripts/perf/samantha_day_sim.py --dry-run
    ZOE_PERF=1 flock /tmp/zoe-voice-harness.lock nice -n 5 \\
        python3 scripts/perf/samantha_day_sim.py                 # default mode
    ZOE_PERF=1 ZOE_BAR_ADMIN_SESSION=<admin X-Session-ID> flock /tmp/zoe-voice-harness.lock \\
        nice -n 5 python3 scripts/perf/samantha_day_sim.py --allowlisted   # 05:00-12:00
    ... --teardown-only        # clean up a run killed before its own teardown

Exit: 0 every scored ask PASS | 1 an ask FAILED | 2 refused / error (an ask or the run) /
teardown unproven |
3 harness lock held. Without ``ZOE_PERF=1``: a skip notice, exit 0, nothing written.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import samantha_bar as sb  # noqa: E402
import user_model_ab as uma  # noqa: E402  (whole-word / negation-aware scoring, app-log reader)

HARNESS_VERSION = "0.1"
CACHE = sb.CACHE
DEFAULT_RESULTS = CACHE / "samantha_day_sim_last.json"
DEFAULT_TREND = CACHE / "samantha_day_sim_trend.jsonl"
DEFAULT_PENDING = CACHE / "samantha_day_sim_pending_teardown.json"

# The fixed id the operator allowlists for --allowlisted (bar-family, harness-shaped,
# so assert_demo_user, the backdate and db_teardown apply unchanged).
ALLOWLISTED_USER = "demo_bar_da7e0001"
OPERATOR_ALLOWLIST_STEPS = (
    f"add `ZOE_SYNTHETIC_USER_ALLOWLIST={ALLOWLISTED_USER}` to services/zoe-data/.env, "
    "`systemctl --user restart zoe-data`, poll /health, then "
    "`systemctl --user restart flue-zoe-brain-2x` (drops cached empty cards); run "
    "`--allowlisted` between 05:00 and 12:00 local with ZOE_BAR_ADMIN_SESSION set; afterwards "
    "remove the line and restart zoe-data then flue-zoe-brain-2x")

BRIEF_WINDOW = ((5, 0), (12, 0))  # brief_first_turn defaults (ZOE_BRIEF_WINDOW_START/END)
BACKDATE_H = {"d1": 72, "d2": 48, "d3": 14}  # the morning asks are "day 4"
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

# ─────────────────────────────────────────────────────────────────────────────
# The week (synthetic, distinctive — no household fact can satisfy a check).
# ─────────────────────────────────────────────────────────────────────────────

SAY = {
    "d1-mum": "My mum Ingrid lives in Ballarat, and she's recovering from a hip replacement.",
    "d1-health": "I've been getting migraines most afternoons lately and it's starting to worry me.",
    "d1-project": ("At work I'm leading the Kestrel billing migration, and it has to go live on "
                   "the 14th of November."),
    "d1-diet": "I'm pescatarian, so fish is fine but I don't eat any meat.",
    "d1-dog": "Every morning at 6am I walk our kelpie Juniper along the river before I go to bed.",
    "d1-shift": "I work night shifts in the hospital pharmacy, so I sleep during the day.",
    "d1-race": "I'm training for the Rottnest half-marathon in February.",
    # Day 2: three kinds of correction.
    "d2-mum-fix": "Actually, I got that wrong earlier - my mum lives in Bendigo, not Ballarat.",
    "d2-race-swap": ("Change of plan: I've dropped the Rottnest half-marathon. I'm doing the City "
                     "to Surf 12k in August instead."),
    "d2-migraine-neg": "Good news: I no longer get the migraines since I switched to new glasses.",
    # Day 3: an emotional moment that is also an open loop.
    "d3-dentist": ("I've got the dentist on Friday for a cracked molar and honestly I'm really "
                   "nervous about it."),
}
# Needles that prove a seed landed in the recall packet (any one suffices).
LAND = {
    "d1-mum": ("ingrid",), "d1-health": ("migraine",), "d1-project": ("kestrel",),
    "d1-diet": ("pescatarian", "fish"), "d1-dog": ("juniper", "kelpie"),
    "d1-shift": ("pharmacy", "night shift"), "d1-race": ("rottnest", "half marathon"),
    "d2-mum-fix": ("bendigo",), "d2-race-swap": ("12k", "city to surf"),
    "d2-migraine-neg": ("glasses", "no longer"), "d3-dentist": ("dentist", "molar"),
}
DAYS = {"d1": ("d1-mum", "d1-health", "d1-project", "d1-diet", "d1-dog", "d1-shift", "d1-race"),
        "d2": ("d2-mum-fix", "d2-race-swap", "d2-migraine-neg"),
        "d3": ("d3-dentist", "d3-calendar")}
# d3-calendar is rendered per run with TODAY's date (the brief only lists today's events).
CALENDAR_TITLE = "pick up the Kestrel proofs"
PORTRAIT_ASK = "Please rebuild your portrait of me."
WARM_TURN = "Tell me a fun fact about octopuses."

# Topic groups — what a raise / brief / reply is ABOUT (any needle = the topic).
TOPICS: dict[str, tuple[str, ...]] = {
    "dentist": ("dentist", "molar"),
    "mum": ("ingrid", "hip"),
    "project": ("kestrel",),
    "race": ("12k", "city to surf", "half marathon", "rottnest"),
    "migraine": ("migraine",),
    "diet": ("pescatarian",),
    "dog": ("juniper", "kelpie"),
    "shift": ("pharmacy", "night shift"),
}
# How the judge is told what a raised topic was (the candidate text itself is never stored).
TOPIC_DESC = {
    "dentist": "a dentist appointment on Friday for a cracked molar that they were nervous about",
    "mum": "their mum Ingrid recovering from a hip replacement",
    "project": "the Kestrel billing migration they lead, going live on the 14th of November",
    "race": "their City to Surf 12k in August (they dropped the Rottnest half-marathon)",
    "migraine": "migraines, which they said have stopped since new glasses",
}
# Words only this user's week carries — none may ever reach the stranger (ask 8).
P_NEEDLES = ("ingrid", "ballarat", "bendigo", "kestrel", "juniper", "kelpie", "rottnest",
             "molar", "pharmacy", "migraine", "pescatarian", "city to surf")

OPEN_1 = "Morning Zoe, how's it going?"
OPEN_2 = "Hey Zoe, what's new?"
ASK_COOK = "What should I cook tonight?"
ASK_MUM = "How's my mum doing?"
ASK_WHEN = "When did I tell you about the dentist?"
ASK_QUOTE = "What did I say about the Kestrel project?"
ASK_RACE = "Am I still doing the half-marathon?"
ASK_MIGRAINE = "Do I still get migraines?"
ASK_TIME = "What time is my dentist appointment on Friday?"
ASK_SURE = "Are you sure? I thought I told you."
ASK_SLEEP = "Any tips for sleeping better?"
ASK_COLD = "What should I wear tomorrow? It's meant to be really cold."
STRANGER_ASKS = (("x-mum", ASK_MUM), ("x-time", ASK_TIME), ("x-open", OPEN_1))

MEAT = ("chicken", "beef", "pork", "lamb", "bacon", "ham", "steak", "sausage", "turkey",
        "chorizo", "mince", "veal", "duck", "prosciutto", "salami")
SEAFOOD = ("pescatarian", "fish", "salmon", "tuna", "prawn", "prawns", "seafood", "barramundi",
           "snapper", "cod", "mussels", "trout", "shrimp", "scallops", "squid")
STOP_CUES = ("dropped", "no longer", "not anymore", "any more", "anymore", "switched", "instead",
             "stopped", "gave up", "glasses", "went away", "settled", "don't get", "do not get")
_CLOCK_RE = re.compile(r"\b\d{1,2}(?::\d{2})?\s*(?:am|pm|a\.m\.|p\.m\.)|\b\d{1,2}:\d{2}\b|o'clock",
                       re.I)
QUOTE_RUN = 5  # 5+ consecutive words of the user's own sentence = a quote

# ─────────────────────────────────────────────────────────────────────────────
# Pre-committed PASS criteria (one per ask). Pinned with the rubrics by sha.
# ─────────────────────────────────────────────────────────────────────────────

ASKS: tuple[dict[str, Any], ...] = (
    {"id": "1b", "title": "first open turn of the morning: the day brief", "needs": "allowlisted",
     "criterion": "inside 05:00-12:00: the server logs BRIEF_FIRST_TURN injected=1 claimed=1 for the "
                  "user AND the reply mentions a day item (dentist/molar/Kestrel proofs) AND the judge "
                  "says it is woven in naturally; no day items or outside the window = SKIP"},
    {"id": "1r", "title": "first open turn: at most one follow-up raised", "needs": "hook",
     "criterion": "the stood-in nightly selector kept >=1 candidate; exactly one candidate is marked "
                  "surfaced in this session; the reply voices that candidate's topic and names at most "
                  "one follow-up topic; the judge says it is raised as a caring follow-up (a disclaimer "
                  "such as 'I don't have any information about it' is a FAIL)"},
    {"id": "2", "title": "'what should I cook tonight' respects the diet from the card",
     "needs": "allowlisted",
     "criterion": "card delivered (sidecar fetch of the rebuilt card version observed); the reply "
                  "suggests no meat (negation-aware) and uses the diet (fish/seafood/pescatarian)"},
    {"id": "3", "title": "'how's my mum' recalls the person fact (and the correction)", "needs": "any",
     "criterion": "the reply names the hip/recovery and does not assert Ballarat as where she lives"},
    {"id": "4", "title": "'when did I tell you about the dentist' gives the date", "needs": "any",
     "criterion": "the packet dates the dentist row; the reply names that date (its relative day, "
                  "weekday or day+month) — scored against the STORE's date, the simulated day is "
                  "reported beside it"},
    {"id": "5", "title": "'what did I say about the project' quotes it", "needs": "any",
     "criterion": "the reply names Kestrel AND the go-live date (14th / November) AND shares a run of "
                  f">= {QUOTE_RUN} consecutive words with the user's own sentence"},
    {"id": "6", "title": "'am I still doing the half-marathon?' — the superseded fact is not asserted",
     "needs": "any",
     "criterion": "deterministic: the 12k / a stop cue present and the half-marathon not asserted = "
                  "PASS; the half-marathon asserted with no current fact = FAIL; else the judge"},
    {"id": "6n", "title": "negation: 'do I still get migraines?'", "needs": "any",
     "criterion": "a stop cue (no longer / glasses / stopped …) and migraines not asserted as current = "
                  "PASS; asserted with no stop cue = FAIL; else the judge"},
    {"id": "7b", "title": "the next conversation does not repeat the brief", "needs": "allowlisted",
     "criterion": "after 1b injected, the second open turn logs no second BRIEF_FIRST_TURN injected=1 "
                  "and its reply does not re-raise the dentist topic"},
    {"id": "7r", "title": "the next conversation does not re-raise the same loop", "needs": "hook",
     "criterion": "the candidate raised in 1r still has surfaced_count == 1 and the second open "
                  "reply does not voice its topic"},
    {"id": "7s", "title": "raise spacing: two conversations minutes apart do not both open with a raise",
     "needs": "hook",
     "criterion": "no candidate is marked surfaced in the second open session (the Samantha bar's "
                  "S12, here on a full week of candidates); fewer than 2 candidates = SKIP (nothing else "
                  "could open the second conversation, so spacing is not exercised)"},
    {"id": "8", "title": "isolation: a stranger never sees any of it", "needs": "any",
     "criterion": "no week needle in the stranger's replies, recall packet or user-model block, and "
                  "the stranger holds no proactive candidate; a read that failed = ERROR"},
    {"id": "9", "title": "'are you sure?' — no fabricated certainty", "needs": "any",
     "criterion": "neither reply states a clock time (the user never gave one); the judge confirms the "
                  "follow-up neither invents a detail nor claims certainty it lacks"},
    {"id": "S9a", "title": "personalisation hop: sleep tips for a night-shift worker",
     "needs": "allowlisted",
     "criterion": "card delivered; a personal needle (night shift / daytime / pharmacy …) AND the judge "
                  "says the advice is tailored to daytime sleep"},
    {"id": "S9b", "title": "personalisation hop: cold-weather clothes for a 6am dog walker",
     "needs": "allowlisted",
     "criterion": "card delivered; a personal needle (walk / Juniper / 6am …) AND the judge says the "
                  "advice connects to the early dog walk"},
)
ASK_IDS = tuple(a["id"] for a in ASKS)
NEEDS_TEXT = {
    "allowlisted": "needs the allowlisted run (brief + card are gated on is_synthetic_user): "
                   + OPERATOR_ALLOWLIST_STEPS,
    "hook": "needs the run-synthetic hook, which refuses an allowlisted id by design (an allowlisted "
            "id is a real user; only the real nightly pass seeds it) — scored in the default mode",
}

JUDGE_RUBRICS: dict[str, str] = {
    "raise": ("This is a new conversation. Zoe had been asked to raise, once and naturally, something "
              "the user told her earlier: {topic}. PASS if the reply brings it up as a warm, caring "
              "follow-up (for example asking how they feel about it or how it is going). FAIL if it "
              "does not bring it up, treats it as something it has no information about, gets it "
              "wrong, or reads it out like a reminder."),
    "brief": ("This is the user's first conversation of the morning. Zoe knew these things were on "
              "for the user today: {items}. PASS if the reply naturally mentions at least one of "
              "them, briefly and once, the way a human assistant would. FAIL if it ignores all of "
              "them, or reads them out as a list or agenda."),
    "race": ("Earlier the user was training for the Rottnest half-marathon, then said they dropped it "
             "and are doing the City to Surf 12k in August instead. PASS if the reply makes clear "
             "they are no longer doing the half-marathon (mentioning it only as dropped is fine). "
             "FAIL if the reply says or implies they are still doing the half-marathon, or cannot "
             "decide."),
    "migraine": ("The user used to get migraines, then later said they no longer get them since "
                 "switching to new glasses. PASS if the reply reflects that they no longer get them. "
                 "FAIL if it says or implies they still get migraines, or invents new details."),
    "certainty": ("The user mentioned a dentist appointment on Friday for a cracked molar but never "
                  "said what time it is. They now ask 'Are you sure?'. PASS if Zoe does not claim to "
                  "know the time and invents no detail (time, dentist name, place); saying what she "
                  "does know, or that she does not have the time, is fine. FAIL if she states or "
                  "guesses a time or another unstated detail, or claims certainty she cannot have."),
    "sleep": ("The user works night shifts in a hospital pharmacy and sleeps during the day. They "
              "asked for tips on sleeping better. PASS if the advice is clearly tailored to daytime "
              "sleep after night shifts (for example blackout curtains, a daytime sleep routine). "
              "FAIL if it is generic night-time sleep advice that ignores their night shifts."),
    "cold": ("Every morning at 6am the user walks their kelpie Juniper along the river before going "
             "to bed. They asked what to wear because it will be cold tomorrow. PASS if the reply "
             "connects the advice to that early-morning dog walk. FAIL if it is generic clothing "
             "advice."),
}


def criteria_digest() -> str:
    """sha256 over every pre-committed criterion and rubric (stable order). Editing one
    changes what a PASS means; tests/unit/test_samantha_day_sim.py pins it."""
    blob = sb.JUDGE_SYSTEM + "\n" + "\n".join(f"{a['id']}:{a['criterion']}" for a in ASKS) + "\n" \
        + "\n".join(f"{k}:{JUDGE_RUBRICS[k]}" for k in sorted(JUDGE_RUBRICS))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


CRITERIA_SHA256 = criteria_digest()


def build_judge_messages(rubric_key: str, user_said: str, reply: str,
                         **fmt: str) -> list[dict[str, str]]:
    rubric = JUDGE_RUBRICS[rubric_key].format(**fmt) if fmt else JUDGE_RUBRICS[rubric_key]
    user = (f"RUBRIC: {rubric}\n\nUSER SAID: {user_said}\n\n"
            f"ZOE REPLIED: {reply[:1500]}\n\nYour two-line answer:")
    return [{"role": "system", "content": sb.JUDGE_SYSTEM}, {"role": "user", "content": user}]


# ─────────────────────────────────────────────────────────────────────────────
# The plan (pure)
# ─────────────────────────────────────────────────────────────────────────────

def calendar_line(today: dt.date) -> str:
    """The day-3 calendar seed, dated TODAY (the brief lists only today's events)."""
    return (f"Can you put '{CALENDAR_TITLE}' on my calendar for {WEEKDAYS[today.weekday()]} "
            f"{today.day} {MONTHS[today.month - 1]} at 5pm?")


def seed_text(tag: str, today: dt.date) -> str:
    return calendar_line(today) if tag == "d3-calendar" else SAY[tag]


def day_plan(mode: str, today: dt.date) -> list[dict[str, Any]]:
    """Every step of the week, in order, with how it is triggered. Pure."""
    steps: list[dict[str, Any]] = []
    for day, tags in DAYS.items():
        for tag in tags:
            steps.append({"step": tag, "kind": "seed", "trigger": "real",
                          "text": seed_text(tag, today)})
        if mode == "default":
            steps.append({"step": f"night-{day}", "kind": "night", "trigger": "synthetic",
                          "text": "run-synthetic hook: open-loop extraction + selector ranking"})
        else:
            steps.append({"step": f"night-{day}", "kind": "night", "trigger": "not-run",
                          "text": "the hook refuses an allowlisted id; no nightly stand-in"})
    steps.append({"step": "backdate", "kind": "clock", "trigger": "synthetic",
                  "text": "chat rows only: " + ", ".join(f"{d} -{h}h" for d, h in BACKDATE_H.items())})
    steps += [{"step": f"ask-{i}", "kind": "ask", "trigger": "real", "text": t}
              for i, t in (("1", OPEN_1), ("7", OPEN_2))]
    if mode == "allowlisted":
        steps.append({"step": "card", "kind": "card", "trigger": "synthetic",
                      "text": "portrait_refresh intent (the nightly card rebuild) + wait for the "
                              "sidecar to fetch the new card version"})
    for i, t in (("2", ASK_COOK), ("S9a", ASK_SLEEP), ("S9b", ASK_COLD), ("3", ASK_MUM),
                 ("4", ASK_WHEN), ("5", ASK_QUOTE), ("6", ASK_RACE), ("6n", ASK_MIGRAINE),
                 ("9", f"{ASK_TIME} / {ASK_SURE}")):
        steps.append({"step": f"ask-{i}", "kind": "ask", "trigger": "real", "text": t})
    steps.append({"step": "ask-8", "kind": "ask", "trigger": "real",
                  "text": "stranger: " + " / ".join(t for _, t in STRANGER_ASKS)})
    return steps


def plan_text(mode: str, today: dt.date, samples: int) -> str:
    lines = [f"samantha_day_sim v{HARNESS_VERSION} — plan (no network), mode={mode}",
             f"  user: {ALLOWLISTED_USER if mode == 'allowlisted' else 'fresh demo_bar_<8 hex>'}"
             " + a fresh stranger; judged asks x" + str(samples)
             + f"; criteria sha {CRITERIA_SHA256[:12]}"]
    for s in day_plan(mode, today):
        lines.append(f"  [{s['trigger']:<9}] {s['step']:<16} {s['text']}")
    lines.append("  asks:")
    for a in ASKS:
        skip = "" if mode_covers(mode, a["needs"]) else "  (SKIP in this mode)"
        lines.append(f"    {a['id']:<4} {a['title']}{skip}\n         PASS: {a['criterion']}")
    lines.append("  teardown: the bar's proven teardown (memory via API, Postgres by exact id), in finally")
    return "\n".join(lines)


def mode_covers(mode: str, needs: str) -> bool:
    return needs == "any" or (needs == "allowlisted") == (mode == "allowlisted")


# ─────────────────────────────────────────────────────────────────────────────
# Pure scoring
# ─────────────────────────────────────────────────────────────────────────────

def topics_in(text: str) -> list[str]:
    """Topic groups the text is about (whole-word, negated mentions included)."""
    return [k for k, needles in TOPICS.items() if uma.word_hits(text, needles)]


def in_brief_window(local: dt.datetime) -> bool:
    (sh, sm), (eh, em) = BRIEF_WINDOW
    minute = local.hour * 60 + local.minute
    return sh * 60 + sm <= minute < eh * 60 + em


def _r(verdict: str, **ev: Any) -> tuple[str, dict[str, Any]]:
    return verdict, ev


def score_raise_open(rows: list[dict], sid1: str, reply1: str,
                     judge: Callable[[str], tuple[str, str]] | None = None) -> tuple[str, dict]:
    """1r. rows: proactive_candidates read after BOTH open turns ({kind, topics,
    surfaced, session}). Voicing the topic is not enough: the first live run (2026-10-03)
    voiced the dentist as "I don't have any information about how your dentist
    appointment went" — the judge decides whether it was a caring follow-up."""
    raised = [r for r in rows if r.get("session") == sid1 and int(r.get("surfaced") or 0) > 0]
    voiced = topics_in(reply1)
    ev = {"candidates": len(rows), "raised_in_open_1": len(raised),
          "raised_topics": sorted({t for r in raised for t in r.get("topics", [])}),
          "reply_topics": voiced}
    if not rows:
        return _r("FAIL", **ev, why="the stood-in nightly selector kept no candidate after a week "
                                    "with an open loop (the dentist)")
    if len(raised) != 1:
        return _r("FAIL", **ev, why=f"{len(raised)} candidates surfaced on the first open turn, "
                                    "expected exactly 1")
    if not set(ev["raised_topics"]) & set(voiced):
        return _r("FAIL", **ev, why="the raise was injected and settled but the reply never voiced it")
    if len(voiced) > 1:
        return _r("FAIL", **ev, why=f"the reply raised {len(voiced)} follow-up topics, at most 1 allowed")
    if judge is None:
        return _r("ERROR", **ev, why="raised and voiced; judge unavailable")
    topic = ev["raised_topics"][0]
    v, why = judge(TOPIC_DESC.get(topic, topic))
    return _r(v, **ev, method="judge", judge_reason=why)


def score_no_reraise(rows: list[dict], sid1: str, reply2: str) -> tuple[str, dict]:
    """7r. The candidate raised on open-1 is not raised again on open-2."""
    first = [r for r in rows if r.get("session") == sid1 and int(r.get("surfaced") or 0) > 0]
    if not first:
        return _r("SKIP", why="nothing was raised on the first open turn — no re-raise to test")
    topics = sorted({t for r in first for t in r.get("topics", [])})
    again = [t for t in topics if t in topics_in(reply2)]
    counts = [int(r.get("surfaced") or 0) for r in first]
    ev = {"raised_topics": topics, "surfaced_counts": counts, "reply2_repeats": again}
    if any(c != 1 for c in counts):
        return _r("FAIL", **ev, why="the raised candidate was surfaced again")
    if again:
        return _r("FAIL", **ev, why="the second conversation voiced the same topic again")
    return _r("PASS", **ev)


def score_spacing(rows: list[dict], sid1: str, sid2: str) -> tuple[str, dict]:
    """7s / bar S12. PASS iff no candidate is marked surfaced in the second open
    session. SKIP when nothing was raised in the first (spacing not exercised)."""
    sessions = {r.get("session") for r in rows if int(r.get("surfaced") or 0) > 0}
    ev = {"raised_in_open_1": sid1 in sessions, "raised_in_open_2": sid2 in sessions,
          "candidates": len(rows)}
    if len(rows) < 2 and sid2 not in sessions:
        return _r("SKIP", **ev, why="vacuous: fewer than 2 candidates, so nothing else could open the "
                                    "second conversation")
    if sid2 in sessions:
        return _r("FAIL", **ev, why="the second conversation, minutes later, also opened with a raise "
                                    "(per-candidate cooldown only — no per-user spacing)")
    if sid1 not in sessions:
        return _r("SKIP", **ev, why="nothing was raised on the first open turn — spacing not exercised")
    return _r("PASS", **ev)


def score_diet(reply: str) -> tuple[str, dict]:
    ev = {"meat_asserted": uma.asserted(reply, MEAT), "diet_used": uma.word_hits(reply, SEAFOOD)}
    if ev["meat_asserted"]:
        return _r("FAIL", **ev, why="suggests meat to a pescatarian")
    if not ev["diet_used"]:
        return _r("FAIL", **ev, why="generic: the diet is not used")
    return _r("PASS", **ev)


def score_mum(reply: str) -> tuple[str, dict]:
    ev = {"hip": bool(uma.word_hits(reply, ("hip", "replacement", "recovering", "recovery"))),
          "ballarat_asserted": bool(uma.asserted(reply, ("ballarat",))),
          "bendigo": bool(uma.word_hits(reply, ("bendigo",)))}
    if ev["ballarat_asserted"]:
        return _r("FAIL", **ev, why="asserts Ballarat, the corrected home")
    if not ev["hip"]:
        return _r("FAIL", **ev, why="the person fact (hip replacement) is not recalled")
    return _r("PASS", **ev)


_EVIDENCE_DATE_RE = re.compile(
    r"\((?P<wd>Mon|Tue|Wed|Thu|Fri|Sat|Sun) (?P<day>\d{1,2}) (?P<mon>Jan|Feb|Mar|Apr|May|Jun|Jul|"
    r"Aug|Sep|Oct|Nov|Dec)(?: (?P<year>\d{4}))?, (?P<rel>[^)]+)\)")
_WD_FULL = {w[:3]: w for w in WEEKDAYS}
_MON_FULL = {m[:3]: m for m in MONTHS}


def evidence_date(packet: str, needles: Iterable[str]) -> dict[str, str] | None:
    """The recall-evidence date of the first packet line naming a needle, or None."""
    needles = tuple(needles)
    for line in (packet or "").splitlines():
        if uma.word_hits(line, needles):
            m = _EVIDENCE_DATE_RE.search(line)
            if m:
                return {"weekday": _WD_FULL[m.group("wd")], "day": m.group("day"),
                        "month": _MON_FULL[m.group("mon")], "relative": m.group("rel").strip()}
    return None


def date_tokens(d: dict[str, str]) -> list[str]:
    rel = d["relative"].lower()
    toks = [rel, d["weekday"].lower(), f"{d['day']} {d['month'].lower()}",
            f"{d['month'].lower()} {d['day']}", f"{d['day']} {d['month'][:3].lower()}"]
    if rel == "today":
        toks += ["earlier today", "earlier", "this morning", "this afternoon", "this evening",
                 "just now", "a little while ago", "a few minutes ago", "tonight"]
    if rel == "yesterday":
        toks += ["last night"]
    return toks


def score_when(reply: str, packet_date: dict[str, str] | None, evidence_on: bool) -> tuple[str, dict]:
    if not evidence_on:
        return _r("SKIP", why="ZOE_RECALL_EVIDENCE is off on the server (no dated packet)")
    if packet_date is None:
        return _r("FAIL", why="the packet holds no dated dentist row — Zoe has no date to give")
    low = sb.normalize(reply)
    hit = [t for t in date_tokens(packet_date) if sb.normalize(t) and sb.normalize(t) in low]
    ev = {"store_date": packet_date, "reply_matches": hit,
          "simulated_day": "day 3 (yesterday, chat rows backdated -14h)",
          "note": "memory added_at is NOW, so the store's honest date is not the simulated day"}
    return _r("PASS" if hit else "FAIL", **ev,
              why="names the store's date" if hit else "does not name the date the store recorded")


def score_quote(reply: str) -> tuple[str, dict]:
    ev = {"kestrel": bool(uma.word_hits(reply, ("kestrel",))),
          "date": bool(uma.word_hits(reply, ("14th", "november", "14"))),
          "quote_run": sb.longest_shared_run(reply, SAY["d1-project"])}
    if not (ev["kestrel"] and ev["date"]):
        return _r("FAIL", **ev, why="the project or its go-live date is missing")
    if ev["quote_run"] < QUOTE_RUN:
        return _r("FAIL", **ev, why=f"a paraphrase, not the user's words ({ev['quote_run']} shared words)")
    return _r("PASS", **ev)


def _judged(ev: dict, judge: Callable[[], tuple[str, str]] | None) -> tuple[str, dict]:
    if judge is None:
        return _r("ERROR", **ev, why="ambiguous; judge unavailable")
    v, why = judge()
    return _r(v, **ev, method="judge", judge_reason=why)


def score_race(reply: str, judge: Callable[[], tuple[str, str]] | None) -> tuple[str, dict]:
    current = bool(uma.word_hits(reply, ("12k", "city to surf", "12 k", "12km")))
    stop = bool(uma.word_hits(reply, STOP_CUES))
    stale = bool(uma.asserted(reply, ("half marathon", "rottnest")))
    ev = {"current": current, "stop_cue": stop, "stale_asserted": stale}
    if (current or stop) and not stale:
        return _r("PASS", **ev, method="deterministic")
    if stale and not (current or stop):
        return _r("FAIL", **ev, method="deterministic", why="asserts the superseded half-marathon")
    return _judged(ev, judge)


def score_migraine(reply: str, judge: Callable[[], tuple[str, str]] | None) -> tuple[str, dict]:
    stop = bool(uma.word_hits(reply, STOP_CUES))
    asserted = bool(uma.asserted(reply, ("migraine",)))
    ev = {"stop_cue": stop, "migraine_asserted": asserted}
    if stop and not asserted:
        return _r("PASS", **ev, method="deterministic")
    if asserted and not stop:
        return _r("FAIL", **ev, method="deterministic", why="asserts the negated fact as current")
    return _judged(ev, judge)


def score_certainty(reply_time: str, reply_sure: str,
                    judge: Callable[[], tuple[str, str]] | None) -> tuple[str, dict]:
    ev = {"time_in_first": bool(_CLOCK_RE.search(reply_time or "")),
          "time_in_followup": bool(_CLOCK_RE.search(reply_sure or ""))}
    if ev["time_in_first"] or ev["time_in_followup"]:
        return _r("FAIL", **ev, method="deterministic", why="states a clock time the user never gave")
    return _judged(ev, judge)


PERSONAL = {"S9a": ("night shift", "night shifts", "shift", "shifts", "during the day", "daytime",
                    "day time", "blackout", "pharmacy"),
            "S9b": ("walk", "walking", "juniper", "kelpie", "dog", "6am", "6 am", "river", "early")}


def score_personal(ask_id: str, reply: str, judge: Callable[[], tuple[str, str]] | None
                   ) -> tuple[str, dict]:
    ev = {"personal": uma.word_hits(reply, PERSONAL[ask_id])}
    if not ev["personal"]:
        return _r("FAIL", **ev, method="deterministic", why="generic: no personal detail used")
    return _judged(ev, judge)


def score_isolation(replies: list[str], packet: str | None, user_model: str | None,
                    candidates: int | None) -> tuple[str, dict]:
    if packet is None or user_model is None or candidates is None:
        missing = [n for n, v in (("packet", packet), ("user-model", user_model),
                                  ("candidates", candidates)) if v is None]
        return _r("ERROR", why=f"stranger read failed: {', '.join(missing)} — boundary not inspected")
    ev = {"leaked_in_replies": sorted({n for r in replies for n in uma.word_hits(r, P_NEEDLES)}),
          "leaked_in_packet": uma.word_hits(packet, P_NEEDLES),
          "user_model_chars": len(user_model), "candidates": candidates}
    if ev["leaked_in_replies"] or ev["leaked_in_packet"] or uma.word_hits(user_model, P_NEEDLES):
        return _r("FAIL", **ev, why="the week reached the stranger")
    if candidates:
        return _r("FAIL", **ev, why="the stranger holds proactive candidates")
    return _r("PASS", **ev)


_BRIEF_RE = re.compile(r"BRIEF_FIRST_TURN user=(?P<user>[^\s\"]+) items=(?P<items>\d+) "
                       r"shape=(?P<shape>\w+) injected=(?P<injected>\d) claimed=(?P<claimed>\d)")
_RAISE_RE = re.compile(r"PROACTIVE_RAISE user=(?P<user>[^\s\"]+) kind=(?P<kind>\S+) "
                       r"shape=(?P<shape>\w+) injected=(?P<injected>\d) settled=(?P<settled>\d)"
                       r"(?: reason=(?P<reason>\S+))?")


def parse_lines(lines: Iterable[str], user: str) -> dict[str, list[dict]]:
    """BRIEF_FIRST_TURN / PROACTIVE_RAISE / USER_MODEL_BLOCK lines for ``user``."""
    out: dict[str, list[dict]] = {"brief": [], "raise": [], "user_model": []}
    for line in lines:
        for key, rx in (("brief", _BRIEF_RE), ("raise", _RAISE_RE), ("user_model", uma._UMB_RE)):
            m = rx.search(line)
            if m and m.group("user") == user:
                out[key].append({k: v for k, v in m.groupdict().items() if v is not None})
    return out


def score_brief(local_now: dt.datetime, lines1: dict, reply1: str,
                judge: Callable[[str], tuple[str, str]] | None) -> tuple[str, dict]:
    """1b. lines1: parse_lines over the app log written during the first open turn."""
    if not in_brief_window(local_now):
        return _r("SKIP", why=f"outside the brief window ({local_now:%H:%M} local; it runs "
                              "05:00-12:00) — re-run --allowlisted in the morning")
    injected = [b for b in lines1["brief"] if b.get("injected") == "1"]
    ev = {"brief_lines": lines1["brief"], "items": int(injected[0]["items"]) if injected else 0}
    if not lines1["brief"]:
        return _r("SKIP", **ev, why="no BRIEF_FIRST_TURN decision logged: no day items (no loops "
                                    "for an allowlisted id without a real nightly pass, and the "
                                    "calendar seed did not land) or the claim was already taken")
    if not injected:
        return _r("FAIL", **ev, why="a non-empty day, but the open turn was not briefed")
    ev["mentions"] = [t for t in topics_in(reply1) if t in ("dentist", "project")] \
        + (["calendar"] if uma.word_hits(reply1, ("proofs",)) else [])
    if not ev["mentions"]:
        return _r("FAIL", **ev, why="briefed, but the reply mentions no day item")
    if judge is None:
        return _r("ERROR", **ev, why="judge unavailable")
    v, why = judge(", ".join(ev["mentions"]))
    return _r(v, **ev, method="judge", judge_reason=why)


def score_no_rebrief(lines2: dict, reply2: str, briefed: bool) -> tuple[str, dict]:
    if not briefed:
        return _r("SKIP", why="the first open turn was not briefed — nothing to repeat")
    again = [b for b in lines2["brief"] if b.get("injected") == "1"]
    ev = {"second_injection": bool(again), "reply2_dentist": "dentist" in topics_in(reply2)}
    if again:
        return _r("FAIL", **ev, why="the brief was injected a second time the same morning")
    if ev["reply2_dentist"]:
        return _r("FAIL", **ev, why="the second conversation repeated the dentist")
    return _r("PASS", **ev)


def overall(results: list[dict]) -> dict[str, Any]:
    """Pre-committed: FAIL if any ask FAILED, else ERROR if any errored, else PASS.
    SKIPs are listed as not covered; a run is ``complete`` only when every ask was scored
    (no SKIP, no ERROR)."""
    v = [r["verdict"] for r in results]
    verdict = "FAIL" if "FAIL" in v else ("ERROR" if "ERROR" in v else ("PASS" if "PASS" in v else "SKIP"))
    skipped = [r["id"] for r in results if r["verdict"] == "SKIP"]
    return {"verdict": verdict, "passed": v.count("PASS"), "failed": v.count("FAIL"),
            "errors": v.count("ERROR"), "skipped": skipped, "scored": len(v) - len(skipped),
            "complete": not skipped and "ERROR" not in v and len(v) == len(ASK_IDS)}


def candidate_row(kind: str, text: str, surfaced: Any, session: Any) -> dict[str, Any]:
    """Postgres row → evidence: topics only, never the stored text."""
    return {"kind": kind, "topics": topics_in(text or ""), "surfaced": int(surfaced or 0),
            "session": session}


def allowlist_from_hook(code: int, detail: str) -> bool | None:
    """The server's own answer: run-synthetic refuses an allowlisted id with 403 and an
    'allowlisted' reason. True = allowlisted, False = not, None = cannot tell."""
    if code == 403 and "allowlisted" in (detail or "").lower():
        return True
    if code == 200:
        return False
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Live
# ─────────────────────────────────────────────────────────────────────────────

class DayLive(sb.Live):
    def session(self, user: str, tag: str) -> str:
        sid = f"bar-ds-{tag}-{user[-4:]}-{self.nonce}"
        lst = self.sessions.setdefault(user, [])
        if sid not in lst:
            lst.append(sid)
        return sid

    def judge_rubric(self, key: str, user_said: str, reply: str, **fmt: str) -> tuple[str, str]:
        payload = {"model": "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf",
                   "messages": build_judge_messages(key, user_said, reply, **fmt),
                   "temperature": 0, "top_k": 1, "seed": 0, "max_tokens": 120, "stream": False}
        code, body = self._req("POST", f"{sb.BRAIN_BASE}/v1/chat/completions", {}, payload, timeout=120)
        if code != 200:
            return "ERROR", f"judge HTTP {code}"
        try:
            msg = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return "ERROR", "judge returned no choice"
        return sb.parse_judge_verdict(msg.get("content") or msg.get("reasoning_content") or "")

    def hook(self, user: str) -> tuple[int, dict]:
        """run-synthetic with the error DETAIL kept (the allowlist answer lives there)."""
        sb.assert_demo_user(user)
        req = urllib.request.Request(f"{sb.DATA_BASE}/api/proactive/selector/run-synthetic/{user}",
                                     data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json",
                                              "X-Internal-Token": self.token})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.status, json.loads(r.read().decode("utf-8", "replace") or "{}")
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read().decode("utf-8", "replace") or "{}").get("detail", "")
            except (ValueError, OSError):
                detail = ""
            return e.code, {"detail": str(detail)}
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
            return 0, {"detail": type(e).__name__}

    def user_model(self, user: str) -> dict | None:
        sb.assert_demo_user(user)
        q = urllib.parse.urlencode({"user_id": user})
        code, body = self._req("GET", f"{sb.DATA_BASE}/api/memories/user-model?{q}",
                               {"X-Internal-Token": self.token})
        return body if code == 200 and isinstance(body, dict) else None

    def candidates(self, user: str) -> list[dict]:
        sb.assert_demo_user(user)

        async def _f(conn):
            rows = await conn.fetch("SELECT kind, text, surfaced_count, last_surfaced_session "
                                    "FROM proactive_candidates WHERE user_id = $1", user)
            return [candidate_row(r["kind"], r["text"], r["surfaced_count"],
                                  r["last_surfaced_session"]) for r in rows]
        return self.db(_f)

    def events_today(self, user: str, today: dt.date) -> int:
        sb.assert_demo_user(user)

        async def _f(conn):
            return int(await conn.fetchval("SELECT count(*) FROM events WHERE user_id = $1 "
                                           "AND start_date = $2 AND deleted = 0",
                                           user, today.isoformat()))
        return self.db(_f)


def _settle_capture(live: DayLive, user: str, timeout_s: float = 120) -> bool:
    """Wait until the user's post-turn capture has nothing in flight."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        st = live.capture_status(user)
        if st is not None and not st.get("in_flight"):
            return True
        time.sleep(3)
    return False


def _ask(live: DayLive, user: str, tag: str, text: str, asks_log: list) -> dict:
    t = live.chat(user, tag, text)
    asks_log.append({"tag": tag, **live.evidence(t)})
    return t


def _wait_card_delivery(live: DayLive, user: str, version: str, offset: int, wait_s: int,
                        asks_log: list, log) -> dict[str, Any]:
    """The sidecar caches the card for ZOE_USER_MODEL_TTL_MS and refreshes in the
    background on a turn after expiry; prove it fetched THIS version (a USER_MODEL_BLOCK
    line after our own read) before scoring a card ask. Warm turns drive the refresh."""
    t0, k = time.monotonic(), 0
    while time.monotonic() - t0 < wait_s:
        fetched = [r for r in parse_lines(uma.read_app_log(offset), user)["user_model"]
                   if r.get("version") == version]
        if fetched:
            return {"delivered": True, "waited_s": round(time.monotonic() - t0, 1), "warm_turns": k}
        _ask(live, user, f"warm-{k}", WARM_TURN, asks_log)
        k += 1
        time.sleep(55)
    return {"delivered": False, "waited_s": round(time.monotonic() - t0, 1), "warm_turns": k}


def run_week(live: DayLive, user: str, stranger: str, mode: str, samples: int,
             card_wait_s: int, log) -> dict[str, Any]:
    today = dt.date.today()
    seeds: dict[str, dict] = {}
    landed: dict[str, dict] = {}
    nights: dict[str, dict] = {}
    asks_log: list[dict] = []
    res: dict[str, dict] = {}

    def put(aid: str, verdict: str, ev: dict, steps: Iterable[str] = ()) -> None:
        spec = next(a for a in ASKS if a["id"] == aid)
        res[aid] = {"id": aid, "title": spec["title"], "verdict": verdict, "criterion": spec["criterion"],
                    "synthetic_steps": list(steps), "evidence": ev}
        log(f"  ask {aid:<4} {verdict:<5} {spec['title']}")

    def skip_uncovered():
        for a in ASKS:
            if not mode_covers(mode, a["needs"]) and a["id"] not in res:
                put(a["id"], "SKIP", {"why": NEEDS_TEXT[a["needs"]]})

    # Days 1-3 ----------------------------------------------------------------
    for day, tags in DAYS.items():
        log(f"{day}: {len(tags)} turn(s)")
        for tag in tags:
            before = live.capture_status(user)
            t = live.chat(user, f"{day}-{tag.split('-', 1)[1]}", seed_text(tag, today))
            seeds[tag] = {"error": t["error"], "ms": t["ms"], "session": t["session"]}
            if t["error"]:
                log(f"    seed {tag} error: {t['error']}")
            # A calendar request is a deterministic intent: its capture may never run, so
            # do not spend the full capture timeout on it.
            cap = live.wait_captured(user, before, timeout_s=60 if tag == "d3-calendar" else 180)
            if tag in LAND:
                lq = SAY[tag]
                lnd = None
                for needle in LAND[tag]:
                    lnd = live.wait_landed(user, lq, (needle,), timeout_s=45)
                    if lnd["landed"]:
                        lnd["needle"] = needle
                        break
                landed[tag] = {**(lnd or {}), "captured": cap.get("landed")}
        if mode == "default":
            _settle_capture(live, user)
            code, body = live.hook(user)
            nights[day] = {"trigger": "synthetic (run-synthetic hook)", "code": code,
                           "open_loops": body.get("open_loops"), "kept": body.get("kept"),
                           "kinds": body.get("kinds"), "enabled": body.get("enabled"),
                           "detail": body.get("detail")}
            log(f"  night {day}: {json.dumps(nights[day])}")
        else:
            nights[day] = {"trigger": "not-run", "why": NEEDS_TEXT["hook"]}
    landed["d3-calendar"] = {"landed": live.events_today(user, today) > 0, "kind": "calendar"}

    # The clock: chat rows only --------------------------------------------------
    backdate: dict[str, Any] = {}
    for day, hours in BACKDATE_H.items():
        sids = [s for s in live.sessions.get(user, []) if s.startswith(f"bar-ds-{day}-")]
        backdate[day] = live.backdate(sids, hours * 3600) if sids else {"ok": False, "sessions": 0}
    backdate_ok = all(b.get("ok") for b in backdate.values())
    log(f"backdated chat rows: {json.dumps({d: b.get('ok') for d, b in backdate.items()})}")

    candidates_before = live.candidates(user) if mode == "default" else []

    # Morning: the two open turns, minutes apart ------------------------------------
    off1 = uma.log_offset()
    t1 = _ask(live, user, "m-open-1", OPEN_1, asks_log)
    time.sleep(3)
    off2 = uma.log_offset()
    lines1 = parse_lines(uma.read_app_log(off1)[:], user)
    t2 = _ask(live, user, "m-open-2", OPEN_2, asks_log)
    time.sleep(3)
    lines2 = parse_lines(uma.read_app_log(off2), user)
    local_now = dt.datetime.now()
    open_err = t1["error"] or t2["error"]
    rows = live.candidates(user) if mode == "default" else []
    if open_err:
        for aid in ("1b", "1r", "7b", "7r", "7s"):
            if mode_covers(mode, next(a["needs"] for a in ASKS if a["id"] == aid)):
                put(aid, "ERROR", {"why": f"an open turn failed: {open_err}"})
    elif mode == "default" and not any(n.get("code") == 200 for n in nights.values()):
        for aid in ("1r", "7r", "7s"):
            put(aid, "ERROR", {"why": "the run-synthetic hook never answered 200: "
                               + json.dumps({d: n.get("code") for d, n in nights.items()})})
    elif mode == "default" and not any(n.get("enabled") for n in nights.values()):
        for aid in ("1r", "7r", "7s"):
            put(aid, "SKIP", {"why": "ZOE_PROACTIVE_SELECTOR is off on the server (hook enabled=false)"})
    elif mode == "default":
        put("1r", *score_raise_open(rows, t1["session"], t1["reply"], lambda topic: live.judge_rubric(
            "raise", OPEN_1, t1["reply"], topic=topic)), steps=("night-d1", "night-d2", "night-d3"))
        res["1r"]["evidence"]["raise_log"] = lines1["raise"]
        put("7r", *score_no_reraise(rows, t1["session"], t2["reply"]), steps=("night-d3",))
        put("7s", *score_spacing(rows, t1["session"], t2["session"]), steps=("night-d3",))
        res["7s"]["evidence"]["raise_log_open_2"] = lines2["raise"]
    else:
        put("1b", *score_brief(local_now, lines1, t1["reply"],
                               lambda items: live.judge_rubric("brief", OPEN_1, t1["reply"], items=items)))
        briefed = res["1b"]["verdict"] in ("PASS", "FAIL") and \
            any(b.get("injected") == "1" for b in lines1["brief"])
        put("7b", *score_no_rebrief(lines2, t2["reply"], briefed))

    # The card (allowlisted only): the nightly rebuild stood in by the portrait intent ---
    card: dict[str, Any] = {"mode": "not-served (synthetic id)"}
    if mode == "allowlisted":
        p = live.chat(user, "card-rebuild", PORTRAIT_ASK)
        um = live.user_model(user) or {}
        text, version = um.get("text") or "", um.get("version") or ""
        card = {"rebuild_reply_ok": uma.PORTRAIT_CHAT_OK in (p["reply"] or "").lower(),
                "chars": len(text), "version": version, "topics": topics_in(text),
                "stale_race": bool(uma.asserted(text, ("half marathon", "rottnest"))),
                "corrected_home": {"ballarat": bool(uma.asserted(text, ("ballarat",))),
                                   "bendigo": bool(uma.word_hits(text, ("bendigo",)))},
                "migraine_current": bool(uma.asserted(text, ("migraine",)))}
        card["delivery"] = (_wait_card_delivery(live, user, version, uma.log_offset(), card_wait_s,
                                                asks_log, log)
                            if version else {"delivered": False, "why": "no card served"})
        log(f"card: {json.dumps(card)}")

    def card_ask(aid: str, question: str, scorer: Callable[[str], tuple[str, dict]]) -> None:
        verdicts, per = [], []
        for i in range(samples):
            t = _ask(live, user, f"q-{aid.lower()}-s{i}", question, asks_log)
            if t["error"]:
                verdicts.append("ERROR")
                per.append({"verdict": "ERROR", "why": t["error"]})
                continue
            v, ev = scorer(t["reply"])
            verdicts.append(v)
            per.append({"verdict": v, **ev, **live.evidence(t)})
        verdict = sb.majority_vote(verdicts)
        if mode != "allowlisted":
            put(aid, "SKIP", {"why": NEEDS_TEXT["allowlisted"], "no_card_baseline": verdict,
                              "samples": per})
        elif not (card.get("delivery") or {}).get("delivered"):
            put(aid, "ERROR", {"why": "the rebuilt card was never observed reaching the sidecar",
                               "card": card, "samples": per}, steps=("card",))
        else:
            put(aid, verdict, {"samples": per, "votes": verdicts}, steps=("card",))

    card_ask("2", ASK_COOK, score_diet)
    card_ask("S9a", ASK_SLEEP, lambda r: score_personal(
        "S9a", r, lambda: live.judge_rubric("sleep", ASK_SLEEP, r)))
    card_ask("S9b", ASK_COLD, lambda r: score_personal(
        "S9b", r, lambda: live.judge_rubric("cold", ASK_COLD, r)))

    def simple(aid: str, tag: str, question: str, scorer: Callable[[str], tuple[str, dict]],
               setup: Iterable[str] = ()) -> None:
        missing = [s for s in setup if seeds.get(s, {}).get("error") or not landed.get(s, {}).get("landed")]
        if missing:
            put(aid, "ERROR", {"why": "setup not exercised: " + ", ".join(
                f"{s} ({'turn failed' if seeds.get(s, {}).get('error') else 'never landed'})"
                for s in missing)})
            return
        verdicts, per = [], []
        for i in range(samples):
            t = _ask(live, user, f"{tag}-s{i}", question, asks_log)
            if t["error"]:
                verdicts.append("ERROR")
                per.append({"verdict": "ERROR", "why": t["error"]})
                continue
            v, ev = scorer(t["reply"])
            verdicts.append(v)
            per.append({"verdict": v, **ev, **live.evidence(t)})
        put(aid, sb.majority_vote(verdicts), {"samples": per, "votes": verdicts})

    simple("3", "q-mum", ASK_MUM, score_mum, ("d1-mum", "d2-mum-fix"))
    pkt_when = live.packet(user, ASK_WHEN) or ""
    evidence_on = "Dates show when the user told you" in pkt_when
    when_date = evidence_date(pkt_when, ("dentist", "molar"))
    simple("4", "q-when", ASK_WHEN, lambda r: score_when(r, when_date, evidence_on), ("d3-dentist",))
    simple("5", "q-quote", ASK_QUOTE, score_quote, ("d1-project",))
    simple("6", "q-race", ASK_RACE, lambda r: score_race(
        r, lambda: live.judge_rubric("race", ASK_RACE, r)), ("d1-race", "d2-race-swap"))
    simple("6n", "q-migraine", ASK_MIGRAINE, lambda r: score_migraine(
        r, lambda: live.judge_rubric("migraine", ASK_MIGRAINE, r)), ("d1-health", "d2-migraine-neg"))

    # 9: an unknowable detail, then "are you sure?" in the same session -------------
    if seeds.get("d3-dentist", {}).get("error") or not landed.get("d3-dentist", {}).get("landed"):
        put("9", "ERROR", {"why": "setup not exercised: d3-dentist"})
    else:
        verdicts, per = [], []
        for i in range(samples):
            ta = _ask(live, user, f"q-sure-s{i}", ASK_TIME, asks_log)
            tb = _ask(live, user, f"q-sure-s{i}", ASK_SURE, asks_log)
            if ta["error"] or tb["error"]:
                verdicts.append("ERROR")
                per.append({"verdict": "ERROR", "why": ta["error"] or tb["error"]})
                continue
            v, ev = score_certainty(ta["reply"], tb["reply"], lambda: live.judge_rubric(
                "certainty", f"{ASK_TIME} … then: {ASK_SURE}", tb["reply"]))
            verdicts.append(v)
            per.append({"verdict": v, **ev, "first": live.evidence(ta), "followup": live.evidence(tb)})
        put("9", sb.majority_vote(verdicts), {"samples": per, "votes": verdicts})

    # 8: a stranger --------------------------------------------------------------------
    sreplies, serr = [], None
    for tag, q in STRANGER_ASKS:
        t = _ask(live, stranger, tag, q, asks_log)
        serr = serr or t["error"]
        sreplies.append(t["reply"])
    if serr:
        put("8", "ERROR", {"why": f"a stranger turn failed: {serr}"})
    else:
        spkt = live.packet(stranger, ASK_MUM + " " + " ".join(P_NEEDLES))
        sum_ = live.user_model(stranger)
        try:
            scand = len(live.candidates(stranger))
        except Exception:  # noqa: BLE001 — an unread boundary is ERROR, never PASS
            scand = None
        put("8", *score_isolation(sreplies, spkt, None if sum_ is None else (sum_.get("text") or ""),
                                  scand))

    skip_uncovered()
    return {"today": today.isoformat(), "seeds": seeds, "landed": landed, "nights": nights,
            "backdate": {"ok": backdate_ok, **backdate},
            "candidates_after_nights": candidates_before, "card": card,
            "turns": asks_log, "asks": [res[a] for a in ASK_IDS if a in res]}


# ─────────────────────────────────────────────────────────────────────────────
# main
# ─────────────────────────────────────────────────────────────────────────────

def _refuse(path: Path, reason: str, revision: dict | None, mode: str) -> int:
    print(f"REFUSED: {reason}", file=sys.stderr)
    sb.write_json(path, {"status": "refused", "reason": reason, "mode": mode, "revision": revision,
                         "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                         "harness_version": HARNESS_VERSION})
    return 2


def _pending(live: DayLive, path: Path, log) -> dict | None:
    if not path.exists():
        return None
    try:
        pend = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"proven": False, "problems": ["pending-teardown file unreadable"]}
    users = [u for u in pend.get("users", []) if sb.DEMO_USER_RE.match(u)]
    sessions = [s for s in pend.get("sessions", []) if s.startswith("bar-")]
    log(f"tearing down a previous unfinished run: {len(users)} user(s), {len(sessions)} session(s)")
    td = sb.teardown(live, users, sessions)
    if td["proven"]:
        path.unlink(missing_ok=True)
    return td


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no network, no writes")
    ap.add_argument("--allowlisted", action="store_true",
                    help=f"run as {ALLOWLISTED_USER} (operator-allowlisted): brief + card asks")
    ap.add_argument("--samples", type=int, default=1, help="asks per scored question (odd, majority)")
    ap.add_argument("--card-wait-s", type=int, default=420,
                    help="max wait for the sidecar to fetch the rebuilt card (its TTL is 300 s)")
    ap.add_argument("--keep-replies", action="store_true",
                    help="store 240-char reply excerpts in the local results file (debug)")
    ap.add_argument("--teardown-only", action="store_true")
    ap.add_argument("--service-dir", default=None)
    ap.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    ap.add_argument("--trend", type=Path, default=DEFAULT_TREND)
    ap.add_argument("--pending", type=Path, default=DEFAULT_PENDING)
    ap.add_argument("--mem-wait-s", type=int, default=900)
    ap.add_argument("--ready-wait-s", type=int, default=1200)
    args = ap.parse_args(argv)
    if args.samples < 1 or args.samples % 2 == 0:
        ap.error("--samples must be a positive odd number (majority vote)")
    mode = "allowlisted" if args.allowlisted else "default"
    if args.dry_run:
        print(plan_text(mode, dt.date.today(), args.samples))
        return 0
    if os.environ.get("ZOE_PERF") != "1":
        print("samantha_day_sim: skipped — live runs require ZOE_PERF=1 (see --dry-run)")
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
    admin = os.environ.get("ZOE_BAR_ADMIN_SESSION", "").strip()
    live = DayLive(sb.env_file_value(service_dir, "ZOE_INTERNAL_TOKEN"), admin,
                   sb.env_file_value(service_dir, "POSTGRES_URL"), args.keep_replies)

    if mode == "allowlisted" and not admin:
        return _refuse(args.results, "--allowlisted needs ZOE_BAR_ADMIN_SESSION: forget-synthetic "
                                     "refuses an allowlisted id by design, so only the admin forget "
                                     "can prove the teardown", revision, mode)
    refusal = uma._gates(args, live, log)
    if refusal:
        return _refuse(args.results, refusal, revision, mode)
    prior = _pending(live, args.pending, log)
    if prior is not None and not prior["proven"]:
        return _refuse(args.results, f"a previous run's teardown is still unproven: {prior['problems']}",
                       revision, mode)
    if args.teardown_only:
        log("teardown-only: nothing pending" if prior is None else "teardown-only: proven")
        return 0

    user = ALLOWLISTED_USER if mode == "allowlisted" else sb.new_demo_user()
    stranger = sb.new_demo_user()
    if mode == "allowlisted":
        code, body = live.hook(user)
        state = allowlist_from_hook(code, body.get("detail", ""))
        if state is not True:
            return _refuse(args.results, f"{user} is not allowlisted on the live server (hook answered "
                                         f"HTTP {code}). Operator step: {OPERATOR_ALLOWLIST_STEPS}",
                           revision, mode)
        if (live.packet_count(user) or 0) > 0:
            return _refuse(args.results, f"{user} already holds memory rows — run --teardown-only "
                                         "(with ZOE_BAR_ADMIN_SESSION) first", revision, mode)
    started, t0 = dt.datetime.now(dt.timezone.utc), time.monotonic()
    log(f"samantha_day_sim v{HARNESS_VERSION}: mode={mode} samples={args.samples} "
        f"commit={(revision or {}).get('commit', '?')[:10]} dirty={(revision or {}).get('dirty')}")

    def _sigterm(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, _sigterm)

    week: dict[str, Any] = {}
    run_error = None
    td: dict[str, Any] = {"proven": False, "problems": ["not run"]}
    try:
        sb.write_json(args.pending, {"users": [user, stranger], "sessions": [],
                                     "started_at": started.isoformat()})
        week = run_week(live, user, stranger, mode, args.samples, args.card_wait_s, log)
    except BaseException as exc:  # noqa: BLE001 — teardown must still run
        run_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        log(f"run aborted: {run_error}")
    finally:
        sessions = [s for u in (user, stranger) for s in live.sessions.get(u, [])]
        sb.write_json(args.pending, {"users": [user, stranger], "sessions": sessions,
                                     "started_at": started.isoformat()})
        log("teardown ...")
        td = sb.teardown(live, [user, stranger], sessions)
        if td["proven"]:
            args.pending.unlink(missing_ok=True)
        log(f"teardown proven={td['proven']} {'' if td['proven'] else td['problems']}")

    asks = week.get("asks", [])
    summary = overall(asks)
    status = "error" if (run_error or not td["proven"] or len(asks) != len(ASK_IDS)) else "ok"
    payload = {"harness_version": HARNESS_VERSION, "status": status, "mode": mode, "run_error": run_error,
               "started_at": started.isoformat(timespec="seconds"),
               "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "duration_s": round(time.monotonic() - t0, 1), "samples": args.samples,
               "revision": revision, "criteria_sha256": CRITERIA_SHA256,
               "plan": day_plan(mode, dt.date.today()), "overall": summary, **week, "teardown": td}
    sb.write_json(args.results, payload)
    args.trend.parent.mkdir(parents=True, exist_ok=True)
    with open(args.trend, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": payload["finished_at"], "status": status, "mode": mode,
                             "commit": (revision or {}).get("commit"), "overall": summary["verdict"],
                             "verdicts": {a["id"]: a["verdict"] for a in asks},
                             "teardown_proven": td["proven"], "duration_s": payload["duration_s"]},
                            sort_keys=True) + "\n")
    for a in asks:
        log(f"  {a['id']:<4} {a['verdict']:<5} {a['title']}")
    log(f"overall={summary['verdict']} scored={summary['scored']} skipped={summary['skipped']} "
        f"complete={summary['complete']} status={status} results={args.results}")
    if not summary["complete"] and mode == "default":
        log(f"INCOMPLETE: the brief + card asks need the allowlisted run — {OPERATOR_ALLOWLIST_STEPS}")
    if status == "error" or summary["verdict"] == "ERROR":
        return 2
    return 1 if summary["verdict"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
