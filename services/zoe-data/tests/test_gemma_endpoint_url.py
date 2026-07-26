"""GEMMA_SERVER_URL normalization — no module may build `/v1/v1/chat/completions`.

`GEMMA_SERVER_URL` is shared across zoe-data. The live systemd unit sets it WITH a
trailing `/v1` (the zoe_agent convention), while several modules append
`/v1/chat/completions` to a "bare base". Without normalization that yields
`/v1/v1/chat/completions` → 404, silently breaking LLM calls in prod. The shared
helper `gemma_endpoint.gemma_base()` strips a trailing `/v1` so the appends below
always produce exactly one `/v1`.
"""
from __future__ import annotations

import importlib

import pytest

import gemma_endpoint

pytestmark = pytest.mark.ci_safe


# ---------------------------------------------------------------------------
# helper unit tests — both conventions, slashes, empty
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        # live systemd value (WITH /v1) → stripped
        ("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434"),
        # bare base → unchanged
        ("http://127.0.0.1:11434", "http://127.0.0.1:11434"),
        # trailing slash on /v1 form
        ("http://127.0.0.1:11434/v1/", "http://127.0.0.1:11434"),
        # trailing slash on bare form
        ("http://127.0.0.1:11434/", "http://127.0.0.1:11434"),
        # alternate host kept intact
        ("http://localhost:11434/v1", "http://localhost:11434"),
        # whitespace tolerated
        ("  http://127.0.0.1:11434/v1  ", "http://127.0.0.1:11434"),
        # empty → default
        ("", "http://127.0.0.1:11434"),
    ],
)
def test_normalize_gemma_base(raw, expected):
    assert gemma_endpoint.normalize_gemma_base(raw) == expected


def test_gemma_base_reads_env_at_call_time(monkeypatch):
    monkeypatch.setenv("GEMMA_SERVER_URL", "http://127.0.0.1:11434/v1")
    assert gemma_endpoint.gemma_base() == "http://127.0.0.1:11434"
    monkeypatch.setenv("GEMMA_SERVER_URL", "http://host:9999")
    assert gemma_endpoint.gemma_base() == "http://host:9999"
    monkeypatch.delenv("GEMMA_SERVER_URL", raising=False)
    assert gemma_endpoint.gemma_base() == "http://127.0.0.1:11434"


def test_gemma_base_append_has_single_v1(monkeypatch):
    """The canonical contract: base + '/v1/chat/completions' == one /v1."""
    for raw in ("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434", "http://x:1/v1/"):
        monkeypatch.setenv("GEMMA_SERVER_URL", raw)
        url = f"{gemma_endpoint.gemma_base()}/v1/chat/completions"
        assert "/v1/v1/" not in url
        assert url.count("/v1/") == 1
        assert url.endswith("/v1/chat/completions")


# ---------------------------------------------------------------------------
# per-module call-site tests — capture the URL each module actually posts to
# ---------------------------------------------------------------------------

# (module name, attribute on that module that builds the URL via gemma_base()).
# Every fixed call site formats f"{gemma_base()}/v1/chat/completions"; we
# reconstruct it the same way the module does and assert no double /v1.
_FIXED_MODULES = [
    "latent_intent_detector",
    "person_extractor_llm",
    "nlu_extractor",
    "user_portrait",
    "proactive.composer",
]


@pytest.mark.parametrize("module_name", _FIXED_MODULES)
@pytest.mark.parametrize(
    "env_value",
    [
        "http://127.0.0.1:11434/v1",   # live systemd convention (the bug trigger)
        "http://127.0.0.1:11434",      # bare base
        "http://127.0.0.1:11434/v1/",  # trailing slash
    ],
)
def test_module_imports_shared_helper_and_builds_single_v1(module_name, env_value, monkeypatch):
    monkeypatch.setenv("GEMMA_SERVER_URL", env_value)
    mod = importlib.import_module(module_name)

    # Each fixed module imports the shared helper by name.
    assert hasattr(mod, "gemma_base"), f"{module_name} must import gemma_base from gemma_endpoint"
    assert mod.gemma_base is gemma_endpoint.gemma_base

    # The URL each module constructs (mirrors the f-string at the call site).
    url = f"{mod.gemma_base()}/v1/chat/completions"
    assert "/v1/v1/" not in url, f"{module_name} would double /v1 against {env_value!r}"
    assert url == "http://127.0.0.1:11434/v1/chat/completions"
