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


def _fake_node_runtime(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    _write_exe(bindir / "node", "#!/bin/sh\nexit 0\n")
    _write_exe(bindir / "npm", "#!/bin/sh\nexit 0\n")
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
    monkeypatch.delenv("ZOE_PI_INTENT_PROMOTED_GROUPS", raising=False)
    app = _admin_app()

    resp = TestClient(app).get("/api/system/pi-intent/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["status"] == "disabled"
    assert data["config"]["enabled"] is False
    assert data["config"]["transport"] == "print"
    assert data["promotion"]["active_groups"] == []


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


def test_pi_intent_status_endpoint_reports_available_execution_disabled_when_tools_exist_and_allow_execution_false(tmp_path, monkeypatch):
    bindir = _fake_pi_runtime(tmp_path)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("ZOE_PI_INTENT_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_CWD", str(tmp_path))
    monkeypatch.setenv("ZOE_PI_LOCAL_MODEL_CONFIGURED", "true")
    monkeypatch.setenv("ZOE_PI_ALLOW_EXECUTION", "false")
    app = _admin_app()

    resp = TestClient(app).get("/api/system/pi-intent/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["status"] == "available_execution_disabled"
    assert data["probe"]["config"]["allow_execution"] is False
    assert data["probe"]["config"]["local_model_configured"] is True


def test_pi_intent_status_endpoint_reports_missing_pi_when_tools_absent(tmp_path, monkeypatch):
    bindir = _fake_node_runtime(tmp_path)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("ZOE_PI_INTENT_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_CWD", str(tmp_path))
    monkeypatch.setenv("ZOE_PI_LOCAL_MODEL_CONFIGURED", "true")
    monkeypatch.setenv("ZOE_PI_ALLOW_EXECUTION", "true")
    app = _admin_app()

    resp = TestClient(app).get("/api/system/pi-intent/status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["status"] == "missing_pi"


def test_pi_intent_status_endpoint_reports_available_with_explicit_gates(tmp_path, monkeypatch):
    bindir = _fake_pi_runtime(tmp_path)
    monkeypatch.setenv("PATH", str(bindir))
    monkeypatch.setenv("ZOE_PI_INTENT_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_TRANSPORT", "rpc")
    monkeypatch.setenv("ZOE_PI_INTENT_PROMOTED_GROUPS", "weather,device_control")
    monkeypatch.setenv("ZOE_PI_INTENT_AUTO_PROMOTE", "true")
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
    assert data["promotion"]["auto_promote_requested"] is True
    assert data["promotion"]["auto_promote_status"] == "requires_explicit_apply_path"
    assert data["promotion"]["active_groups"] == ["weather"]
    assert data["promotion"]["ignored_groups"] == ["device_control"]
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


def test_pi_intent_shadow_status_endpoint_is_admin_scoped(tmp_path, monkeypatch):
    path = tmp_path / "shadow.jsonl"
    path.write_text('{"agreement":true,"timed_out":false,"zoe_intent_group":"weather","pi_latency_ms":100,"zoe_latency_ms":5}\n')
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_PATH", str(path))
    app = _admin_app()

    resp = TestClient(app).get("/api/system/pi-intent/shadow-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["config"]["enabled"] is True
    assert data["record_count_window"] == 1
    assert data["report"]["agreement_rate"] == 1.0


def test_pi_hybrid_buffer_status_endpoint_reports_shadow_buffer_ready(tmp_path, monkeypatch):
    path = tmp_path / "shadow.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    path.write_text('{"agreement":true,"timed_out":false,"zoe_intent_group":"weather","pi_latency_ms":100,"zoe_latency_ms":5}\n')
    monkeypatch.setenv("ZOE_WAKE_ACK_PHRASES", "Yes Jason.")
    monkeypatch.setenv("ZOE_PROCESSING_ACK_PHRASES", "Let me check.")
    monkeypatch.setenv("ZOE_PI_INTENT_ENABLED", "false")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_TRANSPORT", "rpc")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_PATH", str(path))
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_LABELS_PATH", str(labels_path))
    monkeypatch.delenv("ZOE_PI_INTENT_PROMOTED_GROUPS", raising=False)
    app = _admin_app()

    resp = TestClient(app).get("/api/system/pi-intent/hybrid-buffer-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["contract"]["mode"] == "shadow_buffer"
    assert data["contract"]["ready"] is True
    assert data["contract"]["processing_ack_ready"] is True
    assert data["contract"]["pi_shadow_enabled"] is True
    assert data["contract"]["pi_execution_enabled"] is False
    assert data["pi"]["shadow"]["record_count_window"] == 1


def test_pi_hybrid_buffer_status_blocks_execution_without_promoted_groups(tmp_path, monkeypatch):
    path = tmp_path / "shadow.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    path.write_text("", encoding="utf-8")
    labels_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("ZOE_WAKE_ACK_PHRASES", "Yes Jason.")
    monkeypatch.setenv("ZOE_PROCESSING_ACK_PHRASES", "Let me check.")
    monkeypatch.setenv("ZOE_PI_INTENT_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_PROMOTED_GROUPS", "")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_PATH", str(path))
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_LABELS_PATH", str(labels_path))
    app = _admin_app()

    resp = TestClient(app).get("/api/system/pi-intent/hybrid-buffer-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["contract"]["mode"] == "shadow_with_execution_misconfigured"
    assert data["contract"]["ready"] is False
    assert "pi_execution_enabled_without_promoted_groups" in data["contract"]["blockers"]


def test_pi_intent_shadow_status_endpoint_rejects_non_admin():
    app = FastAPI()
    app.include_router(system_router)

    async def fake_non_admin():
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin] = fake_non_admin

    resp = TestClient(app).get("/api/system/pi-intent/shadow-status")

    assert resp.status_code == 403


def test_pi_hybrid_buffer_status_endpoint_rejects_non_admin():
    app = FastAPI()
    app.include_router(system_router)

    async def fake_non_admin():
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin] = fake_non_admin

    resp = TestClient(app).get("/api/system/pi-intent/hybrid-buffer-status")

    assert resp.status_code == 403
