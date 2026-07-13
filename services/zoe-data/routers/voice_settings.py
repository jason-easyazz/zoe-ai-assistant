"""Voice-selection API: catalogue, household preference, and preview.

Backs the touch panel's "Zoe's voice" settings card (skybridge component
``voice_settings``):

  GET  /api/voice/voices   → catalogue from the loaded voices bin + current pick
  PUT  /api/voice/voice    → persist the household voice preference
  POST /api/voice/preview  → synthesize the FIXED sample sentence in a chosen
                             voice (server-side text only — this endpoint is
                             reachable from the kiosk session, so it must never
                             become a free-text TTS proxy)

The zoe_* blended voices appear automatically once the operator installs the
augmented voices bin (labs/kokoro-voice-blend/blend_zoe_voices.py --emit-bin,
point ZOE_KOKORO_VOICES at it, restart kokoro-tts.service).
"""
import base64

from fastapi import APIRouter, Depends, HTTPException

import voice_settings
from auth import get_current_user
from tts_waterfall import _synthesize_kokoro, _synthesize_kokoro_sidecar

router = APIRouter(prefix="/api/voice", tags=["voice-settings"])

# Fixed preview sentence — spoken on the panel when a voice is auditioned.
PREVIEW_TEXT = voice_settings.PREVIEW_TEXT


def _catalogue_payload(current: str) -> dict:
    names = voice_settings.list_kokoro_voices()
    return {
        "voices": [
            {
                "id": name,
                "label": voice_settings.voice_label(name),
                "zoe": name.startswith("zoe_"),
            }
            for name in names
        ],
        "current": current,
        "default": voice_settings.env_default_voice(),
    }


@router.get("/voices")
async def list_voices(user: dict = Depends(get_current_user)):
    """Available Kokoro voices (from the loaded voices bin) + current selection."""
    current = await voice_settings.get_tts_voice()
    return _catalogue_payload(current)


@router.put("/voice")
async def set_voice(payload: dict, user: dict = Depends(get_current_user)):
    """Persist the household voice preference (validated against the catalogue)."""
    requested = str((payload or {}).get("voice") or "").strip()
    if not requested:
        raise HTTPException(status_code=400, detail="voice is required")
    matched = voice_settings.match_voice(requested) or requested
    try:
        voice = await voice_settings.set_tts_voice(matched)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **_catalogue_payload(voice)}


@router.post("/preview")
async def preview_voice(payload: dict, user: dict = Depends(get_current_user)):
    """Speak the fixed sample sentence in a chosen voice (panel Preview button).

    Text is server-fixed; only the voice name is caller-controlled, and it must
    exist in the catalogue. Kokoro-only on purpose: previewing an Edge/espeak
    fallback voice would misrepresent what the picker changes.
    """
    requested = str((payload or {}).get("voice") or "").strip()
    if not requested:
        raise HTTPException(status_code=400, detail="voice is required")
    catalogue = voice_settings.list_kokoro_voices()
    voice = voice_settings.match_voice(requested)
    if voice is None or (catalogue and voice not in catalogue):
        raise HTTPException(status_code=404, detail=f"unknown voice {requested!r}")

    audio = await _synthesize_kokoro_sidecar(PREVIEW_TEXT, voice=voice)
    if audio is None:
        audio = await _synthesize_kokoro(PREVIEW_TEXT, voice=voice)
    if audio is None:
        raise HTTPException(status_code=503, detail="Kokoro is not available for preview")
    return {
        "ok": True,
        "voice": voice,
        "content_type": "audio/wav",
        "audio_base64": base64.b64encode(audio).decode("ascii"),
    }
