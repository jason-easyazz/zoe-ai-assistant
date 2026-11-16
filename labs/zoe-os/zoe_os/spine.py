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
from dataclasses import replace
from types import MappingProxyType
from collections import deque
from typing import Callable, Iterable, Iterator, Mapping, NamedTuple, Protocol

from . import forget_match as fm
from .events import CONTENT_KEYS, KERNEL_ONLY, Event, Kind, Visibility

KERNEL = "kernel"
FORGOTTEN, EXPIRED = 1, 2       # the two reasons a row's ``tombstoned`` is not 0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  ts REAL NOT NULL, kind TEXT NOT NULL, source TEXT NOT NULL,
  subject TEXT, visibility TEXT NOT NULL,
  payload TEXT, mentions TEXT, caused_by TEXT,
  tombstoned INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS people (
  seq INTEGER NOT NULL, person TEXT NOT NULL, PRIMARY KEY (person, seq)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS people_seq ON people(seq);
CREATE TABLE IF NOT EXISTS nested_people (
  seq INTEGER NOT NULL, person TEXT NOT NULL, PRIMARY KEY (person, seq)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS nested_people_seq ON nested_people(seq);
CREATE TABLE IF NOT EXISTS nested_members (owner BLOB PRIMARY KEY) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS causes (
  seq INTEGER NOT NULL, cause INTEGER NOT NULL, PRIMARY KEY (seq, cause)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS causes_cause ON causes(cause);
CREATE TABLE IF NOT EXISTS reversals (seq INTEGER NOT NULL, target INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS reversals_target ON reversals(target);
CREATE INDEX IF NOT EXISTS reversals_seq ON reversals(seq);
CREATE TABLE IF NOT EXISTS dependents (seq INTEGER NOT NULL, target INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS dependents_target ON dependents(target);
CREATE INDEX IF NOT EXISTS latest_receipt ON events(subject, seq DESC)
  WHERE kind = 'action_receipt' AND tombstoned = 0;
CREATE INDEX IF NOT EXISTS live_kind ON events(kind, seq) WHERE tombstoned = 0;
CREATE TABLE IF NOT EXISTS snapshot_dependencies (
  source_seq INTEGER NOT NULL, receipt_seq INTEGER NOT NULL,
  PRIMARY KEY (source_seq, receipt_seq)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS snapshot_receipt ON snapshot_dependencies(receipt_seq);
CREATE TABLE IF NOT EXISTS opaque_snapshots (
  person TEXT NOT NULL, receipt_seq INTEGER NOT NULL,
  PRIMARY KEY (person, receipt_seq)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS opaque_receipt ON opaque_snapshots(receipt_seq);
CREATE TABLE IF NOT EXISTS cursors (reader TEXT PRIMARY KEY, seq INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS consumed_receipts (
  receipt_seq INTEGER PRIMARY KEY, claim_seq INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS forget_ledger (
  id INTEGER PRIMARY KEY, subject TEXT NOT NULL, upto INTEGER NOT NULL, ts REAL NOT NULL,
  count INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS forget_subject ON forget_ledger(subject);
CREATE TABLE IF NOT EXISTS shield (fp BLOB PRIMARY KEY) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS scoped_shield (
  owner BLOB NOT NULL, fp BLOB NOT NULL, PRIMARY KEY (fp, owner)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS scoped_entities (
  owner BLOB NOT NULL, fp BLOB NOT NULL, PRIMARY KEY (fp, owner)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS entity_shield (subject TEXT NOT NULL, fp BLOB NOT NULL, PRIMARY KEY (subject, fp)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value BLOB NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS retired_counts (
  day TEXT NOT NULL, kind TEXT NOT NULL, n INTEGER NOT NULL, PRIMARY KEY (day, kind)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS intents (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, skill TEXT NOT NULL, action TEXT NOT NULL,
  person TEXT NOT NULL, grant_id TEXT, accept_seq INTEGER, caused_by TEXT, receipt_seq INTEGER,
  undoes INTEGER
);
CREATE INDEX IF NOT EXISTS intent_receipt ON intents(receipt_seq);
CREATE INDEX IF NOT EXISTS intent_person ON intents(person);
CREATE INDEX IF NOT EXISTS intent_accept ON intents(accept_seq) WHERE accept_seq IS NOT NULL;
CREATE INDEX IF NOT EXISTS intent_undo ON intents(undoes) WHERE undoes IS NOT NULL;
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

    def __init__(self, message: str, *, reason: str = 'request_refused', wire: str | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.wire = wire      # set only by Spine._refuse, from kernel vocabulary; the one text that may cross the socket


class Skeleton(NamedTuple):
    """An erased event whose body has already aged out: the sequence number and kind are all
    that is left, and all a store purge keyed by request seq needs."""
    seq: int
    kind: Kind
    payload: Mapping = MappingProxyType({})


class ForgetResult(NamedTuple):
    count: int      # rows erased, including derived events, grants, refusals and scoped notices
    upto: int       # the forget covers everything up to this sequence number


class EventCache:
    """A kernel-backed subscriber view. Keep sequence numbers, never copies of event bodies.

    Each access re-reads the authorized live log, so forget reaches this view immediately,
    even if the subscriber ignores the notice. Arbitrary private copies are outside Python's
    in-process boundary; this is the supported cache contract.
    """
    def __init__(self, history: Callable[[], Iterable[Event]]) -> None:
        self._history = history
        self._seqs: set[int] = set()

    def append(self, event: Event) -> None:
        self._seqs.add(event.seq)

    def __iter__(self):
        return (event for event in self._history() if event.seq in self._seqs)


MAX_TOPIC_EVENTS = 12   # a topic forget matching more first-hand events than this is refused
MAX_NOTICE_SEQS = 256   # batch size, never a limit on forget coverage


def forget_affects(notice: Event, seq: int) -> bool:
    """Unknown/legacy purge scopes fail closed; an explicit empty selection is a no-op."""
    p = notice.payload
    if p.get('scope') == 'person':
        return seq <= p.get('upto', float('inf'))
    if p.get('scope') in {'seqs', 'entity', 'topic'} and 'seqs' in p:
        return seq in p['seqs']
    return True


#: action kinds that take rows away and were written WITHOUT the ``reverses`` marker (the shadow kernel's and the lab drivers'
#: ``list.remove``: a snapshot of the person's WHOLE previous list). ``undo`` needs no entry: it links by ``undoes`` and ``caused_by``.
LEGACY_REMOVALS = frozenset({"list.remove"})


def _list_key(row: dict) -> tuple:
    return row.get("list", "shopping"), row.get("item")


def _row_source(payload: dict) -> tuple[int, bool] | None:
    """A durable-row store's receipt (``zoe_os/rows.py``: its before-state is ``{"id", "row", "source"[, "reverses"]}``) over a row
    another request CREATED: ``(that creating request's seq, does this action take the row away)``. ``None`` for anything else,
    and for the create itself (its source is its own request, already its cause).
    * an action that takes the row away (a cancel) is a REVERSAL: forgetting it takes the create (or the rebuild brings the row
      back), and forgetting the create takes it;
    * every action over the row (a cancel too) is a DEPENDENT of the create and of every earlier action over the row: it copies
      the row's whole state, so forgetting any of them (an edit's new words) takes it; forgetting it alone takes only it and the
      actions after it."""
    undo = payload.get("undo") if isinstance(payload, dict) else None
    before = undo.get("before") if isinstance(undo, dict) else None
    if not isinstance(before, dict) or type(before.get("source")) is not int:
        return None
    args = undo.get("args") if isinstance(undo.get("args"), dict) else {}
    if before["source"] == args.get("asked"):
        return None
    return before["source"], before.get("reverses") is True


def _reversal_targets(payload: dict) -> list[int] | None:
    """The requests of the rows a receipt REMOVED. Marked (``reverses``, provenance 1): every row's ``asked``. A LEGACY removal
    (``LEGACY_REMOVALS``, unmarked: its snapshot is the whole previous list): the rows whose (list, item) is the one it removed,
    which is exactly what such a removal took. ``None`` = a legacy removal this record alone cannot link (a matching row without
    an ``asked``): the caller links it by the person's earlier adds of that item instead (``Spine._legacy_targets``)."""
    if not isinstance(payload, dict):
        return []
    undo = payload.get("undo")
    before = undo.get("before") if isinstance(undo, dict) else None
    if isinstance(before, dict) and before.get("provenance") == 1 and before.get("reverses") is True:
        return sorted({r["asked"] for r in before.get("rows", ()) if isinstance(r, dict) and type(r.get("asked")) is int})
    if payload.get("action") not in LEGACY_REMOVALS or not isinstance(undo, dict) or not isinstance(undo.get("args"), dict):
        return []
    want = _list_key(undo["args"])
    rows = before.get("rows") if isinstance(before, dict) else None
    if not isinstance(rows, list):
        return None
    hit = [r for r in rows if isinstance(r, dict) and _list_key(r) == want]
    if any(type(r.get("asked")) is not int for r in hit):
        return None
    return sorted({r["asked"] for r in hit})


def _strings(node):
    """Every string value in a payload, at any depth."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, (list, tuple)):     # a tuple is a JSON array on disk
        for value in node:
            yield from _strings(value)


def _keys_and_strings(node):
    """Every string in a payload INCLUDING dict keys: a name can be smuggled as a key."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            if isinstance(key, str):
                yield key
            yield from _keys_and_strings(value)
    elif isinstance(node, (list, tuple)):     # a tuple is a JSON array on disk
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
    elif isinstance(node, (list, tuple)):     # a tuple is a JSON array on disk
        for value in node:
            yield from _content(value)


def _nested_ids(kind, payload: dict, people, members) -> set[str]:
    """Known member ids a payload names as a WHOLE string value or a dict key, at any depth,
    other than the event's own subject and mentions. Exact match only: an id inside longer
    free text is the name shield's and the entity forget's job, not this index's. A receipt's
    saved before-state is a copy whose source rows carry their own links (the snapshot scrub
    reaches it when they die); indexing it would erase an unrelated surviving action."""
    if not members:
        return set()
    if kind is Kind.ACTION_RECEIPT and (isinstance(payload.get('undo'), dict) or
                                        isinstance(payload.get('proof'), dict)):
        payload = dict(payload)
        if isinstance(payload.get('undo'), dict):
            payload['undo'] = {'args': payload['undo'].get('args', {})}
        if isinstance(payload.get('proof'), dict):
            payload['proof'] = {k: v for k, v in payload['proof'].items() if k != 'restored'}
    return {s for s in _keys_and_strings(payload) if s in members} - set(people)


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
                 queue_size: int = 256, clock: Callable[[], float] = time.time,
                 members: Iterable[str] | None = None) -> None:
        self.authorizer: Authorizer = authorizer or DenyAll()
        # The member ids a payload can name (``_nested_ids``): the kernel's people by default.
        store = getattr(self.authorizer, 'store', None)
        self._members: frozenset[str] = frozenset(
            members if members is not None else getattr(store, 'people', None) or ())
        self.queue_size = queue_size
        self._clock = clock
        self._lock = threading.RLock()
        self._wake = threading.Condition(self._lock)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA secure_delete=ON")
        self._db.executescript(_SCHEMA)
        if self._db.execute("SELECT 1 FROM meta WHERE key = 'reversals_v3'").fetchone() is None:
            # An older record (or one indexed by v1, which knew only the marker): index EVERY removal it holds, marked or legacy,
            # once. ONE transaction, the old index rebuilt from nothing: a crash halfway leaves no meta key and no partial index,
            # and the next start does it again (idempotent). One pass over the receipts (plus, for a legacy removal that cannot be
            # linked by its own record, one pass over that person's earlier receipts).
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute("DELETE FROM reversals")
                self._db.execute("DELETE FROM dependents")
                for seq, subject, raw in self._db.execute(
                        "SELECT seq, subject, payload FROM events WHERE kind = ? AND tombstoned = 0 ORDER BY seq",
                        (Kind.ACTION_RECEIPT.value,)).fetchall():
                    self._index_reversal(seq, subject, json.loads(raw) if raw else {})
                self._db.execute("DELETE FROM meta WHERE key IN ('reversals_v1', 'reversals_v2')")
                self._db.execute("INSERT INTO meta VALUES ('reversals_v3', x'01')")
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
        self._backfill_snapshots()
        # Upgrade extant claims without retaining any person or payload in bookkeeping.
        self._db.execute("""
            INSERT OR IGNORE INTO consumed_receipts
            SELECT r.seq, MIN(c.seq) FROM events c JOIN events r
              ON r.seq = json_extract(c.payload, '$.receipt_seq')
            WHERE c.kind = 'claim' AND r.kind = 'action_receipt' AND r.seq < c.seq
              AND json_extract(r.payload, '$.verified') = 1
              AND json_extract(r.payload, '$.skill') = c.source AND r.subject IS c.subject
              AND json_extract(r.payload, '$.action') IS json_extract(c.payload, '$.action')
            GROUP BY r.seq
        """)
        self._subs: list[Subscription] = []
        self._before_forget = None  # trusted kernel store cleanup, never a skill/wire operation
        self._forget_candidates: list = []  # trusted row stores: visible cross-member receipts, before matching/closure
        self._epoch = 0     # bumped by every forget; a subscription re-checks its queue on a change
        key = self._db.execute("SELECT value FROM meta WHERE key = 'shield_key'").fetchone()
        if key is None:
            key = (os.urandom(32),)
            self._db.execute("INSERT INTO meta VALUES ('shield_key', ?)", key)
        self._shield_key: bytes = key[0]
        # Upgrade old bookkeeping references once, using the kind index, never a
        # history scan on forget. Legacy shields deliberately remain global.
        if not self._db.execute("SELECT 1 FROM meta WHERE key='record_people_v1'").fetchone():
            for seq, kind, payload in self._db.execute(
                    "SELECT seq, kind, payload FROM events INDEXED BY live_kind "
                    "WHERE tombstoned=0 AND kind IN ('grant_change','refusal','forget')").fetchall():
                self._index_record(seq, Kind(kind), json.loads(payload or '{}'))
            self._db.execute("INSERT INTO meta VALUES ('record_people_v1', 1)")
        self._backfill_nested()
        self._shield: set[bytes] = {r[0] for r in self._db.execute("SELECT fp FROM shield")}
        self._entities: dict[str, set[bytes]] = {}      # person -> keyed hashes of forgotten names
        for person, fp in self._db.execute("SELECT subject, fp FROM entity_shield"):
            self._entities.setdefault(person, set()).add(fp)
        self.refusals = 0
        self.last_seq = self._db.execute("SELECT COALESCE(MAX(seq), 0) FROM events").fetchone()[0]
        # Schema/index creation uses autocommit. Do not retain all its intermediate
        # page images in WAL (also keeps the oversized-frame disk guard meaningful).
        self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

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
            claimed = set()
            for ev in events:
                reason = self._publish_refusal(ev) or self._claim_refusal(ev, claimed)
                if reason:
                    self._refuse(ev.source, f"publish {ev.kind.value}", reason)
            return self._append(events)

    def _claim_refusal(self, ev: Event, claimed: set) -> str:
        """Fail closed before append/delivery, including duplicates in the same batch."""
        if ev.kind is not Kind.CLAIM:
            return ""
        from .receipts import ClaimAuditor
        seq = ev.payload.get("receipt_seq")
        candidate = replace(ev, seq=self.last_seq + 1)  # never trust a caller-supplied sequence
        if type(seq) is not int or not 0 < seq <= self.last_seq or seq not in ev.caused_by or seq in claimed or \
                not ClaimAuditor(self, self.authorizer).backed(candidate):
            return "claim requires an unused verified matching receipt"
        claimed.add(seq)
        return ""

    def _publish_refusal(self, ev: Event) -> str:
        if ev.kind in KERNEL_ONLY:
            return "kernel-only kind"
        verdict = self.authorizer.may_publish(ev.source, ev)
        if not verdict.allowed:
            return verdict.reason or "not allowed"
        if not ev.caused_by and not self.authorizer.is_origin(ev.source):
            return "a skill must say which events its event derives from"
        reason = self._cause_refusal(ev.source, ev.caused_by)
        if not reason:
            reason = self._payload_refusal(ev)
        return reason

    def _payload_refusal(self, ev: Event) -> str:
        """Shared publish/action shield semantics, including nested arrays and dictionary keys."""
        if self._shielded(ev):
            return "carries forgotten content"
        if self._names_forgotten(ev):
            return "names a forgotten entity"
        return ""

    def _cause_refusal(self, source: str, caused_by: tuple[int, ...]) -> str:
        """A cause must exist, be live, and be something the publisher may itself read."""
        for cause in caused_by:
            if type(cause) is not int or cause <= 0:
                return "invalid_cause"
            row = self._db.execute(
                f"SELECT {_COLS}, tombstoned FROM events WHERE seq = ?", (cause,)).fetchone()
            if row is None:
                return "cites an event that does not exist"
            if row[-1]:
                return "derives from a forgotten event" if row[-1] == FORGOTTEN else \
                    "derives from an event no longer kept"
            if not self.authorizer.is_origin(source) and \
                    not self._deliverable(source, _row_to_event(row[:-1])):   # a FORGET notice may be cited
                return "cites an event it may not read"
        return ""

    def _kernel_append(self, event: Event, then: Callable[[int], None] | None = None) -> Event:
        """Kernel services (receipts, grants, forget) append without the publish checks.
        ``then(seq)`` runs INSIDE the same transaction: the event and its bookkeeping land together."""
        with self._lock:
            if self._payload_refusal(event):
                self._refuse(KERNEL, "kernel_write", "forgotten_kernel_content")
            return self._append([event], then)[0]

    def _index_record(self, seq, kind, payload):
        keys = {Kind.GRANT_CHANGE: ('person', 'guardian', 'with'), Kind.REFUSAL: ('who',),
                Kind.FORGET: ('subject',)}.get(kind, ())
        self._db.executemany("INSERT OR IGNORE INTO people VALUES (?,?)",
                             [(seq, payload[k]) for k in keys if payload.get(k)])

    def _append(self, events: list[Event], then: Callable[[int], None] | None = None,
                *, before: Callable[[], None] | None = None) -> list[Event]:
        out: list[Event] = []
        now = self._clock()
        self._db.execute("BEGIN IMMEDIATE")
        try:
            if before is not None:
                before()
            for ev in events:
                cur = self._db.execute(
                    "INSERT INTO events (ts, kind, source, subject, visibility, payload, mentions,"
                    " caused_by) VALUES (?,?,?,?,?,?,?,?)",
                    (now, ev.kind.value, ev.source, ev.subject, ev.visibility.value,
                     json.dumps(ev.payload, sort_keys=True),
                     json.dumps(ev.mentions) if ev.mentions else None,
                     json.dumps(ev.caused_by) if ev.caused_by else None))
                seq = cur.lastrowid
                self._index_record(seq, ev.kind, ev.payload)
                nested = _nested_ids(ev.kind, ev.payload, ev.people, self._members)
                if nested:
                    self._db.executemany("INSERT OR IGNORE INTO nested_people VALUES (?,?)",
                                         [(seq, p) for p in nested])
                if ev.kind is Kind.ACTION_RECEIPT:
                    self._index_snapshot(seq, ev.subject, ev.payload)
                if ev.kind is Kind.CLAIM:
                    from .receipts import ClaimAuditor
                    if ClaimAuditor(self, self.authorizer).backed(replace(ev, seq=seq)):
                        self._db.execute("INSERT INTO consumed_receipts VALUES (?, ?)",
                                         (ev.payload['receipt_seq'], seq))
                if ev.people:
                    self._db.executemany("INSERT OR IGNORE INTO people VALUES (?,?)",
                                         [(seq, p) for p in ev.people])
                if ev.caused_by:
                    self._db.executemany("INSERT OR IGNORE INTO causes VALUES (?,?)",
                                         [(seq, c) for c in ev.caused_by])
                if ev.kind is Kind.ACTION_RECEIPT:
                    self._index_reversal(seq, ev.subject, ev.payload)
                out.append(Event(ev.kind, ev.source, ev.subject, ev.visibility, ev.payload,
                                 ev.mentions, ev.caused_by, seq=seq, ts=now))
            if then is not None:
                then(out[-1].seq)
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
            # A failed identity/actor check must not turn the claimed name into content.
            store = getattr(self.authorizer, 'store', None)
            if store is not None and who not in store.manifests and who not in store.people and who != KERNEL:
                who = 'unverified-peer'
            self.refusals += 1
            record = Event(Kind.REFUSAL, KERNEL, visibility=Visibility.KERNEL,
                           payload={"who": who, "attempted": attempted, "reason": reason})
            # Even fixed refusal codes can equal a forgotten sentence. Keep an
            # anonymous refusal marker rather than publishing those words again.
            if self._shielded(record) or self._names_forgotten(record):
                record = Event(Kind.REFUSAL, KERNEL, visibility=Visibility.KERNEL)
            self._append([record])
        text = f"{who}: {attempted}: {reason}"
        raise Refused(text, reason=reason, wire=text)

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
            if entity is not None and len(fm.words(entity)) > fm.MAX_KEY_TOKENS:
                self._refuse(actor, "forget", "entity has too many words")
            groups = fm.topic_groups(topic) if topic is not None else []
            if (entity is not None and not key) or (topic is not None and not groups):
                self._refuse(actor, "forget", "nothing to match")
            upto = self.last_seq
            selected = None if seqs is None else sorted(set(seqs))
            owner = self._owner(subject)
            whole = selected is None and not key and not groups
            dead: list[int] = []
            notices: list[Event] = []
            scope = "entity" if key else "topic" if groups else "seqs" if seqs is not None else "person"

            def prepare():
                # Select under the same write transaction as erasure and every notice.
                seed = set()
                if selected is None:
                    seed.update(r[0] for r in self._db.execute(
                        "SELECT seq FROM people WHERE person = ? AND seq <= ?", (subject, upto)))
                    if whole:     # an event about someone else whose payload names this member
                        seed.update(r[0] for r in self._db.execute(
                            "SELECT seq FROM nested_people WHERE person = ? AND seq <= ?", (subject, upto)))
                else:
                    for chunk in _chunks(selected):
                        marks = ','.join('?' * len(chunk))
                        seed.update(r[0] for r in self._db.execute(
                            f"SELECT seq FROM people INDEXED BY people_seq WHERE person = ?"
                            f" AND seq <= ? AND seq IN ({marks})", [subject, upto, *chunk]))
                if key or groups:
                    for candidates in self._forget_candidates:
                        seed.update(candidates(subject))
                    seed = self._matching(seed, key, groups)
                    if groups and len(seed) > MAX_TOPIC_EVENTS:
                        raise Refused("too many events match: say which part")
                dead.extend(sorted(self._closure(seed)))
                # An earlier notice erased here (a scoped one names its subject in clear) may not
                # have reached every subscriber yet: carry the ids it listed, so its duty is
                # re-delivered. Its list already holds whatever it carried, so one level is
                # transitive. Reads only the erased notices' own rows.
                carried = self._carried(dead)
                if self._before_forget is not None:
                    self._before_forget(subject, dead, whole)
                erase(None)
                batches = list(_chunks(sorted(set(dead) | carried), MAX_NOTICE_SEQS)) or [[]]
                identity = {'subject_hash': owner.hex()}
                if not whole:
                    identity['subject'] = subject
                notices.extend(Event(Kind.FORGET, KERNEL, visibility=Visibility.KERNEL,
                                     payload={**identity, "upto": upto, "count": len(dead),
                                              "scope": scope, "seqs": batch}) for batch in batches)

            def erase(_seq):
                records = set()
                fingerprints = set()
                for chunk in _chunks(dead):
                    marks = ','.join('?' * len(chunk))
                    for seq, payload in self._db.execute(
                            f"SELECT seq, payload FROM events WHERE seq IN ({marks})"
                            " AND kind IN ('grant_change','refusal')", chunk):
                        records.add(seq)
                        fingerprints.update(self._fp(t) for t in _content(json.loads(payload or '{}')))
                fingerprints.update(self._tombstone([s for s in dead if s not in records]) or ())
                self._db.executemany('INSERT OR IGNORE INTO scoped_shield VALUES (?,?)',
                                     [(owner, fp) for fp in fingerprints])
                if key:
                    self._db.execute('INSERT OR IGNORE INTO scoped_entities VALUES (?,?)',
                                     (owner, self._fp(key)))
                if records:
                    self._tombstone(sorted(records))
                if whole:
                    self._db.execute('DELETE FROM intents WHERE person=?', (subject,))
                    self._db.execute('DELETE FROM cursors WHERE reader=?', (subject,))
                    self._db.execute('DELETE FROM forget_ledger WHERE subject=?', (subject,))
                    self._anonymize_legacy_entities(subject)
                self._db.execute("INSERT INTO forget_ledger (subject, upto, ts, count) VALUES (?,?,?,?)",
                                 (owner.hex(), upto, self._clock(), len(dead)))
            try:
                self._append(notices, before=prepare)
            except BaseException as exc:
                self._shield = {r[0] for r in self._db.execute('SELECT fp FROM shield')}
                self._entities = {}
                for person, fp in self._db.execute('SELECT subject, fp FROM entity_shield'):
                    self._entities.setdefault(person, set()).add(fp)
                if isinstance(exc, Refused):
                    self._refuse(actor, "forget", str(exc))
                raise
            if whole:
                self._entities.pop(subject, None)
            self._epoch += 1
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            return ForgetResult(len(dead), upto)

    def _carried(self, dead: list[int]) -> set[int]:
        """The ids listed by the forget notices among ``dead`` (primary-key reads of those rows)."""
        carried: set[int] = set()
        for chunk in _chunks(dead):
            marks = ','.join('?' * len(chunk))
            for (raw,) in self._db.execute(
                    f"SELECT payload FROM events WHERE seq IN ({marks}) AND kind = 'forget'"
                    " AND tombstoned = 0", chunk):
                carried.update(s for s in json.loads(raw or '{}').get('seqs', ()) if type(s) is int)
        return carried

    def _matching(self, seed: set[int], key: str, groups: list) -> set[int]:
        """The seed events whose payload names the entity / holds the whole topic."""
        hit: set[int] = set()
        for chunk in _chunks(sorted(seed)):
            marks = ",".join("?" * len(chunk))
            for seq, payload in self._db.execute(
                    f"SELECT seq, payload FROM events WHERE tombstoned = 0 AND seq IN ({marks})", chunk):
                body = json.loads(payload) if payload else {}
                # A saved before-state is a copy, not this action's subject matter.
                # Scrub it separately; matching it here would erase unrelated later actions.
                if isinstance(body.get('undo'), dict):
                    body['undo'] = {'args': body['undo'].get('args', {})}
                if isinstance(body.get('proof'), dict) and 'restored' in body['proof']:
                    body['proof'] = {}
                if any(fm.names(t, key) for t in _keys_and_strings(body)) if key \
                        else fm.about(" . ".join(_strings(body)), groups):
                    hit.add(seq)
        return hit

    # ---- the entity shield: a keyed hash of a forgotten name, per person, never the name -----
    def _efp(self, person: str, key: str) -> bytes:
        return hmac.new(self._shield_key, f"entity\0{person}\0{key}".encode(), hashlib.sha256).digest()

    def _anonymize_legacy_entities(self, subject):
        # There is no safe hash conversion. A permanent fail-closed legacy
        # marker refuses named content globally rather than losing protection.
        if self._db.execute("SELECT 1 FROM entity_shield WHERE subject=? LIMIT 1",
                            (subject,)).fetchone():
            self._db.execute("INSERT OR IGNORE INTO scoped_entities VALUES (?,?)", (b'', b''))
            self._db.execute("DELETE FROM entity_shield WHERE subject=?", (subject,))

    def _names_forgotten(self, ev: Event) -> bool:
        """Does a skill's event name something a person it is about asked to forget? An event
        about nobody in particular is checked against everyone's forgotten names."""
        if self._db.execute("SELECT 1 FROM scoped_entities WHERE fp=? AND owner=?",
                            (b'', b'')).fetchone() and (ev.people or any(_keys_and_strings(ev.payload))):
            return True
        watched = list(self._entities.items())  # legacy rows fail closed, household-wide
        return self._scoped_hit('scoped_entities', ev,
                                (self._fp(run) for text in (*ev.people, *_keys_and_strings(ev.payload))
                                 for run in fm.runs(fm.words(text)))) or any(self._efp(person, run) in fps
                   for text in (*ev.people, *_keys_and_strings(ev.payload)) for run in fm.runs(fm.words(text))
                   for person, fps in watched)

    def release(self, subject: str, entity: str, *, actor: str) -> bool:
        """Fail closed until the owner defines whether deliberate relearning is allowed."""
        with self._lock:
            verdict = self.authorizer.may_forget(actor, subject)
            if not verdict.allowed:
                self._refuse(actor, "release", verdict.reason or "not theirs to release")
            self._refuse(actor, "release", "forgotten means forever")

    def _closure(self, seed: set[int]) -> set[int]:
        """The seed plus every event that derives from it, to any depth, plus the REVERSAL links both ways.

        THE RULE: after a forget, no live record holds the forgotten content: not the action that created it, not an action that
        reversed or restored it, not their snapshots, arguments, proofs or receipts. So, beyond what derives by ``caused_by``:

        * BACKWARD: a forgotten reversal takes what it reversed. An ``undo`` receipt takes the action it ``undoes``; a removal
          (its receipt's before-state marked ``reverses``, indexed in ``reversals``) takes the requests of the rows it removed, and
          so their receipts. Otherwise a rebuild would replay the earlier action and its words would come back.
        * FORWARD: a forgotten row takes the removals of it. A removal whose removed rows are ALL forgotten is erased whole, with
          its request (the words "remove X" are X's words); one that also removed rows that survive keeps its receipt (their
          history depends on it) and loses only the forgotten rows from its snapshot (``_scrub_snapshots``): its arguments and
          proof name the item, which those surviving rows still hold in their own records. An undo of an erased action derives
          from it (``caused_by``) and goes with it.
        * REQUEST: a dead action takes the utterance that asked for it (it carries the same words).
        Bounded by what is erased: indexed lookups per erased seq (``reversals_target``, ``reversals_seq``, ``causes_cause``)."""
        dead, frontier, pending = set(seed), list(seed), set()
        while frontier or pending:
            if not frontier:                                   # a removal may only become wholly forgotten later in the walk
                done = {r for r in pending if self._all_rows_dead(r, dead)}
                pending -= done
                if not done:
                    break
                frontier = [r for r in done if r not in dead]
                for r in frontier:
                    dead.add(r)
                frontier += [c for r in done for (c,) in self._db.execute("SELECT cause FROM causes WHERE seq = ?", (r,))
                             if c not in dead]
                dead.update(frontier)
                continue
            chunk, frontier = frontier[:300], frontier[300:]
            marks = ",".join("?" * len(chunk))
            more = {seq for (seq,) in self._db.execute(f"SELECT DISTINCT seq FROM causes WHERE cause IN ({marks})", chunk)}
            more |= self._reversed(chunk)
            # a dependent dies with ANY of its targets (it copies each one's content)
            more |= {seq for (seq,) in self._db.execute(f"SELECT DISTINCT seq FROM dependents WHERE target IN ({marks})", chunk)}
            pending |= {seq for (seq,) in self._db.execute(f"SELECT DISTINCT seq FROM reversals WHERE target IN ({marks})", chunk)
                        if seq not in dead}
            for seq in more:
                if seq not in dead:
                    dead.add(seq)
                    frontier.append(seq)
        return dead

    def _index_reversal(self, seq: int, subject, payload: dict) -> None:
        row = _row_source(payload)
        if row is not None:                    # a durable-row store's action over ONE row: linked to the row's creating request
            source, reverses = row
            # Every action over the row copies its WHOLE state (the proof, the before), so it depends on the create AND on every
            # earlier live action over the same row: forgetting any of them (an edit's new words) takes this one too.
            earlier = {s for (s,) in self._db.execute(
                "SELECT seq FROM dependents WHERE target = ? UNION SELECT seq FROM reversals WHERE target = ?", (source, source))}
            self._db.executemany("INSERT INTO dependents VALUES (?,?)", [(seq, t) for t in {source} | earlier])
            if reverses:                       # a cancel: forgetting it takes the row it took away (backward)
                self._db.execute("INSERT INTO reversals VALUES (?,?)", (seq, source))
            return
        targets = _reversal_targets(payload)
        if targets is None:
            targets = self._legacy_targets(seq, subject, payload)
        self._db.executemany("INSERT INTO reversals VALUES (?,?)", [(seq, t) for t in targets])

    def _legacy_targets(self, seq: int, subject, payload: dict) -> list[int]:
        """A legacy removal its own record cannot link: CONSERVATIVELY, the request of every earlier live add of the same (list,
        item) by the same person. A removal takes every copy there is, so this includes every row it took, and may include adds an
        EARLIER removal already took: forgetting this removal then also forgets those older, already-removed adds (more is erased,
        nothing is resurrected), and forgetting one of them only scrubs this removal (it still names a surviving target)."""
        want = _list_key(payload["undo"]["args"])
        out = set()
        for (raw,) in self._db.execute("SELECT payload FROM events WHERE kind = ? AND tombstoned = 0 AND subject IS ? AND seq < ?",
                                       (Kind.ACTION_RECEIPT.value, subject, seq)):
            p = json.loads(raw) if raw else {}
            args = (p.get("undo") or {}).get("args") if isinstance(p.get("undo"), dict) else None
            if p.get("action") == "list.add" and isinstance(args, dict) and _list_key(args) == want and type(args.get("asked")) is int:
                out.add(args["asked"])
        return sorted(out)

    def _all_rows_dead(self, removal: int, dead: set[int]) -> bool:
        return all(t in dead for (t,) in self._db.execute("SELECT target FROM reversals WHERE seq = ?", (removal,)))

    def _reversed(self, seqs: list[int]) -> set[int]:
        """What these (dead) receipts reversed: an undo's original; a removal's rows' requests (``reversals``). And the REQUEST of
        a dead action: the utterance it was caused by holds the same words ("add X", "take X off"), so it goes with it."""
        marks = ",".join("?" * len(seqs))
        out = {t for (t,) in self._db.execute(f"SELECT target FROM reversals WHERE seq IN ({marks})", seqs)}
        out |= {c for (c,) in self._db.execute(
            f"SELECT c.cause FROM causes c JOIN events r ON r.seq = c.seq JOIN events u ON u.seq = c.cause"
            f" WHERE c.seq IN ({marks}) AND r.kind = ? AND u.kind = ?",
            [*seqs, Kind.ACTION_RECEIPT.value, Kind.UTTERANCE.value])}
        for (raw,) in self._db.execute(f"SELECT payload FROM events WHERE kind = ? AND tombstoned = 0 AND seq IN ({marks})",
                                       [Kind.ACTION_RECEIPT.value, *seqs]):
            p = json.loads(raw) if raw else {}
            if type(p.get("undoes")) is int and p.get("action") == "undo":
                out.add(p["undoes"])
        return out

    def _tombstone(self, seqs: list[int]) -> set[bytes]:
        """Erase the rows in place, remember a keyed hash of their free text: first-hand AND
        derived (text that only ever lived in a derived event is forgotten too)."""
        fps: set[bytes] = set()
        outer = self._db.in_transaction
        if not outer:
            self._db.execute("BEGIN IMMEDIATE")
        try:
            self._scrub_snapshots(set(seqs))
            for chunk in _chunks(seqs):
                marks = ",".join("?" * len(chunk))
                for kind, payload in self._db.execute(
                        f"SELECT kind, payload FROM events WHERE seq IN ({marks})", chunk):
                    if payload:
                        body = json.loads(payload)
                        if kind == Kind.ACTION_RECEIPT.value:
                            # Copies belong to their provenance, not this action. In
                            # particular A's before-state may contain surviving B.
                            # The transactional snapshot scrub erases dead sources.
                            if isinstance(body.get('undo'), dict):
                                body['undo'] = {'args': body['undo'].get('args', {})}
                            if isinstance(body.get('proof'), dict):
                                body['proof'].pop('restored', None)
                        fps.update(self._fp(text) for text in _content(body))
                self._db.execute(
                    "UPDATE events SET tombstoned = 1, source = '', payload = NULL, mentions = NULL, subject = NULL,"
                    f" caused_by = NULL WHERE seq IN ({marks})", chunk)
                self._db.execute(f"DELETE FROM people WHERE seq IN ({marks})", chunk)
                self._db.execute(f"DELETE FROM nested_people WHERE seq IN ({marks})", chunk)
                self._db.execute(f"DELETE FROM causes WHERE seq IN ({marks})", chunk)
                self._db.execute(f"DELETE FROM reversals WHERE seq IN ({marks}) OR target IN ({marks})", chunk * 2)
                self._db.execute(f"DELETE FROM dependents WHERE seq IN ({marks}) OR target IN ({marks})", chunk * 2)
                self._db.execute(f"DELETE FROM snapshot_dependencies WHERE receipt_seq IN ({marks})", chunk)
                self._db.execute(f"DELETE FROM snapshot_dependencies WHERE source_seq IN ({marks})", chunk)
                self._db.execute(f"DELETE FROM opaque_snapshots WHERE receipt_seq IN ({marks})", chunk)
            self._scrub_intents(set(seqs))
            if not outer:
                self._db.executemany("INSERT OR IGNORE INTO shield VALUES (?)", [(fp,) for fp in fps])
            if not outer:
                self._db.execute("COMMIT")
        except BaseException:
            if not outer:
                self._db.execute("ROLLBACK")
            raise
        if not outer:
            self._shield |= fps
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # old page images leave the WAL file
        return fps

    def _scrub_snapshots(self, dead: set[int]) -> None:
        """Scrub copies in surviving receipts inside the erase transaction.

        Versioned driver snapshots carry per-row request provenance. Older/opaque snapshots
        cannot prove independence: remove their undo and restored proof for affected people.
        """
        from .receipts import scrub_snapshot
        people = set()
        for chunk in _chunks(sorted(dead)):
            marks = ','.join('?' * len(chunk))
            people.update(r[0] for r in self._db.execute(
                f"SELECT person FROM people WHERE seq IN ({marks})", chunk))
        receipts = set()
        for chunk in _chunks(sorted(dead)):
            marks = ','.join('?' * len(chunk))
            receipts.update(r[0] for r in self._db.execute(
                f"SELECT receipt_seq FROM snapshot_dependencies WHERE source_seq IN ({marks})", chunk))
        for person in people:
            receipts.update(r[0] for r in self._db.execute(
                'SELECT receipt_seq FROM opaque_snapshots WHERE person = ?', (person,)))
        for seq in sorted(receipts - dead):
            person, raw = self._db.execute(
                'SELECT subject, payload FROM events WHERE seq = ? AND tombstoned = 0', (seq,)).fetchone()
            if seq in dead or not raw:
                continue
            p = json.loads(raw)
            saved = p.get('undo')
            changed = False
            for holder, key in ((saved, 'before'), (p.get('proof'), 'restored')):
                if not holder or key not in holder:
                    continue
                before = holder[key]
                clean = scrub_snapshot(before, dead)
                if clean is None:
                    if person not in people:
                        continue
                    p.pop('undo', None)
                    if saved and 'args' in saved:
                        # Arguments belong to this surviving action, not the copied old state.
                        # Reminders also use them to restore their own surviving requests.
                        p['undo'] = {'args': saved['args']}
                    if 'restored' in (p.get('proof') or {}):
                        p['proof'] = None
                        p['verified'] = False
                        p['outcome'] = 'unverified'
                    p['reversible'] = False
                    p['undo_refusal'] = 'forget invalidated snapshot without provenance'
                    changed = True
                    break
                if clean != before:
                    holder[key] = clean
                    changed = True
            if changed:
                self._db.execute('UPDATE events SET payload = ? WHERE seq = ?',
                                 (json.dumps(p, sort_keys=True), seq))
                self._db.execute('DELETE FROM snapshot_dependencies WHERE receipt_seq = ?', (seq,))
                self._db.execute('DELETE FROM opaque_snapshots WHERE receipt_seq = ?', (seq,))
                self._index_snapshot(seq, person, p)

    def _index_snapshot(self, seq: int, person: str | None, payload: dict) -> None:
        """IDs only, in the receipt transaction; opaque text fails closed per person."""
        from .receipts import snapshot_sources
        sources, opaque = snapshot_sources(payload)
        self._db.executemany('INSERT OR IGNORE INTO snapshot_dependencies VALUES (?, ?)',
                             [(source, seq) for source in sources])
        if opaque and person is not None:
            self._db.execute('INSERT OR IGNORE INTO opaque_snapshots VALUES (?, ?)', (person, seq))

    def _backfill_snapshots(self) -> None:
        """One atomic upgrade, bounded to 256 receipt bodies in memory; never per forget."""
        if self._db.execute("SELECT 1 FROM meta WHERE key = 'snapshot_index_v1'").fetchone():
            return
        self._db.execute('BEGIN IMMEDIATE')
        try:
            cursor = self._db.execute("SELECT seq, subject, payload FROM events"
                                      " WHERE kind = 'action_receipt' AND tombstoned = 0")
            while batch := cursor.fetchmany(256):
                for seq, person, raw in batch:
                    self._index_snapshot(seq, person, json.loads(raw or '{}'))
            self._db.execute("INSERT INTO meta VALUES ('snapshot_index_v1', 1)")
            self._db.execute('COMMIT')
        except BaseException:
            self._db.execute('ROLLBACK')
            raise

    def _backfill_nested(self) -> None:
        """Index nested member ids in rows written before this index, or before a member was
        known. Once per member at open (a keyed hash marks it done, never the id), 256 bodies
        at a time; never per write or per forget. Expired and forgotten rows hold no payload."""
        missing = {m for m in self._members if not self._db.execute(
            "SELECT 1 FROM nested_members WHERE owner = ?", (self._owner(m),)).fetchone()}
        if not missing:
            return
        self._db.execute('BEGIN IMMEDIATE')
        try:
            cursor = self._db.execute("SELECT seq, kind, subject, mentions, payload FROM events"
                                      " WHERE tombstoned = 0 AND payload IS NOT NULL")
            while batch := cursor.fetchmany(256):
                for seq, kind, subject, mentions, raw in batch:
                    people = {subject, *(json.loads(mentions) if mentions else ())}
                    nested = _nested_ids(Kind(kind), json.loads(raw), people, missing)
                    self._db.executemany("INSERT OR IGNORE INTO nested_people VALUES (?,?)",
                                         [(seq, p) for p in nested])
            self._db.executemany("INSERT OR IGNORE INTO nested_members VALUES (?)",
                                 [(self._owner(m),) for m in missing])
            self._db.execute('COMMIT')
        except BaseException:
            self._db.execute('ROLLBACK')
            raise

    def _scrub_intents(self, dead: set[int]) -> None:
        """Forget reaches the action bookkeeping (``receipts.Dispatcher``): an intent tied to an
        erased event keeps no person, skill, action, grant or cause. A sequence number that is
        still LIVE stays as a bare marker (a spent yes, an undone action), so forgetting only a
        receipt never makes that yes or that undo usable again. Runs inside ``_tombstone``'s
        transaction. An intent left open by a dead kernel and tied to a forgotten event is closed
        here with no receipt: the forget wins over the later "uncertain" receipt."""
        hit: dict[int, tuple] = {}
        for chunk in _chunks(sorted(dead), 300):            # three IN lists: stay under 999 params
            marks = ",".join("?" * len(chunk))
            for row in self._db.execute(
                    f"SELECT id, accept_seq, undoes FROM intents WHERE receipt_seq IN ({marks})"
                    f" OR accept_seq IN ({marks}) OR undoes IN ({marks})", chunk * 3):
                hit[row[0]] = row
        for row_id, accept, undoes, causes in self._db.execute(
                "SELECT id, accept_seq, undoes, caused_by FROM intents WHERE receipt_seq IS NULL").fetchall():
            if dead & set(json.loads(causes or "[]")):
                hit[row_id] = (row_id, accept, undoes)
        for row_id, accept, undoes in hit.values():
            accept = accept if accept not in dead else None
            undoes = undoes if undoes not in dead else None
            if accept is None and undoes is None:
                self._db.execute("DELETE FROM intents WHERE id = ?", (row_id,))
            else:
                self._db.execute(
                    "UPDATE intents SET ts = 0, skill = '', action = '', person = '', grant_id = NULL,"
                    " caused_by = '[]', receipt_seq = 0, accept_seq = ?, undoes = ? WHERE id = ?",
                    (accept, undoes, row_id))

    def _fp(self, text: str) -> bytes:
        norm = " ".join(text.casefold().split()).encode()
        return hmac.new(self._shield_key, norm, hashlib.sha256).digest()

    def _owner(self, person: str) -> bytes:
        """The keyed hash of one EXACT person id. Never normalised: two ids that fold or
        tokenise alike are two people. Name normalisation belongs to entity forgets only."""
        return hmac.new(self._shield_key, b'owner\0' + person.encode(), hashlib.sha256).digest()

    def _payload_owners(self, payload: dict) -> set[bytes]:
        """Owners a payload names: an exact id held as a whole string or key, an exact id that is
        one word run of its text, or a known member whose name key is a run of its text (two
        members whose ids fold alike are then BOTH named: the text cannot tell them apart)."""
        strings = set(_keys_and_strings(payload))
        found = set(strings)
        run_set = {run for text in strings for run in fm.runs(fm.words(text))}
        found |= run_set
        found |= {m for m in self._members if fm.entity_key(m) in run_set}
        return {self._owner(p) for p in found}

    def _scoped_hit(self, table: str, ev: Event, fps) -> bool:
        if not self._db.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
            return False
        # Payload identities count even if a publisher omitted mentions. Household
        # and kernel visibility may reach A regardless of their declared subject.
        owners = {self._owner(p) for p in (*ev.people, ev.source)} | {b''}
        owners |= self._payload_owners(ev.payload)
        for fp in fps:
            if ev.visibility is not Visibility.PRIVATE:
                if self._db.execute(f"SELECT 1 FROM {table} WHERE fp=? LIMIT 1", (fp,)).fetchone():
                    return True
            else:
                for owner in owners:
                    if self._db.execute(f"SELECT 1 FROM {table} WHERE fp=? AND owner=?",
                                        (fp, owner)).fetchone():
                        return True
        return False

    def _shielded(self, ev: Event | dict) -> bool:
        """Does this payload carry, anywhere, a string a person asked to forget? A bare payload (no event, so no owner to scope
        by) is checked against the household-wide shield only."""
        if isinstance(ev, dict):
            return any(self._fp(s) in self._shield for s in _keys_and_strings(ev))
        return any(self._fp(s) in self._shield for s in _keys_and_strings(ev.payload)) or \
            self._scoped_hit('scoped_shield', ev,
                             (self._fp(s) for s in _keys_and_strings(ev.payload)))

    def _tombstoned(self, seqs: list[int]) -> set[int]:
        """Of these, the ones no longer live: forgotten, expired, or retired to a count."""
        marks = ",".join("?" * len(seqs))
        live = {r[0] for r in self._db.execute(
            f"SELECT seq FROM events WHERE tombstoned = 0 AND seq IN ({marks})", seqs)}
        return set(seqs) - live

    # ---- retention (zoe_os/retention.py decides WHEN; these are the two erasures) ------------
    def _expire(self, seqs: list[int]) -> None:
        """Stage 2, the skeleton: the content and the subject are erased from the row and the
        disk; the sequence number, time, kind, and the links to whom it concerned and what it
        derived from stay, so a later FORGET still finds it and everything built on it.
        Not a forget: nothing is added to the shield, the words may be said or derived again."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                for chunk in _chunks(seqs):
                    marks = ",".join("?" * len(chunk))
                    self._db.execute(
                        f"UPDATE events SET tombstoned = {EXPIRED}, payload = NULL, mentions = NULL,"
                        f" subject = NULL WHERE tombstoned = 0 AND seq IN ({marks})", chunk)
                    self._db.execute(f"DELETE FROM snapshot_dependencies WHERE receipt_seq IN ({marks})", chunk)
                    self._db.execute(f"DELETE FROM opaque_snapshots WHERE receipt_seq IN ({marks})", chunk)
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._epoch += 1                                   # live queues re-check themselves
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def _retire(self, seqs: list[int]) -> None:
        """Stage 3, the count: the rows go. An expired row adds one to its day's count for its
        kind (no person, no source); a forgotten row adds nothing."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                for chunk in _chunks(seqs):
                    marks = ",".join("?" * len(chunk))
                    self._db.execute(
                        "INSERT INTO retired_counts (day, kind, n) SELECT date(ts, 'unixepoch'), kind,"
                        f" COUNT(*) FROM events WHERE tombstoned = {EXPIRED} AND seq IN ({marks})"
                        " GROUP BY 1, 2 ON CONFLICT(day, kind) DO UPDATE SET n = n + excluded.n", chunk)
                    for table in ("events", "people", "nested_people", "causes"):
                        self._db.execute(f"DELETE FROM {table} WHERE seq IN ({marks})", chunk)
                    self._db.execute(f"DELETE FROM snapshot_dependencies WHERE receipt_seq IN ({marks})", chunk)
                    self._db.execute(f"DELETE FROM opaque_snapshots WHERE receipt_seq IN ({marks})", chunk)
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # ---- read ---------------------------------------------------------------------
    def _erased_events(self, seqs: Iterable[int]) -> tuple:
        """The events a forget is about to erase, in order: live ones in full, expired ones as a
        ``Skeleton``. Primary-key reads of these seqs only, never a history scan."""
        out: dict[int, object] = {}
        with self._lock:
            for chunk in _chunks(sorted(set(seqs))):
                marks = ','.join('?' * len(chunk))
                for row in self._db.execute(
                        f"SELECT {_COLS}, tombstoned FROM events WHERE seq IN ({marks})"
                        f" AND tombstoned IN (0, {EXPIRED})", chunk):
                    out[row[0]] = _row_to_event(row[:-1]) if row[-1] == 0 else Skeleton(row[0], Kind(row[2]))
        return tuple(out[s] for s in sorted(out))

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

    def _newest(self, kinds: frozenset[Kind], subject: str | None, batch: int = 200) -> Iterator[Event]:
        """Live rows of these kinds, NEWEST first, paged (kernel-internal). "The last X" must not
        come from a capped oldest-first read, which stops short of the newest on a long log."""
        before = self.last_seq + 1
        while True:
            sql = [f"SELECT {_COLS} FROM events WHERE seq < ? AND tombstoned = 0",
                   f"AND kind IN ({','.join('?' * len(kinds))})"]
            args: list = [before, *sorted(k.value for k in kinds)]
            if subject:
                sql.append("AND seq IN (SELECT seq FROM people WHERE person = ?)")
                args.append(subject)
            sql.append("ORDER BY seq DESC LIMIT ?")
            with self._lock:
                rows = [_row_to_event(r) for r in self._db.execute(" ".join(sql), [*args, batch])]
            yield from rows
            if len(rows) < batch:
                return
            before = rows[-1].seq

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
                # A suspended iterator owns stale objects: refresh by primary key at yield
                # time, including surviving receipts whose snapshots were scrubbed.
                ev = self._get(ev.seq)
                if ev is None:
                    continue
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
        self.cache = EventCache(lambda: self.spine.replay(self.reader, kinds=self.kinds, subject=self.subject))

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
            # Receipts can survive with a scrubbed snapshot: refresh bodies as well as
            # withdrawing tombstones, otherwise a queued copy could resurrect old bytes.
            self._queue = deque(live for e in self._queue
                                if (live := self.spine._get(e.seq)) is not None)
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
                        self.cache.append(ev)
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
