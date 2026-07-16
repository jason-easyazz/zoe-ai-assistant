import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx
import multica_client
import pytest

from multica_ticket_contract import parse_ticket_block
from zoe_evolution_proposal_adapter import dump_mcp_evolution_proposal_contract

pytestmark = pytest.mark.ci_safe


def test_get_engineering_multica_agent_id_prefers_env(monkeypatch):
    monkeypatch.setenv("HERMES_MULTICA_AGENT_ID", "env-hermes-id")
    multica_client._cached_engineering_agent_id = None
    assert multica_client.get_engineering_multica_agent_id() == "env-hermes-id"


def test_get_engineering_multica_agent_id_falls_back_to_default(monkeypatch):
    monkeypatch.delenv("HERMES_MULTICA_AGENT_ID", raising=False)
    multica_client._cached_engineering_agent_id = None
    assert multica_client.get_engineering_multica_agent_id() == "019ae0a7-62f1-47fe-9d46-75fd0ae5d570"


def test_mul_client_reads_env_at_instantiation(monkeypatch):
    monkeypatch.setenv("MULTICA_BASE_URL", "https://multica.example/")
    monkeypatch.setenv("MULTICA_API_TOKEN", "token-1")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-1")

    client = multica_client.MULClient()

    assert client._base == "https://multica.example"
    assert client._token == "token-1"
    assert client._workspace == "workspace-1"
    assert client.is_configured() is True


@pytest.mark.asyncio
async def test_list_issues_forwards_explicit_limit(monkeypatch):
    requests = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"issues": []}

    class FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            requests.append((url, kwargs))
            return FakeResponse()

    monkeypatch.setenv("MULTICA_BASE_URL", "https://multica.example")
    monkeypatch.setenv("MULTICA_API_TOKEN", "token-1")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-1")
    monkeypatch.setattr(
        multica_client.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeHttpClient(),
    )

    await multica_client.MULClient().list_issues(status="backlog", limit=1000)

    assert requests[0][1]["params"] == {"status": "backlog", "limit": 1000}


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [None, "unexpected", 42])
async def test_list_issues_returns_empty_for_non_dict_non_list_body(monkeypatch, body):
    """A 200 with a null/non-dict/non-list JSON body must yield [] not AttributeError."""

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return body

    class FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            return FakeResponse()

    monkeypatch.setenv("MULTICA_BASE_URL", "https://multica.example")
    monkeypatch.setenv("MULTICA_API_TOKEN", "token-1")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-1")
    monkeypatch.setattr(
        multica_client.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeHttpClient(),
    )

    assert await multica_client.MULClient().list_issues() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, endpoint",
    [("list_labels", "/api/labels"), ("list_projects", "/api/projects")],
)
async def test_list_methods_return_empty_for_null_body(monkeypatch, method, endpoint):
    """list_labels/list_projects must also tolerate a null JSON body without raising."""

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return None

    class FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            assert url.endswith(endpoint)
            return FakeResponse()

    monkeypatch.setenv("MULTICA_BASE_URL", "https://multica.example")
    monkeypatch.setenv("MULTICA_API_TOKEN", "token-1")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-1")
    monkeypatch.setattr(
        multica_client.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeHttpClient(),
    )

    assert await getattr(multica_client.MULClient(), method)() == []


@pytest.mark.asyncio
async def test_lookup_evolution_resources_skips_matching_rows_without_ids(monkeypatch):
    requests = []

    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            requests.append(url)
            if url.endswith("/api/agents"):
                return FakeResponse([
                    {"name": "Self-Improvement Agent"},
                    {"name": "Self-Improvement Agent", "id": "agent-1"},
                ])
            if url.endswith("/api/projects"):
                return FakeResponse({"projects": [
                    {"title": "Self-Improvement Engine"},
                    {"title": "Self-Improvement Engine", "id": "project-1"},
                ]})
            return FakeResponse([])

    monkeypatch.setenv("MULTICA_BASE_URL", "https://multica.example")
    monkeypatch.setenv("MULTICA_API_TOKEN", "token-1")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-1")
    monkeypatch.setattr(multica_client.httpx, "AsyncClient", lambda **_kwargs: FakeHttpClient())
    multica_client._cached_self_imp_agent_id = None
    multica_client._cached_self_imp_project_id = None

    try:
        agent_id, project_id = await multica_client._lookup_evolution_resources(multica_client.MULClient())
    finally:
        multica_client._cached_self_imp_agent_id = None
        multica_client._cached_self_imp_project_id = None

    assert requests == ["https://multica.example/api/agents", "https://multica.example/api/projects"]
    assert agent_id == "agent-1"
    assert project_id == "project-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("projects_body", [None, "oops", 42])
async def test_lookup_evolution_resources_tolerates_non_listdict_projects_body(
    monkeypatch, projects_body
):
    """A null/non-list/non-dict projects 200 body must not raise AttributeError."""

    class FakeResponse:
        status_code = 200

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, **kwargs):
            if url.endswith("/api/agents"):
                return FakeResponse(None)
            if url.endswith("/api/projects"):
                return FakeResponse(projects_body)
            return FakeResponse([])

    monkeypatch.setenv("MULTICA_BASE_URL", "https://multica.example")
    monkeypatch.setenv("MULTICA_API_TOKEN", "token-1")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-1")
    monkeypatch.setattr(multica_client.httpx, "AsyncClient", lambda **_kwargs: FakeHttpClient())
    multica_client._cached_self_imp_agent_id = None
    multica_client._cached_self_imp_project_id = None

    try:
        agent_id, project_id = await multica_client._lookup_evolution_resources(
            multica_client.MULClient()
        )
    finally:
        multica_client._cached_self_imp_agent_id = None
        multica_client._cached_self_imp_project_id = None

    assert agent_id is None
    assert project_id is None


def test_get_multica_client_refreshes_cached_client_when_env_changes(monkeypatch):
    original_client = multica_client._client
    original_agent_id = multica_client._cached_self_imp_agent_id
    original_project_id = multica_client._cached_self_imp_project_id
    try:
        monkeypatch.setenv("MULTICA_BASE_URL", "https://multica.example")
        monkeypatch.setenv("MULTICA_API_TOKEN", "token-1")
        monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-1")
        multica_client._client = None
        stale = multica_client.get_multica_client()

        assert stale.is_configured() is True
        assert stale._token == "token-1"
        assert stale._workspace == "workspace-1"

        # Simulate workspace-resource IDs cached for the initial Multica workspace.
        multica_client._cached_self_imp_agent_id = "agent-old"
        multica_client._cached_self_imp_project_id = "project-old"

        monkeypatch.setenv("MULTICA_BASE_URL", "https://multica.example")
        monkeypatch.setenv("MULTICA_API_TOKEN", "token-2")
        monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-2")

        refreshed = multica_client.get_multica_client()

        assert refreshed is not stale
        assert refreshed.is_configured() is True
        assert refreshed._base == "https://multica.example"
        assert refreshed._token == "token-2"
        assert refreshed._workspace == "workspace-2"
        assert multica_client._cached_self_imp_agent_id is None
        assert multica_client._cached_self_imp_project_id is None
    finally:
        multica_client._client = original_client
        multica_client._cached_self_imp_agent_id = original_agent_id
        multica_client._cached_self_imp_project_id = original_project_id


@pytest.mark.asyncio
async def test_sync_evolution_proposal_embeds_contract_marker(monkeypatch):
    requests = []
    contract_snapshot = dump_mcp_evolution_proposal_contract(
        proposal_id="proposal-1",
        title="Improve recall",
        description="Recall needs reviewable evidence before execution.",
        evidence="trace:recall-1",
        proposal_type="intent_pattern",
    )

    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            requests.append(("POST", url, kwargs))
            if url.endswith("/api/issues"):
                return FakeResponse({"id": "issue-1"})
            return FakeResponse({"id": "label-1"}, status_code=201)

        async def get(self, url, **kwargs):
            requests.append(("GET", url, kwargs))
            return FakeResponse([])

    monkeypatch.setenv("MULTICA_BASE_URL", "https://multica.example")
    monkeypatch.setenv("MULTICA_API_TOKEN", "token-1")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-1")
    monkeypatch.setenv("HERMES_MULTICA_AGENT_ID", HERMES := "hermes-agent")
    monkeypatch.setattr(multica_client, "_lookup_evolution_resources", lambda _client: _async_tuple())
    monkeypatch.setattr(
        multica_client.httpx,
        "AsyncClient",
        lambda **_kwargs: FakeHttpClient(),
    )

    issue_id = await multica_client.sync_evolution_proposal_to_multica(
        proposal_id="proposal-1",
        title="Improve recall",
        description="Recall needs reviewable evidence before execution.",
        evidence="trace:recall-1",
        proposal_type="intent_pattern",
        contract_snapshot=contract_snapshot,
    )

    assert issue_id == "issue-1"
    issue_payload = requests[0][2]["json"]
    assert issue_payload["assignee_id"] == HERMES
    metadata = parse_ticket_block(issue_payload["description"])
    assert metadata["source"] == "evolution_proposal:proposal-1"
    assert metadata["evolution_contract_schema"] == "zoe_evolution_proposal"
    assert metadata["evolution_contract_proposal_id"] == "proposal-1"
    assert metadata["evolution_contract_allowed_to_prepare"] is True


@pytest.mark.asyncio
async def test_sync_evolution_proposal_skips_label_rows_without_ids(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            requests.append(("POST", url, kwargs))
            if url.endswith("/api/issues"):
                return FakeResponse({"id": "issue-1"})
            if url.endswith("/api/issues/issue-1/labels"):
                return FakeResponse({"ok": True})
            return FakeResponse({"id": "created-label"}, status_code=201)

        async def get(self, url, **kwargs):
            requests.append(("GET", url, kwargs))
            if url.endswith("/api/labels"):
                return FakeResponse([
                    {"name": "evolution-proposal"},
                    {"name": "evolution-proposal", "id": "label-1"},
                ])
            return FakeResponse([])

    monkeypatch.setenv("MULTICA_BASE_URL", "https://multica.example")
    monkeypatch.setenv("MULTICA_API_TOKEN", "token-1")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-1")
    monkeypatch.setenv("HERMES_MULTICA_AGENT_ID", "hermes-agent")
    monkeypatch.setattr(multica_client, "_lookup_evolution_resources", lambda _client: _async_tuple())
    monkeypatch.setattr(multica_client.httpx, "AsyncClient", lambda **_kwargs: FakeHttpClient())

    issue_id = await multica_client.sync_evolution_proposal_to_multica(
        proposal_id="proposal-1",
        title="Improve recall",
        description="Recall needs reviewable evidence before execution.",
        evidence="trace:recall-1",
        proposal_type="intent_pattern",
    )

    assert issue_id == "issue-1"
    assert ("POST", "https://multica.example/api/issues/issue-1/labels", {
        "json": {"label_id": "label-1"},
        "headers": multica_client.MULClient()._headers(),
        "params": {"workspace_id": "workspace-1"},
    }) in requests


@pytest.mark.asyncio
async def test_sync_evolution_proposal_ignores_non_dict_created_label_response(monkeypatch):
    requests = []

    class FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    class FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            requests.append(("POST", url, kwargs))
            if url.endswith("/api/issues"):
                return FakeResponse({"id": "issue-1"})
            if url.endswith("/api/labels"):
                return FakeResponse(["not-a-dict"], status_code=201)
            raise AssertionError(f"unexpected post: {url}")

        async def get(self, url, **kwargs):
            requests.append(("GET", url, kwargs))
            if url.endswith("/api/labels"):
                return FakeResponse([])
            return FakeResponse([])

    monkeypatch.setenv("MULTICA_BASE_URL", "https://multica.example")
    monkeypatch.setenv("MULTICA_API_TOKEN", "token-1")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-1")
    monkeypatch.setenv("HERMES_MULTICA_AGENT_ID", "hermes-agent")
    monkeypatch.setattr(multica_client, "_lookup_evolution_resources", lambda _client: _async_tuple())
    monkeypatch.setattr(multica_client.httpx, "AsyncClient", lambda **_kwargs: FakeHttpClient())

    issue_id = await multica_client.sync_evolution_proposal_to_multica(
        proposal_id="proposal-1",
        title="Improve recall",
        description="Recall needs reviewable evidence before execution.",
        evidence="trace:recall-1",
        proposal_type="intent_pattern",
    )

    assert issue_id == "issue-1"
    assert not any(url.endswith("/api/issues/issue-1/labels") for _method, url, _kwargs in requests)


@pytest.mark.asyncio
async def test_sync_evolution_proposal_returns_none_for_non_dict_created_issue(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return ["not-a-dict"]

    class FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **_kwargs):
            return FakeResponse()

        async def get(self, url, **_kwargs):
            raise AssertionError(f"unexpected get after missing issue id: {url}")

    monkeypatch.setenv("MULTICA_BASE_URL", "https://multica.example")
    monkeypatch.setenv("MULTICA_API_TOKEN", "token-1")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-1")
    monkeypatch.setenv("HERMES_MULTICA_AGENT_ID", "hermes-agent")
    monkeypatch.setattr(multica_client, "_lookup_evolution_resources", lambda _client: _async_tuple())
    monkeypatch.setattr(multica_client.httpx, "AsyncClient", lambda **_kwargs: FakeHttpClient())

    issue_id = await multica_client.sync_evolution_proposal_to_multica(
        proposal_id="proposal-1",
        title="Improve recall",
        description="Recall needs reviewable evidence before execution.",
        evidence="trace:recall-1",
        proposal_type="intent_pattern",
    )

    assert issue_id is None


async def _async_tuple():
    return None, None


# ── Outage observability: distinguish a real failure from an empty board ──────


class _RaisingHttpClient:
    """Fake httpx.AsyncClient whose every verb raises the given error."""

    def __init__(self, exc):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def get(self, *args, **kwargs):
        raise self._exc

    async def put(self, *args, **kwargs):
        raise self._exc


def _configure_multica(monkeypatch):
    monkeypatch.setenv("MULTICA_BASE_URL", "https://multica.example")
    monkeypatch.setenv("MULTICA_API_TOKEN", "token-1")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-1")


@pytest.mark.asyncio
async def test_list_issues_logs_warning_and_returns_empty_on_outage(monkeypatch, caplog):
    """A real outage is LOGGED at WARNING and still returns [] by default, so the
    existing `or []` callers keep working but the failure is no longer silent."""
    _configure_multica(monkeypatch)
    monkeypatch.setattr(
        multica_client.httpx,
        "AsyncClient",
        lambda **_kwargs: _RaisingHttpClient(httpx.ConnectError("connection refused")),
    )

    with caplog.at_level(logging.WARNING, logger=multica_client.__name__):
        result = await multica_client.MULClient().list_issues(status="todo")

    assert result == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any("list_issues failed" in r.getMessage() for r in warnings)


@pytest.mark.asyncio
async def test_list_issues_raises_typed_error_when_opted_in(monkeypatch, caplog):
    """With raise_on_error=True an outage surfaces as MulticaUnavailableError (carrying
    endpoint + cause) so a caller can tell an OUTAGE apart from an empty board — and it
    is still logged on the way out."""
    _configure_multica(monkeypatch)
    boom = httpx.ConnectError("connection refused")
    monkeypatch.setattr(
        multica_client.httpx, "AsyncClient", lambda **_kwargs: _RaisingHttpClient(boom)
    )

    with caplog.at_level(logging.WARNING, logger=multica_client.__name__):
        with pytest.raises(multica_client.MulticaUnavailableError) as excinfo:
            await multica_client.MULClient().list_issues(status="todo", raise_on_error=True)

    assert excinfo.value.cause is boom
    assert excinfo.value.endpoint.endswith("/api/issues")
    assert any("list_issues failed" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_list_issues_empty_board_does_not_raise_when_opted_in(monkeypatch):
    """HARD CONSTRAINT: a legitimately empty 200 board must behave exactly as before —
    return [] and NEVER raise — even with raise_on_error=True."""

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return []

    class FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return FakeResponse()

    _configure_multica(monkeypatch)
    monkeypatch.setattr(multica_client.httpx, "AsyncClient", lambda **_kwargs: FakeHttpClient())

    result = await multica_client.MULClient().list_issues(status="todo", raise_on_error=True)
    assert result == []


@pytest.mark.asyncio
async def test_typed_error_captures_http_status(monkeypatch):
    """An HTTP error response status is captured on the typed exception for log/metric
    context (endpoint + status)."""
    _configure_multica(monkeypatch)
    request = httpx.Request("GET", "https://multica.example/api/issues")
    response = httpx.Response(503, request=request)
    boom = httpx.HTTPStatusError("service unavailable", request=request, response=response)
    monkeypatch.setattr(
        multica_client.httpx, "AsyncClient", lambda **_kwargs: _RaisingHttpClient(boom)
    )

    with pytest.raises(multica_client.MulticaUnavailableError) as excinfo:
        await multica_client.MULClient().list_issues(raise_on_error=True)

    assert excinfo.value.status == 503


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method, kwargs",
    [
        ("get_issue", {"issue_id": "abc"}),
        ("update_issue", {"issue_id": "abc", "status": "done"}),
    ],
)
async def test_get_and_update_issue_raise_typed_error_when_opted_in(monkeypatch, method, kwargs):
    """get_issue / update_issue default to swallowing (empty dict) but raise the typed
    outage error when the caller opts in."""
    _configure_multica(monkeypatch)
    boom = httpx.ConnectError("down")
    monkeypatch.setattr(
        multica_client.httpx, "AsyncClient", lambda **_kwargs: _RaisingHttpClient(boom)
    )
    client = multica_client.MULClient()

    assert await getattr(client, method)(**kwargs) == {}
    with pytest.raises(multica_client.MulticaUnavailableError):
        await getattr(client, method)(**kwargs, raise_on_error=True)
