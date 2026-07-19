"""Multi-room speaker grouping — grouping view + join/unjoin (mocked MA).

FIXTURE PROVENANCE (this matters — a fixture that invents fields tests nothing):
every player dict below is a field-for-field trim of a REAL `players/all`
response from the live MA 2.8.7 / schema 29 instance on 2026-07-19. Ids, names,
providers, `supported_features`, `can_group_with`, `static_group_members` and
the availability flags are copied verbatim; only fields this module never reads
(device_info, output_protocols, current_media, ...) were dropped.

The ONE exception is `SYNC_LEADER` / `SYNC_FOLLOWER`: no sync group existed on
the live house at capture time and forming one would have moved a real speaker
in an occupied home, so those two are derived from MA's own source contract
(`music_assistant/models/player.py` — `group_members` docstring at :295-305 and
`__final_group_members` at :1806-1842, which guarantees the leader's own id is
FIRST in its `group_members`). They are marked source-derived, not live-observed.
"""
import pytest

import music_service

pytestmark = pytest.mark.ci_safe


# ── live-captured players (verbatim trims) ───────────────────────────────────

MACBOOK = {
    "player_id": "up286412cf6eb7",
    "display_name": "Jason’s MacBook Pro (2)",
    "name": "Jason’s MacBook Pro (2)",
    "provider": "universal_player",
    "type": "player",
    "available": True,
    "powered": True,
    "state": "idle",
    "playback_state": "idle",
    "synced_to": None,
    "active_group": None,
    "group_members": [],
    "group_childs": [],
    "static_group_members": [],
    "can_group_with": [
        "RINCON_347E5C9BEC8F01400", "07af8dad-cc27-a42f-dffb-b9025e92344b",
        "ap40cbc0db9fb8", "apf2e069e22442", "ap9c207b93ae6d",
        "f2f19f55-f07f-4604-d698-cbb4d3da43bc",
        "60580bba-90c8-ae48-f22f-6b441b3d2a4e", "RINCON_38420B45B65001400",
        "b72b454a-9e06-e7a2-61fd-ba158dd2c831",
    ],
    "supported_features": ["volume_set", "volume_mute", "set_members"],
}

BEDROOM_SONOS = {
    "player_id": "RINCON_347E5C9BEC8F01400",
    "display_name": "Bedroom",
    "name": "Bedroom",
    "provider": "sonos",
    "type": "player",
    "available": True,
    "powered": True,
    "state": "playing",
    "playback_state": "playing",
    "synced_to": None,
    "active_group": None,
    "group_members": [],
    "group_childs": [],
    "static_group_members": [],
    "can_group_with": ["up286412cf6eb7", "b72b454a-9e06-e7a2-61fd-ba158dd2c831"],
    "supported_features": [
        "enqueue", "gapless_playback", "next_previous", "pause", "play_announcement",
        "play_media", "seek", "select_source", "set_members", "volume_mute", "volume_set",
    ],
}

# Same display_name as the Sonos zone above — a real collision in this house.
BEDROOM_AIRPLAY = {
    "player_id": "ap40cbc0db9fb8",
    "display_name": "Bedroom",
    "name": "Bedroom",
    "provider": "airplay",
    "type": "player",
    "available": False,          # genuinely offline at capture time
    "powered": True,
    "state": "idle",
    "playback_state": "idle",
    "synced_to": None,
    "active_group": None,
    "group_members": [],
    "group_childs": [],
    "static_group_members": [],
    "can_group_with": ["up286412cf6eb7"],
    "supported_features": [
        "multi_device_dsp", "pause", "play_media", "set_members",
        "volume_mute", "volume_set",
    ],
}

KITCHEN = {
    "player_id": "b72b454a-9e06-e7a2-61fd-ba158dd2c831",
    "display_name": "Kitchen Display",
    "name": "Kitchen Display",
    "provider": "chromecast",
    "type": "player",
    "available": True,
    "powered": True,
    "state": "idle",
    "playback_state": "idle",
    "synced_to": None,
    "active_group": None,
    "group_members": [],
    "group_childs": [],
    "static_group_members": [],
    "can_group_with": ["up286412cf6eb7", "RINCON_347E5C9BEC8F01400"],
    "supported_features": [
        "enqueue", "next_previous", "pause", "play_media", "seek",
        "set_members", "volume_mute", "volume_set",
    ],
}

# A TV that cannot group at all: can_group_with empty, no set_members feature.
SAMSUNG_TV = {
    "player_id": "upe0036b3da273",
    "display_name": "Samsung Q80CA 98",
    "name": "Samsung Q80CA 98",
    "provider": "universal_player",
    "type": "player",
    "available": True,
    "powered": True,
    "state": "idle",
    "playback_state": "idle",
    "synced_to": None,
    "active_group": None,
    "group_members": [],
    "group_childs": [],
    "static_group_members": [],
    "can_group_with": [],
    "supported_features": ["volume_set", "volume_mute"],
}

# The pre-existing Chromecast group player. type == "group", members do NOT
# include itself, static_group_members non-empty => fixed membership.
HOUSE_GROUP = {
    "player_id": "e0951e90-9fad-424a-84ac-08a1e0d720a6",
    "display_name": "House",
    "name": "House",
    "provider": "chromecast",
    "type": "group",
    "available": True,
    "powered": False,
    "state": "idle",
    "playback_state": "idle",
    "synced_to": None,
    "active_group": None,
    "group_members": [
        "f2f19f55-f07f-4604-d698-cbb4d3da43bc",
        "07af8dad-cc27-a42f-dffb-b9025e92344b",
        "b72b454a-9e06-e7a2-61fd-ba158dd2c831",
    ],
    "group_childs": [
        "f2f19f55-f07f-4604-d698-cbb4d3da43bc",
        "07af8dad-cc27-a42f-dffb-b9025e92344b",
        "b72b454a-9e06-e7a2-61fd-ba158dd2c831",
    ],
    "static_group_members": [
        "b72b454a-9e06-e7a2-61fd-ba158dd2c831",
        "f2f19f55-f07f-4604-d698-cbb4d3da43bc",
        "07af8dad-cc27-a42f-dffb-b9025e92344b",
    ],
    "can_group_with": [],
    # NOTE: no "set_members" — this fixed group genuinely cannot be regrouped.
    "supported_features": [
        "enqueue", "pause", "play_media", "power", "volume_mute", "volume_set",
    ],
}

# ── source-derived (see module docstring): an active sync group ──────────────
SYNC_LEADER = {
    **BEDROOM_SONOS,
    # MA guarantees the leader's OWN id is first (player.py:1837).
    "group_members": ["RINCON_347E5C9BEC8F01400", "b72b454a-9e06-e7a2-61fd-ba158dd2c831"],
}
SYNC_FOLLOWER = {**KITCHEN, "synced_to": "RINCON_347E5C9BEC8F01400"}


LIVE_HOUSE = [MACBOOK, BEDROOM_SONOS, BEDROOM_AIRPLAY, KITCHEN, SAMSUNG_TV, HOUSE_GROUP]


def _row(view, player_id):
    return next(r for r in view["players"] if r["player_id"] == player_id)


# ── the grouping view ────────────────────────────────────────────────────────

def test_ungrouped_house_reports_every_player_solo():
    """Live state at capture: nothing synced, so no player claims a leader."""
    view = music_service.build_group_view([MACBOOK, BEDROOM_SONOS, KITCHEN])
    for row in view["players"]:
        assert row["role"] == "solo"
        assert row["grouped"] is False
        assert row["leader_id"] == ""
        assert row["group_member_ids"] == []
    assert view["groups"] == []


def test_sync_leader_owns_the_queue_and_is_listed_first():
    view = music_service.build_group_view([SYNC_LEADER, SYNC_FOLLOWER, MACBOOK])
    leader = _row(view, "RINCON_347E5C9BEC8F01400")
    follower = _row(view, "b72b454a-9e06-e7a2-61fd-ba158dd2c831")

    assert leader["role"] == "leader"
    assert leader["leader_id"] == "RINCON_347E5C9BEC8F01400"
    assert follower["role"] == "follower"
    # The follower must point at the player that owns the queue.
    assert follower["leader_id"] == "RINCON_347E5C9BEC8F01400"

    # Both sides see the SAME membership, leader first.
    expected = ["RINCON_347E5C9BEC8F01400", "b72b454a-9e06-e7a2-61fd-ba158dd2c831"]
    assert leader["group_member_ids"] == expected
    assert follower["group_member_ids"] == expected

    assert _row(view, "up286412cf6eb7")["role"] == "solo"

    (group,) = view["groups"]
    assert group["leader_id"] == "RINCON_347E5C9BEC8F01400"
    assert group["leader_name"] == "Bedroom"
    assert group["member_ids"] == expected
    assert group["member_names"] == ["Bedroom", "Kitchen Display"]
    assert group["is_virtual_leader"] is False
    assert group["is_static"] is False


def test_group_player_is_a_virtual_static_leader():
    """The House group leads its children but is not itself a speaker."""
    view = music_service.build_group_view(LIVE_HOUSE)
    house = _row(view, "e0951e90-9fad-424a-84ac-08a1e0d720a6")
    assert house["role"] == "leader"
    assert house["is_group_player"] is True
    assert house["is_static_group"] is True
    # A group player is NOT a member of itself.
    assert "e0951e90-9fad-424a-84ac-08a1e0d720a6" not in house["group_member_ids"]
    assert house["group_member_ids"] == HOUSE_GROUP["group_childs"]

    group = next(g for g in view["groups"]
                 if g["leader_id"] == "e0951e90-9fad-424a-84ac-08a1e0d720a6")
    assert group["is_virtual_leader"] is True
    assert group["is_static"] is True


def test_permanent_group_member_follows_its_active_group():
    member = {**KITCHEN, "active_group": "e0951e90-9fad-424a-84ac-08a1e0d720a6"}
    view = music_service.build_group_view([HOUSE_GROUP, member])
    row = _row(view, "b72b454a-9e06-e7a2-61fd-ba158dd2c831")
    assert row["role"] == "follower"
    assert row["leader_id"] == "e0951e90-9fad-424a-84ac-08a1e0d720a6"


def test_active_group_beats_synced_to():
    """Both set: the permanent group player owns the queue, so it wins."""
    both = {**KITCHEN, "active_group": "grp", "synced_to": "other"}
    assert music_service.resolve_group_role(both) == ("follower", "grp")


def test_follower_is_kept_even_when_leader_has_not_caught_up():
    """Mid-regroup the leader may not list the member yet — it must not vanish."""
    stale_leader = {**BEDROOM_SONOS, "group_members": []}
    view = music_service.build_group_view([stale_leader, SYNC_FOLLOWER])
    follower = _row(view, "b72b454a-9e06-e7a2-61fd-ba158dd2c831")
    assert follower["group_member_ids"] == ["b72b454a-9e06-e7a2-61fd-ba158dd2c831"]


def test_unavailable_player_is_still_listed_and_still_groupable():
    """An offline speaker must never disappear from the picker."""
    view = music_service.build_group_view(LIVE_HOUSE)
    airplay = _row(view, "ap40cbc0db9fb8")
    assert airplay["available"] is False
    assert airplay["can_lead"] is True
    assert airplay["can_group_with"] == ["up286412cf6eb7"]


def test_provider_disambiguates_duplicate_display_names():
    """Two speakers are both called "Bedroom" in this house."""
    view = music_service.build_group_view(LIVE_HOUSE)
    bedrooms = [r for r in view["players"] if r["name"] == "Bedroom"]
    assert len(bedrooms) == 2
    assert {r["provider"] for r in bedrooms} == {"sonos", "airplay"}


def test_non_groupable_player_is_marked_not_groupable():
    view = music_service.build_group_view(LIVE_HOUSE)
    tv = _row(view, "upe0036b3da273")
    assert tv["can_lead"] is False
    assert tv["can_group_with"] == []


def test_name_never_empty():
    nameless = {"player_id": "x1", "display_name": "", "name": None}
    assert music_service.build_group_view([nameless])["players"][0]["name"] == "x1"


def test_display_name_wins_over_name():
    """MA carries both; `display_name` is the user-facing one (it reflects a
    rename in the MA UI, while `name` can stay the raw device name)."""
    renamed = {"player_id": "x1", "display_name": "Kitchen", "name": "Google-Home-Mini-abc123"}
    assert music_service.build_group_view([renamed])["players"][0]["name"] == "Kitchen"


def test_name_falls_back_to_name_when_display_name_missing():
    bare = {"player_id": "x1", "name": "Patio"}
    assert music_service.build_group_view([bare])["players"][0]["name"] == "Patio"


def test_players_without_an_id_are_dropped():
    view = music_service.build_group_view([MACBOOK, {"name": "junk"}, "not-a-dict"])
    assert [r["player_id"] for r in view["players"]] == ["up286412cf6eb7"]


# ── can_group_with resolution ────────────────────────────────────────────────

def test_can_group_with_drops_self_and_unknown_ids():
    player = {**MACBOOK, "can_group_with": ["up286412cf6eb7", "ghost", "b72b454a-9e06-e7a2-61fd-ba158dd2c831"]}
    assert music_service.resolve_can_group_with(player, [player, KITCHEN]) == [
        "b72b454a-9e06-e7a2-61fd-ba158dd2c831",
    ]


def test_can_group_with_accepts_the_provider_instance_shape():
    """MA may return a PROVIDER id meaning "all of that provider's players"."""
    player = {**MACBOOK, "can_group_with": ["chromecast"]}
    resolved = music_service.resolve_can_group_with(player, [player, KITCHEN, HOUSE_GROUP])
    assert resolved == ["b72b454a-9e06-e7a2-61fd-ba158dd2c831",
                        "e0951e90-9fad-424a-84ac-08a1e0d720a6"]


def test_can_group_with_survives_a_junk_payload():
    assert music_service.resolve_can_group_with({**MACBOOK, "can_group_with": "nope"}, []) == []


# ── writes: join / unjoin ────────────────────────────────────────────────────

class _FakeMA:
    """Records the command + args Zoe actually sends to MA."""

    def __init__(self, players=LIVE_HOUSE, accept=True):
        self._players = players
        self.accept = accept
        self.calls = []

    async def ma(self, command, **args):
        self.calls.append((command, args))
        if command == "players/all":
            return self._players
        return None

    async def ma_ok(self, command, timeout_s=5.0, **args):
        self.calls.append((command, args))
        return self.accept


@pytest.fixture
def fake_ma(monkeypatch):
    fake = _FakeMA()
    monkeypatch.setattr(music_service, "_ma", fake.ma)
    monkeypatch.setattr(music_service, "_ma_ok", fake.ma_ok)
    return fake


@pytest.mark.asyncio
async def test_group_sends_set_members_with_nested_args(fake_ma):
    result = await music_service.group_players(
        "RINCON_347E5C9BEC8F01400", add=["b72b454a-9e06-e7a2-61fd-ba158dd2c831"],
    )
    assert result["ok"] is True
    command, args = fake_ma.calls[-1]
    assert command == "players/cmd/set_members"
    # Exact MA parameter names — a wrong name is silently swallowed into
    # **kwargs and MA still returns 200, so this assertion is load-bearing.
    assert args == {
        "target_player": "RINCON_347E5C9BEC8F01400",
        "player_ids_to_add": ["b72b454a-9e06-e7a2-61fd-ba158dd2c831"],
        "player_ids_to_remove": None,
    }


@pytest.mark.asyncio
async def test_group_applies_add_and_remove_in_one_call(fake_ma):
    await music_service.group_players(
        "RINCON_347E5C9BEC8F01400",
        add=["b72b454a-9e06-e7a2-61fd-ba158dd2c831"],
        remove=["up286412cf6eb7"],
    )
    command, args = fake_ma.calls[-1]
    assert command == "players/cmd/set_members"
    assert args["player_ids_to_add"] == ["b72b454a-9e06-e7a2-61fd-ba158dd2c831"]
    assert args["player_ids_to_remove"] == ["up286412cf6eb7"]


@pytest.mark.asyncio
async def test_group_reports_failure_when_ma_rejects(monkeypatch):
    fake = _FakeMA(accept=False)
    monkeypatch.setattr(music_service, "_ma", fake.ma)
    monkeypatch.setattr(music_service, "_ma_ok", fake.ma_ok)
    result = await music_service.group_players("RINCON_347E5C9BEC8F01400",
                                               add=["up286412cf6eb7"])
    assert result["ok"] is False
    # An MA rejection is still a failure, so it must carry `reason` like every
    # other one — a caller reading result["reason"] must never KeyError.
    assert result["reason"]


@pytest.mark.asyncio
async def test_every_failure_carries_a_reason(monkeypatch):
    """The failure contract is `{ok: false, reason}` with NO exceptions.

    Covers both origins of failure — local validation AND an MA rejection —
    across both write endpoints, so a third response shape cannot creep back in.
    """
    fake = _FakeMA(accept=False)
    monkeypatch.setattr(music_service, "_ma", fake.ma)
    monkeypatch.setattr(music_service, "_ma_ok", fake.ma_ok)

    failures = [
        await music_service.group_players(""),                       # validation
        await music_service.group_players("RINCON_347E5C9BEC8F01400"),
        await music_service.group_players("upe0036b3da273", add=["up286412cf6eb7"]),
        await music_service.group_players("RINCON_347E5C9BEC8F01400",  # MA rejected
                                          add=["up286412cf6eb7"]),
        await music_service.ungroup_player(""),                      # validation
        await music_service.ungroup_player("up286412cf6eb7"),        # MA rejected
    ]
    for result in failures:
        assert result["ok"] is False
        assert isinstance(result.get("reason"), str) and result["reason"], result


@pytest.mark.asyncio
@pytest.mark.parametrize("target,add,remove,reason", [
    ("", ["up286412cf6eb7"], [], "missing target_player_id"),
    ("RINCON_347E5C9BEC8F01400", [], [], "nothing to add or remove"),
    ("RINCON_347E5C9BEC8F01400", ["RINCON_347E5C9BEC8F01400"], [], "target cannot join itself"),
    ("nope", ["up286412cf6eb7"], [], "unknown target_player_id"),
    ("RINCON_347E5C9BEC8F01400", ["ghost"], [], "unknown player_id: ghost"),
    ("upe0036b3da273", ["up286412cf6eb7"], [], "target player does not support grouping"),
    ("e0951e90-9fad-424a-84ac-08a1e0d720a6", ["up286412cf6eb7"], [], "target is a fixed provider group"),
])
async def test_group_rejects_bad_requests(fake_ma, target, add, remove, reason):
    result = await music_service.group_players(target, add=add, remove=remove)
    assert result == {"ok": False, "reason": reason}
    assert not any(c == "players/cmd/set_members" for c, _ in fake_ma.calls)


@pytest.mark.asyncio
async def test_ma_outage_does_not_lock_the_operator_out(monkeypatch):
    """THE bug class this endpoint must not have.

    `_ma` returns None on transport failure, not an exception. Treating that as
    "no such player" would reject every id and make the operator's own speakers
    un-selectable during an MA blip. The command must still be forwarded.
    """
    sent = []

    async def dead_ma(command, **args):
        return None

    async def ma_ok(command, timeout_s=5.0, **args):
        sent.append((command, args))
        return True

    monkeypatch.setattr(music_service, "_ma", dead_ma)
    monkeypatch.setattr(music_service, "_ma_ok", ma_ok)

    result = await music_service.group_players("RINCON_347E5C9BEC8F01400",
                                               add=["b72b454a-9e06-e7a2-61fd-ba158dd2c831"])
    assert result["ok"] is True
    assert sent and sent[-1][0] == "players/cmd/set_members"


@pytest.mark.asyncio
async def test_empty_player_list_means_cannot_validate_not_no_players(monkeypatch):
    """The second half of the lockout bug — and the sharper half.

    A 200 with `[]` is indistinguishable from "MA hasn't discovered the players
    yet". `routers/AGENTS.md` records this exact trap: treating `[]` as an empty
    SET rejects every id and locks the operator out of their own speakers. The
    command must still be forwarded.
    """
    fake = _FakeMA(players=[])
    monkeypatch.setattr(music_service, "_ma", fake.ma)
    monkeypatch.setattr(music_service, "_ma_ok", fake.ma_ok)

    result = await music_service.group_players(
        "RINCON_347E5C9BEC8F01400", add=["b72b454a-9e06-e7a2-61fd-ba158dd2c831"],
    )
    assert result["ok"] is True
    assert any(c == "players/cmd/set_members" for c, _ in fake.calls)


@pytest.mark.asyncio
async def test_ungroup_is_a_single_argument_call(fake_ma):
    result = await music_service.ungroup_player("b72b454a-9e06-e7a2-61fd-ba158dd2c831")
    assert result == {"ok": True, "player_id": "b72b454a-9e06-e7a2-61fd-ba158dd2c831"}
    command, args = fake_ma.calls[-1]
    assert command == "players/cmd/ungroup"
    assert args == {"player_id": "b72b454a-9e06-e7a2-61fd-ba158dd2c831"}


@pytest.mark.asyncio
async def test_ungroup_rejects_empty_id(fake_ma):
    assert await music_service.ungroup_player("") == {"ok": False, "reason": "missing player_id"}
    assert not fake_ma.calls


# ── read endpoint availability semantics ─────────────────────────────────────

@pytest.mark.asyncio
async def test_get_speaker_groups_returns_none_when_ma_is_down(monkeypatch):
    async def dead_ma(command, **args):
        return None
    monkeypatch.setattr(music_service, "_ma", dead_ma)
    assert await music_service.get_speaker_groups() is None


@pytest.mark.asyncio
async def test_get_speaker_groups_distinguishes_empty_house_from_outage(monkeypatch):
    async def empty_ma(command, **args):
        return []
    monkeypatch.setattr(music_service, "_ma", empty_ma)
    view = await music_service.get_speaker_groups()
    assert view == {"players": [], "groups": []}
