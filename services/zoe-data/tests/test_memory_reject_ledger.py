"""Write-time rejections are counted, durable across a restart, and summarised nightly.

Class fixed (docs/knowledge/memory-loss-audit-2026-10-05.md, bucket b): the nightly digest and the
idle consolidation dropped gate rejects with no log line and no counter; other sites logged the
candidate TEXT; the Prometheus counter died with every restart. The ledger keeps COUNTS only.
"""

from __future__ import annotations

import asyncio
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


def test_counts_by_reason_and_source():
    for _ in range(2):
        led.record_reject("digest", "question_mark")
    led.record_reject("idle_consolidation", "weather_report")
    s = led.summary(1)
    assert s["rejected"] == 3
    assert s["reasons"] == {"question_mark": 2, "weather_report": 1}
    assert s["sources"] == {"digest": 2, "idle_consolidation": 1}


def test_a_restart_does_not_zero_the_day(ledger_file):
    led.record_reject("digest", "too_short")
    led.summary(1)                      # forces the flush the service does every 30 s
    led.reset_for_tests()               # a new process: empty memory, same file
    assert led.summary(1)["rejected"] == 1
    assert json.loads(ledger_file.read_text())[led._today()] == {"digest|too_short": 1}


def test_the_file_holds_counts_never_text(ledger_file):
    led.record_reject("digest", "My mum's name is Janice and she lives at 12 High St")  # a careless caller
    led.summary(1)
    raw = ledger_file.read_text()
    assert "Janice" not in raw and "High St" not in raw
    (key,) = json.loads(raw)[led._today()]
    assert len(key) <= 48 + 1 + 48 and " " not in key and "'" not in key


def test_nightly_line_is_logged_even_at_zero(caplog):
    with caplog.at_level(logging.INFO, logger="memory_reject_ledger"):
        line = led.log_nightly_summary(1)
    assert line.startswith("MEMORY_REJECT_SUMMARY window=1d rejected=0")
    assert any("MEMORY_REJECT_SUMMARY" in r.getMessage() for r in caplog.records)


def test_nightly_line_names_the_reasons(caplog):
    led.record_reject("digest", "question_mark")
    led.record_reject("digest", "question_mark")
    led.record_reject("voice_fact", "pii_reject")
    line = led.format_summary(1)
    assert "rejected=3" in line and "question_mark:2" in line and "pii_reject:1" in line
    assert "digest:2" in line and "voice_fact:1" in line


def test_old_days_age_out_of_the_window(ledger_file):
    ledger_file.write_text(json.dumps({"2000-01-01": {"digest|too_short": 9}}))
    led.record_reject("digest", "empty")
    assert led.summary(1)["rejected"] == 1
    assert led.summary(10_000)["rejected"] == 10   # still on file, outside the nightly window


def test_a_pytest_session_without_an_override_never_writes_the_home(monkeypatch):
    """Control for the conftest pin: with no ZOE_MEMORY_REJECT_LEDGER a non-service process keeps
    the counts in memory only (it must not create ~/.zoe/ files)."""
    monkeypatch.delenv("ZOE_MEMORY_REJECT_LEDGER")
    assert led._path() is None
    led.record_reject("digest", "empty")
    assert led.summary(1)["rejected"] == 1


def test_bookkeeping_failure_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("ZOE_MEMORY_REJECT_LEDGER", str(tmp_path / "nodir" / "x" / "ledger.json"))
    monkeypatch.setattr(led.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(OSError("ro fs")))
    led.record_reject("digest", "empty")            # must not raise into a write path


# ── the silent sites now record ──────────────────────────────────────────────────────────────

def test_digest_quality_gate_no_longer_drops_silently():
    import memory_digest
    assert memory_digest._passes_quality_gate("Do you remember my mum's name?") is False
    s = led.summary(1)
    assert s["rejected"] == 1 and s["sources"] == {"digest": 1}, s
    assert memory_digest._passes_quality_gate("My dad's name is Neil.") is True     # control
    assert led.summary(1)["rejected"] == 1


def test_service_refusals_feed_the_ledger(tmp_path):
    import memory_service
    svc = memory_service.MemoryService(data_dir=str(tmp_path / "scratch"))
    for status in ("pii_reject", "tombstone_drop", "opt_out", "dedup"):
        svc._bump(status, "voice_fact")
    svc._bump("ok", "voice_fact")           # control: a successful write is not a refusal
    svc._bump("error", "voice_fact")        # control: a failed write is loud elsewhere, not a reject
    s = led.summary(1)
    assert s["rejected"] == 4 and set(s["reasons"]) == {"pii_reject", "tombstone_drop", "opt_out", "dedup"}


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
    assert lines and "rejected=1" in lines[0] and "question_mark:1" in lines[0]
