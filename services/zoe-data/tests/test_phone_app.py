"""phone_app (M0 box side of Zoe's own iPhone app): pairing by QR code and push-token registration, flag-dark ZOE_PHONE_APP.

Runs the real 0060 migration on SQLite and drives the real router through FastAPI with the auth dependency overridden. Synthetic
accounts only. Falsifiable pins:
  * make a pairing code reusable -> test_a_code_pairs_one_phone_once goes red;
  * store the device credential instead of its hash -> test_no_secret_is_stored goes red;
  * drop the adults-only check -> test_children_and_guests_cannot_pair goes red;
  * accept any topic -> test_push_registration_rules goes red;
  * drop the flag gate -> test_flag_off_is_404_everywhere goes red.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import importlib.util
import sqlite3
import sys
from pathlib import Path

import aiosqlite
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import phone_app  # noqa: E402
from database import get_db  # noqa: E402
from panel_principal import require_member  # noqa: E402

pytestmark = pytest.mark.ci_safe

ADULT, KID = "ann-0001", "kid-0002"
BUNDLE = "life.zoe.lab"
def _p256_x963() -> str:
    """A real P-256 public key in the X9.63 form the Secure Enclave exports (synthetic, made per run)."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    pub = ec.generate_private_key(ec.SECP256R1()).public_key()
    return base64.b64encode(pub.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)).decode()


PUBKEY = _p256_x963()
APNS = "ab" * 32


def _migrate(path: str) -> None:
    engine = sa.create_engine(f"sqlite:///{path}")
    spec = importlib.util.spec_from_file_location("mig_0060", ROOT / "alembic/versions/0060_phone_app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        mod.upgrade()
    engine.dispose()


@pytest.fixture
def box(monkeypatch, tmp_path):
    path = str(tmp_path / "zoe.db")
    _migrate(path)
    phone_app.reset()
    monkeypatch.setenv("ZOE_PHONE_APP", "on")
    monkeypatch.setenv("ZOE_PHONE_APP_BASE_URL", "https://zoe.example.test/")
    monkeypatch.setenv("ZOE_PHONE_APP_CA_SHA256", "AA:" * 31 + "AA")
    monkeypatch.setenv("ZOE_PHONE_APP_BUNDLE_ID", BUNDLE)
    state = {"user": {"user_id": ADULT, "role": "user"}}

    import manner_block

    async def eligible(uid, db=None):
        return uid == ADULT

    monkeypatch.setattr(manner_block, "eligible", eligible)

    from routers.phone_app import router

    app = FastAPI()
    app.include_router(router)

    async def db_dep():
        conn = await aiosqlite.connect(path)
        try:
            yield conn
        finally:
            await conn.close()

    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[require_member] = lambda: state["user"]
    state["client"] = TestClient(app)
    state["path"] = path
    return state


def _pair(box, name="Ann's iPhone"):
    c = box["client"]
    r = c.post("/api/phone/pair/start")
    assert r.status_code == 200, r.text
    code = phone_app.decode_payload(r.json()["pair_payload"])["c"]
    r = c.post("/api/phone/pair/complete", json={"code": code, "device_name": name, "platform": "ios", "public_key": PUBKEY,
                                                  "app_version": "0.1"})
    return code, r


def test_the_qr_payload_carries_the_box_url_code_and_ca_fingerprint(box):
    r = box["client"].post("/api/phone/pair/start")
    body = r.json()
    assert body["expires_in_s"] == 300
    p = phone_app.decode_payload(body["pair_payload"])
    assert p["v"] == 1 and p["u"] == "https://zoe.example.test" and p["fp"] == "aa" * 32 and len(p["c"]) >= 40


def test_a_code_pairs_one_phone_once(box):
    code, r = _pair(box)
    assert r.status_code == 200
    out = r.json()
    assert set(out) == {"device_id", "device_token"}
    again = box["client"].post("/api/phone/pair/complete", json={"code": code, "device_name": "x", "platform": "ios",
                                                                  "public_key": PUBKEY})
    assert again.status_code == 401
    me = box["client"].get("/api/phone/me", headers={"X-Zoe-Phone-Token": out["device_token"]})
    assert me.status_code == 200 and me.json()["device_id"] == out["device_id"]


def test_an_expired_or_unknown_code_fails_the_same_way(box):
    r = box["client"].post("/api/phone/pair/complete", json={"code": "nope", "device_name": "x", "platform": "ios",
                                                              "public_key": PUBKEY})
    assert r.status_code == 401
    out = phone_app.start_pairing(ADULT, now=1000.0)
    code = phone_app.decode_payload(out["pair_payload"])["c"]

    async def run():
        async with aiosqlite.connect(box["path"]) as db:
            with pytest.raises(phone_app.PhoneAppError) as e:
                await phone_app.complete_pairing(db, code=code, device_name="x", platform="ios", public_key=PUBKEY,
                                                 now=1000.0 + phone_app.PAIR_TTL_S + 1)
            return e.value.status

    assert asyncio.run(run()) == 401


def test_bad_keys_and_platforms_are_refused_before_the_code_is_spent(box):
    r = box["client"].post("/api/phone/pair/start")
    code = phone_app.decode_payload(r.json()["pair_payload"])["c"]
    bad = box["client"].post("/api/phone/pair/complete", json={"code": code, "device_name": "x", "platform": "ios",
                                                                "public_key": base64.b64encode(b"\x04" + b"1" * 10).decode()})
    assert bad.status_code == 400
    bad = box["client"].post("/api/phone/pair/complete", json={"code": code, "device_name": "x", "platform": "android",
                                                                "public_key": PUBKEY})
    assert bad.status_code == 400
    ok = box["client"].post("/api/phone/pair/complete", json={"code": code, "device_name": "x", "platform": "ios",
                                                               "public_key": PUBKEY})
    assert ok.status_code == 200


def test_children_and_guests_cannot_pair(box):
    box["user"] = {"user_id": KID, "role": "child"}
    assert box["client"].post("/api/phone/pair/start").status_code == 403


def _real_auth_app(box, principal):
    """The router with the REAL require_member; only get_current_user (zoe-auth) is replaced by ``principal``."""
    from auth import get_current_user
    from routers.phone_app import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user] = lambda: principal
    return TestClient(app)


@pytest.mark.parametrize("principal", [
    {"user_id": "guest", "role": "guest"},
    {"user_id": "guest-abc", "role": "guest"},
    {"user_id": "zoe-panel-1", "role": "guest", "panel_id": "zoe-panel-1", "device_token_role": "voice-daemon"},
    {"user_id": ADULT, "role": "user", "panel_id": "zoe-panel-1", "device_token_role": "voice-daemon"},  # token bound to a member
])
def test_the_panel_principal_and_guests_cannot_pair(box, principal):
    c = _real_auth_app(box, principal)
    assert c.post("/api/phone/pair/start").status_code == 403
    assert c.get("/api/phone/devices").status_code == 403


def test_a_point_off_the_curve_is_not_a_key():
    assert phone_app.valid_public_key(PUBKEY)
    assert not phone_app.valid_public_key(base64.b64encode(b"\x04" + bytes(range(64))).decode())
    assert not phone_app.valid_public_key(base64.b64encode(b"\x30\x59" + b"\x00" * 89).decode())


def test_no_secret_is_stored(box):
    code, r = _pair(box)
    token = r.json()["device_token"]
    box["client"].put("/api/phone/push", headers={"X-Zoe-Phone-Token": token},
                      json={"push_type": "alert", "token": APNS, "environment": "production", "topic": BUNDLE})
    with sqlite3.connect(box["path"]) as conn:
        dump = "\n".join(conn.iterdump())
    assert token not in dump and code not in dump
    assert phone_app._sha(token) in dump


def test_push_registration_rules(box):
    _, r = _pair(box)
    h = {"X-Zoe-Phone-Token": r.json()["device_token"]}
    c = box["client"]
    ok = [("alert", BUNDLE), ("voip", BUNDLE + ".voip"), ("pushtotalk", BUNDLE + ".voip-ptt"),
          ("liveactivity", BUNDLE + ".push-type.liveactivity")]
    for push_type, topic in ok:
        assert c.put("/api/phone/push", headers=h, json={"push_type": push_type, "token": APNS, "environment": "sandbox",
                                                          "topic": topic}).status_code == 200
    for body in (
        {"push_type": "voip", "token": APNS, "environment": "sandbox", "topic": BUNDLE},           # wrong suffix
        {"push_type": "alert", "token": APNS, "environment": "sandbox", "topic": "com.other.app"},  # another app
        {"push_type": "alert", "token": "not-hex", "environment": "sandbox", "topic": BUNDLE},
        {"push_type": "alert", "token": APNS, "environment": "staging", "topic": BUNDLE},
        {"push_type": "sms", "token": APNS, "environment": "sandbox", "topic": BUNDLE},
    ):
        assert c.put("/api/phone/push", headers=h, json=body).status_code == 400, body
    assert c.put("/api/phone/push", json={"push_type": "alert", "token": APNS, "environment": "sandbox",
                                          "topic": BUNDLE}).status_code == 401
    assert c.delete("/api/phone/push/voip", headers=h).json()["removed"] is True


def test_members_list_and_revoke_their_own_phones_only(box):
    _, r = _pair(box)
    dev = r.json()
    h = {"X-Zoe-Phone-Token": dev["device_token"]}
    box["client"].put("/api/phone/push", headers=h, json={"push_type": "alert", "token": APNS, "environment": "sandbox",
                                                           "topic": BUNDLE})
    listed = box["client"].get("/api/phone/devices").json()["devices"]
    assert [d["device_id"] for d in listed] == [dev["device_id"]] and listed[0]["push_registered"] is True
    assert "token" not in str(listed).replace("push_registered", "")
    box["user"] = {"user_id": "bob-0003", "role": "user"}
    assert box["client"].delete(f"/api/phone/devices/{dev['device_id']}").status_code == 404
    box["user"] = {"user_id": ADULT, "role": "user"}
    assert box["client"].delete(f"/api/phone/devices/{dev['device_id']}").status_code == 200
    assert box["client"].get("/api/phone/me", headers=h).status_code == 401
    with sqlite3.connect(box["path"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM phone_push_tokens").fetchone()[0] == 0


def test_an_admin_may_revoke_any_phone(box):
    _, r = _pair(box)
    box["user"] = {"user_id": "owner-0009", "role": "admin"}
    assert box["client"].delete(f"/api/phone/devices/{r.json()['device_id']}").status_code == 200


def test_a_push_registration_racing_a_revoke_stores_nothing(box):
    """The request authenticated (it holds a Device), then the phone was revoked before its insert ran (Codex, #2146)."""
    _, r = _pair(box)
    dev_id = r.json()["device_id"]
    dev = phone_app.Device(dev_id, ADULT, "Ann's iPhone", "ios")
    assert box["client"].delete(f"/api/phone/devices/{dev_id}").status_code == 200

    async def run():
        async with aiosqlite.connect(box["path"]) as db:
            with pytest.raises(phone_app.PhoneAppError) as e:
                await phone_app.register_push(db, dev, push_type="alert", token=APNS, environment="production", topic=BUNDLE)
            return e.value.status

    assert asyncio.run(run()) == 401
    with sqlite3.connect(box["path"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM phone_push_tokens").fetchone()[0] == 0


def test_the_pairing_dependency_only_peeks(box):
    """``require_pairing_code`` checks the code without spending it; only ``complete_pairing`` consumes it."""
    out = phone_app.start_pairing(ADULT)
    code = phone_app.decode_payload(out["pair_payload"])["c"]
    assert phone_app.peek_code(code) == ADULT and phone_app.peek_code(code) == ADULT
    assert phone_app.peek_code("nope") is None
    assert phone_app.peek_code(code, now=10**12) is None


def test_pending_codes_and_box_url_are_bounded(box, monkeypatch):
    for _ in range(phone_app.MAX_PENDING_CODES_PER_MEMBER):
        assert box["client"].post("/api/phone/pair/start").status_code == 200
    assert box["client"].post("/api/phone/pair/start").status_code == 429
    phone_app.reset()
    monkeypatch.setenv("ZOE_PHONE_APP_BASE_URL", "")
    assert box["client"].post("/api/phone/pair/start").status_code == 503


@pytest.mark.parametrize("value", ["", "off", "1", "true", "yes"])
def test_flag_off_is_404_everywhere(box, monkeypatch, value):
    monkeypatch.setenv("ZOE_PHONE_APP", value)
    if value.strip().lower() == "on":
        pytest.skip("on")
    # the REAL auth dependency and no DB override: the router-level gate answers first, so neither ever runs
    from routers.phone_app import router

    app = FastAPI()
    app.include_router(router)
    c = TestClient(app)
    for method, url in (("post", "/api/phone/pair/start"), ("post", "/api/phone/pair/complete"), ("get", "/api/phone/me"),
                        ("put", "/api/phone/push"), ("delete", "/api/phone/push/alert"), ("get", "/api/phone/devices"),
                        ("delete", "/api/phone/devices/x")):
        kw = {"json": {}} if method in ("post", "put") else {}
        assert getattr(c, method)(url, **kw).status_code == 404, url
