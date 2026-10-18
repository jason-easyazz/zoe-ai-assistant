# Lane D — "the new Apple device" (researched 2026-10-18)

Tags: [shipping] = on sale/in customers' hands; [announced] = official Apple announcement, not yet shipping; [rumor] = press/leak only; [background] = from my own prior knowledge, NOT re-verified this session.
Note: HomeView has NOT shipped yet (ships 2026-11-13). Every "hands-on" is a pre-release demo/loaner impression, so Siri AI quality is untested in real homes.

## 0. Which device? ANSWER: Apple HomeView (the "HomePad"/"homeOS" device)
- Apple's "Welcome Home" event, 2026-10-13, announced **HomeView** (the rumored HomePad), plus HomePod mini 2 (ships 2026-10-30), a new Apple TV 4K (A19, ships 2026-11-13) and the **Apple Home Collection** (LG/Schneider cameras, lock, thermostats). [announced]
  - https://www.apple.com/newsroom/2026/10/meet-apple-homeview-a-new-way-to-enjoy-entertainment-and-manage-the-home/
  - https://www.macrumors.com/2026/10/16/apple-homeview-homepoddevice/
  - https://www.cnbc.com/2026/10/13/apple-homeview-smart-home-siri-ai.html
- The Sept 9 2026 "Surprise and Shine" event was phones/watch/AirPods (iPhone 18 Pro, iPhone Duo foldable, Watch S12/Ultra 4, AirPods 5); **no home hub there**. iOS 27 + "Siri AI" shipped in September. [shipping] https://www.macrumors.com/2026/09/09/apple-september-2026-event-recap/  and https://www.apple.com/newsroom/2026/09/siri-ai-a-profoundly-more-capable-and-personal-assistant-is-here/
- Tabletop robot (iPad on a movable arm): still NOT announced; latest secondary-sourced reporting says slipped to **2028** (Kuo: mass production 2028+). [rumor] https://fourweekmba.com/ai-apple-product-delays-ai-bottleneck-siri-2026/ (secondary; original Gurman text not verified), https://www.macrumors.com/2025/02/12/kuo-apple-robots-likely-2028-or-later/
  A "higher-end HomeView with a 9-inch display on a robotic arm" is "still in development, no date" per a MacRumors sidebar. [rumor] https://www.macrumors.com/2026/09/30/apple-home-products-coming-on-october-13/
- No Apple TV camera announced; a "privacy-focused home security camera and sensor" is only "in the works". [rumor] (same MacRumors link)
- Meta Portal: dead (consumer Portal stopped 2022; no 2026 successor found). Google: no new Nest Hub in 5 years; "Google Home Display" only seen in app code. See §4.

## 1. The device — HomeView
| Item | Detail | Tag / source |
|---|---|---|
| Name / OS | "HomeView"; software = "HomeView software" (iOS/iPadOS fork; "homeOS" was a placeholder; legal docs say "HomePodOS 27"/"HomePodDevice"). Requires iOS/iPadOS 27.2+ iPhone/iPad for setup. | [announced] MacRumors homepoddevice link above; Engadget-style detail via https://www.macrumors.com/guide/apple-command-center/ |
| Price | **$299** US, tabletop AND wall model. Wall model ships WITHOUT power adapter; mounts (Schneider Home) $59.95 USB-C / $79.95 AC wired (pro install) / $129.95 PoE (early 2027). Tabletop includes 30W adapter + 1.5 m USB-C. Apple Home Collection $39.95-$399.95 separate. | [announced] Apple newsroom link; https://www.apple.com/newsroom/2026/10/introducing-apple-home-collection-a-new-lineup-of-smart-home-accessories/ |
| Release | Pre-order 2026-10-13, **ships 2026-11-13**: US, UK, CA, AU, NZ, IN (Singapore pre-order 10-15; South Africa via resellers). **Not sold in the EU** (Siri AI not available in iOS there). English only. | [announced] Apple newsroom link |
| Chip | **A18**, with "Secure Exclaves" for camera/mic data | [announced] https://www.apple.com/homeview/ |
| Display | 7-inch **square** Liquid Retina touchscreen (1088x1088 per one report), True Tone, auto-brightness 500 nits down to **0.3 nits**. Pre-launch leaks said 6-inch (wrong). | [announced] Apple newsroom; https://www.engadget.com/2285072/apple-launch-homeview-smart-display-siri-ai/ |
| Camera | **12 MP front camera, Center Stage** (framing for calls). No physical shutter / privacy switch (Macworld). | [announced] Engadget; https://www.macworld.com/article/3261040/homeview-quickly-won-a-place-in-my-heart-and-my-home.html |
| Mics | **4 beamforming mics** (front/rear/left/right), far-field Siri | [announced] apple.com/homeview; https://www.macrumors.com/guide/apple-command-center/ |
| Speakers | 2 custom full-range drivers in display; tabletop adds down-firing woofer + 2 force-cancelling passive radiators; Spatial Audio; computational audio | [announced] Apple newsroom |
| Sensors | Accelerometer, ambient light, temperature+humidity (used for automations). **No Face ID-style depth camera, no UWB, no radar** listed. "Proximity" UI is done with the camera. | [announced] MacRumors guide link above |
| Radios | Wi-Fi 6E, **Thread**, Bluetooth 6; works with existing HomeKit + **Matter** accessories; peer-to-peer discovery for guests | [announced] same |
| Power / battery | **No battery; must be plugged in** (USB-C). No physical buttons: touch + voice only. | [announced] same; Engadget |
| Form | Tabletop: aluminum body on domed speaker base, stainless hinge tilt; 5.73in square, 9.8 mm thick; 2.1 lb. Wall: 0.63 lb. Colors tabletop silver/starlight/rose pink/space gray; wall silver/space gray. | [announced] MacRumors guide; Apple newsroom |
| Apps | **No App Store.** Built-in: Music, Podcasts, Calendar, FaceTime, Safari, Alarms, Reminders, Find My, Home, Settings, **Intercom**, Weather, World Clock, Timers, Messages. Third-party audio/video apps and widgets appear only when an authenticated iPhone is nearby (CarPlay-style entitlement; widgets from small family). | [announced] MacRumors guide; https://blakecrosley.com/blog/apple-homeview-developers-day-one |
| Dev story | At launch: no HomeView SDK/simulator; Apple never said Siri-on-HomeView invokes third-party App Intents (author calls this inference). | [announced] blakecrosley link |

## 2. Feature list (detail)
### Siri AI on HomeView  [announced; beta in HomeView software 27.2, English only]
- Conversational, follow-ups, world knowledge, **onscreen awareness**, music discovery; works for everyone in the home including users with no Apple Intelligence device. Activated by voice or swipe down from top. (Apple newsroom; MacRumors guide)
- **Personal context** (Calendar, Messages, Apple Music playlists, actions across apps) ONLY with a paired Apple-Intelligence iPhone/iPad nearby + recognised user. (Apple newsroom)
- Where it runs: "on-device or on **Private Cloud Compute**, data not stored/accessible to Apple". Server features have **daily usage limits; paid expanded access planned**. (Apple newsroom fn.2)
- Gemini: Apple says its next-gen Foundation Models were "custom-built in collaboration with Google and its Gemini models" [announced] (https://www.apple.com/newsroom/2026/09/siri-ai-a-profoundly-more-capable-and-personal-assistant-is-here/). Joint Jan-2026 Apple/Google statement: models "based on Google's Gemini models and cloud technology" while Apple Intelligence "continues to run on Apple devices and PCC" [announced; reported via secondary summary https://ground.news/article/apple-confirms-gemini-powered-siri-will-use-private-cloud-compute]. Gemini weights run inside Apple infrastructure, not Google servers (consensus of secondary sources; one outlier disagrees). Treat "Siri queries never go to Google" as [announced-by-Apple], unverifiable.
- iPhone-side "System orchestrator + App Toolbox (on-device)" + on-device Spotlight index for personal-context search (Apple Siri AI newsroom). Apple's page says "systemwide app actions"; the string "App Intents" is not in that newsroom text, but App Intents is how developers feed it (Apple developer docs per blakecrosley). Limited set of app-schema domains (commenters).
- Siri conversation history syncs to the Siri app on iPhone/iPad/Mac/Watch/Vision Pro via iCloud; voice expressiveness/pace chosen on a paired device carries over (needs newer iPhone / M4 iPad >=12 GB). (Apple newsroom)

### Multi-user identity  [announced]
- Up to **6 people**, recognised by **FACE** (per Apple/9to5Mac/Macworld/Substack). Each gets own Home Screen, apps, widgets; swipe up after recognition; if a different person appears it **asks whether to switch profiles** (Macworld). Apple's own press page does NOT describe voice-ID for HomeView; apple.com/homeview says "face and voice recognition" are processed in Secure Exclaves and that Siri "recognizes up to six people", so voice-ID exists in some form but is undocumented in detail. Treat voice-ID as unclear.
- **Three-factor gate for private content**: recognised face AND the member's paired iPhone/iPad on the same Wi-Fi AND recently unlocked (overnight setting avoids morning unlock; fn.10 describes a fallback when the phone loses Wi-Fi: face + recent same-network use + cellular + same location + recent unlock). Each member needs an Apple Account and must be in the shared Home. (Engadget; MacRumors guide; Apple newsroom fn.10)
- **Guests / unrecognised**: control accessories, timers, music, weather; no personal data; owner chooses guest scope; Android users get a degraded experience. (MacRumors guide; Macworld)
- Walk away -> lock screen. Kids: **nothing announced** (no kid profiles/parental controls mentioned anywhere; Siri app is 13+).

### Communication  [announced]
- **FaceTime** video on the 12 MP Center Stage camera; both parties need FaceTime devices, Wi-Fi. Handoff of calls to/from iPhone. (Apple newsroom; MacRumors guide)
- **Messages**: read and reply by voice or on-screen keyboard; Siri can answer questions about Messages/Calendar content (personal context rules above). (Apple newsroom)
- **Intercom**: broadcast to one room or whole house; reaches HomeView, HomePod, iPhone, iPad, Apple Watch; shows as a Live Activity. **Audio only - no video, no "Drop In"** (Macworld). (Apple newsroom; Macworld link)
- Find My (people sharing location, find iPhone/AirPods case/AirTag), Live Activities (now playing, timers, incoming Intercom).
- No "Live Translation", "Personal Voice", Announce, or accessibility section appears in Apple's HomeView pages ("not mentioned"). Don't assume them.

### Home / dashboard  [announced]
- Distance-aware UI: simpler glanceable content from across the room, more detail as you approach.
- "Faces": Photos (depth effect, shared-album frame), Calendar, Weather, Flow (reacts to presence/movement), Snoopy, **Home Modular** (thermostat/lights/scenes), **Home Camera** (up to **9 live feeds**). Home Screen has 4 widget areas.
- Control by touch/voice: cameras, thermostat, lights, locks, blinds. Hub role for HomeKit/Matter/Thread.
- **Not a security camera**: can't act as a Nest-Cam-style camera nor Drop In; the lens is for FaceTime/identity only. Cameras shown must be Apple Home/Matter-compatible (no Ring). (Macworld)
- Camera intelligence (with iCloud+ and compatible cameras): E2E-encrypted person / familiar-face / pet / vehicle / package recognition, arrival notifications, **video descriptions + natural-language clip search** (needs Apple-Intelligence iPhone/iPad + eligible iCloud+: 50 GB = 1 camera, 200 GB = 5, 2 TB = unlimited; 10-day history). Footage processed locally on a hub or in PCC. (Apple Home Collection newsroom)
- **Sound Recognition**: detects smoke/CO alarm sounds and alerts iPhone/iPad/Watch.
- Kitchen: web recipe -> step-by-step, hands-free timers, ingredient tracking; new Safari recipe mode; Handoff of recipes from iPhone.
- Music: Apple Music with animated art/lyrics; Handoff by holding iPhone to the display; **multiroom/AirPlay 2** with other HomeView, HomePod, HomePod mini, Apple TV, AirPlay speakers.

### Privacy architecture  [announced]
- A18 **Secure Exclaves** process camera and mic data so that the OS, apps, users and Apple cannot access it ("no visual or audio data exposed to the OS"). Face/voice recognition on-device. Personal content gated as above. PCC for server requests. **No hardware mic/camera kill switch** (Macworld). Siri server features usage-capped (a recurring-fee hint).

### Reception / caveats  (pre-release)
- Siri AI "feels slow" per some testers; launch-video Siri responses were sped up; works best only inside Apple ecosystem; thick bars for video on square screen; mount cost; no EU. https://appleintelligence.substack.com/p/homeview-the-at-home-test-for-siri ; https://www.tomsguide.com/home/smart-home/apple-home-view-buy-or-skip (page text not retrievable, headline only). Gurman: "strong end-to-end smart home strategy", $299 reasonable (via the Substack summary).

### Rest of the Oct 13 lineup (brief)
- HomePod mini 2: S10, "Sound Glow" top, Siri AI, **$149**, ships Oct 30. Apple TV 4K: A19, Siri AI support, ships Nov 13 (price increase reported, amounts not retrieved). [announced] MacRumors links above; CNBC.
- Apple Home Collection (LG/Schneider): Video Doorbell $249.95, Floodlight Cam $399.95, Outdoor Cam $299.95, Indoor Cam $199.95 (physically tilts to "stop monitoring" when first household member returns), Smart Deadbolt $349.95 (UWB unlock, **Matter + Aliro**), thermostats $199.95, sensors $39.95; Thread; NFC one-tap setup; ships Dec 2026 / early 2027. [announced] Home Collection newsroom link.

## 3. Apple comms primitives (what Zoe's household will compare against)
Sources here are mostly 2020 launch coverage [background/secondary]; HomeView press release adds the 2026 delta.
- **Intercom semantics**: "Hey Siri, intercom ..." / "tell the kitchen ..." sends a recorded voice message to one HomePod, a named room/zone, or **all**; it plays aloud on the target speaker. On iPhone/iPad/Watch/CarPlay/AirPods it arrives as a notification you can play; reply with "Hey Siri, reply ..." (works from AirPods, CarPlay, from outside the house). Per-user/per-device controls over who receives Intercom and when, set in Home app. Asynchronous recorded message, not an open two-way line (unlike Amazon Drop In). Sources: https://www.iphoneincanada.ca/apple/apples-new-intercom-feature-support ; https://www.macworld.com/article/675437/how-to-use-intercom-on-homepod-airpods-iphone-and-other-products.html ; https://appleinsider.com/articles/20/10/13/apple-unveils-intercom-feature-for-iphone-ipad-homepod-and-airpods . (Apple's own support page could not be fetched.) 2026 delta: HomeView adds a screen, Live Activity for incoming Intercom, audio only. [announced]
- **FaceTime on HomeView**: native video, Center Stage. On HomePod/Apple TV: HomePod handles FaceTime *audio* via phone handoff; Apple TV uses an iPhone/iPad as camera (Continuity Camera). [background, not verified this session]
- **Messages by Siri**: read/reply/dictate via voice; "Announce Notifications" via AirPods; personal-request gating by voice profile on HomePod. [background]; HomeView adds face+phone gating (above).
- **Find My / presence**: family location sharing, item finding; Home app presence automations use iPhone location ("Home hub" = HomePod/Apple TV/HomeView acting as always-on controller for remote access, automations, Thread border router). The Home app has no room-level presence sensor of its own (house has none per Zoe memory). [background + Apple newsroom]
- **Matter/Thread**: HomeView is Thread (border-router-class) + Matter-compatible hub; Apple's own Home Collection pushes Matter + Aliro (UWB lock). [announced]
- **Multi-room audio**: AirPlay 2 groups incl. HomeView. [announced]

## 4. Competitive table (Zoe column: "unknown" unless VISION/CANONICAL says)
Sources: Apple (above); Amazon https://www.aboutamazon.com/news/devices/getting-started-echo-show-8-11-alexa-plus-features , https://www.thurrott.com/amazon/327568/amazon-reveals-new-echo-lineup-designed-for-alexa , https://www.aboutamazon.com/news/devices/echo-show-alexa-plus-shopping ; Google https://support.google.com/googlehome/answer/15962877?hl=en , https://9to5google.com/2026/10/13/hey-google-where-is-the-next-nest-hub/ , https://9to5google.com/2026/07/08/gemini-nest-hub-answer-weather-redesign/ ; Meta https://en.wikipedia.org/wiki/Meta_Portal

| Feature | Apple HomeView (ships 11/13/26) [announced] | Amazon Echo Show 8/11 (+15/21) w/ Alexa+ [shipping] | Google Nest Hub (2nd gen, 2021) + Gemini for Home [shipping] | Zoe today |
|---|---|---|---|---|
| Price | $299 (+mount $60-130) | Show 8 $179.99 / Show 11 $219.99 (2025 launch prices); Alexa+ free w/ Prime else $19.99/mo | ~$100; Gemini Live needs Home Premium ($10/mo US) | unknown |
| Brain | Siri AI: on-device + PCC (Gemini-derived models), daily caps, English, beta | Alexa+ (cloud LLM) | Gemini (cloud) | Gemma 4 E4B local (CANONICAL) |
| Cloud-free / private | Partly; PCC for hard asks; mic/cam in Secure Exclave | Cloud; Visual ID signals cloud | Cloud | Local-only by principle (VISION) |
| Display / cam | 7" square, 12MP Center Stage | 8"/11", 13MP, 3x zoom, auto-frame | 7" no camera (Hub Max had one) | Pi 5 touch panel w/ camera + mic (brief) |
| Presence sensing | camera-based proximity UI; no radar/UWB | Omnisense presence+temp sensors, Visual ID | none/limited | unknown |
| Multi-user | face (6) + phone-gated personal data; guest mode; voice-ID unclear | Visual ID + voice ID | Voice Match, Familiar Faces (camera) | unknown (brief: voice+face ID at panel) |
| Video calls | FaceTime | Alexa calls / Drop In (video) | Meet/Duo-style on Hub Max (older) | unknown |
| Intercom/broadcast | audio, room/whole-home -> phones/Watch; reply from phone | Announcements + Drop In (video-capable) | Broadcast (improving reliability 9/16/26) | unknown |
| Messages | read/reply by voice | via phone/Alexa app | limited | Telegram (brief) |
| Home control | HomeKit/Matter/Thread, 9-cam view | Alexa/Matter/Zigbee/Ring | Google Home/Matter | Home Assistant underneath (brief) |
| Security-cam role | cannot be one | Ring summaries/live view; Show can be cam | Hub Max could | unknown |
| Camera AI | person/face/pet/package/vehicle, video descriptions, NL clip search (iCloud+ tiered) | Ring Smart Video Search (Ring Premium) | Home Brief, event descriptions, Familiar Faces, Pet Memory (Home Premium Advanced) | unknown |
| Kids | none announced | Alexa kids (existing) | supervised accounts get Gemini | unknown |
| Multi-room music | AirPlay 2 | Echo groups | Cast groups | Music Assistant (CANONICAL) |
| Proactive | widgets/Live Activities; no proactive speech | proactive Routines | Home Brief | proactive-but-restrained (VISION) |
| Subscription/limits | daily server caps, paid tier planned | Prime/$19.99 | Premium $10-$20? (Advanced tier price not retrieved) | none |
Meta Portal: discontinued 2022, no 2026 successor [shipping/discontinued]. Google: no new display; "Google Home Display" only in code [rumor].

## 5. What Apple has that Zoe does NOT (ranked by value to a household that already has a voice+face panel)
(Assumes Zoe lacks each unless parent knows otherwise; Jetson Orin 16 GB shared with 4B LLM + Kokoro 2.3 GB; Pi 5 panel.)
1. **Phone-presence as a second factor for private results** (face + member's phone on LAN + recently unlocked). Buildable, cheap: HA companion-app presence/Wi-Fi association/BLE; Telegram "approve" ping. Pure policy on top of existing face ID. Piece: Home Assistant Companion app presence + ESPresense/room-assistant.
2. **Intercom, whole-home/per-room, delivered to phones/Watch/AirPods with spoken reply.** Buildable: voice -> Moonshine -> route -> Kokoro on target speaker; phone leg via Telegram voice note + reply-by-voice; rooms need more satellites. Pieces: Snapcast or Music Assistant players as targets; HA Assist satellites / ESPHome Voice PE (~$59) for rooms; LiveKit already in stack for live legs.
3. **Video calls (FaceTime-like) panel<->phone/panel<->panel.** Buildable; LiveKit is already a repo service; Pi 5 camera/mic already present. Only Zoe-household peers; no Apple-style reach to arbitrary contacts. Piece: LiveKit + a PWA on phones (QR/Telegram handoff matches VISION principle 8).
4. **Distance-aware adaptive UI + presence-reactive faces.** Buildable: camera face-box size as range proxy (FACE_MIN_PX already exists) or a ~$5 mmWave radar (LD2410/LD2450 on ESPHome -> HA). Cheapest big UX win.
5. **Recognise-and-switch personal home screen, per-member widgets, auto-lock when you walk away, guest scope.** Buildable on existing face/voice ID; mostly UI + session policy. Piece: existing InsightFace-class pipeline; no new model.
6. **Camera intelligence: person/face/pet/package/vehicle, video descriptions, NL clip search.** Partly buildable: Frigate NVR (object+face+genAI descriptions) + Gemma vision for captions; GPU/RAM contention with the 4B brain is the hard limit. Run detection on a Coral/cheap box; captions batched at night (matches 12B-night window).
7. **Sound Recognition (smoke/CO alarm, glass, dog bark) -> alerts to phone.** Fully buildable: YAMNet/PANNs on the panel mic or Jetson CPU at near-zero cost; privacy-safe if only labels leave. Strong safety feature for a family.
8. **Handoff (hold phone to display to move song/call/recipe).** NFC/UWB not on Pi; substitute QR/Telegram "send to panel"/"send to my phone" and Music Assistant queue transfer. Partially buildable, feel is worse.
9. **Cooking mode: recipe->steps, hands-free timers, ingredient tracking.** Fully buildable local: recipe-scrapers/Mealie + Gemma structured extraction + existing timers. Low risk.
10. **Onscreen-aware conversational assistant (knows what is on the display).** Buildable: feed panel state/DOM summary into the brain context; no new hardware.
11. **Find My-class people/item location.** Partial: family location via HA companion/OwnTracks/Traccar; item finding via BLE tags + ESPresense; cannot join Apple's crowd network.
12. **Cross-device conversation continuity (Siri app history on every device).** Buildable: memory is already household-central; expose thread continuity in Telegram + panel.
13. **Matter/Thread hub incl. Aliro/UWB lock support.** Mostly via HA: Matter server + OpenThread border router (Thread dongle). Aliro/UWB unlock is vendor-gated.
14. **Hardware-grade mic/camera isolation (Secure Exclave) as a trust claim.** Not replicable in silicon; a *different, arguably stronger* claim is buildable: physical mic/camera cutoff (GPIO relay/shutter) + local-only processing. Apple has NO hardware switch.
15. **Integrated first-party accessories with one-tap NFC onboarding.** Not buildable; use Matter QR pairing + the QR-to-phone flow.

### Where Zoe is ahead (for contrast)
Fully local brain with no usage caps/subscriptions; no EU/region lockout; works for Android/Telegram users and guests without an account; household memory and proactive-but-restrained behaviour; hardware kill switch possible; open Matter/HA/Music Assistant stack instead of Apple-only; Apple's headline Siri AI personal context is gated on an iPhone being nearby and unlocked, whereas Zoe's memory lives in the box.

## 6. Gaps / unverified
- Apple Intercom support page and Tom's Guide / AppleInsider hands-on bodies could not be fetched (403/headline-only); an AppleInsider forum snippet claiming "no multi-user support" contradicts Apple; treated as noise.
- HomeView voice-ID: Apple's pages say "face and voice recognition" processed in Secure Exclaves but only describe face-based user switching; do not assume voice-ID works at launch.
- Echo Show 8/11 prices are 2025 launch prices (from Thurrott); 2026 street prices differ. Google Home Premium Advanced price not retrieved.
- FaceTime-on-HomePod/Apple TV and Announce Notifications claims are [background].
