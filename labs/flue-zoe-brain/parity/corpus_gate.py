#!/usr/bin/env python3
"""Conversation-quality corpus gate for the live Flue brain (LAB-ONLY).

Replays the memory/chat/social-biased subset of
tests/voice/comprehensive_conversation_test.py through the live /api/chat as a
freshly provisioned (empty-store) user — the confound-free reconstruction of
the 2026-07-03 parity corpus. Auto-flags recall misses (against the corpus's
own expect_recall markers) and any identity/research-stall regression; the rest
are JUDGE rows for a human/model read.

Driven by run_gates.py (import GATE, call GATE.run(ctx)); standalone:
`python3 run_gates.py --gates corpus`.
"""
from __future__ import annotations

import importlib.util

from gatelib import FORBIDDEN_IDENTITY, RESEARCH_STALL, REPO_ROOT, GateContext, GateSpec

CORPUS = REPO_ROOT / "tests" / "voice" / "comprehensive_conversation_test.py"

# Chat/social/info/memory-recall bias — the categories the parity corpus used.
CATEGORIES = [
    "greetings", "memory_personal", "memory_preferences", "simple_questions",
    "complex_questions", "multiturn_memory", "relationship_memory",
    "temporal_memory", "conversation_flow", "confidence_expression",
]


def _load_conversations() -> list[dict]:
    spec = importlib.util.spec_from_file_location("cct", CORPUS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return [c for c in module.TEST_CONVERSATIONS if c["category"] in CATEGORIES]


def run(ctx: GateContext) -> None:
    for conv in _load_conversations():
        cat = conv["category"]
        # One session per conversation preserves multi-turn context; nonce'd so
        # it is unique to this run and can never wedge a future one.
        session = ctx.session(f"corpus-{cat}")
        for turn in conv["conversation"]:
            q = turn["query"]
            recall = turn.get("expect_recall")
            must = [recall] if recall else None
            # Never let a research-stall or persona leak pass silently, even on
            # turns that are otherwise JUDGE.
            ctx.expect(cat, q, session, must=must, must_not=FORBIDDEN_IDENTITY + RESEARCH_STALL)


GATE = GateSpec(name="corpus", description="46-prompt conversation-quality corpus (recall/identity/research auto-checked)", run=run)
