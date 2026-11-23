"""No test under ``tests/`` or the zoe-data suite can post to the live Home Assistant bridge.

What this pins, exactly:
(a) a collected test, in any lane, cannot reach the live bridge FROM THE TEST PROCESS: both test configurations overwrite the
    bridge address with a dead local port before anything that reads it is imported;
(b) a file under tests/ that can be run by hand and imports a network module is live by definition (a test process cannot
    repoint a running app): its ``__main__`` block starts with a ``--live`` refusal and nothing but imports, definitions and
    plain set-up runs before it. Checked by reading the syntax tree; no script is executed by this test.
Stated gap: collected tests that post to the running app when one is up (the self-hosted integration/e2e lane on the box, and
tests/unit/test_tool_calling.py, tests/unit/test_ui_functionality.py) still do so by design; none of them may send a device command.

Negative controls run: (1) the line in ``tests/conftest.py`` changed to ``setdefault`` -> the placement and inherited-address
tests fail; (2) the device phrase put back in ``test_tool_calling.py`` -> the last test fails; (3) the planted holes in
``test_the_checker_itself_catches_each_way_round_it`` (single-quoted main, URL from the environment, a call at import, a guard that
is not first or does not refuse); (4) the guard removed from one real script -> its test fails.
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


NETWORK = {"requests", "httpx", "aiohttp", "urllib", "urllib3", "websockets", "websocket", "socket", "http"}
# Hand-runnable files under tests/ that import no network module at all. Exact: a new one must be added here on purpose.
OFFLINE_SCRIPTS = {"tests/intent_system/test_classification.py", "tests/unit/test_architecture.py"}
# Calls that may run when a guarded script is merely imported or started: path and logging set-up, reading the environment.
PURE_CALLS = ("sys.path.insert", "sys.path.append", "os.path.", "os.environ.get", "os.getenv", "logging.", "Path", "pytest.")


def _is_main(node):
    return (isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and isinstance(node.test.left, ast.Name)
            and node.test.left.id == "__name__"
            and any(isinstance(c, ast.Constant) and c.value == "__main__" for c in node.test.comparators))


def _imports(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            out.add((node.module or "").split(".")[0])
    return out


def _pure(call):
    name = ast.unparse(call.func)
    while name.count("(") and name.endswith((".rstrip", ".resolve", ".strip", ".lower")):  # a method on an allowed call's result
        name = ast.unparse(call.func.value.func) if isinstance(call.func.value, ast.Call) else name.rsplit(".", 1)[0]
        break
    return name.startswith(PURE_CALLS) or name in ("os.environ.get",)


def _import_time_problems(body, where="module"):
    """Statements that run on import/start, outside any function: only imports, definitions and assignments built from PURE_CALLS."""
    problems = []
    for node in body:
        if _is_main(node) or isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            checked = node.decorator_list  # the body runs only when called; the decorators run now
        elif isinstance(node, ast.ClassDef):
            problems += _import_time_problems(node.body, f"class {node.name}")
            checked = node.decorator_list
        elif isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr, ast.Pass)):
            checked = [node]
        else:
            problems.append(f"{where} line {node.lineno}: a {type(node).__name__} statement runs at import")
            continue
        for part in checked:
            for call in (n for n in ast.walk(part) if isinstance(n, ast.Call)):
                inner = call.func.value if isinstance(call.func, ast.Attribute) and isinstance(call.func.value, ast.Call) else None
                if not (_pure(call) or (inner is not None and _pure(inner))):
                    problems.append(f"{where} line {call.lineno}: calls {ast.unparse(call.func)} at import")
    return problems


def _guard_problems(tree):
    """The ``__main__`` block must START with the guard: import sys as _sys; if "--live" not in _sys.argv[1:]: ...; _sys.exit(2)."""
    mains = [n for n in tree.body if _is_main(n)]
    if len(mains) != 1 or mains[0].orelse:
        return ["expected exactly one plain __main__ block"]
    body = mains[0].body
    if len(body) < 2 or ast.unparse(body[0]) != "import sys as _sys" or not isinstance(body[1], ast.If):
        return ["the __main__ block does not start with the --live guard"]
    test, inner = ast.unparse(body[1].test), [ast.unparse(s) for s in body[1].body]
    if test != "'--live' not in _sys.argv[1:]" or not inner or inner[-1] != "_sys.exit(2)" or body[1].orelse:
        return ["the --live guard does not refuse with exit 2"]
    if any(isinstance(n, ast.Call) and not ast.unparse(n.func) in ("print", "_sys.exit") for s in body[1].body for n in ast.walk(s)):
        return ["the --live guard does more than print and exit"]
    return []


def script_problems(source, rel):
    """Why this file under tests/ could contact the running app without an explicit --live. Reads the source; never runs it."""
    tree = ast.parse(source)
    if not any(_is_main(n) for n in tree.body):
        return []
    if not (_imports(tree) & NETWORK) and rel in OFFLINE_SCRIPTS:
        return []
    return _guard_problems(tree) + _import_time_problems(tree.body)


def _hand_runnable():
    out = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        tree = ast.parse(path.read_text(errors="replace"))
        if any(_is_main(n) for n in tree.body):
            out.append(path)
    return out


def test_the_hand_runnable_files_are_found_and_the_offline_list_is_exact():
    found = {str(p.relative_to(ROOT)) for p in _hand_runnable()}
    assert len(found) >= 13 and OFFLINE_SCRIPTS <= found
    for rel in OFFLINE_SCRIPTS:
        assert not (_imports(ast.parse((ROOT / rel).read_text())) & NETWORK), f"{rel} imports a network module: guard it"


@pytest.mark.parametrize("script", _hand_runnable(), ids=lambda p: str(p.relative_to(ROOT)))
def test_a_hand_runnable_file_cannot_contact_the_running_app_without_live(script):
    """Structural, by reading the source: the file is never executed here, so this test cannot itself cause a live action.

    Found by syntax tree (either quoting of ``__main__``), whatever the app address is spelt as or read from. A guarded file's
    ``__main__`` block starts with the guard, and nothing but imports, definitions and plain set-up runs before it.
    """
    assert script_problems(script.read_text(errors="replace"), str(script.relative_to(ROOT))) == []


GOOD = (
    "import httpx, os\n"
    "API = os.environ.get('ZOE_API_URL', '').rstrip('/')\n"
    "def main():\n    httpx.get(API)\n"
    "if __name__ == '__main__':\n"
    "    import sys as _sys\n"
    "    if '--live' not in _sys.argv[1:]:\n        print('x')\n        _sys.exit(2)\n"
    "    main()\n"
)


def test_the_checker_itself_catches_each_way_round_it():
    """The controls, kept as tests: each planted hole must be reported."""
    assert script_problems(GOOD, "tests/x.py") == []  # single-quoted main, URL from the environment: found and accepted
    no_guard = GOOD.replace("    import sys as _sys\n    if '--live' not in _sys.argv[1:]:\n        print('x')\n        _sys.exit(2)\n", "")
    assert script_problems(no_guard, "tests/x.py")  # single-quoted main with a URL from the environment, unguarded
    assert script_problems(GOOD.replace("def main():", "httpx.get(API)\ndef main():"), "tests/x.py")  # a call at import
    assert script_problems(GOOD.replace("def main():", "class C:\n    r = httpx.get(API)\ndef main():"), "tests/x.py")  # in a class body
    assert script_problems(GOOD.replace("def main():", "try:\n    pass\nexcept Exception:\n    pass\ndef main():"), "tests/x.py")
    assert script_problems(GOOD.replace("    import sys as _sys\n", "    main()\n    import sys as _sys\n"), "tests/x.py")  # guard not first
    assert script_problems(GOOD.replace("_sys.exit(2)", "pass"), "tests/x.py")  # a guard that does not refuse
    assert script_problems("import os\nif __name__ == '__main__':\n    os.system('x')\n", "tests/new_script.py")  # not on the offline list


def test_the_collected_chat_test_sends_no_device_command():
    text = (ROOT / "tests" / "unit" / "test_tool_calling.py").read_text()
    params = text[text.index("@pytest.mark.parametrize("):text.index("async def test_chat_tool_calling_returns_structured_response")]
    strings = [n.value.lower() for n in ast.walk(ast.parse(params + "def _(): pass")) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert strings and not any(w in s for s in strings for w in ("turn on", "turn off", "unlock", "open the", "light"))
