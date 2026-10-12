"""omi-sync: the read-only device check, the flag-dark entry point, and the config.

The check must be safe to run with the owner present on the real pendant: GATT reads + the INFO query only. The
fake link makes every mutating call (READ, ADVANCE, STOP, time write) explode, so a check that ever sent one fails.
Source mutation that must turn this file red (re-run by Edit): make ``device_check`` call ``link.set_time`` or ``link.advance``.
"""
from __future__ import annotations

import asyncio
import json
import struct
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "omi-sync"))

from omi_sync import cli, config, legacy_protocol as lp, ring_protocol as rp  # noqa: E402
from omi_sync.check import device_check  # noqa: E402


class ReadOnlyLink:
    def __init__(self, fw="3.0.21", codec=21, dev_time=1_800_000_100) -> None:
        self.fw, self.codec_id, self.dev_time = fw, codec, dev_time
        self.calls: list[str] = []

    async def firmware(self):
        self.calls.append("firmware")
        return self.fw

    async def model(self):
        self.calls.append("model")
        return "Omi CV 1"

    async def codec(self):
        self.calls.append("codec")
        return self.codec_id

    async def battery(self):
        self.calls.append("battery")
        return 81

    async def device_time(self):
        self.calls.append("device_time")
        return self.dev_time

    async def status(self):
        self.calls.append("status")
        return rp.parse_status(struct.pack("<IIII", 4440, 10, 9999, 1))

    async def info(self):
        self.calls.append("info")
        return bytes([rp.NOTIFY_INFO]) + struct.pack(">QQIQH", 5, 15, 90000, 0, 444)

    async def legacy_status(self):
        self.calls.append("legacy_status")
        return lp.parse_status(struct.pack("<IIII", 880, 2, 0, 0))

    async def legacy_list(self):
        self.calls.append("legacy_list")
        return bytes([2]) + struct.pack(">II", 1_800_000_000, 440 * 40) + struct.pack(">II", 1_800_000_900, 440 * 10)

    # every mutating operation explodes
    async def read(self, *a):
        raise AssertionError("check sent READ")
        yield b""

    async def legacy_read(self, *a):
        raise AssertionError("check sent READ_FILE")
        yield b""

    async def advance(self, *a):
        raise AssertionError("check sent ADVANCE")

    async def stop(self):
        raise AssertionError("check sent STOP")

    async def set_time(self, *a):
        raise AssertionError("check wrote the device clock")


def test_check_reports_the_facts_and_touches_nothing():
    link = ReadOnlyLink()
    rep = asyncio.run(device_check(link, spool_stats={"spooled_records": 3}, zoe_reachable=True, clock=lambda: 1_800_000_000.0))
    assert rep["firmware"] == "3.0.21" and rep["ring_protocol_supported"] is True and rep["codec_supported"] is True
    assert rep["frame_ms"] == 20.0 and rep["battery_pct"] == 81
    assert rep["device_clock"] == {"readable": True, "offset_s": 100.0, "trusted": True}
    assert rep["ring_status"] == {"unread_records": 10, "used_bytes": 4440, "free_bytes": 9999, "rtc_valid": True}
    assert rep["ring_info"]["unread"] == 10 and rep["ring_info"]["app_ack_reclaim"] is False
    assert rep["spool"] == {"spooled_records": 3} and rep["zoe_reachable"] is True
    assert set(link.calls) <= {"firmware", "model", "codec", "battery", "device_time", "status", "info"}


def test_check_on_an_unverified_old_firmware_sends_no_storage_command_at_all():
    """0x12 is ADVANCE on one protocol and DELETE_FILE on the other: an unknown version gets GATT reads only."""
    link = ReadOnlyLink(fw="3.0.19")
    rep = asyncio.run(device_check(link))
    assert rep["ring_protocol_supported"] is False and rep["legacy_file_protocol_verified"] is False
    assert rep["drain_path"].startswith("refused") and "ring_info" not in rep and "files" not in rep
    assert not {"info", "status", "legacy_status", "legacy_list"} & set(link.calls)


def test_check_on_the_owners_firmware_3_0_18_lists_the_files_read_only():
    link = ReadOnlyLink(fw="3.0.18", codec=0x0f)
    rep = asyncio.run(device_check(link, clock=lambda: 1_800_000_000.0))
    assert rep["firmware"] == "3.0.18" and rep["ring_protocol_supported"] is False and rep["legacy_file_protocol_verified"] is True
    assert rep["storage_status"] == {"used_bytes": 880, "file_count": 2}
    assert [(f["timestamp"], f["blocks"]) for f in rep["files"]] == [(1_800_000_000, 40), (1_800_000_900, 10)]
    assert rep["codec_id"] == 15 and rep["codec_supported"] is False and rep["codec_assumed_as"] is None
    assert "BLOCKED" in rep["drain_path"] and "OMI_SYNC_LEGACY_FILES" in rep["drain_path"]
    assert set(link.calls) <= {"firmware", "model", "codec", "battery", "device_time", "legacy_status", "legacy_list"}
    assert "info" not in link.calls                                     # the ring INFO byte is LIST_FILES there: never both


def test_check_reports_the_path_once_the_operator_has_opted_in_and_assumed_a_codec():
    rep = asyncio.run(device_check(ReadOnlyLink(fw="3.0.18", codec=0x0f), legacy_files=True, assume_codec=21))
    assert rep["drain_path"] == "legacy-files" and rep["codec_assumed_as"] == 21 and rep["frame_ms"] == 20.0


def test_check_flags_an_untrustworthy_device_clock():
    rep = asyncio.run(device_check(ReadOnlyLink(dev_time=12345), clock=lambda: 1_800_000_000.0))
    assert rep["device_clock"]["trusted"] is False


# --- config + the flag-dark entry point -------------------------------------------------------------------
def test_everything_is_off_by_default():
    s = config.load({})
    assert s.enabled is False and s.address == "" and s.device_token == ""
    assert s.max_recording_s == 600.0 and s.gap_s == 90.0
    # every WRITE or riskier path is opt-in: the pendant clock, the 3.0.18 file protocol (frees audio on delivery), an assumed codec
    assert s.set_device_time is False and s.legacy_files is False and s.assume_codec is None
    assert s.lecc is True and s.lecc_prefix == ("sudo", "-n") and s.addr_type == "random"


def test_opt_ins_parse():
    s = config.load({"OMI_SYNC_LEGACY_FILES": "1", "OMI_SYNC_SET_DEVICE_TIME": "yes", "OMI_SYNC_ASSUME_CODEC": "21",
                     "OMI_SYNC_LECC_PREFIX": "sudo -n -u root", "OMI_PENDANT_ADDR_TYPE": "public"})
    assert (s.legacy_files, s.set_device_time, s.assume_codec, s.addr_type) == (True, True, 21, "public")
    assert s.lecc_prefix == ("sudo", "-n", "-u", "root")
    assert config.load({"OMI_SYNC_ASSUME_CODEC": "nope"}).assume_codec is None


# --- the connection recipe (pure parts; the radio itself needs the pendant) ----------------------------------------------
def test_the_link_is_created_with_hcitool_lecc_and_its_handle_is_parsed():
    from omi_sync import ble_link
    assert ble_link.lecc_argv(("sudo", "-n"), "E4:A3:57:6C:8D:0D", "random") == [
        "sudo", "-n", "hcitool", "lecc", "--random", "E4:A3:57:6C:8D:0D"]
    assert ble_link.lecc_argv((), "AA", "public") == ["hcitool", "lecc", "AA"]
    assert ble_link.parse_lecc_handle("Connection handle 64\n") == 64
    assert ble_link.parse_lecc_handle("Could not create connection: Input/output error") is None
    assert ble_link.parse_lecc_handle("") is None


def test_this_module_never_pairs_or_registers_an_agent():
    """The bring-up connected with no bond (Paired: no); a pairing call here would hand the sole bond to the Pi for nothing."""
    src = (Path(__file__).resolve().parents[2] / "services" / "omi-sync" / "omi_sync" / "ble_link.py").read_text()
    code = "\n".join(line for line in src.splitlines() if not line.lstrip().startswith("#"))
    assert "pair=" not in code and ".pair(" not in code and "RegisterAgent" not in code and "bluetoothctl" not in code


def test_flag_values_parse():
    assert config.load({"OMI_SYNC_ENABLED": "1"}).enabled is True
    assert config.load({"OMI_SYNC_ENABLED": "off"}).enabled is False
    assert config.load({"OMI_SYNC_GAP_S": "nonsense"}).gap_s == 90.0


def test_run_with_the_flag_off_exits_without_touching_bluetooth(monkeypatch, tmp_path, caplog):
    monkeypatch.delenv("OMI_SYNC_ENABLED", raising=False)
    monkeypatch.setenv("OMI_SYNC_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "bleak", None)         # importing it would raise: the off path must not try
    assert cli.main(["run"]) == 0
    assert not (tmp_path / "spool.sqlite3").exists()        # not even the spool is created


def test_status_needs_no_bluetooth_and_prints_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OMI_SYNC_DIR", str(tmp_path))
    monkeypatch.setenv("DEVICE_TOKEN", "secret-token")
    monkeypatch.setitem(sys.modules, "bleak", None)
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    doc = json.loads(out)
    assert doc["enabled"] is False and doc["token_set"] is True
    assert "secret-token" not in out                         # the token is reported as set/unset, never echoed
