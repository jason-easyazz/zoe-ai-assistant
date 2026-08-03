"""Tier 0 of the page-read chain: a plain `httpx` GET.

This is the cheapest way to read a page — one request, no third party, no
browser, ~0.3-1.5 s — and for the majority of the open web it is entirely
sufficient. It exists so that the expensive tiers below it (Jina Reader's
remote render, CloakBrowser's local Chromium) are only ever paid for when
something actually refused us.

WHY IT SHARES THE BROKER'S EXTRACTOR
------------------------------------
Text is extracted with `browser_broker.extract_main_text` — the same pure
function CloakBrowser's text path uses, imported by file path via
`cloak._load_broker()`. That is deliberate: if this tier used a different
extractor, a tier-to-tier comparison would be measuring TWO variables (fetch
method *and* extraction quality) and could not attribute a difference to
either. Sharing it means the only variable is the transport.

If the broker module is not loadable, the tier degrades to a crude tag strip
and SAYS SO in its detail string, rather than silently scoring worse.

WHAT COUNTS AS "REFUSED"
------------------------
Bot walls do not announce themselves consistently, so both signals are needed
and neither is sufficient alone:

- **Status.** 401/403/407/429/451 are explicit refusals. So is 503, which is
  what a Cloudflare/Akamai interstitial commonly returns.
- **Body shape.** Measured on this box: Mojeek served HTTP **200** with
  `<title>Captcha</title>`, and DuckDuckGo served **202** with an
  `anomaly-modal`. A 200 is not proof of content.

Both map to `TierBlocked`, which the chain treats as "fall through", never as
"this page is empty".
"""

from __future__ import annotations

import re
import time

import httpx

from .engines import is_blocked
from .extract import MAX_EXTRACT_CHARS, Page

# A plain GET is a fingerprint too. A descriptive UA is the right thing for the
# JSON APIs in `scrapers.py` (Wikimedia 403s a generic browser UA), but for
# ordinary HTML pages it is an instant "this is a bot" signal. So this tier
# sends a realistic desktop UA and full browser Accept headers: the point of the
# tier is to establish whether a page is readable WITHOUT a browser, and losing
# to a UA sniff answers a different, less interesting question.
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Upgrade-Insecure-Requests": "1",
}

DIRECT_TIMEOUT_S = 15.0

#: Statuses that mean "we were refused", not "this page does not exist". A 404
#: is deliberately absent: a browser would not rescue a genuinely missing page,
#: so escalating to Chromium for one is pure waste.
REFUSAL_STATUSES = frozenset({401, 403, 407, 429, 451, 503})

_TAG_RE = re.compile(r"(?is)<(script|style|noscript|template)[^>]*>.*?</\1>")
_ANY_TAG_RE = re.compile(r"<[^>]+>")


class TierBlocked(RuntimeError):
    """The tier was REFUSED (status or challenge body). Fall through."""


class TierFailed(RuntimeError):
    """The tier errored (timeout, DNS, transport). Fall through, but say so."""


def _strip_html(html: str) -> str:
    """Last-resort text extraction when the broker module is unavailable."""
    import html as _html

    body = _TAG_RE.sub(" ", html)
    return re.sub(r"\s+", " ", _html.unescape(_ANY_TAG_RE.sub(" ", body))).strip()


def _extract(html: str, text_limit: int) -> tuple[str, str, str]:
    """(text, title, strategy). Uses the broker's real extractor when loadable."""
    try:
        from .cloak import _load_broker  # local import: cloak imports httpx-free

        module = _load_broker()
    except Exception as exc:  # noqa: BLE001 - degrade loudly, never silently
        text = _strip_html(html)[:text_limit]
        return text, "", f"degraded:tag-strip ({type(exc).__name__})"

    extracted = module.extract_main_text(html, text_limit=text_limit)
    return extracted.text, extracted.title, extracted.strategy


def direct_fetch(
    url: str,
    *,
    text_limit: int = MAX_EXTRACT_CHARS,
    timeout: float = DIRECT_TIMEOUT_S,
    client: httpx.Client | None = None,
) -> Page:
    """Plain-HTTP page read. Raises `TierBlocked`/`TierFailed` on refusal.

    Never returns a challenge page as if it were content — that is the whole
    point of the tier existing separately from "GET and hope".
    """
    started = time.monotonic()
    owned = client or httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False)
    try:
        resp = owned.get(url, headers=BROWSER_HEADERS)
    except Exception as exc:  # noqa: BLE001 - transport failure is not a refusal
        raise TierFailed(f"httpx {type(exc).__name__}: {str(exc)[:120]}") from exc
    finally:
        if client is None:
            owned.close()

    if resp.status_code in REFUSAL_STATUSES:
        raise TierBlocked(f"HTTP {resp.status_code}")
    if resp.status_code >= 400:
        raise TierFailed(f"HTTP {resp.status_code}")

    body = resp.text
    if is_blocked(body):
        raise TierBlocked(f"challenge body at HTTP {resp.status_code}")

    text, title, strategy = _extract(body, text_limit)
    return Page(
        url=str(resp.url),
        text=text,
        tier="httpx",
        elapsed_s=time.monotonic() - started,
        truncated=len(text) >= text_limit,
        title=title,
        detail=f"HTTP {resp.status_code}, {len(body)}B html, {strategy}",
    )
