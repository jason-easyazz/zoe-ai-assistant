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


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _exact_words_in_process_index():
    """The owner's verbatim-turn index (``exact_words``) is a Postgres table in production; no test has that pool. Since
    ``MemoryService.delete_user`` FAILS CLOSED when the verbatim erase fails, a test of the delete must not reach for a pool
    that is not there: every test gets the in-process index (a test that wants the SQL one sets it itself)."""
    import exact_words
    exact_words.set_backend(exact_words.MemoryBackend())
    yield
    exact_words.set_backend(None)
