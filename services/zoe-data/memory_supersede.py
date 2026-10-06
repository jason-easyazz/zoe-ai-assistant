"""Implicit supersession: a stated change of state retires the fact it replaces.

Context-manager gap #4 (docs/research/samantha-context-engineering-2026-09-29.md §2
pattern 3, §4 item 4; STALE, arXiv 2605.06527). "Change of plan: I've dropped the
half-marathon. I'm doing a 10k in May instead." used to store two NEW rows and leave
"training for their first half-marathon in March" approved: the shared reconciler
(``memory_quality.classify_against_existing``) only supersedes a same-ATTRIBUTE
"my X is Y" correction or a >=0.92 near-duplicate, and neither describes this turn.

Flag-dark ``ZOE_MEMORY_IMPLICIT_SUPERSEDE`` (default OFF, read per call). Off, no caller
reaches this module's write paths. Deterministic: a cue table, a content-token topic
match, a subject guard and one exclusive slot (home). No model call.

* Write time (``memory_digest.run_turn_digest``): a fact that carries a cue, from an
  utterance that carries one, retires the user's older approved facts on the same topic.
  A fact that says something ENDED ("User dropped the half-marathon") is a tombstone:
  it is stored as ``memory_type="state_change"`` so the card never lists it as current.
* Nightly (``memory_digest._implicit_conflict_pass`` in the dreaming cycle): the same
  rule over stored pairs, plus a differing home-slot value, capped per user per run.

The old row is never deleted: ``status=superseded``, ``superseded_by_id``, ``invalid_at``
(epoch seconds: where the successor's ``valid_from`` begins) and ``expired_at``; the successor
gets ``supersedes_id`` and ``valid_from`` (``memory_temporal``: the stated event time, else
its ``added_ts``) via ``MemoryService.supersede_by``. Ordinary reads hide ``superseded``
(``memory_service._BLOCKED_READ_STATUSES``); a history question ("where did I live before?")
and ``search(as_of=...)`` read them.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

from user_model_card import ALLOWED_TYPES, STATE_CHANGE

ENV = "ZOE_MEMORY_IMPLICIT_SUPERSEDE"
ACTOR = "implicit_supersede"
MAX_PER_FACT = 3          # write time: older rows one new fact may retire
NIGHTLY_CAP = 10          # nightly: rows retired per user per run
SCAN_LIMIT = 2000         # approved rows read per user (list_by_status slices after reading)
# Topic match (see same_topic): the change must share at least half of the SMALLER topic
# (the containment rule memory_digest._loop_is_dup applies at 0.6 to paraphrases; lower
# here because a change usually names only the object: "dropped the half-marathon") AND
# name at least half of the OLD fact's topic, so one shared word cannot retire a longer
# fact that is mostly about something else.
MIN_CONTAINMENT = 0.5
MIN_OLD_COVERAGE = 0.5
# Rows a change may retire: the person-fact types the card lists. Emotional moments,
# notes, open loops, insights and earlier tombstones (STATE_CHANGE) are left alone.
TARGET_TYPES = ALLOWED_TYPES


def enabled() -> bool:
    """Per-call env read through the canonical bool parse (default OFF)."""
    from typed_env import env_bool

    return env_bool("ZOE_MEMORY_IMPLICIT_SUPERSEDE", False)


@dataclass(frozen=True)
class Cue:
    name: str
    kind: str                 # "end" = something stopped; "swap" = something replaced it
    pattern: re.Pattern[str]
    fact_level: bool = True   # False = utterance-only ("change of plan" never reaches a fact)


def _c(name: str, kind: str, rx: str, fact_level: bool = True) -> Cue:
    return Cue(name, kind, re.compile(rx, re.I), fact_level)


# The cue table. Negative lookarounds carry the everyday non-change senses, each pinned
# by a test: "dropped my keys / the kids off", "stopped at the shops", "got used to it".
CUES: tuple[Cue, ...] = (
    _c("change of plan", "swap", r"\bchange of plans?\b", fact_level=False),
    _c("dropped", "end", r"\bdropped\b(?!\s+(?:(?:my|the|his|her|their|a|an)\s+)?"
       r"(?:keys?|phone|wallet|glass|cup|plate|bag|ball|kids?|children|son|daughter|"
       r"off|by|in|round|over)\b)"),
    _c("no longer", "end", r"\bno longer\b"),
    _c("not anymore", "end", r"\b(?:not|n't|never)\b[^.!?]{0,40}?\bany ?more\b"),
    _c("stopped", "end", r"\bstopped\b(?!\s+(?:at|by|in|for|off|over|to)\b)"),
    _c("quit", "end", r"\bquit\b"),
    _c("gave up", "end", r"\bg(?:ave|iven) up\b"),
    _c("cancelled", "end", r"\bcancel(?:l)?ed\b"),
    _c("used to", "end", r"(?<!\bget )(?<!\bgot )(?<!\bam )(?<!\bis )(?<!\bare )(?<!\bbe )"
       r"(?<!'m )(?<!\bwas )(?<!\bbeen )\bused to\b"),
    _c("moved from", "swap", r"\bmoved (?:away )?from\b"),
    _c("moved to", "swap", r"\bmoved (?:\w+ )?to\b"),
    _c("instead", "swap", r"\binstead\b"),
    _c("switched to", "swap", r"\bswitched (?:over )?to\b"),
    _c("changed to", "swap", r"\bchanged (?:it |that |this )?to\b"),
    _c("rather than", "swap", r"\bnow\b[^.!?]{0,60}?\brather than\b"),
    # A CORRECTION is a change of state with no change verb: "Actually, I got that
    # wrong — my mum lives in Bendigo, not Ballarat." Utterance-only: the new fact
    # ("User's mum lives in Bendigo") carries no cue word, so it acts through the
    # same-topic / exclusive-slot match in memory_digest + supersede_for_turn
    # instead (measured 2026-10-04: the word-overlap dedup dropped the corrected
    # fact as a duplicate of the one it replaces, in every day-sim run).
    _c("correction", "swap",
       r"^\s*(?:actually|wait|sorry|no)\b[^.!?]{0,80}?\b(?:wrong|meant|mistake|"
       r"not(?!\s+(?:sure|really|yet|bad|much|quite|too|that|so|very|just|only|even)\b))\b"
       r"|\bi (?:got|had) (?:that|it) wrong\b|\bi was wrong\b|\bmy (?:mistake|bad)\b"
       r"|\bthat'?s (?:wrong|not right|incorrect)\b|\bi meant\b|\bcorrection\b",
       fact_level=False),
)
# "Tea instead of coffee this morning" is a one-off substitution, not a change of state.
_ONE_OFF = re.compile(r"\b(?:today|tonight|this (?:morning|afternoon|evening)|yesterday|"
                      r"just now|for (?:breakfast|lunch|dinner))\b", re.I)
_ONE_OFF_CUES = frozenset({"instead", "rather than"})


def utterance_cue(text: str) -> Optional[str]:
    """The first cue in the user's own words, or None."""
    for cue in CUES:
        if cue.pattern.search(text or ""):
            return cue.name
    return None


def fact_cue(text: str) -> Optional[Cue]:
    """The cue a stored fact itself carries (an 'end' cue wins over a 'swap'), or None."""
    hits = [c for c in CUES if c.fact_level and c.pattern.search(text or "")]
    hits = [c for c in hits if not (c.name in _ONE_OFF_CUES and _ONE_OFF.search(text or ""))]
    return next((c for c in hits if c.kind == "end"), hits[0] if hits else None)


def _utterance_swap(cue: Optional[str]) -> Optional[Cue]:
    """The acting cue for a change row whose text carries none: the utterance's own
    cue as a swap (the row replaces what it matched), or None when there was no cue."""
    if not cue:
        return None
    for c in CUES:
        if c.name == cue:
            return Cue(c.name, "swap", c.pattern, c.fact_level)
    return Cue(cue, "swap", re.compile(r"(?!x)x"), False)


# "used to <verb>" is a change of STATE only for the row that verb is about: "used to live in Perth" ends "lives in
# Perth"; "used to love Perth" is a reminiscence and ends nothing about where the person lives (the 2026-10-05
# fidelity audit's S4: a newer "used to love <city>" retired the current "lives in <city>"). The verb after the
# cue must be the old row's own (irregular verbs by their forms).
_USED_TO_VERB = re.compile(r"\bused to\s+(?:not\s+)?([a-z]+)", re.I)
_IRREGULAR_FORMS = dict(be=("is", "am", "are", "was", "were", "been"), have=("has", "have", "had"),
                        do=("does", "did"), go=("goes", "went"))


def cue_applies(cue_name: str, new: str, old: str) -> bool:
    """May the cue ``cue_name`` in ``new`` retire ``old``? Only "used to" is conditional (see above); a fact whose
    "used to" verb cannot be read keeps the cue's old behaviour."""
    if cue_name != "used to":
        return True
    m = _USED_TO_VERB.search(new or "")
    if not m:
        return True
    verb = m.group(1).lower()
    words = re.findall(r"[a-z']+", (old or "").lower())
    if verb in _IRREGULAR_FORMS:
        return any(w in _IRREGULAR_FORMS[verb] for w in words)
    stem = verb[:-1] if verb.endswith("e") else verb
    return any(w.startswith(stem) for w in words)


def changes_existing(fact: str, rows: Iterable[Any]) -> bool:
    """Does this cue-less fact replace an approved row (same topic, or the exclusive
    home slot)? Used by the turn digest so the word-overlap dedup cannot drop a
    corrected fact as a duplicate of the row it retires."""
    for old in rows:
        if not _is_target(getattr(old, "metadata", None) or {}):
            continue
        text = getattr(old, "text", "") or ""
        if exclusive_conflict(fact, text) or same_topic(fact, text):
            return True
    return False


def is_tombstone(text: str) -> bool:
    """True for a fact that says something ENDED ("User dropped the half-marathon")."""
    cue = fact_cue(text)
    return cue is not None and cue.kind == "end"


# ── Same-topic matcher ───────────────────────────────────────────────────────────
# Words that name the change or the verb frame, never the thing: a shared "doing" or
# "training" says nothing about whether two facts are about the same race.
_FRAME_WORDS = frozenset({
    "dropped", "longer", "anymore", "stopped", "quit", "gave", "given", "cancelled",
    "canceled", "used", "moved", "away", "instead", "switched", "changed", "rather",
    "change", "plan", "plans", "doing", "going", "training", "planning", "started",
    "starting", "first", "currently", "still", "every", "really", "does", "done",
    "want", "wants", "wanted", "some", "them", "then", "than", "also", "again",
    # frequency, not topic ("eats meat most days")
    "most", "days", "usually", "often", "always", "sometimes", "never", "much", "many",
    "more", "less", "lots",
})


def topic_tokens(text: str) -> set[str]:
    """Content tokens minus memory_digest's stopwords (which include time words) and the
    change/verb frame, plural 's' folded ("lives" = "live"). A token with a digit counts
    at any length ("10k", "5k")."""
    from memory_digest import _AFFECT_STOPWORDS

    out = set()
    for t in re.findall(r"[a-z0-9']+", (text or "").lower()):
        t = t.removesuffix("'s")
        if not (len(t) > 3 or (len(t) >= 2 and any(ch.isdigit() for ch in t))):
            continue
        if t in _AFFECT_STOPWORDS or t in _FRAME_WORDS:
            continue
        if len(t) > 4 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.add(t)
    return out


def _relations(text: str) -> frozenset[str]:
    from memory_gate import _EVT_REL

    return frozenset(m.lower().rstrip("s")
                     for m in re.findall(rf"\b{_EVT_REL}\b", text or "", re.I))


def subject_key(text: str) -> tuple[str, frozenset[str], frozenset[str]]:
    """(owner, relation words, possessive names). Two facts must share it: the user's
    home is not their sister's, and "Tom quit" says nothing about the user."""
    t = (text or "").strip()
    first = re.match(r"(?:the\s+)?([A-Za-z]+)", t, re.I)
    owner = first.group(1).lower() if first else ""
    owner = "user" if owner in {"user", "i", "my"} else owner
    poss = frozenset(w.lower() for w in re.findall(r"\b([A-Z][a-z]+)['’]s\b", t)
                     if w.lower() != "user")
    return owner, _relations(t), poss


def same_topic(new: str, old: str) -> bool:
    """Deterministic same-topic test (thresholds above; no model)."""
    if subject_key(new) != subject_key(old):
        return False
    a, b = topic_tokens(new), topic_tokens(old)
    shared = a & b
    if not shared:
        return False
    return (len(shared) / min(len(a), len(b)) >= MIN_CONTAINMENT
            and len(shared) / len(b) >= MIN_OLD_COVERAGE)


# The one mutually exclusive slot: where the user lives. A person has one home, so a
# newer different value retires the older one ("moved to Perth" vs "lives in Geraldton").
_PLACE = r"[A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)*"
_HOME = re.compile(rf"\b(?:lives?|living|based|settled)\s+in\s+(?P<a>{_PLACE})"
                   rf"|\bmoved\s+(?:\w+\s+)?to\s+(?P<b>{_PLACE})")


def home_value(text: str) -> Optional[str]:
    """The current home a fact asserts, or None (a past or negated home is not one)."""
    if is_tombstone(text):
        return None
    m = _HOME.search(text or "")
    if not m:
        return None
    return (m.group("a") or m.group("b") or "").strip().lower() or None


def exclusive_conflict(new: str, old: str) -> bool:
    nv, ov = home_value(new), home_value(old)
    return bool(nv and ov and nv != ov and subject_key(new) == subject_key(old))


def _is_target(meta: dict[str, Any]) -> bool:
    tags = str(meta.get("tags") or "").split(",")
    return (str(meta.get("status") or "") == "approved"
            and str(meta.get("memory_type") or "fact") in TARGET_TYPES
            and STATE_CHANGE not in tags)


# ── Write time ──────────────────────────────────────────────────────────────────

async def supersede_for_turn(svc, user_id: str, cue: str, written: Iterable[Any]) -> dict:
    """Retire older same-topic facts for the rows ONE turn just wrote.

    ``written`` are the MemoryRefs the turn stored; only rows whose own text carries a
    cue act. A tombstone's older row is linked to the turn's replacement when the turn
    wrote exactly one swap-cue fact ("…a 10k in May instead"), else to the tombstone.
    Never raises; returns ``{"superseded": n, "new": id8 or ""}``."""
    out: dict[str, Any] = {"superseded": 0, "new": ""}
    try:
        refs = [r for r in written if r is not None and getattr(r, "id", None)]
        # A written row acts through its OWN cue word, or — when the turn's utterance
        # carried a cue — through the topic / exclusive-slot match that admitted it
        # (``changes_existing`` in memory_digest): a corrected fact has no cue word.
        by_utterance = _utterance_swap(cue)
        acting = [(r, fact_cue(r.text or "") or by_utterance) for r in refs]
        acting = [(r, c) for r, c in acting if c is not None]
        if not acting:
            return out
        swaps = [r for r, c in acting if c.kind == "swap"]
        replacement = swaps[0] if len(swaps) == 1 else None
        own = {r.id for r in refs}
        rows = await svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT)
        taken: set[str] = set()
        retired: list[tuple[str, str]] = []  # (old, successor) texts, for the open loops
        for ref, c in sorted(acting, key=lambda rc: rc[1].kind != "end"):
            by = replacement if (c.kind == "end" and replacement is not None) else ref
            hits = 0
            for old in rows:
                if hits >= MAX_PER_FACT:
                    break
                if old.id in own or old.id in taken or not _is_target(old.metadata or {}):
                    continue
                if not (same_topic(ref.text, old.text) or exclusive_conflict(ref.text, old.text)):
                    continue
                if not cue_applies(c.name, ref.text, old.text):
                    continue
                if await svc.supersede_by(user_id, old.id, by.id, actor=ACTOR,
                                          note=f"implicit change ({c.name})"):
                    taken.add(old.id)
                    retired.append((old.text, by.text))
                    hits += 1
                    out["new"] = out["new"] or str(by.id)[:8]
        out["superseded"] = len(taken)
        if taken:
            logger.info("MEMORY_SUPERSEDE user=%s cue=%s superseded=%d new=%s",
                        user_id, cue, len(taken), out["new"])
            from open_loop_lifecycle import resolve_for_supersede

            await resolve_for_supersede(user_id, retired, ended=True, source="turn")
    except Exception as exc:  # the write path must never fail on this
        logger.warning("implicit supersede failed user=%s: %s", user_id, type(exc).__name__)
    return out


# ── Nightly ─────────────────────────────────────────────────────────────────────

def conflict_pairs(rows: list[Any]) -> list[tuple[Any, Any, str]]:
    """Pure: ``(newer, older, reason)`` for every approved pair where the newer row
    carries a change cue on the older row's topic, or asserts a different home.
    Newest first; an older row appears once (paired with its newest retirer)."""
    live = sorted((r for r in rows if str((r.metadata or {}).get("status")) == "approved"),
                  key=lambda r: (str((r.metadata or {}).get("added_at") or ""), r.id),
                  reverse=True)
    pairs: list[tuple[Any, Any, str]] = []
    retired: set[str] = set()
    for i, newer in enumerate(live):
        if newer.id in retired:
            continue
        cue = fact_cue(newer.text or "")
        if cue is None and home_value(newer.text or "") is None:
            continue
        for older in live[i + 1:]:
            if older.id in retired or not _is_target(older.metadata or {}):
                continue
            if cue is not None and same_topic(newer.text, older.text) and cue_applies(cue.name, newer.text, older.text):
                reason = cue.name
            elif exclusive_conflict(newer.text, older.text):
                reason = "home"
            else:
                continue
            pairs.append((newer, older, reason))
            retired.add(older.id)
    return pairs


async def nightly_conflict_pass(svc, user_id: str, *, cap: int = NIGHTLY_CAP,
                                dry_run: bool = False) -> dict:
    """Supersede implicit conflicts in one user's approved facts, at most ``cap``.
    Idempotent: a retired row is no longer approved, so a second run finds nothing.
    ``dry_run`` counts pairs and writes nothing (the operator's first run)."""
    rows = await svc.list_by_status(user_id=user_id, status="approved", limit=SCAN_LIMIT)
    pairs = conflict_pairs(rows)
    done = 0
    retired: list[tuple[str, str]] = []
    for newer, older, reason in pairs:
        if done >= cap or dry_run:
            break
        if await svc.supersede_by(user_id, older.id, newer.id, actor=ACTOR,
                                  note=f"nightly implicit conflict ({reason})"):
            done += 1
            retired.append((older.text, newer.text))
    if retired:
        from open_loop_lifecycle import resolve_for_supersede

        await resolve_for_supersede(user_id, retired, ended=True, source="nightly")
    logger.info("MEMORY_CONFLICT_PASS user=%s pairs=%d superseded=%d%s", user_id,
                len(pairs), done, " dry_run=1" if dry_run else "")
    return {"pairs": len(pairs), "superseded": done}
