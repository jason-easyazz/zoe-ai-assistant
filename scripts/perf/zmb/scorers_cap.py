"""Deterministic scorers of the four CAPABILITY axes (j exact words, k reflection, l multi-hop, m protocol). Pure: no I/O, no model, no clock.

Same contract as ``scorers.py``: a scorer reads what the arm EXPORTED and returns a ``Score`` whose evidence is counts and labels, never household
text. Every item-based score also carries ``items: [passed, n]`` - the unit the winner clause pools across cells and seeds, because a Wilson
interval over two or three CELLS cannot separate 20 of 20 from 0 of 20, and over the 20 ITEMS it does.
"""
from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from .scorers import Score, normalize, wilson

# ═══ shared ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════════


def _items_score(passed: int, n: int, *, label: str, min_rate: float, stage: str = "read", extra: "Optional[dict]" = None) -> Score:
    rate = (passed / n) if n else 0.0
    lo, hi = wilson(passed, n)
    ok = n > 0 and rate >= min_rate
    ev = {label: {"n": n, "hits": passed, "rate": round(rate, 4), "wilson95": [round(lo, 4), round(hi, 4)], "min_rate": min_rate, **(extra or {})},
          "items": [passed, n]}
    return Score(ok, "" if ok else stage, ev)


def _has(text: str, token: str) -> bool:
    t = normalize(token)
    return bool(t) and re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", normalize(text)) is not None


# ═══ (j) exact words ════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def span_of(sentence: str) -> str:
    """The verbatim span a correct answer carries: the sentence, normalised (case, accents, whitespace) and without its closing punctuation."""
    return normalize(sentence).rstrip(" .!?")


def contains_span(text: str, sentence: str) -> bool:
    s = span_of(sentence)
    return bool(s) and s in normalize(text)


def score_exact(hits: Sequence[bool], styles: Sequence[str], *, k: int, min_rate: float) -> Score:
    """Exact words: ``hits[i]`` = the answer to needle i held its sentence word for word. PASS iff the rate >= ``min_rate`` (and n > 0)."""
    by: "dict[str, list[int]]" = {}
    for h, s in zip(hits, styles):
        by.setdefault(s, [0, 0])
        by[s][0] += int(bool(h))
        by[s][1] += 1
    return _items_score(sum(1 for h in hits if h), len(hits), label="exact", min_rate=min_rate,
                        extra={"k": k, "by_style": by})


def score_when(guessed: "Sequence[Optional[float]]", gold: Sequence[int], *, min_rate: float, tolerance: float = 0.5) -> Score:
    """When did I say it: ``guessed[i]`` = the day (ago) the arm says the sentence was said, ``None`` = it cannot say. A hit is within ``tolerance`` days."""
    hits = sum(1 for g, w in zip(guessed, gold) if g is not None and abs(float(g) - float(w)) <= tolerance)
    cannot = sum(1 for g in guessed if g is None)
    return _items_score(hits, len(gold), label="when", min_rate=min_rate, extra={"cannot_say": cannot, "tolerance_days": tolerance})


# ═══ (l) multi-hop ══════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

def fact_in_rows(rows_text: Sequence[str], need: "tuple[str, str]") -> bool:
    """One packet row holds BOTH strings (the subject and the answer token): the fact is in the packet."""
    return any(_has(t, need[0]) and _has(t, need[1]) for t in rows_text)


def score_hops(both: Sequence[bool], kinds: Sequence[str], a_only: int, b_only: int, *, k: int, min_rate: float) -> Score:
    """Multi-hop: ``both[i]`` = BOTH facts of question i are in the packet. ``a_only`` / ``b_only`` count the questions where only the first / the
    second fact made it (where the chain broke). PASS iff the rate >= ``min_rate``."""
    by: "dict[str, list[int]]" = {}
    for h, s in zip(both, kinds):
        by.setdefault(s, [0, 0])
        by[s][0] += int(bool(h))
        by[s][1] += 1
    return _items_score(sum(1 for h in both if h), len(both), label="hops", min_rate=min_rate,
                        extra={"k": k, "by_kind": by, "only_first": a_only, "only_second": b_only})


# ═══ (k) reflection ═════════════════════════════════════════════════════════════════════════════════════════════════════════════════════

TRUE, FALSE, NEUTRAL = "true", "false", "neutral"


def _ents(text: str, ents: Sequence[str]) -> "set[str]":
    low = " " + re.sub(r"[^a-z0-9' ]+", " ", normalize(text)) + " "
    low = re.sub(r"'s\b", "", low)
    return {e for e in ents if re.search(r"(?<![a-z0-9])" + re.escape(e) + r"(?![a-z0-9])", low)}


def classify_observation(text: str, gold: "dict[str, Any]") -> "tuple[str, str]":
    """``(label, why)`` of one derived observation against the life's gold. No model, no judge:

    * ``false/foreign``     it names an entity from another household (a hallucination);
    * ``false/stale``       it states an invalidated value (the sister's old town, the old dentist) as current, with no history marker;
    * ``false/link``        it puts two entities in one statement that the owner never put in one sentence (the fabricated link);
    * ``true``              it names two or more entities and every pair was said together;
    * ``neutral``           it names fewer than two entities: nothing in it can be judged (reported, never counted for or against).
    """
    low = normalize(text)
    ents = _ents(text, gold["entities"])
    foreign = _ents(text, gold.get("foreign") or [])
    if foreign:
        return FALSE, "foreign"
    hist = any(h in low for h in gold.get("history") or [])
    for subj, old, _new in gold.get("stale") or []:
        past = hist or re.search(r"\b(?:from|left|leaving|quit|out of)\s+(?:\w+\s+){0,2}" + re.escape(normalize(old)), low) is not None
        if _has(low, old) and (_has(low, subj) or subj in low) and not past:
            return FALSE, "stale"
    if len(ents) >= 2:
        pairs = {frozenset(p) for p in gold["pairs"]}
        names = sorted(ents)
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                if frozenset((names[i], names[j])) not in pairs:
                    return FALSE, "link"
        return TRUE, "supported"
    return NEUTRAL, "undecidable"


def _items(raw: Sequence[Any]) -> "list[dict[str, Any]]":
    return [x if isinstance(x, dict) else {"text": str(x)} for x in raw]


def score_observations(items: Sequence[Any], gold: "dict[str, Any]", *, kind: str, min_precision: float = 0.95, min_decidable: int = 3,
                       min_observations: int = 3) -> Score:
    """The reflection scorers over an arm's observation export (``items``: ``{"text", "stated_by"?}``).

    * ``kind="true"``     precision = true / (true + false) over the decidable ones; >= ``min_precision`` AND >= ``min_decidable`` of them;
    * ``kind="current"``  not one observation states an invalidated value as current (and at least ``min_observations`` exist: a layer that
      reflects nothing is not current, it is empty);
    * ``kind="attributed"`` a user-stated fact is never re-stated as an inference (labelled ``inferred`` or hedged), and an inference is never presented
      as something the user said (labelled ``user`` or "you told me") - zero violations over at least ``min_observations``.
    """
    obs = _items(items)
    labels = [classify_observation(o.get("text", ""), gold) for o in obs]
    nt = sum(1 for lab, _ in labels if lab == TRUE)
    nf = sum(1 for lab, _ in labels if lab == FALSE)
    nn = sum(1 for lab, _ in labels if lab == NEUTRAL)
    why = {w: sum(1 for lab, ww in labels if lab == FALSE and ww == w) for w in ("foreign", "stale", "link")}
    base = {"observations": len(obs), "true": nt, "false": nf, "neutral": nn, "false_by": why}
    if kind == "true":
        dec = nt + nf
        if dec < min_decidable:
            # items [0, 0]: an insufficient sample is not a measurement, so it must not enter the winner's pooled Wilson interval (the counts stay in the evidence)
            return Score(False, "read", {"observations_judged": {**base, "decidable": dec, "reason": "too few decidable observations"}, "items": [0, 0]})
        return _items_score(nt, dec, label="observations_judged", min_rate=min_precision, stage="write", extra={**base, "decidable": dec, "min_decidable": min_decidable})
    if kind == "current":
        stale = why["stale"]
        ok = stale == 0 and len(obs) >= min_observations
        return Score(ok, "" if ok else ("write" if stale else "read"),
                     {"currency": {**base, "stale_as_current": stale, "min_observations": min_observations}, "aux_items": [len(obs) - stale, len(obs)]})
    if kind == "attributed":
        viol = 0
        claims = [set(c) for c in gold.get("claims") or []]
        for o, (lab, _w) in zip(obs, labels):
            text, by = o.get("text", ""), str(o.get("stated_by") or "").lower()
            low = normalize(text)
            ents = _ents(text, gold["entities"])
            restates = lab == TRUE and any(c <= ents for c in claims)
            hedged = any(h in low for h in gold.get("hedges") or [])
            says_user = any(m in low for m in gold.get("stated") or []) or by == "user"
            if restates and (hedged or by == "inferred"):
                viol += 1                       # a user-stated fact re-stated as an inference
            elif says_user and not restates and (len(ents) >= 2 or lab == FALSE):
                viol += 1                       # an inference (or an invention) presented as something the user said
        ok = viol == 0 and len(obs) >= min_observations
        return Score(ok, "" if ok else ("write" if viol else "read"),
                     {"attribution": {**base, "violations": viol, "min_observations": min_observations}, "aux_items": [len(obs) - viol, len(obs)]})
    raise ValueError(f"unknown observation scorer kind {kind!r}")


def covered_threads(items: Sequence[Any], gold: "dict[str, Any]") -> "set[str]":
    """The ids of the gold threads a set of observations covers: an observation covers a thread when it holds every identity token and the key
    token, and is not itself false."""
    out: "set[str]" = set()
    for o in _items(items):
        lab, _ = classify_observation(o.get("text", ""), gold)
        if lab == FALSE:
            continue
        for t in gold["threads"]:
            if all(_has(o.get("text", ""), i) for i in t["identity"]) and _has(o.get("text", ""), t["key"]):
                out.add(t["id"])
    return out


def score_threads(items: Sequence[Any], gold: "dict[str, Any]", *, min_recall: float = 0.7) -> Score:
    """Thread recall: the share of the life's four developing stories an observation covers (and a false observation never counts)."""
    ids = [t["id"] for t in gold["threads"]]
    got = covered_threads(items, gold)
    return _items_score(len(got), len(ids), label="threads", min_rate=min_recall, extra={"covered": sorted(got)})


def score_useful(answers: "Sequence[Sequence[Any]]", wanted: "Sequence[Sequence[str]]", gold: "dict[str, Any]", *, min_rate: float = 0.7) -> Score:
    """"What's been going on with X lately?" / "how has my week been?": for each question the observations the arm offered (``answers[i]``) must
    cover at least half of the threads it should surface (rounded up)."""
    hits = 0
    for got_items, want in zip(answers, wanted):
        covered = covered_threads(got_items, gold) & set(want)
        if want and len(covered) >= -(-len(want) // 2):
            hits += 1
    return _items_score(hits, len(wanted), label="useful", min_rate=min_rate)


# ═══ (m) memory protocol ════════════════════════════════════════════════════════════════════════════════════════════════════════════════

PROTOCOL_BARS = {"fire_when_needed": 0.90, "quiet_when_not_needed": 0.90, "answered_when_fired": 0.90, "cite_precision": 0.95, "idk_when_silent": 0.90}


def protocol_metrics(records: Sequence["dict[str, Any]"]) -> "dict[str, Any]":
    """The four protocol numbers from per-prompt records ``{"kind": needed|unneeded|silent, "fired": bool, "answer": str, "gold": str}``
    (a record is what a brain's trace holds: whether it called recall, and what it answered). ``decline`` = the "I don't know" reply.

    * fire_when_needed    recall fired on the prompts the store CAN answer;
    * quiet_when_not_needed recall did NOT fire on device commands / small talk / world questions;
    * cite_precision      of every answer that cites a fact, the share that cites the RIGHT one (an answer on a silent prompt is a wrong citation);
    * idk_when_silent     the store is silent about the person asked: the reply says so (an invented answer is a fabrication)."""
    from .life import DECLINE
    needed = [r for r in records if r["kind"] == "needed"]
    unneeded = [r for r in records if r["kind"] == "unneeded"]
    silent = [r for r in records if r["kind"] == "silent"]

    def declined(r: "dict[str, Any]") -> bool:
        return r.get("answer", "") in ("", DECLINE) or "don't know" in r.get("answer", "").lower() or "don't have" in r.get("answer", "").lower()
    cites = [r for r in records if not declined(r)]
    right = [r for r in cites if r["kind"] == "needed" and r.get("gold") and _has(r["answer"], r["gold"])]
    return {"fire_when_needed": [sum(1 for r in needed if r["fired"]), len(needed)],
            "quiet_when_not_needed": [sum(1 for r in unneeded if not r["fired"]), len(unneeded)],
            "cite_precision": [len(right), len(cites)],
            "answered_when_fired": [sum(1 for r in needed if r["fired"] and r.get("gold") and _has(r.get("answer", ""), r["gold"])), sum(1 for r in needed if r["fired"])],
            "idk_when_silent": [sum(1 for r in silent if declined(r)), len(silent)],
            "fired_total": sum(1 for r in records if r["fired"]), "prompts": len(records)}


def score_protocol(records: Sequence["dict[str, Any]"], metric: str) -> Score:
    """One protocol metric against its bar (``PROTOCOL_BARS``). ``cite_precision`` also needs at least one citation (silence is not precision)."""
    if metric not in PROTOCOL_BARS:
        raise ValueError(f"unknown protocol metric {metric!r} (known: {', '.join(PROTOCOL_BARS)})")
    m = protocol_metrics(records)
    got, n = m[metric]
    if metric == "cite_precision" and n == 0:
        return Score(False, "read", {"protocol": {metric: {"n": 0, "reason": "no answer cited anything"}}, "items": [0, 0]})
    return _items_score(got, n, label="protocol", min_rate=PROTOCOL_BARS[metric], stage="answer" if metric != "fire_when_needed" else "read",
                        extra={"metric": metric, "fired": m["fired_total"], "prompts": m["prompts"]})
