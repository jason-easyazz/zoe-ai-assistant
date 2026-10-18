# Comms Matrix lab - deep dive 2026-10-18 section 8, gates 1 and 2

A hermetic lab for the household-comms decision (Matrix hybrid vs Plan B, owner decision 2 in section 8.5). It runs a Matrix
homeserver, the MatrixRTC focus (LiveKit + lk-jwt-service) and a synthetic call on a private Docker network, measures what the
gates ask, and tears everything down. Nothing here touches the411, the live panel or zoe-data. README is the record, not a
contract.

## What the gates are (deep dive 8.3)

1. **Gate 1 - the411's free RAM.** 4 GB or more: Synapse from day one; under 2 GB: Tuwunel for messages and Plan B for calls
   until MSC4140 (delayed events) lands in Tuwunel. The lab cannot read the411 (owner step); it measures the other side of the
   comparison: what each stack actually needs.
2. **Gate 2 - a one-week ringing cell** on the family's real phones, app killed, screen locked. A lab cannot run it. The lab
   runs the server half of call setup instead (focus discovery, the OpenID -> LiveKit token exchange, a two-party audio call
   through the SFU, the delayed-event clean-up a dead caller needs) and records what the owner's 2026-10-18 "Zoe's own iPhone
   app" decision changes about the ring.

## Run

```
labs/comms-matrix/run.sh --report /tmp/claude-1000/comms/matrix-lab.json          # both servers, one at a time
labs/comms-matrix/run.sh --servers synapse --report ...                            # one
```

`run.sh` refuses while `night_busy.sh test` says pause, waits for `MemAvailable >= 2000 MB` before taking
`flock /tmp/zoe-test-lane.lock`, and runs niced. `run_lab.py` re-checks the memory gate before each server, mints throwaway
secrets per run (never printed), renders configs into a 0700 temp dir, always runs `compose down -v`, removes the temp dir and
checks that no labelled container, network or volume is left.

Network `internal: true` (no route out). Caps: homeserver 768 MB (Tuwunel) / 1536 MB (Synapse) with 2 CPUs each (their caches
scale with the core count; a small droplet has 1-2), Postgres 256 MB, LiveKit 256 MB, lk-jwt 64 MB, nginx 64 MB. RAM is
`memory.current` of each container's cgroup, sampled every 0.5 s.

Pinned images (digests in the JSON report): `ghcr.io/matrix-construct/tuwunel:v1.9.4`, `matrixdotorg/synapse:v1.163.0`,
`postgres:17.6-alpine`, `livekit/livekit-server:v1.13.10`, `ghcr.io/element-hq/lk-jwt-service:0.9.0`,
`livekit/livekit-cli:v2.19.0`, nginx by digest.

Cells per server: C0 boot + idle RAM, C1 six synthetic accounts (four members, a bot, a panel), C2 federation endpoints with
federation OFF (must be 403 or 404), C3 a household room + three DMs, 300 messages, an initial `/sync` per account, C4 MSC4140
(schedule a call membership event 3 s out, restart it once, require it absent first, the restart 200, present after), C5 MSC4143
(a `livekit` focus pointing at the lab's lk-jwt), C6 each member's OpenID token -> lk-jwt `/sfu/get` -> a LiveKit JWT whose
HS256 signature verifies with the lab's secret, whose subject is that member, which grants join / publish / subscribe, and both
members in the SAME LiveKit room, C7 `lk load-test` with one audio publisher and one subscriber in THAT room for 20 s (the
subscriber must receive the track).

The run exits non-zero unless every required cell passes on every server and the teardown check finds nothing left
(`verdict` per server, `teardown_ok`, `pass` in the report). MSC4140 is reported, not required: a server without it is a finding.

What C7 does NOT prove: `lk load-test` signs its own participant tokens with the API key (it takes no token), so it proves the
SFU path in the Matrix-derived room, not a client joining WITH the Matrix JWT. That needs a WebRTC client (the LiveKit Python
SDK's livekit-ffi cannot open a PeerConnection on Tegra, deep dive 8.1); C6 checks the JWT's signature and grants instead.

## Results (2026-10-19, Jetson Orin NX, arm64)

Final run: `results/2026-10-19.json` (`pass: true`, `teardown_ok: true`). Two earlier runs the same morning used weaker cells
(see the PR's review thread); their RAM figures are folded into the ranges below. The final report's `verdict` blocks were
recomputed from its stored C7 summary after the `media_received` check was added (the summary was already in the report).

| | Tuwunel 1.9.4 | Synapse 1.163.0 + Postgres 17.6 |
|---|---|---|
| boot to serving | 1.0 s | 9-10 s |
| homeserver RAM idle / after load (across runs) | 96-147 / 106-230 MB | 166-196 / 157-210 MB, + Postgres 56-101 / 96-117 MB |
| sum of per-container peaks during C7 (not simultaneous; excludes the load generator) | about 180-320 MB | about 355-375 MB |
| account creation | 45-68 ms | 385-431 ms |
| send p50 / p95 | 2.4-2.6 / 3.3-4.7 ms | 53-55 / 71-80 ms |
| initial sync (6 accounts) | 3-13 ms | 43-100 ms |
| federation OFF | all federation endpoints 403 | all 404 (no federation listener) |
| **MSC4140 delayed events** | **NOT supported**: the delay parameter is ignored and the event is sent at once (200 with an `event_id`, no `delay_id`) | supported: scheduled, absent before, restart 200, present after |
| MSC4143 focus | advertised (well-known + `rtc/transports`) | advertised (well-known) |
| OpenID -> signed LiveKit JWT, federation off | 200 in 5-41 ms, member's identity, both in one room | 200 in 13-84 ms, same |
| 1 publisher + 1 subscriber in that room, 20 s | 1,000 packets received, 0 % loss; LiveKit peak 55-66 MB | same; LiveKit peak 51-54 MB |
| TURN | not measurable on an internal network | same |

Limits of these numbers: six accounts and 300 messages, no E2EE, no media uploads, no federation, a fresh database, one short
call. Tuwunel's RocksDB footprint moved between runs (147 vs 96 MB idle). Real use grows caches and room state.

## What this says about the gates

- **Gate 1 is about the411's number.** Measured at household scale: about 0.2-0.35 GB for the Tuwunel stack, 0.35-0.4 GB for
  the Synapse stack, well under the deep dive's 0.7-2.3 GB estimates. With 2-3x headroom for growth, E2EE and media that is
  roughly 0.6-1 GB (Tuwunel) or 0.8-1.1 GB (Synapse) of free RAM on the411 beyond the OS and the sites already there. The lab
  does not show the deep dive's thresholds are wrong, only that a small household sits well inside them; `free -m` on the411
  (owner step) decides.
- **Tuwunel cannot carry Element Call / MatrixRTC calls yet.** Without MSC4140 a caller who dies (phone killed, network lost)
  leaves a ghost call membership, and a client asking for a delayed "leave" gets it applied immediately. Calls over Matrix
  mean Synapse.
- **Gate 2 changed shape with the own-app decision.** The ring a Matrix call gives an iPhone comes through a Notification
  Service Extension that needs Apple's managed `com.apple.developer.usernotifications.filtering` entitlement (granted at Apple's
  discretion, no stated turnaround; lane M of deep dive round 2). A Plan B call is a direct PushKit VoIP push that needs no
  approval. The week-long ring test should be run on whichever path the app uses (M2), not on Element X.
- **The server half of call setup works with federation fully off** on both servers, so a private homeserver does not block
  calls. TURN must run on the411 (a public address) for phones off the LAN; not measured here.

## Recommendation: Plan B for calls and the phone ring; Matrix only when households talk to each other

1. **Calls (P2) and the iPhone ring: Plan B** - native signalling from the box, LiveKit (Swift SDK on the phone) with TURN on the411,
   a direct PushKit VoIP push. No Apple managed entitlement, no homeserver on the call path, and it fits Zoe's own app (lane M
   M2, about 1,200-1,800 Swift lines).
2. **Messages inside one household: no Matrix needed.** Every member is on the same box; box-mediated messages to the app
   (APNs) and panels (the intercom lane) cover "leave a message for Sam" with nothing on the411.
3. **Keep Matrix for household-to-household (P3).** If and when that is built: Tuwunel for messages is enough; choose Synapse only
   if Matrix calls are wanted, because of MSC4140. Embedding matrix-rust-sdk in Zoe's own app costs about 4-6k Swift lines
   (lane M), so it should wait until there is a second household to talk to.

This leans away from the owner's "leaning Matrix" because the own-app decision moved the hard part (the ring) to Apple's rules,
and Plan B is the path Apple does not gate. The RAM data does not decide it either way. The owner decides.

## Owner steps

- Read `free -m` (and the droplet plan / transfer) on the411; nothing in this lab was deployed there.
- Decide Matrix vs Plan B with the table above.
