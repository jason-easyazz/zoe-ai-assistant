import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import multica_client


def test_mul_client_reads_env_at_instantiation(monkeypatch):
    monkeypatch.setenv("MULTICA_BASE_URL", "https://multica.example/")
    monkeypatch.setenv("MULTICA_API_TOKEN", "token-1")
    monkeypatch.setenv("MULTICA_WORKSPACE_ID", "workspace-1")

    client = multica_client.MULClient()

    assert client._base == "https://multica.example"
    assert client._token == "token-1"
    assert client._workspace == "workspace-1"
    assert client.is_configured() is True


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
