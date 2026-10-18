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

from . import config, health
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
    misses: int = 0            # consecutive visits where the pendant did not answer (drives the away back-off; reset by a connection)


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
                 drain_factory: Optional[Callable] = None, clock: Callable[[], float] = time.monotonic,
                 wall: Callable[[], float] = time.time) -> None:
        if connect is None:
            from . import ble_link
            connect = ble_link.connect
        self.s, self.spool, self.up = s, spool, up
        self.connect = connect
        self.drain_factory = drain_factory or (lambda link, cfg: Drain(link, spool, up, cfg, decoder_factory=OpusDecoder))
        self.clock = clock
        self.wall = wall
        self.rotation = Rotation(s.addresses)
        self.reporter = health.Reporter(heartbeat_s=s.health_heartbeat_s)
        self._last_sync: dict[str, float] = {}      # device id -> wall time of its last CLEAN sync (also kept in the spool's meta)

    def _cfg(self, t: Target) -> SyncConfig:
        s = self.s
        return SyncConfig(device_id=t.device_id, gap_s=s.gap_s, max_s=s.max_recording_s, set_device_time=s.set_device_time,
                          legacy_files=s.legacy_files, assume_codec=s.assume_codec, outbox=s.outbox)

    def _last_sync_at(self, device_id: str) -> Optional[float]:
        if device_id in self._last_sync:
            return self._last_sync[device_id]
        raw = self.spool.get_meta(f"health:last_sync:{device_id}") if self.spool is not None else None
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    def _note_clean_sync(self, device_id: str) -> None:
        now = self.wall()
        self._last_sync[device_id] = now
        if self.spool is not None:
            self.spool.set_meta(f"health:last_sync:{device_id}", repr(now))

    async def _report_health(self, t: Target, reachable: bool, res: Any) -> None:
        """One numbers-only heartbeat to Zoe after the visit's custody work is done. Best effort: it can never fail a visit."""
        send = getattr(self.up, "report_health", None)
        if not self.s.health or send is None:
            return
        try:
            last = self._last_sync_at(t.device_id)
            rep = health.build_report(t.device_id, reachable=reachable, misses=t.misses,
                                      last_sync_age_s=None if last is None else max(0.0, self.wall() - last), res=res)
            now = self.clock()
            if not self.reporter.should_send(t.device_id, rep, now):
                return
            self.reporter.tried(t.device_id, now)
            if await send(rep):
                self.reporter.sent_ok(t.device_id, rep, now)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - health is advisory; custody and the loop come first
            log.info("health report for %s skipped: %s", t.device_id[:6], type(exc).__name__)

    async def visit(self, t: Target) -> float:
        """One session with one pendant -> how long until it is worth visiting again."""
        s = self.s
        wait = s.scan_interval_s
        res = None
        reachable = False
        busy = False
        try:
            # No scan: at the panel's range the pendant's adverts arrive once per 20-40 s, so a scan mostly misses it. A direct
            # connection attempt (hcitool lecc: 32 s supervision timeout) is both the presence test and the connection.
            async with self.connect(t.address, lecc=s.lecc, lecc_prefix=s.lecc_prefix, addr_type=s.addr_type, attempts=2) as link:
                reachable = True
                t.misses = 0                                # it answered: the away back-off starts over
                res = await self.drain_factory(link, self._cfg(t)).run()
            log.info("sync[%s]: path=%s refused=%s new=%d read_complete=%s dropped=%s uploaded=%d rejected=%d undecodable=%d "
                     "unavailable=%s not_registered=%s pending=%d advanced_to=%s notes=%s", t.device_id[:6], res.path, res.refused,
                     res.new_records, res.read_complete, res.dropped, res.uploaded, res.rejected, res.undecodable, res.unavailable,
                     res.not_registered, res.pending, res.advanced_to, res.notes)
            if res.clean:
                self._note_clean_sync(t.device_id)
            if res.uploaded and self.spool is not None:
                try:
                    freed = self.spool.compact_if_sparse()       # give the disk back after a big drain (audit P2-2)
                    if freed:
                        log.info("spool compacted: about %d MB released", freed // (1024 * 1024))
                except Exception as exc:  # noqa: BLE001 - housekeeping, never a reason to fail the visit
                    log.warning("spool compaction skipped: %s", type(exc).__name__)
            if res.not_registered:
                wait = s.unregistered_retry_s          # slow: an admin has to assign it; the spool holds the audio meanwhile
            else:
                # in range and clean -> wait out the cooldown; a drop/unavailable retries sooner
                wait = s.cooldown_s if res.clean else min(s.cooldown_s, 30.0)
        except ConnectError as exc:
            if str(exc).startswith("radio busy"):
                busy = True                                 # another Bluetooth task has the radio: not evidence the pendant is away
            else:
                t.misses += 1
                # away: ease off exponentially (30 s, 60 s, ... up to the cap) instead of ~85 failed attempts an hour
                wait = health.away_wait(t.misses, s.scan_interval_s, s.away_backoff_max_s)
            log.debug("pendant %s not reachable: %s (misses=%d, next try in %.0f s)", t.device_id[:6], exc, t.misses, wait)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - BLE/BlueZ faults are routine; log the class and try again
            log.warning("sync attempt for %s failed: %s: %s", t.device_id[:6], type(exc).__name__, str(exc)[:200])
            wait = max(wait, 30.0)
        if not busy:
            await self._report_health(t, reachable, res)
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
    try:
        freed = spool.compact_if_sparse()
        if freed:
            log.info("spool compacted at start: about %d MB released", freed // (1024 * 1024))
    except Exception as exc:  # noqa: BLE001
        log.warning("spool compaction skipped: %s", type(exc).__name__)
    log.info("omi-sync running (%d pendant(s): %s; zoe %s, lecc=%s, legacy_files=%s, set_time=%s)", len(runner.rotation.targets),
             ", ".join(t.device_id for t in runner.rotation.targets), s.zoe_url, s.lecc, s.legacy_files, s.set_device_time)
    while True:
        await sleep(await runner.step())
