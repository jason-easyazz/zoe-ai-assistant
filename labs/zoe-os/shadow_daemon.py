"""The Zoe OS kernel as a long-running SHADOW process: the one the zoe-data shadow client (``os_shadow.py``) talks to.

    python3 labs/zoe-os/shadow_daemon.py [--dir STATE_DIR]

Operator opt-in (``scripts/setup/systemd/zoe-os-shadow-kernel.service``); never started by any script or deploy. It is NOT
authoritative: zoe-data's own list add stays the only writer of the household's list, and this kernel records the event and a
receipt beside it, in its OWN store. Read ``docs/knowledge/zoe-os-list-shadow.md`` first.

State directory (``--dir``, else ``$OSLAB_SHADOW_DIR`` (a lab process reads no ``ZOE_`` name), else ``~/.zoe/os-shadow``; mode 0700, never inside a repo checkout):

    people.json   {"people": [member ids]}   REQUIRED, written by the owner (``os_shadow.export_roster``); read at start. Missing:
                  the start is refused. EMPTY (the last member removed) or unreadable: the daemon starts, nobody is granted
                  anything, and the recorded people can still be forgotten (``read_roster``)
    known-people.json  the roster plus every id the kernel still holds a record for: who it can still FORGET. Derived from the
                  kernel's own record at each start, and rewritten (atomic, mode kept) after a whole-person forget succeeds
    creds.json    {"socket": path, "creds": {"voice": hex, "brain": hex, "panel": hex}}   written here, 0600, on every start
    identity-creds.json  {"socket": path, "creds": {"speaker-verifier": hex}, "ttl_s": 60}   0600, on every start: the secret
                  the component that VERIFIES people (zoe-data) mints speaker assertions with (``zoe_os/speaker.py``). A
                  person-bound op from a surface (forget, grant, revoke, undo, explain for a person) needs one; the panel's
                  credential alone is refused ``speaker unproven``. Fresh each start: an older assertion is dead after a restart.
    k.sock        the Unix socket (0600)
    kernel/       the kernel's SQLite spine (events, receipts, intents), 0600

The verbs it serves: ``list.add``, ``list.remove`` (registered HERE, over the same list rows: the lab drivers have no remove),
``remind.set`` and ``alarm.set`` (the lab drivers, ``zoe_os/brain/drivers.py``). Which of them zoe-data proposes is its own
``ZOE_OS_SHADOW_VERBS``; the kernel serves all four.

What it grants itself, and why that is honest only in shadow: every roster member gets the ``brain`` skill's four acts at
start, with no consent ceremony, because a shadow records what the household already did; it must never be reused as the consent
model for an authoritative kernel (a child's grant needs a guardian: not modelled here, every member is an adult).
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
from zoe_os.capability import Person  # noqa: E402
from zoe_os.kernel import Kernel  # noqa: E402
from zoe_os import speaker  # noqa: E402
from zoe_os.server import KernelServer  # noqa: E402

# trusted origin adapters: the voice surface records the utterance that is the cause of an act; the panel is the surface that
# relays a person's forget. Same manifests the lab tests use (tests/world.py ADAPTERS).
ADAPTERS = ({"skill": "voice", "emits": ["utterance"], "origin": True},
            {"skill": "panel", "emits": ["offer_response"], "origin": True})
PUMP_S = 0.5
#: the brain manifest's ceiling plus the shadow-only ``list.remove``
SHADOW_BRAIN = {**BRAIN, "acts": [*BRAIN["acts"], "list.remove"]}
ACTS = tuple(SHADOW_BRAIN["acts"])


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


def recorded_people(db: Path) -> set:
    """Every id the kernel still holds a record for: its ``people`` links, exactly what a whole-person forget seeds from. Any error
    but "no database / no table yet" RAISES: the daemon refuses to start rather than lose who it can still forget."""
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


def remember_people(state: Path, people: list, db: Path | None = None) -> list:
    """The roster plus everyone the kernel still holds a record for, written BEFORE the kernel serves. Derived, never accumulated:
    an id left in the file by a crash between a forget and its rewrite is dropped here. Temp files a killed writer left (they can
    hold ids) are removed first."""
    for stale in (*state.glob(f".{KNOWN}.*.tmp"), state / f"{KNOWN}.tmp"):
        stale.unlink(missing_ok=True)
    return write_known(state, set(people) | recorded_people(db or state / "kernel" / "zoe-os.db"))


class ShadowServer(KernelServer):
    """The kernel's server, plus the one copy of member ids the daemon keeps outside the kernel (``known-people.json``).

    Order: the kernel's forget FIRST, then the copy. A refused or failed forget raises before anything here runs, so the id stays
    for the retry (dropping it first would turn the retry into "never recorded" while the record still exists). If the rewrite
    fails after the kernel forgot, the forget answers ``internal_error`` and the client retries it (a no-op in the kernel, the
    rewrite again); a crash in between is repaired at the next start, which derives the file from the kernel's record."""

    def __init__(self, kernel: Kernel, path: str, *, state: Path, roster) -> None:
        super().__init__(kernel, path)
        self.state, self.roster = state, frozenset(roster)

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
        if subject in self.roster or subject not in people:
            return
        write_known(self.state, (p for p in people if p != subject))
        people.pop(subject, None)


def read_roster(state: Path, daemon: str = "shadow") -> tuple[list, str]:
    """The CURRENT roster and how it read: ``ok``, ``empty`` or ``unreadable``. Forget never depends on it: the kernel loads every
    id its record names whatever this says, and only roster members are granted anything.

    * MISSING people.json (no file, or a symlink to nothing: ``FileNotFoundError``): an operator error (the owner never exported a
      roster): refuse to start;
    * EMPTY (``{"people": []}``, the last member removed): a valid household state. Nobody can be proposed for or acted for; the
      recorded people can still be forgotten;
    * UNREADABLE: a file that is there but cannot be read (``EACCES``, ``EIO``, a directory in its place: any other ``OSError``),
      or is not JSON, has the wrong shape or a non-string id. Treated as EMPTY for grants (nothing is granted on a roster nobody
      can read), and forget of the recorded people is still served. The daemon says so on stderr (a fixed line).
    ONE rule for both daemons: ``authority.py`` imports this (``daemon`` names which one in the stderr line)."""
    path = state / "people.json"
    try:
        raw = path.read_text("utf-8")
    except FileNotFoundError:
        raise SystemExit("people.json is missing: export the household roster first (see README)") from None
    except (OSError, UnicodeDecodeError):
        raw = None                                 # there, but unreadable: never a reason to stop serving forget
    try:
        people = json.loads(raw)["people"] if raw is not None else None
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        people = None
    if not isinstance(people, list) or not all(isinstance(p, str) and p for p in people):
        print(f"zoe-os {daemon} kernel: people.json is unreadable: nobody is granted anything; forget of recorded people is served",
              file=sys.stderr, flush=True)
        return [], "unreadable"
    return people, "ok" if people else "empty"


def build(state: Path, db: str | None = None):
    """The kernel, its server and the credentials. Pure construction: nothing is served until ``serve``."""
    people, roster_status = read_roster(state)
    from authority import db_path                  # the same move rule as the authority: never start beside an unmoved record
    path = db_path(state, db)                      # --db: the permanent home (authority.PERMANENT_DB)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    known = remember_people(state, people, path)   # derived from the record in the database this kernel will open
    # Every id the kernel holds a record for stays a person it knows, so a member taken off people.json can still be forgotten
    # (zoe-data forgets by id and reads "only a person can ask to forget" as "never recorded": this is what makes that true).
    # Only the CURRENT roster is granted anything below: a removed member is forgettable, never proposable.
    k = Kernel(str(path), [Person(p) for p in known])
    lists = Lists().register(k)
    register_list_remove(k, lists)
    reminders, alarms = Reminders(time.time).register(k), Alarms().register(k)
    for manifest in (*ADAPTERS, SHADOW_BRAIN):
        k.grants.install(manifest)
    k.install(Keeper(lists, reminders, alarms))
    for person in people:
        k.grants.grant(person, "brain", acts=list(ACTS))       # the shadow records what already happened: see the module docstring
    srv = ShadowServer(k, str(state / "k.sock"), state=state, roster=people)
    srv.roster_status = roster_status
    creds = {"voice": srv.issue("voice"), "brain": srv.issue("brain"), "panel": srv.issue("panel", surface=True)}
    return k, srv, creds


def write_creds(state: Path, sock: str, creds: dict) -> None:
    tmp = state / "creds.json.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        json.dump({"socket": sock, "creds": {n: c.hex() for n, c in creds.items()}}, fh)
    os.replace(tmp, state / "creds.json")


def write_identity(state: Path, srv: KernelServer) -> None:
    """The speaker-verifier's secret, the way every credential file is written (0600 from creation, atomic). Never printed."""
    from authority import write_secret
    write_secret(state / speaker.IDENTITY_CREDS, {"socket": srv.path, "creds": {speaker.IDENTITY: srv.speaker_secret().hex()},
                                                  "generation": srv.speaker_generation(),
                                                  "ttl_s": speaker.TTL_S})


def serve(k: Kernel, srv: KernelServer, stop: threading.Event) -> None:
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    while not stop.wait(PUMP_S):          # the Keeper erases the driver's rows when the kernel forgets; pumped here
        with k.spine._lock:
            k.pump()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dir", help="state directory (else $OSLAB_SHADOW_DIR, else ~/.zoe/os-shadow)")
    ap.add_argument("--db", help="the kernel's database file; default STATE/kernel/zoe-os.db")
    opts = ap.parse_args(argv)
    state = state_dir(opts.dir)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.umask(0o077)                       # the database, its WAL and the credentials are this user's alone
    k, srv, creds = build(state, opts.db)
    write_creds(state, srv.path, creds)
    write_identity(state, srv)
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
