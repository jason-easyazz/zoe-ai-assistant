"""memory_views.report: every pointer view for one member on a synthetic household - counts and ids, never a word of what was said.

The leak check is the load-bearing one: the report is what the owner reads before switching any view on, and it must be safe to
paste anywhere. Its negative control plants the same distinctive words in every input and shows they come back nowhere."""
from __future__ import annotations

import asyncio
import datetime as dt
import importlib.util
import json
import pathlib
import sys

import pytest

import memory_episodes
import memory_views
import night_store
from dormant_threads import Signal
from memory_service import MemoryRef

pytestmark = pytest.mark.ci_safe

UTC = dt.timezone.utc
NOW = dt.datetime(2026, 10, 30, 12, 0, tzinfo=UTC).timestamp()
D = 86400.0
STATED = {"authority": "user_stated", "authority_class": "user_stated", "source": "chat_regex", "status": "approved"}
INFERRED = {"authority": "inferred", "authority_class": "model_from_transcript", "source": "digest", "status": "approved"}
SECRET_WORDS = ("Zanzibarite", "Quillfeather", "Obsidianvale")


def _rows():
    return [MemoryRef("r-old", f"User works at {SECRET_WORDS[0]}", {**STATED, "added_ts": NOW - 120 * D}),
            MemoryRef("r-new", "User works at Globex", {**INFERRED, "added_ts": NOW - 2 * D})]


def _store():
    store = night_store.MemoryBackend()
    asyncio.run(store.put_thread(night_store.thread_row(id="t-1", user_id="member-a", title=f"the {SECRET_WORDS[1]} shed",
                                                        anchors=f"{SECRET_WORDS[1].lower()} shed primer", status="quiet",
                                                        last_day="2026-08-01")))
    return store


async def _pointers(user_id, start, end):
    base = dt.datetime(2026, 10, 29, 9, 0, tzinfo=UTC).timestamp()
    return [memory_episodes.TurnPointer(f"m{i}", "s1", base + i * 600) for i in range(3)] + \
           [memory_episodes.TurnPointer("m9", "s2", base + 6 * 3600)]


async def _signals(user_id, start, end):
    return [Signal("turn", "m-today", f"bought {SECRET_WORDS[1]} primer for the shed, {SECRET_WORDS[2]} style", "2026-10-30")]


def _report(**kw):
    args = dict(now=NOW, tz=UTC, pointers=_pointers, signals=_signals, threads_backend=_store())
    args.update(kw)
    return asyncio.run(memory_views.report("member-a", _rows(), **args))


def test_report_runs_every_view():
    rep = _report()
    assert rep["slots"]["held"] == ["r-new"] and rep["slots"]["still_true_due"] == ["r-old"]
    assert rep["episodes"]["count"] == 2 and rep["episodes"]["turns"] == 4 and rep["episodes"]["longest_turns"] == 3
    assert rep["dormant"]["dormant"] == 1 and rep["dormant"]["signals"] == 1
    assert [c["thread_id"] for c in rep["dormant"]["candidates"]] == ["t-1"]


def test_report_never_carries_text():
    flat = json.dumps(_report())
    for w in SECRET_WORDS + ("Globex", "primer", "shed"):
        assert w not in flat and w.lower() not in flat
    # negative control: the words really were in the inputs the views read
    planted = json.dumps([r.text for r in _rows()] + [s.text for s in asyncio.run(_signals("member-a", None, None))])
    assert all(w in planted for w in SECRET_WORDS)


def test_a_failing_read_is_reported_and_the_other_views_still_run():
    async def broken(*_a):
        raise ConnectionError("db down")

    rep = _report(pointers=broken, signals=broken)
    assert rep["episodes"] == {"error": "ConnectionError"} and rep["dormant"] == {"error": "ConnectionError"}
    assert rep["slots"]["held"] == ["r-new"]


# ── the CLI ───────────────────────────────────────────────────────────────────

def _cli():
    path = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "maintenance" / "memory_views_report.py"
    spec = importlib.util.spec_from_file_location("memory_views_report", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["memory_views_report"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_cli_refuses_without_a_member(capsys):
    assert _cli().main([]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "refused"


def test_cli_member_rows_keeps_only_that_member():
    rows = [{"id": "a", "user_id": "member-a", "text": "x", "meta": {"k": 1}}, {"id": "b", "user_id": "member-b", "text": "y", "meta": {}}]
    assert _cli().member_rows(rows, "member-a") == [{"id": "a", "text": "x", "metadata": {"k": 1}}]
