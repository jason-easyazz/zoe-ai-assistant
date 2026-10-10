"""commitments - Zoe keeps (or owns up to) the timed promises HER OWN replies make (``ZOE_COMMITMENTS`` = off | shadow | enforce).

Why. "I'll remind you at 5" and "I'll check back tomorrow about the dentist" created no row and no trigger: the 4B says them in
the same breath whether or not it called a tool, and nothing ever checked (gap register 2026-10-09, felt gap 8). A companion that
says "I'll remind you" and doesn't has broken the one thing a reminder is for - and the household cannot tell it happened.

What (all deterministic; no model call anywhere on this path).
  * RECORD. When a brain reply ends (``brain_dispatch``), the reply text - ZOE'S words only - is read with the per-language cue
    data in ``lexicons_data/<lang>.json`` ("commitments"): a first-person FUTURE cue ("I'll remind you", "I'll check back") AND a time
    ("at 5", "tomorrow morning", "in 20 minutes"). Both, in one sentence, or it is not a timed promise: "let me know and I'll add
    it", "want me to remind you?", "if you like I'll ping you", "I can remind you" are offers and conditions (the ``guards``). A row
    lands in ``commitments`` (migration 0044) with the due instant, the subject Zoe named (<= 80 chars), the conversation and the
    NAMES of the tools the turn actually called. NEVER the user's words: ``extract`` is given the reply and nothing else, and a turn
    that is off the record or a distress hand-off records nothing (the existing primitives, ``memory_provenance``).
  * CHECK, at the due time (``sweep``, called from the proactive slow loop, every ~5 minutes):
      remind     - a reminder exists for the member that was created around the promise and is due around the promised time
                   (kept: whether Zoe's tool made it, the router did, or the member did) -> ``kept``. Otherwise
                   - within ``FULFIL_GRACE_S`` of the due time and with a subject: she makes the reminder NOW through the existing
                     reminder path (``fulfilled``) - late, but kept;
                   - otherwise: she owns it up ONCE: a ``commitment`` candidate in the pull-not-push queue
                     ("I said I'd remind you about X and didn't - want me to now?"), delivered when the member next asks
                     "what's up?" (``owned``, then ``surfaced`` once pulled). Never pushed, never spoken unprompted.
      check_back - at record time Zoe arms the check-back herself: the same queue, held until the due time
                   (``cooldown_until``); a pull after the due time delivers it ("I said I'd check back with you about X - how's it
                   going?") -> ``kept``. Nobody asking inside ``CHECKBACK_PATIENCE_S`` closes it ``void`` (pull, not push: not
                   being asked is not a broken promise).
  * ``shadow`` (the default) records and checks and writes one ``COMMITMENT ...`` log line per event - NEVER the words - but takes no
    action: no reminder is made, no candidate armed. ``enforce`` acts. ``off`` is the kill switch.

Statuses: open -> kept | fulfilled | owned (-> surfaced | void) | missed (shadow: a breach she would have acted on) | void.
Rows are history; the sweep never deletes one. Closed rows are purged after ``RETENTION_S``; the forget cascade drops a row that
names a forgotten entity (``forget_naming``).

VOICE-PATH: the record hook runs in ``brain_dispatch``'s stream ``finally`` (every brain turn, voice included) as a background task;
it adds no await to the reply. Everything fails soft.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Optional, Sequence

from typed_env import env_str

logger = logging.getLogger(__name__)

ENV = "ZOE_COMMITMENTS"
MODES = ("off", "shadow", "enforce")
KINDS = ("remind", "check_back")
#: the tool NAMES that make a reminder (zoe-core ``reminder_create``, the Flue ``add_reminder``, ``proactive_schedule``); evidence only
REMINDER_TOOLS = frozenset({"reminder_create", "add_reminder", "proactive_schedule"})

#: a promise whose due time is closer than this to "now" is not tracked ("in a few seconds"); further than HORIZON_S is a misparse
MIN_LEAD_S = 30
HORIZON_S = 14 * 86400
#: she makes a missed reminder herself only this long after its due time; later than that she owns it up instead
FULFIL_GRACE_S = 20 * 60
#: a check-back nobody asked for inside this window closes ``void``
CHECKBACK_PATIENCE_S = 24 * 3600
#: an owning-up / check-back candidate waits in the pull queue this long
CANDIDATE_TTL_S = 3 * 86400
#: a reminder counts as "made for this promise" when it was created within this long BEFORE the reply was recorded (the tool runs
#: during the turn) and is due within DUE_TOLERANCE_S of the promised time
CREATED_BEFORE_S = 900
DUE_TOLERANCE_S = 30 * 60
#: a sweep claim older than this is retried (a crash between claim and outcome)
CLAIM_S = 240
RETENTION_S = 14 * 86400
MAX_PER_REPLY = 3
SWEEP_BATCH = 50
_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"
_DEFAULT_REMINDER_HM = (9, 0)

_pending: set = set()
_last_purge = 0.0


def mode() -> str:
    """``ZOE_COMMITMENTS``: off | shadow (default) | enforce. ``0|false|no`` = off, ``1|true|yes|on`` = enforce; anything else = shadow
    (the safe middle: it records, checks and logs, and changes nothing)."""
    raw = env_str("ZOE_COMMITMENTS", "shadow").lower()      # the name is a LITERAL here: tools/audit/flag_inventory.py reads call sites
    if raw in ("0", "false", "no", "none"):
        return "off"
    if raw in ("1", "true", "yes", "on", "active"):
        return "enforce"
    return raw if raw in MODES else "shadow"


# ── the lexicon, compiled ───────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _Lex:
    lang: str
    split: "re.Pattern[str]"
    clause: "re.Pattern[str]"
    promise: tuple                     # ((kind, (compiled, ...)), ...)
    guards: tuple
    negators: tuple
    rel: "re.Pattern[str]"
    numbers: dict
    units: dict
    clock: "re.Pattern[str]"
    ap: dict
    named: dict
    named_re: Optional["re.Pattern[str]"]
    days: dict
    weekdays: dict
    parts: dict
    part_pm: frozenset
    default: tuple
    connectors: "re.Pattern[str]"
    about_stop: frozenset
    about_trim: frozenset
    about_max: int
    say: dict


def _rx(frag: str) -> "re.Pattern[str]":
    return re.compile(frag, re.IGNORECASE | re.DOTALL)


def _word(phrase: str) -> "re.Pattern[str]":
    return re.compile(rf"(?<!\w){re.escape(phrase)}(?!\w)", re.IGNORECASE)


@lru_cache(maxsize=None)
def _lex(lang: str) -> Optional[_Lex]:
    """The compiled "commitments" section of ``lexicons_data/<lang>.json``, or None (a language with no section contributes nothing -
    it is never guessed from English)."""
    try:
        import lexicons

        sec = (lexicons.load(lang) or {}).get("commitments")
        if not sec or not sec.get("promise"):
            return None
        t = sec["time"]
        a = sec.get("about") or {}
        named = {k.lower(): tuple(v) for k, v in (t.get("named") or {}).items()}
        preps = t.get("named_preps") or []
        named_re = (_rx(rf"(?<!\w)(?:{'|'.join(preps)})\s+(?P<name>{'|'.join(re.escape(k) for k in named)})(?!\w)")
                    if named and preps else None)
        connectors = a.get("connectors") or []
        return _Lex(
            lang=lang, split=_rx(sec["sentence_split"]), clause=_rx(sec["clause_split"]),
            promise=tuple((k, tuple(_rx(f) for f in frags)) for k, frags in sec["promise"].items() if k in KINDS and frags),
            guards=tuple(_rx(f) for f in sec.get("guards") or []), negators=tuple(_rx(f) for f in sec.get("negators") or []),
            rel=_rx(t["relative"]), numbers={k.lower(): float(v) for k, v in (t.get("numbers") or {}).items()},
            units={k.lower(): int(v) for k, v in (t.get("units") or {}).items()}, clock=_rx(t["clock"]),
            ap={k.lower(): v for k, v in (t.get("ap") or {}).items()}, named=named, named_re=named_re,
            days={k.lower(): int(v) for k, v in (t.get("days") or {}).items()},
            weekdays={k.lower(): int(v) for k, v in (t.get("weekdays") or {}).items()},
            parts={k.lower(): tuple(v) for k, v in (t.get("parts") or {}).items()},
            part_pm=frozenset(p.lower() for p in t.get("part_pm") or []), default=tuple(t.get("default") or _DEFAULT_REMINDER_HM),
            connectors=_rx(rf"(?<!\w)(?P<conn>{'|'.join(connectors)})\s+(?P<rest>[^,.;!?\n]+)") if connectors else _rx(r"(?!)"),
            about_stop=frozenset(w.lower() for w in a.get("stop") or []), about_trim=frozenset(w.lower() for w in a.get("trim") or []),
            about_max=int(a.get("max_chars") or 80), say=dict(sec.get("say") or {}))
    except Exception as exc:  # noqa: BLE001 - a lexicon that does not compile contributes nothing
        logger.warning("commitments: lexicon %s unusable (%s)", lang, type(exc).__name__)
        return None


# ── the time of a promise (pure) ────────────────────────────────────────────────────────────────────────

def _num(lx: _Lex, token: str) -> Optional[float]:
    t = re.sub(r"\s+", " ", (token or "").strip().lower())
    if t.isdigit():
        return float(t)
    return lx.numbers.get(t)


def _unit_s(lx: _Lex, token: str) -> Optional[int]:
    t = (token or "").strip().lower()
    return lx.units.get(t) or lx.units.get(t[:-1] if t.endswith("s") else t)


def _blank(text: str, m: "re.Match[str]") -> str:
    return text[:m.start()] + " " * (m.end() - m.start()) + text[m.end():]


def resolve_time(sentence: str, lx: _Lex, now_local: datetime) -> Optional[datetime]:
    """The instant a sentence promises (aware, household timezone), or None when it names no usable time. Pure. Deterministic:
    a relative span ("in 20 minutes"), else a day ("tomorrow" / a weekday / "tonight"), a part of the day and/or a clock time."""
    lead = timedelta(seconds=MIN_LEAD_S)
    rel = lx.rel.search(sentence)
    if rel:
        n, secs = _num(lx, rel.group("n")), _unit_s(lx, rel.group("unit"))
        if not n or not secs or n <= 0:
            return None
        due = now_local + timedelta(seconds=n * secs)
        return due.replace(second=0, microsecond=0) if secs >= 60 else due
    work = sentence.lower()
    part: Optional[str] = None
    for phrase in sorted(lx.parts, key=len, reverse=True):
        m = _word(phrase).search(work)
        if m:
            part, work = phrase, _blank(work, m)
            break
    day_off: Optional[int] = None
    weekday: Optional[int] = None
    best = 10 ** 9
    for table, is_day in ((lx.days, True), (lx.weekdays, False)):
        for word, val in table.items():
            m = _word(word).search(work)
            if m and m.start() < best:
                best = m.start()
                day_off, weekday = (val, None) if is_day else (None, val)
    if day_off is None and weekday is None and part is not None and len(lx.parts[part]) > 2:
        day_off = int(lx.parts[part][2])
    clock = lx.clock.search(sentence)
    named = lx.named_re.search(sentence.lower()) if lx.named_re else None
    if not (clock or named or part or day_off is not None or weekday is not None):
        return None
    today = now_local.date()
    if day_off is not None:
        d = today + timedelta(days=day_off)
    elif weekday is not None:
        d = today + timedelta(days=((weekday - today.weekday()) % 7) or 7)
    else:
        d = None

    def at(date_, h_, m_):
        return datetime(date_.year, date_.month, date_.day, h_, m_, tzinfo=now_local.tzinfo)

    hh, mm = lx.default[0], lx.default[1]
    ambiguous = False
    if clock:
        g = clock.groupdict()
        hh, mm = int(g["h"]), int(g.get("m") or 0)
        if hh > 23 or mm > 59:
            return None
        ap = lx.ap.get((g.get("ap") or "")[:1].lower())
        if ap == "am":
            hh = 0 if hh == 12 else hh
        elif ap == "pm":
            hh = hh if hh == 12 else hh + 12
        elif part and part in lx.part_pm and 1 <= hh <= 11:
            hh += 12
        elif part:
            pass
        elif 1 <= hh <= 11:
            ambiguous = True
        if hh > 23:
            return None
    elif named:
        hh, mm = lx.named[named.group("name").lower()]
    elif part:
        hh, mm = lx.parts[part][0], lx.parts[part][1]
    if d is None:
        tries = [hh, hh + 12] if ambiguous and hh + 12 < 24 else [hh]
        due = None
        for h in tries:
            cand = at(today, h, mm)
            if cand > now_local + lead:
                due = cand
                break
        if due is None:
            due = at(today + timedelta(days=1), tries[0], mm)
    else:
        if ambiguous:
            hh = hh + 12 if hh <= 6 else hh
        due = at(d, hh, mm)
        if d == today and ambiguous and due <= now_local + lead and hh + 12 < 24 and hh < 12:
            due = at(d, hh + 12, mm)
    if due <= now_local + lead or (due - now_local).total_seconds() > HORIZON_S:
        return None
    return due


# ── extraction (pure: Zoe's reply in, promises out) ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class Promise:
    kind: str            # "remind" | "check_back"
    due: datetime        # aware, UTC
    about: str           # "about the dentist" / "to call your mum" / ""  (Zoe's own words, <= 80 chars)
    lang: str


def _about(sentence: str, start: int, lx: _Lex) -> str:
    m = lx.connectors.search(sentence, start)
    if not m:
        return ""
    rest = m.group("rest")
    for rx in (lx.rel, lx.clock):
        rest = rx.sub(" ", rest)
    if lx.named_re:
        rest = lx.named_re.sub(" ", rest)
    low = rest
    for word in [*lx.days, *lx.weekdays, *sorted(lx.parts, key=len, reverse=True)]:
        low = _word(word).sub(" ", low)
    toks = low.split()
    while toks and toks[-1].lower().strip(".,") in lx.about_trim:
        toks.pop()
    if not toks or all(t.lower().strip(".,") in lx.about_stop for t in toks):
        return ""
    text = " ".join(toks).strip(" -")
    if len(text) < 3:
        return ""
    return f"{m.group('conn').lower()} {text}"[:lx.about_max].rstrip()


def extract(reply: str, *, now: Optional[datetime] = None) -> list:
    """The timed promises in ZOE'S reply text. Pure; deterministic. Reads nothing but ``reply`` (so a promise-shaped sentence in the
    USER's turn can never be recorded: this is not given it)."""
    text = (reply or "").strip()
    if not text:
        return []
    try:
        import lexicons
        from time_utils import zoe_timezone

        now_utc = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        now_local = now_utc.astimezone(zoe_timezone())
        # the detected language first; a short reply detects as English with no evidence, so every other language that has a
        # "commitments" section gets a turn (the cues are language-specific regexes, so a cross-language hit is not a risk)
        detected = lexicons.detect(text)
        for code in (detected, *[c for c in lexicons.LANGS if c != detected]):
            lx = _lex(code)
            out = _extract_with(text, lx, now_local) if lx is not None else []
            if out:
                return out
        return []
    except Exception as exc:  # noqa: BLE001 - extraction never breaks a turn
        logger.debug("commitments: extract failed (%s)", type(exc).__name__)
        return []


def _extract_with(text: str, lx: _Lex, now_local: datetime) -> list:
    """One language's pass over Zoe's reply."""
    out: list = []
    for sent in lx.split.split(text):
        sent = (sent or "").strip()
        if not sent or sent.endswith("?") or any(g.search(sent) for g in lx.guards):
            continue
        for kind, rxs in lx.promise:
            m = next((mm for mm in (rx.search(sent) for rx in rxs) if mm), None)
            if m is None:
                continue
            cue_start = m.start()
            clause = next((c for c in _clauses(lx, sent) if c[0] <= cue_start < c[1]), (0, len(sent)))
            if any(n.search(sent[clause[0]:clause[1]]) for n in lx.negators):
                break
            due = resolve_time(sent, lx, now_local)
            if due is None:
                break
            p = Promise(kind, due.astimezone(timezone.utc), _about(sent, m.end(), lx), lx.lang)
            if not any(q.kind == p.kind and q.due == p.due for q in out):
                out.append(p)
            break
        if len(out) >= MAX_PER_REPLY:
            break
    return out


def _clauses(lx: _Lex, sent: str) -> list:
    spans, last = [], 0
    for m in lx.clause.finditer(sent):
        spans.append((last, m.start()))
        last = m.end()
    spans.append((last, len(sent)))
    return spans


def title_of(about: str, lx: Optional[_Lex]) -> str:
    """The reminder title for a promise's subject: ``about`` without its connector, first letter capitalised ('' when none)."""
    if not about or lx is None:
        return ""
    t = lx.connectors.sub(lambda m: m.group("rest"), about, count=1).strip()
    return (t[:1].upper() + t[1:]) if t else ""


# ── recording ───────────────────────────────────────────────────────────────────────────────────────────

def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_TS_FMT)


def _parse_iso(text: str) -> Optional[datetime]:
    try:
        return datetime.strptime(str(text), _TS_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _walled(user_id: str, message: str) -> str:
    """Why a reply's promises must NOT be recorded ('' = record): a guest / unregistered id, an off-the-record turn, a distress turn."""
    import memory_provenance as mp

    if not mp.is_tracked(user_id):
        return "untracked"
    try:
        if message and mp._distress(message):
            return "distress"
        if (message and mp.is_off_record(user_id, message)) or mp.reply_is_off_record(user_id):
            return "off_record"
    except Exception:  # noqa: BLE001 - when the walls cannot be asked, nothing is recorded
        return "wall_error"
    return ""


def schedule_record(user_id: str, session_id: str, reply_id: str, reply: str, message: str = "",
                    tools: Sequence[str] = (), *, now: Optional[datetime] = None) -> bool:
    """A brain reply ended: record its timed promises in the background. True when a task was scheduled. Never raises, never waits.
    ``message`` (the user's turn) is read ONLY by the off-record / distress walls - never for a promise."""
    try:
        md = mode()
        if md == "off" or not (reply or "").strip():
            return False
        why = _walled(user_id, message)
        if why:
            if why in ("off_record", "distress"):
                logger.info("COMMITMENT mode=%s event=skipped reason=%s", md, why)
            return False
        promises = extract(reply, now=now)
        if not promises:
            return False
        loop = asyncio.get_running_loop()
        task = loop.create_task(record(user_id, session_id, reply_id or uuid.uuid4().hex[:16], promises, tools, now=now))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
        return True
    except RuntimeError:        # no running loop
        return False
    except Exception:  # noqa: BLE001
        return False


async def flush() -> None:
    """Await the recordings in flight (tests; a clean shutdown)."""
    if _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


def _say(lang: str, key: str, about: str) -> str:
    lx = _lex(lang)
    tpl = (lx.say.get(key) if lx else "") or ""
    return tpl.replace("{about}", f"{about} " if about else "").strip()


async def record(user_id: str, session_id: str, reply_id: str, promises: Sequence[Promise], tools: Sequence[str] = (), *,
                 now: Optional[datetime] = None) -> int:
    """Insert one row per promise (idempotent on (user, session, reply, kind, due)); in ``enforce`` also arm a check-back's candidate.
    Returns the rows inserted. Never raises."""
    md = mode()
    if md == "off":
        return 0
    made = 0
    try:
        from db_compat import get_compat_db

        when = now or datetime.now(timezone.utc)
        stamp = _iso(when)
        tool_json = json.dumps([str(t)[:60] for t in tools][:12])
        has_tool = int(any(t in REMINDER_TOOLS for t in tools))
        async with get_compat_db() as db:
            for p in promises:
                cid = uuid.uuid4().hex
                cur = await db.execute(
                    "INSERT INTO commitments (id, user_id, session_id, reply_id, kind, due_at, about, lang, tools, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?) ON CONFLICT (user_id, session_id, reply_id, kind, due_at) DO NOTHING",
                    (cid, user_id, session_id or "", reply_id, p.kind, _iso(p.due), p.about, p.lang, tool_json, stamp))
                if (getattr(cur, "rowcount", 1) or 0) < 1:
                    continue
                made += 1
                logger.info("COMMITMENT mode=%s event=recorded kind=%s lang=%s due_in_s=%d tool=%d about=%d", md, p.kind, p.lang,
                            int((p.due - when).total_seconds()), has_tool, int(bool(p.about)))
                if md == "enforce" and p.kind == "check_back":
                    await _arm_candidate(db, user_id, cid, "checkback", p, due_hold=True, now=when)
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - a missing table (0044 not applied) or a DB blip costs only the record
        logger.debug("commitments: record failed (%s)", type(exc).__name__)
    return made


async def _arm_candidate(db, user_id: str, cid: str, say_key: str, p: Promise, *, due_hold: bool, now: datetime) -> str:
    """Put Zoe's own sentence in the pull queue (``kind='commitment'``). A check-back is held until its due time (``cooldown_until``);
    an owning-up is pending at once. Idempotent on (user, kind, source_ref). Returns the candidate id ('' on failure)."""
    text = _say(p.lang, say_key, p.about)
    if not text:
        return ""
    stamp = _iso(now)
    expires = _iso(max(now, p.due) + timedelta(seconds=CANDIDATE_TTL_S))
    cand = uuid.uuid4().hex
    await db.execute(
        "INSERT INTO proactive_candidates (id, user_id, kind, source_ref, text, hint, salience, on_open, cue_words, expires_at, "
        "cooldown_until, surfaced_count, created_at, updated_at) VALUES (?, ?, 'commitment', ?, ?, '', 0.95, 0, '', ?, ?, 0, ?, ?) "
        "ON CONFLICT (user_id, kind, source_ref) DO NOTHING",
        (cand, user_id, f"commitments:{cid}", text, expires, _iso(p.due) if due_hold else None, stamp, stamp))
    return cand


# ── the due-time check ──────────────────────────────────────────────────────────────────────────────────

def _parse_created(text: Any) -> Optional[datetime]:
    """A ``reminders.created_at`` (``NOW()::TEXT``: '2026-10-10 03:12:44.123456+00' / ISO) as an aware datetime, or None."""
    s = str(text or "").strip().replace(" ", "T", 1)
    if not s:
        return None
    s = re.sub(r"([+-]\d{2})$", r"\1:00", s)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _reminder_due(row: Any, tz, fallback_date) -> Optional[datetime]:
    due_date = str(row["due_date"] or "").strip()[:10]
    due_time = str(row["due_time"] or "").strip()
    try:
        d = datetime.strptime(due_date, "%Y-%m-%d").date() if due_date else fallback_date
        hm = datetime.strptime(due_time[:5], "%H:%M") if due_time else datetime(2000, 1, 1, *_DEFAULT_REMINDER_HM)
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, hm.hour, hm.minute, tzinfo=tz)


async def reminder_covers(db, user_id: str, created: datetime, due: datetime) -> Optional[str]:
    """The id of a reminder of ``user_id`` made for this promise - created within ``CREATED_BEFORE_S`` before the promise was recorded
    (or after it) and due within ``DUE_TOLERANCE_S`` of ``due`` - or None. A reminder the member deleted since counts (their choice,
    not a broken promise); acknowledged and fired ones count. Reads the member's recent reminders only."""
    from time_utils import zoe_timezone

    tz = zoe_timezone()
    cur = await db.execute(
        "SELECT id, due_date, due_time, created_at FROM reminders WHERE user_id = ? ORDER BY created_at DESC LIMIT 60", (user_id,))
    for r in await cur.fetchall():
        made = _parse_created(r["created_at"])
        if made is None or made < created - timedelta(seconds=CREATED_BEFORE_S):
            continue
        rd = _reminder_due(r, tz, due.astimezone(tz).date())
        if rd is not None and abs((rd - due).total_seconds()) <= DUE_TOLERANCE_S:
            return str(r["id"])
    return None


async def _make_reminder(db, user_id: str, title: str, now: datetime) -> str:
    """Make the reminder through the same table and scheduler a tool-made one uses (``reminder_service.create_reminder_record``'s row;
    ``proactive.triggers.reminders.schedule_reminder``): due a minute from now, so it fires on the next tick. '' on failure."""
    from time_utils import zoe_timezone

    local = (now + timedelta(minutes=1)).astimezone(zoe_timezone())
    rid = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO reminders (id, user_id, title, description, reminder_type, category, priority, due_date, due_time, "
        "recurring_pattern, is_active, acknowledged, snoozed_until, visibility, deleted) "
        "VALUES (?, ?, ?, '', 'one-time', 'general', 'normal', ?, ?, NULL, 1, 0, NULL, 'personal', 0)",
        (rid, user_id, title, local.strftime("%Y-%m-%d"), local.strftime("%H:%M")))
    await db.commit()
    try:
        from proactive.triggers.reminders import schedule_reminder

        await schedule_reminder(user_id=user_id, message=title, send_at=now + timedelta(seconds=45), item_id=rid)
    except Exception as exc:  # noqa: BLE001 - the scan loop picks the row up if the direct schedule failed
        logger.debug("commitments: direct schedule failed (%s)", type(exc).__name__)
    return rid


def _log_check(md: str, kind: str, verdict: str, late_s: float) -> None:
    logger.info("COMMITMENT mode=%s event=check kind=%s verdict=%s late_s=%d", md, kind, verdict, int(late_s))


async def _close(db, cid: str, status: str, resolution: str, now: datetime, *, reminder_id: str = "", candidate_id: str = "") -> None:
    await db.execute(
        "UPDATE commitments SET status = ?, resolution = ?, checked_at = ?, reminder_id = COALESCE(NULLIF(?, ''), reminder_id), "
        "candidate_id = COALESCE(NULLIF(?, ''), candidate_id) WHERE id = ?",
        (status, resolution, _iso(now), reminder_id, candidate_id, cid))


async def _expire_candidate(db, user_id: str, cid: str, now: datetime) -> None:
    await db.execute("UPDATE proactive_candidates SET expires_at = ? WHERE user_id = ? AND kind = 'commitment' AND source_ref = ?",
                     (_iso(now), user_id, f"commitments:{cid}"))


async def _candidate_state(db, user_id: str, cid: str) -> Optional[tuple]:
    cur = await db.execute("SELECT surfaced_count, expires_at FROM proactive_candidates WHERE user_id = ? AND kind = 'commitment' "
                           "AND source_ref = ?", (user_id, f"commitments:{cid}"))
    r = await cur.fetchone()
    return (int(r[0] or 0), str(r[1] or "")) if r else None


async def sweep(*, now: Optional[datetime] = None) -> dict:
    """Check every promise that is due. Called from the proactive slow loop. Returns ``{verdict: count}`` ({} when off / nothing due).
    Never raises."""
    global _last_purge
    md = mode()
    counts: dict = {}
    if md == "off":
        return counts
    try:
        import memory_provenance as mp
        from db_compat import get_compat_db

        now = now or datetime.now(timezone.utc)
        stamp, claim_before = _iso(now), _iso(now - timedelta(seconds=CLAIM_S))
        async with get_compat_db() as db:
            cur = await db.execute(
                "SELECT id, user_id, kind, due_at, about, lang, status, created_at FROM commitments "
                "WHERE status IN ('open', 'owned') AND due_at <= ? ORDER BY due_at LIMIT ?", (stamp, SWEEP_BATCH))
            rows = list(await cur.fetchall())
            for r in rows:
                cid, uid, kind, status = str(r["id"]), str(r["user_id"]), str(r["kind"]), str(r["status"])
                due = _parse_iso(r["due_at"]) or now
                created = _parse_iso(r["created_at"]) or due
                late = (now - due).total_seconds()
                claimed = await db.execute(
                    "UPDATE commitments SET checked_at = ? WHERE id = ? AND status = ? AND (checked_at IS NULL OR checked_at < ?)",
                    (stamp, cid, status, claim_before))
                if (getattr(claimed, "rowcount", 1) or 0) < 1:
                    continue
                verdict = ""
                try:
                    if not mp.is_tracked(uid):
                        await _close(db, cid, "void", "untracked", now)
                        verdict = "void"
                    elif status == "owned":
                        verdict = await _check_owned(db, uid, cid, now)
                    elif kind == "remind":
                        verdict = await _check_remind(db, md, r, uid, cid, due, created, late, now)
                    else:
                        verdict = await _check_back(db, md, r, uid, cid, due, late, now)
                except Exception as exc:  # noqa: BLE001 - one bad row never stops the sweep; its claim lapses and it is retried
                    logger.debug("commitments: check failed (%s)", type(exc).__name__)
                    continue
                await db.commit()
                if verdict:
                    counts[verdict] = counts.get(verdict, 0) + 1
                    _log_check(md, kind, verdict, late)
            t = time.time()
            if t - _last_purge >= 3600:
                _last_purge = t
                await db.execute("DELETE FROM commitments WHERE status NOT IN ('open', 'owned') AND created_at < ?",
                                 (_iso(now - timedelta(seconds=RETENTION_S)),))
                await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.debug("commitments: sweep failed (%s)", type(exc).__name__)
    return counts


async def _check_remind(db, md, r, uid, cid, due, created, late, now) -> str:
    rid = await reminder_covers(db, uid, created, due)
    if rid:
        await _close(db, cid, "kept", "reminder_exists", now, reminder_id=rid)
        return "kept"
    lx = _lex(str(r["lang"]))
    title = title_of(str(r["about"] or ""), lx)
    can_fulfil = bool(title) and late <= FULFIL_GRACE_S
    if md != "enforce":
        await _close(db, cid, "missed", "shadow:would_fulfil" if can_fulfil else "shadow:would_ownup", now)
        return "would_fulfil" if can_fulfil else "would_ownup"
    if can_fulfil:
        new_id = await _make_reminder(db, uid, title, now)
        if new_id:
            await _close(db, cid, "fulfilled", "reminder_made_late", now, reminder_id=new_id)
            return "fulfilled"
    p = Promise("remind", due, str(r["about"] or ""), str(r["lang"]))
    cand = await _arm_candidate(db, uid, cid, "ownup_remind", p, due_hold=False, now=now)
    if not cand:
        await _close(db, cid, "void", "no_sentence", now)
        return "void"
    await _close(db, cid, "owned", "queued_for_pull", now, candidate_id=cand)
    return "owned"


async def _check_back(db, md, r, uid, cid, due, late, now) -> str:
    if md != "enforce":
        await _close(db, cid, "void", "shadow:check_back_not_armed", now)
        return "would_arm"
    st = await _candidate_state(db, uid, cid)
    if st is None:
        await _close(db, cid, "void", "candidate_gone", now)
        return "void"
    if st[0] >= 1:
        await _expire_candidate(db, uid, cid, now)
        await _close(db, cid, "kept", "pulled", now)
        return "kept"
    if late > CHECKBACK_PATIENCE_S:
        await _expire_candidate(db, uid, cid, now)
        await _close(db, cid, "void", "never_asked", now)
        return "void"
    return ""       # still waiting for the member to ask; checked again next sweep


async def _check_owned(db, uid, cid, now) -> str:
    st = await _candidate_state(db, uid, cid)
    if st is not None and st[0] >= 1:
        await _expire_candidate(db, uid, cid, now)       # said once: never again
        await _close(db, cid, "surfaced", "pulled", now)
        return "surfaced"
    if st is None or st[1] <= _iso(now):
        await _close(db, cid, "void", "ownup_expired", now)
        return "void"
    return ""


# ── the forget cascade ──────────────────────────────────────────────────────────────────────────────────

async def forget_user(user_id: str) -> int:
    """Drop every commitment of a member (the right-to-be-forgotten path, ``MemoryService.delete_user``). Count; 0 on failure."""
    try:
        from db_compat import get_compat_db

        async with get_compat_db() as db:
            cur = await db.execute("DELETE FROM commitments WHERE user_id = ?", (user_id,))
            await db.commit()
            return int(getattr(cur, "rowcount", 0) or 0)
    except Exception:  # noqa: BLE001
        return 0


async def erase_entity(user_id: str, rx: "re.Pattern[str]") -> int:
    """Delete the member's commitments whose subject names the forgotten entity (``rx``, the cascade's whole-word name pattern). The
    matching candidates in the pull queue are the cascade's own ``proactive_candidates`` step. Returns the count; 0 on any failure."""
    try:
        from db_compat import get_compat_db

        n = 0
        async with get_compat_db() as db:
            cur = await db.execute("SELECT id, about FROM commitments WHERE user_id = ?", (user_id,))
            for r in await cur.fetchall():
                if r["about"] and rx.search(str(r["about"])):
                    await db.execute("DELETE FROM commitments WHERE id = ?", (str(r["id"]),))
                    n += 1
            if n:
                await db.commit()
        return n
    except Exception:  # noqa: BLE001
        return 0
