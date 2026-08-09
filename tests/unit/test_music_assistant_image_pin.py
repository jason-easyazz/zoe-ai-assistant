"""Guards that the Music Assistant image stays digest-pinned.

The MA image is not just a music server to us -- it is where the LIVE YouTube
path's JavaScript engine comes from. YouTube signs stream URLs with an
obfuscated `n`/`sig` parameter recoverable only by executing YouTube's player
JS; yt-dlp does that through the yt_dlp_ejs solver, which needs a JS runtime.
MA supplies one (ytmusic manifest: `deno==2.7.4`, baked at /app/venv/bin/deno).

The failure mode this guards is SILENT, which is the whole point. With no JS
runtime yt-dlp raises no dependency error -- it falls back to player clients
that serve pre-signed URLs (ANDROID_VR today) and everything looks fine, right
up until YouTube withdraws that fallback. On a floating `:stable` tag an MA
image that dropped deno would land on the box with no diff in this repo to
review. A digest pin makes every image change a reviewed commit, and the bump
procedure (in the compose comment) requires
`scripts/maintenance/music_jsruntime_probe.sh` green before merge.

This test is deliberately STATIC -- it parses the compose file and needs no
Docker, no network and no live container, so it runs anywhere CI runs. The live
behaviour is covered by the probe script, which cannot run in CI.

`pytest.importorskip("yaml")` rather than a hard import, matching
test_potoken_loopback_bind.py: PyYAML reaches the slim GitHub lane only
transitively, and per tests/AGENTS.md a tests/unit module must at least COLLECT
under the slim dep list. The Jetson catch-all lane runs this directory
unconditionally, so the guard always runs.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
COMPOSE = REPO / "docker-compose.modules.yml"
PROBE = REPO / "scripts" / "maintenance" / "music_jsruntime_probe.sh"
RUNBOOK = REPO / "docs" / "knowledge" / "music-ytdlp-js-runtime.md"

# `name@sha256:<64 hex>` -- a digest reference, not a mutable tag.
DIGEST_REF = re.compile(r"^(?P<name>[^@:\s]+(?::[^@\s]+)?)@sha256:(?P<digest>[0-9a-f]{64})$")


def _music_assistant_service() -> dict:
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8")) or {}
    services = data.get("services") or {}
    assert "music-assistant" in services, (
        "music-assistant service missing from docker-compose.modules.yml"
    )
    return services["music-assistant"]


def test_music_assistant_image_is_digest_pinned():
    image = str(_music_assistant_service().get("image", ""))
    assert image, "music-assistant has no image reference"

    match = DIGEST_REF.match(image)
    assert match, (
        f"music-assistant image {image!r} is not digest-pinned.\n"
        "A floating tag (:stable, :latest) lets an upstream image change land on "
        "the box with no diff to review -- including one that drops the deno "
        "binary the YouTube nsig solver depends on, which fails SILENTLY "
        "(yt-dlp falls back to pre-signed-URL clients until YouTube withdraws "
        "them). Pin as ghcr.io/music-assistant/server@sha256:<64 hex> and see "
        "the bump procedure in the compose comment."
    )
    # EXACT equality, not startswith: the prefix form would also accept
    # `ghcr.io/music-assistant/server-evil@sha256:...`, which is a different
    # repository entirely and carries none of the guarantees this pin asserts
    # (cross-review, #1635). The digest binds the CONTENT; this binds the SOURCE.
    assert match.group("name") == "ghcr.io/music-assistant/server", (
        f"unexpected music-assistant image source {match.group('name')!r} -- "
        "the JS-runtime guarantee is a property of the upstream MA image"
    )


def test_music_assistant_pin_documents_the_bump_procedure():
    """A pin with no bump procedure rots into an un-updatable image.

    The comment must keep pointing at the probe, so whoever bumps the digest is
    told how to prove the new image still has a working JS engine.
    """
    text = COMPOSE.read_text(encoding="utf-8")
    assert "music_jsruntime_probe.sh" in text, (
        "the music-assistant pin must reference "
        "scripts/maintenance/music_jsruntime_probe.sh so a digest bump is "
        "verified against a real JS-challenge solve before it merges"
    )
    assert "music-ytdlp-js-runtime.md" in text, (
        "the music-assistant pin must reference the runbook "
        "docs/knowledge/music-ytdlp-js-runtime.md (it carries the YouTube Music "
        "re-auth risk that applies whenever MA is restarted)"
    )


def test_referenced_probe_and_runbook_exist():
    """The pointers above are only useful if they resolve."""
    assert PROBE.is_file(), f"missing probe script: {PROBE}"
    assert RUNBOOK.is_file(), f"missing runbook: {RUNBOOK}"


def test_potoken_companion_still_present():
    """MA's ytmusic login needs the PO-token generator alongside it.

    Pinning MA invites someone to prune 'unused' services in this file. The
    generator is not optional: if it is down, MA's ytmusic login fails
    (services/zoe-data/AGENTS.md), and the live -v trace shows yt-dlp minting a
    gvs PO token from it on the web client.
    """
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8")) or {}
    assert "ytmusic-potoken" in (data.get("services") or {}), (
        "ytmusic-potoken disappeared from docker-compose.modules.yml -- MA's "
        "YouTube Music provider cannot log in without it"
    )
