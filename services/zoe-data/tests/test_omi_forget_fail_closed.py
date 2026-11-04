"""Forgotten means forever: the day-review surface/apply paths fail CLOSED on a forget-ledger outage.

Before: ``omi_day_review._forgotten`` / ``omi_review_apply._floors`` / ``person_profile._forgotten`` used the lenient lookup,
so a real ledger read failure answered "nothing forgotten" and a forgotten name could be stored, applied or shown.
Synthetic names only.
"""
from __future__ import annotations

import asyncio
import contextlib

import pytest

import memory_forgotten as mf
import omi_day_review as dr
import omi_review_apply as apply
import person_profile as pp
from forgotten_support import SALT, ledger_env  # noqa: F401 - fixture (autouse)

pytestmark = pytest.mark.ci_safe

USER = "demo_bar_00000001"
NAME = "Zelda"


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _DownLedger(mf.MemoryBackend):
    async def active_hashes(self, user_id, now_iso):
        raise RuntimeError(f"ledger down {NAME}")        # must never reach an error string


def _outage():
    mf.reset_state()
    mf.set_backend(_DownLedger())


def _forget():
    run(mf.add(USER, NAME, actor=USER))


def _cand(text):
    return {"id": "c1", "kind": "owner_fact", "payload": {"fact": text}, "evidence": [{"quote": text}]}


def _lenient_spans(monkeypatch):
    """The OLD behaviour: spans() ignored ``strict`` and answered from an empty set on a ledger failure."""
    real = mf.spans

    async def old(user_id, text, *, near=True, strict=False):
        return await real(user_id, text, near=near, strict=False)
    monkeypatch.setattr(mf, "spans", old)


def test_spans_strict_raises_on_outage_and_lenient_stays_empty():
    _forget()
    _outage()
    with pytest.raises(mf.LedgerUnavailable) as ei:
        run(mf.spans(USER, f"met {NAME}", strict=True))
    assert NAME not in str(ei.value)
    assert run(mf.spans(USER, f"met {NAME}")) == []          # redaction (a write) stays lenient


def test_day_review_forgotten_hit_and_healthy():
    assert run(dr._forgotten(USER, f"met {NAME}")) is False  # healthy, nothing forgotten
    _forget()
    assert run(dr._forgotten(USER, f"met {NAME}")) is True
    assert run(dr._forgotten(USER, "bought bread")) is False


def test_day_review_forgotten_outage_fails_closed(monkeypatch, caplog):
    _forget()
    _outage()
    with caplog.at_level("DEBUG"):
        with pytest.raises(dr.LedgerUnavailable) as ei:
            run(dr._forgotten(USER, f"met {NAME}"))
    assert NAME not in str(ei.value) and NAME not in caplog.text
    # negative control: the old lenient lookup reads the same outage as "clean"
    _lenient_spans(monkeypatch)
    assert run(dr._forgotten(USER, f"met {NAME}")) is False


def test_apply_floors_outage_applies_nothing(monkeypatch):
    _forget()
    _outage()
    with pytest.raises(apply.ApplyError) as ei:
        run(apply._floors(USER, _cand(f"met {NAME}")))
    assert ei.value.code == "forget_ledger_unavailable" and ei.value.status == 503
    _lenient_spans(monkeypatch)                               # negative control: old behaviour lets it through
    run(apply._floors(USER, _cand(f"met {NAME}")))


def test_apply_floors_hit_still_withholds_and_healthy_passes(monkeypatch):
    import db_compat

    class _Db:
        async def execute(self, *a, **k):
            return None

        async def commit(self):
            return None

    @contextlib.asynccontextmanager
    async def fake_db():
        yield _Db()
    monkeypatch.setattr(db_compat, "get_compat_db", fake_db)
    run(apply._floors(USER, _cand("bought bread")))           # healthy, nothing forgotten
    _forget()
    with pytest.raises(apply.ApplyError) as ei:
        run(apply._floors(USER, _cand(f"met {NAME}")))
    assert ei.value.code == "forgotten" and ei.value.status == 410
    run(apply._floors(USER, _cand("bought bread")))           # unrelated text unchanged


def test_person_profile_forgotten_outage_hides_row():
    assert run(pp._forgotten(USER, NAME)) is False
    _forget()
    assert run(pp._forgotten(USER, NAME)) is True
    _outage()
    assert run(pp._forgotten(USER, "bought bread")) is True   # fail closed: an unreadable ledger hides the row
