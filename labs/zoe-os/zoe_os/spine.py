"""The event spine: one durable, ordered, capability-filtered bus.

* durable append to SQLite; the sequence number is the row id, never reused;
* default deny: with no authorizer nothing may be published, subscribed to or read;
* a subscriber has a bounded queue. A publisher NEVER waits for a slow subscriber: when a
  queue is full the subscriber is marked lagging and catches up from the durable log, in
  order and without loss, the next time it polls (backpressure by lag-to-replay);
* every delivery is re-authorized at the moment it is handed over, so a revoked grant
  stops events already sitting in a queue;
* a refusal is itself an event (kind REFUSAL, kernel visibility) and carries no content.

Trust boundary: this is an in-process prototype. Python cannot stop a skill in the same
process from calling a private method; the real boundary is a process and a socket.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections import deque
from typing import Callable, Iterable, Iterator, NamedTuple, Protocol

from .events import KERNEL_ONLY, Event, Kind, Visibility

KERNEL = "kernel"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL,
  subject TEXT, visibility TEXT NOT NULL,
  payload TEXT, mentions TEXT, caused_by TEXT,
  tombstoned INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS people (seq INTEGER NOT NULL, person TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS people_person ON people(person, seq);
CREATE TABLE IF NOT EXISTS causes (seq INTEGER NOT NULL, cause INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS causes_cause ON causes(cause);
CREATE TABLE IF NOT EXISTS cursors (reader TEXT PRIMARY KEY, seq INTEGER NOT NULL);
"""


class Decision(NamedTuple):
    allowed: bool
    reason: str = ""
    grant_id: str | None = None


class Authorizer(Protocol):
    """The enforcement point the spine asks. The capability model implements it."""

    def is_origin(self, source: str) -> bool: ...
    def may_publish(self, source: str, event: Event) -> Decision: ...
    def may_subscribe(self, reader: str, kinds: frozenset[Kind] | None, subject: str | None) -> Decision: ...
    def may_read(self, reader: str, event: Event) -> Decision: ...


class DenyAll:
    """The default authorizer: nothing is allowed."""

    def is_origin(self, source: str) -> bool:
        return False

    def may_publish(self, source: str, event: Event) -> Decision:
        return Decision(False, "default deny")

    def may_subscribe(self, reader, kinds, subject) -> Decision:
        return Decision(False, "default deny")

    def may_read(self, reader: str, event: Event) -> Decision:
        return Decision(False, "default deny")


class Refused(Exception):
    """Raised to the caller; the same refusal is also appended as a REFUSAL event."""


def _row_to_event(row: tuple) -> Event:
    seq, ts, kind, source, subject, visibility, payload, mentions, caused_by = row
    return Event(
        kind=Kind(kind), source=source, subject=subject, visibility=Visibility(visibility),
        payload=json.loads(payload) if payload else {},
        mentions=tuple(json.loads(mentions)) if mentions else (),
        caused_by=tuple(json.loads(caused_by)) if caused_by else (),
        seq=seq, ts=ts,
    )


_COLS = "seq, ts, kind, source, subject, visibility, payload, mentions, caused_by"


class Spine:
    def __init__(self, path: str, authorizer: Authorizer | None = None, *,
                 queue_size: int = 256, clock: Callable[[], float] = time.time) -> None:
        self.authorizer: Authorizer = authorizer or DenyAll()
        self.queue_size = queue_size
        self._clock = clock
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA secure_delete=ON")
        self._db.executescript(_SCHEMA)
        self._subs: list[Subscription] = []
        self.refusals = 0
        self.last_seq = self._db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ---- append -------------------------------------------------------------------
    def publish(self, event: Event) -> Event:
        """Append one event on behalf of ``event.source``. Raises Refused."""
        return self.publish_many([event])[0]

    def publish_many(self, events: Iterable[Event]) -> list[Event]:
        """Append a batch in one transaction. One refusal rejects the whole batch."""
        events = list(events)
        with self._lock:
            for ev in events:
                reason = self._publish_refusal(ev)
                if reason:
                    self._refuse(ev.source, f"publish {ev.kind.value}", reason)
            return self._append(events)

    def _publish_refusal(self, ev: Event) -> str:
        if ev.kind in KERNEL_ONLY:
            return "kernel-only kind"
        verdict = self.authorizer.may_publish(ev.source, ev)
        if not verdict.allowed:
            return verdict.reason or "not allowed"
        if not ev.caused_by and not self.authorizer.is_origin(ev.source):
            return "a skill must say which events its event derives from"
        return self._cause_refusal(ev)

    def _cause_refusal(self, ev: Event) -> str:
        """A cause must exist, be live, and be something the publisher may itself read."""
        for cause in ev.caused_by:
            row = self._db.execute(
                f"SELECT {_COLS}, tombstoned FROM events WHERE seq = ?", (cause,)).fetchone()
            if row is None:
                return "cites an event that does not exist"
            if row[-1]:
                return "derives from a forgotten event"
            if not self.authorizer.is_origin(ev.source) and \
                    not self.authorizer.may_read(ev.source, _row_to_event(row[:-1])).allowed:
                return "cites an event it may not read"
        return ""

    def _kernel_append(self, event: Event) -> Event:
        """Kernel services (receipts, grants, forget) append without the publish checks."""
        with self._lock:
            return self._append([event])[0]

    def _append(self, events: list[Event]) -> list[Event]:
        out: list[Event] = []
        now = self._clock()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            for ev in events:
                cur = self._db.execute(
                    "INSERT INTO events (ts, kind, source, subject, visibility, payload, mentions,"
                    " caused_by) VALUES (?,?,?,?,?,?,?,?)",
                    (now, ev.kind.value, ev.source, ev.subject, ev.visibility.value,
                     json.dumps(ev.payload, sort_keys=True),
                     json.dumps(ev.mentions) if ev.mentions else None,
                     json.dumps(ev.caused_by) if ev.caused_by else None))
                seq = cur.lastrowid
                if ev.people:
                    self._db.executemany("INSERT INTO people VALUES (?,?)",
                                         [(seq, p) for p in ev.people])
                if ev.caused_by:
                    self._db.executemany("INSERT INTO causes VALUES (?,?)",
                                         [(seq, c) for c in ev.caused_by])
                out.append(Event(ev.kind, ev.source, ev.subject, ev.visibility, ev.payload,
                                 ev.mentions, ev.caused_by, seq=seq, ts=now))
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        for stored in out:
            for sub in self._subs:
                sub._offer(stored)
            self.last_seq = stored.seq
        self._wake.notify_all()
        return out

    def _refuse(self, who: str, attempted: str, reason: str) -> None:
        """Record the refusal as an event (no content: who, what was tried, why), then raise."""
        with self._lock:
            self.refusals += 1
            self._append([Event(Kind.REFUSAL, KERNEL, visibility=Visibility.KERNEL,
                                payload={"who": who, "attempted": attempted, "reason": reason})])
        raise Refused(f"{who}: {attempted}: {reason}")

    # ---- read ---------------------------------------------------------------------
    def _fetch(self, after: int, kinds: frozenset[Kind] | None, subject: str | None,
               limit: int) -> list[Event]:
        """Live rows after a sequence number, in order. The ONE durable read path."""
        sql = [f"SELECT {_COLS} FROM events WHERE seq > ? AND tombstoned = 0"]
        args: list = [after]
        if kinds:
            sql.append(f"AND kind IN ({','.join('?' * len(kinds))})")
            args += sorted(k.value for k in kinds)
        if subject:
            sql.append("AND seq IN (SELECT seq FROM people WHERE person = ?)")
            args.append(subject)
        sql.append("ORDER BY seq LIMIT ?")
        args.append(limit)
        with self._lock:
            return [_row_to_event(r) for r in self._db.execute(" ".join(sql), args)]

    def _check_subscribe(self, reader: str, kinds, subject) -> frozenset[Kind] | None:
        kinds = frozenset(Kind(k) for k in kinds) if kinds else None
        verdict = self.authorizer.may_subscribe(reader, kinds, subject)
        if not verdict.allowed:
            what = ",".join(sorted(k.value for k in kinds)) if kinds else "all kinds"
            self._refuse(reader, f"read {what}" + (" of one person" if subject else ""),
                         verdict.reason or "not allowed")
        return kinds

    def replay(self, reader: str, from_seq: int = 0, *, kinds=None, subject: str | None = None,
               batch: int = 1000) -> Iterator[Event]:
        """Every event after ``from_seq`` this reader may see. Also the export path."""
        kinds = self._check_subscribe(reader, kinds, subject)
        after = from_seq
        while True:
            rows = self._fetch(after, kinds, subject, batch)
            for ev in rows:
                if self._deliverable(reader, ev):
                    yield ev
            if len(rows) < batch:
                return
            after = rows[-1].seq

    def export(self, reader: str) -> list[dict]:
        return [{"seq": e.seq, "kind": e.kind.value, "subject": e.subject, "payload": e.payload}
                for e in self.replay(reader)]

    def _deliverable(self, reader: str, ev: Event) -> bool:
        return self.authorizer.may_read(reader, ev).allowed

    def subscribe(self, reader: str, *, kinds=None, subject: str | None = None,
                  from_seq: int | None = None) -> "Subscription":
        """Start at ``from_seq``, else at this reader's saved cursor, else at now."""
        kinds = self._check_subscribe(reader, kinds, subject)
        with self._lock:
            if from_seq is None:
                row = self._db.execute("SELECT seq FROM cursors WHERE reader = ?",
                                       (reader,)).fetchone()
                from_seq = row[0] if row else self.last_seq
            sub = Subscription(self, reader, kinds, subject, from_seq)
            self._subs.append(sub)
            return sub


class Subscription:
    """A bounded, ordered view of the spine for one reader. Pull with ``poll``."""

    def __init__(self, spine: Spine, reader: str, kinds: frozenset[Kind] | None,
                 subject: str | None, tail: int) -> None:
        self.spine, self.reader, self.kinds, self.subject = spine, reader, kinds, subject
        self._queue: deque[Event] = deque()
        self._tail = tail                     # every event up to here is queued or skipped
        self._live = tail == spine.last_seq   # False = must catch up from the log
        self.lagged = 0                       # times the queue filled and we fell back
        self.closed = False

    def _matches(self, ev: Event) -> bool:
        if self.kinds and ev.kind not in self.kinds:
            return False
        return not self.subject or self.subject in ev.people

    def _offer(self, ev: Event) -> None:
        """Called by the publisher under the spine lock. Never blocks."""
        if not self._live:
            return
        if self._matches(ev):
            if len(self._queue) >= self.spine.queue_size:
                self._live = False
                self.lagged += 1
                return
            self._queue.append(ev)
        self._tail = ev.seq

    def _catch_up(self) -> None:
        rows = self.spine._fetch(self._tail, self.kinds, self.subject, self.spine.queue_size)
        self._queue.extend(rows)
        if len(rows) < self.spine.queue_size:
            self._tail, self._live = self.spine.last_seq, True
        else:
            self._tail = rows[-1].seq

    def poll(self, timeout: float = 0.0) -> Event | None:
        """The next event this reader may see, or None when there is none in time."""
        deadline = time.monotonic() + timeout
        with self.spine._wake:
            while not self.closed:
                if not self._queue and not self._live:
                    self._catch_up()
                while self._queue:
                    ev = self._queue.popleft()
                    if self.spine._deliverable(self.reader, ev):
                        return ev
                if not self._live:
                    continue
                left = deadline - time.monotonic()
                if left <= 0:
                    return None
                self.spine._wake.wait(left)
        return None

    def ack(self, seq: int) -> None:
        """Save the cursor: a later ``subscribe`` by this reader resumes after ``seq``."""
        with self.spine._lock:
            self.spine._db.execute(
                "INSERT INTO cursors VALUES (?, ?) ON CONFLICT(reader) DO UPDATE SET seq = excluded.seq",
                (self.reader, seq))

    def close(self) -> None:
        with self.spine._wake:
            self.closed = True
            if self in self.spine._subs:
                self.spine._subs.remove(self)
            self.spine._wake.notify_all()
