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

import time

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

# Effect keys summed per loop (see memory_digest.run_memory_digest /
# run_weekly_consolidation). Unknown/missing keys are simply skipped.
_DIGEST_EFFECT_KEYS = ("extracted", "new", "superseded", "skipped_duplicates")
_CONSOLIDATION_EFFECT_KEYS = ("merged", "resolved_contradictions", "archived")

# Expected cadence + grace. Digest is nightly (~03:00); consolidation is
# weekly (Sunday 04:00). A run older than this — or one that never happened —
# reads as stale so a silently-broken loop surfaces.
_DIGEST_MAX_AGE_S = 26 * 3600
_CONSOLIDATION_MAX_AGE_S = 8 * 86400

# In-process last-run state for the human-queryable health endpoint. Gauges
# cover Prometheus; this covers the "what's the age of the last run" question
# without external time math.
_LAST_RUN: dict[str, dict] = {}


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
    Returns a summary dict (``timestamp``, ``users``, ``effects``) so callers
    can log a single enriched run-complete line.
    """
    now_ts = time.time() if now is None else now
    users = len(results) if results is not None else 0
    effects = _sum_effects(results, keys)

    ts_gauge.set(now_ts)
    users_gauge.set(users)
    for effect, value in effects.items():
        effects_gauge.labels(effect=effect).set(value)

    summary = {"timestamp": now_ts, "users": users, "effects": effects}
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
    """Queryable last-run + staleness for both memory-maintenance loops.

    A loop that never ran this process (or ran longer ago than its cadence
    grace) reports ``stale: True`` — that's the signal a nightly/Sunday pass
    was missed. ``age_seconds`` is the age of the last successful run.
    """
    now_ts = time.time() if now is None else now
    out: dict[str, dict] = {}
    for loop, max_age in (
        ("digest", _DIGEST_MAX_AGE_S),
        ("consolidation", _CONSOLIDATION_MAX_AGE_S),
    ):
        info = _LAST_RUN.get(loop)
        if not info:
            out[loop] = {
                "ever_ran": False,
                "last_run_ts": None,
                "age_seconds": None,
                "stale": True,
                "max_age_seconds": max_age,
                "users": None,
                "effects": None,
            }
            continue
        age = max(0.0, now_ts - info["timestamp"])
        out[loop] = {
            "ever_ran": True,
            "last_run_ts": info["timestamp"],
            "age_seconds": age,
            "stale": age > max_age,
            "max_age_seconds": max_age,
            "users": info["users"],
            "effects": info["effects"],
        }
    return out


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
    "record_digest_run",
    "record_consolidation_run",
    "memory_loop_status",
    "snapshot_collection_sizes",
]
