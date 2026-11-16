"""change_notices - Phase 1 of change notices: the owner hears, on Telegram, which Zoe PRs were raised and went live.

Design: ``docs/architecture/communication-preferences.md`` (PR #2075), sections 2-6 and 9 (P1). Flag
``ZOE_CHANGE_NOTICES=off|shadow|on`` (default ``off``: the loop is never started). ``shadow`` does everything except
the send: each message it WOULD send is written to the audit log instead, so a week of shadow output can be read
before anything reaches a phone.

Pipeline, one tick every ``ZOE_CHANGE_NOTICES_INTERVAL_S`` (default 600 s):

  sources (``gh`` on the box; no webhook, no public ingress)
    raised   - open PRs. ``needs-owner`` label = ``decision`` (instant); the rest = a line in the digest.
    merged   - PRs merged on GitHub. A merge is NOT news yet: it waits in ``pending_live``.
    live     - a pending merge whose commit is contained in ``~/.zoe/deploy/last-deployed-sha`` (written by
               deploy.yml only after a healthy deploy) = "went live". Category from the conventional-commit type.
    stuck    - merges still not live after ``ZOE_CHANGE_NOTICES_STALE_MIN`` (60) = one ``broke`` notice per deployed sha.
  -> one notice per ``dedup_key`` (``pr:N:raised``, ``pr:N:live``, ``deploy:stuck:<sha>``), seen-once
  -> fan-out to recipients: OWNER ONLY in P1 (admin role + a linked Telegram id from ``user_prefs``); never anyone else
  -> per-member preferences (``comm_prefs``): cadence instant | digest | weekly | never, detail, quiet hours, cap
  -> instant lane (quiet hours hold, daily cap + 30-min same-category spacing roll over into the digest,
     3+ ready at once = ONE batched message) and digest lane (daily at 19:00, weekly; empty = not sent)
  -> ``telegram_notify`` (the bot's loopback ``/internal/notify``) with at-most-once claims and an audit line per event.

Rendering is deterministic, English only (owner decision 14), in an assistant's voice: the PR body's
``For-The-Household:`` sentence first (else one honest "housekeeping" line from the title's subject), its optional
``Numbers:`` line and ``Owner-Step:`` (else "Nothing for you to do."), bad news first. No model and no external API
ever sees PR content.

Kill switch: a file named ``KILL`` in the state dir (``POST /api/change-notices/kill``) stops every send at the next
tick without a restart. State: ``~/.zoe/change-notices/state.json`` (0600, atomic replace) + ``audit.jsonl``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Protocol

import comm_prefs
import telegram_notify
from typed_env import env_int, env_str

logger = logging.getLogger(__name__)

MODES = ("off", "shadow", "on")
STATE_VERSION = 1
MAX_CHUNKS = 3  # design 6: at most 3 messages a minute to one chat
CHUNK_CHARS = 990  # the notify route caps text at 1000
LINE_CHARS = 880  # one digest line: a line plus the longest possible suffix (``SUFFIX_MAX``) always fits a chunk
SAME_KIND_SPACING = timedelta(minutes=30)
BATCH_AT = 3  # this many instant notices ready at once go out as ONE message
MAX_ATTEMPTS = 8
LIVE_CHECKS_PER_TICK = 30
PENDING_MAX_AGE = timedelta(days=7)
KEEP_SEEN = timedelta(days=30)
SEND_GAP_S = 1.1  # Telegram: about one message a second per chat
AUDIT_MAX_BYTES = 5 * 1024 * 1024
NEEDS_OWNER_LABEL = "needs-owner"
DEPLOY_BRANCH = "main"  # only a merge INTO this branch is ever deployed; a stacked PR merged into a feature branch is not

FINAL = frozenset({"sent", "shadow", "refused", "failed", "superseded", "skipped", "no_channel", "uncertain_reported"})


# ── configuration ─────────────────────────────────────────────────────────────
def mode() -> str:
    m = env_str("ZOE_CHANGE_NOTICES", "off").lower()
    return m if m in MODES else "off"


def interval_s() -> int:
    return max(60, env_int("ZOE_CHANGE_NOTICES_INTERVAL_S", 600))


def stale_after() -> timedelta:
    return timedelta(minutes=max(10, env_int("ZOE_CHANGE_NOTICES_STALE_MIN", 60)))


def repo() -> str:
    return env_str("ZOE_GITHUB_REPO", "jason-easyazz/zoe-ai-assistant")


def state_dir() -> Path:
    raw = env_str("ZOE_CHANGE_NOTICES_STATE_DIR")
    return Path(raw).expanduser() if raw else Path.home() / ".zoe" / "change-notices"


def deploy_marker() -> Path:
    raw = env_str("ZOE_DEPLOY_STATE_DIR")  # the same override deploy.yml honours
    return (Path(raw).expanduser() if raw else Path.home() / ".zoe" / "deploy") / "last-deployed-sha"


def kill_file() -> Path:
    return state_dir() / "KILL"


def killed() -> bool:
    return kill_file().exists()


def set_killed(on: bool) -> None:
    p = kill_file()
    if on:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("change notices stopped by the kill switch\n", encoding="utf-8")
    else:
        p.unlink(missing_ok=True)


def read_deployed_sha(path: Optional[Path] = None) -> str:
    try:
        sha = (path or deploy_marker()).read_text(encoding="utf-8").split()[0].strip()
    except (OSError, IndexError):
        return ""
    return sha if re.fullmatch(r"[0-9a-f]{7,40}", sha) else ""


# ── audit ─────────────────────────────────────────────────────────────────────
def audit(event: str, **fields: Any) -> None:
    """One JSON line per event (0600). Holds keys, statuses and member ids; message TEXT only in shadow mode."""
    rec = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "event": event, **fields}
    try:
        p = state_dir() / "audit.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        if p.exists() and p.stat().st_size > AUDIT_MAX_BYTES:
            os.replace(p, p.with_suffix(".jsonl.1"))
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, sort_keys=True) + "\n")
    except OSError as exc:
        logger.warning("change_notices: audit log not writable (%s)", type(exc).__name__)


# ── state ─────────────────────────────────────────────────────────────────────
def empty_state() -> dict:
    return {"version": STATE_VERSION, "bootstrapped": False, "seen": {}, "notices": {}, "pending_live": {},
            "deliveries": {}, "digest_sent": {}, "instant_sent": {}, "last_instant": {}, "opener": {}}


def load_state(*, repair: bool = True) -> dict:
    p = state_dir() / "state.json"
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return empty_state()
    except (OSError, ValueError) as exc:
        if not repair:
            return empty_state()
        # Never guess from a torn file: park it and start silent (bootstrap marks everything seen).
        logger.warning("change_notices: state unreadable (%s); starting fresh and silent", type(exc).__name__)
        try:
            os.replace(p, p.with_suffix(".json.corrupt"))
        except OSError:
            pass
        audit("state_reset", reason=type(exc).__name__)
        return empty_state()
    st = empty_state()
    if isinstance(raw, dict):
        st.update({k: v for k, v in raw.items() if k in st})
    return st


def save_state(st: dict) -> None:
    p = state_dir() / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(st, fh, sort_keys=True)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, p)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# ── GitHub (read-only, through the box's authenticated ``gh``) ────────────────
class GitHub(Protocol):
    async def open_prs(self) -> list[dict]: ...
    async def merged_prs(self, since: str) -> list[dict]: ...
    async def contains(self, deployed: str, sha: str) -> Optional[bool]: ...
    async def latest_deploy_run(self) -> Optional[dict]: ...


Runner = Callable[[list[str]], Awaitable[tuple[int, str]]]


async def _run_gh(args: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        "gh", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, _err = await asyncio.wait_for(proc.communicate(), timeout=45)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, ""
    return proc.returncode or 0, out.decode("utf-8", "replace")


class GhCli:
    """Real source. Every call is a read; ``gh`` is already authenticated for the zoe user on the box."""

    def __init__(self, repo_name: Optional[str] = None, runner: Runner = _run_gh) -> None:
        self.repo = repo_name or repo()
        self.run = runner

    async def _json(self, args: list[str]) -> Any:
        code, out = await self.run(args)
        if code != 0:
            raise RuntimeError(f"gh exited {code}")
        return json.loads(out or "null")

    async def open_prs(self) -> list[dict]:
        return await self._json(["pr", "list", "--repo", self.repo, "--state", "open", "--limit", "100",
                                 "--json", "number,title,body,isDraft,labels,url,createdAt"]) or []

    async def merged_prs(self, since: str) -> list[dict]:
        return await self._json(["pr", "list", "--repo", self.repo, "--state", "merged", "--limit", "100",
                                 "--search", f"merged:>={since}",
                                 "--json", "number,title,body,url,mergedAt,mergeCommit,labels,baseRefName"]) or []

    async def contains(self, deployed: str, sha: str) -> Optional[bool]:
        """Is ``sha`` an ancestor of (or equal to) ``deployed``? ``None`` = unknown (try again next tick)."""
        code, out = await self.run(["api", f"repos/{self.repo}/compare/{sha}...{deployed}", "--jq", ".status"])
        if code != 0:
            return None
        status = out.strip()
        if status in ("ahead", "identical"):
            return True
        if status in ("behind", "diverged"):
            return False
        return None

    async def latest_deploy_run(self) -> Optional[dict]:
        try:
            runs = await self._json(["run", "list", "--repo", self.repo, "--workflow", "deploy.yml", "--limit", "1",
                                     "--json", "conclusion,status,url,headSha"])
        except Exception:  # noqa: BLE001 - only decorates the stuck notice
            return None
        return runs[0] if isinstance(runs, list) and runs else None


# ── rendering (deterministic, English, in an assistant's voice) ───────────────
# Voice rules (docs/knowledge/change-notices.md "How the messages read"): plain first-person sentences, what is
# different for the household first (the ``For-The-Household:`` trailer), bad news first, every went-live message
# ends with the owner's step or "Nothing for you to do.", no commit syntax / hashes / backticks, the PR number at most
# once (on the button for a single message, in brackets otherwise). Wording rotates by a stable counter, never random.
_CC = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<subject>.+)$", re.I)
_TYPE_CATEGORY = {
    "feat": "ability",
    "fix": "fixed", "perf": "fixed", "security": "fixed",
    "revert": "broke",
    "docs": "under_the_hood", "doc": "under_the_hood", "test": "under_the_hood", "tests": "under_the_hood",
    "chore": "under_the_hood", "ci": "under_the_hood", "build": "under_the_hood", "refactor": "under_the_hood",
    "style": "under_the_hood", "deps": "under_the_hood",
}
# Every PR-body trailer this module knows. A trailer line is read only through ``_trailer`` and is never prose.
_TRAILER = re.compile(r"^(?P<key>Co-Authored-By|Signed-off-by|Zoe-Origin|Zoe-Notice|For-The-Household|For-Children|"
                      r"Numbers|Owner-Step|If-No-Answer|Struggle-Cluster|Refs|Closes|Fixes):[ \t]*(?P<value>.*)$",
                      re.I | re.M)
_NOTHING = ("nothing visible", "none", "n/a", "nothing")
_TYPES = r"(?:feat|fix|perf|security|revert|docs?|tests?|chore|ci|build|refactor|style|deps)"
_CC_LEAD = re.compile(rf"^{_TYPES}!?:\s*", re.I)
_CC_SCOPED = re.compile(rf"\b{_TYPES}\([^)]*\)!?:\s*", re.I)
_PRREF = re.compile(r"\(\s*#\d+\s*\)|#\d+\b")
_HEX = re.compile(r"\b(?=[0-9a-f]*\d)(?=[0-9a-f]*[a-f])[0-9a-f]{7,40}\b")
NUMBERS_MAX = 3
NUMBER_CHARS = 70


def category_for_title(title: str) -> str:
    t = (title or "").strip()
    if t.lower().startswith("revert"):
        return "broke"
    m = _CC.match(t)
    if not m:
        return "fixed"
    return _TYPE_CATEGORY.get(m.group("type").lower(), "fixed")


def _plain(md: str) -> str:
    s = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", md)
    s = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", s)
    s = re.sub(r"\*\*|__|[*`]", "", s)  # emphasis and code marks; single underscores stay (they are in names)
    s = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", s, flags=re.M)
    s = re.sub(r"^\s*>\s?", "", s, flags=re.M)
    return re.sub(r"\s+", " ", s).strip()


def _dejargon(text: str) -> str:
    """``_plain`` plus the deterministic de-jargon rules: no commit prefix, no ``#N``, no commit hash."""
    s = _CC_SCOPED.sub("", _CC_LEAD.sub("", _plain(text or "")))
    s = _HEX.sub("", _PRREF.sub("", s))
    return re.sub(r"\s+([.,;:!?])", r"\1", re.sub(r"\s+", " ", s)).strip(" ,;:-")


def _clip_words(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: cap - 1].rsplit(" ", 1)[0].rstrip(",;:- ") + "…"


def _sentences(text: str, n: int, cap: int) -> str:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", text)
    out = ""
    for p in parts[:n]:
        cand = f"{out} {p}".strip()
        if len(cand) > cap and out:
            break
        out = cand
    return _clip_words(out, cap)


def _end(s: str) -> str:
    return s if not s or s[-1] in ".!?…" else s + "."


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def _low(s: str) -> str:
    return s if s[1:2].isupper() else s[:1].lower() + s[1:]  # "It stays off" -> "it stays off"; "OK" stays


def _trailer(body: str, name: str) -> str:
    text = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    text = re.sub(r"^[ \t]*(```|~~~).*?^[ \t]*\1[^\n]*$", "", text, flags=re.S | re.M)  # either fence kind
    text = re.sub(r"^[ \t]*(?:```|~~~).*", "", text, flags=re.S | re.M)  # an unclosed fence runs to the end
    # An indented (4-space) code line can never match: a trailer must start at column 0 (``_TRAILER`` is anchored).
    for m in _TRAILER.finditer(text):
        if m.group("key").lower() == name.lower():
            v = m.group("value").strip()
            return "" if v.lower().rstrip(". ") in _NOTHING else v
    return ""


def household(body: str) -> str:
    """The ``For-The-Household:`` sentence(s), at most two; "" when missing or "nothing visible"."""
    return _end(_sentences(_dejargon(_trailer(body, "For-The-Household")), 2, 280))


def numbers(body: str) -> list[str]:
    """``Numbers: a -> b; c -> d``: at most 3 items with a digit, each clipped; [] when the line has no digit."""
    raw = _trailer(body, "Numbers")
    if not re.search(r"\d", raw):
        return []
    out = []
    for part in raw.split(";"):
        t = re.sub(r"\s*(?:->|→)\s*", " → ", _dejargon(part.replace("->", " → "))).strip()
        if re.search(r"\d", t):
            out.append(_clip_words(t.rstrip("."), NUMBER_CHARS))
    return out[:NUMBERS_MAX]


def _one(body: str, name: str) -> str:
    return _cap(_end(_sentences(_dejargon(_trailer(body, name)), 1, 200)))


def pr_fields(title: str, body: str) -> dict:
    """What a notice keeps from a PR body (the body itself is never stored)."""
    return {"household": household(body), "numbers": numbers(body), "owner_step": _one(body, "Owner-Step"),
            "if_no_answer": _one(body, "If-No-Answer")}


PR_FIELDS = ("household", "numbers", "owner_step", "if_no_answer")


def subject(title: str) -> str:
    """The title's subject in sentence case: no ``type(scope):``, no ``Revert "..."`` wrapper, no ``(#N)``."""
    t = (title or "").strip()
    rv = re.match(r'^revert:?\s*"?(.+?)"?\s*(?:\(#\d+\))?\s*$', t, re.I)
    t = _dejargon(rv.group(1) if rv else t).rstrip(". ")
    return _clip_words(_cap(t), 120) if t else "A change"


_WORDS = ("no", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")


def _count(n: int, word: str) -> str:
    return f"{_WORDS[n] if n < len(_WORDS) else n} {word if n == 1 else word + 's'}"


def _pick(options: tuple, turn: int) -> str:
    return options[turn % len(options)]


def _when(local: Optional[datetime]) -> str:
    if local is None:
        return "just now"
    h = local.hour
    return ("overnight" if h < 5 or h >= 22 else "this morning" if h < 12 else "this afternoon" if h < 17
            else "this evening")


def _greeting(local: Optional[datetime]) -> str:
    if local is None or local.hour < 5 or local.hour >= 22:
        return ""
    return "Good morning. " if local.hour < 12 else "Good afternoon. " if local.hour < 17 else "Good evening. "


_LEADS_NEW = ("Something new is in and running.", "New, and live now.", "A new change just went live.")
_LEADS_FIX = ("A fix is in and running.", "Fixed, and live now.", "A fix just went live.")
_LEADS_ANY = ("A change is in and running.", "Live now.", "This just went live.")
_HOUSEKEEPING = ("Some housekeeping went in: {s}.", "A behind-the-scenes change is in and running: {s}.",
                 "Housekeeping, nothing you will notice: {s}.")
_REVERT = ('Heads up first: "{s}" was rolled back and is no longer running.',
           'Before anything else: "{s}" was rolled back, so it is no longer running.')
_DECISION = ("I need your OK on something.", "One for you to decide.", "This one is waiting on you.")
_BATCH_HEADS = ("{N} things went in {when}.", "{When}, {n} changes went live.", "{N} changes are now in and running.")
_DIGEST_OPENERS = ("Here's {period}:", "Here's how {period} went:", "Here's the round-up for {period}:")
NOTHING_TO_DO = "Nothing for you to do."
NOTHING_ELSE = "Nothing else for you to do."
FOLLOWS = "The rest of what needs you follows shortly."


def _more(n: int) -> str:
    return f"…and {n} more on GitHub."


# The most text ever appended to a packed chunk: the count line, the follow-up line and the closing line.
SUFFIX_MAX = len("\n" + _more(99999)) + len("\n" + FOLLOWS) + len("\n" + NOTHING_ELSE)
assert LINE_CHARS + SUFFIX_MAX <= CHUNK_CHARS
POINTER_TEXT = "There's an update from Zoe for you. Ask me \"what's new?\" or look on the desktop."


def _hh(item: dict) -> str:
    """The household line. A notice stored before the assistant voice has no ``household`` field, only the old
    ``summary`` (trailer, else first paragraph, else title): that is used, through the same de-jargon path."""
    if "household" in item:
        return item["household"] or ""
    return _end(_sentences(_dejargon(item.get("summary") or ""), 2, 280))


def _is_bad(item: dict) -> bool:
    return item["kind"] == "stuck" or (item["kind"] == "live" and item["category"] == "broke")


def _priority(item: dict) -> int:
    """Bad news first, then what waits on the owner, then the good news."""
    return 0 if _is_bad(item) else 1 if item["kind"] == "raised" else 2


def _ref(item: dict, on: bool) -> str:
    return f" (#{item['number']})" if on and item.get("number") else ""


def _refs(items: list[dict]) -> str:
    nums = [f"#{i['number']}" for i in items if i.get("number")]
    return f" ({', '.join(nums)})" if nums else ""


def _numbers_line(item: dict) -> str:
    return f"By the numbers: {'; '.join(item['numbers'])}." if item.get("numbers") else ""


def _lead(item: dict, turn: int) -> str:
    leads = (_LEADS_NEW if item["category"] in ("ability", "household") else
             _LEADS_FIX if item["category"] == "fixed" else _LEADS_ANY)
    return _pick(leads, turn)


def _story(item: dict, turn: int, *, lead: bool) -> str:
    """One went-live line: the household sentence (with a lead in a single message), else the housekeeping line."""
    hh = _hh(item)
    if item["kind"] == "stuck":
        return item["summary"]
    if _is_bad(item):
        return _pick(_REVERT, turn).format(s=subject(item.get("title", ""))) + (f" {hh}" if hh else "")
    if not hh:
        return _pick(_HOUSEKEEPING, turn).format(s=subject(item.get("title", "")))
    return f"{_lead(item, turn)} {hh}" if lead else hh


def _decision(item: dict, turn: int) -> list[str]:
    what = _hh(item) or f'It is about "{subject(item.get("title", ""))}".'
    ask = item.get("owner_step") or "Can you have a look when you get a minute?"
    out = [f"{_pick(_DECISION, turn)} {what}", ask]
    if item.get("if_no_answer"):
        out.append(f"If you leave it, {_low(item['if_no_answer'])}")
    return out


def _decision_line(item: dict) -> str:
    """A decision as one line inside a batch or a digest (the single message is ``_decision``)."""
    hh, step = _hh(item), item.get("owner_step") or ""
    if not hh and not step:
        return f'"{subject(item.get("title", ""))}" needs a look from you.'
    what = hh or f'"{subject(item.get("title", ""))}":'
    tail = f" If you leave it, {_low(item['if_no_answer'])}" if item.get("if_no_answer") else ""
    return f"{what} {step or 'Can you have a look?'}{tail}"


def _step(item: dict) -> str:
    return f"One thing for you: {item['owner_step']}" if item.get("owner_step") else NOTHING_TO_DO


@dataclass
class Message:
    text: str
    keys: list[str]
    url: Optional[str] = None
    label: Optional[str] = None


def _single(item: dict, detail: str, turn: int) -> str:
    if item["kind"] == "raised":
        return "\n".join(_decision(item, turn))
    lines = [_story(item, turn, lead=True)]
    if item["kind"] == "stuck":
        return lines[0]
    if detail == "technical" and item.get("numbers"):
        lines.append(_numbers_line(item))
    lines.append(_step(item))
    return "\n".join(lines)


def _fit(lines: list[tuple[str, int, bool]], limit: int = CHUNK_CHARS) -> Optional[str]:
    """Fit by dropping only droppable lines (counted as "...and N more"); ``must`` lines (bad news, decisions, the
    owner's steps) are never dropped. ``None`` = the must lines alone do not fit."""
    reserve = len("\n" + _more(sum(n for _, n, _ in lines)))  # the longest count line this message could need
    room = limit + 1 - reserve - sum(len(t) + 1 for t, _, must in lines if must)  # +1: n lines have n-1 joins
    if room < 0:
        return None
    out: list[str] = []
    dropped, at = 0, -1
    for t, n, must in lines:
        if not must and len(t) + 1 > room:
            dropped += n
            at = len(out) if at < 0 else at
            continue
        out.append(t)
        room -= 0 if must else len(t) + 1
    if dropped:
        out.insert(at, _more(dropped))
    return "\n".join(out)


def _word(n: int) -> str:
    return _WORDS[n] if n < len(_WORDS) else str(n)


def _batch(items: list[dict], detail: str, turn: int, local: Optional[datetime], *, numbers_on: bool = True,
           hh_cap: int = 0) -> list[tuple[str, int, bool]]:
    """The batched instant message as (line, items it stands for, must): bad news, then waiting on you, then the
    rest, then every owner step (from any kind that carries one) or the closing line."""
    tech = detail == "technical"
    bad = [i for i in items if _is_bad(i)]
    waiting = [i for i in items if i["kind"] == "raised"]
    good = [i for i in items if not _is_bad(i) and i["kind"] != "raised"]
    lines = [(_story(i, turn, lead=False) + _ref(i, tech and i["kind"] != "stuck"), 1, True) for i in bad]
    if waiting:
        lines.append(("Waiting on you:", 0, True))
        lines += [("• " + _decision_line(i) + _ref(i, tech), 1, True) for i in waiting]
    if good:
        n, when = len(good), _when(local)
        head = (f"One change went in {when}." if n == 1 else
                _pick(_BATCH_HEADS, turn).format(N=_cap(_word(n)), n=_word(n), when=when, When=_cap(when)))
        lines.append((f"Also, {_low(head)}" if bad or waiting else head, 0, True))
    stories = [i for i in good if _hh(i)]
    chores = [i for i in good if not _hh(i)]
    for i in stories:
        hh = _clip_words(_hh(i), hh_cap) if hh_cap else _hh(i)
        lines.append((f"• {hh}{_ref(i, tech)}", 1, False))
        if tech and numbers_on and i.get("numbers"):
            lines.append((f"   {_numbers_line(i)}", 0, False))
    refs = _refs(chores) if tech else ""
    if stories and chores:
        lines.append((f"{_pick(('Plus', 'And'), turn)} {_count(len(chores), 'bit')} of housekeeping{refs}.",
                      len(chores), False))
    elif len(chores) == 1:
        lines.append((f"• {_story(chores[0], turn, lead=False)}{_ref(chores[0], tech)}", 1, False))
    elif chores:
        who = "Both were" if len(chores) == 2 else f"All {_word(len(chores))} were"
        lines.append((f"{who} housekeeping, nothing you will notice{refs}.", len(chores), False))
    steps = [i for i in bad + good if i.get("owner_step")]  # a decision's step is its question, on its own line
    if steps:
        lines.append(("For you:", 0, True))
        lines += [(f"• {i['owner_step']}", 0, True) for i in steps]  # its number is already on the item's line
    elif not any(i["kind"] == "stuck" for i in bad):  # a stuck deploy already says it may need a look
        lines.append((NOTHING_ELSE if waiting else NOTHING_TO_DO, 0, True))
    return lines


def _must(item: dict) -> bool:
    return bool(item.get("owner_step")) or _is_bad(item) or item["kind"] == "raised"


def render_instant(items: list[dict], detail: str, repo_name: str, *, turn: int = 0,
                   local: Optional[datetime] = None) -> Message:
    """One message. Its ``keys`` are the notices it LANDS: a notice whose owner step could not be shown is left
    out (the caller sends it in a follow-up), so a step is never lost to truncation."""
    if detail == "pointer":
        return Message(POINTER_TEXT, [i["key"] for i in items])
    pending = sorted(items, key=_priority)
    while len(pending) > 1:
        tries = [_batch(pending, detail, turn, local, **kw)
                 for kw in ({}, {"hh_cap": 100}, {"hh_cap": 100, "numbers_on": False})]
        # household lines are shortened first, then the numbers go, then whole lines become "...and N more"
        text = next(("\n".join(t for t, _, _ in ls) for ls in tries
                     if len("\n".join(t for t, _, _ in ls)) <= CHUNK_CHARS), None) or _fit(tries[-1])
        if text is not None:
            return Message(text, [i["key"] for i in pending], f"https://github.com/{repo_name}/pulls",
                           "Open on GitHub")
        pending.remove([i for i in pending if _must(i)][-1])  # the steps do not fit: defer the last one
    it = pending[0]
    label = "Deploy runs" if it["kind"] == "stuck" else f"Open #{it['number']}" if it.get("number") else "Open"
    return Message(_clip(_single(it, detail, turn)), [it["key"]], it.get("url"), label)


def _clip(text: str, limit: int = CHUNK_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def render_digest(items: list[dict], uncertain: list[dict], prefs: dict, *, opener: int, repo_name: str,
                  local: Optional[datetime] = None, period: str = "today") -> list[Message]:
    """The digest as up to ``MAX_CHUNKS`` messages of at most ``CHUNK_CHARS``. Every key lands in exactly one."""
    cats = prefs["categories"]

    def tech(i: dict) -> bool:
        return cats.get(i["category"], cats["decision"])["detail"] == "technical"

    live = [i for i in items if i["category"] != "raised"]  # live changes, stuck deploys, decisions rolled over
    raised = [i for i in items if i["category"] == "raised"]
    if all(cats[c]["detail"] == "pointer" for c in comm_prefs.CATEGORIES):
        return [Message(f"Zoe has {len(items)} update{'s' if len(items) != 1 else ''} for you. Ask me \"what's new?\".",
                        [i["key"] for i in items + uncertain])]
    lines: list[tuple[str, list[str]]] = []  # (text, keys this line accounts for)
    n_live = sum(1 for i in live if i["kind"] == "live")
    went = f"{_count(n_live, 'change')} went live." if n_live else "nothing new went live."
    lines.append((f"{_greeting(local)}{_pick(_DIGEST_OPENERS, opener).format(period=period)} {went}", []))
    # The short lines go first, so a truncated digest can never swallow them into "...and N more".
    drafts = sum(1 for i in raised if i.get("draft"))
    tail = (f" ({'one is a draft' if drafts == 1 else _word(drafts) + ' are drafts'})" if drafts else "")
    if raised and prefs.get("raised") != "list":
        lines.append((f"{_cap(_count(len(raised), 'new change'))} {'is' if len(raised) == 1 else 'are'} open and not "
                      f"merged yet{tail}.", [i["key"] for i in raised]))
    # An uncertain notice may never have been seen: one line each, carrying only its own key, and MUST-KEEP (held for
    # the follow-up, never counted unseen), so a clipped or overflowing line can never mark another notice delivered.
    def _was(i: dict) -> str:
        return f'"{subject(i.get("title", ""))}"{_ref(i, True)}' if i.get("number") else i["key"]

    if len(uncertain) == 1:
        lines.append((f"This may not have reached you earlier: {_was(uncertain[0])}.", [uncertain[0]["key"]]))
    elif uncertain:
        lines.append(("These may not have reached you earlier:", []))
        lines += [(f"• {_was(i)}", [i["key"]]) for i in uncertain]
    bad = [i for i in live if _is_bad(i)]
    good = [i for i in live if i["kind"] == "live" and not _is_bad(i)]
    steps = [i for i in bad + good if i.get("owner_step")]
    hold = {i["key"] for i in steps} | {i["key"] for i in uncertain}  # land only on their own line, never counted

    def own(i: dict) -> list[str]:  # the key rides on the item's own line unless its step line carries it
        return [] if i["key"] in hold else [i["key"]]

    for i in bad:  # bad news with a step carries it inline, so its key lands with its own (first) line
        step = f"\n   For you: {i['owner_step']}" if i.get("owner_step") else ""
        lines.append((_story(i, opener, lead=False) + _ref(i, tech(i) and i["kind"] != "stuck") + step, [i["key"]]))
    waiting = [i for i in live if i["category"] == "decision"]
    if waiting:
        lines.append(("\nWaiting on you:", []))
        lines += [("• " + _decision_line(i) + _ref(i, tech(i)), [i["key"]]) for i in waiting]
        hold |= {i["key"] for i in waiting if i.get("owner_step")}  # a decision's step is its question
    good_steps = [i for i in good if i.get("owner_step")]
    if good_steps:
        lines.append(("\nFor you to do:", []))
        lines += [(f"• {i['owner_step']}", [i["key"]]) for i in good_steps]  # its number is on the item's line
    stories = [i for i in good if _hh(i)]
    if stories and len(lines) > 1:
        lines.append(("\nWhat went in:", []))
    for i in stories:
        extra = f"\n   {_numbers_line(i)}" if tech(i) and i.get("numbers") else ""
        lines.append((f"• {_hh(i)}{_ref(i, tech(i))}{extra}", own(i)))
    chores = [i for i in good if not _hh(i)]
    if chores:
        refs = _refs([i for i in chores if tech(i)])
        lead = "Plus " if stories else ""
        word = _count(len(chores), "bit")
        lines.append((f"{_cap(lead + word)} of housekeeping{refs}{'' if stories else ', nothing you will notice'}.",
                      [k for i in chores for k in own(i)]))
    if raised and prefs.get("raised") == "list":
        lines.append((f"\nOpen and not merged yet ({len(raised)}){tail}:", []))
        for i in raised:
            lines.append((f"• {subject(i.get('title', ''))}{' (draft)' if i.get('draft') else ''}{_ref(i, tech(i))}",
                          [i["key"]]))
    stuck = any(i["kind"] == "stuck" for i in bad)  # a stuck deploy already says it may need a look
    done = "" if steps or stuck or not n_live else NOTHING_ELSE if waiting else NOTHING_TO_DO
    return _pack(lines, f"https://github.com/{repo_name}/pulls?q=is%3Apr+is%3Amerged", "Merged PRs on GitHub", done,
                 hold=hold)


def _suffix(rest: list[tuple[str, list[str]]], tail: str, hold: frozenset | set) -> str:
    """Exactly what the last chunk gets appended when ``rest`` does not fit: the count of what is left (held keys
    excluded: they follow up instead), the follow-up line when any held key is left, and the closing line."""
    n = sum(1 for _, ks in rest for k in ks if k not in hold)
    held = any(k in hold for _, ks in rest for k in ks)
    return ((f"\n{_more(n)}" if n else "") + (f"\n{FOLLOWS}" if held else "") + (f"\n{tail}" if tail else ""))


def _pack(lines: list[tuple[str, list[str]]], url: str, label: str, tail: str = "",
          hold: frozenset | set = frozenset()) -> list[Message]:
    """Lines into at most ``MAX_CHUNKS`` messages of at most ``CHUNK_CHARS``. When the last allowed message cannot
    take everything, the WHOLE suffix (count, follow-up line, closing line, with their real numbers) is computed
    first and lines are moved out of that message until message plus suffix fits. A ``hold`` key (one with an
    owner step) lands only if its line is shown: left over, it is in no message and the caller follows up."""
    lines = [(_clip(t, LINE_CHARS), ks) for t, ks in lines] + ([(tail, [])] if tail else [])
    chunks: list[Message] = []
    i = 0
    while i < len(lines):
        take: list[int] = []
        size = 0
        for j in range(i, len(lines)):
            t = lines[j][0] if take else lines[j][0].lstrip("\n")
            if take and size + 1 + len(t) > CHUNK_CHARS:
                break
            size += len(t) + (1 if take else 0)
            take.append(j)
        end = take[-1] + 1
        if end < len(lines) and len(chunks) == MAX_CHUNKS - 1:  # the last allowed message overflows
            while True:
                suffix = _suffix(lines[end:], tail, hold)
                body = "\n".join(lines[k][0] if n else lines[k][0].lstrip("\n") for n, k in enumerate(range(i, end)))
                if len(body) + len(suffix) <= CHUNK_CHARS or end == i:
                    break
                end -= 1  # this line moves into the count (or, if held, into the follow-up)
            text = (body + suffix) if body else suffix.lstrip("\n")
            keys = [k for _, ks in lines[i:end] for k in ks] + [k for _, ks in lines[end:] for k in ks
                                                                 if k not in hold]
            chunks.append(Message(text, keys, url, label))
            return chunks
        body = "\n".join(lines[k][0] if n else lines[k][0].lstrip("\n") for n, k in enumerate(range(i, end)))
        keys = [k for _, ks in lines[i:end] for k in ks]
        chunks.append(Message(body, keys, url if end == len(lines) else None, label if end == len(lines) else None))
        i = end
    return chunks


# ── recipients (OWNER ONLY in P1) ─────────────────────────────────────────────
@dataclass
class Recipient:
    user_id: str
    role: str
    telegram_id: str
    prefs: dict = field(default_factory=dict)
    env_default: bool = False  # prefs came from the operator's env default, not the member's saved document


class Recipients(list):
    """The recipient list plus WHY it is what it is, as counts only (no ids): ``diag`` goes into the
    ``no_recipients`` audit line, so an empty tick says which step is missing."""

    diag: dict

    def __init__(self, items=(), **diag: Any) -> None:
        super().__init__(items)
        self.diag = diag


async def load_recipients(db=None) -> Recipients:
    """Admin members with a linked Telegram id and preferences that let them receive anything.

    The set comes from the ``users`` table (a member) filtered to the owner roles, and the chat id ONLY from that
    member's own ``user_prefs.telegram_id`` (set by ``telegram_link``). A non-member, a guest or a non-owner member
    is never a recipient; the bot additionally refuses any chat that is not a linked member (403)."""
    from contextlib import asynccontextmanager

    from user_prefs import read_prefs

    @asynccontextmanager
    async def _ctx():
        if db is not None:
            yield db
            return
        from database import get_db_ctx

        async with get_db_ctx() as conn:
            yield conn

    out: list[Recipient] = []
    linked = 0
    async with _ctx() as conn:
        cur = await conn.execute("SELECT id, role FROM users WHERE role IN ('admin', 'family-admin') ORDER BY id")
        rows = list(await cur.fetchall())
        for row in rows:
            uid, role = str(row["id"]), str(row["role"])
            prefs = await read_prefs(conn, uid)
            tid = prefs.get("telegram_id")
            raw, from_env = comm_prefs.with_env_default(prefs.get(comm_prefs.PREF_KEY))
            doc = comm_prefs.normalise(raw, role)
            if not (isinstance(tid, str) and tid.isdigit()):
                continue
            linked += 1
            if doc is not None:
                out.append(Recipient(uid, role, tid, doc, env_default=from_env))
        other_case = 0
        if not rows:  # is it "no owner at all" or an owner role spelled differently (the match above is exact)?
            cur = await conn.execute("SELECT id, role FROM users")
            other_case = sum(1 for r in await cur.fetchall()
                             if str(r["role"] or "").strip().lower() in comm_prefs.OWNER_ROLES)
    reason = ("ok" if out else "no_prefs" if linked else "no_link" if rows else
              "owner_role_spelling" if other_case else "no_admin")
    return Recipients(out, reason=reason, admins=len(rows), linked=linked, owner_role_other_case=other_case)


# ── the tick ──────────────────────────────────────────────────────────────────
Sender = Callable[[str, Message], Awaitable[str]]


async def telegram_sender(telegram_id: str, msg: Message) -> str:
    return await telegram_notify.send(telegram_id, msg.text, msg.url, msg.label)


@dataclass
class Ctx:
    st: dict
    now: datetime
    mode: str
    sender: Sender
    sleep: Callable[[float], Awaitable[Any]]
    repo: str
    new_keys: list[str] = field(default_factory=list)
    sent_messages: int = 0

    def persist(self) -> None:
        save_state(self.st)


def _new_notice(c: Ctx, item: dict, *, silent: bool) -> None:
    key = item["key"]
    seen = key in c.st["seen"]
    c.st["seen"][key] = _iso(c.now)  # "last observed": a PR open for months is never pruned and re-announced
    if seen:
        return
    if silent:
        return
    item["at"] = _iso(c.now)
    c.st["notices"][key] = item
    c.new_keys.append(key)
    audit("notice", key=key, category=item["category"], kind=item["kind"])


async def _collect(c: Ctx, gh: GitHub, *, silent: bool) -> None:
    st = c.st
    try:
        for pr in await gh.open_prs():
            n = int(pr["number"])
            labels = {str(lb.get("name", "")).lower() for lb in pr.get("labels") or [] if isinstance(lb, dict)}
            item = {"kind": "raised", "number": n, "title": pr.get("title", ""), "url": pr.get("url"),
                    **pr_fields(pr.get("title", ""), pr.get("body", "")), "draft": bool(pr.get("isDraft"))}
            if NEEDS_OWNER_LABEL in labels:  # waits on the owner: an instant-capable decision, not a digest line
                c.st["seen"][f"pr:{n}:raised"] = _iso(c.now)
                _new_notice(c, {**item, "key": f"pr:{n}:decision", "category": "decision"}, silent=silent)
            else:
                _new_notice(c, {**item, "key": f"pr:{n}:raised", "category": "raised"}, silent=silent)
    except Exception as exc:  # noqa: BLE001 - one bad source never stops the others
        audit("source_error", source="open", error=type(exc).__name__)
    try:
        since = (c.now - timedelta(days=2)).strftime("%Y-%m-%d")
        for pr in await gh.merged_prs(since):
            n = int(pr["number"])
            key = f"pr:{n}:merged"
            seen = key in st["seen"]
            st["seen"][key] = _iso(c.now)
            if seen:
                continue
            sha = ((pr.get("mergeCommit") or {}).get("oid") or "").strip()
            base = str(pr.get("baseRefName") or DEPLOY_BRANCH)
            if silent or not sha or base != DEPLOY_BRANCH:
                continue  # merged into a stacked/feature branch: it never deploys, so it can never be "stuck"
            title = pr.get("title", "")
            st["pending_live"][str(n)] = {"number": n, "title": title, "sha": sha, "url": pr.get("url"),
                                          "merged_at": pr.get("mergedAt") or _iso(c.now), "base": base,
                                          **pr_fields(title, pr.get("body", "")),
                                          "category": category_for_title(title)}
            # A merge supersedes its raised / waiting-on-you notice wherever it is still unsent: a digest line, or an
            # instant one held by quiet hours or a retry backoff (design 5.4).
            for dkey, d in st["deliveries"].items():
                if dkey.endswith((f"|pr:{n}:raised", f"|pr:{n}:decision")) and d["status"] == "queued":
                    d["status"] = "superseded"
    except Exception as exc:  # noqa: BLE001
        audit("source_error", source="merged", error=type(exc).__name__)

    deployed = read_deployed_sha()
    stuck: list[dict] = []
    checks = 0
    for n_s, p in sorted(st["pending_live"].items(), key=lambda kv: kv[1]["merged_at"]):
        merged_at = _parse(p["merged_at"]) or c.now
        if c.now - merged_at > PENDING_MAX_AGE:
            st["pending_live"].pop(n_s, None)
            audit("pending_expired", number=p["number"])
            continue
        if "base" not in p:  # recorded before the base filter: drop one that was never merged into the deploy branch
            try:
                on_main = await gh.contains(DEPLOY_BRANCH, p["sha"])
            except Exception:  # noqa: BLE001
                on_main = None
            if on_main is False:
                st["pending_live"].pop(n_s, None)
                audit("pending_dropped", number=p["number"], reason="not_on_deploy_branch")
                continue
            if on_main:
                p["base"] = DEPLOY_BRANCH
        live: Optional[bool] = None
        if deployed and checks < LIVE_CHECKS_PER_TICK:
            checks += 1
            try:
                live = await gh.contains(deployed, p["sha"])
            except Exception as exc:  # noqa: BLE001
                audit("source_error", source="contains", error=type(exc).__name__)
        if live:
            st["pending_live"].pop(n_s, None)
            _new_notice(c, {"key": f"pr:{p['number']}:live", "kind": "live", "number": p["number"],
                            "title": p["title"], "url": p.get("url"), "category": p["category"],
                            "summary": p.get("summary") or "",  # a merge stored before the upgrade has only this
                            **{k: p[k] for k in PR_FIELDS if k in p}}, silent=silent)
        elif (live is False or not deployed) and c.now - merged_at > stale_after():
            stuck.append(p)  # definitely not in the deployed code (an unchecked or unknown one is not counted)
    if stuck and not silent:
        run = None
        try:
            run = await gh.latest_deploy_run()
        except Exception:  # noqa: BLE001
            run = None
        _new_notice(c, {"key": f"deploy:stuck:{deployed or 'none'}", "kind": "stuck", "category": "broke",
                        "title": "Merged but not live", "summary": stuck_text(stuck, stale_after(), run),
                        "url": (run or {}).get("url") or f"https://github.com/{c.repo}/actions/workflows/deploy.yml"},
                    silent=silent)


_RUN_WORDS = {"failure": "failed", "cancelled": "was cancelled", "timed_out": "timed out", "startup_failure": "failed",
              "in_progress": "is still running", "queued": "is still waiting to start", "success": "finished"}


def stale_words(after: timedelta) -> str:
    """The stale threshold in words ("an hour", "2 hours", "90 minutes"): a CONFIGURED wait, never the time since anything."""
    mins = int(after.total_seconds() // 60)
    return ("an hour" if mins == 60 else f"{mins // 60} hours") if mins % 60 == 0 else f"{mins} minutes"


def stuck_text(stuck: list[dict], after: timedelta, run: Optional[dict]) -> str:
    """Bad news, plainly and first: which changes did not go live, how long, and how the last deploy ended."""
    names = [f'"{subject(p.get("title", ""))}"' for p in stuck[:3]]
    if len(stuck) > 3:
        what = f"{', '.join(names)} and {len(stuck) - 3} more"
    else:
        what = names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"
    ago = stale_words(after)
    one = len(stuck) == 1
    word = _RUN_WORDS.get(str((run or {}).get("conclusion") or (run or {}).get("status") or "")) if run else None
    refs = ", ".join(f"#{p['number']}" for p in stuck[:12]) + (f" and {len(stuck) - 12} more" if len(stuck) > 12 else "")
    lead = f"Heads up: {what} did not go live." if one else \
        f"Heads up: {_count(len(stuck), 'change')} did not go live: {what}."
    return (f"{lead} {'It was' if one else 'They were'} merged over {ago} ago and {'is' if one else 'are'} not "
            f"running yet.{f' The last deploy {word}.' if word else ''} It may need a look. ({refs})")


def _fan_out(c: Ctx, recipients: list[Recipient]) -> None:
    for key in c.new_keys:
        item = c.st["notices"][key]
        for r in recipients:
            dkey = f"{r.user_id}|{key}"
            if dkey in c.st["deliveries"]:
                continue
            if item["category"] == "raised":  # an ordinary open PR: a digest line, governed by prefs.raised
                lane = "never" if r.prefs.get("raised") == "none" else "digest"
                channels = r.prefs["categories"]["decision"]["channels"]
            else:
                cat = r.prefs["categories"][item["category"]]
                lane, channels = cat["cadence"], cat["channels"]
            status = "queued"
            if lane == "never":
                status = "skipped"
            elif "telegram" not in channels:
                status = "no_channel"  # P1 has only the Telegram transport; other channels arrive in P2/P3
            c.st["deliveries"][dkey] = {"status": status, "lane": lane if lane != "never" else "digest",
                                        "attempts": 0, "next_at": None, "at": _iso(c.now)}
            if status != "queued":
                audit("delivery", member=r.user_id, key=key, status=status)


def _ready(d: dict, now: datetime) -> bool:
    nxt = _parse(d.get("next_at"))
    return d["status"] == "queued" and (nxt is None or nxt <= now)


async def _send(c: Ctx, r: Recipient, msg: Message) -> str:
    """Claim -> persist -> send -> record. A crash between claim and record leaves ``sending``, which the next
    tick turns into ``uncertain`` (listed once in the next digest), never into a second send."""
    ds = c.st["deliveries"]
    if len(msg.text) > CHUNK_CHARS:  # our own bug, never the member's loss: not sent, not claimed, not refused
        audit("oversize_held", member=r.user_id, keys=msg.keys, chars=len(msg.text), code="over_chunk_chars")
        return "oversize"  # the keys stay queued for the next tick
    for k in msg.keys:
        ds[f"{r.user_id}|{k}"]["status"] = "sending"
    c.persist()
    if c.sent_messages:
        await c.sleep(SEND_GAP_S)
    if c.mode == "shadow":
        outcome = "shadow"
        audit("shadow_send", member=r.user_id, keys=msg.keys, text=msg.text, url=msg.url)
    else:
        outcome = await c.sender(r.telegram_id, msg)
        audit("send", member=r.user_id, keys=msg.keys, outcome=outcome, chars=len(msg.text))
    c.sent_messages += 1
    for k in msg.keys:
        d = ds[f"{r.user_id}|{k}"]
        if outcome in ("sent", "shadow"):
            d["status"] = "uncertain_reported" if d.get("uncertain") else outcome
            d["sent_at"] = _iso(c.now)
        elif outcome == "refused":
            d["status"] = "refused"
        elif outcome == "uncertain":  # the "may not have reached you" line itself is never mentioned a second time
            d["status"] = "uncertain_reported" if d.get("uncertain") else "uncertain"
        else:  # retry: definitely not delivered
            d["attempts"] = int(d.get("attempts", 0)) + 1
            if d["attempts"] >= MAX_ATTEMPTS:
                d["status"] = "failed"
                audit("delivery_failed", member=r.user_id, key=k)
            else:
                d["status"] = "uncertain" if d.get("uncertain") else "queued"
                d["next_at"] = _iso(c.now + timedelta(minutes=min(360, 5 * 2 ** d["attempts"])))
    c.persist()
    return outcome


def _local(c: Ctx) -> datetime:
    from time_utils import zoe_timezone

    return c.now.astimezone(zoe_timezone())


async def _deliver_instant(c: Ctx, r: Recipient) -> None:
    ds, st = c.st["deliveries"], c.st
    ready = [(k.split("|", 1)[1], d) for k, d in ds.items()
             if k.startswith(f"{r.user_id}|") and d["lane"] == "instant" and _ready(d, c.now)]
    if not ready:
        return
    local = _local(c)
    if comm_prefs.in_quiet_hours(r.prefs, local.hour * 60 + local.minute):
        for key, d in ready:  # nobody is ever woken (floor 3.2): held, and sent when the window ends
            if not d.get("held_quiet"):
                d["held_quiet"] = True
                audit("held_quiet", member=r.user_id, key=key)
        return
    today = local.strftime("%Y-%m-%d")
    sent_today = [t for t in st["instant_sent"].get(r.user_id, []) if t.startswith(today)]
    cap = int(r.prefs.get("daily_instant_cap", 6))
    go: list[tuple[str, dict]] = []
    for key, d in ready:
        cat = st["notices"][key]["category"]
        last = _parse(st["last_instant"].get(f"{r.user_id}|{cat}"))
        # An env-defaulted owner asked for "a message every time a PR lands": no 30-minute spacing (the cap still holds).
        same_kind_recent = (not r.env_default) and last is not None and c.now - last < SAME_KIND_SPACING
        if len(sent_today) >= cap or same_kind_recent:
            d["lane"] = "digest"  # rolls into the next digest; nothing is dropped (design rule 9)
            audit("rolled_to_digest", member=r.user_id, key=key, reason="cap" if len(sent_today) >= cap else "spacing")
            continue
        go.append((key, d))
    if not go:
        return
    items = [st["notices"][k] for k, _ in go]
    details = {r.prefs["categories"].get(i["category"], {}).get("detail", "technical") for i in items}
    detail = "pointer" if "pointer" in details else "plain" if details == {"plain"} else "technical"
    items.sort(key=_priority)  # bad news first, then what waits on the owner (stable within each)
    batches = [items] if len(items) >= BATCH_AT else [[i] for i in items]

    def roll(left: list[dict], reason: str) -> None:  # exactly the over-cap path above: the next digest, audited
        for i in left:
            ds[f"{r.user_id}|{i['key']}"]["lane"] = "digest"
            audit("rolled_to_digest", member=r.user_id, key=i["key"], reason=reason)

    sent_now = 0  # instant messages to this member in this tick (design 6: at most MAX_CHUNKS a minute)
    for batch in batches:
        follow_up = False
        while batch:
            # The base's messages (one per batch) go exactly as before this PR: the selection above is their only
            # check. A FOLLOW-UP (a notice whose step did not fit the message before) is new, so it is rechecked
            # against every limit and can never overrun one; what does not fit rolls into the digest.
            if follow_up:
                if killed():
                    return  # the kill switch appeared mid-tick: the rest stays queued, unsent
                now_local = _local(c)
                if comm_prefs.in_quiet_hours(r.prefs, now_local.hour * 60 + now_local.minute):
                    return  # quiet hours began: held, as at the start of a tick
                if not r.env_default:
                    spaced = [i for i in batch if c.now - (_parse(st["last_instant"].get(
                        f"{r.user_id}|{i['category']}")) or c.now - SAME_KIND_SPACING) < SAME_KIND_SPACING]
                    roll(spaced, "spacing")
                    batch = [i for i in batch if i not in spaced]
                    if not batch:
                        break
                over = "cap" if len(sent_today) >= cap else "rate" if sent_now >= MAX_CHUNKS else ""
                if over:
                    roll(batch, over)
                    break
            turn = int(st["opener"].get(r.user_id, -1)) + 1  # the digest's counter: two in a row never match
            st["opener"][r.user_id] = turn
            msg = render_instant(batch, detail, c.repo, turn=turn, local=local)
            outcome = await _send(c, r, msg)
            if outcome not in ("sent", "shadow", "uncertain"):
                break  # not delivered: its notices retry with backoff, the unclaimed rest stays queued
            sent_now += 1
            sent_today.append(local.isoformat())
            st["instant_sent"].setdefault(r.user_id, []).append(local.isoformat())
            for i in batch:
                if i["key"] in msg.keys:
                    st["last_instant"][f"{r.user_id}|{i['category']}"] = _iso(c.now)
            batch = [i for i in batch if i["key"] not in msg.keys]
            follow_up = True


def _due(c: Ctx, r: Recipient, lane: str) -> Optional[str]:
    """The period id when ``lane``'s digest is due now and not yet sent for it, else ``None``."""
    local = _local(c)
    dg = r.prefs["digest"]
    if lane == "digest":
        period, at = local.strftime("%Y-%m-%d"), dg["daily_at"]
    else:
        if comm_prefs.WEEKDAYS[local.weekday()] != dg["weekly_on"]:
            return None
        iso = local.isocalendar()
        period, at = f"{iso[0]}-W{iso[1]:02d}", dg["weekly_at"]
    h, m = (int(x) for x in at.split(":"))
    if comm_prefs.in_quiet_hours(r.prefs, h * 60 + m):  # a digest time inside the quiet window waits for its end
        h, m = divmod(comm_prefs.quiet_window(r.prefs)[1], 60)
    if (local.hour, local.minute) < (h, m):
        return None
    if c.st["digest_sent"].get(f"{r.user_id}|{lane}") == period:
        return None
    return period


async def _deliver_digest(c: Ctx, r: Recipient, lane: str) -> None:
    period = _due(c, r, lane)
    if period is None:
        return
    local = _local(c)
    if comm_prefs.in_quiet_hours(r.prefs, local.hour * 60 + local.minute):
        return
    ds, st = c.st["deliveries"], c.st
    mine = [(k.split("|", 1)[1], d) for k, d in ds.items() if k.startswith(f"{r.user_id}|")]
    items = [st["notices"][k] for k, d in mine if d["lane"] == lane and _ready(d, c.now) and k in st["notices"]]
    uncertain = [st["notices"][k] for k, d in mine if d["status"] == "uncertain" and k in st["notices"]
                 and lane == "digest" and (_parse(d.get("next_at")) or c.now) <= c.now]
    if not items and not uncertain:
        return  # an empty digest is not sent (design 5.4)
    for k, d in mine:
        if d["status"] == "uncertain" and any(u["key"] == k for u in uncertain):
            d["uncertain"] = True
            d["status"] = "queued"  # claimable once more, ONLY as the one "may not have reached you" line
    idx = int(st["opener"].get(r.user_id, -1)) + 1
    msgs = render_digest(items, uncertain, r.prefs, opener=idx, repo_name=c.repo, local=local,
                         period="today" if lane == "digest" else "this week")
    st["opener"][r.user_id] = idx
    outcomes = [await _send(c, r, m) for m in msgs]
    landed = {k for m, o in zip(msgs, outcomes) if o != "oversize" for k in m.keys}
    for u in uncertain:  # not shown this time: it stays an "uncertain" mention for the next digest, never a resend
        d = ds[f"{r.user_id}|{u['key']}"]
        if u["key"] not in landed and d["status"] == "queued":
            d["status"] = "uncertain"
    left = [i["key"] for i in items + uncertain if i["key"] not in landed]
    if left:  # a step or an uncertain mention that did not fit: it follows next tick
        audit("digest_follow_up", member=r.user_id, keys=left)
    elif all(o != "retry" for o in outcomes):
        st["digest_sent"][f"{r.user_id}|{lane}"] = period


def _recover(c: Ctx) -> None:
    for k, d in c.st["deliveries"].items():
        if d["status"] == "sending":  # crashed mid-send: it may have gone out, so it is never sent again as itself
            d["status"] = "uncertain_reported" if d.get("uncertain") else "uncertain"
            audit("uncertain", member=k.split("|", 1)[0], key=k.split("|", 1)[1])


def _prune(c: Ctx) -> None:
    cutoff = c.now - KEEP_SEEN
    st = c.st
    st["seen"] = {k: v for k, v in st["seen"].items() if (_parse(v) or c.now) > cutoff}
    st["deliveries"] = {k: d for k, d in st["deliveries"].items()
                        if not (d["status"] in FINAL and (_parse(d.get("at")) or c.now) <= cutoff)}
    live = {k.split("|", 1)[1] for k in st["deliveries"]}
    st["notices"] = {k: v for k, v in st["notices"].items() if k in live or (_parse(v.get("at")) or c.now) > cutoff}
    for uid, ts in list(st["instant_sent"].items()):
        st["instant_sent"][uid] = [t for t in ts if (_parse(t) or c.now) > c.now - timedelta(days=2)]


async def tick(*, gh: Optional[GitHub] = None, sender: Sender = telegram_sender,
               recipients: Optional[Callable[[], Awaitable[list[Recipient]]]] = None,
               now: Optional[datetime] = None, sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
               run_mode: Optional[str] = None) -> dict:
    """One pass. Every dependency is injectable; the defaults are the live box's."""
    m = run_mode or mode()
    if m == "off":
        return {"mode": "off"}
    if killed():
        return {"mode": m, "killed": True}
    st = await asyncio.to_thread(load_state)
    c = Ctx(st=st, now=now or datetime.now(timezone.utc), mode=m, sender=sender, sleep=sleep, repo=repo())
    _recover(c)
    silent = not st.get("bootstrapped")
    await _collect(c, gh or GhCli(), silent=silent)
    if silent:
        st["bootstrapped"] = True
        audit("bootstrapped", seen=len(st["seen"]))
    members = await (recipients or load_recipients)()
    if not members and c.new_keys:  # new notices with nobody to send them to are otherwise lost without a trace
        audit("no_recipients", new=len(c.new_keys), **(getattr(members, "diag", None) or {"reason": "unknown"}))
    _fan_out(c, members)
    c.persist()
    if m == "on" and sender is telegram_sender and not telegram_notify.available():
        # No route configured (ZOE_INTERNAL_TOKEN unset or ZOE_TELEGRAM_NOTIFY_URL=off): hold, do not burn retries.
        audit("transport_unavailable")
        members = []
    for r in members:
        await _deliver_instant(c, r)
        await _deliver_digest(c, r, "digest")
        await _deliver_digest(c, r, "weekly")
    _prune(c)
    c.persist()
    return {"mode": m, "new": len(c.new_keys), "messages": c.sent_messages, "recipients": len(members),
            "pending_live": len(st["pending_live"])}


_LOCK = asyncio.Lock()


async def run_loop() -> None:
    """Started from ``main.py`` only when ``ZOE_CHANGE_NOTICES`` is ``shadow`` or ``on``. Sleeps first, so a restart
    never fires a burst; one tick at a time; a failing tick is logged and the loop carries on."""
    while True:
        await asyncio.sleep(interval_s())
        try:
            async with _LOCK:
                summary = await tick()
            if summary.get("messages"):
                logger.info("change_notices: %s", summary)
        except Exception as exc:  # noqa: BLE001
            logger.warning("change_notices: tick failed (%s)", type(exc).__name__)
            audit("tick_error", error=type(exc).__name__)


def status() -> dict:
    """Counts only (no notice text): for the admin status route."""
    st = load_state(repair=False)
    counts: dict[str, int] = {}
    for d in st["deliveries"].values():
        counts[d["status"]] = counts.get(d["status"], 0) + 1
    return {"mode": mode(), "killed": killed(), "bootstrapped": bool(st.get("bootstrapped")),
            "pending_live": len(st["pending_live"]), "deliveries": counts,
            "telegram_route": telegram_notify.available(), "deployed_sha": read_deployed_sha()[:12]}
