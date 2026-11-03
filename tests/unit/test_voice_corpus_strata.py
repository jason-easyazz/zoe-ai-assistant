"""V-8 (deep dive 2, 2026-10-19): propose a STRATIFIED pinned set from the corpus by what each clip does.

The gate's 20 pinned clips were 14 brain and zero lists / music / reminders. The proposal tool labels clips from
the replay's content-free route labels and spreads a fixed budget across categories. Synthetic rows only."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("voice_corpus_strata_t", REPO / "scripts/maintenance/voice_corpus_strata.py")
vcs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vcs)


def _lab(lane="fast", domain=None, intent=None, router_domain="chat", router_tool=None, tier0=None):
    return dict(lane=lane, tier="tier1" if lane == "fast" else None, domain=domain, intent=intent,
                router_domain=router_domain, router_tool=router_tool, tier0=tier0)


@pytest.mark.parametrize("labels,cat", [
    (_lab(domain="lists", intent="add_item"), "lists"),
    (_lab(lane="brain", router_domain="timers", router_tool="set_timer"), "timers"),
    (_lab(lane="brain", router_domain="chat", tier0="reminder_create"), "reminders"),
    (_lab(lane="brain", router_domain="music", router_tool="media"), "music"),
    (_lab(lane="brain", router_domain="smart_home", router_tool="home"), "smart_home"),
    (_lab(domain="weather"), "weather_time"),
    (_lab(lane="brain", router_domain="time"), "weather_time"),
    (_lab(lane="brain", router_domain="memory"), "people_memory"),
    (_lab(lane="brain", router_domain="chat"), "chat"),
    (_lab(lane="brain", router_domain="h:1234abcd"), "chat"),        # a hashed (non-identifier) label says nothing
    (None, "chat"),
])
def test_category(labels, cat):
    assert vcs.category(labels) == cat


def _rows(spec: dict[str, int], verdict="OK"):
    rows, i = [], 0
    for cat, n in spec.items():
        for _ in range(n):
            lab = (_lab(lane="brain", router_domain="chat") if cat == "chat"
                   else _lab(lane="brain", router_domain=cat))
            rows.append({"file": f"c{i:04d}.wav", "verdict": verdict, "route": lab})
            i += 1
    return rows


def _cid(r):
    return "x" + r["file"][1:5].rjust(11, "0")


def test_propose_balances_a_brain_heavy_corpus():
    """The live shape: chat dominates; the proposal must not."""
    rows = _rows({"chat": 900, "lists": 40, "timers": 3, "music": 25, "reminders": 12, "smart_home": 8})
    p = vcs.propose(rows, _cid, 60)
    assert len(p["clip_ids"]) == 60 and len(set(p["clip_ids"])) == 60
    assert p["counts"]["timers"] == 3                                 # a short stratum gives what it has...
    assert p["counts"]["chat"] <= 15                                  # ...and chat no longer swamps the set
    assert min(p["counts"][c] for c in ("lists", "music", "reminders", "smart_home")) >= 8
    assert p["corpus_counts"]["chat"] == 900
    assert "calendar" in p["missing_strata"] and "lists" not in p["missing_strata"]
    assert set(p["categories"]) == set(p["clip_ids"])


def test_propose_is_deterministic_and_ignores_unlabelled_or_failed_rows():
    rows = _rows({"chat": 50, "lists": 50})
    assert vcs.propose(rows, _cid, 20) == vcs.propose(list(reversed(rows)), _cid, 20)
    assert vcs.propose(rows, _cid, 20, seed="other")["clip_ids"] != vcs.propose(rows, _cid, 20)["clip_ids"]
    junk = _rows({"lists": 5}, verdict="EMPTY") + [{"file": "c9999.wav", "verdict": "OK"}]
    assert vcs.propose(junk, _cid, 10)["clip_ids"] == []


def test_proposal_is_content_free(tmp_path):
    rows = _rows({"chat": 5, "lists": 5})
    p = vcs.propose(rows, _cid, 6)
    blob = json.dumps(p)
    assert ".wav" not in blob                                        # ids and categories only


# ---- the 01:50-04:55 brain window guards the REPLAY path (before the lock), never --from-json ------------------------------

import datetime as dt  # noqa: E402
from types import SimpleNamespace  # noqa: E402

_hq_spec = importlib.util.spec_from_file_location("harness_quiet_for_strata", REPO / "scripts/maintenance/harness_quiet.py")
_hq = importlib.util.module_from_spec(_hq_spec)
sys.modules["harness_quiet_for_strata"] = _hq        # a dataclass in the module resolves its own module by name
_hq_spec.loader.exec_module(_hq)


def _at(h, m=0):
    return dt.datetime(2026, 10, 20, h, m)


class _Rig:
    """main() with its collaborators faked: a fixed clock for the brain window, a lock spy, a replay spy."""

    def __init__(self, monkeypatch, when):
        self.locks, self.replays = [], []
        self.hq = SimpleNamespace(
            brain_window_verdict=lambda name, budget_min=45: _hq.brain_window_verdict(name, budget_min=budget_min, now=when),
            check=lambda name: SimpleNamespace(allowed=True, reason="ok"))
        vrp = SimpleNamespace(_row_clip_id=lambda r, d: "x" + r["file"][1:5].rjust(11, "0"),
                              _acquire_harness_lock=lambda: self.locks.append(1), CLIP_ID_SCHEME="t")
        monkeypatch.setattr(vcs, "_load", lambda name: {"harness_quiet": self.hq}.get(name, vrp))
        monkeypatch.setattr(vcs, "_run_replay", lambda args, out: self.replays.append(out) or 2)


def _corpus(tmp_path, n):
    d = tmp_path / "samples"
    d.mkdir()
    for i in range(n):
        (d / f"{i:06d}_000.wav").write_bytes(b"")
    (d / "quarantine-x").mkdir()
    (d / "quarantine-x" / "q.wav").write_bytes(b"")      # the replay's glob is top-level: not counted
    return d


def test_corpus_count_is_the_replays_top_level_glob_capped_by_last(tmp_path):
    d = _corpus(tmp_path, 12)
    assert vcs.corpus_clip_count(str(d)) == 12 and vcs.corpus_clip_count(str(d), 5) == 5
    assert vcs.corpus_clip_count(str(tmp_path / "missing")) == 0
    assert vcs.estimate_budget_min(0) == vcs.OVERHEAD_MIN and vcs.estimate_budget_min(1485) >= 37


def test_replay_inside_the_brain_window_is_refused_before_the_lock(monkeypatch, tmp_path, capsys):
    rig = _Rig(monkeypatch, _at(3, 0))
    d = _corpus(tmp_path, 3)
    assert vcs.main(["--sample-dir", str(d), "--out", str(tmp_path / "o.json")]) == 3
    assert "brain window" in capsys.readouterr().out
    assert rig.locks == [] and rig.replays == []                       # the harness lock was never taken


def test_a_replay_whose_budget_would_cross_0150_is_refused(monkeypatch, tmp_path):
    rig = _Rig(monkeypatch, _at(1, 30))
    d = _corpus(tmp_path, 1000)                                        # 1000 clips = 30 min estimated: still alive at 01:50
    assert vcs.main(["--sample-dir", str(d), "--out", str(tmp_path / "o.json")]) == 3
    assert rig.locks == [] and rig.replays == []
    # a narrower corpus (--last) fits the same slot, an explicit budget is honoured, and a bigger one is refused
    assert vcs.main(["--sample-dir", str(d), "--last", "10", "--out", str(tmp_path / "o.json")]) == 2      # reached the replay
    assert rig.locks == [1] and len(rig.replays) == 1
    assert vcs.main(["--sample-dir", str(d), "--budget-min", "10", "--out", str(tmp_path / "o.json")]) == 2
    assert vcs.main(["--sample-dir", str(d), "--budget-min", "25", "--out", str(tmp_path / "o.json")]) == 3
    assert len(rig.locks) == 2


def test_from_json_is_never_gated_by_the_window(monkeypatch, tmp_path):
    rig = _Rig(monkeypatch, _at(3, 0))
    dump = tmp_path / "replay.json"
    dump.write_text(json.dumps({"rows": _rows({"chat": 4, "lists": 4})}))
    out = tmp_path / "o.json"
    assert vcs.main(["--from-json", str(dump), "--total", "4", "--out", str(out)]) == 0
    assert out.exists() and rig.locks == [] and rig.replays == []
