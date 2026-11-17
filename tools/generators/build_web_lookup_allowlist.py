#!/usr/bin/env python3
"""Build services/zoe-data/lexicons_data/web_lookup_allow_en.txt.gz, the web-lookup scrubber's allow-list: the only
words a search query may carry out of the house. Source: SCOWL via the system word list (Debian/Ubuntu package
wamerican, /usr/share/dict/american-english), whose licence permits redistribution with its notice (shipped beside
the list as web_lookup_allow_en.NOTICE), plus the small hand-written supplement in web_lookup_allow_extra_en.txt.

Only lower-case entries and all-capitals abbreviations are kept (proper nouns are left out); possessives and anything that is not plain letters
are dropped. The output is deterministic (sorted, gzip mtime 0), so a rebuild that changes nothing changes no bytes.

    python3 tools/generators/build_web_lookup_allowlist.py [--dict PATH]
"""
from __future__ import annotations

import argparse
import gzip
import re
import sys
import unicodedata
from pathlib import Path

LEX = Path(__file__).resolve().parents[2] / "services" / "zoe-data" / "lexicons_data"
OUT = LEX / "web_lookup_allow_en.txt.gz"
EXTRA = LEX / "web_lookup_allow_extra_en.txt"
PLAIN = re.compile(r"^[a-z]+$")


def _plain(entry: str) -> str:
    w = entry.strip().removesuffix("'s").casefold()
    return "".join(c for c in unicodedata.normalize("NFKD", w) if not unicodedata.combining(c))   # cafe, naive


def build(dict_path: Path) -> list[str]:
    # Only entries the dictionary spells in lower case. A capitalised entry is a proper noun (Jennifer, Michael, a
    # town), and a word that is ONLY ever capitalised must not become an ordinary word by folding: that would let a
    # person's name out. A name that is also an ordinary word (rose, bill, mark) stays, as it always would.
    # Capitalised words a search genuinely needs (days, months, holidays, languages) come from the supplement.
    words = set()
    for line in dict_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        entry = line.strip()
        # an all-capitals abbreviation (TV, DVD, USB) is not a name, so it stays
        if not entry or not (entry[0].islower() or (len(entry) >= 2 and entry.isupper())):
            continue
        w = _plain(entry)
        if PLAIN.match(w) and len(w) <= 24:
            words.add(w)
    for line in EXTRA.read_text(encoding="utf-8").splitlines():
        w = line.split("#", 1)[0].strip().casefold()
        if PLAIN.match(w):
            words.add(w)
    return sorted(words)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dict", type=Path, default=Path("/usr/share/dict/american-english"))
    a = ap.parse_args(argv)
    words = build(a.dict)
    with open(OUT, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
        gz.write(("\n".join(words) + "\n").encode("utf-8"))
    print(f"{len(words)} words -> {OUT.relative_to(LEX.parents[2])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
