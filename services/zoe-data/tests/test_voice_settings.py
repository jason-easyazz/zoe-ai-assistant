"""User-facing voice selection (voice_settings.py + skybridge voice intent).

Covers:
  1. Voice resolution order: explicit override > persisted preference (cached)
     > env default (ZOE_KOKORO_VOICE → KOKORO_VOICE → af_sky), fail-open when
     the settings store is unreachable.
  2. Catalogue parsing from a real (tiny) NPZ voices bin, keyed by path+mtime.
  3. match_voice mapping ("sky" → af_sky, ambiguous → None).
  4. Skybridge classification of "zoe's voice" / "set your voice to X".
  5. Sidecar phrase-cache keys are voice-scoped (the stale-voice cache bug).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import voice_settings


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    voice_settings.invalidate_cache()
    monkeypatch.setattr(voice_settings, "_catalogue_cache", None)
    yield
    voice_settings.invalidate_cache()


def _make_voices_bin(tmp_path: Path, names: list[str]) -> Path:
    import io
    import zipfile

    path = tmp_path / "voices-test.bin"
    with zipfile.ZipFile(path, "w") as zf:
        for name in names:
            zf.writestr(f"{name}.npy", b"\x93NUMPY-stub")
    return path


# ── resolution order ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_env_default_used_when_no_preference(monkeypatch):
    monkeypatch.setenv("ZOE_KOKORO_VOICE", "af_heart")

    async def _no_pref():
        return None

    monkeypatch.setattr(voice_settings, "_read_persisted_voice", _no_pref)
    assert await voice_settings.get_tts_voice() == "af_heart"


@pytest.mark.asyncio
async def test_fallback_is_af_sky(monkeypatch):
    monkeypatch.delenv("ZOE_KOKORO_VOICE", raising=False)
    monkeypatch.delenv("KOKORO_VOICE", raising=False)

    async def _no_pref():
        return None

    monkeypatch.setattr(voice_settings, "_read_persisted_voice", _no_pref)
    assert await voice_settings.get_tts_voice() == "af_sky"


@pytest.mark.asyncio
async def test_persisted_preference_beats_env(monkeypatch):
    monkeypatch.setenv("ZOE_KOKORO_VOICE", "af_sky")

    async def _pref():
        return "zoe_ember"

    monkeypatch.setattr(voice_settings, "_read_persisted_voice", _pref)
    assert await voice_settings.get_tts_voice() == "zoe_ember"


@pytest.mark.asyncio
async def test_preference_is_cached_within_ttl(monkeypatch):
    calls = {"n": 0}

    async def _pref():
        calls["n"] += 1
        return "af_bella"

    monkeypatch.setattr(voice_settings, "_read_persisted_voice", _pref)
    assert await voice_settings.get_tts_voice() == "af_bella"
    assert await voice_settings.get_tts_voice() == "af_bella"
    assert calls["n"] == 1  # second read served from the TTL cache


@pytest.mark.asyncio
async def test_db_failure_fails_open_to_env_default(monkeypatch):
    """A broken settings store must never break speech."""
    monkeypatch.setenv("ZOE_KOKORO_VOICE", "af_sky")

    # _read_persisted_voice itself swallows errors; simulate via get_db_ctx blowing up.
    import database

    def _broken_ctx():
        raise RuntimeError("pool exhausted")

    monkeypatch.setattr(database, "get_db_ctx", _broken_ctx)
    assert await voice_settings.get_tts_voice() == "af_sky"


@pytest.mark.asyncio
async def test_resolve_override_wins(monkeypatch):
    async def _pref():
        return "af_bella"

    monkeypatch.setattr(voice_settings, "_read_persisted_voice", _pref)
    assert await voice_settings.resolve_tts_voice("zoe_dawn") == "zoe_dawn"
    # Garbage overrides (shell-ish / spaces) are ignored, not passed through.
    assert await voice_settings.resolve_tts_voice("bad voice; rm") == "af_bella"


# ── catalogue ─────────────────────────────────────────────────────────────


def test_list_voices_from_npz_bin(tmp_path, monkeypatch):
    path = _make_voices_bin(tmp_path, ["af_sky", "af_heart", "zoe_ember"])
    monkeypatch.setenv("ZOE_KOKORO_VOICES", str(path))
    assert voice_settings.list_kokoro_voices() == ["af_heart", "af_sky", "zoe_ember"]


def test_list_voices_missing_bin_is_empty(monkeypatch):
    monkeypatch.setenv("ZOE_KOKORO_VOICES", "/nonexistent/voices.bin")
    assert voice_settings.list_kokoro_voices() == []


def test_match_voice(tmp_path, monkeypatch):
    path = _make_voices_bin(tmp_path, ["af_sky", "af_heart", "zoe_ember", "bf_emma"])
    monkeypatch.setenv("ZOE_KOKORO_VOICES", str(path))
    assert voice_settings.match_voice("af_sky") == "af_sky"
    assert voice_settings.match_voice("sky") == "af_sky"
    assert voice_settings.match_voice("Ember") == "zoe_ember"
    assert voice_settings.match_voice("nope") is None
    assert voice_settings.match_voice("") is None


def test_voice_label():
    assert voice_settings.voice_label("af_sky") == "Sky · American female"
    assert voice_settings.voice_label("bm_george") == "George · British male"
    assert voice_settings.voice_label("zoe_ember") == "Ember · Zoe blend"


# ── skybridge classification ──────────────────────────────────────────────


def test_skybridge_classifies_voice_intents():
    from skybridge_service import classify_skybridge_intent

    show = classify_skybridge_intent("show me zoe's voice settings")
    assert show is not None and (show.domain, show.action) == ("voice", "show")

    set_intent = classify_skybridge_intent("set zoe's voice to af_heart")
    assert set_intent is not None
    assert (set_intent.domain, set_intent.action) == ("voice", "set")
    assert set_intent.query == "af_heart"

    spoken = classify_skybridge_intent("Change your voice to ember")
    assert spoken is not None and spoken.query == "ember"

    # Ordinary sentences containing "voice" must fall through to the brain.
    assert classify_skybridge_intent("I lost my voice yesterday") is None


@pytest.mark.asyncio
async def test_skybridge_voice_card_shape(tmp_path, monkeypatch):
    from skybridge_service import SkybridgeIntent, _resolve_voice_settings

    path = _make_voices_bin(tmp_path, ["af_sky", "zoe_ember"])
    monkeypatch.setenv("ZOE_KOKORO_VOICES", str(path))

    async def _pref():
        return "af_sky"

    monkeypatch.setattr(voice_settings, "_read_persisted_voice", _pref)
    result = await _resolve_voice_settings(SkybridgeIntent(domain="voice", action="show"))
    assert result["handled"] is True
    card = result["cards"][0]
    assert card["component"] == "voice_settings"
    props = card["props"]
    assert props["current"] == "af_sky"
    ids = [v["id"] for v in props["voices"]]
    assert ids == ["af_sky", "zoe_ember"]
    assert [v["zoe"] for v in props["voices"]] == [False, True]
    assert props["sample_text"] == voice_settings.PREVIEW_TEXT


# ── sidecar cache keying ──────────────────────────────────────────────────


def test_sidecar_cache_key_is_voice_scoped():
    """The persisted phrase cache must never replay text in the OLD voice."""
    sidecar_path = ROOT.parents[1] / "scripts" / "setup" / "kokoro_sidecar.py"
    src = sidecar_path.read_text("utf-8")
    assert "def _phrase_cache_key(voice: str, text: str)" in src
    assert 'return f"{voice}|{text.strip().lower()}"' in src
    # The /synthesize route must build its key from BOTH voice and text …
    assert "cache_key = _phrase_cache_key(voice, text)" in src
    # … and never from text alone (the pre-fix stale-voice bug).
    assert "cache_key = text.lower()" not in src
    # Warm phrases are keyed under the sidecar's default voice.
    assert "_phrase_cache_key(_VOICE, phrase)" in src
