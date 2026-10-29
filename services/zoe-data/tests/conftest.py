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
# Running timers are snapshotted to ~/.zoe/timers.json by default: a test must never touch the operator's own.
os.environ["ZOE_TIMER_STORE"] = os.path.join(ZOE_TEST_STORE_DIR, "timers.json")
os.environ["ZOE_STRUCTURAL_VERIFIER"] = "off"
# The post-turn 4B callers wait out a hot conversation (brain_slot_guard). A test that drives a brain turn and then a
# background pass in one process would sleep for the quiet window: off for the suite, on in test_brain_slot_guard.py.
os.environ["ZOE_BRAIN_SLOT_GUARD"] = "off"
# The app log. A test that enters the FastAPI lifespan (a TestClient context) runs ``configure_logging``, which attaches a handler
# to the ROOT logger writing to ``$ZOE_LOG_DIR`` (default ``~/.zoe-logs/zoe-data.app.log``) - the live server's own log. Measured
# 2026-10-13: three CI-runner pytest runs (118 lines each) and one agent worktree run put 355 synthetic-user ``memory_forget_cascade:
# no database`` / ``NOT erased`` warnings into it, indistinguishable from a real half-done forget. Read at call time, so this pin holds.
os.environ["ZOE_LOG_DIR"] = os.path.join(ZOE_TEST_STORE_DIR, "logs")
# The local utterance-text logs (local_text_logs): the forget cascade and MemoryService.delete_user REWRITE them, so unpinned a unit test of
# either would strip the household's real router shadow log / intent-miss log. Read at call time, so this pin holds.
os.environ["ZOE_ROUTER_HEAD_LOG"] = os.path.join(ZOE_TEST_STORE_DIR, "router_head_shadow.jsonl")
os.environ["ZOE_INTENT_MISS_LOG"] = os.path.join(ZOE_TEST_STORE_DIR, "intent-misses.jsonl")
# ... and ``local_text_logs.sweep()`` DELETES the owner scripts' one-off backups (router-shadow-*, migrate-hermes-env-*, dead-units-*) past 14 days.
os.environ["ZOE_ADHOC_BACKUP_DIR"] = os.path.join(ZOE_TEST_STORE_DIR, "adhoc-backups")
# The router retrain ledger (router_retrain_ledger, Phase 3 of the struggle loop) lives under ~/.zoe/router_retrain by default, and every
# forget / user delete reaches it (struggle_proposals.forget_events / forget_user): a test's forget must never edit the live ledger.
os.environ["ZOE_ROUTER_RETRAIN_DIR"] = os.path.join(ZOE_TEST_STORE_DIR, "router_retrain")
# Every other state path that defaults under ``~/.zoe`` (2026-10-29: the suite wrote 6 test panel ids into the LIVE ``~/.zoe/warm-speaker.json``.
# ``warm_speaker._path()`` refuses the live file only while ``PYTEST_CURRENT_TEST`` is set, and its ``warm-speaker-save`` thread outlives the
# test that spawned it - the save ran with the guard off and landed on the real path). A session-wide pin makes the real path unreachable
# from ANY thread at ANY time; the autouse fixture below gives each test its own file so no state leaks between tests.
# Deliberately NOT pinned: ``ZOE_KV_MISS_ALERT_STATE`` - setting it would switch ``kv_miss_alert`` ON under test (it sends a Telegram alert).
_STATE_PINS = (
    ("ZOE_WARM_SPEAKER_STATE", "warm-speaker.json"),
    ("ZOE_OMI_AUDIT_PATH", "omi-audit.jsonl"),
    ("ZOE_OMI_AUDIO_DIR", "omi-recordings"),
    ("ZOE_CHANGE_NOTICES_STATE_DIR", "change-notices"),
    ("ZOE_DEPLOY_STATE_DIR", "deploy"),
    ("NIGHT_DIR", "night-window"),
    ("ZOE_PIPELINE_STORE_PATH", "engineering_pipeline_runs.jsonl"),
    ("ZOE_MULTICA_DISPATCH_PAUSE_FILE", "multica_dispatch_paused"),
    ("ZOE_VOICE_GREETING_STATE_PATH", "voice_greeting_state.json"),
)
for _var, _leaf in _STATE_PINS:
    os.environ[_var] = os.path.join(ZOE_TEST_STORE_DIR, _leaf)


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
def _warm_speaker_private_state(monkeypatch, tmp_path):
    """Each test gets its OWN warm-speaker memo file and a fresh in-process view, so a ``note()`` in one test is neither written to
    the live ``~/.zoe/warm-speaker.json`` nor read back by the next test through the shared session file."""
    monkeypatch.setenv("ZOE_WARM_SPEAKER_STATE", str(tmp_path / "warm-speaker.json"))
    try:
        import warm_speaker
    except Exception:  # noqa: BLE001 - a slim lane without the module's deps has nothing to isolate
        yield
        return
    monkeypatch.setattr(warm_speaker, "_mem", {})            # restored on teardown (module globals via monkeypatch)
    monkeypatch.setattr(warm_speaker, "_last_written", {})
    monkeypatch.setattr(warm_speaker, "_loaded", False)
    yield


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


@pytest.fixture
def jason_signed_in_at_the_panel(monkeypatch):
    """OPT-IN (never autouse): "jason" holds a fresh PIN sign-in at whatever panel asks, so a DEVICE-token biometric enrolment
    has a server-verified member to write (``biometric_scope.resolve_enroll_target``, 2026-10-19). Enrolment-shape tests that
    are not about WHO is enrolled use it; the identity tests in ``test_biometric_ownership_scope.py`` set their own."""
    import biometric_scope

    async def _pin(_panel_id):
        return "jason"

    biometric_scope.reset_enrolment_grants()
    monkeypatch.setattr(biometric_scope, "_fresh_panel_member", _pin)
    yield
    biometric_scope.reset_enrolment_grants()
