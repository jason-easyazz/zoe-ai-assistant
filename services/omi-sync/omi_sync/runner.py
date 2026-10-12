"""The sync loop for ONE OR MORE pendants on one Pi: a round-robin of independent visits.

Each pendant has its own state - its own spool rows (keyed by ``device_hash(address)``), its own staged WAVs, its own next-visit time -
so a slow, absent or unregistered pendant never holds up another. A visit is exactly the old single-pendant session: take the shared
radio lock (``ble_link.connect`` -> ``RadioLock``, shared with the Settings > Bluetooth agent), connect, drain, release. The lock is
held PER VISIT, never across visits, so the Bluetooth agent can always get in between two pendants; if it holds the radio, the visit
is a ``ConnectError("radio busy ...")`` and the rotation simply moves on.

Who owns which pendant is Zoe's business (the ``omi_devices`` registry); nothing here knows, and nothing about who is signed in on the
panel reaches this process.

Seams (``connect``, ``drain_factory``, ``clock``, ``sleep``) are injected so ``tests/unit/test_omi_sync_rotation.py`` runs the real
loop against fakes, with no radio.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional, Sequence

from . import config
from .ble_link import ConnectError
from .engine import Drain, SyncConfig
from .recordings import OpusDecoder
from .spool import Spool
from .uploader import HttpUploader, device_hash

log = logging.getLogger("omi_sync")


@dataclass
class Target:
    address: str
    device_id: str
    next_at: float = 0.0       # monotonic seconds; 0 = due now


class Rotation:
    """Which pendant to visit next: due ones only, taken in a fixed rotation (the one after the last visited first)."""

    def __init__(self, addresses: Sequence[str]) -> None:
        self.targets = [Target(a, device_hash(a)) for a in addresses]
        self._last = -1

    def next_due(self, now: float) -> Optional[Target]:
        n = len(self.targets)
        for step in range(1, n + 1):
            i = (self._last + step) % n
            if self.targets[i].next_at <= now:
                self._last = i
                return self.targets[i]
        return None

    def done(self, target: Target, wait_s: float, now: float) -> None:
        target.next_at = now + max(0.0, wait_s)

    def sleep_for(self, now: float, idle_s: float) -> float:
        """How long until something is due (never longer than ``idle_s``, never negative)."""
        if not self.targets:
            return idle_s
        return max(0.0, min(idle_s, min(t.next_at for t in self.targets) - now))


class Runner:
    def __init__(self, s: config.Settings, spool: Spool, up: Any, *, connect: Optional[Callable] = None,
                 drain_factory: Optional[Callable] = None, clock: Callable[[], float] = time.monotonic) -> None:
        if connect is None:
            from . import ble_link
            connect = ble_link.connect
        self.s, self.spool, self.up = s, spool, up
        self.connect = connect
        self.drain_factory = drain_factory or (lambda link, cfg: Drain(link, spool, up, cfg, decoder_factory=OpusDecoder))
        self.clock = clock
        self.rotation = Rotation(s.addresses)

    def _cfg(self, t: Target) -> SyncConfig:
        s = self.s
        return SyncConfig(device_id=t.device_id, gap_s=s.gap_s, max_s=s.max_recording_s, set_device_time=s.set_device_time,
                          legacy_files=s.legacy_files, assume_codec=s.assume_codec, outbox=s.outbox)

    async def visit(self, t: Target) -> float:
        """One session with one pendant -> how long until it is worth visiting again."""
        s = self.s
        wait = s.scan_interval_s
        try:
            # No scan: at the panel's range the pendant's adverts arrive once per 20-40 s, so a scan mostly misses it. A direct
            # connection attempt (hcitool lecc: 32 s supervision timeout) is both the presence test and the connection.
            async with self.connect(t.address, lecc=s.lecc, lecc_prefix=s.lecc_prefix, addr_type=s.addr_type, attempts=2) as link:
                res = await self.drain_factory(link, self._cfg(t)).run()
            log.info("sync[%s]: path=%s refused=%s new=%d read_complete=%s dropped=%s uploaded=%d rejected=%d undecodable=%d "
                     "unavailable=%s not_registered=%s pending=%d advanced_to=%s notes=%s", t.device_id[:6], res.path, res.refused,
                     res.new_records, res.read_complete, res.dropped, res.uploaded, res.rejected, res.undecodable, res.unavailable,
                     res.not_registered, res.pending, res.advanced_to, res.notes)
            if res.not_registered:
                wait = s.unregistered_retry_s          # slow: an admin has to assign it; the spool holds the audio meanwhile
            else:
                # in range and clean -> wait out the cooldown; a drop/unavailable retries sooner
                wait = s.cooldown_s if res.clean else min(s.cooldown_s, 30.0)
        except ConnectError as exc:
            log.debug("pendant %s not reachable: %s", t.device_id[:6], exc)         # the normal 'not in range' / 'radio busy' answer
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - BLE/BlueZ faults are routine; log the class and try again
            log.warning("sync attempt for %s failed: %s: %s", t.device_id[:6], type(exc).__name__, str(exc)[:200])
            wait = max(wait, 30.0)
        return wait

    async def step(self) -> float:
        """Visit the next due pendant, if any. Returns how long the caller should sleep before the next ``step``."""
        now = self.clock()
        t = self.rotation.next_due(now)
        if t is None:
            return self.rotation.sleep_for(now, self.s.scan_interval_s)
        wait = await self.visit(t)
        self.rotation.done(t, wait, self.clock())
        return 0.0                  # another pendant may be due right now; the next step finds out


async def run_loop(s: config.Settings, *, sleep: Callable = asyncio.sleep) -> None:
    spool = Spool(s.spool_path)
    up = HttpUploader(s.zoe_url, s.device_token, verify_ssl=s.verify_ssl)
    runner = Runner(s, spool, up)
    spool.prune_confirmed(time.time() - 30 * 86400)
    log.info("omi-sync running (%d pendant(s): %s; zoe %s, lecc=%s, legacy_files=%s, set_time=%s)", len(runner.rotation.targets),
             ", ".join(t.device_id for t in runner.rotation.targets), s.zoe_url, s.lecc, s.legacy_files, s.set_device_time)
    while True:
        await sleep(await runner.step())
