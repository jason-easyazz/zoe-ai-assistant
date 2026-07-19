"""Settings-page voice-enroll request-shape contract.

POST /api/voice/enroll (`services/zoe-data/routers/voice_tts.py::voice_enroll`)
accepts JSON ``{"audio_base64": <16-bit PCM WAV b64>, "consent": bool}`` and
decodes with resemblyzer's ``preprocess_wav`` — real WAV only. The settings
page originally recorded webm/opus via MediaRecorder and POSTed multipart
FormData, so browser enrollment always failed. This pins the fixed contract:

1. The enroll upload sends JSON with ``audio_base64`` and ``consent`` —
   never FormData/webm.
2. Audio is captured as PCM via WebAudio and encoded to a RIFF/WAVE header
   client-side (``encodeWav``), not MediaRecorder.
3. The profile list renders the fields the server actually returns
   (``id``/``display_name``/``sample_count``), not the legacy
   ``profile_id``/``label`` shape.
4. Consent is explicit: a visible checkbox gates recording.

Static text assertions over the shipped HTML — no browser, no network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

_SETTINGS = Path(__file__).resolve().parents[2] / "services" / "zoe-ui" / "dist" / "settings.html"
HTML = _SETTINGS.read_text(encoding="utf-8")


def _enroll_fetch_block() -> str:
    m = re.search(r"async function uploadEnrollment\(\).*\n}", HTML, re.S)
    assert m, "uploadEnrollment() missing from settings.html"
    return m.group(0)


def test_enroll_posts_json_with_audio_base64_and_consent():
    block = _enroll_fetch_block()
    assert "/api/voice/enroll" in block
    assert "'Content-Type': 'application/json'" in block
    assert "audio_base64" in block
    assert "consent" in block


def test_enroll_never_sends_formdata_or_webm():
    block = _enroll_fetch_block()
    assert "FormData" not in block
    assert "webm" not in block


def test_audio_is_client_side_wav_not_mediarecorder():
    assert "new MediaRecorder(" not in HTML, "webm/opus recording cannot feed preprocess_wav"
    assert "function encodeWav(" in HTML
    # RIFF/WAVE header actually written
    assert "'RIFF'" in HTML and "'WAVE'" in HTML and "'fmt '" in HTML


def test_profile_list_uses_server_field_names():
    m = re.search(r"async function loadVoiceProfiles\(\).*?\n}", HTML, re.S)
    assert m, "loadVoiceProfiles() missing"
    block = m.group(0)
    assert "p.display_name" in block and "p.id" in block
    assert "p.profile_id" not in block and "p.label" not in block


def test_consent_checkbox_gates_recording():
    assert 'id="enroll-consent"' in HTML
    m = re.search(r"async function startRecording\(\).*?getUserMedia", HTML, re.S)
    assert m and "enroll-consent" in m.group(0), "recording must check consent first"
