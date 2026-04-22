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


def snapshot_collection_sizes() -> None:
    """Best-effort refresh of per-user MemPalace size gauge.

    Called by the `/metrics` endpoint handler before generating the scrape
    output so dashboards don't show stale values. Routed through the
    MemoryService facade (no direct ChromaDB client) so we reuse the
    singleton and respect the same backend abstraction as all other
    memory access. Any failure is swallowed - metrics must never break
    the request path.
    """
    try:
        import asyncio
        from memory_service import get_memory_service

        svc = get_memory_service()

        async def _collect() -> dict[str, int]:
            return await svc.collection_sizes_by_user()

        try:
            counts = asyncio.run(_collect())
        except RuntimeError:
            import threading
            result: dict[str, int] = {}

            def _runner() -> None:
                nonlocal result
                loop = asyncio.new_event_loop()
                try:
                    result = loop.run_until_complete(_collect())
                finally:
                    loop.close()

            t = threading.Thread(target=_runner, daemon=True)
            t.start()
            t.join(timeout=2.0)
            counts = result

        mempalace_collection_size.clear()
        for uid, n in counts.items():
            mempalace_collection_size.labels(user_id=uid).set(n)
    except Exception:
        pass


__all__ = [
    "REGISTRY",
    "memory_write_count",
    "memory_search_latency_ms",
    "memory_search_hit_count",
    "memory_dedup_skip_count",
    "memory_pii_reject_count",
    "memory_contradiction_count",
    "agent_prompt_fact_count",
    "mempalace_collection_size",
    "chat_feedback_count",
    "training_last_success_timestamp",
    "training_last_eval_accuracy",
    "routing_decision_count",
    "digest_messages_processed",
    "digest_facts_extracted",
    "snapshot_collection_sizes",
]
