"""Every member-driven way to make music or the house do something, run against a FAKE Music Assistant and a FAKE Home Assistant
bridge: every outbound call (method, path, body), what the entry point returned or raised, in order.

Music and home control store nothing in Zoe: what an entry point DOES is the calls it sends. So that is what is pinned.

Used twice: ``python3 tests/actions_scenarios.py <a zoe-data tree> <out.json>`` writes the record of THAT tree (run against an
export of ``main`` to make ``tests/fixtures/actions_as_on_main.json``), and ``test_actions_same_as_main.py`` runs the same
scenarios in-process and compares. It imports nothing the one-door or the kernel-door change added, so it runs unchanged on
``main``.

Invented speakers, devices and words only. Nothing here opens a socket: ``httpx.AsyncClient`` is replaced for the whole run.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

from calendar_scenarios import _plain, _restore, _set

ANN = "ann-0001"                 # an ADULT by the (faked) account record: unlocking or opening is hers to ask (home_opening.py)
KIP = "member-kip"               # a child
GUEST = {"user_id": "guest", "role": "guest"}
PANEL = "panel-demo-0001"
BARE_PANEL = {**GUEST, "panel_id": PANEL}      # the shared panel with nobody identified
GARAGE = {"entity_id": "cover.garage_door", "state": "closed", "attributes": {"friendly_name": "Garage door"}}
PLAYERS = [
    {"player_id": "ply0001", "display_name": "Kitchen speaker", "name": "Kitchen speaker", "available": True, "powered": True,
     "state": "playing", "volume_level": 40, "type": "player", "active_source": "ply0001", "group_childs": [], "can_group_with": [],
     "supported_features": [], "current_media": {"title": "Womble Song", "artist": "Quib", "uri": "library://track/1", "duration": 200}},
    {"player_id": "ply0002", "display_name": "Bedroom speaker", "name": "Bedroom speaker", "available": True, "powered": True,
     "state": "idle", "volume_level": 20, "type": "player", "active_source": "", "group_childs": [], "can_group_with": [],
     "supported_features": []},
]
QUEUES = [{"queue_id": "ply0001", "state": "playing", "elapsed_time": 10, "items": 1, "shuffle_enabled": False, "repeat_mode": "off",
           "current_item": {"queue_item_id": "qi1", "name": "Womble Song", "duration": 200,
                            "media_item": {"uri": "library://track/1", "name": "Womble Song", "artists": [{"name": "Quib"}]}}}]
SEARCH = {"tracks": [{"uri": "library://track/9", "name": "Womble Song", "media_type": "track", "artists": [{"name": "Quib"}]}],
          "artists": [], "albums": [], "playlists": [], "radio": []}
MA_ANSWERS = {"players/all": PLAYERS, "player_queues/all": QUEUES, "music/search": SEARCH, "player_queues/items": []}
HA_ANSWERS = {
    "/lights": {"lights": [{"entity_id": "light.kitchen_lamp", "state": "off", "attributes": {"friendly_name": "Kitchen lamp"}},
                           {"entity_id": "light.hall_lamp", "state": "on", "brightness": 128, "attributes": {"friendly_name": "Hall lamp"}}]},
    "/switches": {"switches": [{"entity_id": "switch.kettle_plug", "state": "off", "attributes": {"friendly_name": "Kettle plug"}}]},
    "/entities": {"entities": []},
    "/scenes": {"scenes": [{"entity_id": "scene.movie_time", "attributes": {"friendly_name": "Movie time"}}]},
}


class _Response:
    def __init__(self, status: int, body) -> None:
        self.status_code, self._body = status, body

    def json(self):
        return self._body

    @property
    def text(self) -> str:
        return json.dumps(self._body)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("status", request=None, response=self)


class World:
    """The fake network. ``ha_post`` / ``ma_status``: what the bridge / Music Assistant answers to a command (a test can
    change them); ``fail`` raises a transport error for paths that contain it."""

    def __init__(self, log: list) -> None:
        import httpx

        self.log, self.ha_post, self.ma_status, self.fail = log, (200, {"message": "Successfully executed"}), 200, ""
        self.ma_silent = False                 # True: Music Assistant has no speakers playing (music then goes through the house)
        self.ma_answers = dict(MA_ANSWERS)     # Music Assistant's answer per command (a scenario can add or change one)
        self.ha_answers = dict(HA_ANSWERS)     # the bridge's answer per read path (a scenario can change one)
        self.adults = {ANN}                    # who the account record says is an adult
        self.voice_sessions: dict = {}         # panel id -> the member whose verified voice session is open on it
        world = self
        import manner_block
        import voice_session

        async def account_is_adult(user_id, db=None):
            return user_id in world.adults

        _set(manner_block, "_account_is_adult", account_is_adult)      # no account store here: the record is the world's
        _set(voice_session, "active_member", lambda panel: world.voice_sessions.get(panel))

        class Client:
            def __init__(self, *a, **k) -> None:
                self.timeout = k.get("timeout")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                return False

            async def get(self, url, **k):
                return world.answer("GET", url, k.get("params"), self.timeout)

            async def post(self, url, **k):
                return world.answer("POST", url, k.get("json"), self.timeout)

        _set(httpx, "AsyncClient", Client)

    def answer(self, method, url, body, timeout):
        parts = urlsplit(str(url))
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        self.log.append(["http", method, path, _plain(body), timeout])
        if self.fail and self.fail in path:
            import httpx

            raise httpx.ConnectError("offline")
        if parts.path == "/api":                                        # Music Assistant's command endpoint
            command = (body or {}).get("command", "")
            if self.ma_silent and command in ("players/all", "player_queues/all"):
                return _Response(200, [])
            return _Response(self.ma_status, self.ma_answers.get(command))
        if method == "POST":
            return _Response(*self.ha_post)
        return _Response(200, self.ha_answers.get(parts.path, {}))


def _quiet(module, *names):
    async def nothing(*a, **k):
        return None

    for name in names:
        if hasattr(module, name):
            _set(module, name, nothing)


def scenarios() -> dict:
    """name -> a coroutine function of the world; its return value is recorded."""
    def music(route, answers=None, **payload):
        async def run(w):
            from routers import music as rest

            w.ma_answers.update(answers or {})
            return await getattr(rest, route)(payload)
        return run

    def said_music(action, query="", who=ANN, answers=None):
        async def run(w):
            import music_service
            import skybridge_service as sky

            w.ma_answers.update(answers or {})
            _quiet(music_service, "record_play", "observe")
            return (await music_service.resolve_music(sky.SkybridgeIntent("music", action, query=query), who)).get("spoken_summary")
        return run

    def music_intent(name, who=ANN, **slots):
        async def run(w):
            import intent_router

            return await intent_router._execute_music_intent(intent_router.Intent(name, slots), who)
        return run

    def music_through_the_house(name, **slots):
        async def run(w):
            w.ma_silent = True
            return await music_intent(name, **slots)(w)
        return run

    def home(who=None, session=None, **payload):
        async def run(w):
            from routers import ha_control

            w.voice_sessions.update(session or {})
            return await ha_control.ha_control(ha_control.HAControlPayload(**payload), caller=dict(who or {"user_id": ANN, "role": "user"}))
        return run

    def on_a_turn_of(who, call):
        """Run ``call`` as a chat / voice turn of ``who`` (a caller record, or None for no turn at all): the turn's entry point
        notes its caller for the opening rule (``home_opening.note_turn``). Put back afterwards, so nothing leaks to the next."""
        async def run(w):
            import home_opening

            token = home_opening._TURN.set(None)
            try:
                if who is not None:
                    home_opening.note_turn(dict(who))
                return await call(w)
            finally:
                home_opening._TURN.reset(token)
        return run

    def with_a_garage_door_listed(call):
        """The worst case for a spoken command: a bridge that lists a garage door among the switches."""
        async def run(w):
            w.ha_answers["/switches"] = {"switches": [*HA_ANSWERS["/switches"]["switches"], GARAGE]}
            return await call(w)
        return run

    def helper_set(entity_id, on):
        async def run(w):
            import smart_home_service

            return await smart_home_service.set_device(entity_id, on)
        return run

    def home_down(status=None, fail="", **payload):
        async def run(w):
            if status:
                w.ha_post = (status, {"message": "no"})
            w.fail = fail
            return await home(**payload)(w)
        return run

    def said_home(action, query="", percent=0):
        async def run(w):
            import skybridge_service as sky
            import smart_home_service

            intent = sky.SkybridgeIntent("smart_home", action, query=query, duration_seconds=percent)
            out = await smart_home_service.resolve_smart_home(intent)
            return {"spoken_summary": out.get("spoken_summary"), "action": (out.get("intent") or {}).get("action")}
        return run

    def tool_home(entity_id, action, data=None):
        async def run(w):
            import zoe_agent

            return await zoe_agent._ha_control(entity_id, action, data)
        return run

    adult, child = {"user_id": ANN, "role": "user"}, {"user_id": KIP, "role": "user"}
    said_garage = with_a_garage_door_listed(said_home("turn_on", "garage door"))

    async def tool_home_offline(w):
        w.fail = "/devices/control"
        return await tool_home("light.kitchen_lamp", "turn_on")(w)

    return {
        # MUSIC: the screen
        "music_screen_pause": music("music_control", action="pause"),
        "music_screen_next": music("music_control", action="next", player_id="ply0001"),
        "music_screen_volume_set": music("music_control", action="volume_set", value=30),
        "music_screen_volume_up": music("music_control", action="volume_up"),
        "music_screen_unknown_action": music("music_control", action="explode"),
        "music_screen_seek": music("music_seek", position_seconds=42),
        "music_screen_transfer": music("music_transfer", target_player_id="ply0002"),
        "music_screen_play": music("music_play", query="womble"),
        "music_screen_play_media": music("music_play_media", uri="library://track/9", player_id="ply0002"),
        "music_screen_queue_clear": music("music_queue_clear", queue_id="ply0001"),
        "music_screen_queue_remove": music("music_queue_remove", queue_id="ply0001", item_id="qi1"),
        "music_screen_queue_play_index": music("music_queue_play_index", queue_id="ply0001", index=2),
        "music_screen_favorite": music("music_favorite", uri="library://track/9"),
        # the uri resolves to the LIBRARY row Music Assistant's remove command needs (media type + library id)
        "music_screen_unfavorite": music("music_unfavorite", uri="library://track/9",
                                         answers={"music/item_by_uri": {"provider": "library", "item_id": 2, "media_type": "track",
                                                                        "uri": "library://track/9", "name": "Womble Song"}}),
        "music_screen_group": music("music_group", target_player_id="ply0001", add=["ply0002"],
                                    answers={"players/all": [{**PLAYERS[0], "supported_features": ["set_members"]}, PLAYERS[1]]}),
        "music_screen_queue_move": music("music_queue_move", queue_id="ply0001", item_id="qi1", to_index=2,
                                         answers={"player_queues/items": [{"queue_item_id": q} for q in ("qi1", "qi2", "qi3")]}),
        "music_screen_queue_save": music("music_queue_save", queue_id="ply0001", name="Womble mix"),
        "music_screen_playlist_add": music("music_playlist_add", playlist_uri="library://playlist/1", track_uri="library://track/9"),
        "music_screen_dont_stop": music("music_dont_stop", enabled=True),
        "music_screen_ungroup": music("music_ungroup", player_id="ply0002"),
        # MUSIC: spoken (Skybridge) and the intent executor
        "music_spoken_play": said_music("play", "womble"),
        "music_spoken_pause": said_music("pause"),
        "music_spoken_transfer": said_music("transfer", "bedroom"),
        "music_spoken_discovery": said_music("play", "my discovery playlist",
                                             answers={"music/playlists/library_items": [
                                                 {"name": "Zoe Discovery", "uri": "library://playlist/7", "item_id": "7"}]}),
        "music_intent_favorite": music_intent("music_favorite"),       # "I like this song": favourite what is playing
        "music_intent_pause": music_intent("music_control", command="pause"),
        "music_intent_volume_down": music_intent("music_control", command="volume_down"),
        "music_intent_volume_set": music_intent("music_volume", level=30),
        "music_intent_play": music_intent("music_play", query="womble"),
        "music_intent_delayed": music_intent("music_play", query="womble", delayed=True),
        # MUSIC through a Home Assistant media player (Music Assistant is playing nothing)
        "music_house_pause": music_through_the_house("music_control", command="pause"),
        "music_house_volume_set": music_through_the_house("music_volume", level=30),
        # HOME: the panel's control route
        "home_screen_turn_on": home(entity_id="light.kitchen_lamp", action="turn_on", params={"brightness_pct": 80}),
        "home_screen_off": home(entity_id="light.hall_lamp", action="off"),
        "home_screen_toggle_a_switch": home(entity_id="switch.kettle_plug", action="toggle"),
        "home_screen_scene": home(domain="scene", service="turn_on", entity_id="scene.movie_time"),
        # HOME: unlocking or opening (home_opening.py). Each exists BOTH ways: by an actor main allows (an adult member; the
        # panel while an adult's verified voice session is open on it), where the SEND is pinned, and by one main refuses (the
        # bare panel, a guest, a child, no turn at all), where the REFUSAL is pinned and nothing is sent.
        "home_screen_open_a_cover": home(entity_id="cover.garage_door", action="open"),
        "home_screen_open_a_cover_bare_panel": home(who=BARE_PANEL, entity_id="cover.garage_door", action="open"),
        "home_screen_unlock": home(entity_id="lock.front_door", action="unlock"),
        "home_screen_unlock_guest": home(who=GUEST, entity_id="lock.front_door", action="unlock"),
        "home_screen_unlock_child": home(who=child, entity_id="lock.front_door", action="unlock"),
        "home_screen_unlock_bare_panel": home(who=BARE_PANEL, entity_id="lock.front_door", action="unlock"),
        "home_screen_unlock_panel_adult_voice_session": home(who=BARE_PANEL, session={PANEL: ANN}, entity_id="lock.front_door", action="unlock"),
        "home_screen_unlock_panel_child_voice_session": home(who=BARE_PANEL, session={PANEL: KIP}, entity_id="lock.front_door", action="unlock"),
        "home_screen_lock_bare_panel": home(who=BARE_PANEL, entity_id="lock.front_door", action="lock"),      # locking: anyone
        "home_screen_close_a_cover_guest": home(who=GUEST, entity_id="cover.garage_door", action="close"),    # closing: anyone
        "home_screen_media_pause": home(entity_id="media_player.lounge", action="pause"),
        "home_screen_no_entity": home(action="turn_on"),
        "home_screen_bridge_refuses": home_down(status=500, entity_id="light.kitchen_lamp", action="turn_on"),
        "home_screen_bridge_offline": home_down(fail="/devices/control", entity_id="light.kitchen_lamp", action="turn_on"),
        # HOME: spoken (Skybridge) and the brain's tool
        "home_spoken_turn_on": said_home("turn_on", "kitchen lamp"),
        "home_spoken_turn_off": said_home("turn_off", "hall lamp"),
        "home_spoken_brightness": said_home("set_brightness", "hall lamp", 40),
        "home_spoken_scene": said_home("activate_scene", "movie time"),
        "home_spoken_nothing_matches": said_home("turn_on", "nothing like it"),
        "home_tool_turn_on": tool_home("light.kitchen_lamp", "turn_on", {"brightness_pct": 50}),
        "home_tool_unlock": on_a_turn_of(adult, tool_home("lock.front_door", "unlock")),
        "home_tool_unlock_bare_panel": on_a_turn_of(BARE_PANEL, tool_home("lock.front_door", "unlock")),
        "home_tool_unlock_child": on_a_turn_of(child, tool_home("lock.front_door", "unlock")),
        "home_tool_unlock_no_turn": on_a_turn_of(None, tool_home("lock.front_door", "unlock")),
        "home_tool_lock_guest": on_a_turn_of(GUEST, tool_home("lock.front_door", "lock")),
        "home_spoken_open_a_cover": on_a_turn_of(adult, said_garage),
        "home_spoken_open_a_cover_guest": on_a_turn_of(GUEST, said_garage),
        "home_spoken_open_a_cover_no_turn": on_a_turn_of(None, said_garage),
        # the device helper under the spoken path (its own backstop; the resolver's refusal comes first when it is reached)
        "home_helper_open_a_cover": on_a_turn_of(adult, helper_set("cover.garage_door", True)),
        "home_helper_open_a_cover_bare_panel": on_a_turn_of(BARE_PANEL, helper_set("cover.garage_door", True)),
        "home_helper_lamp_guest": on_a_turn_of(GUEST, helper_set("light.kitchen_lamp", True)),
        "home_tool_bridge_offline": tool_home_offline,
    }


def _isolated() -> None:
    """Refuse to run outside the test configuration: a music or home command reaches stores that default under the live ``~/.zoe`` (logs, the
    shadow's files). ``tests/conftest.py`` points each at a temporary directory."""
    import os

    live = os.path.join(os.path.expanduser("~"), ".zoe")
    for var in ("ZOE_LOG_DIR", "MEMPALACE_DATA_DIR", "ZOE_MEMORY_REJECT_LEDGER"):
        value = os.environ.get(var, "")
        if not value or os.path.abspath(value).startswith(live):
            raise RuntimeError(f"music and home scenarios need the test configuration ({var} is not isolated): import tests/conftest.py first")


def run_all() -> dict:
    _isolated()
    out = {}
    for name, call in scenarios().items():
        log: list = []
        try:
            world = World(log)
            try:
                log.append(["returned", _plain(asyncio.run(call(world)))])
            except Exception as exc:  # noqa: BLE001 - a refusal is part of the record (an HTTP error by its words; any other by TYPE)
                log.append(["raised", getattr(exc, "status_code", None), str(getattr(exc, "detail", None) or type(exc).__name__)])
        finally:
            _restore()
        out[name] = log
    return out


if __name__ == "__main__":
    sys.path[:0] = [sys.argv[1], sys.argv[1].rstrip("/") + "/tests"]
    import conftest  # noqa: F401 - that tree's test configuration: every live store path pinned to a temporary directory
    record = run_all()
    text = "{\n" + ",\n".join(f" {json.dumps(k)}: {json.dumps(record[k], sort_keys=True)}" for k in sorted(record)) + "\n}\n"
    Path(sys.argv[2]).write_text(text, "utf-8")
