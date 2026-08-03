"""CloakBrowser page-extract tier — the LOCAL answer to "read this page".

This is the third extraction tier, measured against `jina_reader` on identical
input so the only variable is the extraction backend (see `eval/run_eval.py`,
`combo_cloakbrowser`).

WHY THIS MODULE EXISTS AT ALL
-----------------------------
`extract.py` records the blocker: `services/zoe-data/browser_broker.py` wraps
CloakBrowser but its executor returned a **base64 PNG screenshot only**, so the
broker could not feed a text packet. That is fixed in
`feat/browser-broker-text-extraction` (PR #1626), which adds a pure
`extract_main_text(html)` plus an in-process `fetch_page_text(url)`.

LAB WIRING, DELIBERATELY NOT A COPY
-----------------------------------
This module **imports that function by file path** rather than vendoring it, so
the eval scores the REAL implementation and cannot silently drift from it. That
also means the tier is **unavailable until PR #1626 is merged** into the live
checkout — which is the honest state, and `available()` reports it rather than
faking a result.

Resolution order for the broker module:

1. ``$ZOE_BROWSER_BROKER_PATH`` — an explicit path (used to score the branch
   before it merges).
2. ``/home/zoe/assistant/services/zoe-data/browser_broker.py`` — the live
   checkout, which is correct once the PR lands.

`agent_safety` (the SSRF guards the broker calls) is imported from the LIVE
checkout's `services/zoe-data/` in both cases: it is stdlib-only, already on
main, and unchanged by the PR — so the guard under test is production's guard.

On promotion this module is deleted and callers use the broker directly; there
must not be two CloakBrowser text paths.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import pathlib
import sys
import time

from .extract import MAX_EXTRACT_CHARS, ExtractUnavailable, Page

LIVE_ZOE_DATA = pathlib.Path("/home/zoe/assistant/services/zoe-data")
DEFAULT_BROKER = LIVE_ZOE_DATA / "browser_broker.py"

# CloakBrowser launches a full Chromium. The eval runs it under a
# `systemd-run --user --scope -p MemoryMax=1024M` cap; this timeout is the
# second bound, so one wedged page cannot stall a 26-query run.
CLOAK_TIMEOUT_MS = 30_000

# Sentinel: `settle=None` must mean "no settle", which is DIFFERENT from
# "caller did not specify" (-> the tier's own default policy).
_UNSET = object()

_broker_mod = None


def broker_path() -> pathlib.Path:
    override = os.environ.get("ZOE_BROWSER_BROKER_PATH", "").strip()
    return pathlib.Path(override) if override else DEFAULT_BROKER


def _load_broker():
    """Import the broker module from disk, memoised."""
    global _broker_mod
    if _broker_mod is not None:
        return _broker_mod

    path = broker_path()
    if not path.is_file():
        raise ExtractUnavailable(f"browser_broker.py not found at {path}")

    # agent_safety lives beside the broker in the live checkout and is
    # stdlib-only; the broker lazy-imports it inside fetch_page_text.
    if str(LIVE_ZOE_DATA) not in sys.path:
        sys.path.append(str(LIVE_ZOE_DATA))

    spec = importlib.util.spec_from_file_location("zoe_browser_broker", path)
    if spec is None or spec.loader is None:
        raise ExtractUnavailable(f"cannot load a module spec from {path}")
    module = importlib.util.module_from_spec(spec)
    # MUST be registered BEFORE exec_module: on py3.10 `@dataclass` resolves the
    # defining class's module out of sys.modules, and the broker defines its
    # dataclasses at import time — without this the import dies with a bare
    # "NoneType has no attribute __dict__" from inside dataclasses.py.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        sys.modules.pop(spec.name, None)
        raise ExtractUnavailable(f"failed to import {path}: {type(exc).__name__}: {exc}") from exc

    if not hasattr(module, "fetch_page_text"):
        raise ExtractUnavailable(
            f"{path} has no fetch_page_text — PR #1626 (broker text extraction) "
            "is not present in this checkout"
        )
    _broker_mod = module
    return module


def available() -> tuple[bool, str]:
    """(usable, reason). Never raises — the harness prints this in --list."""
    if importlib.util.find_spec("cloakbrowser") is None:
        return False, "cloakbrowser not installed"
    path = broker_path()
    if not path.is_file():
        return False, f"broker missing at {path}"
    try:
        _load_broker()
    except Exception as exc:  # noqa: BLE001 - --list must never crash the harness
        return False, str(exc)
    return True, f"ready (broker: {path})"


def settle_policy(module):
    """The post-load settle this tier asks for, or `None` on an older broker.

    CloakBrowser is the LAST tier: by the time it runs, two cheaper tiers have
    already refused, so the URLs that reach it are disproportionately the
    client-rendered ones. Reading the DOM at `domcontentloaded` on those gets
    an empty shell — a refusal wearing a success's clothes, which is the exact
    failure this chain exists to prevent. So the tier that is meant to be the
    FLOOR must wait for the page it was escalated to.

    Degrades to `None` (previous behaviour) against a broker without
    `SETTLE_SPA`, so this module keeps working while PR #1626 is in flight.
    """
    return getattr(module, "SETTLE_SPA", None)


def cloak_fetch(url: str, *, text_limit: int = MAX_EXTRACT_CHARS, settle=_UNSET) -> Page:
    """Fetch ONE url through CloakBrowser and return extracted main-content text.

    Returns the same `Page` shape as `jina_reader`, so the extraction tiers are
    directly comparable. Raises `ExtractUnavailable` on refusal/failure — the
    harness records that as `blocked` rather than folding it into "no content",
    matching the tier-honesty rule.

    `settle` defaults to `SETTLE_SPA` (see `settle_policy`); pass `None` to
    reproduce the pre-settle timing, which is what the botwall corpus does to
    measure what the settle is actually worth.
    """
    module = _load_broker()
    kwargs = {"text_limit": text_limit, "timeout_ms": CLOAK_TIMEOUT_MS}
    policy = settle_policy(module) if settle is _UNSET else settle
    if policy is not None:
        kwargs["settle"] = policy

    started = time.monotonic()
    result = asyncio.run(module.fetch_page_text(url, **kwargs))
    elapsed = time.monotonic() - started

    if not result.get("ok"):
        raise ExtractUnavailable(f"cloakbrowser: {result.get('error', 'unknown error')}: {url}")

    text = result.get("text") or ""
    if not text.strip():
        raise ExtractUnavailable(f"cloakbrowser returned no text for {url}")

    settle_log = result.get("settle") or []
    note = "; ".join(f"{e.get('stage')}={e.get('outcome')}" for e in settle_log) or "no settle"
    return Page(
        url=result.get("final_url") or url,
        text=text,
        tier="cloakbrowser",
        elapsed_s=elapsed,
        truncated=bool(result.get("truncated")),
        title=result.get("title") or "",
        detail=f"{result.get('strategy', '?')}; settle: {note}",
    )
