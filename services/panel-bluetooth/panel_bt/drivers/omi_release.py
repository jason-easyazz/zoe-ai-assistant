"""The pinned official Omi CV1 firmware release: metadata, download, and verification. Pure except ``fetch_package``.

NOTHING here talks to Omi's cloud. The pin (``panel_bt/data/omi_firmware.json``, or ``PANEL_BT_OTA_PIN_FILE``) names ONE GitHub release
asset by URL, version, size and SHA-256. An update is possible only if the bytes downloaded hash to exactly that digest; there is no
"latest" lookup and no other source. To move to a newer firmware a person edits the pin in a reviewed change.

``parse_package`` is ported from the supervised 2026-10-12 attempt (``omi_dfu_2026-10-12.py`` ``parse_zip``): every image in the
zip must be a well-formed MCUboot image (magic ``0x96F3B83D``, the protected-TLV area accounted for) whose SHA-256 TLV equals the
hash we compute over header + body + protected TLVs, and the zip's manifest must list exactly the images the pin lists, at the pinned
sizes. The computed hashes are what the device's secondary slots are verified against after upload.
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
import urllib.request
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlparse

from ..model import version_tuple

DEFAULT_PIN = Path(__file__).resolve().parent.parent / "data" / "omi_firmware.json"
ALLOWED_HOSTS = ("github.com",)
MCUBOOT_MAGIC = 0x96F3B83D
_TLV_MAGICS = (0x6907, 0x6908)
_TLV_SHA256 = 0x10
_SHA_RX = re.compile(r"^[0-9a-f]{64}$")


class ReleaseError(Exception):
    """The pin is malformed, the download does not match it, or the package is not what the pin says."""


@dataclass(frozen=True)
class PinnedImage:
    index: int
    file: str
    size: int
    core: str


@dataclass(frozen=True)
class Pin:
    model: str
    version: str
    url: str
    sha256: str
    size: int
    minimum_firmware: str
    changelog: str
    source: str
    images: tuple

    @property
    def version_tuple(self) -> tuple:
        return version_tuple(self.version) or ()


@dataclass(frozen=True)
class PackageImage:
    index: int
    file: str
    data: bytes
    hash: bytes            # SHA-256 over header + body + protected TLVs (what the device reports for the slot)
    size: int
    core: str


def load_pin(path: Optional[Path] = None) -> Pin:
    p = Path(path) if path else DEFAULT_PIN
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ReleaseError(f"firmware pin unreadable: {type(exc).__name__}") from exc
    try:
        url = str(doc["url"])
        u = urlparse(url)
        if u.scheme != "https" or u.hostname not in ALLOWED_HOSTS:
            raise ReleaseError("firmware pin url must be https on github.com")
        sha = str(doc["sha256"]).lower()
        if not _SHA_RX.match(sha):
            raise ReleaseError("firmware pin sha256 is not 64 hex characters")
        ver = str(doc["version"])
        if version_tuple(ver) is None:
            raise ReleaseError("firmware pin version is not a dotted version")
        images = tuple(PinnedImage(int(i["index"]), str(i["file"]), int(i["size"]), str(i.get("core", ""))) for i in doc["images"])
        if not images or sorted(i.index for i in images) != list(range(len(images))):
            raise ReleaseError("firmware pin images must be indexed 0..n-1")
        return Pin(model=str(doc["model"]), version=ver, url=url, sha256=sha, size=int(doc["size"]),
                   minimum_firmware=str(doc.get("minimum_firmware_required", "")), changelog=str(doc.get("changelog", ""))[:300],
                   source=str(doc.get("source", ""))[:200], images=images)
    except (KeyError, TypeError, ValueError) as exc:
        raise ReleaseError(f"firmware pin malformed: {type(exc).__name__}") from exc


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _default_download(url: str, max_bytes: int) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "zoe-panel-bluetooth"})
    with urllib.request.urlopen(req, timeout=120) as resp:      # follows GitHub's redirect to its asset CDN; the digest is the guard
        data = resp.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ReleaseError("download larger than the pinned size allows")
    return data


def fetch_package(pin: Pin, cache_dir: Path, *, download: Callable[[str, int], bytes] = _default_download) -> bytes:
    """The pinned zip's bytes, verified. A cached copy is used only if it hashes to the pin; a download is kept only if it does."""
    cache_dir = Path(cache_dir)
    cached = cache_dir / f"omi-cv1-{pin.version}-{pin.sha256[:12]}.zip"
    try:
        data = cached.read_bytes()
        if sha256_hex(data) == pin.sha256 and len(data) == pin.size:
            return data
    except OSError:
        pass
    data = download(pin.url, pin.size + 1_048_576)
    if len(data) != pin.size or sha256_hex(data) != pin.sha256:
        raise ReleaseError("the downloaded firmware does not match the pinned SHA-256 - refusing to use it")
    cache_dir.mkdir(parents=True, exist_ok=True)
    tmp = cached.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(cached)
    return data


def _mcuboot_hash(name: str, b: bytes) -> bytes:
    if len(b) < 32:
        raise ReleaseError(f"{name}: too short for an MCUboot header")
    magic, _, hdr, prot, isz, _flags = struct.unpack_from("<IIHHII", b, 0)
    if magic != MCUBOOT_MAGIC or len(b) < hdr + isz:
        raise ReleaseError(f"{name}: not a valid MCUboot image")
    tlv_off = hdr + isz + prot
    if len(b) < tlv_off + 4:
        raise ReleaseError(f"{name}: truncated TLV area")
    tlv_magic, tlv_total = struct.unpack_from("<HH", b, tlv_off)
    if tlv_magic not in _TLV_MAGICS:
        raise ReleaseError(f"{name}: bad TLV magic")
    pos, end, tlv_sha = tlv_off + 4, min(len(b), tlv_off + tlv_total), None
    while pos + 4 <= end:
        ty, ln = struct.unpack_from("<BxH", b, pos)
        if ty == _TLV_SHA256:
            tlv_sha = b[pos + 4: pos + 4 + ln]
        pos += 4 + ln
    calc = hashlib.sha256(b[:hdr + isz + prot]).digest()
    if tlv_sha != calc:
        raise ReleaseError(f"{name}: the image's own SHA-256 does not match its contents")
    return calc


def parse_package(zip_bytes: bytes, pin: Pin) -> list[PackageImage]:
    """Open the verified zip and return its images (ordered by index), each checked against the pin and its MCUboot header."""
    try:
        z = zipfile.ZipFile(BytesIO(zip_bytes))
        manifest = json.loads(z.read("manifest.json"))
        files = manifest["files"]
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise ReleaseError(f"firmware package unreadable: {type(exc).__name__}") from exc
    pinned = {i.index: i for i in pin.images}
    out: list[PackageImage] = []
    for f in files:
        idx = int(f["image_index"])
        want = pinned.get(idx)
        if want is None or f["file"] != want.file:
            raise ReleaseError(f"package image {idx} ({f.get('file')}) is not in the pin")
        data = z.read(f["file"])
        if len(data) != want.size or int(f.get("size", -1)) != want.size:
            raise ReleaseError(f"package image {idx} has an unexpected size")
        out.append(PackageImage(idx, want.file, data, _mcuboot_hash(want.file, data), len(data), want.core))
    if sorted(i.index for i in out) != sorted(pinned):
        raise ReleaseError("package does not hold exactly the pinned images")
    out.sort(key=lambda i: i.index)
    return out
