"""The household's DREAMING WINDOW (``dream_window.py`` + ``/api/settings/dream-window``): validated, stored, who may change it, the spoken flow. Synthetic data."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import json

import dream_window as dw
from fastapi import FastAPI
from fastapi.testclient import TestClient

from auth import get_current_user
from db_pool import get_db
from routers import stubs


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch, tmp_path):
    for k in ("ZOE_DREAM_EARLIEST", "ZOE_DREAM_LATEST", "ZOE_DREAM_AWAKE_BY", "ZOE_DREAM_QUIET_MIN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ZOE_DREAM_WINDOW_FILE", str(tmp_path / "dream-window.json"))


GOOD = {"earliest": "00:30", "latest": "02:30", "awake_by": "05:15", "quiet_min": 30}


# ── validation ─────────────────────────────────────────────────────────────────────────────────

def test_the_defaults_are_valid_and_a_good_custom_window_is_accepted():
    assert dw.validate(GOOD) == (GOOD, [])
    assert dw.validate({"earliest": "23:00", "latest": "01:00", "awake_by": "04:00", "quiet_min": 45})[1] == []       # crosses midnight
    assert dw.validate(None) == (dw.env_defaults(), [])


@pytest.mark.parametrize("bad,why", [
    ({"earliest": "noon"}, "not a time"),
    ({"earliest": "12:00"}, "outside the night"),
    ({"awake_by": "09:30"}, "outside the night"),
    ({"earliest": "03:00", "latest": "02:00"}, "after the latest start"),
    ({"earliest": "01:00", "latest": "01:30", "awake_by": "02:00"}, "at least 90 minutes"),
    ({"earliest": "00:30", "latest": "03:30", "awake_by": "05:30"}, "after the latest start"),                      # only 120 min of chain after the latest start (< 150)
    ({"awake_by": "04:30"}, "voice-check slot"),
    ({"quiet_min": 5}, "quiet_min"),
    ({"quiet_min": "lots"}, "quiet_min"),
])
def test_an_invalid_window_is_refused_with_the_reason_and_falls_back_to_the_defaults(bad, why):
    window, errs = dw.validate({**GOOD, **bad})
    assert errs and any(why in e for e in errs), errs
    assert window == dw.env_defaults()                                                  # never a half-applied setting
    partial, errs2 = dw.validate({**GOOD, **bad}, fallback=False)
    assert errs2 and isinstance(partial, dict)


def test_env_is_only_the_fallback_default_and_only_when_it_is_itself_valid(monkeypatch):
    monkeypatch.setenv("ZOE_DREAM_EARLIEST", "01:00")
    monkeypatch.setenv("ZOE_DREAM_LATEST", "02:00")
    monkeypatch.setenv("ZOE_DREAM_AWAKE_BY", "05:00")
    assert dw.env_defaults()["earliest"] == "01:00"
    monkeypatch.setenv("ZOE_DREAM_AWAKE_BY", "04:30")                                    # a bad override is ignored, not obeyed
    assert dw.env_defaults() == dw.DEFAULTS
    monkeypatch.setenv("ZOE_DREAM_AWAKE_BY", "05:00")
    stored = {"earliest": "00:00", "latest": "02:00", "awake_by": "05:00", "quiet_min": 30}
    assert dw.validate(stored)[0]["earliest"] == "00:00"                                  # a stored setting beats the env


def test_the_mirror_file_is_what_the_night_reads_and_a_bad_file_is_loud_not_silent(tmp_path):
    p = tmp_path / "w.json"
    assert dw.load_mirror(p) == (dw.env_defaults(), [], "default")                       # never written: the defaults, no complaint
    dw.save_mirror({"earliest": "01:00", "latest": "02:00", "awake_by": "05:00", "quiet_min": 20}, p)
    w, errs, src = dw.load_mirror(p)
    assert (w["earliest"], w["quiet_min"], errs, src) == ("01:00", 20, [], "stored")
    p.write_text(json.dumps({"earliest": "01:00", "latest": "02:00", "awake_by": "04:30", "quiet_min": 20}))
    w, errs, src = dw.load_mirror(p)
    assert errs and src == "default" and w == dw.env_defaults()                          # reported for the morning report
    p.write_text("{not json")
    assert dw.load_mirror(p)[1] and dw.load_mirror(p)[2] == "default"


def test_the_start_gate_waits_for_quiet_and_respects_the_bounds():
    w = GOOD
    assert dw.start_gate(0, w, 99) == "too_early"                                        # 00:00 < 00:30
    assert dw.start_gate(30, w, 29) == "wait" and dw.start_gate(30, w, 30) == "start"
    assert dw.start_gate(150, w, 99) == "start" and dw.start_gate(151, w, 99) == "too_late"
    assert dw.start_gate(23 * 60, {"earliest": "22:00", "latest": "01:00", "awake_by": "04:00", "quiet_min": 30}, 40) == "start"      # a window across midnight


# ── who may change it ──────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("role,via_voice,verified,ok", [
    ("admin", False, False, True), ("owner", False, False, True),
    ("admin", True, True, True), ("admin", True, False, False),                           # by voice the speaker must be verified
    ("member", False, False, False), ("child", False, False, False), ("guest", True, True, False), (None, False, False, False), (5, False, False, False)])
def test_only_adult_admins_change_it_and_by_voice_only_when_verified(role, via_voice, verified, ok):
    assert dw.may_change(role, via_voice=via_voice, voice_verified=verified) is ok


# ── spoken ─────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,earliest,awake_by", [
    ("Zoe, do your dreaming between 1 and 5", "01:00", "05:00"),
    ("do your dreaming between 1am and 5am", "01:00", "05:00"),
    ("do your dreaming between one and five", "01:00", "05:00"),
    ("I'd like you to dream between midnight and 5:30", "00:00", "05:30"),
    ("do your dreaming between 11 pm and 5", "23:00", "05:00"),
    ("do your dreaming between 1:30 and 5", "01:30", "05:00")])
def test_the_spoken_window_is_understood(text, earliest, awake_by):
    assert dw.parse_utterance(text) == {"kind": "set", "earliest": earliest, "awake_by": awake_by}


def test_questions_and_unclear_requests_are_told_apart():
    assert dw.parse_utterance("Zoe, when do you dream?") == {"kind": "ask"}
    assert dw.parse_utterance("what time is your dreaming window") == {"kind": "ask"}
    assert dw.parse_utterance("do your dreaming between ten and five") == {"kind": "set_unclear"}      # 10 is ambiguous (am or pm)
    assert dw.parse_utterance("what's the weather between 1 and 5") is None
    assert dw.parse_utterance("") is None


def test_a_verified_adult_changes_it_by_voice_and_zoe_reads_it_back():
    out = dw.apply_spoken("do your dreaming between 1 and 5", GOOD, role="admin", voice_verified=True)
    assert out["window"] == {"earliest": "01:00", "latest": "02:30", "awake_by": "05:00", "quiet_min": 30} and not out["errors"]
    assert "1 am" in out["reply"] and "5 am" in out["reply"] and out["reply"].startswith("Okay. I'll dream between")
    squeezed = dw.apply_spoken("do your dreaming between 1 and 4", GOOD, role="admin", voice_verified=True)
    assert squeezed["window"]["latest"] == "01:30" and squeezed["window"]["awake_by"] == "04:00"            # exactly the chain: she must start by 1:30 to be back at 4
    tight = dw.apply_spoken("do your dreaming between 1 and 3", GOOD, role="admin", voice_verified=True)
    assert tight["window"] is None and tight["errors"] and "can't dream between those hours" in tight["reply"]        # 2 h cannot hold the chain


def test_an_unverified_voice_a_child_or_a_guest_can_only_ask():
    for role, verified in (("admin", False), ("member", True), ("child", True), ("guest", True)):
        out = dw.apply_spoken("do your dreaming between 1 and 5", GOOD, role=role, voice_verified=verified)
        assert out["window"] is None and out["errors"] == ["not allowed"] and "Only an adult I recognise" in out["reply"]
    ask = dw.apply_spoken("when do you dream", GOOD, role="guest", voice_verified=False)
    assert ask["window"] is None and "12:30 am" in ask["reply"] and "5:15 am" in ask["reply"] and not ask["errors"]            # anyone may ask


# ── the API ────────────────────────────────────────────────────────────────────────────────────

class FakeDb:
    def __init__(self, stored=None):
        self.stored, self.writes = stored, []

    async def execute_fetchall(self, sql, params=()):
        return [{"value": json.dumps(self.stored)}] if self.stored is not None else []

    async def execute(self, sql, params=()):
        self.writes.append(params)

    async def commit(self):
        pass


def client(role, db):
    app = FastAPI()
    app.include_router(stubs.router)
    app.dependency_overrides[get_current_user] = lambda: {"user_id": "u1", "role": role}
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_the_api_reads_the_window_for_anyone_signed_in_and_says_whether_they_may_change_it():
    body = client("member", FakeDb()).get("/api/settings/dream-window").json()
    assert body["window"] == dw.env_defaults() and body["can_change"] is False and body["source"] == "default"
    assert client("admin", FakeDb(GOOD)).get("/api/settings/dream-window").json()["can_change"] is True
    bad = client("admin", FakeDb({**GOOD, "awake_by": "04:30"})).get("/api/settings/dream-window").json()
    assert bad["errors"] and bad["window"] == dw.env_defaults() and bad["source"] == "default"                # reported, defaults in force


def test_only_an_admin_can_write_it_and_the_write_is_validated_stored_and_mirrored(tmp_path):
    body = {"earliest": "01:00", "latest": "02:30", "awake_by": "05:30", "quiet_min": 20}
    for role in ("guest", "child", "member"):
        db = FakeDb()
        assert client(role, db).put("/api/settings/dream-window", json=body).status_code == 403 and db.writes == []
    db = FakeDb()
    r = client("admin", db).put("/api/settings/dream-window", json=body)
    assert r.status_code == 200 and r.json()["window"] == body and json.loads(db.writes[0][1]) == body
    assert json.loads((tmp_path / "dream-window.json").read_text()) == body                                   # what the night session reads


def test_an_invalid_write_is_a_422_with_every_reason_and_stores_nothing(tmp_path):
    db = FakeDb()
    r = client("admin", db).put("/api/settings/dream-window", json={"earliest": "03:00", "latest": "02:00", "awake_by": "04:30", "quiet_min": 30})
    assert r.status_code == 422 and len(r.json()["detail"]["errors"]) >= 2 and db.writes == [] and not (tmp_path / "dream-window.json").exists()
    assert client("admin", db).put("/api/settings/dream-window", json={"earliest": "01:00"}).status_code == 422       # all four fields are required
