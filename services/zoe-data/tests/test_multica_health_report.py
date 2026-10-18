import pytest
import asyncio
import importlib.util
import os
from pathlib import Path

import runtime_env

pytestmark = pytest.mark.ci_safe


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
    monkeypatch.setattr(runtime_env, "_ENV_BOOTSTRAPPED", False)

    # bootstrap writes os.environ directly (not via monkeypatch), so pop it in a
    # finally to guarantee the fixture value never leaks into later tests.
    try:
        _module()
        assert os.environ.get("MULTICA_BASE_URL") == "http://fixture:8080"
    finally:
        os.environ.pop("MULTICA_BASE_URL", None)


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


def _gated_probe(monkeypatch, status, location=""):
    module = _module()

    class Response:
        status_code = status
        headers = {"location": location} if location else {}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, *_args, **_kwargs):
            return Response()

    seen = {}

    def _client(**kwargs):
        seen.update(kwargs)
        return Client()

    monkeypatch.setattr(module.httpx, "AsyncClient", _client)
    result = asyncio.run(module._probe_gated("http://example.test/multica/"))
    assert seen.get("follow_redirects") is False  # following the 302 would report the launcher page's 200 as the board's
    return result


def test_gated_probe_counts_the_sign_in_gate_refusal_as_healthy(monkeypatch):
    for status, location in ((401, ""), (403, ""), (302, "/board.html?next=/multica/")):
        result = _gated_probe(monkeypatch, status, location)
        assert result["ok"] is True and result["gated"] is True, (status, result)


def test_gated_probe_reports_an_open_gate_and_real_failures(monkeypatch):
    assert _gated_probe(monkeypatch, 200) == {"ok": True, "gated": False, "status": 200, "url": "http://example.test/multica/"}
    for status, location in ((502, ""), (500, ""), (404, ""), (302, "/elsewhere")):
        result = _gated_probe(monkeypatch, status, location)
        assert result["ok"] is False and result["gated"] is False, (status, result)


def test_upload_check_reads_the_runtime_compose_contract():
    module = _module()

    assert module._uploads_configured() is True


def test_oidc_client_id_requires_explicit_configuration(monkeypatch):
    module = _module()
    monkeypatch.delenv("MULTICA_OIDC_CLIENT_ID", raising=False)

    assert module._oidc_client_id_configured() is False
    monkeypatch.setenv("MULTICA_OIDC_CLIENT_ID", "multica")
    assert module._oidc_client_id_configured() is True


def test_native_comment_guard_unset_is_not_an_error(monkeypatch):
    module = _module()
    monkeypatch.delenv("ZOE_MULTICA_NATIVE_COMMENT_GUARD", raising=False)
    assert module._native_comment_guard_status() == "unset"
    # Blank string is treated the same as absent.
    monkeypatch.setenv("ZOE_MULTICA_NATIVE_COMMENT_GUARD", "  ")
    assert module._native_comment_guard_status() == "unset"


def test_native_comment_guard_enabled_for_truthy_values(monkeypatch):
    module = _module()
    for value in ("1", "true", "TRUE", "yes", "On"):
        monkeypatch.setenv("ZOE_MULTICA_NATIVE_COMMENT_GUARD", value)
        assert module._native_comment_guard_status() == "enabled"


def test_native_comment_guard_disabled_only_when_explicitly_off(monkeypatch):
    module = _module()
    for value in ("false", "0", "no", "off"):
        monkeypatch.setenv("ZOE_MULTICA_NATIVE_COMMENT_GUARD", value)
        assert module._native_comment_guard_status() == "disabled"


def test_report_surfaces_guard_status_without_a_live_database(monkeypatch):
    import multica_client

    module = _module()
    monkeypatch.setenv("ZOE_MULTICA_NATIVE_COMMENT_GUARD", "false")

    class _Unconfigured:
        def is_configured(self):
            return False

    # run() does `from multica_client import ...` at call time, so patch the
    # source module rather than the script module.
    monkeypatch.setattr(multica_client, "get_multica_client", lambda: _Unconfigured())
    monkeypatch.setattr(
        multica_client, "get_engineering_multica_agent_id", lambda: None
    )

    report = asyncio.run(module.run())
    # The field is present even on the unconfigured early-return path, and an
    # explicitly-off guard is surfaced as "disabled". ok is False here only
    # because the client is unconfigured, not because the guard is off.
    assert report["native_comment_guard"] == "disabled"
    assert report["ok"] is False
