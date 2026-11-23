"""No test under ``tests/`` or the zoe-data suite can post to the live Home Assistant bridge.

A test whose fake missed the sender once posted a real device command to the house. Both test configurations now overwrite the
bridge address with a dead local port before anything that reads it is imported.

Negative controls run: (1) the line in ``tests/conftest.py`` changed to ``setdefault`` -> the placement and inherited-address
tests fail; (2) the ``--live`` guard removed from the integration script -> the refusal test fails.
"""
import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
DEAD = "http://127.0.0.1:9"
CONFTESTS = [ROOT / "tests" / "conftest.py", ROOT / "services" / "zoe-data" / "tests" / "conftest.py"]
STDLIB_OK = set(sys.stdlib_module_names) | {"pytest", "_pytest", "pytest_asyncio"}


def test_this_session_points_at_the_dead_port():
    assert os.environ["ZOE_HA_BRIDGE_URL"] == DEAD


@pytest.mark.parametrize("conftest", CONFTESTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_the_pin_is_unconditional_and_comes_before_any_project_import(conftest):
    tree = ast.parse(conftest.read_text())
    pin_line = None
    for node in tree.body:  # top level only: a pin inside an ``if`` or a function is conditional
        if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == "os.environ['ZOE_HA_BRIDGE_URL']":
            assert ast.literal_eval(node.value) == DEAD
            pin_line = node.lineno
    assert pin_line, "no top-level, unconditional ZOE_HA_BRIDGE_URL assignment"
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            names = [node.module or ""]
        for name in names:
            if name.split(".")[0] not in STDLIB_OK:
                assert node.lineno > pin_line, f"{name} is imported at line {node.lineno}, before the pin at {pin_line}"


@pytest.mark.parametrize("conftest", CONFTESTS, ids=lambda p: str(p.relative_to(ROOT)))
def test_an_inherited_live_address_is_overwritten(conftest):
    """Run only the conftest's top-level pin statements with a live-looking address inherited; it must not survive."""
    tree = ast.parse(conftest.read_text())
    pins = [n for n in tree.body if isinstance(n, ast.Expr) or isinstance(n, ast.Assign)]
    pins = [n for n in pins if "ZOE_HA_BRIDGE_URL" in ast.unparse(n)]
    assert pins
    code = "import os\n" + "\n".join(ast.unparse(n) for n in pins) + "\nprint(os.environ['ZOE_HA_BRIDGE_URL'])"
    env = dict(os.environ, ZOE_HA_BRIDGE_URL="http://127.0.0.1:8007")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=30)
    assert out.stdout.strip() == DEAD, out.stderr


def test_the_live_capability_script_refuses_without_an_explicit_opt_in():
    script = ROOT / "tests" / "integration" / "test_all_capabilities.py"
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=60)
    assert out.returncode == 2 and "--live" in out.stdout
    assert "localhost:8007/" not in script.read_text().replace('"http://localhost:8007").rstrip', "")
