"""Runtime controls for the single-ticket Multica engineering lane."""

from __future__ import annotations

import os
from pathlib import Path


def pause_path() -> Path:
    override = os.environ.get("ZOE_MULTICA_DISPATCH_PAUSE_FILE", "").strip()
    if override:
        return Path(override)
    return Path(os.path.expanduser("~/.zoe/multica_dispatch_paused"))


def dispatch_is_paused() -> bool:
    return pause_path().exists()


def pause_dispatch(reason: str = "operator requested pause") -> Path:
    path = pause_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(reason.strip() + "\n", encoding="utf-8")
    return path


def resume_dispatch() -> bool:
    path = pause_path()
    existed = path.exists()
    path.unlink(missing_ok=True)
    return existed


def pause_reason() -> str | None:
    path = pause_path()
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8", errors="replace").strip() or "paused"
