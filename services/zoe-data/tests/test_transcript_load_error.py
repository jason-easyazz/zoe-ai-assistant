"""A failed transcript READ is never a quiet day.

2026-10-12: ``zoe-night-mind.py --date D`` read 0 turns for every member because the loader swallowed a bind error and returned ``""``; the pass called that
``quiet_day``. The loaders now raise ``memory_digest.TranscriptLoadError``; the night mind reports ``status: load_error`` (own counter, non-zero exit), the digest
row carries ``error: load_failed:<Type>`` (a processing error); a genuinely empty day is still ``""`` / ``quiet_day``.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import pathlib
import sys

import pytest

pytestmark = pytest.mark.ci_safe

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import memory_digest as md  # noqa: E402
import night_mind as nm  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[3]


def _cli():
    spec = importlib.util.spec_from_file_location("zoe_night_mind_cli_loaderr", REPO / "scripts" / "maintenance" / "zoe-night-mind.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Cur:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows


class _Db:
    """``execute`` raises when ``boom`` is set (a pool / bind / query failure), else returns ``rows``."""

    def __init__(self, rows=(), boom=None):
        self.rows, self.boom = list(rows), boom

    async def execute(self, sql, params=()):
        if self.boom:
            raise self.boom
        return _Cur(self.rows)


@pytest.fixture(autouse=True)
def _night_mind_on(monkeypatch):
    monkeypatch.setenv("ZOE_NIGHT_MIND", "shadow")


# ── the loaders ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_a_failing_rolling_read_raises_not_empty():
    with pytest.raises(md.TranscriptLoadError) as ei:
        asyncio.run(md._load_todays_messages("u1", db=_Db(boom=ConnectionError("pool gone"))))
    assert ei.value.kind == "ConnectionError" and str(ei.value) == "load_failed:ConnectionError"
    assert "pool gone" not in str(ei.value)                                    # the class only, never the driver's text


def test_a_failing_dated_read_raises_not_empty():
    with pytest.raises(md.TranscriptLoadError) as ei:
        asyncio.run(md.load_day_messages("u1", "2026-10-10T00:00:00+08:00", "2026-10-11T00:00:00+08:00", db=_Db(boom=TypeError("bind"))))
    assert ei.value.kind == "TypeError"


def test_a_genuinely_empty_day_is_still_empty_string():
    assert asyncio.run(md._load_todays_messages("u1", db=_Db(rows=[]))) == ""
    assert asyncio.run(md.load_day_messages("u1", "2026-10-10T00:00:00+08:00", "2026-10-11T00:00:00+08:00", db=_Db(rows=[]))) == ""


# ── the night mind CLI ──────────────────────────────────────────────────────────────────────────────────────────────────────────

def _member(cli, loader):
    cfg = nm.config_from_env()
    return asyncio.run(cli.reflect_member("u1", loader, nm, None, cfg, "shadow", "2026-10-10"))


def test_a_loader_that_raises_is_load_error_not_quiet_day():
    cli = _cli()

    async def failing(_uid):
        raise md.TranscriptLoadError(TypeError("bind"))
    res = _member(cli, failing)
    assert res["status"] == "load_error" and res["error"] == "TypeError"
    assert res.get("skipped_reason") is None and res["turns_in"] == 0 and res["written"] == 0 and res["calls"] == 0


def test_an_empty_read_stays_quiet_day():
    cli = _cli()

    async def empty(_uid):
        return ""
    res = _member(cli, empty)
    assert res["status"] == "skipped" and res["skipped_reason"] == "quiet_day"


def test_negative_control_the_old_swallowing_loader_reads_as_quiet_day():
    """What the swallow did: a loader that returns ``""`` on failure is indistinguishable from a quiet day. If this ever shows load_error the control no longer controls."""
    cli = _cli()

    async def swallowed(_uid):
        return ""
    assert _member(cli, swallowed)["status"] == "skipped"


def test_totals_count_load_errors_apart_from_quiet_days():
    cli = _cli()
    t = cli.totals([{"status": "load_error"}, {"status": "skipped", "skipped_reason": "quiet_day"}, {"status": "ran"}])
    assert t["load_errors"] == 1 and t["members"] == 3


def _run_cli(monkeypatch, cli, members):
    async def probe(_cfg):
        return True, ""

    async def run_members(_args, _cfg, _mode):
        return {"members": members}
    monkeypatch.setattr(nm, "probe_model", probe)
    monkeypatch.setattr(cli, "run_members", run_members)
    return asyncio.run(cli.amain(["--all-members", "--model-url", "http://127.0.0.1:1/v1"]))


def test_the_cli_exits_non_zero_when_any_member_failed_to_load(monkeypatch):
    cli = _cli()
    code, summary = _run_cli(monkeypatch, cli, [{"status": "skipped", "skipped_reason": "quiet_day"}, {"status": "load_error", "error": "TypeError"}])
    assert code == 1 and summary["status"] == "load_error" and summary["totals"]["load_errors"] == 1


def test_the_cli_exits_zero_on_an_all_quiet_night(monkeypatch):
    cli = _cli()
    code, summary = _run_cli(monkeypatch, cli, [{"status": "skipped", "skipped_reason": "quiet_day"}])
    assert code == 0 and summary["status"] == "ok" and summary["totals"]["load_errors"] == 0


# ── the digest ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────

def test_the_digest_row_carries_load_failed_and_counts_as_a_processing_error(monkeypatch):
    import memory_metrics

    async def boom(_uid, _db=None):
        raise md.TranscriptLoadError(TypeError("bind"))
    monkeypatch.setattr(md, "_load_todays_messages", boom)
    row = asyncio.run(md.run_memory_digest("u1"))
    assert row.get("error") == "load_failed:TypeError" and row.get("skipped_reason") is None
    counts = memory_metrics._classify_rows([row])
    assert counts["processing_errors"] == 1 and counts["extractor_errors"] == 0 and counts["skipped"] == 0
