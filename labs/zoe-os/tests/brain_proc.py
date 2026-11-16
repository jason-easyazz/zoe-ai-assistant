"""The kernel as its own process, with the brain's drivers registered (lab launcher, no service).

    stdin line 1   {"db_dir": ..., "socket": ...}
    stdout line 1  {"pid": ..., "creds": {name: hex}, "recovered": [seq]}
    then one JSON command per line: state | pump | arm_die | stop  (the supervisor's pipe, which
    the socket cannot reach). The brain's credential is issued here and handed to the brain
    process by the parent; the drivers and the database stay in THIS process.
"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent.parent), str(Path(__file__).parent)]

from world import ALEX, household  # noqa: E402

from zoe_os.brain.drivers import BRAIN, Alarms, Keeper, Lists, Reminders  # noqa: E402
from zoe_os.server import KernelServer  # noqa: E402


def main() -> None:
    cfg = json.loads(sys.stdin.readline())
    k = household(Path(cfg["db_dir"]))
    lists, reminders, alarms = Lists(), Reminders(k.clock), Alarms()
    die = {"armed": False}
    plain_add = lists.add

    def add(person, args):
        plain_add(person, args)
        if die["armed"]:
            os._exit(1)          # the write landed; the receipt never will
    lists.add = add
    for store in (lists, reminders, alarms):
        store.register(k)
    for manifest in (BRAIN, Keeper.manifest):
        k.grants.install(manifest)
    k.install(Keeper(lists, reminders, alarms))
    srv = KernelServer(k, cfg["socket"])
    creds = {name: srv.issue(name, surface=name == "panel").hex()
             for name in ("brain", "voice", "panel", "audit")}
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(json.dumps({"pid": os.getpid(), "creds": creds, "recovered": [e.seq for e in k.recovered]}),
          flush=True)
    for line in sys.stdin:
        cmd, out = json.loads(line)["cmd"], {}
        if cmd == "state":
            out = {"lists": lists.rows, "reminders": reminders.rows, "alarms": alarms.at}
        elif cmd == "pump":
            out = {"handled": k.pump()}
        elif cmd == "arm_die":
            die["armed"] = True
        elif cmd == "stop":
            break
        print(json.dumps(out), flush=True)
    srv.shutdown()
    k.close()


if __name__ == "__main__":
    main()
