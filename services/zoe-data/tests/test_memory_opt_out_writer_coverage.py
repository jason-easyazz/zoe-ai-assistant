"""The memory opt-out must cover EVERY automatic writer, the voice lane included.

Found 2026-10-12: ``MEMORY_OPT_OUT_SOURCES`` listed the chat-lane sources but not the
voice-lane ones (``voice_regex``, ``voice_turn_digest``, ``voice``), so an opted-out member
speaking to a panel was still mined. The ingest chokepoint only honours the opt-out for the
listed sources, so the list IS the policy: this file pins it by behaviour (each real source)
and by enumeration (every writer source in the codebase must be classified on purpose).
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from test_memory_opt_out_endpoints import _isolated_memory_service

pytestmark = pytest.mark.ci_safe

#: Sources deliberately NOT subject to the opt-out: explicit teach / the user's own action /
#: a tool the user invoked / measurement harnesses. A NEW source that is in neither this set
#: nor MEMORY_OPT_OUT_SOURCES fails the scan below, so whoever adds a writer must choose.
EXPLICIT_OR_NON_AUTOMATIC = frozenset({
    "brain_tool", "mcp", "voice_fact", "skybridge_action", "proposal", "manual",
    "journal", "notes",                       # person-extractor over the user's own notes/journal (open-problems.md)
    "profile-analysis",                       # user-invoked profile analysis endpoint
    "mempalace_baseline", "prompt_packet_measure",
})
_WRITER_CALLS = ("ingest", "extract", "digest", "process_text")


def _writer_sources_in_tree() -> dict[str, set[str]]:
    root = pathlib.Path(__file__).resolve().parent.parent
    found: dict[str, set[str]] = {}
    for p in root.rglob("*.py"):
        if {"tests", "alembic", ".venv", "venv"} & set(p.relative_to(root).parts):
            continue
        try:
            tree = ast.parse(p.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for n in ast.walk(tree):
            if not isinstance(n, ast.Call):
                continue
            name = getattr(n.func, "attr", getattr(n.func, "id", ""))
            if not any(w in name for w in _WRITER_CALLS):
                continue
            for k in n.keywords:
                if k.arg == "source" and isinstance(k.value, ast.Constant) and isinstance(k.value.value, str):
                    found.setdefault(k.value.value, set()).add(f"{p.relative_to(root)}:{name}")
    return found


@pytest.mark.asyncio
@pytest.mark.parametrize("src", ["voice_regex", "voice_turn_digest", "voice", "chat_regex_fallback", "idle_consolidation"])
async def test_voice_lane_and_fallback_writers_honour_opt_out(monkeypatch, src):
    import user_prefs

    async def opted_out(user_id, *, db=None):
        return user_id == "u-optout"

    monkeypatch.setattr(user_prefs, "is_memory_opted_out", opted_out)
    svc, _col = _isolated_memory_service()
    written: list[str] = []
    svc._write_row = lambda mem_id, text, metadata: written.append(metadata["source"])
    assert await svc.ingest("Voice lane fact about the user.", user_id="u-optout", source=src) is None
    assert written == [], f"{src} wrote for an opted-out user"
    assert await svc.ingest("Voice lane fact about another user.", user_id="u-other", source=src) is not None


def test_every_writer_source_in_the_codebase_is_classified():
    import memory_authority as ma
    from user_prefs import MEMORY_OPT_OUT_SOURCES

    seen = _writer_sources_in_tree()
    assert "voice" in seen and "chat_regex" in seen, "the scan found no known writers: the scan is broken"
    unclassified = {s: sorted(w)[:2] for s, w in seen.items()
                    if s not in MEMORY_OPT_OUT_SOURCES and s not in EXPLICIT_OR_NON_AUTOMATIC}
    assert not unclassified, f"writer sources neither opt-out-honouring nor explicitly exempt: {unclassified}"

    # memory_authority's automatic classes are the second enumeration (it names writers no
    # call site spells as a literal): all must honour the opt-out unless exempted here.
    automatic = (ma.DETERMINISTIC_USER_WRITERS | ma.MODEL_FROM_TURN_WRITERS | ma.TRANSCRIPT_WRITERS
                 | ma.VOICE_LANE_WRITERS | ma.INFERRED_ONLY_AUTOMATIC | ma.LLM_WRITERS)
    exempt = EXPLICIT_OR_NON_AUTOMATIC | {
        "zoe_agent",            # a wrapper label; its real writes go out as chat_regex / chat_regex_fallback
        "decay_sweep", "implicit_supersede", "hindsight_retain_candidate", "person_extractor_llm",
    }
    missing = sorted(w for w in automatic if w not in MEMORY_OPT_OUT_SOURCES and w not in exempt)
    assert not missing, f"automatic writers missing from MEMORY_OPT_OUT_SOURCES: {missing}"
