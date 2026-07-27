"""
Browser broker for Zoe multi-surface browser orchestration.

This module is intentionally lightweight for phase-1 rollout:
- deterministic planning with a default Zoe-native CloakBrowser surface
- pluggable executor registry for each surface
- shared evidence envelope for UI and telemetry consumers
"""

from __future__ import annotations

import base64
import uuid

from dataclasses import asdict, dataclass, field
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
