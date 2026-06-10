import asyncio
import importlib.util
from pathlib import Path


def _module():
    path = (
        Path(__file__).resolve().parents[3]
        / "scripts/maintenance/multica_health_report.py"
    )
    spec = importlib.util.spec_from_file_location("multica_health_report", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_probe_reports_http_status(monkeypatch):
    module = _module()

    class Response:
        status_code = 200

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: Client())
    result = asyncio.run(module._probe("http://example.test/health"))
    assert result["ok"] is True
    assert result["status"] == 200


def test_probe_rejects_client_errors(monkeypatch):
    module = _module()

    class Response:
        status_code = 404

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    monkeypatch.setattr(module.httpx, "AsyncClient", lambda **_kwargs: Client())
    result = asyncio.run(module._probe("http://example.test/missing"))
    assert result["ok"] is False
    assert result["status"] == 404


def test_upload_check_reads_the_runtime_compose_contract():
    module = _module()

    assert module._uploads_configured() is True


def test_oidc_client_id_requires_explicit_configuration(monkeypatch):
    module = _module()
    monkeypatch.delenv("MULTICA_OIDC_CLIENT_ID", raising=False)

    assert module._oidc_client_id_configured() is False
    monkeypatch.setenv("MULTICA_OIDC_CLIENT_ID", "multica")
    assert module._oidc_client_id_configured() is True


def test_load_default_env_reads_root_and_service_env_without_overriding(tmp_path, monkeypatch):
    module = _module()
    root = tmp_path / "repo"
    service = root / "services" / "zoe-data"
    service.mkdir(parents=True)
    (root / ".env").write_text("MULTICA_BASE_URL=http://root.example\nEXPORTED=from-root\n", encoding="utf-8")
    (service / ".env").write_text("MULTICA_WORKSPACE_ID=workspace-1\nEXPORTED=from-service\n", encoding="utf-8")

    class FakeScriptPath:
        def resolve(self):
            return self

        @property
        def parents(self):
            return [tmp_path, tmp_path, root]

    monkeypatch.setattr(module, "Path", lambda *_args, **_kwargs: FakeScriptPath())
    monkeypatch.setenv("EXPORTED", "already-set")
    monkeypatch.delenv("MULTICA_BASE_URL", raising=False)
    monkeypatch.delenv("MULTICA_WORKSPACE_ID", raising=False)

    module._load_default_env()

    assert module.os.environ["MULTICA_BASE_URL"] == "http://root.example"
    assert module.os.environ["MULTICA_WORKSPACE_ID"] == "workspace-1"
    assert module.os.environ["EXPORTED"] == "already-set"


def test_agent_runtime_contracts_require_cli_version_and_zoe_tools():
    module = _module()
    agents = [
        {
            "name": "Hermes",
            "runtime_id": "rt-hermes",
            "status": "idle",
            "max_concurrent_tasks": 1,
            "model": "main",
            "mcp_config": {
                "servers": {
                    "zoe-tools": {
                        "url": "http://zoe-ui/api/mcp",
                        "transport": "http",
                    }
                }
            },
        },
        {
            "name": "OpenClaw",
            "runtime_id": "rt-openclaw",
            "status": "idle",
            "max_concurrent_tasks": 1,
            "model": "main",
            "mcp_config": {
                "servers": {
                    "zoe-tools": {
                        "url": "http://zoe-ui/api/mcp",
                        "transport": "http",
                    }
                }
            },
        },
    ]
    runtimes = [
        {
            "id": "rt-hermes",
            "provider": "hermes",
            "status": "online",
            "metadata": {"cli_version": "0.3.19"},
        },
        {
            "id": "rt-openclaw",
            "provider": "openclaw",
            "status": "online",
            "metadata": {"cli_version": "0.3.19"},
        },
    ]

    contracts = module._agent_runtime_contracts(agents, runtimes)

    assert contracts["Hermes"]["ok"] is True
    assert contracts["OpenClaw"]["ok"] is True


def test_agent_runtime_contracts_fail_when_cli_version_missing():
    module = _module()
    agents = [
        {
            "name": "Hermes",
            "runtime_id": "rt-hermes",
            "max_concurrent_tasks": 1,
            "model": "main",
            "mcp_config": {"servers": {"zoe-tools": {"url": "http://zoe-ui/api/mcp"}}},
        }
    ]
    runtimes = [{"id": "rt-hermes", "provider": "hermes", "status": "online", "metadata": {}}]

    contracts = module._agent_runtime_contracts(agents, runtimes)

    assert contracts["Hermes"]["ok"] is False
    assert contracts["Hermes"]["runtime_cli_version"] == ""


def test_worker_profile_contexts_reject_zoe_self_blocks(tmp_path, monkeypatch):
    module = _module()
    good = tmp_path / "zoe-coder" / "SOUL.md"
    bad = tmp_path / "zoe-planner" / "SOUL.md"
    good.parent.mkdir(parents=True)
    bad.parent.mkdir(parents=True)
    good.write_text("kanban_show kanban_complete kanban_block")
    bad.write_text("ZOE_SELF_BEGIN\nfull Zoe context\nZOE_SELF_END")
    monkeypatch.setattr(module, "_HERMES_WORKER_SOULS", (good, bad))

    result = module._worker_profile_contexts()

    assert result["zoe-coder"]["ok"] is True
    assert result["zoe-planner"]["ok"] is False
    assert result["zoe-planner"]["has_zoe_self"] is True


def test_worker_dispatch_contract_requires_lean_dropin(tmp_path, monkeypatch):
    module = _module()
    dropin = tmp_path / "kanban-worker-lean.conf"
    system_prompt = tmp_path / "system_prompt.py"
    monkeypatch.setattr(module, "_HERMES_KANBAN_WORKER_DROPIN", dropin)
    kanban_tools = tmp_path / "kanban_tools.py"
    monkeypatch.setattr(module, "_HERMES_SYSTEM_PROMPT_FILE", system_prompt)
    monkeypatch.setattr(module, "_HERMES_KANBAN_TOOLS_FILE", kanban_tools)

    assert module._worker_dispatch_contract()["ok"] is False
    dropin.write_text(
        """[Service]
Environment=HERMES_KANBAN_WORKER_IGNORE_RULES=true
Environment=HERMES_KANBAN_WORKER_TOOLSETS=terminal,file,kanban
Environment=HERMES_KANBAN_LEAN_SYSTEM=true
Environment=HERMES_KANBAN_WORKER_AUTO_SKILL=false
Environment=HERMES_KANBAN_COMPACT_SHOW=true
"""
    )
    system_prompt.write_text("HERMES_KANBAN_LEAN_SYSTEM HERMES_KANBAN_TASK")
    kanban_tools.write_text("HERMES_KANBAN_COMPACT_SHOW _compact_show_for_worker")
    assert module._worker_dispatch_contract()["ok"] is True


def test_worker_tool_envelope_rejects_large_or_mcp_toolset(monkeypatch, tmp_path):
    module = _module()
    soul = tmp_path / "zoe-coder" / "SOUL.md"
    soul.parent.mkdir(parents=True)
    soul.write_text("kanban_show kanban_complete kanban_block")
    monkeypatch.setattr(module, "_HERMES_WORKER_SOULS", (soul,))
    monkeypatch.setattr(module, "_HERMES_AGENT_ROOT", tmp_path / "agent")
    monkeypatch.setattr(module, "_HERMES_AGENT_PYTHON", tmp_path / "agent" / "venv" / "bin" / "python")

    class Completed:
        returncode = 0
        stderr = ""
        stdout = '{"toolsets":["file","kanban","terminal"],"tool_count":2,"max_tokens":1024,"context_length":8192,"ollama_num_ctx":16384,"base_url":"https://openrouter.ai/api/v1","file_read_max_chars":6000,"tool_output":{"max_bytes":8000,"max_line_length":300,"max_lines":120},"tools":["kanban_show","mcp_zoe_tools_memory_search"]}\n'

    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: Completed())

    result = module._worker_tool_envelope()

    assert result["zoe-coder"]["ok"] is False
    assert result["zoe-coder"]["disallowed_tools"] == ["mcp_zoe_tools_memory_search"]
    assert result["zoe-coder"]["max_tokens_ok"] is True
    assert result["zoe-coder"]["context_length_ok"] is False
    assert result["zoe-coder"]["ollama_num_ctx_ok"] is False
    assert result["zoe-coder"]["base_url_ok"] is False


def test_worker_tool_envelope_accepts_lean_kanban_tools(monkeypatch, tmp_path):
    module = _module()
    soul = tmp_path / "zoe-coder" / "SOUL.md"
    soul.parent.mkdir(parents=True)
    soul.write_text("kanban_show kanban_complete kanban_block")
    monkeypatch.setattr(module, "_HERMES_WORKER_SOULS", (soul,))
    monkeypatch.setattr(module, "_HERMES_WORKER_TOOL_LIMIT", 3)

    class Completed:
        returncode = 0
        stderr = ""
        stdout = '{"toolsets":["file","kanban","terminal"],"tool_count":3,"max_tokens":1024,"context_length":64000,"ollama_num_ctx":65536,"base_url":"http://127.0.0.1:11434/v1","file_read_max_chars":6000,"tool_output":{"max_bytes":8000,"max_line_length":300,"max_lines":120},"tools":["terminal","kanban_show","kanban_complete"]}\n'

    monkeypatch.setattr(module.subprocess, "run", lambda *_args, **_kwargs: Completed())

    result = module._worker_tool_envelope()["zoe-coder"]

    assert result["ok"] is True
    assert result["max_tokens"] == 1024
    assert result["context_length"] == 64000
    assert result["context_length_ok"] is True
    assert result["ollama_num_ctx"] == 65536
    assert result["ollama_num_ctx_ok"] is True
    assert result["base_url"] == "http://127.0.0.1:11434/v1"
    assert result["base_url_ok"] is True
