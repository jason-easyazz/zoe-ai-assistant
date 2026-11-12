"""A member links (and unlinks) their OWN Omi pendant at the panel, and their pendant settings can only NARROW the operator's flags.
Design: ``docs/knowledge/omi-linking.md``. Modules: ``omi_linking.py`` (the pairing window), ``omi_devices.self_assign/self_unassign``,
``omi_member_settings.py`` (migration 0077), the member routes in ``routers/omi_recordings.py`` / ``routers/omi_voices.py``.

Owner's words: "I can link my Omi to Zoe using her interface and my voice. I never run code or edit a file." / "My Omi settings are
listed in her settings."

Negative controls - remove the guard by Edit and the named test turns red:
  ``assign(claim_only=True)`` refusal of an owned pendant                -> test_a_pendant_someone_else_owns_is_never_claimed
  ``self_assign``'s ``window_saw`` check                                 -> test_a_pendant_this_panel_did_not_hear_in_my_window_is_refused
  ``window_saw``'s user / deadline / grace checks                        -> test_a_pendant_this_panel_did_not_hear_in_my_window_is_refused
  ``NEAR_RSSI`` filter in ``drive``                                       -> test_a_pendant_across_the_room_is_not_linked
  ``member_only`` (guest / device token)                                  -> test_a_guest_or_a_panel_token_gets_nothing
  ``manner_block.is_adult`` in ``self_assign`` / ``start``               -> test_a_child_cannot_self_link_and_is_still_never_tagged
  ``link_my_pendant``'s fresh-PIN check                                  -> test_the_touch_link_needs_this_members_pin_at_that_panel
  ``_Link`` ``extra=forbid``                                              -> test_the_member_route_can_never_name_someone_else
  ``unassign(only_owner=...)`` / ``self_unassign``'s owner check          -> test_unlink_is_my_own_pendant_only
  ``pending_audio_known`` refusal                                         -> test_unlink_is_refused_while_audio_is_known_to_be_pending
  ``effective`` using ``or`` / ``clamp_days`` using ``max``               -> test_a_member_can_narrow_but_never_widen_an_operator_flag
  the ``sync_paused`` check in ``upload_recording``                       -> test_pausing_holds_my_uploads_on_the_pi
  ``retention_days_for`` in ``store_recording``                           -> test_my_shorter_keep_audio_is_used_and_a_longer_one_is_capped
  ``day_review_on`` in ``members_with_recordings``                        -> test_turning_off_my_day_review_skips_only_me
  ``available()``'s ``devices.enabled`` check                             -> test_with_linking_off_settings_say_ask_the_admin
  0077 defaults                                                           -> test_the_migration_defaults_reproduce_todays_behaviour
  ``omi_voice_settings`` in ``omi_voices.forget_user``                    -> test_erasing_a_member_removes_their_pendant_settings

Synthetic data only. Real SQLite via migrations, the real Bluetooth broker with a fake panel agent; no hardware, no network.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import asyncio
from datetime import datetime, timedelta, timezone

import manner_block
import omi_day_review
import omi_devices
import omi_linking
import omi_member_settings as ms
import omi_recordings as omi
from test_omi_device_owners import ADM, ANN, BEN, DEV_A, DEV_B, DEV_C, GST, PANEL_AS_BEN, env, register, upload  # noqa: F401
from test_omi_recordings import _migrate

ADULTS = {"ann", "ben", "adm"}
KID = {"user_id": "kid", "role": "member", "username": "kid"}


class Clock:
    def __init__(self) -> None:
        self.t = 5000.0

    def __call__(self) -> float:
        return self.t


class Panel:
    """The panel's Bluetooth agent, faked at the broker's wire: it answers each queued job with the radio's ``heard`` list."""

    def __init__(self, broker, panel_id: str, clock: Clock, heard: list) -> None:
        self.broker, self.pid, self.clock, self.heard = broker, panel_id, clock, heard
        self.ops: list = []
        broker.heartbeat(panel_id, {"agent": {"version": "t"}})

    async def sleep(self, _s: float) -> None:
        st = self.broker.panels[self.pid]
        st.last_seen = self.clock()
        while st.queue:
            job = st.queue.popleft()
            job.state = "sent"
            self.ops.append((job.op, job.device_id))
            result = {"devices": list(self.heard)} if job.op == "scan" else {"connected": job.op == "connect"}
            self.broker.report(self.pid, {"job_id": job.id, "state": "succeeded", "result": result})
        self.clock.t += 1.0


def omi_dev(dev: str, rssi: int, driver: str = "omi") -> dict:
    return {"id": dev, "name": "Omi", "rssi": rssi, "driver": driver}


@pytest.fixture
def link(env, monkeypatch):
    import bluetooth_broker as bt
    _migrate(env.engine, "0077_omi_member_settings.py")
    env.db.conn.execute("CREATE TABLE omi_device_health (device_id TEXT PRIMARY KEY, user_id TEXT, ring_stored_bytes BIGINT, "
                        "last_sync_at TEXT)")
    env.db.conn.execute("INSERT INTO users VALUES ('kid', 'Kid', 'member')")
    env.db.conn.commit()
    monkeypatch.setenv("ZOE_BLUETOOTH_SETTINGS", "1")
    monkeypatch.setenv("ZOE_BLUETOOTH_AUDIT_PATH", str(env.tmp / "bt-audit.jsonl"))

    async def is_adult(uid, db=None):
        return uid in ADULTS
    monkeypatch.setattr(manner_block, "is_adult", is_adult)
    monkeypatch.setattr(manner_block, "eligible", is_adult)
    said: list = []
    screens: list = []

    async def speak(panel_id, user_id, text):
        said.append((panel_id, user_id, text))

    async def screen(panel_id, state, line, phrase="", index=0):
        screens.append((panel_id, state, phrase or line))
    monkeypatch.setattr(omi_linking, "_speak", speak)
    monkeypatch.setattr(omi_linking, "_screen", screen)
    omi_linking.reset()
    clock = Clock()
    broker = bt.Broker(clock=clock)
    env.clock, env.broker, env.said, env.screens = clock, broker, said, screens
    yield env
    omi_linking.reset()


def run(coro):
    return asyncio.run(coro)


def open_and_drive(env, member, heard, panel_id="p1", notice_told=True):
    panel = Panel(env.broker, panel_id, env.clock, heard)

    async def go():
        w = await omi_linking.start(member, panel_id, notice_told=notice_told, broker=env.broker, sleep=panel.sleep, clock=env.clock,
                                    spawn=False)
        return await omi_linking.drive(w, broker=env.broker, sleep=panel.sleep, clock=env.clock), panel
    return run(go())


def owner(env, dev):
    return run(omi_devices.owner_of(dev))


# --- linking ------------------------------------------------------------------------------------------------------------------------
def test_a_member_links_their_own_pendant_held_at_the_panel(link):
    w, panel = open_and_drive(link, ANN, [omi_dev(DEV_A, -48), omi_dev(DEV_B, -50, driver="generic")])
    assert w.state == "linked" and w.device_id == DEV_A and w.remembered is True
    assert owner(link, DEV_A) == "ann"
    assert [op for op, _ in panel.ops] == ["scan", "connect", "disconnect"], "connected once so the panel remembers it, then let go"
    assert link.said[-1][2] == omi_linking.say("linked") and link.said[-1][:2] == ("p1", "ann")
    assert any(s[1] == "done" for s in link.screens), "progress went through the existing enrolment overlay frames"
    acts = [a for a in link.audit() if a.get("action") == "self_link"]
    assert acts and acts[-1]["actor"] == "ann" and acts[-1]["to_user"] == "ann"
    assert run(ms.read_uid("ann"))["notice_ack_at"], "the one-sentence notice was acknowledged at link time"


def test_a_pendant_across_the_room_is_not_linked(link):
    w, _ = open_and_drive(link, ANN, [omi_dev(DEV_A, -82)])
    assert w.state == "failed" and w.reason == "not_found" and owner(link, DEV_A) == ""
    assert link.said[-1][2] == omi_linking.say("not_found")


def test_two_free_pendants_next_to_the_panel_are_never_guessed(link):
    w, _ = open_and_drive(link, ANN, [omi_dev(DEV_A, -48), omi_dev(DEV_B, -50)])
    assert w.state == "failed" and owner(link, DEV_A) == "" and owner(link, DEV_B) == ""
    assert omi_linking.say("crowded") in [s[2] for s in link.said]


def test_a_pendant_someone_else_owns_is_never_claimed(link):
    register(link, DEV_A, "ben")
    w, _ = open_and_drive(link, ANN, [omi_dev(DEV_A, -45)])
    assert w.state == "failed" and w.reason == "owned_by_someone_else" and owner(link, DEV_A) == "ben"
    # even with a window that saw it (the race: owned between the scan and the claim), the claim refuses inside the device lock
    omi_linking._WINDOWS["p1"] = omi_linking.Window("p1", "ann", "member", link.clock(), link.clock() + 60, {DEV_A: link.clock()},
                                                    clock=link.clock)
    with pytest.raises(omi_devices.DeviceError) as ei:
        run(omi_devices.self_assign(DEV_A, ANN, panel_id="p1"))
    assert ei.value.code == "owned_by_someone_else" and owner(link, DEV_A) == "ben"


def test_a_pendant_this_panel_did_not_hear_in_my_window_is_refused(link):
    c = link.clock
    with pytest.raises(omi_devices.DeviceError) as ei:
        run(omi_devices.self_assign(DEV_A, ANN, panel_id="p1"))                   # no window at all
    assert ei.value.code == "not_seen_here"
    omi_linking._WINDOWS["p1"] = omi_linking.Window("p1", "ben", "member", c(), c() + 60, {DEV_A: c()}, clock=c)
    with pytest.raises(omi_devices.DeviceError):
        run(omi_devices.self_assign(DEV_A, ANN, panel_id="p1"))                   # Ben's window, not mine
    with pytest.raises(omi_devices.DeviceError):
        run(omi_devices.self_assign(DEV_A, BEN, panel_id="p2"))                   # a different panel
    with pytest.raises(omi_devices.DeviceError):
        run(omi_devices.self_assign(DEV_B, BEN, panel_id="p1"))                   # a pendant the window never heard
    c.t += 61
    with pytest.raises(omi_devices.DeviceError):
        run(omi_devices.self_assign(DEV_A, BEN, panel_id="p1"))                   # the window has closed
    assert owner(link, DEV_A) == "" and owner(link, DEV_B) == ""
    # control: inside Ben's own open window, on this panel, the pendant it heard is his
    omi_linking._WINDOWS["p1"] = omi_linking.Window("p1", "ben", "member", c(), c() + 60, {DEV_A: c()}, clock=c)
    run(omi_devices.self_assign(DEV_A, BEN, panel_id="p1"))
    assert owner(link, DEV_A) == "ben"


def test_a_guest_or_a_panel_token_gets_nothing(link):
    for who in (GST, PANEL_AS_BEN, {"user_id": "", "role": "member"}):
        with pytest.raises(omi_linking.LinkError):
            run(omi_linking.start(who, "p1", notice_told=True, broker=link.broker, spawn=False))
        with pytest.raises(omi_devices.DeviceError):
            run(omi_devices.self_assign(DEV_A, who, panel_id="p1"))
    link.as_(GST)
    assert link.client.get("/api/omi/my-pendant").status_code in (401, 403)


def test_a_child_cannot_self_link_and_is_still_never_tagged(link, monkeypatch):
    Panel(link.broker, "p1", link.clock, [omi_dev(DEV_C, -45)])
    with pytest.raises(omi_linking.LinkError) as ei:
        run(omi_linking.start(KID, "p1", notice_told=True, broker=link.broker, spawn=False))
    assert ei.value.code == "adult_only"
    c = link.clock
    omi_linking._WINDOWS["p1"] = omi_linking.Window("p1", "kid", "member", c(), c() + 60, {DEV_C: c()}, clock=c)
    with pytest.raises(omi_devices.DeviceError) as e2:
        run(omi_devices.self_assign(DEV_C, KID, panel_id="p1"))
    assert e2.value.code == "adult_only" and owner(link, DEV_C) == ""
    # the admin's assign still works for a child, and the child's recordings are still never voice-tagged (D14), whatever the
    # member setting says
    register(link, DEV_C, "kid")
    monkeypatch.setenv("ZOE_OMI_SPEAKER_TAG", "on")
    run(_write("kid", speaker_tag_enabled=1))
    assert run(omi._diarise("kid", "/nonexistent.wav", None)) == (None, "unverified", None)


async def _write(uid, **kw):
    import db_compat
    async with db_compat.get_compat_db() as db:
        return await ms.write(db, uid, **kw)


def test_the_touch_link_needs_this_members_pin_at_that_panel(link, monkeypatch):
    import biometric_scope
    Panel(link.broker, "p1", link.clock, [])
    signed_in = {"p1": None}

    async def fresh(pid):
        return signed_in.get(pid)
    monkeypatch.setattr(biometric_scope, "_fresh_panel_member", fresh)
    link.as_(ANN)
    r = link.client.post("/api/omi/my-pendant/link", json={"panel_id": "p1", "notice_ack": True})
    assert r.status_code == 403, "a desktop session, or nobody signed in at the panel, does not open a window"
    signed_in["p1"] = "ben"
    assert link.client.post("/api/omi/my-pendant/link", json={"panel_id": "p1", "notice_ack": True}).status_code == 403
    signed_in["p1"] = "ann"
    import bluetooth_broker

    async def no_driver(w, **kw):
        return w
    monkeypatch.setattr(omi_linking, "drive", no_driver)               # the window's driver is tested on its own above
    monkeypatch.setattr(bluetooth_broker, "BROKER", link.broker)
    r = link.client.post("/api/omi/my-pendant/link", json={"panel_id": "p1", "notice_ack": False})
    assert r.status_code == 428 and omi_linking.notice() in r.text, "the first link must carry the notice acknowledgement"
    r = link.client.post("/api/omi/my-pendant/link", json={"panel_id": "p1", "notice_ack": True})
    assert r.status_code == 200 and r.json()["window"]["state"] == "searching"


def test_the_member_route_can_never_name_someone_else(link):
    link.as_(ANN)
    r = link.client.post("/api/omi/my-pendant/link", json={"panel_id": "p1", "notice_ack": True, "user_id": "ben"})
    assert r.status_code == 422
    assert link.put(DEV_A, "ann").status_code == 403, "the admin assign route stays admin-only for a member"


# --- unlinking ----------------------------------------------------------------------------------------------------------------------
def test_unlink_is_my_own_pendant_only(link):
    register(link, DEV_A, "ann")
    register(link, DEV_B, "ben")
    link.as_(ANN)
    assert link.client.delete(f"/api/omi/my-pendant/{DEV_B}").status_code == 404 and owner(link, DEV_B) == "ben"
    r = link.client.delete(f"/api/omi/my-pendant/{DEV_A}")
    assert r.status_code == 200 and owner(link, DEV_A) == ""
    assert r.json()["note"] == omi_linking.say("unlink_done_warn"), "nobody knows what is still on it: the member is warned"
    with pytest.raises(omi_devices.DeviceError):
        run(omi_devices.unassign(DEV_B, actor={"user_id": "ann"}, only_owner="ann"))
    assert owner(link, DEV_B) == "ben"


def test_unlink_is_refused_while_audio_is_known_to_be_pending(link):
    register(link, DEV_A, "ann")
    link.db.conn.execute("INSERT INTO omi_device_health VALUES (?, 'ann', 4096, NULL)", (DEV_A,))
    link.db.conn.commit()
    link.as_(ANN)
    r = link.client.delete(f"/api/omi/my-pendant/{DEV_A}")
    assert r.status_code == 409 and owner(link, DEV_A) == "ann"
    link.db.conn.execute("UPDATE omi_device_health SET ring_stored_bytes = 0")
    link.db.conn.commit()
    r = link.client.delete(f"/api/omi/my-pendant/{DEV_A}")
    assert r.status_code == 200 and r.json()["pending_audio"] is False and owner(link, DEV_A) == ""


# --- settings: narrow, never widen --------------------------------------------------------------------------------------------------
def test_a_member_can_narrow_but_never_widen_an_operator_flag():
    stored = dict(ms.DEFAULTS)
    on = {"sync": True, "speaker_tag": True, "learn_voices": True, "keep_audio_days_max": 7, "day_review": True}
    off = {"sync": False, "speaker_tag": False, "learn_voices": False, "keep_audio_days_max": 7, "day_review": False}
    assert ms.effective(stored, on) == {"sync": True, "speaker_tag": True, "learn_voices": True, "keep_audio_days": 7, "day_review": True}
    assert ms.effective({**stored, "keep_audio_days": 30}, off) == {"sync": False, "speaker_tag": False, "learn_voices": False,
                                                                     "keep_audio_days": 7, "day_review": False}
    narrowed = {**stored, "sync_paused": 1, "speaker_tag_enabled": 0, "learning_enabled": 0, "keep_audio_days": 3, "day_review_enabled": 0}
    assert ms.effective(narrowed, on) == {"sync": False, "speaker_tag": False, "learn_voices": False, "keep_audio_days": 3,
                                          "day_review": False}
    assert ms.clamp_days(0, 7) == 1 and ms.clamp_days(None, 7) == 7 and ms.clamp_days(99, 7) == 7


def test_the_settings_route_round_trips_and_reports_the_ceiling(link, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_AUDIO_RETENTION_DAYS", "7")
    link.as_(ANN)
    r = link.client.put("/api/omi/voices/settings", json={"keep_audio_days": 30, "speaker_tag_enabled": True, "day_review_enabled": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["keep_audio_days"] == 30 and body["effective"]["keep_audio_days"] == 7, "stored as asked, capped when used"
    assert body["effective"]["speaker_tag"] is False, "the operator's ZOE_OMI_SPEAKER_TAG is off: a member's 'on' widens nothing"
    assert body["effective"]["day_review"] is False and body["day_review_enabled"] is False
    assert link.client.put("/api/omi/voices/settings", json={"keep_audio_default": True}).json()["keep_audio_days"] is None
    assert link.client.put("/api/omi/voices/settings", json={"owner": "ben"}).status_code == 422
    assert link.client.get("/api/omi/voices/settings").json()["operator"]["keep_audio_days_max"] == 7


def test_pausing_holds_my_uploads_on_the_pi(link):
    register(link, DEV_A, "ann")
    run(_write("ann", sync_paused=1))
    r = upload(link, DEV_A, key="p1.0-9")
    assert r.status_code == 409 and r.json()["error"] == "device_not_registered" and r.json()["held"] == "paused"
    assert link.recs() == [], "nothing stored; the Pi keeps the audio (the same answer as an unassigned pendant)"
    run(_write("ann", sync_paused=0))
    assert upload(link, DEV_A, key="p1.0-9").status_code == 200


def test_my_shorter_keep_audio_is_used_and_a_longer_one_is_capped(link, monkeypatch):
    monkeypatch.setenv("ZOE_OMI_AUDIO_RETENTION_DAYS", "7")
    register(link, DEV_A, "ann")
    run(_write("ann", keep_audio_days=2))
    assert upload(link, DEV_A, key="k1.0-9").status_code == 200
    run(_write("ann", keep_audio_days=40))
    assert upload(link, DEV_A, key="k2.0-9").status_code == 200
    by_key = {r["recording_key"]: r for r in link.recs()}

    def days(r):
        exp = datetime.strptime(r["audio_expires_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        made = datetime.strptime(r["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return round((exp - made) / timedelta(days=1))
    assert days(by_key["k1.0-9"]) == 2 and days(by_key["k2.0-9"]) == 7


def test_turning_off_my_day_review_skips_only_me(link, monkeypatch):
    async def fetch(db, sql, params=()):
        return [{"user_id": "ann"}, {"user_id": "ben"}]
    monkeypatch.setattr(omi_day_review, "_fetch", fetch)
    assert run(omi_day_review.members_with_recordings()) == ["ann", "ben"]
    run(_write("ann", day_review_enabled=0))
    assert run(omi_day_review.members_with_recordings()) == ["ben"]


# --- the operator flag off: honest, not broken --------------------------------------------------------------------------------------
def test_with_linking_off_settings_say_ask_the_admin(link, monkeypatch):
    monkeypatch.delenv("ZOE_OMI_DEVICE_OWNERS")
    monkeypatch.setenv("ZOE_OMI_OWNER_USER_ID", "ann")
    link.as_(ANN)
    body = link.client.get("/api/omi/my-pendant").json()
    assert body["linking"]["enabled"] is False and body["linking"]["reason"] == "linking_off"
    assert "ask the household admin to turn on pendant linking" in body["linking"]["message"].lower()
    with pytest.raises(omi_linking.LinkError) as ei:
        run(omi_linking.start(ANN, "p1", notice_told=True, broker=link.broker, spawn=False))
    assert ei.value.code == "linking_off" and ei.value.message == body["linking"]["message"]
    monkeypatch.setenv("ZOE_OMI_DEVICE_OWNERS", "1")             # control: on, the same page offers linking
    assert link.client.get("/api/omi/my-pendant").json()["linking"]["enabled"] is True


# --- migration + erasure ------------------------------------------------------------------------------------------------------------
def test_the_migration_defaults_reproduce_todays_behaviour(env):
    env.db.conn.execute("CREATE TABLE omi_voice_settings (user_id TEXT PRIMARY KEY, learning_enabled INTEGER NOT NULL DEFAULT 1, "
                        "updated_at TEXT NOT NULL)")
    env.db.conn.execute("INSERT INTO omi_voice_settings VALUES ('ann', 0, '2026-01-01T00:00:00Z')")
    env.db.conn.commit()
    before = run(ms.read_uid("ann"))
    _migrate(env.engine, "0077_omi_member_settings.py")
    _migrate(env.engine, "0077_omi_member_settings.py")           # idempotent
    after = run(ms.read_uid("ann"))
    assert before == after == {**ms.DEFAULTS, "learning_enabled": False, "sync_paused": False, "speaker_tag_enabled": True,
                               "day_review_enabled": True}
    env.db.conn.execute("INSERT INTO omi_voice_settings (user_id, updated_at) VALUES ('ben', 'x')")
    env.db.conn.commit()
    row = dict(env.db.conn.execute("SELECT * FROM omi_voice_settings WHERE user_id = 'ben'").fetchone())
    assert (row["sync_paused"], row["speaker_tag_enabled"], row["keep_audio_days"], row["day_review_enabled"], row["notice_ack_at"]) \
        == (0, 1, None, 1, None)
    _migrate(env.engine, "0077_omi_member_settings.py", down=True)
    cols = {r[1] for r in env.db.conn.execute("PRAGMA table_info(omi_voice_settings)").fetchall()}
    assert cols == {"user_id", "learning_enabled", "updated_at"}


def test_erasing_a_member_removes_their_pendant_settings(link):
    import db_compat
    import omi_voices
    for ddl in ("CREATE TABLE learned_voiceprints (id TEXT, owner_user_id TEXT)",       # the other voice tables the erase walks (0055)
                "CREATE TABLE learned_voiceprint_segments (voiceprint_id TEXT)", "CREATE TABLE omi_segment_embeddings (user_id TEXT)",
                "CREATE TABLE omi_segments (user_id TEXT)", "CREATE TABLE omi_voice_clusters (user_id TEXT)"):
        link.db.conn.execute(ddl)
    link.db.conn.commit()
    run(_write("ann", sync_paused=1, keep_audio_days=3, notice_ack_at="2026-11-01T00:00:00Z"))
    run(_write("ben", sync_paused=1))

    async def erase():
        async with db_compat.get_compat_db() as db:
            return await omi_voices.forget_user(db, "ann")
    run(erase())
    left = [r["user_id"] for r in link.db.rows("SELECT user_id FROM omi_voice_settings")]
    assert left == ["ben"], "the erased member's settings (incl. the 0077 columns) are gone; another member's are kept"
