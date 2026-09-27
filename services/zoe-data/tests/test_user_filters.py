"""user_filters.is_synthetic_user + the batch passes that must honour it.

Every nightly/weekly pass that walks "all users" (dreaming, consolidation, music
taste, portrait) must drop test/demo/probe ids and guest sentinels, keep real
ids, and log ONE count-only line. Measured 2026-09-27: 21 of the 25 ids the
dreaming pass walked were synthetic. Negative control: delete the
``drop_synthetic_users`` call from any pass and its test below goes red.
"""
from __future__ import annotations

import logging

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import memory_digest
import user_filters
import user_portrait
from user_filters import drop_synthetic_users, is_synthetic_user

SYNTHETIC = ["test-route-probe", "demo_tomb", "bench-1"]
REAL = "member-a"
MIXED = [SYNTHETIC[0], REAL, SYNTHETIC[1], SYNTHETIC[2]]


@pytest.fixture(autouse=True)
def _no_allowlist(monkeypatch):
    monkeypatch.delenv("ZOE_SYNTHETIC_USER_ALLOWLIST", raising=False)


# --------------------------------------------------------------------------- #
# The predicate
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("uid", [
    "test-route-probe", "test-sec-b-4f9c0c", "test_isolation_a_1752624000",
    "demo_isoA_1a2b", "demo-tomb-final2", "probe-x", "ci_runner", "e2e-panel",
    "bench_1", "TEST-upper", "guest", "voice-daemon", "voice-guest", "anonymous",
    "", None, "  test-padded",
])
def test_synthetic_ids_match(uid):
    assert is_synthetic_user(uid) is True


@pytest.mark.parametrize("uid", [
    "jason", "zoe-touch-pi", "family-admin", "Christine", "benchley", "testa",
    "demolition", "probey", "cian", "e2eish", "contest-winner", "member-a",
])
def test_real_ids_do_not_match(uid):
    """The separator is load-bearing: a name merely STARTING with a prefix
    ('Christine', 'testa', 'demolition') must stay a person."""
    assert is_synthetic_user(uid) is False


def test_allowlist_opts_a_synthetic_id_back_in(monkeypatch):
    monkeypatch.setenv("ZOE_SYNTHETIC_USER_ALLOWLIST", " demo_lab , ,test-keep")
    assert is_synthetic_user("demo_lab") is False
    assert is_synthetic_user("test-keep") is False
    assert is_synthetic_user("demo_other") is True


def test_allowlist_cannot_turn_guest_into_a_person(monkeypatch):
    monkeypatch.setenv("ZOE_SYNTHETIC_USER_ALLOWLIST", "guest,voice-daemon")
    assert is_synthetic_user("guest") is True
    assert is_synthetic_user("voice-daemon") is True


def test_drop_logs_one_count_only_line(caplog):
    log = logging.getLogger("t.drop")
    with caplog.at_level(logging.INFO, logger="t.drop"):
        kept = drop_synthetic_users(MIXED, pass_name="unit", log=log)
    assert kept == [REAL]
    lines = [r.getMessage() for r in caplog.records]
    assert lines == ["unit: users kept=1 skipped_synthetic=3"]
    assert not any(s in lines[0] for s in SYNTHETIC)  # counts only, never ids


def test_memory_digest_guest_constant_is_the_shared_one():
    assert memory_digest._GUEST_USERS is user_filters.GUEST_USERS
    assert memory_digest._message_owner_expr is user_filters.message_owner_expr


# --------------------------------------------------------------------------- #
# The passes
# --------------------------------------------------------------------------- #
def _skip_line(caplog, pass_name):
    hits = [r.getMessage() for r in caplog.records
            if r.getMessage().startswith(f"{pass_name}: users kept=")]
    assert hits == [f"{pass_name}: users kept=1 skipped_synthetic=3"], hits
    return hits[0]


def _stub_listing(monkeypatch):
    async def fake_list(sql, params=(), *, db=None):
        return list(MIXED)
    monkeypatch.setattr(memory_digest, "_list_user_ids", fake_list)


class _ListingDb:  # portrait lists with `await (await db.execute(sql)).fetchall()`
    async def execute(self, sql, params=()):
        rows = [(uid,) for uid in MIXED]

        class _R:
            async def fetchall(self):
                return rows
        return _R()


# (pass log name, module, per-user fn patched, runner)
PASSES = [
    ("dreaming", memory_digest, "run_dreaming_cycle",
     lambda: memory_digest.run_dreaming_for_all()),
    ("consolidation", memory_digest, "run_weekly_consolidation",
     lambda: memory_digest.run_weekly_consolidation_for_all()),
    ("music_taste_digest", memory_digest, "run_music_taste_digest",
     lambda: memory_digest.run_music_taste_digest_for_all()),
    ("portrait", user_portrait, "run_portrait_synthesis",
     lambda: user_portrait.run_portrait_synthesis_for_all(db=_ListingDb())),
]


@pytest.mark.parametrize("pass_name, module, per_user, run", PASSES, ids=[p[0] for p in PASSES])
async def test_pass_skips_synthetic(monkeypatch, caplog, pass_name, module, per_user, run):
    _stub_listing(monkeypatch)
    seen = []

    async def fake(user_id, db=None, run_agent_sync_phase=True):
        seen.append((user_id, run_agent_sync_phase))
        return {"user_id": user_id}

    monkeypatch.setattr(module, per_user, fake)
    with caplog.at_level(logging.INFO):
        await run()
    # Only the real id; for dreaming, agent sync runs for the first REAL user.
    assert seen == [(REAL, True)]
    _skip_line(caplog, pass_name)


async def test_allowlisted_demo_user_is_processed(monkeypatch):
    monkeypatch.setenv("ZOE_SYNTHETIC_USER_ALLOWLIST", "demo_tomb")
    _stub_listing(monkeypatch)
    seen = []

    async def fake_cycle(user_id, db=None, run_agent_sync_phase=True):
        seen.append(user_id)
        return {"user_id": user_id}

    monkeypatch.setattr(memory_digest, "run_dreaming_cycle", fake_cycle)
    await memory_digest.run_dreaming_for_all()
    assert seen == [REAL, "demo_tomb"]
