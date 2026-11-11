"""The replay harness is the member UNDER TEST: its promises must never become commitments-ledger rows (the 2026-11-11 finding:
33 of 33 rows were ``replay-`` sessions). Same detection as the write-claim guard: ``kwargs["replay_isolation"]``. Synthetic data only."""
from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.ci_safe

import brain_dispatch
import commitments

PROMISE = "Sure, I'll remind you at 5 pm to call the dentist."


def _commit(monkeypatch, **kw):
    seen = []
    monkeypatch.setattr(commitments, "schedule_record", lambda *a, **k: seen.append(a) or True)
    brain_dispatch._provenance_commit("member-1", PROMISE, "remind me", "replay-s1", (0, 0.0), **kw)
    return seen


def test_a_normal_member_turn_records_its_promise(monkeypatch):      # the negative control: the wiring is live
    assert len(_commit(monkeypatch)) == 1
    assert len(_commit(monkeypatch, replay=False)) == 1


def test_a_replay_turn_records_nothing(monkeypatch):
    assert _commit(monkeypatch, replay=True) == []


@pytest.mark.parametrize("replay,expected", [(True, 0), (False, 1)])
def test_the_streaming_path_threads_replay_isolation(monkeypatch, replay, expected):
    seen = []
    monkeypatch.setattr(commitments, "schedule_record", lambda *a, **k: seen.append(a) or True)
    monkeypatch.setattr(commitments, "mode", lambda: "shadow")

    async def inner():
        yield PROMISE

    async def drain():
        return [d async for d in brain_dispatch._provenance_tracked_stream(inner(), "remind me", "replay-s1", "member-1", replay)]

    assert asyncio.run(drain()) == [PROMISE]
    assert len(seen) == expected
