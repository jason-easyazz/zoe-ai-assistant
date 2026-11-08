"""The panel clip corpus: who each saved clip was, and how long a non-member's clip is kept (named guests, spec items 6 and 9).

``ZOE_VOICE_SAVE_AUDIO`` saves EVERY panel turn to ``~/.zoe-voice-samples`` (``routers/voice_tts._maybe_capture_stt``). That corpus is
the OWNER's real-voice regression set, and a guest's turn used to land in it unlabelled, skewing every owner-voice metric and living
on forever. This module is the missing label and the missing retention.

THE SIDECAR  ``<clip>.verdict.json`` beside ``<clip>.wav``: ``{"v": 1, "kind": "member"|"guest"|"unknown", "id", "cluster", "ts"}``,
             written when the server has judged the turn (``voice_command``: the claim it accepted, the named guest it matched, or the
             gate's "nobody matched"; a no-wake window turn the gate dropped). ``id`` is the member id or the guest's person id;
             ``cluster`` is the unknown voice (``panel_voice_clusters``, #2343) the turn was filed under, which is how a member later
             names the clip: the clip points at the EXISTING cluster, there is no second registry. A clip with NO sidecar is LEGACY
             (or a turn the server never judged) and is treated exactly as it always was: nobody relabels history by guessing.
THE READERS  ``scripts/lib/panel_voice_log.clip_verdict`` (stdlib, the same suffix) - owner-voice metrics keep ``member`` and legacy
             clips and drop ``guest`` / ``unknown`` ones. Said-vs-did replay keeps every clip: the words are the test, not the voice.
THE SWEEP    ``ZOE_VOICE_CLIP_RETENTION`` (off): clips whose verdict is ``guest`` or ``unknown`` and older than
             ``ZOE_VOICE_CLIP_RETENTION_DAYS`` (30, owner 2026-11-08) are deleted with their sidecar. NEVER a ``member`` or legacy clip.
             Logs counts only.

Flag ``ZOE_VOICE_CLIP_VERDICT`` (off) gates writing the sidecar. Runbook: ``docs/knowledge/named-guests.md``.
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Optional

from typed_env import env_bool, env_int

logger = logging.getLogger(__name__)

SUFFIX = ".verdict.json"
KINDS = ("member", "guest", "unknown")
NON_MEMBER = ("guest", "unknown")
SWEEP_EVERY_S = 6 * 3600

# The clip THIS transcription saved (a fresh holder per transcription, like ``_stt_raw_var``: never a previous turn's clip).
_HOLDER: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("voice_clip_holder", default=None)


def verdict_enabled() -> bool:
    return env_bool("ZOE_VOICE_CLIP_VERDICT", default=False)


def retention_enabled() -> bool:
    return env_bool("ZOE_VOICE_CLIP_RETENTION", default=False)


def retention_days() -> int:
    return max(1, env_int("ZOE_VOICE_CLIP_RETENTION_DAYS", 30))


def sample_dir() -> str:
    return os.environ.get("ZOE_VOICE_SAMPLE_DIR") or "/home/zoe/.zoe-voice-samples"


def sidecar_path(wav: str) -> str:
    return str(wav)[: -len(".wav")] + SUFFIX if str(wav).endswith(".wav") else str(wav) + SUFFIX


def begin() -> None:
    """A new transcription starts: forget the previous turn's clip."""
    _HOLDER.set({})


def note_saved(path: str) -> None:
    h = _HOLDER.get()
    if h is not None:
        h["path"] = str(path)


def current_clip() -> Optional[str]:
    return (_HOLDER.get() or {}).get("path")


def _write(path: str, body: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".verdict-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(body, f, separators=(",", ":"))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def record(kind: str, ident: str = "", cluster: str = "") -> bool:
    """Label the clip this turn saved with the server's verdict. False when the flag is off, no clip was saved or kind is invalid.
    Never raises (a label is never worth a turn)."""
    try:
        clip = current_clip()
        if not verdict_enabled() or not clip or kind not in KINDS or not os.path.isfile(clip):
            return False
        _write(sidecar_path(clip), dict(v=1, kind=kind, id=str(ident or "")[:64], cluster=str(cluster or "")[:64], ts=int(time.time())))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("voice_clips: verdict not recorded (%s)", type(exc).__name__)
        return False


def read(wav: str) -> Optional[dict]:
    """The clip's verdict, or None (legacy / unreadable / unknown kind): the caller then treats it as it always did."""
    try:
        with open(sidecar_path(wav)) as f:
            d = json.load(f)
        return d if isinstance(d, dict) and d.get("kind") in KINDS else None
    except (OSError, ValueError):
        return None


def _sidecars(directory: str):
    try:
        return [e for e in os.listdir(directory) if e.endswith(SUFFIX) and not e.startswith(".")]
    except OSError:
        return []


def sweep(directory: Optional[str] = None, *, now: Optional[float] = None, days: Optional[int] = None) -> dict:
    """Delete guest / unknown clips older than the retention (and their sidecars). Counts only. Member and legacy clips (no sidecar) are
    never touched; a sidecar whose clip is already gone is removed when it is a non-member's."""
    d = directory or sample_dir()
    cutoff = (now if now is not None else time.time()) - 86400 * (days if days is not None else retention_days())
    removed = kept = 0
    for name in _sidecars(d):
        side = os.path.join(d, name)
        try:
            meta = json.loads(Path(side).read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or meta.get("kind") not in NON_MEMBER:
            continue                                   # member, or something unreadable: never deleted
        ts = meta.get("ts")
        if not isinstance(ts, (int, float)) or ts > cutoff:
            kept += 1
            continue
        wav = os.path.join(d, name[: -len(SUFFIX)] + ".wav")
        try:
            if os.path.isfile(wav):
                os.unlink(wav)
            os.unlink(side)
            removed += 1
        except OSError as exc:
            logger.warning("voice_clips: could not remove an expired clip (%s)", type(exc).__name__)
    if removed:
        logger.info("VOICE_CLIPS retention removed=%d kept_non_member=%d days=%s", removed, kept, days if days is not None else retention_days())
    return dict(removed=removed, kept=kept)


def relabel_cluster(cluster_id: str, kind: str, ident: str, directory: Optional[str] = None) -> int:
    """A cluster was named: its clips are now that guest's (still non-member, still on the 30-day clock). Returns how many."""
    d = directory or sample_dir()
    n = 0
    for name in _sidecars(d):
        side = os.path.join(d, name)
        try:
            meta = json.loads(Path(side).read_text())
            if meta.get("kind") == "unknown" and meta.get("cluster") == cluster_id:
                _write(side, dict(meta, kind=kind, id=str(ident)[:64], cluster=""))
                n += 1
        except (OSError, ValueError, AttributeError):
            continue
    return n


def erase_guests(person_ids, directory: Optional[str] = None) -> int:
    """Forget: delete every saved clip labelled as one of these guests (and the sidecar). Counts only."""
    d = directory or sample_dir()
    ids = {str(i) for i in person_ids}
    n = 0
    for name in _sidecars(d):
        side = os.path.join(d, name)
        try:
            meta = json.loads(Path(side).read_text())
            if meta.get("kind") == "guest" and str(meta.get("id")) in ids:
                wav = os.path.join(d, name[: -len(SUFFIX)] + ".wav")
                if os.path.isfile(wav):
                    os.unlink(wav)
                os.unlink(side)
                n += 1
        except (OSError, ValueError, AttributeError):
            continue
    if n:
        logger.info("VOICE_CLIPS forget removed=%d", n)
    return n


def clips_of_cluster(cluster_id: str, directory: Optional[str] = None) -> list[str]:
    """The saved clips filed under an unknown voice, newest first (wav paths that still exist)."""
    d = directory or sample_dir()
    out = []
    for name in sorted(_sidecars(d), reverse=True):
        try:
            meta = json.loads(Path(os.path.join(d, name)).read_text())
        except (OSError, ValueError):
            continue
        wav = os.path.join(d, name[: -len(SUFFIX)] + ".wav")
        if isinstance(meta, dict) and meta.get("kind") == "unknown" and meta.get("cluster") == cluster_id and os.path.isfile(wav):
            out.append(wav)
    return out


def newest_unknown_clip(directory: Optional[str] = None, valid=None) -> Optional[tuple[str, str]]:
    """``(wav, cluster_id)`` of the newest saved clip filed under an unknown voice that still exists (``valid`` = the live cluster ids), else None."""
    d = directory or sample_dir()
    best = None
    for name in _sidecars(d):
        try:
            meta = json.loads(Path(os.path.join(d, name)).read_text())
        except (OSError, ValueError):
            continue
        if not isinstance(meta, dict) or meta.get("kind") != "unknown" or not meta.get("cluster"):
            continue
        if valid is not None and meta["cluster"] not in valid:
            continue
        wav = os.path.join(d, name[: -len(SUFFIX)] + ".wav")
        if os.path.isfile(wav) and (best is None or name > best[0]):
            best = (name, wav, str(meta["cluster"]))
    return (best[1], best[2]) if best else None


async def run_retention_loop() -> None:
    """Started by ``main.lifespan``; the flag is read per pass, so a flip needs no restart. Never raises."""
    while True:
        await asyncio.sleep(SWEEP_EVERY_S)
        try:
            if retention_enabled():
                await asyncio.get_running_loop().run_in_executor(None, sweep)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("voice_clips: retention pass failed (%s)", type(exc).__name__)
