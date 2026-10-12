"""Entry points: ``python -m omi_sync run | check | status``.

``run``    the sync loop (flag-dark: exits 0 immediately unless ``OMI_SYNC_ENABLED`` is on).
``check``  one connection, read-only (see ``check.py``): prints a JSON report.
``status`` no Bluetooth at all: spool stats + what the config resolved to.

A BLE fault here cannot touch the voice daemon: this is its own process, its own unit, its own radio
use (a scan every ``OMI_SYNC_SCAN_INTERVAL_S``, a connection only while syncing).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time

from . import config
from .check import device_check
from .engine import Drain, SyncConfig
from .recordings import OpusDecoder
from .spool import Spool
from .uploader import HttpUploader, device_hash

log = logging.getLogger("omi_sync")


def _settings_or_exit(require_address: bool = True) -> config.Settings:
    s = config.load()
    if require_address and not s.address:
        print("OMI_PENDANT_ADDRESS is not set (the bonded pendant's BLE address).", file=sys.stderr)
        raise SystemExit(2)
    return s


async def _run_loop(s: config.Settings) -> None:
    from . import ble_link
    spool = Spool(s.spool_path)
    up = HttpUploader(s.zoe_url, s.device_token, verify_ssl=s.verify_ssl)
    cfg = SyncConfig(device_id=device_hash(s.address), gap_s=s.gap_s, max_s=s.max_recording_s,
                     set_device_time=s.set_device_time, legacy_files=s.legacy_files, assume_codec=s.assume_codec,
                     outbox=s.outbox)
    spool.prune_confirmed(time.time() - 30 * 86400)
    log.info("omi-sync running (device %s, zoe %s, lecc=%s, legacy_files=%s, set_time=%s)", cfg.device_id, s.zoe_url, s.lecc,
             s.legacy_files, s.set_device_time)
    while True:
        wait = s.scan_interval_s
        try:
            # No scan: at the panel's range the pendant's adverts arrive once per 20-40 s, so a scan mostly misses it. A direct
            # connection attempt (hcitool lecc: 32 s supervision timeout) is both the presence test and the connection.
            async with ble_link.connect(s.address, lecc=s.lecc, lecc_prefix=s.lecc_prefix, addr_type=s.addr_type, attempts=2) as link:
                res = await Drain(link, spool, up, cfg, decoder_factory=OpusDecoder).run()
            log.info("sync: path=%s refused=%s new=%d read_complete=%s dropped=%s uploaded=%d rejected=%d undecodable=%d "
                     "unavailable=%s pending=%d advanced_to=%s notes=%s", res.path, res.refused, res.new_records, res.read_complete,
                     res.dropped, res.uploaded, res.rejected, res.undecodable, res.unavailable, res.pending, res.advanced_to, res.notes)
            # in range and clean -> wait out the cooldown; a drop/unavailable retries sooner
            wait = s.cooldown_s if res.clean else min(s.cooldown_s, 30.0)
        except ble_link.ConnectError as exc:
            log.debug("pendant not reachable: %s", exc)         # the normal 'not in range' answer
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - BLE/BlueZ faults are routine; log the class and try again
            log.warning("sync attempt failed: %s: %s", type(exc).__name__, str(exc)[:200])
            wait = max(wait, 30.0)
        await asyncio.sleep(wait)


async def _check(s: config.Settings) -> dict:
    from . import ble_link
    spool = Spool(s.spool_path) if s.spool_path.exists() else None
    up = HttpUploader(s.zoe_url, s.device_token, verify_ssl=s.verify_ssl)
    reachable = await up.reachable()
    try:
        async with ble_link.connect(s.address, lecc=s.lecc, lecc_prefix=s.lecc_prefix, addr_type=s.addr_type, attempts=5) as link:
            report = await device_check(link, spool_stats=spool.stats() if spool else None, zoe_reachable=reachable,
                                        legacy_files=s.legacy_files, assume_codec=s.assume_codec)
    except ble_link.ConnectError as exc:
        return {"found": False, "address_configured": True, "zoe_reachable": reachable, "error": str(exc),
                "hint": "pendant not reachable: powered on? in range (adverts at -90 dBm are marginal)? connected to a phone? "
                        "is `sudo -n hcitool` allowed (OMI_SYNC_LECC_PREFIX)?"}
    report["found"] = True
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="omi_sync")
    p.add_argument("command", nargs="?", default="run", choices=("run", "check", "status"))
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = config.load()
    if args.command == "status":
        spool = Spool(s.spool_path) if s.spool_path.exists() else None
        print(json.dumps({"enabled": s.enabled, "address_set": bool(s.address), "zoe_url": s.zoe_url,
                          "token_set": bool(s.device_token), "state_dir": str(s.state_dir),
                          "lecc": s.lecc, "legacy_files": s.legacy_files, "set_device_time": s.set_device_time,
                          "assume_codec": s.assume_codec, "spool": spool.stats() if spool else None}, indent=2))
        return 0
    if args.command == "check":
        s = _settings_or_exit()
        print(json.dumps(asyncio.run(_check(s)), indent=2, default=str))
        return 0
    if not s.enabled:
        log.info("OMI_SYNC_ENABLED is off - nothing to do (flag-dark). Exiting.")
        return 0
    s = _settings_or_exit()
    if not s.device_token:
        print("DEVICE_TOKEN is not set.", file=sys.stderr)
        return 2
    try:
        asyncio.run(_run_loop(s))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
