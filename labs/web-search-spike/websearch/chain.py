"""The page-read FALLBACK CHAIN — cheap tier first, CloakBrowser as the floor.

`extract.py` and `cloak.py` each expose ONE way to turn a URL into text. This
module is the policy that decides which one runs, in what order, and — the part
that matters — makes the decision AUDITABLE.

THE PHILOSOPHY IT INHERITS
--------------------------
`EnginesBlocked` exists because `ddgs` reports "every engine refused us" and
"this query genuinely has no hits" as the same exception, and DESIGN.md §5
records why that is corrosive: Zoe tells Jason "there's nothing" when the truth
is the lookup never happened. A refusal arrives looking like a success.

The same trap, one layer down, is what this module closes. A bot wall returns
HTTP 200 with a plausible body. A client-rendered page returns HTTP 200 with an
empty shell. Both extract to a short string, and code that merely returns that
short string is reporting a REFUSAL as a THIN PAGE. So:

1. Every tier classifies its own outcome — `ok` / `thin` / `blocked` / `error`.
2. A tier that is blocked OR thin falls through to the next one.
3. **Every hop is recorded**: tier attempted -> why it did not serve -> which
   tier finally did. `FetchResult.provenance` is never empty and never
   summarised away. A silent fallback would be the same bug wearing a new hat.

THE COST ASYMMETRY IS THE WHOLE DESIGN
--------------------------------------
The tiers are not interchangeable. Measured on this box:

    httpx          ~0.3-1.5 s   one request, no third party
    jina           8.5-17.0 s   remote render, ~20 RPM, domain-restricted
    cloakbrowser   ~5-25 s      LOCAL CHROMIUM, ~553 MB RSS

The Jetson runs the live voice brain (7.9 GB resident) and Kokoro (2.2 GB), and
free memory during this spike measured 178-561 MB. A Chromium launch is
therefore not merely slow, it is the single most dangerous thing this chain can
do — `reference_voice_stack_memory_protection` records that burst RAM can kill
the RUNNING brain. So the chain is strictly LAZY and strictly SEQUENTIAL: a
later tier is not called, not constructed, and not imported unless every
cheaper tier has actually refused. `test_cloakbrowser_never_launches_when_*`
pins that with a spy, and it is the load-bearing test in this module.

THE CONTENT FLOOR, AND WHY IT IS NOT ENOUGH ON ITS OWN
------------------------------------------------------
`CONTENT_FLOOR_CHARS = 600`. Rationale rather than a round number: a product
page that genuinely renders server-side carries a name, a price, a size, a
description and a store block — comfortably over 1 kB. An SPA shell, a cookie
wall, and an "enable JavaScript" stub all land under ~300. 600 sits in the gap
with room on both sides, and it is a PARAMETER (`floor=`) because a corpus of
short reference pages would want it lower.

**But a length floor cannot tell "short page" from "page that rendered its
chrome but not its content", and that failure was MEASURED here.** On the
2026-08-03 botwall run, `cellarbrations.rsgwa.com.au` returned 759 characters
to plain httpx — over the floor, so the chain accepted it and stopped. Those
759 characters contained no prices at all; CloakBrowser returned 4,915
characters including the Emu Export special the query was about. The chain
reported a confident success and served the wrong bytes.

That is the module's own thesis coming back around one level up: a threshold on
SIZE is a proxy, and a proxy can be satisfied without the thing it proxies for.
So a caller that knows what it is looking for can say so, via `accept=`:

    fetch_url(url, accept=lambda text: "emu export" in text.lower())

`accept` runs IN ADDITION to the floor and can only make the chain try harder,
never stop earlier — it cannot be used to accept a page the floor rejected.
Without it the floor is still the policy, because a generic reader has no
predicate to offer. `test_accept_predicate_*` pins both directions.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Callable

from .direct import TierBlocked, TierFailed, direct_fetch
from .engines import EnginesBlocked
from .extract import MAX_EXTRACT_CHARS, ExtractUnavailable, Page, jina_reader

#: Below this many extracted characters a "success" is treated as a NON-ANSWER
#: and the chain falls through. See the module docstring for the rationale.
CONTENT_FLOOR_CHARS = 600

# Outcome vocabulary. Deliberately four values, not two: "blocked" and "error"
# both fall through, but they are different findings for the operator — a wall
# is a property of the site, a timeout is a property of the night.
OK = "ok"
THIN = "thin"
BLOCKED = "blocked"
ERROR = "error"


@dataclass(slots=True)
class Hop:
    """One tier attempt. `verdict` is why the chain did or did not stop here."""

    tier: str
    verdict: str
    detail: str = ""
    chars: int = 0
    elapsed_s: float = 0.0

    def line(self) -> str:
        return (
            f"{self.tier}: {self.verdict}"
            + (f" ({self.detail})" if self.detail else "")
            + f" [{self.chars} chars, {self.elapsed_s:.1f}s]"
        )


@dataclass(slots=True)
class FetchResult:
    """One URL read through the chain, with the full trail of how."""

    url: str
    text: str = ""
    title: str = ""
    #: The tier whose content is in `text`. None when every tier refused.
    tier_served: str | None = None
    #: EVERY attempt, in order. Never empty, never summarised away.
    provenance: list[Hop] = field(default_factory=list)
    elapsed_s: float = 0.0
    floor: int = CONTENT_FLOOR_CHARS

    @property
    def ok(self) -> bool:
        return self.tier_served is not None

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def fell_back(self) -> bool:
        """True when the cheapest tier did NOT serve — i.e. a wall was hit."""
        return self.ok and len(self.provenance) > 1

    @property
    def blocked_by(self) -> list[str]:
        """Tiers that were actively REFUSED (not merely thin or erroring)."""
        return [h.tier for h in self.provenance if h.verdict == BLOCKED]

    def trail(self) -> str:
        """One-line human summary — this is what gets quoted in a report."""
        return " -> ".join(h.line() for h in self.provenance)

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "ok": self.ok,
            "tier_served": self.tier_served,
            "chars": self.chars,
            "title": self.title,
            "elapsed_s": round(self.elapsed_s, 2),
            "floor": self.floor,
            "provenance": [asdict(h) for h in self.provenance],
        }


#: A tier is `(name, callable(url, text_limit=...) -> Page)`. It must raise on
#: refusal rather than return a short string, so that "refused" and "this page
#: is short" stay distinguishable all the way up.
Tier = tuple[str, Callable[..., Page]]


def _cloak_fetch(url: str, *, text_limit: int = MAX_EXTRACT_CHARS) -> Page:
    """CloakBrowser tier, imported LAZILY.

    The import itself is deferred: `cloak.py` loads the broker module from
    disk, which the chain must not do on a run where the cheap tier answered.
    """
    from .cloak import cloak_fetch

    return cloak_fetch(url, text_limit=text_limit)


def default_tiers(*, use_jina: bool = True) -> list[Tier]:
    """Cheapest first. CloakBrowser is always LAST — it is the floor, not a peer.

    `use_jina=False` skips the middle tier. That is the right setting when the
    corpus is commercial retail: Jina's anonymous tier is domain-restricted
    (measured 403 on britannica.com) and its 8.5-17 s median buys a coin flip,
    so on a run where the interesting question is "does a browser rescue this?"
    it is 15 s of noise per URL.
    """
    tiers: list[Tier] = [("httpx", direct_fetch)]
    if use_jina:
        tiers.append(("jina", jina_reader))
    tiers.append(("cloakbrowser", _cloak_fetch))
    return tiers


def classify(page: Page, floor: int, accept: Callable[[str], bool] | None = None) -> tuple[str, str]:
    """(verdict, detail) for a tier that returned WITHOUT raising.

    `accept` is checked AFTER the floor, never instead of it, so a predicate
    can only make the chain keep trying — it can never promote a page the floor
    already rejected. That ordering is the safety property: a caller's
    predicate is a hint about what it wants, not permission to lower the bar.
    """
    chars = len(page.text.strip())
    if chars < floor:
        return THIN, f"{chars} chars < floor {floor}"
    if accept is not None and not accept(page.text):
        return THIN, f"{chars} chars cleared the floor but FAILED the caller's accept()"
    return OK, page.detail


def _verdict_for(exc: Exception) -> tuple[str, str]:
    """(verdict, detail) for a tier that RAISED.

    `EnginesBlocked` is accepted here as well as the page-level exceptions:
    it is the search tier's refusal signal, and a caller that composes a
    search-backed tier into this chain must not have it silently become an
    'error'. Same philosophy, same vocabulary.
    """
    if isinstance(exc, (TierBlocked, EnginesBlocked)):
        return BLOCKED, str(exc)[:160]
    if isinstance(exc, ExtractUnavailable):
        # jina/cloak fold refusal and failure into one exception type; the
        # message is the only discriminator available, so read it rather than
        # guessing. A 403/429 IS a refusal and must be reported as one.
        text = str(exc)
        low = text.lower()
        refused = any(
            m in low
            for m in ("403", "429", "407", "rate limit", "refused", "challenge", "domain")
        )
        return (BLOCKED if refused else ERROR), text[:160]
    if isinstance(exc, TierFailed):
        return ERROR, str(exc)[:160]
    return ERROR, f"{type(exc).__name__}: {str(exc)[:140]}"


def fetch_url(
    url: str,
    *,
    floor: int = CONTENT_FLOOR_CHARS,
    text_limit: int = MAX_EXTRACT_CHARS,
    tiers: list[Tier] | None = None,
    use_jina: bool = True,
    accept: Callable[[str], bool] | None = None,
) -> FetchResult:
    """Read ONE url through the fallback chain. NEVER raises.

    Returns a `FetchResult` whose `.provenance` records every tier attempted
    and why it did not serve. `.ok` is False only when EVERY tier refused —
    and even then the trail says which wall stopped which tier, which is a
    finding rather than a failure.

    `accept(text) -> bool` lets a caller that knows WHAT it is looking for say
    so, instead of relying on the length floor as a proxy. See the module
    docstring for the measured case that motivated it.
    """
    chain = tiers if tiers is not None else default_tiers(use_jina=use_jina)
    out = FetchResult(url=url, floor=floor)
    started = time.monotonic()

    for name, fetcher in chain:
        hop_started = time.monotonic()
        try:
            page = fetcher(url, text_limit=text_limit)
        except Exception as exc:  # noqa: BLE001 - classification IS the handling
            verdict, detail = _verdict_for(exc)
            out.provenance.append(
                Hop(tier=name, verdict=verdict, detail=detail,
                    elapsed_s=time.monotonic() - hop_started)
            )
            continue

        verdict, detail = classify(page, floor, accept)
        out.provenance.append(
            Hop(tier=name, verdict=verdict, detail=detail,
                chars=len(page.text.strip()), elapsed_s=page.elapsed_s or (time.monotonic() - hop_started))
        )
        if verdict == OK:
            out.text = page.text
            out.title = page.title
            out.tier_served = name
            break
        # A THIN page is kept as the best-effort answer in case every remaining
        # tier also refuses — better to hand back 200 characters plus the trail
        # than nothing plus the trail. It is NOT marked served.
        if len(page.text.strip()) > len(out.text.strip()):
            out.text = page.text
            out.title = page.title

    out.elapsed_s = time.monotonic() - started
    return out


def fetch_urls(urls: list[str], **kwargs) -> list[FetchResult]:
    """SEQUENTIAL by design — never parallel.

    Parallelising this would be the obvious optimisation and it is forbidden:
    two concurrent CloakBrowser fallbacks are two Chromiums (~1.1 GB) on a box
    whose free memory measured 178-561 MB while the voice brain was resident.
    The chain is allowed to be slow; it is not allowed to evict the brain.
    """
    return [fetch_url(url, **kwargs) for url in urls]
