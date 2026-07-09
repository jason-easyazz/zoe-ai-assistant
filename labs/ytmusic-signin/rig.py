"""Remote, phone-drivable sign-in browser for YouTube Music.

LAB-ONLY. Hand-started process. See ./AGENTS.md.

Stack (all local, LAN-bound, torn down on exit):

    Xvfb :99            virtual X display for a *headful* Chromium
      └─ CloakBrowser   stealth Chromium, persistent profile, CDP port open
    x11vnc              mirrors :99 over VNC on the LAN
      └─ websockify     serves noVNC web client -> a phone browser can drive it

The human opens the printed http://<lan-ip>:6080 URL on their phone and signs
into YouTube Music *themselves*. This script never types, stores, or sees a
password — it only opens the door. After login, run ``harvest.py`` to read the
resulting cookie over the CDP port.

    python3 rig.py            # start the rig, print the phone URL, wait
    (Ctrl-C to tear everything down)

Headful needs a display; we use Xvfb + x11vnc + noVNC rather than a blind
headless run so a person can actually see and complete the Google login (and so
we can eyeball any anti-bot interstitial). Bound to the LAN IP only — treat the
VNC/noVNC ports as sensitive while the rig is up.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

import common

_PROCS: list[subprocess.Popen] = []
_CONTEXT = None  # CloakBrowser BrowserContext


def _require_binaries() -> None:
    missing = [b for b in ("Xvfb", "x11vnc", "websockify") if shutil.which(b) is None]
    if missing:
        print(f"missing binaries: {', '.join(missing)}", file=sys.stderr)
        print("install:  sudo apt-get install -y xvfb x11vnc websockify novnc", file=sys.stderr)
        sys.exit(1)


def _spawn(name: str, argv: list[str]) -> subprocess.Popen:
    print(f"  starting {name}: {' '.join(argv)}", flush=True)
    p = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _PROCS.append(p)
    return p


def _wait_port(host: str, port: int, timeout: float = 10.0) -> bool:
    import socket
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            if s.connect_ex((host, port)) == 0:
                return True
        time.sleep(0.2)
    return False


def _novnc_web_root() -> str:
    for cand in ("/usr/share/novnc", "/usr/share/webapps/novnc"):
        if os.path.isdir(cand):
            return cand
    return ""


def _start_display_stack(bind: str) -> None:
    # 1) virtual display
    _spawn("Xvfb", ["Xvfb", common.DISPLAY, "-screen", "0", common.XVFB_GEOMETRY, "-nolisten", "tcp"])
    time.sleep(1.0)
    # 2) VNC server mirroring the display — bound to LOCALHOST ONLY. Raw VNC never
    #    touches the LAN; websockify (below) is the single LAN-exposed port.
    _spawn("x11vnc", [
        "x11vnc", "-display", common.DISPLAY, "-rfbport", str(common.VNC_PORT),
        "-listen", "localhost", "-forever", "-shared", "-nopw", "-quiet", "-noxdamage",
    ])
    if not _wait_port("127.0.0.1", common.VNC_PORT):
        print("x11vnc did not open its port", file=sys.stderr)
    # 3) noVNC web client over websockify
    web = _novnc_web_root()
    argv = ["websockify"]
    if web:
        argv += ["--web", web]
    argv += [f"{bind}:{common.NOVNC_PORT}", f"127.0.0.1:{common.VNC_PORT}"]
    _spawn("websockify/noVNC", argv)
    _wait_port("127.0.0.1", common.NOVNC_PORT)


async def _launch_browser() -> None:
    global _CONTEXT
    from cloakbrowser import launch_persistent_context_async

    common.PROFILE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.environ["DISPLAY"] = common.DISPLAY  # headful Chromium draws into Xvfb

    _CONTEXT = await launch_persistent_context_async(
        str(common.PROFILE_DIR),
        headless=False,
        user_agent=common.USER_AGENT,        # None => coherent stealth UA
        viewport=None,                        # use the real window size (no CDP emulation)
        args=[
            f"--remote-debugging-port={common.CDP_PORT}",
            "--remote-debugging-address=127.0.0.1",
            "--start-maximized",
            "--window-position=0,0",
        ],
    )
    page = _CONTEXT.pages[0] if _CONTEXT.pages else await _CONTEXT.new_page()
    await page.goto(common.LOGIN_URL, wait_until="domcontentloaded")


def _teardown(*_a) -> None:
    print("\ntearing down rig…")
    global _CONTEXT
    if _CONTEXT is not None:
        try:
            loop = asyncio.new_event_loop()
            loop.run_until_complete(_CONTEXT.close())  # cloakbrowser also stops playwright
            loop.close()
        except Exception:
            pass
        _CONTEXT = None
    for p in reversed(_PROCS):
        try:
            p.terminate()
        except Exception:
            pass
    for p in reversed(_PROCS):
        try:
            p.wait(timeout=3)
        except Exception:
            p.kill()
    print("rig down. (profile persists at the secret dir for auto-refresh)")


async def main() -> int:
    _require_binaries()
    bind = common.lan_ip()
    common.ensure_secret_dir()

    print("=== YouTube Music remote sign-in rig (LAB) ===")
    _start_display_stack(bind)
    print("  launching stealth Chromium (headful, persistent profile)…")
    await _launch_browser()

    url = f"http://{bind}:{common.NOVNC_PORT}/vnc.html?autoconnect=1&resize=scale"
    print("\n" + "=" * 64)
    print("  OPEN THIS ON THE PHONE (same Wi-Fi):")
    print(f"    {url}")
    print("  Sign into YOUR dedicated YouTube Music Premium account there.")
    print("  This rig never types or sees your password.")
    print("  When YTMusic has loaded, in another shell run:")
    print("    python3 harvest.py")
    print("=" * 64 + "\n")

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
    return 0


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    finally:
        _teardown()
    raise SystemExit(rc)
