import asyncio
import importlib.util
import os
from pathlib import Path

import runtime_env


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


def test_report_module_self_loads_env_on_import(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("MULTICA_BASE_URL=http://fixture:8080\n", encoding="utf-8")
    monkeypatch.setattr(runtime_env, "_ENV_FILES", (str(env_file),))
    monkeypatch.delenv("MULTICA_BASE_URL", raising=False)
    runtime_env._ENV_BOOTSTRAPPED = False

    _module()

    assert os.environ.get("MULTICA_BASE_URL") == "http://fixture:8080"


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
