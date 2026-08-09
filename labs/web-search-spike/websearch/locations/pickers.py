"""`establish_session` — run a retailer's picker ONCE and bank the result.

This is the deliberate, operator-scale act that an `interaction` recipe depends
on: one CloakBrowser session, one store selection, cookies written to the jar.
Every product read afterwards is plain `httpx` with those cookies until the jar
goes stale.

WHY IT IS A SEPARATE ENTRY POINT FROM `fetch_with_location`
-----------------------------------------------------------
Because a per-product fetch must never be able to launch Chromium on the box
that runs the voice brain. If `fetch_with_location` could fall back to "just
run the picker", then a 40-product weekly run over a retailer whose session
expired mid-run would launch Chromium forty times, each ~553 MB, serially, with
nobody having decided that. So the fetch path degrades to a labelled store-less
read and NAMES this function in the detail string; a human (or the weekly
runner's explicit setup phase) calls it.

The picker scripts themselves live in `capture.py` — one definition, used both
for discovery (print what the front end called) and for establishment (keep the
cookies). Two copies of a fragile selector list would drift within a week.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from .cookies import CookieJar
from .provenance import GERALDTON, StoreContext
from .registry import INTERACTION, get_recipe
from .session import SessionRefused, StoreSession


@dataclass(slots=True)
class SessionOutcome:
    """What the picker run achieved — including when it achieved nothing."""

    retailer: str
    domain: str
    ok: bool
    #: Did the resulting page actually mention the locality we asked for?
    locality_confirmed: bool = False
    store_id: str = ""
    store_label: str = ""
    cookies_saved: int = 0
    note: str = ""
    page_loads: int = 0
    warnings: list[str] = field(default_factory=list)

    def line(self) -> str:
        state = "OK" if self.ok else "FAILED"
        return (
            f"{self.retailer}: {state} — {self.cookies_saved} cookies, "
            f"locality_confirmed={self.locality_confirmed}, {self.page_loads} page loads"
            + (f" | {self.note}" if self.note else "")
        )


async def _run(
    retailer: str,
    store_ctx: StoreContext,
    jar: CookieJar,
    *,
    domain: str,
    max_page_loads: int,
) -> SessionOutcome:
    from .capture import SCRIPTS  # local import: capture pulls in argparse etc.

    script = SCRIPTS[retailer]
    out = SessionOutcome(retailer=retailer, domain=domain, ok=False)

    async with StoreSession(label=retailer, max_page_loads=max_page_loads) as s:
        try:
            out.note = await script(s)
        except Exception as exc:  # noqa: BLE001 - a half-run is still bankable
            out.note = f"picker raised {type(exc).__name__}: {exc}"
            out.warnings.append("the picker script did not complete — treat the session as suspect")

        out.page_loads = s.page_loads
        cookies = await s.cookies()
        storage = await s.local_storage()

        # Corroboration BEFORE banking: if the last page we read never mentions
        # the locality, we may have banked a session that selected nothing. Say
        # so rather than writing a jar that will later claim a store.
        last = s.reads[-1] if s.reads else None
        out.locality_confirmed = bool(last and store_ctx.matches(last.text))
        if not out.locality_confirmed:
            out.warnings.append(
                f"the final page does not mention {store_ctx.suburb!r} — the selection "
                f"may not have taken; prices read with this session are NOT confirmed "
                f"store-accurate"
            )

        path = jar.save(
            domain,
            cookies,
            store_id=out.store_id,
            store_label=out.store_label or f"{retailer} {store_ctx.suburb}",
            local_storage=storage,
        )
        out.cookies_saved = len(jar.load(domain))
        out.ok = out.cookies_saved > 0
        out.note += f"; jar -> {path}"
    return out


def establish_session(
    retailer: str,
    *,
    store_ctx: StoreContext = GERALDTON,
    jar: CookieJar | None = None,
    domain: str = "",
    max_page_loads: int = 12,
) -> SessionOutcome:
    """Run `retailer`'s picker for `store_ctx` and bank the cookies. Sync wrapper.

    `domain` defaults to the domain of the retailer's registry entry; pass it
    explicitly only when establishing a session for a retailer that does not
    have a recipe yet (i.e. during discovery).

    Raises `SessionRefused` when the box cannot afford a Chromium — that is a
    refusal to act, not a failed action, and the caller should see the
    difference.
    """
    from .capture import SCRIPTS

    if retailer not in SCRIPTS:
        raise KeyError(f"no picker script for {retailer!r}; have {sorted(SCRIPTS)}")

    if not domain:
        for key, rec in _recipes_for(retailer):
            domain = key
            break
        if not domain:
            raise ValueError(
                f"no registry entry for {retailer!r} and no explicit domain= given"
            )

    return asyncio.run(
        _run(retailer, store_ctx, jar or CookieJar(), domain=domain, max_page_loads=max_page_loads)
    )


def _recipes_for(retailer: str) -> list[tuple[str, Any]]:
    from .registry import RECIPES

    return [
        (key, rec)
        for key, rec in RECIPES.items()
        if rec.picker == retailer and rec.kind == INTERACTION
    ]


def needs_session(url: str, jar: CookieJar | None = None) -> bool:
    """True when this URL's recipe is `interaction` and no fresh jar exists.

    The weekly runner calls this over its product list FIRST, so the whole
    "which pickers must I run tonight" question is answered before any fetching
    starts — one browser session per retailer, decided up front, rather than
    discovered halfway through.
    """
    recipe = get_recipe(url)
    if recipe is None or recipe.kind != INTERACTION:
        return False
    return not (jar or CookieJar()).is_fresh(recipe.domain)


__all__ = ["SessionOutcome", "SessionRefused", "establish_session", "needs_session"]
