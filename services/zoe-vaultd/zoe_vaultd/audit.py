"""Append-only, hash-chained audit log (credential-vault.md section 12, cell V9).

One JSON object per line. Each line carries `prev`, the SHA-256 of the previous line's bytes (the first line carries
64 zeros), so deleting, reordering or editing any line breaks `verify()`. The writer accepts only an allowlisted set of
metadata fields with short scalar values: there is no field a secret, a code, a cookie or a page body could go into.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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
# Free-text fields are reason codes and short diagnostics, never data: only these characters may appear in them.
SAFE_TEXT = re.compile(r"^[A-Za-z0-9 ._:/-]*$")
SAFE_TEXT_FIELDS = frozenset({"reason", "detail", "decision", "mode", "verb"})


class AuditError(RuntimeError):
    pass


def _line_hash(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


def verify(path: str | Path) -> tuple[bool, int, str]:
    """(ok, lines checked, problem). An absent or empty log verifies as ok with 0 lines."""
    ok, n, problem, _ = _walk(path)
    return ok, n, problem


def _walk(path: str | Path) -> tuple[bool, int, str, str]:
    """verify() plus the hash of the last line (the chain head), in ONE read."""
    p = Path(path)
    if not p.exists():
        return True, 0, "", GENESIS
    prev = GENESIS
    n = 0
    with p.open("rb") as fh:
        for raw in fh:
            n += 1
            if not raw.endswith(b"\n"):
                return False, n, "truncated line", ""
            body = raw[:-1]
            try:
                obj = json.loads(body)
            except ValueError:
                return False, n, "not json", ""
            if not isinstance(obj, dict) or obj.get("prev") != prev:
                return False, n, "chain broken", ""
            prev = _line_hash(body)
    return True, n, "", prev


class AuditLog:
    def __init__(self, path: str | Path, *, clock=time.time) -> None:
        self.path = Path(path)
        self._clock = clock
        self._lock = threading.Lock()
        # Open first, then read the chain head once: the broker is the only writer of this file.
        self._fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        ok, n, problem, head = _walk(self.path)
        if not ok:
            os.close(self._fd)
            # Fail closed: a broker whose audit trail was tampered with does not start.
            raise AuditError(f"audit chain broken at line {n}: {problem}")
        self._prev = head

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
                if key in SAFE_TEXT_FIELDS and not SAFE_TEXT.match(value):
                    raise AuditError(f"audit field {key!r} carries characters outside the reason-code set")
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
