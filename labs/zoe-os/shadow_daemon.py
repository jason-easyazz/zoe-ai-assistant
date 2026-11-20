"""The Zoe OS kernel as a long-running SHADOW process: the one the zoe-data shadow client (``os_shadow.py``) talks to.

    python3 labs/zoe-os/shadow_daemon.py [--dir STATE_DIR]

Operator opt-in (``scripts/setup/systemd/zoe-os-shadow-kernel.service``); never started by any script or deploy. It is NOT
authoritative: zoe-data's own list add stays the only writer of the household's list, and this kernel records the event and a
receipt beside it, in its OWN store. Read ``docs/knowledge/zoe-os-list-shadow.md`` first.

State directory (``--dir``, else ``$OSLAB_SHADOW_DIR`` (a lab process reads no ``ZOE_`` name), else ``~/.zoe/os-shadow``; mode 0700, never inside a repo checkout):

    people.json   {"people": [entries]}   REQUIRED (an entry: {"id", "role": "adult"|"child"|"guest", "guardians": [ids]}; a
                  plain id, a missing or unknown role, or anything the loader cannot fully read is LEAST access: say "adult"), written by zoe-data (``os_shadow.py``); read at start. Missing:
                  the start is refused. EMPTY (the last member removed) or unreadable: the daemon starts, nobody is granted
                  anything, and the recorded people can still be forgotten (``read_roster``). Read AGAIN, with no restart,
                  whenever the file itself changes (``ShadowServer.sync_roster``, looked at before each ``list_ask``, ``publish`` and ``act``)
    known-people.json  the roster plus every id the kernel still holds a record for: who it can still FORGET. Derived from the
                  kernel's own record at each start, and rewritten (atomic, mode kept) after a whole-person forget succeeds
    creds.json    {"socket": path, "creds": {"voice": hex, "brain": hex, "panel": hex}}   written here, 0600, on every start
    k.sock        the Unix socket (0600)
    kernel/       the kernel's SQLite spine (events, receipts, intents), 0600

The verbs it serves: ``list.add``, ``list.remove`` (registered HERE, over the same list rows: the lab drivers have no remove),
``remind.set`` and ``alarm.set`` (the lab drivers, ``zoe_os/brain/drivers.py``). Which of them zoe-data proposes is its own
``ZOE_OS_SHADOW_VERBS``; the kernel serves all four.

It ALSO serves the door (``zoe_os/door.py``): there the kernel DECIDES a change by the roster's roles and records ids only; the
thing changed stays in production's store. Two areas: lists (``list_ask`` / ``list_report``, ``zoe_os/lists.py``) and reminders
(``door_ask`` / ``door_report`` with ``area="reminders"``, ``zoe_os/remind_door.py``). Same socket, same credentials (the
``brain`` one).

What it grants itself, and why that is honest only in shadow: every roster member gets a RECORD-ONLY grant of the ``brain``
skill's four acts at start (``GrantStore.grant(record_only=True)``, refused anywhere but a store marked ``shadow``), with no consent
ceremony, because a shadow records what the household already did, whatever the member's role: a least-access member's adds are
still recorded for comparison. It is not consent and must never be reused as the consent model for an authoritative kernel.
Roles come from the roster as stated; an entry with no stated or an unknown role is LEAST access (``capability.LEAST``), never adult.
A member taken off the roster stays known (forgettable, never granted) while the kernel holds a record for them, and no longer: once a
whole-person forget of theirs succeeds their id leaves ``known-people.json`` and the kernel's people. A forget does not shield the
PERSON: a member still on the roster who acts again is recorded afresh, and that record makes them forgettable again.

Stdlib only. The list rows live in memory (the lab ``Lists`` driver); the durable record is the spine.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sqlite3
import stat
import sys
import threading
import time
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent)]

from zoe_os.brain.drivers import ASKED, BRAIN, Alarms, Keeper, Lists, Reminders  # noqa: E402
from zoe_os.capability import Person, person_from_entry  # noqa: E402
from zoe_os.kernel import Kernel  # noqa: E402
from zoe_os.lists import VERBS as LIST_DOOR_VERBS  # noqa: E402
from zoe_os.remind_door import VERBS as REMINDER_DOOR_VERBS  # noqa: E402
from zoe_os.server import KernelServer  # noqa: E402
from zoe_os import shadow_state  # noqa: E402

# trusted origin adapters: the voice surface records the utterance that is the cause of an act; the panel is the surface that
# relays a person's forget. Same manifests the lab tests use (tests/world.py ADAPTERS).
ADAPTERS = ({"skill": "voice", "emits": ["utterance"], "origin": True},
            {"skill": "panel", "emits": ["offer_response"], "origin": True})
PUMP_S = 0.5
#: the brain manifest's ceiling plus the shadow-only ``list.remove``
ACTS = (*BRAIN["acts"], "list.remove")       # the acts every roster member is given record-only, and the brain may propose
#: the manifest also names the door's verbs, every area's (``zoe_os/lists.py``, ``zoe_os/remind_door.py``): a ceiling for
#: ``list_ask`` / ``door_ask``, decided by role, granted to nobody
SHADOW_BRAIN = {**BRAIN, "acts": [*ACTS, *LIST_DOOR_VERBS, *REMINDER_DOOR_VERBS]}


def register_list_remove(kernel, lists: Lists) -> None:
    """``list.remove`` over the lab list driver's rows. Its post-condition is "that item is not on that list": every matching row
    goes, and the read-back verifies the absence (an item the shadow never saw added is verified too: the household's list is
    the authority, and it no longer has the item)."""
    def key(args):
        return args.get("list", "shopping"), args["item"]

    def remove(person, args):
        rows = lists.rows.get(person, [])
        keep = [r for r in rows if (r["list"], r["item"]) != key(args)]
        lists.rows[person] = keep
        return len(rows) - len(keep)

    def removed(person, args, result):
        gone = all((r["list"], r["item"]) != key(args) for r in lists.rows.get(person, []))
        return {"item": args["item"], "removed": int(result or 0)} if gone else None

    kernel.actions.register(
        "list.remove", remove, removed, needs_accept=False, snapshot=lists.snapshot, undo=lists.put_back,
        doc="Remove an item from one of the person's lists",
        params={"item": {"type": "string", "required": True, "description": "what to remove"},
                "list": {"type": "string", "required": False, "description": "which list; the shopping list if not said"},
                "asked": ASKED})


def state_dir(arg: str | None) -> Path:
    return Path(arg or os.environ.get("OSLAB_SHADOW_DIR") or Path.home() / ".zoe" / "os-shadow")


KNOWN = "known-people.json"


#: identities that are never people: the daemon's own credentials, the kernel, an unverified peer. The kernel's record links them in
#: its bookkeeping (a refusal names ``who`` asked; a grant change names the skill ``with``), and they must not become "people".
IDENTITIES = frozenset({"kernel", "unverified-peer", "voice", "panel", "brain", "presence", "routines", "calendar", "audit",
                        "eraser", "reconciler"})


def recorded_people(db: Path, identities=IDENTITIES) -> set:
    """Every MEMBER id the kernel still holds a live record for: what a whole-person forget seeds from (its ``people`` links, and
    ``nested_people`` where the kernel has that table: an id named inside another record). Not a FORGET notice (bookkeeping about a
    forget, not a record of the person: a forgotten member must leave), and never an identity (``identities``). Any error but "no
    database / no table yet" RAISES: the daemon refuses to start rather than lose who it can still forget."""
    if not db.exists():
        return set()
    con = sqlite3.connect(str(db))
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        if "people" not in tables:
            return set()
        found = set()
        for table in ("people", "nested_people"):
            if table in tables:
                found |= {r[0] for r in con.execute(
                    f"SELECT DISTINCT p.person FROM {table} p JOIN events e ON e.seq = p.seq"
                    " WHERE e.tombstoned = 0 AND e.kind != 'forget'")}
        return found - set(identities)
    finally:
        con.close()


def write_known(state: Path, ids) -> list:
    """``known-people.json``, atomic and durable: a unique temp file in the same directory, fsync, rename, fsync of the directory.
    The file keeps its mode (0600 when new); a failed write leaves the old file whole and no temp file behind."""
    path = state / KNOWN
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except FileNotFoundError:
        mode = 0o600
    known = sorted(set(ids))
    tmp = state / f".{KNOWN}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode), "w") as fh:
            os.fchmod(fh.fileno(), mode)                          # the umask must not change it
            json.dump({"people": known}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    dfd = os.open(state, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return known


def remember_people(state: Path, people: list) -> list:
    """The roster plus everyone the kernel still holds a record for, written BEFORE the kernel serves. Derived, never accumulated:
    an id left in the file by a crash between a forget and its rewrite is dropped here. Temp files a killed writer left (they can
    hold ids) are removed first."""
    for stale in (*state.glob(f".{KNOWN}.*.tmp"), state / f"{KNOWN}.tmp"):
        stale.unlink(missing_ok=True)
    return write_known(state, set(people) | recorded_people(state / "kernel" / "zoe-os.db"))


class ShadowServer(KernelServer):
    """The kernel's server, plus the one copy of member ids the daemon keeps outside the kernel (``known-people.json``).

    Order: the kernel's forget FIRST, then the copy. A refused or failed forget raises before anything here runs, so the id stays
    for the retry (dropping it first would turn the retry into "never recorded" while the record still exists). If the rewrite
    fails after the kernel forgot, the forget answers ``internal_error`` and the client retries it (a no-op in the kernel, the
    rewrite again); a crash in between is repaired at the next start, which derives the file from the kernel's record."""

    def __init__(self, kernel: Kernel, path: str, *, state: Path, roster, seen=None) -> None:
        super().__init__(kernel, path)
        self.state, self.roster = state, frozenset(roster)
        self.forgotten: set = set()      # roster members whose whole record (their record-only grant with it) was forgotten since the start
        self.roster_seen = seen          # which people.json the roster was read from (``roster_identity``)

    def _op_act(self, conn, a: dict) -> dict:
        self.record_again(a.get("on_behalf_of"))
        return super()._op_act(conn, a)

    # The roster is re-read ONLY where the kernel DECIDES (the door: ``list_ask`` / ``door_ask`` and their reports). The watch-only ops (publish, act) keep the roster
    # as loaded, exactly as before the door existed: with lists off nothing here costs a stat or follows a file edit.
    def _op_list_ask(self, conn, a: dict) -> dict:
        self.sync_roster()
        return super()._op_list_ask(conn, a)

    def _op_list_report(self, conn, a: dict) -> dict:
        self.sync_roster()
        return super()._op_list_report(conn, a)

    def _op_door_ask(self, conn, a: dict) -> dict:
        self.sync_roster()
        return super()._op_door_ask(conn, a)

    def _op_door_report(self, conn, a: dict) -> dict:
        self.sync_roster()
        return super()._op_door_report(conn, a)

    def sync_roster(self) -> str:
        """The roster follows ``people.json`` with no restart. Called under the kernel lock (``KernelServer._answer``) before an
        ask, a publish or an act is decided: one ``stat``; the file is read only when it is a different file than the one last read
        (``roster_identity``). Answers ``same`` | ``kept`` | ``changed``.

        A file that is missing or cannot be read changes NOTHING (``kept``): the roster in force was stated by a file that could
        be read, a damaged one states nothing, and it is looked at again only once it changes. A readable one is applied
        exactly as a start would apply it (``apply_roster``), so a restart and a re-read never disagree."""
        seen = roster_identity(self.state)
        if seen == self.roster_seen:
            return "same"
        entries = load_roster(self.state)
        if entries is None:
            self.roster_seen = seen
            return "kept"
        self.apply_roster(entries)
        self.roster_seen = seen          # last: a re-read that failed half way is tried again at the next ask
        return "changed"

    def apply_roster(self, entries: list) -> None:
        """Make the kernel's people what ``build`` would make them from these entries, in place (the kernel's parts share the
        one ``people`` dict).

        * a NEW member is a person with the role STATED, and gets the record-only grant every member has from the start;
        * a role that changed is the role now stated. ``person_from_entry`` is the only reader, here as at the start: an entry
          that does not fully say otherwise is least access, whatever it claims;
        * a member taken OFF the file loses their record-only grant (revoked: a grant change in the record, which a forget of
          theirs reaches) and their role (least access). They stay a person the kernel knows while it holds a record for them, so
          they can still be forgotten, and no longer; one it holds nothing for is no person here at all (``unknown_person``);
        * a member whose whole record was forgotten and who has not acted since is NOT granted here: nothing names them until
          their next act (``record_again``)."""
        k, people = self.kernel, self.kernel.grants.people
        ids, members = set(roster_ids(entries)), {p.id: p for p in roster_people(entries)}
        for gone in sorted(self.roster - ids):
            if k.grants.of("brain", gone) is not None:   # only a grant that exists: a forgotten member has none, and a revoke
                k.grants.revoke(gone, "brain")           # written for them would name them again
        known = write_known(self.state, ids | recorded_people(self.state / "kernel" / "zoe-os.db"))
        want = {**{p: Person(p) for p in known if p not in members}, **members}
        added = set(want) - set(people)
        was, was_members = dict(people), k.spine._members
        people.clear()
        people.update(want)
        if added:                        # ids a payload can name: the spine indexes the new members' (rows already written too)
            try:
                k.spine._members = frozenset(people)
                k.spine._backfill_nested()
            except BaseException:
                # the backfill's transaction rolled back: put memory back too, or the next re-read would find nobody "added",
                # skip the backfill for good, and leave the new member's older records outside their forget
                people.clear()
                people.update(was)
                k.spine._members = was_members
                raise
        self.roster = frozenset(ids)
        # ``forgotten`` is NOT trimmed to the roster: a forgotten member taken off the file and put back must still not be
        # granted (nothing names them until they act again; only ``record_again`` clears the mark)
        for person in sorted(ids - self.forgotten):
            if k.grants.of("brain", person) is None:
                k.grants.grant(person, "brain", acts=list(ACTS), record_only=True)

    def record_again(self, person) -> None:
        """A roster member whose whole record was forgotten acts again: forgetting a member's data must not lock them out. The
        forget erased their record-only grant along with everything else that names them (rightly: nothing may name them after
        it), and until now only the next START gave it back, so every later act of theirs was refused "no grant from that
        person". It is installed again HERE, at their next act and not before: between the forget and that act nothing in the
        record names them. The same grant ``build`` gives every roster member; never consent, and only ever in a shadow store."""
        if person in self.forgotten and person in self.roster and person in self.kernel.grants.people:
            self.forgotten.discard(person)
            if self.kernel.grants.of("brain", person) is None:
                self.kernel.grants.grant(person, "brain", acts=list(ACTS), record_only=True)

    def _op_forget(self, conn, a: dict) -> dict:
        out = super()._op_forget(conn, a)                         # under the kernel lock (KernelServer._answer)
        # the Keeper erases the driver rows (in memory) NOW, before the reply: left to the next pump tick, an act in between would
        # snapshot a forgotten row into its receipt (refused as forgotten arguments) and the rows would outlive a confirmed forget
        self.kernel.pump()
        if a.get("seqs") is None and a.get("entity") is None and a.get("topic") is None:
            self.forgot(a["subject"])
        return out

    def forgot(self, subject: str) -> None:
        """A whole-person forget of ``subject`` succeeded: they hold no record now. A member still on the roster stays (they are a
        member, and people.json names them); one off the roster leaves the file, then the kernel's people (so a later forget is
        the kernel's own "never knew them", and nothing new can be recorded about them)."""
        people = self.kernel.grants.people
        # marked whether or not they are on the roster NOW: a member taken off the file, then forgotten, then put back must not
        # be granted (and so named) by the re-read. Only their own next act clears the mark (record_again).
        self.forgotten.add(subject)
        if subject in self.roster or subject not in people:
            return
        write_known(self.state, (p for p in people if p != subject))
        people.pop(subject, None)


def entry_id(p) -> str:
    """A roster entry's member id: a plain id, or the ``id`` of an object entry; '' when it has none (the roster is unreadable)."""
    if isinstance(p, str):
        return p
    return p["id"] if isinstance(p, dict) and isinstance(p.get("id"), str) else ""


def roster_ids(entries: list) -> list:
    return [entry_id(p) for p in entries]


def roster_people(entries: list) -> list:
    """Persons from roster entries, roles as STATED. Least access whenever the entry does not fully say otherwise: a plain id, a
    missing or unknown role, an unknown key, or guardians that are not a list of ids give ``Person(id)`` (``capability.LEAST``)."""
    return [person_from_entry(p) for p in entries]


def roster_identity(state: Path):
    """WHICH file people.json is now: its inode, size and modification time (a rewrite replaces the file, so the inode moves
    even inside one clock tick). None when it is not there. Never its contents."""
    try:
        st = os.stat(state / "people.json")
    except OSError:
        return None
    return st.st_ino, st.st_size, st.st_mtime_ns


def load_roster(state: Path):
    """The roster entries as written, or None when the file is missing or unreadable (not JSON, wrong shape, an entry with no
    id). The one reader of the file: the start and every later re-read."""
    try:
        people = json.loads((state / "people.json").read_text("utf-8"))["people"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return people if isinstance(people, list) and all(entry_id(p) for p in people) else None


def read_roster(state: Path) -> tuple[list, str]:
    """The CURRENT roster and how it read: ``ok``, ``empty`` or ``unreadable``. Forget never depends on it: the kernel loads every
    id its record names whatever this says, and only roster members are granted anything.

    * MISSING people.json: an operator error (the owner never exported a roster): refuse to start;
    * EMPTY (``{"people": []}``, the last member removed): a valid household state. Nobody can be proposed for or acted for; the
      recorded people can still be forgotten;
    * UNREADABLE (not JSON, wrong shape, a non-string id): treated as EMPTY for grants (nothing is granted on a roster nobody can
      read), and forget of the recorded people is still served. The daemon says so on stderr (a fixed line).
    The same rule as ``authority.read_roster`` (kept textually close; neither imports the other)."""
    if not (state / "people.json").exists():
        raise SystemExit("people.json is missing: export the household roster first (see README)")
    people = load_roster(state)
    if people is None:
        print("zoe-os shadow kernel: people.json is unreadable: nobody is granted anything; forget of recorded people is served",
              file=sys.stderr, flush=True)
        return [], "unreadable"
    return people, "ok" if people else "empty"


def build(state: Path):
    """The kernel, its server and the credentials. Pure construction: nothing is served until ``serve``."""
    seen = roster_identity(state)        # BEFORE the read: a file replaced in between is simply read again at the first ask
    entries, roster_status = read_roster(state)
    people = roster_ids(entries)
    known = remember_people(state, people)
    (state / "kernel").mkdir(mode=0o700, exist_ok=True)
    # Every id the kernel holds a record for stays a person it knows, so a member taken off people.json can still be forgotten
    # (zoe-data forgets by id and reads "only a person can ask to forget" as "never recorded": this is what makes that true).
    # Only the CURRENT roster is granted anything below: a removed member is forgettable, never proposable.
    members = roster_people(entries)
    k = Kernel(str(state / "kernel" / "zoe-os.db"), members + [Person(p) for p in known if p not in set(people)])  # extras: least
    lists = Lists().register(k)
    register_list_remove(k, lists)
    reminders, alarms = Reminders(time.time).register(k), Alarms().register(k)
    for manifest in (*ADAPTERS, SHADOW_BRAIN):
        k.grants.install(manifest)
    k.install(Keeper(lists, reminders, alarms))
    k.grants.shadow = True                                     # this store records; it decides nothing (see the module docstring)
    for person in people:
        k.grants.grant(person, "brain", acts=list(ACTS), record_only=True)    # recorded whatever their role, never consent
    srv = ShadowServer(k, str(state / "k.sock"), state=state, roster=people, seen=seen)
    srv.roster_status = roster_status
    creds = {"voice": srv.issue("voice"), "brain": srv.issue("brain"), "panel": srv.issue("panel", surface=True)}
    return k, srv, creds


def write_creds(state: Path, sock: str, creds: dict) -> None:
    tmp = state / "creds.json.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"socket": sock, "creds": {n: c.hex() for n, c in creds.items()}}, fh)
    os.replace(tmp, state / "creds.json")


def serve(k: Kernel, srv: KernelServer, stop: threading.Event) -> None:
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    while not stop.wait(PUMP_S):          # the Keeper erases the driver's rows when the kernel forgets; pumped here
        with k.spine._lock:
            k.pump()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", help="state directory (else $OSLAB_SHADOW_DIR, else ~/.zoe/os-shadow)")
    state = state_dir(ap.parse_args(argv).dir)
    shadow_state.require_settled(state)   # makes and settles a missing dir; refuses data with no trustworthy settle marker
    os.umask(0o077)                       # the database, its WAL and the credentials are this user's alone
    k, srv, creds = build(state)
    write_creds(state, srv.path, creds)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    print(f"zoe-os shadow kernel up: {len(k.grants.people)} members, {len(k.recovered)} intents recovered as uncertain", flush=True)
    serve(k, srv, stop)
    srv.shutdown()
    k.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
