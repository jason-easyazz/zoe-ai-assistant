"""POST /api/voice/livekit-audio + /livekit-cancel — the speech-to-brain
pipeline is never reachable anonymously, and a BAD credential is never treated
as no credential.

The gap this pins (found 2026-08-05 while fixing #1649). Both endpoints resolved
their caller through a `_get_current_user_soft()` helper in `voice_livekit.py`:

    try:
        return await get_current_user(request)
    except Exception:
        return {"user_id": "guest", "role": "guest"}

Two distinct problems, both confirmed against the LIVE box before any code
changed (`curl` to :8000):

  * `POST /livekit-audio` with no credential at all → **HTTP 200**. That is the
    full STT → `brain_oneshot` → TTS pipeline on the Jetson, handed to any LAN
    device — free GPU compute, plus a brain turn attributed to `guest`.
    (Contrast control on the same box: `/api/voice/announcements`, which is
    gated by `_require_voice_auth`, → 401.)
  * `POST /livekit-cancel` with `X-Session-ID: totally-bogus-session-id` →
    **HTTP 200**. `get_current_user` raises 401 for an unknown/expired session;
    the bare `except Exception` swallowed it, so an INVALID credential and NO
    credential were indistinguishable — strictly worse than a deliberate
    resolve-to-guest default.

Falsifiable pins — break the thing, these go red:
  * drop the gate back to a soft/no-op resolver and
    `test_anonymous_caller_is_refused_*` return 200;
  * re-wrap the resolver in `except Exception` and
    `test_invalid_or_expired_session_is_refused_*` return 200 — that test is
    the swallow bug's specific control;
  * drop the validated-guest branch and the PANEL breaks
    (`test_validated_guest_session_is_accepted_*`) — that branch is the
    acceptance constraint, not an oversight;
  * take the pipeline's user from a form field and
    `test_brain_turn_is_attributed_to_the_authenticated_principal` goes red.
"""
from __future__ import annotations

import inspect
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import routers.voice_livekit as voice_livekit
import routers.voice_tts as voice_tts
from auth import get_current_user

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane


def _invalid_session():
    """Exactly what `auth.get_current_user` does for an unknown or expired
    session id (`auth.py`: `raise HTTPException(401, "Invalid or expired
    session")`). The old helper caught this and returned guest."""
    raise HTTPException(status_code=401, detail="Invalid or expired session")


def _app(*, device=None, user=None, user_raises=False) -> FastAPI:
    """The router under test with its two upstream resolvers stubbed.

    `_validate_device_token` and `get_current_user` are the INPUTS to the gate;
    everything the gate then decides is the code under test.
    """
    app = FastAPI()
    app.include_router(voice_livekit.router)
    app.dependency_overrides[voice_tts._validate_device_token] = lambda: device
    if user_raises:
        app.dependency_overrides[get_current_user] = _invalid_session
    else:
        app.dependency_overrides[get_current_user] = lambda: (
            user if user is not None else {"user_id": "guest", "role": "guest"}
        )
    return app


def _post_audio(client: TestClient, **kwargs):
    return client.post(
        "/api/voice/livekit-audio",
        files={"audio": ("ptt.webm", b"\x00\x01", "audio/webm")},
        data={"session_id": "sess-lk"},
        **kwargs,
    )


def _post_cancel(client: TestClient, **kwargs):
    return client.post("/api/voice/livekit-cancel", json={"session_id": "sess-lk"}, **kwargs)


@pytest.fixture
def _no_pipeline(monkeypatch):
    """Neutralise the STT stage so an ACCEPTED request returns immediately.

    An empty transcript short-circuits before `brain_oneshot` and before TTS, so
    the allow-path tests assert the GATE and never load a model. Any test that
    wants the brain stage opts in by re-patching.
    """
    async def _empty(_path):
        return ""

    monkeypatch.setattr(voice_tts, "_transcribe_audio", _empty)
    return monkeypatch


# ── the gap itself ──────────────────────────────────────────────────────────

def test_anonymous_caller_is_refused_audio():
    """No session header, no device token — the exact `curl` that returned 200
    and a working speech-to-brain pipeline on the live box."""
    resp = _post_audio(TestClient(_app()))
    assert resp.status_code == 401
    assert "transcript" not in resp.json()


def test_anonymous_caller_is_refused_cancel():
    resp = _post_cancel(TestClient(_app()))
    assert resp.status_code == 401


def test_invalid_or_expired_session_is_refused_audio():
    """THE SWALLOW BUG'S CONTROL. `get_current_user` raises 401 for a session id
    zoe-auth rejects; the old `except Exception` turned that into a guest and a
    200. A bad credential must fail LOUDER than no credential, never the same."""
    app = _app(user_raises=True)
    resp = _post_audio(
        TestClient(app), headers={"X-Session-ID": "totally-bogus-session-id"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired session"


def test_invalid_or_expired_session_is_refused_cancel():
    app = _app(user_raises=True)
    resp = _post_cancel(
        TestClient(app), headers={"X-Session-ID": "totally-bogus-session-id"}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid or expired session"


def test_invalid_device_token_alone_is_refused():
    """A bogus X-Device-Token resolves to None, and header PRESENCE must not be
    mistaken for a credential — only a header the server validated counts."""
    resp = _post_audio(
        TestClient(_app(device=None)), headers={"X-Device-Token": "not-a-real-token"}
    )
    assert resp.status_code == 401


def test_blank_session_header_is_not_a_credential():
    """An empty X-Session-ID is what a browser with no stored session sends —
    `getSessionId()` returns '' — and must not satisfy the gate by presence."""
    resp = _post_audio(TestClient(_app()), headers={"X-Session-ID": "   "})
    assert resp.status_code == 401


# ── who IS allowed ──────────────────────────────────────────────────────────

def test_device_token_is_accepted(_no_pipeline):
    """The Pi voice daemon / provisioned kiosk lane."""
    app = _app(device={"panel_id": "zoe-touch-pi", "user_id": "jason", "role": "kiosk"})
    resp = _post_audio(TestClient(app), headers={"X-Device-Token": "raw"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "transcript": "", "audio_base64": None}


def test_device_token_plus_invalid_session_is_refused_documents_precedence():
    """PINS CURRENT BEHAVIOUR, and it is deliberately not the "OR" the docstring
    of the gate reads like — surfaced by cross-review on #1652.

    `_validate_device_token` and `get_current_user` are declared as SIBLING
    `Depends`, so FastAPI resolves BOTH before the gate body runs. A caller with
    a VALID device token but a present-and-invalid `X-Session-ID` therefore gets
    `get_current_user`'s 401 before `if device:` is ever reached — the device
    token does not rescue it.

    Why that is acceptable today rather than a bug to fix here:
      * no real client sends both — the Pi daemon sends `X-Device-Token` only
        (and does not call these endpoints at all), browsers send session only;
      * it is the exact idiom already shipping in `voice_tts._require_voice_auth`
        for every other voice endpoint, which this gate deliberately mirrors;
      * the OLD code mis-served this combination as `guest`, so nothing that
        worked regresses.

    This test exists so the consolidation recorded in `routers/AGENTS.md` (fold
    the two gates into one shared helper) CHANGES this on purpose and not by
    accident: if that work resolves the session lazily only when there is no
    valid device token, this assertion flips to 200 and must be updated with it.
    """
    app = _app(device={"panel_id": "zoe-touch-pi", "user_id": "jason"}, user_raises=True)
    client = TestClient(app)
    assert _post_audio(
        client, headers={"X-Device-Token": "raw", "X-Session-ID": "stale-or-bogus"}
    ).status_code == 401
    assert _post_cancel(
        client, headers={"X-Device-Token": "raw", "X-Session-ID": "stale-or-bogus"}
    ).status_code == 401


def test_signed_in_user_is_accepted(_no_pipeline):
    app = _app(user={"user_id": "jason", "role": "admin"})
    resp = _post_audio(TestClient(app), headers={"X-Session-ID": "sess-real"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_validated_guest_session_is_accepted_audio(_no_pipeline):
    """THE PANEL. touch/voice.html and dist/voice.html run on a kiosk that boots
    as a guest (`/api/auth/guest`) and holds no device token, so a validated
    guest session is its only credential. Verified live: zoe-auth's
    `/api/auth/user` answers 200 for a freshly minted guest session, so
    `get_current_user` resolves it rather than 401ing. Tightening this to reject
    guests breaks the voice turn on the panel."""
    app = _app(user={"user_id": "guest", "role": "guest"})
    resp = _post_audio(TestClient(app), headers={"X-Session-ID": "kiosk-guest-sid"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_validated_guest_session_is_accepted_cancel():
    app = _app(user={"user_id": "guest", "role": "guest"})
    resp = _post_cancel(TestClient(app), headers={"X-Session-ID": "kiosk-guest-sid"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


# ── the degraded-auth hole in the validated-guest branch ────────────────────
#
# The guest branch above substitutes HEADER PRESENCE for "the server validated
# this session", which is sound only while the server really did validate it.
# Under `ZOE_AUTH_FAIL_CLOSED=false` (an explicit operator opt-out) an
# auth-service outage makes `get_current_user` resolve ANY nonblank header to a
# plain guest, so that substitution would hand the GPU pipeline back to
# anonymous LAN callers for the length of the outage — the same hole this PR
# exists to close, reachable through a config flag instead of a bug.

def test_degraded_auth_guest_is_refused_audio():
    """Auth service down + fail-OPEN: a made-up header must NOT buy the pipeline."""
    app = _app(user={"user_id": "guest", "role": "guest", "auth_degraded": True})
    resp = _post_audio(TestClient(app), headers={"X-Session-ID": "anything-at-all"})
    assert resp.status_code == 503
    assert resp.json()["detail"] == "Authentication service unavailable"


def test_degraded_auth_guest_is_refused_cancel():
    app = _app(user={"user_id": "guest", "role": "guest", "auth_degraded": True})
    resp = _post_cancel(TestClient(app), headers={"X-Session-ID": "anything-at-all"})
    assert resp.status_code == 503


def test_degraded_marker_survives_get_current_user(monkeypatch):
    """CONTROL for the gate above: the marker has to REACH it.

    `get_current_user` used to strip every trace of the degraded state, leaving a
    degraded guest byte-identical to a validated one — with that stripping back,
    the two tests above pass a plain guest and go green while the hole is open.
    This asserts the real resolver still labels the principal.
    """
    import asyncio
    import auth as auth_mod

    monkeypatch.setattr(auth_mod, "_AUTH_FAIL_CLOSED", False)

    async def _degraded(_sid):
        return auth_mod._degraded_user()

    monkeypatch.setattr(auth_mod, "_validate_with_auth_service", _degraded)
    monkeypatch.setattr(auth_mod, "_session_cache", {})

    class _Req:
        headers = {"X-Session-ID": "anything-at-all"}
        class url:  # noqa: D106
            path = "/api/voice/livekit-audio"
        method = "POST"

    resolved = asyncio.run(auth_mod.get_current_user(_Req()))
    assert resolved["auth_degraded"] is True, (
        "get_current_user no longer marks a degraded principal — the LiveKit media "
        "gate cannot tell an auth outage from a validated guest session"
    )
    assert resolved["role"] == "guest"
    # The private wire flag must NOT leak out with it.
    assert auth_mod._DEGRADED_MARK not in resolved


def test_degraded_auth_does_not_block_a_device_token(_no_pipeline):
    """A provisioned panel never depended on zoe-auth, so an outage must not
    take the Pi voice daemon down with it — only the header-presence branch."""
    app = _app(
        device={"panel_id": "zoe-touch-pi", "user_id": "jason", "role": "kiosk"},
        user={"user_id": "guest", "role": "guest", "auth_degraded": True},
    )
    resp = _post_audio(TestClient(app), headers={"X-Device-Token": "raw"})
    assert resp.status_code == 200


# ── attribution ─────────────────────────────────────────────────────────────

def test_brain_turn_is_attributed_to_the_authenticated_principal(monkeypatch):
    """The brain turn's user is the SERVER-resolved principal. A caller must not
    be able to steer it from the multipart body — that is how an anonymous
    request ended up spending a brain turn as `guest` in the first place, and
    how a caller could otherwise write into somebody else's memory."""
    seen: dict = {}

    async def _transcribe(_path):
        return "what is the weather"

    async def _brain(text, session_id, user_id, **kwargs):
        seen["text"] = text
        seen["session_id"] = session_id
        seen["user_id"] = user_id
        return "It is sunny."

    monkeypatch.setattr(voice_tts, "_transcribe_audio", _transcribe)
    monkeypatch.setitem(
        sys.modules, "brain_dispatch", types.SimpleNamespace(brain_oneshot=_brain)
    )

    async def _synth(_payload, caller=None):
        raise RuntimeError("TTS deliberately skipped in this test")

    monkeypatch.setattr(voice_tts, "synthesize", _synth)

    app = _app(user={"user_id": "jason", "role": "admin"})
    client = TestClient(app)
    resp = client.post(
        "/api/voice/livekit-audio",
        files={"audio": ("ptt.webm", b"\x00\x01", "audio/webm")},
        # A caller-supplied user_id field must be ignored outright.
        data={"session_id": "sess-lk", "user_id": "family-admin"},
        headers={"X-Session-ID": "sess-real"},
    )
    assert resp.status_code == 200
    assert seen["user_id"] == "jason", "brain turn must track the authenticated principal"
    assert seen["session_id"] == "sess-lk"
    assert resp.json()["transcript"] == "what is the weather"


def test_device_token_turn_runs_as_the_PANEL_BOUND_user(monkeypatch):
    """A device token authenticates the DEVICE; the acting person is the bound user.

    `_validate_device_token` hardcodes `user_id="voice-daemon"`, while
    `get_current_user` resolves the SAME token through `panel_user_bindings` to
    the panel's bound user. Taking the device dict's id runs the Pi daemon's
    brain turns as `voice-daemon` — no personal context, and any memory written
    into the wrong scope.
    """
    seen: dict = {}

    async def _transcribe(_path):
        return "what is on my calendar"

    async def _brain(text, session_id, user_id, **kwargs):
        seen["user_id"] = user_id
        return "Nothing today."

    monkeypatch.setattr(voice_tts, "_transcribe_audio", _transcribe)
    monkeypatch.setitem(
        sys.modules, "brain_dispatch", types.SimpleNamespace(brain_oneshot=_brain)
    )

    async def _synth(_payload, caller=None):
        raise RuntimeError("TTS deliberately skipped in this test")

    monkeypatch.setattr(voice_tts, "synthesize", _synth)

    app = _app(
        device={"panel_id": "zoe-touch-pi", "user_id": "voice-daemon", "role": "kiosk"},
        # what get_current_user returns for this token via _resolve_device_token_user
        user={"user_id": "jason", "role": "user"},
    )
    resp = TestClient(app).post(
        "/api/voice/livekit-audio",
        files={"audio": ("ptt.webm", b"\x00\x01", "audio/webm")},
        data={"session_id": "sess-lk"},
        headers={"X-Device-Token": "raw"},
    )
    assert resp.status_code == 200
    assert seen["user_id"] == "jason", (
        "the device-token turn ran as the raw device identity instead of the "
        "panel's bound user — personal context lost, memory in the wrong scope"
    )


@pytest.mark.parametrize(
    "resolved_user",
    [
        {"user_id": "guest", "role": "guest"},                      # unbound panel
        {"user_id": "guest", "role": "guest", "auth_degraded": True},  # + auth outage
        {},                                                          # nothing resolved
    ],
)
def test_unbound_panel_device_token_stays_the_device_identity(monkeypatch, resolved_user):
    """CONTROL for the above: an UNBOUND panel resolves to guest (fail-closed,
    ZOE-4321), and that must fall back to the DEVICE identity — never attribute
    the turn to a `guest` principal whose scope other callers also share."""
    seen: dict = {}

    async def _transcribe(_path):
        return "hello"

    async def _brain(text, session_id, user_id, **kwargs):
        seen["user_id"] = user_id
        return "Hi."

    monkeypatch.setattr(voice_tts, "_transcribe_audio", _transcribe)
    monkeypatch.setitem(
        sys.modules, "brain_dispatch", types.SimpleNamespace(brain_oneshot=_brain)
    )

    async def _synth(_payload, caller=None):
        raise RuntimeError("TTS deliberately skipped in this test")

    monkeypatch.setattr(voice_tts, "synthesize", _synth)

    app = _app(
        device={"panel_id": "unbound-panel", "user_id": "voice-daemon", "role": "kiosk"},
        user=resolved_user,
    )
    resp = TestClient(app).post(
        "/api/voice/livekit-audio",
        files={"audio": ("ptt.webm", b"\x00\x01", "audio/webm")},
        data={"session_id": "sess-lk"},
        headers={"X-Device-Token": "raw"},
    )
    assert resp.status_code == 200
    assert seen["user_id"] == "voice-daemon", (
        "an unbound (or unresolvable) panel must keep the device identity, not "
        "borrow the shared guest scope"
    )


# ── wiring pins ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("endpoint", ["livekit_audio", "livekit_cancel"])
def test_endpoint_is_wired_through_the_gate(endpoint):
    """Signature pin: a future edit that swaps the gate back to a soft resolver
    reopens the hole silently — the success response shape does not change at
    all. Pin the dependency itself."""
    sig = inspect.signature(getattr(voice_livekit, endpoint))
    dep = sig.parameters["caller"].default
    assert getattr(dep, "dependency", None) is voice_livekit._require_livekit_media_auth


def test_the_swallowing_resolver_is_gone():
    """`_get_current_user_soft` was the bug, not a helper worth keeping. Retire
    by removing — if it comes back, so does the invalid-session hole."""
    assert not hasattr(voice_livekit, "_get_current_user_soft")
