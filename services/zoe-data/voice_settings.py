"""Persisted TTS voice preference + Kokoro voice catalogue.

Zoe's speaking voice is a HOUSEHOLD setting (the panel speaks with one voice for
everyone), persisted in the generic ``app_settings`` table (migration 0018) under
key ``tts_voice`` and selectable from the touch panel's "Zoe's voice" card.

Resolution order (``resolve_tts_voice``):
  1. explicit per-call override (the Preview action)
  2. persisted preference (``app_settings.tts_voice``), cached in-process with a
     short TTL so the per-sentence voice path never adds a DB round-trip
  3. env default — ``ZOE_KOKORO_VOICE`` / ``KOKORO_VOICE``, falling back to
     ``af_sky`` (the shipped default)

Everything here is fail-open: any DB or voices-bin error degrades to the env
default / empty catalogue — a broken settings store must never break speech.

Catalogue: the voices bin (``ZOE_KOKORO_VOICES``, the kokoro-onnx NPZ archive)
is the single source of truth for which voice names exist — stock ``af_*`` etc.
plus any ``zoe_*`` blends present in an operator-installed augmented bin (built
by ``labs/kokoro-voice-blend/blend_zoe_voices.py --emit-bin``). The UI never
hardcodes names.
"""
from __future__ import annotations

import logging
import os
import re
import time
import zipfile
from typing import Optional

logger = logging.getLogger(__name__)

SETTING_KEY = "tts_voice"
FALLBACK_VOICE = "af_sky"

# Fixed preview sentence — the ONLY text the panel-facing preview endpoint will
# synthesize (server-fixed so preview can never become a free-text TTS proxy).
PREVIEW_TEXT = "Hi, I'm Zoe. This is how I'd sound around the house."

# Voice ids are kokoro tensor names: short, lowercase, [a-z0-9_].
_VOICE_NAME_RE = re.compile(r"^[a-z0-9_]{2,64}$")

# ── in-process preference cache ────────────────────────────────────────────
_PREF_TTL_S = 5.0
_pref_cache: tuple[float, Optional[str]] | None = None  # (expires_at, voice|None)

# ── voices-bin catalogue cache, keyed by (path, mtime) ─────────────────────
_catalogue_cache: tuple[str, float, list[str]] | None = None


def env_default_voice() -> str:
    """The env-configured default voice (the pre-preference behaviour)."""
    return (
        os.environ.get("ZOE_KOKORO_VOICE", "").strip()
        or os.environ.get("KOKORO_VOICE", "").strip()
        or FALLBACK_VOICE
    )


def _voices_bin_path() -> str:
    return os.environ.get("ZOE_KOKORO_VOICES", "").strip()


def list_kokoro_voices() -> list[str]:
    """Names available in the loaded voices bin (sorted), [] if unreadable.

    The kokoro-onnx voices bin is an NPZ (zip of ``<name>.npy`` members), so the
    catalogue is just the zip namelist — no numpy import, no tensor loads.
    Cached by (path, mtime) so a swapped/augmented bin is picked up on the next
    call without a restart of zoe-data (the SIDECAR still needs its documented
    restart to load new tensors).
    """
    global _catalogue_cache
    path = _voices_bin_path()
    if not path:
        return []
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    if _catalogue_cache and _catalogue_cache[0] == path and _catalogue_cache[1] == mtime:
        return list(_catalogue_cache[2])
    try:
        with zipfile.ZipFile(path) as zf:
            names = sorted(
                {
                    member[: -len(".npy")]
                    for member in zf.namelist()
                    if member.endswith(".npy")
                }
            )
    except Exception as exc:  # fail-open: catalogue is a convenience, not a gate
        logger.warning("voice_settings: cannot read voices bin %s: %s", path, exc)
        return []
    names = [n for n in names if _VOICE_NAME_RE.match(n)]
    _catalogue_cache = (path, mtime, names)
    return list(names)


def voice_label(name: str) -> str:
    """Human label for a kokoro voice id (af_sky → 'Sky · American female')."""
    if name.startswith("zoe_"):
        pretty = name[len("zoe_"):].replace("_", " ").strip().title() or name
        return f"{pretty} · Zoe blend"
    m = re.match(r"^([abefhijpz])([fm])_([a-z0-9_]+)$", name)
    if not m:
        return name
    accents = {
        "a": "American", "b": "British", "e": "Spanish", "f": "French",
        "h": "Hindi", "i": "Italian", "j": "Japanese", "p": "Portuguese",
        "z": "Chinese",
    }
    gender = "female" if m.group(2) == "f" else "male"
    pretty = m.group(3).replace("_", " ").title()
    return f"{pretty} · {accents.get(m.group(1), '')} {gender}".replace("·  ", "· ")


def invalidate_cache() -> None:
    global _pref_cache
    _pref_cache = None


async def _read_persisted_voice() -> Optional[str]:
    """Persisted preference or None; never raises (fail-open to env default)."""
    try:
        from database import get_db_ctx

        async with get_db_ctx() as db:
            cursor = await db.execute(
                "SELECT value FROM app_settings WHERE key = ?", (SETTING_KEY,)
            )
            row = await cursor.fetchone()
    except Exception as exc:
        logger.debug("voice_settings: preference read failed (fail-open): %s", exc)
        return None
    if not row:
        return None
    value = str(row["value"] or "").strip()
    return value if _VOICE_NAME_RE.match(value) else None


def _in_catalogue(voice: str, catalogue: list[str]) -> bool:
    """True when the voice is usable: present in the bin, or no catalogue is
    readable (fail-open — never block speech on a missing/broken bin)."""
    return not catalogue or voice in catalogue


async def get_tts_voice() -> str:
    """The voice Zoe should speak with right now (pref → env default).

    A candidate that is no longer in the loaded voices bin (e.g. the operator
    swapped back to the stock bin after a zoe_* voice was picked) is skipped, so
    a stale preference degrades to the env default / a catalogue voice instead
    of making every synth call miss into the lower-quality fallback engines.
    """
    global _pref_cache
    now = time.monotonic()
    if _pref_cache and _pref_cache[0] > now:
        stored = _pref_cache[1]
    else:
        stored = await _read_persisted_voice()
        _pref_cache = (now + _PREF_TTL_S, stored)
    catalogue = list_kokoro_voices()
    for candidate in (stored, env_default_voice(), FALLBACK_VOICE):
        if candidate and _in_catalogue(candidate, catalogue):
            return candidate
    return catalogue[0]  # non-empty here: an empty catalogue accepted FALLBACK


async def resolve_tts_voice(override: Optional[str] = None) -> str:
    """Per-call voice: explicit override (shape- and catalogue-checked) beats
    the preference; an unusable override falls through to the preference."""
    if override:
        candidate = str(override).strip()
        if _VOICE_NAME_RE.match(candidate) and _in_catalogue(candidate, list_kokoro_voices()):
            return candidate
    return await get_tts_voice()


def match_voice(requested: str) -> Optional[str]:
    """Map a spoken/tapped voice name onto a catalogue entry.

    Exact id match first; then a unique suffix/word match so "sky" → af_sky and
    "ember" → zoe_ember. Ambiguous or unknown → None (callers speak an error).
    """
    wanted = re.sub(r"[\s-]+", "_", str(requested or "").strip().lower())
    if not wanted:
        return None
    voices = list_kokoro_voices()
    if wanted in voices:
        return wanted
    partial = [v for v in voices if v.endswith(f"_{wanted}") or wanted in v.split("_")]
    return partial[0] if len(partial) == 1 else None


async def set_tts_voice(name: str) -> str:
    """Persist the household voice preference. Raises ValueError on a bad name."""
    voice = str(name or "").strip()
    if not _VOICE_NAME_RE.match(voice):
        raise ValueError("voice must be a short lowercase kokoro voice id")
    catalogue = list_kokoro_voices()
    if catalogue and voice not in catalogue:
        raise ValueError(f"unknown voice {voice!r}")
    from database import get_db_ctx

    async with get_db_ctx() as db:
        await db.execute(
            """INSERT INTO app_settings (key, value, updated_at)
               VALUES (?, ?, NOW())
               ON CONFLICT(key) DO UPDATE
               SET value = excluded.value, updated_at = NOW()""",
            (SETTING_KEY, voice),
        )
        await db.commit()
    invalidate_cache()
    return voice
