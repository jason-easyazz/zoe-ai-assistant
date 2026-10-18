"""The ONE place on the Pi that creates a Bluetooth bond: an Omi pendant on Zoe phase-2 firmware, when the owner asks.

Phase-2 firmware (``labs/omi-firmware``) serves audio, storage, settings and the OTA (SMP) service only over an ENCRYPTED link
and accepts a pairing only while its button window is open (two double-taps; it buzzes). So bonding is a deliberate, physical,
owner-present act: Settings > Bluetooth -> "Pair securely" -> ``omi_bond`` (flag ``PANEL_BT_BOND_ENABLED``, default OFF, and a
``confirm``) -> this module. After it, ``omi-sync`` (``OMI_SYNC_REQUIRE_BOND``) and this agent's firmware update both ride the
bond; nobody else can read the pendant.

How: the hcitool-lecc connection recipe, then BlueZ ``Device1.Pair`` through bleak 3.0.2's ``client.pair()`` with NO agent.
The panel's adapter is NOT bondable at rest (read 2026-10-19: BlueZ 5.66, ``Pairable`` false, ``AlwaysPairable`` unset) - which
is also why a pendant's Security Request is answered "not supported" - and a pairing on a non-bondable adapter stores no keys.
So this module makes the adapter pairable for the pairing ONLY: ``PairableTimeout`` 60 s first (BlueZ reverts by itself even if
this process dies), ``Pairable`` true, and ``Pairable`` false again in ``finally``.
BlueZ then pairs as NoInputNoOutput, i.e. LE Secure Connections "Just Works" from the initiator side, which needs no user
confirmation (UNVERIFIED on the panel's BlueZ until the first supervised pairing; if it asks for one, the fix is a scoped
agent HERE, reviewed, never anywhere else). Refused unless the pendant reports Zoe firmware (a 4-part DIS version, e.g.
``3.0.21.1``): bonding a stock pendant would only take its single bond slot for nothing. Success is not "Pair returned": it
is BlueZ reading ``Paired`` AND ``Bonded`` true and the kernel showing the link ``ENCRYPT``ed.

``replace`` deletes THIS pendant's old BlueZ entry first (``Adapter1.RemoveDevice`` for its path only) - for a pendant that was
re-paired or re-flashed and holds a new key. Nothing else is ever removed. The parsers below are deliberate copies of
``omi_sync.link_security`` (two venvs, no import); tests/unit/test_omi_bond.py pins them equal.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable, Optional, Sequence

from . import backend as _backend
from .model import version_tuple

Runner = Callable[[Sequence[str], float], Awaitable[tuple[int, str]]]
DIS_FIRMWARE_UUID = "00002a26-0000-1000-8000-00805f9b34fb"
ADDR_RX = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
ADAPTER_RX = re.compile(r"^hci[0-9]{1,2}$")


class BondRefused(Exception):
    """A bond that did not happen, with a stable ``reason`` and a sentence for the household."""

    def __init__(self, reason: str, sentence: str) -> None:
        super().__init__(sentence)
        self.reason = reason
        self.sentence = sentence


def zoe_firmware(fw: str) -> bool:
    """Zoe phase-2 builds carry a 4th version component (``3.0.21.1``); upstream releases use three."""
    v = version_tuple(fw)
    return v is not None and len(v) == 4


def device_path(address: str, adapter: str = "hci0") -> str:
    if not ADDR_RX.match(address.strip()) or not ADAPTER_RX.match(adapter):
        raise ValueError("bad address or adapter")
    return f"/org/bluez/{adapter}/dev_" + address.strip().upper().replace(":", "_")


def parse_busctl_bool(output: str) -> Optional[bool]:
    parts = (output or "").strip().split()
    if len(parts) == 2 and parts[0] == "b" and parts[1] in ("true", "false"):
        return parts[1] == "true"
    return None


def parse_link_mode(output: str, address: str) -> Optional[frozenset]:
    want = address.strip().upper()
    for line in (output or "").splitlines():
        parts = line.split()
        if "LE" in parts and want in (p.upper() for p in parts):
            return frozenset(p.upper() for p in parts[parts.index("lm") + 1:]) if "lm" in parts else frozenset()
    return None


def _set_adapter(adapter_path: str, prop: str, sig: str, value: str) -> list[str]:
    return ["busctl", "--system", "set-property", "org.bluez", adapter_path, "org.bluez.Adapter1", prop, sig, value]


async def _bond_state(path: str, run: Runner) -> tuple[Optional[bool], Optional[bool]]:
    out = []
    for prop in ("Paired", "Bonded"):
        rc, text = await run(["busctl", "--system", "get-property", "org.bluez", path, "org.bluez.Device1", prop], 10.0)
        out.append(parse_busctl_bool(text) if rc == 0 else None)
    return out[0], out[1]


async def _encrypted(address: str, run: Runner, wait_s: float = 6.0) -> bool:
    waited = 0.0
    while True:
        rc, text = await run(["hcitool", "con"], 10.0)
        flags = parse_link_mode(text, address) if rc == 0 else None
        if flags is not None and "ENCRYPT" in flags:
            return True
        if waited >= wait_s:
            return False
        await asyncio.sleep(0.5)
        waited += 0.5


async def bond_pendant(ble: Any, address: str, addr_type: str, *, replace: bool = False, run: Optional[Runner] = None) -> dict:
    """Bond this Pi with the pendant at ``address`` (its pairing window must be open). Returns ``{"bonded", "encrypted",
    "firmware"}``; raises ``BondRefused`` (or the backend's ``ConnectError``) otherwise. The caller holds the radio lock."""
    run = run or _backend._run
    adapter = getattr(ble, "hci", "") or "hci0"
    path = device_path(address, adapter)
    adapter_path = f"/org/bluez/{adapter}"
    if replace:
        await run(["busctl", "--system", "call", "org.bluez", f"/org/bluez/{adapter}", "org.bluez.Adapter1", "RemoveDevice", "o",
                   path], 15.0)
    link = await ble.connect(address, addr_type)
    pairable = False
    try:
        fw = (await link.read_char(DIS_FIRMWARE_UUID)).decode("ascii", "ignore").strip("\x00 ")
        if not zoe_firmware(fw):
            raise BondRefused("stock_firmware", "This pendant still runs the stock firmware. Secure pairing needs Zoe's firmware.")
        await run(_set_adapter(adapter_path, "PairableTimeout", "u", "60"), 10.0)
        rc, _out = await run(_set_adapter(adapter_path, "Pairable", "b", "true"), 10.0)
        pairable = True
        if rc != 0:
            raise BondRefused("adapter", "The panel's Bluetooth would not accept a pairing right now. Try again.")
        try:
            await link.client.pair()
        except Exception as exc:  # noqa: BLE001 - BlueZ says AuthenticationFailed/Rejected/Canceled: the window was shut
            raise BondRefused("pair_failed", "The pendant did not accept pairing. Tap it twice, then twice again until it buzzes, "
                                             "and try again within a minute.") from exc
        paired, bonded = await _bond_state(path, run)
        if not (paired is True and bonded is True and await _encrypted(address, run)):
            raise BondRefused("not_bonded", "Pairing started but the bond did not hold. Try again; if this pendant "
                                             "was paired before, choose to replace the old pairing.")
        return {"bonded": True, "encrypted": True, "firmware": fw}
    finally:
        if pairable:
            await run(_set_adapter(adapter_path, "Pairable", "b", "false"), 10.0)
        await link.disconnect()
