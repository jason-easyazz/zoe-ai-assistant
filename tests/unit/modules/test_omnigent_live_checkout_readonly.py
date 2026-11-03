"""The zoe-omnigent container must never be able to write the LIVE checkout.

THE INCIDENT (2026-11-02 22:55 AWST, ledger #2255): a cross-review polly session ran
`cd /workspace && git fetch -q origin pull/2253/head; git checkout -q FETCH_HEAD` inside
the container, where `../../:/workspace` was a READ-WRITE bind of /home/zoe/assistant.
Production HEAD detached onto an unmerged PR, every deploy after it refused (not a
fast-forward), and zoe-data's night restart loaded the draft into production.

THE CONTRACT pinned here, by parsing the compose file (a comment cannot satisfy it):

* every mount whose TARGET is /workspace or under it is read-only — `.git` included;
* every mount whose target is /home/zoe/assistant or under it is read-only, EXCEPT exactly
  the four .git subtrees linked-worktree work writes (objects, refs, logs, worktrees), each
  bound from the matching repo subtree. The .git ROOT (HEAD, index, FETCH_HEAD, config,
  packed-refs) therefore stays read-only: nothing in the container can move the live HEAD;
* every mount whose SOURCE is the repo (or inside it) is read-only unless it is one of
  those four;
* the default session workspace (OMNIGENT_RUNNER_WORKSPACE) is a read-only view.

Each rule has a mutated-compose negative control below, so a checker that passes
everything cannot hide here.
"""
from __future__ import annotations

import copy
import posixpath
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE = REPO_ROOT / "modules" / "omnigent" / "docker-compose.module.yml"
MODULE_DIR = "/REPO/modules/omnigent"   # compose resolves relative sources from here
LIVE = "/home/zoe/assistant"
RW_GIT_SUBTREES = ("objects", "refs", "logs", "worktrees")


def _compose() -> dict:
    yaml = pytest.importorskip("yaml")
    return yaml.safe_load(COMPOSE.read_text())


def _parse(vol) -> tuple[str, str, bool]:
    """(source, target, read_only) for a short- or long-syntax compose volume."""
    if isinstance(vol, dict):
        return str(vol.get("source", "")), str(vol["target"]), bool(vol.get("read_only", False))
    # Resolve compose's ${VAR:-default} to its default first: the default holds no colon,
    # but the expression does (the ZOE_WORKTREE_ROOT mount).
    parts = re.sub(r"\$\{[A-Za-z_][A-Za-z0-9_]*:-([^}]*)\}", r"\1", str(vol)).split(":")
    src, tgt = parts[0], parts[1]
    opts = parts[2].split(",") if len(parts) > 2 else []
    return src, tgt, "ro" in opts


def _repo_path(src: str) -> str | None:
    """The source as a /REPO-relative path when it is the repo or inside it, else None."""
    if src.startswith((".", "/")) is False:
        return None   # a named volume
    if src.startswith("/"):
        if src == LIVE or src.startswith(LIVE + "/"):
            return "/REPO" + src[len(LIVE):]
        return None
    p = posixpath.normpath(posixpath.join(MODULE_DIR, src))
    return p if p == "/REPO" or p.startswith("/REPO/") else None


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root + "/")


def violations(service: dict) -> list[str]:
    out: list[str] = []
    allowed_rw = {f"{LIVE}/.git/{d}": f"/REPO/.git/{d}" for d in RW_GIT_SUBTREES}
    targets: dict[str, bool] = {}
    for vol in service.get("volumes", []):
        src, tgt, ro = _parse(vol)
        targets[tgt] = ro
        repo_src = _repo_path(src)
        if ro:
            continue
        if _under(tgt, "/workspace"):
            out.append(f"rw mount into the live checkout view: {vol}")
        elif _under(tgt, LIVE):
            if allowed_rw.get(tgt) != repo_src:
                out.append(f"rw mount into the live checkout: {vol}")
        elif repo_src is not None:
            out.append(f"rw mount of the live checkout at another path: {vol}")
    for required in ("/workspace", LIVE):
        if targets.get(required) is not True:
            out.append(f"{required} must be mounted, read-only")
    env = service.get("environment", [])
    env = dict(e.split("=", 1) for e in env) if isinstance(env, list) else dict(env)
    ws = env.get("OMNIGENT_RUNNER_WORKSPACE", "")
    if ws and (_under(ws, "/workspace") or _under(ws, LIVE)):
        cover = max((t for t in targets if _under(ws, t)), key=len, default=None)
        if cover is None or not targets[cover]:
            out.append(f"OMNIGENT_RUNNER_WORKSPACE={ws} is a writable view of the live checkout")
    return out


def _service() -> dict:
    return _compose()["services"]["omnigent"]


def test_the_live_checkout_is_read_only_in_the_omnigent_container():
    assert violations(_service()) == []


def test_exactly_the_four_git_subtrees_are_writable():
    rw = sorted(
        _parse(v)[1] for v in _service()["volumes"]
        if not _parse(v)[2] and _under(_parse(v)[1], LIVE)
    )
    assert rw == sorted(f"{LIVE}/.git/{d}" for d in RW_GIT_SUBTREES)


def test_the_executor_worktree_mount_stays_writable_at_the_same_path():
    # The read-only change must not take away where work legitimately happens.
    wt = [v for v in _service()["volumes"] if "ZOE_WORKTREE_ROOT" in str(v)]
    assert len(wt) == 1
    src, tgt, ro = _parse(wt[0])
    assert src == tgt and not ro


def _mutate(edit) -> list[str]:
    svc = copy.deepcopy(_service())
    svc["volumes"] = [edit(v) for v in svc["volumes"]]
    return violations(svc)


@pytest.mark.parametrize(
    ("name", "edit"),
    [
        # The incident's own mount line.
        ("rw /workspace", lambda v: "../../:/workspace" if v == "../../:/workspace:ro" else v),
        ("rw /home/zoe/assistant", lambda v: "../../:/home/zoe/assistant" if v == "../../:/home/zoe/assistant:ro" else v),
        # The whole .git rw would let a container `git checkout` move the live HEAD.
        ("rw whole .git", lambda v: "../../.git:/home/zoe/assistant/.git" if v.endswith("/.git/refs") else v),
        # A rw sub-mount nested under the read-only /workspace view.
        ("rw nested under /workspace", lambda v: "../../.git/refs:/workspace/.git/refs" if v.endswith("/.git/refs") else v),
        # The repo re-exposed rw somewhere else.
        ("rw repo elsewhere", lambda v: "../../:/srv/repo" if v == "../../:/workspace:ro" else v),
        ("rw .mcp.json shadow", lambda v: v.replace(".mcp.json:ro", ".mcp.json") if isinstance(v, str) else v),
    ],
)
def test_negative_controls_each_mutation_is_caught(name, edit):
    assert _mutate(edit), f"the checker missed: {name}"


def test_negative_control_runner_workspace_on_a_writable_live_view():
    svc = copy.deepcopy(_service())
    svc["volumes"] = ["../../:/workspace" if v == "../../:/workspace:ro" else v for v in svc["volumes"]]
    assert any("OMNIGENT_RUNNER_WORKSPACE" in m for m in violations(svc))
