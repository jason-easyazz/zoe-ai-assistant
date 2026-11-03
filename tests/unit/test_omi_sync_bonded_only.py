"""omi-sync bonded-only mode (phase-2 Zoe firmware): no bond -> no connection; an unencrypted link -> torn down unread.

No hardware: bleak is a fake and ``_run`` answers ``busctl`` / ``hcitool`` like BlueZ and the kernel. Source mutations that must
turn this file red (re-run by Edit):
  drop the pre-connect ``bond_state`` check in ``ble_link.connect``          -> test_no_bond_means_no_connection_attempt
  drop the post-subscribe ``link_encrypted`` check                          -> test_an_unencrypted_link_is_torn_down_unread
  drop the post-subscribe bond re-read                                      -> test_a_bond_that_vanishes_mid_connect_is_refused
  make ``link_security.bonded`` accept an unknown (None)                    -> test_an_unreadable_bond_is_not_a_bond
  run the checks when ``require_bond`` is False                             -> test_stock_mode_reads_nothing_extra
"""
from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "omi-sync"))

from omi_sync import ble_link, config, link_security as sec  # noqa: E402
from omi_sync.radio_lock import RadioLock  # noqa: E402

ADDR = "E4:A3:57:6C:8D:0D"
OTHER = "AA:BB:CC:DD:EE:FF"


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, *_a, **_kw) -> None:
        self.is_connected = False
        FakeClient.instances.append(self)

    async def connect(self):
        self.is_connected = True

    async def start_notify(self, uuid, cb):
        pass

    async def stop_notify(self, uuid):
        pass

    async def disconnect(self):
        self.is_connected = False


class Sys:
    """busctl (BlueZ Device1 properties), hcitool lecc/ledc/con (the kernel's link and its link mode)."""

    def __init__(self, *, paired=True, bonded=True, encrypted=True, bond_after_connect=None) -> None:
        self.props = {"Paired": paired, "Bonded": bonded}
        self.encrypted = encrypted
        self.bond_after_connect = bond_after_connect
        self.link = False
        self.calls: list[list[str]] = []

    async def run(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        if argv[:3] == ["busctl", "--system", "get-property"]:
            v = self.props.get(argv[-1])
            return (0, f"b {'true' if v else 'false'}\n") if v is not None else (1, "Unknown property")
        cmd = argv[argv.index("hcitool") + 1]
        if cmd == "lecc":
            self.link = True
            if self.bond_after_connect is not None:
                self.props["Bonded"] = self.bond_after_connect
            return 0, "Connection handle 64\n"
        if cmd == "ledc":
            self.link = False
            return 0, ""
        if cmd == "con":
            lm = "CENTRAL AUTH ENCRYPT" if self.encrypted else "CENTRAL"
            return 0, "Connections:\n" + (f"\t< LE {ADDR} handle 64 state 1 lm {lm}\n" if self.link else "")
        return 0, ""

    def did(self, what: str) -> bool:
        return any(what in " ".join(c) for c in self.calls)


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_BLE_RADIO_LOCK", str(tmp_path / "radio.lock"))
    monkeypatch.setitem(sys.modules, "bleak", types.SimpleNamespace(BleakClient=FakeClient, BleakScanner=None))
    FakeClient.instances = []

    async def no_sleep(_s):
        return None
    monkeypatch.setattr(ble_link.asyncio, "sleep", no_sleep)


def use(monkeypatch, s: Sys) -> Sys:
    monkeypatch.setattr(ble_link, "_run", s.run)
    return s


async def _session(**kw):
    async with ble_link.connect(ADDR, attempts=1, require_bond=True, **kw) as link:
        return link


# --- config -------------------------------------------------------------------------------------------------------------
def test_require_bond_is_off_unless_named():
    assert not config.load({}).requires_bond(ADDR)
    assert not config.load({"OMI_SYNC_REQUIRE_BOND": "0"}).requires_bond(ADDR)
    for v in ("1", "all", "true"):
        assert config.load({"OMI_SYNC_REQUIRE_BOND": v}).requires_bond(ADDR)
    s = config.load({"OMI_SYNC_REQUIRE_BOND": ADDR.lower() + ", 11:22:33:44:55:66"})
    assert s.requires_bond(ADDR) and not s.requires_bond(OTHER)
    assert config.load({}).bluez_adapter == "hci0" and config.load({"OMI_SYNC_BLUEZ_ADAPTER": "hci1"}).bluez_adapter == "hci1"


# --- pure ---------------------------------------------------------------------------------------------------------------
def test_link_mode_and_bond_parsing():
    out = f"Connections:\n\t< LE {OTHER} handle 70 state 1 lm CENTRAL ENCRYPT\n\t< LE {ADDR.lower()} handle 64 state 1 lm CENTRAL\n"
    assert sec.parse_link_mode(out, ADDR) == frozenset({"CENTRAL"})           # another device's ENCRYPT is not ours
    assert sec.parse_link_mode(out, "11:22:33:44:55:66") is None
    assert "ENCRYPT" in sec.parse_link_mode(f"\t< LE {ADDR} handle 64 state 1 lm CENTRAL AUTH ENCRYPT", ADDR)
    assert sec.parse_busctl_bool("b true\n") is True and sec.parse_busctl_bool("b false") is False
    assert sec.parse_busctl_bool("Failed to get property") is None and sec.parse_busctl_bool("") is None
    assert sec.device_path(ADDR) == "/org/bluez/hci0/dev_E4_A3_57_6C_8D_0D"
    with pytest.raises(ValueError):
        sec.device_path(ADDR, "hci0/../x")


def test_an_unreadable_bond_is_not_a_bond():
    assert sec.bonded(True, True)
    assert not sec.bonded(True, False) and not sec.bonded(False, True)
    assert not sec.bonded(True, None) and not sec.bonded(None, None)          # old BlueZ / failed read: fail closed


# --- connect ------------------------------------------------------------------------------------------------------------
async def test_a_bonded_encrypted_pendant_is_served(monkeypatch):
    s = use(monkeypatch, Sys())
    async with ble_link.connect(ADDR, attempts=1, require_bond=True) as link:
        assert isinstance(link, ble_link.BleLink)
    assert s.did("get-property") and s.did("hcitool con") and not s.link


async def test_no_bond_means_no_connection_attempt(monkeypatch):
    s = use(monkeypatch, Sys(paired=False, bonded=False))
    with pytest.raises(ble_link.NotBonded):
        await _session()
    assert not s.did("lecc") and not FakeClient.instances                     # the radio was never touched
    assert RadioLock().try_acquire()                                          # and nothing holds it


async def test_a_session_only_pairing_is_not_a_bond(monkeypatch):
    s = use(monkeypatch, Sys(paired=True, bonded=False))
    with pytest.raises(ble_link.NotBonded):
        await _session()
    assert not s.did("lecc")


async def test_an_unencrypted_link_is_torn_down_unread(monkeypatch):
    """A look-alike that copies the address but not the key: the link comes up, but not encrypted."""
    s = use(monkeypatch, Sys(encrypted=False))
    body_ran = False
    with pytest.raises(ble_link.InsecureLink):
        async with ble_link.connect(ADDR, attempts=1, require_bond=True):
            body_ran = True
    assert not body_ran and not s.link and s.did("ledc")
    assert not any(c.is_connected for c in FakeClient.instances)
    assert RadioLock().try_acquire()


async def test_a_bond_that_vanishes_mid_connect_is_refused(monkeypatch):
    s = use(monkeypatch, Sys(bond_after_connect=False))
    with pytest.raises(ble_link.InsecureLink):
        await _session()
    assert not s.link


async def test_stock_mode_reads_nothing_extra(monkeypatch):
    s = use(monkeypatch, Sys(paired=False, bonded=False, encrypted=False))
    async with ble_link.connect(ADDR, attempts=1):
        pass
    assert not s.did("busctl")                    # flag off: byte-for-byte the stock path (its own `hcitool con` stale-link check only)


async def test_the_runner_says_why_a_bonded_only_pendant_was_refused(monkeypatch, tmp_path, caplog):
    from omi_sync.runner import Runner
    st = config.load({"OMI_PENDANT_ADDRESSES": ADDR, "OMI_SYNC_DIR": str(tmp_path), "OMI_SYNC_REQUIRE_BOND": "1"})
    seen = {}

    def connect(address, **kw):
        seen.update(kw)
        raise ble_link.NotBonded("refused: no bond with this pendant")
    runner = Runner(st, None, None, connect=connect, drain_factory=lambda *_: pytest.fail("never drained"))
    with caplog.at_level(logging.WARNING, logger="omi_sync"):
        wait = await runner.visit(runner.rotation.targets[0])
    assert seen["require_bond"] is True and seen["adapter"] == "hci0"
    assert wait > 0 and "refused" in caplog.text


def test_the_security_module_only_reads():
    src = (Path(__file__).resolve().parents[2] / "services" / "omi-sync" / "omi_sync" / "link_security.py").read_text()
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    for bad in (".pair(", "pair=", "RegisterAgent", "bluetoothctl", "RemoveDevice", '"call"', "set-property", "Pairable", "Trusted"):
        assert bad not in code, bad
