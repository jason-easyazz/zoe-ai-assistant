---
type: spec
title: Linking my Omi pendant - a member links and unlinks their own pendant by voice and the panel, and sees their pendant settings in Settings
description: Acceptance spec + design record for member self-linking of an Omi pendant (verified voice or PIN + physical possession in a short pairing window at the panel), self-unlinking, the recording notice, the per-member "My pendant" settings that can only narrow the operator flags (migration 0077), the omi-sync change that reads the panel's remembered devices, the privacy rules, and the owner steps after landing.
tags: [omi, pendant, linking, bluetooth, panel, voice, settings, privacy, flags, spec]
timestamp: 2026-11-12T00:00:00Z
---

# Linking my Omi pendant

## Acceptance - true when done (the owner's words)

- I can link my Omi to Zoe using her interface and my voice. I never run code or edit a file.
- My Omi settings are listed in her settings.

Standing rules this obeys: users never run code; the wall panel is voice first, touch second, no keyboard; a verified voice is a
password; a child's pendant is never voice-tagged; lists and settings are per member.

## Decisions (made by the lead; the owner may change them)

- A member may link a pendant only to THEMSELF. Linking to someone else, and any child's pendant, stays the existing human-admin assign
  (Settings > Bluetooth, "Assigned to"). A guest gets nothing.
- Proof = WHO + WHAT IS IN THEIR HAND. Who: a verified voice (`speaker_verified is True`) or this member's fresh PIN sign-in at that panel
  (the touch Link button). What: the pendant must be the one THIS panel hears next to it (strong signal) inside a short pairing window the
  request opened. Voice alone never links a device. A pendant someone else owns is never claimed: they unlink first, or the admin moves
  it. This replaces the PIN step-up for this one flow only; the step-up policy (`voice_session_policy.json`) is unchanged.
- No QR for linking (nothing is typed on a phone).
- Unlink by voice or touch, own pendant only. The member is told plainly that anything still on the pendant goes to whoever links it
  next. When the pendant's last health report says it still holds unsynced audio, the first unlink is refused with a plain warning
  that the unsynced audio will go to whoever links the pendant next; a second, explicit "unlink it anyway" (voice) or "Unlink anyway"
  tap (touch) within two minutes of that warning unlinks, audited as `forced_pending`. A confirmation with no warning before it does
  nothing. When pending audio is not known (no report, older firmware) the unlink goes ahead with the warning.
- The notice: the first time a member links, they hear one sentence - the pendant records the people around them as well as them, and
  syncing can be paused in Settings under My pendant. The time is stored per member (`omi_voice_settings.notice_ack_at`). No legal
  wording in the repo beyond that sentence.

## The flow

Voice, at the panel: "Zoe, link my pendant".

1. The fast tier (`omi_link_tier.py`, reached from `fast_tiers._resolve_tiers` after the named-guest tiers) recognises the words
   (`lexicons_data/omi_linking_en.json`). A guest, a child, an unverified voice or a typed channel gets a spoken reason and nothing else.
2. It opens the pairing window on THAT panel (`omi_linking.start`, 90 s) and the reply is the first instruction ("Hold your pendant right
   next to the panel"), with the notice sentence in front of it the first time.
3. The window asks the panel's Bluetooth agent to scan through the existing Settings > Bluetooth broker (pull-based, nothing new on the
   Pi). Only an Omi pendant heard at -65 dBm or stronger counts as next to the panel.
4. Exactly one unowned pendant next to the panel -> `omi_devices.self_assign` links it to the member (audited `self_link`, fail-closed),
   the notice acknowledgement is stored, and the panel connects to it once (audited `connect`) so its known-devices store remembers it;
   then it lets go. Two pendants next to the panel -> "keep only yours next to the panel". A pendant someone else owns -> refused.
   Nothing heard within the window -> "I couldn't find your pendant". A pendant that is already mine but that this panel does not
   remember (an earlier connect failed) is connected again on the retry; only mine AND remembered is "already linked".
5. Progress is shown with the existing enrolment overlay (`voice_enroll` push frame -> `js/touch-voice-enroll.js`: a step line, three
   dots, a tick) and each outcome is spoken on that panel (`voice_announce`, trigger `omi_link`, panel-scoped: only that panel's daemon may claim it).
6. omi-sync on the Pi reads the agent's known-devices store on every step, so the new pendant is synced from its next visit. No env edit,
   no restart.

Touch: Settings > My pendant > Link (panel only; it needs the member's fresh PIN sign-in at that panel and shows the notice before the
first link). Desktop Settings shows the state and the settings, and says to link at the panel.

"Is my pendant linked?" and "Unlink my pendant" are answered the same way, own pendant only.

## Privacy rules

- The pendant's BLE address never leaves the Pi; every message carries the 16-hex device id (`device_hash`).
- A member's screen never carries a device id: their pendants are a label plus an opaque handle (an HMAC of member + device under a
  per-process key), resolved only against the caller's own pendants. Logs carry counts and panel ids, never a device id or address.
- The member routes act only on the caller's own id; the request body cannot name anyone else (`extra=forbid`, a stray field is a 422).
- A device token, a guest and an anonymous caller are refused. A child cannot self-link, and a child's recordings are still never
  voice-tagged or learned (D14: `_diarise` and `may_learn` require a positive adult; no member setting changes that).
- Every link and unlink is in the Omi audit log (`ZOE_OMI_AUDIT_PATH`), and the panel connect is in the Bluetooth audit log; a change
  whose audit line cannot be written is refused.
- Erasing a member removes their settings row, including every 0077 column (`omi_voices.forget_user`), and unassigns their pendants
  (`omi_devices.forget_user`).

## Settings: per member vs operator

The operator flag is the master switch and the ceiling; a member can only narrow it (`omi_member_settings.effective`).

| My pendant setting | stored in (default) | operator flag (master / ceiling) |
|---|---|---|
| Pause recording sync | `sync_paused` (0) | `ZOE_OMI_SYNC_ENABLED` |
| Tag voices on my recordings | `speaker_tag_enabled` (1) | `ZOE_OMI_SPEAKER_TAG` |
| Learn the voices I name | `learning_enabled` (1, migration 0055) | `ZOE_OMI_LEARN_VOICES` |
| Keep audio (days) | `keep_audio_days` (empty = the household's) | `ZOE_OMI_AUDIO_RETENTION_DAYS` (ceiling) |
| Offer me a day review | `day_review_enabled` (1) | `ZOE_OMI_DAY_REVIEW` |

"Tag voices on my recordings" off holds for the night re-pass too: the transcript is still improved, but no speaker is computed or
stored for that member's audio. If the setting cannot be read, nothing is tagged.

Pausing answers the Pi's upload with the same 409 `device_not_registered` an unassigned pendant gets, so the Pi keeps every byte and
asks again later; the pendant's own storage still fills while paused. Operator-only (no member setting): `ZOE_OMI_DEVICE_OWNERS`,
`ZOE_OMI_KEEP_AUDIO`, `ZOE_OMI_KEEP_AUDIO_FOR_TAGGING`, `ZOE_OMI_NIGHT_REPASS`, `ZOE_OMI_HEALTH`, `ZOE_BLUETOOTH_SETTINGS`.

With `ZOE_OMI_DEVICE_OWNERS` off, My pendant says "Pendant linking isn't turned on here yet. Ask the household admin to turn on pendant
linking." and the voice says the same.

## Routes

- `GET /api/omi/my-pendant` - linking on/off (and why), my pendants (handle, label, linked at, last sync), a window in progress, my settings,
  the operator ceiling, the effective values, the notice sentence and whether I have heard it.
- `POST /api/omi/my-pendant/link` `{"panel_id", "notice_ack"}` - open the window (fresh PIN at that panel; 428 until the notice is
  acknowledged once). `GET /api/omi/my-pendant/link` - the window's state.
- `DELETE /api/omi/my-pendant/{handle}` - unlink my own pendant; `?confirm=true` only right after the pending-audio warning.
- `GET` / `PUT /api/omi/voices/settings` - the settings above (every field optional).

All are mounted with the Omi router (absent while `ZOE_OMI_SYNC_ENABLED` is off). The admin routes are unchanged.

## Owner steps after landing

Flag names only; set the values in the live env files yourself.

1. zoe-data (Jetson), from merged `main`: apply migration 0077 and restart zoe-data, then check `/health`.

   ```bash
   cd ~/assistant && git fetch origin && git merge --ff-only origin/main
   bash scripts/deploy/migrate.sh
   systemctl --user restart zoe-data.service      # then poll http://127.0.0.1:8000/health
   ```

2. Turn on, in zoe-data's env: `ZOE_OMI_SYNC_ENABLED`, `ZOE_OMI_DEVICE_OWNERS`, `ZOE_BLUETOOTH_SETTINGS`. Check which are already on:

   ```bash
   grep -o -E '^(ZOE_OMI_SYNC_ENABLED|ZOE_OMI_DEVICE_OWNERS|ZOE_BLUETOOTH_SETTINGS)=' ~/assistant/services/zoe-data/.env
   ```

   Edit `services/zoe-data/.env` for any that are missing, then restart zoe-data as in step 1.

3. Pi (`zoe-pi`): the Bluetooth agent must be running with `PANEL_BT_ENABLED`, and omi-sync with `OMI_SYNC_ENABLED`. Deploy the new
   omi-sync from a worktree of merged `main` (same recipe as [Omi pendant health](omi-pendant-health.md), Operator deploy step 2):

   ```bash
   cd <worktree of merged main>
   ssh zoe-pi 'cp -a ~/.zoe-omi-sync/app/omi_sync ~/.zoe-omi-sync/app/omi_sync.bak-$(date +%Y%m%d-%H%M%S)'
   rsync -a --delete --exclude __pycache__ services/omi-sync/omi_sync/ zoe-pi:~/.zoe-omi-sync/app/omi_sync/
   ssh zoe-pi 'systemctl --user restart omi-sync.service; sleep 5; journalctl --user -u omi-sync -n 20 --no-pager'
   ```

   omi-sync reads the agent's store at `$PANEL_BT_DIR/known.json` (default `~/.zoe-bluetooth/known.json`); `OMI_SYNC_KNOWN_DEVICES`
   names another path, or `off` to read only `OMI_PENDANT_ADDRESSES`. The existing `OMI_PENDANT_ADDRESSES` keeps working and is
   visited first.
4. Check that the voice daemon's and the Bluetooth agent's device tokens name the SAME `panel_id` (the window is opened for the panel the
   voice turn came from and scans through the agent of that panel id). Not verified on hardware.
5. Live check: at the panel, sign in by voice and say "Zoe, link my pendant" with the pendant in your hand; then open Settings > My
   pendant on the panel and on the desktop.

Rollback: alembic downgrade of 0077 drops the five columns; the Pi rollback is the backup move in the health doc.

## Not built

- No consent record for the people a pendant hears (only the wearer's notice acknowledgement).
- The Pi's own spool is not visible to zoe-data, so "audio still waiting on the Pi" cannot be known at unlink time; only the pendant's
  ring report is.
- Pausing does not stop the pendant recording; it holds syncing.

Code: `services/zoe-data/omi_linking.py`, `omi_link_tier.py`, `omi_member_settings.py`, `omi_devices.py` (`self_assign`,
`self_unassign`), `routers/omi_recordings.py`, `routers/omi_voices.py`, migration `0077_omi_member_settings.py`,
`services/omi-sync/omi_sync/config.py` + `runner.py`, `services/zoe-ui/dist/js/omi-pendant-settings.js`. Tests:
`services/zoe-data/tests/test_omi_linking.py`, `test_omi_link_tier.py`, `tests/unit/test_omi_sync_known_devices.py`,
`test_omi_pendant_settings_ui_harness.py` (runs `services/zoe-ui/dist/test_omi_pendant_settings.js`).
