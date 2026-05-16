"""skills_watcher.py — filesystem watcher for live skill cache invalidation.

Monitors ~/.openclaw/workspace/skills/ and ~/.hermes/skills/ for *.md changes.
On any create/modify/delete event, sets the dirty flag in skill_discovery.py so
the next peer card request rebuilds the cache from disk.

Started as a background thread in main.py lifespan; the Observer handle is
returned so the lifespan shutdown can stop and join it cleanly.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

logger = logging.getLogger(__name__)

_OPENCLAW_SKILLS_DIR = os.path.expanduser("~/.openclaw/workspace/skills")
_HERMES_SKILLS_DIR = os.path.expanduser("~/.hermes/skills")


class _SkillEventHandler(FileSystemEventHandler):
    """Marks the appropriate cache dirty on any *.md change."""

    def __init__(self, is_openclaw: bool) -> None:
        self._is_openclaw = is_openclaw

    def _on_md_event(self, event: FileSystemEvent) -> None:
        if isinstance(event.src_path, str) and event.src_path.endswith(".md"):
            from skill_discovery import invalidate_openclaw_cache, invalidate_hermes_cache  # type: ignore[import]
            if self._is_openclaw:
                logger.debug("OpenClaw skill changed: %s — invalidating cache", event.src_path)
                invalidate_openclaw_cache()
            else:
                logger.debug("Hermes skill changed: %s — invalidating cache", event.src_path)
                invalidate_hermes_cache()

    def on_created(self, event: FileSystemEvent) -> None:
        self._on_md_event(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._on_md_event(event)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self._on_md_event(event)


def start_skills_watcher() -> Observer:
    """Start the filesystem watcher and return the Observer for lifecycle management."""
    observer = Observer()

    openclaw_dir = Path(_OPENCLAW_SKILLS_DIR)
    if openclaw_dir.is_dir():
        observer.schedule(
            _SkillEventHandler(is_openclaw=True),
            str(openclaw_dir),
            recursive=True,
        )
        logger.info("Skills watcher: monitoring %s", openclaw_dir)
    else:
        logger.warning("Skills watcher: OpenClaw skills dir not found: %s", openclaw_dir)

    hermes_dir = Path(_HERMES_SKILLS_DIR)
    if hermes_dir.is_dir():
        observer.schedule(
            _SkillEventHandler(is_openclaw=False),
            str(hermes_dir),
            recursive=True,
        )
        logger.info("Skills watcher: monitoring %s", hermes_dir)
    else:
        logger.warning("Skills watcher: Hermes skills dir not found: %s", hermes_dir)

    observer.start()
    return observer
