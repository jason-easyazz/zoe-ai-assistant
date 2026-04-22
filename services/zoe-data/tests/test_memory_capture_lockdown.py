"""Lockdown tests for conversational memory capture resilience."""

import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_fast_memory_extract_works_with_extractor_module():
    import pi_agent

    out = pi_agent._fast_memory_extract("remember that i met Sarah yesterday", "")
    assert isinstance(out, list)
    assert out


def test_fast_memory_extract_falls_back_when_extractor_missing(monkeypatch):
    import pi_agent

    broken = types.ModuleType("memory_extractor")
    monkeypatch.setitem(sys.modules, "memory_extractor", broken)

    out = pi_agent._fast_memory_extract("remember that my favourite coffee is flat white", "")
    assert isinstance(out, list)
    assert out
    assert any("remember" in item.lower() or "favourite" in item.lower() for item in out)

