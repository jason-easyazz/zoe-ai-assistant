"""The ZOE_* flag inventory, scanned from THIS tree in memory - for tests that ask "is flag X in the inventory".

``docs/knowledge/flag-inventory.json`` is a LOCAL build product since 2026-11-10 (gitignored; the committed copy
conflicted on nearly every concurrent PR), so a test must never read it from disk: it may be absent (CI, a fresh
clone) or stale (a dev box that has not regenerated). This runs the same scan ``tools/audit/flag_inventory.py`` writes,
once per pytest process (the scan walks every tracked source file). Local, offline, stdlib only.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
_TOOL = REPO / "tools" / "audit" / "flag_inventory.py"
_MOD = "_zoe_flag_inventory_for_tests"


def _tool():
    mod = sys.modules.get(_MOD)
    if mod is None:
        spec = importlib.util.spec_from_file_location(_MOD, _TOOL)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[_MOD] = mod
        spec.loader.exec_module(mod)
    return mod


@lru_cache(maxsize=1)
def _inventory_json() -> str:
    return json.dumps(_tool().scan_repo(REPO), sort_keys=True)


def inventory() -> dict:
    """A fresh copy of the full inventory (the dict ``flag_inventory.py`` writes as JSON)."""
    return json.loads(_inventory_json())


def write_inventory(path: Path) -> Path:
    """Write the inventory where a runtime reader (e.g. ``self_model.INVENTORY``) can be pointed at it."""
    path.write_text(_inventory_json() + "\n", encoding="utf-8")
    return path
