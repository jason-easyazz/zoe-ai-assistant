---
type: design
title: The TV as a display-only Zoe panel - a Google TV launcher over ADB, the ears stay on the Pi
date: 2026-11-02
status: design only; nothing built. Phase P0 needs no code. Later phases ship flag-dark behind a `display` panel role.
evidence: docs/research/completeness-and-issues-2026-11-02/lane-T-tv-display-panel.md (platform rules, open-source references, hardware, build path; [P]/[S]/[U] tagged)
builds_on: docs/VISION.md principle 8 (voice first, touch second), routers/panel_provision.py and panel_auth.py (code pairing and device tokens), panel_principal.py, the per-turn panel targeting of #2117, Home Assistant's androidtv_remote integration
---

# The TV as a display-only Zoe panel

> Owner, 2026-11-01: "is there a smart TV I could reverse hack and install Zoe onto?" The answer: a TV is a good screen and a bad ear, and the only platform worth the effort is Android TV / Google TV, which needs no hack at all. Zoe's estate page becomes the TV's home screen, installed over ADB from the box; wake word, STT, identity and barge-in stay on the Pi panel or a satellite in the same room.

## 1. Rules

1. **Display only.** A TV panel has the `display` role: a device token with read-only scope, no PIN pad, no microphone claims, no speaker verdicts. It never raises the household's trust.
2. **Guest surface.** Everything a TV shows is what a guest in the room may see; private cards render as absence, as the panel does for guests.
3. **Reversible by construction.** Nothing is uninstalled on the TV, only disabled; a restore button and a watchdog that reverts to the stock launcher after repeated crashes (the Free My TV pattern, MIT).
4. **ADB is off day to day.** Pair once, install, then disable wireless debugging; updates come from the box on a schedule or the app self-updates from the LAN.

## 2. What the platforms allow (2026)

- **Android TV / Google TV:** the only real candidate. A sideloaded app declares `LEANBACK_LAUNCHER` and `HOME`; there is no supported "default launcher" picker on Google TV, so the stock launcher is disabled over ADB (`pm disable-user --user 0` on the launcher and the setup package that re-enables it). A system update can revert it; the watchdog and a re-apply script cover that. `RECORD_AUDIO` works only in the foreground and far-field mics on 2025+ sets are undocumented for apps: do not design around them. HDMI-CEC is a system API; the practical route is Home Assistant waking the device through the Android TV Remote protocol. A screensaver `DreamService` can be set with `settings put secure screensaver_components` and needs no launcher change at all.
- **LG webOS and Samsung Tizen:** developer-mode web apps only, time-limited, no mic; skip as a primary path. **Fire TV:** launcher replacement blocked on recent Fire OS. **Roku, tvOS:** no.

## 3. Phases

| Phase | What | Size |
|---|---|---|
| P0 | A Google TV Streamer (about A$150) or a second-hand Chromecast with Google TV; Fully Kiosk Browser (or a DreamService of the estate's ambient face) showing the estate page; Home Assistant `androidtv_remote` for wake, input and launch. No code, no launcher replacement. | S, half a day |
| P1 | The `display` role in zoe-data: a display-scoped device token through the existing code pairing confirmed from the owner's phone, the privacy filter (private cards as absence), a settings card that lists displays. | M, 2 to 3 days |
| P2 | Zoe's own signed WebView launcher (about 300 lines of Kotlin: HOME + LEANBACK activity, WebView on the estate, device-token header, reconnect on network loss, DreamService variant), built on GitHub Actions (the Jetson cannot run Google's x86 build tools), installed over ADB from the box with the Free My TV safety pattern (protected package list, restore, watchdog). | M, 3 to 4 days |
| P3 | "Show this on the TV": a `show_on_display` brain tool and a card action, CEC wake through Home Assistant, the TV as a target in per-panel UI routing. | S to M, 2 days |
| P4 | A native Compose-for-TV app only if the WebView is janky or an app-accessible far-field mic proves out on a specific set. | later |

## 4. Audio

TTS stays on the Pi panel or the room's speaker. Playing Zoe's voice from the TV's speakers is possible from the app but doubles the audio paths in a room and adds the TV's own latency; revisit only if a room has no other speaker.

## 5. Risks

Google launcher resets after updates; Play Protect flagging a sideloaded launcher; ADB left enabled on the LAN; TV telemetry (only `pm disable-user`, never touching Google services, inputs, remote pairing or the Play Store); old WebView versions on cheap sets; licences of any launcher code mined (FLauncher is GPL-3.0, read for ideas only; Projectivy is proprietary).

## 6. Sequencing

After multi-panel targeting (#2117) and panel-as-code (#2142) are verified live, and after the household's own panel is reliable (synthesis section 2). P0 can happen any weekend; it is a purchase and an afternoon.
