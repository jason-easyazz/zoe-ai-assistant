"""Entry points: ``python -m omi_sync run | check | status``.

``run``    the sync loop over every configured pendant (``runner.py``; flag-dark: exits 0 immediately unless ``OMI_SYNC_ENABLED`` is on).
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
import dataclasses
import sys

from . import config
from .check import device_check
from .runner import run_loop
from .spool import Spool
from .uploader import HttpUploader, device_hash

log = logging.getLogger("omi_sync")


def _settings_or_exit(require_address: bool = True) -> config.Settings:
    s = config.load()
    if require_address and not s.address:
        print("OMI_PENDANT_ADDRESSES (or OMI_PENDANT_ADDRESS) is not set: the pendant's BLE address(es).", file=sys.stderr)
        raise SystemExit(2)
    return s


async def _check(s: config.Settings) -> dict:
    from . import ble_link
    spool = Spool(s.spool_path) if s.spool_path.exists() else None
    up = HttpUploader(s.zoe_url, s.device_token, verify_ssl=s.verify_ssl)
    reachable = await up.reachable()
    try:
        async with ble_link.connect(s.address, lecc=s.lecc, lecc_prefix=s.lecc_prefix, addr_type=s.addr_type, attempts=5,
                                    require_bond=s.requires_bond(s.address), adapter=s.bluez_adapter) as link:
            report = await device_check(link, spool_stats=spool.stats() if spool else None, zoe_reachable=reachable,
                                        legacy_files=s.legacy_files, assume_codec=s.assume_codec)
            report["require_bond"] = s.requires_bond(s.address)    # true = this link was checked bonded AND encrypted
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
    p.add_argument("-p", "--pendant", type=int, default=1, metavar="N", help="check: which configured pendant (1 = the first)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = config.load()
    if args.command == "status":
        spool = Spool(s.spool_path) if s.spool_path.exists() else None
        print(json.dumps({"enabled": s.enabled, "address_set": bool(s.address),
                          "pendants": [device_hash(a) for a in s.addresses], "zoe_url": s.zoe_url,
                          "token_set": bool(s.device_token), "state_dir": str(s.state_dir),
                          "lecc": s.lecc, "legacy_files": s.legacy_files, "set_device_time": s.set_device_time,
                          "assume_codec": s.assume_codec, "spool": spool.stats() if spool else None,
                          "require_bond": [device_hash(a) for a in s.addresses if s.requires_bond(a)]}, indent=2))
        return 0
    if args.command == "check":
        s = _settings_or_exit()
        if not 1 <= args.pendant <= len(s.addresses):
            print(f"--pendant {args.pendant}: only {len(s.addresses)} pendant(s) configured.", file=sys.stderr)
            return 2
        s = dataclasses.replace(s, address=s.addresses[args.pendant - 1])
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
        asyncio.run(run_loop(s))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
