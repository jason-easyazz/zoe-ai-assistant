"""The kiosk credential: the panel's kiosk BROWSER is "the panel" by a credential minted from its device token, not by its address.

Design: docs/architecture/panel-remote-access.md section 4.2. Over a private link home or on another Wi-Fi the kiosk's address is no
longer the registered ``panels.ip_address``, so without this the panel silently becomes "a guest somewhere" (household lists go
read-only, the display identity disappears). The credential must: be flag-dark (LAN-identical while off), die with the device
token and with the panel row, never pass the Cloudflare tunnel, never be forgeable from a header, and confer only "this is the
panel" on a GUEST - never a member. All data here is invented; addresses are TEST-NET / the shared tailnet range.
"""
import hashlib
import time
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

import auth_handoff
import panel_kiosk_credential as kiosk
import panel_principal
from routers import lists, panel_auth, panel_kiosk
from test_household_lists_panel import GUEST, MEMBER, PANEL_IP, SqlDb

pytestmark = pytest.mark.ci_safe

TAILNET_IP = "100.101.102.103"    # the same panel, now reaching home over the private link
RAW_TOKEN = "kitchen-panel-device-token-for-tests"
OTHER_TOKEN = "another-panels-device-token-for-tests"


def _hash(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for name in ("ZOE_PANEL_KIOSK_CREDENTIAL", "ZOE_PANEL_KIOSK_SECRET", "ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE",
                 "ZOE_TRUSTED_PROXY_NETS", "ZOE_PANEL_KIOSK_TICKET_TTL_S", "ZOE_PANEL_KIOSK_CREDENTIAL_TTL_S"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ZOE_INTERNAL_TOKEN", "test-internal-token")
    monkeypatch.setitem(panel_auth._token_cache, _hash(RAW_TOKEN),
                        dict(panel_id="kitchen-panel", role="voice", scopes="[]", expires_at=None, revoked=0))
    monkeypatch.setitem(panel_auth._token_cache, _hash(OTHER_TOKEN),
                        dict(panel_id="hall-panel", role="voice", scopes="[]", expires_at=None, revoked=0))

    async def _bcast(*_a, **_k):
        return 1

    monkeypatch.setattr(lists.broadcaster, "broadcast", _bcast)


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("ZOE_PANEL_KIOSK_CREDENTIAL", "1")


@pytest.fixture
def db():
    d = SqlDb()
    d.panel()                                   # kitchen-panel, registered at its LAN address PANEL_IP
    d.lst("fam", "jo", name="Shopping")
    d.item("i1", "fam", "milk")
    d.item("i2", "fam", "eggs")
    return d


def _req(peer="127.0.0.1", real_ip=TAILNET_IP, cookie=None, extra=()):
    headers = [(b"x-session-id", b"guest-session")]
    if real_ip:
        headers.append((b"x-real-ip", real_ip.encode()))
    if cookie is not None:
        headers.append((b"cookie", ("%s=%s" % (kiosk.COOKIE_NAME, cookie)).encode()))
    headers.extend(extra)
    return Request(dict(type="http", method="GET", path="/", headers=headers, client=(peer, 1234)))


def _client(db):
    app = FastAPI()
    app.include_router(panel_kiosk.router)

    async def _db():
        yield db

    app.dependency_overrides[panel_kiosk.get_db] = _db
    return TestClient(app, base_url="https://zoe.test", client=("127.0.0.1", 50000), follow_redirects=False)


def _enter(client, ticket, nxt="/touch/home.html?panel_id=kitchen-panel&kiosk=1", headers=None):
    h = dict([("Content-Type", "application/x-www-form-urlencoded")])
    h.update(headers or dict())
    return client.post("/api/panels/kiosk-enter", content=urlencode(dict(ticket=ticket, next=nxt)), headers=h)


def _ticket(client, token=RAW_TOKEN, panel="kitchen-panel", headers=None):
    h = dict([("X-Device-Token", token)])
    h.update(headers or dict())
    return client.post("/api/panels/%s/kiosk-ticket" % panel, headers=h)


def _cookie_from(resp):
    raw = resp.headers.get("set-cookie", "")
    assert raw.startswith(kiosk.COOKIE_NAME + "="), raw
    return raw.split(";", 1)[0].split("=", 1)[1], raw.lower()


# ---- the whole flow ------------------------------------------------------------------------------------------------------------


async def test_the_gate_flow_makes_the_kiosk_the_panel_from_a_new_address(db, on):
    client = _client(db)
    r = _ticket(client)
    assert r.status_code == 200 and r.json()["enter_path"] == "/api/panels/kiosk-enter"
    resp = _enter(client, r.json()["ticket"])
    assert resp.status_code == 303
    assert resp.headers["location"] == "/touch/home.html?panel_id=kitchen-panel&kiosk=1"
    assert resp.headers["cache-control"] == "no-store"
    value, attrs = _cookie_from(resp)
    for attr in ("httponly", "secure", "samesite=strict", "path=/", "max-age="):
        assert attr in attrs, attrs
    assert "domain=" not in attrs  # host-only: never sent to another host name
    # From the tailnet address (not the registered LAN one), through our proxy: the panel.
    assert await panel_principal.is_panel_guest(_req(cookie=value), GUEST, db) is True
    # NEGATIVE CONTROL: the same request without the credential is a guest somewhere, as before.
    assert await panel_principal.is_panel_guest(_req(), GUEST, db) is False


async def test_end_to_end_household_list_item_from_the_tailnet_needs_the_credential(db, on):
    client = _client(db)
    value, _ = _cookie_from(_enter(client, _ticket(client).json()["ticket"]))
    with pytest.raises(HTTPException) as e:
        await lists.delete_item("shopping", "fam", "i1", user=GUEST, db=db, request=_req())
    assert e.value.status_code == 403
    assert await lists.delete_item("shopping", "fam", "i1", user=GUEST, db=db, request=_req(cookie=value)) == dict(ok=True)
    assert db.live_items("fam") == ["eggs"]


# ---- LAN-identical while the flag is off ------------------------------------------------------------------------------------


async def test_flag_off_endpoints_404_and_a_presented_credential_is_ignored(db):
    client = _client(db)
    assert _ticket(client).status_code == 404
    assert _enter(client, "anything").status_code == 404
    valid = kiosk.mint_credential("kitchen-panel", _hash(RAW_TOKEN))   # a genuine credential minted while the flag was on
    assert await panel_principal.is_panel_guest(_req(cookie=valid), GUEST, db) is False
    # ...and the LAN kiosk is still the panel by its registered address, exactly as before
    assert await panel_principal.is_panel_guest(_req(real_ip=PANEL_IP), GUEST, db) is True


# ---- what the credential is NOT ---------------------------------------------------------------------------------------------


async def test_forged_addresses_without_the_credential_are_never_the_panel(db, on):
    direct_forger = _req(peer="192.0.2.77", real_ip=PANEL_IP)                  # a LAN device straight to :8000
    assert await panel_principal.is_panel_guest(direct_forger, GUEST, db) is False
    xff = Request(dict(type="http", method="GET", path="/", client=("192.0.2.77", 1),
                       headers=[(b"x-forwarded-for", PANEL_IP.encode()), (b"x-session-id", b"s")]))
    assert await panel_principal.is_panel_guest(xff, GUEST, db) is False


async def test_never_over_the_public_tunnel(db, on):
    client = _client(db)
    for header in ("cf-connecting-ip", "cf-ray", "cf-visitor"):
        assert _ticket(client, headers=dict([(header, "1")])).status_code == 403
        assert _enter(client, "x", headers=dict([(header, "1")])).status_code == 403
        valid = kiosk.mint_credential("kitchen-panel", _hash(RAW_TOKEN))
        req = _req(cookie=valid, extra=[(header.encode(), b"1")])
        assert await panel_principal.is_panel_guest(req, GUEST, db) is False
    # negative control: the same credential without the edge header IS the panel
    assert await panel_principal.is_panel_guest(_req(cookie=kiosk.mint_credential("kitchen-panel", _hash(RAW_TOKEN))), GUEST, db)


async def test_a_member_or_degraded_principal_gains_nothing(db, on):
    valid = kiosk.mint_credential("kitchen-panel", _hash(RAW_TOKEN))
    assert await panel_principal.is_panel_guest(_req(cookie=valid), MEMBER, db) is False
    degraded = dict(GUEST, auth_degraded=True)
    assert await panel_principal.is_panel_guest(_req(cookie=valid), degraded, db) is False


async def test_tampered_wrong_purpose_and_cross_panel_credentials_are_refused(db, on):
    valid = kiosk.mint_credential("kitchen-panel", _hash(RAW_TOKEN))
    body, sig = valid.split(".", 1)
    tampered = body + "." + ("A" if sig[0] != "A" else "B") + sig[1:]
    ticket = kiosk.mint_ticket("kitchen-panel", _hash(RAW_TOKEN))                 # a ticket is not a credential
    cross = kiosk.mint_credential("kitchen-panel", _hash(OTHER_TOKEN))            # another panel's token
    unknown = kiosk.mint_credential("kitchen-panel", _hash("never-issued"))
    for bad in (tampered, ticket, cross, unknown, "", "garbage"):
        assert await panel_principal.is_panel_guest(_req(cookie=bad), GUEST, db) is False, bad
    assert await panel_principal.is_panel_guest(_req(cookie=valid), GUEST, db) is True  # negative control


async def test_a_credential_signed_with_the_raw_shared_secret_is_refused(db, on):
    """Other handoff flows sign with the raw ZOE_INTERNAL_TOKEN; the kiosk key is derived, so their tokens never verify here."""
    raw_signer = auth_handoff.SignedTokens("UNUSED_TEST_SECRET", lambda: "test-internal-token")
    forged = raw_signer.mint(dict(p="kiosk-cred", pid="kitchen-panel", th=_hash(RAW_TOKEN)), 600)
    assert await panel_principal.is_panel_guest(_req(cookie=forged), GUEST, db) is False


# ---- it dies with the token and the panel row --------------------------------------------------------------------------------


async def test_revoking_or_expiring_the_device_token_kills_the_credential(db, on, monkeypatch):
    valid = kiosk.mint_credential("kitchen-panel", _hash(RAW_TOKEN))
    assert await panel_principal.is_panel_guest(_req(cookie=valid), GUEST, db) is True
    monkeypatch.setitem(panel_auth._token_cache[_hash(RAW_TOKEN)], "revoked", 1)
    assert await panel_principal.is_panel_guest(_req(cookie=valid), GUEST, db) is False
    monkeypatch.setitem(panel_auth._token_cache[_hash(RAW_TOKEN)], "revoked", 0)
    monkeypatch.setitem(panel_auth._token_cache[_hash(RAW_TOKEN)], "expires_at", "2020-01-01T00:00:00+00:00")
    assert await panel_principal.is_panel_guest(_req(cookie=valid), GUEST, db) is False


@pytest.mark.parametrize("active,allow_guest", [(0, 1), (1, 0)])
async def test_a_deactivated_or_guest_refusing_panel_is_not_the_panel(active, allow_guest, on):
    d = SqlDb()
    d.panel(active=active, allow_guest=allow_guest)
    valid = kiosk.mint_credential("kitchen-panel", _hash(RAW_TOKEN))
    assert await panel_principal.is_panel_guest(_req(cookie=valid), GUEST, d) is False


# ---- the mint and the enter -------------------------------------------------------------------------------------------------------


def test_mint_needs_this_panels_valid_token_and_an_active_panel(db, on, monkeypatch):
    client = _client(db)
    assert _ticket(client, token="").status_code == 403
    assert _ticket(client, token="never-issued").status_code == 403
    assert _ticket(client, token=OTHER_TOKEN).status_code == 403                    # hall-panel's token, kitchen-panel's id
    monkeypatch.setitem(panel_auth._token_cache[_hash(RAW_TOKEN)], "revoked", 1)
    assert _ticket(client).status_code == 403
    monkeypatch.setitem(panel_auth._token_cache[_hash(RAW_TOKEN)], "revoked", 0)
    db.con.execute("UPDATE panels SET is_active = 0")
    assert _ticket(client).status_code == 403
    db.con.execute("UPDATE panels SET is_active = 1")
    assert _ticket(client).status_code == 200                                         # negative control


def test_a_ticket_is_single_use_and_short_lived(db, on, monkeypatch):
    client = _client(db)
    ticket = _ticket(client).json()["ticket"]
    assert "set-cookie" in _enter(client, ticket).headers
    again = _enter(client, ticket)
    assert again.status_code == 303 and "set-cookie" not in again.headers          # spent: lands as a plain guest kiosk
    late = _ticket(client).json()["ticket"]
    real_time = time.time
    monkeypatch.setattr(auth_handoff.time, "time", lambda: real_time() + 3600)
    assert "set-cookie" not in _enter(client, late).headers


def test_a_ticket_minted_before_the_token_was_revoked_sets_nothing(db, on, monkeypatch):
    client = _client(db)
    ticket = _ticket(client).json()["ticket"]
    monkeypatch.setitem(panel_auth._token_cache[_hash(RAW_TOKEN)], "revoked", 1)
    assert "set-cookie" not in _enter(client, ticket).headers


@pytest.mark.parametrize("nxt", ["//evil.example/x", "https://evil.example/", "/api/panels", "/touch/../api/x",
                                 "/touch\\\\evil", "javascript:alert(1)", ""])
def test_enter_only_redirects_to_the_estate(db, on, nxt):
    client = _client(db)
    resp = _enter(client, _ticket(client).json()["ticket"], nxt=nxt)
    assert resp.headers["location"] == "/touch/home.html?panel_id=kitchen-panel&kiosk=1"


def test_the_credential_is_not_a_widening_of_internal_trust():
    """``_is_internal_request`` is untouched: loopback or the internal token, nothing else (the gate is a network client)."""
    import inspect

    import auth

    src = inspect.getsource(auth._is_internal_request)
    assert "cookie" not in src.lower() and "kiosk" not in src.lower()
    req = _req(peer="100.101.102.103", cookie=kiosk.mint_credential("kitchen-panel", _hash(RAW_TOKEN)))
    assert auth._is_internal_request(req) is False


def test_enter_refuses_an_oversized_body_without_buffering_it(db, on):
    client = _client(db)
    big = "ticket=" + "x" * 5000
    resp = client.post("/api/panels/kiosk-enter", content=big,
                       headers=dict([("Content-Type", "application/x-www-form-urlencoded")]))
    assert resp.status_code == 413 and "set-cookie" not in resp.headers
