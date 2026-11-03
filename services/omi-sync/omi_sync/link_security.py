"""Bonded-only enforcement for the Zoe phase-2 pendant firmware (``labs/omi-firmware``). READ-ONLY.

Phase-2 firmware serves its storage, audio and settings only over an ENCRYPTED link and accepts a new pairing only while its
button pairing window is open, so after the owner bonds it to this Pi nobody else can read it. This module is the Pi's half of
that promise, and it never pairs, bonds, trusts, unpairs or registers an agent: the bond is created by the owner-run
``omi_bond`` operation in ``services/panel-bluetooth``. It only READS two facts and fails closed on both:

1. before connecting: BlueZ holds a bond for the pendant - ``Device1.Paired`` AND ``Device1.Bonded`` are true
   (``busctl get-property``; a read, no agent). No bond -> no connection attempt at all.
2. after connecting and subscribing (on phase-2 firmware the subscribe itself needs encryption, so BlueZ starts it with the
   stored key): the kernel's LE link to the pendant carries ``ENCRYPT`` in its link mode (``hcitool con``). With no pairing
   agent registered, BlueZ answers a pairing request "not supported", so an encrypted link can only come from the stored key:
   a look-alike that copies the address but not the key is refused before one storage command is sent.

Opt-in per pendant (``OMI_SYNC_REQUIRE_BOND``): stock firmware has nothing to encrypt with, so turning this on before the
pendant is flashed and bonded refuses every visit - fail closed, never a fallback to an open link.
"""
from __future__ import annotations

import asyncio
import re
from typing import Awaitable, Callable, Optional, Sequence

Runner = Callable[[Sequence[str], float], Awaitable[tuple[int, str]]]

ADDR_RX = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
ADAPTER_RX = re.compile(r"^hci[0-9]{1,2}$")


def device_path(address: str, adapter: str = "hci0") -> str:
    """BlueZ's D-Bus object path for a device: ``/org/bluez/hci0/dev_E4_A3_57_6C_8D_0D``."""
    if not ADDR_RX.match(address.strip()) or not ADAPTER_RX.match(adapter):
        raise ValueError("bad address or adapter")
    return f"/org/bluez/{adapter}/dev_" + address.strip().upper().replace(":", "_")


def busctl_argv(path: str, prop: str) -> list[str]:
    return ["busctl", "--system", "get-property", "org.bluez", path, "org.bluez.Device1", prop]


def parse_busctl_bool(output: str) -> Optional[bool]:
    """``b true`` -> True, ``b false`` -> False; anything else (unknown device, no such property, an error) -> None."""
    parts = (output or "").strip().split()
    if len(parts) == 2 and parts[0] == "b" and parts[1] in ("true", "false"):
        return parts[1] == "true"
    return None


def parse_link_mode(output: str, address: str) -> Optional[frozenset]:
    """The link-mode flags of the LE link to ``address`` in ``hcitool con`` output, e.g.
    ``< LE E4:A3:57:6C:8D:0D handle 64 state 1 lm CENTRAL AUTH ENCRYPT`` -> {CENTRAL, AUTH, ENCRYPT}; None = no such link."""
    want = address.strip().upper()
    for line in (output or "").splitlines():
        parts = line.split()
        if "LE" in parts and want in (p.upper() for p in parts):
            return frozenset(p.upper() for p in parts[parts.index("lm") + 1:]) if "lm" in parts else frozenset()
    return None


def bonded(paired: Optional[bool], bonded_: Optional[bool]) -> bool:
    """Both must be READ as true. An unknown (old BlueZ without ``Bonded``, a failed read) is not a bond."""
    return paired is True and bonded_ is True


async def bond_state(address: str, adapter: str, run: Runner) -> tuple[Optional[bool], Optional[bool]]:
    path = device_path(address, adapter)
    out: list[Optional[bool]] = []
    for prop in ("Paired", "Bonded"):
        rc, text = await run(busctl_argv(path, prop), 10.0)
        out.append(parse_busctl_bool(text) if rc == 0 else None)
    return out[0], out[1]


async def link_encrypted(address: str, run: Runner, *, wait_s: float = 6.0, poll_s: float = 0.5) -> bool:
    """True once ``hcitool con`` shows the pendant's link ENCRYPTED; False if it is not within ``wait_s`` (or never listed)."""
    waited = 0.0
    while True:
        rc, text = await run(["hcitool", "con"], 10.0)
        flags = parse_link_mode(text, address) if rc == 0 else None
        if flags is not None and "ENCRYPT" in flags:
            return True
        if waited >= wait_s:
            return False
        await asyncio.sleep(poll_s)
        waited += poll_s
