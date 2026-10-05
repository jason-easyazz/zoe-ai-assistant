"""Arm G: Graphiti (getzep/graphiti) behind the bench's arm interface. STUB.

Not implemented in the foundation PR. The bake-off arm is Graphiti used as a LIBRARY with **no Graphiti LLM**:
Zoe's own extractors build ``EntityNode`` / ``EntityEdge`` and call ``add_triplet``, with the authority wrapper
around ``resolve_edge_contradictions`` (decision record, section 6: arm ``G`` is scoped to the temporal and
people cells; ``G-native`` - ``add_episode`` on the clone brain, 20 episodes - is a viability record only).
Nothing here opens a socket or imports Graphiti.
"""
from __future__ import annotations

from typing import Any

from .base import Arm, IngestReport, Turn

INSTALL_HINT = (
    "Graphiti is not installed here. To run this arm: in the bake-off venv under "
    "/home/zoe/.zoe/bakeoff-2026-10/ run `pip install graphiti-core` (Neo4j-free backend: the scratch "
    "Postgres/FalkorDB the decision record names, never the live database); set "
    "GRAPHITI_TELEMETRY_ENABLED=false; then implement GraphitiArm.ingest (Zoe extractors -> add_triplet), "
    "recall, forget (invalidated-but-stored is Graphiti's native behaviour: score it), as_of and stats "
    "(see docs/adr/ADR-graphiti-bakeoff.md)."
)


class GraphitiArm(Arm):
    name = "G"
    capabilities = frozenset()

    def _todo(self, what: str):
        raise NotImplementedError(f"arm G: {what} is not implemented. {INSTALL_HINT}")

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
