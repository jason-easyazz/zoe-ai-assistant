"""LiveKit compose hygiene (A10, 2026-10-18). LiveKit is ALIVE on demand (zoe-data starts it per session, reaps it after 300 s
idle), so its block is load-bearing: an exact version + digest (it was ``:latest``), a memory cap with no swap headroom (it had
none), capped json-file logs. A static read of ``docker-compose.yml``; nothing is started."""
import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.ci_safe

LIVEKIT = yaml.safe_load((Path(__file__).resolve().parents[2] / "docker-compose.yml").read_text())["services"]["livekit"]
PINNED = re.compile(r"^livekit/livekit-server:v\d+\.\d+\.\d+@sha256:[0-9a-f]{64}$")


def _bytes(v) -> int:
    m = re.fullmatch(r"(\d+)([kmg]?)b?", str(v).strip().lower())
    return int(m.group(1)) * {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30}[m.group(2)]


def test_the_image_is_pinned_to_a_version_and_a_digest():
    assert PINNED.match(LIVEKIT["image"]), LIVEKIT["image"]
    for floating in ("livekit/livekit-server:latest", "livekit/livekit-server:v1.9.3", "livekit/livekit-server@sha256:" + "0" * 64):
        assert not PINNED.match(floating), floating  # control: the check can fail


def test_memory_is_capped_without_swap_and_logs_are_capped():
    cap = _bytes(LIVEKIT["mem_limit"])
    assert 128 << 20 <= cap <= 1 << 30, cap  # measured idle ~19 MiB; outside this band is a typo, not a decision
    assert _bytes(LIVEKIT["memswap_limit"]) == cap
    assert LIVEKIT["logging"]["driver"] == "json-file"
    assert {"max-size", "max-file"} <= set(LIVEKIT["logging"]["options"])
