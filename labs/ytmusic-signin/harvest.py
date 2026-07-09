"""Cookie harvester — reads the auth cookie out of the live sign-in browser.

LAB-ONLY. See ./AGENTS.md.

Mechanism: the rig launches Chromium with an open CDP endpoint
(--remote-debugging-port). After a human has signed in through the remote view,
this connects to that endpoint and asks the browser for *all* its cookies via
`Network.getAllCookies` (Playwright's ``context.cookies()`` under the hood). That
returns HttpOnly cookies too — which is the point: __Secure-3PAPISID is HttpOnly,
so page JavaScript can never read it, but the browser owner (us, over CDP) can.

This code NEVER sees or asks for a password. It only reads cookies the browser
already holds. The assembled header is a SECRET: written 0600 under SECRET_DIR,
proof printed is redacted.

    python3 harvest.py            # connect to the running rig and harvest
    python3 harvest.py --print    # also print the assembled header (DANGER: secret)
"""
from __future__ import annotations

import argparse
import asyncio
import sys

import common


async def collect_cookies_over_cdp(cdp_url: str) -> list[dict]:
    """Pull every cookie from a browser already running with an open CDP port."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(cdp_url)
        try:
            cookies: list[dict] = []
            # A persistent context shows up as the default browser context.
            for ctx in browser.contexts:
                cookies.extend(await ctx.cookies())
            return cookies
        finally:
            await browser.close()  # detaches CDP only; does not kill the rig browser


def report(names: list[str], header: str) -> bool:
    """Print redacted proof. Returns True iff the required cookie is present."""
    ok = common.has_required(names)
    print(f"harvested {len(names)} auth-domain cookies")
    print(f"  names: {', '.join(names) if names else '(none)'}")
    print(f"  {common.REQUIRED_COOKIE}: {'PRESENT ✓' if ok else 'MISSING ✗'}")
    print(f"  assembled Cookie header: {common.redact(header)}")
    return ok


async def main() -> int:
    ap = argparse.ArgumentParser(description="Harvest the YTMusic auth cookie from the rig browser.")
    ap.add_argument("--cdp", default=f"http://127.0.0.1:{common.CDP_PORT}", help="CDP endpoint of the rig browser")
    ap.add_argument("--print", action="store_true", dest="print_secret", help="ALSO print the raw header (secret!)")
    args = ap.parse_args()

    try:
        cookies = await collect_cookies_over_cdp(args.cdp)
    except Exception as exc:  # noqa: BLE001
        print(f"could not reach the rig browser at {args.cdp}: {exc}", file=sys.stderr)
        print("is the rig running?  python3 rig.py", file=sys.stderr)
        return 2

    header, names = common.assemble_cookie_header(cookies)
    ok = report(names, header)

    if not header:
        print("no auth cookies yet — has the human finished signing in?", file=sys.stderr)
        return 1
    if not ok:
        print(f"cookie set is incomplete (no {common.REQUIRED_COOKIE}) — Music "
              "Assistant will reject it. Finish the YTMusic login and retry.", file=sys.stderr)
        return 1

    path = common.store_cookie_header(header)
    print(f"stored secret -> {path} (0600)")
    if args.print_secret:
        print("--- RAW COOKIE HEADER (SECRET) ---")
        print(header)
    print("next: python3 validate.py   (saves it to Music Assistant and plays a track)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
