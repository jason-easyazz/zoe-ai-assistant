"""Write-time rejections are counted, durable across a restart, summarised nightly, and counted once.

Class fixed (docs/knowledge/memory-loss-audit-2026-10-05.md, bucket b): the nightly digest and the idle
consolidation dropped gate rejects with no log line and no counter; other sites logged the candidate TEXT;
the Prometheus counter died with every restart. The ledger keeps COUNTS only, per UTC hour, over a rolling
24 h window, and never reports a false zero when the disk is unwritable.
"""

from __future__ import annotations

import asyncio
import datetime
import json
import logging

import pytest

import memory_reject_ledger as led

pytestmark = pytest.mark.ci_safe


@pytest.fixture(autouse=True)
def ledger_file(tmp_path, monkeypatch):
    path = tmp_path / "ledger.json"
    monkeypatch.setenv("ZOE_MEMORY_REJECT_LEDGER", str(path))
    led.reset_for_tests()
    yield path
    led.reset_for_tests()


def _freeze(monkeypatch, iso):
    now = datetime.datetime.fromisoformat(iso).replace(tzinfo=datetime.timezone.utc)
    monkeypatch.setattr(led, "_now", lambda: now)
    return now


def test_counts_by_reason_and_source():
    for _ in range(2):
        led.record_reject("digest", "question_mark")
    led.record_reject("idle_consolidation", "weather_report")
    s = led.summary(24)
    assert s["rejected"] == 3 and s["persist_failed"] is False
    assert s["reasons"] == {"question_mark": 2, "weather_report": 1}
    assert s["sources"] == {"digest": 2, "idle_consolidation": 1}


def test_a_restart_does_not_zero_the_window(ledger_file):
    led.record_reject("digest", "too_short")
    led.summary(24)                     # forces the flush the service does every 30 s
    led.reset_for_tests()               # a new process: empty memory, same file
    assert led.summary(24)["rejected"] == 1
    (hour_bucket,) = json.loads(ledger_file.read_text()).values()
    assert hour_bucket == {"digest|too_short": 1}


def test_the_file_holds_counts_never_text(ledger_file):
    led.record_reject("digest", "My mum's name is Janice and she lives at 12 High St")  # a careless caller
    led.summary(24)
    raw = ledger_file.read_text()
    assert "Janice" not in raw and "High St" not in raw
    (bucket,) = json.loads(raw).values()
    (key,) = bucket
    assert len(key) <= 48 + 1 + 48 and " " not in key and "'" not in key


def test_nightly_line_is_logged_even_at_zero(caplog):
    with caplog.at_level(logging.INFO, logger="memory_reject_ledger"):
        line = led.log_nightly_summary(24)
    assert line.startswith("MEMORY_REJECT_SUMMARY window=24h rejected=0") and "persist_failed" not in line
    assert any("MEMORY_REJECT_SUMMARY" in r.getMessage() for r in caplog.records)


def test_nightly_line_names_the_reasons():
    led.record_reject("digest", "question_mark")
    led.record_reject("digest", "question_mark")
    led.record_reject("voice_fact", "pii_reject", gate=False)
    line = led.format_summary(24)
    assert "rejected=3" in line and "question_mark:2" in line and "pii_reject:1" in line
    assert "digest:2" in line and "voice_fact:1" in line


def test_the_window_is_the_last_24_hours_not_the_utc_date(monkeypatch):
    """The nightly run fires at 03:00 Perth = 19:00 UTC. A date-keyed window reported 'the UTC day so far'
    and the 19:00–24:00 UTC band of every day appeared in NO summary. Frozen at 19:00 UTC."""
    _freeze(monkeypatch, "2026-10-05T21:30:00")                 # 05:30 Perth, still on the 05th UTC
    led.record_reject("digest", "late_evening_utc")             # 21:30 UTC on the 5th
    _freeze(monkeypatch, "2026-10-05T23:50:00")
    led.record_reject("digest", "just_before_midnight_utc")
    _freeze(monkeypatch, "2026-10-06T19:00:00")                 # next nightly run, 19:00 UTC on the 6th
    s = led.summary(24)
    assert s["reasons"] == {"late_evening_utc": 1, "just_before_midnight_utc": 1}, \
        "the band between the previous run and the UTC date change must still be in the window"
    _freeze(monkeypatch, "2026-10-07T23:00:00")                 # control: well past 24 h → aged out
    assert led.summary(24)["rejected"] == 0


def test_old_hours_age_out_of_the_window_but_stay_on_file(ledger_file):
    ledger_file.write_text(json.dumps({"2000-01-01T00": {"digest|too_short": 9}}))
    led.record_reject("digest", "empty")
    assert led.summary(24)["rejected"] == 1
    assert led.summary(24 * 365 * 100)["rejected"] == 10


def test_a_failed_persist_keeps_the_counts_and_says_so(monkeypatch, tmp_path):
    """A read-only HOME / full disk used to clear the in-memory delta and report rejected=0 — exactly the
    false zero the ledger exists to prevent."""
    monkeypatch.setenv("ZOE_MEMORY_REJECT_LEDGER", str(tmp_path / "nodir" / "x" / "ledger.json"))
    monkeypatch.setattr(led.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("ro fs")))
    for _ in range(5):
        led.record_reject("digest", "question_mark")
    s = led.summary(24)
    assert s["rejected"] == 5 and s["persist_failed"] is True
    assert led.format_summary(24).endswith("rejected=5 reasons=question_mark:5 sources=digest:5 persist_failed=1")


def test_the_delta_is_flushed_once_the_disk_recovers(monkeypatch, tmp_path):
    path = tmp_path / "later" / "ledger.json"
    monkeypatch.setenv("ZOE_MEMORY_REJECT_LEDGER", str(path))
    real = led.os.makedirs
    monkeypatch.setattr(led.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("ro fs")))
    led.record_reject("digest", "question_mark")
    assert led.summary(24)["persist_failed"] is True
    monkeypatch.setattr(led.os, "makedirs", real)
    s = led.summary(24)
    assert s["rejected"] == 1 and s["persist_failed"] is False       # merged, not lost, not double-counted
    assert sum(sum(b.values()) for b in json.loads(path.read_text()).values()) == 1


def test_a_pytest_session_without_an_override_never_writes_the_home(monkeypatch):
    """Control for the conftest pin: with no ZOE_MEMORY_REJECT_LEDGER a non-service process keeps the counts
    in memory only (it must not create ~/.zoe/ files)."""
    monkeypatch.delenv("ZOE_MEMORY_REJECT_LEDGER")
    assert led._path() is None
    led.record_reject("digest", "empty")
    assert led.summary(24)["rejected"] == 1


def test_bookkeeping_failure_never_raises(monkeypatch):
    monkeypatch.setattr(led, "_hour", lambda _d: (_ for _ in ()).throw(RuntimeError("boom")))
    led.record_reject("digest", "empty")            # must not raise into a write path


# ── the silent sites now record ──────────────────────────────────────────────────────────────

def test_digest_quality_gate_no_longer_drops_silently():
    import memory_digest
    assert memory_digest._passes_quality_gate("Do you remember my mum's name?") is False
    s = led.summary(24)
    assert s["rejected"] == 1 and s["sources"] == {"digest": 1}, s
    assert memory_digest._passes_quality_gate("My dad's name is Neil.") is True     # control
    assert led.summary(24)["rejected"] == 1


def test_nightly_pass_emits_the_summary_line(monkeypatch, caplog):
    import memory_digest

    async def _results(db=None, cutoff=None):
        return []

    async def _age(db=None, cutoff=None):
        return 1.0

    monkeypatch.setattr(memory_digest, "run_digest_for_all_active_users", _results)
    monkeypatch.setattr(memory_digest, "newest_owned_user_turn_age_hours", _age)
    led.record_reject("digest", "question_mark")
    with caplog.at_level(logging.INFO, logger="memory_reject_ledger"):
        asyncio.run(memory_digest.run_nightly_digest_pass())
    lines = [r.getMessage() for r in caplog.records if "MEMORY_REJECT_SUMMARY" in r.getMessage()]
    assert lines and "window=24h" in lines[0] and "rejected=1" in lines[0] and "question_mark:1" in lines[0]
