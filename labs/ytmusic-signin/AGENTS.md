# ytmusic-signin — friendlier YouTube Music sign-in (LAB SPIKE)

## Purpose

De-risk a phone-drivable YouTube Music sign-in for Zoe that beats the standard
"copy `__Secure-3PAPISID` out of DevTools" flow. Proves Zoe can (1) present a
remote browser a human signs into themselves, (2) **auto-harvest the resulting
auth cookie** without any script touching a password, (3) hand it to Music
Assistant's `ytmusic` provider and confirm playback, and (4) sketch cookie
**auto-refresh** off a persistent profile. Records here are findings, not
contracts.

## Ownership

The operator running the spike (Jason + assisting agent). Hand-started only.

## Local Contracts

- Runs entirely under `labs/ytmusic-signin/`. It **imports** the production
  bridge `services/zoe-data/music_service.py` read-only to save/validate/remove
  the provider; nothing is wired back into the runtime.
- All secrets + browser profile live under `$ZOE_YTMUSIC_SECRET_DIR`
  (default `~/.zoe-ytmusic/`, mode 0700) — **outside** the repo.
- Music Assistant creds are read from `services/zoe-data/.env` (same as the live
  service) or the environment. So `validate.py`/`selftest.py` expect to run from
  a checkout that has the live `.env`, or with `MUSIC_ASSISTANT_URL`/`_TOKEN` set.
- Reuses **CloakBrowser** (`launch_persistent_context_async`, headful) — the
  Hermes-owned stealth Chromium already installed — not a new browser stack.

## Forbidden

- **Never** enter, request, autofill, store, or log a Google password or any
  credential. The human logs in themselves through the remote view; this code
  only ever *reads the resulting cookie*. Non-negotiable.
- **Never** commit, log in full, or write to a tracked file the harvested cookie
  or any secret. Redact (`common.redact`) always; secrets stay under SECRET_DIR.
- **Never** wire any of this into `zoe-data`, skybridge, the prod music setup
  flow, a systemd unit, a Docker image, or CI. Hand-started processes only.
- **Never** run against Jason's primary Google account. Use a **dedicated**
  YouTube Music Premium account (logging that account into YTMusic web elsewhere
  invalidates the harvested cookie).
- Do **not** restart or disrupt `zoe-data`, `zoe-music-assistant`, or the panel.
  Bringing up the additive `ytmusic-potoken` container is allowed.
- Do **not** expose raw VNC to the LAN — only the noVNC/websockify port is
  LAN-bound; x11vnc stays on localhost.

## Work Guidance

- `rig.py` launch → human login on phone → `harvest.py` → `validate.py`.
- Self-test everything provable without a login first: `python3 selftest.py`.
- The provider save is reversible: `python3 validate.py --remove`.
- Pinned deps in `requirements.txt`; the stack is already installed on this host.

## Verification

- `python3 selftest.py` → 11/11 (VNC stack, CloakBrowser, HttpOnly-cookie
  harvest, MA auth, PO-token /ping). Last run: 11/11 pass.
- `python3 anti_bot_probe.py` (or the rig + CDP) → anti-bot verdict. Last run:
  **REACHED LOGIN** (Google sign-in form rendered, no insecure-browser block).
- Repo structure validator: `labs/**/*` is an approved manifest pattern.

## Child DOX Index

- (none) — single-directory spike; `README.md` is the runbook record.
