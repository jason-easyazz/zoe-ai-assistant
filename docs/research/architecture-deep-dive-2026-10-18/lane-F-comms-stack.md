# Lane F - household comms stack (PA, intercom, messages, calls, backups) on the411

Date of research: 2026-10-18. Tags: [verified] = read on a primary page this session; [secondary] = blog/aggregator/search snippet;
[repo] = read in this repo or measured on this box today; [estimate] = my reasoning, no published figure; [unverified] = could not confirm.
Web search was a snippet tool, so many figures are [secondary]; items that decide the recommendation were re-read at the primary source where I could.

## 0. Answer first (revised after the coordinator's note: Matrix + Element is the incumbent candidate)

**Recommendation: a hybrid. Adopt Matrix (homeserver on the411 + Element X on phones + Element Call/LiveKit on the411) for everything that is PEOPLE-to-PEOPLE (messages, phone ringing, household<->household, group calls); build a small native Opus-over-WebSocket path for the LAN (PA + PTT intercom + in-house panel<->panel audio). Keep the bespoke signed-envelope + P2P-WebRTC design as Plan B.** The hybrid is chosen because the one thing a PWA cannot do on a phone is ring (section 4), and Element X is the only already-shipped, E2EE, native-ringing, push-capable family client; the LAN PA/intercom is the one thing Matrix cannot do (and would hairpin through the411 if forced to).

1. **Homeserver for the411 (a): Tuwunel v1.9.4 (2026-10-10) for messaging; Synapse for calls unless Tuwunel's MSC4140 lands.** conduwuit is archived (read-only since 2026-05-29); the two live descendants are Tuwunel (self-described official successor, full-time sponsored staff, Matrix-project releases every 1-2 weeks, aarch64 + amd64 builds) and Continuwuity (volunteer community fork). Dendrite is in maintenance mode (security fixes only, Element's own repo). Element Call needs MSC4140 delayed events, which Tuwunel and Continuwuity do not implement (Tuwunel PR #555 opened Sept 2026, merge unknown), so **on Tuwunel calls can leave stuck participants; on Synapse (1.163.0 current) the Element Call docs are written for it.** RAM: Tuwunel 512 MB minimum, >=1 GiB advised for a private family server (maintainer's discussion #227, "not official docs"); Synapse ~1-2 GB (Element sizing 2000 MiB / 1-500 users; tutorials ~1 GB idle). The droplet's size is unknown: this is the first question for the owner.
2. **Calls (b): Element Call is now the default calling in upstream Element Web (ESS Pro 26.7.1 notes, 2026-07-24) and embedded in Element X. It always needs its own LiveKit SFU + lk-jwt-service**, discovered via `org.matrix.msc4143.rtc_foci` in `.well-known` on the bare server-name domain (valid TLS, CORS, no port; Element X Android 26.04.4 broke on exactly this, closed 2026-04-30). Media E2EE: LiveKit encrypts audio/video/data tracks and says "no intermediaries (including LiveKit servers)" can read them; Element says each participant has its own key (v0.8.0 notes: call membership via MatrixRTC, "final step toward to-device media key exchange"). So **an SFU on the411 can be blind to media**; the homeserver/SFU still learn who called whom, when, and for how long. I verified the LiveKit half at the source and Element's claim from its blog; I did not read the key-exchange code. Ringing: spec MSC4075 is STILL in proposal review (This Week in Matrix 2026-10-16), so ringing is implementation-defined; Element X iOS uses PushKit+CallKit (secondary, 2025 source) with 2026 regressions (PR #5026 draft, 26.04.1 workaround); Element X Android has full-screen-intent permission, but UnifiedPush ringing bug reports exist (old; no 2026 confirmation). Element's own roadmap still lists "extend 1:1 to a full telephone use-case incl. ringing, declining, handset mode". **Honest read: usable, not yet boring.**
3. **Topology (c): each household = a Matrix user (plus a bot user) on the411's homeserver. Do NOT run a homeserver per box.** The box is behind CGNAT, federation needs a public CA certificate and inbound 443/8448 (the relay's R5 public name), and a homeserver costs 0.5-1 GB the Orin does not have. With E2EE on, content at rest on the411 is ciphertext; the411 sees metadata (accounts, room membership, timestamps, event sizes, device lists, push tokens). A federated second homeserver (another family's own) is possible later and changes nothing in this design.
4. **Bridges (d):** mautrix v26.09 (2026-09-16): telegram v0.2609.0 (Go bridgev2 rewrite since April), whatsapp v26.09, signal v26.09, mautrix-go v0.31.0. They are how "Zoe messages you where you already are" without a new app, but: bridged content is plaintext inside the bridge process, the bridge holds the member's WhatsApp/Signal session, unofficial WhatsApp bridging can get a number banned (from memory, unverified), and the bridge needs the homeserver to reach it (so it runs ON the411, not the box). Telegram is already native in Zoe, so the real incremental gain is WhatsApp/Signal only: treat as an opt-in per member, P3+.
5. **Zoe bot (e): a normal bot USER per household (outbound `/sync`, E2EE-capable, runs on the box), not an appservice.** An appservice needs the homeserver to push to it (CGNAT), needs a registration per household (Synapse reloads registration files only with config reload/restart; Tuwunel can register via admin room) and is the pattern for bridges, not for a per-household agent. Appservice E2EE is still moving (MSC4190 device management, MSC3202 encryption bookkeeping; one project reports MSC4190 disables appservice login per-appservice, Sept 2026). The box-side bot costs RAM [estimate +60-120 MB for matrix-rust-sdk/nio + Olm state]; that is the one new box cost of this plan and must be measured against the 0.6-0.9 GB free. Zoe-to-member and Zoe-to-Zoe messages are then ordinary DMs/rooms.
6. **Phone (f): Element X is the "Zoe app" for messages and calls today; the PWA is the fallback, not the product.** Push: iOS goes through Element's gateway + APNs by necessity (self-hosting Sygnal for iOS needs your own app build + Apple credentials); Android supports UnifiedPush/ntfy (self-host works for messages; call ringing under UnifiedPush has old bug reports). Element Classic is sunset 2026-12-31, so Element X is the only client with a future.
7. **Honest downside list** (full version in 3.3): Synapse RAM; Element Call maturity and its SFU requirement even for 1:1; Tuwunel's missing MSC4140; E2EE key backup/recovery-key UX for non-technical family (lose the key + all devices = history gone); cross-signing/verification prompts on every new login (Element postponed *mandatory* device verification in Sept 2026, one month notice promised); the Zoe bot's own device must be verified or its messages may be hidden once enforcement lands; LAN calls hairpin through the411 if routed through Element Call; a second chat app next to Telegram; moving specs.
8. **What does not change from the first draft:** do not put an SFU on the box (the old `livekit` container is `Exited (137)`); LiveKit Cloud is a non-option; Galene has no E2EE; Jitsi too heavy; LAN PA/intercom = native Opus-over-WS; backups = restic -> rest-server; TURN on the411's second IP.

## 1. Media server choice

| Option | Licence | Version / last release | 1:1 fit | Group | E2EE (2026) | RAM / CPU (published or estimated) | Verdict |
|---|---|---|---|---|---|---|---|
| **Plain WebRTC P2P + coturn** | coturn BSD-3 [secondary, from memory]; browsers built in | coturn: not re-verified | best | no (mesh, 3-4 max) | DTLS-SRTP end-to-end; TURN sees ciphertext only | coturn idle RSS: no published figure found [unverified]; C daemon, expect tens of MB [estimate] | **Primary for all 1:1.** Needs STUN/TURN on a public IP = the411's second address. |
| **LiveKit server** | Apache-2.0 [verified, repo page] | **v1.13.10, 2026-10-14** (v1.13.9 2026-10-07) [verified] | works (even 1:1 via SFU) | yes; simulcast, SVC | **Yes**: media tracks AND data channels, shared-key or custom key provider; signalling is TLS only, key distribution is YOUR job [verified, docs.livekit.io/transport/encryption] | No published idle RSS for Pi/ARM [searched, none]. Third-party sizing: Element Call+LiveKit ~10 participants on 4 vCPU/8 GB incl. a homeserver [secondary, bigiron]. This repo's own note guessed +~560 MB incl. agent while in use [repo, est.]. The existing `livekit` container is `Exited (137)` = OOM-killed 5 days ago [repo, today] | **Use only for 3+ and only on the411.** On one IP it needs: UDP mux 7882, TCP 7881, TURN UDP 3478, TURN/TLS 443 (if no LB) [verified, docs ports page]. |
| LiveKit SIP | Apache-2.0 [verified] | release date not shown on page | n/a | n/a | PSTN is not E2E | needs Redis + public 5060 + RTP 10000-20000, `--network host` [verified] | Only if a phone number is ever wanted; Twilio AU DID US$3.00/mo + US$0.01/min inbound [repo research, secondary]. Skip. |
| LiveKit Cloud | proprietary service | - | - | - | client-side E2EE possible | - | **Non-option**: breaks "no third party sees media/metadata" and "local-first". |
| **Galene** (Go) | **MIT** [verified, galene.org; one aggregator wrongly says AGPL] | 1.0 (2025-08-09); 1.1 listed only in Wikidata (2026-06-21) [unverified] | ok | yes; "~20 per core many-to-many, ~300 per core lecture" [verified, own site] | **None** - media decrypted and re-encrypted on the server [verified] | tiny; built-in TURN [verified] | Smallest, but the411 operator would see other households' media. Reject on principle 2. |
| Jitsi (JVB) | Apache-2.0 [secondary] | not confirmed | ok | yes | optional Insertable-Streams E2EE, mobile must be built with it [secondary] | handbook: 4 GB for small meetings, 2 GB "test servers"; JVB RSS capped <1.5 GB in old load test [secondary] | Too heavy (Java stack: prosody+jicofo+jvb). Reject. |
| Janus | GPL-3.0 [secondary] | 1.4.1 ~2026-04/05 [secondary] | ok | yes | none built in (you would write it) | C, small | Library-grade; you build the app + E2EE. Reject (adopt-don't-rebuild). |
| mediasoup | ISC [secondary] | 3.2x, versions conflict across sources [unverified] | ok | yes | none built in | Node + C++ worker | A toolkit, not a product. Reject. |
| pion ion-sfu | MIT | v1.11.0, **2021-11** [secondary] | - | - | - | - | Dormant. Reject. (Pion the library is alive; LiveKit is built on it.) |
| BigBlueButton | LGPL-3 | - | - | - | none | "will not install on 2 vCPU/4 GB" [secondary] | Reject. |
| Matrix + Element Call (MatrixRTC) | Element Call: **AGPL-3.0 or Element Commercial Licence** [verified, repo page]; LiveKit Apache-2.0 | Element Call v0.28.0 (This Week in Matrix 2026-10-16) [verified] | works, via LiveKit even for 1:1 | yes | **Yes** (Matrix-native, on by default) [secondary] | homeserver + LiveKit + lk-jwt-service | See section 3. Synergy is real (LiveKit is the SFU inside MatrixRTC) but you carry a homeserver. |

### 1.1 Where each piece should run

| Piece | On the box (Orin)? | On the411? | Why |
|---|---|---|---|
| Signalling for calls | in zoe-data (WS already exists to panels) | a thin hub for cross-household | box is CGNAT: it dials OUT to the411 |
| STUN/TURN | **no** (CGNAT: a TURN server behind CGNAT relays nothing) | **yes**, on the relay's second public IP | TURN must be publicly reachable; it only sees ciphertext |
| SFU | **never** (RAM; the 560 MB-class guess already OOM-killed the old container) | only if 3+ party video is wanted | media transits the411; E2EE (LiveKit key provider) keeps it blind |
| Matrix homeserver (if chosen) | no | yes | RAM + public federation |

### 1.2 Per-topology recommendation

| Topology | Recommendation | E2E? | New RAM |
|---|---|---|---|
| **In-house panel<->panel** | WebRTC P2P between the two kiosk Chromiums; signalling over the existing panel WS in zoe-data; host candidates on the LAN, no TURN. Audio-first; video at 480p. Pi 5 has no hardware H.264 *encode* [from memory, not re-verified] so software VP8 encode will cost a core: measure before promising video. Mic: the Python daemon uses PyAudio through **PulseAudio** (not raw ALSA) [repo, panel-as-ma-speaker-feasibility.md], so Chromium can open a second capture stream; no exclusive-device fight. Daemon must pause wake-word/STT while a call is live (policy, ~30 lines). | yes (DTLS-SRTP) | 0 on Orin |
| **Panel<->phone, away** | Same P2P; ICE via STUN + TURN on the411 (`turn.relay.the411.life`, UDP 3478 + TLS). Phone side = Zoe PWA call page. Fallback ping via Telegram. | yes | 0 on Orin; the411 +coturn |
| **Household<->household** | Same P2P; signalling = signed envelopes through the411 hub (6.3); both households use the411's TURN. Grandma's side: panel kiosk (a Zoe) or PWA. Group (3+) -> LiveKit on the411 with a per-call shared key distributed in the signed envelope. | yes | 0 on Orin; the411 +TURN, (+LiveKit later) |

TURN bandwidth: media only transits TURN when direct/STUN fails (behind-CGNAT mobile to CGNAT home is the common failure). The fraction is often quoted at 10-20% [estimate]; a relayed 480p video leg is ~1-2 Mbit/s each way [estimate]. Check the droplet's monthly transfer allowance (unknown here) before P3 video. `docs/architecture/panel-anywhere.md` already caps frp at 20 Mbit/s per household; TURN needs its own quota (LiveKit's embedded TURN gained per-participant allocation quotas in v1.13.6 and refuses TCP relays in v1.13.10 [verified]; coturn has `user-quota`/`total-quota` [from memory]).

### 1.3 How it plugs into the chosen relay (panel-anywhere sections 2, 9)

- frp's `https` proxy is SNI passthrough for TLS to the box. **Media does not ride frp** (frp proxy types other than https are refused by the session-master plugin by design). Calls therefore add two things on the relay's second IP: `3478/udp` (STUN+TURN) and TURN/TLS. TURN/TLS can share 443 through the existing nginx `stream` `ssl_preread` front by SNI (`turn.relay.the411.life` -> loopback TURN TLS port); **do not use PROXY protocol toward coturn on a TLS port** (coturn issue #1642 [secondary]); the cost is losing the client IP in TURN logs, which is fine.
- Cross-household signalling does NOT need a public name on any box: each box keeps an outbound WSS to a small "hub" on the411 (same session-master registry, same household identity). That keeps zoe-data off the internet and keeps R5 (public name) optional.

## 2. LAN intercom and PA without WebRTC

Facts about the panel [repo]: kiosk Chromium; Python daemon owns the mic through PulseAudio (`AUDIO_DEVICE=pulse`); TTS plays via short-lived `aplay -D pulse`; the daemon already ducks with `pactl set-sink-input-volume`; the panel is also a live AirPlay-2 speaker for Music Assistant (shairport-sync). Pulse mixes streams.

| Option | What it gives | Fit | RAM | Verdict |
|---|---|---|---|---|
| **Opus-over-WebSocket in Zoe (build)** | one-to-all, one-to-room, PTT; Zoe TTS announcements as a pre-rendered clip; door/ring chimes; ducking via the daemon's existing `pactl` duck | reuses panel push channel + room=panel identity + the household's auth; works through the relay later | Orin +0 MB; Pi daemon +~5-10 MB [estimate] (libopus) | **Choose.** ~250 lines for PA (server fan-out + daemon play + duck), ~400 for PTT intercom (daemon capture/encode + hub + a Talk button), +~150 for open-duplex |
| HA `assist_satellite.announce` / `start_conversation` | announce to a device/area/floor/label; multi-turn needs a multi-turn agent [verified, home-assistant.io] | panels are not HA satellites; making them one means `wyoming-satellite`/ESPHome owning the mic - the exact conflict to avoid | n/a | Reject |
| Music Assistant announcements | native on Sonos/Snapcast/Sendspin; **MA pauses and restores music, it does not duck**; ducking is an open request (#998 Jan 2026, Sendspin spec #305, support #6616) [verified/secondary]; a Sendspin announcement bug was filed 2026-10-07 (#6663) | fine for "Zoe says X in the kitchen" if the panel were an MA player, but latency, no live voice, no PTT | 0 extra | Use only as a bonus for TTS PA to non-panel speakers |
| Snapcast | synced multi-room playback; snapserver v0.35.0 [secondary, date unclear]; MA can use it | solves *sync* (rooms that overhear each other); not a mic path; adds a second audio pipeline per panel | snapserver/snapclient figures not found [unverified] | Not P0. Revisit only if adjacent rooms echo |
| **Mumble / murmur** | channel-per-room, native PTT, whisper-to-channel, Opus, mature; murmur ~29 MB RAM on a Pi 1 in an old Tom's Hardware test (version not named) [secondary]; mumble-server 1.5.939 is the latest patch I found [secondary]; Go headless client talkkonnect (PTT/GPIO) on SBCs [secondary]; talkiepi archived 2020 [secondary] | Android/iOS clients exist [unverified list]. But: second identity+ACL system, second always-open mic consumer per panel, TCP-in-TLS without SNI routing so the SNI relay is awkward, no video/ring/message story | ~30 MB Orin | Best *adoptable* alternative; reject because every Zoe feature (room = panel, who is speaking, guest rules) would need a mapping layer anyway |

Design notes for the build: (1) PTT is half-duplex so no echo problem; (2) open-duplex needs echo cancellation - the panel's USB speakerphone is a Jabra Speak 750 [repo]; whether its AEC is hardware is [unverified], fallback is PulseAudio `module-echo-cancel` (webrtc) [from memory]; (3) the daemon must mute wake-word + STT while a stream is live and surface "someone is talking" on the panel; (4) PA is gated: only a signed-in member or Zoe (never a guest session) may broadcast; log every PA with the sender; (5) rooms already exist as the panel registry; "all" = every panel in the household.

## 3. Messaging, calls and the account layer on the411 - Matrix as the incumbent

### 3.1 What the repo already has (coordinator's note)
548-line zoe-core router `/api/matrix` (sqlite users/rooms/messages, a Zoe bot, QR onboarding at zoe-pi.local, household setup) and a `matrix-mcp-server` pointed at **matrix.org**; no homeserver was ever in compose. Two consequences: (1) matrix.org as the homeserver contradicts "no third party" and cannot be the answer; (2) the sqlite users/rooms/messages tables are a *second* source of truth that should be retired (not extended) when a real homeserver arrives - "retire by removing". I did not re-read that router; the line count and shape are as reported.

### 3.2 Matrix, concretely for 2026

**(a) Homeserver pick**

| Server | Status 2026 | RAM guidance | Element Call needs (MSC3266/4222/4143/4140) | Appservices / bridges | Verdict |
|---|---|---|---|---|---|
| **Synapse** (Python, Element) | 1.163.0 current; active | ~1-2 GB small (Element sizing 2000 MiB for 1-500 users; tutorials ~1 GB idle; GitHub reports of 1.6 GB on 2 GB VPS) [secondary] | all, documented in Element Call's own self-hosting guide [verified] | reference implementation; **MSC4190 supported, needs Synapse >= 1.141 for bridges with MAS** [secondary] | Safe for calls + bridges; heaviest. |
| **Tuwunel** (Rust, matrix-construct) | **v1.9.4, 2026-10-10** [verified]; releases roughly every 1-2 weeks; "official successor to conduwuit", full-time sponsored staff (a directory page says Swiss government sponsorship, unverified); aarch64 builds [verified]; in-place conduwuit DB migration | **512 MB minimum, >=1 GiB advised for a private family server, ~2 GiB per core at scale**; memory scales with the size of rooms joined, not accounts [verified, maintainer discussion #227, not official docs]; v1.7 notes warn about raised cache modifier costing 25-50% more RAM [secondary] | 3266, 4222, 4143, 4158 yes; **4140 delayed events NO** (PR #555, Sept 2026, unmerged as far as I could see), 4075 ringing no, 4310 no [secondary: tuna-os/spindle #35]; lk-jwt-service needs `/openid/userinfo` (federation API) - issue #240 "federation disabled" [secondary, status unknown] | **appservice docs show registration via admin room/config, MSC4190 and MSC3202 options** [verified] | Best fit for messaging on a small droplet; **calls blocked on MSC4140.** |
| **Continuwuity** (Rust, community) | active, volunteer-run; own forgejo; self-description: more user-facing fixes, next-gen OIDC "in the pipeline"; Tuwunel has LDAP [secondary] | one aggregator says 64-256 MB for 10-100 users [secondary, unverified] | same gap as Tuwunel (4140, 4075 missing) [secondary] | one aggregator says "does not support application services" - **conflicts with Tuwunel's own docs; unverified** | Alternative to Tuwunel; no advantage that matters here. |
| conduwuit | **archived 2026-05-29** (read-only) [secondary: repo page via search] | - | - | - | Dead. Do not use. |
| Dendrite (Go) | **maintenance mode, security fixes only** (Element's repo README) [secondary]; one aggregator says last active Nov 2024 | 256-512 MB [secondary] | not supported as far as found | limited | Do not use. |

Decision rule: ask the owner for the droplet size. If >= 4 GB RAM free after the other sites: **Synapse** from day one (one server for messaging + calls + bridges; cost 1-2 GB). If < 2 GB free: **Tuwunel for messaging now, no Element Call until MSC4140 merges** (then re-test; meanwhile calls use the native P2P Plan B).

**(b) MatrixRTC / Element Call state (2026-10)**
- *Stability:* MSC4143 (MatrixRTC), MSC4195 (LiveKit transport), MSC4196 (calling app), MSC4354 (sticky events) entered **Final Comment Period 2026-10-15**; MSC4075 (invites/notifications = ringing) is the holdout, still in proposal review with an open "session identity / simultaneous invites" question and `needs-implementation` [verified, This Week in Matrix 2026-10-16]. Element Call v0.28.0 (same source). Element Call is the default calling in upstream Element Web [ESS Pro 26.7.1 notes, via search, secondary].
- *Does it ring?* iOS: Element X registers PushKit VoIP pushes and reports to CallKit [secondary, DeepWiki 2025]; 2026 had regressions (a Feb 2026 draft PR #5026 on "never posted an incoming call to the system", 26.04.1 "required video for CallKit ... workaround answering on lock screen", 26.06.0 "clear incoming-call state"). Android: Play listing declares USE_FULL_SCREEN_INTENT for locked-screen calls [secondary]; open complaints that a call sometimes arrives as a plain banner with no ring (#4802, #4390, a Pangea/other-client report 10 days old), and "calls not ringing with UnifiedPush" (#8428, old). Ring is decided client-side ("ring if two-member room, else notify" per an issue author's reading). **Verdict: ringing exists and is the best on offer, but must be acceptance-tested on the family's actual phones; do not promise it.**
- *Needs its own SFU?* Yes, always, even 1:1: LiveKit + `lk-jwt-service` + proxy routing `/livekit/sfu` -> 7880 and `/livekit/jwt` -> 8080; homeserver MSCs 3266, 4140, 4519 (flag `msc4143_enabled` in Synapse), 4222 [verified, Element Call self_hosting.md]. Element's free hosted backend was withdrawn, so self-hosters must run it.
- *Can the411's SFU be E2EE so the411 never sees media?* Yes by design: LiveKit E2EE covers tracks and data; LiveKit says the key provider is the integrator's job and the SFU cannot read content [verified]; Element states per-participant keys and E2EE "maintained throughout", with keys exchanged over Matrix [Element blog, secondary]. Residual: signalling, room/participant identities and timing are visible to the homeserver and SFU. Also the *homeserver admin is the owner* - for other households that is a third party, which is why E2EE (not just TLS) matters.
- *LAN hairpin:* with Element Call every call, even two panels in one house, goes panel -> the411's SFU -> panel. That is why in-house calls and PA/intercom stay native (section 2).

**(c) Per household a user on the411, vs a homeserver per box**
| | One homeserver on the411, household = user(s) | Each box runs its own homeserver, federating via the411 |
|---|---|---|
| Orin RAM | bot only (+60-120 MB est.) | +0.5-1 GB: not possible |
| Reachability | none needed (outbound /sync) | federation inbound via the relay needs the R5 public name + ACME cert + 8448/443 |
| At rest on the411 | E2EE ciphertext + metadata of every household | only ciphertext of rooms the411 participates in, own server holds the rest |
| Privacy upside | none beyond E2EE | real, but unaffordable on this box |
| Verdict | **choose** | reject (RAM + CGNAT); revisit only on a bigger household box |

**(d) Bridges.** mautrix v26.09 (all 2026-09-16): telegram v0.2609.0 (legacy Python line ended v0.15.3; /v1 provisioning API removed v26.06), whatsapp v26.09, signal v26.09, mautrix-go v0.31.0 [secondary, mau.fi blog]; a new Instagram bridge appeared July 2026 [secondary]. They run as appservices and need the homeserver to push to them, so they live on the411 (Docker, each tens of MB [estimate]). Privacy trade-offs: end-to-bridge encryption means the homeserver's disk is ciphertext, but the bridge process on the411 holds plaintext and the member's WhatsApp/Signal login. Telegram needs no bridge (native lane). Treat WhatsApp/Signal as opt-in per member, after P2.

**(e) Zoe bot per household.** Primitive for Zoe-to-member and Zoe-to-Zoe: a Matrix user `@zoe_<hid>:the411.life` per household, DMs/rooms with members' accounts. Zoe-to-Zoe = a room between two bot users whose members have accepted an invite (+ cross-signing verification via emoji/QR, voice-confirmed on the panel). Implementation choices: matrix-rust-sdk (via bindings) or mautrix-python/nio with Olm/vodozemac state on the box. Do not use an appservice (see 0.5). Egress: outbound only. Lines: ~600-900 for bot + delivery policy (leave-a-message, room routing, quiet hours) [estimate], replacing the 548-line sqlite router and the ~1,400 messaging lines of the bespoke plan. **Open risk (lab cell first):** E2EE bot device lifecycle (device verification, key backup of the bot's own keys on a box that may be restored from backup - restoring an old Olm state forks the device).

**(f) Phone: Element X vs PWA**
| | Element X | Zoe PWA |
|---|---|---|
| Messages | full chat, E2EE, history, media, QR device login | only what we build |
| Calls | native ring attempt (PushKit/CallKit, full-screen intent), Element Call UI | banner only on iOS (section 4) |
| Push | iOS: Element's gateway + APNs; Android: FCM or UnifiedPush/ntfy; payload is `event_id_only`, content fetched by the app [from memory] | Web Push direct from the box |
| Onboarding | install app, sign in (registration token / MAS); recovery key prompt | Add to Home Screen, QR pair |
| Fits "users never run code" | yes (app install + sign-in, one-off) | yes |
| Cost | ops of homeserver | ~1,400 lines + no ring |
Use **Element X as the Zoe app** for people who will take calls; the PWA remains the fallback and the kid/guest path (no account).

### 3.3 Honest downside list for the Matrix path
1. **Synapse RAM** 1-2 GB on a droplet shared with other sites; Tuwunel avoids it but cannot yet host Element Call properly (MSC4140).
2. **Element Call maturity**: 1:1 "telephone" behaviour (ring, decline, handset mode) is still on the roadmap; MSC4075 unfinished; 2026 iOS CallKit regressions; Element X Android 26.04.4 self-host `.well-known` failures (fixed server-side config, but shows how brittle discovery is); lk-jwt-service/federation coupling on Tuwunel.
3. **E2EE key backup UX for non-technical family**: Element X ties history to a recovery key; "if you lose your recovery key and all your devices, you will need to reset your digital identity ... previous messages can not be decrypted" [Element help, secondary]; a fork's issue (2026-10-06) says the key is offered once and can be skipped. Mitigation in Zoe's spirit: Zoe generates/escrows the recovery key in the household credential vault (Vaultwarden design #2100) and shows it on the panel as a QR at onboarding.
4. **Verification prompts**: every new login must be verified (another device or recovery key); Element announced unverified devices will stop sending/receiving E2EE messages, originally April 2026, postponed in Sept 2026 to an unspecified date with one month notice [verified, Element blog]. The Zoe bot's device and the panel's Element Call identity must be verified too.
5. **Metadata** on the411: who talks to whom and when, push tokens, device lists.
6. **iOS push is not private**: Apple and Element's gateway see delivery metadata; own Sygnal needs your own app build + Apple credentials.
7. **LAN hairpin** for Element Call (mitigated: native LAN path).
8. **Two chat apps** (Element X + Telegram) unless Zoe's Telegram lane is later replaced.
9. **Spec churn**: MSC4143/4195/4196/4354 only just in FCP.
10. **Bridge risks** (plaintext in bridge, WhatsApp ban risk).
11. **Ops burden** on the411: homeserver upgrades, `.well-known` on the bare domain, registration policy, backups of the homeserver DB (add to restic).
12. **Retire the sqlite `/api/matrix` router** or it becomes a second truth.

### 3.4 Alternatives (kept short)
- **Bespoke Zoe envelope over the relay (Plan B)**: signed+sealed envelopes, directory + mailbox on the411 (~800 lines), Web Push, native P2P calls with coturn. Wins on RAM and E2E-by-construction, loses on phone ringing and on build size. Use if the droplet is small or Element Call proves unreliable on the family's phones.
- **XMPP (Prosody / Snikket)**: Snikket stable.20260611 (Prosody security + memory-leak fixes, TURN IP detection); 1 GB advised (10-15 users fit in ~128 MB per FAQ), bare Prosody 40-80 MB; Jingle P2P+TURN built in; Android app from Conversations; Monal iOS 6.4.21 (2026-05-27) lists audio/video calls, CallKit use not confirmed; Snikket iOS app status unclear [secondary]. No federation E2EE parity with Matrix, weaker bridges, no browser Jingle client for the panel. Runner-up only.
- **Nostr**: reasoned only (no 2026 source read): no mainstream family client, no ring, no push story. Not recommended.


## 4. Phones in 2026 - exactly what each path can do

| Path | Message Zoe / get Zoe's messages | Zoe rings your phone | Notes |
|---|---|---|---|
| **Telegram (today)** | yes (text, voice notes, photos; lane live) | **no**: Bot API has no calls [repo research §0 point 4, Bot API 10.3]; a bot can send a Mini App/URL button that opens Zoe's WebRTC page | Telegram sees plaintext (not E2E for bots). Good fallback "ring" = a message with a button. |
| **Zoe PWA + Web Push** | yes | **banner only.** iOS (installed PWA, 16.4+): push via APNs, one notification per push, no silent push, no ringtone loop, no wake-to-call; WebRTC works in Safari/standalone but only one `getUserMedia` capture at a time and device ids randomise per page load [secondary, webrtchacks/WebKit threads]. Android Chrome: persistent high-priority notification possible, still no ringtone [from memory] | Must be tested on an iPhone and an Android before promising anything. Needs "Add to Home Screen" once (a tap, not code). |
| **Element X (Matrix)** | yes | yes via MatrixRTC push (`m.rtc.notification`/`m.call.notify`), reliable on the Element push gateway; **ring decision is client logic ("ring if member count == 2, else notify" per one issue reading)** [secondary]; UnifiedPush ringing reports are old and mixed [secondary]; spec MSC4075 unfinished | Real native ringing, but only if you adopt the Matrix stack. |
| **Monal / Conversations (XMPP)** | yes | yes (Monal lists audio/video calls; CallKit use not confirmed [unverified]) | needs XMPP stack. |
| **WhatsApp Business Calling API** | n/a | user-initiated calls free (pricing page updated 2026-05-21) but business-initiated needs a messaging tier of 2,000 business-initiated conversations/24 h to enable calling and permission requests (max 1/24 h, 2/7 days); API reaches your WebRTC/SIP system, not a phone number [verified/secondary] | Built for brands; Meta sees signalling. **No-go** (agrees with repo research). |
| **SIP softphone + DID** | n/a | yes, PSTN-grade ringing | carrier + trunk hear audio (not E2E); LiveKit SIP + Redis + public RTP, or Twilio Media Streams over WSS; AU number US$3/mo + US$0.01/min [repo research]. Opt-in only. |
| **Thin Capacitor/Tauri app (later)** | yes | **yes**: CallKit + PushKit on iOS (Apple requires a CallKit report for every VoIP push), ConnectionService/full-screen intent on Android | Needs an Apple Developer account and a push sender; do it only if the PWA ring proves inadequate. |

Push without a big-tech relay is impossible on iOS: ntfy's iOS instant delivery relays a poll request (message id + SHA-256 of the topic) through ntfy.sh -> Firebase -> APNs; UnifiedPush is Android-only [verified, ntfy docs via search]. Web Push and any iOS app go through Apple's APNs by necessity; payloads are encrypted, only metadata is visible.

### 4.1 "One Zoe app" = Omi companion pieces + Matrix/Element (owner idea; verified 2026-10-18)

**Omi app (BasedHardware/omi).** Licence **MIT**, "Based Hardware Contributors" 2024, no extra restrictions on the LICENSE page [verified]. Stack [verified, README]: mobile app `app/` = **Flutter** (iOS + Android); backend `backend/` = Python/FastAPI on **Firebase/Firestore + Deepgram + Redis + LLM providers**; firmware nRF/Zephyr C; macOS Swift app; SDKs. **Forkable but not clean:** the app's backend host was hard-coded to `api.omi.me` (issue #6603, ~187 days old); a debug-only "custom backend URL" in Developer Settings **merged 2026-10-09** (PR #21057: release builds ignore the override and fail closed to the flavor backend; the Omi bearer token is never sent to a custom host) [verified]. So a shipping Zoe build needs a fork anyway (release builds ignore the override), you still need your own Firebase project/config (`flutterfire configure`; auth can be bypassed server-side with `LOCAL_DEVELOPMENT`, but the app wants a Firebase config) [secondary, community gist + issue], and Flutter's `dart:io` TLS does not honour the iOS-installed custom CA, so a household-CA/`.zoe.internal` endpoint needs a pinned `SecurityContext` or a publicly trusted name (issue #6603) [secondary]. The pieces worth borrowing are the BLE/Opus protocol SDKs and the device-pairing/recording UI, not the backend. **This repo already syncs the pendant on the panel Pi** (`routers/omi_recordings.py`: the Pi sync service uploads each recording with a device token, custody confirmed by sha256 echo before the Pi frees the pendant's audio), so a phone BLE path would be a *second* ingest and should reuse `POST /api/omi/recordings` unchanged. I did not read Omi's BLE sync code, its offline-storage format or its background behaviour (searches returned only generic flutter_blue_plus facts).

**Flutter + Matrix in 2026 - three real options**

| Option | Licence | E2EE | Calls | Effort | Risk |
|---|---|---|---|---|---|
| **famedly `matrix` Dart SDK** (FluffyChat's) | **AGPL-3.0** [verified pub.dev] | **vodozemac** (libolm dropped June 2025; 3.0.0 Oct 2025 no longer needs flutter_openssl_crypto) [secondary] | FluffyChat **2.10.0 (F-Droid 2026-10-04)** ships E2EE MatrixRTC calls on Flutter's LiveKit package, 1:1 + group, **CallKit integration on Android and iOS**, Element Call-compatible; call button only if the homeserver advertises MatrixRTC in `.well-known`; early bug of a LiveKit auth failure on conduit-rs [secondary, This Week in Matrix 2026-09-11/10-02, issue #3728] | Medium: embed `matrix` SDK, copy FluffyChat's call code (AGPL: whole app becomes AGPL, fine for a household app, bad if ever closed-source) | 2.10.0 is days old; MSC4195 transport was still at 4/8 approvals on 2026-10-02 (FCP since 10-15) so implementations run ahead of spec |
| **matrix-rust-sdk via FFI** (Element X's engine) | **Apache-2.0** [secondary] | best-in-class (the Element X engine; sliding sync, Rust crypto) | Calls are Element Call in a widget/webview; Element's native `matrix-rust-rtc` is **AGPL-3.0 or Element Commercial** and at v0.5.0-rc.2 (2026-10-08), with Kotlin/Swift bindings only, **no Flutter binding** [secondary] | High: official bindings are Swift (`matrix-rust-components-swift`) and Kotlin only; Flutter would mean `flutter_rust_bridge` (MIT, 2.13.0) over matrix-sdk, and an old issue shows codegen failing on matrix-sdk's backup module (2.7.0, maybe stale); you own the glue forever | High |
| **Wrap, don't merge**: Capacitor/webview around Zoe's PWA + **Element X installed separately** | PWA yours; Element X AGPL-3.0 (store app, not your code) | Element X's | Element X's (native ring) | Lowest | two apps |
| Webview embed of **Element Call** (widget mode) inside the Zoe app | Element Call AGPL-3.0 or commercial [verified] | LiveKit E2EE | Element Call widget (widget host must implement the widget API, auth, room state; "embedded packaging recommended") | Medium-high: you become a Matrix widget host | no native CallKit/ringing unless you add it |

**Verdict (one paragraph).** The cheapest credible "one Zoe app" today is **a Flutter (or Capacitor) Zoe app that is NOT a Matrix client: it carries the Omi-style pendant companion + Zoe's own screens (day review, people tagging, household lists, pairing via QR), and delegates people-to-people chat and calls to Element X, deep-linked from Zoe (`element://`/matrix.to links).** If the owner insists on a single binary, the next-cheapest is **Flutter + famedly `matrix` SDK (the FluffyChat 2.10 code path)**, because it is the only 2026 stack where Dart already has E2EE (vodozemac), MatrixRTC calls over LiveKit and CallKit wiring in one codebase, at the cost of AGPL-3.0 and a days-old call stack; wrapping matrix-rust-sdk through FFI is the best crypto but the most work, and a webview Element Call gives you calls without a native ring. **What even the best composition still lacks:** (1) *iOS ringing* needs a PushKit VoIP push + CallKit report on every push and an Apple Developer account, so a Zoe-owned iOS binary means running your own Sygnal/APNs key (Element X's push gateway only serves Element's own bundle id), and Element X itself has had CallKit regressions in 2026; (2) *background BLE on iOS*: `bluetooth-central` + state restoration keeps a connection alive but only while BLE activity is pending, wakes give roughly 10 seconds, iOS negotiates a smaller MTU than Android (135-255 vs 512 bytes) and background scans stop after minutes-to-hours [secondary, flutter_blue_plus docs/threads], so bulk pendant sync from an iPhone is unreliable in the background; **the panel-Pi sync that already exists is the reliable path, with the phone as a foreground "sync now" convenience.** (3) Android is easier on both (foreground service, full-screen intent) but UnifiedPush call-ringing is unproven.

## 5. Backups to the411

Reference UX (Nabu Casa, 2025.1) [secondary, support.nabucasa.com + HA 2025.1 blog]: one toggle, runs with automatic backups, **one latest encrypted backup kept, <5 GB, AES-128, Nabu Casa has no key; the key is in an "emergency kit"**; downloading via the HA UI yields a decrypted file. Our version should keep more than one generation (their single-copy is their weakness).

| | restic 0.19.1 (2026-07-05, BSD-2) | Borg 1.4.5 (2026-07-18?) | rclone crypt |
|---|---|---|---|
| Server needed on the411 | **rest-server** (BSD-2; `--private-repos` = user may only touch `/<user>/...`, htpasswd bcrypt, `--append-only`, TLS flags) [verified] | `borg serve` over SSH (needs shell accounts) | `rclone serve` + files; no dedupe/snapshots |
| Client memory | published numbers disagree (150 MB vs 400-800 MB) and none are ARM [secondary]; measure on the Orin | similar class | low |
| Fit | best: Go static binary, HTTP, per-household repo, same session-master credentials | SSH accounts per household = new accounts, no | no snapshots, weak retention |

Recommendation: **restic -> rest-server (private repos, one per household id) behind HTTPS on the411**, repo password generated on the box, shown once as a QR/print "emergency kit", also escrowed by the household's credential vault (see the vault design). Run from a systemd timer in the box's quiet window (must stay out of 01:50-04:55 per repo rule). Pipeline: `pg_dump -Fc | restic backup --stdin`; SQLite `.backup` of `data/*.db` (zoe.db is 454 MB today [repo, measured]); Chroma: back up only the sqlite and rebuild HNSW (the repo's own notes say docs live in sqlite and the index is rebuildable) ; `.env`/secret files inside the encrypted repo; the voice corpus (225 MB) optional; **never `models/` (34 GB, re-downloadable; back up only the manifest/hashes)**. Measured sizes: `data/` 758 MB, `~/.zoe` 6.4 GB (not analysed what is in it), voice corpus 225 MB [repo, today].
Append-only caveat: rest-server `--append-only` blocks deletes, but pruning then needs a trusted second client, and restic issue #22057 argues a compromised client can set snapshot times to defeat keep policies [secondary]. Practical rule: append-only + per-household quota on the411, and rotate to a new repo yearly; do not store the repo key on the411, so the411 cannot prune for you. Restore UX: "new Zoe -> sign in to the411 -> enter/scan the emergency kit -> restore."
Cost on the box: restic runs only during the backup window; RSS is transient (measure; do not run next to the 12B night window).

## 6. Target architecture (hybrid: Matrix for people, native for the LAN)

### 6.1 Diagram

```
 PHONES / FAMILY AWAY                       the411.life  (existing DO droplet, root, host nginx; SIZE UNKNOWN - ask)         THE HOUSE (CGNAT, no inbound)
 ------------------------      +-------------------------------------------------------------------------------+    --------------------------------
 Element X  (E2EE chat,        |  existing sites on primary IP (unchanged)                                      |    Orin 16 GB (RAM ~0.6-0.9 GB free)
   Element Call, ring attempt) |                                                                               |      zoe-data
 Zoe app  (Omi-style pendant   |  SECOND PUBLIC IP = relay.the411.life                                          |       |- panel WS hub (rooms = panels)
   + day review; later)        |   :443 nginx stream (ssl_preread):                                            |       |- PA / PTT fan-out (native)    [P0]
 Zoe PWA  (fallback, kids,     |        <hid>.zoe.internal  -> frps (SNI passthrough, TLS ends on the box)      |       |- Zoe bot user (Matrix, E2EE)  [P1]
   guests)                     |        matrix.the411.life  -> homeserver (TLS at nginx; .well-known on bare)   |       |- backup timer (restic)        [P1]
 Telegram (existing lane)      |        livekit/turn names  -> LiveKit SFU + TURN/TLS                           |      frpc <== outbound
        |                      |   :3478/udp, 7882/udp, 7881/tcp  LiveKit/TURN media (UDP mux)                  |      Matrix bot  ==== outbound /sync ==> homeserver
        +--HTTPS/WSS/APNs/FCM-->   session master + device registry (frp plugin)      --- exists (F0 81/81)       |
                               |   Matrix homeserver (Tuwunel msgs / Synapse calls)  [P1, adopt]                |    Pi 5 wall panels (per room)
 grandma's Zoe (a household    |   Sygnal not needed for Element X (Element's gateway)                          |      kiosk Chromium (+Element Call widget P2)
   with its own bot user) ---> |   lk-jwt-service + LiveKit SFU (E2EE keys via Matrix)        [P2, adopt]       |      Python voice daemon (mic via Pulse;
                               |   mautrix bridges (WhatsApp/Signal opt-in)                  [P3+, adopt]       |       Opus WS PTT/PA, ducking)  [P0 native]
                               |   restic rest-server --private-repos (repo per household)    [P1, adopt]       |      Jabra speakerphone, camera
                               +-------------------------------------------------------------------------------+

 MEDIA:  LAN panel<->panel + PA/intercom: native, never leaves the house.  Phone/other household/group: Element Call -> LiveKit on the411,
         media E2EE (the411 relays ciphertext; it learns who/when). Plan B (if Element Call is not good enough on the family's phones or the
         droplet is small): native P2P WebRTC + coturn on the411 with signed envelopes (sections 1.2, 6.3).
```

### 6.2 Phases, RAM budget, adopt vs build (Zoe lines +/-50%)

| Phase | Scope | Orin RAM | Pi RAM | the411 RAM | Adopt | Build |
|---|---|---|---|---|---|---|
| **P0** LAN PA + intercom | one-to-all / one-to-room PA (TTS clip or recorded), PTT intercom, ducking, sender log, guest-denied | +0 | +5-10 MB [est] | 0 | libopus, PulseAudio duck already in daemon | ~700 (+150 open-duplex) |
| **P1** messages + backups | homeserver on the411; one human account per member + a bot user per household; Zoe bot (outbound /sync, E2EE); delivery policy ("leave a message for Sam"); retire the sqlite `/api/matrix` router; restic to rest-server; recovery-key escrow in the vault | **+60-120 MB [est] for the E2EE bot (measure)** | 0 | **Tuwunel 0.5-1 GiB or Synapse 1-2 GB** + rest-server ~20-30 MB [est] + relay stack 40 MB measured (16+20+3) | Tuwunel/Synapse, Element X, restic, rest-server | ~700-900 (bot + delivery policy + onboarding via QR/registration token); net ~-550 after deleting the old router |
| **P2** calls panel<->phone | LiveKit + lk-jwt-service + `.well-known` rtc_foci on the411; Element Call widget on the panel kiosk (panel user identity); phone = Element X; acceptance test of ringing on the family's iPhone/Android; **needs Synapse or Tuwunel with MSC4140** | +0 | Chromium already running; **measure** Element Call CPU on Pi 5 | **+LiveKit ~100-300 MB [est, no published ARM/idle figure]** + coturn/TURN quotas + bandwidth | LiveKit (Apache-2.0), lk-jwt-service, Element Call (AGPL) | ~300 (panel Matrix identity, "call Sam" intent, presence gating, daemon pause) |
| **P2b** one Zoe app | Flutter/Capacitor app: Omi-style pendant companion + day review + pairing; Element X for chat/calls; optional famedly SDK later | 0 | 0 | 0 (+Sygnal only if Zoe-owned iOS push) | Omi MIT BLE/Opus pieces, Flutter | ~3,000-6,000 for a credible app [est] (the pendant sync path already exists on the Pi) |
| **P3** household<->household | two bot users + a shared room, cross-signing verification confirmed by voice on both panels, optional federation to a friend's homeserver; WhatsApp/Signal bridges opt-in | 0 | 0 | +bridges tens of MB each [est] | mautrix v26.09 | ~400 (pairing + trust UX) |

Totals: **Orin +~60-120 MB (bot only); the411 +~0.7-1.3 GB with Tuwunel, +1.2-2.3 GB with Synapse, +~0.1-0.3 GB LiveKit** - hence the droplet-size question gates the plan. Zoe code ~2,100-2,400 lines for P0-P3 without the app (versus ~3,800-4,500 for the all-bespoke Plan B), but with ~12 new operational surfaces (homeserver, LiveKit, jwt service, `.well-known`, TURN, bridges, registration policy, key recovery...).

**Plan B (all native), for comparison:** P1 directory + mailbox + signed/sealed envelopes + Web Push (~1,400 lines, +40-60 MB on the411); P2/P3 native WebRTC P2P + coturn (~900 + ~800 lines, +10-30 MB). Cheaper on RAM and E2E-by-construction; no native phone ring; every message/call feature is Zoe's to maintain.

**Decision gates:** (1) droplet size/free RAM (>=4 GB free -> Synapse; <2 GB -> Tuwunel messaging only, calls on Plan B until MSC4140 merges); (2) a one-week acceptance cell: Element X ringing on the family's actual iPhone + Android with the app killed and screen locked, against a lab homeserver + LiveKit; fail -> Plan B for calls; (3) E2EE bot lifecycle cell (verification, backup restore without forking the Olm device).

### 6.3 Security rules that make "E2E between households" true (both plans)

1. Matrix track: E2EE rooms only (no unencrypted bot rooms), cross-signing on the bot and each member; recovery keys escrowed in the household vault and printed/QR'd at onboarding; sessions verified at pairing, never "skip".
2. Plan B track: each household has an Ed25519 signing key and X25519 box key minted on the box; SDP/ICE fingerprints travel in signed+sealed envelopes; a peer refuses a DTLS fingerprint not signed by the paired household key.
3. TURN/LiveKit credentials are short-lived, issued by the session master; logs hold household ids and byte counts only.
4. PA/intercom/call from a panel obeys the repo's identity rules: panel principal vs voice-verified member, never `guest`, never over the relay without mTLS.
5. Everything ships flag-dark with a lab cell first; any voice-daemon change goes through the replay gate (`voice_regression_probe.py` under `flock /tmp/zoe-voice-harness.lock`).

## 7. Risks and unknowns (not verified)

- The411 droplet size, CPU, transfer allowance: not in the repo; every the411 RAM figure above is relative to an unknown total. Ask the owner.
- No published idle RSS for LiveKit server or coturn on ARM/small VMs; the 560 MB figure in the Telegram research is its own estimate. Measure both in the lab before P3b.
- Pi 5 WebRTC video encode CPU in kiosk Chromium, Jabra Speak 750 hardware AEC, and iOS 26 PWA call behaviour were not tested.
- Galene 1.1 (June 2026) changelog and E2EE status not read; the site says no E2EE.
- Element X ringing against a self-hosted LiveKit stack: no 2026 source found; treat as untested.
- Tuwunel PR #555 (MSC4140) merge status and lk-jwt-service federation-off bug (#240) status unknown.
- Snapcast/mumble ARM RAM figures: none found.
- Element Call/LiveKit key exchange was read at the claim level (Element blog, LiveKit docs), not in code; the 'SFU is blind' conclusion rests on those.
- Omi BLE sync internals, offline storage format and iOS background behaviour were not read; PR #21057 override is debug-only.
- Whether Element X rings on a self-hosted stack with the app killed: no 2026 confirmation; gate (2) in 6.2.
- Nostr was assessed by reasoning only (no 2026 source read); no mainstream family-grade client or push story, so it is not recommended.

## Sources

- LiveKit releases: https://github.com/livekit/livekit/releases ; repo https://github.com/livekit/livekit ; E2EE https://docs.livekit.io/transport/encryption/ ; ports https://docs.livekit.io/transport/self-hosting/ports-firewall/ ; SIP https://github.com/livekit/sip
- Galene: https://galene.org/ ; comparison https://www.bigiron.cc/guides/self-hosted-conference-bridge-jitsi-meet-vs-galene-vs-bigbluebutton-revisited
- Jitsi requirements: https://jitsi.github.io/handbook/docs/devops-guide/devops-guide-requirements/
- Janus/mediasoup/ion-sfu: https://www.freshports.org/www/janus ; https://www.npmjs.com/package/mediasoup-client ; https://pkg.go.dev/github.com/pion/ion-sfu
- Matrix status: https://matrix.org/blog/2026/10/16/this-week-in-matrix-2026-10-16/ ; https://github.com/tuna-os/spindle/issues/35 ; https://github.com/element-hq/element-call/issues/3933 ; https://github.com/element-hq/element-call/blob/livekit/docs/self_hosting.md ; https://github.com/matrix-construct/tuwunel/releases ; https://github.com/matrix-construct/tuwunel/discussions/227 ; https://github.com/matrix-construct/tuwunel/pull/555 ; https://github.com/matrix-construct/tuwunel/issues/240 ; MSC4075 https://github.com/matrix-org/matrix-spec-proposals/pull/4075
- Element X / FluffyChat: https://docs.element.io/latest/element-server-suite-pro/migration/classic-to-pro-mobile-apps-faq/ ; https://f-droid.org/en/packages/chat.fluffy.fluffychat/ ; ringing push issues https://github.com/element-hq/element-x-android/issues/4390
- mautrix: https://mau.fi/blog/2026-09-mautrix-release/
- XMPP/Snikket/Monal: https://snikket.org/updates/release/20260611/ ; https://github.com/monal-im/Monal/releases ; https://monal-im.org/post/00005-push-on-ios/
- HA assist satellite: https://www.home-assistant.io/actions/assist_satellite.start_conversation/ ; MA announcements https://www.music-assistant.io/integration/announcements/ ; https://github.com/orgs/music-assistant/discussions/998 ; https://github.com/music-assistant/support/issues/6616 ; Sendspin https://github.com/Sendspin/spec/issues/305
- Mumble: https://www.mumble.info/blog/mumble-1.5.939/ ; talkkonnect https://github.com/talkkonnect/talkkonnect ; Pi RAM anecdote https://www.tomshardware.com/raspberry-pi/create-your-own-raspberry-pi-powered-home-voip-service-with-mumble
- Snapcast: https://github.com/snapcast/snapcast/releases
- coturn/nginx stream: https://github.com/coturn/coturn/issues/1642
- iOS PWA/WebRTC: https://webkit.org/blog/17333/webkit-features-in-safari-26-0/ ; https://webrtchacks.com/guide-to-safari-webrtc/
- ntfy/UnifiedPush: https://docs.ntfy.sh/config/ (iOS upstream section)
- WhatsApp calling pricing: https://developers.facebook.com/documentation/business-messaging/whatsapp/calling/pricing
- restic: https://github.com/restic/restic/releases ; rest-server https://github.com/restic/rest-server ; append-only caveat https://github.com/restic/restic/issues/22057
- Nabu Casa backups: https://support.nabucasa.com/hc/en-us/articles/25619338169501-About-the-Home-Assistant-Cloud-backup ; https://www.home-assistant.io/blog/2025/01/03/release-20251/
- Repo: docs/architecture/panel-anywhere.md; labs/relay/README.md (measured relay 16 MiB, plugin 20 MiB, frpc 3 MiB); docs/research/telegram-calls-voice-photos-2026-10-04.md; docs/knowledge/panel-as-ma-speaker-feasibility.md; services/zoe-data/routers/voice_livekit.py; docker-compose.yml (livekit service, `:latest`, Exited 137)
- Omi: https://github.com/BasedHardware/omi ; https://github.com/BasedHardware/omi/pull/21057 ; https://github.com/BasedHardware/omi/issues/6603 ; LICENSE https://github.com/BasedHardware/omi/blob/main/LICENSE
- Dart SDK / FluffyChat: https://pub.dev/packages/matrix ; https://github.com/famedly/matrix-dart-sdk ; https://matrix.org/blog/2026/09/11/this-week-in-matrix-2026-09-11/ ; https://matrix.org/blog/2026/10/02/this-week-in-matrix-2026-10-02/
- matrix-rust-sdk / rtc: https://github.com/element-hq/matrix-rust-rtc ; https://github.com/element-hq/matrix-rust-components-swift ; https://github.com/matrix-org/matrix-rust-components-kotlin ; https://pub.dev/packages/flutter_rust_bridge
- Element Call licence/embedding: https://github.com/element-hq/element-call
- Element verification/recovery: https://element.io/blog/verifying-your-devices-is-becoming-mandatory-2/ ; https://docs.element.io/latest/element-support/device-verification/how-to-ensure-you-have-a-recovery-key/
- Tuwunel appservices: https://matrix-construct.github.io/tuwunel/appservices.html ; conduwuit/Tuwunel/Continuwuity: https://github.com/matrix-construct/tuwunel ; https://codeberg.org/continuwuity/continuwuity ; Dendrite: https://github.com/element-hq/dendrite
- Element X iOS releases: https://github.com/vector-im/element-x-ios/releases ; PR #5026 https://github.com/element-hq/element-x-ios/pull/5026
- Repo: services/zoe-data/routers/omi_recordings.py (Pi-side pendant sync)
