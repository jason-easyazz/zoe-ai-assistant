"""Key-free search engines: plain HTTP + regex parsing, no browser, no new deps.

Ported from oh-my-pi (MIT) `src/web/search/providers/duckduckgo.ts` — the
regex parse of DuckDuckGo's no-JS HTML frontend, its `uddg=` redirect
unwrapping and its `anomaly-modal` bot-challenge detection. The upstream
module also carries a headless-browser escalation path; it is deliberately
NOT ported (see DESIGN.md §3) — every engine here is plain `httpx`.

Measured on this box 2026-08-03: of oh-my-pi's key-free engine set, only
DuckDuckGo answers. Startpage/Ecosia/Mojeek/Brave/searx.be all bot-block.
So the second engine is DDG's independent `lite/` endpoint rather than a
second vendor — see README.md "Findings".
"""

from __future__ import annotations

import html as _html
import re
import urllib.parse
from dataclasses import dataclass, field

import httpx

DDG_HTML_URL = "https://html.duckduckgo.com/html/"
DDG_LITE_URL = "https://lite.duckduckgo.com/lite/"

UA_BROWSER = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
# Wikimedia and most JSON APIs REJECT a generic browser UA (measured: 403) but
# accept a descriptive one. Structured scrapers must identify themselves.
UA_POLITE = "ZoeAssistant/0.1 (local household assistant; contact: jason@easyazz.com)"

DEFAULT_TIMEOUT_S = 8.0


class EngineBlocked(RuntimeError):
    """The engine served a bot challenge instead of results (often HTTP 200)."""


@dataclass(slots=True)
class Result:
    """One search hit. Mirrors oh-my-pi's `SearchSource`, minus the fields a
    voice product never reads."""

    title: str
    url: str
    snippet: str = ""
    engine: str = ""
    # Populated by merge.consensus_merge: how many engines returned this URL.
    engines: int = 1
    rank: int = 0
    extra: dict = field(default_factory=dict)


_TAG_RE = re.compile(r"<[^>]*>")
_WS_RE = re.compile(r"\s+")


def clean_text(value: str) -> str:
    """Strip inline tags, unescape entities, collapse whitespace."""
    return _WS_RE.sub(" ", _html.unescape(_TAG_RE.sub(" ", value))).strip()


def unwrap_ddg_url(href: str) -> str | None:
    """Resolve a DDG result href to the real target.

    DDG routes clicks through `//duckduckgo.com/l/?uddg=<encoded>`; we want the
    unwrapped URL. Handles redirect wrappers, protocol-relative and absolute
    links (oh-my-pi duckduckgo.ts `unwrapResultUrl`).
    """
    if not href:
        return None
    decoded = href.replace("&amp;", "&")
    wrapped = re.search(r"[?&]uddg=([^&]+)", decoded)
    if wrapped:
        try:
            return urllib.parse.unquote(wrapped.group(1))
        except Exception:  # noqa: BLE001
            return None
    if decoded.startswith("//"):
        return f"https:{decoded}"
    if decoded.startswith(("http://", "https://")):
        return decoded
    return None


def is_blocked(body: str) -> bool:
    """True when the body is a bot challenge rather than results.

    Engines mix status codes on these (DDG serves 200 and 202), so the body is
    the only reliable signal — the Mojeek probe returned HTTP 200 with a
    `<title>Captcha</title>` page. Status 200 is NOT proof of results.
    """
    low = body.lower()
    return any(
        marker in low
        for marker in ("anomaly-modal", "anomaly.js", "<title>captcha", "unusual traffic", "verifying your browser")
    )


# --- DuckDuckGo html/ ------------------------------------------------------

_BLOCK_RE = re.compile(
    r'<div\b[^>]*\bclass="[^"]*\bresult\b[^"]*"[^>]*>(.*?)'
    r'(?=<div\b[^>]*\bclass="[^"]*\bresult\b|<div\b[^>]*\bclass="[^"]*\bnav-link\b|$)',
    re.S,
)
_TITLE_RE = re.compile(r'<a\b[^>]*\bclass="[^"]*\bresult__a\b[^"]*"[^>]*\bhref="([^"]+)"[^>]*>(.*?)</a>', re.S)
_SNIPPET_RE = re.compile(
    r'<(?:a|div|span)\b[^>]*\bclass="[^"]*\bresult__snippet\b[^"]*"[^>]*>(.*?)</(?:a|div|span)>', re.S
)


def parse_ddg_html(body: str) -> list[Result]:
    """Parse DDG's `html/` results page. Pure function — the offline test seam."""
    if is_blocked(body):
        raise EngineBlocked("duckduckgo html/ served a bot challenge")
    out: list[Result] = []
    seen: set[str] = set()
    for block in _BLOCK_RE.finditer(body):
        chunk = block.group(1)
        title_m = _TITLE_RE.search(chunk)
        if not title_m:
            continue
        url = unwrap_ddg_url(title_m.group(1))
        title = clean_text(title_m.group(2))
        if not url or not title or url in seen:
            continue
        seen.add(url)
        snippet_m = _SNIPPET_RE.search(chunk)
        out.append(
            Result(
                title=title,
                url=url,
                snippet=clean_text(snippet_m.group(1)) if snippet_m else "",
                engine="ddg-html",
                rank=len(out),
            )
        )
    return out


# --- DuckDuckGo lite/ ------------------------------------------------------

# `lite/` emits SINGLE-quoted attributes in attribute order `href` then `class`
# (measured 2026-08-03), so these patterns are quote- and order-agnostic. A
# double-quote-only regex silently matched nothing — see README "Findings".
_LITE_ROW_RE = re.compile(r"<tr\b([^>]*)>(.*?)</tr>", re.S | re.I)
_LITE_LINK_RE = re.compile(
    r"""<a\b(?=[^>]*\bclass\s*=\s*['"][^'"]*\bresult-link\b)[^>]*\bhref\s*=\s*['"]([^'"]+)['"][^>]*>(.*?)</a>""",
    re.S | re.I,
)
_LITE_SNIP_RE = re.compile(
    r"""<td\b[^>]*\bclass\s*=\s*['"][^'"]*\bresult-snippet\b[^'"]*['"][^>]*>(.*?)</td>""", re.S | re.I
)


def parse_ddg_lite(body: str) -> list[Result]:
    """Parse DDG's `lite/` table layout — different markup, same index.

    Useful as a fallback: when `html/` trips the anomaly modal, `lite/` often
    still answers. Sponsored rows (`<tr class="result-sponsored">`) are
    dropped — they are ads, not results, and a voice answer must never cite one.
    """
    if is_blocked(body):
        raise EngineBlocked("duckduckgo lite/ served a bot challenge")
    out: list[Result] = []
    seen: set[str] = set()
    pending: Result | None = None
    for row in _LITE_ROW_RE.finditer(body):
        attrs, inner = row.group(1), row.group(2)
        if "result-sponsored" in attrs:
            pending = None
            continue
        snippet_m = _LITE_SNIP_RE.search(inner)
        if snippet_m and pending is not None:
            pending.snippet = clean_text(snippet_m.group(1))
            pending = None
            continue
        link_m = _LITE_LINK_RE.search(inner)
        if not link_m:
            continue
        url = unwrap_ddg_url(link_m.group(1))
        title = clean_text(link_m.group(2))
        if not url or not title or url in seen:
            continue
        seen.add(url)
        pending = Result(title=title, url=url, engine="ddg-lite", rank=len(out))
        out.append(pending)
    return out


# --- transport -------------------------------------------------------------

def _post(client: httpx.Client, url: str, data: dict, referer: str = "") -> str:
    headers = {"User-Agent": UA_BROWSER, "Accept-Language": "en-US,en;q=0.9"}
    if referer:
        headers["Referer"] = referer
    resp = client.post(url, data=data, headers=headers)
    resp.raise_for_status()
    return resp.text


def search_ddg_html(query: str, client: httpx.Client, limit: int = 10) -> list[Result]:
    body = _post(client, DDG_HTML_URL, {"q": query, "kl": "us-en", "b": ""}, "https://html.duckduckgo.com/")
    return parse_ddg_html(body)[:limit]


def search_ddg_lite(query: str, client: httpx.Client, limit: int = 10) -> list[Result]:
    body = _post(client, DDG_LITE_URL, {"q": query}, "https://lite.duckduckgo.com/")
    return parse_ddg_lite(body)[:limit]


ENGINES = {"ddg-html": search_ddg_html, "ddg-lite": search_ddg_lite}
