#!/usr/bin/env python3
"""Household-data canary for this PUBLIC repository (2026-10-19).

Fails when a tracked file contains one of the household's canary values: a member's name, a personal email address, the
home's coordinates or a phone number. The repository must never contain the values it guards, so it holds only KEYED
digests of them:

  * ``tools/audit/privacy_canary_digests.json`` (committed): ``HMAC-SHA256(key, "<kind>:<normalised value>")`` per canary.
    Without the key the digests reveal nothing - not even a dictionary attack on first names works, which a plain salted
    hash of a short name would not survive.
  * the KEY, never committed: ``$ZOE_PRIVACY_CANARY_KEY`` (the GitHub Actions secret of that name in CI) or the file
    ``~/.zoe/privacy-canary/key`` (mode 0600) on the box.
  * the plaintext canary list, never committed: ``~/.zoe/privacy-canary/canaries.txt`` (mode 0600), one per line, used only
    by ``--rebuild``. Lines: ``word:<name>``, ``email:<address>``, ``coord:<decimal degrees>``, ``tel:<number>``; ``#`` comments.

Every tracked text file is cut into candidates of the same four kinds and normalised the same way (lower-case words of 3+
letters; lower-case emails; decimal numbers with 3+ decimals truncated to 3; phone-shaped digit runs reduced to their last 9
digits). A candidate whose digest is in the committed set is a hit. Hits are reported as ``path:line kind`` - never the value.

Usage:
  privacy_canary.py --scan            # exit 1 on a hit, 2 when no key is available
  privacy_canary.py --rebuild         # re-derive the digests from the private canary list (after adding a canary)
  privacy_canary.py --init-key        # create ~/.zoe/privacy-canary/key (0600) if absent

Tests: ``tests/unit/test_privacy_canary.py`` (the mechanism with a throwaway key always; the real scan when a key is present).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path
from typing import Iterable, Iterator

ROOT = Path(__file__).resolve().parents[2]
DIGESTS = Path(__file__).with_name("privacy_canary_digests.json")
PRIVATE_DIR = Path(os.environ.get("ZOE_PRIVACY_CANARY_DIR", str(Path.home() / ".zoe" / "privacy-canary")))
KINDS = ("word", "email", "coord", "tel")
MAX_FILE_BYTES = 8 * 1024 * 1024

_WORD = re.compile(r"[a-z]{3,}")
_EMAIL = re.compile(r"[a-z0-9._%+-]+@[a-z0-9-]+(?:\.[a-z0-9-]+)+")
_COORD = re.compile(r"-?\d{1,3}\.\d{3,}")
_TEL = re.compile(r"(?<![\w.])\+?\d[\d ()\-]{7,16}\d(?![\w.])")


def _norm_coord(raw: str) -> str:
    whole, _, frac = raw.partition(".")
    return f"{whole}.{frac[:3]}"


def _norm_tel(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    return digits[-9:] if len(digits) >= 9 else None


def normalise(kind: str, value: str) -> str | None:
    """The canonical ``kind:value`` string a canary or a candidate is digested as (None = not a valid value of that kind)."""
    v = value.strip().lower()
    if kind == "word":
        return f"word:{v}" if _WORD.fullmatch(v) else None
    if kind == "email":
        return f"email:{v}" if _EMAIL.fullmatch(v) else None
    if kind == "coord":
        return f"coord:{_norm_coord(v)}" if _COORD.fullmatch(v) else None
    if kind == "tel":
        t = _norm_tel(v)
        return f"tel:{t}" if t else None
    return None


def candidates(text: str) -> Iterator[str]:
    """Every canonical candidate in ``text`` (duplicates included)."""
    low = text.lower()
    for m in _WORD.finditer(low):
        yield f"word:{m.group(0)}"
    for m in _EMAIL.finditer(low):
        yield f"email:{m.group(0)}"
    for m in _COORD.finditer(low):
        yield f"coord:{_norm_coord(m.group(0))}"
    for m in _TEL.finditer(low):
        t = _norm_tel(m.group(0))
        if t:
            yield f"tel:{t}"


def digest(key: bytes, canonical: str) -> str:
    return hmac.new(key, canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def load_key() -> bytes | None:
    env = os.environ.get("ZOE_PRIVACY_CANARY_KEY", "").strip()
    if env:
        return env.encode("utf-8")
    path = PRIVATE_DIR / "key"
    try:
        raw = path.read_text().strip()
    except OSError:
        return None
    return raw.encode("utf-8") if raw else None


def load_digests(path: Path = DIGESTS) -> set[str]:
    data = json.loads(path.read_text())
    return set(data.get("digests", []))


def tracked_files(root: Path = ROOT) -> list[Path]:
    out = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], capture_output=True, check=True).stdout
    return [root / p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]


def _read_text(path: Path) -> str | None:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            return None
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\0" in raw[:8192]:
        return None  # binary
    return raw.decode("utf-8", "replace")


def scan(files: Iterable[Path], key: bytes, digests: set[str], root: Path = ROOT) -> list[tuple[str, int, str]]:
    """``(relative path, line, kind)`` for every canary hit. Digests are computed once per distinct candidate."""
    cache: dict[str, bool] = {}

    def is_hit(c: str) -> bool:
        if c not in cache:
            cache[c] = digest(key, c) in digests
        return cache[c]

    hits: list[tuple[str, int, str]] = []
    for path in files:
        text = _read_text(path)
        if text is None:
            continue
        if not any(is_hit(c) for c in set(candidates(text))):
            continue
        for n, line in enumerate(text.splitlines(), 1):
            for c in set(candidates(line)):
                if is_hit(c):
                    try:
                        rel = str(path.relative_to(root))
                    except ValueError:
                        rel = str(path)
                    hits.append((rel, n, c.split(":", 1)[0]))
    return hits


def parse_canaries(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        kind, sep, value = line.partition(":")
        canon = normalise(kind.strip().lower(), value) if sep else None
        if canon is None:
            raise ValueError("unparseable canary line (expected <word|email|coord|tel>:<value>)")
        out.append(canon)
    return out


def build_digests(key: bytes, canaries: Iterable[str]) -> dict:
    return {
        "version": 1,
        "scheme": "HMAC-SHA256(key, '<kind>:<normalised value>'), key outside the repository",
        "digests": sorted({digest(key, c) for c in canaries}),
    }


def _private_file(path: Path, content: str) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(content)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--scan", action="store_true")
    g.add_argument("--rebuild", action="store_true")
    g.add_argument("--init-key", action="store_true")
    args = ap.parse_args(argv)

    if args.init_key:
        path = PRIVATE_DIR / "key"
        if path.exists():
            print(f"key already present at {path}")
            return 0
        _private_file(path, secrets.token_hex(32) + "\n")
        print(f"created {path} (0600). Add the same value as the GitHub Actions secret ZOE_PRIVACY_CANARY_KEY.")
        return 0

    key = load_key()
    if key is None:
        print("privacy canary: no key (ZOE_PRIVACY_CANARY_KEY or ~/.zoe/privacy-canary/key) - not checked", file=sys.stderr)
        return 2

    if args.rebuild:
        canaries = parse_canaries((PRIVATE_DIR / "canaries.txt").read_text())
        DIGESTS.write_text(json.dumps(build_digests(key, canaries), indent=2) + "\n")
        print(f"wrote {len(set(canaries))} digests to {DIGESTS.relative_to(ROOT)}")
        return 0

    hits = scan(tracked_files(), key, load_digests())
    for rel, line, kind in hits:
        print(f"{rel}:{line}: household canary ({kind})")
    if hits:
        print(f"privacy canary: {len(hits)} hit(s) - replace them with obvious placeholders", file=sys.stderr)
        return 1
    print("privacy canary: clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
