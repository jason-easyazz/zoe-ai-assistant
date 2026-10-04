"""Logic tests for scripts/maintenance/unit_drift_check.py (installed unit vs template).

Born of the 2026-10-04 router incident: a merged template carried ``--mlock`` while the live
unit did not, and every test (which reads the template) stayed green. Includes negative
controls and a check that the REAL templates parse (a parser that reads nothing reports
"no drift" about everything).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "maintenance" / "unit_drift_check.py"
TEMPLATES = ROOT / "scripts" / "setup" / "systemd"
H = "/home/x"


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
MIN = "[Service]\nExecStart=/bin/x\n"


def _write(base: Path, name: str, main: str, dropins: dict[str, str] | None = None) -> None:
    base.mkdir(parents=True, exist_ok=True)
    (base / f"{name}.service").write_text(main, encoding="utf-8")
    for fname, text in (dropins or {}).items():
        (base / f"{name}.service.d").mkdir(exist_ok=True)
        (base / f"{name}.service.d" / fname).write_text(text, encoding="utf-8")


def _kinds(findings):
    return {(f.kind, f.key) for f in findings}


def _pair(udc, tmp_path, tmpl, live, name="u", **kw):
    """Write template + live (str, or (main, dropins)) and return the findings."""
    _write(tmp_path / "t", name, tmpl)
    main, drops = live if isinstance(live, tuple) else (live, None)
    _write(tmp_path / "l", name, main, drops)
    return udc.check([name], tmp_path / "t", tmp_path / "l", H, **kw)[0]


def test_identical_units_have_no_drift_and_home_and_sizes_normalise(udc, tmp_path):
    live = ROUTER.replace("%h", H).replace("1280M", "1342177280")  # literal path, bytes
    assert _pair(udc, tmp_path, ROUTER, live, "r") == []


def test_each_drift_kind_is_reported(udc, tmp_path):
    live = (ROUTER.replace("  --mlock \\\n", "").replace("LimitMEMLOCK=infinity\n", "")
            .replace("MemoryMax=1280M", "MemoryMax=1G") + "Nice=10\n")
    assert _kinds(_pair(udc, tmp_path, ROUTER, live, "r")) == {
        ("missing", "ExecStart --mlock"), ("missing", "LimitMEMLOCK"),
        ("differs", "MemoryMax"), ("live_only", "Nice"),
    }


def test_dropin_reset_semantics_match_systemd(udc, tmp_path):
    """An empty ExecStart= in a drop-in RESETS the list (else the model reads the wrong line)."""
    venv = "[Service]\nExecStart=\nExecStart=/venv/bin/python %h/app.py\n"
    main = "[Service]\nExecStart=/usr/bin/python3 %h/app.py\n"
    _write(tmp_path / "t", "k", main, {"60-venv.conf": venv})
    _write(tmp_path / "l", "k", main, {"60-venv.conf": venv})
    assert udc.check(["k"], tmp_path / "t", tmp_path / "l", H)[0] == []
    _write(tmp_path / "l2", "k", main)  # negative control: drop-in never installed
    assert ("differs", "ExecStart binary") in _kinds(udc.check(["k"], tmp_path / "t", tmp_path / "l2", H)[0])


LEAKY = (
    "[Service]\n"
    "ExecStart=/bin/srv --api-key=SK_X --hf-token HF_X\n"
    'ExecStartPre=/bin/curl -H "Authorization: Bearer BEARER_X" http://x\n'
    "Environment=DATABASE_URL=postgresql://u:PW_X@db/x ZOE_AUTH=AUTH_X API_TOKEN=TOK_X\n"
)
SENTINELS = ("SK_", "HF_", "BEARER_", "PW_", "AUTH_", "TOK_")


def _run(udc, tmp_path, capsys, *extra):
    _write(tmp_path / "t", "s", LEAKY.replace("_X", "_T"))
    _write(tmp_path / "l", "s", LEAKY.replace("_X", "_L"))
    rc = udc.main(["--template-dir", str(tmp_path / "t"), "--live-dir", str(tmp_path / "l"),
                   "--units", "s", "--home", "/h", *extra])
    return rc, capsys.readouterr()


@pytest.mark.parametrize("fmt", [[], ["--json"]])
def test_no_value_is_printed_by_default_for_any_directive(udc, tmp_path, capsys, fmt):
    """--flag=value, --flag value, ExecStartPre lines, URL passwords and arbitrarily named
    variables stay out of text AND --json (sha256 prefixes only)."""
    rc, cap = _run(udc, tmp_path, capsys, *fmt)
    assert rc == 1  # the values DIFFER (actionable) regardless of redaction
    assert not [s for s in SENTINELS if s in cap.out + cap.err] and "sha256:" in cap.out
    if fmt:  # the flag NAME is split from its value, so it is the key
        assert "ExecStart --api-key" in {f["key"] for f in json.loads(cap.out)["findings"]}


def test_show_values_prints_text_but_redacts_userinfo_and_secret_names(udc, tmp_path, capsys):
    _, cap = _run(udc, tmp_path, capsys, "--show-values")
    assert "PW_" not in cap.out and "TOK_" not in cap.out and "SK_" not in cap.out
    assert "BEARER_L" in cap.out  # opt-in really shows values
    a = udc._disp("same", False)
    assert a == udc._disp("same", False) and a != udc._disp("other", False)


def test_exit_codes_live_only_is_informational_unless_strict(udc, tmp_path):
    _write(tmp_path / "t", "r", ROUTER)
    _write(tmp_path / "l", "r", ROUTER + "Nice=5\n")
    base = ["--template-dir", str(tmp_path / "t"), "--live-dir", str(tmp_path / "l"), "--units", "r"]
    assert udc.main(base) == 0 and udc.main(base + ["--strict"]) == 1


def test_not_installed_and_missing_template(udc, tmp_path):
    _write(tmp_path / "t", "r", ROUTER)
    (tmp_path / "l").mkdir()
    findings, problems = udc.check(["r"], tmp_path / "t", tmp_path / "l", "/h")
    assert [f.kind for f in findings] == ["not_installed"] and problems == []
    findings, problems = udc.check(["ghost"], tmp_path / "t", tmp_path / "l", "/h")
    assert findings == [] and "no template" in problems[0]


def test_real_templates_parse_to_a_real_exec_start(udc):
    """Verify the instrument: a parser that read nothing would report 'no drift'."""
    for name in udc.DEFAULT_UNITS:
        texts = udc.unit_texts(TEMPLATES, name)
        assert texts is not None, f"template for {name} vanished — update DEFAULT_UNITS"
        model = udc.exec_start_model(udc.parse_service_section(texts, H)["ExecStart"])
        assert model.get("binary"), f"{name}: parsed an empty ExecStart"


def test_real_router_template_trips_when_mlock_is_dropped(udc, tmp_path):
    """The 2026-10-04 incident, reproduced."""
    tmpl = (TEMPLATES / "functiongemma-router.service").read_text(encoding="utf-8")
    stripped = "\n".join(ln for ln in tmpl.splitlines()
                         if ln.strip() != "--mlock \\" and not ln.startswith("LimitMEMLOCK="))
    _write(tmp_path / "l", "functiongemma-router", stripped)
    found = _kinds(udc.check(["functiongemma-router"], TEMPLATES, tmp_path / "l", H)[0])
    assert {("missing", "ExecStart --mlock"), ("missing", "LimitMEMLOCK")} <= found
    _write(tmp_path / "same", "functiongemma-router", tmpl)  # unmodified template compares clean
    assert udc.check(["functiongemma-router"], TEMPLATES, tmp_path / "same", H)[0] == []


def _main(udc, tmp_path):
    return udc.main(["--template-dir", str(tmp_path / "t"), "--live-dir", str(tmp_path / "l"), "--units", "u"])


def test_unreadable_input_exits_2_not_the_drift_code(udc, tmp_path, capsys):
    """chmod 000 / dangling symlink / non-UTF-8 used to traceback with rc=1 (= 'drift')."""
    _write(tmp_path / "t", "u", MIN)
    _write(tmp_path / "l", "u", MIN, {"50.conf": "[Service]\nNice=5\n"})
    drop = tmp_path / "l" / "u.service.d"
    (drop / "50.conf").chmod(0o000)
    if not os.access(drop / "50.conf", os.R_OK):  # as root chmod cannot deny, skip that case
        assert _main(udc, tmp_path) == 2 and "cannot read" in capsys.readouterr().err
    (drop / "50.conf").chmod(0o644)
    (drop / "50.conf").unlink()
    (drop / "dangling.conf").symlink_to(tmp_path / "nowhere.conf")
    assert _main(udc, tmp_path) == 2
    (drop / "dangling.conf").unlink()
    (drop / "bad.conf").write_bytes(b"[Service]\nNice=\xff\xfe\n")
    assert _main(udc, tmp_path) == 2
    (drop / "bad.conf").unlink()
    assert _main(udc, tmp_path) == 0  # control: once readable it is a normal verdict, not 2


def test_append_list_directives_accumulate_across_dropins(udc, tmp_path):
    """SuccessExitStatus is an append list: main '0 1' + drop-in '143' == '0 1 143'; After= too."""
    t = "[Unit]\nAfter=network.target llama-server.service\n[Service]\nExecStart=/bin/x\nSuccessExitStatus=0 1 143\n"
    low = "[Unit]\nAfter=network.target\n[Service]\nExecStart=/bin/x\nSuccessExitStatus=0 1\n"
    both = {"50.conf": "[Unit]\nAfter=llama-server.service\n[Service]\nSuccessExitStatus=143\n"}
    assert _pair(udc, tmp_path, t, (low, both)) == []
    assert {("missing", "SuccessExitStatus"), ("missing", "After")} <= _kinds(_pair(udc, tmp_path, t, low, "v"))  # control
    reset = {"50.conf": "[Service]\nSuccessExitStatus=\nSuccessExitStatus=143\n"}
    assert ("missing", "SuccessExitStatus") in _kinds(_pair(udc, tmp_path, t, (t, reset), "w"))  # 0 and 1 reset away


@pytest.mark.parametrize("a,b", [("5min", "300"), ("5s", "5"), ("1m 30s", "90"), ("2h", "7200"), ("500ms", "0.5")])
def test_time_spans_are_normalised(udc, tmp_path, a, b):
    unit = "[Service]\nExecStart=/bin/x\nTimeoutStartSec={0}\nRestartSec={0}\n"
    assert _pair(udc, tmp_path, unit.format(a), unit.format(b)) == []
    assert ("differs", "TimeoutStartSec") in _kinds(_pair(udc, tmp_path, unit.format(a), unit.format(7)))
