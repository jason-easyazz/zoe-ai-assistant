# omi-receiver — B9.1 "Omi lab receive" (P0 of the Omi plan)

**Lab only. Off-Orin.** A hand-run BLE receiver for the Omi CV1 pendant: connect,
decode the Opus stream, write rolling 16 kHz WAVs, reconnect on drop, print the
numbers the P0 gate asks for. Nothing under `services/` imports this; no unit, no
Docker, no CI wiring. Plan: [`docs/architecture/omi-integration-plan.md`](../../docs/architecture/omi-integration-plan.md)
§2.1 (protocol) and §6 (phases/gates); program: `beat-the-bar-2026-program.md` B9.1.

**Status: scaffold + fixture-tested. No pendant has been connected yet** — every
number in the gate table below is *to be measured by Jason*; nothing here is a
live result.

## What is in here

| file | role |
|---|---|
| `omi_bridge.py` | the bridge: `BleakTransport` (bleak 0.22.3, BlueZ) → `OmiFramer` (3-byte header, fragment reassembly, gap/wrap/resync accounting) → `OpusDecoder16k` (opuslib) → `WavSink` (rolling WAVs; a write that crosses the roll boundary is split, never overshoots). `Bridge` owns connect / own `disconnected_callback` / backoff reconnect / DIS+codec+battery reads / summary. `--codec` is only the assumption until the pendant answers: a different **supported** codec rebuilds the decoder before any audio flows, an unsupported one aborts. Gap silence is bounded (`--max-gap-fill-seconds`, default 5: per gap, and the total may never lead the wall clock by more — the packet id is untrusted input, an unbounded fill could write ~21 MB per notification). The BLE surface is the `Transport` protocol, so everything below it runs without hardware. |
| `wer.py` | word error rate (stdlib) for the Moonshine comparison; CLI over the reference sentences and the transcripts — a line-aligned text file **or `replay_samples.py --json` output directly**. A transcript count that differs from the sentence count, or a replay row with `stt_error` (transcription failed, not misheard), is exit 2 — never padded, truncated or scored as 100 %. |
| `split_on_silence.py` | cuts one long capture into per-utterance WAVs (stdlib) so the replay path, which treats each WAV as one utterance, can transcribe the 20 sentences. Re-running into the same `--out` replaces that capture's previous segments (no stale higher-numbered files survive a re-tune). |
| `tests/test_omi_framer.py` | synthetic packet streams: contiguous, gap (+ a **negative control** that bypasses the detector and proves the gap assertions go red; frame-before-silence ordering; the fill bound + its uncapped negative control), counter wrap (65535→0, and a gap across it), truncated/header-only packets, fragmentation (reassembly, missing fragment, orphan, a gap that lands on the next frame's fragment 0 keeps the complete pending frame, fragmentation memory reset per connection), counter restart mid-session and on reconnect, WAV rolling (incl. one write split across files), the bridge over a scripted fake transport (reconnect, no-reconnect, failed connect + backoff, a drop *during setup* reconnects, 1.0 % gap = gate FAIL, unsupported codec, reported-codec decoder rebuild, **real SIGINT → summary.json still written**), a **real opuslib encode→decode** round trip (skips with a stated reason when opuslib/libopus is absent — never a fake pass), and the two helpers (count mismatch, `--json` rows, segment replacement). |
| `requirements.txt` | `bleak==0.22.3`, `opuslib==3.0.1` (+ `apt install libopus0`). Why opuslib and not pyogg is in the file. |

Run the tests (hand-run; labs are outside production CI by design):

```bash
pytest labs/omi-receiver/tests -q -x -p no:cacheprovider          # 41 pass + 1 skip (real decode) without opuslib
PYTHONPATH=<dir with opuslib> pytest labs/omi-receiver/tests -q -x -p no:cacheprovider   # 42 pass
```

## The wire format this bridge relies on (verified in firmware, not from the plan)

`BasedHardware/omi` → `omi/firmware/omi/src/lib/core/transport.c`, `push_to_gatt()`:

```c
uint32_t id = packet_next_index++;      // static uint16_t packet_next_index → wraps at 65536
pusher_temp_data[0] = id & 0xFF;        // u16 packet id, little-endian
pusher_temp_data[1] = (id >> 8) & 0xFF;
pusher_temp_data[2] = index;            // u8 fragment index inside ONE codec frame
memcpy(pusher_temp_data + NET_BUFFER_HEADER_SIZE, buffer + offset, packet_size);
```

Two things the plan's one-liner ("`u16` packet number + `u8` fragment index") does
not say and the bridge depends on:

- **The id advances per notification, not per frame.** A frame larger than
  `current_mtu - 3` is split, and each fragment gets the next id with fragment
  index 0, 1, 2… There is **no end-of-frame marker**: a frame is complete when the
  next fragment-0 arrives (or the stream ends). At the MTU BlueZ normally
  negotiates (247) a 20 ms Opus frame (≤ `CODEC_OUTPUT_MAX_BYTES` = 160 bytes,
  ~80 typical at 32 kbps) is one fragment, so packet gaps = frame gaps.
- **Codec id.** `omi/firmware/omi/src/lib/core/config.h` on `main` says
  `#define CODEC_ID 21`, `OPUS_APPLICATION_RESTRICTED_LOWDELAY`, 32 kbps VBR,
  complexity 3, `CODEC_PACKAGE_SAMPLES 160*2` (20 ms). `sdks/device/PROTOCOL.md`:
  `20` = Opus 10 ms frames (DevKit), `21` = Opus FS320 20 ms (CV1); same 16 kHz
  PCM contract. The plan's table says `20`/VOIP — that is the DevKit/older
  firmware; **the CV1 is expected to answer `21`**. The bridge accepts 0, 20 and 21
  and logs what the pendant actually reports (first thing to check on the run).

UUIDs: `sdks/device/dart/lib/uuids.dart` / `sdks/python/omi/constants.py`
(audio service `19b10000-…`, data `19b10001-…`, codec `19b10002-…`, button
`23ba7925-…`, storage `30295780-…`, battery `0x2A19`, DIS `0x2A24/26/27`). Header
strip length matches `sdks/python/omi/decoder.py` (`PACKET_HEADER_BYTES = 3`,
decode buffer 960 samples). Disconnect handling: the SDK never observes drops
(BasedHardware/omi #13290) — `BleakTransport` registers bleak's
`disconnected_callback` itself and `Bridge` reconnects with 1→30 s backoff.

## Manual test protocol (Jason)

### 0. Box + install

Laptop (Linux/BlueZ) or the Pi panel (`ssh zoe-pi`). **Not the Orin.** On the Pi
the daemon's Python is fine; use `--user` so nothing lands in a service venv.

```bash
sudo apt install libopus0 bluez
python3 -m pip install --user -r labs/omi-receiver/requirements.txt
# the user must be able to use BlueZ: `bluetoothctl show` must print an adapter
```

### 1. Un-pair the phone app (one bonded central only)

The pendant streams to **one** central. In the Omi phone app: device → *Unpair /
Forget device*, then in the phone's Bluetooth settings *Forget* "Omi", then
**force-quit the app** (it reconnects in the background). Charge the pendant and
note the battery % the app showed last.

### 2. Pairing / advertising

The CV1 advertises as `Omi` whenever it is powered and not connected — there is no
separate pairing mode we know of (unknown #1 below). Check it is visible:

```bash
bluetoothctl scan on      # expect "Omi" with its address; Ctrl-C
```

If it never appears: power-cycle the pendant (button hold) and re-check the phone
really let go of it.

### 3. Capture runs (the gap gate)

Each run: ≥ 10 min, `--reconnect`, its own `--out`. The summary is printed at the
end and written to `<out>/summary.json`. Exit code 0 = gap gate PASS, 1 = FAIL.

```bash
cd ~/assistant   # any checkout of this branch
# a) 3 m line of sight, pendant worn, normal talking
python3 labs/omi-receiver/omi_bridge.py --device-name Omi --out ~/omi-capture/3m   --max-minutes 10 --reconnect
# b) receiver on the other side of one wall, same distance-ish
python3 labs/omi-receiver/omi_bridge.py --device-name Omi --out ~/omi-capture/wall --max-minutes 10 --reconnect
```

`--address AA:BB:…` instead of `--device-name` if two Omis are around;
`--adapter hci1` for a USB dongle; `-v` for per-packet gap logging. Ctrl-C ends a
run cleanly (summary still written, `"interrupted": true`; a second Ctrl-C while it
is finishing aborts without one). If the pendant answers a codec other than
`--codec` the log says so and the decoder is rebuilt for it (`summary.json`
`codec_id` is what it reported). Play a WAV back to confirm it is speech:
`aplay ~/omi-capture/3m/omi_*_000.wav`.

### 4. The 20 corpus sentences (the WER gate)

The gate compares Moonshine on the pendant against Moonshine on the panel mic for
the **same 20 sentences**. The reference is the 20 newest corpus captures (what the
replay gate itself uses — `replay_samples.py --last 20`).

On the **Orin**, get the sentences and the panel-mic transcripts (this is the
documented replay path, `docs/knowledge/voice-pipeline.md`; `--stt remote` uses
the live Moonshine instead of loading a second one; always under the harness lock):

```bash
cd ~/assistant/services/zoe-data
flock /tmp/zoe-voice-harness.lock python3 tests/replay_samples.py --last 20 --stt remote --json /tmp/panel20.json
# the JSON is {"counts", "stt_mode", "rows": [{"file", "transcript", ...}]} in file (mtime) order:
python3 -c "import json; [print(r['file'], '|', r['transcript']) for r in json.load(open('/tmp/panel20.json'))['rows']]"
```

Listen to those 20 WAVs (`aplay ~/.zoe-voice-samples/<name>.wav`) and write what
was **actually said**, one per line, **in that same row order**, to `sentences.txt`
— that is the reference. `wer.py` reads the panel transcripts straight from
`/tmp/panel20.json` (row order), so no `panel.txt` is needed.

Then, wearing the pendant, run one capture and read the 20 sentences in order
with a ~1.5 s pause between them:

```bash
python3 labs/omi-receiver/omi_bridge.py --device-name Omi --out ~/omi-capture/read20 --roll-minutes 60 --reconnect
python3 labs/omi-receiver/split_on_silence.py ~/omi-capture/read20/omi_*_000.wav --out ~/omi-capture/read20/segments
#   → expect exactly 20 segments; tune --threshold / --min-silence and re-run if not
#     (a re-run into the same --out replaces the previous segments; wer.py refuses ≠ 20 anyway)
```

Copy the segments to the Orin and transcribe them through the same replay path.
`replay_samples.py` reads only the **top-level** `*.wav` of the sample dir (never
subdirectories, on purpose), so the copy must *replace* the directory's contents:
a repeated `scp -r segments /tmp/omi-read20` would nest the new files under
`/tmp/omi-read20/segments/` and leave the previous 20 in place to be scored again.

```bash
rsync -a --delete ~/omi-capture/read20/segments/ zoe@<orin>:/tmp/omi-read20/   # trailing slashes: contents, stale files removed
#   (no rsync: ssh zoe@<orin> rm -rf /tmp/omi-read20 && scp -r ~/omi-capture/read20/segments zoe@<orin>:/tmp/omi-read20)
cd ~/assistant/services/zoe-data
ZOE_VOICE_SAMPLE_DIR=/tmp/omi-read20 flock /tmp/zoe-voice-harness.lock python3 tests/replay_samples.py --stt remote --json /tmp/pendant20.json
python3 ~/assistant/labs/omi-receiver/wer.py --ref sentences.txt --hyp /tmp/panel20.json     # panel-mic WER
python3 ~/assistant/labs/omi-receiver/wer.py --ref sentences.txt --hyp /tmp/pendant20.json   # pendant WER
#   exit 2 = the transcript count is not 20 (re-split), or a row has an stt_error (Moonshine failed
#            on that file — re-run the replay); a failed transcription is never scored as 100 % WER
```

(`replay_samples.py` also routes each transcript through the fast tiers with
writes off — harmless, but it is why the harness lock and `--stt remote` matter.)

### 5. Battery

`summary.json` carries `battery_start`, `battery_end` and `battery_drop_per_hour`
(from the 0x2A19 read at connect and at the end, plus notifies if the firmware
sends them). For a clean number do one **60-min** worn-and-streaming run:

```bash
python3 labs/omi-receiver/omi_bridge.py --device-name Omi --out ~/omi-capture/1h --max-minutes 60 --reconnect
```

If `battery_start` is `null` the characteristic was not readable on this firmware
— record that; it is a finding (unknown #3).

## What to record (paste into the PR / the plan's §6.1)

| measurement | where it comes from | run |
|---|---|---|
| `model` / `firmware` / `hardware` / `codec_id` | summary, first lines | any |
| `storage_service_seen` | summary (GATT discovery of `30295780-…`) | any — expected **true** on stock CV1 (B9.0) |
| packets received / lost / `gap_pct`, `gap_events`, `largest_gap` | summary | 3 m, one wall |
| `reconnects`, `connect_failures`, `connected_s` vs `duration_s` | summary | 3 m, one wall |
| `frames_dropped`, `truncated`, `reordered`, `wraps`, `resyncs` | summary — anything but 0 in `truncated`/`reordered` is a finding about the firmware, not the link | any |
| WAV plays as speech | `aplay` | 3 m |
| panel WER vs pendant WER on the 20 sentences | `wer.py` | read20 |
| battery % start/end, drop per hour | summary | 1 h |
| button events (1 tap, 2 double, 3 long, 4 down, 5 up) | summary `button_events` | any — tap the button once during a run |

## Gate (P0, from the plan §6 — measured, reproducible)

- ≥ 10 min continuous at 3 m **and** through one wall;
- packet-number gaps **< 1 %** on each (the summary's `gap_gate_pass`, exit code 0);
- decoded WAV plays;
- **Moonshine WER on the 20 replay-corpus sentences read while wearing the pendant ≤ panel-mic WER + 5 pts**;
- battery drop per hour recorded.

Pass → B9.2 (bridge thread in the Pi daemon, flag-dark). Fail on gaps → try the
Pi's placement / a USB adapter before concluding; fail on WER → record the
transcripts side by side, that is the input to the B9.2 segmenter settings.

## Unknowns — questions for Jason (answers go in the plan's §8)

1. **Pairing mode.** Does the CV1 advertise as soon as the app lets go, or does it
   need a power-cycle / button hold to re-advertise? (Bridge assumes: advertises when
   not connected.)
2. **Device model string.** What does DIS `0x2A24` actually return — `Omi CV 1` as
   the plan says, or something else? The summary prints it.
3. **Battery readable?** Is `0x2A19` readable/notifying on the live firmware
   (plan: notify since fw 1.5)? `battery_start: null` means no.
4. **Codec id reported.** `21` expected (CV1 FS320); if the pendant answers `20` the
   firmware is older than `main` and the frame is 10 ms.
5. **Can CV1 offline storage be disabled without a firmware build?** The stock
   pendant records to its SD ring whenever powered (B9.0). `storage_service_seen`
   only tells us the service is there; whether a stock setting turns it off is
   unknown — if not, B9.0's `omi-zoe` firmware variant is mandatory before P3.
6. **Is the phone app still bonded?** If reconnects are frequent with
   `connect_failures` > 0, the phone is probably stealing the link — check step 1.

## What remains hardware-only

Everything in the gate table. The code path from notification bytes to WAV is
fixture-tested including a real Opus round trip; the BLE layer
(`BleakTransport`: scan → connect → `disconnected_callback` → notify → GATT reads)
is written against bleak 0.22.3's signatures but has **not** been exercised
against a pendant, nor has the CV1's actual MTU, codec answer, DIS strings,
battery readability or button notifications been observed.
