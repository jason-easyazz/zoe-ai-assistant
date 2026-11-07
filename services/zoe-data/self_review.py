"""self_review - Zoe reviews her OWN real conversations at night, on the 12B (flag-dark ``ZOE_SELF_REVIEW=off|shadow|on``).

The human-supervised loop (an owner session read turn by turn: what was asked, what Zoe did, what she said, what went wrong, what
to fix) made into a nightly job. Phase 1, contract: ``docs/knowledge/self-review.md``.

    gather     real household turns since the last night (chat_messages through the app's own SQL, the reply from the same table or,
               when the save was lost, from the Flue session store read-only), joined with the log receipts: VOICE TIMING (path,
               first-token latency), WRITE_CLAIM_BLOCKED (claim guard), the STT log (audio length), struggle_events
    exclude    harness / probe / ci / demo traffic (the row's server-side ``traffic_class``, synthetic ids, harness panels), off-the-record
               rows, distress turns, turns that name a forgotten entity (counted, never read further)
    flag       deterministic rules only (``flag_turn``): misroute, garbled STT, a claimed write with no tool, an empty reply, an ack the
               next turn contradicts, a rephrase-retry within 60 s, an emotional misread of a short utterance, "I can't"
    review     the 12B writes a short review per flagged turn (likely cause + a fix class from a fixed list) inside a time AND token
               budget; a turn the budget does not reach keeps its deterministic flags (status ``no_time``)
    write      PRIVATE: ``~/.zoe/self-review/<night>.md`` + ``<night>.self-review.jsonl`` (0600; household words stay on the box);
               counts-only: ``<night>.self-review-summary.json`` (classes, counts, example ids) which capability_gap_backlog reads;
               mode ``on`` also: ``struggle_events`` rows (signal ``SELF_REVIEW``, no words), neutral synthetic paraphrases of
               style failures for the self-improver (``~/.zoe/self-improver/candidate-cells.jsonl``), and one counts-only line in
               the owner's morning brief (``brief_line``)

Privacy (the rules are code, tested): the full review with quotes is for the OWNER's own turns only. Another adult's turn is
flagged and counted, never quoted, never sent to the model. A child's turn is reviewed under the kid-safe rubric, never quoted in
the file or any summary, and a review that echoes the child's words is withheld. Guests and unverified voices are counted only.
Forget: ``erase_entity`` (the forget cascade's ``self_review`` step) drops every record of the member naming the entity;
``delete_user`` (via ``struggle_detector.delete_user``) drops all of a member's records; ``load_reviews`` re-checks the ledger
on every read (a serve-time wall).
"""
from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import datetime as dt
import fcntl
import glob
import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

MODES = ("off", "shadow", "on")
FLAG = "ZOE_SELF_REVIEW"

# the failure classes (fixed vocabulary: every summary label comes from here, never from an utterance)
MISROUTE = "misroute"
GARBLED_STT = "garbled_stt"
CLAIM_NO_TOOL = "claim_no_tool"
EMPTY_REPLY = "empty_reply"
ACK_CONTRADICTED = "ack_contradicted"
REPHRASE_RETRY = "rephrase_retry"
EMOTION_MISREAD = "emotion_misread"
CANT_DO = "cant_do"
CLASSES = (MISROUTE, GARBLED_STT, CLAIM_NO_TOOL, EMPTY_REPLY, ACK_CONTRADICTED, REPHRASE_RETRY, EMOTION_MISREAD, CANT_DO)
CLASS_WORDS = {MISROUTE: "went down the wrong path", GARBLED_STT: "misheard", CLAIM_NO_TOOL: "claimed a change that did not happen",
               EMPTY_REPLY: "gave no reply", ACK_CONTRADICTED: "said done and was told it was not",
               REPHRASE_RETRY: "had to be asked again", EMOTION_MISREAD: "read feelings into a short remark", CANT_DO: "said I could not"}
#: highest first: which flagged turns get the model's time when the budget is short
SEVERITY = {CLAIM_NO_TOOL: 9, ACK_CONTRADICTED: 8, EMPTY_REPLY: 7, CANT_DO: 6, MISROUTE: 5, REPHRASE_RETRY: 4, GARBLED_STT: 3, EMOTION_MISREAD: 3}
#: what the model may name as the fix (anything else is "unclear")
FIX_CLASSES = ("router_training", "stt", "capability_gap", "claim_guard", "answer_quality", "manner", "latency", "none", "unclear")
#: classes that are about HOW Zoe answers (her manner): the self-improver's candidate cells come from these
STYLE_CLASSES = frozenset({EMOTION_MISREAD})
STYLE_FIXES = frozenset({"manner"})
#: which classes are a capability gap for capability_gap (the rest are hearing / manner problems, not missing abilities)
GAP_CLASSES = frozenset({MISROUTE, CLAIM_NO_TOOL, ACK_CONTRADICTED, REPHRASE_RETRY, CANT_DO, EMPTY_REPLY})

ROLE_OWNER, ROLE_ADULT, ROLE_CHILD = "owner", "adult", "child"
RECORDS_SUFFIX = ".self-review.jsonl"
SUMMARY_SUFFIX = ".self-review-summary.json"
CANDIDATES_FILE = "candidate-cells.jsonl"
STRUGGLE_SIGNAL = "SELF_REVIEW"
STRUGGLE_FIX = "self_review"          # not a struggle_proposals fix type: these rows never become proposals by themselves

RETRY_WINDOW_S = 60.0
ACK_WINDOW_S = 120.0
CLAIM_WINDOW_S = 90.0
PAIR_WINDOW_S = 180.0
MAX_LOOKBACK_H = 36.0
MAX_TURNS = 4000
REPLY_CLIP = 600


# ── settings ─────────────────────────────────────────────────────────────────

def mode() -> str:
    try:
        from typed_env import flag

        m = str(flag(FLAG)).strip().lower()
    except Exception:  # noqa: BLE001 - the registry unavailable is off (fail dark)
        m = "off"
    return m if m in MODES else "off"


def _env_int(key: str, default: int) -> int:
    try:
        from typed_env import env_int

        return env_int(key, default)
    except Exception:  # noqa: BLE001
        return default


def state_dir() -> Path:
    raw = os.environ.get("ZOE_SELF_REVIEW_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".zoe" / "self-review"


def improver_dir() -> Path:
    raw = os.environ.get("ZOE_SELF_REVIEW_IMPROVER_DIR", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".zoe" / "self-improver"


def max_calls() -> int:
    return max(0, _env_int("ZOE_SELF_REVIEW_MAX_CALLS", 20))


def max_tokens() -> int:
    """The night's OUTPUT token cap across every review and paraphrase call (each call is charged its full allowance up front)."""
    return max(0, _env_int("ZOE_SELF_REVIEW_MAX_TOKENS", 4000))


def _tz():
    try:
        from time_utils import zoe_timezone

        return zoe_timezone()
    except Exception:  # noqa: BLE001
        return dt.timezone.utc


# ── the turn ─────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class Turn:
    id: str
    session_id: str
    user_id: str
    ts: float
    text: str
    reply: str = ""
    reply_id: str = ""
    reply_source: str = ""             # chat | flue | ""
    channel: str = "chat"              # voice | telegram | chat
    panel_id: str = ""
    traffic: str = "household"
    speaker_verified: Optional[bool] = None
    truncated: bool = False
    latency_ms: Optional[int] = None
    path: str = ""
    claim_blocked: bool = False
    audio_s: Optional[float] = None
    struggles: list = dataclasses.field(default_factory=list)      # [{"signal", "fix_type", "turn_id", "prev_turn_id", "ts"}]
    role: str = ""
    flags: list = dataclasses.field(default_factory=list)


def channel_of(session_id: str) -> tuple[str, str]:
    """``(channel, panel_id)`` from a session id (``voice-panel-<panel>-<hex8>`` is the voice lane)."""
    sid = session_id or ""
    m = re.match(r"^voice-panel-(.+)-[0-9a-f]{8}$", sid)
    if m:
        return "voice", m.group(1)
    if sid.startswith("voice"):
        return "voice", ""
    if sid.startswith(("tg-", "telegram")):
        return "telegram", ""
    return "chat", ""


def _meta(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        d = json.loads(raw or "{}")
        return d if isinstance(d, dict) else {}
    except (TypeError, ValueError):
        return {}


def pair_rows(rows: Iterable[dict]) -> list[Turn]:
    """Chat rows (``id, session_id, role, content, metadata, ts, owner``), any order -> one ``Turn`` per user turn with the
    assistant reply that followed it in its session (before the next user turn). The voice lane saves one user turn twice (its
    start and with the reply): a repeat of the same words within ``PAIR_WINDOW_S`` in the session is the same turn."""
    by_session: dict[str, list[dict]] = {}
    for r in rows:
        by_session.setdefault(str(r.get("session_id") or ""), []).append(r)
    out: list[Turn] = []
    for sid, rs in by_session.items():
        rs.sort(key=lambda r: (float(r.get("ts") or 0.0), 0 if r.get("role") == "user" else 1))
        cur: Optional[Turn] = None
        for r in rs:
            meta = _meta(r.get("metadata"))
            if r.get("role") == "user":
                text = str(r.get("content") or "").strip()
                if cur is not None and not cur.reply and text == cur.text and float(r["ts"]) - cur.ts <= PAIR_WINDOW_S:
                    continue
                ch, panel = channel_of(sid)
                uid = str(r.get("owner") or meta.get("user_id") or "guest")
                cur = Turn(id=str(r.get("id") or ""), session_id=sid, user_id=uid, ts=float(r.get("ts") or 0.0), text=text, channel=ch,
                           panel_id=panel, traffic=str(meta.get("traffic_class") or "household").lower(),
                           speaker_verified=False if meta.get("speaker_verified") is False else None)
                out.append(cur)
            elif r.get("role") == "assistant" and cur is not None and not cur.reply_id:
                cur.reply = str(r.get("content") or "").strip()
                cur.reply_id = str(r.get("id") or "")
                cur.reply_source = "chat"
                cur.truncated = bool(meta.get("truncated"))
    out.sort(key=lambda t: t.ts)
    return out


# ── exclusion ────────────────────────────────────────────────────────────────

def exclusion(t: Turn) -> str:
    """Why this turn is NOT household (``""`` = it is): our own traffic never enters a review."""
    try:
        import traffic_class as tc

        if t.traffic in tc.NON_HOUSEHOLD:
            return t.traffic
        cls = tc.class_for_user(t.user_id) or tc.class_for_panel(t.panel_id)
        if cls:
            return cls
    except Exception:  # noqa: BLE001
        if t.traffic in ("harness", "probe", "ci"):
            return t.traffic
    low = (t.user_id or "").lower()
    if low.startswith(("demo_", "demo-", "bench", "probe", "test_", "test-", "ci_", "ci-", "e2e")):
        return "demo"           # a demo id an operator re-admitted as a person is still a demo for this review
    if t.panel_id.lower().startswith(("replay", "test", "probe", "ci-", "e2e", "bench", "demo")):
        return "probe"
    return ""


def is_guestish(t: Turn) -> bool:
    try:
        from user_filters import GUEST_USERS

        guests = set(GUEST_USERS)
    except Exception:  # noqa: BLE001
        guests = {"guest", "anonymous", "voice-guest", "voice-daemon", ""}
    return t.user_id in guests or t.user_id.startswith("guest") or t.speaker_verified is False


# ── the deterministic rules ──────────────────────────────────────────────────

_WORD = re.compile(r"[a-z0-9']+")
_STOP = frozenset("a an the and or to of in on at for is it its this that be me my you your i we our please can could would will "
                  "zoe hey ok okay so just".split())
ACK_RE = re.compile(r"\b(?:done|i(?:'ve| have) (?:added|set|turned|put|saved|created|scheduled|booked|sent|started|stopped|cancelled|removed)|"
                    r"(?:added|set|saved|scheduled|turned (?:on|off)|reminder set)\b)", re.I)
CONTRA_RE = re.compile(r"\b(?:you didn'?t|it didn'?t|didn'?t (?:work|happen)|it(?:'s| is) not (?:there|on|off|working|done)|that(?:'s| is) not|"
                       r"nothing happened|still (?:on|off|not)|no it (?:isn'?t|didn'?t|is not)|not done|it isn'?t|you haven'?t)\b", re.I)
EMPATHY_RE = re.compile(r"\b(?:i'?m (?:so )?sorry (?:to hear|you(?:'re| are))|that sounds (?:hard|tough|difficult|rough|frustrating)|"
                        r"i'?m here for you|it'?s (?:okay|ok) to feel|how are you feeling|sounds like you(?:'re| are) (?:feeling|going through)|"
                        r"take care of yourself|be gentle with yourself)\b", re.I)
AFFECT_RE = re.compile(r"\b(?:sad|tired|exhausted|stress\w*|upset|angry|mad|lonely|worried|anxious|scared|afraid|hurt\w*|cry\w*|depress\w*|"
                       r"miserable|awful|terrible|horrible|frustrat\w*|annoyed|hate|grief|griev\w*|sick|ill|unwell|bad day|rough|overwhelm\w*)\b", re.I)


def _tokens(text: str) -> frozenset:
    return frozenset(w for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 1)


def _words(text: str) -> list[str]:
    return _WORD.findall((text or "").lower())


def garbled(t: Turn) -> bool:
    """A transcript that looks misheard: the struggle detector said so, a word repeated 3+ times in a row, far too few characters
    for the audio's length, or mostly one/two-letter fragments."""
    if any(s.get("signal") == "MISHEAR" or s.get("fix_type") in ("stt", "stt_snapped") for s in t.struggles if s.get("turn_id") == t.id):
        return True
    if t.channel != "voice":
        return False
    ws = _words(t.text)
    if any(ws[i] == ws[i + 1] == ws[i + 2] for i in range(len(ws) - 2)):
        return True
    if t.audio_s and t.audio_s >= 2.0 and len(t.text) / t.audio_s < 2.0:
        return True
    return len(ws) >= 4 and sum(1 for w in ws if len(w) <= 2) / len(ws) >= 0.6


def flag_turn(t: Turn, nxt: Optional[Turn]) -> list[str]:
    """The failure classes of ONE turn, from code alone (``nxt`` = the same member's next turn, if any)."""
    flags: list[str] = []
    mine = [s for s in t.struggles if s.get("turn_id") == t.id or s.get("prev_turn_id") == t.id]
    gap = (nxt.ts - t.ts) if nxt is not None else None
    if any(s.get("fix_type") == "router_training" or s.get("signal") == "FALLTHROUGH" for s in mine):
        flags.append(MISROUTE)
    if garbled(t):
        flags.append(GARBLED_STT)
    if t.claim_blocked:
        flags.append(CLAIM_NO_TOOL)
    if not t.reply.strip() or t.truncated:
        flags.append(EMPTY_REPLY)
    if nxt is not None and gap is not None and gap <= ACK_WINDOW_S and ACK_RE.search(t.reply or "") and CONTRA_RE.search(nxt.text or ""):
        flags.append(ACK_CONTRADICTED)
    retry = any(s.get("signal") in ("RE_ASK", "REPHRASE") and s.get("prev_turn_id") == t.id for s in t.struggles)
    if not retry and nxt is not None and gap is not None and gap <= RETRY_WINDOW_S:
        a, b = _tokens(t.text), _tokens(nxt.text)
        retry = len(a) >= 2 and len(b) >= 2 and len(a & b) / len(a | b) >= 0.5
    if retry:
        flags.append(REPHRASE_RETRY)
    if len(_words(t.text)) <= 4 and not AFFECT_RE.search(t.text or "") and EMPATHY_RE.search(t.reply or ""):
        flags.append(EMOTION_MISREAD)
    if any(s.get("signal") == "CANT_DO" for s in mine):
        flags.append(CANT_DO)
    return flags


def flag_all(turns: list[Turn]) -> None:
    """Fill ``Turn.flags`` for every turn (the next turn is the same member's, in the same channel)."""
    last: dict[tuple[str, str], Turn] = {}
    nxt_of: dict[int, Turn] = {}
    for t in turns:
        k = (t.user_id, t.channel)
        if k in last:
            nxt_of[id(last[k])] = t
        last[k] = t
    for t in turns:
        t.flags = flag_turn(t, nxt_of.get(id(t)))


# ── the log receipts ─────────────────────────────────────────────────────────

_LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}) +\w+ +(.*)$")
_TIMING = re.compile(r"VOICE TIMING .*?\bsession=(\S+) path=(\S+)(.*)$")
_CLAIM = re.compile(r"WRITE_CLAIM_BLOCKED .*?\buser=(\S+)")
_TRAFFIC = re.compile(r"\btraffic=(\w+)")


def parse_log(lines: Iterable[str], since: float, until: float) -> dict:
    """``{"timing": [(ts, session, path, ms)], "claims": [(ts, user)]}`` from service-log lines in [since, until). Lines of
    non-household traffic are skipped. Counts and ids only are kept."""
    timing, claims = [], []
    for line in lines:
        if "VOICE TIMING" not in line and "WRITE_CLAIM_BLOCKED" not in line:
            continue
        m = _LINE.match(line.rstrip("\n"))
        if not m:
            continue
        try:
            ts = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S%z").timestamp()
        except ValueError:
            continue
        if not since <= ts < until + CLAIM_WINDOW_S:
            continue
        body = m.group(2)
        if any(x.lower() in ("harness", "probe", "ci") for x in _TRAFFIC.findall(body)):
            continue
        if (t := _TIMING.search(body)):
            nums = dict(re.findall(r"\b(\w+_ms)=(\d+)", t.group(3)))
            ms = int(nums.get("pre_brain_ms", 0)) + int(nums.get("brain_ttft_ms", 0)) if nums else None
            timing.append((ts, t.group(1), t.group(2), ms))
        elif (c := _CLAIM.search(body)):
            claims.append((ts, c.group(1)))
    return {"timing": timing, "claims": claims}


def read_log_segments(base: Path) -> list[str]:
    out: list[str] = []
    for p in [str(base)] + sorted(glob.glob(str(base) + ".*")):
        if p != str(base) and not p.rsplit(".", 1)[-1].isdigit():
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as fh:
                out.extend(fh.readlines())
        except OSError:
            continue
    return out


def parse_stt(lines: Iterable[str], since: float, until: float) -> list[tuple[float, str, float]]:
    """``(ts, transcript, audio_seconds)`` household records of the local STT log in the window."""
    out = []
    for line in lines:
        try:
            d = json.loads(line)
            ts = dt.datetime.fromisoformat(str(d["ts"])).timestamp()
        except (ValueError, KeyError, TypeError):
            continue
        if str(d.get("traffic_class") or "household").lower() != "household" or not since - 60 <= ts < until:
            continue
        out.append((ts, str(d.get("transcript") or ""), float(d.get("audio_duration_seconds") or 0.0)))
    return out


def attach_receipts(turns: list[Turn], logs: dict, stt: list, struggles: list[dict]) -> None:
    """Join the receipts to the turns: timing by session (the first line after the turn, within 120 s), a claim-guard block by
    member (the latest turn before it, within ``CLAIM_WINDOW_S``), the STT record by words (within 120 s), struggle rows by id."""
    by_session: dict[str, list[Turn]] = {}
    by_user: dict[str, list[Turn]] = {}
    for t in turns:
        by_session.setdefault(t.session_id, []).append(t)
        by_user.setdefault(t.user_id, []).append(t)
    for ts, session, path, ms in logs.get("timing", []):
        cands = [t for t in by_session.get(session, []) if 0 <= ts - t.ts <= 120 and t.latency_ms is None]
        if cands:
            t = cands[-1]
            t.latency_ms, t.path = ms, path
    for ts, user in logs.get("claims", []):
        cands = [t for t in by_user.get(user, []) if 0 <= ts - t.ts <= CLAIM_WINDOW_S]
        if cands:
            cands[-1].claim_blocked = True
    norm = lambda s: " ".join(_words(s))  # noqa: E731
    for ts, words, audio in stt:
        n = norm(words)
        for t in turns:
            if t.channel == "voice" and t.audio_s is None and abs(t.ts - ts) <= 120 and n and norm(t.text) == n:
                t.audio_s = audio
                break
    ids = {t.id: t for t in turns}
    for s in struggles:
        for key in ("turn_id", "prev_turn_id"):
            t = ids.get(str(s.get(key) or ""))
            if t is not None and s not in t.struggles:
                t.struggles.append(s)


# ── the Flue session store (reply fallback; read-only) ───────────────────────

def flue_replies(db_path: Path, since: float, until: float, *, max_batches: int = 40000) -> list[tuple[float, str, str]]:
    """``(ts, user_message_text, assistant_reply)`` from the Flue sidecar's conversation store, newest batches first, stopping once
    a batch is older than ``since``. Opened ``mode=ro``; a missing or unreadable store is ``[]``."""
    if not db_path.exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error:
        return []
    users: dict[str, tuple[float, str]] = {}
    deltas: dict[str, list[tuple[int, str]]] = {}
    try:
        cur = conn.execute("SELECT path, seq, data FROM flue_conversation_stream_batches ORDER BY rowid DESC")
        for n, (path, seq, data) in enumerate(cur):
            if n >= max_batches:
                break
            try:
                recs = json.loads(data)
                if isinstance(recs, dict) and "$flueChunkCount" in recs:
                    parts = conn.execute("SELECT data FROM flue_conversation_stream_batch_chunks WHERE path = ? AND seq = ? ORDER BY chunk_index",
                                         (path, seq)).fetchall()
                    recs = json.loads("".join(p[0] for p in parts))
            except (ValueError, sqlite3.Error):
                continue
            if not isinstance(recs, list):
                continue
            oldest = None
            for r in recs:
                if not isinstance(r, dict):
                    continue
                try:
                    ts = dt.datetime.fromisoformat(str(r.get("timestamp")).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
                oldest = ts if oldest is None else min(oldest, ts)
                sub = str(r.get("submissionId") or "")
                if not sub or not since - 60 <= ts < until + 60:
                    continue
                if r.get("type") == "user_message":
                    c = r.get("content")
                    text = c if isinstance(c, str) else " ".join(str(x.get("text") or "") for x in c if isinstance(x, dict)) if isinstance(c, list) else ""
                    users[sub] = (ts, text)
                elif r.get("type") == "assistant_text_delta":
                    deltas.setdefault(sub, []).append((int(r.get("sequence") or 0), str(r.get("delta") or "")))
            if oldest is not None and oldest < since - 600:
                break
    except sqlite3.Error:
        return []
    finally:
        conn.close()
    return [(ts, text, "".join(d for _, d in sorted(deltas.get(sub, [])))) for sub, (ts, text) in users.items()]


def fill_from_flue(turns: list[Turn], flue: list[tuple[float, str, str]]) -> int:
    """A turn whose reply was never saved takes the Flue store's reply to the same words (within ``PAIR_WINDOW_S``)."""
    n = 0
    for t in turns:
        if t.reply_id or not t.text:
            continue
        for ts, utext, reply in flue:
            if reply.strip() and abs(ts - t.ts) <= PAIR_WINDOW_S and t.text in (utext or ""):
                t.reply, t.reply_source = reply.strip(), "flue"
                n += 1
                break
    return n


# ── the model's review ───────────────────────────────────────────────────────

Llm = Callable[[list, int], Awaitable[str]]

_SYSTEM = ("You are Zoe, a home assistant, reviewing one of your OWN past turns to find what went wrong and how to fix it. "
           "Be concrete and brief. Reply with ONE JSON object and nothing else: "
           '{"cause": "<one sentence: the most likely cause>", "fix": "<one of: ' + ", ".join(FIX_CLASSES) + '>", '
           '"note": "<one short sentence: the change that would have made this turn right>"}')
_KID_RUBRIC = (" This turn was a CHILD's. Judge it by the kid-safe rubric: was the reply safe, kind, honest, short and right for a "
               "child; did it avoid adult topics and anything frightening? NEVER quote, repeat or paraphrase the child's words in "
               "your answer: describe the problem only in general terms.")


def review_prompt(t: Turn) -> list:
    did = [f"channel={t.channel}", f"path={t.path or 'unknown'}"]
    if t.latency_ms is not None:
        did.append(f"first_token_ms={t.latency_ms}")
    if t.claim_blocked:
        did.append("the claim guard blocked a reply that claimed a change no tool made")
    if t.struggles:
        did.append("struggle signals: " + ",".join(sorted({str(s.get('signal')) for s in t.struggles})))
    if t.audio_s:
        did.append(f"audio_s={t.audio_s:.1f}")
    flags = "; ".join(f"{f} ({CLASS_WORDS[f]})" for f in t.flags)
    user = (f"What was asked (the transcript): {t.text[:400]!r}\nWhat I did: {', '.join(did)}\n"
            f"What I said: {(t.reply or '(nothing)')[:REPLY_CLIP]!r}\nWhat the checks flagged: {flags}")
    return [{"role": "system", "content": _SYSTEM + (_KID_RUBRIC if t.role == ROLE_CHILD else "")}, {"role": "user", "content": user}]


def parse_review(raw: str) -> Optional[dict]:
    """The model's JSON, validated: two short sentences and a fix from ``FIX_CLASSES`` (anything else = ``unclear``)."""
    m = re.search(r"\{.*\}", raw or "", re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    fix = str(d.get("fix") or "").strip().lower()
    return {"cause": re.sub(r"\s+", " ", str(d.get("cause") or "")).strip()[:240],
            "fix": fix if fix in FIX_CLASSES else "unclear",
            "note": re.sub(r"\s+", " ", str(d.get("note") or "")).strip()[:240]}


def echoes(source: str, text: str, n: int = 4) -> bool:
    """Does ``text`` carry any run of ``n`` consecutive words of ``source``? (a review must not quote a child; a paraphrase must
    not reuse the household's words)"""
    a, b = _words(source), " ".join(_words(text))
    return any(" ".join(a[i:i + n]) in b for i in range(len(a) - n + 1)) if len(a) >= n else bool(a) and " ".join(a) in b


@dataclasses.dataclass
class Budget:
    """Time AND tokens. Every call is charged its full output allowance BEFORE it is made, so the cap can never be crossed."""
    deadline: float = 0.0              # time.monotonic() deadline, 0 = none
    calls_left: int = 20
    tokens_left: int = 4000
    decode_tok_s: float = 6.5
    prefill_tok_s: float = 180.0
    calls: int = 0
    tokens: int = 0

    def estimate_s(self, prompt_chars: int, out_tokens: int) -> float:
        return 5.0 + (prompt_chars / 3.5) / max(10.0, self.prefill_tok_s) + out_tokens / max(0.5, self.decode_tok_s)

    def take(self, prompt_chars: int, out_tokens: int, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        if self.calls_left <= 0 or self.tokens_left < out_tokens:
            return False
        if self.deadline and now + self.estimate_s(prompt_chars, out_tokens) > self.deadline:
            return False
        self.calls_left -= 1
        self.tokens_left -= out_tokens
        self.calls += 1
        self.tokens += out_tokens
        return True


REVIEW_TOKENS = 160
PARAPHRASE_TOKENS = 60


async def review_turn(t: Turn, llm: Llm, budget: Budget) -> tuple[str, Optional[dict]]:
    """``(status, review)``: ``ok`` / ``no_time`` (the budget did not reach it) / ``bad_output`` / ``llm_unreachable`` / ``error``."""
    msgs = review_prompt(t)
    if not budget.take(sum(len(m["content"]) for m in msgs), REVIEW_TOKENS):
        return "no_time", None
    try:
        raw = await llm(msgs, REVIEW_TOKENS)
    except ConnectionError:
        return "llm_unreachable", None
    except Exception as exc:  # noqa: BLE001
        logger.warning("self_review: review call failed (%s)", type(exc).__name__)
        return "error", None
    rv = parse_review(raw)
    if rv is None:
        return "bad_output", None
    if t.role == ROLE_CHILD and (echoes(t.text, rv["cause"]) or echoes(t.text, rv["note"])):
        rv = {"cause": "(withheld: the review echoed the child's words)", "fix": rv["fix"], "note": ""}
    return "ok", rv


_BEHAVIOUR = {EMOTION_MISREAD: "a short, plain remark gets a plain answer, not comfort it did not ask for",
              "manner": "the reply's manner fits the ask (length, warmth, directness)"}


def paraphrase_ok(original: str, cand: str, deny_terms: Iterable[str] = ()) -> bool:
    """A synthetic ask may enter the self-improver's candidate file only when it carries NONE of the household's words: no run of
    three of the original's words, no deny term, no email / long number, no capitalised word but the first and "I", 2-24 words."""
    c = (cand or "").strip().strip('"').strip()
    ws = c.split()
    if not 2 <= len(ws) <= 24 or "\n" in c:
        return False
    if echoes(original, c, 3):
        return False
    if re.search(r"\S+@\S+|\d{4,}|https?://", c):
        return False
    if any(w[:1].isupper() and w.strip(".,!?'\"") not in ("I", "I'm", "I've", "I'd", "I'll") for w in ws[1:]):
        return False
    low = c.lower()
    return not any(t.strip() and t.strip().lower() in low for t in deny_terms)


async def paraphrase(t: Turn, llm: Llm, budget: Budget, deny_terms: Iterable[str] = ()) -> Optional[str]:
    msgs = [{"role": "system", "content": "Write ONE new, invented message a person might say to a home assistant. It must have the same "
             "SHAPE as the example (about as long, the same tone, the same kind of remark or request) but entirely different words, and "
             "no names, places, dates, numbers or personal details. Reply with the message only."},
            {"role": "user", "content": f"Example: {t.text[:300]!r}"}]
    if not budget.take(sum(len(m["content"]) for m in msgs), PARAPHRASE_TOKENS):
        return None
    try:
        raw = (await llm(msgs, PARAPHRASE_TOKENS)).strip().strip('"').strip()
    except Exception:  # noqa: BLE001
        return None
    return raw if paraphrase_ok(t.text, raw, deny_terms) else None


# ── privacy policy per turn ──────────────────────────────────────────────────

def policy(t: Turn) -> str:
    """``full`` (owner: quoted + reviewed) | ``kid`` (reviewed under the kid rubric, never quoted) | ``count`` (counted only)."""
    if is_guestish(t):
        return "count"
    if t.role == ROLE_OWNER:
        return "full"
    if t.role == ROLE_CHILD:
        return "kid"
    return "count"


async def resolve_roles(turns: list[Turn], lookup: Optional[Callable[[str], Awaitable[str]]] = None) -> None:
    if lookup is None:
        import household_authority

        lookup = household_authority.lookup_role
    cache: dict[str, str] = {}
    for t in turns:
        if t.user_id not in cache:
            try:
                cache[t.user_id] = str(await lookup(t.user_id))
            except Exception:  # noqa: BLE001 - not knowing who it was = the most restricted member role
                cache[t.user_id] = ROLE_CHILD
        t.role = cache[t.user_id]


async def drop_forgotten(turns: list[Turn]) -> tuple[list[Turn], int]:
    """Turns naming an entity their member has forgotten are not reviewed (counted). A ledger outage keeps nothing of that member."""
    try:
        import memory_forgotten as mf
    except Exception:  # noqa: BLE001
        return turns, 0
    out, dropped = [], 0
    by_user: dict[str, list[Turn]] = {}
    for t in turns:
        by_user.setdefault(t.user_id, []).append(t)
    for uid, ts in by_user.items():
        try:
            kept, n = await mf.keep_unforgotten(uid, ts, text_of=lambda x: f"{x.text}\n{x.reply}", near=False, strict=True)
        except Exception:  # noqa: BLE001 - LedgerUnavailable: withhold, never "nothing forgotten"
            kept, n = [], len(ts)
        out.extend(kept)
        dropped += n
    out.sort(key=lambda t: t.ts)
    return out, dropped


# ── files ────────────────────────────────────────────────────────────────────

def _private_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(OSError):
        os.chmod(p, 0o700)
    return p


def _write_private(path: Path, text: str) -> None:
    """Atomic 0600 write (a fresh temp file, then a rename: a pre-existing 0644 file never keeps its mode)."""
    _private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


@contextlib.contextmanager
def _locked(d: Path):
    _private_dir(d)
    fd = os.open(d / ".lock", os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def records_path(night: str, d: Optional[Path] = None) -> Path:
    return (d or state_dir()) / f"{night}{RECORDS_SUFFIX}"


def summary_path(night: str, d: Optional[Path] = None) -> Path:
    return (d or state_dir()) / f"{night}{SUMMARY_SUFFIX}"


def report_path(night: str, d: Optional[Path] = None) -> Path:
    return (d or state_dir()) / f"{night}.md"


def _read_records(p: Path) -> list[dict]:
    try:
        lines = p.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for ln in lines:
        with contextlib.suppress(ValueError):
            d = json.loads(ln)
            if isinstance(d, dict):
                out.append(d)
    return out


def render_report(night: str, records: list[dict], summary: dict) -> str:
    L = [f"# Self-review, night of {night}", "",
         "PRIVATE: what the household said stays on this box. Generated by `self_review.py`; the counts-only summary is the "
         f"`{SUMMARY_SUFFIX}` file beside this one.", "",
         f"- turns read {summary.get('turns_seen', 0)}, household {summary.get('turns_household', 0)}, flagged {summary.get('flagged', 0)}, "
         f"reviewed by the model {summary.get('reviewed', 0)}, not reached by the budget {summary.get('no_time', 0)}",
         "- excluded: " + (", ".join(f"{k} {v}" for k, v in sorted((summary.get("excluded") or {}).items())) or "none"),
         "- classes: " + (", ".join(f"{k} {v}" for k, v in sorted((summary.get("classes") or {}).items(), key=lambda kv: -kv[1])) or "none"),
         ""]
    for r in records:
        when = dt.datetime.fromtimestamp(float(r.get("ts") or 0), _tz()).strftime("%H:%M")
        L.append(f"## {when} {r.get('channel')} - {', '.join(r.get('flags') or [])}  (`{r.get('turn_id')}`)")
        if r.get("policy") == "full":
            L.append(f"- asked: {r.get('text')!r}")
            L.append(f"- said ({r.get('reply_source') or 'none'}): {(r.get('reply') or '(nothing)')!r}")
        else:
            L.append(f"- {r.get('role') or 'member'}'s turn: words not shown ({'kid-safe rubric' if r.get('policy') == 'kid' else 'counted only'})")
        did = [f"path {r.get('path') or '?'}"] + ([f"first token {r['latency_ms']} ms"] if r.get("latency_ms") is not None else []) + \
              (["claim guard blocked"] if r.get("claim_blocked") else []) + ([f"struggles {','.join(r['struggles'])}"] if r.get("struggles") else [])
        L.append("- did: " + "; ".join(did))
        rv = r.get("review")
        if rv:
            L.append(f"- likely cause: {rv.get('cause')}")
            L.append(f"- fix class: **{rv.get('fix')}**" + (f" - {rv.get('note')}" if rv.get("note") else ""))
        else:
            L.append(f"- model review: {r.get('review_status') or 'not asked'}")
        L.append("")
    return "\n".join(L)


def build_summary(night: str, run_mode: str, turns_seen: int, household: list[Turn], excluded: dict, flagged: list[Turn],
                  statuses: dict, budget: Budget) -> dict:
    """COUNTS ONLY: classes, counts, example ids, and one event per (flagged turn, class) for capability_gap (fixed area label from
    its lexicon, the utterance HASH the logs already carry - never words)."""
    classes: dict[str, int] = {}
    examples: dict[str, list[str]] = {}
    fixes: dict[str, int] = {}
    events = []
    try:
        import capability_gap as cg
    except Exception:  # noqa: BLE001
        cg = None
    for t in flagged:
        for c in t.flags:
            classes[c] = classes.get(c, 0) + 1
            ex = examples.setdefault(c, [])
            if len(ex) < 3:
                ex.append(t.id)
            if c in GAP_CLASSES and cg is not None:
                events.append({"ts": round(t.ts, 3), "cls": c, "member": "guest" if is_guestish(t) else t.user_id, "turn_id": t.id,
                               "utt": cg.utterance_hash(t.text), "area": cg.classify(t.text)[0]})
        rv = statuses.get(t.id, {}).get("review")
        if rv:
            fixes[rv["fix"]] = fixes.get(rv["fix"], 0) + 1
    st = [v.get("status") for v in statuses.values()]
    return {"night": night, "mode": run_mode, "turns_seen": turns_seen, "turns_household": len(household), "excluded": excluded,
            "flagged": len(flagged), "classes": classes, "fixes": fixes, "examples": examples, "events": events,
            "reviewed": st.count("ok"), "no_time": st.count("no_time"), "calls": budget.calls, "tokens_charged": budget.tokens,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}


def _record(t: Turn, status: str, review: Optional[dict], night: str) -> dict:
    pol = policy(t)
    rec = {"night": night, "turn_id": t.id, "reply_id": t.reply_id, "user_id": t.user_id, "role": t.role, "policy": pol,
           "ts": round(t.ts, 3), "channel": t.channel, "flags": list(t.flags), "path": t.path, "latency_ms": t.latency_ms,
           "claim_blocked": t.claim_blocked, "struggles": sorted({str(s.get("signal")) for s in t.struggles}),
           "reply_source": t.reply_source, "review_status": status, "review": review}
    if pol == "full":
        rec["text"], rec["reply"] = t.text, t.reply[:2000]
    return rec


# ── the struggle store + the self-improver candidates (mode on) ──────────────

def struggle_rows(flagged: list[Turn], night: str) -> list[dict]:
    """One wordless ``struggle_events`` row per (turn, class): the summary column stays EMPTY (nothing to forget, nothing quoted)."""
    rows = []
    for t in flagged:
        for c in t.flags:
            rid = hashlib.sha256(f"self_review|{t.id}|{c}".encode()).hexdigest()[:32]
            rows.append({"id": rid, "ts": float(t.ts), "user_id": "guest" if is_guestish(t) else t.user_id, "is_guest": 1 if is_guestish(t) else 0,
                         "channel": t.channel, "panel_id": t.panel_id[:80], "session_id": "" if is_guestish(t) else t.session_id[:120],
                         "signal": STRUGGLE_SIGNAL, "confidence": 0.6, "fix_type": STRUGGLE_FIX, "prev_turn_id": "", "turn_id": t.id,
                         "domain_predicted": "", "domain_served": "", "path_served": t.path, "latency_ms": t.latency_ms, "stt_ms": None,
                         "pattern": c, "summary": "", "detail": json.dumps({"source": "self_review", "night": night}, sort_keys=True), "mode": "self_review"})
    return rows


async def store_struggles(rows: list[dict]) -> int:
    """Insert the rows not already there (a re-run of a night adds nothing twice)."""
    if not rows:
        return 0
    import struggle_detector

    backend = struggle_detector.get_backend()
    since = min(r["ts"] for r in rows) - 1
    have = {str(r.get("id")) for r in await backend.rows(since, None, limit=20000)}
    new = [r for r in rows if r["id"] not in have]
    return int(await backend.add(new) or 0) if new else 0


def deny_terms() -> list[str]:
    try:
        return [ln.strip() for ln in (improver_dir() / "deny-terms.txt").read_text(encoding="utf-8").splitlines() if ln.strip()]
    except OSError:
        return []


def append_candidates(cells: list[dict]) -> int:
    """Append to the self-improver's CANDIDATE file (never its frozen split: promoting a cell is a human step)."""
    if not cells:
        return 0
    d = _private_dir(improver_dir())
    p = d / CANDIDATES_FILE
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW, 0o600)
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        for c in cells:
            fh.write(json.dumps(c, sort_keys=True) + "\n")
    return len(cells)


# ── the night ────────────────────────────────────────────────────────────────

@dataclasses.dataclass
class NightInputs:
    rows: list                 # chat rows (see ``pair_rows``)
    log_lines: list
    stt_lines: list
    struggles: list
    flue: list
    since: float
    until: float


async def run_night(inp: NightInputs, *, llm: Optional[Llm], budget: Budget, night: str, run_mode: str, dry_run: bool = False,
                    lookup: Optional[Callable[[str], Awaitable[str]]] = None, out_dir: Optional[Path] = None) -> dict:
    """Assemble, exclude, flag, review, write. Returns the counts-only summary plus ``status`` and what was written."""
    turns = pair_rows(inp.rows)
    seen = len(turns)
    excluded: dict[str, int] = {}
    household = []
    for t in turns:
        why = exclusion(t)
        if not why:
            try:
                import distress_handoff

                if distress_handoff.guarded_text(t.text):
                    why = "distress"
            except Exception:  # noqa: BLE001
                pass
        if why:
            excluded[why] = excluded.get(why, 0) + 1
        else:
            household.append(t)
    household, forgotten = await drop_forgotten(household)
    if forgotten:
        excluded["forgotten"] = forgotten
    fill_from_flue(household, inp.flue)
    attach_receipts(household, parse_log(inp.log_lines, inp.since, inp.until), parse_stt(inp.stt_lines, inp.since, inp.until), inp.struggles)
    flag_all(household)
    flagged = [t for t in household if t.flags]
    await resolve_roles(flagged, lookup)
    order = sorted(flagged, key=lambda t: (-max(SEVERITY.get(f, 0) for f in t.flags), {"full": 0, "kid": 1}.get(policy(t), 2), t.ts))
    statuses: dict[str, dict] = {}
    status = "ok"
    for t in order:
        if policy(t) == "count" or llm is None:
            statuses[t.id] = {"status": "count_only" if policy(t) == "count" else "no_model", "review": None}
            continue
        if status == "llm_unreachable":
            statuses[t.id] = {"status": "llm_unreachable", "review": None}
            continue
        st, rv = await review_turn(t, llm, budget)
        statuses[t.id] = {"status": st, "review": rv}
        if st == "llm_unreachable":
            status = "llm_unreachable"
    summary = build_summary(night, run_mode, seen, household, excluded, flagged, statuses, budget)
    cells: list[dict] = []
    if run_mode == "on" and llm is not None and status != "llm_unreachable":
        terms = deny_terms()
        for t in order:
            rv = statuses.get(t.id, {}).get("review") or {}
            style = [f for f in t.flags if f in STYLE_CLASSES] or (["manner"] if rv.get("fix") in STYLE_FIXES else [])
            if policy(t) != "full" or not style:
                continue          # only the OWNER's turns seed a synthetic ask, and only a manner failure
            ask = await paraphrase(t, llm, budget, terms)
            if ask:
                cells.append({"ask": ask, "behaviour": _BEHAVIOUR.get(style[0], _BEHAVIOUR["manner"]), "class": style[0],
                              "night": night, "source": "self_review", "status": "candidate"})
    summary["candidate_cells"] = len(cells)
    summary["status"] = status
    if dry_run:
        summary["written"] = []
        return summary
    d = out_dir or state_dir()
    written = []
    with _locked(d):
        records = [_record(t, statuses[t.id]["status"], statuses[t.id]["review"], night) for t in sorted(flagged, key=lambda x: x.ts)]
        old = [r for r in _read_records(records_path(night, d)) if r.get("turn_id") not in {x["turn_id"] for x in records}]
        records = sorted(old + records, key=lambda r: float(r.get("ts") or 0))
        _write_private(records_path(night, d), "".join(json.dumps(r, sort_keys=True) + "\n" for r in records))
        _write_private(summary_path(night, d), json.dumps(summary, indent=1, sort_keys=True))
        _write_private(report_path(night, d), render_report(night, records, summary))
        written += [records_path(night, d).name, summary_path(night, d).name, report_path(night, d).name]
        _write_private(d / "state.json", json.dumps({"reviewed_until": inp.until, "night": night}))
    if run_mode == "on":
        try:
            summary["struggle_rows"] = await store_struggles(struggle_rows(flagged, night))
        except Exception as exc:  # noqa: BLE001 - the private review stands; the store is retried by the next night's re-run
            summary["struggle_rows"] = 0
            summary.setdefault("problems", []).append(f"struggle store: {type(exc).__name__}")
        summary["candidate_cells"] = append_candidates(cells)
        with _locked(d):
            _write_private(summary_path(night, d), json.dumps(summary, indent=1, sort_keys=True))
    summary["written"] = written
    return summary


def window_start(now: float, d: Optional[Path] = None) -> float:
    """Since the last reviewed instant (``state.json``), never more than ``MAX_LOOKBACK_H`` back."""
    floor = now - MAX_LOOKBACK_H * 3600
    try:
        st = json.loads(((d or state_dir()) / "state.json").read_text(encoding="utf-8"))
        return max(floor, float(st.get("reviewed_until") or floor))
    except (OSError, ValueError, TypeError):
        return max(floor, now - 24 * 3600)


async def load_chat_rows(since: float, until: float, db=None) -> list[dict]:
    """The window's chat rows through the app's own SQL helpers (owner expression, off-the-record filter). Raises on a failed read."""
    from memory_provenance import off_record_sql
    from user_filters import message_owner_expr

    sql = ("SELECT cm.id, cm.session_id, cm.role, cm.content, cm.metadata, EXTRACT(EPOCH FROM cm.created_at::timestamptz) AS ts, "
           + message_owner_expr() + " AS owner FROM chat_messages cm JOIN chat_sessions cs ON cm.session_id = cs.id "
           "WHERE cm.created_at::timestamptz >= to_timestamp(?::double precision) AND cm.created_at::timestamptz < to_timestamp(?::double precision) "
           "AND " + off_record_sql("cm") + " ORDER BY cm.created_at::timestamptz ASC LIMIT " + str(MAX_TURNS * 2))
    cols = ("id", "session_id", "role", "content", "metadata", "ts", "owner")

    async def _q(conn):
        rows = await (await conn.execute(sql, (float(since), float(until)))).fetchall()
        return [dict(zip(cols, tuple(r))) for r in rows]

    if db is not None:
        return await _q(db)
    from db_pool import get_db_ctx  # type: ignore[import]

    async with get_db_ctx() as conn:
        return await _q(conn)


async def load_struggles(since: float) -> list[dict]:
    try:
        import struggle_detector

        rows = await struggle_detector.get_backend().rows(since - 120, None, limit=20000)
        return [{k: r.get(k) for k in ("signal", "fix_type", "turn_id", "prev_turn_id", "ts")} for r in rows if r.get("signal") != STRUGGLE_SIGNAL]
    except Exception as exc:  # noqa: BLE001 - no struggle store = no struggle receipts
        logger.info("self_review: struggle rows unavailable (%s)", type(exc).__name__)
        return []


# ── forget, delete, read ─────────────────────────────────────────────────────

def _record_text(r: dict) -> str:
    rv = r.get("review") or {}
    return "\n".join(str(x or "") for x in (r.get("text"), r.get("reply"), rv.get("cause"), rv.get("note")))


def _rewrite(d: Path, keep: Callable[[dict], bool]) -> int:
    removed = 0
    for p in sorted(d.glob(f"*{RECORDS_SUFFIX}")):
        recs = _read_records(p)
        kept = [r for r in recs if keep(r)]
        if len(kept) == len(recs):
            continue
        removed += len(recs) - len(kept)
        night = p.name[: -len(RECORDS_SUFFIX)]
        _write_private(p, "".join(json.dumps(r, sort_keys=True) + "\n" for r in kept))
        try:
            summary = json.loads(summary_path(night, d).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            summary = {}
        gone = {r.get("turn_id") for r in recs} - {r.get("turn_id") for r in kept}
        if summary:
            summary["events"] = [e for e in summary.get("events") or [] if e.get("turn_id") not in gone]
            summary["examples"] = {c: [i for i in ids if i not in gone] for c, ids in (summary.get("examples") or {}).items()}
            _write_private(summary_path(night, d), json.dumps(summary, indent=1, sort_keys=True))
        _write_private(report_path(night, d), render_report(night, kept, summary))
    return removed


async def erase_entity(user_id: str, rx, *, strict: bool = False) -> int:
    """The forget cascade's ``self_review`` step: every review record of ``user_id`` whose words or review name the entity (``rx``: a
    ``re.Pattern`` or ``memory_forgotten.LedgerPattern``) is dropped and the night's report re-rendered without it. RAISES with
    ``strict`` (a forget that could not erase is not confirmed); a missing directory is nothing stored."""
    d = state_dir()
    if not user_id or not d.exists():
        return 0
    try:
        def run() -> int:
            with _locked(d):
                return _rewrite(d, lambda r: not (r.get("user_id") == user_id and rx.search(_record_text(r))))
        return await asyncio.to_thread(run)
    except Exception as exc:  # noqa: BLE001
        if strict:
            raise
        logger.warning("self_review: records naming a forgotten entity NOT erased (%s)", type(exc).__name__)
        return 0


async def delete_user(user_id: str) -> int:
    """Every review record of the member (``MemoryService.delete_user`` via ``struggle_detector.delete_user``) and their capability
    events in the summaries. RAISES on a failure (fail closed)."""
    d = state_dir()
    if not (user_id or "").strip() or not d.exists():
        return 0

    def run() -> int:
        with _locked(d):
            n = _rewrite(d, lambda r: r.get("user_id") != user_id)
            for p in d.glob(f"*{SUMMARY_SUFFIX}"):
                try:
                    s = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                ev = [e for e in s.get("events") or [] if e.get("member") != user_id]
                if len(ev) != len(s.get("events") or []):
                    s["events"] = ev
                    _write_private(p, json.dumps(s, indent=1, sort_keys=True))
            return n
    return await asyncio.to_thread(run)


async def load_reviews(night: str, *, user_id: Optional[str] = None) -> list[dict]:
    """A night's records for a reader (the owner's CLI), re-checked against the forget ledger on EVERY read: a record naming an entity
    its member forgot is withheld even when the erase did not land; a ledger outage withholds that member's records."""
    import memory_forgotten as mf

    recs = [r for r in _read_records(records_path(night)) if user_id is None or r.get("user_id") == user_id]
    out: list[dict] = []
    by_user: dict[str, list[dict]] = {}
    for r in recs:
        by_user.setdefault(str(r.get("user_id") or ""), []).append(r)
    for uid, rs in by_user.items():
        try:
            kept, _ = await mf.keep_unforgotten(uid, rs, text_of=_record_text, near=False, strict=True)
        except Exception:  # noqa: BLE001
            kept = []
        out.extend(kept)
    return sorted(out, key=lambda r: float(r.get("ts") or 0))


# ── the owner's morning line ─────────────────────────────────────────────────

def latest_summary(now: Optional[float] = None, max_age_h: float = 36.0) -> Optional[dict]:
    now = time.time() if now is None else now
    best = None
    for p in state_dir().glob(f"*{SUMMARY_SUFFIX}"):
        try:
            if now - p.stat().st_mtime > max_age_h * 3600:
                continue
            s = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(s, dict) and (best is None or str(s.get("night")) > str(best.get("night"))):
            best = s
    return best


def morning_line(summary: Optional[dict]) -> Optional[str]:
    """Plain English, counts and classes only (never a word anyone said, never a member)."""
    if not summary or summary.get("mode") != "on":
        return None
    n, m = int(summary.get("turns_household") or 0), int(summary.get("flagged") or 0)
    if n <= 0:
        return None
    if m <= 0:
        return f"Last night I looked back over {n} of my conversations and found nothing that went wrong."
    top = sorted((summary.get("classes") or {}).items(), key=lambda kv: (-kv[1], kv[0]))[:3]
    what = ", ".join(f"{v} {CLASS_WORDS.get(k, k)}" for k, v in top)
    return (f"Last night I looked back over {n} of my conversations and found {m} that probably went wrong ({what}); "
            "the private review is on the box.")


async def brief_line(user_id: str) -> Optional[str]:
    """The admin's morning-brief mention (mode ``on`` only). Never raises."""
    try:
        if mode() != "on":
            return None
        return morning_line(await asyncio.to_thread(latest_summary))
    except Exception:  # noqa: BLE001
        return None
