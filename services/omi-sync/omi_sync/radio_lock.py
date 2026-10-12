"""The panel Pi's one-radio advisory lock, shared with ``services/panel-bluetooth`` (the Settings > Bluetooth agent).

Same path, same ``flock`` semantics as ``panel_bt.radio_lock`` (the two services are separate processes and venvs, so this is a
deliberate 40-line copy, not an import): ``ZOE_BLE_RADIO_LOCK``, default ``~/.zoe-ble-radio.lock``. While the Bluetooth agent holds it
(a scan, an open Settings link, a firmware update) the sync service does NOT connect - it reports 'not reachable' and tries again
later - because the pendant accepts one connection and a stolen link during a firmware reset cannot be recovered.
The kernel drops the lock if either process dies. Nobody waits: the sync loop already retries on its own timer.
"""
from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path
from typing import Optional

HOLDER = "omi-sync"


def default_path() -> Path:
    return Path(os.environ.get("ZOE_BLE_RADIO_LOCK") or Path.home() / ".zoe-ble-radio.lock").expanduser()


class RadioLock:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else default_path()
        self._fd: Optional[int] = None

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
            os.write(fd, HOLDER.encode())
        self._fd = fd
        return True

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
        try:
            return self.path.read_text(encoding="utf-8", errors="ignore").strip()[:40]
        except OSError:
            return ""
