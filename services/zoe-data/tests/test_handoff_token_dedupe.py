"""The three one-time token modules are adapters over auth_handoff — wire unchanged.

``music_setup``, ``smart_home_setup`` and ``telegram_link`` each carried their own
HMAC + base64 + single-use ledger. They now delegate to
``auth_handoff.SignedTokens`` / ``SingleUseLedger`` / ``b64``. The REFERENCE
functions below are the pre-dedupe algorithms copied verbatim in substance, and
the tests pin byte equality: same secret + same clock + same nonce → the same
token, and each side verifies the other's.

Negative control: change the claim order, the separator or the signature
encoding in ``SignedTokens.mint`` and the byte-equality tests go red.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

import base64
import hashlib
import hmac
import importlib
import json
import sys

import auth_handoff
import music_setup
import smart_home_setup

NOW = 1_790_000_000
NONCE = "fixedNonce12"


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _ref_setup_token(secret: str, claims: dict, ttl: int) -> str:
    """Pre-dedupe music_setup.mint / smart_home_setup.mint."""
    payload = {**claims, "exp": NOW + ttl, "n": NONCE}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


@pytest.fixture
def frozen(monkeypatch):
    monkeypatch.setattr(auth_handoff.time, "time", lambda: float(NOW))
    monkeypatch.setattr(auth_handoff.secrets, "token_urlsafe", lambda n=None: NONCE)
    music_setup._TOKENS.ledger.spent.clear()
    smart_home_setup._TOKENS.ledger.spent.clear()


def test_music_token_is_byte_identical(frozen, monkeypatch):
    monkeypatch.setenv("ZOE_MUSIC_SETUP_SECRET", "music-secret")
    tok = music_setup.mint("spotify", "jason")["token"]
    assert tok == _ref_setup_token("music-secret", {"p": "spotify", "u": "jason"}, music_setup.SETUP_TTL_S)
    assert music_setup.verify(tok) == {"p": "spotify", "u": "jason", "exp": NOW + 900, "n": NONCE}


def test_home_token_is_byte_identical(frozen, monkeypatch):
    monkeypatch.setenv("ZOE_HOME_SETUP_SECRET", "home-secret")
    tok = smart_home_setup.mint("jason")["token"]
    assert tok == _ref_setup_token("home-secret", {"u": "jason"}, smart_home_setup.SETUP_TTL_S)


def test_secret_fallback_order_is_unchanged(frozen, monkeypatch):
    monkeypatch.delenv("ZOE_MUSIC_SETUP_SECRET", raising=False)
    monkeypatch.setenv("ZOE_INTERNAL_TOKEN", "internal")
    tok = music_setup.mint("tidal")["token"]
    assert tok == _ref_setup_token("internal", {"p": "tidal", "u": ""}, 900)
    monkeypatch.delenv("ZOE_INTERNAL_TOKEN")
    music_setup.mint("tidal")  # no key at all → a per-process random, written back
    import os
    assert len(os.environ["ZOE_MUSIC_SETUP_SECRET"]) == 64


def test_single_use_expiry_and_tamper_semantics(monkeypatch):
    monkeypatch.setenv("ZOE_MUSIC_SETUP_SECRET", "s")
    tok = music_setup.mint("qobuz")["token"]
    assert music_setup.verify(tok) and music_setup.verify(tok)  # verify does not spend
    assert music_setup.consume(tok) is not None
    assert music_setup.consume(tok) is None and music_setup.verify(tok) is None
    body, sig = tok.split(".")
    assert music_setup.verify(body + "." + sig[:-2] + "AA") is None
    monkeypatch.setenv("ZOE_HOME_SETUP_SECRET", "other")
    assert music_setup.verify(smart_home_setup.mint()["token"]) is None  # cross-flow tokens do not verify
    import time as _t
    real = _t.time
    exp_tok = music_setup.mint("qobuz")["token"]
    monkeypatch.setattr(auth_handoff.time, "time", lambda: real() + 901)
    assert music_setup.verify(exp_tok) is None


@pytest.fixture
def tl(monkeypatch):
    monkeypatch.setenv("ZOE_TELEGRAM_LINK_SECRET", "tg-secret")
    saved = sys.modules.pop("telegram_link", None)
    import telegram_link as m
    importlib.reload(m)
    yield m
    if saved is not None:
        sys.modules["telegram_link"] = saved
    else:
        sys.modules.pop("telegram_link", None)


def _ref_link_token(secret: bytes, user_id: str, exp: int) -> str:
    """Pre-dedupe telegram_link.make_link_token."""
    payload = f"{user_id}:{exp}".encode()
    sig = hmac.new(secret, payload, hashlib.sha256).digest()[:12]
    return base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")


def test_telegram_link_token_is_byte_identical(tl, monkeypatch):
    monkeypatch.setattr(tl.time, "time", lambda: float(NOW))
    tok = tl.make_link_token("jason")
    assert tok == _ref_link_token(b"tg-secret", "jason", NOW + tl.LINK_TOKEN_TTL_S)
    assert tl.verify_link_token(_ref_link_token(b"tg-secret", "amy", NOW + 60)) == "amy"


def test_telegram_ledger_is_the_shared_one(tl):
    assert isinstance(tl._LEDGER, auth_handoff.SingleUseLedger)
    assert tl._pending_sigs is tl._LEDGER.pending and tl._consumed_sigs is tl._LEDGER.spent
    tok = tl.make_link_token("jason")
    assert tl.verify_link_token(tok) == "jason" and tl.verify_link_token(tok) is None  # reserved
    tl.release_token(tok)
    assert tl.verify_link_token(tok) == "jason"
    tl.mark_token_consumed(tok)
    tl.release_token(tok)  # a release after the spend cannot revive it
    assert tl.verify_link_token(tok) is None


def test_one_copy_of_the_mechanics():
    """No module re-grows its own HMAC/base64/ledger."""
    from pathlib import Path
    svc = Path(__file__).resolve().parents[1]
    for rel in ("music_setup.py", "smart_home_setup.py"):
        src = (svc / rel).read_text()
        assert "hmac" not in src and "base64" not in src and "def _prune" not in src, rel
    tsrc = (svc / "telegram_link.py").read_text()
    assert "base64" not in tsrc and "def _prune" not in tsrc
    for rel in ("routers/music_setup.py", "routers/smart_home_setup.py"):
        assert "def _base_url" not in (svc / rel).read_text(), rel
