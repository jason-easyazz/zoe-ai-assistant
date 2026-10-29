"""Environment-driven configuration. Flag-dark: ``PANEL_BT_ENABLED`` unset/0 = the service exits at start.

Two independent switches, both default OFF:

* ``PANEL_BT_ENABLED``      the agent at all (scan / connect / disconnect / forget / info).
* ``PANEL_BT_OTA_ENABLED``  firmware updates specifically. Off = ``update_start`` is refused with a plain message even if zoe-data
                            asks. The update path is unverified on real hardware until an operator has run one supervised.

The update safety floors are clamped HERE, not just defaulted: configuration can make them stricter, never weaker than the
floors the plan requires (battery >= 50 %, a strong signal).
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

#: configuration may raise these floors, never lower them
OTA_BATTERY_FLOOR = 50
OTA_RSSI_FLOOR = -75          # the median advert RSSI must be BETTER than this; configuration can only demand a stronger signal


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _hci(raw: str) -> str:
    """``hci0``/``hci1``... or '' (BlueZ's default adapter). Anything else is dropped, never passed to a command line."""
    raw = raw.strip().lower()
    return raw if re.fullmatch(r"hci\d{1,2}", raw) else ""


def _num(env, name: str, default: float) -> float:
    try:
        return float(env.get(name, "") or default)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    enabled: bool
    ota_enabled: bool
    zoe_url: str
    device_token: str
    verify_ssl: bool
    state_dir: Path
    lecc: bool
    hci: str
    lecc_prefix: tuple
    scan_s: float
    lease_s: float
    max_hold_s: float
    poll_wait_s: float
    ota_min_battery: int
    ota_min_rssi: int
    ota_cooldown_s: float
    ota_max_s: float
    ota_pin_file: Optional[Path]
    radio_lock: Path
    omi_sync_dir: Path
    bond_enabled: bool = False       # PANEL_BT_BOND_ENABLED: the owner-run secure pairing (omi_bond) - default OFF

    @property
    def known_path(self) -> Path:
        return self.state_dir / "known.json"

    @property
    def cache_dir(self) -> Path:
        return self.state_dir / "cache"

    @property
    def ota_state_path(self) -> Path:
        return self.state_dir / "ota-state.json"


def load(env: dict | None = None) -> Settings:
    e = os.environ if env is None else env
    get = lambda k, d="": (e.get(k) or d)  # noqa: E731
    state = Path(get("PANEL_BT_DIR") or Path.home() / ".zoe-bluetooth").expanduser()
    pin = get("PANEL_BT_OTA_PIN_FILE").strip()
    return Settings(
        enabled=_truthy(get("PANEL_BT_ENABLED", "0")),
        ota_enabled=_truthy(get("PANEL_BT_OTA_ENABLED", "0")),
        bond_enabled=_truthy(get("PANEL_BT_BOND_ENABLED", "0")),
        zoe_url=get("ZOE_URL", "https://zoe.local").strip(),
        device_token=get("DEVICE_TOKEN").strip(),
        verify_ssl=_truthy(get("VERIFY_SSL", "1")),
        state_dir=state,
        lecc=_truthy(get("PANEL_BT_LECC", "1")),
        hci=_hci(get("PANEL_BT_HCI")),
        lecc_prefix=tuple(shlex.split(get("PANEL_BT_LECC_PREFIX", "sudo -n"))),
        scan_s=min(20.0, max(3.0, _num(e, "PANEL_BT_SCAN_S", 10.0))),
        lease_s=min(600.0, max(30.0, _num(e, "PANEL_BT_LEASE_S", 120.0))),
        max_hold_s=min(3600.0, max(60.0, _num(e, "PANEL_BT_MAX_HOLD_S", 600.0))),
        poll_wait_s=min(25.0, max(1.0, _num(e, "PANEL_BT_POLL_WAIT_S", 20.0))),
        ota_min_battery=max(OTA_BATTERY_FLOOR, int(_num(e, "PANEL_BT_OTA_MIN_BATTERY", OTA_BATTERY_FLOOR))),
        ota_min_rssi=max(OTA_RSSI_FLOOR, int(_num(e, "PANEL_BT_OTA_MIN_RSSI", OTA_RSSI_FLOOR))),
        ota_cooldown_s=max(60.0, _num(e, "PANEL_BT_OTA_COOLDOWN_S", 600.0)),
        ota_max_s=min(3600.0, max(300.0, _num(e, "PANEL_BT_OTA_MAX_S", 1200.0))),
        ota_pin_file=Path(pin).expanduser() if pin else None,
        radio_lock=Path(get("ZOE_BLE_RADIO_LOCK") or Path.home() / ".zoe-ble-radio.lock").expanduser(),
        omi_sync_dir=Path(get("OMI_SYNC_DIR") or Path.home() / ".zoe-omi-sync").expanduser(),
    )
