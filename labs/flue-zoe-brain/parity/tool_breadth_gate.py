#!/usr/bin/env python3
"""TOOL-BREADTH gate for the live flue brain — end-to-end, DB-verified (LAB-ONLY).

The existing gates (parity_check.py / hard_gate.py / tool_reliability.py) only
exercise ~6 of the brain's ~19 registered Zoe tools (get_time, recall_memory,
shopping_list_add / list writes, calendar add, get_weather). The other tools —
reminders, timers, journal, notes, people — go END-TO-END untested: nothing
proves a natural-language turn fires the tool AND that the effect actually
lands. This gate closes that gap.

Same discipline as hard_gate.py:
  * runs through the LIVE prod path (zoe-data /api/chat on :8000), which this
    deployment routes through the Flue brain (ZOE_BRAIN_BACKEND=flue, live
    2026-07-03) — so it exercises the real tool-selection + fulfilment seam;
  * authed test user via provision_parity_test_user.py (X-Session-ID = pgu.sid);
  * PER-RUN nonce'd session ids (MANDATORY — a fresh session per turn so recall
    contamination and sticky tool-disclosure from a prior run can't leak in);
  * SAID-vs-DID: every write is verified against GROUND TRUTH (Postgres for
    writes, chat-read reflection for read tools), NEVER reply-trust. A reply
    that CLAIMS success while the DB shows nothing = FAIL (the honesty check).

Read tools (list_reminders, note_search, people_search) are verified by:
  1. writing a nonce'd item via its write tool, verified in Postgres;
  2. a natural-language READ turn that must surface that nonce in the reply.
So the read tool's said-vs-did is anchored to a DB fact, not to itself.

Tools with no DB side-effect (weather) are verified by response sanity only.

Cleanup: every nonce'd test row this gate writes is soft-deleted from Postgres
at the end (best-effort). Nonce markers make the rows unambiguously ours.

Run ON the Zoe host, quiet window (no merge-train deploy restart mid-run):
    python3 labs/flue-zoe-brain/parity/tool_breadth_gate.py           # full run
    python3 labs/flue-zoe-brain/parity/tool_breadth_gate.py --dry     # 2-tool smoke
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import urllib.request
import uuid
from collections import Counter
from pathlib import Path

SCRATCH = Path(__file__).parent
BASE = "http://127.0.0.1:8000"
ZOE_DATA_DIR = "/home/zoe/assistant/services/zoe-data"

# The authed parity-gate-user session (minted via provision_parity_test_user.py
# + /api/auth/login). Same file hard_gate.py reads.
SID_FILE = SCRATCH / "pgu.sid"
if not SID_FILE.is_file():
    # Fall back to the scratchpad copy hard_gate.py uses on this host.
    alt = Path(
        "/tmp/claude-1000/-home-zoe-assistant--claude-worktrees-cool-volhard-4d17b3"
        "/c0a01881-9dc6-4c60-8327-dc9c28eb23e4/scratchpad/pgu.sid"
    )
    if alt.is_file():
        SID_FILE = alt
SID = SID_FILE.read_text().strip()

# Per-run nonce: unique-ish marker embedded in every test item so DB ground
# truth (and read-reflection) is unambiguously ours, never a pre-existing row.
MARK = uuid.uuid4().hex[:8]
RUN = str(int(time.time()))[-6:]


def _sid_for(name: str) -> str:
    """PER-RUN nonce'd, PER-TURN unique session id (mandatory)."""
    return f"tbg-{name}-{RUN}"


def call(path, payload=None, headers=None, method=None, timeout=120):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", **(headers or {})})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = json.loads(r.read().decode() or "{}")
    return body, (time.time() - t0) * 1000


def chat(msg, session):
    h = {"X-Session-ID": SID}
    try:
        body, ms = call("/api/chat/?stream=false",
                        {"message": msg, "session_id": session, "stream": False}, h)
        return (body.get("response") or body.get("error") or "(no response)"), ms
    except Exception as e:  # noqa: BLE001
        return f"(ERROR: {e})", 0


# ── Postgres ground truth ────────────────────────────────────────────────────
# Mirrors hard_gate.py's db_item_state helper: connect straight to the live
# Postgres and read the write's real state, unfiltered by user (the nonce makes
# each row unambiguously this run's). Each helper returns (present, row_repr).
def _pg():
    sys.path.insert(0, ZOE_DATA_DIR)
    from runtime_env import bootstrap_runtime_env
    bootstrap_runtime_env()
    import asyncpg
    return asyncpg, os.environ["POSTGRES_URL"]


def db_query(sql: str, param: str):
    """Run one parameterised read against live Postgres; return list[dict]."""
    asyncpg, url = _pg()

    async def q():
        conn = await asyncpg.connect(url)
        try:
            rows = await conn.fetch(sql, param)
        finally:
            await conn.close()
        return [dict(r) for r in rows]

    return asyncio.run(q())


def db_present(table: str, col: str, needle: str, active: bool = True):
    """(present, repr) — an undeleted row in `table` whose `col` ILIKEs needle."""
    rows = db_query(
        f"SELECT {col}, deleted FROM {table} WHERE {col} ILIKE $1", f"%{needle}%")
    live = [r for r in rows if not (active and r.get("deleted"))]
    return bool(live), str(rows)[:180]


def db_soft_delete(table: str, col: str, needle: str) -> int:
    """Best-effort cleanup: soft-delete nonce'd test rows. Returns row count."""
    asyncpg, url = _pg()

    async def q():
        conn = await asyncpg.connect(url)
        try:
            # `deleted` is an integer flag (0/1) in these tables, not boolean.
            res = await conn.execute(
                f"UPDATE {table} SET deleted = 1 WHERE {col} ILIKE $1", f"%{needle}%")
        finally:
            await conn.close()
        # asyncpg returns e.g. "UPDATE 3"
        try:
            return int(res.split()[-1])
        except Exception:  # noqa: BLE001
            return 0

    return asyncio.run(q())


# ── verdict recording ────────────────────────────────────────────────────────
ROWS = []


def row(cat, query, reply, ms, verdict, why):
    ROWS.append({"category": cat, "query": query, "reply": str(reply)[:200],
                 "ms": round(ms), "verdict": verdict, "why": why})
    print(f"[{verdict:5s}] {cat:16s} {str(query)[:46]!r} :: {why[:64]}")


def _claims_success(reply: str) -> bool:
    """Heuristic: did the reply CLAIM the write happened? Used to distinguish a
    said-but-not-done FAIL (claimed + absent) from an honest can't-do."""
    rl = reply.lower()
    honest = ["can't", "cannot", "couldn't", "won't say", "not started",
              "write disabled", "i can't do that", "unable"]
    if any(h in rl for h in honest):
        return False
    claims = ["added", "saved", "created", "set", "noted", "done", "got it",
              "i'll remind", "reminder", "on your", "to your"]
    return any(c in rl for c in claims)


def verify_write(cat, turn, table, col, needle, session):
    """WRITE said-vs-did: fire the tool via NL, then check Postgres ground truth.
    reply-claims-success + DB-absent = FAIL (the honesty check)."""
    reply, ms = chat(turn, session)
    if reply.startswith("(ERROR"):
        row(cat, turn, reply, ms, "ERROR", "transport"); return reply
    time.sleep(3)  # let the direct DB executor land the row
    present, dbrepr = db_present(table, col, needle)
    claimed = _claims_success(reply)
    if present:
        row(cat, turn, reply, ms, "PASS", f"DB-verified in {table}: {dbrepr}")
    elif claimed:
        row(cat, turn, reply, ms, "FAIL",
            f"SAID-vs-DID: reply claims success but {table} has NO row: {dbrepr}")
    else:
        row(cat, turn, reply, ms, "FAIL",
            f"tool did not fire (no claim, {table} empty): {dbrepr}")
    return reply


def verify_read(cat, turn, needle, session, require_db=None):
    """READ said-vs-did: the reply must SURFACE `needle` (a fact already proven
    in the DB by a prior write). Missing it = the read tool didn't really read.

    `require_db=(table, col)` re-checks Postgres first: if the prior write never
    landed, the read CANNOT legitimately pass — a name echoed back inside a
    "no contacts found" line is not a read (guards against a false PASS when the
    reply just parrots the query term)."""
    if require_db is not None:
        table, col = require_db
        present, _ = db_present(table, col, needle)
        if not present:
            reply, ms = chat(turn, session)
            row(cat, turn, reply, ms, "FAIL",
                f"read unverifiable: prior write never landed in {table} "
                f"(reply echoing {needle!r} is not proof of a read)")
            return reply
    reply, ms = chat(turn, session)
    if reply.startswith("(ERROR"):
        row(cat, turn, reply, ms, "ERROR", "transport"); return reply
    rl = reply.lower()
    not_found = ["no contacts found", "couldn't find", "could not find",
                 "don't have any", "no notes", "nothing"]
    if needle.lower() in rl and not any(nf in rl for nf in not_found):
        row(cat, turn, reply, ms, "PASS", f"read surfaced {needle!r}")
    else:
        row(cat, turn, reply, ms, "FAIL",
            f"read did NOT surface DB fact {needle!r} (reply hid a real row)")
    return reply


def verify_sanity(cat, turn, must, session):
    """No-DB-side-effect tool (weather): verify the reply is a plausible answer
    (contains one of `must`) and did not fabricate/stall."""
    reply, ms = chat(turn, session)
    if reply.startswith("(ERROR"):
        row(cat, turn, reply, ms, "ERROR", "transport"); return reply
    rl = reply.lower()
    if any(m.lower() in rl for m in must):
        row(cat, turn, reply, ms, "PASS", "response sane")
    else:
        row(cat, turn, reply, ms, "FAIL", f"missing any of {must}")
    return reply


# ── the gate ─────────────────────────────────────────────────────────────────
CLEANUP = []  # (table, col, needle) tuples to soft-delete at the end


def gate_reminders():
    title = f"water the plants {MARK}"
    verify_write("reminders", f"Remind me to {title} at 7pm tomorrow",
                 "reminders", "title", title, _sid_for("rem-add"))
    CLEANUP.append(("reminders", "title", title))
    # read tool: list_reminders must surface the reminder we just proved in DB.
    verify_read("reminders", "What reminders do I have?", MARK, _sid_for("rem-list"),
                require_db=("reminders", "title"))


def gate_timers():
    # set_timer FAILS CLOSED by design (the backend can't confirm a real
    # countdown scheduled — see zoe-tools.ts). The honest outcome is that the
    # brain must NOT claim a timer started. There is no DB side-effect. We check
    # the reply is either a genuine confirmation OR an honest can't-do — and
    # never a fabricated "timer started" that the backend can't back.
    s = _sid_for("timer")
    reply, ms = chat("Set a 5 minute timer for the pasta", s)
    rl = reply.lower()
    if reply.startswith("(ERROR"):
        row("timers", "set a 5 min timer", reply, ms, "ERROR", "transport"); return
    fabricated = ("starting a" in rl and "timer" in rl) and \
                 not any(h in rl for h in ["can't", "cannot", "won't", "panel", "reminder"])
    if fabricated:
        row("timers", "set a 5 min timer", reply, ms, "FAIL",
            "SAID-vs-DID: claimed a timer started that the backend can't schedule")
    else:
        row("timers", "set a 5 min timer", reply, ms, "PASS",
            "no fabricated timer (fail-closed honesty respected)")


def gate_journal():
    entry = f"grateful for the rain today {MARK}"
    verify_write("journal", f"Add a journal entry: {entry}",
                 "journal_entries", "content", entry, _sid_for("jrnl"))
    CLEANUP.append(("journal_entries", "content", entry))


def gate_notes():
    body = f"wifi password is hunter2-{MARK}"
    verify_write("notes", f"Make a note: {body}",
                 "notes", "content", body, _sid_for("note-add"))
    CLEANUP.append(("notes", "content", body))
    # note_search read tool must surface the note we just proved in DB.
    verify_read("notes", "Search my notes for the wifi password", MARK,
                _sid_for("note-search"), require_db=("notes", "content"))


def gate_people():
    name = f"Zephyrina{MARK}"
    verify_write("people", f"Add {name} to my contacts as my colleague",
                 "people", "name", name, _sid_for("ppl-add"))
    CLEANUP.append(("people", "name", name))
    # people_search read tool must surface the contact we just proved in DB.
    # require_db guards against a false PASS: if the create never landed, the
    # name echoed back in a "no contacts found" line is not a real read.
    verify_read("people", f"Who is {name}?", name, _sid_for("ppl-search"),
                require_db=("people", "name"))


def gate_weather():
    # Named-location weather (get_weather) — no DB side-effect; verify sanity.
    verify_sanity("weather", "What's the weather like in Perth?",
                  ["perth", "degree", "°", "rain", "sun", "cloud", "wind",
                   "warm", "cool", "cold", "hot", "clear", "temperature",
                   "forecast", "right now", "weather"],
                  _sid_for("weather"))


ALL_GATES = [
    ("reminders", gate_reminders),
    ("timers", gate_timers),
    ("journal", gate_journal),
    ("notes", gate_notes),
    ("people", gate_people),
    ("weather", gate_weather),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true",
                    help="2-tool smoke (reminders + weather) — for a merge-train window")
    args = ap.parse_args()

    gates = ALL_GATES[:1] + ALL_GATES[-1:] if args.dry else ALL_GATES
    mode = "DRY SMOKE (2 tools)" if args.dry else "FULL"
    print(f"TOOL-BREADTH gate [{mode}] start {time.strftime('%H:%M:%S')} "
          f"mark={MARK} run={RUN} sid={SID[:8]}…")

    for _, fn in gates:
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            row(fn.__name__, "(gate)", str(e), 0, "ERROR", f"gate raised: {e}")

    # cleanup — best-effort soft-delete of everything we wrote. Always also
    # sweep list_items by the run MARK: a misrouted write (a FAIL where the
    # brain sent journal/people to list_add) leaks the nonce'd content into the
    # shopping list, so clean that up too regardless of which gates fired.
    print("\ncleanup:")
    for table, col, needle in CLEANUP + [("list_items", "text", MARK)]:
        try:
            n = db_soft_delete(table, col, needle)
            print(f"  soft-deleted {n} row(s) from {table} matching {needle!r}")
        except Exception as e:  # noqa: BLE001
            print(f"  cleanup FAILED for {table}/{needle!r}: {e}")

    out = SCRATCH / "tool_breadth_gate_results.json"
    out.write_text(json.dumps(ROWS, indent=1))
    c = Counter(r["verdict"] for r in ROWS)
    print(f"\nTOOL-BREADTH gate done: {dict(c)} → {out}")
    fails = [r for r in ROWS if r["verdict"] in ("FAIL", "ERROR")]
    if fails:
        print("\nFAILs / ERRORs (each becomes a fix ticket):")
        for r in fails:
            print(f"  [{r['verdict']}] {r['category']}: {r['query'][:50]!r} — {r['why']}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
