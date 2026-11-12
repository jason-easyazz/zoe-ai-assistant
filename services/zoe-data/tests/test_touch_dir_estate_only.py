"""dist/touch/ is the estate plus three helper pages, and nothing in it is orphaned (2026-11-13).

The 17 legacy per-domain touch pages (and their nav + CSS/JS) were deleted: the estate never linked to
them, and several were harmful when reached by URL (dialogs stubbed to auto-confirm deletes, a settings
page calling 20 missing routes, browser-only timers). A new panel screen belongs INSIDE the estate.

Two checks over the real tree, each with a negative control on a planted listing:
* the page set is exactly home / skybridge / pair / index;
* every non-page file under touch/ is loaded by a kept page or by desktop chat.html (directly, or as a
  font a kept stylesheet pulls in), and every touch/ asset those pages load exists.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

DIST = Path(__file__).resolve().parents[2] / "zoe-ui" / "dist"
TOUCH = DIST / "touch"
PAGES = {"home.html", "skybridge.html", "pair.html", "index.html"}
# Desktop pages that load a shared /touch/ asset (a dependency, not a route).
DESKTOP_LOADERS = ("chat.html",)
_REF = re.compile(r"""(?:src|href)=["']/touch/([^"'?#]+)""")
_CSS_URL = re.compile(r"""url\(["']?\./([^"')]+)""")


def touch_files(root: Path) -> set[str]:
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


def referenced_assets(texts: list[str], css: dict[str, str]) -> set[str]:
    """touch/-relative paths loaded by the given page texts, plus url(./...) targets of loaded stylesheets."""
    refs = {m for t in texts for m in _REF.findall(t)}
    for rel in list(refs):
        if rel.endswith(".css") and rel in css:
            base = rel.rsplit("/", 1)[0]
            refs |= {f"{base}/{u}" for u in _CSS_URL.findall(css[rel])}
    return refs


def unexpected_pages(files: set[str]) -> set[str]:
    return {f for f in files if f.endswith(".html")} ^ PAGES


def orphans(files: set[str], refs: set[str]) -> set[str]:
    return {f for f in files if not f.endswith(".html")} - refs


def _real():
    files = touch_files(TOUCH)
    texts = [(TOUCH / p).read_text(encoding="utf-8") for p in PAGES]
    texts += [(DIST / p).read_text(encoding="utf-8") for p in DESKTOP_LOADERS]
    css = {f: (TOUCH / f).read_text(encoding="utf-8") for f in files if f.endswith(".css")}
    return files, referenced_assets(texts, css)


def test_touch_pages_are_the_estate_and_its_helpers():
    files, _ = _real()
    assert unexpected_pages(files) == set()


def test_no_orphan_asset_and_no_missing_asset_under_touch():
    files, refs = _real()
    assert orphans(files, refs) == set()
    assert refs - files == set(), "a kept page loads a /touch/ asset that does not exist"


def test_negative_controls_see_a_planted_page_and_a_planted_orphan():
    files, refs = _real()
    assert unexpected_pages(files | {"timers.html"}) == {"timers.html"}
    assert unexpected_pages(files - {"pair.html"}) == {"pair.html"}
    assert orphans(files | {"js/touch-menu.js"}, refs) == {"js/touch-menu.js"}
