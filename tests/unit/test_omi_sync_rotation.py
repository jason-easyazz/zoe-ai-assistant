"""omi-sync with SEVERAL pendants on one panel Pi: the config, the round-robin, the shared radio lock, the slow retry for an unowned pendant.

Owner requirement (2026-10-13): "link a certain omi to a certain user, but the sync should sync no matter who is logged on, as long as in
range of the pi, and only sync to the user its registered for". The Pi half of that is here: it visits every configured pendant in turn,
holds the one radio only per visit (so Settings > Bluetooth can always get in between), keeps each pendant's spool/custody separate (the
spool is keyed by the per-pendant id), and when Zoe says a pendant is not assigned yet (409 ``device_not_registered``) it keeps the audio
and asks again slowly. Nothing here knows who owns what or who is signed in.

Source mutations worth re-running by Edit (each turns a named test red): ``Rotation.next_due`` always returning ``targets[0]`` ->
``test_every_pendant_is_visited_in_turn``; ``Runner.visit`` ignoring ``res.not_registered`` -> ``test_an_unowned_pendant_is_retried_slowly``;
``classify`` mapping 409 to REJECTED unconditionally -> ``test_device_not_registered_is_not_a_rejection``; dropping the lock from
``ble_link.connect`` -> ``test_a_busy_radio_skips_every_pendant_without_touching_one`` and the radio-lock suite.
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

from omi_sync import ble_link, config  # noqa: E402
from omi_sync.engine import DrainResult  # noqa: E402
from omi_sync.radio_lock import RadioLock as SyncLock  # noqa: E402
from omi_sync.runner import Rotation, Runner  # noqa: E402
from omi_sync.uploader import NOT_REGISTERED_CODE, Outcome, classify, device_hash  # noqa: E402
from panel_bt.radio_lock import RadioLock as AgentLock  # noqa: E402

A, B, C = "AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02", "AA:BB:CC:DD:EE:03"
ID_A, ID_B = device_hash(A), device_hash(B)


# --- config ------------------------------------------------------------------------------------------------------------------------
def test_several_addresses_are_read_in_order_deduplicated_and_the_old_single_var_is_the_fallback():
    s = config.load({"OMI_PENDANT_ADDRESSES": f"{A}, {B};{A.lower()}  {C}"})
    assert s.addresses == (A, B, C) and s.address == A
    only_old = config.load({"OMI_PENDANT_ADDRESS": B})
    assert only_old.addresses == (B,) and only_old.address == B
    both = config.load({"OMI_PENDANT_ADDRESSES": A, "OMI_PENDANT_ADDRESS": B})
    assert both.addresses == (A,), "the list wins; the old single var is only a fallback"
    none = config.load({})
    assert none.addresses == () and none.address == ""


def test_the_unregistered_retry_is_slow_by_default_and_cannot_be_set_to_a_hammer():
    assert config.load({}).unregistered_retry_s == 900.0
    assert config.load({"OMI_SYNC_UNREGISTERED_RETRY_S": "1"}).unregistered_retry_s == 60.0
    assert config.load({"OMI_SYNC_UNREGISTERED_RETRY_S": "3600"}).unregistered_retry_s == 3600.0


# --- the uploader's reading of Zoe's refusal -------------------------------------------------------------------------------------------
def test_device_not_registered_is_not_a_rejection():
    """409 is also 'this key holds different bytes' (a permanent per-recording refusal). The unowned-pendant 409 is a DIFFERENT answer:
    nothing is wrong with the audio, it just has no owner yet - so it must be kept and retried, not logged as rejected-forever."""
    body = json.dumps({"ok": False, "error": NOT_REGISTERED_CODE}).encode()
    res = classify(409, body, "0" * 64)
    assert res.outcome is Outcome.NOT_REGISTERED
    other = classify(409, json.dumps({"ok": False, "error": "key already stored with different content"}).encode(), "0" * 64)
    assert other.outcome is Outcome.REJECTED, "an ordinary 409 stays a permanent per-recording refusal"
    assert classify(409, b"not json", "0" * 64).outcome is Outcome.REJECTED
    # the three answers that must never free anything
    for status, b in ((409, body), (503, b""), (404, b"")):
        assert classify(status, b, "0" * 64).outcome is not Outcome.CONFIRMED


# --- the rotation -------------------------------------------------------------------------------------------------------------------
class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def make_runner(addresses, *, connect, results=None, clock=None, env=None):
    s = config.load({"OMI_PENDANT_ADDRESSES": ",".join(addresses), "OMI_SYNC_COOLDOWN_S": "120", "OMI_SYNC_SCAN_INTERVAL_S": "30",
                     "OMI_SYNC_UNREGISTERED_RETRY_S": "900", **(env or {})})
    drained: list[str] = []
    results = results if results is not None else {}

    class FakeDrain:
        def __init__(self, link, cfg) -> None:
            self.cfg = cfg

        async def run(self) -> DrainResult:
            drained.append(self.cfg.device_id)
            return results.get(self.cfg.device_id, DrainResult(path="ring", read_complete=True))

    clk = clock or Clock()
    return Runner(s, None, None, connect=connect, drain_factory=lambda link, cfg: FakeDrain(link, cfg), clock=clk), drained, clk


def lock_taking_connect(visits):
    """What ``ble_link.connect`` does about the radio, with a fake pendant behind it: take the shared lock or say the radio is busy."""
    import contextlib

    @contextlib.asynccontextmanager
    async def connect(address, **kw):
        radio = SyncLock()
        if not radio.try_acquire():
            raise ble_link.ConnectError("radio busy")
        visits.append(address)
        try:
            yield object()
        finally:
            radio.release()
    return connect


@pytest.fixture(autouse=True)
def lock_path(tmp_path, monkeypatch):
    p = tmp_path / "radio.lock"
    monkeypatch.setenv("ZOE_BLE_RADIO_LOCK", str(p))
    return p


def step(runner):
    return asyncio.run(runner.step())


def test_every_pendant_is_visited_in_turn(lock_path):
    visits: list[str] = []
    runner, drained, clock = make_runner([A, B], connect=lock_taking_connect(visits))
    assert step(runner) == 0.0 and step(runner) == 0.0
    assert visits == [A, B] and drained == [ID_A, ID_B], "each pendant gets its own session, under its own id"
    assert step(runner) == 30.0, "both just synced: nothing is due, so sleep (never longer than the scan interval)"
    clock.t += 121                       # the cooldown passed for both: round-robin continues AFTER the last visited
    assert step(runner) == 0.0 and step(runner) == 0.0
    assert visits == [A, B, A, B]


def test_a_busy_radio_skips_every_pendant_without_touching_one(lock_path, caplog):
    """The Settings > Bluetooth agent holds the radio (a scan, an open link, a FIRMWARE UPDATE): a sync must not connect to ANY pendant."""
    agent = AgentLock(lock_path, "panel-bluetooth")
    assert agent.try_acquire()
    runner, drained, clock = make_runner([A, B], connect=ble_link.connect)         # the REAL connect: its lock check is what is under test
    try:
        with caplog.at_level("DEBUG", logger="omi_sync"):
            assert step(runner) == 0.0 and step(runner) == 0.0
        assert drained == [], "no pendant was connected to while the agent held the radio"
        assert caplog.text.count("radio busy") == 2, "each visit was stopped by the radio lock itself (not by some other failure)"
    finally:
        agent.release()
    assert all(t.next_at == clock.t + 30.0 for t in runner.rotation.targets), "busy = 'not reachable': retry on the short timer"
    clock.t += 31
    visits: list[str] = []
    runner.connect = lock_taking_connect(visits)
    step(runner)
    step(runner)
    assert visits == [A, B], "the moment the agent lets go, both pendants are synced"


def test_the_radio_is_held_per_visit_never_between_pendants(lock_path):
    seen = {}

    def connect(address, **kw):
        import contextlib

        @contextlib.asynccontextmanager
        async def cm():
            radio = SyncLock()
            assert radio.try_acquire()
            seen[address] = AgentLock(lock_path, "panel-bluetooth").try_acquire()       # while we are connected the agent must be refused
            try:
                yield object()
            finally:
                radio.release()
        return cm()

    runner, drained, clock = make_runner([A, B], connect=connect)
    step(runner)
    other = AgentLock(lock_path, "panel-bluetooth")
    assert other.try_acquire(), "between two visits the radio is free: the Bluetooth agent can always get in"
    other.release()
    step(runner)
    assert seen == {A: False, B: False}


def test_an_unowned_pendant_is_retried_slowly_while_its_neighbour_keeps_its_own_cadence(lock_path):
    visits: list[str] = []
    unowned = DrainResult(path="ring", read_complete=True, not_registered=True, pending=3)
    runner, drained, clock = make_runner([A, B], connect=lock_taking_connect(visits), results={ID_A: unowned})
    step(runner)
    step(runner)
    ta, tb = runner.rotation.targets
    assert ta.next_at == clock.t + 900.0, "not assigned yet: ask again in 15 minutes, not every 30 s"
    assert tb.next_at == clock.t + 120.0, "the other pendant keeps the normal cooldown - one pendant never holds up another"
    clock.t += 130
    step(runner)
    assert visits == [A, B, B], "B is due again, A is not"
    clock.t += 800
    step(runner)
    step(runner)
    assert visits.count(A) == 2


def test_a_failed_drain_retries_soon_and_a_clean_one_waits_out_the_cooldown(lock_path):
    dropped = DrainResult(path="ring", dropped=True)
    runner, _, clock = make_runner([A, B], connect=lock_taking_connect([]), results={ID_A: dropped})
    step(runner)
    step(runner)
    ta, tb = runner.rotation.targets
    assert ta.next_at - clock.t == 30.0 and tb.next_at - clock.t == 120.0


def test_one_pendant_behaves_as_before(lock_path):
    visits: list[str] = []
    runner, drained, clock = make_runner([A], connect=lock_taking_connect(visits))
    assert step(runner) == 0.0
    assert step(runner) == 30.0 and visits == [A]
    clock.t += 121
    step(runner)
    assert visits == [A, A]


def test_the_rotation_is_pure_and_continues_after_the_last_visited():
    r = Rotation([A, B, C])
    assert [r.next_due(0).address for _ in range(3)] == [A, B, C]
    for t in r.targets:
        r.done(t, 100.0, 0.0)
    assert r.next_due(50.0) is None and r.sleep_for(50.0, 30.0) == 30.0 and r.sleep_for(90.0, 30.0) == 10.0
    assert r.next_due(100.0).address == A
    assert Rotation([]).next_due(0) is None and Rotation([]).sleep_for(0, 30.0) == 30.0
