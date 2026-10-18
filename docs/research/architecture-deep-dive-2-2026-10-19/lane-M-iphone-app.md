# Lane 2-M: Zoe's own iPhone app (research, 2026-10-19)

Scope: the owner's 2026-10-18 decision ("build Zoe's own iPhone app, not Element X") against the comms design in
`docs/research/architecture-deep-dive-2026-10-18.md` section 8.3/8.4 and `lane-F-comms-stack.md` section 4/4.1.

Confidence tags used on every claim:
- `[P]` primary source read today (Apple docs/forum posts by Apple DTS, vendor docs, the vendor's own repo or API).
- `[C]` read in source code cloned today (path given).
- `[S]` secondary (blog, aggregator, forum user report, search summary).
- `[M]` from memory / general knowledge, NOT re-verified today.
- `[E]` my estimate. `[U]` unverified.

Working files (untrusted clones, scratch only): `scratchpad/dl/{exios,sygnal,omi,lks}`; Apple doc text was read through
`developer.apple.com/tutorials/data/documentation/...json` because the normal pages are JS-rendered.

---------------------------------------------------------------------------------------------------

## 0. Answer first

1. **Distribution: Apple Developer Program (individual, US$99/yr) + TestFlight.** Family = internal testers (App Store Connect users,
   no review); friends' households = external testers by public link (first build per group goes through Beta App Review). Cost
   about US$99/yr plus one build upload at least every 90 days (automate it). Ad Hoc (100 devices/yr) is the fallback, the free
   Personal Team is dev-only, Apple Business Manager custom apps and the Enterprise program do not fit a family. Unlisted App Store
   distribution is the one non-expiring path for friends later, but it needs full App Review and a demo mode.
2. **Stack: native Swift/SwiftUI.** Every hard requirement (PushKit/CallKit, Notification Service Extension, PushToTalk, Live
   Activity, CoreBluetooth state restoration, App Intents, AccessorySetupKit) is native-API or lives in a separate native extension
   process, and the Omi team itself (Flutter) replaced flutter_blue_plus with a 1,848-line native Swift CoreBluetooth manager
   for iOS background reliability. Biggest risk: a single maintainer owning an iOS-only native app plus two Apple "managed
   capability" approvals (NSE filtering if Matrix, PushToTalk) with no published turnaround.
3. **Push: the Jetson can call APNs directly (yes).** APNs is an outbound HTTP/2 client call to `api.push.apple.com:443` (or 2197)
   authenticated with an ES256 JWT from the .p8 key, so CGNAT is irrelevant `[P]`. For friends' households put the key on a
   relay on the411, never in their boxes.
4. **Correction to the brief: Sygnal is AGPL-3.0 (dual-licensed with a commercial licence) since 0.16.0 (2025-07-18), not Apache-2.0**
   `[P]` (gh API licence field AGPL-3.0; `CHANGELOG.md` in the clone). Latest release v0.17.0 (2025-11-05).
5. **Calls: do the ring with the simplest path that works.** If the server knows it is a call (Plan B, native signalling), send a
   direct PushKit VoIP push (`apns-push-type: voip`, topic `<bundle>.voip`). If calls ride Matrix in E2EE rooms the server cannot
   tell, so Element X uses an alert push -> Notification Service Extension -> `CXProvider.reportNewIncomingVoIPPushPayload`
   -> PushKit delegate -> CallKit `[C]`. That second path needs the **managed entitlement
   `com.apple.developer.usernotifications.filtering`** which Apple grants at its discretion `[P]/[S]`.
6. **Omi pendant: the phone cannot be a co-equal sync path.** The Omi CV1 firmware sets `CONFIG_BT_MAX_CONN=1` `[C]`
   (`omi/firmware/omi/omi.conf:88`), so only one central holds the pendant at a time. The panel Pi stays the reliable sync; the
   phone is a foreground "sync now / live" companion that must release the link. Also, since iOS 26 a force-quit app is relaunched
   by Bluetooth state restoration only if it uses AccessorySetupKit `[P]` (TN3115).
7. **Recommended sequence:** M0 push + pairing + lists/notices (about 3k Swift LOC), M1 PTT intercom + Live Activity, M2 calls (Plan
   B native first; Matrix calls optional), M3 pendant. Build on GitHub-hosted macOS runners (the repo is public) or Xcode Cloud; a
   real iPhone is mandatory (no simulator for PushKit/CallKit/BLE). Needs a Mac on macOS Tahoe 26.2+ for Xcode 26.6, 26.6+ for Xcode 27.
8. **Android: do not pay the cross-platform tax now.** Native Kotlin later (about 60-80% of the iOS LOC `[E]`), PWA/Telegram until then.

---------------------------------------------------------------------------------------------------

## 1. Distribution without the public App Store

### 1.1 Facts

| Item | Fact | Source | Conf |
|---|---|---|---|
| Programme fee | US$99 per membership year; fee waivers for nonprofits/edu/government | https://developer.apple.com/programs/whats-included/ | P |
| TestFlight internal | up to 100 App Store Connect users, no review | https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview | P |
| TestFlight external | up to 10,000 testers (email or public link); the **first build added to a group goes to App Review**, later builds may not | same page | P |
| Build life | 90 days per build, then unavailable to testers | same page | P |
| Guideline 2.2 | "Any app submitted for beta distribution via TestFlight should be intended for public distribution" is the usual risk reading; Apple's own intro also says family/friends apps can use Xcode install or Ad Hoc | https://developer.apple.com/app-store/review/guidelines/ (Introduction, 2.2) | P |
| Ad Hoc | register up to 100 devices per product family per membership year; disabling does not free a slot; list can be reset at the start of a new membership year; after lapse devices are removed 180 days later | https://developer.apple.com/help/account/devices/devices-overview | P |
| Ad Hoc profile life | at most one year | https://bifrost.nativephp.com/docs/mobile/credentials/devices (not Apple) | S |
| Personal Team (free Apple Account) | 10 App IDs and 3 devices, both expire after 7 days, 3 apps per device, profiles expire in 7 days | https://developer.apple.com/support/compare-memberships/ | P |
| Personal Team and push | Push Notifications capability is not available to a free account (widely known; the capability matrix page lost its check-marks on fetch) | https://developer.apple.com/help/account/reference/supported-capabilities-ios | M |
| Enterprise Program | US$299/yr, needs 100+ employees, D-U-N-S, legal entity; internal employees only, "not for ... consumer distribution" | https://developer.apple.com/programs/enterprise/how-it-works | P |
| Apple Business (ABM) custom apps | for specific organisations; needs org enrolment, App Review; D-U-N-S requirement reported (one forum post says gov entities hit it despite "optional") | https://developer.apple.com/forums/thread/686765 and Apple's ABM guide | S |
| Unlisted App Distribution | App Store app reachable only by link, "limited audiences", managed and unmanaged devices, all regions; needs the request form and an app already on, or submitted to, App Review; declined if "in a beta or prerelease state" | https://developer.apple.com/support/unlisted-app-distribution/ | P |
| Managed capabilities | some capabilities need an entitlement assigned by Apple after a request form; only the Account Holder can request | https://developer.apple.com/help/account/capabilities/capability-requests | P |

### 1.2 Which path fits a family of 2-6 plus friends' households

- **Family (2-6): TestFlight internal testing.** Each member is invited as an App Store Connect user with a limited role (role names
  `[M]`), installs TestFlight, taps install. No review, no device registration, builds last 90 days. Apple IDs are the members' own.
- **Friends' households: TestFlight external (public link).** One Beta App Review for the first build, then updates flow. The
  friend never creates a developer account. The link is the secret: put an in-app pairing step (QR on their own panel) so a
  leaked link is useless, which is also what Apple recommends for unlisted apps ("implement a mechanism ... to prevent
  unauthorized use") `[P]`.
- **Ad Hoc as the fallback** if Beta App Review proves hostile to "needs a Zoe box": register UDIDs (100 per year per family
  of device), re-sign yearly, install over HTTPS manifest or Apple Configurator `[M]`. The ceremony is worse (collect UDIDs, yearly re-provision).
- **Free Personal Team: development only.** 7-day expiry, 3 devices, no push, so it cannot carry the product.
- **ABM custom apps / Enterprise: not eligible.** Both assume an organisation.
- **Unlisted distribution: the exit ramp** if a friend count outgrows TestFlight or 90-day builds annoy. It needs a finished app,
  a working backend or approved demo mode during review (Guideline 2.1(a) `[P]`), and Guideline 4.2.3(i) ("work on its own without
  requiring installation of another app") `[P]` would **reject a Zoe app that delegates chat/calls to Element X**.

### 1.3 Yearly cost and ceremony

- Money: US$99 (programme). CI on a public repo is free on GitHub-hosted macOS runners `[S]` or Xcode Cloud's 25 compute hours/month included with the programme `[P]` (https://developer.apple.com/xcode-cloud/ ; paid tiers US$49.99 per 100 h `[S]`). A Mac is a one-off (section 7).
- Ceremony per year: renew (once), keep one build alive (a scheduled CI job that uploads a build every 60 days; the 90-day limit
  makes this a hard operational duty), answer any Beta App Review question, file the managed-capability requests once, rotate
  the APNs key if compromised.

### 1.4 Do TestFlight builds get PushKit / CallKit / background BLE?

Yes. TestFlight builds are App Store-signed distribution builds, so they carry the same entitlements and use the **production** APNs
environment; an Apple DTS engineer says the new VoIP diagnostic dialogs are "specifically tied to development and TestFlight signed builds"
which implies TestFlight builds exercise the full VoIP contract (thread 787466, https://developer.apple.com/forums/tags/pushkit) `[P]`.
`bluetooth-central`, `voip`, `audio` are `UIBackgroundModes` Info.plist keys, not entitlements. Two things do need an Apple request: the
Notification Service Extension filtering entitlement `[P]` and, most likely, PushToTalk `[U]` (the forum report below).
Beta App Review does look at background modes: declare only what you use (Omi declares `audio, location, bluetooth-central, fetch, processing, remote-notification, voip` `[C]` `app/ios/Runner/Info.plist:205-213`, which is a review risk we should not copy blindly).

### 1.5 If we ever publish (App Store review implications) `[P]` guidelines page

- **2.5.4** background services only for intended purpose: VoIP, audio, location, etc. Declare `voip` only if CallKit/PushKit is real.
- **4.5.4** push must not be required for the app to function, and "should not be used to send sensitive personal or confidential information" (another reason to send minimal payloads).
- **4.2 / 4.2.3(i)** no repackaged websites (a bare Capacitor wrap of the PWA is exposed); app must work without another app.
- **2.1(a)** backend must be live in review; give a demo account or an Apple-approved built-in demo mode (a Zoe box in the reviewer's hands is not realistic, so build demo mode).
- **5.1.1(v)** in-app account deletion, **5.1.2(i)** disclose and get permission before sharing personal data with third-party AI (Zoe is local, so state that).
- **2.5.14** explicit consent and visible indication when recording (the pendant and mic features).
- HealthKit: not used, nothing to declare. Background audio: allowed under 2.5.4 if it is real playback/VoIP.
- Family Sharing: only matters for in-app purchases; irrelevant to a free companion app `[M]`.

---------------------------------------------------------------------------------------------------

## 2. Stack decision: native Swift/SwiftUI

### 2.1 Requirements scored

| Need | Native Swift | Flutter | RN/Expo | Capacitor around PWA |
|---|---|---|---|---|
| CallKit + PushKit | direct | plugin glue (e.g. FluffyChat wires CallKit in Dart `[S]`) but still a native delegate | plugin glue | plugin glue |
| NSE (decrypt/relay push) | **must be Swift** (separate process) | Swift anyway | Swift anyway | Swift anyway |
| PushToTalk, Live Activity, WidgetKit, App Intents, controls | **must be Swift** (extensions) | Swift anyway | Swift anyway | Swift anyway |
| Background BLE with state restoration | direct | needs a native manager (Omi did exactly this) | native module | native plugin |
| WebRTC | LiveKit Swift SDK 2.18.0 | livekit client-sdk-flutter 2.13.1 | LiveKit RN | JS in WKWebView (media in another process; CallKit conflict, below) |
| Custom household CA | direct (URLSession delegate) | **dart:io ignores the iOS-installed CA**; workaround = cupertino_http or custom SecurityContext | n/a | WKWebView uses system trust |
| Matrix | matrix-rust-components-swift (Apache) | famedly Dart SDK (AGPL) | none official | none |

Evidence for the two load-bearing claims:
- Omi (Flutter, MIT) wrote `app/ios/Runner/Ble/OmiBleManager.swift` (1,848 lines) whose header says it "Replaces flutter_blue_plus on iOS for better battery efficiency and background reliability" and implements `willRestoreState` with restore id `com.omi.ble.restore` `[C]`.
- Omi issue #6603: Flutter `dart:io` "ignores the iOS system certificate store"; user-trusted CA works in Safari but the app fails `CERTIFICATE_VERIFY_FAILED` (closed 2026-10-09 by PR #21057 adding a debug-only override) https://github.com/BasedHardware/omi/issues/6603 `[S]` (an issue author's account); independent corroboration: a Proxmox client patch reports plain `IOClient` ignores user-installed certs on iOS and fixes it with `cupertino_http` https://lists.proxmox.com/pipermail/pve-devel/2025-September/075058.html `[S]`.
- A webview-hosted Element Call hits Apple's single-process media rule: Element X ends the CallKit call right after answering for web calls "as EC gets access to media again" (comments in `ElementCallService.swift` `[C]`, Apple forum thread 767949 referenced there). That is the strongest argument against "Capacitor around the PWA" for calls.

### 2.2 Library facts (all versions checked today via the GitHub API / pypi)

| Piece | Version / date | Licence | Notes |
|---|---|---|---|
| LiveKit Swift SDK `livekit/client-sdk-swift` | 2.18.0, 2026-10-16 | Apache-2.0 | iOS 13+, Swift 6.1, includes an `E2EE/` module, `Docs/audio.md` tells you to set `AudioManager.shared.audioSession.isAutomaticConfigurationEnabled = false` when using CallKit `[C]` |
| LiveKit server `livekit/livekit` | v1.13.10, 2026-10-14 | Apache-2.0 | |
| matrix-rust-components-swift | 26.10.15, 2026-10-15 | Apache-2.0 | iOS-only package; wraps matrix-rust-sdk |
| matrix-rust-sdk | matrix-sdk-0.19.1, 2026-09-18 (main active 2026-10-16) | Apache-2.0 | E2EE, sliding sync, Swift/Kotlin bindings; Tuwunel has `sync/v5.rs` (simplified sliding sync) `[S via GitHub tree]` |
| Element X iOS | release 26.10.4, 2026-10-16; iOS 18+ | AGPL-3.0-only OR Element Commercial | about 152k lines of Swift in app+NSE+share ext, +36k test `[C]` |
| element-call-ios (native MatrixRTC) | 0.1.0-rc.11, 2026-10-15 | AGPL-3.0 | "Native MatrixRTC calls for iOS as a Swift package"; Swift tools 6.2 (so Xcode 26+), iOS 18+; README says substantial AI assistance `[C]` |
| matrix-rust-rtc | 0.5.0-rc.5 | AGPL-3.0 | media core under element-call-ios |
| Element Call (web) | v0.28.0, 2026-10-13 | AGPL-3.0 | |
| Sygnal | v0.17.0, 2025-11-05 | **AGPL-3.0** (dual, commercial) | per-app config (`certfile` or `keyfile/key_id/team_id`, `topic`, `push_type`, `platform`); one app config per bundle id |
| Omi app | v0.12.501 macOS tag 2026-10-18 | MIT | Flutter app + native iOS BLE manager |
| aioapns | 4.0, 2025-04-14 | Apache-2.0 | Sygnal pins it below 4.0 because 4.0 changed key handling (Sygnal changelog `[C]`) |

Native MatrixRTC on iOS is **real but experimental**: Element X 26.10.4 has `nativeCallEnabled` behind a developer flag ("Add experimental native calls behind a developer flag", PR 6159 `[C]` `CHANGES.md`). Correction to lane F 4.1: Swift bindings for the native RTC library do exist (element-call-ios), it is not "no Flutter binding only"; but it is AGPL, rc, and iOS 18+/Xcode 26+.

### 2.3 Siri, Shortcuts, widgets, assistants

- **App Intents** is the supported surface: actions discoverable by Siri, Spotlight, Shortcuts, Action button, widgets, controls, Live Activities https://developer.apple.com/documentation/appintents `[P]`. "Hey Siri, ask Zoe to ..." works as an App Shortcut phrase containing the app name `[M]`. Reports say WWDC26 deprecated SiriKit in favour of App Intents and added streaming/multi-turn `[S]` (low-quality SEO sources, treat as unconfirmed).
- **Can a third-party app be the default assistant? No, outside Japan.** iOS 26.2 let users in Japan reassign the side button to a third-party assistant under the Mobile Software Competition Act, apps must adopt it, Japan Apple accounts only https://www.macrumors.com/2025/11/17/ios-26-2-iphone-users-eu-siri-replacement/ `[S]`. The household is in Western Australia (repo context), so no.
- **"Siri Extensions" for third-party chatbots in iOS 27:** in beta code, whether announced is disputed `[S]` (https://thenextweb.com/news/apple-siri-extensions-third-party-ai-missing-wwdc). Watch item only; do not design around it.
- **Widgets / controls / Action button:** a Zoe "Talk" control and a shopping-list widget are cheap once the intents exist `[M]`.
- Apple state today: iOS 27.0.1 (2026-09-28) is current, iOS 26.6.2 (2026-09-08) latest of 26, Xcode 27.1 (2026-10-15) https://developer.apple.com/news/releases/ `[P]`.

### 2.4 Recommendation (one paragraph)

Build **native Swift/SwiftUI, one app target plus extensions (NSE, widget/Live Activity, optional share)**, deployment target iOS 18 (matches
Element X and element-call-ios, and a household's phones are current), Xcode 26.6 now and 27.x when the iOS 27 SDK becomes the upload floor. Keep
Zoe's own screens (lists, notices, day review, people tagging) as SwiftUI against Zoe's REST + WebSocket; keep the PWA as the guest/kid and Android fallback. Use the
LiveKit Swift SDK for media, a thin hand-written CallKit/PushKit layer for rings, the PushToTalk framework for intercom, and port Omi's `OmiBleManager.swift` pattern (MIT) for the pendant.
**Biggest risk:** one maintainer + two Apple managed-capability approvals with no stated SLA (forum reports of months of silence for the NSE filtering request `[S]`) + the iOS-only, Mac-dependent toolchain.

---------------------------------------------------------------------------------------------------

## 3. Push

### 3.1 APNs setup (primary: Apple docs read today)

- Hosts `api.push.apple.com:443` (prod) and `api.sandbox.push.apple.com:443`; port **2197** also works to pass firewalls that block other HTTPS; HTTP/2 + TLS 1.2+ required https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns `[P]`.
- Token auth: .p8 signing key + 10-char Key ID + Team ID; JWT alg ES256 with `kid`, claims `iss` (team) `iat`; refresh **no more than once per 20 minutes and no less than once per 60** (APNs rejects older than one hour; `TooManyProviderTokenUpdates` on over-refresh); one connection cannot serve several teams https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns `[P]`.
- Key types: **team-scoped keys** (Sandbox or Production only, **max two per environment**, any topic in the team) and **topic-specific keys** (max 200 per environment, up to 400 topics per key). Prefer a **topic-specific production key** limited to Zoe's bundle ids so a leaked key cannot push to anything else `[P]`.
- Headers: `apns-push-type` (alert, background, voip, liveactivity, pushtotalk ...), `apns-topic` (bundle id; **`<bundle>.voip`** for VoIP; `<bundle>.push-type.liveactivity`; `<bundle>.voip-ptt` for PushToTalk `[S]`), `apns-priority` 10, `apns-expiration` 0 or a few seconds for calls (Apple says set expiration to "only a few seconds" for VoIP) `[P]`.
- Payload limits **4 KB** normal, **5 KB** VoIP `[P]`. APNs stores at most **one** notification per bundle id while a device is offline, for up to 30 days `[P]`.
- Environments: development builds use sandbox tokens, TestFlight/App Store builds use production tokens; a token is only valid in its own environment `[M]`.

### 3.2 Can the Jetson call APNs directly? **Yes.**

The provider is an outbound HTTP/2 client; nothing connects inbound to the box `[P]`. So CGNAT does not matter. Python stack: `aioapns` 4.0 (Apache-2.0, 2025-04-14) or `httpx[http2]` 0.28.1 (BSD-3, `h2` 4.4.1 MIT) + `pyjwt` 2.15.1 (MIT) `[pypi today]`. The repo already carries `pywebpush`.
Constraints and the design that follows:
- The .p8 on the box means a rooted box can push to **every** installed Zoe app of that team. Own household only: acceptable with a topic-specific production key (and the vault design, #2100).
- **Friends' households** each run their own box but share one app (one Team ID). They cannot each hold the .p8. So: box -> authenticated call to a **push relay on the411** (holds the key, maps household id -> device tokens, rate-limits) -> APNs. That relay is the same small hub already proposed for cross-household signalling (lane F 6). Alternative (each household its own Apple developer account) is not realistic.
- Apple closes connections on revoked keys; handle reconnect and `410 Unregistered` by dropping the token `[P]/[M]`.

### 3.3 Matrix path: Sygnal

Needs its own APNs credentials and `topic` equal to our bundle id; tokens for other apps return `BadDeviceToken`/`DeviceTokenNotForTopic` (`sygnal/apnspushkin.py` handles these as reasons to reject the token `[C]`). Element X hard-codes its gateway: `pushGatewayBaseURL: URL = "https://matrix.org"` in `AppSettings.swift:225` `[C]`, so a forked Element X must change that and register pushers with our app_id. Element's own `docs/FORKING.md` is the official recipe: change `BASE_BUNDLE_IDENTIFIER`, `APP_GROUP_IDENTIFIER`, `DEVELOPMENT_TEAM` in `app.yml`, set up OIDC/MAS associated domains, MapLibre key `[C]`. Sygnal is Python 3, AGPL: acceptable because the repo is public, but state it. UnifiedPush is Android-only (ntfy docs, lane F).

### 3.4 What Apple sees `[P]` + reasoned

- Sees: that provider X (IP) sent something to device token T of bundle B at time t, its size, priority, and **the full payload unless you encrypt it** (Apple: "Don't include customer information or any sensitive data ... If you must ... encrypt it before adding it to the payload" https://developer.apple.com/documentation/usernotifications/generating-a-remote-notification `[P]`). Apple also sees device IP when the phone connects to APNs. That is metadata about when Zoe talks to a person, not the content.
- Mitigations: send **content-free** pushes ("ring", "notice available") with an opaque id; the app or NSE fetches the content over the pinned channel. For alert pushes use `mutable-content: 1` and a Notification Service Extension (about 30 s budget) to fetch or decrypt https://developer.apple.com/documentation/usernotifications/modifying-content-in-newly-delivered-notifications `[P]`. **An NSE does not run for PushKit VoIP pushes** (VoIP payloads go straight to the app), so VoIP payloads carry only call id + generic caller label and are decrypted in-process if needed `[P]` (Apple: "If you need to do work in response ... for example, to decrypt content use a notification service extension" applies to the User Notifications path).
- Payload encryption option: per-device AES-GCM/HPKE key created at pairing and kept in a Keychain access group shared with the NSE (`kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly`) `[M]`. Caveat `[E]`: before the first unlock after a reboot, that Keychain item is unreadable, so a call arriving then can show only a generic caller.
- Time-sensitive notifications (lock-screen break-through for PA messages) need only the capability; **Critical Alerts need an Apple-granted entitlement** `[M]`; do not plan on it.

---------------------------------------------------------------------------------------------------

## 4. Calls on iPhone, concretely

### 4.0 Hard rules (iOS 26/27)

- PushKit VoIP: for apps built with iOS 13+ SDK, you **must** use CallKit; "If you are unable to support CallKit in your app, you cannot use PushKit" https://developer.apple.com/documentation/pushkit/responding-to-voip-notifications-from-pushkit `[P]`.
- iOS 26 SDK: the last unrestricted PushKit entitlement (`com.apple.developer.pushkit.unrestricted-voip.ptt`) is **disabled**; "ALL apps that link against the iOS 26 SDK which receive a voip push through PushKit and which fail to report a call to CallKit will be now be terminated by the system" (Apple DTS, thread 787466, listed Jun 2025) `[P]`.
- iOS 26.4: new delegate `pushRegistry(_:didReceiveIncomingVoIPPushWith:metadata:withCompletionHandler:)` with `PKVoIPPushMetadata.mustReport`; `mustReport` may be false when the app is foreground, already on a call, or the push is stale; Apple has not documented the full criteria (DTS, thread 816211, Feb 2026) `[P]`. A developer reports the old method is not called when the new one exists on iOS 26.4 devices (thread 824245) so implement both `[P]`. A Sept-2026 thread on "VoIP push compliance rules" has no Apple answer in the capture `[P]`.
- **Therefore: use VoIP push only for a real, ringing call.** Never for PA announcements or notices; those are alert pushes (or PushToTalk). Misusing VoIP push gets the app killed and, per Apple, delivery disabled.

### 4.1 (a) Matrix / Element Call via Sygnal + PushKit + CallKit + matrix-rust-sdk

Element X iOS **does show the exact pattern**, and it is not the textbook PushKit-token flow. Read from `element-hq/element-x-ios` develop (Oct 16, 2026) `[C]`:
1. Matrix homeserver pushes (via Sygnal -> APNs) a normal **alert push with `mutable-content`**, `event_id_only`. `NSE/Sources/NotificationHandler.swift` (271 lines).
2. The NSE, using the Rust SDK, fetches and decrypts the event. For an `m.rtc.notification` of type `.ring` it checks the room still has an active call (waiting up to 5 s on room-info updates) or the event is under a freshness threshold, builds `{roomID, roomDisplayName, expirationDate, rtcNotifyEventID, isVoiceCall}`, calls **`CXProvider.reportNewIncomingVoIPPushPayload(payload)`**, and returns "processed, discard" so no banner shows. Comment cites Apple's "Sending end-to-end encrypted VoIP calls".
3. The system launches the app; `ElementCallService.swift` (737 lines) `PKPushRegistryDelegate.didReceiveIncomingPushWith` validates the payload, enforces a 90 s max ring, and calls **`callProvider.reportNewIncomingCall`** (`supportedHandleTypes = [.generic]`, `includesCallsInRecents`). `didUpdate pushCredentials` is **empty**: Element never uses a PushKit token or `.voip` topic.
4. On answer it fulfils the action, then either keeps the CallKit call (native stack) or ends it after 1 s (webview Element Call) because a WKWebView runs media in another process and CallKit gives exclusive media to the host process.
5. Background modes include `audio, fetch, processing, voip, location` `[C]` `Info.plist`; NSE entitlement `com.apple.developer.usernotifications.filtering = true` `[C]` `NSE/SupportingFiles/NSE.entitlements`.
Apple's doc for this exact flow: https://developer.apple.com/documentation/callkit/sending-end-to-end-encrypted-voip-calls `[P]`; it says use it only when "your server can't determine whether an outgoing notification is a request for a VoIP call ... due to metadata encryption". The filtering entitlement (silences the alert) is a managed capability: https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.usernotifications.filtering `[P]`; approval is discretionary, one developer was refused for a non-messaging use, others waited weeks without reply, Signal is cited as a user (forum threads 707406, 757197) `[S]`.

Licence: Element X app code (the files above) is AGPL-3.0-only OR commercial; matrix-rust-sdk and matrix-rust-components-swift are Apache-2.0 `[P]`. Copying `ElementCallService`/`NotificationHandler` makes our app AGPL; with a public repo that is a compliance non-event, but TestFlight distribution still "conveys" the app, so keep the source link current.
Effort: embedding matrix-rust-sdk and element-call-ios yourself is the bulk of the work (login/OIDC, room list, timeline, verification, key backup UX, the call stack). Forking Element X gets all of it but also inherits about 152k lines and its release cadence.
State: native calls flag is experimental, 1:1 "telephone" semantics (ringing MSC4075) still unfinished `[S, lane F]`.

### 4.2 (b) Plan B: native WebRTC with LiveKit Swift SDK and our own signalling

Server knows it is a call, so the **plain PushKit path is correct** and cheaper:
1. App registers `PKPushRegistry(queue: nil)`, `desiredPushTypes = [.voIP]`, uploads the token (encrypted) to the box/relay at pairing (Apple: forward token with account id) `[P]`.
2. Caller side (panel kiosk, another phone, or a household hub) -> box -> relay -> APNs: `apns-push-type: voip`, `apns-topic: <bundle>.voip`, priority 10, expiration a few seconds, payload (<=5 KB) = `{call_id, caller_label, media: audio|video, expires}`. No secrets.
3. Delegate runs, **immediately** `reportNewIncomingCall` (use a generic label first, then `reportCall(with:updated:)` once the app fetches the display name), and implement both PushKit delegates (26.4 `mustReport`) `[P]`.
4. On `CXAnswerCallAction`: fetch a LiveKit join token from the box with the device credential, connect the LiveKit room. Turn off the SDK's automatic AVAudioSession handling and let CallKit's `didActivate` start audio (`Docs/audio.md` in the SDK) `[C]`.
5. **Lock-screen answer:** CallKit's native incoming screen works on the lock screen with no unlock; Do Not Disturb/Focus is respected by CallKit `[P]` (Apple doc above).
6. **Speaker/earpiece/handset:** `AVAudioSession` category playAndRecord, mode `.voiceChat`; audio calls default to the earpiece ("handset mode"), speaker toggled via `overrideOutputAudioPort` or the system route picker; Bluetooth/AirPods route automatically `[M]`.
7. **Background audio:** the CallKit-activated session plus `audio` background mode keeps the call alive with the screen locked `[M]` (`Info.plist` `audio`+`voip`).
8. **Ring for the panel side:** the panel is always on, so no push is needed; the box rings it over the existing WebSocket.
Cost: about 1,200-1,800 Swift lines plus box/hub signalling `[E]`. SFU/TURN: LiveKit server (Apache-2.0, 1.13.10) on the411; it bundles a TURN server `[M]` so one process covers both.
Plan B vs Matrix at the ring layer: Plan B needs **no NSE filtering approval and no E2EE-metadata trick**; its price is that every feature (accounts, presence, group calls, history) is ours.

### 4.3 CarPlay (note only)

Apple restricts CarPlay to categories gated by an Apple-granted entitlement `[M]`; not needed. Calls started from CallKit appear in the car's native phone UI anyway `[M]`.

### 4.4 Intercom (not a call)

- **PushToTalk framework** (iOS 16+) is the system answer for walkie-talkie/intercom: system UI with blue status-bar pill and Lock Screen controls, background audio for incoming speech, ephemeral push token, `apns-push-type: pushtotalk`, topic `<bundle>.voip-ptt`, priority 10, expiration 0; join a channel only from the foreground; **only one PTT app active system-wide; PTT yields to calls**; do not do network work in the restoration/push handlers https://developer.apple.com/videos/play/wwdc2022/10117/ `[P]`.
- Zello (a large PTT vendor) says Apple requires PTT apps to use the framework **starting with iOS 26** https://blog.zello.com/what-to-know-about-apples-ptt-framework-and-zello `[S]`, consistent with the DTS statement that the unrestricted voip.ptt entitlement was removed `[P]`.
- Entitlement: the WWDC session names no approval step, but a developer reports App ID registration for PushToTalk failed with "no eligible program membership" (forum thread 804205) `[S]`. Treat as a managed capability to **request in week 1** `[U]`.
- **Live Activity for "incoming intercom like Apple's":** ActivityKit supports push-to-start tokens (obtain without launching the app), `apns-push-type: liveactivity`, topic `<bundle>.push-type.liveactivity`, an hourly update budget; active up to 8 h, 12 h on the Lock Screen https://developer.apple.com/documentation/activitykit/starting-and-updating-live-activities-with-activitykit-push-notifications `[P]`. A Live Activity shows a lock-screen card with Reply/Talk buttons but **does not ring and does not start audio by itself**. Use it for "Sam is calling the kitchen panel, tap to talk"; the audio path is PTT (or the app).
- PA announcements (one-way "dinner is ready"): alert push with a custom short sound + time-sensitive interruption level; optional NSE to fetch a pre-rendered clip.

---------------------------------------------------------------------------------------------------

## 5. Omi pendant on iOS

### 5.1 What Omi's iOS app does (read from `BasedHardware/omi` main, 2026-10-18, MIT) `[C]`

- **Native BLE**: `app/ios/Runner/Ble/OmiBleManager.swift` (CoreBluetooth, `CBCentralManagerOptionRestoreIdentifierKey = com.omi.ble.restore`, `willRestoreState` re-connects restored peripherals and re-discovers services; bridges to Flutter through Pigeon). `app/lib/services/devices/ARCHITECTURE.md` describes a saved-pendant reconnect scheduler with 5 s exponential backoff capped at 5 min.
- **Background modes** declared: `audio, location, bluetooth-central, fetch, processing, remote-notification, voip` (`Info.plist:205-213`).
- **Protocol**: GATT service `19b10000-e8f2-537e-4f6c-d104768a1214`, audio notify `19b10001...`, codec read `19b10002...`; Opus 16 kHz mono; 3-byte header per packet; codec ids 0x14 (devkit, 10 ms frames) and 0x15 (CV1, 20 ms) (`sdks/device/PROTOCOL.md`).
- **Offline storage sync**: a multi-file storage protocol (`CMD_LIST_FILES 0x10`, `CMD_READ_FILE 0x11`, `CMD_DELETE_FILE 0x12`, plus ring ADVANCE/CLEAR, `CMD_CUSTODY_ENABLE 0x14`) and a **custody contract**: "the pendant frees audio from its SD ring only after the app has durably stored it ... Delivery over Bluetooth is not custody" (`app/lib/services/wals/PENDANT_RING_CUSTODY.md`). This is the same invariant as Zoe's Pi sync (sha256 echo before freeing), so the phone path can reuse the same contract.
- **SDKs**: `sdks/swift` (omi-lib, 1,820 lines: BLE scanner/manager, Opus codecs, STT engines incl. on-device Whisper tiny), `sdks/device/{cpp,dart,go,rust,typescript}`, `sdks/python`, `sdks/react-native`. All MIT.
- **Firmware limits**: `omi/firmware/omi/omi.conf`: **`CONFIG_BT_MAX_CONN=1`**, `L2CAP_TX_MTU=498`, `DATA_LENGTH_MAX=251`, `ACL_RX_SIZE=1024`. iOS picks its own MTU (typically below Android's `[S]` per lane F: 135-255 vs 512), so throughput on iPhone is lower than on the Pi (BlueZ `[M]`).

### 5.2 Background limits that bind a phone path

- `bluetooth-central` lets the system wake the app for connect/disconnect/characteristic events, then you have **about 10 seconds**; background scans coalesce duplicates and slow down https://developer.apple.com/library/archive/documentation/NetworkingInternetWeb/Conceptual/CoreBluetooth_concepts/CoreBluetoothBackgroundProcessingForIOSApps/PerformingTasksWhileYourAppIsInTheBackground.html `[P]`. A bulk storage drain is a minutes-long transfer, so it does not fit a background wake unless the link stays up and notifications keep arriving.
- **Relaunch rules (TN3115, updated 2025-09-15 for iOS 26)**: relaunched after crash, memory eviction, device restart (after first unlock), Airplane Mode toggle; **not** after a Settings Bluetooth power toggle; **force quit and Control Center Bluetooth toggle: "Starting in iOS 26 ... only apps that use [AccessorySetupKit] to setup Bluetooth accessories will be relaunched"**; and only if the app is waiting for a specific BLE event (scan, connect, subscribed characteristic) https://developer.apple.com/documentation/technotes/tn3115-bluetooth-state-restoration-app-relaunch-rules `[P]`. A forum report says even AccessorySetupKit did not relaunch a force-quit app via advertisement (thread 840468) `[S]`.
- **AccessorySetupKit** (iOS 18+): privacy-preserving discovery/pairing UI that also grants Bluetooth permission and enables pairing removal https://developer.apple.com/documentation/accessorysetupkit `[P]`. Use it for pendant onboarding; it is also the route to relaunch after force-quit.

### 5.3 Realistic design

1. **The panel Pi stays the reliable sync** (`POST /api/omi/recordings`, custody by sha256 echo). It is the only always-near, always-on central.
2. **Because the pendant accepts one connection**, the Zoe app must never silently hold it. Policy: the app connects only on an explicit "Sync now / Live" action or inside a foreground session, tells the box (`/api/omi/...` lock) so the Pi backs off, drains using the same custody contract, uploads to the **same** `POST /api/omi/recordings` with a member credential (needs a member-authenticated variant; the current route is device-token only), then **disconnects**. Live streaming (Opus 16 kHz) is foreground-only, with the `audio` background mode as a best-effort bridge.
3. Do not declare `location` as a keep-alive trick; declare `bluetooth-central` only (and `location` only if iBeacon region monitoring is shipped for presence, section 6).
4. Adopt, do not build: use the pattern and constants from `sdks/swift` and `OmiBleManager.swift` (MIT; keep the licence notice). Do not fork the Flutter app (Firebase, hard-coded backend until PR 21057 which is debug-only and release builds ignore it `[S]`).
5. Estimate: 2,000-3,000 Swift lines for BLE + Opus decode + custody + a sync screen `[E]`; the Omi native iOS directory is 25,679 lines `[C]`, most of it feature surface we do not need.

---------------------------------------------------------------------------------------------------

## 6. Security and privacy posture

### 6.1 TLS to the household, pinning, and the private CA

- **Public name on the relay (preferred for the phone):** a normal publicly trusted certificate for a name served through the frp relay (R5 "public name" in the repo plan). Then nothing special is needed in the app; optionally pin with `NSPinnedDomains` (CA and/or leaf SPKI identities) https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity/nspinneddomains `[P]`. Pin the key you control (certbot `--reuse-key` `[M]`), not a rotating intermediate.
- **Private CA (`.zoe.internal`)**: Apple states pinning "doesn't change the way the system evaluates that certificate's suitability as an anchor" (same doc) `[P]`, so `NSPinnedDomains` alone will **not** make a private CA trusted. Two options: (a) ask each user to install and trust a CA profile (a manual Settings step, contradicts "users never run code"); (b) **ship the trust inside the app**: a `URLSessionDelegate` that evaluates the server trust with `SecTrustSetAnchorCertificates` + anchor-only and fingerprint checks `[M]` (Apple's "Performing manual server trust authentication"). Use the **QR on the panel** as the trusted channel: it carries the base URL and the CA SHA-256 fingerprint; the app fetches the CA, verifies the fingerprint, stores it in the Keychain, and uses it for URLSession (REST + `URLSessionWebSocketTask`) and, for `NWConnection`, `sec_protocol_options_set_verify_block` `[M]`. This is the pairing step, not an extra one.
- ATS note: ATS does not apply to low-level Network.framework connections https://developer.apple.com/documentation/security/preventing-insecure-network-connections `[P]`.
- Avoid Flutter's `dart:io` for exactly this reason (section 2).

### 6.2 Device identity, App Lock, "voice is a password" on the phone

- **Secure Enclave key** created at pairing (CryptoKit `SecureEnclave.P256`, access control biometric-gated) https://developer.apple.com/documentation/security/protecting-keys-with-the-secure-enclave `[P]`. The box stores the public key per member device; sensitive actions (show private results on a panel, approve a day review, unlock a voice session) require a signed server challenge. Revocation is deleting the public key. This makes the phone a strong factor, stronger than a bearer device token.
- **App Attest** (DCAppAttestService) to prove the requests come from a genuine instance of the app https://developer.apple.com/documentation/devicecheck/establishing-your-app-s-integrity `[P]`; not available on every device (do a compatibility check); optional hardening, not required for a household.
- **Keychain**: tokens in `kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly` in a shared access group (app + NSE + extensions) `[M]`. Face ID/Touch ID app lock via LocalAuthentication gates the UI and the Secure Enclave signing, not the NSE `[M]`.
- Guests and kids: no app, PWA only (matches the lane F decision).

### 6.3 Phone presence as a second factor for the panel

| Mechanism | What it gives | Limits (source) | Verdict |
|---|---|---|---|
| **Panel advertises iBeacon, phone monitors** (Core Location region monitoring wakes the app on enter/exit; beacon ranging for proximity) | Passive "member near panel X", works in background | Needs Location "Always" authorisation and the Pi must advertise via BlueZ; region monitoring detection can lag (older reports say minutes `[S]`); 20-condition cap per app https://developer.apple.com/documentation/corelocation/monitoring-the-user-s-proximity-to-geographic-regions `[P]` | **Use as a hint, never as the factor** (replayable) |
| **Phone advertises** (iBeacon or service UUID) | would let the Pi sense the phone | "Apps that use their underlying iOS device as an iBeacon must run in the foreground" https://developer.apple.com/documentation/corelocation/turning-an-ios-device-into-an-ibeacon-device `[P]`; background advertising drops the local name and moves service UUIDs into an overflow area only iOS devices can scan for (Core Bluetooth guide `[P]`, so a BlueZ scanner on the Pi cannot see them) | **Not viable** in background |
| **Same Wi-Fi / mDNS reachability** | phone on the LAN = present at the house | Needs Local Network permission; says "at home", not "at this panel" `[M]` | cheap house-level signal |
| **NFC background tag reading** (iPhone XS+) | "tap phone on the panel bezel" opens a universal link to the app after a notification tap; the tag is a `$0.5` sticker | Only when the iPhone is in use, shows a banner, asks to unlock if locked; NDEF URI must be a universal link/URL scheme https://developer.apple.com/documentation/corenfc/adding-support-for-background-tag-reading `[P]` | **Best cheap handoff**: link carries panel id; app signs the challenge with the Secure Enclave key |
| **Nearby Interaction / UWB** | "hold phone to panel" distance/direction | Needs a UWB chip on both ends: iPhone 11+ has one; the panel Pi does not, so a third-party UWB accessory board using Apple's Nearby Interaction Accessory Protocol is needed; interactions run while the app is in the foreground https://developer.apple.com/documentation/nearbyinteraction `[P]` | Nice-to-have later, extra hardware, foreground only |

Recommended factor: **NFC tap (or QR on the panel) + Secure Enclave-signed challenge**, with iBeacon region as a passive hint to pre-warm things. Do not authenticate on beacon proximity alone.

---------------------------------------------------------------------------------------------------

## 7. Effort and sequence

Swift LOC are `[E]` ranges for hand-written app code (excluding libraries), plus box-side Python.

| Phase | Scope | Swift | Box/hub side | Adopt | Build | Calendar `[E]` |
|---|---|---|---|---|---|---|
| **M0** | pairing by panel QR (VisionKit `DataScannerViewController`), CA-in-app TLS, Keychain, Secure Enclave device key, APNs token registration, NSE, notices inbox, household lists (items edit; list delete/rename needs sign-in per the fridge rule), Face ID lock | 2,500-3,500 | device registry + APNs sender (aioapns/httpx), member-authenticated list/notice endpoints, pairing endpoint: 400-700 | SwiftUI, LiveKit not yet | all of it | 4-6 weeks incl. managed-capability requests filed |
| **M1** | PushToTalk channel manager + intercom transport over the panel WebSocket or LiveKit data, Live Activity (push-to-start) for "incoming intercom", PA alert pushes, Siri App Intents ("ask Zoe") and a Talk control | 1,500-2,200 | PTT push type + routing + quiet hours/guest rules: 300-500 | Apple PushToTalk, ActivityKit, AppIntents | glue | 3-4 weeks |
| **M2** | calls: CallKit + PushKit (both delegates), LiveKit Swift, join-token endpoint, ring/decline/cancel logic, speaker/handset UI | 1,200-1,800 (Plan B) | signalling + hub + LiveKit on the411: 500-900 | LiveKit Swift SDK (Apache-2.0) | the call state machine | 3-5 weeks; **plus a 1-week ring acceptance test on the family's real phones, app killed, screen locked** (lane F gate) |
| **M2-Matrix (optional)** | embed matrix-rust-sdk (login, rooms, E2EE, verification, key backup) + element-call-ios, Sygnal + NSE filtering | +4,000-6,000 (or fork Element X: +3,000 on a 152k base) | Sygnal, homeserver, bot | matrix-rust-components-swift (Apache), element-call-ios (AGPL rc) | integration, UX for recovery key | 8-12 weeks; blocked on the NSE entitlement approval |
| **M3** | AccessorySetupKit onboarding, CoreBluetooth manager with restoration, Opus decode, custody sync, foreground live stream, day review, people tagging | 3,500-5,500 | member-authenticated variant of `/api/omi/recordings`, BLE lock handoff with the Pi: 200-400 | port from Omi `sdks/swift` + `OmiBleManager.swift` (MIT) | review/tagging UI | 5-7 weeks |

Totals `[E]`: about 9-13k Swift lines for M0-M3 on Plan B (vs lane F's 3-6k for "a credible app without calls"); the Matrix option adds roughly 4-6k.

### 7.1 What needs the Mac and signing

- Xcode build, archive, signing, App Store Connect upload and any Simulator run need macOS. Apple's upload floor today is **Xcode 26 / iOS 26 SDK since 2026-04-28**, deployment target >= iOS 13 since 2026-09-09; the page lists no iOS 27 requirement yet https://developer.apple.com/news/upcoming-requirements/ `[P]` (a third-party guide claims an April 2027 iOS 27 SDK requirement `[U]`).
- Xcode 26.6 (2026-06-25) needs macOS Tahoe 26.2+; **Xcode 27.1 (2026-10-15) needs macOS Tahoe 26.6+**; Xcode 27 is Apple-silicon only per Apple's deprecation notice (forum thread 829619) https://developer.apple.com/xcode/system-requirements/ `[P]`, https://developer.apple.com/news/releases/ `[P]`. Swift 6.3 in 26.6, 6.4 in 27. element-call-ios needs Swift tools 6.2, hence Xcode 26+ `[C]`.
- **A real iPhone is mandatory**: Omi's README says simulators do not support Bluetooth `[C]`; Element X's code comments that CallKit "isn't even supported" on the simulator `[C]`; PushKit needs a device and production-environment tokens for TestFlight.
- The owner "has a Mac" (docs mention a Mac virtual panel); **confirm Apple silicon and macOS >= 26.2** before relying on it. The repo's hardware notes already weigh a Mac mini (M6) as a second box (`docs/research/what-gets-us-closer-2026-10-07.md` row 6); that purchase would double as the build host.
- **Working from the Jetson:** edit Swift and Python on the Jetson with Claude Code; compile/test on a Mac over SSH or in CI; keep the iOS project in a subtree of the public repo (e.g. `apps/ios/`), which also needs a DOX AGENTS.md and a `validate_structure` manifest entry per the repo rules. Never commit the .p8, certificates, or provisioning profiles.

### 7.2 CI options

| Option | Cost | Notes |
|---|---|---|
| **GitHub-hosted macOS runners** | free on public repos `[S]`; images exist for `macos-26` arm64 with **Xcode 26.6 default** and an `xcode-27-arm64` readme (public preview) `[C]` actions/runner-images; private rate US$0.062/min `[S]` | `fastlane match` or App Store Connect API key for signing; builds, unit tests (no simulators for push/BLE), TestFlight upload on tag; a cron workflow re-uploads every 60 days to beat the 90-day expiry |
| **Xcode Cloud** | 25 compute hours/month included with the programme; paid tiers from US$49.99/100 h `[P]` | Apple-managed signing, TestFlight delivery built in; less scriptable from the Jetson workflow |
| **Own Mac / Mac mini self-hosted runner** | hardware | fastest iterations, device attached for BLE tests; a self-hosted runner on a box that also holds signing assets must obey the repo's PR-as-data rule (never run PR code on it) |
Recommendation: GitHub-hosted for CI/TestFlight, the owner's Mac for device debugging.

---------------------------------------------------------------------------------------------------

## 8. Android in one paragraph

An Android equivalent is a native Kotlin/Compose app of roughly 60-80% of the iOS code `[E]`: FCM high-priority data messages for calls (or UnifiedPush/ntfy for de-Googled phones), a self-managed `ConnectionService` for the ring plus a full-screen intent (Android 14+: the default grant exists only for apps with calling or alarm functions, with a Play Console declaration since 2024-05-31 https://support.google.com/googleplay/android-developer/answer/13392821 `[S]`), a foreground service for BLE (Android is more permissive than iOS here, and the Omi Flutter app already covers Android BLE), and the LiveKit Android SDK (Apache-2.0). **Distribution is the new wrinkle:** Google's developer verification for sideloaded apps went live 2026-09-30 in Brazil, Indonesia, Singapore, Thailand with all regions in 2027 and a "limited distribution" account for up to 20 devices with no ID or fee https://android-developers.googleblog.com/2026/06/android-developer-verification.html `[S]` (Australia's date is not confirmed). **Recommendation: do not choose Flutter or KMP for both now.** Flutter would reintroduce the iOS CA, BLE and extension problems documented above (NSE, PTT, Live Activity, widgets are Swift regardless), and KMP shares only business logic while UI and every system integration stay per-platform; so ship native iOS first, give Android users the PWA + Telegram (live today), and if Android households show up, write a native Kotlin app against the same REST/WebSocket and push-relay contracts, which M0-M2 will have frozen by then.

---------------------------------------------------------------------------------------------------

## 9. What this changes in the deep-dive text (corrections and additions)

1. Sygnal licence: **AGPL-3.0 / commercial since 0.16.0 (2025-07-18)**, not Apache-2.0 (`[C]` clone, `CHANGELOG.md`).
2. Lane F 4.1 said Element's native RTC library has "no Flutter binding" and Swift bindings only; **element-call-ios exists (0.1.0-rc.11, AGPL-3.0, 2026-10-15) and Element X ships it behind a developer flag**.
3. Lane F 8.3/4: "Element X iOS uses PushKit and CallKit" is accurate but incomplete: it registers PushKit with **no token** and gets the ring through an NSE calling `reportNewIncomingVoIPPushPayload`, which requires the managed **notification-filtering entitlement**.
4. Omi pendant: **one BLE connection at a time** (`BT_MAX_CONN=1`), so phone and panel Pi are mutually exclusive; the Omi iOS app is **not** plain flutter_blue_plus any more (native Swift manager with state restoration).
5. iOS 26 BLE: force-quit relaunch is limited to AccessorySetupKit apps (TN3115, 2025-09-15). iOS 27 is shipping (27.0.1 on 2026-09-28).
6. Apple 'unlisted app distribution' is a real, non-expiring path for friends' households (needs full review, demo mode, and 4.2.3(i) means "delegate to Element X" cannot be the published design).

## 10. Not verified / limits

- Apple capability matrix (which capabilities a free Personal Team can use) did not survive fetch; "no push on free accounts" is `[M]`.
- Whether PushToTalk is a managed capability, and any approval turnaround for it or for the NSE filtering entitlement: `[U]`.
- iOS 27 behaviour changes for PushKit/CallKit beyond the 26.4 `mustReport` delegate; the WWDC26 App Intents claims; "Siri Extensions": `[S]/[U]`.
- Real iPhone MTU/throughput for Omi storage sync, and whether the Pi can hold the pendant while the phone also scans: not measured.
- Region-monitoring wake latency for an iBeacon on a Pi panel: not measured; the "minutes" figure is an old third-party slide deck.
- TestFlight Beta App Review treatment of an app that needs a self-hosted box: no source; expect questions, prepare a demo mode and review notes.
- GitHub macOS runners being free for public repos and the US$0.062/min rate are `[S]`, from third-party pricing pages (the Enterprise Server docs page confirms the rate).

## Sources (URLs)

Apple (primary): https://developer.apple.com/programs/whats-included/ ; https://developer.apple.com/help/app-store-connect/test-a-beta-version/testflight-overview ; https://developer.apple.com/help/account/devices/devices-overview ; https://developer.apple.com/support/compare-memberships/ ; https://developer.apple.com/programs/enterprise/how-it-works ; https://developer.apple.com/support/unlisted-app-distribution/ ; https://developer.apple.com/help/account/capabilities/capability-requests ; https://developer.apple.com/app-store/review/guidelines/ ; https://developer.apple.com/news/upcoming-requirements/ ; https://developer.apple.com/news/releases/ ; https://developer.apple.com/xcode/system-requirements/ ; https://developer.apple.com/forums/tags/pushkit (threads 787466, 816211, 824245, 817976) ; https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns ; https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns ; https://developer.apple.com/documentation/usernotifications/generating-a-remote-notification ; https://developer.apple.com/documentation/usernotifications/modifying-content-in-newly-delivered-notifications ; https://developer.apple.com/documentation/pushkit/responding-to-voip-notifications-from-pushkit ; https://developer.apple.com/documentation/pushkit/supporting-pushkit-notifications-in-your-app ; https://developer.apple.com/documentation/callkit/sending-end-to-end-encrypted-voip-calls ; https://developer.apple.com/documentation/bundleresources/entitlements/com.apple.developer.usernotifications.filtering ; https://developer.apple.com/videos/play/wwdc2022/10117/ ; https://developer.apple.com/documentation/activitykit/starting-and-updating-live-activities-with-activitykit-push-notifications ; https://developer.apple.com/documentation/technotes/tn3115-bluetooth-state-restoration-app-relaunch-rules ; https://developer.apple.com/library/archive/documentation/NetworkingInternetWeb/Conceptual/CoreBluetooth_concepts/CoreBluetoothBackgroundProcessingForIOSApps/PerformingTasksWhileYourAppIsInTheBackground.html ; https://developer.apple.com/documentation/accessorysetupkit ; https://developer.apple.com/documentation/corenfc/adding-support-for-background-tag-reading ; https://developer.apple.com/documentation/nearbyinteraction ; https://developer.apple.com/documentation/corelocation/turning-an-ios-device-into-an-ibeacon-device ; https://developer.apple.com/documentation/corelocation/determining-the-proximity-to-an-ibeacon-device ; https://developer.apple.com/documentation/corelocation/monitoring-the-user-s-proximity-to-geographic-regions ; https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity/nspinneddomains ; https://developer.apple.com/documentation/security/protecting-keys-with-the-secure-enclave ; https://developer.apple.com/documentation/devicecheck/establishing-your-app-s-integrity ; https://developer.apple.com/documentation/appintents ; https://developer.apple.com/xcode-cloud/

Code read (cloned today): https://github.com/element-hq/element-x-ios (NSE/Sources/NotificationHandler.swift, ElementX/Sources/Services/ElementCall/ElementCallService.swift, NSE entitlements, docs/FORKING.md, AppSettings.swift, project.yml, CHANGES.md) ; https://github.com/element-hq/sygnal ; https://github.com/element-hq/element-call-ios ; https://github.com/BasedHardware/omi (app/ios/Runner/Ble/OmiBleManager.swift, sdks/device/PROTOCOL.md, sdks/swift, app/lib/services/wals/PENDANT_RING_CUSTODY.md, omi/firmware/omi/omi.conf) ; https://github.com/livekit/client-sdk-swift ; https://github.com/actions/runner-images

Other: https://github.com/BasedHardware/omi/issues/6603 ; https://github.com/BasedHardware/omi/pull/21057 ; https://matrix.org/blog/2026/10/16/this-week-in-matrix-2026-10-16/ ; https://blog.zello.com/what-to-know-about-apples-ptt-framework-and-zello ; https://www.macrumors.com/2025/11/17/ios-26-2-iphone-users-eu-siri-replacement/ ; https://thenextweb.com/news/apple-siri-extensions-third-party-ai-missing-wwdc ; https://lists.proxmox.com/pipermail/pve-devel/2025-September/075058.html ; https://android-developers.googleblog.com/2026/06/android-developer-verification.html ; https://support.google.com/googleplay/android-developer/answer/13392821 ; https://github.com/matrix-construct/tuwunel/pull/555
