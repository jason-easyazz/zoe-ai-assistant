"""SSE/AG-UI protocol mechanics for the chat router (W4-C2).

Pure "turn X into wire events" adapters cut verbatim out of ``routers/chat.py``
— no lane decisions, no router state. ``routers/chat.py`` re-exports every
public and underscore name below (the ``voice_tts`` contract, applied to chat):
existing importers and test monkeypatches keep targeting ``routers.chat``.
"""
import asyncio
import json
import logging
import re
import time

from ag_ui.core import (
    CustomEvent,
    EventType,
    StateSnapshotEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallArgsEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from ag_ui.encoder import EventEncoder

from ag_ui_stream import AgRunRecorder, iter_openclaw_text_chunks
from db_pool import get_db_ctx
from ui_orchestrator import enqueue_ui_action
from zoe_ui_components import auto_extract_components

logger = logging.getLogger(__name__)


def brain_tool_sentinel_events(sentinel, *, assistant_message_id, tool_names):
    """Map a brain ``__TOOL__:`` sentinel to canonical AG-UI tool/step events.

    The Pi brain (zoe_core_client._read_turn) surfaces tool activity as JSON
    sentinels with phases start / args / result. We translate each phase to the
    same event shapes the intent path emits (StepStarted+ToolCallStart, then
    ToolCallArgs+ToolCallEnd, then ToolCallResult+StepFinished) so a brain turn
    that calls a tool produces TOOL_CALL_START → ARGS → END → RESULT (+STEP_*) in
    order. ``tool_names`` is a caller-owned dict tracking id→name across the
    stream so the result phase (which often omits the name) can finish the step
    under the same name it started. Yields AG-UI event objects; the caller emits
    them. Malformed sentinels yield nothing (logged, never raise).
    """
    try:
        tc = json.loads(sentinel[len("__TOOL__:"):])
    except Exception as exc:  # noqa: BLE001 - malformed sentinel must not break the turn
        logger.debug("__TOOL__ parse error (non-fatal): %s", exc)
        return
    phase = tc.get("phase")
    tc_id = str(tc.get("id") or "")
    tc_name = str(tc.get("name") or "")
    if phase == "start" and tc_id and tc_name:
        tool_names[tc_id] = tc_name
        yield StepStartedEvent(type=EventType.STEP_STARTED, step_name=tc_name)
        yield ToolCallStartEvent(
            type=EventType.TOOL_CALL_START,
            tool_call_id=tc_id,
            tool_call_name=tc_name,
            parent_message_id=assistant_message_id,
        )
    elif phase == "args" and tc_id:
        if tc_name:
            tool_names.setdefault(tc_id, tc_name)
        yield ToolCallArgsEvent(
            type=EventType.TOOL_CALL_ARGS,
            tool_call_id=tc_id,
            delta=json.dumps(tc.get("args") or {}),
        )
        yield ToolCallEndEvent(type=EventType.TOOL_CALL_END, tool_call_id=tc_id)
    elif phase == "result" and tc_id:
        yield ToolCallResultEvent(
            type=EventType.TOOL_CALL_RESULT,
            message_id=assistant_message_id,
            tool_call_id=tc_id,
            content=str(tc.get("result", "")),
            role="tool",
        )
        # The result sentinel often omits the name, so resolve via the id→name map
        # (set at start/args), then any inline name, then the id as a last resort.
        yield StepFinishedEvent(
            type=EventType.STEP_FINISHED,
            step_name=tool_names.get(tc_id) or tc_name or tc_id,
        )


# Brain UI tools that should also surface a data-filled card in chat. The Pi
# brain calls these and returns only text (#766/#767 added the live activity
# view); Wave A additionally re-reads read-only and emits the SAME
# {type, data, card} zoe.ui_component the intent fast-path renders, so a brain
# turn that uses calendar/lists/weather shows the result card, not just prose.
# Map: tool name → (read-only show query, chat card `type`, action label).
# (people/reminders have no chat card renderer, so they get the activity view only.)
_BRAIN_UI_TOOL_CARDS = {
    "calendar": ("show my calendar", "calendar", "Showing calendar"),
    "lists": ("show my lists", "list", "Showing lists"),
    "weather": ("what is the weather", "weather", "Showing weather"),
}


async def brain_tool_card_events(sentinel, *, user_id, tool_names, emitted_domains):
    """For a brain ``__TOOL__`` 'result' on a UI domain, fetch the data-filled
    card (read-only, via the existing skybridge resolver) and yield it as the
    same ``{type, data, card}`` ``zoe.ui_component`` the intent fast-path emits.

    Deduped per domain per turn via ``emitted_domains``. Never raises — a failure
    just means no card (the activity view from ``brain_tool_sentinel_events``
    still shows). Yields ``CustomEvent`` objects; the caller emits them.
    """
    try:
        tc = json.loads(sentinel[len("__TOOL__:"):])
    except Exception:  # noqa: BLE001 - malformed sentinel must not break the turn
        return
    if tc.get("phase") != "result":
        return
    tc_id = str(tc.get("id") or "")
    # The result sentinel often omits the name; resolve via the id→name map first.
    name = (str(tc.get("name") or "") or tool_names.get(tc_id, "")).strip().lower()
    spec = _BRAIN_UI_TOOL_CARDS.get(name)
    if spec is None or name in emitted_domains:
        return
    show_query, card_type, action_label = spec
    try:
        from skybridge_service import resolve_skybridge_request

        result = await resolve_skybridge_request(show_query, user_id)
    except Exception as exc:  # noqa: BLE001 - a card failure must not break the turn
        logger.debug("brain tool card build failed for %s: %s", name, exc)
        return
    if not isinstance(result, dict) or not result.get("handled"):
        return
    cards = result.get("cards") or []
    if not cards:
        return
    # Mark emitted only after a handled result with ≥1 card, so a failed/empty
    # first result for a domain doesn't suppress a later successful one this turn.
    emitted_domains.add(name)
    for card in cards:
        yield CustomEvent(
            name="zoe.ui_component",
            value={"type": card_type, "data": {"action": action_label}, "card": card},
        )


async def _iter_openclaw_heartbeats(emit, task: asyncio.Task, *, phase_label: str = "OpenClaw"):
    """Emit run_log + STATE_SNAPSHOT every ~4s while the OpenClaw subprocess runs."""
    t0 = time.monotonic()
    while not task.done():
        await asyncio.wait({task}, timeout=4.0)
        if task.done():
            break
        elapsed = int(time.monotonic() - t0)
        # heartbeat=true tells the frontend to update a single sticky status
        # line rather than appending a new trace card on every tick.
        yield emit(
            CustomEvent(
                name="zoe.run_log",
                value={
                    "level": "info",
                    "heartbeat": True,
                    "message": f"{phase_label} still working… {elapsed}s elapsed (browser and tools can take several minutes).",
                },
            )
        )
        yield emit(
            StateSnapshotEvent(
                type=EventType.STATE_SNAPSHOT,
                snapshot={
                    "status": "generating",
                    "phase": "openclaw",
                    "model": phase_label,
                    "detail": f"Running agent… {elapsed}s",
                },
            )
        )


async def _cancel_if_pending(task: asyncio.Task) -> None:
    """Cancel an agent task that's still running and await its teardown.

    Called from the `finally` of every heartbeat-driven agent block. On a normal
    finish the task is already done and this is a no-op. On SSE client disconnect
    the generator is closed (GeneratorExit thrown at a `yield`), `await task` is
    skipped, and without this the multi-minute browser/agent run would keep going
    orphaned — burning CPU/GPU and holding the single brain slot. We cancel it and
    swallow the resulting CancelledError (and any error it surfaces while tearing
    down) so the original GeneratorExit/exception keeps propagating unchanged.
    """
    if task.done():
        return
    task.cancel()
    try:
        await task
    except (asyncio.CancelledError, Exception):
        logger.debug("agent task cancelled on stream close", exc_info=True)


_UI_MARKER_RE = re.compile(r":::zoe-ui\s*\n(.*?)\n:::", re.DOTALL)


# ── Server-side builder safety net ────────────────────────────────────────────
# OpenClaw's SKILL.md files instruct the agent to emit `:::zoe-ui` navigate +
# orb_prompt blocks after staging a widget/page. These regexes let the server
# detect a successful stage (preview URL mentioned in the response) and
# synthesize the missing navigate/orb_prompt events so the user always sees the
# live preview.
_PREVIEW_WIDGET_URL_RE = re.compile(
    r"(/_preview_harness/widget-harness\.html\?[^\s)\"'`<>]+)"
)
_PREVIEW_PAGE_URL_RE = re.compile(
    r"(/_preview/[a-z0-9_\-]+/[a-z0-9_\-]+\.html(?![a-z0-9_\-]))",
    re.IGNORECASE,
)
# Matches fenced code blocks of any language — stripped from the chat bubble
# for delegation intents so raw JS/HTML never reaches the user.
_FENCED_CODE_BLOCK_RE = re.compile(r"```[\w-]*\n.*?\n```", re.DOTALL)

# Builder intents that we apply the "no code in chat, auto-preview" policy to.
_BUILDER_INTENTS: frozenset[str] = frozenset({"build_widget", "build_page"})


def _detect_preview_urls(text: str) -> tuple[str | None, str | None]:
    """Return (widget_preview_url, page_preview_url) if found in text."""
    widget_m = _PREVIEW_WIDGET_URL_RE.search(text or "")
    page_m = _PREVIEW_PAGE_URL_RE.search(text or "")
    return (
        widget_m.group(1) if widget_m else None,
        page_m.group(1) if page_m else None,
    )


def _synthesize_builder_actions(
    response_text: str,
    explicit_actions: list,
    intent_name: str | None,
) -> list:
    """If a builder run staged a preview but forgot to emit navigate/orb_prompt,
    synthesize those actions here so the user always sees the live preview.

    Returns a list of *additional* actions to emit (never modifies the explicit
    ones). Idempotent — skips emission if the LLM already got it right.
    """
    if intent_name not in _BUILDER_INTENTS:
        return []
    has_navigate = any(
        (a.get("action") or a.get("type")) == "navigate" for a in explicit_actions
    )
    has_orb = any(
        (a.get("action") or a.get("type")) == "orb_prompt" for a in explicit_actions
    )
    widget_url, page_url = _detect_preview_urls(response_text)
    preview_url = widget_url or page_url
    if not preview_url:
        return []  # stage probably didn't happen; nothing to preview
    synth: list = []
    if not has_navigate:
        synth.append({
            "action": "navigate",
            "url": preview_url,
            "target": "iframe",
            "source": "zoe-data.safety_net",
        })
    if not has_orb:
        thing = "this widget" if widget_url else "this page"
        synth.append({
            "action": "orb_prompt",
            "prompt": f"Here's {thing}. Does it look right, or want me to tweak it?",
            "auto_mic": True,
            "source": "zoe-data.safety_net",
        })
    return synth


def _sanitize_builder_reply(clean_text: str, intent_name: str | None,
                            did_auto_preview: bool) -> str:
    """For builder intents, strip code fences from the chat bubble and
    shorten the text if the agent emitted a wall of prose alongside the preview.

    The live preview iframe is the evidence — the user asked for the file to be
    made and shown working, not for a code review in the bubble.
    """
    if intent_name not in _BUILDER_INTENTS:
        return clean_text
    # Strip fenced code blocks entirely.
    stripped = _FENCED_CODE_BLOCK_RE.sub("", clean_text or "").strip()
    # Collapse 3+ blank lines left behind by stripping.
    stripped = re.sub(r"\n{3,}", "\n\n", stripped)
    if did_auto_preview:
        # If the agent rambled, replace with a short confirmation line — the
        # preview iframe + orb prompt are the real UX. Keep at most ~240 chars.
        if len(stripped) > 240:
            stripped = (
                "Here it is — take a look at the preview and let me know if "
                "it's what you wanted, or tell me what to change."
            )
        elif not stripped:
            stripped = (
                "Done — preview below. Does it look right?"
            )
    return stripped


def _extract_ui_actions(text: str):
    """Extract :::zoe-ui JSON blocks from response text. Returns (clean_text, actions)."""
    actions = []
    for match in _UI_MARKER_RE.finditer(text):
        try:
            payload = json.loads(match.group(1).strip())
            actions.append(payload)
        except json.JSONDecodeError:
            pass
    clean = _UI_MARKER_RE.sub("", text).strip()
    return clean, actions


def _map_ui_payload_to_action(action: dict):
    if "command" in action:
        command = action.get("command")
        params = action.get("params", {})
        if command == "navigate":
            return "navigate", params
        if command == "notify":
            return "notify", params
        if command == "refresh_data":
            return "refresh", params
        if command in {"add_widget", "remove_widget"}:
            return "update_record", {"command": command, **params}
        return "highlight", {"command": command, **params}
    if "component" in action:
        return "open_panel", action
    return None, {}


async def _queue_ui_actions_background(actions: list, user_id: str, session_id: str):
    if not actions:
        return
    try:
        # get_db_ctx, not `async for db in get_db()`: exiting the generator
        # early leaks the pooled connection (#953 / the 2026-07-03 pool drain).
        async with get_db_ctx() as db:
            for i, action in enumerate(actions):
                action_type, payload = _map_ui_payload_to_action(action)
                if not action_type:
                    continue
                await enqueue_ui_action(
                    db,
                    user_id=user_id,
                    action_type=action_type,
                    payload=payload,
                    requested_by="chat",
                    chat_session_id=session_id,
                    idempotency_key=f"{session_id}:{action_type}:{i}",
                )
    except Exception as e:
        logger.warning(f"Failed to enqueue UI actions (non-fatal): {e}")


async def _stream_openclaw_assistant_ag(
    enc: EventEncoder,
    recorder: AgRunRecorder,
    assistant_message_id: str,
    response_text: str,
    intent_name: str | None = None,
):
    """TEXT_MESSAGE_* for assistant reply, then CUSTOM zoe.ui_* for generative UI.

    `intent_name` lets the builder safety net kick in: for `build_widget` /
    `build_page` intents, we strip fenced code from the bubble and synthesize
    `navigate` + `orb_prompt` events when the LLM forgets to emit them, so the
    user always sees the live preview instead of a wall of code.
    """
    clean_text, actions = _extract_ui_actions(response_text)

    # Builder-intent safety net: synthesize navigate + orb_prompt if the LLM
    # staged a preview but forgot to wire up the UI actions.
    synth_actions = _synthesize_builder_actions(response_text, actions, intent_name)
    did_auto_preview = bool(synth_actions) or any(
        (a.get("action") or a.get("type")) == "navigate" for a in actions
    )
    # Strip fenced code blocks and/or replace rambling text with a short
    # confirmation for builder intents — the preview iframe is the evidence.
    clean_text = _sanitize_builder_reply(clean_text, intent_name, did_auto_preview)
    all_actions = list(actions) + synth_actions

    yield recorder.emit(
        enc,
        CustomEvent(
            name="zoe.run_log",
            value={
                "level": "info",
                "message": "OpenClaw response received",
                "chars": len(clean_text),
                "actions": len(all_actions),
                "synthesized": len(synth_actions),
            },
        ),
    )
    yield recorder.emit(
        enc,
        TextMessageStartEvent(
            type=EventType.TEXT_MESSAGE_START,
            message_id=assistant_message_id,
            role="assistant",
        ),
    )
    async for line in iter_openclaw_text_chunks(enc, recorder, assistant_message_id, clean_text):
        yield line
    yield recorder.emit(
        enc,
        TextMessageEndEvent(type=EventType.TEXT_MESSAGE_END, message_id=assistant_message_id),
    )
    for action in all_actions:
        act_type = action.get("action") or action.get("type")
        if "component" in action:
            yield recorder.emit(enc, CustomEvent(name="zoe.ui_component", value=action))
        elif act_type == "navigate":
            yield recorder.emit(enc, CustomEvent(name="zoe.ui_navigate", value=action))
        elif act_type == "orb_prompt":
            yield recorder.emit(enc, CustomEvent(name="zoe.ui_orb_prompt", value=action))
        elif "command" in action:
            yield recorder.emit(enc, CustomEvent(name="zoe.ui_command", value=action))
    # Auto-extract rich components from plain text (price tables, maps, menus)
    # only when no explicit :::zoe-ui::: blocks were present AND this isn't a
    # builder intent (for builders, preview iframe is the only component we want).
    if not all_actions and intent_name not in _BUILDER_INTENTS:
        try:
            extracted = auto_extract_components(clean_text)
            for comp in extracted:
                yield recorder.emit(enc, CustomEvent(name="zoe.ui_component", value=comp))
        except Exception as _aex:
            logger.debug("auto_extract_components failed (non-fatal): %s", _aex)
