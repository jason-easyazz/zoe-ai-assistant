import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import multica_client
import pytest

from multica_ticket_contract import parse_ticket_block
from zoe_evolution_proposal_adapter import dump_mcp_evolution_proposal_contract


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


async def _async_tuple():
    return None, None
