"""Music Assistant on-demand start (the START half of the idle reap, flag-dark).

Everything runs against a fake docker + fake HTTP probe in tmp_path — never the
live container. The load-bearing properties: flag OFF touches nothing (byte-
identical); flag ON starts a stopped container and waits for HTTP; a start that
never comes up is bounded and reported False; reads never wake MA.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import ma_ondemand
import music_service


@pytest.fixture
def reap(monkeypatch, tmp_path):
    """Flag on, state dir in tmp, docker + HTTP faked; returns the call log."""
    monkeypatch.setenv("ZOE_MA_IDLE_REAP", "1")
    monkeypatch.setenv("ZOE_MA_REAP_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(ma_ondemand, "_last_seen_up", 0.0)
    monkeypatch.setattr(ma_ondemand, "_last_seen_up_wall", 0.0)
    monkeypatch.setattr(ma_ondemand, "_lock", None)
    log = {"docker": [], "http_up": False, "running": False}

    async def _docker(*args, timeout=20.0):
        log["docker"].append(args)
        if args[0] == "start":
            log["running"] = True
            log["http_up"] = True  # the container serves /info ~3.5 s after start
        return 0, ""

    async def _running():
        return log["running"]

    async def _http_up(timeout=1.5):
        return log["http_up"]

    monkeypatch.setattr(ma_ondemand, "_docker_cmd", _docker)
    monkeypatch.setattr(ma_ondemand, "_container_running", _running)
    monkeypatch.setattr(ma_ondemand, "_http_up", _http_up)
    return log


async def test_flag_off_is_a_no_op(monkeypatch, tmp_path, reap):
    """Negative control: with the flag off nothing is touched — no docker, no stamps."""
    monkeypatch.setenv("ZOE_MA_IDLE_REAP", "0")
    assert await ma_ondemand.ensure_running() is True
    assert reap["docker"] == []
    assert list(tmp_path.iterdir()) == []


async def test_start_path_starts_container_and_waits_for_http(tmp_path, reap):
    ok = await ma_ondemand.ensure_running(timeout_s=2.0)
    assert ok is True
    assert ("start", "zoe-music-assistant") in reap["docker"]
    assert (tmp_path / "activity").exists() and (tmp_path / "inflight").exists()


async def test_already_serving_skips_docker(reap):
    reap["http_up"] = True
    assert await ma_ondemand.ensure_running(timeout_s=1.0) is True
    assert reap["docker"] == []


async def test_timeout_path_is_bounded_and_false(reap, monkeypatch):
    async def _docker(*args, timeout=20.0):
        reap["docker"].append(args)
        return 0, ""  # started, but /info never answers
    monkeypatch.setattr(ma_ondemand, "_docker_cmd", _docker)
    assert await ma_ondemand.ensure_running(timeout_s=0.6) is False
    assert ("start", "zoe-music-assistant") in reap["docker"]


async def test_docker_start_failure_is_false(reap, monkeypatch):
    async def _docker(*args, timeout=20.0):
        return 1, "Error: No such container"
    monkeypatch.setattr(ma_ondemand, "_docker_cmd", _docker)
    assert await ma_ondemand.ensure_running(timeout_s=0.2) is False


async def test_fresh_seen_up_skips_the_probe(reap):
    """The cache's positive case, so the two tests below are not vacuous."""
    ma_ondemand.note_seen_up()
    assert await ma_ondemand.ensure_running(timeout_s=0.2) is True
    assert reap["docker"] == []


async def test_reaper_stop_stamp_overrides_a_fresh_seen_up(reap, tmp_path):
    """Codex P2: a 'seen up' from the panel's 5 s poll can describe the container
    the reaper stopped a moment later — the reaper's `stopped` stamp (newer than
    that answer) must force a real probe + start, not a skip."""
    ma_ondemand.note_seen_up()
    import time
    time.sleep(0.01)
    (tmp_path / "stopped").touch()
    assert await ma_ondemand.ensure_running(timeout_s=2.0) is True
    assert ("start", "zoe-music-assistant") in reap["docker"]
    assert not (tmp_path / "stopped").exists(), "a successful start clears the stamp"


async def test_failed_request_invalidates_the_seen_up_cache(monkeypatch, reap):
    """Codex P2: any transport failure must drop the cache so the next wake probes."""
    ma_ondemand.note_seen_up()

    class _Down(_FakeClient):
        async def post(self, url, json=None, headers=None):
            raise OSError("connection refused")
    monkeypatch.setattr(music_service.httpx, "AsyncClient", _Down)
    assert await music_service._ma("players/all") is None
    assert ma_ondemand._last_seen_up == 0.0
    assert await ma_ondemand.ensure_running(timeout_s=2.0) is True
    assert ("start", "zoe-music-assistant") in reap["docker"]


async def test_provider_write_path_wakes_ma_before_the_version_probe(monkeypatch, reap):
    """Codex P2: /api/music/setup/start → provider_setup_form → _ma_api_for_write
    reads /info directly; with MA reaped that returned None and the flow died as
    'unknown provider' before any wake command. The YouTube Music reconnect is the
    operator's only re-auth path, so the write funnel must wake first."""
    order = []

    async def _ensure():
        order.append("wake")
        return True

    async def _info():
        order.append("info")
        return None
    monkeypatch.setattr(ma_ondemand, "ensure_running", _ensure)
    monkeypatch.setattr(music_service, "_ma_info", _info)
    assert await music_service.provider_setup_form("ytmusic") is None  # MA still unreadable → honest None
    assert order == ["wake", "info"], "the wake must precede the /info probe"


async def test_provider_write_path_is_inert_with_flag_off(monkeypatch, reap):
    monkeypatch.setenv("ZOE_MA_IDLE_REAP", "0")

    async def _boom():
        raise AssertionError("no wake with the flag off")

    async def _info():
        return None
    monkeypatch.setattr(ma_ondemand, "ensure_running", _boom)
    monkeypatch.setattr(music_service, "_ma_info", _info)
    assert await music_service.provider_setup_form("ytmusic") is None


class _FakeClient:
    """Stand-in for httpx.AsyncClient: records posts, answers 200 {}."""
    posted: list = []

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeClient.posted.append(json["command"])

        class R:
            status_code = 200

            def json(self):
                return []
        return R()


async def test_wrapper_wakes_on_intent_commands_but_never_on_reads(monkeypatch, reap):
    woke = []

    async def _ensure():
        woke.append(1)
        return True
    monkeypatch.setattr(ma_ondemand, "ensure_running", _ensure)
    monkeypatch.setattr(music_service.httpx, "AsyncClient", _FakeClient)

    await music_service._ma("players/all")             # the panel's 5 s poll
    await music_service._ma("player_queues/all")
    await music_service._ma("music/recently_played_items")  # the journal observer
    assert woke == [], "a read poll must never wake a reaped MA"
    await music_service._ma("music/search", search_query="jazz")
    assert woke == [1]


async def test_wrapper_is_inert_with_flag_off(monkeypatch, reap, tmp_path):
    monkeypatch.setenv("ZOE_MA_IDLE_REAP", "0")

    async def _boom():
        raise AssertionError("ensure_running must not be called with the flag off")
    monkeypatch.setattr(ma_ondemand, "ensure_running", _boom)
    monkeypatch.setattr(music_service.httpx, "AsyncClient", _FakeClient)
    await music_service._ma("music/search", search_query="jazz")
    await music_service._ma("player_queues/play", queue_id="x")
    assert list(tmp_path.iterdir()) == [], "no activity stamp with the flag off"
