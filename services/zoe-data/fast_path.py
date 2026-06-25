"""Back-compat shim — the channel-agnostic fast path moved to `fast_tiers` (Stage A).

`fast_path.resolve` is now `fast_tiers.resolve`. Existing callers
(`import fast_path; await fast_path.resolve(text, user, session, allow_writes=…)`)
keep working unchanged: `resolve` added keyword-only `channel`/`run_tier0` params
with safe defaults, so the old call shape is fully compatible. New code should
import `fast_tiers` directly and pass a `channel` tag. This shim stays for one
release, then callers migrate off it.
"""
from __future__ import annotations

from fast_tiers import resolve  # noqa: F401

__all__ = ["resolve"]
