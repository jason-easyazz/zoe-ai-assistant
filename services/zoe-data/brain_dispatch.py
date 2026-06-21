"""Single source of truth for which brain answers a turn.

zoe-core (Pi on local Gemma) by default; the legacy ``zoe_agent`` brain only when
``ZOE_USE_CORE_BRAIN`` is explicitly off (the validation-window fallback). Every
entry point — text chat AND all the voice paths — routes through here so the
cutover flag controls them consistently. (chat.py keeps its own equivalent
helpers, which are exercised by the routing tests; this module is what the voice
paths use to avoid circular imports with chat.py.)

Imports are lazy inside each function to avoid import-time cycles
(main.py → routers.chat → ... ).
"""
from __future__ import annotations

import os
from typing import Any, AsyncIterator


def use_core_brain() -> bool:
    """True when the brain is zoe-core (default); read lazily so a .env value
    bootstrapped after import is honored."""
    return (os.environ.get("ZOE_USE_CORE_BRAIN", "true") or "").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def brain_streaming(message: str, session_id: str, user_id: str = "", **kwargs: Any) -> AsyncIterator[str]:
    """Streaming brain turn — zoe-core by default, legacy on fallback."""
    if use_core_brain():
        from zoe_core_client import run_zoe_core_streaming

        return run_zoe_core_streaming(message, session_id, user_id, **kwargs)
    from zoe_agent import run_zoe_agent_streaming

    return run_zoe_agent_streaming(message, session_id, user_id, **kwargs)


async def brain_oneshot(message: str, session_id: str, user_id: str = "", **kwargs: Any) -> str:
    """Non-streaming brain turn — zoe-core by default, legacy on fallback."""
    if use_core_brain():
        from zoe_core_client import run_zoe_core

        return await run_zoe_core(message, session_id, user_id, **kwargs)
    from zoe_agent import run_zoe_agent

    return await run_zoe_agent(message, session_id, user_id, **kwargs)
