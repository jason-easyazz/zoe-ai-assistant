"""Prometheus metrics for Zoe memory + self-learning.

Exposed via `/metrics` on the FastAPI app (see `main.py`). Tests can reset the
global registry using `reset_for_test()` to avoid "duplicated timeseries"
errors across test reloads.

Every metric name is prefixed with `zoe_` so they filter cleanly out of the
default Python/process metrics exposed by `prometheus_client.REGISTRY`.

Baseline counters are created up-front so they appear in scrapes even with
zero observations - that lets dashboards render without waiting for a first
event.
"""

from __future__ import annotations

import os
import time
from collections import deque

from prometheus_client import Counter, Gauge, Histogram, CollectorRegistry


# Dedicated registry so we can reset / wipe cleanly without touching the
# process-wide default registry.
REGISTRY = CollectorRegistry(auto_describe=True)


# Memory ingest / read
memory_write_count = Counter(
    "zoe_memory_write_count",
    "MemoryService ingest attempts, labelled by source and outcome.",
    ["source", "status"],
    registry=REGISTRY,
)
memory_search_latency_ms = Histogram(
    "zoe_memory_search_latency_ms",
    "MemPalace semantic search latency (ms).",
    buckets=(5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000),
    registry=REGISTRY,
)
memory_search_hit_count = Histogram(
    "zoe_memory_search_hits",
    "Number of results returned from a MemPalace search.",
    buckets=(0, 1, 2, 5, 10, 20, 50),
    registry=REGISTRY,
)
memory_dedup_skip_count = Counter(
    "zoe_memory_dedup_skip_count",
    "Ingest calls skipped because an identical-or-near-identical fact exists.",
    registry=REGISTRY,
)
memory_pii_reject_count = Counter(
    "zoe_memory_pii_reject_count",
    "Ingest calls rejected by the PII scrubber, labelled by the pattern that matched.",
    ["pattern"],
    registry=REGISTRY,
)
memory_contradiction_count = Counter(
    "zoe_memory_contradiction_count",
    "Facts superseded by the nightly contradiction pass.",
    registry=REGISTRY,
)
memory_quality_reject_count = Counter(
    "zoe_memory_quality_reject_count",
    "Conversational write candidates rejected by the write-quality gate, "
    "labelled by source and the reason that matched.",
    ["source", "reason"],
    registry=REGISTRY,
)
memory_async_extract_fail_count = Counter(
    "zoe_memory_async_extract_fail_count",
    "Async post-turn memory extractor passes that raised (facts possibly lost "
    "while the turn reply already claimed success), labelled by lane "
    "(chat|voice) and the pass that failed.",
    ["lane", "pass_name"],
    registry=REGISTRY,
)
memory_supersede_count = Counter(
    "zoe_memory_supersede_count",
    "Conversational writes that updated/superseded an existing same-attribute "
    "fact instead of adding a near-duplicate, labelled by source.",
    ["source"],
    registry=REGISTRY,
)
agent_prompt_fact_count = Histogram(
    "zoe_agent_prompt_facts",
    "Number of memory facts injected into the agent system prompt per turn.",
    buckets=(0, 1, 2, 3, 5, 10, 20, 50),
    registry=REGISTRY,
)
mempalace_collection_size = Gauge(
    "zoe_mempalace_collection_size",
    "Number of MemPalace rows per user_id (approximate; refreshed on scrape).",
    ["user_id"],
    registry=REGISTRY,
)

# DB connection pool (asyncpg) — updated by db_pool on each acquire/release so
# ops can see exhaustion building (the 2026-07-12 outage was invisible: pool
# drained to 0 free while /health stayed 200). Alert on zoe_db_pool_free == 0.
db_pool_size = Gauge(
    "zoe_db_pool_size",
    "Current number of connections held by the asyncpg pool (open, in-use + idle).",
    registry=REGISTRY,
)
db_pool_in_use = Gauge(
    "zoe_db_pool_in_use",
    "Pooled connections currently checked out (acquired, not yet released).",
    registry=REGISTRY,
)
db_pool_free = Gauge(
    "zoe_db_pool_free",
    "Pooled connections currently idle and available for acquire.",
    registry=REGISTRY,
)

# Reconciliation fail-open-to-ADD observability (QA concern #4). The shared
# reconcile_for_ingest chokepoint (#1280) all conversational writers route
# through PREFERS DUPLICATES OVER LOST FACTS: on a search timeout / empty result
# it stores the fact as ADD without a supersession check. That is a deliberate
# tradeoff, but under sustained load it becomes a duplicate factory — and the
# WARNING it logged needed a watcher (the standing lesson: any mandatory
# loop/gate must emit a heartbeat something checks). This counter + sliding
# window make the tradeoff visible WITHOUT changing it. Alert on
# `increase(zoe_memory_reconcile_failopen_count[5m])`.
memory_reconcile_failopen_count = Counter(
    "zoe_memory_reconcile_failopen_count",
    "reconcile_for_ingest fail-open events: a fact stored as ADD WITHOUT a "
    "supersession check because search timed out / returned empty / errored. "
    "Labelled by cause (search_timeout | empty_results | search_error).",
    ["cause"],
    registry=REGISTRY,
)

# Sliding-window watcher for the fail-open rate. A few fail-opens are normal (a
# cold store, a genuinely new fact); a SUSTAINED burst means search is failing
# and reconciliation is silently duplicating every write. `sustained` trips when
# the window count reaches the threshold — the human-queryable mirror of the
# PromQL alert, and the signal the caller uses to escalate WARNING → ERROR.
_RECONCILE_FAILOPEN_WINDOW_S = 300.0
_RECONCILE_FAILOPEN_THRESHOLD = 20
_RECONCILE_FAILOPEN_EVENTS: deque[float] = deque()


def reconcile_failopen_status(now: float | None = None) -> dict:
    """Fail-open-to-ADD rate over the recent window + a ``sustained`` trip flag.

    Prunes events older than the window, then reports how many fail-open ADDs
    landed inside it. ``sustained: True`` (count >= threshold) is the signal
    that reconciliation has become a duplicate factory — alert on it. The
    Prometheus equivalent is ``increase(zoe_memory_reconcile_failopen_count[5m])``.
    """
    now_ts = time.time() if now is None else now
    cutoff = now_ts - _RECONCILE_FAILOPEN_WINDOW_S
    while _RECONCILE_FAILOPEN_EVENTS and _RECONCILE_FAILOPEN_EVENTS[0] < cutoff:
        _RECONCILE_FAILOPEN_EVENTS.popleft()
    count = len(_RECONCILE_FAILOPEN_EVENTS)
    return {
        "window_seconds": _RECONCILE_FAILOPEN_WINDOW_S,
        "count": count,
        "threshold": _RECONCILE_FAILOPEN_THRESHOLD,
        "sustained": count >= _RECONCILE_FAILOPEN_THRESHOLD,
    }


def record_reconcile_failopen(cause: str, *, now: float | None = None) -> dict:
    """Count one reconcile fail-open-to-ADD and return the current window status.

    Increments the Prometheus counter (labelled by ``cause``) and appends to the
    sliding window backing :func:`reconcile_failopen_status`. Returns that status
    so the caller can escalate its log level when the rate is sustained. Never
    raises — observability must never break the memory write path.
    """
    now_ts = time.time() if now is None else now
    try:
        memory_reconcile_failopen_count.labels(cause=cause).inc()
    except Exception:  # pragma: no cover - metrics must never break the write
        pass
    _RECONCILE_FAILOPEN_EVENTS.append(now_ts)
    return reconcile_failopen_status(now=now_ts)


# Self-learning / feedback
chat_feedback_count = Counter(
    "zoe_chat_feedback_count",
    "Chat feedback events submitted by users.",
    ["kind"],
    registry=REGISTRY,
)
training_last_success_timestamp = Gauge(
    "zoe_training_last_success_timestamp_seconds",
    "Unix seconds of the last nightly training run that passed the eval gate.",
    registry=REGISTRY,
)
training_last_eval_accuracy = Gauge(
    "zoe_training_last_eval_accuracy",
    "Tool-selection accuracy (0..1) from the most recent training eval run.",
    registry=REGISTRY,
)

# Routing / intent
routing_decision_count = Counter(
    "zoe_routing_decision_count",
    "Chat turn routing decisions, labelled by intent family and chosen model.",
    ["intent", "model"],
    registry=REGISTRY,
)

# Digest
digest_messages_processed = Counter(
    "zoe_digest_messages_processed",
    "Chat messages consumed by the nightly digest, per user.",
    ["user_id"],
    registry=REGISTRY,
)
digest_facts_extracted = Counter(
    "zoe_digest_facts_extracted",
    "Facts extracted by the nightly digest, per user and outcome.",
    ["user_id", "status"],
    registry=REGISTRY,
)


# ── Memory-maintenance loop observability ────────────────────────────────────
# The two in-process loops (nightly digest, weekly consolidation) failed
# silently in production for weeks because nothing recorded whether they ran.
# These gauges expose a last-run timestamp + aggregate effect counts so a
# missed nightly/Sunday pass is detectable (Prometheus: alert on
# `time() - zoe_memory_*_last_run_timestamp_seconds` exceeding the cadence).
memory_digest_last_run_timestamp = Gauge(
    "zoe_memory_digest_last_run_timestamp_seconds",
    "Unix seconds of the last completed nightly memory digest loop run.",
    registry=REGISTRY,
)
memory_digest_last_run_users = Gauge(
    "zoe_memory_digest_last_run_users",
    "Users processed by the last completed nightly memory digest run.",
    registry=REGISTRY,
)
memory_digest_last_run_effects = Gauge(
    "zoe_memory_digest_last_run_effects",
    "Summed per-effect counts from the last nightly memory digest run.",
    ["effect"],
    registry=REGISTRY,
)
memory_consolidation_last_run_timestamp = Gauge(
    "zoe_memory_consolidation_last_run_timestamp_seconds",
    "Unix seconds of the last completed weekly memory consolidation loop run.",
    registry=REGISTRY,
)
memory_consolidation_last_run_users = Gauge(
    "zoe_memory_consolidation_last_run_users",
    "Users processed by the last completed weekly memory consolidation run.",
    registry=REGISTRY,
)
memory_consolidation_last_run_effects = Gauge(
    "zoe_memory_consolidation_last_run_effects",
    "Summed per-effect counts from the last weekly memory consolidation run.",
    ["effect"],
    registry=REGISTRY,
)

# ── Effect-count heartbeat + zero-effect alerting ────────────────────────────
# The staleness signal above only catches a MISSED run. It does not catch the
# failure that actually happened twice: a loop that runs exactly on schedule,
# logs "run complete", and does NOTHING (#1217 timezone cast, #1480 ten
# consecutive nights of zero users). A heartbeat must carry what the run DID.
# These gauges carry the scalar effect count per run and the consecutive
# zero-effect streak, so "ran 10 times, did nothing 10 times" is visible
# instead of a green tick. Alert on `zoe_memory_loop_zero_effect_alert == 1`.
memory_loop_last_run_effect_count = Gauge(
    "zoe_memory_loop_last_run_effect_count",
    "Total effects (sum of the loop's effect keys) produced by its last run. "
    "Zero means the run did no work at all.",
    ["loop"],
    registry=REGISTRY,
)
memory_loop_zero_effect_streak = Gauge(
    "zoe_memory_loop_zero_effect_streak",
    "Consecutive completed runs of this loop that produced zero effects.",
    ["loop"],
    registry=REGISTRY,
)
memory_loop_zero_effect_alert = Gauge(
    "zoe_memory_loop_zero_effect_alert",
    "1 when the loop's zero-effect streak has reached its alert threshold "
    "(the loop is running but doing nothing); 0 otherwise.",
    ["loop"],
    registry=REGISTRY,
)

# Effect keys summed per loop (see memory_digest.run_memory_digest /
# run_weekly_consolidation). Unknown/missing keys are simply skipped.
_DIGEST_EFFECT_KEYS = ("extracted", "new", "superseded", "skipped_duplicates")
_CONSOLIDATION_EFFECT_KEYS = ("merged", "resolved_contradictions", "archived")

# Expected cadence + grace. Digest is nightly (~03:00); consolidation is
# weekly (Sunday 04:00). A run older than this — or one that never happened —
# reads as stale so a silently-broken loop surfaces.
_DIGEST_MAX_AGE_S = 26 * 3600
_CONSOLIDATION_MAX_AGE_S = 8 * 86400

# How many consecutive zero-effect runs raise the alert, for loops that are NOT
# idle-tolerant. Default 5: a household that talks to Zoe can plausibly have a
# quiet night or two, but five nights of literally zero extracted/new/
# superseded/skipped facts is a broken loop, not a quiet week. Override with
# ZOE_MEMORY_LOOP_ZERO_EFFECT_RUNS; a value < 1 disables zero-effect alerting.
_ZERO_EFFECT_RUNS_DEFAULT = 5

# Loops that legitimately have nothing to do on a normal run DECLARE it here,
# so they cannot cry wolf. Weekly consolidation only fires when there are
# near-duplicates to merge, contradictions to resolve, or stale rows to
# archive; on a well-maintained store the honest answer is "nothing to do" for
# many weeks running. Its missed-run staleness check (8d grace) still covers a
# consolidation loop that dies. The nightly digest is deliberately NOT
# idle-tolerant — if Zoe was used at all there are messages to digest.
_IDLE_TOLERANT_LOOPS = frozenset({"consolidation"})

# In-process last-run state for the human-queryable health endpoint. Gauges
# cover Prometheus; this covers the "what's the age of the last run" question
# without external time math. Streaks are per-process and reset on restart —
# a restart therefore forgives the streak but not the staleness clock.
_LAST_RUN: dict[str, dict] = {}
_ZERO_EFFECT_STREAK: dict[str, int] = {}


def zero_effect_threshold(loop: str) -> int | None:
    """Consecutive zero-effect runs that raise an alert for ``loop``.

    ``None`` means zero-effect alerting is off for this loop: either the loop
    declared itself idle-tolerant (see ``_IDLE_TOLERANT_LOOPS``) or the
    operator disabled alerting with a ``< 1`` env value. Never raises — a
    malformed env value falls back to the default rather than breaking the
    status surface.
    """
    if loop in _IDLE_TOLERANT_LOOPS:
        return None
    # Literal env name (not a constant) so tools/audit/flag_inventory.py can
    # statically extract this flag.
    raw = os.environ.get("ZOE_MEMORY_LOOP_ZERO_EFFECT_RUNS")
    threshold = _ZERO_EFFECT_RUNS_DEFAULT
    if raw is not None:
        try:
            threshold = int(str(raw).strip())
        except (TypeError, ValueError):
            threshold = _ZERO_EFFECT_RUNS_DEFAULT
    return threshold if threshold >= 1 else None


def _sum_effects(results, keys) -> dict[str, int]:
    """Sum integer effect counts across per-user result dicts.

    Booleans are ignored (a stray ``True`` must not read as 1); only real
    numeric counts contribute. Non-dict rows are skipped defensively so a
    malformed result never breaks recording.
    """
    totals = {k: 0 for k in keys}
    for row in results or []:
        if not isinstance(row, dict):
            continue
        for k in keys:
            v = row.get(k)
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                totals[k] += int(v)
    return totals


def _record_loop_run(
    loop: str,
    results,
    *,
    keys,
    ts_gauge: Gauge,
    users_gauge: Gauge,
    effects_gauge: Gauge,
    now: float | None = None,
) -> dict:
    """Persist last-run timestamp + aggregate counts for a memory loop.

    Sets the Prometheus gauges and updates the in-process ``_LAST_RUN`` state.
    Returns a summary dict (``timestamp``, ``users``, ``effects``,
    ``effect_count``, ``zero_effect_streak``, ``zero_effect_alert``) so callers
    can log a single enriched run-complete line that says what the run DID, not
    merely that it ran.
    """
    now_ts = time.time() if now is None else now
    users = len(results) if results is not None else 0
    effects = _sum_effects(results, keys)
    effect_count = sum(effects.values())

    # A run that produced nothing extends the streak; any real effect clears it.
    streak = _ZERO_EFFECT_STREAK.get(loop, 0) + 1 if effect_count == 0 else 0
    _ZERO_EFFECT_STREAK[loop] = streak
    threshold = zero_effect_threshold(loop)
    alert = threshold is not None and streak >= threshold

    ts_gauge.set(now_ts)
    users_gauge.set(users)
    for effect, value in effects.items():
        effects_gauge.labels(effect=effect).set(value)
    memory_loop_last_run_effect_count.labels(loop=loop).set(effect_count)
    memory_loop_zero_effect_streak.labels(loop=loop).set(streak)
    memory_loop_zero_effect_alert.labels(loop=loop).set(1 if alert else 0)

    summary = {
        "timestamp": now_ts,
        "users": users,
        "effects": effects,
        "effect_count": effect_count,
        "zero_effect_streak": streak,
        "zero_effect_alert_after": threshold,
        "zero_effect_alert": alert,
    }
    _LAST_RUN[loop] = summary
    return summary


def record_digest_run(results, *, now: float | None = None) -> dict:
    """Record a completed nightly digest run (timestamp + summed effects)."""
    return _record_loop_run(
        "digest",
        results,
        keys=_DIGEST_EFFECT_KEYS,
        ts_gauge=memory_digest_last_run_timestamp,
        users_gauge=memory_digest_last_run_users,
        effects_gauge=memory_digest_last_run_effects,
        now=now,
    )


def record_consolidation_run(results, *, now: float | None = None) -> dict:
    """Record a completed weekly consolidation run (timestamp + summed effects)."""
    return _record_loop_run(
        "consolidation",
        results,
        keys=_CONSOLIDATION_EFFECT_KEYS,
        ts_gauge=memory_consolidation_last_run_timestamp,
        users_gauge=memory_consolidation_last_run_users,
        effects_gauge=memory_consolidation_last_run_effects,
        now=now,
    )


def memory_loop_status(now: float | None = None) -> dict:
    """Queryable last-run, staleness AND zero-effect state for both loops.

    Two independent unhealthy signals, because they catch different failures:

    * ``stale: True`` — a loop that never ran this process, or ran longer ago
      than its cadence grace. This is the MISSED-run signal.
    * ``zero_effect_alert: True`` — the loop ran on schedule
      ``zero_effect_streak`` times in a row and produced zero effects each
      time. This is the DID-NOTHING signal, and it is the one that was missing
      when the nightly digest processed zero users for ten straight nights
      while logging "nightly run complete".

    ``healthy`` is the flat "is this loop fine" answer (not stale and not
    alerting) so a human glancing at the surface does not have to reason about
    two flags. Idle-tolerant loops report ``zero_effect_alert_after: None`` and
    never raise the zero-effect alert; their streak is still reported.
    """
    now_ts = time.time() if now is None else now
    out: dict[str, dict] = {}
    for loop, max_age in (
        ("digest", _DIGEST_MAX_AGE_S),
        ("consolidation", _CONSOLIDATION_MAX_AGE_S),
    ):
        threshold = zero_effect_threshold(loop)
        streak = _ZERO_EFFECT_STREAK.get(loop, 0)
        # Recomputed here (not read from the recorded run) so an operator
        # retuning the threshold sees the effect without waiting for a run.
        alert = threshold is not None and streak >= threshold
        info = _LAST_RUN.get(loop)
        common = {
            "idle_tolerant": loop in _IDLE_TOLERANT_LOOPS,
            "zero_effect_streak": streak,
            "zero_effect_alert_after": threshold,
            "zero_effect_alert": alert,
        }
        if not info:
            out[loop] = {
                "ever_ran": False,
                "last_run_ts": None,
                "age_seconds": None,
                "stale": True,
                "max_age_seconds": max_age,
                "users": None,
                "effects": None,
                "effect_count": None,
                "healthy": False,
                **common,
            }
            continue
        age = max(0.0, now_ts - info["timestamp"])
        stale = age > max_age
        out[loop] = {
            "ever_ran": True,
            "last_run_ts": info["timestamp"],
            "age_seconds": age,
            "stale": stale,
            "max_age_seconds": max_age,
            "users": info["users"],
            "effects": info["effects"],
            "effect_count": info.get("effect_count"),
            "healthy": not stale and not alert,
            **common,
        }
    return out


def memory_loop_health(now: float | None = None) -> dict:
    """Roll the per-loop status up into one glanceable healthy/alert verdict.

    Returns ``{"loops": ..., "healthy": bool, "alerts": [str, ...]}``. The
    alert strings are written to be read by a human at 2am — e.g.
    ``"digest: ran 10 times in a row and did nothing each time (threshold 5)"``
    — because the whole point of this surface is that "ran, did nothing" must
    not look like a green tick.
    """
    loops = memory_loop_status(now=now)
    alerts: list[str] = []
    for loop, info in loops.items():
        if info["zero_effect_alert"]:
            alerts.append(
                f"{loop}: ran {info['zero_effect_streak']} times in a row and did "
                f"nothing each time (threshold {info['zero_effect_alert_after']})"
            )
        if info["stale"]:
            if not info["ever_ran"]:
                alerts.append(f"{loop}: has never run in this process")
            else:
                alerts.append(
                    f"{loop}: last run was {info['age_seconds'] / 3600:.1f}h ago "
                    f"(grace {info['max_age_seconds'] / 3600:.0f}h)"
                )
    return {
        "loops": loops,
        "healthy": all(info["healthy"] for info in loops.values()),
        "alerts": alerts,
    }


async def snapshot_collection_sizes(timeout_s: float = 2.0) -> None:
    """Best-effort refresh of per-user MemPalace size gauge.

    Called by the `/metrics` endpoint handler before generating the scrape
    output so dashboards don't show stale values. Routed through the
    MemoryService facade (no direct ChromaDB client) so we reuse the
    singleton and respect the same backend abstraction as all other
    memory access. The collection scan stays off the event loop via the
    service's executor-backed async wrapper, and is time-bounded so metrics
    scrapes cannot stall chat/voice. Any failure is swallowed - metrics must
    never break the request path.
    """
    try:
        import asyncio
        from memory_service import get_memory_service

        svc = get_memory_service()
        counts = await asyncio.wait_for(svc.collection_sizes_by_user(), timeout=timeout_s)

        mempalace_collection_size.clear()
        for uid, n in counts.items():
            mempalace_collection_size.labels(user_id=uid).set(n)
    except Exception:
        mempalace_collection_size.clear()


__all__ = [
    "REGISTRY",
    "memory_write_count",
    "memory_search_latency_ms",
    "memory_search_hit_count",
    "memory_dedup_skip_count",
    "memory_pii_reject_count",
    "memory_contradiction_count",
    "memory_quality_reject_count",
    "memory_supersede_count",
    "agent_prompt_fact_count",
    "mempalace_collection_size",
    "db_pool_size",
    "db_pool_in_use",
    "db_pool_free",
    "memory_reconcile_failopen_count",
    "record_reconcile_failopen",
    "reconcile_failopen_status",
    "chat_feedback_count",
    "training_last_success_timestamp",
    "training_last_eval_accuracy",
    "routing_decision_count",
    "digest_messages_processed",
    "digest_facts_extracted",
    "memory_digest_last_run_timestamp",
    "memory_digest_last_run_users",
    "memory_digest_last_run_effects",
    "memory_consolidation_last_run_timestamp",
    "memory_consolidation_last_run_users",
    "memory_consolidation_last_run_effects",
    "memory_loop_last_run_effect_count",
    "memory_loop_zero_effect_streak",
    "memory_loop_zero_effect_alert",
    "zero_effect_threshold",
    "record_digest_run",
    "record_consolidation_run",
    "memory_loop_status",
    "memory_loop_health",
    "snapshot_collection_sizes",
]
