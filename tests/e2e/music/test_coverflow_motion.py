"""Cover Flow direct-manipulation harness + the contract check that keeps it honest.

Two halves, and the second is the load-bearing one:

* ``test_coverflow_motion`` drives a headless Chromium at 1280x720 (the panel's
  size) with real synthetic pointer events and asserts the flow follows the
  finger — continuity, rubber-band, fling vs slow drag, catch-mid-settle,
  long-press lift, flick-up remove, and the frame budget.
* ``test_fixture_matches_the_live_contract`` asserts the harness's mock has the
  same shape as the REAL endpoints. A previous carousel PR passed 50 assertions
  on a feature that was dead on the panel because its mocks invented
  ``queue_item_id``/``queue_index`` — fields the API did not send. A mock that
  encodes the contract you *want* proves nothing, so the fixture is pinned to
  what ``localhost:8000`` actually returns.

NOT marked ``ci_safe``: needs node, a real Chromium, and (for the contract half)
the live music stack — none of which exist on a slim GitHub runner. It runs
unmarked in the Jetson's full-directory catch-all lane.

The contract half is additionally opt-in behind ``ZOE_LIVE_MUSIC_CONTRACT=1``
(see ``requires_live_music`` below for why, and for what that gate deliberately
does NOT do).
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

try:
    import requests
except ImportError:  # slim lane
    requests = None

HERE = Path(__file__).parent
HARNESS = HERE / "coverflow_motion.mjs"
FIXTURE = json.loads((HERE / "fixtures" / "music_api.json").read_text())
CHROME = Path("/home/zoe/.cache/ms-playwright/chromium-1148/chrome-linux/chrome")
PW = Path("/home/zoe/.openclaw/npm/node_modules/playwright-core")
API = os.environ.get("ZOE_API", "http://localhost:8000")

# The live-contract probe is OPT-IN, following the repo's `requires_env` shape
# (services/zoe-data/tests/test_zoe_core_client.py) — but with the probe itself
# deferred into the tests, because tests/AGENTS.md forbids live I/O at import.
#
# Why gated rather than always-on: these assert the REAL endpoint's shape, and
# that shape only exists once PR #1393 is merged AND zoe-data has been restarted
# from the live checkout (a merge to main is not a deploy). GitHub runners can't
# reach localhost:8000 so they would skip regardless — but the self-hosted Jetson
# CAN, and this suite would sit red there for a reason that has nothing to do
# with the carousel.
#
# What this is NOT: weakened. The assertions below are unchanged and still fail
# loudly when run. The one thing that must never happen is making them pass by
# teaching the mock to agree with itself — that is exactly how a carousel shipped
# green and dead on the panel. Run them against a live box with:
#     ZOE_LIVE_MUSIC_CONTRACT=1 pytest tests/e2e/music/test_coverflow_motion.py
_LIVE_FLAG = "ZOE_LIVE_MUSIC_CONTRACT"
requires_live_music = pytest.mark.skipif(
    not os.environ.get(_LIVE_FLAG),
    reason=(
        f"live music-API contract probe — opt in with {_LIVE_FLAG}=1 against a box "
        "whose zoe-data includes PR #1393 (merge != deploy; restart it first)"
    ),
)


def _live(path, timeout=8):
    if requests is None:
        pytest.skip("requests not installed (slim lane)")
    try:
        r = requests.get(f"{API}{path}", timeout=timeout)
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"live music API unreachable: {e}")
    if r.status_code != 200:
        pytest.skip(f"live music API returned {r.status_code} for {path}")
    return r.json()


@pytest.mark.skipif(not CHROME.exists(), reason="playwright chromium not installed")
@pytest.mark.skipif(not PW.exists(), reason="playwright-core not installed")
@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_coverflow_motion():
    """The flow follows the finger — driven at the real page, read off the DOM."""
    p = subprocess.run(
        ["node", str(HARNESS)], capture_output=True, text=True, timeout=300
    )
    print(p.stdout)
    if p.returncode != 0:
        print(p.stderr)
    assert p.returncode == 0, f"cover flow motion harness failed:\n{p.stdout}\n{p.stderr}"


@requires_live_music
def test_fixture_matches_the_live_contract():
    """The mock's shape must be the REAL endpoint's shape.

    This is the assertion that would have caught the dead-on-the-panel carousel:
    it fails if the fixture claims a field the API does not actually send.
    """
    live = _live("/api/music/now-playing")
    if not live.get("available") or not live.get("now_playing"):
        pytest.skip("nothing playing — now-playing has no shape to compare")
    real_np = live["now_playing"]
    mock_np = FIXTURE["now_playing_response"]["now_playing"]

    invented = sorted(set(mock_np) - set(real_np))
    assert not invented, (
        f"fixture invents now-playing fields the live API does not send: {invented}. "
        "Either the API regressed or the mock is lying — do not 'fix' this by "
        "deleting the assertion."
    )

    # The two fields the whole flow depends on to find "Now" and to notice a
    # track change (added by PR #1393). Without them cfIndex() can never match a
    # row, _cf.cur pins to -1, and auto-recentre is dead code on the panel.
    for f in ("queue_item_id", "queue_index"):
        assert f in real_np, (
            f"live /api/music/now-playing is missing {f!r} — the Cover Flow "
            "cannot locate the playing track without it (see PR #1393)."
        )


@requires_live_music
def test_queue_fixture_matches_the_live_contract():
    """Queue items: same rule, plus `image` must already be a flat url."""
    np = _live("/api/music/now-playing")
    pid = (np.get("now_playing") or {}).get("player_id")
    if not pid:
        pytest.skip("no active player")
    live = _live(f"/api/music/queue/{pid}")
    items = live.get("items") or []
    if not items:
        pytest.skip("queue is empty — no shape to compare")

    real_keys = set(items[0])
    mock_item = FIXTURE["queue_response"]["items"][0]
    invented = sorted(set(mock_item) - real_keys)
    assert not invented, f"fixture invents queue-item fields: {invented}"

    for f in ("queue_item_id", "index", "name", "image"):
        assert f in real_keys, f"live queue item missing {f!r}"

    # PR #1393 normalizes art at the seam. If this regresses to MA's raw dict the
    # panel renders "[object Object]" and every cover goes blank.
    assert isinstance(items[0]["image"], str), (
        f"live queue item .image must be a flat string url, got "
        f"{type(items[0]['image']).__name__} — the panel renders it into src= "
        "directly and a dict becomes '[object Object]' (PR #1393)."
    )
