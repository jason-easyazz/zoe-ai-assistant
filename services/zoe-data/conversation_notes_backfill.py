"""Owner BACKFILL of conversation notes for a named guest from the panel turns the Flue brain kept (``conversation_notes.py``).

THE CASE. A friend talked to Zoe on the panel BEFORE she was enrolled. zoe-data discarded those turns (``GUEST_MEMORY_DISCARD``; a
guest sentinel's chat is never saved to ``chat_messages``), but the Flue brain sidecar kept its own transcript of the session
(``labs/flue-zoe-brain-2x/data/zoe-brain.db``, the append-only ``flue_conversation_stream_batches`` event log). This reads that file
READ-ONLY, picks out the turns of a time window that came from an UNIDENTIFIED speaker (the ``zoe-uid:`` envelope line is a guest
sentinel or absent; a member's turn in the same window is counted and left alone; a harness replay is skipped), strips the context
blocks zoe-data folded into each message, and hands the conversation to the SAME note pass the nightly run uses
(``conversation_notes.notes_for_convos``), stored in the guest's own layer (``people_id`` = their enrolled person).

Nothing is copied out of the Flue file; only the note is stored, and only while ``ZOE_CONVERSATION_NOTES`` is shadow / on (shadow notes
are promoted to servable by the first ``on`` night). While the flag is off the tool refuses to do anything, a dry run included. A forgotten name, a distress hand-off and an off-the-record cue
keep a turn out, exactly as for the nightly pass. ``dry_run`` makes the model call and prints the notes it WOULD write; it stores nothing.
Runbook: ``docs/knowledge/conversation-notes.md`` "Backfill a guest's earlier chat". CLI: ``scripts/maintenance/conversation_notes_backfill.py``.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: pinned equal to ``zoe_flue_client._FLUE_CONTEXT_BLOCKS`` by ``test_conversation_notes.py``
CONTEXT_BLOCKS = (
    ("[MEMORY CONTEXT", "[END MEMORY CONTEXT]"),
    ("[PENDING CONTACT OFFER", "[END PENDING CONTACT OFFER]"),
    ("[Today", "[END Today]"),
    ("[RAISE", "[END RAISE]"),
)
_ENVELOPE_RE = re.compile(r"^\s*zoe-([a-z]+):(.*)$")
_HOP_PREFIX = "(you know this about me: "


@dataclass
class Exchange:
    submission_id: str
    session: str
    epoch: float
    uid: str              # the envelope's ``zoe-uid:`` value ("" = none)
    replay: bool
    user: str
    reply: str


def unwrap(text: str) -> tuple[str, str, bool]:
    """A stored user message -> ``(words, uid, is_replay)``: the envelope lines (`` zoe-<name>:...``) read, the context blocks and the
    one-line hop note dropped, what is left joined. Pure."""
    uid, replay, rest = "", False, []
    for line in str(text or "").split("\n"):
        m = _ENVELOPE_RE.match(line)
        if m and not rest:
            if m.group(1) == "uid":
                uid = m.group(2).strip().strip('"')
            elif m.group(1) == "replay":
                replay = True
            continue
        rest.append(line)
    out, closing = [], ""
    for line in rest:
        t = line.rstrip()
        if closing:
            if t == closing:
                closing = ""
            continue
        hit = next((c for o, c in CONTEXT_BLOCKS if t.endswith("]") and (t == o + "]" or t.startswith(o + " "))), "")
        if hit:
            closing = hit
            continue
        if t.startswith(_HOP_PREFIX) and t.endswith(")"):
            continue
        out.append(line)
    return " ".join(" ".join(out).split()), uid, replay


def _utc_prefixes(since: float, until: float) -> list:
    """Hour prefixes (``2026-11-07T18``) covering the window, so the scan parses only batches that mention them."""
    out, t = [], datetime.fromtimestamp(since, tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
    end = datetime.fromtimestamp(until, tz=timezone.utc)
    while t <= end:
        out.append(t.strftime("%Y-%m-%dT%H"))
        t += timedelta(hours=1)
    return out


def read_exchanges(db_path: str, since: float, until: float) -> list:
    """Every exchange of ``[since, until]`` in the Flue event log, oldest first. READ-ONLY (``mode=ro``). Raises on a missing file."""
    from memory_episodes import _epoch

    prefixes = _utc_prefixes(since, until + 3600)      # an hour of margin: a reply streams after the question
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    users: dict = {}
    replies: dict = {}
    try:
        for path, data in conn.execute("SELECT path, data FROM flue_conversation_stream_batches ORDER BY rowid"):
            if not any(p in data for p in prefixes):
                continue
            try:
                events = json.loads(data)
            except ValueError:
                continue
            for e in events if isinstance(events, list) else []:
                kind, sub = e.get("type"), str(e.get("submissionId") or "")
                if not sub:
                    continue
                if kind == "user_message":
                    at = _epoch(e.get("timestamp"))
                    if at is None or not (since <= at <= until):
                        continue
                    content = e.get("content")
                    text = content if isinstance(content, str) else "".join(
                        str(b.get("text") or "") for b in (content or []) if isinstance(b, dict))
                    users[sub] = (str(path), at, text)
                elif kind == "assistant_text_delta" and sub in users:
                    replies[sub] = replies.get(sub, "") + str(e.get("delta") or "")
    finally:
        conn.close()
    out = []
    for sub, (path, at, text) in users.items():
        words, uid, replay = unwrap(text)
        out.append(Exchange(sub, path.rsplit("/", 1)[-1], at, uid, replay, words, " ".join(replies.get(sub, "").split())))
    out.sort(key=lambda x: x.epoch)
    return out


def guest_exchanges(all_exchanges: list) -> tuple[list, dict]:
    """The unidentified speaker's exchanges and a count of what was left out (members' turns, harness replays, empty)."""
    from user_filters import is_unidentified

    kept, skipped = [], {"member": 0, "replay": 0, "empty": 0}
    for x in all_exchanges:
        if x.replay:
            skipped["replay"] += 1
        elif not x.user:
            skipped["empty"] += 1
        elif x.uid and not is_unidentified(x.uid):
            skipped["member"] += 1
        else:
            kept.append(x)
    return kept, skipped


async def run(owner: str, name: str, db_path: str, since: float, until: float, *, dry_run: bool, now: Optional[float] = None) -> dict:
    """Notes for the enrolled guest ``name`` (in ``owner``'s layer) from the Flue turns of the window. Returns a JSON-able report."""
    import conversation_notes as cn
    import distress_handoff
    import memory_forgotten as mf
    import named_guest_memory as ngm

    m = cn.mode()
    if m == "off":
        return {"status": "refused", "error": ("ZOE_CONVERSATION_NOTES is off: the feature is dark, so this reads and makes nothing. Set it to "
                                               "shadow (stored but never served - zero risk) or on in this process's environment "
                                               "(zoe-data's .env) and run again")}
    guests = [g for g in await ngm.list_for(owner) if g["name"].strip().lower() == name.strip().lower()]
    if len(guests) != 1:
        return {"status": "refused", "error": ("no enrolled guest with that name in this member's layer (enrol them first: "
                                               "\"learn <name>'s voice\" at the panel, or named_guest_backfill.py)") if not guests
                else "more than one guest has that name"}
    guest = guests[0]
    exchanges = read_exchanges(db_path, since, until)
    mine, skipped = guest_exchanges(exchanges)
    mine = [x for x in mine if not distress_handoff.guarded_text(f"{x.user} {x.reply}")]
    mine, dropped_forgotten = await mf.keep_unforgotten(owner, mine, text_of=lambda x: f"{x.user} {x.reply}", strict=True)
    rows = [(x.submission_id, x.epoch, x.user, x.reply) for x in mine]
    guest_ref = cn._GuestRef(guest["guest_id"], guest["name"], owner)
    convos = cn._convos(owner, guest_ref.person_id, rows, with_name=guest_ref.name, minor=await cn._guest_minor(guest_ref), now=now,
                        settle=False)
    out = await cn.notes_for_convos(owner, guest_ref.person_id, convos, source="backfill", status="active" if m == "on" else "shadow",
                                     dry_run=dry_run)
    return {"status": "ok", "dry_run": dry_run, "guest": guest["name"], "exchanges_in_window": len(exchanges), "guest_exchanges": len(rows),
            "skipped": {**skipped, "forgotten": dropped_forgotten}, "conversations": out["convos"], "already_noted": out["known"],
            "too_short": out["too_short"], "no_note": out["no_note"], "stored": out["stored"], "notes": out["notes"]}


def flue_db_path() -> str:
    """Where the Flue brain's event log lives (``ZOE_FLUE_BRAIN_DB`` overrides the repo default)."""
    import os
    import pathlib

    return os.environ.get("ZOE_FLUE_BRAIN_DB") or str(
        pathlib.Path(__file__).resolve().parents[2] / "labs" / "flue-zoe-brain-2x" / "data" / "zoe-brain.db")


def source_text(ids: list, started: str, ended: str) -> Optional[str]:
    """The words of a backfilled note's source exchanges (Flue ``submissionId`` = the note's ``turn_ids``), read back READ-ONLY from the
    event log so a forget can reach a paraphrasing note. None = they cannot ALL be found (file gone / rotated): the caller must treat the
    note as unprovable and remove it (forgotten means forever). Never raises."""
    from memory_episodes import _epoch

    want = {str(i) for i in ids if i}
    if not want:
        return ""
    try:
        s, e = _epoch(started), _epoch(ended)
        if s is None or e is None:
            return None
        found = {x.submission_id: f"{x.user} {x.reply}" for x in read_exchanges(flue_db_path(), s - 60, e + 60)
                 if x.submission_id in want}
    except Exception as exc:  # noqa: BLE001
        logger.warning("CONVERSATION_NOTES backfill source unreadable (%s)", type(exc).__name__)
        return None
    return " ".join(found.values()) if want <= set(found) else None
