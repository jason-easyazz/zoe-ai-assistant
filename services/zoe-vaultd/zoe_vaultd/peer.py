"""SO_PEERCRED and process hardening helpers (Linux only)."""

from __future__ import annotations

import ctypes
import resource
import socket
import struct

PR_SET_DUMPABLE = 4


def peer_uid(conn: socket.socket) -> int:
    """The connecting process's uid, as the kernel recorded it at connect() time."""
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    _pid, uid, _gid = struct.unpack("3i", creds)
    return uid


def harden_process() -> dict:
    """No core dumps and not dumpable (no ptrace attach or /proc/<pid>/mem by other processes of the same uid).
    mlockall is best effort; the result is reported, never fatal."""
    out = {"dumpable_off": False, "core_off": False, "mlock": False}
    try:
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        out["core_off"] = True
    except (ValueError, OSError):
        pass
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        out["dumpable_off"] = libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0
        # MCL_FUTURE under a finite RLIMIT_MEMLOCK makes later allocations fail, so lock only when unlimited.
        if resource.getrlimit(resource.RLIMIT_MEMLOCK)[0] == resource.RLIM_INFINITY:
            MCL_CURRENT, MCL_FUTURE = 1, 2
            out["mlock"] = libc.mlockall(MCL_CURRENT | MCL_FUTURE) == 0
    except (OSError, AttributeError):
        pass
    return out
