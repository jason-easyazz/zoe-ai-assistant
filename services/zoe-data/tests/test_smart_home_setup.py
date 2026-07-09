"""Add-a-device setup: the HMAC token module + the token-gated phone guide.

Mirrors the music-setup token contract. Home Assistant runs headless with no
config-flow/pairing endpoint on the bridge, so the phone endpoint is deliberately
read-only guidance — these tests pin that it never leaks the guide without a valid
one-time token, and that the token is tamper-proof + single-use.
"""
from __future__ import annotations

import pytest
from fastapi import Response

import smart_home_setup
from routers.smart_home_setup import setup_info


def test_token_round_trip_and_single_use():
    tok = smart_home_setup.mint("user-1")["token"]
    payload = smart_home_setup.verify(tok)
    assert payload is not None and payload["u"] == "user-1"
    # consume() spends it once; a second verify/consume fails.
    assert smart_home_setup.consume(tok) is not None
    assert smart_home_setup.verify(tok) is None
    assert smart_home_setup.consume(tok) is None


def test_token_tamper_is_rejected():
    tok = smart_home_setup.mint()["token"]
    body, sig = tok.split(".", 1)
    forged = body + "." + ("A" * len(sig))
    assert smart_home_setup.verify(forged) is None
    assert smart_home_setup.verify("not-a-token") is None
    assert smart_home_setup.verify("") is None


def test_token_expiry(monkeypatch):
    tok = smart_home_setup.mint()["token"]
    # Jump past the TTL — the token must no longer verify.
    real = smart_home_setup.time.time()
    monkeypatch.setattr(smart_home_setup.time, "time",
                        lambda: real + smart_home_setup.SETUP_TTL_S + 1)
    assert smart_home_setup.verify(tok) is None


@pytest.mark.asyncio
async def test_setup_info_requires_valid_token():
    bad = await setup_info(Response(), token="garbage")
    assert bad["ok"] is False and "expired" in bad["reason"].lower()


@pytest.mark.asyncio
async def test_setup_info_returns_guidance_and_honest_note():
    tok = smart_home_setup.mint()["token"]
    info = await setup_info(Response(), token=tok)
    assert info["ok"] is True
    ids = {t["id"] for t in info["device_types"]}
    assert {"light", "plug", "speaker", "sensor"} <= ids
    for t in info["device_types"]:
        assert t["steps"], t["id"]  # every type has actionable steps
    # No faked automation — the note is honest about the manual hand-off + never
    # mentions "Home Assistant" jargon.
    assert "can't finish pairing" in info["note"].lower()
    assert "home assistant" not in info["note"].lower()


@pytest.mark.asyncio
async def test_setup_info_is_single_use():
    # The guide fetch SPENDS the token — a re-opened/photographed QR can't re-fetch.
    tok = smart_home_setup.mint()["token"]
    assert (await setup_info(Response(), token=tok))["ok"] is True
    again = await setup_info(Response(), token=tok)
    assert again["ok"] is False and "expired" in again["reason"].lower()


@pytest.mark.asyncio
async def test_setup_info_is_never_cached():
    # Token-gated + single-use → the guide must carry no-store so a cached copy
    # can't be replayed after the token is spent.
    tok = smart_home_setup.mint()["token"]
    for call_token in (tok, "garbage"):  # both success and expired paths
        resp = Response()
        await setup_info(resp, token=call_token)
        assert "no-store" in resp.headers["Cache-Control"]
        assert resp.headers["Pragma"] == "no-cache"
