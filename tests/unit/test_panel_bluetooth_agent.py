"""panel-bluetooth: the agent's operations against a fake radio, the driver registry, the wire shapes, and the structural rules.

No hardware, no BLE, no network. Source mutations worth re-running by Edit (each must turn a named test red):
  drop the ``DEVICE_ID_RX`` check in ``parse_command``          -> test_command_validation_*
  put the address into the scan rows / the connect result        -> test_no_address_ever_leaves_the_agent
  skip ``self.lock.release()`` in ``execute``'s finally          -> test_a_failed_connect_releases_the_radio
  let ``_touch`` ignore ``max_hold_s``                           -> test_a_lease_cannot_be_extended_past_the_hard_cap
  drop the ``busy_job`` check in ``execute``                     -> test_a_second_command_while_busy_is_refused
  add a ``pair(`` call anywhere in panel_bt but bond.py          -> test_the_agent_never_pairs_and_never_listens
"""
from __future__ import annotations

import asyncio
import itertools
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

from tests.unit.panel_bt_fakes import (OMI_ADDR, PHONE_ADDR, SENSOR_ADDR, FakeBackend, advert, omi_chars, settings)  # noqa: E402

from panel_bt import config  # noqa: E402
from panel_bt.agent import PanelBluetoothAgent  # noqa: E402
from panel_bt.backend import lecc_argv, ledc_argv, parse_lecc_handle  # noqa: E402
from panel_bt.drivers import DriverRegistry  # noqa: E402
from panel_bt.drivers.base import BATTERY_LEVEL, DIS_FIRMWARE, DIS_MODEL  # noqa: E402
from panel_bt.known import KnownStore  # noqa: E402
from panel_bt.model import (AgentError, E_BAD_REQUEST, E_BUSY, E_CONNECT, E_NOT_CONNECTED, E_NOT_FOUND, device_id, parse_command,  # noqa: E402
                            version_tuple)
from panel_bt.radio_lock import RadioLock  # noqa: E402

PKG = Path(__file__).resolve().parents[2] / "services" / "panel-bluetooth" / "panel_bt"
_job = itertools.count(1)


def sensor_chars():
    return {DIS_MODEL: b"HR-1", DIS_FIRMWARE: b"1.2.3", BATTERY_LEVEL: bytes([77])}


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make(tmp_path, adverts=None, chars=None, **env):
    clock = Clock()
    backend = FakeBackend(adverts if adverts is not None else [
        advert(OMI_ADDR, "Omi", -62), advert(SENSOR_ADDR, "HR Strap", -50, services=()), advert(PHONE_ADDR, "", -40, services=())],
        chars if chars is not None else {OMI_ADDR: omi_chars(), SENSOR_ADDR: sensor_chars()})
    agent = PanelBluetoothAgent(settings(tmp_path, **env), backend, DriverRegistry(), None, clock=clock)
    return agent, backend, clock


async def run(agent, op, dev=None, params=None):
    job = f"job{next(_job):06d}"
    await agent.execute({"job_id": job, "op": op, "device_id": dev, "params": params or {}})
    return [p for p in agent._outbox if p["job_id"] == job][-1]


OMI_ID, SENSOR_ID, PHONE_ID = device_id(OMI_ADDR), device_id(SENSOR_ADDR), device_id(PHONE_ADDR)


# --- registry ---------------------------------------------------------------------------------------------------------
def test_registry_matches_by_service_uuid_name_and_falls_back_to_generic():
    reg = DriverRegistry()
    assert reg.identify(advert(name="whatever")).name == "omi"                         # the audio service UUID is enough
    assert reg.identify(advert(name="Omi", services=())).name == "omi"                 # or the advertised name
    assert reg.identify(advert(name="OMI-1234", services=())).name == "omi"
    assert reg.identify(advert(name="Omicron Speaker", services=())).name == "generic"  # not a prefix match
    assert reg.identify(advert(name="HR Strap", services=())).name == "generic"
    assert reg.identify(advert(name="", services=())).name == "generic"                 # everything stays connectable
    assert not reg.identify(advert(name="HR Strap", services=())).known and reg.identify(advert()).known


def test_registry_always_keeps_a_generic_fallback():
    assert "generic" in DriverRegistry([]).names()


# --- wire shapes -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("raw", [
    None, [], {"op": "scan"}, {"op": "scan", "job_id": "x"}, {"op": "reboot", "job_id": "job000001"},
    {"op": "connect", "job_id": "job000001"}, {"op": "connect", "job_id": "job000001", "device_id": OMI_ADDR},
    {"op": "connect", "job_id": "job000001", "device_id": "ZZZZZZZZZZZZ"}, {"op": "scan", "job_id": "job 000001"},
    {"op": "scan", "job_id": "job000001", "params": "x" * 10}, {"op": "scan", "job_id": "job000001", "params": {"a": "x" * 3000}},
])
def test_command_validation_refuses_malformed_commands(raw):
    with pytest.raises(AgentError) as e:
        parse_command(raw)
    assert e.value.code == E_BAD_REQUEST


def test_command_validation_accepts_the_good_shapes():
    assert parse_command({"op": "scan", "job_id": "job000001"}).device_id is None
    assert parse_command({"op": "connect", "job_id": "job000001", "device_id": OMI_ID}).device_id == OMI_ID
    assert parse_command({"op": "scan", "job_id": "job000001", "device_id": OMI_ID}).device_id is None    # ignored where unused


def test_device_id_is_a_short_hash_not_the_address():
    assert re.fullmatch(r"[0-9a-f]{16}", OMI_ID)
    assert OMI_ADDR.lower().replace(":", "") not in OMI_ID
    assert device_id(OMI_ADDR) == device_id(OMI_ADDR.lower())


def test_version_parsing():
    assert version_tuple("3.0.18") == (3, 0, 18) and version_tuple("v3.0.21\x00") == (3, 0, 21)
    assert version_tuple("") is None and version_tuple("dev-build") is None and version_tuple(None) is None


# --- config: floors cannot be configured away -------------------------------------------------------------------------------
def test_update_floors_can_be_raised_never_lowered(tmp_path):
    s = settings(tmp_path, PANEL_BT_OTA_MIN_BATTERY=10, PANEL_BT_OTA_MIN_RSSI=-95)
    assert s.ota_min_battery == 50 and s.ota_min_rssi == -75
    s = settings(tmp_path, PANEL_BT_OTA_MIN_BATTERY=80, PANEL_BT_OTA_MIN_RSSI=-60)
    assert s.ota_min_battery == 80 and s.ota_min_rssi == -60
    assert settings(tmp_path).ota_enabled is False and config.load({}).enabled is False        # both flags default OFF


def test_adapter_name_is_validated_before_it_reaches_a_command_line(tmp_path):
    assert settings(tmp_path, PANEL_BT_HCI="hci1").hci == "hci1"
    assert settings(tmp_path, PANEL_BT_HCI="hci0; reboot").hci == "" and settings(tmp_path, PANEL_BT_HCI="../x").hci == ""
    assert lecc_argv(("sudo", "-n"), OMI_ADDR, "random", "hci1") == ["sudo", "-n", "hcitool", "-i", "hci1", "lecc", "--random", OMI_ADDR]
    assert lecc_argv((), SENSOR_ADDR, "public") == ["hcitool", "lecc", SENSOR_ADDR]
    assert ledc_argv(("sudo", "-n"), 64, "hci1") == ["sudo", "-n", "hcitool", "-i", "hci1", "ledc", "64"]
    assert parse_lecc_handle("Connection handle 64\n") == 64 and parse_lecc_handle("Could not create connection") is None


# --- scan ---------------------------------------------------------------------------------------------------------------
async def test_scan_lists_named_devices_known_drivers_first_and_hides_unnamed(tmp_path):
    agent, backend, _ = make(tmp_path)
    r = await run(agent, "scan")
    assert r["state"] == "succeeded"
    rows = r["result"]["devices"]
    assert [x["name"] for x in rows] == ["Omi", "HR Strap"]            # the Omi (specific driver) beats a stronger generic device
    assert rows[0]["driver"] == "omi" and rows[0]["specific_driver"] and rows[1]["driver"] == "generic"
    assert r["result"]["hidden_unnamed"] == 1
    assert ("scan", 3.0) in backend.calls
    assert not agent.lock.held, "a scan must not keep the radio"


def test_no_address_ever_leaves_the_agent(tmp_path):
    async def go():
        agent, _, _ = make(tmp_path)
        out = [await run(agent, "scan")]
        out.append(await run(agent, "connect", OMI_ID))
        out.append(await run(agent, "info", OMI_ID))
        out.append(await run(agent, "update_check", OMI_ID))
        out.append(await run(agent, "status"))
        blob = json.dumps([out, agent.snapshot()]).lower()
        for addr in (OMI_ADDR, SENSOR_ADDR, PHONE_ADDR):
            assert addr.lower() not in blob and addr.lower().replace(":", "") not in blob
        assert OMI_ADDR in KnownStore(agent.s.known_path).get(OMI_ID)["address"]       # the address lives ONLY in the Pi-local file
    asyncio.run(go())


# --- connect / disconnect / forget / info ---------------------------------------------------------------------------------
async def test_connect_a_generic_device_reads_standard_info_and_remembers_it(tmp_path):
    agent, backend, _ = make(tmp_path)
    await run(agent, "scan")
    r = await run(agent, "connect", SENSOR_ID)
    assert r["state"] == "succeeded" and r["result"]["connected"] and r["result"]["driver"] == "generic"
    assert r["result"]["info"]["model"] == "HR-1" and r["result"]["info"]["battery"] == 77 and r["result"]["supports_update"] is False
    assert agent.lock.held, "an open link keeps the radio lease"
    assert KnownStore(agent.s.known_path).get(SENSOR_ID)["driver"] == "generic"
    mode = oct(agent.s.known_path.stat().st_mode & 0o777)
    assert mode == "0o600"


async def test_connect_the_omi_adds_charging_and_sync_status(tmp_path):
    agent, _, _ = make(tmp_path)
    await run(agent, "scan")
    r = await run(agent, "connect", OMI_ID)
    info = r["result"]["info"]
    assert info["firmware"] == "3.0.18" and info["battery"] == 95 and info["charging"] is True and info["model"] == "Omi CV 1"
    assert info["sync"] == {"configured": False}                      # no omi-sync spool on this (test) panel
    assert r["result"]["supports_update"] is True


async def test_omi_sync_status_is_read_only_from_its_spool(tmp_path):
    import sqlite3
    agent, _, _ = make(tmp_path)
    d = tmp_path / "omi"
    d.mkdir()
    db = sqlite3.connect(d / "spool.sqlite3")
    db.executescript("CREATE TABLE records (device TEXT, seq INTEGER); CREATE TABLE confirmed (confirmed_at REAL);"
                     "INSERT INTO records VALUES ('d',1),('d',2); INSERT INTO confirmed VALUES (1700000000.0);")
    db.commit()
    db.close()
    before = (d / "spool.sqlite3").read_bytes()
    await run(agent, "scan")
    r = await run(agent, "connect", OMI_ID)
    assert r["result"]["info"]["sync"] == {"configured": True, "waiting_to_upload": 2, "confirmed_ranges": 1, "last_confirmed_at": 1700000000.0}
    assert (d / "spool.sqlite3").read_bytes() == before


async def test_connecting_a_second_device_releases_the_first(tmp_path):
    agent, backend, _ = make(tmp_path)
    await run(agent, "scan")
    await run(agent, "connect", OMI_ID)
    await run(agent, "connect", SENSOR_ID)
    assert len(backend.live_links()) == 1 and backend.live_links()[0].address == SENSOR_ADDR


async def test_disconnect_is_idempotent_and_frees_the_radio(tmp_path):
    agent, backend, _ = make(tmp_path)
    await run(agent, "scan")
    await run(agent, "connect", SENSOR_ID)
    assert (await run(agent, "disconnect", SENSOR_ID))["result"] == {"connected": False}
    assert not backend.live_links() and not agent.lock.held
    assert (await run(agent, "disconnect", SENSOR_ID))["state"] == "succeeded"
    assert KnownStore(agent.s.known_path).get(SENSOR_ID) is not None, "disconnect is not forget"


async def test_forget_drops_the_link_and_the_memory_of_the_device(tmp_path):
    agent, backend, _ = make(tmp_path)
    await run(agent, "scan")
    await run(agent, "connect", SENSOR_ID)
    r = await run(agent, "forget", SENSOR_ID)
    assert r["result"]["forgotten"] and r["result"]["was_remembered"]
    assert not backend.live_links() and KnownStore(agent.s.known_path).get(SENSOR_ID) is None
    again = await run(agent, "connect", SENSOR_ID)                  # the scan cache was cleared too: it has to be found again
    assert again["state"] == "failed" and again["error"]["code"] == E_NOT_FOUND


async def test_a_remembered_device_connects_without_a_fresh_scan(tmp_path):
    agent, _, _ = make(tmp_path)
    await run(agent, "scan")
    await run(agent, "connect", SENSOR_ID)
    await run(agent, "disconnect", SENSOR_ID)
    agent2 = PanelBluetoothAgent(agent.s, agent.backend, DriverRegistry(), None, clock=Clock())          # a restarted agent
    assert (await run(agent2, "connect", SENSOR_ID))["state"] == "succeeded"


async def test_a_failed_connect_releases_the_radio(tmp_path):
    agent, backend, _ = make(tmp_path)
    await run(agent, "scan")
    backend.connect_fail = 1
    r = await run(agent, "connect", OMI_ID)
    assert r["state"] == "failed" and r["error"]["code"] == E_CONNECT
    assert "switched on" in r["error"]["message"]
    assert not agent.lock.held and not agent.busy_job


async def test_an_unreadable_field_is_absent_but_a_driver_that_blows_up_leaves_no_half_open_link(tmp_path):
    agent, backend, _ = make(tmp_path, chars={SENSOR_ADDR: {DIS_MODEL: RuntimeError("gatt read failed"), BATTERY_LEVEL: bytes([5])}})
    await run(agent, "scan")
    r = await run(agent, "connect", SENSOR_ID)
    assert r["state"] == "succeeded" and r["result"]["info"]["model"] is None and r["result"]["info"]["battery"] == 5
    await run(agent, "disconnect", SENSOR_ID)

    async def boom(link, ctx):
        raise RuntimeError("driver bug")

    agent.registry.by_name("generic").info = boom
    r2 = await run(agent, "connect", SENSOR_ID)
    assert r2["state"] == "failed" and r2["error"]["code"] == E_CONNECT
    assert not backend.live_links() and not agent.lock.held


async def test_info_and_update_check_need_a_live_link(tmp_path):
    agent, _, _ = make(tmp_path)
    await run(agent, "scan")
    for op in ("info", "update_check"):
        r = await run(agent, op, OMI_ID)
        assert r["state"] == "failed" and r["error"]["code"] == E_NOT_CONNECTED


async def test_update_check_on_a_generic_device_says_unsupported(tmp_path):
    agent, _, _ = make(tmp_path)
    await run(agent, "scan")
    await run(agent, "connect", SENSOR_ID)
    r = await run(agent, "update_check", SENSOR_ID)
    assert r["result"]["supported"] is False and r["result"]["available"] is False


# --- the radio is shared with omi-sync --------------------------------------------------------------------------------------
async def test_a_busy_radio_says_the_pendant_is_syncing(tmp_path):
    agent, backend, _ = make(tmp_path)
    other = RadioLock(agent.s.radio_lock, holder="omi-sync")
    assert other.try_acquire()
    try:
        agent.lock.try_acquire = lambda: False                                    # (do not wait out LOCK_WAIT_S in a test)
        agent.lock.acquire = lambda timeout_s, poll_s=0.25: asyncio.sleep(0, False)
        r = await run(agent, "scan")
        assert r["state"] == "failed" and r["error"]["code"] == E_BUSY and "syncing recordings" in r["error"]["message"]
        assert not any(c[0] == "scan" for c in backend.calls), "must not scan while another process holds the radio"
    finally:
        other.release()


def test_the_radio_lock_excludes_across_instances_and_survives_release(tmp_path):
    a, b = RadioLock(tmp_path / "l", "panel-bluetooth"), RadioLock(tmp_path / "l", "omi-sync")
    assert a.try_acquire() and not b.try_acquire() and b.current_holder() == "panel-bluetooth"
    a.release()
    assert b.try_acquire() and a.current_holder() == "omi-sync"
    b.release()
    assert a.try_acquire()


# --- leases: a forgotten 'Connected' cannot starve the sync -----------------------------------------------------------------------
async def test_an_idle_link_is_released_when_its_lease_ends(tmp_path):
    agent, backend, clock = make(tmp_path, PANEL_BT_LEASE_S=60)
    await run(agent, "scan")
    await run(agent, "connect", SENSOR_ID)
    clock.t += 59
    assert await agent.sweep_once() is False and backend.live_links()
    clock.t += 2
    assert await agent.sweep_once() is True
    assert not backend.live_links() and not agent.lock.held


async def test_status_touches_extend_the_lease(tmp_path):
    agent, backend, clock = make(tmp_path, PANEL_BT_LEASE_S=60)
    await run(agent, "scan")
    await run(agent, "connect", SENSOR_ID)
    for _ in range(5):
        clock.t += 50
        await run(agent, "status")
        assert await agent.sweep_once() is False
    assert backend.live_links()


async def test_a_lease_cannot_be_extended_past_the_hard_cap(tmp_path):
    agent, backend, clock = make(tmp_path, PANEL_BT_LEASE_S=60, PANEL_BT_MAX_HOLD_S=300)
    await run(agent, "scan")
    await run(agent, "connect", SENSOR_ID)
    for _ in range(9):
        clock.t += 50
        await run(agent, "status")
    clock.t += 50                                                                  # 500 s after connecting, still being touched
    await run(agent, "status")
    assert await agent.sweep_once() is True and not backend.live_links()


async def test_a_dropped_link_is_noticed_and_the_radio_freed(tmp_path):
    agent, backend, _ = make(tmp_path)
    await run(agent, "scan")
    await run(agent, "connect", SENSOR_ID)
    backend.links[-1].connected = False
    st = await run(agent, "status")
    assert [d["connected"] for d in st["result"]["devices"]] == [False] and not agent.lock.held


# --- one task at a time --------------------------------------------------------------------------------------------------------
async def test_a_second_command_while_busy_is_refused(tmp_path):
    agent, backend, _ = make(tmp_path)
    gate = asyncio.Event()
    orig = backend.scan

    async def slow_scan(seconds):
        await gate.wait()
        return await orig(seconds)

    backend.scan = slow_scan
    first = asyncio.create_task(run(agent, "scan"))
    await asyncio.sleep(0.05)
    r = await run(agent, "scan")
    assert r["state"] == "failed" and r["error"]["code"] == E_BUSY
    st = await run(agent, "status")                                               # status is always answered, even mid-scan
    assert st["state"] == "succeeded" and st["result"]["busy"]["op"] == "scan"
    gate.set()
    assert (await first)["state"] == "succeeded"


async def test_a_redelivered_command_runs_once(tmp_path):
    agent, backend, _ = make(tmp_path)
    cmd = {"job_id": "dupjob0001", "op": "scan", "params": {}}
    await agent.execute(cmd)
    await agent.execute(cmd)
    assert [c for c in backend.calls if c[0] == "scan"] == [("scan", 3.0)]


async def test_a_malformed_command_is_reported_not_run(tmp_path):
    agent, backend, _ = make(tmp_path)
    await agent.execute({"job_id": "badjob0001", "op": "connect", "device_id": OMI_ADDR})
    assert agent._outbox[-1]["state"] == "failed" and agent._outbox[-1]["error"]["code"] == E_BAD_REQUEST
    assert not backend.calls


# --- structural rules (static guards over the source) --------------------------------------------------------------------------------
def _sources():
    return {p.name: p.read_text(encoding="utf-8") for p in PKG.rglob("*.py")}


def test_the_agent_never_pairs_and_never_listens():
    """No bond anywhere but bond.py (the owner-run ``omi_bond``), and nowhere an agent, an unpair or a listening socket."""
    forbidden = [r"\.unpair\(", r"bluetoothctl", r"\btrust\b.*device", r"register_agent", r"RegisterAgent",
                 r"TCPSite", r"web\.Application", r"start_server", r"http\.server", r"\.listen\(", r"\.bind\(", r"create_server"]
    bond_only = [r"\.pair\(", r"RemoveDevice", r"Pairable"]
    for name, text in _sources().items():
        code = "\n".join(line for line in text.splitlines() if not line.strip().startswith(("#", '"""')))
        for pat in forbidden + ([] if name == "bond.py" else bond_only):
            assert not re.search(pat, code), f"{name} matches forbidden /{pat}/"


def test_the_agent_has_no_audio_and_no_omi_cloud():
    for name, text in _sources().items():
        assert not re.search(r"(?i)pyaudio|sounddevice|pulsectl|alsaaudio|zoe_voice|\bimport wave\b", text), name
        assert "omi.me" not in text and "api.omi" not in text, f"{name} references Omi's cloud"


def test_the_agent_does_not_import_the_other_services():
    for name, text in _sources().items():
        assert not re.search(r"^\s*(from|import)\s+(omi_sync|zoe_data|routers|labs)\b", text, re.M), name


def test_only_the_smp_session_imports_smpclient_and_nothing_writes_gatt_directly():
    for name, text in _sources().items():
        assert "write_gatt_char" not in text, f"{name}: the agent never writes a GATT characteristic itself (SMP goes through smpclient)"
        if name != "smp_session.py":
            assert not re.search(r"^\s*(from|import)\s+smpclient", text, re.M), f"{name} imports smpclient"
