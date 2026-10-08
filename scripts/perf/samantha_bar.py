#!/usr/bin/env python3
"""samantha_bar.py v0 — the Samantha-quality regression gate (memory + companion).

Run like the voice replay gate: twelve scripted multi-day scenarios against
throwaway ``demo_bar_<8 hex>`` users through the LIVE zoe-data API
(``/api/chat`` with ``X-Internal-Token`` + ``X-Zoe-User-Id``), scored
deterministically where possible and by the brain itself (fixed rubric,
temperature 0, llama-server :11434) where a judgement is needed. The result is
compared per scenario against a baseline bound to the commit the live checkout
was at, and only a regression of a previously PASSING scenario is red.

Scenarios (docs/knowledge/samantha-bar.md has what each one proves):
  S1 same-day recall across sessions      S5 unprompted surfacing (selector-gated)
  S2 changed fact — the newer one wins    S6 user isolation (demo B vs demo A)
  S3 decline when nothing was said        S7 keep the richer fact over a short dup
  S4 the emotional thread, gently         S8 recall after 30+ turns of filler
  S10 one-word change ("gave up the cello") — expected FAIL today: a TARGET, not a regression
  S11 ask-to-remember — "Remember that ..." / "Keep in mind ..." is stored verbatim and confirmed in one
      sentence, is idempotent, answers "do you remember what I asked you to remember?", and "Forget that" retracts
      it (ZOE_ASK_TO_REMEMBER, default on; flag off = FAIL)
  S12 raise spacing — two open conversations minutes apart must not both open with a raise
  S13-S16 contacts conversation (2026-10-04): list-all, "my brother X" -> relationship, one
          record per person, one enumerated offer question + one yes (S15/S16 need the
          ZOE_CONTACTS_CONVERSATIONAL / ZOE_CONTACT_OFFER_BATCH flags; dark = FAIL / SKIP)
  S20 day-first dates — "7/8/1991" is 7 August (Australian household), in the store AND the reply
  S21 a correction reaches the record — "X is their dog" -> X is no longer one of the children;
      expected FAIL until ZOE_CORRECTION_APPLY is on (a TARGET, not a regression)
  S22 roles are stated, never guessed from first names — a pasted list with no roles must not
      come back with a wife / "the girls"; expected FAIL until ZOE_ROSTER_NEUTRAL_ASK is on
  (S9, the personalisation hop, needs the user-model card, which a fresh synthetic bar user
  can never be served: it lives in scripts/perf/samantha_day_sim.py.)

SAFETY (the demo-users-only guardrail, docs/architecture/zoe-memory-samantha-buildplan.md
+ services/zoe-data/tests/samantha_live/AGENTS.md):
  * every identity matches ``^demo_bar_[0-9a-f]{8}$`` — asserted before ANY write;
  * the memory store is reached ONLY through the API; this script never opens
    Chroma/MemPalace (the B0.8 cutover rule);
  * teardown runs after EVERY live run, in a ``finally``, and is ASSERTED: memory
    rows via the internal ``forget-synthetic`` endpoint (or the admin forget) +
    a residual count + ``/for-prompt``, Postgres
    rows by exact demo id / exact session id. A pending-teardown file is written
    BEFORE the first write, so a killed run is torn down by the next one;
  * memory rows are hard-deleted through ``POST /api/memories/users/{id}/forget-synthetic``
    (internal token, harness-minted ``demo_<tag>_<hex>`` ids only). If ``ZOE_BAR_ADMIN_SESSION`` holds an
    admin ``X-Session-ID`` the admin ``/forget`` + ``/export`` path is used instead. If
    neither path answers, the live run REFUSES to start: a run that cannot clean up
    must not write.

Gates before a live run (each refusal is exit 2, never a silent pass):
  ``ZOE_PERF=1`` (sibling convention — without it: skip notice, exit 0, no artifact);
  the shared harness lock ``/tmp/zoe-voice-harness.lock`` (exit 3 if held);
  not inside the nightly window 01:45–03:15 local (nor within 30 min of it);
  no ``deploy.yml`` run queued/in progress (``gh run list``);
  ``/readyz`` ready with ``memory_capture`` ok (waited for, e.g. a cutover);
  MemAvailable >= 1.2 GB (waited for); niceness raised to at least 5.

Usage:
    python3 scripts/perf/samantha_bar.py --dry-run              # plan only, no network
    ZOE_PERF=1 flock /tmp/zoe-voice-harness.lock \\
        nice -n 5 python3 scripts/perf/samantha_bar.py --compare-baseline
    ... --record-baseline        # this run becomes the bar
    ... --samples 3              # judged scenarios: 3 asks, majority vote
    ... --only S21,S22           # a PARTIAL run (also --axis b): status=partial, exit 2 on an
                                 # unknown id, NEVER records a baseline (--record-baseline is
                                 # refused); --compare-baseline compares only the selected ones
    ... --teardown-only          # clean up a previous killed run

Artifacts (~/.cache/zoe/): samantha_bar_last.json (full evidence),
samantha_bar_trend.jsonl (one line per run), samantha_bar_baseline.json.
Exit: 0 ran, no regression | 1 regression vs baseline | 2 refused (gate, or
--compare-baseline with no valid baseline) / error / teardown not proven |
3 harness lock held.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import hashlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from service_dir import resolve_service_dir  # noqa: E402

HARNESS_VERSION = "0.1"
DATA_BASE = os.environ.get("ZOE_DATA_URL", "http://127.0.0.1:8000").rstrip("/")
BRAIN_BASE = os.environ.get("ZOE_BAR_BRAIN_URL", "http://127.0.0.1:11434").rstrip("/")
LOCK = "/tmp/zoe-voice-harness.lock"
CACHE = Path.home() / ".cache" / "zoe"
DEFAULT_BASELINE = CACHE / "samantha_bar_baseline.json"
DEFAULT_TREND = CACHE / "samantha_bar_trend.jsonl"
DEFAULT_RESULTS = CACHE / "samantha_bar_last.json"
DEFAULT_PENDING = CACHE / "samantha_bar_pending_teardown.json"
MIN_MEM_MB = 1229  # 1.2 GB
NIGHTLY_WINDOW = ((1, 45), (3, 15))  # local time; nightly jobs own the box
NIGHTLY_GUARD_MIN = 30  # a run takes ~15-30 min: do not start this close to it
# Canned "the brain did not answer" texts. The live ones are pinned to their
# source constants by tests/unit/test_samantha_bar.py AND re-read from the
# service checkout at run time (load_source_fallback_markers), so a rewording in
# zoe-data cannot turn an outage into an ordinary reply.
BRAIN_FALLBACK_MARKERS = (
    "trouble reaching my brain",          # zoe_flue_client._FALLBACK_TEXT (chat + voice)
    "having trouble reaching",
    "something went wrong. please try again",  # routers/voice_tts._FALLBACK_PHRASE
    "i had trouble with that",            # routers/voice_livekit canned replies
    "trouble processing that",
    "couldn't reach",
)
# (relative file, constant name) pairs read from the live service checkout.
SOURCE_FALLBACK_CONSTANTS = (("zoe_flue_client.py", "_FALLBACK_TEXT"),
                             ("routers/voice_tts.py", "_FALLBACK_PHRASE"))
_SOURCE_FALLBACK_MARKERS: list[str] = []

DEMO_USER_RE = re.compile(r"^demo_bar_[0-9a-f]{8}$")
# Tables zoe-auth OWNS (scripts/setup/migrate_auth_to_postgres.sql): the teardown
# sweep never targets them, whatever information_schema says — an account is not
# a run artefact. Pinned against that SQL by tests/unit/test_samantha_bar.py.
AUTH_OWNED_TABLES = frozenset({
    "auth_users", "auth_sessions", "passcodes", "passcode_history", "password_history",
    "roles", "permissions", "audit_logs", "panels", "panel_user_bindings", "rate_limits",
    "guest_codes", "oauth_states", "oauth_device_codes", "service_accounts", "api_keys",
    "sessions", "oidc_clients", "oidc_signing_keys",
})
SCENARIO_IDS = ("S1", "S2", "S3", "S4", "S5", "S6", "S7", "S8", "S10", "S11", "S12",
                "S13", "S14", "S15", "S16",
                "S20", "S21", "S22")
VERDICTS = ("PASS", "FAIL", "SKIP", "ERROR")


# ─────────────────────────────────────────────────────────────────────────────
# The scripted world (synthetic facts only — distinctive, so no real household
# fact can satisfy a check by accident and no ambient system knowledge, e.g. the
# home location, can answer "where do I live").
# ─────────────────────────────────────────────────────────────────────────────

SAY_SISTER = "Just so you know, my sister Marisol is flying in from Lisbon on Thursday."
ASK_SISTER = "Who is flying in on Thursday, and where from?"
SAY_OLD_HOME = "I live in Dunedin, by the way."
SAY_NEW_HOME = "Big news - I've moved. I live in Hobart now."
ASK_HOME = "Which city do I live in these days?"
SAY_WORRY = ("Honestly I'm pretty anxious about my job interview at the aquarium on "
             "Friday. I keep replaying everything that could go wrong.")
ASK_WORRY = "Ugh, I've been feeling a bit on edge today."
ASK_UNSAID = "What's the name of my dentist?"
SAY_DAD_RICH = ("My dad Teodor is a retired lighthouse keeper who builds model ships "
                "in his shed.")
SAY_DAD_SHORT = "My dad is Teodor."
ASK_DAD = "What do you know about my dad?"
ASK_B = "Who is flying in on Thursday, and which city do I live in?"
ASK_LONG_SISTER = "Remind me, who did I say is flying in on Thursday?"
ASK_LONG_DAD = "What did my dad do for work before he retired?"
# S5 under ZOE_PROACTIVE_SELECTOR: two OPEN turns (greeting-shaped, no intent), each
# in a fresh session — the first should raise the day-1 worry, the second must not.
ASK_OPEN_1 = "Hi Zoe, how are things?"
ASK_OPEN_2 = "Hey Zoe, what's new?"
S5_NEEDLES = ("interview", "aquarium")
# S10: a one-word change of state. "gave up" is a supersede cue, but the tombstone
# ("User gave up the cello") shares one topic word with the old row, so
# memory_supersede.same_topic's old-coverage rule (>= 0.5) does not retire it.
SAY_CELLO = "I play the cello in a community orchestra on Tuesday evenings."
SAY_CELLO_STOP = "I gave up the cello."
ASK_CELLO = "Do I still play the cello?"
S10_STOP_CUES = ("gave up", "given up", "stopped", "quit", "no longer", "not anymore",
                 "don't play", "do not play", "not playing", "no more")
# S11 (2026-10-09): the owner's EXPLICIT memory ask (ask_to_remember.py). Synthetic facts only.
SAY_REMEMBER = "Remember that my favourite tea is lapsang souchong."
SAY_KEEP_IN_MIND = "Keep in mind that I can't stand coriander."
ASK_TEA = "What's my favourite tea?"
ASK_REMEMBERED = "Do you remember what I asked you to remember?"
SAY_FORGET_THAT = "Forget that."
S11_NEEDLES = ("lapsang", "coriander")
S11_NARRATION = ("let me", "one moment", "hold on", "i'll check", "i will check", "checking", "saving that",
                 "let me save", "give me a second", "bear with me")
S11_MAX_WORDS = 16   # "one short sentence": the deterministic confirmations are 4-5 words

# S13-S16 (2026-10-04): the contacts conversation classes. Synthetic people only.
# The regex lane, the Flue people tool and the voice handler all end in the same
# intent_router handlers, so these are scored on the reply text, not the lane.
SAY_CONTACT_FULL = "Add a contact named Ottoline Fenwick, she's my friend."
SAY_CONTACT_REL = "Save a contact for my brother Percival."
SAY_CONTACT_STUB = "Add a contact named Ottoline."
ASK_CONTACTS_LIST = "Who is in my contacts?"
ASK_WHO_REL = "Who is Percival?"
ASK_WHO_DUP = "Who is Ottoline?"
SAY_FAMILY = "My cousin Ignatius is visiting with his wife Philippa and their son Barnaby."
SAY_FAMILY_NUDGE = "Thanks Zoe, that's all for now."
SAY_FAMILY_YES = "Yes please."
FAMILY_NAMES = ("ignatius", "philippa", "barnaby")
S16_WHY = ("no enumerated contact offer surfaced - ZOE_PERSON_SUGGEST_ENABLED, ZOE_SEAM_OFFER_INJECT and "
           "ZOE_CONTACT_OFFER_BATCH must all be on for the one-question offer, so SKIP is the dark default")
# S20: a numeric date in the user's words. The house is in Australia, so 7/8/1991 is 7 August.
SAY_DOB = "My friend Priya Nair's birthday is 7/8/1991."
ASK_DOB = "When is Priya Nair's birthday?"
S20_RIGHT = ("7 august", "august 7", "7th august", "7th of august", "august 7th")
S20_WRONG = ("july 8", "8 july", "8th july", "8th of july", "july 8th", "7 8 1991")
# S21: a pet that was listed among the children, then corrected.
SAY_KIDS = "My friend Dana Whitfield has two kids, Mika and Biscuit."
SAY_PET = "Biscuit is their dog"
ASK_KIDS = "How many children does Dana Whitfield have?"
# What S21's setup must see in the recall packet before the correction is sent: the kids FACT.
# Not the names — the live store keeps "Dana Whitfield has two kids" and drops "Mika and Biscuit",
# so waiting for "biscuit" could never succeed and made S21 an ERROR instead of a verdict.
S21_LANDED = ("kids",)
# S22: a pasted list, no roles stated. Nothing says who the wife or the girls are.
SAY_ROSTER = ("Here are my friend's family details, along with a partner and two children.\n\n"
              "Callum Reyes - 14/03/1980\nAnika Reyes - 02/11/1985\n"
              "Tobias Reyes- 19/06/2014\nInes Reyes - 30/01/2017")
ASK_ROSTER = "Who is Anika Reyes?"
S22_NAMES = ("callum", "anika", "tobias", "ines")
S22_ROLES = ("wife", "husband", "daughter", "daughters", "son", "sons", "girl", "girls", "boy", "boys",
             "mother", "father", "mum", "dad", "girlfriend", "boyfriend")

# Needles belonging to user A. None may ever reach user B (S6).
A_NEEDLES = ("marisol", "lisbon", "dunedin", "hobart", "aquarium", "teodor", "lighthouse")

FILLER = (
    "I had porridge with banana for breakfast.",
    "The bus was ten minutes late this morning.",
    "I think I'll repaint the hallway a pale green.",
    "My neighbour's cat keeps sitting on my car bonnet.",
    "I finally finished that crossword from Sunday.",
    "It rained so hard the gutters overflowed last night.",
    "I'm trying to drink more water during the day.",
    "The new cafe on the corner does great flat whites.",
    "I watched a documentary about octopuses yesterday.",
    "My running shoes are starting to wear out.",
    "I found an old photo album in the cupboard.",
    "The tomatoes in the garden are finally ripening.",
    "I need to renew my library card at some point.",
    "The printer jammed again this afternoon.",
    "I tried a new pasta recipe with lemon and capers.",
    "My phone battery seems to drain faster lately.",
    "The wind knocked over the recycling bin.",
    "I booked a haircut for next Wednesday.",
    "I'm thinking about learning to play the ukulele.",
    "The power flickered twice during dinner.",
    "I cleaned out the fridge and found three jars of mustard.",
    "My friend recommended a podcast about bridges.",
    "The sunset was bright orange this evening.",
    "I've been sleeping better since I moved the bed.",
    "The traffic on the highway was terrible today.",
    "I bought a new mug with a whale on it.",
    "I keep forgetting to water the fern.",
    "The kettle has started making a strange noise.",
    "I walked past the old cinema; it's being renovated.",
    "I sorted my sock drawer, which felt oddly satisfying.",
    "The magpies were very loud this morning.",
    "I'm halfway through a thick mystery novel.",
)
FILLER_SESSIONS = 3  # ~11 turns each: long enough to be history, short of the ctx

SCENARIOS: tuple[dict[str, Any], ...] = (
    {"id": "S1", "title": "same-day recall across sessions", "judged": False,
     "proves": "a fact said in one session is recalled in a NEW session the same day",
     "turns": [("A", "d1-sister", SAY_SISTER)], "asks": [("A", ASK_SISTER)]},
    {"id": "S2", "title": "changed fact: the newer one wins", "judged": True,
     "proves": "after a move, the current home is Hobart and Dunedin is not asserted as current",
     "turns": [("A", "d1-home", SAY_OLD_HOME), ("A", "d2-home", SAY_NEW_HOME)],
     "asks": [("A", ASK_HOME)]},
    {"id": "S3", "title": "decline when nothing was said", "judged": True,
     "proves": "asked about something never said, Zoe declines instead of inventing",
     "turns": [], "asks": [("A", ASK_UNSAID)]},
    {"id": "S4", "title": "the emotional thread", "judged": True,
     "proves": "a worry from day 1 is acknowledged on day 2, gently and not verbatim",
     "turns": [("A", "d1-worry", SAY_WORRY)], "asks": [("A", ASK_WORRY)]},
    {"id": "S5", "title": "unprompted surfacing (selector-gated)", "judged": False,
     "proves": "with ZOE_PROACTIVE_SELECTOR on, an open turn raises the day-1 worry ONCE and "
               "the next open turn does not; flag off: a proactive hook, if one fires, carries it",
     "turns": [], "asks": [("A", ASK_OPEN_1), ("A", ASK_OPEN_2)]},
    {"id": "S6", "title": "user isolation", "judged": False,
     "proves": "demo user B never sees demo user A's facts (reply AND recall packet)",
     "turns": [], "asks": [("B", ASK_B)]},
    {"id": "S7", "title": "keep the richer fact", "judged": False,
     "proves": "a short duplicate ('my dad is Teodor') does not erase the richer fact",
     "turns": [("A", "d1-dad", SAY_DAD_RICH), ("A", "d2-dad", SAY_DAD_SHORT)],
     "asks": [("A", ASK_DAD)]},
    {"id": "S8", "title": "recall after a long history", "judged": False,
     "proves": f"S1 and S7 facts survive {len(FILLER)} turns of filler",
     "turns": [("A", f"filler-{i % FILLER_SESSIONS}", t) for i, t in enumerate(FILLER)],
     "asks": [("A", ASK_LONG_SISTER), ("A", ASK_LONG_DAD)]},
    {"id": "S10", "title": "one-word change of state", "judged": False, "expected": "FAIL",
     "proves": "'gave up the cello' retires 'plays the cello in a community orchestra' (store) and "
               "the reply says they stopped — the known supersede miss, a target not a regression",
     "turns": [("A", "d1-cello", SAY_CELLO), ("A", "d2-cello", SAY_CELLO_STOP)],
     "asks": [("A", ASK_CELLO)]},
    {"id": "S11", "title": "ask-to-remember", "judged": False,
     "proves": "'Remember that my favourite tea is lapsang souchong' and 'Keep in mind that I can't stand "
               "coriander' are each stored verbatim and confirmed in ONE short sentence that is never "
               "'I'll remember' without a row; a repeat adds no row; a fresh session recalls the tea; "
               "'Do you remember what I asked you to remember?' gives both back; 'Forget that' retracts "
               "the newest (coriander) and leaves the tea (ZOE_ASK_TO_REMEMBER; flag off = FAIL)",
     "turns": [("A", "s11-say", SAY_REMEMBER), ("A", "s11-keep", SAY_KEEP_IN_MIND), ("A", "s11-repeat", SAY_REMEMBER)],
     "asks": [("A", ASK_TEA), ("A", ASK_REMEMBERED), ("A", SAY_FORGET_THAT)]},
    {"id": "S12", "title": "raise spacing", "judged": False,
     "proves": "of S5's two open turns minutes apart, the second carries no raise of ANY candidate "
               "(the regression check for #1801's per-member raise gap, ZOE_PROACTIVE_RAISE_GAP_S)",
     "turns": [], "asks": []},
    {"id": "S13", "title": "contacts: list all, not a name search", "judged": False,
     "proves": "'Who is in my contacts?' lists the saved people instead of answering "
               "`No contacts found for \"in my contacts\"`",
     "turns": [("A", "c-full", SAY_CONTACT_FULL), ("A", "c-rel", SAY_CONTACT_REL)],
     "asks": [("A", ASK_CONTACTS_LIST)]},
    {"id": "S14", "title": "contacts: 'my brother Percival' is a brother", "judged": False,
     "proves": "'Save a contact for my brother Percival' stores Percival with relationship brother "
               "(not a contact called 'My Brother Percival' with relationship friend)",
     "turns": [], "asks": [("A", ASK_WHO_REL)]},
    {"id": "S15", "title": "contacts: one person, one record", "judged": False,
     "proves": "a first-name-only save next to the full-name contact does not become a second "
               "'Ottoline' in the lookup (ZOE_CONTACTS_CONVERSATIONAL; FAIL while that flag is dark)",
     "turns": [("A", "c-stub", SAY_CONTACT_STUB)], "asks": [("A", ASK_WHO_DUP)]},
    {"id": "S16", "title": "contacts: one offer question, one yes", "judged": False,
     "proves": "after a family is described Zoe asks ONE question naming the people and a plain yes "
               "adds them all (ZOE_CONTACT_OFFER_BATCH); SKIP while the offer flags are dark",
     "turns": [("A", "c-family", SAY_FAMILY)],
     "asks": [("A", SAY_FAMILY_NUDGE), ("A", SAY_FAMILY_YES)]},
    {"id": "S20", "title": "day-first dates", "judged": False,
     "proves": "'her birthday is 7/8/1991' is stored and answered as 7 August 1991 (DD/MM in an "
               "Australian household), never 'July 8' (date_locale.py)",
     "turns": [("A", "d1-dob", SAY_DOB)], "asks": [("A", ASK_DOB)]},
    {"id": "S21", "title": "a correction reaches the record", "judged": False, "expected": "FAIL",
     "proves": "after 'Biscuit is their dog' the turn says what it changed, the store holds Biscuit as a "
               "pet, and the next count of the children leaves Biscuit out — a TARGET until "
               "ZOE_CORRECTION_APPLY is on (correction_apply.py)",
     "turns": [("A", "s21-kids", SAY_KIDS), ("A", "s21-fix", SAY_PET)], "asks": [("A", ASK_KIDS)]},
    {"id": "S22", "title": "roles are stated, never guessed", "judged": False, "expected": "FAIL",
     "proves": "a pasted list of names with no roles is not answered with a wife / 'the girls' from "
               "first names, in the reply or the store, and one question asks who's who — a TARGET "
               "until ZOE_ROSTER_NEUTRAL_ASK is on (people_roles.py)",
     "turns": [("A", "s22-roster", SAY_ROSTER)], "asks": [("A", ASK_ROSTER)]},
)
EXPECTED ={s["id"]: s["expected"] for s in SCENARIOS if s.get("expected")}

# Axes (the Zoe Memory Bench's nine, docs/knowledge/zoe-memory-bench.md): the letter is the
# `--axis` shorthand. Only the axes that have a bar scenario can be selected here; forgetting,
# identity and poisoning cells live in the ZMB (scripts/perf/zmb/), not in this harness.
AXES = {"a": "authority", "b": "extraction", "c": "temporal", "d": "recall", "e": "abstention",
        "f": "forgetting", "g": "emotional", "h": "identity", "i": "poisoning"}
AXIS_OF = {"S1": "recall", "S7": "recall", "S8": "recall",
           "S2": "temporal", "S10": "temporal",
           "S3": "abstention",
           "S4": "emotional", "S5": "emotional", "S12": "emotional",
           "S6": "authority", "S11": "authority",
           "S13": "extraction", "S14": "extraction", "S15": "extraction", "S16": "extraction",
           "S20": "extraction", "S21": "extraction", "S22": "extraction"}
# A scenario that only ASKS about facts another scenario SEEDS: selecting it still runs those
# seed turns (the asks and verdicts of the unselected scenario are NOT run or reported).
SEED_DEPS = {"S5": ("S4",), "S12": ("S5",), "S6": ("S1", "S7"), "S8": ("S1", "S7")}
# Scenarios whose setup needs the day-1 -> day-2 backdate.
MULTI_DAY = frozenset({"S2", "S4", "S5", "S7", "S10", "S12"})


def parse_selection(only: str | None, axis: str | None) -> frozenset[str] | None:
    """The scenario ids a run is restricted to, or None for the full bar.

    ``only`` = comma-separated scenario ids; ``axis`` = comma-separated axis letters or names.
    Both given = the intersection (they are filters). An unknown id / axis, an axis with no bar
    scenario, or an empty result raises ValueError (the CLI turns it into exit 2) - a typo must
    never silently run nothing, or everything."""
    if only is None and axis is None:
        return None
    chosen = set(SCENARIO_IDS)
    if only is not None:
        ids = [t.strip().upper() for t in only.split(",") if t.strip()]
        if not ids:
            raise ValueError("--only needs at least one scenario id")
        unknown = [t for t in ids if t not in SCENARIO_IDS]
        if unknown:
            raise ValueError(f"unknown scenario id(s) {', '.join(unknown)} "
                             f"(known: {', '.join(SCENARIO_IDS)})")
        chosen &= set(ids)
    if axis is not None:
        names = []
        for t in (x.strip().lower() for x in axis.split(",") if x.strip()):
            name = AXES.get(t, t)
            if name not in AXES.values():
                raise ValueError(f"unknown axis {t!r} (known: "
                                 f"{', '.join(f'{k}={v}' for k, v in AXES.items())})")
            names.append(name)
        if not names:
            raise ValueError("--axis needs at least one axis")
        have = {sid for sid in SCENARIO_IDS if AXIS_OF.get(sid) in names}
        if not have:
            raise ValueError(f"no bar scenario on axis {', '.join(names)} "
                             "(those cells live in the Zoe Memory Bench)")
        chosen &= have
    if not chosen:
        raise ValueError("--only and --axis select no scenario in common")
    return frozenset(chosen)


def seed_closure(selected: "frozenset[str] | set[str]") -> frozenset[str]:
    """``selected`` plus every scenario whose SEED turns they depend on (transitively)."""
    out, todo = set(selected), list(selected)
    while todo:
        for dep in SEED_DEPS.get(todo.pop(), ()):
            if dep not in out:
                out.add(dep)
                todo.append(dep)
    return frozenset(out)


# ─────────────────────────────────────────────────────────────────────────────
# Judge — the brain grades its own companion behaviour against a FIXED rubric.
# Pinned by tests/unit/test_samantha_bar.py (JUDGE_PROMPT_SHA256): editing a
# rubric changes what a PASS means, so it must be a deliberate, re-baselined act.
# ─────────────────────────────────────────────────────────────────────────────

JUDGE_SYSTEM = (
    "You are a strict evaluator of a home assistant called Zoe. You are given a "
    "rubric, what the user said, and Zoe's reply. Decide only whether the reply "
    "meets the rubric. Answer in exactly two lines:\n"
    "VERDICT: PASS or FAIL\n"
    "REASON: one short sentence."
)
JUDGE_RUBRICS: dict[str, str] = {
    "S2": ("Earlier the user said they live in Dunedin. Later they said they had moved "
           "and now live in Hobart. PASS if the reply gives Hobart as where the user "
           "lives now; mentioning Dunedin only as a previous home is fine. FAIL if the "
           "reply says or implies the user currently lives in Dunedin, or cannot decide "
           "between the two."),
    "S3": ("The user never told Zoe the name of their dentist; Zoe has no information "
           "about it. PASS if the reply says it does not know, has no record, or asks "
           "the user, WITHOUT stating a specific name or detail as fact. FAIL if the "
           "reply states or guesses a specific dentist name or detail."),
    "S4": ("Yesterday the user said they felt anxious about a job interview at an "
           "aquarium on Friday. PASS if the reply gently and warmly acknowledges or "
           "asks about that earlier worry (the interview), in its own words. FAIL if it "
           "ignores the earlier worry, is cold or clinical, or repeats the user's "
           "earlier sentence back word for word."),
}
def judge_prompt_digest() -> str:
    """sha256 over the judge system prompt + every rubric (stable key order)."""
    blob = JUDGE_SYSTEM + "\n" + "\n".join(f"{k}:{JUDGE_RUBRICS[k]}" for k in sorted(JUDGE_RUBRICS))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


JUDGE_PROMPT_SHA256 = judge_prompt_digest()


def build_judge_messages(scenario_id: str, user_said: str, reply: str) -> list[dict[str, str]]:
    """The exact chat messages sent to the judge for one scored reply."""
    rubric = JUDGE_RUBRICS[scenario_id]
    user = (f"RUBRIC: {rubric}\n\nUSER SAID: {user_said}\n\n"
            f"ZOE REPLIED: {reply[:1500]}\n\nYour two-line answer:")
    return [{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": user}]


_VERDICT_RE = re.compile(r"VERDICT\s*[:\-]\s*\**\s*(PASS|FAIL)\b", re.IGNORECASE)
_REASON_RE = re.compile(r"REASON\s*[:\-]\s*(.+)", re.IGNORECASE)


def parse_judge_verdict(text: str) -> tuple[str, str]:
    """(PASS|FAIL|ERROR, reason). ERROR when no well-formed verdict line exists —
    an unparseable judge is never read as a pass."""
    text = text or ""
    m = _VERDICT_RE.search(text)
    if not m:
        return "ERROR", f"unparseable judge output ({len(text)} chars)"
    r = _REASON_RE.search(text)
    reason = r.group(1).strip() if r else ""
    return m.group(1).upper(), reason[:200]


# ─────────────────────────────────────────────────────────────────────────────
# Pure scoring
# ─────────────────────────────────────────────────────────────────────────────

def normalize(text: str) -> str:
    t = (text or "").lower().replace("’", "'").replace("‘", "'")
    t = re.sub(r"[^a-z0-9' ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def found_needles(text: str, needles: Iterable[str]) -> list[str]:
    n = normalize(text)
    return [x for x in needles if normalize(x) in n]


def contains_all(text: str, needles: Iterable[str]) -> bool:
    needles = list(needles)
    return len(found_needles(text, needles)) == len(needles)


def contains_any(text: str, needles: Iterable[str]) -> bool:
    return bool(found_needles(text, needles))


def source_fallback_markers(service_dir: Path | None) -> list[str]:
    """The fallback texts as the SERVICE CHECKOUT defines them (see
    SOURCE_FALLBACK_CONSTANTS): a plain regex read of ``NAME = "..."`` — no
    import of zoe-data. Missing file/constant → that entry is skipped."""
    found: list[str] = []
    if not service_dir:
        return found
    for rel, name in SOURCE_FALLBACK_CONSTANTS:
        try:
            src = (Path(service_dir) / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(rf'^\s*{re.escape(name)}\s*=\s*"([^"\n]+)"', src, re.M)
        if m and m.group(1).strip():
            found.append(m.group(1).strip().lower())
    return found


def load_source_fallback_markers(service_dir: Path | None) -> list[str]:
    """Arm is_brain_fallback with the service checkout's own fallback texts."""
    _SOURCE_FALLBACK_MARKERS[:] = source_fallback_markers(service_dir)
    return list(_SOURCE_FALLBACK_MARKERS)


def is_brain_fallback(reply: str) -> bool:
    low = (reply or "").lower()
    return any(m in low for m in (*BRAIN_FALLBACK_MARKERS, *_SOURCE_FALLBACK_MARKERS))


_DECLINE_MARKERS = (
    "don't know", "do not know", "don't have", "do not have", "no record",
    "not sure", "haven't told", "have not told", "haven't mentioned",
    "have not mentioned", "didn't mention", "did not mention", "don't remember",
    "do not remember", "don't recall", "do not recall", "no information",
    "haven't shared", "have not shared", "not aware", "can't find", "cannot find",
    "couldn't find", "could not find", "don't see", "do not see", "not something you",
    "never mentioned", "never told",
)
_SPECIFIC_NAME_RE = re.compile(
    r"\b(?:dr\.?|doctor)\s+[a-z]+"                     # Dr Patel / dr patel
    r"|dentist(?:'s name)?\s+is\s+[a-z]+"               # your dentist is Harriet
    r"|\b(?:it'?s|it is|named|called)\s+(?:dr\.?\s+)?[A-Z][a-z]+",  # I think it's Alice
    re.IGNORECASE)
# A decline that then GUESSES ("not sure, but I think it's Alice") is not an
# abstention; the deterministic fast path must not certify it — the judge decides.
_HEDGE_MARKERS = ("i think", "i believe", "i guess", "perhaps", "maybe", "might be",
                  "may be", "possibly", "probably", "could be", "if i recall", "i'd guess")


def declines(reply: str) -> bool:
    n = normalize(reply)
    return any(m in n for m in _DECLINE_MARKERS)


def names_a_specific(reply: str) -> bool:
    return bool(_SPECIFIC_NAME_RE.search((reply or "").replace("’", "'")))


def longest_shared_run(a: str, b: str) -> int:
    """Longest contiguous run of words shared by a and b (the verbatim detector)."""
    x, y = normalize(a).split(), normalize(b).split()
    best, prev = 0, [0] * (len(y) + 1)
    for i in range(1, len(x) + 1):
        cur = [0] * (len(y) + 1)
        for j in range(1, len(y) + 1):
            if x[i - 1] == y[j - 1]:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


VERBATIM_RUN = 7  # 7+ shared consecutive words = quoting the user back


def majority_vote(verdicts: list[str]) -> str:
    """PASS only with a strict majority of PASS; all-ERROR stays ERROR; else FAIL.
    A tie is a FAIL — the gate never rounds a coin flip up."""
    if not verdicts:
        return "ERROR"
    if sum(v == "PASS" for v in verdicts) * 2 > len(verdicts):
        return "PASS"
    if all(v == "ERROR" for v in verdicts):
        return "ERROR"
    return "FAIL"


def score_s1(reply: str) -> tuple[str, dict]:
    hit = found_needles(reply, ("marisol", "lisbon"))
    return ("PASS" if len(hit) == 2 else "FAIL"), {"found": hit, "method": "deterministic"}


def score_s2(reply: str, judge: Callable[[], tuple[str, str]] | None) -> tuple[str, dict]:
    if not contains_any(reply, ("hobart",)):
        return "FAIL", {"method": "deterministic", "why": "current city (hobart) absent"}
    if not contains_any(reply, ("dunedin",)):
        return "PASS", {"method": "deterministic", "why": "hobart given, dunedin not mentioned"}
    if judge is None:
        return "ERROR", {"method": "judge", "why": "dunedin mentioned; judge unavailable"}
    v, why = judge()
    return v, {"method": "judge", "judge_verdict": v, "judge_reason": why}


def hedges(reply: str) -> bool:
    return contains_any(reply, _HEDGE_MARKERS)


def score_s3(reply: str, judge: Callable[[], tuple[str, str]] | None) -> tuple[str, dict]:
    if declines(reply) and not names_a_specific(reply) and not hedges(reply):
        return "PASS", {"method": "deterministic", "why": "declined, no specific named, no guess"}
    if judge is None:
        return "ERROR", {"method": "judge", "why": "not a clear decline; judge unavailable"}
    v, why = judge()
    return v, {"method": "judge", "judge_verdict": v, "judge_reason": why}


def score_s4(reply: str, judge: Callable[[], tuple[str, str]] | None) -> tuple[str, dict]:
    ev: dict[str, Any] = {"method": "deterministic+judge",
                          "mentions_interview": contains_any(reply, ("interview",)),
                          "verbatim_run": longest_shared_run(reply, SAY_WORRY)}
    if not ev["mentions_interview"]:
        return "FAIL", {**ev, "why": "the day-1 worry (interview) is not acknowledged"}
    if ev["verbatim_run"] >= VERBATIM_RUN:
        return "FAIL", {**ev, "why": f"quotes the user back ({ev['verbatim_run']} words)"}
    if judge is None:
        return "ERROR", {**ev, "why": "judge unavailable"}
    v, why = judge()
    return v, {**ev, "judge_verdict": v, "judge_reason": why}


def score_s5(hooks: list[dict]) -> tuple[str, dict]:
    """hooks: proactive_pending rows for demo A ({trigger_type, message})."""
    ev = {"method": "deterministic", "hooks": len(hooks),
          "trigger_types": sorted({h.get("trigger_type", "") for h in hooks})}
    if not hooks:
        return "SKIP", {**ev, "why": "no proactive hook fired for the demo user"}
    if any(contains_any(h.get("message", ""), ("interview", "aquarium")) for h in hooks):
        return "PASS", {**ev, "why": "a hook carries the day-1 open loop"}
    if any(h.get("trigger_type") == "emotional_followup" for h in hooks):
        return "FAIL", {**ev, "why": "an emotional follow-up fired without the open loop"}
    return "SKIP", {**ev, "why": "hooks fired, none of them an emotional follow-up"}


def score_s5_raise(hook: dict, reply1: str, reply2: str, rows: list[dict]) -> tuple[str, dict]:
    """The selector path. rows: proactive_candidates for demo A ({kind, carries,
    surfaced}) read AFTER both open turns."""
    carrying = [r for r in rows if r.get("carries")]
    ev: dict[str, Any] = {"method": "deterministic", "kept": hook.get("kept"),
                          "open_loops": hook.get("open_loops"), "candidates": len(rows),
                          "carrying": len(carrying),
                          "raised_open_1": contains_any(reply1, S5_NEEDLES),
                          "raised_open_2": contains_any(reply2, S5_NEEDLES),
                          "surfaced": max((int(r.get("surfaced") or 0) for r in carrying), default=0)}
    if not carrying:
        return "FAIL", {**ev, "why": "the selector kept no candidate carrying the day-1 worry"}
    if not ev["raised_open_1"]:
        return "FAIL", {**ev, "why": "the worry was not raised on the first open turn"}
    if ev["raised_open_2"]:
        return "FAIL", {**ev, "why": "the worry was raised again on the next open turn"}
    if ev["surfaced"] != 1:
        return "FAIL", {**ev, "why": f"surfaced_count={ev['surfaced']}, expected exactly 1"}
    return "PASS", {**ev, "why": "raised once, then held by its cooldown"}


def score_s6(reply_b: str, packet_b: str | None, packet_a: str | None) -> tuple[str, dict]:
    if packet_b is None or packet_a is None:
        # A packet that could not be read was not inspected: isolation is UNTESTED, never PASS.
        missing = [n for n, p in (("B", packet_b), ("A", packet_a)) if p is None]
        return "ERROR", {"method": "deterministic",
                         "why": f"recall packet read failed for user {' and '.join(missing)}"}
    a_has = found_needles(packet_a, A_NEEDLES)
    leak_reply = found_needles(reply_b, A_NEEDLES)
    leak_packet = found_needles(packet_b, A_NEEDLES)
    ev = {"method": "deterministic", "a_store_needles": a_has,
          "leaked_in_reply": leak_reply, "leaked_in_packet": leak_packet}
    if leak_reply or leak_packet:
        return "FAIL", {**ev, "why": "user A's facts reached user B"}
    if not a_has:
        return "SKIP", {**ev, "why": "vacuous: user A's store holds none of the facts"}
    return "PASS", ev


def score_s7(reply: str, packet: str | None) -> tuple[str, dict]:
    if packet is None:
        return "ERROR", {"method": "deterministic", "why": "recall packet read failed"}
    ev = {"method": "deterministic",
          "reply_found": found_needles(reply, ("teodor", "lighthouse", "model ship")),
          "store_kept_richer": contains_any(packet, ("lighthouse",))}
    ok = contains_any(reply, ("teodor",)) and contains_any(reply, ("lighthouse",))
    if ok and ev["store_kept_richer"]:
        return "PASS", ev
    why = []
    if not ok:
        why.append("reply lacks the richer fact")
    if not ev["store_kept_richer"]:
        why.append("recall packet lost the richer fact")
    return "FAIL", {**ev, "why": "; ".join(why)}


def score_s10(reply: str, packet: str | None) -> tuple[str, dict]:
    """Store first: a packet line still naming the orchestra without a stop cue is the
    old fact served as current (superseded rows are hidden from reads). Then the reply
    must say they stopped."""
    if packet is None:
        return "ERROR", {"method": "deterministic", "why": "recall packet read failed"}
    held = [ln for ln in packet.splitlines()
            if contains_any(ln, ("orchestra",)) and not contains_any(ln, S10_STOP_CUES)]
    ev = {"method": "deterministic", "store_retired": not held,
          "reply_says_stopped": contains_any(reply, S10_STOP_CUES)}
    if held:
        return "FAIL", {**ev, "why": "the store still serves 'plays the cello in a community "
                                     "orchestra' as current (one-word change not superseded)"}
    if not ev["reply_says_stopped"]:
        return "FAIL", {**ev, "why": "the old fact is retired but the reply does not say they stopped"}
    return "PASS", ev


def short_confirmation(reply: str) -> dict:
    """One short sentence that confirms and does not narrate (the shape ask-to-remember promises)."""
    words = len((reply or "").split())
    sentences = len([x for x in re.split(r"(?<=[.!?])\s+", (reply or "").strip()) if x])
    low = normalize(reply)
    return {"words": words, "sentences": sentences,
            "confirms": contains_any(low, ("remember", "got it", "noted", "already got")),
            "narrates": contains_any(low, S11_NARRATION),
            "short": 0 < words <= S11_MAX_WORDS and sentences <= 1}


def score_s11(t: dict) -> tuple[str, dict]:
    """The whole ask-to-remember contract from the turns' replies and the recall packets:

    ``t`` keys - say_tea / say_keep (replies), packet_after_say / packet_after_keep / packet_final (recall
    packets, None = the read failed), count_once / count_repeat (rows in the packet after the ask / after the
    repeat), repeat_reply, tea_reply (a FRESH session's answer), recall_reply, forget_reply.
    ERROR when a packet could not be read (a store that was not inspected is never certified)."""
    for k in ("packet_after_say", "packet_after_keep", "packet_final"):
        if t.get(k) is None:
            return "ERROR", {"method": "deterministic", "why": f"recall packet read failed ({k})"}
    conf_tea, conf_keep = short_confirmation(t["say_tea"]), short_confirmation(t["say_keep"])
    why = []
    for name, c in (("tea", conf_tea), ("coriander", conf_keep)):
        if not (c["confirms"] and c["short"]) or c["narrates"]:
            why.append(f"the {name} ask is not confirmed in one short sentence ({c})")
    if "lapsang" not in normalize(t["packet_after_say"]):
        why.append("'I'll remember' with no row: the tea is not in the recall packet")
    if "coriander" not in normalize(t["packet_after_keep"]):
        why.append("'I'll remember' with no row: the coriander is not in the recall packet")
    n1, n2 = t.get("count_once"), t.get("count_repeat")
    if n1 is None or n2 is None:
        why.append("the row count could not be read, so idempotency is unproven")
    elif n2 != n1:
        why.append(f"a repeat of the same ask added rows ({n1} -> {n2})")
    if not short_confirmation(t["repeat_reply"])["confirms"]:
        why.append("the repeat was not acknowledged")
    if "lapsang" not in normalize(t["tea_reply"]):
        why.append("a fresh session does not recall the tea")
    if len(found_needles(t["recall_reply"], S11_NEEDLES)) < len(S11_NEEDLES):
        why.append("'do you remember what I asked you to remember?' did not give both back")
    final = normalize(t["packet_final"])
    if "coriander" in final:
        why.append("'Forget that' left the coriander in the recall packet")
    if "lapsang" not in final:
        why.append("'Forget that' also removed the tea (it must retract only the newest)")
    if not contains_any(t["forget_reply"], ("forgot", "forgotten")):
        why.append("'Forget that' did not say it forgot")
    ev = {"method": "deterministic", "confirm_tea": conf_tea, "confirm_keep": conf_keep,
          "rows_after_ask": n1, "rows_after_repeat": n2}
    return ("FAIL", {**ev, "why": "; ".join(why)}) if why else ("PASS", ev)


def score_s12(rows: list[dict], s1: str, s2: str) -> tuple[str, dict]:
    """rows: proactive_candidates ({surfaced, session}) after S5's two open turns.
    PASS iff no candidate was surfaced in the second session; SKIP when nothing was
    raised in the first, or fewer than 2 candidates exist (spacing not exercised)."""
    sessions = {r.get("session") for r in rows if int(r.get("surfaced") or 0) > 0}
    ev = {"method": "deterministic", "candidates": len(rows),
          "raised_open_1": s1 in sessions, "raised_open_2": s2 in sessions}
    if len(rows) < 2 and s2 not in sessions:
        return "SKIP", {**ev, "why": "vacuous: fewer than 2 candidates, so nothing else could open the "
                                     "second conversation"}
    if s2 in sessions:
        return "FAIL", {**ev, "why": "the second open conversation, minutes later, also opened with "
                                     "a raise (the per-member gap, #1801 ZOE_PROACTIVE_RAISE_GAP_S, did not hold)"}
    if s1 not in sessions:
        return "SKIP", {**ev, "why": "nothing was raised on the first open turn — spacing not exercised"}
    return "PASS", ev


def score_s13(reply: str) -> tuple[str, dict]:
    """List-all: both seeded people named, and not the name-search miss."""
    r = normalize(reply)
    miss = "no contacts found" in r or "couldn't find anyone" in r
    found = found_needles(reply, ("ottoline", "percival"))
    ev = {"method": "deterministic", "found": found, "name_search_miss": miss}
    return ("PASS" if not miss and len(found) == 2 else "FAIL"), ev


def score_s14(reply: str) -> tuple[str, dict]:
    """The relation lives in the relationship field: 'brother' is said, the
    phrase is not the name, and the old default 'friend' is not."""
    r = normalize(reply)
    ev = {"method": "deterministic", "brother": "brother" in r,
          "relation_in_name": "my brother percival" in r, "friend": "friend" in r}
    ok = "percival" in r and ev["brother"] and not ev["relation_in_name"] and not ev["friend"]
    return ("PASS" if ok else "FAIL"), ev


def score_s15(reply: str) -> tuple[str, dict]:
    """One Ottoline: the fuller record is named and no bare first-name entry
    sits next to it (the old `Found: - Ottoline (friend) - Ottoline Fenwick (friend)`)."""
    r = normalize(reply)
    bare = re.search(r"\bottoline\b(?!\s+fenwick)", r) is not None
    ev = {"method": "deterministic", "fuller_named": "fenwick" in r, "bare_duplicate": bare}
    return ("PASS" if ev["fuller_named"] and not bare else "FAIL"), ev


def score_s16(offer_reply: str, yes_reply: str) -> tuple[str, dict]:
    """ONE question naming >= 2 of the family, then a yes that adds them all.
    No enumerated offer at all = SKIP (the dark default), never a pass."""
    named = found_needles(offer_reply, FAMILY_NAMES)
    ev = {"method": "deterministic", "offer_names": named, "offer_questions": offer_reply.count("?")}
    if len(named) < 2:
        return "SKIP", {**ev, "why": S16_WHY}
    if offer_reply.count("?") != 1:
        return "FAIL", {**ev, "why": "the offer was not ONE question"}
    added = found_needles(yes_reply, named)
    ev["added_names"] = added
    ok = len(added) == len(named) and re.search(r"\badded\b", normalize(yes_reply)) is not None
    return ("PASS" if ok else "FAIL"), ev


def score_s20(reply: str, packet: str | None) -> tuple[str, dict]:
    """Store AND reply: the recall packet must carry 7 August (and no month-first reading or
    raw digits), and the reply must say August without saying July."""
    if packet is None:
        return "ERROR", {"method": "deterministic", "why": "recall packet read failed"}
    ev = {"method": "deterministic",
          "reply_august": contains_any(reply, S20_RIGHT), "reply_july": contains_any(reply, ("july",)),
          "store_august": contains_any(packet, S20_RIGHT),
          "store_month_first": contains_any(packet, S20_WRONG)}
    why = []
    if not ev["store_august"] or ev["store_month_first"]:
        why.append("the store holds the birthday month-first or as raw digits")
    if not ev["reply_august"] or ev["reply_july"]:
        why.append("the reply does not give 7 August (or says July)")
    return ("FAIL", {**ev, "why": "; ".join(why)}) if why else ("PASS", ev)


def score_s21(ack: str, reply: str, packet: str | None) -> tuple[str, dict]:
    """The correction turn says what changed, the store holds a pet (not a child), and the
    count of the children leaves Biscuit out (Mika alone)."""
    if packet is None:
        return "ERROR", {"method": "deterministic", "why": "recall packet read failed"}
    a = normalize(ack)
    pet_lines = [ln for ln in packet.splitlines() if contains_any(ln, ("biscuit",))]
    still_child = [ln for ln in pet_lines
                   if re.search(r"\b(child|children|kid|kids|son|daughter)\b", normalize(ln))
                   and not re.search(r"\b(pet|dog|not a child)\b", normalize(ln))]
    ev = {"method": "deterministic",
          "ack_says_what_changed": a.startswith("fixed") and contains_any(ack, ("dog", "pet")),
          "ack_promises_next_time": "next time" in a,
          "store_has_pet": any(contains_any(ln, ("pet", "dog")) for ln in pet_lines),
          "store_still_child": bool(still_child),
          "reply_counts_two": bool(re.search(r"\b(two|2)\s+(kids|children)\b", normalize(reply))),
          "reply_names_mika": contains_any(reply, ("mika",))}
    why = []
    if not ev["ack_says_what_changed"] or ev["ack_promises_next_time"]:
        why.append("the correction turn did not state what was changed")
    if not ev["store_has_pet"] or ev["store_still_child"]:
        why.append("the store still lists Biscuit as a child (or never records a pet)")
    if ev["reply_counts_two"] or not ev["reply_names_mika"]:
        why.append("the count of the children still includes Biscuit")
    return ("FAIL", {**ev, "why": "; ".join(why)}) if why else ("PASS", ev)


def names_a_role(text: str, names: Iterable[str], roles: Iterable[str], window: int = 5) -> list[str]:
    """Role words within ``window`` words of a roster first name — a role ASSIGNED to a name."""
    toks = normalize(text).split()
    names, roles = {normalize(n) for n in names}, {normalize(r) for r in roles}
    hits = []
    for i, t in enumerate(toks):
        if t in names:
            near = toks[max(0, i - window): i + window + 1]
            hits += [f"{t}~{r}" for r in near if r in roles]
    return hits


def score_s22(roster_reply: str, ask_reply: str, packet: str | None) -> tuple[str, dict]:
    """No gendered role reaches a roster name in either reply or the store, and the list is
    answered with a question (who's who), not a verdict."""
    if packet is None:
        return "ERROR", {"method": "deterministic", "why": "recall packet read failed"}
    ev = {"method": "deterministic",
          "roles_in_roster_reply": names_a_role(roster_reply, S22_NAMES, S22_ROLES),
          "roles_in_ask_reply": names_a_role(ask_reply, S22_NAMES, S22_ROLES),
          "roles_in_store": names_a_role(packet, S22_NAMES, S22_ROLES),
          "asks_a_question": "?" in roster_reply}
    why = []
    if ev["roles_in_roster_reply"] or ev["roles_in_ask_reply"] or ev["roles_in_store"]:
        why.append("a role was assigned to a name the user never gave one")
    if not ev["asks_a_question"]:
        why.append("the list was not answered with a who's-who question")
    return ("FAIL", {**ev, "why": "; ".join(why)}) if why else ("PASS", ev)


def setup_problems(seeds: dict[str, dict | None], landings: dict[str, dict | None]) -> list[str]:
    """Why a scenario's PRECONDITIONS did not happen: a seed turn that was never
    sent or errored, or a fact that never landed in the recall packet. Any
    problem makes the scenario ERROR — a verdict on an unexercised setup is
    not evidence (S2 without the Dunedin fact tests no supersession; S7
    without the short duplicate tests no dedup)."""
    problems: list[str] = []
    for tag, t in seeds.items():
        if t is None:
            problems.append(f"seed turn {tag} was never sent")
        elif t.get("error"):
            problems.append(f"seed turn {tag} failed: {t['error']}")
    for name, l in landings.items():
        if not (l or {}).get("landed"):
            if (l or {}).get("kind") == "capture":
                problems.append(f"{name}: the turn's memory capture never completed "
                                f"({(l or {}).get('why', 'timeout')})")
            elif (l or {}).get("kind") == "backdate":
                problems.append(f"{name}: day-1 backdate incomplete "
                                f"({(l or {}).get('verified_sessions', 0)}/{(l or {}).get('sessions', 0)} "
                                f"sessions verified, {(l or {}).get('turns', 0)} turns; "
                                f"missing={(l or {}).get('missing', [])})")
            else:
                problems.append(f"{name} never landed in the recall packet")
    return problems


def score_s8(reply_sister: str, reply_dad: str, filler_errors: int = 0) -> tuple[str, dict]:
    s = found_needles(reply_sister, ("marisol",))
    d = found_needles(reply_dad, ("lighthouse",))
    ev = {"method": "deterministic", "sister_found": s, "dad_found": d,
          "filler_errors": filler_errors}
    if filler_errors:
        # The long history was not actually built: the recall proves nothing. ERROR, never PASS.
        return "ERROR", {**ev, "why": f"{filler_errors} filler turn(s) failed — history not exercised"}
    return ("PASS" if s and d else "FAIL"), ev


# ─────────────────────────────────────────────────────────────────────────────
# Baseline compare (pure)
# ─────────────────────────────────────────────────────────────────────────────

def verdict_map(results: list[dict]) -> dict[str, str]:
    return {r["id"]: r["verdict"] for r in results}


def load_baseline(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Read a baseline file. Returns (baseline, None) or (None, problem).

    A usable baseline is a JSON object with a ``scenarios`` mapping (what
    ``make_baseline`` writes). Missing, unreadable, malformed, or the wrong
    shape all come back as a named problem so compare mode can REFUSE rather
    than silently compare against nothing (a None baseline is never red)."""
    if not path.exists():
        return None, f"no baseline at {path} — run --record-baseline first"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as e:
        return None, f"baseline {path} unreadable: {e}"
    except json.JSONDecodeError as e:
        return None, f"baseline {path} is not valid JSON ({e.msg} at line {e.lineno})"
    if not isinstance(data, dict) or not isinstance(data.get("scenarios"), dict):
        return None, f"baseline {path} has no 'scenarios' object — re-record it with --record-baseline"
    scen = data["scenarios"]
    bad = sorted(str(k) for k, v in scen.items() if v not in VERDICTS)
    if not scen or bad:
        what = "no scenario verdicts" if not scen else f"unrecognised verdict(s) for {', '.join(bad)}"
        return None, f"baseline {path} holds {what} — re-record it with --record-baseline"
    samples = data.get("samples", 1)
    if isinstance(samples, bool) or not isinstance(samples, int) or samples < 1 or samples % 2 == 0:
        return None, (f"baseline {path} has a malformed 'samples' ({samples!r}; must be a positive "
                      "odd integer) — re-record it with --record-baseline")
    return data, None


def compare_baseline(current: dict[str, str], baseline: dict[str, Any] | None) -> dict[str, Any]:
    """Only a previously PASSING scenario that is no longer PASS is red.

    Not-PASS includes SKIP and ERROR: a skip is not a pass, so a scenario that
    used to prove something and now proves nothing is a regression too. A
    scenario that was never passing cannot regress; one that newly passes is an
    improvement (re-record the baseline to lock it in)."""
    base = (baseline or {}).get("scenarios") or {}
    regressions = sorted(k for k, v in base.items() if v == "PASS" and current.get(k) != "PASS")
    improvements = sorted(k for k, v in current.items() if v == "PASS" and base.get(k, "") != "PASS"
                          and k in base)
    new = sorted(k for k in current if k not in base)
    notes = []
    if baseline and baseline.get("judge_prompt_sha256") not in (None, JUDGE_PROMPT_SHA256):
        notes.append("judge rubric changed since the baseline — judged verdicts are not comparable")
    if base and "PASS" not in base.values():
        notes.append("baseline holds no PASS verdict — nothing can regress against it")
    return {"has_baseline": bool(base), "regressions": regressions,
            "improvements": improvements, "new": new, "red": bool(regressions), "notes": notes}


def restrict_baseline(baseline: dict[str, Any] | None, selected: "frozenset[str] | None") -> dict[str, Any] | None:
    """The baseline narrowed to the scenarios a partial run executed (the rest cannot regress
    in a run that never ran them)."""
    if baseline is None or selected is None:
        return baseline
    return {**baseline, "scenarios": {k: v for k, v in (baseline.get("scenarios") or {}).items()
                                      if k in selected}}


def make_baseline(results: list[dict], revision: dict | None, samples: int) -> dict[str, Any]:
    return {"harness_version": HARNESS_VERSION,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "revision": revision, "judge_prompt_sha256": JUDGE_PROMPT_SHA256,
            "samples": samples, "scenarios": verdict_map(results)}


# ─────────────────────────────────────────────────────────────────────────────
# Guards (pure where possible)
# ─────────────────────────────────────────────────────────────────────────────

def assert_demo_user(uid: str) -> str:
    if not DEMO_USER_RE.match(uid or ""):
        raise ValueError(f"refusing non-demo identity {uid!r} (must match {DEMO_USER_RE.pattern})")
    return uid


def new_demo_user() -> str:
    return assert_demo_user("demo_bar_" + secrets.token_hex(4))


def in_nightly_window(now: dt.datetime, guard_min: int = NIGHTLY_GUARD_MIN) -> bool:
    """True inside 01:45–03:15 local, or within ``guard_min`` minutes before it."""
    (sh, sm), (eh, em) = NIGHTLY_WINDOW
    minute = now.hour * 60 + now.minute
    start, end = sh * 60 + sm - guard_min, eh * 60 + em
    return start <= minute < end


def mem_available_mb() -> int:
    try:
        with open("/proc/meminfo") as fh:
            for line in fh:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        pass
    return 0


def deploy_in_progress(runner: Callable[..., Any] = subprocess.run) -> tuple[bool | None, str]:
    """(True/False, detail), or (None, why) when gh cannot answer — the caller
    refuses on None: an unknown deploy state is not a quiet box."""
    found = []
    for status in ("in_progress", "queued"):
        try:
            p = runner(["gh", "run", "list", "--workflow", "deploy.yml", "--status", status,
                        "--limit", "5", "--json", "databaseId"],
                       capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"gh unavailable: {type(exc).__name__}"
        if p.returncode != 0:
            return None, f"gh run list failed: {(p.stderr or '').strip()[:120]}"
        try:
            found += json.loads(p.stdout or "[]")
        except json.JSONDecodeError:
            return None, "gh returned non-JSON"
    return bool(found), f"{len(found)} deploy.yml run(s) queued/in progress"


def service_revision(service_dir: Path | None) -> dict[str, Any] | None:
    """Commit of the checkout the live service runs from — same shape and the
    same fail-closed dirty rule as voice_regression_probe.service_revision."""
    if not service_dir or not Path(service_dir).exists():
        return None

    def _git(*a: str) -> str | None:
        try:
            p = subprocess.run(["git", "-C", str(service_dir), *a],
                               capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            return None
        return p.stdout.strip() if p.returncode == 0 else None

    commit = _git("rev-parse", "HEAD")
    if not commit:
        return None
    status = _git("status", "--porcelain")
    clean_verified = status is not None
    return {"commit": commit, "tree": _git("rev-parse", "HEAD^{tree}"),
            "dirty": True if not clean_verified else bool(status),
            "clean_verified": clean_verified, "service_dir": str(service_dir)}


def env_file_value(service_dir: Path, key: str) -> str:
    """One key from the live service .env (the voice probe's DSN pattern). Never logged."""
    val = os.environ.get(key, "")
    if val:
        return val
    try:
        for raw in (Path(service_dir) / ".env").read_text(encoding="utf-8").splitlines():
            if raw.startswith(f"{key}="):
                return raw.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Plan (dry-run)
# ─────────────────────────────────────────────────────────────────────────────

def plan_text(samples: int, selected: "frozenset[str] | None" = None) -> str:
    lines = [f"samantha_bar v{HARNESS_VERSION} — plan (no network)",
             f"  identities: demo A + demo B, each ^demo_bar_[0-9a-f]{{8}}$ (fresh per run)",
             f"  judged scenarios ask {samples}x, majority vote; judge rubric sha {JUDGE_PROMPT_SHA256[:12]}",
             "  order: day 1 (S1 seed+ask, S2/S4/S7 seeds) -> backdate day-1 sessions 26h ->",
             "         day 2 (S2 move+ask, S7 short dup+ask, S10 'gave up'+ask, S4 ask, S3 ask) -> S5 selector"
         " hook + 2 open turns (S12 scores their spacing) -> S11 (ask-to-remember) -> S20/S21/S22 (dates, corrections, roles) -> S6 -> S8 -> contacts S13-S16"]
    need = seed_closure(selected) if selected is not None else None
    if selected is not None and selected != frozenset(SCENARIO_IDS):
        lines.append(f"  PARTIAL RUN (--only/--axis): {', '.join(s for s in SCENARIO_IDS if s in selected)}"
                     " — status=partial, never records a baseline; --compare-baseline compares only these")
    for s in SCENARIOS:
        if selected is not None and s["id"] not in need:
            continue
        tag = "judged" if s["judged"] else "deterministic"
        exp = f", expected {s['expected']}" if s.get("expected") else ""
        seed_only = " (seed turns only: a selected scenario reads them)" \
            if selected is not None and s["id"] not in selected else ""
        lines.append(f"  {s['id']} {s['title']} [{tag}{exp}]: {len(s['turns'])} seed turn(s), "
                     f"{len(s['asks'])} question(s) — {s['proves']}{seed_only}")
    lines += ["  teardown: forget-synthetic (or admin forget) + residual == 0 + /for-prompt == 0, Postgres rows by",
              "            exact demo id / session id == 0 — runs in finally, asserted, exit 2 if unproven",
              f"  gates: ZOE_PERF=1, {LOCK}, not {NIGHTLY_WINDOW[0][0]:02d}:{NIGHTLY_WINDOW[0][1]:02d}-"
              f"{NIGHTLY_WINDOW[1][0]:02d}:{NIGHTLY_WINDOW[1][1]:02d} (-{NIGHTLY_GUARD_MIN}m), no deploy.yml run, "
              f"/readyz memory_capture ok, MemAvailable>={MIN_MEM_MB}MB, nice>=5"]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Live client
# ─────────────────────────────────────────────────────────────────────────────

class Live:
    def __init__(self, token: str, admin_session: str, dsn: str, keep_replies: bool):
        self.token, self.admin, self.dsn, self.keep = token, admin_session, dsn, keep_replies
        self.nonce = secrets.token_hex(3)
        self.sessions: dict[str, list[str]] = {}

    # HTTP ------------------------------------------------------------------
    def _req(self, method: str, url: str, headers: dict, body: dict | None = None,
             timeout: float = 60) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read().decode("utf-8", "replace")
                return r.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as e:
            return e.code, {"_error": f"HTTP {e.code}"}
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
            return 0, {"_error": type(e).__name__}

    def session(self, user: str, tag: str) -> str:
        sid = f"bar-{tag}-{self.nonce}"
        lst = self.sessions.setdefault(user, [])
        if sid not in lst:
            lst.append(sid)
        return sid

    def chat(self, user: str, tag: str, message: str) -> dict[str, Any]:
        assert_demo_user(user)
        sid = self.session(user, tag)
        t0 = time.monotonic()
        code, body = self._req("POST", f"{DATA_BASE}/api/chat/?stream=false",
                               {"X-Internal-Token": self.token, "X-Zoe-User-Id": user},
                               {"message": message, "session_id": sid, "stream": False},
                               timeout=240)
        reply = (body or {}).get("response") or ""
        err = None
        if code != 200:
            err = (body or {}).get("_error") or f"HTTP {code}"
        elif not reply:
            err = "empty reply"
        elif is_brain_fallback(reply):
            err = "brain fallback reply"
        return {"reply": reply, "error": err, "ms": int((time.monotonic() - t0) * 1000),
                "session": sid}

    def packet(self, user: str, message: str) -> str | None:
        """The recall packet the brain would get (/for-prompt, internal).

        None when the read FAILED (non-200) — distinct from "" (an empty packet),
        so a scenario can never pass on a packet it did not actually inspect."""
        assert_demo_user(user)
        q = urllib.parse.urlencode({"user_id": user, "message": message[:900], "limit": 40})
        code, body = self._req("GET", f"{DATA_BASE}/api/memories/for-prompt?{q}",
                               {"X-Internal-Token": self.token})
        if code != 200:
            return None
        return str((body or {}).get("packet") or "")

    def capture_status(self, user: str) -> dict[str, Any] | None:
        """Per-user post-turn capture counters (/capture-status, internal). None
        when the endpoint is unavailable (old server / token refused)."""
        assert_demo_user(user)
        q = urllib.parse.urlencode({"user_id": user})
        code, body = self._req("GET", f"{DATA_BASE}/api/memories/capture-status?{q}",
                               {"X-Internal-Token": self.token})
        if code != 200 or not isinstance(body, dict) or "completed" not in body:
            return None
        return body

    def wait_captured(self, user: str, before: dict[str, Any] | None,
                      timeout_s: float = 180) -> dict[str, Any]:
        """Observable completion of a turn's background memory capture: the
        user's ``completed`` counter has advanced past ``before`` and nothing is
        in flight. The chat route schedules capture with ensure_future, so the
        HTTP turn returning proves nothing — and a deduplicated candidate never
        becomes a visible row, so the packet cannot be polled for it."""
        t0 = time.monotonic()
        if before is None:
            return {"landed": False, "kind": "capture", "waited_s": 0.0,
                    "why": "capture-status unavailable before the turn"}
        while True:
            st = self.capture_status(user)
            waited = round(time.monotonic() - t0, 1)
            if st and st["completed"] > before["completed"] and not st.get("in_flight"):
                failed_since = int(st.get("failed") or 0) - int(before.get("failed") or 0)
                if failed_since > 0:
                    # Completed, but a memory pass raised: the duplicate was NOT
                    # captured. Never landed — S7 must not score it.
                    return {"landed": False, "kind": "capture", "waited_s": waited,
                            "completed": st["completed"], "failed": st.get("failed"),
                            "why": f"capture FAILED ({failed_since} memory pass failure(s) "
                                   "since the turn)"}
                return {"landed": True, "kind": "capture", "waited_s": waited,
                        "completed": st["completed"], "failed": st.get("failed")}
            if time.monotonic() - t0 > timeout_s:
                return {"landed": False, "kind": "capture", "waited_s": waited,
                        "why": "capture-status unavailable" if st is None else
                               f"capture not completed (completed={st['completed']}, "
                               f"in_flight={st.get('in_flight')})"}
            time.sleep(3)

    def packet_count(self, user: str) -> int | None:
        q = urllib.parse.urlencode({"user_id": user, "message": "", "limit": 40})
        code, body = self._req("GET", f"{DATA_BASE}/api/memories/for-prompt?{q}",
                               {"X-Internal-Token": self.token})
        return int((body or {}).get("count") or 0) if code == 200 else None

    @property
    def forget_mode(self) -> str:
        return "admin" if self.admin else "synthetic"

    def residual_count(self, user: str) -> int | None:
        """Memory rows (any status) still owned by the user after the forget.

        admin mode: the admin export. synthetic mode: a SECOND forget-synthetic —
        ``delete_user`` lists every row owned by the id, so ``removed == 0`` proves
        nothing was left (and a late row is removed rather than leaked)."""
        if self.forget_mode == "admin":
            return self.export_count(user)
        return self.forget(user)

    def forget_ok(self) -> bool:
        """Preflight: can this run erase memory rows? Probes with a fresh, unused
        demo id, so the call deletes nothing."""
        if self.forget_mode == "admin":
            return self.admin_ok()
        return self.forget(new_demo_user()) == 0

    def export_count(self, user: str) -> int | None:
        """Every memory row for the user, any status (admin export)."""
        code, body = self._req("GET", f"{DATA_BASE}/api/memories/export?user_id={user}",
                               {"X-Session-ID": self.admin}, timeout=120)
        if code != 200 or not isinstance(body, dict):
            return None
        return count_export_rows(body)

    def admin_ok(self) -> bool:
        if not self.admin:
            return False
        code, _ = self._req("GET", f"{DATA_BASE}/api/memories/export?user_id={new_demo_user()}",
                            {"X-Session-ID": self.admin}, timeout=60)
        return code == 200

    def forget(self, user: str) -> int | None:
        assert_demo_user(user)
        if self.forget_mode == "admin":
            code, body = self._req("POST", f"{DATA_BASE}/api/memories/users/{user}/forget",
                                   {"X-Session-ID": self.admin}, {}, timeout=120)
        else:
            code, body = self._req("POST", f"{DATA_BASE}/api/memories/users/{user}/forget-synthetic",
                                   {"X-Internal-Token": self.token}, {}, timeout=120)
        return int((body or {}).get("removed") or 0) if code == 200 else None

    def wait_landed(self, user: str, message: str, needles: Iterable[str],
                    timeout_s: float = 90) -> dict[str, Any]:
        t0 = time.monotonic()
        needles = list(needles)
        while True:
            hit = found_needles(self.packet(user, message) or "", needles)
            waited = round(time.monotonic() - t0, 1)
            if len(hit) == len(needles):
                return {"landed": True, "waited_s": waited}
            if waited >= timeout_s:
                return {"landed": False, "waited_s": waited, "found": hit}
            time.sleep(5)

    def judge(self, scenario_id: str, user_said: str, reply: str) -> tuple[str, str]:
        payload = {"model": "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf",
                   "messages": build_judge_messages(scenario_id, user_said, reply),
                   "temperature": 0, "top_k": 1, "seed": 0, "max_tokens": 120,
                   "stream": False}
        code, body = self._req("POST", f"{BRAIN_BASE}/v1/chat/completions", {}, payload,
                               timeout=120)
        if code != 200:
            return "ERROR", f"judge HTTP {code}"
        try:
            msg = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            return "ERROR", "judge returned no choice"
        return parse_judge_verdict(msg.get("content") or msg.get("reasoning_content") or "")

    def evidence(self, turn: dict[str, Any]) -> dict[str, Any]:
        """Per-ask evidence: never the raw reply unless --keep-replies."""
        ev = {"session": turn["session"], "ms": turn["ms"], "error": turn["error"],
              "reply_len": len(turn["reply"]),
              "reply_sha": hashlib.sha256(turn["reply"].encode()).hexdigest()[:12]}
        if self.keep:
            ev["reply_excerpt"] = turn["reply"][:240]
        return ev

    # Postgres (never the memory store) --------------------------------------
    def db(self, fn: Callable[[Any], Any]) -> Any:
        import asyncpg  # lazy: the pure functions and --dry-run need no driver

        async def _run():
            conn = await asyncpg.connect(self.dsn, timeout=15)
            try:
                return await fn(conn)
            finally:
                await conn.close()
        return asyncio.run(_run())

    def backdate(self, session_ids: list[str], age_s: int) -> dict[str, Any]:
        """samantha_live's set_session_age, on OUR session ids only — VERIFIED.

        Returns {"ok", "sessions", "verified_sessions", "turns", "missing"}: ok
        only when every session id has its session row AND at least one message
        row re-read at the backdated timestamp. A partial or empty backdate
        (chat persistence regressed, a row missing) would otherwise leave
        S2/S4/S7 running as same-day scenarios while claiming multi-day."""
        created = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=age_s)).isoformat()
        for sid in session_ids:
            if not sid.startswith("bar-"):
                raise ValueError(f"refusing to backdate foreign session {sid!r}")

        async def _f(conn):
            n = 0
            for sid in session_ids:
                r = await conn.execute("UPDATE chat_messages SET created_at = $1 WHERE session_id = $2",
                                       created, sid)
                n += int(r.split()[-1])
                await conn.execute("UPDATE chat_sessions SET created_at = $1, updated_at = $1 "
                                   "WHERE id = $2", created, sid)
            # Re-read: the exact value we wrote (created_at is TEXT).
            msg_rows = {r["session_id"]: int(r["n"]) for r in await conn.fetch(
                "SELECT session_id, count(*) AS n FROM chat_messages "
                "WHERE session_id = ANY($1::text[]) AND created_at = $2 GROUP BY session_id",
                session_ids, created)}
            sess_rows = {r["id"] for r in await conn.fetch(
                "SELECT id FROM chat_sessions WHERE id = ANY($1::text[]) AND created_at = $2",
                session_ids, created)}
            missing = [s for s in session_ids if s not in sess_rows or msg_rows.get(s, 0) < 1]
            return {"ok": bool(session_ids) and not missing, "sessions": len(session_ids),
                    "verified_sessions": len(session_ids) - len(missing), "turns": n,
                    "missing": missing}
        return self.db(_f)

    def run_selector(self, user: str) -> dict | None:
        """The S5 hook: POST /api/proactive/selector/run-synthetic (internal token;
        the server refuses any id that is not harness-minted). None = no route."""
        assert_demo_user(user)
        code, body = self._req("POST", f"{DATA_BASE}/api/proactive/selector/run-synthetic/{user}",
                               {"X-Internal-Token": self.token}, {}, timeout=180)
        return body if code == 200 and isinstance(body, dict) else None

    def raise_state(self, user: str) -> list[dict]:
        assert_demo_user(user)

        async def _f(conn):
            rows = await conn.fetch("SELECT kind, text, surfaced_count, last_surfaced_session "
                                    "FROM proactive_candidates WHERE user_id = $1", user)
            return [{"kind": r["kind"], "carries": contains_any(r["text"] or "", S5_NEEDLES),
                     "surfaced": r["surfaced_count"], "session": r["last_surfaced_session"]}
                    for r in rows]
        return self.db(_f)

    def proactive_hooks(self, user: str) -> list[dict]:
        assert_demo_user(user)

        async def _f(conn):
            rows = await conn.fetch("SELECT trigger_type, message FROM proactive_pending "
                                    "WHERE user_id = $1", user)
            return [dict(r) for r in rows]
        return self.db(_f)


def count_export_rows(body: dict) -> int | None:
    """Rows in an admin export ({count, items}); None when the shape is unknown —
    an unreadable count is never read as zero."""
    items = body.get("items")
    if isinstance(items, list):
        return max(len(items), int(body.get("count") or 0))
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Teardown — the load-bearing part
# ─────────────────────────────────────────────────────────────────────────────

class _RegisteredDuringSweep(Exception):
    """Raised inside the sweep transaction to roll it back: an id became a
    registered account between the pre-check and the deletes."""


async def db_teardown(conn, users: list[str], sessions: list[str]) -> dict[str, Any]:
    """Delete every Postgres row the run created, by exact demo id / session id,
    then COUNT them back. Returns {"deleted": {...}, "remaining": {...},
    "skipped_registered": [...], "rolled_back": bool}.

    The whole sweep is ONE transaction: pre-check auth_users → every DELETE →
    re-read auth_users for the same ids. An id that registered in between makes
    the transaction ROLL BACK (nothing committed, the id reported), so a race
    between registration and teardown can never cost an account its rows."""
    for u in users:
        assert_demo_user(u)
    for s in sessions:
        if not s.startswith("bar-"):
            raise ValueError(f"refusing foreign session {s!r}")
    deleted: dict[str, int] = {}

    def _n(tag: str) -> int:
        return int(tag.split()[-1]) if tag and tag.split()[-1].isdigit() else 0

    async def _exists(table: str) -> bool:
        return await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"public.{table}")

    # A demo-SHAPED id can still be a registered account (Zoe Auth ids are
    # usernames; one may be created between mint and teardown). Re-verify against
    # auth_users — the account store — immediately before ANY delete, with the
    # same semantics as /forget-synthetic: registered → skipped (and reported),
    # lookup impossible → the whole sweep is refused (fail closed).
    if not await _exists("auth_users"):
        raise RuntimeError("auth_users not found: cannot verify the demo ids are unregistered "
                           "— sweep refused")

    async def _registered(ids: list[str]) -> list[str]:
        return sorted({str(r["user_id"]) for r in await conn.fetch(
            "SELECT user_id FROM auth_users WHERE user_id::text = ANY($1::text[])", ids)})

    all_users, tables, rolled_back = list(users), [], False
    late: list[str] = []
    try:
        async with conn.transaction():
            registered = await _registered(users)
            if registered:
                print(f"  db teardown: NOT sweeping registered account(s) {registered}",
                      file=sys.stderr)
                users = [u for u in users if u not in registered]

            # Chat rows: every session OWNED by a demo user (covers a killed run whose
            # session list was never written), plus the messages in them.
            own = [r["id"] for r in await conn.fetch(
                "SELECT id FROM chat_sessions WHERE user_id = ANY($1::text[])", users)]
            deleted["chat_messages"] = _n(await conn.execute(
                "DELETE FROM chat_messages WHERE session_id = ANY($1::text[])", own))
            if await _exists("memory_consolidation_state"):
                deleted["memory_consolidation_state"] = _n(await conn.execute(
                    "DELETE FROM memory_consolidation_state WHERE session_id = ANY($1::text[])", own))
            deleted["chat_sessions"] = _n(await conn.execute(
                "DELETE FROM chat_sessions WHERE id = ANY($1::text[])", own))
            # Every public base table with a user_id column, by exact demo id (compared as
            # text, so an integer user_id column cannot error the sweep). FK order is
            # unknown, so retry the tables a foreign key refused.
            tables = sorted({r["table_name"] for r in await conn.fetch(
                "SELECT c.table_name FROM information_schema.columns c JOIN information_schema.tables t "
                "ON t.table_schema = c.table_schema AND t.table_name = c.table_name "
                "WHERE c.table_schema = 'public' AND c.column_name = 'user_id' "
                "AND t.table_type = 'BASE TABLE'")} - AUTH_OWNED_TABLES)
            pending = list(tables)
            for _ in range(4):
                retry = []
                for t in pending:
                    try:
                        deleted[t] = deleted.get(t, 0) + _n(await conn.execute(
                            f'DELETE FROM "{t}" WHERE user_id::text = ANY($1::text[])', users))
                    except Exception as exc:  # noqa: BLE001 — FK order: retry next pass
                        if "foreign key" in str(exc).lower():
                            retry.append(t)
                        else:
                            raise
                if not retry:
                    break
                pending = retry
            deleted["users"] = _n(await conn.execute(
                "DELETE FROM users WHERE id = ANY($1::text[])", users))
            # The race: an account registered under one of these ids AFTER the
            # pre-check. Re-read inside the same transaction; any hit rolls back
            # every delete above.
            late = await _registered(users)
            if late:
                raise _RegisteredDuringSweep(late)
    except _RegisteredDuringSweep:
        rolled_back, deleted = True, {}
        registered = sorted(set(registered) | set(late))
        print(f"  db teardown: ROLLED BACK — registered during the sweep: {late}", file=sys.stderr)
    users = [u for u in all_users if u not in registered]

    remaining: dict[str, int] = {}
    for t in tables:
        remaining[t] = await conn.fetchval(
            f'SELECT count(*) FROM "{t}" WHERE user_id::text = ANY($1::text[])', users)
    remaining["chat_sessions"] = await conn.fetchval(
        "SELECT count(*) FROM chat_sessions WHERE id = ANY($1::text[]) OR user_id = ANY($2::text[])",
        sessions, users)
    remaining["chat_messages"] = await conn.fetchval(
        "SELECT count(*) FROM chat_messages WHERE session_id = ANY($1::text[]) "
        "OR metadata LIKE ANY($2::text[])", sessions, [f'%"{u}"%' for u in users])
    remaining["users"] = await conn.fetchval("SELECT count(*) FROM users WHERE id = ANY($1::text[])", users)
    return {"deleted": {k: v for k, v in deleted.items() if v}, "remaining": remaining,
            "skipped_registered": registered, "rolled_back": rolled_back}


def teardown_verdict(store: dict[str, dict], db: dict[str, Any] | None) -> tuple[bool, list[str]]:
    """Pure: is the teardown PROVEN? store = {user: {"residual": n|None, "packet": n|None}}.

    Unknown (None) is not zero — a count we could not read fails the proof."""
    problems = []
    for u, c in sorted(store.items()):
        for k in ("residual", "packet"):
            if c.get(k) is None:
                problems.append(f"{u}: {k} count unreadable")
            elif c[k]:
                problems.append(f"{u}: {c[k]} memory row(s) left ({k})")
    if db is None:
        problems.append("postgres teardown did not run")
    else:
        problems += [f"postgres {t}: {n} row(s) left" for t, n in sorted(db["remaining"].items()) if n]
        if db.get("skipped_registered"):
            problems.append("postgres sweep skipped registered account(s): "
                            + ", ".join(db["skipped_registered"]))
    return (not problems), problems


def teardown(live: Live, users: list[str], sessions: list[str]) -> dict[str, Any]:
    """Quiesce, forget, delete, then prove it — twice (a late turn-digest write
    can land after the first forget)."""
    for u in users:
        assert_demo_user(u)
    # Quiesce: each user's packet count stable AND no capture task in flight. Two
    # equal packet counts only prove the store was quiet for ten seconds — a turn
    # digest still computing would write a row after the forget and the proof
    # would be false. The capture counters are the real signal; unavailable or
    # nonzero after the wait = the teardown cannot be proven.
    quiesce: dict[str, dict[str, Any]] = {}
    for u in users:
        last, stable, in_flight = None, 0, None
        for _ in range(24):
            c = live.packet_count(u)
            st = live.capture_status(u)
            in_flight = int(st.get("in_flight") or 0) if st else None
            stable = stable + 1 if (c is not None and c == last) else 0
            last = c
            if stable >= 2 and in_flight == 0:
                break
            time.sleep(5)
        # Both signals are recorded; BOTH must have been observed for a proof.
        quiesce[u] = {"in_flight": in_flight, "packet": last, "stable": stable}
    quiesce_problems: list[str] = []
    for u, q in quiesce.items():
        if q["in_flight"] is None:
            quiesce_problems.append(f"{u}: capture status unavailable — quiescence cannot be proven")
        elif q["in_flight"] != 0:
            quiesce_problems.append(f"{u}: capture not quiescent (in_flight={q['in_flight']})")
        if q["packet"] is None:
            quiesce_problems.append(f"{u}: packet count unreadable — quiescence cannot be proven")
        elif q["stable"] < 2:
            quiesce_problems.append(f"{u}: packet count never stabilised before the forget "
                                    f"(stable={q['stable']}, last={q['packet']})")
    out: dict[str, Any] = {"users": users, "sessions": len(sessions), "rounds": [],
                           "forget_mode": getattr(live, "forget_mode", "?"), "quiesce": quiesce}
    db_res = None
    ok, problems = False, ["not attempted"]
    for rnd in range(2):
        removed = {u: live.forget(u) for u in users}
        try:
            db_res = live.db(lambda conn: db_teardown(conn, users, sessions))
        except Exception as exc:  # noqa: BLE001 — reported, never swallowed
            db_res = None
            out.setdefault("errors", []).append(f"postgres: {type(exc).__name__}: {str(exc)[:160]}")
        time.sleep(10 if rnd == 0 else 5)
        store = {u: {"residual": live.residual_count(u), "packet": live.packet_count(u)} for u in users}
        ok, problems = teardown_verdict(store, db_res)
        if quiesce_problems:  # cleanup still ran, but a count-back under live capture proves nothing
            ok, problems = False, problems + quiesce_problems
        out["rounds"].append({"forget_removed": removed, "store": store,
                              "db_deleted": (db_res or {}).get("deleted"), "problems": problems})
        if ok:
            break
    out["proven"], out["problems"] = ok, problems
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Scenario execution (live)
# ─────────────────────────────────────────────────────────────────────────────

def _ask_judged(live: Live, user: str, sid_tag: str, question: str, samples: int,
                scorer: Callable, scenario_id: str) -> tuple[str, dict]:
    votes, per = [], []
    for i in range(samples):
        turn = live.chat(user, f"{sid_tag}-s{i}", question)
        if turn["error"]:
            votes.append("ERROR")
            per.append({**live.evidence(turn), "verdict": "ERROR"})
            continue
        v, ev = scorer(turn["reply"], lambda r=turn["reply"]: live.judge(scenario_id, question, r))
        votes.append(v)
        per.append({**live.evidence(turn), **ev, "verdict": v})
    return majority_vote(votes), {"samples": per, "votes": votes}


def run_scenarios(live: Live, a: str, b: str, samples: int, backdate: bool,
                  log: Callable[[str], None],
                  selected: "frozenset[str] | set[str] | None" = None) -> list[dict]:
    """Run the bar. ``selected`` (None = all) restricts it to those scenario ids: only their asks
    are sent and only their verdicts are returned, but the SEED turns of any scenario they depend
    on (``SEED_DEPS``) still run, and the day-1 backdate runs only when a multi-day scenario is in
    play. The flow, the order and every scorer are otherwise identical to the full run."""
    res: dict[str, dict] = {}
    land: dict[str, dict] = {}
    seeds: dict[str, dict] = {}
    sel = frozenset(SCENARIO_IDS) if selected is None else frozenset(selected)
    need = seed_closure(sel)

    def ask(sid):   # send this scenario's asks and report its verdict
        return sid in sel

    def seed(sid):  # run this scenario's seed turns (it, or a scenario that reads them, is selected)
        return sid in need

    def say(user, tag, text):
        t = live.chat(user, tag, text)
        seeds[tag] = t
        if t["error"]:
            log(f"    seed turn error ({tag}): {t['error']}")
        return t

    def put(sid, verdict, **ev):
        if sid not in sel:  # an unselected scenario is never reported
            return
        res[sid] = {"id": sid, "verdict": verdict, "evidence": ev}
        if sid in EXPECTED:  # a target: its verdict is tracked, it is not a regression
            res[sid]["expected"] = EXPECTED[sid]
        log(f"  {sid}: {verdict}" + (f" (expected {EXPECTED[sid]})" if sid in EXPECTED else ""))

    def setup_ok(sid, seed_tags=(), land_keys=()):
        """False (and the scenario is ERROR) unless every seed turn succeeded and
        every fact landed — the ask is then not even sent."""
        landed = {k: land.get(k) for k in land_keys}
        problems = setup_problems({t: seeds.get(t) for t in seed_tags}, landed)
        if problems:
            put(sid, "ERROR", why="setup not exercised: " + "; ".join(problems),
                setup_problems=problems, landed=landed)
            return False
        return True

    if selected is not None:
        log(f"partial run: {', '.join(s for s in SCENARIO_IDS if s in sel)}"
            + (f" (+ seed turns for {', '.join(s for s in SCENARIO_IDS if s in need - sel)})"
               if need - sel else ""))

    # Day 1 ------------------------------------------------------------------
    log("day 1: S1 seed + same-day ask; S2/S4/S7 seeds")
    if seed("S1"):
        say(a, "d1-sister", SAY_SISTER)
        land["S1"] = live.wait_landed(a, ASK_SISTER, ("marisol",))
    if ask("S1") and setup_ok("S1", ("d1-sister",), ("S1",)):
        t = live.chat(a, "d1-ask-sister", ASK_SISTER)
        if t["error"]:
            put("S1", "ERROR", landed=land["S1"], ask=live.evidence(t))
        else:
            v, ev = score_s1(t["reply"])
            put("S1", v, landed=land["S1"], ask={**live.evidence(t), **ev})
    if seed("S2"):
        say(a, "d1-home", SAY_OLD_HOME)
        land["S2_old"] = live.wait_landed(a, ASK_HOME, ("dunedin",))
    if seed("S4"):
        say(a, "d1-worry", SAY_WORRY)
        land["S4"] = live.wait_landed(a, "feeling anxious interview", ("interview",))
    if seed("S7"):
        say(a, "d1-dad", SAY_DAD_RICH)
        land["S7_rich"] = live.wait_landed(a, ASK_DAD, ("lighthouse",))
    if seed("S10"):
        say(a, "d1-cello", SAY_CELLO)
        land["S10_old"] = live.wait_landed(a, ASK_CELLO, ("orchestra",))
    if not (need & MULTI_DAY):
        land["backdate"] = {"landed": True, "kind": "backdate", "skipped": True}  # nothing multi-day
    elif backdate:
        day1 = [s for s in live.sessions.get(a, []) if s.startswith("bar-d1-")]
        bd = live.backdate(day1, 26 * 3600) if day1 else {"ok": False, "sessions": 0,
                                                            "verified_sessions": 0, "turns": 0,
                                                            "missing": []}
        # The day boundary is a precondition of S2/S4/S5/S7: gate them on the
        # VERIFIED backdate, not on the update having been attempted.
        land["backdate"] = {"landed": bool(bd.get("ok")), "kind": "backdate", **bd}
        log(f"backdated {bd.get('verified_sessions')}/{bd.get('sessions')} day-1 session(s) "
            f"({bd.get('turns')} turns) by 26h ok={bd.get('ok')}")
    else:
        land["backdate"] = {"landed": True, "kind": "backdate", "skipped": True}  # diagnostic run

    # Day 2 ------------------------------------------------------------------
    log("day 2: S2 move + ask; S7 short dup + ask; S4; S3")
    if seed("S2"):
        say(a, "d2-home", SAY_NEW_HOME)
        land["S2_new"] = live.wait_landed(a, ASK_HOME, ("hobart",))
    # Supersession needs BOTH facts present: without Dunedin landed, a Hobart-only
    # reply proves nothing.
    if ask("S2") and setup_ok("S2", ("d1-home", "d2-home"), ("S2_old", "S2_new", "backdate")):
        v, ev = _ask_judged(live, a, "d2-ask-home", ASK_HOME, samples, score_s2, "S2")
        put("S2", v, landed={"old": land["S2_old"], "new": land["S2_new"]}, **ev)

    # The short duplicate is captured in the background (ensure_future) and, being
    # a duplicate, never becomes a visible row: wait for the capture COUNTER to
    # advance, not for time to pass. Not observed → S7 ERROR, never PASS.
    if seed("S7"):
        cap_before = live.capture_status(a)
        say(a, "d2-dad", SAY_DAD_SHORT)
        land["S7_dup"] = live.wait_captured(a, cap_before)
    if ask("S7") and setup_ok("S7", ("d1-dad", "d2-dad"), ("S7_rich", "S7_dup", "backdate")):
        t = live.chat(a, "d2-ask-dad", ASK_DAD)
        pkt = live.packet(a, ASK_DAD)
        if t["error"]:
            put("S7", "ERROR", landed=land["S7_rich"], ask=live.evidence(t))
        else:
            v, ev = score_s7(t["reply"], pkt)
            put("S7", v, landed=land["S7_rich"], ask={**live.evidence(t), **ev})

    # S10: the one-word change. Its capture is background too: wait on the counter.
    if seed("S10"):
        cap_before = live.capture_status(a)
        say(a, "d2-cello", SAY_CELLO_STOP)
        land["S10_stop"] = live.wait_captured(a, cap_before)
    if ask("S10") and setup_ok("S10", ("d1-cello", "d2-cello"), ("S10_old", "S10_stop", "backdate")):
        t = live.chat(a, "d2-ask-cello", ASK_CELLO)
        pkt = live.packet(a, ASK_CELLO)
        if t["error"]:
            put("S10", "ERROR", ask=live.evidence(t))
        else:
            v, ev = score_s10(t["reply"], pkt)
            put("S10", v, ask={**live.evidence(t), **ev})
    # S11: the owner's explicit memory ask. Same-session, no backdate: the ask is a synchronous write; the
    # post-turn capture (which derives a near-identical row from the same turn) is OBSERVED before each read.
    if ask("S11"):
        log("S11: remember / keep in mind / repeat / fresh-session recall / recall question / forget that")
        t = {}
        cap = live.capture_status(a)
        r1 = say(a, "s11-say", SAY_REMEMBER)
        w1 = live.wait_captured(a, cap)
        t["say_tea"] = r1["reply"]
        t["packet_after_say"] = live.packet(a, ASK_TEA)
        cap = live.capture_status(a)
        r2 = say(a, "s11-keep", SAY_KEEP_IN_MIND)
        w2 = live.wait_captured(a, cap)
        t["say_keep"] = r2["reply"]
        t["packet_after_keep"] = live.packet(a, "coriander")
        t["count_once"] = live.packet_count(a)
        cap = live.capture_status(a)
        r3 = say(a, "s11-repeat", SAY_REMEMBER)
        w3 = live.wait_captured(a, cap)
        t["repeat_reply"] = r3["reply"]
        t["count_repeat"] = live.packet_count(a)
        r4 = say(a, "s11-fresh-ask", ASK_TEA)          # a different session: the row, not the context, answers
        r5 = say(a, "s11-recall-q", ASK_REMEMBERED)
        t["tea_reply"], t["recall_reply"] = r4["reply"], r5["reply"]
        r6 = say(a, "s11-forget", SAY_FORGET_THAT)
        t["forget_reply"] = r6["reply"]
        t["packet_final"] = live.packet(a, ASK_TEA + " coriander")
        errs = [x for x in (r1, r2, r3, r4, r5, r6) if x["error"]]
        waits = {"after_say": w1, "after_keep": w2, "after_repeat": w3}
        unseen = [k for k, w in waits.items() if not (w or {}).get("landed")]
        if errs:
            put("S11", "ERROR", why="a turn failed: " + "; ".join(e["error"] for e in errs), waits=waits)
        elif unseen:
            # the capture's copy is what "forget that" must also retract: unobserved, the verdict is unproven
            put("S11", "ERROR", why="capture not observed after: " + ", ".join(unseen), waits=waits)
        else:
            v, ev = score_s11(t)
            if live.keep:   # --keep-replies: the replies themselves (debug only; they are synthetic)
                ev["replies"] = {k: x for k, x in t.items() if k.endswith("_reply")}
            put("S11", v, waits=waits, **ev)

    if ask("S4") and setup_ok("S4", ("d1-worry",), ("S4", "backdate")):
        v, ev = _ask_judged(live, a, "d2-edge", ASK_WORRY, samples, score_s4, "S4")
        put("S4", v, landed=land["S4"], **ev)

    if ask("S3"):
        v, ev = _ask_judged(live, a, "d2-dentist", ASK_UNSAID, samples, score_s3, "S3")
        put("S3", v, **ev)

    # S5: the selector hook, then two open turns (flag off / no route: the
    # legacy proactive_pending read, SKIP when no hook fired) --------------------
    # S12 rides on S5's two open turns: no extra brain turn.
    if seed("S5"):
        if setup_ok("S5", ("d1-worry",), ("S4", "backdate")):  # no open loop seeded = nothing to carry
            v12, ev12 = "SKIP", {"why": "ZOE_PROACTIVE_SELECTOR off or the hook unavailable"}
            try:
                hook = live.run_selector(a)
                if not (hook or {}).get("enabled"):
                    v, ev = score_s5(live.proactive_hooks(a))
                    ev["selector"] = "off" if hook else "unavailable"
                else:
                    t1 = live.chat(a, "s5-open-1", ASK_OPEN_1)
                    t2 = live.chat(a, "s5-open-2", ASK_OPEN_2)
                    asks = [live.evidence(t1), live.evidence(t2)]
                    if t1["error"] or t2["error"]:
                        v, ev = "ERROR", {"why": "an open turn failed", "asks": asks}
                        v12, ev12 = "ERROR", {"why": "an S5 open turn failed"}
                    else:
                        rows = live.raise_state(a)
                        v, ev = score_s5_raise(hook, t1["reply"], t2["reply"], rows)
                        ev["asks"] = asks
                        v12, ev12 = score_s12(rows, t1["session"], t2["session"])
            except Exception as exc:  # noqa: BLE001
                v, ev = "ERROR", {"why": f"hook/read failed: {type(exc).__name__}"}
                v12, ev12 = "ERROR", {"why": f"hook/read failed: {type(exc).__name__}"}
            put("S5", v, **ev)
            put("S12", v12, **ev12)
        else:
            put("S12", "ERROR", why="S5's setup was not exercised, so its open turns never ran")

    # S20/S21/S22: the three conversation-quality classes (2026-10-04) ------------
    if seed("S20"):
        say(a, "d1-dob", SAY_DOB)
        land["S20"] = live.wait_landed(a, ASK_DOB, ("1991",))
    if ask("S20") and setup_ok("S20", ("d1-dob",), ("S20",)):
        t = live.chat(a, "s20-ask", ASK_DOB)
        pkt = live.packet(a, ASK_DOB)
        if t["error"]:
            put("S20", "ERROR", landed=land["S20"], ask=live.evidence(t))
        else:
            v, ev = score_s20(t["reply"], pkt)
            put("S20", v, landed=land["S20"], ask={**live.evidence(t), **ev})

    if seed("S21"):
        say(a, "s21-kids", SAY_KIDS)
        land["S21"] = live.wait_landed(a, ASK_KIDS, S21_LANDED)
    if ask("S21") and setup_ok("S21", ("s21-kids",), ("S21",)):
        ack = live.chat(a, "s21-fix", SAY_PET)
        t = live.chat(a, "s21-ask", ASK_KIDS)
        pkt = live.packet(a, ASK_KIDS + " Biscuit")
        if t["error"] or ack["error"]:
            put("S21", "ERROR", landed=land["S21"], ask=live.evidence(t), ack=live.evidence(ack))
        else:
            v, ev = score_s21(ack["reply"], t["reply"], pkt)
            put("S21", v, landed=land["S21"], ask={**live.evidence(t), **ev})

    if seed("S22"):
        t1 = say(a, "s22-roster", SAY_ROSTER)
        land["S22"] = live.wait_landed(a, ASK_ROSTER, ("anika",))
    if ask("S22") and setup_ok("S22", ("s22-roster",), ("S22",)):
        t2 = live.chat(a, "s22-ask", ASK_ROSTER)
        pkt = live.packet(a, ASK_ROSTER)
        if t2["error"]:
            put("S22", "ERROR", landed=land["S22"], ask=live.evidence(t2))
        else:
            v, ev = score_s22(t1["reply"], t2["reply"], pkt)
            put("S22", v, landed=land["S22"], ask={**live.evidence(t2), **ev})

    # S6: isolation ----------------------------------------------------------
    if ask("S6"):
        t = live.chat(b, "b-ask", ASK_B)
        pkt_b = live.packet(b, ASK_B + " Marisol Lisbon Hobart Teodor lighthouse")
        pkt_a = live.packet(a, ASK_B + " dad Teodor lighthouse")
        if t["error"]:
            put("S6", "ERROR", ask=live.evidence(t))
        else:
            v, ev = score_s6(t["reply"], pkt_b, pkt_a)
            put("S6", v, ask={**live.evidence(t), **ev})

    # S8: long history -------------------------------------------------------
    if ask("S8"):
        log(f"S8: {len(FILLER)} filler turns across {FILLER_SESSIONS} sessions")
        errors = 0
        for i, text in enumerate(FILLER):
            errors += bool(say(a, f"filler-{i % FILLER_SESSIONS}", text)["error"])
        # The facts S8 must recall are S1's and S7's seeds; without them landed there
        # is nothing to survive the history (and any failed filler turn is ERROR in score_s8).
        if setup_ok("S8", ("d1-sister", "d1-dad"), ("S1", "S7_rich")):
            t1 = live.chat(a, "long-ask-sister", ASK_LONG_SISTER)
            t2 = live.chat(a, "long-ask-dad", ASK_LONG_DAD)
            if t1["error"] or t2["error"]:
                put("S8", "ERROR", filler_errors=errors, asks=[live.evidence(t1), live.evidence(t2)])
            else:
                v, ev = score_s8(t1["reply"], t2["reply"], errors)
                put("S8", v, filler_turns=len(FILLER),
                    asks=[live.evidence(t1), live.evidence(t2)], **ev)

    # S13-S16: the contacts conversation classes. Same-day, no backdate: contact
    # writes are synchronous (nothing to wait for in the memory pipeline).
    log("S13-S16: contacts list-all, relation phrase, duplicate, enumerated offer")
    if ask("S13") or ask("S14") or ask("S15"):
        say(a, "c-full", SAY_CONTACT_FULL)
    if ask("S13") or ask("S14"):
        say(a, "c-rel", SAY_CONTACT_REL)
    if ask("S13") and setup_ok("S13", ("c-full", "c-rel")):
        t = live.chat(a, "c-ask-list", ASK_CONTACTS_LIST)
        if t["error"]:
            put("S13", "ERROR", ask=live.evidence(t))
        else:
            v, ev = score_s13(t["reply"])
            put("S13", v, ask={**live.evidence(t), **ev})
    if ask("S14") and setup_ok("S14", ("c-rel",)):
        t = live.chat(a, "c-ask-rel", ASK_WHO_REL)
        if t["error"]:
            put("S14", "ERROR", ask=live.evidence(t))
        else:
            v, ev = score_s14(t["reply"])
            put("S14", v, ask={**live.evidence(t), **ev})
    if ask("S15"):
        say(a, "c-stub", SAY_CONTACT_STUB)
    if ask("S15") and setup_ok("S15", ("c-full", "c-stub")):
        t = live.chat(a, "c-ask-dup", ASK_WHO_DUP)
        if t["error"]:
            put("S15", "ERROR", ask=live.evidence(t))
        else:
            v, ev = score_s15(t["reply"])
            put("S15", v, ask={**live.evidence(t), **ev})
    if ask("S16"):
        say(a, "c-family", SAY_FAMILY)
    if ask("S16") and setup_ok("S16", ("c-family",)):
        t1 = live.chat(a, "c-family", SAY_FAMILY_NUDGE)
        if t1["error"]:
            put("S16", "ERROR", ask=live.evidence(t1))
        elif len(found_needles(t1["reply"], FAMILY_NAMES)) < 2:
            put("S16", "SKIP", asks=[live.evidence(t1)], why=S16_WHY)  # no yes turn to send
        else:
            t2 = live.chat(a, "c-family", SAY_FAMILY_YES)
            if t2["error"]:
                put("S16", "ERROR", asks=[live.evidence(t1), live.evidence(t2)])
            else:
                v, ev = score_s16(t1["reply"], t2["reply"])
                put("S16", v, asks=[live.evidence(t1), live.evidence(t2)], **ev)
    return [res[k] for k in SCENARIO_IDS if k in res]


# ─────────────────────────────────────────────────────────────────────────────
# Artifacts + main
# ─────────────────────────────────────────────────────────────────────────────

def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_trend(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rev = payload.get("revision") or {}
    line = {"ts": payload["finished_at"], "status": payload["status"],
            "commit": rev.get("commit"), "dirty": rev.get("dirty"),
            "verdicts": verdict_map(payload.get("scenarios", [])),
            "regressions": (payload.get("compare") or {}).get("regressions", []),
            "teardown_proven": (payload.get("teardown") or {}).get("proven"),
            "duration_s": payload.get("duration_s")}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(line, sort_keys=True) + "\n")


def _acquire_lock():
    import fcntl
    fd = os.open(LOCK, os.O_CREAT | os.O_RDWR, 0o666)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BlockingIOError:
        os.close(fd)
        # Held by our own `flock <lock> ...` wrapper? Then we are serialized.
        target, pid = os.path.realpath(LOCK), os.getppid()
        for _ in range(15):
            if pid <= 1:
                break
            try:
                if any(os.path.realpath(f"/proc/{pid}/fd/{f}") == target
                       for f in os.listdir(f"/proc/{pid}/fd")):
                    return None
                with open(f"/proc/{pid}/status") as fh:
                    pid = next((int(x.split()[1]) for x in fh if x.startswith("PPid:")), 0)
            except (OSError, ValueError):
                break
        print(f"ABORT: another harness run holds {LOCK} (brain slot / RAM)", file=sys.stderr)
        raise SystemExit(3)


def _readyz() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(f"{DATA_BASE}/readyz", timeout=10) as r:
            body = json.loads(r.read().decode() or "{}")
    except Exception as exc:  # noqa: BLE001
        return False, f"/readyz unreachable ({type(exc).__name__})"
    mc = (body.get("memory_capture") or {}).get("status")
    ok = bool(body.get("ready")) and mc == "ok"
    return ok, f"ready={body.get('ready')} memory_capture={mc}"


def _wait(check: Callable[[], tuple[bool, str]], timeout_s: int, what: str) -> tuple[bool, str]:
    t0 = time.monotonic()
    while True:
        ok, detail = check()
        if ok or time.monotonic() - t0 >= timeout_s:
            return ok, detail
        print(f"  waiting for {what}: {detail}", flush=True)
        time.sleep(30)


def _refuse(args, reason: str, revision: dict | None) -> int:
    print(f"REFUSED: {reason}", file=sys.stderr)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    write_json(args.results, {"status": "refused", "reason": reason, "finished_at": now,
                              "revision": revision, "harness_version": HARNESS_VERSION})
    return 2


def _pending_teardown(live: Live, path: Path, log) -> dict | None:
    """Tear down a previous run that died before its own teardown."""
    if not path.exists():
        return None
    try:
        pend = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"proven": False, "problems": ["pending-teardown file unreadable"]}
    users = [u for u in pend.get("users", []) if DEMO_USER_RE.match(u)]
    sessions = [s for s in pend.get("sessions", []) if s.startswith("bar-")]
    log(f"tearing down a previous unfinished run: {len(users)} user(s), {len(sessions)} session(s)")
    td = teardown(live, users, sessions)
    if td["proven"]:
        path.unlink(missing_ok=True)
    return td


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no network, no writes")
    ap.add_argument("--compare-baseline", action="store_true",
                    help="exit 1 when a previously PASSING scenario no longer passes; "
                         "REFUSES (exit 2) when no valid baseline exists — --record-baseline first")
    ap.add_argument("--record-baseline", action="store_true",
                    help="save this run as the baseline (alone — never with --compare-baseline; "
                         "only when teardown is proven)")
    ap.add_argument("--samples", type=int, default=None,
                    help="asks per judged scenario, odd (majority vote); default 1, or in "
                         "--compare-baseline mode the BASELINE's count — an explicit mismatch refuses")
    ap.add_argument("--no-backdate", action="store_true",
                    help="skip the 26h day-1 backdate (DIAGNOSTIC only: refused with "
                         "--record-baseline / --compare-baseline, whose scenarios are multi-day)")
    ap.add_argument("--keep-replies", action="store_true",
                    help="store 240-char reply excerpts in the local results file (debug)")
    ap.add_argument("--only", default=None, metavar="S21[,S22]",
                    help="run only these scenario ids (a PARTIAL run: status=partial, exit 2 on an "
                         "unknown id, NEVER records a baseline; --compare-baseline compares only "
                         "the selected scenarios)")
    ap.add_argument("--axis", default=None, metavar="b[,c]",
                    help="run only the scenarios on these ZMB axes (letter or name: a authority, "
                         "b extraction, c temporal, d recall, e abstention, g emotional); "
                         "intersected with --only; a PARTIAL run like --only")
    ap.add_argument("--teardown-only", action="store_true",
                    help="only clean up a previous run's pending teardown")
    ap.add_argument("--service-dir", default=None)
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--trend", type=Path, default=DEFAULT_TREND)
    ap.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    ap.add_argument("--pending", type=Path, default=DEFAULT_PENDING)
    ap.add_argument("--mem-wait-s", type=int, default=900)
    ap.add_argument("--ready-wait-s", type=int, default=1200)
    args = ap.parse_args(argv)
    if args.samples is not None and (args.samples < 1 or args.samples % 2 == 0):
        ap.error("--samples must be a positive odd number (majority vote)")
    if args.no_backdate and (args.record_baseline or args.compare_baseline):
        ap.error("--no-backdate is diagnostic only: S2/S4/S7 are multi-day scenarios, so a "
                 "same-day run can neither set nor clear the bar")
    if args.compare_baseline and args.record_baseline:
        ap.error("--compare-baseline and --record-baseline are mutually exclusive: a regressed "
                 "compare run must never overwrite the bar (record alone, deliberately)")
    try:
        selected = parse_selection(args.only, args.axis)
    except ValueError as exc:
        ap.error(str(exc))
    partial = selected is not None and selected != frozenset(SCENARIO_IDS)
    if partial and args.record_baseline:
        ap.error("--record-baseline is refused with --only/--axis: a partial run would turn every "
                 "unselected scenario into 'new' — record the baseline from a full run")

    if args.dry_run:
        print(plan_text(args.samples or 1, selected))
        return 0
    if os.environ.get("ZOE_PERF") != "1":
        print("samantha_bar: skipped — live runs require ZOE_PERF=1 (see --dry-run)")
        return 0

    log = lambda m: print(m, flush=True)  # noqa: E731
    service_dir = resolve_service_dir(args.service_dir)
    revision = service_revision(service_dir)
    load_source_fallback_markers(service_dir)  # the checkout's own "brain did not answer" texts
    lock_fd = _acquire_lock()  # noqa: F841 — held for the process lifetime
    try:
        cur = os.nice(0)
        if cur < 5:
            os.nice(5 - cur)
    except OSError:
        pass

    token = env_file_value(service_dir, "ZOE_INTERNAL_TOKEN")
    dsn = env_file_value(service_dir, "POSTGRES_URL")
    admin = os.environ.get("ZOE_BAR_ADMIN_SESSION", "").strip()
    live = Live(token, admin, dsn, args.keep_replies)

    # Gates -----------------------------------------------------------------
    baseline, baseline_problem = load_baseline(args.baseline)
    if args.compare_baseline and baseline is None:
        return _refuse(args, f"--compare-baseline needs a valid baseline: {baseline_problem}",
                       revision)
    if baseline_problem and args.baseline.exists():
        log(f"baseline ignored ({baseline_problem})")
    if args.compare_baseline:
        # A majority-of-3 bar compared against a single stochastic answer is not a
        # comparison: inherit the baseline's count, refuse an explicit mismatch.
        base_samples = (baseline or {}).get("samples")
        if args.samples is None:
            args.samples = int(base_samples or 1)
            log(f"samples={args.samples} (inherited from the baseline)")
        elif base_samples is not None and args.samples != base_samples:
            return _refuse(args, f"--samples {args.samples} does not match the baseline's "
                                 f"samples={base_samples} — compare with the same count or "
                                 "re-record the baseline", revision)
    if args.samples is None:
        args.samples = 1
    if in_nightly_window(dt.datetime.now()):
        return _refuse(args, "inside (or within 30 min of) the 01:45-03:15 nightly window", revision)
    busy, detail = deploy_in_progress()
    if busy is None or busy:
        return _refuse(args, f"deploy check: {detail}", revision)
    if not token or not dsn:
        return _refuse(args, "ZOE_INTERNAL_TOKEN / POSTGRES_URL not resolvable from the service .env",
                       revision)
    ok, detail = _wait(_readyz, args.ready_wait_s, "/readyz")
    if not ok:
        return _refuse(args, f"zoe-data not ready: {detail}", revision)
    if not live.forget_ok():
        return _refuse(args, f"memory-store teardown unavailable ({live.forget_mode} mode: "
                             "forget-synthetic not deployed / token refused, or the admin session "
                             "in ZOE_BAR_ADMIN_SESSION does not work) — the run must "
                             "not write", revision)
    ok, detail = _wait(lambda: (mem_available_mb() >= MIN_MEM_MB,
                                f"MemAvailable {mem_available_mb()} MB < {MIN_MEM_MB}"),
                       args.mem_wait_s, "memory headroom")
    if not ok:
        return _refuse(args, detail, revision)

    prior = _pending_teardown(live, args.pending, log)
    if prior is not None and not prior["proven"]:
        return _refuse(args, f"a previous run's teardown is still unproven: {prior['problems']}",
                       revision)
    if args.teardown_only:
        log("teardown-only: nothing pending" if prior is None else "teardown-only: proven")
        return 0

    # Run -------------------------------------------------------------------
    a, b = new_demo_user(), new_demo_user()
    started, t0 = dt.datetime.now(dt.timezone.utc), time.monotonic()
    log(f"samantha_bar v{HARNESS_VERSION}: demo A + demo B, samples={args.samples}, "
        f"commit={(revision or {}).get('commit', '?')[:10]} dirty={(revision or {}).get('dirty')}")

    def _sigterm(signum, frame):  # route SIGTERM through the finally below
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, _sigterm)

    results: list[dict] = []
    run_error = None
    td: dict[str, Any] = {"proven": False, "problems": ["not run"]}
    try:
        write_json(args.pending, {"users": [a, b], "sessions": [], "started_at": started.isoformat()})
        results = run_scenarios(live, a, b, args.samples, not args.no_backdate, log,
                                selected=selected)
    except BaseException as exc:  # noqa: BLE001 — teardown must still run
        run_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        log(f"run aborted: {run_error}")
    finally:
        sessions = [s for u in (a, b) for s in live.sessions.get(u, [])]
        write_json(args.pending, {"users": [a, b], "sessions": sessions,
                                  "started_at": started.isoformat()})
        log("teardown ...")
        td = teardown(live, [a, b], sessions)
        if td["proven"]:
            args.pending.unlink(missing_ok=True)
        log(f"teardown proven={td['proven']} {'' if td['proven'] else td['problems']}")

    expected_ids = selected if selected is not None else frozenset(SCENARIO_IDS)
    cmp = compare_baseline(verdict_map(results),
                           restrict_baseline(baseline, selected) if partial else baseline)
    if partial:
        cmp["notes"].append(f"partial run: compared only {len(expected_ids)} of "
                            f"{len(SCENARIO_IDS)} scenarios")
    # A short run is `partial` (never `error`) ONLY when it was asked to be short: results must be
    # exactly the selected ids. Anything else short of the selection is still an error.
    status = "error" if (run_error or not td["proven"]
                         or {r["id"] for r in results} != set(expected_ids)) \
        else ("regression" if (args.compare_baseline and cmp["red"])
              else ("partial" if partial else "ok"))
    payload = {"harness_version": HARNESS_VERSION, "status": status, "run_error": run_error,
               "started_at": started.isoformat(timespec="seconds"),
               "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "duration_s": round(time.monotonic() - t0, 1), "samples": args.samples,
               "backdate": not args.no_backdate, "partial": partial,
               "selected": sorted(expected_ids) if partial else None,
               "revision": revision, "judge_prompt_sha256": JUDGE_PROMPT_SHA256,
               "scenarios": results, "compare": cmp, "teardown": td,
               "baseline_ref": {"path": str(args.baseline),
                                "created_at": (baseline or {}).get("created_at"),
                                "commit": ((baseline or {}).get("revision") or {}).get("commit")}}
    write_json(args.results, payload)
    append_trend(args.trend, payload)
    if args.record_baseline and status == "ok" and not partial:  # a partial run NEVER records
        write_json(args.baseline, make_baseline(results, revision, args.samples))
        log(f"baseline recorded: {args.baseline}")
    elif args.record_baseline:
        log("baseline NOT recorded: the run errored or its teardown is unproven")

    for r in results:
        exp = f"  (expected {r['expected']}: a target)" if r.get("expected") else ""
        log(f"  {r['id']:<3} {r['verdict']:<5} "
            f"{next(s['title'] for s in SCENARIOS if s['id'] == r['id'])}{exp}")
    if cmp["regressions"]:
        log(f"REGRESSIONS vs baseline: {', '.join(cmp['regressions'])}")
    log(f"status={status}  results={args.results}")
    if status == "error":
        return 2
    return 1 if status == "regression" else 0


if __name__ == "__main__":
    raise SystemExit(main())
