"""An MA outage must not lock the operator out of their own speakers.

`music_service._ma` NEVER raises: a transport failure returns None, and
`get_players()` turns that into `[]`. So `[]` is ambiguous — it means either
"MA has no players" or "we could not see the roster". `routers/AGENTS.md`
records the rule this ambiguity forces:

    An empty MA player list means "cannot validate", not "no players".
    Treating [] as an empty set rejects every id and locks the operator out
    during an MA outage.

Two call sites were still treating `[]` as an empty set. Both validated a
caller-supplied player_id against the list and rejected on a miss, so during a
brief MA blip every id — including the speaker sitting in the next room — came
back "unknown".

FIXTURE PROVENANCE: the player id and shape are copied from the live MA 2.8.7
instance (the Bedroom Sonos). Only the fields these functions read are kept.
"""
import pytest

pytestmark = pytest.mark.ci_safe

import music_service
from routers.music import set_preferred_player

# Verbatim id from the live house; the only fields these code paths read.
LIVE_PLAYER = {"player_id": "RINCON_347E5C9BEC8F01400", "name": "Bedroom"}
OTHER_ID = "up286412cf6eb7"


@pytest.mark.asyncio
async def test_preferred_player_accepted_when_ma_roster_is_invisible(monkeypatch):
    """The reported bug: setting a default speaker during an MA outage.

    Nothing about the player list is knowable, so validation cannot run — and a
    preference is local state that has no reason to depend on MA being up.
    """
    async def no_roster():
        return []                       # what get_players() yields when MA is down
    saved = {}
    monkeypatch.setattr(music_service, "get_players", no_roster)
    monkeypatch.setattr(music_service, "set_preferred_player_id",
                        lambda pid: saved.setdefault("pid", pid))

    result = await set_preferred_player({"player_id": LIVE_PLAYER["player_id"]})

    assert result["ok"] is True, "an MA blip must not reject a real speaker"
    assert saved.get("pid") == LIVE_PLAYER["player_id"], "the preference must persist"


@pytest.mark.asyncio
async def test_preferred_player_still_rejects_a_bogus_id_when_roster_is_visible(monkeypatch):
    """The guard is narrow: when the roster IS visible, validation still bites.
    Otherwise the fix would trade a lockout for accepting anything.
    """
    async def roster():
        return [LIVE_PLAYER]
    monkeypatch.setattr(music_service, "get_players", roster)
    monkeypatch.setattr(music_service, "set_preferred_player_id", lambda pid: None)

    result = await set_preferred_player({"player_id": "totally-made-up"})

    assert result["ok"] is False
    assert result["reason"] == "unknown player_id"


@pytest.mark.asyncio
async def test_preferred_player_accepts_a_known_id_normally(monkeypatch):
    async def roster():
        return [LIVE_PLAYER, {"player_id": OTHER_ID, "name": "MacBook"}]
    saved = {}
    monkeypatch.setattr(music_service, "get_players", roster)
    monkeypatch.setattr(music_service, "set_preferred_player_id",
                        lambda pid: saved.setdefault("pid", pid))

    result = await set_preferred_player({"player_id": OTHER_ID})

    assert result["ok"] is True and saved.get("pid") == OTHER_ID


@pytest.mark.asyncio
async def test_play_media_forwards_when_ma_roster_is_invisible(monkeypatch):
    """Same bug class, second site. play_media rejected an explicit player_id as
    "unknown player" whenever the roster came back empty. When we cannot
    validate, forward and let MA arbitrate — it is the authority on its own
    players, and it may well be reachable for the play even if the roster read
    blipped.
    """
    async def no_roster():
        return []
    calls = []
    async def fake_ma_ok(command, **args):
        calls.append(command)
        return True
    monkeypatch.setattr(music_service, "get_players", no_roster)
    monkeypatch.setattr(music_service, "_ma_ok", fake_ma_ok)

    result = await music_service.play_media(
        "ytmusic://track/x", player_id=LIVE_PLAYER["player_id"])

    assert result.get("reason") != "unknown player", (
        "an invisible roster must not turn a real speaker into 'unknown player'"
    )
    assert calls, "the command must still be forwarded to MA"


@pytest.mark.asyncio
async def test_play_media_still_rejects_a_bogus_id_when_roster_is_visible(monkeypatch):
    async def roster():
        return [LIVE_PLAYER]
    monkeypatch.setattr(music_service, "get_players", roster)

    result = await music_service.play_media("ytmusic://track/x", player_id="nope")

    assert result["ok"] is False and result["reason"] == "unknown player"
