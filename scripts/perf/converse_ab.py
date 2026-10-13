#!/usr/bin/env python3
"""converse_ab.py - does Zoe CONVERSE instead of MIRROR? The mirror rate and a conversational-quality score, arm against arm, on the live 4B.

THE QUESTION (owner, 2026-10-12: "We really need to teach you how to talk to people ... it feels more natural rather than you just
regurgitating what I'm doing"). In one live evening 6 of 14 conversational replies opened "It sounds like ..." / "That sounds like ...", most
ended on a generic question, a complaint about a pronunciation glitch was answered with an invented apology, and "what can you tell me about it?"
got the previous answer back verbatim. Does the manner block's NEW text (``lexicons_data/<lang>.json`` "manner") cut the mirror rate and raise the
share of replies that are a conversation (substance, at most one question, brief, no repeat, feedback taken as feedback), against the same brain
without it AND against the text that shipped before (it literally said "first say back what you heard")?

SIBLING OF ``manner_block_ab.py`` (the integrity rules are THAT harness's, reused, not re-derived): the real zoe-data composition and the real
manner seam run in this process against the live llama-server 4B, through a scratch sidecar built from this worktree's ``dist`` (copy the
deployed build; never ``npm run build`` beside the live brain), a stub data server (no live DB read), ``demo_bar_<hex>`` ids, ``replay_isolation``.
Locks and guards are manner_block_ab's: run under
    ZOE_PERF=1 flock -w 3600 /tmp/zoe-voice-harness.lock flock -s -w 3600 /tmp/zoe-brain-window.lock <zoe-data venv python> scripts/perf/converse_ab.py ...
never 02:30-04:30, refuses while the night window / a landing / low RAM / a young zoe-data; its OWN brain-time ledger (this file's CACHE dir).

PROTOCOL (fixed before any live number):
  * arms: ``none`` (flag unset = today's bytes), ``on:LEGACY`` (the text that shipped in #1977), ``on:C1..C3`` (candidates, DEV only),
    ``on:V0`` (the text now in the lexicon = the shipped candidate).
  * the corpus is INVENTED (no household text): six kinds - a personal disclosure, an open statement, feedback about Zoe herself, a complaint about
    how she talks, a "tell me more" follow-up after a factual answer, and plain factual controls. Every kind has a DEV half and a frozen HELD-OUT
    half (``SPLIT_DIGEST``). The candidate is chosen on DEV only; the verdict is the held-out half, once.
  * scored by deterministic scorers only (no model judge: an unvalidated 4B judge would grade its own homework). Each sub-metric is reported
    beside the composite so a composite cannot hide a gamed part. The mirror formulas ARE the guard's lexicon (``mirror_guard``): that is the
    definition of "mirror"; the OTHER scorers (generic closer at the end, boilerplate, invented premise, substance, repeat, length) are this
    file's own and independent of what the prompt text says.
  * the POST-GUARD figures apply ``mirror_guard.trim`` (proven equal to the stream form in tests) to the saved raw replies: the harness drives
    the seam below ``run_flue_brain_streaming`` where the guard sits, so the raw rows are what the PROMPT alone produced.
  * KEEP (``decide``) for the shipped arm vs ``none`` on the held-out half: its mirror-open rate is lower with the 95 % Newcombe-Wilson interval
    wholly below 0; its composite pass rate is higher with the interval wholly above 0; no kind's composite separates DOWN; the factual controls'
    answered rate does not separate down. A missing comparison is DO NOT KEEP.

Usage:
    python3 scripts/perf/converse_ab.py --selftest                              # OFFLINE: corpus, scorers, negative controls, decide()
    ... --phase dev --arms none,LEGACY,C1,C2,C3 --run-id conv-1
    ... --phase report --run-id conv-1 --split dev --arms none,LEGACY,C1,C2,C3  # pick the candidate on DEV only
    ... --phase heldout --arms none,LEGACY,V0 --run-id conv-1
    ... --phase report --run-id conv-1 --split heldout --arms none,LEGACY,V0 --ship V0
Exit: 0 ran | 2 refused / error / selftest failed | 3 lock held / brain busy.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import manner_block_ab as mab  # noqa: E402  (the shared rig: Runner, scratch sidecar, guards, ledger, integrity helpers)
import person_half_enforce_ab as pab  # noqa: E402
import samantha_bar as sb  # noqa: E402
import samantha_person as sp  # noqa: E402

CACHE = sb.CACHE / "converse_ab"
HARNESS_VERSION = "converse-ab-1"
TOTAL_BUDGET_S = 2400.0


def configure_rig() -> None:
    """Point the shared rig at THIS harness: its rows, its OWN brain-time ledger (manner_block_ab's 1500 s belongs to that A/B), its candidate
    texts. The shared helpers read these module globals at call time, so this runs once per CLI invocation (never at import: importing this
    file, e.g. from a unit test, must not change manner_block_ab)."""
    mab.CACHE = CACHE
    mab.LEDGER = CACHE / "brain_time.json"
    mab.TOTAL_BUDGET_S = TOTAL_BUDGET_S
    mab.HARNESS_VERSION = HARNESS_VERSION
    mab.VARIANTS.update({"LEGACY": LEGACY, **CANDIDATES})

# ── the arms ──────────────────────────────────────────────────────────────────────────────────────────────────────────────
#: the text that shipped in #1977 (flag-dark, never on): it tells the model to "first say back what you heard". Kept literal here so the
#: A/B can compare against it after the lexicon moves on; V0 is whatever ``lexicons_data/en.json`` holds right now.
LEGACY = ("With people: when someone shares a feeling, first say back what you heard in a few words of your own, then stop or ask ONE question; "
          "give advice only if they ask. Name one specific thing about what they told you, and never flatter or gush. If their plan has a real "
          "problem, say so once, kindly, then respect their choice; if it is a good plan, say so plainly. Ask one question at a time. Never hint "
          "they should stay or that you missed them. When they say goodnight, say a plain goodnight.")
#: candidates, written after reading the live prompts and BEFORE any converse number. The examples are outside every corpus topic.
CANDIDATES: dict[str, str] = {
    # C1: a procedure, with the banned openers named (the risk: naming a phrase can prime it on a small model)
    "C1": ("Talk like a friend, not a counsellor. Never open with \"It sounds like\", \"That sounds like\" or \"I hear you\", and never restate what they just "
           "said. Start with something of your own: a fact, a real opinion, a related thought, a next step. If they are upset, one short clause "
           "is enough (\"Ugh, rough.\"), then carry on. Ask at most one question, only if the answer changes what you say next; otherwise end "
           "on a statement. If they criticise you, take it as feedback about you: name the exact thing, say what you will do. Never repeat your last answer. Never flatter."),
    # C2: positive only (no banned phrase in the prompt), with two short model exchanges outside every corpus topic
    "C2": ("Reply like a friend who knows things, in one to three short sentences. Give them something: a fact, a take of your own, a suggestion, "
           "or a small story. If they are upset, one short clause of care, then something useful. Ask a question only when the answer would "
           "change what you say next. If they point out a mistake of yours, name it and fix it. If they ask for more, add something new. "
           "Example. \"I found a hornets' nest in the shed.\" -> \"Leave it be till dusk, they're calmer then. Is it the paper kind?\" "
           "Example. \"Rough week.\" -> \"Ugh. Friday's close, though. Anything you can drop?\""),
    # C3: C1's procedure plus the old manner behaviours that were never shown to help are NOT carried; the shortest of the three
    "C3": ("You are talking with a friend. Do not repeat their words back to them or describe their feelings; respond with substance instead: "
           "a fact, an opinion, a suggestion, a small story. Care is one short clause at most. At most one question, and only if it moves "
           "things on. Criticism of you is feedback: name the exact thing and put it right. Never repeat your previous answer; add to it."),
}


def variant_text(name: str) -> str:
    """The text of a variant: LEGACY and the candidates are this file's; ``V0`` is whatever the lexicon holds now."""
    if name == "LEGACY":
        return LEGACY
    return CANDIDATES[name] if name in CANDIDATES else mab.variant_text(name)


def arm_text(arm: str) -> str:
    return "" if arm == "none" else variant_text(arm.split(":", 1)[1])


# ── the corpus (INVENTED; the held-out half was written before the first live run) ────────────────────────────────────────
# each ask: (id, kind, turns, needles-or-None)
DEV = (
    ("d-dis-1", "disclosure", ("My brother-in-law is having surgery on Thursday and everyone is pretending not to be nervous.",), None),
    ("d-dis-2", "disclosure", ("I finally finished the first draft of my thesis and now I feel weirdly empty.",), None),
    ("d-dis-3", "disclosure", ("The bus was an hour late and I missed my own meeting.",), None),
    ("d-dis-4", "disclosure", ("I got a puppy last weekend and she has chewed through two of my shoes already.",), None),
    ("d-dis-5", "disclosure", ("My oldest friend is moving to Canada next month and I haven't worked out how to say goodbye.",), None),
    ("d-dis-6", "disclosure", ("I've been up since five fixing the fence after the storm and I'm completely wrecked.",), None),
    ("d-dis-7", "disclosure", ("My dad is in hospital for a few days with a chest infection, but it's nice that the whole family is staying nearby.",), None),
    ("d-dis-8", "disclosure", ("I got the job, but it means moving across the country.",), None),
    ("d-open-1", "open", ("I've been reading about how octopuses can solve puzzles.",), None),
    ("d-open-2", "open", ("I think I want to try making sourdough this weekend.",), None),
    ("d-open-3", "open", ("I've started watching old black and white films in the evenings.",), None),
    ("d-open-4", "open", ("Lately I keep thinking about taking a train trip across the country.",), None),
    ("d-open-5", "open", ("I want to see what you can actually do, I've only ever used you for timers.",), None),
    ("d-fb-1", "feedback", ("You said February wrong just now, you skipped the first r.",), ("february", "pronounc", "said", "say", "r")),
    ("d-fb-2", "feedback", ("That answer was far too long, I only wanted one line.",), ("long", "short", "line", "brief", "one", "answer")),
    ("d-fb-3", "feedback", ("You got the capital wrong earlier, it's Canberra, not Sydney.",), ("canberra", "capital", "sydney", "wrong")),
    ("d-fb-4", "feedback", ("You keep jumping in when I pause to think.",), ("interrupt", "pause", "wait", "think", "jump")),
    ("d-meta-1", "meta", ("You never really talk to me, you just say back what I tell you.",), None),
    ("d-meta-2", "meta", ("It feels like I'm talking to a form, not a person.",), None),
    ("d-meta-3", "meta", ("Can you just have a normal conversation with me?",), None),
    ("d-fu-1", "followup", ("Why do octopuses have three hearts?", "What can you tell me about it?"), None),
    ("d-fu-2", "followup", ("How long does it take to boil an egg?", "Anything else I should know about that?"), None),
    ("d-fu-3", "followup", ("Who invented the telephone?", "Tell me a bit more about that."), None),
    ("d-ctl-1", "control", ("How many legs does a spider have?",), None),
    ("d-ctl-2", "control", ("What's a good substitute for buttermilk?",), None),
    ("d-ctl-3", "control", ("How do I get a red wine stain out of a shirt?",), None),
    ("d-ctl-4", "control", ("Is it okay to eat rice that's been left out overnight?",), None),
)
HELD = (
    ("h-dis-1", "disclosure", ("My grandmother fell yesterday and she's in hospital for tests, and honestly it's been good to have all the cousins in one place.",), None),
    ("h-dis-2", "disclosure", ("I burned the dinner I'd been planning all week and my in-laws were sitting right there.",), None),
    ("h-dis-3", "disclosure", ("My daughter just learned to ride her bike and I cried a little in the park.",), None),
    ("h-dis-4", "disclosure", ("Work announced layoffs for next quarter and nobody is saying anything.",), None),
    ("h-dis-5", "disclosure", ("I've been learning the cello for three months and I can finally play a whole piece.",), None),
    ("h-dis-6", "disclosure", ("My neighbour's tree came down in the wind and missed our car by about a foot.",), None),
    ("h-dis-7", "disclosure", ("I haven't spoken to my cousin in two years and she just sent me a birthday message.",), None),
    ("h-dis-8", "disclosure", ("I ran my first ten kilometres this morning and my knees are furious.",), None),
    ("h-dis-9", "disclosure", ("The landlord says he's selling the house and we have sixty days.",), None),
    ("h-dis-10", "disclosure", ("My mum has started forgetting where she puts her keys and I'm trying not to make a big deal of it.",), None),
    ("h-open-1", "open", ("I've been getting into birdwatching and there's a kookaburra that visits every morning.",), None),
    ("h-open-2", "open", ("I'm thinking about repainting the kitchen a dark green.",), None),
    ("h-open-3", "open", ("I want to learn something new this year, maybe a language.",), None),
    ("h-open-4", "open", ("I just found out that honey never goes off.",), None),
    ("h-open-5", "open", ("I keep wondering whether I should get a second cat.",), None),
    ("h-open-6", "open", ("I'd like to see what all your abilities are, I mostly just ask you the time.",), None),
    ("h-fb-1", "feedback", ("You pronounced Wednesday as if it has an extra syllable.",), ("wednesday", "syllable", "pronounc", "say", "said")),
    ("h-fb-2", "feedback", ("That recipe you gave me had way too much salt.",), ("salt", "recipe")),
    ("h-fb-3", "feedback", ("You talk too fast when you read lists out.",), ("fast", "slow", "list", "pace", "speed")),
    ("h-fb-4", "feedback", ("You told me the shop closes at five but it closes at six.",), ("five", "six", "close", "shop", "hours")),
    ("h-fb-5", "feedback", ("You called my sister by the wrong name just now.",), ("name", "wrong", "sister")),
    ("h-meta-1", "meta", ("You always sound the same no matter what I say.",), None),
    ("h-meta-2", "meta", ("I wish you'd say something I didn't already know.",), None),
    ("h-meta-3", "meta", ("Why do you always end with a question?",), None),
    ("h-meta-4", "meta", ("Just talk to me like a friend would.",), None),
    ("h-fu-1", "followup", ("Why is the sea salty?", "What else can you tell me about it?"), None),
    ("h-fu-2", "followup", ("What's the tallest mountain in Australia?", "What can you tell me about it?"), None),
    ("h-fu-3", "followup", ("How do bees make honey?", "Anything more on that?"), None),
    ("h-fu-4", "followup", ("Why do cats purr?", "What can you tell me about it?"), None),
    ("h-ctl-1", "control", ("How many hearts does an octopus have, and what colour is its blood?",), None),
    ("h-ctl-2", "control", ("What's the difference between baking soda and baking powder?",), None),
    ("h-ctl-3", "control", ("How do I stop my glasses fogging up with a mask on?",), None),
    ("h-ctl-4", "control", ("Can I freeze cooked pasta?",), None),
)
SAMPLES = {"dev": 2, "heldout": 3}
KINDS = ("disclosure", "open", "feedback", "meta", "followup", "control")


def corpus(phase: str) -> tuple:
    return {"dev": DEV, "heldout": HELD}[phase]


def split_digest() -> str:
    return hashlib.sha256(json.dumps({"dev": DEV, "heldout": HELD}, sort_keys=True).encode()).hexdigest()


#: pinned by ``--selftest``: editing a held-out message after seeing a number is a deliberate act with the old digest in the PR
SPLIT_DIGEST = "3c286d6bfac7ec7c15a58a84ccc350a8363b66b6c84fa1c3bd10e9e7159769fb"

# ── the scorers (deterministic; independent of the prompt text) ───────────────────────────────────────────────────────────
STOP = frozenset("""a an and are as at be but by can could did do does for from get got had has have he her him his how i if in into is it its just
me my no not of on one or our out she so some than that the their them then there these they this to too up us was we were what when where which
who will with would you your yours really very also about more much many any all been being only still even like bit lot thing things way""".split())
#: words that carry feeling or acknowledgement but no information: a sentence made of these (and the user's own words) is not substance
AFFECT = frozenset("""sorry hard tough difficult stressful exhausting frightening scary worried worry nervous emotional overwhelming frustrating
understandable understand feelings feeling feel felt sounds sound seems seem tired rough heavy glad happy sad upset hear hearing sense imagine
appreciate thanks thank grateful lovely wonderful great good nice awesome amazing mind mindful support here""".split())
BOILERPLATE = ("i hear what you", "i hear you", "i really appreciate you", "i appreciate you pointing", "i appreciate your feedback", "thank you for your feedback",
               "thanks for the feedback", "thanks for your feedback", "always trying to get better", "always working to improve", "i'll be more mindful",
               "i will be more mindful", "i'll do my best to improve", "i apologize for any", "sorry for any confusion", "i understand your frustration",
               "i'm here to help", "i'm here for you", "i'm here to listen", "your feelings are valid", "it's something i'm always")
#: an apology or a claim about the person's inner state that the message never made ("I'm sorry I said something that upset you")
INVENTED_PREMISE = ("upset you", "hurt you", "hurt your feelings", "offended you", "offend you", "made you feel", "make you feel", "bothered you",
                    "something i said that", "something that upset")
MAX_SENTENCES, MAX_WORDS = 3, 55
CONTROL_MAX_SENTENCES, CONTROL_MAX_WORDS = 4, 75
_TOK = re.compile(r"[a-z']+")


def _lex():
    zoe_path()
    import mirror_guard as mg

    return mg, mg.lexicon("en")


def zoe_path() -> None:
    mab.zoe_path()


def sentences(text: str) -> list[str]:
    zoe_path()
    from narration_filter import _next_end

    out, rest = [], (text or "").strip()
    while rest:
        end = _next_end(rest)
        end = len(rest) if end < 0 else end
        piece, rest = rest[:end].strip(), rest[end:].strip()
        if piece:
            out.append(piece)
    return out


def tokens(text: str) -> list[str]:
    return _TOK.findall((text or "").lower().replace("’", "'"))


def content(text: str) -> set[str]:
    return {t for t in tokens(text) if t not in STOP and len(t) > 2}


def novel(sentence: str, against: set[str]) -> int:
    return len({t for t in content(sentence) if t not in AFFECT} - against)


def substantive_sentences(reply: str, user_text: str) -> int:
    """Sentences that are not a mirror, not a generic closer and carry at least 3 content words that neither the user nor affect-talk supplied."""
    mg, lex = _lex()
    ctx = content(user_text)
    return sum(1 for s in sentences(reply)
               if not mg.is_mirror_sentence(s, lex) and not mg.is_generic_closer(s, lex) and novel(s, ctx) >= 3)


def verbatim_run(a: str, b: str) -> int:
    """The longest run of consecutive words shared by two texts."""
    ta, tb = tokens(a), tokens(b)
    best = 0
    for i in range(len(ta)):
        for j in range(len(tb)):
            k = 0
            while i + k < len(ta) and j + k < len(tb) and ta[i + k] == tb[j + k]:
                k += 1
            best = max(best, k)
    return best


def features(reply: str, user_text: str, prev: str = "") -> dict:
    mg, lex = _lex()
    sents = sentences(reply)
    low = " ".join(tokens(reply))
    last = sents[-1] if sents else ""
    f = {
        "mirror_open": bool(sents) and mg.is_mirror_sentence(sents[0], lex),
        "mirror_any": any(mg.is_mirror_sentence(s, lex) for s in sents),
        "generic_close": bool(last) and mg.is_generic_closer(last, lex),
        "ends_q": bool(last) and last.rstrip().endswith("?"),
        "multi_q": (reply or "").count("?") > 1,
        "substance": substantive_sentences(reply, user_text) >= 1,
        "boilerplate": any(b in low for b in BOILERPLATE),
        "invented": any(b in low for b in INVENTED_PREMISE),
        "long": len(sents) > MAX_SENTENCES or len(tokens(reply)) > MAX_WORDS,
        "empty": not sents,
    }
    if prev:
        f["repeat"] = verbatim_run(reply, prev) >= sb.VERBATIM_RUN or (
            bool(content(reply)) and len(content(reply) & content(prev)) / len(content(reply)) >= 0.7)
        f["new_info"] = len(content(reply) - content(prev) - content(user_text)) >= 6
    return f


def score(kind: str, turns: tuple, replies: list[str], needles) -> dict:
    """The composite verdict for one ask and its parts. The scored reply is the LAST one (a follow-up's earlier reply is its seed)."""
    if not replies or not replies[-1].strip():
        return {"pass": None, "parts": {}}
    if kind == "followup" and (len(replies) < 2 or not replies[-2].strip()):
        return {"pass": None, "parts": {}}      # nothing to repeat or to add to: the ask was never exercised
    reply, user = replies[-1], turns[-1]
    prev = replies[-2] if len(replies) > 1 else ""
    f = features(reply, user, prev)
    if kind in ("disclosure", "open"):
        ok = not f["mirror_open"] and not f["generic_close"] and not f["multi_q"] and f["substance"] and not f["long"]
    elif kind == "feedback":
        toks = tokens(reply)
        topical = any(n in toks or (len(n) >= 4 and any(t.startswith(n) for t in toks)) for n in (needles or ()))   # whole words, or a stem of 4+
        f["topical"] = topical
        ok = not f["mirror_open"] and not f["boilerplate"] and not f["invented"] and topical and not f["long"] and not f["multi_q"]
    elif kind == "meta":
        ok = not f["mirror_open"] and not f["boilerplate"] and not f["generic_close"] and not f["multi_q"] and f["substance"] and not f["long"]
    elif kind == "followup":
        ok = not f["repeat"] and f["new_info"] and not f["mirror_open"] and not f["generic_close"]
    elif kind == "control":
        f["answered"] = bool(content(reply)) and not f["empty"]   # "Eight." is an answer
        sents = sentences(reply)
        ok = not f["mirror_open"] and f["answered"] and len(sents) <= CONTROL_MAX_SENTENCES and len(tokens(reply)) <= CONTROL_MAX_WORDS
    else:
        raise ValueError(kind)
    return {"pass": bool(ok), "parts": f}


def post_guard(replies: list[str]) -> list[str]:
    """What the user would hear with ``ZOE_MIRROR_GUARD=on``: the pure trim of each reply (the stream form is proven equal in tests)."""
    zoe_path()
    import mirror_guard as mg

    return [mg.trim(r, "en") for r in replies]


# ── statistics + the keep rule ────────────────────────────────────────────────────────────────────────────────────────────
def rate(k: int, n: int) -> str:
    lo, hi = sp.wilson(k, n)
    return f"{k:>3}/{n:<3} {k / n:5.0%} [{lo:4.0%}-{hi:4.0%}]" if n else "   no data"


def cmp_rates(a: tuple[int, int], b: tuple[int, int]) -> dict:
    """a - b with the 95 % Newcombe-Wilson interval; ``lower``/``higher`` only when the interval excludes 0."""
    (ka, na), (kb, nb) = a, b
    if na == 0 or nb == 0:
        return {"status": "NO_DATA"}
    lo, hi = mab.newcombe_diff(ka, na, kb, nb)
    return {"diff": round(ka / na - kb / nb, 3), "ci": [round(lo, 3), round(hi, 3)], "lower": hi < 0, "higher": lo > 0}


def decide(mirror: dict, composite: dict, kinds: dict, control: dict, problems=()) -> tuple[str, list[str]]:
    """KEEP / DO NOT KEEP and why (see the module docstring). Inputs are ``cmp_rates`` of ship vs none: ``mirror`` (mirror_open rate),
    ``composite`` (overall pass rate), ``kinds`` ({kind: cmp of that kind's pass rate}), ``control`` (answered rate of the factual controls)."""
    why = list(problems)
    for name, c in (("mirror rate", mirror), ("composite", composite), ("control answered", control)):
        if not c or c.get("status") == "NO_DATA":
            why.append(f"{name}: no data in both arms")
    missing = [k for k in KINDS if not kinds.get(k) or kinds[k].get("status") == "NO_DATA"]
    if missing:
        why.append("no data for kind(s): " + ", ".join(missing))
    if why:
        return "DO NOT KEEP", why
    if not mirror["lower"]:
        why.append(f"the mirror-open rate does not separate DOWN (diff {mirror['diff']:+.2f}, CI {mirror['ci']})")
    if not composite["higher"]:
        why.append(f"the conversation pass rate does not separate UP (diff {composite['diff']:+.2f}, CI {composite['ci']})")
    bad = [k for k, c in kinds.items() if c.get("lower")]
    if bad:
        why.append("a kind's pass rate separates DOWN: " + ", ".join(bad))
    if control["lower"]:
        why.append("the factual controls are answered LESS often")
    return ("KEEP" if not why else "DO NOT KEEP"), why


# ── persistence (manner_block_ab's: tombstones, fingerprints, plan digests) ────────────────────────────────────────────────────
def build_plan(phase: str) -> list:
    return [(sp.Ask(a, "CONV", kind, [sp.Turn(t) for t in turns], (), {"needles": needles, "kind": kind, "turns": turns}, sp.BASE_SEED), s)
            for a, kind, turns, needles in corpus(phase) for s in range(SAMPLES[phase])]


def fingerprint(arm: str) -> str:
    """sha256(harness version, arm, the EXACT block text, the split digest, the scorer/lexicon digest): what a row was measured WITH."""
    return hashlib.sha256(json.dumps([HARNESS_VERSION, arm, arm_text(arm), split_digest(), scorer_digest()]).encode()).hexdigest()


def scorer_digest() -> str:
    zoe_path()
    import lexicons

    blob = json.dumps([BOILERPLATE, INVENTED_PREMISE, sorted(STOP), sorted(AFFECT), MAX_SENTENCES, MAX_WORDS, lexicons.load("en").get("mirror")],
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


async def run_phase(runner, phase: str, arms: list[str], run_id: str, log) -> None:
    if len(set(arms)) != len(arms):
        raise ValueError(f"refusing a repeated arm in {arms}")
    CACHE.mkdir(parents=True, exist_ok=True)
    plan = build_plan(phase)
    pdig = hashlib.sha256(json.dumps([(a.id, s) for a, s in plan]).encode()).hexdigest()
    fps = {a: fingerprint(a) for a in arms}
    prior = mab.load_rows(run_id)
    bad = [r for r in prior if r.get("arm") in fps and r.get("fp") != fps[r["arm"]]]
    if bad:
        raise RuntimeError(f"refusing to resume {run_id}: {len(bad)} row(s) were saved under another text/split/scorer/harness; use a new run id")
    done = {(r["arm"], r["phase"], r["ask"], r["sample"]) for r in prior}
    nonce, inv, stamp0 = str(int(time.time())), secrets.token_hex(6), mab.zoe_data_stamp()

    def check(out) -> None:
        if mab.zoe_data_stamp() != stamp0:
            mab.write_tombstone(out, inv, "zoe-data restarted mid-run")
            raise mab.RestartedMidRun(f"zoe-data restarted mid-run: invocation {inv}'s rows are discarded; re-run the same command")

    with mab.results_path(run_id).open("a") as out:
        for arm in arms:
            n = 0
            for ask, s in plan:
                if (arm, phase, ask.id, s) in done:
                    continue
                check(out)
                try:
                    replies = await runner.ask(arm, ask, s, nonce)
                except pab.BudgetExceeded as exc:
                    log(f"  stopped: {exc}")
                    return
                check(out)
                m = ask.meta
                out.write(json.dumps({"arm": arm, "phase": phase, "ask": ask.id, "sample": s, "inv": inv, "fp": fps[arm], "plan": pdig,
                                      "kind": ask.kind, "replies": [r[:600] for r in replies],
                                      "scored": score(ask.kind, m["turns"], replies, m["needles"])}) + "\n")
                out.flush()
                done.add((arm, phase, ask.id, s))
                n += 1
                if n % 10 == 0:
                    log(f"  {arm} {phase}: {n}/{len(plan)} (brain {runner.brain.spent_s:.0f}s)")


# ── the report ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def arm_rows(rows: list[dict], arm: str, phase: str) -> list[dict]:
    return [r for r in rows if r["arm"] == arm and r["phase"] == phase]


def tallies(rows: list[dict], guard: bool = False) -> dict:
    """{'n', 'mirror_open', 'pass', parts..., 'by_kind': {kind: (k, n)}} over the scored rows; ``guard`` re-scores the post-trim replies."""
    t = {"n": 0, "pass": 0, "by_kind": {k: [0, 0] for k in KINDS}, "parts": {}}
    for r in rows:
        meta = {a: (k, tr, nd) for a, k, tr, nd in DEV + HELD}[r["ask"]]
        replies = post_guard(r["replies"]) if guard else r["replies"]
        sc = score(meta[0], meta[1], replies, meta[2])
        if sc["pass"] is None:
            continue
        t["n"] += 1
        t["pass"] += int(sc["pass"])
        t["by_kind"][r["kind"]][0] += int(sc["pass"])
        t["by_kind"][r["kind"]][1] += 1
        for p, v in sc["parts"].items():
            c = t["parts"].setdefault(p, [0, 0])
            c[0] += int(bool(v))
            c[1] += 1
    return t


PARTS = ("mirror_open", "mirror_any", "generic_close", "ends_q", "multi_q", "substance", "long", "boilerplate", "invented", "repeat", "new_info", "topical", "answered")


def report(run_id: str, phase: str, arms: list[str], ship: str | None) -> str:
    rows = mab.load_rows(run_id)
    problems = []
    fps = {a: fingerprint(a) for a in arms}
    stale = [r for r in rows if r.get("arm") in fps and r["phase"] == phase and r.get("fp") != fps[r["arm"]]]
    if stale:
        problems.append(f"{len(stale)} row(s) were saved under a different text/split/scorer/harness than the current one: use a new run id")
    plans = {r["plan"] for r in rows if r["phase"] == phase and r.get("arm") in fps}
    if len(plans) > 1:
        problems.append(f"{phase}: rows come from {len(plans)} different plans")
    expect = len(build_plan(phase))
    for a in arms:
        have = len(arm_rows(rows, a, phase))
        if have != expect:
            problems.append(f"{phase} arm {a}: {have} of {expect} planned asks have a row")
    if problems and stale:
        return "REFUSED (rows not comparable):" + "".join("\n  - " + p for p in problems)
    lines = [f"== {phase.upper()}  run {run_id}  (scorers: {scorer_digest()}, split {split_digest()[:12]}) =="]
    tall = {a: tallies(arm_rows(rows, a, phase)) for a in arms}
    gall = {a: tallies(arm_rows(rows, a, phase), guard=True) for a in arms}
    hdr = f"{'':26}" + "".join(f"{a:>28}" for a in arms)
    lines += [hdr, f"{'asks scored':26}" + "".join(f"{tall[a]['n']:>28}" for a in arms)]
    lines.append("-- the prompt alone (raw replies; lower is better for the first rows) --")
    for p in PARTS:
        if any(p in tall[a]["parts"] for a in arms):
            lines.append(f"{p:26}" + "".join(f"{rate(*tall[a]['parts'].get(p, [0, 0])):>28}" for a in arms))
    lines.append(f"{'CONVERSATION PASS':26}" + "".join(f"{rate(tall[a]['pass'], tall[a]['n']):>28}" for a in arms))
    for k in KINDS:
        lines.append(f"{'  ' + k:26}" + "".join(f"{rate(*tall[a]['by_kind'][k]):>28}" for a in arms))
    lines.append("-- with ZOE_MIRROR_GUARD=on applied to the same replies (the pure trim) --")
    for p in ("mirror_open", "generic_close", "empty"):
        lines.append(f"{p:26}" + "".join(f"{rate(*gall[a]['parts'].get(p, [0, 0])):>28}" for a in arms))
    lines.append(f"{'CONVERSATION PASS':26}" + "".join(f"{rate(gall[a]['pass'], gall[a]['n']):>28}" for a in arms))
    verdict = ""
    if ship and ship in arms and "none" in arms:
        s, n = tall[f"{ship}"], tall["none"]
        mirror = cmp_rates(tuple(s["parts"].get("mirror_open", [0, 0])), tuple(n["parts"].get("mirror_open", [0, 0])))
        comp = cmp_rates((s["pass"], s["n"]), (n["pass"], n["n"]))
        kinds = {k: cmp_rates(tuple(s["by_kind"][k]), tuple(n["by_kind"][k])) for k in KINDS}
        ctl = cmp_rates(tuple(s["parts"].get("answered", [0, 0])), tuple(n["parts"].get("answered", [0, 0])))
        v, why = decide(mirror, comp, kinds, ctl, problems)
        verdict = f"\nVERDICT ({ship} vs none, {phase}): {v}" + "".join("\n  - " + w for w in why)
        lines.append(f"mirror-open diff {mirror.get('diff')} CI {mirror.get('ci')}; pass diff {comp.get('diff')} CI {comp.get('ci')}")
    return "\n".join(lines) + verdict


def arm_id(name: str) -> str:
    """CLI arm name -> the arm id the rows carry: ``none`` stays, a variant ``V0`` is ``on:V0``."""
    return name if name == "none" or name.startswith("on:") else f"on:{name}"


# ── the live driver ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def live(args) -> int:
    with pab.whole_environment():
        return _live(args)


def _live(args) -> int:
    configure_rig()
    if args.phase == "report":
        print(report(args.run_id, args.split, [arm_id(a) for a in args.arms.split(",") if a], arm_id(args.ship) if args.ship else None))
        return 0
    code, msg = mab.live_preflight()
    if code != -1:
        print(msg)
        return code
    for dsn in ("POSTGRES_URL", "DATABASE_URL", "ZOE_DATABASE_URL"):
        if os.environ.get(dsn):
            print(f"refused: {dsn} is set in this environment - the rig must have no database")
            return 2
    zoe_path()
    user = sb.new_demo_user()
    sb.assert_demo_user(user)
    token = secrets.token_hex(16)
    os.environ.update({"ZOE_BRAIN_TOKEN": token, "ZOE_FLUE_BRAIN_URL": f"http://127.0.0.1:{mab.SIDECAR_PORT}", "ZOE_FLUE_WIRE": "2",
                       "ZOE_FLUE_STREAM_ENABLED": "1", "ZOE_FLUE_BRAIN_TIMEOUT_S": "120"})
    import zoe_flue_client as zc

    world = sp.World(sp.BASE_SEED)
    arms = [arm_id(a) for a in args.arms.split(",") if a]
    for a in arms:
        if a != "none":
            print(f"arm {a}: {mab._tokens(variant_text(a.split(':', 1)[1]))} tokens, {len(variant_text(a.split(':', 1)[1]))} chars")
    t0 = time.monotonic()
    with mab.scratch_sidecar(token, user):
        runner = mab.Runner(zc, world, user, args.brain_budget_s)
        try:
            asyncio.run(run_phase(runner, args.phase, arms, args.run_id, print))
        finally:
            runner.close()
            mab.ledger_add(runner.brain.spent_s, f"{args.run_id} {args.phase} {','.join(arms)} calls={runner.brain.calls}")
    print(f"brain time this invocation {runner.brain.spent_s:.0f}s over {runner.brain.calls} calls; cumulative {mab.ledger_spent():.0f}s of "
          f"{TOTAL_BUDGET_S:.0f}s (wall {time.monotonic() - t0:.0f}s)")
    return 0


# ── offline selftest ──────────────────────────────────────────────────────────────────────────────────────────────────────────
def selftest() -> int:
    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(f"  {'ok  ' if cond else 'FAIL'} {name}")
        ok &= bool(cond)

    ids = [a for a, *_ in DEV + HELD]
    check("ids are unique and the halves are disjoint", len(ids) == len(set(ids)))
    check("every kind is in both halves", all({k for _, k, *_ in half} == set(KINDS) for half in (DEV, HELD)))
    check(f"the split is frozen (digest {split_digest()[:12]})", SPLIT_DIGEST == split_digest())
    check("every feedback ask carries needles", all(nd for _, k, _, nd in DEV + HELD if k == "feedback"))

    def p(kind, turns, replies, needles=None):
        return score(kind, turns, replies, needles)["pass"]

    dis = ("My bus was late and I missed my meeting.",)
    good = "Late buses are the worst. Ask if they can move it to tomorrow, most people don't mind one reschedule."
    check("a conversational disclosure reply PASSES", p("disclosure", dis, [good]) is True)
    check("control: an 'It sounds like' opener FAILS", p("disclosure", dis, ["It sounds like you had a frustrating morning. " + good]) is False)
    check("control: 'That sounds like ...' mirror + generic question FAILS", p("disclosure", dis, ["That sounds like such a stressful start to your day. How are you holding up?"]) is False)
    check("control: substance followed by a generic closer FAILS", p("disclosure", dis, [good + " How are you feeling about it?"]) is False)
    check("control: two questions FAIL", p("disclosure", dis, [good + " Was it the 7 am one? Did they tell you why?"]) is False)
    check("control: a reply with no substance (sympathy only) FAILS", p("disclosure", dis, ["Oh no, that is rough. I am sorry."]) is False)
    check("control: a monologue FAILS (long)", p("disclosure", dis, [" ".join([good] * 3)]) is False)
    fb = ("You said February wrong just now, you skipped the first r.",)
    nd = ("february", "pronounc", "said", "say", "r")
    check("feedback taken as feedback PASSES", p("feedback", fb, ["Fair, February has that first r. I'll say it properly."], nd) is True)
    check("control: boilerplate FAILS", p("feedback", fb, ["I hear what you're saying, and I really appreciate you pointing that out. It's something I'm always trying to get better at."], nd) is False)
    check("control: an invented apology FAILS", p("feedback", fb, ["I'm sorry I said something that upset you; I'll be more mindful."], nd) is False)
    check("control: an off-topic reply FAILS (not topical)", p("feedback", fb, ["Thanks for telling me."], nd) is False)
    fu = ("Why is the sea salty?", "What else can you tell me about it?")
    first = "Rivers wash tiny amounts of salt out of rocks and carry it to the sea, where the water evaporates and the salt stays."
    check("a follow-up that adds something PASSES", p("followup", fu, [first, "The Dead Sea is about ten times saltier because nothing flows out of it."]) is True)
    check("control: a verbatim repeat FAILS", p("followup", fu, [first, first]) is False)
    check("control: a paraphrased repeat FAILS (content overlap)", p("followup", fu, [first, "Salt gets washed from rocks by rivers, then the sea water evaporates and the salt stays behind."]) is False)
    ctl = ("How many legs does a spider have?",)
    check("a plain answer to a factual control PASSES", p("control", ctl, ["Eight."]) is True and p("control", ctl, ["A spider has eight legs, two more than an insect."]) is True)
    check("control: an empty reply is not an answer (and is not scored)", p("control", ctl, [""]) is None)
    check("control: a mirror preface on a factual question FAILS", p("control", ctl, ["It sounds like you are curious about spiders. They have eight legs."]) is False)
    # the guard simulation: a mirror + substance reply passes after the trim, a mirror + generic closer is left alone
    mg, lex = _lex()
    raw = "It sounds like you had a frustrating morning. " + good
    check("post-guard: the trimmed reply passes where the raw one failed", p("disclosure", dis, post_guard([raw])) is True)
    check("post-guard: a mirror + generic question is NOT 'fixed' (left alone, still fails)", p("disclosure", dis, post_guard(["That sounds like such a stressful start to your day. How are you holding up?"])) is False)
    # decide(): the controls
    flat = cmp_rates((10, 40), (10, 40))
    up = cmp_rates((30, 40), (10, 40))
    down = cmp_rates((5, 40), (25, 40))
    kinds_ok = {k: cmp_rates((20, 30), (20, 30)) for k in KINDS}
    check("control: identical arms never KEEP", decide(flat, flat, kinds_ok, flat)[0] == "DO NOT KEEP")
    check("control: mirror DOWN + pass UP + nothing worse => KEEP", decide(down, up, kinds_ok, flat)[0] == "KEEP")
    check("control: ... but a kind that separates DOWN blocks the keep", decide(down, up, dict(kinds_ok, followup=down), flat)[0] == "DO NOT KEEP")
    check("control: ... and the factual controls answered less blocks it", decide(down, up, kinds_ok, down)[0] == "DO NOT KEEP")
    check("control: a missing comparison is never skipped", decide(down, up, {k: v for k, v in kinds_ok.items() if k != "meta"}, flat)[0] == "DO NOT KEEP"
          and decide({"status": "NO_DATA"}, up, kinds_ok, flat)[0] == "DO NOT KEEP")
    check("control: mirror unchanged but pass UP is not enough", decide(flat, up, kinds_ok, flat)[0] == "DO NOT KEEP")
    return 0 if ok else 2


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--phase", choices=("dev", "heldout", "report"), default="report")
    ap.add_argument("--arms", default="none,LEGACY,V0", help="comma list: none and/or LEGACY, C1..C3, V0 (the text now in the lexicon)")
    ap.add_argument("--split", choices=("dev", "heldout"), default="heldout", help="report: which half")
    ap.add_argument("--ship", default=None, help="report: the arm whose verdict vs none is printed (e.g. V0)")
    ap.add_argument("--run-id", default="conv-1")
    ap.add_argument("--brain-budget-s", type=float, default=1200.0, help="this invocation's brain-time cap")
    args = ap.parse_args()
    return selftest() if args.selftest else live(args)


if __name__ == "__main__":
    sys.exit(main())
