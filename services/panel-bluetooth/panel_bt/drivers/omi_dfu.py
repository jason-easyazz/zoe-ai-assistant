"""Omi CV1 firmware update: the safety gates and the staged procedure. Hardware-free: the SMP side is an injected ``SmpSession``.

READ THIS BEFORE CHANGING ANYTHING. Facts from the supervised 2026-10-12 attempt (it stopped safely at the signal gate, nothing was
written; record: ``docs/knowledge/bluetooth-settings.md``):

* The pendant's bootloader is MCUboot in **OVERWRITE-ONLY** mode (``SB_CONFIG_MCUBOOT_MODE_OVERWRITE_ONLY=y``, downgrade prevention on,
  multi-image net-core update). After the reset MCUboot COPIES the new image over the old one. There is **no revert and no test mode**:
  "test" and "confirm" are the same thing, and a power loss during the copy is **unrecoverable over Bluetooth**. Hence the gates:
  the pendant must be ON ITS CHARGER (``19b10013`` = 1), battery >= 50 %, and the advert signal strong and STABLE (median better than
  -75 dBm over >= 5 samples).
* The OTA zip holds TWO images: 0 = app core (``omi.signed.bin``), 1 = net-core radio (``ipc_radio.bin``). Both are uploaded and BOTH
  secondary-slot hashes are verified against the zip before anything is marked; the reset is sent only if exactly two images are pending.
* Omi's own app also sends "erase app settings" (the Zephyr settings partition). We DO NOT: it may change the static-random address
  the sync worker uses, and the bootloader does not need it.
* The procedure is staged and **stops on the first doubt, never retries**: info -> upload (no reset) -> mark -> reset -> post (wait for
  the advert up to 300 s, expect the pinned version, confirm if still unconfirmed). A failure starts a cool-down (``ota_cooldown_s``); a
  second attempt is always a fresh, explicit request.
* UNVERIFIED ON HARDWARE: ``upload`` / ``mark`` / ``reset`` / ``post`` have only been exercised against a fake session. The real
  session (``smp_session.py``) is a port of the operator's staged tool and was written against the smpclient 7.3.0 source. Treat the
  first real run as supervised.
"""
from __future__ import annotations

import asyncio
import json
import logging
import statistics
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Protocol

from ..backend import ConnectError
from ..model import (E_CANCELLED, E_CONNECT, E_GATE, E_OTA_OFF, E_UPDATE, AgentError, version_tuple)
from .base import UpdateContext, read_standard_info
from .omi_release import Pin, ReleaseError, fetch_package, load_pin, parse_package

log = logging.getLogger("panel_bt.omi_dfu")

OMI_CHARGING_UUID = "19b10013-e8f2-537e-4f6c-d104768a1214"      # read: 0x01 = charging (omi_bringup_2026-10-12.md)
MIN_RSSI_SAMPLES = 5
MAX_RSSI_SPREAD_DB = 15
RSSI_SAMPLE_S = 12.0
POST_WAIT_S = 300.0
POST_SETTLE_S = 3.0

SIGNAL_HINT = ("Move the pendant closer - right next to the panel - or use a USB Bluetooth adapter on the panel - the panel's built-in radio is "
               "weak (about -91 dBm even beside it).")


# --- the SMP side (real impl: smp_session.py) ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SlotInfo:
    image: Optional[int]
    slot: int
    version: str
    hash: Optional[str]            # lowercase hex
    active: bool = False
    confirmed: bool = False
    pending: bool = False
    permanent: bool = False
    bootable: bool = True


class SmpSession(Protocol):
    async def open(self) -> None: ...
    async def close(self) -> None: ...
    async def bootloader_mode(self) -> Optional[int]: ...
    async def images(self) -> list: ...
    async def upload(self, image_index: int, data: bytes, on_offset: Callable[[int], Awaitable[None]]) -> None: ...
    async def mark_permanent(self, image_hash: bytes) -> None: ...
    async def reset(self) -> None: ...


# --- gates --------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Gate:
    id: str
    ok: bool
    label: str
    detail: str = ""
    hint: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class GateInputs:
    ota_enabled: bool
    model: Optional[str]
    firmware: Optional[str]
    battery: Optional[int]
    charging: Optional[bool]
    rssi_samples: Optional[list]            # None = not measured yet (update_check); [] = measured, heard nothing
    cooldown_remaining_s: float = 0.0
    other_job: bool = False


def rssi_summary(samples: Optional[list]) -> dict:
    if not samples:
        return {"n": 0, "median": None, "spread": None}
    return {"n": len(samples), "median": int(statistics.median(samples)), "spread": max(samples) - min(samples)}


def evaluate_gates(g: GateInputs, pin: Pin, *, min_battery: int, min_rssi: int) -> list:
    """Every precondition of an update, as data. ``all(gate.ok)`` is the licence to start; any failure carries a plain reason."""
    gates: list[Gate] = []
    gates.append(Gate("ota_enabled", g.ota_enabled, "Updates are switched on for this panel",
                      "" if g.ota_enabled else "Firmware updates are off on this panel (PANEL_BT_OTA_ENABLED).",
                      "" if g.ota_enabled else "An operator has to switch updates on for this panel first."))
    model_ok = g.model is not None and g.model == pin.model
    gates.append(Gate("model", model_ok, "This is an Omi CV1 pendant",
                      "" if model_ok else f"Reports '{g.model or 'unknown'}', the pinned firmware is for '{pin.model}'."))
    cur, new, minimum = version_tuple(g.firmware), pin.version_tuple, version_tuple(pin.minimum_firmware)
    if cur is None:
        gates.append(Gate("firmware", False, "Current firmware is known", "The pendant's firmware version could not be read."))
    elif cur >= new:
        gates.append(Gate("firmware", False, "A newer firmware is available", f"Already on {g.firmware}; the pinned release is {pin.version}."))
    elif minimum and cur < minimum:
        gates.append(Gate("firmware", False, "Current firmware can be updated directly",
                          f"{g.firmware} is older than the {pin.minimum_firmware} the release needs; update it in the Omi app first."))
    else:
        gates.append(Gate("firmware", True, f"Update available: {g.firmware} to {pin.version}"))
    ch_ok = g.charging is True
    gates.append(Gate("charging", ch_ok, "On its charger",
                      "" if ch_ok else ("The pendant is not charging." if g.charging is False else "Charging state could not be read."),
                      "" if ch_ok else "Put the pendant on its charger and leave it there: a power cut during the install cannot be undone."))
    b_ok = g.battery is not None and g.battery >= min_battery
    gates.append(Gate("battery", b_ok, f"Battery at least {min_battery}%",
                      "" if b_ok else (f"Battery is {g.battery}%." if g.battery is not None else "Battery level could not be read."),
                      "" if b_ok else "Charge it first."))
    s = rssi_summary(g.rssi_samples)
    if g.rssi_samples is None:
        gates.append(Gate("signal", False, f"Strong, steady signal (better than {min_rssi} dBm)",
                          "Signal is measured when you press Update.", SIGNAL_HINT))
    elif s["n"] < MIN_RSSI_SAMPLES:
        gates.append(Gate("signal", False, f"Strong, steady signal (better than {min_rssi} dBm)",
                          f"Only heard the pendant {s['n']} time(s) in {int(RSSI_SAMPLE_S)} s.", SIGNAL_HINT))
    elif s["spread"] > MAX_RSSI_SPREAD_DB:
        gates.append(Gate("signal", False, f"Strong, steady signal (better than {min_rssi} dBm)",
                          f"The signal is unsteady ({s['spread']} dB swing).", SIGNAL_HINT))
    elif not (s["median"] > min_rssi):
        gates.append(Gate("signal", False, f"Strong, steady signal (better than {min_rssi} dBm)",
                          f"Median signal is {s['median']} dBm.", SIGNAL_HINT))
    else:
        gates.append(Gate("signal", True, f"Strong, steady signal ({s['median']} dBm)"))
    gates.append(Gate("cooldown", g.cooldown_remaining_s <= 0, "No recent failed attempt",
                      "" if g.cooldown_remaining_s <= 0 else f"A previous attempt failed; wait {int(g.cooldown_remaining_s // 60) + 1} minute(s).",
                      "" if g.cooldown_remaining_s <= 0 else "Retries are never automatic."))
    gates.append(Gate("idle", not g.other_job, "Nothing else is using the radio",
                      "" if not g.other_job else "Another Bluetooth task is running on this panel."))
    return gates


def failed_gates(gates: list) -> list:
    return [x for x in gates if not x.ok]


def gate_error(gates: list) -> AgentError:
    bad = failed_gates(gates)
    first = bad[0]
    msg = f"{first.label}: {first.detail}".strip(": ") + (f" {first.hint}" if first.hint else "")
    return AgentError(E_GATE, msg.strip(), detail={"gates": [x.as_dict() for x in gates]})


# --- cool-down state ----------------------------------------------------------------------------------------------------
class OtaState:
    """When the last attempt failed. Survives a restart so a crash-looping service can never turn into a retry loop."""

    def __init__(self, path: Path, cooldown_s: float, clock: Callable[[], float] = time.time) -> None:
        self.path, self.cooldown_s, self.clock = Path(path), cooldown_s, clock

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def record_failure(self, device_id: str, reason: str) -> None:
        doc = self._load()
        doc["last_failure"] = {"device_id": device_id, "at": self.clock(), "reason": reason[:200]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        tmp.replace(self.path)

    def remaining_s(self) -> float:
        last = self._load().get("last_failure") or {}
        at = last.get("at")
        if not isinstance(at, (int, float)):
            return 0.0
        return max(0.0, self.cooldown_s - (self.clock() - at))


# --- the procedure ------------------------------------------------------------------------------------------------------
@dataclass
class UpdateDeps:
    smp_factory: Callable[[Any], SmpSession]
    pin: Optional[Pin] = None
    fetch: Callable[..., bytes] = fetch_package
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    clock: Callable[[], float] = time.monotonic
    state: Optional[OtaState] = None


class _Cancelled(Exception):
    pass


async def read_omi_facts(link: Any) -> dict:
    facts = await read_standard_info(link)
    facts["charging"] = None
    if link.has_char(OMI_CHARGING_UUID):
        try:
            raw = await link.read_char(OMI_CHARGING_UUID)
            facts["charging"] = bool(raw and raw[0] == 1)
        except Exception:  # noqa: BLE001
            pass
    return facts


async def run_omi_update(u: UpdateContext, deps: UpdateDeps) -> dict:
    """Run one update attempt. Raises ``AgentError`` on the first doubt; never retries; always records a failure's cool-down."""
    s = u.settings
    if not s.ota_enabled:
        raise AgentError(E_OTA_OFF, "Firmware updates are switched off on this panel.")
    try:
        pin = deps.pin or load_pin(s.ota_pin_file)
    except ReleaseError as exc:
        raise AgentError(E_UPDATE, f"The pinned firmware release is unusable: {exc}") from exc
    state = deps.state or OtaState(s.ota_state_path, s.ota_cooldown_s)
    reset_sent = False
    session: Optional[SmpSession] = None
    link: Optional[Any] = None

    async def step(name: str, pct: int, msg: str) -> None:
        if u.cancel.is_set() and not reset_sent and name in ("preflight", "download", "upload", "verify"):
            raise _Cancelled()
        await u.progress(name, pct, msg)

    async def close_all() -> None:
        nonlocal session, link
        if session is not None:
            try:
                await asyncio.wait_for(session.close(), 15)
            except Exception as exc:  # noqa: BLE001
                log.info("smp close: %s", type(exc).__name__)
            session = None
        if link is not None:
            try:
                await link.disconnect()
            except Exception:  # noqa: BLE001
                pass
            link = None

    try:
        try:
            # 1. preflight: free the radio of our own idle link, measure the signal while the pendant is advertising.
            await step("preflight", 2, "Checking the pendant is ready")
            if u.release_link is not None:
                await u.release_link()
            samples = await u.backend.sample_rssi(u.address, RSSI_SAMPLE_S)
            pre = evaluate_gates(GateInputs(True, pin.model, "0.0.0", 100, True, samples, state.remaining_s()), pin,
                                 min_battery=s.ota_min_battery, min_rssi=s.ota_min_rssi)
            early = [g for g in pre if g.id in ("signal", "cooldown")]    # (the other gates ran on placeholders; see below)
            if failed_gates(early):
                raise gate_error(early)
            # 2. download + verify the pinned package BEFORE touching the pendant.
            await step("download", 8, "Fetching the official firmware")
            try:
                data = await asyncio.to_thread(deps.fetch, pin, s.cache_dir)
                images = parse_package(data, pin)
            except ReleaseError as exc:
                raise AgentError(E_UPDATE, f"The firmware package failed verification, nothing was changed: {exc}") from exc
            await step("verify", 12, "Firmware verified against the pinned SHA-256")
            # 3. link up, read the facts the remaining gates need.
            await step("connect", 15, "Connecting to the pendant")
            link = await u.backend.connect(u.address, u.addr_type)
            facts = await read_omi_facts(link)
            gates = evaluate_gates(GateInputs(True, facts.get("model"), facts.get("firmware"), facts.get("battery"),
                                              facts.get("charging"), samples, state.remaining_s()), pin,
                                   min_battery=s.ota_min_battery, min_rssi=s.ota_min_rssi)
            if failed_gates(gates):
                raise gate_error(gates)
            from_version = facts.get("firmware")
            session = deps.smp_factory(link)
            await session.open()
            mode = await session.bootloader_mode()
            before = await session.images()
            if any(getattr(r, "slot", 0) == 1 and (r.pending or r.permanent) for r in before):
                raise AgentError(E_UPDATE, "A secondary firmware slot is already waiting to install. Nothing was touched.")
            # 4. upload both images; SMP resumes from the last good offset, but we never auto-retry.
            total = sum(i.size for i in images)
            done_before = 0
            for im in images:
                await step("upload", 15 + int(65 * done_before / total), f"Uploading firmware part {im.index + 1} of {len(images)}")

                async def on_offset(off: int, _im=im, _base=done_before) -> None:
                    if u.cancel.is_set():
                        raise _Cancelled()
                    pct = 15 + int(65 * (_base + off) / total)
                    await u.progress("upload", pct, f"Uploading firmware part {_im.index + 1} of {len(images)}")

                await session.upload(im.index, im.data, on_offset)
                done_before += im.size
            # 5. both secondary hashes must equal the zip's before anything is marked.
            await step("verify", 82, "Checking the uploaded copies")
            after = await session.images()
            for im in images:
                ok = any(getattr(r, "slot", 0) == 1 and r.hash == im.hash.hex() and r.image in (im.index, None) for r in after)
                if not ok:
                    raise AgentError(E_UPDATE, f"The pendant's copy of firmware part {im.index + 1} does not match the official file. "
                                               "Nothing was installed.")
            # ---- point of no return: from here the pendant will install at its next reset (overwrite-only) --------------
            await step("install", 86, "Telling the pendant to install")
            for im in images:
                await session.mark_permanent(im.hash)
            marked = await session.images()
            pend = [r for r in marked if getattr(r, "slot", 0) == 1 and r.pending]
            if len(pend) != len(images):
                raise AgentError(E_UPDATE, f"Expected {len(images)} firmware parts waiting to install, found {len(pend)}. Not restarting it.")
            # last look before the irreversible step
            facts2 = await read_omi_facts(link)
            if facts2.get("charging") is not True or (facts2.get("battery") or 0) < s.ota_min_battery:
                raise AgentError(E_GATE, "The pendant stopped charging or its battery dropped just before the install. Not restarting it; "
                                         "it will install on its own next time it restarts.")
            await step("reset", 90, "Restarting the pendant. Do not move or unplug it for 2 minutes.")
            reset_sent = True
            try:
                await session.reset()
            except Exception as exc:  # noqa: BLE001 - the link dropping IS the reset taking effect
                log.info("reset: %s", type(exc).__name__)
            await close_all()
            # 6. post: wait for the advert, reconnect, check the version, confirm if still unconfirmed.
            await step("waiting", 92, "Waiting for the pendant to come back. Do not move or unplug it for 2 minutes.")
            waited, back = 0.0, False
            while waited < POST_WAIT_S:
                heard = await u.backend.sample_rssi(u.address, 10.0)
                waited += 10.0
                if heard:
                    back = True
                    break
            if not back:
                raise AgentError(E_UPDATE, "The pendant did not come back within 5 minutes. Leave it on its charger, untouched, and check "
                                           "again later. If it stays silent it needs a cable recovery.")
            await deps.sleep(POST_SETTLE_S)
            link = await u.backend.connect(u.address, u.addr_type)
            post = await read_omi_facts(link)
            if post.get("firmware") != pin.version:
                raise AgentError(E_UPDATE, f"The pendant reports firmware {post.get('firmware')}, expected {pin.version}.")
            await step("confirm", 97, "Confirming the new firmware")
            session = deps.smp_factory(link)
            await session.open()
            for r in await session.images():
                if getattr(r, "slot", 0) == 0 and r.active and not r.confirmed and r.hash:
                    await session.mark_permanent(bytes.fromhex(r.hash))
            await u.progress("done", 100, f"Updated to {pin.version}")
            return {"outcome": "updated", "from": from_version, "to": pin.version, "bootloader_mode": mode,
                    "images": [{"index": i.index, "bytes": i.size} for i in images]}
        except _Cancelled:
            raise AgentError(E_CANCELLED, "Update cancelled before anything was installed. The pendant is unchanged.") from None
        except AgentError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - first doubt: stop, say what state we are in
            if isinstance(exc, ConnectError) and not reset_sent:
                raise AgentError(E_CONNECT, "Could not reach the pendant, so nothing was changed. " + SIGNAL_HINT) from exc
            where = "after the restart command" if reset_sent else "before anything was installed"
            raise AgentError(E_UPDATE, f"The update stopped {where} ({type(exc).__name__}). "
                                       + ("Leave the pendant on its charger and check it again in a few minutes."
                                          if reset_sent else "The pendant is unchanged."),
                             detail={"stage_reset_sent": reset_sent}) from exc
    except AgentError as err:
        if err.code == E_UPDATE:           # an attempt that actually ran and failed starts the cool-down; a refused gate does not
            state.record_failure(u.device_id, err.message)
        raise
    finally:
        await close_all()
