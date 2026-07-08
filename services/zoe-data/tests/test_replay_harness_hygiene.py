"""Replay-harness session hygiene + brain-fallback detection.

Incident 2026-07-07: the voice-replay harness hardcoded flue session id
"replay", so the sidecar's durable session grew across runs until prompt
assembly overflowed the model context (8288 > 8192 tokens → HTTP 500 on every
turn). Because the flue client yields a graceful fallback text instead of
raising, every degraded brain turn still counted as OK — silently weakening
replay-gate evidence. These tests pin the two fixes:

  * the canonical fallback text classifies as a HARD failure (ERROR), never OK;
  * the session id is generated per RUN (never the bare "replay" again), while
    samples within one run still share it (within-run continuity preserved).

Slim-dep safe: ``replay_samples`` imports only stdlib + ``zoe_flue_client``
(itself slim) at module level — the heavy voice/DB imports live inside its
``_run`` coroutine, which these tests never call.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.ci_safe


def _harness():
    """Import the harness module with its import-time ``os.chdir`` contained,
    so the rest of the pytest session keeps its working directory."""
    cwd = os.getcwd()
    try:
        import replay_samples
        return replay_samples
    finally:
        os.chdir(cwd)


# ── fallback text = HARD failure ──────────────────────────────────────────────

def test_fallback_reply_is_hard_failure():
    rs = _harness()
    import zoe_flue_client

    verdict = rs._classify("what is on my calendar", zoe_flue_client._FALLBACK_TEXT, "brain")
    assert verdict == "ERROR"


def test_fallback_embedded_in_longer_reply_is_hard_failure():
    rs = _harness()
    import zoe_flue_client

    reply = "Hmm. " + zoe_flue_client._FALLBACK_TEXT
    assert rs._classify("hello", reply, "brain") == "ERROR"


def test_fallback_constant_is_imported_not_duplicated():
    # The harness must reference the client's canonical constant, not carry a
    # copy that can drift if the fallback wording ever changes.
    rs = _harness()
    import zoe_flue_client

    assert rs._BRAIN_FALLBACK_TEXT is zoe_flue_client._FALLBACK_TEXT


def test_normal_brain_reply_still_ok():
    rs = _harness()
    assert rs._classify("hello", "Hi Jason! Your calendar is clear today.", "brain") == "OK"


# ── per-run session id ────────────────────────────────────────────────────────

def test_session_id_is_per_run_not_hardcoded():
    rs = _harness()
    sid = rs._run_session_id()
    # replay-<uuid4 hex[:12]> — a RANDOM token, not a wall-clock second, so two
    # runs in the same second (parallel CI workers) can't collide into one
    # sidecar session and re-create the durable-session-growth incident.
    assert sid.startswith("replay-")
    assert sid != "replay"  # the durable-session-growth incident id
    suffix = sid[len("replay-"):]
    assert len(suffix) == 12 and all(c in "0123456789abcdef" for c in suffix)


def test_session_id_differs_across_runs():
    rs = _harness()
    # Random ids: distinct even when generated back-to-back in the same second.
    assert rs._run_session_id() != rs._run_session_id()
