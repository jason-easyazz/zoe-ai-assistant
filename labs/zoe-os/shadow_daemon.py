"""The Zoe OS kernel as a long-running SHADOW process: the one the list-add shadow client talks to.

    python3 labs/zoe-os/shadow_daemon.py [--dir STATE_DIR]

Operator opt-in (``scripts/setup/systemd/zoe-os-shadow-kernel.service``); never started by any script or deploy. It is NOT
authoritative: zoe-data's own list add stays the only writer of the household's list, and this kernel records the event and a
receipt beside it, in its OWN store. Read ``docs/knowledge/zoe-os-list-shadow.md`` first.

State directory (default ``$ZOE_OS_SHADOW_DIR`` or ``~/.zoe/os-shadow``; mode 0700, never inside a repo checkout):

    people.json   {"people": [member ids]}   REQUIRED, written by the owner (``os_shadow.export_roster``); read at start
    creds.json    {"socket": path, "creds": {"voice": hex, "brain": hex, "panel": hex}}   written here, 0600, on every start
    k.sock        the Unix socket (0600)
    kernel/       the kernel's SQLite spine (events, receipts, intents), 0600

What it grants itself, and why that is honest only in shadow: every roster member gets the ``brain`` skill's ``list.add`` grant at
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
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent)]

from zoe_os.brain.drivers import BRAIN, Keeper, Lists  # noqa: E402
from zoe_os.capability import Person  # noqa: E402
from zoe_os.kernel import Kernel  # noqa: E402
from zoe_os.server import KernelServer  # noqa: E402

# trusted origin adapters: the voice surface records the utterance that is the cause of an act; the panel is the surface that
# relays a person's forget. Same manifests the lab tests use (tests/world.py ADAPTERS).
ADAPTERS = ({"skill": "voice", "emits": ["utterance"], "origin": True},
            {"skill": "panel", "emits": ["offer_response"], "origin": True})
PUMP_S = 0.5


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
    for manifest in (*ADAPTERS, BRAIN):
        k.grants.install(manifest)
    k.install(Keeper(lists))
    for person in people:
        k.grants.grant(person, "brain", acts=["list.add"])      # the shadow records what already happened: see the module docstring
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
