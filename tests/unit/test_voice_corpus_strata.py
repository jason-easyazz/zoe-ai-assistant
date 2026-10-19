"""V-8 (deep dive 2, 2026-10-19): propose a STRATIFIED pinned set from the corpus by what each clip does.

The gate's 20 pinned clips were 14 brain and zero lists / music / reminders. The proposal tool labels clips from
the replay's content-free route labels and spreads a fixed budget across categories. Synthetic rows only."""
from __future__ import annotations

import importlib.util
import json
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
