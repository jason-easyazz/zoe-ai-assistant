#!/usr/bin/env python3
"""Desktop browser gate — the desktop pages in a REAL Chromium against a LIVE Zoe edge.

Logged OUT: every data page must navigate exactly once to /index.html with ZERO /api
requests and ZERO /ws/push attempts (the synchronous members-only gate in js/auth.js);
public pages stay put. Logged IN (a member session minted with a PIN from $DESKTOP_GATE_PIN via
/api/auth/login/passcode): every data page loads with no "Session Expired" overlay, no
401/403, no ReferenceError/TypeError, one push URL carrying session_id, chat lists the
member's sessions and never the guest probe pool, and a GUEST session is treated as
logged out. Reads only; the member session is used to LOAD pages, never to write.

Drives a headless Chromium over CDP — run it ON THE PI (the Jetson is RAM-tight), see
scripts/maintenance/estate_browser_verify.py for the tunnel/launch recipe. Against a
TEST origin (e.g. https://192.168.1.218:8443 with a second nginx container) zoe-data
rejects the push handshake as cross-origin, so the socket check only requires a single
URL with bounded reconnects; run it against the real origin (--base https://192.168.1.218)
after deploy to see exactly one socket.

  DESKTOP_GATE_PIN=<member pin> python3 scripts/maintenance/desktop_browser_verify.py --base https://192.168.1.218:8443
"""
import argparse
ap = argparse.ArgumentParser()
ap.add_argument("--base", default="https://192.168.1.218:8443")
ap.add_argument("--cdp", default="http://127.0.0.1:9223")
ap.add_argument("--user", default="jason")
ap.add_argument("--username", default="Jason")
args = ap.parse_args()
import json, sys, time, os
from playwright.sync_api import sync_playwright
BASE=args.base.rstrip('/')
DATA=["/dashboard.html","/chat.html","/calendar.html","/lists.html","/notes.html","/people.html","/memories.html","/journal.html","/updates.html"]
PUBLIC=["/index.html","/jukebox.html","/404.html","/touch/home.html?kiosk=1&panel_id=uitest-headless"]
fails=[]
def check(c,m): print(("  ok   " if c else "  FAIL ")+m); (not c) and fails.append(m)
def load(b, path, session=None, wait=5):
    ctx=b.new_context(ignore_https_errors=True, viewport={"width":1440,"height":900})
    if session: ctx.add_init_script("localStorage.setItem('zoe_session', JSON.stringify(%s))" % json.dumps(session))
    ctx.add_init_script("try{sessionStorage.setItem('zoe_gb','1')}catch(e){}")
    pg=ctx.new_page(); api=[]; ws=[]; errs=[]; navs=[]; statuses=[]
    pg.on("request", lambda r: api.append(r.method+" "+r.url.replace(BASE,"")) if "/api/" in r.url else None)
    pg.on("websocket", lambda w: ws.append(w.url.replace(BASE.replace("https","wss"),"")))
    pg.on("response", lambda r: statuses.append((r.status, r.url.replace(BASE,""))) if r.status>=400 else None)
    pg.on("console", lambda m: errs.append(m.text[:160]) if m.type=="error" else None)
    pg.on("framenavigated", lambda f: navs.append(f.url.replace(BASE,"")) if f==pg.main_frame else None)
    pg.goto(BASE+path, wait_until="load", timeout=25000); time.sleep(wait)
    out=dict(final=pg.url.replace(BASE,""), api=api, ws=ws, errs=errs, navs=navs, statuses=statuses, pg=pg, ctx=ctx)
    return out
with sync_playwright() as p:
    b=p.chromium.connect_over_cdp(args.cdp)
    print("=== LOGGED OUT: every data page redirects once to /index.html with ZERO /api calls and ZERO sockets")
    for path in DATA:
        r=load(b, path, wait=4)
        check(r["final"]=="/index.html", f"{path}: lands on /index.html (got {r['final']})")
        check(len(r["api"])==0, f"{path}: zero /api requests (got {r['api'][:3]})")
        check(len(r["ws"])==0, f"{path}: zero /ws/push attempts (got {len(r['ws'])})")
        r["ctx"].close()
    for path in PUBLIC:
        r=load(b, path, wait=4)
        check(r["final"].split("?")[0]==path.split("?")[0], f"public {path} stays put (got {r['final']})")
        check(not any("/ws/push?channel=all" in w for w in r["ws"]), f"public {path}: no channel=all socket")
        r["ctx"].close()
    print("=== LOGGED IN (member session via passcode) — pages load, ONE push socket each, no overlay, chat lists MY sessions")
    import urllib.request, ssl
    ctxssl=ssl.create_default_context(); ctxssl.check_hostname=False; ctxssl.verify_mode=ssl.CERT_NONE
    body=json.dumps({"username":args.username,"user_id":args.user,"passcode":os.environ["DESKTOP_GATE_PIN"]}).encode()
    req=urllib.request.Request(BASE+"/api/auth/login/passcode", data=body, headers={"Content-Type":"application/json"})
    d=json.loads(urllib.request.urlopen(req, context=ctxssl, timeout=15).read())
    assert d.get("success") and d.get("session_id"), "passcode login failed"
    session={"session_id":d["session_id"],"user_id":d.get("user_id","jason"),"username":"Jason","role":d.get("role","admin")}
    print("  (signed in as", session["user_id"], "role", session["role"], ")")
    for path in DATA:
        r=load(b, path, session=session, wait=7)
        pg=r["pg"]
        check(r["final"]==path, f"{path}: stays on the page (got {r['final']})")
        overlay=pg.evaluate("()=>!!document.body.innerText.includes('Session Expired')")
        check(not overlay, f"{path}: no 'Session Expired' overlay")
        pushes=[w for w in r["ws"] if "/ws/push" in w]
        # On this TEST origin (port 8443) zoe-data rejects the handshake as cross-origin, so the hub
        # reconnects with backoff (2 s, 4 s): every attempt must be the SAME single URL carrying
        # session_id, and never more than the backoff allows. The one-socket-per-page contract is
        # pinned by test_desktop_wave3.js and re-checked on the real origin after deploy.
        check(len(set(pushes))==1 and "session_id=" in pushes[0] and len(pushes)<=3, f"{path}: one push URL with session_id, bounded reconnects (got {len(pushes)}: {set(p.split('session_id=')[0] for p in pushes)})")
        bad=[s for s in r["statuses"] if s[0] in (401,403)]
        check(len(bad)==0, f"{path}: no 401/403 (got {bad[:4]})")
        refs=[e for e in r["errs"] if "ReferenceError" in e or "TypeError" in e]
        check(len(refs)==0, f"{path}: no ReferenceError/TypeError in console (got {refs[:2]})")
        if path=="/chat.html":
            txt=pg.evaluate("()=>(document.getElementById('sessionsList')||{}).innerText||''")
            check("Sign in to see your chats" not in txt, "chat: sessions list is rendered for a member")
            check("s1probe" not in txt and "What tools do you have" not in txt, "chat: the guest probe pool is NOT listed")
        if path=="/dashboard.html":
            vap=[s for s in r["api"] if "vapid-public-key" in s]
            check(len(vap)>=1, "dashboard: push subscribe path reached the VAPID key as a member")
        r["ctx"].close()
    # negative control: a GUEST session must be treated as logged out
    r=load(b, "/dashboard.html", session={"session_id":"guest-fake","user_id":"guest","role":"guest"}, wait=4)
    check(r["final"]=="/index.html", "guest session on desktop → /index.html (members only)")
    r["ctx"].close()
print("RESULT:", "PASS" if not fails else f"FAIL {len(fails)}"); sys.exit(1 if fails else 0)
