from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from auth import require_admin
from routers.system import router as system_router


def _write_exe(path, body):
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _fake_pi_runtime(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_exe(bindir / "node", "#!/bin/sh\nexit 0\n")
    _write_exe(bindir / "npm", "#!/bin/sh\nexit 0\n")
    _write_exe(bindir / "pi", "#!/bin/sh\nexit 0\n")
    return bindir


def _admin_app():
    app = FastAPI()
    app.include_router(system_router)

    async def fake_admin():
        return {"user_id": "admin", "role": "family-admin"}

    app.dependency_overrides[require_admin] = fake_admin
    return app


def test_pi_intent_status_endpoint_is_admin_scoped_and_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ZOE_PI_INTENT_ENABLED", raising=False)
    app = _admin_app()

    resp = TestClient(app).get("/api/system/pi-intent/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["status"] == "disabled"
    assert data["config"]["enabled"] is False
    assert data["config"]["transport"] == "print"


def test_pi_intent_status_endpoint_reports_execution_disabled_when_tools_exist(tmp_path, monkeypatch):
    bindir = _fake_pi_runtime(tmp_path)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("ZOE_PI_INTENT_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_CWD", str(tmp_path))
    monkeypatch.setenv("ZOE_PI_LOCAL_MODEL_CONFIGURED", "true")
    monkeypatch.delenv("ZOE_PI_ALLOW_EXECUTION", raising=False)
    app = _admin_app()

    resp = TestClient(app).get("/api/system/pi-intent/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["status"] == "available_execution_disabled"
    assert data["probe"]["config"]["allow_execution"] is False
    assert data["probe"]["config"]["local_model_configured"] is True


def test_pi_intent_status_endpoint_reports_available_with_explicit_gates(tmp_path, monkeypatch):
    bindir = _fake_pi_runtime(tmp_path)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("ZOE_PI_INTENT_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_TRANSPORT", "rpc")
    monkeypatch.setenv("ZOE_PI_CWD", str(tmp_path))
    monkeypatch.setenv("ZOE_PI_ALLOW_EXECUTION", "true")
    monkeypatch.setenv("ZOE_PI_LOCAL_MODEL_CONFIGURED", "true")
    app = _admin_app()

    resp = TestClient(app).get("/api/system/pi-intent/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["status"] == "available"
    assert data["config"]["transport"] == "rpc"
    assert data["probe"]["config"]["allow_execution"] is True
    assert data["probe"]["config"]["local_model_configured"] is True


def test_pi_intent_status_endpoint_rejects_non_admin():
    app = FastAPI()
    app.include_router(system_router)

    async def fake_non_admin():
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin] = fake_non_admin

    resp = TestClient(app).get("/api/system/pi-intent/status")

    assert resp.status_code == 403
