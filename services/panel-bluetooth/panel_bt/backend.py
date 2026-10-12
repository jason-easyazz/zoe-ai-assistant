"""The radio, behind a small interface.

``BleBackend`` / ``BleLink`` are what the agent and the drivers talk to; tests give them a fake. ``BleakBackend`` is the real one
(bleak >= 3.0.2 on BlueZ - the version ``smpclient`` needs; ``omi-sync`` stays on its own 0.22.3 venv).

THE CONNECTION RECIPE is ported from ``services/omi-sync/omi_sync/ble_link.py`` (verified on the real pendant 2026-10-12, see
``docs/knowledge/bluetooth-settings.md``): create the LE link yourself with ``sudo -n hcitool lecc [--random] <addr>`` (a 32 s
supervision timeout; BlueZ's own 420 ms default kills the link during service discovery at panel range), THEN attach bleak to the
already-connected device. No pairing, no bond, no agent: the pendant's SMP Security Request is answered 'not supported' and the
link carries on. Retry with backoff, dropping the half-open link between tries; one attempt in six stalls for a minute.

bleak API used (``opensrc path pypi:bleak@3.0.2``): ``BleakScanner.discover(timeout, return_adv=True)`` -> ``{addr: (BLEDevice,
AdvertisementData)}``; ``BleakClient(device_or_address, disconnected_callback=cb, timeout=)``; ``client.services.get_characteristic
(uuid)``; ``client.read_gatt_char(uuid)``; ``client.is_connected``.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from typing import Any, Optional, Protocol, Sequence

from .model import Advert

log = logging.getLogger("panel_bt.ble")


class ConnectError(Exception):
    """Every connection attempt failed (out of range, or the link would not hold)."""


class BleLink(Protocol):
    address: str

    @property
    def is_connected(self) -> bool: ...

    @property
    def client(self) -> Any: ...

    def has_char(self, uuid: str) -> bool: ...

    async def read_char(self, uuid: str) -> bytes: ...

    def on_disconnect(self, callback: Any) -> None: ...

    async def disconnect(self) -> None: ...


class BleBackend(Protocol):
    async def scan(self, seconds: float) -> list[Advert]: ...

    async def connect(self, address: str, addr_type: str) -> BleLink: ...

    async def sample_rssi(self, address: str, seconds: float) -> list[int]:
        """Every advert RSSI heard from ``address`` in ``seconds`` (a median over many beats one noisy reading)."""
        ...


# --- the hcitool lecc half of the recipe (pure helpers, tested) ------------------------------------------------------
_HANDLE_RX = re.compile(r"handle\s+(\d+)", re.I)


def parse_lecc_handle(output: str) -> Optional[int]:
    """``hcitool lecc`` prints ``Connection handle 64`` on success."""
    m = _HANDLE_RX.search(output or "")
    return int(m.group(1)) if m else None


def _hci_args(hci: str) -> list[str]:
    return ["-i", hci] if hci else []


def lecc_argv(prefix: Sequence[str], address: str, addr_type: str, hci: str = "") -> list[str]:
    """``hcitool [-i hciN] lecc [--random] <addr>`` (supervision timeout 32 s, interval 18.75 ms)."""
    return [*prefix, "hcitool", *_hci_args(hci), "lecc", *(["--random"] if addr_type == "random" else []), address]


def ledc_argv(prefix: Sequence[str], handle: int, hci: str = "") -> list[str]:
    return [*prefix, "hcitool", *_hci_args(hci), "ledc", str(handle)]


def lecc_cancel_argv(prefix: Sequence[str], hci: str = "") -> list[str]:
    """LE Create Connection Cancel (OGF 0x08 OCF 0x000E): clears a connect still pending after our timeout."""
    return [*prefix, "hcitool", *_hci_args(hci), "cmd", "0x08", "0x000E"]


async def _run(argv: Sequence[str], timeout: float) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "timeout"
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace")


class BleakLink:
    def __init__(self, client: Any, address: str, handle: Optional[int], prefix: Sequence[str], hci: str = "",
                 listeners: Optional[list] = None) -> None:
        self._c = client
        self.address = address
        self._handle = handle
        self._prefix = tuple(prefix)
        self._hci = hci
        self._listeners = listeners if listeners is not None else []

    def on_disconnect(self, callback: Any) -> None:
        """Call ``callback()`` (no args) when the remote drops the link. bleak only accepts this at construction, so the backend
        hands every client a callback that fans out to this list."""
        self._listeners.append(callback)

    @property
    def is_connected(self) -> bool:
        return bool(self._c.is_connected)

    @property
    def client(self) -> Any:
        return self._c

    def has_char(self, uuid: str) -> bool:
        try:
            return self._c.services.get_characteristic(uuid) is not None
        except Exception:  # noqa: BLE001
            return False

    async def read_char(self, uuid: str) -> bytes:
        return bytes(await self._c.read_gatt_char(uuid))

    async def disconnect(self) -> None:
        with contextlib.suppress(Exception):
            if self._c.is_connected:
                await self._c.disconnect()
        if self._handle is not None:
            with contextlib.suppress(Exception):
                await _run(ledc_argv(self._prefix, self._handle, self._hci), 10.0)
            self._handle = None


class BleakBackend:
    def __init__(self, *, lecc: bool = True, lecc_prefix: Sequence[str] = ("sudo", "-n"), attempts: int = 4,
                 backoff_s: float = 3.0, hci: str = "") -> None:
        # ``hci``: which adapter ("hci0", "hci1", ...). The panel's built-in radio is weak (about -91 dBm even beside the panel:
        # the enclosure shields the Pi's antenna), so a USB adapter is a supported fix - name it here; '' = BlueZ's default.
        self.hci = hci
        self.lecc = lecc
        self.lecc_prefix = tuple(lecc_prefix)
        self.attempts = attempts
        self.backoff_s = backoff_s

    def _adapter_kw(self) -> dict:
        return {"adapter": self.hci} if self.hci else {}

    async def scan(self, seconds: float) -> list[Advert]:
        from bleak import BleakScanner  # type: ignore[import-not-found]
        found = await BleakScanner.discover(timeout=seconds, return_adv=True, **self._adapter_kw())
        now = time.time()
        out: list[Advert] = []
        for dev, adv in found.values():
            addr_type = "random"
            with contextlib.suppress(Exception):
                props = (dev.details or {}).get("props", {})
                addr_type = "public" if str(props.get("AddressType", "random")).lower() == "public" else "random"
            out.append(Advert(address=dev.address, addr_type=addr_type, name=(adv.local_name or dev.name or "").strip(),
                              rssi=adv.rssi if isinstance(adv.rssi, int) else None,
                              service_uuids=tuple(str(u).lower() for u in (adv.service_uuids or ())),
                              manufacturer_ids=tuple(int(k) for k in (adv.manufacturer_data or {})), seen_at=now))
        return out

    async def sample_rssi(self, address: str, seconds: float) -> list[int]:
        from bleak import BleakScanner  # type: ignore[import-not-found]
        want = address.strip().lower()
        samples: list[int] = []

        def on_adv(dev, adv) -> None:
            if (dev.address or "").lower() == want and isinstance(adv.rssi, int):
                samples.append(adv.rssi)

        async with BleakScanner(detection_callback=on_adv, **self._adapter_kw()):
            await asyncio.sleep(seconds)
        return samples

    async def connect(self, address: str, addr_type: str) -> BleLink:
        from bleak import BleakClient, BleakScanner  # type: ignore[import-not-found]
        last: Optional[BaseException] = None
        for attempt in range(1, self.attempts + 1):
            handle: Optional[int] = None
            client = None
            listeners: list = []

            def fan_out(_client: Any, _l: list = listeners) -> None:
                for cb in list(_l):
                    with contextlib.suppress(Exception):
                        cb()

            try:
                if self.lecc:
                    rc, out = await _run(lecc_argv(self.lecc_prefix, address, addr_type, self.hci), 35.0)
                    handle = parse_lecc_handle(out) if rc == 0 else None
                    if handle is None:
                        with contextlib.suppress(Exception):
                            await _run(lecc_cancel_argv(self.lecc_prefix, self.hci), 10.0)
                        raise ConnectError(f"hcitool lecc failed (rc={rc}): {out.strip()[:120]}")
                client = BleakClient(address, timeout=60.0, disconnected_callback=fan_out, **self._adapter_kw())
                try:
                    await client.connect()
                except Exception as exc:  # noqa: BLE001 - not in BlueZ's cache: one scan, then connect with the device object
                    if type(exc).__name__ != "BleakDeviceNotFoundError":
                        raise
                    dev = await BleakScanner.find_device_by_address(address, timeout=10.0, **self._adapter_kw())
                    if dev is None:
                        raise
                    client = BleakClient(dev, timeout=60.0, disconnected_callback=fan_out, **self._adapter_kw())
                    await client.connect()
                return BleakLink(client, address, handle, self.lecc_prefix, self.hci, listeners)
            except Exception as exc:  # noqa: BLE001
                last = exc
                log.info("connect attempt %d/%d failed: %s: %s", attempt, self.attempts, type(exc).__name__, str(exc)[:120])
                if client is not None:
                    with contextlib.suppress(Exception):
                        await client.disconnect()
                if handle is not None:
                    with contextlib.suppress(Exception):
                        await _run(ledc_argv(self.lecc_prefix, handle, self.hci), 10.0)
                if attempt < self.attempts:
                    await asyncio.sleep(self.backoff_s * attempt)
        raise ConnectError(f"could not connect after {self.attempts} attempts: {type(last).__name__ if last else 'n/a'}") from last
