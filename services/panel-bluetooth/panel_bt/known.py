"""The devices this panel has been told about (connected at least once, not since forgotten). Pi-local; mode 0600.

This is the ONLY place a device ADDRESS is persisted. It is never sent to zoe-data: the agent speaks in ``device_id`` (a hash).
``forget`` removes the entry; there is no BlueZ bond to clear because the agent never pairs.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from .model import ADDR_RX, DEVICE_ID_RX, device_id


class KnownStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _load(self) -> dict:
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            return doc if isinstance(doc, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, doc: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        tmp.replace(self.path)

    def all(self) -> dict:
        return {k: v for k, v in self._load().items() if DEVICE_ID_RX.match(k) and isinstance(v, dict) and ADDR_RX.match(str(v.get("address", "")))}

    def get(self, dev_id: str) -> Optional[dict]:
        return self.all().get(dev_id)

    def add(self, address: str, addr_type: str, name: str, driver: str) -> str:
        doc = self._load()
        did = device_id(address)
        doc[did] = {"address": address, "addr_type": addr_type if addr_type in ("random", "public") else "random",
                    "name": name[:60], "driver": driver, "added_at": doc.get(did, {}).get("added_at") or time.time()}
        self._save(doc)
        return did

    def remove(self, dev_id: str) -> bool:
        doc = self._load()
        if dev_id not in doc:
            return False
        del doc[dev_id]
        self._save(doc)
        return True
