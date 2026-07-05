import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

import greptile_client
import pytest


def test_parse_confidence_score_from_nested_sources():
    payload = {
        "description": "No score here",
        "codeReviews": [{"body": "Confidence Score: 4/5\nLooks close"}],
    }

    assert greptile_client.parse_confidence_score(payload) == 4


def test_parse_confidence_score_prefers_direct_numeric_value():
    assert greptile_client.parse_confidence_score({"confidenceScore": 5}) == 5


def test_normalize_pr_comment_maps_common_fields():
    comment = {
        "id": 123,
        "filePath": "services/zoe-data/example.py",
        "lineStart": 42,
        "lineEnd": 43,
        "body": "Fix this",
        "suggestedCode": "pass",
        "url": "https://github.example/comment",
    }

    normalized = greptile_client.normalize_pr_comment(comment)

    assert normalized["id"] == "123"
    assert normalized["file_path"] == "services/zoe-data/example.py"
    assert normalized["line"] == 42
    assert normalized["line_end"] == 43
    assert normalized["url"] == "https://github.example/comment"
    assert normalized["has_suggestion"] is True


def test_review_is_running_detects_pending_states():
    assert greptile_client.review_is_running({"reviewCompleteness": "in_progress"}) is True
    assert greptile_client.review_is_running({"codeReviews": [{"status": "REVIEWING_FILES"}]}) is True
    assert greptile_client.review_is_running({"reviewCompleteness": "reviewed"}) is False


class _FakeStreamResponse:
    def __init__(
        self,
        chunks: list[bytes],
        headers: dict[str, str] | None = None,
        *,
        encoding: str | None = None,
    ):
        self.chunks = chunks
        self.headers = headers or {}
        self.encoding = encoding

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        for chunk in self.chunks:
            yield chunk


class _FakeAsyncClient:
    def __init__(self, response: _FakeStreamResponse, *args, **kwargs):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        return self.response


@pytest.mark.asyncio
async def test_mcp_call_parses_normal_bounded_json_response(monkeypatch):
    body = (
        b'{"result":{"content":[{"type":"text","text":"{\\"ok\\": true}"}]}}'
    )
    response = _FakeStreamResponse([body], {"Content-Length": str(len(body))})

    monkeypatch.setenv("GREPTILE_API_KEY", "test-key")
    monkeypatch.setattr(
        greptile_client.httpx,
        "AsyncClient",
        lambda *a, **k: _FakeAsyncClient(response, *a, **k),
    )

    assert await greptile_client._mcp_call("tool", {}) == {"ok": True}


@pytest.mark.asyncio
async def test_mcp_call_uses_response_encoding_for_bounded_json_response(monkeypatch):
    text = '{"result":{"content":[{"type":"text","text":"{\\"message\\": \\"caf\xe9\\"}"}]}}'
    body = text.encode("iso-8859-1")
    response = _FakeStreamResponse(
        [body],
        {"Content-Length": str(len(body)), "Content-Type": "application/json; charset=iso-8859-1"},
        encoding="iso-8859-1",
    )

    monkeypatch.setenv("GREPTILE_API_KEY", "test-key")
    monkeypatch.setattr(
        greptile_client.httpx,
        "AsyncClient",
        lambda *a, **k: _FakeAsyncClient(response, *a, **k),
    )

    assert await greptile_client._mcp_call("tool", {}) == {"message": "caf\xe9"}


@pytest.mark.asyncio
async def test_mcp_call_rejects_over_cap_json_response(monkeypatch):
    cap = 8
    response = _FakeStreamResponse([b"x" * cap, b"y"])

    monkeypatch.setenv("GREPTILE_API_KEY", "test-key")
    monkeypatch.setattr(greptile_client, "MCP_RESPONSE_MAX_BYTES", cap)
    monkeypatch.setattr(
        greptile_client.httpx,
        "AsyncClient",
        lambda *a, **k: _FakeAsyncClient(response, *a, **k),
    )

    with pytest.raises(RuntimeError, match="Greptile MCP response exceeds"):
        await greptile_client._mcp_call("tool", {})
