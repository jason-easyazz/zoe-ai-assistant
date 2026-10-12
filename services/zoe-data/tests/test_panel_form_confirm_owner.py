"""A guest-recovered panel never writes a member's form entry under ``guest``.

PR #1999 review (P1). When the panel's PIN session expires the touch UI falls back to a guest
session (no ``X-Session-ID``), but the unauthenticated push socket stays up, so a voice-presented
calendar / list form still reaches the panel. Its Done then posts to ``/api/ui/panel/form/confirm``
as ``guest``, which used to execute the write under ``user["user_id"]`` -- the member's event or
item was saved under ``guest`` while the panel said it saved.

The form is now bound to the member it was presented for (server-side, in ``panel_form_state``),
and the confirm endpoint refuses a guest Done for a member's form without executing anything.
"""
from __future__ import annotations

import os
import sys
import types

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import panel_form_state  # noqa: E402
import routers.ui_actions as ui_actions  # noqa: E402

pytestmark = pytest.mark.ci_safe

PANEL = "zoe-touch-pi"
GUEST = {"user_id": "guest", "role": "guest", "username": "guest"}
MEMBER = {"user_id": "member-1", "role": "user", "username": "member"}


@pytest.fixture(autouse=True)
def _fresh_forms(monkeypatch):
    monkeypatch.setattr(panel_form_state, "_ACTIVE_FORMS", {})


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


@pytest.fixture
def writes(monkeypatch):
    """Record every execute_intent write (user_id, intent name, slots); silence the broadcaster."""
    seen: list[tuple[str, str, dict]] = []

    async def _execute_intent(intent, user_id):
        seen.append((user_id, intent.name, dict(intent.slots)))
        return "done"

    monkeypatch.setitem(sys.modules, "intent_router", types.SimpleNamespace(execute_intent=_execute_intent, detect_and_extract_intent=None))

    async def _noop(*_a, **_k):
        return 0

    monkeypatch.setattr(ui_actions.broadcaster, "broadcast", _noop)
    return seen


def _body(panel_type="shopping_list"):
    data = {"list_name": "shopping", "new_item": "milk", "items": ["milk"]} if panel_type == "shopping_list" else {"title": "Dentist", "date": "2026-10-14"}
    return {"panel_type": panel_type, "panel_id": PANEL, "data": data}


def test_owner_is_bound_and_survives_the_panels_own_form_open():
    panel_form_state.set_active_form(PANEL, "shopping_list", {"items": ["milk"]}, owner_user_id="member-1")
    # the panel registers the same open form again (POST /panel/form/open): no owner passed
    panel_form_state.set_active_form(PANEL, "shopping_list", {"items": ["milk"]})
    assert panel_form_state.get_active_form(PANEL)["owner_user_id"] == "member-1"
    # a DIFFERENT form type on the panel does not inherit the previous form's owner
    panel_form_state.set_active_form(PANEL, "calendar_event", {})
    assert panel_form_state.get_active_form(PANEL)["owner_user_id"] is None


def test_confirm_is_misattributed_matrix():
    f = panel_form_state.confirm_is_misattributed
    panel_form_state.set_active_form(PANEL, "shopping_list", {}, owner_user_id="member-1")
    assert f(PANEL, "guest") is True
    assert f(PANEL, None) is True
    assert f(PANEL, "member-1") is False
    assert f(PANEL, "member-2") is False, "guest-vs-member guard only: a signed-in member is not second-guessed"
    assert f("other-panel", "guest") is False
    panel_form_state.set_active_form(PANEL, "shopping_list", {}, owner_user_id="guest")
    assert f(PANEL, "guest") is False
    panel_form_state.set_active_form(PANEL, "shopping_list", {})
    assert f(PANEL, "guest") is False, "a form with no bound owner (opened on the panel itself) is unchanged"
    panel_form_state.clear_active_form(PANEL)
    assert f(PANEL, "guest") is False


@pytest.mark.asyncio
@pytest.mark.parametrize("panel_type", ["shopping_list", "calendar_event"])
async def test_guest_done_for_a_members_form_is_refused_and_writes_nothing(writes, panel_type):
    panel_form_state.set_active_form(PANEL, panel_type, {}, owner_user_id="member-1")
    with pytest.raises(ui_actions.HTTPException) as exc:
        await ui_actions.confirm_action_form(_Req(_body(panel_type)), user=GUEST, db=None)
    assert exc.value.status_code == 403
    assert writes == [], "nothing may be saved under guest"
    assert panel_form_state.get_active_form(PANEL) is not None, "the form stays open so the member can sign in and finish"


@pytest.mark.asyncio
async def test_the_member_still_saves_their_own_form(writes):
    panel_form_state.set_active_form(PANEL, "shopping_list", {}, owner_user_id="member-1")
    out = await ui_actions.confirm_action_form(_Req(_body("shopping_list")), user=MEMBER, db=None)
    assert out["status"] == "ok"
    assert writes == [("member-1", "list_add", {"item": "milk", "list_name": "shopping", "list_type": "shopping"})]
    assert panel_form_state.get_active_form(PANEL) is None


@pytest.mark.asyncio
async def test_a_guest_form_a_guest_confirms(writes):
    panel_form_state.set_active_form(PANEL, "shopping_list", {}, owner_user_id="guest")
    out = await ui_actions.confirm_action_form(_Req(_body("shopping_list")), user=GUEST, db=None)
    assert out["status"] == "ok" and writes[0][0] == "guest"


def test_voice_path_binds_the_effective_user_to_both_forms():
    src = open(os.path.join(os.path.dirname(__file__), "..", "routers", "voice_tts.py")).read()
    assert src.count("owner_user_id=effective_user,") == 2
    assert "set_active_form(panel_id, panel_type, data, owner_user_id=owner_user_id)" in src
