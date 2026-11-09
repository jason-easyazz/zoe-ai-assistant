"""CLEAN READ: a DISPLAY layer over the pendant's verbatim transcript (owner, 2026-11-09). Measured and approved.

What it is: each speaker turn gets a lightly cleaned twin - fillers, false starts and stutters out, punctuation and capitals tidied - stored BESIDE the verbatim
(``omi_segments.clean_text`` / ``clean_how``). Memory, extraction, the day review, the contender ledger and every search read the VERBATIM ``text``; this twin is
for a person reading the transcript (``omi_transcript_book.py``), nothing else.

How a turn is cleaned (one LLM request per turn, T=0, the 12B at night, the 4B only if the 12B stage was skipped):
  * under ``SHORT_WORDS`` (6) words: the deterministic cleaner only - no model call;
  * otherwise the model; its answer must pass the CODE GUARD (``guard``): the clean words are an in-order SUBSEQUENCE of the verbatim words (case, punctuation,
    apostrophes and spoken-number-vs-digit spelling ignored), at most 15% of the verbatim words dropped, and no dropped run longer than 4 words. A model that
    rewrites, reorders, paraphrases or summarises fails the subsequence test; one that deletes a clause fails the run test; one that over-trims fails the 15%.
  * a violation -> the deterministic cleaner (itself guarded) -> the verbatim, shown marked "(lightly cleaned)". A turn is NEVER shown as the model's words unless
    the guard passed.

``clean_how``: ``''`` not yet cleaned | ``llm`` | ``det`` (short turn, by design) | ``det_fb`` (model failed the guard / was unreachable) | ``raw_fb`` (nothing safe).
The nightly counts (``summary``) carry the violation rate; above ``ALERT_RATE`` (20%) the job note says ALERT and the morning report prints it.

SEAM (not built): ``Suggestion`` / ``suggest_hook`` - a whole-conversation correction pass (what the turns around it say about a garbled word) would hand its
proposals to the morning check as "did you mean ...?" items. Nothing calls it today; the clean read never changes a word's meaning on its own.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

import omi_panel_anchor as pa

SHORT_WORDS = 6
MAX_DROP_FRAC = 0.15
MAX_DROP_RUN = 4
ALERT_RATE = 0.20
FILLERS = frozenset({"um", "umm", "uh", "uhh", "ah", "er", "erm", "hmm", "hm", "mm", "mmm"})
MAX_TOKENS_FACTOR = 2

SYSTEM = ("You tidy a spoken transcript for reading. Remove filler sounds (um, uh, er), false starts and stuttered repeats, and fix capitals and punctuation. "
          "Do NOT add words, change words, reorder, paraphrase, summarise or answer. Keep every other word exactly as spoken, in the same order. "
          "Reply with the tidied text only.")

_APOS = re.compile(r"['’]")
_PUNCT = re.compile(r"[^a-z0-9 ]")


def keys(text: str) -> list:
    """Comparable word keys: lowercase, apostrophes and punctuation removed, spoken numbers as digits."""
    return pa.words_of(_APOS.sub("", (text or "").lower().replace("-", " ")))


# ── the guard ────────────────────────────────────────────────────────────────────────────────────

@dataclass
class Verdict:
    ok: bool
    reason: str = ""
    dropped: int = 0
    frac: float = 0.0
    run: int = 0


def guard(verbatim: str, clean: str, *, max_frac: float = MAX_DROP_FRAC, max_run: int = MAX_DROP_RUN) -> Verdict:
    """May ``clean`` be shown for ``verbatim``? In-order subsequence, <= ``max_frac`` of the words dropped, no dropped run > ``max_run``. Pure."""
    v, c = keys(verbatim), keys(clean)
    if not v:
        return Verdict(not c, "" if not c else "invented_words")
    if not c:
        return Verdict(False, "empty", len(v), 1.0, len(v))
    i, run, longest = 0, 0, 0
    for w in c:
        j = i
        while j < len(v) and v[j] != w:
            j += 1
        if j == len(v):
            return Verdict(False, "not_subsequence", len(v) - len(c))
        run = j - i
        longest = max(longest, run)
        i = j + 1
    longest = max(longest, len(v) - i)                   # the tail the clean text dropped
    dropped = len(v) - len(c)
    frac = dropped / len(v)
    if frac > max_frac:
        return Verdict(False, "too_many_dropped", dropped, frac, longest)
    if longest > max_run:
        return Verdict(False, "long_run_dropped", dropped, frac, longest)
    return Verdict(True, "", dropped, frac, longest)


# ── the deterministic cleaner ────────────────────────────────────────────────────────────────────

def det_ok(verbatim: str, det: str) -> bool:
    """The deterministic cleaner's output is trusted for WHAT it drops (fillers and stutters, by construction, however many), but it must still be an in-order
    subsequence of the verbatim and not empty - the same invariant, without the model's drop limits."""
    return guard(verbatim, det, max_frac=1.0, max_run=10 ** 6).ok


def _k(tok: str) -> str:
    return "".join(keys(tok)) if tok.strip() else ""


def deterministic(text: str) -> str:
    """Fillers (um/uh/ah/er/hmm/mm) out; an immediate 1-3 word repeat collapsed ("I I think" -> "I think"); "like" out only when it opens a sentence or is
    followed by a comma. Everything else is kept as spoken; the first word is re-capitalised if the first was dropped."""
    toks = (text or "").split()
    out: list = []
    for idx, t in enumerate(toks):
        k = _k(t)
        if k in FILLERS:
            continue
        if k == "like":
            starts = not out or out[-1][-1:] in ".!?"
            if starts or t.rstrip().endswith(",") or t.lower().rstrip(".!?").endswith(","):
                continue
        out.append(t)
    changed = True
    while changed:                                                 # collapse immediate repeats of 1-3 word groups, longest first
        changed = False
        for n in (3, 2, 1):
            i = 0
            while i + 2 * n <= len(out):
                a = [_k(x) for x in out[i:i + n]]
                if all(a) and a == [_k(x) for x in out[i + n:i + 2 * n]]:
                    del out[i:i + n]
                    changed = True
                else:
                    i += 1
    s = " ".join(out).strip()
    s = re.sub(r"\s+([,.!?])", r"\1", s)
    if s and s[0].islower() and toks and (toks[0][:1].isupper() or _k(toks[0]) in FILLERS):
        s = s[0].upper() + s[1:]                                   # the opening word was dropped: the next one opens the sentence
    return s


# ── one turn ─────────────────────────────────────────────────────────────────────────────────────

def request(text: str, model: str = "") -> dict:
    """The chat request for one turn: T=0, a token cap that fits the turn."""
    n = len(text.split())
    return {"model": model, "temperature": 0, "max_tokens": MAX_TOKENS_FACTOR * n + 48, "stream": False,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": text}]}


def _scrub(reply: str) -> str:
    r = (reply or "").strip()
    r = re.sub(r"^```[a-z]*\s*|\s*```$", "", r).strip()
    if len(r) >= 2 and r[0] == r[-1] and r[0] in "\"'":
        r = r[1:-1].strip()
    return r


async def clean_turn(text: str, llm: Optional[Callable[[str], Any]]) -> dict:
    """-> {text, how, violation (bool), reason}. ``llm(text)`` is async or sync, returns the model's reply or raises / returns None when unreachable."""
    words = len(text.split())
    if words < SHORT_WORDS or llm is None:
        det = deterministic(text)
        if det_ok(text, det):
            return {"text": det, "how": "det" if words < SHORT_WORDS else "det_fb", "violation": False, "reason": "" if words < SHORT_WORDS else "no_model"}
        return {"text": text, "how": "raw_fb", "violation": False, "reason": "empty"}
    reply, why = None, ""
    try:
        out = llm(text)
        if asyncio.iscoroutine(out):
            out = await out
        reply = _scrub(str(out)) if out is not None else None
    except Exception as exc:  # noqa: BLE001 - unreachable / timeout: not a guard violation, counted separately
        why = type(exc).__name__
    if reply is not None:
        g = guard(text, reply)
        if g.ok:
            return {"text": reply, "how": "llm", "violation": False, "reason": ""}
        why, violation = g.reason, True
    else:
        why, violation = why or "no_reply", False
    det = deterministic(text)
    if det_ok(text, det):
        return {"text": det, "how": "det_fb", "violation": violation, "reason": why}
    return {"text": text, "how": "raw_fb", "violation": violation, "reason": why}


def marked(how: str) -> bool:
    """Show "(lightly cleaned)" beside this turn: the model could not be trusted for it."""
    return how in ("det_fb", "raw_fb")


# ── the night's numbers ──────────────────────────────────────────────────────────────────────────

def summary(results: list) -> dict:
    """Counts only. ``violation_rate`` = guard violations / turns sent to the model (unreachable model calls are not violations)."""
    sent = sum(1 for r in results if r["how"] == "llm" or r["violation"])
    viol = sum(1 for r in results if r["violation"])
    by_how: dict = {}
    for r in results:
        by_how[r["how"]] = by_how.get(r["how"], 0) + 1
    rate = viol / sent if sent else 0.0
    return {"turns": len(results), "llm": by_how.get("llm", 0), "short": by_how.get("det", 0), "violations": viol, "sent": sent, "unreachable": sum(1 for r in results if not r["violation"] and r["how"] in ("det_fb", "raw_fb") and r["reason"] not in ("", "no_model")),
            "violation_rate": round(rate, 4), "alert": bool(sent >= 10 and rate > ALERT_RATE)}


# ── future seam: suggestions into the morning check (nothing calls it today) ─────────────────────

@dataclass
class Suggestion:
    """A proposed correction of a garbled word, to be ASKED not applied: ``segment_id`` / ``heard`` / ``maybe`` / ``why``. The morning check's
    ``transcript`` item kind is where the owner would answer it."""
    segment_id: str
    heard: str
    maybe: str
    why: str = ""


suggest_hook: Optional[Callable[[str, list], Any]] = None      # (recording_id, turns) -> [Suggestion]; unset: no correction pass exists


# ── storage glue ─────────────────────────────────────────────────────────────────────────────────

def llm_from_url(url: str, model: str = "", *, timeout_s: float = 90.0) -> Callable[[str], Any]:
    """An async ``llm(text)`` against a llama-server's ``/v1/chat/completions`` (loopback). Raises on a transport error; returns None on an empty body."""
    import httpx

    async def call(text: str) -> Optional[str]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s, connect=8.0)) as client:
            resp = await client.post(url.rstrip("/") + "/chat/completions" if url.rstrip("/").endswith("/v1") else url.rstrip("/") + "/v1/chat/completions",
                                     json=request(text, model))
            resp.raise_for_status()
            return str(resp.json()["choices"][0]["message"]["content"] or "")
    return call


async def _all(db, sql: str, params: tuple = ()) -> list:
    cur = await db.execute(sql, params)
    return list(await cur.fetchall())


async def pending_segments(db, limit: int = 2000) -> list:
    """(id, user_id, text, not_person) of uncleaned segments of DONE recordings, oldest first. Zoe's own lines are listed too (they are cheap: no model)."""
    try:
        rows = await _all(db, "SELECT s.id, s.user_id, s.text, s.not_person FROM omi_segments s JOIN omi_recordings r ON r.id = s.recording_id "
                              "WHERE r.status = 'done' AND s.clean_how = '' AND s.text != '' ORDER BY r.started_at, s.seq LIMIT ?", (limit,))
    except Exception as exc:  # noqa: BLE001
        if type(exc).__name__ == "UndefinedColumnError" or "no such column" in str(exc).lower():
            return []                                          # pre-0073: nothing to do
        raise
    return [tuple(r) for r in rows]


async def run(*, llm: Optional[Callable[[str], Any]], budget_s: float = 0.0, now: Callable[[], float] = time.monotonic, limit: int = 2000,
              db_factory: Optional[Callable[[], Any]] = None) -> dict:
    """Clean every pending segment inside ``budget_s`` (0 = unbounded). Each write is ordered against the erase gate and the forget ledger, and lands only while
    the verbatim is unchanged. Returns the counts-only summary (+ ``status``)."""
    import omi_recordings as omi
    import user_erase_gate as erase_gate
    if db_factory is None:
        from db_compat import get_compat_db as db_factory
    t0 = now()
    seen = erase_gate.snapshot()
    results: list = []
    stop = ""
    async with db_factory() as db:
        todo = await pending_segments(db, limit)
    for sid, uid, text, not_person in todo:
        if budget_s and now() - t0 >= budget_s:
            stop = "no_time"
            break
        r = await clean_turn(text, None if not_person else llm)         # Zoe's own words are already clean: never a model call
        results.append(r)
        async with erase_gate.writing(uid, seen.get(uid, 0)) as live:
            if not live:
                continue
            async with db_factory() as db:
                shown = await omi._redact_forgotten(uid, r["text"])
                await db.execute("UPDATE omi_segments SET clean_text = ?, clean_how = ? WHERE id = ? AND user_id = ? AND text = ?", (shown, r["how"], sid, uid, text))
                await db.commit()
    out = summary(results)
    out["status"] = stop or ("nothing_to_do" if not todo else "ok")
    out["left"] = max(0, len(todo) - len(results))
    return out
