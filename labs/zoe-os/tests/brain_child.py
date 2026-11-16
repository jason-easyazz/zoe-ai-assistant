"""The brain process for the structural test: it is handed a socket path and a credential, and
nothing else, then reports what it can see. Run with a throwaway working directory."""
import json
import os
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parent.parent)]      # the package, NOT the tests directory

from zoe_os.brain.bridge import BrainBridge, Turn  # noqa: E402

handed = json.loads(sys.stdin.readline())
bridge = BrainBridge(handed["socket"], bytes.fromhex(handed["cred"]))
tools = [t["function"]["name"] for t in bridge.tools()]
out = bridge.propose(Turn(handed["member"], handed["cause"]), "list_add", {"item": handed["item"]})
fds = []
for name in os.listdir("/proc/self/fd"):
    try:
        fds.append(os.readlink(f"/proc/self/fd/{name}"))
    except OSError:
        pass
print(json.dumps({
    "tools": tools, "status": out.status, "handed": sorted(handed),
    "modules": sorted(m for m in sys.modules if m.startswith("zoe_os") or m in ("world", "sqlite3")),
    "file_fds": sorted(f for f in fds if f.startswith("/") and not f.startswith(("/dev", "/proc", "/sys"))
                       and ".so" not in f),
    "kernel_db_visible_by_handed_path": [v for v in handed.values() if isinstance(v, str) and v.endswith(".db")],
}), flush=True)
