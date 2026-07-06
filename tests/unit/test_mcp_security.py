"""Security checks for production MCP actor resolution."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Slim-dep green: opts into the GitHub-runner fast lane (see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ZOE_DATA = PROJECT_ROOT / "services" / "zoe-data"


def _import_mcp_server():
    sys.path.insert(0, str(ZOE_DATA))
    try:
        import mcp_server
    except Exception as exc:
        pytest.skip(f"Cannot import production mcp_server.py dependencies: {exc}")
    return mcp_server


class _RoleCursor:
    def __init__(self, row=None):
        self._row = row

    async def fetchone(self):
        return self._row


class _RoleDb:
    def __init__(self, role=None):
        self.role = role
        self.calls = []

    async def execute(self, sql, params=None):
        self.calls.append((sql, params))
        return _RoleCursor({"role": self.role} if self.role else None)


def test_trusted_actor_context_uses_transport_metadata_not_tool_args(monkeypatch):
    mcp_server = _import_mcp_server()
    monkeypatch.delenv("ZOE_MCP_ACTOR_USER_ID", raising=False)
    monkeypatch.delenv("ZOE_MCP_USER_ID", raising=False)

    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "list_get_items",
            "arguments": {"user_id": "attacker-user", "_user_id": "attacker-user"},
            "_meta": {"zoe": {"actor_user_id": "transport-user"}},
        },
    }

    context = mcp_server._trusted_actor_context_from_message(message)
    assert context["user_id"] == "transport-user"
    assert context["source"] == "transport_meta"
    assert context["role"] is None


@pytest.mark.asyncio
async def test_resolve_mcp_actor_drops_caller_user_overrides_from_stdio_context():
    mcp_server = _import_mcp_server()
    args = {"user_id": "attacker-user", "_user_id": "attacker-user", "list_type": "shopping"}

    actor = await mcp_server._resolve_mcp_actor(
        _RoleDb(role="admin"),
        "list_get_items",
        args,
        actor_context={"user_id": "transport-user", "source": "transport_meta"},
    )

    assert actor["user_id"] == "transport-user"
    assert actor["role"] == "member"
    assert "_user_id" not in args


def test_authorized_target_user_blocks_non_admin_override():
    mcp_server = _import_mcp_server()

    actor = {"user_id": "member-user", "role": "member"}
    assert mcp_server._authorized_target_user(actor, "other-user", "list_get_items") == "member-user"


def test_authorized_target_user_allows_admin_override():
    mcp_server = _import_mcp_server()

    actor = {"user_id": "admin-user", "role": "admin"}
    assert mcp_server._authorized_target_user(actor, "other-user", "list_get_items") == "other-user"


def test_env_actor_role_is_only_trusted_with_env_actor(monkeypatch):
    mcp_server = _import_mcp_server()
    monkeypatch.setenv("ZOE_MCP_ACTOR_USER_ID", "env-user")
    monkeypatch.setenv("ZOE_MCP_ACTOR_ROLE", "admin")

    context = mcp_server._trusted_actor_context_from_message(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"_meta": {}}}
    )

    assert context["user_id"] == "env-user"
    assert context["role"] == "admin"
    assert context["role_source"] == "env"
