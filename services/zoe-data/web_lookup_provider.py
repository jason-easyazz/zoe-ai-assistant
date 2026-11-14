"""The web-lookup PROVIDER SEAM: the one door a query leaves the house through.

``lookup(provider, scrub_result)`` is the only entry point. It takes a
``web_lookup_scrub.ScrubResult``, never a bare string, so a query that has not
been through the scrubber cannot be sent by construction; a refused scrub is
answered ``refused`` and the provider is never called.

Providers:
  * ``FakeProvider``  - scripted answers, records every query it was handed.
                         Tests and ``labs/web-lookup/`` use nothing else.
  * ``LiveProvider``  - the ONE real adapter: ``research_evidence.fetch_web_fallback``
                         (Tavily when keyed, else DuckDuckGo) with the seam's caps.
  * ``OffProvider``   - what ``provider_for_env()`` returns while
                         ``ZOE_WEB_SEARCH_TOOL`` is off: answers ``off``, opens nothing.

Outbound rules, the same as any guarded fetch plus this seam's own:
  * no private, loopback or link-local address, IP-pinned (``agent_safety``);
  * at most ``MAX_REDIRECTS`` hops, each re-validated (Tavily: none followed);
  * a hard end-to-end deadline enforced HERE, whatever the provider does;
  * provider body capped (``MAX_PROVIDER_BYTES``), and what crosses back is
    capped again: ``MAX_ROWS`` rows, each field cut, ``MAX_PAYLOAD_CHARS`` in all;
  * a result row is kept only with a public http(s) link (no credentials in
    it, no private or loopback literal, no single-label or local-only host).

Never log a query. Operator doc: docs/knowledge/web-lookup.md.
"""

from __future__ import annotations

import ipaddress
import threading
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit

from agent_safety import is_public_ip
from research_evidence import (
    WEB_LOOKUP_ERROR,
    WEB_LOOKUP_NO_RESULTS,
    WEB_LOOKUP_OFF,
    WEB_LOOKUP_RESULTS,
    WEB_SEARCH_TOOL_DEADLINE_S,
    WEB_SEARCH_TOOL_MAX_RESULTS,
    WEB_SEARCH_TOOL_PROVIDER_TIMEOUT_S,
    WebFallbackOutcome,
    fetch_web_fallback,
    web_search_tool_enabled,
)
from web_lookup_scrub import ScrubResult

WEB_LOOKUP_REFUSED = "refused"  # the scrubber withheld the query; nothing was sent
MAX_REDIRECTS = 3
MAX_PROVIDER_BYTES = 512 * 1024
MAX_ROWS = WEB_SEARCH_TOOL_MAX_RESULTS
MAX_TITLE_CHARS = 200
MAX_SNIPPET_CHARS = 500
MAX_URL_CHARS = 500
MAX_PAYLOAD_CHARS = 4000
_LOCAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home", ".home.arpa", ".intranet", ".corp")


class LookupProvider(Protocol):
    name: str

    def search(self, query: str, max_results: int, deadline_s: float) -> WebFallbackOutcome: ...


class OffProvider:
    name = "none"

    def search(self, query: str, max_results: int, deadline_s: float) -> WebFallbackOutcome:
        return WebFallbackOutcome(WEB_LOOKUP_OFF, "none", [], "ZOE_WEB_SEARCH_TOOL is off")


class LiveProvider:
    """The real adapter. One bounded search; no per-row page fetches."""

    name = "live"

    def __init__(self, fetch: Callable[..., WebFallbackOutcome] | None = None) -> None:
        self._fetch = fetch  # None = this module's fetch_web_fallback, looked up per call

    def search(self, query: str, max_results: int, deadline_s: float) -> WebFallbackOutcome:
        return (self._fetch or fetch_web_fallback)(
            query,
            max_results,
            WEB_SEARCH_TOOL_PROVIDER_TIMEOUT_S,
            enrich_prices=False,
            deadline_s=deadline_s,
            max_redirects=MAX_REDIRECTS,
            max_response_bytes=MAX_PROVIDER_BYTES,
        )


class FakeProvider:
    """Scripted provider. ``script`` maps an exact query to rows (a list), an outcome, an exception to raise, or a
    callable taking the query; ``default`` answers everything else. ``queries`` is everything that left the house."""

    name = "fake"

    def __init__(self, script: Mapping[str, Any] | None = None, default: Any = None) -> None:
        self.script = dict(script or {})
        self.default = default
        self.queries: list[str] = []

    def search(self, query: str, max_results: int, deadline_s: float) -> WebFallbackOutcome:
        self.queries.append(query)
        entry = self.script.get(query, self.default)
        if callable(entry):
            entry = entry(query)
        if isinstance(entry, BaseException):
            raise entry
        if isinstance(entry, WebFallbackOutcome):
            return entry
        if entry:
            return WebFallbackOutcome(WEB_LOOKUP_RESULTS, self.name, [dict(r) for r in entry])
        return WebFallbackOutcome(WEB_LOOKUP_NO_RESULTS, self.name, [])


def provider_for_env() -> LookupProvider:
    """The live adapter only while ``ZOE_WEB_SEARCH_TOOL`` is on (read per call)."""
    return LiveProvider() if web_search_tool_enabled() else OffProvider()


def public_link(url: object) -> str:
    """``url`` when it is a plain public http(s) link, else "". No DNS: literals and names only."""
    try:
        text = str(url or "").strip()
        parts = urlsplit(text)
        host = (parts.hostname or "").rstrip(".").lower()
        if parts.scheme.lower() not in ("http", "https") or not host or parts.username or parts.password:
            return ""
        if len(text) > MAX_URL_CHARS or any(c.isspace() or ord(c) < 0x20 for c in text):
            return ""
        try:
            return text if is_public_ip(ipaddress.ip_address(host)) else ""
        except ValueError:
            pass
        if "." not in host or host == "localhost" or host.endswith(_LOCAL_SUFFIXES) or host.replace(".", "").isdigit():
            return ""
        return text
    except Exception:  # noqa: BLE001 - an unparseable link is not a link
        return ""


def _cut(value: object, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def bound_rows(rows: object, max_results: int = MAX_ROWS) -> list[dict[str, str]]:
    """At most ``max_results`` rows of title/url/snippet, each field cut, the whole under ``MAX_PAYLOAD_CHARS``."""
    out: list[dict[str, str]] = []
    total = 0
    for row in rows if isinstance(rows, (list, tuple)) else ():
        if len(out) >= max(1, min(int(max_results), MAX_ROWS)):
            break
        if not isinstance(row, Mapping):
            continue
        url = public_link(row.get("url"))
        if not url:
            continue
        kept = {"title": _cut(row.get("title"), MAX_TITLE_CHARS), "url": url, "snippet": _cut(row.get("snippet"), MAX_SNIPPET_CHARS)}
        total += sum(len(v) for v in kept.values())
        if total > MAX_PAYLOAD_CHARS:
            break
        out.append(kept)
    return out


def lookup(
    provider: LookupProvider,
    scrub: ScrubResult,
    *,
    max_results: int = MAX_ROWS,
    deadline_s: float = WEB_SEARCH_TOOL_DEADLINE_S,
) -> WebFallbackOutcome:
    """Send ``scrub.query`` through ``provider`` under the seam's rules. Never raises."""
    if not isinstance(scrub, ScrubResult):
        raise TypeError("lookup() takes a ScrubResult: a query leaves the house only through the scrubber")
    if not scrub.send or not scrub.query:
        return WebFallbackOutcome(WEB_LOOKUP_REFUSED, "none", [], f"query withheld ({scrub.reason})")
    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["outcome"] = provider.search(scrub.query, max(1, min(int(max_results), MAX_ROWS)), deadline_s)
        except BaseException as exc:  # noqa: BLE001 - a provider failure is an honest error, never a crash
            box["error"] = type(exc).__name__

    worker = threading.Thread(target=_run, name="web-lookup", daemon=True)
    worker.start()
    worker.join(deadline_s)
    name = str(getattr(provider, "name", "") or "unknown")
    if worker.is_alive():  # still running: its own socket timeouts end it; nothing it returns is used
        return WebFallbackOutcome(WEB_LOOKUP_ERROR, name, [], f"timeout: lookup exceeded {deadline_s:g}s deadline")
    outcome = box.get("outcome")
    if not isinstance(outcome, WebFallbackOutcome):
        return WebFallbackOutcome(WEB_LOOKUP_ERROR, name, [], box.get("error") or "provider returned no outcome")
    if outcome.status != WEB_LOOKUP_RESULTS:
        return WebFallbackOutcome(outcome.status, outcome.provider, [], _cut(outcome.detail, 200))
    rows = bound_rows(outcome.results, max_results)
    if not rows:
        return WebFallbackOutcome(WEB_LOOKUP_NO_RESULTS, outcome.provider, [], "no usable public result")
    return WebFallbackOutcome(WEB_LOOKUP_RESULTS, outcome.provider, rows, _cut(outcome.detail, 200))
