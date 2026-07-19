"""
Chat proxy router: bridges the Zoe UI (REST+SSE) to the active agent backend.

Tiered architecture (Jetson + Pi):
- Tier 0: Intent router — regex-matched commands (lists, calendar, HA control)
  handled directly in <5ms without any LLM.
- Tier 1: Zoe Agent — Gemma 4 E4B-QAT with MemPalace memory, HA control,
  bash tools, and Hermes escalation. True SSE streaming, first token fast.
  Active when JETSON_AGENT_MODE=true OR HERMES_FAST_PATH=false.
  Pi: CPU, 7 TPS, port 11434.  Jetson: GPU, 40+ TPS, port 11434.
- Tier 2: Hermes — reasoning/review/development repair plus browser work through Zoe CloakBrowser tools.
  Activated via escalation from Tier 1.
  Also used as direct path when Zoe Agent is bypassed.
"""
import asyncio
import concurrent.futures
import json
import logging
import pathlib
import re
import subprocess
import time
import uuid
import os
from collections import OrderedDict
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Request, Depends
from fastapi.responses import StreamingResponse
from intent_router import detect_intent, detect_and_extract_intent, execute_intent, openclaw_user_message, Intent
from browser_broker import create_default_browser_broker
from conversation_context import ConversationContext as _CC

# Bounded via _bounded_lru_set below: these module-level maps are keyed by
# session_id with no other eviction (sessions are pruned WITHIN a session but
# never removed), so without a cap they grow for the life of the process.
_CHAT_CONTEXTS: "OrderedDict[str, _CC]" = OrderedDict()
_MAX_CHAT_CONTEXT_SESSIONS = int(os.environ.get("ZOE_CHAT_CONTEXT_MAX_SESSIONS", "2000"))


def _bounded_lru_set(store: "OrderedDict[str, object]", key: str, value: object, *, max_size: int) -> None:
    """Insert/update key in an OrderedDict-backed store, evicting the
    least-recently-touched entries once over max_size.

    Keeps long-lived in-memory session maps (chat context, frustration
    tracker) from growing unbounded across the process lifetime.
    """
    store[key] = value
    store.move_to_end(key)
    while len(store) > max_size:
        store.popitem(last=False)

# Intent → touch panel navigation map (page + optional form to open).
# Panel navigation targets are the ESTATE surfaces (home.html?domain=… opens
# the matching in-estate screen) — the per-domain legacy pages (calendar.html,
# lists.html, weather.html, cooking.html, …) are retired panel chrome and kept
# only for desktop/reference (operator report 2026-07-13: "certain links or
# buttons take me back to the old interfaces"). notes/journal/recipes have no
# estate surface yet → no navigation (reply renders as chat/toast).
_INTENT_PANEL_NAV = {
    "calendar_create":   ("/touch/home.html?domain=calendar", "new_event"),
    "calendar_show":     ("/touch/home.html?domain=calendar", None),
    "note_create":       (None,                   None),
    "journal_create":    (None,                   None),
    "weather":           ("/touch/home.html?domain=weather",  None),
    "list_add":          ("/touch/home.html?domain=lists",    "new_list_item"),
    "list_show":         ("/touch/home.html?domain=lists",    None),
    "timer_create":      ("/touch/home.html?domain=timers",   "new_timer"),
    "recipe_search":     (None,                   None),
    "reminder_create":   (None,                   None),  # handled as toast
    "lets_talk":         ("/touch/voice.html?conv=1", None),  # phone-call voice mode (still its own surface)
}

# Intents that show a full-screen interactive action-form overlay on the touch panel
# instead of navigating to a detail page. The overlay appears in-place and allows
# voice + touch editing before a Confirm/Cancel decision.
_ACTION_FORM_INTENTS: frozenset[str] = frozenset({
    "calendar_create",
    "list_add",
    "list_show",
    "timer_create",
})


def _normalized_list_items(slots: dict) -> list[str]:
    try:
        from card_service import list_items

        return list_items(slots)
    except ImportError as exc:
        logging.getLogger(__name__).debug("list item normalizer import failed: %s", exc)
        raw_items = slots.get("items")
        if isinstance(raw_items, list):
            candidates = raw_items
        elif raw_items:
            candidates = [raw_items]
        elif slots.get("item") or slots.get("text"):
            candidates = [slots.get("item") or slots.get("text")]
        else:
            candidates = []
        return [item for item in (str(value or "").strip() for value in candidates) if item]


def _intent_card_data(intent) -> dict:
    """Build show_card data payload from intent slots for Google Home-style card."""
    slots = intent.slots or {}
    name = intent.name
    if name == "calendar_create":
        payload = {
            "type": "calendar",
            "data": {
                "action": "Event added",
                "title": slots.get("title") or slots.get("event") or "",
                "date": slots.get("date") or "",
                "time": slots.get("time") or "",
            },
        }
        try:
            from card_service import card_service

            payload["card"] = card_service.build_calendar_event_editor_card(slots)
        except Exception as exc:
            logger.debug("calendar_create card contract build failed: %s", exc)
        return payload
    if name == "calendar_show":
        payload = {
            "type": "calendar",
            "data": {
                "action": "Showing calendar",
                "qualifier": slots.get("qualifier") or "today",
            },
        }
        try:
            from card_service import card_service

            payload["card"] = card_service.build_calendar_timeline_card(slots)
        except Exception as exc:
            logger.debug("calendar_show card contract build failed: %s", exc)
        return payload
    if name == "list_add":
        items = _normalized_list_items(slots)
        item = items[0] if items else ""
        list_name = slots.get("list_name") or slots.get("list_type") or "Shopping"
        payload = {
            "type": "list",
            "data": {
                "list_name": list_name,
                "item": item,
            },
        }
        try:
            from card_service import card_service

            payload["card"] = card_service.build_shopping_item_editor_card(
                {**slots, "list_name": list_name, "item": item}
            )
        except Exception as exc:
            logger.debug("list_add card contract build failed: %s", exc)
        return payload
    if name == "list_show":
        items = _normalized_list_items(slots)
        list_name = slots.get("list_name") or slots.get("list_type") or "Shopping"
        payload = {
            "type": "list",
            "data": {
                "list_name": list_name,
                "items": items,
            },
        }
        try:
            from card_service import card_service

            payload["card"] = card_service.build_shopping_list_card(
                {**slots, "list_name": list_name, "items": items}
            )
        except Exception as exc:
            logger.debug("list_show card contract build failed: %s", exc)
        return payload
    if name == "timer_create":
        return {
            "type": "timer",
            "data": {
                "minutes": slots.get("minutes") or slots.get("duration") or "",
                "label": slots.get("label") or "",
            },
        }
    if name == "weather":
        return {
            "type": "weather",
            "data": {"summary": "Fetching weather…"},
        }
    # Generic answer card for all other navigation intents
    return {
        "type": "answer",
        "data": {"text": ""},
    }


def _intent_action_form_payload(intent, panel_id: str | None = None) -> dict | None:
    """Build a panel_show_action_form payload for intents that show an in-place overlay.

    Returns None for intents that don't have a form template.
    """
    import datetime
    from intent_router import _parse_date, _parse_time

    slots = intent.slots or {}
    name = intent.name

    if name == "calendar_create":
        date_raw = slots.get("date", "")
        time_raw = slots.get("time", "")
        parsed_date = (_parse_date(date_raw) if date_raw else None) or datetime.date.today().isoformat()
        parsed_time = (_parse_time(time_raw) if time_raw else "") or ""
        return {
            "panel_type": "calendar_event",
            "title": "New Calendar Event",
            "data": {
                "title": slots.get("title") or slots.get("event") or "",
                "date": parsed_date,
                "time": parsed_time,
                "duration": slots.get("duration") or "",
                "category": slots.get("category") or "general",
                "location": slots.get("location") or "",
                "notes": slots.get("notes") or "",
            },
            **({"panel_id": panel_id} if panel_id else {}),
        }

    if name in ("list_add", "list_show"):
        items = _normalized_list_items(slots)
        item = items[0] if items else ""
        return {
            "panel_type": "shopping_list",
            "title": f"{slots.get('list_name') or slots.get('list_type') or 'Shopping'} List",
            "data": {
                "list_name": slots.get("list_name") or slots.get("list_type") or "Shopping",
                "items": items,
                "item": item,
            },
            **({"panel_id": panel_id} if panel_id else {}),
        }

    if name == "timer_create":
        return {
            "panel_type": "timer",
            "title": "New Timer",
            "data": {
                "minutes": slots.get("minutes") or slots.get("duration") or 5,
                "label": slots.get("label") or "Timer",
            },
            **({"panel_id": panel_id} if panel_id else {}),
        }

    return None


async def _broadcast_intent_nav(intent, panel_id: str | None = None) -> None:
    """Broadcast UI actions to the touch panel when an intent is detected.

    For action-form intents (calendar_create, list_add/show, timer_create), a full-screen
    interactive form overlay is shown instead of navigating to a detail page.
    For all other intents, the classic panel_navigate + panel_open_form flow runs.
    A show_card is always emitted so the dashboard overlay shows intent-specific info.

    panel_id is embedded in action payloads so the executor can filter events
    belonging to a different panel (multi-panel homes, web chat alongside touch).
    """
    nav = _INTENT_PANEL_NAV.get(intent.name)
    if not nav:
        return
    page, form = nav
    try:
        from push import broadcaster

        # ── Action-form intents: show a full-screen interactive overlay ─────
        if intent.name in _ACTION_FORM_INTENTS:
            form_payload = _intent_action_form_payload(intent, panel_id=panel_id)
            if form_payload:
                await broadcaster.broadcast("all", "ui_action", {
                    "action": {
                        "id": f"intent_action_form_{intent.name}",
                        "action_type": "panel_show_action_form",
                        "payload": form_payload,
                    }
                })
                # Also emit show_card for the dashboard bar.
                card = _intent_card_data(intent)
                card_payload: dict = {"type": card["type"], "data": card["data"]}
                if card.get("card"):
                    card_payload["card"] = card["card"]
                if panel_id:
                    card_payload["panel_id"] = panel_id
                await broadcaster.broadcast("all", "ui_action", {
                    "action": {
                        "id": f"intent_card_{intent.name}",
                        "action_type": "show_card",
                        "payload": card_payload,
                    }
                })
                return

        # ── Default: navigate to page + open form + show card ────────────────
        if page:
            nav_payload: dict = {"url": page, "label": f"Opening {page.split('/')[-1]}"}
            if panel_id:
                nav_payload["panel_id"] = panel_id
            await broadcaster.broadcast("all", "ui_action", {
                "action": {
                    "id": f"intent_nav_{intent.name}",
                    "action_type": "panel_navigate",
                    "payload": nav_payload,
                }
            })
        if form:
            await asyncio.sleep(0.4)  # Brief delay so page loads before form opens.
            form_payload_nav: dict = {"form": form, "prefill": intent.slots}
            if panel_id:
                form_payload_nav["panel_id"] = panel_id
            await broadcaster.broadcast("all", "ui_action", {
                "action": {
                    "id": f"intent_form_{intent.name}",
                    "action_type": "panel_open_form",
                    "payload": form_payload_nav,
                }
            })
        card = _intent_card_data(intent)
        card_payload_nav: dict = {"type": card["type"], "data": card["data"]}
        if card.get("card"):
            card_payload_nav["card"] = card["card"]
        if panel_id:
            card_payload_nav["panel_id"] = panel_id
        await broadcaster.broadcast("all", "ui_action", {
            "action": {
                "id": f"intent_card_{intent.name}",
                "action_type": "show_card",
                "payload": card_payload_nav,
            }
        })
    except Exception as exc:
        logger.warning("_broadcast_intent_nav failed (non-fatal): %s", exc)
from openclaw_ws import openclaw_cli, chat_inject, discover_openclaw_capabilities, _zoe_context_prefix
from zoe_acp_client import openclaw_acp_stream as _acp_stream
from zoe_agent import (
    _mempalace_load_user_facts, _mempalace_add, _fire_memory_capture,
    _build_memory_context,
)
# Brain-lane selection has ONE source of truth (brain_dispatch.py) — every voice
# path already routes through it. Aliased so every internal call site and every
# test target name in this module stays unchanged.
from brain_dispatch import (
    use_core_brain,
    use_flue_brain as _use_flue_brain,
    brain_streaming as _brain_streaming,
    brain_oneshot as _brain_oneshot,
)
from auth import get_current_user, resolve_acting_user
from database import get_db
from db_pool import get_db_ctx
from ui_orchestrator import enqueue_ui_action
from zoe_ui_components import auto_extract_components
from research_evidence import (
    build_package,
    classify_query,
    default_source_for_query,
    fetch_web_fallback_results,
    missing_brief_fields,
    package_needs_web_fallback,
)
from risk_policy import classify_request, is_whatsapp_connect_request
from chat_session_title import derive_session_title, title_is_weak
from ag_ui_stream import AgRunRecorder, iter_openclaw_text_chunks, iter_text_message_chunks, new_run_ids
from ag_ui.core import (
    CustomEvent,
    EventType,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StateSnapshotEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageChunkEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder

# W4-C2: SSE/AG-UI protocol mechanics moved verbatim to chat_stream_protocol.py.
# These re-exports are PERMANENT API — tests and callers import/monkeypatch these
# names on routers.chat (the voice_tts re-export contract, applied to chat).
from chat_stream_protocol import (
    brain_tool_sentinel_events,
    brain_tool_card_events,
    _iter_openclaw_heartbeats,
    _cancel_if_pending,
    _BUILDER_INTENTS,
    _detect_preview_urls,
    _synthesize_builder_actions,
    _sanitize_builder_reply,
    _extract_ui_actions,
    _map_ui_payload_to_action,
    _queue_ui_actions_background,
    _stream_openclaw_assistant_ag,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

# Intents that show a generative UI form in the chat instead of silently executing.
# The form is pre-filled from extracted slots; the user confirms before the API call is made.
_FORM_INTENTS: frozenset[str] = frozenset({
    "calendar_create",
    "note_create",
    "journal_create",
    "list_add",
    "reminder_create",
    "timer_create",
})

# Intents that deliberately do not have a direct `execute_intent` handler
# because they're designed to be expanded via openclaw_user_message() and
# routed to OpenClaw.  For these, a None result from execute_intent is not a
# failure — it's a delegation.  Tagging them here keeps the UI from showing a
# red "tool failed" tile.
_OPENCLAW_DELEGATION_INTENTS: frozenset[str] = frozenset({
    "build_widget",
    "build_page",
    "extend_capability",
    "self_improve",  # reviews intent-miss log and proposes new patterns
    # connect_chatgpt is handled by execute_intent (Tier-0 Python) — no LLM needed
})

# Long-running intents that should route through the Multica board when available.
# These get an AG-UI approval card before being queued; openclaw_user_message() is
# NOT called for these — the raw user message is used as the board issue description.
_MULTICA_BOARD_INTENTS: frozenset[str] = frozenset({
    "build_widget",
    "build_page",
    "extend_capability",
    "self_improve",
})


def _build_calendar_form_props(slots: dict) -> dict:
    import datetime
    from intent_router import _parse_date, _parse_time
    date_raw = slots.get("date", "")
    time_raw = slots.get("time", "")
    parsed_date = (_parse_date(date_raw) if date_raw else None) or datetime.date.today().isoformat()
    return {
        "title":    slots.get("title", ""),
        "date":     parsed_date,
        "time":     _parse_time(time_raw) or "" if time_raw else "",
        "category": slots.get("category", "general"),
    }


def _build_note_form_props(slots: dict) -> dict:
    return {
        "title":   slots.get("title", ""),
        "content": slots.get("content", ""),
    }


def _build_journal_form_props(slots: dict) -> dict:
    import datetime
    return {
        "content": slots.get("content", ""),
        "date":    datetime.date.today().isoformat(),
    }


def _build_list_add_form_props(slots: dict) -> dict:
    return {
        "item":      slots.get("item", ""),
        "list_type": slots.get("list_type", "shopping"),
    }


def _build_reminder_form_props(slots: dict) -> dict:
    import datetime
    from intent_router import _parse_date
    date_raw = slots.get("date", "")
    parsed_date = (_parse_date(date_raw) if date_raw else None) or datetime.date.today().isoformat()
    return {
        "title": slots.get("title", ""),
        "date":  parsed_date,
        "time":  slots.get("time", ""),   # already HH:MM from intent_router._parse_time
    }


def _build_timer_form_props(slots: dict) -> dict:
    return {
        "minutes": int(slots.get("minutes", 5)),
        "label":   slots.get("label", "Timer"),
    }


_FORM_COMPONENT_MAP: dict[str, tuple[str, callable]] = {
    "calendar_create": ("calendar_event_form", _build_calendar_form_props),
    "note_create":     ("note_create_form",    _build_note_form_props),
    "journal_create":  ("journal_create_form", _build_journal_form_props),
    "list_add":        ("list_add_form",       _build_list_add_form_props),
    "reminder_create": ("reminder_create_form", _build_reminder_form_props),
    "timer_create":    ("timer_create_form",   _build_timer_form_props),
}

_FORM_BLURB: dict[str, str] = {
    "calendar_create": "Here's your event — fill in the details and create it when you're ready.",
    "note_create":     "Here's your note — add your content and save it.",
    "journal_create":  "Here's your journal entry — write your thoughts and save.",
    "list_add":        "Here's your list item — confirm the details and add it.",
    "reminder_create": "Here's your reminder — check the details and set it.",
    "timer_create":    "",   # timer tile speaks for itself
}
_MEMORY_AUTO_INGEST = os.environ.get("MEMORY_AUTO_INGEST", "false").lower() == "true"
# Approval guard: disabled in Zoe Agent mode — Zoe Agent handles safety natively
_ZOE_AGENT_MODE    = os.environ.get("HERMES_FAST_PATH", "true").lower() != "true"
_JETSON_AGENT_MODE = os.environ.get("JETSON_AGENT_MODE", "false").lower() == "true"
_USE_ZOE_AGENT = _ZOE_AGENT_MODE or _JETSON_AGENT_MODE

# Production cutover: the brain is now zoe-core (Pi full-agent on local Gemma).
# Defaults ON. ZOE_USE_CORE_BRAIN=false falls back to the legacy zoe_agent brain
# during the validation window (removed once every avenue is proven).
# Import-time SNAPSHOT of the canonical parser: this must stay a module constant
# (it feeds the _USE_LOCAL_BRAIN lane-entry gate below, which tests monkeypatch
# as a constant), but it now shares use_core_brain()'s parse so the lane gate and
# the dispatch functions can never disagree on what the env means.
_USE_ZOE_CORE = use_core_brain()
# "Use a local brain" = either the new zoe-core (Pi) or the legacy zoe_agent.
# When set, the local brain takes the slot that would otherwise fall to OpenClaw.
_USE_LOCAL_BRAIN = _USE_ZOE_AGENT or _USE_ZOE_CORE
# The zoe-core Pi brain pulls memory itself every turn via the memory.ts extension
# (/api/memories/for-prompt), so also passing db_memory_context into the brain
# prompt double-injects the same facts (extra prefill). Default OFF (deduped);
# set ZOE_CHAT_INJECT_DB_MEMORY=1 to restore the old double-injection if needed.
_CHAT_INJECT_DB_MEMORY = os.environ.get("ZOE_CHAT_INJECT_DB_MEMORY", "0").strip().lower() in ("1", "true", "yes", "on")


# Hard wall-clock budget for the flag-gated compose step inside the chat
# stream. compose_card has its own HTTP timeout, but that can still hold the
# stream's RUN_FINISHED for many seconds when the model server is slow — this
# budget caps the wait as seen by the stream, whatever the cause.
_COMPOSE_STREAM_BUDGET_S = float(os.environ.get("ZOE_COMPOSE_STREAM_BUDGET_S", "6"))


async def maybe_compose_event(user_message, answer_text, *, user_id, emitted_domains):
    """Flag-gated generative-UI step: returns the zoe.ui_component CustomEvent
    carrying a composed card, or None (flag off / a domain card already emitted /
    compose failed / budget exceeded). Never raises; bounded by
    _COMPOSE_STREAM_BUDGET_S so the stream can never hang on composition."""
    try:
        from ui_compose import compose_card, compose_enabled

        if not compose_enabled() or emitted_domains:
            return None
        composed = await asyncio.wait_for(
            compose_card(user_message, answer_text, user_id=user_id),
            timeout=_COMPOSE_STREAM_BUDGET_S,
        )
        if not composed:
            return None
        return CustomEvent(
            name="zoe.ui_component",
            value={"type": "compose", "data": {"action": "Composed view"}, "card": composed},
        )
    except asyncio.TimeoutError:
        logger.info("compose skipped: exceeded stream budget %.1fs", _COMPOSE_STREAM_BUDGET_S)
        return None
    except Exception as exc:  # noqa: BLE001 — additive, never break the turn
        logger.debug("compose hook failed (non-fatal): %s", exc)
        return None


_GUARDED_AUTO = (
    os.environ.get("OPENCLAW_GUARDED_AUTO", "true").lower() == "true"
    and not _USE_ZOE_AGENT
)
_ALL_TOOLS_ENABLED = os.environ.get("OPENCLAW_ALL_TOOLS_ENABLED", "true").lower() == "true"
_WHATSAPP_FLOW_ENABLED = os.environ.get("WHATSAPP_FLOW_ENABLED", "true").lower() == "true"

_OPENCLAW_GW = os.environ.get("ZOE_OPENCLAW_GW",
    os.environ.get("OPENCLAW_GATEWAY_URL", "http://127.0.0.1:18789"))
_BROWSER_BROKER = create_default_browser_broker(_OPENCLAW_GW)

# ── Frustration signal detection ──────────────────────────────────────────────
# Lightweight in-memory tracker: session_id → list of (normalized_msg, ts) tuples
# Not persisted — session-scoped only. Proposals written via evolution_notice.
_frustration_tracker: "OrderedDict[str, list[tuple[str, float]]]" = OrderedDict()
_FRUSTRATION_WINDOW_S = 1800  # 30 minute session window
_FRUSTRATION_THRESHOLD = 3    # same message N times = frustration signal
_MAX_FRUSTRATION_SESSIONS = int(os.environ.get("ZOE_FRUSTRATION_MAX_SESSIONS", "2000"))


def _chat_pi_context_turns(context: object | None) -> str:
    if context is None:
        return ""
    last_text = str(getattr(context, "last_text", "") or "").strip()
    last_intent = str(getattr(context, "last_intent", "") or "").strip()
    if not last_text:
        return ""
    return f"previous={last_text!r}; previous_intent={last_intent}"


async def _run_chat_pi_hybrid_lane(
    message_for_processing: str,
    *,
    user_id: str,
    session_id: str,
    context: object | None = None,
    request_text: str | None = None,
    run_id: str | None = None,
    record_run_state: bool = False,
    panel_id: str | None = None,
) -> dict:
    """Run the production Pi hybrid lane once and persist accepted chat output."""
    try:
        from pi_hybrid_production import (
            PiHybridProductionConfig,
            pi_hybrid_production_eligible,
            processing_cue_packet,
            try_pi_hybrid_production,
        )

        config = PiHybridProductionConfig.from_env()
        eligible, reason = pi_hybrid_production_eligible(message_for_processing, config=config)
        if not eligible:
            if config.enabled:
                logger.debug("Pi hybrid production skipped: %s", reason)
            return {"attempted": False, "reason": reason, "config_enabled": config.enabled}

        cue = await processing_cue_packet(text=message_for_processing)
        decision = await try_pi_hybrid_production(
            message_for_processing,
            user_id=user_id,
            context_turns=_chat_pi_context_turns(context),
            config=config,
        )
        response_text = str(decision.get("response_text") or "")
        action_form = decision.get("action_form") if isinstance(decision.get("action_form"), dict) else None
        accepted = bool(decision.get("accepted") and (response_text or action_form))
        payload = {
            "attempted": True,
            "cue": cue,
            "decision": decision,
            "accepted": accepted,
            "response_text": response_text,
            "action_form": action_form,
        }
        if not accepted:
            return payload

        intent_name = str(decision.get("intent") or "pi_hybrid")
        if action_form and panel_id and intent_name in _INTENT_PANEL_NAV:
            try:
                from intent_router import Intent

                asyncio.ensure_future(_broadcast_intent_nav(
                    Intent(intent_name, dict(action_form.get("prefill") or {}), 1.0),
                    panel_id=panel_id,
                ))
            except Exception as exc:
                logger.debug("Pi hybrid action form broadcast skipped: %s", exc)
        if not action_form:
            asyncio.ensure_future(chat_inject_background(
                message_for_processing,
                response_text,
                intent_name,
                user_id,
                session_id,
            ))
            asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, response_text))
        if response_text:
            await _save_chat_message(session_id, "assistant", response_text, user_id=user_id)
        if record_run_state:
            active_run_id = run_id or new_run_ids()[0]
            await _record_run_state(
                active_run_id,
                session_id,
                user_id,
                mode="chat",
                status="completed",
                request_text=request_text or message_for_processing,
                response_text=response_text,
                metadata={
                    "pi_hybrid": {
                        "accepted": True,
                        "reason": decision.get("reason"),
                        "intent": decision.get("intent"),
                        "intent_group": decision.get("intent_group"),
                        "agreement_kind": decision.get("agreement_kind"),
                        "execution_scope": decision.get("execution_scope"),
                        "action_form": bool(action_form),
                    }
                },
            )
        return payload
    except Exception as exc:
        logger.debug("Pi hybrid production path failed open to Zoe route: %s", exc)
        return {"attempted": False, "reason": "exception", "error": exc.__class__.__name__}


def _normalize_for_frustration(text: str) -> str:
    """Strip punctuation and lowercase for cosine similarity comparison."""
    return re.sub(r'[^\w\s]', '', text.lower().strip())


def _check_frustration(session_id: str, user_id: str, message: str) -> None:
    """Check if user has sent similar messages repeatedly. Fire-and-forget via asyncio."""
    import asyncio as _asyncio
    norm = _normalize_for_frustration(message)
    if len(norm) < 10:
        return  # too short to be meaningful

    now = time.time()
    entries = [
        (m, t) for m, t in _frustration_tracker.get(session_id, [])
        if now - t < _FRUSTRATION_WINDOW_S
    ]
    entries.append((norm, now))
    _bounded_lru_set(_frustration_tracker, session_id, entries, max_size=_MAX_FRUSTRATION_SESSIONS)

    # Count similar entries (simple: exact match after normalization)
    similar = sum(1 for m, _ in entries if m == norm)
    if similar >= _FRUSTRATION_THRESHOLD:
        async def _record():
            try:
                from evolution_notice import record_frustration_signal  # type: ignore[import]
                await record_frustration_signal(user_id, norm, session_id, similar)
            except Exception:
                pass
        _asyncio.ensure_future(_record())


# ── ChatGPT / OpenAI Codex device-code OAuth constants ────────────────────────
_CODEX_CLIENT_ID    = "app_EMoamEEZ73f0CkXaXp7hrann"
_CODEX_AUTH_BASE    = "https://auth.openai.com"
_CODEX_USERCODE_URL = f"{_CODEX_AUTH_BASE}/api/accounts/deviceauth/usercode"
_CODEX_POLL_URL     = f"{_CODEX_AUTH_BASE}/api/accounts/deviceauth/token"
_CODEX_TOKEN_URL    = f"{_CODEX_AUTH_BASE}/oauth/token"
_CODEX_CALLBACK_URL = f"{_CODEX_AUTH_BASE}/deviceauth/callback"
_CODEX_VERIFY_URL   = f"{_CODEX_AUTH_BASE}/codex/device"
_CODEX_AUTH_PROFILES_PATH = os.path.expanduser(
    "~/.openclaw/agents/main/agent/auth-profiles.json"
)


_HERMES_AUTH_PATH = pathlib.Path.home() / ".hermes" / "auth.json"


async def _write_hermes_codex_token(access_token: str, refresh_token: str = "") -> bool:
    """Write ChatGPT OAuth tokens to Hermes auth store (~/.hermes/auth.json)."""
    import datetime
    try:
        auth_data: dict = {}
        if _HERMES_AUTH_PATH.exists():
            auth_data = json.loads(_HERMES_AUTH_PATH.read_text())
        auth_data.setdefault("version", 1)
        auth_data.setdefault("providers", {})
        auth_data["providers"]["openai-codex"] = {
            "tokens": {
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
            "last_refresh": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z",
            "auth_mode": "chatgpt",
        }
        _HERMES_AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        _HERMES_AUTH_PATH.write_text(json.dumps(auth_data, indent=2))
        _HERMES_AUTH_PATH.chmod(0o600)
        logger.info("Hermes codex token written to %s", _HERMES_AUTH_PATH)
        return True
    except Exception as exc:
        logger.warning("Failed to write Hermes codex token: %s", exc)
        return False


async def _restart_hermes() -> None:
    """Restart hermes-agent systemd user service so it picks up the new token."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "--user", "restart", "hermes-agent.service",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)
        logger.info("hermes-agent.service restarted after token write")
    except Exception as exc:
        logger.warning("Hermes restart after token write failed: %s", exc)


# Off-loop runner for the panel_status ssh reachability probe (AGENTS.md fork
# rule: never fork on the event loop thread — same pattern as
# tts_waterfall._spawn_tts_cli / kanban_adapter._spawn_cli).
_SSH_PROBE_POOL = concurrent.futures.ThreadPoolExecutor(
    max_workers=2, thread_name_prefix="ssh-probe"
)
_SSH_PROBE_WAIT_GRACE_S = 5.0


async def _spawn_ssh_probe(
    args: list[str], *, timeout: float
) -> "subprocess.CompletedProcess[bytes]":
    """Run a short-lived reachability CLI off the event loop, bounded even if fork() wedges."""
    loop = asyncio.get_running_loop()

    def _blocking() -> "subprocess.CompletedProcess[bytes]":
        return subprocess.run(args, capture_output=True, timeout=timeout, check=False)

    return await asyncio.wait_for(
        loop.run_in_executor(_SSH_PROBE_POOL, _blocking),
        timeout=timeout + _SSH_PROBE_WAIT_GRACE_S,
    )


async def _build_panel_intent_card(intent, db, user_id: str) -> str:
    """Build an AG-UI markdown status card for touch panel intents."""
    import httpx as _httpx

    if intent.name == "panel_list":
        cur = await db.execute(
            "SELECT panel_id, name, location, ip_address, is_active, last_seen_at FROM panels ORDER BY created_at DESC"
        )
        rows = await cur.fetchall()
        if not rows:
            return (
                "**Touch Panels** — none registered yet.\n\n"
                "To set up your first panel, say **\"set up touch panel\"** or scan the QR code on the panel screen."
            )
        lines = ["**Touch Panels**\n"]
        for r in rows:
            dot = "🟢" if r["is_active"] else "🔴"
            ip = r["ip_address"] or "unknown IP"
            last = r["last_seen_at"] or "never"
            name = r["name"] or r["panel_id"]
            loc = f" · {r['location']}" if r["location"] else ""
            lines.append(f"{dot} **{name}** (`{r['panel_id']}`){loc} — {ip} · last seen {last}")
        lines.append("\n[Manage panels](/settings.html#touch-panels)")
        return "\n".join(lines)

    elif intent.name == "panel_status":
        cur = await db.execute(
            "SELECT panel_id, name, ip_address, last_seen_at, ssh_user, ssh_port FROM panels WHERE is_active = 1 ORDER BY created_at DESC LIMIT 5"
        )
        rows = await cur.fetchall()
        if not rows:
            return "No active panels registered. Say **\"set up touch panel\"** to add one."
        lines = ["**Panel Status**\n"]
        for r in rows:
            ip = r["ip_address"]
            reachable = None
            if ip:
                try:
                    # Off-loop spawn (AGENTS.md fork rule): run-to-completion
                    # subprocess.run in a small dedicated pool — never a fork on
                    # the event loop thread. run()'s own timeout kills+reaps a
                    # stalled ssh child in the worker thread; the coroutine-side
                    # wait_for still bounds this turn even if the fork itself
                    # wedges (run()'s timeout only starts once Popen returns).
                    result = await _spawn_ssh_probe(
                        [
                            "ssh", "-o", "ConnectTimeout=4", "-o", "StrictHostKeyChecking=no",
                            "-o", "BatchMode=yes", "-p", str(r["ssh_port"] or 22),
                            f"{r['ssh_user'] or 'pi'}@{ip}", "echo ok",
                        ],
                        timeout=6.0,
                    )
                    reachable = result.returncode == 0
                except Exception:
                    reachable = False
            ssh_dot = ("🟢 SSH ok" if reachable else "🔴 SSH unreachable") if reachable is not None else "⚪ no IP"
            lines.append(f"**{r['name'] or r['panel_id']}** · {ip or 'no IP'} · {ssh_dot} · last seen {r['last_seen_at'] or 'never'}")
        return "\n".join(lines)

    elif intent.name == "panel_setup":
        return (
            "**Set up a new touch panel**\n\n"
            "1. Power on the Raspberry Pi (fresh image)\n"
            "2. A WiFi hotspot named **ZoeTouch-Setup-XXXX** will appear\n"
            "3. Connect your phone to that hotspot\n"
            "4. Select your home WiFi and tap **Connect**\n"
            "5. The panel will auto-update, then show a **QR code**\n"
            "6. Scan the QR code with your phone — confirm the name/location\n"
            "7. Your panel is live!\n\n"
            "Or enter a 6-character code here: say **\"connect panel XXXXXX\"**\n\n"
            "[Manage panels](/settings.html#touch-panels)"
        )

    elif intent.name == "panel_confirm_code":
        code = (intent.slots or {}).get("code", "").upper()
        if not code:
            return "What's the 6-character code shown on the panel screen?"
        # Look up the code in DB and confirm it
        row = await (await db.execute(
            "SELECT code, device_id, status, expires_at FROM panel_provision_codes WHERE code = ?",
            (code,),
        )).fetchone()
        if not row:
            return f"Code **{code}** wasn't found. Make sure the panel is showing the code and hasn't expired."
        if row["status"] == "expired":
            return f"Code **{code}** has expired. The panel will generate a new code — scan the updated QR."
        if row["status"] == "confirmed":
            return f"Code **{code}** was already confirmed. The panel should be connecting now."
        return (
            f"Code **{code}** found. Open this link on your phone to name and confirm the panel:\n\n"
            f"[Pair panel →](/touch/pair.html?code={code})"
        )

    return "I couldn't build a panel card for that intent."


async def _chatgpt_connect_flow(emit, enc, recorder, assistant_message_id, tool_call_id, label):  # noqa: ARG001
    """Full OpenAI Codex device-code OAuth flow, streamed as AG-UI events.

    Phases emitted via zoe.chatgpt_connect custom events:
      verify  – show verification URL + user code to the user
      waiting – heartbeat while polling (every poll interval)
      success – tokens saved, include email
      timeout – 15-minute deadline passed without authorization
      error   – unexpected error, include message
    """
    import base64
    import httpx

    _hdrs = {
        "Content-Type": "application/json",
        "originator": "openclaw",
        "User-Agent": "openclaw",
    }

    # ── Step 1: request device code ──────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _CODEX_USERCODE_URL, json={"client_id": _CODEX_CLIENT_ID}, headers=_hdrs
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        yield emit(CustomEvent(name="zoe.chatgpt_connect", value={"phase": "error", "message": str(exc)}))
        return

    device_auth_id = data.get("device_auth_id", "")
    user_code = data.get("user_code", "")
    interval_sec = int(data.get("interval", 5))

    if not device_auth_id or not user_code:
        yield emit(CustomEvent(name="zoe.chatgpt_connect", value={"phase": "error", "message": "OpenAI did not return a device code."}))
        return

    # ── Step 2: show card (card renders into message bubble — no text message) ─
    yield emit(CustomEvent(name="zoe.chatgpt_connect", value={
        "phase": "verify",
        "url": _CODEX_VERIFY_URL,
        "code": user_code,
        "expires_in_minutes": 15,
    }))

    # ── Step 3: poll for authorization ───────────────────────────────────────
    deadline = asyncio.get_event_loop().time() + 900  # 15 minutes
    authorization_code = None
    code_verifier = None

    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(interval_sec)
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                poll_resp = await client.post(
                    _CODEX_POLL_URL,
                    json={"device_auth_id": device_auth_id, "user_code": user_code},
                    headers=_hdrs,
                )
            if poll_resp.status_code == 200:
                body = poll_resp.json()
                authorization_code = body.get("authorization_code")
                code_verifier = body.get("code_verifier")
                if authorization_code and code_verifier:
                    break
            # 403/404 = pending — keep polling
        except Exception:
            pass
        yield emit(CustomEvent(name="zoe.chatgpt_connect", value={"phase": "waiting"}))

    if not authorization_code:
        yield emit(CustomEvent(name="zoe.chatgpt_connect", value={"phase": "timeout"}))
        return

    # ── Step 4: exchange for tokens ───────────────────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            tok_resp = await client.post(
                _CODEX_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": authorization_code,
                    "redirect_uri": _CODEX_CALLBACK_URL,
                    "client_id": _CODEX_CLIENT_ID,
                    "code_verifier": code_verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded", "originator": "openclaw", "User-Agent": "openclaw"},
            )
            tok_resp.raise_for_status()
            tok = tok_resp.json()
    except Exception as exc:
        yield emit(CustomEvent(name="zoe.chatgpt_connect", value={"phase": "error", "message": f"Token exchange failed: {exc}"}))
        return

    access_token  = tok.get("access_token", "")
    refresh_token = tok.get("refresh_token", "")
    expires_in    = tok.get("expires_in", 3600)
    expires_at_ms = int((time.time() + expires_in) * 1000)

    # ── Step 5: decode email from JWT payload ─────────────────────────────────
    email = None
    try:
        payload_b64 = access_token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        jd = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
        email = jd.get("email") or jd.get("sub")
    except Exception:
        pass

    # ── Step 6: persist to ~/.openclaw/agents/main/agent/auth-profiles.json ──
    profile_id = f"openai-codex:{email}" if email else "openai-codex:default"
    os.makedirs(os.path.dirname(_CODEX_AUTH_PROFILES_PATH), exist_ok=True)
    try:
        store: dict = {}
        if os.path.exists(_CODEX_AUTH_PROFILES_PATH):
            with open(_CODEX_AUTH_PROFILES_PATH) as _f:
                store = json.load(_f)
        store.setdefault("version", 1)
        store.setdefault("profiles", {})[profile_id] = {
            "profileId": profile_id,
            "credential": {
                "type": "oauth",
                "provider": "openai-codex",
                "access": access_token,
                "refresh": refresh_token,
                "expires": expires_at_ms,
                **({"email": email} if email else {}),
            },
        }
        with open(_CODEX_AUTH_PROFILES_PATH, "w") as _f:
            json.dump(store, _f, indent=2)
        os.chmod(_CODEX_AUTH_PROFILES_PATH, 0o600)
    except Exception as exc:
        yield emit(CustomEvent(name="zoe.chatgpt_connect", value={"phase": "error", "message": f"Failed to save credentials: {exc}"}))
        return

    # ── Step 6b: persist to ~/.hermes/auth.json (openai-codex provider) ─────
    hermes_ok = await _write_hermes_codex_token(access_token, refresh_token)
    if hermes_ok:
        await _restart_hermes()

    # ── Step 7: success ───────────────────────────────────────────────────────
    services_note = "OpenClaw and Hermes are now using your ChatGPT account." if hermes_ok else "OpenClaw is now using your ChatGPT account."
    yield emit(CustomEvent(name="zoe.chatgpt_connect", value={"phase": "success", "email": email or "your account", "services_note": services_note}))
    logger.info("ChatGPT OAuth connected: profile_id=%s hermes_ok=%s", profile_id, hermes_ok)

# Per-session concurrency guard: only one OpenClaw turn runs per session at a time.
# If a second request arrives for the same session while one is running, it waits
# up to _SESSION_LOCK_TIMEOUT_S before being rejected to avoid duplicate responses.
_SESSION_LOCKS: dict[str, asyncio.Lock] = {}
_SESSION_LOCK_TIMEOUT_S = float(os.environ.get("ZOE_SESSION_LOCK_TIMEOUT_S", "5"))


def _get_session_lock(session_id: str) -> asyncio.Lock:
    if session_id not in _SESSION_LOCKS:
        _SESSION_LOCKS[session_id] = asyncio.Lock()
    return _SESSION_LOCKS[session_id]

async def _persist_ag_ui_run(session_id: str, run_id: str, events: list) -> None:
    """Best-effort persistence of the wire-format event list for debugging / future resume."""
    if not events:
        return
    try:
        # get_db_ctx, not `async for db in get_db()`: exiting the generator
        # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
        async with get_db_ctx() as db:
            await db.execute(
                """INSERT INTO chat_ag_ui_runs (id, session_id, run_id, events)
                   VALUES (?, ?, ?, ?)""",
                (uuid.uuid4().hex[:16], session_id, run_id, json.dumps(events)),
            )
            await db.commit()
    except Exception as e:
        logger.warning("chat_ag_ui_runs persist failed (non-fatal): %s", e)


def _extract_memory_candidates(user_message: str, assistant_response: str):
    """Back-compat shim: legacy dict shape over the unified extractor.

    Kept so in-tree tests and any external callers that still expect the
    pre-consolidation dict contract keep working. New code should call
    ``memory_extractor.extract_candidates`` directly, which returns typed
    ``MemoryCandidate`` dataclasses.
    """
    from memory_extractor import extract_candidates

    return [
        {
            "memory_type": c.memory_type,
            "title": c.title or c.memory_type.title(),
            "content": c.text,
            "entity_type": c.entity_type,
            "entity_id": c.entity_id,
            "confidence": c.confidence,
            "source_type": "chat",
            "source_excerpt": c.source_excerpt,
            "visibility": "personal",
            "provenance": {"session_id": None},
        }
        for c in extract_candidates(user_message, assistant_response)
    ]


async def _persist_memory_candidates(user_id: str, session_id: str, user_message: str, assistant_response: str):
    """Single post-turn memory hook.

    Runs two passes in parallel:
    1. Regex extraction  — zero-latency, catches explicit patterns immediately.
    2. LLM turn digest   — background Gemma call, catches nuanced facts the
                           regex misses (relationships, pets, life events, etc.)
                           within seconds rather than waiting for the 3am batch.
    """
    if user_id == "guest":
        return
    # A memory COMMAND ("forget everything about Delia", "forget that") is an
    # instruction, not a fact — mining it minted junk rows ("Gift idea for
    # everything about: Delia", live repro 2026-07-13) that resurrected the
    # just-forgotten entity into the recall packet. Skip every extractor pass.
    try:
        from intent_router import _FORGET_ENTITY_RE, _FORGET_LAST_RE
        t = (user_message or "").strip()
        if _FORGET_LAST_RE.match(t) or _FORGET_ENTITY_RE.match(t):
            return
    except Exception:
        pass  # never let the guard break extraction itself
    # The mirror case: an EXPLICIT "remember/note that …" utterance clears any
    # forget tombstone whose name it mentions — regardless of which lane
    # answered the turn (the semantic router sometimes sends a re-teach to the
    # note/brain lane, whose mined extraction would otherwise be shadow-dropped
    # and the re-teach silently lost; live repro 2026-07-13).
    try:
        from memory_tombstones import clear_matching as _tomb_clear, is_explicit_teach
        if is_explicit_teach(user_message):
            _tomb_clear(user_id, user_message)
    except Exception:
        pass
    try:
        from memory_extractor import extract_and_ingest
        from memory_digest import run_turn_digest
        from person_extractor import process_text as _person_extract
        from person_extractor_llm import process_text_llm as _person_extract_llm
        from latent_intent_detector import detect_and_store as _detect_suggestions

        _mx_results = await asyncio.gather(
            extract_and_ingest(
                user_message,
                assistant_response,
                user_id=user_id,
                session_id=session_id,
                source="chat_regex",
                auto_approve=_MEMORY_AUTO_INGEST,
            ),
            run_turn_digest(
                user_id,
                user_message,
                assistant_response,
                session_id=session_id,
                source="turn_digest",
            ),
            # USER MESSAGE ONLY — never mine the assistant reply for facts.
            # Feeding f"{user_message}\n{assistant_response}" here stored Zoe's
            # own sentences ("... but I don't have a specific favorite recipe
            # noted for you right now.") as approved user memories, which then
            # surfaced in the recall packet and reinforced the wrong answer
            # (poisoned-store bug, 2026-07-07). Pinned by
            # tests/test_memory_extractor_purity.py.
            _person_extract(
                user_message,
                user_id=user_id,
                source="conversation",
                session_id=session_id,
            ),
            _person_extract_llm(
                user_message,
                user_id=user_id,
                source="conversation",
                session_id=session_id,
            ),
            return_exceptions=True,
        )
        # QA review F3 (silent fact loss): with return_exceptions=True and the
        # results discarded, a dying extractor vanished without a trace — whole
        # turns' facts were lost while the reply claimed "I'll remember that".
        # Name-and-shame each failed pass at WARNING so loss is visible in ops.
        for _mx_name, _mx_res in zip(
            ("extract_and_ingest", "run_turn_digest", "person_extract", "person_extract_llm"),
            _mx_results,
        ):
            if isinstance(_mx_res, BaseException):
                logger.warning(
                    "memory pass %s FAILED for user=%s (fact loss possible): %s",
                    _mx_name, user_id, _mx_res,
                )
                try:  # QA review F13: make silent fact loss countable in ops
                    from memory_metrics import memory_async_extract_fail_count
                    memory_async_extract_fail_count.labels(
                        lane="chat", pass_name=_mx_name).inc()
                except Exception:
                    pass
        asyncio.ensure_future(_detect_suggestions(
            user_message,
            user_id=user_id,
            session_id=session_id,
        )).add_done_callback(
            lambda t: None if t.cancelled() else (
                logger.warning("latent intent detection failed: %s", t.exception())
                if t.exception() else None
            )
        )
    except Exception as e:
        logger.warning("Memory candidate persistence failed: %s", e)


async def _ensure_user_and_chat_session(session_id: str, user_id: str) -> None:
    """Create users row and chat_sessions row if missing (UI sends client session ids before POST /sessions/)."""
    from fastapi import HTTPException

    # get_db_ctx, not `async for db in get_db()`: exiting the generator
    # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
    async with get_db_ctx() as db:
        existing = await (
            await db.execute(
                "SELECT user_id FROM chat_sessions WHERE id = ?", (session_id,)
            )
        ).fetchone()
        if existing and existing["user_id"] != user_id:
            raise HTTPException(status_code=403, detail="Not your chat session")
        await db.execute(
            "INSERT INTO users (id, name, role) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
            (user_id, user_id, "member"),
        )
        await db.execute(
            "INSERT INTO chat_sessions (id, user_id, title) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
            (session_id, user_id, "New Chat"),
        )
        await db.commit()


async def _save_chat_message(
    session_id: str, role: str, content: str, user_id: str | None = None,
    *, truncated: bool = False,
) -> bool:
    """Persist a single chat turn to chat_messages.

    Returns True only when the row was actually committed (False for empty
    content or a swallowed DB failure), so callers can decide whether the
    reply still needs a recovery save.

    Mirrors the OpenClaw gateway pattern: the caller sends only the new message;
    the server owns the transcript. Zoe Agent reads this table for conversation
    history on the next turn, enabling proper follow-on context.

    When `user_id` is supplied (and is a real, non-guest identity) it is recorded
    into `chat_messages.metadata` as JSON {"user_id": ...}. Auth happens per-turn
    but `chat_sessions.user_id` stays 'guest', so idle consolidation reads this
    per-turn user to know whose memory to write. Guest/empty users are not stored.
    """
    clean_content = (content or "").strip()
    if not clean_content:
        return False
    meta: dict = {}
    if user_id and user_id not in ("guest", "voice-daemon", ""):
        meta["user_id"] = user_id
    if truncated:
        # Flags a partial reply persisted after a mid-stream failure (P3-A): the
        # tokens the user saw before the brain raised, saved so history isn't lost.
        meta["truncated"] = True
    metadata = json.dumps(meta) if meta else None
    # Use the context-managed pool acquire (deterministic release). The bare
    # `async for db in get_db(): ... break` form leaves the generator suspended
    # at the yield when broken out of, so the connection isn't returned to the
    # pool until GC — under fire-and-forget concurrency that produced the
    # asyncpg "another operation is in progress" errors that dropped saves.
    try:
        from db_pool import get_db_ctx
        async with get_db_ctx() as db:
            await db.execute(
                "INSERT INTO chat_messages (id, session_id, role, content, metadata) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
                (uuid.uuid4().hex, session_id, role, clean_content, metadata),
            )
            await _touch_chat_session(db, session_id=session_id, content=clean_content)
            await db.commit()
        return True
    except Exception as _sme:
        # Warning, not debug: a dropped save means the reply the user saw is
        # missing from history, and callers use the False return to recover.
        logger.warning(
            "_save_chat_message failed (%s turn for session %s dropped): %s",
            role, session_id, _sme,
        )
        return False


async def _touch_chat_session(db, *, session_id: str, content: str, user_id: str | None = None) -> None:
    """Refresh session recency and promote weak titles from the saved turn."""
    where = "id = ? AND user_id = ?" if user_id else "id = ?"
    params = (session_id, user_id) if user_id else (session_id,)
    title_row = await db.execute_fetchall(
        f"SELECT title FROM chat_sessions WHERE {where}",
        params,
    )
    if not title_row:
        return
    current_title = dict(title_row[0]).get("title") or "New Chat"
    new_title = derive_session_title(content) if content.strip() and title_is_weak(current_title) else None
    if new_title:
        await db.execute(
            f"UPDATE chat_sessions SET updated_at = NOW()::text, title = ? WHERE {where}",
            (new_title, *params),
        )
    else:
        await db.execute(
            f"UPDATE chat_sessions SET updated_at = NOW()::text WHERE {where}",
            params,
        )


INTENT_LABELS = {
    "list_add": "Shopping List",
    "list_show": "Shopping List",
    "list_remove": "Shopping List",
    "calendar_create": "Calendar",
    "calendar_show": "Calendar",
    "reminder_create": "Reminders",
    "reminder_list": "Reminders",
    "people_create": "Contacts",
    "people_search": "Contacts",
    "note_create": "Notes",
    "note_search": "Notes",
    "weather": "Weather",
    "journal_create": "Journal",
    "journal_streak": "Journal",
    "journal_prompt": "Journal",
    "transaction_create": "Transactions",
    "transaction_summary": "Transactions",
    "daily_briefing": "Daily Briefing",
    "ha_full_setup": "Home Assistant setup",
    # New intents (ZOE-42, ZOE-15, ZOE-9, ZOE-10, ZOE-13)
    "greeting": "Greeting",
    "smart_home": "Smart Home",
    "calculate": "Calculator",
    "set_volume": "Volume",
}


async def run_openclaw_agent(
    message: str,
    session_id: str,
    user_id: str = "guest",  # fail-open to least-privilege, not admin (#1021/#1032 posture)
    *,
    user_role: str | None = None,
    username: str | None = None,
    memories: str | None = None,
    allow_openclaw: bool = False,
) -> str:
    """Legacy entry point retained for callers; routes to Hermes by default.

    OpenClaw remains available, but callers must opt in explicitly. This keeps
    old call sites from silently bypassing Hermes after future edits.
    """
    if allow_openclaw:
        return await openclaw_cli(
            message,
            session_id,
            user_id,
            user_role=user_role,
            username=username,
            memories=memories,
        )
    return await _hermes_completion(
        message,
        session_id,
        user_id,
        username=username or "",
        portrait="",
        facts=memories or "",
    )


async def chat_inject_background(user_message: str, assistant_response: str, intent_name: str, user_id: str = "guest", session_id: str = "web"):
    """Optionally mirror an intent summary into OpenClaw for legacy debugging."""
    if os.environ.get("ZOE_MIRROR_INTENTS_TO_OPENCLAW", "false").lower() != "true":
        return
    try:
        summary = f"[Intent: {intent_name}] User: {user_message} | Result: {assistant_response}"
        await chat_inject(summary, user_id, session_id)
        logger.info(f"chat.inject sent for intent {intent_name}")
    except Exception as e:
        logger.warning(f"chat.inject background failed (non-fatal): {e}")


_APPROVE_RE = re.compile(r"^/approve\s+([a-zA-Z0-9_-]{8,})\s*(.*)$")

def _research_followup_prompt(missing: list[str]) -> str:
    questions = {
        "location": "What location should I search in?",
        "budget": "What budget or price range should I use?",
        "timeframe": "What date or timeframe should I target?",
    }
    prompts = [questions[m] for m in missing if m in questions]
    if not prompts:
        return ""
    return "Before I start research, I need a bit more detail: " + " ".join(prompts)


async def _capture_research_screenshot(
    *,
    query: str,
    candidate_source: str,
    user_id: str,
    session_id: str,
) -> tuple[str, str]:
    """Capture a screenshot for research evidence using broker-backed browser control."""
    navigate_to = (candidate_source or "").strip() or default_source_for_query(query)
    try:
        plan = _BROWSER_BROKER.plan_action(
            action="capture_screenshot",
            params={
                "navigate_to": navigate_to,
                "timeout_s": 15.0,
                "screenshot_timeout_s": 20.0,
            },
            user_id=user_id,
            session_id=f"chat:{session_id}",
            action_class="read_only_research",
            requested_surface="openclawLocal",
        )
        result = await _BROWSER_BROKER.execute(plan)
        image_b64 = str(result.get("image_base64") or "").strip()
        if result.get("ok") and image_b64:
            return image_b64, navigate_to
    except Exception as exc:
        logger.debug("research screenshot capture failed (non-fatal): %s", exc)
    return "", navigate_to


async def _build_research_package(
    *,
    query: str,
    response_text: str,
    backend: str,
    user_id: str,
    session_id: str,
) -> dict:
    """Build research package and attach screenshot evidence when possible."""
    fallback_rows: list[dict] = []
    pkg = build_package(query=query, response_text=response_text, backend=backend)
    if package_needs_web_fallback(pkg):
        fallback_rows = await asyncio.to_thread(fetch_web_fallback_results, query)
        if fallback_rows:
            pkg = build_package(
                query=query,
                response_text=response_text,
                backend=backend,
                web_fallback_results=fallback_rows,
            )
    source = (pkg.get("sources") or [""])[0]
    image_b64, screenshot_url = await _capture_research_screenshot(
        query=query,
        candidate_source=str(source or ""),
        user_id=user_id,
        session_id=session_id,
    )
    if image_b64:
        pkg = build_package(
            query=query,
            response_text=response_text,
            backend=backend,
            screenshot_b64=image_b64,
            screenshot_url=screenshot_url,
            web_fallback_results=fallback_rows,
        )
    return pkg


def _extract_approval_token(message: str):
    m = _APPROVE_RE.match((message or "").strip())
    if not m:
        return None, message
    token = m.group(1)
    rest = (m.group(2) or "").strip()
    return token, rest or message


async def _create_pending_approval(user_id: str, session_id: str, message: str, risk_level: str, reason: str, normalized_action: str) -> str:
    approval_id = uuid.uuid4().hex[:16]
    # get_db_ctx, not `async for db in get_db()`: exiting the generator
    # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
    async with get_db_ctx() as db:
        await db.execute(
            """INSERT INTO openclaw_approvals
               (id, session_id, user_id, request_text, normalized_action, risk_level, status, reason)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (approval_id, session_id, user_id, message, normalized_action, risk_level, reason),
        )
        await db.commit()
    return approval_id


async def _resolve_approval(user_id: str, approval_id: str) -> dict | None:
    # get_db_ctx, not `async for db in get_db()`: exiting the generator
    # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
    async with get_db_ctx() as db:
        rows = await db.execute_fetchall(
            """SELECT * FROM openclaw_approvals
               WHERE id = ? AND user_id = ? AND status = 'pending'
               ORDER BY created_at DESC LIMIT 1""",
            (approval_id, user_id),
        )
        if not rows:
            return None
        row = dict(rows[0])
        await db.execute(
            "UPDATE openclaw_approvals SET status='approved', resolved_at=NOW()::text WHERE id = ?",
            (approval_id,),
        )
        await db.commit()
        return row
    return None


async def _record_run_state(run_id: str, session_id: str, user_id: str, mode: str, status: str, request_text: str, response_text: str | None = None, metadata: dict | None = None):
    # get_db_ctx, not `async for db in get_db()`: exiting the generator
    # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
    async with get_db_ctx() as db:
        await db.execute(
            """INSERT INTO openclaw_run_state
               (id, session_id, user_id, mode, status, request_text, response_text, metadata, finished_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, CASE WHEN ? IN ('completed','error','cancelled') THEN NOW()::text ELSE NULL END)
               ON CONFLICT(id) DO UPDATE SET
                 status=excluded.status,
                 response_text=COALESCE(excluded.response_text, openclaw_run_state.response_text),
                 metadata=COALESCE(excluded.metadata, openclaw_run_state.metadata),
                 finished_at=CASE WHEN excluded.status IN ('completed','error','cancelled') THEN NOW()::text ELSE openclaw_run_state.finished_at END""",
            (
                run_id,
                session_id,
                user_id,
                mode,
                status,
                request_text,
                response_text,
                json.dumps(metadata) if metadata else None,
                status,
            ),
        )
        await db.commit()


async def chat_stream_generator(
    message: str,
    session_id: str,
    user: dict,
    *,
    force_openclaw: bool = False,
    force_agent: str = "auto",
    req_panel_id: str | None = None,
    channel: str = "chat",
):
    user_id = user["user_id"]
    user_role = user.get("role")
    username = user.get("username")
    await _ensure_user_and_chat_session(session_id, user_id)
    # Persist user turn immediately — enables history for Zoe Agent on the NEXT request
    await _save_chat_message(session_id, "user", message, user_id=user_id)
    # Frustration signal detection (non-blocking, session-scoped)
    _check_frustration(session_id, user_id, message)
    enc = EventEncoder()
    recorder = AgRunRecorder()
    run_id, assistant_message_id = new_run_ids()
    run_mode = "hermes" if force_agent == "hermes" else "chat"
    # P3-A: accumulate brain tokens at generator scope so the error path can
    # recover the partial the user already saw. `persisted_assistant` guards
    # against double-saving once the normal-path save has been scheduled.
    full_response = ""
    persisted_assistant = False

    def emit(ev):
        return recorder.emit(enc, ev)

    try:
        yield emit(RunStartedEvent(type=EventType.RUN_STARTED, thread_id=session_id, run_id=run_id))
        yield emit(
            CustomEvent(
                name="zoe.run_meta",
                value={
                    "runId": run_id,
                    "sessionId": session_id,
                    "mode": "hermes" if force_agent == "hermes" else "chat",
                    "forceOpenClaw": force_openclaw,
                    "forceAgent": force_agent,
                },
            )
        )
        yield emit(
            CustomEvent(
                name="zoe.session",
                value={"sessionId": session_id, "messageId": assistant_message_id},
            )
        )
        await _record_run_state(run_id, session_id, user_id, mode=run_mode, status="running", request_text=message)

        approval_token, message_for_processing = _extract_approval_token(message)
        if approval_token:
            approved = await _resolve_approval(user_id, approval_token)
            if not approved:
                yield emit(
                    RunErrorEvent(
                        type=EventType.RUN_ERROR,
                        message="Approval token is invalid or already used.",
                        code="approval_invalid",
                    )
                )
                await _record_run_state(run_id, session_id, user_id, mode=run_mode, status="error", request_text=message)
                return
            message_for_processing = approved.get("request_text") or message_for_processing
            yield emit(
                CustomEvent(
                    name="zoe.run_log",
                    value={"level": "info", "message": "Approval accepted. Executing requested action."},
                )
            )

        if _GUARDED_AUTO:
            risk = classify_request(message_for_processing)
            if risk.requires_confirmation and not approval_token:
                approval_id = await _create_pending_approval(
                    user_id=user_id,
                    session_id=session_id,
                    message=message_for_processing,
                    risk_level=risk.level,
                    reason=risk.reason,
                    normalized_action=risk.normalized_action,
                )
                yield emit(
                    CustomEvent(
                        name="zoe.ui_component",
                        value={
                            "component": "confirmation",
                            "props": {
                                "title": "Approval Required",
                                "description": f"{risk.reason}. Approve to continue.",
                                "yes_text": "Approve",
                                "no_text": "Cancel",
                                "yes_action": f"/approve {approval_id}",
                            },
                        },
                    )
                )
                yield emit(
                    CustomEvent(
                        name="zoe.run_log",
                        value={"level": "warn", "message": f"Action gated ({risk.level} risk). Pending approval id: {approval_id}"},
                    )
                )
                yield emit(
                    RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=session_id, run_id=run_id)
                )
                await _record_run_state(
                    run_id,
                    session_id,
                    user_id,
                    mode=run_mode,
                    status="completed",
                    request_text=message,
                    response_text="Approval required",
                    metadata={"approval_id": approval_id, "risk": risk.level},
                )
                return

        if force_agent == "hermes":
            _h_portrait, _h_facts, _h_semantic = await asyncio.gather(
                _safe_load_portrait(user_id),
                _mempalace_load_user_facts(user_id),
                _build_memory_context(message_for_processing, user_id=user_id),
            )
            _h_combined_facts = "\n\n".join(filter(None, [_h_facts, _h_semantic]))
            yield emit(
                StateSnapshotEvent(
                    type=EventType.STATE_SNAPSHOT,
                    snapshot={
                        "status": "generating",
                        "phase": "hermes",
                        "model": "Hermes Agent",
                        "detail": "Thinking…",
                    },
                )
            )
            yield emit(
                TextMessageStartEvent(
                    type=EventType.TEXT_MESSAGE_START,
                    message_id=assistant_message_id,
                    role="assistant",
                )
            )
            response_text = ""
            async for hermes_event in _iter_hermes_stream_events(
                message_for_processing,
                session_id,
                user_id,
                username=username or "",
                portrait=_h_portrait,
                facts=_h_combined_facts,
            ):
                if hermes_event.get("kind") == "progress":
                    for progress_event in _hermes_progress_events(
                        hermes_event.get("event", "hermes.progress"),
                        hermes_event.get("payload", {}),
                    ):
                        yield emit(progress_event)
                    continue
                token = hermes_event.get("text", "")
                response_text += token
                yield emit(
                    TextMessageChunkEvent(
                        type=EventType.TEXT_MESSAGE_CHUNK,
                        message_id=assistant_message_id,
                        role="assistant",
                        delta=token,
                    )
                )
            yield emit(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=assistant_message_id))
            if response_text.strip():
                asyncio.ensure_future(_save_chat_message(session_id, "assistant", response_text, user_id=user_id))
                if user_id != "guest":
                    asyncio.ensure_future(
                        _persist_memory_candidates(user_id, session_id, message_for_processing, response_text)
                    )
            yield emit(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=session_id, run_id=run_id))
            await _record_run_state(
                run_id,
                session_id,
                user_id,
                mode="hermes",
                status="completed",
                request_text=message,
                response_text=response_text,
            )
            return

        lc = message_for_processing.lower().strip()
        task_class = classify_query(message_for_processing)
        response_text = ""
        if task_class == "research":
            missing = missing_brief_fields(message_for_processing)
            if missing:
                followup = _research_followup_prompt(missing)
                yield emit(
                    TextMessageStartEvent(
                        type=EventType.TEXT_MESSAGE_START,
                        message_id=assistant_message_id,
                        role="assistant",
                    )
                )
                async for line in iter_text_message_chunks(enc, recorder, assistant_message_id, followup):
                    yield line
                yield emit(
                    TextMessageEndEvent(
                        type=EventType.TEXT_MESSAGE_END,
                        message_id=assistant_message_id,
                    )
                )
                yield emit(
                    RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=session_id, run_id=run_id)
                )
                await _record_run_state(
                    run_id,
                    session_id,
                    user_id,
                    mode="chat",
                    status="completed",
                    request_text=message,
                    response_text=followup,
                    metadata={"task_class": task_class, "missing_constraints": missing},
                )
                await _save_chat_message(session_id, "assistant", followup, user_id=user_id)
                return
        if "what can you do right now" in lc or lc in {"/capabilities", "capabilities", "tools"}:
            try:
                caps_text = Path("/home/zoe/assistant/CAPABILITIES.md").read_text()[:12000]
            except Exception:
                caps_text = "Hermes is the active escalation agent. Zoe tools include calendar, lists, reminders, memory, Graphify, Multica, and CloakBrowser."
            capabilities_text = f"Hermes/Zoe capabilities:\n\n{caps_text}"
            yield emit(
                TextMessageStartEvent(
                    type=EventType.TEXT_MESSAGE_START,
                    message_id=assistant_message_id,
                    role="assistant",
                )
            )
            async for line in iter_text_message_chunks(enc, recorder, assistant_message_id, capabilities_text):
                yield line
            yield emit(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=assistant_message_id))
            yield emit(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=session_id, run_id=run_id))
            await _record_run_state(run_id, session_id, user_id, mode="chat", status="completed", request_text=message, response_text=capabilities_text)
            await _save_chat_message(session_id, "assistant", capabilities_text, user_id=user_id)
            return

        if _WHATSAPP_FLOW_ENABLED and is_whatsapp_connect_request(message_for_processing):
            yield emit(
                CustomEvent(
                    name="zoe.run_log",
                    value={"level": "info", "message": "Starting WhatsApp connect flow preflight..."},
                )
            )
            flow_text = (
                "Starting WhatsApp connection workflow.\n"
                "1) Preflight checks\n"
                "2) Credential/session validation\n"
                "3) QR/session handshake\n"
                "4) Webhook and test message validation\n"
                "I will now run this through Hermes with guarded confirmations."
            )
            yield emit(TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=assistant_message_id, role="assistant"))
            async for line in iter_text_message_chunks(enc, recorder, assistant_message_id, flow_text):
                yield line
            yield emit(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=assistant_message_id))
            message_for_processing = (
                "Connect WhatsApp integration for user with full guided flow: "
                "preflight checks, credential/session validation, qr/session setup, webhook test, "
                "and remediation steps. Use guarded execution and ask for confirmation before any write/auth step."
            )

        use_intent_fast_path = (not force_openclaw) and _ALL_TOOLS_ENABLED
        if task_class == "research":
            # Research requests must flow through the evidence path, not terse intent handlers.
            use_intent_fast_path = False
        if message_for_processing.startswith("/openclaw "):
            message_for_processing = message_for_processing[len("/openclaw ") :].strip()
            force_openclaw = True
            use_intent_fast_path = False

        _chat_ctx = _CHAT_CONTEXTS.get(session_id) or _CC()
        if use_intent_fast_path:
            # Tier-1.5 channel-agnostic fast path (the SAME core voice uses): answer
            # calendar / lists / weather / time / people / memory sub-second instead
            # of the Pi-hybrid/brain lane below. Threshold-gated in expert_dispatch —
            # only confident matches short-circuit; otherwise None → hybrid unchanged.
            try:
                import fast_tiers as _fast_path
                _fp_res = await _fast_path.resolve(
                    message_for_processing, user_id, session_id,
                    channel=channel,
                )
            except Exception as _fp_exc:  # never let the fast path break a turn
                logger.debug("chat_stream fast_path resolve failed (non-fatal): %s", _fp_exc)
                _fp_res = None
            if _fp_res is not None and getattr(_fp_res, "reply", ""):
                _fp_reply = _fp_res.reply
                yield emit(TextMessageStartEvent(
                    type=EventType.TEXT_MESSAGE_START,
                    message_id=assistant_message_id,
                    role="assistant",
                ))
                async for line in iter_text_message_chunks(enc, recorder, assistant_message_id, _fp_reply):
                    yield line
                yield emit(TextMessageEndEvent(
                    type=EventType.TEXT_MESSAGE_END,
                    message_id=assistant_message_id,
                ))
                asyncio.ensure_future(chat_inject_background(message_for_processing, _fp_reply, f"fast:{_fp_res.domain}", user_id, session_id))
                asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, _fp_reply))
                asyncio.ensure_future(_save_chat_message(session_id, "assistant", _fp_reply, user_id=user_id))
                yield emit(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=session_id, run_id=run_id))
                # Close out the run row (opened status="running" at the top); every
                # other early exit here records completed/error before returning.
                await _record_run_state(run_id, session_id, user_id, mode=run_mode, status="completed", request_text=message, response_text=_fp_reply)
                return
            _pi_hybrid = await _run_chat_pi_hybrid_lane(
                message_for_processing,
                user_id=user_id,
                session_id=session_id,
                context=_chat_ctx,
                request_text=message,
                run_id=run_id,
                record_run_state=True,
                panel_id=req_panel_id,
            )
            if _pi_hybrid.get("attempted"):
                _pi_cue = _pi_hybrid.get("cue") or {}
                if _pi_cue.get("available"):
                    yield emit(CustomEvent(name="zoe.pi_hybrid_cue", value={
                        "text": _pi_cue.get("text") or "",
                        "event": _pi_cue.get("event"),
                        "source": "pi_hybrid_production",
                    }))
                yield emit(StateSnapshotEvent(
                    type=EventType.STATE_SNAPSHOT,
                    snapshot={
                        "status": "generating",
                        "phase": "pi_hybrid",
                        "model": "Zoe",
                        "detail": _pi_cue.get("text") or "Checking...",
                    },
                ))
                _pi_decision = _pi_hybrid.get("decision") or {}
                yield emit(CustomEvent(name="zoe.pi_hybrid_decision", value={
                    "accepted": _pi_decision.get("accepted"),
                    "reason": _pi_decision.get("reason"),
                    "intent": _pi_decision.get("intent"),
                    "intent_group": _pi_decision.get("intent_group"),
                    "agreement_kind": _pi_decision.get("agreement_kind"),
                    "production_route_change": _pi_decision.get("production_route_change"),
                    "lab_result": _pi_decision.get("lab_result"),
                    "execution_scope": _pi_decision.get("execution_scope"),
                    "action_form": _pi_hybrid.get("action_form"),
                }))
                if _pi_hybrid.get("accepted"):
                    action_form = _pi_hybrid.get("action_form") or {}
                    if action_form:
                        component = action_form.get("component")
                        prefill = action_form.get("prefill") or {}
                        if component:
                            yield emit(CustomEvent(
                                name="zoe.ui_component",
                                value={"component": component, "props": prefill},
                            ))
                    response_text = str(_pi_hybrid.get("response_text") or "")
                    yield emit(TextMessageStartEvent(
                        type=EventType.TEXT_MESSAGE_START,
                        message_id=assistant_message_id,
                        role="assistant",
                    ))
                    async for line in iter_text_message_chunks(enc, recorder, assistant_message_id, response_text):
                        yield line
                    yield emit(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=assistant_message_id))
                    yield emit(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=session_id, run_id=run_id))
                    return
        intent = await detect_and_extract_intent(
            message_for_processing, user_id, context=_chat_ctx
        ) if use_intent_fast_path else None
        if intent:
            _chat_ctx.activate(intent.name, getattr(intent, "slots", {}), message_for_processing)
            _bounded_lru_set(_CHAT_CONTEXTS, session_id, _chat_ctx, max_size=_MAX_CHAT_CONTEXT_SESSIONS)

        # Tier 0.5: LLM classifier for short missed utterances
        _tier05_hint: Optional[Intent] = None
        if intent is None and use_intent_fast_path and len(message_for_processing.split()) <= 20:
            try:
                from intent_classifier_llm import (
                    classify_intent_with_context as _classify,
                    CONFIDENCE_EXECUTE_THRESHOLD,
                    CONFIDENCE_HINT_THRESHOLD,
                )
                _classified = await _classify(message_for_processing, context=_chat_ctx, timeout=2.0)
                if _classified and _classified.confidence >= CONFIDENCE_EXECUTE_THRESHOLD:
                    intent = _classified
                    _chat_ctx.activate(intent.name, getattr(intent, "slots", {}), message_for_processing)
                    _bounded_lru_set(_CHAT_CONTEXTS, session_id, _chat_ctx, max_size=_MAX_CHAT_CONTEXT_SESSIONS)
                    logger.info("Tier 0.5 hit: %s confidence=%.2f", intent.name, intent.confidence)
                elif _classified and _classified.confidence >= CONFIDENCE_HINT_THRESHOLD:
                    _tier05_hint = _classified
                    logger.info("Tier 0.5 hint: %s confidence=%.2f", _classified.name, _classified.confidence)
            except Exception as _e:
                logger.debug("Tier 0.5 classifier failed (non-fatal): %s", _e)

        if intent:
            logger.info("Intent matched: %s slots=%s", intent.name, getattr(intent, "slots", None))
            logger.info("intent_outcome=matched intent=%s", intent.name)
            # Panel navigation is intentionally NOT fired from the web chat path.
            # _broadcast_intent_nav is preserved for voice and touch-panel request paths.

            label = INTENT_LABELS.get(intent.name, intent.name)
            tool_call_id = uuid.uuid4().hex[:12]
            tool_name = f"zoe-data.{intent.name}"

            yield emit(StepStartedEvent(type=EventType.STEP_STARTED, step_name=label))
            yield emit(
                ToolCallStartEvent(
                    type=EventType.TOOL_CALL_START,
                    tool_call_id=tool_call_id,
                    tool_call_name=tool_name,
                    parent_message_id=assistant_message_id,
                )
            )
            slots = getattr(intent, "slots", None) or {}
            yield emit(
                ToolCallArgsEvent(
                    type=EventType.TOOL_CALL_ARGS,
                    tool_call_id=tool_call_id,
                    delta=json.dumps(slots),
                )
            )
            yield emit(ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=tool_call_id))

            # ── Form-based intents: show a generative UI tile instead of silently executing ──
            if intent.name in _FORM_INTENTS:
                logger.info("intent_outcome=matched_form intent=%s", intent.name)
                _comp_name, _prop_builder = _FORM_COMPONENT_MAP[intent.name]
                _form_props = _prop_builder(slots)
                yield emit(
                    ToolCallResultEvent(
                        type=EventType.TOOL_CALL_RESULT,
                        message_id=assistant_message_id,
                        tool_call_id=tool_call_id,
                        content=json.dumps({"status": "form_shown", "component": _comp_name}),
                        role="tool",
                    )
                )
                yield emit(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=label))
                yield emit(
                    CustomEvent(
                        name="zoe.ui_component",
                        value={"component": _comp_name, "props": _form_props},
                    )
                )
                _blurb = _FORM_BLURB.get(intent.name, "")
                if _blurb:
                    yield emit(TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=assistant_message_id, role="assistant"))
                    async for line in iter_text_message_chunks(enc, recorder, assistant_message_id, _blurb):
                        yield line
                    yield emit(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=assistant_message_id))
                    asyncio.ensure_future(_save_chat_message(session_id, "assistant", _blurb, user_id=user_id))
                return  # skip the standard execute_intent path

            # ── Touch Panel intents: AG-UI status cards ───────────────────────
            if intent.name in ("panel_setup", "panel_status", "panel_list", "panel_confirm_code"):
                logger.info("intent_outcome=panel_%s", intent.name)
                try:
                    panel_card_md = await _build_panel_intent_card(intent, db, user_id)
                except Exception as _pe:
                    logger.warning("panel intent card error: %s", _pe)
                    panel_card_md = "I had trouble fetching panel status. Check `/api/panels` for details."
                yield emit(TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=assistant_message_id, role="assistant"))
                async for line in iter_text_message_chunks(enc, recorder, assistant_message_id, panel_card_md):
                    yield line
                yield emit(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=assistant_message_id))
                yield emit(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=session_id, run_id=run_id))
                asyncio.ensure_future(_save_chat_message(session_id, "assistant", panel_card_md, user_id=user_id))
                return

            # ── ChatGPT connect: full device-code OAuth flow inline ────────────
            if intent.name == "connect_chatgpt":
                logger.info("intent_outcome=chatgpt_connect_flow")
                yield emit(
                    ToolCallResultEvent(
                        type=EventType.TOOL_CALL_RESULT,
                        message_id=assistant_message_id,
                        tool_call_id=tool_call_id,
                        content=json.dumps({"status": "chatgpt_connect_started"}),
                        role="tool",
                    )
                )
                yield emit(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=label))
                async for event in _chatgpt_connect_flow(emit, enc, recorder, assistant_message_id, tool_call_id, label):
                    yield event
                yield emit(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=session_id, run_id=run_id))
                await _record_run_state(run_id, session_id, user_id, mode="chat", status="completed", request_text=message, response_text="chatgpt_connect_flow")
                return

            # ── Multica board routing for long-running intents ────────────────
            # Board-routed intents get an AG-UI approval card; openclaw_user_message()
            # is NOT called — the raw user_text is used as the board issue description.
            if intent.name in _MULTICA_BOARD_INTENTS:
                try:
                    from multica_client import MULClient  # type: ignore[import]
                    _mul = MULClient()
                    if _mul.is_configured():
                        import uuid as _uuid2
                        _board_task_id = _uuid2.uuid4().hex[:12]
                        _card_text = (
                            f"**Task ready for board**\n\n"
                            f"**{message_for_processing[:80]}**\n\n"
                            f"Est. 5–15 min · Will appear on the Multica board.\n\n"
                            f"[Start now](/api/agent/board/approve?task_id={_board_task_id}) | "
                            f"[Review first](/api/agent/board/review?task_id={_board_task_id}) | "
                            f"[Cancel](/api/agent/board/cancel?task_id={_board_task_id})"
                        )
                        yield emit(TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=assistant_message_id, role="assistant"))
                        async for line in iter_text_message_chunks(enc, recorder, assistant_message_id, _card_text):
                            yield line
                        yield emit(TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=assistant_message_id))
                        yield emit(RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=session_id, run_id=run_id))
                        asyncio.ensure_future(_save_chat_message(session_id, "assistant", _card_text, user_id=user_id))
                        return
                except ImportError:
                    pass  # Multica not installed — fall through to standard OpenClaw path
                except Exception as _mul_exc:
                    logger.warning("Multica board routing failed, falling through: %s", _mul_exc)

            result = await execute_intent(intent, user_id)

            if result:
                logger.info("intent_outcome=matched_exec_ok intent=%s", intent.name)
                body = result if len(result) <= 12000 else result[:12000] + "…"
                yield emit(
                    ToolCallResultEvent(
                        type=EventType.TOOL_CALL_RESULT,
                        message_id=assistant_message_id,
                        tool_call_id=tool_call_id,
                        content=body,
                        role="tool",
                    )
                )
                yield emit(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=label))
                yield emit(
                    TextMessageStartEvent(
                        type=EventType.TEXT_MESSAGE_START,
                        message_id=assistant_message_id,
                        role="assistant",
                    )
                )
                async for line in iter_text_message_chunks(enc, recorder, assistant_message_id, result):
                    yield line
                yield emit(
                    TextMessageEndEvent(
                        type=EventType.TEXT_MESSAGE_END,
                        message_id=assistant_message_id,
                    )
                )
                asyncio.ensure_future(chat_inject_background(message_for_processing, result, intent.name, user_id, session_id))
                asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, result))
                asyncio.ensure_future(_save_chat_message(session_id, "assistant", result, user_id=user_id))
            else:
                if intent.name in _OPENCLAW_DELEGATION_INTENTS:
                    logger.info("Intent %s delegating to Hermes/Multica", intent.name)
                    logger.info("intent_outcome=matched_delegated intent=%s", intent.name)
                    _tc_status = {"status": "delegated_to_hermes", "tool": tool_name}
                else:
                    logger.warning("Intent %s execution failed, falling back to LLM", intent.name)
                    logger.info("intent_outcome=matched_exec_failed intent=%s fallback=%s", intent.name, "zoe_agent" if _USE_ZOE_AGENT else "hermes")
                    _tc_status = {"status": "failed", "tool": tool_name}
                yield emit(
                    ToolCallResultEvent(
                        type=EventType.TOOL_CALL_RESULT,
                        message_id=assistant_message_id,
                        tool_call_id=tool_call_id,
                        content=json.dumps(_tc_status),
                        role="tool",
                    )
                )
                yield emit(StepFinishedEvent(type=EventType.STEP_FINISHED, step_name=label))
                # Delegation intents go through Hermes/Multica by default.
                # OpenClaw is only used for explicit /openclaw or force_agent=openclaw requests.
                _force_openclaw_here = force_openclaw
                if _USE_LOCAL_BRAIN and not _force_openclaw_here:
                    yield emit(
                        StateSnapshotEvent(
                            type=EventType.STATE_SNAPSHOT,
                            snapshot={
                                "status": "generating",
                                "phase": "zoe_agent",
                                "model": "Zoe",
                                "detail": "Thinking…",
                            },
                        )
                    )
                    task = asyncio.create_task(
                        _brain_oneshot(message_for_processing, session_id, user_id)
                    )
                    try:
                        async for hb in _iter_openclaw_heartbeats(emit, task, phase_label="Zoe"):
                            yield hb
                        response_text = await task
                    finally:
                        await _cancel_if_pending(task)
                else:
                    yield emit(
                        StateSnapshotEvent(
                            type=EventType.STATE_SNAPSHOT,
                            snapshot={
                                "status": "generating",
                                "phase": "openclaw",
                                "model": "Zoe (OpenClaw explicit fallback)",
                                "detail": "Handing off to OpenClaw because it was explicitly requested…",
                            },
                        )
                    )
                    oc_message = openclaw_user_message(intent, message_for_processing)
                    yield emit(
                        CustomEvent(
                            name="zoe.run_log",
                            value={
                                "level": "info",
                                "message": "Starting OpenClaw explicit fallback (browser and tools can take 30s to several minutes).",
                            },
                        )
                    )
                    _oc_portrait, _oc_intent_fallback_mem, _oc_semantic = await asyncio.gather(
                        _safe_load_portrait(user_id),
                        _mempalace_load_user_facts(user_id),
                        _build_memory_context(message_for_processing, user_id=user_id),
                    )
                    _oc_full_mem = "\n\n".join(filter(None, [_oc_portrait, _oc_intent_fallback_mem, _oc_semantic]))
                    task = asyncio.create_task(
                        run_openclaw_agent(
                            oc_message,
                            session_id,
                            user_id,
                            user_role=user_role,
                            username=username,
                            memories=_oc_full_mem or None,
                            allow_openclaw=True,
                        )
                    )
                    try:
                        async for hb in _iter_openclaw_heartbeats(emit, task):
                            yield hb
                        response_text = await task
                    finally:
                        await _cancel_if_pending(task)
                _, actions = _extract_ui_actions(response_text)
                if actions:
                    asyncio.ensure_future(_queue_ui_actions_background(actions, user_id, session_id))
                asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, response_text))
                if response_text:
                    asyncio.ensure_future(_save_chat_message(session_id, "assistant", response_text, user_id=user_id))
                async for line in _stream_openclaw_assistant_ag(
                    enc, recorder, assistant_message_id, response_text,
                    intent_name=intent.name if intent else None,
                ):
                    yield line
        else:
            logger.info("intent_outcome=no_match fast_path=%s", bool(use_intent_fast_path))
            if _USE_LOCAL_BRAIN:
                # ── Zoe Agent: Gemma 4 E4B-QAT with MemPalace + tools — true SSE streaming ──
                tier_label = "Jetson" if _JETSON_AGENT_MODE else "Pi"
                yield emit(
                    StateSnapshotEvent(
                        type=EventType.STATE_SNAPSHOT,
                        snapshot={
                            "status": "generating",
                            "phase": "zoe_agent",
                            "model": "Zoe",
                            "detail": "Thinking…",
                        },
                    )
                )
                yield emit(
                    CustomEvent(
                        name="zoe.run_log",
                        value={"level": "info", "message": f"{tier_label} Agent streaming…"},
                    )
                )
                yield recorder.emit(
                    enc,
                    TextMessageStartEvent(
                        type=EventType.TEXT_MESSAGE_START,
                        message_id=assistant_message_id,
                        role="assistant",
                    ),
                )
                full_response = ""
                escalate_signal: str | None = None
                # Load recent conversation history so Zoe Agent has context for follow-ups ("yes", etc.)
                prior_history: list[dict] = []
                try:
                    # get_db_ctx, not `async for db in get_db()`: exiting the generator
                    # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
                    async with get_db_ctx() as db:
                        rows = await db.execute(
                            "SELECT role, content FROM chat_messages "
                            "WHERE session_id = ? ORDER BY created_at DESC LIMIT 12",
                            (session_id,),
                        )
                        rows = await rows.fetchall()
                        prior_history = [{"role": r[0], "content": r[1]} for r in reversed(rows)]
                except Exception as _he:
                    logger.debug("history load failed (non-fatal): %s", _he)
                # Zoe Agent loads MemPalace facts internally. We also load a copy here
                # so that if Pi escalates to OpenClaw, the context prefix isn't blank.
                pi_db_memory = await _mempalace_load_user_facts(user_id)
                # Load user portrait (synthesized narrative understanding of the user).
                # Fast SQLite key-lookup — non-fatal if table not yet populated.
                pi_portrait = ""
                try:
                    from user_portrait import load_portrait  # type: ignore[import]
                    pi_portrait = await load_portrait(user_id) or ""
                except Exception as _pe:
                    logger.debug("chat: portrait load failed (non-fatal): %s", _pe)
                # Apply openclaw_user_message expansion so Zoe Agent has the same rich context
                # as the OpenClaw path (includes HA device state bootstrap text when intent matched).
                expanded_msg = openclaw_user_message(intent, message_for_processing) if intent else message_for_processing
                if _tier05_hint is not None:
                    expanded_msg = (
                        f"[Intent hint: {_tier05_hint.name}, confidence {_tier05_hint.confidence:.2f}, "
                        f"slots {_tier05_hint.slots}] "
                    ) + expanded_msg
                # Tracks tool_call_id → tool name across the brain stream so the
                # result/finish events can re-use the name the start event paired
                # with (the brain's result sentinel may omit it).
                _brain_tool_names: dict[str, str] = {}
                # Wave A: UI domains already carded this turn (dedupe repeat calls).
                _brain_card_domains: set[str] = set()
                async for chunk in _brain_streaming(
                    expanded_msg,
                    session_id,
                    user_id,
                    history=prior_history or None,
                    # Deduped by default: the memory.ts extension already injects the
                    # for-prompt packet, so don't double-inject the same facts here.
                    db_memory_context=(pi_db_memory or None) if _CHAT_INJECT_DB_MEMORY else None,
                    portrait=pi_portrait or None,
                ):
                    if chunk.startswith("__ESCALATE__:") or chunk.startswith("__ESCALATE_BG__:") or chunk.startswith("__ESCALATE_HERMES__:"):
                        escalate_signal = chunk
                        break
                    if chunk.startswith("__SEARCH_START__:"):
                        # Web search is about to execute — immediately stream a status line
                        # so the user sees activity instead of silence during the search delay.
                        _sq = chunk[len("__SEARCH_START__:"):]
                        _search_notice = (
                            f"Searching the web for *{_sq}*…\n\n" if _sq
                            else "Searching the web…\n\n"
                        )
                        full_response += _search_notice
                        yield recorder.emit(
                            enc,
                            TextMessageChunkEvent(
                                type=EventType.TEXT_MESSAGE_CHUNK,
                                message_id=assistant_message_id,
                                role="assistant",
                                delta=_search_notice,
                            ),
                        )
                        continue
                    if chunk.startswith("__DEEP_RESEARCH_START__:"):
                        # Deep research (~60s) — stream a context-aware status so the
                        # user knows what's happening and doesn't see a blank screen.
                        _dq = chunk[len("__DEEP_RESEARCH_START__:"):]
                        _dq_l = _dq.lower()
                        if re.search(r'\b(price|prices|cheap|cheapest|cost|buy|stock)\b', _dq_l):
                            _action = "Comparing prices across local stores"
                        elif re.search(r'\b(event|concert|festival|show|movie|cinema|gig|market|what.?s on)\b', _dq_l):
                            _action = "Checking what's on"
                        elif re.search(r'\b(restaurant|cafe|coffee|pizza|eat|food|pub|bar|takeaway)\b', _dq_l):
                            _action = "Finding local dining options"
                        elif re.search(r'\b(plumber|electrician|mechanic|dentist|doctor|pharmacy|vet|tradie|tradesman|cleaner|handyman)\b', _dq_l):
                            _action = "Finding local service providers"
                        elif re.search(r'\b(hotel|motel|airbnb|accommodation|stay)\b', _dq_l):
                            _action = "Checking accommodation"
                        elif re.search(r'\b(flight|flights|bus|train|timetable|schedule)\b', _dq_l):
                            _action = "Checking transport options"
                        elif re.search(r'\b(job|jobs|work|hiring|vacancy)\b', _dq_l):
                            _action = "Searching job listings"
                        else:
                            _action = "Researching"
                        # Extract a capitalised location from the query for the message
                        _loc_m = re.search(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b', _dq)
                        _loc_part = f" in {_loc_m.group(1)}" if _loc_m else ""
                        _dr_notice = f"{_action}{_loc_part} — visiting multiple sources, this takes ~60s…\n\n"
                        full_response += _dr_notice
                        yield recorder.emit(
                            enc,
                            TextMessageChunkEvent(
                                type=EventType.TEXT_MESSAGE_CHUNK,
                                message_id=assistant_message_id,
                                role="assistant",
                                delta=_dr_notice,
                            ),
                        )
                        continue
                    if chunk.startswith("__THINKING__:"):
                        # Tool activity hint — emit as a transient state snapshot (not message text)
                        _thinking_tool = chunk[len("__THINKING__:"):]
                        _tool_label = _thinking_tool.replace("_", " ").title()
                        yield emit(StateSnapshotEvent(
                            type=EventType.STATE_SNAPSHOT,
                            snapshot={"status": "generating", "phase": "tool", "detail": f"Using {_tool_label}…"},
                        ))
                        continue
                    if chunk.startswith("__UI__:"):
                        # Zoe Agent visual tool — emit via the same zoe.ui_component CUSTOM
                        # event used by every other component path in chat.py. The frontend
                        # handler for this event mounts to messageGroup (not contentEl) so
                        # components survive RUN_FINISHED. The test also detects this event.
                        try:
                            comp = json.loads(chunk[7:])
                            yield emit(CustomEvent(name="zoe.ui_component", value=comp))
                        except Exception as _uie:
                            logger.debug("__UI__ parse error (non-fatal): %s", _uie)
                        continue
                    if chunk.startswith("__TOOL__:"):
                        # Brain tool activity — map the sentinel phases the Pi brain
                        # surfaces (start/args/result) onto canonical AG-UI tool/step
                        # events so the chat UI can show "what Zoe is doing". Shared
                        # with the contract test via brain_tool_sentinel_events().
                        for _tool_ev in brain_tool_sentinel_events(
                            chunk,
                            assistant_message_id=assistant_message_id,
                            tool_names=_brain_tool_names,
                        ):
                            yield emit(_tool_ev)
                        # Wave A: also surface the data-filled card for UI-domain
                        # tool results (calendar/lists/weather), reusing the proven
                        # {type, data, card} zoe.ui_component render path.
                        async for _card_ev in brain_tool_card_events(
                            chunk,
                            user_id=user_id,
                            tool_names=_brain_tool_names,
                            emitted_domains=_brain_card_domains,
                        ):
                            yield emit(_card_ev)
                        continue
                    full_response += chunk
                    yield recorder.emit(
                        enc,
                        TextMessageChunkEvent(
                            type=EventType.TEXT_MESSAGE_CHUNK,
                            message_id=assistant_message_id,
                            role="assistant",
                            delta=chunk,
                        ),
                    )
                message_open = True
                # ── Generative UI (PR-B, flag-gated): if the brain answered with
                # text only (no domain card was emitted this turn) compose a card
                # from the answer via the catalog grammar. Runs AFTER the full text
                # has streamed (never delays tokens) and is bounded by a hard
                # stream budget so a slow/down model server can never hold
                # RUN_FINISHED hostage; any failure = simply no card.
                if not escalate_signal and full_response.strip():
                    _compose_ev = await maybe_compose_event(
                        message_for_processing,
                        full_response,
                        user_id=user_id,
                        emitted_domains=_brain_card_domains,
                    )
                    if _compose_ev is not None:
                        yield emit(_compose_ev)
                if escalate_signal:
                    is_background = escalate_signal.startswith("__ESCALATE_BG__:")
                    is_hermes = escalate_signal.startswith("__ESCALATE_HERMES__:")
                    # Operator policy: Hermes owns all foreground escalation. OpenClaw
                    # signals are treated as Hermes tasks unless explicitly re-enabled elsewhere.
                    if not is_background and not force_openclaw:
                        is_hermes = True
                    _, escalate_body = escalate_signal.split(":", 1)
                    reason, _, oc_task = escalate_body.partition("|")
                    oc_task_text = oc_task or message_for_processing

                    if is_hermes:
                        # Escalate to Hermes inside the existing AG-UI run/message.
                        logger.info("chat: Zoe Agent escalating to Hermes — reason=%s", reason.strip())
                        yield emit(
                            StateSnapshotEvent(
                                type=EventType.STATE_SNAPSHOT,
                                snapshot={
                                    "status": "generating",
                                    "phase": "hermes",
                                    "model": "Hermes Agent",
                                    "detail": f"Escalated: {reason.strip()}",
                                },
                            )
                        )
                        yield emit(
                            CustomEvent(
                                name="zoe.run_log",
                                value={"level": "info", "message": f"Escalating to Hermes: {reason.strip()}"},
                            )
                        )
                        response_text = full_response
                        async for hermes_event in _iter_hermes_stream_events(
                            oc_task_text,
                            session_id,
                            user_id,
                            username=user.get("username", ""),
                            portrait=pi_portrait or "",
                            facts=pi_db_memory or "",
                        ):
                            if hermes_event.get("kind") == "progress":
                                for progress_event in _hermes_progress_events(
                                    hermes_event.get("event", "hermes.progress"),
                                    hermes_event.get("payload", {}),
                                ):
                                    yield emit(progress_event)
                                continue
                            token = hermes_event.get("text", "")
                            response_text += token
                            yield recorder.emit(
                                enc,
                                TextMessageChunkEvent(
                                    type=EventType.TEXT_MESSAGE_CHUNK,
                                    message_id=assistant_message_id,
                                    role="assistant",
                                    delta=token,
                                ),
                            )
                    elif is_background:
                        # Queue as a background task and ack immediately
                        logger.info("chat: Zoe Agent background escalation — reason=%s", reason.strip())
                        try:
                            from background_runner import enqueue_background_task
                            task_id = await enqueue_background_task(
                                task=oc_task_text, user_id=user_id, session_id=session_id
                            )
                            ack_text = f"On it! I'll work on that in the background and let you know when it's done. (Task #{task_id})"
                        except Exception as _bge:
                            logger.warning("background task enqueue failed: %s", _bge)
                            ack_text = "I'll get started on that. I'll let you know when I'm done!"
                        yield emit(TextMessageChunkEvent(
                            type=EventType.TEXT_MESSAGE_CHUNK,
                            message_id=assistant_message_id,
                            role="assistant",
                            delta=ack_text,
                        ))
                        response_text = ack_text
                    else:
                        # Explicit OpenClaw request — stream via ACP channel.
                        logger.info("chat: Zoe Agent escalating to explicit OpenClaw fallback (ACP) — reason=%s", reason.strip())
                        yield recorder.emit(
                            enc,
                            TextMessageEndEvent(
                                type=EventType.TEXT_MESSAGE_END,
                                message_id=assistant_message_id,
                            ),
                        )
                        message_open = False
                        # Bridging message so the user isn't left staring at silence while
                        # OpenClaw spins up (typically 3-10s before first token).
                        _bridge_id = str(uuid.uuid4())
                        yield emit(TextMessageStartEvent(
                            type=EventType.TEXT_MESSAGE_START,
                            message_id=_bridge_id,
                            role="assistant",
                        ))
                        yield emit(TextMessageChunkEvent(
                            type=EventType.TEXT_MESSAGE_CHUNK,
                            message_id=_bridge_id,
                            role="assistant",
                            delta="On it — let me work through this properly for you.",
                        ))
                        yield emit(TextMessageEndEvent(
                            type=EventType.TEXT_MESSAGE_END,
                            message_id=_bridge_id,
                        ))
                        yield emit(
                            StateSnapshotEvent(
                                type=EventType.STATE_SNAPSHOT,
                                snapshot={
                                    "status": "generating",
                                    "phase": "openclaw",
                                    "model": "Zoe (OpenClaw explicit fallback)",
                                    "detail": f"Explicit fallback: {reason.strip()}",
                                },
                            )
                        )
                        # Start a new streaming message block for the OpenClaw response
                        oc_msg_id = str(uuid.uuid4())
                        yield emit(TextMessageStartEvent(
                            type=EventType.TEXT_MESSAGE_START,
                            message_id=oc_msg_id,
                            role="assistant",
                        ))
                        gateway_session_key = f"agent:main:zoe_{user_id}_{session_id}"
                        # Prepend user context + portrait + approved memory facts so OpenClaw
                        # has the same deep context it would get via openclaw_cli.
                        _oc_memories = "\n\n".join(filter(None, [pi_portrait or None, pi_db_memory or None]))
                        oc_prefixed = _zoe_context_prefix(
                            user_id,
                            user_role=user_role,
                            username=username,
                            memories=_oc_memories or None,
                        ) + oc_task_text
                        oc_full = ""
                        async for oc_chunk in _acp_stream(oc_prefixed, gateway_session_key):
                            oc_full += oc_chunk
                            yield recorder.emit(
                                enc,
                                TextMessageChunkEvent(
                                    type=EventType.TEXT_MESSAGE_CHUNK,
                                    message_id=oc_msg_id,
                                    role="assistant",
                                    delta=oc_chunk,
                                ),
                            )
                        yield emit(TextMessageEndEvent(
                            type=EventType.TEXT_MESSAGE_END,
                            message_id=oc_msg_id,
                        ))
                        response_text = oc_full
                        _, actions = _extract_ui_actions(response_text)
                        if actions:
                            asyncio.ensure_future(_queue_ui_actions_background(actions, user_id, session_id))
                        else:
                            # Auto-extract rich components from plain text
                            try:
                                extracted = auto_extract_components(response_text)
                                for _comp in extracted:
                                    yield emit(CustomEvent(name="zoe.ui_component", value=_comp))
                            except Exception as _aex:
                                logger.debug("auto_extract_components (escalation) failed: %s", _aex)
                else:
                    response_text = full_response
                if message_open:
                    yield recorder.emit(
                        enc,
                        TextMessageEndEvent(
                            type=EventType.TEXT_MESSAGE_END,
                            message_id=assistant_message_id,
                        ),
                    )
                # Save cards show prior-turn suggestions; current-turn detection runs
                # in the background via _persist_memory_candidates (one-turn lag).
                try:
                    from pending_suggestions import list_active, ui_components_for_suggestions
                    for _scomp in ui_components_for_suggestions(
                        await list_active(user_id, session_id)
                    ):
                        yield emit(CustomEvent(name="zoe.ui_component", value=_scomp))
                except Exception as _psc:
                    logger.debug("pending suggestion cards (non-fatal): %s", _psc)
                asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, response_text))
                # Persist assistant reply so Zoe Agent has context on the next turn.
                # Awaited (not fire-and-forget) so `persisted_assistant` reflects
                # whether the row actually landed: marking it persisted while the
                # scheduled save could still fail would make the error handler skip
                # the truncated-partial fallback and silently lose the reply.
                if response_text:
                    persisted_assistant = await _save_chat_message(
                        session_id, "assistant", response_text, user_id=user_id
                    )
                    if not persisted_assistant:
                        raise RuntimeError("assistant reply save failed")

            else:
                # Explicit OpenClaw fallback path.
                yield emit(
                    StateSnapshotEvent(
                        type=EventType.STATE_SNAPSHOT,
                        snapshot={
                            "status": "generating",
                            "phase": "openclaw",
                            "model": "Zoe (OpenClaw explicit fallback)",
                            "detail": "Starting OpenClaw because it was explicitly requested…",
                        },
                    )
                )
                oc_message = openclaw_user_message(intent, message_for_processing)
                yield emit(
                    CustomEvent(
                        name="zoe.run_log",
                        value={
                            "level": "info",
                            "message": "Starting OpenClaw explicit fallback (browser and tools can take 30s to several minutes).",
                        },
                    )
                )
                _oc2_portrait, oc_db_memory, _oc2_semantic = await asyncio.gather(
                    _safe_load_portrait(user_id),
                    _mempalace_load_user_facts(user_id),
                    _build_memory_context(message_for_processing, user_id=user_id),
                )
                _oc2_full_mem = "\n\n".join(filter(None, [_oc2_portrait, oc_db_memory, _oc2_semantic]))
                task = asyncio.create_task(
                    run_openclaw_agent(
                        oc_message,
                        session_id,
                        user_id,
                        user_role=user_role,
                        username=username,
                        memories=_oc2_full_mem or None,
                        allow_openclaw=True,
                    )
                )
                try:
                    async for hb in _iter_openclaw_heartbeats(emit, task):
                        yield hb
                    response_text = await task
                finally:
                    await _cancel_if_pending(task)
                _, actions = _extract_ui_actions(response_text)
                if actions:
                    asyncio.ensure_future(_queue_ui_actions_background(actions, user_id, session_id))
                asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, response_text))
                if response_text:
                    asyncio.ensure_future(_save_chat_message(session_id, "assistant", response_text, user_id=user_id))
                async for line in _stream_openclaw_assistant_ag(
                    enc, recorder, assistant_message_id, response_text
                ):
                    yield line

        if task_class == "research":
            backend = "openclawLocal" if force_openclaw or not _USE_ZOE_AGENT else "zoeAgent"
            pkg = await _build_research_package(
                query=message_for_processing,
                response_text=response_text,
                backend=backend,
                user_id=user_id,
                session_id=session_id,
            )
            yield emit(
                CustomEvent(
                    name="zoe.ui_component",
                    value={"component": "research_evidence", "props": pkg},
                )
            )

        yield emit(
            RunFinishedEvent(type=EventType.RUN_FINISHED, thread_id=session_id, run_id=run_id)
        )
        await _record_run_state(
            run_id,
            session_id,
            user_id,
            mode=run_mode,
            status="completed",
            request_text=message,
            response_text=response_text,
        )
    except Exception as e:
        logger.exception("Error in chat stream: %s", e)
        # P3-A: if the brain streamed tokens before failing, the user already saw
        # them. Don't drop that partial — persist it (flagged truncated) so the
        # next turn's history matches the screen. Skip if the normal save already
        # ran (persisted_assistant) to avoid a duplicate row. CancelledError is a
        # BaseException and never reaches here, so the cancellation path is intact.
        if full_response.strip() and not persisted_assistant:
            try:
                await _save_chat_message(
                    session_id, "assistant", full_response, user_id=user_id, truncated=True
                )
            except Exception:
                logger.debug("partial persist on error failed (non-fatal)", exc_info=True)
        yield emit(
            RunErrorEvent(
                type=EventType.RUN_ERROR,
                message="Something went wrong. Please try again.",
                code="internal_error",
            )
        )
        await _record_run_state(run_id, session_id, user_id, mode=run_mode, status="error", request_text=message, response_text=str(e))
    finally:
        await _persist_ag_ui_run(session_id, run_id, recorder.events)


_HERMES_API_URL = os.environ.get("HERMES_API_URL", "http://127.0.0.1:8642")
_HERMES_MODEL   = os.environ.get("HERMES_MODEL", "hermes-agent")
_HERMES_API_KEY = (
    os.environ.get("HERMES_API_KEY")
    or os.environ.get("API_SERVER_KEY")
    or ""
)

_ZOE_SOUL_HERMES = (
    "You are Zoe — a warm, curious, genuinely present AI companion. "
    "You know the person you're talking to well. You speak naturally, "
    "not as a task executor but as someone who cares about them. "
    "Draw on the context provided to give responses that feel personal and considered."
)


def _build_hermes_payload(
    message: str,
    *,
    username: str = "",
    portrait: str = "",
    facts: str = "",
    stream: bool,
) -> tuple[dict, int]:
    _zoe_compact = _load_zoe_self_compact_for_chat()
    _ctx_parts = [_ZOE_SOUL_HERMES]
    if _zoe_compact:
        _ctx_parts.append(f"[System context: {_zoe_compact}]")
    if username:
        _ctx_parts.append(f"[Talking to: {username}]")
    if portrait:
        _ctx_parts.append(f"[About this person: {portrait}]")
    if facts:
        _ctx_parts.append(f"[Memory context:\n{facts}]")
    _enhanced_message = "\n".join(_ctx_parts) + "\n\n" + message
    return (
        {
            "model": _HERMES_MODEL,
            "messages": [{"role": "user", "content": _enhanced_message}],
            "stream": stream,
        },
        len(_enhanced_message) // 4,
    )


def _hermes_progress_message(event_name: str, payload) -> tuple[str, str]:
    if not isinstance(payload, dict):
        text = str(payload or event_name)
        return "Hermes", text
    tool = (
        payload.get("tool")
        or payload.get("tool_name")
        or payload.get("name")
        or payload.get("skill")
        or "Hermes"
    )
    detail = (
        payload.get("message")
        or payload.get("detail")
        or payload.get("status")
        or payload.get("phase")
        or payload.get("step")
        or event_name
    )
    return str(tool), str(detail)


def _hermes_progress_events(event_name: str, payload) -> list:
    tool, detail = _hermes_progress_message(event_name, payload)
    label = f"{tool}: {detail}" if tool and tool != "Hermes" else detail
    return [
        StateSnapshotEvent(
            type=EventType.STATE_SNAPSHOT,
            snapshot={
                "status": "generating",
                "phase": "hermes_tool" if tool and tool != "Hermes" else "hermes",
                "model": "Hermes Agent",
                "detail": label,
                "event": event_name,
            },
        ),
        CustomEvent(
            name="zoe.run_log",
            value={
                "level": "info",
                "message": label,
                "source": "hermes",
                "phase": "hermes_tool" if tool and tool != "Hermes" else "hermes",
                "event": event_name,
                "payload": payload if isinstance(payload, dict) else {"value": payload},
            },
        ),
    ]


def _hermes_request_headers(*, session_id: str | None = None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if _HERMES_API_KEY:
        headers["Authorization"] = f"Bearer {_HERMES_API_KEY}"
    if session_id:
        headers["X-Hermes-Session-Id"] = session_id
    return headers


async def _iter_hermes_stream_events(
    message: str,
    session_id: str,
    user_id: str,
    *,
    username: str = "",
    portrait: str = "",
    facts: str = "",
):
    """Yield Hermes stream events; callers own AG-UI lifecycle and persistence."""
    import aiohttp

    full_text: list[str] = []
    _t0 = asyncio.get_event_loop().time()
    _hermes_error = False
    payload, _hermes_prompt_tokens = _build_hermes_payload(
        message,
        username=username,
        portrait=portrait,
        facts=facts,
        stream=True,
    )
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{_HERMES_API_URL}/v1/chat/completions",
                json=payload,
                headers=_hermes_request_headers(session_id=session_id),
                timeout=aiohttp.ClientTimeout(total=120),
            ) as resp:
                resp.raise_for_status()
                sse_event = ""
                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if line.startswith("event:"):
                        sse_event = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    if sse_event and sse_event != "message":
                        try:
                            progress_payload = json.loads(data_str)
                        except json.JSONDecodeError:
                            progress_payload = {"message": data_str}
                        yield {"kind": "progress", "event": sse_event, "payload": progress_payload}
                        sse_event = ""
                        continue
                    sse_event = ""
                    try:
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        token = delta.get("content", "")
                    except (json.JSONDecodeError, IndexError, KeyError):
                        continue
                    if token:
                        full_text.append(token)
                        yield {"kind": "token", "text": token}
    except Exception as exc:
        _hermes_error = True
        error_token = f"\n\n*[Hermes Agent error: {exc}]*"
        full_text.append(error_token)
        yield {"kind": "token", "text": error_token}
    finally:
        # Log to llm_call_log so evolution_notice can include Hermes in health checks.
        # latency_ms < 0 is used as the error signal by evolution_notice.py.
        _latency_ms = int((asyncio.get_event_loop().time() - _t0) * 1000)
        _completion_tokens = len("".join(full_text)) // 4

        async def _log_hermes_call():
            try:
                from db_pool import get_db_ctx as _get_pg_db  # type: ignore[import]
                import uuid as _uuid, time as _time
                async with _get_pg_db() as _db:
                    await _db.execute(
                        """INSERT INTO llm_call_log
                           (id, agent_tier, model, session_id, user_id,
                            latency_ms, prompt_tokens, completion_tokens, estimated_cost_usd, ts)
                           VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                        _uuid.uuid4().hex, "hermes", _HERMES_MODEL,
                        session_id, user_id,
                        _latency_ms if not _hermes_error else -1,
                        _hermes_prompt_tokens, _completion_tokens,
                        0.0,
                        _time.time(),
                    )
            except Exception as _exc:
                logger.warning(
                    "chat: hermes llm_call_log insert failed for session=%s — "
                    "call NOT accounted: %s", session_id, _exc)

        asyncio.ensure_future(_log_hermes_call())


async def _iter_hermes_text_chunks(
    message: str,
    session_id: str,
    user_id: str,
    *,
    username: str = "",
    portrait: str = "",
    facts: str = "",
):
    """Yield Hermes text tokens only; callers own AG-UI lifecycle and persistence."""
    async for event in _iter_hermes_stream_events(
        message,
        session_id,
        user_id,
        username=username,
        portrait=portrait,
        facts=facts,
    ):
        if event.get("kind") == "token":
            yield event.get("text", "")

def _load_zoe_self_compact_for_chat() -> str:
    """Load compact Zoe self-description from file; falls back to empty string."""
    try:
        _p = os.path.expanduser("~/.zoe/zoe_self_compact.txt")
        with open(_p) as _f:
            return _f.read().strip()
    except Exception:
        return ""


async def _safe_load_portrait(user_id: str) -> str:
    """Load portrait for context injection; returns '' on any error or missing table."""
    try:
        from user_portrait import load_portrait  # type: ignore[import]
        return await load_portrait(user_id) or ""
    except Exception:
        return ""


async def _hermes_completion(
    message: str,
    session_id: str,
    user_id: str,
    *,
    username: str = "",
    portrait: str = "",
    facts: str = "",
) -> str:
    """Return a non-streaming Hermes response with Zoe context attached."""
    import aiohttp
    payload, _ = _build_hermes_payload(
        message,
        username=username,
        portrait=portrait,
        facts=facts,
        stream=False,
    )
    async with aiohttp.ClientSession() as _hses:
        async with _hses.post(
            f"{_HERMES_API_URL}/v1/chat/completions",
            json=payload,
            headers=_hermes_request_headers(),
            timeout=aiohttp.ClientTimeout(total=120),
        ) as _hr:
            _hr.raise_for_status()
            _hj = await _hr.json()
    return _hj.get("choices", [{}])[0].get("message", {}).get("content", "") or "(no response)"


async def _hermes_stream_generator(
    message: str, session_id: str, user_id: str,
    *, username: str = "", portrait: str = "", facts: str = "",
):
    """Standalone Hermes AG-UI stream; caller owns user-turn persistence."""
    enc = EventEncoder()
    recorder = AgRunRecorder()
    run_id = uuid.uuid4().hex
    assistant_message_id = uuid.uuid4().hex

    yield recorder.emit(enc, RunStartedEvent(type=EventType.RUN_STARTED, run_id=run_id, thread_id=session_id))
    yield recorder.emit(enc, StateSnapshotEvent(
        type=EventType.STATE_SNAPSHOT,
        snapshot={"status": "generating", "phase": "hermes", "model": "Hermes Agent", "detail": "Thinking…"},
    ))
    yield recorder.emit(enc, TextMessageStartEvent(type=EventType.TEXT_MESSAGE_START, message_id=assistant_message_id, role="assistant"))

    full_text = []
    async for hermes_event in _iter_hermes_stream_events(
        message,
        session_id,
        user_id,
        username=username,
        portrait=portrait,
        facts=facts,
    ):
        if hermes_event.get("kind") == "progress":
            for progress_event in _hermes_progress_events(
                hermes_event.get("event", "hermes.progress"),
                hermes_event.get("payload", {}),
            ):
                yield recorder.emit(enc, progress_event)
            continue
        token = hermes_event.get("text", "")
        full_text.append(token)
        yield recorder.emit(enc, TextMessageChunkEvent(
            type=EventType.TEXT_MESSAGE_CHUNK,
            message_id=assistant_message_id,
            role="assistant",
            delta=token,
        ))

    yield recorder.emit(enc, TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=assistant_message_id))
    yield recorder.emit(enc, RunFinishedEvent(type=EventType.RUN_FINISHED, run_id=run_id, thread_id=session_id))

    response_text = "".join(full_text)
    if response_text.strip():
        asyncio.ensure_future(_save_chat_message(session_id, "assistant", response_text, user_id=user_id))
        if user_id != "guest":
            asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message, response_text))
        asyncio.ensure_future(
            _record_run_state(
                session_id=session_id,
                user_id=user_id,
                mode="hermes",
                run_id=run_id,
                status="completed",
                request_text=message,
                response_text=response_text,
            )
        )


def _resolve_channel(body: dict) -> str:
    """Normalize the optional per-channel tag from a chat request body.

    Phase 1 (additive): a channel may identify itself via an optional `channel`
    field (e.g. "telegram") so `fast_tiers.resolve()` can select its
    CHANNEL_PROFILES entry. Only a KNOWN channel is honored — a missing, blank,
    non-string (the body is raw JSON, so `channel` could be `123`/`true`/an
    object), or unknown/typo'd value resolves to "chat", the historical hardcoded
    default. This keeps web/voice/touch byte-identical to before the field
    existed, never 500s on a malformed field, and ensures an unrecognized channel
    can't silently widen behaviour (an unknown tag yields an empty profile whose
    writes default to allowed — falling back to "chat" keeps writes deferred).
    """
    raw = body.get("channel")
    if not isinstance(raw, str):
        return "chat"
    channel = raw.strip().lower()
    if not channel:
        return "chat"
    try:
        import fast_tiers as _ft

        known = channel in _ft.CHANNEL_PROFILES
    except Exception:
        known = channel == "chat"
    return channel if known else "chat"


@router.post("/")
async def chat(request: Request, user: dict = Depends(resolve_acting_user), stream: bool = True):
    body = await request.json()
    message = body.get("message", "")
    session_id = body.get("session_id", f"web_{uuid.uuid4().hex[:8]}")
    user_id = user["user_id"]
    force_agent: str = body.get("force_agent", "auto")  # 'auto' | 'hermes' | legacy 'openclaw'
    # OpenClaw remains available, but only for explicit manual requests.
    force_openclaw = bool(body.get("force_openclaw", False)) or (force_agent == "openclaw")
    req_panel_id: str | None = body.get("panel_id") or None
    # Optional per-channel tag (Phase 1: additive) — see _resolve_channel.
    req_channel: str = _resolve_channel(body)
    is_voice_mode = request.headers.get("X-Voice-Mode", "").lower() in ("true", "1", "yes")
    voice_max_tokens = int(body.get("max_tokens", 0)) if is_voice_mode else 0

    if not message:
        return {"error": "No message provided"}

    await _ensure_user_and_chat_session(session_id, user_id)

    if stream:
        # Wrap the generator with a per-session lock so parallel SSE connections
        # for the same session don't interleave long-running agent calls.
        async def _locked_stream():
            lock = _get_session_lock(session_id)
            try:
                acquired = await asyncio.wait_for(lock.acquire(), timeout=_SESSION_LOCK_TIMEOUT_S)
            except asyncio.TimeoutError:
                acquired = False
            if not acquired:
                logger.warning("session %s concurrency timeout — rejecting duplicate request", session_id)
                enc = EventEncoder()
                err_ev = enc.encode(RunErrorEvent(type=EventType.RUN_ERROR, message="Another request is already in progress for this session. Please wait.", code="session_busy"))
                yield err_ev
                return
            try:
                async for chunk in chat_stream_generator(
                    message,
                    session_id,
                    user,
                    force_openclaw=force_openclaw,
                    force_agent=force_agent,
                    req_panel_id=req_panel_id,
                    channel=req_channel,
                ):
                    yield chunk
            finally:
                lock.release()

        return StreamingResponse(
            _locked_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    else:
        approval_token, message_for_processing = _extract_approval_token(message)
        # Persist the user turn on the non-stream path as well. The stream
        # path does this inside chat_stream_generator; without this the
        # nightly digest would see an empty chat_messages table for any
        # voice / CLI clients that opt out of SSE.
        if message:
            await _save_chat_message(session_id, "user", message, user_id=user_id)
        if approval_token:
            approved = await _resolve_approval(user_id, approval_token)
            if not approved:
                return {"error": "Invalid approval token", "session_id": session_id}
            message_for_processing = approved.get("request_text") or message_for_processing

        if _GUARDED_AUTO and not approval_token:
            risk = classify_request(message_for_processing)
            if risk.requires_confirmation:
                approval_id = await _create_pending_approval(
                    user_id=user_id,
                    session_id=session_id,
                    message=message_for_processing,
                    risk_level=risk.level,
                    reason=risk.reason,
                    normalized_action=risk.normalized_action,
                )
                return {
                    "response": "Approval required before executing this action.",
                    "session_id": session_id,
                    "ui_components": [
                        {
                            "component": "confirmation",
                            "props": {
                                "description": f"{risk.reason}. Approve to continue.",
                                "yes_text": "Approve",
                                "no_text": "Cancel",
                                "yes_action": f"/approve {approval_id}",
                            },
                        }
                    ],
                }

        lc = message_for_processing.lower().strip()
        task_class = classify_query(message_for_processing)
        if task_class == "research":
            missing = missing_brief_fields(message_for_processing)
            if missing:
                followup = _research_followup_prompt(missing)
                await _save_chat_message(session_id, "assistant", followup, user_id=user_id)
                return {
                    "response": followup,
                    "session_id": session_id,
                    "ui_components": [
                        {
                            "component": "status",
                            "props": {
                                "level": "info",
                                "message": "Research brief incomplete - waiting for constraints.",
                            },
                        }
                    ],
                }
        if "what can you do right now" in lc or lc in {"/capabilities", "capabilities", "tools"}:
            try:
                caps_text = Path("/home/zoe/assistant/CAPABILITIES.md").read_text()[:12000]
            except Exception:
                caps_text = "Hermes is the active escalation agent. Zoe tools include calendar, lists, reminders, memory, Graphify, Multica, and CloakBrowser."
            capabilities_text = "Hermes/Zoe capabilities:\n\n" + caps_text
            await _save_chat_message(session_id, "assistant", capabilities_text, user_id=user_id)
            return {"response": capabilities_text, "session_id": session_id}

        use_intent_fast_path = (not force_openclaw) and _ALL_TOOLS_ENABLED
        if task_class == "research":
            # Keep research prompts on the evidence-producing flow.
            use_intent_fast_path = False
        if message_for_processing.startswith("/openclaw "):
            message_for_processing = message_for_processing[len("/openclaw ") :].strip()
            force_openclaw = True
            use_intent_fast_path = False

        if use_intent_fast_path:
            # Tier-1.5 channel-agnostic fast path (the SAME core voice uses): answer
            # calendar / lists / weather / time / people / memory sub-second instead
            # of paying ~5s for the Pi-hybrid/brain lane below. Threshold-gated inside
            # expert_dispatch, so only confident matches short-circuit; everything else
            # returns None and falls through to the hybrid lane unchanged. Non-fatal.
            # Keep ONLY resolve() inside the try so a fast-path miss/error falls
            # through to the hybrid lane. The success branch is OUTSIDE the try:
            # if persistence raises it must NOT be swallowed (that would re-run the
            # turn on the brain and return a different reply than we injected).
            _fp_res = None
            try:
                import fast_tiers as _fast_path
                _fp_res = await _fast_path.resolve(
                    message_for_processing, user_id, session_id,
                    channel=req_channel,
                )
            except Exception as _fp_exc:  # never let the fast path break a turn
                logger.debug("chat fast_path resolve failed (non-fatal): %s", _fp_exc)
                _fp_res = None
            if _fp_res is not None and getattr(_fp_res, "reply", ""):
                _fp_reply = _fp_res.reply
                asyncio.ensure_future(
                    chat_inject_background(
                        message_for_processing, _fp_reply,
                        f"fast:{_fp_res.domain}", user_id, session_id,
                    )
                )
                asyncio.ensure_future(
                    _persist_memory_candidates(
                        user_id, session_id, message_for_processing, _fp_reply
                    )
                )
                await _save_chat_message(session_id, "assistant", _fp_reply, user_id=user_id)
                return {"response": _fp_reply, "session_id": session_id}
            _pi_hybrid = await _run_chat_pi_hybrid_lane(
                message_for_processing,
                user_id=user_id,
                session_id=session_id,
                context=_CHAT_CONTEXTS.get(session_id),
                request_text=message,
                record_run_state=True,
                panel_id=req_panel_id,
            )
            if _pi_hybrid.get("accepted"):
                _pi_cue = _pi_hybrid.get("cue") or {}
                _pi_decision = _pi_hybrid.get("decision") or {}
                return {
                    "response": _pi_hybrid.get("response_text") or "",
                    "session_id": session_id,
                    "processing_cue": {
                        "available": bool(_pi_cue.get("available")),
                        "text": _pi_cue.get("text") or "",
                        "event": _pi_cue.get("event"),
                    },
                    "pi_hybrid": {
                        "accepted": True,
                        "reason": _pi_decision.get("reason"),
                        "intent": _pi_decision.get("intent"),
                        "intent_group": _pi_decision.get("intent_group"),
                        "agreement_kind": _pi_decision.get("agreement_kind"),
                        "execution_scope": _pi_decision.get("execution_scope"),
                        "action_form": _pi_hybrid.get("action_form"),
                    },
                }

        intent = await detect_and_extract_intent(message_for_processing, user_id) if use_intent_fast_path else None
        # Save original message before appending voice suffix; run_zoe_agent needs the
        # clean text so _check_fast_response (greetings/acks) still matches correctly.
        _original_message_for_agent = message_for_processing
        # Apply voice mode suffix AFTER intent detection so regex anchors ($) still match.
        if is_voice_mode and message_for_processing:
            try:
                from routers.voice_tts import _VOICE_SYSTEM_PROMPT_SUFFIX  # type: ignore
                message_for_processing = message_for_processing + "\n" + _VOICE_SYSTEM_PROMPT_SUFFIX
            except ImportError:
                pass
        if intent:
            # Fire intent navigation to the touch panel immediately (non-blocking).
            # This is the fix for _broadcast_intent_nav being dead code — it is now called
            # on every non-streaming request that has a panel_id (i.e. voice commands).
            if req_panel_id and intent.name in _INTENT_PANEL_NAV:
                asyncio.ensure_future(_broadcast_intent_nav(intent, panel_id=req_panel_id))
            result = await execute_intent(intent, user_id)
            if result:
                asyncio.ensure_future(
                    chat_inject_background(message_for_processing, result, intent.name, user_id, session_id)
                )
                asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, result))
                await _save_chat_message(session_id, "assistant", result, user_id=user_id)
                return {"response": result, "session_id": session_id}
        if _WHATSAPP_FLOW_ENABLED and is_whatsapp_connect_request(message_for_processing):
            message_for_processing = (
                "Connect WhatsApp integration for user with full guided flow: preflight checks, "
                "credential/session validation, qr/session setup, webhook test, remediation."
            )

        response_text = ""
        try:
            # Portrait is cheap and the local brain takes it directly, so load it
            # eagerly. Facts + semantic recall (ns_full_mem) are ONLY consumed by the
            # Hermes/OpenClaw escalation branches and the non-local-brain path — the
            # local Pi brain gets its memory from the memory.ts extension's
            # /api/memories/for-prompt packet (db_memory_context=None below). So
            # compute them LAZILY to avoid a redundant facts-load + semantic search
            # on every non-escalating turn (the recall double-load this PR removes).
            ns_portrait = await _safe_load_portrait(user_id)
            _ns_full_mem_cache: "str | None" = None

            async def _ns_full_mem() -> str:
                nonlocal _ns_full_mem_cache
                if _ns_full_mem_cache is None:
                    _dbm, _sem = await asyncio.gather(
                        _mempalace_load_user_facts(user_id),
                        _build_memory_context(message_for_processing, user_id=user_id),
                    )
                    _ns_full_mem_cache = "\n\n".join(filter(None, [ns_portrait, _dbm, _sem]))
                return _ns_full_mem_cache

            if _USE_LOCAL_BRAIN:
                # Use original (pre-suffix) message for agent so fast-path checks work
                _agent_msg = _original_message_for_agent if is_voice_mode else message_for_processing
                expanded_msg = openclaw_user_message(intent, _agent_msg) if intent else _agent_msg
                response_text = await _brain_oneshot(
                    expanded_msg, session_id, user_id,
                    portrait=ns_portrait,
                    db_memory_context=None,
                    max_tokens_override=voice_max_tokens,
                    voice_mode=is_voice_mode,
                )
                # If Zoe Agent signals escalation, route accordingly
                if response_text.startswith("__ESCALATE_HERMES__:"):
                    _, escalate_body = response_text.split(":", 1)
                    _, _, hermes_task = escalate_body.partition("|")
                    try:
                        response_text = await _hermes_completion(
                            hermes_task or message_for_processing,
                            session_id,
                            user_id,
                            username=user.get("username") or "",
                            portrait=ns_portrait,
                            facts=await _ns_full_mem() or "",
                        )
                    except Exception as _he:
                        logger.warning("Hermes non-stream escalation failed: %s", _he)
                        response_text = "I couldn't reach Hermes right now. Please try again."
                elif response_text.startswith("__ESCALATE__:"):
                    _, escalate_body = response_text.split(":", 1)
                    _, _, oc_task = escalate_body.partition("|")
                    response_text = await _hermes_completion(
                        oc_task or message_for_processing,
                        session_id,
                        user_id,
                        username=user.get("username") or "",
                        portrait=ns_portrait,
                        facts=await _ns_full_mem() or "",
                    )
            else:
                oc_message = openclaw_user_message(intent, message_for_processing)
                response_text = await _hermes_completion(
                    oc_message,
                    session_id,
                    user_id,
                    username=user.get("username") or "",
                    portrait=ns_portrait,
                    facts=await _ns_full_mem() or "",
                )
        except Exception as exc:
            if task_class != "research":
                raise
            logger.exception("research execution failed; using deterministic fallback: %s", exc)
            response_text = (
                "I could not complete live browsing just now, so I prepared a deterministic "
                "research brief with source links and evidence placeholders."
            )
        # Never hand the user a blank turn. Under heavy concurrent load the local
        # brain can occasionally return no text (Pi-RPC subprocess thrash); surface
        # a graceful retry prompt instead of an empty response. Skip for research
        # tasks — those have their own evidence-package flow below and must not feed
        # a retry string into _build_research_package.
        if task_class != "research" and not (response_text or "").strip():
            logger.warning("chat non-stream: empty brain response, using fallback (session=%s)", session_id)
            response_text = "Sorry, I didn't catch that — could you say it again?"
        clean_text, actions = _extract_ui_actions(response_text)
        resp = {"response": clean_text, "session_id": session_id}
        ui_commands = [a for a in actions if "command" in a]
        ui_components = [a for a in actions if "component" in a]
        if ui_commands:
            resp["ui_commands"] = ui_commands
        if ui_components:
            resp["ui_components"] = ui_components
        if task_class == "research":
            pkg = await _build_research_package(
                query=message_for_processing,
                response_text=response_text,
                backend="openclawLocal" if force_openclaw or not _USE_ZOE_AGENT else "zoeAgent",
                user_id=user_id,
                session_id=session_id,
            )
            resp.setdefault("ui_components", [])
            resp["ui_components"].append({"component": "research_evidence", "props": pkg})
            if req_panel_id:
                # Push a touch-optimized report card automatically for research tasks.
                # get_db_ctx, not `async for db in get_db()`: exiting the generator
                # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
                async with get_db_ctx() as db:
                    try:
                        await enqueue_ui_action(
                            db,
                            user_id=user_id,
                            action_type="panel_show_research_report",
                            payload={"package": pkg, "panel_id": req_panel_id},
                            requested_by="chat",
                            panel_id=req_panel_id,
                            chat_session_id=session_id,
                            idempotency_key=f"{session_id}:research:{uuid.uuid4().hex[:8]}",
                        )
                        await db.commit()
                    except Exception as exc:
                        # Non-fatal: research response should still return in chat
                        # even if panel action delivery fails.
                        logger.warning("research panel push skipped: %s", exc)
        if actions:
            asyncio.ensure_future(_queue_ui_actions_background(actions, user_id, session_id))
        asyncio.ensure_future(_persist_memory_candidates(user_id, session_id, message_for_processing, response_text))
        if response_text:
            await _save_chat_message(session_id, "assistant", response_text, user_id=user_id)
        return resp


@router.get("/sessions/")
async def list_sessions(user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    # get_db_ctx, not `async for db in get_db()`: exiting the generator
    # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
    async with get_db_ctx() as db:
        rows = await db.execute_fetchall(
            """SELECT s.id, s.title, s.created_at, s.updated_at,
                      (SELECT COUNT(*) FROM chat_messages WHERE session_id = s.id) AS message_count
               FROM chat_sessions s WHERE s.user_id = ? ORDER BY s.updated_at DESC LIMIT 50""",
            (user_id,),
        )
        sessions = [dict(r) for r in rows]
        return {"sessions": sessions, "count": len(sessions)}


@router.post("/sessions/")
async def create_session(request: Request, user: dict = Depends(get_current_user)):
    body = await request.json()
    session_id = f"web_{uuid.uuid4().hex[:8]}"
    user_id = user["user_id"]
    title = body.get("title", "New Chat")
    async for db in get_db():
        await db.execute(
            "INSERT INTO chat_sessions (id, user_id, title) VALUES (?, ?, ?)",
            (session_id, user_id, title),
        )
        await db.commit()
    return {"session_id": session_id, "title": title, "created_at": "now"}


@router.get("/sessions/{session_id}/messages/")
async def get_session_messages(session_id: str, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    # get_db_ctx, not `async for db in get_db()`: exiting the generator
    # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
    async with get_db_ctx() as db:
        owner = await db.execute_fetchall(
            "SELECT id FROM chat_sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
        )
        if not owner:
            return {"messages": [], "count": 0}
        rows = await db.execute_fetchall(
            "SELECT id, role, content, metadata, created_at FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
            (session_id,),
        )
        messages = [dict(r) for r in rows]
        return {"messages": messages, "count": len(messages)}


@router.post("/sessions/{session_id}/messages/")
async def save_message(session_id: str, request: Request, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    body = await request.json()
    msg_id = uuid.uuid4().hex[:12]
    role = body.get("role", "user")
    content = body.get("content", "")
    metadata = json.dumps(body.get("metadata")) if body.get("metadata") else None
    # get_db_ctx, not `async for db in get_db()`: exiting the generator
    # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
    async with get_db_ctx() as db:
        await db.execute(
            "INSERT INTO users (id, name, role) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
            (user_id, user_id, "member"),
        )
        await db.execute(
            "INSERT INTO chat_sessions (id, user_id, title) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
            (session_id, user_id, "New Chat"),
        )
        owner = await db.execute_fetchall(
            "SELECT id FROM chat_sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
        )
        if not owner:
            await db.commit()
            return {"status": "error", "message": "Session not found"}
        await db.execute(
            "INSERT INTO chat_messages (id, session_id, role, content, metadata) VALUES (?, ?, ?, ?, ?)",
            (msg_id, session_id, role, content, metadata),
        )

        await _touch_chat_session(db, session_id=session_id, user_id=user_id, content=content)
        await db.commit()
    return {"status": "ok", "id": msg_id}


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    # get_db_ctx, not `async for db in get_db()`: exiting the generator
    # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
    async with get_db_ctx() as db:
        owner = await db.execute_fetchall(
            "SELECT id FROM chat_sessions WHERE id = ? AND user_id = ?", (session_id, user_id)
        )
        if not owner:
            return {"status": "error", "message": "Session not found"}
        await db.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
        await db.execute("DELETE FROM chat_sessions WHERE id = ? AND user_id = ?", (session_id, user_id))
        await db.commit()
    return {"status": "ok"}


@router.get("/capabilities")
async def chat_capabilities(user: dict = Depends(get_current_user)):
    try:
        caps_text = Path("/home/zoe/assistant/CAPABILITIES.md").read_text()
    except Exception:
        caps_text = ""
    return {"ok": True, "agent": "hermes", "capabilities_markdown": caps_text}


@router.post("/whatsapp/connect")
async def whatsapp_connect(request: Request, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    body = await request.json()
    session_id = body.get("session_id", f"web_{uuid.uuid4().hex[:8]}")
    approved = bool(body.get("approved", False))
    if not approved:
        approval_id = await _create_pending_approval(
            user_id=user_id,
            session_id=session_id,
            message="Connect to WhatsApp",
            risk_level="high",
            reason="External account linking requires confirmation",
            normalized_action="connect to whatsapp",
        )
        return {
            "status": "approval_required",
            "approval_id": approval_id,
            "prompt": f"/approve {approval_id}",
        }
    guidance = (
        "Connect WhatsApp integration for user with full guided flow: "
        "preflight checks, credential/session validation, qr/session setup, "
        "webhook test message validation, and remediation on failures."
    )
    response_text = await run_openclaw_agent(
        guidance,
        session_id,
        user_id,
        user_role=user.get("role"),
        username=user.get("username"),
    )
    return {"status": "ok", "response": response_text}


@router.get("/approvals/pending")
async def pending_approvals(limit: int = 20, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    # get_db_ctx, not `async for db in get_db()`: exiting the generator
    # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
    async with get_db_ctx() as db:
        rows = await db.execute_fetchall(
            """SELECT id, session_id, request_text, risk_level, reason, created_at
               FROM openclaw_approvals
               WHERE user_id = ? AND status = 'pending'
               ORDER BY created_at DESC
               LIMIT ?""",
            (user_id, limit),
        )
        return {"items": [dict(r) for r in rows], "count": len(rows)}


@router.get("/runs/{session_id}/latest")
async def latest_run(session_id: str, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    # get_db_ctx, not `async for db in get_db()`: exiting the generator
    # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
    async with get_db_ctx() as db:
        rows = await db.execute_fetchall(
            """SELECT id, status, request_text, response_text, metadata, started_at, finished_at
               FROM openclaw_run_state
               WHERE session_id = ? AND user_id = ?
               ORDER BY started_at DESC LIMIT 1""",
            (session_id, user_id),
        )
        if not rows:
            return {"run": None}
        row = dict(rows[0])
        row["metadata"] = json.loads(row["metadata"] or "{}")
        return {"run": row}


@router.post("/runs/{session_id}/resume")
async def resume_run(session_id: str, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    # get_db_ctx, not `async for db in get_db()`: exiting the generator
    # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
    async with get_db_ctx() as db:
        rows = await db.execute_fetchall(
            """SELECT request_text FROM openclaw_run_state
               WHERE session_id = ? AND user_id = ?
               ORDER BY started_at DESC LIMIT 1""",
            (session_id, user_id),
        )
        if not rows:
            return {"status": "error", "message": "No run to resume"}
        req = dict(rows[0]).get("request_text")
        return {"status": "ok", "resume_prompt": req}


@router.post("/runs/{session_id}/cancel")
async def cancel_latest_run(session_id: str, user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    # get_db_ctx, not `async for db in get_db()`: exiting the generator
    # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
    async with get_db_ctx() as db:
        await db.execute(
            """UPDATE openclaw_run_state
               SET status='cancelled', finished_at=NOW()::text
               WHERE id = (
                 SELECT id FROM openclaw_run_state
                 WHERE session_id = ? AND user_id = ?
                 ORDER BY started_at DESC LIMIT 1
               )""",
            (session_id, user_id),
        )
        await db.commit()
        return {"status": "ok"}


@router.post("/feedback/{interaction_id}")
async def submit_feedback(interaction_id: str, request: Request, feedback_type: str = "thumbs_up", user: dict = Depends(get_current_user)):
    user_id = user["user_id"]
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    corrected = body.get("corrected_response")
    async for db in get_db():
        await db.execute(
            """INSERT INTO chat_feedback (id, interaction_id, user_id, feedback_type, corrected_response)
               VALUES (?, ?, ?, ?, ?)""",
            (uuid.uuid4().hex[:12], interaction_id, user_id, feedback_type, corrected),
        )
        await db.commit()
    logger.info(f"Feedback {feedback_type} from {user_id} on {interaction_id}")
    messages = {
        "thumbs_up": "Thanks — glad that helped.",
        "thumbs_down": "Thanks — we'll use that to improve.",
        "correction": "Thanks — noted for learning.",
    }
    return {
        "status": "ok",
        "feedback_type": feedback_type,
        "message": messages.get(feedback_type, "Feedback saved."),
    }


# ── Background task endpoints ─────────────────────────────────────────────────

@router.post("/task")
async def create_background_task(request: Request, user: dict = Depends(get_current_user)):
    """Queue a long-running task for background execution.

    Body: {"message": "find the cheapest hotel in Perth under $150"}
    Returns: {"task_id": 42, "ack": "On it! I'll let you know when done."}
    """
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    task_text = body.get("message", "").strip()
    if not task_text:
        return {"error": "message is required"}
    from background_runner import enqueue_background_task
    user_id = user["user_id"]
    task_id = await enqueue_background_task(task=task_text, user_id=user_id)
    return {
        "task_id": task_id,
        "ack": "On it! I'll work on that in the background and let you know when it's done.",
    }


@router.get("/tasks/pending")
async def get_pending_tasks(user: dict = Depends(get_current_user)):
    """Return completed background tasks not yet shown to the user."""
    from background_runner import get_pending_tasks as _get
    user_id = user["user_id"]
    # Inject display name from user dict for personalised "Hey Jason!" message
    user_name = user.get("username") or user.get("display_name") or ""
    tasks = await _get(user_id)
    return {"tasks": tasks, "user_name": user_name}
