"""The pendant HEALTH heartbeat: a small numbers-only record the Pi sends to Zoe so that loss is never silent (gap audit P0-2), and the
gentler retry cadence for a pendant that is away (P2-1).

What is reported (see ``build_report``): whether the pendant answered, when it last synced cleanly (as an AGE in seconds, so the Pi's
clock never matters), the standard BLE Battery Service level (0x2A19), the firmware version, and - from the ring ``INFO`` the engine
already reads - how full the ring is, its stored and capacity bytes, and the pendant's cumulative ``dropped_packets`` counter (records the
firmware overwrote before anyone read them). Zoe derives "overwritten since the last report" from that absolute counter, so a lost or
repeated report can neither lose nor double-count an overwrite. NEVER any audio, transcript, address or free text.

Pure functions plus one small stateful throttle; the real loop wiring is in ``runner.py``. Tests: ``tests/unit/test_omi_sync_health.py``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from .engine import DrainResult

#: the longest the Pi waits between visits to a pendant that is not answering (the owner's number: about ten minutes)
AWAY_BACKOFF_CAP_S = 600.0
#: a heartbeat is sent at least this often even when nothing changed (Zoe calls the Pi silent after a few hours)
HEARTBEAT_S = 900.0

_FIRMWARE_RX = re.compile(r"[^0-9A-Za-z._+-]")


def away_wait(misses: int, base_s: float, cap_s: float = AWAY_BACKOFF_CAP_S) -> float:
    """How long to wait before the next try after ``misses`` consecutive failed connection attempts (>= 1): ``base`` doubling, capped.

    Before this the Pi retried at the 30 s scan cadence all day (about 85 failed ``hcitool lecc`` attempts an hour while the owner was
    out). 1 miss -> base, 2 -> 2x, ... never more than the cap, and never less than the base. Reset to 0 by a connection.
    """
    base = max(1.0, float(base_s))
    cap = max(base, float(cap_s))
    if misses <= 1:
        return base
    return min(cap, base * (2.0 ** min(misses - 1, 30)))      # the exponent is bounded so a huge miss count cannot overflow


def clean_firmware(raw: Optional[str]) -> Optional[str]:
    text = _FIRMWARE_RX.sub("", (raw or "").strip())[:32]
    return text or None


def _pct(part: float, whole: float) -> Optional[float]:
    if whole <= 0:
        return None
    return round(max(0.0, min(100.0, 100.0 * part / whole)), 1)


def build_report(device_id: str, *, reachable: bool, misses: int, last_sync_age_s: Optional[float], res: Optional[DrainResult]) -> dict:
    """The JSON body of ``POST /api/omi/device-status``. ``res`` is the visit's ``DrainResult`` (None when the pendant did not answer).

    ``ring_fill_pct`` is the END-of-session estimate (what is still on the pendant after the drain freed what Zoe confirmed): the
    start-of-session fill would raise a false alarm right after a successful catch-up. ``ring_fill_start_pct`` is what the ring held when
    we connected (how full it got while away).
    """
    rep: dict[str, Any] = {"device": device_id, "reachable": bool(reachable), "consecutive_misses": max(0, int(misses))}
    if last_sync_age_s is not None:
        rep["last_sync_age_s"] = max(0, int(last_sync_age_s))
    if res is None or not reachable:
        return rep
    if res.battery_pct is not None and 0 <= res.battery_pct <= 100:
        rep["battery_pct"] = int(res.battery_pct)
    fw = clean_firmware(res.firmware)
    if fw:
        rep["firmware"] = fw
    if res.ring_capacity_records and res.ring_packet_bytes:
        cap = int(res.ring_capacity_records)
        pkt = int(res.ring_packet_bytes)
        start_unread = res.ring_unread_start
        end_unread = res.ring_unread_end if res.ring_unread_end is not None else start_unread
        if end_unread is not None:
            rep["ring_fill_pct"] = _pct(end_unread, cap)
            rep["ring_stored_bytes"] = int(end_unread) * pkt
            rep["ring_capacity_bytes"] = cap * pkt
        if start_unread is not None:
            rep["ring_fill_start_pct"] = _pct(start_unread, cap)
    if res.dropped_total is not None and res.dropped_total >= 0:
        rep["dropped_total"] = int(res.dropped_total)
    return rep


def signature(rep: dict) -> tuple:
    """What counts as 'something changed' for the throttle: a flip in reachability, a new clean sync, a new overwrite, a battery step."""
    batt = rep.get("battery_pct")
    fill = rep.get("ring_fill_pct")
    age = rep.get("last_sync_age_s")
    return (rep.get("reachable"), rep.get("firmware"), rep.get("dropped_total"), None if batt is None else int(batt) // 10,
            None if fill is None else int(fill) // 10, age is not None and age < 60)


@dataclass
class Reporter:
    """Per pendant: send when the picture changed, otherwise at most every ``heartbeat_s``; never twice within ``min_gap_s``.

    ``sent_ok`` is called only when Zoe ANSWERED 200, so a refused or unreachable report is retried at the next visit rather than
    being treated as delivered.
    """

    heartbeat_s: float = HEARTBEAT_S
    min_gap_s: float = 15.0
    _last_sig: dict = field(default_factory=dict)
    _last_ok_at: dict = field(default_factory=dict)
    _last_try_at: dict = field(default_factory=dict)

    def should_send(self, device_id: str, rep: dict, now: float) -> bool:
        if now - self._last_try_at.get(device_id, -1e12) < self.min_gap_s:
            return False
        if device_id not in self._last_ok_at:
            return True
        if signature(rep) != self._last_sig.get(device_id):
            return True
        return now - self._last_ok_at[device_id] >= self.heartbeat_s

    def tried(self, device_id: str, now: float) -> None:
        self._last_try_at[device_id] = now

    def sent_ok(self, device_id: str, rep: dict, now: float) -> None:
        self._last_sig[device_id] = signature(rep)
        self._last_ok_at[device_id] = now
