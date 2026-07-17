import asyncio
import os
import time
import uuid as _uuid_mod
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import httpx
from database import init_db
from gemma_endpoint import gemma_base
from typed_env import env_float, env_int, env_list, env_str
from push import broadcaster
from auth import require_internal_token
from routers import (
    calendar_router,
    lists_router,
    people_router,
    memories_router,
    reminders_router,
    notes_router,
    journal_router,
    transactions_router,
    weather_router,
    system_router,
    notifications_router,
    chat_router,
    ui_router,
    voice_tts_router,
    user_profile_router,
    panel_auth_router,
    panel_provision_router,
    capability_matrix_router,
    music_router,
    skybridge_router,
    autoresearch_router,
    pi_intent_lab_router,
)
from routers.dashboard import router as dashboard_router
from routers.stubs import router as stubs_router
from routers.push import router as push_router
from routers.proactive import router as proactive_router
from routers.system import (
    start_openclaw_background_tasks,
    start_memory_digest_background,
    start_memory_consolidation_background,
    _agent_card_router,
)
from system_updates import start_zoe_update_background_tasks
from routers.openclaw import router as openclaw_router
from voice_presence import is_wake_payload, is_wake_text, wake_ack_events
import logging
from middleware.logging import setup_json_logging

# Setup JSON logging
setup_json_logging()
logger = logging.getLogger(__name__)

# Legacy variable kept for backward compatibility during transition
_REQUEST_ID_CTX_VAR = None
_openclaw_bg_task = None
_digest_bg_task = None
_consolidation_bg_task = None
_runtime_health_task = None
_zoe_update_bg_task = None
_skills_observer = None
_memory_capture_health: dict[str, str] = {"status": "unknown", "detail": "startup pending"}

# Runtime health dict — populated by _probe_runtimes() at startup; exported so
# routers.system can read it for agent card tier status.
_RUNTIME_HEALTH: dict[str, bool] = {
    "local_llm": False,
    "hermes": False,
    "openclaw": False,
}
# Timestamp of the last probe (ISO string); exposed via GET /api/agent/runtimes
_RUNTIME_LAST_PROBED: str = ""
_READINESS_CACHE: dict[str, object] = {"expires_at": 0.0, "report": None}
_READINESS_CACHE_LOCK = asyncio.Lock()


def _ws_idle_timeout_seconds() -> float:
    # typed_env (Wave-4 W4-T4): absent/empty/invalid -> default, same as the old
    # local try/except, + typed_env's warn-once on invalid values.
    value = env_float("ZOE_WS_IDLE_TIMEOUT_SECONDS", 120.0)
    if value <= 0:
        return 120.0
    return value


WS_IDLE_TIMEOUT_SECONDS = _ws_idle_timeout_seconds()


def _gemma_base_url() -> str:
    return gemma_base()


def _canonical_gemma_model(model_id: str) -> bool:
    normalized = (model_id or "").lower()
    return "gemma" in normalized and "e4b" in normalized


def _env_float(name: str, default: float) -> float:
    # Delegates to typed_env (Wave-4 W4-T4). Same absent/empty/invalid -> default
    # semantics as the old local try/except, + typed_env's warn-once on invalid.
    return env_float(name, default)


async def _check_brain_ready(timeout_s: float = 2.0) -> dict:
    base_url = _gemma_base_url()
    detail: dict = {"ok": False, "url": base_url}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            health = await client.get(f"{base_url}/health")
            detail["health_status"] = health.status_code
            if health.status_code != 200:
                detail["error"] = f"health_http_{health.status_code}"
                return detail

            detail["ok"] = True
            try:
                models = await client.get(f"{base_url}/v1/models")
                detail["models_status"] = models.status_code
                if models.status_code == 200:
                    payload = models.json()
                    model_ids = [
                        str(item.get("id") or item.get("model") or "")
                        for item in payload.get("data", [])
                        if isinstance(item, dict)
                    ]
                    detail["models"] = model_ids[:5]
                    # Telemetry only: current llama-server exposes a model id/path
                    # containing "gemma" and "e4b", but readiness is gated on the
                    # server's own /health so model-list shape drift cannot block boot.
                    detail["canonical_model_seen"] = any(
                        _canonical_gemma_model(model_id) for model_id in model_ids
                    )
            except Exception as models_exc:
                detail["models_error"] = models_exc.__class__.__name__
            return detail
    except Exception as exc:
        detail["error"] = exc.__class__.__name__
        return detail


async def _check_stt_ready() -> dict:
    try:
        from routers import voice_tts

        loaded = bool(voice_tts.moonshine_ready())
        load_error = voice_tts.moonshine_error()
        return {
            "ok": loaded or load_error is None,
            "engine": "moonshine",
            "arch": voice_tts.moonshine_arch(),
            "loaded": loaded,
            **({"error": load_error} if load_error else {}),
        }
    except Exception as exc:
        return {"ok": False, "engine": "moonshine", "error": exc.__class__.__name__}


async def _check_tts_ready(timeout_s: float = 2.0) -> dict:
    from routers import voice_tts

    mode = env_str("ZOE_TTS_MODE", "hybrid").lower()
    sidecar_url = os.environ.get("ZOE_KOKORO_SIDECAR_URL", "http://127.0.0.1:10201").rstrip("/")
    detail: dict = {
        "ok": False,
        "engine": "kokoro-waterfall",
        "mode": mode,
        "sidecar_url": sidecar_url,
    }
    if mode in {"edge", "cloud", "offline"}:
        detail["ok"] = True
        detail["provider"] = mode
        return detail
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            response = await client.get(f"{sidecar_url}/health")
        detail["status_code"] = response.status_code
        if response.status_code == 200:
            payload = response.json()
            detail["pipeline_loaded"] = bool(payload.get("pipeline_loaded"))
            detail["voice"] = payload.get("voice")
            detail["device"] = payload.get("device")
            # A sidecar that fell back to CPU still loads its pipeline and serves
            # audio, so ok stays True (flipping it would make the waterfall drop
            # Kokoro for espeak — worse). But CPU synthesis is slower than real time
            # and makes replies choppy, so surface it here: watchdogs gate only on
            # pipeline_loaded/ok and would otherwise never see the regression.
            if payload.get("degraded"):
                detail["degraded"] = True
                detail["degraded_reason"] = payload.get("degraded_reason")
            detail["ok"] = bool(payload.get("pipeline_loaded"))
            if not detail["ok"]:
                detail["error"] = "pipeline_not_loaded"
            else:
                detail["provider"] = "kokoro-sidecar"
                return detail
        detail["error"] = f"http_{response.status_code}"
    except Exception as exc:
        detail["error"] = exc.__class__.__name__
    detail["local_onnx_loaded"] = bool(voice_tts.kokoro_ready())
    detail["local_onnx_configured"] = bool(voice_tts.kokoro_configured())
    if detail["local_onnx_loaded"] or detail["local_onnx_configured"]:
        detail["ok"] = True
        detail["provider"] = "kokoro-onnx"
        return detail
    detail["espeak_available"] = bool(voice_tts._has_espeak_ng())
    if mode in {"hybrid", "local", "offline"} and detail["espeak_available"]:
        detail["ok"] = True
        detail["provider"] = "espeak-ng"
        return detail
    detail["edge_tts_available"] = bool(voice_tts.edge_tts_available())
    if mode in {"hybrid"} and detail["edge_tts_available"]:
        detail["ok"] = True
        detail["provider"] = "edge-tts"
        return detail
    if mode in {"hybrid", "local"}:
        detail["error"] = "no_tts_provider_available"
    return detail


async def _build_readiness_report_uncached() -> dict:
    brain, stt, tts = await asyncio.gather(
        _check_brain_ready(),
        _check_stt_ready(),
        _check_tts_ready(),
    )
    dependencies = {"brain": brain, "stt": stt, "tts": tts}
    ready = all(bool(dep.get("ok")) for dep in dependencies.values())
    # A dependency can be up (ok) yet degraded — e.g. Kokoro serving on CPU instead
    # of CUDA: replies still play but slower-than-realtime, so they come out choppy.
    # This is a quality regression, not an outage, so it must NOT flip `ready`/HTTP
    # 503 (that restarts zoe-data — the wrong service, and a busy box would just flap
    # since it's the sidecar that needs to re-grab CUDA). Instead we lift it to the
    # top-level `status`, which deploy/watchdog checks already gate on, so a silent
    # CPU fallback stops reading as fully healthy.
    degraded_reasons = {
        name: dep.get("degraded_reason") or True
        for name, dep in dependencies.items()
        if dep.get("degraded")
    }
    degraded = bool(degraded_reasons)
    # status is "ok" only when fully ready AND no dependency is degraded; unchanged
    # "degraded" for the not-ready case (was already that), now also for ready-but-degraded.
    status = "ok" if (ready and not degraded) else "degraded"
    report = {
        "status": status,
        "service": "zoe-data",
        "version": "1.0.0",
        "memory_capture": _memory_capture_health,
        "ready": ready,
        "dependencies": dependencies,
    }
    if degraded:
        report["degraded"] = True
        report["degraded_reasons"] = degraded_reasons
    return report


async def _build_readiness_report(*, use_cache: bool = True) -> dict:
    now = time.monotonic()
    cached_report = _READINESS_CACHE.get("report")
    if use_cache and isinstance(cached_report, dict) and now < float(_READINESS_CACHE.get("expires_at") or 0.0):
        return dict(cached_report)

    async with _READINESS_CACHE_LOCK:
        now = time.monotonic()
        cached_report = _READINESS_CACHE.get("report")
        if use_cache and isinstance(cached_report, dict) and now < float(_READINESS_CACHE.get("expires_at") or 0.0):
            return dict(cached_report)

        total_timeout_s = max(0.1, _env_float("ZOE_READINESS_TIMEOUT_S", 4.0))
        cache_ttl_s = max(0.0, _env_float("ZOE_READINESS_CACHE_TTL_S", 3.0))
        try:
            report = await asyncio.wait_for(_build_readiness_report_uncached(), timeout=total_timeout_s)
        except Exception as exc:
            report = {
                "status": "degraded",
                "service": "zoe-data",
                "version": "1.0.0",
                "memory_capture": _memory_capture_health,
                "ready": False,
                "dependencies": {"readiness": {"ok": False, "error": exc.__class__.__name__}},
            }
        _READINESS_CACHE["report"] = dict(report)
        _READINESS_CACHE["expires_at"] = time.monotonic() + cache_ttl_s
        return report


async def _wait_for_brain_startup() -> None:
    deadline = time.monotonic() + max(0.0, _env_float("ZOE_BRAIN_STARTUP_WAIT_S", 30.0))
    last: dict = {}
    while time.monotonic() < deadline:
        last = await _check_brain_ready(timeout_s=2.0)
        if last.get("ok"):
            logger.info("Brain readiness gate passed")
            return
        await asyncio.sleep(1.0)
    logger.warning("Brain readiness gate timed out; /readyz will report not ready: %s", last)


# These legacy logging filters are no longer needed with the new JSON middleware
# but kept for backward compatibility during transition



# Rotating cursor for the blocked-resume scan (see _bounded_blocked_resume_window).
_BLOCKED_RESUME_CURSOR: dict[str, int] = {"offset": 0}


def _bounded_blocked_resume_window(
    blocked: list[dict], offset: int, budget: int
) -> tuple[list[dict], int]:
    """Return a rotating slice of at most ``budget`` blocked issues, plus the next offset.

    Resuming a blocked chain requires an expensive (and memory-heavy) executor
    poll, so polling *every* blocked chain each 30s cycle can starve admission and
    dispatch — and on constrained hardware it can OOM. Bound the work per cycle to
    ``budget`` chains and rotate the starting offset so, across consecutive cycles,
    every blocked chain is still eventually polled (no chain is permanently
    skipped). Returns the whole list when ``budget`` covers it.
    """
    count = len(blocked)
    if count == 0 or budget <= 0:
        return [], 0
    if budget >= count:
        # Whole list covered this cycle; preserve the caller's place so a list
        # that briefly shrinks below budget and grows back keeps rotating.
        return list(blocked), offset % count
    start = offset % count
    window = [blocked[(start + step) % count] for step in range(budget)]
    return window, (start + budget) % count


def _multica_poll_interval_s(paused: bool, *, active_s: float, paused_s: float) -> float:
    """Cadence for the Multica poll loop.

    Throttle to ``paused_s`` while dispatch is paused — the per-issue chain
    reconcile (worktree + git ops) is expensive and pointless when nothing is
    being dispatched — and use the normal ``active_s`` otherwise.

    Floors the paused cadence at ``active_s`` so a misconfigured
    ``ZOE_MULTICA_PAUSED_POLL_S`` of 0 or a negative value can't turn the paused
    path into a tight spin loop that burns *more* CPU than the active cadence.
    """
    if not paused:
        return active_s
    return max(paused_s, active_s)


async def _poll_chain_guarded(ref: str, *, issue: dict | None, timeout: float) -> dict:
    """Poll a chain without letting one dead ref wedge the whole poll loop.

    ``poll_ref`` has no internal timeout, so a died executor reference can hang
    indefinitely and freeze the entire Multica poll loop (observed: a single
    stale ``in_progress`` chain stalled all admission/dispatch for days). Bound
    the call and, on timeout/error, return a safe not-found sentinel so callers
    treat the chain as inactive and simply skip it this cycle.
    """
    from executor_registry import poll_ref  # type: ignore[import]

    try:
        return await asyncio.wait_for(poll_ref(ref, issue=issue), timeout=timeout)
    except asyncio.TimeoutError:
        logger.warning("multica_poll: poll_ref timed out for %s after %ss", ref, timeout)
        return {"found": False, "status": "poll_timeout", "timed_out": True}
    except Exception as exc:
        logger.warning("multica_poll: poll_ref error for %s: %s", ref, exc)
        return {"found": False, "status": "poll_error", "error": str(exc)}


async def _resolve_chain_for_dispatch(chain, *, ref, issue, poll_guarded, poll_timeout):
    """Re-poll once with a longer timeout when the first poll failed.

    ``_poll_chain_guarded`` returns a timeout/error sentinel on a slow/failed
    poll. An *existing* multi-row chain's poll (worktree + git ops) is expensive
    and can time out under event-loop load, while a fresh todo's poll is cheap —
    so without a retry the sentinel made the backfill skip the chain forever
    (stranding it past implement). If the chain isn't a sentinel, return it
    unchanged (no extra poll).
    """
    from multica_poll_dispatch import chain_poll_failed  # type: ignore[import]

    if not chain_poll_failed(chain):
        return chain
    return await poll_guarded(ref, issue=issue, timeout=max(float(poll_timeout) * 2, 60.0))


async def _recover_stale_in_progress_issues(
    client,
    in_progress_issues: list[dict],
    *,
    hermes_id: str,
    poll_chain,
    now,
    max_age_hours: float,
) -> list[dict]:
    """Reset dead ``in_progress`` chains to ``blocked`` so a zombie can't hold the lane.

    The single-ticket lane is guarded by the presence of an ``in_progress``
    issue. If a chain dies mid-run, its ticket sits ``in_progress`` forever and
    the guard correctly — but unhelpfully — refuses to admit anything else. This
    mirrors the existing stale ``Autopilot:`` todo cleanup: detect Hermes-owned
    ``in_progress`` chains that are no longer active and untouched for
    ``max_age_hours``, reset them to ``blocked`` (operator-visible), and return
    the issues that are still legitimately in progress.
    """
    from multica_poll_dispatch import is_stale_in_progress  # type: ignore[import]

    live: list[dict] = []
    for issue in in_progress_issues or []:
        if str(issue.get("assignee_id") or "") != hermes_id:
            live.append(issue)
            continue
        if (issue.get("title") or "").lower().startswith("autopilot:"):
            live.append(issue)
            continue
        chain = await poll_chain(issue)
        if not is_stale_in_progress(issue, chain, now=now, max_age_hours=max_age_hours):
            live.append(issue)
            continue
        try:
            await client.record_progress(
                str(issue.get("id")),
                status="blocked",
                blocker=(
                    "stale in_progress reset: chain inactive and no metadata update "
                    f"for >= {max_age_hours}h; freed the single ticket lane"
                ),
            )
            logger.info(
                "multica_poll: recovered stale in_progress %s -> blocked",
                issue.get("identifier") or issue.get("id"),
            )
        except Exception as exc:
            logger.warning(
                "multica_poll: stale in_progress recovery failed for %s: %s",
                issue.get("id"),
                exc,
            )
            live.append(issue)
    return live


async def _record_running_multica_chain_progress(
    client,
    issue_id: str,
    chain: dict,
    *,
    issue: dict | None = None,
) -> bool:
    """Persist operator-visible PR/phase progress for a non-terminal chain."""
    pipeline = chain.get("pipeline") if isinstance(chain.get("pipeline"), dict) else {}
    phase = pipeline.get("phase") or None
    pr_url = chain.get("pr_url") or None
    if not phase and not pr_url:
        return False

    target_status = "in_review" if pr_url else None
    try:
        from multica_ticket_contract import parse_ticket_block

        current_issue = issue if isinstance(issue, dict) else {}
        if not current_issue.get("description"):
            current_issue = await client.get_issue(issue_id)
        metadata = parse_ticket_block(current_issue.get("description") or "")
        if (
            metadata.get("phase") == phase
            and metadata.get("pr_url") == pr_url
            and not metadata.get("blocked_reason")
            and (not target_status or current_issue.get("status") == target_status)
        ):
            return False
    except Exception:
        pass

    progress_kwargs = {
        key: value
        for key, value in {
            "phase": phase,
            "evidence": "Engineering PR opened; validation/review in progress" if pr_url else "Engineering run in progress",
            "pr_url": pr_url,
            "clear_blocker": True,
        }.items()
        if value is not None
    }
    if target_status:
        progress_kwargs["status"] = target_status

    await client.record_progress(issue_id, **progress_kwargs)
    return True


async def _record_completed_multica_chain(client, issue_id: str, chain: dict) -> None:
    """Persist operator-visible completion metadata for a finished Multica chain."""
    pipeline = chain.get("pipeline") or {}
    phase = pipeline.get("phase") or "closeout"
    progress_kwargs = {
        key: value
        for key, value in {
            "phase": phase,
            "evidence": "Engineering run done after retro" if phase == "retro" else "Engineering run done",
            "pr_url": chain.get("pr_url"),
            "clear_blocker": True,
            "status": "done",
        }.items()
        if value is not None
    }
    await client.record_progress(issue_id, **progress_kwargs)
    follow_up = pipeline.get("retro_followup") if isinstance(pipeline, dict) else None
    if phase != "retro" or not isinstance(follow_up, dict) or not follow_up.get("title"):
        return
    try:
        from multica_ticket_contract import describe_ticket

        parent = await client.get_issue(issue_id)
        parent_ident = parent.get("identifier") or issue_id
        description = describe_ticket(
            str(follow_up.get("description") or follow_up.get("title")),
            zoe_kind="harness_fix",
            evidence_profile="code",
            engineering_mode="interactive",
            acceptance_criteria=["Address the retro-identified harness improvement in a small, reviewable change."],
            evidence_expectations=["Focused tests or validators", "PR URL when code changes are made"],
            source="retro_followup",
            parent_issue_id=issue_id,
        )
        issue = await client.create_issue(
            title=str(follow_up.get("title"))[:140],
            description=description,
            priority="medium",
            status="backlog",
            assignee_id=parent.get("assignee_id"),
            assignee_type=parent.get("assignee_type") or "agent",
            project_id=parent.get("project_id"),
        )
        child_id = str(issue.get("id") or "")
        if child_id:
            await client.attach_label(child_id, "harness-fix")
            await client.append_issue_note(
                issue_id,
                f"Retro follow-up created: {issue.get('identifier') or child_id} from {parent_ident}",
            )
    except Exception as exc:
        logger.warning("multica_poll: retro follow-up creation failed for %s: %s", issue_id, exc)


def _tracked_multica_engineering_issues(*groups: list[dict]) -> list[dict]:
    """Return unique active/review issues whose engineering chain needs reconciliation."""
    tracked: list[dict] = []
    seen: set[str] = set()
    for group in groups:
        for issue in group or []:
            issue_id = str(issue.get("id") or "")
            if not issue_id or issue_id in seen:
                continue
            seen.add(issue_id)
            tracked.append(issue)
    return tracked


async def _read_multica_board_statuses(
    client, statuses: tuple[str, ...]
) -> tuple[dict[str, list[dict]], set[str]]:
    """Read Multica board statuses, tolerating partial failures.

    A total outage must still raise so the poll cycle is skipped as observable
    downtime. If at least one status read succeeds, keep that work moving and
    log the missing statuses rather than starving the whole cycle. The returned
    failed-status set keeps unknown board state distinct from a real empty lane.
    """
    results: dict[str, list[dict]] = {}
    failures: dict[str, Exception] = {}

    for status in statuses:
        try:
            results[status] = await client.list_issues(status=status, raise_on_error=True) or []
        except Exception as exc:
            failures[status] = exc
            results[status] = []

    if failures and len(failures) == len(statuses):
        first_status = next(iter(failures))
        logger.warning(
            "multica_poll: all Multica status reads failed (%s); skipping this cycle",
            ", ".join(statuses),
        )
        raise failures[first_status]

    if failures:
        logger.warning(
            "multica_poll: partial Multica status read failure for %s; "
            "continuing with successfully-read statuses %s",
            ", ".join(failures),
            ", ".join(status for status in statuses if status not in failures),
        )

    return results, set(failures)


def _blocked_multica_chain_reason(chain: dict) -> str:
    """Return the operator-visible reason for a blocked engineering chain."""
    pipeline = chain.get("pipeline") or {}
    phase = pipeline.get("phase") or "implement"
    blocker = (
        chain.get("blocker")
        or pipeline.get("block_reason")
        or pipeline.get("block_classification")
        or f"pipeline blocked at {phase}"
    )
    if pipeline.get("terminal_block") and "terminal" not in str(blocker).lower():
        blocker = f"terminal block: {blocker}"
    return str(blocker)


def _blocker_followup_marker(phase: str, blocker: str) -> str | None:
    """Return the harness blocker class that should create a follow-up ticket."""
    if phase != "implement":
        return None
    upper = blocker.upper()
    for marker in ("IMPLEMENT_BUDGET", "ITERATION_BUDGET", "PROTOCOL_VIOLATION"):
        if marker in upper:
            return marker
    return None


async def _ensure_blocker_followup_ticket(client, issue_id: str, chain: dict, blocker: str) -> dict:
    """Create or reuse one harness-fix follow-up for implement budget/protocol blocks."""
    pipeline = chain.get("pipeline") or {}
    phase = str(pipeline.get("phase") or "implement")
    marker = _blocker_followup_marker(phase, blocker)
    if marker is None:
        return {}

    try:
        from multica_ticket_contract import (
            append_child_id,
            describe_ticket,
            parse_ticket_block,
        )

        parent = await client.get_issue(issue_id)
        if not parent.get("id"):
            return {}
        parent_metadata = parse_ticket_block(parent.get("description") or "")
        if parent_metadata.get("source") == "engineering_blocker_followup":
            logger.info(
                "multica_poll: not creating recursive harness follow-up for %s (%s)",
                parent.get("identifier") or issue_id,
                marker,
            )
            return {}
        parent_ident = parent.get("identifier") or issue_id
        # Dedup across the whole ticket system, not just active columns. A done/no-op
        # follow-up for the same parent and blocker is still the canonical audit trail;
        # creating another ticket hides the recurring harness failure in backlog noise.
        seen_candidates: set[str] = set()

        def matching_followup(candidate: dict) -> dict | None:
            candidate_id = str(candidate.get("id") or "")
            if candidate_id and candidate_id in seen_candidates:
                return None
            if candidate_id:
                seen_candidates.add(candidate_id)
            metadata = parse_ticket_block(candidate.get("description") or "")
            if (
                metadata.get("source") == "engineering_blocker_followup"
                and str(metadata.get("parent_issue_id") or "") == str(issue_id)
                and metadata.get("source_blocker") == marker
            ):
                return candidate
            return None

        for status in ("backlog", "todo", "in_progress", "blocked", "in_review", "done", "canceled"):
            for candidate in await client.list_issues(status=status, limit=1000) or []:
                if match := matching_followup(candidate):
                    return match
        for candidate in await client.list_issues(limit=5000) or []:
            if match := matching_followup(candidate):
                return match

        description = describe_ticket(
            (
                f"Follow up from {parent_ident}: the engineering driver blocked in "
                f"implement with {marker}. Fix the harness so this class of block "
                "is surfaced or prevented without manual product-ticket rescue."
            ),
            zoe_kind="harness_fix",
            evidence_profile="code",
            engineering_mode="interactive",
            acceptance_criteria=[
                f"{marker} implement blockers create or surface exactly one harness follow-up",
                "Blocked source ticket remains dispatch_approved=false",
                "Repeated poll cycles do not create duplicate follow-ups",
                "Focused tests cover the blocker path",
                "PR URL is recorded for code changes",
            ],
            evidence_expectations=["Focused tests", "Greptile 5/5", "PR URL"],
            source="engineering_blocker_followup",
            parent_issue_id=issue_id,
        )
        metadata = parse_ticket_block(description)
        metadata["source_blocker"] = marker
        metadata["source_block_reason"] = blocker
        from multica_ticket_contract import write_ticket_block

        issue = await client.create_issue(
            title=f"Harness: follow up {marker} for {parent_ident}"[:140],
            description=write_ticket_block(description, metadata),
            priority="medium",
            status="backlog",
            assignee_id=parent.get("assignee_id"),
            assignee_type=parent.get("assignee_type") or "agent",
            project_id=parent.get("project_id"),
        )
        child_id = str(issue.get("id") or "")
        if child_id:
            await client.attach_label(child_id, "harness-fix")
            await client.attach_label(child_id, marker.lower().replace("_", "-"))
            await client.update_issue(
                issue_id,
                description=append_child_id(parent.get("description") or "", child_id),
            )
            await client.append_issue_note(
                issue_id,
                f"Harness follow-up created for {marker}: {issue.get('identifier') or child_id}",
            )
        return issue
    except Exception as exc:
        logger.warning("multica_poll: blocker follow-up creation failed for %s: %s", issue_id, exc)
        return {}


async def _record_blocked_multica_chain(client, issue_id: str, chain: dict) -> str:
    """Persist operator-visible block metadata for a stopped Multica chain."""
    pipeline = chain.get("pipeline") or {}
    phase = pipeline.get("phase") or "implement"
    blocker = _blocked_multica_chain_reason(chain)
    await client.record_progress(
        issue_id,
        phase=phase,
        evidence="Engineering run blocked",
        pr_url=chain.get("pr_url"),
        blocker=blocker,
        status="blocked",
        dispatch_approved=False,
    )
    await _ensure_blocker_followup_ticket(client, issue_id, chain, blocker)
    return blocker


async def _reconcile_diverged_board_status(client, issue_id: str, issue: dict, chain: dict) -> bool:
    """Converge a board status that diverged from a regressed (partial) chain.

    When a found, non-terminal ``partial`` chain (ready next phase) is paired with
    a board status that is not ``in_progress`` — e.g. stuck ``in_review`` after a
    verify bounce — the issue is neither a dispatch candidate nor advanced by the
    done/blocked/running reconcile branches, so it freezes the single lane (and
    blocks auto-admission while it sits ``in_review``). Move the board back to
    ``in_progress`` so the next cycle's backfill re-dispatches the ready phase.

    Returns True iff a reconcile write was made. No-ops (returns False) when the
    chain is not a reconcilable partial or the board is already ``in_progress``.
    """
    from multica_poll_dispatch import chain_needs_reconcile  # type: ignore[import]

    if not (chain.get("found") and chain_needs_reconcile(chain)):
        return False
    if str(issue.get("status") or "") == "in_progress":
        return False
    await client.record_progress(
        str(issue_id),
        evidence="Engineering run reconciled (board/journal divergence)",
        status="in_progress",
        clear_blocker=True,
    )
    return True


async def _run_memory_capture_startup_probe() -> None:
    """Validate memory capture plumbing at startup.

    This is a non-invasive probe: it verifies extractor import/patterns and
    MemoryService read paths without writing synthetic rows.

    Timeouts are generous (10 s) because at cold boot the vector index and
    ONNX runtime are both initialising concurrently with DB setup.  A
    background retry fires 45 s later so transient cold-boot failures
    self-heal without requiring a service restart.
    """
    global _memory_capture_health
    try:
        from memory_extractor import extract_candidates
        from memory_service import get_memory_service

        probe = "remember that i met startup-probe person"
        candidates = extract_candidates(probe, "")
        if not candidates:
            raise RuntimeError("memory_extractor produced no candidates for probe")

        svc = get_memory_service()
        await asyncio.wait_for(svc.load_for_prompt("family-admin", limit=1), timeout=10.0)
        await asyncio.wait_for(
            svc.search("startup probe", user_id="family-admin", limit=1, timeout_s=3.0),
            timeout=10.0,
        )
        _memory_capture_health = {"status": "ok", "detail": "extractor+service ready"}
        logger.info("Memory capture startup probe: OK")
    except Exception as exc:
        detail = str(exc) or type(exc).__name__
        _memory_capture_health = {"status": "degraded", "detail": detail[:240]}
        logger.error("Memory capture startup probe FAILED: %s", detail)
        if os.environ.get("ZOE_MEMORY_STARTUP_STRICT", "false").strip().lower() == "true":
            raise


async def _memory_capture_retry_task() -> None:
    """Background retry: re-run the startup probe once, 45 s after boot.

    Clears false-degraded status caused by cold-boot timeouts without
    requiring a full service restart.
    """
    await asyncio.sleep(45)
    if _memory_capture_health.get("status") == "ok":
        return
    logger.info("Memory capture retry probe starting...")
    await _run_memory_capture_startup_probe()


async def _probe_runtimes() -> None:
    """Probe agent runtime ports at startup; update _RUNTIME_HEALTH."""
    import asyncio as _asyncio

    async def _check_port(port: int, timeout: float = 2.0) -> bool:
        try:
            reader, writer = await _asyncio.wait_for(
                _asyncio.open_connection("127.0.0.1", port), timeout=timeout
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    _RUNTIME_HEALTH["local_llm"] = await _check_port(11434)
    _RUNTIME_HEALTH["hermes"] = await _check_port(8642)
    _RUNTIME_HEALTH["openclaw"] = await _check_port(18789)
    global _RUNTIME_LAST_PROBED
    from datetime import datetime, timezone
    _RUNTIME_LAST_PROBED = datetime.now(timezone.utc).isoformat()
    logger.info(
        "Runtime health probe: local_llm=%s hermes=%s openclaw=%s",
        _RUNTIME_HEALTH["local_llm"],
        _RUNTIME_HEALTH["hermes"],
        _RUNTIME_HEALTH["openclaw"],
    )


async def _runtime_health_refresh_loop() -> None:
    """Re-probe runtime health every 5 minutes."""
    while True:
        await asyncio.sleep(300)
        await _probe_runtimes()


_MEMPALACE_MIG_FLAG_KEY = "mempalace_wing_migration_done"


async def _run_mempalace_migration_gate(get_db_ctx=None, migrate=None, flag_path=None) -> bool:
    """Run the one-time MemPalace wing migration, gated by a DB flag.

    The expensive ChromaDB re-tag only runs on first startup. ``migrate``
    swallows its own errors and only writes its filesystem flag on a *truly*
    successful run, so the DB gate flag is persisted only when that filesystem
    flag exists afterwards — otherwise a silently-failed migration would be
    permanently skipped on every future restart. Returns True when the DB gate
    flag is (or already was) set.
    """
    if get_db_ctx is None:
        from db_pool import get_db_ctx as get_db_ctx  # noqa: PLW0127
    async with get_db_ctx() as db:
        rows = await db.execute_fetchall(
            "SELECT value FROM system_preferences WHERE key = $1",
            (_MEMPALACE_MIG_FLAG_KEY,),
        )
    if rows:
        logger.debug("MemPalace migration already done (DB flag present); skipping")
        return True

    if migrate is None:
        from zoe_agent import migrate_mempalace_legacy_records as migrate
    if flag_path is None:
        from zoe_agent import _MIGRATION_DONE_FLAG as flag_path
    await asyncio.get_event_loop().run_in_executor(None, migrate)

    if not os.path.exists(flag_path):
        logger.warning(
            "MemPalace migration did not complete (filesystem flag %s absent);"
            " deferring DB gate flag so it retries next startup",
            flag_path,
        )
        return False

    async with get_db_ctx() as db:
        await db.execute(
            "INSERT INTO system_preferences (key, value, updated_by, updated_at)"
            " VALUES ($1, $2, $3, NOW()::text)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value,"
            " updated_at = NOW()::text",
            (_MEMPALACE_MIG_FLAG_KEY, "true", "startup"),
        )
    logger.info("MemPalace migration complete; DB flag set (%s=true)", _MEMPALACE_MIG_FLAG_KEY)
    return True


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _openclaw_bg_task, _digest_bg_task, _zoe_update_bg_task, _consolidation_bg_task, _runtime_health_task
    try:
        from runtime_env import bootstrap_runtime_env  # type: ignore[import]
        from hermes_http import hermes_api_key  # type: ignore[import]

        bootstrap_runtime_env()
        if not hermes_api_key():
            logger.error(
                "HERMES_API_KEY/API_SERVER_KEY missing after bootstrap — "
                "engineering/Hermes background tasks may fail with 401"
            )
    except Exception as _env_exc:
        logger.warning("runtime_env bootstrap (non-fatal): %s", _env_exc)

    logger.info("Initializing zoe-data database...")
    await init_db()
    logger.info("Database initialized. zoe-data is ready.")
    asyncio.create_task(_wait_for_brain_startup(), name="brain_startup_readiness_probe")
    # One-time MemPalace migration: re-tag legacy records from wing="zoe" to wing="family-admin".
    # Gated behind a DB flag so the expensive ChromaDB scan only runs on first startup.
    try:
        await _run_mempalace_migration_gate()
    except Exception as _mig_exc:
        logger.warning("MemPalace migration (non-fatal): %s", _mig_exc)
    # Load device tokens into memory so voice daemons can authenticate.
    try:
        from routers.panel_auth import load_device_tokens
        from db_pool import get_db_ctx as _get_pg_db
        async with _get_pg_db() as _db_conn:
            await load_device_tokens(_db_conn)
    except Exception as _exc:
        logger.warning("Could not pre-load device tokens: %s", _exc)
    # Warm the memory-recall path FIRST — before the capture startup probe below,
    # which itself runs a real MemoryService.search() and would otherwise be the
    # cold first search (ticking real rows' access metadata while cold — Greptile).
    # Background: search() fails OPEN to [] on its 2s timeout, and the process's
    # FIRST search cold-loads the embedder (~1.7s idle, worse under load) — so the
    # first recall after every restart (incl. every auto-deploy) silently returned
    # an empty packet and Zoe denied knowing stored facts ("I don't have any
    # information about Caitlin", 2026-07-12). Warm by embedding ONLY: a raw
    # collection query scoped to a user that owns no rows loads the embedder with
    # zero rows returned and zero access-count ticks. AWAITED (bounded) rather than
    # backgrounded so traffic served right after startup cannot race a still-cold
    # embedder; fail-open on timeout/error.
    try:
        from memory_service import get_memory_service
        import time as _t

        _svc_warm = get_memory_service()

        def _embed_only_warmup() -> None:
            _svc_warm._collection().query(
                query_texts=["startup warmup"],
                n_results=1,
                where={"user_id": {"$eq": "_warmup_no_such_user"}},
            )

        _t0 = _t.monotonic()
        await asyncio.wait_for(_svc_warm._run_sync(_embed_only_warmup), timeout=20.0)
        logger.info("memory embedder warmed in %.1fs", _t.monotonic() - _t0)
    except Exception as _exc:
        logger.warning("memory embedder warmup failed (non-fatal): %s", _exc)
    await _run_memory_capture_startup_probe()
    asyncio.create_task(_memory_capture_retry_task(), name="memory_capture_retry")
    _openclaw_bg_task = start_openclaw_background_tasks()
    _digest_bg_task = start_memory_digest_background()
    _consolidation_bg_task = start_memory_consolidation_background()
    # Idle-triggered "live → idle → store" consolidation (self-gates on
    # ZOE_IDLE_CONSOLIDATION_ENABLED; off by default until lab-proven).
    try:
        from memory_idle_consolidation import start_idle_consolidation_loop
        asyncio.create_task(start_idle_consolidation_loop(), name="memory_idle_consolidation")
    except Exception as _exc:
        logger.warning("idle consolidation loop not started: %s", _exc)
    # Segment-stitch vocabulary prewarm (flag-gated, default OFF): synth the finite
    # weather/time phrase vocabulary once so stitched replies are cache-hit-instant.
    # Paced + best-effort; a no-op when the flag is off.
    if os.environ.get("ZOE_VOICE_STITCH_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on"):
        try:
            from voice_stitch import prewarm_vocabulary
            from tts_waterfall import _synthesize_kokoro_sidecar
            asyncio.create_task(
                prewarm_vocabulary(_synthesize_kokoro_sidecar), name="voice_stitch_prewarm"
            )
        except Exception as _exc:
            logger.warning("voice_stitch prewarm not started: %s", _exc)
    _zoe_update_bg_task = start_zoe_update_background_tasks()

    try:
        from routers.voice_tts import warm_moonshine
        asyncio.create_task(warm_moonshine(), name="moonshine_warmup")
        logger.info("Voice STT warmup scheduled (Moonshine — the only STT engine)")
        try:
            import semantic_router as _sr
            if _sr.is_enabled():
                asyncio.create_task(asyncio.to_thread(_sr.warm), name="semantic_router_warmup")
                logger.info("Semantic router (Tier-1) warmup scheduled — mode=%s", _sr.mode())
        except Exception as _sr_exc:
            logger.warning("Semantic router warmup scheduling failed (non-fatal): %s", _sr_exc)
    except Exception as _voice_warmup_exc:
        logger.warning("Voice STT worker warmup scheduling failed (non-fatal): %s", _voice_warmup_exc)

    # Runtime health probe — run once immediately, then refresh every 5 min
    await _probe_runtimes()
    _runtime_health_task = asyncio.create_task(_runtime_health_refresh_loop(), name="runtime_health_refresh")

    # Background task watchdog — detects tasks stuck in 'running' state
    try:
        from background_runner import _watchdog_loop
        asyncio.create_task(_watchdog_loop(), name="task_watchdog")
        logger.info("Background task watchdog started")
    except Exception as _wd_exc:
        logger.warning("Task watchdog not started (non-fatal): %s", _wd_exc)
    # Zoe Agent: warm Gemma's KV cache in background so first real query is fast
    # Check env directly to avoid circular import from routers.chat
    _pi_mode = os.environ.get("HERMES_FAST_PATH", "true").lower() != "true"
    _jetson_mode = os.environ.get("JETSON_AGENT_MODE", "false").lower() == "true"
    if _pi_mode or _jetson_mode:
        try:
            from zoe_agent import warmup_kv_cache
            asyncio.create_task(warmup_kv_cache(), name="gemma_kv_warmup")
            tier = "Jetson" if _jetson_mode else "Pi"
            logger.info("%s Agent: Gemma KV cache warmup scheduled (fires in 8s)", tier)
        except Exception as _wup_exc:
            logger.warning("Agent KV warmup scheduling failed (non-fatal): %s", _wup_exc)

    # Proactive engine: APScheduler (Tier 1) + slow-loop (Tier 2).
    try:
        # Schema guard: the claim/generation logic depends on migration 0012.
        # Fail fast & LOUD (and don't start the engine in a broken state) rather
        # than letting every reminder silently error on the missing columns.
        from proactive.engine import verify_proactive_schema
        _missing_cols = await verify_proactive_schema()
        if _missing_cols:
            raise RuntimeError(
                "proactive DB schema out of date — missing columns: "
                f"{_missing_cols}. Run: alembic upgrade head (migration 0012). "
                "Proactive engine NOT started."
            )

        from proactive.engine import start_proactive_engine, register_trigger
        from proactive.triggers.reminder_scan import ReminderScanTrigger
        from proactive.triggers.morning_checkin import MorningCheckInTrigger
        from proactive.triggers.evening_windown import EveningWindDownTrigger
        from proactive.triggers.openclaw_trigger import OpenClawTrigger
        from proactive.triggers.people_health import PeopleHealthTrigger
        from proactive.triggers.people_birthday import PeopleBirthdayTrigger
        from proactive.triggers.emotional_followup import EmotionalFollowUpTrigger
        register_trigger(ReminderScanTrigger())
        register_trigger(MorningCheckInTrigger())
        register_trigger(EveningWindDownTrigger())
        register_trigger(OpenClawTrigger())
        register_trigger(PeopleHealthTrigger())
        register_trigger(PeopleBirthdayTrigger())
        # Samantha proactivity: gentle follow-up on captured worries. Registered
        # always; gated OFF by ZOE_EMOTIONAL_FOLLOWUP_ENABLED (check() no-ops when off).
        register_trigger(EmotionalFollowUpTrigger())
        # EvolutionWeeklyDigestTrigger registered after evolve-weekly-digest is built
        try:
            from proactive.triggers.evolution_weekly_digest import EvolutionWeeklyDigestTrigger
            register_trigger(EvolutionWeeklyDigestTrigger())
            logger.info("Proactive engine started (+ EvolutionWeeklyDigestTrigger registered)")
        except ImportError:
            logger.info(
                "Proactive engine started (ReminderScanTrigger, MorningCheckInTrigger,"
                " EveningWindDownTrigger, OpenClawTrigger registered)"
            )
        start_proactive_engine()
        # Reconcile after the scheduler is live: re-register any unfired reminder
        # whose one-shot job was dropped while the service was down (>misfire
        # grace) so a missed reminder still fires after restart.
        try:
            from proactive.engine import reconcile_scheduled_jobs
            _recovered = await reconcile_scheduled_jobs()
            if _recovered:
                logger.info("Proactive reconcile recovered %d missed reminder(s)", _recovered)
        except Exception as _rec_exc:
            logger.warning("Proactive reconcile skipped (non-fatal): %s", _rec_exc)
    except Exception as _pe_exc:
        # Loud + explicit: reminders/proactive nudges will NOT fire until this is
        # resolved (commonly: run alembic migrations). Not silently swallowed.
        logger.error("Proactive engine NOT started — reminders will not fire: %s", _pe_exc)

    # Multica autopilot schedule sync — register cron jobs from Multica into APScheduler.
    # Must run after start_proactive_engine() so the scheduler is already running.
    if os.environ.get("ZOE_MULTICA", "false").lower() == "true":
        try:
            from multica_autopilot_sync import sync_autopilots_from_multica
            from proactive.scheduler import get_scheduler as _get_aps
            _n = await sync_autopilots_from_multica(_get_aps())
            logger.info("Multica autopilot sync: %d job(s) registered", _n)
        except Exception as _mas_exc:
            logger.warning("Multica autopilot sync skipped (non-fatal): %s", _mas_exc)

    # YouTube Music cookie auto-refresh — the anti-expiry path. Opens the
    # persistent profile HEADLESS on a cadence, re-harvests the login cookie, and
    # re-saves it to MA so the connection never silently expires. Must run after
    # start_proactive_engine() so the APScheduler is already live. Gated OFF by
    # default (ZOE_YTMUSIC_REFRESH_ENABLED) — additive, operator-enabled.
    if os.environ.get("ZOE_YTMUSIC_REFRESH_ENABLED", "false").lower() == "true":
        try:
            import ytmusic_signin  # noqa: F401 — ensure importable before scheduling
            from proactive.scheduler import get_scheduler as _get_aps
            _ytm_hours = float(os.environ.get("ZOE_YTMUSIC_REFRESH_HOURS", "12"))
            _get_aps().add_job(
                ytmusic_signin.refresh_now,
                trigger="interval",
                hours=_ytm_hours,
                id="ytmusic_cookie_refresh",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
            logger.info("YouTube Music cookie auto-refresh scheduled (every %sh)", _ytm_hours)
        except Exception as _ytm_exc:
            logger.warning("YouTube Music auto-refresh not scheduled (non-fatal): %s", _ytm_exc)

    # Listening-journal observed-events poll — back-fills plays Zoe didn't
    # initiate (radio-mode auto-continuation) from MA's recently-played into
    # music_play_history (music_history.observe_once: cheap, idempotent,
    # quiet when MA is down). Default ON; disable with ZOE_MUSIC_HISTORY=off.
    if os.environ.get("ZOE_MUSIC_HISTORY", "on").lower() in ("on", "true", "1"):
        try:
            import music_history as _music_history
            from proactive.scheduler import get_scheduler as _get_aps
            _mh_interval = float(os.environ.get("ZOE_MUSIC_HISTORY_INTERVAL_S", "300"))
            _get_aps().add_job(
                _music_history.observe_once,
                trigger="interval",
                seconds=_mh_interval,
                id="music_history_observe",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
            logger.info("Music listening-journal observer scheduled (every %ss)", _mh_interval)
        except Exception as _mh_exc:
            logger.warning("Music listening-journal observer not scheduled (non-fatal): %s", _mh_exc)

    # Weekly music discovery — ephemeral digarr batch refreshing the
    # "Zoe Discovery" MA playlist (scripts/maintenance/music_discovery_batch.py;
    # the script owns its own memory + brain-idle gates and container cleanup).
    # DISABLED by default (ZOE_MUSIC_DISCOVERY=off) — the operator flips it
    # only after verifying a manual run. Cadence via ZOE_MUSIC_DISCOVERY_DOW
    # (default sun) + ZOE_MUSIC_DISCOVERY_HOUR (default 3, a quiet window).
    if os.environ.get("ZOE_MUSIC_DISCOVERY", "off").lower() in ("on", "true", "1"):
        try:
            from proactive.scheduler import get_scheduler as _get_aps

            async def _run_music_discovery_batch() -> None:
                import asyncio as _aio
                import sys as _sys
                _script = os.path.normpath(os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "scripts", "maintenance", "music_discovery_batch.py"))
                proc = await _aio.create_subprocess_exec(
                    _sys.executable, _script,
                    stdout=_aio.subprocess.PIPE, stderr=_aio.subprocess.STDOUT)
                try:
                    out, _ = await _aio.wait_for(proc.communicate(), timeout=2400)
                except _aio.TimeoutError:
                    # SIGTERM first so the script's `finally` can stop+remove
                    # the digarr container; SIGKILL only if it hangs. Then
                    # force-remove the container regardless — a leaked 768MB
                    # container on this memory-tight box is an incident.
                    proc.terminate()
                    try:
                        await _aio.wait_for(proc.communicate(), timeout=60)
                        logger.error("music discovery batch timed out (terminated; script cleanup ran)")
                    except _aio.TimeoutError:
                        proc.kill()
                        logger.error("music discovery batch timed out (killed)")
                    _rm = await _aio.create_subprocess_exec(
                        "docker", "rm", "-f", "zoe-digarr-batch",
                        stdout=_aio.subprocess.DEVNULL,
                        stderr=_aio.subprocess.DEVNULL)
                    await _rm.wait()
                    return
                tail = (out or b"").decode(errors="replace")[-2000:]
                if proc.returncode == 0:
                    logger.info("music discovery batch ok:\n%s", tail)
                else:
                    logger.error("music discovery batch rc=%s:\n%s", proc.returncode, tail)

            _get_aps().add_job(
                _run_music_discovery_batch,
                trigger="cron",
                day_of_week=os.environ.get("ZOE_MUSIC_DISCOVERY_DOW", "sun"),
                hour=int(os.environ.get("ZOE_MUSIC_DISCOVERY_HOUR", "3")),
                id="music_discovery_weekly",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
            logger.info("Weekly music discovery scheduled (%s @ %sh)",
                        os.environ.get("ZOE_MUSIC_DISCOVERY_DOW", "sun"),
                        os.environ.get("ZOE_MUSIC_DISCOVERY_HOUR", "3"))
        except Exception as _md_exc:
            logger.warning("Music discovery not scheduled (non-fatal): %s", _md_exc)

    # Weekly router self-training — retrain the two-stage router's stage-2
    # decoder on mined real-traffic mistakes and PROMOTE ONLY IF PROVABLY BETTER
    # (scripts/maintenance/router_selftrain.py owns the ratchet: no accuracy
    # regression, zero chat-FP, p50 under budget, voice replay gate passed —
    # otherwise the incumbent keeps serving). The script's only production
    # mutation is swapping the sidecar model file + restarting that one unit,
    # and it auto-rolls-back to last-known-good on any post-deploy failure.
    # DISABLED by default (ZOE_ROUTER_SELFTRAIN=off) — the operator flips it on
    # only after verifying a manual --dry-run. Cadence via
    # ZOE_ROUTER_SELFTRAIN_DOW (default sat) + _HOUR (default 1, a quiet window
    # that does not collide with the sun@3 music batch: training may stop the
    # brain for hours). See docs/knowledge/router-selftrain-loop.md.
    if os.environ.get("ZOE_ROUTER_SELFTRAIN", "off").lower() in ("on", "true", "1"):
        try:
            from proactive.scheduler import get_scheduler as _get_aps

            async def _run_router_selftrain() -> None:
                import subprocess as _sp
                import sys as _sys

                from async_subprocess import run_to_completion as _run_off_loop

                _script = os.path.normpath(os.path.join(
                    os.path.dirname(os.path.abspath(__file__)),
                    "..", "..", "scripts", "maintenance", "router_selftrain.py"))
                _timeout = int(os.environ.get(
                    "ZOE_ROUTER_SELFTRAIN_TIMEOUT_S", "28800"))  # 8h: CPU train is slow
                _brain = os.environ.get("ZOE_BRAIN_UNIT", "llama-server.service")
                _env = dict(os.environ)
                # no login session in a scheduled job — point systemctl at the user bus
                _env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")

                # NOTE: every spawn here goes through async_subprocess.run_to_completion
                # (fork+exec inside a thread pool). asyncio.create_subprocess_exec forks
                # ON the loop thread, which in this large multi-threaded process can
                # deadlock pre-exec and freeze the whole API — that is the 2026-06-29
                # outage. See services/zoe-data/AGENTS.md.
                try:
                    proc = await _run_off_loop(
                        [_sys.executable, _script], env=_env, timeout=_timeout)
                except _sp.TimeoutExpired:
                    # The helper KILLs the child on timeout (SIGKILL — no in-process
                    # handler survives it). So a timeout can land (a) inside a training
                    # window with llama-server stopped, or (b) mid-deploy, after the
                    # served GGUF was swapped but before the new model passed its live
                    # checks — leaving the sidecar on an UNVERIFIED model.
                    # `--recover` is the idempotent cleanup for both: it ensures the
                    # brain is up and, if the on-disk deploy marker says a swap was in
                    # flight, restores the last-known-good GGUF and restarts onto it.
                    # Safe to run when nothing is broken.
                    logger.error("router self-train timed out after %ss (child killed) "
                                 "— running --recover", _timeout)
                    try:
                        _rec = await _run_off_loop(
                            [_sys.executable, _script, "--recover"],
                            env=_env, timeout=900)
                        _rtail = (_rec.stdout or b"").decode(errors="replace")[-1500:]
                        if _rec.returncode == 0:
                            logger.info("router self-train recovery ok:\n%s", _rtail)
                        else:
                            logger.error(
                                "router self-train RECOVERY FAILED (rc=%s) — OPERATOR "
                                "ACTION REQUIRED (brain may be down and/or the sidecar "
                                "may be serving an unverified model):\n%s",
                                _rec.returncode, _rtail)
                    except Exception as _rexc:
                        logger.error(
                            "router self-train RECOVERY FAILED (%s) — OPERATOR ACTION "
                            "REQUIRED: brain (%s) may be down and the sidecar may be "
                            "serving an unverified model", _rexc, _brain)
                    return

                tail = (proc.stdout or b"").decode(errors="replace")[-3000:]
                if proc.returncode == 0:
                    logger.info("router self-train ok:\n%s", tail)
                else:
                    # rc!=0 includes an auto-rollback — loud on purpose.
                    logger.error("router self-train rc=%s:\n%s\n%s", proc.returncode, tail,
                                 (proc.stderr or b"").decode(errors="replace")[-1000:])

            _get_aps().add_job(
                _run_router_selftrain,
                trigger="cron",
                day_of_week=os.environ.get("ZOE_ROUTER_SELFTRAIN_DOW", "sat"),
                hour=int(os.environ.get("ZOE_ROUTER_SELFTRAIN_HOUR", "1")),
                id="router_selftrain_weekly",
                replace_existing=True,
                coalesce=True,
                max_instances=1,
            )
            logger.info("Weekly router self-train scheduled (%s @ %sh)",
                        os.environ.get("ZOE_ROUTER_SELFTRAIN_DOW", "sat"),
                        os.environ.get("ZOE_ROUTER_SELFTRAIN_HOUR", "1"))
        except Exception as _rst_exc:
            logger.warning("Router self-train not scheduled (non-fatal): %s", _rst_exc)

    # Skills filesystem watcher (live cache invalidation for peer agent cards)
    try:
        from skills_watcher import start_skills_watcher  # type: ignore[import]
        _skills_observer = start_skills_watcher()
        logger.info("Skills watcher started")
    except Exception as _sw_exc:
        _skills_observer = None
        logger.warning("Skills watcher not started (non-fatal): %s", _sw_exc)

    # Multica board polling loop (30s interval, no-op if ZOE_MULTICA=false)
    async def _multica_poll_loop():
        import time as _t
        # Observability: the systemd unit doesn't capture app stdout (journalctl
        # shows nothing for zoe-data), so persist poll-loop diagnostics to a
        # dedicated rotating file. Isolated to this module logger — does not touch
        # uvicorn's logging config. Without this, dispatch stalls are invisible.
        try:
            from logging.handlers import RotatingFileHandler

            if not any(getattr(_h, "_zoe_poll_log", False) for _h in logger.handlers):
                _poll_log_path = os.path.expanduser("~/.zoe/zoe-data-poll.log")
                os.makedirs(os.path.dirname(_poll_log_path), exist_ok=True)
                _fh = RotatingFileHandler(_poll_log_path, maxBytes=2_000_000, backupCount=3)
                _fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
                _fh.setLevel(logging.INFO)  # handler-scoped; do NOT mutate the shared logger level
                _fh._zoe_poll_log = True  # type: ignore[attr-defined]
                logger.addHandler(_fh)
                logger.info("multica_poll: diagnostics logging to %s", _poll_log_path)
        except Exception as _log_exc:  # pragma: no cover - logging setup must never break the loop
            logger.warning("multica_poll: could not attach poll-loop file log: %s", _log_exc)
        _last_worktree_prune = 0.0
        _prune_interval_s = env_float("ZOE_WORKTREE_PRUNE_INTERVAL_S", 86400.0)
        # Cadence when dispatch is paused. The per-issue chain reconcile below
        # (poll_ref → worktree+git ops) costs ~30s CPU and a multi-GB transient
        # allocation each pass; running it every 30s while nothing is being
        # dispatched is pure waste. Poll every 30s when active, every
        # ZOE_MULTICA_PAUSED_POLL_S (default 300s) when paused.
        _paused_poll_s = env_float("ZOE_MULTICA_PAUSED_POLL_S", 300.0)
        _ACTIVE_POLL_S = 30.0
        _pause_check_warned = False
        # Bind the typed outage exception once so the loop-level handler can tell a
        # Multica OUTAGE apart from a genuinely empty board. Defensive fallback to an
        # empty tuple (matches nothing) keeps the handler safe even if the import
        # fails — never let this kill the poll task.
        try:
            from multica_client import MulticaUnavailableError
        except Exception:  # pragma: no cover - import guard
            MulticaUnavailableError = ()  # type: ignore[assignment,misc]
        while True:
            try:
                # Re-checked inside the cycle, so a resume still takes effect
                # promptly on the next pass.
                try:
                    from multica_dispatch_control import dispatch_is_paused as _dispatch_is_paused
                    _poll_interval = _multica_poll_interval_s(
                        _dispatch_is_paused(), active_s=_ACTIVE_POLL_S, paused_s=_paused_poll_s
                    )
                    _pause_check_warned = False
                except Exception as _pause_exc:
                    # A pause-check failure must not silently disable the throttle
                    # forever with no trace. Warn once (then debug), and fall back
                    # to the active cadence until it recovers.
                    if not _pause_check_warned:
                        logger.warning(
                            "multica_poll: pause check failed (%s); using active poll "
                            "cadence until it recovers", _pause_exc,
                        )
                        _pause_check_warned = True
                    else:
                        logger.debug("multica_poll: pause check still failing: %s", _pause_exc)
                    _poll_interval = _ACTIVE_POLL_S
                await asyncio.sleep(_poll_interval)
                from multica_client import MULClient  # type: ignore[import]
                client = MULClient()
                if not client.is_configured():
                    continue
                try:
                    from multica_autopilot_sync import close_stale_autopilot_wrappers

                    _n_stale = await close_stale_autopilot_wrappers()
                    if _n_stale:
                        logger.info("multica_poll: close_stale_autopilot_wrappers closed %d", _n_stale)
                except Exception as _stale_exc:
                    logger.debug("multica_poll: stale wrapper cleanup failed: %s", _stale_exc)
                # Daily safety-net: reclaim merged/squash-merged task worktrees that
                # crashed or lost their terminal handoff and never self-cleaned.
                if _t.time() - _last_worktree_prune >= _prune_interval_s:
                    _last_worktree_prune = _t.time()
                    try:
                        from worktree_bootstrap import prune_merged_worktrees

                        _pruned = await asyncio.to_thread(prune_merged_worktrees)
                        _removed = [r for r in _pruned if r.get("decision") == "removed"]
                        if _removed:
                            logger.info(
                                "multica_poll: pruned %d stale merged worktree(s)", len(_removed)
                            )
                    except Exception as _prune_exc:
                        logger.warning("multica_poll: worktree prune sweep failed: %s", _prune_exc)
                # Board-state reads must distinguish a Multica outage from an empty
                # board, but a partial status-read failure should not starve work
                # from statuses already read this cycle.
                (
                    _board_statuses,
                    _failed_board_statuses,
                ) = await _read_multica_board_statuses(
                    client,
                    ("todo", "in_progress", "in_review", "blocked"),
                )
                _can_start_new_multica_work = not _failed_board_statuses
                stale_todos = _board_statuses["todo"]
                _now_ts = _t.time()
                _closed_stale_ids: set[str] = set()
                for _stale in stale_todos or []:
                    _stale_title = _stale.get("title", "")
                    _stale_id = _stale.get("id")
                    if not _stale_id or not _stale_title.startswith("Autopilot:"):
                        continue
                    _created = _stale.get("created_at", "")
                    try:
                        import datetime as _dt
                        _age_h = (_dt.datetime.now(_dt.timezone.utc) - _dt.datetime.fromisoformat(
                            _created.replace("Z", "+00:00")
                        )).total_seconds() / 3600
                        if _age_h >= 2:
                            await client.update_issue(_stale_id, status="done")
                            _closed_stale_ids.add(str(_stale_id))
                            logger.info("multica_poll: auto-closed stale todo '%s'", _stale_title[:50])
                    except Exception as _se:
                        logger.debug("multica_poll: stale-todo close error: %s", _se)
                if _closed_stale_ids:
                    stale_todos = [
                        issue
                        for issue in stale_todos
                        if str(issue.get("id") or "") not in _closed_stale_ids
                    ]

                in_progress_issues = _board_statuses["in_progress"]
                in_review_issues = _board_statuses["in_review"]
                blocked_issues = _board_statuses["blocked"]

                # Webhook bridge: Hermes-assigned todos / in_progress (no chain) → issue.assigned.
                try:
                    from multica_webhook_emitter import is_configured as _wh_ok
                    from multica_client import get_engineering_multica_agent_id  # type: ignore[import]
                    from multica_poll_dispatch import chain_is_running, chain_needs_dispatch, chain_poll_failed  # type: ignore[import]
                    from multica_dispatch_control import dispatch_is_paused, pause_reason

                    _dispatch_paused = dispatch_is_paused()
                    if _wh_ok() and not _dispatch_paused:
                        _hermes = str(get_engineering_multica_agent_id())

                        # Bound every chain poll and reclaim zombie in_progress
                        # chains, so one dead executor ref can neither wedge the
                        # loop nor jam the single lane indefinitely.
                        _poll_timeout = env_float("ZOE_MULTICA_POLL_REF_TIMEOUT_S", 20.0)
                        _stale_ip_hours = env_float("ZOE_MULTICA_STALE_IN_PROGRESS_HOURS", 6.0)
                        _chain_cache: dict[str, dict] = {}

                        async def _poll_chain(_issue: dict) -> dict:
                            _tid = str(_issue.get("id") or "")
                            if _tid not in _chain_cache:
                                _chain_cache[_tid] = await _poll_chain_guarded(
                                    f"multica:{_tid}", issue=_issue, timeout=_poll_timeout
                                )
                            return _chain_cache[_tid]

                        # Recover dead in_progress chains before the idle check so a
                        # freed lane can admit new work this same cycle.
                        if _stale_ip_hours > 0 and in_progress_issues:
                            import datetime as _dt

                            in_progress_issues = await _recover_stale_in_progress_issues(
                                client,
                                in_progress_issues,
                                hermes_id=_hermes,
                                poll_chain=_poll_chain,
                                now=_dt.datetime.now(_dt.timezone.utc),
                                max_age_hours=_stale_ip_hours,
                            )

                        # Throttle first-dispatch: cap new chains per cycle so a
                        # wave of assigned todos can't spawn N concurrent chains
                        # (mirrors the compatibility sync limit / kanban.max_in_progress).
                        _wh_limit = env_int("ZOE_MULTICA_POLL_DISPATCH_LIMIT", 1)
                        if (
                            _wh_limit > 0
                            and os.environ.get("ZOE_MULTICA_AUTO_ADMIT", "false").lower() == "true"
                            and _can_start_new_multica_work
                            and not stale_todos
                            and not in_progress_issues
                            and not in_review_issues
                        ):
                            from multica_admission import select_next_approved_issue

                            _backlog = await client.list_issues(status="backlog") or []
                            _all_issues = await client.list_issues() or []
                            _admitted, _held = select_next_approved_issue(
                                _backlog,
                                _all_issues,
                                hermes_agent_id=_hermes,
                            )
                            for _reason in _held:
                                logger.info("multica_poll: admission held %s", _reason)
                            if _admitted and _admitted.get("id"):
                                _admitted = await client.update_issue(
                                    str(_admitted["id"]),
                                    status="todo",
                                )
                                if _admitted.get("id"):
                                    stale_todos.append(_admitted)
                                    logger.info(
                                        "multica_poll: admitted %s from backlog into the single ticket lane",
                                        _admitted.get("identifier") or _admitted.get("id"),
                                    )

                        _running_chains = 0
                        for _ip_issue in in_progress_issues:
                            if str(_ip_issue.get("assignee_id") or "") != _hermes:
                                continue
                            if (_ip_issue.get("title") or "").lower().startswith("autopilot:"):
                                continue
                            _ip_tid = str(_ip_issue.get("id") or "")
                            if _ip_tid and chain_is_running(await _poll_chain(_ip_issue)):
                                _running_chains += 1

                        _wh_dispatched = min(_running_chains, _wh_limit)
                        _wh_dispatched_ids: set[str] = set()
                        if _running_chains >= _wh_limit:
                            logger.info(
                                "multica_poll: dispatch paused; %d running Hermes chain(s) already at limit %d",
                                _running_chains,
                                _wh_limit,
                            )

                        async def _maybe_dispatch_hermes_issue(_candidate: dict, *, from_todo: bool) -> None:
                            nonlocal _wh_dispatched
                            if _wh_dispatched >= _wh_limit:
                                return
                            if str(_candidate.get("assignee_id") or "") != _hermes:
                                return
                            if (_candidate.get("title") or "").lower().startswith("autopilot:"):
                                return
                            _tid = str(_candidate.get("id") or "")
                            if not _tid:
                                return
                            _chain = await _poll_chain(_candidate)
                            # A failed poll (timeout/error sentinel) on a known in-progress
                            # chain must NOT silently skip it — that stranded chains past
                            # implement forever (an existing multi-row chain's poll can time
                            # out under load). Re-poll once fresh with a generous timeout; if
                            # it still can't resolve, fall through to dispatch_issue anyway —
                            # dispatch is idempotent + re-derives state from the journal, so it
                            # safely creates the next ready phase or no-ops if a row exists.
                            if chain_poll_failed(_chain) and not from_todo:
                                logger.info(
                                    "multica_poll: chain poll failed for %s (%s); re-polling before skip",
                                    _candidate.get("identifier") or _tid,
                                    _chain.get("status"),
                                )
                                _chain = await _resolve_chain_for_dispatch(
                                    _chain,
                                    ref=f"multica:{_tid}",
                                    issue=_candidate,
                                    poll_guarded=_poll_chain_guarded,
                                    poll_timeout=_poll_timeout,
                                )
                                # Keep the per-cycle cache consistent with the re-poll so the
                                # later todo loop sees the resolved state, not the stale sentinel.
                                _chain_cache[_tid] = _chain
                                # If the re-poll revealed an active worker, the single lane is
                                # genuinely occupied (the earlier running-count missed it via the
                                # sentinel). Mark the lane full so the todo loop can't over-dispatch.
                                if chain_is_running(_chain):
                                    _wh_dispatched = _wh_limit
                                    return
                            if not chain_needs_dispatch(_chain) and not chain_poll_failed(_chain):
                                return
                            _phase = (_chain.get("pipeline") or {}).get("phase")
                            # Dispatch the next ready phase IN-PROCESS. The poll loop runs
                            # inside zoe-data, so it calls dispatch_issue() directly rather
                            # than POSTing issue.assigned to its own :8000 webhook receiver.
                            # That loopback hop (a self-HTTP call with a 10s timeout against a
                            # busy event loop) could silently no-op and strand a chain at its
                            # next phase — e.g. implement done but verify never dispatched,
                            # which blocked autonomous verify→review→closeout advancement.
                            try:
                                from executor_registry import dispatch_issue  # type: ignore[import]

                                _result = await dispatch_issue(_candidate)
                            except Exception as _disp_exc:
                                logger.warning(
                                    "multica_poll: dispatch_issue failed for %s (phase=%s): %s",
                                    _candidate.get("identifier") or _tid,
                                    _phase,
                                    _disp_exc,
                                )
                                return
                            if not (isinstance(_result, dict) and _result.get("ok")):
                                logger.info(
                                    "multica_poll: dispatch_issue(%s) phase=%s not dispatched: %s",
                                    _candidate.get("identifier") or _tid,
                                    _phase,
                                    (_result or {}).get("reason")
                                    if isinstance(_result, dict)
                                    else _result,
                                )
                                return
                            if from_todo:
                                try:
                                    await client.update_issue(_tid, status="in_progress")
                                except Exception as _ip_exc:
                                    logger.debug(
                                        "multica_poll: set in_progress failed for %s: %s",
                                        _tid,
                                        _ip_exc,
                                    )
                            elif (_candidate.get("status") or "") == "blocked":
                                try:
                                    await client.record_progress(
                                        _tid,
                                        phase=_phase,
                                        evidence="Engineering run resumed",
                                        status="in_progress",
                                        clear_blocker=True,
                                    )
                                except Exception as _resume_exc:
                                    logger.debug(
                                        "multica_poll: clear stale blocked status failed for %s: %s",
                                        _tid,
                                        _resume_exc,
                                    )
                            _wh_dispatched += 1
                            _wh_dispatched_ids.add(_tid)
                            logger.info(
                                "multica_poll: dispatched %s phase=%s (%s)",
                                _candidate.get("identifier") or _tid,
                                _phase,
                                "todo" if from_todo else "in_progress-backfill",
                            )

                        # Backfill existing in-progress runs before starting fresh todo work.
                        # A partial chain owns the one active ticket lane but still needs this
                        # dispatch path to create its next ready phase.
                        if _wh_dispatched < _wh_limit:
                            for _ip_issue in in_progress_issues:
                                if str(_ip_issue.get("id") or "") in _wh_dispatched_ids:
                                    continue
                                await _maybe_dispatch_hermes_issue(_ip_issue, from_todo=False)
                                if _wh_dispatched >= _wh_limit:
                                    break
                        # Resume blocked issues only when the journal says a non-terminal next
                        # phase is ready; terminal/fingerprint blocks remain operator-visible.
                        # Each resume check is an expensive executor poll, so bound how many
                        # blocked chains we probe per cycle (rotating across cycles) — probing
                        # all of them every cycle can starve admission and OOM the host.
                        if _wh_dispatched < _wh_limit:
                            _blk_budget = env_int("ZOE_MULTICA_BLOCKED_RESUME_BUDGET", 4)
                            if _blk_budget <= 0:
                                logger.warning(
                                    "multica_poll: ZOE_MULTICA_BLOCKED_RESUME_BUDGET=%s is not "
                                    "positive; blocked-resume polling disabled this cycle",
                                    _blk_budget,
                                )
                            _blk_window, _BLOCKED_RESUME_CURSOR["offset"] = _bounded_blocked_resume_window(
                                list(blocked_issues or []),
                                _BLOCKED_RESUME_CURSOR["offset"],
                                _blk_budget,
                            )
                            for _blocked in _blk_window:
                                if str(_blocked.get("id") or "") in _wh_dispatched_ids:
                                    continue
                                await _maybe_dispatch_hermes_issue(_blocked, from_todo=False)
                                if _wh_dispatched >= _wh_limit:
                                    break
                        # Reuse the todo list already fetched above for the stale autopilot pass.
                        if _wh_dispatched < _wh_limit and _can_start_new_multica_work:
                            for _todo in stale_todos or []:
                                await _maybe_dispatch_hermes_issue(_todo, from_todo=True)
                                if _wh_dispatched >= _wh_limit:
                                    break
                    elif _dispatch_paused:
                        logger.info(
                            "multica_poll: runtime dispatch pause active (%s)",
                            pause_reason(),
                        )
                except Exception as _wh_exc:
                    logger.debug("multica_poll: webhook dispatch failed: %s", _wh_exc)

                issues = _tracked_multica_engineering_issues(
                    in_progress_issues,
                    in_review_issues,
                )
                in_review_ids = {
                    str(issue.get("id") or "")
                    for issue in in_review_issues
                    if issue.get("id")
                }
                # Read the poll timeout once per cycle (not per tracked issue). The
                # dispatch path resolves the same env var separately; this branch may
                # run when that block was skipped, so it keeps its own local.
                _reconcile_timeout = env_float("ZOE_MULTICA_POLL_REF_TIMEOUT_S", 20.0)
                from multica_poll_dispatch import chain_needs_reconcile  # type: ignore[import]

                for issue in issues:
                    # Check whether a linked engineering workflow has reached a terminal state.
                    issue_id = issue.get("id")
                    title = issue.get("title", "")
                    if not issue_id:
                        continue
                    if title.startswith("Autopilot:"):
                        if str(issue_id) in in_review_ids:
                            continue
                        try:
                            import datetime as _dt

                            _created = issue.get("created_at", "")
                            _age_h = 0.0
                            try:
                                _age_h = (
                                    _dt.datetime.now(_dt.timezone.utc)
                                    - _dt.datetime.fromisoformat(_created.replace("Z", "+00:00"))
                                ).total_seconds() / 3600
                            except Exception:
                                _age_h = 2.0
                            if _age_h >= 2:
                                await client.update_issue(issue_id, status="done")
                                logger.info(
                                    "multica_poll: closed stale autopilot '%s' (was %s)",
                                    title[:50],
                                    issue.get("status", "in_progress"),
                                )
                        except Exception as _ap_exc:
                            logger.debug("multica_poll: autopilot in_progress close: %s", _ap_exc)
                        continue
                    try:
                        # Use the timeout-guarded poller (not raw poll_ref): a died
                        # executor reference must not hang the whole poll iteration.
                        # On timeout/error this returns a sentinel with found=False,
                        # so every branch below is safely skipped this cycle. See
                        # test_reconcile_branch_skips_on_poll_timeout_sentinel
                        # (tests/test_main_multica_poll.py) for the skip-on-sentinel
                        # regression coverage of this call site.
                        chain = await _poll_chain_guarded(
                            f"multica:{issue_id}", issue=issue, timeout=_reconcile_timeout
                        )
                        if chain.get("found") and chain.get("status") == "done":
                            pr_url = chain.get("pr_url")
                            await _record_completed_multica_chain(client, str(issue_id), chain)
                            logger.info(
                                "multica_poll: advanced issue %s (%s) - engineering run done%s",
                                issue_id,
                                title[:40],
                                f" PR={pr_url}" if pr_url else "",
                            )
                            # Push WebSocket notification to all connected clients
                            try:
                                await broadcaster.broadcast(
                                    "all",
                                    "multica_task_done",
                                    {
                                        "multica_issue_id": str(issue_id),
                                        "title": title,
                                        "pr_url": chain.get("pr_url"),
                                    },
                                )
                            except Exception as _push_exc:
                                logger.debug("multica_poll: ws push failed: %s", _push_exc)
                        elif chain.get("found") and chain.get("status") == "blocked":
                            blocker = await _record_blocked_multica_chain(client, str(issue_id), chain)
                            logger.info(
                                "multica_poll: blocked issue %s (%s) - %s",
                                issue_id,
                                title[:40],
                                blocker,
                            )
                            try:
                                await broadcaster.broadcast(
                                    "all",
                                    "multica_task_blocked",
                                    {
                                        "multica_issue_id": str(issue_id),
                                        "title": title,
                                        "blocker": blocker,
                                    },
                                )
                            except Exception as _push_exc:
                                logger.debug("multica_poll: ws block push failed: %s", _push_exc)
                        elif chain.get("found") and chain.get("status") == "running":
                            if await _record_running_multica_chain_progress(
                                client,
                                str(issue_id),
                                chain,
                                issue=issue,
                            ):
                                logger.info(
                                    "multica_poll: synced running issue %s (%s) progress%s",
                                    issue_id,
                                    title[:40],
                                    f" PR={chain.get('pr_url')}" if chain.get("pr_url") else "",
                                )
                                try:
                                    await broadcaster.broadcast(
                                        "all",
                                        "multica_task_progress",
                                        {
                                            "multica_issue_id": str(issue_id),
                                            "title": title,
                                            "phase": (chain.get("pipeline") if isinstance(chain.get("pipeline"), dict) else {}).get("phase"),
                                            "pr_url": chain.get("pr_url"),
                                            **({"status": "in_review"} if chain.get("pr_url") else {}),
                                        },
                                    )
                                except Exception as _push_exc:
                                    logger.debug("multica_poll: ws progress push failed: %s", _push_exc)
                        elif chain.get("found") and chain_needs_reconcile(chain):
                            try:
                                if await _reconcile_diverged_board_status(
                                    client, str(issue_id), issue, chain
                                ):
                                    logger.warning(
                                        "multica_poll: reconciled diverged issue %s (%s) %s->in_progress "
                                        "(partial chain needs re-dispatch)",
                                        issue_id,
                                        title[:40],
                                        issue.get("status"),
                                    )
                            except Exception as _rec_exc:
                                logger.debug(
                                    "multica_poll: divergence reconcile failed for %s: %s",
                                    issue_id,
                                    _rec_exc,
                                )
                    except Exception as _inner_exc:
                        logger.debug("multica_poll: inner error for issue %s: %s", issue_id, _inner_exc)

                for _review in in_review_issues:
                    _rt = _review.get("title", "")
                    _rid = _review.get("id")
                    if _rid and _rt.startswith("Autopilot:"):
                        try:
                            await client.update_issue(_rid, status="done")
                            logger.info(
                                "multica_poll: closed in_review autopilot '%s'",
                                _rt[:50],
                            )
                        except Exception as _rev_exc:
                            logger.debug("multica_poll: in_review autopilot close: %s", _rev_exc)
            except asyncio.CancelledError:
                return
            except MulticaUnavailableError as _outage_exc:
                # Multica OUTAGE (not an empty board): log loudly and skip this cycle.
                # The loop keeps running and retries next pass — dispatch/sync are
                # deferred, never silently suppressed, until Multica recovers.
                logger.warning(
                    "multica_poll: Multica unavailable (%s) — skipping this cycle; "
                    "dispatch/sync deferred until it recovers", _outage_exc,
                )
            except Exception as _exc:
                logger.debug("multica_poll: loop error (non-fatal): %s", _exc)

    if os.environ.get("ZOE_MULTICA", "false").lower() == "true":
        asyncio.create_task(_multica_poll_loop(), name="multica_poll")
        logger.info("Multica board polling loop started (30s interval)")

    # Start the LiveKit server-side voice agent (connects to the LiveKit room and handles VAD/STT/TTS).
    try:
        from routers.voice_livekit import start_livekit_agent
        asyncio.create_task(start_livekit_agent(), name="livekit_agent")
        logger.info("LiveKit voice agent task created")
    except Exception as _lk_exc:
        logger.warning("LiveKit setup (non-fatal): %s", _lk_exc)

    yield

    # Stop skills watcher thread
    try:
        if _skills_observer is not None:
            _skills_observer.stop()
            _skills_observer.join(timeout=3)
    except Exception:
        pass
    try:
        from proactive.engine import stop_proactive_engine
        stop_proactive_engine()
    except Exception:
        pass
    try:
        from zoe_core_client import shutdown_workers
        await shutdown_workers(reset_timeout_s=2.0)
    except asyncio.TimeoutError:
        logger.warning("zoe-core worker shutdown timed out (non-fatal)")
    except Exception:
        logger.warning("zoe-core worker shutdown failed (non-fatal)", exc_info=True)
    for task in (_openclaw_bg_task, _digest_bg_task, _zoe_update_bg_task,
                 _consolidation_bg_task, _runtime_health_task):
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
    logger.info("zoe-data shutting down.")


app = FastAPI(
    title="zoe-data",
    description="Structured data backend for Zoe family assistant",
    version="1.0.0",
    lifespan=lifespan,
)





from middleware.logging import StructuredLoggingMiddleware

app.add_middleware(StructuredLoggingMiddleware)

# Browser origins permitted to make credentialed requests. This base list plus
# the ZOE_ALLOWED_WS_ORIGINS extras (resolved by _allowed_browser_origins) are
# the single source of truth for BOTH the HTTP CORS policy (below) and the
# WebSocket CSWSH origin guard (_ws_origin_allowed) so the two cannot drift
# apart — an operator-added kiosk origin is honoured by credentialed HTTP calls
# and WebSocket handshakes alike.
ALLOWED_ORIGINS = [
    "https://zoe.local",
    "https://zoe.the411.life",
    "http://zoe.local",
    "http://localhost",
    "http://localhost:8080",
]


def _self_lan_origins() -> frozenset[str]:
    """Origins that point at THIS host's own LAN address (https://<own-ip>).

    The touch kiosk loads the UI from the server's LAN IP
    (https://192.168.1.218/touch/home.html — see
    scripts/setup/touchscreen/config.json), so its browser sends
    ``Origin: https://<this-host's-ip>``. That origin is same-site by
    construction — the page was served BY this stack — yet the name-based
    allowlist above can't express it, and when the CSWSH guard shipped without
    it the panel's voice+push websockets were 403'd and the kiosk went dead
    (2026-07-03 incident). Deriving it from the host's own addresses keeps the
    allowlist correct on any deployment without hardcoding an IP; it can never
    admit a foreign origin because it only ever names this machine.

    ZOE_HOST_LAN_IP overrides discovery when set (multi-homed hosts).
    """
    ips: set[str] = set()
    env_ip = env_str("ZOE_HOST_LAN_IP")
    if env_ip:
        ips.add(env_ip)
    else:
        try:
            import socket

            # UDP "connect" selects the primary outbound interface without
            # sending traffic; works headless and needs no DNS.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                ips.add(s.getsockname()[0])
        except OSError as exc:
            # Do NOT stay silent: with no self origin the kiosk's websockets are
            # 403'd again (the exact 2026-07-03 incident) and nothing would say
            # why. Surface it and point at the manual overrides.
            logger.warning(
                "Self-LAN origin discovery failed (%s) — the touch kiosk's "
                "Origin (https://<host-ip>) will NOT be allowlisted; set "
                "ZOE_HOST_LAN_IP or ZOE_ALLOWED_WS_ORIGINS to restore it",
                exc,
            )
    return frozenset(f"https://{ip}" for ip in ips if ip)


_SELF_LAN_ORIGINS = _self_lan_origins()  # resolved once at import


def _allowed_browser_origins() -> frozenset[str]:
    """The credentialed-origin allowlist shared by HTTP CORS and the WebSocket
    CSWSH guard: the base ALLOWED_ORIGINS, this host's own LAN origin(s)
    (see _self_lan_origins — the kiosk connects with Origin:https://<own-ip>),
    plus any extra panel/kiosk hosts configured via ZOE_ALLOWED_WS_ORIGINS
    (comma-separated).

    The env var lets an operator add a LAN hostname/IP-based origin the browser
    kiosk uses without a code change; it is empty by default. Because both the
    CORS middleware and the WS guard read this one function, adding an origin
    here cannot leave the HTTP and WebSocket policies inconsistent.
    """
    extra = env_list("ZOE_ALLOWED_WS_ORIGINS")
    return frozenset(ALLOWED_ORIGINS) | _SELF_LAN_ORIGINS | frozenset(extra)


def _ws_origin_allowed(websocket: WebSocket) -> bool:
    """CSWSH guard: True if this WS handshake's Origin may connect.

    Policy:
    - No Origin header → allow. Browsers ALWAYS send Origin on a WS handshake,
      so a missing Origin means a non-browser client (the native kiosk/voice
      panel, CLI tools, internal services). Those are not reachable by the
      cross-site-request threat CSWSH defends against, so we do not block them.
    - Origin present and in the allowlist → allow.
    - Origin present and NOT in the allowlist → reject (foreign web origin).
    """
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    return origin in _allowed_browser_origins()


async def _enforce_ws_origin(websocket: WebSocket) -> bool:
    """Apply the CSWSH origin allowlist to a WS handshake.

    Returns True when the connection may proceed. On rejection it closes the
    handshake before accept (the ASGI server turns a pre-accept close into an
    HTTP 403; the 1008 policy-violation code is recorded for clients that see
    it) and returns False — the caller MUST return immediately.
    """
    if _ws_origin_allowed(websocket):
        return True
    logger.warning(
        "Rejected cross-origin WebSocket handshake: origin=%r path=%s",
        websocket.headers.get("origin"),
        websocket.url.path,
    )
    await websocket.close(code=1008)
    return False


app.add_middleware(
    CORSMiddleware,
    # Same allowlist the WS CSWSH guard uses (base list + ZOE_ALLOWED_WS_ORIGINS
    # extras) so HTTP CORS and WebSocket origin policy can never drift apart.
    allow_origins=sorted(_allowed_browser_origins()),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(calendar_router)
app.include_router(lists_router)
app.include_router(people_router)
app.include_router(memories_router)
app.include_router(reminders_router)
app.include_router(notes_router)
app.include_router(journal_router)
app.include_router(transactions_router)
app.include_router(weather_router)
app.include_router(system_router)
app.include_router(_agent_card_router)
app.include_router(notifications_router)
app.include_router(chat_router)
app.include_router(ui_router)
app.include_router(openclaw_router)
app.include_router(voice_tts_router)
from routers.voice_settings import router as voice_settings_router  # noqa: E402

app.include_router(voice_settings_router)
app.include_router(user_profile_router)
app.include_router(dashboard_router)
app.include_router(stubs_router)
app.include_router(push_router)
app.include_router(proactive_router)
app.include_router(panel_auth_router)
app.include_router(panel_provision_router)
# Registered AFTER panel_provision on purpose: that router's more specific
# /api/panels/provision/{code} must win over /api/panels/{device_id}/config
# (FastAPI matches routes in registration order).
from routers.panel_config import router as panel_config_router
app.include_router(panel_config_router)
app.include_router(capability_matrix_router)
app.include_router(music_router)
from routers.music_setup import router as music_setup_router
app.include_router(music_setup_router)
from routers.smart_home_setup import router as smart_home_setup_router
app.include_router(smart_home_setup_router)
app.include_router(skybridge_router)
app.include_router(autoresearch_router)
app.include_router(pi_intent_lab_router)

from routers.portrait import router as portrait_router
app.include_router(portrait_router)

from routers.ha_control import router as ha_control_router
app.include_router(ha_control_router)

from routers.auth import router as auth_router
app.include_router(auth_router)

try:
    from routers.voice_livekit import router as voice_livekit_router
    app.include_router(voice_livekit_router)
    logger.info("LiveKit audio upload endpoint registered")
except Exception as _lk_router_exc:
    logger.warning("LiveKit router not loaded (non-fatal): %s", _lk_router_exc)


@app.get("/.well-known/agent.json", include_in_schema=False)
async def a2a_well_known():
    """A2A v1.0 agent discovery — served inline to avoid redirect caching issues."""
    from routers.system import _build_agent_card
    return _build_agent_card()


from db_pool import PoolExhaustedError as _PoolExhaustedError


@app.exception_handler(_PoolExhaustedError)
async def _pool_exhausted_handler(request: Request, exc: _PoolExhaustedError):
    """Map pool exhaustion to a diagnosable 503 instead of a generic 500.

    Any endpoint using Depends(get_db) raises PoolExhaustedError before its
    handler body runs when the pool is drained; without this mapping clients
    would see FastAPI's opaque 500 and lose the outage signature."""
    return JSONResponse(
        {"detail": str(exc), "error": "db_pool_exhausted"}, status_code=503
    )


# /health pool-check cache: watchdogs poll /health frequently, so the pooled
# acquire+SELECT 1 probe runs at most once per TTL and the verdict is reused.
# Keeps the fast path allocation-free while still catching exhaustion within ~5s.
# Only a HEALTHY verdict is cached — see below.
_POOL_HEALTH_TTL_S = 5.0
_pool_health_cache = {"checked_at": 0.0, "healthy": True, "detail": "not checked yet"}


@app.get("/health")
async def root_health():
    # Honest health: verify a pooled DB connection is actually acquirable.
    # The 2026-07-12 outage drained the pool while /health stayed 200 — every
    # chat request hung, invisible to monitoring. Conservative by design:
    # only a genuine acquire timeout reports 503 (check_pool_health fails open
    # on every other condition), because a false 503 triggers watchdog restarts.
    # Only a healthy verdict is cached: while unhealthy we re-probe every call
    # (bounded at 2s) so a recovered pool flips back to 200 immediately instead
    # of holding a stale 503 for the TTL and triggering a needless watchdog restart.
    now = time.monotonic()
    if (not _pool_health_cache["healthy"]) or (
        now - _pool_health_cache["checked_at"] > _POOL_HEALTH_TTL_S
    ):
        try:
            from db_pool import check_pool_health
            healthy, detail = await check_pool_health(timeout_s=2.0)
        except Exception as exc:  # health must never crash on a probe error
            healthy, detail = True, f"pool check unavailable: {exc}"
        _pool_health_cache.update(checked_at=now, healthy=healthy, detail=detail)

    body = {
        "status": "ok" if _pool_health_cache["healthy"] else "unhealthy",
        "service": "zoe-data",
        "version": "1.0.0",
        "memory_capture": _memory_capture_health,
        "db_pool": {
            "healthy": _pool_health_cache["healthy"],
            "detail": _pool_health_cache["detail"],
        },
    }
    if not _pool_health_cache["healthy"]:
        return JSONResponse(body, status_code=503)
    return body


@app.get("/readyz")
async def root_readyz():
    report = await _build_readiness_report(use_cache=True)
    return JSONResponse(report, status_code=200 if report["ready"] else 503)


@app.get("/api/router/classify")
async def router_classify(text: str, _: None = Depends(require_internal_token)):
    """Tier-1 semantic router test endpoint: classify an utterance into a domain.
    Internal-token gated (like /metrics) — it exposes per-domain scores, the
    active mode and the threshold, which could be used to calibrate utterances
    to influence routing if the service were LAN-exposed."""
    import semantic_router as _sr
    return {"enabled": _sr.is_enabled(), "mode": _sr.mode(),
            "threshold": _sr.threshold(), **_sr.route(text)}


@app.get("/api/settings")
async def app_settings():
    """Public settings consumed by frontend widgets (e.g. music widget HA deep-link)."""
    import os
    return {
        "homeassistant_url": os.environ.get("HA_BASE_URL", "http://homeassistant.local:8123"),
    }


@app.get("/metrics")
async def prometheus_metrics(_: None = Depends(require_internal_token)):
    """Prometheus scrape endpoint.

    Exposes counters/gauges from `memory_metrics.REGISTRY` (MemPalace ingest,
    search latency, PII rejects, feedback, training, routing). Refreshes the
    per-user collection-size gauge inline so dashboards always see current
    values. Uses `prometheus_client.generate_latest` with our dedicated
    registry to avoid leaking default Python/process metrics.
    """
    from fastapi.responses import Response
    from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
    from memory_metrics import REGISTRY, snapshot_collection_sizes

    await snapshot_collection_sizes()
    return Response(
        content=generate_latest(REGISTRY),
        media_type=CONTENT_TYPE_LATEST,
    )


@app.post("/api/internal/broadcast")
async def internal_broadcast(payload: dict, _: None = Depends(require_internal_token)):
    """Internal endpoint for MCP server to trigger WebSocket broadcasts."""
    channel = payload.get("channel", "all")
    event_type = payload.get("event_type", "update")
    data = payload.get("data", {})
    await broadcaster.broadcast(channel, event_type, data)
    return {"ok": True}


async def _resolve_subscribable_panel(panel_id: str, session_id: str | None) -> str | None:
    """Resolve the *canonical* panel channel a browser push socket may subscribe to.

    Returns the panel_id whose ``panel_{id}`` channel the socket should join, or
    ``None`` to reject the connection.

    A single physical browser may hold several ``ui_panel_sessions`` rows for one
    session over its life: a generated ``panel_xxxx`` alias captured from
    localStorage, plus the REGISTERED id (e.g. ``zoe-touch-pi``). But server pushes
    (``broadcast_to_panel``) are addressed to the registered panel, and the
    registered ids are exactly the rows in the ``panels`` table — generated aliases
    are written to ``ui_panel_sessions`` by bind but never to ``panels``.

    Resolution prefers the session's REGISTERED panel (a bound row whose id is in
    ``panels``) over a generated alias, which closes the whole "stale alias wins /
    rebinds" class instead of fixing it per-permutation. Crucially the selected
    registered row is TIED TO THE CONNECTING panel, so a session that holds rows for
    SEVERAL registered panels (e.g. A and B) can't misroute:
      1. Among the session's registered bound rows, prefer the one whose
         ``panel_id`` IS the connecting id; only when none is (the connecting id is
         a generated alias, not a registered row) fall back to the foreground /
         most-recently-seen registered row. So panel A reconnecting always joins
         ``panel_A`` even when panel B is foreground in the same session, while a
         generated ``panel_xxxx`` still resolves to the registered
         ``panel_zoe-touch-pi``. (An alias has no column linking it to a specific
         registered panel; the session's foreground/most-recent registered panel is
         the documented best-guess target for it.)
      2. Else (the session has only generated aliases, or no session_id) fall back
         to the connecting id when it is itself a bound row the user owns — under
         this session, or a NULL-session legacy/device bind.
      * admin/agent roles may target any explicit panel_id.
      * an unresolved session defers to the registered-panel guest policy.
    A user can never reach a panel their session/account isn't bound to (every
    query filters on ``user_id``).
    """
    user = await _resolve_ws_session(session_id)
    if user is None:
        if panel_id and await _panel_allows_guest_push(panel_id):
            return panel_id
        return None
    user_id = str(user.get("user_id") or "")
    role = str(user.get("role") or "").lower()
    if not user_id:
        return None
    if role in {"admin", "agent"}:
        # Trusted roles may subscribe to an explicit panel channel as-is.
        return panel_id or None
    if not panel_id:
        return None
    try:
        # get_db_ctx, NOT `async for db in get_db()`: this guard RETURNS from inside
        # the DB block, and returning out of get_db()'s generator LEAKS the pooled
        # connection (see #953). The panel kiosk retries its push socket every few
        # seconds, so each leaked conn compounds — max_size=10 drained the WHOLE
        # pool within a minute of deploy and took every DB-backed endpoint down
        # (voice, chat, API) while /health stayed green. The context manager
        # releases on ANY exit path.
        from db_pool import get_db_ctx

        async with get_db_ctx() as db:
            # (1) CANONICAL: a REGISTERED panel (a bound row whose panel_id exists
            # in `panels`) for this session + user. The connecting id is tied into
            # the choice via `(s.panel_id = ?) DESC`, which ranks the row for the
            # CONNECTING panel first. So when the session holds rows for several
            # registered panels (A and B), panel A's socket resolves to panel_A —
            # never whichever row happens to win is_foreground/last_seen_at. Only
            # when the connecting id is NOT one of the session's registered rows
            # (it is a generated alias) does the foreground/recency order decide;
            # that is the documented fallback (an alias has no column linking it to
            # a specific registered panel, so the foreground/most-recent registered
            # panel is the best canonical target — this still routes panel_<alias>
            # to panel_zoe-touch-pi). user_id scoping keeps it the session's own.
            if session_id:
                cursor = await db.execute(
                    "SELECT s.panel_id FROM ui_panel_sessions s "
                    "JOIN panels p ON p.panel_id = s.panel_id "
                    "WHERE s.chat_session_id = ? AND s.user_id = ? "
                    "ORDER BY (s.panel_id = ?) DESC, "
                    "s.is_foreground DESC, s.last_seen_at DESC LIMIT 1",
                    (session_id, user_id, panel_id),
                )
                row = await cursor.fetchone()
                if row and row["panel_id"]:
                    return str(row["panel_id"])

            # (2) No registered panel for this session (only generated aliases, or
            # no session_id). Authorise the connecting id when it is itself a bound
            # row the user owns — under this session, or a NULL-session legacy/device
            # bind — and subscribe to its own channel.
            cursor = await db.execute(
                "SELECT 1 FROM ui_panel_sessions "
                "WHERE panel_id = ? AND user_id = ? "
                "AND (chat_session_id = ? OR chat_session_id IS NULL) LIMIT 1",
                (panel_id, user_id, session_id),
            )
            if await cursor.fetchone():
                return panel_id
            return None
    except Exception as exc:
        logger.debug("push websocket panel session validation failed: %s", exc)
        return None


async def _panel_allows_guest_push(panel_id: str) -> bool:
    """Return true when a registered active panel explicitly allows guest use."""
    try:
        # get_db_ctx, NOT `async for db in get_db()` — returning from inside the
        # generator leaks the pooled connection (#953); this function returns from
        # inside the block and runs on every kiosk push reconnect. See
        # _resolve_subscribable_panel for the incident this caused.
        from db_pool import get_db_ctx

        async with get_db_ctx() as db:
            cursor = await db.execute(
                "SELECT allow_guest, is_active FROM panels WHERE panel_id = ? LIMIT 1",
                (panel_id,),
            )
            row = await cursor.fetchone()
            if not row:
                return False
            return bool(row["allow_guest"]) and bool(row["is_active"])
    except Exception as exc:
        logger.debug("push websocket guest panel validation failed: %s", exc)
        return False
    return False


async def _receive_ws_text_with_deadline(websocket: WebSocket) -> str:
    return await asyncio.wait_for(
        websocket.receive_text(),
        timeout=WS_IDLE_TIMEOUT_SECONDS,
    )


async def _run_push_ws_loop(
    websocket: WebSocket,
    disconnect_channel: str,
    *,
    allow_catchup: bool = False,
):
    try:
        while True:
            data = await _receive_ws_text_with_deadline(websocket)
            if data == "ping":
                await websocket.send_json({"type": "pong"})
            elif allow_catchup and data.startswith("catchup:"):
                _, _, raw_seq = data.partition(":")
                try:
                    seq = int(raw_seq)
                except (TypeError, ValueError):
                    await websocket.send_json({
                        "type": "error",
                        "error": "invalid_catchup_sequence",
                    })
                    continue
                if seq < 0:
                    await websocket.send_json({
                        "type": "error",
                        "error": "invalid_catchup_sequence",
                    })
                    continue
                await broadcaster.catchup(websocket, seq)
    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        logger.info("WebSocket idle timeout on channel %s", disconnect_channel)
        try:
            await websocket.close(1001, "Idle timeout")
        except Exception:
            pass
    finally:
        broadcaster.disconnect(websocket, disconnect_channel)


@app.websocket("/ws/push")
async def websocket_push(
    websocket: WebSocket,
    channel: str = Query("all"),
    panel_id: str = Query(default=""),
):
    """WebSocket push endpoint.

    Clients may connect with ?panel_id=<id> to subscribe to their dedicated
    panel channel (receives both global 'all' events AND panel-specific events).
    Without panel_id, falls back to the plain channel subscription.
    """
    # CSWSH guard: reject foreign browser origins before any auth/session work.
    if not await _enforce_ws_origin(websocket):
        return
    # -----------------------------------------------------------------
    # WebSocket auth and session validation
    # -----------------------------------------------------------------
    # 1. Panel connections are exempted as long as a valid device token is present
    if panel_id:
        token_header = websocket.headers.get("X-Device-Token", "")
        device_info = None
        if token_header:
            from routers.panel_auth import lookup_device_token
            device_info = lookup_device_token(token_header)
        session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
        # A valid device token (the Pi kiosk) subscribes under its own connecting
        # id. Browser WebSockets cannot send X-Device-Token; for them resolve the
        # CANONICAL bound panel id from the session and subscribe there, so the
        # socket joins the channel pushes are actually sent to even when the client
        # connected under a different alias id.
        subscribe_panel_id = panel_id
        if not device_info:
            subscribe_panel_id = await _resolve_subscribable_panel(panel_id, session_id)
            if not subscribe_panel_id:
                await websocket.close(1008, "Invalid device token")
                return
        # Panel token or bound browser session verified; subscribe to panel push.
        await broadcaster.connect_panel(websocket, subscribe_panel_id)
        # Clean up under the RESOLVED id we actually subscribed to — not the
        # connecting alias. A browser that connected as panel_<alias> but resolved
        # to panel_<registered> would otherwise hand disconnect the wrong channel.
        # (disconnect() self-heals via the broadcaster's per-socket panel map, so
        # this isn't a live leak, but aligning the channel keeps cleanup correct,
        # explicit, and robust to future broadcaster refactors.)
        disconnect_channel = f"panel_{subscribe_panel_id}"
    else:
        # Non-panel: perform lightweight session validation via zoe-auth HTTP.
        session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
        if not session_id:
            await websocket.close(1008, "Missing session identifier")
            return
        # Use a direct zoe-auth call rather than a fake Starlette Request to avoid
        # fragility around scope fields and the fail-open degraded-user path.
        user = await _resolve_ws_session(session_id)
        if user is None:
            await websocket.close(1008, "Invalid session")
            return
        # Role allowlist gates access for valid sessions and for degraded-pass-through
        # sessions (role=member) when zoe-auth is temporarily unreachable.
        # NOTE: when zoe-auth is unreachable, _resolve_ws_session returns a degraded
        # user with role="member"; since "member" is in this allowlist the connection
        # is still permitted. This is intentional for single-family deployments where
        # LAN availability is assumed, but callers should not assume the allowlist
        # prevents unauthenticated access during an auth-service outage.
        if user.get("role") not in ("member", "admin", "agent"):
            await websocket.close(1008, "Insufficient privileges")
            return
        # Auth OK – connect to requested channel
        await broadcaster.connect(
            websocket, channel, user_id=str(user.get("user_id") or "")
        )
        disconnect_channel = channel
    # -----------------------------------------------------------------
    # Normal data relay loop
    await _run_push_ws_loop(websocket, disconnect_channel, allow_catchup=True)


@app.websocket("/api/calendar/ws/{user_id}")
async def calendar_ws(websocket: WebSocket, user_id: str):
    if not await _enforce_ws_origin(websocket):
        return
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
    # Validate user_id against the session: member role may only subscribe to their own channel.
    # admin/agent roles may subscribe on behalf of any user (e.g. background sync).
    if user.get("role") == "member" and user.get("user_id") and user.get("user_id") != user_id:
        await websocket.close(1008, "Forbidden")
        return
    await broadcaster.connect(
        websocket, "calendar", user_id=str(user.get("user_id") or user_id)
    )
    await _run_push_ws_loop(websocket, "calendar")


@app.websocket("/api/lists/ws/{user_id}")
async def lists_ws_with_user(websocket: WebSocket, user_id: str):
    if not await _enforce_ws_origin(websocket):
        return
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
    # Validate user_id against the session: member role may only subscribe to their own channel.
    if user.get("role") == "member" and user.get("user_id") and user.get("user_id") != user_id:
        await websocket.close(1008, "Forbidden")
        return
    await broadcaster.connect(
        websocket, "lists", user_id=str(user.get("user_id") or user_id)
    )
    await _run_push_ws_loop(websocket, "lists")


@app.websocket("/api/lists/ws")
async def lists_ws(websocket: WebSocket):
    if not await _enforce_ws_origin(websocket):
        return
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
    await broadcaster.connect(
        websocket, "lists", user_id=str(user.get("user_id") or "")
    )
    await _run_push_ws_loop(websocket, "lists")


@app.websocket("/api/people/ws/{user_id}")
async def people_ws(websocket: WebSocket, user_id: str):
    if not await _enforce_ws_origin(websocket):
        return
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
    # Validate user_id against the session: member role may only subscribe to their own channel.
    if user.get("role") == "member" and user.get("user_id") and user.get("user_id") != user_id:
        await websocket.close(1008, "Forbidden")
        return
    await broadcaster.connect(
        websocket, "people", user_id=str(user.get("user_id") or user_id)
    )
    await _run_push_ws_loop(websocket, "people")


@app.websocket("/api/reminders/ws/{user_id}")
async def reminders_ws(websocket: WebSocket, user_id: str):
    if not await _enforce_ws_origin(websocket):
        return
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
    # Validate user_id against the session: member role may only subscribe to their own channel.
    if user.get("role") == "member" and user.get("user_id") and user.get("user_id") != user_id:
        await websocket.close(1008, "Forbidden")
        return
    await broadcaster.connect(
        websocket, "reminders", user_id=str(user.get("user_id") or user_id)
    )
    await _run_push_ws_loop(websocket, "reminders")


@app.websocket("/api/notes/ws/{user_id}")
async def notes_ws(websocket: WebSocket, user_id: str):
    if not await _enforce_ws_origin(websocket):
        return
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
    # Validate user_id against the session: member role may only subscribe to their own channel.
    if user.get("role") == "member" and user.get("user_id") and user.get("user_id") != user_id:
        await websocket.close(1008, "Forbidden")
        return
    await broadcaster.connect(
        websocket, "notes", user_id=str(user.get("user_id") or user_id)
    )
    await _run_push_ws_loop(websocket, "notes")


@app.websocket("/api/journal/ws/{user_id}")
async def journal_ws(websocket: WebSocket, user_id: str):
    if not await _enforce_ws_origin(websocket):
        return
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
    await broadcaster.connect(
        websocket, "journal", user_id=str(user.get("user_id") or user_id)
    )
    await _run_push_ws_loop(websocket, "journal")


async def _resolve_ws_session(session_id: str | None) -> dict | None:
    """Resolve a browser session_id to a full user dict via zoe-auth HTTP.

    Returns a dict with at least ``user_id`` and ``role`` keys on success, or
    ``None`` if the session is absent, invalid, or explicitly rejected (401/403).

    On transient zoe-auth failures (5xx, timeout, connection error) the function
    returns a degraded-user dict (role=member) via auth._validate_with_auth_service.
    Because "member" is in every endpoint's allowlist, any caller that supplies a
    non-empty session_id string will be granted access while zoe-auth is unreachable
    (degraded pass-through).  This is intentional for single-family LAN deployments
    but callers must not assume the allowlist prevents unauthenticated access during
    an auth-service outage.
    """
    if not session_id:
        return None
    from auth import _validate_with_auth_service
    from fastapi import HTTPException

    try:
        return await _validate_with_auth_service(session_id)
    except HTTPException:
        return None


async def _resolve_ws_user(session_id: str) -> str:
    """Resolve a browser session_id to a user_id via zoe-auth HTTP.

    Calls the same /api/auth/user endpoint that get_current_user uses in auth.py.
    Falls back to 'voice-guest' on any auth failure or timeout.
    """
    if not session_id:
        return "voice-guest"
    try:
        import httpx
        auth_url = os.getenv("ZOE_AUTH_URL", "http://localhost:8002").rstrip("/")
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(
                f"{auth_url}/api/auth/user",
                headers={"X-Session-ID": session_id},
            )
        if r.status_code == 200:
            data = r.json()
            uid = data.get("user_id") or data.get("id") or ""
            return uid or "voice-guest"
    except Exception:
        pass
    return "voice-guest"


_VOICE_COMPOSE_BUDGET_S = float(os.environ.get("ZOE_COMPOSE_VOICE_BUDGET_S", "8"))


async def _voice_compose_cards_frame(message_text: str, reply_text: str, user_id: str):
    """Flag-gated generative UI for the PANEL: after the brain's spoken reply has
    fully streamed (audio frames already sent; playback is client-side), compose
    a card and return the ws `cards` frame for it — or None (flag off / empty
    reply / failure / budget exceeded). Never raises; never delays speech."""
    try:
        from ui_compose import compose_card, compose_enabled

        if not compose_enabled() or not (reply_text or "").strip():
            return None
        composed = await asyncio.wait_for(
            compose_card(message_text, reply_text, user_id=user_id),
            timeout=_VOICE_COMPOSE_BUDGET_S,
        )
        if not composed:
            return None
        return {
            "type": "cards",
            "result": {
                "handled": True,
                "intent": {"domain": "compose", "action": "composed"},
                "cards": [composed],
                "spoken_summary": "",
                "actions": [],
            },
        }
    except asyncio.TimeoutError:
        logger.info("voice compose skipped: exceeded budget %.1fs", _VOICE_COMPOSE_BUDGET_S)
        return None
    except Exception as exc:  # noqa: BLE001 — additive, never break the turn
        logger.debug("voice compose failed (non-fatal): %s", exc)
        return None


async def _resolve_voice_cards(message_text: str, user_id: str, context: dict | None = None) -> dict:
    """Resolve voice text to real Skybridge data cards when a supported domain exists."""
    try:
        from skybridge_service import resolve_skybridge_request

        return await resolve_skybridge_request(message_text, user_id, context=context)
    except Exception as exc:
        logger.warning("Voice WS Skybridge resolve failed: %s", exc)
        return {"handled": False, "cards": [], "spoken_summary": ""}


# Read-only intents the voice WS may answer instantly (no writes → cannot
# double-execute a skybridge-card action). Kept narrow on purpose.
_VOICE_INSTANT_INTENTS = frozenset({
    "calculate", "time_query", "date_query", "greeting", "acknowledgement", "status_check",
})


@app.websocket("/ws/voice/")
async def websocket_voice(websocket: WebSocket, session_id: str = Query("")):
    """Local voice session WebSocket for voice.html.

    Accepts text and binary (audio) messages:
    - Text JSON {"type": "text", "message": "..."} → routed through zoe_agent
    - Text JSON {"type": "cancel"} → sets cancel flag for in-flight pipeline
    - Binary → transcribed via Moonshine then routed as text
    Emits {"type": "state"}, {"type": "transcript"}, {"type": "audio"}, {"type": "done"},
    plus {"type": "activity", "phase": "start"|"result", "tool": "<name>"} frames
    during brain tool turns (name+phase only — args/results never cross the wire)
    so the touch panel's live-activity strip can show what Zoe is doing.
    """
    # CSWSH guard. NOTE: guest authentication for this endpoint is intentionally
    # OUT OF SCOPE here (a separate threat-model decision about LAN-kiosk guest
    # voice); this only closes the cross-web-origin vector. The native kiosk
    # sends no Origin header and is unaffected.
    if not await _enforce_ws_origin(websocket):
        return
    await websocket.accept()
    ws_session_id = session_id or f"ws-voice-{_uuid_mod.uuid4().hex[:8]}"
    user_id = await _resolve_ws_user(session_id)

    await websocket.send_json({"type": "state", "state": "ambient"})

    # Mutable cancel flag — text handler sets it; streaming pipeline checks it
    # between sentence boundaries. True mid-stream cancel requires task refactor (future).
    _ws_cancelled: list[bool] = [False]
    skybridge_context: dict = {}

    try:
        while True:
            message_text: str | None = None
            try:
                raw = await websocket.receive()
            except WebSocketDisconnect:
                break

            if raw.get("type") == "websocket.disconnect":
                break

            if raw.get("bytes"):
                # Binary audio chunk: detect format from magic bytes, write temp file, transcribe.
                # Browser MediaRecorder always sends WebM/opus (magic: \x1a\x45\xdf\xa3).
                # Preserve the real container suffix; WebM/opus must not be mislabeled as WAV.
                audio_bytes: bytes = raw["bytes"]
                await websocket.send_json({"type": "state", "state": "thinking"})
                try:
                    import tempfile, os as _os
                    from routers.voice_tts import _transcribe_audio
                    _magic = audio_bytes[:4] if len(audio_bytes) >= 4 else b""
                    _suffix = ".wav" if _magic == b"RIFF" else ".webm"
                    with tempfile.NamedTemporaryFile(suffix=_suffix, delete=False) as tf:
                        tf.write(audio_bytes)
                        tmp_path = tf.name
                    try:
                        message_text = await _transcribe_audio(tmp_path)
                    finally:
                        try:
                            _os.unlink(tmp_path)
                        except OSError:
                            pass
                    if message_text:
                        await websocket.send_json({"type": "transcript", "role": "user", "text": message_text})
                except Exception as _exc:
                    logger.warning("Voice WS audio transcription failed: %s", _exc)
                    await websocket.send_json({"type": "state", "state": "ambient"})
                    continue

            elif raw.get("text"):
                text_data: str = raw["text"]
                if text_data == "ping":
                    await websocket.send_json({"type": "pong"})
                    continue
                msg = None
                try:
                    msg = __import__("json").loads(text_data)
                except ValueError:
                    msg = None
                if isinstance(msg, dict):
                    if is_wake_payload(msg):
                        for event in wake_ack_events():
                            await websocket.send_json(event)
                        continue
                    if msg.get("type") == "text":
                        message_text = msg.get("message", "").strip()
                    elif msg.get("type") == "cancel":
                        _ws_cancelled[0] = True
                        continue
                else:
                    if is_wake_text(text_data):
                        for event in wake_ack_events():
                            await websocket.send_json(event)
                        continue

            if not message_text:
                continue

            # ── "Let's talk" conversation-session opener (flag-gated) ──────────
            # Mirrors the wake-ack early-return above: a session opener is
            # acknowledged instantly with a warm line and NEVER enters the
            # skybridge/intent/brain lanes. On a hit the LiveKit room is warmed
            # fire-and-forget and the final "done" event carries
            # conversation_mode=true so the panel can switch to the continuous
            # LiveKit mode. DEFAULT OFF via ZOE_CONVERSATION_OPENER_ENABLED
            # (checked per call inside maybe_conversation_opener).
            _co_ack = None
            try:
                from conversation_opener import maybe_conversation_opener
                _co_ack = maybe_conversation_opener(message_text)
            except Exception as _co_exc:
                logger.debug("conversation opener fast-path skipped: %s", _co_exc)
                _co_ack = None
            if _co_ack:
                import base64 as _b64co
                await websocket.send_json({"type": "state", "state": "responding"})
                await websocket.send_json(
                    {"type": "transcript", "role": "zoe", "text": _co_ack["phrase"]}
                )
                try:
                    from routers.voice_tts import _synthesize_kokoro_sidecar as _co_tts
                    _co_wav = await _co_tts(_co_ack["phrase"])
                    if _co_wav:
                        await websocket.send_json({
                            "type": "audio",
                            "audio_base64": _b64co.b64encode(_co_wav).decode("ascii"),
                            "content_type": "audio/wav",
                        })
                except Exception as _co_tts_exc:
                    logger.debug(
                        "conversation opener TTS failed (text already sent): %s",
                        _co_tts_exc,
                    )
                await websocket.send_json({"type": "state", "state": "ambient"})
                await websocket.send_json({"type": "done", "conversation_mode": True})
                continue

            # Reset cancel flag at the start of each new pipeline
            _ws_cancelled[0] = False

            await websocket.send_json({"type": "state", "state": "thinking"})
            skybridge_result = await _resolve_voice_cards(message_text, user_id, context=skybridge_context)
            if skybridge_result.get("handled"):
                skybridge_context = skybridge_result.get("skybridge_context") or skybridge_context
                await websocket.send_json({"type": "cards", "result": skybridge_result})
                await websocket.send_json({"type": "skybridge_context", "context": skybridge_context})
                spoken_summary = str(skybridge_result.get("spoken_summary") or "").strip()
                if spoken_summary:
                    await websocket.send_json({"type": "transcript", "role": "assistant", "text": spoken_summary})
                await websocket.send_json({"type": "state", "state": "ambient"})
                await websocket.send_json({"type": "done"})
                continue
            skybridge_context = {}

            # ── Instant intent fast-path ────────────────────────────────────────
            # Side-effect-free commands the card path doesn't cover (calc, clock,
            # greeting, acknowledgement, presence check) — answer + speak without
            # waking the ~2-4s brain. Restricted to READ-ONLY intents so it can
            # never double-execute a write the skybridge path already handled.
            #
            # Detect+execute is wrapped so a FAILURE THERE (before any send) falls
            # through to the brain. But once we have a reply and start sending, we
            # are committed: TTS is best-effort and we never fall through (avoids a
            # duplicate transcript if the brain path also ran).
            _fp_reply = None
            try:
                from intent_router import detect_intent as _fp_detect, execute_intent as _fp_exec
                _fp_intent = _fp_detect(message_text, log_miss=False, user_id=user_id)
                if _fp_intent is not None and _fp_intent.name in _VOICE_INSTANT_INTENTS:
                    _fp_reply = await _fp_exec(_fp_intent, user_id=user_id)
            except Exception as _fp_exc:
                logger.debug("voice instant fast-path detect/execute skipped: %s", _fp_exc)
                _fp_reply = None
            if _fp_reply:
                import base64 as _b64fp
                await websocket.send_json({"type": "state", "state": "responding"})
                await websocket.send_json({"type": "transcript", "role": "zoe", "text": _fp_reply})
                try:
                    from routers.voice_tts import _synthesize_kokoro_sidecar as _fp_tts
                    _fp_wav = await _fp_tts(_fp_reply)
                    if _fp_wav:
                        await websocket.send_json({
                            "type": "audio",
                            "audio_base64": _b64fp.b64encode(_fp_wav).decode("ascii"),
                            "content_type": "audio/wav",
                        })
                except Exception as _fp_tts_exc:
                    logger.debug("voice fast-path TTS failed (text already sent): %s", _fp_tts_exc)
                await websocket.send_json({"type": "state", "state": "ambient"})
                await websocket.send_json({"type": "done"})
                continue

            # ── Streaming LLM + per-sentence TTS ────────────────────────────────
            # Track LLM output and TTS output separately so fallback only re-runs
            # what actually failed (avoids double-transcript if LLM works but TTS is down).
            _stream_llm_reply = ""   # filled once LLM streaming completes
            _stream_tts_ok = False   # True if at least one audio chunk was sent
            try:
                import base64 as _b64
                from brain_dispatch import brain_streaming  # zoe-core by default
                from routers.voice_tts import (  # type: ignore
                    _extract_complete_sentences,
                    _forward_voice_activity,
                    _synthesize_kokoro_sidecar,
                )

                token_buf = ""
                full_reply: list[str] = []
                tts_started = False
                # id→name map for this turn so a result sentinel (which often
                # omits the tool name) closes under the tool that started.
                _activity_tools: dict = {}

                async for delta in brain_streaming(
                    message_text, ws_session_id, user_id=user_id, voice_mode=True
                ):
                    if _ws_cancelled[0]:
                        break
                    # Brain "what I'm doing" sentinels (__TOOL__/__THINKING__) ride
                    # alongside the spoken stream — never buffer them toward TTS.
                    # Tool start/result phases are forwarded to the panel as
                    # {"type":"activity","phase":...,"tool":...} frames (name+phase
                    # only; args/results never cross the wire) so the touch panel's
                    # live-activity strip can show what Zoe is doing mid-turn.
                    if await _forward_voice_activity(delta, websocket.send_json, _activity_tools):
                        continue
                    token_buf += delta
                    sentences, token_buf = _extract_complete_sentences(token_buf)
                    for sentence in sentences:
                        if _ws_cancelled[0]:
                            break
                        full_reply.append(sentence)
                        if not tts_started:
                            await websocket.send_json({"type": "state", "state": "responding"})
                            tts_started = True
                        wav = await _synthesize_kokoro_sidecar(sentence)
                        if wav:
                            _stream_tts_ok = True
                            await websocket.send_json({
                                "type": "audio",
                                "audio_base64": _b64.b64encode(wav).decode("ascii"),
                                "content_type": "audio/wav",
                            })

                # Flush any remaining text that didn't end with punctuation
                if not _ws_cancelled[0] and token_buf.strip():
                    full_reply.append(token_buf.strip())
                    if not tts_started:
                        await websocket.send_json({"type": "state", "state": "responding"})
                    wav = await _synthesize_kokoro_sidecar(token_buf.strip())
                    if wav:
                        _stream_tts_ok = True
                        await websocket.send_json({
                            "type": "audio",
                            "audio_base64": _b64.b64encode(wav).decode("ascii"),
                            "content_type": "audio/wav",
                        })

                _stream_llm_reply = " ".join(p.strip() for p in full_reply if p.strip())
                if _stream_llm_reply:
                    await websocket.send_json({"type": "transcript", "role": "zoe", "text": _stream_llm_reply})

            except Exception as _stream_exc:
                logger.error("Voice WS streaming error: %s", _stream_exc, exc_info=True)

            if not _ws_cancelled[0]:
                if not _stream_llm_reply:
                    # LLM streaming failed entirely → full single-shot fallback
                    try:
                        import base64 as _b64
                        from brain_dispatch import brain_oneshot  # zoe-core by default
                        from routers.voice_tts import synthesize as _synth  # type: ignore
                        _fallback_response = await brain_oneshot(
                            message_text, ws_session_id, user_id, voice_mode=True
                        )
                        await websocket.send_json({"type": "state", "state": "responding"})
                        await websocket.send_json({"type": "transcript", "role": "zoe", "text": _fallback_response})
                        try:
                            _tts_resp = await _synth({"text": _fallback_response}, caller={"source": "ws", "user_id": user_id})
                            await websocket.send_json({
                                "type": "audio",
                                "audio_base64": _b64.b64encode(_tts_resp.body).decode("ascii"),
                                "content_type": _tts_resp.media_type,
                                "text": _fallback_response,
                            })
                        except Exception as _tts_exc:
                            logger.warning("Voice WS TTS fallback failed: %s", _tts_exc)
                            await websocket.send_json({"type": "text", "content": _fallback_response})
                        _stream_llm_reply = _fallback_response  # one reply var for the compose hook
                    except Exception as _fallback_exc:
                        logger.error("Voice WS fallback error: %s", _fallback_exc)

                elif not _stream_tts_ok:
                    # LLM worked but Kokoro TTS was down → try synthesize (has Edge TTS fallback)
                    try:
                        import base64 as _b64
                        from routers.voice_tts import synthesize as _synth  # type: ignore
                        _tts_resp = await _synth({"text": _stream_llm_reply}, caller={"source": "ws", "user_id": user_id})
                        await websocket.send_json({
                            "type": "audio",
                            "audio_base64": _b64.b64encode(_tts_resp.body).decode("ascii"),
                            "content_type": _tts_resp.media_type,
                        })
                    except Exception as _tts_exc:
                        logger.warning("Voice WS TTS synthesize fallback failed: %s", _tts_exc)

            # ── Generative UI on the panel (flag-gated): all audio frames are
            # already sent (playback is client-side), so composing here cannot
            # delay speech. The panel renders via the deployed `compose` entry.
            if not _ws_cancelled[0] and _stream_llm_reply:
                _cframe = await _voice_compose_cards_frame(message_text, _stream_llm_reply, user_id)
                # Re-check cancel AFTER the compose window (a cancel can arrive
                # during the up-to-8s compose await; the card must not land on a
                # turn the user already cancelled).
                if _cframe and not _ws_cancelled[0]:
                    await websocket.send_json(_cframe)

            await websocket.send_json({"type": "done"})
            await websocket.send_json({"type": "state", "state": "ambient"})

    except Exception as _exc:
        logger.warning("Voice WS closed: %s", _exc)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
