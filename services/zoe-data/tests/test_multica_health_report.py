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
