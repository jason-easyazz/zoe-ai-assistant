"""The real SMP/MCUmgr session for the Omi update: smpclient 7.3.0 attached to a link we already made.

UNVERIFIED ON HARDWARE beyond the read-only ``info`` stage. This is a port of the operator's staged tool
(``/home/zoe/.zoe/agent-tools/omi_dfu_2026-10-12.py``: stages info | upload | reset | post; its ``info`` stage ran twice on the real
pendant, ``upload`` / ``reset`` / ``post`` were written against the library source only). Library facts, checked in
``opensrc path pypi:smpclient@7.3.0`` (requires ``bleak>=3.0.2,<4``):

* ``SMPBLETransport._connect`` SCANS and lets BlueZ create the link (a 420 ms supervision timeout, dropped at panel range), so
  ``AttachedTransport`` overrides it to use the client the backend already connected via ``hcitool lecc`` (32 s supervision timeout).
* ``SMPClient.upload(image, slot=<image index>, upgrade=False)`` yields the offset after each chunk and raises ``SMPUploadError`` on a
  server error or a SHA-256 mismatch; ``upgrade=False`` means the library itself marks nothing - we mark explicitly, after
  verifying both slots.
* ``ImageStatesWrite(hash=h, confirm=True)`` makes the slot permanent (Omi's app uses confirm-only mode on this overwrite-only
  bootloader). ``ResetWrite()`` restarts; the link dropping during it is expected.
* We send ONLY SMP traffic plus the standard reads the caller does. No settings-partition erase, no storage-service commands.

The smpclient import is lazy: only a panel that actually runs an update needs it installed.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable, Optional

from .drivers.omi_dfu import SlotInfo, SmpSession

REQUEST_TIMEOUT_S = 15.0


def _attached_transport_class() -> Any:
    from smpclient.transport import SMP_CHARACTERISTIC_UUID
    from smpclient.transport.ble import SMPBLETransport, SMPBLETransportNotSMPServer

    class AttachedTransport(SMPBLETransport):
        """``SMPBLETransport`` over a client that is ALREADY connected. It does not own the link: ``disconnect`` is a no-op."""

        def __init__(self, link: Any) -> None:
            super().__init__()
            self._link = link

        async def _connect(self, address: str, timeout_s: float) -> None:
            client = self._link.client
            self._client = client
            self._disconnected_event.clear()
            self._link.on_disconnect(self._disconnected_event.set)
            ch = client.services.get_characteristic(SMP_CHARACTERISTIC_UUID)
            if ch is None:
                raise SMPBLETransportNotSMPServer("Missing the SMP characteristic UUID.")
            self._max_write_without_response_size = ch.max_write_without_response_size
            await client._backend._acquire_mtu()                      # BlueZ: the real ATT MTU (bleak reports 23 until asked)
            self._max_write_without_response_size = client.mtu_size - 3
            self._smp_characteristic = ch
            await self._await_or_disconnect(client.start_notify(SMP_CHARACTERISTIC_UUID, self._notify_callback))

        async def disconnect(self) -> None:
            return None

    return AttachedTransport


class RealSmpSession:
    def __init__(self, link: Any) -> None:
        self._link = link
        self._client: Optional[Any] = None

    async def open(self) -> None:
        from smpclient import SMPClient
        transport = _attached_transport_class()(self._link)
        self._client = SMPClient(transport, self._link.address, timeout_s=8)
        await self._client.connect(connect_timeout_s=60)

    async def close(self) -> None:
        self._client = None            # the link (and its hcitool handle) belongs to the caller

    async def bootloader_mode(self) -> Optional[int]:
        from smpclient.requests.os_management import BootloaderInformationRead
        try:
            r = await self._client.request(BootloaderInformationRead(query="mode"), timeout_s=10)
            mode = getattr(r, "mode", None)
            return int(mode) if mode is not None else None
        except Exception:  # noqa: BLE001 - informational: the typed model rejects the bare {mode: n} reply on this firmware
            return None

    async def images(self) -> list:
        from smpclient.generics import success
        from smpclient.requests.image_management import ImageStatesRead
        r = await self._client.request(ImageStatesRead(), timeout_s=REQUEST_TIMEOUT_S)
        if not success(r):
            raise RuntimeError(f"image list failed: {r}")
        return [SlotInfo(image=i.image, slot=i.slot, version=str(i.version), hash=(bytes(i.hash).hex() if i.hash else None),
                         active=bool(i.active), confirmed=bool(i.confirmed), pending=bool(i.pending), permanent=bool(i.permanent),
                         bootable=bool(i.bootable)) for i in r.images]

    async def upload(self, image_index: int, data: bytes, on_offset: Callable[[int], Awaitable[None]]) -> None:
        async for off in self._client.upload(data, slot=image_index, upgrade=False, first_timeout_s=60, subsequent_timeout_s=20):
            await on_offset(off)

    async def mark_permanent(self, image_hash: bytes) -> None:
        from smpclient.generics import success
        from smpclient.requests.image_management import ImageStatesWrite
        r = await self._client.request(ImageStatesWrite(hash=image_hash, confirm=True), timeout_s=20)
        if not success(r):
            raise RuntimeError(f"marking image failed: {r}")

    async def reset(self) -> None:
        from smpclient.requests.os_management import ResetWrite
        await asyncio.wait_for(self._client.request(ResetWrite(), timeout_s=10), 15)


def make_smp_session(link: Any) -> SmpSession:
    return RealSmpSession(link)
