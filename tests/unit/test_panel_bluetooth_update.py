"""panel-bluetooth: the Omi firmware update - every safety gate blocks, the pinned release is verified, the staged procedure stops on the
first doubt and never retries. Hardware-free: a fake radio and a fake MCUboot (``tests/unit/panel_bt_fakes.py``).

UNVERIFIED ON HARDWARE: the real SMP session (``smp_session.py``). These tests pin the PROCEDURE and its gates, not smpclient.

Facts under test come from the supervised 2026-10-12 attempt: overwrite-only bootloader (no revert), two images (app + net core),
charger + battery >= 50 % + median signal better than -75 dBm over >= 5 steady samples, no settings-partition erase, stages in
order, never an automatic retry.

Source mutations worth re-running by Edit (each must turn a named test red):
  ``g.charging is True`` -> ``g.charging is not False`` in evaluate_gates      -> test_each_gate_blocks[charging-unknown]
  ``s["median"] > min_rssi`` -> ``>=``                                         -> test_the_signal_gate_needs_a_median_BETTER_than_the_floor
  drop the secondary-hash check before ``mark_permanent``                      -> test_a_secondary_slot_that_does_not_match_the_zip_is_never_marked
  send ``reset`` without the pending-count check                               -> test_reset_is_only_sent_when_exactly_the_expected_images_are_pending
  record the cool-down for E_GATE failures too / drop it for E_UPDATE          -> test_a_failed_update_starts_a_cool_down_and_is_never_retried
  drop the sha256 comparison in fetch_package                                  -> test_a_download_that_does_not_match_the_pin_is_refused
"""
from __future__ import annotations

import asyncio
import itertools
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

from tests.unit.panel_bt_fakes import (OMI_ADDR, FakeBackend, FakeSmp, advert, make_package, mcuboot_image, omi_chars, settings)  # noqa: E402

from panel_bt.agent import PanelBluetoothAgent  # noqa: E402
from panel_bt.drivers import DriverRegistry  # noqa: E402
from panel_bt.drivers import omi_dfu  # noqa: E402
from panel_bt.drivers.omi import OmiDriver  # noqa: E402
from panel_bt.drivers.omi_dfu import GateInputs, OtaState, UpdateDeps, evaluate_gates, failed_gates, rssi_summary  # noqa: E402
from panel_bt.drivers.omi_release import (DEFAULT_PIN, Pin, ReleaseError, fetch_package, load_pin, parse_package, sha256_hex)  # noqa: E402
from panel_bt.model import (E_BAD_REQUEST, E_CANCELLED, E_CONNECT, E_GATE, E_OTA_OFF, E_UPDATE, device_id)  # noqa: E402

OMI_ID = device_id(OMI_ADDR)
GOOD_RSSI = [-62, -60, -61, -63, -60, -62]
_job = itertools.count(1)


# --- the pinned release ----------------------------------------------------------------------------------------------------
def test_the_shipped_pin_is_the_official_github_release_with_a_sha256():
    pin = load_pin()
    assert pin.version == "3.0.21" and pin.model == "Omi CV 1" and pin.minimum_firmware == "3.0.6"
    assert pin.url.startswith("https://github.com/BasedHardware/omi/releases/download/Omi_CV1_v3.0.21/")
    assert re.fullmatch(r"[0-9a-f]{64}", pin.sha256) and pin.size == 424966
    assert [(i.index, i.file, i.core) for i in pin.images] == [(0, "omi.signed.bin", "app"), (1, "ipc_radio.bin", "net")]
    assert DEFAULT_PIN.is_file()


def test_the_pin_is_not_gitignored_and_the_deploy_ships_it():
    """The blanket ``*.json`` rule once hid this file (it existed only on disk and CI failed). It must stay trackable, and the deploy
    script must ship the whole ``panel_bt`` tree (so the pin reaches the Pi)."""
    import shutil
    import subprocess
    root = Path(__file__).resolve().parents[2]
    pin = "services/panel-bluetooth/panel_bt/data/omi_firmware.json"
    if shutil.which("git") and (root / ".git").exists():
        r = subprocess.run(["git", "check-ignore", "-q", "--no-index", pin], cwd=root)
        assert r.returncode == 1, "omi_firmware.json is gitignored - add/keep the negation in .gitignore"
    deploy = (root / "scripts" / "setup" / "deploy-pi-bluetooth.sh").read_text()
    assert "tar --exclude='__pycache__' -c panel_bt requirements.txt" in deploy and "find panel_bt -type f" in deploy


@pytest.mark.parametrize("change", [
    {"url": "http://github.com/BasedHardware/omi/x.zip"}, {"url": "https://api.omi.me/firmware.zip"},
    {"url": "https://evil.example/github.com/x.zip"}, {"sha256": "abc"}, {"sha256": "Z" * 64}, {"version": "latest"},
    {"images": [{"index": 1, "file": "a", "size": 1}]}, {"images": []},
])
def test_a_malformed_or_foreign_pin_is_refused(tmp_path, change):
    doc = json.loads(DEFAULT_PIN.read_text())
    doc.update(change)
    p = tmp_path / "pin.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(ReleaseError):
        load_pin(p)


def test_a_download_that_does_not_match_the_pin_is_refused(tmp_path):
    data, pin, _ = make_package()
    tampered = bytearray(data)
    tampered[len(tampered) // 2] ^= 0xFF
    with pytest.raises(ReleaseError, match="SHA-256"):
        fetch_package(pin, tmp_path, download=lambda url, mx: bytes(tampered))
    assert not list(tmp_path.glob("*.zip")), "a bad download must not be cached"
    with pytest.raises(ReleaseError):
        fetch_package(pin, tmp_path, download=lambda url, mx: data + b"x")        # wrong size


def test_a_good_download_is_cached_and_a_corrupted_cache_is_not_trusted(tmp_path):
    data, pin, _ = make_package()
    calls = []
    got = fetch_package(pin, tmp_path, download=lambda url, mx: calls.append(url) or data)
    assert got == data and len(calls) == 1
    assert fetch_package(pin, tmp_path, download=lambda url, mx: calls.append(url) or data) == data and len(calls) == 1      # cache hit
    next(tmp_path.glob("*.zip")).write_bytes(b"corrupt")
    assert fetch_package(pin, tmp_path, download=lambda url, mx: calls.append(url) or data) == data and len(calls) == 2      # re-fetched


def test_the_package_is_checked_image_by_image_against_the_pin():
    data, pin, imgs = make_package()
    out = parse_package(data, pin)
    assert [i.index for i in out] == [0, 1] and out[0].data == imgs[0] and len(out[0].hash) == 32
    # a body byte flipped inside image 0 (zip rebuilt): its own SHA-256 TLV no longer matches
    import io, zipfile
    bad = bytearray(imgs[0])
    bad[40] ^= 1
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", zipfile.ZipFile(io.BytesIO(data)).read("manifest.json"))
        z.writestr("omi.signed.bin", bytes(bad))
        z.writestr("ipc_radio.bin", imgs[1])
    with pytest.raises(ReleaseError, match="SHA-256"):
        parse_package(buf.getvalue(), pin)
    # not an MCUboot image at all
    with pytest.raises(ReleaseError):
        parse_package(_zip_with({"omi.signed.bin": b"\x00" * 300, "ipc_radio.bin": imgs[1]}, data), pin)
    # an extra image the pin does not list
    pin1 = Pin(**{**pin.__dict__, "images": pin.images[:1]})
    with pytest.raises(ReleaseError):
        parse_package(data, pin1)


def _zip_with(files: dict, orig: bytes) -> bytes:
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("manifest.json", zipfile.ZipFile(io.BytesIO(orig)).read("manifest.json"))
        for k, v in files.items():
            z.writestr(k, v)
    return buf.getvalue()


# --- the gates ----------------------------------------------------------------------------------------------------------------
def good(**kw) -> GateInputs:
    base = dict(ota_enabled=True, model="Omi CV 1", firmware="3.0.18", battery=100, charging=True, rssi_samples=list(GOOD_RSSI),
                cooldown_remaining_s=0.0, other_job=False)
    base.update(kw)
    return GateInputs(**base)


def gates(g: GateInputs, **kw):
    pin = load_pin()
    return evaluate_gates(g, pin, min_battery=kw.get("min_battery", 50), min_rssi=kw.get("min_rssi", -75))


def test_the_control_case_passes_every_gate():
    gs = gates(good())
    assert not failed_gates(gs) and {g.id for g in gs} == {"ota_enabled", "model", "firmware", "charging", "battery", "signal", "cooldown", "idle"}


@pytest.mark.parametrize("name,kw,gate", [
    ("ota-off", dict(ota_enabled=False), "ota_enabled"),
    ("wrong-model", dict(model="Omi EVT"), "model"),
    ("unknown-model", dict(model=None), "model"),
    ("already-current", dict(firmware="3.0.21"), "firmware"),
    ("newer-than-pin", dict(firmware="3.1.0"), "firmware"),
    ("too-old-to-jump", dict(firmware="3.0.5"), "firmware"),
    ("firmware-unreadable", dict(firmware=None), "firmware"),
    ("firmware-garbage", dict(firmware="beta"), "firmware"),
    ("not-charging", dict(charging=False), "charging"),
    ("charging-unknown", dict(charging=None), "charging"),
    ("battery-49", dict(battery=49), "battery"),
    ("battery-unknown", dict(battery=None), "battery"),
    ("signal-weak", dict(rssi_samples=[-91] * 6), "signal"),
    ("signal-just-too-weak", dict(rssi_samples=[-75] * 6), "signal"),
    ("signal-too-few-samples", dict(rssi_samples=[-60, -61, -60]), "signal"),
    ("signal-unsteady", dict(rssi_samples=[-50, -50, -50, -70, -70, -70]), "signal"),
    ("signal-not-measured", dict(rssi_samples=None), "signal"),
    ("signal-silent", dict(rssi_samples=[]), "signal"),
    ("cooldown", dict(cooldown_remaining_s=120.0), "cooldown"),
    ("other-job", dict(other_job=True), "idle"),
])
def test_each_gate_blocks(name, kw, gate):
    gs = gates(good(**kw))
    bad = failed_gates(gs)
    assert [g.id for g in bad] == [gate], f"{name}: expected only {gate} to fail, got {[g.id for g in bad]}"
    assert bad[0].detail or bad[0].hint


def test_boundaries_battery_50_passes_and_the_signal_gate_needs_a_median_BETTER_than_the_floor():
    assert not failed_gates(gates(good(battery=50)))
    assert failed_gates(gates(good(battery=49)))
    assert not failed_gates(gates(good(rssi_samples=[-74] * 6)))
    assert [g.id for g in failed_gates(gates(good(rssi_samples=[-75] * 6)))] == ["signal"]
    assert rssi_summary([-60, -62, -64]) == {"n": 3, "median": -62, "spread": 4}


def test_a_weak_signal_tells_the_household_to_move_it_closer_or_use_a_usb_adapter():
    sig = next(g for g in gates(good(rssi_samples=[-91] * 6)) if g.id == "signal")
    assert "closer" in sig.hint.lower() and "usb" in sig.hint.lower()


# --- the Omi driver's update check ----------------------------------------------------------------------------------------------
async def test_update_check_reports_availability_gates_and_the_no_undo_warning(tmp_path):
    s = settings(tmp_path, PANEL_BT_OTA_ENABLED=1)
    info = {"model": "Omi CV 1", "firmware": "3.0.18", "battery": 93, "charging": False}
    out = await OmiDriver().update_check(info, {"settings": s})
    assert out["available"] and out["current"] == "3.0.18" and out["latest"] == "3.0.21" and out["needs_ack"]
    assert "cannot be undone" in out["warning"] and "charger" in out["warning"] and "2 minutes" in out["warning"]
    assert out["ready"] is False and "charging" in out["blocked_by"] and out["blocked_by"] == ["charging"]
    assert next(g for g in out["gates"] if g["id"] == "signal")["ok"] is False        # not measured until Update is pressed
    ok = await OmiDriver().update_check({**info, "charging": True}, {"settings": s})
    assert ok["ready"] is True and ok["blocked_by"] == []
    cur = await OmiDriver().update_check({**info, "firmware": "3.0.21"}, {"settings": s})
    assert cur["available"] is False and "3.0.21" in cur["reason"]


# --- the procedure ------------------------------------------------------------------------------------------------------------------
class Rig:
    """An agent wired to a fake pendant, a fake MCUboot and the real procedure."""

    def __init__(self, tmp_path, monkeypatch, *, ota=True, rssi=None, chars=None, smp_flags=None, fetch=None, **env):
        monkeypatch.setattr(omi_dfu, "POST_WAIT_S", 30.0)
        self.data, self.pin, self.imgs = make_package()
        self.pkg = parse_package(self.data, self.pin)
        self.backend = FakeBackend([advert(OMI_ADDR, "Omi", -60)], {OMI_ADDR: chars if chars is not None else omi_chars()})
        self.backend.rssi[OMI_ADDR] = list(GOOD_RSSI) if rssi is None else rssi
        self.smp = FakeSmp(self.pkg, **(smp_flags or {}))
        self.fetched = []
        self.s = settings(tmp_path, PANEL_BT_OTA_ENABLED=1 if ota else 0, **env)
        self.state = OtaState(self.s.ota_state_path, self.s.ota_cooldown_s)

        async def nosleep(_):
            return None

        def fetch_fn(pin, cache):
            self.fetched.append(1)
            return fetch(pin, cache) if fetch else self.data

        def deps(_uctx):
            return UpdateDeps(smp_factory=lambda link: self._smp_for(link), pin=self.pin, fetch=fetch_fn, sleep=nosleep, state=self.state)

        self.agent = PanelBluetoothAgent(self.s, self.backend, DriverRegistry([OmiDriver(deps)]), None)
        self.links = []

    def _smp_for(self, link):
        self.smp.link = link
        return self.smp

    def after_reset_comes_back_as(self, fw="3.0.21", visible=True):
        """Once reset is called, the next connect reads ``fw`` and the advert is (not) heard again."""
        orig = self.smp.reset

        async def reset():
            self.backend.chars[OMI_ADDR] = omi_chars(fw=fw)
            if not visible:
                self.backend.rssi[OMI_ADDR] = []
            try:
                await orig()
            finally:
                self.backend.rssi[OMI_ADDR] = [] if not visible else list(GOOD_RSSI)

        self.smp.reset = reset

    async def run(self, op, params=None):
        job = f"job{next(_job):06d}"
        await self.agent.execute({"job_id": job, "op": op, "device_id": OMI_ID, "params": params or {}})
        return [p for p in self.agent._outbox if p["job_id"] == job][-1]

    async def start(self, **params):
        await self.run("connect") if False else None
        return await self.run("update_start", {"confirm": True, "ack_no_undo": True, **params})

    @property
    def touched_pendant(self):
        return [c for c in self.smp.calls if isinstance(c, tuple) and c[0] in ("upload", "mark")] + [c for c in self.smp.calls if c == "reset"]


async def prime(rig):
    await rig.agent.execute({"job_id": "scanjob001", "op": "scan", "params": {}})


async def test_the_happy_path_runs_the_stages_in_order_and_ends_on_the_new_firmware(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    rig.after_reset_comes_back_as("3.0.21")
    await prime(rig)
    r = await rig.start()
    assert r["state"] == "succeeded", r
    assert r["result"]["outcome"] == "updated" and r["result"]["from"] == "3.0.18" and r["result"]["to"] == "3.0.21"
    calls = [c for c in rig.smp.calls if c not in ("images", "open", "close")]
    h0, h1 = rig.pkg[0].hash.hex()[:8], rig.pkg[1].hash.hex()[:8]
    assert calls[:5] == [("upload", 0), ("upload", 1), ("mark", h0), ("mark", h1), "reset"], calls          # upload both, mark both, THEN reset
    assert rig.fetched == [1]
    assert not rig.agent.lock.held and not rig.backend.live_links()
    assert not rig.state.remaining_s(), "a success starts no cool-down"


async def test_progress_climbs_through_the_stages_and_warns_about_the_two_minutes(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    rig.after_reset_comes_back_as()
    seen = []

    class Zoe:
        async def report(self, p):
            if p.get("state") == "running":
                seen.append(p["progress"])

        async def poll(self, *a):
            return []

    await prime(rig)
    rig.agent.zoe = Zoe()
    await rig.agent.execute({"job_id": "progjob001", "op": "update_start", "device_id": OMI_ID,
                             "params": {"confirm": True, "ack_no_undo": True}})
    steps = [p["step"] for p in seen]
    order = [s for s in dict.fromkeys(steps)]
    assert order.index("download") < order.index("connect") < order.index("upload") < order.index("install") < order.index("reset") < order.index("waiting") < order.index("confirm")
    pcts = [p["pct"] for p in seen if p["step"] != "update_start"]
    assert pcts == sorted(pcts), "progress never goes backwards"
    assert any("2 minutes" in p["message"] for p in seen if p["step"] in ("reset", "waiting"))


async def test_each_failed_gate_stops_before_the_pendant_is_touched(tmp_path, monkeypatch):
    cases = {
        "not charging": dict(chars=omi_chars(charging=False)),
        "charging unreadable": dict(chars=omi_chars(charging=None)),
        "battery 40": dict(chars=omi_chars(battery=40)),
        "already current": dict(chars=omi_chars(fw="3.0.21")),
        "wrong model": dict(chars=omi_chars(model="Omi EVT")),
        "weak signal": dict(rssi=[-91] * 6),
        "no adverts": dict(rssi=[]),
        "unsteady": dict(rssi=[-50, -50, -50, -72, -72, -72]),
    }
    for label, kw in cases.items():
        rig = Rig(tmp_path / label.replace(" ", "_"), monkeypatch, **kw)
        await prime(rig)
        r = await rig.start()
        assert r["state"] == "failed" and r["error"]["code"] == E_GATE, (label, r)
        assert rig.touched_pendant == [], label
        assert "open" not in rig.smp.calls, f"{label}: no SMP session may even open"
        assert not rig.state.remaining_s(), f"{label}: a refused gate is not a failed attempt"
        assert not rig.agent.lock.held and not rig.backend.live_links(), label


async def test_a_weak_signal_stops_before_any_connection_or_download(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, rssi=[-91] * 6)
    await prime(rig)
    r = await rig.start()
    assert r["error"]["code"] == E_GATE and "USB" in r["error"]["message"]
    assert rig.fetched == [] and not any(c[0] == "connect" for c in rig.backend.calls)


async def test_updates_off_on_the_panel_refuses_even_a_confirmed_request(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, ota=False)
    await prime(rig)
    r = await rig.start()
    assert r["state"] == "failed" and r["error"]["code"] == E_OTA_OFF and rig.touched_pendant == []
    assert not any(c[0] in ("connect", "sample_rssi") for c in rig.backend.calls)


async def test_an_unconfirmed_request_is_refused(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    await prime(rig)
    for params in ({}, {"confirm": True}, {"ack_no_undo": True}, {"confirm": "yes", "ack_no_undo": True}):
        r = await rig.run("update_start", params)
        assert r["state"] == "failed" and r["error"]["code"] == E_BAD_REQUEST, params
    assert not rig.backend.calls[1:], "nothing may touch the radio before the request is confirmed"


async def test_a_download_that_does_not_verify_changes_nothing(tmp_path, monkeypatch):
    def bad(pin, cache):
        raise ReleaseError("the downloaded firmware does not match the pinned SHA-256 - refusing to use it")

    rig = Rig(tmp_path, monkeypatch, fetch=bad)
    await prime(rig)
    r = await rig.start()
    assert r["error"]["code"] == E_UPDATE and "nothing was changed" in r["error"]["message"]
    assert not any(c[0] == "connect" for c in rig.backend.calls) and rig.touched_pendant == []


async def test_a_secondary_slot_that_does_not_match_the_zip_is_never_marked(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, smp_flags={"corrupt_hash_for": 1})
    await prime(rig)
    r = await rig.start()
    assert r["state"] == "failed" and r["error"]["code"] == E_UPDATE and "does not match" in r["error"]["message"]
    assert [c for c in rig.smp.calls if isinstance(c, tuple) and c[0] == "mark"] == [] and "reset" not in rig.smp.calls
    assert [c for c in rig.smp.calls if isinstance(c, tuple) and c[0] == "upload"] == [("upload", 0), ("upload", 1)]


async def test_a_slot_already_waiting_to_install_is_not_touched(tmp_path, monkeypatch):
    from panel_bt.drivers.omi_dfu import SlotInfo
    rig = Rig(tmp_path, monkeypatch)
    rig.smp.slots.append(SlotInfo(0, 1, "0.0.0", "b" * 64, pending=True))
    await prime(rig)
    r = await rig.start()
    assert r["error"]["code"] == E_UPDATE and "already waiting" in r["error"]["message"] and rig.touched_pendant == []


async def test_reset_is_only_sent_when_exactly_the_expected_images_are_pending(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    orig = rig.smp.mark_permanent

    async def mark_only_first(h):
        if not any(isinstance(c, tuple) and c[0] == "mark" for c in rig.smp.calls):
            await orig(h)
        else:
            rig.smp.calls.append(("mark", h.hex()[:8]))                    # the second mark "succeeds" but sticks nowhere

    rig.smp.mark_permanent = mark_only_first
    await prime(rig)
    r = await rig.start()
    assert r["state"] == "failed" and "Not restarting" in r["error"]["message"]
    assert "reset" not in rig.smp.calls


async def test_the_charger_check_is_repeated_just_before_the_irreversible_step(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    orig = rig.smp.mark_permanent

    async def mark_then_unplug(h):
        await orig(h)
        rig.backend.links[-1].chars[omi_dfu.OMI_CHARGING_UUID] = bytes([0])      # the live link's view of the charging characteristic

    rig.smp.mark_permanent = mark_then_unplug
    await prime(rig)
    r = await rig.start()
    assert r["state"] == "failed" and r["error"]["code"] == E_GATE and "stopped charging" in r["error"]["message"]
    assert "reset" not in rig.smp.calls


async def test_a_failed_update_starts_a_cool_down_and_is_never_retried(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch, smp_flags={"fail_upload_at": 1})
    await prime(rig)
    r = await rig.start()
    assert r["state"] == "failed" and r["error"]["code"] == E_UPDATE and "unchanged" in r["error"]["message"]
    uploads = [c for c in rig.smp.calls if isinstance(c, tuple) and c[0] == "upload"]
    assert uploads == [("upload", 0), ("upload", 1)], "each image is attempted once - there is no automatic retry"
    assert "reset" not in rig.smp.calls and rig.state.remaining_s() > 0
    again = await rig.start()                                                # an explicit second try is still refused during the cool-down
    assert again["error"]["code"] == E_GATE and "previous attempt failed" in again["error"]["message"]
    assert len([c for c in rig.smp.calls if isinstance(c, tuple) and c[0] == "upload"]) == 2
    # ...and the cool-down survives a restart of the service
    assert OtaState(rig.s.ota_state_path, rig.s.ota_cooldown_s).remaining_s() > 0


async def test_an_unreachable_pendant_before_any_install_changes_nothing_and_asks_for_no_cool_down(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    rig.backend.connect_fail = 5
    await prime(rig)
    r = await rig.start()
    assert r["error"]["code"] == E_CONNECT and "nothing was changed" in r["error"]["message"] and not rig.state.remaining_s()


async def test_cancel_during_upload_leaves_the_pendant_unchanged(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    orig = rig.smp.upload

    async def cancel_after_first_chunk(idx, data, on_offset):
        async def trip(off):
            rig.agent._cancel.set()
            await on_offset(off)

        await orig(idx, data, trip)

    rig.smp.upload = cancel_after_first_chunk
    await prime(rig)
    r = await rig.start()
    assert r["state"] == "cancelled" and r["error"]["code"] == E_CANCELLED and "unchanged" in r["error"]["message"]
    assert [c for c in rig.smp.calls if isinstance(c, tuple) and c[0] == "mark"] == [] and "reset" not in rig.smp.calls
    assert not rig.state.remaining_s(), "a cancel is not a failure"


async def test_cancel_cannot_stop_an_install_once_it_has_been_ordered(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    rig.after_reset_comes_back_as()
    orig = rig.smp.mark_permanent

    async def mark_and_cancel(h):
        await orig(h)
        rig.agent._cancel.set()

    rig.smp.mark_permanent = mark_and_cancel
    await prime(rig)
    r = await rig.start()
    assert r["state"] == "succeeded" and "reset" in rig.smp.calls, "after 'install' the only safe direction is forward"


async def test_a_pendant_that_never_comes_back_is_reported_not_retried(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    rig.after_reset_comes_back_as(visible=False)
    await prime(rig)
    r = await rig.start()
    assert r["state"] == "failed" and r["error"]["code"] == E_UPDATE and "did not come back" in r["error"]["message"]
    assert "charger" in r["error"]["message"] and rig.smp.calls.count("reset") == 1
    waits = [c for c in rig.backend.calls if c[0] == "sample_rssi"]
    assert len(waits) <= 1 + 3 + 2, "the wait is bounded (30 s patched here), not a loop"
    assert rig.state.remaining_s() > 0


async def test_wrong_firmware_after_the_restart_is_a_failure_and_nothing_is_confirmed(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    rig.after_reset_comes_back_as("3.0.18")
    await prime(rig)
    r = await rig.start()
    assert r["error"]["code"] == E_UPDATE and "3.0.18" in r["error"]["message"] and "expected 3.0.21" in r["error"]["message"]
    assert [c for c in rig.smp.calls if isinstance(c, tuple) and c[0] == "mark"][2:] == []


async def test_an_unconfirmed_running_image_is_confirmed_after_the_restart(tmp_path, monkeypatch):
    rig = Rig(tmp_path, monkeypatch)
    rig.after_reset_comes_back_as()
    await prime(rig)
    r = await rig.start()
    assert r["state"] == "succeeded"
    marks = [c for c in rig.smp.calls if isinstance(c, tuple) and c[0] == "mark"]
    assert len(marks) == 3 and marks[2][1] == rig.pkg[0].hash.hex()[:8], "the third mark confirms the running (app) image"


async def test_the_update_never_erases_settings_or_sends_storage_commands():
    src = (Path(omi_dfu.__file__).parent.parent / "smp_session.py").read_text() + Path(omi_dfu.__file__).read_text()
    code = "\n".join(line for line in src.splitlines() if not line.strip().startswith(("#", '"""', "*", "-")))
    assert not re.search(r"(?i)erase_app_settings|eraseAppSettings|SettingsManagement|30295781|storage_data|CMD_RING_CLEAR|DELETE_FILE", code)
    assert "ResetWrite" in src and "ImageStatesWrite" in src and "slot=image_index" in src


async def test_a_busy_radio_blocks_an_update_start(tmp_path, monkeypatch):
    from panel_bt.model import E_BUSY
    from panel_bt.radio_lock import RadioLock
    rig = Rig(tmp_path, monkeypatch)
    await prime(rig)
    other = RadioLock(rig.s.radio_lock, holder="omi-sync")
    assert other.try_acquire()
    rig.agent.lock.acquire = lambda timeout_s, poll_s=0.25: asyncio.sleep(0, False)
    try:
        r = await rig.start()
    finally:
        other.release()
    assert r["error"]["code"] == E_BUSY and rig.touched_pendant == []
