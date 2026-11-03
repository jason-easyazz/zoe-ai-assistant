"""The replay harness's per-clip route labels and its fault-injection seam (T-3, deep dive 2, 2026-10-19).

The voice probe asserts WHAT each pinned clip did from these labels, and its --self-test replays through the
injected faults and must go red. Pinned here without a corpus, a DB or a model (``_run`` is never called):

  * labels are code identifiers only; anything else (a stray transcript-like string) is hashed;
  * the lane comes from the outcome label exactly as ``_classify`` reads it;
  * ``misroute`` always moves a clip to a DIFFERENT router domain and drops the two-stage tool;
  * ``ZOE_REPLAY_FAULT`` refuses an unknown value instead of ignoring it.
Slim-dep safe: ``replay_samples`` imports only stdlib + ``zoe_flue_client`` at module level.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.ci_safe


def _harness():
    cwd = os.getcwd()
    try:
        import replay_samples
        return replay_samples
    finally:
        os.chdir(cwd)


@pytest.mark.parametrize("outcome,lane,tier,domain,intent", [
    ("tier1:lists:add_item", "fast", "tier1", "lists", "add_item"),
    ("tier1.5:timers:set_timer", "fast", "tier1.5", "timers", "set_timer"),
    ("brain", "brain", None, None, None),
    ("→ brain", "deferred", None, None, None),
    ("<resolve error: boom>", "error", None, None, None),
])
def test_route_labels_lane_and_fast_path(outcome, lane, tier, domain, intent):
    rs = _harness()
    lab = rs._route_labels({"domain": "lists", "two_stage": {"tool": "add_to_list"}}, "list_add", outcome)
    assert (lab["lane"], lab["tier"], lab["domain"], lab["intent"]) == (lane, tier, domain, intent)
    assert (lab["router_domain"], lab["router_tool"], lab["tier0"]) == ("lists", "add_to_list", "list_add")
    assert set(lab) == {"lane", "tier", "domain", "intent", "router_domain", "router_tool", "tier0"}


def test_labels_never_carry_free_text():
    """A value that is not a plain identifier (spaces, punctuation: transcript-shaped) is hashed, never stored."""
    rs = _harness()
    lab = rs._route_labels({"domain": "add milk to my list please"}, "what's up", "tier1:lists:add milk")
    assert lab["router_domain"].startswith("h:") and lab["tier0"].startswith("h:") and lab["intent"].startswith("h:")
    assert "milk" not in repr(lab)
    assert rs._route_labels(None, None, "brain")["router_domain"] is None


@pytest.mark.parametrize("domain", ["chat", "lists", "memory", "people", "time", "some_unknown_domain", None])
def test_misroute_always_moves_the_clip(domain):
    rs = _harness()
    rr = {"domain": domain, "score": 0.4, "two_stage": {"tool": "x"}, "scores": {}}
    out = rs._misroute(rr)
    assert out["domain"] != domain and "two_stage" not in out and out["routed"] == out["domain"]
    assert rr["two_stage"] == {"tool": "x"}                     # the input is not mutated


def test_misroute_ring_covers_every_router_domain():
    """Every domain the two-stage router can return must be in the ring, or misroute maps it to chat (which a
    chat clip would not notice)."""
    rs = _harness()
    import router_two_stage
    assert set(router_two_stage.DOMAIN_TOOLS) <= set(rs._MISROUTE_RING)


def test_fault_env_parsing(monkeypatch):
    rs = _harness()
    monkeypatch.delenv("ZOE_REPLAY_FAULT", raising=False)
    assert rs._replay_fault() is None
    for f in rs.REPLAY_FAULTS:
        monkeypatch.setenv("ZOE_REPLAY_FAULT", f)
        assert rs._replay_fault() == f
    monkeypatch.setenv("ZOE_REPLAY_FAULT", "blank-stt")
    with pytest.raises(ValueError):
        rs._replay_fault()
