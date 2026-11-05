"""The recall gate's opt-in private trace (``recall_gate_trace.py``, ``ZOE_RECALL_GATE_TRACE``, default off).

Synthetic data only (``demo_bar_*`` ids, invented names). Each property has a negative control: off by default (nothing written, no
directory), on writes row text + the owner's words, 0700 / 0600 permissions (and a looser pre-existing file is tightened), rotation
past the size cap, retention past the day cap, a skipped decision writes nothing, a write failure never reaches the gate, and the trace
never touches the RECALL_GATE log line.
"""
from __future__ import annotations

import json
import logging
import os
import stat
import time

import pytest

pytestmark = pytest.mark.ci_safe

import recall_gate as rg
import recall_gate_trace as tr
import restraint
from memory_service import MemoryRef

UID = "demo_bar_00000001"
T0 = 1_760_000_000.0
WALK = "User walks their kelpie Juniper along the river every morning at 6am."
BOOKS = "User is reading a long novel about lighthouse keepers."
FILLER = "User had porridge with banana for breakfast."


def ref(i, text):
    md = {"status": "approved", "user_id": UID, "memory_type": "fact", "added_ts": T0, "authority_class": "user_stated"}
    return MemoryRef(id=i, text=text, metadata=md)


class Svc:
    def __init__(self, pool):
        self.pool = list(pool)

    async def load_durable_for_hop(self, user_id):
        return list(self.pool)


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    for k in (rg.ENV, tr.ENV, tr.DAYS_ENV, "ZOE_RESTRAINT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(tr, "TRACE_DIR", tmp_path / "trace")
    rg._reset_state()
    restraint._reset_state()

    async def no_mutes(_uid):
        return []

    monkeypatch.setattr(restraint, "list_mutes", no_mutes)
    monkeypatch.setattr(rg, "_off_record", lambda uid, text: False)
    yield
    rg._reset_state()


def _live():
    return tr.TRACE_DIR / tr.FILE_NAME


async def _decide(message="How is Juniper getting on?", *, floor=(FILLER,)):
    """A decision whose floor presented a filler row (dropped) and whose pool carries the Juniper row (added)."""
    walk, books = ref("zoe_demo_walk0001", WALK), ref("zoe_demo_book0001", BOOKS)
    fill = [ref(f"zoe_demo_fill{i:04d}", t) for i, t in enumerate(floor)]
    return await rg.evaluate_packet(Svc([walk, books] + fill), UID, message, facts=fill, hits=[], recent=None,
                                    presented=[r.id for r in fill], mood=False, now=T0, trace_ep=rg.trace_epoch())


async def test_off_by_default_writes_nothing_and_creates_no_directory():
    d = await _decide()
    assert d.added and d.dropped                     # the decision itself is non-trivial: there WAS something to trace
    assert not tr.enabled()
    assert not tr.TRACE_DIR.exists()


@pytest.mark.parametrize("value", ["0", "off", "false", "", "shadow", "enforce"])
async def test_only_an_explicit_on_value_enables_it(monkeypatch, value):
    monkeypatch.setenv(tr.ENV, value)
    await _decide()
    assert not tr.TRACE_DIR.exists()


async def test_on_writes_the_owner_words_and_the_added_and_dropped_rows_text(monkeypatch):
    monkeypatch.setenv(tr.ENV, "1")
    d = await _decide()
    lines = _live().read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["user"] == UID and row["surface"] == "packet" and row["mode"] == "shadow"
    assert row["owner_words"] == "How is Juniper getting on?"
    assert {a["id"] for a in row["added"]} == set(d.added)
    assert any(a["text"] == WALK for a in row["added"])
    assert [x["text"] for x in row["dropped"]] == [FILLER]
    assert row["selected"] == d.ids


async def test_file_is_0600_and_directory_0700_and_a_looser_existing_file_is_tightened(monkeypatch):
    monkeypatch.setenv(tr.ENV, "on")
    tr.TRACE_DIR.mkdir(mode=0o755)
    _live().write_text("")
    os.chmod(_live(), 0o644)
    os.chmod(tr.TRACE_DIR, 0o755)
    await _decide()
    assert stat.S_IMODE(os.stat(_live()).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(tr.TRACE_DIR).st_mode) == 0o700


async def test_a_skipped_decision_writes_nothing(monkeypatch):
    monkeypatch.setenv(tr.ENV, "1")
    monkeypatch.setattr(rg, "_off_record", lambda uid, text: True)
    d = await _decide()
    assert d.skipped == "off_record"
    assert not _live().exists()


def _dec():
    p = rg.Pick("zoe_demo_walk0001", WALK, 3.0, 4, "pool", (), 10)
    return rg.Decision("packet", 1, (p,), ("zoe_demo_walk0001",), (), 10, 400)


def test_rotation_past_the_size_cap_keeps_at_most_keep_files(monkeypatch):
    monkeypatch.setenv(tr.ENV, "1")
    monkeypatch.setattr(tr, "MAX_BYTES", 300)
    for _ in range(40):
        assert tr.record(UID, _dec(), "how is Juniper", [], mode="shadow", ep=tr.epoch())
    names = sorted(p.name for p in tr.TRACE_DIR.iterdir())
    assert names == [tr.FILE_NAME] + [f"{tr.FILE_NAME}.{i}" for i in range(1, tr.KEEP + 1)]
    for p in tr.TRACE_DIR.iterdir():
        assert p.stat().st_size < 300 + 600         # each file stopped growing at the cap (one line over, at most)
    # negative control: an unlimited cap never rotates
    monkeypatch.setattr(tr, "MAX_BYTES", 10**9)
    for p in list(tr.TRACE_DIR.iterdir()):
        p.unlink()
    for _ in range(40):
        tr.record(UID, _dec(), "how is Juniper", [], mode="shadow", ep=tr.epoch())
    assert [p.name for p in tr.TRACE_DIR.iterdir()] == [tr.FILE_NAME]


def test_retention_deletes_files_older_than_the_day_cap(monkeypatch):
    monkeypatch.setenv(tr.ENV, "1")
    monkeypatch.setenv(tr.DAYS_ENV, "2")
    tr.TRACE_DIR.mkdir(mode=0o700)
    old, fresh = tr.TRACE_DIR / f"{tr.FILE_NAME}.2", tr.TRACE_DIR / f"{tr.FILE_NAME}.1"
    old.write_text("old\n")
    fresh.write_text("fresh\n")
    now = time.time()
    os.utime(old, (now - 3 * 86400, now - 3 * 86400))
    os.utime(fresh, (now - 1 * 86400, now - 1 * 86400))
    tr.record(UID, _dec(), "how is Juniper", [], mode="shadow", ep=tr.epoch(), now=now)
    assert not old.exists() and fresh.exists() and _live().exists()


def test_an_abandoned_live_file_past_retention_is_replaced_not_appended(monkeypatch):
    monkeypatch.setenv(tr.ENV, "1")
    tr.TRACE_DIR.mkdir(mode=0o700)
    _live().write_text('{"stale": true}\n')
    now = time.time()
    os.utime(_live(), (now - 30 * 86400, now - 30 * 86400))
    tr.record(UID, _dec(), "how is Juniper", [], mode="shadow", ep=tr.epoch(), now=now)
    assert "stale" not in _live().read_text()


def _snap(d):
    return (d.surface, d.turn, d.ids, d.added, d.dropped, d.budget_used, d.budget, d.held, d.cooled, d.delayed, d.skipped,
            [(p.id, p.score, p.reasons) for p in d.selected])


@pytest.mark.parametrize("trace", ["off", "on", "failing"])
async def test_the_trace_never_changes_the_decision(monkeypatch, trace):
    """The whole decision (ids, order, adds, drops, budget, counters, scores) is identical with the trace off, on, and failing."""
    async def three_turns():
        rg._reset_state()
        restraint._reset_state()
        return [_snap(await rg.evaluate_packet(Svc([ref("zoe_demo_walk0001", WALK), ref("zoe_demo_book0001", BOOKS),
                                                    ref("zoe_demo_fill0000", FILLER)]), UID, m,
                                               facts=[ref("zoe_demo_fill0000", FILLER)], hits=[], recent=None,
                                               presented=["zoe_demo_fill0000"], mood=False, now=T0 + k, session_id="s",
                                               trace_ep=rg.trace_epoch()))
                for k, m in enumerate(("How is Juniper getting on?", "and the lighthouse novel?", "thanks"))]

    baseline = await three_turns()
    if trace != "off":
        monkeypatch.setenv(tr.ENV, "1")
    if trace == "failing":
        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(tr, "_append", boom)
    assert await three_turns() == baseline
    assert _live().exists() is (trace == "on")
    if trace == "failing":
        assert tr.record(UID, _dec(), "x", [], mode="shadow", ep=tr.epoch()) is False


def test_a_symlinked_trace_directory_or_file_is_refused_and_nothing_reaches_the_target(monkeypatch, tmp_path):
    monkeypatch.setenv(tr.ENV, "1")
    target = tmp_path / "elsewhere"
    target.mkdir()
    tr.TRACE_DIR.symlink_to(target, target_is_directory=True)
    assert tr.record(UID, _dec(), "how is Juniper", [], mode="shadow", ep=tr.epoch()) is False
    assert list(target.iterdir()) == []
    with pytest.raises(OSError):
        tr.erase_all()                                        # the erasers refuse it too (fail closed, never follow)
    tr.TRACE_DIR.unlink()
    tr.TRACE_DIR.mkdir(mode=0o700)
    victim = tmp_path / "victim.txt"
    victim.write_text("")
    _live().symlink_to(victim)
    assert tr.record(UID, _dec(), "how is Juniper", [], mode="shadow", ep=tr.epoch()) is False
    assert victim.read_text() == ""
    # negative control: a plain directory and file are written
    _live().unlink()
    assert tr.record(UID, _dec(), "how is Juniper", [], mode="shadow", ep=tr.epoch()) is True


def test_a_symlinked_ancestor_is_refused_and_nothing_reaches_the_target(monkeypatch, tmp_path):
    monkeypatch.setenv(tr.ENV, "1")
    real = tmp_path / "real-zoe"
    real.mkdir()
    (tmp_path / "zoe-link").symlink_to(real, target_is_directory=True)
    monkeypatch.setattr(tr, "TRACE_DIR", tmp_path / "zoe-link" / "recall-gate-trace")
    assert tr.record(UID, _dec(), "how is Juniper", [], mode="shadow", ep=tr.epoch()) is False
    assert list(real.iterdir()) == []
    with pytest.raises(OSError):
        tr.erase_all()
    monkeypatch.setattr(tr, "TRACE_DIR", real / "recall-gate-trace")    # negative control: the real path is written
    assert tr.record(UID, _dec(), "how is Juniper", [], mode="shadow", ep=tr.epoch()) is True


def test_a_retention_failure_abandons_the_write(monkeypatch):
    monkeypatch.setenv(tr.ENV, "1")
    tr.TRACE_DIR.mkdir(mode=0o700)
    old = tr.TRACE_DIR / f"{tr.FILE_NAME}.2"
    old.write_text("old\n")
    now = time.time()
    os.utime(old, (now - 30 * 86400, now - 30 * 86400))
    real_unlink = type(old).unlink

    def stuck(self, *a, **k):
        if self.name.endswith(".2"):
            raise PermissionError("busy")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(type(old), "unlink", stuck)
    assert tr.record(UID, _dec(), "how is Juniper", [], mode="shadow", ep=tr.epoch(), now=now) is False
    assert not _live().exists()
    monkeypatch.setattr(type(old), "unlink", real_unlink)     # negative control: retention enforceable -> written
    assert tr.record(UID, _dec(), "how is Juniper", [], mode="shadow", ep=tr.epoch(), now=now) is True


def test_no_append_during_an_erase_or_after_one_that_finished_since_the_rows_were_read(monkeypatch):
    import local_text_logs as ltl

    monkeypatch.setenv(tr.ENV, "1")
    ep = tr.epoch()
    ltl._bump_epoch(+1)                                       # an erase is running
    try:
        assert tr.record(UID, _dec(), "how is Juniper", [], mode="shadow", ep=tr.epoch()) is False
    finally:
        ltl._bump_epoch(-1)
    assert tr.record(UID, _dec(), "how is Juniper", [], mode="shadow", ep=ep) is False      # stale epoch: the forget finished
    assert not _live().exists()
    assert tr.record(UID, _dec(), "how is Juniper", [], mode="shadow", ep=None) is False    # no barrier reading: not written
    assert tr.record(UID, _dec(), "how is Juniper", [], mode="shadow", ep=tr.epoch()) is True  # negative control


class _All(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.mark.parametrize("fail", [False, True])
async def test_the_trace_is_silent_in_the_normal_logs_whether_it_writes_or_fails(monkeypatch, fail):
    """Every record ANY logger emits while the gate decides with the trace on (and its write failing) is captured at DEBUG: none comes
    from the trace module and none carries row text or the owner's words."""
    monkeypatch.setenv(tr.ENV, "1")
    if fail:
        def boom(*a, **k):
            raise OSError("disk full")

        monkeypatch.setattr(tr, "_append", boom)
    root, h = logging.getLogger(), _All()
    old = root.level
    root.addHandler(h)
    root.setLevel(logging.DEBUG)
    try:
        d = await _decide()
        rg._log(UID, d, "shadow")                           # the RECALL_GATE line itself, as the shadow task writes it
    finally:
        root.removeHandler(h)
        root.setLevel(old)
    msgs = [r.getMessage() for r in h.records]
    assert any(m.startswith("RECALL_GATE") for m in msgs)    # the capture works: the gate's own line was seen
    assert not [r for r in h.records if r.name.startswith("recall_gate_trace")]
    assert all("Juniper" not in m and "porridge" not in m for m in msgs)
    assert _live().exists() is (not fail)


def _seed(lines):
    tr.TRACE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    _live().write_text("".join(json.dumps(x) + "\n" for x in lines))
    os.chmod(_live(), 0o600)


def test_a_member_erase_clears_every_trace_file_and_keeps_0600():
    _seed([{"user": UID, "owner_words": "a"}, {"user": "demo_bar_00000002", "owner_words": "b"}])
    (tr.TRACE_DIR / f"{tr.FILE_NAME}.1").write_text(json.dumps({"user": UID, "owner_words": "d"}) + "\nnot json\n")
    assert tr.erase_all() == 4                               # every line, the malformed one included
    assert _live().read_text() == "" and (tr.TRACE_DIR / f"{tr.FILE_NAME}.1").read_text() == ""
    assert stat.S_IMODE(os.stat(_live()).st_mode) == 0o600


def test_the_member_erase_of_the_text_logs_reaches_the_trace(monkeypatch):
    import local_text_logs

    _seed([{"user": UID, "owner_words": "how is Juniper"}])
    monkeypatch.setattr(local_text_logs, "LOGS", ())          # only the trace is in play here
    assert local_text_logs.erase_all_text() == 1
    assert _live().read_text() == ""
    _seed([{"user": UID, "owner_words": "how is Juniper"}])
    monkeypatch.setattr(tr, "erase_all", lambda: 0)          # negative control: an eraser that does nothing leaves the line
    local_text_logs.erase_all_text()
    assert "Juniper" in _live().read_text()


def test_erase_entity_drops_lines_naming_the_entity_in_words_or_row_text():
    import re

    _seed([{"user": UID, "owner_words": "how is Juniper", "added": [], "dropped": []},
           {"user": UID, "owner_words": "what to cook", "added": [{"id": "x", "text": WALK}], "dropped": []},
           {"user": UID, "owner_words": "what to cook", "added": [], "dropped": [{"id": "y", "text": FILLER}]}])
    assert tr.erase_entity(re.compile(r"juniper", re.I)) == 2
    assert [json.loads(x)["dropped"][0]["text"] for x in _live().read_text().splitlines()] == [FILLER]
    assert tr.erase_entity(re.compile(r"zebra")) == 0        # negative control


def test_the_entity_forget_of_the_text_logs_reaches_the_trace(monkeypatch):
    """The cascade's text_logs step calls local_text_logs.erase_entity (pinned by test_forget_reach_inventory); it reaches the trace."""
    import re

    import local_text_logs

    monkeypatch.setattr(local_text_logs, "LOGS", ())
    _seed([{"user": UID, "owner_words": "how is Juniper"}, {"user": UID, "owner_words": "what to cook"}])
    assert local_text_logs.erase_entity(re.compile(r"juniper", re.I)) == 1
    assert [json.loads(x)["owner_words"] for x in _live().read_text().splitlines()] == ["what to cook"]
    monkeypatch.setattr(tr, "erase_entity", lambda rx: 0)    # negative control: a do-nothing eraser leaves the line
    _seed([{"user": UID, "owner_words": "how is Juniper"}])
    local_text_logs.erase_entity(re.compile(r"juniper", re.I))
    assert "Juniper" in _live().read_text()


def test_an_eraser_failure_raises_so_the_forget_step_fails_closed(monkeypatch):
    _seed([{"user": UID, "owner_words": "a"}])

    def boom(*a, **k):
        raise OSError("read-only")

    monkeypatch.setattr(tr.os, "replace", boom)
    with pytest.raises(OSError):
        tr.erase_all()


# ── the callers read the barrier BEFORE their own row reads (codex #2303: a forget during the caller's awaits) ─────────────

class _FloorSvc:
    """What memory_for_prompt reads; ``erase_during_read`` completes a forget while the caller is reading its rows."""

    def __init__(self, rows, erase_during_read):
        self.rows, self.erase = list(rows), erase_during_read

    def _maybe_erase(self):
        if self.erase:
            import local_text_logs as ltl

            ltl._bump_epoch(+1)
            ltl._bump_epoch(-1)                                 # a whole erase, start to finish, inside the caller's read

    async def load_for_prompt(self, user_id, *, limit):
        self._maybe_erase()
        return self.rows[:limit]

    async def load_recent_for_prompt(self, user_id, *, window_s, limit, emotional_first=False):
        return []

    async def search(self, query, *, user_id, limit=6, **_):
        return []

    async def load_durable_for_hop(self, user_id):
        self._maybe_erase()
        return list(self.rows)


@pytest.mark.parametrize("erase", [True, False])
async def test_a_forget_during_the_packet_callers_reads_keeps_those_rows_out_of_the_trace(monkeypatch, erase):
    import routers.memories as memories

    monkeypatch.setenv(tr.ENV, "1")
    svc = _FloorSvc([ref("zoe_demo_fill0000", FILLER), ref("zoe_demo_walk0001", WALK)], erase)
    monkeypatch.setattr(memories, "_svc", lambda: svc)
    await memories.memory_for_prompt(user_id=UID, message="How is Juniper getting on?", limit=12, _=None)
    await rg.drain()
    assert _live().exists() is (not erase)                  # negative control: no forget -> the decision IS traced


@pytest.mark.parametrize("erase", [True, False])
async def test_a_forget_during_the_hop_callers_read_keeps_those_rows_out_of_the_trace(monkeypatch, erase):
    import personalisation_hop as hop

    monkeypatch.setenv(tr.ENV, "1")
    await hop.build(UID, "What should I wear tomorrow? It's meant to be really cold.",
                    svc=_FloorSvc([ref("zoe_demo_walk0001", WALK)], erase))
    await rg.drain()
    assert _live().exists() is (not erase)


def test_the_test_suite_pins_the_trace_directory_away_from_home(monkeypatch):
    monkeypatch.setattr(tr, "TRACE_DIR", None)
    d = tr.trace_dir()
    assert not str(d).startswith(os.path.expanduser("~/.zoe")), d     # the conftest pin (ZOE_RECALL_GATE_TRACE_DIR) holds
    monkeypatch.delenv("ZOE_RECALL_GATE_TRACE_DIR", raising=False)
    assert str(tr.trace_dir()) == os.path.expanduser(tr.DEFAULT_DIR)    # negative control: unpinned = the owner's real path
