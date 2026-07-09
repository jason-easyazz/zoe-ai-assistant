"""Static contract checks for the Skybridge touch surface."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "zoe-ui" / "dist" / "touch"
DATA = ROOT / "zoe-data"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_skybridge_runtime_scripts_parse_as_javascript():
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    for script in [
        UI / "js" / "skybridge-renderer.js",
        UI / "js" / "skybridge.js",
        UI / "js" / "skybridge-voice.js",
        UI / "js" / "skybridge-capabilities.js",
        ROOT / "zoe-ui" / "dist" / "js" / "touch-ui-executor.js",
    ]:
        subprocess.run([node, "--check", str(script)], check=True, capture_output=True, text=True)


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

    assert "/js/websocket-sync.js?v=skybridge-panel-session-2" in html
    assert "/touch/js/skybridge.js?v=skybridge-panel-session-2" in html
    assert "/touch/js/skybridge-renderer.js?v=skybridge-panel-session-2" in html
    assert "/js/touch-ui-executor.js?v=skybridge-panel-session-2" in html
    assert "initPush(panelId, sessionId)" in sync
    assert "params.set('panel_id', panelId)" in sync
    assert "else params.set('channel', 'all')" in sync
    assert "params.set('session_id', sessionId)" in sync
    assert "initPush called without sessionId; push will be rejected" in sync
    assert "const pushUrl = `${protocol}//${window.location.host}/ws/push?${params.toString()}`;" in sync
    assert "window.zoePushWs = this.push;" in sync
    assert "originalConnect" not in sync
    assert "/ws/push?channel=all`" not in sync
    assert "async def _resolve_subscribable_panel" in main
    # Canonical resolution joins ui_panel_sessions to the registered `panels` table.
    assert "JOIN panels p ON p.panel_id = s.panel_id" in main
    assert "subscribe_panel_id = await _resolve_subscribable_panel(panel_id, session_id)" in main
    assert "await broadcaster.connect_panel(websocket, subscribe_panel_id)" in main
    assert "Touch guest session was rejected; refreshing guest session" in auth
    assert "profile.status === 401 || profile.status === 403 || profile.status === 404" in auth
    assert "keeping existing session" in auth
    assert "window.zoeAuthReady = new Promise" in auth
    assert "await window.zoeAuthReady" in executor



def test_touch_kiosk_guest_sessions_are_not_sent_to_data_api():
    auth = (ROOT / "zoe-ui" / "dist" / "js" / "auth.js").read_text(encoding="utf-8")
    executor = (ROOT / "zoe-ui" / "dist" / "js" / "touch-ui-executor.js").read_text(encoding="utf-8")

    assert "function shouldAttachSessionToUrl" in auth
    assert "pathname.startsWith('/api/auth/')" in auth
    assert "return !isGuestSessionObject(session);" in auth
    assert "pathname.startsWith('/api/ui/')" in auth
    assert "function getDataApiSession" in executor
    assert "if (isGuestSession(session)) return null;" in executor
    assert executor.count("const session = getDataApiSession();") >= 2

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
    assert "brain_oneshot(message, session_id, user_id, voice_mode=True)" in router


def test_skybridge_renderer_keeps_button_actions_functional():
    renderer = read(UI / "js" / "skybridge-renderer.js")

    assert "cardActions" in renderer
    assert "Open page" in renderer
    assert "Show related settings" in renderer
    # Setting card keeps its two-action contract (open route + risk-gated change);
    # the change action goes `warn` when the setting is critical.
    assert "settingActions" in renderer
    assert "Open settings" in renderer
    assert "Change setting" in renderer
    assert "Prepare change" in renderer
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
    # Home (awake) is the ambient dashboard surface, not the old glance-card loop.
    assert "wakeToDashboard()" in app
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
    assert "function prepareAuthRoute" in app
    assert "function buildLoginRoute(panelId, selectedUserId)" in app
    assert "params.set('user_id', selectedUserId)" in app
    assert "params.set('auth', 'skybridge')" in app
    assert "return route || buildLoginRoute(panelId, selectedUserId)" in app
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
    assert 'skybridge_context = {}\n\n            # ── Instant intent fast-path' in main
    assert "# ── Streaming LLM + per-sentence TTS" in main


def test_skybridge_touch_executor_card_shell_spans_panel_grid():
    html = read(UI / "skybridge.html")

    assert ".sky-card-shell {" in html
    assert "grid-column: span 12 !important;" in html
    assert ".sky-card-shell > .sky-card" in html
    assert "body:not(.sky-empty) .sky-card-shell" in html



def test_skybridge_renderer_supports_real_data_cards(tmp_path):
    """Intent: every live data source renders through the REAL renderer to its
    dedicated card scene. Behavioral (node-executed) on purpose — internal
    helper names/markup are free to change; the scene contract is not."""
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = tmp_path / "cards_harness.cjs"
    harness.write_text(
        """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const sandbox = { window: {} };
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const R = sandbox.window.SkybridgeRenderer;
const mk = (source, extra) => ({ card_type: 'generic', schema_version: '1.0.0', card_id: 'c', content: Object.assign({ source }, extra) });
const expect = {
  list_show: ['lst-scene', 'zoe-list-card'],
  calendar_show: ['cal-scene', 'calendar-card'],
  weather_current: ['wx-card', 'wx-hero'],
  weather_forecast: ['wx-card'],
  people_directory: ['people-scene', 'people-grid'],
  person_profile: ['people-profile-card'],
  clock_show: ['clock-scene', 'clock-card']
};
const payloads = {
  list_show: { list_type: 'shopping', list_name: 'Shopping', items: [{ id: 'i1', text: 'bread', completed: false }], lists: [{ id: 'l1', name: 'Shopping', list_type: 'shopping' }] },
  calendar_show: { qualifier: 'today', date: '2026-06-23', events: [{ id: 'e1', title: 'Dentist', start_time: '15:00', start_date: '2026-06-23' }] },
  weather_current: { location: { city: 'X' }, current: { temp: 19, description: 'clear' }, forecast: { daily: [{ day: '2026-06-24', high: 20, low: 9 }], hourly: [{ time: '10:00', temp: 18 }] } },
  weather_forecast: { location: { city: 'X' }, current: { temp: 19 }, forecast: { daily: [{ day: '2026-06-24', high: 20, low: 9 }] } },
  people_directory: { people: [{ name: 'Al', relationship: 'friend', health_score: 0.5, context: 'personal' }] },
  person_profile: { person: { name: 'Al', relationship: 'friend', health_score: 0.5 } },
  clock_show: {}
};
const out = {};
for (const [source, hooks] of Object.entries(expect)) {
  const html = R.render(mk(source, payloads[source]));
  out[source] = html.includes('sky-card') && hooks.every(h => html.includes(h));
}
const authHtml = R.render({ component: 'auth_challenge', props: {} });
out.auth_challenge = ['auth-challenge', 'sky-auth-scene', 'sky-auth-profile-grid', 'data-auth-profiles'].every(h => authHtml.includes(h));
process.stdout.write(JSON.stringify(out));
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [node, str(harness), str(UI / "js" / "skybridge-renderer.js")],
        check=True, capture_output=True, text=True,
    )
    import json
    checks = json.loads(proc.stdout)
    assert all(checks.values()), f"card sources failed to render their scenes: {checks}"

    # The renderer registry is a stable wiring contract.
    renderer = read(UI / "js" / "skybridge-renderer.js")
    assert "auth_challenge: renderAuthChallenge" in renderer
    assert "unsupported_contract: renderUnsupportedContract" in renderer

    # Each scene's CSS ships in the ds1 card sheets that skybridge.html links.
    html = read(UI / "skybridge.html")
    for sheet in ("cards/calendar.css", "cards/lists.css", "cards/weather.css",
                  "cards/people.css", "cards/auth-challenge.css", "cards/clock.css"):
        assert sheet in html, f"skybridge.html no longer links {sheet}"
    for hook, sheet in (("lst-row", "cards/lists.css"), ("cal-row", "cards/calendar.css"),
                        ("wx-hero", "cards/weather.css"), ("people-grid", "cards/people.css"),
                        ("clock-scene", "cards/clock.css")):
        assert hook in read(UI / "css" / sheet), f"{hook} missing from {sheet}"



def test_skybridge_renderer_rows_carry_tap_action_and_escape(tmp_path):
    """Render a list + calendar card through the real renderer in Node and confirm
    each row is a data-sky-action target and that '<' injection is HTML-escaped.
    ds1 mechanics: list rows toggle via a check-off query; the (single/first)
    calendar event renders as the cal-hero button carrying an edit query."""
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    renderer_path = UI / "js" / "skybridge-renderer.js"
    harness = tmp_path / "render_harness.cjs"
    harness.write_text(
        """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const sandbox = { window: {} };
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const R = sandbox.window.SkybridgeRenderer;
const listCard = { card_type: 'generic', schema_version: '1.0.0', card_id: 'c1', content: {
    source: 'list_show', list_type: 'shopping', list_name: 'Shopping',
    items: [{ id: 'i1', text: 'bread <b>x</b>', completed: false }],
    lists: [{ id: 'l1', name: 'Shopping', list_type: 'shopping' }]
} };
const calCard = { card_type: 'generic', schema_version: '1.0.0', card_id: 'c2', content: {
    source: 'calendar_show', qualifier: 'today', date: '2026-06-23',
    events: [{ id: 'e1', title: 'Dentist <x>', start_time: '15:00', start_date: '2026-06-23' }]
} };
const listHtml = R.render(listCard);
const calHtml = R.render(calCard);
const checks = {
    list_row_action: /lst-row[^>]*data-sky-action=\"query\"/.test(listHtml),
    list_check_query: listHtml.includes('data-query=\"check off bread &lt;b&gt;x&lt;/b&gt; on the shopping list\"'),
    list_escaped: !listHtml.includes('<b>x</b>') && listHtml.includes('&lt;b&gt;x&lt;/b&gt;'),
    cal_row_action: /cal-hero[^>]*data-sky-action=\"query\"/.test(calHtml),
    cal_edit_query: calHtml.includes('data-query=\"edit Dentist &lt;x&gt; at 15:00\"'),
    cal_escaped: !calHtml.includes('Dentist <x>') && calHtml.includes('Dentist &lt;x&gt;')
};
process.stdout.write(JSON.stringify(checks));
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [node, str(harness), str(renderer_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    import json

    checks = json.loads(proc.stdout)
    assert all(checks.values()), f"renderer harness failed: {checks}"



def test_resting_ambient_clock_is_pure_no_briefing():
    """The resting/standby screen must be a PURE clock — no card, no briefing,
    nothing riding the time (operator-reported twice). The ambient briefing was
    removed from the resting surface; its info lives on the wake dashboard.
    """
    app = read(UI / "js" / "skybridge.js")
    html = read(UI / "skybridge.html")

    # No briefing wiring on the resting path anywhere.
    assert "maybeFetchAmbientBriefing" not in app
    assert "/api/skybridge/briefing" not in app
    assert "skyAmbientBriefing" not in app
    assert "id=\"skyAmbientBriefing\"" not in html
    assert "sky-ambient-briefing" not in html

    # The ambient clock itself is still there (the resting surface is the clock).
    assert 'id="skyAmbientClock"' in html

    # Returning to the resting clock leaves a clean class set — no contradictory
    # sky-has-cards lingering next to sky-empty.
    assert "document.body.classList.remove('sky-has-cards');" in app


def test_skybridge_returns_to_ambient_clock_after_card_idle():
    app = read(UI / "js" / "skybridge.js")
    html = read(UI / "skybridge.html")

    assert "CARD_IDLE_MS" in app
    assert "skybridge_idle_return_ms" in app
    assert "function scheduleIdleReturn" in app
    assert "function returnToAmbientClock" in app
    assert "renderHome({ idle: true })" in app
    assert "function updateAllClocks" in app
    assert ".sky-live-clock" in app
    assert "id=\"skyAmbientClock\"" in html
    # The ambient clock must not be a live region (it ticks every second).
    ambient_start = html.index("id=\"skyAmbientClock\"")
    ambient_tag = html[html.rfind("<div", 0, ambient_start):html.find(">", ambient_start) + 1]
    assert "aria-live" not in ambient_tag
    assert "sky-ambient-clock" in html



def test_skybridge_weather_renderer_uses_widget_forecast_structure():
    """Intent: forecast dates parse timezone-safely (no bare new Date(raw)) and
    the weather card keeps distinct day rows + hourly strip structures."""
    renderer = read(UI / "js" / "skybridge-renderer.js")
    forecast_label_helper = renderer[
        renderer.index("function formatForecastLabel"):
        renderer.index("function formatForecastShort")
    ]

    assert "const datePart = raw.slice(0, 10);" in forecast_label_helper
    assert r"/^\d{4}-\d{2}-\d{2}$/.test(datePart)" in forecast_label_helper
    assert "new Date(datePart + 'T12:00:00')" in forecast_label_helper
    assert "new Date(raw + 'T12:00:00')" not in forecast_label_helper
    # ds1 structure: day rows (wx-drow) and hourly strip (wx-hr) are separate.
    assert "wx-drow" in renderer
    assert "wx-hr" in renderer
    assert "wx-hero" in read(UI / "css" / "cards/weather.css")



def test_skybridge_list_renderer_has_switcher_columns_and_new_list_action():
    renderer = read(UI / "js" / "skybridge-renderer.js")
    css = read(UI / "css" / "cards/lists.css")

    # Switcher tabs, a "+ New" tab wired to the new-list flow, and overview columns.
    assert "lst-switcher" in renderer
    assert "lst-tab" in renderer
    assert "lst-tab-new" in renderer
    assert 'data-query="new list"' in renderer
    assert "lst-col" in renderer
    assert "lst-switcher" in css
    assert "lst-tab" in css
    assert "lst-row" in css

    # Redesign contract: the switcher is the TOP tab row (active tab == title, so
    # there is no separate big heading), items flow into a multi-column grid, and
    # a keep-out cell reserves the bottom-left corner so items wrap around the orb.
    assert "lst-header" not in renderer  # the old vertical-space-wasting title block is gone
    assert "is-grid" in renderer
    assert "lst-keepout" in renderer
    assert "is-grid" in css
    assert "lst-keepout" in css
    assert "grid-auto-flow: column" in css

    # a11y: only real tabs live in the tablist; "+ New" is an action sibling.
    assert 'role="tablist"' in renderer
    assert "lst-tablist" in renderer
    assert "lst-tablist" in css and "display: contents" in css
    # Short lists use adaptive rows (no fixed ~604px reservation).
    assert "--lst-rows:" in renderer


def test_skybridge_list_short_list_is_compact_no_keepout(tmp_path):
    """A short list sets an adaptive --lst-rows and omits the orb keep-out (it is
    too short to reach the orb); a long list keeps the 9-row wrap + keep-out."""
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = tmp_path / "lst_rows.cjs"
    harness.write_text(
        """
const fs = require('fs'); const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const sandbox = { window: {} }; vm.createContext(sandbox); vm.runInContext(src, sandbox);
const R = sandbox.window.SkybridgeRenderer;
function mk(n){ const items=[]; for(let i=0;i<n;i++) items.push({id:'i'+i,text:'Item '+i,done:false});
  return R.render({card_type:'generic',schema_version:'1.0.0',card_id:'l',content:{
    source:'list_show', list_type:'shopping', name:'Shopping',
    lists:[{id:'shopping',name:'Shopping',type:'shopping'}], selected:'shopping', items:items}}); }
const short = mk(3), long = mk(20);
process.stdout.write(JSON.stringify({
  short_rows: /--lst-rows:\\s*\\d/.test(short),
  short_no_keepout: !short.includes('lst-keepout'),
  long_keepout: long.includes('lst-keepout')
}));
""",
        encoding="utf-8",
    )
    proc = subprocess.run([node, str(harness), str(UI / "js" / "skybridge-renderer.js")],
                          check=True, capture_output=True, text=True)
    import json
    c = json.loads(proc.stdout)
    assert c["short_rows"], "short list should set adaptive --lst-rows"
    assert c["short_no_keepout"], "short list should omit the orb keep-out"
    assert c["long_keepout"], "long list should keep the orb keep-out"



def test_skybridge_calendar_renderer_handles_datetime_dates_and_ordering(tmp_path):
    """Intent: calendar dates parse timezone-safely and events render earliest-first.
    Ordering is asserted behaviorally through the real renderer."""
    renderer = read(UI / "js" / "skybridge-renderer.js")
    calendar_date_helper = renderer[
        renderer.index("function formatCalendarDate"):
        renderer.index("function calendarEventSortKey")
    ]
    assert "const datePart = raw.slice(0, 10);" in calendar_date_helper
    assert r"/^\d{4}-\d{2}-\d{2}$/.test(datePart)" in calendar_date_helper
    assert "new Date(datePart + 'T12:00:00')" in calendar_date_helper
    assert "new Date(raw + 'T12:00:00')" not in calendar_date_helper
    assert "function calendarEventSortKey" in renderer
    assert "function calendarEditQuery" in renderer
    assert "function accentClass" in renderer

    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = tmp_path / "cal_order.cjs"
    harness.write_text(
        """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
const sandbox = { window: {} };
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const R = sandbox.window.SkybridgeRenderer;
const html = R.render({ card_type: 'generic', schema_version: '1.0.0', card_id: 'c', content: {
  source: 'calendar_show', qualifier: 'today', date: '2026-06-23',
  events: [
    { id: 'late', title: 'LateEvent', start_time: '15:00', start_date: '2026-06-23' },
    { id: 'early', title: 'EarlyEvent', start_time: '08:00', start_date: '2026-06-23' }
  ]
} });
process.stdout.write(JSON.stringify({ earliest_first: html.indexOf('EarlyEvent') !== -1 && html.indexOf('EarlyEvent') < html.indexOf('LateEvent') }));
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [node, str(harness), str(UI / "js" / "skybridge-renderer.js")],
        check=True, capture_output=True, text=True,
    )
    import json
    checks = json.loads(proc.stdout)
    assert checks["earliest_first"], "calendar events must render earliest-first"


def test_skybridge_calendar_ribbon_and_now_line(tmp_path):
    """Behavioral coverage for the ribbon helpers (calMinutes/calDuration/
    calHourLabel/calGutter/calendarRibbon): a busy 'today' renders the ribbon with
    hour ticks + event blocks, and the live now-line stays inside the rail even
    when the current time lands exactly on an hour boundary (was clipped at 100%)."""
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = tmp_path / "cal_ribbon.cjs"
    harness.write_text(
        """
const fs = require('fs');
const vm = require('vm');
const src = fs.readFileSync(process.argv[2], 'utf8');
// Freeze "now" to an EXACT hour boundary (23:00) on the card's date so the
// now-line's window end is driven by nowMin — the case that used to clip.
const FIXED = new Date('2026-06-23T23:00:00').getTime();
class FakeDate extends Date {
  constructor(...a){ if (a.length === 0) super(FIXED); else super(...a); }
  static now(){ return FIXED; }
}
const sandbox = { window: {}, Date: FakeDate };
vm.createContext(sandbox);
vm.runInContext(src, sandbox);
const R = sandbox.window.SkybridgeRenderer;
const html = R.render({ card_type: 'generic', schema_version: '1.0.0', card_id: 'c', content: {
  source: 'calendar_show', qualifier: 'today', date: '2026-06-23',
  events: [
    { id: 'a', title: 'Standup', start_time: '08:00', end_time: '08:30', start_date: '2026-06-23' },
    { id: 'b', title: 'Lunch', start_time: '12:00', end_time: '13:00', start_date: '2026-06-23' }
  ]
} });
const m = html.match(/cal-ribbon-now[^>]*left:([0-9.]+)%/);
process.stdout.write(JSON.stringify({
  ribbon: html.includes('cal-ribbon'),
  ticks: html.includes('cal-tick'),
  blocks: html.includes('cal-block'),
  now_line: !!m,
  now_left: m ? parseFloat(m[1]) : -1
}));
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [node, str(harness), str(UI / "js" / "skybridge-renderer.js")],
        check=True, capture_output=True, text=True,
    )
    import json
    c = json.loads(proc.stdout)
    assert c["ribbon"] and c["ticks"] and c["blocks"], f"ribbon markup missing: {c}"
    assert c["now_line"], "live now-line should render on today"
    assert 0 <= c["now_left"] < 99.5, f"now-line clipped at boundary: left={c['now_left']}"


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
    """The auth picker's cross-file contract: renderer shell, skybridge.js
    hydration, executor wiring, service payload, and ds1 stylesheet."""
    app = read(UI / "js" / "skybridge.js")
    renderer = read(UI / "js" / "skybridge-renderer.js")
    executor = (ROOT / "zoe-ui" / "dist" / "js" / "touch-ui-executor.js").read_text(encoding="utf-8")
    auth_css = read(UI / "css" / "cards/auth-challenge.css")
    service = read(DATA / "skybridge_service.py")

    # Renderer shell + hydration hooks.
    assert "auth_challenge: renderAuthChallenge" in renderer
    assert "function renderAuthChallenge" in renderer
    assert "sky-auth-people-only" in renderer
    assert "sky-auth-profile-grid" in renderer
    assert "data-auth-profiles" in renderer

    # skybridge.js hydrates profiles (abortable, panel-scoped) and routes selection.
    assert "function hydrateAuthCard" in app
    assert "window.SkybridgeHydrateAuthCard = hydrateAuthCard" in app
    assert "AbortController" in app
    assert "/api/auth/profiles?panel_id=" in app
    assert "trySelectAuthProfile" in app
    assert "buildLoginRoute(panelId, profile.user_id)" in app

    # The touch executor renders the same component and reuses the hydrator.
    assert "renderSkybridgeAuthChallenge" in executor
    assert "component: 'auth_challenge'" in executor
    assert "window.SkybridgeHydrateAuthCard" in executor

    # Service emits the component; ds1 sheet styles the picker.
    assert '"component": "auth_challenge"' in service
    assert "sky-auth-profile" in auth_css
    assert "sky-authx" in auth_css


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

    # Intent: the live voice/data surface must never be served from SW cache.
    # (No version-string pin here — SW_VERSION churns every release; the ROUTE
    # guard below is the actual contract.)
    assert "Skybridge is a live voice/data surface" in sw
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
    assert 'height: min(604px, calc(100dvh - 92px))' in html
    assert 'weather-card.sky-premium-card::before' in html
    assert 'display: none !important' in html
    assert 'min-height: 57px !important' in html
    assert 'font-size: 16px !important' in html
    assert 'font-size: 13px !important' in html
    assert 'minmax(610px, 1fr)' in html
    assert 'max-width: 520px !important' in html
    assert 'font-size: 28px !important' in html
    assert 'width: min(920px, calc(100vw - 180px))' in html
    assert 'left: calc(50% - 425px)' in html
    assert 'left: calc(50% - 350px)' in html
    assert 'person-profile-card.sky-premium-card' in html
    assert 'sky-list-item-row:nth-child(n+17)' in html
    assert 'min-height: 48px !important' in html
    assert 'sky-list-switcher' in html
    assert 'min-height: 56px !important' in html
    assert 'font-size: 18px !important' in html
    assert 'sky-list-column li:nth-child(n+4)' in html
    assert 'sky-card.list-card.sky-premium-card' in html
    assert 'grid-template-columns: repeat(2, minmax(0, 1fr)) !important' in html
    assert 'grid-template-columns: repeat(3, minmax(0, 1fr)) !important' in html
    assert 'sky-panel-orb-float' in html
    assert 'margin-bottom: 3px' in html
    assert 'width: 132px !important' in html
    assert 'max-width: none !important' in html
    assert 'grid-template-columns: repeat(8, minmax(46px, 1fr))' in html
    assert 'body:not(.sky-empty) .sky-command' in html
    assert '.sky-orb-button span' in html
    assert '.sky-orb-button::before' in html
    assert 'display: none !important' in html
    assert 'skybridge-native-panel-fit-final' in html
    assert 'inset: 24px 24px 112px !important' in html
    assert 'Native Zoe touch panel: 1280x720 rotated DSI display' in html


def test_skybridge_weather_renderer_accepts_temperature_aliases():
    renderer = read(UI / "js" / "skybridge-renderer.js")

    assert "function weatherValue(source, keys)" in renderer
    assert "['temp', 'temperature', 'temperature_c', 'temp_c', 'current_temp']" in renderer
    assert "['feels_like', 'feels_like_c', 'apparent_temperature']" in renderer
    assert "['temp', 'temperature', 'temperature_c', 'temp_c', 'high']" in renderer


def test_skybridge_generic_renderers_fill_the_stage(tmp_path):
    """The legacy generic renderers (status/info/generic/stream_text, page,
    setting, page_grid, settings_overview, the generic list fallback,
    action_form/form, media, research_report and the unsupported-schema card) are
    rebuilt to the ds1 "fill-the-stage" standard: a `.gx-scene` with a header band
    (`.gx-head`) over a growing body (`.gx-body`) — no content stranded in a dark
    void. Behavioral: render each type through the real renderer and assert
    (a) the gx- scene grammar, (b) no legacy sky-card-body/sky-field markup,
    (c) injection stays escaped, (d) every action keeps data-sky-action so tap +
    voice still work. `smart_home` is NOT rebuilt here — it keeps its zoe-compose
    body, so it is checked separately."""
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = tmp_path / "generic_stage_harness.cjs"
    harness.write_text(
        """
const fs = require('fs');
const vm = require('vm');
const sandbox = { window: {} };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox); // zoe-compose first (like the panel)
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), sandbox); // then renderer
const R = sandbox.window.SkybridgeRenderer;
const INJ = 'sneaky <b>x</b>';
const cards = {
  status: { component: 'status', props: { title: 'Status ' + INJ, body: 'Body ' + INJ, metric: '42', metric_label: 'answers', actions: [{ label: 'More', query: 'more status' }] } },
  page: { component: 'page', props: { title: 'Calendar ' + INJ, summary: 'Summary ' + INJ, route: '/touch/calendar.html' } },
  setting: { component: 'setting', props: { title: 'Trust Gate ' + INJ, summary: 'Summary ' + INJ, risk: 'critical', domain: 'security', route: '/touch/settings.html#trust-gate' } },
  page_grid: { component: 'page_grid', props: { title: 'Map', items: [{ title: 'A ' + INJ, summary: 'sa' }, { title: 'B', summary: 'sb ' + INJ }], actions: [{ label: 'Open', query: 'open map' }] } },
  settings_overview: { component: 'settings_overview', props: { title: 'Settings', items: [{ title: 'API ' + INJ, risk: 'high', summary: 'keys' }], actions: [{ label: 'Open', query: 'open settings' }] } },
  list: { component: 'list', props: { title: 'Related', items: ['plain ' + INJ, { title: 'obj ' + INJ, summary: 'detail ' + INJ }], actions: [{ label: 'One', query: 'one' }] } },
  action_form: { component: 'action_form', props: { title: 'Form ' + INJ, form_id: 'f1', summary: 'Check ' + INJ, fields: [{ label: 'Who ' + INJ, value: 'me ' + INJ }, { label: 'Empty', value: '' }] } },
  form: { component: 'form', props: { title: 'Form2 ' + INJ, form_id: 'f2', fields: [{ name: 'x', value: 'y ' + INJ }] } },
  list_create: { component: 'action_form', props: { source: 'list_create', title: 'New list', summary: 'Name it ' + INJ, fields: [{ label: 'List type', value: 'Personal' }, { label: 'Name', value: '' }], actions: [{ label: 'Create', query: 'create it' }] } },
  media: { component: 'media', props: { title: 'Now playing', items: [{ title: 'Song ' + INJ, artist: 'Band', artwork: '/touch/img/art.png' }, { title: 'NoArt', artist: 'X', artwork: 'https://evil.example/a.png' }], actions: [{ label: 'Pause', query: 'pause music' }] } },
  research_report: { component: 'research_report', props: { title: 'Report', sections: [{ title: 'Findings ' + INJ, body: 'Text ' + INJ, items: [{ name: 'Opt ' + INJ, value: '$5' }] }], actions: [{ label: 'Sources', query: 'show sources' }] } }
};
const out = {};
for (const [name, card] of Object.entries(cards)) {
  const html = R.render(card);
  out[name] = {
    scene: html.includes('sky-card') && html.includes('gx-card') && html.includes('gx-scene'),
    fills: html.includes('gx-head') && html.includes('gx-body'),
    no_legacy: !html.includes('sky-card-body') && !html.includes('sky-field') && !html.includes('sky-card-grid') && !html.includes('zx-root'),
    escaped: !html.includes('<b>x</b>') && html.includes('&lt;b&gt;x&lt;/b&gt;'),
    action: html.includes('data-sky-action=')
  };
}
// Type-specific structure checks.
out.status.metric = R.render(cards.status).includes('gx-figure-value') && R.render(cards.status).includes('42');
out.page.open_route = /data-sky-action=\\"open\\"[^>]*data-route=\\"\\/touch\\/calendar.html\\"/.test(R.render(cards.page));
out.setting.warn_change = R.render(cards.setting).includes('warn') && R.render(cards.setting).includes('gx-critical');
out.page_grid.grid = R.render(cards.page_grid).includes('gx-tiles');
(function(){
  var h = R.render(cards.settings_overview);
  out.settings_overview.risk = h.includes('gx-tile-risk') && h.includes('API sneaky &lt;b&gt;x&lt;/b&gt;') && h.includes('high');
})();
out.action_form.not_set = R.render(cards.action_form).includes('Not set');
out.list_create.identity = R.render(cards.list_create).includes('gx-list-create') && R.render(cards.list_create).includes('New list');
out.list.index_cue = R.render(cards.list).includes('01') && R.render(cards.list).includes('02');
(function(){
  var h = R.render(cards.media);
  out.media.tile = h.includes('gx-media-art') && h.includes('/touch/img/art.png');
  out.media.foreign_art_dropped = !h.includes('evil.example');
})();
out.research_report.kicker = R.render(cards.research_report).includes('gx-sec-kicker');
// Unsupported-schema card also fills the stage as a calm empty state.
(function(){
  var h = R.render({ component: 'unsupported_contract', props: { schema_version: '9.9.9' } });
  out.unsupported = { scene: h.includes('gx-scene') && h.includes('gx-empty'), no_legacy: !h.includes('sky-card-body') };
})();
// smart_home is NOT rebuilt here: it keeps its zoe-compose body.
(function(){
  var h = R.render({ component: 'smart_home', props: { title: 'Lights', devices: [{ name: 'Lamp ' + INJ, state: 'on' }, { entity_id: 'light.hall', state: 'off' }], actions: [{ label: 'All off', query: 'lights off' }] } });
  var empty = R.render({ component: 'smart_home', props: { title: 'Lights', devices: [] } });
  out.smart_home = {
    grid_state: h.includes('zx-grid') && h.includes('<em>on</em>'),
    escaped: !h.includes('<b>x</b>') && h.includes('&lt;b&gt;x&lt;/b&gt;'),
    action: h.includes('data-sky-action='),
    empty_container: empty.includes('zx-stack') && empty.includes('No devices available.')
  };
})();
// The rebuilt renderers no longer depend on zoe-compose: render with NO catalog
// loaded and confirm the generic answer card still fills the stage, escaped.
const bare = { window: {} };
vm.createContext(bare);
vm.runInContext(fs.readFileSync(process.argv[3], 'utf8'), bare);
const bareHtml = bare.window.SkybridgeRenderer.render(cards.status);
out.no_compose_needed = {
  text: bareHtml.includes('Body sneaky') && bareHtml.includes('&lt;b&gt;x&lt;/b&gt;'),
  scene: bareHtml.includes('sky-card') && bareHtml.includes('gx-scene'),
  no_tree: !bareHtml.includes('zx-root')
};
process.stdout.write(JSON.stringify(out));
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [node, str(harness), str(UI / "js" / "zoe-compose.js"), str(UI / "js" / "skybridge-renderer.js")],
        check=True, capture_output=True, text=True,
    )
    import json
    out = json.loads(proc.stdout)
    failures = {
        f"{card}.{check}": ok
        for card, checks in out.items()
        for check, ok in checks.items()
        if not ok
    }
    assert not failures, f"generic renderers failed the fill-the-stage contract: {failures}"

    # The ds1 fill-the-stage scene CSS ships in a linked card sheet.
    html = read(UI / "skybridge.html")
    assert "cards/generic.css" in html
    generic_css = read(UI / "css" / "cards" / "generic.css")
    for hook in ("gx-scene", "gx-head", "gx-body", "gx-facts", "gx-empty"):
        assert hook in generic_css, f"{hook} missing from cards/generic.css"

    # The legacy list_create CSS died with the legacy markup.
    css = read(UI / "css" / "skybridge-data-widgets.css")
    assert "sky-list-create" not in css


def test_skybridge_list_create_has_database_conflict_guard():
    service = read(DATA / "skybridge_service.py")
    migration = read(DATA / "alembic" / "versions" / "0011_unique_active_list_names.py")

    assert "ON CONFLICT (user_id, lower(name)) WHERE deleted = 0 DO NOTHING" in service
    assert "idx_lists_active_user_lower_name" in migration
    assert "ON lists (user_id, lower(name))" in migration
    assert "WHERE deleted = 0" in migration
    assert "UPDATE list_items" in migration


def test_skybridge_stage_v2_fullscreen_cards_and_floating_chrome():
    """Stage v2: while a card shows, the stage is near-fullscreen and the Home
    pill + orb float above it (fixed, high z-index, blur scrim, >=48px)."""
    html = read(UI / "skybridge.html")
    stage = read(UI / "css" / "skybridge-stage.css")

    # Stage sheet is linked LAST (after the ds1 card sheets) with a cache-buster.
    assert "/touch/css/skybridge-stage.css?v=stage1" in html
    assert html.index("skybridge-stage.css") > html.index("cards/compose.css")

    # Floating chrome classes present on the Home pill and the orb tap target.
    assert 'class="sky-nav-home sky-float-chrome"' in html
    assert 'class="sky-orb-button sky-float-chrome"' in html
    assert ".sky-float-chrome" in stage
    assert "position: fixed !important" in stage
    assert "z-index: 60 !important" in stage
    assert "backdrop-filter" in stage
    assert "min-height: 50px" in stage          # Home pill tap target
    assert "min-height: 48px !important" in stage  # orb tap target floor

    # Near-fullscreen single-card stage; timer tiles keep their layout.
    assert "inset: 16px !important" in stage
    assert "min-height: 100% !important" in stage
    assert ".sky-card.timer" in stage
    assert "grid-column: span 6 !important" in stage

    # Ambient/idle composition untouched: the stage sheet never targets
    # sky-empty or the rest-dim curve (PR #1126 territory).
    assert "body.sky-empty" not in stage
    assert "data-rest-dim" not in stage


def test_skybridge_dashboard_tiles_carry_query_actions(tmp_path):
    """The ambient wake dashboard (View Assist cue): left third = live clock,
    right two-thirds = 2x3 shortcut grid. Behavioral: render through the real
    renderer and assert every tile is a working data-sky-action target."""
    app = read(UI / "js" / "skybridge.js")
    css = read(UI / "css" / "cards/dashboard.css")

    # Home taps and panel wake both land on the dashboard surface.
    assert "wakeToDashboard()" in app
    assert "function renderDashboardSurface" in app
    assert "renderHome({ idle: true })" in app  # idle still rests on the clock

    # 2x3 grid + kitchen-glance tile sizing in the ds1 sheet.
    assert "grid-template-columns: repeat(3, minmax(0, 1fr))" in css
    assert "minmax(120px, 1fr)" in css
    assert "min-height: 120px" in css

    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    harness = tmp_path / "dash_harness.cjs"
    harness.write_text(
        """
const fs = require('fs');
const vm = require('vm');
const sandbox = { window: {} };
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
const R = sandbox.window.SkybridgeRenderer;
const html = R.render({ component: 'dashboard', props: { guest: true, weather: { current: { temp: 19, description: 'clear' } } } });
const tile = q => new RegExp('data-sky-action="query" data-query="' + q + '"').test(html);
const out = {
  weather: tile('what is the weather'),
  calendar: tile('show my calendar'),
  lists: tile('show my shopping list'),
  music: tile('play some music'),
  lights: tile('turn on the lights'),
  timers: tile('show my timers'),
  grid: html.includes('dash-tiles') && (html.match(/dash-ctrl--/g) || []).length === 6,
  clock: html.includes('sky-ambient-time') && html.includes('sky-live-clock') && html.includes('data-clock-hour'),
  live_weather: html.includes('19°'),
  signin: html.includes('data-sky-action="auth"')
};
process.stdout.write(JSON.stringify(out));
""",
        encoding="utf-8",
    )
    proc = subprocess.run(
        [node, str(harness), str(UI / "js" / "skybridge-renderer.js")],
        check=True, capture_output=True, text=True,
    )
    import json
    checks = json.loads(proc.stdout)
    assert all(checks.values()), f"dashboard tiles failed: {checks}"


def test_skybridge_activity_strip_wiring():
    """Live-activity strip: server forwards brain tool sentinels as activity
    frames; the transport demuxes them; the app renders a compact escaped strip."""
    voice = read(UI / "js" / "skybridge-voice.js")
    app = read(UI / "js" / "skybridge.js")
    css = read(UI / "css" / "skybridge-data-widgets.css")
    main = read(DATA / "main.py")
    tts = read(DATA / "routers" / "voice_tts.py")

    # Server: the /ws/voice/ brain lane routes every delta through the sentinel
    # filter/forwarder (never buffers sentinels toward TTS), and the helper
    # emits name+phase-only activity frames.
    assert "_forward_voice_activity(delta, websocket.send_json" in main
    assert "async def _forward_voice_activity" in tts
    assert '"type": "activity"' in tts

    # Transport demux (skybridge-voice.js) re-emits activity frames.
    assert "type === 'activity'" in voice
    assert "phase: msg.phase || ''" in voice
    assert "tool: msg.tool || ''" in voice

    # App (skybridge.js) consumes them: activity case, verb map, strip element.
    assert "event.type === 'activity'" in app
    assert "function showActivity" in app
    assert "function activityToolVerb" in app
    assert "function clearActivity" in app
    assert "skyActivityStrip" in app

    # Escaping discipline: the wire-derived tool name is sanitized by a local
    # escaper and only ever reaches the DOM via textContent, never innerHTML.
    assert "replace(/[^a-z0-9_-]/g, '')" in app
    strip_impl = app.split("function showActivity")[1].split("function setContext")[0]
    assert "textContent" in strip_impl
    assert ".innerHTML" not in strip_impl

    # The strip clears when the turn returns to ambient (done → ambient).
    assert "clearActivity()" in app

    # Style: strip exists, uses ds1 tokens, stays a single non-wrapping line.
    assert ".sky-activity-strip" in css
    assert ".sky-activity-strip.is-active" in css
    assert ".sky-activity-strip.is-done" in css
    assert "var(--sky-" in css.split(".sky-activity-strip", 1)[1]
    assert "white-space: nowrap" in css


def test_dashboard_auth_chip_and_sun_line(tmp_path):
    """Signed-in panels show WHO is signed in (not 'Sign in'); sun line renders."""
    harness = tmp_path / "dash_auth.cjs"
    harness.write_text(
        """
const fs=require('fs'),vm=require('vm');const s={window:{},sessionStorage:{getItem:()=>null}};
vm.createContext(s);vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),s);
vm.runInContext(fs.readFileSync(process.argv[3],'utf8'),s);
const R=s.window.SkybridgeRenderer;
const sun={rise:new Date().toISOString(), set:new Date().toISOString()};
const signedIn=R.render({component:'dashboard',props:{guest:false,user_name:'Jason',sun:sun}});
const guest=R.render({component:'dashboard',props:{guest:true}});
process.stdout.write(JSON.stringify({
  profile_chip: signedIn.includes('dash-ctrl-profile') && signedIn.includes('Jason') && signedIn.includes('Tap to switch'),
  no_signin_when_authed: !signedIn.includes('>Sign in<'),
  auth_action_kept: signedIn.includes('data-sky-action="auth"'),
  // ICU-aware: on stripped-ICU Node, Intl time formatting can yield '' and the
  // renderer (correctly) omits the sun line — only require it when Intl works.
  sun_line: (function(){try{
    var probe=new Intl.DateTimeFormat(undefined,{hour:'numeric',minute:'2-digit',hour12:true}).format(new Date());
    return probe ? signedIn.includes('dash-sun') : true;
  }catch(e){return true;}})(),
  guest_still_signin: guest.includes('>Sign in<') && !guest.includes('dash-ctrl-profile')
}));
""", encoding="utf-8")
    import json as _json
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js is not installed on this host")
    proc = subprocess.run([node, str(harness), str(UI / "js" / "zoe-compose.js"),
                           str(UI / "js" / "skybridge-renderer.js")],
                          check=True, capture_output=True, text=True)
    checks = _json.loads(proc.stdout)
    assert all(checks.values()), f"dashboard auth chip failed: {checks}"


def test_now_playing_card_renders_transport_and_escapes(tmp_path):
    """Music now-playing card: SVG transport with data-sky-action, escaped meta."""
    harness = tmp_path / "np.cjs"
    harness.write_text(
        """
const fs=require('fs'),vm=require('vm');const s={window:{}};vm.createContext(s);
vm.runInContext(fs.readFileSync(process.argv[2],'utf8'),s);
vm.runInContext(fs.readFileSync(process.argv[3],'utf8'),s);
const R=s.window.SkybridgeRenderer;
const html=R.render({card_type:'now_playing',schema_version:'1.0.0',card_id:'np',
  content:{source:'music_now_playing',title:'Song <b>x</b>',artist:'Artist',state:'playing',player_name:'Kitchen',transport:true}});
// Same-origin album art → blurred backdrop + <img>; cross-origin-ish paths are dropped.
const artHtml=R.render({card_type:'now_playing',schema_version:'1.0.0',card_id:'np2',
  content:{source:'music_now_playing',title:'T',artist:'A',album:'Alb',state:'playing',player_name:'Den',image:'/media/cover.png',transport:true,elapsed:60,duration:180}});
const badHtml=R.render({card_type:'now_playing',schema_version:'1.0.0',card_id:'np3',
  content:{source:'music_now_playing',title:'T',state:'playing',image:'//evil.example/x.png"onerror=alert(1)',transport:true}});
const absHtml=R.render({card_type:'now_playing',schema_version:'1.0.0',card_id:'np4',
  content:{source:'music_now_playing',title:'T',state:'playing',image:'https://cdn.example.com/a/cover.png',transport:true}});
const angleHtml=R.render({card_type:'now_playing',schema_version:'1.0.0',card_id:'np5',
  content:{source:'music_now_playing',title:'T',state:'playing',image:'/media/c<over>.png',transport:true}});
process.stdout.write(JSON.stringify({
  card: html.includes('now-playing-card'),
  transport: html.includes('np-transport') && html.includes('np-btn'),
  pause_when_playing: html.includes('data-query="pause music"'),
  actions_wired: html.includes('data-sky-action="query"') && html.includes('data-query="next song"'),
  escaped: html.includes('&lt;b&gt;') && !html.includes('<b>x</b>'),
  no_dup_header: (html.match(/np-title/g)||[]).length===1,
  ambient: html.includes('np-ambient'),
  placeholder_when_no_art: html.includes('np-art-empty'),
  art_backdrop_same_origin: artHtml.includes('np-art-bg') && artHtml.includes('src="/media/cover.png"'),
  art_backdrop_absolute: absHtml.includes('np-art-bg') && absHtml.includes('src="https://cdn.example.com/a/cover.png"'),
  progress_when_elapsed: artHtml.includes('np-progress') && artHtml.includes('1:00'),
  reject_protocol_relative: !badHtml.includes('np-art-bg') && badHtml.includes('np-art-empty') && !badHtml.includes('onerror'),
  reject_angle_brackets: !angleHtml.includes('np-art-bg') && angleHtml.includes('np-art-empty')
}));
""", encoding="utf-8")
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        pytest.skip("Node.js not installed")
    proc = subprocess.run([node, str(harness), str(UI / "js" / "zoe-compose.js"),
                           str(UI / "js" / "skybridge-renderer.js")], check=True, capture_output=True, text=True)
    import json as _j
    checks = _j.loads(proc.stdout)
    assert all(checks.values()), f"now-playing render failed: {checks}"


def test_nowplaying_miniplayer_present_and_wired():
    """The persistent now-playing mini-player: markup in the shell + poll/control
    wiring in skybridge.js. It's floating chrome shown only while music plays."""
    html = read(UI / "skybridge.html")
    # DOM: the container + the five transport/volume actions + tap-to-expand.
    assert 'id="skyNowPlaying"' in html
    for action in ("expand", "previous", "play_pause", "next", "volume_down", "volume_up"):
        assert f'data-np-action="{action}"' in html, f"missing mini-player action: {action}"
    # CSS: centered floating pill by default; repositioned under the clock at rest.
    assert ".sky-nowplaying" in html
    assert "body.sky-empty .sky-nowplaying" in html
    js = read(UI / "js" / "skybridge.js")
    # Behaviour: polls now-playing, drives control, expands to the full card.
    assert "/api/music/now-playing" in js
    assert "/api/music/control" in js
    assert "startNowPlayingWatch" in js
    assert "what's playing" in js  # tap-to-expand opens the full music card


def test_music_hub_output_picker_and_add_source_wired():
    """Music hub: the now-playing card carries a speaker/output picker + a
    persistent 'Add source' affordance; the mini-player carries a compact output
    button + popover; skybridge.js persists the pick and threads it everywhere."""
    html = read(UI / "skybridge.html")
    renderer = read(UI / "js" / "skybridge-renderer.js")
    js = read(UI / "js" / "skybridge.js")

    # Card renderer: output button (client-side picker) + inline picker container
    # + persistent add-source button reusing the "add music" resolver flow.
    assert "data-music-output" in renderer
    assert "data-music-picker" in renderer
    assert 'class="np-output"' in renderer
    assert "np-out-name" in renderer
    assert 'data-sky-action="query" data-query="add music"' in renderer  # add source

    # Mini-player: compact output button + floating popover container.
    assert 'class="snp-btn snp-btn-sm snp-out" data-music-output' in html
    assert 'id="skyNpOutputs"' in html
    assert "data-music-picker" in html
    assert ".snp-outputs" in html

    # skybridge.js: persist the chosen speaker + thread it into poll/control,
    # fetch players, render the picker, and transfer live playback on select.
    assert "zoe_music_player_id" in js
    assert "function getMusicPlayerId" in js
    assert "function selectMusicPlayer" in js
    assert "function toggleMusicPicker" in js
    assert "/api/music/players" in js
    assert "/api/music/transfer" in js
    # Poll targets the persisted speaker; control POSTs the persisted speaker.
    assert "'?player_id=' + encodeURIComponent(pid)" in js
    assert "player_id: getMusicPlayerId() || npPlayerId" in js
    # Selection routes on both the card grid and the mini-player.
    assert "data-music-player" in js
    assert "target_player_id: id, source_player_id: prev" in js

    # ds1 tokens for the new picker chrome (no rgba(var()); accents via color-mix).
    css = read(UI / "css" / "cards/dashboard.css")
    assert ".np-output" in css
    assert ".mp-opt" in css
    assert "rgba(var(" not in css


def test_music_transfer_endpoint_shape():
    """POST /api/music/transfer delegates to music_service.transfer, which speaks
    MA's player_queues/transfer command with source/target queue ids."""
    router = read(DATA / "routers" / "music.py")
    service = read(DATA / "music_service.py")

    assert '@router.post("/transfer")' in router
    assert "async def music_transfer" in router
    assert "target_player_id" in router
    assert "music_service.transfer(target, source_player_id=source)" in router

    assert "async def transfer(target_player_id: str, source_player_id: str = \"\")" in service
    assert '"player_queues/transfer"' in service
    assert "source_queue_id=source_id, target_queue_id=target_player_id" in service
