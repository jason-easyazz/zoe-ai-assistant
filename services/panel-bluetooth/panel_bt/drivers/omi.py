"""The Omi CV1 pendant driver: info (model, firmware, battery, charging, sync status), update check, and the Update action.

Matched by advert: the Omi audio service UUID ``19b10000-e8f2-537e-4f6c-d104768a1214`` (strongest), or the local name ``Omi``.
Sync status comes from ``omi-sync``'s own spool, read-only (``mode=ro``): this driver never imports ``omi_sync`` and never writes
to its state. The update itself is ``omi_dfu.run_omi_update`` (gates + staged procedure); read its docstring first.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Optional

from ..model import Advert
from .base import Driver, UpdateContext
from .omi_dfu import (OtaState, UpdateDeps, evaluate_gates, failed_gates, read_omi_facts, run_omi_update)
from .omi_release import ReleaseError, load_pin

OMI_AUDIO_SERVICE = "19b10000-e8f2-537e-4f6c-d104768a1214"
_NAME_RX = re.compile(r"^omi(\s|$|[-_])", re.I)

INSTALL_WARNING = (
    "This install cannot be undone: the pendant has no fallback copy, and if it loses power while it installs it may not be recoverable "
    "over Bluetooth. Keep it on its charger, close to the panel, and do not move or unplug it for 2 minutes after it restarts."
)


def read_sync_status(omi_sync_dir: Path) -> dict:
    """What omi-sync is holding (read-only). ``configured`` False = no spool on this panel (sync never ran here)."""
    path = Path(omi_sync_dir) / "spool.sqlite3"
    if not path.is_file():
        return {"configured": False}
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        try:
            n = db.execute("SELECT COUNT(*) FROM records").fetchone()[0]
            c, last = db.execute("SELECT COUNT(*), MAX(confirmed_at) FROM confirmed").fetchone()
        finally:
            db.close()
        return {"configured": True, "waiting_to_upload": int(n), "confirmed_ranges": int(c),
                "last_confirmed_at": float(last) if last is not None else None}
    except sqlite3.Error:
        return {"configured": True, "unreadable": True}


class OmiDriver(Driver):
    name = "omi"
    label = "Omi pendant"
    supports_update = True
    supports_bond = True            # phase-2 (Zoe) firmware only - bond.bond_pendant checks the DIS version itself

    def __init__(self, deps_factory: Optional[Any] = None) -> None:
        self._deps_factory = deps_factory      # tests inject a fake SMP session here; production builds the real one lazily

    def match(self, advert: Advert) -> int:
        if OMI_AUDIO_SERVICE in advert.service_uuids:
            return 100
        if _NAME_RX.match(advert.name or ""):
            return 60
        return 0

    async def info(self, link: Any, ctx: dict) -> dict:
        facts = await read_omi_facts(link)
        s = ctx.get("settings")
        facts["sync"] = read_sync_status(s.omi_sync_dir) if s is not None else {"configured": False}
        return facts

    async def update_check(self, info: dict, ctx: dict) -> dict:
        s = ctx["settings"]
        try:
            pin = load_pin(s.ota_pin_file)
        except ReleaseError as exc:
            return {"supported": True, "available": False, "reason": f"The pinned firmware release is unusable: {exc}"}
        state = OtaState(s.ota_state_path, s.ota_cooldown_s)
        gates = evaluate_gates(
            _inputs(s, info, ctx, state), pin, min_battery=s.ota_min_battery, min_rssi=s.ota_min_rssi)
        fw_gate = next(g for g in gates if g.id == "firmware")
        out = {
            "supported": True,
            "available": fw_gate.ok,
            "current": info.get("firmware"),
            "latest": pin.version,
            "changelog": pin.changelog,
            "source": pin.source,
            "gates": [g.as_dict() for g in gates],
            "ready": all(g.ok for g in gates if g.id != "signal"),      # the signal is measured fresh when Update is pressed
            "min_battery": s.ota_min_battery,
            "min_rssi": s.ota_min_rssi,
            "warning": INSTALL_WARNING,
            "needs_ack": True,
        }
        if not fw_gate.ok:
            out["reason"] = fw_gate.detail
        out["blocked_by"] = [g.id for g in failed_gates(gates) if g.id != "signal"]
        return out

    async def run_update(self, uctx: UpdateContext) -> dict:
        if self._deps_factory is not None:
            deps = self._deps_factory(uctx)
        else:
            from ..smp_session import make_smp_session      # lazy: smpclient is only needed on a panel that actually updates
            deps = UpdateDeps(smp_factory=make_smp_session)
        return await run_omi_update(uctx, deps)


def _inputs(s: Any, info: dict, ctx: dict, state: OtaState):
    from .omi_dfu import GateInputs
    return GateInputs(ota_enabled=s.ota_enabled, model=info.get("model"), firmware=info.get("firmware"), battery=info.get("battery"),
                      charging=info.get("charging"), rssi_samples=ctx.get("rssi_samples"), cooldown_remaining_s=state.remaining_s(),
                      other_job=bool(ctx.get("other_job")))
