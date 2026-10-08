"""``ZOE_DIGEST_LLM_TIMEOUT_SCALE``: the 12B night window serves the nightly passes from a slower model.

The digest's model calls were sized for the 4B (45 s for a 512-token extraction already needs ~11 tok/s). The night window
(``scripts/night/``) runs the SAME passes in its own processes against the 12B and sets the scale for those processes only; the
live service never sets it, so its timeouts must stay byte-for-byte what they were.
"""
import ast
import inspect

import pytest

import memory_digest as md


def test_default_is_unchanged(monkeypatch):
    monkeypatch.delenv("ZOE_DIGEST_LLM_TIMEOUT_SCALE", raising=False)
    assert [md._llm_timeout(x) for x in (10.0, 20.0, 30.0, 45.0)] == [10.0, 20.0, 30.0, 45.0]


@pytest.mark.parametrize("raw,want", [("5", 225.0), ("1.5", 67.5), ("", 45.0), ("nonsense", 45.0), ("0", 45.0), ("-3", 45.0)])
def test_scale_is_read_at_call_time_and_bad_values_fall_back_to_one(monkeypatch, raw, want):
    monkeypatch.setenv("ZOE_DIGEST_LLM_TIMEOUT_SCALE", raw)
    assert md._llm_timeout(45.0) == want


def test_every_model_call_in_the_module_goes_through_the_scale():
    """A bare ``httpx.AsyncClient(timeout=<number>)`` / ``timeout=<number>`` on a chat-completions POST would silently stay at the 4B's budget on the 12B."""
    tree = ast.parse(inspect.getsource(md))
    bare = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "timeout" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, (int, float)):
                    fn = getattr(node.func, "attr", getattr(node.func, "id", ""))
                    if fn in ("AsyncClient", "post"):
                        bare.append((fn, kw.value.value, node.lineno))
    assert bare == [], f"model-call timeouts that bypass _llm_timeout: {bare}"
