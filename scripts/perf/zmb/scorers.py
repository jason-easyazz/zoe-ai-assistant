"""Deterministic scorers. Pure functions: no I/O, no model, no clock, no network.

A scorer reads what the arm EXPORTED (rows, a recall packet, a reply) and returns a ``Score``. Gold comes
from the generated world, so there is no judge and no corrupt-golden-answer ceiling. Every FAIL carries a
``stage`` (``write`` = the store is wrong, ``read`` = retrieval missed, ``answer`` = the store is right and
the reply is wrong): the write / read split is how a failure is reported, not just counted.

Evidence is counts and labels, **never household text**: an artifact built from it can be shared.
"""
from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

SCORER_VERSION = "1"
STAGES = ("write", "read", "answer")


@dataclass
class Score:
    ok: bool
    stage: str = ""                      # "" on PASS; write | read | answer on FAIL
    evidence: "dict[str, Any]" = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.stage and self.stage not in STAGES:
            raise ValueError(f"unknown stage {self.stage!r}")

    @property
    def verdict(self) -> str:
        return "PASS" if self.ok else "FAIL"


def merge(*scores: Score) -> Score:
    """All must pass; the first failure's stage wins; evidence is merged under its own keys."""
    ev: dict[str, Any] = {}
    for i, s in enumerate(scores):
        ev.update({f"{k}" if k not in ev else f"{k}#{i}": v for k, v in s.evidence.items()})
    bad = next((s for s in scores if not s.ok), None)
    return Score(ok=bad is None, stage=bad.stage if bad else "", evidence=ev)


def score_disk(report: "dict[str, Any]", *, stage: str = "write") -> Score:
    """The forgotten text is PHYSICALLY gone: ``report`` is ``memory_residue.scan_palace``'s output (counts per
    token / file / SQLite page owner - never text). Any byte-hit anywhere fails the cell at the write stage."""
    toks = report.get("tokens") or {}
    total = sum(int(t.get("total", 0)) for t in toks.values())
    where = sorted({f for t in toks.values() for f in (t.get("files") or {})})
    owners = sorted({o for t in toks.values() for o in (t.get("sqlite_pages") or {})})
    ev = {"tokens": len(toks), "byte_hits": total, "files": where[:8], "sqlite_page_owners": owners[:8]}
    ok = bool(toks) and total == 0
    return Score(ok=ok, stage="" if ok else stage, evidence=ev)


# ── text normalisation ───────────────────────────────────────────────────────

def normalize(text: str) -> str:
    """Lower-case, accent-folded, whitespace-collapsed (so 'Tomas' matches 'Tomás')."""
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t.lower()).strip()


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", normalize(text))


def contains_phrase(text: str, phrase: str) -> bool:
    """Whole-word / whole-phrase match on normalised text ('Dev' is not in 'Devon')."""
    p = normalize(phrase)
    if not p:
        return False
    return re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", normalize(text)) is not None


def found_needles(text: str, needles: Iterable[str]) -> list[str]:
    return [n for n in needles if contains_phrase(text, n)]


def fact_present(text: str, needles: Sequence[str], window: int = 12) -> bool:
    """Every needle occurs in ``text`` within a ``window``-token span (so 'Mika' and 'son' must sit in
    the same clause, not just the same paragraph)."""
    if not needles:
        return False
    ws = _words(text)
    spans: list[list[int]] = []
    for n in needles:
        nw = _words(n)
        if not nw:
            return False
        hits = [i for i in range(len(ws) - len(nw) + 1) if ws[i:i + len(nw)] == nw]
        if not hits:
            return False
        spans.append(hits)
    # smallest window containing one hit of each needle
    for start in spans[0]:
        if all(any(abs(h - start) <= window for h in hs) for hs in spans[1:]):
            return True
    return False


# ── statistics ───────────────────────────────────────────────────────────────

def wilson(k: int, n: int, z: float = 1.96) -> "tuple[float, float]":
    """Wilson score interval for k successes in n trials (95% by default). n = 0 -> (0, 1): no data
    is the widest possible interval, never a point estimate."""
    if n <= 0:
        return (0.0, 1.0)
    if not 0 <= k <= n:
        raise ValueError(f"k={k} outside 0..n={n}")
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def prf(tp: int, fp: int, fn: int) -> "dict[str, float]":
    """Precision / recall / F1 from counts. No prediction -> precision 1.0 (nothing wrong was said);
    no gold -> recall 1.0; both empty -> F1 1.0."""
    p = tp / (tp + fp) if (tp + fp) else 1.0
    r = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (2 * p * r / (p + r)) if (p + r) else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f1, 4),
            "tp": tp, "fp": fp, "fn": fn}


# ── needle / anti-needle / canary scorers ────────────────────────────────────

def score_needles(text: str, needles: Sequence[str] = (), anti_needles: Sequence[str] = (),
                  canaries: Sequence[str] = (), *, stage: str = "answer") -> Score:
    """PASS iff every needle is present and no anti-needle / canary is. Evidence: counts only."""
    found = found_needles(text, needles)
    bad = found_needles(text, anti_needles)
    leaked = found_needles(text, canaries)
    ok = len(found) == len(needles) and not bad and not leaked
    return Score(ok, "" if ok else stage, {
        "needles": len(needles), "needles_found": len(found),
        "anti_needle_hits": len(bad), "canary_hits": len(leaked)})


def score_canaries(texts: Iterable[str], canaries: Sequence[str], *, stage: str = "write") -> Score:
    """PASS iff no canary token (a string that exists nowhere else in the world) appears in any text."""
    texts = list(texts)
    leaked = sorted({c for c in canaries for t in texts if contains_phrase(t, c)})
    return Score(not leaked, "" if not leaked else stage,
                 {"canaries": len(canaries), "canary_hits": len(leaked), "texts": len(texts)})


# ── store assertions over the arm's row export ───────────────────────────────

ASSERT_OPS = ("present", "absent", "count_eq", "count_at_least", "all_have", "field_set")


def _rows_matching(rows: Sequence[dict], contains: Sequence[str], statuses: "Sequence[str] | None") -> list[dict]:
    out = []
    for r in rows:
        if statuses and r.get("status") not in statuses:
            continue
        if all(contains_phrase(r.get("text", ""), c) for c in contains):
            out.append(r)
    return out


def score_store(rows: Sequence[dict], assertions: Sequence[dict], *, stage: str = "write") -> Score:
    """Evaluate store assertions over exported rows. An assertion is
    ``{"op": ..., "contains": [..], "statuses": [..], "n": int, "field": "authority_class", "equals": ".."}``:

    * ``present``        >= 1 row (with all ``contains``, in ``statuses``) exists
    * ``absent``         no such row exists
    * ``count_eq``       exactly ``n`` such rows
    * ``count_at_least`` at least ``n`` such rows
    * ``all_have``       every such row has ``row[field] == equals`` (and there is at least one)
    * ``field_set``      every such row has a non-empty ``row[field]`` (and there is at least one)

    Unknown ops raise: a typo in a spec must be a loud error, not a silent PASS. Evidence is which
    assertion INDEXES failed - never the text.
    """
    failed: list[int] = []
    for i, a in enumerate(assertions):
        op = a.get("op")
        if op not in ASSERT_OPS:
            raise ValueError(f"unknown store assertion op {op!r} (known: {', '.join(ASSERT_OPS)})")
        hit = _rows_matching(rows, a.get("contains") or [], a.get("statuses"))
        n = int(a.get("n", 1))
        ok = {"present": len(hit) >= 1,
              "absent": len(hit) == 0,
              "count_eq": len(hit) == n,
              "count_at_least": len(hit) >= n,
              "all_have": bool(hit) and all(r.get(a.get("field", "")) == a.get("equals") for r in hit),
              "field_set": bool(hit) and all(r.get(a.get("field", "")) for r in hit),
              }[op]
        if not ok:
            failed.append(i)
    return Score(not failed, "" if not failed else stage,
                 {"assertions": len(assertions), "failed": failed})


# ── entity precision / recall (extraction fidelity) ──────────────────────────

_NOT_ENTITIES = frozenset({
    "user", "user's", "users", "preference", "favourite", "person", "important", "note", "the", "my",
    "i", "he", "she", "they", "we", "you", "it", "a", "an", "and", "or", "of", "is", "was", "are",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
    "november", "december", "asked",
})
_CAP = re.compile(r"\b([A-Z][a-z]{2,})\b")


def entities_in(texts: Iterable[str], ignore: Iterable[str] = ()) -> set[str]:
    """Capitalised words in the texts that are not role words / months / labels: the names a store holds."""
    skip = _NOT_ENTITIES | {normalize(x) for x in ignore}
    out: set[str] = set()
    for t in texts:
        for m in _CAP.finditer(t or ""):
            w = normalize(m.group(1))
            if w and w not in skip:
                out.add(w)
    return out


def score_entities(pred_texts: Iterable[str], gold: Iterable[str], *, min_precision: float = 1.0,
                   min_recall: float = 0.75, ignore: Iterable[str] = ()) -> Score:
    """Entity precision / recall of the names a store holds against the generated gold set. A name in the
    store that is not in gold is a false positive (a hallucinated or mis-extracted entity)."""
    pred = entities_in(pred_texts, ignore)
    g = {normalize(x) for x in gold}
    m = prf(len(pred & g), len(pred - g), len(g - pred))
    ok = m["precision"] >= min_precision and m["recall"] >= min_recall
    return Score(ok, "" if ok else "write", {"entities": m})


def score_facts(texts: Sequence[str], gold_facts: Sequence[Sequence[str]],
                anti_facts: Sequence[Sequence[str]] = (), *, min_recall: float = 1.0) -> Score:
    """Fact recall and anti-fact precision over store texts. Each gold fact is a needle list that must sit
    in ONE text within a window; each anti-fact (a wrong owner, role, month-first date, pet-as-child) must
    appear in NO text. PASS iff recall >= ``min_recall`` and zero anti-facts fired."""
    hit = sum(1 for g in gold_facts if any(fact_present(t, g) for t in texts))
    bad = sum(1 for a in anti_facts if any(fact_present(t, a) for t in texts))
    recall = (hit / len(gold_facts)) if gold_facts else 1.0
    ok = recall >= min_recall and bad == 0
    return Score(ok, "" if ok else "write", {
        "gold": len(gold_facts), "gold_found": hit, "anti": len(anti_facts), "anti_hits": bad,
        "recall": round(recall, 4)})
