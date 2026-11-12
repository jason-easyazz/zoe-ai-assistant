"""The night model wraps JSON in a markdown fence or gets cut by max_tokens: parse it, stay strict on garbage."""

import asyncio

import pytest

pytestmark = pytest.mark.ci_safe

FACT = '{"fact": "Dad is Neil", "category": "family"}'


def test_strip_code_fence_variants():
    from llm_json import strip_code_fence as f

    assert f("```json\n[1, 2]\n```") == "[1, 2]"
    assert f("```\n[1, 2]\n```") == "[1, 2]"
    assert f("  ```JSON\n[1]```  ") == "[1]"
    assert f("[1, 2]") == "[1, 2]"                      # unfenced: untouched
    assert f("```json\n[1, 2") == "[1, 2"               # cut off: opening still stripped
    assert f("") == "" and f(None) == ""
    assert f("here ```json\n[1]\n``` there").startswith("here")   # not a surrounding fence: left alone


def _client(content):
    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": content}}]}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **kw):
            return _Resp()
    return _Client


def _run(md, monkeypatch, content):
    monkeypatch.setattr(md.httpx, "AsyncClient", _client(content))
    return asyncio.run(md._extract_facts_with_gemma("some transcript " * 10))


def test_digest_parses_fenced_unfenced_truncated_and_rejects_garbage(monkeypatch):
    import memory_digest as md

    assert _run(md, monkeypatch, "```json\n[" + FACT + "]\n```")[0]["fact"] == "Dad is Neil"
    assert _run(md, monkeypatch, "```\n[" + FACT + "]\n```")[0]["fact"] == "Dad is Neil"
    assert _run(md, monkeypatch, "[" + FACT + "]")[0]["fact"] == "Dad is Neil"
    assert _run(md, monkeypatch, "```json\n[]\n```") == []
    # cut off by max_tokens after one complete item and part of a second: the complete one is kept
    cut = "```json\n[" + FACT + ', {"fact": "Lives in Gera'
    assert [x["fact"] for x in _run(md, monkeypatch, cut)] == ["Dad is Neil"]
    # garbage / nothing completed stays an error
    for bad in ("I cannot help with that.", "```json\n[", "```\n```"):
        with pytest.raises(md.ExtractorError) as ei:
            _run(md, monkeypatch, bad)
        assert ei.value.kind == "NoJSONArray"
