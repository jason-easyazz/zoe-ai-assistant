"""The QR→phone setup token never travels in a query string (auth audit 2026-09-27).

The phone link keeps the one-time token in its ``#fragment`` precisely so it
never reaches a server log — but the panel's ``<img src=/api/…/setup/qr?token=…>``
and the phone's own GETs (``/form?token=``, ``/oauth/status?token=``,
``/browser/status?token=``, ``/info?token=``) put the same 15-minute secret in
nginx's access log. Now: the QR is fetched by an opaque single-use handle, GETs
carry the token in ``X-Setup-Token``, POSTs in the body.

Falsifiable pins: re-add a ``token`` query parameter to any setup route, or put
the token back into a ``qr_path``, and these go red.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # slim-dep green; opts into validate.yml's `-m ci_safe` lane

from fastapi import FastAPI
from fastapi.testclient import TestClient

import music_service
import music_setup
import setup_qr
import smart_home_setup
from routers import music_setup as music_router
from routers import smart_home_setup as home_router


@pytest.fixture
def client(monkeypatch):
    # segno is not in the slim CI venv; what the image encodes is not under test.
    seen = []
    monkeypatch.setattr(setup_qr, "render_svg", lambda url: seen.append(url) or b"<svg/>")
    app = FastAPI()
    app.include_router(music_router.router)
    app.include_router(home_router.router)
    return TestClient(app)


@pytest.mark.parametrize("router", [music_router.router, home_router.router],
                         ids=["music", "home"])
def test_no_setup_route_accepts_the_token_as_a_query_param(router):
    for route in router.routes:
        names = {p.name for p in route.dependant.query_params}
        names |= {getattr(p, "alias", None) or p.name for p in route.dependant.query_params}
        assert "token" not in names, f"{route.path} takes the setup token in its query string"
        assert "t" not in names, route.path


def test_qr_routes_take_an_opaque_handle():
    paths = {r.path for r in music_router.router.routes} | {r.path for r in home_router.router.routes}
    assert "/api/music/setup/qr/{handle}" in paths and "/api/home/setup/qr/{handle}" in paths
    assert "/api/music/setup/qr" not in paths and "/api/home/setup/qr" not in paths


def test_qr_paths_never_contain_the_token():
    tok = music_setup.mint("spotify")["token"]
    path = music_setup.qr_path(tok, "spotify")
    assert tok not in path and "?" not in path and path.startswith("/api/music/setup/qr/")
    htok = smart_home_setup.mint()["token"]
    hpath = smart_home_setup.qr_path(htok)
    assert htok not in hpath and "?" not in hpath and hpath.startswith("/api/home/setup/qr/")


def test_music_qr_handle_reloads_within_its_ttl(client):
    """A failed image load or card redraw must still show the code (Greptile, #1741)."""
    tok = music_setup.mint("spotify")["token"]
    path = music_setup.qr_path(tok, "spotify")
    first = client.get(path)
    assert first.status_code == 200 and first.headers["content-type"].startswith("image/svg")
    assert first.headers["cache-control"] == "no-store"
    assert client.get(path).status_code == 200  # second GET within the TTL


def test_qr_handle_is_dead_after_its_ttl(client, monkeypatch):
    import time as _time
    tok = music_setup.mint("spotify")["token"]
    path = music_setup.qr_path(tok, "spotify")
    assert client.get(path).status_code == 200
    real = _time.time
    monkeypatch.setattr(setup_qr.time, "time", lambda: real() + setup_qr.QR_HANDLE_TTL_S + 1)
    assert client.get(path).status_code == 404  # a handle copied from a log is dead


def test_home_qr_handle_is_kind_bound(client):
    htok = smart_home_setup.mint()["token"]
    hpath = smart_home_setup.qr_path(htok)
    handle = hpath.rsplit("/", 1)[1]
    assert client.get(f"/api/music/setup/qr/{handle}").status_code == 404  # wrong flow
    assert client.get(hpath).status_code == 200
    assert client.get(hpath).status_code == 200


def test_expired_qr_handle_is_refused(client, monkeypatch):
    monkeypatch.setattr(setup_qr, "QR_HANDLE_TTL_S", -1)
    tok = music_setup.mint("spotify")["token"]
    assert client.get(music_setup.qr_path(tok, "spotify")).status_code == 404


def test_phone_form_reads_the_token_from_the_header(client, monkeypatch):
    async def fake_form(provider):
        return {"name": "Spotify", "auth": "oauth", "fields": []}

    monkeypatch.setattr(music_service, "provider_setup_form", fake_form)
    tok = music_setup.mint("spotify")["token"]
    ok = client.get("/api/music/setup/form?provider=spotify", headers={"X-Setup-Token": tok})
    assert ok.json()["ok"] is True
    # The same token in the query string is ignored — the call fails closed.
    leaked = client.get(f"/api/music/setup/form?provider=spotify&token={tok}")
    assert leaked.json()["ok"] is False


def test_home_info_reads_the_token_from_the_header(client):
    tok = smart_home_setup.mint()["token"]
    assert client.get(f"/api/home/setup/info?token={tok}").json()["ok"] is False
    assert client.get("/api/home/setup/info", headers={"X-Setup-Token": tok}).json()["ok"] is True
