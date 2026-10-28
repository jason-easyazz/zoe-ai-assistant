"""Guard: the Home Assistant image is a REAL pin (tag@sha256), not a floating :stable.

Until 2026-10-28 the compose line was `home-assistant:stable  # pinned: sha256:...` -- the
digest lived only in a comment, so `docker compose pull`/`up` could jump HA a month at a
time (HA migrates .storage forward on first boot; there is no downgrade). A digest ref
makes every HA image change a reviewed commit. Static: parses compose, no Docker/network.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

pytestmark = pytest.mark.ci_safe

COMPOSE = Path(__file__).resolve().parents[2] / "docker-compose.yml"
REF = re.compile(
    r"^ghcr\.io/home-assistant/home-assistant:(?P<tag>\d{4}\.\d{1,2}\.\d+)@sha256:(?P<digest>[0-9a-f]{64})$"
)


def test_homeassistant_image_is_tag_plus_digest():
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    image = data["services"]["homeassistant"]["image"]
    assert REF.match(image), f"HA image must be <version>@sha256:<64 hex>, got {image!r}"
