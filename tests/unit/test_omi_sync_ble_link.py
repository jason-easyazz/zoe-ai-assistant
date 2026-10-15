"""omi-sync never leaves the controller initiating, and clears a pendant link that is already up before connecting.

The 2026-10-15 incident: one ``hcitool lecc`` timed out as the owner walked out of range. hcitool gives up WITHOUT sending LE
Create Connection Cancel, so the controller kept initiating; every later ``lecc`` was refused ``Command Disallowed`` (hcitool:
``Input/output error``) for 11 hours, and when the pendant came home the stale initiation completed into a link nobody owned -
re-created within 110 ms of every ``ledc`` (btmon showed no new Create Connection in between).

bleak cannot attach to such a link (its BlueZ connect scans first and a connected pendant does not advertise:
``BleakDeviceNotFoundError`` after the full timeout, measured live), so it is cleared - cancel FIRST, then ``ledc`` - and our
own ``lecc`` follows. ``FakeHci`` models the controller as btmon showed it.

Source mutations that must turn this file red (re-run by Edit): drop the ``_cancel_pending`` call after a failed ``lecc`` ->
the cancel tests go red; drop the clearing of an existing link, or ``ledc`` it before cancelling -> the stale-link tests go red;
drop the ``_drop_link`` in the ``finally`` -> the teardown assertions go red.
"""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "omi-sync"))

from omi_sync import ble_link, ring_protocol as rp  # noqa: E402
from omi_sync.radio_lock import RadioLock  # noqa: E402

ADDR = "E4:A3:57:6C:8D:0D"
CANCEL = ["sudo", "-n", "hcitool", "cmd", "0x08", "0x000E"]


class FakeHci:
    """Scripted ``hcitool`` over a controller that behaves like the panel Pi's (btmon, 2026-10-15).

    ``con`` lists ``links``. ``lecc`` while an initiation is ``pending`` is refused (Command Disallowed); ``lecc`` for a peer that
    is already connected is ACCEPTED and left pending (hcitool still fails); otherwise it answers from ``lecc_answers``. ``ledc``
    of a peer with a pending initiation re-creates the link at once with a new handle; the cancel clears ``pending``."""

    EIO = (1, "Could not create connection: Input/output error")

    def __init__(self, *, links: dict[str, int] | None = None, lecc_answers: list[tuple[int, str]] | None = None,
                 pending: str | None = None) -> None:
        self.links = dict(links or {})
        self.lecc_answers = list(lecc_answers or [])
        self.pending = pending
        self.next_handle = 90
        self.calls: list[list[str]] = []

    async def run(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        cmd = argv[argv.index("hcitool") + 1]
        if cmd == "con":
            lines = ["Connections:"] + [f"\t< LE {a} handle {h} state 1 lm CENTRAL " for a, h in self.links.items()]
            return 0, "\n".join(lines) + "\n"
        if cmd == "lecc":
            addr = argv[-1]
            if self.pending is not None:
                return self.EIO
            if addr in self.links:
                self.pending = addr
                return self.EIO
            rc, out = self.lecc_answers.pop(0)
            h = ble_link.parse_lecc_handle(out) if rc == 0 else None
            if h is not None:
                self.links[addr] = h
            elif rc == -1 or (rc != 0 and "timed out" in out):
                self.pending = addr                         # hcitool gave up; the controller did not
            return rc, out
        if cmd == "ledc":
            gone = [a for a, h in self.links.items() if h == int(argv[-1])]
            self.links = {a: h for a, h in self.links.items() if h != int(argv[-1])}
            for a in gone:
                if self.pending == a:                       # the stale initiation completes the instant the peer is free
                    self.pending = None
                    self.links[a] = self.next_handle
                    self.next_handle += 1
            return 0, ""
        if cmd == "cmd" and argv[-2:] == ["0x08", "0x000E"]:
            self.pending = None
            return 0, ""
        return 0, ""

    def of(self, cmd: str) -> list[list[str]]:
        return [c for c in self.calls if c[c.index("hcitool") + 1] == cmd]


class FakeClient:
    def __init__(self, *_a, **_kw) -> None:
        self.is_connected = False

    async def connect(self):
        self.is_connected = True

    async def start_notify(self, uuid, cb):
        assert uuid == rp.STORAGE_DATA_UUID

    async def stop_notify(self, uuid):
        pass

    async def disconnect(self):
        self.is_connected = False


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_BLE_RADIO_LOCK", str(tmp_path / "radio.lock"))
    monkeypatch.setitem(sys.modules, "bleak", types.SimpleNamespace(BleakClient=FakeClient, BleakScanner=None))

    async def no_sleep(_s):
        return None
    monkeypatch.setattr(ble_link.asyncio, "sleep", no_sleep)


def use(monkeypatch, hci: FakeHci) -> FakeHci:
    monkeypatch.setattr(ble_link, "_run", hci.run)
    return hci


# --- pure helpers -------------------------------------------------------------------------------------------------------
def test_the_cancel_is_le_create_connection_cancel():
    assert ble_link.lecc_cancel_argv(("sudo", "-n")) == CANCEL
    assert ble_link.lecc_cancel_argv(()) == ["hcitool", "cmd", "0x08", "0x000E"]


def test_hcitool_con_is_parsed_for_the_pendant_only():
    out = ("Connections:\n\t< LE AA:BB:CC:DD:EE:FF handle 70 state 1 lm CENTRAL \n"
           "\t< LE e4:a3:57:6c:8d:0d handle 64 state 1 lm CENTRAL \n")
    assert ble_link.parse_con_handle(out, ADDR) == 64
    assert ble_link.parse_con_handle(out, "11:22:33:44:55:66") is None
    assert ble_link.parse_con_handle("Connections:\n", ADDR) is None
    assert ble_link.parse_con_handle("", ADDR) is None


# --- the connect recipe -------------------------------------------------------------------------------------------------
async def test_a_failed_lecc_is_always_followed_by_a_cancel(monkeypatch):
    hci = use(monkeypatch, FakeHci(lecc_answers=[(1, "Could not create connection: Connection timed out"),
                                                  (1, "Could not create connection: Input/output error")]))
    with pytest.raises(ble_link.ConnectError):
        async with ble_link.connect(ADDR, attempts=2):
            pytest.fail("must not connect")
    assert len(hci.of("lecc")) == 2
    assert hci.of("cmd") == [CANCEL, CANCEL], "every failed lecc must cancel the controller's pending initiation"
    assert hci.pending is None


async def test_a_killed_lecc_is_cancelled_too(monkeypatch):
    hci = use(monkeypatch, FakeHci(lecc_answers=[(-1, "timeout")]))
    with pytest.raises(ble_link.ConnectError):
        async with ble_link.connect(ADDR, attempts=1):
            pass
    assert hci.of("cmd") == [CANCEL]
    assert hci.pending is None


async def test_the_incident_a_timed_out_lecc_does_not_wedge_the_next_attempt(monkeypatch):
    """Walked out of range (lecc times out, initiation left pending), came back: the very next attempt connects."""
    hci = use(monkeypatch, FakeHci(lecc_answers=[(1, "Could not create connection: Connection timed out"),
                                                  (0, "Connection handle 64\n")]))
    async with ble_link.connect(ADDR, attempts=2):
        assert hci.links == {ADDR: 64}
    assert hci.links == {} and hci.pending is None


async def test_an_owner_less_link_with_a_stale_initiation_is_cleared_cancel_first_then_ours_is_created(monkeypatch):
    """The live state on 2026-10-15 evening: a link up (handle 65) AND an initiation pending behind it."""
    hci = use(monkeypatch, FakeHci(links={ADDR: 65}, pending=ADDR, lecc_answers=[(0, "Connection handle 64\n")]))
    async with ble_link.connect(ADDR, attempts=1) as link:
        assert isinstance(link, ble_link.BleLink)
        assert hci.links == {ADDR: 64}, "the stale link must be gone and ours up"
    first_cancel = hci.calls.index(CANCEL)
    first_ledc = next(i for i, c in enumerate(hci.calls) if "ledc" in c)
    assert first_cancel < first_ledc, "ledc before the cancel lets the controller re-create the link at once"
    assert hci.of("ledc") == [["sudo", "-n", "hcitool", "ledc", "65"], ["sudo", "-n", "hcitool", "ledc", "64"]]
    assert hci.links == {} and hci.pending is None


async def test_an_owner_less_link_alone_is_cleared_then_ours_is_created(monkeypatch):
    hci = use(monkeypatch, FakeHci(links={ADDR: 65}, lecc_answers=[(0, "Connection handle 64\n")]))
    async with ble_link.connect(ADDR, attempts=1):
        assert hci.links == {ADDR: 64}
    assert hci.of("ledc") == [["sudo", "-n", "hcitool", "ledc", "65"], ["sudo", "-n", "hcitool", "ledc", "64"]]
    assert hci.links == {} and hci.pending is None


async def test_a_fresh_link_is_created_and_dropped_without_a_cancel(monkeypatch):
    hci = use(monkeypatch, FakeHci(lecc_answers=[(0, "Connection handle 64\n")]))
    async with ble_link.connect(ADDR, attempts=2):
        assert hci.links == {ADDR: 64}
    assert hci.of("ledc") == [["sudo", "-n", "hcitool", "ledc", "64"]]
    assert hci.of("cmd") == []
    assert hci.links == {}


async def test_a_link_to_another_device_is_left_alone(monkeypatch):
    hci = use(monkeypatch, FakeHci(links={"AA:BB:CC:DD:EE:FF": 70}, lecc_answers=[(0, "Connection handle 64\n")]))
    async with ble_link.connect(ADDR, attempts=1):
        pass
    assert len(hci.of("lecc")) == 1
    assert hci.of("ledc") == [["sudo", "-n", "hcitool", "ledc", "64"]]
    assert hci.of("cmd") == []
    assert hci.links == {"AA:BB:CC:DD:EE:FF": 70}


async def test_without_lecc_nothing_is_run(monkeypatch):
    hci = use(monkeypatch, FakeHci(links={ADDR: 65}))
    async with ble_link.connect(ADDR, lecc=False, attempts=1):
        pass
    assert hci.calls == []


async def test_a_connect_cancelled_mid_lecc_cancels_the_initiation_and_frees_the_radio(monkeypatch):
    hci = use(monkeypatch, FakeHci())
    started = asyncio.Event()
    real = hci.run

    async def run(argv, timeout):
        if "lecc" in argv:
            hci.calls.append(list(argv))
            started.set()
            await asyncio.Event().wait()           # the pendant is out of range: hcitool is still waiting
        return await real(argv, timeout)
    monkeypatch.setattr(ble_link, "_run", run)

    async def go():
        async with ble_link.connect(ADDR, attempts=2):
            pytest.fail("must not connect")
    task = asyncio.ensure_future(go())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert hci.of("cmd") == [CANCEL], "a cancelled connect must not leave the controller initiating"
    lock = RadioLock()
    assert lock.try_acquire(), "a cancelled connect must give the radio back"
    lock.release()
