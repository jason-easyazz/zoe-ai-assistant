#!/usr/bin/env python3
"""Estate browser gate — the REAL touch/home.html in a REAL Chromium, against the LIVE backend.

Why this exists: the estate's Playwright harnesses fulfil network routes and serve the page
from disk, so they never see the nginx CSP — which is how #1536 ("orb opens the mic, not a
keyboard") shipped green while every orb tap on the real panel opened the typed bar for
months. This gate serves the WORKTREE's home.html + touch-ui-executor.js with the WORKTREE's
nginx.conf CSP header, lets every /api call hit the live server as a fresh guest kiosk, and
checks the things a fake DOM cannot: CSP refusals, the who+PIN card's show/hide lifecycle,
a guest 403 not raising the card, the night-clock wake, settings content.

It drives a headless Chromium over CDP. Run that Chromium ON THE PI (the Jetson is RAM-tight):
  ssh pi@192.168.1.61 'systemd-run --user --unit=pw-headless --collect -p MemoryMax=1500M     /usr/lib/chromium/chromium --headless --remote-debugging-port=9223 --remote-debugging-address=127.0.0.1     --no-sandbox --disable-gpu --disable-dev-shm-usage --user-data-dir=/tmp/pw-headless --window-size=1280,720     --ignore-certificate-errors --no-first-run about:blank'
  setsid ssh -N -L 127.0.0.1:9223:127.0.0.1:9223 pi@192.168.1.61 </dev/null >/dev/null 2>&1 &
  python3 scripts/maintenance/estate_browser_verify.py --worktree /path/to/worktree
(127.0.0.1, not localhost — that resolves to ::1 and the tunnel is v4-only.)

Side effects: none on the real panel. The daemon call (localhost:7777/activate) is fulfilled
by the harness, never forwarded; the only writes are a throwaway guest session and a
panel bind for --panel-id (rejected unauthenticated). Exit 0 = PASS, 1 = a check failed.
"""
import argparse, json, os, re, sys, time
from playwright.sync_api import sync_playwright

ap = argparse.ArgumentParser()
ap.add_argument("--worktree", default=os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
ap.add_argument("--cdp", default="http://127.0.0.1:9223")
ap.add_argument("--base", default="https://192.168.1.218")
ap.add_argument("--panel-id", default="uitest-headless")
ap.add_argument("--screenshot", default="")
args = ap.parse_args()
W = os.path.join(args.worktree, "services", "zoe-ui")
BASE = args.base
html = open(f"{W}/dist/touch/home.html").read(); execjs = open(f"{W}/dist/js/touch-ui-executor.js").read()
csp = re.search(r'Content-Security-Policy "([^"]+)"', open(f"{W}/nginx.conf").read()).group(1)
fails = []
def check(cond,msg): print(("  ok   " if cond else "  FAIL ")+msg); fails.append(msg) if not cond else None
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp(args.cdp)
    ctx=b.new_context(ignore_https_errors=True, viewport={"width":1280,"height":720})
    ctx.add_init_script("try{sessionStorage.setItem('zoe_gb','1')}catch(e){}")   # skip the bootstrap's one-time reload
    pg=ctx.new_page()
    pg.clock.install()
    logs=[]; pg.on("console", lambda m: logs.append((m.type,m.text[:200])) if m.type=="error" else None)
    pg.on("pageerror", lambda e: logs.append(("PAGEERROR",str(e)[:200])))
    # serve the WORKTREE page + executor with the WORKTREE CSP; everything else is the live server
    pg.route("**/touch/home.html*", lambda r: r.fulfill(status=200, body=html, headers={"content-type":"text/html","content-security-policy":csp}))
    pg.route("**/js/touch-ui-executor.js*", lambda r: r.fulfill(status=200, body=execjs, headers={"content-type":"application/javascript","content-security-policy":csp}))
    activates=[]
    pg.route("**localhost:7777/**", lambda r: (activates.append(r.request.url), r.fulfill(status=200, body="ok")))
    pg.goto(BASE+"/touch/home.html?panel_id="+args.panel_id+"&kiosk=1", wait_until="load"); time.sleep(2); pg.clock.run_for(9000); time.sleep(2)
    st=pg.evaluate("()=>({authOn:!!document.querySelector('#authov.on'), sess:!!localStorage.getItem('zoe_session')})")
    check(not st["authOn"] and st["sess"], f"boots to ambient home as guest: {st}")
    print("--- 1. CSP: the orb reaches the daemon (fulfilled by the harness, never the real daemon)")
    pg.click("#orb", force=True); pg.clock.run_for(300); time.sleep(0.5)
    st=pg.evaluate("()=>({listening:document.getElementById('orb').classList.contains('listening'), cmdbar:document.getElementById('cmdbar').classList.contains('on')})")
    csp_err=[l for l in logs if "Content Security Policy" in l[1]]
    check(len(activates)==1 and not csp_err, f"orb tap POSTed /activate without a CSP refusal (activates={len(activates)}, csp_errors={len(csp_err)})")
    check(st["listening"] and not st["cmdbar"], f"orb shows listening, no keyboard bar: {st}")
    print("--- 2. a guest 403 (Contacts) must NOT raise the PIN card")
    pg.click("#apps"); pg.clock.run_for(600); time.sleep(0.4); pg.click('.ltile[data-id="person"]'); pg.clock.run_for(1500); time.sleep(2.5)
    st=pg.evaluate("()=>({authOn:!!document.querySelector('#authov.on'), sess:!!localStorage.getItem('zoe_session'), copy:(document.querySelector('.ctsign')||{}).textContent||''})")
    check(not st["authOn"], "Contacts as guest: no PIN card")
    check(st["sess"], "Contacts as guest: guest session kept (403 is not a dead session)")
    check("Tap to sign in" in st["copy"], f"Contacts shows tap-to-sign-in copy: {st['copy'][:60]!r}")
    print("--- 3. the card is dismissable: tap-to-sign-in → Not now → tap outside → auto-hide after the TTL")
    pg.click(".ctsign", force=True); pg.clock.run_for(200); time.sleep(0.3)
    check(pg.evaluate("()=>!!document.querySelector('#authov.on')"), "card opens from the contacts prompt")
    check(pg.evaluate("()=>getComputedStyle(document.getElementById('auBack')).display!=='none'"), "'Not now' is visible on the kiosk")
    pg.click("#auBack", force=True); pg.clock.run_for(200); time.sleep(0.3)
    check(pg.evaluate("()=>!document.querySelector('#authov.on')"), "'Not now' hides the card")
    pg.evaluate("()=>window.__showAuthCard()"); pg.clock.run_for(200); time.sleep(0.3)
    pg.mouse.click(20, 20); pg.clock.run_for(200); time.sleep(0.3)
    check(pg.evaluate("()=>!document.querySelector('#authov.on')"), "a tap outside hides the card")
    pg.evaluate("()=>window.__showAuthCard()"); pg.clock.run_for(200); time.sleep(0.3)
    check(pg.evaluate("()=>!!document.querySelector('#authov.on')"), "card re-opens cleanly (listeners wired once)")
    pg.clock.run_for(121000); time.sleep(0.5)
    check(pg.evaluate("()=>!document.querySelector('#authov.on')"), "card auto-hides after the 120 s challenge TTL")
    print("--- 4. the PIN pad still works after repeated show/hide (no duplicated key handlers)")
    pg.evaluate("()=>window.__showAuthCard()"); pg.clock.run_for(500); time.sleep(1.5)
    pg.click('.au[data-u="jason"]'); pg.clock.run_for(100); time.sleep(0.2)
    pg.click('.akey[data-k="1"]'); pg.clock.run_for(100); time.sleep(0.2)
    filled=pg.evaluate("()=>document.querySelectorAll('#auDots .adot.f').length")
    check(filled==1, f"one key press fills exactly one dot (got {filled})")
    pg.click("#auBack", force=True); pg.clock.run_for(200); time.sleep(0.3)
    print("--- 5. the settings Household section no longer offers the fake 'Link a device' code")
    pg.click("#apps"); pg.clock.run_for(600); time.sleep(0.4); pg.click('.ltile[data-id="settings"]'); pg.clock.run_for(1500); time.sleep(1.5)
    check(pg.evaluate("()=>!document.querySelector('.srow2[data-act=\"pair\"]')") , "no 'Link a device' row")
    print("--- 6. voice is activity: a transcript on the night clock wakes the panel")
    pg.click("#apps"); pg.clock.run_for(600); time.sleep(0.4); pg.click('.ltile[data-id="sleep"]'); pg.clock.run_for(1500); time.sleep(1.5)
    check(pg.evaluate("()=>!!document.getElementById('slClock') && document.getElementById('dock').classList.contains('hide')"), "on the sleep surface, dock hidden")
    pg.evaluate("()=>window.ZoeEstateVoice.transcript('what time is it')"); pg.clock.run_for(300); time.sleep(0.5)
    st=pg.evaluate("()=>({dockHidden:document.getElementById('dock').classList.contains('hide'), heard:(document.querySelector('#dbody .dheard')||{}).textContent||'', clock:!!document.getElementById('hClock')})")
    check(not st["dockHidden"] and "what time is it" in st["heard"] and st["clock"], f"transcript left the night clock and shows Heard on the home dock: {st}")
    if args.screenshot: pg.screenshot(path=args.screenshot)
    noise = [l for l in logs if "Content Security" not in l[1] and "/ws/push" not in l[1] and "status of 403" not in l[1]]
    check(not noise, f"no unexpected console errors: {noise[:5]}")
    ctx.close()
print("RESULT:", "PASS" if not fails else f"FAIL {len(fails)}")
sys.exit(1 if fails else 0)
