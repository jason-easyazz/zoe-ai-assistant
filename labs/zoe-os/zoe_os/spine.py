"""The event spine: one durable, ordered, capability-filtered bus.

* durable append to SQLite; the sequence number is the row id, never reused;
* default deny: with no authorizer nothing may be published, subscribed to or read;
* a subscriber has a bounded queue. A publisher NEVER waits for a slow subscriber: when a
  queue is full the subscriber is marked lagging and catches up from the durable log, in
  order and without loss, the next time it polls (backpressure by lag-to-replay);
* every delivery is re-authorized at the moment it is handed over, so a revoked grant
  stops events already sitting in a queue;
* a refusal is itself an event (kind REFUSAL, kernel visibility) and carries no content;
* FORGET is a property of the log, not of each feature (``Spine.forget``): a person's events,
  everything derived from them and their bytes on disk are gone from replay, export and every
  queue, every subscriber is told, and a skill cannot publish the forgotten words again.
  A forget is one of four scopes: everything about the person, named events, an ENTITY
  ("forget Dana": their events that name it, plus a keyed hash of the name so a skill cannot
  write the name about them again), or a TOPIC (their events that hold every topic word).

Trust boundary: this is an in-process prototype. Python cannot stop a skill in the same
process from calling a private method; the real boundary is a process and a socket.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from collections import deque
from typing import Callable, Iterable, Iterator, NamedTuple, Protocol

from . import forget_match as fm
from .events import CONTENT_KEYS, KERNEL_ONLY, Event, Kind, Visibility

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
CREATE TABLE IF NOT EXISTS forget_ledger (
  id INTEGER PRIMARY KEY, subject TEXT NOT NULL, upto INTEGER NOT NULL, ts REAL NOT NULL,
  count INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS shield (fp BLOB PRIMARY KEY);
CREATE TABLE IF NOT EXISTS entity_shield (subject TEXT NOT NULL, fp BLOB NOT NULL, PRIMARY KEY (subject, fp));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value BLOB NOT NULL);
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
    def may_forget(self, actor: str, subject: str) -> Decision: ...


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

    def may_forget(self, actor: str, subject: str) -> Decision:
        return Decision(False, "default deny")


class Refused(Exception):
    """Raised to the caller; the same refusal is also appended as a REFUSAL event."""


class ForgetResult(NamedTuple):
    count: int      # events erased, including derived ones
    upto: int       # the forget covers everything up to this sequence number


MAX_TOPIC_EVENTS = 12   # a topic forget matching more first-hand events than this is refused
MAX_NOTICE_SEQS = 256   # a scoped forget notice lists the erased sequence numbers up to this many


def _strings(node):
    """Every string value in a payload, at any depth."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def _keys_and_strings(node):
    """Every string in a payload INCLUDING dict keys: a name can be smuggled as a key."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield key
            yield from _keys_and_strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _keys_and_strings(value)


def _content(node):
    """The free-text values of a payload: strings held under a content key, at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in CONTENT_KEYS and isinstance(value, str):
                yield value
            else:
                yield from _content(value)
    elif isinstance(node, list):
        for value in node:
            yield from _content(value)


def _chunks(items: list, size: int = 500):
    for i in range(0, len(items), size):
        yield items[i:i + size]


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
        self._epoch = 0     # bumped by every forget; a subscription re-checks its queue on a change
        key = self._db.execute("SELECT value FROM meta WHERE key = 'shield_key'").fetchone()
        if key is None:
            key = (os.urandom(32),)
            self._db.execute("INSERT INTO meta VALUES ('shield_key', ?)", key)
        self._shield_key: bytes = key[0]
        self._shield: set[bytes] = {r[0] for r in self._db.execute("SELECT fp FROM shield")}
        self._entities: dict[str, set[bytes]] = {}      # person -> keyed hashes of forgotten names
        for person, fp in self._db.execute("SELECT subject, fp FROM entity_shield"):
            self._entities.setdefault(person, set()).add(fp)
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
            for ev in events:
                if self._shield and self.authorizer.is_origin(ev.source):
                    self._reassert(ev.payload)
            return self._append(events)

    def _publish_refusal(self, ev: Event) -> str:
        if ev.kind in KERNEL_ONLY:
            return "kernel-only kind"
        verdict = self.authorizer.may_publish(ev.source, ev)
        if not verdict.allowed:
            return verdict.reason or "not allowed"
        if not ev.caused_by and not self.authorizer.is_origin(ev.source):
            return "a skill must say which events its event derives from"
        reason = self._cause_refusal(ev.source, ev.caused_by)
        if not reason and not self.authorizer.is_origin(ev.source):
            if self._shielded(ev.payload):
                reason = "carries forgotten content"
            elif self._names_forgotten(ev):
                reason = "names a forgotten entity"
        return reason

    def _cause_refusal(self, source: str, caused_by: tuple[int, ...]) -> str:
        """A cause must exist, be live, and be something the publisher may itself read."""
        for cause in caused_by:
            row = self._db.execute(
                f"SELECT {_COLS}, tombstoned FROM events WHERE seq = ?", (cause,)).fetchone()
            if row is None:
                return "cites an event that does not exist"
            if row[-1]:
                return "derives from a forgotten event"
            if not self.authorizer.is_origin(source) and \
                    not self.authorizer.may_read(source, _row_to_event(row[:-1])).allowed:
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

    # ---- forget -------------------------------------------------------------------
    def forget(self, subject: str, *, actor: str, seqs: Iterable[int] | None = None,
               entity: str | None = None, topic: str | None = None) -> ForgetResult:
        """Erase a person's events and all that derives from them.

        With nothing else: every event about them. ``seqs``: only the named ones. ``entity``:
        only their events that name it, and the name is shielded for them from then on.
        ``topic``: only their events that hold every topic word. An entity or topic with no
        words is REFUSED: a scope that matches nothing never widens to "everything".
        """
        with self._lock:
            verdict = self.authorizer.may_forget(actor, subject)
            if not verdict.allowed:
                self._refuse(actor, "forget", verdict.reason or "not theirs to forget")
            if entity is not None and topic is not None:
                self._refuse(actor, "forget", "one scope at a time")
            key = fm.entity_key(entity) if entity is not None else ""
            groups = fm.topic_groups(topic) if topic is not None else []
            if (entity is not None and not key) or (topic is not None and not groups):
                self._refuse(actor, "forget", "nothing to match")
            upto = self.last_seq
            seed = {r[0] for r in self._db.execute(
                "SELECT seq FROM people WHERE person = ? AND seq <= ?", (subject, upto))}
            if seqs is not None:
                seed &= set(seqs)
            if key or groups:
                seed = self._matching(seed, key, groups)
                if groups and len(seed) > MAX_TOPIC_EVENTS:
                    self._refuse(actor, "forget", "too many events match: say which part")
            dead = sorted(self._closure(seed))
            self._tombstone(dead)
            if key:
                self._shield_entity(subject, key)
            self._db.execute("INSERT INTO forget_ledger (subject, upto, ts, count) VALUES (?,?,?,?)",
                             (subject, upto, self._clock(), len(dead)))
            self._epoch += 1
            notice = {"subject": subject, "upto": upto, "count": len(dead)}
            if key or groups:           # a scoped notice says WHICH events, never which words
                notice |= {"scope": "entity" if key else "topic", "seqs": dead[:MAX_NOTICE_SEQS]}
            self._append([Event(Kind.FORGET, KERNEL, visibility=Visibility.KERNEL, payload=notice)])
            return ForgetResult(len(dead), upto)

    def _matching(self, seed: set[int], key: str, groups: list) -> set[int]:
        """The seed events whose payload names the entity / holds the whole topic."""
        hit: set[int] = set()
        for chunk in _chunks(sorted(seed)):
            marks = ",".join("?" * len(chunk))
            for seq, payload in self._db.execute(
                    f"SELECT seq, payload FROM events WHERE tombstoned = 0 AND seq IN ({marks})", chunk):
                body = json.loads(payload) if payload else {}
                if any(fm.names(t, key) for t in _keys_and_strings(body)) if key \
                        else fm.about(" . ".join(_strings(body)), groups):
                    hit.add(seq)
        return hit

    # ---- the entity shield: a keyed hash of a forgotten name, per person, never the name -----
    def _efp(self, person: str, key: str) -> bytes:
        return hmac.new(self._shield_key, f"entity\0{person}\0{key}".encode(), hashlib.sha256).digest()

    def _shield_entity(self, subject: str, key: str) -> None:
        fp = self._efp(subject, key)
        self._entities.setdefault(subject, set()).add(fp)
        self._db.execute("INSERT OR IGNORE INTO entity_shield VALUES (?, ?)", (subject, fp))

    def _names_forgotten(self, ev: Event) -> bool:
        """Does a skill's event name something a person it is about asked to forget? An event
        about nobody in particular is checked against everyone's forgotten names."""
        watched = [(p, self._entities[p]) for p in (ev.people or self._entities) if self._entities.get(p)]
        if not watched:
            return False
        return any(self._efp(person, run) in fps
                   for text in _keys_and_strings(ev.payload) for run in fm.runs(fm.words(text))
                   for person, fps in watched)

    def release(self, subject: str, entity: str, *, actor: str) -> bool:
        """The person explicitly teaches the name again: lift the shield. Never automatic: a
        first-hand mention by an adapter is recorded but does NOT lift it."""
        with self._lock:
            verdict = self.authorizer.may_forget(actor, subject)
            if not verdict.allowed:
                self._refuse(actor, "release", verdict.reason or "not theirs to release")
            fp = self._efp(subject, fm.entity_key(entity))
            if fp not in self._entities.get(subject, ()):
                return False
            self._entities[subject].discard(fp)
            self._db.execute("DELETE FROM entity_shield WHERE subject = ? AND fp = ?", (subject, fp))
            return True

    def _closure(self, seed: set[int]) -> set[int]:
        """The seed plus every event that derives from it, to any depth."""
        dead, frontier = set(seed), list(seed)
        while frontier:
            chunk, frontier = frontier[:500], frontier[500:]
            marks = ",".join("?" * len(chunk))
            for (seq,) in self._db.execute(
                    f"SELECT DISTINCT seq FROM causes WHERE cause IN ({marks})", chunk):
                if seq not in dead:
                    dead.add(seq)
                    frontier.append(seq)
        return dead

    def _tombstone(self, seqs: list[int]) -> None:
        """Erase the rows in place, remember a keyed hash of their first-hand free text."""
        fps: set[bytes] = set()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            for chunk in _chunks(seqs):
                marks = ",".join("?" * len(chunk))
                for source, payload in self._db.execute(
                        f"SELECT source, payload FROM events WHERE seq IN ({marks})", chunk):
                    if payload and self.authorizer.is_origin(source):
                        fps.update(self._fp(text) for text in _content(json.loads(payload)))
                self._db.execute(
                    "UPDATE events SET tombstoned = 1, payload = NULL, mentions = NULL, subject = NULL,"
                    f" caused_by = NULL WHERE seq IN ({marks})", chunk)
                self._db.execute(f"DELETE FROM people WHERE seq IN ({marks})", chunk)
                self._db.execute(f"DELETE FROM causes WHERE seq IN ({marks})", chunk)
            self._db.executemany("INSERT OR IGNORE INTO shield VALUES (?)", [(fp,) for fp in fps])
            self._db.execute("COMMIT")
        except BaseException:
            self._db.execute("ROLLBACK")
            raise
        self._shield |= fps
        self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # old page images leave the WAL file

    def _fp(self, text: str) -> bytes:
        norm = " ".join(text.casefold().split()).encode()
        return hmac.new(self._shield_key, norm, hashlib.sha256).digest()

    def _shielded(self, payload: dict) -> bool:
        """Does this payload carry, anywhere, a string a person asked to forget?"""
        return bool(self._shield) and any(self._fp(s) in self._shield for s in _strings(payload))

    def _reassert(self, payload: dict) -> None:
        """A trusted adapter recorded these words again first-hand: that is new reality."""
        again = {self._fp(text) for text in _content(payload)} & self._shield
        if again:
            self._shield -= again
            self._db.executemany("DELETE FROM shield WHERE fp = ?", [(fp,) for fp in again])

    def _tombstoned(self, seqs: list[int]) -> set[int]:
        marks = ",".join("?" * len(seqs))
        return {r[0] for r in self._db.execute(
            f"SELECT seq FROM events WHERE tombstoned = 1 AND seq IN ({marks})", seqs)}

    # ---- read ---------------------------------------------------------------------
    def _get(self, seq: int) -> Event | None:
        """One live event by sequence number (kernel-internal: no capability check)."""
        with self._lock:
            row = self._db.execute(
                f"SELECT {_COLS} FROM events WHERE seq = ? AND tombstoned = 0", (seq,)).fetchone()
        return _row_to_event(row) if row else None

    def _fetch(self, after: int, kinds: frozenset[Kind] | None, subject: str | None,
               limit: int) -> list[Event]:
        """Live rows after a sequence number, in order. The ONE durable read path."""
        sql = [f"SELECT {_COLS} FROM events WHERE seq > ? AND tombstoned = 0"]
        args: list = [after]
        if kinds:
            sql.append(f"AND kind IN ({','.join('?' * len(kinds))})")
            args += sorted(k.value for k in kinds)
        if subject:
            sql.append("AND (seq IN (SELECT seq FROM people WHERE person = ?) OR kind = 'forget')")
            args.append(subject)
        sql.append("ORDER BY seq LIMIT ?")
        args.append(limit)
        with self._lock:
            return [_row_to_event(r) for r in self._db.execute(" ".join(sql), args)]

    def _check_subscribe(self, reader: str, kinds, subject) -> frozenset[Kind] | None:
        kinds = frozenset(Kind(k) for k in kinds) | {Kind.FORGET} if kinds else None
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
        """The delivery-time check. A FORGET notice reaches every reader: it is a duty, not data."""
        return ev.kind is Kind.FORGET or self.authorizer.may_read(reader, ev).allowed

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
        self._epoch = spine._epoch
        self.closed = False

    def _matches(self, ev: Event) -> bool:
        if ev.kind is Kind.FORGET:
            return True
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

    def _purge_forgotten(self) -> None:
        """After a forget, withdraw any erased event still waiting in this queue."""
        if self._epoch != self.spine._epoch and self._queue:
            dead = self.spine._tombstoned([e.seq for e in self._queue])
            self._queue = deque(e for e in self._queue if e.seq not in dead)
        self._epoch = self.spine._epoch

    def poll(self, timeout: float = 0.0) -> Event | None:
        """The next event this reader may see, or None when there is none in time."""
        deadline = time.monotonic() + timeout
        with self.spine._wake:
            while not self.closed:
                self._purge_forgotten()
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
