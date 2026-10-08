"""The arms of the bake-off, by name. ``make_arm`` is the only place a runner picks one."""
from __future__ import annotations

from .base import Arm, IngestReport, Turn

#: arm name -> what it is (docs/knowledge/zoe-memory-bench.md, "Bake-off arms")
ARMS = {
    "Z0": "the current MemoryService, in-process over the lab store (implemented)",
    "Z0-off": "Z0 with every control switched off: the negative control (implemented)",
    "Z0n": "Z0 + the night mind (services/zoe-data/night_mind.py): the nightly reflection pass with its OWN model - the lab's fake brain, or the clone at --model-url - so K2 / K3 (and K6-K12) are measured, not scripted",
    "Z0e": "Z0 over a REAL Chroma collection with Chroma's MiniLM embedder, as live: the lab's retrieval is bag-of-words, this arm measures retrieval (needs chromadb + the MiniLM model on disk, else SKIP)",
    "H0": "Hindsight concise + observations, no Zoe layer (implemented over its HTTP API; needs the bake-off server, else every cell SKIPs)",
    "H1": "Hindsight verbatim, observations off, Zoe layer on (implemented; needs the bake-off server)",
    "H2": "Hindsight concise + observations, fenced consolidation, Zoe layer on (implemented; needs the bake-off server)",
    "G": "Graphiti add_triplet only + authority wrapper (STUB)",
    "MV": "MemPalace 3.10.0 as a library: the VERBATIM tier alone (needs the bake-off venv; else every cell SKIPs)",
    "HM": "Hindsight (distilled tier: STUB) + MemPalace (verbatim tier) combined; its own cells: zmb/hm_cells.py",
    "MPA": "MemPalace 3.10.0 set up as designed: the AGENT operates it (the brain calls its MCP tools: search, add, supersede, the temporal triples; its protocol is injected; its closet pass runs at idle); Zoe floors around the writes; its own cells: zmb/mpa_cells.py",
    "HMA": "the FULL combination, integrated: agent-operated MemPalace (MPA) as the episodic tier + Hindsight (concise + observations, H2) as the reflective tier: one ingest path (a drawer is Hindsight's only document), one embedder, one packet, one protocol, one forget; its own cells: zmb/mpa_cells.py",
    "ZMA": "Zoe's CURRENT stack (Z0e: real MemoryService over Chroma + MiniLM) with MemPalace operated on top, integrated: one ingest path (the chunk is the source Z0 reads), one embedder, one packet, one forget; its own cells: zmb/mpa_cells.py",
}


def make_arm(name: str) -> Arm:
    """Build an arm. An unimplemented one is returned as a stub whose every call raises
    ``NotImplementedError`` with the install hint (the runner reports that as a SKIP with the reason)."""
    if name not in ARMS:
        raise ValueError(f"unknown arm {name!r} (known: {', '.join(ARMS)})")
    if name == "Z0":
        from .z0 import Z0Arm
        return Z0Arm()
    if name == "Z0n":
        from .z0 import Z0Arm
        return Z0Arm(name="Z0n", night=True)
    if name == "Z0e":
        from .z0 import Z0Arm
        return Z0Arm(name="Z0e", embed=True)
    if name == "Z0-off":
        from .z0 import Z0Arm
        from ..lab_driver import CONTROLS
        return Z0Arm(off=frozenset(CONTROLS), name="Z0-off")
    if name == "G":
        from .graphiti import GraphitiArm
        return GraphitiArm()
    if name == "MV":
        from .mempalace_verbatim import MemPalaceVerbatimArm
        return MemPalaceVerbatimArm()
    if name == "HM":
        from .hm import HMArm
        return HMArm()
    if name == "MPA":
        from .mempalace_agent import MemPalaceAgentArm
        return MemPalaceAgentArm()
    if name == "ZMA":
        from .zma import ZMAArm
        return ZMAArm()
    if name == "HMA":
        from .hma import HMAArm
        return HMAArm()
    from .hindsight import HindsightArm
    return HindsightArm(name)


__all__ = ["ARMS", "Arm", "IngestReport", "Turn", "make_arm"]
