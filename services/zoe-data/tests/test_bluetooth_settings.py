"""Settings > Bluetooth devices, zoe-data side: ``bluetooth_broker.py`` + ``routers/bluetooth.py`` (flag ``ZOE_BLUETOOTH_SETTINGS``, default OFF).

Groups (each flag/auth test carries its own control arm; source mutations worth re-running by Edit: drop ``_require_human_admin``'s
device-token refusal; drop ``job.panel_id != panel_id`` in ``report``; make ``audit`` always return True; drop the ``update_start``
confirm check; drop ``_sweep``'s queued TTL):

  flag + auth    off = routes ABSENT; admin session only - a member, a guest, an unauthenticated caller and a DEVICE TOKEN are refused
  agent lane     a device token polls/reports; its panel id comes from the token; it cannot touch another panel's jobs
  the flow       scan -> job -> agent picks it up -> report -> the device list; connect / forget / update are queued, audited, serialised
  audit          every connect/disconnect/forget/update request AND outcome is logged; no audit line = no action (fail closed)
  bounds         panel-supplied data is truncated; unreachable panels fail their jobs with a sentence

Synthetic data only: panel ids are made up, device ids are 16-hex hashes, and a BLE address appears nowhere.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

import auth
import bluetooth_broker as bt
from routers import bluetooth as bt_router

PANEL, OTHER = "panel-a1", "panel-b2"
DEV = "0123456789abcdef"
ADMIN = {"user_id": "u-admin", "role": "admin", "username": "jason"}
MEMBER = {"user_id": "u-member", "role": "member", "username": "sam"}
GUEST = {"user_id": "guest", "role": "guest", "username": "guest"}
DEVICE_AS_ADMIN = {"user_id": "u-admin", "role": "admin", "username": "panel:panel-a1", "panel_id": PANEL, "device_token_role": "admin"}


class Env:
    def __init__(self, app, client, broker, clock, tmp):
        self.app, self.client, self.broker, self.clock, self.tmp = app, client, broker, clock, tmp
        self.user = ADMIN
        app.dependency_overrides[auth.get_current_user] = lambda: self.user

    def as_(self, user):
        self.user = user
        return self

    def audit_lines(self):
        p = bt.audit_path()
        return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


class Clock:
    def __init__(self):
        self.t = 5000.0

    def __call__(self):
        return self.t


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("ZOE_BLUETOOTH_SETTINGS", "1")
    monkeypatch.setenv("ZOE_BLUETOOTH_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    clock = Clock()
    broker = bt.Broker(clock=clock)
    monkeypatch.setattr(bt, "BROKER", broker)

    import routers.panel_auth as pa
    monkeypatch.setattr(pa, "lookup_device_token", lambda raw: {"panel_id": PANEL} if raw == "tok-a" else {"panel_id": OTHER} if raw == "tok-b" else None)

    async def names():
        return {PANEL: {"name": "Kitchen Panel", "location": "Kitchen"}}

    monkeypatch.setattr(bt_router, "_panel_names", names)
    app = FastAPI()
    assert bt_router.register(app) is True
    return Env(app, TestClient(app), broker, clock, tmp_path)


A, B = {"X-Device-Token": "tok-a"}, {"X-Device-Token": "tok-b"}


def poll(env, headers=A, snapshot=None, wait=0):
    r = env.client.post("/api/bluetooth/agent/poll", json={"snapshot": snapshot if snapshot is not None else {}, "wait_s": wait}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["commands"]


def report(env, job, headers=A, **kw):
    return env.client.post("/api/bluetooth/agent/report", json={"job_id": job, **kw}, headers=headers)


SNAP = {"agent": {"version": "1.0.0", "ota_enabled": True, "hci": "default"}, "devices": [], "busy": None, "radio_holder": ""}


def online(env, panel_headers=A, snap=None):
    poll(env, panel_headers, snap or SNAP)


# --- flag + auth ---------------------------------------------------------------------------------------------------------------
def test_flag_off_routes_are_absent(monkeypatch):
    monkeypatch.delenv("ZOE_BLUETOOTH_SETTINGS", raising=False)
    app = FastAPI()
    assert bt_router.register(app) is False
    c = TestClient(app)
    assert c.get("/api/bluetooth/config").status_code == 404
    assert c.get("/api/bluetooth/panels").status_code == 404
    assert c.post("/api/bluetooth/agent/poll", json={}, headers=A).status_code == 404


def test_flag_on_is_the_control_arm(env):
    assert env.client.get("/api/bluetooth/config").json() == {"enabled": True}


@pytest.mark.parametrize("who", [MEMBER, GUEST, DEVICE_AS_ADMIN])
def test_only_a_human_admin_may_use_the_household_routes(env, who):
    env.as_(who)
    online(env)
    for method, path in (("get", "/api/bluetooth/config"), ("get", "/api/bluetooth/panels"), ("get", f"/api/bluetooth/panels/{PANEL}"),
                         ("post", f"/api/bluetooth/panels/{PANEL}/scan"), ("post", f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/connect"),
                         ("post", f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/forget"),
                         ("post", f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/update_start"),
                         ("get", f"/api/bluetooth/panels/{PANEL}/jobs/abc")):
        assert getattr(env.client, method)(path).status_code == 403, (who["username"], path)
    assert env.broker.jobs == {}, "a refused caller must not have queued anything"


def test_the_admin_control_arm_is_allowed(env):
    online(env)
    assert env.client.get("/api/bluetooth/panels").status_code == 200
    assert env.client.post(f"/api/bluetooth/panels/{PANEL}/scan").status_code == 200


def test_the_agent_lane_needs_a_device_token_and_a_session_is_not_enough(env):
    env.as_(ADMIN)
    for hdr in ({}, {"X-Device-Token": "wrong"}, {"X-Device-Token": ""}):
        assert env.client.post("/api/bluetooth/agent/poll", json={}, headers=hdr).status_code == 401
        assert env.client.post("/api/bluetooth/agent/report", json={"job_id": "x"}, headers=hdr).status_code == 401
    assert env.broker.panels == {}


# --- the flow ------------------------------------------------------------------------------------------------------------------------
def test_panels_list_shows_name_and_online_state_only_for_panels_with_an_agent(env):
    assert env.client.get("/api/bluetooth/panels").json() == {"panels": []}
    online(env)
    p = env.client.get("/api/bluetooth/panels").json()["panels"]
    assert p == [{"panel_id": PANEL, "online": True, "last_seen_s": 0, "agent_version": "1.0.0", "ota_enabled": True, "adapter": "default",
                  "name": "Kitchen Panel", "location": "Kitchen"}]
    env.clock.t += bt.ONLINE_S + 1
    assert env.client.get("/api/bluetooth/panels").json()["panels"][0]["online"] is False
    env.clock.t += bt.FORGET_PANEL_S
    assert env.client.get("/api/bluetooth/panels").json() == {"panels": []}


def test_scan_flows_from_the_ui_through_the_agent_and_back_into_the_device_list(env):
    online(env)
    job = env.client.post(f"/api/bluetooth/panels/{PANEL}/scan").json()
    assert job["state"] == "queued" and job["op"] == "scan"
    cmds = poll(env)
    assert cmds == [{"job_id": job["job_id"], "op": "scan", "device_id": None, "params": {}}]
    assert poll(env) == [], "a command is delivered once"
    assert report(env, job["job_id"], state="running", progress={"step": "scan", "pct": 10, "message": "Scanning"}).status_code == 200
    mid = env.client.get(f"/api/bluetooth/panels/{PANEL}/jobs/{job['job_id']}").json()
    assert mid["state"] == "running" and mid["progress"]["pct"] == 10
    result = {"devices": [{"id": DEV, "name": "Omi", "rssi": -60, "driver": "omi", "label": "Omi pendant", "specific_driver": True,
                           "remembered": False, "connected": False},
                          {"id": "fedcba9876543210", "name": "HR Strap", "rssi": -50, "driver": "generic", "label": "Bluetooth device",
                           "specific_driver": False, "remembered": False, "connected": False}], "hidden_unnamed": 3, "scanned_s": 10}
    assert report(env, job["job_id"], state="succeeded", result=result).status_code == 200
    view = env.client.get(f"/api/bluetooth/panels/{PANEL}").json()
    assert [d["name"] for d in view["devices"]] == ["Omi", "HR Strap"] and view["scan"]["hidden_unnamed"] == 3 and view["busy"] is None
    assert view["devices"][0]["supports_update"] is True and view["devices"][0]["in_range"] is True


def test_remembered_devices_show_even_when_out_of_range(env):
    snap = {**SNAP, "devices": [{"id": DEV, "name": "Omi", "driver": "omi", "label": "Omi pendant", "connected": True, "lease_s": 90,
                                 "supports_update": True}]}
    online(env, snap=snap)
    d = env.client.get(f"/api/bluetooth/panels/{PANEL}").json()["devices"][0]
    assert d["remembered"] and d["connected"] and d["lease_s"] == 90 and d["in_range"] is False and d["rssi"] is None


def test_a_device_action_is_queued_with_its_device_id(env):
    online(env)
    r = env.client.post(f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/connect")
    assert r.status_code == 200
    assert poll(env) == [{"job_id": r.json()["job_id"], "op": "connect", "device_id": DEV, "params": {}}]


@pytest.mark.parametrize("path,status", [
    (f"/api/bluetooth/panels/{PANEL}/devices/NOT-A-HASH/connect", 400),
    (f"/api/bluetooth/panels/{PANEL}/devices/E6:11:22:33:44:55/connect", 400),            # an address is never an id
    (f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/reboot", 404),
    (f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/scan", 404),
    (f"/api/bluetooth/panels/nope/scan", 404),
    ("/api/bluetooth/panels/bad%20id/scan", 400),
])
def test_bad_targets_are_refused(env, path, status):
    online(env)
    assert env.client.post(path).status_code == status
    assert env.broker.jobs == {}


def test_an_offline_panel_cannot_be_given_work(env):
    online(env)
    env.clock.t += bt.ONLINE_S + 1
    r = env.client.post(f"/api/bluetooth/panels/{PANEL}/scan")
    assert r.status_code == 409 and "offline" in r.json()["detail"]


def test_one_task_at_a_time_per_panel_but_cancel_and_status_always_get_through(env):
    online(env)
    first = env.client.post(f"/api/bluetooth/panels/{PANEL}/scan").json()
    assert env.client.post(f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/connect").status_code == 409
    c = env.client.post(f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/update_cancel")
    assert c.status_code == 200, "cancel is never queued behind the thing it cancels"
    report(env, first["job_id"], state="succeeded", result={"devices": []})
    assert env.client.post(f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/connect").status_code == 200


def test_a_panel_cannot_report_on_another_panels_job_or_an_unknown_job(env):
    online(env)
    online(env, B)
    job = env.client.post(f"/api/bluetooth/panels/{PANEL}/scan").json()
    assert report(env, job["job_id"], B, state="succeeded", result={"devices": [{"id": DEV, "name": "evil"}]}).status_code == 404
    assert env.broker.jobs[job["job_id"]].state == "queued"
    assert report(env, "nonexistent", state="succeeded").status_code == 404
    assert report(env, job["job_id"], state="exploded").status_code in (400, 404)


def test_a_finished_job_cannot_be_rewritten_by_a_late_report(env):
    online(env)
    job = env.client.post(f"/api/bluetooth/panels/{PANEL}/scan").json()
    poll(env)
    report(env, job["job_id"], state="failed", error={"code": "radio_busy", "message": "busy"})
    report(env, job["job_id"], state="succeeded", result={"devices": []})
    assert env.client.get(f"/api/bluetooth/panels/{PANEL}/jobs/{job['job_id']}").json()["state"] == "failed"


def test_a_job_for_a_panel_is_invisible_through_another_panels_url(env):
    online(env)
    online(env, B)
    job = env.client.post(f"/api/bluetooth/panels/{PANEL}/scan").json()
    assert env.client.get(f"/api/bluetooth/panels/{OTHER}/jobs/{job['job_id']}").status_code == 404


def test_unreached_commands_fail_with_a_sentence(env):
    online(env)
    job = env.client.post(f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/connect").json()
    env.clock.t += bt.QUEUED_TTL_S + 1
    got = env.client.get(f"/api/bluetooth/panels/{PANEL}/jobs/{job['job_id']}").json()
    assert got["state"] == "failed" and got["error"]["code"] == "panel_unreachable" and "online" in got["error"]["message"]
    assert [l["phase"] for l in env.audit_lines()] == ["requested", "finished"], "a timed-out audited action still gets its outcome line"
    env.clock.t -= bt.QUEUED_TTL_S
    assert env.client.post(f"/api/bluetooth/panels/{PANEL}/scan").status_code in (200, 409)


def test_a_long_poll_wakes_when_a_command_arrives(env):
    import threading
    import time
    online(env)
    out = {}
    with TestClient(env.app) as shared:                    # ONE event loop for both requests, as in production (an Event wakes only its own loop)
        def waiter():
            r = shared.post("/api/bluetooth/agent/poll", json={"snapshot": SNAP, "wait_s": 5}, headers=A)
            out["cmds"] = r.json()["commands"]
            out["took"] = time.monotonic() - t0

        t0 = time.monotonic()
        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.3)
        shared.post(f"/api/bluetooth/panels/{PANEL}/scan")
        t.join(timeout=4)
    assert not t.is_alive() and out["cmds"][0]["op"] == "scan" and out["took"] < 3, "woken by the command, not by the 5 s timeout"


# --- audit ---------------------------------------------------------------------------------------------------------------------------
def test_connect_forget_disconnect_and_update_are_audited_request_and_outcome(env):
    online(env)
    ids = {}
    for op, params in (("connect", None), ("disconnect", None), ("forget", None), ("update_start", {"confirm": True, "ack_no_undo": True})):
        j = env.client.post(f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/{op}", json=params or {}).json()
        ids[op] = j["job_id"]
        poll(env)
        report(env, j["job_id"], state="succeeded" if op != "update_start" else "failed",
               **({"result": {}} if op != "update_start" else {"error": {"code": "gate_failed", "message": "Not on its charger"}}))
    lines = env.audit_lines()
    assert [(l["action"], l["phase"]) for l in lines] == [(a, p) for a in ("connect", "disconnect", "forget", "update_start") for p in ("requested", "finished")]
    req = lines[0]
    assert req["actor"] == "u-admin" and req["role"] == "admin" and req["panel_id"] == PANEL and req["device_id"] == DEV and req["job_id"] == ids["connect"]
    assert lines[-1]["outcome"] == "failed" and lines[-1]["error_code"] == "gate_failed"
    assert oct(bt.audit_path().stat().st_mode & 0o777) == "0o600"


def test_reads_and_scans_are_not_audited(env):
    online(env)
    j = env.client.post(f"/api/bluetooth/panels/{PANEL}/scan").json()
    poll(env)
    report(env, j["job_id"], state="succeeded", result={"devices": []})
    j2 = env.client.post(f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/info").json()
    assert env.audit_lines() == [] and j2["op"] == "info"


def test_no_audit_line_means_no_action(env, monkeypatch):
    online(env)
    blocked = env.tmp / "is-a-dir"
    blocked.mkdir()
    monkeypatch.setenv("ZOE_BLUETOOTH_AUDIT_PATH", str(blocked))
    for op, params in (("connect", {}), ("forget", {}), ("update_start", {"confirm": True, "ack_no_undo": True})):
        r = env.client.post(f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/{op}", json=params)
        assert r.status_code == 503 and "audit" in r.json()["detail"], op
    assert env.broker.jobs == {} and poll(env) == []
    assert env.client.post(f"/api/bluetooth/panels/{PANEL}/scan").status_code == 200, "unaudited reads still work"


def test_the_audit_log_holds_no_addresses_tokens_or_free_text(env):
    online(env)
    j = env.client.post(f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/forget").json()
    poll(env)
    report(env, j["job_id"], state="failed", error={"code": "x", "message": "E6:11:22:33:44:55 tok-a <script>"})
    text = bt.audit_path().read_text()
    assert "E6:11" not in text and "tok-a" not in text and "<script>" not in text


# --- firmware update confirmation ---------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("body", [{}, {"confirm": True}, {"ack_no_undo": True}, {"confirm": "true", "ack_no_undo": True}, {"confirm": 1, "ack_no_undo": True}])
def test_a_firmware_update_must_be_explicitly_confirmed_including_no_undo(env, body):
    online(env)
    r = env.client.post(f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/update_start", json=body)
    assert r.status_code == 400 and "cannot be undone" in r.json()["detail"]
    assert env.broker.jobs == {} and env.audit_lines() == []


def test_a_confirmed_update_is_queued_with_only_the_confirmation_params(env):
    online(env)
    r = env.client.post(f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/update_start", json={"confirm": True, "ack_no_undo": True, "evil": "x" * 5000})
    assert r.status_code == 200
    assert poll(env)[0]["params"] == {"confirm": True, "ack_no_undo": True}


@pytest.mark.parametrize("body", [{}, {"confirm": "true", "ack_wipe": True}, {"confirm": 1, "ack_wipe": True}, {"replace": True},
                                  {"confirm": True}, {"confirm": True, "ack_wipe": "yes"}])
def test_secure_pairing_must_be_confirmed(env, body):
    online(env)
    r = env.client.post(f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/omi_bond", json=body)
    assert r.status_code == 400 and "confirmed" in r.json()["detail"]
    assert env.broker.jobs == {} and env.audit_lines() == []


def test_a_confirmed_pairing_is_audited_and_forwards_only_its_own_params(env):
    online(env)
    r = env.client.post(f"/api/bluetooth/panels/{PANEL}/devices/{DEV}/omi_bond",
                        json={"confirm": True, "ack_wipe": True, "replace": True, "force": True, "ack_no_undo": True,
                              "evil": "x" * 500})
    assert r.status_code == 200
    assert poll(env)[0]["params"] == {"confirm": True, "ack_wipe": True, "replace": True, "force": True}
    assert [(a["action"], a["phase"]) for a in env.audit_lines()] == [("omi_bond", "requested")]


# --- bounds on what a panel may say -----------------------------------------------------------------------------------------------------------
def test_panel_supplied_data_is_bounded(env):
    huge = {"agent": {"version": "v" * 5000}, "devices": [{"id": DEV, "name": "n" * 5000, "driver": "omi"} for _ in range(500)],
            "deep": {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": 1}}}}}}}}}
    online(env, snap=huge)
    view = env.client.get(f"/api/bluetooth/panels/{PANEL}").json()
    assert len(view["agent"]["version"]) <= 300 and len(view["devices"]) == 1 and len(view["devices"][0]["name"]) <= 300
    j = env.client.post(f"/api/bluetooth/panels/{PANEL}/scan").json()
    poll(env)
    report(env, j["job_id"], state="succeeded", result={"devices": [{"id": "x" * 400}] * 200, "k" * 200: 1})
    got = env.client.get(f"/api/bluetooth/panels/{PANEL}/jobs/{j['job_id']}").json()
    assert len(got["result"]["devices"]) <= 60 and all(len(k) <= 40 for k in got["result"])


def test_clean_drops_non_json_values():
    assert bt.clean({"a": object(), "b": {1, 2}, "c": [1, "x"]}) == {"a": None, "b": None, "c": [1, "x"]}


# --- the static rules -------------------------------------------------------------------------------------------------------------------------
def test_the_zoe_side_never_sees_or_stores_a_device_address():
    import re
    from pathlib import Path
    for f in (Path(bt.__file__), Path(bt_router.__file__)):
        text = f.read_text()
        assert not re.search(r"\b[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}\b", text), f.name       # no MAC literal; the opaque 16-hex id is all it handles
