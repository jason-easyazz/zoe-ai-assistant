"""The Kokoro sidecar HTTP client is pooled and reused across spoken sentences.

The streaming voice path calls the sidecar once per sentence; a fresh
httpx.AsyncClient per call paid a TCP/connection setup at every inter-sentence
boundary. _kokoro_http_client() must hand back the SAME live client and only
re-open one once it's been closed.
"""
import pytest
import asyncio

import tts_waterfall as v

pytestmark = pytest.mark.ci_safe


def test_client_is_reused_across_calls(monkeypatch):
    # monkeypatch.setattr (not direct assignment) so the singleton is restored on
    # teardown and a live client isn't leaked into other test files.
    monkeypatch.setattr(v, "_KOKORO_HTTP", None)
    c1 = v._kokoro_http_client()
    c2 = v._kokoro_http_client()
    assert c1 is c2, "sidecar client should be pooled, not recreated per sentence"
    assert not c1.is_closed
    asyncio.run(c1.aclose())


def test_client_reopened_after_close(monkeypatch):
    monkeypatch.setattr(v, "_KOKORO_HTTP", None)
    c1 = v._kokoro_http_client()
    asyncio.run(c1.aclose())
    c2 = v._kokoro_http_client()
    assert c2 is not c1, "a closed client must be replaced with a fresh one"
    assert not c2.is_closed
    asyncio.run(c2.aclose())
