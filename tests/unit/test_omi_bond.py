"""panel-bluetooth ``omi_bond``: the owner-run secure pairing of a pendant on Zoe phase-2 firmware (bond.py + the agent op).

No hardware: a fake backend/link and a scripted ``busctl`` / ``hcitool``. Source mutations that must turn this file red:
  drop the ``zoe_firmware`` check in ``bond_pendant``            -> test_a_stock_pendant_is_never_bonded
  accept "Pair returned" without reading Paired+Bonded           -> test_a_bond_that_bluez_does_not_hold_is_refused
  drop the ENCRYPT read                                          -> test_an_unencrypted_link_after_pairing_is_refused
  RemoveDevice without ``replace``                               -> test_replace_removes_only_this_pendant_and_only_when_asked
  skip making the adapter pairable, or never reset it            -> test_a_zoe_pendant_..., test_the_adapter_is_never_left_pairable
  drop the ``bond_enabled`` / ``confirm`` / ``ack_wipe`` checks   -> test_the_op_is_flag_dark_and_needs_a_confirm
  ignore the PairableTimeout write or its read-back              -> test_pairing_is_never_enabled_without_an_installed_timeout
  ignore the RemoveDevice result or the Paired read-back         -> test_replace_stops_unless_the_old_entry_is_really_gone
  drop the unsynced-recordings refusal                           -> test_pairing_waits_for_unsynced_recordings_unless_forced
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

from tests.unit.panel_bt_fakes import OMI_ADDR, SENSOR_ADDR, FakeBackend, advert, omi_chars, settings  # noqa: E402

from panel_bt import bond  # noqa: E402
from panel_bt.agent import PanelBluetoothAgent  # noqa: E402
from panel_bt.drivers import DriverRegistry  # noqa: E402
from panel_bt.model import device_id  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "omi-sync"))
from omi_sync import link_security  # noqa: E402

ZOE_FW = "3.0.21.1"
PATH = "/org/bluez/hci0/dev_E6_11_22_33_44_55"


class Client:
    def __init__(self, sysbus: "SysBus", fail: bool = False) -> None:
        self.sys, self.fail, self.paired = sysbus, fail, 0

    async def pair(self):
        self.paired += 1
        if self.fail:
            raise RuntimeError("org.bluez.Error.AuthenticationFailed")
        # a non-bondable adapter pairs WITHOUT keys (Paired yes, Bonded no): the panel's adapter is non-bondable at rest
        self.sys.props = {"Paired": True, "Bonded": self.sys.bond_holds and self.sys.pairable}
        self.sys.encrypted = self.sys.encrypt_holds


class SysBus:
    """busctl + hcitool con as BlueZ / the kernel would answer them."""

    def __init__(self, *, bond_holds=True, encrypt_holds=True, timeout_write_fails=False, timeout_ignored=False,
                 old_bond=False, remove="ok") -> None:
        self.props = {"Paired": False, "Bonded": False}
        self.bond_holds, self.encrypt_holds, self.encrypted = bond_holds, encrypt_holds, False
        self.pairable, self.pairable_timeout = False, 0
        self.timeout_write_fails, self.timeout_ignored = timeout_write_fails, timeout_ignored
        self.remove = remove                      # ok | fails | lies (rc 0, entry stays) | unknown (no such device)
        if old_bond:
            self.props = {"Paired": True, "Bonded": True}
        self.calls: list[list[str]] = []

    async def run(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        if argv[:3] == ["busctl", "--system", "get-property"]:
            if self.remove == "dbus_down" and argv[-1] in ("Paired", "Bonded"):
                return 1, "Failed to get property: Connection timed out"
            if argv[-1] == "PairableTimeout":
                return 0, f"u {self.pairable_timeout}\n"
            return 0, f"b {'true' if self.props.get(argv[-1]) else 'false'}\n"
        if argv[:3] == ["busctl", "--system", "set-property"]:
            if argv[-3] == "PairableTimeout":
                if self.timeout_write_fails:
                    return 1, "Failed to set property PairableTimeout: Access denied"
                if not self.timeout_ignored:
                    self.pairable_timeout = int(argv[-1])
            elif argv[-3] == "Pairable":
                assert self.pairable_timeout > 0, "Pairable set before its timeout: a crash would leave the adapter bondable"
                self.pairable = argv[-1] == "true"
            return 0, ""
        if argv[:3] == ["busctl", "--system", "call"]:
            if self.remove in ("fails", "dbus_down"):
                return 1, "Call failed: org.bluez.Error.Failed"
            if self.remove == "lies":
                return 0, ""
            self.props = {"Paired": False, "Bonded": False}
            return (1, "Call failed: org.bluez.Error.DoesNotExist") if self.remove == "unknown" else (0, "")
        if argv == ["hcitool", "con"]:
            lm = "CENTRAL AUTH ENCRYPT" if self.encrypted else "CENTRAL"
            return 0, f"Connections:\n\t< LE {OMI_ADDR} handle 64 state 1 lm {lm}\n"
        return 1, "unexpected"


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    async def no_sleep(_s):
        return None
    monkeypatch.setattr(bond.asyncio, "sleep", no_sleep)


def backend_for(sysbus: SysBus, fw: str = ZOE_FW, fail: bool = False) -> tuple[FakeBackend, list]:
    be = FakeBackend([advert(OMI_ADDR, "Omi", -60)], {OMI_ADDR: omi_chars(fw=fw)})
    clients: list[Client] = []

    def attach(link):
        link.client = Client(sysbus, fail)
        clients.append(link.client)
    be.on_connect = attach
    return be, clients


# --- pure ---------------------------------------------------------------------------------------------------------------
def test_only_a_four_part_zoe_version_counts_as_zoe_firmware():
    assert bond.zoe_firmware("3.0.21.1") and bond.zoe_firmware("v3.0.22.7\x00")
    assert not bond.zoe_firmware("3.0.21") and not bond.zoe_firmware("3.0.18") and not bond.zoe_firmware("") \
        and not bond.zoe_firmware("3.0.21.1-x")


@pytest.mark.parametrize("text", [
    "b true\n", "b false", "", "Failed to get property Bonded on interface org.bluez.Device1: Unknown property", "s true",
])
def test_the_parsers_are_the_same_as_omi_sync(text):
    assert bond.parse_busctl_bool(text) == link_security.parse_busctl_bool(text)


@pytest.mark.parametrize("text", [
    f"Connections:\n\t< LE {OMI_ADDR} handle 64 state 1 lm CENTRAL AUTH ENCRYPT\n",
    f"Connections:\n\t< LE {OMI_ADDR.lower()} handle 64 state 1 lm CENTRAL \n", "Connections:\n",
    f"\t< LE {SENSOR_ADDR} handle 70 state 1 lm CENTRAL ENCRYPT\n", f"\t< LE {OMI_ADDR} handle 64 state 1\n",
])
def test_the_link_mode_parsers_agree(text):
    assert bond.parse_link_mode(text, OMI_ADDR) == link_security.parse_link_mode(text, OMI_ADDR)
    assert bond.device_path(OMI_ADDR) == link_security.device_path(OMI_ADDR) == PATH
    with pytest.raises(ValueError):
        bond.device_path("E6:11:22:33:44:55; reboot")


# --- bond_pendant -------------------------------------------------------------------------------------------------------
async def test_a_zoe_pendant_with_an_open_window_is_bonded_and_verified():
    sysbus = SysBus()
    be, clients = backend_for(sysbus)
    res = await bond.bond_pendant(be, OMI_ADDR, "random", run=sysbus.run)
    assert res == {"bonded": True, "encrypted": True, "firmware": ZOE_FW}
    assert clients[0].paired == 1 and be.live_links() == []                     # paired once, link closed afterwards
    assert ["hcitool", "con"] in sysbus.calls
    assert sysbus.pairable is False and sysbus.pairable_timeout == 60


async def test_the_adapter_is_never_left_pairable():
    for kw, fail in (({}, True), ({"bond_holds": False}, False), ({"encrypt_holds": False}, False)):
        sysbus = SysBus(**kw)
        be, _ = backend_for(sysbus, fail=fail)
        with pytest.raises(bond.BondRefused):
            await bond.bond_pendant(be, OMI_ADDR, "random", run=sysbus.run)
        assert sysbus.pairable is False, kw
    sysbus = SysBus()
    be, _ = backend_for(sysbus, fw="3.0.21")
    with pytest.raises(bond.BondRefused):
        await bond.bond_pendant(be, OMI_ADDR, "random", run=sysbus.run)
    assert not [c for c in sysbus.calls if "set-property" in c]          # a stock pendant never makes the adapter pairable


@pytest.mark.parametrize("kw", [{"timeout_write_fails": True}, {"timeout_ignored": True}])
async def test_pairing_is_never_enabled_without_an_installed_timeout(kw):
    """Codex finding 4: a failed (or silently ignored) PairableTimeout write must stop BEFORE Pairable is ever set."""
    sysbus = SysBus(**kw)
    be, clients = backend_for(sysbus)
    with pytest.raises(bond.BondRefused) as e:
        await bond.bond_pendant(be, OMI_ADDR, "random", run=sysbus.run)
    assert e.value.reason == "adapter" and clients[0].paired == 0
    assert not [c for c in sysbus.calls if c[:3] == ["busctl", "--system", "set-property"] and c[-3] == "Pairable"]
    assert sysbus.pairable is False


@pytest.mark.parametrize("remove", ["fails", "lies", "dbus_down"])
async def test_replace_stops_unless_the_old_entry_is_really_gone(remove):
    """Codex round 2: a failed (or silently ineffective) RemoveDevice must stop the operation before connecting/pairing."""
    sysbus = SysBus(old_bond=True, remove=remove)
    be, clients = backend_for(sysbus)
    with pytest.raises(bond.BondRefused) as e:
        await bond.bond_pendant(be, OMI_ADDR, "random", replace=True, run=sysbus.run)
    assert e.value.reason == "replace_failed" and not clients and be.live_links() == []
    assert not [c for c in sysbus.calls if "set-property" in c]


async def test_replace_of_a_device_bluez_never_knew_goes_ahead():
    sysbus = SysBus(remove="unknown")
    be, clients = backend_for(sysbus)
    res = await bond.bond_pendant(be, OMI_ADDR, "random", replace=True, run=sysbus.run)
    assert res["bonded"] is True and clients[0].paired == 1


async def test_a_stock_pendant_is_never_bonded():
    sysbus = SysBus()
    be, clients = backend_for(sysbus, fw="3.0.21")
    with pytest.raises(bond.BondRefused) as e:
        await bond.bond_pendant(be, OMI_ADDR, "random", run=sysbus.run)
    assert e.value.reason == "stock_firmware" and clients[0].paired == 0 and be.live_links() == []


async def test_a_pendant_whose_window_is_shut_refuses_and_says_how_to_open_it():
    sysbus = SysBus()
    be, _ = backend_for(sysbus, fail=True)
    with pytest.raises(bond.BondRefused) as e:
        await bond.bond_pendant(be, OMI_ADDR, "random", run=sysbus.run)
    assert e.value.reason == "pair_failed" and "twice" in e.value.sentence and be.live_links() == []


async def test_a_bond_that_bluez_does_not_hold_is_refused():
    sysbus = SysBus(bond_holds=False)                     # e.g. a session-only pairing: Paired yes, Bonded no
    be, _ = backend_for(sysbus)
    with pytest.raises(bond.BondRefused) as e:
        await bond.bond_pendant(be, OMI_ADDR, "random", run=sysbus.run)
    assert e.value.reason == "not_bonded"


async def test_an_unencrypted_link_after_pairing_is_refused():
    sysbus = SysBus(encrypt_holds=False)
    be, _ = backend_for(sysbus)
    with pytest.raises(bond.BondRefused) as e:
        await bond.bond_pendant(be, OMI_ADDR, "random", run=sysbus.run)
    assert e.value.reason == "not_bonded"


async def test_replace_removes_only_this_pendant_and_only_when_asked():
    sysbus = SysBus()
    be, _ = backend_for(sysbus)
    await bond.bond_pendant(be, OMI_ADDR, "random", run=sysbus.run)
    assert not [c for c in sysbus.calls if "RemoveDevice" in c]
    sysbus2 = SysBus()
    be2, _ = backend_for(sysbus2)
    await bond.bond_pendant(be2, OMI_ADDR, "random", replace=True, run=sysbus2.run)
    removes = [c for c in sysbus2.calls if "RemoveDevice" in c]
    assert removes == [["busctl", "--system", "call", "org.bluez", "/org/bluez/hci0", "org.bluez.Adapter1", "RemoveDevice", "o", PATH]]


# --- the agent op -------------------------------------------------------------------------------------------------------
GO = {"confirm": True, "ack_wipe": True}


def _spool(tmp_path, waiting: int):
    import sqlite3
    d = tmp_path / "omi"
    d.mkdir(exist_ok=True)
    db = sqlite3.connect(d / "spool.sqlite3")
    db.execute("CREATE TABLE IF NOT EXISTS records (id INTEGER)")
    db.execute("CREATE TABLE IF NOT EXISTS confirmed (id INTEGER, confirmed_at REAL)")
    db.execute("DELETE FROM records")
    db.executemany("INSERT INTO records VALUES (?)", [(i,) for i in range(waiting)])
    db.commit()
    db.close()


async def _op(agent, dev, params):
    await agent.execute({"job_id": "bondjob0001" + str(len(agent._outbox)), "op": "omi_bond", "device_id": dev, "params": params})
    return agent._outbox[-1]


async def test_the_op_is_flag_dark_and_needs_a_confirm(tmp_path, monkeypatch):
    sysbus = SysBus()
    monkeypatch.setattr(bond._backend, "_run", sysbus.run)
    be, clients = backend_for(sysbus)
    off = PanelBluetoothAgent(settings(tmp_path), be, DriverRegistry(), None)
    off.scan_cache[device_id(OMI_ADDR)] = advert(OMI_ADDR)
    r = await _op(off, device_id(OMI_ADDR), GO)
    assert r["state"] == "failed" and r["error"]["code"] == "bond_disabled" and not clients
    on = PanelBluetoothAgent(settings(tmp_path, PANEL_BT_BOND_ENABLED=1), be, DriverRegistry(), None)
    on.scan_cache[device_id(OMI_ADDR)] = advert(OMI_ADDR)
    for params in ({}, {"confirm": True}, {"ack_wipe": True}):            # both the confirm AND the wipe acknowledgement
        r = await _op(on, device_id(OMI_ADDR), params)
        assert r["state"] == "failed" and r["error"]["code"] == "bad_request" and not clients
        assert "erases" in r["error"]["message"]
    r = await _op(on, device_id(OMI_ADDR), GO)
    assert r["state"] == "succeeded" and r["result"]["bonded"] is True and len(clients) == 1
    assert not on.lock.held                                    # the radio goes back to omi-sync afterwards


async def test_only_an_omi_pendant_can_be_bonded(tmp_path, monkeypatch):
    sysbus = SysBus()
    monkeypatch.setattr(bond._backend, "_run", sysbus.run)
    be, clients = backend_for(sysbus)
    agent = PanelBluetoothAgent(settings(tmp_path, PANEL_BT_BOND_ENABLED=1), be, DriverRegistry(), None)
    agent.scan_cache[device_id(SENSOR_ADDR)] = advert(SENSOR_ADDR, "HR Strap", services=())
    r = await _op(agent, device_id(SENSOR_ADDR), GO)
    assert r["state"] == "failed" and r["error"]["code"] == "unsupported" and not clients


async def test_pairing_waits_for_unsynced_recordings_unless_forced(tmp_path, monkeypatch):
    """The pendant crypto-wipes when it pairs: refuse while omi-sync still holds unsynced records, unless forced."""
    sysbus = SysBus()
    monkeypatch.setattr(bond._backend, "_run", sysbus.run)
    be, clients = backend_for(sysbus)
    _spool(tmp_path, 3)
    agent = PanelBluetoothAgent(settings(tmp_path, PANEL_BT_BOND_ENABLED=1), be, DriverRegistry(), None)
    agent.scan_cache[device_id(OMI_ADDR)] = advert(OMI_ADDR)
    r = await _op(agent, device_id(OMI_ADDR), GO)
    assert r["state"] == "failed" and r["error"]["detail"]["reason"] == "unsynced" and not clients
    r = await _op(agent, device_id(OMI_ADDR), {**GO, "force": True})
    assert r["state"] == "succeeded" and len(clients) == 1
    _spool(tmp_path, 0)
    r = await _op(agent, device_id(OMI_ADDR), GO)                      # synced: no force needed
    assert r["state"] == "succeeded"
