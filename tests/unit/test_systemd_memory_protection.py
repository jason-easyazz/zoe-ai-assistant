"""Pins the cgroup memory protection on the latency-critical user units.

The 2026-07-19 fix (#1409) gave llama-server and kokoro-tts `MemorySwapMax=0`
+ `MemoryLow` because a swapped voice stack does not fail, it just gets slow in
a way that reads as a product bug. The fix was applied per-unit by hand, so
nothing stopped a NEW latency-critical unit from shipping uncapped — and two
did. Measured on the live Orin 2026-08-03, both `infinity`/`infinity`:

* `flue-zoe-brain` — the TOP brain lane under ZOE_BRAIN_BACKEND=flue
  (flue > core > legacy) — VmRSS 2.0 MB against VmSwap 70.4 MB, 87% paged out,
  despite a MemoryLow=512M drop-in that had been live since 2026-07-09.
* `flue-zoe-telegram` — no memory directives at all, cgroup `memory.low` 0,
  VmRSS 6.1 MB against VmSwap 65.2 MB.

That is the gap this test closes. It pins the DOCTRINE, not the numbers:
retuning a cap is fine, dropping one is not.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
UNIT_DIR = ROOT / "scripts" / "setup" / "systemd"

# Units whose working set must never be paged out. Adding a latency-critical
# user unit means adding it here.
NO_SWAP_UNITS = (
    "llama-server.service",
    "kokoro-tts.service",
    "flue-zoe-brain.service",
    "flue-zoe-telegram.service",
)

# llama-server is the ONE documented exemption from needing a ceiling: it is a
# ~5.6 GB unified-memory process, and a hard ceiling *plus* no swap turns a
# transient spike into an OOM kill of the brain rock. Every other no-swap unit
# is bounded userspace, so it must carry a ceiling — denying swap without one
# leaves the cgroup free to grow into the box.
MEMORY_MAX_EXEMPT = {"llama-server.service"}

_SIZE = re.compile(r"^(\d+)([KMGT]?)$")
_SCALE = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def _parse_size(value: str) -> int:
    match = _SIZE.match(value.strip())
    assert match, f"unparseable systemd size {value!r}"
    return int(match.group(1)) * _SCALE[match.group(2)]


def _directives(unit: str) -> dict[str, str]:
    """Last-wins map of `Key=value` directives, comments and blanks dropped."""
    path = UNIT_DIR / unit
    assert path.exists(), f"{unit} is pinned by this test but missing from {UNIT_DIR}"
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";", "[")):
            continue
        key, sep, value = line.partition("=")
        if sep:
            out[key.strip()] = value.strip()
    return out


@pytest.fixture(params=NO_SWAP_UNITS)
def unit(request) -> str:
    return request.param


def test_swap_is_denied(unit):
    """`MemoryLow` is reclaim resistance, never swap immunity — measured on the
    live box, flue-zoe-brain sat 87% in swap under MemoryLow=512M. Only
    MemorySwapMax=0 holds the line."""
    directives = _directives(unit)
    assert directives.get("MemorySwapMax") == "0", (
        f"{unit} must set MemorySwapMax=0; its working set is latency-critical "
        f"and must never be paged out"
    )


def test_reclaim_floor_is_set(unit):
    """A unit with no memory directives gets cgroup `memory.low` 0 — the kernel
    reclaims it FIRST. That was kokoro-tts before #1409 and flue-zoe-telegram
    until 2026-08-03."""
    directives = _directives(unit)
    assert "MemoryLow" in directives, (
        f"{unit} must set MemoryLow, otherwise its cgroup memory.low is 0 and "
        f"it is the kernel's first reclaim target"
    )


def test_denying_swap_is_paired_with_a_ceiling(unit):
    """Swap denied + no ceiling = a cgroup free to grow into the box, with no
    release valve. Bounded userspace units must carry MemoryMax."""
    if unit in MEMORY_MAX_EXEMPT:
        pytest.skip(f"{unit} is the documented MemoryMax exemption (see module docstring)")
    directives = _directives(unit)
    assert "MemoryMax" in directives, (
        f"{unit} sets MemorySwapMax=0, so it must also set MemoryMax; without a "
        f"ceiling, refusing swap turns into unbounded pressure"
    )


def test_floor_sits_below_the_ceiling(unit):
    """A MemoryLow above MemoryMax is a protection zone the unit can never
    reach — it reads as protective and guarantees nothing."""
    directives = _directives(unit)
    if "MemoryMax" not in directives:
        pytest.skip(f"{unit} has no MemoryMax")
    low = _parse_size(directives["MemoryLow"])
    ceiling = _parse_size(directives["MemoryMax"])
    assert low < ceiling, (
        f"{unit} has MemoryLow={directives['MemoryLow']} >= "
        f"MemoryMax={directives['MemoryMax']}"
    )


def test_the_flue_sidecars_are_actually_covered():
    """Guards the guard: the 2026-08-03 gap was a latency-critical unit simply
    not being in anyone's list. Removing one from NO_SWAP_UNITS must fail here
    rather than silently shrinking the test matrix."""
    for unit in ("flue-zoe-brain.service", "flue-zoe-telegram.service"):
        assert unit in NO_SWAP_UNITS, (
            f"{unit} is on the live brain/alerting path and must stay covered"
        )


@pytest.mark.parametrize("unit_path", sorted(UNIT_DIR.glob("*.service")))
def test_no_user_unit_asks_for_elevated_priority(unit_path):
    """A `--user` unit CANNOT raise priority on this box (`ulimit -e` is 0).
    systemd accepts the directive, the service starts, status is success — and
    the value is silently dropped. Writing one documents a guarantee that does
    not exist."""
    directives = _directives(unit_path.name)
    for key in ("Nice", "OOMScoreAdjust"):
        value = directives.get(key)
        if value is None:
            continue
        assert not value.startswith("-"), (
            f"{unit_path.name} sets {key}={value}; negative values are SILENTLY "
            f"DROPPED on a --user unit. De-prioritise the other side instead."
        )
