#!/usr/bin/env python3
"""Pin the service worker's precache to the files it precaches.

Workbox revisions every precached entry with the hand-bumped SW_VERSION string, so a
change to a precached page (index/dashboard/chat/calendar/lists/offline, manifest, the two
precached scripts, the shared stylesheet) that ships WITHOUT a bump leaves every returning
browser on the old copy until some later bump. The rule has lived in services/zoe-ui/AGENTS.md
for months with nothing enforcing it. This records a digest of the precached files' contents
next to SW_VERSION; tests/unit/test_sw_precache_digest.py fails when they drift.

  python3 tools/audit/sw_precache_digest.py            # check (exit 1 on drift)
  python3 tools/audit/sw_precache_digest.py --write    # refresh the digest — AND bump SW_VERSION
"""
from __future__ import annotations

import argparse, hashlib, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SW = ROOT / "services" / "zoe-ui" / "dist" / "sw.js"
DIST = SW.parent
MARK = "// PRECACHE_DIGEST="


def precached_urls(sw_text: str) -> list[str]:
    block = re.search(r"precacheAndRoute\(\[(.*?)\]\);", sw_text, re.S)
    if not block:
        raise ValueError("precacheAndRoute([...]) not found in sw.js")
    return re.findall(r"url:\s*'([^']+)'", block.group(1))


def url_to_path(url: str) -> Path:
    return DIST / ("index.html" if url == "/" else url.lstrip("/"))


def digest(sw_text: str) -> str:
    h = hashlib.sha256()
    for url in precached_urls(sw_text):
        p = url_to_path(url)
        h.update(url.encode()); h.update(b"\0"); h.update(p.read_bytes()); h.update(b"\0")
    return h.hexdigest()[:16]


def recorded(sw_text: str) -> str | None:
    m = re.search(re.escape(MARK) + r"([0-9a-f]{16})", sw_text)
    return m.group(1) if m else None


def sw_version(sw_text: str) -> str | None:
    m = re.search(r"const SW_VERSION = '([^']+)'", sw_text)
    return m.group(1) if m else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--write", action="store_true", help="record the current digest")
    args = ap.parse_args(argv)
    text = SW.read_text(encoding="utf-8")
    now, was = digest(text), recorded(text)
    if args.write:
        line = f"{MARK}{now}  (sha256 of the precached files; refresh with tools/audit/sw_precache_digest.py --write and BUMP SW_VERSION)"
        if was is None:
            text = text.replace("const SW_VERSION = ", line + "\nconst SW_VERSION = ", 1)
        else:
            text = re.sub(re.escape(MARK) + r"[^\n]*", line, text, count=1)
        SW.write_text(text, encoding="utf-8")
        print(f"recorded PRECACHE_DIGEST={now} (SW_VERSION {sw_version(text)}) — bump SW_VERSION if a precached file changed")
        return 0
    if was == now:
        print(f"precache digest fresh: {now} (SW_VERSION {sw_version(text)})")
        return 0
    print(f"PRECACHE DRIFT: a precached file changed (recorded {was}, now {now}).\n"
          f"Bump SW_VERSION in services/zoe-ui/dist/sw.js, then: python3 tools/audit/sw_precache_digest.py --write")
    return 1


if __name__ == "__main__":
    sys.exit(main())
