"""
Browser broker for Zoe multi-surface browser orchestration.

This module is intentionally lightweight for phase-1 rollout:
- deterministic planning with a default Zoe-native CloakBrowser surface
- pluggable executor registry for each surface
- shared evidence envelope for UI and telemetry consumers
"""

from __future__ import annotations

import base64
import re
import time
import uuid

from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable, Literal


# "hermesCloak" is a DEPRECATED ALIAS for "zoeCloak", accepted so any persisted
# plan/telemetry value still validates. OpenClaw surfaces are retired.
BrowserSurface = Literal["zoeCloak", "hermesCloak", "touchPanel", "userDesktop", "harness"]
BrowserActionClass = Literal[
    "read_only_research",
    "account_navigation",
    "form_entry",
    "transactional_submission",
]
PolicyDecision = Literal["allowed_auto", "requires_confirmation", "requires_live_takeover"]

BrowserExecutor = Callable[["BrowserActionPlan"], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class BrowserEvidence:
    backend: BrowserSurface
    final_url: str | None = None
    screenshots: list[str] = field(default_factory=list)
    action_log: list[dict[str, Any]] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    policy_decisions: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BrowserActionPlan:
    action: str
    params: dict[str, Any]
    user_id: str
    session_id: str
    action_class: BrowserActionClass = "read_only_research"
    requested_surface: BrowserSurface | None = None
    selected_surface: BrowserSurface = "zoeCloak"
    policy_decision: PolicyDecision = "allowed_auto"
    plan_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class BrowserBackendCapabilities:
    backend: BrowserSurface
    available: bool
    supports_navigation: bool
    supports_screenshot: bool
    supports_action_log: bool
    supports_live_user_browser: bool
    notes: list[str] = field(default_factory=list)


class BrowserBroker:
    """Simple deterministic broker used as a compatibility-safe first step."""

    def __init__(self, default_surface: BrowserSurface = "zoeCloak") -> None:
        self._default_surface = default_surface
        self._executors: dict[BrowserSurface, BrowserExecutor] = {}

    def register_executor(self, surface: BrowserSurface, executor: BrowserExecutor) -> None:
        self._executors[surface] = executor

    def default_surface(self) -> BrowserSurface:
        return self._default_surface

    def capabilities(self) -> list[dict[str, Any]]:
        """Return a normalized capability matrix for known browser backends."""
        # "hermesCloak" is deliberately NOT advertised here: it is a deprecated
        # execution-only alias (registered so persisted plans still run) and new
        # clients picking surfaces from capabilities() must not adopt it.
        known: list[BrowserSurface] = ["zoeCloak", "harness", "touchPanel", "userDesktop"]
        matrix: list[dict[str, Any]] = []
        for backend in known:
            available = backend in self._executors
            if backend == "zoeCloak":
                caps = BrowserBackendCapabilities(
                    backend=backend,
                    available=available,
                    supports_navigation=True,
                    supports_screenshot=True,
                    supports_action_log=True,
                    supports_live_user_browser=False,
                    notes=["Default backend: Zoe-native CloakBrowser stealth Chromium."],
                )
            elif backend == "harness":
                caps = BrowserBackendCapabilities(
                    backend=backend,
                    available=available,
                    supports_navigation=True,
                    supports_screenshot=True,
                    supports_action_log=True,
                    supports_live_user_browser=True,
                    notes=["Specialist backend for brittle/complex browser mechanics."],
                )
            elif backend == "touchPanel":
                caps = BrowserBackendCapabilities(
                    backend=backend,
                    available=available,
                    supports_navigation=True,
                    supports_screenshot=False,
                    supports_action_log=True,
                    supports_live_user_browser=False,
                    notes=["Display surface and control plane for panel UX."],
                )
            else:
                caps = BrowserBackendCapabilities(
                    backend=backend,
                    available=available,
                    supports_navigation=True,
                    supports_screenshot=True,
                    supports_action_log=True,
                    supports_live_user_browser=True,
                    notes=["Requires explicit consent lease and policy gate."],
                )
            matrix.append(asdict(caps))
        return matrix

    def compare_backends(self) -> dict[str, Any]:
        """Provide side-by-side backend summary and current recommendation."""
        matrix = self.capabilities()
        available = [m["backend"] for m in matrix if m["available"]]
        recommendation = {
            "default": self._default_surface,
            "rule": "Zoe-native CloakBrowser is the only browser surface; Hermes/OpenClaw are retired.",
            "available_backends": available,
        }
        return {"matrix": matrix, "recommendation": recommendation}

    def plan_action(
        self,
        *,
        action: str,
        params: dict[str, Any],
        user_id: str,
        session_id: str,
        action_class: BrowserActionClass = "read_only_research",
        requested_surface: BrowserSurface | None = None,
    ) -> BrowserActionPlan:
        surface = requested_surface or self._default_surface
        notes: list[str] = []
        if requested_surface and requested_surface not in self._executors:
            notes.append(
                f"requested surface '{requested_surface}' unavailable; falling back to '{self._default_surface}'"
            )
            surface = self._default_surface

        return BrowserActionPlan(
            action=action,
            params=params,
            user_id=user_id,
            session_id=session_id,
            action_class=action_class,
            requested_surface=requested_surface,
            selected_surface=surface,
            notes=notes,
        )

    async def execute(self, plan: BrowserActionPlan) -> dict[str, Any]:
        executor = self._executors.get(plan.selected_surface)
        if executor is None:
            return {
                "ok": False,
                "error": f"no executor registered for surface '{plan.selected_surface}'",
                "plan_id": plan.plan_id,
                "surface": plan.selected_surface,
            }
        result = await executor(plan)
        if "plan_id" not in result:
            result["plan_id"] = plan.plan_id
        if "surface" not in result:
            result["surface"] = plan.selected_surface
        return result




def target_url(params: dict[str, Any]) -> str:
    """The URL a plan wants to open, accepting BOTH spellings.

    chat.py's research screenshots pass ``navigate_to``; the MCP browser tool
    passes ``url``. Before the OpenClaw surface was retired these were served by
    different executors, so the surviving Zoe-native executor must honour both or
    screenshots silently navigate nowhere. Module-level (not buried in the
    executor closure) so it is testable without the browser package installed.
    """
    for key in ("url", "navigate_to"):
        value = str(params.get(key) or "").strip()
        if value:
            return value
    return ""


# --- text extraction -------------------------------------------------------
#
# WHY THIS LIVES HERE AND USES ONLY THE STDLIB
#
# The broker's CloakBrowser executor returned a base64 PNG and nothing else, so
# every text consumer (chat research packets, the web-search eval harness) had
# to bypass the broker entirely. `mcp_server.py`'s `cloakbrowser_fetch` already
# reached past it with a raw `page.locator("body").inner_text()` — which is the
# whole page including nav/footer/cookie banners, i.e. mostly boilerplate.
#
# Extraction is deliberately a PURE FUNCTION over an HTML string rather than a
# live-page call, for two reasons: it is the offline test seam (same convention
# as `tavily.parse_tavily` in the lab spike), and it keeps the readability
# scoring identical whether the HTML came from CloakBrowser, httpx, or a
# fixture.
#
# NO NEW DEPENDENCY. Checked before writing this: `readability`, `trafilatura`,
# `bs4`, `html2text`, `justext`, `selectolax` are all ABSENT on this box. `lxml`
# 6.1.0 is installed but only transitively — it is NOT in
# services/zoe-data/requirements.txt, so depending on it would mean adding a
# compiled dependency to a live service for a text-trimming helper. The stdlib
# `html.parser` is sufficient for readability-lite scoring, so requirements.txt
# is untouched by this change.

_DROP_TAGS = frozenset({
    "script", "style", "noscript", "svg", "iframe", "form", "nav", "header",
    "footer", "aside", "template", "button", "select", "canvas", "object",
    "video", "audio", "figure", "picture",
})
_VOID_TAGS = frozenset({
    "br", "img", "hr", "input", "meta", "link", "source", "track", "wbr",
    "col", "area", "base", "embed", "param",
})
_BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "main", "h1", "h2", "h3", "h4", "h5",
    "h6", "li", "ul", "ol", "tr", "td", "th", "table", "blockquote", "pre",
    "dd", "dt", "dl", "body", "hr", "br",
})
# Containers eligible to BE the main-content block.
_CANDIDATE_TAGS = frozenset({"div", "section", "article", "main", "body", "td"})

_MIN_MAIN_CHARS = 200


@dataclass(slots=True)
class ExtractedText:
    """Result of readability-lite extraction. `text` is the main content only."""

    text: str
    title: str = ""
    chars: int = 0
    truncated: bool = False
    strategy: str = ""


class _Node:
    __slots__ = ("tag", "children", "parent")

    def __init__(self, tag: str, parent: "_Node | None" = None) -> None:
        self.tag = tag
        self.parent = parent
        self.children: list[Any] = []


class _TreeParser(HTMLParser):
    """Build a minimal element tree, discarding boilerplate containers wholesale."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("[root]")
        self._cur = self.root
        self._drop_depth = 0
        self._title_parts: list[str] = []
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:  # noqa: ARG002
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
            return
        if self._drop_depth:
            if tag not in _VOID_TAGS:
                self._drop_depth += 1
            return
        if tag in _DROP_TAGS:
            self._drop_depth = 1
            return
        if tag in _VOID_TAGS:
            self._cur.children.append(_Node(tag, self._cur))
            return
        node = _Node(tag, self._cur)
        self._cur.children.append(node)
        self._cur = node

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
            return
        if self._drop_depth:
            self._drop_depth -= 1
            return
        if tag in _VOID_TAGS:
            return
        # Walk up to the matching open tag; tolerate unclosed/mismatched markup
        # rather than corrupting the tree (real pages are rarely well-formed).
        node = self._cur
        while node is not self.root and node.tag != tag:
            node = node.parent or self.root
        if node is not self.root:
            self._cur = node.parent or self.root

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)
            return
        if self._drop_depth or not data.strip():
            return
        self._cur.children.append(data)

    @property
    def title(self) -> str:
        return _collapse_ws(" ".join(self._title_parts))


def _collapse_ws(value: str) -> str:
    """Collapse ALL whitespace runs, newlines included.

    Source newlines inside a text run are formatting, not structure — HTML
    wraps a paragraph across source lines and that must not survive into the
    output, or a phrase spanning a wrap ("removes the\\nnetwork round trip")
    becomes unsearchable. Real line breaks come from block tags in
    `_node_text`, which appends them as separate parts after this runs.
    """
    return re.sub(r"\s+", " ", value).strip()


def _node_text(node: _Node) -> str:
    """Render a node's text with block-level newlines."""
    parts: list[str] = []

    def walk(n: _Node) -> None:
        for child in n.children:
            if isinstance(child, str):
                parts.append(_collapse_ws(child))
            else:
                block = child.tag in _BLOCK_TAGS
                if block:
                    parts.append("\n")
                walk(child)
                if block:
                    parts.append("\n")

    walk(node)
    return " ".join(p for p in parts if p).replace(" \n ", "\n").replace(" \n", "\n").replace("\n ", "\n")


def _link_text_len(node: _Node) -> int:
    total = 0
    stack: list[_Node] = [node]
    while stack:
        n = stack.pop()
        for child in n.children:
            if isinstance(child, _Node):
                if child.tag == "a":
                    total += len(_collapse_ws(_node_text(child)))
                else:
                    stack.append(child)
    return total


def _count_tag(node: _Node, tag: str) -> int:
    total = 0
    stack: list[_Node] = [node]
    while stack:
        n = stack.pop()
        for child in n.children:
            if isinstance(child, _Node):
                if child.tag == tag:
                    total += 1
                stack.append(child)
    return total


def _tidy(text: str) -> str:
    """Boilerplate trim: drop junk lines, dedupe repeats, collapse blank runs."""
    out: list[str] = []
    seen_recent: list[str] = []
    for raw in text.splitlines():
        line = _collapse_ws(raw)
        if not line:
            if out and out[-1] != "":
                out.append("")
            continue
        # A line with no letters or digits is punctuation/icon residue.
        if not re.search(r"[0-9A-Za-zÀ-￿]", line):
            continue
        # Consecutive duplicate lines are nav/menu echoes.
        if line in seen_recent[-3:]:
            continue
        seen_recent.append(line)
        out.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()


def extract_main_text(html: str, *, text_limit: int = 20_000) -> ExtractedText:
    """Readability-lite main-content extraction from an HTML string. PURE.

    Scores candidate containers by text volume discounted by link density (a
    nav block is mostly anchor text; an article is mostly prose), prefers an
    explicit ``<article>``/``<main>`` when one carries enough text, and falls
    back to the whole body. Returns main content, NOT the whole page.
    """
    if not html or not html.strip():
        return ExtractedText(text="", title="", chars=0, strategy="empty")

    parser = _TreeParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup must degrade, not raise
        pass

    root = parser.root
    title = parser.title

    # 1. Semantic shortcut: an explicit article/main container that carries real text.
    semantic: list[_Node] = []
    stack: list[_Node] = [root]
    while stack:
        n = stack.pop()
        for child in n.children:
            if isinstance(child, _Node):
                if child.tag in ("article", "main"):
                    semantic.append(child)
                stack.append(child)
    for node in semantic:
        body = _tidy(_node_text(node))
        if len(body) >= _MIN_MAIN_CHARS:
            return _finish(body, title, text_limit, f"semantic:<{node.tag}>")

    # 2. Score candidate containers.
    #
    # Link density is penalised QUADRATICALLY, not linearly. A wrapper <div>
    # holding nav + article + footer always has more raw text than the article
    # alone, so under a linear penalty the wrapper wins by a hair and drags the
    # whole page's boilerplate in with it (measured on the divsoup fixture:
    # wrapper 779 vs content 775). Squaring makes prose dilution expensive
    # enough that the tight container wins on merit.
    candidates: list[tuple[float, int, _Node]] = []
    stack_d: list[tuple[_Node, int]] = [(root, 0)]
    while stack_d:
        n, depth = stack_d.pop()
        for child in n.children:
            if not isinstance(child, _Node):
                continue
            stack_d.append((child, depth + 1))
            if child.tag not in _CANDIDATE_TAGS:
                continue
            text = _collapse_ws(_node_text(child))
            if len(text) < _MIN_MAIN_CHARS:
                continue
            link_density = min(1.0, _link_text_len(child) / max(1, len(text)))
            score = len(text) * (1.0 - link_density) ** 2 + 25 * _count_tag(child, "p")
            candidates.append((score, depth + 1, child))

    if candidates:
        top = max(c[0] for c in candidates)
        # Tie-break toward the DEEPEST (most specific) container that still
        # scores essentially as well — "the tightest node holding basically all
        # the content" — so a near-tie never resolves to the outer wrapper.
        near = [c for c in candidates if c[0] >= 0.95 * top]
        chosen = max(near, key=lambda c: c[1])[2]
        return _finish(_tidy(_node_text(chosen)), title, text_limit, "scored-container")

    # 3. Fallback: everything that survived the drop-list.
    return _finish(_tidy(_node_text(root)), title, text_limit, "fallback:whole-document")


def _finish(body: str, title: str, text_limit: int, strategy: str) -> ExtractedText:
    truncated = len(body) > text_limit
    if truncated:
        body = body[:text_limit]
    return ExtractedText(
        text=body, title=title, chars=len(body), truncated=truncated, strategy=strategy
    )


async def fetch_page_text(
    url: str,
    *,
    text_limit: int = 20_000,
    timeout_ms: int = 30_000,
    wait_until: str = "domcontentloaded",
) -> dict[str, Any]:
    """Fetch ONE url through CloakBrowser and return extracted main-content text.

    The in-process entry point: importable and awaitable directly, with no
    broker/plan/executor ceremony. `execute_text_extraction` is the same
    capability reached through the normal broker plan pipeline.

    Never raises for an ordinary failure — returns ``{"ok": False, "error": ...}``
    so a caller enriching a search packet degrades instead of exploding.
    """
    import importlib.util

    if importlib.util.find_spec("cloakbrowser") is None:
        return {"ok": False, "error": "cloakbrowser_not_installed"}
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "url must start with http:// or https://"}

    from agent_safety import assert_public_url, guard_browser_page

    try:
        assert_public_url(url)
    except Exception as exc:  # noqa: BLE001
        import logging

        logging.getLogger(__name__).info("text extract refused %s (%s)", url[:80], exc)
        return {"ok": False, "error": "refused: url does not resolve to a public address"}

    from cloakbrowser import launch_context_async  # type: ignore[import]

    started = time.monotonic()
    try:
        context = await launch_context_async(headless=True)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"CloakBrowser launch failed: {exc}"}
    try:
        page = await context.new_page()
        await guard_browser_page(page)
        await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
        final_url = page.url
        html = await page.content()
        page_title = await page.title()
        extracted = extract_main_text(html, text_limit=text_limit)
        return {
            "ok": True,
            "url": url,
            "final_url": final_url,
            "title": extracted.title or page_title,
            "text": extracted.text,
            "chars": extracted.chars,
            "truncated": extracted.truncated,
            "strategy": extracted.strategy,
            "elapsed_s": round(time.monotonic() - started, 3),
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"CloakBrowser text extraction failed: {exc}"}
    finally:
        await context.close()


async def execute_text_extraction(
    broker: BrowserBroker,
    *,
    url: str,
    user_id: str,
    session_id: str,
    text_limit: int = 20_000,
) -> dict[str, Any]:
    """Run text extraction through the broker's normal plan/execute pipeline."""
    plan = broker.plan_action(
        action="extract_text",
        params={"url": url, "text_limit": text_limit},
        user_id=user_id,
        session_id=session_id,
        action_class="read_only_research",
    )
    return await broker.execute(plan)


def build_cloak_executor() -> BrowserExecutor | None:
    """Build a CloakBrowser executor for bot-protected targets, if installed.

    CloakBrowser (pip install cloakbrowser) is a stealth Chromium with 49 source-level
    fingerprint patches. Drop-in Playwright replacement — passes Cloudflare Turnstile,
    FingerprintJS, and 30+ detection sites. ARM64 Linux supported (Jetson Orin NX).

    Returns None if cloakbrowser is not installed (graceful degradation).
    """
    import importlib.util
    if importlib.util.find_spec("cloakbrowser") is None:
        return None

    async def _execute(plan: BrowserActionPlan) -> dict[str, Any]:
        try:
            from cloakbrowser import launch_context_async  # type: ignore[import]
            action_log: list[dict] = []
            url = target_url(plan.params)
            if not url:
                return {"ok": False, "error": "no url/navigate_to in plan params"}
            # SSRF: chat research derives this URL from model/search output, so it is
            # untrusted. Validate the initial target, then install the route guard
            # (which re-checks every redirect hop pre-connect) — the same protection
            # zoe_agent._web_browse and the MCP cloakbrowser_* tools use.
            from agent_safety import assert_public_url, guard_browser_page
            try:
                assert_public_url(url)
            except Exception as exc:  # noqa: BLE001
                # log detail; the returned error must not echo the resolved
                # internal IP that assert_public_url includes in its message
                import logging
                logging.getLogger(__name__).info("cloak executor refused %s (%s)", url[:80], exc)
                return {"ok": False, "error": "refused: url does not resolve to a public address"}
            # launch_context_async returns a BrowserContext directly (not an async ctx manager)
            context = await launch_context_async(headless=True)
            try:
                page = await context.new_page()
                await guard_browser_page(page)
                action_log.append({"action": "navigate", "url": url})
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                final_url = page.url
                action_log.append({"action": "loaded", "url": final_url})

                # TEXT MODE. Opt-in via the plan action, so every existing
                # caller (chat.py research screenshots, the MCP browser tool)
                # keeps getting a PNG and is unaffected. Screenshotting is
                # skipped here deliberately: encoding a PNG we would throw away
                # costs time and a few MB of peak RSS per page.
                if plan.action in ("extract_text", "fetch_text"):
                    html = await page.content()
                    page_title = await page.title()
                    limit = int(plan.params.get("text_limit") or 20_000)
                    extracted = extract_main_text(html, text_limit=limit)
                    action_log.append(
                        {"action": "extract_text", "chars": extracted.chars,
                         "strategy": extracted.strategy}
                    )
                    evidence = BrowserEvidence(
                        backend=plan.selected_surface,
                        final_url=final_url,
                        screenshots=[],
                        action_log=action_log,
                        sources=[final_url],
                        policy_decisions=[plan.policy_decision],
                    )
                    return {
                        "ok": True,
                        "text": extracted.text,
                        "title": extracted.title or page_title,
                        "chars": extracted.chars,
                        "truncated": extracted.truncated,
                        "strategy": extracted.strategy,
                        "final_url": final_url,
                        "evidence": asdict(evidence),
                    }

                screenshot_bytes = await page.screenshot(type="png", full_page=False)
                image_b64 = base64.b64encode(screenshot_bytes).decode()
                evidence = BrowserEvidence(
                    backend=plan.selected_surface,
                    final_url=final_url,
                    screenshots=[image_b64] if image_b64 else [],
                    action_log=action_log,
                    sources=[final_url],
                    policy_decisions=[plan.policy_decision],
                )
                return {"ok": True, "image_base64": image_b64, "evidence": asdict(evidence)}
            finally:
                await context.close()
        except Exception as exc:
            return {"ok": False, "error": f"CloakBrowser executor failed: {exc}"}

    return _execute


def create_default_browser_broker(openclaw_gateway_url: str | None = None) -> BrowserBroker:
    """Zoe's browser broker: a single Zoe-native CloakBrowser surface.

    ``openclaw_gateway_url`` is accepted but IGNORED — kept so existing callers
    keep working while the OpenClaw retirement lands. The OpenClaw fallback
    surface (already operator-flag-dark) and its gateway executor are removed.
    """
    broker = BrowserBroker(default_surface="zoeCloak")
    cloak_exec = build_cloak_executor()
    if cloak_exec is not None:
        broker.register_executor("zoeCloak", cloak_exec)
        # legacy alias: a stored plan naming the old surface still executes
        broker.register_executor("hermesCloak", cloak_exec)
        broker.register_executor("harness", cloak_exec)
    return broker
