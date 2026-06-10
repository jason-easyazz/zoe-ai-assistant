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
        "/js/auth.js",
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
    assert "id=\"skyOrbButton\"" in html
    assert "id=\"skyVoiceHint\"" in html
    assert "Touch the orb to speak" in html
    assert "aria-live=\"polite\"" in html


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

    assert "cardActions" in renderer
    assert "Open page" in renderer
    assert "Show related settings" in renderer
    assert "Object.assign({ status: risk }, props, { actions: settingActions })" in renderer
    assert "function safeClassTokens" in renderer
    assert "/^[a-z0-9-]+$/i.test(token)" in renderer
    assert "function rendererAccepts" in renderer
    assert "function renderActionForm" in renderer
    assert "unsupported_contract" in renderer


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
    assert "toggleVoiceCapture" in app
    assert "getHomeCards()" in app
    assert "/api/skybridge/resolve" in app
    assert "resolveCommand(query)" in app
    assert "isDataQuery(query)" in app
    assert "resp.status === 401 || resp.status === 503" in app
    assert "if (voice) voice.sendText(query)" in app
    assert "event.role === 'user') projectCards" not in app
    assert "projectCommand(query);\n        if (voice) voice.sendText(query);" not in app


def test_skybridge_auth_and_voice_bus_contracts_are_wired():
    auth = (ROOT / "zoe-ui" / "dist" / "js" / "auth.js").read_text(encoding="utf-8")
    main = read(DATA / "main.py")

    assert "'/touch/skybridge.html': 'skybridge'" in auth
    assert "pageId === 'skybridge'" in auth
    assert 'return "voice-guest"' in main
    assert 'return "family-admin"' not in main[main.index("async def _resolve_ws_user"):main.index("@app.websocket(\"/ws/voice/\")")]
    assert "async def _resolve_voice_cards" in main
    assert "resolve_skybridge_request(message_text, user_id)" in main
    assert '"type": "card", "card": card_contract' in main


def test_skybridge_renderer_supports_real_data_cards():
    renderer = read(UI / "js" / "skybridge-renderer.js")
    html = read(UI / "skybridge.html")

    assert "renderCalendar(props)" in renderer
    assert "renderWeather(props)" in renderer
    assert "sky-premium-card" in renderer
    assert "formatForecastLabel" in renderer
    assert "forecastTempBand(item)" in renderer
    assert "sky-weather-tile-temp" in renderer
    assert "sky-weather-temp-band" in renderer
    assert "Current location" in renderer
    assert "Geraldton" not in renderer
    assert "metres per second" in renderer
    assert "props.source === 'calendar_show'" in renderer
    assert "props.source === 'weather_current'" in renderer
    assert "props.source === 'weather_forecast'" in renderer
    assert "sky-event-row" in html
    assert "sky-weather-forecast-tile" in html
    assert "sky-weather-scene" in html
    assert "weather-sunny" in html
    assert "skybridge-runtime-overrides" in html
    assert "skybridge-premium-card-system" in html
    assert "skybridge-forecast-widget-overrides" in html
    assert "skybridge-premium-cards-3" in html
    assert "backdrop-filter: none !important" in html
    assert "No events " in renderer


def test_skybridge_is_registered_in_touch_menu():
    menu = read(UI / "js" / "touch-menu.js")

    assert "{ id: 'skybridge'" in menu
    assert "/touch/skybridge.html" in menu
    assert "'skybridge.html'" in menu
    assert "'dashboard', 'skybridge', 'calendar'" not in menu



def test_service_worker_does_not_cache_skybridge_runtime():
    sw = read(ROOT / "zoe-ui" / "dist" / "sw.js")

    assert "Skybridge is a live voice/data surface" in sw
    assert "4.63.12" in sw
    assert "url.pathname === '/touch/skybridge.html'" in sw
    assert "url.pathname === '/js/auth.js'" in sw
    assert "url.pathname.startsWith('/touch/js/skybridge')" in sw
    assert "new workbox.strategies.NetworkOnly()" in sw



def test_skybridge_has_typed_fallback_for_insecure_voice_contexts():
    html = read(UI / "skybridge.html")
    app = read(UI / "js" / "skybridge.js")

    assert "sky-command-open" in html
    assert "skybridge-premium-pill-overrides" in html
    assert "sky-voice-fallback.sky-empty .sky-command" in html
    assert "canUseMicrophone()" in app
    assert "openCommandFallback(" in app
    assert "if (!commandFallbackOpen)" in app
    assert "syncVoiceFallbackState();" in app
    assert "Microphone needs HTTPS here" in app
    assert "resp.status === 401 || resp.status === 503" in app
