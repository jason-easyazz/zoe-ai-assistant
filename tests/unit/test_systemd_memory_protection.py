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
* `zoe-data` — the same day, the same finding a third time: `MemoryLow=2G` +
  `CPUWeight`/`IOWeight` 300 were real and live, and existed ONLY as untracked
  local drop-ins, so any rebuild or second host lost them silently. VmRSS 40 MB
  against VmSwap 1056 MB — 96% paged out *with the floor in force*. It hosts
  in-process Moonshine STT, so "just the backend API" is also the voice path.

That is the gap this test closes. It pins the DOCTRINE, not the numbers:
retuning a cap is fine, dropping one is not.

Why the floors keep failing on this box, in one line: every ancestor cgroup
(`user.slice` -> `user-1000.slice` -> `user@1000.service` -> `app.slice`) has
`memory.low=0`, and an effective floor is capped by its ancestors', so a leaf
`MemoryLow` computes to 0 whatever it says. `MemorySwapMax` has no such
propagation — measured across six live units, every one that denies swap holds
0 swap and every one that does not is 96-98% out, regardless of its floor.
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
    "zoe-data.service",
    "flue-zoe-brain.service",
    "flue-zoe-telegram.service",
)

# Exemptions from "swap denied implies a ceiling", each with the reason it is
# safe to skip. A bare name would let the next reader assume it was an
# oversight, so the rationale is DATA here, not a comment: `MemoryMax` is the
# directive that converts a transient spike into an OOM kill, and every
# exemption is a deliberate decision to trade unbounded growth for uptime.
# Every other no-swap unit is bounded userspace and must carry a ceiling —
# denying swap without one leaves the cgroup free to grow into the box.
MEMORY_MAX_EXEMPT = {
    "llama-server.service": (
        "~5.6 GB unified-memory process whose GPU/NvMap pages are not fully "
        "accounted to the cgroup, so a ceiling is both unreliable and lethal: "
        "hard limit + no swap OOM-kills the brain rock on a transient spike."
    ),
    "zoe-data.service": (
        "The whole product surface (chat, voice, panel, Multica poll loop) and "
        "spiky with it: ~1.1 GB steady anon against a 3.16 GB VmHWM measured "
        "2026-07-06, a 3x idle-to-peak spread. Accounting IS complete here (0 "
        "CUDA/NvMap mappings; STT is ONNX Runtime on CPU), so unlike "
        "llama-server a ceiling would genuinely bound it — it is declined on "
        "blast radius, not measurability. A cgroup OOM kill here is a total "
        "outage, where llama-server only loses a lane to fallback."
    ),
}

# Units that must win CPU and disk against the agent fleet. Weights are the only
# priority knob a --user unit actually gets: Nice=-N/OOMScoreAdjust=-N are
# silently dropped (see the elevated-priority test below), so if these go
# missing there is no fallback mechanism keeping the voice path ahead.
PRIORITY_WEIGHTED_UNITS = ("zoe-data.service",)

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
        pytest.skip(f"{unit} is a documented MemoryMax exemption: {MEMORY_MAX_EXEMPT[unit]}")
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


def test_zoe_data_is_actually_covered():
    """Same guard for the API. zoe-data reads as 'just the backend', which is
    why its protection sat in an untracked drop-in for a month — but Moonshine
    STT is an IN-PROCESS singleton, so a swapped zoe-data is a swapped voice
    path. Measured 2026-08-03 under a live MemoryLow=2G: VmRSS 40 MB against
    VmSwap 1056 MB, 96% paged out."""
    assert "zoe-data.service" in NO_SWAP_UNITS, (
        "zoe-data hosts in-process Moonshine STT — it is on the voice path and "
        "must stay covered, not just the API path"
    )


def test_every_memory_max_exemption_states_a_reason():
    """An exemption is a decision to accept unbounded growth for uptime. It only
    stays reviewable if the reason travels with it — an entry added as a bare
    name to make a red test green is the failure this guards."""
    for unit, reason in MEMORY_MAX_EXEMPT.items():
        assert unit in NO_SWAP_UNITS, (
            f"{unit} is exempt from MemoryMax but is not a no-swap unit; the "
            f"exemption only means anything for a unit that denies swap"
        )
        assert reason and len(reason) > 40, (
            f"{unit} is exempt from MemoryMax with no substantive rationale; "
            f"state WHY a ceiling is unsafe or unreliable for it"
        )


@pytest.mark.parametrize("unit", PRIORITY_WEIGHTED_UNITS)
def test_scheduling_weights_are_tracked(unit):
    """The other half of what lived only in zoe-data's untracked drop-in. Memory
    guards keep it resident; the weights keep it SCHEDULED once resident, and a
    rebuild that restored one without the other would look protected and still
    lose the voice path to whatever the agent fleet is doing."""
    directives = _directives(unit)
    for key in ("CPUWeight", "IOWeight"):
        value = directives.get(key)
        assert value is not None, (
            f"{unit} must set {key}; it competes with the agent fleet and "
            f"weights are the only priority knob a --user unit can use"
        )
        assert int(value) > 100, (
            f"{unit} sets {key}={value}, at or below the default of 100 — that "
            f"is not a priority, it is a no-op"
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
