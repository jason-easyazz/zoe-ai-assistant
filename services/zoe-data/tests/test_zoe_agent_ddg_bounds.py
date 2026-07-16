import pytest
import sys
import types

import research_evidence
import zoe_agent

pytestmark = pytest.mark.ci_safe


class _FailingDDGS:
    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        raise RuntimeError("force HTML fallback")

    def __exit__(self, *args):
        return False


class _FakeUrlResponse:
    def __init__(
        self,
        body: bytes,
        headers: dict[str, str] | None = None,
        *,
        max_chunk_size: int | None = None,
    ):
        self._body = body
        self.headers = headers or {}
        self._offset = 0
        self._max_chunk_size = max_chunk_size
        self.read_sizes: list[int | None] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size: int | None = None) -> bytes:
        self.read_sizes.append(size)
        if self._offset >= len(self._body):
            return b""
        limit = len(self._body) if size is None else self._offset + size
        if self._max_chunk_size is not None:
            limit = min(limit, self._offset + self._max_chunk_size)
        chunk = self._body[self._offset : min(limit, len(self._body))]
        self._offset += len(chunk)
        return chunk


def _force_html_fallback(monkeypatch):
    fake_ddgs = types.ModuleType("ddgs")
    fake_ddgs.DDGS = _FailingDDGS
    monkeypatch.setitem(sys.modules, "ddgs", fake_ddgs)


def test_ddg_search_sync_parses_normal_bounded_search_html(monkeypatch):
    _force_html_fallback(monkeypatch)
    body = (
        b'<a class="result__a" href="https://example.com/deals">Example deal</a>'
        b'<a class="result__snippet">Example snippet for this search result</a>'
        + b"x" * 3000
    )
    response = _FakeUrlResponse(body)

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: response)

    rows = zoe_agent._ddg_search_sync("example deal", max_results=1)

    assert rows == [
        {
            "name": "Example deal",
            "value": "Example snippet for this search result",
            "url": "https://example.com/deals",
        }
    ]
    assert response.read_sizes == [65536, 65536]


def test_ddg_search_sync_reads_until_eof_after_short_read(monkeypatch):
    _force_html_fallback(monkeypatch)
    body = (
        b'<a class="result__a" href="https://example.com/deals">Example deal</a>'
        b'<a class="result__snippet">Example snippet for this search result</a>'
        + b"x" * 3000
    )
    response = _FakeUrlResponse(body, max_chunk_size=1024)

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: response)

    rows = zoe_agent._ddg_search_sync("example deal", max_results=1)

    assert rows[0]["url"] == "https://example.com/deals"
    assert len(response.read_sizes) > 1


def test_ddg_search_sync_rejects_over_cap_search_html(monkeypatch):
    _force_html_fallback(monkeypatch)
    cap = 16
    response = _FakeUrlResponse(b"x" * (cap + 1))

    monkeypatch.setattr(zoe_agent, "_DDG_SEARCH_HTML_MAX_BYTES", cap)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: response)
    monkeypatch.setattr(research_evidence, "fetch_web_fallback_results", lambda *a, **k: [])

    rows = zoe_agent._ddg_search_sync("example deal", max_results=1)

    assert rows == []
    assert response.read_sizes == [cap + 1, cap + 1]
