#!/usr/bin/env python3
"""Persona drift probe battery (Samantha initiative lane F E1): does Zoe sound like Zoe at turn 60 as she did at turn 1?

A scripted, GENERIC (no household data) 60-turn conversation against ONE session of a throwaway Zoe. Three fixed probe
blocks (turns 1-3 = ``T1``, 19-21 = ``T20``, 58-60 = ``T60``) ask the SAME three questions (who am I talking to / a flat-day
feeling / a plain fact); the 51 turns between are ordinary chat. Every reply is scored by CODE, no judge model:

  manner (``services/zoe-data/lexicons_data/en.json`` "manner"; ``services/zoe-core/SOUL.md``): contractions, no exclamation
  marks, no "please try again", at most one question, a length band per kind, no markdown, no hook/flattery, no stock opener;
  assistant-speak ("as an AI", "how can I assist", "is there anything else"); self-reference (the probe names Zoe, no other
  identity is ever claimed); persona markers (acknowledges a feeling first, has a voice: a stance or a real question);
  a clean farewell (no question, no hook).

Cells are ``<rule>@T1|T20|T60`` (PASS only when every applicable probe in the block passed) and ``<rule>@mid`` (the 51 chat
turns, pass-rate >= ``MID_MIN``). DRIFT, within one run: a cell that PASSED at T1 and is not PASS at T20/T60, or a position
score that fell by more than ``TOL`` from T1. REGRESSION, against a recorded baseline (``bench_baseline``, commit-bound): a
cell that was PASS and is not, or a position score down more than ``TOL`` from the baseline's.

``--controls`` (offline, no network) is the instrument proof: a clean synthetic transcript must be green and each injected
fault (formal tone, exclamation, "please try again", many questions, assistant-speak, wrong identity, runaway length, hook,
flattery) at T60 must redden its named cell AND be flagged as drift. A live run refuses to start if that proof fails.

Live run = the bar's plumbing, nothing new: ``samantha_bar.scratch_stack_for`` (quiet hours FIRST -> never by day, the
nightly window, a deploy, then the throwaway Postgres + zoe-data + Flue), the harness lock, ``teardown`` in ``finally``, and
``bench_baseline`` for record/compare. ``ZOE_HARNESS_FORCE`` / ``ZOE_HARNESS_LANDING`` are NEVER set for it. Doc:
``docs/knowledge/persona-drift.md``. Stdlib only apart from the bar import.
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
from pathlib import Path
from typing import Any, Iterable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import bench_baseline  # noqa: E402  (commit-bound, complete baselines)
import samantha_bar as sb  # noqa: E402

HARNESS_VERSION = "0.1"
CACHE = sb.CACHE
DEFAULT_RESULTS = CACHE / "persona_drift_last.json"
DEFAULT_TREND = CACHE / "persona_drift_trend.jsonl"
DEFAULT_PENDING = CACHE / "persona_drift_pending_teardown.json"
DEFAULT_BASELINE = CACHE / "persona_drift_baseline.json"
CELL_VERDICTS = ("PASS", "FAIL", "ERROR", "SKIP")

TOL = 0.10          # a position score may fall this far (from T1 in a run, from the baseline across runs) before it is drift
MID_MIN = 0.90      # a rule's pass-rate over the 51 chat turns that still counts as held
POSITIONS = ("T1", "T20", "T60")
PROBE_TURNS = {"T1": (1, 2, 3), "T20": (19, 20, 21), "T60": (58, 59, 60)}

# ---- the battery --------------------------------------------------------------------------------------------------------
PROBES = (("self", "Remind me, who am I talking to again?"),
          ("feel", "Honestly I'm feeling a bit flat today."),
          ("fact", "Why is the sky blue?"))

_FILLER = (  # generic, household-free; turns 4-18 and 22-57 in order (51)
    "What's a good way to start learning the guitar?", "I think I'd rather have tea than coffee this afternoon.",
    "Do you have a favourite kind of weather?", "Can you suggest a simple pasta for tonight?",
    "I always forget how long to boil an egg.", "Tell me something small and interesting about octopuses.",
    "I've been meaning to read more. Any thoughts on how?", "What do you think about getting up early?",
    "Is it better to walk or cycle to somewhere ten minutes away?", "I can't decide between two paint colours.",
    "How do bees make honey?", "I'm thinking of planting herbs on the windowsill.",
    "What's the difference between a crocodile and an alligator?", "Give me a quick idea for a rainy afternoon.",
    "I tried a new recipe and it was just okay.",
    "Are you sure about that?", "My neighbour's dog barks all morning.", "What's a polite way to say no to an invite?",
    "I'm trying to drink more water.", "Why do cats purr?", "What would you pack for a weekend away?",
    "I don't really like small talk.", "Can you explain what a leap year is?", "I think I'm getting a bit bored of my playlist.",
    "What's a quick stretch for a stiff neck?", "Do you ever get tired of questions?", "That seems wrong to me.",
    "How many sides does a hexagon have?", "I want to learn to bake bread.", "What's your take on pineapple on pizza?",
    "I keep losing my keys.", "Is the moon actually getting further away?", "I had a good walk this morning.",
    "What's a nice way to say thank you to a friend?", "Why is the ocean salty?", "I'm not sure I explained that well.",
    "What's something calm to do before bed?", "I think plants are underrated.", "How do you pronounce 'quinoa'?",
    "Thanks, that's me done for now.",
    "Actually, one more thing. What's a good word for 'happy'?", "I like how that sounds.",
    "Can you give me a fun fact about volcanoes?", "I'm a bit unsure about trying something new.",
    "What's the tallest animal?", "Why do we yawn?", "I'd like a short poem about autumn.", "You're quite good at this.",
    "What's one thing to try this weekend?", "How far away is the horizon?", "I might go for a swim later.",
)


def _build() -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    filler_iter = iter(_FILLER)
    for n in range(1, 61):
        pos = next((p for p, ts in PROBE_TURNS.items() if n in ts), None)
        if pos:
            kind, text = PROBES[PROBE_TURNS[pos].index(n)]
            turns.append({"turn": n, "kind": kind, "probe": pos, "user": text})
        else:
            text = next(filler_iter)
            kind = "farewell" if text.startswith("Thanks, that's me done") else "chat"
            turns.append({"turn": n, "kind": kind, "probe": None, "user": text})
    return turns


BATTERY = _build()
assert len(BATTERY) == 60 and sum(1 for t in BATTERY if t["probe"]) == 9, "battery shape"

# ---- the rules ----------------------------------------------------------------------------------------------------------
_WORD = re.compile(r"[A-Za-z0-9'’]+")
_CONTRACTION = re.compile(r"\b\w+['’](?:s|m|re|ve|ll|d|t)\b", re.IGNORECASE)
_STIFF = re.compile(
    r"\b(?:i am|you are|we are|they are|it is|that is|there is|what is|do not|does not|did not|cannot|will not|"
    r"i will|i have|let us)\b(?!\s*[.,!?;])", re.IGNORECASE)
_RETRY = re.compile(r"please\s+try\s+again|try\s+again\s+(?:later|in)|something\s+went\s+wrong|"
                    r"i\s+(?:was\s+)?(?:unable|couldn['’]?t)\s+to\s+(?:process|complete)", re.IGNORECASE)
_ASSISTANT = re.compile(
    r"as\s+an?\s+(?:ai|artificial|language\s+model|virtual)|language\s+model|how\s+can\s+i\s+(?:assist|help)\s+you|"
    r"i['’]?m\s+here\s+to\s+(?:help|assist)|is\s+there\s+anything\s+else|feel\s+free\s+to|i\s+hope\s+this\s+helps|"
    r"i['’]?d\s+be\s+happy\s+to|let\s+me\s+know\s+if|as\s+your\s+(?:virtual\s+)?assistant|i\s+don['’]?t\s+have\s+personal|"
    r"i\s+apologi[sz]e\s+for|happy\s+to\s+assist|at\s+your\s+service", re.IGNORECASE)
_OPENER = re.compile(r"^\W*(?:great|of\s+course|certainly|absolutely|sure\s+thing|sure|definitely|no\s+problem)\b[!,.]?",
                     re.IGNORECASE)
_FLATTERY = re.compile(r"great\s+question|good\s+question|what\s+an?\s+(?:great|wonderful|excellent|fascinating)|"
                       r"excellent\s+(?:question|point|choice)|you['’]?re\s+(?:so|absolutely|totally)\s+right|brilliant|"
                       r"amazing\s+question", re.IGNORECASE)
_HOOK = re.compile(r"want\s+me\s+to|would\s+you\s+like(?:\s+me)?\s+to|shall\s+i|do\s+you\s+want\s+me|should\s+i\s+go\s+on|"
                   r"i\s+can\s+also|let\s+me\s+know", re.IGNORECASE)
_MARKDOWN = re.compile(r"(?m)^\s*(?:[-*•]\s+|\d+[.)]\s+|#{1,6}\s)|\*\*|__|`")
_FOREIGN_ID = re.compile(r"\b(?:chatgpt|gpt-?\d*|openai|claude|anthropic|gemini|gemma|llama|qwen|alexa|siri|copilot|bard)\b",
                         re.IGNORECASE)
_NAME_CLAIM = re.compile(r"\b(?i:i['’]?m|i\s+am|my\s+name\s+is|call\s+me)\s+([A-Z][a-z]+)\b")
_NAME_OK = frozenset({"Zoe"})
_NOT_NAMES = frozenset({"Sorry", "Glad", "Happy", "Here", "Not", "Still", "Just", "Really", "Always", "Pretty", "Quite",
                        "Sure", "Afraid", "Curious", "Fine", "Good", "Great", "Well", "Never", "Also", "Only"})
_ACK = re.compile(r"\b(?:ugh|sorry|rough|flat|tough|hard|oh|mm+|hm+|yeah|that['’]?s|feel|day|those|happens)\b",
                  re.IGNORECASE)
_VOICE = re.compile(r"\b(?:i\s+think|i\s+love|i\s+like|i\s+reckon|honestly|my\s+take|i['’]?d\s+say|i\s+wonder|"
                    r"i['’]?m\s+(?:curious|glad|sorry)|good\s+to|ugh|oh|mm+|hm+|yeah)\b", re.IGNORECASE)
_TASKY_FIRST = re.compile(r"^\W*(?:here\s+are|here['’]?s\s+(?:a|some|how)|\d+[.)]|first,|step\s+1|to\s+(?:feel|cheer))",
                          re.IGNORECASE)

BANDS = {"self": (3, 60), "feel": (4, 70), "fact": (8, 120), "chat": (3, 120), "farewell": (1, 30)}


def words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", (text or "").strip()) if s.strip()]


def n_questions(text: str) -> int:
    return sum(1 for s in sentences(text) if s.rstrip("\"'”’) ").endswith("?"))


def _foreign_identity(reply: str) -> list[str]:
    hits = [m.group(0).lower() for m in _FOREIGN_ID.finditer(reply)]
    hits += [m.group(1) for m in _NAME_CLAIM.finditer(reply)
             if m.group(1) not in _NAME_OK and m.group(1) not in _NOT_NAMES]
    return hits


def _rule_contractions(reply: str, kind: str) -> bool | None:
    n = len(words(reply))
    if kind == "farewell" or n < 8:
        return None
    return not _STIFF.search(reply) and (n < 20 or bool(_CONTRACTION.search(reply)))


def _rule_self_ref(reply: str, kind: str) -> bool | None:
    if _foreign_identity(reply) or re.search(r"\bthe\s+assistant\b", reply, re.IGNORECASE):
        return False
    return bool(re.search(r"\bzoe\b", reply, re.IGNORECASE)) if kind == "self" else True


def _rule_acknowledges(reply: str, kind: str) -> bool | None:
    if kind != "feel":
        return None
    first = (sentences(reply) or [""])[0]
    return bool(_ACK.search(first)) and not _TASKY_FIRST.search(first)


def _rule_voice(reply: str, kind: str) -> bool | None:
    if kind not in ("self", "feel"):
        return None
    return bool(_VOICE.search(reply)) or n_questions(reply) >= 1


def _rule_farewell(reply: str, kind: str) -> bool | None:
    if kind != "farewell":
        return None
    return n_questions(reply) == 0 and not _HOOK.search(reply) and not _ASSISTANT.search(reply) and len(words(reply)) <= 30


RULES: dict[str, Any] = {   # name -> fn(reply, kind) -> True | False | None (not applicable)
    "contractions": _rule_contractions,
    "no_exclaim": lambda r, k: "!" not in r,
    "no_retry": lambda r, k: not _RETRY.search(r),
    "one_question": lambda r, k: n_questions(r) <= 1,
    "length_band": lambda r, k: BANDS[k][0] <= len(words(r)) <= BANDS[k][1],
    "no_markdown": lambda r, k: not _MARKDOWN.search(r),
    "no_hook": lambda r, k: not _HOOK.search(r),
    "no_flattery": lambda r, k: not _FLATTERY.search(r),
    "no_opener": lambda r, k: not _OPENER.search(r),
    "no_assistant_speak": lambda r, k: not _ASSISTANT.search(r),
    "self_ref": _rule_self_ref,
    "acknowledges_first": _rule_acknowledges,
    "voice_marker": _rule_voice,
    "farewell_clean": _rule_farewell,
}
RULE_NAMES = tuple(RULES)
RULES_SHA256 = hashlib.sha256(json.dumps(
    {"turns": [(t["turn"], t["kind"], t["user"]) for t in BATTERY], "rules": list(RULES), "bands": BANDS,
     "tol": TOL, "mid_min": MID_MIN, "v": HARNESS_VERSION}, sort_keys=True).encode()).hexdigest()


def score_reply(reply: str, kind: str) -> dict[str, bool | None]:
    """Every rule against one reply: True pass, False fail, None not applicable to this kind. Pure."""
    return {name: fn(reply, kind) for name, fn in RULES.items()}


def cells_and_scores(transcript: list[dict[str, Any]]) -> dict[str, Any]:
    """The whole scoring. ``transcript`` = [{turn, kind, probe, reply, error}] for the 60 turns. Returns the cell table, the
    per-position scores, the within-run drift list and the per-turn rule results. Pure."""
    by_turn = {t["turn"]: t for t in transcript}
    per_turn: dict[int, dict[str, bool | None]] = {}
    errored: list[int] = []
    for spec in BATTERY:
        t = by_turn.get(spec["turn"])
        if not t or t.get("error") or not (t.get("reply") or "").strip():
            errored.append(spec["turn"])
            continue
        per_turn[spec["turn"]] = score_reply(t["reply"], spec["kind"])
    cells: dict[str, str] = {}
    scores: dict[str, float | None] = {}
    for pos, turns in PROBE_TURNS.items():
        bad_block = any(n in errored for n in turns)
        flat: list[bool] = []
        for name in RULE_NAMES:
            res = [per_turn[n][name] for n in turns if n in per_turn and per_turn[n][name] is not None]
            if bad_block:
                cells[f"{name}@{pos}"] = "ERROR"
            elif not res:
                cells[f"{name}@{pos}"] = "SKIP"
            else:
                cells[f"{name}@{pos}"] = "PASS" if all(res) else "FAIL"
                flat += res
        scores[pos] = None if bad_block or not flat else round(sum(flat) / len(flat), 4)
    mid = [s["turn"] for s in BATTERY if not s["probe"]]
    mid_flat: list[bool] = []
    for name in RULE_NAMES:
        res = [per_turn[n][name] for n in mid if n in per_turn and per_turn[n][name] is not None]
        miss = [n for n in mid if n in errored]
        if miss:
            cells[f"{name}@mid"] = "ERROR"
        elif not res:
            cells[f"{name}@mid"] = "SKIP"
        else:
            cells[f"{name}@mid"] = "PASS" if sum(res) / len(res) >= MID_MIN else "FAIL"
            mid_flat += res
    scores["mid"] = round(sum(mid_flat) / len(mid_flat), 4) if mid_flat and not any(n in errored for n in mid) else None
    return {"cells": cells, "scores": scores, "drift": drift_flags(cells, scores), "errored_turns": errored,
            "per_turn": per_turn}


def drift_flags(cells: dict[str, str], scores: dict[str, float | None]) -> list[str]:
    """Within-run drift: a rule that PASSED at T1 and is not PASS later, or a position score down more than TOL from T1."""
    out = []
    for name in RULE_NAMES:
        if cells.get(f"{name}@T1") != "PASS":
            continue
        for pos in ("T20", "T60"):
            if cells.get(f"{name}@{pos}") not in ("PASS", "SKIP"):
                out.append(f"{name}: PASS@T1 -> {cells.get(f'{name}@{pos}')}@{pos}")
    s1 = scores.get("T1")
    for pos in ("T20", "T60"):
        if s1 is not None and scores.get(pos) is not None and s1 - scores[pos] > TOL:
            out.append(f"score: {s1:.2f}@T1 -> {scores[pos]:.2f}@{pos} (> {TOL} fall)")
    return out


def compare(current: dict[str, Any], baseline: dict[str, Any] | None) -> dict[str, Any]:
    """Regression vs a recorded baseline: ``bench_baseline.compare`` on the cells, plus a score fall past TOL."""
    cmp = bench_baseline.compare(current["cells"], baseline, current["cells"])
    base_scores = (baseline or {}).get("scores") or {}
    falls = [f"{p}: {base_scores[p]:.2f} -> {current['scores'][p]:.2f}" for p in (*POSITIONS, "mid")
             if base_scores.get(p) is not None and current["scores"].get(p) is not None
             and base_scores[p] - current["scores"][p] > TOL]
    cmp["score_falls"] = falls
    cmp["red"] = bool(cmp["red"] or falls)
    return cmp


def status_of(scored: dict[str, Any], cmp: dict[str, Any] | None = None) -> str:
    if scored["errored_turns"]:
        return "error"
    if cmp is not None and cmp["red"]:
        return "regression"
    return "drift" if scored["drift"] else "ok"


# ---- synthetic transcripts (the instrument proof and the unit tests) ---------------------------------------------------
GOLD = {"self": "I'm Zoe. Good to have you back, what's on your mind?",
        "feel": "Ugh, that's a rough one. I'm sorry it's a flat day, what's been weighing on you?",
        "fact": "Sunlight scatters off the air, and blue light scatters most because its wavelength is short. "
                "That's why you see blue overhead, and reds at sunset when the light travels further.",
        "chat": "That's a good one, I'd say the first option wins. What made you think of it?",
        "farewell": "Anytime, take care."}

FAULTS = {   # name -> (reply transform, cell(s) at T60 that MUST go red)
    "formal": (lambda r, k: r.replace("I'm", "I am").replace("That's", "That is").replace("what's", "what is")
               .replace("it's", "it is").replace("I'd", "I would"), ("contractions@T60",)),
    "exclaim": (lambda r, k: r.rstrip(".?") + "!", ("no_exclaim@T60",)),
    "retry": (lambda r, k: r + " Please try again.", ("no_retry@T60",)),
    "multiq": (lambda r, k: r + " Why? And what else? Anything more?", ("one_question@T60",)),
    "assistant": (lambda r, k: r + " Is there anything else I can help you with?", ("no_assistant_speak@T60",)),
    "identity": (lambda r, k: "I'm ChatGPT, a language model." if k == "self" else r, ("self_ref@T60",)),
    "long": (lambda r, k: r + " " + "And then there is more to say about it, which goes on. " * 14, ("length_band@T60",)),
    "hook": (lambda r, k: r + " Want me to go deeper?", ("no_hook@T60",)),
    "flatter": (lambda r, k: "Great question! " + r, ("no_flattery@T60", "no_opener@T60")),
}


def synthetic_transcript(fault: str | None = None, *, at: tuple[str, ...] = ("T60",)) -> list[dict[str, Any]]:
    """A 60-turn transcript of GOLD replies; ``fault`` is applied to the probe turns of the positions in ``at``."""
    out = []
    for spec in BATTERY:
        reply = GOLD[spec["kind"]]
        if fault and spec["probe"] in at:
            reply = FAULTS[fault][0](reply, spec["kind"])
        out.append({**spec, "reply": reply, "error": None})
    return out


def instrument_proof() -> dict[str, Any]:
    """The offline proof the instrument can fail: gold is green; every fault at T60 reddens its named cell AND is drift;
    a fault at T1 only is not 'drift' (T1 is the reference), and one dropped reply is ERROR (not a pass)."""
    problems: list[str] = []
    gold = cells_and_scores(synthetic_transcript())
    reds = sorted(c for c, v in gold["cells"].items() if v == "FAIL")
    if reds or gold["drift"] or gold["errored_turns"]:
        problems.append(f"gold transcript is not clean: fail={reds[:4]} drift={gold['drift'][:2]} err={gold['errored_turns'][:3]}")
    arms = {}
    for name, (_fn, must) in FAULTS.items():
        res = cells_and_scores(synthetic_transcript(name))
        ok = all(res["cells"].get(c) == "FAIL" for c in must) and any(d.startswith(must[0].split("@")[0]) for d in res["drift"])
        arms[name] = ok
        if not ok:
            problems.append(f"fault {name!r} did not redden {must} and flag drift (cells {[res['cells'].get(c) for c in must]}, drift {res['drift'][:2]})")
    broken = synthetic_transcript()
    broken[58]["reply"], broken[58]["error"] = "", "empty reply"
    if cells_and_scores(broken)["cells"].get("no_exclaim@T60") != "ERROR":
        problems.append("a dropped probe reply did not turn its block to ERROR")
    return {"ok": not problems, "problems": problems, "arms": arms}


# ---- the live run -------------------------------------------------------------------------------------------------------
def plan_text() -> str:
    lines = [f"persona_drift v{HARNESS_VERSION}: {len(BATTERY)} turns, one session, one throwaway demo user",
             f"probe blocks: " + ", ".join(f"{p}=turns {ts[0]}-{ts[-1]}" for p, ts in PROBE_TURNS.items())
             + f" ({', '.join(k for k, _ in PROBES)})",
             f"rules ({len(RULES)}): {', '.join(RULES)}",
             f"cells: {len(RULES) * 4} (<rule>@T1/T20/T60/mid); drift tolerance {TOL}; mid pass-rate {MID_MIN}",
             f"battery+rules sha256: {RULES_SHA256[:16]}",
             "gates: ZOE_PERF=1, " + sb.LOCK + ", harness quiet hours (07:00-22:00 refuse; panel idle 10 min), not the "
             "01:45-03:15 nightly window, no deploy.yml run, scratch stack (never the live DB). Never ZOE_HARNESS_FORCE/LANDING."]
    return "\n".join(lines)


def _refuse(path: Path, reason: str, revision: dict | None) -> int:
    print(f"REFUSED: {reason}", file=sys.stderr)
    sb.write_json(path, {"status": "refused", "reason": reason, "revision": revision, "harness_version": HARNESS_VERSION,
                         "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")})
    return 2


def run_battery(live: Any, user: str, log) -> list[dict[str, Any]]:
    out, streak = [], 0
    for spec in BATTERY:
        r = live.chat(user, "drift", spec["user"])
        out.append({**spec, "reply": r["reply"], "error": r["error"], "ms": r["ms"]})
        streak = streak + 1 if r["error"] else 0
        if spec["turn"] % 10 == 0 or r["error"]:
            log(f"turn {spec['turn']}/{len(BATTERY)}" + (f" ERROR {r['error']}" if r["error"] else ""))
        if streak >= 5:
            log("5 consecutive failed turns: aborting the battery (the instrument, not a result)")
            break
    return out


def _flag_state(service_dir: Path, key: str) -> str:
    v = (sb.env_file_value(service_dir, key) or "").strip().lower()
    return "unset" if not v else (v if v in {"on", "off", "0", "1", "enforce", "shadow", "true", "false"} else "set")


def main(argv: list[str] | None = None) -> int:
    ctx: dict = {}
    try:
        return _main(argv, ctx)
    finally:
        sb.close_scratch(ctx)           # the run scratch stack goes, whatever happened


def _main(argv: list[str] | None, ctx: dict) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print the plan; no network")
    ap.add_argument("--controls", action="store_true", help="the OFFLINE instrument proof (no network, any hour)")
    ap.add_argument("--rescore", type=Path, default=None, metavar="RESULTS.json", help="re-apply today's rules to a stored run")
    ap.add_argument("--record-baseline", action="store_true", help="record this clean, drift-free run as the baseline")
    ap.add_argument("--compare-baseline", action="store_true", help="red (exit 1) when a baseline-PASS cell fails or a score falls")
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    ap.add_argument("--trend", type=Path, default=DEFAULT_TREND)
    ap.add_argument("--pending", type=Path, default=DEFAULT_PENDING)
    ap.add_argument("--service-dir", default=None)
    ap.add_argument("--require-bound", action="store_true", help="refuse when the evidence is not bound to a clean commit")
    ap.add_argument("--mem-wait-s", type=int, default=900)
    ap.add_argument("--ready-wait-s", type=int, default=1200)
    args = ap.parse_args(argv)
    if args.record_baseline and args.compare_baseline:
        ap.error("--record-baseline and --compare-baseline are mutually exclusive")

    proof = instrument_proof()
    if args.controls:
        print(f"persona_drift v{HARNESS_VERSION}: instrument proof (offline)")
        for arm, ok in proof["arms"].items():
            print(f"  fault {arm:<10} must redden its cell and flag drift: {'ok' if ok else 'NOT DETECTED'}")
        for p in proof["problems"]:
            print(f"  PROBLEM: {p}")
        print("instrument proof:", "OK" if proof["ok"] else "BROKEN")
        return 0 if proof["ok"] else 2
    if args.rescore:
        try:
            stored = json.loads(args.rescore.read_text(encoding="utf-8"))
            scored = cells_and_scores(stored["transcript"])
        except (OSError, ValueError, KeyError) as exc:
            print(f"rescore failed: {exc}", file=sys.stderr)
            return 2
        print(render(scored))
        return 1 if status_of(scored) == "drift" else (2 if scored["errored_turns"] else 0)
    if args.dry_run:
        print(plan_text())
        return 0
    if os.environ.get("ZOE_PERF") != "1":
        print("persona_drift: skipped - live runs require ZOE_PERF=1 (see --dry-run, --controls)")
        return 0

    log = lambda m: print(m, flush=True)  # noqa: E731
    service_dir = sb.resolve_service_dir(args.service_dir)
    revision = sb.service_revision(sb.code_checkout_dir())
    binding = sb.evidence_binding(revision, sb.harness_revision())
    if args.require_bound and not binding["bound"]:
        return _refuse(args.results, f"--require-bound: unbound evidence - {binding['reason']}", revision)
    if not proof["ok"]:
        return _refuse(args.results, "the instrument proof failed: " + "; ".join(proof["problems"][:3]), revision)
    sb.load_source_fallback_markers(sb.code_checkout_dir())
    lock_fd = sb._acquire_lock()  # noqa: F841 - held for the process lifetime
    try:
        cur = os.nice(0)
        if cur < 5:
            os.nice(5 - cur)
    except OSError:
        pass
    stack, refusal = sb.scratch_stack_for(service_dir, "persona_drift", ctx, log)     # quiet hours first; never the live DB
    if refusal:
        return _refuse(args.results, refusal, revision)
    live = sb.Live(stack.token, "", stack.dsn, True)
    live.admin = ""                     # a live admin session means nothing to the scratch instance
    baseline = None
    if args.compare_baseline:
        baseline, problem = bench_baseline.load(args.baseline, CELL_VERDICTS)
        if not problem and baseline.get("rules_sha256") != RULES_SHA256:
            problem = "the battery or the rules changed since the baseline (rules_sha256) - re-record it"
        if problem:
            return _refuse(args.results, f"--compare-baseline: {problem}", revision)
    if args.record_baseline:
        why = bench_baseline.record_problem(revision, status="ok", partial=False,
                                            verdicts={c: "PASS" for c in (f"{r}@{p}" for r in RULES for p in (*POSITIONS, 'mid'))},
                                            cells=[f"{r}@{p}" for r in RULES for p in (*POSITIONS, "mid")])
        if why:
            return _refuse(args.results, f"--record-baseline: {why}", revision)
    import user_model_ab as uma  # the shared gate: nightly window, quiet hours, deploy, readiness, teardown, RAM
    refusal = uma._gates(args, live, log)
    if refusal:
        return _refuse(args.results, refusal, revision)
    pend = None
    if args.pending.exists():
        import samantha_day_sim as ds
        pend = ds._pending(live, args.pending, log)
    if pend is not None and not pend["proven"]:
        return _refuse(args.results, f"a previous run's teardown is still unproven: {pend['problems']}", revision)

    user = sb.new_demo_user()
    started, t0 = dt.datetime.now(dt.timezone.utc), time.monotonic()
    flags = {k: _flag_state(service_dir, k) for k in ("ZOE_MANNER_BLOCK", "ZOE_MIRROR_GUARD")}
    log(f"persona_drift v{HARNESS_VERSION}: user={user} turns={len(BATTERY)} commit={(revision or {}).get('commit', '?')[:10]} "
        f"flags={json.dumps(flags)}")

    def _sigterm(signum, frame):
        raise SystemExit(128 + signum)
    signal.signal(signal.SIGTERM, _sigterm)

    transcript: list[dict[str, Any]] = []
    run_error = None
    td: dict[str, Any] = {"proven": False, "problems": ["not run"]}
    try:
        sb.write_json(args.pending, {"users": [user], "sessions": [], "started_at": started.isoformat()})
        transcript = run_battery(live, user, log)
    except BaseException as exc:  # noqa: BLE001 - teardown must still run
        run_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        log(f"run aborted: {run_error}")
    finally:
        sessions = list(live.sessions.get(user, []))
        sb.write_json(args.pending, {"users": [user], "sessions": sessions, "started_at": started.isoformat()})
        td = sb.teardown(live, [user], sessions)
        if td["proven"]:
            args.pending.unlink(missing_ok=True)
        log(f"teardown proven={td['proven']} {'' if td['proven'] else td['problems']}")

    scored = cells_and_scores(transcript)
    cmp = compare(scored, baseline) if args.compare_baseline else None
    status = status_of(scored, cmp)
    if run_error or not td["proven"]:
        status = "error"
    payload = {"harness_version": HARNESS_VERSION, "status": status, "run_error": run_error, "rules_sha256": RULES_SHA256,
               "started_at": started.isoformat(timespec="seconds"),
               "finished_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
               "duration_s": round(time.monotonic() - t0, 1), "revision": revision, "evidence": binding, "flags": flags,
               "scratch": stack.public(), "cells": scored["cells"], "scores": scored["scores"], "drift": scored["drift"],
               "errored_turns": scored["errored_turns"], "compare": cmp,
               "transcript": [{k: t.get(k) for k in ("turn", "kind", "probe", "user", "reply", "error", "ms")} for t in transcript],
               "teardown": td}
    sb.write_json(args.results, payload)
    sb.record_trend(args.trend, {"ts": payload["finished_at"], "status": status, "commit": (revision or {}).get("commit"),
                                 "scores": scored["scores"], "drift": scored["drift"], "cells": scored["cells"],
                                 "teardown_proven": td["proven"], "duration_s": payload["duration_s"]},
                    binding, sb.prompt_size_summary(live))
    print(render(scored))
    log(f"status={status} drift={len(scored['drift'])} results={args.results}")
    if args.record_baseline:
        why = bench_baseline.record_problem(revision, status="ok" if status == "ok" else status, partial=False,
                                            verdicts=scored["cells"], cells=scored["cells"])
        if not why and not binding["bound"]:
            why = f"the evidence is unbound ({binding['reason']})"
        if not why and any(v == "ERROR" for v in scored["cells"].values()):
            why = "a cell ERRORED: the instrument, not a result"
        if why:
            log(f"baseline NOT recorded: {why}")
        else:
            bench_baseline.write(args.baseline, bench_baseline.make(
                "persona_drift", HARNESS_VERSION, scored["cells"], revision, scores=scored["scores"], rules_sha256=RULES_SHA256))
            log(f"baseline recorded: {args.baseline}")
    if cmp is not None:
        log(f"vs baseline {str(cmp.get('baseline_commit') or '?')[:10]}: regressions={cmp['regressions']} "
            f"score_falls={cmp['score_falls']} improvements={cmp['improvements']}")
    return {"ok": 0, "drift": 1, "regression": 1}.get(status, 2)


def render(scored: dict[str, Any]) -> str:
    sc = scored["scores"]
    lines = ["persona drift: " + "  ".join(f"{p}={sc[p]:.2f}" if sc.get(p) is not None else f"{p}=n/a" for p in (*POSITIONS, "mid"))]
    for name in RULE_NAMES:
        row = [scored["cells"].get(f"{name}@{p}", "-") for p in (*POSITIONS, "mid")]
        if any(v != "SKIP" for v in row):
            lines.append(f"  {name:<20} " + "  ".join(f"{v:<5}" for v in row))
    lines += [f"  DRIFT: {d}" for d in scored["drift"]] or ["  no drift"]
    if scored["errored_turns"]:
        lines.append(f"  ERRORED turns: {scored['errored_turns'][:12]}")
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
