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

Rendering is deterministic, English only (owner decision 14): the PR title plus one or two sentences from the PR
body (its ``For-The-Household:`` trailer when present). No model and no external API ever sees PR content.

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
LINE_CHARS = 900  # one digest line, so a line plus the "...and N more" tail always fits a chunk
MORE_RESERVE = 40
SAME_KIND_SPACING = timedelta(minutes=30)
BATCH_AT = 3  # this many instant notices ready at once go out as ONE message
MAX_ATTEMPTS = 8
LIVE_CHECKS_PER_TICK = 30
PENDING_MAX_AGE = timedelta(days=7)
KEEP_SEEN = timedelta(days=30)
SEND_GAP_S = 1.1  # Telegram: about one message a second per chat
AUDIT_MAX_BYTES = 5 * 1024 * 1024
NEEDS_OWNER_LABEL = "needs-owner"

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
                                 "--json", "number,title,body,url,mergedAt,mergeCommit,labels"]) or []

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


# ── rendering (deterministic, English) ────────────────────────────────────────
_CC = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<subject>.+)$", re.I)
_TYPE_CATEGORY = {
    "feat": "ability",
    "fix": "fixed", "perf": "fixed", "security": "fixed",
    "revert": "broke",
    "docs": "under_the_hood", "doc": "under_the_hood", "test": "under_the_hood", "tests": "under_the_hood",
    "chore": "under_the_hood", "ci": "under_the_hood", "build": "under_the_hood", "refactor": "under_the_hood",
    "style": "under_the_hood", "deps": "under_the_hood",
}
_TRAILER = re.compile(r"^(?:Co-Authored-By|Signed-off-by|Zoe-Origin|Zoe-Notice|For-The-Household|For-Children|"
                      r"Struggle-Cluster|Refs|Closes|Fixes):\s", re.I)
_HOUSEHOLD = re.compile(r"^For-The-Household:\s*(.+)$", re.M)


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


def _sentences(text: str, n: int, cap: int) -> str:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])", text)
    out = ""
    for p in parts[:n]:
        cand = f"{out} {p}".strip()
        if len(cand) > cap and out:
            break
        out = cand
    if len(out) > cap:
        out = out[: cap - 1].rsplit(" ", 1)[0].rstrip(",;:- ") + "…"
    return out


def summarise(title: str, body: str, *, cap: int = 280) -> str:
    """One or two plain sentences about the PR: its ``For-The-Household:`` trailer, else the body's first paragraph,
    else the title's subject. Never invents anything that is not in the PR text."""
    body = body or ""
    hh = _HOUSEHOLD.search(body)
    if hh and hh.group(1).strip().lower() not in ("nothing visible", "none", "n/a"):
        return _sentences(_plain(hh.group(1)), 2, cap)
    text = re.sub(r"<!--.*?-->", "", body, flags=re.S)
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    para: list[str] = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            if para:
                break
            continue
        if s.startswith("#") or _TRAILER.match(s) or s.startswith("🤖") or s.startswith("|"):
            if para:
                break
            continue
        para.append(re.sub(r"^(?:[-*+]|\d+\.)\s+", "", s))
    out = _sentences(_plain(" ".join(para)), 2, cap) if para else ""
    if out:
        return out
    m = _CC.match((title or "").strip())
    subject = (m.group("subject") if m else (title or "")).strip()
    return (subject[:1].upper() + subject[1:]).rstrip(".") + "." if subject else ""


def _ref(item: dict) -> str:
    return f"#{item['number']} {item['title']}" if item.get("number") else item.get("title", "")


def _short_ref(item: dict) -> str:
    """``#N scope: subject`` for a digest line (the section already says fixed / new), at most 110 characters."""
    title = (item.get("title") or "").strip()
    m = _CC.match(title)
    if m:
        title = f"{m.group('scope')}: {m.group('subject')}" if m.group("scope") else m.group("subject")
    title = _clip(title, 110)
    return f"#{item['number']} {title}" if item.get("number") else title


def _short_summary(item: dict) -> str:
    return _sentences(item.get("summary") or "", 1, 160)


_INSTANT_LEAD = {"decision": "Needs your OK", "broke": "Something broke", "security": "Account notice",
                 "fixed": "Live now", "ability": "Live now", "household": "Live now", "under_the_hood": "Live now"}


def _instant_line(item: dict, detail: str) -> str:
    if item["kind"] == "stuck":
        return item["summary"]
    lead = "Reverted and live" if item["kind"] == "live" and item["category"] == "broke" else (
        "Raised, waiting on you" if item["kind"] == "raised" else _INSTANT_LEAD.get(item["category"], "Update"))
    if detail == "plain":
        return f"{lead}: {item['summary']}"
    return f"{lead}: {_ref(item)}\n{item['summary']}".rstrip()


POINTER_TEXT = "There's an update from Zoe for you. Ask me \"what's new?\" or look on the desktop."


@dataclass
class Message:
    text: str
    keys: list[str]
    url: Optional[str] = None
    label: Optional[str] = None


def render_instant(items: list[dict], detail: str, repo_name: str) -> Message:
    keys = [i["key"] for i in items]
    if detail == "pointer":
        return Message(POINTER_TEXT, keys)
    if len(items) == 1:
        it = items[0]
        label = "Deploy runs" if it["kind"] == "stuck" else f"Open #{it['number']}" if it.get("number") else "Open"
        return Message(_clip(_instant_line(it, detail)), keys, it.get("url"), label)
    lines = [f"{len(items)} updates from Zoe:"] + [f"• {_instant_line(i, detail).splitlines()[0]}" for i in items]
    return Message(_clip("\n".join(lines)), keys, f"https://github.com/{repo_name}/pulls", "Open on GitHub")


def _clip(text: str, limit: int = CHUNK_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


_OPENERS = ("Today on Zoe: {n}.", "Here's what changed in Zoe: {n}.", "Zoe's changes since the last update: {n}.")
_SECTIONS = (("ability", "New"), ("fixed", "Fixed"), ("broke", "Reverted or broken"), ("household", "Household"),
             ("decision", "Waiting on you"), ("security", "Account"), ("under_the_hood", "Under the hood"))


def render_digest(items: list[dict], uncertain: list[dict], prefs: dict, *, opener: int,
                  repo_name: str) -> list[Message]:
    """The digest as up to ``MAX_CHUNKS`` messages of at most ``CHUNK_CHARS``. Every key lands in exactly one."""
    cats = prefs["categories"]
    live = [i for i in items if i["category"] != "raised"]  # live changes, stuck deploys, decisions rolled over
    raised = [i for i in items if i["category"] == "raised"]
    lines: list[tuple[str, list[str]]] = []  # (text, keys this line accounts for)
    n_live = sum(1 for i in live if i["kind"] == "live")
    head = _OPENERS[opener % len(_OPENERS)].format(
        n=f"{n_live} change{'s' if n_live != 1 else ''} went live" if n_live else "nothing new went live")
    lines.append((head, []))
    # The short lines go first, so a truncated digest can never swallow them into "...and N more".
    drafts = sum(1 for i in raised if i.get("draft"))
    tail = f" ({drafts} draft{'s' if drafts != 1 else ''})" if drafts else ""
    if raised and prefs.get("raised") != "list":
        lines.append((f"Raised, not merged: {len(raised)} new{tail}.", [i["key"] for i in raised]))
    if uncertain:
        refs = ", ".join(f"#{i['number']}" if i.get("number") else i["key"] for i in uncertain)
        lines.append((f"This may not have reached you earlier: {refs}.", [i["key"] for i in uncertain]))
    pointer_only = all(cats[c]["detail"] == "pointer" for c in comm_prefs.CATEGORIES)
    if pointer_only:
        return [Message(f"Zoe has {len(items)} update{'s' if len(items) != 1 else ''} for you. Ask me \"what's new?\".",
                        [i["key"] for i in items + uncertain])]
    for cat, heading in _SECTIONS:
        group = [i for i in live if i["category"] == cat]
        if not group:
            continue
        detail = cats[cat]["detail"]
        lines.append((f"\n{heading} ({len(group)})", []))
        if cat == "under_the_hood" and detail != "technical":
            lines.append(("housekeeping only: tests, docs, tooling", [i["key"] for i in group]))
            continue
        for i in group:
            if i["kind"] == "stuck":
                lines.append((f"• {i['summary']}", [i["key"]]))
            elif cat == "under_the_hood":
                lines.append((f"• {_short_ref(i)}", [i["key"]]))
            elif detail == "plain":
                lines.append((f"• {_short_summary(i)}", [i["key"]]))
            else:
                ref, summ = _short_ref(i), _short_summary(i)
                same = summ.rstrip(".").lower() in ref.lower()  # the summary only repeats the title
                lines.append((f"• {ref}" if same else f"• {ref} - {summ}", [i["key"]]))
    if raised and prefs.get("raised") == "list":
        lines.append((f"\nRaised, not merged ({len(raised)}){tail}", []))
        for i in raised:
            lines.append((f"• {_short_ref(i)}{' (draft)' if i.get('draft') else ''}", [i["key"]]))
    return _pack(lines, f"https://github.com/{repo_name}/pulls?q=is%3Apr+is%3Amerged", "Merged PRs on GitHub")


def _pack(lines: list[tuple[str, list[str]]], url: str, label: str) -> list[Message]:
    chunks: list[Message] = []
    cur, keys = "", []
    for idx, (text, lkeys) in enumerate(lines):
        text = _clip(text, LINE_CHARS)
        last = len(chunks) == MAX_CHUNKS - 1
        if cur and len(cur) + 1 + len(text) > CHUNK_CHARS - (MORE_RESERVE if last else 0):
            if last:  # the last allowed message: everything left becomes one count line
                rest = [k for _, ks in lines[idx:] for k in ks]
                chunks.append(Message(f"{cur}\n…and {len(rest)} more on GitHub.", keys + rest, url, label))
                return chunks
            chunks.append(Message(cur, keys))
            cur, keys = text.lstrip("\n"), list(lkeys)
            continue
        cur = f"{cur}\n{text}" if cur else text
        keys.extend(lkeys)
    if cur:
        chunks.append(Message(cur, keys, url, label))
    return chunks


# ── recipients (OWNER ONLY in P1) ─────────────────────────────────────────────
@dataclass
class Recipient:
    user_id: str
    role: str
    telegram_id: str
    prefs: dict = field(default_factory=dict)


async def load_recipients(db=None) -> list[Recipient]:
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
    async with _ctx() as conn:
        cur = await conn.execute("SELECT id, role FROM users WHERE role IN ('admin', 'family-admin') ORDER BY id")
        rows = list(await cur.fetchall())
        for row in rows:
            uid, role = str(row["id"]), str(row["role"])
            prefs = await read_prefs(conn, uid)
            tid = prefs.get("telegram_id")
            doc = comm_prefs.normalise(prefs.get(comm_prefs.PREF_KEY), role)
            if doc is None or not (isinstance(tid, str) and tid.isdigit()):
                continue
            out.append(Recipient(uid, role, tid, doc))
    return out


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
                    "summary": summarise(pr.get("title", ""), pr.get("body", "")), "draft": bool(pr.get("isDraft"))}
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
            if silent or not sha:
                continue
            title = pr.get("title", "")
            st["pending_live"][str(n)] = {"number": n, "title": title, "sha": sha, "url": pr.get("url"),
                                          "merged_at": pr.get("mergedAt") or _iso(c.now),
                                          "summary": summarise(title, pr.get("body", "")),
                                          "category": category_for_title(title)}
            # A merge supersedes its "raised" notice in any digest not yet sent (design 5.4).
            for dkey, d in st["deliveries"].items():
                if (dkey.endswith((f"|pr:{n}:raised", f"|pr:{n}:decision")) and d["status"] == "queued"
                        and d["lane"] != "instant"):
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
                            "title": p["title"], "summary": p["summary"], "url": p.get("url"),
                            "category": p["category"]}, silent=silent)
        elif (live is False or not deployed) and c.now - merged_at > stale_after():
            stuck.append(p)  # definitely not in the deployed code (an unchecked or unknown one is not counted)
    if stuck and not silent:
        run = None
        try:
            run = await gh.latest_deploy_run()
        except Exception:  # noqa: BLE001
            run = None
        refs = ", ".join(f"#{p['number']}" for p in stuck[:12]) + (f" and {len(stuck) - 12} more" if len(stuck) > 12 else "")
        mins = int(stale_after().total_seconds() // 60)
        why = (f" The last deploy run ended: {run.get('conclusion') or run.get('status')}."
               if isinstance(run, dict) and (run.get("conclusion") or run.get("status")) else "")
        _new_notice(c, {"key": f"deploy:stuck:{deployed or 'none'}", "kind": "stuck", "category": "broke",
                        "title": "Merged but not live",
                        "summary": f"Merged but not live after {mins} min: {refs}. The deploy may have failed or "
                                   f"been held by the voice gate.{why}",
                        "url": (run or {}).get("url") or f"https://github.com/{c.repo}/actions/workflows/deploy.yml"},
                    silent=silent)


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
        elif outcome == "uncertain":
            d["status"] = "uncertain"
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
        same_kind_recent = last is not None and c.now - last < SAME_KIND_SPACING
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
    batches = [items] if len(items) >= BATCH_AT else [[i] for i in items]
    for batch in batches:
        outcome = await _send(c, r, render_instant(batch, detail, c.repo))
        if outcome in ("sent", "shadow", "uncertain"):
            st["instant_sent"].setdefault(r.user_id, []).append(local.isoformat())
            for i in batch:
                st["last_instant"][f"{r.user_id}|{i['category']}"] = _iso(c.now)


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
    msgs = render_digest(items, uncertain, r.prefs, opener=idx, repo_name=c.repo)
    st["opener"][r.user_id] = idx
    outcomes = [await _send(c, r, m) for m in msgs]
    if all(o != "retry" for o in outcomes):
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
