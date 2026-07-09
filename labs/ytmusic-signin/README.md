# YouTube Music remote sign-in (LAB SPIKE)

A friendlier way to connect YouTube Music to Zoe than the standard
"open DevTools, copy the `__Secure-3PAPISID` cookie by hand" dance.

**Status: lab spike, nothing wired to prod.** Everything is hand-started and
lives under `labs/ytmusic-signin/`. It imports the production Music Assistant
bridge read-only to save/validate/remove the provider; the runtime is untouched.

---

## Why this exists

YouTube Music has **no username/password and no OAuth** (Google removed OAuth in
Nov 2024). Music Assistant's `ytmusic` provider authenticates with a **browser
cookie** that must contain `__Secure-3PAPISID`, needs a **YTMusic Premium**
account, and needs a local **PO-token server** (the `ytmusic-potoken` container,
:4416). The cookie is **HttpOnly**, so page JavaScript can't read it — only the
browser owner can. Cookies also rotate/expire, so the real prize is
**auto-refresh**, not a one-time copy.

This spike proves Zoe can host the whole thing: a remote browser the user drives
from their phone, automatic cookie harvest (no script ever sees a password), and
a persistent profile that enables refresh.

## How it works

```
        phone browser (same Wi-Fi)
                │  http://<lan-ip>:6080/vnc.html
                ▼
   websockify + noVNC   ── LAN-exposed (the only LAN port) ──┐
                │  ws → 127.0.0.1:5900                        │
                ▼                                             │
        x11vnc (localhost only) ── mirrors ──► Xvfb :99       │  rig.py
                                                  │           │
                                    headful stealth Chromium  │
                                    (CloakBrowser, persistent │
                                     profile, CDP :9222)      │
                                                  │           │
   human signs into YouTube Music THEMSELVES  ────┘           │
                                                              │
   harvest.py ── CDP Network.getAllCookies ──► assemble Cookie header
                 assert __Secure-3PAPISID ──► ~/.zoe-ytmusic/cookie.header (0600)
                                                              │
   validate.py ── music_service.save_provider("ytmusic", {username, cookie})
                 (po_token URL auto-injected) ──► confirm library + players
```

**Reuses CloakBrowser** — Zoe's existing Hermes-owned stealth Chromium (49
fingerprint patches, ARM64/Jetson-supported) — via
`launch_persistent_context_async(headless=False, …)`. That gives us a headful,
persistent-profile browser with the best shot at not tripping Google's anti-bot,
plus a CDP port for harvesting. We did **not** build a new browser stack.

## One-time setup (already done on this host)

```bash
sudo apt-get install -y xvfb x11vnc websockify novnc
docker compose -f docker-compose.modules.yml up -d ytmusic-potoken   # :4416
curl -s http://localhost:4416/ping                                   # {"version":"1.3.1",…}
```

CloakBrowser + Playwright are already installed (`cloakbrowser==0.3.28`,
`playwright==1.59.0`, bundled Chromium 146.x).

## The joint live-login test (Jason + assistant, DEDICATED Premium account)

Run from the live checkout `/home/zoe/assistant` (needs `services/zoe-data/.env`
for MA creds), or set `MUSIC_ASSISTANT_URL`/`MUSIC_ASSISTANT_TOKEN` yourself.

1. **Preflight** (no login needed):
   ```bash
   cd labs/ytmusic-signin
   python3 selftest.py            # expect 11/11
   ```

2. **Start the rig**:
   ```bash
   python3 rig.py
   ```
   It prints a phone URL, e.g. `http://192.168.1.218:6080/vnc.html?autoconnect=1&resize=scale`.

3. **On the phone** (same Wi-Fi): open that URL. You'll see the Google sign-in
   page inside Zoe's browser. **Sign in yourself** with the dedicated YTMusic
   Premium account, complete any 2FA, and wait until `music.youtube.com` loads.
   *Zoe never types or sees your password.* If Google shows
   *"This browser or app may not be secure,"* stop — see Anti-bot notes below.

4. **Harvest the cookie** (in another shell):
   ```bash
   python3 harvest.py             # asserts __Secure-3PAPISID, stores 0600, redacted proof
   ```

5. **Validate against Music Assistant**:
   ```bash
   python3 validate.py --username you@gmail.com
   ```
   Confirms the PO-token server, saves the `ytmusic` provider (po_token URL
   auto-injected by `music_service`), and checks the account's library resolves +
   players are available. Then ask Zoe (voice/chat) to *"play &lt;song&gt;"*.

6. **Undo** (fully reversible):
   ```bash
   python3 validate.py --remove
   ```

7. **Teardown**: `Ctrl-C` the rig. Xvfb/x11vnc/websockify/Chromium are killed;
   the **profile persists** under `~/.zoe-ytmusic/profile/` for refresh.

## Anti-bot signal (measured, no login required)

Cheap signal we can get alone: does a Zoe-controlled headful stealth Chromium
with a coherent UA reach Google's login without an immediate block?

**Observed 2026-07-09 on this host: REACHED LOGIN.** The rig's CloakBrowser
loaded `accounts.google.com/v3/signin/identifier` and rendered the normal email
form ("Email or phone", "Forgot email", "Use your Google Account") with **no
"this browser may not be secure" interstitial**. Screenshot saved to
`~/.zoe-ytmusic/anti_bot_probe.png`.

Caveats (honest risk):
- We stopped at the **email screen** — we did not submit credentials. Google's
  stricter anti-automation checks often fire **after** the password step or on a
  new device/IP, sometimes as a "verify it's you" challenge rather than a hard
  block. The real login is where residual risk lives.
- Re-run the probe any time: `xvfb-run -s "-screen 0 1280x800x24" python3 anti_bot_probe.py`.
- If Google *does* block: options are the real Chrome release channel
  (`channel="chrome"`), reducing automation surface further, or a manual cookie
  paste as the fallback (the flow this spike aims to replace).

## Cookie auto-refresh (the anti-expiry win over stock MA)

The whole point of the **persistent profile** (`~/.zoe-ytmusic/profile/`): once a
human has logged in once, the profile keeps a valid session. Google's SAPISID/
`__Secure-3PAPISID` family rotates but the *logged-in profile* keeps producing a
fresh valid set as long as the session isn't invalidated. So refresh is:

1. On a schedule (e.g. daily, well before MA reports auth failure), headlessly
   re-open the persistent profile:
   `launch_persistent_context_async(PROFILE_DIR, headless=True)` → `music.youtube.com`.
2. Re-run the harvest logic (`common.assemble_cookie_header`) to read the current
   cookie set from that profile.
3. If `__Secure-3PAPISID` is present and the header changed, call
   `music_service.save_provider("ytmusic", {username, cookie})` to update MA
   in place (idempotent — same instance).
4. If the profile session has died (no `__Secure-3PAPISID`, or YTMusic redirects
   to login), surface a "please re-sign-in" card and re-launch the rig.

This keeps YTMusic working across cookie rotation **without** the user ever
re-copying a cookie — the failure mode stock MA has today. Prototype note: steps
1–3 are a thin loop over the functions already in `harvest.py` + `validate.py`;
not wired here because prod integration is explicitly out of scope for the spike.
**Constraint:** don't log the dedicated account into YTMusic web anywhere else —
that invalidates the profile's cookie.

## Security posture

- No password is ever entered, requested, autofilled, stored, or logged by any
  script here. The human logs in through the remote view.
- The cookie is a secret: only ever written to `~/.zoe-ytmusic/cookie.header`
  (0600); proof output is redacted (length + prefix only).
- Only the noVNC/websockify port is LAN-exposed; raw VNC (x11vnc) is
  localhost-only and unauthenticated by design for a short-lived LAN session —
  treat the noVNC URL as sensitive while the rig is up, and tear it down after.

## Files

| File | Role |
|---|---|
| `rig.py` | Launch Xvfb + x11vnc + noVNC + headful CloakBrowser → Google/YTMusic login; print phone URL; teardown on exit. |
| `harvest.py` | Read cookies from the live browser over CDP, assert `__Secure-3PAPISID`, store 0600, redacted proof. |
| `validate.py` | Save the `ytmusic` provider in MA and confirm library/players; `--remove` to undo. |
| `selftest.py` | Offline proofs (VNC stack, HttpOnly harvest, MA auth, PO-token). |
| `anti_bot_probe.py` | Load Google login and report blocked vs reached (no login). |
| `common.py` | Shared config, LAN bind, secret paths, cookie assembly + redaction. |
