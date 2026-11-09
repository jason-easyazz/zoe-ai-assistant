"""The MORNING TRANSCRIPT CHECK: the words the night re-pass could not settle, put to the owner in the morning, on a turn HE started.

Flag ``ZOE_TRANSCRIPT_CHECK`` = off | shadow | on (default OFF). Spec and owner acceptance: ``docs/knowledge/transcript-check.md``.

  off     nothing runs; every hook returns before any I/O.
  shadow  the night half builds the queue and logs COUNTS ("would ask 7: 3 names, 2 numbers ...", never a word) and stores the items
          as ``shadow`` rows: nothing is spoken, nothing is shown, no audio is held for them.
  on      the queue is real: audio is held for each open item (inside the existing retention), the owner's first verified-voice turn
          after 06:00 carries ONE appended line, and the panel "Transcript check" screen serves the items.

Two halves with one table (``omi_check_items``, migration 0073) between them:

  NIGHT   ``candidates_from_chunk`` turns the ROVER fusion's per-chunk output (contested slots, ``[low confidence]`` chunks) into
          candidates; ``enqueue`` ranks them (names, numbers/times/dates, commitments, anything feeding a day-review candidate first;
          chit-chat last), keeps the top ``max_items()`` per day, and stores them in the owner's private layer.
  MORNING ``note_voice_turn`` / ``append_stream`` / ``tier`` carry the spoken ask; ``list_open`` / ``answer`` / ``skip`` serve the panel.

Invariants (each has a negative control in ``tests/test_transcript_check.py``):
  * NEVER UNPROMPTED (owner W2, ``ZOE_PROACTIVE_SPOKEN=0``): the ask is appended to the reply of a turn the owner started. No announce
    path, no timer, no speech of its own. Once a household day (a PRIMARY KEY is the lock), only on a turn whose speaker was VERIFIED
    (never a guest, an unverified voice, a typed turn or a PIN-only turn), only after 06:00, only after the command was answered with
    real text, never on an emotional turn, never when the queue holds nothing the owner may see.
  * OWNER ONLY: items are served to the member whose recording they came from, on a voice session that is still open for them or their
    own signed-in session. A guest panel gets 401/403 and no counts.
  * FORGOTTEN STAYS FORGOTTEN: a span that names a forgotten entity is deleted instead of shown; the answer is redacted too.
  * An answer corrects the stored text with provenance ``owner_corrected``, is ground truth for the per-model contender ledger, and
    re-opens the day review of any recording whose pending candidates it may have fed.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator, Optional

import user_erase_gate as erase_gate
from typed_env import env_int, env_str

logger = logging.getLogger(__name__)

ENV = "ZOE_TRANSCRIPT_CHECK"
MODES = ("off", "shadow", "on")
TIER = "transcript_check"
DEFAULT_MAX_ITEMS = 10
MAX_CHUNK_ITEMS = 3                 # low-confidence whole-chunk items are the weakest evidence: they never fill the queue
CONTEXT_WORDS = 9
SNIPPET_PAD_MS = 1200
SNIPPET_MAX_MS = 8000
ASK_FROM_HOUR = 6
ASK_TTL_S = 120.0                   # a "yes" answers the ask only this soon after it was spoken
LISTEN_TTL_S = 45.0                 # "say it": how long Zoe waits for the word
TURN_TTL_S = 180.0
LOW_CONFIDENCE_MARK = " [low confidence]"
NONE_WORD = "(nothing)"
ASK_LINE = "Hey {name}, I had a few issues with last night's transcripts — can you help when you've got a moment?"
ASK_LINE_NONAME = "Hey, I had a few issues with last night's transcripts — can you help when you've got a moment?"
OPENED_REPLY = "Okay, it's on the screen."
LATER_REPLY = "No worries, I'll leave it in your notifications."
HEARD_REPLY = "Got it."
PRIVATE_REPLY = "That's private. Say it again so I know it's you, or unlock with your PIN."

IMPORTANCE_SCORE = {"name": 100.0, "number": 90.0, "commitment": 70.0, "chat": 10.0}
FEEDS_BONUS = 40.0

_UTC = timezone.utc


# ---------------------------------------------------------------------------------------------------------------------- flags
def mode() -> str:
    m = env_str(ENV, "off").strip().lower()
    return m if m in MODES else "off"


def enabled() -> bool:
    return mode() != "off"


def live() -> bool:
    return mode() == "on"


def max_items() -> int:
    return max(1, min(40, env_int("ZOE_TRANSCRIPT_CHECK_MAX_ITEMS", DEFAULT_MAX_ITEMS)))


def ask_from_hour() -> int:
    return max(0, min(23, env_int("ZOE_TRANSCRIPT_CHECK_ASK_FROM_HOUR", ASK_FROM_HOUR)))


def _now() -> datetime:
    return datetime.now(_UTC)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(raw: Any) -> Optional[datetime]:
    try:
        s = str(raw or "").strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=_UTC)
    except ValueError:
        return None


def _local(dt: Optional[datetime] = None) -> datetime:
    import omi_day_review as dr
    return (dt or _now()).astimezone(dr._tz())


def _today() -> str:
    return _local().date().isoformat()


async def _fetch(db, sql: str, params: tuple = ()) -> list:
    cur = await db.execute(sql, params)
    return list(await cur.fetchall())


def _db():
    from db_compat import get_compat_db
    return get_compat_db()


def _missing(exc: Exception) -> bool:
    """The table is not migrated (a box that has not run 0073) or there is no pool in this process: nothing to read or erase."""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(s in text for s in ("no such table", "does not exist", "undefinedtable", "not initialised", "no such column"))


# ----------------------------------------------------------------------------------------------------------------- importance
_NUM_WORDS = frozenset("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen "
                       "nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred thousand million first second third fourth fifth "
                       "sixth seventh eighth ninth tenth noon midnight".split())
_DATE_WORDS = frozenset("monday tuesday wednesday thursday friday saturday sunday january february march april may june july august september "
                        "october november december tomorrow tonight yesterday morning afternoon evening o'clock oclock".split())
_DIGITS = re.compile(r"\d")
_CAP_FUNCTION = frozenset("i i'm i'll i've i'd a an the you your we but and so or if it its it's he she they alright okay ok yeah yes no oh well now then".split())
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9'\-]*")


def _words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9']", "", (w or "").lower())


def classify(before: str, span: str, after: str, alts: list, names: Optional[set] = None, kind: str = "slot") -> str:
    """name | number | commitment | chat. Pure. A span is a NAME when any alternative is a household name or is capitalised mid-sentence; a
    NUMBER when it holds a digit, a number word, a weekday/month or a clock word; a COMMITMENT when its sentence reads as one."""
    names = names or set()
    cands = [span] + [str(a.get("word") or "") for a in alts if isinstance(a, dict)]
    toks = [t for c in cands for t in _words(c)]
    if any(_norm(t) in names for t in toks):
        return "name"
    lead = _words(before)
    mid = bool(lead) and not before.rstrip().endswith((".", "?", "!"))
    for c in cands if (kind == "slot" and mid) else ():         # a capital MID-sentence is a name; a whole low-confidence chunk always starts with one
        ws = _words(c)
        if ws and len(ws) <= 2 and ws[0][:1].isupper() and _norm(ws[0]) not in _CAP_FUNCTION:
            return "name"
    if any(_DIGITS.search(t) or _norm(t) in _NUM_WORDS or _norm(t) in _DATE_WORDS or _norm(t) in {"am", "pm"} for t in toks):
        return "number"
    try:
        import omi_day_review as dr
        sentence = f"{before} {span} {after}"
        if dr._COMMIT.search(sentence) or dr._TASK.search(sentence) or dr._INTENT.search(sentence):
            return "commitment"
    except Exception:  # noqa: BLE001 - ranking never fails an enqueue
        pass
    return "chat"


def _margin_bonus(alts: list) -> float:
    """0..9: a closer vote is a likelier error. Only tie-breaks inside one importance class."""
    votes = sorted((int(a.get("votes") or 0) for a in alts if isinstance(a, dict)), reverse=True)
    if len(votes) < 2 or not votes[0]:
        return 0.0
    return round(9.0 * (votes[1] / votes[0]), 2)


def score(importance: str, alts: list, feeds_review: bool) -> float:
    return IMPORTANCE_SCORE.get(importance, 10.0) + (FEEDS_BONUS if feeds_review else 0.0) + _margin_bonus(alts)


# --------------------------------------------------------------------------------------------------------------- night: input
def _models_with(word: Optional[str], hyps: dict) -> list[str]:
    """Which models' chunk text contains ``word`` (approximate attribution; an exact per-slot ``by_model`` from the fusion wins)."""
    if word is None:
        return []
    w = _norm(word)
    return sorted(m for m, t in (hyps or {}).items() if w and w in {_norm(x) for x in str(t or "").split()})


def candidates_from_chunk(chunk: dict) -> list[dict]:
    """ROVER's per-chunk output -> candidates. ``chunk``: ``t0_ms``/``t1_ms`` (chunk bounds in the recording), ``tokens`` (the fused
    ``[(word, surface)]`` list ``rover()`` returns), ``slots`` (its contested slots ``{pos, best, cands: {word|None: votes}, surf, n}``,
    optional per-slot ``by_model {model: word}``), ``hyps`` ``{model: text}``, ``mode`` ``rover`` | ``lowconf``. Pure; no I/O."""
    t0, t1 = int(chunk.get("t0_ms") or 0), int(chunk.get("t1_ms") or 0)
    hyps = chunk.get("hyps") or {}
    out: list[dict] = []
    if chunk.get("mode") == "lowconf":
        base = str((hyps.get("qwen3") or chunk.get("text") or "")).replace(LOW_CONFIDENCE_MARK, "").strip()
        if not base:
            return out
        alts: dict[str, list] = {}
        for m, t in hyps.items():
            t = str(t or "").strip()
            if t:
                alts.setdefault(t, []).append(m)
        out.append({"kind": "chunk", "start_ms": t0, "end_ms": min(t1, t0 + SNIPPET_MAX_MS), "before": "", "span": base, "after": "",
                    "pick": base, "alts": [{"word": t, "votes": len(ms), "models": sorted(ms)} for t, ms in alts.items()]})
        return out
    tokens = list(chunk.get("tokens") or [])
    n = max(1, len(tokens))
    for slot in chunk.get("slots") or []:
        pos = int(slot.get("pos") or 0)
        surf = slot.get("surf") or {}
        by_model = slot.get("by_model") or {}
        alts = []
        for cand, votes in (slot.get("cands") or {}).items():
            label = NONE_WORD if cand is None or cand == "None" else str(surf.get(cand) or cand)
            ms = sorted(m for m, w in by_model.items() if (w is None) == (cand is None) and _norm(str(w or "")) == _norm(str(cand or ""))) \
                if by_model else _models_with(cand, hyps)
            alts.append({"word": label, "votes": int(votes), "models": ms})
        if len(alts) < 2:
            continue
        best = slot.get("best")
        pick = NONE_WORD if best is None else str(surf.get(best) or best)
        before = " ".join(s for _, s in tokens[max(0, pos - CONTEXT_WORDS):pos])
        after = " ".join(s for _, s in tokens[pos + (1 if best is not None else 0):pos + (1 if best is not None else 0) + CONTEXT_WORDS])
        mid = t0 + int((pos / n) * max(0, t1 - t0))
        out.append({"kind": "slot", "start_ms": max(t0, mid - SNIPPET_PAD_MS), "end_ms": min(max(t1, t0 + 1), mid + SNIPPET_PAD_MS),
                    "before": before, "span": "" if best is None else pick, "after": after, "pick": pick, "alts": alts})
    return out


# --------------------------------------------------------------------------------------------------------------- night: queue
async def _feeds_review(db, user_id: str, rid: str) -> bool:
    """Does a PENDING day-review candidate cite this recording? (Its words may be about to be written somewhere.)"""
    try:
        rows = await _fetch(db, "SELECT recording_ids FROM omi_day_candidates WHERE user_id = ? AND status = 'pending' AND recording_ids LIKE ?",
                            (user_id, f'%"{rid}"%'))
        return bool(rows)
    except Exception as exc:  # noqa: BLE001
        if _missing(exc):
            return False
        raise


async def _forgotten(user_id: str, text: str) -> bool:
    if not text.strip():
        return False
    import memory_forgotten as mf
    return bool(await mf.spans(user_id, text, near=False, strict=True))


def _shadow_log(rec: dict) -> None:
    """One counts-only line: what the queue WOULD have asked. No word, no name."""
    logger.info("TRANSCRIPT_CHECK_SHADOW %s", json.dumps(rec, sort_keys=True))


async def enqueue(user_id: str, recording: dict, candidates: list[dict], *, names: Optional[Any] = None,
                  now: Optional[datetime] = None) -> dict:
    """Rank this recording's candidates against what the member's day already holds and keep the best ``max_items()`` for the day. Never
    raises (a failed enqueue must not fail the night pass). Returns counts only. Off = ``{"status": "off"}``."""
    m = mode()
    if m == "off" or not candidates:
        return {"status": m if m == "off" else "empty", "stored": 0}
    try:
        import omi_day_review as dr
        rid = str(recording["id"])
        now = now or _now()
        day = dr.day_of(recording.get("started_at")) or _local(now).date().isoformat()
        nameset = {_norm(n) for n in (names or []) if n}
        expires = recording.get("audio_expires_at") or _iso(now + timedelta(days=7))
        status = "open" if m == "on" else "shadow"
        stored = 0
        by_class: dict[str, int] = {}
        async with _db() as db:
            feeds = await _feeds_review(db, user_id, rid)
            for c in candidates:
                alts = [a for a in (c.get("alts") or []) if isinstance(a, dict) and str(a.get("word") or "").strip()]
                if len(alts) < 2:
                    continue
                imp = classify(c.get("before", ""), c.get("span", ""), c.get("after", ""), alts, nameset, c.get("kind", "slot"))
                by_class[imp] = by_class.get(imp, 0) + 1
                row = (str(uuid.uuid4()), user_id, rid, day, c["kind"], int(c["start_ms"]), int(c["end_ms"]), str(c.get("before", "")),
                       str(c.get("span", "")), str(c.get("after", "")), json.dumps(alts), str(c.get("pick", "")), imp,
                       score(imp, alts, feeds), status, _iso(now), expires)
                cur = await db.execute(
                    "INSERT INTO omi_check_items (id, user_id, recording_id, day_local, kind, start_ms, end_ms, before_text, span_text, after_text, "
                    "alts, pick, importance, score, status, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                    "ON CONFLICT (recording_id, kind, start_ms, end_ms) DO NOTHING", row)
                stored += int(getattr(cur, "rowcount", 1) or 0)
            await _trim(db, user_id, day, status)
            await db.commit()
        if m == "shadow":
            _shadow_log({"day": day, "candidates": len(candidates), "stored": stored, "classes": by_class, "feeds_review": feeds})
        return {"status": status, "stored": stored, "classes": by_class}
    except Exception as exc:  # noqa: BLE001
        if not _missing(exc):
            logger.warning("TRANSCRIPT_CHECK_ENQUEUE_FAILED reason=%s", type(exc).__name__)
        return {"status": "failed", "stored": 0}



async def _trim(db, user_id: str, day: str, status: str) -> int:
    """Keep the top ``max_items()`` of the day's still-undecided items (at most ``MAX_CHUNK_ITEMS`` whole-chunk ones); delete the rest."""
    rows = await _fetch(db, "SELECT id, kind, score FROM omi_check_items WHERE user_id = ? AND day_local = ? AND status = ? ORDER BY score DESC, id",
                        (user_id, day, status))
    keep: list[str] = []
    chunks = 0
    for r in rows:
        if len(keep) >= max_items():
            break
        if r["kind"] == "chunk":
            if chunks >= MAX_CHUNK_ITEMS:
                continue
            chunks += 1
        keep.append(r["id"])
    drop = [r["id"] for r in rows if r["id"] not in set(keep)]
    for i in drop:
        await db.execute("DELETE FROM omi_check_items WHERE id = ?", (i,))
    return len(drop)


async def open_recording_ids(user_id: Optional[str] = None) -> set[str]:
    """Recordings whose WAV an OPEN item still needs (``release_tagged_audio`` skips them). Empty unless live. Never raises."""
    if not live():
        return set()
    try:
        async with _db() as db:
            sql = "SELECT DISTINCT recording_id FROM omi_check_items WHERE status = 'open' AND (expires_at IS NULL OR expires_at > ?)"
            params: tuple = (_iso(_now()),)
            if user_id:
                sql += " AND user_id = ?"
                params += (user_id,)
            return {r["recording_id"] for r in await _fetch(db, sql, params)}
    except Exception:  # noqa: BLE001
        return set()


async def sweep(*, now: Optional[datetime] = None) -> int:
    """Expire open items past their audio's retention; drop stale shadow rows. Returns rows touched. Never raises."""
    if not enabled():
        return 0
    try:
        stamp = _iso(now or _now())
        async with _db() as db:
            cur = await db.execute("UPDATE omi_check_items SET status = 'expired' WHERE status = 'open' AND expires_at IS NOT NULL AND expires_at <= ?", (stamp,))
            n = int(getattr(cur, "rowcount", 0) or 0)
            await db.execute("DELETE FROM omi_check_items WHERE status IN ('shadow', 'expired', 'skipped') AND created_at < ?",
                             (_iso((now or _now()) - timedelta(days=14)),))
            await db.commit()
            return n
    except Exception:  # noqa: BLE001
        return 0


# ------------------------------------------------------------------------------------------------------------ morning: serve
def public(row: Any) -> dict:
    alts = json.loads(row["alts"] or "[]")
    return {"id": row["id"], "kind": row["kind"], "before": row["before_text"], "span": row["span_text"], "after": row["after_text"],
            "pick": row["pick"], "alts": [{"i": i, "word": a.get("word", ""), "votes": a.get("votes", 0)} for i, a in enumerate(alts)],
            "importance": row["importance"], "has_audio": True}


async def list_open(user_id: str) -> dict:
    """The member's OWN open items, best first, with the progress counts. Items that name a forgotten entity are deleted, not shown.
    Not live = nothing (shadow items are never served)."""
    if not live():
        return {"items": [], "done": 0, "total": 0}
    stamp = _iso(_now())
    async with _db() as db:
        rows = await _fetch(db, "SELECT i.* FROM omi_check_items i JOIN omi_recordings r ON r.id = i.recording_id AND r.user_id = i.user_id "
                                "WHERE i.user_id = ? AND i.status IN ('open', 'answered', 'skipped') AND r.status = 'done' "
                                "AND (i.expires_at IS NULL OR i.expires_at > ?) ORDER BY i.score DESC, i.id", (user_id, stamp))
        shown, done = [], 0
        for r in rows:
            if r["status"] != "open":
                done += 1
                continue
            text = " ".join([r["before_text"], r["span_text"], r["after_text"]] + [str(a.get("word", "")) for a in json.loads(r["alts"] or "[]")])
            try:
                if await _forgotten(user_id, text):
                    await db.execute("DELETE FROM omi_check_items WHERE id = ?", (r["id"],))
                    continue
            except Exception:  # noqa: BLE001 - an unreadable ledger FAILS CLOSED: nothing is shown
                logger.warning("TRANSCRIPT_CHECK_LEDGER_UNREADABLE")
                return {"items": [], "done": done, "total": done}
            shown.append(public(r))
        await db.commit()
    return {"items": shown, "done": done, "total": done + len(shown)}


async def open_count(user_id: str) -> int:
    return len((await list_open(user_id))["items"]) if live() else 0


async def _item(db, user_id: str, item_id: str) -> Optional[Any]:
    rows = await _fetch(db, "SELECT * FROM omi_check_items WHERE id = ? AND user_id = ?", (str(item_id), user_id))
    return rows[0] if rows else None


async def clip(user_id: str, item_id: str) -> Optional[bytes]:
    """The item's audio snippet (<= 8 s of WAV) from the still-held recording; None when the item is not this member's or no audio is held."""
    if not live():
        return None
    import omi_recordings as omi
    async with _db() as db:
        row = await _item(db, user_id, item_id)
    if row is None or row["status"] != "open":
        return None
    return await omi.span_clip(user_id, row["recording_id"], int(row["start_ms"]), int(row["end_ms"]), max_s=SNIPPET_MAX_MS / 1000)


# ----------------------------------------------------------------------------------------------------------------- answering
def _swap(text: str, before: str, old: str, after: str, new: str) -> tuple[str, bool]:
    """Replace ``old`` with ``new`` in ``text``, anchored on the words around it (the nearest match to its context), else the only whole-word hit."""
    if not text:
        return text, False
    if not old:                                   # a contested "nothing": insert between the anchors
        a, b = before.split()[-3:], after.split()[:3]
        pat = re.compile(r"\s+".join(re.escape(w) for w in a + b)) if (a or b) else None
        if pat and (mt := pat.search(text)) and a:
            hit = mt.group(0)
            return text.replace(hit, " ".join(a + [new] + b), 1), True
        return text, False
    ctx_b, ctx_a = before.split()[-3:], after.split()[:3]
    pat = r"\s+".join([re.escape(w) for w in ctx_b] + [r"(?P<span>" + re.escape(old) + r")"] + [re.escape(w) for w in ctx_a])
    mt = re.search(pat, text)
    if mt:
        s, e = mt.span("span")
        return text[:s] + new + text[e:], True
    hits = list(re.finditer(r"(?<!\w)" + re.escape(old) + r"(?!\w)", text))
    if len(hits) == 1:
        return text[:hits[0].start()] + new + text[hits[0].end():], True
    return text, False


async def _apply_text(db, row: Any, new: str) -> bool:
    old = row["span_text"]
    rid, uid = row["recording_id"], row["user_id"]
    ok = False
    segs = await _fetch(db, "SELECT id, text FROM omi_segments WHERE recording_id = ? AND user_id = ? AND start_ms <= ? AND end_ms >= ? ORDER BY seq",
                        (rid, uid, int(row["end_ms"]), int(row["start_ms"])))
    variants = [old + LOW_CONFIDENCE_MARK, old] if row["kind"] == "chunk" else [old]
    for s in segs:
        for v in variants:
            text, hit = _swap(s["text"], row["before_text"], v, row["after_text"], new)
            if hit:
                await db.execute("UPDATE omi_segments SET text = ?, text_provenance = 'owner_corrected' WHERE id = ? AND user_id = ?", (text, s["id"], uid))
                ok = True
                break
        else:                       # a retry after a failed answer: the word is already the owner's
            if new and _swap(s["text"], row["before_text"], new, row["after_text"], new)[1]:
                ok = True
    recs = await _fetch(db, "SELECT transcript FROM omi_recordings WHERE id = ? AND user_id = ?", (rid, uid))
    if recs:
        for v in variants:
            text, hit = _swap(recs[0]["transcript"] or "", row["before_text"], v, row["after_text"], new)
            if hit:
                await db.execute("UPDATE omi_recordings SET transcript = ? WHERE id = ? AND user_id = ?", (text, rid, uid))
                ok = True
                break
        else:
            if new and _swap(recs[0]["transcript"] or "", row["before_text"], new, row["after_text"], new)[1]:
                ok = True
    return ok


async def _credit_ledger(db, user_id: str, alts: list, answer: str) -> int:
    """Ground truth: each model that offered the answer agreed; each that offered something else contradicted. Counts only."""
    n, stamp = 0, _iso(_now())
    want = _norm(answer)
    seen: dict[str, bool] = {}
    for a in alts:
        agreed = bool(want) and _norm(str(a.get("word", ""))) == want
        for m in a.get("models") or []:
            seen[m] = seen.get(m, False) or agreed
    for model, agreed in seen.items():
        await db.execute("INSERT INTO asr_contender_ledger (user_id, model, agreed, contradicted, updated_at) VALUES (?,?,?,?,?) "
                         "ON CONFLICT (user_id, model) DO UPDATE SET agreed = asr_contender_ledger.agreed + ?, "
                         "contradicted = asr_contender_ledger.contradicted + ?, updated_at = ?",
                         (user_id, model, int(agreed), int(not agreed), stamp, int(agreed), int(not agreed), stamp))
        n += 1
    return n


class CheckError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


async def answer(user_id: str, item_id: str, *, alt: Optional[int] = None, word: Optional[str] = None, via: str = "panel") -> dict:
    """The owner settled an item: pick alternative ``alt`` or say ``word``. Corrects the stored segment + transcript (``owner_corrected``),
    feeds the contender ledger, and re-opens the day review of the recording if it had pending candidates. Raises ``CheckError``."""
    if not live():
        raise CheckError("off", 404)
    async with _db() as db:
        row = await _item(db, user_id, item_id)
    if row is None:
        raise CheckError("not_found", 404)
    if row["status"] != "open":
        raise CheckError("already_decided")
    alts = json.loads(row["alts"] or "[]")
    if alt is not None:
        if not (0 <= int(alt) < len(alts)):
            raise CheckError("bad_choice", 400)
        chosen = str(alts[int(alt)].get("word", ""))
    else:
        chosen = " ".join(str(word or "").split())[:80]
    if not chosen:
        raise CheckError("empty", 400)
    if chosen == NONE_WORD:
        chosen = ""
    import omi_recordings as omi
    stored = await omi._redact_forgotten(user_id, chosen) if chosen else ""
    if await _forgotten(user_id, " ".join([row["before_text"], chosen, row["after_text"]])):
        raise CheckError("forgotten", 410)
    stamp = _iso(_now())
    async with erase_gate.writing(user_id, erase_gate.generation(user_id)) as ok:
        if not ok:
            raise CheckError("erased", 410)
        async with _db() as db:
            fresh = await _item(db, user_id, item_id)
            if fresh is None or fresh["status"] != "open":
                raise CheckError("already_decided")
            applied = await _apply_text(db, row, stored)
            if not applied:           # the words it pointed at are gone (a later pass, an erase): nothing was corrected, so nothing is credited or closed as answered
                await db.execute("UPDATE omi_check_items SET status = 'expired' WHERE id = ? AND status = 'open'", (row["id"],))
                await db.commit()
                raise CheckError("text_moved", 409)
            # ONE transaction: the correction, the pending day-review candidates that rested on the old words, the ledger and the status either
            # all land or none do (a failure here propagates; the answer can be retried, and _apply_text is idempotent)
            import omi_day_review as dr
            reopened = await dr.reopen_recording_in(db, user_id, row["recording_id"])
            ledgered = await _credit_ledger(db, user_id, alts, chosen)
            await db.execute("UPDATE omi_check_items SET status = 'answered', answer = ?, answered_via = ?, answered_at = ?, applied = 1 WHERE id = ? AND status = 'open'",
                             (stored, via, stamp, row["id"]))
            await db.commit()
    await _after_decision(user_id)
    logger.info("TRANSCRIPT_CHECK_ANSWERED via=%s ledger=%d reopened=%d", via, ledgered, reopened)
    return {"ok": True, "applied": True, "ledger": ledgered, "reopened": reopened}


async def skip(user_id: str, item_id: str) -> bool:
    if not live():
        raise CheckError("off", 404)
    async with _db() as db:
        cur = await db.execute("UPDATE omi_check_items SET status = 'skipped', answered_via = 'skip', answered_at = ? WHERE id = ? AND user_id = ? AND status = 'open'",
                               (_iso(_now()), str(item_id), user_id))
        await db.commit()
        done = bool(getattr(cur, "rowcount", 0))
    if done:
        await _after_decision(user_id)
    return done


async def _after_decision(user_id: str) -> None:
    """The audio an item held goes once nothing else holds it; the notification is cleared when the queue is empty."""
    try:
        import omi_recordings as omi
        await omi.release_tagged_audio(user_id)
        if not await open_recording_ids(user_id):
            async with _db() as db:
                await db.execute("UPDATE notifications SET delivered = 1, action_taken = 'done' WHERE user_id = ? AND type = 'transcript_check'", (user_id,))
                await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("transcript_check: after-decision cleanup skipped (%s)", type(exc).__name__)


async def ledger(user_id: str) -> list[dict]:
    async with _db() as db:
        rows = await _fetch(db, "SELECT model, agreed, contradicted FROM asr_contender_ledger WHERE user_id = ? ORDER BY model", (user_id,))
    return [{"model": r["model"], "agreed": int(r["agreed"]), "contradicted": int(r["contradicted"])} for r in rows]


# ----------------------------------------------------------------------------------------------------------- forget / erase
async def forget_user(db, user_id: str) -> int:
    n = 0
    for table in ("omi_check_items", "omi_check_asks", "asr_contender_ledger"):
        try:
            cur = await db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))
            n += int(getattr(cur, "rowcount", 0) or 0)
        except Exception as exc:  # noqa: BLE001
            if not _missing(exc):
                raise
    return n


async def drop_recording(db, rid: str) -> int:
    try:
        cur = await db.execute("DELETE FROM omi_check_items WHERE recording_id = ?", (rid,))
        return int(getattr(cur, "rowcount", 0) or 0)
    except Exception as exc:  # noqa: BLE001
        if not _missing(exc):
            raise
        return 0


async def redact(db, user_id: str, rx: "re.Pattern[str]") -> int:
    """A forgotten name: every item of the member that names it is deleted (its words are the sentence, so they cannot be redacted in place)."""
    n = 0
    try:
        for r in await _fetch(db, "SELECT id, before_text, span_text, after_text, alts FROM omi_check_items WHERE user_id = ?", (user_id,)):
            if rx.search(" ".join([r["before_text"], r["span_text"], r["after_text"], r["alts"] or ""])):
                await db.execute("DELETE FROM omi_check_items WHERE id = ?", (r["id"],))
                n += 1
    except Exception as exc:  # noqa: BLE001
        if not _missing(exc):
            raise
    return n


# ============================================================================================================== the spoken ask
class _Turn:
    __slots__ = ("panel_id", "scope_user", "verified", "at")

    def __init__(self, panel_id: str, scope_user: Optional[str], verified: bool, at: float):
        self.panel_id, self.scope_user, self.verified, self.at = panel_id, scope_user, verified, at


_TURNS: dict[str, _Turn] = {}
_PENDING: dict[str, tuple[str, float]] = {}        # session -> (member, asked_at): a "yes" in the next ASK_TTL_S answers the ask
_LISTEN: dict[str, tuple[str, str, float]] = {}    # member -> (item_id, panel_id, until): "say it"


def _clock() -> float:
    return time.monotonic()


def note_voice_turn(session_id: str, panel_id: str, *, scope_user: Optional[str], verdict: Optional[bool]) -> None:
    """Called by the voice lane once per spoken turn after its identity is resolved. Only a turn the speaker gate CONFIRMED (verdict True)
    with an identified member counts as verified. Typed chat never calls this, so it can never be asked. NEVER raises."""
    try:
        if not live() or not session_id:
            return
        now = _clock()
        _TURNS[str(session_id)] = _Turn(str(panel_id or ""), (scope_user or None), verdict is True, now)
        for s in [s for s, t in _TURNS.items() if now - t.at > TURN_TTL_S]:
            _TURNS.pop(s, None)
    except Exception:  # noqa: BLE001
        pass


def verified_member(session_id: str) -> Optional[str]:
    """The member this session's latest voice turn was VERIFIED as, else None (guest, unverified, typed, unknown lane, stale)."""
    t = _TURNS.get(str(session_id or ""))
    if t is None or not t.verified or not t.scope_user or _clock() - t.at > TURN_TTL_S:
        return None
    m = str(t.scope_user)
    try:
        import identity_facts
        from user_filters import is_unidentified
        if m.startswith("panel") or is_unidentified(m) or not identity_facts._is_account_id(m):
            return None
    except Exception:  # noqa: BLE001
        return None
    return m


_YES = re.compile(r"^\W*(?:(?:yes|yeah|yep|yup|sure|okay|ok|alright|all right|please|go ahead|of course|sounds good|that's fine|go on)\b[\s,.!]*)+"
                  r"(?:please|zoe|thanks|thank you|now)?\W*$", re.I)
_LATER = re.compile(r"^\W*(?:(?:no|nope|nah|later|not now|not yet|not right now|maybe later|in a (?:bit|while|minute)|another time|leave it|"
                    r"not today|i'm busy|im busy|not at the moment|after)\b[\s,.!]*)+(?:thanks|thank you|zoe|please)?\W*$", re.I)


def classify_reply(text: Optional[str]) -> Optional[str]:
    t = (text or "").strip()
    if not t or len(t) > 60:
        return None
    if _YES.match(t):
        return "yes"
    if _LATER.match(t):
        return "later"
    return None


async def _preferred_name(user_id: str) -> str:
    try:
        import identity_facts
        return (await identity_facts.preferred_name(user_id) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _after_hour(now: Optional[datetime] = None) -> bool:
    return _local(now).hour >= ask_from_hour()


async def _reserve(user_id: str, day: str) -> bool:
    """The once-a-day lock: True only for the caller that inserted the row."""
    async with _db() as db:
        cur = await db.execute("INSERT INTO omi_check_asks (user_id, day_local, outcome, created_at) VALUES (?,?, 'reserved', ?) "
                               "ON CONFLICT (user_id, day_local) DO NOTHING", (user_id, day, _iso(_now())))
        await db.commit()
        return bool(getattr(cur, "rowcount", 0))


async def _settle(user_id: str, day: str, outcome: str) -> None:
    async with _db() as db:
        if outcome == "released":
            await db.execute("DELETE FROM omi_check_asks WHERE user_id = ? AND day_local = ? AND outcome = 'reserved'", (user_id, day))
        else:
            await db.execute("UPDATE omi_check_asks SET outcome = ? WHERE user_id = ? AND day_local = ?", (outcome, user_id, day))
        await db.commit()


async def _notify(user_id: str, day: str, n: int) -> None:
    """The panel notification the ask always leaves, so 'later' (or no answer) never loses the queue. Never raises."""
    try:
        nid = f"transcript_check:{user_id}:{day}"
        msg = "A few words from last night's transcripts need you. Tap to help."
        async with _db() as db:
            await db.execute("INSERT INTO notifications (id, user_id, type, title, message, data, delivered, created_at) VALUES (?,?,?,?,?,?,0,NOW()) "
                             "ON CONFLICT (id) DO NOTHING", (nid, user_id, "transcript_check", "Transcript check", msg, json.dumps({"open": "checks", "count": n})))
            await db.commit()
        from push import broadcaster
        await broadcaster.broadcast("all", "notification_created", {"id": nid, "type": "transcript_check", "title": "Transcript check", "message": msg,
                                                                    "delivered": False}, user_id=user_id)
    except Exception as exc:  # noqa: BLE001
        logger.debug("transcript_check: notification skipped (%s)", type(exc).__name__)


async def prepare_tail(user_id: str, session_id: str, user_text: str, reply_text: str) -> Optional[str]:
    """The line to append to a reply that has just been answered, or None. Reserves the day's single ask; the caller MUST then ``confirm_tail``
    (spoken) or ``release_tail`` (not spoken). Every refusal is a plain None."""
    try:
        if not live():
            return None
        member = verified_member(session_id)
        if not member:
            return None
        if not reply_text.strip() or reply_text.rstrip().endswith("?"):        # one question per reply: never stack ours on Zoe's own
            return None
        if not _after_hour():
            return None
        if _is_emotional(user_text, member):
            return None
        if classify_reply(user_text) or (session_id in _PENDING):
            return None
        if await open_count(member) < 1:
            return None
        day = _today()
        if not await _reserve(member, day):
            return None
        name = await _preferred_name(member)
        return ASK_LINE.format(name=name) if name else ASK_LINE_NONAME
    except Exception as exc:  # noqa: BLE001
        logger.debug("transcript_check: tail skipped (%s)", type(exc).__name__)
        return None


def _is_emotional(text: str, member: str) -> bool:
    try:
        import emotional_turn_guard as etg
        return bool(etg.is_emotional_turn(text)) or bool(etg.holds_extras(text, member, log=False))
    except Exception:  # noqa: BLE001 - cannot tell = not asked (fail closed)
        return True


async def confirm_tail(user_id: str, session_id: str) -> None:
    member = verified_member(session_id) or user_id
    day = _today()
    await _settle(member, day, "asked")
    _PENDING[str(session_id)] = (member, _clock())
    await _notify(member, day, await open_count(member))


async def release_tail(user_id: str, session_id: str) -> None:
    await _settle(verified_member(session_id) or user_id, _today(), "released")


async def append_stream(turn: AsyncIterator[str], user_text: str, user_id: str, session_id: str, *, skip: tuple = ()) -> AsyncIterator[str]:
    """Wrap the brain's reply stream: the reply goes out untouched; only when it finished with real text does ONE line follow. Off = the
    stream is passed through (callers do not even wrap it). A consumer that stops early (barge-in) gets no line and releases nothing it held."""
    reply: list[str] = []
    async for d in turn:
        if not d.startswith(("__TOOL__:", "__THINKING__:")) and d.strip() and d not in skip:
            reply.append(d)
        yield d
    text = "".join(reply)
    tail = await prepare_tail(user_id, session_id, user_text, text) if text.strip() else None
    if tail:
        try:
            yield " " + tail
        except GeneratorExit:
            await release_tail(user_id, session_id)
            raise
        await confirm_tail(user_id, session_id)


async def append_to_reply(reply: str, user_text: str, user_id: str, session_id: str) -> str:
    """The non-streaming twin (deterministic tiers): ``reply`` with the line appended when due, else ``reply`` unchanged."""
    tail = await prepare_tail(user_id, session_id, user_text, reply or "")
    if not tail:
        return reply
    await confirm_tail(user_id, session_id)
    return f"{reply.rstrip()} {tail}"


# ------------------------------------------------------------------------------------------------------------ the answer to it
async def _open_frame(member: str, session_id: str) -> int:
    """Push the 'open the Transcript check' frame to the panel the member just spoke at (a minted card token rides with it)."""
    t = _TURNS.get(str(session_id))
    if t is None or not t.panel_id:
        return 0
    import panel_review_cards as prc
    import voice_session
    n = await open_count(member)
    token = prc.mint(t.panel_id, member, "transcript_check", {})
    frame = {"panel_id": t.panel_id, "member_id": member, "kind": "transcript_check", "private": True, "open": "checks",
             "title": "Transcript check", "subtitle": f"{n} to check" if n != 1 else "1 to check", "items": [], "card_token": token}
    return int(await voice_session.send_to_panel(t.panel_id, "voice_card", frame) or 0)


def listen_for(member: str, item_id: str, panel_id: str) -> None:
    _LISTEN[member] = (str(item_id), str(panel_id), _clock() + LISTEN_TTL_S)


_SAID = re.compile(r"^\W*(?:(?:it's|it is|its|that's|that is|the word is|it was|i said|i think it's|it's the word)\s+)+", re.I)


def heard_word(text: str) -> str:
    """The word(s) from a 'say it' utterance: strips the lead-in, at most 4 words, no sentence punctuation."""
    t = _SAID.sub("", (text or "").strip()).strip(" .,!?\"'")
    return " ".join(t.split()[:4])


async def tier(text: str, user_id: str, session_id: str = "", *, channel: Optional[str] = None, extra_ctx: Optional[dict] = None,
               allow_writes: bool = True):
    """Fast-tier hook (voice only). Takes a turn ONLY when it answers the ask Zoe just made, or fills a 'say it' the owner started on the
    panel. Anything else returns None before any work. NEVER raises."""
    try:
        if not live() or (channel or "").strip().lower() != "voice" or allow_writes is False:
            return None
        sid = str(session_id or "")
        member = verified_member(sid)
        pend = _PENDING.get(sid)
        listening = _LISTEN.get(member or "")
        if not pend and not listening:
            return None
        import expert_dispatch as _xd
        if listening and member and _clock() <= listening[2]:
            _LISTEN.pop(member, None)
            word = heard_word(text)
            if word:
                await answer(member, listening[0], word=word, via="voice")
                return _xd.DispatchResult(domain="memory", reply=HEARD_REPLY, intent="transcript_check", tier=TIER)
        if not pend or _clock() - pend[1] > ASK_TTL_S:
            _PENDING.pop(sid, None)
            return None
        kind = classify_reply(text)
        if not kind:
            _PENDING.pop(sid, None)                 # anything else is a new command: the ask stays as a notification
            return None
        _PENDING.pop(sid, None)
        if not member or member != pend[0]:
            return _xd.DispatchResult(domain="memory", reply=PRIVATE_REPLY, intent="transcript_check", tier=TIER)
        if kind == "later":
            await _settle(member, _today(), "later")
            return _xd.DispatchResult(domain="memory", reply=LATER_REPLY, intent="transcript_check", tier=TIER)
        await _settle(member, _today(), "opened")
        await _open_frame(member, sid)
        return _xd.DispatchResult(domain="memory", reply=OPENED_REPLY, intent="transcript_check", tier=TIER)
    except CheckError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("transcript_check: tier failed (non-fatal): %s", type(exc).__name__)
        return None


def reset() -> None:
    _TURNS.clear()
    _PENDING.clear()
    _LISTEN.clear()
