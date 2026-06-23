"""Canonical invariants — LOCK IN the settled decisions so they can't silently drift.

The repo kept re-deciding the brain model and the voice stack because nothing said,
in one enforced place, *what was locked*. `docs/CANONICAL.md` now declares it, and
this test makes the declaration load-bearing: swapping a rock fails CI, which forces
the change to be a deliberate, reviewed edit (of BOTH the doc and this test) rather
than a quiet config tweak that the next refactor undoes.

If a rock legitimately changes, update the expected value here in the SAME commit —
that keeps the intent explicit and visible in review. See feedback_fixed_models_are_rocks
and project_zoe_voice_live_topology in memory.
"""
import os
import re

import pytest

DATA = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # services/zoe-data
REPO = os.path.dirname(os.path.dirname(DATA))  # repo root
CANONICAL = os.path.join(REPO, "docs", "CANONICAL.md")


def _rocks() -> dict:
    """Parse the machine-readable rocks block out of docs/CANONICAL.md."""
    text = open(CANONICAL, encoding="utf-8").read()
    m = re.search(r"LOCKED-ROCKS.*?```yaml\n(.*?)```", text, re.DOTALL)
    assert m, "LOCKED-ROCKS yaml block missing from docs/CANONICAL.md"
    block = m.group(1)
    try:
        import yaml
        return yaml.safe_load(block)["rocks"]
    except ImportError:
        # dependency-free fallback: flat key: "value" scrape
        out, stack = {}, {}
        for line in block.splitlines():
            mm = re.match(r"^(\s*)([\w]+):\s*(.*)$", line)
            if not mm:
                continue
            indent, key, val = len(mm.group(1)), mm.group(2), mm.group(3).strip().strip('"')
            if not val:
                stack[indent] = out.setdefault(key, {})
            else:
                parent = stack.get(indent - 2, out)
                parent[key] = val
        return out["rocks"]


# ── The rocks: settled, do not swap (see feedback_fixed_models_are_rocks) ─────
def test_brain_rock_is_gemma4_e4b_with_mtp():
    brain = _rocks()["brain"]
    assert brain["family"] == "Gemma 4", f"brain LLM family drifted: {brain}"
    assert brain["variant"] == "E4B-QAT", f"brain variant drifted off E4B-QAT: {brain}"
    assert brain["drafter"] == "MTP", "MTP speculative drafter dropped from the brain rock"


def test_stt_rock_is_moonshine():
    stt = _rocks()["stt"]
    assert "Moonshine" in stt["name"], f"STT rock drifted off Moonshine: {stt}"


def test_tts_rock_is_kokoro():
    assert "Kokoro" in _rocks()["tts"]["name"], "TTS rock drifted off Kokoro"


# ── The rocks must match the LIVE wiring, not just the doc ────────────────────
def test_moonshine_actually_loaded_in_live_startup():
    """The STT rock has to be wired, not merely declared — guard the loader marker."""
    marker = _rocks()["stt"]["loader_marker"]
    main = open(os.path.join(DATA, "main.py"), encoding="utf-8").read()
    assert marker in main, (
        f"STT loader marker '{marker}' not found in main.py — Moonshine warmup "
        "may have been dropped (whisper is a fallback, never the primary rock)"
    )


# ── The cleanup stays clean: no archive graveyard creeps back ─────────────────
def test_no_docs_archive_graveyard():
    """docs/archive was removed on 2026-06-24 (git history keeps it). Retire by
    removing, not by hoarding a graveyard the whole team greps through."""
    assert not os.path.isdir(os.path.join(REPO, "docs", "archive")), (
        "docs/archive reappeared — retire superseded files by deleting them "
        "(git keeps history); do not re-introduce an archive graveyard. See docs/CANONICAL.md"
    )
