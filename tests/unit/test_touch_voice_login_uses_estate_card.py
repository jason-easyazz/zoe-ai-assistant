"""Guard: a voice-forced login must open the estate card, not the retired page.

Live complaint (2026-08-10): "when the voice forces login it goes to the OLD
login screen". It did. `touch/home.html` carries the estate-native who+PIN
unlock card that, in its own words, "replaces the old login page" (#1245,
2026-07-12) — but `redirectToTouchLogin` in the shared executor navigated
unconditionally to `/touch/index.html`, the standalone login page that dates
from the initial commit. A voice turn therefore threw the user off the estate
and onto the retired screen mid-conversation.

`/touch/index.html` is NOT retired by this guard and must not be deleted: it is
still the login destination for every non-estate touch page (`js/auth.js`) and
for non-kiosk browsers (`home.html`'s 401 bounce). The contract asserted here is
narrower — *when the host page offers the estate card, the voice path uses it*.

Static guard, in the spirit of tests/unit/test_ui_auth_routes_exist.py: the
relationship is a cross-file one (executor calls a hook home.html defines), so
either side could drift silently. No browser or running service required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO_ROOT = Path(__file__).resolve().parents[2]
UI_DIST = REPO_ROOT / "services" / "zoe-ui" / "dist"
EXECUTOR = UI_DIST / "js" / "touch-ui-executor.js"
ESTATE = UI_DIST / "touch" / "home.html"

AUTH_CARD_HOOK = "__showAuthCard"
OLD_LOGIN_PAGE = "/touch/index.html"
PANEL_PIN_ENDPOINT = "/api/panels/auth/pin"


def _function_body(source: str, signature: str) -> str:
    """Return the brace-matched body of `signature` (a `function foo(...) {`)."""
    start = source.index(signature)
    depth = 0
    for i in range(source.index("{", start), len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced braces after {signature!r}")


@pytest.fixture(scope="module")
def redirect_body() -> str:
    assert EXECUTOR.is_file(), f"missing {EXECUTOR}"
    return _function_body(EXECUTOR.read_text(), "function redirectToTouchLogin(")


def test_voice_login_prefers_the_estate_card_over_the_old_page(redirect_body: str):
    assert AUTH_CARD_HOOK in redirect_body, (
        "redirectToTouchLogin no longer consults the estate auth card — a "
        f"voice-forced login will navigate to the retired {OLD_LOGIN_PAGE}"
    )
    assert redirect_body.index(AUTH_CARD_HOOK) < redirect_body.index(OLD_LOGIN_PAGE), (
        "the estate card must be tried BEFORE falling back to the old page"
    )


def test_the_card_branch_guard_has_the_canonical_shape(redirect_body: str):
    """Pin the guard's exact form, not merely that the hook name appears.

    Cross-review's fair hit on the first version of this file: a *semantic*
    disable such as `false && typeof window.__showAuthCard === 'function'`
    satisfies a substring check and still navigates to the old page every time.
    Pinning the one canonical condition closes that without starting a
    regex-versus-bypass arms race (the repo has already rejected one — see the
    voice-gate notes in AGENTS.md); any rewrite must update this line
    deliberately.

    Honest limitation: this file is a TEXT guard. It proves the branch is
    written correctly, not that it executes — there is no JS lane in CI for
    `dist/js/`. Behavioural proof is the browser harnesses, which are local
    gates (services/zoe-ui/AGENTS.md).
    """
    assert "if (typeof window.__showAuthCard === 'function') {" in redirect_body, (
        "the estate-card guard is not in its canonical form — a rewritten or "
        "short-circuited condition can silently always navigate to the old page"
    )


def test_the_card_branch_actually_stops_the_navigation(redirect_body: str):
    """Showing the card is not enough — it must return, or we navigate anyway."""
    assert AUTH_CARD_HOOK in redirect_body, (
        "redirectToTouchLogin does not reference the estate auth card at all"
    )
    card_at = redirect_body.index(AUTH_CARD_HOOK)
    nav_at = redirect_body.index(OLD_LOGIN_PAGE)
    assert "return" in redirect_body[card_at:nav_at], (
        "the estate-card branch falls through to window.location.assign, so the "
        "user still lands on the retired login page"
    )


def test_the_old_page_remains_the_fallback(redirect_body: str):
    """Negative control: pages without the card (desktop) must still reach a login.

    If this fails alongside the tests above passing, the fix removed the
    fallback rather than making it conditional.
    """
    assert OLD_LOGIN_PAGE in redirect_body


def test_estate_defines_the_hook_the_executor_calls():
    """The cross-file half of the contract — rename one side and it breaks."""
    estate = ESTATE.read_text()
    assert f"window.{AUTH_CARD_HOOK}=" in estate.replace(" ", ""), (
        f"touch/home.html no longer exposes window.{AUTH_CARD_HOOK}; "
        "redirectToTouchLogin's preference silently degrades to the old page"
    )


def test_estate_card_resolves_the_held_voice_challenge():
    """Signing in does not release a held voice turn — answering it does.

    The estate card posts to the session-login endpoint. Without also answering
    the panel challenge, the voice command parked in `_PENDING_VOICE_IDENT` is
    never replayed and the user's spoken request is silently dropped.
    """
    estate = ESTATE.read_text()
    assert PANEL_PIN_ENDPOINT in estate, (
        "the estate card never answers the panel auth challenge, so a "
        "voice-forced login drops the command it was gating"
    )
    body = _function_body(estate, "function resolvePanelChallenge(")
    assert PANEL_PIN_ENDPOINT in body
    assert "zoe_panel_auth_challenge" in body, (
        "resolvePanelChallenge must read the challenge the executor stashed"
    )
    # Called, not merely defined.
    assert estate.count("resolvePanelChallenge") >= 2


def test_executor_stashes_the_challenge_the_card_reads(redirect_body: str):
    """Both sides must agree on the sessionStorage key, or the handoff is dead."""
    assert "zoe_panel_auth_challenge" in redirect_body
