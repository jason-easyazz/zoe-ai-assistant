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
tests fail; (2) the device phrase put back in ``test_tool_calling.py`` -> the last test fails; (3) every planted hole in ``PLANTED`` below
(``test_the_checker_reports_each_planted_hole``); (4) the guard removed from one real script, and a call planted at import in another -> their tests fail.
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
# Files a human has read whose only mention of "__main__" is not an entry point (and that import no network module). Exact.
REVIEWED_MENTIONS = {"tests/unit/test_palace_client_guard.py"}  # sys.modules["__main__"] inside a test body, for a pickle
# The ONLY calls that may run when a guarded file is imported or started: path and logging set-up, reading the environment.
PURE_CALLS = ("sys.path.insert", "sys.path.append", "os.path.", "os.environ.get", "os.getenv", "logging.", "Path", "pytest.")
PURE_METHODS = {"rstrip", "strip", "lower", "resolve"}  # on the RESULT of another call, which is checked in its own right
# What the pure names must be bound to, so that ``import httpx as logging`` cannot borrow an allowed name.
PURE_ROOTS = {"sys": "import sys", "os": "import os", "logging": "import logging", "pytest": "import pytest",
              "Path": "from pathlib import Path"}


def _operands(compare):
    return [compare.left, *compare.comparators]


def _is_main_test(test):
    """``__name__ == "__main__"`` with the two operands in either order (and nothing else)."""
    if not (isinstance(test, ast.Compare) and len(test.ops) == 1 and isinstance(test.ops[0], ast.Eq)):
        return False
    a, b = _operands(test)
    name = lambda n: isinstance(n, ast.Name) and n.id == "__name__"
    main = lambda n: isinstance(n, ast.Constant) and n.value == "__main__"
    return (name(a) and main(b)) or (main(a) and name(b))


def _main_ifs(tree):
    return [n for n in tree.body if isinstance(n, ast.If) and _is_main_test(n.test)]


def _unrecognised_main(tree):
    """FAIL CLOSED: any mention of ``"__main__"`` that is not the test of a top-level main ``if`` (a nested one, a ``match``, a
    comparison this checker does not know, ``import __main__``) is flagged for a human rather than assumed harmless."""
    known = {id(n) for m in _main_ifs(tree) for n in ast.walk(m.test)}
    out = []
    for node in ast.walk(tree):
        if id(node) in known:
            continue
        if (isinstance(node, ast.Constant) and node.value == "__main__") or (isinstance(node, ast.Name) and node.id == "__main__") \
                or (isinstance(node, (ast.Import, ast.ImportFrom)) and "__main__" in ast.unparse(node)):
            out.append(f"line {node.lineno}: mentions __main__ in a form this checker does not recognise")
    return out


def _imports(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            out.add((node.module or "").split(".")[0])
    return out


def _pure(call):
    func = call.func
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Call):
        return func.attr in PURE_METHODS
    return ast.unparse(func).startswith(PURE_CALLS)


def _rebinding_problems(tree):
    """An allowed name must mean what it says everywhere in the file."""
    out = []
    for node in ast.walk(tree):
        bound = []
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for a in node.names:
                local = a.asname or a.name.split(".")[0]
                if local in PURE_ROOTS:
                    one = ast.unparse(ast.Import([a]) if isinstance(node, ast.Import) else ast.ImportFrom(node.module, [a], node.level))
                    if one != PURE_ROOTS[local] and not (isinstance(node, ast.Import) and a.asname is None and a.name.split(".")[0] == local):
                        out.append(f"line {node.lineno}: {local} is bound by `{one}`")
        elif isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound = [node.id]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            bound = [node.name]
        elif isinstance(node, ast.arg):
            bound = [node.arg]
        out += [f"line {node.lineno}: rebinds {b}" for b in bound if b in PURE_ROOTS]
    return out


class _ImportTime(ast.NodeVisitor):
    """Visits EVERYTHING that Python evaluates when the file is imported or started, by construction: the whole tree, pruning
    only the BODIES of functions and lambdas (their decorators, defaults, annotations and return annotations are still visited)
    and the main ``if`` (whose body the guard check owns). Any call met that is not on the pure list is a problem."""

    ALLOWED_STATEMENTS = (ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Assign, ast.AnnAssign)

    def __init__(self, main_ifs):
        self.main_ifs, self.problems = {id(m) for m in main_ifs}, []

    def check_body(self, body, where):
        for node in body:
            if id(node) in self.main_ifs:
                continue
            docstring = isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)
            set_up = isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)  # allowed only if the call is pure: visit() decides
            if not (isinstance(node, self.ALLOWED_STATEMENTS) or docstring or set_up or isinstance(node, ast.Pass)):
                self.problems.append(f"{where} line {node.lineno}: a {type(node).__name__} statement runs at import")
                continue
            self.visit(node)

    def visit_Module(self, node):
        self.check_body(node.body, "module")

    def visit_ClassDef(self, node):
        for part in (*node.decorator_list, *node.bases, *node.keywords):
            self.visit(part)
        self.check_body(node.body, f"class {node.name}")

    def _function(self, node):
        for part in (*getattr(node, "decorator_list", ()), node.args, *filter(None, [getattr(node, "returns", None)])):
            self.visit(part)  # ast.arguments carries defaults, kw_defaults and every annotation; the body is NOT visited

    visit_FunctionDef = visit_AsyncFunctionDef = visit_Lambda = _function

    def visit_Call(self, node):
        if not _pure(node):
            self.problems.append(f"line {node.lineno}: calls {ast.unparse(node.func)} at import")
        self.generic_visit(node)

    def visit_Await(self, node):
        self.problems.append(f"line {node.lineno}: awaits at import")

    visit_Yield = visit_YieldFrom = visit_Await


def _guard_problems(main_if):
    """The main block must START with the guard: import sys as _sys; if "--live" not in _sys.argv[1:]: print(...); _sys.exit(2)."""
    body = main_if.body
    if main_if.orelse:
        return ["the main block has an else branch"]
    if len(body) < 2 or ast.unparse(body[0]) != "import sys as _sys" or not isinstance(body[1], ast.If):
        return ["the main block does not start with the --live guard"]
    test, inner = ast.unparse(body[1].test), [ast.unparse(s) for s in body[1].body]
    if test != "'--live' not in _sys.argv[1:]" or not inner or inner[-1] != "_sys.exit(2)" or body[1].orelse:
        return ["the --live guard does not refuse with exit 2"]
    if any(isinstance(n, ast.Call) and ast.unparse(n.func) not in ("print", "_sys.exit") for s in body[1].body for n in ast.walk(s)):
        return ["the --live guard does more than print and exit"]
    return []


def is_hand_runnable(tree):
    return bool(_main_ifs(tree) or _unrecognised_main(tree))


def script_problems(source, rel):
    """Why this file under tests/ could contact the running app without an explicit --live. Reads the source; never runs it."""
    tree = ast.parse(source)
    if not is_hand_runnable(tree):
        return []
    problems = _unrecognised_main(tree)
    mains = _main_ifs(tree)
    if len(mains) > 1:
        problems.append("more than one main block")
    offline = not (_imports(tree) & NETWORK)
    if offline and not mains and rel in REVIEWED_MENTIONS:
        return []
    if offline and rel in OFFLINE_SCRIPTS:
        return problems
    walker = _ImportTime(mains)
    walker.visit(tree)
    return problems + [p for m in mains for p in _guard_problems(m)] + walker.problems + _rebinding_problems(tree)


def _hand_runnable():
    out = []
    for path in sorted((ROOT / "tests").rglob("*.py")):
        if path.resolve() == Path(__file__).resolve():
            continue  # this checker names the string it looks for
        if is_hand_runnable(ast.parse(path.read_text(errors="replace"))):
            out.append(path)
    return out


def test_the_hand_runnable_files_are_found_and_the_offline_list_is_exact():
    found = {str(p.relative_to(ROOT)) for p in _hand_runnable()}
    assert len(found) >= 14 and OFFLINE_SCRIPTS <= found
    assert REVIEWED_MENTIONS <= found
    for rel in OFFLINE_SCRIPTS | REVIEWED_MENTIONS:
        assert not (_imports(ast.parse((ROOT / rel).read_text())) & NETWORK), f"{rel} imports a network module: guard it"


@pytest.mark.parametrize("script", _hand_runnable(), ids=lambda p: str(p.relative_to(ROOT)))
def test_a_hand_runnable_file_cannot_contact_the_running_app_without_live(script):
    """Structural, by reading the source: the file is never executed here, so this test cannot itself cause a live action.

    Found by syntax tree: a top-level ``if`` comparing ``__name__`` with ``"__main__"`` in either order, and ANY other mention of
    ``"__main__"`` fails closed. A guarded file's main block starts with the guard, and the import-time walk visits everything
    Python evaluates before it (see ``_ImportTime``).
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
GUARD = "    import sys as _sys\n    if '--live' not in _sys.argv[1:]:\n        print('x')\n        _sys.exit(2)\n"
PLANTED = {
    "no guard (single-quoted main, URL from the environment)": GOOD.replace(GUARD, ""),
    "no guard, operands reversed": GOOD.replace(GUARD, "").replace("__name__ == '__main__'", "'__main__' == __name__"),
    "a match on __name__": "import httpx\nmatch __name__:\n    case '__main__':\n        httpx.get('x')\n",
    "a main if nested in a try": "import httpx\ntry:\n    if __name__ == '__main__':\n        httpx.get('x')\nfinally:\n    pass\n",
    "a comparison through a variable": "import httpx\nM = '__main__'\nif __name__ == M:\n    httpx.get('x')\n",
    "a call at import": GOOD.replace("def main():", "httpx.get(API)\ndef main():"),
    "a call in a default": GOOD.replace("def main():", "def main(r=httpx.post(API)):"),
    "a call in a keyword-only default": GOOD.replace("def main():", "def main(*, r=httpx.post(API)):"),
    "a call in an annotation": GOOD.replace("def main():", "def main(r: httpx.post(API) = None):"),
    "a call in a return annotation": GOOD.replace("def main():", "def main() -> httpx.post(API):"),
    "a decorator call": GOOD.replace("def main():", "@httpx.get(API)\ndef main():"),
    "a class-base call": GOOD.replace("def main():", "class C(httpx.get(API)):\n    pass\ndef main():"),
    "a class keyword call": GOOD.replace("def main():", "class C(metaclass=httpx.get(API)):\n    pass\ndef main():"),
    "a call in a class body": GOOD.replace("def main():", "class C:\n    r = httpx.get(API)\ndef main():"),
    "a call in a lambda default": GOOD.replace("def main():", "f = lambda r=httpx.get(API): r\ndef main():"),
    "a call in a comprehension": GOOD.replace("def main():", "X = [httpx.get(u) for u in (API,)]\ndef main():"),
    "a try block at import": GOOD.replace("def main():", "try:\n    pass\nexcept Exception:\n    pass\ndef main():"),
    "a for loop at import": GOOD.replace("def main():", "for u in (API,):\n    pass\ndef main():"),
    "a with block at import": GOOD.replace("def main():", "with open(API):\n    pass\ndef main():"),
    "an allowed name borrowed by an import": GOOD.replace("import httpx, os\n", "import httpx, os\nimport httpx as logging\nlogging.get('x')\n"),
    "an allowed name rebound": GOOD.replace("def main():", "Path = httpx.get\ndef main():"),
    "the guard is not first": GOOD.replace(GUARD, "    main()\n" + GUARD),
    "the guard does not refuse": GOOD.replace("_sys.exit(2)", "pass"),
    "an else branch on the main block": GOOD + "else:\n    httpx.get(API)\n",
    "two main blocks": GOOD + GOOD[GOOD.index("if __name__"):],
    "a new offline-looking script not on the list": "import os\nif __name__ == '__main__':\n    os.system('x')\n",
}


def test_the_reference_script_is_accepted():
    """Single-quoted main, URL read from the environment, a pure call with a method on its result: found and accepted."""
    assert is_hand_runnable(ast.parse(GOOD)) and script_problems(GOOD, "tests/x.py") == []
    assert script_problems(GOOD.replace("__name__ == '__main__'", '"__main__" == __name__'), "tests/x.py") == []


@pytest.mark.parametrize("why", sorted(PLANTED))
def test_the_checker_reports_each_planted_hole(why):
    """The controls, kept as tests: every way round the check that review found or that follows from the same construction."""
    assert is_hand_runnable(ast.parse(PLANTED[why])), "not even discovered"
    assert script_problems(PLANTED[why], "tests/new_script.py"), "passed as safe"


def test_the_collected_chat_test_sends_no_device_command():
    text = (ROOT / "tests" / "unit" / "test_tool_calling.py").read_text()
    params = text[text.index("@pytest.mark.parametrize("):text.index("async def test_chat_tool_calling_returns_structured_response")]
    strings = [n.value.lower() for n in ast.walk(ast.parse(params + "def _(): pass")) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert strings and not any(w in s for s in strings for w in ("turn on", "turn off", "unlock", "open the", "light"))
