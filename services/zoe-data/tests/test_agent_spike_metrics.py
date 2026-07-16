import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe


def _load_module():
    path = Path(__file__).resolve().parents[3] / "scripts/maintenance/agent_spike_metrics.py"
    spec = importlib.util.spec_from_file_location("agent_spike_metrics", path)
    assert spec is not None, f"Could not locate module at {path}"
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
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

    with pytest.raises(ValueError, match="unknown candidate"):
        module.build_packet("unknown", task="x")

