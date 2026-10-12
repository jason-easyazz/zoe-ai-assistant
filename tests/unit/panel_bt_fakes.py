"""Fakes for the panel Bluetooth agent tests: a radio with no hardware, a pendant with GATT values, an SMP session with MCUboot slots.

Synthetic data only (the address below is a made-up locally-administered one).
"""
from __future__ import annotations

import hashlib
import io
import json
import struct
import sys
import zipfile
from pathlib import Path
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "panel-bluetooth"))

from panel_bt import config  # noqa: E402
from panel_bt.backend import ConnectError  # noqa: E402
from panel_bt.drivers.base import BATTERY_LEVEL, DIS_FIRMWARE, DIS_HARDWARE, DIS_MANUFACTURER, DIS_MODEL  # noqa: E402
from panel_bt.drivers.omi import OMI_AUDIO_SERVICE  # noqa: E402
from panel_bt.drivers.omi_dfu import OMI_CHARGING_UUID, SlotInfo  # noqa: E402
from panel_bt.drivers.omi_release import Pin, PinnedImage, sha256_hex  # noqa: E402
from panel_bt.model import Advert  # noqa: E402

OMI_ADDR = "E6:11:22:33:44:55"
SENSOR_ADDR = "AA:BB:CC:00:11:22"
PHONE_ADDR = "7C:00:00:00:00:01"


def settings(tmp: Path, **env):
    base = {"PANEL_BT_ENABLED": "1", "PANEL_BT_DIR": str(tmp / "bt"), "ZOE_BLE_RADIO_LOCK": str(tmp / "radio.lock"),
            "OMI_SYNC_DIR": str(tmp / "omi"), "DEVICE_TOKEN": "tok", "ZOE_URL": "https://zoe.test", "PANEL_BT_SCAN_S": "3"}
    base.update({k: str(v) for k, v in env.items()})
    return config.load(base)


def omi_chars(fw="3.0.18", battery=95, charging=True, model="Omi CV 1"):
    c = {DIS_MANUFACTURER: b"Based Hardware", DIS_MODEL: model.encode(), DIS_FIRMWARE: fw.encode(), DIS_HARDWARE: b"5.0",
         BATTERY_LEVEL: bytes([battery])}
    if charging is not None:
        c[OMI_CHARGING_UUID] = bytes([1 if charging else 0])
    return c


class FakeLink:
    def __init__(self, address: str, chars: dict, on_close: Optional[Callable] = None) -> None:
        self.address = address
        self.chars = dict(chars)
        self.connected = True
        self.closed = 0
        self.client = object()
        self._on_close = on_close
        self.listeners = []

    @property
    def is_connected(self) -> bool:
        return self.connected

    def has_char(self, uuid: str) -> bool:
        return uuid in self.chars

    async def read_char(self, uuid: str) -> bytes:
        if not self.connected:
            raise RuntimeError("link dropped")
        v = self.chars[uuid]
        if isinstance(v, Exception):
            raise v
        return v

    def on_disconnect(self, cb) -> None:
        self.listeners.append(cb)

    async def disconnect(self) -> None:
        self.connected = False
        self.closed += 1
        if self._on_close:
            self._on_close(self)


class FakeBackend:
    """Adverts to scan, GATT tables to connect to, and a script of connect failures. Records every radio call."""

    def __init__(self, adverts=None, chars=None) -> None:
        self.adverts = list(adverts or [])
        self.chars = dict(chars or {})           # address -> dict(uuid -> bytes)
        self.connect_fail = 0                    # fail the next N connects
        self.rssi = {}                           # address -> list (a callable may be set for sequences)
        self.calls = []
        self.links: list[FakeLink] = []
        self.on_connect: Optional[Callable] = None

    async def scan(self, seconds: float):
        self.calls.append(("scan", seconds))
        return list(self.adverts)

    async def connect(self, address: str, addr_type: str):
        self.calls.append(("connect", address))
        if self.connect_fail > 0:
            self.connect_fail -= 1
            raise ConnectError("fake: out of range")
        link = FakeLink(address, self.chars.get(address, {}))
        self.links.append(link)
        if self.on_connect:
            self.on_connect(link)
        return link

    async def sample_rssi(self, address: str, seconds: float):
        self.calls.append(("sample_rssi", address))
        v = self.rssi.get(address, [])
        return v() if callable(v) else list(v)

    def live_links(self):
        return [lk for lk in self.links if lk.connected]


def advert(address=OMI_ADDR, name="Omi", rssi=-60, services=(OMI_AUDIO_SERVICE,), mfr=()):
    return Advert(address=address, addr_type="random", name=name, rssi=rssi, service_uuids=tuple(services), manufacturer_ids=tuple(mfr), seen_at=0.0)


# --- a signed-looking MCUboot image + the official zip layout ---------------------------------------------------------------
def mcuboot_image(body: bytes) -> bytes:
    hdr, prot = 32, 0
    head = struct.pack("<IIHHII", 0x96F3B83D, 0, hdr, prot, len(body), 0) + b"\x00" * (hdr - 20)
    covered = head + body
    sha = hashlib.sha256(covered).digest()
    tlv = struct.pack("<HH", 0x6907, 4 + 4 + 32) + struct.pack("<BxH", 0x10, 32) + sha
    return covered + tlv


def make_package(sizes=(300, 200)):
    imgs = [mcuboot_image(bytes([i + 1]) * n) for i, n in enumerate(sizes)]
    files = [{"image_index": str(i), "file": ["omi.signed.bin", "ipc_radio.bin"][i], "size": len(b), "board": "x"} for i, b in enumerate(imgs)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", json.dumps({"format-version": 1, "files": files}))
        for f, b in zip(files, imgs):
            z.writestr(f["file"], b)
    data = buf.getvalue()
    pin = Pin(model="Omi CV 1", version="3.0.21", url="https://github.com/BasedHardware/omi/releases/download/x/y.zip", sha256=sha256_hex(data),
              size=len(data), minimum_firmware="3.0.6", changelog="fix", source="test",
              images=tuple(PinnedImage(i, f["file"], len(imgs[i]), ["app", "net"][i]) for i, f in enumerate(files)))
    return data, pin, imgs


class FakeSmp:
    """MCUboot-ish slots. ``scenario`` flags inject the failures each safety rule must survive."""

    def __init__(self, images, from_fw="3.0.18", to_fw="3.0.21", link=None, **flags) -> None:
        self.pkg = images                                  # list[PackageImage]
        self.flags = flags
        self.calls: list = []
        self.slots = [SlotInfo(0, 0, "0.0.0", "a" * 64, active=True, confirmed=True)]
        self.link = link
        self.to_fw = to_fw
        self.rebooted = False

    async def open(self):
        self.calls.append("open")
        if self.flags.get("open_fails"):
            raise RuntimeError("smp open failed")

    async def close(self):
        self.calls.append("close")

    async def bootloader_mode(self):
        return 2

    async def images(self):
        self.calls.append("images")
        return list(self.slots)

    async def upload(self, image_index, data, on_offset):
        self.calls.append(("upload", image_index))
        if self.flags.get("fail_upload_at") == image_index:
            raise RuntimeError("link lost mid-upload")
        step = max(1, len(data) // 4)
        for off in range(step, len(data) + 1, step):
            await on_offset(min(off, len(data)))
        h = self.pkg[image_index].hash.hex()
        if self.flags.get("corrupt_hash_for") == image_index:
            h = "f" * 64
        self.slots.append(SlotInfo(image_index, 1, "0.0.0", h))

    async def mark_permanent(self, image_hash):
        self.calls.append(("mark", image_hash.hex()[:8]))
        if self.flags.get("mark_fails"):
            raise RuntimeError("mark failed")
        self.slots = [SlotInfo(s.image, s.slot, s.version, s.hash, s.active, s.confirmed, True if s.slot == 1 and s.hash == image_hash.hex() else s.pending,
                               True if s.slot == 1 and s.hash == image_hash.hex() else s.permanent) for s in self.slots]
        if self.rebooted:
            self.slots = [SlotInfo(s.image, s.slot, s.version, s.hash, s.active, True, False, s.permanent) for s in self.slots]

    async def reset(self):
        self.calls.append("reset")
        self.rebooted = True
        if self.link is not None:
            self.link.connected = False
        self.slots = [SlotInfo(0, 0, "0.0.0", self.pkg[0].hash.hex(), active=True, confirmed=bool(self.flags.get("auto_confirmed")))]
        raise ConnectionResetError("link dropped by the reset")
