import os
import tempfile
import importlib.util

from fastapi.testclient import TestClient


def load_app(tmpdir):
    os.environ["DATABASE_PATH"] = os.path.join(tmpdir, "test.db")
    os.environ["ZOE_DATA_DIR"] = tmpdir
    spec = importlib.util.spec_from_file_location(
        "zoe_core_main", "services/zoe-core/main_complex_with_auth.py"
    )
    main = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(main)
    main.current_session["username"] = "admin"
    main.current_session["role"] = "admin"
    return main.app


def test_module_toggle_and_list():
    with tempfile.TemporaryDirectory() as tmpdir:
        app = load_app(tmpdir)
        with TestClient(app) as client:
            resp = client.get("/api/modules/list")
            data = resp.json()["modules"]
            assert data, "no modules returned"
            first = data[0]["name"]
            toggle_resp = client.post(
                "/api/modules/toggle", json={"name": first, "enabled": True}
            )
            assert toggle_resp.json()["enabled"] is True
            resp2 = client.get("/api/modules/list")
            match = next(m for m in resp2.json()["modules"] if m["name"] == first)
            assert match["enabled"] is True


def test_backup_creation_and_listing():
    with tempfile.TemporaryDirectory() as tmpdir:
        app = load_app(tmpdir)
        with TestClient(app) as client:
            create = client.post("/api/backup")
            assert create.status_code == 200
            fname = create.json()["filename"]
            listing = client.get("/api/backups")
            assert fname in listing.json()["backups"]
