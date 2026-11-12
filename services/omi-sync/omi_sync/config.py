"""Environment-driven configuration. The feature is flag-dark: ``OMI_SYNC_ENABLED`` unset/0 = the service exits at start."""
from __future__ import annotations

import json
import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def _truthy(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


def _float(name: str, default: float, env) -> float:
    try:
        return float(env.get(name, "") or default)
    except ValueError:
        return default


def _addresses(multi: str, single: str) -> tuple:
    """``OMI_PENDANT_ADDRESSES`` (comma / space / semicolon list) wins; the old single ``OMI_PENDANT_ADDRESS`` is the fallback.
    Order is kept (it is the visiting order), duplicates are dropped case-insensitively."""
    seen, out = set(), []
    for raw in re.split(r"[,;\s]+", multi or single or ""):
        a = raw.strip()
        if a and a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return tuple(out)


_ADDR_RX = re.compile(r"^[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}$")
#: the panel Bluetooth agent's driver name for an Omi pendant (``panel_bt/drivers/omi.py``: ``name = "omi"``)
OMI_DRIVER = "omi"


def known_omi_addresses(path: Optional[Path]) -> tuple:
    """The Omi pendants the panel's Bluetooth agent remembers (its ``KnownStore`` file, ``known.json``: ``{device_id: {"address",
    "driver", ...}}``), in a stable order. READ-ONLY, and the reader is a deliberate copy of that format (two venvs, no import). A
    missing, unreadable or malformed file is simply no pendants - never an error, never a guess."""
    if path is None:
        return ()
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if not isinstance(doc, dict):
        return ()
    out = []
    for _did, v in sorted(doc.items(), key=lambda kv: str(kv[0])):
        if isinstance(v, dict) and v.get("driver") == OMI_DRIVER and _ADDR_RX.match(str(v.get("address", ""))):
            out.append(str(v["address"]))
    return tuple(out)


def union_addresses(configured: tuple, known: tuple) -> tuple:
    """The env list first (its order is the visiting order), then every remembered pendant it does not already name (case-insensitive)."""
    seen = {a.lower() for a in configured}
    out = list(configured)
    for a in known:
        if a.lower() not in seen:
            seen.add(a.lower())
            out.append(a)
    return tuple(out)


def _require_bond(raw: str) -> tuple:
    """``OMI_SYNC_REQUIRE_BOND``: unset/0/off = none; 1/all/true = every pendant (``*``); else a list of addresses."""
    v = (raw or "").strip()
    if v.lower() in ("", "0", "off", "false", "no"):
        return ()
    if v.lower() in ("1", "all", "true", "yes", "on", "*"):
        return ("*",)
    return tuple(a.lower() for a in _addresses(v, ""))


def _opt_int(raw: str) -> Optional[int]:
    try:
        return int(raw.strip()) if raw.strip() else None
    except ValueError:
        return None


@dataclass(frozen=True)
class Settings:
    enabled: bool
    address: str                 # the FIRST pendant's BLE address (operator-provided; never uploaded) - '' if none
    addresses: tuple             # every pendant this Pi syncs, in visiting order (OMI_PENDANT_ADDRESSES, else OMI_PENDANT_ADDRESS)
    addr_type: str               # 'random' (the CV1 uses a static random address) | 'public'
    zoe_url: str
    device_token: str
    verify_ssl: bool
    state_dir: Path
    gap_s: float
    max_recording_s: float
    set_device_time: bool        # a WRITE to the pendant's clock; OFF unless the operator turns it on
    legacy_files: bool           # opt in to the 3.0.18 file protocol (the pendant deletes files itself on delivery)
    assume_codec: Optional[int]  # decode as this codec id when the pendant reports one we do not know
    lecc: bool                   # create the LE link with `hcitool lecc` first (the recipe that works at range)
    lecc_prefix: tuple           # how to run it privileged, e.g. ('sudo', '-n')
    scan_interval_s: float
    cooldown_s: float
    unregistered_retry_s: float  # how long to wait before asking again after Zoe said "this pendant is not assigned to anyone"
    away_backoff_max_s: float    # the longest wait between tries for a pendant that is not answering (exponential up to this)
    health: bool                 # send the numbers-only health heartbeat to Zoe (kill switch; read-only, default on)
    health_heartbeat_s: float    # ... at least this often when nothing changed
    require_bond: tuple = ()     # phase-2 firmware: pendants that must be bonded + encrypted ('*' = every pendant); default none
    bluez_adapter: str = "hci0"  # the adapter whose BlueZ bond the check reads
    configured: Optional[tuple] = None  # the env list alone (OMI_PENDANT_ADDRESSES / OMI_PENDANT_ADDRESS); None = use ``addresses``
    known_path: Optional[Path] = None   # the panel Bluetooth agent's remembered devices (read-only); None = not read

    def current_addresses(self) -> tuple:
        """The pendants to sync NOW: the env list unioned with the Omi pendants the panel remembers (re-read on every call, so a
        pendant a member just linked at the panel is picked up without a restart or an env edit)."""
        base = self.addresses if self.configured is None else self.configured
        return union_addresses(tuple(base), known_omi_addresses(self.known_path))

    def requires_bond(self, address: str) -> bool:
        """True when ``address`` is listed in ``OMI_SYNC_REQUIRE_BOND`` (or it says ``all``). Once true, never an open link."""
        return "*" in self.require_bond or address.strip().lower() in self.require_bond

    @property
    def spool_path(self) -> Path:
        return self.state_dir / "spool.sqlite3"

    @property
    def outbox(self) -> Path:
        return self.state_dir / "outbox"


def _known_path(get) -> Optional[Path]:
    """``OMI_SYNC_KNOWN_DEVICES`` (a path; ``0``/``off`` = do not read it) else the agent's own default, ``$PANEL_BT_DIR/known.json``
    (``~/.zoe-bluetooth/known.json``)."""
    raw = get("OMI_SYNC_KNOWN_DEVICES").strip()
    if raw.lower() in ("0", "off", "false", "no"):
        return None
    if raw:
        return Path(raw).expanduser()
    return Path(get("PANEL_BT_DIR") or Path.home() / ".zoe-bluetooth").expanduser() / "known.json"


def load(env: dict | None = None) -> Settings:
    e = os.environ if env is None else env
    get = lambda k, d="": (e.get(k) or d)  # noqa: E731
    configured = _addresses(get("OMI_PENDANT_ADDRESSES"), get("OMI_PENDANT_ADDRESS"))
    known_path = _known_path(get)
    addresses = union_addresses(configured, known_omi_addresses(known_path))
    return Settings(
        enabled=_truthy(get("OMI_SYNC_ENABLED", "0")),
        address=(addresses or ("",))[0],
        addresses=addresses,
        addr_type="public" if get("OMI_PENDANT_ADDR_TYPE", "random").strip().lower() == "public" else "random",
        zoe_url=get("ZOE_URL", "https://zoe.local").strip(),
        device_token=get("DEVICE_TOKEN").strip(),
        verify_ssl=_truthy(get("VERIFY_SSL", "1")),
        state_dir=Path(get("OMI_SYNC_DIR") or Path.home() / ".zoe-omi-sync").expanduser(),
        gap_s=_float("OMI_SYNC_GAP_S", 90.0, e),
        max_recording_s=_float("OMI_SYNC_MAX_RECORDING_S", 600.0, e),
        set_device_time=_truthy(get("OMI_SYNC_SET_DEVICE_TIME", "0")),
        legacy_files=_truthy(get("OMI_SYNC_LEGACY_FILES", "0")),
        assume_codec=_opt_int(get("OMI_SYNC_ASSUME_CODEC")),
        lecc=_truthy(get("OMI_SYNC_LECC", "1")),
        lecc_prefix=tuple(shlex.split(get("OMI_SYNC_LECC_PREFIX", "sudo -n"))),
        scan_interval_s=_float("OMI_SYNC_SCAN_INTERVAL_S", 30.0, e),
        cooldown_s=_float("OMI_SYNC_COOLDOWN_S", 120.0, e),
        unregistered_retry_s=max(60.0, _float("OMI_SYNC_UNREGISTERED_RETRY_S", 900.0, e)),
        away_backoff_max_s=max(_float("OMI_SYNC_SCAN_INTERVAL_S", 30.0, e), _float("OMI_SYNC_AWAY_BACKOFF_MAX_S", 600.0, e)),
        health=_truthy(get("OMI_SYNC_HEALTH", "1")),
        health_heartbeat_s=max(60.0, _float("OMI_SYNC_HEALTH_HEARTBEAT_S", 900.0, e)),
        require_bond=_require_bond(get("OMI_SYNC_REQUIRE_BOND")),
        bluez_adapter=get("OMI_SYNC_BLUEZ_ADAPTER", "hci0").strip() or "hci0",
        configured=configured,
        known_path=known_path,
    )
