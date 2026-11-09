"""forget_session - after a forget, the brain's own conversation history must not hand the item back.

QA round 7 (2026-11-09): "forget the thing about asha and mushrooms" was answered, and the very next turn the brain said
"Asha hates mushrooms". Two things re-fed it. The rows were not erased (the topic phrase was not recognised - ``forget_topic``),
and the Flue sidecar keeps its OWN per-session history, built from the turns it answered: the owner's original sentence, and the
brain's own replies, are still in it. Erasing the stored rows cannot reach that copy (the sidecar is a separate process).

The only honest way to make the brain forget what is in its session is to stop using that session. After a forget that changed
anything, the member's sessions are addressed to the sidecar under a new alias (``<session>~f<epoch>``), so the next turn starts a
clean sidecar history. Nothing is lost that matters: the stored memory is read fresh every turn (the recall packet), and the
fast-path exchanges of the conversation are carried by ``flue_fastpath_context`` (which redacts the forget confirmation).

The epoch is DURABLE (a small private JSON file, written atomically): a zoe-data restart, or any cap, must never address the sidecar's
old history again - that history is exactly what was forgotten. A member's epoch is never evicted; the file holds one short number per
member who has ever forgotten something. Stdlib only; never raises (a write that fails leaves the in-memory epoch, which still holds
until the next restart).
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_PATH = Path.home() / ".zoe" / "forget_session_epochs.json"
# user id -> epoch (ms) of that member's latest forget that changed something; loaded once, written through
_EPOCH: "dict[str, int]" = {}
_LOADED = False


def _load() -> None:
    global _LOADED
    if _LOADED:
        return
    _LOADED = True
    try:
        raw = json.loads(_PATH.read_text())
        if isinstance(raw, dict):
            for k, v in raw.items():
                if isinstance(k, str) and isinstance(v, int) and v > _EPOCH.get(k, 0):
                    _EPOCH[k] = v
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001 - an unreadable file means no alias; rows and packet stay correct
        logger.warning("forget_session: epochs unreadable (%s)", type(exc).__name__)


def _save() -> None:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(_PATH.parent), prefix=".forget_session.")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(_EPOCH, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, _PATH)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    except Exception as exc:  # noqa: BLE001
        logger.warning("forget_session: epochs not saved (%s) - the alias holds until restart", type(exc).__name__)


def note_forget(user_id: Optional[str]) -> None:
    """A forget changed this member's stored memory: their brain sessions restart on the next turn. Never raises."""
    try:
        uid = (user_id or "").strip()
        if not uid:
            return
        _load()
        epoch = int(time.time() * 1000)
        _EPOCH[uid] = max(epoch, _EPOCH.get(uid, 0) + 1)       # strictly increasing: two forgets in one millisecond still rotate
        _save()
    except Exception:  # noqa: BLE001
        pass


def sidecar_session(user_id: Optional[str], session_id: str) -> str:
    """The session id the sidecar is addressed with: the plain one until this member forgets something, then an alias with a fresh
    history. Unknown member or no forget -> ``session_id`` unchanged."""
    try:
        _load()
        epoch = _EPOCH.get((user_id or "").strip())
        if not epoch or not session_id:
            return session_id
        return f"{session_id}~f{epoch}"
    except Exception:  # noqa: BLE001
        return session_id
