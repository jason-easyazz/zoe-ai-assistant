"""forget_session - after a forget, the brain's own conversation history must not hand the item back.

QA round 7 (2026-11-09): "forget the thing about asha and mushrooms" was answered, and the very next turn the brain said
"Asha hates mushrooms". Two things re-fed it. The rows were not erased (the topic phrase was not recognised - ``forget_topic``),
and the Flue sidecar keeps its OWN per-session history, built from the turns it answered: the owner's original sentence, and the
brain's own replies, are still in it. Erasing the stored rows cannot reach that copy (the sidecar is a separate process).

The only honest way to make the brain forget what is in its session is to stop using that session. After a forget that changed
anything, the member's sessions are addressed to the sidecar under a new alias (``<session>~f<epoch>``), so the next turn starts a
clean sidecar history. Nothing is lost that matters: the stored memory is read fresh every turn (the recall packet), and the
fast-path exchanges of the conversation are carried by ``flue_fastpath_context`` (which redacts the forget confirmation).

Process-local on purpose: a zoe-data restart inside the same conversation drops the alias (the sidecar's durable history would be
addressed again); the packet and rows stay correct either way. Bounded; stdlib only; never raises.
"""
from __future__ import annotations

import time
from collections import OrderedDict
from typing import Optional

_MAX_USERS = 256
# user id -> epoch (ms) of that member's latest forget that changed something
_EPOCH: "OrderedDict[str, int]" = OrderedDict()


def note_forget(user_id: Optional[str]) -> None:
    """A forget changed this member's stored memory: their brain sessions restart on the next turn. Never raises."""
    try:
        uid = (user_id or "").strip()
        if not uid:
            return
        epoch = int(time.time() * 1000)
        prev = _EPOCH.pop(uid, 0)
        _EPOCH[uid] = max(epoch, prev + 1)       # strictly increasing: two forgets in one millisecond still rotate
        while len(_EPOCH) > _MAX_USERS:
            _EPOCH.popitem(last=False)
    except Exception:  # noqa: BLE001
        pass


def sidecar_session(user_id: Optional[str], session_id: str) -> str:
    """The session id the sidecar is addressed with: the plain one until this member forgets something, then an alias with a fresh
    history. Unknown member or no forget -> ``session_id`` unchanged."""
    try:
        epoch = _EPOCH.get((user_id or "").strip())
        if not epoch or not session_id:
            return session_id
        return f"{session_id}~f{epoch}"
    except Exception:  # noqa: BLE001
        return session_id
