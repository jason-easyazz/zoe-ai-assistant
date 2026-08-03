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

Why the floors keep failing ON THIS HOST (a slice-configuration property, not a
kernel law, and fixable by protecting the ancestor slices): an effective floor is
capped by its ancestors', and `user.slice` -> `user-1000.slice` ->
`user@1000.service` -> `app.slice` all read `memory.low=0`, so a leaf
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

# Units allowed to run a ceiling CLOSE to their floor, with the reason. Normally
# a ceiling must leave real headroom over the protected working set, or normal
# operation runs against the kill threshold and the "ceiling" is really a
# throttle waiting to become an outage. A bounded workload that does not grow
# with load is the legitimate exception.
TIGHT_CEILING_OK = {
    "kokoro-tts.service": (
        "Bounded ~2.3 GB CUDA-resident model that does not grow with load — the "
        "floor is sized to hold the whole working set and the ceiling only has "
        "to sit above it, so 3G/4G is deliberate rather than tight."
    ),
}

# How much room a ceiling must leave above the floor for everything the floor
# does NOT cover. On Node units that is the decisive quantity: V8 sizes its JS
# heap to ~51% of the cgroup ceiling and self-limits gracefully, but external
# memory (Buffers, stream chunks) sits outside that budget and is SIGKILLed by
# the cgroup instead — measured 2026-08-03, exit 137 under MemoryMax=1G.
#
# 3x, not 2x, and the difference is the whole point: the floor is sized to hold
# the working set, so a backstop needs at least TWO more floors above it for
# allocation the floor never covered. flue-zoe-brain's original 512M/1G was
# exactly 2x — it looked like headroom and was the value this rule exists to
# reject, so a threshold of 2 would have ratified the bug instead of catching it.
MIN_CEILING_TO_FLOOR_RATIO = 3

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


def _directives(unit: str, section: str = "Service") -> dict[str, str]:
    """Last-wins map of `Key=value` directives from ONE section.

    Section-aware on purpose. The first version of this parser ignored `[...]`
    headers entirely and merged every section into one map, so a resource
    directive moved to `[Unit]` or `[Install]` — where systemd silently ignores
    it, because these are `[Service]`-only settings — still read as present and
    the whole suite stayed green while the protection was actually gone. That is
    the same "documents a guarantee that does not exist" failure as the
    silently-dropped `Nice=-N`, so it is parsed, not assumed.
    """
    path = UNIT_DIR / unit
    assert path.exists(), f"{unit} is pinned by this test but missing from {UNIT_DIR}"
    out: dict[str, str] = {}
    current: str | None = None
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1].strip()
            continue
        if current != section:
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


def test_ceiling_leaves_headroom_over_the_floor(unit):
    """A ceiling barely above the floor is a kill threshold sitting inside the
    normal operating range. This is the 2026-08-03 red-team finding: both flue
    ceilings had been sized as a multiple of a VmHWM sampled on a STARVED box —
    a lower bound on the real peak — which is the wrong quantity entirely. What
    decides throttle-vs-kill is what the runtime allocates OUTSIDE the floor's
    protection: V8 self-limits its JS heap to ~51% of the ceiling, but Buffers
    and stream chunks are external memory and get SIGKILLed (exit 137, measured)
    instead. Ceilings are backstops against a leak, not working limits."""
    directives = _directives(unit)
    if "MemoryMax" not in directives:
        pytest.skip(f"{unit} has no MemoryMax")
    if unit in TIGHT_CEILING_OK:
        pytest.skip(f"{unit} is a documented tight-ceiling exception: {TIGHT_CEILING_OK[unit]}")
    low = _parse_size(directives["MemoryLow"])
    ceiling = _parse_size(directives["MemoryMax"])
    assert ceiling >= MIN_CEILING_TO_FLOOR_RATIO * low, (
        f"{unit} has MemoryMax={directives['MemoryMax']} against "
        f"MemoryLow={directives['MemoryLow']} — under "
        f"{MIN_CEILING_TO_FLOOR_RATIO}x the floor, so a normal spike in memory "
        f"the floor does not cover (external buffers, stream chunks) hits the "
        f"OOM killer rather than a throttle"
    )


def test_the_parser_only_reads_the_service_section(tmp_path, monkeypatch):
    """Guards the parser itself. `MemorySwapMax` in `[Unit]` or `[Install]` is
    silently ignored by systemd, so a parser that merges sections would report
    full protection for a unit that has none — every other test in this file
    rests on this being right."""
    unit = tmp_path / "section-probe.service"
    unit.write_text(
        "[Unit]\n"
        "Description=probe\n"
        "MemorySwapMax=0\n"          # ignored by systemd — wrong section
        "\n"
        "[Service]\n"
        "ExecStart=/bin/true\n"
        "MemoryLow=512M\n"
        "\n"
        "[Install]\n"
        "MemoryMax=1G\n"             # ignored by systemd — wrong section
        "WantedBy=default.target\n"
    )
    monkeypatch.setitem(globals(), "UNIT_DIR", tmp_path)
    service = _directives("section-probe.service")
    assert "MemoryLow" in service, "a real [Service] directive must be read"
    assert "MemorySwapMax" not in service, (
        "MemorySwapMax was declared in [Unit], where systemd ignores it — the "
        "parser must not report it as protection"
    )
    assert "MemoryMax" not in service, (
        "MemoryMax was declared in [Install], where systemd ignores it"
    )
    assert _directives("section-probe.service", section="Unit")["MemorySwapMax"] == "0"


def test_every_tight_ceiling_exception_states_a_reason():
    """Same discipline as MEMORY_MAX_EXEMPT: an exception added as a bare name
    to silence a red test is exactly the failure being guarded against."""
    for unit, reason in TIGHT_CEILING_OK.items():
        assert unit in NO_SWAP_UNITS, (
            f"{unit} is listed as a tight-ceiling exception but is not a "
            f"no-swap unit; the exception has no meaning there"
        )
        assert reason and len(reason) > 40, (
            f"{unit} claims a tight-ceiling exception with no substantive "
            f"rationale; state why its workload cannot grow into the ceiling"
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
