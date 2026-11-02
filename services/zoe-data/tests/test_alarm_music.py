"""alarm_music (P3 clock radio): a music alarm starts Music Assistant playback on the PANEL's speaker and reports how it went; it never
leaves an alarm silent (the panel plays the tone unless the server says ``playing``). Runs against a FAKE Music Assistant and a real
sqlite DB through the real ``alarms.tick`` hook path. Synthetic ids only.

Negative controls (run by hand when this was written; each turns the named test red):
  * play on ``_pick_player`` instead of the panel by name -> test_only_the_panel_speaker_is_used;
  * report ``playing`` without polling MA -> test_music_that_never_plays_fails_within_the_deadline_and_says_so;
  * drop the ``stop`` hook on dismiss/snooze/auto-stop/edit/delete -> test_music_stops_with_the_alarm_every_way_it_can_end;
  * let a hook exception escape ``alarms._emit`` -> test_a_broken_hook_never_breaks_the_ring;
  * drift the client tone ids from ``alarms.TONES`` -> test_tone_ids_match_the_panel_synth.
"""
from __future__ import annotations

import asyncio
import contextlib
import importlib.util
import re
import sys
from pathlib import Path

import aiosqlite
import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import alarm_music  # noqa: E402
import alarms  # noqa: E402

pytestmark = pytest.mark.ci_safe

REAL_LOG = alarm_music._log  # the autouse fixture swaps in a recorder; one test needs the real one

NIGHT, SEVEN = 1_793_000_000.0, 1_793_000_000.0 + 3600  # any fixed instants: the engine only compares them
PANEL = {"player_id": "ap-panel", "display_name": "Zoe Panel", "available": True}
KITCHEN = {"player_id": "ap-kitchen", "display_name": "Kitchen", "available": True}


class FakeMA:
    def __init__(self, players=None, plays_after=0, source_ok=True, boom=None, playlists=None):
        self.players = [PANEL, KITCHEN] if players is None else players
        self.plays_after, self.source_ok, self.boom = plays_after, source_ok, boom
        self.playlists = playlists if playlists is not None else [{"name": "Morning", "uri": "pl:1"}]
        self.calls, self.polls, self.started = [], 0, False

    async def get_players(self):
        if self.boom:
            raise self.boom
        return self.players

    async def control(self, action, player_id="", value=None):
        self.calls.append(("control", action, player_id, value))
        return True

    async def now_playing(self, player_id=""):
        self.polls += 1
        return {"state": "playing" if self.started and self.polls > self.plays_after else "idle"}

    async def search_and_play(self, query, player_id="", radio_mode=False, zoe_user_id=""):
        self.calls.append(("search_and_play", query, player_id, radio_mode))
        self.started = self.source_ok
        return {"name": query} if self.source_ok else None

    async def play_media(self, uri, player_id="", option="replace", radio_mode=False, zoe_user_id=""):
        self.calls.append(("play_media", uri, player_id))
        self.started = self.source_ok
        return {"ok": self.source_ok}

    async def list_playlists(self):
        return self.playlists

    async def get_recently_played(self, limit=10, media_types=None):
        return {"items": [{"uri": "track:9"}]}

    def of(self, kind):
        return [c for c in self.calls if c[0] == kind]

    def pauses(self):
        return [c for c in self.calls if c[:2] == ("control", "pause")]


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(alarm_music, "START_DEADLINE_S", 0.5)
    monkeypatch.setattr(alarm_music, "POLL_S", 0.02)
    monkeypatch.setattr(alarm_music, "RAMP_STEP_S", 0.05)
    monkeypatch.setattr(alarms, "HOOKS", [])
    monkeypatch.delenv("ZOE_ALARM_MUSIC_PLAYER", raising=False)
    alarm_music.STATE.clear()
    alarm_music._tasks.clear()
    logged = []

    async def fake_log(aid, event, detail):
        logged.append((aid, event, detail))

    monkeypatch.setattr(alarm_music, "_log", fake_log)
    return logged


def _alarm(**kw):
    base = {"id": "a1", "sound_kind": "music", "sound_id": "search:jazz", "ramp_seconds": 1, "user_id": alarms.HOUSEHOLD}
    base.update(kw)
    return base


def run(coro):
    return asyncio.run(coro)


async def _ring(ma, monkeypatch, alarm=None, settle=0.4):
    monkeypatch.setattr(alarm_music, "_ms", lambda: ma)
    a = alarm or _alarm()
    await alarm_music.on_event("rang", a)
    await asyncio.sleep(settle)
    return a


def test_a_music_alarm_starts_playing_on_the_panel_and_ramps_the_volume(monkeypatch):
    ma = FakeMA(plays_after=2)

    async def go():
        a = await _ring(ma, monkeypatch, settle=0.15)
        assert alarm_music.state_of(a["id"]) == "playing"
        await asyncio.sleep(1.2)  # the ramp finishes
        await alarm_music.stop(a["id"])

    run(go())
    assert ma.of("search_and_play") == [("search_and_play", "jazz", "ap-panel", False)]
    vols = [c[3] for c in ma.calls if c[:2] == ("control", "volume_set")]
    assert vols[0] == alarm_music.START_VOLUME and vols[-1] == alarm_music.TARGET_VOLUME and vols == sorted(vols) and len(vols) > 3
    assert all(c[2] == "ap-panel" for c in ma.calls if c[0] == "control")


@pytest.mark.parametrize("sound_id,call", [
    ("last", ("play_media", "track:9", "ap-panel")),
    ("playlist:Morning", ("play_media", "pl:1", "ap-panel")),
    ("playlist:  morning ", ("play_media", "pl:1", "ap-panel")),
    ("radio:Triple J", ("search_and_play", "Triple J", "ap-panel", True)),
    ("search:jazz", ("search_and_play", "jazz", "ap-panel", False)),
])
def test_every_source_kind_reaches_the_panel_player(monkeypatch, sound_id, call):
    ma = FakeMA()
    run(_ring(ma, monkeypatch, _alarm(sound_id=sound_id, ramp_seconds=0), settle=0.2))
    assert call in ma.calls


def test_only_the_panel_speaker_is_used(monkeypatch):
    ma = FakeMA(players=[KITCHEN])  # the panel's player is not in Music Assistant
    run(_ring(ma, monkeypatch))
    assert alarm_music.state_of("a1") == "failed"
    assert not ma.of("search_and_play") and not ma.of("play_media")  # never the kitchen speaker
    assert not [c for c in ma.calls if c[0] == "control" and c[2] == "ap-kitchen"]


def test_the_player_name_is_configurable(monkeypatch):
    monkeypatch.setenv("ZOE_ALARM_MUSIC_PLAYER", "kitchen")
    ma = FakeMA()
    run(_ring(ma, monkeypatch, settle=0.2))
    assert ma.of("search_and_play")[0][2] == "ap-kitchen"


def test_music_that_never_plays_fails_within_the_deadline_and_says_so(monkeypatch, _fast):
    ma = FakeMA(plays_after=10 ** 9)  # accepted the command, never actually plays

    async def go():
        await _ring(ma, monkeypatch, settle=0)
        assert alarm_music.state_of("a1") == "starting"
        await asyncio.sleep(0.8)

    run(go())
    assert alarm_music.state_of("a1") == "failed"
    assert _fast and _fast[-1][:2] == ("a1", "music_fallback") and "10 s" in _fast[-1][2]
    assert ma.pauses()  # and whatever MA did start is stopped, not left playing under the tone


@pytest.mark.parametrize("ma", [
    FakeMA(source_ok=False),                      # search finds nothing / provider refuses
    FakeMA(boom=RuntimeError("MA down")),          # Music Assistant unreachable
    FakeMA(playlists=[]),                          # a stale playlist name
])
def test_provider_failures_end_in_failed_never_an_exception(monkeypatch, ma):
    sid = "playlist:Gone" if not ma.playlists else "search:jazz"
    run(_ring(ma, monkeypatch, _alarm(sound_id=sid), settle=0.3))
    assert alarm_music.state_of("a1") == "failed"


def test_a_hung_music_assistant_is_cut_off_at_the_deadline(monkeypatch):
    ma = FakeMA()

    async def hang():
        await asyncio.sleep(30)

    ma.get_players = hang

    async def go():
        await _ring(ma, monkeypatch, settle=0)
        await asyncio.sleep(0.8)

    run(go())
    assert alarm_music.state_of("a1") == "failed"


def test_music_stops_with_the_alarm_every_way_it_can_end(monkeypatch):
    for ev in ("snoozed", "dismissed", "auto_stopped", "updated", "deleted"):
        ma = FakeMA()

        async def go():
            a = await _ring(ma, monkeypatch, settle=0.15)
            assert alarm_music.state_of(a["id"]) == "playing"
            await alarm_music.on_event(ev, a)

        run(go())
        assert ma.pauses(), ev
        assert alarm_music.state_of("a1") == "" and "a1" not in alarm_music._tasks, ev


def test_a_tone_alarm_never_touches_music_assistant(monkeypatch):
    ma = FakeMA()

    async def go():
        await _ring(ma, monkeypatch, _alarm(sound_kind="tone", sound_id="bell"), settle=0.1)
        await alarm_music.on_event("dismissed", _alarm(sound_kind="tone", sound_id="bell"))

    run(go())
    assert ma.calls == []


def test_a_re_ring_after_snooze_starts_clean(monkeypatch):
    ma = FakeMA()

    async def go():
        a = await _ring(ma, monkeypatch, settle=0.15)
        await alarm_music.on_event("snoozed", a)
        await alarm_music.on_event("rang", a)
        await asyncio.sleep(0.2)
        assert alarm_music.state_of("a1") == "playing"
        await alarm_music.stop("a1")

    run(go())
    assert len(ma.of("search_and_play")) == 2


# ── through the real engine + DB ─────────────────────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def dbfile(tmp_path):
    p = str(tmp_path / "z.db")
    spec = importlib.util.spec_from_file_location("mig_0063", ROOT / "alembic/versions/0063_alarms.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    engine = sa.create_engine(f"sqlite:///{p}")
    with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
        mod.upgrade()
    engine.dispose()
    return p


def test_the_engine_starts_music_on_a_ring_and_stops_it_on_dismiss(dbfile, monkeypatch):
    ma = FakeMA()
    monkeypatch.setattr(alarm_music, "_ms", lambda: ma)
    alarm_music.register()
    alarm_music.register()  # idempotent
    assert alarms.HOOKS.count(alarm_music.on_event) == 1

    async def go():
        db = await aiosqlite.connect(dbfile)
        try:
            a = await alarms.create_alarm(db, "ann-0001", time_hhmm="07:00", repeat="daily", sound_kind="music", sound_id="search:jazz",
                                          sound_name="Jazz", ramp_seconds=0, now=NIGHT)
            fire = a["next_fire_at"]
            await alarms.tick(db, fire)
            await asyncio.sleep(0.2)
            assert alarm_music.state_of(a["id"]) == "playing"
            await alarms.dismiss(db, await alarms.get_alarm(db, a["id"]), now=fire + 5)
            assert alarm_music.state_of(a["id"]) == ""
            await alarms.tick(db, fire + 86400)  # next day: rings and plays again
            await asyncio.sleep(0.2)
            assert alarm_music.state_of(a["id"]) == "playing"
            await alarms.tick(db, fire + 86400 + alarms.RING_MAX_S)  # unanswered: auto-stop
            assert alarm_music.state_of(a["id"]) == ""
        finally:
            await db.close()

    run(go())
    assert len(ma.of("search_and_play")) == 2 and len(ma.pauses()) >= 2


def test_a_broken_hook_never_breaks_the_ring(dbfile, monkeypatch):
    async def bad(event, alarm):
        raise RuntimeError("boom")

    monkeypatch.setattr(alarms, "HOOKS", [bad])

    async def go():
        db = await aiosqlite.connect(dbfile)
        try:
            a = await alarms.create_alarm(db, "ann-0001", time_hhmm="07:00", now=NIGHT)
            out = await alarms.tick(db, a["next_fire_at"])
            return [e for e, _ in out], (await alarms.get_alarm(db, a["id"]))["state"]
        finally:
            await db.close()

    assert run(go()) == (["rang"], "ringing")


def test_the_fallback_is_logged_in_the_alarm_events(dbfile, monkeypatch):
    import db_pool

    @contextlib.asynccontextmanager
    async def ctx():
        db = await aiosqlite.connect(dbfile)
        try:
            yield db
        finally:
            await db.close()

    monkeypatch.setattr(db_pool, "get_db_ctx", ctx)
    monkeypatch.setattr(alarm_music, "_log", REAL_LOG)
    monkeypatch.setattr(alarm_music, "START_DEADLINE_S", 0.3)
    monkeypatch.setattr(alarm_music, "_ms", lambda: FakeMA(players=[]))
    real = alarm_music

    async def go():
        db = await aiosqlite.connect(dbfile)
        a = await alarms.create_alarm(db, "ann-0001", time_hhmm="07:00", sound_kind="music", sound_id="last", now=NIGHT)
        await db.close()
        await real.on_event("rang", {**a, "ramp_seconds": 0})
        await asyncio.sleep(0.4)
        db = await aiosqlite.connect(dbfile)
        async with db.execute("SELECT event, detail FROM alarm_events WHERE alarm_id = ? AND event = 'music_fallback'", (a["id"],)) as cur:
            rows = await cur.fetchall()
        await db.close()
        return rows

    rows = run(go())
    assert rows and "no panel player" in rows[0][1]


def test_the_ring_payload_reports_the_music_state(dbfile, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from database import get_db
    from panel_principal import require_member_or_panel
    from routers.alarms import router

    monkeypatch.setenv("ZOE_ALARMS", "on")
    app = FastAPI()
    app.include_router(router)

    async def db_dep():
        conn = await aiosqlite.connect(dbfile)
        try:
            yield conn
        finally:
            await conn.close()

    app.dependency_overrides[get_db] = db_dep
    app.dependency_overrides[require_member_or_panel] = lambda: {"user_id": "ann-0001", "role": "user"}

    async def mk():
        db = await aiosqlite.connect(dbfile)
        a = await alarms.create_alarm(db, "ann-0001", time_hhmm="07:00", sound_kind="music", sound_id="last", now=NIGHT)
        await alarms.tick(db, a["next_fire_at"])
        await db.close()
        return a

    a = run(mk())
    alarm_music.STATE[a["id"]] = {"state": "playing"}
    c = TestClient(app)
    got = c.get("/api/alarms/ringing").json()["ringing"]
    assert got[0]["music_state"] == "playing" and got[0]["sound_kind"] == "music"
    alarm_music.STATE.clear()
    assert c.get("/api/alarms/ringing").json()["ringing"][0]["music_state"] == ""  # unknown (e.g. zoe-data restarted): the panel plays the tone


def test_tone_ids_match_the_panel_synth():
    js = (ROOT.parent / "zoe-ui" / "dist" / "js" / "touch-alarm-sound.js").read_text(encoding="utf-8")
    block = js.split("var TONES = {")[1].split("var st =")[0]
    ids = set(re.findall(r"^\s{8}(\w+): \{ name: '([^']+)'", block, re.M))
    assert {i for i, _ in ids} == set(alarms.TONES), "client tone ids drifted from alarms.TONES"
    assert dict(ids) == alarms.TONES
