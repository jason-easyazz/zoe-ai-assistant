#!/usr/bin/env python3
"""Scratch probe of candidate key-free engines + APIs. Hand-run, read-only."""

from __future__ import annotations

import time

import httpx

UA_BROWSER = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
UA_POLITE = "ZoeAssistant/0.1 (local household assistant; +https://github.com/jason/zoe)"

Q = "capital of Australia"

CASES = [
    ("ddg-html", "POST", "https://html.duckduckgo.com/html/", UA_BROWSER,
     {"data": {"q": Q, "kl": "us-en", "b": ""}}),
    ("ddg-lite", "POST", "https://lite.duckduckgo.com/lite/", UA_BROWSER,
     {"data": {"q": Q}}),
    ("startpage", "GET", f"https://www.startpage.com/sp/search?query={Q.replace(' ', '+')}", UA_BROWSER, {}),
    ("ecosia", "GET", f"https://www.ecosia.org/search?q={Q.replace(' ', '+')}", UA_BROWSER, {}),
    ("brave", "GET", f"https://search.brave.com/search?q={Q.replace(' ', '+')}", UA_BROWSER, {}),
    ("searx-be", "GET", f"https://searx.be/search?q={Q.replace(' ', '+')}&format=json", UA_BROWSER, {}),
    ("wiki-summary-polite", "GET",
     "https://en.wikipedia.org/api/rest_v1/page/summary/Canberra", UA_POLITE, {}),
    ("wiki-action-api", "GET",
     "https://en.wikipedia.org/w/api.php?action=query&format=json&prop=extracts"
     "&exintro=1&explaintext=1&redirects=1&titles=Canberra", UA_POLITE, {}),
    ("wiki-search-api", "GET",
     f"https://en.wikipedia.org/w/api.php?action=query&format=json&list=search&srsearch={Q.replace(' ', '%20')}&srlimit=3",
     UA_POLITE, {}),
    ("hn-algolia", "GET", "https://hn.algolia.com/api/v1/search?query=jetson&hitsPerPage=3", UA_POLITE, {}),
]


def main() -> None:
    for name, method, url, ua, kw in CASES:
        started = time.time()
        try:
            with httpx.Client(timeout=12.0, follow_redirects=True, trust_env=False) as c:
                r = c.request(method, url, headers={"User-Agent": ua, "Accept-Language": "en-US,en;q=0.9"}, **kw)
            body = r.text
            el = time.time() - started
            low = body.lower()
            bad = any(s in low for s in ("captcha", "anomaly-modal", "unusual traffic", "are you a robot"))
            title = ""
            if "<title>" in low:
                title = body[low.index("<title>") + 7 : low.index("</title>")][:40]
            print(f"{name:22s} {r.status_code:<5} {len(body):<8} {el:5.2f}s bad={bad!s:<5} title={title!r}")
        except Exception as exc:  # noqa: BLE001
            print(f"{name:22s} ERR   {type(exc).__name__}: {str(exc)[:60]}")


if __name__ == "__main__":
    main()
