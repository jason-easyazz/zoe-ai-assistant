"""Guard the GEMMA_SERVER_URL /v1 normalization (the prod /v1/v1 404 bug).

The live systemd unit sets GEMMA_SERVER_URL=http://127.0.0.1:11434/v1 (zoe_agent's
convention), but memory_digest appends /v1/chat/completions — which produced
http://127.0.0.1:11434/v1/v1/chat/completions (404) and silently killed idle
consolidation's Gemma extraction in prod. Normalization strips a trailing /v1 so
the append is always a single /v1.
"""
import pytest
from memory_digest import _normalize_gemma_base

pytestmark = pytest.mark.ci_safe


def test_strips_trailing_v1():
    assert _normalize_gemma_base("http://127.0.0.1:11434/v1") == "http://127.0.0.1:11434"

def test_strips_trailing_v1_with_slash():
    assert _normalize_gemma_base("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434"

def test_leaves_bare_base_untouched():
    assert _normalize_gemma_base("http://127.0.0.1:11434") == "http://127.0.0.1:11434"

def test_empty_falls_back_to_default():
    assert _normalize_gemma_base("") == "http://127.0.0.1:11434"

def test_resulting_url_is_single_v1_both_ways():
    for raw in ("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434"):
        url = f"{_normalize_gemma_base(raw)}/v1/chat/completions"
        assert url == "http://127.0.0.1:11434/v1/chat/completions"
        assert "/v1/v1/" not in url
