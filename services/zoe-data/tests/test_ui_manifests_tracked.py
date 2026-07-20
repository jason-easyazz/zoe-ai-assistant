"""The desktop UI's two load-bearing manifests must be GIT-TRACKED.

Both were caught by the blanket `*.json` rule in .gitignore and existed only as
untracked files on the live box:

  - services/zoe-ui/dist/manifest.json          -- linked by ~20 pages and
    precached by sw.js. Missing, nginx's SPA fallback serves index.html AS the
    manifest, silently breaking PWA install.
  - services/zoe-ui/dist/js/widgets/widget-manifest.json -- widget-system.js
    aborts with 0 widgets without it, blanking the grid on the lists page and
    both touch dashboards.

A `git clean -fdx` would have destroyed the only copies in existence.

CRITICAL: this asserts TRACKING, not existence. An existence check passes on any
box where the untracked file happens to be present -- which is exactly how this
went unnoticed. validate_critical_files.py only does Path.exists(), so it cannot
catch a regression here on its own.
"""
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[3]
MANIFESTS = [
    "services/zoe-ui/dist/manifest.json",
    "services/zoe-ui/dist/js/widgets/widget-manifest.json",
]


@pytest.mark.parametrize("rel", MANIFESTS)
def test_manifest_is_git_tracked(rel):
    proc = subprocess.run(
        ["git", "ls-files", "--error-unmatch", rel],
        cwd=REPO, capture_output=True, text=True,
    )
    assert proc.returncode == 0, (
        f"{rel} is NOT git-tracked. It is covered by the blanket '*.json' rule in "
        ".gitignore and needs a negation entry — `git add -f` would stage this one "
        "file while leaving the rule to swallow the next."
    )


@pytest.mark.parametrize("rel", MANIFESTS)
def test_manifest_is_valid_json(rel):
    import json
    json.loads((REPO / rel).read_text(encoding="utf-8"))


def test_widget_manifest_flags_the_list_widgets_for_the_lists_page():
    """createDefaultLayout() selects via `w.lists === true`. Only 'project'
    carried that flag, so the lists page defaulted to a single stub-backed
    Project tile and never called /api/lists (reported 2026-07-20).
    """
    import json
    m = json.loads((REPO / MANIFESTS[1]).read_text(encoding="utf-8"))
    flagged = {w["id"] for w in m["widgets"] if w.get("lists") is True}
    for wid in ("shopping", "personal", "work", "bucket"):
        assert wid in flagged, (
            f"{wid} is not flagged lists:true — the lists page will not offer it "
            "as a default widget"
        )


def test_manifest_lists_flags_match_the_client_allowlist():
    """The manifest's `lists: true` set and LIST_WIDGET_TYPES must match EXACTLY,
    in both directions. They are consulted at different moments, so any drift is
    silent rather than an error:

      * flagged but NOT allowlisted -> createDefaultLayout() offers the widget,
        it renders on first load, gets saved, then loadFromData() filters it out
        on the next reload and it vanishes. (Caught when 'tasks' was flagged.)
      * allowlisted but NOT flagged -> getAvailableWidgets('lists') never offers
        it for a fresh default layout, yet a saved layout containing it is
        happily accepted. The widget is reachable only by accident of history.
        ('reminders' and 'dynamic-list' were in exactly this state, and
        'reminders' is even named in the client's own fallback defaults.)
    """
    import json
    import re

    m = json.loads((REPO / MANIFESTS[1]).read_text(encoding="utf-8"))
    flagged = {w["id"] for w in m["widgets"] if w.get("lists") is True}

    src = (REPO / "services/zoe-ui/dist/js/lists-dashboard.js").read_text(encoding="utf-8")
    block = re.search(r"const LIST_WIDGET_TYPES = \[(.*?)\]", src, re.S)
    assert block, "LIST_WIDGET_TYPES not found in lists-dashboard.js"
    allowed = set(re.findall(r"'([^']+)'", block.group(1)))

    only_manifest = flagged - allowed
    only_client = allowed - flagged
    assert not only_manifest, (
        f"manifest flags {sorted(only_manifest)} for the lists page, but "
        "LIST_WIDGET_TYPES does not allow them — they would appear on first "
        "load and disappear after the layout is saved and reloaded"
    )
    assert not only_client, (
        f"LIST_WIDGET_TYPES allows {sorted(only_client)}, but the manifest does "
        "not flag them lists:true — they are never offered for a fresh default "
        "layout, only reachable via an already-saved layout"
    )
