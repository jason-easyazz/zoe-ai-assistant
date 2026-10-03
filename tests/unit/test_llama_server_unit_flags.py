"""Pins the brain unit's llama.cpp serving flags against the build it targets (B0.4).

The template runs llama.cpp b11194 (``%h/llama.cpp-b11194/build-jetson``) with
FlashAttention ON and a q8_0 K+V cache, replay-gated 2026-09-27. Three couplings
break the brain at STARTUP rather than in any test, so they are pinned here:

* A quantized V cache requires FlashAttention — ``--cache-type-v q8_0`` with
  ``--flash-attn off`` throws in llama-context.cpp. A half-done rollback (FA off,
  V still q8_0) must go red here, not crash-loop the brain.
* b11194 REMOVED ``--mlock`` (now ``--load-mode mmap+mlock``) and deprecated
  ``--chat-template-kwargs '{"enable_thinking":false}'`` (now ``--reasoning off``).
  An old spelling on the new binary is an unknown-flag startup failure.
* draft-MTP with ``--parallel`` > 1 leaks content between concurrent requests
  (upstream ggml-org/llama.cpp#28286, OPEN) with no garbage-token signature, so a
  draft-mtp unit must run ONE slot. And ``--fit`` defaults ON, so ``--fit off``
  must be explicit for the written config to be the served config.
* ExecStart and LD_LIBRARY_PATH must name the SAME build — mixing b9733 libs
  with the b11194 binary (or vice versa) is an ABI mismatch.

B6.6 (2026-09-27, docs/knowledge/brain-flags-tuning-2026-09.md) adds two sizing
couplings that fail at RUNTIME, not startup:

* The per-slot context (``--ctx-size`` / ``--parallel``) must be at least the
  window the Flue brain client budgets for (``DEFAULT_CONTEXT_WINDOW_TOKENS`` in
  ``labs/flue-zoe-brain-2x/src/context-window.ts``). Below it, a long session
  the client considers in-budget is refused by the server — every such turn fails.
* zoe-core's ``local-gemma`` Pi provider must not declare more context than
  one slot holds (``provider-local-gemma.ts`` defaults): Pi compacts against the
  declared window, so an oversized declaration lets sessions grow until the
  server refuses them. Its compaction thresholds (``services/zoe-core/.pi/
  settings.json``) must fit that window, and the RPC spawn must ``--approve``
  the project so Pi actually loads them.
* ``--cache-ram`` must stay a positive cap. ``0`` disables the host prompt cache,
  and with one slot every chat turn then re-prefills its whole prompt (+4.1 s
  TTFT measured); ``-1`` is "no limit" on 15.6G unified memory.

The FunctionGemma router sidecar (``functiongemma-router.service``, same llama.cpp
server, CPU-only) carries couplings of its own: its ``--cache-ram`` must be a
positive cap that fits under the unit's ``MemoryMax`` (left at the 8192 MiB default
inside a ~1G cgroup, the cache never evicts — the cgroup OOM-kills the sidecar
first). And since A1 (infra audit 2026-10-03) it MLOCKS its GGUF: the lock flag must
be spelled for the build it runs, ``LimitMEMLOCK`` must not cap it, and the ceiling
must hold the floor plus the now-unreclaimable weights.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "scripts" / "setup" / "systemd" / "llama-server.service"


def _exec_start(unit: Path = UNIT) -> str:
    text = unit.read_text(encoding="utf-8")
    m = re.search(r"^ExecStart=(.*?)(?=\n[A-Z][A-Za-z]*=|\n\[|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, f"{unit.name} has no ExecStart block"
    # Drop comment lines and line continuations -> one command line.
    lines = [ln for ln in m.group(1).splitlines() if not ln.lstrip().startswith("#")]
    return " ".join(ln.rstrip("\\").strip() for ln in lines)


def _flag(cmd: str, name: str) -> str | None:
    m = re.search(rf"(?:^|\s){re.escape(name)}\s+(\S+)", cmd)
    return m.group(1) if m else None


def test_quantized_v_cache_requires_flash_attn_on():
    cmd = _exec_start()
    v_type = _flag(cmd, "--cache-type-v") or "f16"
    fa = _flag(cmd, "--flash-attn")
    if v_type not in {"f16", "f32", "bf16"}:
        assert fa == "on", (
            f"--cache-type-v {v_type} needs --flash-attn on (got {fa!r}); a quantized "
            "V cache with FA off throws at startup. Rollback = drop --cache-type-v too."
        )


def test_adopted_b0_4_config():
    cmd = _exec_start()
    assert _flag(cmd, "--flash-attn") == "on"
    assert _flag(cmd, "--cache-type-k") == "q8_0"
    assert _flag(cmd, "--cache-type-v") == "q8_0"
    assert _flag(cmd, "--spec-type") == "draft-mtp", "FA-on was replay-gated WITH the MTP drafter"


def test_no_flags_removed_or_deprecated_in_b11194():
    cmd = _exec_start()
    assert not re.search(r"(?:^|\s)--mlock(?:\s|$)", cmd), "--mlock was removed; use --load-mode mmap+mlock"
    assert _flag(cmd, "--load-mode") == "mmap+mlock"
    assert "--chat-template-kwargs" not in cmd, "enable_thinking kwargs are deprecated; use --reasoning off"
    assert _flag(cmd, "--reasoning") == "off"


def test_binary_and_libs_come_from_the_same_build():
    text = UNIT.read_text(encoding="utf-8")
    lib = re.search(r"^Environment=LD_LIBRARY_PATH=(\S+)", text, re.MULTILINE)
    assert lib, "llama-server.service lost its LD_LIBRARY_PATH"
    binary = _exec_start().split()[0]
    assert binary == f"{lib.group(1).rstrip('/')}/llama-server", (
        f"ExecStart binary {binary} and LD_LIBRARY_PATH {lib.group(1)} name different builds"
    )


def test_draft_mtp_implies_single_slot():
    cmd = _exec_start()
    if _flag(cmd, "--spec-type") == "draft-mtp":
        assert _flag(cmd, "--parallel") == "1", (
            f"--spec-type draft-mtp with --parallel {_flag(cmd, '--parallel')!r}: upstream "
            "llama.cpp#28286 leaks content between concurrent requests. Keep --parallel 1."
        )


def test_fit_is_explicitly_off():
    assert _flag(_exec_start(), "--fit") == "off", "--fit defaults ON; the B0.4 gate keeps it off explicitly"


FLUE_WINDOW = ROOT / "labs" / "flue-zoe-brain-2x" / "src" / "context-window.ts"


def _flue_default_window() -> int:
    m = re.search(r"DEFAULT_CONTEXT_WINDOW_TOKENS\s*=\s*([0-9_]+)", FLUE_WINDOW.read_text(encoding="utf-8"))
    assert m, "context-window.ts lost DEFAULT_CONTEXT_WINDOW_TOKENS — re-point this pin"
    return int(m.group(1).replace("_", ""))


def test_slot_context_covers_the_flue_brain_window():
    cmd = _exec_start()
    ctx = int(_flag(cmd, "--ctx-size") or 0)
    parallel = int(_flag(cmd, "--parallel") or 1)
    window = _flue_default_window()
    assert ctx // parallel >= window, (
        f"per-slot context {ctx}//{parallel} = {ctx // parallel} is below the Flue brain's "
        f"{window}-token window: turns the client thinks fit would be refused by llama-server"
    )


def test_adopted_b6_6_context_size():
    # 8192 = the Flue window exactly; measured p99 prompt+reply 3280 tokens (B6.6).
    assert _flag(_exec_start(), "--ctx-size") == "8192"


def test_host_prompt_cache_is_a_positive_cap():
    cram = _flag(_exec_start(), "--cache-ram")
    assert cram is not None, "--cache-ram missing: the llama.cpp default is 8192 MiB (OOM hazard here)"
    assert int(cram) > 0, (
        f"--cache-ram {cram}: 0 disables the host prompt cache (one slot -> full re-prefill on "
        "every chat turn, +4.1 s TTFT measured) and -1 is unbounded on unified memory"
    )


CORE_PROVIDER = ROOT / "services" / "zoe-core" / "extensions" / "provider-local-gemma.ts"


def _core_provider_default(env_name: str) -> int:
    m = re.search(
        rf"Number\(process\.env\.{env_name}\)\s*\|\|\s*([0-9_]+)", CORE_PROVIDER.read_text(encoding="utf-8")
    )
    assert m, f"provider-local-gemma.ts lost its {env_name} default — re-point this pin"
    return int(m.group(1).replace("_", ""))


def test_core_provider_context_fits_one_slot():
    cmd = _exec_start()
    per_slot = int(_flag(cmd, "--ctx-size") or 0) // int(_flag(cmd, "--parallel") or 1)
    ctx = _core_provider_default("ZOE_CORE_MODEL_CONTEXT")
    max_out = _core_provider_default("ZOE_CORE_MODEL_MAXTOKENS")
    assert ctx <= per_slot, (
        f"zoe-core local-gemma declares a {ctx}-token context but llama-server serves {per_slot} per slot: "
        "Pi would not compact until the server already refuses the session"
    )
    # Half the window for the reply at most: measured p99 prompts are ~3.3k tokens
    # (B6.6), so 3.3k + a full reply must still fit. 2048 of 8192 does.
    assert 0 < max_out <= ctx // 2, f"max output {max_out} leaves too little of the {ctx} window for the prompt"


CORE_PI_SETTINGS = ROOT / "services" / "zoe-core" / ".pi" / "settings.json"
# Headroom for zoe-core's system prompt + tool schemas that survive compaction.
_CORE_FIXED_PROMPT_HEADROOM = 2048


def test_core_pi_compaction_fits_one_slot():
    """Pi 0.82.1 defaults (reserve 16384 / keep 20000) assume a huge window: with an
    8192 contextWindow, shouldCompact() compares usage to 8192 - 16384 (< 0) and
    compaction can never make room. zoe-core must ship its own values."""
    import json

    settings = json.loads(CORE_PI_SETTINGS.read_text(encoding="utf-8"))
    comp = settings["compaction"]
    reserve, keep = int(comp["reserveTokens"]), int(comp["keepRecentTokens"])
    ctx = _core_provider_default("ZOE_CORE_MODEL_CONTEXT")
    max_out = _core_provider_default("ZOE_CORE_MODEL_MAXTOKENS")
    assert comp.get("enabled", True) is True
    assert reserve + keep < ctx, f"reserve {reserve} + keepRecent {keep} >= context {ctx}"
    assert reserve >= max_out, "compaction must trigger early enough to leave room for a full reply"
    # After compaction: fixed prompt + summary (Pi caps it at 0.8 x reserve) + kept
    # recent turns must sit BELOW the trigger (ctx - reserve), or it re-compacts forever.
    assert _CORE_FIXED_PROMPT_HEADROOM + int(0.8 * reserve) + keep <= ctx - reserve
    assert int(settings["branchSummary"]["reserveTokens"]) < ctx


def test_core_rpc_spawn_trusts_the_project_settings():
    # A project .pi/settings.json is a trust-requiring resource in Pi 0.82.1; a
    # non-interactive RPC spawn without --approve would ignore it.
    src = (ROOT / "services" / "zoe-data" / "zoe_core_client.py").read_text(encoding="utf-8")
    body = src[src.index("def _rpc_command"): src.index("def _data_url")]
    assert '"--approve"' in body


ROUTER_UNIT = ROOT / "scripts" / "setup" / "systemd" / "functiongemma-router.service"


def _memory_max_mib(unit: Path) -> int:
    m = re.search(r"^MemoryMax=([0-9]+)([KMG])$", unit.read_text(encoding="utf-8"), re.MULTILINE)
    assert m, f"{unit.name} lost its numeric MemoryMax — re-point this pin"
    return int(m.group(1)) * {"K": 1 / 1024, "M": 1, "G": 1024}[m.group(2)]


def test_router_prompt_cache_fits_under_its_cgroup_ceiling():
    cmd = _exec_start(ROUTER_UNIT)
    cram = _flag(cmd, "--cache-ram")
    assert cram is not None, (
        "router --cache-ram missing: the llama.cpp default is 8192 MiB, which a ~1G MemoryMax "
        "cgroup OOM-kills long before the cache would evict"
    )
    ceiling = _memory_max_mib(ROUTER_UNIT)
    assert 0 < int(cram) < ceiling // 4, (
        f"router --cache-ram {cram} MiB must be a positive cap well under MemoryMax "
        f"({ceiling} MiB) — the model and KV already hold most of it"
    )


# A1 (docs/research/infra-data-config-2026-10-03.md D1): the router's GGUF mapping was
# measured at Rss 0 kB of 278 MB with 7.7 M file refaults, costing ~130 ms p50 on the
# first routed turn after a quiet gap. The r2 GGUF is 291,557,728 bytes; its tensor
# mapping is 278 MiB. Re-point this if the router model changes size.
ROUTER_LOCKED_GGUF_MIB = 278


def _service_section(unit: Path) -> str:
    text = unit.read_text(encoding="utf-8")
    m = re.search(r"^\[Service\]\n(.*?)(?=^\[|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, f"{unit.name} has no [Service] section"
    return m.group(1)


def _router_mlock_problems(unit: Path) -> list[str]:
    """Every way the router's weight lock is missing or would fail at startup."""
    cmd = _exec_start(unit)
    binary = cmd.split()[0]
    has_old = re.search(r"(?:^|\s)--mlock(?:\s|$)", cmd) is not None
    has_new = _flag(cmd, "--load-mode") == "mmap+mlock"
    problems = []
    if not (has_old or has_new):
        problems.append("router ExecStart does not mlock its weights (A1)")
    if "b11194" in binary and has_old:
        problems.append(f"{binary} is the b11194 build, which removed --mlock")
    if "b11194" not in binary and has_new:
        problems.append(f"{binary} predates --load-mode; use --mlock")
    if not re.search(r"^LimitMEMLOCK=infinity$", _service_section(unit), re.MULTILINE):
        problems.append("router [Service] lacks LimitMEMLOCK=infinity; a capped "
                        "RLIMIT_MEMLOCK makes llama.cpp skip the lock with only a warning")
    return problems


def test_router_pins_its_weights_with_the_flag_its_build_accepts():
    assert _router_mlock_problems(ROUTER_UNIT) == []


def test_router_mlock_check_catches_each_failure(tmp_path):
    """Negative controls: each break of the live unit must go red."""
    live = ROUTER_UNIT.read_text(encoding="utf-8")
    breaks = {
        "no lock": live.replace("  --mlock \\\n", ""),
        "b11194 + --mlock": live.replace(
            "/home/zoe/llama.cpp/build-jetson-new/bin/llama-server",
            "/home/zoe/llama.cpp-b11194/build-jetson/bin/llama-server"),
        "memlock capped": live.replace("LimitMEMLOCK=infinity", "LimitMEMLOCK=8M"),
        "memlock in [Install]": live.replace("LimitMEMLOCK=infinity\n", "")
        + "LimitMEMLOCK=infinity\n",
    }
    for name, text in breaks.items():
        assert text != live, f"negative control {name!r} did not change the unit"
        probe = tmp_path / f"{name.replace(' ', '_')}.service"
        probe.write_text(text, encoding="utf-8")
        assert _router_mlock_problems(probe), f"negative control {name!r} stayed green"


def _size_mib(value: str) -> float:
    m = re.fullmatch(r"([0-9]+)([KMG])", value)
    assert m, f"unparseable size {value!r}"
    return int(m.group(1)) * {"K": 1 / 1024, "M": 1, "G": 1024}[m.group(2)]


def _router_floor_plus_lock_fits(low: str, ceiling: str) -> bool:
    return _size_mib(low) + ROUTER_LOCKED_GGUF_MIB <= _size_mib(ceiling)


def test_router_ceiling_holds_the_floor_plus_the_locked_weights():
    """Locked pages are unreclaimable. The 768M floor was sized for a working set
    measured while the weights were still evictable, so after the lock the
    ceiling must hold floor + GGUF, or the backstop moves inside normal use."""
    section = _service_section(ROUTER_UNIT)
    low = re.search(r"^MemoryLow=(\S+)$", section, re.MULTILINE).group(1)
    ceiling = re.search(r"^MemoryMax=(\S+)$", section, re.MULTILINE).group(1)
    assert _router_floor_plus_lock_fits(low, ceiling), (
        f"MemoryLow={low} + {ROUTER_LOCKED_GGUF_MIB} MiB locked GGUF exceeds MemoryMax={ceiling}"
    )
    # Negative control: the pre-A1 1G ceiling must fail this rule.
    assert not _router_floor_plus_lock_fits(low, "1G")
