"""Voice music fixes (panel bug 2026-07-13, second report).

"Play some music" used to classify as music/STATUS, whose reply is
"Nothing's playing. Ask me to put something on." — a loop: the user asks to
play, Zoe tells them to ask. It must classify as PLAY with an empty query
(resume/default). And "play jazz in the kitchen" must aim at the Kitchen
player instead of searching for the literal phrase.
"""
import pytest

pytestmark = pytest.mark.ci_safe

from music_service import split_play_target
from skybridge_service import _classify_music


def _c(text):
    return _classify_music(f" {text.lower()} ")


@pytest.mark.parametrize("text", ["play some music", "play music", "play something", "play anything"])
def test_generic_play_is_play(text):
    i = _c(text)
    assert i is not None and i.domain == "music" and i.action == "play"
    assert (i.query or "") == ""


def test_specific_play_keeps_query():
    i = _c("play some jazz")
    assert i is not None and i.action == "play" and i.query == "jazz"


def test_whats_playing_stays_status():
    i = _c("what's playing")
    assert i is not None and i.action == "status"


PLAYERS = [
    {"player_id": "p1", "display_name": "Kitchen Display"},
    {"player_id": "p2", "display_name": "Bathroom speaker"},
]


def test_room_suffix_targets_player():
    base, target = split_play_target("jazz in the kitchen", PLAYERS)
    assert base == "jazz"
    assert target and target["player_id"] == "p1"


def test_generic_with_room():
    base, target = split_play_target("music in the kitchen", PLAYERS)
    assert base == ""
    assert target and target["player_id"] == "p1"


def test_non_player_suffix_stays_in_query():
    base, target = split_play_target("golden on youtube music", PLAYERS)
    assert target is None
    assert base == "golden on youtube music"


def test_no_suffix():
    base, target = split_play_target("the beatles", PLAYERS)
    assert base == "the beatles" and target is None
