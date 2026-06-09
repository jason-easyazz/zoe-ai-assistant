"""Static contract checks for the Skybridge touch surface."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "zoe-ui" / "dist" / "touch"
DATA = ROOT / "zoe-data"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_skybridge_page_loads_required_modules_in_order():
    html = read(UI / "skybridge.html")
    expected = [
        "/touch/js/skybridge-capabilities.js",
        "/touch/js/skybridge-renderer.js",
        "/touch/js/skybridge-voice.js",
        "/touch/js/skybridge.js",
    ]

    positions = [html.index(src) for src in expected]

    assert positions == sorted(positions)
    assert "/js/touch-ui-executor.js" in html
    assert "id=\"skyCards\"" in html
    assert "id=\"skyCommandForm\"" in html
    assert "window.confirm = function() { return true; }" not in html


def test_skybridge_uses_login_orb_to_voice_pill_layout():
    html = read(UI / "skybridge.html")

    assert "Login-screen composition reset" in html
    assert "<h1>Zoe</h1>" in html
    assert "Skybridge is listening." in html
    assert "#skyOrb" in html
    assert "display: none !important" in html
    assert ".sky-orb-panel::before" in html
    assert "sky-login-breathe" in html
    assert "body:not(.sky-empty) .sky-orb-panel::after" in html
    assert "body:not(.sky-empty) .sky-listening-copy" in html
    assert "body.sky-empty .sky-command" in html
    assert "pointer-events: none" in html
    assert "body.sky-empty .sky-command:focus-within" in html
    assert "body.sky-empty .sky-command:hover" in html
    assert "body:not(.sky-empty) .sky-command:hover" in html


def test_skybridge_exposes_voice_transport_without_dashboard_header():
    html = read(UI / "skybridge.html")

    assert "sky-transport-toggle" in html
    assert "data-mode=\"local\"" in html
    assert "data-mode=\"livekit\"" in html
    assert ".sky-stage-header" in html
    assert "display: none" in html


def test_skybridge_capability_registry_covers_core_touch_pages():
    registry = read(UI / "js" / "skybridge-capabilities.js")

    for page_id in [
        "dashboard",
        "chat",
        "calendar",
        "lists",
        "notes",
        "journal",
        "memories",
        "people",
        "smart-home",
        "music",
        "weather",
        "settings",
    ]:
        assert f"page('{page_id}'" in registry


def test_skybridge_settings_manifest_marks_sensitive_sections():
    registry = read(UI / "js" / "skybridge-capabilities.js")

    assert "setting('security'" in registry
    assert "setting('api'" in registry
    assert "setting('trust-gate'" in registry
    assert "setting('self-creation'" in registry
    assert "'critical'" in registry
    assert "confirm_change" in registry


def test_skybridge_voice_normalizes_both_transports():
    voice = read(UI / "js" / "skybridge-voice.js")

    assert "class SkybridgeVoice" in voice
    assert "connectLocal()" in voice
    assert "connectLiveKit()" in voice
    assert "handleServerEvent(msg)" in voice
    assert "this.emit({ type: 'card'" in voice
    assert "scheduleReconnect()" in voice
    assert "Malformed server event" in voice
    assert "Skybridge LiveKit event parse failed" in voice
    assert "Voice transport unavailable" in voice
    assert "this.mediaRecorder.onstop = null" in voice
    assert "const ab = await blob.arrayBuffer()" in voice
    assert "this.ws.send(ab)" in voice
    assert "this.mode === 'livekit'" in voice
    assert "publishData(payload, { reliable: true })" in voice
    assert "RoomEvent.TrackSubscribed" in voice
    assert "RoomEvent.TrackUnsubscribed" in voice
    assert "Track.Kind.Audio" in voice
    assert "participant_identity" in voice
    assert "/api/voice/livekit-cancel" in voice
    assert "stopPlayback()" in voice
    assert "timeoutId = setTimeout" in voice
    assert "clearTimeout(timeoutId)" in voice


def test_livekit_voice_router_accepts_text_commands():
    router = read(DATA / "routers" / "voice_livekit.py")

    assert "async def _run_text_pipeline" in router
    assert "elif msg_type == \"text\"" in router
    assert "run_zoe_agent(message, session_id, user_id, voice_mode=True)" in router


def test_skybridge_renderer_keeps_button_actions_functional():
    renderer = read(UI / "js" / "skybridge-renderer.js")

    assert "Object.assign({}, props, { actions: cardActions })" in renderer
    assert "Object.assign({ status: risk }, props, { actions: settingActions })" in renderer
    assert "function safeClassTokens" in renderer
    assert "/^[a-z0-9-]+$/i.test(token)" in renderer


def test_skybridge_uses_backend_status_contract():
    app = read(UI / "js" / "skybridge.js")

    assert "/api/skybridge/status" in app
    assert "Skybridge runtime ready" in app
    assert "route && route.startsWith('/')" in app
    assert "Unsupported card route" in app
    assert "skybridge_voice_mode" in app
    assert "localStorage.setItem('skybridge_voice_mode', mode)" in app
    assert "currentUtterance" in app
    assert "Heard: " in app
    assert "voice.cancel()" in app
    assert "Notice: " in app


def test_skybridge_is_registered_in_touch_menu():
    menu = read(UI / "js" / "touch-menu.js")

    assert "{ id: 'skybridge'" in menu
    assert "/touch/skybridge.html" in menu
    assert "'skybridge.html'" in menu
    assert "'dashboard', 'skybridge', 'calendar'" not in menu
