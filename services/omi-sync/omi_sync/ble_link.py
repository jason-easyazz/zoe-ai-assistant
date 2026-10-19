"""The real pendant link: ``bleak`` (0.22.3, BlueZ) behind the ``engine.RingLink`` interface.

Thin by design - every decision lives in ``engine`` / ``ring_protocol`` where it is tested on
fixture bytes. This module is the one piece that needs a pendant, and it is exercised on hardware
by the operator (``python -m omi_sync check`` is its read-only smoke test).

bleak API used (verified against ``opensrc path pypi:bleak@0.22.3``): ``BleakScanner.discover(timeout,
return_adv=True)``, ``BleakClient(device, disconnected_callback=cb)`` where ``cb(client)`` is a plain
callable, ``client.start_notify(uuid, cb(sender, bytearray))``, ``client.write_gatt_char(uuid, data,
response=True)``, ``client.read_gatt_char(uuid)``. The official Omi SDK never observes disconnects
(BasedHardware/omi #13290), so the disconnect callback here is ours, and a silent link times out.

DO NOT ``pair`` from this module, and register no agent: stock firmware connects and reads with no bond (2026-10-12
bring-up, ``Paired: no``). Phase-2 (Zoe) firmware needs the bond the owner creates with panel-bluetooth's ``omi_bond``; for a
pendant listed in ``OMI_SYNC_REQUIRE_BOND`` ``connect`` only CHECKS it, read-only (``link_security``), and refuses the link
otherwise. Only ONE connection is possible at a time (``CONFIG_BT_MAX_CONN=1``): if the phone app is connected, we cannot be.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from typing import AsyncIterator, Optional, Sequence

from . import legacy_protocol as lp
from . import link_security as sec
from . import ring_protocol as rp
from .engine import LinkDropped
from .radio_lock import RadioLock

log = logging.getLogger("omi_sync.ble")

FIRST_DATA_TIMEOUT_S = 15.0    # Omi's app waits 5 s for the first byte; the Pi's radio is slower to wake
NOTIFY_IDLE_TIMEOUT_S = 10.0
COMMAND_TIMEOUT_S = 6.0


class BleLink:
    def __init__(self, client, queue: "asyncio.Queue[Optional[bytes]]") -> None:
        self._c = client
        self._q = queue

    # --- plain reads ---------------------------------------------------------------
    async def firmware(self) -> str:
        return bytes(await self._c.read_gatt_char(rp.DIS_FIRMWARE_UUID)).decode("ascii", "ignore").strip("\x00 ")

    async def model(self) -> str:
        return bytes(await self._c.read_gatt_char(rp.DIS_MODEL_UUID)).decode("ascii", "ignore").strip("\x00 ")

    async def codec(self) -> int:
        data = await self._c.read_gatt_char(rp.AUDIO_CODEC_UUID)
        return int(data[0]) if data else -1

    async def battery(self) -> Optional[int]:
        try:
            data = await self._c.read_gatt_char(rp.BATTERY_LEVEL_UUID)
            return int(data[0]) if data else None
        except Exception:  # noqa: BLE001
            return None

    async def device_time(self) -> Optional[int]:
        try:
            return rp.parse_time(bytes(await self._c.read_gatt_char(rp.TIME_SYNC_READ_UUID)))
        except Exception as exc:  # noqa: BLE001 - an unreadable clock means "estimate", not "fail"
            log.info("device clock unreadable: %s", type(exc).__name__)
            return None

    async def status(self) -> rp.RingStatus:
        return rp.parse_status(bytes(await self._c.read_gatt_char(rp.STORAGE_STATUS_UUID)))

    # --- storage characteristic ------------------------------------------------------
    def _flush(self) -> None:
        while True:
            try:
                item = self._q.get_nowait()
            except asyncio.QueueEmpty:
                return
            if item is None:                 # the disconnect sentinel is never discarded
                self._q.put_nowait(None)
                return

    async def _get(self, timeout: float) -> bytes:
        try:
            item = await asyncio.wait_for(self._q.get(), timeout)
        except asyncio.TimeoutError as exc:
            raise LinkDropped("no notification within %.0fs" % timeout) from exc
        if item is None:
            self._q.put_nowait(None)
            raise LinkDropped("disconnected")
        return item

    async def _write(self, payload: bytes) -> None:
        try:
            await self._c.write_gatt_char(rp.STORAGE_DATA_UUID, payload, response=True)
        except Exception as exc:  # noqa: BLE001
            raise LinkDropped(f"write failed: {type(exc).__name__}") from exc

    async def info(self) -> bytes:
        self._flush()
        await self._write(rp.encode_info())
        while True:
            note = await self._get(COMMAND_TIMEOUT_S)
            if rp.opcode(note) == rp.NOTIFY_INFO:
                return note
            if rp.opcode(note) == rp.NOTIFY_ACK:       # e.g. STORAGE_NOT_READY while the SD remounts
                raise rp.ProtocolError(f"INFO refused (ack {note[1] if len(note) > 1 else '?'})")

    async def read(self, start_seq: int) -> AsyncIterator[bytes]:
        self._flush()
        await self._write(rp.encode_read(start_seq))
        timeout = FIRST_DATA_TIMEOUT_S
        while True:
            note = await self._get(timeout)
            timeout = NOTIFY_IDLE_TIMEOUT_S
            yield note
            if rp.opcode(note) in (rp.NOTIFY_DONE,):
                return
            if rp.opcode(note) == rp.NOTIFY_ACK:       # an error ack ends the transfer
                return

    async def advance(self, command: bytes) -> bytes:
        self._flush()
        await self._write(command)
        while True:
            note = await self._get(COMMAND_TIMEOUT_S)
            if rp.opcode(note) == rp.NOTIFY_ACK:
                return note

    async def stop(self) -> None:
        with contextlib.suppress(Exception):
            await self._c.write_gatt_char(rp.STORAGE_DATA_UUID, rp.encode_stop(), response=True)

    async def set_time(self, epoch_s: int) -> None:
        await self._c.write_gatt_char(rp.TIME_SYNC_WRITE_UUID, rp.encode_time(epoch_s), response=True)

    # --- the 3.0.18 legacy file protocol (legacy_protocol.py) ----------------------------------
    async def legacy_status(self) -> lp.LegacyStatus:
        return lp.parse_status(bytes(await self._c.read_gatt_char(rp.STORAGE_STATUS_UUID)))

    async def legacy_list(self) -> bytes:
        """``0x10`` LIST_FILES -> the one notification that parses as a file list (read-only on the pendant)."""
        self._flush()
        await self._write(lp.encode_list())
        note = await self._get(COMMAND_TIMEOUT_S)       # the firmware answers LIST with the list itself (no separate ack); 0xFF = error
        lp.parse_file_list(note)                        # raises ProtocolError for an error byte / a malformed list
        return note

    async def legacy_read(self, index: int, offset: int) -> AsyncIterator[bytes]:
        self._flush()
        await self._write(lp.encode_read_file(index, offset))
        timeout = FIRST_DATA_TIMEOUT_S
        while True:
            note = await self._get(timeout)
            timeout = NOTIFY_IDLE_TIMEOUT_S
            yield note
            if len(note) == 1 and note[0] != lp.STATUS_ACK:          # 100 end, 4 empty, anything else is an error: all terminal
                return


async def find_pendant(address: str, *, timeout: float = 10.0):
    """Scan for the bonded pendant by address; ``None`` if it is not in range right now. Read-only: a scan."""
    from bleak import BleakScanner  # type: ignore[import-not-found]
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    want = address.strip().lower()
    for dev, _adv in found.values():
        if (dev.address or "").lower() == want:
            return dev
    return None


class ConnectError(Exception):
    """Every connection attempt failed (pendant out of range, or the link would not hold)."""


class NotBonded(ConnectError):
    """Bonded-only pendant, but BlueZ holds no bond for it: no connection is even attempted."""


class InsecureLink(ConnectError):
    """Bonded-only pendant, but the link did not come up encrypted with the stored key: dropped before any storage command."""


_HANDLE_RX = re.compile(r"handle\s+(\d+)", re.I)


def parse_lecc_handle(output: str) -> Optional[int]:
    """``hcitool lecc`` prints ``Connection handle 64`` on success."""
    m = _HANDLE_RX.search(output or "")
    return int(m.group(1)) if m else None


def lecc_argv(prefix: Sequence[str], address: str, addr_type: str) -> list[str]:
    """The link-creation command: ``hcitool lecc [--random] <addr>``. Its supervision timeout is 32 s (0x0C80) and its interval
    18.75 ms; BlueZ's own default (420 ms) is what killed every attempt at this range (see the plan, section 0.3)."""
    return [*prefix, "hcitool", "lecc", *(["--random"] if addr_type == "random" else []), address]


def lecc_cancel_argv(prefix: Sequence[str]) -> list[str]:
    """LE Create Connection Cancel (HCI OGF 0x08 OCF 0x000E). ``hcitool lecc`` gives up after its own timeout WITHOUT cancelling,
    and the controller keeps initiating forever: every later ``lecc`` is then refused ``Command Disallowed`` (``Input/output
    error``), and when the pendant comes back into range the stale initiation completes into a link nobody owns. Harmless when
    nothing is pending (the controller answers ``Command Disallowed``). Same command ``services/panel-bluetooth`` sends."""
    return [*prefix, "hcitool", "cmd", "0x08", "0x000E"]


def parse_con_handle(output: str, address: str) -> Optional[int]:
    """The handle of an LE link to ``address`` in ``hcitool con`` output (``< LE E4:A3:.. handle 64 state 1 lm CENTRAL``)."""
    want = address.strip().upper()
    for line in (output or "").splitlines():
        parts = line.split()
        if "LE" in parts and want in (p.upper() for p in parts):
            m = _HANDLE_RX.search(line)
            if m:
                return int(m.group(1))
    return None


async def _run(argv: Sequence[str], timeout: float) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "timeout"
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace")


async def _drop_link(prefix: Sequence[str], handle: Optional[int]) -> None:
    if handle is not None:
        with contextlib.suppress(Exception):
            await _run([*prefix, "hcitool", "ledc", str(handle)], 10.0)


async def _cancel_pending(prefix: Sequence[str]) -> None:
    with contextlib.suppress(Exception):
        await _run(lecc_cancel_argv(prefix), 10.0)


async def _existing_link(address: str) -> Optional[int]:
    """An LE link to the pendant that is already up (``hcitool con``, read-only, no privileges)."""
    with contextlib.suppress(Exception):
        rc, out = await _run(["hcitool", "con"], 10.0)
        if rc == 0:
            return parse_con_handle(out, address)
    return None


@contextlib.asynccontextmanager
async def connect(address: str, *, lecc: bool = True, lecc_prefix: Sequence[str] = ("sudo", "-n"), addr_type: str = "random",
                  attempts: int = 5, backoff_s: float = 4.0, require_bond: bool = False,
                  adapter: str = "hci0") -> AsyncIterator[BleLink]:
    """Connect to the pendant and subscribe to its storage characteristic. NO pairing, NO agent, NO bond (the pendant's SMP
    Security Request is answered 'not supported' by BlueZ and the link carries on; its GATT table needs no encryption).

    The recipe that worked 4/4 on the real pendant (2026-10-12 bring-up; the owner's adverts arrive at -88..-96 dBm at the panel):
    create the LE link yourself with a LONG supervision timeout (``hcitool lecc``), THEN attach bleak to the already-connected
    device. A BlueZ-initiated connect uses a 420 ms supervision timeout and died with ``Connection Timeout 0x08`` during service
    discovery 24/24 times. One attempt in six still stalls for a minute: hence retry with backoff, dropping the half-open link
    between tries. ``lecc=False`` is the plain bleak connect (fine on a bench, hopeless at range).

    A failed ``lecc`` is followed by LE Create Connection Cancel, and a link to the pendant that is already up is cleared (cancel,
    then ``ledc``) before our own ``lecc`` (2026-10-15: one ``lecc`` timed out as the owner walked away, the controller kept initiating, every
    later ``lecc`` got ``Input/output error`` for 11 h, and the stale initiation re-created an owner-less link within 110 ms of
    every ``ledc``).

    ``require_bond`` (phase-2 firmware, ``OMI_SYNC_REQUIRE_BOND``): no bond in BlueZ -> ``NotBonded`` before the radio is
    touched; a link that is not ENCRYPTED after the subscribe (or whose bond vanished meanwhile) -> ``InsecureLink``, torn down
    before the caller sees it. Never a fallback to an open link.
    """
    if require_bond and not sec.bonded(*await sec.bond_state(address, adapter, _run)):
        raise NotBonded("refused: no bond with this pendant (pair it from Settings > Bluetooth first)")
    # One radio: while the Settings > Bluetooth agent holds the shared lock (a scan, an open link, a FIRMWARE UPDATE) we do not
    # connect. This is what stops a sync from taking the pendant's single connection in the gap after a firmware reset.
    radio = RadioLock()
    if not radio.try_acquire():
        raise ConnectError(f"radio busy ({radio.current_holder() or 'another Bluetooth task'})")
    try:
        from bleak import BleakClient  # type: ignore[import-not-found]
    except BaseException:
        radio.release()
        raise
    q: "asyncio.Queue[Optional[bytes]]" = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def on_disconnect(_client) -> None:
        loop.call_soon_threadsafe(q.put_nowait, None)

    def on_notify(_sender, data: bytearray) -> None:
        loop.call_soon_threadsafe(q.put_nowait, bytes(data))

    client = None
    handle: Optional[int] = None
    last: Optional[BaseException] = None
    try:
        for attempt in range(1, attempts + 1):
            handle = None
            try:
                if lecc:
                    # A link to the pendant may already be up with no owner (a stale initiation completed - see lecc_cancel_argv).
                    # The controller would ACCEPT another lecc for it and leave that one pending too, and bleak cannot attach to
                    # it (its BlueZ connect scans first, and a connected pendant does not advertise: BleakDeviceNotFoundError
                    # after the full timeout, measured 2026-10-15). So clear it: cancel any pending initiation FIRST (else the
                    # controller re-creates the link within ~110 ms of the ledc), drop the link, then create our own. We hold
                    # the radio lock, so the link is no one else's.
                    stale = await _existing_link(address)
                    if stale is not None:
                        log.info("clearing an existing LE link to the pendant (handle %d) before connecting", stale)
                        await _cancel_pending(lecc_prefix)
                        await _drop_link(lecc_prefix, stale)
                    rc, out = await _run(lecc_argv(lecc_prefix, address, addr_type), 30.0)
                    handle = parse_lecc_handle(out) if rc == 0 else None
                    if handle is None:
                        await _cancel_pending(lecc_prefix)      # never leave the controller initiating behind us
                        raise ConnectError(f"hcitool lecc failed (rc={rc}): {out.strip()[:120]}")
                client = BleakClient(address, disconnected_callback=on_disconnect, timeout=60.0)
                try:
                    await client.connect()
                except Exception as exc:  # noqa: BLE001 - device unknown to BlueZ's cache: one scan, then connect with the device object
                    if type(exc).__name__ != "BleakDeviceNotFoundError":
                        raise
                    dev = await find_pendant(address, timeout=10.0)
                    if dev is None:
                        raise
                    client = BleakClient(dev, disconnected_callback=on_disconnect, timeout=60.0)
                    await client.connect()
                await client.start_notify(rp.STORAGE_DATA_UUID, on_notify)
                break
            except Exception as exc:  # noqa: BLE001
                last = exc
                log.info("connect attempt %d/%d failed: %s: %s", attempt, attempts, type(exc).__name__, str(exc)[:120])
                if client is not None:
                    with contextlib.suppress(Exception):
                        await client.disconnect()
                await _drop_link(lecc_prefix, handle)
                client, handle = None, None     # dropped: a cancel during the backoff must not ledc a reused handle
                if attempt < attempts:
                    await asyncio.sleep(backoff_s * attempt)
    except BaseException:           # cancelled mid-connect (an Exception is handled per attempt): leave nothing behind
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()
        await _drop_link(lecc_prefix, handle)
        if lecc:
            await _cancel_pending(lecc_prefix)
        radio.release()
        raise
    if client is None:
        radio.release()
        raise ConnectError(f"could not connect after {attempts} attempts: {type(last).__name__ if last else 'n/a'}") from last
    try:
        if require_bond:
            if not await sec.link_encrypted(address, _run):
                raise InsecureLink("refused: the link to the pendant is not encrypted with the bond")
            if not sec.bonded(*await sec.bond_state(address, adapter, _run)):
                raise InsecureLink("refused: the bond disappeared while connecting")
        yield BleLink(client, q)
    finally:
        with contextlib.suppress(Exception):
            if client.is_connected:
                await client.stop_notify(rp.STORAGE_DATA_UUID)
        with contextlib.suppress(Exception):
            await client.disconnect()
        await _drop_link(lecc_prefix, handle)
        radio.release()                  # only after the link is really down: the next holder may connect at once
