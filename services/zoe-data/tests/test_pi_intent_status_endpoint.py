import pytest
import json
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from auth import require_admin
from routers.system import _pi_hybrid_production_public_status, router as system_router

pytestmark = pytest.mark.ci_safe


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
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
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


def test_pi_hybrid_buffer_status_reports_enabled_pi_without_promotions_as_nonblocking_shadow(tmp_path, monkeypatch):
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
    assert data["contract"]["mode"] == "shadow_buffer"
    assert data["contract"]["ready"] is True
    assert data["contract"]["pi_classifier_enabled"] is True
    assert data["contract"]["pi_execution_enabled"] is True
    assert data["contract"]["foreground_pi_execution_enabled"] is False
    assert "pi_execution_enabled_without_promoted_groups" not in data["contract"]["blockers"]
    assert "pi_classifier_enabled_without_promoted_groups_runs_shadow_only" in data["contract"]["warnings"]


def test_pi_hybrid_buffer_status_blocks_promoted_groups_when_classifier_disabled(tmp_path, monkeypatch):
    path = tmp_path / "shadow.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    path.write_text("", encoding="utf-8")
    labels_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("ZOE_WAKE_ACK_PHRASES", "Yes Jason.")
    monkeypatch.setenv("ZOE_PROCESSING_ACK_PHRASES", "Let me check.")
    monkeypatch.setenv("ZOE_PI_INTENT_ENABLED", "false")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_PROMOTED_GROUPS", "weather")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_PATH", str(path))
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_LABELS_PATH", str(labels_path))
    app = _admin_app()

    resp = TestClient(app).get("/api/system/pi-intent/hybrid-buffer-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["contract"]["mode"] == "shadow_buffer"
    assert data["contract"]["ready"] is False
    assert data["contract"]["pi_classifier_enabled"] is False
    assert data["contract"]["foreground_pi_execution_enabled"] is False
    assert data["contract"]["promoted_groups"] == ["weather"]
    assert "promoted_groups_without_pi_classifier_enabled" in data["contract"]["blockers"]


def test_pi_hybrid_production_status_endpoint_reports_live_config(monkeypatch):
    monkeypatch.setenv("ZOE_PI_HYBRID_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_HYBRID_PRODUCTION_GROUPS", "weather,daily_briefing")
    monkeypatch.setenv("ZOE_PI_HYBRID_PRODUCTION_TRANSPORT", "rpc")
    monkeypatch.setenv("ZOE_PI_HYBRID_RESOURCE_GUARD_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_HYBRID_MIN_AVAILABLE_MB", "1024")
    monkeypatch.setenv("ZOE_PI_HYBRID_MIN_SWAP_FREE_MB", "128")
    app = _admin_app()

    resp = TestClient(app).get("/api/system/pi-intent/production-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["report_kind"] == "zoe_pi_hybrid_production_status"
    assert data["ok"] is True
    assert data["status"] == "enabled"
    assert data["route"] == "pi_intent_buffer_plus_zoe_safe_fulfillment"
    assert "voice_non_stream" in data["surfaces"]
    assert data["config"]["enabled"] is True
    assert set(data["config"]["groups"]) == {"weather", "daily_briefing"}
    assert data["config"]["transport"] == "rpc"
    assert data["config"]["resource_guard_enabled"] is True
    assert data["config"]["min_available_mb"] == 1024
    assert data["config"]["min_swap_free_mb"] == 128


def test_pi_hybrid_production_status_endpoint_reports_invalid_config(monkeypatch):
    monkeypatch.setenv("ZOE_PI_HYBRID_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_HYBRID_PRODUCTION_GROUPS", "weather,device_control")
    app = _admin_app()

    resp = TestClient(app).get("/api/system/pi-intent/production-status")

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is False
    assert data["status"] == "invalid_config"
    assert "device_control" in data["error"]


def test_pi_hybrid_production_public_status_hides_admin_details(monkeypatch):
    monkeypatch.setenv("ZOE_PI_HYBRID_PRODUCTION_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_HYBRID_PRODUCTION_GROUPS", "weather,device_control")

    data = _pi_hybrid_production_public_status()

    assert data == {
        "report_kind": "zoe_pi_hybrid_production_summary",
        "ok": False,
        "status": "invalid_config",
        "details_endpoint": "/api/system/pi-intent/production-status",
    }
    assert "config" not in data
    assert "error" not in data


def test_pi_hybrid_production_status_endpoint_rejects_non_admin():
    app = FastAPI()
    app.include_router(system_router)

    async def fake_non_admin():
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin] = fake_non_admin

    resp = TestClient(app).get("/api/system/pi-intent/production-status")

    assert resp.status_code == 403


def test_pi_readiness_report_endpoint_is_admin_scoped(tmp_path, monkeypatch):
    path = tmp_path / "shadow.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    path.write_text("", encoding="utf-8")
    labels_path.write_text("", encoding="utf-8")
    monkeypatch.setenv("ZOE_WAKE_ACK_PHRASES", "Yes Jason.")
    monkeypatch.setenv("ZOE_PROCESSING_ACK_PHRASES", "Let me check.")
    monkeypatch.setenv("ZOE_PI_INTENT_ENABLED", "false")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_TRANSPORT", "rpc")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_PATH", str(path))
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_LABELS_PATH", str(labels_path))
    app = _admin_app()

    resp = TestClient(app).get("/api/system/pi-intent/readiness-report")

    assert resp.status_code == 200
    data = resp.json()
    assert data["report_kind"] == "zoe_pi_readiness_report"
    assert data["hybrid"]["ready"] is True


def test_pi_readiness_report_endpoint_rejects_non_admin():
    app = FastAPI()
    app.include_router(system_router)

    async def fake_non_admin():
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin] = fake_non_admin

    resp = TestClient(app).get("/api/system/pi-intent/readiness-report")

    assert resp.status_code == 403


def test_pi_intent_shadow_label_endpoint_appends_trusted_label(tmp_path, monkeypatch):
    shadow_path = tmp_path / "shadow.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    shadow_path.write_text(
        '{"text_hash":"weatherhash","text_preview":"rain later","route_class":"fallback","zoe_latency_ms":500,"pi_intent":"weather","pi_latency_ms":120,"pi_confidence":0.91}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_PATH", str(shadow_path))
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_LABELS_PATH", str(labels_path))
    app = _admin_app()

    resp = TestClient(app).post(
        "/api/system/pi-intent/shadow-labels",
        json={"text_hash": "weatherhash", "outcome_label": "weather"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["label"]["outcome_label"] == "weather"
    assert data["matched_record"]["text_preview"] == "rain later"
    saved = json.loads(labels_path.read_text(encoding="utf-8"))
    assert saved["text_hash"] == "weatherhash"
    assert saved["outcome_label"] == "weather"
    assert len(saved["reviewed_by_hash"]) == 64
    assert "labels_path" not in data
    assert data["labels_store"] == "shadow_labels_sidecar"

    status = TestClient(app).get("/api/system/pi-intent/shadow-status").json()
    assert status["label_count"] == 1
    assert status["report"]["accuracy_available"] is True


def test_pi_intent_shadow_label_endpoint_rejects_unlisted_source(tmp_path, monkeypatch):
    shadow_path = tmp_path / "shadow.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    shadow_path.write_text('{"text_hash":"known","text_preview":"rain later"}\n', encoding="utf-8")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_PATH", str(shadow_path))
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_LABELS_PATH", str(labels_path))
    app = _admin_app()

    resp = TestClient(app).post(
        "/api/system/pi-intent/shadow-labels",
        json={"text_hash": "known", "outcome_label": "weather", "source": "freeform"},
    )

    assert resp.status_code == 422
    assert not labels_path.exists()


def test_pi_intent_shadow_label_endpoint_rejects_bad_label(tmp_path, monkeypatch):
    shadow_path = tmp_path / "shadow.jsonl"
    labels_path = tmp_path / "labels.jsonl"
    shadow_path.write_text('{"text_hash":"known","text_preview":"upgrade yourself"}\n', encoding="utf-8")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_ENABLED", "true")
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_PATH", str(shadow_path))
    monkeypatch.setenv("ZOE_PI_INTENT_SHADOW_LABELS_PATH", str(labels_path))
    app = _admin_app()

    resp = TestClient(app).post(
        "/api/system/pi-intent/shadow-labels",
        json={"text_hash": "known", "outcome_label": "extend_capability"},
    )

    assert resp.status_code == 400
    assert "low-risk" in resp.json()["detail"]
    assert not labels_path.exists()


def test_pi_intent_shadow_label_endpoint_rejects_non_admin():
    app = FastAPI()
    app.include_router(system_router)

    async def fake_non_admin():
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin] = fake_non_admin

    resp = TestClient(app).post(
        "/api/system/pi-intent/shadow-labels",
        json={"text_hash": "known", "outcome_label": "weather"},
    )

    assert resp.status_code == 403




def test_pi_hybrid_production_label_queue_endpoint_returns_sanitized_rows(tmp_path, monkeypatch):
    production_path = tmp_path / "production.jsonl"
    labels_path = tmp_path / "production-labels.jsonl"
    production_path.write_text(
        json.dumps(
            {
                "text_hash": "weatherhash",
                "text_preview": "rain later",
                "accepted": True,
                "intent": "weather",
                "intent_group": "weather",
                "pi_intent": "weather",
                "outcome_label": None,
            }
        )
        + "\n"
        + json.dumps(
            {
                "text_hash": "briefinghash",
                "text_preview": "daily briefing",
                "accepted": True,
                "intent": "daily_briefing",
                "intent_group": "daily_briefing",
                "pi_intent": "daily_briefing",
                "outcome_label": None,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    labels_path.write_text(
        json.dumps({"text_hash": "briefinghash", "outcome_label": "daily_briefing", "source": "admin_review"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH", str(production_path))
    monkeypatch.setenv("ZOE_PI_HYBRID_PRODUCTION_LABELS_PATH", str(labels_path))
    app = _admin_app()

    resp = TestClient(app).get("/api/system/pi-intent/production-label-queue")

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["path"] == str(production_path)
    assert data["summary"]["labels_path"] == str(labels_path)
    assert data["summary"]["skipped_labeled_count"] == 1
    assert [row["text_hash"] for row in data["queue"]] == ["weatherhash"]
    assert data["queue"][0]["label_example"] == {
        "text_hash": "weatherhash",
        "source": "admin_review",
        "outcome_label": "weather",
    }


def test_pi_hybrid_production_label_queue_endpoint_rejects_non_admin():
    app = FastAPI()
    app.include_router(system_router)

    async def fake_non_admin():
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin] = fake_non_admin

    resp = TestClient(app).get("/api/system/pi-intent/production-label-queue")

    assert resp.status_code == 403


def test_pi_hybrid_production_label_endpoint_appends_trusted_label(tmp_path, monkeypatch):
    production_path = tmp_path / "production.jsonl"
    labels_path = tmp_path / "production-labels.jsonl"
    production_path.write_text(
        '{"text_hash":"weatherhash","text_preview":"rain later","accepted":true,"reason":"accepted","intent":"weather","intent_group":"weather","pi_intent":"weather"}\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("ZOE_PI_HYBRID_PRODUCTION_EVIDENCE_PATH", str(production_path))
    monkeypatch.setenv("ZOE_PI_HYBRID_PRODUCTION_LABELS_PATH", str(labels_path))
    app = _admin_app()

    resp = TestClient(app).post(
        "/api/system/pi-intent/production-labels",
        json={
            "text_hash": "weatherhash",
            "outcome_label": "weather",
            "route_class": "fallback",
            "baseline_kind": "zoe_agent_fallback_baseline",
            "baseline_comparable": True,
            "zoe_latency_ms": 4321.0,
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["label"]["outcome_label"] == "weather"
    assert data["label"]["route_class"] == "fallback"
    assert data["label"]["baseline_kind"] == "zoe_agent_fallback_baseline"
    assert data["label"]["baseline_comparable"] is True
    assert data["label"]["zoe_latency_ms"] == 4321.0
    assert data["matched_record"]["text_preview"] == "rain later"
    saved = json.loads(labels_path.read_text(encoding="utf-8"))
    assert saved["text_hash"] == "weatherhash"
    assert saved["outcome_label"] == "weather"
    assert saved["baseline_kind"] == "zoe_agent_fallback_baseline"
    assert saved["baseline_comparable"] is True
    assert saved["zoe_latency_ms"] == 4321.0
    assert len(saved["reviewed_by_hash"]) == 64
    assert data["labels_store"] == "production_labels_sidecar"


def test_pi_hybrid_production_label_endpoint_rejects_non_admin():
    app = FastAPI()
    app.include_router(system_router)

    async def fake_non_admin():
        raise HTTPException(status_code=403, detail="Admin access required")

    app.dependency_overrides[require_admin] = fake_non_admin

    resp = TestClient(app).post(
        "/api/system/pi-intent/production-labels",
        json={"text_hash": "known", "outcome_label": "weather"},
    )

    assert resp.status_code == 403


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
