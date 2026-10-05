"""What does a SECOND writer process see on a MemPalace palace? (HM arm, Part A.1: lock files and concurrent clients.)

MemPalace takes ``mine_palace_lock(palace_path)`` - a non-blocking ``flock`` on ``$HOME/.mempalace/locks/mine_palace_
<sha256(path)[:16]>.lock`` - around every collection write. A second PROCESS writing the same palace while the first
holds the lock does not wait: it raises ``MineAlreadyRunning``. Two palaces (two paths) never contend. This probe
measures both, with a child process that holds the lock for a few seconds.

Run with the bake-off venv in the scrubbed env (mp_run.sh). Prints JSON.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

WORK = Path("/home/zoe/.zoe/bakeoff-2026-10/mp-work/lock-probe")

HOLDER = """
import sys, time
from mempalace.palace import mine_palace_lock
with mine_palace_lock(sys.argv[1]):
    print('held', flush=True)
    time.sleep(float(sys.argv[2]))
"""


def main() -> int:
    from mempalace.palace import get_collection
    shutil.rmtree(WORK, ignore_errors=True)
    a, b = WORK / "palace-a", WORK / "palace-b"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    ca = get_collection(str(a), create=True)
    cb = get_collection(str(b), create=True)
    ca.upsert(ids=["x"], documents=["seed"], metadatas=[{"wing": "w", "room": "r"}])
    out: dict = {"lock_dir": str(Path(os.path.expanduser("~")) / ".mempalace" / "locks")}
    child = subprocess.Popen([sys.executable, "-c", HOLDER, str(a), "6"], stdout=subprocess.PIPE, text=True)
    child.stdout.readline()                                  # 'held'
    t0 = time.perf_counter()
    try:
        ca.upsert(ids=["y"], documents=["second writer"], metadatas=[{"wing": "w", "room": "r"}])
        out["same_palace_second_process_write"] = "succeeded"
    except Exception as exc:  # noqa: BLE001 - record the type
        out["same_palace_second_process_write"] = f"{type(exc).__name__}: {str(exc)[:90]}"
    out["same_palace_attempt_seconds"] = round(time.perf_counter() - t0, 3)
    t0 = time.perf_counter()
    cb.upsert(ids=["z"], documents=["other palace"], metadatas=[{"wing": "w", "room": "r"}])
    out["other_palace_write"] = "succeeded"
    out["other_palace_seconds"] = round(time.perf_counter() - t0, 3)
    r = ca.query(query_texts=["seed"], n_results=1)
    out["same_palace_read_while_locked"] = "succeeded" if r["ids"][0] else "empty"
    child.wait()
    out["lock_files"] = sorted(p.name for p in (Path(os.path.expanduser("~")) / ".mempalace" / "locks").glob("*"))
    shutil.rmtree(WORK, ignore_errors=True)
    print(json.dumps(out, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
