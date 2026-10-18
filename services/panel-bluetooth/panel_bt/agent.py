"""The panel Bluetooth agent: executes zoe-data's commands against the local radio and reports back.

Shape: PULL, not push. The agent opens NO listening socket. It long-polls ``POST /api/bluetooth/agent/poll`` (device token), runs what
comes back, and posts outcomes to ``/agent/report``. So there is nothing on the LAN to authenticate, and zoe-data never needs the
panel's address (which also sidesteps the panel agent on :8765, which is unauthenticated and not part of this repo).

One radio: every operation that touches it holds the shared ``RadioLock`` (also taken by ``omi-sync``). A link is a LEASE: it is
dropped ``lease_s`` after the last ``status`` / ``info`` touch from the UI, and in any case ``max_hold_s`` after it opened - so a
forgotten "Connected" can never starve the pendant's recording sync. An update holds the radio until it finishes.

Never touches zoe-voice or audio: this process has no audio code, no PulseAudio/ALSA access, and does not import the voice daemon.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import VERSION
from .backend import BleBackend, BleLink, ConnectError
from .config import Settings
from .drivers import DriverRegistry
from .drivers.base import Driver, UpdateContext
from .known import KnownStore
from .bond import BondRefused, bond_pendant
from .model import (ADDR_RX, E_BAD_REQUEST, E_BOND, E_BOND_OFF, E_BUSY, E_CANCELLED, E_CONNECT, E_INTERNAL, E_NOT_CONNECTED,
                    E_NOT_FOUND, E_OTA_OFF, E_UNSUPPORTED, Advert, AgentError, Command, parse_command)
from .radio_lock import RadioLock
from .zoe_link import ZoeLink, ZoeUnavailable

log = logging.getLogger("panel_bt.agent")

IMMEDIATE_OPS = frozenset({"status", "update_cancel"})
MAX_SCAN_RESULTS = 40
LOCK_WAIT_S = 20.0
PROGRESS_MIN_INTERVAL_S = 1.5
SEEN_JOBS_MAX = 200


@dataclass
class LiveLink:
    device_id: str
    link: Any
    driver: Driver
    opened_at: float
    lease_until: float
    info: dict = field(default_factory=dict)


class PanelBluetoothAgent:
    def __init__(self, settings: Settings, backend: BleBackend, registry: DriverRegistry, zoe: Optional[ZoeLink] = None, *,
                 known: Optional[KnownStore] = None, lock: Optional[RadioLock] = None, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Any] = asyncio.sleep) -> None:
        self.s = settings
        self.backend = backend
        self.registry = registry
        self.zoe = zoe
        self.known = known or KnownStore(settings.known_path)
        self.lock = lock or RadioLock(settings.radio_lock)
        self.clock = clock
        self._sleep = sleep
        self.scan_cache: dict[str, Advert] = {}
        self.link: Optional[LiveLink] = None
        self.busy_job: Optional[str] = None
        self.busy_op: Optional[str] = None
        self._cancel: Optional[asyncio.Event] = None
        self._rssi_samples: dict[str, list] = {}
        self._outbox: list[dict] = []
        self._seen: list[str] = []
        self._last_progress = 0.0
        self._last_step = ""
        self._stop = asyncio.Event()

    # ---------------------------------------------------------------- snapshot (the heartbeat) -----------------
    def snapshot(self) -> dict:
        now = self.clock()
        devices = []
        for did, k in self.known.all().items():
            drv = self.registry.by_name(k.get("driver", "")) or self.registry.by_name("generic")
            live = self.link is not None and self.link.device_id == did and self._link_alive()
            devices.append({"id": did, "name": k.get("name", ""), "driver": drv.name if drv else "generic",
                            "label": drv.label if drv else "Bluetooth device", "connected": bool(live),
                            "lease_s": int(max(0, self.link.lease_until - now)) if live else 0,
                            "supports_update": bool(drv and drv.supports_update)})
        holder = "" if self.lock.held else self.lock.current_holder()
        return {"agent": {"version": VERSION, "ota_enabled": self.s.ota_enabled, "bond_enabled": self.s.bond_enabled,
                          "hci": self.s.hci or "default"},
                "busy": {"job_id": self.busy_job, "op": self.busy_op} if self.busy_job else None,
                "radio_holder": holder, "devices": devices}

    def _link_alive(self) -> bool:
        try:
            return bool(self.link and self.link.link.is_connected)
        except Exception:  # noqa: BLE001
            return False

    # ---------------------------------------------------------------- command entry ---------------------------
    async def execute(self, raw: Any) -> None:
        """Run one command from zoe-data and queue its report. Never raises."""
        job_id = raw.get("job_id") if isinstance(raw, dict) else None
        try:
            cmd = parse_command(raw)
        except AgentError as err:
            if isinstance(job_id, str):
                self._queue({"job_id": job_id, "state": "failed", "error": err.as_dict()})
            return
        if cmd.job_id in self._seen:
            return                                       # a re-delivered command is not run twice
        self._seen.append(cmd.job_id)
        del self._seen[:-SEEN_JOBS_MAX]
        try:
            if cmd.op not in IMMEDIATE_OPS:
                if self.busy_job:
                    raise AgentError(E_BUSY, "This panel is busy with another Bluetooth task. Try again in a moment.")
                self.busy_job, self.busy_op = cmd.job_id, cmd.op
            await self._emit(cmd.job_id, "running", {"step": cmd.op, "pct": 0, "message": ""})
            result = await getattr(self, f"_op_{cmd.op}")(cmd)
            self._queue({"job_id": cmd.job_id, "state": "succeeded", "result": result})
        except AgentError as err:
            self._queue({"job_id": cmd.job_id, "state": "cancelled" if err.code == E_CANCELLED else "failed", "error": err.as_dict()})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("job %s (%s) crashed", cmd.job_id, cmd.op)
            self._queue({"job_id": cmd.job_id, "state": "failed",
                         "error": {"code": E_INTERNAL, "message": f"Something went wrong on the panel ({type(exc).__name__})."}})
        finally:
            if self.busy_job == cmd.job_id:
                self.busy_job = self.busy_op = None
                self._cancel = None
                if self.link is None:
                    self.lock.release()
        await self.flush_outbox()

    # ---------------------------------------------------------------- reporting ------------------------------
    def _queue(self, payload: dict) -> None:
        self._outbox.append(payload)

    async def _emit(self, job_id: str, state: str, progress: dict) -> None:
        if self.zoe is None:
            return
        with contextlib.suppress(ZoeUnavailable):
            await self.zoe.report({"job_id": job_id, "state": state, "progress": progress})

    async def flush_outbox(self) -> None:
        """Send queued outcomes; whatever does not get through stays queued for the next poll (a final report must not be lost)."""
        if self.zoe is None:
            return
        keep: list[dict] = []
        for p in self._outbox:
            try:
                await self.zoe.report(p)
            except ZoeUnavailable:
                keep.append(p)
        self._outbox = keep[-50:]

    def _progress_fn(self, job_id: str):
        async def progress(step: str, pct: int, message: str) -> None:
            now = self.clock()
            if step == self._last_step and now - self._last_progress < PROGRESS_MIN_INTERVAL_S and pct < 100:
                return
            self._last_step, self._last_progress = step, now
            await self._emit(job_id, "running", {"step": step, "pct": int(pct), "message": message})
        return progress

    # ---------------------------------------------------------------- helpers --------------------------------
    async def _need_radio(self, timeout_s: float = LOCK_WAIT_S) -> None:
        if self.lock.held:
            return
        if not await self.lock.acquire(timeout_s):
            holder = self.lock.current_holder()
            who = "The pendant is syncing recordings right now" if holder == "omi-sync" else "Bluetooth is in use right now"
            raise AgentError(E_BUSY, f"{who}. Try again in a minute.")

    def _resolve(self, dev_id: str) -> tuple[str, str, str, Driver]:
        """(address, addr_type, name, driver) for an id: the live scan cache first, then the known-devices file."""
        adv = self.scan_cache.get(dev_id)
        if adv is not None:
            return adv.address, adv.addr_type, adv.name, self.registry.identify(adv)
        k = self.known.get(dev_id)
        if k is not None:
            drv = self.registry.by_name(k.get("driver", "")) or self.registry.by_name("generic")
            return k["address"], k.get("addr_type", "random"), k.get("name", ""), drv  # type: ignore[return-value]
        raise AgentError(E_NOT_FOUND, "That device is not in the list any more. Scan again.")

    async def _drop_link(self) -> None:
        ll, self.link = self.link, None
        if ll is not None:
            with contextlib.suppress(Exception):
                await ll.link.disconnect()
        if self.busy_job is None:
            self.lock.release()

    def _touch(self) -> None:
        if self.link is not None:
            self.link.lease_until = min(self.clock() + self.s.lease_s, self.link.opened_at + self.s.max_hold_s)

    def _need_link(self, dev_id: str) -> LiveLink:
        if self.link is None or self.link.device_id != dev_id or not self._link_alive():
            raise AgentError(E_NOT_CONNECTED, "Not connected to that device. Connect it first.")
        return self.link

    def _ctx(self, dev_id: str) -> dict:
        return {"settings": self.s, "rssi_samples": self._rssi_samples.get(dev_id), "other_job": False}

    # ---------------------------------------------------------------- operations -----------------------------
    async def _op_status(self, cmd: Command) -> dict:
        if self.link is not None and not self._link_alive():
            await self._drop_link()
        self._touch()
        return self.snapshot()

    async def _op_scan(self, cmd: Command) -> dict:
        await self._need_radio()
        adverts = await self.backend.scan(self.s.scan_s)      # (the radio lock is released by execute()'s finally when no link is open)
        self.scan_cache = {a.id: a for a in adverts}
        known = self.known.all()
        rows, hidden = [], 0
        for a in adverts:
            drv = self.registry.identify(a)
            if not a.name and drv.name == "generic":
                hidden += 1
                continue
            rows.append({"id": a.id, "name": a.name or "Unnamed device", "rssi": a.rssi, "driver": drv.name, "label": drv.label,
                         "specific_driver": drv.known, "remembered": a.id in known,
                         "connected": bool(self.link and self.link.device_id == a.id and self._link_alive())})
        rows.sort(key=lambda r: (not r["specific_driver"], -(r["rssi"] if r["rssi"] is not None else -999)))
        return {"devices": rows[:MAX_SCAN_RESULTS], "hidden_unnamed": hidden, "scanned_s": self.s.scan_s}

    async def _op_connect(self, cmd: Command) -> dict:
        address, addr_type, name, drv = self._resolve(cmd.device_id)  # type: ignore[arg-type]
        if not ADDR_RX.match(address):
            raise AgentError(E_BAD_REQUEST, "bad device address")
        if self.link is not None and self.link.device_id == cmd.device_id and self._link_alive():
            self._touch()
            self.link.info = await drv.info(self.link.link, self._ctx(cmd.device_id))
            return self._connected_result(self.link, name)
        if self.link is not None:
            await self._drop_link()                     # one live link at a time: connecting another releases the first
        await self._need_radio()
        try:
            link = await self.backend.connect(address, addr_type)
        except ConnectError as exc:
            raise AgentError(E_CONNECT, "Could not connect. Make sure it is switched on, in range and not connected to a phone.",
                             detail={"reason": str(exc)[:120]}) from exc
        try:
            info = await drv.info(link, self._ctx(cmd.device_id))
        except Exception as exc:  # noqa: BLE001 - connected but unreadable: do not leave a half-open link
            with contextlib.suppress(Exception):
                await link.disconnect()
            raise AgentError(E_CONNECT, "Connected, but the device did not answer. Try again.", detail={"reason": type(exc).__name__}) from exc
        now = self.clock()
        self.link = LiveLink(cmd.device_id, link, drv, now, min(now + self.s.lease_s, now + self.s.max_hold_s), info)
        self.known.add(address, addr_type, name, drv.name)
        return self._connected_result(self.link, name)

    def _connected_result(self, ll: LiveLink, name: str) -> dict:
        return {"connected": True, "name": name, "driver": ll.driver.name, "label": ll.driver.label, "info": ll.info,
                "lease_s": int(max(0, ll.lease_until - self.clock())), "supports_update": ll.driver.supports_update}

    async def _op_disconnect(self, cmd: Command) -> dict:
        if self.link is not None and self.link.device_id == cmd.device_id:
            await self._drop_link()
        return {"connected": False}

    async def _op_forget(self, cmd: Command) -> dict:
        if self.link is not None and self.link.device_id == cmd.device_id:
            await self._drop_link()
        removed = self.known.remove(cmd.device_id)
        self.scan_cache.pop(cmd.device_id, None)
        self._rssi_samples.pop(cmd.device_id, None)
        return {"forgotten": True, "was_remembered": removed}

    async def _op_info(self, cmd: Command) -> dict:
        ll = self._need_link(cmd.device_id)             # type: ignore[arg-type]
        self._touch()
        ll.info = await ll.driver.info(ll.link, self._ctx(ll.device_id))
        return {"info": ll.info, "driver": ll.driver.name, "label": ll.driver.label, "lease_s": int(max(0, ll.lease_until - self.clock())),
                "supports_update": ll.driver.supports_update}

    async def _op_update_check(self, cmd: Command) -> dict:
        ll = self._need_link(cmd.device_id)             # type: ignore[arg-type]
        self._touch()
        ll.info = await ll.driver.info(ll.link, self._ctx(ll.device_id))
        return await ll.driver.update_check(ll.info, self._ctx(ll.device_id))

    async def _op_update_start(self, cmd: Command) -> dict:
        if not self.s.ota_enabled:
            raise AgentError(E_OTA_OFF, "Firmware updates are switched off on this panel.")
        if cmd.params.get("confirm") is not True or cmd.params.get("ack_no_undo") is not True:
            raise AgentError(E_BAD_REQUEST, "The update must be confirmed, including that it cannot be undone.")
        address, addr_type, _name, drv = self._resolve(cmd.device_id)  # type: ignore[arg-type]
        if not drv.supports_update:
            raise AgentError(E_UNSUPPORTED, "This device type cannot be updated from here.")
        await self._need_radio()
        self._cancel = asyncio.Event()
        ll = self.link if (self.link and self.link.device_id == cmd.device_id) else None

        async def release_link() -> None:
            if self.link is not None:                   # the lock stays held: the update keeps the radio until it is finished
                ll_, self.link = self.link, None
                with contextlib.suppress(Exception):
                    await ll_.link.disconnect()

        uctx = UpdateContext(settings=self.s, backend=self.backend, address=address, addr_type=addr_type, device_id=cmd.device_id,  # type: ignore[arg-type]
                             progress=self._progress_fn(cmd.job_id), cancel=self._cancel,
                             link=ll.link if ll else None, release_link=release_link)
        try:
            return await asyncio.wait_for(drv.run_update(uctx), self.s.ota_max_s)
        except asyncio.TimeoutError as exc:
            raise AgentError(E_INTERNAL, "The update took far too long and was stopped. Check the pendant before trying again.") from exc

    async def _op_omi_bond(self, cmd: Command) -> dict:
        """Owner-run, physical: bond this Pi with a pendant on Zoe phase-2 firmware whose pairing window is open (bond.py)."""
        if not self.s.bond_enabled:
            raise AgentError(E_BOND_OFF, "Secure pairing is switched off on this panel.")
        if cmd.params.get("confirm") is not True:
            raise AgentError(E_BAD_REQUEST, "Secure pairing must be confirmed.")
        address, addr_type, _name, drv = self._resolve(cmd.device_id)  # type: ignore[arg-type]
        if not ADDR_RX.match(address):
            raise AgentError(E_BAD_REQUEST, "bad device address")
        if not drv.supports_bond:
            raise AgentError(E_UNSUPPORTED, "Only an Omi pendant can be securely paired here.")
        if self.link is not None:
            await self._drop_link()                     # the pairing needs its own fresh link
        await self._need_radio()
        try:
            return await bond_pendant(self.backend, address, addr_type, replace=cmd.params.get("replace") is True)
        except BondRefused as exc:
            raise AgentError(E_BOND, exc.sentence, detail={"reason": exc.reason}) from exc
        except ConnectError as exc:
            raise AgentError(E_CONNECT, "Could not connect. Keep the pendant switched on and close to the panel.",
                             detail={"reason": str(exc)[:120]}) from exc

    async def _op_update_cancel(self, cmd: Command) -> dict:
        if self._cancel is not None and self.busy_op == "update_start":
            self._cancel.set()
            return {"cancel_requested": True}
        return {"cancel_requested": False}

    # ---------------------------------------------------------------- the loops ------------------------------
    async def sweep_once(self) -> bool:
        """Drop the live link if its lease is over, it hit the hard hold cap, or it died. Never while a job is running."""
        if self.link is None or self.busy_job is not None:
            return False
        if not self._link_alive() or self.clock() >= self.link.lease_until:
            log.info("releasing the Bluetooth link (lease over or link lost)")
            await self._drop_link()
            return True
        return False

    async def _lease_sweeper(self) -> None:
        while not self._stop.is_set():
            await self._sleep(5.0)
            await self.sweep_once()

    async def run_forever(self) -> None:
        if self.zoe is None:
            raise RuntimeError("no ZoeLink configured")
        sweeper = asyncio.create_task(self._lease_sweeper())
        failures = 0
        tasks: set = set()
        try:
            while not self._stop.is_set():
                await self.flush_outbox()
                try:
                    cmds = await self.zoe.poll(self.snapshot(), self.s.poll_wait_s)
                    failures = 0
                except ZoeUnavailable as exc:
                    failures += 1
                    wait = min(60.0, 2.0 ** min(failures, 6))
                    log.info("zoe not reachable (%s); retrying in %.0fs", exc, wait)
                    await self._sleep(wait)
                    continue
                for raw in cmds[:4]:
                    t = asyncio.create_task(self.execute(raw))
                    tasks.add(t)
                    t.add_done_callback(tasks.discard)
                if not cmds:
                    await self._sleep(0.2)
        finally:
            sweeper.cancel()
            for t in list(tasks):
                t.cancel()
            await self._drop_link()
            self.lock.release()

    def stop(self) -> None:
        self._stop.set()
