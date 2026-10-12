"""One radio, one user at a time: an advisory ``flock`` shared with ``omi-sync``.

The panel Pi has ONE Bluetooth radio and the pendant accepts ONE connection (``CONFIG_BT_MAX_CONN=1``). If the sync service
connected in the gap after a firmware reset, the update could never be confirmed and MCUboot would revert it. So every radio
use here - a scan, an open link, a whole firmware update - holds this lock, and ``omi_sync.ble_link`` takes the SAME lock (same
path, same ``flock`` semantics; ``ZOE_BLE_RADIO_LOCK``, default ``~/.zoe-ble-radio.lock``) before it connects.

``flock`` is released by the kernel when the holder dies, so a crash can never wedge the radio. The file's text names the holder
(``panel-bluetooth`` / ``omi-sync``) so the other side can say "the pendant is syncing" instead of "busy".
"""
from __future__ import annotations

import asyncio
import contextlib
import fcntl
import os
from pathlib import Path
from typing import Optional


class RadioLock:
    def __init__(self, path: Path, holder: str = "panel-bluetooth") -> None:
        self.path = Path(path)
        self.holder = holder
        self._fd: Optional[int] = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def try_acquire(self) -> bool:
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        with contextlib.suppress(OSError):
            os.ftruncate(fd, 0)
            os.write(fd, self.holder.encode())
        self._fd = fd
        return True

    async def acquire(self, timeout_s: float, poll_s: float = 0.25) -> bool:
        waited = 0.0
        while True:
            if self.try_acquire():
                return True
            if waited >= timeout_s:
                return False
            await asyncio.sleep(poll_s)
            waited += poll_s

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        with contextlib.suppress(OSError):
            os.ftruncate(fd, 0)
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)

    def current_holder(self) -> str:
        """Who holds it right now, best effort ('' when nobody / unreadable)."""
        try:
            return self.path.read_text(encoding="utf-8", errors="ignore").strip()[:40]
        except OSError:
            return ""
