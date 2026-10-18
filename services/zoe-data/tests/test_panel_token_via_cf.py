"""A panel DEVICE TOKEN presented over the Cloudflare tunnel is a plain GUEST (owner decision 2026-10-19, card ``cf-device-token``).

Found by #2112: ``panel_principal.is_panel_guest`` accepted a device-token principal BEFORE its Cloudflare check, so a copy of
the panel's token counted as "the panel" from anywhere on the internet - home control, household lists, music. The owner's
answer: over Cloudflare the token gets guest access only, no home control; the relay (#2095, its own ingress, no Cloudflare
header) is the way in from outside. ``guest`` is the code default; ``ZOE_PANEL_TOKEN_VIA_CF=panel`` is an explicit escape
hatch that restores the old rule. The LAN and the kiosk credential (already refused over Cloudflare) are unaffected.
Invented data only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from starlette.requests import Request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import panel_principal

pytestmark = pytest.mark.ci_safe

TOKEN_USER = {"user_id": "guest", "role": "guest", "panel_id": "kitchen-panel"}   # what get_current_user makes of a device token
CF = {"cf-ray": "8a1b2c3d4e5f-SYD", "cf-connecting-ip": "203.0.113.50"}           # set by Cloudflare's edge, not removable


def _req(headers=None, peer="172.18.0.5"):
    return Request({"type": "http", "method": "POST", "path": "/api/ha/control",
                    "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()], "client": (peer, 1)})


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in ("ZOE_PANEL_TOKEN_VIA_CF", "ZOE_HA_CONTROL_SCOPE", "ZOE_HOUSEHOLD_LISTS_PANEL_SCOPE"):
        monkeypatch.delenv(name, raising=False)


# ── THE FIX, by default: a token over Cloudflare is a plain guest ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, "guest", "GUEST", "", "  ", "nonsense", "off", "1"])
async def test_THE_BUG_a_device_token_over_cloudflare_is_a_plain_guest(monkeypatch, value):
    """Default (unset) and anything but the exact escape-hatch word: a typo never re-opens the house to the internet."""
    if value is not None:
        monkeypatch.setenv("ZOE_PANEL_TOKEN_VIA_CF", value)
    assert panel_principal.device_token_via_cf_is_panel() is False
    assert await panel_principal.is_panel_guest(_req(CF), TOKEN_USER) is False
    assert await panel_principal.is_panel_guest(_req(CF), TOKEN_USER, mode="registered") is False
    assert await panel_principal.may_control_home(_req(CF), TOKEN_USER) is False
    assert panel_principal.device_token_is_panel(_req(CF)) is False


@pytest.mark.parametrize("header", ["cf-ray", "cf-connecting-ip", "cf-visitor"])
async def test_any_one_cloudflare_edge_header_is_enough(header):
    assert await panel_principal.may_control_home(_req({header: "x"}), TOKEN_USER) is False


async def test_a_refused_turn_is_recorded_so_every_ha_executor_refuses():
    assert await panel_principal.record_turn_home_control(_req(CF), TOKEN_USER) is False
    assert panel_principal.home_control_refused("test") is True


async def test_the_lan_is_unchanged():
    """Negative control for the fix: the SAME token without a Cloudflare header is still the panel (kiosk, voice daemon)."""
    for peer in ("192.168.1.61", "172.18.0.5", "127.0.0.1"):
        assert await panel_principal.is_panel_guest(_req(peer=peer), TOKEN_USER) is True
        assert await panel_principal.may_control_home(_req(peer=peer), TOKEN_USER) is True
    assert panel_principal.device_token_is_panel(_req()) is True


async def test_a_member_over_cloudflare_is_still_a_member():
    member = {"user_id": "casey", "role": "member"}
    assert await panel_principal.may_control_home(_req(CF), member) is True


async def test_an_anonymous_caller_over_cloudflare_was_and_is_never_the_panel():
    assert await panel_principal.is_panel_guest(_req(CF), {"user_id": "guest", "role": "guest"}) is False


# ── the escape hatch (explicit, operator only) and the LAN rollback ───────────────────────────────────────────────────────

@pytest.mark.parametrize("value", ["panel", "PANEL", " panel "])
async def test_the_escape_hatch_restores_the_old_rule(monkeypatch, value):
    monkeypatch.setenv("ZOE_PANEL_TOKEN_VIA_CF", value)
    assert panel_principal.device_token_via_cf_is_panel() is True
    assert await panel_principal.is_panel_guest(_req(CF), TOKEN_USER) is True
    assert await panel_principal.may_control_home(_req(CF), TOKEN_USER) is True


async def test_the_open_rollback_is_checked_first_as_before(monkeypatch):
    """``ZOE_HA_CONTROL_SCOPE=open`` (any caller, the operator's LAN rollback) keeps its meaning."""
    monkeypatch.setenv("ZOE_HA_CONTROL_SCOPE", "open")
    assert await panel_principal.may_control_home(_req(CF), TOKEN_USER) is True


# ── the voice lanes take the same rule ─────────────────────────────────────────────────────────────────────────────────────

def test_a_panel_computed_speaker_claim_over_cloudflare_is_ignored(monkeypatch):
    """The score is the panel's word; over the tunnel the 'panel' is a copied token anywhere, so the claim never opens a
    member's turn or voice session (a PIN instead), exactly as over the relay. Negative control: the same claim on the LAN."""
    import routers.voice_tts as voice_tts

    scored = []          # _claim_model runs only AFTER the relay/tunnel refusal: it marks "the claim reached the score checks"
    monkeypatch.setattr(voice_tts, "_claim_fields", lambda payload: ("jason", 0.99))
    monkeypatch.setattr(voice_tts, "_claim_model", lambda payload: scored.append(1) or ("no-such-model", 3.0))
    lan = {"source": "device", "panel_id": "kitchen-panel", "via_relay": False, "via_tunnel": False}
    assert voice_tts._accept_panel_voice_claim({"voice_user_id": "jason"}, dict(lan, via_tunnel=True)) is None
    assert voice_tts._accept_panel_voice_claim({"voice_user_id": "jason"}, dict(lan, via_relay=True)) is None
    assert scored == []
    voice_tts._accept_panel_voice_claim({"voice_user_id": "jason"}, dict(lan))   # LAN: reaches the score checks
    assert scored == [1]


async def test_voice_adaptation_over_cloudflare_is_refused():
    from fastapi import HTTPException

    import routers.voice_speaker_adapt as adapt

    with pytest.raises(HTTPException) as exc:
        await adapt.voice_adapt({"voice_user_id": "jason"}, caller={"source": "device", "panel_id": "p", "via_tunnel": True})
    assert exc.value.status_code == 403


async def test_the_voice_auth_marks_a_panel_turn_only_when_the_token_counts_as_the_panel(monkeypatch):
    """``_require_voice_auth`` gates ``mark_panel_turn`` (household lists on a voice turn) on the same rule, and its home-control
    verdict goes through ``is_panel_guest``."""
    import routers.voice_tts as voice_tts

    marked, verdicts = [], []
    monkeypatch.setattr(panel_principal, "mark_panel_turn", lambda: marked.append(True))
    real_record = panel_principal.record_turn_home_control

    async def _record(request, user, db=None):
        verdicts.append(await panel_principal.may_control_home(request, user, db))
        return verdicts[-1]

    monkeypatch.setattr(panel_principal, "record_turn_home_control", _record)
    device = {"panel_id": "kitchen-panel", "user_id": "voice-daemon", "raw_token": "t"}
    guest = {"user_id": "guest", "role": "guest"}
    caller = await voice_tts._require_voice_auth(_req(CF), device=device, user=guest)
    assert caller["via_tunnel"] is True and marked == [] and verdicts == [False]       # over Cloudflare: a guest
    caller = await voice_tts._require_voice_auth(_req(), device=device, user=guest)
    assert caller["via_tunnel"] is False and marked == [True] and verdicts[-1] is True  # the LAN: the panel
    monkeypatch.setenv("ZOE_PANEL_TOKEN_VIA_CF", "panel")
    marked.clear()
    await voice_tts._require_voice_auth(_req(CF), device=device, user=guest)
    assert marked == [True] and verdicts[-1] is True                                     # escape hatch
    assert real_record is not None
