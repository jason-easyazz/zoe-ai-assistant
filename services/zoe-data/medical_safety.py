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
  2. red-flag symptoms that need the ambulance now (chest tightness, stroke signs, breathing, heavy bleeding, worst headache): the number, plainly, first.
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
NOT marked off the record (unlike a distress turn): a symptom the member mentions is still recallable. Doc: docs/knowledge/medical-safety.md. Stdlib only. NEVER raises.
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


def _pair_rx(p: dict) -> "re.Pattern":
    gap, body, sym = int(p.get("window", 4)), p["body"], p["sym"]
    pre = "".join(rf"(?<!{re.escape(w)} )" for w in p.get("body_not_after", []))
    post = "".join(rf"(?!\s+(?:{n}))" for n in [("|".join(p.get("body_not_before", [])))] if n)
    b = rf"{pre}(?:{body}){post}"
    return re.compile(rf"(?<!\w)(?:{b})(?!\w)(?:\s+\w+){{0,{gap}}}\s+(?:{sym})(?!\w)|(?<!\w)(?:{sym})(?:\s+\w+){{0,{gap}}}\s+{b}(?!\w)", re.I)


@lru_cache(maxsize=1)
def _rx() -> dict:
    d = _data()
    w = lambda body: re.compile(rf"(?<!\w)(?:{body})(?!\w)", re.I)  # noqa: E731
    ing, nq, gl = d["ingest"], d["number_question"], d["glucose"]
    return {
        "verb": w(ing["verb"]), "sub": w(ing["substance"]), "idiom": re.compile(ing["idioms"], re.I), "took": w(ing["took_verb"]), "took_risky": w(ing["took_risky"]),
        "human": w(ing["human"]), "animal": w(ing["animal"]), "obj_urgent": w(ing["obj_urgent"]), "obj_minor": w(ing["obj_minor"]), "obj_signs": w(ing["obj_signs"]),
        "pairs": [(p["id"], p["tier"], _pair_rx(p), re.compile(p["skip"], re.I) if p.get("skip") else None) for p in d.get("pairs", [])],
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


_DENIED_INSIDE = re.compile(r"(?<!\w)(?:no|not|isnt|arent|wasnt|never|dont|doesnt|without|nothing)(?!\w)")   # "my eyes are not blurry": the denial sits between body and symptom


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
    for i, seg in enumerate(parts):
        if not (rx["obj_minor"].search(seg) or (i and _CONTINUES.match(seg) and rx["obj_minor"].search(parts[i - 1]))):
            continue
        if seg.rstrip().endswith("?") or _HYPOTHETICAL.search(seg):
            continue
        if i and not rx["obj_minor"].search(seg) and (rx["human"].search(seg) or re.search(r"(?<!\w)(?:i|im|ive|my)(?!\w)", seg)):
            continue
        for m in rx["obj_signs"].finditer(seg):
            if not _negated(seg, m.start(), rx):
                return True
    return False


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


def detect(text: str) -> Verdict:
    """The class of one turn (``NONE`` = leave it to the brain). Pure; never raises."""
    try:
        s = _fold(text)
        if not s:
            return NONE
        rx = _rx()
        c = _canon(s)
        ing = _ingestion(s, rx)
        call = _flag(s, rx, "call") or _pair(c, rx, "call")
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
        soon = _flag(s, rx, "soon") or _pair(c, rx, "soon")
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


def build_reply(v: Verdict, *, minor: bool = False) -> str:
    """The fixed text for a verdict. Never model text."""
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
        if minor and v.cls == "red_call":
            out = r["minor_prefix"] + out
    elif v.cls == "glucose":
        out = _glucose_reply(v, r, emergency)
    else:
        return ""
    return out


async def handle(text: str, user_id: str = "", *, dry: bool = False) -> Optional[str]:
    """The seam. The fixed reply when this turn is a medical-safety class in ENFORCE (the caller must NOT ask the brain), else None. NEVER raises."""
    try:
        m = mode()
        if m == "off":
            return None
        v = detect(text)
        if not v.cls:
            return None
        logger.info("MEDICAL_SAFETY user=%s cls=%s key=%s mode=%s", (user_id or "guest").strip() or "guest", v.cls, v.key, m)
        if m != "enforce":
            return None
        minor = False
        if v.cls == "red_call":
            import distress_handoff

            minor = await distress_handoff._is_minor((user_id or "").strip())
        return build_reply(v, minor=minor) or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("medical_safety handle failed (%s)", type(exc).__name__)
        return None
