"""``POST /api/enrollment/screen`` - the enrolment prompt on the panel's talk screen ("Zoe, learn my voice").

The person only HEARS each phrase, which is hard to remember, so the panel daemon also has the panel SHOW it. Pinned here, against the REAL
router and the REAL ``PushBroadcaster`` (fake sockets only):

  * only a DEVICE TOKEN may drive it (a signed-in session, even an admin, is refused);
  * the frame goes to the panel the token belongs to and to NO other panel - and not to the shared ``all`` channel every panel is also on
    (a body ``panel_id`` is ignored: a daemon cannot put words on someone else's wall);
  * nothing about the member is in the frame (the daemon never sends it, and a stray ``user_id`` / ``name`` in the body is not forwarded);
  * the state is validated, text is bounded and stripped of control characters, counters are clamped;
  * a panel with no live socket is not an error (the spoken prompt is the contract), and nothing is queued or stored.
"""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.ci_safe

PANEL = "zoe-touch-pi"
OTHER = "zoe-touch-kitchen"


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def accept(self):
        return None

    async def send_json(self, message):
        self.sent.append(message)

    def frames(self):
        return [m for m in self.sent if m.get("type") == "voice_enroll"]


@pytest.fixture
def rig(monkeypatch):
    import push
    import routers.voice_tts as voice_tts
    from routers import enrollment

    b = push.PushBroadcaster()
    monkeypatch.setattr(push, "broadcaster", b)
    sockets = {PANEL: FakeSocket(), OTHER: FakeSocket()}
    anon = FakeSocket()
    loop = asyncio.new_event_loop()
    try:
        for pid, ws in sockets.items():
            loop.run_until_complete(b.connect_panel(ws, pid))
        loop.run_until_complete(b.connect(anon, "all"))
    finally:
        loop.close()

    app = FastAPI()
    app.include_router(enrollment.router)
    who = {"caller": {"source": "device", "user_id": "voice-daemon", "panel_id": PANEL}}
    app.dependency_overrides[voice_tts._require_voice_auth] = lambda: who["caller"]
    client = TestClient(app)
    client.who, client.sockets, client.anon = who, sockets, anon
    return client


def test_the_frame_goes_to_the_calling_panel_only(rig):
    r = rig.post("/api/enrollment/screen", json={"state": "phrase", "phrase": "Add milk, bread, and coffee.", "index": 2, "total": 5, "line": "Say:"})
    assert r.status_code == 200 and r.json() == {"ok": True, "delivered": 1}
    mine = rig.sockets[PANEL].frames()
    assert len(mine) == 1
    assert mine[0]["data"] == {"panel_id": PANEL, "state": "phrase", "line": "Say:", "phrase": "Add milk, bread, and coffee.", "index": 2, "total": 5}
    # NEGATIVE CONTROLS: the other panel, and the shared channel every panel (and any anonymous viewer) is subscribed to, hear nothing
    assert rig.sockets[OTHER].frames() == [] and rig.anon.frames() == []


def test_a_panel_id_in_the_body_cannot_aim_the_frame_at_another_wall(rig):
    rig.post("/api/enrollment/screen", json={"state": "phrase", "phrase": "x", "index": 1, "total": 5, "panel_id": OTHER})
    assert rig.sockets[OTHER].frames() == [] and len(rig.sockets[PANEL].frames()) == 1
    assert rig.sockets[PANEL].frames()[0]["data"]["panel_id"] == PANEL


def test_only_a_device_token_may_drive_the_screen(rig):
    # (a panel_id is present on these so the SOURCE check, not the missing-panel check, is what refuses them)
    for who in ({"source": "session", "user_id": "ann", "role": "user", "panel_id": PANEL},
                {"source": "session", "user_id": "admin", "role": "admin", "panel_id": PANEL}):
        rig.who["caller"] = who
        assert rig.post("/api/enrollment/screen", json={"state": "phrase", "phrase": "x"}).status_code == 403
    rig.who["caller"] = {"source": "device", "user_id": "voice-daemon", "panel_id": ""}
    assert rig.post("/api/enrollment/screen", json={"state": "phrase", "phrase": "x"}).status_code == 403     # a token bound to no panel
    assert all(ws.frames() == [] for ws in (*rig.sockets.values(), rig.anon))


def test_no_credentials_at_all_is_refused_by_the_real_dependency():
    from routers import enrollment

    app = FastAPI()
    app.include_router(enrollment.router)
    r = TestClient(app).post("/api/enrollment/screen", json={"state": "phrase", "phrase": "x"})
    assert r.status_code in (401, 403)


def test_nothing_about_the_member_is_forwarded(rig):
    rig.post("/api/enrollment/screen", json={"state": "phrase", "phrase": "Remind me.", "index": 1, "total": 5, "line": "Say:",
                                              "user_id": "ann", "name": "Ann", "display_name": "Ann", "audio_base64": "AAAA"})
    data = rig.sockets[PANEL].frames()[0]["data"]
    assert set(data) == {"panel_id", "state", "line", "phrase", "index", "total"}
    assert "ann" not in str(data).lower()


def test_clear_carries_no_text_at_all(rig):
    rig.post("/api/enrollment/screen", json={"state": "clear", "phrase": "leftover", "line": "leftover", "index": 3, "total": 5})
    assert rig.sockets[PANEL].frames()[0]["data"] == {"panel_id": PANEL, "state": "clear"}


def test_closing_states_carry_a_line_but_no_phrase_or_counter(rig):
    rig.post("/api/enrollment/screen", json={"state": "done", "line": "Done", "phrase": "leftover", "index": 3, "total": 5})
    assert rig.sockets[PANEL].frames()[0]["data"] == {"panel_id": PANEL, "state": "done", "line": "Done"}


def test_the_state_is_validated(rig):
    for bad in ("", "weird", None, 7, "ui_action"):
        assert rig.post("/api/enrollment/screen", json={"state": bad, "phrase": "x"}).status_code == 400, bad
    assert rig.post("/api/enrollment/screen", json={"state": "weird"}).status_code == 400
    assert rig.post("/api/enrollment/screen", json={}).status_code == 400


def test_text_is_bounded_and_stripped_and_counters_are_clamped(rig):
    rig.post("/api/enrollment/screen", json={"state": "phrase", "phrase": "a\x00b\nc" + "z" * 1000, "line": "L" * 1000, "index": 99, "total": -4})
    data = rig.sockets[PANEL].frames()[0]["data"]
    assert len(data["phrase"]) <= 240 and len(data["line"]) <= 160
    assert not any(ord(ch) < 32 for ch in data["phrase"]) and data["phrase"].startswith("a b c")
    assert data["index"] == 12 and data["total"] == 0
    rig.post("/api/enrollment/screen", json={"state": "recording", "phrase": "p", "index": "two", "total": None})
    assert rig.sockets[PANEL].frames()[1]["data"]["index"] == 0


def test_a_panel_with_no_live_socket_is_not_an_error_and_nothing_leaks_elsewhere(rig):
    rig.who["caller"] = {"source": "device", "user_id": "voice-daemon", "panel_id": "zoe-touch-offline"}
    r = rig.post("/api/enrollment/screen", json={"state": "phrase", "phrase": "x", "index": 1, "total": 5})
    assert r.status_code == 200 and r.json() == {"ok": True, "delivered": 0}
    assert all(ws.frames() == [] for ws in (*rig.sockets.values(), rig.anon))        # no fallback to the "all" channel
