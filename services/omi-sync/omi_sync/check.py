"""The read-only device check: what is this pendant, which drain path would run, how much is stored?

Sends ONLY: GATT reads (DIS firmware/model, codec id, battery, time-sync read, the 16-byte storage status read) and ONE list
query - ``0x10 INFO`` on ring firmware (>= 3.0.20) or ``0x10 LIST_FILES`` on the verified legacy firmware (3.0.18; the firmware
answers it with a file list and changes nothing). It never sends READ, ADVANCE, DELETE, CLEAR, STOP or a time write - the test
(``tests/unit/test_omi_sync_check.py``) hands it a link whose every mutating method raises.

The command bytes are NOT sent to a firmware we have not read the source of: ``0x12`` is ADVANCE on one protocol and DELETE_FILE
on the other, so an unknown version gets the GATT reads only.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Optional

from . import legacy_protocol as lp
from . import ring_protocol as rp
from .recordings import MAX_TRUSTED_OFFSET_S, frame_seconds


async def device_check(link: Any, *, spool_stats: Optional[dict] = None, zoe_reachable: Optional[bool] = None,
                       legacy_files: bool = False, assume_codec: Optional[int] = None,
                       clock: Callable[[], float] = time.time) -> dict:
    out: dict[str, Any] = {}
    fw = await link.firmware()
    ver = rp.parse_firmware(fw)
    out["model"] = await link.model()
    out["firmware"] = fw
    ring = rp.supports_ring(fw)
    legacy = lp.is_legacy_file_firmware(ver)
    out["ring_protocol_supported"] = ring
    out["legacy_file_protocol_verified"] = legacy
    codec = await link.codec()
    out["codec_id"] = codec
    out["codec_supported"] = codec in rp.FRAME_SAMPLES_BY_CODEC
    out["codec_assumed_as"] = None if out["codec_supported"] else assume_codec
    eff = codec if out["codec_supported"] else assume_codec
    out["frame_ms"] = round(frame_seconds(eff) * 1000, 1) if eff in rp.FRAME_SAMPLES_BY_CODEC else None
    if ring:
        out["drain_path"] = "ring"
    elif legacy:
        out["drain_path"] = "legacy-files (opt-in: OMI_SYNC_LEGACY_FILES=1)" if not legacy_files else "legacy-files"
    else:
        out["drain_path"] = "refused (firmware not covered by the ring or the verified legacy protocol)"
    if out["drain_path"].startswith("refused") is False and eff not in rp.FRAME_SAMPLES_BY_CODEC:
        out["drain_path"] += " - BLOCKED: unknown codec id, set OMI_SYNC_ASSUME_CODEC after confirming"
    out["battery_pct"] = await link.battery()
    dev_t = await link.device_time()
    if dev_t is None:
        out["device_clock"] = {"readable": False}
    else:
        offset = dev_t - clock()
        out["device_clock"] = {"readable": True, "offset_s": round(offset, 1),
                               "trusted": abs(offset) <= MAX_TRUSTED_OFFSET_S and dev_t >= 1_700_000_000}
    if ring:
        try:
            st = await link.status()
            out["ring_status"] = {"unread_records": st.unread_packets, "used_bytes": st.used_bytes,
                                  "free_bytes": st.free_bytes, "rtc_valid": st.rtc_valid}
        except Exception as exc:  # noqa: BLE001
            out["ring_status"] = {"error": type(exc).__name__}
        try:
            info = rp.parse_info(await link.info())
            out["ring_info"] = {
                "read_seq": info.read_seq, "write_seq": info.write_seq, "unread": info.unread,
                "capacity_records": info.capacity_packets, "dropped_records": info.dropped_packets,
                "record_bytes": info.packet_size,
                "custody_caps": f"0x{info.caps:02x}", "custody_contract": info.contract_version,
                "ring_id": f"{info.ring_id:x}" if info.has_ring_id else None,
                "app_ack_reclaim": bool(info.caps & rp.CAP_APP_ACK_RECLAIM),
            }
        except Exception as exc:  # noqa: BLE001
            out["ring_info"] = {"error": type(exc).__name__}
    elif legacy:
        try:
            st = await link.legacy_status()
            out["storage_status"] = {"used_bytes": st.used_bytes, "file_count": st.file_count}
        except Exception as exc:  # noqa: BLE001
            out["storage_status"] = {"error": type(exc).__name__}
        try:
            files = lp.parse_file_list(await link.legacy_list())
            out["files"] = [{"timestamp": f.timestamp, "size_bytes": f.size, "blocks": f.blocks,
                             "approx_audio_s": round(f.blocks * 0.11, 1)} for f in files]
        except Exception as exc:  # noqa: BLE001
            out["files"] = {"error": type(exc).__name__}
    if spool_stats is not None:
        out["spool"] = spool_stats
    if zoe_reachable is not None:
        out["zoe_reachable"] = zoe_reachable
    return out
