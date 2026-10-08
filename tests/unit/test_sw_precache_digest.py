"""A precached page cannot change without a service-worker version bump.

Workbox revisions every precached entry with the hand-bumped SW_VERSION string in
services/zoe-ui/dist/sw.js, so a change to a precached file that ships without a bump
leaves returning browsers on the old copy. tools/audit/sw_precache_digest.py records a
digest of the precached files next to SW_VERSION; this test fails on drift and names the
fix (bump SW_VERSION, re-run the tool with --write). A test also proves the digest moves
when a precached file moves (negative control), so a stale digest cannot pass by accident.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "audit" / "sw_precache_digest.py"
spec = importlib.util.spec_from_file_location("sw_precache_digest", TOOL)
mod = importlib.util.module_from_spec(spec)
sys.modules["sw_precache_digest"] = mod
spec.loader.exec_module(mod)


def test_recorded_digest_matches_the_precached_files():
    text = mod.SW.read_text(encoding="utf-8")
    assert mod.recorded(text) is not None, "sw.js carries no PRECACHE_DIGEST line — run tools/audit/sw_precache_digest.py --write"
    assert mod.recorded(text) == mod.digest(text), (
        "a precached file changed without a SW_VERSION bump: bump SW_VERSION in services/zoe-ui/dist/sw.js "
        "and run python3 tools/audit/sw_precache_digest.py --write"
    )


def test_every_precached_url_exists():
    text = mod.SW.read_text(encoding="utf-8")
    urls = mod.precached_urls(text)
    assert urls, "no precached urls parsed"
    for u in urls:
        assert mod.url_to_path(u).is_file(), f"precached {u} is not a file in dist"


def test_digest_moves_when_a_precached_file_changes(monkeypatch, tmp_path):
    # negative control: a one-byte change to a precached file must change the digest
    text = mod.SW.read_text(encoding="utf-8")
    before = mod.digest(text)
    fake_dist = tmp_path / "dist"
    for u in mod.precached_urls(text):
        src = mod.url_to_path(u); dst = fake_dist / src.relative_to(mod.DIST)
        dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(src.read_bytes())
    monkeypatch.setattr(mod, "DIST", fake_dist)
    assert mod.digest(text) == before
    target = fake_dist / "lists.html"
    target.write_bytes(target.read_bytes() + b"\n<!-- changed -->")
    assert mod.digest(text) != before
