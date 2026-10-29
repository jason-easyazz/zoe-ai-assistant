"""The wake KV warm's likely-speaker memo survives a zoe-data restart (2026-10-28: after a deploy the memo was empty, the warm
rendered the GUEST envelope, and the member's first turn re-prefilled 1087 tokens: brain TTFT 3.8 s instead of ~1.2 s).

It is a cache-shaping hint only: ids + a time in a state file, 12 h age cap, never a guest, removed by forget.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import routers.voice_tts as v
import warm_speaker as ws
import zoe_flue_client as zfc
from test_kv_warm_prod_path import prod_turn  # noqa: F401  (fixture: the production turn path with a stubbed sidecar)

pytestmark = pytest.mark.ci_safe

T0 = __import__("time").time() - 60.0   # "now": entries are pruned by the real clock


def _drain_background_saves():
    """Join any in-flight background save (thread "warm-speaker-save") so one test's write never lands inside the next."""
    import threading
    for t in threading.enumerate():
        if t.name == "warm-speaker-save" and t is not threading.current_thread():
            t.join(5)


@pytest.fixture
def state(monkeypatch, tmp_path):
    _drain_background_saves()
    p = tmp_path / "warm-speaker.json"
    monkeypatch.setenv("ZOE_WARM_SPEAKER_STATE", str(p))
    monkeypatch.delenv("ZOE_WARM_SPEAKER_MAX_AGE_S", raising=False)
    monkeypatch.setattr(ws, "_mem", {})                              # restored on teardown (module globals via monkeypatch)
    monkeypatch.setattr(ws, "_last_written", {})
    monkeypatch.setattr(ws, "_loaded", False)
    monkeypatch.setattr(zfc, "_LAST_TURN", dict(zfc._LAST_TURN))     # restored on teardown: _restart() clears these
    monkeypatch.setattr(zfc, "_SESSION_UID", dict(zfc._SESSION_UID))
    ws.reset()
    yield p
    _drain_background_saves()
    ws.reset()


def _restart():
    """A fresh process: the in-process memos are empty, only the state file remains."""
    ws.reset()
    zfc._LAST_TURN.clear()
    zfc._SESSION_UID.clear()


def _prewarm_member(monkeypatch, panel="p1"):
    seen = []

    async def _kv(member=""):
        seen.append(member)
        return "warmed"

    async def _none(panel_id, fallback_db=None):
        return None

    monkeypatch.setattr(v, "_brain_prewarm_on_wake_enabled", lambda: True)
    monkeypatch.setattr(v, "_get_or_create_voice_session", lambda p: "voice-panel-t-1")
    monkeypatch.setattr(v, "_VOICE_SESSIONS", {})
    monkeypatch.setattr(v, "_resolve_fresh_panel_member", _none)
    monkeypatch.setattr(zfc, "warm_kv_prefix", _kv)
    monkeypatch.setenv("ZOE_BRAIN_BACKEND", "flue")
    monkeypatch.delenv("ZOE_BRAIN_FAILOVER", raising=False)
    asyncio.run(v._prewarm_brain_for_panel(panel))
    return seen[-1]


def test_memo_round_trips_through_the_file_with_ids_only(state):
    ws.note("p1", "casey", now=T0, sync=True)
    raw = json.loads(state.read_text())
    assert raw == {"p1": ["casey", T0]}, "ids and a time, nothing else"
    assert (state.stat().st_mode & 0o777) == 0o600
    _restart()
    assert ws.get("p1", now=T0 + 60) == "casey"
    assert ws.get("p2", now=T0 + 60) == "", "another panel's speaker is not borrowed"


def test_the_production_note_path_persists_without_sync_and_does_no_io_on_the_caller(state, monkeypatch):
    """voice_tts calls note() with the default (threaded) save; the restart survives it, and the caller thread reads nothing."""
    import threading

    reads = []
    real_loaded = ws._ensure_loaded
    monkeypatch.setattr(ws, "_ensure_loaded", lambda: reads.append(threading.current_thread().name) or real_loaded())
    v._note_voice_speaker("p1", "casey")
    assert reads == [] or all(n == "warm-speaker-save" for n in reads), "note() read the file on the caller's thread"
    for t in [t for t in threading.enumerate() if t.name == "warm-speaker-save"]:
        t.join(5)
    _restart()
    assert ws.get("p1") == "casey"


def test_an_in_flight_note_save_cannot_resurrect_a_forgotten_member(state, monkeypatch):
    import threading

    ws.note("p1", "casey", now=T0, sync=True)                 # casey is on disk
    real_replace = ws.os.replace
    at_replace, go = threading.Event(), threading.Event()

    def paused(src, dst):
        if threading.current_thread().name == "warm-speaker-save":
            at_replace.set()
            go.wait(10)                                       # the background writer is parked AFTER snapshotting dana
        return real_replace(src, dst)

    monkeypatch.setattr(ws.os, "replace", paused)
    ws.note("p1", "dana", now=T0 + 1)                         # background save of dana: snapshot taken, replace pending
    assert at_replace.wait(5)
    ws.note("p1", "casey", now=T0 + 2)                        # unchanged vs the file and recent: schedules no save
    forgot = threading.Thread(target=ws.forget_user, args=("dana",))
    forgot.start()                                            # memory and _last_written no longer name dana ...
    forgot.join(0.3)
    go.set()                                                  # ... but the in-flight writer now lands dana in the file
    forgot.join(5)
    for t in [t for t in threading.enumerate() if t.name == "warm-speaker-save"]:
        t.join(5)
    assert "dana" not in state.read_text()                    # forget's rewrite is ordered AFTER the in-flight write
    _restart()
    assert ws.get("p1", now=T0 + 3) != "dana"


def test_the_cap_is_hard_and_expired_entries_are_pruned_from_the_file(state, monkeypatch):
    monkeypatch.setenv("ZOE_WARM_SPEAKER_MAX_AGE_S", "999999999")
    assert ws.max_age_s() == 43200.0, "the env can shorten the cap, never lift it"
    monkeypatch.setenv("ZOE_WARM_SPEAKER_MAX_AGE_S", "nan")
    assert ws.max_age_s() == 43200.0
    monkeypatch.delenv("ZOE_WARM_SPEAKER_MAX_AGE_S")
    import time

    ws.note("old", "casey", now=time.time() - 13 * 3600, sync=True)
    ws.note("new", "dana", now=time.time(), sync=True)      # this save prunes the expired one
    assert set(json.loads(state.read_text())) == {"new"}


def test_a_nan_timestamp_on_load_is_not_an_immortal_entry(state):
    state.write_text('{"p1": ["casey", NaN], "p2": ["dana", %r]}' % T0)
    ws.reset()
    assert ws.get("p1", now=T0) == "" and ws.get("p2", now=T0) == "dana"


def test_age_cap_degrades_a_stale_guess_to_the_guest_warm(state):
    ws.note("p1", "casey", now=T0, sync=True)
    _restart()
    assert ws.get("p1", now=T0 + 11.9 * 3600) == "casey"
    assert ws.get("p1", now=T0 + 12.1 * 3600) == "", "older than 12 h: no entry"
    assert ws.get("p1", now=T0 - 3600) == "", "stamped in the future (clock jump): no entry"


def test_a_guest_or_sentinel_is_never_recorded(state):
    for who in ("guest", "voice-guest", "", "  ", "anonymous", "voice-daemon", "guest-ab12", "GUEST-ab12"):
        ws.note("p1", who, now=T0, sync=True)
    assert not state.exists()
    v._note_voice_speaker("p1", "voice-guest")
    v._note_voice_speaker("p1", None)
    assert ws.get("p1") == ""


def test_an_unrecognised_voice_does_not_replace_the_remembered_member(state):
    """'Voice is a password': a guest turn records nothing, and the warm's envelope is not a guest turn's envelope either."""
    ws.note("p1", "casey", now=T0, sync=True)
    v._note_voice_speaker("p1", "voice-guest")
    _restart()
    assert ws.get("p1", now=T0 + 5) == "casey"


def test_forget_removes_the_member_from_memory_and_file(state):
    ws.note("p1", "casey", now=T0, sync=True)
    ws.note("p2", "dana", now=T0, sync=True)
    assert ws.forget_user("casey") == 1
    assert ws.get("p1", now=T0 + 1) == "" and ws.get("p2", now=T0 + 1) == "dana"
    assert "casey" not in state.read_text()
    _restart()
    assert ws.get("p1", now=T0 + 1) == "", "gone after a restart too"
    assert ws.forget_user("nobody") == 0


def test_revoke_member_reaches_the_memo(state):
    import voice_session

    ws.note("p1", "casey", now=T0, sync=True)
    asyncio.run(voice_session.revoke_member("casey", "voice_profile_deleted"))
    assert "casey" not in state.read_text()
    ws.note("p1", "casey", now=T0, sync=True)
    asyncio.run(voice_session.revoke_member("dana", "voice_profile_deleted"))     # control: another member's revoke keeps it
    assert "casey" in state.read_text()


def test_revoke_closes_the_sessions_before_the_memo_rewrite(state, monkeypatch):
    """Access is removed FIRST: a slow memo rewrite (disk stall, another save holding the I/O lock) must not keep a
    revoked member's voice sessions - and so their private cards - open for the length of that wait."""
    import voice_session

    order = []
    monkeypatch.setattr(voice_session, "close_member", lambda m, r: order.append("close") or [])
    monkeypatch.setattr(ws, "forget_user", lambda m: order.append("forget") or 0)
    asyncio.run(voice_session.revoke_member("casey", "consent_revoked"))
    assert order == ["close", "forget"]


def test_a_stalled_panel_publish_never_blocks_the_memo_removal(state, monkeypatch):
    """A panel socket that never answers must not delay - or, if the revoke is cancelled, skip - the memo cleanup:
    the revoked member's hint must not come back after a restart."""
    import voice_session

    ws.note("p1", "casey", now=T0, sync=True)
    stuck = {}

    async def _stalled_publish(evs):
        stuck["hit"] = True
        await asyncio.sleep(3600)

    monkeypatch.setattr(voice_session, "close_member", lambda m, r: [{"type": "voice_session_closed"}])
    monkeypatch.setattr(voice_session, "publish", _stalled_publish)

    async def _run_and_cancel():
        task = asyncio.ensure_future(voice_session.revoke_member("casey", "consent_revoked"))
        for _ in range(200):                          # until the publish is stuck
            await asyncio.sleep(0.01)
            if stuck:
                break
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        for _ in range(200):                          # the worker thread finishes on its own
            if "casey" not in state.read_text():
                break
            await asyncio.sleep(0.01)

    asyncio.run(_run_and_cancel())
    assert stuck, "the publish was reached"
    assert "casey" not in state.read_text(), "a stalled/cancelled publish left the revoked member in the memo"


def test_corrupt_or_foreign_state_is_no_memo(state):
    state.write_text("{not json")
    ws.reset()
    assert ws.get("p1", now=T0) == ""
    state.write_text(json.dumps({"p1": ["voice-guest", T0], "p2": "casey", "p3": ["dana", T0], "p4": ["anonymous", T0],
                                 "p5": ["guest-ab12", T0]}))
    ws.reset()
    assert ws.get("p1", now=T0) == "" and ws.get("p2", now=T0) == "" and ws.get("p3", now=T0) == "dana"
    assert ws.get("p4", now=T0) == "" and ws.get("p5", now=T0) == "", "the load boundary does not trust the file"


def test_unchanged_entry_is_not_rewritten_every_turn(state, monkeypatch):
    saves = []
    monkeypatch.setattr(ws, "_save", lambda sync: saves.append(sync))
    ws.note("p1", "casey", now=T0)
    ws._last_written["p1"] = ("casey", T0)    # what the save would have recorded
    ws.note("p1", "casey", now=T0 + 30)       # same member, seconds later: no write
    assert len(saves) == 1
    ws.note("p1", "dana", now=T0 + 31)        # a different member: write
    ws._last_written["p1"] = ("dana", T0 + 31)
    ws.note("p1", "dana", now=T0 + 31 + 601)  # refreshed after the rewrite interval
    assert len(saves) == 3


# -- the end-to-end restart: the real turn's key == the warm's key, from the persisted memo alone -----------------------

def test_after_a_restart_the_wake_warm_renders_the_members_envelope(monkeypatch, state, prod_turn):  # noqa: F811
    wire = prod_turn("How are you going tonight?")                 # the member's real turn (also records the in-process memos)
    ws.note("panel-1", "zed_uid", sync=True)                       # ... verified by voice on this panel (the save made synchronous)
    _restart()                                                     # deploy: every in-process memo is gone, the file remains
    assert not zfc._LAST_TURN

    assert _prewarm_member(monkeypatch, "panel-1") == "zed_uid", "the warm is asked for the member, not the guest"
    msg, key = asyncio.run(zfc._kv_warm_outbound("zed_uid"))       # no _LAST_TURN memo either: rebuilt from the member alone
    assert key == zfc.prefix_key(wire, "cardv1"), "the warm's key matches the member's real turn"
    assert "zoe-manner:" in msg


def test_negative_control_without_the_persisted_memo_the_warm_is_the_guest_one(monkeypatch, state, prod_turn):  # noqa: F811
    """What 2026-10-28 did: nothing survived the restart -> guest warm -> a different key from the member's turn."""
    wire = prod_turn("How are you going tonight?")
    _restart()                                                     # the state file was never written
    assert not state.exists()
    assert _prewarm_member(monkeypatch, "panel-1") == ""
    _msg, guest_key = asyncio.run(zfc._kv_warm_outbound(""))
    assert guest_key != zfc.prefix_key(wire, "cardv1")


def test_negative_control_an_expired_memo_is_the_guest_warm(monkeypatch, state, prod_turn):  # noqa: F811
    prod_turn("How are you going tonight?")
    ws.note("panel-1", "zed_uid", now=T0 - 13 * 3600, sync=True)               # 13 h old
    _restart()
    assert _prewarm_member(monkeypatch, "panel-1") == ""


# -- review fixes: retry after a failed write, no I/O under the caller's lock, private from the first byte, typed env --------

def test_a_failed_write_is_retried_on_the_next_turn(state, monkeypatch):
    real_replace = ws.os.replace
    monkeypatch.setattr(ws.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    ws.note("p1", "casey", now=T0, sync=True)
    assert not state.exists() and "p1" not in ws._last_written, "a failed save is not recorded as written"
    monkeypatch.setattr(ws.os, "replace", real_replace)
    ws.note("p1", "casey", now=T0 + 5, sync=True)             # same member seconds later: still saved, because nothing was
    assert json.loads(state.read_text())["p1"][0] == "casey"


def test_note_does_not_wait_for_a_slow_save(state, monkeypatch):
    import threading
    import time

    started, release = threading.Event(), threading.Event()
    real_replace = ws.os.replace

    def slow_replace(*a, **k):
        started.set()
        release.wait(5)
        return real_replace(*a, **k)

    monkeypatch.setattr(ws.os, "replace", slow_replace)
    ws.note("p1", "casey", now=T0)                            # threaded save, now stuck mid-write
    assert started.wait(5)
    t0 = time.monotonic()
    ws.note("p2", "dana", now=T0)                             # must not block behind the disk write
    assert ws.get("p2", now=T0 + 1) == "dana"
    assert time.monotonic() - t0 < 1.0
    release.set()
    for t in [t for t in threading.enumerate() if t.name == "warm-speaker-save"]:
        t.join(5)


def test_the_temp_file_is_private_before_any_byte_is_written(state, monkeypatch):
    import os

    old = os.umask(0o022)
    modes = []
    real_replace = ws.os.replace
    try:
        monkeypatch.setattr(ws.os, "replace", lambda src, dst: modes.append(os.stat(src).st_mode & 0o777) or real_replace(src, dst))
        real_open = ws.os.open
        opened = []
        monkeypatch.setattr(ws.os, "open", lambda path, flags, mode=0o777: opened.append(mode) or real_open(path, flags, mode))
        ws.note("p1", "casey", now=T0, sync=True)
    finally:
        os.umask(old)
    assert opened == [0o600], "created with 0600 up front, not chmod-ed after the write"
    assert modes == [0o600]


def test_a_bad_age_setting_warns_and_uses_the_cap(monkeypatch, caplog):
    monkeypatch.setenv("ZOE_WARM_SPEAKER_MAX_AGE_S", "twelve")
    caplog.set_level("WARNING")
    import typed_env

    monkeypatch.setattr(typed_env, "_warned", set())
    assert ws.max_age_s() == 43200.0
    assert "ZOE_WARM_SPEAKER_MAX_AGE_S" in caplog.text, "the typo is reported, not silent"
    monkeypatch.setenv("ZOE_WARM_SPEAKER_MAX_AGE_S", "3600")
    assert ws.max_age_s() == 3600.0


def test_note_does_not_wait_for_a_slow_first_load(state, monkeypatch):
    import pathlib
    import threading
    import time

    state.write_text(json.dumps({"p9": ["dana", T0]}))
    started, release = threading.Event(), threading.Event()
    real_read = pathlib.Path.read_text

    def slow_read(self, *a, **k):
        if self == state:
            started.set()
            release.wait(5)
        return real_read(self, *a, **k)

    monkeypatch.setattr(pathlib.Path, "read_text", slow_read)
    monkeypatch.setattr(ws, "_save", lambda sync: None)
    t = threading.Thread(target=ws.get, args=("p9",))
    t.start()                                                 # the lazy load, stuck mid-read
    assert started.wait(5)
    t0 = time.monotonic()
    ws.note("p1", "casey", now=T0)
    assert time.monotonic() - t0 < 1.0, "note() waited for the file read"
    release.set()
    t.join(5)
    assert ws.get("p9", now=T0 + 1) == "dana" and ws.get("p1", now=T0 + 1) == "casey"


def test_a_failed_refresh_after_a_good_save_is_retried(state, monkeypatch):
    ws.note("p1", "casey", now=T0, sync=True)                 # T0 reached disk
    real_replace = ws.os.replace
    real_save = ws._save
    monkeypatch.setattr(ws, "_save", lambda sync: None)
    ws.note("p1", "casey", now=T0 + 100)                      # a queued refresh: memory has T0+100, disk still T0
    monkeypatch.setattr(ws.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    ws._persist()                                             # ... and that queued write fails
    monkeypatch.setattr(ws.os, "replace", real_replace)
    monkeypatch.setattr(ws, "_save", real_save)
    ws.note("p1", "casey", now=T0 + 110, sync=True)           # <600 s after the last GOOD write, but nothing newer is on disk: save
    assert json.loads(state.read_text())["p1"][1] == T0 + 110


def test_a_non_finite_age_setting_warns_and_uses_the_cap(monkeypatch, caplog):
    caplog.set_level("WARNING")
    for bad in ("nan", "inf"):
        monkeypatch.setenv("ZOE_WARM_SPEAKER_MAX_AGE_S", bad)
        caplog.clear()
        assert ws.max_age_s() == 43200.0
        assert "not finite" in caplog.text


def test_a_failed_forget_stays_pending_and_is_retried(state, monkeypatch):
    ws.note("p1", "casey", now=T0, sync=True)
    real_replace = ws.os.replace
    monkeypatch.setattr(ws.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    ws.forget_user("casey")                                   # the write fails: casey is still in the file
    assert "casey" in state.read_text()
    monkeypatch.setattr(ws.os, "replace", real_replace)
    ws.forget_user("casey")                                   # the retry finds nothing in memory but must still clean the file
    assert "casey" not in state.read_text()
    _restart()
    assert ws.get("p1", now=T0 + 1) == ""


def test_a_failed_speaker_change_does_not_hide_the_old_member_from_forget(state, monkeypatch):
    ws.note("p1", "casey", now=T0, sync=True)                 # casey is on disk
    real_replace = ws.os.replace
    monkeypatch.setattr(ws.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    ws.note("p1", "dana", now=T0 + 5, sync=True)              # the change to dana fails: the file still says casey
    assert "casey" in state.read_text()
    monkeypatch.setattr(ws.os, "replace", real_replace)
    ws.forget_user("casey")                                   # memory holds dana, but casey is still on disk: must rewrite
    assert "casey" not in state.read_text()
    _restart()
    assert ws.get("p1", now=T0 + 6) != "casey"


def test_a_speaker_change_saved_while_another_save_is_in_flight_does_not_leave_the_wrong_speaker(state, monkeypatch):
    import threading

    ws.note("p1", "casey", now=T0, sync=True)                 # casey on disk
    real_replace = ws.os.replace
    at_replace, go = threading.Event(), threading.Event()
    first = []

    def paused(src, dst):
        if threading.current_thread().name == "warm-speaker-save" and not first:
            first.append(1)
            at_replace.set()
            go.wait(10)                                       # dana's writer parked after its snapshot
        return real_replace(src, dst)

    monkeypatch.setattr(ws.os, "replace", paused)
    ws.note("p1", "dana", now=T0 + 1)
    assert at_replace.wait(5)
    ws.note("p1", "casey", now=T0 + 2)                        # back to casey; _last_written still says casey: no save scheduled
    go.set()                                                  # dana's stale snapshot lands
    for _ in range(50):
        for t in [t for t in threading.enumerate() if t.name == "warm-speaker-save"]:
            t.join(5)
        if json.loads(state.read_text())["p1"][0] == "casey":
            break
    assert json.loads(state.read_text())["p1"][0] == "casey"
    _restart()
    assert ws.get("p1", now=T0 + 3) == "casey"
