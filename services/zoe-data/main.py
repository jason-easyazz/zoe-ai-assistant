import asyncio
import os
import time
import uuid as _uuid_mod
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from database import init_db
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
    await _run_memory_capture_startup_probe()
    asyncio.create_task(_memory_capture_retry_task(), name="memory_capture_retry")
    _openclaw_bg_task = start_openclaw_background_tasks()
    _digest_bg_task = start_memory_digest_background()
    _consolidation_bg_task = start_memory_consolidation_background()
    _zoe_update_bg_task = start_zoe_update_background_tasks()

    try:
        from routers.voice_tts import warm_faster_whisper_worker, warm_moonshine
        asyncio.create_task(warm_moonshine(), name="moonshine_warmup")
        asyncio.create_task(warm_faster_whisper_worker(), name="faster_whisper_warmup")
        logger.info("Voice STT worker warmup scheduled (moonshine + whisper)")
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
        from proactive.engine import start_proactive_engine, register_trigger
        from proactive.triggers.reminder_scan import ReminderScanTrigger
        from proactive.triggers.morning_checkin import MorningCheckInTrigger
        from proactive.triggers.evening_windown import EveningWindDownTrigger
        from proactive.triggers.openclaw_trigger import OpenClawTrigger
        from proactive.triggers.people_health import PeopleHealthTrigger
        from proactive.triggers.people_birthday import PeopleBirthdayTrigger
        register_trigger(ReminderScanTrigger())
        register_trigger(MorningCheckInTrigger())
        register_trigger(EveningWindDownTrigger())
        register_trigger(OpenClawTrigger())
        register_trigger(PeopleHealthTrigger())
        register_trigger(PeopleBirthdayTrigger())
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
    except Exception as _pe_exc:
        logger.warning("Proactive engine failed to start (non-fatal): %s", _pe_exc)

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
        try:
            _prune_interval_s = float(os.environ.get("ZOE_WORKTREE_PRUNE_INTERVAL_S", "86400") or "86400")
        except ValueError:
            logger.warning(
                "multica_poll: invalid ZOE_WORKTREE_PRUNE_INTERVAL_S=%r; using 86400",
                os.environ.get("ZOE_WORKTREE_PRUNE_INTERVAL_S"),
            )
            _prune_interval_s = 86400.0
        # Cadence when dispatch is paused. The per-issue chain reconcile below
        # (poll_ref → worktree+git ops) costs ~30s CPU and a multi-GB transient
        # allocation each pass; running it every 30s while nothing is being
        # dispatched is pure waste. Poll every 30s when active, every
        # ZOE_MULTICA_PAUSED_POLL_S (default 300s) when paused.
        try:
            _paused_poll_s = float(os.environ.get("ZOE_MULTICA_PAUSED_POLL_S", "300") or "300")
        except ValueError:
            logger.warning(
                "multica_poll: invalid ZOE_MULTICA_PAUSED_POLL_S=%r; using 300",
                os.environ.get("ZOE_MULTICA_PAUSED_POLL_S"),
            )
            _paused_poll_s = 300.0
        _ACTIVE_POLL_S = 30.0
        _pause_check_warned = False
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
                # Fast-path: auto-close stale autopilot tracker todos (no agent needed)
                stale_todos = await client.list_issues(status="todo")
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

                in_progress_issues = await client.list_issues(status="in_progress") or []
                in_review_issues = await client.list_issues(status="in_review") or []
                blocked_issues = await client.list_issues(status="blocked") or []

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
                        try:
                            _poll_timeout = float(
                                os.environ.get("ZOE_MULTICA_POLL_REF_TIMEOUT_S", "20") or "20"
                            )
                        except ValueError:
                            _poll_timeout = 20.0
                        try:
                            _stale_ip_hours = float(
                                os.environ.get("ZOE_MULTICA_STALE_IN_PROGRESS_HOURS", "6") or "6"
                            )
                        except ValueError:
                            _stale_ip_hours = 6.0
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
                        try:
                            _wh_limit = int(os.environ.get("ZOE_MULTICA_POLL_DISPATCH_LIMIT", "1") or "1")
                        except ValueError:
                            _wh_limit = 1
                        if (
                            _wh_limit > 0
                            and os.environ.get("ZOE_MULTICA_AUTO_ADMIT", "false").lower() == "true"
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
                            try:
                                _blk_budget = int(
                                    os.environ.get("ZOE_MULTICA_BLOCKED_RESUME_BUDGET", "4") or "4"
                                )
                            except ValueError:
                                _blk_budget = 4
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
                        if _wh_dispatched < _wh_limit:
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
                try:
                    _reconcile_timeout = float(
                        os.environ.get("ZOE_MULTICA_POLL_REF_TIMEOUT_S", "20") or "20"
                    )
                except ValueError:
                    _reconcile_timeout = 20.0
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://zoe.local",
        "https://zoe.the411.life",
        "http://zoe.local",
        "http://localhost",
        "http://localhost:8080",
    ],
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
app.include_router(user_profile_router)
app.include_router(dashboard_router)
app.include_router(stubs_router)
app.include_router(push_router)
app.include_router(proactive_router)
app.include_router(panel_auth_router)
app.include_router(panel_provision_router)
app.include_router(capability_matrix_router)
app.include_router(music_router)
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


@app.get("/health")
async def root_health():
    return {
        "status": "ok",
        "service": "zoe-data",
        "version": "1.0.0",
        "memory_capture": _memory_capture_health,
    }


@app.get("/api/router/classify")
async def router_classify(text: str):
    """Tier-1 semantic router test endpoint: classify an utterance into a domain.
    No auth (local test/observability); returns the full score breakdown."""
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

    snapshot_collection_sizes()
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


async def _session_can_subscribe_panel(panel_id: str, session_id: str | None) -> bool:
    """Allow browser panel sockets only for sessions bound to that panel."""
    user = await _resolve_ws_session(session_id)
    if user is None:
        return await _panel_allows_guest_push(panel_id)
    user_id = str(user.get("user_id") or "")
    role = str(user.get("role") or "").lower()
    if not user_id:
        return False
    if role in {"admin", "agent"}:
        return True
    try:
        from database import get_db

        async for db in get_db():
            cursor = await db.execute(
                "SELECT user_id FROM ui_panel_sessions WHERE panel_id = ? ORDER BY last_seen_at DESC LIMIT 1",
                (panel_id,),
            )
            row = await cursor.fetchone()
            return bool(row and str(row["user_id"]) == user_id)
    except Exception as exc:
        logger.debug("push websocket panel session validation failed: %s", exc)
        return False


async def _panel_allows_guest_push(panel_id: str) -> bool:
    """Return true when a registered active panel explicitly allows guest use."""
    try:
        from database import get_db

        async for db in get_db():
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
        # Browser WebSockets cannot send X-Device-Token. Accept a panel channel
        # subscription when the browser session is already bound to this panel.
        if not device_info and not await _session_can_subscribe_panel(panel_id, session_id):
            await websocket.close(1008, "Invalid device token")
            return
        # Panel token or bound browser session verified; subscribe to panel push.
        await broadcaster.connect_panel(websocket, panel_id)
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
    # -----------------------------------------------------------------
    # Normal data relay loop
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
            elif data.startswith("catchup:"):
                seq = int(data.split(":")[1])
                await broadcaster.catchup(websocket, seq)
    except WebSocketDisconnect:
        if panel_id:
            broadcaster.disconnect(websocket, f"panel_{panel_id}")
        else:
            broadcaster.disconnect(websocket, channel)


@app.websocket("/api/calendar/ws/{user_id}")
async def calendar_ws(websocket: WebSocket, user_id: str):
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
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "calendar")


@app.websocket("/api/lists/ws/{user_id}")
async def lists_ws_with_user(websocket: WebSocket, user_id: str):
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
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "lists")


@app.websocket("/api/lists/ws")
async def lists_ws(websocket: WebSocket):
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
    await broadcaster.connect(
        websocket, "lists", user_id=str(user.get("user_id") or "")
    )
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "lists")


@app.websocket("/api/people/ws/{user_id}")
async def people_ws(websocket: WebSocket, user_id: str):
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
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "people")


@app.websocket("/api/reminders/ws/{user_id}")
async def reminders_ws(websocket: WebSocket, user_id: str):
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
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "reminders")


@app.websocket("/api/notes/ws/{user_id}")
async def notes_ws(websocket: WebSocket, user_id: str):
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
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "notes")


@app.websocket("/api/journal/ws/{user_id}")
async def journal_ws(websocket: WebSocket, user_id: str):
    session_id = websocket.query_params.get("session_id") or websocket.headers.get("X-Session-ID")
    user = await _resolve_ws_session(session_id)
    if user is None or user.get("role") not in ("member", "admin", "agent"):
        await websocket.close(1008, "Unauthorized")
        return
    await broadcaster.connect(
        websocket, "journal", user_id=str(user.get("user_id") or user_id)
    )
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        broadcaster.disconnect(websocket, "journal")


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
    - Binary → transcribed via faster-whisper then routed as text
    Emits {"type": "state"}, {"type": "transcript"}, {"type": "audio"}, {"type": "done"}.
    """
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
                # Saving as .wav when the content is WebM causes whisper.cpp to fail.
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
                from routers.voice_tts import _extract_complete_sentences, _synthesize_kokoro_sidecar  # type: ignore

                token_buf = ""
                full_reply: list[str] = []
                tts_started = False

                async for delta in brain_streaming(
                    message_text, ws_session_id, user_id=user_id, voice_mode=True
                ):
                    if _ws_cancelled[0]:
                        break
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

            await websocket.send_json({"type": "done"})
            await websocket.send_json({"type": "state", "state": "ambient"})

    except Exception as _exc:
        logger.warning("Voice WS closed: %s", _exc)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
