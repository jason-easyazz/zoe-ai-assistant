#!/usr/bin/env python3
"""Capture TRIMMED offline fixtures from live responses. Hand-run, read-only.

Fixtures are deliberately trimmed to the parseable region so the committed
corpus stays small and reviewable. Re-run when an engine changes its markup
and a parser test starts failing for real.

    python3 labs/web-search-spike/capture_fixtures.py
"""

from __future__ import annotations

import json
import pathlib
import re

import httpx

from websearch.engines import DDG_HTML_URL, DDG_LITE_URL, UA_BROWSER, UA_POLITE

FIXTURES = pathlib.Path(__file__).parent / "tests" / "fixtures"
QUERY = "capital of Australia"


def _trim_html(html: str, opener: str, keep: int = 14000) -> str:
    """Keep a window starting at the first result CONTAINER.

    Anchoring on the inner `<a>` instead of the enclosing container truncates
    the first result block, so the parser silently sees one fewer result than
    the live page returned — which is how the first captured fixture lost its
    top hit. Anchor on the container opener.
    """
    idx = html.find(opener)
    return html[idx : idx + keep] if idx >= 0 else html[:keep]


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=15.0, follow_redirects=True, trust_env=False) as client:
        html = client.post(
            DDG_HTML_URL,
            data={"q": QUERY, "kl": "us-en", "b": ""},
            headers={"User-Agent": UA_BROWSER, "Referer": "https://html.duckduckgo.com/"},
        ).text
        (FIXTURES / "ddg_html.html").write_text(_trim_html(html, '<div class="result '), encoding="utf-8")

        lite = client.post(DDG_LITE_URL, data={"q": QUERY}, headers={"User-Agent": UA_BROWSER}).text
        (FIXTURES / "ddg_lite.html").write_text(_trim_html(lite, "<table"), encoding="utf-8")

        wiki = client.get(
            "https://en.wikipedia.org/w/api.php",
            params={
                "action": "query", "format": "json", "prop": "extracts",
                "exintro": 1, "explaintext": 1, "redirects": 1, "titles": "Canberra",
            },
            headers={"User-Agent": UA_POLITE},
        ).json()
        # Trim the extract so the fixture stays small but still exercises the parser.
        for page in wiki.get("query", {}).get("pages", {}).values():
            if "extract" in page:
                page["extract"] = page["extract"][:1200]
        (FIXTURES / "wikipedia_extract.json").write_text(json.dumps(wiki, indent=1), encoding="utf-8")

        hn = client.get(
            "https://hn.algolia.com/api/v1/search",
            params={"query": "jetson orin", "hitsPerPage": 3},
            headers={"User-Agent": UA_POLITE},
        ).json()
        slim = {"hits": [
            {k: h.get(k) for k in ("title", "url", "points", "num_comments", "objectID", "created_at")}
            for h in hn.get("hits", [])
        ]}
        (FIXTURES / "hackernews_search.json").write_text(json.dumps(slim, indent=1), encoding="utf-8")

    # A recorded bot-challenge body: the negative control for "status 200 != results".
    (FIXTURES / "ddg_anomaly.html").write_text(
        '<!DOCTYPE html><html><head><title>DuckDuckGo</title></head>'
        '<body><div id="anomaly-modal"></div><script src="/anomaly.js"></script></body></html>',
        encoding="utf-8",
    )

    for path in sorted(FIXTURES.glob("*")):
        print(f"{path.name:28s} {path.stat().st_size:>7} bytes")
    assert re.search(r"result__a", (FIXTURES / "ddg_html.html").read_text()), "DDG fixture lost its markers"


if __name__ == "__main__":
    main()
