"""The spoken half of linking a pendant (``omi_link_tier.py``; record ``docs/knowledge/omi-linking.md``): "Zoe, link my pendant",
"unlink my pendant", "is my pendant linked?".

Negative controls - remove the guard by Edit and the named test turns red:
  ``_verified_here`` (accepting ``speaker_verified`` None/False)        -> test_an_unverified_voice_is_refused_and_opens_nothing
  the audience check for guests                                        -> test_a_guest_or_a_child_gets_a_reason_and_nothing_else
  ``manner_block.is_adult`` in the link branch                          -> test_a_guest_or_a_child_gets_a_reason_and_nothing_else
  ``omi_linking.available()``                                           -> test_linking_off_is_said_plainly
  the VOICE_CHANNELS / panel check                                      -> test_typed_chat_is_told_to_use_the_panel
  ``allow_writes is False`` return                                      -> test_a_dry_run_changes_nothing
  ``self_unassign`` owning check (via ``owned_by``)                      -> test_unlink_by_voice_is_my_own_pendant_only

Synthetic data only.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe

import asyncio

import enrollment_state as es
import omi_link_tier as olt
import omi_linking
from test_omi_device_owners import DEV_A, DEV_B, env, register  # noqa: F401
from test_omi_linking import Panel, link, owner  # noqa: F401

AUD = {"ann": "adult", "ben": "adult", "kid": "minor", "guest": "guest"}


@pytest.fixture
def tier_env(link, monkeypatch):
    import bluetooth_broker

    async def audience(uid):
        return AUD.get(uid, "unknown")
    monkeypatch.setattr(es, "audience_of", audience)
    monkeypatch.setattr(bluetooth_broker, "BROKER", link.broker)
    started = []

    async def no_driver(w, **kw):
        started.append((w.panel_id, w.user_id))
        return w
    monkeypatch.setattr(omi_linking, "drive", no_driver)
    Panel(link.broker, "p1", link.clock, [])
    link.started = started
    return link


def say(text, uid="ann", verified=True, channel="voice", panel="p1", writes=True):
    res = asyncio.run(olt.tier(text, uid, "s1", channel=channel, speaker_verified=verified, panel_id=panel, allow_writes=writes))
    return None if res is None else res.reply


def test_the_words_route_only_these_requests():
    for t in ("Hey Zoe, link my pendant", "link my omi", "could you pair my pendant please", "connect my omi pendant to you"):
        assert olt.classify(t) == "link", t
    for t in ("unlink my pendant", "Zoe, remove my omi", "disconnect my pendant please"):
        assert olt.classify(t) == "unlink", t
    for t in ("is my pendant linked?", "have I linked my omi"):
        assert olt.classify(t) == "status", t
    for t in ("link my calendar", "what is a pendant", "my pendant is lovely", "add milk to my list", ""):
        assert olt.classify(t) is None, t


def test_an_unrelated_turn_is_left_alone(tier_env):
    assert say("what's the weather tomorrow") is None


def test_a_verified_member_opens_the_window_with_the_notice_once(tier_env):
    first = say("link my pendant")
    assert first == omi_linking.say("start_with_notice", notice=omi_linking.notice())
    assert tier_env.started == [("p1", "ann")]
    asyncio.run(_ack("ann"))
    assert say("link my pendant") == omi_linking.say("start"), "the notice is said once"


async def _ack(uid):
    import db_compat
    import omi_member_settings as ms
    async with db_compat.get_compat_db() as db:
        await ms.acknowledge_notice(db, uid)


def test_an_unverified_voice_is_refused_and_opens_nothing(tier_env):
    for v in (False, None):
        assert say("link my pendant", verified=v) == omi_linking.say("unverified")
    assert tier_env.started == [] and omi_linking.status("ann") is None
    # control: a fresh PIN sign-in at THIS panel is proof too
    import biometric_scope

    async def fresh(pid):
        return "ann" if pid == "p1" else None
    tier_env.mp.setattr(biometric_scope, "_fresh_panel_member", fresh)
    assert say("link my pendant", verified=None) != omi_linking.say("unverified") and tier_env.started == [("p1", "ann")]


def test_a_guest_or_a_child_gets_a_reason_and_nothing_else(tier_env):
    assert say("link my pendant", uid="guest") == omi_linking.say("guest")
    assert say("link my pendant", uid="kid") == omi_linking.say("child")
    assert tier_env.started == []


def test_linking_off_is_said_plainly(tier_env, monkeypatch):
    monkeypatch.delenv("ZOE_OMI_DEVICE_OWNERS")
    reply = say("link my pendant")
    assert reply == omi_linking.say("off_linking_off") and "ask the household admin to turn on pendant linking" in reply.lower()
    assert tier_env.started == []


def test_typed_chat_is_told_to_use_the_panel(tier_env):
    assert say("link my pendant", channel="chat", panel="") == omi_linking.say("no_panel")
    assert tier_env.started == []


def test_a_dry_run_changes_nothing(tier_env):
    assert say("link my pendant", writes=False) is None and tier_env.started == []
    register(tier_env, DEV_A, "ann")
    assert say("unlink my pendant", writes=False) is None and owner(tier_env, DEV_A) == "ann"


def test_unlink_by_voice_is_my_own_pendant_only(tier_env):
    register(tier_env, DEV_B, "ben")
    assert say("unlink my pendant") == omi_linking.say("unlink_none") and owner(tier_env, DEV_B) == "ben"
    register(tier_env, DEV_A, "ann")
    assert say("is my pendant linked") == omi_linking.say("status_linked")
    assert say("unlink my pendant") == omi_linking.say("unlink_done_warn")
    assert owner(tier_env, DEV_A) == "" and owner(tier_env, DEV_B) == "ben"
    assert say("unlink my pendant", verified=False) == omi_linking.say("unverified")
