"""Unit tests for the stateless Telegram link-token lifecycle (telegram_link.py)."""

import importlib
import sys

import pytest


@pytest.fixture
def tl(monkeypatch):
    # Pin a stable secret so tokens are deterministic across a reload.
    monkeypatch.setenv("ZOE_TELEGRAM_LINK_SECRET", "unit-test-secret")
    monkeypatch.delenv("ZOE_TELEGRAM_BOT_USERNAME", raising=False)
    # Save + restore the real module so our reloads never leak into other tests.
    saved = sys.modules.pop("telegram_link", None)
    import telegram_link as m

    importlib.reload(m)
    yield m
    if saved is not None:
        sys.modules["telegram_link"] = saved
    else:
        sys.modules.pop("telegram_link", None)


def test_token_roundtrip(tl):
    tok = tl.make_link_token("jason")
    assert tl.verify_link_token(tok) == "jason"
    # Deep-link/start-param safe: URL-safe charset, comfortably under 64 chars.
    assert all(c.isalnum() or c in "-_" for c in tok)
    assert len(tok) <= 64


def test_tampered_token_rejected(tl):
    tok = tl.make_link_token("jason")
    assert tl.verify_link_token(tok[:-3] + "AAA") is None
    assert tl.verify_link_token("garbage") is None
    assert tl.verify_link_token("") is None


def test_expired_token_rejected(tl):
    assert tl.verify_link_token(tl.make_link_token("jason", ttl=-1)) is None


def test_single_use_consume(tl):
    tok = tl.make_link_token("jason")
    # Non-consuming verify can be repeated (used by nothing in prod, but safe).
    assert tl.verify_link_token(tok) == "jason"
    assert tl.verify_link_token(tok) == "jason"
    # First redemption consumes it; a second scan of the SAME token is rejected
    # (kiosk QR can't be hijacked by a later scanner).
    assert tl.verify_link_token(tok, consume=True) == "jason"
    assert tl.verify_link_token(tok, consume=True) is None
    assert tl.verify_link_token(tok) is None  # stays dead even non-consuming


def test_wrong_secret_cannot_verify(tl, monkeypatch):
    tok = tl.make_link_token("jason")
    # Reload the module under a DIFFERENT secret → prior token must not verify.
    monkeypatch.setenv("ZOE_TELEGRAM_LINK_SECRET", "a-different-secret")
    del sys.modules["telegram_link"]
    import telegram_link as other

    importlib.reload(other)
    assert other.verify_link_token(tok) is None


def test_bot_username_and_deep_link(tl):
    assert tl.get_bot_username() is None
    assert tl.build_deep_link("abc") is None  # unknown bot → no deep link
    tl.set_bot_username("@ZoeFamilyBot")
    assert tl.get_bot_username() == "ZoeFamilyBot"  # @ stripped
    assert tl.build_deep_link("abc") == "https://t.me/ZoeFamilyBot?start=abc"


def test_env_username_overrides_when_unregistered(tl, monkeypatch):
    monkeypatch.setenv("ZOE_TELEGRAM_BOT_USERNAME", "EnvBot")
    del sys.modules["telegram_link"]
    import telegram_link as m

    importlib.reload(m)
    assert m.get_bot_username() == "EnvBot"


def test_qr_svg_is_inline_or_none(tl):
    # segno may or may not be installed; both are valid (progressive enhancement).
    svg = tl.make_qr_svg("https://t.me/ZoeFamilyBot?start=abc")
    assert svg is None or svg.lstrip().startswith("<svg")
