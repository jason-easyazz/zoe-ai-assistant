"""Offline self-test — everything provable WITHOUT a Google login.

LAB-ONLY. See ./AGENTS.md.

Proves the load-bearing mechanics so the only untested step left is the human
login itself:
  A. VNC stack + noVNC web assets are installed.
  B. CloakBrowser + Playwright import and a stealth context launches.
  C. The harvester reads HttpOnly cookies (the class __Secure-3PAPISID belongs
     to) and correctly flags the required cookie — proven against a synthetic
     cookie set, no login needed.
  D. Music Assistant is reachable AND authenticates (players/all returns).
  E. The PO-token generator answers /ping.

    python3 selftest.py

Exit 0 = every critical check passed.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path

import common

REPO_ROOT = Path(__file__).resolve().parents[2]
ZOE_DATA = REPO_ROOT / "services" / "zoe-data"

_results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    _results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


async def test_httponly_harvest() -> None:
    """C. Add HttpOnly cookies to a stealth context, then read them back via the
    same API harvest.py uses (context.cookies()). Proves we can read cookies page
    JS cannot, and that __Secure-3PAPISID is detected + assembled."""
    from cloakbrowser import launch_context_async

    ctx = await launch_context_async(headless=True)
    try:
        await ctx.add_cookies([
            {"name": common.REQUIRED_COOKIE, "value": "SECRET3PAPISIDVALUE",
             "domain": ".youtube.com", "path": "/", "httpOnly": True, "secure": True},
            {"name": "SAPISID", "value": "sapisidval", "domain": ".google.com",
             "path": "/", "httpOnly": False, "secure": True},
            {"name": "unrelated", "value": "x", "domain": ".example.com", "path": "/"},
        ])
        cookies = await ctx.cookies()
        req = next((c for c in cookies if c.get("name") == common.REQUIRED_COOKIE), None)
        check("C1 reads HttpOnly cookie back", bool(req) and bool(req.get("httpOnly")),
              "httpOnly flag preserved" if req and req.get("httpOnly") else "not found/flag lost")
        header, names = common.assemble_cookie_header(cookies)
        check("C2 assembles auth-domain header (drops off-domain)",
              common.REQUIRED_COOKIE in names and "unrelated" not in names,
              f"names={names}")
        check("C3 detects required cookie", common.has_required(names))
    finally:
        await ctx.close()


async def test_services() -> None:
    """D+E. Reuse the production bridge to hit MA + the PO-token generator."""
    # load MA creds like validate.py does
    env_path = ZOE_DATA / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                if k.strip() in ("MUSIC_ASSISTANT_URL", "MUSIC_ASSISTANT_TOKEN") and k.strip() not in os.environ:
                    os.environ[k.strip()] = v.strip()

    sys.path.insert(0, str(ZOE_DATA))
    import music_service as ms

    players = await ms.get_players()
    check("D Music Assistant authenticates (players/all)", isinstance(players, list) and len(players) >= 0,
          f"{len(players)} player(s)")
    # get_entries confirms the save path's fields exist
    entries = await ms._ma("config/providers/get_entries", provider_domain="ytmusic")
    keys = [e.get("key") for e in (entries or []) if isinstance(e, dict)]
    check("D2 ytmusic save fields present", {"username", "cookie", "po_token_server_url"} <= set(keys),
          f"keys={keys}")
    url = ms._ytmusic_potoken_url()
    reachable = await ms._potoken_reachable(url)
    check("E PO-token generator /ping", reachable, url)


def test_env() -> None:
    """A+B. Tooling present."""
    for b in ("Xvfb", "x11vnc", "websockify"):
        check(f"A {b} installed", shutil.which(b) is not None)
    web = any(os.path.isdir(p) for p in ("/usr/share/novnc", "/usr/share/webapps/novnc"))
    check("A noVNC web assets present", web)
    try:
        import cloakbrowser  # noqa: F401
        import playwright  # noqa: F401
        check("B cloakbrowser + playwright import", True, f"chromium {cloakbrowser.CHROMIUM_VERSION}")
    except Exception as exc:  # noqa: BLE001
        check("B cloakbrowser + playwright import", False, str(exc))


async def main() -> int:
    print("=== ytmusic sign-in spike — offline self-test ===")
    test_env()
    try:
        await test_httponly_harvest()
    except Exception as exc:  # noqa: BLE001
        check("C harvester HttpOnly test", False, str(exc))
    try:
        await test_services()
    except Exception as exc:  # noqa: BLE001
        check("D/E services test", False, str(exc))

    failed = [n for n, ok, _ in _results if not ok]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} checks passed.")
    if failed:
        print("FAILED: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
