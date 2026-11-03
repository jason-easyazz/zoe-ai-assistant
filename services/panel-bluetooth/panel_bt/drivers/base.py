"""The driver interface and the standard GATT reads every driver shares.

A driver answers four questions about a kind of device: *is this advert one of mine* (``match``), *what do I know about it once
linked* (``info``), *is there an update* (``update_check``), and *do the update* (``run_update``). The registry (``__init__``) picks
the best match for an advert; the generic driver matches everything at the lowest score, so every device is connectable and gets
DIS + battery, and a specific driver (Omi) adds its extras on top.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from ..model import E_UNSUPPORTED, Advert, AgentError

# Standard Bluetooth SIG characteristics (Device Information + Battery)
DIS_MANUFACTURER = "00002a29-0000-1000-8000-00805f9b34fb"
DIS_MODEL = "00002a24-0000-1000-8000-00805f9b34fb"
DIS_FIRMWARE = "00002a26-0000-1000-8000-00805f9b34fb"
DIS_HARDWARE = "00002a27-0000-1000-8000-00805f9b34fb"
BATTERY_LEVEL = "00002a19-0000-1000-8000-00805f9b34fb"
# (DIS Serial Number 2A25 is deliberately NOT read: it identifies one physical device and nothing here needs it.)


def _text(raw: bytes) -> str:
    return raw.decode("utf-8", "ignore").strip("\x00 \r\n\t")[:60]


async def read_standard_info(link: Any) -> dict:
    """Manufacturer / model / firmware / hardware / battery - only the characteristics the device actually has. Never raises for a
    missing or unreadable one (that field is simply absent / None)."""
    out: dict[str, Any] = {}
    for key, uuid in (("manufacturer", DIS_MANUFACTURER), ("model", DIS_MODEL), ("firmware", DIS_FIRMWARE), ("hardware", DIS_HARDWARE)):
        if link.has_char(uuid):
            try:
                out[key] = _text(await link.read_char(uuid))
            except Exception:  # noqa: BLE001 - one unreadable field must not lose the rest
                out[key] = None
    out["battery"] = None
    if link.has_char(BATTERY_LEVEL):
        try:
            raw = await link.read_char(BATTERY_LEVEL)
            if raw and 0 <= raw[0] <= 100:
                out["battery"] = int(raw[0])
        except Exception:  # noqa: BLE001
            pass
    return out


@dataclass
class UpdateContext:
    """Everything an update run needs, handed in by the agent (which already holds the radio lock)."""
    settings: Any
    backend: Any
    address: str
    addr_type: str
    device_id: str
    progress: Callable[[str, int, str], Awaitable[None]]       # (step, percent, message)
    cancel: asyncio.Event
    link: Optional[Any] = None                                    # an open link to this device, if the agent has one
    release_link: Optional[Callable[[], Awaitable[None]]] = None  # closes that link (the update opens its own sessions)
    extras: dict = field(default_factory=dict)


class Driver:
    name = "base"
    label = "Device"
    known = True                       # False only for the generic fallback ("no specific driver")
    supports_update = False
    supports_bond = False           # only a driver whose device runs bonded-only firmware (the Omi pendant) may be bonded

    def match(self, advert: Advert) -> int:
        """0 = not mine; higher beats lower. The generic driver returns 1 for everything."""
        return 0

    async def info(self, link: Any, ctx: dict) -> dict:
        return await read_standard_info(link)

    async def update_check(self, info: dict, ctx: dict) -> dict:
        return {"supported": False, "available": False, "reason": "This device type has no update support."}

    async def run_update(self, uctx: UpdateContext) -> dict:
        raise AgentError(E_UNSUPPORTED, "This device type cannot be updated from here.")
