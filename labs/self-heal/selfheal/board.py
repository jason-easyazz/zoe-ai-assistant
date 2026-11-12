"""Optional, best-effort mirror of heal tickets onto the Multica board, so the owner sees self-heal
work where engineering work already lives. The loop never depends on it: board `off` (the sold-Zoe
default) or an unconfigured / unreachable Multica just means no mirror.

One issue per FAULT (keyed by fault_key), created in `backlog` with NO assignee: never `todo`,
because the board runner claims `todo` issues and would execute the same fault a second time.
State changes are appended as notes; healed -> `done`, parked -> `blocked` (needs a human).
This never touches Multica DISPATCH or its kill switch.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

STATUS = {"pr_open": "in_review", "healed": "done", "rolled_back": "blocked", "parked": "blocked"}


class BoardMirror:
    def __init__(self, client, audit):
        self.client, self.audit = client, audit

    def _call(self, coro_fn, *a, **k):
        try:
            return asyncio.run(coro_fn(*a, **k)) or {}
        except Exception as e:  # best effort: a board outage never stops a heal
            self.audit("board", "error", why=f"{type(e).__name__}: {e}"[:200])
            return {}

    def on_ticket(self, state, ticket) -> None:
        board = state.data.setdefault("board", {})
        if ticket.fault_key in board:
            self.note(state, ticket, f"new heal ticket {ticket.id} for this fault")
            return
        desc = (f"Opened by Zoe's self-heal loop. Fault class: {ticket.fault_class}.\n"
                f"Reproduction: `{' '.join(ticket.repro_cmd)}`\nFault key: {ticket.fault_key}\n"
                f"Ticket: {ticket.id}")
        issue = self._call(self.client.create_issue, f"[self-heal] {ticket.summary}"[:120], desc,
                           priority="medium", status="backlog")
        if issue.get("id"):
            board[ticket.fault_key] = str(issue["id"])
            state.save()
            self.audit("board", "created", ticket.id, issue=str(issue["id"]))

    def note(self, state, ticket, text: str, status_key: str | None = None) -> None:
        issue_id = state.data.get("board", {}).get(ticket.fault_key)
        if not issue_id:
            return
        self._call(self.client.append_issue_note, issue_id, f"[{ticket.id}] {text}")
        if status_key in STATUS:
            self._call(self.client.update_issue, issue_id, status=STATUS[status_key])


def make_board(cfg, audit):
    """`off` -> None. `multica` -> the existing zoe-data client, or None when it is not configured."""
    if cfg.board != "multica":
        return None
    zoe_data = Path(__file__).resolve().parents[3] / "services" / "zoe-data"
    if str(zoe_data) not in sys.path:
        sys.path.append(str(zoe_data))
    try:
        from multica_client import MULClient
    except Exception as e:
        audit("board", "absent", why=f"client import failed: {type(e).__name__}")
        return None
    client = MULClient()
    if not client.is_configured():
        audit("board", "absent", why="MULTICA_BASE_URL / token / workspace not set")
        return None
    return BoardMirror(client, audit)
