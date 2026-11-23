"""``ZOE_OS_LOOKUPS=kernel`` and ``ZOE_OS_MESSAGES=kernel`` (both default off; ``lookup_door.py``, ``message_door.py``).

LOOKUPS (weather, web search, page fetch): data leaves the box, nothing is changed. Told to the kernel AFTER the request went
out (``sent``, at the line that sent it), never asked, never delayed; a lookup that never left (an empty query, a cache hit, a
provider off or missing, a private URL) is not recorded; once per class per request; nobody identified acting = automatic, not
recorded; a caller KNOWN to be off the roster is refused before anything is sent (``check``, which records nothing); only who
and the class cross. Every outbound sender in zoe-data is listed (``SENDERS``), so a new one fails until it is placed.

MESSAGES: a member asking Zoe to send something asks first (the intercom, the panel toast, the sign-in link to their own
phone); a refusal sends nothing; kernel down = sent, ``not_checked``; EXCEPT an outside address, which is never sent
unchecked. Zoe's own notifications never reach the door. Only the channel class and member ids cross.

Flag off = main: the doors answer after one flag read, and the existing suites of every touched sender run unchanged.
Each test names the control that was run against it. Invented people and words only.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import shutil
import tempfile
import sys
import time
import types
from pathlib import Path

import pytest

import auth_handoff
import kernel_door
import lookup_door
import message_door
import os_shadow
import user_erase_gate
from test_auth_handoff import _link_telegram, _start, hdb  # noqa: F401 - the hand-off's own fixture and helpers
from test_intercom import ADULT, KITCHEN, _rows, _say, house  # noqa: F401 - the simulated house
from test_kernel_door import AreaKernel

pytestmark = pytest.mark.ci_safe
SVC = Path(__file__).resolve().parents[1]
ANN, STRANGER = "member-ann", "member-zzz"


@pytest.fixture
def door(monkeypatch):
    k = AreaKernel()
    state = Path(tempfile.mkdtemp(prefix="osd"))
    (state / "creds.json").write_text(json.dumps({"socket": k.path, "creds": {s: c.hex() for s, c in k.creds.items()}}))
    (state / "people.json").write_text(json.dumps({"people": [ANN, ADULT, "jason"]}))
    monkeypatch.setenv("ZOE_OS_SHADOW_DIR", str(state))
    monkeypatch.setenv("ZOE_OS_LOOKUPS", "kernel")
    monkeypatch.setenv("ZOE_OS_MESSAGES", "kernel")
    monkeypatch.setattr(os_shadow, "OP_TIMEOUT_S", 0.3)
    monkeypatch.setattr(os_shadow, "ASK_DEADLINE_S", 0.3)
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    token, done = kernel_door._ACTOR.set(None), lookup_door._DONE.set(None)
    yield k
    kernel_door._ACTOR.reset(token)
    lookup_door._DONE.reset(done)
    os_shadow._reset_for_tests()
    user_erase_gate.reset()
    k.close()
    shutil.rmtree(state, ignore_errors=True)


def _asks(door, n=0, timeout=2.0) -> list:
    end = time.monotonic() + timeout
    while time.monotonic() < end and len(door.ops("door_ask")) < n:
        time.sleep(0.01)
    return door.ops("door_ask")


def _never(*a, **k):
    raise AssertionError("the kernel was reached")


# ---- lookups ---------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("value", [None, "off", "shadow"])
def test_with_the_lookups_flag_off_nothing_is_told_and_nobody_is_refused(door, monkeypatch, value):
    """Control runs: with the flag check dropped from ``lookup_door.check`` the stranger is refused; with ``carry`` copying the
    context whatever the flag, the identity assertion fails."""
    monkeypatch.delenv("ZOE_OS_LOOKUPS") if value is None else monkeypatch.setenv("ZOE_OS_LOOKUPS", value)
    monkeypatch.setattr(os_shadow, "record_door", _never)
    os_shadow.roster()
    kernel_door.note_actor(STRANGER)
    for kind in lookup_door.KINDS:
        lookup_door.check(kind)
        lookup_door.sent(kind)
    fn = lambda: None  # noqa: E731
    assert lookup_door.carry(fn) is fn


def test_a_lookup_with_nobody_acting_is_automatic_and_is_not_told(door, monkeypatch):
    """The wall panel's weather card, a night job's fetch. Control run: with ``sent`` recording as the panel when nobody is
    noted the stub raises."""
    monkeypatch.setattr(os_shadow, "record_door", _never)
    assert kernel_door.acting() is None
    lookup_door.sent(lookup_door.WEATHER)


@pytest.mark.asyncio
async def test_a_members_lookup_is_told_once_per_class_per_request_and_never_waits(door, caplog):
    """A search that fans out into many fetches is one ``web_search`` and one ``web_fetch``. The call returns at once even
    with a slow kernel. Control run: with the once-per-request set dropped four asks are made."""
    door.delay = 0.1
    os_shadow.roster()
    kernel_door.note_actor(ANN)
    started = time.monotonic()
    with caplog.at_level(logging.INFO, logger="os_shadow"):
        for kind in (lookup_door.WEB_SEARCH, lookup_door.WEB_FETCH, lookup_door.WEB_FETCH, lookup_door.WEB_SEARCH):
            lookup_door.sent(kind)
        assert time.monotonic() - started < 0.1
        asks = await asyncio.to_thread(_asks, door, 2)
        await asyncio.sleep(0.8)
    assert [(a["area"], a["verb"], a["person"]) for a in door.ops("door_ask")] == [("lookups", "lookup.web_search", ANN),
                                                                                  ("lookups", "lookup.web_fetch", ANN)]
    assert all(set(a) <= {"area", "verb", "person", "visibility", "stamps"} for a in asks)       # who and the class: nothing else
    kernel_door.note_actor(ANN)                                                         # the next request: told again
    lookup_door.sent(lookup_door.WEB_SEARCH)
    assert len(await asyncio.to_thread(_asks, door, 3)) == 3


def test_a_caller_known_to_be_off_the_roster_is_refused_before_anything_is_sent(door, monkeypatch):
    """It would leave the box in their name. Decided from the roster already in memory: no kernel call, no wait. A roster not
    read yet refuses nobody. Control run: with the roster check dropped the stranger's lookup is told and not refused."""
    kernel_door.note_actor(STRANGER)
    lookup_door.check(lookup_door.WEATHER)                                              # roster not read yet: not refused
    os_shadow.roster()
    assert os_shadow._roster_seen() is not None
    kernel_door.note_actor(STRANGER)
    monkeypatch.setattr(os_shadow, "record_door", _never)
    for kind in lookup_door.KINDS:
        with pytest.raises(lookup_door.LookupRefused) as exc:
            lookup_door.check(kind)
        assert exc.value.reason == "unknown_person" and isinstance(exc.value, kernel_door.Refused)


@pytest.mark.asyncio
async def test_a_refused_web_lookup_sends_nothing_and_says_why(door, monkeypatch):
    """The page reader and the search tool answer the refusal's sentence and never reach the browser or a provider.
    Control run: with the door call dropped from ``zoe_agent._web_browse`` the stub browser is reached."""
    import zoe_agent

    os_shadow.roster()
    kernel_door.note_actor(STRANGER)
    monkeypatch.setattr("browser_broker.fetch_page_text", _never)
    monkeypatch.setattr(zoe_agent, "_ddg_search_sync", _never)
    monkeypatch.setattr(zoe_agent, "_cloak_search", _never)
    line = lookup_door.LookupRefused("unknown_person").line()
    assert await zoe_agent._web_browse("https://example.invalid/page", user_id=STRANGER) == line
    assert await zoe_agent._web_search_ddg("words to look up", user_id=STRANGER) == line
    assert await zoe_agent._web_research("words to look up", user_id=STRANGER) == line


def test_the_panel_with_nobody_identified_looks_up_as_the_panel(door):
    """Control run: with ``guest`` sent as a person the assertion fails."""
    os_shadow.roster()
    kernel_door.note_actor("guest")
    lookup_door.sent(lookup_door.WEATHER)
    ask = _asks(door, 1)[0]
    assert ask.get("via") == "panel" and ask["person"] == "panel" and ask["verb"] == "lookup.weather"


#: The ENTRY functions of every lookup: their first statement refuses a caller known to be off the roster, before anything leaves.
ENTRIES = {"routers/weather.py": {"_geocode": "WEATHER", "_fetch_openmeteo_current": "WEATHER", "_fetch_openmeteo_forecast": "WEATHER",
                                  "_fetch_owm_current": "WEATHER", "_fetch_owm_forecast": "WEATHER", "search_locations": "WEATHER"},
           "zoe_agent.py": {"_web_search_ddg": "WEB_SEARCH", "_web_browse": "WEB_FETCH", "_web_research": "WEB_SEARCH"},
           "browser_broker.py": {"search_web": "WEB_SEARCH", "fetch_page_text": "WEB_FETCH", "execute_text_extraction": "WEB_FETCH"},
           "research_evidence.py": {"fetch_web_fallback": "WEB_SEARCH", "fetch_web_fallback_results": "WEB_SEARCH"}}

#: What counts as an outbound transport: a call into one of these (resolved through the module's own imports, aliases
#: included), a page navigation (``.goto``), an ``urlopen``, or a method of a module-level client made from one of them.
TRANSPORT = ("httpx", "requests", "aiohttp", "urllib3", "urllib.request.urlopen", "urllib.request.build_opener",
             "urllib.request.OpenerDirector", "http.client", "socket.create_connection", "socket.socket", "socket.getaddrinfo",
             "asyncio.open_connection", "websockets", "cloakbrowser", "playwright", "ddgs", "pywebpush", "livekit", "aiortc",
             "smtplib", "agent_safety.guarded_urlopen", "web_search_provider.tavily_search_outcome",
             "web_search_provider.tavily_search_sync")
TRANSPORT_METHODS = ("goto", "urlopen")
#: Opening a browser sends nothing (the page's ``goto`` is the request).
OPENERS = ("launch_context_async",)
#: On a transport object opened under a local name, these calls are the requests (an HTTP client's verbs, a search client's).
REQUEST_METHODS = ("get", "post", "put", "patch", "delete", "head", "request", "stream", "send", "text", "news", "images",
                   "videos", "open")

_LOCAL = "a service on the box or the house LAN, by its configured URL (not a lookup)"
_MODEL = "the local model servers on the box (not a lookup)"
_HA = "Home Assistant on the box (the home area's door, not a lookup)"
_MA = "Music Assistant on the box (the music area's door, not a lookup)"
_ENG = "engineering tooling for the operator (not a household lookup)"
_GAP = "UNRECORDED LOOKUP (known gap, reported): a member's place search leaves the box with no lookups record and no class for it"
#: EVERY function under services/zoe-data that makes an outbound call: (module, qualified function) -> (exact count of
#: transport calls in it, what it is). A lookup names its class (the function itself records it, after its first send),
#: ``via ...`` (it sends through a listed lookup that records), or ``mixed`` (``_execute_tool``, pinned separately).
#: A new sender anywhere, a new transport call in a listed function, or a moved one fails until it is placed here.
SENDERS = {
    ("agent_safety.py", "_PinnedHTTPConnection.connect"): (1, "the SSRF-guarded transport itself (its callers are listed)"),
    ("agent_safety.py", "_PinnedHTTPSConnection.connect"): (1, "the SSRF-guarded transport itself (its callers are listed)"),
    ("agent_safety.py", "guarded_urlopen"): (1, "the SSRF-guarded transport itself (its callers are listed)"),
    ("agent_safety.py", "resolve_ips"): (1, "a DNS resolution for the SSRF guard"),
    ("agent_sync.py", "_brain_registers_web_search"): (1, _LOCAL),
    ("auth.py", "_validate_with_auth_service"): (2, _LOCAL),
    ("auth_handoff.py", "_telegram_send_direct"): (1, "the sign-in link to a member's own phone (the messages door asks first)"),
    ("auth_handoff.py", "_telegram_send_via_bot"): (1, "the sign-in link to a member's own phone (the messages door asks first)"),
    ("auth_handoff.py", "_wake_panel"): (1, _LOCAL),
    ("brain_prefix_keeper.py", "read_slot"): (1, _MODEL),
    ("browser_broker.py", "build_cloak_executor._execute"): (2, "WEB_FETCH"),
    ("browser_broker.py", "fetch_page_text"): (2, "WEB_FETCH"),
    ("contact_backfill.py", "_llm_extract_people"): (1, _MODEL),
    ("conversation_notes.py", "_complete._llm_call"): (1, _MODEL),
    ("greploop_guard.py", "_run_cheap_agent"): (1, _ENG),
    ("greptile_client.py", "_mcp_call"): (1, _ENG),
    ("hindsight_memory.py", "HindsightMemoryClient._request"): (1, _LOCAL),
    ("home_control.py", "send"): (1, _HA),
    ("intent_classifier_llm.py", "classify_intent_with_context"): (1, _MODEL),
    ("intent_router.py", "_execute_music_intent_asked"): (4, _MA),
    ("intent_router.py", "_execute_music_intent_asked._log_play_event"): (2, _MA),
    ("intent_router.py", "_list_media_players"): (1, _HA),
    ("intent_router.py", "execute_intent"): (2, _LOCAL),
    ("latent_intent_detector.py", "_complete._llm"): (1, _MODEL),
    ("livekit_aiortc.py", "AiortcRoom.__init__"): (1, "LiveKit on the box (voice)"),
    ("livekit_aiortc.py", "AiortcRoom._ensure_sub_pc"): (1, "LiveKit on the box (voice)"),
    ("livekit_aiortc.py", "AiortcRoom._ensure_sub_pc.on_ice_candidate"): (1, "LiveKit on the box (voice)"),
    ("livekit_aiortc.py", "AiortcRoom._on_offer"): (2, "LiveKit on the box (voice)"),
    ("livekit_aiortc.py", "AiortcRoom._on_trickle"): (1, "LiveKit on the box (voice)"),
    ("livekit_aiortc.py", "AiortcRoom._ping_loop"): (1, "LiveKit on the box (voice)"),
    ("livekit_aiortc.py", "AiortcRoom._signal_loop"): (1, "LiveKit on the box (voice)"),
    ("livekit_aiortc.py", "AiortcRoom.connect"): (1, "LiveKit on the box (voice)"),
    ("ma_ondemand.py", "_http_up"): (1, _MA),
    ("main.py", "_check_brain_ready"): (1, _LOCAL),
    ("main.py", "_check_tts_ready"): (1, _LOCAL),
    ("main.py", "_probe_runtimes._check_port"): (1, _LOCAL),
    ("main.py", "_self_lan_origins"): (1, _LOCAL),
    ("mcp_server.py", "_execute_tool"): (9, "mixed: weather_current / weather_forecast (2, WEATHER), cloakbrowser_fetch / "
                                            "cloakbrowser_screenshot (4, WEB_FETCH), Home Assistant (2), Multica (1)"),
    ("mcp_server.py", "_execute_tool._port_open"): (1, _LOCAL),
    ("mcp_server.py", "_notify_ui"): (1, _LOCAL),
    ("memory_digest.py", "_emotional_call"): (1, _MODEL),
    ("memory_digest.py", "_extract_concept_tags"): (1, _MODEL),
    ("memory_digest.py", "_extract_open_loops"): (1, _MODEL),
    ("memory_digest.py", "_facts_call"): (1, _MODEL),
    ("memory_digest.py", "_is_contradiction"): (1, _MODEL),
    ("memory_digest.py", "_synthesis_pass"): (1, _MODEL),
    ("memory_digest.py", "run_turn_digest._llm"): (1, _MODEL),
    ("memory_retire.py", "ask_judge._llm"): (1, _MODEL),
    ("multica_autopilot_sync.py", "_fire_autopilot_job"): (1, _ENG),
    ("multica_autopilot_sync.py", "_fire_autopilot_job._update_issue_status"): (1, _ENG),
    ("multica_autopilot_sync.py", "close_stale_autopilot_wrappers"): (1, _ENG),
    ("multica_autopilot_sync.py", "get_autopilot_triggers"): (1, _ENG),
    ("multica_autopilot_sync.py", "get_multica_autopilots"): (1, _ENG),
    ("multica_client.py", "MULClient.attach_label"): (1, _ENG),
    ("multica_client.py", "MULClient.create_issue"): (1, _ENG),
    ("multica_client.py", "MULClient.ensure_label"): (1, _ENG),
    ("multica_client.py", "MULClient.ensure_project"): (1, _ENG),
    ("multica_client.py", "MULClient.get_issue"): (1, _ENG),
    ("multica_client.py", "MULClient.list_issues"): (1, _ENG),
    ("multica_client.py", "MULClient.list_labels"): (1, _ENG),
    ("multica_client.py", "MULClient.list_projects"): (1, _ENG),
    ("multica_client.py", "MULClient.update_issue"): (1, _ENG),
    ("multica_client.py", "_lookup_evolution_resources"): (1, _ENG),
    ("multica_client.py", "sync_evolution_proposal_to_multica"): (1, _ENG),
    ("multica_webhook_emitter.py", "emit_event"): (1, _ENG),
    ("music_oauth.py", "_run_flow"): (1, "a music provider's sign-in flow (account setup, not a lookup)"),
    ("music_oauth.py", "_run_setup_flow"): (1, "a music provider's sign-in flow (account setup, not a lookup)"),
    ("music_service.py", "_ma_info"): (1, _MA),
    ("music_service.py", "_ma_response"): (1, _MA),
    ("music_service.py", "_potoken_reachable"): (1, _MA),
    ("night_mind.py", "_complete"): (2, _MODEL),
    ("night_mind.py", "probe_model"): (2, _MODEL),
    ("nlu_extractor.py", "_call_with_tool"): (1, _MODEL),
    ("omi_day_review.py", "_brain_slots_busy"): (1, _MODEL),
    ("omi_day_review.py", "_complete"): (2, _MODEL),
    ("omi_repass_clean.py", "llm_from_url.call"): (2, _MODEL),
    ("omnigent_issue_executor.py", "_api"): (1, _ENG),
    ("os_shadow.py", "_Conn.__init__"): (1, "the Zoe OS kernel's own socket"),
    ("person_extractor_llm.py", "process_text_llm._llm"): (1, _MODEL),
    ("presence/ha_presence.py", "_bridge_fetch"): (1, _HA),
    ("proactive/composer.py", "compose_message"): (1, _MODEL),
    ("research_evidence.py", "_fetch_ddg"): (1, "WEB_SEARCH"),
    ("research_evidence.py", "_fetch_page_price"): (1, "WEB_FETCH"),
    ("research_evidence.py", "_fetch_tavily"): (1, "via web_search_provider.tavily_search_outcome"),
    ("router_two_stage.py", "_post_sidecar"): (1, _MODEL),
    ("router_two_stage.py", "sidecar_healthy"): (1, _MODEL),
    ("routers/auth.py", "logout"): (2, _LOCAL),
    ("routers/geo.py", "_fetch_nominatim"): (1, _GAP),
    ("routers/ha_control.py", "_bridge_get"): (1, _HA),
    ("routers/music.py", "_get_info"): (1, _MA),
    ("routers/music.py", "_get_players"): (1, _MA),
    ("routers/music.py", "_get_providers"): (1, _MA),
    ("routers/music.py", "_get_queue_items"): (1, _MA),
    ("routers/panel_auth.py", "submit_pin"): (1, _LOCAL),
    ("routers/panel_config.py", "_entity_index"): (1, _HA),
    ("routers/push.py", "send_push_to_user"): (1, "Zoe's own push notifications (automatic: never asked, never recorded)"),
    ("routers/system.py", "_proxy_reload_to_pi"): (1, _LOCAL),
    ("routers/system.py", "get_system_status"): (2, _LOCAL),
    ("routers/system.py", "post_display_brightness"): (1, _LOCAL),
    ("routers/system.py", "post_display_volume"): (1, _LOCAL),
    ("routers/voice_livekit.py", "_agent_loop"): (1, "LiveKit on the box (voice)"),
    ("routers/voice_livekit.py", "_collect_audio_stream"): (1, "LiveKit on the box (voice)"),
    ("routers/voice_livekit.py", "_wait_port"): (1, "LiveKit on the box (voice)"),
    ("routers/weather.py", "_fetch_openmeteo_current"): (1, "WEATHER"),
    ("routers/weather.py", "_fetch_openmeteo_forecast"): (1, "WEATHER"),
    ("routers/weather.py", "_fetch_owm_current"): (1, "WEATHER"),
    ("routers/weather.py", "_fetch_owm_forecast"): (1, "WEATHER"),
    ("routers/weather.py", "_geocode"): (1, "WEATHER"),
    ("routers/weather.py", "search_locations"): (2, "WEATHER"),
    ("self_model.py", "_tcp_up"): (1, _LOCAL),
    ("smart_home_service.py", "_ha_get"): (1, _HA),
    ("startup_warmups.py", "_once"): (1, _LOCAL),
    ("structural_reader.py", "_call._llm"): (1, _MODEL),
    ("structural_verifier.py", "judge._llm"): (1, _MODEL),
    ("system_stats.py", "_http_status"): (1, _LOCAL),
    ("system_updates.py", "_dockerhub_digest"): (1, "the image registry, for the update check (a system job: automatic)"),
    ("system_updates.py", "_ghcr_digest"): (1, "the image registry, for the update check (a system job: automatic)"),
    ("telegram_notify.py", "send"): (2, "Zoe's own notifications (automatic: never asked, never recorded)"),
    ("tts_waterfall.py", "_kokoro_http_client"): (2, _MODEL),
    ("tts_waterfall.py", "_synthesize_local_service"): (1, _MODEL),
    ("ui_compose.py", "compose_card"): (1, _MODEL),
    ("user_portrait.py", "_call_llm_for_portrait"): (1, _MODEL),
    ("user_portrait.py", "_tokenize_count"): (1, _MODEL),
    ("web_search_provider.py", "tavily_search_outcome"): (1, "WEB_SEARCH"),
    ("ytmusic_signin.py", "_clear_stale_login"): (1, "YouTube Music sign-in (account setup, not a lookup)"),
    ("ytmusic_signin.py", "_lan_ip"): (1, "YouTube Music sign-in (account setup, not a lookup)"),
    ("ytmusic_signin.py", "_launch_browser"): (2, "YouTube Music sign-in (account setup, not a lookup)"),
    ("ytmusic_signin.py", "_validate_cookie"): (1, "YouTube Music sign-in (account setup, not a lookup)"),
    ("ytmusic_signin.py", "_wait_port"): (1, "YouTube Music sign-in (account setup, not a lookup)"),
    ("zoe_agent.py", "_cloak_search"): (2, "WEB_SEARCH"),
    ("zoe_agent.py", "_context_enhance"): (1, _MODEL),
    ("zoe_agent.py", "_ddg_search_sync"): (2, "WEB_SEARCH"),
    ("zoe_agent.py", "_google_maps_local_search"): (2, "WEB_SEARCH"),
    ("zoe_agent.py", "_llm_call"): (1, _MODEL),
    ("zoe_agent.py", "_web_browse"): (2, "WEB_FETCH"),
    ("zoe_agent.py", "_web_research"): (3, "WEB_SEARCH"),
    ("zoe_agent.py", "_web_research._visit_with_search"): (1, "WEB_FETCH"),
    ("zoe_agent.py", "_web_search_ddg"): (1, "via web_search_provider.tavily_search_sync"),
    ("zoe_agent.py", "run_zoe_agent_streaming"): (1, _MODEL),
    ("zoe_flue_client.py", "_kv_warm_turn"): (1, _MODEL),
    ("zoe_flue_client.py", "_post_guarded_abort"): (1, _MODEL),
    ("zoe_flue_client.py", "_run_flue_brain_streaming_turn"): (2, _MODEL),
    ("zoe_flue_client.py", "_run_turn_aggregated_wire2"): (1, _MODEL),
    ("zoe_ui_components.py", "_geocode"): (1, _GAP),
}


def _outbound(root: Path) -> tuple:
    """Every module under ``root`` but the tests: ({(module, qualified function): transport calls}, {same key: [(line,
    the call)]}, {same key: [(line, the class) of each ``lookup_door.sent``]}). A nested function is its own entry."""
    import collections

    counts, where, sent = collections.Counter(), collections.defaultdict(list), collections.defaultdict(list)
    requests, opened = collections.defaultdict(list), collections.defaultdict(set)
    binders: set = set()
    for f in sorted(root.rglob("*.py")):
        rel = f.relative_to(root).as_posix()
        if rel.startswith("tests/"):
            continue
        tree = ast.parse(f.read_text("utf-8"))
        names: dict = {}
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    names[a.asname or a.name.split(".")[0]] = a.name if a.asname else a.name.split(".")[0]
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                for a in n.names:
                    names[a.asname or a.name] = f"{n.module}.{a.name}"

        def dotted(node):
            parts = []
            while isinstance(node, ast.Attribute):
                parts.append(node.attr)
                node = node.value
            head = names.get(node.id) if isinstance(node, ast.Name) else None
            return ".".join([head, *parts[::-1]]) if head else None

        def transport(d) -> bool:
            return bool(d) and any(d == t or d.startswith(t + ".") for t in TRANSPORT)

        clients = {t.id for n in tree.body if isinstance(n, (ast.Assign, ast.AnnAssign)) and isinstance(n.value, ast.Call)
                   and transport(dotted(n.value.func)) for t in (n.targets if isinstance(n, ast.Assign) else [n.target])
                   if isinstance(t, ast.Name)}

        def visit(node, qual):
            for ch in ast.iter_child_nodes(node):
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    visit(ch, ch.name if qual == "<module>" else f"{qual}.{ch.name}")
                    continue
                # a transport CLIENT opened under a local name (``with httpx.AsyncClient() as client``, ``c = DDGS()``): opening it
                # sends nothing; each REQUEST_METHODS call on that name is a request of its own. A transport FUNCTION bound the
                # same way (``with guarded_urlopen(url) as resp``) is itself the request.
                bound = [(i.context_expr, i.optional_vars) for i in ch.items] if isinstance(ch, (ast.With, ast.AsyncWith)) else \
                    [(ch.value, ch.targets[0])] if isinstance(ch, ast.Assign) and len(ch.targets) == 1 else []
                for value, target in bound:
                    value = value.value if isinstance(value, ast.Await) else value
                    if isinstance(value, ast.Call) and isinstance(target, ast.Name) and transport(dotted(value.func)) \
                            and ast.unparse(value.func).rsplit(".", 1)[-1][:1].isupper():      # a client CLASS; a function sends
                        opened[(rel, qual)].add(target.id)
                        binders.add(id(value))
                if isinstance(ch, ast.Call):
                    fn = ch.func
                    if transport(dotted(fn)) or (isinstance(fn, ast.Attribute) and fn.attr in TRANSPORT_METHODS) or \
                            (isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id in clients):
                        counts[(rel, qual)] += 1
                        where[(rel, qual)].append((ch.lineno, ast.unparse(fn)))
                        if id(ch) not in binders and not ast.unparse(fn).endswith(OPENERS):
                            requests[(rel, qual)].append((ch.lineno, ast.unparse(ch)))
                    elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) and fn.value.id in opened[(rel, qual)] \
                            and fn.attr in REQUEST_METHODS:
                        requests[(rel, qual)].append((ch.lineno, ast.unparse(ch)))
                    if ast.unparse(fn) == "lookup_door.sent" and ch.args:
                        sent[(rel, qual)].append((ch.lineno, ast.unparse(ch.args[0]).rsplit(".", 1)[-1]))
                visit(ch, qual)

        visit(tree, "<module>")
    _outbound.requests = {k: sorted(v) for k, v in requests.items()}
    return dict(counts), where, sent


def _unrecorded(requests: list, records: list) -> list:
    """The requests with no record of their OWN: none on a line after the request and before the next request."""
    lines = [line for line, _ in requests] + [10 ** 9]
    return [requests[i] for i in range(len(requests)) if not any(lines[i] < r < lines[i + 1] for r in records)]


#: Entry points that are routes whose caller nothing upstream notes (a bare ``get_current_user``): they note it themselves, FIRST.
NOTES_ITS_CALLER = {("routers/weather.py", "search_locations")}


def test_every_lookup_entry_point_checks_before_anything_leaves():
    """Each entry function's FIRST statement is ``lookup_door.check`` with its class (the off-roster refusal, which records
    nothing); a route in ``NOTES_ITS_CALLER`` notes its caller first and checks second. The plan executor
    (``browser_broker.build_cloak_executor._execute``, a nested function) is held to the same rule. Controls run: the check
    dropped from ``browser_broker.search_web`` fails; dropped from ``execute_text_extraction`` fails; dropped from the plan
    executor's ``_execute`` fails; ``_note_actor`` dropped from ``routers/weather.search_locations`` fails."""
    broker = ast.parse((SVC / "browser_broker.py").read_text("utf-8"))
    (build,) = [n for n in broker.body if isinstance(n, ast.FunctionDef) and n.name == "build_cloak_executor"]
    (execute,) = [n for n in ast.walk(build) if isinstance(n, ast.AsyncFunctionDef) and n.name == "_execute"]
    assert ast.unparse(execute.body[0]) == "lookup_door.check(lookup_door.WEB_FETCH)"
    for rel, wanted in ENTRIES.items():
        tree = ast.parse((SVC / rel).read_text("utf-8"))
        for fn in tree.body:
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and fn.name in wanted:
                body = fn.body[1:] if isinstance(fn.body[0], ast.Expr) and isinstance(getattr(fn.body[0], "value", None), ast.Constant) else fn.body
                if (rel, fn.name) in NOTES_ITS_CALLER:      # a route nothing upstream notes the caller of: noted first, then checked
                    assert ast.unparse(body[0]) == "panel_principal._note_actor(user)", (rel, fn.name)
                    body = body[1:]
                assert f"lookup_door.check(lookup_door.{wanted[fn.name]})" in ast.unparse(body[0]), (rel, fn.name)
                wanted = {k: v for k, v in wanted.items() if k != fn.name}
        assert not wanted, (rel, wanted)


def test_every_outbound_sender_in_zoe_data_is_listed_and_every_lookup_records_after_it_sends():
    """The scan covers EVERY module under services/zoe-data (tests excepted): every function with an outbound transport
    call is listed with its exact count, so a new sender in any module (one taking its URL as an argument too, or one
    reached through an import alias) fails until it is listed, and a listed lookup must RECORD its class after its first
    send and never before. ``via`` lookups send only through a listed lookup that records.
    Controls run (planted, each failed the test): a ``def _ping(url): return httpx.get(url)`` added to
    ``reminder_delivery.py``; a second ``httpx.AsyncClient`` in ``routers/weather._geocode`` (count 2 against 1);
    ``from httpx import AsyncClient as _C`` with a ``_C()`` call in ``change_notices.py``; the record in
    ``research_evidence._fetch_page_price`` dropped; the record in ``zoe_agent._web_research._visit_with_search`` dropped;
    ``routers/weather._geocode``'s record moved above its request; the Open-Meteo record dropped from
    ``routers/weather.search_locations`` (the fallback branch: the OpenWeatherMap branch's record used to hide it); the second
    record dropped from ``zoe_agent._web_research``; the ``weather_forecast`` tool's record dropped from
    ``mcp_server._execute_tool``; ``OPENERS`` emptied (a browser launch counted as a request) fails every page lookup."""
    counts, where, sent = _outbound(SVC)
    assert counts == {k: n for k, (n, _) in SENDERS.items()}, (
        sorted(set(counts.items()) ^ {(k, n) for k, (n, _) in SENDERS.items()}))
    kinds = set(lookup_door.KINDS) | {"WEATHER", "WEB_SEARCH", "WEB_FETCH"}
    for key, (_, what) in SENDERS.items():
        if what in kinds:
            first_send = min(line for line, _ in where[key])
            records = sent.get(key, [])
            assert records and all(kind == what for _, kind in records), (key, records)
            assert min(line for line, _ in records) > first_send, (key, "recorded before it sent")
            # EVERY request has a record of its own, after it and before the next request: a fallback branch that sends
            # without recording is not hidden by the first branch's record
            reqs = _outbound.requests.get(key, [])
            assert reqs, (key, "no request found: the scan cannot see how this lookup sends")
            assert not _unrecorded(reqs, [line for line, _ in records]), (key, _unrecorded(reqs, [line for line, _ in records]))
        elif what.startswith("via "):
            mod, fn = what[4:].rsplit(".", 1)
            calls = [c for _, c in where[key]]
            assert calls and all(c.endswith(fn) for c in calls), (key, calls)
            assert not sent.get(key), key
        else:
            assert not sent.get(key) or key == ("mcp_server.py", "_execute_tool"), key
    tool = sent[("mcp_server.py", "_execute_tool")]
    assert sorted(kind for _, kind in tool) == ["WEATHER", "WEATHER", "WEB_FETCH", "WEB_FETCH"]
    # the dispatcher's four lookup requests (two to the weather service, two page loads), each with its own record before
    # the dispatcher's next request of any kind
    every_request = _outbound.requests[("mcp_server.py", "_execute_tool")]
    lookups = [r for r in every_request if "open-meteo" in r[1] or ".goto(" in r[1]]
    assert len(lookups) == 4, lookups
    assert not [r for r in _unrecorded(every_request, [line for line, _ in tool]) if r in lookups]
    tools = (SVC / "mcp_server.py").read_text("utf-8")
    assert tools.count("lookup_door.check(lookup_door.WEATHER)") == 2 and tools.count("lookup_door.check(lookup_door.WEB_FETCH)") == 2
    assert tools.count("api.open-meteo.com") == 2
    every = {k for k in counts} | {k for k in sent}
    assert {k for k in sent} <= {k for k, (_, w) in SENDERS.items() if w in kinds or k == ("mcp_server.py", "_execute_tool")}, every


class _Resp:
    """An answered request (urllib's shape): one body, then end of stream."""

    status, headers = 200, {}

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, n: int = -1) -> bytes:
        body, self._body = self._body, b""
        return body

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> bool:
        return False


def _trail(monkeypatch) -> list:
    """What went out and what the kernel was told, in order (``("told", verb, who)`` / ``("sent", what)``)."""
    trail: list = []
    monkeypatch.setattr(os_shadow, "record_door", lambda area, verb, who, **kw: trail.append(("told", verb, who)))
    return trail


def _opener(trail):
    def fake_open(url, **kw):
        trail.append(("sent", "search" if "duckduckgo" in url else "page"))
        return _Resp(b"<html><body>womble boots $12.00</body></html>")
    return fake_open


@pytest.mark.asyncio
async def test_a_lookup_that_never_left_the_box_is_not_recorded(door, monkeypatch):
    """An empty query, the provider switched off, a fresh weather cache, a too-short place search and a private page are
    all answered with nothing sent, and nothing is told. Control run: with ``lookup_door.check`` recording as the old
    entry-point ``sent`` did, each of them is told."""
    import research_evidence
    import zoe_agent
    from routers import weather

    trail = _trail(monkeypatch)
    os_shadow.roster()
    kernel_door.note_actor(ANN)
    monkeypatch.setattr(research_evidence, "guarded_urlopen", _never)
    monkeypatch.setattr(weather.httpx, "AsyncClient", _never)
    assert research_evidence.fetch_web_fallback("  ").results == [] and research_evidence.fetch_web_fallback_results("") == []
    monkeypatch.setenv("ZOE_WEB_FALLBACK_PROVIDER", "off")
    assert research_evidence.fetch_web_fallback("womble boots").status == research_evidence.WEB_LOOKUP_OFF
    weather._cache_put("current", 1.0, 2.0, {"_hourly_raw": {"times": []}, "_daily_raw": {}})
    await weather._fetch_openmeteo_forecast(1.0, 2.0)
    assert await weather.search_locations(query="w", limit=8, user={}) == {"results": []}
    assert "Refused" in await zoe_agent._web_browse("http://127.0.0.1/womble", user_id=ANN)
    assert trail == []


def test_a_search_and_its_price_fetch_are_each_recorded_after_they_are_sent(door, monkeypatch):
    """The search is ``web_search`` once its request was answered; the price enrichment of its rows (a fetch of each
    result page) is ``web_fetch``. Controls run: with the record dropped from ``research_evidence._fetch_ddg`` the first
    assertion fails; with the record dropped from ``_fetch_page_price`` the second fails; with the record moved above the
    request in ``_fetch_ddg`` the order fails."""
    import research_evidence

    trail = _trail(monkeypatch)
    kernel_door.note_actor(ANN)
    monkeypatch.setattr(research_evidence, "guarded_urlopen", _opener(trail))
    research_evidence._fetch_ddg("womble boots", 3, 2.0, enrich_prices=False)
    assert trail == [("sent", "search"), ("told", "lookup.web_search", ANN)]
    rows = [{"title": "womble boots", "url": "https://shop.example.invalid/womble-boots", "snippet": "womble boots on sale"}]
    research_evidence._verify_rows("womble boots", rows, 2.0, enrich_prices=True)
    assert trail[2:] == [("sent", "page"), ("told", "lookup.web_fetch", ANN)]


@pytest.mark.asyncio
async def test_a_lookup_run_on_an_executor_thread_is_recorded_for_who_asked_after_it_was_sent(door, monkeypatch):
    """``run_in_executor`` does not copy the context: without ``carry`` the thread would not know who is asking and the
    search would go unrecorded. The broker's search and the brain's ``web_search`` (its ddgs tier). Controls run: with
    ``carry`` returning the bare function the broker search is not told; with the record dropped from
    ``zoe_agent._ddg_search_sync`` the ddgs search is not told."""
    import browser_broker
    import research_evidence
    import web_search_provider
    import zoe_agent

    trail = _trail(monkeypatch)
    kernel_door.note_actor(ANN)
    monkeypatch.setattr(research_evidence, "guarded_urlopen", _opener(trail))
    monkeypatch.setenv("ZOE_WEB_FALLBACK_PROVIDER", "duckduckgo")
    await browser_broker.search_web("womble boots", timeout_s=2.0)
    assert trail[:2] == [("sent", "search"), ("told", "lookup.web_search", ANN)]

    class DDGS:
        def __init__(self, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def text(self, query, max_results):
            trail.append(("sent", "search"))
            return [{"title": "womble", "href": "https://example.invalid/womble", "body": "a womble"}]

    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=DDGS))
    monkeypatch.setattr(web_search_provider, "tavily_enabled", lambda: False)
    del trail[:]
    kernel_door.note_actor(ANN)                                                         # the next request
    assert "womble" in await zoe_agent._web_search_ddg("womble", user_id=ANN)
    assert trail == [("sent", "search"), ("told", "lookup.web_search", ANN)]


# ---- messages --------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_with_the_messages_flag_off_nothing_is_asked(door, monkeypatch):
    """Control run: with the flag check dropped from ``message_door.ask`` the stub raises."""
    monkeypatch.setenv("ZOE_OS_MESSAGES", "off")
    monkeypatch.setattr(os_shadow, "ask_door", _never)
    assert message_door.stamps_now() is None
    for channel in (message_door.PANEL, message_door.TELEGRAM, message_door.EXTERNAL):
        assert await message_door.ask(channel, ANN, recipient=ANN, stamps_at=None) is None
    message_door.sent(None, True)


@pytest.mark.asyncio
async def test_a_send_asks_first_with_the_channel_and_member_ids_only_and_reports_how_it_ended(door):
    """Control run: with the recipient sent as the acting person the assertion fails."""
    stamps = message_door.stamps_now()
    assert stamps is not None
    asked = await message_door.ask(message_door.TELEGRAM, ANN, recipient=ADULT, stamps_at=stamps)
    panel = await message_door.ask(message_door.PANEL, "guest", stamps_at=stamps)
    message_door.sent(asked, True)
    message_door.sent(panel, False)
    a, p = _asks(door, 2)
    assert (a["area"], a["verb"], a["person"], a["owner"]) == ("messages", "send.telegram", ANN, ADULT)
    assert p["verb"] == "send.panel" and p.get("via") == "panel" and "owner" not in p
    assert all(set(x) <= {"area", "verb", "person", "owner", "visibility", "stamps", "via"} for x in (a, p))
    end = time.monotonic() + 2
    while time.monotonic() < end and len(door.ops("door_report")) < 2:
        await asyncio.sleep(0.02)
    assert [r["outcome"] for r in door.ops("door_report")] == ["done", "failed"]


@pytest.mark.parametrize("reason", ["adults_only", "not_owner", "unknown_person"])
@pytest.mark.asyncio
async def test_a_refused_send_raises_with_a_plain_sentence(door, reason):
    """Control run: with the refusal not raised in ``message_door.ask`` this returns a decision."""
    door.door = f"refuse:{reason}"
    with pytest.raises(message_door.MessageRefused) as exc:
        await message_door.ask(message_door.TELEGRAM, ANN, recipient=ANN, stamps_at=message_door.stamps_now())
    assert exc.value.reason == reason and exc.value.line() != message_door.MessageRefused.fallback


@pytest.mark.parametrize("how", ["slow", "gone", "old_kernel"])
@pytest.mark.asyncio
async def test_a_kernel_that_cannot_answer_lets_a_household_send_go_and_never_an_outside_address(door, how):
    """The one place "kernel down" means NO for a message: an address that is not a known contact or channel of the
    household. Control run: with the ``EXTERNAL`` rule dropped the outside send proceeds ``not_checked``."""
    stamps = message_door.stamps_now()
    if how != "gone":       # (asked and allowed: sent. Not before "gone": an open connection outlives the listener)
        assert (await message_door.ask(message_door.EXTERNAL, ANN, stamps_at=stamps)).decision == "allow"
    if how == "slow":
        door.delay = 0.8
    elif how == "gone":
        door.close()
    else:
        door.door = "old_kernel"
    for channel in (message_door.PANEL, message_door.TELEGRAM):
        assert (await message_door.ask(channel, ANN, recipient=ANN, stamps_at=stamps)).decision == "not_checked"
    with pytest.raises(message_door.MessageRefused) as exc:
        await message_door.ask(message_door.EXTERNAL, ANN, stamps_at=stamps)
    assert exc.value.reason == "unavailable"


async def test_the_intercom_asks_before_it_queues_and_a_refusal_queues_nothing(door, house):  # noqa: F811
    """A member's words on another panel. Control run: with the ask dropped from ``intercom.handle`` the refused
    announcement is queued."""
    res = await _say("tell the kitchen dinner's ready")
    assert res is not None and res.reply == "Okay, I've told the kitchen." and len(await _rows(house)) == 1
    ask = _asks(door, 1)[0]
    assert (ask["verb"], ask["person"]) == ("send.panel", ADULT) and "owner" not in ask
    assert "dinner" not in json.dumps(door.calls, default=str)
    door.door = "refuse:adults_only"
    res = await _say("tell the kitchen the bus is here")
    assert res is not None and res.reply == message_door.MessageRefused("adults_only").line()
    assert len(await _rows(house)) == 1                                                 # nothing more was queued


async def test_the_sign_in_link_asks_before_it_is_sent_to_the_members_own_phone(door, hdb, monkeypatch):  # noqa: F811
    """Control run: with the ask dropped from ``auth_handoff.send_to_phone`` the refused link is sent."""
    monkeypatch.setenv("ZOE_TELEGRAM_BOT_TOKEN", "123:abc")
    _link_telegram(hdb.path)
    calls = []

    async def fake_send(chat_id, text, url):
        calls.append(chat_id)
        return True

    monkeypatch.setattr(auth_handoff, "_telegram_send", fake_send)
    h = await _start()
    assert (await auth_handoff.send_to_phone(h["id"]))["status"] == "sent" and len(calls) == 1
    ask = _asks(door, 1)[0]
    assert (ask["verb"], ask["person"], ask["owner"]) == ("send.telegram", "jason", "jason")
    assert calls[0] not in json.dumps(door.calls, default=str)                          # the chat id never crosses
    door.door = "refuse:adults_only"
    h2 = await _start()
    r = await auth_handoff.send_to_phone(h2["id"])
    assert r == {"ok": False, "status": "unavailable", "detail": message_door.MessageRefused("adults_only").line()} and len(calls) == 1


def test_zoes_own_notifications_never_reach_the_door_and_the_member_senders_do():
    """Automatic sends (reminders, change notices, the day review, proactive nudges) go through ``telegram_notify.send`` and
    ``routers/push.send_push_to_user``; neither they nor their callers name the door. The three member-driven senders do, and
    in each the ask comes before the send. Control run: a ``message_door.ask`` planted in ``reminder_delivery`` fails this."""
    for rel in ("telegram_notify.py", "routers/push.py", "reminder_delivery.py", "change_notices.py", "day_review_raise.py",
                "omi_review_digest.py", "proactive/engine.py", "voice_announce.py"):
        assert "message_door" not in (SVC / rel).read_text("utf-8"), rel
    for rel, fn_name, send in (("intercom.py", "handle", "enqueue_announcement"), ("auth_handoff.py", "send_to_phone", "_telegram_send"),
                               ("mcp_server.py", "_execute_tool", "action_type=\"panel_announce\"")):
        src = (SVC / rel).read_text("utf-8")
        fn = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.AsyncFunctionDef) and n.name == fn_name)
        text = "\n".join(src.split("\n")[fn.lineno - 1:fn.end_lineno])
        assert 0 < text.index("message_door.ask(") < text.index(send), rel
    assert tuple(os_shadow.AREAS["messages"].verbs) == (message_door.PANEL, message_door.TELEGRAM, message_door.PUSH, message_door.EXTERNAL)
    assert tuple(os_shadow.AREAS["lookups"].verbs) == tuple(f"lookup.{k}" for k in lookup_door.KINDS)


# ---- who a tool call acts for: that call's own caller, and nobody afterwards -------------------------------------------------------
KID = "member-kip"


def _noting(monkeypatch) -> list:
    """Every lookup check records who is acting at that moment and refuses (so nothing is sent and no kernel is needed)."""
    seen: list = []

    def check(kind):
        seen.append((kernel_door.acting() or (None,))[0])
        raise lookup_door.LookupRefused("unknown_person")

    monkeypatch.setattr(lookup_door, "check", check)
    return seen


@pytest.mark.asyncio
async def test_each_tool_call_in_one_task_acts_for_its_own_caller_and_leaves_nobody_noted(door, monkeypatch):
    """The stdio tool server answers call after call in ONE task. Each call that states its caller acts for THAT member at the
    doors (lookups, music, home control), and when it returns nobody is noted: an adult's call followed by a child's must reach
    the kernel as the child, never as the adult before them. A call with no context (the service's own dispatch, which noted the
    turn's member) keeps that member, and it is still noted afterwards. A call that raises puts the actor back too.

    Controls run: ``@_actor_for_this_call_only`` removed from ``mcp_server._execute_tool`` -> the adult is still noted after the
    first call (and the child's call would act as the adult under the old ``is None`` rule); the rule put back to
    ``if kernel_door.acting() is None`` alone -> a stated caller no longer replaces a member already noted; ``actor_scope``
    not putting the actor back (its ``finally`` dropped) -> noted after the call."""
    import mcp_server

    seen = _noting(monkeypatch)
    assert kernel_door.acting() is None
    for who in (ANN, KID, ANN):
        out = await mcp_server._execute_tool(None, "weather_current", {}, {"user_id": who, "source": "transport"})
        assert out["status"] == "refused" and kernel_door.acting() is None, who
    assert seen == [ANN, KID, ANN]
    kernel_door.note_actor(ANN)                                                         # something left noted in this task ...
    await mcp_server._execute_tool(None, "weather_current", {}, {"user_id": KID, "source": "transport"})
    assert seen[-1] == KID and kernel_door.acting()[0] == ANN                           # ... never decides a stated caller's call
    kernel_door.note_actor(KID)                                                         # the service's dispatch noted the turn's member
    await mcp_server._execute_tool(None, "weather_current", {"_user_id": ANN})          # no context: the turn's member stands
    assert seen[-1] == KID and kernel_door.acting()[0] == KID
    kernel_door._ACTOR.set(None)
    monkeypatch.setattr(lookup_door, "check", lambda kind: (_ for _ in ()).throw(RuntimeError("womble")))
    with pytest.raises(RuntimeError):
        await mcp_server._execute_tool(None, "weather_current", {}, {"user_id": ANN, "source": "transport"})
    assert kernel_door.acting() is None


@pytest.mark.asyncio
async def test_the_plan_pipeline_checks_before_a_page_is_fetched(door, monkeypatch):
    """``execute_text_extraction`` (the broker's plan / execute pipeline) refuses a caller known to be off the roster before a
    plan is even made; it used to plan and navigate with no check. Control run: the check dropped from
    ``browser_broker.execute_text_extraction`` -> the broker is reached. (The executor's own first statement is pinned in
    ``test_every_lookup_entry_point_checks_before_anything_leaves``.)"""
    import browser_broker

    class Broker:
        def plan_action(self, **kw):
            raise AssertionError("a plan was made for a refused caller")

    seen = _noting(monkeypatch)
    kernel_door.note_actor(STRANGER)
    with pytest.raises(lookup_door.LookupRefused):
        await browser_broker.execute_text_extraction(Broker(), url="https://example.invalid/page", user_id=STRANGER, session_id="s")
    assert seen == [STRANGER]


@pytest.mark.asyncio
async def test_a_place_search_is_its_signed_in_callers_lookup(door, monkeypatch):
    """``GET /api/weather/location/search`` takes the signed-in caller and nothing upstream notes them: the route notes them
    itself, first, so the lookup is theirs (refused for someone off the roster, recorded for a member) and not automatic.
    Control run: ``panel_principal._note_actor(user)`` dropped from ``routers/weather.search_locations`` -> nobody is acting
    at the check."""
    from routers import weather

    seen = _noting(monkeypatch)
    with pytest.raises(lookup_door.LookupRefused):
        await weather.search_locations(query="womble town", limit=3, user={"user_id": STRANGER})
    assert seen == [STRANGER]
