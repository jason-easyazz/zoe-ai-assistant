"""
Browser broker for Zoe multi-surface browser orchestration.

This module is intentionally lightweight for phase-1 rollout:
- deterministic planning with a default OpenClaw surface
- pluggable executor registry for each surface
- shared evidence envelope for UI and telemetry consumers
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable, Literal

import httpx

BrowserSurface = Literal["openclawLocal", "touchPanel", "userDesktop", "harness"]
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
    selected_surface: BrowserSurface = "openclawLocal"
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

    def __init__(self, default_surface: BrowserSurface = "openclawLocal") -> None:
        self._default_surface = default_surface
        self._executors: dict[BrowserSurface, BrowserExecutor] = {}

    def register_executor(self, surface: BrowserSurface, executor: BrowserExecutor) -> None:
        self._executors[surface] = executor

    def default_surface(self) -> BrowserSurface:
        return self._default_surface

    def capabilities(self) -> list[dict[str, Any]]:
        """Return a normalized capability matrix for known browser backends."""
        known: list[BrowserSurface] = ["openclawLocal", "harness", "touchPanel", "userDesktop"]
        matrix: list[dict[str, Any]] = []
        for backend in known:
            available = backend in self._executors
            if backend == "openclawLocal":
                caps = BrowserBackendCapabilities(
                    backend=backend,
                    available=available,
                    supports_navigation=True,
                    supports_screenshot=True,
                    supports_action_log=True,
                    supports_live_user_browser=False,
                    notes=["Default deterministic backend for Zoe browser tasks."],
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
            "rule": "Prefer openclawLocal unless workload-level benchmarks prove another backend materially better.",
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


def build_openclaw_gateway_executor(openclaw_gateway_url: str) -> BrowserExecutor:
    """Create an OpenClaw browser executor backed by gateway HTTP endpoints."""

    def _discover_chromium_path() -> str | None:
        env_path = os.environ.get("OPENCLAW_CHROMIUM_PATH")
        if env_path and os.path.exists(env_path):
            return env_path
        cfg_path = os.path.expanduser("~/.openclaw/openclaw.json")
        try:
            with open(cfg_path, "r", encoding="utf-8") as fh:
                cfg = json.load(fh)
            path = ((cfg.get("browser") or {}).get("executablePath") or "").strip()
            if path and os.path.exists(path):
                return path
        except Exception:
            return None
        return None

    async def _capture_with_local_chromium(url: str) -> tuple[str | None, str | None]:
        chromium_path = _discover_chromium_path()
        if not chromium_path:
            return None, "Local Chromium path not configured or not found"

        async def _run_once(target_url: str) -> tuple[str | None, str | None]:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                screenshot_path = tmp.name
            proc: asyncio.subprocess.Process | None = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    chromium_path,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--no-first-run",
                    "--disable-dev-shm-usage",
                    "--ignore-certificate-errors",
                    f"--screenshot={screenshot_path}",
                    "--window-size=1365,1024",
                    target_url,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=20.0)
                if proc.returncode != 0:
                    err = (stderr or b"").decode(errors="replace").strip()
                    return None, f"Local Chromium screenshot failed (exit {proc.returncode}): {err}"
                if not os.path.exists(screenshot_path):
                    return None, "Local Chromium did not produce screenshot output"
                with open(screenshot_path, "rb") as fh:
                    image_b64 = base64.b64encode(fh.read()).decode("ascii")
                return image_b64, None
            except asyncio.TimeoutError:
                if proc and proc.returncode is None:
                    proc.kill()
                    await proc.wait()
                return None, "Local Chromium screenshot timed out"
            except Exception as exc:
                return None, f"Local Chromium screenshot failed: {exc}"
            finally:
                try:
                    os.remove(screenshot_path)
                except OSError:
                    pass

        # First try target URL, then guarantee screenshot pipeline with about:blank.
        image_b64, err = await _run_once(url)
        if image_b64:
            return image_b64, None
        fallback_b64, fallback_err = await _run_once("about:blank")
        if fallback_b64:
            return fallback_b64, err or "Captured blank-page fallback screenshot"
        return None, err or fallback_err or "Local Chromium screenshot failed"

    async def _execute(plan: BrowserActionPlan) -> dict[str, Any]:
        action_log: list[dict[str, Any]] = []
        navigate_to = (plan.params.get("navigate_to") or "").strip()
        timeout_s = float(plan.params.get("timeout_s", 20.0))
        screenshot_timeout_s = float(plan.params.get("screenshot_timeout_s", 20.0))

        try:
            async with httpx.AsyncClient(timeout=timeout_s) as client:
                if plan.action in {"capture_screenshot", "navigate_and_screenshot"} and navigate_to:
                    nav_resp = await client.post(
                        f"{openclaw_gateway_url}/browser/navigate",
                        json={"url": navigate_to},
                        timeout=timeout_s,
                    )
                    action_log.append(
                        {
                            "action": "navigate",
                            "url": navigate_to,
                            "status_code": nav_resp.status_code,
                            "ok": nav_resp.status_code < 400,
                        }
                    )

                if plan.action in {"capture_screenshot", "navigate_and_screenshot"}:
                    screenshot_resp = await client.post(
                        f"{openclaw_gateway_url}/browser/screenshot",
                        json={},
                        timeout=screenshot_timeout_s,
                    )
                    if screenshot_resp.status_code != 200:
                        action_log.append(
                            {
                                "action": "gateway_screenshot",
                                "ok": False,
                                "status_code": screenshot_resp.status_code,
                            }
                        )
                        if navigate_to:
                            image_b64, err = await _capture_with_local_chromium(navigate_to)
                            if image_b64:
                                local_action = {"action": "local_chromium_screenshot", "ok": True}
                                if err:
                                    local_action["note"] = err
                                evidence = BrowserEvidence(
                                    backend="openclawLocal",
                                    final_url=navigate_to,
                                    screenshots=["inline_base64"],
                                    action_log=action_log
                                    + [local_action],
                                    policy_decisions=[plan.policy_decision],
                                )
                                return {
                                    "ok": True,
                                    "image_base64": image_b64,
                                    "evidence": asdict(evidence),
                                }
                        return {
                            "ok": False,
                            "error": f"OpenClaw screenshot returned HTTP {screenshot_resp.status_code}",
                            "action_log": action_log,
                        }
                    data = screenshot_resp.json()
                    image_b64 = data.get("image") or data.get("screenshot") or data.get("data") or ""
                    if not image_b64:
                        action_log.append({"action": "gateway_screenshot_decode", "ok": False})
                        if navigate_to:
                            local_b64, err = await _capture_with_local_chromium(navigate_to)
                            if local_b64:
                                local_action = {"action": "local_chromium_screenshot", "ok": True}
                                if err:
                                    local_action["note"] = err
                                evidence = BrowserEvidence(
                                    backend="openclawLocal",
                                    final_url=navigate_to,
                                    screenshots=["inline_base64"],
                                    action_log=action_log
                                    + [local_action],
                                    policy_decisions=[plan.policy_decision],
                                )
                                return {
                                    "ok": True,
                                    "image_base64": local_b64,
                                    "evidence": asdict(evidence),
                                }
                        return {
                            "ok": False,
                            "error": "No screenshot data in OpenClaw response",
                            "action_log": action_log,
                            "raw_response": data,
                        }
                    evidence = BrowserEvidence(
                        backend="openclawLocal",
                        final_url=navigate_to or None,
                        screenshots=["inline_base64"],
                        action_log=action_log
                        + [
                            {
                                "action": "screenshot",
                                "status_code": screenshot_resp.status_code,
                                "ok": True,
                            }
                        ],
                        policy_decisions=[plan.policy_decision],
                    )
                    return {
                        "ok": True,
                        "image_base64": image_b64,
                        "evidence": asdict(evidence),
                    }

                return {
                    "ok": False,
                    "error": f"unsupported action '{plan.action}' for OpenClaw executor",
                    "action_log": action_log,
                }

        except httpx.ConnectError:
            action_log.append(
                {"action": "gateway_connect", "ok": False, "error": "OpenClaw gateway unreachable"}
            )
            if plan.action in {"capture_screenshot", "navigate_and_screenshot"} and navigate_to:
                image_b64, err = await _capture_with_local_chromium(navigate_to)
                if image_b64:
                    local_action = {"action": "local_chromium_screenshot", "ok": True}
                    if err:
                        local_action["note"] = err
                    evidence = BrowserEvidence(
                        backend="openclawLocal",
                        final_url=navigate_to,
                        screenshots=["inline_base64"],
                        action_log=action_log
                        + [
                            local_action,
                        ],
                        policy_decisions=[plan.policy_decision],
                    )
                    return {
                        "ok": True,
                        "image_base64": image_b64,
                        "evidence": asdict(evidence),
                    }
                return {"ok": False, "error": err or "OpenClaw gateway unreachable", "action_log": action_log}
            return {"ok": False, "error": "OpenClaw gateway unreachable", "action_log": action_log}
        except Exception as exc:
            return {"ok": False, "error": f"OpenClaw executor failed: {exc}"}

    return _execute


def create_default_browser_broker(openclaw_gateway_url: str) -> BrowserBroker:
    broker = BrowserBroker(default_surface="openclawLocal")
    broker.register_executor("openclawLocal", build_openclaw_gateway_executor(openclaw_gateway_url))
    return broker
