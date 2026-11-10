"""Generated docs are LOCAL build products, never committed (2026-11-10).

The committed copies of the flag inventory, live-state and memory-writer map conflicted on nearly every concurrent PR
(the landing logs recorded 94 "inventory-only conflict" auto-resolutions, each a push plus a full CI re-run). They are
now gitignored and regenerated where they are read: by a person locally, and by the deploy on the box for
``flag-inventory.json`` (``services/zoe-data/self_model.py`` reads it at runtime). Every invariant they used to stand
in for is tested from the code itself (``test_flag_registry.py``, ``test_roadmap_flag_claims.py``,
``test_memory_writer_map.py``), locally and offline.

``docs/knowledge/forget-reach.md`` deliberately STAYS committed: only its table is generated, between markers, inside
hand-written policy prose that the test also checks (``test_forget_reach_inventory.py``).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
GENERATED = (
    "docs/knowledge/flag-inventory.json",
    "docs/knowledge/flag-inventory.md",
    "docs/knowledge/live-state.json",
    "docs/knowledge/memory-writer-map.md",
)
HAND_ANNOTATED_STAYS_TRACKED = ("docs/knowledge/forget-reach.md",)


def _git(*args: str) -> subprocess.CompletedProcess:
    if shutil.which("git") is None or not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, timeout=60)


def test_generated_docs_are_not_tracked():
    r = _git("ls-files", "--", *GENERATED)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", f"generated docs are tracked again (they will conflict on every PR): {r.stdout}"


def test_generated_docs_are_gitignored():
    r = _git("check-ignore", "--no-index", *GENERATED)
    ignored = set(r.stdout.split())
    assert ignored == set(GENERATED), f"not ignored: {sorted(set(GENERATED) - ignored)}"


def test_the_hand_annotated_doc_stays_tracked():
    r = _git("ls-files", "--error-unmatch", "--", *HAND_ANNOTATED_STAYS_TRACKED)
    assert r.returncode == 0, r.stderr


def test_the_deploy_regenerates_the_runtime_inventory_after_the_reset():
    """self_model.py reads flag-inventory.json at runtime; `git reset --hard` onto a tree that no longer tracks it
    deletes it, so the deploy must regenerate it locally (never fetch it) after the reset and before any restart."""
    text = (REPO / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
    reset = text.index('git reset --hard "$target"')
    regen = text.index("python3 tools/audit/flag_inventory.py", reset)
    restarts = [i for i in (text.find("systemctl restart", reset), text.find("docker restart", reset)) if i > 0]
    assert restarts and regen < min(restarts), "the inventory must exist before anything restarts"
    block = text[reset:regen + 400]
    # fail-closed: the stale copy goes first (an ignored file survives the reset) and a failure stops the deploy
    assert "rm -f docs/knowledge/flag-inventory.json" in block
    assert "exit 1" in block[block.index("python3 tools/audit/flag_inventory.py"):]


def test_the_manual_deploy_regenerates_it_too_fail_closed():
    text = (REPO / "scripts" / "maintenance" / "deploy_live.sh").read_text(encoding="utf-8")
    merge = text.index('merge --ff-only "$target"')
    regen = text.index("python3 tools/audit/flag_inventory.py", merge)
    assert text.index("flag-inventory.json", merge) < regen, "the stale copy must be removed before regenerating"
    assert "exit 1" in text[regen:regen + 300]
    assert regen < text.index("restart", regen)
