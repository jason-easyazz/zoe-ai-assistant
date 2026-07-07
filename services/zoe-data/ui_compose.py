"""ui_compose — the brain composes a card (grammar-constrained, always-fallback).

``compose_card()`` asks the local brain (llama-server, the same Gemma the rest of
Zoe runs on) to compose a component tree for a just-answered request, using the
catalog's generated JSON Schema as a decoding grammar — so the model *cannot*
emit an invalid tree — then re-validates server-side (defence in depth) and
returns a ``compose`` card ready for the shared renderer.

Design rules (from the approved ever-evolving-interface plan):
  - Flag-gated by ``ZOE_COMPOSE_UI`` (default OFF).
  - Failure of ANY kind (timeout, HTTP error, invalid JSON, validation reject)
    returns ``None`` — callers already delivered the text answer, so composition
    is strictly additive and can never break or delay a turn.
  - Never called on the voice path before TTS has started.
  - Layout memory (``ui_layouts``, flag ``ZOE_LAYOUT_MEMORY``, default ON):
    stored trees are injected as a prompt-side structural hint only and every
    successful compose is saved back — layouts converge per user+intent over
    time, and stale stored content is never rendered.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

import httpx

import ui_layouts
from card_contract import CardContractError
from ui_catalog import catalog_doc, llm_schema, validate_component_tree

logger = logging.getLogger(__name__)

_GEMMA_URL = os.environ.get("GEMMA_SERVER_URL", "http://127.0.0.1:11434/v1")
_TIMEOUT_S = float(os.environ.get("ZOE_COMPOSE_TIMEOUT_S", "14"))
_MAX_TOKENS = int(os.environ.get("ZOE_COMPOSE_MAX_TOKENS", "700"))
# Composition input is a summary of an already-answered turn; cap it so the
# compose call stays cheap and can't drag a huge context back through the model.
_MAX_CONTEXT_CHARS = 1200
# Layout-memory few-shot hint (ZOE_LAYOUT_MEMORY, see ui_layouts.py) is bounded
# so a large stored tree can't bloat the compose prompt. Truncation may cut the
# hint JSON mid-token; that is acceptable — it is a structural *hint*, and the
# grammar-constrained decoder guarantees output validity regardless.
_LAYOUT_HINT_MAX_CHARS = 800

_SYSTEM = (
    "You are Zoe's interface composer. Given a user's request and the answer "
    "Zoe already gave, compose ONE glanceable card that shows the answer "
    "visually. Use the component vocabulary exactly; do not invent components "
    "or props.\n\n" + catalog_doc() + "\n\n"
    "Rules: no more than ~8 elements; prefer Stat/Badge/ListRow over prose; "
    "include at most 2 ActionButtons and only for obvious follow-ups; never "
    "repeat the entire answer as a wall of Text."
)


def compose_enabled() -> bool:
    return os.environ.get("ZOE_COMPOSE_UI", "").strip().lower() in ("1", "true", "yes", "on")


async def compose_card(user_message: str, answer_text: str, *, user_id: str = "") -> Optional[dict[str, Any]]:
    """Compose a card for an answered turn. Returns a ``compose`` card dict
    (``{"component": "compose", "props": {"tree": ...}}``) or ``None``.

    Never raises. Callers must treat ``None`` as "no card" and move on.
    """
    # Layout memory (ZOE_LAYOUT_MEMORY, default ON; only reachable when
    # ZOE_COMPOSE_UI is on since this is the sole compose path). v1 is
    # layout-as-few-shot: a stored tree is a structural hint in the prompt,
    # never rendered directly — reuse must not show stale content.
    layout_family = ""
    layout_hint = ""
    if user_id and ui_layouts.layout_memory_enabled():
        try:
            layout_family = ui_layouts.intent_family_for(user_message)
            stored = await ui_layouts.get_layout(user_id, layout_family)
            if stored:
                hint_json = json.dumps(stored, separators=(",", ":"))[:_LAYOUT_HINT_MAX_CHARS]
                layout_hint = (
                    "\nPreviously, a good layout for a similar request was: "
                    + hint_json
                    + "\nPrefer this structure, updated with the new content."
                )
        except Exception as exc:  # noqa: BLE001 — layout memory never breaks compose
            logger.info("layout memory lookup skipped (non-fatal): %s", exc)
            layout_hint = ""

    prompt = (
        "User asked: " + (user_message or "")[:_MAX_CONTEXT_CHARS]
        + "\nZoe answered: " + (answer_text or "")[:_MAX_CONTEXT_CHARS]
        + layout_hint
        + "\nCompose the card now."
    )
    body = {
        "model": "gemma",
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": _MAX_TOKENS,
        "temperature": 0.4,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "zoe_card", "schema": llm_schema()},
        },
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.post(f"{_GEMMA_URL}/chat/completions", json=body)
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        tree = validate_component_tree(json.loads(content))
    except (httpx.HTTPError, KeyError, IndexError, ValueError, CardContractError) as exc:
        # ValueError covers json.JSONDecodeError; composition is best-effort by design.
        logger.info("compose_card skipped (non-fatal): %s: %s", type(exc).__name__, str(exc)[:160])
        return None
    except Exception as exc:  # noqa: BLE001 — absolutely never break the turn
        logger.warning("compose_card unexpected failure (non-fatal): %s", exc)
        return None
    if layout_family:  # only set when user_id present and layout memory enabled
        try:
            await ui_layouts.save_layout(user_id, layout_family, tree)
        except Exception:  # noqa: BLE001 — save_layout is no-raise; belt and braces
            pass
    return {"component": "compose", "props": {"tree": tree}}
