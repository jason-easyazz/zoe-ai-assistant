import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts/maintenance/agent_spike_metrics.py"
    spec = importlib.util.spec_from_file_location("agent_spike_metrics", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_build_packet_contains_cost_policy_and_required_evidence(monkeypatch):
    module = _load_module()
    monkeypatch.setattr(module, "tool_available", lambda candidate: candidate == "caveman")

    packet = module.build_packet("caveman", task="compress a Greptile repair packet", pr_url="https://x/pull/1")

    assert packet["candidate"] == "caveman"
    assert packet["available"] is True
    assert "token compression" in packet["role"]
    assert "cost_policy" in packet
    assert "gotchas" in packet["required_evidence"]


def test_unknown_candidate_is_rejected():
    module = _load_module()

    try:
        module.build_packet("unknown", task="x")
    except ValueError as exc:
        assert "unknown candidate" in str(exc)
    else:
        raise AssertionError("expected ValueError")

