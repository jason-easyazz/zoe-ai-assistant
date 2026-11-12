"""The MORNING TRANSCRIPT CHECK: the words the night re-pass could not settle, put to the owner in the morning, on a turn HE started.

Flag ``ZOE_TRANSCRIPT_CHECK`` = off | shadow | on (default OFF). Spec and owner acceptance: ``docs/knowledge/transcript-check.md``.

  off     nothing runs; every hook returns before any I/O.
  shadow  the night half builds the queue and logs COUNTS ("would ask 7: 3 names, 2 numbers ...", never a word) and stores the items
          as ``shadow`` rows: nothing is spoken, nothing is shown, no audio is held for them.
  on      the queue is real: audio is held for each open item (inside the existing retention), the owner's first verified-voice turn
          after 06:00 carries ONE appended line, and the panel "Transcript check" screen serves the items.

Two halves with one table (``omi_check_items``, migration 0076) between them:

  NIGHT   ``candidates_from_row`` turns the ROVER fusion's stored chunk rows (contested slots, low_conf chunks) into
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
  * An answer corrects the stored text with provenance ``owner_corrected``, is ground truth for the night re-pass's contender ledger
    (``omi_repass_eval.note_owner_correction``), and
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

IMPORTANCE_SCORE = {"name": 100.0, "who_first": 95.0, "number": 90.0, "commitment": 70.0, "who": 60.0, "chat": 10.0}
WHO_UNKNOWN = "(someone new)"
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
    """The table is not migrated (a box that has not run 0076) or there is no pool in this process: nothing to read or erase."""
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
def _surface(word: Optional[str]) -> str:
    return NONE_WORD if not word else word


def candidates_from_row(row: dict) -> list[dict]:
    """One stored ROVER chunk row (``omi_repass_eval.is_contested``; the shape ``check_queue`` returns: ``t0``/``t1`` seconds into the recording, ``hyps``
    {model: text}, ``rover`` the fused text, ``slots`` [{pos, pick, votes {word: n}, who {word: [models]}, n}], ``low_conf``) -> candidates. Pure; no I/O.
    A contested SLOT gives one candidate per slot (each word voted for, its vote count, who voted for it); a ``low_conf`` chunk gives ONE whole-chunk
    candidate offering each model's text. Both carry the chunk's fused text as a template (``chunk_tpl``, the span replaced by ``\x01``) so an answer can be
    scored against every model's whole-chunk hypothesis (``omi_repass_eval.note_owner_correction``)."""
    t0, t1 = int(float(row.get("t0") or 0) * 1000), int(float(row.get("t1") or 0) * 1000)
    hyps = {m: str(v) for m, v in (row.get("hyps") or {}).items() if v}
    rover = str(row.get("rover") or "")
    out: list[dict] = []
    if row.get("low_conf"):
        base = rover.replace(LOW_CONFIDENCE_MARK, "").strip()
        if not base:
            return out
        alts: dict[str, list] = {}
        for m, txt in hyps.items():
            alts.setdefault(txt.strip(), []).append(m)
        out.append({"kind": "chunk", "start_ms": t0, "end_ms": min(t1, t0 + SNIPPET_MAX_MS), "before": "", "span": base, "after": "", "pick": base,
                    "alts": [{"word": txt, "votes": len(ms), "models": sorted(ms)} for txt, ms in alts.items()],
                    "chunk_tpl": "\x01", "chunk_t0_ms": t0, "chunk_t1_ms": t1})
        return out
    tokens = rover.split()
    n = max(1, len(tokens))
    for slot in row.get("slots") or []:
        pos = max(0, min(int(slot.get("pos") or 0), len(tokens)))
        votes, who = slot.get("votes") or {}, slot.get("who") or {}
        pick_w = slot.get("pick")
        pick_s = tokens[pos] if (pick_w and pos < len(tokens)) else ""
        alts = []
        for word, v in votes.items():
            label = pick_s if (word and word == pick_w and pick_s) else _surface(word)
            alts.append({"word": label, "votes": int(v), "models": sorted(who.get(word) or [])})
        if len(alts) < 2:
            continue
        has = bool(pick_w) and pos < len(tokens)
        before = " ".join(tokens[max(0, pos - CONTEXT_WORDS):pos])
        after = " ".join(tokens[pos + (1 if has else 0):pos + (1 if has else 0) + CONTEXT_WORDS])
        tpl = " ".join(tokens[:pos] + ["\x01"] + tokens[pos + (1 if has else 0):])
        mid = t0 + int((pos / n) * max(0, t1 - t0))
        out.append({"kind": "slot", "start_ms": max(t0, mid - SNIPPET_PAD_MS), "end_ms": min(max(t1, t0 + 1), mid + SNIPPET_PAD_MS), "before": before,
                    "span": pick_s if has else "", "after": after, "pick": pick_s or NONE_WORD, "alts": alts,
                    "chunk_tpl": tpl, "chunk_t0_ms": t0, "chunk_t1_ms": t1})
    return out


def candidates_from_who(row: dict) -> list[dict]:
    """A ``who_said`` row (the night re-pass's turns labelled "?" or "X?" - leaning) -> ONE candidate of kind ``who``. ``row``: ``t0``/``t1`` seconds into
    the recording, ``text`` the turn as written, ``candidates`` [{person_id, name, score, cluster_id?}] best first (a candidate with a ``cluster_id`` and no
    ``person_id`` is an UNNAMED voice the owner may merge this into), ``first`` True when answering would give a voice with no pendant print its first
    confirmation (what it unlocks ranks it first), ``turns`` how many turns of that voice wait. Pure; no I/O."""
    text = " ".join(str(row.get("text") or "").split())
    alts = []
    for c in row.get("candidates") or []:
        name = " ".join(str(c.get("name") or "").split())
        if name and (c.get("person_id") or c.get("cluster_id")):
            alts.append({"word": name, "person_id": c.get("person_id") or "", "cluster_id": c.get("cluster_id") or "", "votes": int(round(float(c.get("score") or 0) * 100)), "models": []})
    t0, t1 = int(float(row.get("t0") or 0) * 1000), int(float(row.get("t1") or 0) * 1000)
    if not text:
        return []
    return [{"kind": "who", "start_ms": t0, "end_ms": min(max(t1, t0 + 1), t0 + SNIPPET_MAX_MS), "before": "", "span": text, "after": "", "alts": alts,
             "pick": alts[0]["word"] if alts else "", "first": bool(row.get("first")), "turns": int(row.get("turns") or 1)}]


def who_row_from_item(item: dict, text: str, people: dict) -> dict:
    """The night pass's REAL ``who_items`` row (``seg_id``, ``t0``/``t1`` seconds, ``mark``, ``cands`` = [[id, score]] best first, id ``"you"`` or a person id) plus the turn's
    text (resolved by the caller from the segment it was made for) and ``people`` {person_id: name} (his own live people) -> the shape ``candidates_from_who`` reads.
    A candidate whose id is not one of his people (``"you"``, a deleted person) is not offered here; the owner can still say a name or pick "someone new". Pure."""
    cands = []
    for pair in item.get("cands") or []:
        try:
            pid, score = str(pair[0]), float(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        if pid in people:
            cands.append({"person_id": pid, "name": people[pid], "score": score})
    return {"kind": "who_said", "t0": item.get("t0") or 0, "t1": item.get("t1") or 0, "text": text, "candidates": cands, "first": item.get("mark") == "unknown", "turns": 1}


async def _drop_forgotten(user_id: str, candidates: list[dict]) -> list[dict]:
    """Forgotten means forever: a candidate whose context, span, pick or any offered word / name mentions an entity the forget ledger holds is dropped BEFORE it is stored.
    FAIL CLOSED: an unreadable ledger raises (``enqueue`` then stores nothing)."""
    import memory_forgotten as mf
    kept = []
    for c in candidates:
        ctx = " ".join(str(c.get(k) or "") for k in ("before", "span", "after", "pick")).strip()
        if ctx and await mf.spans(user_id, ctx, strict=True):
            continue                                                  # the words themselves name a forgotten entity: nothing of this stretch is queued
        alts = []
        for a in c.get("alts") or []:
            w = str(a.get("word", "")) if isinstance(a, dict) else ""
            if w.strip() and await mf.spans(user_id, w, strict=True):
                continue                                              # an offered word / name that is forgotten is simply not offered
            alts.append(a)
        kept.append({**c, "alts": alts})
    return kept


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
        candidates = await _drop_forgotten(user_id, candidates)          # before any store: an unreadable ledger raises and nothing is queued
        async with _db() as db:
            feeds = await _feeds_review(db, user_id, rid)
            for c in candidates:
                alts = [a for a in (c.get("alts") or []) if isinstance(a, dict) and str(a.get("word") or "").strip()]
                who = c.get("kind") == "who"
                if len(alts) < (1 if who else 2) and not who:
                    continue
                imp = ("who_first" if c.get("first") else "who") if who else classify(c.get("before", ""), c.get("span", ""), c.get("after", ""), alts, nameset, c.get("kind", "slot"))
                by_class[imp] = by_class.get(imp, 0) + 1
                row = (str(uuid.uuid4()), user_id, rid, day, c["kind"], int(c["start_ms"]), int(c["end_ms"]), str(c.get("before", "")),
                       str(c.get("span", "")), str(c.get("after", "")), json.dumps(alts), str(c.get("pick", "")), imp,
                       (score(imp, alts, feeds) + (min(10, int(c.get("turns") or 1)) if who else 0.0)), status, _iso(now), expires, str(c.get("chunk_tpl", "")), int(c.get("chunk_t0_ms", c["start_ms"])),
                       int(c.get("chunk_t1_ms", c["end_ms"])))
                cur = await db.execute(
                    "INSERT INTO omi_check_items (id, user_id, recording_id, day_local, kind, start_ms, end_ms, before_text, span_text, after_text, "
                    "alts, pick, importance, score, status, created_at, expires_at, chunk_tpl, chunk_t0_ms, chunk_t1_ms) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
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
    """Recordings whose WAV an OPEN item still needs (``release_tagged_audio`` skips them). Empty unless live. Raises on a real read failure (callers keep the audio)."""
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
    except Exception as exc:  # noqa: BLE001
        if _missing(exc):                       # not migrated / no pool: no items exist, so nothing is held
            return set()
        raise                                   # a transient read failure must NOT read as "nothing held": the caller (release_tagged_audio) fails closed and keeps the WAV


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
def public(row: Any, alts: Optional[list] = None) -> dict:
    alts = json.loads(row["alts"] or "[]") if alts is None else alts
    return {"id": row["id"], "kind": row["kind"], "before": row["before_text"], "span": row["span_text"], "after": row["after_text"],
            "pick": row["pick"], "alts": [{"i": a.get("_i", i), "word": a.get("word", ""), "votes": a.get("votes", 0), **({"cluster": True} if a.get("cluster_id") and not a.get("person_id") else {})}
                                          for i, a in enumerate(alts)],
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
            alts = json.loads(r["alts"] or "[]")
            text = " ".join([r["before_text"], r["span_text"], r["after_text"]] + ([] if r["kind"] == "who" else [str(a.get("word", "")) for a in alts]))
            try:
                if await _forgotten(user_id, text):
                    await db.execute("DELETE FROM omi_check_items WHERE id = ?", (r["id"],))
                    continue
                if r["kind"] == "who":
                    alts = await _offerable(db, user_id, alts)
            except Exception:  # noqa: BLE001 - an unreadable ledger FAILS CLOSED: nothing is shown
                logger.warning("TRANSCRIPT_CHECK_LEDGER_UNREADABLE")
                return {"items": [], "done": done, "total": done}
            shown.append(public(r, alts))
        await db.commit()
    return {"items": shown, "done": done, "total": done + len(shown)}


async def _offerable(db, user_id: str, alts: list) -> list:
    """The candidates of a ``who`` item the owner may be offered NOW: a person who is still one of his own live people (a deleted / forgotten person is never
    offered, whatever the night thought) and whose name the forget ledger does not hold; an unnamed voice that still exists. An unreadable ledger raises."""
    import omi_voices
    people = {p["id"] for p in await omi_voices.own_people(db, user_id)}
    clusters = {r["id"] for r in await _fetch(db, "SELECT id FROM omi_voice_clusters WHERE user_id = ? AND status = 'open'", (user_id,))} if any(a.get("cluster_id") for a in alts) else set()
    out = []
    for i, a in enumerate(alts):
        if a.get("person_id"):
            if a["person_id"] not in people or await _forgotten(user_id, str(a.get("word", ""))):
                continue
        elif a.get("cluster_id") not in clusters:
            continue
        out.append({**a, "_i": i})
    return out


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


def _plan(text: str, row: Any, variants: list, new: str) -> Optional[str]:
    """The corrected ``text`` (the span swapped for ``new``), the text itself when ``new`` is already there (a retry), or None when the span is not found."""
    for v in variants:
        out, hit = _swap(text, row["before_text"], v, row["after_text"], new)
        if hit:
            return out
    if new and _swap(text, row["before_text"], new, row["after_text"], new)[1]:
        return text
    return None


def _segment_window(transcript: str, segs: list, seg_id: str) -> Optional[tuple[int, int]]:
    """Where segment ``seg_id`` sits in the recording transcript: the segments are the transcript's turns in ``seq`` order, so each one is found after the previous
    one. None when the segments no longer line up with the transcript (the caller then falls back to the context-anchored whole-text swap)."""
    cursor = 0
    for sg in segs:
        t = (sg["text"] or "").strip()
        if not t:
            continue
        i = transcript.find(t, cursor)
        if i < 0:
            return None
        if sg["id"] == seg_id:
            return i, i + len(t)
        cursor = i + len(t)
    return None


async def _apply_text(db, row: Any, new: str) -> bool:
    """Correct BOTH stores or neither. Every store that holds this stretch must contain the span: at least one overlapping ``omi_segments`` row, and the recording
    transcript when it has words. The swaps are planned first and written only when the whole plan holds, so a one-sided match (a store that moved on) writes nothing
    and the caller refuses the answer. A pre-segment recording (no rows) is corrected through its transcript alone."""
    old = row["span_text"]
    rid, uid = row["recording_id"], row["user_id"]
    variants = [old + LOW_CONFIDENCE_MARK, old] if row["kind"] == "chunk" else [old]
    segs = await _fetch(db, "SELECT id, text, start_ms, end_ms FROM omi_segments WHERE recording_id = ? AND user_id = ? AND start_ms < ? AND end_ms > ? ORDER BY seq",
                        (rid, uid, int(row["end_ms"]), int(row["start_ms"])))
    writes = []
    hit_seg, hit_share = None, -1                 # the segment whose plan REPLACES the span (most overlap first): the transcript is corrected at THAT segment's occurrence
    kept_seg, kept_share = None, -1               # a segment that already holds the new word (a retry): the anchor only when NO segment replaces and none lost the span
    lost = False
    for sg in segs:
        out = _plan(sg["text"], row, variants, new)
        if out is None:
            lost = True
            continue
        share = min(int(sg["end_ms"]), int(row["end_ms"])) - max(int(sg["start_ms"]), int(row["start_ms"]))
        if out != sg["text"] or any(_swap(sg["text"], row["before_text"], v, row["after_text"], new)[1] for v in variants):     # the span really is here (also when the owner confirms the word as heard)
            if share > hit_share:
                hit_seg, hit_share = sg["id"], share
        elif share > kept_share:
            kept_seg, kept_share = sg["id"], share
        writes.append(("UPDATE omi_segments SET text = ?, text_provenance = 'owner_corrected', clean_text = '', clean_how = '' WHERE id = ? AND user_id = ?", (out, sg["id"], uid)))
    if segs and (not writes or (hit_seg is None and lost)):
        return False                              # nothing replaces the span (a neighbour that already says the new word does not count): refuse, never close the item
    if hit_seg is None:
        hit_seg = kept_seg
    recs = await _fetch(db, "SELECT transcript FROM omi_recordings WHERE id = ? AND user_id = ?", (rid, uid))
    if recs and (recs[0]["transcript"] or "").strip():
        transcript = recs[0]["transcript"]
        window = None
        if hit_seg is not None:
            every = await _fetch(db, "SELECT id, text FROM omi_segments WHERE recording_id = ? AND user_id = ? ORDER BY seq", (rid, uid))
            window = _segment_window(transcript, every, hit_seg)
        if window is not None:                    # a repeated phrase: only the selected segment's own occurrence is rewritten
            a, b = window
            part = _plan(transcript[a:b], row, variants, new)
            out = None if part is None else transcript[:a] + part + transcript[b:]
        elif segs and old and transcript.count(old) > 1:
            return False                          # the segments no longer line up with the transcript and the words repeat: which one is unknowable, so write nothing
        else:
            out = _plan(transcript, row, variants, new)
        if out is None:
            return False
        writes.append(("UPDATE omi_recordings SET transcript = ? WHERE id = ? AND user_id = ?", (out, rid, uid)))
    if not writes:
        return False
    for sql, params in writes:
        await db.execute(sql, params)
    return True


def _score_owner(row: Any, chosen: str) -> dict:
    """Ground truth for the contender ledger: ``omi_repass_eval.note_owner_correction`` scores every model's stored hypothesis for the chunk against the chunk
    as the owner corrected it (the fused text with this span replaced by his word; a whole-chunk item is his text). Counts only reach ``ledger.jsonl``.
    Fail-soft: the hypotheses are kept 7 days and may be gone; the correction itself never depends on it."""
    try:
        import omi_repass
        import omi_repass_eval as eu
        tpl = row["chunk_tpl"] or ""
        fixed = tpl.replace("\x01", chosen).strip() if tpl else chosen
        return eu.note_owner_correction(omi_repass.home_dir(), row["user_id"], row["recording_id"], int(row["chunk_t0_ms"]), int(row["chunk_t1_ms"]), fixed)
    except Exception as exc:  # noqa: BLE001
        logger.warning("TRANSCRIPT_CHECK_LEDGER_NOT_WRITTEN reason=%s", type(exc).__name__)
        return {}


class CheckError(Exception):
    def __init__(self, code: str, status: int = 409):
        super().__init__(code)
        self.code, self.status = code, status


async def answer(user_id: str, item_id: str, *, alt: Optional[int] = None, word: Optional[str] = None, new: bool = False, via: str = "panel") -> dict:
    """The owner settled an item: pick alternative ``alt`` or say ``word``. Corrects the stored segment + transcript (``owner_corrected``),
    feeds the contender ledger, and re-opens the day review of the recording if it had pending candidates. Raises ``CheckError``."""
    if not live():
        raise CheckError("off", 404)
    async with _db() as db:
        row = await _item(db, user_id, item_id)
    if row is None:
        raise CheckError("not_found", 404)
    naming_later = row["kind"] == "who" and row["status"] == "answered" and not row["answer"] and word is not None      # "someone new" -> named afterwards
    if row["status"] != "open" and not naming_later:
        raise CheckError("already_decided")
    alts = json.loads(row["alts"] or "[]")
    if row["kind"] == "who":
        return await _answer_who(user_id, row, alts, alt=alt, word=word, new=new, via=via)
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
    stamp = _iso(_now())
    async with erase_gate.writing(user_id, erase_gate.generation(user_id)) as ok:
        if not ok:
            raise CheckError("erased", 410)
        # The forget ledger is read UNDER the erase gate, before any mutation: an entity forget (bump=False) takes this same lock, so a name forgotten while this
        # request was in flight is already in the ledger here and cannot be written back. Unreadable ledger = refuse (``_forgotten`` is strict; redaction is strict).
        try:
            stored = await omi._redact_forgotten(user_id, chosen, strict=True) if chosen else ""
            forgotten = await _forgotten(user_id, " ".join([row["before_text"], chosen, row["after_text"]]))
        except Exception as exc:  # noqa: BLE001 - fail closed: nothing is written when the ledger cannot vouch for the text
            raise CheckError("forgotten", 410) from exc
        if forgotten:
            raise CheckError("forgotten", 410)
        async with _db() as db:
            fresh = await _item(db, user_id, item_id)
            if fresh is None or fresh["status"] != "open":
                raise CheckError("already_decided")
            moved = False
            # ONE explicit transaction (asyncpg auto-commits each statement outside one - commit() is a no-op - as omi_repass.finish_one notes): the correction, the pending
            # day-review candidates that rested on the old words and the status either all land or none do. A failure propagates and the answer can be retried.
            async with db.transaction():
                import omi_day_review as dr
                if not await _apply_text(db, row, stored):
                    moved = True           # the words it pointed at are gone (a later pass, an erase): nothing was corrected, so nothing is credited or closed as answered
                    await db.execute("UPDATE omi_check_items SET status = 'expired' WHERE id = ? AND status = 'open'", (row["id"],))
                else:
                    reopened = await dr.reopen_recording_in(db, user_id, row["recording_id"])
                    await db.execute("UPDATE omi_check_items SET status = 'answered', answer = ?, answered_via = ?, answered_at = ?, applied = 1 WHERE id = ? AND status = 'open'",
                                     (stored, via, stamp, row["id"]))
            if moved:
                raise CheckError("text_moved", 409)
    # The ledger is an append-only counts file outside the database (``ledger.jsonl``, #2358): it cannot join the transaction, so it is written only AFTER
    # the commit and never blocks it - a missed line is a missed data point (logged), a double line on a retry cannot happen because a decided item is final.
    ledgered = len(_score_owner(row, chosen))
    await _after_decision(user_id)
    logger.info("TRANSCRIPT_CHECK_ANSWERED via=%s ledger=%d reopened=%d", via, ledgered, reopened)
    return {"ok": True, "applied": True, "ledger": ledgered, "reopened": reopened}


async def _turn_segment(db, row: Any) -> Optional[str]:
    """The stored turn this item is about: the recording's segment overlapping its time span the most (a later pass may have re-stored the segments)."""
    rows = await _fetch(db, "SELECT id, start_ms, end_ms FROM omi_segments WHERE recording_id = ? AND user_id = ?", (row["recording_id"], row["user_id"]))
    best, share = None, 0
    for s in rows:
        ov = min(int(s["end_ms"]), int(row["end_ms"])) - max(int(s["start_ms"]), int(row["start_ms"]))
        if ov > share:
            best, share = s["id"], ov
    return best


async def _merge_into(db, user_id: str, seg_id: str, dst: str) -> None:
    """"The same person as that unnamed voice": the turn - and every turn of the voice it was in - joins cluster ``dst``; the emptied cluster goes and ``dst``'s centroid is rebuilt."""
    import omi_voices
    src = [r["voice_cluster_id"] for r in await _fetch(db, "SELECT voice_cluster_id FROM omi_segments WHERE id = ? AND user_id = ? AND voice_cluster_id IS NOT NULL", (seg_id, user_id))]
    if not await _fetch(db, "SELECT id FROM omi_voice_clusters WHERE id = ? AND user_id = ?", (dst, user_id)):
        raise CheckError("voice_gone", 410)
    await db.execute("UPDATE omi_segments SET voice_cluster_id = ? WHERE id = ? AND user_id = ?", (dst, seg_id, user_id))
    for c in src:
        if c != dst:
            await db.execute("UPDATE omi_segments SET voice_cluster_id = ? WHERE voice_cluster_id = ? AND user_id = ?", (dst, c, user_id))
            await db.execute("DELETE FROM omi_voice_clusters WHERE id = ? AND user_id = ?", (c, user_id))
    await omi_voices.rebuild_cluster(db, dst)


async def _answer_who(user_id: str, row: Any, alts: list, *, alt, word, new, via) -> dict:
    """"Who said this?": relabel the turn (``omi_voices.tag``: the CONFIRMED tag, and with learning allowed the turn's embedding joins that person's voiceprint),
    or - someone new - leave it in its persistent unnamed voice for the owner to name later, or merge it into an unnamed voice, or tag by a NAME the owner said /
    typed (a person created in his own layer). A forgotten or deleted person cannot be chosen; nothing is stored of a name the forget ledger holds."""
    import omi_voices
    import omi_day_review as dr
    async with erase_gate.writing(user_id, erase_gate.generation(user_id)) as ok:
        if not ok:
            raise CheckError("erased", 410)
        async with _db() as db:
            fresh = await _item(db, user_id, row["id"])            # claimed under the erase lock: a second, stale request for the same item is refused
            if fresh is None or not (fresh["status"] == "open" or (fresh["status"] == "answered" and not fresh["answer"] and word is not None)):
                raise CheckError("already_decided")
            seg = await _turn_segment(db, row)
            if not seg:
                await db.execute("UPDATE omi_check_items SET status = 'expired' WHERE id = ? AND status = 'open'", (row["id"],))
                await db.commit()
                raise CheckError("text_moved", 409)
            async with db.transaction():       # explicit: asyncpg auto-commits each statement outside one, so a mid-way failure would leave a half-applied answer
                answer_label = ""
                try:
                    if alt is not None:
                        offered = await _offerable(db, user_id, alts)
                        if not (0 <= int(alt) < len(alts)) or not any(o.get("_i") == int(alt) for o in offered):
                            raise CheckError("person_gone", 410)
                        pick = alts[int(alt)]
                        answer_label = str(pick.get("word", ""))
                        if pick.get("person_id"):
                            await omi_voices.tag(db, user_id, person_id=pick["person_id"], segment_id=seg)
                        else:
                            await _merge_into(db, user_id, seg, pick["cluster_id"])
                    elif word is not None:
                        name = " ".join(str(word).split())[:40]
                        if not name:
                            raise CheckError("empty", 400)
                        if await _forgotten(user_id, name):
                            raise CheckError("forgotten", 410)
                        answer_label = name
                        await omi_voices.tag(db, user_id, name=name, segment_id=seg)
                    elif new:
                        answer_label = WHO_UNKNOWN
                        await omi_voices.set_owner_label(db, user_id, seg, "other")
                    else:
                        raise CheckError("bad_choice", 400)
                except omi_voices.VoiceError as exc:
                    raise CheckError("voice:" + str(exc)[:40], getattr(exc, "status", 400)) from exc
                reopened = await dr.reopen_recording_in(db, user_id, row["recording_id"])     # the review reads who said what
                await db.execute("UPDATE omi_check_items SET status = 'answered', answer = ?, answered_via = ?, answered_at = ?, applied = 1 WHERE id = ? AND status IN ('open', 'answered')",
                                 ("" if answer_label == WHO_UNKNOWN else "set", via, _iso(_now()), row["id"]))
    await _after_decision(user_id)
    logger.info("TRANSCRIPT_CHECK_WHO_ANSWERED via=%s new=%d reopened=%d", via, int(bool(new)), reopened)
    return {"ok": True, "applied": True, "ledger": 0, "reopened": reopened}


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


def ledger() -> dict:
    """The per-model contender summary the night re-pass keeps (``omi_repass_eval.summarise`` over ``ledger.jsonl``): counts only, owner hits / misses included."""
    import omi_repass
    import omi_repass_eval as eu
    return {m: {k: v for k, v in o.items() if k in ("owner_hit", "owner_miss")} for m, o in eu.summarise(eu.read_ledger(omi_repass.home_dir())).items()}


# ----------------------------------------------------------------------------------------------------------- forget / erase
async def forget_user(db, user_id: str) -> int:
    n = 0
    for table in ("omi_check_items", "omi_check_asks"):
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
        for r in await _fetch(db, "SELECT id, before_text, span_text, after_text, alts, answer FROM omi_check_items WHERE user_id = ?", (user_id,)):
            # ``answer`` too, on ANSWERED rows: an owner-supplied name was never in the original sentence, so only the answer column holds it
            if rx.search(" ".join([r["before_text"], r["span_text"], r["after_text"], r["alts"] or "", r["answer"] or ""])):
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
    try:
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
    finally:
        # closing this closes the inner turn (Flue abort, tools log, brief settle) however the consumer stopped
        aclose = getattr(turn, "aclose", None)
        if aclose is not None:
            await aclose()


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


_NAMED = re.compile(r"^\W*(?:(?:that's|that is|it's|it is|this is|his name is|her name is|their name is|call (?:him|her|them)|they're|he's|she's)\s+)+", re.I)


def heard_name(text: str) -> str:
    """The name from "that's Quinn" / "it's Quinn Hart": at most 2 words, letters only, capitalised. '' when it is not a name."""
    t = _NAMED.sub("", (text or "").strip()).strip(" .,!?\"'")
    toks = t.split()
    ws = [w for w in toks if re.fullmatch(r"[A-Za-z][A-Za-z'\-]{0,24}", w)]
    if len(toks) > 2 or len(ws) != len(toks):       # a sentence is not a name: only "Quinn" / "Quinn Hart" (the prefix is already stripped)
        return ""
    return " ".join(w[:1].upper() + w[1:] for w in ws)


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
        turn = _TURNS.get(sid)
        # A pending "say it" belongs to the panel the owner pressed it on: speech at another panel (even the owner's, verified) neither fills it nor eats it.
        if listening and member and _clock() <= listening[2] and turn is not None and str(turn.panel_id or "") == listening[1]:
            _LISTEN.pop(member, None)
            async with _db() as db:
                lrow = await _item(db, member, listening[0])
            word = heard_name(text) if (lrow is not None and lrow["kind"] == "who") else heard_word(text)
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
