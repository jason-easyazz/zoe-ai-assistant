# services/omi-sync — Omi pendant ring sync (Pi side, flag-dark)

## Purpose

"Record on the pendant, sync when in range of Zoe." The owner wears an Omi CV1 pendant that records to its own SD storage while powered. When he comes within Bluetooth range of the panel Pi, this small service connects (to the Pi, NOT the Omi phone app; no pairing/bond is involved), drains the storage, decodes Opus to 16 kHz WAV, uploads each recording to zoe-data, and — on ring firmware — frees the pendant's ring only after zoe-data confirms it stored that recording. The owner's pendant runs firmware 3.0.18, which predates the ring protocol: see the legacy-file contract below. Nothing goes to Omi's cloud. Design + status: [`docs/architecture/omi-integration-plan.md`](../../docs/architecture/omi-integration-plan.md) (section 4 and the operator runbook).

It runs as its OWN process and unit (`scripts/setup/systemd/omi-sync.service`, operator opt-in, never auto-enabled) — **not** inside `zoe_voice_daemon.py` — so a BlueZ fault cannot touch the voice path.

## Ownership

- `omi_sync/ring_protocol.py` — the ring wire format as pure functions (UUIDs, commands, notifications, 444-byte records, the 3.0.20 firmware gate). No `CMD_RING_CLEAR` encoder exists, deliberately.
- `omi_sync/legacy_protocol.py` — the firmware-**3.0.18** multi-file SD protocol (`LIST_FILES` / `READ_FILE`), as pure functions. Verified from `storage.c` at the commit that shipped 3.0.18 only; 3.0.19 is NOT covered. No encoder for its `DELETE_FILE` (`0x12` — the same byte as the ring's ADVANCE).
- `omi_sync/spool.py` — the durable local custody spool (SQLite, `synchronous=FULL`): every received record, until Zoe confirms its recording.
- `omi_sync/recordings.py` — deterministic segmentation of spooled records into recordings, time resolution (device clock vs host), Opus → WAV.
- `omi_sync/engine.py` — one drain session and THE CUSTODY RULE (`Drain`, `advance_target`, `ring_label`).
- `omi_sync/uploader.py` — HTTP upload + the mapping of Zoe's answer to a custody outcome (`classify`).
- `omi_sync/ble_link.py` — the only module that needs a pendant (bleak 0.22.3). Everything else is tested on fixture bytes. Connection recipe (verified on the real pendant 2026-10-12): `sudo -n hcitool lecc --random <addr>` first (32 s supervision timeout; BlueZ's 420 ms default dies at this range), then bleak on the connected device; no scan, no pairing, no agent; retry with backoff.
- `omi_sync/check.py`, `omi_sync/cli.py`, `omi_sync/config.py` — the read-only device check, the entry points (`run | check | status`), env config.
- `THIRD_PARTY_NOTICES.md` — the MIT notice for the Omi source the protocol is ported from. Keep it with any substantial new port.
- The Zoe side lives in `services/zoe-data/omi_recordings.py` + `routers/omi_recordings.py` (migration 0045).

## Local Contracts

- **Custody: audio is freed from the pendant only after Zoe CONFIRMED it stored that exact recording** (HTTP 200, `ok`, and the echoed SHA-256 of the bytes sent — `uploader.classify`). Delivery over Bluetooth is not custody. The ring is advanced only to `engine.advance_target(...)`: never past the first unconfirmed record, never past what we hold or confirmed contiguously from the pendant's own read pointer. A dropped link, refused READ, inconsistent DONE, unavailable Zoe or rejected recording advances nothing.
- Every received record is committed to the spool BEFORE anything else happens to it; a record leaves the spool only through `Spool.mark_confirmed`. Idempotent by `(device, ring, seq)`; a confirmed range is a tombstone.
- Which protocol runs is decided by DIS `2A26` BEFORE any storage command: **≥ 3.0.20 → ring**; **exactly 3.0.18 → the legacy file protocol, only with `OMI_SYNC_LEGACY_FILES=1`** (its firmware deletes each file itself on delivery — a free we can neither gate nor defer, so the spool is the ONLY custody; default off); anything else is refused with GATT reads only (the same opcode byte is ADVANCE on one protocol and DELETE_FILE on the other). An unknown codec id (the owner's pendant reports `0x0f`; the source says 21) is refused unless `OMI_SYNC_ASSUME_CODEC` names one; a recording whose frames do not decode is HELD (raw records stay in the spool), never uploaded. Durations come from the decoded PCM, not the codec id.
- The pendant is never written to except: the storage commands above (list/read/advance/stop) and, only with `OMI_SYNC_SET_DEVICE_TIME=1`, one clock write at the start of a session. No pairing, no agent, no OTA/SMP traffic, no settings or haptic writes.
- The last recording of a session stays OPEN (not uploaded, not freed) until it has been quiet for `OMI_SYNC_GAP_S`, hit `OMI_SYNC_MAX_RECORDING_S`, or the pendant is fully drained; a tail we have not caught up with (dropped READ) is held however old it is.
- The pendant address never leaves the Pi: uploads carry `device_hash(address)`.
- Flag-dark: `OMI_SYNC_ENABLED` unset/0 → `python -m omi_sync run` exits 0 without importing bleak or creating state. Opt-ins that default OFF: `OMI_SYNC_LEGACY_FILES`, `OMI_SYNC_SET_DEVICE_TIME`, `OMI_SYNC_ASSUME_CODEC`. Zoe-side, `ZOE_OMI_SYNC_ENABLED` unset → the routes do not exist (404).
- Stock-firmware caveat (read from `storage.c`): firmware without the custody capability frees records as the link layer acknowledges them; the spool is what makes that safe. Custody firmware (`CAP_APP_ACK_RECLAIM`, `ring_id`, `ADVANCE_ID 0x15`) is supported when the pendant advertises it.

## Forbidden

- Never send `CMD_RING_CLEAR` (`0x13`) or the legacy `DELETE_FILE` (`0x12`), and never add an encoder for either. Never ADVANCE on anything but confirmed state.
- Never pair, bond, unpair or register a BlueZ agent from code (a test pins it): the real pendant connects and reads fine with no bond, and it holds ONE bond which the phone app would otherwise keep. Never touch the MCUmgr SMP service (`8d53dc1d-…`): a firmware update is an owner-run operation (plan 4.10), never this service.
- Never upload to any host except the configured `ZOE_URL`; never log audio, transcripts, the device token or the pendant address.
- Never import from `labs/` or `services/zoe-data`; never run inside the voice daemon, the Orin or a Docker image.
- Never delete a spool record or a staged WAV except via a CONFIRMED upload (or the confirmed-range prune).
- Never write the pendant's clock unless `OMI_SYNC_SET_DEVICE_TIME=1` (default off), and then only once, at the start of a session, after the offset was measured.

## Work Guidance

- Keep all decisions in `engine` / `ring_protocol` / `recordings`; `ble_link` stays thin. A new behaviour gets a fixture-byte or fake-pendant test with a break-the-fix control.
- Third-party API: check `opensrc path pypi:bleak@0.22.3` before changing `ble_link.py`; port Omi protocol facts from `~/.opensrc/repos/github.com/BasedHardware/omi` and cite the file in the code.
- Hardware-only facts are marked UNVERIFIED in the plan until the operator has run `python -m omi_sync check` on the pendant (see the plan's section 4.7).

## Verification

- `pytest tests/unit/test_omi_sync_protocol.py tests/unit/test_omi_sync_engine.py tests/unit/test_omi_sync_check.py -q` (all `ci_safe`; no hardware, no network, no `bleak`/`opuslib` needed).
- Zoe side: `pytest services/zoe-data/tests/test_omi_recordings.py -q`.
- On the Pi, read-only: `python -m omi_sync check` (GATT reads + `0x10 INFO`; never READ/ADVANCE/CLEAR/time-write).

## Child DOX Index

No child AGENTS.md files.
