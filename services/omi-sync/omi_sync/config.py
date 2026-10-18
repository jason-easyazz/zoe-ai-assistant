"""Environment-driven configuration. The feature is flag-dark: ``OMI_SYNC_ENABLED`` unset/0 = the service exits at start."""
from __future__ import annotations

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

    @property
    def spool_path(self) -> Path:
        return self.state_dir / "spool.sqlite3"

    @property
    def outbox(self) -> Path:
        return self.state_dir / "outbox"


def load(env: dict | None = None) -> Settings:
    e = os.environ if env is None else env
    get = lambda k, d="": (e.get(k) or d)  # noqa: E731
    return Settings(
        enabled=_truthy(get("OMI_SYNC_ENABLED", "0")),
        address=(_addresses(get("OMI_PENDANT_ADDRESSES"), get("OMI_PENDANT_ADDRESS")) or ("",))[0],
        addresses=_addresses(get("OMI_PENDANT_ADDRESSES"), get("OMI_PENDANT_ADDRESS")),
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
    )
