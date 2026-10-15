"""Omi pendant OWNERSHIP: a registry maps each pendant to ONE household member; the sync runs for whoever is in range and a recording lands
only with the member its pendant is registered to (``omi_devices.py``, migration 0047, flag ``ZOE_OMI_DEVICE_OWNERS`` default OFF).

Owner requirement (2026-10-13): "link a certain omi to a certain user, but the sync should sync no matter who is logged on, as long as in
range of the pi, and of course only sync to the user its registered for".

Every safety rule has a named test AND a control arm (the opposite configuration, which must behave differently). Source mutations to
re-run by Edit, and the test each turns red:
  routers ``_upload_owner`` attributing to the panel's user instead of the registry owner          -> test_an_upload_lands_under_the_devices_owner_not_the_panels_signed_in_member
  routers ``_upload_owner`` falling back to ``omi.owner_user_id()`` AND ``store_recording``'s       -> test_an_unregistered_pendant_is_refused_and_nothing_falls_back_to_a_default_or_an_admin
    in-gate re-check removed (two layers: either alone is caught by the other, by design)
  ``omi_devices.assign`` also running ``UPDATE omi_recordings SET user_id``                        -> test_reassigning_a_pendant_moves_no_existing_recording
  ``_require_owner`` (device-owners branch) dropping the ``user_id`` filter / adding an admin override -> test_a_member_cannot_list_read_patch_or_delete_another_members_recordings
  ``_require_human_admin`` -> ``require_admin`` only (device tokens let through)                    -> test_registering_is_for_a_human_admin_only
  ``omi_devices.audit`` ignored (always True)                                                      -> test_a_change_the_audit_log_cannot_record_is_refused
  ``omi_devices.forget_user`` removed from ``omi_recordings.forget_user``                           -> test_erasing_a_member_unassigns_their_pendants_and_scrubs_their_name
  ``ensure_seeded`` without the existing-row check / ``DO NOTHING``                                -> test_the_seed_is_one_time_idempotent_and_never_undoes_an_admin_or_an_erase
  ``sync_notice`` using ``omi.owner_user_id()``                                                    -> test_the_sync_notice_goes_to_the_pendants_owner_only
  ``store_recording`` without the in-gate registry re-check                                        -> test_an_owner_change_between_lookup_and_write_wins

Synthetic data only (made-up ids and addresses; the 16-hex ids are hashes of fixtures). Real SQLite built by migrations 0045 + 0047 behind
``db_compat``; no hardware, no network.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import asyncio
import contextlib
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

import sqlalchemy as sa
from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import db_compat
import omi_devices
import omi_recordings as omi
import user_erase_gate as erase_gate
from routers import omi_recordings as omi_router
from test_omi_recordings import TOKEN, _migrate, _Sqlite, headers, make_wav

SVC = Path(__file__).resolve().parents[1]
REPO = SVC.parents[1]


def ident(address: str) -> str:
    """The same derivation both Pi services use (pinned against them in tests/unit/test_omi_device_identity.py)."""
    return hashlib.sha256(("omi-sync:" + address.strip().lower()).encode()).hexdigest()[:16]


DEV_A, DEV_B, DEV_C = ident("AA:BB:CC:DD:EE:01"), ident("AA:BB:CC:DD:EE:02"), ident("AA:BB:CC:DD:EE:03")
ANN = {"user_id": "ann", "role": "member", "username": "ann"}
BEN = {"user_id": "ben", "role": "member", "username": "ben"}
ADM = {"user_id": "adm", "role": "admin", "username": "adm"}
GST = {"user_id": "guest", "role": "guest", "username": "guest"}
PANEL_AS_BEN = {"user_id": "ben", "role": "member", "username": "panel:p1", "panel_id": "p1", "device_token_role": "panel"}
PANEL_AS_ADMIN = {"user_id": "adm", "role": "admin", "username": "panel:p1", "panel_id": "p1", "device_token_role": "admin"}


class Env:
    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.user = ANN
        self.app.dependency_overrides[auth.get_current_user] = lambda: self.user

    def as_(self, user):
        self.user = user
        return self

    def put(self, dev, user_id, label=None):
        return self.client.put(f"/api/omi/devices/{dev}", json={"user_id": user_id, **({"label": label} if label is not None else {})})

    def audit(self):
        p = omi_devices.audit_path()
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []

    def registry(self):
        return {r["device_id"]: dict(r) for r in self.db.rows("SELECT * FROM omi_devices")}

    def recs(self):
        return self.db.rows("SELECT * FROM omi_recordings ORDER BY created_at, id")


@pytest.fixture
def env(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    engine = sa.create_engine(f"sqlite:///{path}")
    _migrate(engine)
    _migrate(engine, "0047_omi_devices.py")
    db = _Sqlite(path)
    db.conn.execute("CREATE TABLE users (id TEXT PRIMARY KEY, name TEXT, role TEXT)")
    db.conn.executemany("INSERT INTO users VALUES (?, ?, ?)", [("ann", "Ann", "member"), ("ben", "Ben", "member"), ("adm", "Admin", "admin"),
                                                                ("guest", "Guest", "guest"), ("gst2", "Visitor", "guest")])
    db.conn.execute("CREATE TABLE notifications (id TEXT PRIMARY KEY, user_id TEXT, type TEXT, title TEXT, message TEXT, data TEXT, "
                    "delivered INTEGER DEFAULT 0, created_at TEXT)")
    db.conn.create_function("NOW", 0, lambda: "2026-10-10 06:00:00+00")
    db.conn.commit()

    @contextlib.asynccontextmanager
    async def fake_db():
        yield db

    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    monkeypatch.setenv("ZOE_OMI_SYNC_ENABLED", "1")
    monkeypatch.setenv("ZOE_OMI_DEVICE_OWNERS", "1")
    monkeypatch.delenv("ZOE_OMI_OWNER_USER_ID", raising=False)
    monkeypatch.setenv("ZOE_OMI_AUDIT_PATH", str(tmp_path / "omi-audit.jsonl"))
    monkeypatch.setenv("ZOE_OMI_AUDIO_DIR", str(tmp_path / "audio"))
    monkeypatch.setenv("ZOE_OMI_MIN_MEM_MB", "0")
    monkeypatch.setenv("ZOE_TIMEZONE", "Australia/Perth")
    erase_gate.reset()
    omi_devices.reset_for_tests()
    monkeypatch.setattr(omi_router, "_last_notice", {})

    import routers.panel_auth as pa
    # the panel's token is bound to BEN - and that must change nothing about whose recording an upload is
    monkeypatch.setattr(pa, "lookup_device_token", lambda raw: {"panel_id": "p1", "user_id": "ben"} if raw == "tok" else None)

    app = FastAPI()
    assert omi_router.register(app) is True
    yield Env(db=db, client=TestClient(app), app=app, tmp=tmp_path, engine=engine, mp=monkeypatch)
    erase_gate.reset()
    omi_devices.reset_for_tests()


def run(coro):
    return asyncio.run(coro)


def upload(env, dev=DEV_A, wav=None, **kw):
    wav = wav if wav is not None else make_wav(2.0)
    return env.client.post("/api/omi/recordings", content=wav, headers=headers(wav, device=dev, **kw))


def register(env, dev, who="ann"):
    env.as_(ADM)
    r = env.put(dev, who)
    assert r.status_code == 200, r.text
    env.as_(ANN)


# --- 1. the upload lands with the pendant's owner, whoever is signed in --------------------------------------------------------------------
def test_an_upload_lands_under_the_devices_owner_not_the_panels_signed_in_member(env):
    """The panel is bound to BEN and BEN is the signed-in session; the pendant is ANN's. Ann's recording is Ann's."""
    register(env, DEV_A, "ann")
    register(env, DEV_B, "ben")
    env.as_(BEN)
    r = upload(env, DEV_A, key="r1.0-9")
    assert r.status_code == 200 and r.json()["ok"] is True
    r2 = upload(env, DEV_B, key="r1.0-9")
    assert r2.status_code == 200
    by_dev = {x["device_id"]: x["user_id"] for x in env.recs()}
    assert by_dev == {DEV_A: "ann", DEV_B: "ben"}, "each recording is attributed by its PENDANT, not by the panel's session or binding"
    # control arm: the legacy single-owner mode attributes by configuration, so the registry is what made the difference above
    env.mp.delenv("ZOE_OMI_DEVICE_OWNERS")
    env.mp.setenv("ZOE_OMI_OWNER_USER_ID", "adm")
    r3 = upload(env, DEV_B, key="r2.0-9")
    assert r3.status_code == 200 and [x for x in env.recs() if x["recording_key"] == "r2.0-9"][0]["user_id"] == "adm"


def test_the_sync_needs_no_signed_in_user_at_all(env):
    register(env, DEV_A, "ann")
    env.as_(GST)                      # nobody is logged in on the panel (guest session)
    assert upload(env).status_code == 200
    assert [x["user_id"] for x in env.recs()] == ["ann"]


# --- 2. an unregistered pendant ------------------------------------------------------------------------------------------------------------
def test_an_unregistered_pendant_is_refused_and_nothing_falls_back_to_a_default_or_an_admin(env):
    env.mp.setenv("ZOE_OMI_OWNER_USER_ID", "adm")        # a configured default exists...
    env.as_(ADM)                                         # ...an admin is signed in...
    r = upload(env, DEV_C)                               # ...and a pendant nobody registered uploads
    assert r.status_code == 409 and r.json() == {"ok": False, "error": "device_not_registered"}
    assert env.recs() == [] and not list((env.tmp / "audio").glob("*.wav"))


def test_the_pi_reads_that_refusal_as_keep_custody_not_as_confirmed_and_not_as_a_permanent_rejection(env):
    sys.path.insert(0, str(REPO / "services" / "omi-sync"))
    try:
        from omi_sync.uploader import Outcome, classify
    finally:
        sys.path.pop(0)
    wav = make_wav(2.0)
    r = upload(env, DEV_C, wav)
    assert classify(r.status_code, r.content, hashlib.sha256(wav).hexdigest()).outcome is Outcome.NOT_REGISTERED


def test_an_unassigned_or_inactive_pendant_is_refused_too(env):
    register(env, DEV_A, "ann")
    assert upload(env, key="k1").status_code == 200
    env.as_(ADM)
    assert env.client.delete(f"/api/omi/devices/{DEV_A}").status_code == 200
    assert upload(env, key="k2").status_code == 409
    env.db.conn.execute("UPDATE omi_devices SET user_id = 'ann', active = 0 WHERE device_id = ?", (DEV_A,))      # owner set but inactive
    env.db.conn.commit()
    assert upload(env, key="k3").status_code == 409
    assert [x["recording_key"] for x in env.recs()] == ["k1"]


def test_a_malformed_device_id_is_a_400_and_a_missing_registry_table_refuses_instead_of_guessing(env):
    assert upload(env, "short").status_code == 400
    assert upload(env, DEV_A.upper()).status_code == 400
    env.db.conn.execute("DROP TABLE omi_devices")                      # migration 0047 not applied yet
    env.db.conn.commit()
    env.mp.setenv("ZOE_OMI_OWNER_USER_ID", "adm")
    r = upload(env, DEV_A)
    assert r.status_code == 409 and env.recs() == [], "an unreadable registry is 'nobody owns it', never a guess"


def test_an_owner_change_between_lookup_and_write_wins(env):
    """Erase / reassign lands after the route looked the owner up and before the write: the write is refused, the Pi keeps the audio."""
    register(env, DEV_A, "ann")
    wav = make_wav(2.0)
    kw = dict(device_id=DEV_A, key="r9.0-9", sha256=hashlib.sha256(wav).hexdigest(), started_at="2026-10-10T01:00:00Z", duration_s=2.0,
              time_quality="device", gap_before=0, wav=wav)
    env.as_(ADM)
    assert env.put(DEV_A, "ben").status_code == 200                    # the pendant moved to ben...
    with pytest.raises(omi.StoreError) as ei:
        run(omi.store_recording("ann", registry=True, **kw))           # ...but the route had resolved "ann" a moment earlier
    assert (ei.value.status, ei.value.code) == (409, "device_not_registered")
    assert env.recs() == []
    assert run(omi.store_recording("ben", registry=True, **kw))["ok"] is True


# --- 3. viewing ------------------------------------------------------------------------------------------------------------------------------
def test_a_member_cannot_list_read_patch_or_delete_another_members_recordings(env):
    register(env, DEV_A, "ann")
    register(env, DEV_B, "ben")
    ra = upload(env, DEV_A, key="a1").json()["id"]
    rb = upload(env, DEV_B, key="b1").json()["id"]
    env.as_(ANN)
    assert [x["id"] for x in env.client.get("/api/omi/recordings").json()["recordings"]] == [ra]
    assert env.client.get(f"/api/omi/recordings/{ra}").status_code == 200
    assert env.client.get(f"/api/omi/recordings/{rb}").status_code == 404
    assert env.client.patch(f"/api/omi/recordings/{rb}", json={"reviewed": True}).status_code == 404
    assert env.client.delete(f"/api/omi/recordings/{rb}").status_code == 404
    env.as_(BEN)
    assert [x["id"] for x in env.client.get("/api/omi/recordings").json()["recordings"]] == [rb]
    assert env.client.patch(f"/api/omi/recordings/{rb}", json={"reviewed": True}).status_code == 200
    rows = {r["id"]: r for r in env.recs()}
    assert rows[rb]["reviewed"] == 1 and rows[ra]["reviewed"] == 0 and rows[ra]["status"] != "deleted" and rows[rb]["status"] != "deleted"


def test_no_admin_override_for_reading_another_members_recordings(env):
    register(env, DEV_A, "ann")
    ra = upload(env, DEV_A, key="a1").json()["id"]
    env.as_(ADM)
    body = env.client.get("/api/omi/recordings").json()
    assert body["recordings"] == [] and body["empty_transcript_held"] == 0 and body["transcription"]["backlog"] == 0
    for r in (env.client.get(f"/api/omi/recordings/{ra}"), env.client.patch(f"/api/omi/recordings/{ra}", json={"keep_audio": True}),
              env.client.delete(f"/api/omi/recordings/{ra}")):
        assert r.status_code == 404
    assert env.recs()[0]["status"] != "deleted"


def test_guests_and_panel_tokens_do_not_read_recordings(env):
    register(env, DEV_B, "ben")
    upload(env, DEV_B, key="b1")
    env.as_(GST)
    assert env.client.get("/api/omi/recordings").status_code == 403
    env.as_(PANEL_AS_BEN)               # the kiosk's device token resolves to ben - a panel is not a person
    assert env.client.get("/api/omi/recordings").status_code == 403
    env.as_(BEN)
    assert env.client.get("/api/omi/recordings").status_code == 200       # control arm: ben himself


def test_legacy_mode_is_still_owner_only(env):
    """Flag off: the old contract is untouched (one configured owner; admin does not widen it)."""
    env.mp.delenv("ZOE_OMI_DEVICE_OWNERS")
    env.mp.setenv("ZOE_OMI_OWNER_USER_ID", "ann")
    env.as_(BEN)
    assert env.client.get("/api/omi/recordings").status_code == 403
    env.as_(ADM)
    assert env.client.get("/api/omi/recordings").status_code == 403
    env.as_(ANN)
    assert env.client.get("/api/omi/recordings").status_code == 200
    env.as_(ADM)
    assert env.client.get("/api/omi/devices").status_code == 404, "the registry routes need the flag"


# --- 4. registration is a human admin's, audited -------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("who,name", [(ANN, "member"), (GST, "guest"), (PANEL_AS_ADMIN, "device token resolving to an admin"),
                                      (PANEL_AS_BEN, "device token resolving to a member")])
def test_registering_is_for_a_human_admin_only(env, who, name):
    env.as_(who)
    assert env.put(DEV_A, "ann").status_code == 403, name
    assert env.client.delete(f"/api/omi/devices/{DEV_A}").status_code == 403, name
    assert env.client.get("/api/omi/devices").status_code == 403, name
    assert env.client.get("/api/omi/members").status_code == 403, name
    assert env.registry() == {} and env.audit() == []
    # control arm: the same calls succeed for a signed-in admin person
    env.as_(ADM)
    assert env.put(DEV_A, "ann").status_code == 200 and env.client.get("/api/omi/devices").status_code == 200
    assert env.client.get("/api/omi/members").status_code == 200


def test_a_bare_device_token_cannot_register_either(env):
    """No session at all, only the panel's X-Device-Token (what omi-sync holds): get_current_user resolves it to a panel principal."""
    env.app.dependency_overrides.pop(auth.get_current_user)
    r = env.client.put(f"/api/omi/devices/{DEV_A}", json={"user_id": "ann"}, headers=TOKEN)
    assert r.status_code in (401, 403) and env.registry() == {}


def test_assign_validates_the_member_and_the_id(env):
    env.as_(ADM)
    assert env.put(DEV_A, "nobody").status_code == 400
    assert env.put(DEV_A, "gst2").status_code == 400, "a guest cannot own a pendant"
    assert env.put(DEV_A, "guest").status_code == 400
    assert env.put("not-an-id", "ann").status_code == 400
    assert env.registry() == {}
    r = env.put(DEV_A, "ann", label="Kitchen \x00pendant " + "x" * 200)
    body = r.json()
    assert r.status_code == 200 and body["user_id"] == "ann" and len(body["label"]) <= 60 and "\x00" not in body["label"]
    assert body["registered_by"] == "adm" and body["active"] is True and body["previous_user_id"] is None
    assert "stay with their owner" in body["note"]


def test_the_members_list_has_no_guests(env):
    env.as_(ADM)
    members = env.client.get("/api/omi/members").json()["members"]
    assert {m["user_id"] for m in members} == {"ann", "ben", "adm"} and all(set(m) == {"user_id", "name"} for m in members)


def test_the_registry_shows_ownership_never_recordings(env):
    register(env, DEV_A, "ann")
    upload(env, DEV_A, key="a1")
    env.as_(ADM)
    (d,) = env.client.get("/api/omi/devices").json()["devices"]
    assert set(d) == {"device_id", "user_id", "label", "active", "registered_by", "registered_at", "updated_at"}
    blob = json.dumps(d)
    assert not re.search(r"transcript|recording|duration|count|audio", blob), "an admin manages who owns a pendant, never what it heard"


# --- 5. reassigning ------------------------------------------------------------------------------------------------------------------------------
def test_reassigning_a_pendant_moves_no_existing_recording(env):
    register(env, DEV_A, "ann")
    old = upload(env, DEV_A, key="old").json()["id"]
    before = {r["id"]: (r["user_id"], r["device_id"]) for r in env.recs()}
    env.as_(ADM)
    r = env.put(DEV_A, "ben")
    assert r.status_code == 200 and r.json()["previous_user_id"] == "ann" and r.json()["user_id"] == "ben"
    assert {r["id"]: (r["user_id"], r["device_id"]) for r in env.recs()} == before, "reassigning rewrote a recording's owner"
    new = upload(env, DEV_A, key="new").json()["id"]
    rows = {r["id"]: r["user_id"] for r in env.recs()}
    assert rows == {old: "ann", new: "ben"}
    env.as_(ANN)
    assert [x["id"] for x in env.client.get("/api/omi/recordings").json()["recordings"]] == [old]
    env.as_(BEN)
    assert [x["id"] for x in env.client.get("/api/omi/recordings").json()["recordings"]] == [new]


def test_unassigning_keeps_recordings_and_refuses_the_next_upload(env):
    register(env, DEV_A, "ann")
    rid = upload(env, key="a1").json()["id"]
    env.as_(ADM)
    assert env.client.delete(f"/api/omi/devices/{DEV_A}").status_code == 200
    assert env.client.delete(f"/api/omi/devices/{DEV_C}").status_code == 404
    assert upload(env, key="a2").status_code == 409
    assert env.registry()[DEV_A]["user_id"] == "" and env.registry()[DEV_A]["active"] == 0
    env.as_(ANN)
    assert env.client.get(f"/api/omi/recordings/{rid}").status_code == 200


def test_assigning_the_same_member_again_keeps_the_registration_time_and_relabels(env):
    register(env, DEV_A, "ann")
    first = env.registry()[DEV_A]["registered_at"]
    env.as_(ADM)
    assert env.put(DEV_A, "ann", label="Kitchen").status_code == 200
    reg = env.registry()[DEV_A]
    assert reg["label"] == "Kitchen" and reg["registered_at"] == first
    assert [e["action"] for e in env.audit() if e["phase"] == "requested"] == ["assign", "relabel"]


# --- 6. the audit log, fail-closed ---------------------------------------------------------------------------------------------------------------
def test_every_change_is_audited_with_who_and_what_and_nothing_secret(env):
    env.as_(ADM)
    env.put(DEV_A, "ann", label="Kitchen")
    env.put(DEV_A, "ben")
    env.client.delete(f"/api/omi/devices/{DEV_A}")
    lines = env.audit()
    assert [(e["phase"], e["action"]) for e in lines] == [("requested", "assign"), ("finished", "assign"), ("requested", "reassign"),
                                                         ("finished", "reassign"), ("requested", "unassign"), ("finished", "unassign")]
    req = lines[2]
    assert (req["actor"], req["role"], req["device_id"], req["from_user"], req["to_user"]) == ("adm", "admin", DEV_A, "ann", "ben")
    text = json.dumps(lines)
    assert not re.search(r"\b[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}\b", text) and "Kitchen" not in text
    assert (omi_devices.audit_path().stat().st_mode & 0o777) == 0o600


def test_a_change_the_audit_log_cannot_record_is_refused(env):
    env.as_(ADM)
    env.put(DEV_A, "ann")
    (env.tmp / "blocker").write_text("a file where the audit directory should be")
    env.mp.setenv("ZOE_OMI_AUDIT_PATH", str(env.tmp / "blocker" / "audit.jsonl"))                  # unwritable: its parent is a file
    assert env.put(DEV_A, "ben").status_code == 503
    assert env.client.delete(f"/api/omi/devices/{DEV_A}").status_code == 503
    assert env.put(DEV_B, "ben").status_code == 503
    reg = env.registry()
    assert reg[DEV_A]["user_id"] == "ann" and reg[DEV_A]["active"] == 1 and DEV_B not in reg, "no audit line, no change"
    env.mp.setenv("ZOE_OMI_AUDIT_PATH", str(env.tmp / "ok.jsonl"))                                  # control arm
    assert env.put(DEV_A, "ben").status_code == 200


# --- 7. forgetting a member ------------------------------------------------------------------------------------------------------------------------
def test_erasing_a_member_unassigns_their_pendants_and_scrubs_their_name(env):
    register(env, DEV_A, "ann")
    register(env, DEV_B, "ben")
    env.db.conn.execute("UPDATE omi_devices SET registered_by = 'ann' WHERE device_id = ?", (DEV_B,))        # ann once assigned ben's pendant
    env.db.conn.commit()
    upload(env, DEV_A, key="a1")
    upload(env, DEV_B, key="b1")
    assert run(omi.forget_user("ann")) == 1                                                                  # her one recording
    reg = env.registry()
    assert not [r for r in reg.values() if "ann" in (r["user_id"], r["registered_by"])], "a row still references the erased member"
    assert (reg[DEV_A]["user_id"], reg[DEV_A]["active"], reg[DEV_A]["label"]) == ("", 0, "")
    assert (reg[DEV_B]["user_id"], reg[DEV_B]["active"]) == ("ben", 1), "another member's pendant is untouched"
    assert upload(env, DEV_A, key="a2").status_code == 409, "her pendant is nobody's now"
    assert upload(env, DEV_B, key="b2").status_code == 200
    assert "omi_recordings.forget_user(user_id)" in (SVC / "memory_service.py").read_text(), "the erase cascade reaches this module"


def test_a_registry_that_is_not_migrated_never_blocks_erasing_the_audio(env):
    register(env, DEV_A, "ann")
    upload(env, key="a1")
    env.db.conn.execute("DROP TABLE omi_devices")
    env.db.conn.commit()
    assert run(omi.forget_user("ann")) == 1
    assert env.recs()[0]["status"] == "deleted"


# --- 8. the one-time seed -------------------------------------------------------------------------------------------------------------------------
def seed_history(env, dev=DEV_A, who="ann", key="legacy1"):
    """Recordings uploaded under the LEGACY single-owner mode (flag off), as on a box that has been syncing since before the registry."""
    env.mp.delenv("ZOE_OMI_DEVICE_OWNERS")
    env.mp.setenv("ZOE_OMI_OWNER_USER_ID", who)
    assert upload(env, dev, key=key).status_code == 200
    env.mp.setenv("ZOE_OMI_DEVICE_OWNERS", "1")
    omi_devices.reset_for_tests()                         # "restart zoe-data"


def test_the_seed_is_one_time_idempotent_and_never_undoes_an_admin_or_an_erase(env):
    seed_history(env)
    assert upload(env, DEV_A, key="after").status_code == 200, "the one known pendant keeps syncing to its owner with no operator step"
    assert env.registry()[DEV_A]["user_id"] == "ann" and env.registry()[DEV_A]["registered_by"] == "seed"
    assert len(env.registry()) == 1
    assert run(omi_devices.ensure_seeded()) is False                       # same process: a no-op
    omi_devices.reset_for_tests()
    assert run(omi_devices.ensure_seeded()) is False and len(env.registry()) == 1, "a restart does not seed twice"
    env.as_(ADM)
    assert env.put(DEV_A, "ben").status_code == 200                        # the admin reassigns...
    omi_devices.reset_for_tests()
    assert upload(env, DEV_A, key="x").status_code == 200
    assert env.registry()[DEV_A]["user_id"] == "ben", "...and a restart does not put it back to the configured owner"
    assert env.client.delete(f"/api/omi/devices/{DEV_A}").status_code == 200
    omi_devices.reset_for_tests()
    assert upload(env, DEV_A, key="y").status_code == 409, "an unassign is not undone by the seed"
    assert env.registry()[DEV_A]["active"] == 0


def test_erasing_the_seeded_owner_is_not_undone_by_a_restart(env):
    seed_history(env)
    assert upload(env, DEV_A, key="after").status_code == 200 and env.registry()[DEV_A]["user_id"] == "ann"
    run(omi.forget_user("ann"))                           # the legacy env var still names ann, and her tombstoned rows still carry the device id
    omi_devices.reset_for_tests()                         # restart
    assert run(omi_devices.ensure_seeded()) is False
    assert (env.registry()[DEV_A]["user_id"], env.registry()[DEV_A]["active"]) == ("", 0), "the seed resurrected an erased member's pendant"
    assert upload(env, DEV_A, key="later").status_code == 409


def test_the_seed_needs_exactly_one_known_pendant_under_the_configured_owner(env):
    # no history at all: registration is explicit
    env.mp.setenv("ZOE_OMI_OWNER_USER_ID", "ann")
    assert run(omi_devices.ensure_seeded()) is False and env.registry() == {}
    assert upload(env, DEV_A).status_code == 409
    # two pendants have uploaded: ambiguous, so nothing is guessed
    omi_devices.reset_for_tests()
    seed_history(env, DEV_A, "ann", "h1")
    seed_history(env, DEV_B, "ann", "h2")
    assert run(omi_devices.ensure_seeded()) is False and env.registry() == {}
    assert upload(env, DEV_A, key="n").status_code == 409
    # the history belongs to someone other than the configured owner
    env.db.conn.execute("DELETE FROM omi_recordings WHERE device_id = ?", (DEV_B,))
    env.db.conn.commit()
    omi_devices.reset_for_tests()
    env.mp.setenv("ZOE_OMI_OWNER_USER_ID", "ben")
    assert run(omi_devices.ensure_seeded()) is False and env.registry() == {}
    # no configured owner: nothing to seed
    omi_devices.reset_for_tests()
    env.mp.delenv("ZOE_OMI_OWNER_USER_ID")
    assert run(omi_devices.ensure_seeded()) is False and env.registry() == {}


def test_the_seed_does_not_run_while_the_flag_is_off(env):
    env.mp.delenv("ZOE_OMI_DEVICE_OWNERS")
    env.mp.setenv("ZOE_OMI_OWNER_USER_ID", "ann")
    assert upload(env, DEV_A).status_code == 200
    assert env.registry() == {}


# --- 9. the sync notice goes to the pendant's owner only ------------------------------------------------------------------------------------
def test_the_sync_notice_goes_to_the_pendants_owner_only(env, monkeypatch):
    register(env, DEV_A, "ann")
    register(env, DEV_B, "ben")
    got = []

    async def fake_notify(uid, count):
        got.append((uid, count))
        return True

    monkeypatch.setattr(omi, "notify_synced", fake_notify)
    env.as_(BEN)                                           # ben is signed in on the panel; the pendant that synced is ANN's
    assert env.client.post("/api/omi/sync-notice", json={"count": 3, "device": DEV_A}, headers=TOKEN).json() == {"ok": True, "sent": True}
    assert got == [("ann", 3)]
    assert env.client.post("/api/omi/sync-notice", json={"count": 3, "device": DEV_A}, headers=TOKEN).json()["sent"] is False     # rate limit, per owner
    assert env.client.post("/api/omi/sync-notice", json={"count": 1, "device": DEV_B}, headers=TOKEN).json()["sent"] is True       # ben's own, not blocked by ann's
    assert got == [("ann", 3), ("ben", 1)]
    for body in ({"count": 2, "device": DEV_C}, {"count": 2, "device": "garbage"}, {"count": 2}):
        assert env.client.post("/api/omi/sync-notice", json=body, headers=TOKEN).json() == {"ok": True, "sent": False}, body
    assert got == [("ann", 3), ("ben", 1)], "an unowned or unnamed pendant notifies nobody"
    assert env.client.post("/api/omi/sync-notice", json={"count": 2, "device": DEV_A}).status_code == 401


# --- 10. flag-dark + migration ---------------------------------------------------------------------------------------------------------------------
def test_the_registry_routes_do_not_exist_while_the_flag_is_off(env):
    env.mp.delenv("ZOE_OMI_DEVICE_OWNERS")
    env.as_(ADM)
    assert env.client.get("/api/omi/devices").status_code == 404
    assert env.client.get("/api/omi/members").status_code == 404
    assert env.put(DEV_A, "ann").status_code == 404 and env.registry() == {}


def test_there_is_exactly_one_alembic_head_and_0047_extends_main():
    versions = SVC / "alembic" / "versions"
    revs, downs = {}, set()
    for p in versions.glob("*.py"):
        txt = p.read_text()
        m = re.search(r'^revision\s*=\s*"([^"]+)"', txt, re.M)
        d = re.search(r'^down_revision\s*=\s*"([^"]+)"', txt, re.M)
        if m:
            revs[m.group(1)] = d.group(1) if d else None
            if d:
                downs.add(d.group(1))
    heads = sorted(set(revs) - downs)
    assert len(heads) == 1, f"alembic has {len(heads)} heads: {heads}"
    assert revs["0047"] in revs, "0047 must extend an existing revision"
    chain, cur = [], heads[0]
    while cur:
        chain.append(cur)
        cur = revs.get(cur)
    assert "0047" in chain and "0045" in chain, "0047 is on the main line"


def test_migration_0047_is_reversible_and_rerunnable(env):
    _migrate(env.engine, "0047_omi_devices.py")                           # IF NOT EXISTS: a rerun is safe
    cols = [r[1] for r in env.db.conn.execute("PRAGMA table_info(omi_devices)")]
    assert cols == ["device_id", "user_id", "label", "registered_by", "registered_at", "updated_at", "active"]
    _migrate(env.engine, "0047_omi_devices.py", down=True)
    assert env.db.rows("SELECT name FROM sqlite_master WHERE name = 'omi_devices'") == []
    assert env.db.rows("SELECT name FROM sqlite_master WHERE name = 'omi_recordings'") != [], "the downgrade leaves 0045 alone"


def test_the_registry_never_sees_a_ble_address(env):
    for f in (SVC / "omi_devices.py", SVC / "routers" / "omi_recordings.py"):
        assert not re.search(r"\b[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}\b", f.read_text()), f.name
    assert not omi_devices.DEVICE_ID_RX.match("AA:BB:CC:DD:EE:01"), "a raw address is not a device id"
