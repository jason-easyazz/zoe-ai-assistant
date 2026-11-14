"""A6 step 1: an event-loop block names the code that held it (``loop_block_stacks``), and nothing else.

A real loop is blocked with ``time.sleep`` inside a known function while ``event_loop_lag.monitor`` runs and the watcher
thread samples the loop thread. Controls: two different blockers get different ``top``/``fp`` (the attribution is not a
constant), and the planted sentence IS reachable from the blocked frame's locals (so its absence from every record, DEBUG
included, means the reducer dropped it, not that the test could never see it).
"""
from __future__ import annotations

import asyncio
import logging
import re
import sys
import time

import pytest

import event_loop_lag
import loop_block_stacks as lbs

pytestmark = pytest.mark.ci_safe

HERE = "tests/test_loop_block_stacks.py"
PLANTED = "Quillon Marsh told me his sister is expecting twins"  # synthetic
LAG_RE = re.compile(r"^EVENT_LOOP_LAG \d+\.\d{3}s: something blocked the single zoe-data worker \(every request, push "
                    r"socket and voice stream waited\); \d+ sample\(s\) >= 0\.25s since start$")


def _block_here(s: float) -> None:
    time.sleep(s)


def _block_other(s: float) -> None:
    time.sleep(s)


def _block_with_secret(sentence: str, s: float) -> None:
    said = sentence.upper()  # noqa: F841 - a local holding what a person said
    time.sleep(s)


def _two_calls() -> None:
    _block_here(0.45)
    _block_other(0.6)


@pytest.fixture
def watcher(monkeypatch, caplog):
    monkeypatch.setenv("ZOE_LOOP_BLOCK_STACKS", "1")
    caplog.set_level(logging.WARNING, logger="event_loop_lag")
    caplog.set_level(logging.DEBUG, logger="loop_block_stacks")  # last: set_level also sets the handler level
    lbs.stop()
    assert lbs.start(summary_every_s=1e9)
    yield lbs._watcher
    lbs.stop()


def _lines(caplog, marker="EVENT_LOOP_BLOCK "):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(marker)]


def _field(line: str, name: str) -> str:
    return re.search(rf"\b{name}=(\S+)", line).group(1)


async def _blocked(caplog, fn, *args, expect: int = 1) -> list[str]:
    event_loop_lag.reset_for_test()
    task = asyncio.create_task(event_loop_lag.monitor(0.02))
    # The watcher idles in a 0.5 s wait while the monitor has no thread id (reset_for_test clears it). A block that starts and
    # ends inside that one wait is reported fp=unseen, so the blocker starts only once the watcher has had time to see the id.
    for _ in range(200):
        if event_loop_lag._beat.tid is not None:
            break
        await asyncio.sleep(0.01)
    assert event_loop_lag._beat.tid is not None
    await asyncio.sleep(0.6)
    fn(*args)
    for _ in range(40):  # the watcher logs once it sees the loop tick again
        await asyncio.sleep(0.05)
        if len(_lines(caplog)) >= expect:
            break
    await asyncio.sleep(0.1)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    return _lines(caplog)


async def test_block_names_the_function_and_file_and_lag_line_is_unchanged(watcher, caplog):
    lines = await _blocked(caplog, _block_here, 0.4)
    assert len(lines) == 1, lines
    assert _field(lines[0], "top").startswith(f"{HERE}:_block_here:"), lines
    assert _field(lines[0], "via") == "-" and int(_field(lines[0], "ms")) >= 300
    assert re.fullmatch(r"[0-9a-f]{8}", _field(lines[0], "fp"))
    lag = [r.getMessage() for r in caplog.records if r.getMessage().startswith("EVENT_LOOP_LAG")]
    assert lag and all(LAG_RE.match(m) for m in lag), lag
    assert lbs.snapshot()[0]["count"] == 1


async def test_negative_control_a_different_blocker_gets_a_different_name(watcher, caplog):
    a = (await _blocked(caplog, _block_here, 0.4))[0]
    b = (await _blocked(caplog, _block_other, 0.4, expect=2))[1]
    assert ":_block_other:" in _field(b, "top") and ":_block_here:" not in b
    assert _field(a, "fp") != _field(b, "fp")


async def test_third_party_call_is_attributed_via_its_package(watcher, caplog, tmp_path, monkeypatch):
    pkg = tmp_path / "site-packages" / "fakepkg_zz"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("import time\n\ndef slow_call(s):\n    time.sleep(s)\n")
    monkeypatch.syspath_prepend(str(tmp_path / "site-packages"))
    import fakepkg_zz

    def _calls_lib() -> None:
        fakepkg_zz.slow_call(0.4)

    try:
        lines = await _blocked(caplog, _calls_lib)
    finally:
        sys.modules.pop("fakepkg_zz", None)
    assert _field(lines[0], "via") == "fakepkg_zz", lines
    assert ":_calls_lib:" in _field(lines[0], "top")


async def test_no_local_or_argument_reaches_any_record_even_at_debug(watcher, caplog):
    lines = await _blocked(caplog, _block_with_secret, PLANTED, 0.4)
    assert ":_block_with_secret:" in _field(lines[0], "top")
    assert any(r.getMessage().startswith("EVENT_LOOP_BLOCK_FRAMES") for r in caplog.records)  # DEBUG ran
    text = " ".join(r.getMessage() for r in caplog.records) + repr(lbs.snapshot())
    assert "Quillon" not in text and "QUILLON" not in text and "twins" not in text.lower()


def test_negative_control_the_planted_sentence_is_reachable_from_the_frame():
    seen = []

    def _peek(sentence: str) -> None:
        said = sentence.upper()  # noqa: F841
        seen.append(repr(sys._getframe().f_locals))  # what a leaky reducer would log

    _peek(PLANTED)
    assert "QUILLON" in seen[0]


async def test_long_block_one_line_second_sample_same_or_moved(watcher, caplog, monkeypatch):
    monkeypatch.setattr(lbs, "SECOND_S", 0.3)
    same = await _blocked(caplog, _block_here, 0.8)
    assert len(same) == 1 and _field(same[0], "at1s") == "same", same
    moved = (await _blocked(caplog, _two_calls, expect=2))[1:]
    assert len(moved) == 1 and _field(moved[0], "at1s") == "moved", moved
    assert ":_block_here:" in _field(moved[0], "top") and ":_block_other:" in _field(moved[0], "top1s")


async def test_healthy_loop_logs_nothing(watcher, caplog):
    event_loop_lag.reset_for_test()
    task = asyncio.create_task(event_loop_lag.monitor(0.02))
    await asyncio.sleep(0.6)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert _lines(caplog) == [] and lbs.snapshot() == []


async def test_flag_off_adds_nothing(monkeypatch, caplog):
    lbs.stop()
    monkeypatch.setenv("ZOE_LOOP_BLOCK_STACKS", "0")
    caplog.set_level(logging.DEBUG)
    assert lbs.start() is False and lbs._watcher is None
    lines = await _blocked(caplog, _block_here, 0.4, expect=99)
    assert lines == [] and any(r.getMessage().startswith("EVENT_LOOP_LAG") for r in caplog.records)


def test_table_is_bounded_and_summary_lists_top_five(caplog):
    w = lbs._Watcher(summary_every_s=0.0)
    caplog.set_level(logging.WARNING, logger="loop_block_stacks")
    for i in range(100):
        w._emit(0.3 + i / 1000, {"top": f"x.py:f{i}:1", "via": "-", "depth": 3, "fp": f"{i:08x}", "frames": []}, None)
    assert len(w.table) == lbs.MAX_FPS + 1 and w.table["other"]["count"] == 100 - lbs.MAX_FPS
    w._maybe_summary()
    top = _lines(caplog, "EVENT_LOOP_BLOCK_TOP ")
    assert len(top) == 5 and all(_field(t, "n") == "100" for t in top)
    w._maybe_summary()  # nothing new since: no second summary
    assert len(_lines(caplog, "EVENT_LOOP_BLOCK_TOP ")) == 5


def test_unsampled_lag_is_counted_as_unseen(caplog):
    caplog.set_level(logging.WARNING, logger="loop_block_stacks")
    w = lbs._Watcher()
    event_loop_lag._beat.big = (event_loop_lag._beat.big[0] + 1, 0.6)
    w._step()
    lines = _lines(caplog)
    assert len(lines) == 1 and _field(lines[0], "fp") == "unseen" and _field(lines[0], "ms") == "600"


async def test_clean_shutdown(watcher):
    task = asyncio.create_task(event_loop_lag.monitor(0.02))
    await asyncio.sleep(0.05)
    assert event_loop_lag._beat.tid is not None
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert event_loop_lag._beat.tid is None
    t = time.monotonic()
    lbs.stop()
    assert not watcher.is_alive() and time.monotonic() - t < 1.5
