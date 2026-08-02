"""Self-recall integrity check for the vector memory store.

Motivation (2026-07-31 incident): a torn HNSW persist left the drawers index
returning garbage neighbours for ~8 hours before the next cold load segfaulted.
Plumbing probes ("does search() return?") pass in that state — recall was
"working" and silently wrong. This check verifies search returns the RIGHT
thing: query the text of one real stored row and require that row (or an
exact-duplicate embedding) among the nearest neighbours.

Kept dependency-free (the collection is passed in) so slim-CI can unit-test it
with a stub. Read-only: uses collection.get/query directly — never
MemoryService.search — so it ticks no access counters.
"""
from __future__ import annotations

from typing import Any

# A top-hit distance this close to zero means the query text's own embedding
# was found — an exact-duplicate row legitimately outranking the sampled row.
_ZERO_DISTANCE = 1e-3
# Neighbours to request; generous so benign near-duplicates can't evict the
# sampled row from the window.
_N_RESULTS = 10


def run_self_recall_check(collection: Any, *, max_doc_chars: int = 512) -> dict[str, str]:
    """Return {"status": "ok"|"empty"|"degraded", "detail": ...}. Never raises."""
    try:
        sample = collection.get(limit=1, include=["documents"])
        ids = sample.get("ids") or []
        docs = sample.get("documents") or []
        if not ids or not docs or not docs[0]:
            return {"status": "empty", "detail": "no stored rows to self-recall"}
        row_id, doc = ids[0], str(docs[0])[:max_doc_chars]

        res = collection.query(
            query_texts=[doc], n_results=_N_RESULTS, include=["distances"]
        )
        hit_ids = (res.get("ids") or [[]])[0]
        distances = (res.get("distances") or [[]])[0]

        if row_id in hit_ids:
            return {"status": "ok", "detail": f"row {row_id} recalled itself"}
        if distances and distances[0] is not None and distances[0] < _ZERO_DISTANCE:
            # The sampled row lost a tie to an identical embedding — the
            # embedder and index are still answering correctly.
            return {"status": "ok", "detail": "exact-duplicate embedding recalled"}
        return {
            "status": "degraded",
            "detail": (
                f"self-recall MISS: row {row_id} absent from top-{_N_RESULTS} "
                f"for its own text (top distance {distances[0] if distances else 'n/a'}) "
                "— vector index likely corrupt"
            ),
        }
    except Exception as exc:  # the probe must never take the service down
        return {"status": "degraded", "detail": f"self-recall check errored: {exc}"}
