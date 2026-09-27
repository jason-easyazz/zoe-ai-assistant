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
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
UNIT = ROOT / "scripts" / "setup" / "systemd" / "llama-server.service"


def _exec_start() -> str:
    text = UNIT.read_text(encoding="utf-8")
    m = re.search(r"^ExecStart=(.*?)(?=\n[A-Z][A-Za-z]*=|\n\[|\Z)", text, re.DOTALL | re.MULTILINE)
    assert m, "llama-server.service has no ExecStart block"
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
