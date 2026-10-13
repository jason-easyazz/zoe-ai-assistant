"""The voice WebSocket's "did Zoe just make an offer?" memory follows EVERY spoken reply (Codex #2001 P2).

``websocket_voice`` keeps ``_ws_last_reply`` so a bare "sounds good" can be told apart from an acceptance: after an offer it goes to the
brain, otherwise it gets the instant canned ack. The first version updated it only on the instant path and after a brain turn, so a
handled-Skybridge reply (and the conversation-opener ack) left an OLDER offer standing and "sounds good" accepted it.

NOT in the ci_safe lane: it imports the whole app (``main``). The scripted socket drives the REAL ``websocket_voice``; only the
router verdicts, the brain, the card resolver and TTS are stubbed.
"""
import pytest

main = pytest.importorskip("main")

OFFER = "Coffee beans are really the seeds of a cherry. How about the history of coffee?"


class ScriptedSocket:
    """Feeds ``websocket_voice`` a script of text messages, then a disconnect, and records what it sent."""

    def __init__(self, texts):
        self._script = [{"text": __import__("json").dumps({"type": "text", "message": t})} for t in texts]
        self.sent = []
        self.headers = {}

    async def accept(self):
        return None

    async def receive(self):
        return self._script.pop(0) if self._script else {"type": "websocket.disconnect"}

    async def send_json(self, payload):
        self.sent.append(payload)


def _zoe_lines(sock):
    return [m["text"] for m in sock.sent if m.get("type") == "transcript" and m.get("role") in ("zoe", "assistant")]


@pytest.fixture
def wired(monkeypatch):
    brain_calls = []

    async def brain_streaming(message, session_id, user_id=None, voice_mode=False, **_kw):
        brain_calls.append(message)
        yield OFFER if message.startswith("tell me") else "Lovely."

    async def tts(_text):
        return b"RIFFfake"

    async def cards(message, user_id, context=None):
        if message == "what is on my list":
            return {"handled": True, "spoken_summary": "You have milk on your list.", "cards": [], "skybridge_context": {}}
        return {"handled": False, "cards": [], "spoken_summary": ""}

    async def no_frame(*_a, **_k):
        return None

    async def allow(_ws):
        return True

    async def user(_sid):
        return "demo_user"

    import brain_dispatch
    from routers import voice_tts

    monkeypatch.setattr(brain_dispatch, "brain_streaming", brain_streaming)
    monkeypatch.setattr(voice_tts, "_synthesize_kokoro_sidecar", tts)
    monkeypatch.setattr(main, "_resolve_voice_cards", cards)
    monkeypatch.setattr(main, "_voice_compose_cards_frame", no_frame)
    monkeypatch.setattr(main, "_enforce_ws_origin", allow)
    monkeypatch.setattr(main, "_resolve_ws_user", user)
    return brain_calls


async def _run(texts):
    sock = ScriptedSocket(texts)
    await main.websocket_voice(sock, session_id="ws-test")
    return sock


async def test_an_ack_right_after_the_offer_is_an_acceptance_and_reaches_the_brain(wired):
    await _run(["tell me about coffee", "sounds good"])
    assert wired == ["tell me about coffee", "sounds good"]


async def test_an_ack_after_an_intervening_card_reply_is_not_an_acceptance_of_the_old_offer(wired):
    sock = await _run(["tell me about coffee", "what is on my list", "sounds good"])
    assert wired == ["tell me about coffee"], wired                         # the stale offer was NOT accepted
    assert _zoe_lines(sock)[-1] in {"Got it.", "Sure thing.", "Okay.", "No problem."}     # the instant canned ack


async def test_an_ack_with_no_offer_on_the_socket_is_the_instant_ack(wired):
    sock = await _run(["sounds good"])
    assert wired == [] and _zoe_lines(sock)[-1] in {"Got it.", "Sure thing.", "Okay.", "No problem."}
