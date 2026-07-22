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
A URL carrying an authority (``https://host/…``, ``http://host/…``, or
protocol-relative ``//host/…``) in any of:

* a ``src``/``href`` attribute on a **loading tag** (``script``, ``link``,
  ``img``, ``iframe``, ``source``, ``video``, ``audio``, ``embed``);
* a CSS ``@import``; or
* a CSS ``url(…)`` — a font, background image or cursor.

The CSS forms are scanned in **both** ``dist/**/*.html`` (inline ``<style>``)
and ``dist/**/*.css``. Scanning only the HTML would leave the hole open: a page
loads a local stylesheet, and that stylesheet ``@import``s a CDN — the exact
cross-origin page-load dependency this guard exists to stop, with the check
still green (Greptile, PR #1506). Vendored stylesheets under ``dist/lib/`` are
scanned too, for the same reason.

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
# CSS url(...) — fonts, background images, cursors. `url(#default#VML)`,
# `url(data:...)` and relative paths do not match: an authority is required.
_CSS_URL_RE = re.compile(r"""url\(\s*["']?((?:https?:)?//[^"')\s]+)""", re.IGNORECASE)
# An authority-bearing URL: https://host, http://host, or protocol-relative //host.
_EXTERNAL_RE = re.compile(r"^(?:https?:)?//", re.IGNORECASE)

# ── Allowlist ───────────────────────────────────────────────────────────────
# (asset path relative to dist/ — .html or .css, exact URL) → one-line reason.
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


def find_external_assets(source: str) -> list[str]:
    """Every external-origin subresource URL declared in `source` (HTML or CSS).

    Tag-anchored on purpose: only URLs inside a loading tag's ``src``/``href``
    count, so hyperlinks and inline-JS string URLs are not matched. The CSS
    forms (``@import``, ``url(…)``) are matched anywhere, which covers both a
    ``.css`` file and an inline ``<style>`` block.
    """
    found: list[str] = []
    for attrs in _TAG_RE.findall(source):
        for url in _ATTR_RE.findall(attrs):
            if _EXTERNAL_RE.match(url.strip()):
                found.append(url.strip())
    for pattern in (_IMPORT_RE, _CSS_URL_RE):
        found.extend(url.strip() for url in pattern.findall(source))
    return sorted(set(found))


def _pages() -> list[Path]:
    return sorted(p for p in _DIST.rglob("*.html") if p.is_file())


def _stylesheets() -> list[Path]:
    return sorted(p for p in _DIST.rglob("*.css") if p.is_file())


def test_dist_has_pages_to_scan():
    """A scan over zero files is green for the wrong reason."""
    assert len(_pages()) > 10, f"expected the docroot at {_DIST} to hold pages"
    assert len(_stylesheets()) > 10, f"expected the docroot at {_DIST} to hold stylesheets"


@pytest.mark.parametrize(
    "asset",
    _pages() + _stylesheets(),
    ids=lambda p: str(p.relative_to(_DIST)),
)
def test_no_external_subresources(asset):
    rel = str(asset.relative_to(_DIST))
    offenders = [
        url
        for url in find_external_assets(asset.read_text(encoding="utf-8", errors="replace"))
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
# A .css FILE reaching off-box — the hole a HTML-only scan leaves open: the page
# links a perfectly local stylesheet, and the stylesheet imports the CDN.
_LINKED_CSS_IMPORT = "@import url('https://cdn.jsdelivr.net/npm/x@1/x.css');\n.a{color:red}"
_CSS_FONT_URL = "@font-face{font-family:Inter;src:url(https://fonts.gstatic.com/s/i.woff2)}"
_CSS_BG_PROTOCOL_RELATIVE = '.hero{background:url("//cdn.example.com/hero.jpg")}'


@pytest.mark.parametrize(
    "bad",
    [
        _CDN_SCRIPT,
        _CDN_STYLESHEET,
        _PROTOCOL_RELATIVE,
        _CSS_IMPORT,
        _CDN_IMAGE,
        _LINKED_CSS_IMPORT,
        _CSS_FONT_URL,
        _CSS_BG_PROTOCOL_RELATIVE,
    ],
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


def test_scan_accepts_local_css_urls():
    """Relative, data: and Leaflet's `url(#default#VML)` are all same-origin."""
    good = """
    .leaflet-control-layers-toggle{background-image:url(images/layers.png)}
    .vml{behavior:url(#default#VML)}
    .icon{background:url("data:image/svg+xml;base64,AAAA")}
    .abs{background:url('/lib/leaflet/images/marker-icon.png')}
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


# --------------------------------------------------------------------------- #
# CSP: the ENFORCEMENT half of local-first.
#
# #1506 removed every CDN reference from dist/. This pins the other half: if the
# CSP still permits those origins, "local-first" is a convention rather than a
# guarantee — one inline <script src="https://cdn…"> would load happily and the
# scan above would be the only thing standing in its way. Dropping the origins
# means the browser refuses, whatever a page asks for.
# --------------------------------------------------------------------------- #
_NGINX_CONF = Path(__file__).resolve().parents[2] / "zoe-ui" / "nginx.conf"

# Origins that must never reappear in a CSP directive. youtube.com is
# deliberately absent: video embeds are a real, in-use feature.
_FORBIDDEN_CSP_ORIGINS = ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com",
                          "fonts.googleapis.com", "fonts.gstatic.com")

_CSP_LINE_RE = re.compile(r"add_header\s+Content-Security-Policy\s+(.+?)\s+always;", re.I | re.S)


def _csp_headers() -> list[str]:
    return _CSP_LINE_RE.findall(_NGINX_CONF.read_text(encoding="utf-8"))


def test_nginx_conf_has_csp_headers_to_check():
    # Guard against the scan silently passing because the regex stopped matching.
    assert len(_csp_headers()) >= 5, "expected the known CSP headers in nginx.conf"


@pytest.mark.parametrize("origin", _FORBIDDEN_CSP_ORIGINS)
def test_csp_does_not_permit_cdn_origins(origin):
    offending = [h[:120] for h in _csp_headers() if origin in h]
    assert not offending, (
        f"CSP still allows {origin} — every asset was vendored in #1506, so "
        f"permitting it re-opens the hole silently. Offending header(s): {offending}"
    )


def test_csp_still_allows_youtube_embeds():
    # Negative control: the tightening must not have been a blanket strip.
    assert all("www.youtube.com" in h for h in _csp_headers()), (
        "youtube.com was removed from a CSP header — video embeds are in use"
    )
