"""omi-sync also syncs the Omi pendants the panel's Bluetooth agent REMEMBERS (its ``KnownStore`` file), unioned with the env list, so a
member who links their pendant at the panel ("Zoe, link my pendant") needs no operator env step (``docs/knowledge/omi-linking.md``).

Negative controls (each turns a named test red when the guard is removed by Edit):
  ``known_omi_addresses`` without the ``driver == "omi"`` check            -> test_only_omi_pendants_from_the_known_store_join_the_env_list
  ``union_addresses`` without the case-insensitive dedupe                    -> test_only_omi_pendants_from_the_known_store_join_the_env_list
  ``Runner.step`` without ``rotation.refresh``                               -> test_a_pendant_linked_after_start_is_visited_without_a_restart
  ``cli.main`` running the loop before the ``enabled`` check                 -> test_the_flag_still_turns_everything_off_with_remembered_pendants
  the refresh log listing device ids                                         -> test_the_refresh_log_carries_a_count_never_an_id
  the reader pointed at a different format than ``panel_bt.known.KnownStore`` -> test_the_reader_reads_what_the_agent_writes

Synthetic addresses only. No radio, no network.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "omi-sync"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "panel-bluetooth"))

from omi_sync import cli, config  # noqa: E402
from omi_sync.engine import DrainResult  # noqa: E402
from omi_sync.runner import Runner  # noqa: E402
from omi_sync.uploader import device_hash  # noqa: E402
from panel_bt.known import KnownStore  # noqa: E402

A, B, C, D = "AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03", "AA:BB:CC:DD:EE:04"


def write_known(path: Path, entries: dict) -> None:
    path.write_text(json.dumps({device_hash(a): {"address": a, "addr_type": "random", "name": "x", "driver": drv, "added_at": 1.0}
                                for a, drv in entries.items()}), encoding="utf-8")


@pytest.fixture(autouse=True)
def lock_path(tmp_path, monkeypatch):
    monkeypatch.setenv("ZOE_BLE_RADIO_LOCK", str(tmp_path / "radio.lock"))


def test_only_omi_pendants_from_the_known_store_join_the_env_list(tmp_path):
    known = tmp_path / "known.json"
    write_known(known, {B: "omi", C: "generic", A.lower(): "omi"})
    s = config.load({"OMI_PENDANT_ADDRESSES": A, "OMI_SYNC_KNOWN_DEVICES": str(known)})
    assert s.addresses == (A, B), "env first; a remembered Omi joins; a remembered speaker (generic) never does; no duplicate of A"
    assert s.current_addresses() == (A, B)
    # control: nothing remembered = exactly the env list, as before
    assert config.load({"OMI_PENDANT_ADDRESSES": A, "OMI_SYNC_KNOWN_DEVICES": str(tmp_path / "absent.json")}).addresses == (A,)
    assert config.load({"OMI_PENDANT_ADDRESSES": A, "OMI_SYNC_KNOWN_DEVICES": "off"}).addresses == (A,)


def test_a_malformed_known_store_is_no_pendants_never_an_error(tmp_path):
    bad = tmp_path / "known.json"
    for body in ("{not json", "[1, 2]", json.dumps({"x": {"address": "not-an-address", "driver": "omi"}})):
        bad.write_text(body, encoding="utf-8")
        assert config.known_omi_addresses(bad) == ()


def test_the_default_path_is_the_agents_own(tmp_path):
    s = config.load({"PANEL_BT_DIR": str(tmp_path)})
    assert s.known_path == tmp_path / "known.json"


def test_the_reader_reads_what_the_agent_writes(tmp_path):
    store = KnownStore(tmp_path / "known.json")
    did = store.add(B, "random", "Omi", "omi")
    store.add(C, "public", "Speaker", "generic")
    assert did == device_hash(B), "one identity: the agent's id is the uploader's id"
    assert config.known_omi_addresses(tmp_path / "known.json") == (B,)


def _runner(s):
    seen: list[str] = []

    import contextlib

    @contextlib.asynccontextmanager
    async def connect(address, **kw):
        seen.append(address)
        yield object()

    class FakeDrain:
        def __init__(self, link, cfg):
            self.cfg = cfg

        async def run(self):
            return DrainResult(path="ring", read_complete=True)

    clock = [1000.0]
    r = Runner(s, None, None, connect=connect, drain_factory=lambda link, cfg: FakeDrain(link, cfg), clock=lambda: clock[0])
    return r, seen, clock


def test_a_pendant_linked_after_start_is_visited_without_a_restart(tmp_path):
    known = tmp_path / "known.json"
    s = config.load({"OMI_PENDANT_ADDRESSES": A, "OMI_SYNC_KNOWN_DEVICES": str(known), "OMI_SYNC_COOLDOWN_S": "120"})
    r, seen, clock = _runner(s)
    asyncio.run(r.step())
    assert seen == [A]
    write_known(known, {D: "omi"})                     # a member links their pendant at the panel; the agent remembers it
    asyncio.run(r.step())
    assert seen == [A, D], "the new pendant is due at once, with no env edit and no restart"
    assert [t.device_id for t in r.rotation.targets] == [device_hash(A), device_hash(D)]
    known.write_text("{}", encoding="utf-8")           # forgotten at the panel: it leaves; the env pendant keeps its state
    a_next = r.rotation.targets[0].next_at
    asyncio.run(r.step())
    assert [t.device_id for t in r.rotation.targets] == [device_hash(A)] and r.rotation.targets[0].next_at == a_next


def test_no_pendant_at_all_idles_instead_of_exiting(tmp_path, monkeypatch):
    called = []

    async def fake_loop(s):
        called.append(s.addresses)

    monkeypatch.setattr(cli, "run_loop", fake_loop)
    monkeypatch.setenv("OMI_SYNC_ENABLED", "1")
    monkeypatch.setenv("DEVICE_TOKEN", "t")
    monkeypatch.delenv("OMI_PENDANT_ADDRESSES", raising=False)
    monkeypatch.delenv("OMI_PENDANT_ADDRESS", raising=False)
    monkeypatch.setenv("OMI_SYNC_KNOWN_DEVICES", str(tmp_path / "absent.json"))
    assert cli.main(["run"]) == 0 and called == [()], "it waits for a pendant to be linked rather than exiting 2"


def test_the_flag_still_turns_everything_off_with_remembered_pendants(tmp_path, monkeypatch):
    known = tmp_path / "known.json"
    write_known(known, {B: "omi"})
    called = []

    async def fake_loop(s):
        called.append(s.addresses)

    monkeypatch.setattr(cli, "run_loop", fake_loop)
    monkeypatch.setenv("OMI_SYNC_KNOWN_DEVICES", str(known))
    monkeypatch.setenv("DEVICE_TOKEN", "t")
    monkeypatch.delenv("OMI_PENDANT_ADDRESSES", raising=False)
    monkeypatch.setenv("OMI_SYNC_ENABLED", "0")
    assert cli.main(["run"]) == 0 and called == [], "OMI_SYNC_ENABLED off: a remembered pendant changes nothing"
    monkeypatch.setenv("OMI_SYNC_ENABLED", "1")       # control: on, the remembered pendant is synced
    assert cli.main(["run"]) == 0 and called == [(B,)]


def test_the_refresh_log_carries_a_count_never_an_id(tmp_path, caplog):
    import logging
    known = tmp_path / "known.json"
    s = config.load({"OMI_PENDANT_ADDRESSES": A, "OMI_SYNC_KNOWN_DEVICES": str(known)})
    r, _seen, _clock = _runner(s)
    write_known(known, {D: "omi"})
    with caplog.at_level(logging.INFO, logger="omi_sync"):
        asyncio.run(r.step())
    text = "\n".join(r.getMessage() for r in caplog.records if "pendant list" in r.getMessage())
    assert "2 pendant(s)" in text, "the refresh line (the visit's own sync line is pre-existing and not this guard's)"
    for ident in (device_hash(A), device_hash(D), device_hash(A)[:6], A, D):
        assert ident not in text, "no device id or address in the refresh log"
