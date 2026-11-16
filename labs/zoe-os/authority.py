"""The Zoe OS kernel as the AUTHORITY for list add (stage (c), ``docs/knowledge/zoe-os-integration.md``).

    python3 labs/zoe-os/authority.py [--dir STATE_DIR]

The shadow daemon (``shadow_daemon.py``) plus three things an authority needs and a shadow did not:

* a DURABLE list driver. Its rows are REBUILT FROM THE LOG: a row exists exactly when a live, verified ``list.add`` receipt says so
  (minus the verified ``list.remove`` / ``undo`` receipts after it). The receipt is already durable, written in one transaction with
  the closing of its write-ahead intent, and already reached by every forget, so the row and its proof cannot disagree: an
  acknowledged add (the client read a verified receipt) survives a ``kill -9``; one killed before its receipt committed has no
  receipt, so it is absent after the restart (and ``recover`` gives its intent an ``uncertain`` receipt). Never half there. Chosen
  over a second table so there is ONE copy of the household's words to erase, and so nothing here touches the spine's or the
  dispatcher's transaction code;
* erase reach over the wire: ``erase_member`` (everything of the member in the record and the list store: the member-delete
  path's own ``eraser`` credential, kept in ``erase-creds.json`` apart from the surfaces' ``creds.json``, OR a surface carrying a
  ``forget.person`` speaker assertion for that member) and ``erase_item`` ("forget that" on one list item of ONE named member: a
  surface with a ``forget.scoped`` assertion for that member, or the eraser). Who may is decided by ``KernelServer._prove_speaker``
  and nowhere here. Idempotent, each answers with a receipt that is ``verified`` only when the read-back finds nothing left;
* a DURABLE roster: the kernel knows the current people.json PLUS every id its record still names, so a member taken off the roster
  is still erased by id. After a whole-person erase or forget of a member off the roster, the forget first, then the id leaves;
* ``health``: alive, schema version, whether the last write went through: what zoe-data reads to decide whether to fall back.

A revoke is remembered: at start every roster member is granted the brain's acts EXCEPT one whose newest ``brain`` grant change in
the log is a revoke (the shadow re-granted everyone on every start, which an authority must not do).

Lab only, stdlib only; never started by any script or deploy (unit template ``zoe-os-authority-kernel.service`` beside this file).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sqlite3
import sys
import threading
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent)]

from shadow_daemon import ACTS, ADAPTERS, SHADOW_BRAIN, serve, state_dir, write_creds, write_identity  # noqa: E402
from zoe_os.brain.drivers import ASKED, Alarms, Keeper, Lists, Reminders  # noqa: E402
from zoe_os.capability import Person  # noqa: E402
from zoe_os.events import Kind  # noqa: E402
from zoe_os.kernel import Kernel  # noqa: E402
from zoe_os.server import KernelServer  # noqa: E402
from zoe_os.spine import Refused  # noqa: E402

SCHEMA = 1          # the authority's own record shape (the rebuild rule below); bumped when it changes meaning
_RECEIPTS = "SELECT seq, subject, payload FROM events WHERE kind = 'action_receipt' AND tombstoned = 0"
#: the member-delete path's identity: the forget family without a speaker assertion, nothing else. No reads, no emits, no acts.
ERASER = {"skill": "eraser"}
ERASER_CREDS = "erase-creds.json"


def recorded_people(db: Path) -> set:
    """Every id the kernel's record still names (its ``people`` links: what a whole-person forget seeds from). The same rule as
    ``shadow_daemon.recorded_people``. Any error but "no database / no table yet" RAISES: never start without knowing who is held."""
    if not db.exists():
        return set()
    con = sqlite3.connect(str(db))
    try:
        return {r[0] for r in con.execute("SELECT DISTINCT person FROM people")}
    except sqlite3.OperationalError as exc:
        if "no such table" in str(exc):
            return set()
        raise
    finally:
        con.close()


def write_secret(path: Path, payload: dict) -> None:
    """A credential file the way ``write_creds`` writes one: 0600 from creation, replaced atomically. Never printed or logged."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump(payload, fh)
    os.replace(tmp, path)


def digest(item: str) -> str:
    """What the reconcile compares: never the words. zoe-data computes the same over its own stored text."""
    return hashlib.sha256(item.strip().lower().encode()).hexdigest()[:16]


class DurableLists(Lists):
    """``Lists`` whose rows are a function of the log. Snapshots are LEAN (nothing for an add, the removed rows for a remove): a receipt
    never carries the person's whole list, so an add costs the same on the hundredth item as on the first. Both are versioned
    (``provenance: 1``, every row with its ``asked``): a forget then scrubs only the rows it reaches (``receipts.scrub_snapshot``) and
    the surviving action stays undoable; an unmarked snapshot would be treated as opaque and lose its undo on ANY forget of the
    person's."""

    def __init__(self, spine) -> None:
        super().__init__()
        self.spine = spine

    @staticmethod
    def key(row_or_args) -> tuple:
        return row_or_args.get("list", "shopping"), row_or_args["item"]

    def snapshot(self, person, args):                       # list.add: its undo takes back only its own row, so nothing is copied
        return {"provenance": 1, "rows": []}

    def put_back(self, person, args, before):               # undo of list.add: the row this add appended goes
        rows = self.rows.get(person, [])
        mine = [r for r in rows if self.key(r) == self.key(args) and r["asked"] == args["asked"]]
        if mine:
            rows.remove(mine[-1])

    def removed_snapshot(self, person, args):               # list.remove: the rows it is about to take
        return {"provenance": 1, "rows": [dict(r) for r in self.rows.get(person, []) if self.key(r) == self.key(args)]}

    def put_back_removed(self, person, args, before):
        self.rows.setdefault(person, []).extend(dict(r) for r in before["rows"])

    def remove(self, person, args):
        rows = self.rows.get(person, [])
        keep = [r for r in rows if self.key(r) != self.key(args)]
        self.rows[person] = keep
        return len(rows) - len(keep)

    def removed(self, person, args, result):
        gone = all(self.key(r) != self.key(args) for r in self.rows.get(person, []))
        return {"item": args["item"], "removed": int(result or 0)} if gone else None

    def register(self, kernel) -> "DurableLists":
        super().register(kernel)
        kernel.actions.register(
            "list.remove", self.remove, self.removed, needs_accept=False, snapshot=self.removed_snapshot,
            undo=self.put_back_removed, doc="Remove an item from one of the person's lists",
            params={"item": {"type": "string", "required": True, "description": "what to remove"},
                    "list": {"type": "string", "required": False, "description": "which list; the shopping list if not said"},
                    "asked": ASKED})
        return self

    # ---- the rebuild: the rows ARE the live verified receipts, in order ----------------------------------------------------------
    def _apply(self, payload: dict, original) -> None:
        action, person = payload.get("action"), payload["_subject"]
        if action in ("list.add", "list.remove") and payload.get("outcome") == "verified" and "undo" in payload:
            args = payload["undo"]["args"]
            if action == "list.add":
                self.add(person, args)
            else:
                self.remove(person, args)
        elif action == "undo" and payload.get("outcome") == "verified" and original is not None:
            saved = original.get("undo") or {}
            if original.get("action") == "list.add" and "args" in saved:
                self.put_back(person, saved["args"], saved.get("before"))
            elif original.get("action") == "list.remove" and "before" in saved:
                self.put_back_removed(person, saved["args"], saved["before"])

    def rebuild(self, person: str | None = None) -> int:
        """Rows from the log: everyone at start, one person after a forget. Returns the number of rows."""
        db = self.spine._db
        sql, args = (_RECEIPTS + " AND subject = ? ORDER BY seq", (person,)) if person else (_RECEIPTS + " ORDER BY seq", ())
        if person:
            self.rows.pop(person, None)
        else:
            self.rows.clear()
        live: dict[int, dict] = {}
        for seq, subject, raw in db.execute(sql, args).fetchall():
            payload = json.loads(raw) if raw else {}
            live[seq] = payload
            if payload.get("action") not in ("list.add", "list.remove", "undo") or subject is None:
                continue
            original = live.get(payload.get("undoes")) if payload.get("action") == "undo" else None
            self._apply({**payload, "_subject": subject}, original)
        for who in [p for p, rows in self.rows.items() if not rows]:
            del self.rows[who]
        return sum(len(r) for r in self.rows.values())


class DurableKeeper(Keeper):
    """On a forget the list rows are REBUILT from what the log still holds (a forget by named events carries no scope, and the lab
    keeper reads that as "everything": the rebuild is right for every scope). Reminders and alarms keep the lab keeper's rule."""

    def __init__(self, lists: DurableLists, *others) -> None:
        super().__init__(*others)
        self.lists = lists

    def on_event(self, ev, ctx) -> None:
        super().on_event(ev, ctx)
        if ev.kind is Kind.FORGET and ev.payload.get("subject"):
            self.lists.rebuild(ev.payload["subject"])


class AuthorityServer(KernelServer):
    """The kernel server plus the authority's ops (``wire.OPS``: ``health``, ``erase_member``, ``erase_item``, ``list_digest``)."""

    def __init__(self, kernel, path: str, lists: DurableLists, others=(), *, roster=(), **kw) -> None:
        super().__init__(kernel, path, **kw)
        self.lists, self.others = lists, tuple(others)
        self.roster = frozenset(roster)
        self.last_write_ok: bool | None = None     # None: no write since start
        self.roster_status = "ok"                  # set by ``build`` from ``read_roster``

    def _held(self, person: str) -> bool:
        """Does the record still name ``person`` (any people link)? Read from the database every time, never assumed."""
        return self.kernel.spine._db.execute("SELECT 1 FROM people WHERE person = ? LIMIT 1", (person,)).fetchone() is not None

    def _known_for_forget(self, person: str) -> bool:
        """Make sure the kernel can forget ``person`` if the record names them: an id off the roster that the record still holds is
        (re)admitted as a person, never granted. False = never seen and nothing held: there is nothing to forget."""
        people = self.kernel.grants.people
        if person in people:
            return True
        if self._held(person):
            people[person] = Person(person)
            return True
        return False

    def _left_the_roster(self, person: str) -> None:
        """After a whole-person forget SUCCEEDED (forget first, then the roster): a member off the roster leaves the kernel's people,
        so nothing new is recorded about them and a later forget is honestly "never knew them"."""
        if person not in self.roster and not self._held(person):
            self.kernel.grants.people.pop(person, None)

    def _op_forget(self, conn, a: dict) -> dict:
        out = super()._op_forget(conn, a)
        self.kernel.pump()                         # the keepers erase the stores' copies before the reply
        if a.get("seqs") is None and a.get("entity") is None and a.get("topic") is None:
            self._left_the_roster(a["subject"])
        return out

    def _op_act(self, conn, a: dict) -> dict:
        try:
            out = super()._op_act(conn, a)
        except Refused:
            self.last_write_ok = True              # the refusal was recorded: the write path works
            raise
        except Exception:
            self.last_write_ok = False
            raise
        self.last_write_ok = out.get("outcome") == "verified"
        return out

    def _op_health(self, conn, a: dict) -> dict:
        """Alive, the schema, and whether a write goes through RIGHT NOW (a no-op write in its own transaction): an unhealthy answer
        is the fallback signal, and it can never stick, because every health call writes again."""
        db = self.kernel.spine._db
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE meta SET value = value WHERE key = 'authority_schema'")
            db.execute("COMMIT")
            self.last_write_ok = True
        except Exception:  # noqa: BLE001 - a locked or read-only database is an unhealthy kernel, not a crashed one
            if db.in_transaction:
                db.execute("ROLLBACK")
            self.last_write_ok = False
        return {"alive": True, "schema": SCHEMA, "last_write_ok": self.last_write_ok,
                "recovered": len(self.kernel.recovered), "rows": sum(len(r) for r in self.lists.rows.values()),
                "roster": self.roster_status}

    def _receipts_for(self, person: str, item_digest: str) -> list[int]:
        """The person's live list receipts for the item (by digest), and the utterances they cite (the words go with them)."""
        db, seqs = self.kernel.spine._db, []
        for seq, _subject, raw in db.execute(_RECEIPTS + " AND subject = ?", (person,)).fetchall():
            p = json.loads(raw) if raw else {}
            args = (p.get("undo") or {}).get("args") or {}
            if p.get("action") in ("list.add", "list.remove") and digest(str(args.get("item", ""))) == item_digest:
                seqs.append(seq)
                seqs.extend(json.loads(db.execute("SELECT COALESCE(caused_by, '[]') FROM events WHERE seq = ?", (seq,)).fetchone()[0]))
        return seqs

    def _live_events(self, person: str) -> int:
        return self.kernel.spine._db.execute(
            "SELECT COUNT(*) FROM people p JOIN events e ON e.seq = p.seq WHERE p.person = ? AND e.tombstoned = 0",
            (person,)).fetchone()[0]

    def _op_erase_member(self, conn, a: dict) -> dict:
        """Everything of the member: their events (and what derives from them), their list rows, their reminder and alarm rows, and
        their brain grant (revoked, so a restart does not re-grant them). ``verified`` only when the read-back finds nothing.
        Forgets BY ID whatever the current roster says. The member-delete path's ``eraser`` credential, or a surface with the
        member's own ``forget.person`` assertion; a surface alone is refused ``speaker unproven`` (``_prove_speaker``)."""
        subject = a["subject"]
        if not self._known_for_forget(subject):                              # checked in the record: never seen, nothing held
            left = len(self.lists.rows.get(subject, [])) + self._live_events(subject)
            return {"erased": 0, "receipt_seq": None, "verified": left == 0, "known": False}
        count = self.kernel.spine.forget(subject, actor=subject).count
        notice = self.kernel.spine.last_seq                                  # the FORGET event: the erase's durable receipt
        self.kernel.grants.revoke(subject, SHADOW_BRAIN["skill"])
        self.lists.rebuild(subject)
        for store in self.others:                                            # the lab reminder / alarm stores, in memory
            (store.at if isinstance(store, Alarms) else store.rows).pop(subject, None)
        left = len(self.lists.rows.get(subject, [])) + self._live_events(subject)
        if left == 0:
            self._left_the_roster(subject)                                   # forget first, then the roster
        return {"erased": count, "receipt_seq": notice, "verified": left == 0, "known": True}

    def _op_erase_item(self, conn, a: dict) -> dict:
        """"Forget that" on one list item of ONE member, by DIGEST (no words cross the wire for an erase). ``subject`` is required:
        a request without it is refused (``name whose item``), so one person's request never changes another member's list."""
        subject, want = a.get("subject"), a["digest"]
        if not subject:
            self.kernel.spine._refuse(conn.name, "erase a list item", "name whose item")
        erased, notice = 0, None
        if self._known_for_forget(subject):
            seqs = self._receipts_for(subject, want)
            if seqs:
                erased = self.kernel.spine.forget(subject, actor=subject, seqs=seqs).count
                notice = self.kernel.spine.last_seq
            self.lists.rebuild(subject)
        left = sum(1 for r in self.lists.rows.get(subject, []) if digest(r["item"]) == want) + len(self._receipts_for(subject, want))
        return {"erased": erased, "receipt_seq": notice, "verified": left == 0}

    def _op_list_digest(self, conn, a: dict) -> dict:
        self._person_op(conn, "read the list digest")
        return {"digests": sorted(digest(r["item"]) for rows in self.lists.rows.values() for r in rows)}


def revoked(k: Kernel, skill: str) -> set[str]:
    """Members whose newest grant change for ``skill`` in the log is a revoke."""
    last: dict[str, str] = {}
    for (raw,) in k.spine._db.execute("SELECT payload FROM events WHERE kind = 'grant_change' AND tombstoned = 0 ORDER BY seq"):
        p = json.loads(raw) if raw else {}
        if p.get("skill") == skill and p.get("person"):
            last[p["person"]] = p.get("change", "")
    return {who for who, change in last.items() if change == "revoked"}


#: the PROPOSED permanent home of the kernel's own database (owner decision 2026-11-15: its own file, beside Zoe's other stores, in the
#: nightly backup). The unit template passes it as ``--db``; without ``--db`` the lab default is ``STATE/kernel/zoe-os.db``.
PERMANENT_DB = "~/.zoe/zoe-os/zoe-os.db"


def _sidecars(db: Path) -> list:
    return [db.with_name(db.name + suffix) for suffix in ("-wal", "-shm", "-journal")]


def _fsync_dir(d: Path) -> None:
    fd = os.open(d, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fingerprint(db: Path) -> dict:
    """What "the same record" means for the move: per table (and the schema), the row count and a digest of every row, compared
    as a SORTED multiset so a copy that renumbers implicit rowids (VACUUM INTO) still matches, and a lost, added or changed row
    never does. A plain connection, so a WAL left by a killed kernel is read too (closing it may fold that WAL in: no row changes)."""
    con = sqlite3.connect(str(db))
    try:
        out = {"schema": sorted(con.execute("SELECT type, name, sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_autoindex%'"))}
        for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall():
            rows = sorted(hashlib.sha256(repr(r).encode()).digest() for r in con.execute(f'SELECT * FROM "{table}"'))
            out[table] = (len(rows), hashlib.sha256(b"".join(rows)).hexdigest())
        return out
    finally:
        con.close()


def migrate(old: Path, new: Path) -> None:
    """Move the record from the lab location to ``--db`` and FINISH a move a crash interrupted. Every step is safe to interrupt;
    the next start resumes from what is on disk:

    * nothing moved yet (old only)                 -> copy, verify, remove the old
    * killed mid copy (old + ``.moving`` temp)     -> the temp is thrown away, copy again
    * killed after the rename (old + new)          -> verify the new against the old, then remove the old
    * killed during the removal (old's sidecars)   -> the sidecars are removed
    Verified = ``integrity_check`` ok AND ``fingerprint`` equal. A mismatch REFUSES the start with both files kept: no kernel ever
    runs on the new file while the old one exists, so a mismatch is damage, never newer data."""
    tmp = new.with_name(new.name + ".moving")
    if not old.exists():
        for f in (*_sidecars(old), tmp, *_sidecars(tmp)):
            f.unlink(missing_ok=True)
        return
    new.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    con = sqlite3.connect(str(old))
    try:
        con.execute("PRAGMA journal_mode=DELETE")              # the old record as ONE file: its WAL folded in and removed
    finally:
        con.close()
    if not new.exists():
        for f in (tmp, *_sidecars(tmp)):
            f.unlink(missing_ok=True)
        con = sqlite3.connect(str(old))
        try:
            con.execute("VACUUM INTO ?", (str(tmp),))          # a compact copy: no free pages carried over
        finally:
            con.close()
        os.chmod(tmp, 0o600)
        fd = os.open(tmp, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp, new)
        _fsync_dir(new.parent)
    check = sqlite3.connect(f"file:{new}?mode=ro", uri=True)
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        check.close()
    if not ok or fingerprint(new) != fingerprint(old):
        raise SystemExit("the kernel database move could not be verified: both copies kept, nothing started (see README)")
    _remove_copy(old)                                          # only now: the old copy is outside every future forget


def _remove_copy(db: Path) -> None:
    """The main file first, then its sidecars: a crash in between leaves only sidecars, which the next start removes."""
    for f in (db, *_sidecars(db)):
        f.unlink(missing_ok=True)
    _fsync_dir(db.parent)


def db_path(state: Path, db: str | None) -> Path:
    """The database to open. With ``--db`` the record at the OLD lab location is moved there first (``migrate``): a kernel never
    starts beside a second copy of the record, which no forget would reach."""
    old = state / "kernel" / "zoe-os.db"
    if not db:
        return old
    path = Path(db).expanduser()
    if path.resolve() != old.resolve():
        migrate(old, path)
    return path


def read_roster(state: Path) -> tuple[list, str]:
    """The CURRENT roster and how it read: ``ok``, ``empty`` or ``unreadable``. Forget never depends on it: the kernel loads every
    id its record names whatever this says, and only roster members are granted anything.

    * MISSING people.json: an operator error (the owner never exported a roster): refuse to start;
    * EMPTY (``{"people": []}``, the last member removed): a valid household state. Nobody can be proposed for or acted for; the
      recorded people can still be forgotten and erased;
    * UNREADABLE (not JSON, wrong shape, a non-string id): treated as EMPTY for grants (nothing is granted on a roster nobody can
      read), and forget of the recorded people is still served. The daemon says so on stderr (a fixed line) and ``health``
      reports ``roster: unreadable``."""
    path = state / "people.json"
    if not path.exists():
        raise SystemExit("people.json is missing: export the household roster first (see README)")
    try:
        people = json.loads(path.read_text("utf-8"))["people"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        people = None
    if not isinstance(people, list) or not all(isinstance(p, str) and p for p in people):
        print("zoe-os authority kernel: people.json is unreadable: nobody is granted anything; forget of recorded people is served",
              file=sys.stderr, flush=True)
        return [], "unreadable"
    return people, "ok" if people else "empty"


def build(state: Path, db: str | None = None):
    people, roster_status = read_roster(state)
    path = db_path(state, db)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # the roster PLUS every id the record still names: a member taken off people.json is still erased by id (never granted)
    k = Kernel(str(path), [Person(p) for p in sorted(set(people) | recorded_people(path))])
    k.spine._db.execute("INSERT OR REPLACE INTO meta VALUES ('authority_schema', ?)", (str(SCHEMA).encode(),))
    lists = DurableLists(k.spine).register(k)
    lists.rebuild()
    reminders, alarms = Reminders(lambda: k.clock()).register(k), Alarms().register(k)
    for manifest in (*ADAPTERS, SHADOW_BRAIN, ERASER):
        k.grants.install(manifest)
    k.install(DurableKeeper(lists, reminders, alarms))
    no = revoked(k, SHADOW_BRAIN["skill"])
    for person in people:
        if person not in no:
            k.grants.grant(person, SHADOW_BRAIN["skill"], acts=list(ACTS))
    srv = AuthorityServer(k, str(state / "k.sock"), lists, (reminders, alarms), roster=people)
    srv.roster_status = roster_status
    creds = {"voice": srv.issue("voice"), "brain": srv.issue("brain"), "panel": srv.issue("panel", surface=True)}
    write_secret(state / ERASER_CREDS, {"socket": srv.path, "creds": {ERASER["skill"]: srv.issue(ERASER["skill"], eraser=True).hex()}})
    return k, srv, creds


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", help="state directory (default $OSLAB_SHADOW_DIR or ~/.zoe/os-shadow)")
    ap.add_argument("--db", help=f"the kernel's database file (the unit passes {PERMANENT_DB}); default STATE/kernel/zoe-os.db")
    opts = ap.parse_args(argv)
    state = state_dir(opts.dir)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.umask(0o077)
    k, srv, creds = build(state, opts.db)
    write_creds(state, srv.path, creds)
    write_identity(state, srv)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    print(f"zoe-os authority kernel up: {len(k.grants.people)} members, {sum(len(r) for r in srv.lists.rows.values())} list rows, "
          f"{len(k.recovered)} intents recovered as uncertain", flush=True)
    serve(k, srv, stop)
    srv.shutdown()
    k.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
