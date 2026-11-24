"""Music and home control each have ONE way out of zoe-data. Structural, over EVERY module of the service (tests aside).

HOME. A command to the house is a POST to the Home Assistant bridge's device or scene path. Only ``home_control.py`` names
those paths and only it sends them; no other module POSTs to the bridge at all.

MUSIC. A command to Music Assistant is a POST of ``{"command": ...}`` to its API. Only ``music_service.py`` sends one
(``_ma_response``; every other module reaches it through ``music_service``). Three fixed READS elsewhere are named here and
allowed by their exact command. NOT commands, and not here: the provider sign-in (``music_oauth.py``, a config flow over a
websocket) and the admin-gated proxy of Music Assistant's own UI (``routers/music_ma_ui.py``): both are listed in the record
as outside the door.

Negative controls run on the real tree (then restored): the raw device POST put back in ``zoe_agent._ha_control`` ->
``test_only_home_control_sends_a_command_to_the_house`` fails naming it; a ``player_queues/stop`` POST planted in
``routers/music.py`` -> ``test_only_music_service_sends_a_command_to_music_assistant`` fails naming it. The planted cases keep
the scans honest.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

import sql_scan

pytestmark = pytest.mark.ci_safe
ROOT = Path(__file__).resolve().parents[1]
HOME_DOOR, MUSIC_DOOR = "home_control.py", "music_service.py"
HOME_PATHS = ("/devices/control", "/scenes/activate")
BRIDGE_NAMES = ("ha_bridge", "ha_url", "_ha_url")          # what a module calls the bridge's address (lower-cased match)
#: fixed reads of Music Assistant made outside the door, by (relative path, command)
MUSIC_READS = {("routers/music.py", "providers"), ("routers/music.py", "players/all"), ("routers/music.py", "player_queues/items"),
               ("intent_router.py", "players/all")}


def _modules() -> dict:
    skip = {"tests", "alembic", "node_modules", "__pycache__"}
    return {p.relative_to(ROOT).as_posix(): p for p in sorted(ROOT.rglob("*.py")) if not skip.intersection(p.relative_to(ROOT).parts)}


def home_commands(source: str) -> list:
    """Where this module names a command path of the bridge, or POSTs to the bridge: ``(what, line)``."""
    tree = ast.parse(source)
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.body
            and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs \
                and any(p in node.value for p in HOME_PATHS):
            out.append(("path", node.lineno))
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "post" and node.args \
                and any(name in ast.unparse(node.args[0]).lower() for name in BRIDGE_NAMES):
            out.append(("post", node.lineno))
    return out


def music_commands(source: str) -> list:
    """Every ``.post(..., json={"command": X, ...})`` in this module: the command (a literal), or ``<built>`` when it is not one."""
    out = []
    for node in ast.walk(ast.parse(source)):
        if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "post"):
            continue
        body = next((k.value for k in node.keywords if k.arg == "json"), None)
        if isinstance(body, ast.Dict):
            for key, value in zip(body.keys, body.values):
                if isinstance(key, ast.Constant) and key.value == "command":
                    out.append(value.value if isinstance(value, ast.Constant) else "<built>")
        elif isinstance(body, ast.Name) and body.id == "payload" and "command" in source and "_ma_url()" in ast.unparse(node.args[0] if node.args else node):
            out.append("<built>")
    return out


def test_only_home_control_sends_a_command_to_the_house():
    rel = _modules()
    assert len(rel) > 100 and {"smart_home_service.py", "routers/ha_control.py", "zoe_agent.py", "intent_router.py", HOME_DOOR} <= set(rel)
    offenders = {name: found for name, p in rel.items() if name != HOME_DOOR and (found := home_commands(p.read_text("utf-8")))}
    assert not offenders, offenders
    door = home_commands(rel[HOME_DOOR].read_text("utf-8"))
    assert [kind for kind, _ in door].count("path") >= 1           # the door itself names the paths (the scan sees them)
    for name in ("smart_home_service.py", "routers/ha_control.py", "zoe_agent.py", "intent_router.py"):
        assert "home_control.send(" in rel[name].read_text("utf-8"), name


def test_only_music_service_sends_a_command_to_music_assistant():
    rel = _modules()
    seen = set()
    offenders = []
    for name, p in rel.items():
        if name == MUSIC_DOOR:
            continue
        for command in music_commands(p.read_text("utf-8")):
            if (name, command) in MUSIC_READS:
                seen.add((name, command))
            else:
                offenders.append((name, command))
    assert not offenders, offenders
    assert seen == MUSIC_READS                                      # each named read is still there (none is stale)
    assert music_commands(rel[MUSIC_DOOR].read_text("utf-8")) == ["<built>"]      # ONE send in the door, its command a variable


def test_a_planted_command_is_caught():
    assert home_commands("async def f(c, base):\n    await c.post(base + '/devices/control', json={})\n") == [("path", 2)]
    assert home_commands("async def f(c, _HA_BRIDGE):\n    await c.post(f'{_HA_BRIDGE}/x', json={})\n") == [("post", 2)]
    assert home_commands("async def f(c, ha_url, p):\n    await c.post(ha_url + p, json={})\n") == [("post", 2)]
    assert home_commands("def f():\n    '''Sends to /devices/control through the door.'''\n") == []        # a docstring is not a sender
    assert music_commands("async def f(c, u):\n    await c.post(u, json={'command': 'player_queues/stop', 'args': {}})\n") == ["player_queues/stop"]
    assert music_commands("async def f(c, u, cmd):\n    await c.post(u, json={'command': cmd})\n") == ["<built>"]
    assert sql_scan.texts is not None                               # (the shared reader stays importable beside this scan)
