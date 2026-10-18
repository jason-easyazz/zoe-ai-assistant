# Zoe's own iPhone app - design and the box-side API contract (M0)

Status: **design + box side M0 built flag-dark** (`ZOE_PHONE_APP`, default off). No Swift code yet: the app cannot be built or
signed without a Mac and an Apple Developer account (owner steps, section 7).

Decision (owner, 2026-10-18): build Zoe's OWN native iPhone app, not Element X. Research: `lane-M-iphone-app.md` in the
architecture deep dive round 2 (`docs/research/architecture-deep-dive-2-2026-10-19/`, PR #2123) and deep dive 2026-10-18 section
8.4. This doc keeps only what the build needs.

## 1. What the app is

- **Native Swift / SwiftUI**, one app target plus extensions (Notification Service Extension, widget / Live Activity). Deployment
  target iOS 18. Every hard requirement (PushKit + CallKit, NSE, PushToTalk, Live Activity, CoreBluetooth state restoration,
  App Intents, AccessorySetupKit) is a native API or a native extension, so a cross-platform layer would only add a second stack.
- **Adults only.** Guests and children keep the PWA (and Telegram); the box refuses to pair them.
- **Talks only to the household's own box**: REST + WebSocket to `ZOE_PHONE_APP_BASE_URL` (the relay's public name when that
  lands, or a LAN name with the household CA pinned in the app). No Firebase, no third-party backend.
- **Distribution**: TestFlight internal testing for the family (no review, builds expire after 90 days, a CI job re-uploads every
  60 days). Ad Hoc is the fallback.

## 2. Phases

| Phase | App | Box |
|---|---|---|
| **M0** | pairing by panel QR, CA-in-app TLS, Keychain, Secure Enclave device key, APNs token registration, NSE, notices inbox, household lists (the fridge rule), Face ID lock | **this PR**: pairing, device registry, push-token registry (flag-dark). Next: APNs sender, member-authenticated list / notice endpoints for a device credential |
| M1 | PushToTalk channel for the intercom, Live Activity for "incoming intercom", PA alert pushes, App Intents ("ask Zoe") | PTT push type, routing through the intercom's rules (`intercom.decide`: guests never, quiet hours, ring first, adults drop in) |
| M2 | calls: CallKit + PushKit (both delegates incl. the iOS 26.4 `mustReport` one), LiveKit Swift SDK | signalling + a join-token endpoint; LiveKit + TURN on the411. One-week ring acceptance test on the family's phones, app killed, screen locked |
| M3 | Omi pendant: AccessorySetupKit onboarding, CoreBluetooth with restoration, custody sync (the pendant takes ONE connection, so the phone syncs only on "Sync now") | a member-authenticated variant of `POST /api/omi/recordings`, a BLE lock hand-off with the panel Pi |

Calls on iPhone take the **Plan B ring path** (a direct PushKit VoIP push the box knows is a call), not the Matrix one: Matrix
calls in E2EE rooms need the managed `com.apple.developer.usernotifications.filtering` entitlement, which Apple grants at its
discretion with no stated turnaround. See the Matrix lab (`labs/comms-matrix/`) for the server side.

## 3. Box-side API contract (M0, built)

All routes are under `/api/phone` and answer **404 while `ZOE_PHONE_APP` is not `on`**. JSON bodies. Errors are
`{"detail": "<short reason>"}`; no reason ever contains a secret.

| Method + path | Auth | Request | Response |
|---|---|---|---|
| `POST /pair/start` | signed-in adult member session (not the panel principal) | none | `{"pair_payload": "zoe-pair:v1:<b64url JSON>", "expires_in_s": 300}`; 403 child / guest, 429 five codes already waiting, 503 box URL not configured |
| `POST /pair/complete` | none: the code is the credential | `{"code", "device_name", "platform": "ios", "public_key": "<b64 P-256: 65-byte X9.63 or 91-byte SPKI>", "app_version"}` | `{"device_id", "device_token"}` (the token is shown ONCE); 401 unknown / used / expired code (one answer for all three), 400 bad key or platform (the code is not spent), 409 ten phones already |
| `GET /me` | header `X-Zoe-Phone-Token` | none | `{"device_id", "device_name", "member_name"}`; 401 unpaired or revoked |
| `PUT /push` | `X-Zoe-Phone-Token` | `{"push_type": "alert"\|"voip"\|"pushtotalk"\|"liveactivity", "token": "<hex>", "environment": "sandbox"\|"production", "topic"}` | `{"ok": true}`; 400 when the topic is not `ZOE_PHONE_APP_BUNDLE_ID` + Apple's suffix for that type (`""`, `.voip`, `.voip-ptt`, `.push-type.liveactivity`) |
| `DELETE /push/{push_type}` | `X-Zoe-Phone-Token` | none | `{"ok": true, "removed": bool}` |
| `GET /devices` | signed-in member | none | `{"devices": [{"device_id", "name", "platform", "app_version", "paired_at", "last_seen_at", "push_registered"}]}` (own phones only, never a hash, key or token) |
| `DELETE /devices/{id}` | signed-in member (own) or admin (any) | none | `{"ok": true}`; 404 otherwise. Deletes the phone's push tokens |

The QR payload: `zoe-pair:v1:` + base64url (no padding) of `{"v": 1, "u": "<box URL>", "c": "<code>", "fp": "<CA SHA-256 hex>"}`
(`fp` only when `ZOE_PHONE_APP_CA_SHA256` is set). The app verifies the box's certificate chains to a CA with that fingerprint
(`SecTrustSetAnchorCertificates`, anchor-only), stores the CA in the Keychain, and uses it for REST and the WebSocket.

Configuration (operator, env of zoe-data): `ZOE_PHONE_APP` (off | on), `ZOE_PHONE_APP_BASE_URL`, `ZOE_PHONE_APP_CA_SHA256`,
`ZOE_PHONE_APP_BUNDLE_ID`.

Storage: alembic `0058_phone_app` - `phone_devices` (SHA-256 of the credential, the public key, timestamps, revoked) and
`phone_push_tokens` (one per device and push type). Pairing codes live 300 s in the zoe-data process only, hashed, single use.

## 4. Security model

- **Codes**: 256-bit, single use, 300 s, at most five waiting per member, only the hash in memory. An expired, used or unknown
  code all get the same 401.
- **Device credential**: 256-bit bearer token, stored as SHA-256; revocation is immediate. The app keeps it in the Keychain
  (`kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`, shared access group with the NSE).
- **Device key**: the Secure Enclave P-256 public key is stored at pairing. M0 does not use it; the next phase adds a signed
  server challenge for step-up actions (show private results on a panel, approve a day review), which makes the phone a strong
  factor instead of a bearer token.
- **Push payloads are content-free** ("notice available" + an opaque id); the app or its NSE fetches the content from the box.
  Apple sees only that the box pushed something to a device at a time. VoIP pushes are for real ringing calls only (iOS 26
  terminates apps that receive a VoIP push and do not report a call).
- **APNs key**: a topic-specific production `.p8` limited to Zoe's bundle ids, stored in the household vault (#2100), never in
  the repo or a plain env file. The box calls `api.push.apple.com:443` directly (outbound HTTP/2; CGNAT is irrelevant). Friends'
  households would go through a push relay on the411 instead, so the key never sits in their boxes.

## 5. Not built yet (box side)

- The APNs sender (ES256 JWT refreshed every 20-60 min, HTTP/2, `410 Unregistered` drops the token). Needs the `.p8`.
- Member-authenticated list / notice endpoints that accept a device credential.
- A user erase must also revoke the member's phones (today neither the phones nor the web-push subscriptions are in the erase
  path).
- The panel / desktop card that renders `pair_payload` as a QR (the payload is returned in a POST body, never in a URL).

## 6. Repo layout for the app (when the Mac exists)

`apps/ios/` with its own DOX `AGENTS.md` and a `.zoe/manifest.json` entry. Xcode project via XcodeGen (`project.yml`) so the
project file is generated, not hand-merged. CI on GitHub-hosted macOS runners (the repo is public); a real iPhone for anything
touching push, CallKit or Bluetooth. Never commit the `.p8`, certificates or provisioning profiles.

## 7. Owner steps

1. **Enrol in the Apple Developer Program** (individual, US$99/yr). Only the Account Holder can request managed capabilities.
2. **Create an APNs key**: a topic-specific production `.p8` for Zoe's bundle ids; put it in the vault, note the Key ID and Team
   ID. Do not paste it anywhere else.
3. **Request the PushToTalk entitlement** in week 1 (one developer report says App ID registration for PushToTalk needed a
   request; turnaround unknown). Only request the notification-filtering entitlement if Matrix calls are ever wanted.
4. **The Mac**: Apple silicon, **macOS Tahoe 26.2 or later** for Xcode 26.6 (26.6 or later for Xcode 27.1, which is Apple
   silicon only). Confirm the owner's Mac meets this, or plan the Mac mini that would double as the build host.
5. Choose the bundle id (for example `life.zoe.app`); set `ZOE_PHONE_APP_BUNDLE_ID`, `ZOE_PHONE_APP_BASE_URL` (and
   `ZOE_PHONE_APP_CA_SHA256` for a private CA) in zoe-data's env; flip `ZOE_PHONE_APP=on` when the first TestFlight build exists.
