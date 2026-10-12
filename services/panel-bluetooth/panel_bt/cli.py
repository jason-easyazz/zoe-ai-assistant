"""Entry points: ``python -m panel_bt run | status | scan``.

``run``     the agent (flag-dark: exits 0 immediately unless ``PANEL_BT_ENABLED`` is on).
``status``  no Bluetooth: what the config resolved to, the known devices (ids only) and the pinned firmware.
``scan``    one local scan, printed as JSON (ids, names, signal, driver - never addresses). The operator's smoke test.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import signal
import sys

from . import VERSION, config
from .agent import PanelBluetoothAgent
from .backend import BleakBackend
from .drivers import DriverRegistry
from .drivers.omi_release import ReleaseError, load_pin
from .known import KnownStore
from .zoe_link import ZoeLink

log = logging.getLogger("panel_bt")


def _backend(s: config.Settings) -> BleakBackend:
    return BleakBackend(lecc=s.lecc, lecc_prefix=s.lecc_prefix, hci=s.hci)


async def _run(s: config.Settings) -> None:
    agent = PanelBluetoothAgent(s, _backend(s), DriverRegistry(), ZoeLink(s.zoe_url, s.device_token, verify_ssl=s.verify_ssl))
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, agent.stop)
    log.info("panel-bluetooth %s running (zoe %s, adapter %s, lecc=%s, ota=%s)", VERSION, s.zoe_url, s.hci or "default", s.lecc,
             "ON" if s.ota_enabled else "off")
    await agent.run_forever()


async def _scan(s: config.Settings) -> dict:
    reg = DriverRegistry()
    adverts = await _backend(s).scan(s.scan_s)
    rows = [{"id": a.id, "name": a.name, "rssi": a.rssi, "driver": reg.identify(a).name} for a in adverts]
    rows.sort(key=lambda r: -(r["rssi"] if r["rssi"] is not None else -999))
    return {"count": len(rows), "devices": rows[:40]}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="panel_bt")
    p.add_argument("command", nargs="?", default="run", choices=("run", "status", "scan"))
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    s = config.load()
    if args.command == "status":
        try:
            pin = load_pin(s.ota_pin_file)
            pin_doc = {"model": pin.model, "version": pin.version, "sha256": pin.sha256, "images": len(pin.images)}
        except ReleaseError as exc:
            pin_doc = {"error": str(exc)}
        print(json.dumps({"version": VERSION, "enabled": s.enabled, "ota_enabled": s.ota_enabled, "zoe_url": s.zoe_url,
                          "token_set": bool(s.device_token), "state_dir": str(s.state_dir), "adapter": s.hci or "default",
                          "lecc": s.lecc, "known_devices": sorted(KnownStore(s.known_path).all()), "firmware_pin": pin_doc}, indent=2))
        return 0
    if not s.enabled:
        log.info("PANEL_BT_ENABLED is off - nothing to do (flag-dark). Exiting.")
        return 0
    if args.command == "scan":
        print(json.dumps(asyncio.run(_scan(s)), indent=2))
        return 0
    if not s.device_token:
        print("DEVICE_TOKEN is not set.", file=sys.stderr)
        return 2
    try:
        asyncio.run(_run(s))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
