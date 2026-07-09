"""Anti-bot probe — the one cheap signal we CAN get without any credentials.

LAB-ONLY. See ./AGENTS.md.

Opens the Google sign-in page in the *same* headful stealth Chromium the rig
uses (under Xvfb) and reports whether Google shows the email field normally, or
an interstitial like "This browser or app may not be secure" / "Couldn't sign
you in". It stops at the email screen — it NEVER types an email or password.

    python3 anti_bot_probe.py            # needs an X display (run under the rig's Xvfb, or xvfb-run)
    xvfb-run -s "-screen 0 1280x800x24" python3 anti_bot_probe.py

Writes a screenshot to the gitignored secret dir (the login page itself carries
no secret, but we keep all rig artifacts there by policy).
"""
from __future__ import annotations

import asyncio
import sys

import common

# Signals Google shows a controlled/automated browser instead of the login form.
_BLOCK_MARKERS = (
    "this browser or app may not be secure",
    "couldn't sign you in",
    "couldn’t sign you in",
    "try using a different browser",
    "not secure",
)
_OK_MARKERS = (
    "email or phone",
    "sign in",
    "use your google account",
    "forgot email",
)


async def main() -> int:
    from cloakbrowser import launch_persistent_context_async

    common.ensure_secret_dir()
    common.PROFILE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)

    ctx = await launch_persistent_context_async(
        str(common.PROFILE_DIR),
        headless=False,
        user_agent=common.USER_AGENT,
        viewport=None,
        args=["--window-size=1280,800", "--window-position=0,0"],
    )
    try:
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        await page.goto(common.LOGIN_URL, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(3500)  # let any interstitial render
        try:
            body = (await page.inner_text("body")).lower()
        except Exception:
            body = ""
        title = (await page.title()) or ""
        final_url = page.url

        shot = common.SECRET_DIR / "anti_bot_probe.png"
        await page.screenshot(path=str(shot))

        blocked = [m for m in _BLOCK_MARKERS if m in body]
        looks_ok = any(m in body for m in _OK_MARKERS)

        print("=== anti-bot probe result ===")
        print(f"  final URL : {final_url}")
        print(f"  title     : {title}")
        print(f"  screenshot: {shot}")
        if blocked:
            print(f"  VERDICT   : BLOCKED — Google showed: {blocked}")
            print("  => a Zoe-controlled browser is refused; login flow needs rework "
                  "(real Chrome channel, less automation surface, or manual handoff).")
            return 3
        if looks_ok:
            print("  VERDICT   : REACHED LOGIN — email/sign-in form rendered, no 'insecure browser' block.")
            print("  => the human can proceed to sign in from the phone.")
            return 0
        print("  VERDICT   : INCONCLUSIVE — no known marker matched; inspect the screenshot.")
        return 2
    finally:
        await ctx.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
