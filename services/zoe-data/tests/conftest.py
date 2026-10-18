"""Pin every per-user LIVE store to a throwaway directory before any zoe-data module imports.

Why this exists: `memory_service._MEMPALACE_DATA` (the MemPalace/Chroma directory) is
read from the environment AT IMPORT and defaults to the operator's own palace
(``~/.mempalace``); the voice STT log defaults to ``~/.zoe-voice/voice_stt.jsonl``.
A test that drives a real handler against a fake database still reaches the real
MemoryService — ``test_people_create_fk_ensure_user.py`` runs
``_execute_people_create_direct``, whose ``_store_person_memory`` ingests a
"Person in contacts" fact — so every local run of the suite appended synthetic
contact drawers for the fixture users ``u1`` / ``newbie`` / ``existing`` to the
HOUSEHOLD palace. Measured 2026-09-29: 225 of 482 live drawers were test rows
(135 written on a single day of agent test runs), all counted by the nightly
memory-quality snapshot and all visible to recall.

pytest imports this conftest before it collects any module under this directory,
so the pin lands before ``memory_service`` is imported. It is UNCONDITIONAL: an
operator-exported ``MEMPALACE_DATA_DIR`` that points at the live palace must not
leak into the suite either. ``HOME`` itself is deliberately NOT redirected — the
venv, model caches and the MiniLM ONNX cache live under it and a fresh HOME would
turn every session into a network download.

Add every new live-store env knob HERE, not in the test that happens to notice it.
``test_live_store_isolation.py`` is the negative control (fails under ``--noconftest``).
"""

from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile

_LIVE_STORE_MODULES = ("memory_service", "routers.voice_tts")
_too_late = [name for name in _LIVE_STORE_MODULES if name in sys.modules]
if _too_late:  # a pin after the import is a silent no-op — fail loudly instead
    raise RuntimeError(
        f"live-store pin came too late: {_too_late} imported before tests/conftest.py"
    )

ZOE_TEST_STORE_DIR = tempfile.mkdtemp(prefix="zoe-test-stores-")
atexit.register(shutil.rmtree, ZOE_TEST_STORE_DIR, ignore_errors=True)  # no /tmp litter per session
os.environ["MEMPALACE_DATA_DIR"] = os.path.join(ZOE_TEST_STORE_DIR, "mempalace")
os.environ["ZOE_VOICE_STT_LOG"] = os.path.join(ZOE_TEST_STORE_DIR, "voice_stt.jsonl")
# The write-time reject ledger (memory_reject_ledger) persists day counters under ~/.zoe by default.
os.environ["ZOE_MEMORY_REJECT_LEDGER"] = os.path.join(ZOE_TEST_STORE_DIR, "memory-reject-ledger.json")
# The structural floors' off-path verifier is the one caller of the LIVE brain's endpoint in the memory path; a test that exercises it
# turns it on with a stubbed client (test_structural_verifier.py, test_structural_claims_digest.py). No other test may reach it.
os.environ["ZOE_STRUCTURAL_VERIFIER"] = "off"
# The post-turn 4B callers wait out a hot conversation (brain_slot_guard). A test that drives a brain turn and then a
# background pass in one process would sleep for the quiet window: off for the suite, on in test_brain_slot_guard.py.
os.environ["ZOE_BRAIN_SLOT_GUARD"] = "off"
# The app log. A test that enters the FastAPI lifespan (a TestClient context) runs ``configure_logging``, which attaches a handler
# to the ROOT logger writing to ``$ZOE_LOG_DIR`` (default ``~/.zoe-logs/zoe-data.app.log``) - the live server's own log. Measured
# 2026-10-13: three CI-runner pytest runs (118 lines each) and one agent worktree run put 355 synthetic-user ``memory_forget_cascade:
# no database`` / ``NOT erased`` warnings into it, indistinguishable from a real half-done forget. Read at call time, so this pin holds.
os.environ["ZOE_LOG_DIR"] = os.path.join(ZOE_TEST_STORE_DIR, "logs")
# The router retrain ledger (router_retrain_ledger, Phase 3 of the struggle loop) lives under ~/.zoe/router_retrain by default, and every
# forget / user delete reaches it (struggle_proposals.forget_events / forget_user): a test's forget must never edit the live ledger.
os.environ["ZOE_ROUTER_RETRAIN_DIR"] = os.path.join(ZOE_TEST_STORE_DIR, "router_retrain")


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _exact_words_in_process_index():
    """The owner's verbatim-turn index (``exact_words``) is a Postgres table in production; no test has that pool. Since
    ``MemoryService.delete_user`` FAILS CLOSED when the verbatim erase fails, a test of the delete must not reach for a pool
    that is not there: every test gets the in-process index (a test that wants the SQL one sets it itself)."""
    import exact_words
    import night_store
    import struggle_detector
    exact_words.set_backend(exact_words.MemoryBackend())
    night_store.set_backend(night_store.MemoryBackend())     # the night mind's tables are Postgres in production; same rule
    struggle_detector.set_backend(struggle_detector.MemoryBackend())   # struggle_events too (delete_user / forget fail closed)
    yield
    exact_words.set_backend(None)
    night_store.set_backend(None)
    struggle_detector.reset()     # the worker task belongs to this test's loop
    struggle_detector.set_backend(None)


@pytest.fixture(autouse=True)
def _db_pool_never_initialised(monkeypatch):
    """``db_pool.pool_ever_initialised()`` is process-global and sticky (a pool that existed and was lost is a FAULT to the forget
    cascade, a process that never had one is not). A test that really calls ``init_pool`` (or a lifespan with a faked asyncpg) must not
    leave it True for the next forget test: every test starts as a process with no database; one that wants the other state sets it."""
    try:
        import db_pool
    except Exception:  # noqa: BLE001 - a slim lane without asyncpg has no pool to speak of
        yield
        return
    monkeypatch.setattr(db_pool, "_ever_initialised", False, raising=False)
    yield
