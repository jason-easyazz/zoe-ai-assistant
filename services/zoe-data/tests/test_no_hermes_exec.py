"""Nothing in this repo may launch `hermes`, or set HERMES_YOLO_MODE, or run an agent with auto-approve.

Deep dive S0-7: a guest-admitting ``POST /api/chat/task`` (and the chat / voice / A2A escalations) fed
``background_runner.enqueue_background_task``, which ran ``hermes --accept-hooks`` with ``HERMES_YOLO_MODE=1``
as the ``zoe`` user - and ``zoe`` has NOPASSWD root. It was inert only because ``~/.local/bin/hermes`` is a
dangling symlink. The path was DELETED (not gated); these tests keep it deleted:

* a repo-wide AST/text scan of every tracked prod source file (tests and docs excluded) for any way to exec
  ``hermes`` (a subprocess / exec / spawn call whose argv names it, ``hermes_bin``, ``shutil.which("hermes")``,
  a unit or script that runs it, a ``systemctl`` start of ``hermes-agent``), any ``HERMES_YOLO_MODE`` or
  ``--accept-hooks`` string, and any ``enqueue_background_task`` reference;
* NEGATIVE CONTROLS: each detector is fed a synthetic source that must be caught, so the scan cannot be vacuous;
* behavioural: an UNAUTHENTICATED (guest) POST to ``/api/chat/task`` and ``/api/agent/tasks`` reaches no executor,
  and a guest-admitting route in a synthetic app IS flagged by the same probe (so the probe can fail).
"""
from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]          # services/zoe-data
REPO = ROOT.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.ci_safe

_SKIP_PREFIXES = ("docs/", "services/zoe-ui/dist/lib/", "services/zoe-ui/node_modules/")
_SKIP_PARTS = {"tests", "test"}      # test code may NAME the strings it forbids
_EXEC_CALLS = {
    "run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput",
    "create_subprocess_exec", "create_subprocess_shell", "run_to_completion", "_spawn_cli",
    "execv", "execve", "execvp", "execvpe", "execl", "execle", "execlp", "execlpe",
    "spawnv", "spawnve", "spawnvp", "spawnl", "system", "popen", "run_in_executor",
}
_SHELL_CALLS = {"system", "popen", "getoutput", "getstatusoutput", "create_subprocess_shell"}
_FORBIDDEN_NAMES = {"hermes_bin", "enqueue_background_task", "_run_hermes_background_task"}
_FORBIDDEN_STRINGS = ("HERMES_YOLO_MODE", "--accept-hooks")
_HERMES_ARGV = re.compile(r"(^|/)hermes$")  # applied to argv[0]
# Text files that run things: a shell/yaml/unit line that executes `hermes`.
_TEXT_EXEC = re.compile(
    r"(^|[\s\"'=;&|(/])hermes(\s+(-p|-z|chat|kanban|gateway|--accept-hooks|--yolo)\b)"
    r"|ExecStart[^\n]*hermes|systemctl[^\n]*(start|restart|enable)[^\n]*hermes",
    re.MULTILINE,
)


def _tracked(*suffixes: str) -> list[str]:
    out = subprocess.run(["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True, check=True).stdout.splitlines()
    return [
        f for f in out
        if f.endswith(suffixes)
        and not f.startswith(_SKIP_PREFIXES)
        and not (set(Path(f).parts) & _SKIP_PARTS)
        and not Path(f).name.startswith("test_")
    ]


def _docstring_nodes(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(n, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant):
                ids.add(id(body[0].value))
    return ids


def _call_name(func: ast.expr) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def python_findings(source: str) -> list[str]:
    """Every way this module could launch hermes / set YOLO, as short human-readable findings."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    doc = _docstring_nodes(tree)
    found: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id in _FORBIDDEN_NAMES:
            found.append(f"line {n.lineno}: name {n.id}")
        elif isinstance(n, ast.Attribute) and n.attr in _FORBIDDEN_NAMES:
            found.append(f"line {n.lineno}: attribute {n.attr}")
        elif isinstance(n, (ast.ImportFrom, ast.Import)):
            for a in n.names:
                if a.name in _FORBIDDEN_NAMES:
                    found.append(f"line {n.lineno}: import {a.name}")
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in doc:
            for bad in _FORBIDDEN_STRINGS:
                if bad in n.value:
                    found.append(f"line {n.lineno}: string {bad!r}")
        if isinstance(n, ast.Call):
            name = _call_name(n.func)
            # shutil.which("hermes") / which("hermes")
            if name == "which" and any(isinstance(a, ast.Constant) and a.value == "hermes" for a in n.args):
                found.append(f"line {n.lineno}: which('hermes')")
            if name in _EXEC_CALLS:
                first = n.args[0] if n.args else None
                if isinstance(first, (ast.List, ast.Tuple)) and first.elts:
                    first = first.elts[0]
                # the program being run: argv[0] (a pgrep/grep that merely MENTIONS hermes is not an exec)
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    prog = first.value.strip()
                    shell = name in _SHELL_CALLS
                    if _HERMES_ARGV.search(prog.split()[0] if prog.split() else "") or (
                        shell and re.search(r"(^|[\s;&|/(])hermes\s+(-|kanban|chat|gateway)", prog)
                    ):
                        found.append(f"line {n.lineno}: {name}() runs hermes ({prog[:40]!r})")
                for sub in ast.walk(n):
                    if isinstance(sub, ast.Call) and _call_name(sub.func) == "hermes_bin":
                        found.append(f"line {n.lineno}: {name}() runs hermes_bin()")
                    if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and "hermes-agent" in sub.value and any(
                        isinstance(x, ast.Constant) and x.value in ("start", "restart", "enable") for x in ast.walk(n)
                    ):
                        found.append(f"line {n.lineno}: {name}() starts hermes-agent")
    return found


# ---- negative controls: every detector must be able to fail --------------------------------------------


@pytest.mark.parametrize(
    "src",
    [
        'import subprocess\nsubprocess.run(["hermes", "-p", "x", "--accept-hooks", "-z", "p"])\n',
        'import subprocess\nsubprocess.Popen(["/home/zoe/.local/bin/hermes", "kanban", "list"])\n',
        'from hermes_http import hermes_bin\n',
        'cmd = [hermes_bin(), "-z", p]\nawait run_to_completion(cmd)\n',
        'env.setdefault("HERMES_YOLO_MODE", "1")\n',
        'ARGS = ["--accept-hooks"]\n',
        'import shutil\np = shutil.which("hermes")\n',
        'import os\nos.system("hermes -z go")\n',
        'import subprocess\nsubprocess.run(["systemctl", "--user", "restart", "hermes-agent.service"])\n',
        'from background_runner import enqueue_background_task\n',
        'await asyncio.create_subprocess_exec("hermes", "chat")\n',
        'import os\nos.popen("cd /tmp && hermes -z go")\n',
    ],
)
def test_control_python_detector_catches_each_way_to_launch_hermes(src):
    assert python_findings(src), f"detector missed: {src!r}"


def test_control_python_detector_ignores_docstrings_prose_and_unrelated_calls():
    src = (
        '"""Mentions HERMES_YOLO_MODE and hermes_bin in prose only."""\n'
        'import subprocess\n'
        'def f():\n'
        '    """--accept-hooks used to be passed here."""\n'
        '    subprocess.run(["git", "status"])\n'
        '    subprocess.run(["pgrep", "-f", "hermes .*work kanban task"])\n'
        '    return "hermes is retired"\n'
    )
    assert python_findings(src) == []


@pytest.mark.parametrize(
    "text",
    [
        "ExecStart=%h/.local/bin/hermes gateway run --replace\n",
        "  hermes -p zoe-coder --accept-hooks -z \"$PROMPT\"\n",
        "run: ~/.local/bin/hermes kanban list\n",
        "systemctl --user restart hermes-agent.service\n",
    ],
)
def test_control_text_detector_catches_scripts_and_units(text):
    assert _TEXT_EXEC.search(text), f"detector missed: {text!r}"


def test_control_text_detector_ignores_prose():
    for text in ("# Hermes retired 2026-07-22\n", "echo 'hermes is gone'\n", "name: hermes-agent-docs\n"):
        assert not _TEXT_EXEC.search(text), text


def test_tracked_enumeration_is_not_empty():
    py = _tracked(".py")
    assert len(py) > 300 and "services/zoe-data/background_runner.py" in py
    assert any(f.endswith(".service") for f in _tracked(".service"))
    assert not any("/tests/" in f for f in py)


# ---- the repo-wide scans ---------------------------------------------------------------------------------


def test_no_python_module_can_launch_hermes_or_set_yolo():
    bad = {}
    for rel in _tracked(".py"):
        hits = python_findings((REPO / rel).read_text(encoding="utf-8", errors="ignore"))
        if hits:
            bad[rel] = hits[:3]
    assert not bad, f"code that can exec hermes / set HERMES_YOLO_MODE / pass --accept-hooks: {bad}"


def test_no_script_unit_or_workflow_runs_hermes():
    bad = {}
    for rel in _tracked(".sh", ".service", ".timer", ".yml", ".yaml", ".ts", ".js", ".conf"):
        text = (REPO / rel).read_text(encoding="utf-8", errors="ignore")
        m = _TEXT_EXEC.search(text)
        if m and not text.lstrip().startswith("#!/dev/null"):
            line = text[: m.start()].count("\n") + 1
            bad[rel] = f"line {line}: {m.group(0).strip()[:60]}"
    assert not bad, f"scripts/units that run hermes: {bad}"


def test_background_runner_has_no_executor_left():
    import background_runner as br

    public = {n for n in dir(br) if not n.startswith("__")}
    assert not ({"enqueue_background_task", "_run_task", "_run_hermes_background_task", "run_to_completion"} & public)
    assert {"get_pending_tasks", "_watchdog_loop"} <= public


# ---- behavioural: a guest reaches no executor ----------------------------------------------------------------


def _guest_post(app, path: str, **json) -> int:
    from fastapi.testclient import TestClient

    return TestClient(app, raise_server_exceptions=False).post(path, json=json or {"message": "run this"}).status_code


def test_control_the_guest_probe_flags_a_route_that_admits_guests():
    """The probe must be able to fail: a route that takes get_current_user and 'queues' for a guest is flagged."""
    from fastapi import APIRouter, Depends, FastAPI

    import auth

    queued = []
    r = APIRouter(prefix="/api/chat")

    @r.post("/task")
    async def create_background_task(user: dict = Depends(auth.get_current_user)):   # the removed route's shape
        queued.append(user["user_id"])
        return {"task_id": 1}

    app = FastAPI()
    app.include_router(r)
    assert _guest_post(app, "/api/chat/task") == 200
    assert queued == ["guest"], "the guest-admitting control did not reach its executor"


def test_guest_post_to_chat_task_reaches_nothing():
    from fastapi import FastAPI

    from routers import chat

    app = FastAPI()
    app.include_router(chat.router)
    assert not any(
        "POST" in getattr(rt, "methods", ()) and rt.path.rstrip("/").endswith("/task") for rt in app.routes
    ), "POST /api/chat/task is back"
    assert _guest_post(app, "/api/chat/task") in (404, 405)


def test_guest_post_to_agent_task_intake_and_hermes_delegate_is_refused():
    from fastapi import FastAPI

    from routers import system

    app = FastAPI()
    app.include_router(system._agent_card_router)
    # the removed intake route: nothing accepts a POST to /api/agent/tasks any more
    assert _guest_post(app, "/api/agent/tasks", task="x", caller="a") in (404, 405)
    # delegate is privileged (guests are refused before any routing) ...
    assert _guest_post(app, "/api/agent/delegate", agent_name="hermes", task="x") == 401


@pytest.mark.asyncio
async def test_privileged_delegate_to_hermes_is_gone_not_queued(monkeypatch):
    from fastapi import HTTPException

    from routers import system

    with pytest.raises(HTTPException) as ei:
        await system.delegate_to_agent({"agent_name": "hermes", "task": "rm -rf"}, user={"user_id": "jason", "role": "admin"})
    assert ei.value.status_code == 410


@pytest.mark.asyncio
async def test_mcp_a2a_delegate_to_hermes_is_refused_not_queued(monkeypatch):
    import mcp_server

    async def fake_actor(db, name, args, actor_context=None):
        return {"user_id": "jason"}

    monkeypatch.setattr(mcp_server, "_resolve_mcp_actor", fake_actor)
    out = await mcp_server._execute_tool(None, "a2a_delegate", {"agent_name": "hermes", "task": "x"})
    assert "retired" in str(out.get("error", "")) and "queued" not in str(out)
