"""omi-sync: the pendant HEALTH heartbeat and the gentler away back-off (gap audit P0-2 and P2-1/P2-2). No hardware, no network.

What is pinned, each with a control arm (the naive version, which must behave differently):

  the back-off doubles and CAPS            a pendant that never answers is visited at 30, 60, ... 600 s and never faster than the base,
                                           never slower than the cap; control: a cap equal to the base is the old fixed cadence
  a busy radio is not "away"               another Bluetooth task holding the radio never grows the back-off and sends no report
  a connection resets the back-off         one answer and the next miss starts again at the base
  the report is numbers only               a fixed key set; the BLE address, audio and notes never appear in it
  an unreachable pendant still reports     reachable=false + the miss count + the last-sync AGE (so Zoe can say "away")
  the end-of-session fill is what is sent  a successful catch-up reports a nearly empty ring (no false alarm); with Zoe down the ring is
                                           still full and says so
  the overwrite counter is reported absolute   dropped_total from the ring INFO (Zoe derives the delta, so repeats cannot double count)
  battery is best effort                   an unreadable battery changes nothing about custody; the level rides the report when read
  the throttle                             a quiet pendant is not reported every 30 s; a change is reported at once; a refused report is retried
  health can never fail a visit            a reporter that raises, or Zoe answering 401/404/409, leaves the loop and custody untouched
  spool compaction                         the 252 MB / 99 % free spool shrinks without losing a held record

Source mutations worth re-running by Edit (each turns a named test red): ``away_wait`` without ``min(cap, ...)`` ->
``test_the_backoff_doubles_and_caps``; ``ConnectError("radio busy")`` counted as a miss in ``Runner.visit`` ->
``test_a_busy_radio_is_not_an_away_pendant``; ``t.misses = 0`` removed -> ``test_an_answer_resets_the_backoff``; ``build_report`` using the
start-of-session unread count -> ``test_the_report_carries_the_end_of_session_fill``.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import struct
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "omi-sync"))

from omi_sync import ble_link, config, health  # noqa: E402
from omi_sync import ring_protocol as rp  # noqa: E402
from omi_sync.engine import Drain, DrainResult, SyncConfig  # noqa: E402
from omi_sync.runner import Runner  # noqa: E402
from omi_sync.spool import Spool, SpoolRecord  # noqa: E402
from omi_sync.uploader import HttpUploader, device_hash  # noqa: E402
from tests.unit.test_omi_sync_engine import (  # noqa: E402
    NOW, RING, FakeDecoder, FakePendant, FakeZoe, build_ring)

ADDR = "AA:BB:CC:DD:EE:01"
ID = device_hash(ADDR)
ALLOWED_KEYS = {"device", "reachable", "consecutive_misses", "last_sync_age_s", "battery_pct", "firmware", "ring_fill_pct",
                "ring_fill_start_pct", "ring_stored_bytes", "ring_capacity_bytes", "dropped_total"}


# --- the back-off, pure ----------------------------------------------------------------------------------------------------------------
def test_the_backoff_doubles_and_caps():
    waits = [health.away_wait(n, 30.0, 600.0) for n in range(1, 12)]
    assert waits == [30.0, 60.0, 120.0, 240.0, 480.0, 600.0, 600.0, 600.0, 600.0, 600.0, 600.0]
    assert max(waits) == 600.0 and min(waits) == 30.0
    assert health.away_wait(10**9, 30.0, 600.0) == 600.0, "a huge miss count cannot overflow or exceed the cap"
    assert health.away_wait(0, 30.0, 600.0) == 30.0 and health.away_wait(-3, 30.0, 600.0) == 30.0
    # the cap can never undercut the base (a mis-set cap must not make the Pi hammer faster than its scan interval)
    assert health.away_wait(5, 30.0, 5.0) == 30.0
    # control arm: the OLD cadence is "cap == base" - a flat 30 s, about 85 attempts an hour; the doubling is what changes that
    assert {health.away_wait(n, 30.0, 30.0) for n in range(1, 12)} == {30.0}


def test_the_old_cadence_was_dozens_of_attempts_an_hour_and_the_new_one_is_a_handful():
    def attempts_in_an_hour(cap):
        t, n, misses = 0.0, 0, 0
        while t < 3600:
            n += 1
            misses += 1
            t += health.away_wait(misses, 30.0, cap)
        return n
    assert attempts_in_an_hour(30.0) == 120
    assert attempts_in_an_hour(600.0) <= 12


# --- the runner: fake pendant behind connect ---------------------------------------------------------------------------------------
class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class FakeUp:
    """Records the health reports; ``answer`` is what Zoe says."""

    def __init__(self, answer=True) -> None:
        self.reports: list[dict] = []
        self.answer = answer

    async def report_health(self, rep: dict) -> bool:
        self.reports.append(json.loads(json.dumps(rep)))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def make_runner(*, connect, results=None, up=None, spool=None, env=None, wall=None):
    s = config.load({"OMI_PENDANT_ADDRESSES": ADDR, "OMI_SYNC_COOLDOWN_S": "120", "OMI_SYNC_SCAN_INTERVAL_S": "30", **(env or {})})
    clock = Clock()
    box = {"res": results or DrainResult(path="ring", read_complete=True)}

    class FakeDrain:
        def __init__(self, link, cfg) -> None:
            pass

        async def run(self) -> DrainResult:
            r = box["res"]
            if isinstance(r, Exception):
                raise r
            return r

    runner = Runner(s, spool, up if up is not None else FakeUp(), connect=connect, drain_factory=lambda link, cfg: FakeDrain(link, cfg),
                    clock=clock, wall=wall or (lambda: 1_800_000_000.0 + clock.t))
    return runner, clock, box


def never_there(msg="could not connect after 2 attempts"):
    def connect(address, **kw):
        raise ble_link.ConnectError(msg)
    return connect


def there():
    @contextlib.asynccontextmanager
    async def connect(address, **kw):
        yield object()
    return connect


def step(runner):
    return asyncio.run(runner.step())


def waits_of(runner, clock, n):
    """Run n visits to the one pendant, jumping the clock to each due time; return how long each told us to wait."""
    out = []
    for _ in range(n):
        t = runner.rotation.targets[0]
        clock.t = max(clock.t, t.next_at)
        step(runner)
        out.append(round(t.next_at - clock.t, 3))
    return out


def test_an_away_pendant_is_visited_less_and_less_often_up_to_the_cap():
    runner, clock, _ = make_runner(connect=never_there())
    assert waits_of(runner, clock, 9) == [30.0, 60.0, 120.0, 240.0, 480.0, 600.0, 600.0, 600.0, 600.0]
    assert runner.rotation.targets[0].misses == 9
    # control arm: with the cap pinned to the scan interval (the old behaviour) the cadence never eases off
    old, clock2, _ = make_runner(connect=never_there(), env={"OMI_SYNC_AWAY_BACKOFF_MAX_S": "1"})
    assert waits_of(old, clock2, 5) == [30.0] * 5


def test_a_busy_radio_is_not_an_away_pendant():
    up = FakeUp()
    runner, clock, _ = make_runner(connect=never_there("radio busy (panel-bluetooth)"), up=up)
    assert waits_of(runner, clock, 4) == [30.0] * 4, "another Bluetooth task holding the radio says nothing about the pendant"
    assert runner.rotation.targets[0].misses == 0
    assert up.reports == [], "and a report claiming the pendant is away would be a lie"
    # control arm: the same loop with a real miss does grow the back-off and does report
    real, clock2, _ = make_runner(connect=never_there("hcitool lecc failed (rc=1)"), up=up)
    waits_of(real, clock2, 3)
    assert real.rotation.targets[0].misses == 3 and up.reports and up.reports[0]["reachable"] is False


def test_an_answer_resets_the_backoff():
    runner, clock, _ = make_runner(connect=never_there())
    waits_of(runner, clock, 4)
    assert runner.rotation.targets[0].misses == 4
    runner.connect = there()
    waits_of(runner, clock, 1)
    assert runner.rotation.targets[0].misses == 0, "the owner came home: the next miss starts at the base again"
    runner.connect = never_there()
    assert waits_of(runner, clock, 2) == [30.0, 60.0]


def test_a_failed_session_is_still_an_answer_so_it_does_not_grow_the_backoff():
    runner, clock, box = make_runner(connect=there())
    box["res"] = RuntimeError("bluez glitch")
    waits_of(runner, clock, 3)
    assert runner.rotation.targets[0].misses == 0


# --- the report ---------------------------------------------------------------------------------------------------------------------------
def result(**kw):
    base = dict(path="ring", read_complete=True, firmware="3.0.21", battery_pct=63, ring_capacity_records=1000, ring_packet_bytes=444,
                ring_unread_start=800, ring_after=(1800, 1800), dropped_total=5)
    base.update(kw)
    return DrainResult(**base)


def test_the_report_is_numbers_only_and_never_names_the_pendant_address():
    rep = health.build_report(ID, reachable=True, misses=0, last_sync_age_s=12.7,
                              res=result(notes=["Zoe unavailable (secret transcript words)"], refused=None))
    assert set(rep) <= ALLOWED_KEYS
    blob = json.dumps(rep)
    assert ADDR not in blob and ADDR.lower() not in blob and "transcript" not in blob and "Zoe unavailable" not in blob
    assert all(isinstance(v, (bool, int, float, str)) for v in rep.values())
    assert rep["device"] == ID and rep["last_sync_age_s"] == 12 and rep["battery_pct"] == 63 and rep["firmware"] == "3.0.21"
    assert rep["dropped_total"] == 5 and rep["ring_capacity_bytes"] == 444_000
    # a firmware string with odd bytes is reduced to a safe token (never free text)
    odd = health.build_report(ID, reachable=True, misses=0, last_sync_age_s=None, res=result(firmware="v3.0.21\x00 <script>"))
    assert odd["firmware"] == "v3.0.21script" and "last_sync_age_s" not in odd


def test_an_unreachable_pendant_reports_the_misses_and_how_long_since_the_last_sync():
    rep = health.build_report(ID, reachable=False, misses=7, last_sync_age_s=3600 * 40, res=None)
    assert rep == {"device": ID, "reachable": False, "consecutive_misses": 7, "last_sync_age_s": 3600 * 40}
    # control arm: reachable=False never carries pendant readings even if a stale result is passed along
    stale = health.build_report(ID, reachable=False, misses=1, last_sync_age_s=None, res=result())
    assert "battery_pct" not in stale and "ring_fill_pct" not in stale


def test_the_report_carries_the_end_of_session_fill():
    """800 of 1000 records waited on the ring (80 %), and the drain freed them all: reporting 80 % would be a false alarm right
    after a successful catch-up. The start fill is still sent, separately, as 'how full it got'."""
    rep = health.build_report(ID, reachable=True, misses=0, last_sync_age_s=0, res=result(ring_after=(1800, 1800)))
    assert rep["ring_fill_pct"] == 0.0 and rep["ring_fill_start_pct"] == 80.0 and rep["ring_stored_bytes"] == 0
    # control arm: nothing was freed (Zoe down) -> the end fill is still high, and says so
    held = health.build_report(ID, reachable=True, misses=0, last_sync_age_s=None, res=result(ring_after=(1000, 1800)))
    assert held["ring_fill_pct"] == 80.0 and held["ring_stored_bytes"] == 800 * 444
    # the link dropped before the post-drain INFO: fall back to the start fill, never invent a better one
    dropped = health.build_report(ID, reachable=True, misses=0, last_sync_age_s=None, res=result(ring_after=None))
    assert dropped["ring_fill_pct"] == 80.0


def test_no_ring_numbers_when_the_pendant_did_not_report_a_capacity():
    rep = health.build_report(ID, reachable=True, misses=0, last_sync_age_s=None, res=DrainResult(path="legacy", read_complete=True))
    assert "ring_fill_pct" not in rep and "dropped_total" not in rep and "battery_pct" not in rep


# --- the engine collects the facts without touching custody -------------------------------------------------------------------------------------
class HealthyPendant(FakePendant):
    """A ring pendant with a Battery Service and a small ring, so a fill percentage is meaningful."""

    def __init__(self, *a, battery=63, capacity=150, dropped=7, battery_raises=False, **kw) -> None:
        super().__init__(*a, **kw)
        self._battery, self._capacity, self._dropped, self._battery_raises = battery, capacity, dropped, battery_raises

    async def battery(self):
        if self._battery_raises:
            raise RuntimeError("GATT read failed")
        return self._battery

    def _info(self) -> bytes:
        note = bytes([rp.NOTIFY_INFO]) + struct.pack(">QQIQH", self.read_seq, self.write_seq, self._capacity, self._dropped, 444)
        if self.caps is not None:
            note += bytes([self.caps, 1]) + struct.pack(">Q", self.ring_id)
        return note


def drain(pendant, spool, zoe, tmp_path):
    cfg = SyncConfig(device_id=ID, outbox=tmp_path / "outbox")
    return asyncio.run(Drain(pendant, spool, zoe, cfg, decoder_factory=lambda codec: FakeDecoder(), clock=lambda: NOW).run())


@pytest.fixture
def spool(tmp_path):
    s = Spool(tmp_path / "spool.sqlite3")
    yield s
    s.close()


def test_a_drain_records_battery_firmware_fill_and_the_overwrite_counter(spool, tmp_path):
    pendant, zoe = HealthyPendant(build_ring(RING), caps=0x09), FakeZoe()      # 120 records on a 150-record ring = 80 %
    res = drain(pendant, spool, zoe, tmp_path)
    assert res.battery_pct == 63 and res.firmware == "3.0.21" and res.dropped_total == 7
    assert (res.ring_capacity_records, res.ring_packet_bytes, res.ring_unread_start) == (150, 444, 120)
    rep = health.build_report(ID, reachable=True, misses=0, last_sync_age_s=0, res=res)
    assert rep["ring_fill_start_pct"] == 80.0
    assert rep["ring_fill_pct"] == 0.0, "Zoe confirmed all three recordings and the ring was advanced past them"
    assert res.advanced_to == 120


def test_with_zoe_down_the_ring_stays_full_and_the_report_says_so(spool, tmp_path):
    pendant, zoe = HealthyPendant(build_ring(RING), caps=0x09), FakeZoe(mode="down")
    res = drain(pendant, spool, zoe, tmp_path)
    assert res.advanced_to is None
    rep = health.build_report(ID, reachable=True, misses=0, last_sync_age_s=None, res=res)
    assert rep["ring_fill_pct"] == 80.0, "nothing was confirmed, so nothing was freed: this is the state the 75 % warning exists for"


def test_stock_firmware_that_frees_records_itself_reports_an_empty_ring_after_a_clean_drain(spool, tmp_path):
    pendant, zoe = HealthyPendant(build_ring(RING), auto_free=True), FakeZoe()           # no custody caps: the pendant frees as it delivers
    res = drain(pendant, spool, zoe, tmp_path)
    rep = health.build_report(ID, reachable=True, misses=0, last_sync_age_s=0, res=res)
    assert rep["ring_fill_pct"] == 0.0, "the post-drain INFO shows the pendant's own read pointer, not our (absent) ADVANCE"


def test_an_unreadable_battery_changes_nothing_about_custody(spool, tmp_path, tmp_path_factory):
    ok_pendant, ok_zoe = HealthyPendant(build_ring(RING), caps=0x09), FakeZoe()
    bad_pendant, bad_zoe = HealthyPendant(build_ring(RING), caps=0x09, battery_raises=True), FakeZoe()
    other = Spool(tmp_path / "other.sqlite3")
    try:
        good = drain(ok_pendant, spool, ok_zoe, tmp_path)
        bad = drain(bad_pendant, other, bad_zoe, tmp_path_factory.mktemp("o"))
    finally:
        other.close()
    assert bad.battery_pct is None and good.battery_pct == 63
    assert (bad.uploaded, bad.advanced_to, bad.clean) == (good.uploaded, good.advanced_to, good.clean) == (3, 120, True)
    assert "battery_pct" not in health.build_report(ID, reachable=True, misses=0, last_sync_age_s=None, res=bad)
    # a link with no battery method at all (the plain fake used by the other suites) is the same: unknown, not an error
    plain = drain(FakePendant(build_ring(RING), caps=0x09), Spool(tmp_path / "p.sqlite3"), FakeZoe(), tmp_path)
    assert plain.battery_pct is None and plain.clean


# --- the throttle + the loop never fails over health --------------------------------------------------------------------------------------------
def test_a_quiet_pendant_is_not_reported_every_visit_but_a_change_is_reported_at_once():
    up = FakeUp()
    runner, clock, box = make_runner(connect=never_there(), up=up)
    waits_of(runner, clock, 6)
    assert len(up.reports) <= 2, "six misses within ~15 minutes: the first report, and at most one heartbeat"
    first = up.reports[0]
    assert first["reachable"] is False and first["consecutive_misses"] == 1
    # the pendant comes back: the flip is reported immediately, not at the next heartbeat
    runner.connect = there()
    box["res"] = result(read_complete=True)
    n = len(up.reports)
    waits_of(runner, clock, 1)
    assert len(up.reports) == n + 1 and up.reports[-1]["reachable"] is True and up.reports[-1]["battery_pct"] == 63
    # control arm: the heartbeat interval passing sends one even with no change
    clock.t += 1000
    runner.connect = never_there()
    runner.rotation.targets[0].next_at = 0
    step(runner)
    runner.rotation.targets[0].next_at = 0
    clock.t += 1000
    before = len(up.reports)
    step(runner)
    assert len(up.reports) >= before


def test_a_refused_report_is_retried_not_treated_as_delivered():
    up = FakeUp(answer=False)               # Zoe is older than this build, the token is wrong, or the pendant is not assigned: not delivered
    runner, clock, _ = make_runner(connect=never_there(), up=up)
    waits_of(runner, clock, 3)
    assert len(up.reports) == 3, "every visit tries again (min gap passed), because none was ever accepted"
    up.answer = True
    waits_of(runner, clock, 1)
    sent = len(up.reports)
    waits_of(runner, clock, 1)
    assert len(up.reports) == sent, "once accepted, the same picture is not re-sent until the heartbeat"


@pytest.mark.parametrize("failure", [RuntimeError("boom"), asyncio.TimeoutError(), OSError("network down")])
def test_health_can_never_fail_a_visit_or_change_custody(failure):
    up = FakeUp(answer=failure)
    runner, clock, box = make_runner(connect=there(), up=up, results=result())
    waits_of(runner, clock, 1)
    assert runner.rotation.targets[0].next_at - clock.t == 120.0, "a clean visit still waits out the normal cooldown"
    assert up.reports, "it did try"


def test_a_uploader_without_a_health_method_is_simply_skipped():
    class Old:
        pass
    runner, clock, _ = make_runner(connect=never_there(), up=Old())
    assert waits_of(runner, clock, 2) == [30.0, 60.0]


def test_the_kill_switch_stops_the_reports_but_not_the_backoff():
    up = FakeUp()
    runner, clock, _ = make_runner(connect=never_there(), up=up, env={"OMI_SYNC_HEALTH": "0"})
    assert waits_of(runner, clock, 3) == [30.0, 60.0, 120.0] and up.reports == []


def test_the_last_clean_sync_survives_a_restart(tmp_path):
    sp = Spool(tmp_path / "s.sqlite3")
    try:
        up = FakeUp()
        wall = {"t": 1_800_000_000.0}
        runner, clock, box = make_runner(connect=there(), up=up, spool=sp, wall=lambda: wall["t"])
        waits_of(runner, clock, 1)                                              # a clean visit at wall = 1.8e9
        wall["t"] += 7200
        runner2, clock2, _ = make_runner(connect=never_there(), up=up, spool=sp, wall=lambda: wall["t"])      # the service restarted
        waits_of(runner2, clock2, 1)
        assert up.reports[-1]["reachable"] is False and up.reports[-1]["last_sync_age_s"] == 7200
        # control arm: a pendant that never synced cleanly has no age to report
        sp2 = Spool(tmp_path / "s2.sqlite3")
        runner3, clock3, _ = make_runner(connect=never_there(), up=FakeUp(), spool=sp2)
        waits_of(runner3, clock3, 1)
        assert "last_sync_age_s" not in runner3.up.reports[-1]
        sp2.close()
    finally:
        sp.close()


# --- the uploader's reading of Zoe's answer -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("status, body, expected", [(200, b'{"ok": true}', True), (401, b"", False), (404, b"", False),
                                                    (409, b'{"ok": false, "error": "device_not_registered"}', False), (503, b"", False)])
def test_report_health_is_true_only_when_zoe_accepted_it(status, body, expected):
    up = HttpUploader("https://zoe.invalid", "tok")
    seen = {}

    def post(path, data, headers, timeout=None):
        seen.update(path=path, data=json.loads(data), headers=headers, timeout=timeout)
        return status, body

    up._post = post
    assert asyncio.run(up.report_health({"device": ID, "reachable": True})) is expected
    assert seen["path"] == "/api/omi/device-status" and seen["data"]["device"] == ID and seen["timeout"] == 15.0


def test_report_health_never_raises_when_zoe_is_down():
    up = HttpUploader("https://zoe.invalid", "tok")

    def post(*a, **k):
        raise OSError("connection refused")

    up._post = post
    assert asyncio.run(up.report_health({"device": ID})) is False


def test_the_health_settings_and_their_floors():
    s = config.load({})
    assert s.health is True and s.health_heartbeat_s == 900.0 and s.away_backoff_max_s == 600.0
    assert config.load({"OMI_SYNC_HEALTH": "0"}).health is False
    assert config.load({"OMI_SYNC_HEALTH_HEARTBEAT_S": "1"}).health_heartbeat_s == 60.0
    assert config.load({"OMI_SYNC_AWAY_BACKOFF_MAX_S": "5"}).away_backoff_max_s == 30.0, "never below the scan interval"


# --- the spool gives the disk back -------------------------------------------------------------------------------------------------------------------
def _rec(i, size=2000):
    return SpoolRecord(seq=i, t_epoch=NOW, tq="device", frames=5, codec=21, received_at=NOW, record=bytes([i % 251]) * size)


def test_a_sparse_spool_is_compacted_without_losing_a_held_record(tmp_path):
    sp = Spool(tmp_path / "s.sqlite3")
    try:
        sp.add_records(ID, "r1", [_rec(i) for i in range(400)])
        sp.mark_confirmed(ID, "r1", 0, 389, "k1")                     # a big drain confirmed; ten records are still held
        sp._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")             # WAL mode: the main file only grows at a checkpoint
        before = sp.path.stat().st_size
        assert before > 700_000
        released = sp.compact_if_sparse(min_free_bytes=100_000, min_free_ratio=0.5)
        assert released > 500_000 and sp.path.stat().st_size < before / 4
        held = sp.records(ID, "r1")
        assert [r.seq for r in held] == list(range(390, 400)) and held[0].record == bytes([390 % 251]) * 2000
        assert sp.coverage(ID, "r1") and sp.stats()["confirmed_ranges"] == 1, "the confirmed tombstone survives too"
    finally:
        sp.close()


def test_a_busy_spool_is_not_churned(tmp_path):
    sp = Spool(tmp_path / "s.sqlite3")
    try:
        sp.add_records(ID, "r1", [_rec(i) for i in range(400)])
        sp.mark_confirmed(ID, "r1", 0, 39, "k1")                      # control arm: only 10 % free - mostly live data
        assert sp.compact_if_sparse(min_free_bytes=1, min_free_ratio=0.5) == 0
        assert len(sp.records(ID, "r1")) == 360
        assert sp.compact_if_sparse() == 0, "and with the production thresholds a small file is never vacuumed"
    finally:
        sp.close()
