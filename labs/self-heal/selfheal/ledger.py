"""The landing ledger: an append-only, hash-chained local record of what landed, what was rolled back
and what was refused, each with the reason (a closed token) and the PROOF HASH of the landing.

It is the sold unit's answer to "what did she change, and why": no GitHub, no PR page. One JSON object
per line; every entry carries the hash of the one before it, so an edited, reordered or removed entry
breaks the chain (`verify`). What the chain does NOT show: entries cut from the END (the file is in the
same trust domain as the loop; an off-box anchor of the head hash would close that, and is not built).
Ids, shas, file names, counts and reason tokens only: never failure text, model output or a diff.
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from pathlib import Path

EVENTS = ("landed", "rolled_back", "refused")
KEYS = {"ticket", "fault_key", "test", "engine", "shape", "ref", "from", "to", "tree", "heal", "merge", "files",
        "reason", "proof", "recovered", "restored"}
_TOKEN = re.compile(r"^[A-Za-z0-9_./:@+=,#\[\]-]{0,240}$")
GENESIS = "0" * 64


class LedgerBroken(Exception):
    pass


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def proof_hash(**parts) -> str:
    """One hash over everything the landing's verdict rested on (shas, the merged tree, every gate's
    name / status / detail before and after the merge, the node runner's own hash)."""
    return _digest(parts)


def _clean(v):
    if isinstance(v, bool) or v is None or isinstance(v, int):
        return v
    if isinstance(v, str):
        return v if _TOKEN.match(v) else "<redacted>"
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    return "<redacted>"


class Ledger:
    def __init__(self, path: str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def entries(self) -> list:
        """Every entry, chain-checked from the first line; LedgerBroken names the first bad line."""
        out, prev = [], GENESIS
        if not self.path.exists():
            return out
        for i, line in enumerate(self.path.read_text().splitlines(), 1):
            try:
                e = json.loads(line)
                body = {k: v for k, v in e.items() if k != "hash"}
                ok = e["prev"] == prev and e["seq"] == i and e["hash"] == _digest(body)
            except (ValueError, KeyError, TypeError):
                ok = False
            if not ok:
                raise LedgerBroken(f"line {i}")
            out.append(e)
            prev = e["hash"]
        return out

    def append(self, event: str, ts: str, **fields) -> dict:
        """Refuses an unknown event or key, and refuses to extend a chain that does not verify."""
        if event not in EVENTS or set(fields) - KEYS:
            raise ValueError(f"ledger: event {event!r} / keys {sorted(set(fields) - KEYS)} not allowed")
        with open(self.path, "a+") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                done = self.entries()
                e = {"seq": len(done) + 1, "ts": ts, "event": event, "prev": done[-1]["hash"] if done else GENESIS,
                     **{k: _clean(v) for k, v in fields.items()}}
                e["hash"] = _digest(e)
                fh.write(json.dumps(e, sort_keys=True) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
                return e
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)

    def last_for(self, ticket: str, to: str) -> dict | None:
        """The latest landed / rolled_back entry for this ticket's landing (makes recovery idempotent)."""
        hits = [e for e in self.entries() if e.get("ticket") == ticket and e.get("to") == to and e["event"] != "refused"]
        return hits[-1] if hits else None

    def verify(self) -> tuple[bool, int]:
        try:
            return True, len(self.entries())
        except LedgerBroken:
            return False, 0
