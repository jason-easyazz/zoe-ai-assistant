"""
Regression tests for the zoe-music intent handlers
(modules/zoe-music/intents/handlers.py).

Covers the contract-alignment fix:
  (c) every HTTP-backed handler targets a route that actually exists on the
      live bridge (main.py) and carries the shared service token, and
  (a) replaying that exact call against the real app passes the auth gate.
"""
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from conftest import load_music_handlers, load_music_main

TOKEN = "handler-token-xyz"


class Intent:
    def __init__(self, slots=None, text=""):
        self.slots = slots or {}
        self.text = text


class FakeResp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._p = payload if payload is not None else {
            "ok": True, "playing": True, "title": "Song", "artist": "Artist",
        }

    def json(self):
        return self._p


class FakeClient:
    """Records the call the handler makes instead of hitting the network."""
    calls = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        FakeClient.calls.append(("POST", url, json, headers))
        return FakeResp()

    async def get(self, url, params=None, headers=None):
        FakeClient.calls.append(("GET", url, params, headers))
        return FakeResp()


@pytest.fixture()
def handlers(monkeypatch):
    mod = load_music_handlers()
    monkeypatch.setenv("ZOE_MUSIC_SERVICE_TOKEN", TOKEN)
    FakeClient.calls = []
    monkeypatch.setattr(mod.httpx, "AsyncClient", FakeClient)
    return mod


@pytest.fixture(scope="module")
def real_routes():
    main = load_music_main()
    routes = {}
    for r in main.app.routes:
        if hasattr(r, "methods") and hasattr(r, "path"):
            routes.setdefault(r.path, set()).update(r.methods)
    return routes


# Each HTTP-backed handler and the slots that exercise it.
HTTP_HANDLERS = [
    ("handle_music_play", {"query": "beatles"}),
    ("handle_music_pause", {}),
    ("handle_music_resume", {}),
    ("handle_music_skip", {}),
    ("handle_music_previous", {}),
    ("handle_music_volume", {"level": 30}),
    ("handle_music_search", {"query": "jazz"}),
    ("handle_music_now_playing", {}),
]


@pytest.mark.parametrize("fn_name,slots", HTTP_HANDLERS)
async def test_handler_hits_existing_route_with_token(handlers, real_routes, fn_name, slots):
    fn = getattr(handlers, fn_name)
    result = await fn(Intent(slots=slots), "user-1", {})

    # The handler made exactly one outbound call...
    assert len(FakeClient.calls) == 1, f"{fn_name} made {len(FakeClient.calls)} calls"
    method, url, _payload, headers = FakeClient.calls[0]
    path = urlsplit(url).path

    # ...to a route that actually exists on the live bridge, with the right verb.
    assert path in real_routes, f"{fn_name} -> {method} {path} is not a live route"
    assert method in real_routes[path], f"{fn_name}: {method} not allowed on {path}"

    # ...carrying the shared service token (so the gate accepts it).
    assert headers.get("X-Zoe-Service-Token") == TOKEN

    # ...and a happy response yields a successful handler result.
    assert result["success"] is True


@pytest.mark.parametrize("fn_name,slots", HTTP_HANDLERS)
async def test_handler_call_passes_real_auth_gate(handlers, fn_name, slots):
    """Replay the exact (method, path, token) the handler emits against the real
    app: it must clear the gate (not 401), the route must exist (not 404/405),
    and the body/params must validate (not 422)."""
    fn = getattr(handlers, fn_name)
    await fn(Intent(slots=slots), "user-1", {})
    method, url, payload, headers = FakeClient.calls[0]
    path = urlsplit(url).path

    main = load_music_main()

    async def _ma_down():
        return False

    async def _ha_noop(service, extra=None):
        return None

    # Stub the network so authorized calls return cleanly.
    import os
    main._ma_available = _ma_down
    main._ha_service = _ha_noop
    os.environ["ZOE_MUSIC_SERVICE_TOKEN"] = TOKEN
    client = TestClient(main.app)

    if method == "POST":
        ok = client.post(path, json=payload or {}, headers=headers)
        missing = client.post(path, json=payload or {})
    else:
        ok = client.get(path, params=payload or {}, headers=headers)
        missing = client.get(path, params=payload or {})

    assert ok.status_code not in (401, 404, 405, 422), (
        f"{fn_name}: tokened call rejected with {ok.status_code}"
    )
    # And the same call WITHOUT the token is rejected by the gate.
    assert missing.status_code == 401


async def test_static_stubs_make_no_http_call(handlers):
    """Intents with no backing route return a friendly message and never touch
    a (possibly stale) endpoint."""
    for name in ("handle_music_radio", "handle_music_mood", "handle_music_like"):
        FakeClient.calls = []
        res = await getattr(handlers, name)(Intent(), "u", {})
        assert res["success"] is True
        assert FakeClient.calls == []


def test_dead_intents_not_registered(handlers):
    """Queue/recommendation intents had no real endpoint; they must not be
    re-registered to call stale routes."""
    for dead in ("MusicQueue", "MusicQueueAdd", "MusicSimilar", "MusicDiscover"):
        assert dead not in handlers.INTENT_HANDLERS
    # Sanity: the playback intents are still wired.
    for live in ("MusicPlay", "MusicPause", "MusicVolume", "MusicNowPlaying"):
        assert live in handlers.INTENT_HANDLERS
