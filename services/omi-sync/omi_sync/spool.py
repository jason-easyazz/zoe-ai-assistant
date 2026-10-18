"""The local custody spool: every ring record we have RECEIVED, durable on disk, until Zoe confirms the recording.

Why a spool at all. The ring protocol hands records over a BLE link that can drop at
any instant, and stock firmware (no custody capability) frees records on the
pendant as the link layer acknowledges them. So the moment a record is received it
must be safe on THIS side, or a crash between "pendant freed it" and "we wrote it"
loses audio. ``add_records`` therefore commits (``synchronous=FULL``, WAL) before the
engine considers anything received. A record leaves the spool only through
``mark_confirmed`` - after Zoe answered that it stored the recording those records
belong to (the custody rule).

Idempotency is the primary key ``(device, ring, seq)``: a record that arrives twice
(a resumed READ, a retried drain) is ignored, never duplicated. ``confirmed`` keeps a
tombstone range per uploaded recording so a re-read of already-confirmed seqs (the
pendant re-sends from its read pointer when an ADVANCE was lost) does not
re-create a recording that Zoe already holds.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records (
    device      TEXT    NOT NULL,
    ring        TEXT    NOT NULL,
    seq         INTEGER NOT NULL,
    t_epoch     REAL    NOT NULL,
    tq          TEXT    NOT NULL,
    frames      INTEGER NOT NULL,
    codec       INTEGER NOT NULL,
    received_at REAL    NOT NULL,
    record      BLOB    NOT NULL,
    PRIMARY KEY (device, ring, seq)
);
CREATE TABLE IF NOT EXISTS confirmed (
    device       TEXT    NOT NULL,
    ring         TEXT    NOT NULL,
    first_seq    INTEGER NOT NULL,
    last_seq     INTEGER NOT NULL,
    rec_key      TEXT    NOT NULL,
    confirmed_at REAL    NOT NULL,
    PRIMARY KEY (device, ring, first_seq)
);
"""


@dataclass(frozen=True)
class SpoolRecord:
    seq: int
    t_epoch: float          # best wall-clock time of the record (host timeline)
    tq: str                 # 'device' | 'corrected' | 'estimated'
    frames: int             # Opus frames in the record
    codec: int
    received_at: float
    record: bytes           # the full 444-byte record, verbatim


class Spool:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.executescript(_SCHEMA)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # --- meta ---------------------------------------------------------------------
    def get_meta(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self._lock:
            row = self._db.execute("SELECT v FROM meta WHERE k = ?", (key,)).fetchone()
        return row[0] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._db.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (key, value))

    # --- records ------------------------------------------------------------------
    def _confirmed_ranges(self, device: str, ring: str) -> list[tuple[int, int]]:
        return [(r[0], r[1]) for r in self._db.execute(
            "SELECT first_seq, last_seq FROM confirmed WHERE device = ? AND ring = ?", (device, ring))]

    def add_records(self, device: str, ring: str, items: Iterable[SpoolRecord]) -> int:
        """Durably store records; return how many were NEW. Already-held and already-confirmed seqs are ignored."""
        new = 0
        with self._lock:
            ranges = self._confirmed_ranges(device, ring)
            self._db.execute("BEGIN IMMEDIATE")
            try:
                for it in items:
                    if any(a <= it.seq <= b for a, b in ranges):
                        continue
                    cur = self._db.execute(
                        "INSERT OR IGNORE INTO records (device, ring, seq, t_epoch, tq, frames, codec, received_at, record) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (device, ring, it.seq, it.t_epoch, it.tq, it.frames, it.codec, it.received_at, it.record))
                    new += cur.rowcount
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return new

    def records(self, device: str, ring: str) -> list[SpoolRecord]:
        with self._lock:
            rows = self._db.execute(
                "SELECT seq, t_epoch, tq, frames, codec, received_at, record FROM records "
                "WHERE device = ? AND ring = ? ORDER BY seq", (device, ring)).fetchall()
        return [SpoolRecord(r[0], r[1], r[2], r[3], r[4], r[5], bytes(r[6])) for r in rows]

    def high_seq(self, device: str, ring: str) -> Optional[int]:
        """The highest seq this side has ever held for the ring (spooled OR already confirmed), else None."""
        with self._lock:
            a = self._db.execute("SELECT MAX(seq) FROM records WHERE device = ? AND ring = ?", (device, ring)).fetchone()[0]
            b = self._db.execute("SELECT MAX(last_seq) FROM confirmed WHERE device = ? AND ring = ?", (device, ring)).fetchone()[0]
        vals = [v for v in (a, b) if v is not None]
        return max(vals) if vals else None

    def rings(self, device: str) -> list[str]:
        """Every ring incarnation that still holds unconfirmed records for the device."""
        with self._lock:
            return [r[0] for r in self._db.execute(
                "SELECT DISTINCT ring FROM records WHERE device = ? ORDER BY ring", (device,))]

    def coverage(self, device: str, ring: str) -> list[tuple[int, int]]:
        """Sorted inclusive ``(first, last)`` seq intervals this side holds OR has confirmed (merged where contiguous)."""
        with self._lock:
            seqs = [r[0] for r in self._db.execute(
                "SELECT seq FROM records WHERE device = ? AND ring = ? ORDER BY seq", (device, ring))]
            spans = self._confirmed_ranges(device, ring)
        spans += [(s, s) for s in seqs]
        merged: list[list[int]] = []
        for a, b in sorted(spans):
            if merged and a <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], b)
            else:
                merged.append([a, b])
        return [(a, b) for a, b in merged]

    def mark_confirmed(self, device: str, ring: str, first_seq: int, last_seq: int, rec_key: str, *, now: Optional[float] = None) -> int:
        """Zoe confirmed this recording: drop its records and remember the range. Atomic. Returns records removed."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                cur = self._db.execute(
                    "DELETE FROM records WHERE device = ? AND ring = ? AND seq BETWEEN ? AND ?", (device, ring, first_seq, last_seq))
                removed = cur.rowcount
                self._db.execute(
                    "INSERT OR REPLACE INTO confirmed (device, ring, first_seq, last_seq, rec_key, confirmed_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (device, ring, first_seq, last_seq, rec_key, now if now is not None else time.time()))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        return removed

    def prune_confirmed(self, older_than: float) -> int:
        with self._lock:
            return self._db.execute("DELETE FROM confirmed WHERE confirmed_at < ?", (older_than,)).rowcount

    def compact_if_sparse(self, *, min_free_bytes: int = 16 * 1024 * 1024, min_free_ratio: float = 0.5) -> int:
        """Give the disk back after a big drain (gap audit P2-2: the file was 252 MB with 99% of its pages free and never shrank, because
        SQLite only reuses free pages). ``VACUUM`` rewrites the file without its free pages; no row changes, and a crash mid-VACUUM leaves
        the original intact. Only runs when the free pages are both large and most of the file, so a busy spool is never churned.
        Returns the bytes released (0 = nothing to do). Call between visits, never during one."""
        with self._lock:
            pages = self._db.execute("PRAGMA page_count").fetchone()[0]
            free = self._db.execute("PRAGMA freelist_count").fetchone()[0]
            size = self._db.execute("PRAGMA page_size").fetchone()[0]
            if not pages or free * size < min_free_bytes or free / pages < min_free_ratio:
                return 0
            self._db.execute("VACUUM")
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        return free * size

    def stats(self) -> dict:
        with self._lock:
            n = self._db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            c = self._db.execute("SELECT COUNT(*) FROM confirmed").fetchone()[0]
        return {"spooled_records": n, "confirmed_ranges": c}
