"""Logic tests for scripts/maintenance/unit_drift_check.py (installed unit vs template).

Born of the 2026-10-04 router incident: a merged template carried ``--mlock`` while the live
unit did not, and every test (which reads the template) stayed green. Includes negative
controls and a check that the REAL templates parse (a parser that reads nothing reports
"no drift" about everything).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "maintenance" / "unit_drift_check.py"
TEMPLATES = ROOT / "scripts" / "setup" / "systemd"


@pytest.fixture(scope="module")
def udc():
    spec = importlib.util.spec_from_file_location("unit_drift_check", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["unit_drift_check"] = mod
    spec.loader.exec_module(mod)
    return mod


ROUTER = """\
[Unit]
Description=router
[Service]
Type=simple
ExecStart=/opt/llama/bin/llama-server \\
  --model %h/m.gguf \\
  --port 11436 \\
  --mlock \\
  --cache-ram 64
LimitMEMLOCK=infinity
MemoryMax=1280M
MemorySwapMax=0
Restart=always
"""


def _write(base: Path, name: str, main: str, dropins: dict[str, str] | None = None) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{name}.service").write_text(main, encoding="utf-8")
    if dropins:
        d = base / f"{name}.service.d"
        d.mkdir(exist_ok=True)
        for fname, text in dropins.items():
            (d / fname).write_text(text, encoding="utf-8")


def _kinds(findings):
    return {(f.kind, f.key) for f in findings}


def test_identical_units_have_no_drift_and_home_expands(udc, tmp_path):
    _write(tmp_path / "t", "r", ROUTER)
    # Live uses the literal path where the template uses %h.
    # Literal path where the template uses %h; 1280M spelled in bytes (sizes normalise).
    _write(tmp_path / "l", "r", ROUTER.replace("%h", "/home/x").replace("1280M", "1342177280"))
    findings, problems = udc.check(["r"], tmp_path / "t", tmp_path / "l", "/home/x")
    assert findings == [] and problems == []


def test_each_drift_kind_is_reported(udc, tmp_path):
    live = (
        ROUTER.replace("  --mlock \\\n", "")
        .replace("LimitMEMLOCK=infinity\n", "")
        .replace("MemoryMax=1280M", "MemoryMax=1G")
        + "Nice=10\n"
    )
    _write(tmp_path / "t", "r", ROUTER)
    _write(tmp_path / "l", "r", live)
    findings, _ = udc.check(["r"], tmp_path / "t", tmp_path / "l", "/home/x")
    assert _kinds(findings) == {
        ("missing", "ExecStart --mlock"),
        ("missing", "LimitMEMLOCK"),
        ("differs", "MemoryMax"),
        ("live_only", "Nice"),
    }


def test_dropin_reset_semantics_match_systemd(udc, tmp_path):
    """An empty ExecStart= in a drop-in RESETS the list; without it a second
    ExecStart would be appended and the model would read the wrong (last) line."""
    venv = "[Service]\nExecStart=\nExecStart=/venv/bin/python %h/app.py\n"
    main = "[Service]\nExecStart=/usr/bin/python3 %h/app.py\n"
    _write(tmp_path / "t", "k", main, {"60-venv.conf": venv})
    _write(tmp_path / "l", "k", main, {"60-venv.conf": venv})
    findings, _ = udc.check(["k"], tmp_path / "t", tmp_path / "l", "/home/x")
    assert findings == []
    # Negative control: the live host never installed the drop-in.
    _write(tmp_path / "l2", "k", main)
    findings, _ = udc.check(["k"], tmp_path / "t", tmp_path / "l2", "/home/x")
    assert ("differs", "ExecStart binary") in _kinds(findings)


def test_secret_environment_values_are_never_printed(udc, tmp_path, capsys):
    t = "[Service]\nExecStart=/bin/x\nEnvironment=API_TOKEN=template-secret-value\n"
    lv = "[Service]\nExecStart=/bin/x\nEnvironment=API_TOKEN=live-secret-value\nEnvironment=PLAIN=ok\n"
    _write(tmp_path / "t", "s", t)
    _write(tmp_path / "l", "s", lv)
    rc = udc.main(["--template-dir", str(tmp_path / "t"), "--live-dir", str(tmp_path / "l"),
                   "--units", "s", "--json", "--home", "/h"])
    out = capsys.readouterr().out
    assert "template-secret-value" not in out and "live-secret-value" not in out
    data = json.loads(out)
    assert any(f["key"] == "Environment API_TOKEN" and f["live"] == "<redacted>" for f in data["findings"])
    assert rc == 1  # the secret DIFFERS (actionable), regardless of redaction


def test_exit_codes_live_only_is_informational_unless_strict(udc, tmp_path):
    _write(tmp_path / "t", "r", ROUTER)
    _write(tmp_path / "l", "r", ROUTER + "Nice=5\n")
    base = ["--template-dir", str(tmp_path / "t"), "--live-dir", str(tmp_path / "l"), "--units", "r"]
    assert udc.main(base) == 0
    assert udc.main(base + ["--strict"]) == 1


def test_not_installed_and_unreadable_template(udc, tmp_path):
    _write(tmp_path / "t", "r", ROUTER)
    (tmp_path / "l").mkdir()
    findings, problems = udc.check(["r"], tmp_path / "t", tmp_path / "l", "/h")
    assert [f.kind for f in findings] == ["not_installed"] and problems == []
    findings, problems = udc.check(["ghost"], tmp_path / "t", tmp_path / "l", "/h")
    assert findings == [] and problems and "no template" in problems[0]


def test_real_templates_parse_to_a_real_exec_start(udc):
    """Verify the instrument: if the parser read nothing it would report 'no drift'."""
    for name in udc.DEFAULT_UNITS:
        texts = udc.unit_texts(TEMPLATES, name)
        assert texts is not None, f"template for {name} vanished — update DEFAULT_UNITS"
        cfg = udc.parse_service_section(texts, "/home/x")
        model = udc.exec_start_model(cfg["ExecStart"])
        assert model.get("binary"), f"{name}: parsed an empty ExecStart"


def test_real_router_template_trips_when_mlock_is_dropped(udc, tmp_path):
    """The 2026-10-04 incident, reproduced: the live copy lacks what the template has."""
    tmpl = (TEMPLATES / "functiongemma-router.service").read_text(encoding="utf-8")
    stripped = "\n".join(
        ln for ln in tmpl.splitlines()
        if ln.strip() != "--mlock \\" and not ln.startswith("LimitMEMLOCK=")
    )
    _write(tmp_path / "l", "functiongemma-router", stripped)
    findings, _ = udc.check(
        ["functiongemma-router"], TEMPLATES, tmp_path / "l", "/home/x"
    )
    assert ("missing", "ExecStart --mlock") in _kinds(findings)
    assert ("missing", "LimitMEMLOCK") in _kinds(findings)
    # And the unmodified template compares clean against itself.
    _write(tmp_path / "same", "functiongemma-router", tmpl)
    findings, _ = udc.check(
        ["functiongemma-router"], TEMPLATES, tmp_path / "same", "/home/x"
    )
    assert findings == []
