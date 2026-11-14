"""Medical safety floor: a deterministic, fixed-text answer for the few medical turns where a wrong or vague reply can hurt (QA round 6b, 2026-11-09).

THE MISS. A toddler eating dishwasher powder was answered "call your vet or an animal poison centre" (memory had the child typed as a pet); "what number do I ring
if <person> collapses" asked mid-thread got the chicken resting time; chest tightness got "call emergency services" with no number; a three-day headache with
blurred vision got a question; a blood sugar of 9.8 got a deflection. Each turn went to the 4B brain, which improvised from whatever memory held.

WHAT IT DOES. Like ``distress_handoff`` (it runs right after it, before the brain, recall or persona), a matched turn is answered with ONE fixed reply built from
``medical_safety_data/en.json`` and the household's numbers from ``distress_data/crisis_lines.json`` (country from ``household_locale.country()``: config, never
hard-coded per person; no country = "your local emergency number"). The classes, highest priority first:
  1. ingestion / poisoning (``ate``/``swallowed`` + a harmful substance). HUMAN FIRST: it is an animal ONLY when the words say so (a pet word and no person word).
     Unclear = the Poisons line and the emergency number first, then "if it is an animal, ring your vet". Memory is never consulted, so a wrongly typed pet cannot
     redirect a child's poisoning.
  2. red-flag symptoms that need the ambulance now (chest tightness, stroke signs, breathing, heavy bleeding, worst headache, a fall and cannot get up): the
     number, plainly, first, then how to get someone in the house (the intercom phrase when it will work, else "call out"; ``build_reply``).
  3. an emergency-number question ("what number do I ring if <person> collapses"). It pre-empts any thread: the number is the whole reply, plus one line saying
     the earlier question can be asked again.
  4. red-flag symptoms that need a doctor today (blurred vision, blood where it should not be): see a doctor today, the number if it gets worse.
  4b. swallowed OBJECTS (QA 7): a button battery, magnet or sharp object = the number now; a coin, lego or small part = the number if there is choking, trouble
     breathing, drooling or pain, otherwise the Poisons line and what to watch for. Choking in the same turn still wins as a red flag.
  5. a blood sugar reading: what the number generally means for someone with diabetes, and when to act. Never a diagnosis; always "check your plan or your GP".
PARAPHRASES (QA 7). Besides exact phrases, a turn is also read in a CANONICAL form: each word is mapped through the small ``synonyms`` table (tightness / squeezing /
heavy -> tight / pressure; blurred / hazy / foggy -> blurry; ...) and the ``pairs`` rules fire on a BODY word and a SYMPTOM word in the same clause within a few words,
in either order ("tight feeling in my chest", "my chest feels tight", "pressure in my chest", "eyes are blurry"), with per-rule skip words (chest of drawers, a tight
shirt, a blurry photo). Still deterministic, still stdlib, still no model.
A non-emergency question in a thread ("how long should it rest", "what number is the vet") matches nothing and stays with the brain.

FLAG ``ZOE_MEDICAL_SAFETY`` off | shadow | enforce (DEFAULT enforce; a typo enforces). shadow logs the class only. Logs carry the class id, never the words. The turn is
NOT marked off the record (unlike a distress turn): a symptom the member mentions is still recallable. A call-now turn is filed as ONE
dated health event, never a routine or standing fact (``emergency_event``). Doc: docs/knowledge/medical-safety.md. Stdlib only. NEVER raises.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

from typed_env import env_str

logger = logging.getLogger(__name__)

ENV = "ZOE_MEDICAL_SAFETY"
DATA_DIR = Path(__file__).with_name("medical_safety_data")
MAX_CHARS = 2000
_FILLER = re.compile(r"\b(?:and|but|so|oh|ok|okay|sorry|um|uh|hold on|wait|actually|hang on|quick question|one sec|also|by the way|hey|zoe|please|could|can|would|you|tell|me|just|quickly|quick)\b|[^\w\s]", re.I)
_SELF_INGEST = re.compile(r"(?<!\w)(?:i|ive|i have|i just|i accidentally|i think i|im pretty sure i)\s+(?:accidentally |just |maybe )?(?:ate|eaten|swallowed|drank|drunk|chewed|licked|ingested)(?!\w)")
_GLUCOSE_NUM = r"(?<![\d.])(\d{1,3}(?:\.\d{1,2})?)(?!\d)(?:\s*(mmol|mg))?"   # a WHOLE token: "1000" never reads as "100"


@dataclass(frozen=True)
class Verdict:
    cls: str = ""      # ingest_human | ingest_animal | red_call | red_soon | number | glucose | ""
    key: str = ""      # the reply key / red-flag id
    value: str = ""    # glucose reading as typed
    unit: str = ""     # mmol | mg
    lead: str = ""     # the words BEFORE the safety clause (thread detection)


NONE = Verdict()


def mode() -> str:
    raw = env_str(ENV, "enforce").strip().lower()
    if raw in {"0", "false", "no", "none", "off"}:
        return "off"
    return "shadow" if raw == "shadow" else "enforce"


def _fold(text: str) -> str:
    import distress_handoff

    return distress_handoff._strip(unicodedata.normalize("NFKC", text or "").casefold())[:MAX_CHARS]


@lru_cache(maxsize=1)
def _data() -> dict:
    return json.loads((DATA_DIR / "en.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _syn() -> tuple:
    d = _data().get("synonyms") or {}
    return d, re.compile(r"[a-z]+")


def _canon(s: str) -> str:
    """Words folded to their canonical symptom word via the synonym table (same length rules do not matter: it is matched on its own string)."""
    syn, word = _syn()
    return word.sub(lambda m: syn.get(m.group(0), m.group(0)), s)


def _pair_rx(p: dict, block: str) -> "re.Pattern":
    gap, body, sym = int(p.get("window", 4)), p["body"], p["sym"]
    pre = "".join(rf"(?<!{re.escape(w)} )" for w in p.get("body_not_after", []))
    post = "".join(rf"(?!\s+(?:{n}))" for n in [("|".join(p.get("body_not_before", [])))] if n)
    b = rf"{pre}(?:{body}){post}"
    # the words BETWEEN may not turn to a contrast or another body part ("my chest is fine but my knee hurts"): the symptom must belong to THIS body word
    mid = rf"(?:\s+(?!(?:{block})(?!\w))\w+){{0,{gap}}}"
    return re.compile(rf"(?<!\w)(?:{b})(?!\w){mid}\s+(?:{sym})(?!\w)|(?<!\w)(?:{sym}){mid}\s+{b}(?!\w)", re.I)


@lru_cache(maxsize=1)
def _rx() -> dict:
    d = _data()
    w = lambda body: re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.I)  # noqa: E731
    ing, nq, gl = d["ingest"], d["number_question"], d["glucose"]
    return {
        "verb": w(ing["verb"]), "sub": w(ing["substance"]), "idiom": re.compile(ing["idioms"], re.I), "took": w(ing["took_verb"]), "took_risky": w(ing["took_risky"]),
        "human": w(ing["human"]), "animal": w(ing["animal"]), "obj_urgent": w(ing["obj_urgent"]), "obj_minor": w(ing["obj_minor"]), "obj_signs": w(ing["obj_signs"]),
        "pairs": [(p["id"], p["tier"], _pair_rx(p, d["pair_gap_block"]), re.compile(rf"(?<!\w)(?:{p['skip']})(?!\w)", re.I) if p.get("skip") else None) for p in d.get("pairs", [])],
        "neg_adj": re.compile(rf"(?<!\w)(?:{d['negation']})(?:\s+\w+){{0,2}}\s*$", re.I), "unsure": re.compile(r"(?<!\w)(?:not sure|unsure|not certain|dont know|no idea)(?!\w)", re.I),
        "term_any": w(gl["term"] + "|" + gl["plain_term"]),
        "ask": w(nq["ask"]), "event": w(nq["event"]), "poison_event": w(nq["poison_event"]),
        "flags": [(f["id"], f["tier"], w(f["re"])) for f in d["red_flags"]],
        "gl": re.compile(rf"(?<!\w)(?:{gl['term']})\W+(?:[a-z]+\W+){{0,4}}?{_GLUCOSE_NUM}", re.I),
        "gl_after": re.compile(gl["not_glucose_after"], re.I), "gl_ctx": re.compile(w(gl["not_glucose_context"])),
        "gl_plain": re.compile(rf"(?<!\w)(?:{gl['plain_term']})\W+(?:{gl['plain_link']})\W+(?:[a-z]+\W+){{0,3}}?{_GLUCOSE_NUM}", re.I),
    }


def _negated(s: str, start: int, rx: dict) -> bool:
    """Is the symptom at ``start`` denied in its own clause ("I have no chest pain")? Looks only at the 30 characters before it."""
    cuts = [(s.rfind(c, 0, start), len(c)) for c in (".", ",", ";", "!", "?", " but ", " however ", " though ")]
    clause_start = max([i + n for i, n in cuts if i >= 0] + [0])
    pre = s[max(clause_start, start - 40):start]
    if rx["unsure"].search(pre):
        return False   # "not sure why my chest feels tight" doubts the cause, it does not deny the symptom
    return bool(rx["neg_adj"].search(pre))   # the negation must sit right before the symptom: "no chest pain", "dont have any chest pain"


def _flag(s: str, rx: dict, tier: str) -> Optional[Verdict]:
    for fid, t, pat in rx["flags"]:
        if t != tier:
            continue
        for m in pat.finditer(s):
            if not _negated(s, m.start(), rx):
                return Verdict("red_call" if tier == "call" else "red_soon", fid)
    return None


_DENIED_INSIDE = re.compile(r"(?<!\w)(?:no|not|isnt|arent|wasnt|werent|never|dont|doesnt|didnt|hasnt|havent|hadnt|wouldnt|without|nothing|nor)(?!\w)")   # "my eyes are not blurry": the denial sits between body and symptom


def _pair(c: str, rx: dict, tier: str) -> Optional[Verdict]:
    """A body word + symptom word within a few words, in the canonical form (``_canon``), not denied, not skipped ("my shirt is tight across the chest")."""
    for fid, t, pat, skip in rx["pairs"]:
        if t != tier:
            continue
        for m in pat.finditer(c):
            if skip and skip.search(c[max(0, m.start() - 30): m.end() + 30]):
                continue
            if not _negated(c, m.start(), rx) and not _DENIED_INSIDE.search(m.group(0)):
                return Verdict("red_call" if tier == "call" else "red_soon", fid)
    return None


#: wording that makes an ingestion a concern even when the medication itself is the member's usual one
_ACCIDENT = re.compile(r"(?<!\w)(?:accident\w*|by mistake|too many|too much|double(?: the| my| his| her)? dose|twice|overdos\w*|extra)(?!\w)", re.I)


#: a sentence that ASKS about, or hypothesises, signs ("what signs of pain should i watch for") does not report them
_HYPOTHETICAL = re.compile(r"(?<!\w)(?:what|which|how|should|signs?|symptoms?|watch|look(?:ing)? for|in case|whether|might|could|would|if|unless|until|any)(?!\w)")
_CONTINUES = re.compile(r"\s*(?:he|she|they|hes|shes|theyre|its|now|and|then|but)(?!\w)")


def _stated_sign(s: str, rx: dict) -> bool:
    """A sign (drooling, pain, cant swallow ...) ASSERTED about the child who swallowed the object: in the sentence that names the object, or the next one when it
    carries on about the same child ("... swallowed a coin. now he is drooling"). Questions, watch-for lists and denials do not count."""
    parts = re.findall(r"[^.!?;\n]+[.!?;\n]?", s)
    humans = lambda t: {m.group(0) for m in rx["human"].finditer(t)}   # noqa: E731
    for i, seg in enumerate(parts):
        if not rx["obj_minor"].search(seg):
            # a later sentence counts only when it carries on about the SAME child: a pronoun / "now", or the same person word named again
            if not (i and rx["obj_minor"].search(parts[i - 1])):
                continue
            same = humans(seg) & humans(parts[i - 1])
            speaker = bool(_SELF_INGEST.search(parts[i - 1]))   # the member swallowed it herself: "i swallowed a coin. i cant swallow" is the same person
            first = bool(re.search(r"(?<!\w)(?:i|im|ive)(?!\w)", seg))
            if (first and not speaker) or not (same or _CONTINUES.match(seg) or (speaker and first)):
                continue   # the member's own ailment after a child's ingestion ("my back has pain") or another person ("my mum has pain")
        for m in rx["obj_signs"].finditer(seg):
            clause = seg[max(seg.rfind(",", 0, m.start()), seg.rfind(";", 0, m.start())) + 1:]   # the symptom's OWN clause: "is drooling, what should i do" still asserts it
            clause = clause.split(",", 1)[0]
            if not _negated(seg, m.start(), rx) and not _HYPOTHETICAL.search(clause):
                return True
    return False


_DRINKING = re.compile(r"(?:drank|drunk|drinking|sipped|sipping)$")


def _target(tail: str, rx: dict) -> str:
    """What was swallowed: a battery / magnet / sharp object (urgent), a harmful substance, or a small harmless-looking object. '' = nothing the floor answers."""
    return "object_urgent" if rx["obj_urgent"].search(tail) else "poison_human" if rx["sub"].search(tail) else "object_minor" if rx["obj_minor"].search(tail) else ""


def _ingestion(s: str, rx: dict) -> Optional[Verdict]:
    """Verb + harmful substance within ~70 chars, same sentence. Human unless the words clearly say animal."""
    for sentence in re.split(r"[.!?;\n]", s):
        for v in rx["verb"].finditer(sentence):
            tail = sentence[v.start(): v.end() + 70]
            key = _target(tail, rx)
            if not key:
                continue
            if key.startswith("object_") and (_DRINKING.match(v.group(0)) or re.match(r"\s+from\b", sentence[v.end():])):
                continue         # an OBJECT is swallowed or eaten, never drunk: "drank from a bottle with a screw top" describes the bottle
            lo = max(0, v.start() - 8)
            routine = rx["idiom"].search(sentence[lo:])
            # the routine-medication idiom must be THIS ingestion ("swallowed his morning tablets"), not a later one in the same sentence
            if routine and routine.start() < (v.start() - lo) + (v.end() - v.start()) and not _ACCIDENT.search(sentence):
                continue         # (an accident / double dose is never "routine": "accidentally took my usual tablets twice")
            human = bool(rx["human"].search(sentence) or _SELF_INGEST.search(sentence))
            animal = bool(rx["animal"].search(sentence[max(0, v.start() - 45):v.start()]))   # the animal must be the SUBJECT (before the verb), not a bystander
            if animal and not human:
                return Verdict("ingest_animal", "poison_animal", lead=s[:v.start()])
            return Verdict("ingest_human", key, lead=s[:v.start()])
        # "took" is ordinary ("took my tablets"): only a concerning amount or an accident makes it an ingestion ("accidentally took 20 paracetamol")
        for v in rx["took"].finditer(sentence):
            tail = sentence[v.start(): v.end() + 70]
            key = "object_urgent" if rx["obj_urgent"].search(tail) else "poison_human" if rx["sub"].search(tail) else ""
            if key and rx["took_risky"].search(sentence) and (
                    not rx["idiom"].search(sentence[max(0, v.start() - 8):]) or _ACCIDENT.search(sentence)):
                human = bool(rx["human"].search(sentence) or _SELF_INGEST.search(sentence) or re.search(r"(?<!\w)(?:i|ive|i have)(?!\w)", sentence[:v.start()]))
                animal = bool(rx["animal"].search(sentence[max(0, v.start() - 45):v.start()]))
                if animal and not human:
                    return Verdict("ingest_animal", "poison_animal", lead=s[:v.start()])
                return Verdict("ingest_human", key, lead=s[:v.start()])
    return None


_INGEST_RANK = {"object_urgent": 0, "poison_human": 1, "object_minor": 2, "poison_animal": 3}


def _ingestion_all(s: str, rx: dict) -> Optional[Verdict]:
    """The most urgent ingestion ACROSS the turn ("swallowed a coin. she also swallowed a battery" is the battery), human before animal."""
    found = [v for v in (_ingestion(seg, rx) for seg in re.findall(r"[^.!?;\n]+[.!?;\n]?", s)) if v]
    return min(found, key=lambda v: _INGEST_RANK.get(v.key, 9)) if found else None


def _mmol(raw: str, unit: str) -> float:
    n = float(raw)
    return n if unit == "mmol" else n / 18.0


def _band(raw: str, unit: str) -> str:
    mmol = _mmol(raw, unit)
    return "glucose_low" if mmol < 4.0 else "glucose_ok" if mmol <= 7.0 else "glucose_up" if mmol <= 10.0 else "glucose_high"


_CHANGE_BY = re.compile(r"(?:rose|risen|dropped|fell|fallen|went up|went down|up|down|increased|decreased|jumped|climbed|changed)\s+by\s+$", re.I)
_AFTER_CLOCK = re.compile(rf"\W*(?:[a-z]+\W+){{0,4}}?{_GLUCOSE_NUM}", re.I)
_SEVERITY = {"glucose_low": 3, "glucose_high": 2, "glucose_up": 1, "glucose_ok": 0}
_NOW = re.compile(r"now|currently|latest")
_SECOND = re.compile(rf"(?<!\w)(?P<lead>right now|is now|now|currently|latest|then|dropped to|fell to|down to|up to|rose to|went to|reads|reading|is|are)\s+{_GLUCOSE_NUM}", re.I)


def _reading(raw: str, unit: str, after: str, rx: dict, default_unit: str = "") -> Optional[tuple]:
    """(raw, unit) when ``raw`` followed by ``after`` is a plausible reading, else None (a recipe quantity, a time or a price is not one)."""
    if rx["gl_after"].search(after[:24]):
        return None
    n = float(raw)
    unit = (unit or "").lower() or default_unit or ("mmol" if 1.0 <= n <= 30 else ("mg" if 30 < n <= 700 else ""))   # no unit: 40 is 40 mg/dL (severely low), never 40 mmol/L
    return (raw, unit) if (unit == "mmol" and 1.0 <= n <= 40) or (unit == "mg" and 20 <= n <= 700) else None


def _glucose(s: str, rx: dict) -> Optional[Verdict]:
    """The reading. Several in one sentence ("5.5 earlier but now 2.8"): the one marked now/currently/latest, else the most urgent band - never the first by accident."""
    for key in ("gl", "gl_plain"):
        m = rx[key].search(s)
        if not m or (key == "gl_plain" and rx["gl_ctx"].search(s)):
            continue
        if _CHANGE_BY.search(s[:m.start(1)]):
            continue             # "my blood sugar rose by 5 to 18": 5 is how much it moved, not the reading - leave it to the brain
        num = m
        if re.match(r":[0-5]\d(?!\d)", s[m.end(1):]):
            # "my blood sugar at 5:30 was 2.8": the captured number is the hour of a clock time - the reading is the next number, if any
            num = _AFTER_CLOCK.match(s, m.end(1) + 3)
            if not num:
                continue
        first = _reading(num.group(1), num.group(2) or "", s[num.end(1):], rx)
        if not first:
            continue
        cands, current = [first], []
        tail = re.split(r"[.!?;](?:\s|$)|\n", s[num.end():], 1)[0]   # a decimal point is not a sentence end
        for sm in _SECOND.finditer(tail):
            seg = tail[:sm.start()]
            if re.search(r"(?<!\w)(?:my|his|her|their|our|your|the)(?!\w)", seg) and not rx["term_any"].search(seg):
                break   # a newly named thing ("my temperature is now 39") ends the glucose association
            got = _reading(sm.group(2), sm.group(3) or "", tail[sm.end(2):], rx, default_unit=first[1])
            if got:
                cands.append(got)
                if _NOW.search(sm.group("lead")):
                    current.append(got)
        raw, unit = current[-1] if current else max(cands, key=lambda c: _SEVERITY[_band(*c)])
        return Verdict("glucose", "glucose", raw, unit, lead=s[:m.start()])
    return None


# A RECALL QUESTION about the past ("did i have chest pain last month?", "have i ever had chest pain?", "has my wife had chest pain?")
# asks what memory holds; it reports nothing happening now (QA round 10a: three such questions got "Ring 000 now"). An ALLOW-LIST,
# never a classifier of what is left over: a clause loses the floor only when the WHOLE clause is exactly
#   [filler] (did <who> [ever] have | have|has|had <who> [ever] had) <symptom> [<one past-time phrase from _RECALL_TIME>]
# where <symptom> is ONE exact red-flag phrase and nothing else (leading intensity words aside: _SYM_FILLER). The turn is split at punctuation only,
# so a run-on is one clause and any trailing word ("its back", "it never stopped", another symptom, "and now", a typo, another
# language) is a leftover that keeps the floor. Every clause must be one (filler-only clauses aside), and no present marker may
# appear anywhere. If in doubt, the floor fires.
_RQ_FILLER = r"(?:hey|hi|ok|okay|so|um+|uh+|oh|well|zoe|quick question)"
_RQ_FILLER_ONLY_RE = re.compile(rf"^(?:{_RQ_FILLER}\s*)+$")
_MONTHS = "january|february|march|april|may|june|july|august|september|october|november|december"
#: THE CLOSED past-time list. "recently" is deliberately NOT in it: it can be present-relevant ("did i have chest pain recently"
#: from someone with it now), so it keeps the floor.
_RECALL_TIME = (
    r"(?:last\s+(?:week|month|year|night|summer|winter|spring|autumn|christmas|easter)"
    r"|yesterday"
    rf"|in\s+(?:{_MONTHS})"
    r"|in\s+(?:19|20)\d\d"
    r"|(?:\d{1,3}|a|an|one|two|three|four|five|six|seven|eight|nine|ten|a\s+few|several)\s+(?:days?|weeks?|months?|years?)\s+ago"
    r"|years\s+ago"
    r"|before"
    r"|in\s+the\s+past"
    r"|at\s+(?:christmas|easter)"
    r"|when\s+(?:i|he|she|they|we|you)\s+(?:was|were)\s+(?:younger|little|small|a\s+kid|a\s+child|a\s+baby))"
)
_RQ_WHO = r"(?:i|you|he|she|we|they|(?:my|our|your)\s+[a-z]+|[a-z]+)"
_RECALL_SHAPE_RE = re.compile(
    rf"^(?:{_RQ_FILLER}\s+)*(?:did\s+{_RQ_WHO}\s+(?:ever\s+)?have|(?:have|has|had)\s+{_RQ_WHO}\s+(?:ever\s+)?had)"
    rf"\s+(?P<sym>[a-z ]+?)(?:\s+{_RECALL_TIME})?$")
#: the only words a symptom may carry BEFORE the red flag itself
_SYM_FILLER = frozenset({"a", "an", "any", "some", "the", "crushing", "bad", "severe", "sharp"})
_PRESENT_MARK_RE = re.compile(r"(?<!\w)(?:just|now|still|again|today|tonight|this\s+(?:morning|afternoon|evening|week)|since|"
                              r"all\s+day|at\s+the\s+moment|currently|right\s+now|anymore|recently|lately)(?!\w)")
_CLAUSE_RE = re.compile(r"[.!?;,\n]+")


def _only_a_symptom(sym: str, rx: dict) -> bool:
    """Is ``sym`` exactly ONE red-flag PHRASE (call or see-today tier), with only ``_SYM_FILLER`` words beside it? The paraphrase
    ``pairs`` are NOT accepted here: a body word and a symptom word a few words apart also fit "chest pain hurts", so a symptom
    the exact phrases do not cover keeps the floor. Pure."""
    words = sym.split()
    while words and words[0] in _SYM_FILLER:      # LEADING intensity words only ("crushing chest pain"): a trailing one is a leftover
        words.pop(0)
    core = " ".join(words)
    return bool(core) and any(pat.fullmatch(core) for _fid, _t, pat in rx["flags"])


def _recall_question(s: str, rx: Optional[dict] = None) -> bool:
    """Is the (folded) turn ONLY a recall question about the past (above)? Pure; False whenever in doubt."""
    rx = rx or _rx()
    if _PRESENT_MARK_RE.search(s):
        return False
    clauses = [c.strip() for c in _CLAUSE_RE.split(s) if c and c.strip()]
    clauses = [c for c in clauses if not _RQ_FILLER_ONLY_RE.match(c)]       # "hey zoe, did i ..."
    if not clauses:
        return False
    for c in clauses:
        m = _RECALL_SHAPE_RE.match(c)
        if not m or not _only_a_symptom(m.group("sym"), rx):
            return False
    return True


def detect(text: str) -> Verdict:
    """The class of one turn (``NONE`` = leave it to the brain). Pure; never raises."""
    return _detect(text, recall_gate=True)


def _detect(text: str, *, recall_gate: bool) -> Verdict:
    try:
        s = _fold(text)
        if not s:
            return NONE
        rx = _rx()
        c = _canon(s)
        ing = _ingestion_all(s, rx)
        call = _flag(s, rx, "call") or _pair(c, rx, "call")
        recall_q = recall_gate and _recall_question(s, rx)
        if recall_q:
            call = None      # a symptom ASKED ABOUT in the past is answered from memory; an ingestion is never withheld
        if ing and ing.key == "object_minor" and not call:
            # a coin AND a stated sign (drooling, pain, cant swallow): the number now, not "ring if there is..."
            if _stated_sign(s, rx):
                return Verdict("red_call", "object_signs")
        if ing and not (call and ing.key == "object_minor"):   # a coin AND choking: the ambulance answer is the better one
            return ing
        if call:
            return call
        ask, ev = rx["ask"].search(s), rx["event"].search(s)
        if ask and ev and abs(ev.start() - ask.start()) <= 90:
            first = min(ask.start(), ev.start())
            key = "emergency_number_poison" if rx["poison_event"].search(s[first:]) else "emergency_number"
            return Verdict("number", key, lead=s[:first])
        soon = None if recall_q else (_flag(s, rx, "soon") or _pair(c, rx, "soon"))
        if soon:
            return soon
        return _glucose(s, rx) or NONE
    except Exception as exc:  # noqa: BLE001 - a floor must not break a turn
        logger.warning("medical_safety detect failed (%s)", type(exc).__name__)
        return NONE


def _lines() -> tuple[str, str]:
    """(emergency number, poisons line number) for the household country, or the fallbacks."""
    r = _data()["replies"]
    emergency, poison = r["emergency_fallback"], ""
    try:
        import distress_handoff
        from household_locale import country

        ent = distress_handoff._entry(country())
        emergency = ent.get("emergency") or emergency
        poison = (ent.get("poison") or {}).get("number", "")
    except Exception:  # noqa: BLE001
        pass
    return emergency, poison


def _glucose_reply(v: Verdict, r: dict, emergency: str) -> str:
    mg = v.unit == "mg"
    unit = r["glucose_unit_mg" if mg else "glucose_unit_mmol"]
    return r[_band(v.value, v.unit)].format(value=v.value, unit=unit, emergency=emergency, low_line=r["glucose_low_mg" if mg else "glucose_low_mmol"],
                                            usual=r["glucose_usual_mg" if mg else "glucose_usual_mmol"])


async def _intercom_reachable(user_id: str, voice: bool) -> bool:
    """Can "tell everyone I need help" actually reach ANOTHER panel on the member's next turn? Only through the existing intercom
    (``intercom.py``): it is on, this is the voice lane it serves, the member is identified, it is not quiet hours (which hold the
    intercom), and at least TWO panels hold a live voice daemon - the intercom never targets the panel you are at, and this floor
    does not know which panel that is, so two is the least that leaves one to hear it. In a one-panel house the offer would be a
    false promise in an emergency. Anything unsure = False, and the reply offers nothing Zoe cannot do. Never raises."""
    try:
        import intercom
        from user_filters import is_unidentified

        if not (voice and intercom.mode() == "on" and not is_unidentified(user_id) and not intercom._quiet_now()):
            return False
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            panels = await intercom._speaker_panels(db)
        return len({str(p.get("panel_id") or "") for p in panels} - {""}) >= 2
    except Exception:  # noqa: BLE001
        return False


# -- how an emergency turn is FILED (QA round 9) ----------------------------------------------------------------------------------
# All four fall turns became ordinary rows ("you fell over in the kitchen and are unable to get up" listed under Routines, because
# "get up" reads as a routine). The turn stays recallable (Zoe should ask after them), but as ONE dated health EVENT, never a habit,
# preference or standing fact. ``MemoryService.ingest`` (every writer's chokepoint) files a mined fact through ``emergency_event``.
_EVENT_WHAT = {"fall": "a fall", "heart": "chest pain", "stroke": "signs of a stroke", "breathing": "trouble breathing",
               "bleeding": "bleeding that would not stop", "worst_headache": "a sudden severe headache"}


# THE CONSERVATIVE RULE (QA round 9 review; a better design is a follow-up). A mined fact becomes the event only when it is
# unambiguous; anything else is stored exactly as mined (a missed conversion is safe, a false health record is not):
#   * the source turn names EXACTLY ONE affected person: the member alone (first person, nobody else mentioned), or exactly one
#     other person BY NAME (no first person, at most one relation word, no plural "sons" / "kids", the name not listed with another);
#   * the fact's subject is that same person: "User" for the member, or the SAME NAME ("User's son Max" / "Max"); a relation alone
#     ("User's son has chest pain") is never enough;
#   * the fact's own text is the detector's emergency of the same class (``detect(fact)``): a condition ("has heart disease"), a
#     body-part or topic overlap, or a paraphrase the detector does not read stays as mined.
_REL_WORDS = (r"son|daughter|grandson|granddaughter|grandchild|child|kid|baby|wife|husband|partner|girlfriend|boyfriend|sister|"
              r"brother|mum|mom|mother|dad|father|nan|nana|grandma|grandmother|grandpa|grandfather|aunt|auntie|uncle|cousin|niece|"
              r"nephew|friend|neighbou?r|flatmate|housemate")
_REL_RE = re.compile(rf"\b(?:{_REL_WORDS})\b", re.IGNORECASE)
_PLURAL_PEOPLE_RE = re.compile(rf"\b(?:(?:{_REL_WORDS})s|children|kids|parents|twins|grandkids|boys|girls|everyone|both)\b",
                               re.IGNORECASE)
_WHO_REL_RE = re.compile(rf"^\s*(?:the\s+)?user['’]s\s+(?:{_REL_WORDS}),?\s+(?:named\s+|called\s+)?(?-i:([A-Z][a-z]+))\b",
                         re.IGNORECASE)
_REL_OF_RE = re.compile(r"^\s*(?:the\s+)?user['’]s\s+([a-z]+)", re.IGNORECASE)
_WHO_USER_RE = re.compile(r"^\s*(?:the\s+)?user\b(?!['’])", re.IGNORECASE)
_WHO_NAME_RE = re.compile(r"^\s*([A-Z][a-z]+)\b(?!['’])")
#: a first-person turn: the member is the one it happened to
_FIRST_PERSON_RE = re.compile(r"\b(?:i|im|i['’]m|ive|i['’]ve|me|myself)\b", re.IGNORECASE)
#: a PAST time: a year, "last year", "in March", "years ago", "when I was a kid", past perfect, "used to" - a history, not now
_PAST_RE = re.compile(
    r"\b(?:1[89]\d\d|20\d\d)\b|\b(?:last|previous)\s+(?:year|month|week|summer|winter|spring|autumn|fall|christmas|time)\b"
    r"|\bago\b|\b(?:in|back\s+in|during|since)\s+(?:january|february|march|april|may|june|july|august|september|october|november|"
    r"december)\b|\bwhen\s+(?:i|he|she|they|we|you)\s+(?:was|were)\b|\bas\s+a\s+(?:kid|child|boy|girl|teenager|baby)\b"
    r"|\b(?:had|has|have)\s+had\b|\bused\s+to\b|\bonce\b|\byesterday\b|\blast\s+night\b|\bhistory\s+of\b|\bprevious(?:ly)?\b",
    re.IGNORECASE)
#: a CONDITIONAL / hypothetical / instruction: "told to call an ambulance if I have chest pain", "if I ever fall", "what would you
#: do if". FILING only (the reply still gives the number). Plain "when" is left out on purpose: it is temporal in real present-tense
#: emergencies ("I can't breathe when I lie down", "when I stood up I fell and can't get up"); only "whenever" counts. "could not"
#: / "would not" are left out too ("I fell and could not get up", "the bleeding would not stop" are actual).
_HYPOTHETICAL_RE = re.compile(
    r"\b(?:if|in\s+case|should|could(?!\s+not)|would(?!\s+not)|might|told\s+(?:me\s+|us\s+|him\s+|her\s+|them\s+)?to|was\s+told|"
    r"were\s+told|advised|supposed\s+to|in\s+the\s+future|what\s+if|unless|next\s+time|whenever)\b", re.IGNORECASE)


def _not_now(text: str) -> bool:
    """Is this NOT an emergency happening now - a past time or a condition / hypothetical / instruction? (filing only)"""
    return bool(_PAST_RE.search(text or "") or _HYPOTHETICAL_RE.search(text or ""))


_NOT_A_NAME = frozenset({"the", "user", "a", "an", "my", "his", "her", "their", "they", "he", "she", "it", "we", "you", "i", "this",
                         "that", "there", "someone", "somebody", "anyone", "anybody", "everyone", "everybody", "nobody", "people",
                         "one", "both", "all", "some", "each", "family", "household"})


def _subject(fact: str) -> Optional[str]:
    """"User" or the NAME the fact is about; None for a relation alone ("User's son ...") or anything unclear."""
    m = _WHO_REL_RE.match(fact or "")
    if m:
        return m.group(1)
    if _WHO_USER_RE.match(fact or ""):
        return "User"
    m = _WHO_NAME_RE.match(fact or "")
    return m.group(1) if m and m.group(1).lower() not in _NOT_A_NAME else None


#: a third-person reference ("because HE has chest pain"): someone other than the subject may be the one it happened to
_THIRD_PERSON_RE = re.compile(r"\b(?:he|she|they|him|her|them)\b", re.IGNORECASE)
_CALENDAR_WORDS = frozenset({"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                             "november", "december", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"})


def _names_another(fact: str, said: str, subject: str) -> bool:
    """Do the fact or the turn name ANY person besides ``subject`` (QA round 9 review: "I called an ambulance because Quinlan has
    crushing chest pain" was filed as the member's own emergency)? Another capitalised name in the fact, a he / she / they / him /
    her / them anywhere, or a kin word beyond the subject's own (none for the member). Pure."""
    said_mid = re.sub(r"(?:^|[.!?;]\s*)[A-Za-z]+", " ", said or "")      # a typed turn's sentence-initial word is no name
    names = {w for w in re.findall(r"\b[A-Z][a-z]+\b", f"{fact or ''} {said_mid}")
             if w.lower() not in _NOT_A_NAME and w.lower() not in _CALENDAR_WORDS and w.lower() != subject.lower()}
    if names or _THIRD_PERSON_RE.search(fact or "") or _THIRD_PERSON_RE.search(said or ""):
        return True
    return len(_REL_RE.findall(fact or "")) > (0 if subject == "User" else 1)


_CLAUSE_SPLIT_RE = re.compile(r"[;.!?]|\b(?:because|cause|cos|coz|since|while|after|so)\b", re.IGNORECASE)


def _clause_is_about(said: str, subject: str) -> bool:
    """The clause of the turn the detector reads as the emergency must itself name the subject: the member in the first person,
    or the subject's name ("i called an ambulance because quinlan has crushing chest pain": the emergency clause is quinlan's,
    a lowercase name the fact-side checks cannot see). No such clause on its own: the whole turn. Pure."""
    clauses = [c for c in _CLAUSE_SPLIT_RE.split(said) if c and detect(c).cls == "red_call"] or [said]
    c = clauses[0]
    if subject == "User":
        return bool(_FIRST_PERSON_RE.search(c))
    return bool(re.search(rf"\b{re.escape(subject)}\b", c, re.IGNORECASE))


def _only_person(said: str, subject: str) -> bool:
    """Does the turn name exactly ONE affected person, and is it ``subject``? (the rule above). Pure."""
    first, rels = bool(_FIRST_PERSON_RE.search(said)), len(_REL_RE.findall(said))
    if _PLURAL_PEOPLE_RE.search(said):
        return False
    if subject == "User":
        return first and rels == 0
    name = re.escape(subject)
    if first or rels > 1 or not re.search(rf"\b{name}\b", said, re.IGNORECASE):
        return False
    return not re.search(rf"\b\w+\s*(?:,|&|\band\b)\s*{name}\b|\b{name}\s*(?:,|&|\band\b)\s*(?!(?:i|im|cant|can|cannot|is|has|he|she)\b)\w+",
                         said, re.IGNORECASE)


def _present_emergency(fact: str, said: str) -> Optional[Verdict]:
    """The call-now verdict when ``said`` (ONE turn) and the ``fact`` mined from it are the SAME call-now emergency said NOW (no past
    time, no condition, in either), else None. The first half of ``emergency_event``'s rule. Pure."""
    one = (said or "").strip()
    if not one or "\n" in one or _not_now(one) or _not_now(fact):
        return None
    v = detect(one)
    if v.cls != "red_call":
        return None
    fv = detect(fact or "")
    return v if fv.cls == "red_call" and fv.key == v.key else None


def unfiled_emergency(fact: str, said: str = "") -> bool:
    """A mined fact that IS a present call-now emergency of its turn (``_present_emergency``) that ``emergency_event`` declined to
    convert (a bystander, more than one person, a subject it cannot tie to the turn: QA round 10a). Stored as mined it reads as a
    standing condition ("<name> fell over and cannot get up" under Routines, "wife has chest pain" as an attribute), so an AUTOMATIC
    writer does not store it at all until the structured-claims redesign files it against the right person. The spoken reply is
    unaffected. Pure; never raises."""
    try:
        return _present_emergency(fact, said) is not None and emergency_event(fact, said) is None
    except Exception:  # noqa: BLE001
        return False


def emergency_event(fact: str, said: str = "", *, today=None) -> Optional[str]:
    """The ONE dated health event a call-now emergency is filed as ("User had a fall on 13 November 2026 (a medical emergency).",
    "User's son Max had chest pain on ..."), or None: the mined fact is then stored exactly as mined. Only under THE CONSERVATIVE
    RULE above, and only when ``said`` (the ONE turn it was mined from) is a call-now emergency said NOW (no past time in the turn
    or the fact: "chest pain in 2020" keeps its date). In EVERY mode: the flag gates the reply, never how memory files an
    emergency. A whole transcript (several lines) is never read as the turn. Pure apart from the date. Never raises."""
    try:
        one = (said or "").strip()
        # one check, "is this happening now to this person": present (no past time), actual (no condition), then one named person
        v = _present_emergency(fact, one)
        if v is None:
            return None
        subject = _subject(fact)
        if subject is None or not _only_person(one, subject) or _names_another(fact, one, subject):
            return None
        if not _clause_is_about(one, subject):
            return None
        rel = _REL_OF_RE.match(fact or "") if _WHO_REL_RE.match(fact or "") else None
        who = f"User's {rel.group(1).lower()} {subject}" if rel else subject
        import datetime as _dt

        d = today or _dt.date.today()
        return f"{who} had {_EVENT_WHAT.get(v.key, 'a medical emergency')} on {d.day} {d:%B %Y} (a medical emergency)."
    except Exception:  # noqa: BLE001
        return None


_EVENT_TEXT_RE = re.compile(r"^(?P<who>.+?) had (?P<what>.+?) on (?P<date>\d{1,2} [A-Z][a-z]+ \d{4}) \(a medical emergency\)\.?$")


def event_key(text: str) -> Optional[tuple]:
    """``(person, kind, date)`` of an ``emergency_event`` text ("User had a fall on 14 November 2026 (a medical emergency)."), else
    None. Two event texts are the SAME record only when all three match: the fixed template's own words ("User had ... on <date>
    (a medical emergency)") outweigh the kind word, so a word-overlap paraphrase test read the day's chest pain as a repeat of the
    day's fall and dropped it (QA round 10a). Pure."""
    m = _EVENT_TEXT_RE.match((text or "").strip())
    return (m.group("who").strip().lower(), m.group("what").strip().lower(), m.group("date")) if m else None


def build_reply(v: Verdict, *, minor: bool = False, intercom: bool = False) -> str:
    """The fixed text for a verdict. Never model text. A call-now reply also says how to get someone in the house (QA round 8: a member
    who has fallen may not reach a phone): the intercom phrase only when ``intercom`` says it will work, else "call out". Zoe never
    claims to have alerted or rung anyone."""
    r = _data()["replies"]
    emergency, poison = _lines()
    f = dict(emergency=emergency, poison_number=poison or r["poison_number_fallback"])
    if v.cls == "ingest_human":
        out = r[v.key if poison else v.key + "_nolines"].format(**f)
    elif v.cls == "ingest_animal":
        out = r["poison_animal"].format(**f)
    elif v.cls == "number":
        out = r["emergency_number"].format(**f)
        if v.key == "emergency_number_poison":
            out += r["emergency_number_poison" if poison else "emergency_number_poison_nolines"].format(**f)
        if len([w for w in _FILLER.sub(" ", v.lead).split() if w]) >= 3:
            out += r["thread_back"]
    elif v.cls in {"red_call", "red_soon"}:
        out = r[v.key].format(**f)
        if v.cls == "red_call":
            out += r["alert_intercom" if intercom else "alert_call_out"]
        if minor and v.cls == "red_call":
            out = r["minor_prefix"] + out
    elif v.cls == "glucose":
        out = _glucose_reply(v, r, emergency)
    else:
        return ""
    return out


async def handle(text: str, user_id: str = "", *, dry: bool = False, voice: bool = False) -> Optional[str]:
    """The seam. The fixed reply when this turn is a medical-safety class in ENFORCE (the caller must NOT ask the brain), else None. NEVER raises."""
    try:
        m = mode()
        if m == "off":
            return None
        v = detect(text)
        if not v.cls:
            held = _detect(text, recall_gate=False)
            if held.cls in ("red_call", "red_soon"):
                # a recall question about the past: answered from memory, not the floor (a fixed code, never the words)
                logger.info("MEDICAL_SAFETY_RECALL_QUESTION user=%s cls=%s key=%s mode=%s",
                            (user_id or "guest").strip() or "guest", held.cls, held.key, m)
            return None
        logger.info("MEDICAL_SAFETY user=%s cls=%s key=%s mode=%s", (user_id or "guest").strip() or "guest", v.cls, v.key, m)
        if m != "enforce":
            return None
        minor = False
        if v.cls == "red_call":
            import distress_handoff

            minor = await distress_handoff._is_minor((user_id or "").strip())
        return build_reply(v, minor=minor, intercom=v.cls == "red_call" and await _intercom_reachable(user_id, voice)) or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("medical_safety handle failed (%s)", type(exc).__name__)
        return None
