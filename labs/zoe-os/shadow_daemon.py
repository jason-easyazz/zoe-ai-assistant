"""The Zoe OS kernel as a long-running SHADOW process: the one the zoe-data shadow client (``os_shadow.py``) talks to.

    python3 labs/zoe-os/shadow_daemon.py [--dir STATE_DIR]

Operator opt-in (``scripts/setup/systemd/zoe-os-shadow-kernel.service``); never started by any script or deploy. It is NOT
authoritative: zoe-data's own list add stays the only writer of the household's list, and this kernel records the event and a
receipt beside it, in its OWN store. Read ``docs/knowledge/zoe-os-list-shadow.md`` first.

State directory (default ``$ZOE_OS_SHADOW_DIR`` or ``~/.zoe/os-shadow``; mode 0700, never inside a repo checkout):

    people.json   {"people": [member ids]}   REQUIRED, written by the owner (``os_shadow.export_roster``); read at start
    creds.json    {"socket": path, "creds": {"voice": hex, "brain": hex, "panel": hex}}   written here, 0600, on every start
    k.sock        the Unix socket (0600)
    kernel/       the kernel's SQLite spine (events, receipts, intents), 0600

The verbs it serves: ``list.add``, ``list.remove`` (registered HERE, over the same list rows: the lab drivers have no remove),
``remind.set`` and ``alarm.set`` (the lab drivers, ``zoe_os/brain/drivers.py``). Which of them zoe-data proposes is its own
``ZOE_OS_SHADOW_VERBS``; the kernel serves all four.

What it grants itself, and why that is honest only in shadow: every roster member gets the ``brain`` skill's four acts at
start, with no consent ceremony, because a shadow records what the household already did; it must never be reused as the consent
model for an authoritative kernel (a child's grant needs a guardian: not modelled here, every member is an adult).
Members are never removed from the roster by this process: a member the kernel recorded cannot be forgotten once it no longer
knows them.

Stdlib only. The list rows live in memory (the lab ``Lists`` driver); the durable record is the spine.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent)]

from zoe_os.brain.drivers import ASKED, BRAIN, Alarms, Keeper, Lists, Reminders  # noqa: E402
from zoe_os.capability import Person  # noqa: E402
from zoe_os.kernel import Kernel  # noqa: E402
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
    return Path(arg or os.environ.get("ZOE_OS_SHADOW_DIR") or Path.home() / ".zoe" / "os-shadow")


def build(state: Path):
    """The kernel, its server and the credentials. Pure construction: nothing is served until ``serve``."""
    people = json.loads((state / "people.json").read_text("utf-8"))["people"]
    if not people or not all(isinstance(p, str) and p for p in people):
        raise SystemExit("people.json has no members: the shadow kernel records nothing for a household it does not know")
    (state / "kernel").mkdir(mode=0o700, exist_ok=True)
    k = Kernel(str(state / "kernel" / "zoe-os.db"), [Person(p) for p in people])
    lists = Lists().register(k)
    register_list_remove(k, lists)
    reminders, alarms = Reminders(time.time).register(k), Alarms().register(k)
    for manifest in (*ADAPTERS, SHADOW_BRAIN):
        k.grants.install(manifest)
    k.install(Keeper(lists, reminders, alarms))
    for person in people:
        k.grants.grant(person, "brain", acts=list(ACTS))       # the shadow records what already happened: see the module docstring
    srv = KernelServer(k, str(state / "k.sock"))
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
    ap.add_argument("--dir", help="state directory (default $ZOE_OS_SHADOW_DIR or ~/.zoe/os-shadow)")
    state = state_dir(ap.parse_args(argv).dir)
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
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
