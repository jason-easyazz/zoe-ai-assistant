"""PR 2443 round 3: the local landing rolls back on ANY exception, the forbidden floor covers
every repo script the deploy / voice-gate workflows run, and the example config ships and loads."""
from __future__ import annotations

import datetime as dt
import fnmatch
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from conftest import PLANTED_TOKEN, FakeEngine, fix_calc
from selfheal import gitops
from selfheal.config import DEFAULT_FORBIDDEN, HARD_FORBIDDEN, Config, MAX_DAILY_CAP
from selfheal.landing import NoopApplier
from selfheal.runner import Runner

NOON = dt.datetime(2026, 11, 13, 12, 0)
REPO_ROOT = Path(__file__).resolve().parents[3]


def state(cfg):
    return json.loads((Path(cfg.state_dir) / "state.json").read_text())


# --- 1. any exception after the ref advanced restores the snapshot --------------------------

class RaisingApplier(NoopApplier):
    def __init__(self, cfg, exc):
        super().__init__(cfg)
        self.exc = exc

    def apply(self, sha):
        self.calls.append(("apply", sha))
        raise self.exc


def _assert_rolled_back(cfg, tip, out=None):
    assert gitops.rev(str(cfg.repo), "refs/heads/main") == tip      # integration == snapshot
    st = state(cfg)
    rec = next(iter(st["tickets"].values()))
    assert rec["status"] == "rolled_back"
    assert next(iter(st["faults"].values()))["parked"] is True      # parked like a failed verify
    blob = "\n".join(p.read_text() for p in Path(cfg.state_dir).rglob("*") if p.is_file())
    assert PLANTED_TOKEN not in blob                                 # exception CLASS only
    if out is not None:
        assert out["outcome"] == "rolled_back" and out["why"].startswith("exception:")


@pytest.fixture
def full_local(local_scratch):
    cfg = local_scratch.cfg
    cfg.dispatch = "full"
    return local_scratch, cfg, gitops.rev(str(cfg.repo), "refs/heads/main")


def test_apply_raises(full_local):
    _, cfg, tip = full_local
    ap = RaisingApplier(cfg, OSError(f"disk said {PLANTED_TOKEN}"))
    out = Runner(cfg, engine=FakeEngine(fix_calc), applier=ap, now=lambda: NOON).run_once()
    _assert_rolled_back(cfg, tip, out)
    assert out["why"].startswith("exception:OSError")
    assert [c[0] for c in ap.calls] == ["apply", "rollback"]


def test_health_check_raises_when_systemctl_is_missing(full_local, monkeypatch, tmp_path):
    scratch, cfg, tip = full_local
    cfg.health_checks = [["systemctl", "--user", "is-active", "zoe-data.service"]]
    only = tmp_path / "onlygit"
    only.mkdir()
    for tool in ("git", "flock", "pgrep", "bwrap", "prlimit"):
        os.symlink(shutil.which(tool), only / tool)
    monkeypatch.setenv("PATH", str(only))                             # no systemctl: FileNotFoundError
    out = Runner(cfg, engine=FakeEngine(fix_calc), now=lambda: NOON).run_once()
    _assert_rolled_back(cfg, tip, out)
    assert out["why"] == "exception:FileNotFoundError"


def test_verify_raises(full_local, monkeypatch):
    _, cfg, tip = full_local

    def boom(*a, **k):
        raise RuntimeError(f"verify blew up {PLANTED_TOKEN}")
    monkeypatch.setattr(gitops, "run_repro", boom)
    out = Runner(cfg, engine=FakeEngine(fix_calc), now=lambda: NOON).run_once()
    _assert_rolled_back(cfg, tip, out)
    assert out["why"] == "exception:RuntimeError"


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(3)])
def test_base_exception_rolls_back_records_then_reraises(full_local, exc):
    _, cfg, tip = full_local
    ap = RaisingApplier(cfg, exc)
    with pytest.raises(type(exc)):
        Runner(cfg, engine=FakeEngine(fix_calc), applier=ap, now=lambda: NOON).run_once()
    _assert_rolled_back(cfg, tip)
    assert next(iter(state(cfg)["tickets"].values()))["why"] == f"exception:{type(exc).__name__}"


def test_rollback_apply_failure_is_recorded_and_ref_still_restored(full_local):
    _, cfg, tip = full_local

    class Both(RaisingApplier):
        def rollback(self, sha):
            raise OSError("live rollback failed")
    out = Runner(cfg, engine=FakeEngine(fix_calc), applier=Both(cfg, OSError("x")), now=lambda: NOON).run_once()
    _assert_rolled_back(cfg, tip, out)
    assert out["why"] == "exception:OSError,rollback_apply_failed:OSError"


# --- 2. the floor covers every repo script the workflows run ----------------------------------

WORKFLOWS = (".github/workflows/deploy.yml", ".github/workflows/voice-gate.yml")
SCRIPT = re.compile(r"(?<![\w-])((?:\./)?(?:scripts|tools|services|labs|modules|config)/[\w./-]+?\.(?:py|sh|js|mjs|ts))(?![\w])")


def _run_blocks(node):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "run" and isinstance(v, str):
                yield v
            else:
                yield from _run_blocks(v)
    elif isinstance(node, list):
        for x in node:
            yield from _run_blocks(x)


def workflow_scripts(root: Path = REPO_ROOT) -> set:
    """Repo files executed by the workflows' `run:` blocks (comments ignored): `python3 <path>`,
    `bash <path>`, `./<path>`, `"$REPO/<path>.sh"` - any existing repo script path on a code line."""
    found = set()
    for wf in WORKFLOWS:
        doc = yaml.safe_load((root / wf).read_text())
        for block in _run_blocks(doc):
            for line in block.splitlines():
                if line.strip().startswith("#"):
                    continue
                code = line.split(" #", 1)[0]
                for m in SCRIPT.finditer(code):
                    p = m.group(1).removeprefix("./")
                    if (root / p).is_file():
                        found.add(p)
    return found


def uncovered(scripts: set, forbidden) -> list:
    return sorted(p for p in scripts if not any(fnmatch.fnmatch(p, pat) for pat in forbidden))


def test_every_workflow_run_script_is_forbidden():
    scripts = workflow_scripts()
    assert "tools/audit/flag_inventory.py" in scripts and "scripts/maintenance/voice_gate_check.py" in scripts
    missing = uncovered(scripts, HARD_FORBIDDEN)
    assert not missing, f"workflow-run scripts missing from the forbidden floor: {missing}"


def test_coverage_check_names_a_dropped_entry():
    """The negative control, kept as a test: drop one literal entry and the check names it."""
    dropped = [p for p in DEFAULT_FORBIDDEN if p != "tools/audit/flag_inventory.py"]
    assert uncovered(workflow_scripts(), dropped) == ["tools/audit/flag_inventory.py"]


def test_forbidden_floor_is_a_literal_list():
    """The floor must not be computed from the workflow at run time (the engine must not be able
    to influence what is forbidden by editing a workflow)."""
    import inspect

    import selfheal.config as config_mod
    src = inspect.getsource(config_mod)
    assert "import yaml" not in src and "deploy.yml" not in src and "voice-gate.yml" not in src
    assert isinstance(DEFAULT_FORBIDDEN, list) and "tools/audit/flag_inventory.py" in HARD_FORBIDDEN


# --- 3. the example config ships and loads ----------------------------------------------------

EXAMPLE = Path(__file__).resolve().parents[1] / "self-heal.example.json"


def test_example_config_is_tracked_and_not_ignored():
    rel = str(EXAMPLE.relative_to(REPO_ROOT))
    ignored = subprocess.run(["git", "-C", str(REPO_ROOT), "check-ignore", "-q", "--no-index", rel]).returncode == 0
    assert EXAMPLE.is_file() and not ignored


def test_example_config_loads_through_the_real_loader_without_weakening():
    cfg = Config.load(str(EXAMPLE))
    assert cfg.dispatch == "dry" and cfg.landing == "github"
    assert cfg.daily_cap <= MAX_DAILY_CAP and set(HARD_FORBIDDEN) <= set(cfg.forbidden)
    assert cfg.codex_args == [] and cfg.claude_args == []
