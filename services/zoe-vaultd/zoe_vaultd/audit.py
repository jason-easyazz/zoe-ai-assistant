"""Append-only, hash-chained audit log (credential-vault.md section 12, cell V9).

One JSON object per line. Each line carries `prev`, the SHA-256 of the previous line's bytes (the first line carries
64 zeros), so deleting, reordering or editing any line breaks `verify()`. The writer accepts only an allowlisted set of
metadata fields with short scalar values: there is no field a secret, a code, a cookie or a page body could go into.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path

GENESIS = "0" * 64

EVENTS = frozenset({
    "start", "labels", "propose", "approve", "deny", "capabilities", "login", "otp", "refuse", "lock", "unlock",
    "sync", "redeem", "peer_refused",
})

FIELDS = frozenset({
    "decision", "reason", "member", "errand", "step", "item", "label", "domain", "origin", "frame_origin", "proposal",
    "approver", "peer_uid", "verb", "duration_ms", "mode", "category", "count", "detail",
})

MAX_VALUE_LEN = 300


class AuditError(RuntimeError):
    pass


def _line_hash(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


def verify(path: str | Path) -> tuple[bool, int, str]:
    """(ok, lines checked, problem). An absent or empty log verifies as ok with 0 lines."""
    p = Path(path)
    if not p.exists():
        return True, 0, ""
    prev = GENESIS
    n = 0
    with p.open("rb") as fh:
        for raw in fh:
            n += 1
            if not raw.endswith(b"\n"):
                return False, n, "truncated line"
            body = raw[:-1]
            try:
                obj = json.loads(body)
            except ValueError:
                return False, n, "not json"
            if not isinstance(obj, dict) or obj.get("prev") != prev:
                return False, n, "chain broken"
            prev = _line_hash(body)
    return True, n, ""


class AuditLog:
    def __init__(self, path: str | Path, *, clock=time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        ok, n, problem = verify(self.path)
        if not ok:
            # Fail closed: a broker whose audit trail was tampered with does not start.
            raise AuditError(f"audit chain broken at line {n}: {problem}")
        self._prev = GENESIS
        if n:
            last = self.path.read_bytes().rstrip(b"\n").rsplit(b"\n", 1)[-1]
            self._prev = _line_hash(last)
        self._fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)

    def write(self, event: str, **fields) -> None:
        if event not in EVENTS:
            raise AuditError(f"unknown audit event {event!r}")
        record: dict = {}
        for key, value in fields.items():
            if key not in FIELDS:
                raise AuditError(f"audit field {key!r} is not allowlisted")
            if value is None:
                continue
            if isinstance(value, bool) or isinstance(value, int):
                record[key] = value
            elif isinstance(value, float):
                record[key] = round(value, 3)
            elif isinstance(value, str):
                record[key] = value[:MAX_VALUE_LEN]
            else:
                raise AuditError(f"audit field {key!r} must be a scalar")
        with self._lock:
            record["ts"] = round(self._clock(), 3)
            record["event"] = event
            record["prev"] = self._prev
            body = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            os.write(self._fd, body + b"\n")
            os.fsync(self._fd)
            self._prev = _line_hash(body)

    def close(self) -> None:
        try:
            os.close(self._fd)
        except OSError:
            pass
