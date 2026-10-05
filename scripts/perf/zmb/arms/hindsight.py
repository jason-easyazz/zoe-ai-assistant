"""Arms H0 / H1 / H2: Hindsight (vectorize-io/hindsight) behind the bench's arm interface. STUB.

Not implemented in the foundation PR: the bake-off runner fills it in (docs/knowledge/zoe-memory-bench.md,
"Bake-off arms"), on the lab box, inside an operator-approved brain-stop window, against a **scratch**
Postgres (a separate ``pgvector/pgvector:pg17`` container on port 55432 - never ``zoe-database``) and the
clone brain on :11500. Nothing here opens a socket or imports Hindsight.

Variants (decision record, section 6): ``H1`` = 0.10.2 slim, ``verbatim`` extraction, observations OFF,
reranker OFF, Zoe's authority gate + forget ledger in front (lean arm); ``H2`` = ``concise`` + observations
ON, consolidation per authority tag scope (the fence), Zoe layer ON (full arm); ``H0`` = ``concise`` +
observations ON with NO Zoe layer (measures what is native).
"""
from __future__ import annotations

from typing import Any

from .base import Arm, IngestReport, Turn

VARIANTS = {
    "H0": "concise + observations ON, no Zoe layer (what Hindsight does natively)",
    "H1": "verbatim, observations OFF, reranker OFF, Zoe gate + forget ledger in front (lean)",
    "H2": "concise + observations ON, consolidation per authority tag scope, Zoe layer ON (full)",
}
INSTALL_HINT = (
    "Hindsight is not installed here. To run this arm: create a py3.12 venv under "
    "/home/zoe/.zoe/bakeoff-2026-10/ and `pip install 'hindsight-api-slim==0.10.2'`; start the scratch "
    "Postgres (pgvector/pgvector:pg17, port 55432, its own volume) and the clone brain on :11500 in an "
    "operator-approved brain-stop window; export HF_HUB_OFFLINE=1 ANONYMIZED_TELEMETRY=False; then implement "
    "HindsightArm.ingest/recall/forget/stats against its HTTP API (see docs/architecture/zoe-hindsight-bakeoff.md)."
)


class HindsightArm(Arm):
    capabilities = frozenset()

    def __init__(self, variant: str = "H1"):
        if variant not in VARIANTS:
            raise ValueError(f"unknown Hindsight variant {variant!r} (known: {', '.join(VARIANTS)})")
        self.name = variant
        self.variant = variant

    def _todo(self, what: str):
        raise NotImplementedError(f"arm {self.name}: {what} is not implemented. {INSTALL_HINT}")

    def reset(self, user_id: str) -> None:
        self._todo("reset")

    def ingest(self, turns: "list[Turn]") -> IngestReport:
        self._todo("ingest")

    def recall(self, query: str, k: int = 10) -> "list[dict[str, Any]]":
        self._todo("recall")

    def forget(self, entity: str) -> str:
        self._todo("forget")

    def as_of(self, query: str, ts: str) -> "list[dict[str, Any]]":
        self._todo("as_of")

    def stats(self) -> "dict[str, Any]":
        self._todo("stats")
