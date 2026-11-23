"""No test under ``tests/`` or the zoe-data suite can post to the live Home Assistant bridge.

What this pins, exactly:
(a) a collected test, in any lane, cannot reach the live bridge FROM THE TEST PROCESS: both test configurations overwrite the
    bridge address with a dead local port before anything that reads it is imported;
(b) a script under tests/ that drives the RUNNING app is live by definition (a test process cannot repoint a running app): every
    one of them refuses to start without ``--live``.
Stated gap: collected tests that post to the running app when one is up (the self-hosted integration/e2e lane on the box, and
tests/unit/test_tool_calling.py, tests/unit/test_ui_functionality.py) still do so by design; none of them may send a device command.

Negative controls run: (1) the line in ``tests/conftest.py`` changed to ``setdefault`` -> the placement and inherited-address
tests fail; (2) the ``--live`` guard removed from one script -> its refusal test fails; (3) the device phrase put back in
``test_tool_calling.py`` -> the last test fails.
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


LIVE_ADDRESS = ("localhost:8000", "127.0.0.1:8000", "localhost:8007", "127.0.0.1:8007")


def _live_scripts():
    """Every file under tests/ that can be started by hand (has a ``__main__`` block) and names the running app or the bridge."""
    out = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        text = path.read_text(errors="replace")
        if 'if __name__ == "__main__":' in text and any(a in text for a in LIVE_ADDRESS) and path != Path(__file__).resolve():
            out.append(path)
    return out


def test_there_are_live_scripts_to_check():
    assert len(_live_scripts()) >= 10


@pytest.mark.parametrize("script", _live_scripts(), ids=lambda p: str(p.relative_to(ROOT)))
def test_a_script_that_drives_the_running_app_refuses_without_live(script):
    """A test process cannot repoint an app that is already running, so these are live by definition: opt-in only."""
    text = script.read_text(errors="replace")
    main_block = text[text.index('if __name__ == "__main__":'):]
    guard = main_block.find('if "--live" not in _sys.argv[1:]:')
    assert guard != -1, "no --live guard"
    first_call = min((main_block.find(c) for c in ("main()", "asyncio.run(", "pytest.main(", "run_all_tests(", "_chat_events(")
                      if main_block.find(c) != -1), default=len(main_block))
    assert guard < first_call, "the guard comes after the script has started"
    out = subprocess.run([sys.executable, str(script)], capture_output=True, text=True, timeout=120, cwd=str(ROOT))
    assert out.returncode == 2 and "--live" in out.stdout, (out.returncode, out.stdout[-300:], out.stderr[-300:])


def test_the_collected_chat_test_sends_no_device_command():
    text = (ROOT / "tests" / "unit" / "test_tool_calling.py").read_text()
    params = text[text.index("@pytest.mark.parametrize("):text.index("async def test_chat_tool_calling_returns_structured_response")]
    strings = [n.value.lower() for n in ast.walk(ast.parse(params + "def _(): pass")) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert strings and not any(w in s for s in strings for w in ("turn on", "turn off", "unlock", "open the", "light"))
