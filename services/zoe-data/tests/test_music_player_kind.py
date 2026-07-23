"""Server-side device-type resolution for the speaker picker.

FIXTURE PROVENANCE (this matters — a fixture that invents fields tests nothing):
every player dict below is a field-for-field trim of the REAL `GET
/api/music/players` response from the live MA instance behind zoe-data on
2026-07-23. `player_id`, `name`, `provider`, `type` and
`device_info.{model,manufacturer}` are copied verbatim; fields the resolver
never reads were dropped. The whole point of this feature is that the panel can
tell the two identically-named "Bedroom" players apart, so both are here:
the real Sonos Beam (available) and the dead AirPlay Apple TV (unavailable).

`resolve_player_kind` is imported and exercised directly — the expected values
below are the DESIGNED mapping, hand-written, NOT computed by re-running the
function's own logic. (A test that re-implements the code it checks passes on
broken code; this one would go red if the mapping regressed.)
"""
import pytest

from routers.music import resolve_player_kind

pytestmark = pytest.mark.ci_safe


def _p(player_id, name, provider, ptype, model, manufacturer):
    return {
        "player_id": player_id,
        "name": name,
        "provider": provider,
        "type": ptype,
        "device_info": {"model": model, "manufacturer": manufacturer},
    }


# (player, expected_kind, expected_kind_label) — the live 14-player inventory.
LIVE_INVENTORY = [
    (_p("up286412cf6eb7", "Jason’s MacBook Pro (2)", "universal_player",
        "player", "MacBook Pro (MacBookPro18,2)", "Apple"),
     "computer", "MacBook Pro"),
    (_p("upe0036b3da273", "Samsung Q80CA 98", "universal_player",
        "player", "QCQ80", "Samsung"),
     "tv", "Samsung TV"),
    (_p("up2ce55d131cec", "[LG] webOS TV OLED55B8STB", "universal_player",
        "player", "OLED55B8STB", "LG Electronics"),
     "tv", "LG TV"),
    (_p("up00bfafdf46d2", "NT72563_AU(192.168.1.234)", "universal_player",
        "player", "TCL Media Renderer", "TCL"),
     "tv", "TCL TV"),
    (_p("up7931634c4d664cb4e3fa033fcb105adf", "MAD Bedroom TV",
        "universal_player", "player", "Smart TV", "Unknown manufacturer"),
     "tv", "Smart TV"),
    (_p("e0951e90-9fad-424a-84ac-08a1e0d720a6", "House", "chromecast",
        "group", "Google Cast Group", "Google Inc."),
     "group", "Speaker group"),
    (_p("b72b454a-9e06-e7a2-61fd-ba158dd2c831", "Kitchen Display", "chromecast",
        "player", "Google Nest Hub", "Google Inc."),
     "display", "Nest Hub"),
    (_p("60580bba-90c8-ae48-f22f-6b441b3d2a4e", "Bedroom 2 TV", "chromecast",
        "player", "Chromecast HD", "Google Inc."),
     "tv", "Chromecast"),
    (_p("f2f19f55-f07f-4604-d698-cbb4d3da43bc", "Bedroom 3 TV", "chromecast",
        "player", "Chromecast HD", "Google Inc."),
     "tv", "Chromecast"),
    (_p("07af8dad-cc27-a42f-dffb-b9025e92344b", "Bathroom speaker", "chromecast",
        "player", "Google Home Mini", "Google Inc."),
     "speaker", "Home Mini"),
    (_p("RINCON_38420B45B65001400", "Living Room", "sonos",
        "player", "Arc", "SONOS"),
     "speaker", "Sonos Arc"),
    # --- the disambiguation pair: both named "Bedroom" ---
    (_p("RINCON_347E5C9BEC8F01400", "Bedroom", "sonos",
        "player", "Beam", "SONOS"),
     "speaker", "Sonos Beam"),
    (_p("ap40cbc0db9fb8", "Bedroom", "airplay",
        "player", "Apple TV 4K", "Apple"),
     "tv", "Apple TV"),
    (_p("ap9c207b93ae6d", "Parents Lounge Apple TV", "airplay",
        "player", "Apple TV Gen3", "Apple"),
     "tv", "Apple TV"),
]


@pytest.mark.parametrize("player,exp_kind,exp_label", LIVE_INVENTORY,
                         ids=[x[0]["name"] for x in LIVE_INVENTORY])
def test_resolve_kind_and_label(player, exp_kind, exp_label):
    out = resolve_player_kind(player)
    assert out["kind"] == exp_kind, f"{player['name']}: kind {out['kind']!r}"
    assert out["kind_label"] == exp_label, f"{player['name']}: label {out['kind_label']!r}"


def test_two_bedrooms_are_distinguishable():
    """The whole reason this exists: the two "Bedroom" players must differ."""
    sonos = resolve_player_kind(
        _p("RINCON_347E5C9BEC8F01400", "Bedroom", "sonos", "player", "Beam", "SONOS"))
    appletv = resolve_player_kind(
        _p("ap40cbc0db9fb8", "Bedroom", "airplay", "player", "Apple TV 4K", "Apple"))
    assert sonos["kind"] != appletv["kind"]            # speaker vs tv -> different icon
    assert sonos["kind_label"] != appletv["kind_label"]  # "Sonos Beam" vs "Apple TV"


def test_kind_is_from_the_closed_set():
    allowed = {"speaker", "tv", "display", "group", "computer"}
    for player, _k, _l in LIVE_INVENTORY:
        assert resolve_player_kind(player)["kind"] in allowed


def test_name_never_drives_classification():
    """A user-renamed device must classify on its hardware, not its label.

    A Sonos speaker someone called "TV Room" is still a speaker; a Smart TV
    someone called "Bedroom Speakers" is still a tv.
    """
    misleading_speaker = _p("RINCON_x", "TV Room", "sonos", "player", "Beam", "SONOS")
    assert resolve_player_kind(misleading_speaker)["kind"] == "speaker"
    misleading_tv = _p("up_x", "Bedroom Speakers", "universal_player", "player",
                       "OLED55B8STB", "LG Electronics")
    assert resolve_player_kind(misleading_tv)["kind"] == "tv"


def test_display_word_in_a_tv_model_stays_tv():
    """A TV whose model merely CONTAINS the word "display" must not be mistaken
    for a Nest-Hub-style smart display (Greptile finding on PR #1516).

    The display check runs before the TV check, so a bare "display" hint would
    have swallowed these. Only specific smart-display models (nest hub, smart
    display, …) may resolve to `display`.
    """
    monitor = _p("up_mon", "Lounge Screen", "universal_player", "player",
                 "Samsung Smart Monitor M7 Display", "Samsung")
    assert resolve_player_kind(monitor)["kind"] == "tv"

    cc_tv = _p("cc_gtv", "Lounge", "chromecast", "player",
               "Chromecast with Google TV Display", "Google Inc.")
    assert resolve_player_kind(cc_tv)["kind"] == "tv"

    # …and a genuine smart display still resolves to display.
    nest = _p("cc_hub", "Kitchen Display", "chromecast", "player",
              "Google Nest Hub", "Google Inc.")
    assert resolve_player_kind(nest)["kind"] == "display"


def test_missing_device_info_degrades_safely():
    """A player with no device_info must still return a valid kind + label."""
    bare = {"player_id": "x", "name": "Mystery", "provider": "sonos", "type": "player"}
    out = resolve_player_kind(bare)
    assert out["kind"] == "speaker"
    assert out["kind_label"]  # non-empty
    empty = {"player_id": "y", "name": "?", "provider": "", "type": "player",
             "device_info": {}}
    out2 = resolve_player_kind(empty)
    assert out2["kind"] == "speaker"   # unknown default
    assert out2["kind_label"]
