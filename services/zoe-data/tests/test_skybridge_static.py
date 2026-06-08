"""Static contract checks for the Skybridge touch surface."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "zoe-ui" / "dist" / "touch"


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


def test_skybridge_uses_backend_status_contract():
    app = read(UI / "js" / "skybridge.js")

    assert "/api/skybridge/status" in app
    assert "Skybridge runtime ready" in app
    assert "route && route.startsWith('/')" in app
    assert "Unsupported card route" in app


def test_skybridge_is_registered_in_touch_menu():
    menu = read(UI / "js" / "touch-menu.js")

    assert "{ id: 'skybridge'" in menu
    assert "/touch/skybridge.html" in menu
    assert "'skybridge.html'" in menu
    assert "'dashboard', 'skybridge', 'calendar'" not in menu
