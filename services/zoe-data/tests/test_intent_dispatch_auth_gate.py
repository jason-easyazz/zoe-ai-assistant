"""ZOE_INTENT_DISPATCH_REQUIRE_TOKEN — strict token gate for the brain's
write funnel (/api/system/intent-dispatch).

The body carries an arbitrary ``user_id`` under loopback trust — the same
impersonation class #1054 closed for the ``X-Zoe-User-Id`` header. The strict
gate ships DARK (flag default OFF) because the flue brain sidecar does not
send ``X-Internal-Token`` until its env is provisioned; unset-flag behavior is
byte-for-byte today's trust plus a readiness WARNING per unproven caller.
"""
import logging

import pytest

pytestmark = pytest.mark.ci_safe  # pure dependency logic via fake Request

import auth as auth_mod
from auth import require_intent_dispatch_auth
from fastapi import HTTPException
from starlette.datastructures import Headers


class _Req:
    """Minimal Request stand-in: client host + REAL Starlette Headers, so
    lookups are case-insensitive exactly like production Request.headers (a
    plain dict would silently diverge if a caller ever changed header casing)."""

    def __init__(self, host="127.0.0.1", token=None):
        self.client = type("C", (), {"host": host})()
        self.headers = Headers({"X-Internal-Token": token} if token else {})


def _set_token(monkeypatch, value):
    # module constant read at import in auth.py — patch the module attribute
    monkeypatch.setattr(auth_mod, "_ZOE_INTERNAL_TOKEN", value)


# ── flag OFF (default): today's trust + readiness warning ───────────────────

@pytest.mark.asyncio
async def test_flag_off_loopback_allowed_with_warning(monkeypatch, caplog):
    monkeypatch.delenv("ZOE_INTENT_DISPATCH_REQUIRE_TOKEN", raising=False)
    _set_token(monkeypatch, "sekrit")
    caplog.set_level(logging.WARNING, logger=auth_mod.__name__)
    await require_intent_dispatch_auth(_Req(host="127.0.0.1"))  # no exception
    assert any("WITHOUT a proven X-Internal-Token" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_flag_off_loopback_with_valid_token_no_warning(monkeypatch, caplog):
    monkeypatch.delenv("ZOE_INTENT_DISPATCH_REQUIRE_TOKEN", raising=False)
    _set_token(monkeypatch, "sekrit")
    caplog.set_level(logging.WARNING, logger=auth_mod.__name__)
    await require_intent_dispatch_auth(_Req(host="127.0.0.1", token="sekrit"))
    assert not caplog.records, "a token-proven caller must not warn"


@pytest.mark.asyncio
async def test_flag_off_token_unprovisioned_loopback_allowed_with_warning(monkeypatch, caplog):
    """The ACTUAL initial prod state: flag off AND no server-side token yet.
    Loopback must still be allowed (fail-open guarantee of the dark stage) and
    the readiness warning must still fire so the journal shows the gap."""
    monkeypatch.delenv("ZOE_INTENT_DISPATCH_REQUIRE_TOKEN", raising=False)
    _set_token(monkeypatch, "")
    caplog.set_level(logging.WARNING, logger=auth_mod.__name__)
    await require_intent_dispatch_auth(_Req(host="127.0.0.1"))  # no exception
    assert any("WITHOUT a proven X-Internal-Token" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_flag_off_external_still_403(monkeypatch):
    monkeypatch.delenv("ZOE_INTENT_DISPATCH_REQUIRE_TOKEN", raising=False)
    _set_token(monkeypatch, "sekrit")
    with pytest.raises(HTTPException) as exc:
        await require_intent_dispatch_auth(_Req(host="203.0.113.9"))
    assert exc.value.status_code == 403


# ── flag ON: token-proven only, loopback insufficient ────────────────────────

@pytest.mark.asyncio
async def test_flag_on_loopback_without_token_denied(monkeypatch):
    monkeypatch.setenv("ZOE_INTENT_DISPATCH_REQUIRE_TOKEN", "1")
    _set_token(monkeypatch, "sekrit")
    with pytest.raises(HTTPException) as exc:
        await require_intent_dispatch_auth(_Req(host="127.0.0.1"))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_flag_on_wrong_token_denied(monkeypatch):
    monkeypatch.setenv("ZOE_INTENT_DISPATCH_REQUIRE_TOKEN", "1")
    _set_token(monkeypatch, "sekrit")
    with pytest.raises(HTTPException):
        await require_intent_dispatch_auth(_Req(host="127.0.0.1", token="wrong"))


@pytest.mark.asyncio
async def test_flag_on_valid_token_allowed_even_nonloopback(monkeypatch):
    monkeypatch.setenv("ZOE_INTENT_DISPATCH_REQUIRE_TOKEN", "1")
    _set_token(monkeypatch, "sekrit")
    await require_intent_dispatch_auth(_Req(host="127.0.0.1", token="sekrit"))
    await require_intent_dispatch_auth(_Req(host="10.0.0.5", token="sekrit"))


@pytest.mark.asyncio
async def test_flag_on_but_token_unconfigured_denies_everything(monkeypatch):
    """Misconfiguration (flag on, no token provisioned) fails CLOSED — never
    silently falls back to loopback trust."""
    monkeypatch.setenv("ZOE_INTENT_DISPATCH_REQUIRE_TOKEN", "1")
    _set_token(monkeypatch, "")
    with pytest.raises(HTTPException):
        await require_intent_dispatch_auth(_Req(host="127.0.0.1", token="anything"))


# ── flag PARSING: the documented enable + rollback words must actually work ──

@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " 1 "])
@pytest.mark.asyncio
async def test_enable_words_engage_the_gate(monkeypatch, value):
    """Every value .env.example tells an operator they may enable with MUST engage
    the gate. If one silently didn't, the operator would believe the hole is closed
    while loopback impersonation stayed wide open — a false sense of security."""
    monkeypatch.setenv("ZOE_INTENT_DISPATCH_REQUIRE_TOKEN", value)
    _set_token(monkeypatch, "sekrit")
    with pytest.raises(HTTPException):
        await require_intent_dispatch_auth(_Req(host="127.0.0.1"))


@pytest.mark.parametrize("value", ["0", "false", "no", "off", ""])
@pytest.mark.asyncio
async def test_rollback_words_disengage_the_gate(monkeypatch, value):
    """ROLLBACK path. The documented rollback is 'unset the flag', but an operator
    under pressure may instead set =0/false. Those MUST disable the gate: if they
    didn't, rollback would appear applied while every caller kept 403ing — i.e. the
    outage would survive its own remedy."""
    monkeypatch.setenv("ZOE_INTENT_DISPATCH_REQUIRE_TOKEN", value)
    _set_token(monkeypatch, "sekrit")
    await require_intent_dispatch_auth(_Req(host="127.0.0.1"))  # loopback trusted again


@pytest.mark.asyncio
async def test_flag_read_lazily_not_cached_at_import(monkeypatch):
    """The flip is delivered by .env + restart, but the rollback story assumes no
    import-time caching of the flag. Pin that flipping the env alone changes
    behaviour with no re-import."""
    _set_token(monkeypatch, "sekrit")
    monkeypatch.setenv("ZOE_INTENT_DISPATCH_REQUIRE_TOKEN", "1")
    with pytest.raises(HTTPException):
        await require_intent_dispatch_auth(_Req(host="127.0.0.1"))
    monkeypatch.delenv("ZOE_INTENT_DISPATCH_REQUIRE_TOKEN")
    await require_intent_dispatch_auth(_Req(host="127.0.0.1"))  # same process, now open


# ── the same gate covers BOTH actor-asserting endpoints ──────────────────────

def test_delegate_sync_uses_the_same_gate():
    """delegate-sync also takes a body user_id under internal trust — the same
    impersonation class. Lock in that its dependency IS the flag-keyed gate,
    so the two endpoints can never silently diverge."""
    import inspect

    from routers.system import delegate_sync, intent_dispatch

    def _gate_of(fn):
        for p in inspect.signature(fn).parameters.values():
            d = p.default
            if hasattr(d, "dependency"):
                return d.dependency
        return None

    assert _gate_of(intent_dispatch) is auth_mod.require_intent_dispatch_auth
    assert _gate_of(delegate_sync) is auth_mod.require_intent_dispatch_auth
