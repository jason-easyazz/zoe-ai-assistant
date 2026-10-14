"""The struggles REPORT: ``struggle_events`` clustered by (signal, domain, text-free shape) with counts, first/last seen and
a suggested fix type. Read side of ``struggle_detector`` (which detects and stores).

Surfaces (all owner/operator-only; contract ``docs/architecture/struggle-detector.md``):
  * ``digest`` - the admin endpoint (``routers/struggles.py``) and the CLI (``scripts/maintenance/struggle_report.py``);
  * ``log_nightly_summary`` - the ``STRUGGLE_SUMMARY`` line beside ``MEMORY_REJECT_SUMMARY`` (counts only, never words);
  * ``brief_line`` - ``ZOE_STRUGGLE_DETECT=on`` only: one line in the admin's morning brief.

PRIVACY: example WORDS come only from the viewer's own rows; a guest row has none; ``scope=all`` is counts and clusters.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Optional

import struggle_signals as sig
from struggle_detector import STORE_TIMEOUT_S, get_backend, mode

logger = logging.getLogger("struggle_detector")

_FIX_HINT = {
    sig.FIX_ROUTER_TRAINING: "add router training examples for this phrasing (Phase 3 self-train feed)",
    sig.FIX_ROUTER_LATENCY: "the two-stage router timed out or failed; check the router sidecar latency",
    sig.FIX_CAPABILITY_GAP: "the right path answered but not what was asked (missing capability / answer shape)",
    sig.FIX_STT: "speech recognition misheard; review the wake/STT path or add a homophone fix",
    sig.FIX_VERBOSITY: "the answer was too long for the question; tighten the reply budget for this shape",
    sig.FIX_ANSWER_QUALITY: "the brain answered wrongly; review the prompt / memory for this domain",
}


def cluster(rows: list[dict], *, viewer_id: Optional[str] = None, examples: int = 3, top: int = 20) -> list[dict]:
    """Group rows by (signal, domain, pattern). Example WORDS come only from ``viewer_id``'s own rows. Pure."""
    groups: dict[tuple, dict] = {}
    for r in rows:
        key = (r["signal"], r.get("domain_predicted") or "unrouted", r.get("pattern") or "")
        g = groups.get(key)
        if g is None:
            g = groups[key] = {"signal": key[0], "domain": key[1], "pattern": key[2], "count": 0, "users": set(),
                               "first_seen": r["ts"], "last_seen": r["ts"], "fix_types": {}, "paths": {},
                               "examples": [], "confidence_sum": 0.0}
        g["count"] += 1
        g["users"].add("guest" if r.get("is_guest") else r["user_id"])
        g["first_seen"] = min(g["first_seen"], r["ts"])
        g["last_seen"] = max(g["last_seen"], r["ts"])
        g["fix_types"][r.get("fix_type") or ""] = g["fix_types"].get(r.get("fix_type") or "", 0) + 1
        g["paths"][r.get("path_served") or ""] = g["paths"].get(r.get("path_served") or "", 0) + 1
        g["confidence_sum"] += float(r.get("confidence") or 0.0)
        if (viewer_id and r["user_id"] == viewer_id and not r.get("is_guest") and r.get("summary")
                and len(g["examples"]) < examples and r["summary"] not in g["examples"]):
            g["examples"].append(r["summary"])
    out = []
    for g in groups.values():
        fix = max(g["fix_types"].items(), key=lambda kv: (kv[1], kv[0]))[0]
        out.append({
            "signal": g["signal"], "domain": g["domain"], "pattern": g["pattern"], "count": g["count"],
            "users": len(g["users"]), "first_seen": g["first_seen"], "last_seen": g["last_seen"],
            "mean_confidence": round(g["confidence_sum"] / g["count"], 3), "suggested_fix": fix,
            "fix_hint": _FIX_HINT.get(fix, ""), "paths": g["paths"], "examples": g["examples"],
        })
    out.sort(key=lambda c: (-c["count"], -c["last_seen"], c["signal"]))
    return out[:top]


async def digest(*, viewer_id: Optional[str] = None, user_ids: Optional[list[str]] = None, days: float = 7.0,
                 top: int = 20, now: Optional[float] = None) -> dict[str, Any]:
    """The struggles report: per-signal counts and the top clusters over the last ``days``.

    ``user_ids`` limits the rows (None = everyone; ``["guest"]`` = guest rows). Example words only from ``viewer_id``'s own
    rows (and never a guest's: guest rows hold none)."""
    t = time.time() if now is None else now
    rows = await get_backend().rows(t - days * 86400.0, user_ids)
    by_signal: dict[str, int] = {}
    for r in rows:
        by_signal[r["signal"]] = by_signal.get(r["signal"], 0) + 1
    return {"days": days, "total": len(rows), "by_signal": dict(sorted(by_signal.items())),
            "clusters": cluster(rows, viewer_id=viewer_id, top=top), "mode": mode()}


def summary_line(report: dict[str, Any]) -> str:
    """``STRUGGLE_SUMMARY window=… total=N signals=RE_ASK:2,… top=signal/domain:n,…`` - counts only, never words."""
    sigs = ",".join(f"{k}:{v}" for k, v in report.get("by_signal", {}).items()) or "-"
    top = ",".join(f"{c['signal']}/{c['domain']}:{c['count']}" for c in report.get("clusters", [])[:3]) or "-"
    return f"STRUGGLE_SUMMARY window={report.get('days')}d total={report.get('total', 0)} signals={sigs} top={top}"


async def log_nightly_summary(days: float = 1.0) -> Optional[str]:
    """The nightly operator line (``memory_digest.run_nightly_digest_pass``). Counts only. Never raises."""
    try:
        if mode() == "off":
            return None
        line = summary_line(await asyncio.wait_for(digest(days=days, top=3), STORE_TIMEOUT_S))
        logger.info(line)
        return line
    except Exception as exc:  # noqa: BLE001
        logger.info("STRUGGLE_SUMMARY unavailable (%s)", type(exc).__name__)
        return None


async def brief_line(user_id: str, *, days: float = 1.0) -> Optional[str]:
    """The owner's one-line morning-brief mention (mode ``on`` only): counts of the owner's OWN struggles + guest rows."""
    try:
        if mode() != "on":
            return None
        rep = await asyncio.wait_for(digest(viewer_id=user_id, user_ids=[user_id, "guest"], days=days, top=1),
                                     STORE_TIMEOUT_S)
        n = int(rep.get("total") or 0)
        if n <= 0:
            return None
        top = rep["clusters"][0] if rep.get("clusters") else None
        what = f", mostly {top['domain']} ({top['signal'].lower().replace('_', ' ')})" if top else ""
        return f"Yesterday I noticed {n} moment{'s' if n != 1 else ''} where I struggled{what}; the struggles report has details."
    except Exception:  # noqa: BLE001
        return None
