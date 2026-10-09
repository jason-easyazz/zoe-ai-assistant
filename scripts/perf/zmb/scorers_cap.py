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


# ── (k) the night mind: K6 compression, K7 dense day, K8 citations, K9 change / quiet, K10 restraint, K11 resolution, K12 labels ────────────────────────────
# Deterministic, no judge. Every one reads what the arm EXPORTED (observations with their thread and turn pointers, the threads, the pass's changes, the
# simulated mornings) and returns counts, never household text.

def _covers(item: Any, thread: "dict[str, Any]") -> bool:
    text = (item or {}).get("text", "") if isinstance(item, dict) else str(item)
    return all(_has(text, i) for i in thread["identity"]) and _has(text, thread["key"])


def score_compression(items: Sequence[Any], gold: "dict[str, Any]", *, n_turns: int, max_per_thread: int = 3, max_share: float = 0.6) -> Score:
    """K6: a layer that COPIES every turn is not reflection. Every gold thread has at least one and at most ``max_per_thread`` non-false observations, and the
    whole export is at most ``max_share`` of the owner's turns. The echo arm (observations = the owner's turns) fails the second bound."""
    obs = _items(items)
    per = {t["id"]: 0 for t in gold["threads"]}
    for o in obs:
        lab, _ = classify_observation(o.get("text", ""), gold)
        if lab == FALSE:
            continue
        for t in gold["threads"]:
            if _covers(o, t):
                per[t["id"]] += 1
    bounded = sum(1 for c in per.values() if 1 <= c <= max_per_thread)
    share = (len(obs) / n_turns) if n_turns else 1.0
    ok = bounded == len(per) and share <= max_share and bool(obs)
    return Score(ok, "" if ok else "read", {"compression": {"observations": len(obs), "turns": n_turns, "share": round(share, 3), "max_share": max_share,
                                                             "per_thread": per, "max_per_thread": max_per_thread}, "items": [bounded, len(per)]})


def score_late_threads(items: Sequence[Any], gold: "dict[str, Any]", late: Sequence[str], *, min_recall: float = 0.7) -> Score:
    """K7: thread recall on a DENSE day, overall AND on the threads planted late in the day (where a 3,000-character cut never reaches)."""
    got = covered_threads(items, gold)
    ids = [t["id"] for t in gold["threads"]]
    late_hit = sum(1 for i in late if i in got)
    overall, lrate = (len(got) / len(ids) if ids else 0.0), (late_hit / len(late) if late else 0.0)
    ok = overall >= min_recall and lrate >= min_recall
    n = len(ids) + len(late)
    return Score(ok, "" if ok else "read", {"dense": {"threads": len(ids), "covered": sorted(got), "overall": round(overall, 3), "late": list(late), "late_covered": late_hit,
                                                      "late_rate": round(lrate, 3), "min_recall": min_recall}, "items": [len(got) + late_hit, n]})


def _squash(t: str) -> str:
    return re.sub(r"\s+", " ", str(t or "")).strip().lower()


def score_citations(items: Sequence[Any], lookup: Any, *, min_observations: int = 3) -> Score:
    """K8: every served observation's pointer is real: its ``turn_id`` exists in this member's turns and its words are a span of that turn. 100 %, and a layer
    that exports nothing has not passed. ``lookup(turn_id) -> text | None``."""
    obs = _items(items)
    ok_n = 0
    missing = wrong = 0
    for o in obs:
        text = lookup(str(o.get("turn_id") or "")) if o.get("turn_id") else None
        if text is None:
            missing += 1
        elif _squash(o.get("text", "")) and _squash(o.get("text", "")) in _squash(text):
            ok_n += 1
        else:
            wrong += 1
    ok = len(obs) >= min_observations and ok_n == len(obs)
    return Score(ok, "" if ok else ("write" if (missing or wrong) else "read"),
                 {"citations": {"observations": len(obs), "valid": ok_n, "missing_turn": missing, "not_a_span": wrong, "min_observations": min_observations}, "items": [ok_n, len(obs)]})


def _thread_for(threads: Sequence[dict], key: str) -> "Optional[dict]":
    for t in threads:
        if key in str(t.get("anchors") or "").split() or key in str(t.get("title") or "").lower():
            return t
    return None


def score_change_quiet(threads: Sequence[dict], changes: Sequence[dict], gold: "dict[str, Any]", *, flat: bool, max_false: float = 0.05) -> Score:
    """K9. DRIFT: the thread that stopped being mentioned is flagged ``quiet`` with >= 2 cited turn ids, and the thread whose plan changed is ``changed``; the
    steady thread is neither. FLAT (no drift planted): the false-notice rate - threads flagged quiet or changed / threads - is at most ``max_false``."""
    flagged_quiet = {c["thread"] for c in changes if c.get("type") == "quiet"}
    flagged_change = {t["id"] for t in threads if t.get("status") == "changed"}
    n = len(threads)
    if flat:
        false = len(flagged_quiet | flagged_change)
        rate = false / n if n else 1.0
        ok = n > 0 and rate <= max_false
        return Score(ok, "" if ok else "write", {"flat_week": {"threads": n, "false_notices": false, "rate": round(rate, 3), "max_false": max_false}, "items": [n - false, n]})
    hits = 0
    detail: "dict[str, Any]" = {}
    for kind, keys in (("quiet", gold.get("quiet") or []), ("changed", gold.get("changed") or [])):
        for k in keys:
            t = _thread_for(threads, k)
            if t is None:
                detail[f"{kind}:{k}"] = "no thread"
                continue
            if kind == "quiet":
                ch = next((c for c in changes if c.get("type") == "quiet" and c.get("thread") == t["id"]), None)
                good = ch is not None and len(set(ch.get("ids") or [])) >= 2
            else:
                good = t["id"] in flagged_change
            hits += int(good)
            detail[f"{kind}:{k}"] = bool(good)
    steady_wrong = 0
    for k in gold.get("steady") or []:
        t = _thread_for(threads, k)
        if t is not None and (t["id"] in flagged_quiet or t["id"] in flagged_change):
            steady_wrong += 1
    want = len(gold.get("quiet") or []) + len(gold.get("changed") or [])
    ok = want > 0 and hits == want and steady_wrong == 0
    return Score(ok, "" if ok else "read", {"drift": {"wanted": want, "detected": hits, "steady_flagged": steady_wrong, "detail": detail}, "items": [hits, want]})


def score_restraint(plan: Sequence[dict], threads: Sequence[dict], gold: "dict[str, Any]", asked: "Sequence[tuple[str, bool]]") -> Score:
    """K10, over simulated mornings with every raise ignored: (1) NO raise of a ``leave`` thread (health, grief, money, conflict, 'don't bring that up');
    (2) at most ONE raise a morning; (3) the BACK-OFF doubles after an ignored raise (the gaps between successive raises of the benign thread at least double,
    and it was raised at least twice); (4) a leave thread is still recallable when the member asks (``asked`` = (key, found))."""
    leave_ids = {t["id"] for k in gold.get("leave") or [] for t in [_thread_for(threads, k)] if t}
    leave_raises = sum(1 for m in plan for tid in m["raised"] if tid in leave_ids)
    many = sum(1 for m in plan if len(m["raised"]) > 1)
    benign = [t for k in gold.get("benign") or [] for t in [_thread_for(threads, k)] if t]
    days = [i for i, m in enumerate(plan) for tid in m["raised"] if benign and tid == benign[0]["id"]]
    gaps = [b - a for a, b in zip(days, days[1:])]
    doubles = len(days) >= 2 and all(gaps[i] >= 2 * (gaps[i - 1] if i else 1) for i in range(len(gaps)))
    recallable = all(f for _k, f in asked) and bool(asked)
    found_leave = len(leave_ids) >= len(gold.get("leave") or [1])
    ok = leave_raises == 0 and many == 0 and doubles and recallable and found_leave
    return Score(ok, "" if ok else "write", {"restraint": {"mornings": len(plan), "leave_threads": len(leave_ids), "leave_raised": leave_raises, "mornings_over_one": many,
                                                           "benign_raises": len(days), "gaps": gaps, "backoff_doubles": doubles, "recallable_when_asked": recallable},
                                             "items": [int(leave_raises == 0) + int(many == 0) + int(doubles) + int(recallable), 4]})


def score_resolution(threads: Sequence[dict], gold: "dict[str, Any]") -> Score:
    """K11: a plan with no reported outcome stays ``open``; a plan the owner reports finished is ``resolved``; a thread nobody mentions again is never CLOSED
    (it may go ``quiet``: absence is a fact about counts, not an ending)."""
    checks = {}
    for kind, keys, allowed in (("open", gold.get("open") or [], ("open", "changed", "quiet")), ("resolved", gold.get("resolved") or [], ("resolved",)),
                                ("absent", gold.get("absent") or [], ("open", "quiet", "changed", "recurring"))):
        for k in keys:
            t = _thread_for(threads, k)
            checks[f"{kind}:{k}"] = bool(t and t.get("status") in allowed)
    ok = bool(checks) and all(checks.values())
    return Score(ok, "" if ok else "write", {"resolution": {"checks": checks}, "items": [sum(1 for v in checks.values() if v), len(checks)]})


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float:
    def rank(v: Sequence[float]) -> "list[float]":
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0 + 1.0
            i = j + 1
        return r
    if len(xs) < 3:
        return 0.0
    rx, ry = rank(xs), rank(ys)
    mx, my = sum(rx) / len(rx), sum(ry) / len(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5
    return num / den if den else 0.0


def score_weights(labels: Sequence[dict], gold: Sequence[dict], *, min_spearman: float = 0.5, min_accuracy: float = 0.85, min_matched: float = 0.75) -> Score:
    """K12: the model's moment labels against the written ones. ``labels`` = what the pass picked (quote, kind, feeling, weight); matched to the gold by the quote
    (the turn text). Passes at weight rank correlation >= ``min_spearman`` AND kind / feeling accuracy >= ``min_accuracy`` over the moments it picked, having
    picked at least ``min_matched`` of the labelled ones. Below that the weights are reported, not trusted."""
    by_quote = {_squash(l["quote"]): l for l in labels}
    pairs = [(g, by_quote[_squash(g["text"])]) for g in gold if _squash(g["text"]) in by_quote]
    matched = len(pairs) / len(gold) if gold else 0.0
    acc_n = sum(int(g["kind"] == m["kind"]) + int(g["feeling"] == m["feeling"]) for g, m in pairs)
    acc = acc_n / (2 * len(pairs)) if pairs else 0.0
    rho = spearman([g["weight"] for g, _ in pairs], [m["weight"] for _, m in pairs])
    ok = matched >= min_matched and acc >= min_accuracy and rho >= min_spearman
    return Score(ok, "" if ok else "read", {"weights": {"labelled": len(gold), "matched": len(pairs), "matched_rate": round(matched, 3), "accuracy": round(acc, 3),
                                                        "spearman": round(rho, 3), "min_accuracy": min_accuracy, "min_spearman": min_spearman}, "items": [acc_n, 2 * len(pairs)]})


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
