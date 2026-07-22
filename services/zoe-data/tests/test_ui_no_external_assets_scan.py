"""No page in the nginx docroot may load a subresource from another origin.

Zoe is local-first (``docs/VISION.md``): the box runs on a LAN that may have no
internet at all, and nothing should leave it unless the operator opted in. A
``<script src="https://cdn.jsdelivr.net/...">`` breaks both — it is an
availability dependency on a third party for a feature that is supposed to work
offline, and it announces every page view to that third party.

``chat.html`` used to pull nine libraries from jsDelivr/unpkg and the journal
pages pulled FilePond the same way. The service worker has already broken this
once: routing those cross-origin requests through NetworkFirst handed workbox an
opaque response it could not read, and all nine assets failed with
``net::ERR_FAILED`` on every SW-controlled reload — markdown rendering, HTML
sanitization, code highlighting, charts and maps all dead (see
``services/zoe-ui/AGENTS.md``). They are now vendored under ``dist/lib/``.

This scan makes the CDN dependency impossible to re-add silently, and is the
precondition for dropping ``cdn.jsdelivr.net`` / ``unpkg.com`` from the nginx
CSP. It is a pure source scan (stdlib ``re``), sibling to
``test_write_path_exception_scan.py``.

Definition — an "external subresource"
--------------------------------------
A ``src``/``href`` attribute on a **loading tag** (``script``, ``link``,
``img``, ``iframe``, ``source``, ``video``, ``audio``, ``embed``) — or a CSS
``@import`` — whose URL carries an authority: ``https://host/…``,
``http://host/…`` or protocol-relative ``//host/…``.

Deliberately out of scope (keep this scan narrow enough to stay enabled)
-----------------------------------------------------------------------
* **Hyperlinks** (``<a href="https://t.me/BotFather">``). A link the user
  chooses to click is not a subresource; it costs nothing when offline.
* **Runtime API/data URLs in JavaScript** — the OpenStreetMap tile template and
  the Nominatim geocoder are user-triggered map/data features, not page assets,
  and they degrade gracefully. Only markup-declared loads are scanned; the
  regex is tag-anchored, so ``img.src = "https://…"`` inside inline JS is not
  matched.
* **`data:` / relative / root-relative URLs** — same-origin by construction.

The companion test walks the other way: every ``/lib/...`` path a page
references must exist on disk. ``dist/lib/`` is tracked in git and nothing
builds it, so a typo'd vendor path is a 404 that only shows up in a browser.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# Slim-dep-green (pure stdlib source scan): runs in the ci_safe lane on
# GitHub-hosted CI (marker-based selection, no validate.yml enumeration —
# see tests/AGENTS.md).
pytestmark = pytest.mark.ci_safe

_DIST = Path(__file__).resolve().parents[2] / "zoe-ui" / "dist"

# Tags whose src/href the browser fetches as part of rendering the page.
_LOADING_TAGS = "script|link|img|iframe|source|video|audio|embed"

_TAG_RE = re.compile(rf"<(?:{_LOADING_TAGS})\b([^>]*)>", re.IGNORECASE)
_ATTR_RE = re.compile(r"""\b(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_IMPORT_RE = re.compile(
    r"""@import\s+(?:url\(\s*)?["']?((?:https?:)?//[^"')\s;]+)""", re.IGNORECASE
)
# An authority-bearing URL: https://host, http://host, or protocol-relative //host.
_EXTERNAL_RE = re.compile(r"^(?:https?:)?//", re.IGNORECASE)

# ── Allowlist ───────────────────────────────────────────────────────────────
# (page relative to dist/, exact URL) → one-line reason.
# Every entry is an external load that is knowingly tolerated. Keep this short;
# a growing allowlist means the docroot is drifting back off-box.
ALLOWLIST: dict[tuple[str, str], str] = {
    # Decorative stock photos hardcoded into journal.html's static demo timeline
    # (the real timeline is rendered by js/journal-api.js from Zoe's own data).
    # They are <img> placeholders, not libraries: offline they render as broken
    # images rather than breaking a feature. Replacing them is a design call,
    # tracked as a follow-up to the CDN vendoring PR.
    (
        "journal.html",
        "https://images.unsplash.com/photo-1506905925346-21bda4d32df4?w=800&h=400&fit=crop",
    ): "static demo placeholder photo — decorative, follow-up to replace",
    (
        "journal.html",
        "https://images.unsplash.com/photo-1495521821757-a1efb6729352?w=800&h=400&fit=crop",
    ): "static demo placeholder photo — decorative, follow-up to replace",
    (
        "journal.html",
        "https://images.unsplash.com/photo-1506905925346-21bda4d32df4?w=1200&h=600&fit=crop",
    ): "static demo placeholder photo — decorative, follow-up to replace",
}


def find_external_assets(markup: str) -> list[str]:
    """Every external-origin subresource URL declared in `markup`.

    Tag-anchored on purpose: only URLs inside a loading tag's ``src``/``href``
    (or a CSS ``@import``) count, so hyperlinks and inline-JS string URLs are
    not matched.
    """
    found: list[str] = []
    for attrs in _TAG_RE.findall(markup):
        for url in _ATTR_RE.findall(attrs):
            if _EXTERNAL_RE.match(url.strip()):
                found.append(url.strip())
    for url in _IMPORT_RE.findall(markup):
        found.append(url.strip())
    return sorted(set(found))


def _pages() -> list[Path]:
    return sorted(p for p in _DIST.rglob("*.html") if p.is_file())


def test_dist_has_pages_to_scan():
    """A scan over zero files is green for the wrong reason."""
    assert len(_pages()) > 10, f"expected the docroot at {_DIST} to hold pages"


@pytest.mark.parametrize("page", _pages(), ids=lambda p: str(p.relative_to(_DIST)))
def test_no_external_subresources(page):
    rel = str(page.relative_to(_DIST))
    offenders = [
        url
        for url in find_external_assets(page.read_text(encoding="utf-8", errors="replace"))
        if (rel, url) not in ALLOWLIST
    ]
    assert not offenders, (
        f"{rel} loads {len(offenders)} subresource(s) from another origin. Zoe is "
        f"local-first: vendor them under dist/lib/ and reference /lib/... (see "
        f"services/zoe-ui/AGENTS.md), or add an ALLOWLIST entry with a reason: "
        + ", ".join(offenders)
    )


def test_allowlist_entries_are_live():
    """An allowlist entry for a URL no page loads any more is stale."""
    for (rel, url), reason in ALLOWLIST.items():
        assert reason.strip(), f"{rel}:{url} allowlist entry needs a reason"
        page = _DIST / rel
        assert page.is_file(), f"ALLOWLIST references {rel}, which no longer exists"
        assert url in find_external_assets(
            page.read_text(encoding="utf-8", errors="replace")
        ), f"ALLOWLIST references {rel} → {url}, which it no longer loads — drop it"


def test_vendored_paths_resolve_on_disk():
    """Every `/lib/...` asset a page references must be committed under dist/lib/.

    Nothing fetches or builds `dist/lib/`, so a typo'd vendor path is a silent
    404 in the browser and a dead library — exactly the failure this PR removes.
    """
    missing: list[str] = []
    for page in _pages():
        markup = page.read_text(encoding="utf-8", errors="replace")
        for attrs in _TAG_RE.findall(markup):
            for url in _ATTR_RE.findall(attrs):
                url = url.strip().split("?")[0]
                if not url.startswith("/lib/"):
                    continue
                if not (_DIST / url.lstrip("/")).is_file():
                    missing.append(f"{page.relative_to(_DIST)} → {url}")
    assert not missing, "referenced vendor asset(s) not present in dist/lib/: " + ", ".join(
        sorted(set(missing))
    )


# ── negative cases: the scan itself must catch the patterns it exists for ────

_CDN_SCRIPT = '<script src="https://cdn.jsdelivr.net/npm/marked@15/marked.min.js"></script>'
_CDN_STYLESHEET = '<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />'
_PROTOCOL_RELATIVE = '<script src="//cdnjs.cloudflare.com/ajax/libs/x/x.js"></script>'
_CSS_IMPORT = "<style>@import url('https://fonts.googleapis.com/css2?family=Inter');</style>"
_CDN_IMAGE = '<img src="https://images.example.com/a.png" alt="a">'


@pytest.mark.parametrize(
    "bad", [_CDN_SCRIPT, _CDN_STYLESHEET, _PROTOCOL_RELATIVE, _CSS_IMPORT, _CDN_IMAGE]
)
def test_scan_flags_external_subresources(bad):
    assert find_external_assets(bad), "scan missed an external subresource"


def test_scan_accepts_local_paths():
    good = """
    <script src="/lib/marked/marked.min.js"></script>
    <link rel="stylesheet" href="/lib/prism/prism-tomorrow.min.css">
    <link rel="stylesheet" href="css/dark-mode-shared.css">
    <img src="data:image/png;base64,AAAA" alt="x">
    """
    assert find_external_assets(good) == []


def test_scan_ignores_hyperlinks():
    """An <a href> is a user-initiated navigation, not a page asset."""
    assert find_external_assets('<a href="https://t.me/BotFather">bot</a>') == []


def test_scan_ignores_inline_js_url_assignment():
    """`img.src = "https://…"` in inline JS is a runtime data URL, not markup."""
    js = """<script>
      const u = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
      img.src = "https://images.example.com/a.png";
    </script>"""
    assert find_external_assets(js) == []
