"""The HA MCP bridge image is built from exact, patched pins on a digest-pinned base.

Its requirements were unbounded ``>=`` floors, so the image froze whatever PyPI served on
build day (2025-11-13): starlette 0.49.3 (5 advisories), anyio 4.11.0 (GHSA-82r6-8w77-94w6,
critical), idna 3.11, click 8.3.0. This pins the manifest shape (every line ``==``) and the
security floors, so a later edit cannot quietly reopen them. The advisory-by-advisory map
lives in the header of ``services/homeassistant-mcp-bridge/requirements.txt``.

Negative controls (2026-09-27): ``starlette==1.6.0`` -> ``starlette>=0.40`` and
-> ``starlette==1.3.0``; ``anyio==4.15.1`` -> ``anyio==4.11.0``; Dockerfile ``FROM`` without
``@sha256:`` — each turned this file red.

Stdlib only (no ``packaging``), so it collects in the slim GitHub lane.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

BRIDGE = Path(__file__).resolve().parents[2] / "services" / "homeassistant-mcp-bridge"

# package -> first version free of every advisory listed in requirements.txt
SECURITY_FLOORS = {
    "starlette": (1, 3, 1),  # GHSA-82w8-qh3p-5jfq (+ -86qp, -x746, -wqp7, -jp82)
    "anyio": (4, 14, 2),     # GHSA-82r6-8w77-94w6, GHSA-5p39-cfhj-2xmp
    "idna": (3, 15),         # GHSA-65pc-fj4g-8rjx
    "click": (8, 3, 3),      # PYSEC-2026-2132 / CVE-2026-7246
}


def _pins() -> dict[str, str]:
    pins = {}
    for raw in (BRIDGE / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.fullmatch(r"([A-Za-z0-9_.\-]+)==([0-9][0-9A-Za-z.]*)", line)
        assert m, f"requirements.txt line is not an exact `name==version` pin: {raw!r}"
        pins[m.group(1).lower().replace("_", "-")] = m.group(2)
    return pins


def _vtuple(v: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", v))


def test_every_requirement_is_an_exact_pin():
    pins = _pins()
    for name in ("fastapi", "uvicorn", "pydantic", "httpx"):
        assert name in pins, f"{name} (a direct import of main.py) must be pinned"


@pytest.mark.parametrize("name,floor", sorted(SECURITY_FLOORS.items()))
def test_security_floor_holds(name, floor):
    pins = _pins()
    assert name in pins, f"{name} must be pinned explicitly (security floor {floor})"
    assert _vtuple(pins[name]) >= floor, (
        f"{name}=={pins[name]} is below the patched floor {'.'.join(map(str, floor))}"
    )


def test_base_image_is_digest_pinned():
    froms = [
        line.split()[1]
        for line in (BRIDGE / "Dockerfile").read_text(encoding="utf-8").splitlines()
        if line.strip().upper().startswith("FROM ")
    ]
    assert froms, "Dockerfile has no FROM line"
    for ref in froms:
        assert re.fullmatch(r"python:3\.11\.\d+-slim-bookworm@sha256:[0-9a-f]{64}", ref), (
            f"bridge base image must be a patch-version python tag pinned by digest: {ref!r}"
        )
