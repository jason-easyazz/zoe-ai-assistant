"""Shapes shared by the whole agent: adverts, commands, results, the opaque device id.

Pure - no BLE, no I/O. Everything that crosses the wire to zoe-data is built here, so the privacy rule is structural:
a device's BLE ADDRESS never leaves the Pi. zoe-data, the browser and the audit log only ever see ``device_id(address)``
(16 hex of a salted SHA-256), and the agent maps it back to an address from its own scan cache / known-devices file.

ONE identity per device, on purpose: this is the SAME id ``omi_sync.uploader.device_hash`` sends as ``X-Omi-Device``, so the id
Settings > Bluetooth shows for a pendant is the id zoe-data's ``omi_devices`` registry maps to the household member who owns it
(tests/unit/test_omi_device_identity.py pins that the two derivations cannot drift).
"""
from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

ADDR_RX = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
DEVICE_ID_RX = re.compile(r"^[0-9a-f]{16}$")
#: the namespace is historic (omi-sync shipped first); it is part of the id, so it cannot be renamed without orphaning recordings
ID_NAMESPACE = "omi-sync:"
JOB_ID_RX = re.compile(r"^[A-Za-z0-9_-]{6,64}$")

#: every operation the agent will run. Anything else is refused before it touches the radio.
OPS = ("scan", "connect", "disconnect", "forget", "info", "status", "update_check", "update_start", "update_cancel", "omi_bond")
#: operations that need a target device
DEVICE_OPS = frozenset({"connect", "disconnect", "forget", "info", "update_check", "update_start", "update_cancel", "omi_bond"})

# error codes the UI maps to plain sentences (the agent also sends a human message with each)
E_BAD_REQUEST = "bad_request"
E_BUSY = "radio_busy"
E_NOT_FOUND = "not_found"
E_OUT_OF_RANGE = "out_of_range"
E_CONNECT = "connect_failed"
E_NOT_CONNECTED = "not_connected"
E_GATE = "gate_failed"
E_OTA_OFF = "ota_disabled"
E_BOND_OFF = "bond_disabled"
E_BOND = "bond_failed"
E_UPDATE = "update_failed"
E_CANCELLED = "cancelled"
E_UNSUPPORTED = "unsupported"
E_INTERNAL = "internal"


def device_id(address: str) -> str:
    """A stable, non-reversible 16-hex id for a device (identical to ``omi_sync.uploader.device_hash``). The address itself stays on the Pi."""
    return hashlib.sha256((ID_NAMESPACE + address.strip().lower()).encode()).hexdigest()[:16]


@dataclass(frozen=True)
class Advert:
    """One device seen in a scan. ``address`` is Pi-local; it is dropped before anything is sent to zoe-data."""
    address: str
    addr_type: str                       # 'random' | 'public'
    name: str
    rssi: Optional[int]
    service_uuids: tuple = ()
    manufacturer_ids: tuple = ()
    seen_at: float = 0.0

    @property
    def id(self) -> str:
        return device_id(self.address)


class AgentError(Exception):
    """A refused or failed operation with a stable ``code`` and a sentence a household member can read."""

    def __init__(self, code: str, message: str, *, detail: Optional[dict] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail or {}

    def as_dict(self) -> dict:
        out = {"code": self.code, "message": self.message}
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass(frozen=True)
class Command:
    job_id: str
    op: str
    device_id: Optional[str] = None
    params: dict = field(default_factory=dict)


def parse_command(raw: Any) -> Command:
    """Validate one command from zoe-data. The agent never trusts the server's shape: whitelisted op, well-formed ids."""
    if not isinstance(raw, dict):
        raise AgentError(E_BAD_REQUEST, "command is not an object")
    op = raw.get("op")
    if op not in OPS:
        raise AgentError(E_BAD_REQUEST, "unknown operation")
    job_id = raw.get("job_id")
    if not isinstance(job_id, str) or not JOB_ID_RX.match(job_id):
        raise AgentError(E_BAD_REQUEST, "bad job id")
    dev = raw.get("device_id")
    if op in DEVICE_OPS:
        if not isinstance(dev, str) or not DEVICE_ID_RX.match(dev):
            raise AgentError(E_BAD_REQUEST, "bad device id")
    else:
        dev = None
    params = raw.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict) or len(repr(params)) > 2000:
        raise AgentError(E_BAD_REQUEST, "bad params")
    return Command(job_id=job_id, op=op, device_id=dev, params=params)


def version_tuple(text: Optional[str]) -> Optional[tuple]:
    """'3.0.18' / 'v3.0.21' -> (3, 0, 18). ``None`` when it is not a plain dotted version (unknown is not 0.0.0)."""
    if not text:
        return None
    m = re.match(r"^\s*v?(\d+(?:\.\d+){0,3})\s*$", text.strip().strip("\x00"))
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def now() -> float:
    return time.time()
