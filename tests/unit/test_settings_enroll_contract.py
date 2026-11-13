"""Settings > Speaker Identity: the page tells the truth about where voices are learned.

Voice profiles are built at a wall panel ("Zoe, learn my voice"); ``POST /api/voice/enroll`` refuses every audio upload
since the browser-side speaker model was retired. The page used to record WAV in the browser, ask for three samples and
name the retired model, so every attempt ended in an error. This pins the replacement:

1. No browser recorder and no upload: no ``/api/voice/enroll`` call, no ``getUserMedia``, no WAV encoder.
2. The retired model is not named anywhere on the page.
3. The page says where enrolment happens (a panel, by voice).
4. The profile list still renders the server's fields (``id`` / ``display_name``) and each row can be forgotten
   through ``DELETE /api/voice/profiles/{id}``.

Static text assertions over the shipped HTML - no browser, no network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe  # GitHub-CI opt-in: runs in validate.yml's `-m ci_safe` lane

_SETTINGS = Path(__file__).resolve().parents[2] / "services" / "zoe-ui" / "dist" / "settings.html"
HTML = _SETTINGS.read_text(encoding="utf-8")


def test_no_browser_enrolment_upload():
    assert "/api/voice/enroll" not in HTML
    assert "getUserMedia" not in HTML
    assert "function encodeWav(" not in HTML
    assert 'id="record-btn"' not in HTML


def test_retired_speaker_model_is_not_named():
    assert "resemblyzer" not in HTML.lower()
    assert "Repeat 3 times" not in HTML


def test_page_says_enrolment_happens_at_a_panel():
    assert "learn my voice" in HTML
    assert "at a panel" in HTML


def test_profile_list_uses_server_field_names_and_can_forget():
    m = re.search(r"async function loadVoiceProfiles\(\).*?\n}", HTML, re.S)
    assert m, "loadVoiceProfiles() missing"
    block = m.group(0)
    assert "/api/voice/profiles" in block
    assert "p.display_name" in block and "p.id" in block
    assert "p.profile_id" not in block and "p.label" not in block
    d = re.search(r"async function deleteVoiceProfile\(.*?\n}", HTML, re.S)
    assert d and "'DELETE', `/api/voice/profiles/${id}`" in d.group(0)
