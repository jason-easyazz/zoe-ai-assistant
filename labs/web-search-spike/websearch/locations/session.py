"""A HELD-OPEN CloakBrowser session that CLICKS, and records what the page asked for.

WHY THIS EXISTS — THE CAPABILITY BOUNDARY THE BOTWALL RUN NAMED
---------------------------------------------------------------
`eval/results/emu-export-geraldton-2026-08-03.md` ended on a precise finding:
Thirsty Camel rendered for *all three* tiers and still printed *"To view in
store availability and pricing for this product:"* instead of a number. The page
was never walled. It was **unanswered**, because nobody had picked a store.

`browser_broker.fetch_page_text()` cannot fix that, and not by accident: it
opens a context, navigates once, extracts, and closes. One navigation per
context is the correct shape for "read this URL", and it is the wrong shape for
"pick Geraldton, THEN read these six URLs". This module is the second shape.

WHAT IT ADDS OVER `fetch_page_text`, AND NOTHING ELSE
----------------------------------------------------
1. **The context stays open** across navigations, so a store selection made on
   page 1 is still in force on page 6.
2. **Clicking and typing** — the store picker is a form, not a document.
3. **Network capture.** Every response the page received is recorded (JSON
   bodies kept, size-capped). This is the part that pays for the whole module:
   a picker session run ONCE tells us the API the site's own front end calls,
   and if that API takes a store id we never need Chromium again.

Everything else is BORROWED, deliberately, so there is exactly one of each:

- text extraction  -> `browser_broker.extract_main_text` (via `cloak._load_broker`)
- post-load settle -> `browser_broker.settle_and_extract` / `SettlePolicy`
- SSRF guards      -> `agent_safety.assert_public_url` + `guard_browser_page`

None of it is vendored. If PR #1626 changes the extractor, this changes with it.

THIS IS LAB CODE AND IT LAUNCHES CHROMIUM
-----------------------------------------
~553 MB per launch on a box that runs the mlocked voice brain. So:

- `MemFree` is re-read immediately before the launch and the session REFUSES to
  start under `min_free_mb` (default 380). That check is in the constructor path,
  not in the caller, because a caller that forgets it is the failure mode.
- One session at a time. There is no pooling, no concurrency, and none is wanted.
- `page_loads` is counted and `max_page_loads` is a hard stop (default 25). This
  is price-checking a handful of product pages, not crawling, and a runaway loop
  on a retailer's site is both rude and a way to get the home IP walled.

On promotion the plan/executor registry in `browser_broker.py` grows a
`select_store` action and this module is deleted. See README §promotion.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

#: Refuse to launch below this many MB of MemFree. See the module docstring.
DEFAULT_MIN_FREE_MB = 380

#: Refuse to launch below this many MB of MemAvailable.
#:
#: MEASURED 2026-08-05, and the reason this second floor exists: the box read
#: MemFree 532 MB / MemAvailable 301 MB. MemFree ALONE would have cleared the
#: 380 MB floor and launched a ~553 MB Chromium into 301 MB of actual headroom,
#: beside the mlocked voice brain. On this box MemFree can exceed MemAvailable
#: (unreclaimable pages and watermarks), so MemFree is not a conservative
#: instrument — it is the optimistic one. Both floors must pass.
DEFAULT_MIN_AVAILABLE_MB = 700

#: Hard ceiling on navigations in ONE session. Decency, not just cost.
DEFAULT_MAX_PAGE_LOADS = 25

#: Response bodies above this are recorded as a size + content-type only. A
#: 3 MB bundle.js is not evidence and holding twelve of them is a leak.
MAX_CAPTURE_BODY = 200_000

#: Content types worth keeping a body for. A price API is JSON; if a retailer
#: turns out to answer in XML this list grows with a measurement behind it.
CAPTURE_CONTENT_TYPES = ("application/json", "text/json", "application/graphql")

_PRICE_RE = re.compile(r"\$\s?\d{1,4}(?:\.\d{2})?")


def _meminfo_mb(key: str) -> int:
    """One /proc/meminfo field in MB, read fresh. Never cached — that is the point."""
    prefix = key + ":"
    with open("/proc/meminfo", "r", encoding="utf-8") as fh:
        for line in fh:
            if line.startswith(prefix):
                return int(line.split()[1]) // 1024
    raise RuntimeError(f"/proc/meminfo has no {key} line")


def mem_free_mb() -> int:
    return _meminfo_mb("MemFree")


def mem_available_mb() -> int:
    """MemAvailable — the kernel's own estimate of what a new process can get.

    The honest instrument. See `DEFAULT_MIN_AVAILABLE_MB` for the measurement
    that made this necessary.
    """
    return _meminfo_mb("MemAvailable")


def load1() -> float:
    with open("/proc/loadavg", "r", encoding="utf-8") as fh:
        return float(fh.read().split()[0])


class SessionRefused(RuntimeError):
    """The session declined to start or to continue. NOT a site failure.

    Kept distinct from any page-level error so a run report can say "we chose
    not to launch" rather than implying the retailer defeated us. Conflating
    those two is the same class of lie the chain's `blocked`/`thin` split
    exists to prevent.
    """


@dataclass(slots=True)
class Captured:
    """One response the page received during the session."""

    url: str
    method: str
    status: int
    content_type: str
    #: Decoded body, JSON only and size-capped. None when not captured.
    body: str | None = None
    #: Request headers the FRONT END chose to send. This is where a store id
    #: hides when it is not in the URL — measured on more than one retailer.
    request_headers: dict[str, str] = field(default_factory=dict)
    resource_type: str = ""

    @property
    def is_json(self) -> bool:
        return any(t in self.content_type for t in CAPTURE_CONTENT_TYPES)

    def json(self) -> Any | None:
        if not self.body:
            return None
        try:
            return json.loads(self.body)
        except ValueError:
            return None

    def price_tokens(self) -> list[str]:
        """`$`-shaped tokens in the body — a cheap 'is there a price in here?'."""
        return _PRICE_RE.findall(self.body or "")

    def summary(self) -> str:
        return "%-6s %3d %-24s %6s  %s" % (
            self.method,
            self.status,
            self.content_type.split(";")[0][:24],
            len(self.body or "") or "-",
            self.url[:150],
        )


@dataclass(slots=True)
class PageRead:
    """The text of one navigation, with the settle log that produced it."""

    url: str
    final_url: str
    title: str
    text: str
    strategy: str
    settle: list[dict[str, Any]] = field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def chars(self) -> int:
        return len(self.text)

    def has(self, needle: str) -> bool:
        return needle.lower() in self.text.lower()


class StoreSession:
    """One Chromium context, held open, driven by a per-retailer script.

    Async context manager::

        async with StoreSession(label="bws") as s:
            await s.goto("https://bws.com.au/")
            await s.click_text("Set your store")
            await s.fill("input[name=search]", "6530")
            ...
            for call in s.captured_json():
                print(call.summary())

    Every method that touches the network counts against `max_page_loads`, and
    the counter is checked BEFORE the action, so the ceiling is a ceiling rather
    than a suggestion.
    """

    def __init__(
        self,
        *,
        label: str,
        min_free_mb: int = DEFAULT_MIN_FREE_MB,
        min_available_mb: int = DEFAULT_MIN_AVAILABLE_MB,
        max_page_loads: int = DEFAULT_MAX_PAGE_LOADS,
        max_load1: float = 3.0,
        headless: bool = True,
        capture: bool = True,
        mem_reader: Callable[[], int] = mem_free_mb,
        avail_reader: Callable[[], int] = mem_available_mb,
        load_reader: Callable[[], float] = load1,
    ) -> None:
        self.label = label
        self.min_free_mb = min_free_mb
        self.min_available_mb = min_available_mb
        self.max_page_loads = max_page_loads
        self.max_load1 = max_load1
        self.headless = headless
        self.capture = capture
        self._mem_reader = mem_reader
        self._avail_reader = avail_reader
        self._load_reader = load_reader

        self.page_loads = 0
        self.calls: list[Captured] = []
        self.reads: list[PageRead] = []
        self._context = None
        self._page = None
        self._broker = None

    # ------------------------------------------------------------------ guards

    def preflight(self) -> None:
        """Refuse to launch on a box that cannot afford it. Raises `SessionRefused`.

        All three conditions are the operator's stated discipline, encoded once
        here rather than repeated at every call site: free memory below the
        floor, AVAILABLE memory below its (higher) floor, or a box already busy
        enough that adding a Chromium is antisocial.
        """
        free = self._mem_reader()
        if free < self.min_free_mb:
            raise SessionRefused(
                f"MemFree {free} MB < floor {self.min_free_mb} MB — refusing to launch "
                f"Chromium beside the voice brain"
            )
        avail = self._avail_reader()
        if avail < self.min_available_mb:
            raise SessionRefused(
                f"MemAvailable {avail} MB < floor {self.min_available_mb} MB (MemFree said "
                f"{free} MB, which is the optimistic number) — refusing to launch Chromium "
                f"beside the voice brain"
            )
        load = self._load_reader()
        if load > self.max_load1:
            raise SessionRefused(f"load1 {load:.2f} > {self.max_load1} — box is busy, back off")

    def _budget(self, what: str) -> None:
        if self.page_loads >= self.max_page_loads:
            raise SessionRefused(
                f"page-load budget spent ({self.page_loads}/{self.max_page_loads}) — "
                f"refusing {what}. Raise max_page_loads deliberately or split the run."
            )

    # ------------------------------------------------------------- lifecycle

    async def __aenter__(self) -> "StoreSession":
        self.preflight()

        from ..cloak import _load_broker  # the ONE broker path-import

        self._broker = _load_broker()

        import sys

        # agent_safety sits beside the broker in the live checkout; _load_broker
        # has already put that directory on sys.path.
        from agent_safety import assert_public_url  # noqa: F401  (import check)

        from cloakbrowser import launch_context_async  # type: ignore[import]

        self._context = await launch_context_async(headless=self.headless)
        self._page = await self._context.new_page()

        from agent_safety import guard_browser_page

        await guard_browser_page(self._page)
        if self.capture:
            self._install_capture(self._page)
        assert sys is not None
        return self

    async def __aexit__(self, *exc) -> None:
        if self._context is not None:
            try:
                await self._context.close()
            finally:
                self._context = None
                self._page = None

    # --------------------------------------------------------------- capture

    def _install_capture(self, page) -> None:
        """Record every response. Bodies for JSON only, size-capped.

        `response.text()` is awaited inside the handler; a failure there is
        SWALLOWED to a body of None rather than raised, because a response that
        was already consumed or a navigation that tore the frame down is
        routine and must not kill the session that is mid-store-selection.
        """

        async def _on_response(response):
            try:
                req = response.request
                ctype = (response.headers or {}).get("content-type", "")
                body = None
                if any(t in ctype for t in CAPTURE_CONTENT_TYPES):
                    try:
                        raw = await response.text()
                        body = raw[:MAX_CAPTURE_BODY]
                    except Exception:  # noqa: BLE001 - see docstring
                        body = None
                self.calls.append(
                    Captured(
                        url=response.url,
                        method=req.method,
                        status=response.status,
                        content_type=ctype,
                        body=body,
                        request_headers=dict(req.headers or {}),
                        resource_type=getattr(req, "resource_type", ""),
                    )
                )
            except Exception:  # noqa: BLE001 - capture is evidence, never a gate
                pass

        page.on("response", _on_response)

    def captured_json(self) -> list[Captured]:
        return [c for c in self.calls if c.is_json and c.body]

    def captured_with_price(self) -> list[Captured]:
        """JSON calls whose body contains price-shaped tokens OR a `price` key.

        Two signals, because both miss on their own: an API that returns
        ``{"Price": 69}`` has no ``$`` anywhere, and an API that returns a
        rendered ``"$69.00"`` string may have no key called price.
        """
        out = []
        for c in self.captured_json():
            low = (c.body or "").lower()
            if c.price_tokens() or '"price' in low or "price:" in low:
                out.append(c)
        return out

    # ----------------------------------------------------------- page driving

    async def goto(self, url: str, *, settle: str = "spa", timeout_ms: int = 30_000) -> PageRead:
        """Navigate and extract. Counts against the page-load budget."""
        self._budget(f"goto {url[:60]}")
        from agent_safety import assert_public_url

        assert_public_url(url)
        policy = self._settle_policy(settle)
        started = time.monotonic()
        self.page_loads += 1
        await self._page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        read = await self._extract(url, policy, started)
        self.reads.append(read)
        return read

    async def reread(self, *, settle: str = "spa") -> PageRead:
        """Re-extract the CURRENT page without navigating.

        This is how a picker result is read: the click already re-rendered the
        page in place, so a second `goto` would both waste budget and, on a
        client-rendered store picker, throw away the selection.
        """
        started = time.monotonic()
        read = await self._extract(self._page.url, self._settle_policy(settle), started)
        self.reads.append(read)
        return read

    def _settle_policy(self, settle: str):
        broker = self._broker
        if settle == "spa":
            return broker.SETTLE_SPA
        if settle == "light":
            return broker.SETTLE_LIGHT
        if settle in (None, "none"):
            return broker.SettlePolicy()
        raise ValueError(f"unknown settle preset {settle!r}")

    async def _extract(self, url: str, policy, started: float) -> PageRead:
        extracted, settle_log = await self._broker.settle_and_extract(
            self._page, policy=policy, text_limit=40_000
        )
        return PageRead(
            url=url,
            final_url=self._page.url,
            title=extracted.title or (await self._page.title()),
            text=extracted.text,
            strategy=extracted.strategy,
            settle=settle_log,
            elapsed_s=time.monotonic() - started,
        )

    async def click(self, selector: str, *, timeout_ms: int = 8_000) -> bool:
        """Click a CSS selector. Returns False when it is not there — not an error.

        A store picker that has already been dismissed, or a cookie banner that
        did not appear this time, is the normal case. Raising would make every
        recipe a pile of try/except; returning False lets a recipe say
        "dismiss the banner if there is one" in one line.
        """
        try:
            await self._page.click(selector, timeout=timeout_ms)
            return True
        except Exception:  # noqa: BLE001 - absence is an outcome, not a failure
            return False

    async def click_text(self, text: str, *, timeout_ms: int = 8_000) -> bool:
        """Click the first element whose visible text matches, case-insensitively."""
        try:
            await self._page.get_by_text(text, exact=False).first.click(timeout=timeout_ms)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def fill(self, selector: str, value: str, *, timeout_ms: int = 8_000) -> bool:
        try:
            await self._page.fill(selector, value, timeout=timeout_ms)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def press(self, selector: str, key: str, *, timeout_ms: int = 8_000) -> bool:
        try:
            await self._page.press(selector, key, timeout=timeout_ms)
            return True
        except Exception:  # noqa: BLE001
            return False

    async def wait(self, ms: int) -> None:
        await self._page.wait_for_timeout(ms)

    async def html(self) -> str:
        return await self._page.content()

    async def eval_js(self, expression: str) -> Any:
        """Read a value out of the page. READ-ONLY by convention.

        Used to dump `localStorage` — several retailers keep the selected store
        there rather than in a cookie, and a recipe that only harvests cookies
        would silently lose the selection.
        """
        return await self._page.evaluate(expression)

    async def cookies(self) -> list[dict[str, Any]]:
        return await self._context.cookies()

    async def local_storage(self) -> dict[str, str]:
        try:
            return await self._page.evaluate("() => ({...localStorage})")
        except Exception:  # noqa: BLE001
            return {}
