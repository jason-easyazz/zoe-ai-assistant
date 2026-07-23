---
type: reference
status: draft-findings
audience: operator
scope: Feasibility of adding the touch panel (zoe-touch / 192.168.1.61) as a
  selectable Music Assistant player. READ-ONLY investigation, no changes made.
date: 2026-07-23
---

# Panel-as-a-Music-Assistant-speaker — feasibility findings

**Question:** can the Raspberry Pi 5 touch panel appear in the estate speaker
picker as a real, selectable player and play music out of its own speaker,
without disturbing the canonical voice path?

**Answer:** Yes, cheaply. The two hard problems people expect — "will it fight
the voice daemon for the sound device?" and "will the estate have to be
re-wired?" — both turn out to be already solved by the existing setup. What
remains is choosing a small receiver to run on the Pi. Nothing below was
changed; several claims are marked *unverified* where confirming them requires
an operator-authorized change.

---

## 1. Ground truth (all verified read-only)

### The Pi's audio hardware (`zoe-touch`, 192.168.1.61)
- `aplay -l` / `/proc/asound/cards`:
  - card 0/1: `vc4-hdmi-0/1` (the two HDMI outputs)
  - **card 2: `J750` — Jabra Speak 750** USB speakerphone (mic + speaker)
- Sound server: **PulseAudio 16.1** (not PipeWire).
- PulseAudio sink present: `alsa_output.usb-…Jabra_Speak_750….analog-stereo`,
  state `SUSPENDED` (idle, nothing playing at probe time).
- **Default sink = the Jabra.** No sink-inputs active at probe time.
- The Jabra confirmed as the "USB-power-shared-with-PanaCast" speaker from the
  panel-identity notes — it is real, attached, and the panel's primary audio out.

### What owns the audio now — the voice daemon
- `zoe-voice.service` (systemd **`--user`** as `pi`) is **active/running**;
  also `zoe-panel-agent.service`.
- Code: `/home/pi/.zoe-voice/zoe_voice_daemon.py`.
- **Mic capture:** a single continuous PyAudio **input** stream (wake word
  always listening).
- **TTS playback:** short-lived `aplay`/`mpg123` **subprocesses**, spawned only
  while speaking, killed on barge-in.
- **Decisive routing fact:** `.env.voice` sets `AUDIO_DEVICE=pulse` and
  `AUDIO_OUTPUT_DEVICE=pulse`. Both capture and playback go **through
  PulseAudio**, *not* raw ALSA. (There is a `~/.asoundrc` that points the raw
  ALSA `default` at `hw:card2`, but the daemon deliberately uses `pulse`, so it
  bypasses that raw-hw default.)

**Why that matters:** PulseAudio is the arbitration layer and it **mixes**
multiple playback streams into one sink. A music receiver that also outputs to
the `pulse` Jabra sink will **mix with TTS, not lock it out**. There is no
exclusive-hardware contention to engineer around. The mic input stream is the
PulseAudio *source*, independent of playback, so a music player never touches
it. During a spoken announcement, music and TTS would simply play at once
(auto-ducking is a nice-to-have, not a feasibility blocker).

### Where Music Assistant runs
- MA is a **Docker container** `zoe-music-assistant`,
  image `ghcr.io/music-assistant/server:stable` (operator states v2.8.7),
  **`network_mode: host`**, on **this box, the Orin — 192.168.1.218**
  (`hostname -I` → `192.168.1.218`; compose: `docker-compose.modules.yml`).
- **MA is NOT on the Pi.** So "MA's local/builtin player" would emit audio on
  the *Orin*, which is useless for the panel. To make the *Pi* a speaker, the
  Pi must run something MA reaches **over the network**.
- Because the container is `network_mode: host`, MA sits directly on the LAN and
  can see mDNS/zeroconf and UPnP broadcasts from the Pi.

### The estate picker is already dynamic
- `GET /api/music/players` (`services/zoe-data/routers/music.py`) is fed by
  `music_service.get_players()` → MA `players/all`. It is **not a hardcoded list
  of 14** — it renders whatever MA reports.
- Each player is passed through the pure helper `resolve_player_kind(player)`
  which derives a flat `kind` (`speaker`/`tv`/`display`/`group`/`computer`) from
  `type` + `provider` + `device_info.model`.
- **Consequence:** any device MA registers as a player will **just appear** in
  the picker — no estate/UI code change required to make it *show up*. The only
  estate consideration is the *label* it gets (see the kind-mapping gotcha per
  option below).

### Live MA player inventory (verified via API, read-only)
14 players, all network, **zero local to the Pi** — matches the operator:
`universal_player` ×5 (MacBook, Samsung/LG/TCL TVs), `chromecast` ×5 (incl. a
group + Nest Hub + Home Mini), `sonos` ×2 (Arc, Beam), `airplay` ×2 (Apple TVs).
Notably **the `airplay` provider is already enabled and auto-discovering** — two
Apple TVs are live players right now. `snapcast`, `dlna`, `squeezelite` show no
players → those providers are **not currently enabled**.

### MA bundles a Snapcast server
- `docker exec zoe-music-assistant which snapserver` → `/usr/bin/snapserver`,
  **v0.34.0**. MA's snapcast provider treats `>=27 && !=30` as
  "local snapserver present" → it will run its **own bundled snapserver**;
  `CONF_USE_EXTERNAL_SERVER` defaults to **false** in that case. No external
  snapserver needs to be stood up. (Snapclients on the Pi would connect to the
  bundled server's ports on the Orin — standard 1704/1705.)
- The image does **not** bundle shairport-sync/librespot — irrelevant, because
  the AirPlay *provider* streams **out** via libraop; the *receiver* lives on
  the Pi.

---

## 2. The viable options

For each: what runs where, setup cost, reboot-persistence, voice-daemon
contention, and how it surfaces in `/api/music/players`.

### A. AirPlay receiver on the Pi (shairport-sync) — LOWEST live-change cost
- **Runs where:** `shairport-sync` on the Pi, output → `pulse` (Jabra sink).
- **MA side:** **nothing.** The airplay provider is *already enabled and
  auto-discovering via mDNS* (`_airplay._tcp` / `_raop._tcp`, per manifest +
  proven by the 2 live Apple TVs). A shairport-sync instance anywhere on the LAN
  auto-appears.
- **Setup:** `apt install shairport-sync` on the Pi + a small unit to run it
  against the `pulse` sink. That's it.
- **Reboot-survival:** yes, via a systemd unit (user or system).
- **Contention:** none at the device level — mixes through PulseAudio.
- **Surfacing gotcha:** `resolve_player_kind` maps `provider in
  (chromecast, airplay, universal_player)` that isn't a known
  speaker/Mac/AppleTV to **`kind="tv"`** (deliberate — "every AirPlay device
  here is an Apple TV or a Mac"). So the Pi would appear **selectable and
  playable but mislabeled as a TV**. Fixing the label = a tiny estate patch
  (teach the resolver the Pi's shairport model → speaker). Functionally works
  today; cosmetically wrong until patched.
- **Caveats:** classic AirPlay is single-stream and adds ~2s buffering latency
  (fine for music, not for "instant"). *Unverified:* whether a freshly
  discovered AirPlay device is auto-enabled in MA or needs a one-time enable
  toggle in the MA UI.

### B. Snapcast client on the Pi — BEST "real speaker" semantics
- **Runs where:** `snapclient` on the Pi, output → `pulse` (Jabra sink). The
  **snapserver runs inside the MA container** (bundled v0.34.0).
- **MA side:** **enable the Snapcast provider** in MA (a config change — see
  §4). That starts the bundled snapserver (opens ~1704/1705 on the Orin).
- **Setup:** `apt install snapclient` on the Pi + unit pointing at
  `192.168.1.218` + enable provider in MA.
- **Reboot-survival:** yes (systemd unit + provider stays enabled).
- **Contention:** none — mixes through PulseAudio.
- **Surfacing:** `provider=snapcast` is **not** in the "→ tv" set, so
  `resolve_player_kind` returns **`kind="speaker"`** — **correctly labeled out
  of the box**, no estate patch.
- **Bonus:** purpose-built for "turn this box into a synced multiroom speaker";
  low, tunable latency; would sync with any future snapclients.

### C. Squeezelite client on the Pi
- **Runs where:** `squeezelite` on the Pi → `pulse`.
- **MA side:** enable the Squeezelite provider (config change).
- **Surfacing:** `provider=squeezelite` → `kind="speaker"` (correct label).
- Roughly the same cost/shape as Snapcast; Snapcast is the more standard
  "make-this-a-speaker" answer and needs no external server, so B is preferred
  over C.

### D. DLNA/UPnP renderer on the Pi (gmrender-resurrect)
- **Runs where:** a UPnP MediaRenderer on the Pi → `pulse`.
- **MA side:** enable the DLNA provider (config change); it auto-discovers via
  UPnP (`MediaRenderer:1`).
- **Surfacing:** `provider=dlna` → `kind="speaker"` (correct label).
- Heavier/older stack than snapcast for no benefit here. Lowest preference.

### Not viable / rejected
- **MA `builtin` player:** `builtin` is a *music* provider (generic URL/playlist
  handling), **not** a player that outputs to an ALSA/Pulse sink. It cannot make
  the Pi a speaker.
- **MA local player on the MA host:** even if it existed, it would emit on the
  Orin (.218), not the Pi.
- **Kiosk browser as speaker:** consistent with the prior "browser is not a
  speaker" finding — the panel runs plain Chromium kiosk, not Fully Kiosk /
  Dashie (MA has providers for those Android kiosks, which do not apply here).
  Not pursued.

---

## 3. Recommendation + lab-provable first step

**Ranked for the production build:**
1. **Snapcast (Option B)** — correct `speaker` label with zero estate code,
   bundled server, purpose-built, low latency, coexists with voice via
   PulseAudio. Costs one operator-authorized MA provider-enable + one Pi package.
2. **AirPlay (Option A)** — fewest moving parts and *zero* MA config, but lands
   as a "TV" until a small resolver patch, and is single-stream/higher-latency.
3. Squeezelite (C) / DLNA (D) — work, correctly labeled, but no advantage over
   Snapcast.

**Lab-provable first step (zero change to the live panel or live MA config):**

Run **shairport-sync in a throwaway Docker container on the Orin (or any spare
Linux box that is *not* the panel)**, outputting to a null/dummy sink. Because
the AirPlay provider is *already live and auto-discovering*, this receiver will
**auto-appear in the live MA** with no MA config change and nothing installed on
the Pi. Then:
- confirm it shows up in `GET /api/music/players`, and observe **what `kind` the
  resolver gives it** (this immediately surfaces the "tv" mislabel finding
  before any commitment);
- optionally send a short stream to it (into the null sink → silent, so it
  respects "no playback in the house");
- confirm whether MA auto-enables the discovered device or needs a UI toggle.

This proves the *entire* MA→discovery→registration→estate-picker loop end to end
while touching neither the canonical voice box nor the live MA configuration —
exactly the lab-before-prod discipline. Only after that passes do you decide
between "shairport-sync on the real Pi + a small speaker-label patch" and
"snapcast on the real Pi + enable the snapcast provider," and prove *that*
choice on the Pi with a **foreground, non-persistent** run (no systemd, no
reboot) to watch it mix with a live TTS announcement before making it permanent.

---

## 4. Operator-authorized changes each path needs (decisions, not actions)

None of these were done; each requires the operator's say-so.

| Path | Pi-side (install/persist) | MA-side (shared) | Estate code |
|---|---|---|---|
| A · AirPlay | `apt install shairport-sync` + systemd unit → `pulse` | **none** (provider already on); maybe one-time enable of the discovered device in MA UI | small `resolve_player_kind` patch so it labels as *speaker* not *tv* |
| B · Snapcast | `apt install snapclient` + systemd unit → `pulse` | **enable Snapcast provider** (starts bundled snapserver, opens ~1704/1705 on the Orin) | none — labels as *speaker* already |
| C · Squeezelite | `apt install squeezelite` + unit → `pulse` | enable Squeezelite provider | none |
| D · DLNA | install gmrender-resurrect + unit → `pulse` | enable DLNA provider | none |

**Cross-cutting authorizations for any path:**
- Installing a package on the Pi (the canonical voice box) and adding a
  **resident playback process** to the same PulseAudio the voice daemon uses.
  Low risk (PulseAudio mixes; TTS is short subprocess bursts), but it is a new
  permanent tenant on the voice hardware — the operator should sign off.
- For B/C/D: a **live MA config change** (enabling a provider). Whether enabling
  a provider is fully hot or briefly disrupts MA is *unverified* — treat as an
  authorized MA touch.

---

## 5. Honest unknowns (would require an authorized change to confirm)
- Whether music and TTS actually mix cleanly through PulseAudio on the *real*
  Pi under load — provable only by running a receiver on the Pi (foreground,
  non-persistent).
- Whether the Jabra's known USB-power quirk (TTS-during-video knocks the
  PanaCast off the bus, per panel-identity notes) has any analog when a
  *continuous* music stream holds the sink open — unobserved; watch for it
  during the on-Pi step.
- Whether a newly discovered AirPlay device auto-enables in MA vs. needs a UI
  toggle — the lab step in §3 answers this.
- Exact snapserver ports/latency once the provider is enabled — standard
  1704/1705 assumed; unverified until enabled.
- No auto-ducking exists today; music + announcements will simply mix. Ducking
  would be separate follow-up work.
