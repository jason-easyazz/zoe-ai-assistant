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


def test_skybridge_push_socket_uses_authenticated_session_query():
    html = read(UI / "skybridge.html")
    auth = (ROOT / "zoe-ui" / "dist" / "js" / "auth.js").read_text(encoding="utf-8")
    executor = (ROOT / "zoe-ui" / "dist" / "js" / "touch-ui-executor.js").read_text(encoding="utf-8")
    sync = (ROOT / "zoe-ui" / "dist" / "js" / "websocket-sync.js").read_text(encoding="utf-8")
    main = (ROOT / "zoe-data" / "main.py").read_text(encoding="utf-8")

    assert "/js/websocket-sync.js?v=skybridge-auth-ready-1" in html
    assert "/touch/js/skybridge.js?v=skybridge-auth-card-1" in html
    assert "/touch/js/skybridge-renderer.js?v=skybridge-auth-card-1" in html
    assert "/js/touch-ui-executor.js?v=skybridge-auth-card-1" in html
    assert "initPush(panelId, sessionId)" in sync
    assert "params.set('panel_id', panelId)" in sync
    assert "else params.set('channel', 'all')" in sync
    assert "params.set('session_id', sessionId)" in sync
    assert "initPush called without sessionId; push will be rejected" in sync
    assert "const pushUrl = `${protocol}//${window.location.host}/ws/push?${params.toString()}`;" in sync
    assert "window.zoePushWs = this.push;" in sync
    assert "originalConnect" not in sync
    assert "/ws/push?channel=all`" not in sync
    assert "async def _session_can_subscribe_panel" in main
    assert "FROM ui_panel_sessions WHERE panel_id = ?" in main
    assert "not device_info and not await _session_can_subscribe_panel(panel_id, session_id)" in main
    assert "Touch guest session was rejected; refreshing guest session" in auth
    assert "profile.status === 401 || profile.status === 403 || profile.status === 404" in auth
    assert "keeping existing session" in auth
    assert "window.zoeAuthReady = new Promise" in auth
    assert "await window.zoeAuthReady" in executor


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
    assert "this.emit({ type: 'cards'" in voice
    assert "this.emit({ type: 'skybridge_context'" in voice
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
    assert "skybridgeContext" in app
    assert "JSON.stringify({ message: query, context: skybridgeContext })" in app
    assert "renderSkybridgeResult(data)" in app
    assert "function renderSkybridgeResult" in app
    assert "function retireRenderedVoiceCards" in app
    assert "/api/ui/actions/pending?panel_id=" in app
    assert "action.payload.source === 'voice:skybridge'" in app
    assert "Promise.allSettled(actions" in app
    assert "skybridge-direct-render" in app
    assert "Voice is still connecting. Type here and Zoe will still render cards." in app
    assert "Microphone is not available here. Type a request and Zoe will still render cards." in app
    assert "Type here while voice reconnects..." in app
    assert "Voice reconnecting" in app
    assert "Voice needs attention" not in app
    assert "contextLabelFor(intent)" in app
    assert "isDataQuery(query)" in app
    assert "resp.status === 401 || resp.status === 503" in app
    assert "const panelId = new URLSearchParams(location.search).get('panel_id')" in app
    assert app.count("if (!route) route = '/touch/index.html' + (panelId ? '?panel_id=' + encodeURIComponent(panelId) : '');") >= 2
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
    assert "resolve_skybridge_request(message_text, user_id, context=context)" in main
    assert "skybridge_context: dict = {}" in main
    assert '"type": "cards", "result": skybridge_result' in main
    assert '"type": "skybridge_context", "context": skybridge_context' in main
    assert '"type": "transcript", "role": "assistant", "text": spoken_summary' in main
    assert 'skybridge_context = {}\n\n            # ── Streaming LLM' in main


def test_skybridge_touch_executor_card_shell_spans_panel_grid():
    html = read(UI / "skybridge.html")

    assert ".sky-card-shell {" in html
    assert "grid-column: span 12 !important;" in html
    assert ".sky-card-shell > .sky-card" in html
    assert "body:not(.sky-empty) .sky-card-shell" in html


def test_skybridge_renderer_supports_real_data_cards():
    renderer = read(UI / "js" / "skybridge-renderer.js")
    html = read(UI / "skybridge.html")
    data_widgets_css = read(UI / "css" / "skybridge-data-widgets.css")

    assert "renderCalendar(props)" in renderer
    assert "renderWeather(props)" in renderer
    assert "sky-premium-card" in renderer
    assert "formatCalendarDate" in renderer
    assert "calendarEventSortKey" in renderer
    assert "calendarCategoryClass" in renderer
    assert "hideHeader: true" in renderer
    assert "hideStatus: true" in renderer
    assert "sky-calendar-scene" in renderer
    assert "sky-calendar-event-main" in renderer
    assert "sky-calendar-category" not in renderer
    assert "formatForecastLabel" in renderer
    assert "forecastTempBand(item)" in renderer
    assert "formatHourLabel" in renderer
    assert "const dayList = dailyRows;" in renderer
    assert "fallbackTiles" not in renderer
    assert "sky-weather-hour-strip" in renderer
    assert "sky-weather-hour-tile" in renderer
    assert "sky-weather-day-list" in renderer
    assert "sky-weather-day-row" in renderer
    assert "sky-weather-day-main" in renderer
    assert "sky-weather-temp-band" in renderer
    assert "Current location" in renderer
    assert "Geraldton" not in renderer
    assert "metres per second" in renderer
    assert "props.source === 'calendar_show'" in renderer
    assert "props.source === 'weather_current'" in renderer
    assert "props.source === 'weather_forecast'" in renderer
    assert "props.source === 'list_show'" in renderer
    assert "props.source === 'people_directory'" in renderer
    assert "props.source === 'person_profile'" in renderer
    assert "renderZoeList(props)" in renderer
    assert "renderPeopleDirectory(props)" in renderer
    assert "renderPersonProfile(props)" in renderer
    assert "sky-list-scene" in renderer
    assert "sky-people-scene" in renderer
    assert "sky-profile-scene" in renderer
    assert "sky-event-row" in html
    assert "sky-weather-hour-tile" in html
    assert "sky-weather-day-row" in html
    assert "/touch/css/skybridge-data-widgets.css?v=skybridge-auth-card-1" in html
    assert "skybridge-lists-people-widgets" not in html
    assert "sky-list-item-row" in data_widgets_css
    assert "sky-person-row" in data_widgets_css
    assert "sky-profile-health" in data_widgets_css
    assert "sky-weather-scene" in html
    assert "weather-sunny" in html
    assert "skybridge-runtime-overrides" in html
    assert "skybridge-premium-card-system" in html
    assert "skybridge-calendar-widget-overrides" in html
    assert "skybridge-forecast-widget-overrides" in html
    assert "skybridge-weather-widget-v2" in html
    assert "skybridge-push-ack-1" in html
    assert "backdrop-filter: none !important" in html
    assert "No events " in renderer


def test_skybridge_weather_renderer_uses_widget_forecast_structure():
    renderer = read(UI / "js" / "skybridge-renderer.js")
    html = read(UI / "skybridge.html")
    forecast_label_helper = renderer[
        renderer.index("function formatForecastLabel"):
        renderer.index("function formatForecastShort")
    ]

    assert "const datePart = raw.slice(0, 10);" in forecast_label_helper
    assert r"/^\d{4}-\d{2}-\d{2}$/.test(datePart)" in forecast_label_helper
    assert "new Date(datePart + 'T12:00:00')" in forecast_label_helper
    assert "new Date(raw + 'T12:00:00')" not in forecast_label_helper
    assert "const dailyRows = daily.slice(0, 5).map" in renderer
    assert "const hourlyTiles = hourly.slice(0, 8).map" in renderer
    assert "fallbackTiles" not in renderer
    assert "const dayList = dailyRows;" in renderer
    assert "current && current.description" in renderer
    assert "rain|drizzle|shower|09|10" in renderer
    assert "cloud|overcast|03|04" in renderer
    assert "replace(',', '')" in renderer
    assert "sky-weather-forecast-head" in renderer
    assert "sky-weather-forecast-grid" not in renderer
    assert "sky-weather-forecast-tile" not in renderer
    assert "grid-template-columns: repeat(8, minmax(54px, 1fr))" in html
    assert "grid-template-columns: minmax(88px, 0.32fr) minmax(0, 1fr) auto" in html
    assert "text-transform: capitalize" in html


def test_skybridge_calendar_renderer_handles_datetime_dates_and_ordering():
    renderer = read(UI / "js" / "skybridge-renderer.js")
    calendar_date_helper = renderer[
        renderer.index("function formatCalendarDate"):
        renderer.index("function calendarEventSortKey")
    ]

    assert "const datePart = raw.slice(0, 10);" in calendar_date_helper
    assert r"/^\d{4}-\d{2}-\d{2}$/.test(datePart)" in calendar_date_helper
    assert "new Date(datePart + 'T12:00:00')" in calendar_date_helper
    assert "new Date(raw + 'T12:00:00')" not in calendar_date_helper
    assert "events.slice().sort((a, b) => calendarEventSortKey(a) - calendarEventSortKey(b)).slice(0, 8)" in renderer
    assert "props.date || props.start_date || (visibleEvents[0] && visibleEvents[0].start_date)" in renderer
    assert "props.date || props.start_date || (events[0] && events[0].start_date)" not in renderer
    assert "const detail = [item.location].filter(Boolean).join(' · ');" in renderer
    assert "const detail = [item.location, calendarAccentLabel(item.category)]" not in renderer
    assert "calendarAccentLabel" not in renderer
    assert "return known.indexOf(token) >= 0 ? token : 'general';" in renderer


def test_skybridge_is_registered_in_touch_menu():
    menu = read(UI / "js" / "touch-menu.js")

    assert "{ id: 'skybridge'" in menu
    assert "/touch/skybridge.html" in menu
    assert "{ id: 'skybridge', path: '/touch/skybridge.html',  label: 'Home'" in menu
    assert "{ id: 'dashboard', path: '/touch/dashboard.html',  label: 'Home'" not in menu
    assert "q=show%20my%20calendar" not in menu
    assert "Skybridge Calendar" not in menu
    assert "Skybridge Interface" not in menu
    assert "verify=touch-menu-calendar" not in menu
    assert "item.href = p.path;" in menu
    assert "'skybridge-calendar'" not in menu
    assert "'skybridge-calendar-test'" not in menu
    assert "'dashboard', 'skybridge'," not in menu
    assert "'skybridge.html'" in menu
    assert "'dashboard', 'skybridge', 'calendar'" not in menu


def test_touch_defaults_to_skybridge_home():
    auth = (ROOT / "zoe-ui" / "dist" / "js" / "auth.js").read_text(encoding="utf-8")
    touch_index = read(UI / "index.html")
    executor = (ROOT / "zoe-ui" / "dist" / "js" / "touch-ui-executor.js").read_text(encoding="utf-8")
    menu = read(UI / "js" / "touch-menu.js")

    assert "const HOME_PATH = '/touch/skybridge.html';" in executor
    assert "let dest = '/touch/skybridge.html';" in touch_index
    assert "window.location.href = '/touch/skybridge.html';" in touch_index
    assert "window.location.href = '/touch/skybridge.html';" in auth
    assert "return '/touch/skybridge.html';" in menu


def test_touch_executor_renders_skybridge_card_contracts():
    executor = (ROOT / "zoe-ui" / "dist" / "js" / "touch-ui-executor.js").read_text(encoding="utf-8")

    assert "function renderSkybridgeCardPayload(payload)" in executor
    assert "function sanitizeSkybridgeHtml(html)" in executor
    assert "querySelectorAll('script, iframe, object, embed, link, meta, style')" in executor
    assert "name.startsWith('on')" in executor
    assert "/^(javascript|data):/.test(value)" in executor
    assert "payload && payload.card ? [payload.card]" in executor
    assert "sanitizeSkybridgeHtml(window.SkybridgeRenderer.render(card))" in executor
    assert "if (renderSkybridgeCardPayload(payload))" in executor


def test_skybridge_auth_challenge_card_contract():
    html = read(UI / "skybridge.html")
    app = read(UI / "js" / "skybridge.js")
    renderer = read(UI / "js" / "skybridge-renderer.js")
    executor = (ROOT / "zoe-ui" / "dist" / "js" / "touch-ui-executor.js").read_text(encoding="utf-8")
    css = read(UI / "css" / "skybridge-data-widgets.css")
    service = read(DATA / "skybridge_service.py")
    voice = read(DATA / "routers" / "voice_tts.py")

    assert "auth_challenge: renderAuthChallenge" in renderer
    assert "function renderAuthChallenge(props)" in renderer
    assert "data-challenge-id" in renderer
    assert "data-action-context" in renderer
    assert "btn.dataset.skyAction === 'auth'" in app
    assert "if (!route) route = '/touch/index.html'" in app
    assert "encodeURIComponent(panelId)" in app
    assert "storedChallenge.panel_id" in app
    assert "zoe_panel_auth_challenge" in app
    assert "zoe_redirect_after_login" in app
    assert "function renderSkybridgeAuthChallenge(payload)" in executor
    assert "renderSkybridgeAuthChallenge(payload)" in executor
    assert "component: 'auth_challenge'" in executor
    assert "buildTouchLoginRoute(panelId)" in executor
    assert "redirectToTouchLogin" in executor
    assert "sky-card.auth-challenge" in css
    assert "sky-auth-scene" in css
    assert '"component": "auth_challenge"' in service
    assert '"route": ""' in service
    assert '"summary": "Please authenticate on the touch panel to continue."' in voice
    assert '"challenge_id": challenge_id' in voice


def test_voice_command_has_skybridge_first_touch_path():
    voice = read(DATA / "routers" / "voice_tts.py")

    assert "async def _broadcast_skybridge_ui" in voice
    assert "resolve_skybridge_request(" in voice
    assert "intent\": f\"skybridge:" in voice
    assert "\"url\": url" in voice
    assert "\"type\": \"skybridge\"" in voice
    assert "payload={**card_payload, \"result\": skybridge_result}" in voice
    assert "Keep the card queued" in voice
    assert "poll /api/ui/actions/pending and render it" in voice
    assert "{**card_message, \"payload\": card_payload}" not in voice



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



def test_skybridge_has_touch_panel_fit_overrides():
    html = read(UI / "skybridge.html")

    assert 'id="skybridge-touch-panel-fit"' in html
    assert '@media (min-width: 900px) and (max-height: 760px)' in html
    assert 'height: min(472px, calc(100dvh - 154px))' in html
    assert 'grid-template-columns: repeat(8, minmax(46px, 1fr))' in html
    assert 'body:not(.sky-empty) .sky-command' in html
