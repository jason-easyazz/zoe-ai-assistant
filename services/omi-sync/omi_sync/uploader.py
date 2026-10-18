"""Upload a recording to Zoe and decide what the answer means for custody.

The ONLY thing that may free audio is ``Outcome.CONFIRMED``: HTTP 200 whose JSON says
``ok`` and echoes the SHA-256 of the exact bytes we sent (or says the recording was
deleted by its owner - a tombstone hit, which is also "Zoe has dealt with it"). Every
other answer keeps the audio: ``REJECTED`` is a permanent per-recording refusal (kept,
logged loudly, never retried in a loop), ``NOT_REGISTERED`` is Zoe saying "this pendant is not assigned to any household member"
(409 ``device_not_registered`` - an admin has to assign it; custody is KEPT and the next try is slow), ``UNAVAILABLE`` is everything
transient or config-shaped (Zoe down, the feature flag off => 404, bad token, owner unset).

Stdlib ``urllib`` on purpose: the Pi service has two dependencies (bleak, opuslib) and
does not need a third for one POST.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Protocol

log = logging.getLogger("omi_sync.uploader")

#: statuses that mean "this recording, as sent, will never be accepted" (not "try later")
_PERMANENT = {400, 409, 413, 415, 422}


class Outcome(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    NOT_REGISTERED = "not_registered"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class UploadMeta:
    device_id: str          # a stable HASH of the pendant address - the MAC never leaves the Pi
    key: str                # Segment.key
    started_at: float       # epoch seconds, host timeline
    duration_s: float
    time_quality: str
    gap_before: int
    sha256: str


@dataclass(frozen=True)
class UploadResult:
    outcome: Outcome
    detail: str = ""


class Uploader(Protocol):
    async def upload(self, meta: UploadMeta, wav: bytes) -> UploadResult: ...
    async def notify_synced(self, count: int, device_id: str = "") -> None: ...


def device_hash(address: str) -> str:
    """A stable, non-reversible id for the pendant (16 hex chars). The address itself stays on the Pi."""
    return hashlib.sha256(("omi-sync:" + address.strip().lower()).encode()).hexdigest()[:16]


NOT_REGISTERED_CODE = "device_not_registered"


def _error_code(body: bytes) -> str:
    try:
        doc = json.loads(body.decode("utf-8", "replace") or "{}")
    except ValueError:
        return ""
    return str(doc.get("error") or "") if isinstance(doc, dict) else ""


def _answered_ok(body: bytes) -> bool:
    """True only for a JSON object whose ``ok`` is the boolean ``true`` (not 1, "true" or a non-JSON body)."""
    try:
        doc = json.loads(body.decode("utf-8", "replace") or "null")
    except ValueError:
        return False
    return isinstance(doc, dict) and doc.get("ok") is True


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def classify(status: int, body: bytes, sent_sha: str) -> UploadResult:
    """Pure: map an HTTP answer to a custody outcome. The echo check is what makes 'confirmed' mean 'stored THESE bytes'."""
    if status == 200:
        try:
            doc = json.loads(body.decode("utf-8", "replace") or "{}")
        except ValueError:
            return UploadResult(Outcome.UNAVAILABLE, "200 with a non-JSON body")
        if not isinstance(doc, dict) or doc.get("ok") is not True:
            return UploadResult(Outcome.UNAVAILABLE, "200 without ok:true")
        if doc.get("deleted") is True:
            return UploadResult(Outcome.CONFIRMED, "owner had deleted this recording")
        if doc.get("sha256") != sent_sha:
            return UploadResult(Outcome.UNAVAILABLE, "200 but the echoed sha256 does not match what was sent")
        return UploadResult(Outcome.CONFIRMED, "duplicate" if doc.get("duplicate") else "stored")
    if status == 409 and _error_code(body) == NOT_REGISTERED_CODE:
        # NOT a per-recording refusal: nothing is wrong with these bytes, nobody owns this pendant yet. Keep everything and ask again later.
        return UploadResult(Outcome.NOT_REGISTERED, "device_not_registered")
    if status in _PERMANENT:
        return UploadResult(Outcome.REJECTED, f"HTTP {status}")
    return UploadResult(Outcome.UNAVAILABLE, f"HTTP {status}")


def iso_utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class HttpUploader:
    def __init__(self, base_url: str, device_token: str, *, verify_ssl: bool = True, timeout_s: float = 120.0) -> None:
        self.base = base_url.rstrip("/")
        self.token = device_token
        self.timeout = timeout_s
        self._ctx: Optional[ssl.SSLContext] = None
        if not verify_ssl:
            self._ctx = ssl.create_default_context()
            self._ctx.check_hostname = False
            self._ctx.verify_mode = ssl.CERT_NONE

    def _post(self, path: str, data: bytes, headers: dict[str, str], timeout: Optional[float] = None) -> tuple[int, bytes]:
        req = urllib.request.Request(self.base + path, data=data, method="POST", headers={"X-Device-Token": self.token, **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout, context=self._ctx) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    async def upload(self, meta: UploadMeta, wav: bytes) -> UploadResult:
        headers = {
            "Content-Type": "audio/wav",
            "X-Omi-Device": meta.device_id,
            "X-Omi-Key": meta.key,
            "X-Omi-Started-At": iso_utc(meta.started_at),
            "X-Omi-Duration-S": f"{meta.duration_s:.3f}",
            "X-Omi-Time-Quality": meta.time_quality,
            "X-Omi-Gap-Before": str(meta.gap_before),
            "X-Omi-Sha256": meta.sha256,
        }
        try:
            status, body = await asyncio.to_thread(self._post, "/api/omi/recordings", wav, headers)
        except Exception as exc:  # noqa: BLE001 - connection refused / timeout / DNS: Zoe is simply not there
            return UploadResult(Outcome.UNAVAILABLE, type(exc).__name__)
        res = classify(status, body, meta.sha256)
        if res.outcome is not Outcome.CONFIRMED:
            log.warning("upload of %s not confirmed: %s (%s)", meta.key, res.outcome.value, res.detail)
        return res

    async def notify_synced(self, count: int, device_id: str = "") -> None:
        """Best-effort 'I synced N recordings' notice trigger (Zoe sends it to the pendant's OWNER only, found from ``device_id``).
        Never raises; losing it loses nothing."""
        try:
            await asyncio.to_thread(self._post, "/api/omi/sync-notice", json.dumps({"count": int(count), "device": device_id}).encode(),
                                    {"Content-Type": "application/json"})
        except Exception as exc:  # noqa: BLE001
            log.info("sync notice not sent: %s", type(exc).__name__)

    async def report_health(self, report: dict) -> bool:
        """Best-effort numbers-only health heartbeat (``POST /api/omi/device-status``). True only when Zoe answered 200 ``ok``. Never
        raises and never waits long (15 s): it runs AFTER a visit's custody work and must not delay the next one. A 401/404/409 (token
        wrong, Zoe older than this, pendant not assigned yet) is simply 'not delivered'."""
        try:
            status, body = await asyncio.to_thread(self._post, "/api/omi/device-status", json.dumps(report).encode(),
                                                   {"Content-Type": "application/json"}, 15.0)
        except Exception as exc:  # noqa: BLE001
            log.info("health report not sent: %s", type(exc).__name__)
            return False
        if status != 200:
            log.info("health report not accepted: HTTP %s %s", status, _error_code(body))
            return False
        # A 200 alone is not "Zoe stored it": a proxy, a captive portal or a stub answers 200 with some other page. Only Zoe's own
        # ``{"ok": true}`` suppresses the retry (the reporter would otherwise wait out the whole heartbeat on a report nobody received).
        if not _answered_ok(body):
            log.info("health report not confirmed: HTTP 200 without ok=true")
            return False
        return True

    async def reachable(self) -> bool:
        def _get() -> bool:
            try:
                with urllib.request.urlopen(self.base + "/health", timeout=10, context=self._ctx) as r:
                    return 200 <= r.status < 300
            except Exception:  # noqa: BLE001
                return False
        return await asyncio.to_thread(_get)
