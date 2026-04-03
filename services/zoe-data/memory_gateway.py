import os
from typing import Any, Dict, List

import httpx


class MemoryGateway:
    """Gateway for hybrid memory retrieval with optional Atomic sidecar."""

    def __init__(self):
        self.atomic_url = os.environ.get("ATOMIC_BASE_URL", "").rstrip("/")
        self.atomic_enabled = os.environ.get("SEMANTIC_MEMORY_GATEWAY", "false").lower() == "true"
        self.timeout = float(os.environ.get("ATOMIC_TIMEOUT_SECONDS", "6"))

    async def semantic_search(self, query: str, limit: int = 10) -> List[Dict[str, Any]]:
        """Return Atomic semantic hits as normalized records."""
        if not self.atomic_enabled or not self.atomic_url:
            return []
        payload = {"query": query, "limit": limit}
        endpoint = f"{self.atomic_url}/api/search"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(endpoint, json=payload)
                resp.raise_for_status()
                data = resp.json()
        except Exception:
            return []

        hits = data.get("results") or data.get("hits") or []
        normalized = []
        for hit in hits:
            normalized.append(
                {
                    "id": hit.get("id"),
                    "content": hit.get("content") or hit.get("text") or "",
                    "score": float(hit.get("score", 0.0) or 0.0),
                    "source": "atomic",
                    "metadata": hit.get("metadata", {}),
                }
            )
        return normalized

    async def ingest_memory(self, memory: Dict[str, Any]) -> bool:
        """Best-effort memory write to Atomic."""
        if not self.atomic_enabled or not self.atomic_url:
            return False
        endpoint = f"{self.atomic_url}/api/atoms"
        payload = {
            "title": memory.get("title") or "Zoe Memory",
            "content": memory.get("content", ""),
            "tags": memory.get("tags") or ["zoe", "memory"],
            "metadata": memory.get("metadata") or {},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(endpoint, json=payload)
                return resp.status_code < 300
        except Exception:
            return False
