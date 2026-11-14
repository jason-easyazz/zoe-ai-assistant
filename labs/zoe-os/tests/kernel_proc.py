"""The kernel as ITS OWN PROCESS, for the boundary tests (a lab launcher, not a service).

    stdin line 1   {"db_dir": ..., "socket": ..., "install": [manifests], "surfaces": [names],
                    "server": {KernelServer keyword arguments}}
    stdout line 1  {"pid": ..., "creds": {skill: hex}}       <- the supervisor's pipe

The pipe to the parent stands in for the supervisor (systemd in production): it is how a
credential reaches the one process it is for, and it is NOT reachable from the socket. The
parent can then ask for driver state, re-issue a credential, or apply one named negative
control. Controls live HERE, in the tests: the kernel has no switch that weakens it.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent.parent), str(Path(__file__).parent)]

from world import household  # noqa: E402

from zoe_os.capability import Enforcer  # noqa: E402
from zoe_os.receipts import Dispatcher  # noqa: E402
from zoe_os.server import KernelServer  # noqa: E402
from zoe_os.spine import Decision, Spine  # noqa: E402


def _set(obj, **attrs):
    for name, value in attrs.items():
        setattr(obj, name, value)


CONTROLS = {     # name -> remove exactly one guarantee
    "no_read_check": lambda srv: _set(Enforcer, may_read=lambda s, r, e: Decision(True),
                                      may_subscribe=lambda s, r, k, p: Decision(True)),
    "no_authorize": lambda srv: _set(Dispatcher, _authorize=lambda s, a, b, c: Decision(True, "", "none")),
    "no_tombstone": lambda srv: _set(Spine, _tombstone=lambda s, seqs: None),
    "no_shield": lambda srv: _set(Spine, _shielded=lambda s, payload: False),
    "no_verify": lambda srv: _set(KernelServer, _verify=lambda s, name, nonce, proof: True),
    "fixed_nonce": lambda srv: _set(KernelServer, _nonce=lambda s: b"\0" * 32),
    "no_size_cap": lambda srv: _set(srv, max_frame=1 << 24),
    "no_validate": lambda srv: _set(KernelServer, _validate=lambda s, req: ""),
    "no_deadline": lambda srv: _set(srv, frame_deadline=None),
    "no_release": lambda srv: _set(KernelServer, _release=lambda s, conn: None),
}


def main() -> None:
    cfg = json.loads(sys.stdin.readline())
    os.umask(0o077)                       # the database and its WAL are the kernel user's alone
    k = household(Path(cfg["db_dir"]))
    for manifest in cfg.get("install", ()):
        k.grants.install(manifest)
    srv = KernelServer(k, cfg["socket"], **cfg.get("server", {}))
    creds = {name: srv.issue(name, surface=name in cfg.get("surfaces", ())).hex()
             for name in k.grants.manifests}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(json.dumps({"pid": os.getpid(), "creds": creds}), flush=True)
    for line in sys.stdin:                # EOF (the parent died) ends the kernel too
        msg = json.loads(line)
        out: dict = {}
        if msg["cmd"] == "state":
            out = {"lights": k.lights.toggled, "alarms": k.alarms.at, "subs": len(k.spine._subs)}
        elif msg["cmd"] == "control":
            CONTROLS[msg["name"]](srv)
        elif msg["cmd"] == "reissue":
            out = {"cred": srv.issue(msg["skill"]).hex()}
        elif msg["cmd"] == "stop":
            break
        print(json.dumps(out), flush=True)
    srv.shutdown()
    k.close()


if __name__ == "__main__":
    main()
