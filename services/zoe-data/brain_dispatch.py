"""Single source of truth for which brain answers a turn.

zoe-core (Pi on local Gemma) by default; the legacy ``zoe_agent`` brain only when
``ZOE_USE_CORE_BRAIN`` is explicitly off (the validation-window fallback). Every
entry point — text chat AND all the voice paths — routes through here so the
cutover flag controls them consistently. ``routers/chat.py`` imports these under
its historical private names (``_use_flue_brain`` / ``_brain_streaming`` /
``_brain_oneshot``), so its call sites and the routing tests that patch those
names on the chat module keep working unchanged.

Cutover seam: ``ZOE_BRAIN_BACKEND='flue'`` (default ``'core'``) opts the brain
lane into the Flue brain sidecar (``zoe_flue_client``) instead of zoe-core. This
is ADDITIVE and default-OFF — with the env unset/``'core'`` dispatch is
byte-identical to today, so the live voice path is unaffected. The flip is
operator-gated on voice-corpus parity; reversible by env toggle (no migration).

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


def use_flue_brain() -> bool:
    """True ONLY when ``ZOE_BRAIN_BACKEND == 'flue'`` (default ``'core'``).

    The additive, default-OFF cutover seam to the Flue brain sidecar. With the
    env unset or ``'core'`` this returns False and dispatch is byte-identical to
    today (zoe-core, legacy on fallback). Read lazily so a .env value
    bootstrapped after import is honored. The flip is operator-gated on
    voice-corpus parity — do not change the default here.
    """
    return (os.environ.get("ZOE_BRAIN_BACKEND", "core") or "").strip().lower() == "flue"


def brain_streaming(message: str, session_id: str, user_id: str = "", **kwargs: Any) -> AsyncIterator[str]:
    """Streaming brain turn — Flue (opt-in) > zoe-core (default) > legacy."""
    if use_flue_brain():
        from zoe_flue_client import run_flue_brain_streaming

        return run_flue_brain_streaming(message, session_id, user_id, **kwargs)
    if use_core_brain():
        from zoe_core_client import run_zoe_core_streaming

        return run_zoe_core_streaming(message, session_id, user_id, **kwargs)
    from zoe_agent import run_zoe_agent_streaming

    return run_zoe_agent_streaming(message, session_id, user_id, **kwargs)


async def brain_oneshot(message: str, session_id: str, user_id: str = "", **kwargs: Any) -> str:
    """Non-streaming brain turn — Flue (opt-in) > zoe-core (default) > legacy."""
    if use_flue_brain():
        from zoe_flue_client import run_flue_brain

        return await run_flue_brain(message, session_id, user_id, **kwargs)
    if use_core_brain():
        from zoe_core_client import run_zoe_core

        return await run_zoe_core(message, session_id, user_id, **kwargs)
    from zoe_agent import run_zoe_agent

    return await run_zoe_agent(message, session_id, user_id, **kwargs)
